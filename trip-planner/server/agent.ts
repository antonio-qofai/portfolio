// The agent: the deterministic planner with real-world data and two
// language-model steps around it.
//
//   1. Read everyone's notes into structured constraints        (Claude, cached)
//   2. Estimate flight time from every home to every place      (Open-Meteo geocoding)
//   3. Rank destinations                                         (planner)
//   4. Fetch weather for the trip dates at the shortlist, rerank (Open-Meteo)
//   5. Check live flight and hotel prices for the winner; if     (SerpApi, quota-capped)
//      someone can no longer afford it, rerank and check the next
//   6. Write the itinerary, check it, retry with feedback        (Claude, up to 3 tries)
//
// Every external step degrades gracefully: without it the planner falls back
// to static data, keyword rules, or the template itinerary, and says so in the
// trace. A plan always comes back.

import {
  applyConstraints, checkPlan, DESTINATIONS, planTrip,
  type AgentTrace, type Enrichment, type PlanResult, type Traveler, type TravelerConstraints,
} from "../planner";
import { constraintsCacheKey, readConstraints } from "./claude/constraints";
import { writeItinerary } from "./claude/itinerary";
import { addUsage, MODEL, NO_USAGE, type Llm } from "./claude/llm";
import { travelHours } from "./data/geo";
import { checkPrices, SearchQuota, type Fetcher } from "./data/prices";
import { weatherFor, weatherTags } from "./data/weather";
import type { Store } from "./store";

/** How many destinations may be price-checked per plan before settling. */
export const MAX_PRICE_CHECKS = 2;

export interface AgentDeps {
  store: Store;
  llm: Llm | null;
  /** Live prices. Absent means prices are never checked. */
  prices?: { fetcher: Fetcher; monthlyCap: number };
  /** Whether to call free public data APIs (weather, geocoding). Off in unit tests. */
  publicData?: boolean;
  today?: () => string;
}

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

