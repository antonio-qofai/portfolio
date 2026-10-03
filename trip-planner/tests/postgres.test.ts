// Round-trips through the real Postgres store. Skipped unless DATABASE_URL is
// set (run with: node --env-file=.env node_modules/.bin/vitest run tests/postgres.test.ts).
// Catches what the in-memory store cannot, like how the driver encodes jsonb.

import { beforeAll, describe, expect, test } from "vitest";
import { newId } from "../server/app";
import { PostgresStore } from "../server/store-postgres";
import { sampleTravelers } from "../server/sample-trip";

const url = process.env.DATABASE_URL;

describe.skipIf(!url)("PostgresStore", () => {
  // Built in beforeAll: a skipped describe still runs its body during collection.
  let store: PostgresStore;
  beforeAll(() => {
    store = new PostgresStore(url!);
  });
  // The trip expires in a minute and is swept (with its responses) by the next createTrip.
  const tripId = `test-${newId(6)}`;
  const cacheKey = `test:${newId(6)}`;

  test("trips, responses, and plans survive the round trip", async () => {
    const now = new Date().toISOString();
    await store.createTrip({
      id: tripId, admin_key: "k", name: "Test", organizer_name: "Test", date_from: "2026-06-01", date_to: "2026-06-10",
      is_sample: true, created_at: now, expires_at: new Date(Date.now() + 60_000).toISOString(), plan: null,
    });
    const [traveler] = sampleTravelers("2026-06-03");
    await store.createResponse({ id: `${tripId}-r`, trip_id: tripId, edit_token: "t", traveler, is_sample: true, created_at: now, updated_at: now });

    expect((await store.getTrip(tripId))?.date_from).toBe("2026-06-01");
    expect((await store.listResponses(tripId))[0].traveler).toEqual(traveler);
  });

  test("arrays stored as jsonb come back as arrays", async () => {
    const value = [{ name: "Noah", avoid_tags: ["walking_tours"] }];
    await store.setCached(cacheKey, value);
    expect(await store.getCached(cacheKey)).toEqual(value);
  });
});
