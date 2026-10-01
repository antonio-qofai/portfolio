// Live flight and hotel prices from SerpApi's Google Flights and Google Hotels
// engines, on the free plan (250 searches a month). Every search is cached for
// a few days and counted against a monthly cap, and a destination is only
// checked if the quota covers all of its searches, so a plan never mixes live
// and missing prices for the same place.

import { addDays } from "../../planner/dates";
import type { Destination, LiveCosts, Traveler, TravelerCost, TravelerConstraints } from "../../planner";
import type { Store } from "../store";

const DAY = 86_400;
const PRICE_TTL = 3 * DAY;
/** Food, local transport, and activities per person per day. An assumption, shown in the UI. */
export const DAILY_SPEND_USD = 80;
/** People per hotel room. */
const ROOM_SHARE = 2;

export interface Flight {
  price: number;
  minutes: number;
  stops: number;
  url: string;
}

export interface Hotel {
  name: string;
  nightly_usd: number;
  rating: number | null;
  url: string;
}

/** The monthly search budget, shared across all plans. */
export class SearchQuota {
  used = 0;
  constructor(private store: Store, private cap: number, private month: string) {}

  private key() {
    return `serpapi:count:${this.month}`;
  }
  async remaining(): Promise<number> {
    return this.cap - ((await this.store.getCached<number>(this.key())) ?? 0);
  }
  async spend(n = 1) {
    const count = ((await this.store.getCached<number>(this.key())) ?? 0) + n;
    await this.store.setCached(this.key(), count, 40 * DAY);
    this.used += n;
  }
}

export type Fetcher = (params: Record<string, string>) => Promise<unknown>;

export function serpApiFetcher(apiKey: string): Fetcher {
  return async params => {
    const qs = new URLSearchParams({ ...params, hl: "en", gl: "us", currency: "USD", api_key: apiKey });
    const res = await fetch(`https://serpapi.com/search.json?${qs}`, { signal: AbortSignal.timeout(30_000) });
    const body = await res.json();
    if (!res.ok || body.error) throw new Error(`SerpApi: ${body.error ?? res.status}`);
    return body;
  };
}

type FlightOption = { price?: number; total_duration: number; flights: unknown[] };

export function parseFlights(body: unknown): Flight | null {
  const b = body as { best_flights?: FlightOption[]; other_flights?: FlightOption[]; search_metadata?: { google_flights_url?: string } };
  const options = [...(b.best_flights ?? []), ...(b.other_flights ?? [])].filter(o => typeof o.price === "number");
  if (options.length === 0) return null;
  const cheapest = options.reduce((a, o) => (o.price! < a.price! ? o : a));
  return { price: cheapest.price!, minutes: cheapest.total_duration, stops: cheapest.flights.length - 1, url: b.search_metadata?.google_flights_url ?? "" };
}

type Property = { name: string; rate_per_night?: { extracted_lowest?: number }; overall_rating?: number };

export function parseHotels(body: unknown): Hotel | null {
  const b = body as { properties?: Property[]; search_metadata?: { google_hotels_url?: string } };
  const priced = (b.properties ?? []).filter(p => typeof p.rate_per_night?.extracted_lowest === "number");
  if (priced.length === 0) return null;
  const cheapest = priced.reduce((a, p) => (p.rate_per_night!.extracted_lowest! < a.rate_per_night!.extracted_lowest! ? p : a));
  return { name: cheapest.name, nightly_usd: cheapest.rate_per_night!.extracted_lowest!, rating: cheapest.overall_rating ?? null, url: b.search_metadata?.google_hotels_url ?? "" };
}

export function flightsSearchUrl(origin: string, dest: string, out: string, ret: string): string {
  return `https://www.google.com/travel/flights?q=${encodeURIComponent(`Flights from ${origin} to ${dest} on ${out} through ${ret}`)}`;
}

interface Search<T> {
  key: string;
  params: Record<string, string>;
  parse: (body: unknown) => T | null;
}

