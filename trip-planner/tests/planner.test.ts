import { describe, expect, test } from "vitest";
import { planTrip, parseTraveler } from "../planner";
import { parseFlex, parseMoney } from "../planner/survey";
import { isDomestic, isHomeCity, scoreDestination } from "../planner/scoring";
import type { Destination, Traveler } from "../planner/types";

function dest(id: string, overrides: Partial<Destination> = {}): Destination {
  return {
    id,
    name: id.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase()),
    country_or_region: "USA",
    climate_tags: ["warm"],
    activity_tags: ["food"],
    estimated_cost_per_person_usd: 1000,
    notes: "",
    latitude: 30,
    longitude: -90,
    airport: "XXX",
    ...overrides,
  };
}

function person(name: string, overrides: Partial<Traveler> = {}): Traveler {
  return {
    name,
    departure_city: "Boston",
    availability: [{ start: "2026-06-01", end: "2026-06-10" }],
    budget_usd: 2000,
    budget_flex_pct: 0,
    interests: ["food"],
    passport_ready: true,
    avoid_crowds: false,
    ...overrides,
  };
}

// The five cases from the original UChicago brief.
describe("original brief", () => {
  test("no overlap even at 2 days is an error that says so", () => {
    const plan = planTrip(
      [
        person("A", { availability: [{ start: "2026-06-01", end: "2026-06-03" }] }),
        person("B", { availability: [{ start: "2026-06-03", end: "2026-06-06" }] }),
      ],
      [dest("austin"), dest("denver")],
    );
    expect(plan.status).toBe("error");
    expect(plan.limiting_factors.join(" ")).toMatch(/overlap/i);
    expect(plan.suggested_fixes.length).toBeGreaterThan(0);
    expect(plan.checks.passed).toBe(true);
  });

  test("budget mismatch is an error with budget guidance", () => {
    const plan = planTrip([person("A", { budget_usd: 500 })], [dest("austin"), dest("denver")]);
    expect(plan.status).toBe("error");
    expect(plan.suggested_fixes.join(" ")).toMatch(/budget/i);
  });

  test("constraints too tight to satisfy list what limited them", () => {
    const plan = planTrip(
      [person("A", { passport_ready: false }), person("B", { budget_usd: 800 })],
      [dest("lisbon", { country_or_region: "Portugal", estimated_cost_per_person_usd: 700 }), dest("austin")],
    );
    expect(plan.status).toBe("error");
    expect(plan.limiting_factors.length).toBeGreaterThan(0);
    expect(plan.limiting_factors.join(" ")).toMatch(/passport/);
  });

  test("missing required fields return needs_info per person", () => {
    const plan = planTrip(
      [
        { name: "Maya", departure_city: "Boston", available_dates: "2026-06-01..2026-06-10", budget: "", top_interests: "food" },
        { name: "Dev", departure_city: "", available_dates: "", budget: "1500", top_interests: "hiking" },
      ],
      [dest("austin")],
    );
    expect(plan.status).toBe("needs_info");
    expect(plan.missing_fields).toEqual({ Maya: ["budget"], Dev: ["departure_city", "availability"] });
    expect(plan.checks.passed).toBe(true);
  });

  test("runner-up is returned and distinct when 2+ destinations are feasible", () => {
    const plan = planTrip([person("A")], [dest("austin"), dest("denver"), dest("miami")]);
    expect(plan.status).toBe("ok");
    expect(plan.runner_up?.feasible).toBe(true);
    expect(plan.runner_up?.destination.id).not.toBe(plan.winner?.destination.id);
  });
});

describe("dates", () => {
  test("falls back from 4 to 3 days with a warning, and the end date matches the length", () => {
    const plan = planTrip(
      [
        person("A", { availability: [{ start: "2026-06-01", end: "2026-06-03" }] }),
        person("B", { availability: [{ start: "2026-05-28", end: "2026-06-05" }] }),
      ],
      [dest("austin"), dest("denver")],
    );
    expect(plan.status).toBe("ok");
    expect(plan.trip_window).toMatchObject({ start: "2026-06-01", end: "2026-06-03", days: 3 });
    expect(plan.warnings).toContain("No 4-day overlap; using 3-day overlap instead.");
    expect(plan.itinerary).toHaveLength(3);
  });

  test("end date is start + length - 1 even when the shared run is longer", () => {
    // The original returned the end of the whole shared run (June 10) for a 4-day trip.
    const plan = planTrip([person("A")], [dest("austin"), dest("denver")]);
    expect(plan.trip_window).toMatchObject({ start: "2026-06-01", end: "2026-06-04", days: 4 });
  });

  test("names the person blocking the schedule", () => {
    const plan = planTrip(
      [person("A"), person("B"), person("Late", { availability: [{ start: "2026-07-01", end: "2026-07-05" }] })],
      [dest("austin")],
    );
    expect(plan.status).toBe("error");
    expect(plan.suggested_fixes[0]).toMatch(/Without Late/);
  });

  test("parses M/D/YYYY start and end dates", () => {
    const r = parseTraveler({ name: "A", departure_city: "Boston", start_date: "6/11/2026", end_date: "6/28/2026", budget: "1000", top_interests: "food" }, 0);
    expect(r.ok && r.traveler.availability).toEqual([{ start: "2026-06-11", end: "2026-06-28" }]);
  });
});

