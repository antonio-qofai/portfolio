// Real-world data: parsing saved SerpApi responses, the quota guard, travel
// time, weather tags, and how the planner and agent use them. No network.

import fs from "fs";
import { describe, expect, test } from "vitest";
import { DESTINATIONS, planTrip, type Traveler } from "../planner";
import { MAX_PRICE_CHECKS, runAgent } from "../server/agent";
import { emptyConstraints } from "../server/claude/constraints";
import { distanceKm, estimateFlightHours } from "../server/data/geo";
import { checkPrices, DAILY_SPEND_USD, parseFlights, parseHotels, SearchQuota, type Fetcher } from "../server/data/prices";
import { weatherTags } from "../server/data/weather";
import { MemoryStore } from "../server/store";
import { sampleTravelers, sampleTripStart } from "../server/sample-trip";

const fixture = (name: string) => JSON.parse(fs.readFileSync(`tests/fixtures/${name}`, "utf8"));
const byId = (id: string) => DESTINATIONS.find(d => d.id === id)!;
const travelers = sampleTravelers(sampleTripStart("2026-10-01"));
const window = { start: "2026-11-13", days: 4 };

/** Answers SerpApi requests with a fixed flight price per arrival airport and a fixed hotel rate, and records them. */
function fakeSerpApi(flightPrice: (arrival: string) => number, nightly = 100) {
  const calls: Record<string, string>[] = [];
  const fetcher: Fetcher = async params => {
    calls.push(params);
    if (params.engine === "google_hotels") {
      return { properties: [{ name: "Test Inn", rate_per_night: { extracted_lowest: nightly }, overall_rating: 4.2 }], search_metadata: { google_hotels_url: "https://hotels" } };
    }
    return {
      best_flights: [{ price: flightPrice(params.arrival_id), total_duration: 150, flights: [{}] }],
      search_metadata: { google_flights_url: `https://flights/${params.departure_id}-${params.arrival_id}` },
    };
  };
  return { fetcher, calls };
}

describe("SerpApi parsing (saved real responses)", () => {
  test("cheapest flight, its duration, stops, and the Google Flights link", () => {
    const f = parseFlights(fixture("serpapi-flights-ORD-CHS.json"))!;
    expect(f).toMatchObject({ price: 308, stops: 0 });
    expect(f.minutes).toBeGreaterThan(100);
    expect(f.url).toMatch(/^https:\/\/www\.google\.com\/travel\/flights/);
  });

  test("cheapest well-rated hotel and its nightly rate", () => {
    expect(parseHotels(fixture("serpapi-hotels-charleston.json"))).toMatchObject({ name: "Best Western Charleston Inn", nightly_usd: 118 });
  });

  test("empty results parse to null instead of throwing", () => {
    expect(parseFlights({})).toBeNull();
    expect(parseHotels({ properties: [] })).toBeNull();
  });
});

describe("price checks", () => {
  test("one search per home airport plus one hotel, with per-person totals", async () => {
    const { fetcher, calls } = fakeSerpApi(() => 300);
    const quota = new SearchQuota(new MemoryStore(), 200, "2026-10");
    const store = new MemoryStore();
    const r = (await checkPrices(store, fetcher, quota, byId("charleston"), travelers, [], window))!;

    // JFK, SEA, ORD (shared by Ariana and Omar), DFW, LAX, and the hotel.
    expect(calls).toHaveLength(6);
    const maya = r.costs.per_traveler.find(c => c.name === "Maya")!;
    // $300 flight + $100 x 3 nights / 2 per room + 4 days of daily spend.
    expect(maya.total_usd).toBe(300 + 150 + 4 * DAILY_SPEND_USD);
    expect(maya).toMatchObject({ source: "live", booking_url: "https://flights/JFK-CHS" });
  });

  test("someone who needs nonstop gets a separate nonstop-only search", async () => {
    const { fetcher, calls } = fakeSerpApi(() => 300);
    const constraints = travelers.map(t => ({ ...emptyConstraints(t.name), nonstop_only: t.name === "Ariana" }));
    await checkPrices(new MemoryStore(), fetcher, new SearchQuota(new MemoryStore(), 200, "2026-10"), byId("charleston"), travelers, constraints, window);
    const ord = calls.filter(c => c.departure_id === "ORD");
    expect(ord.map(c => c.stops).sort()).toEqual(["0", "1"]);
  });

  test("cached prices cost no quota, and a destination is skipped entirely if the quota can't cover it", async () => {
    const store = new MemoryStore();
    const quota = new SearchQuota(store, 200, "2026-10");
    const first = fakeSerpApi(() => 300);
    await checkPrices(store, first.fetcher, quota, byId("charleston"), travelers, [], window);
    expect(await quota.remaining()).toBe(194);

    const second = fakeSerpApi(() => 300);
    const again = await checkPrices(store, second.fetcher, quota, byId("charleston"), travelers, [], window);
    expect(second.calls).toHaveLength(0);
    expect(again!.cached).toBe(6);

    const tight = new SearchQuota(new MemoryStore(), 3, "2026-10");
    const third = fakeSerpApi(() => 300);
    expect(await checkPrices(new MemoryStore(), third.fetcher, tight, byId("savannah"), travelers, [], window)).toBeNull();
    expect(third.calls).toHaveLength(0);
  });
});

