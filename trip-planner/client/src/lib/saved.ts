// Remembers, in this browser only, which trips you organize and which response
// is yours on each trip. A convenience: the links still work without it.

type Saved = {
  organized: Record<string, { name: string; admin_key: string; created: string }>;
  responses: Record<string, { id: string; edit_token: string }>;
};

const KEY = "trip-planner";

function read(): Saved {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) return { organized: {}, responses: {}, ...JSON.parse(raw) };
  } catch {
    // Private mode or blocked storage. Fall through to empty.
  }
  return { organized: {}, responses: {} };
}

function write(s: Saved) {
  try {
    localStorage.setItem(KEY, JSON.stringify(s));
  } catch {
    // Nothing to do. The share and organizer links still work.
  }
}

export const saved = {
  organized: () => read().organized,
  rememberOrganized(tripId: string, name: string, admin_key: string) {
    const s = read();
    s.organized[tripId] = { name, admin_key, created: new Date().toISOString() };
    write(s);
  },
  forgetOrganized(tripId: string) {
    const s = read();
    delete s.organized[tripId];
    write(s);
  },
  response: (tripId: string) => read().responses[tripId],
  rememberResponse(tripId: string, id: string, edit_token: string) {
    const s = read();
    s.responses[tripId] = { id, edit_token };
    write(s);
  },
};

/** Organizer links carry the admin key in the URL fragment, which browsers never send to the server. */
export function organizerUrl(tripId: string, adminKey: string): string {
  return `${location.origin}/t/${tripId}/organize#k=${adminKey}`;
}

export function shareUrl(tripId: string): string {
  return `${location.origin}/t/${tripId}`;
}

export function adminKeyFromHash(): string | null {
  return new URLSearchParams(location.hash.slice(1)).get("k");
}
