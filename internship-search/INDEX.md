# Index

A map of this repo. One line per file, and a table at the top for the question that actually
gets asked, which is "where do I change this".

How to run any of it is in `README.md`. What is being built and why is in `PRD.md`.

## Where to change something

| To change | Edit | Then run |
| --- | --- | --- |
| How postings are scored, tier boundaries, which model a posting is worth | `rubric.md` | `python -m tools.rubric_check` |
| What gets filtered out before scoring: titles, locations, terms, hours | `sources/prefilter.toml` | `tools.prefilter_report --dry-run`, then `--reapply`, then `tools.test_prefilter` |
| Which companies are polled, and their ATS tokens | `sources/companies.toml` | `python -m tools.verify_tokens` |
| How Workday boards are asked for their interns | `sources/companies.toml`, `[workday]` | `python -m tools.test_workday`, then `tools.probe_workday <url>` |
| Which aggregator feeds are pulled | `sources/feeds.toml` | `python -m agent.run --skip-triage` |
| What Stage 0 reads off a posting | `sources/intake_prompt.md` | `python -m agent.run` |
| When the agent runs | `sources/schedule.toml` | `python -m tools.schedule --install` |
| When the agent emails you that it is broken | `sources/schedule.toml`, `[health]` | `python -m tools.test_health` |
| Which email a tier goes to | `rubric.md`, `delivery` on the tier band | `python -m tools.rubric_check`, then `tools.test_digest` |
| Email item caps, the urgent triggers, the roundup day, collapsed locations | `sources/email.toml` | `python -m tools.test_digest` |
| The YOUR MOVE block: its cap, what counts as waiting, when an application is called silent | `sources/email.toml`, `[actions]` | `python -m tools.test_actions` |
| Referral contacts | `sources/contacts.toml` | `python -m agent.run` |
| The Airtable base layout | `sources/airtable.toml` | `python -m tools.sync_airtable` |
| Application windows on the calendar | `sources/cycle_windows.toml` | `python -m tools.sync_calendar` |
| Anything about credentials | `.env` | nothing, it is read on the next run |

No company name, city, job title, term, hour count or scoring rule belongs in a `.py` file.
That is CLAUDE.md rules 2 and 3, and it is the rule most worth defending.

## Documents

| File | What it is |
| --- | --- |
| `PRD.md` | The specification of record. The whole plan, the architecture, the milestone order. |
| `CLAUDE.md` | Standing instructions for any agent working in this repo. |
| `README.md` | How to run everything, from setup to reading the digest. Written for a stranger. |
| `CHANGELOG.md` | Dated log of what changed and why, including fixes and reversals. |
| `NEXT_STEPS.md` | Short and forward-looking. What is blocked on the owner, what gets built next. |
| `rubric.md` | The scoring rubric, authoritative at runtime. The owner's most-edited file once ranking is live. |
| `INDEX.md` | This file. |
| `docs/calendar-setup.md` | The one-time Google Calendar OAuth setup. |
| `sources/needs-other-approach.md` | Target companies no board and no feed reaches, and why. |

## Data, in `sources/`

Everything here is hand-edited data that code reads. Nothing here is generated.

| File | What it holds |
| --- | --- |
| `companies.toml` | The token map: every company polled, its ATS platform and its token. |
| `feeds.toml` | The aggregator feeds and how to read each one's fields. |
| `prefilter.toml` | Every title, place, term and phrase the Stage A prefilter matches on. |
| `intake_prompt.md` | The Stage 0 tagger's instruction text, sent to the model verbatim. |
| `schedule.toml` | The run times, the launchd labels, the log paths, and the three steps a run consists of. |
| `email.toml` | What each email carries: item caps, the two urgent triggers, the roundup day and window. Not which tier goes where; that is `rubric.md`. |
| `airtable.toml` | The Airtable base described as data: tables, fields, and the SQLite column behind each. |
| `contacts.toml` | Referral contacts, by company. |
| `cycle_windows.toml` | Recruiting windows pushed to Google Calendar. |
| `resources.toml` | Preparation resources and the concrete work each one asks for. |
| `needs-other-approach.md` | Companies that need a hand-check, with the reason for each. |

## The agent, in `agent/`

The pipeline, in the order a posting moves through it.

