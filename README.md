# Antonio Rodriguez Diaz

I build AI agents that pull from live systems, use Claude where judgment is needed, and check
their own output with deterministic code before a person sees it. I study economics and
physics at the University of Chicago (class of 2028). In summer 2026 I built production agents
at QofAI, which sells AI agents to middle-market private equity firms.

Every folder is a published copy of a working project. The deck generator is the largest.

[LinkedIn](https://www.linkedin.com/in/antonio-rodriguez-diaz-76115632a)

## Projects

<!-- projects:start -->

### QofAI internship

#### [qofai-deck-generator](qofai-deck-generator/)

Generates the proposal and status decks QofAI presents to clients, and gives reviewers a
studio to finish them without touching HTML. Data comes from QofAI's internal platform over
MCP or from an uploaded PRD or research paper, and Claude Opus renders the deck from a typed
slide spec. Seven deterministic guards check it first. Every value must be sourced or marked
missing, no number or name may change during style passes, nothing may clip in headless Chrome, and commercial
figures are rebuilt from source.

The review studio is the center of the project. A reviewer types an edit in plain language,
Claude Opus translates it into exact text swaps on named slides, and deterministic code
applies them, so the rest of the deck stays byte-for-byte identical and a request the model
cannot pin down changes nothing. One value typed into a missing-value card fills every place
it belongs, such as a date repeated across six footers. Reviewers can switch individual
bullets on or off, enter conservative, base and optimistic commercial terms in one form,
confirm or send back flagged claims, and save formatting edits as standing preferences for
future decks (content edits never carry over). Every change is a logged revision with
one-click undo, and decks export to PDF. About 20,000 lines of application code and 2,447
tests. Sanitized with fictional clients and figures.

![The deck generator's review studio, showing a generated status deck](/qofai-deck-generator/docs/screenshots/review-studio.png)

Python, Claude API, MCP, Flask, Playwright, SQLite, Railway.

#### [qofai-content-calendar](qofai-content-calendar/)

Schedules the founders' LinkedIn posts from the output of QofAI's other content agents. A
daily job ingests five sources (two upstream content agents, an Airtable conference tracker,
an RSS news scan and post engagement from the internal portal) and flags any that go quiet or
arrive late. Once a month a single Claude Opus 5 call at high effort sequences the posts into
a calendar per founder, returning schema-constrained JSON under a policy defined entirely in
YAML. Before saving, every slot's thread, form and lens tags are checked against the content
taxonomy. Each calendar is re-checked weekly and repaired on failure. Founders approve,
decline, swap and reschedule on a served page through one approval queue, and the reason
required on every decline feeds the next run. The agent never posts. Taken over from a
teammate and largely rebuilt, with 173 tests.

Python, Claude API, Flask, Airtable REST API, RSS, PyYAML, GitHub Actions, Railway.

#### [qofai-pe-research-agent](qofai-pe-research-agent/)

Writes a sourced dossier on a private equity firm: its profile, its portfolio companies,
revenue and EBITDA estimates for the private ones from public comparables, and recent
activity. Eight skills with written contracts run under Claude Code on Sonnet 4.6, with one
parallel subagent per portfolio company and a final audit pass that critiques the dossier.
Every claim carries a confidence band and a source type, and an interrupted run resumes from
assembly or audit. In an eval on 50 labeled claims, the API labeler rose from 50% to 79.6%
accuracy after adding a one-line source summary per claim, which closed 83% of the gap to a
human scorer at 86%. Internship pre-work.

Python, Claude Code subagents, Claude API.

### Personal projects

#### [internship-search](internship-search/)

An agent that watches about 175 company job boards and three aggregator feeds on a schedule,
stores every posting in SQLite, works out what is new and what has closed, and filters out
anything that does not apply. Survivors go through a staged LLM ranker. A cheap model
scores every posting against a rubric with fixed anchors, and only the strongest go to a
larger model. The results reach me as a daily email digest, an Airtable base I label, and a
local dashboard. My labels feed back into the ranker as few-shot examples. It also has
mutation-tested health alerting and runs itself on a launchd schedule.

Python, SQLite, Claude API, Airtable API, Greenhouse/Lever/Ashby/Workday fetchers, launchd.

#### [trip-planner](trip-planner/)

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

#### [apartment-chores](apartment-chores/)

A rotation scheduler for a shared apartment. Every roommate does every chore the same number
of times over a quarter. It emails a weekly digest and sends private nudges when a chore is
overdue, and it reads the landlord's emails with an LLM to pick up cleaner visits. Proposed
dates stay pending until a person confirms them. The whole setup (chores, people, rules) lives
in Airtable rows, so pointing it at a different apartment means editing data, not code. It runs
on GitHub Actions, with 389 tests.

Python, Airtable API, Gmail SMTP/IMAP, Claude API, GitHub Actions.

#### [life-dashboard](life-dashboard/)

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