export async function runAgent(travelers: Traveler[], deps: AgentDeps): Promise<PlanResult> {
  const { store, llm } = deps;
  const today = (deps.today ?? (() => new Date().toISOString().slice(0, 10)))();
  const started = Date.now();
  let usage = NO_USAGE;
  const trace: AgentTrace = {
    model: MODEL,
    constraints: { source: "keywords" },
    data: { weather: "skipped", travel_time: "unavailable", prices: "skipped", price_searches: 0, destinations_price_checked: [], errors: [] },
    itinerary: { source: "template", attempts: [] },
    ...NO_USAGE,
    duration_ms: 0,
  };

  // 1. Constraints from notes.
  let constraints: TravelerConstraints[] | undefined;
  if (llm && travelers.length > 0) {
    const key = `constraints:${constraintsCacheKey(travelers)}`;
    const cached = await store.getCached<TravelerConstraints[]>(key);
    if (cached) {
      constraints = cached;
      trace.constraints.source = "cache";
    } else {
      try {
        const r = await readConstraints(llm, travelers);
        if (r) {
          constraints = r.constraints;
          usage = addUsage(usage, r.usage);
          trace.constraints.source = "claude";
        }
      } catch (e) {
        trace.constraints.error = message(e);
      }
      // A failed cache write costs a repeat read next time; it should not fail the plan.
      if (constraints) await store.setCached(key, constraints).catch(e => console.error("Could not cache constraints:", e));
    }
  }

  const enrichment: Record<string, Enrichment> = Object.fromEntries(DESTINATIONS.map(d => [d.id, {}]));
  const replan = () => planTrip(travelers, DESTINATIONS, { constraints, enrichment });

  // 2. Flight time from every home city to every destination.
  if (deps.publicData !== false) {
    try {
      const hours = await travelHours(store, travelers, DESTINATIONS);
      for (const d of DESTINATIONS) enrichment[d.id].travel_hours = hours[d.id];
      trace.data.travel_time = "estimated";
    } catch (e) {
      trace.data.errors.push(`travel time: ${message(e)}`);
    }
  }

  // 3. First ranking.
  let plan = replan();

  // 4. Real weather for the shortlist, then rank again.
  if (plan.status === "ok" && plan.trip_window && deps.publicData !== false) {
    const window = plan.trip_window;
    const shortlist = plan.shortlist;
    const results = await Promise.allSettled(shortlist.map(s => weatherFor(store, s.destination, window.start, window.days, today)));
    results.forEach((r, i) => {
      const id = shortlist[i].destination.id;
      if (r.status === "fulfilled") Object.assign(enrichment[id], { weather: r.value, weather_tags: weatherTags(r.value) });
      else trace.data.errors.push(`weather ${id}: ${message(r.reason)}`);
    });
    trace.data.weather = results.some(r => r.status === "fulfilled") ? "open-meteo" : "unavailable";
    plan = replan();
  }

  // 5. Live prices for the winner. If they push someone over budget, the planner
  // drops it and the next winner gets checked, up to MAX_PRICE_CHECKS.
  if (plan.status === "ok" && plan.trip_window && deps.prices) {
    const quota = new SearchQuota(store, deps.prices.monthlyCap, today.slice(0, 7));
    let fromCache = 0;
    for (let i = 0; i < MAX_PRICE_CHECKS && plan.status === "ok" && plan.winner && plan.trip_window; i++) {
      const d = plan.winner.destination;
      if (enrichment[d.id].costs) break;
      try {
        const r = await checkPrices(store, deps.prices.fetcher, quota, d, travelers, constraints ?? [], plan.trip_window);
        if (!r) {
          trace.data.prices = "quota_reached";
          break;
        }
        enrichment[d.id].costs = r.costs;
        // Real flight times replace the straight-line estimate, so flight-time limits are checked against actual routes.
        const hours = { ...enrichment[d.id].travel_hours };
        for (const c of r.costs.per_traveler) if (c.flight_minutes !== null) hours[c.name] = Math.round((c.flight_minutes / 60) * 10) / 10;
        enrichment[d.id].travel_hours = hours;
        fromCache += r.cached;
        trace.data.destinations_price_checked.push(d.id);
        plan = replan();
        if (plan.winner?.destination.id === d.id) break;
      } catch (e) {
        trace.data.errors.push(`prices ${d.id}: ${message(e)}`);
        trace.data.prices = "unavailable";
        break;
      }
    }
    trace.data.price_searches = quota.used;
    if (trace.data.destinations_price_checked.length && trace.data.prices === "skipped") {
      trace.data.prices = quota.used === 0 && fromCache > 0 ? "cache" : "serpapi";
    }
  }

  if (plan.status === "ok" && plan.winner) {
    const w = plan.winner;
    if (deps.prices && !w.costs) plan.warnings.push(`Prices for ${w.destination.name} are estimates; live prices were not checked.`);
    for (const c of w.costs?.per_traveler ?? []) {
      if (c.nonstop_required && c.flight_usd === null) plan.warnings.push(`No nonstop flight found for ${c.name} from ${c.origin}.`);
    }
  }
  plan.constraints = constraints;

  // 6. The written itinerary.
  if (llm && plan.status === "ok") {
    const r = await writeItinerary(llm, plan, travelers, constraints ?? []);
    usage = addUsage(usage, r.usage);
    trace.itinerary.attempts = r.attempts;
    if (r.itinerary) {
      plan.itinerary = r.itinerary;
      plan.summary = r.summary ?? undefined;
      trace.itinerary.source = "claude";
    }
    // Re-run the plan checks on what is actually being returned.
    plan.checks = checkPlan(plan, constraints ? applyConstraints(travelers, constraints) : travelers, enrichment);
  }

  // Without Claude or external data this is just the planner, so there is no trace to show.
  if (llm || deps.prices || deps.publicData !== false) plan.agent = { ...trace, ...usage, duration_ms: Date.now() - started };
  return plan;
}
