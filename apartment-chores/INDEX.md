# Index

One line per file.

| File | What it is |
|---|---|
| `CLAUDE.md` | Standing rules for working in this repo |
| `README.md` | How a stranger sets this up and runs it in five minutes |
| `PRD.md` | The spec |
| `PRD-v1.1-landlord-reader.md` | Spec for the landlord email reader: proposes items, never confirms them. Built, running in dry-run |
| `CHANGELOG.md` | One entry per push, plus the handoff note for the next session |
| `INDEX.md` | This file |
| `.github/workflows/scheduler.yml` | The cron job: weekday mornings, checkout, install, run the orchestrator |
| `.github/workflows/read_inbox.yml` | The landlord inbox reader's cron job: daily, its own install step, starts in dry-run |
| `config/calendar.py` | The `Term` shape and Autumn Quarter 2026 as data: start date, week count, inactive weeks |
| `config/cadences.py` | The `Cadence` shape and the cadence registry, mapping a name to an interval in active weeks |
| `config/cleaner.py` | The `CleanerBehaviour` shape and the registry of what a chore does in a confirmed cleaner week |
| `config/due_policy.py` | Timezone and due times: ordinary chores Sunday 20:00 Central, cleaner prep 11:00 on the visit date |
| `config/rules.py` | The `RuleCategory` shape and the override / standing registry, with each category's heading and order |
| `config/digest_copy.py` | Every word the digest can say, including weekday and month names. Change the wording here, not in code |
| `config/digest_style.py` | Fonts and colours for the HTML digest. Presentation only, no words |
| `config/nudge_copy.py` | Every word a nudge can say. Same pattern as the digest copy |
| `config/sender.py` | The display name on outgoing mail, so a reminder reads as the system rather than as one housemate chasing another |
| `config/landlord.py` | The inbox reader's settings: landlord allowlist, IMAP host, lookback window, model and parameters |
| `config/inbox_copy.py` | Every word the inbox reader says: the model's system prompt and the summary email |
| `config/proposal_fields.py` | Airtable names the inbox reader writes: the Requests table and two Cleaner Visits fields. No Confirmed field to write |
| `config/airtable_fields.py` | Every Airtable table and field name. Rename a field in Airtable by changing one string here |
| `src/rotation.py` | The rotation and calendar engine: active weeks, occurrence indexes, whose turn it is. Pure functions, no I/O |
| `src/schedule.py` | The schedule builder: task text, due datetimes, cleaner prep conversion. Pure functions, no I/O |
| `src/digest.py` | The weekly digest renderer: one shared plain text body plus the house rules in force. Pure functions, no I/O |
| `src/nudge.py` | Which overdue assignments get a nudge and which kind, and the body for each. Pure functions, no clock |
| `src/airtable.py` | The HTTP client: reads every table, writes Assignments, reads and writes the nudge log. Validates on read and fails with the record ID |
| `src/email_sender.py` | Gmail SMTP. Builds a plain text envelope and sends it. Credentials from the environment only |
| `src/orchestrator.py` | The entry point. The only module that reads a clock, an environment, or argv. Wires the rest together |
| `src/inbox.py` | IMAP fetch of the landlord's mail only: read-only, headers checked before any body is loaded, quoted replies cut |
| `src/extract.py` | The one model call: email text in, proposed items out, every date and excerpt checked against the email |
| `src/proposals.py` | Turns items into unconfirmed rows, de-duplicates by Message-ID and visit date, and writes them |
| `src/read_inbox.py` | The inbox reader's entry point. Separate from the orchestrator so it cannot affect the Monday run |
| `scripts/setup_base.py` | One-time Airtable base setup: creates the tables and fields. Idempotent, safe to re-run |
| `scripts/send_test_digest.py` | Sends a real digest to addresses given with --to, never to the roster, for testing delivery |
| `scripts/setup_inbox_tables.py` | One-time setup for the inbox reader: creates Requests, adds two Cleaner Visits fields. Idempotent |
| `tests/test_rotation.py` | Invariant tests for the engine: quarter totals, per-chore evenness, inactive weeks, a four-person term, and the failure cases |
| `tests/test_schedule.py` | Tests for the schedule builder: due times, daylight saving, cleaner conversion, and the failure cases |
| `tests/test_digest.py` | Tests for the digest: the week's split, cleaner weeks, rule activation dates, and that it never mentions who is behind |
| `tests/test_nudge.py` | Tests for the nudge rules: one first, one follow-up at 48h, never a third, never before the due date |
| `tests/test_airtable.py` | Tests for the record parsers, the assignment identity tuple, and the nudge log round trip. No HTTP |
| `tests/test_email_sender.py` | Tests for credential handling and the envelope. No sockets |
| `tests/test_inbox.py` | The allowlist, and proof that only the landlord's genuine mail is fetched past its headers (L6) |
| `tests/test_extract.py` | The model request's shape, and every check on the model's answer, against hand-written answers |
| `tests/test_read_inbox.py` | The inbox reader end to end against fakes: L2 through L7, the Airtable budget, dry-run |
| `tests/test_orchestrator.py` | End-to-end tests against a fake client: 72 assignments, idempotent generation, digest recipients, nudge routing |
