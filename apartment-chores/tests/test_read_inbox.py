"""End-to-end tests for the inbox reader, and for the rows it proposes.

Covers PRD-v1.1 criteria L2 through L7 against fakes: no Airtable, no model,
no mail server. L1 needs a real saved landlord email, which does not exist
yet; the nearest stand-in is TestRun.test_date_email_proposes_one_visit.
"""

import io
import json
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from config.airtable_fields import DEFAULT_FIELD_CONFIG
from config.calendar import AUTUMN_2026
from config.proposal_fields import DEFAULT_PROPOSAL_FIELDS as FIELDS
from src import extract, proposals, read_inbox, schedule
from src.airtable import _parse_cleaner_visit
from src.inbox import Message

CHICAGO = ZoneInfo("America/Chicago")
SENT = datetime(2026, 9, 22, 19, 5, tzinfo=timezone.utc)
VISIT_TABLE = DEFAULT_FIELD_CONFIG.table_cleaner_visits
VISIT_DATE = DEFAULT_FIELD_CONFIG.cleaner_visits.visit_date
CONFIRMED = DEFAULT_FIELD_CONFIG.cleaner_visits.confirmed

VISIT_BODY = "The cleaner will come Thursday, October 1st at 12:30. Please clear the hallway by Wednesday."
VISIT_EXCERPT = "The cleaner will come Thursday, October 1st at 12:30."
HALLWAY_EXCERPT = "Please clear the hallway by Wednesday."


def message(message_id="<visit@x>", body=VISIT_BODY):
    return Message(message_id, "landlord@gmail.com", SENT, "Cleaning", body)


def visit_answer():
    return json.dumps({"items": [
        {"kind": "cleaner_visit", "summary": "Cleaner visit", "detail": "",
         "date": "2026-10-01", "excerpt": VISIT_EXCERPT},
        {"kind": "request", "summary": "Clear the hallway", "detail": "Before the visit.",
         "date": "2026-09-30", "excerpt": HALLWAY_EXCERPT},
    ]})


class FakeClient:
    """Stands in for the v1 AirtableClient's two generic helpers."""

    def __init__(self, tables=None):
        self.tables = {VISIT_TABLE: [], FIELDS.table_requests: []}
        self.tables.update(tables or {})
        self.reads = 0
        self.writes = 0

    def _fetch_all(self, table):
        self.reads += 1
        return [{"id": "rec%d" % i, "fields": dict(f)} for i, f in enumerate(self.tables[table])]

    def _create_records(self, table, field_dicts):
        self.writes += 1
        self.tables[table].extend(dict(f) for f in field_dicts)


class Harness:
    def __init__(self, answer=visit_answer, client=None, send_error=None):
        self.client = client or FakeClient()
        self.model_calls = 0
        self.store_built = 0
        self.sent = []
        self.answer = answer
        self.send_error = send_error

    def store(self):
        self.store_built += 1
        return proposals.ProposalStore(self.client, VISIT_TABLE, VISIT_DATE, FIELDS)

    def sender(self):
        if self.send_error:
            raise extract.ExtractionError(self.send_error)

        def send(request):
            self.model_calls += 1
            return "end_turn", self.answer()
        return send

    def run(self, messages, dry_run=False, notes=()):
        out = io.StringIO()
        with redirect_stdout(out):
            code = read_inbox.run(
                messages, list(notes), CHICAGO, dry_run,
                store_factory=self.store, send_factory=self.sender,
                notify=lambda subject, body: self.sent.append((subject, body)),
                link="https://airtable.com/appTEST",
            )
        self.output = out.getvalue()
        return code


