import type { TravelerConstraints } from "./constraints";

// Shared types for the trip planner. Everything the planner reads or returns is
// described here, so the UI, the server and the tests agree on one shape.

export interface Destination {
  id: string;
  name: string;
  country_or_region: string;
  climate_tags: string[];
  activity_tags: string[];
  estimated_cost_per_person_usd: number;
  notes: string;
  latitude: number;
  longitude: number;
  /** Main airport, IATA code. */
  airport: string;
}

/** Real-world data fetched for a destination, folded into the plan by the planner. */
export interface Enrichment {
  /** Estimated flight hours from each traveler's home, by name. */
  travel_hours?: Record<string, number>;
  /** Climate tags derived from the real weather for the trip dates, replacing the static ones for matching. */
  weather_tags?: string[];
  weather?: WeatherSummary;
  /** Live per-person cost (flights, lodging share, daily spend), by name. Replaces the static estimate. */
  costs?: LiveCosts;
}

export interface WeatherSummary {
  /** "forecast" for dates within about two weeks, "typical" (past years' average for these dates) otherwise. */
  kind: "forecast" | "typical";
  high_c: number;
  low_c: number;
  /** Average daily precipitation in mm. */
  precipitation_mm: number;
  summary: string;
}

export interface TravelerCost {
  name: string;
  origin: string;
  flight_usd: number | null;
  flight_minutes: number | null;
  stops: number | null;
  nonstop_required: boolean;
  lodging_usd: number;
  daily_usd: number;
  total_usd: number;
  source: "live" | "estimate";
  booking_url: string;
}

export interface LiveCosts {
  checked_at: string;
  hotel: { name: string; nightly_usd: number; rating: number | null; url: string } | null;
  per_traveler: TravelerCost[];
}

/** An inclusive range of ISO dates (YYYY-MM-DD). */
export interface DateRange {
  start: string;
  end: string;
}

/** One person's answers, already validated. */
export interface Traveler {
  name: string;
  departure_city: string;
  home_airport?: string;
  availability: DateRange[];
  budget_usd: number;
  /** Fraction over budget this person tolerates, 0.1 means 10%. */
  budget_flex_pct: number;
  interests: string[];
  climate_preference?: string;
  passport_ready: boolean;
  avoid_crowds: boolean;
  trip_length_min?: number;
  trip_length_max?: number;
  max_travel_hours?: number;
  accessibility_needs?: string;
  dietary_restrictions?: string;
  must_have?: string;
  dealbreakers?: string;
  notes?: string;
  /** Set from notes by applyConstraints. Destination tags this person wants to avoid. */
  avoid_tags?: string[];
  /** Set from notes by applyConstraints. Overrides the keyword check when present. */
  domestic_only?: boolean;
}

export type Status = "ok" | "needs_info" | "error";

export type ExclusionReason = "home_city" | "passport" | "domestic_requested" | "over_budget" | "too_far";

export interface ScoreBreakdown {
  /** 0 to 100, the weighted sum of the components below. */
  total: number;
  /** Each component is 0 to 1 before weighting. */
  components: {
    interests: number;
    fairness: number;
    climate: number;
    budget: number;
    crowds: number;
    /** Share of the group with a dealbreaker here, stored negative like crowds. */
    dealbreakers: number;
  };
}

export interface TravelerFit {
  name: string;
  matched_interests: string[];
  unmatched_interests: string[];
  climate_match: boolean | null;
  dealbreaker_hits: string[];
  budget_limit_usd: number;
  /** This person's cost here: live if prices were checked, otherwise the static estimate. */
  cost_usd: number;
  within_budget: boolean;
  travel_hours: number | null;
}

export interface RankedDestination {
  destination: Destination;
  score: ScoreBreakdown;
  excluded_by: ExclusionReason[];
}

export interface ItineraryDay {
  day: number;
  date: string;
  title: string;
  activity: string;
  /** Who this day is mainly for. Set on generated itineraries. */
  for?: string[];
}

export interface AgentTrace {
  model: string;
  data: {
    weather: "open-meteo" | "unavailable" | "skipped";
    travel_time: "estimated" | "unavailable";
    prices: "serpapi" | "cache" | "quota_reached" | "unavailable" | "skipped";
    price_searches: number;
    destinations_price_checked: string[];
    errors: string[];
  };
  constraints: { source: "claude" | "cache" | "keywords"; error?: string };
  itinerary: { source: "claude" | "template"; attempts: { failed_checks: string[]; error?: string }[] };
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  duration_ms: number;
}

export interface PlanResult {
  status: Status;
  generated_at: string;
  group_size: number;
  trip_window: {
    start: string;
    end: string;
    days: number;
    target_days: number;
  } | null;
  winner: {
    destination: Destination;
    score: ScoreBreakdown;
    per_traveler: TravelerFit[];
    explanation: string;
    weather?: WeatherSummary;
    costs?: LiveCosts;
  } | null;
  /** The best few feasible destinations, in rank order, winner first. */
  shortlist: { destination: Destination; score: ScoreBreakdown; weather?: WeatherSummary }[];
  runner_up: {
    destination: Destination;
    score: ScoreBreakdown;
    feasible: boolean;
    reason: string;
  } | null;
  itinerary: ItineraryDay[];
  budget: {
    per_person_limit_usd: number;
    limiting_traveler: string;
    group_budget_total_usd: number;
    group_est_cost_total_usd: number;
    state: "healthy" | "tight" | "insufficient" | "unknown";
  } | null;
  excluded_counts: Partial<Record<ExclusionReason, number>>;
  missing_fields?: Record<string, string[]>;
  limiting_factors: string[];
  warnings: string[];
  suggested_fixes: string[];
  checks: { passed: boolean; failed: string[] };
  /** What the language-model steps did. Absent when the planner runs on its own. */
  agent?: AgentTrace;
  constraints?: TravelerConstraints[];
  summary?: string;
}
