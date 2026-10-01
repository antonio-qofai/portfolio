// The planning pipeline: validate answers, find shared dates, filter
// destinations, score, pick a winner and runner-up, build the itinerary,
// then check the result before returning it.

import { checkPlan } from "./checks";
import { commonDates, findWindow, longestRun } from "./dates";
import { templateItinerary } from "./itinerary";
import { budgetLimit, costFor, exclusionReasons, scoreDestination, travelerFit, WEIGHTS } from "./scoring";
import { applyConstraints, type TravelerConstraints } from "./constraints";
import { parseTravelers, type RawAnswers } from "./survey";
import type { Destination, Enrichment, ExclusionReason, PlanResult, RankedDestination, ScoreBreakdown, Traveler } from "./types";

export const TARGET_DAYS = 4;
export const MIN_DAYS = 2;

const usd = (n: number) => `$${Math.round(n).toLocaleString("en-US")}`;
const pct = (n: number) => `${Math.round(n * 100)}%`;

function emptyPlan(status: PlanResult["status"], groupSize: number): PlanResult {
  return {
    status,
    generated_at: new Date().toISOString(),
    group_size: groupSize,
    trip_window: null,
    winner: null,
    runner_up: null,
    itinerary: [],
    budget: null,
    excluded_counts: {},
    shortlist: [],
    limiting_factors: [],
    warnings: [],
    suggested_fixes: [],
    checks: { passed: true, failed: [] },
  };
}

export const SHORTLIST_SIZE = 5;

function finish(plan: PlanResult, travelers: Traveler[], enrichment?: Record<string, Enrichment>): PlanResult {
  plan.checks = checkPlan(plan, travelers, enrichment);
  return plan;
}

/** Which one person's absence would open up the longest shared window. */
function scheduleBlocker(travelers: Traveler[]): { name: string; days: number } | null {
  if (travelers.length < 3) return null;
  let best: { name: string; days: number } | null = null;
  for (const t of travelers) {
    const days = longestRun(commonDates(travelers.filter(o => o !== t)));
    if (!best || days > best.days) best = { name: t.name, days };
  }
  return best && best.days >= MIN_DAYS ? best : null;
}

const REASON_TEXT: Record<ExclusionReason, string> = {
  home_city: "is someone's home city",
  passport: "needs a passport not everyone has",
  domestic_requested: "is international and someone asked to stay domestic",
  over_budget: "is over the tightest budget",
  too_far: "is a longer flight than someone said they would take",
};

function explainWinner(d: Destination, score: ScoreBreakdown, travelers: Traveler[], limit: number, limiter: string, e?: Enrichment): string {
  const c = score.components;
  const leftOut = travelers.filter(t => travelerFit(d, t).matched_interests.length === 0).map(t => t.name);
  const parts = [
    leftOut.length
      ? `${d.name} covers ${pct(c.interests)} of each person's interests on average, though nothing on ${leftOut.join(" and ")}'s list.`
      : `${d.name} covers ${pct(c.interests)} of each person's interests on average, and at least ${pct(c.fairness)} for everyone.`,
  ];
  const withPref = travelers.filter(t => t.climate_preference);
  if (withPref.length) {
    const matched = Math.round(c.climate * withPref.length);
    parts.push(`It matches the climate preference of ${matched} of ${withPref.length} people.`);
  }
  if (e?.costs) {
    // Live prices differ by home airport, so report the range and the person with the least room.
    const fits = travelers.map(t => ({ name: t.name, cost: costFor(d, t, e), room: budgetLimit(t) - costFor(d, t, e) }));
    const costs = fits.map(f => f.cost);
    const tightest = fits.reduce((a, b) => (b.room < a.room ? b : a));
    parts.push(`With current prices it costs ${usd(Math.min(...costs))} to ${usd(Math.max(...costs))} per person depending on home airport, and ${tightest.name} has the least room, ${usd(tightest.room)} under their limit.`);
  } else {
    const headroom = limit - d.estimated_cost_per_person_usd;
    parts.push(`At about ${usd(d.estimated_cost_per_person_usd)} per person it sits ${usd(headroom)} under ${limiter}'s limit, the tightest in the group.`);
  }
  if (c.crowds < 0) parts.push("It is a crowded destination, which most of the group wanted to avoid, and it still came out ahead.");
  if (c.dealbreakers < 0) parts.push("It includes something at least one person asked to avoid, and it still came out ahead.");
  return parts.join(" ");
}

/** The single component where the runner-up loses the most weighted points to the winner. */
function explainRunnerUp(winner: RankedDestination, second: RankedDestination): string {
  const w = winner.score.components, r = second.score.components;
  // Every component contributes weight * value to the total (crowds is stored negative).
  const gaps = (Object.keys(WEIGHTS) as (keyof typeof WEIGHTS)[]).map(k => ({ k, gap: WEIGHTS[k] * (w[k] - r[k]) }));
  const biggest = gaps.sort((a, b) => b.gap - a.gap)[0];
  const why: Record<keyof typeof WEIGHTS, string> = {
    interests: "it covers less of what the group said they want",
    fairness: "it leaves at least one person with less of what they asked for",
    climate: "fewer people's climate preferences match",
    budget: "it costs more for the same fit",
    crowds: "it is crowded and most of the group wants to avoid crowds",
    dealbreakers: "it has something on someone's list of things to avoid",
  };
  const margin = winner.score.total - second.score.total;
  return `${margin.toFixed(1)} points behind ${winner.destination.name}, mainly because ${why[biggest.k]}. Estimated at about ${usd(second.destination.estimated_cost_per_person_usd)} per person.`;
}

