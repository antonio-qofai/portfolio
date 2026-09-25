# Life Dashboard

A personal morning brief. One page that pulls news, weather, two inboxes, Google Calendar, chores, job search, and a separate QofAI work section, with a short ranked list of Pressing actions at the top. It runs locally on my laptop and is read-only.

PRD.md is the source of truth for scope and design.

## Status

M1 (Schedule + weather) is running and being verified over several mornings. M2 (Calendar) code is in place and waiting on Google OAuth setup. Weather is real (Open-Meteo, no key); the calendar is real once authorized. Every other connector returns stub data.

## Run locally

Requires [uv](https://docs.astral.sh/uv/) (`brew install uv`). uv installs Python 3.12 and the dependencies on first run.

```sh
uv run run.py --serve     # build the page and serve it at http://127.0.0.1:8000
uv run run.py             # build only: writes data/brief.json and web/index.html
uv run pytest             # tests (no network)
```

Secrets go in `.env` (copy `.env.example`). Calendar and Gmail need `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.

## Google Calendar

One read-only Google connection (`calendar.readonly`) to the personal account. UChicago, QofAI and Canvas are calendars subscribed into that account, so no third-party app touches the UChicago or QofAI accounts.

1. Create an OAuth client (Desktop app) in a Google Cloud project with the Calendar API enabled, and put its ID and secret in `.env`. Set the app's publishing status to "In production"; in "Testing", Google expires refresh tokens after 7 days.
2. `uv run scripts/google_auth.py authorize` opens the browser for consent and saves the token to `data/tokens/google-personal.json` (gitignored, mode 600).
3. `uv run scripts/google_auth.py calendars` lists calendar IDs. Copy them into `google_id` in config.yaml.

The code refuses any Google token with a scope beyond read-only Calendar and Gmail.

## Gmail

The personal inbox uses the same token with `gmail.readonly` added. Enable the Gmail API in the same Cloud project, then re-run `uv run scripts/google_auth.py authorize` (the consent screen should list only read-only Calendar and Gmail access).

Only inboxes with a `google_account` in config.yaml are read: personal (the Calendar token) and UChicago (`uv run scripts/google_auth.py authorize uchicago`, a Gmail-only token; pick the UChicago account in the chooser and click past Google's unverified-app warning). QofAI email is not read, since QofAI works in Slack.

The connector reads Primary-tab inbox threads from the last `email.lookback_days` (at most `email.max_threads`) as metadata only (sender, subject, Gmail's snippet), never bodies. Promotions, Social, Updates and Forums are only counted, as one line.

Every thread where you didn't send the last message (and that didn't come from one of your own addresses, like your agents' reports) goes to Claude, in batches of 10, (`email.model`, prompt in `prompts/email_triage.md`), which keeps only pressing school, work and internship email and says why, with any deadline. It needs `ANTHROPIC_API_KEY` in `.env`. If the call fails, rules stand in (last message from a real person, no mailing-list or auto-reply headers, no no-reply sender) and those items are marked "(AI filter unavailable)".

## Scheduling

Two launchd jobs keep the brief fresh and the page up.

| Job | What it does |
| --- | --- |
| `...life-dashboard.build` | `run.py --catch-up` at 6:00 AM, at login, on wake after a missed 6:00, and every 30 min. Skips if today's brief already exists. |
| `...life-dashboard.serve` | `run.py --serve --no-build`, always on, localhost only. |

```sh
uv sync                                  # creates .venv, which launchd uses
uv run scripts/launchd.py install        # (re)install both jobs after changing config.yaml
uv run scripts/launchd.py status
uv run scripts/launchd.py uninstall
```

launchd only runs jobs while the Mac is awake. To wake it before the build, run this once (needs sudo, 5 minutes before `schedule` in config.yaml):

```sh
sudo pmset repeat wakeorpoweron MTWRFSU 05:55:00
pmset -g sched                           # confirm
```

Every build appends a line to `data/runs.log` (time, trigger, status); that log is how we check the "ready before 7:00 AM" metric. Job output goes to `data/logs/`.

When a connector fails, its card shows the error plus its last good result from `data/cache/`, marked with its age.

## Folder layout

```
PRD.md              product requirements (source of truth)
config.yaml         inboxes, calendars, location, news sections, caps, schedule
run.py              entry point: build, catch-up build, serve
scripts/launchd.py  install/uninstall/status for the launchd jobs
scripts/google_auth.py  Google consent flow and calendar ID listing
dashboard/
  schema.py         shared Item shape + agent report contract
  config.py         config and .env loader
  google_auth.py    read-only Google OAuth tokens
  pipeline.py       run connectors, isolate failures, cache last good results,
                    pick Pressing actions
  render.py         HTML page in PRD layout order
connectors/         one module per source, each `fetch(config) -> list[Item]`
agent-reports/      report files written by my other agents (real ones gitignored)
prompts/            LLM prompts (placeholders until M5)
web/                page template; generated index.html is gitignored
data/               local store, gitignored: brief.json, cache/, logs/, runs.log, tokens/
tests/
```

## Adding a source

Write `connectors/<source>.py` with a `fetch(config)` that returns a list of `Item`, register it in `connectors/__init__.py`, and place its items in a card in `dashboard/render.py`.

Agents that already exist plug in without a connector change by writing `agent-reports/<agent>.json` in the contract format (`generated_at`, `status`, `items[]` with `title`, `summary`, `due`, `urgency`, `link`). See `agent-reports/chores.sample.json`.

The chore agent runs on GitHub Actions, so its exporter runs here instead: `~/agents/chores/src/report.py`, scheduled by `python3 scripts/report_launchd.py install --out ~/agents/life-dashboard/agent-reports/chores.json` in that repo (setup in its README, "Report exporter"). It runs at 05:45, before the 6:00 build. A missing report, one older than `agent_report_max_age_hours`, or one with `"status": "error"` shows as an error on the Chores card; the sample file is only used by tests.

## Roadmap

From PRD.md, one milestone at a time.

| Milestone | Scope |
| --- | --- |
| M0 Skeleton | Scaffold, config, static page with placeholder cards (done) |
| M1 Schedule + weather | Weather connector, 6:00 AM wake + catch-up run (in progress) |
| M2 Calendar | Google Calendar, read-only; UChicago, QofAI, Canvas feeds (in progress) |
| M3 Email | Personal Gmail and UChicago, then QofAI after policy check |
| M4 Agent reports | Chore agent writes the report file |
| M5 Pressing actions | LLM ranking, lead times, check-off and feedback buttons |
| M6 Phone | 7:00 AM email digest, then Tailscale |
| M7 Job search | Parse the internship agent's report email |
| M8 Reading | NYT (cap 5) and AI Daily Brief cards |
| M9 Actions (v2) | Draft replies and add events, with approval each time |
