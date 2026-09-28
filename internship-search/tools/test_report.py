"""The Life Dashboard report exporter, tools/report.py. 2026-09-28.

    .venv/bin/python -m tools.test_report

Spends nothing, sends nothing, and never touches state.db or build/.

The exporter is read-only by construction, and that is the property most worth
testing, because a report tool that wrote a stamp would silently remove postings
from every future email (CLAUDE.md rules 10 and 14). The rest checks what goes
in the file the dashboard reads, and that "moved since yesterday" survives the
four runs a day and the morning after.
"""

import datetime as dt
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

from agent import db
from tools import report

PASS, FAIL = "ok  ", "FAIL"
results: list[tuple[bool, str, str]] = []
NOW = dt.datetime.now().astimezone().replace(microsecond=0)


def check(name: str, got, want, note: str = "") -> None:
    results.append((got == want, name, note or f"got {got!r}, wanted {want!r}"))


def day(offset: int) -> str:
    return (dt.date.today() + dt.timedelta(days=offset)).isoformat()


def make_db() -> tuple[Path, Path]:
    """A migrated test database and a state file path, both in a temp dir."""
    tmp = Path(tempfile.mkdtemp())
    db.connect(tmp / "test.db").close()
    return tmp / "test.db", tmp / "report_state.json"


def add(path: Path, n: int, **kw) -> None:
    row = {
        "hash": f"h{n}", "identity": f"i{n}", "content_hash": f"h{n}",
        "company": f"Co{n}", "title": f"Intern {n}", "location": "Chicago, IL",
        "url": f"https://example.test/{n}", "source": "greenhouse:acme",
        "first_seen": (NOW - dt.timedelta(days=10)).isoformat(), "last_seen_open": NOW.isoformat(),
        "prefilter_verdict": "surface", "applied_status": "not_applied", "flags": "", "term": "summer2027",
    }
    row.update(kw)
    conn = sqlite3.connect(path)
    conn.execute(f"INSERT INTO postings ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})", list(row.values()))
    conn.commit()
    conn.close()


