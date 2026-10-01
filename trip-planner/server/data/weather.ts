// Weather for the actual trip dates, from Open-Meteo (free, no key). Within its
// 16-day forecast range we use the forecast; further out, the average of the
// same dates over the past three years, labeled as typical rather than predicted.

import { addDays, daysBetween } from "../../planner/dates";
import type { Destination, WeatherSummary } from "../../planner";
import type { Store } from "../store";

const FORECAST_DAYS = 16;
const PAST_YEARS = 3;
const DAY = 86_400;

interface Daily {
  temperature_2m_max: number[];
  temperature_2m_min: number[];
  precipitation_sum?: number[];
}

async function getDaily(url: string): Promise<Daily> {
  const res = await fetch(url, { signal: AbortSignal.timeout(8000) });
  if (!res.ok) throw new Error(`Open-Meteo returned ${res.status}`);
  return (await res.json()).daily;
}

const mean = (xs: number[]) => xs.reduce((a, b) => a + b, 0) / xs.length;
const round1 = (n: number) => Math.round(n * 10) / 10;

/** Climate tags the scorer understands, derived from real temperatures and rain. */
export function weatherTags(w: Pick<WeatherSummary, "high_c" | "precipitation_mm">): string[] {
  const tags: string[] = [];
  if (w.high_c >= 29) tags.push("hot");
  if (w.high_c >= 24) tags.push("warm");
  if (w.high_c >= 15 && w.high_c <= 25) tags.push("mild");
  if (w.high_c <= 16) tags.push("cool");
  if (w.high_c <= 8) tags.push("cold");
  if (w.precipitation_mm <= 1) tags.push("dry");
  return tags;
}

function describe(kind: WeatherSummary["kind"], high: number, low: number, rain: number): string {
  const f = (c: number) => Math.round((c * 9) / 5 + 32);
  const wet = rain >= 5 ? "rainy" : rain >= 1.5 ? "some rain likely" : "mostly dry";
  const lead = kind === "forecast" ? "Forecast" : "Typically";
  return `${lead}: highs around ${Math.round(high)}°C (${f(high)}°F), lows ${Math.round(low)}°C (${f(low)}°F), ${wet}.`;
}

export async function weatherFor(store: Store, d: Destination, start: string, days: number, today: string): Promise<WeatherSummary> {
  const end = addDays(start, days - 1);
  const inForecastRange = daysBetween(today, end) < FORECAST_DAYS && daysBetween(today, start) >= 0;
  const kind: WeatherSummary["kind"] = inForecastRange ? "forecast" : "typical";
  // Forecasts change daily; typical weather for a set of dates does not.
  const key = `weather:${kind}:${d.id}:${start}:${days}${kind === "forecast" ? `:${today}` : ""}`;
  const cached = await store.getCached<WeatherSummary>(key);
  if (cached) return cached;

  const at = `latitude=${d.latitude}&longitude=${d.longitude}`;
  let highs: number[], lows: number[], rain: number[];
  if (kind === "forecast") {
    const daily = await getDaily(
      `https://api.open-meteo.com/v1/forecast?${at}&daily=temperature_2m_max,temperature_2m_min,precipitation_sum&start_date=${start}&end_date=${end}&timezone=auto`,
    );
    [highs, lows, rain] = [daily.temperature_2m_max, daily.temperature_2m_min, daily.precipitation_sum ?? []];
  } else {
    const year = Number(today.slice(0, 4));
    const shift = (iso: string, back: number) => `${Number(iso.slice(0, 4)) - back}${iso.slice(4)}`;
    // Same calendar dates in each of the last few complete years.
    const years = Array.from({ length: PAST_YEARS }, (_, i) => Number(start.slice(0, 4)) - year + i + 1);
    const series = await Promise.all(
      years.map(back =>
        getDaily(
          `https://archive-api.open-meteo.com/v1/archive?${at}&daily=temperature_2m_max,temperature_2m_min,precipitation_sum&start_date=${shift(start, back)}&end_date=${shift(end, back)}&timezone=auto`,
        ),
      ),
    );
    highs = series.flatMap(s => s.temperature_2m_max);
    lows = series.flatMap(s => s.temperature_2m_min);
    rain = series.flatMap(s => s.precipitation_sum ?? []);
  }

  const clean = (xs: number[]) => xs.filter(x => typeof x === "number");
  const high = round1(mean(clean(highs)));
  const low = round1(mean(clean(lows)));
  const precipitation = round1(clean(rain).length ? mean(clean(rain)) : 0);
  const summary: WeatherSummary = { kind, high_c: high, low_c: low, precipitation_mm: precipitation, summary: describe(kind, high, low, precipitation) };
  await store.setCached(key, summary, kind === "forecast" ? DAY : 180 * DAY);
  return summary;
}
