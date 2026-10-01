// Date handling. Everything works on YYYY-MM-DD strings in UTC, so the result
// never shifts by a day depending on the viewer's time zone.

import type { DateRange, Traveler } from "./types";

const DAY_MS = 86_400_000;

/** Accepts YYYY-MM-DD or M/D/YYYY. Returns YYYY-MM-DD, or null if unparseable. */
export function parseDate(raw: string | undefined | null): string | null {
  if (!raw) return null;
  const s = raw.trim();
  let y: number, m: number, d: number;

  const iso = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(s);
  const us = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(s);
  if (iso) [y, m, d] = [Number(iso[1]), Number(iso[2]), Number(iso[3])];
  else if (us) [y, m, d] = [Number(us[3]), Number(us[1]), Number(us[2])];
  else return null;

  const date = new Date(Date.UTC(y, m - 1, d));
  // Rejects dates like 2026-02-31 that Date would silently roll over.
  if (date.getUTCFullYear() !== y || date.getUTCMonth() !== m - 1 || date.getUTCDate() !== d) return null;
  return toIso(date);
}

function toIso(date: Date): string {
  return date.toISOString().slice(0, 10);
}

export function addDays(iso: string, n: number): string {
  return toIso(new Date(Date.parse(iso) + n * DAY_MS));
}

export function daysBetween(start: string, end: string): number {
  return Math.round((Date.parse(end) - Date.parse(start)) / DAY_MS);
}

function eachDay(range: DateRange): string[] {
  const days: string[] = [];
  const n = daysBetween(range.start, range.end);
  for (let i = 0; i <= n; i++) days.push(addDays(range.start, i));
  return days;
}

/** Dates every traveler is free, sorted. */
export function commonDates(travelers: Traveler[]): string[] {
  if (travelers.length === 0) return [];
  const sets = travelers.map(t => new Set(t.availability.flatMap(eachDay)));
  return [...sets[0]].filter(d => sets.every(s => s.has(d))).sort();
}

/** Every run of consecutive dates in a sorted list. */
export function consecutiveRuns(dates: string[]): DateRange[] {
  const runs: DateRange[] = [];
  for (const d of dates) {
    const last = runs[runs.length - 1];
    if (last && addDays(last.end, 1) === d) last.end = d;
    else runs.push({ start: d, end: d });
  }
  return runs;
}

/**
 * The earliest window of exactly `length` days inside the shared dates,
 * or null if no run is that long.
 */
export function findWindow(dates: string[], length: number): DateRange | null {
  for (const run of consecutiveRuns(dates)) {
    if (daysBetween(run.start, run.end) + 1 >= length) {
      return { start: run.start, end: addDays(run.start, length - 1) };
    }
  }
  return null;
}

export function longestRun(dates: string[]): number {
  return Math.max(0, ...consecutiveRuns(dates).map(r => daysBetween(r.start, r.end) + 1));
}
