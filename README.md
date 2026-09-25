# Antonio Rodriguez Diaz, projects

Agents and tools I have designed and built, mostly with Claude Code. Each folder is a
published snapshot of a working project that I use every day. The folders update
automatically when I commit to the original repos, so what you see here is the current state
of each project, not a frozen copy.

Economics and physics at the University of Chicago, class of 2028.

## Projects

### [internship-search](internship-search/)

An agent that watches about 175 company job boards and two aggregator feeds on a schedule,
stores every posting in SQLite, works out what is new and what has closed, and filters out
anything that does not apply. Survivors go through a staged LLM ranker. A cheap model
scores every posting against a rubric with fixed anchors, and only the strongest go to a
larger model. The results reach me as a daily email digest, an Airtable base I label, and a
local dashboard. My labels feed back into the ranker as few-shot examples. It also has
mutation-tested health alerting and runs itself on a launchd schedule.

Python, SQLite, Claude API, Airtable API, Greenhouse/Lever/Ashby/Workday fetchers, launchd.

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

### QofAI

Agentic systems built during my internship at QofAI, which builds AI for middle-market private
equity. Coming soon, with client and internal details removed.

## How this repo is maintained

Each project lives in its own private repo. [`tools/sync.py`](tools/sync.py) runs after every
commit to any of them. It exports the committed code, removes private files (personal
configuration, application history, planning notes), replaces personal details with example
values, and scans the result for credentials, email addresses and other identifying text.
If the scan finds anything, nothing is published. What to redact lives in a private file on
my machine, because a public list of what is being hidden would reveal it.

A few documents are left out for privacy, including the internship agent's PRD and changelog.
Some links inside the project READMEs point to those files and will not resolve here.
