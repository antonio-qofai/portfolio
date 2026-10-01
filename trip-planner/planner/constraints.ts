// Structured constraints read from each person's free-text notes. Claude fills
// these in (server/claude/constraints.ts); the planner applies them here,
// deterministically. Without them the planner falls back to keyword rules.

import type { Traveler } from "./types";

export interface TravelerConstraints {
  name: string;
  /** Must stay in the US (expired passport, "domestic only", refuses international). */
  domestic_only: boolean;
  mobility: "full" | "limited_walking" | "wheelchair";
  /** e.g. "vegetarian", "gluten_free". Used by the itinerary, not the ranking. */
  dietary: string[];
  /** Destination tags this person wants to avoid ("no party towns" -> nightlife). */
  avoid_tags: string[];
  /** Destination tags their notes ask for beyond the interest chips they picked. */
  extra_interests: string[];
  nonstop_only: boolean;
  /** One short line restating the notes, shown to the organizer. */
  summary: string;
}

/** Folds constraints into each traveler: extra interests join the list, dealbreakers and domestic-only become fields the scorer reads. */
export function applyConstraints(travelers: Traveler[], constraints: TravelerConstraints[]): Traveler[] {
  const byName = new Map(constraints.map(c => [c.name.toLowerCase(), c]));
  return travelers.map(t => {
    const c = byName.get(t.name.toLowerCase());
    if (!c) return t;
    return {
      ...t,
      interests: [...new Set([...t.interests, ...c.extra_interests])],
      avoid_tags: c.avoid_tags,
      domestic_only: c.domestic_only,
    };
  });
}
