"""Tests for src/report.py and AirtableClient.assignment_rows.

No HTTP: the client's _fetch_all is replaced with synthetic records, and
run() is handed a fake client.
"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from config.airtable_fields import DEFAULT_FIELD_CONFIG
from src.airtable import AirtableClient, AssignmentRow, Person
from src import report

CHICAGO = ZoneInfo("America/Chicago")
AF = DEFAULT_FIELD_CONFIG.assignments
ALICE = Person(id="recR1", name="Alice", email="alice@example.edu", active=True, sort_order=1)
BOB = Person(id="recR2", name="Bob", email="bob@example.edu", active=True, sort_order=2)
NOW = datetime(2026, 9, 30, 6, 0, tzinfo=CHICAGO)  # a Wednesday morning


def row(label, due, assignee=ALICE, done=False, task="Wipe counters\nand the stove", is_prep=False):
    return AssignmentRow(label=label, task=task, due=due, done=done, is_prep=is_prep, assignee=assignee)


class BuildReportTests(unittest.TestCase):
    def test_contract_shape(self):
        out = report.build_report([row("Kitchen", NOW + timedelta(days=2))], "alice@example.edu", NOW)
        self.assertEqual(set(out), {"generated_at", "status", "items"})
        self.assertEqual(out["status"], "ok")
        [item] = out["items"]
        self.assertEqual(set(item), {"title", "summary", "due", "urgency", "link"})
        self.assertEqual(item["title"], "Kitchen")
        self.assertEqual(item["summary"], "Wipe counters")  # first line only
        self.assertTrue(item["due"].endswith("-05:00"))
        self.assertIsNone(item["urgency"])

    def test_urgency(self):
        rows = [
            row("Late", NOW - timedelta(hours=1)),
            row("Tonight", NOW.replace(hour=20)),
            row("Sunday", NOW + timedelta(days=4)),
        ]
        items = report.build_report(rows, "alice@example.edu", NOW)["items"]
        self.assertEqual([(i["title"], i["urgency"]) for i in items],
                         [("Late", "overdue"), ("Tonight", "due_today"), ("Sunday", None)])

    def test_filters_done_other_people_and_far_future(self):
        rows = [
            row("Done", NOW + timedelta(days=1), done=True),
            row("Bob's", NOW + timedelta(days=1), assignee=BOB),
            row("Next month", NOW + timedelta(days=30)),
            row("Kept", NOW + timedelta(days=1)),
        ]
        items = report.build_report(rows, "ALICE@example.edu ", NOW)["items"]
        self.assertEqual([i["title"] for i in items], ["Kept"])

    def test_days_ahead(self):
        rows = [row("In 3 days", NOW + timedelta(days=3))]
        self.assertEqual(report.build_report(rows, "alice@example.edu", NOW, days_ahead=2)["items"], [])

    def test_sorted_by_due_and_due_in_policy_timezone(self):
        utc_due = datetime(2026, 10, 5, 1, 0, tzinfo=timezone.utc)  # Sun Oct 4, 8pm Chicago
        rows = [row("Later", utc_due), row("Sooner", NOW + timedelta(hours=2))]
        items = report.build_report(rows, "alice@example.edu", NOW)["items"]
        self.assertEqual([i["title"] for i in items], ["Sooner", "Later"])
        self.assertEqual(items[1]["due"], "2026-10-04T20:00:00-05:00")


class FakeClient:
    def __init__(self, roster, rows=(), fail=None):
        self._roster, self._rows, self._fail = roster, rows, fail

    def roster(self):
        if self._fail:
            raise self._fail
        return list(self._roster)

    def assignment_rows(self, roster):
        return tuple(self._rows)


class RunTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.out = Path(self.dir.name) / "reports" / "chores.json"
        patcher = mock.patch.dict(os.environ, {report.EMAIL_ENV: "alice@example.edu"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.dir.cleanup)

    def test_writes_report(self):
        client = FakeClient([ALICE, BOB], [row("Kitchen", NOW + timedelta(days=1))])
        with mock.patch("builtins.print"):
            report.run(self.out, 7, dry_run=False, client=client, now=NOW)
        data = json.loads(self.out.read_text())
        self.assertEqual(data["status"], "ok")
        self.assertEqual([i["title"] for i in data["items"]], ["Kitchen"])
        self.assertFalse(self.out.with_suffix(".json.tmp").exists())

    def test_dry_run_writes_nothing(self):
        with mock.patch("builtins.print"):
            report.run(self.out, 7, dry_run=True, client=FakeClient([ALICE]), now=NOW)
        self.assertFalse(self.out.exists())

    def test_unknown_email_fails_and_writes_error_report(self):
        with self.assertRaises(RuntimeError):
            report.run(self.out, 7, dry_run=False, client=FakeClient([BOB]), now=NOW)
        data = json.loads(self.out.read_text())
        self.assertEqual((data["status"], data["items"]), ("error", []))

    def test_airtable_failure_writes_error_report(self):
        with self.assertRaises(ConnectionError):
            report.run(self.out, 7, dry_run=False, client=FakeClient([ALICE], fail=ConnectionError("down")), now=NOW)
        self.assertEqual(json.loads(self.out.read_text())["status"], "error")

    def test_missing_settings(self):
        with mock.patch.dict(os.environ, {report.API_KEY_ENV: "", report.BASE_ID_ENV: ""}):
            with self.assertRaises(RuntimeError) as ctx:
                report.run(self.out, 7, dry_run=True, now=NOW)
        self.assertIn(report.API_KEY_ENV, str(ctx.exception))

    def test_env_file_does_not_override(self):
        env = Path(self.dir.name) / ".env"
        env.write_text("# comment\nCHORES_REPORT_EMAIL=other@example.edu\nNEW_KEY='x'\n")
        with mock.patch.dict(os.environ, {}, clear=False):
            report.load_env_file(env)
            self.assertEqual(os.environ[report.EMAIL_ENV], "alice@example.edu")
            self.assertEqual(os.environ["NEW_KEY"], "x")
            del os.environ["NEW_KEY"]


class AssignmentRowsTests(unittest.TestCase):
    def client(self, records):
        c = AirtableClient.__new__(AirtableClient)
        c._fields = DEFAULT_FIELD_CONFIG
        c._fetch_all = lambda table: records
        return c

    def record(self, rid="recA1", assignee="recR1", **extra):
        fields = {AF.label: "Kitchen", AF.task: "Wipe", AF.due: "2026-10-05T01:00:00.000Z",
                  AF.done: False, AF.assignee: [assignee] if assignee else []}
        fields.update(extra)
        return {"id": rid, "fields": fields}

    def test_parses_rows(self):
        [r] = self.client([self.record(**{AF.done: True, AF.is_prep: True})]).assignment_rows([ALICE])
        self.assertEqual((r.label, r.task, r.done, r.is_prep, r.assignee), ("Kitchen", "Wipe", True, True, ALICE))
        self.assertEqual(r.due, datetime(2026, 10, 5, 1, 0, tzinfo=timezone.utc))

    def test_skips_rows_without_roster_assignee(self):
        rows = self.client([self.record(assignee=None), self.record(assignee="recGone")]).assignment_rows([ALICE])
        self.assertEqual(rows, ())

    def test_malformed_row_raises_with_record_id(self):
        with self.assertRaises(ValueError) as ctx:
            self.client([self.record(rid="recBad", **{AF.due: None})]).assignment_rows([ALICE])
        self.assertIn("recBad", str(ctx.exception))
        with self.assertRaises(ValueError):
            self.client([self.record(**{AF.label: ""})]).assignment_rows([ALICE])


if __name__ == "__main__":
    unittest.main()
