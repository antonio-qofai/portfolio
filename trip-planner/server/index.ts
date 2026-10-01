// Production wiring: the API backed by Postgres, with Claude and live prices
// when their keys are set. Imported by the Vercel function and the Vite dev server.

import { createApp } from "./app";
import { ClaudeLlm } from "./claude/llm";
import { serpApiFetcher } from "./data/prices";
import { PostgresStore } from "./store-postgres";

const url = process.env.DATABASE_URL;
if (!url) throw new Error("DATABASE_URL is not set. Copy .env.example to .env and fill it in.");

const anthropicKey = process.env.ANTHROPIC_API_KEY;
if (!anthropicKey) console.warn("ANTHROPIC_API_KEY is not set. Plans will use keyword rules and template itineraries.");

const serpApiKey = process.env.SERPAPI_API_KEY;
if (!serpApiKey) console.warn("SERPAPI_API_KEY is not set. Plans will use estimated prices.");

export const handle = createApp(new PostgresStore(url), {
  llm: anthropicKey ? new ClaudeLlm(anthropicKey) : null,
  // The free plan allows 250 searches a month; stop at 200 to leave room for manual testing.
  prices: serpApiKey ? { fetcher: serpApiFetcher(serpApiKey), monthlyCap: Number(process.env.SERPAPI_MONTHLY_CAP ?? 200) } : undefined,
});
