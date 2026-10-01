// Hard filters and the scoring model.
//
// Filters decide what is allowed at all (home cities, passports, budget).
// Scoring ranks what is left. Every component is normalized to 0..1 before
// weighting, so no single factor (cheapness, in the original version) can
// drown out what the group actually said they want.

import type { Destination, Enrichment, ExclusionReason, ScoreBreakdown, Traveler, TravelerFit } from "./types";

export const WEIGHTS = {
  interests: 0.4, // how much of each person's interest list the place covers, averaged
  fairness: 0.15, // the same, for the least-served person, so nobody gets zero
  climate: 0.2, // share of people whose climate preference matches
  budget: 0.15, // headroom under the tightest budget
  crowds: 0.1, // penalty, applied only if most of the group avoids crowds
  dealbreakers: 0.2, // penalty, scaled by the share of people with a dealbreaker here
} as const;

/**
 * Survey interests use looser words than destination tags. Each interest
 * matches any tag in its list. Unlisted interests match themselves only.
 */
const INTEREST_TAGS: Record<string, string[]> = {
  beach: ["beach", "island", "snorkeling", "water_sports"],
  relaxation: ["relaxation", "thermal_baths", "hot_springs", "luxury", "all_inclusive", "beach"],
  food: ["food", "markets", "wine"],
  budget_food: ["food", "markets"],
  nightlife: ["nightlife", "music", "shows"],
  clubs: ["nightlife"],
  live_music: ["music", "nightlife", "festivals"],
  festivals: ["festivals", "music"],
  museums: ["museums", "history"],
  art: ["museums", "culture"],
  history: ["history", "historic", "museums"],
  architecture: ["architecture", "modern_architecture", "historic"],
  culture: ["culture", "history", "historic"],
  walking_tours: ["walking_tours"],
  markets: ["markets"],
  local_markets: ["markets"],
  shopping: ["shopping", "markets"],
  hiking: ["hiking", "mountain"],
  nature: ["nature", "hiking", "wildlife", "lakes", "lake"],
  parks: ["nature"],
  lakes: ["lakes", "lake"],
  scenic_views: ["scenic_views", "scenic", "mountain"],
  stargazing: ["desert", "northern_lights", "arctic"],
  adventure: ["adventure", "skiing", "snorkeling"],
};

const CLIMATE_TAGS: Record<string, string[]> = {
  warm: ["warm", "hot", "tropical"],
  hot: ["hot", "tropical", "desert"],
  mild: ["mild", "spring_weather", "coastal"],
  cool: ["cool", "cold", "mountain", "highland", "arctic"],
  cold: ["cold", "arctic"],
  dry: ["dry", "desert"],
};

/**
 * Destinations treated as crowded in peak season. A hardcoded list is an
 * assumption carried over from the original brief. Anything tagged "busy" is
 * treated the same way.
 */
export const POPULAR_DESTINATIONS = new Set([
  "miami_beach", "cancun", "las_vegas", "new_york_city", "tokyo", "rome",
  "barcelona", "santorini", "amsterdam", "dubai", "bali", "honolulu",
]);

export function isDomestic(d: Destination): boolean {
  return /\bUSA\b/.test(d.country_or_region);
}

function words(s: string): string {
  return ` ${s.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim()} `;
}

/** True if the destination is this home city ("Chicago" matches "chicago"; "LA" does not match "Atlanta"). */
export function isHomeCity(d: Destination, city: string): boolean {
  const c = words(city);
  if (c.trim() === "") return false;
  return words(d.name).includes(c) || words(d.id).includes(c);
}

function destinationTags(d: Destination): Set<string> {
  return new Set([...d.activity_tags, ...d.climate_tags].map(t => t.toLowerCase()));
}

export function tagsForInterest(interest: string): string[] {
  return INTEREST_TAGS[interest] ?? [interest];
}

export function matchesInterest(d: Destination, interest: string): boolean {
  const tags = destinationTags(d);
  return tagsForInterest(interest).some(t => tags.has(t));
}

/** Uses the real weather for the trip dates when it was fetched, otherwise the destination's static climate tags. */
export function matchesClimate(d: Destination, pref: string, e?: Enrichment): boolean {
  const tags = e?.weather_tags ? new Set(e.weather_tags) : destinationTags(d);
  return (CLIMATE_TAGS[pref] ?? [pref]).some(t => tags.has(t));
}

/** This person's cost at a destination: live prices when checked, otherwise the static estimate. */
export function costFor(d: Destination, t: Traveler, e?: Enrichment): number {
  return e?.costs?.per_traveler.find(c => c.name === t.name)?.total_usd ?? d.estimated_cost_per_person_usd;
}

