// Turns raw survey answers (from a form or a CSV row) into validated Travelers.
// Anything missing is collected per person instead of failing on the first gap,
// so the organizer sees every hole at once.

import Papa from "papaparse";
import { parseDate } from "./dates";
import type { DateRange, Traveler } from "./types";

export type RawAnswers = Record<string, unknown>;

/** Budget flex used when a traveler does not give one. From the original brief. */
export const DEFAULT_BUDGET_FLEX = 0.2;

function str(raw: RawAnswers, ...keys: string[]): string {
  for (const k of keys) {
    const v = raw[k] ?? raw[k.toLowerCase()] ?? raw[k[0].toUpperCase() + k.slice(1)];
    if (v !== undefined && v !== null && String(v).trim() !== "") return String(v).trim();
  }
  return "";
}

function bool(raw: RawAnswers, key: string, fallback: boolean): boolean {
  const v = str(raw, key).toLowerCase();
  if (["true", "yes", "y", "1"].includes(v)) return true;
  if (["false", "no", "n", "0"].includes(v)) return false;
  return fallback;
}

function num(raw: RawAnswers, key: string): number | undefined {
  const v = parseMoney(str(raw, key));
  return Number.isFinite(v) ? v : undefined;
}

/** "$1,500", "1500", "1500.50" all parse. Anything else is NaN. */
export function parseMoney(raw: string): number {
  const clean = raw.replace(/[$,\s]/g, "");
  if (!/^\d+(\.\d+)?$/.test(clean)) return NaN;
  return Number(clean);
}

/** "0.1", "10", "10%" all mean 10%. An explicit 0 stays 0. */
export function parseFlex(raw: string): number {
  if (raw.trim() === "") return DEFAULT_BUDGET_FLEX;
  const n = Number(raw.replace("%", "").trim());
  if (!Number.isFinite(n) || n < 0) return DEFAULT_BUDGET_FLEX;
  return n > 1 ? n / 100 : n;
}

function parseAvailability(raw: RawAnswers): DateRange[] {
  const ranges: DateRange[] = [];

  // "2026-06-20..2026-07-05;2026-08-01..2026-08-10" allows several windows.
  for (const seg of str(raw, "available_dates").split(";")) {
    const [a, b] = seg.split("..");
    const start = parseDate(a), end = parseDate(b);
    if (start && end && start <= end) ranges.push({ start, end });
  }

  // Fall back to a single start/end pair if no windows were given.
  if (ranges.length === 0) {
    const start = parseDate(str(raw, "start_date")), end = parseDate(str(raw, "end_date"));
    if (start && end && start <= end) ranges.push({ start, end });
  }
  return ranges;
}

function list(raw: string): string[] {
  return raw.split(",").map(s => s.trim().toLowerCase().replace(/\s+/g, "_")).filter(Boolean);
}

export type ParseResult =
  | { ok: true; traveler: Traveler }
  | { ok: false; name: string; missing: string[] };

export function parseTraveler(raw: RawAnswers, index: number): ParseResult {
  const name = str(raw, "name");
  const departure_city = str(raw, "departure_city", "origin");
  const home_airport = str(raw, "home_airport").toUpperCase();
  const availability = parseAvailability(raw);
  const budget = parseMoney(str(raw, "budget"));
  const interests = list(str(raw, "top_interests"));

  const missing: string[] = [];
  if (!name) missing.push("name");
  if (!departure_city && !home_airport) missing.push("departure_city");
  if (availability.length === 0) missing.push("availability");
  if (!Number.isFinite(budget) || budget <= 0) missing.push("budget");
  if (interests.length === 0) missing.push("top_interests");

  if (missing.length > 0) return { ok: false, name: name || `Traveler ${index + 1}`, missing };

  const optional = (key: string) => str(raw, key) || undefined;
  return {
    ok: true,
    traveler: {
      name,
      departure_city: departure_city || home_airport,
      home_airport: home_airport || undefined,
      availability,
      budget_usd: budget,
      budget_flex_pct: parseFlex(str(raw, "budget_flex_pct")),
      interests,
      climate_preference: optional("climate_preference")?.toLowerCase(),
      // No answer means we can't assume a passport, so plan domestic.
      passport_ready: bool(raw, "passport_ready", false),
      avoid_crowds: bool(raw, "avoid_crowds", false),
      trip_length_min: num(raw, "trip_length_days_min"),
      trip_length_max: num(raw, "trip_length_days_max"),
      max_travel_hours: num(raw, "max_travel_hours"),
      accessibility_needs: optional("accessibility_needs"),
      dietary_restrictions: optional("dietary_restrictions"),
      must_have: optional("must_have"),
      dealbreakers: optional("dealbreakers"),
      notes: optional("notes"),
    },
  };
}

export function parseTravelers(rows: RawAnswers[]): {
  travelers: Traveler[];
  missing_fields: Record<string, string[]>;
} {
  const travelers: Traveler[] = [];
  const missing_fields: Record<string, string[]> = {};

  rows.forEach((row, i) => {
    // Fully blank rows (trailing lines in a spreadsheet export) are not people.
    if (Object.values(row).every(v => v === undefined || String(v).trim() === "")) return;
    const r = parseTraveler(row, i);
    if (r.ok) travelers.push(r.traveler);
    else missing_fields[r.name] = r.missing;
  });

  return { travelers, missing_fields };
}

export function parseSurveyCsv(csv: string): RawAnswers[] {
  return Papa.parse<RawAnswers>(csv, { header: true, skipEmptyLines: true }).data;
}