describe("travel time and weather", () => {
  test("Chicago to Charleston is about 1,200 km and under three hours", () => {
    const km = distanceKm({ latitude: 41.85, longitude: -87.65 }, byId("charleston"));
    expect(km).toBeGreaterThan(1150);
    expect(km).toBeLessThan(1300);
    expect(estimateFlightHours(km)).toBeLessThan(3);
    expect(estimateFlightHours(100)).toBe(0);
  });

  test("weather becomes climate tags the scorer understands", () => {
    expect(weatherTags({ high_c: 30, precipitation_mm: 0.2 })).toEqual(["hot", "warm", "dry"]);
    expect(weatherTags({ high_c: 20, precipitation_mm: 4 })).toEqual(["mild"]);
    expect(weatherTags({ high_c: 5, precipitation_mm: 2 })).toEqual(["cool", "cold"]);
  });
});

describe("planner with real data", () => {
  const two: Traveler[] = travelers.slice(0, 2);

  test("real weather overrides the static climate tags", () => {
    const plan = planTrip(two, DESTINATIONS);
    const id = plan.winner!.destination.id;
    const freezing = planTrip(two, DESTINATIONS, { enrichment: { [id]: { weather_tags: ["cold"] } } });
    expect(freezing.shortlist.find(s => s.destination.id === id)?.score.components.climate ?? 0).toBeLessThan(plan.winner!.score.components.climate);
  });

  test("a flight longer than someone's limit rules the destination out", () => {
    const limited = two.map(t => ({ ...t, max_travel_hours: 4 }));
    const id = planTrip(limited, DESTINATIONS).winner!.destination.id;
    const plan = planTrip(limited, DESTINATIONS, { enrichment: { [id]: { travel_hours: { Maya: 6, Dev: 2 } } } });
    expect(plan.winner!.destination.id).not.toBe(id);
    expect(plan.limiting_factors.join(" ")).toMatch(/Flight-time limits/);
  });

  test("live prices are checked per person against their own budget", () => {
    const id = planTrip(two, DESTINATIONS).winner!.destination.id;
    const costs = (maya: number) => ({
      checked_at: "", hotel: null,
      per_traveler: [
        { name: "Maya", origin: "JFK", flight_usd: 1, flight_minutes: 1, stops: 0, nonstop_required: false, lodging_usd: 0, daily_usd: 0, total_usd: maya, source: "live" as const, booking_url: "" },
        { name: "Dev", origin: "SEA", flight_usd: 1, flight_minutes: 1, stops: 0, nonstop_required: false, lodging_usd: 0, daily_usd: 0, total_usd: 500, source: "live" as const, booking_url: "" },
      ],
    });
    // Maya's limit is $1,870: $1,800 fits, $1,900 does not, whatever the static estimate says.
    expect(planTrip(two, DESTINATIONS, { enrichment: { [id]: { costs: costs(1800) } } }).winner!.destination.id).toBe(id);
    expect(planTrip(two, DESTINATIONS, { enrichment: { [id]: { costs: costs(1900) } } }).winner!.destination.id).not.toBe(id);
  });
});

describe("agent price verification", () => {
  const deps = (fetcher: Fetcher, cap = 200) => ({ store: new MemoryStore(), llm: null, publicData: false, prices: { fetcher, monthlyCap: cap }, today: () => "2026-10-01" });

  test("prices that fit keep the winner and show live per-person costs", async () => {
    const { fetcher } = fakeSerpApi(() => 300);
    const plan = await runAgent(travelers, deps(fetcher));
    expect(plan.agent!.data).toMatchObject({ prices: "serpapi", price_searches: 6, destinations_price_checked: ["charleston"] });
    expect(plan.winner!.costs!.per_traveler.every(c => c.source === "live")).toBe(true);
    expect(plan.checks.passed).toBe(true);
  });

  test("if real prices break someone's budget, the next destination is checked", async () => {
    // Charleston flights cost $900, which puts Omar ($1,300 hard limit) over.
    const { fetcher } = fakeSerpApi(arrival => (arrival === "CHS" ? 900 : 200));
    const plan = await runAgent(travelers, deps(fetcher));
    expect(plan.agent!.data.destinations_price_checked).toHaveLength(2);
    expect(plan.agent!.data.destinations_price_checked[0]).toBe("charleston");
    expect(plan.winner!.destination.id).not.toBe("charleston");
    expect(plan.winner!.costs).toBeDefined();
  });

  test(`it stops after ${MAX_PRICE_CHECKS} checks and labels the winner's prices as estimates`, async () => {
    const { fetcher } = fakeSerpApi(() => 5000);
    const plan = await runAgent(travelers, deps(fetcher));
    expect(plan.agent!.data.destinations_price_checked).toHaveLength(MAX_PRICE_CHECKS);
    expect(plan.warnings.join(" ")).toMatch(/are estimates/);
  });

  test("with the monthly quota used up, it plans on estimates without searching", async () => {
    const { fetcher, calls } = fakeSerpApi(() => 300);
    const plan = await runAgent(travelers, deps(fetcher, 2));
    expect(calls).toHaveLength(0);
    expect(plan.agent!.data.prices).toBe("quota_reached");
    expect(plan.status).toBe("ok");
  });

  test("a SerpApi error is recorded and the plan still comes back", async () => {
    const plan = await runAgent(travelers, deps(async () => { throw new Error("SerpApi: Invalid API key"); }));
    expect(plan.agent!.data.prices).toBe("unavailable");
    expect(plan.agent!.data.errors[0]).toMatch(/Invalid API key/);
    expect(plan.status).toBe("ok");
  });
});
