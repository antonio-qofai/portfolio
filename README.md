# Antonio Rodriguez Diaz

I love building AI agents. I study physics and
economics at the University of Chicago (class of 2028). In summer 2026 I built production agents
at QofAI, which sells AI agents to middle-market private equity firms.

Every folder is a published copy of a working project. 

[LinkedIn](https://www.linkedin.com/in/antonio-rodriguez-diaz-76115632a)

## Projects

<!-- projects:start -->

### QofAI internship

#### [qofai-deck-generator](qofai-deck-generator/)

Generates the proposal and status decks QofAI presents to clients, and gives reviewers a studio
to finish them without touching HTML. Built so founders could have an in-house alternative to
Claude Design. Data comes from QofAI's internal platform over MCP or from an uploaded PRD or
research paper, and Claude Opus renders the deck from a typed slide spec. Seven deterministic
guards check it first. Every value must be sourced or marked missing, no number or name may
change during style passes, nothing may clip in headless Chrome, and commercial figures are
rebuilt from source.

The review studio is the center of the project. A reviewer types an edit in plain language,
Claude Opus translates it into exact text swaps on named slides, and deterministic code
applies them, so the rest of the deck stays byte-for-byte identical and a request the model
cannot pin down changes nothing. One value typed into a missing-value card fills every place
it belongs, such as a date repeated across six footers. Reviewers can switch individual
bullets on or off, enter conservative, base and optimistic commercial terms in one form,
confirm or send back flagged claims, and save formatting edits as standing preferences for
future decks (content edits never carry over). Every change is a logged revision with
one-click undo, and decks export to PDF. About 29,000 lines of application code and 2,434
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
assembly or audit. In an eval on 50 labeled claims, adding a one-line source summary per
claim raised the API labeler from 25 of 50 correct to 39 of 49 scored (one failed on an API
overload), against 43 of 50 for a human scorer. Internship pre-work.

Python, Claude Code subagents, Claude API.

### Personal projects

#### [internship-search](internship-search/)

Tracks internship postings across 180 company job boards (Greenhouse, Lever, Ashby, Workday)
and three aggregator feeds, four times a day. As of October 2026 it has tracked 40,659
postings. A rules filter defined as data cuts the 27,968 open ones to 2,485 (8.9%). Claude
Haiku 4.5 reads the term and weekly hours once from each new posting that passes the first
rules and that no feed has already labelled. Feeds carry only a link, so the agent reads
each link's shape to find the Greenhouse, Lever or Ashby job behind it and fetches the
description before ranking, re-scoring anything already judged without it. A two-stage ranker then scores survivors against a
fixed-anchor rubric. Haiku scores every posting, and only those above a routing threshold go to
Claude Sonnet 5. Each prompt carries all 53 of my Airtable labels as few-shot examples, which
also pushed the prompt past Haiku's caching threshold, so every call after the first in a run
reads it at a tenth of the input price. A hard per-run call cap bounds spending. Output goes to
a daily email, an Airtable base and a local dashboard, on a launchd schedule with health
alerts. About 11,000 lines of Python including tests, and 510 checks.

Python, SQLite, Claude API, Airtable API, launchd.

#### [trip-planner](trip-planner/)

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

#### [apartment-chores](apartment-chores/)

Assigns chores in a shared apartment on a rotation that balances every chore across roommates
over a quarter, sends a weekly digest, and privately nudges anyone overdue. Claude Sonnet 5
reads the landlord's emails into proposed cleaner visits through structured output, and plain
code verifies each one. The quoted sentence must appear in the email, the date must fall in
range, and any named weekday must match the date. A visit whose date fails a check becomes an
undated request for a person to fill in, and nothing takes effect until a roommate confirms
it. All configuration lives in Airtable. Runs on GitHub Actions, with over 400 tests.

Python, Airtable API, Gmail SMTP/IMAP, Claude API, GitHub Actions.

#### [life-dashboard](life-dashboard/)

A personal morning brief that builds one local page at 6 AM and emails a digest at 7. Live
sources: Open-Meteo weather, five Google calendars including Canvas due dates, two Gmail
inboxes, chore and internship reports from my other agents, NYT, and the AI Daily Brief. Claude
Haiku 4.5 triages email in batches of 10. Claude Opus 5.5 rates each upcoming event's lead
time, then ranks up to seven Pressing actions using my feedback. Code rejects non-read-only
Google scopes, fetches email metadata and never bodies, and falls back to rules when a model
call fails. Work meetings reach only the lead-time call, never the ranking. 126 offline tests.

Python 3.12, uv, launchd, Gmail and Google Calendar APIs, Open-Meteo, NYT API, Claude API
(Haiku 4.5, Opus 5.5), Tailscale.

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

A few documents are left out for privacy, including the PRDs and changelogs of
internship-search and the QofAI projects. Some links inside the project READMEs point to those
files and will not resolve here.
