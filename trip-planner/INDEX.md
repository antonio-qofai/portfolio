# Index

One line per file. shadcn primitives in `client/src/components/ui/` are listed
as a group.

| File | What it is |
|---|---|
| `README.md` | What it does, how the agent works, costs and limits, how to run it |
| `PRD.md` | The spec: inputs, planning rules, agent pipeline, limits, success criteria |
| `CLAUDE.md` | Standing rules for working in this repo, and the mistakes log |
| `CHANGELOG.md` | History from the original class project to now |
| `INDEX.md` | This file |
| `PORTFOLIO.md` | The paragraph shown for this project in the public portfolio README |
| `.env.example` | Names of the environment variables, no values |
| `vercel.json` | Tells Vercel to run `build:vercel` and use its output |
| `vite.config.ts` | Vite build, path aliases, test config, and the dev-server API middleware |
| `tsconfig.json` | TypeScript settings for the app, planner, server, scripts, and tests |
| `components.json` | shadcn/ui configuration |
| `data/destinations.json` | 61 destinations: tags, estimated cost, coordinates, main airport |
| `data/samples/*.csv` | Three survey groups from the original project, used as test fixtures |
| `db/schema.sql` | Tables: trips, responses, cache, rate limits. Idempotent |
| `docs/original-brief.md` | The class brief the first version was built against |
| `docs/screenshots/` | Screenshots from production, used in the README |
| `planner/index.ts` | Public surface of the planner, imported as `@planner` |
| `planner/types.ts` | Every shape the planner reads or returns, including enrichment and the agent trace |
| `planner/schema.ts` | Zod schemas for the trip and friend forms, shared by browser and server |
| `planner/survey.ts` | Parses raw answers (form or CSV row) into travelers, collecting missing fields per person |
| `planner/dates.ts` | UTC date strings, shared dates, consecutive windows |
| `planner/constraints.ts` | The shape of constraints read from notes, and how they fold into travelers |
| `planner/scoring.ts` | Hard filters, weights, interest and climate matching, per-person fit, the score |
| `planner/itinerary.ts` | The template itinerary, deterministic, used when Claude's is unavailable |
| `planner/checks.ts` | Must-pass checks on a plan and on any itinerary |
| `planner/plan.ts` | The planning pipeline: validate, dates, filter, score, winner, runner-up, explanations |
| `server/app.ts` | The HTTP API: routes, access control, rate limits, sample trips |
| `server/agent.ts` | The agent loop: notes, travel time, ranking, weather, prices, itinerary |
| `server/claude/llm.ts` | The Claude wrapper: structured output, refusal fallback, cost accounting |
| `server/claude/constraints.ts` | Step 1 prompt and schema: notes into constraints |
| `server/claude/itinerary.ts` | Step 6 prompt, schema, draft checks, and the retry loop |
| `server/data/geo.ts` | Geocoding home cities and estimating flight hours |
| `server/data/weather.ts` | Forecast or typical weather for the trip dates, and climate tags from it |
| `server/data/prices.ts` | SerpApi flight and hotel searches, the monthly quota, per-person costs |
| `server/sample-trip.ts` | The six made-up friends, dated relative to a start date |
| `server/store.ts` | The storage interface and the in-memory version used by tests |
| `server/store-postgres.ts` | The Postgres version on Neon |
| `server/index.ts` | Production wiring: Postgres, Claude, and prices when their keys are set |
| `server/node-adapter.ts` | Runs the fetch-style API behind a Node (req, res) server, for dev and Vercel |
| `server/vercel.ts` | The Vercel function entry, bundled by `scripts/build-vercel.ts` |
| `scripts/migrate.ts` | Applies `db/schema.sql` |
| `scripts/enrich-destinations.ts` | Adds coordinates and airports to destinations |
| `scripts/build-vercel.ts` | Builds `.vercel/output`: static app, bundled API function, routes |
| `client/index.html` | Page shell and social preview tags |
| `client/src/main.tsx` | React entry |
| `client/src/App.tsx` | Routes |
| `client/src/pages/landing.tsx` | Landing page, sample trip button, trips you organize |
| `client/src/pages/new-trip.tsx` | Create a trip |
| `client/src/pages/trip.tsx` | The share link: friend form, who's in, the plan |
| `client/src/pages/organize.tsx` | The organizer view: answers, share link, plan button, plan |
| `client/src/pages/not-found.tsx` | 404 page |
| `client/src/components/plan-result.tsx` | The plan: winner, itinerary, per-person fit, costs, shortlist, score, agent trace |
| `client/src/components/traveler-form.tsx` | The friend form |
| `client/src/components/form.tsx` | Form controls: field, inputs, select, chips, toggle |
| `client/src/components/layout.tsx` | Page header, footer, loading and error pages |
| `client/src/components/json-viewer.tsx` | Raw planner output viewer |
| `client/src/components/ui/*` | shadcn primitives: badge, button, card, scroll area, tabs, textarea, toast, tooltip |
| `client/src/lib/api.ts` | Typed client for the API |
| `client/src/lib/saved.ts` | Remembers organizer keys and edit tokens in this browser |
| `client/src/lib/utils.ts` | Class name helper |
| `client/src/hooks/use-toast.ts` | Toast notifications |
| `client/src/index.css` | Theme tokens and base styles |
| `client/public/*` | Favicon and social preview image |
| `tests/planner.test.ts` | The original brief's five cases, each scoring fix, filters, scoring, itinerary |
| `tests/samples.test.ts` | Pinned outcomes for the three sample CSV groups |
| `tests/api.test.ts` | Routes, access control, validation, sample trips, rate limits |
| `tests/agent.test.ts` | The agent with a scripted Claude: constraints, retries, fallbacks, cache |
| `tests/data.test.ts` | SerpApi parsing, quota, travel time, weather tags, price-driven re-ranking |
| `tests/postgres.test.ts` | Round trips through the real database, skipped without `DATABASE_URL` |
| `tests/fixtures/*.json` | One saved SerpApi flight search and one hotel search |
