"""Report exporter for the Life Dashboard: the search, as a file another program reads.

    .venv/bin/python -m tools.report --out PATH     write the report
    .venv/bin/python -m tools.report --dry-run      print it, write nothing

Added 2026-09-28. The Life Dashboard (~/agents/life-dashboard, a separate
project on this Mac) has a Job search card and reads it from a file in its
"agent report contract": `generated_at`, `status`, and `items[]` of `title`,
`summary`, `due`, `urgency`, `link`. Parsing the daily email was the other
option and it loses: the digest is suppressed on quiet days, is sent at 18:10,
and its layout changes whenever it is improved.

What goes in, each as one item:

    offers                     urgency reply_needed
    interviews and events      next_event_at within EVENT_DAYS; due_today or deadline
    deadlines                  roles marked interested, not applied, open, with a
                               stated_deadline date within DEADLINE_DAYS
    status changes             Applied status moves detected in the last CHANGE_HOURS
    silent applications        one item naming every application with no reply in
                               dashboard.SILENT_AFTER_DAYS

READ-ONLY, by construction. It opens state.db with `PRAGMA query_only` rather than
`db.connect`, which runs migrations, and it never writes a stamp (CLAUDE.md rules
10 and 14), never calls Airtable (rule 8) and never sends anything. The one file
it keeps of its own is `build/report_state.json`, because SQLite stores only each
posting's current Applied status and never when it changed; comparing against the
statuses this tool saw last time is the only way to say "moved since yesterday".

It runs as the last, optional step of every scheduled job (sources/schedule.toml),
so the report is at most a few hours old when the dashboard builds at 06:00, and
a failure here can never stop a run. On any error it writes `"status": "error"`
so the dashboard shows an error rather than an old list as if it were current.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

from agent import config, dashboard

STATE_FILE = config.ROOT / "build" / "report_state.json"
EVENT_DAYS = 7
DEADLINE_DAYS = 7
# Longer than a day on purpose: the 18:10 run is the one that pulls Airtable, so
# a status he changed at noon is first seen at 18:10 and must still be in the
# report the dashboard reads at 06:00 the next morning, and the morning after
# that if the Mac slept through one.
CHANGE_HOURS = 36
KEEP_CHANGES_DAYS = 7
STATUS_WORDS = {
    "not_applied": "not applied",
    "applied": "applied",
    "interviewing": "interviewing",
    "rejected": "rejected",
    "offer": "offer",
    "skipped": "skipped",
    "missed": "missed",
}


def _now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def _open(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(f"no database at {path}")  # connect would create an empty one
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA query_only = ON")
    conn.row_factory = sqlite3.Row
    return conn


def _date(value) -> str | None:
    """An ISO date or datetime string the dashboard can parse, or None.

    Tolerant for the reason CLAUDE.md rule 15 gives: columns people fill hold
    dates, timestamps, "rolling" and blanks, and none of that may raise here.
    """
    if not value:
        return None
    text = str(value).strip()
    try:
        dt.datetime.fromisoformat(text)
        return text
    except ValueError:
        pass
    try:
        return dt.date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


def _label(row) -> str:
    return f"{row['company']}, {row['title']}"


def _link(row) -> str:
    url = (row["url"] or "").strip()
    return url if url.startswith(("https://", "http://")) else ""


def _word(status: str) -> str:
    return STATUS_WORDS.get(status, status.replace("_", " "))


# ------------------------------------------------------------ status changes

def statuses(conn) -> dict[str, dict]:
    """Every posting whose Applied status is not the default, keyed by row hash."""
    return {
        r["hash"]: {"status": r["applied_status"], "company": r["company"], "title": r["title"], "url": r["url"]}
        for r in conn.execute(
            "SELECT hash, company, title, url, applied_status FROM postings "
            "WHERE applied_status <> 'not_applied'"
        )
    }


def detect_changes(previous: dict | None, current: dict, now: dt.datetime) -> list[dict]:
    """Moves since the last run. The first run ever has nothing to compare against."""
    if previous is None:
        return []
    changes = []
    for key, row in current.items():
        before = previous.get(key, {}).get("status", "not_applied")
        if before != row["status"]:
            changes.append({**row, "hash": key, "from": before, "to": row["status"], "at": now.isoformat()})
    return changes


def load_state(path: Path = STATE_FILE) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def save_state(state: dict, path: Path = STATE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    tmp.replace(path)


def update_state(state: dict, current: dict, now: dt.datetime) -> tuple[dict, list[dict]]:
    """Record this run's statuses; return the new state and changes inside CHANGE_HOURS."""
    found = detect_changes(state.get("statuses"), current, now)
    keep_after = now - dt.timedelta(days=KEEP_CHANGES_DAYS)
    log = [c for c in state.get("changes", []) + found if dt.datetime.fromisoformat(c["at"]) >= keep_after]
    show_after = now - dt.timedelta(hours=CHANGE_HOURS)
    recent = [c for c in log if dt.datetime.fromisoformat(c["at"]) >= show_after]
    return {"statuses": current, "changes": log}, recent


