Plans a group trip from one shared link and a two-minute form per person. A deterministic
planner filters 61 destinations on hard constraints (passports, each person's budget against
their real cost, maximum flight time). It scores the rest on five weighted factors, including
coverage for the least-served traveler. It re-ranks the top five on Open-Meteo weather for the
actual dates and prices live flights and hotels through SerpApi. Claude Opus 5.5 handles the
two language tasks. It converts free-text notes ("I use a wheelchair") into constraints, with
structured output limited to tags the planner recognizes. It also writes the itinerary, which
gets three drafts to pass validation before a template takes over. Every external call has a
fallback. Built for a UChicago AI program in winter 2026 and since deployed at
grouptrip-planner.vercel.app, with 69 tests.

TypeScript, React, Vite, Vercel Functions, Neon Postgres, Claude API, Open-Meteo, SerpApi.