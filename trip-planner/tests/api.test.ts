import { beforeEach, describe, expect, test } from "vitest";
import { createApp } from "../server/app";
import { MemoryStore } from "../server/store";
import type { TravelerInput } from "../planner/schema";

let store: MemoryStore;
let handle: ReturnType<typeof createApp>;

beforeEach(() => {
  store = new MemoryStore();
  handle = createApp(store, { today: () => "2026-09-30", publicData: false });
});

async function call(method: string, path: string, body?: unknown, headers: Record<string, string> = {}) {
  const res = await handle(
    new Request(`http://test${path}`, {
      method,
      headers: { "content-type": "application/json", ...headers },
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  );
  return { status: res.status, body: res.status === 204 ? null : await res.json() };
}

const tripInput = { name: "Spring break", organizer_name: "Alex", date_from: "2026-06-01", date_to: "2026-06-30" };

const answers = (name: string, overrides: Partial<TravelerInput> = {}): TravelerInput => ({
  name,
  departure_city: "Boston",
  home_airport: "BOS",
  availability: [{ start: "2026-06-05", end: "2026-06-15" }],
  budget_usd: 1500,
  budget_flex_pct: 0.1,
  interests: ["food", "museums"],
  climate_preference: "mild",
  passport_ready: true,
  avoid_crowds: false,
  notes: "",
  ...overrides,
});

async function newTrip() {
  const { body } = await call("POST", "/api/trips", tripInput);
  return body as { id: string; admin_key: string };
}

describe("trips", () => {
  test("create returns a share id and a separate admin key", async () => {
    const { status, body } = await call("POST", "/api/trips", tripInput);
    expect(status).toBe(201);
    expect(body.id).toMatch(/^[\w-]{11}$/);
    expect(body.admin_key.length).toBeGreaterThan(20);
  });

  test("rejects invalid trips with field details", async () => {
    const { status, body } = await call("POST", "/api/trips", { ...tripInput, date_to: "2026-05-01" });
    expect(status).toBe(422);
    expect(body.details.fieldErrors.date_to).toBeDefined();
  });

  test("the public view never exposes the admin key or anyone's answers", async () => {
    const trip = await newTrip();
    await call("POST", `/api/trips/${trip.id}/responses`, answers("Maya"));
    const { body } = await call("GET", `/api/trips/${trip.id}`);
    const text = JSON.stringify(body);
    expect(text).not.toContain(trip.admin_key);
    expect(text).not.toContain("edit_token");
    expect(text).not.toContain("budget_usd");
    expect(body.responses).toEqual([{ id: expect.any(String), name: "Maya", is_sample: false }]);
  });

  test("the organizer view includes answers, and a wrong key is refused", async () => {
    const trip = await newTrip();
    await call("POST", `/api/trips/${trip.id}/responses`, answers("Maya"));
    const ok = await call("GET", `/api/trips/${trip.id}`, undefined, { "x-admin-key": trip.admin_key });
    expect(ok.body.responses[0].traveler.budget_usd).toBe(1500);
    const bad = await call("GET", `/api/trips/${trip.id}`, undefined, { "x-admin-key": "nope" });
    expect(bad.status).toBe(403);
  });

  test("expired trips are gone", async () => {
    const trip = await newTrip();
    store.trips.get(trip.id)!.expires_at = "2000-01-01T00:00:00.000Z";
    expect((await call("GET", `/api/trips/${trip.id}`)).status).toBe(404);
  });
});

describe("responses", () => {
  test("a friend can submit, then read and edit their own answers with the edit token", async () => {
    const trip = await newTrip();
    const created = await call("POST", `/api/trips/${trip.id}/responses`, answers("Maya"));
    expect(created.status).toBe(201);
    const { id, edit_token } = created.body;

    const read = await call("GET", `/api/trips/${trip.id}/responses/${id}`, undefined, { "x-edit-token": edit_token });
    expect(read.body.traveler.name).toBe("Maya");

    const edited = await call("PUT", `/api/trips/${trip.id}/responses/${id}`, answers("Maya", { budget_usd: 2000 }), { "x-edit-token": edit_token });
    expect(edited.status).toBe(200);
    expect((await store.getResponse(trip.id, id))!.traveler.budget_usd).toBe(2000);
  });

  test("nobody else can edit a response", async () => {
    const trip = await newTrip();
    const { body } = await call("POST", `/api/trips/${trip.id}/responses`, answers("Maya"));
    const res = await call("PUT", `/api/trips/${trip.id}/responses/${body.id}`, answers("Maya"), { "x-edit-token": "guess" });
    expect(res.status).toBe(403);
  });

  test("the organizer can remove a response", async () => {
    const trip = await newTrip();
    const { body } = await call("POST", `/api/trips/${trip.id}/responses`, answers("Maya"));
    const res = await call("DELETE", `/api/trips/${trip.id}/responses/${body.id}`, undefined, { "x-admin-key": trip.admin_key });
    expect(res.status).toBe(204);
    expect(await store.listResponses(trip.id)).toHaveLength(0);
  });

  test("validates answers and rejects duplicate names", async () => {
    const trip = await newTrip();
    const invalid = await call("POST", `/api/trips/${trip.id}/responses`, answers("Maya", { interests: [], home_airport: "BOSTON" }));
    expect(invalid.status).toBe(422);
    expect(Object.keys(invalid.body.details.fieldErrors).sort()).toEqual(["home_airport", "interests"]);

    await call("POST", `/api/trips/${trip.id}/responses`, answers("Maya"));
    const dup = await call("POST", `/api/trips/${trip.id}/responses`, answers("maya"));
    expect(dup.status).toBe(409);
  });
});

describe("planning", () => {
  test("only the organizer can plan, and the saved plan is visible on the share link", async () => {
    const trip = await newTrip();
    await call("POST", `/api/trips/${trip.id}/responses`, answers("Maya"));
    await call("POST", `/api/trips/${trip.id}/responses`, answers("Dev", { departure_city: "Denver", home_airport: "DEN" }));

    expect((await call("POST", `/api/trips/${trip.id}/plan`)).status).toBe(403);

    const plan = await call("POST", `/api/trips/${trip.id}/plan`, undefined, { "x-admin-key": trip.admin_key });
    expect(plan.status).toBe(200);
    expect(plan.body.status).toBe("ok");
    expect(plan.body.winner.destination.id).not.toMatch(/boston|denver/);

    const shared = await call("GET", `/api/trips/${trip.id}`);
    expect(shared.body.plan.winner.destination.id).toBe(plan.body.winner.destination.id);
  });

  test("the sample trip comes with six friends and plans successfully", async () => {
    const { body } = await call("POST", "/api/trips/sample");
    const trip = await call("GET", `/api/trips/${body.id}`);
    expect(trip.body.is_sample).toBe(true);
    expect(trip.body.responses.map((r: { name: string }) => r.name)).toEqual(["Maya", "Dev", "Ariana", "Omar", "Noah", "Sofia"]);

    const plan = await call("POST", `/api/trips/${body.id}/plan`, undefined, { "x-admin-key": body.admin_key });
    expect(plan.body.status).toBe("ok");
    expect(plan.body.checks.passed).toBe(true);
    // Sofia has no passport, so the winner must be domestic.
    expect(plan.body.winner.destination.country_or_region).toMatch(/USA/);
  });
});

describe("sample friends on a real trip", () => {
  test("land inside the organizer's window, skip taken names, and can only be added once", async () => {
    const trip = await newTrip();
    await call("POST", `/api/trips/${trip.id}/responses`, answers("Maya", { availability: [{ start: "2026-06-01", end: "2026-06-30" }] }));
    const admin = { "x-admin-key": trip.admin_key };

    expect((await call("POST", `/api/trips/${trip.id}/sample-friends`)).status).toBe(403);
    const added = await call("POST", `/api/trips/${trip.id}/sample-friends`, undefined, admin);
    expect(added.body.added).toEqual(["Dev", "Ariana", "Omar", "Noah", "Sofia"]);
    expect((await call("POST", `/api/trips/${trip.id}/sample-friends`, undefined, admin)).status).toBe(409);

    const plan = await call("POST", `/api/trips/${trip.id}/plan`, undefined, admin);
    expect(plan.body.status).toBe("ok");
    expect(plan.body.trip_window.start >= tripInput.date_from).toBe(true);
    expect(plan.body.trip_window.end <= tripInput.date_to).toBe(true);
  });
});

describe("limits", () => {
  test("rate-limits trip creation per IP", async () => {
    const headers = { "x-forwarded-for": "1.2.3.4" };
    for (let i = 0; i < 20; i++) expect((await call("POST", "/api/trips", tripInput, headers)).status).toBe(201);
    expect((await call("POST", "/api/trips", tripInput, headers)).status).toBe(429);
    expect((await call("POST", "/api/trips", tripInput, { "x-forwarded-for": "5.6.7.8" })).status).toBe(201);
  });

  test("unknown routes and bad JSON get clean errors", async () => {
    expect((await call("GET", "/api/nope")).status).toBe(404);
    const res = await handle(new Request("http://test/api/trips", { method: "POST", body: "{" }));
    expect(res.status).toBe(400);
  });
});
