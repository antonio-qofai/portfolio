# Antonio Rodriguez Diaz

I build AI agents that do real work: they pull from live systems, call Claude where judgment is
needed, and check their own output before a person sees it. I study economics and physics at
the University of Chicago (class of 2028), and in 2026 I built agents at QofAI, an
AI company for middle-market private equity.

Every folder here is a published copy of a project that runs. Some are personal tools I use
every day, and some are agents I built during the internship.

## Start here

[deck-generator](deck-generator/) is my largest project. It writes the proposal and status
decks QofAI brings to a client, from the company's internal data or an uploaded PRD. Most of
the code makes sure a deck never states something its sources do not, and about 2,400 tests
hold it to that.

![The deck generator's review studio, showing a generated status deck](deck-generator/docs/screenshots/review-studio.png)

## Projects

<!-- projects:start -->

### [deck-generator](deck-generator/)

Built during my internship at QofAI, this agent writes the proposal and status check-in
decks QofAI brings to a client engagement. It pulls a project's data from the company's
internal platform over MCP, or parses an uploaded PRD or research paper, and maps it onto a
typed slide spec. Then it renders an HTML deck through Claude and runs it through a stack of
guards before a person sees it. The guards check that every needed value is either sourced
or visibly marked missing, that no number, date or name changes during style edits, that
nothing clips or overlaps on the slide, and that commercial figures come from the source and
not from the model. Reviewers work in a hosted studio where they toggle bullets, request
edits in plain language with undo, fill in missing values and export to PDF. It has about
2,400 tests. This copy is sanitized: clients, people and deal figures are replaced with
fictional ones.

Python, Claude API, MCP, Flask, Playwright, pypdf and python-docx, SQLite, Railway.

### [internship-search](internship-search/)

An agent that watches about 175 company job boards and three aggregator feeds on a schedule,
stores every posting in SQLite, works out what is new and what has closed, and filters out
anything that does not apply. Survivors go through a staged LLM ranker. A cheap model
scores every posting against a rubric with fixed anchors, and only the strongest go to a
larger model. The results reach me as a daily email digest, an Airtable base I label, and a
local dashboard. My labels feed back into the ranker as few-shot examples. It also has
mutation-tested health alerting and runs itself on a launchd schedule.

Python, SQLite, Claude API, Airtable API, Greenhouse/Lever/Ashby/Workday fetchers, launchd.

### [trip-planner](trip-planner/)

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

### [apartment-chores](apartment-chores/)

A rotation scheduler for a shared apartment. Every roommate does every chore the same number
of times over a quarter. It emails a weekly digest and sends private nudges when a chore is
overdue, and it reads the landlord's emails with an LLM to pick up cleaner visits. Proposed
dates stay pending until a person confirms them. The whole setup (chores, people, rules) lives
in Airtable rows, so pointing it at a different apartment means editing data, not code. It runs
on GitHub Actions, with 389 tests.

Python, Airtable API, Gmail SMTP/IMAP, Claude API, GitHub Actions.

### [life-dashboard](life-dashboard/)

A personal morning brief. One page that pulls news, weather, inboxes, Google Calendar, chores
and job search. An LLM triages email and ranks a short list of pressing actions. Work items
stay in their own section and never mix with personal ones. It runs locally and is read-only.
Still in progress.

Python, uv, Google Calendar and Gmail APIs (read-only OAuth), Open-Meteo, Claude API.

<!-- projects:end -->

## How this repo is maintained

Each project lives in its own private repo. [`tools/sync.py`](tools/sync.py) runs after every
commit to any of them. It exports the project's main branch, removes private files (personal
configuration, application history, planning notes), and replaces personal details with
example values. Two gates follow. A scanner checks for credentials, email addresses and known
identifying text, and then Claude reviews exactly what changed for personal or confidential
details the rules did not anticipate. If either gate finds anything, nothing is published.
Each project's entry above comes from a short `PORTFOLIO.md` in its own repo, so the
descriptions update with the projects. What to redact lives in a private file on
my machine, because a public list of what is being hidden would reveal it.

The QofAI projects are the exception. They are published with a cofounder's approval, and they
sync only by hand after I review each change, never on commit. Their descriptions and READMEs
live in `tools/overlays/`, and anything built from real client or company material is replaced
with synthetic stand-ins or left out.

A few documents are left out for privacy, including the internship agent's PRD and changelog.
Some links inside the project READMEs point to those files and will not resolve here.
