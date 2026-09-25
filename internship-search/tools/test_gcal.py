"""The calendar push, tested against a fake Google service.

    .venv/bin/python -m tools.test_gcal

Spends nothing, calls no Google API, and never touches state.db. Added
2026-09-25, when all 12 derived events turned out to be dated in the past on
the day they were created, so he never saw one.
"""

import datetime as dt
import sys

from agent import gcal

PASS, FAIL = "ok  ", "FAIL"
results: list[tuple[bool, str, str]] = []

TODAY = dt.date(2026, 9, 25)


def check(name: str, got, want, note: str = "") -> None:
    results.append((got == want, name, note or f"got {got!r}, wanted {want!r}"))


class _Call:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class FakeService:
    """Just enough of the Calendar API for `gcal.push` and `gcal.find_existing`."""

    def __init__(self, existing: dict | None = None):
        # key -> event, as Google would hold it
        self.events_by_key = dict(existing or {})
        self.inserted: list[dict] = []
        self.patched: list[dict] = []

    def events(self):
        return self

    def list(self, calendarId, privateExtendedProperty, **_kw):
        key = privateExtendedProperty.split("=", 1)[1]
        ev = self.events_by_key.get(key)
        return _Call({"items": [ev] if ev else []})

    def insert(self, calendarId, body):
        self.inserted.append(body)
        return _Call(body)

    def patch(self, calendarId, eventId, body):
        self.patched.append(body)
        return _Call(body)


def window(key, day, pinned=True):
    return gcal.Window(key=key, company="Acme", label=f"Follow up {key}",
                       start=day, end=day, confidence="confirmed", pinned=pinned)


def test_a_past_derived_event_is_created_today():
    svc = FakeService()
    gcal.push(svc, "cal", [window("followup:a", dt.date(2026, 8, 17))], today=TODAY)
    body = svc.inserted[0]
    check("a past-dated derived event is created on today", body["start"]["date"],
          "2026-09-25")
    check("and its exclusive end is the day after", body["end"]["date"], "2026-09-26")


def test_a_future_derived_event_keeps_its_date():
    svc = FakeService()
    gcal.push(svc, "cal", [window("followup:b", dt.date(2026, 10, 3))], today=TODAY)
    check("a future derived event keeps its computed date",
          svc.inserted[0]["start"]["date"], "2026-10-03")


def test_an_existing_derived_event_never_moves():
    """The trap from CHANGELOG 2026-09-22: a date of today, recomputed on every
    run, walks the event down the calendar four times a day."""
    existing = {"followup:c": {
        "id": "ev1",
        "start": {"date": "2026-09-20"},
        "end": {"date": "2026-09-21"},
    }}
    svc = FakeService(existing)
    gcal.push(svc, "cal", [window("followup:c", dt.date(2026, 8, 17))],
              today=dt.date(2026, 9, 28))
    body = svc.patched[0]
    check("an existing derived event keeps the date it was created with",
          (body["start"]["date"], body["end"]["date"]), ("2026-09-20", "2026-09-21"))
    check("but its title is still patched", body["summary"], "Follow up followup:c")
    check("and nothing is inserted beside it", svc.inserted, [])


def test_a_cycle_window_still_follows_the_toml():
    """Only derived events are pinned. A TOML edit to a window's dates must
    reach the calendar, and a past window stays where the TOML puts it."""
    existing = {"window:x": {
        "id": "ev2", "start": {"date": "2026-09-01"}, "end": {"date": "2026-09-02"},
    }}
    svc = FakeService(existing)
    gcal.push(svc, "cal", [
        window("window:x", dt.date(2026, 10, 1), pinned=False),
        window("window:y", dt.date(2026, 8, 1), pinned=False),
    ], today=TODAY)
    check("an unpinned window is patched to the TOML's date",
          svc.patched[0]["start"]["date"], "2026-10-01")
    check("an unpinned past window is created where the TOML says",
          svc.inserted[0]["start"]["date"], "2026-08-01")


def test_derived_windows_are_pinned():
    """The flag has to be set where the events are made, or none of the above
    applies to them."""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE postings (hash, company, title, url, applied_at, "
        "applied_status, tier, closed_detected_at, closed_by_me, "
        "prefilter_verdict, label, first_seen)"
    )
    conn.execute(
        "INSERT INTO postings VALUES ('h1','Acme','Intern','u','2026-08-01',"
        "'applied',NULL,NULL,0,'surface','interested','2026-07-01')"
    )
    conn.execute(
        "INSERT INTO postings VALUES ('h2','Beta','Intern','u',NULL,"
        "'not_applied',1,NULL,0,'surface','','2026-07-01')"
    )
    got = gcal.derived_windows(conn, today=TODAY)
    check("both kinds of derived event are made", sorted(w.key for w in got),
          ["followup:h1", "stale:h2"])
    check("every derived event is pinned", all(w.pinned for w in got), True)


def main() -> int:
    for fn in [
        test_a_past_derived_event_is_created_today,
        test_a_future_derived_event_keeps_its_date,
        test_an_existing_derived_event_never_moves,
        test_a_cycle_window_still_follows_the_toml,
        test_derived_windows_are_pinned,
    ]:
        fn()

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
