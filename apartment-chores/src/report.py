"""Report exporter: one person's open chores, as a file another program reads.

Writes the agent report contract used by the Life Dashboard
(generated_at, status, items[title, summary, due, urgency, link]). Read-only:
it reads Roster and Assignments and writes nothing to Airtable. Give it a
token with data.records:read only.

    python -m src.report --out PATH [--days-ahead 7] [--dry-run]

Environment (or a .env file in the repo root, which is gitignored):
AIRTABLE_API_KEY, AIRTABLE_BASE_ID, and CHORES_REPORT_EMAIL, the roster email
of the person the report is for.

Like the orchestrator, this is an entry point, so it reads the clock, the
environment, and argv. `build_report` below it is pure.

An item is included when it is not done and is overdue or due within
--days-ahead days. Urgency is "overdue" once the due time has passed,
"due_today" when it falls on today's date, and null otherwise.

If the run fails, it writes a report with status "error" and no items, so the
reader shows an error instead of yesterday's list as if it were current.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from config.due_policy import DEFAULT_DUE_POLICY

API_KEY_ENV = "AIRTABLE_API_KEY"
BASE_ID_ENV = "AIRTABLE_BASE_ID"
EMAIL_ENV = "CHORES_REPORT_EMAIL"
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
DEFAULT_DAYS_AHEAD = 7


def build_report(rows, email, now, days_ahead=DEFAULT_DAYS_AHEAD):
    """Return the report dict for the person with this roster email.

    rows:  AssignmentRow objects (any assignee, any week)
    now:   timezone-aware datetime in the policy timezone
    """
    # No rows for this email is normal before the term starts, and looks the
    # same as a typo, so `run` checks the email against the roster first.
    mine = [r for r in rows if r.assignee.email.strip().lower() == email.strip().lower()]
    horizon = now + timedelta(days=days_ahead)
    items = []
    for r in sorted(mine, key=lambda r: r.due):
        if r.done or r.due > horizon:
            continue
        due_local = r.due.astimezone(now.tzinfo)
        if r.due < now:
            urgency = "overdue"
        elif due_local.date() == now.date():
            urgency = "due_today"
        else:
            urgency = None
        items.append(
            {
                "title": r.label,
                "summary": r.task.splitlines()[0].strip() if r.task else "",
                "due": due_local.isoformat(),
                "urgency": urgency,
                "link": "",
            }
        )
    return {"generated_at": now.isoformat(), "status": "ok", "items": items}


def error_report(now):
    return {"generated_at": now.isoformat(), "status": "error", "items": []}


def load_env_file(path=ENV_FILE):
    """Read KEY=value lines into os.environ without overriding what is set."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def write_report(report, out):
    """Write atomically, so a reader never sees half a file."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(report, indent=2) + "\n")
    tmp.replace(out)


def _settings():
    values = {name: os.environ.get(name, "").strip() for name in (API_KEY_ENV, BASE_ID_ENV, EMAIL_ENV)}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise RuntimeError(
            "cannot build the report: %s unset or empty (set in .env)" % ", ".join(missing)
        )
    return values[API_KEY_ENV], values[BASE_ID_ENV], values[EMAIL_ENV]


def run(out, days_ahead, dry_run, client=None, now=None):
    """Read Airtable and write (or print) the report. Returns the report."""
    from src.airtable import AirtableClient

    now = now or datetime.now(ZoneInfo(DEFAULT_DUE_POLICY.timezone))
    try:
        if client is None:
            api_key, base_id, email = _settings()
            client = AirtableClient(api_key, base_id)
        else:
            email = os.environ.get(EMAIL_ENV, "").strip()
        roster = tuple(client.roster())
        if not any(p.email.strip().lower() == email.lower() for p in roster):
            raise RuntimeError("%s is not an active roster email" % EMAIL_ENV)
        report = build_report(client.assignment_rows(roster), email, now, days_ahead)
    except Exception:
        if not dry_run:
            write_report(error_report(now), out)
        raise
    if dry_run:
        print(json.dumps(report, indent=2))
    else:
        write_report(report, out)
        print("Report: %d item(s) written to %s" % (len(report["items"]), out))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Write one person's open chores as an agent report.")
    parser.add_argument("--out", required=True, help="path of the report file to write")
    parser.add_argument("--days-ahead", type=int, default=DEFAULT_DAYS_AHEAD,
                        help="include chores due within this many days (default %(default)s)")
    parser.add_argument("--dry-run", action="store_true", help="print the report, write nothing")
    args = parser.parse_args(argv)
    load_env_file()
    try:
        run(args.out, args.days_ahead, args.dry_run)
    except Exception as e:
        print("Report failed: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
