// The "Try a sample trip" group. Six made-up friends, each written to exercise
// a different part of the planner: an expired passport (domestic only), a
// wheelchair user, a tight budget with no flex, two people from the same city,
// someone with two separate availability windows, and short-flight limits.
// Dates are relative to a start date (six weeks out for the demo trip, or the
// organizer's window when added to a real trip), so the sample never goes stale.

import { addDays } from "../planner/dates";
import type { Traveler } from "../planner";

/** The first Friday at least six weeks from `today`. */
export function sampleTripStart(today: string): string {
  let d = addDays(today, 42);
  while (new Date(d).getUTCDay() !== 5) d = addDays(d, 1);
  return d;
}

/** Six friends whose shared availability is `base` through `base + 5`. */
export function sampleTravelers(base: string): Traveler[] {
  const range = (from: number, to: number) => ({ start: addDays(base, from), end: addDays(base, to) });

  return [
    {
      name: "Maya",
      departure_city: "New York",
      home_airport: "JFK",
      availability: [range(-3, 10)],
      budget_usd: 1700,
      budget_flex_pct: 0.1,
      interests: ["museums", "food", "walking_tours"],
      climate_preference: "mild",
      passport_ready: true,
      avoid_crowds: true,
      notes: "Vegetarian. Great food and museums would make my trip. Not a party town please.",
    },
    {
      name: "Dev",
      departure_city: "Seattle",
      home_airport: "SEA",
      availability: [range(-4, 6), range(12, 16)],
      budget_usd: 1400,
      budget_flex_pct: 0.15,
      interests: ["hiking", "nature", "scenic_views"],
      climate_preference: "mild",
      passport_ready: true,
      avoid_crowds: true,
      notes: "Calm vibe, outdoors when possible.",
    },
    {
      name: "Ariana",
      departure_city: "Chicago",
      home_airport: "ORD",
      availability: [range(-1, 8)],
      budget_usd: 1300,
      budget_flex_pct: 0.05,
      interests: ["nightlife", "food", "live_music"],
      climate_preference: "warm",
      passport_ready: true,
      avoid_crowds: false,
      max_travel_hours: 4,
      notes: "Nonstop flights only, I get anxious with connections.",
    },
    {
      name: "Omar",
      departure_city: "Chicago",
      home_airport: "ORD",
      availability: [range(0, 9)],
      budget_usd: 1300,
      budget_flex_pct: 0,
      interests: ["hiking", "food", "scenic_views"],
      climate_preference: "dry",
      passport_ready: true,
      avoid_crowds: true,
      notes: "Money is tight this year, $1,300 is a hard limit for me.",
    },
    {
      name: "Noah",
      departure_city: "Dallas",
      home_airport: "DFW",
      availability: [range(-6, 5)],
      budget_usd: 1600,
      budget_flex_pct: 0.1,
      interests: ["history", "museums", "architecture"],
      climate_preference: "mild",
      passport_ready: true,
      avoid_crowds: true,
      notes: "I use a wheelchair, so step-free places and no long walking tours.",
    },
    {
      name: "Sofia",
      departure_city: "Los Angeles",
      home_airport: "LAX",
      availability: [range(-2, 7)],
      budget_usd: 1900,
      budget_flex_pct: 0.1,
      interests: ["culture", "food", "markets"],
      climate_preference: "warm",
      passport_ready: false,
      avoid_crowds: false,
      notes: "My passport is expired, so it has to be somewhere in the US.",
    },
  ];
}
