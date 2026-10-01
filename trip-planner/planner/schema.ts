// The answers a friend submits through the trip form. The browser validates
// with this before sending and the server validates again before storing, so
// the planner only ever sees well-formed Travelers.

import { z } from "zod";
import { parseDate } from "./dates";

export const INTEREST_OPTIONS = [
  "beach", "food", "nightlife", "museums", "history", "culture", "hiking", "nature",
  "scenic_views", "shopping", "markets", "relaxation", "adventure", "architecture",
  "live_music", "walking_tours",
] as const;

export const CLIMATE_OPTIONS = ["warm", "mild", "cool", "dry"] as const;

const isoDate = z.string().refine(s => parseDate(s) === s, "Use YYYY-MM-DD");

const dateRange = z
  .object({ start: isoDate, end: isoDate })
  .refine(r => r.start <= r.end, "End date is before start date");

const optionalText = (max: number) =>
  z.string().trim().max(max).optional().transform(s => (s ? s : undefined));

export const travelerSchema = z.object({
  name: z.string().trim().min(1, "Required").max(40),
  departure_city: z.string().trim().min(1, "Required").max(60),
  home_airport: z
    .string()
    .trim()
    .toUpperCase()
    .regex(/^[A-Z]{3}$/, "Three-letter airport code")
    .optional()
    .or(z.literal("").transform(() => undefined)),
  availability: z.array(dateRange).min(1, "Add at least one date range").max(5),
  budget_usd: z.number().positive("Required").max(50_000),
  budget_flex_pct: z.number().min(0).max(1),
  interests: z.array(z.string()).min(1, "Pick at least one").max(8),
  climate_preference: z.enum(CLIMATE_OPTIONS).optional(),
  passport_ready: z.boolean(),
  avoid_crowds: z.boolean(),
  max_travel_hours: z.number().positive().max(30).optional(),
  notes: optionalText(500),
});

export type TravelerInput = z.input<typeof travelerSchema>;

export const tripSchema = z
  .object({
    name: z.string().trim().min(1, "Required").max(60),
    organizer_name: z.string().trim().min(1, "Required").max(40),
    date_from: isoDate,
    date_to: isoDate,
  })
  .refine(t => t.date_from <= t.date_to, { message: "End date is before start date", path: ["date_to"] });

export type TripInput = z.infer<typeof tripSchema>;
