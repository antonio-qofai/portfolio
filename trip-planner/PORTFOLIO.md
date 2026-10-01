The first agent I built, for the University of Chicago AI integration program in winter 2026,
since refined into a live product. A group shares one link and everyone answers a short form
on their phone. A deterministic planner picks the dates and the destination that works for the
most people, using real weather for those dates, estimated flight times, and live flight and
hotel prices from each person's own airport. Claude reads everyone's free-text notes ("I use a
wheelchair", "my passport is expired") into constraints the planner enforces, and writes the
itinerary, which must pass the plan's checks or go back for another draft. Every outside call
has a fallback, so a plan always comes back. Live at grouptrip-planner.vercel.app, with 69
tests.

TypeScript, React, Vite, Vercel Functions, Neon Postgres, Claude API, Open-Meteo, SerpApi.