describe("budget", () => {
  test("parses dollar signs and commas", () => {
    expect(parseMoney("$1,500")).toBe(1500);
    expect(parseMoney("1500")).toBe(1500);
    expect(parseMoney("abc")).toBeNaN();
  });

  test("an explicit 0% flex stays 0, and blank flex uses the 20% default", () => {
    // The original treated 0 as missing and silently gave 10%.
    expect(parseFlex("0")).toBe(0);
    expect(parseFlex("")).toBe(0.2);
    expect(parseFlex("10%")).toBe(0.1);
    expect(parseFlex("15")).toBe(0.15);
  });

  test("zero flex excludes a destination $1 over budget", () => {
    const plan = planTrip([person("A", { budget_usd: 999 })], [dest("austin"), dest("denver", { estimated_cost_per_person_usd: 900 })]);
    expect(plan.winner?.destination.id).toBe("denver");
  });

  test("budget state is tight when the winner needs flex", () => {
    const plan = planTrip([person("A", { budget_usd: 950, budget_flex_pct: 0.1 })], [dest("austin"), dest("denver")]);
    expect(plan.budget?.state).toBe("tight");
  });

  test("cheapness does not outweigh what the group wants", () => {
    // The original gave 1 point per $100 saved and 2 per interest, so cheap won.
    const plan = planTrip(
      [person("A", { interests: ["hiking", "nature", "scenic_views"] })],
      [
        dest("cheap_city", { activity_tags: ["shopping"], estimated_cost_per_person_usd: 400 }),
        dest("mountains", { activity_tags: ["hiking", "nature", "scenic_views"], estimated_cost_per_person_usd: 1800 }),
      ],
    );
    expect(plan.winner?.destination.id).toBe("mountains");
  });
});

describe("filters", () => {
  test("excludes home cities by whole words", () => {
    expect(isHomeCity(dest("new_york_city", { name: "New York City" }), "New York")).toBe(true);
    expect(isHomeCity(dest("atlanta"), "LA")).toBe(false);
  });

  test("Puerto Rico counts as domestic for travelers without passports", () => {
    expect(isDomestic(dest("san_juan", { country_or_region: "Puerto Rico (USA)" }))).toBe(true);
  });

  test("a 'domestic only' note restricts to US destinations", () => {
    const plan = planTrip(
      [person("A", { must_have: "domestic_only" })],
      [dest("lisbon", { country_or_region: "Portugal", activity_tags: ["food", "culture"] }), dest("austin"), dest("denver")],
    );
    expect(plan.winner?.destination.country_or_region).toBe("USA");
    expect(plan.excluded_counts.domestic_requested).toBe(1);
  });

  test("with only one feasible destination the runner-up is the closest alternative, marked infeasible", () => {
    const plan = planTrip([person("A", { budget_usd: 1000 })], [dest("austin"), dest("denver", { estimated_cost_per_person_usd: 1200 })]);
    expect(plan.status).toBe("ok");
    expect(plan.runner_up).toMatchObject({ feasible: false, destination: { id: "denver" } });
    expect(plan.runner_up?.reason).toMatch(/budget/);
  });
});

describe("scoring", () => {
  const crowdAvoiders = [person("A", { avoid_crowds: true }), person("B", { avoid_crowds: true })];

  test("crowd penalty applies to Miami Beach and New York City", () => {
    // The original checked for ids "miami" and "new_york", which never matched.
    for (const id of ["miami_beach", "new_york_city"]) {
      expect(scoreDestination(dest(id), crowdAvoiders, 2000).components.crowds).toBe(-1);
    }
    expect(scoreDestination(dest("austin"), crowdAvoiders, 2000).components.crowds).toBe(0);
  });

  test("survey interest words match destination tags through synonyms", () => {
    const s = scoreDestination(dest("nashville", { activity_tags: ["music"] }), [person("A", { interests: ["live_music"] })], 2000);
    expect(s.components.interests).toBe(1);
  });

  test("fairness is the least-served person's coverage", () => {
    const s = scoreDestination(dest("austin"), [person("A"), person("B", { interests: ["skiing"] })], 2000);
    expect(s.components.fairness).toBe(0);
    expect(s.components.interests).toBe(0.5);
  });
});

describe("itinerary", () => {
  test("is deterministic and has no extra day", () => {
    const run = () => planTrip([person("A", { interests: ["food", "museums"] })], [dest("austin", { activity_tags: ["food", "museums"] }), dest("denver")]);
    const a = run(), b = run();
    expect(a.itinerary).toEqual(b.itinerary);
    expect(a.itinerary).toHaveLength(4);
    expect(a.itinerary.map(d => d.date)).toEqual(["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04"]);
  });
});
