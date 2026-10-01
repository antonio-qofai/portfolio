# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Build retries.

### Fixed

- A connector that fails is retried once, 10 seconds after the others finish. On Sep 30 the Mac went back to sleep 14 seconds into the 6:00 build (a maintenance wake on battery, lid closed); email and AI Daily Brief had connections open across the 16-minute sleep and failed when it resumed, while the connectors that started after it succeeded.
- `--catch-up` rebuilds when today's brief has failed connectors, up to `schedule.retries` (3) times a day, logged as `retry` in runs.log. Before, it saw today's brief and skipped, so the failed cards stayed stale until a manual refresh. The cap keeps a source that's really down from costing a ranking call every 30 minutes. The digest still waits only for the scheduled build.
- Tests for both.

QofAI section.

### Added

- The QofAI card lists this week's QofAI meetings instead of only today's. The lead-time call now rates QofAI meetings with the personal ones (title and time only; the prompt says what makes a work meeting need prep), and meetings inside their lead time get a "Prep" line. QofAI results stay off Coming up, Pressing actions and the digest (`qofai_prep` in brief.json).
- A quick-add box on the QofAI card for work to-dos, in place of reading Slack. A trailing date ("by Thu", "tomorrow", "Oct 3", "10/3") becomes the due date; a to-do due on a meeting day names the meeting; overdue ones are flagged. Stored in `data/qofai_todos.json` through `GET /api/qofai`, `POST /api/qofai/add` and `POST /api/qofai/done` (same-origin, like the other endpoints). Checked-off to-dos stay visible, struck through, until the next day.
- Tests for the prep split, the card, date parsing, the store and the endpoints.
- The Canvas calendar feed is subscribed in Google Calendar and mapped in config.yaml. It has no assignments yet.
- Recurring coursework rules (`coursework` in config.yaml, `connectors/coursework.py`) for deadlines no calendar holds: Money & Banking homework due Wednesdays from Oct 7, with a reminder on Tuesday, my target day (`finish_early_days`; overdue on Wednesday until done), and the Colonizations reading before each class. They become Canvas-style deadline items from the calendar connector, next occurrence only, so they get lead times and can rank in Pressing actions. Economic Policy Analysis homework is on Gradescope; its rule waits for the due day.

### Changed

- PRD: the QofAI module, privacy note and interactions describe the week view, prep notes and to-dos. Slack stays unread.

Refresh now.

### Added

- A refresh button in the header (the PRD's "refresh now"). It starts `run.py --trigger refresh` as a child process through `POST /api/refresh`, same-origin only like the other endpoints, one build at a time, killed after 5 minutes. The page polls `GET /api/refresh`, then reloads, or says the refresh failed and keeps the last brief. runs.log labels these builds `refresh`.
- Tests for the refresher, the endpoint, and the greeting.

### Changed

- The greeting follows the time of day (morning, afternoon, evening), by the reader's clock.
- The header shows the next event that hasn't started ("Next up") instead of the day's first one, which had often already happened by an afternoon refresh.

M8 Reading, NYT half (live).

### Added

- NYT connector on the Top Stories API (`NYT_API_KEY` in `.env`): the home feed plus `news.sections` (business, upshot), three calls per build. Up to `news.cap` stories in all, chosen by rule, no model call.
- Stories for my macro classes lead the list, up to `news.courses.cap`: a story from the last 48 hours qualifies when one of its first 3 NYT topic tags is on a course's list in config.yaml (Fed, rates, inflation, banking and currency for Money & Banking; the economy, tariffs and trade, taxes, the budget and labor for Econ Policy). Courses take turns so one busy topic can't fill the list. The Reading card shows them under "For your classes" with the course name.
- Tests for class matching, taking turns, the cap, the age cutoff, duplicates across sections, and a missing key or HTTP error. Pipeline tests use a fake.

Page redesign.

### Changed

- New look: a UChicago maroon header with the date, greeting, weather and first event; white cards with small uppercase titles and item counts; a serif greeting; full dark mode.
- Less text. "Updated" shows once in the header instead of on every card, and cards show only connector errors. Links are no longer underlined. Calendar rows show the time in a left column without the calendar name. Chores show a short due date. The "Everything else" inbox list shows subjects only. The QofAI card collapses to one line when empty.
- Pressing actions use round check-offs, and the feedback buttons sit behind a "⋯" toggle per row. Save messages appear as a brief toast.
- The actions card no longer repeats every connector's error, only the ranking note.
- QofAI and Job search sit side by side on wide screens, as do NYT and the AI Daily Brief. Long summaries clamp to two lines.
- PRD: the brief's build time shows once in the header; a card shows a time only when its connector failed and it falls back to older data.

M8 Reading, AI Daily Brief half (live). NYT waits on an API key.

### Added

- AI Daily Brief connector: the latest episode from the site's agent feed, trying today's edition (`/e/<today>.json`) first because the feed can lag a day. Up to `ai_daily_brief.cap` items chosen by rule, no model call: the episode's thesis, then the headlines roundup, then main-segment insights in episode order; quotes and takes are skipped. Summaries are trimmed to whole sentences, about two lines. Each item links to the episode page.
- The Reading card names the episode day ("AI Daily Brief · Sun Sep 27"), so an older episode is obvious.
- Tests for item selection, the cap, the feed fallback, and HTTP errors. Pipeline tests use a fake.

M7 Job search (built; check pending).

### Added

- Job search card from the internship agent's report file (`agent-reports/internship.json`). Its new read-only exporter (`tools/report.py` in `~/agents/internship-search`, committed there) writes it at the end of each of that agent's four daily runs: offers, interviews and events this week, deadlines on roles marked interested and not applied, status changes in the last 36 hours, and one line for applications with no reply in 21+ days. Missing, stale or error reports are errors, as with chores.
- `connectors/_report.py`: the shared agent report reader for chores and job search.
- Tests for the job search connector and for check-offs surviving a regenerated report.

### Changed

- Job search rejoins Pressing actions, and the ranking prompt says what its items are.
- PRD: M7 reads a report file, not the internship agent's email (suppressed on quiet days, sent at 18:10, redesigned often). Recruiter replies are out of scope; the agent doesn't track them.
- The Job search card shows urgency with the due date, and "Nothing new." when empty.

### Fixed

- Agent report items took the report's `generated_at` as their timestamp. That is part of the check-off key, so a checked-off chore came back the next day when the report was regenerated, and the ranking prompt showed the export time as the chore's start. Report items now have no timestamp.

Chores card shows titles only.

### Changed

- The Chores card no longer shows each chore's description (the chore agent's full definition of done); title, urgency and due date stay.
- The ranking prompt no longer gets chore descriptions either; it ranks chores by title, due date and urgency.