export function budgetLimit(t: Traveler): number {
  return t.budget_usd * (1 + t.budget_flex_pct);
}

export function wantsDomestic(t: Traveler): boolean {
  const wants = `${t.notes ?? ""} ${t.must_have ?? ""}`.toLowerCase();
  const refuses = (t.dealbreakers ?? "").toLowerCase();
  return /domestic|usa[ _]only|\bus[ _]only/.test(wants) || /international/.test(refuses);
}

export function exclusionReasons(d: Destination, travelers: Traveler[], perPersonLimit: number, e?: Enrichment): ExclusionReason[] {
  const reasons: ExclusionReason[] = [];
  if (travelers.some(t => isHomeCity(d, t.departure_city))) reasons.push("home_city");
  if (!isDomestic(d)) {
    if (travelers.some(t => !t.passport_ready)) reasons.push("passport");
    else if (travelers.some(t => t.domestic_only ?? wantsDomestic(t))) reasons.push("domestic_requested");
  }
  // With live prices each person is checked against their own cost; otherwise the
  // group shares one estimate, checked against the tightest budget.
  const overBudget = e?.costs
    ? travelers.some(t => costFor(d, t, e) > budgetLimit(t))
    : d.estimated_cost_per_person_usd > perPersonLimit;
  if (overBudget) reasons.push("over_budget");
  // Each person's stated longest flight is a hard limit for the whole group.
  if (travelers.some(t => t.max_travel_hours && (e?.travel_hours?.[t.name] ?? 0) > t.max_travel_hours)) reasons.push("too_far");
  return reasons;
}

export function dealbreakerHits(d: Destination, t: Traveler): string[] {
  const tags = destinationTags(d);
  return (t.avoid_tags ?? []).filter(tag => tags.has(tag));
}

export function travelerFit(d: Destination, t: Traveler, e?: Enrichment): TravelerFit {
  const matched = t.interests.filter(i => matchesInterest(d, i));
  const limit = budgetLimit(t);
  const cost = costFor(d, t, e);
  return {
    name: t.name,
    matched_interests: matched,
    unmatched_interests: t.interests.filter(i => !matched.includes(i)),
    climate_match: t.climate_preference ? matchesClimate(d, t.climate_preference, e) : null,
    dealbreaker_hits: dealbreakerHits(d, t),
    budget_limit_usd: Math.round(limit),
    cost_usd: Math.round(cost),
    within_budget: cost <= limit,
    travel_hours: e?.travel_hours?.[t.name] ?? null,
  };
}

export function scoreDestination(d: Destination, travelers: Traveler[], perPersonLimit: number, e?: Enrichment): ScoreBreakdown {
  const fits = travelers.map(t => travelerFit(d, t, e));
  const coverage = fits.map((f, i) => f.matched_interests.length / travelers[i].interests.length);

  const interests = coverage.reduce((a, b) => a + b, 0) / coverage.length;
  const fairness = Math.min(...coverage);

  const withPref = fits.filter(f => f.climate_match !== null);
  // Nobody stated a preference, so climate should not move the ranking.
  const climate = withPref.length ? withPref.filter(f => f.climate_match).length / withPref.length : 0.5;

  // Headroom for whoever is closest to their limit: with live prices that is per person.
  const headroom = e?.costs
    ? Math.min(...travelers.map(t => (budgetLimit(t) - costFor(d, t, e)) / budgetLimit(t)))
    : (perPersonLimit - d.estimated_cost_per_person_usd) / perPersonLimit;
  const budget = Math.max(0, Math.min(1, headroom));

  const majorityAvoidCrowds = travelers.filter(t => t.avoid_crowds).length > travelers.length / 2;
  const popular = POPULAR_DESTINATIONS.has(d.id) || d.climate_tags.includes("busy");
  const crowds = majorityAvoidCrowds && popular ? 1 : 0;

  const dealbreakers = fits.filter(f => f.dealbreaker_hits.length > 0).length / fits.length;

  const total =
    100 *
    (WEIGHTS.interests * interests +
      WEIGHTS.fairness * fairness +
      WEIGHTS.climate * climate +
      WEIGHTS.budget * budget -
      WEIGHTS.crowds * crowds -
      WEIGHTS.dealbreakers * dealbreakers);

  const round = (n: number) => Math.round(n * 1000) / 1000;
  return {
    total: Math.round(total * 10) / 10,
    components: {
      interests: round(interests),
      fairness: round(fairness),
      climate: round(climate),
      budget: round(budget),
      crowds: crowds ? -1 : 0,
      dealbreakers: dealbreakers ? -round(dealbreakers) : 0,
    },
  };
}
