"""The Airtable call counter, tested against a fake transport.

    .venv/bin/python -m tools.test_airtable_calls

Spends nothing and makes no network call: every request goes to an
`httpx.MockTransport` defined here. Added 2026-09-25, when nothing counted
Airtable calls and every figure CLAUDE.md rule 8 reasons with was an estimate.

Three things are pinned. The client counts every attempt, reads and writes
separately, including one that errors. A sync that fails part way still reports
what it spent. And the contacts push no longer rewrites contacts that already
match, which was one wasted write call on every sync.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import httpx

from agent import airtable, airtable_sync, db

FAILED: list[str] = []
PASSED = 0


def check(label, got, want):
    global PASSED
    if got == want:
        PASSED += 1
        print(f"ok   {label}")
    else:
        FAILED.append(f"{label}: expected {want!r}, got {got!r}")
        print(f"FAIL {label}: expected {want!r}, got {got!r}")


class FakeBase:
    """Answers the handful of routes the client uses. Tables hold records by
    name; `fail` makes every request to a path containing it return 500, and
    `drop` makes it raise as a network error would."""

    def __init__(self, tables: dict[str, list[dict]] | None = None,
                 page_size: int = 100, fail: str = "", drop: str = ""):
        self.tables = tables or {}
        self.page_size = page_size
        self.fail, self.drop = fail, drop
        self.seen: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.seen.append((request.method, path))
        if self.drop and self.drop in path:
            raise httpx.ConnectError("nodename nor servname provided", request=request)
        if self.fail and self.fail in path:
            return httpx.Response(500, text="server error")
        table = path.rstrip("/").split("/")[-1]
        if request.method == "GET":
            rows = self.tables.get(table, [])
            start = int(request.url.params.get("offset") or 0)
            body = {"records": rows[start:start + self.page_size]}
            if start + self.page_size < len(rows):
                body["offset"] = str(start + self.page_size)
            return httpx.Response(200, json=body)
        if request.method == "POST":
            sent = json.loads(request.content)["records"]
            return httpx.Response(200, json={"records": [
                {"id": f"rec{i}", "fields": r["fields"]} for i, r in enumerate(sent)
            ]})
        return httpx.Response(200, json={"records": []})


REAL_CLIENT = airtable.Client


def client_for(base: FakeBase) -> airtable.Client:
    return REAL_CLIENT(token="test", base_id="appTEST",
                           transport=httpx.MockTransport(base))


def test_counts_reads_writes_and_errors():
    rows = [{"id": f"r{i}", "fields": {}} for i in range(250)]
    base = FakeBase({"Postings": rows})
    c = client_for(base)
    airtable.WRITE_INTERVAL, saved = 0, airtable.WRITE_INTERVAL
    try:
        got = c.records("Postings")
        check("250 records at 100 a page is 3 reads", (len(got), c.reads), (250, 3))
        c.create_records("Postings", [{"Title": str(i)} for i in range(15)])
        check("15 creates at 10 a call is 2 writes", c.calls_by_method.get("POST"), 2)
        c.update_records("Postings", [{"id": "r1", "fields": {}}])
        c.delete_records("Postings", ["r1", "r2"])
        check("a PATCH and a DELETE are one write each",
              (c.calls_by_method.get("PATCH"), c.calls_by_method.get("DELETE")), (1, 1))
        check("the total is every attempt", (c.calls, c.reads, c.writes), (7, 3, 4))
        check("the counter agrees with what the transport saw", c.calls, len(base.seen))
    finally:
        airtable.WRITE_INTERVAL = saved
        c.close()

    # Airtable counts a refused request, so the counter must too.
    for kind, base in [("a 500", FakeBase(fail="Postings")),
                       ("a network error", FakeBase(drop="Postings"))]:
        c = client_for(base)
        raised = False
        try:
            c.records("Postings")
        except airtable.AirtableError:
            raised = True
        check(f"{kind} still raises AirtableError", raised, True)
        check(f"{kind} is still counted as a call", (c.calls, c.reads), (1, 1))
        c.close()


def test_a_failed_sync_still_reports_its_calls():
    """The report rides on the exception, filled by sync's finally."""
    base = FakeBase(fail="Postings")
    conn = db.connect(Path(tempfile.mkdtemp()) / "calls.db")
    saved = airtable_sync.airtable.Client
    airtable_sync.airtable.Client = lambda *a, **k: client_for(base)
    report = None
    try:
        airtable_sync.sync(dry_run=True, conn=conn)
    except airtable.AirtableError as exc:
        report = getattr(exc, "report", None)
    finally:
        airtable_sync.airtable.Client = saved
        conn.close()
    check("a failing sync attaches its report to the error", report is not None, True)
    check("and the report counts the call that failed",
          (report.api_calls, report.api_reads) if report else None, (1, 1))


def _contacts_conn():
    conn = db.connect(Path(tempfile.mkdtemp()) / "contacts.db")
    conn.execute("INSERT INTO contacts (name, relationship, notes) "
                 "VALUES ('Pat Example', 'family friend', 'met in June')")
    conn.commit()
    return conn


def test_contacts_are_not_rewritten_when_they_match():
    schema = airtable.load_schema()
    same = {"id": "recC", "fields": {
        "Name": "Pat Example", "Relationship": "family friend", "Notes": "met in June",
    }}
    changed = {"id": "recC", "fields": {
        "Name": "Pat Example", "Relationship": "colleague", "Notes": "met in June",
    }}
    for label, live, want_patch in [
        ("a matching contact sends no update", same, 0),
        ("a changed contact still sends one", changed, 1),
    ]:
        base = FakeBase({"Contacts": [live], "Companies": []})
        c = client_for(base)
        conn = _contacts_conn()
        try:
            airtable_sync._push_contacts(c, conn, schema, {}, dry_run=False)
            check(label, c.calls_by_method.get("PATCH", 0), want_patch)
        finally:
            c.close()
            conn.close()


def test_payload_differs_on_contact_shapes():
    """Rule 8: a false negative here stops real edits reaching the base, so the
    comparison is tested directly and not only through the push."""
    differs = airtable_sync._payload_differs
    live = {"fields": {"Name": "Pat", "Company": ["recX"], "Last contacted": "2026-09-01"}}
    check("identical text, link and date do not differ",
          differs({"Name": "Pat", "Company": ["recX"], "Last contacted": "2026-09-01"},
                  live), False)
    check("an empty link matches a cell Airtable left out",
          differs({"Name": "Pat", "Company": []}, {"fields": {"Name": "Pat"}}), False)
    check("a changed name differs", differs({"Name": "Sam"}, live), True)
    check("a changed link differs", differs({"Company": ["recY"]}, live), True)
    check("a new date differs", differs({"Last contacted": "2026-09-20"}, live), True)
    check("a value where the cell is empty differs",
          differs({"Notes": "new"}, live), True)


def main() -> int:
    test_counts_reads_writes_and_errors()
    test_a_failed_sync_still_reports_its_calls()
    test_contacts_are_not_rewritten_when_they_match()
    test_payload_differs_on_contact_shapes()
    print(f"\n{PASSED} of {PASSED + len(FAILED)} passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
