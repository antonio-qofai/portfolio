# life-dashboard

My morning brief. At 6 AM it builds one page from my calendars, inboxes, coursework, chores, job
search, weather and news, with a short ranked list of what to do today at the top. At 7 it
emails me a plain-text digest. It runs locally on my laptop, reads everything and changes
nothing.

This is a sanitized copy. Calendar IDs and addresses are replaced, and the README is written for
this public copy. The code is unchanged.

## How a build works

```
connectors/  weather, 5 Google calendars (Canvas due dates among them), 2 Gmail
             inboxes, recurring coursework rules, NYT, AI Daily Brief, and report
             files from my chore and internship-search agents
     │  each fails alone; a failed card shows its last good result and its age
     ▼
email triage       Claude Haiku 4.5, batches of 10, metadata only
lead times         Claude Opus 5.5 rates how early each upcoming event needs attention
Pressing actions   Claude Opus 5.5 ranks up to 7, each with a one-line reason
     │  any failed model call falls back to rules, and the card says so
     ▼
web/index.html, served on localhost, reachable from my phone over Tailscale
morning digest email
```

## Design decisions

Read-only by construction. `dashboard/google_auth.py` refuses any Google token carrying a scope
beyond read-only Calendar and Gmail, both when authorizing and when loading a saved token.

The model sees as little as possible. Email triage gets sender, subject and Gmail's snippet,
never message bodies. The calendar connector requests title, time, location and link, never
descriptions. Source text goes into prompts wrapped in tags with an instruction not to follow
anything inside it, and is HTML-escaped on the page.

Work stays separate. My QofAI calendar feeds the lead-time call, so work meetings get a
prep note, but no work item enters the Pressing actions ranking or the digest email.

It learns from feedback. Every action has a check-off box, and every action and upcoming event
has too early, too late and not needed buttons. The last few feedback entries go into both
prompts on the next build, and a dismissed item stays out until it changes (a new due date, a
new message in the thread).

One connector failing never breaks the page. The pipeline isolates each one, caches its last
good result, and the card shows the error with the cached data's age. A build retries when any
connector failed, up to a daily limit.

Other agents plug in through files. My chore and internship agents write a JSON report in a
shared contract (`dashboard/schema.py`), and a missing, stale or errored report shows as an
error rather than an old list.

## Scheduling

Three launchd jobs: the build (6 AM, on login, and on wake after a missed run), the digest (7 AM,
sent once a day, only after that day's build), and an always-on local server for the page and its
feedback endpoints. The server binds to 127.0.0.1 and accepts same-origin JSON only, for keys in
the current brief.

## Code

    run.py                 entry point: build, catch-up build, serve, digest
    connectors/            one module per source, each fetch(config) -> list[Item]
    dashboard/pipeline.py  runs connectors, isolates failures, caches last good results
    dashboard/triage.py    email triage
    dashboard/actions.py   lead times and Pressing actions, with rule fallbacks
    dashboard/digest.py    the morning email, the only code that sends
    dashboard/render.py    the page
    prompts/               the three prompts
    config.yaml            sources, caps, models and schedule

126 tests. They never touch the network or the real data folder, and a fake Claude client
covers the model paths:

    uv run pytest

## Running it

    uv run run.py --serve    # build the page and serve it at http://127.0.0.1:8000

Weather and the AI Daily Brief work with no setup. NYT needs an API key, Calendar and Gmail need
a Google OAuth client, and the model calls need `ANTHROPIC_API_KEY`, all in `.env` (see
`.env.example`).

Python 3.12, uv, launchd, Gmail and Google Calendar APIs, Open-Meteo, NYT API, Claude API
(Haiku 4.5, Opus 5.5), Tailscale.