def set_status(path: Path, n: int, status: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute("UPDATE postings SET applied_status = ? WHERE hash = ?", (status, f"h{n}"))
    conn.commit()
    conn.close()


def titles(rep: dict) -> list[str]:
    return [i["title"] for i in rep["items"]]


# ------------------------------------------------------------ read-only

def test_the_connection_cannot_write():
    path, _ = make_db()
    conn = report._open(path)
    try:
        conn.execute("UPDATE postings SET alerted_at = 'x'")
        check("query_only blocks writes", False, True, "an UPDATE went through")
    except sqlite3.OperationalError as exc:
        check("query_only blocks writes", "readonly" in str(exc), True, str(exc))
    finally:
        conn.close()


def test_no_stamp_is_written():
    path, state = make_db()
    add(path, 1, applied_status="offer")
    add(path, 2, label="interested", stated_deadline=day(2))
    report.run(path, NOW, state)
    conn = sqlite3.connect(path)
    stamped = conn.execute(
        "SELECT COUNT(*) FROM postings WHERE alerted_at IS NOT NULL OR closure_alerted_at IS NOT NULL"
    ).fetchone()[0]
    conn.close()
    check("no alert stamp written", stamped, 0)


def test_a_missing_database_is_an_error_not_a_new_file():
    tmp = Path(tempfile.mkdtemp())
    try:
        report.run(tmp / "nope.db", NOW, tmp / "state.json")
        check("missing db raises", False, True)
    except FileNotFoundError:
        check("missing db raises", True, True)
    check("missing db not created", (tmp / "nope.db").exists(), False)


# ------------------------------------------------------------ what goes in

def test_offers_events_and_deadlines():
    path, state = make_db()
    add(path, 1, applied_status="offer")
    add(path, 2, applied_status="interviewing", next_event_at=day(0) + "T14:00", next_event_note="Interview")
    add(path, 3, applied_status="interviewing", next_event_at=day(3), next_event_note="OA")
    add(path, 4, applied_status="applied", next_event_at=day(12), next_event_note="Too far")
    add(path, 5, label="interested", stated_deadline=day(5))
    add(path, 6, label="interested", stated_deadline="rolling")
    add(path, 7, label="interested", stated_deadline=day(2), closed_detected_at=NOW.isoformat())
    add(path, 8, label="interested", stated_deadline=day(2), applied_status="applied")
    add(path, 9, stated_deadline=day(1))  # not marked interested
    rep = report.run(path, NOW, state)
    by = {i["title"]: i for i in rep["items"]}
    check("offer first, reply_needed", rep["items"][0]["urgency"], "reply_needed")
    check("interview today is due_today", by["Interview: Co2"]["urgency"], "due_today")
    check("event keeps its time", by["Interview: Co2"]["due"], day(0) + "T14:00")
    check("event in 3 days is a deadline", by["OA: Co3"]["urgency"], "deadline")
    check("event past the window left out", "Too far: Co4" in by, False)
    check("interested deadline in", by["Apply: Co5, Intern 5"]["due"], day(5))
    check("only the one deadline", [t for t in by if t.startswith("Apply:")], ["Apply: Co5, Intern 5"])
    check("status ok", rep["status"], "ok")


def test_silent_applications_are_one_item():
    path, state = make_db()
    old = (NOW - dt.timedelta(days=30)).isoformat()
    add(path, 1, applied_status="applied", applied_at=old)
    add(path, 2, applied_status="applied", applied_at=old)
    add(path, 3, applied_status="applied", applied_at=NOW.isoformat())
    add(path, 4, applied_status="rejected", applied_at=old)
    rep = report.run(path, NOW, state)
    silent = [i for i in rep["items"] if "no reply" in i["title"]]
    check("one silent item", len(silent), 1)
    check("silent counts only waiting ones", silent[0]["title"], "2 applications with no reply in 21+ days")


def test_dates_are_tolerant():
    check("rolling is no date", report._date("rolling"), None)
    check("bare date kept", report._date("2026-10-01"), "2026-10-01")
    check("timestamp kept", report._date("2026-10-01T09:00:00+00:00"), "2026-10-01T09:00:00+00:00")
    check("garbage after a date trimmed", report._date("2026-10-01 (approx)"), "2026-10-01")


# ------------------------------------------------------------ changes

def test_first_run_reports_no_changes():
    path, state = make_db()
    add(path, 1, applied_status="applied")
    rep = report.run(path, NOW, state)
    check("no changes on first run", [i for i in rep["items"] if i["summary"].startswith("Now ")], [])


def test_a_move_shows_until_it_ages_out():
    path, state = make_db()
    add(path, 1, applied_status="applied")
    add(path, 2)
    report.run(path, NOW, state)
    set_status(path, 1, "interviewing")
    set_status(path, 2, "applied")
    later = NOW + dt.timedelta(hours=6)
    rep = report.run(path, later, state)
    summaries = sorted(i["summary"] for i in rep["items"] if i["summary"].startswith("Now "))
    check("both moves reported", summaries, ["Now applied (was not applied).", "Now interviewing (was applied)."])
    # Two more runs the same evening and the next morning: still there.
    report.run(path, later + dt.timedelta(hours=4), state)
    rep = report.run(path, later + dt.timedelta(hours=12), state)
    check("move survives later runs", sum(i["summary"].startswith("Now ") for i in rep["items"]), 2)
    rep = report.run(path, later + dt.timedelta(hours=40), state)
    check("move ages out", sum(i["summary"].startswith("Now ") for i in rep["items"]), 0)
    kept = json.loads(state.read_text())["changes"]
    check("log kept for a week", len(kept), 2)


def test_dry_run_saves_nothing():
    path, state = make_db()
    add(path, 1, applied_status="applied")
    report.run(path, NOW, state, save=False)
    check("dry run leaves no state file", state.exists(), False)


def main() -> int:
    for fn in [
        test_the_connection_cannot_write,
        test_no_stamp_is_written,
        test_a_missing_database_is_an_error_not_a_new_file,
        test_offers_events_and_deadlines,
        test_silent_applications_are_one_item,
        test_dates_are_tolerant,
        test_first_run_reports_no_changes,
        test_a_move_shows_until_it_ages_out,
        test_dry_run_saves_nothing,
    ]:
        try:
            fn()
        except Exception as exc:
            results.append((False, fn.__name__, f"raised {type(exc).__name__}: {exc}"))

    failed = 0
    for ok, name, note in results:
        if ok:
            print(f"{PASS} {name}")
        else:
            failed += 1
            print(f"{FAIL} {name}: {note}")
    print(f"\n{len(results) - failed} of {len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