export function planTrip(
  input: Traveler[] | RawAnswers[],
  destinations: Destination[],
  options: {
    targetDays?: number;
    constraints?: TravelerConstraints[];
    /** Real-world data by destination id: travel time, weather, live prices. */
    enrichment?: Record<string, Enrichment>;
  } = {},
): PlanResult {
  const targetDays = options.targetDays ?? TARGET_DAYS;
  const enrichment = options.enrichment ?? {};

  // 1. Validate answers. Anyone missing required fields blocks the plan.
  const isParsed = (x: unknown): x is Traveler => Array.isArray((x as Traveler).availability);
  const parsed = input.every(isParsed)
    ? { travelers: input as Traveler[], missing_fields: {} }
    : parseTravelers(input as RawAnswers[]);
  const { missing_fields } = parsed;
  // Constraints read from notes (if any) refine interests, dealbreakers, and domestic-only.
  const travelers = options.constraints ? applyConstraints(parsed.travelers, options.constraints) : parsed.travelers;

  if (Object.keys(missing_fields).length > 0) {
    const plan = emptyPlan("needs_info", travelers.length + Object.keys(missing_fields).length);
    plan.missing_fields = missing_fields;
    plan.suggested_fixes = Object.entries(missing_fields).map(
      ([name, fields]) => `Ask ${name} to fill in: ${fields.join(", ").replace(/_/g, " ")}.`,
    );
    return finish(plan, travelers, enrichment);
  }
  if (travelers.length === 0) {
    const plan = emptyPlan("error", 0);
    plan.limiting_factors = ["No survey responses."];
    plan.suggested_fixes = ["Share the trip link and wait for at least one response."];
    return finish(plan, travelers, enrichment);
  }

  const plan = emptyPlan("ok", travelers.length);

  // 2. Shared dates. Try the target length, then shorter, down to MIN_DAYS.
  const shared = commonDates(travelers);
  let window = null;
  let days = targetDays;
  for (; days >= MIN_DAYS; days--) {
    window = findWindow(shared, days);
    if (window) break;
  }
  if (!window) {
    plan.status = "error";
    const longest = longestRun(shared);
    plan.limiting_factors.push(
      longest === 0
        ? "No overlap: there is no single day everyone is free."
        : `No overlap long enough: the group shares at most ${longest} consecutive day.`,
    );
    const blocker = scheduleBlocker(travelers);
    if (blocker) plan.suggested_fixes.push(`Without ${blocker.name}, the rest of the group shares ${blocker.days} consecutive days. Check whether ${blocker.name} can move dates.`);
    plan.suggested_fixes.push("Ask everyone to widen their availability.", "Consider splitting into two smaller trips.");
    return finish(plan, travelers, enrichment);
  }
  if (days < targetDays) plan.warnings.push(`No ${targetDays}-day overlap; using ${days}-day overlap instead.`);
  for (const t of travelers) {
    if (t.trip_length_min && days < t.trip_length_min) plan.warnings.push(`${t.name} wanted at least ${t.trip_length_min} days.`);
  }
  plan.trip_window = { ...window, days, target_days: targetDays };

  // 3. Budget. The group can only spend what its tightest member can.
  const limiter = travelers.reduce((a, b) => (budgetLimit(b) < budgetLimit(a) ? b : a));
  const limit = budgetLimit(limiter);

  // 4. Filter and score every destination, keeping the reasons for exclusions.
  const ranked: RankedDestination[] = destinations
    .map(d => ({
      destination: d,
      score: scoreDestination(d, travelers, limit, enrichment[d.id]),
      excluded_by: exclusionReasons(d, travelers, limit, enrichment[d.id]),
    }))
    .sort(
      (a, b) =>
        b.score.total - a.score.total ||
        a.destination.estimated_cost_per_person_usd - b.destination.estimated_cost_per_person_usd ||
        a.destination.id.localeCompare(b.destination.id),
    );

  for (const r of ranked) for (const reason of r.excluded_by) plan.excluded_counts[reason] = (plan.excluded_counts[reason] ?? 0) + 1;
  const feasible = ranked.filter(r => r.excluded_by.length === 0);

  const excludedHomes = ranked.filter(r => r.excluded_by.includes("home_city")).map(r => r.destination.name);
  if (excludedHomes.length) plan.limiting_factors.push(`Excluded home cities: ${excludedHomes.join(", ")}.`);
  const noPassport = travelers.filter(t => !t.passport_ready).map(t => t.name);
  if (noPassport.length) plan.limiting_factors.push(`${noPassport.join(", ")} ${noPassport.length === 1 ? "has" : "have"} no passport ready, so only US destinations qualify.`);
  else if (plan.excluded_counts.domestic_requested) plan.limiting_factors.push("Someone asked to stay domestic, so only US destinations qualify.");
  if (plan.excluded_counts.over_budget) {
    plan.limiting_factors.push(`${limiter.name}'s budget (${usd(limit)} including flex) rules out ${plan.excluded_counts.over_budget} destinations.`);
  }
  if (plan.excluded_counts.too_far) {
    const limited = travelers.filter(t => t.max_travel_hours).map(t => `${t.name} (${t.max_travel_hours}h)`);
    plan.limiting_factors.push(`Flight-time limits from ${limited.join(", ")} rule out ${plan.excluded_counts.too_far} destinations.`);
  }

  // Closest alternative: the best-scoring place that fails exactly one rule,
  // preferring budget misses, since money is the easiest rule to bend.
  const closest = (exclude: string[]) =>
    ranked
      .filter(r => r.excluded_by.length === 1 && !exclude.includes(r.destination.id) && r.excluded_by[0] !== "home_city")
      .sort((a, b) => Number(b.excluded_by[0] === "over_budget") - Number(a.excluded_by[0] === "over_budget"))[0];

  plan.budget = {
    per_person_limit_usd: Math.round(limit),
    limiting_traveler: limiter.name,
    group_budget_total_usd: Math.round(travelers.reduce((s, t) => s + t.budget_usd, 0)),
    group_est_cost_total_usd: 0,
    state: "unknown",
  };

  if (feasible.length === 0) {
    plan.status = "error";
    plan.trip_window = null;
    plan.budget.state = "insufficient";
    const alt = closest([]);
    if (alt) {
      plan.runner_up = {
        destination: alt.destination,
        score: alt.score,
        feasible: false,
        reason: `Closest alternative, but it ${REASON_TEXT[alt.excluded_by[0]]}.`,
      };
      if (alt.excluded_by[0] === "over_budget") {
        plan.suggested_fixes.push(
          `Raising ${limiter.name}'s budget to ${usd(alt.destination.estimated_cost_per_person_usd)} would make ${alt.destination.name} possible.`,
        );
      }
    }
    if (noPassport.length) plan.suggested_fixes.push(`If ${noPassport.join(" and ")} can get a passport in time, international destinations open up.`);
    if (plan.excluded_counts.domestic_requested) plan.suggested_fixes.push("Relax the stay-domestic request if possible.");
    if (plan.excluded_counts.over_budget) plan.suggested_fixes.push("Raise the lowest budgets or increase budget flex.");
    return finish(plan, travelers, enrichment);
  }

  plan.shortlist = feasible.slice(0, SHORTLIST_SIZE).map(r => ({ destination: r.destination, score: r.score, weather: enrichment[r.destination.id]?.weather }));

  // 5. Winner and runner-up.
  const [win, second] = feasible;
  const e = enrichment[win.destination.id];
  const costs = travelers.map(t => costFor(win.destination, t, e));
  plan.budget.group_est_cost_total_usd = Math.round(costs.reduce((a, b) => a + b, 0));
  // Tight means someone is into their flex; healthy means everyone is under their stated budget.
  const flexUsers = travelers.filter((t, i) => costs[i] > t.budget_usd).map(t => t.name);
  plan.budget.state = flexUsers.length ? "tight" : "healthy";
  if (flexUsers.length) plan.warnings.push(`${win.destination.name} uses part of ${flexUsers.join(", ")}'s budget flex.`);

  plan.winner = {
    destination: win.destination,
    score: win.score,
    per_traveler: travelers.map(t => travelerFit(win.destination, t, e)),
    explanation: explainWinner(win.destination, win.score, travelers, limit, limiter.name, e),
    weather: e?.weather,
    costs: e?.costs,
  };

  if (second) {
    plan.runner_up = { destination: second.destination, score: second.score, feasible: true, reason: explainRunnerUp(win, second) };
  } else {
    const alt = closest([win.destination.id]);
    if (alt) {
      plan.runner_up = {
        destination: alt.destination,
        score: alt.score,
        feasible: false,
        reason: `Only one destination passed every rule. This is the closest alternative, but it ${REASON_TEXT[alt.excluded_by[0]]}.`,
      };
    }
  }

  const unhappy = plan.winner.per_traveler.filter(f => f.matched_interests.length === 0).map(f => f.name);
  if (unhappy.length) plan.warnings.push(`None of ${unhappy.join(", ")}'s interests match ${win.destination.name}.`);
  for (const f of plan.winner.per_traveler) {
    if (f.dealbreaker_hits.length) plan.warnings.push(`${f.name} asked to avoid ${f.dealbreaker_hits.join(", ").replace(/_/g, " ")}, which ${win.destination.name} is known for.`);
  }

  // 6. Itinerary.
  plan.itinerary = templateItinerary(win.destination, travelers, window.start, days);
  return finish(plan, travelers, enrichment);
}