# ------------------------------------------------------------ the report

def build_items(conn, changes: list[dict]) -> list[dict]:
    items: list[dict] = []

    for row in conn.execute("SELECT * FROM postings WHERE applied_status = 'offer'"):
        items.append({
            "title": f"Offer: {_label(row)}",
            "summary": "You have an offer to answer.",
            "due": None,
            "urgency": "reply_needed",
            "link": _link(row),
        })

    for row in dashboard.upcoming(conn):
        days = row.get("days_until")
        if days is None or days < 0 or days > EVENT_DAYS:
            continue
        items.append({
            "title": f"{row.get('next_event_note') or 'Scheduled event'}: {row['company']}",
            "summary": row["title"],
            "due": _date(row.get("next_event_at")),
            "urgency": "due_today" if days == 0 else "deadline",
            "link": _link(row),
        })

    for row in conn.execute(
        f"SELECT * FROM postings WHERE {dashboard.OPEN} AND label = 'interested' "
        f"AND applied_status = '{dashboard.LIVE}'"
    ):
        days = dashboard._days_until(row["stated_deadline"])
        if days is None or days < 0 or days > DEADLINE_DAYS:
            continue
        items.append({
            "title": f"Apply: {_label(row)}",
            "summary": "Marked interested, not applied yet.",
            "due": _date(row["stated_deadline"]),
            "urgency": "due_today" if days == 0 else "deadline",
            "link": _link(row),
        })

    for c in sorted(changes, key=lambda c: c["at"], reverse=True):
        items.append({
            "title": f"{c['company']}, {c['title']}",
            "summary": f"Now {_word(c['to'])} (was {_word(c['from'])}).",
            "due": None,
            "urgency": None,
            "link": c["url"] if (c.get("url") or "").startswith(("https://", "http://")) else "",
        })

    silent = [r for r in dashboard.pipeline(conn) if r.get("silent")]
    if silent:
        names = ", ".join(r["company"] for r in silent)
        items.append({
            "title": f"{len(silent)} application{'s' if len(silent) != 1 else ''} with no reply "
                     f"in {dashboard.SILENT_AFTER_DAYS}+ days",
            "summary": names,
            "due": None,
            "urgency": None,
            "link": "",
        })
    return items


def run(db_path: Path, now: dt.datetime, state_path: Path = STATE_FILE, save: bool = True) -> dict:
    conn = _open(db_path)
    try:
        current = statuses(conn)
        state, recent = update_state(load_state(state_path), current, now)
        items = build_items(conn, recent)
    finally:
        conn.close()
    if save:
        save_state(state, state_path)
    return {"generated_at": now.isoformat(timespec="seconds"), "status": "ok", "items": items}


def write_report(report: dict, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, indent=2) + "\n")
    tmp.replace(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", help="where to write the report (a ~ is expanded)")
    parser.add_argument("--dry-run", action="store_true", help="print the report and write nothing")
    args = parser.parse_args(argv)
    if not args.out and not args.dry_run:
        parser.error("--out is required unless --dry-run")
    now = _now()
    try:
        report = run(config.DB_PATH, now, save=not args.dry_run)
    except Exception as e:  # noqa: BLE001 - any failure becomes an error report, never a stale list
        print(f"report failed: {type(e).__name__}: {e}", file=sys.stderr)
        report = {"generated_at": now.isoformat(timespec="seconds"), "status": "error", "items": []}
    if args.dry_run:
        print(json.dumps(report, indent=2))
    else:
        out = Path(args.out).expanduser()
        write_report(report, out)
        print(f"Report: {len(report['items'])} item(s), status {report['status']}, written to {out}")
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
