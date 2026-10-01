// Vercel function entry. scripts/build-vercel.ts bundles this into a single
// file, so imports, JSON data, and dependencies all resolve at build time.

import type { IncomingMessage, ServerResponse } from "http";
import { handle } from "./index";
import { serveNode } from "./node-adapter";

export default function handler(req: IncomingMessage, res: ServerResponse) {
  return serveNode(handle, req, res);
}