| Module | What it does |
| --- | --- |
| `config.py` | Paths, environment and tunables. The only module that reads `os.environ`. |
| `sources.py` | Loads the company token map. |
| `fetchers.py` | Layer 1 watchers. One fetcher per ATS, all returning the same posting record. Owns what makes two sightings the same posting, and the Workday facet query. |
| `feeds.py` | Layer 1 watchers, aggregator half. Same contract as the fetchers. |
| `db.py` | SQLite. The source of truth for posting state, plus the schema and its migrations. |
| `triage.py` | Runs Stage A, the description fetch and Stage 0 over the open postings. Orchestration only. |
| `prefilter.py` | Stage A. The deterministic prefilter. No LLM, and no rule of its own. |
| `tagger.py` | Stage 0. One small model call per posting, once, reading term, hours and deadline. |
| `rubric.py` | Reads `rubric.md`: the prompt text for the ranker and the settings applied after it. |
| `ranker.py` | Stage B and Stage C. Scores fit and reach, derives tier. Holds no scoring rule. |
| `contacts.py` | Referral lookup by company. Called by every email since Milestone 7. |
| `delivery.py` | Which email a posting belongs in and when each is due. Reads `rubric.md` and `sources/email.toml`. |
| `notify.py` | The three emails, written and sent over SMTP. Decides wording, never policy. |
| `grouping.py` | Collapses one role posted across many cities. Presentation only, never identity. |
| `airtable.py` | Airtable client and schema bootstrap. |
| `airtable_sync.py` | Two-way sync between SQLite and the base. |
| `gcal.py` | Google Calendar writes, to a dedicated calendar and never the primary one. |
| `prep.py` | Preparation tasks: definitions from TOML, completion state from SQLite. |
| `schedule.py` | Reads `sources/schedule.toml` and generates the launchd plists. |
| `health.py` | Whether the agent is alive, and the email when it is not. Depends on almost nothing, on purpose. |
| `run.py` | One polling cycle. The entry point: `python -m agent.run`. |

Built 2026-08-12: the ranker, Milestone 6. `agent/ranker.py` reads `agent/rubric.py` and follows
the call pattern in `agent/tagger.py`. It contains no criterion, anchor or threshold of its own.

## Tools, in `tools/`

Things run by hand or by the scheduler, never part of the polling cycle.

| Tool | What it does |
| --- | --- |
| `scheduled_run.py` | The one program launchd starts. Poll, then sync Airtable, then sync the calendar. |
| `schedule.py` | Installs, removes and inspects the launchd agents. `--dry-run` writes nothing. |
| `rubric_check.py` | Parses `rubric.md` and prints what the ranker will apply. Read-only. |
| `rank_report.py` | What the ranker would do and what it decided. Read-only unless `--run`. |
| `test_prefilter.py` | 99 cases the prefilter must get right. No framework, no database. |
| `test_identity.py` | 9 cases on what makes two sightings the same posting. No framework, no database. |
| `test_digest.py` | 43 cases on who gets which email, the caps, the collapse and the urgent triggers. |
| `test_health.py` | 13 cases on the failure alert. Mutation-tested, because a silent monitor looks healthy. |
| `test_actions.py` | 33 cases on the YOUR MOVE block, the applied date and the owner's own closure. |
| `test_workday.py` | 23 cases on the Workday fetcher, against a fake board. No network. |
| `probe_workday.py` | Is a Workday board addable? Prints the employer's own job types and answers outright. |
| `health.py` | Is the agent alive? Reads the heartbeat and the run log. Exits non-zero when something is wrong. |
| `reconcile_identity.py` | Merges postings split across rows. Read-only unless `--apply`, which backs up first. |
| `prefilter_report.py` | What the prefilter killed and whether it was right. Sampling and re-apply. |
| `backlog_report.py` | Milestone 2.5. Everything open that was never alerted on. `--mark-alerted` stamps it. |
| `sync_airtable.py` | Moves records both ways by hand. |
| `airtable_bootstrap.py` | Creates the base described in `sources/airtable.toml`. |
| `sync_calendar.py` | Pushes application windows and deadlines to Google Calendar. |
| `verify_tokens.py` | Proves every token in the map still returns postings. Run after editing the map. |
| `probe_tokens.py` | Brute-forces candidate tokens for a new company across all three ATS platforms. |
| `prep.py` | Tracks the preparation work, highest leverage first. |

## Local files, never committed

| Path | What it is |
| --- | --- |
| `state.db` | SQLite. The source of truth, roughly 80 MB. Losing it loses every `first_seen` date. |
| `.env` | API keys, SMTP credentials, Airtable token. |
| `logs/` | Output from scheduled runs. `run.log` is the run history, `launchd.log` should stay empty. |
| `credentials.json`, `.google-token.json` | Google Calendar OAuth. |
| `.venv/` | The virtual environment. Everything runs as `.venv/bin/python -m ...`. |
