import type { PlanResult, Traveler, TravelerInput, TripInput } from "@planner";

export interface PublicTrip {
  id: string;
  name: string;
  organizer_name: string;
  date_from: string;
  date_to: string;
  is_sample: boolean;
  expires_at: string;
  responses: { id: string; name: string; is_sample: boolean }[];
  plan: PlanResult | null;
}

export interface AdminTrip extends Omit<PublicTrip, "responses"> {
  responses: { id: string; is_sample: boolean; updated_at: string; traveler: Traveler }[];
}

export class ApiError extends Error {
  constructor(public status: number, message: string, public fieldErrors: Record<string, string[]> = {}) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown, headers: Record<string, string> = {}): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    headers: { "content-type": "application/json", ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, data.error ?? "Request failed", data.details?.fieldErrors);
  return data as T;
}

const admin = (key: string) => ({ "x-admin-key": key });
const owner = (token: string) => ({ "x-edit-token": token });

export const api = {
  createTrip: (input: TripInput) => request<{ id: string; admin_key: string }>("POST", "/trips", input),
  createSampleTrip: () => request<{ id: string; admin_key: string }>("POST", "/trips/sample"),
  getTrip: (id: string) => request<PublicTrip>("GET", `/trips/${id}`),
  getAdminTrip: (id: string, key: string) => request<AdminTrip>("GET", `/trips/${id}`, undefined, admin(key)),

  submitResponse: (tripId: string, answers: TravelerInput) =>
    request<{ id: string; edit_token: string }>("POST", `/trips/${tripId}/responses`, answers),
  getOwnResponse: (tripId: string, id: string, token: string) =>
    request<{ id: string; traveler: Traveler }>("GET", `/trips/${tripId}/responses/${id}`, undefined, owner(token)),
  updateResponse: (tripId: string, id: string, answers: TravelerInput, auth: { token?: string; adminKey?: string }) =>
    request("PUT", `/trips/${tripId}/responses/${id}`, answers, auth.adminKey ? admin(auth.adminKey) : owner(auth.token!)),
  deleteResponse: (tripId: string, id: string, adminKey: string) =>
    request("DELETE", `/trips/${tripId}/responses/${id}`, undefined, admin(adminKey)),

  addSampleFriends: (tripId: string, adminKey: string) =>
    request<{ added: string[] }>("POST", `/trips/${tripId}/sample-friends`, undefined, admin(adminKey)),
  plan: (tripId: string, adminKey: string) => request<PlanResult>("POST", `/trips/${tripId}/plan`, undefined, admin(adminKey)),
};
