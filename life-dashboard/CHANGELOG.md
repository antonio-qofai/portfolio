# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
