// Where people live and how long it takes to fly from there. Home cities are
// geocoded with Open-Meteo (free, no key) and cached; flight time is estimated
// from great-circle distance, since real schedules only come with paid searches.

import type { Destination, Traveler } from "../../planner";
import type { Store } from "../store";

export interface Place {
  name: string;
  latitude: number;
  longitude: number;
}

const DAY = 86_400;

export async function geocodeCity(store: Store, city: string): Promise<Place | null> {
  const key = `geo:${city.trim().toLowerCase()}`;
  const cached = await store.getCached<Place | { missing: true }>(key);
  if (cached) return "missing" in cached ? null : cached;

  const url = `https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(city.trim())}&count=5&language=en&format=json`;
  const res = await fetch(url, { signal: AbortSignal.timeout(8000) });
  if (!res.ok) throw new Error(`Geocoding failed (${res.status})`);
  type R = { name: string; latitude: number; longitude: number; population?: number };
  const results: R[] = (await res.json()).results ?? [];
  // The most populous match: "Charleston" means South Carolina, not West Virginia.
  const best = results.sort((a, b) => (b.population ?? 0) - (a.population ?? 0))[0];
  const place = best ? { name: best.name, latitude: best.latitude, longitude: best.longitude } : null;
  await store.setCached(key, place ?? { missing: true }, 30 * DAY);
  return place;
}

export function distanceKm(a: { latitude: number; longitude: number }, b: { latitude: number; longitude: number }): number {
  const rad = (x: number) => (x * Math.PI) / 180;
  const dLat = rad(b.latitude - a.latitude);
  const dLon = rad(b.longitude - a.longitude);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a.latitude)) * Math.cos(rad(b.latitude)) * Math.sin(dLon / 2) ** 2;
  return 2 * 6371 * Math.asin(Math.sqrt(h));
}

/**
 * Rough door-to-door flying time for the longest leg of the trip: cruise at
 * about 800 km/h plus 45 minutes for takeoff, landing, and taxiing. Under
 * 150 km people drive, so it returns 0.
 */
export function estimateFlightHours(km: number): number {
  if (km < 150) return 0;
  return Math.round((km / 800 + 0.75) * 10) / 10;
}

/** Estimated flight hours for every traveler to every destination. Travelers whose city can't be found are left out. */
export async function travelHours(store: Store, travelers: Traveler[], destinations: Destination[]): Promise<Record<string, Record<string, number>>> {
  const homes = await Promise.all(travelers.map(t => geocodeCity(store, t.departure_city).catch(() => null)));
  const out: Record<string, Record<string, number>> = {};
  for (const d of destinations) {
    out[d.id] = {};
    travelers.forEach((t, i) => {
      const home = homes[i];
      if (home) out[d.id][t.name] = estimateFlightHours(distanceKm(home, d));
    });
  }
  return out;
}