/**
 * Prices for one destination: the cheapest round trip from each traveler's
 * home airport (nonstop only where someone asked for it) and the cheapest
 * well-rated hotel. Returns null without searching if the quota can't cover it.
 */
export async function checkPrices(
  store: Store,
  fetcher: Fetcher,
  quota: SearchQuota,
  d: Destination,
  travelers: Traveler[],
  constraints: TravelerConstraints[],
  window: { start: string; days: number },
): Promise<{ costs: LiveCosts; searches: number; cached: number } | null> {
  const out = window.start;
  const ret = addDays(window.start, window.days - 1);
  const nonstop = (t: Traveler) => constraints.find(c => c.name === t.name)?.nonstop_only ?? false;

  const flightSearches = new Map<string, Search<Flight>>();
  for (const t of travelers) {
    if (!t.home_airport || t.home_airport === d.airport) continue;
    const ns = nonstop(t);
    const key = `price:flight:${t.home_airport}:${d.airport}:${out}:${ret}:${ns ? "nonstop" : "any"}`;
    flightSearches.set(key, {
      key,
      params: { engine: "google_flights", departure_id: t.home_airport, arrival_id: d.airport, outbound_date: out, return_date: ret, type: "1", adults: "1", stops: ns ? "1" : "0" },
      parse: parseFlights,
    });
  }
  const hotelSearch: Search<Hotel> = {
    key: `price:hotel:${d.id}:${out}:${ret}`,
    params: { engine: "google_hotels", q: `${d.name} hotels`, check_in_date: out, check_out_date: ret, adults: String(ROOM_SHARE), sort_by: "3", rating: "8" },
    parse: parseHotels,
  };

  // Look in the cache first, so only the missing searches count against the quota.
  const all: Search<Flight | Hotel>[] = [...flightSearches.values(), hotelSearch];
  const results = new Map<string, Flight | Hotel | null>();
  for (const s of all) {
    const hit = await store.getCached<{ result: Flight | Hotel | null }>(s.key);
    if (hit) results.set(s.key, hit.result);
  }
  const missing = all.filter(s => !results.has(s.key));
  if (missing.length > (await quota.remaining())) return null;
  // Reserve the whole batch in one write; spending per search in parallel would race and undercount.
  if (missing.length) await quota.spend(missing.length);

  await Promise.all(
    missing.map(async s => {
      const body = await fetcher(s.params);
      const result = s.parse(body);
      results.set(s.key, result);
      await store.setCached(s.key, { result }, PRICE_TTL);
    }),
  );

  const hotel = results.get(hotelSearch.key) as Hotel | null;
  const nights = Math.max(1, window.days - 1);
  const lodging = hotel ? Math.round((hotel.nightly_usd * nights) / ROOM_SHARE) : null;

  const per_traveler: TravelerCost[] = travelers.map(t => {
    const ns = nonstop(t);
    const key = `price:flight:${t.home_airport}:${d.airport}:${out}:${ret}:${ns ? "nonstop" : "any"}`;
    const flight = (t.home_airport && t.home_airport !== d.airport ? results.get(key) : null) as Flight | null | undefined;
    const daily = DAILY_SPEND_USD * window.days;
    // Without a flight price or a hotel price, fall back to the static estimate for this person.
    const live = flight && lodging !== null;
    return {
      name: t.name,
      origin: t.home_airport ?? t.departure_city,
      flight_usd: flight?.price ?? null,
      flight_minutes: flight?.minutes ?? null,
      stops: flight?.stops ?? null,
      nonstop_required: ns,
      lodging_usd: lodging ?? 0,
      daily_usd: daily,
      total_usd: live ? flight.price + lodging + daily : d.estimated_cost_per_person_usd,
      source: live ? "live" : "estimate",
      booking_url: flight?.url || flightsSearchUrl(t.home_airport ?? t.departure_city, d.airport, out, ret),
    };
  });

  return {
    costs: { checked_at: new Date().toISOString(), hotel: hotel ? { ...hotel } : null, per_traveler },
    searches: missing.length,
    cached: all.length - missing.length,
  };
}
