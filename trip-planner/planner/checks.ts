// Must-pass checks on a plan. The planner runs these on its own output before
// returning it, and the result carries which ones failed. They are also the
// acceptance test for any itinerary a language model writes.

import { addDays } from "./dates";
import { exclusionReasons } from "./scoring";
import type { Enrichment, ItineraryDay, PlanResult, Traveler } from "./types";

export function checkItinerary(days: ItineraryDay[], window: { start: string; days: number }): string[] {
  const failed: string[] = [];
  if (days.length !== window.days) failed.push("itinerary_wrong_length");
  days.forEach((d, i) => {
    if (d.day !== i + 1 || d.date !== addDays(window.start, i)) failed.push(`itinerary_day_${i + 1}_misdated`);
    if (!d.activity?.trim()) failed.push(`itinerary_day_${i + 1}_empty`);
  });
  return failed;
}

export function checkPlan(plan: PlanResult, travelers: Traveler[], enrichment?: Record<string, Enrichment>): { passed: boolean; failed: string[] } {
  const failed: string[] = [];

  if (plan.status === "ok") {
    const { winner, runner_up, budget, trip_window } = plan;
    if (!winner) failed.push("missing_winner");
    if (!runner_up) failed.push("missing_runner_up");
    if (winner && runner_up && winner.destination.id === runner_up.destination.id) failed.push("runner_up_same_as_winner");
    if (!trip_window) failed.push("missing_trip_window");
    if (!budget) failed.push("missing_budget");

    if (winner && budget) {
      const reasons = exclusionReasons(winner.destination, travelers, budget.per_person_limit_usd, enrichment?.[winner.destination.id]);
      reasons.forEach(r => failed.push(`winner_violates_${r}`));
    }
    if (trip_window) failed.push(...checkItinerary(plan.itinerary, trip_window));
  }

  if (plan.status === "needs_info" && Object.keys(plan.missing_fields ?? {}).length === 0) {
    failed.push("needs_info_without_missing_fields");
  }

  if (plan.status === "error") {
    if (plan.limiting_factors.length === 0) failed.push("error_without_limiting_factors");
    if (plan.suggested_fixes.length === 0) failed.push("error_without_suggested_fixes");
  }

  return { passed: failed.length === 0, failed };
}
