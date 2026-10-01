// Storage for trips and responses. The API talks to this interface only, so
// tests run against the in-memory version and production runs on Postgres.

import type { PlanResult, Traveler } from "../planner";

export interface Trip {
  id: string;
  admin_key: string;
  name: string;
  organizer_name: string;
  date_from: string;
  date_to: string;
  is_sample: boolean;
  created_at: string;
  expires_at: string;
  plan: PlanResult | null;
}

export interface TripResponse {
  id: string;
  trip_id: string;
  edit_token: string;
  traveler: Traveler;
  is_sample: boolean;
  created_at: string;
  updated_at: string;
}

export interface Store {
  createTrip(trip: Trip): Promise<void>;
  getTrip(id: string): Promise<Trip | null>;
  savePlan(tripId: string, plan: PlanResult): Promise<void>;

  listResponses(tripId: string): Promise<TripResponse[]>;
  getResponse(tripId: string, id: string): Promise<TripResponse | null>;
  createResponse(response: TripResponse): Promise<void>;
  updateResponse(tripId: string, id: string, traveler: Traveler): Promise<void>;
  deleteResponse(tripId: string, id: string): Promise<void>;

  /** Adds one hit to a counter and returns the new total for the current window. */
  hit(key: string, windowSeconds: number): Promise<number>;

  /** Cached results of slow or paid calls (Claude, prices, weather), keyed by their inputs. No TTL means keep forever. */
  getCached<T>(key: string): Promise<T | null>;
  setCached(key: string, value: unknown, ttlSeconds?: number): Promise<void>;
}

export class MemoryStore implements Store {
  trips = new Map<string, Trip>();
  responses = new Map<string, TripResponse>();
  counters = new Map<string, { window: number; count: number }>();
  cache = new Map<string, { value: unknown; expires: number }>();

  async createTrip(trip: Trip) {
    this.trips.set(trip.id, structuredClone(trip));
  }
  async getTrip(id: string) {
    const t = this.trips.get(id);
    return t && t.expires_at > new Date().toISOString() ? structuredClone(t) : null;
  }
  async savePlan(tripId: string, plan: PlanResult) {
    const t = this.trips.get(tripId);
    if (t) t.plan = structuredClone(plan);
  }
  async listResponses(tripId: string) {
    return [...this.responses.values()]
      .filter(r => r.trip_id === tripId)
      .sort((a, b) => a.created_at.localeCompare(b.created_at))
      .map(r => structuredClone(r));
  }
  async getResponse(tripId: string, id: string) {
    const r = this.responses.get(id);
    return r && r.trip_id === tripId ? structuredClone(r) : null;
  }
  async createResponse(response: TripResponse) {
    this.responses.set(response.id, structuredClone(response));
  }
  async updateResponse(tripId: string, id: string, traveler: Traveler) {
    const r = this.responses.get(id);
    if (r && r.trip_id === tripId) Object.assign(r, { traveler: structuredClone(traveler), updated_at: new Date().toISOString() });
  }
  async deleteResponse(tripId: string, id: string) {
    const r = this.responses.get(id);
    if (r && r.trip_id === tripId) this.responses.delete(id);
  }
  async hit(key: string, windowSeconds: number) {
    const window = Math.floor(Date.now() / 1000 / windowSeconds);
    const c = this.counters.get(key);
    const next = c && c.window === window ? { window, count: c.count + 1 } : { window, count: 1 };
    this.counters.set(key, next);
    return next.count;
  }

  async getCached<T>(key: string) {
    const hit = this.cache.get(key);
    return hit && hit.expires > Date.now() ? (structuredClone(hit.value) as T) : null;
  }
  async setCached(key: string, value: unknown, ttlSeconds?: number) {
    this.cache.set(key, { value: structuredClone(value), expires: ttlSeconds ? Date.now() + ttlSeconds * 1000 : Infinity });
  }
}