M6 Phone, Tailscale access (live; morning check pending).

### Added

- Phone access through Tailscale: `tailscale serve` proxies the tailnet-only HTTPS address to the server on 127.0.0.1:8000. The server stays bound to localhost.
- The server accepts check-offs and feedback from the Tailscale origin, read from `DASHBOARD_URL` in `.env` so the tailnet name stays out of committed files. Other hosts and origins are still refused.
- Tests for the Tailscale origin (right host and scheme only) and for building the allowed origins from `DASHBOARD_URL`.

M6 Phone, email digest (live since Sep 28; the Gmail app password is in `.env`).

### Added

- Morning digest (`dashboard/digest.py`, `run.py --digest`): a short plain-text email with Pressing actions and their why, today's personal events, chores, the weather line, any fallback or failed-card notes, and a link to the page (`DASHBOARD_URL`). QofAI items are never included. Read from `data/brief.json`, so it matches the page.
- Sent from `GMAIL_ADDRESS` to itself over Gmail SMTP with an app password (`GMAIL_APP_PASSWORD`). This is not an OAuth scope; Google access stays read-only.
- `digest` in config.yaml (7:00, window until 12:00) and a third launchd job that runs at 7:00, at login and every 15 minutes. It sends once a day, only after the day's brief exists, never builds itself, and retries on failure. `data/digests.log` records each attempt.
- `.env.example`: `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `DASHBOARD_URL`.
- `run.py --digest-test`: send the current brief now, marked [Test], without counting as the day's digest.
- Tests for the digest body, QofAI exclusion, the send window, once-a-day sending, retry after failure, missing credentials, and test sends.

### Fixed

- Scheduled builds ran right after the 5:55 wake, before Wi-Fi was back, so every network connector and both Claude calls failed DNS on Sep 26, 27 and 28. The build still wrote a brief, so catch-up considered the day done and never retried. A scheduled build now waits up to 2 minutes for DNS; if the network never comes, it writes no brief and logs `skipped=no_network` in `data/runs.log`, and the next 30-minute trigger tries again.

### Changed

- PRD: the digest is a short top-of-brief email with a link, not the whole brief; the page has everything. Privacy section notes the digest is the only thing that sends.

M5 Pressing actions (built; one-week check pending).

### Added

- LLM Pressing actions (`dashboard/actions.py`, `prompts/rank_actions.md`): Claude (`actions.model`, Opus 5.5, `actions.effort` high) ranks today's personal calendar events, pressing email, chores and surfaced upcoming events into at most `actions.cap` actions, each with a one-line why. QofAI items are never candidates. Structured JSON output; candidates and feedback are wrapped in tags and marked as data.
- Importance-based lead time (`prompts/lead_time.md`): Claude rates each personal event in the next `calendar.lookahead_days` (14) by title, calendar and start time, and picks how many days ahead it should surface. Events inside their lead time appear under Coming up on the Calendar card and become ranking candidates.
- Check-off and "too early / too late / not needed" buttons. The serve job gained `GET /api/state`, `POST /api/check` and `POST /api/feedback` (`dashboard/api.py`, stdlib only). They accept JSON from the page's own origin and host only, and only keys in the current brief; item details are copied from the brief, not the request. Stored in `data/checked.json` and `data/feedback.jsonl` (`dashboard/store.py`).
- Feedback loop: the last `actions.feedback_limit` (60) feedback entries go into both prompts. Checked-off and not-needed items stay out of Pressing actions until the item changes (its key covers source, title, due date and timestamp).
- Rule fallback when either call fails (no key, API error, cut-off output): items with urgency hints, most urgent first, and deadlines within 3 days under Coming up. The card says which step fell back.
- `calendar.lookahead_days` and `actions.model`, `actions.effort`, `actions.feedback_limit` in config.yaml.
- Tests for lead times, candidate selection, ranking validation, fallbacks, prompt wrapping, the store, the API, the page's buttons, and the server's origin checks. An autouse fixture keeps every test off the Claude API.

### Changed

- The calendar connector fetches today plus `calendar.lookahead_days`; the page still lists only today's events, and the header and QofAI card use today only. Canvas deadlines after today are marked `deadline` rather than `due_today`.
- The placeholder ranking in `dashboard/pipeline.py` is gone. `build_brief` takes a `data_dir` (check-offs, feedback, cache) and a Claude `client`, so tests use a tmp dir and a fake.
- Job search stays out of Pressing actions until M7; its stub items had ranked as real actions.
- Pipeline test fixtures use dates relative to today.
- CLAUDE.md's server and testing conventions cover the new endpoints, the tmp data_dir and the fake Claude client.

M4 Agent reports (code complete; waiting on the chore exporter's read-only Airtable token).

### Added

- The chore agent's repo gained a read-only exporter (`src/report.py`) and a launchd job (`scripts/report_launchd.py`) that write `agent-reports/chores.json` daily at 05:45. The chore agent runs on GitHub Actions, so it can't write here itself.
- `agent_report_max_age_hours` in config.yaml (30): an older report is an error, not current chores.
- Tests for the chores connector: fresh, missing, stale, agent error status, empty.

### Changed

- The chores connector no longer falls back to `chores.sample.json`; a missing report is an error. Pipeline tests use the sample through a fake connector.
- Chores card shows urgency and due date together.

M3 Email, UChicago inbox connected.

### Added

- UChicago Gmail inbox (`google_account: uchicago`, Gmail-only read-only token). UChicago's Google Workspace allows the app; the user accepted the policy question.
- Tests for batching, retrying skipped emails, per-email fallback, and own-address filtering.

### Changed

- Triage sends emails in batches of 10 and tells the model how many verdicts to return; Haiku had returned 10 of 28 on one long list. Emails it skips are retried once, and only those still missing fall back to the rules.
- Mail sent from any of the user's own inbox addresses (e.g. the internship agent's reports from personal Gmail to UChicago) is never flagged in the Inbox card; M7 reads those reports.

M3 Email, LLM triage (code complete; waiting on ANTHROPIC_API_KEY).

### Added

- LLM email triage (`dashboard/triage.py`, `prompts/email_triage.md`): Claude (`email.model`, Haiku 4.5) sees sender, subject, snippet and date for each Primary thread where the user didn't send the last message, and keeps only pressing school, work and internship email, with a one-line reason and any deadline. Structured JSON output; email text is wrapped in tags and marked as data.
- If triage fails (no key, API error, incomplete output), the rule-based flags stand and those items are marked "(AI filter unavailable)".
- `scripts/google_auth.py authorize <account>`: authorize a second Google account (Gmail only), for testing UChicago access.
- Dependency: anthropic.
- Tests for the triage prompt, output validation, verdicts, metadata-only input, and each fallback path.

### Changed

- Inbox card shows pressing email with why and due date ("Nothing pressing." when empty); everything else is collapsed.
- QofAI email is no longer planned: removed from config.yaml, the QofAI card and the PRD (QofAI communication happens in Slack). PRD updated for the triage step and the new M3 done-when.

M3 Email, personal Gmail only (live; 5-day accuracy check pending).

### Added

- Gmail connector (`gmail.readonly`): Primary-tab inbox threads from the last `email.lookback_days`, capped at `email.max_threads`, fetched as metadata only (sender, subject, snippet, never bodies). Promotions, Social, Updates and Forums are excluded from that query, so they can't crowd real mail out of the cap, and appear as one count line. Gmail's HTML-encoded snippets are decoded before the page escapes them. Rule-based reply-needed until M5: last message from someone else, not in a bulk Gmail tab, no list or auto-reply headers, no no-reply sender. Links open the thread in the right Gmail account.
- `email` section in config.yaml; an inbox is read only when it names a `google_account`. Only the personal inbox does; UChicago and QofAI wait on their policy checks.
- Tests for reply-needed rules, drafts, automated-mail detection, skipped inboxes, and the Inbox card. Pipeline tests use a fake email connector.

### Changed

- `scripts/google_auth.py authorize` requests `calendar.readonly` and `gmail.readonly`.
- The Inbox card no longer shows placeholder emails for unconnected inboxes.

### Fixed

- `scripts/launchd.py install` retries `bootstrap` for up to 10 seconds, since `bootout` finishes asynchronously and an immediate reload failed with error 5.

M2 Calendar (code complete; waiting on Google OAuth setup).

### Added

- Google Calendar connector: today's events from each calendar mapped in config.yaml, in the configured timezone. Skips cancelled, declined, working-location, out-of-office and focus-time events. Calendars with `kind: deadlines` (Canvas) mark each event as due today.
- `dashboard/google_auth.py`: read-only OAuth with tokens in `data/tokens/` (mode 600), automatic refresh, and refusal of any token carrying a non-read-only scope.
- `scripts/google_auth.py authorize` (browser consent) and `calendars` (list IDs to map in config.yaml).
- `.env` loader; `google_account` and per-calendar `google_id` in config.yaml.
- Dependencies: google-auth, google-auth-oauthlib, requests.
- Page shows all-day events as "All day" and due dates as "due Sep 27, 11:59 PM"; the header's first event skips all-day events.
- Tests for event parsing and filtering, the day window, read-only scope enforcement, and token handling.
- Calendars mapped: personal (primary), UChicago and QofAI (both shared into the personal account with full details), and Family. Holidays and the internship agent's "Internship Deadlines" calendar are left out for now.
- Consent flow always shows Google's account chooser (`prompt=select_account consent`).
- OAuth app published "In production" so refresh tokens don't expire after 7 days; its home page and privacy policy are hosted on a separate public GitHub Pages repo (life-dashboard-site).

M1 Schedule + weather.

### Added

- Real weather from Open-Meteo for Chicago (no API key): current temperature, feels-like when it differs, high/low, rain chance, and a rule-based what-to-wear line.
- Last-good-result cache per connector in `data/cache/`. A failed fetch shows the cached data with its age next to the error.
- `run.py --catch-up` builds only if the most recent scheduled time was missed; `--no-build` serves without building.
- `data/runs.log` records every build (time, trigger, status) to track the 7:00 AM metric.
- `scripts/launchd.py` installs two launchd jobs: a build job (6:00 AM, login, wake, every 30 min, with catch-up) and an always-on localhost server.
- `schedule` section in config.yaml.
- Tests for weather parsing, what-to-wear rules, stale fallback, catch-up timing, and launchd job definitions.

### Changed

- Server sends `Cache-Control: no-store` so the page always shows the latest brief.
- Tests use a fake weather connector and a temporary cache so they never hit the network or the real `data/`.

## [0.1.0] - 2026-09-25

M0 Skeleton.

### Added

- Repo scaffold: README.md, CHANGELOG.md, CLAUDE.md alongside PRD.md.
- Python 3.12 project managed with uv (`pyproject.toml`, `.python-version`). Only runtime dependency is PyYAML.
- `config.yaml` with Chicago location, three inboxes and four calendars tagged personal or qofai, NYT sections, news cap 5, actions cap 7.
- Shared `Item` schema (source, title, summary, link, timestamp, due, urgency_hints, section) and the agent report contract with validation.
- Stub connectors: weather, calendar, email, chores, job_search, nyt, ai_daily_brief.
- Chores connector reads `agent-reports/chores.json`, falling back to the committed `chores.sample.json`.
- Pipeline that runs every connector, turns a connector failure into an error card, and applies a placeholder Pressing actions ranking (personal items only, capped).
- Static, phone-friendly page in PRD layout order (header, Pressing actions, Today, Inbox, QofAI, Job search, Reading) with per-card last-updated times; light and dark themes.
- `run.py` entry point with `--serve` for localhost.
- Placeholder prompts for action ranking and lead times.
- `.env.example` and `.gitignore` covering secrets, tokens, `data/`, generated page, and real agent reports.
- Tests for the end-to-end stub pipeline, error isolation, caps, QofAI exclusion, report contract, and HTML escaping.
