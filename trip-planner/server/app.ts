// The HTTP API. One fetch-style handler (Request in, Response out) that runs
// the same way on Vercel and inside the Vite dev server.
//
// Access model: a trip id is the share link, so anyone with it can respond and
// see the plan. The organizer gets a separate admin key for editing and
// planning. Each response gets an edit token, kept in the friend's browser,
// so they can change their own answers later.

import { randomBytes } from "node:crypto";
import { z } from "zod";
import type { PlanResult, Traveler } from "../planner";
import { runAgent, type AgentDeps } from "./agent";
import type { Llm } from "./claude/llm";
import { travelerSchema, tripSchema } from "../planner/schema";
import { addDays } from "../planner/dates";
import { sampleTravelers, sampleTripStart } from "./sample-trip";
import type { Store, Trip, TripResponse } from "./store";

const TRIP_TTL_DAYS = 60;
const SAMPLE_TTL_DAYS = 7;
const MAX_RESPONSES = 20;

/** Requests per hour, per IP. */
// Planning calls Claude, so it gets the tightest limit.
const LIMITS = { create: 20, respond: 60, plan: 10 } as const;

export function newId(bytes: number): string {
  return randomBytes(bytes).toString("base64url");
}

class HttpError extends Error {
  constructor(public status: number, message: string, public details?: unknown) {
    super(message);
  }
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

async function readJson(req: Request): Promise<unknown> {
  try {
    return await req.json();
  } catch {
    throw new HttpError(400, "Body must be JSON");
  }
}

function parse<S extends z.ZodTypeAny>(schema: S, body: unknown): z.output<S> {
  const r = schema.safeParse(body);
  if (!r.success) throw new HttpError(422, "Invalid input", r.error.flatten());
  return r.data;
}

function clientIp(req: Request): string {
  return req.headers.get("x-forwarded-for")?.split(",")[0].trim() || "local";
}

async function rateLimit(store: Store, req: Request, action: keyof typeof LIMITS) {
  const n = await store.hit(`${action}:${clientIp(req)}`, 3600);
  if (n > LIMITS[action]) throw new HttpError(429, "Too many requests, try again in an hour");
}

function daysFromNow(days: number): string {
  return new Date(Date.now() + days * 86_400_000).toISOString();
}

/** What anyone with the share link may see. No admin key, no edit tokens, no one else's answers. */
function publicTrip(trip: Trip, responses: TripResponse[]) {
  return {
    id: trip.id,
    name: trip.name,
    organizer_name: trip.organizer_name,
    date_from: trip.date_from,
    date_to: trip.date_to,
    is_sample: trip.is_sample,
    expires_at: trip.expires_at,
    responses: responses.map(r => ({ id: r.id, name: r.traveler.name, is_sample: r.is_sample })),
    plan: trip.plan,
  };
}

function adminTrip(trip: Trip, responses: TripResponse[]) {
  return {
    ...publicTrip(trip, responses),
    responses: responses.map(r => ({ id: r.id, is_sample: r.is_sample, updated_at: r.updated_at, traveler: r.traveler })),
  };
}

async function loadTrip(store: Store, id: string): Promise<Trip> {
  const trip = await store.getTrip(id);
  if (!trip) throw new HttpError(404, "Trip not found or expired");
  return trip;
}

function requireAdmin(req: Request, trip: Trip) {
  if (req.headers.get("x-admin-key") !== trip.admin_key) throw new HttpError(403, "Organizer link required");
}

export function createApp(
  store: Store,
  options: { today?: () => string; llm?: Llm | null; prices?: AgentDeps["prices"]; publicData?: boolean } = {},
) {
  const today = options.today ?? (() => new Date().toISOString().slice(0, 10));
  const agentDeps: AgentDeps = { store, llm: options.llm ?? null, prices: options.prices, publicData: options.publicData, today };

  async function createTrip(req: Request) {
    await rateLimit(store, req, "create");
    const input = parse(tripSchema, await readJson(req));
    const trip: Trip = {
      id: newId(8),
      admin_key: newId(18),
      ...input,
      is_sample: false,
      created_at: new Date().toISOString(),
      expires_at: daysFromNow(TRIP_TTL_DAYS),
      plan: null,
    };
    await store.createTrip(trip);
    return json({ id: trip.id, admin_key: trip.admin_key }, 201);
  }

  async function createSampleTrip(req: Request) {
    await rateLimit(store, req, "create");
    // A fresh copy per visitor, so nobody edits someone else's demo.
    const travelers = sampleTravelers(sampleTripStart(today()));
    const dates = travelers.flatMap(t => t.availability.flatMap(r => [r.start, r.end])).sort();
    const trip: Trip = {
      id: newId(8),
      admin_key: newId(18),
      name: "Long weekend with the college group",
      organizer_name: "Sample organizer",
      date_from: dates[0],
      date_to: dates[dates.length - 1],
      is_sample: true,
      created_at: new Date().toISOString(),
      expires_at: daysFromNow(SAMPLE_TTL_DAYS),
      plan: null,
    };
    await store.createTrip(trip);
    await addSampleResponses(trip.id, travelers);
    return json({ id: trip.id, admin_key: trip.admin_key }, 201);
  }

  async function addSampleResponses(tripId: string, travelers: Traveler[]) {
    for (const traveler of travelers) {
      const now = new Date().toISOString();
      await store.createResponse({ id: newId(8), trip_id: tripId, edit_token: newId(18), traveler, is_sample: true, created_at: now, updated_at: now });
    }
  }

  /** Fills a real trip with the sample friends, for organizers trying it out alone. */
  async function addSampleFriends(req: Request, tripId: string) {
    await rateLimit(store, req, "respond");
    const trip = await loadTrip(store, tripId);
    requireAdmin(req, trip);
    const existing = await store.listResponses(tripId);
    if (existing.some(r => r.is_sample)) throw new HttpError(409, "Sample friends are already on this trip");
    const taken = new Set(existing.map(r => r.traveler.name.toLowerCase()));
    // Start a few days into the window so the friends' ranges mostly fall inside it.
    const start = addDays(trip.date_from, Math.min(3, Math.max(0, (Date.parse(trip.date_to) - Date.parse(trip.date_from)) / 86_400_000 - 5)));
    const friends = sampleTravelers(start).filter(t => !taken.has(t.name.toLowerCase()));
    if (existing.length + friends.length > MAX_RESPONSES) throw new HttpError(409, "Not enough room for the sample friends");
    await addSampleResponses(tripId, friends);
    return json({ added: friends.map(f => f.name) }, 201);
  }

  async function getTrip(req: Request, id: string) {
    const trip = await loadTrip(store, id);
    const responses = await store.listResponses(id);
    if (req.headers.get("x-admin-key")) {
      requireAdmin(req, trip);
      return json(adminTrip(trip, responses));
    }
    return json(publicTrip(trip, responses));
  }

  async function createResponse(req: Request, tripId: string) {
    await rateLimit(store, req, "respond");
    await loadTrip(store, tripId);
    const traveler = parse(travelerSchema, await readJson(req));
    const existing = await store.listResponses(tripId);
    if (existing.length >= MAX_RESPONSES) throw new HttpError(409, `This trip already has ${MAX_RESPONSES} responses`);
    if (existing.some(r => r.traveler.name.toLowerCase() === traveler.name.toLowerCase())) {
      throw new HttpError(409, `Someone named ${traveler.name} already responded. Use a different name, or edit that response.`);
    }
    const now = new Date().toISOString();
    const response: TripResponse = { id: newId(8), trip_id: tripId, edit_token: newId(18), traveler, is_sample: false, created_at: now, updated_at: now };
    await store.createResponse(response);
    return json({ id: response.id, edit_token: response.edit_token }, 201);
  }

  /** The friend who wrote it (edit token) or the organizer (admin key) may read, change, or delete a response. */
  async function authorizeResponse(req: Request, tripId: string, id: string) {
    const trip = await loadTrip(store, tripId);
    const response = await store.getResponse(tripId, id);
    if (!response) throw new HttpError(404, "Response not found");
    const isOwner = req.headers.get("x-edit-token") === response.edit_token;
    const isAdmin = req.headers.get("x-admin-key") === trip.admin_key;
    if (!isOwner && !isAdmin) throw new HttpError(403, "Not your response");
    return response;
  }

  async function getResponse(req: Request, tripId: string, id: string) {
    const response = await authorizeResponse(req, tripId, id);
    return json({ id: response.id, traveler: response.traveler });
  }

  async function updateResponse(req: Request, tripId: string, id: string) {
    await rateLimit(store, req, "respond");
    await authorizeResponse(req, tripId, id);
    const traveler = parse(travelerSchema, await readJson(req));
    await store.updateResponse(tripId, id, traveler);
    return json({ id });
  }

  async function deleteResponse(req: Request, tripId: string, id: string) {
    await authorizeResponse(req, tripId, id);
    await store.deleteResponse(tripId, id);
    return new Response(null, { status: 204 });
  }

  async function runPlan(req: Request, tripId: string) {
    await rateLimit(store, req, "plan");
    const trip = await loadTrip(store, tripId);
    requireAdmin(req, trip);
    const responses = await store.listResponses(tripId);
    const plan: PlanResult = await runAgent(responses.map(r => r.traveler), agentDeps);
    await store.savePlan(tripId, plan);
    return json(plan);
  }

  const routes: [string, RegExp, (req: Request, ...params: string[]) => Promise<Response>][] = [
    ["POST", /^\/api\/trips$/, createTrip],
    ["POST", /^\/api\/trips\/sample$/, createSampleTrip],
    ["GET", /^\/api\/trips\/([\w-]+)$/, getTrip],
    ["POST", /^\/api\/trips\/([\w-]+)\/responses$/, createResponse],
    ["GET", /^\/api\/trips\/([\w-]+)\/responses\/([\w-]+)$/, getResponse],
    ["PUT", /^\/api\/trips\/([\w-]+)\/responses\/([\w-]+)$/, updateResponse],
    ["DELETE", /^\/api\/trips\/([\w-]+)\/responses\/([\w-]+)$/, deleteResponse],
    ["POST", /^\/api\/trips\/([\w-]+)\/plan$/, runPlan],
    ["POST", /^\/api\/trips\/([\w-]+)\/sample-friends$/, addSampleFriends],
  ];

  return async function handle(req: Request): Promise<Response> {
    const { pathname } = new URL(req.url);
    try {
      for (const [method, pattern, handler] of routes) {
        const m = pattern.exec(pathname);
        if (m && req.method === method) return await handler(req, ...m.slice(1));
      }
      if (pathname === "/api/health") return json({ ok: true });
      throw new HttpError(404, "Not found");
    } catch (e) {
      if (e instanceof HttpError) return json({ error: e.message, details: e.details }, e.status);
      console.error(e);
      return json({ error: "Something went wrong" }, 500);
    }
  };
}
