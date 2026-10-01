// Postgres storage on Neon, over its HTTP driver (one round trip per query,
// no connection pool to manage inside serverless functions).

import { neon, type NeonQueryFunction } from "@neondatabase/serverless";
import type { PlanResult, Traveler } from "../planner";
import type { Store, Trip, TripResponse } from "./store";

const iso = (col: string) => `to_char(${col} at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"')`;

const TRIP_COLUMNS = `id, admin_key, name, organizer_name, date_from::text as date_from, date_to::text as date_to,
  is_sample, ${iso("created_at")} as created_at, ${iso("expires_at")} as expires_at, plan`;

/** jsonb parameters must be serialized here: the driver would send a JS array as a Postgres array, not JSON. */
const json = (v: unknown) => (v == null ? null : JSON.stringify(v));

const RESPONSE_COLUMNS = `id, trip_id, edit_token, traveler, is_sample,
  ${iso("created_at")} as created_at, ${iso("updated_at")} as updated_at`;

export class PostgresStore implements Store {
  private sql: NeonQueryFunction<false, false>;

  constructor(url: string) {
    this.sql = neon(url);
  }

  async createTrip(t: Trip) {
    // Opportunistic cleanup, so expired trips never need a scheduled job.
    await this.sql.query("delete from trips where expires_at < now()");
    await this.sql.query("delete from rate_limits where window_start < extract(epoch from now()) - 86400");
    await this.sql.query("delete from cache where expires_at < now()");
    await this.sql.query(
      `insert into trips (id, admin_key, name, organizer_name, date_from, date_to, is_sample, created_at, expires_at, plan)
       values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)`,
      [t.id, t.admin_key, t.name, t.organizer_name, t.date_from, t.date_to, t.is_sample, t.created_at, t.expires_at, json(t.plan)],
    );
  }

  async getTrip(id: string) {
    const rows = await this.sql.query(`select ${TRIP_COLUMNS} from trips where id = $1 and expires_at > now()`, [id]);
    return (rows[0] as Trip | undefined) ?? null;
  }

  async savePlan(tripId: string, plan: PlanResult) {
    await this.sql.query("update trips set plan = $2, plan_at = now() where id = $1", [tripId, json(plan)]);
  }

  async listResponses(tripId: string) {
    const rows = await this.sql.query(`select ${RESPONSE_COLUMNS} from responses where trip_id = $1 order by created_at, id`, [tripId]);
    return rows as TripResponse[];
  }

  async getResponse(tripId: string, id: string) {
    const rows = await this.sql.query(`select ${RESPONSE_COLUMNS} from responses where trip_id = $1 and id = $2`, [tripId, id]);
    return (rows[0] as TripResponse | undefined) ?? null;
  }

  async createResponse(r: TripResponse) {
    await this.sql.query(
      `insert into responses (id, trip_id, edit_token, traveler, is_sample, created_at, updated_at)
       values ($1, $2, $3, $4, $5, $6, $7)`,
      [r.id, r.trip_id, r.edit_token, json(r.traveler), r.is_sample, r.created_at, r.updated_at],
    );
  }

  async updateResponse(tripId: string, id: string, traveler: Traveler) {
    await this.sql.query("update responses set traveler = $3, updated_at = now() where trip_id = $1 and id = $2", [tripId, id, json(traveler)]);
  }

  async deleteResponse(tripId: string, id: string) {
    await this.sql.query("delete from responses where trip_id = $1 and id = $2", [tripId, id]);
  }

  async hit(key: string, windowSeconds: number) {
    const now = Math.floor(Date.now() / 1000);
    const window = now - (now % windowSeconds);
    const rows = await this.sql.query(
      `insert into rate_limits (key, window_start, count) values ($1, $2, 1)
       on conflict (key, window_start) do update set count = rate_limits.count + 1
       returning count`,
      [key, window],
    );
    return Number(rows[0].count);
  }

  async getCached<T>(key: string) {
    const rows = await this.sql.query("select value from cache where key = $1 and (expires_at is null or expires_at > now())", [key]);
    return (rows[0]?.value as T | undefined) ?? null;
  }

  async setCached(key: string, value: unknown, ttlSeconds?: number) {
    await this.sql.query(
      `insert into cache (key, value, expires_at) values ($1, $2, now() + make_interval(secs => $3))
       on conflict (key) do update set value = excluded.value, expires_at = excluded.expires_at, created_at = now()`,
      [key, json(value), ttlSeconds ?? null],
    );
  }

}