class TestRun(unittest.TestCase):
    def test_date_email_proposes_one_visit(self):
        h = Harness()
        self.assertEqual(h.run([message()]), 0)
        visits = h.client.tables[VISIT_TABLE]
        self.assertEqual(len(visits), 1)
        self.assertEqual(visits[0][VISIT_DATE], "2026-10-01")
        self.assertEqual(visits[0][FIELDS.visits.raw_excerpt], VISIT_EXCERPT)
        self.assertEqual(visits[0][FIELDS.visits.source_message], "<visit@x>")
        requests = h.client.tables[FIELDS.table_requests]
        self.assertEqual(requests[0][FIELDS.requests.due], "2026-09-30")
        self.assertEqual(requests[0][FIELDS.requests.received], "2026-09-22T19:05:00Z")

    def test_summary_email_quotes_each_excerpt_and_links_the_base(self):
        h = Harness()
        h.run([message()])
        self.assertEqual(len(h.sent), 1)
        subject, body = h.sent[0]
        self.assertIn("2 to confirm", subject)
        self.assertIn("Cleaner visit on Thursday 1 October 2026", body)
        self.assertIn(VISIT_EXCERPT, body)
        self.assertIn(HALLWAY_EXCERPT, body)
        self.assertIn("https://airtable.com/appTEST", body)

    def test_L3_rerun_proposes_nothing_and_calls_no_model(self):
        h = Harness()
        h.run([message()])
        rows_after_first = {t: len(r) for t, r in h.client.tables.items()}
        calls_after_first = h.model_calls
        h.run([message()])
        self.assertEqual({t: len(r) for t, r in h.client.tables.items()}, rows_after_first)
        self.assertEqual(h.model_calls, calls_after_first)
        self.assertEqual(len(h.sent), 1)

    def test_reminder_with_new_message_id_does_not_duplicate_the_visit(self):
        h = Harness()
        h.run([message()])
        h.run([message("<reminder@x>")])
        self.assertEqual(len(h.client.tables[VISIT_TABLE]), 1)

    def test_hand_entered_visit_on_same_date_is_not_duplicated(self):
        client = FakeClient({VISIT_TABLE: [{VISIT_DATE: "2026-10-01", CONFIRMED: True}]})
        h = Harness(client=client)
        h.run([message()])
        self.assertEqual(len(client.tables[VISIT_TABLE]), 1)

    def test_L4_thank_you_note_produces_nothing(self):
        h = Harness(answer=lambda: json.dumps({"items": []}))
        self.assertEqual(h.run([message(body="Thanks for keeping the place tidy!")]), 0)
        self.assertEqual(h.client.writes, 0)
        self.assertEqual(h.sent, [])

    def test_L6_no_landlord_mail_touches_nothing(self):
        h = Harness()
        self.assertEqual(h.run([], notes=["UID 5: skipped, sender is not on the allowlist."]), 0)
        self.assertEqual(h.store_built, 0)
        self.assertEqual(h.model_calls, 0)
        self.assertEqual(h.client.reads, 0)

    def test_L7_model_error_changes_nothing_and_exits_zero(self):
        h = Harness(send_error="the API key was rejected")
        self.assertEqual(h.run([message()]), 0)
        self.assertEqual(h.client.writes, 0)
        self.assertEqual(h.sent, [])
        self.assertIn("Changing nothing", h.output)

    def test_L7_error_on_second_message_writes_nothing_from_the_first(self):
        h = Harness()
        answers = iter([visit_answer(), None])

        def flaky():
            value = next(answers)
            if value is None:
                raise extract.ExtractionError("overloaded")
            return value
        h.answer = flaky
        self.assertEqual(h.run([message("<a@x>"), message("<b@x>")]), 0)
        self.assertEqual(h.client.writes, 0)

    def test_dry_run_reads_but_writes_and_sends_nothing(self):
        h = Harness()
        self.assertEqual(h.run([message()], dry_run=True), 0)
        self.assertEqual(h.client.writes, 0)
        self.assertEqual(h.sent, [])
        self.assertIn("Would propose 2 row(s)", h.output)

    def test_airtable_budget_two_reads_and_at_most_one_write_per_table(self):
        h = Harness()
        h.run([message()])
        self.assertEqual(h.client.reads, 2)
        self.assertEqual(h.client.writes, 2)


class TestL5NeverConfirms(unittest.TestCase):
    def test_no_row_carries_a_confirmed_field(self):
        h = Harness()
        h.run([message()])
        for table, rows in h.client.tables.items():
            for row in rows:
                self.assertNotIn(CONFIRMED, row)
                self.assertNotIn(FIELDS.request_confirmed, row)

    def test_write_refuses_a_confirmed_row(self):
        store = proposals.ProposalStore(FakeClient(), VISIT_TABLE, VISIT_DATE, FIELDS)
        row = proposals.Row(VISIT_TABLE, {VISIT_DATE: "2026-10-01", CONFIRMED: True}, "cleaner_visit", None)
        with self.assertRaises(AssertionError):
            store.write([row])

    def test_write_path_source_never_names_the_confirmed_field(self):
        root = Path(__file__).resolve().parent.parent / "src"
        for name in ("read_inbox.py", "extract.py", "inbox.py"):
            source = (root / name).read_text()
            self.assertNotIn(".confirmed", source, name)
            self.assertNotIn("request_confirmed", source, name)
        # proposals.py names them exactly once each, in the refusal check.
        source = (root / "proposals.py").read_text()
        self.assertEqual(source.count("request_confirmed"), 1)
        self.assertEqual(source.count("cleaner_visits.confirmed"), 1)


class TestL2ConfirmedProposalActsLikeHandEntry(unittest.TestCase):
    """Ticking a proposed row changes generation exactly as a hand-entered one does.

    Generation sees a Cleaner Visits row only through the CleanerVisit the v1
    parser builds from it. If a ticked proposal and a hand-entered row parse
    to equal CleanerVisits, generation cannot tell them apart. The live
    version of this check, against the real base, is still to do.
    """

    def scheduled(self, visit_fields):
        return _parse_cleaner_visit({"id": "recX", "fields": visit_fields},
                                    DEFAULT_FIELD_CONFIG.cleaner_visits)

    def test_proposed_then_ticked_parses_identically_to_hand_entered(self):
        h = Harness()
        h.run([message()])
        proposed = dict(h.client.tables[VISIT_TABLE][0])
        self.assertFalse(self.scheduled(proposed).confirmed)

        proposed[CONFIRMED] = True
        hand_entered = {VISIT_DATE: "2026-10-01", CONFIRMED: True}
        self.assertEqual(self.scheduled(proposed), self.scheduled(hand_entered))

    def test_unticked_proposal_is_ignored_by_generation(self):
        h = Harness()
        h.run([message()])
        visit = self.scheduled(h.client.tables[VISIT_TABLE][0])
        self.assertEqual(schedule._confirmed_visits_by_week(AUTUMN_2026, [visit]), {})


if __name__ == "__main__":
    unittest.main()
