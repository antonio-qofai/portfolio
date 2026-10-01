// Runs the fetch-style API handler (Request in, Response out) behind a plain
// Node (req, res) server. Used by the Vite dev server and by the Vercel
// function, so production runs the same code path as local development.

import type { IncomingMessage, ServerResponse } from "http";

export type FetchHandler = (req: Request) => Promise<Response>;

export async function serveNode(handle: FetchHandler, req: IncomingMessage, res: ServerResponse): Promise<void> {
  const url = new URL(req.url ?? "/", `http://${req.headers.host ?? "localhost"}`);
  // Vercel routes every /api/* request to one function and passes the original
  // path as __path, in case the platform hands the function the rewritten one.
  const original = url.searchParams.get("__path");
  if (original !== null) {
    url.pathname = `/api/${original}`;
    url.searchParams.delete("__path");
  }

  const chunks: Buffer[] = [];
  for await (const chunk of req) chunks.push(chunk as Buffer);
  const body = ["GET", "HEAD"].includes(req.method ?? "GET") ? undefined : Buffer.concat(chunks);

  const headers = new Headers();
  for (const [key, value] of Object.entries(req.headers)) {
    if (Array.isArray(value)) value.forEach(v => headers.append(key, v));
    else if (value !== undefined) headers.set(key, value);
  }

  const response = await handle(new Request(url, { method: req.method, headers, body }));
  res.statusCode = response.status;
  response.headers.forEach((value, key) => res.setHeader(key, value));
  res.end(Buffer.from(await response.arrayBuffer()));
}
