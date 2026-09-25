"""Tests for src/orchestrator.py.

The orchestrator is the only module that reads a clock, an environment, or a
network, so every test here hands it a fake client, a fixed `now`, and a
captured mail sender. Nothing opens a socket.

The fixture is 3 people, 6 weekly chores and 6 every_3 chores. That was the
real base until Sept 2026 and is now deliberately denser than it: two chores
share each offset, which is the harder case for the engine. The live 6-and-3
shape is pinned in tests/test_rotation.py under TestLiveApartmentShape.
"""

import io
import os
import unittest
from collections import Counter
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from config.calendar import AUTUMN_2026
from src import nudge, orchestrator
from src.airtable import AssignmentSnapshot, Person
from src.digest import Rule
from src.rotation import Chore
from src.schedule import ChoreDetail, CleanerVisit

CHICAGO = ZoneInfo("America/Chicago")

PEOPLE = (
    Person(id="recR1", name="One", email="one@uchicago.edu", active=True, sort_order=1),
    Person(id="recR2", name="Two", email="two@uchicago.edu", active=True, sort_order=2),
    Person(id="recR3", name="Three", email="three@uchicago.edu", active=True, sort_order=3),
)

WEEKLY_SEEDS = (0, 0, 1, 1, 2, 2)
PERIODIC = ((0, 0), (0, 1), (1, 1), (1, 2), (2, 2), (2, 0))  # (offset, seed)


def make_chores():
    """Return [(Chore, ChoreDetail)] in the shape the real base has."""
    pairs = []
    for index, seed in enumerate(WEEKLY_SEEDS):
        key = "recW%d" % index
        pairs.append(
            (
                Chore(key=key, cadence="weekly", offset=0, seed=seed),
                ChoreDetail(
                    name="Weekly chore %d" % index,
                    task="Do weekly thing %d" % index,
                    cleaner_behaviour="convert_to_prep" if index % 2 else "normal",
                    prep_task="Prep for weekly %d" % index if index % 2 else "",
                ),
            )
        )
    for index, (offset, seed) in enumerate(PERIODIC):
        key = "recP%d" % index
        pairs.append(
            (
                Chore(key=key, cadence="every_3", offset=offset, seed=seed),
                ChoreDetail(
                    name="Periodic chore %d" % index,
                    task="Do periodic thing %d" % index,
                    cleaner_behaviour="normal",
                ),
            )
        )
    return pairs


class FakeClient:
    """Stands in for AirtableClient. Records every write it is asked to make."""

    def __init__(self, visits=(), generated=(), completed=frozenset(), log=(), record_ids=None):
        self._visits = tuple(visits)
        self._generated = set(generated)
        self._completed = frozenset(completed)
        self._log = list(log)
        self._record_ids = dict(record_ids or {})
        self.written = []
        self.recorded = []

    def roster(self):
        return list(PEOPLE)

    def chores(self):
        return make_chores()

    def rules(self):
        return [
            Rule(text="Nothing sits in the sink.", category="override", number=1),
            Rule(text="Quiet hours.", category="standing", number=2, active_from=date(2026, 11, 1)),
        ]

    def cleaner_visits(self):
        return list(self._visits)

    def assignments(self, roster):
        return AssignmentSnapshot(
            week_numbers=frozenset(self._generated),
            completed_ids=self._completed,
            nudge_log=tuple(self._log),
            record_ids=dict(self._record_ids),
        )

    def generated_week_numbers(self):
        return frozenset(self._generated)

    def write_assignments(self, scheduled, existing=None):
        if existing is not None:
            self._generated |= set(existing)
        for item in scheduled:
            if item.week.number in self._generated:
                continue
            self.written.append(item)
        self._generated.update(item.week.number for item in scheduled)

    def completed_assignment_ids(self, roster):
        return self._completed

    def nudge_log(self, roster):
        return list(self._log)

    def assignment_record_ids(self, roster):
        return dict(self._record_ids)

    def record_nudge(self, record_id, nudge_type, sent_at):
        self.recorded.append((record_id, nudge_type, sent_at))


class OrchestratorCase(unittest.TestCase):
    """Captures stdout and swaps the mail sender for a recorder."""

    def setUp(self):
        self.sent = []
        self._real_send = orchestrator.send_email
        orchestrator.send_email = (
            lambda subject, body, to, display_name=None, html=None:
                self.sent.append((subject, body, tuple(to), display_name, html))
        )

    def tearDown(self):
        orchestrator.send_email = self._real_send

    def run_quietly(self, fn, *args, **kwargs):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            result = fn(*args, **kwargs)
        self.output = buffer.getvalue()
        return result

    def load(self, client):
        return self.run_quietly(orchestrator._load, client)


def _resnapshot(client, context):
    """Rebuild the context's snapshot after a fixture mutates the fake."""
    from dataclasses import replace as _replace
    return _replace(context, assignments=client.assignments(context.roster))


class TestJobSelection(unittest.TestCase):

    def test_no_flags_runs_both(self):
        args = orchestrator._parse_args([])
        self.assertEqual(orchestrator._jobs(args), (True, True))

    def test_generate_only(self):
        args = orchestrator._parse_args(["--generate"])
        self.assertEqual(orchestrator._jobs(args), (True, False))

    def test_nudge_only(self):
        args = orchestrator._parse_args(["--nudge"])
        self.assertEqual(orchestrator._jobs(args), (False, True))

    def test_both_flags_runs_both(self):
        args = orchestrator._parse_args(["--generate", "--nudge"])
        self.assertEqual(orchestrator._jobs(args), (True, True))

    def test_dry_run_defaults_off(self):
        self.assertFalse(orchestrator._parse_args([]).dry_run)

    def test_dry_run_applies_with_either_job(self):
        for argv in (["--dry-run"], ["--generate", "--dry-run"], ["--nudge", "--dry-run"]):
            self.assertTrue(orchestrator._parse_args(argv).dry_run, argv)


class TestWeekContaining(unittest.TestCase):

    def setUp(self):
        from src import rotation
        self.weeks = rotation.weeks(AUTUMN_2026)

    def test_first_monday(self):
        week = orchestrator._week_containing(date(2026, 9, 28), self.weeks)
        self.assertEqual(week.number, 1)

    def test_midweek(self):
        week = orchestrator._week_containing(date(2026, 9, 30), self.weeks)
        self.assertEqual(week.number, 1)

    def test_last_sunday_of_a_week(self):
        week = orchestrator._week_containing(date(2026, 10, 4), self.weeks)
        self.assertEqual(week.number, 1)

    def test_inactive_week_is_still_found(self):
        week = orchestrator._week_containing(date(2026, 11, 25), self.weeks)
        self.assertEqual(week.number, 9)
        self.assertFalse(week.active)

    def test_before_the_term_is_none(self):
        self.assertIsNone(orchestrator._week_containing(date(2026, 9, 27), self.weeks))

    def test_after_the_term_is_none(self):
        self.assertIsNone(orchestrator._week_containing(date(2026, 12, 14), self.weeks))


class TestLoad(OrchestratorCase):

    def test_builds_the_whole_term(self):
        context = self.load(FakeClient())
        self.assertEqual(len(context.scheduled), 72)

    def test_twenty_four_each(self):
        context = self.load(FakeClient())
        for person in PEOPLE:
            count = sum(1 for item in context.scheduled if item.assignee == person)
            self.assertEqual(count, 24, "%s has %d" % (person, count))

    def test_inactive_weeks_carry_nothing(self):
        context = self.load(FakeClient())
        numbers = {item.week.number for item in context.scheduled}
        self.assertNotIn(9, numbers)
        self.assertNotIn(11, numbers)

    def test_totals_are_printed(self):
        self.load(FakeClient())
        self.assertIn("72 assignments", self.output)

    def test_empty_roster_raises(self):
        client = FakeClient()
        client.roster = lambda: []
        with self.assertRaises(RuntimeError) as ctx:
            self.load(client)
        self.assertIn("nobody", str(ctx.exception))

    def test_confirmed_visit_in_an_inactive_week_warns(self):
        client = FakeClient(visits=[CleanerVisit(visit_date=date(2026, 11, 25), confirmed=True)])
        self.load(client)
        self.assertIn("WARNING", self.output)
        self.assertIn("2026-11-25", self.output)

    def test_unconfirmed_visit_does_not_warn(self):
        client = FakeClient(visits=[CleanerVisit(visit_date=date(2026, 11, 25), confirmed=False)])
        self.load(client)
        self.assertNotIn("WARNING", self.output)

    def test_confirmed_visit_converts_chores(self):
        client = FakeClient(visits=[CleanerVisit(visit_date=date(2026, 10, 15), confirmed=True)])
        context = self.load(client)
        week3 = [item for item in context.scheduled if item.week.number == 3]
        prep = [item for item in week3 if item.is_prep]
        self.assertEqual(len(prep), 3)
        for item in prep:
            self.assertEqual(item.due, datetime(2026, 10, 15, 11, 0, tzinfo=CHICAGO))

    def test_conversion_does_not_change_the_totals(self):
        plain = self.load(FakeClient())
        converted = self.load(
            FakeClient(visits=[CleanerVisit(visit_date=date(2026, 10, 15), confirmed=True)])
        )
        self.assertEqual(len(plain.scheduled), len(converted.scheduled))
        for a, b in zip(plain.scheduled, converted.scheduled):
            self.assertEqual(a.assignee, b.assignee)


class TestGenerate(OrchestratorCase):

    def _week(self, context, number):
        return next(w for w in context.weeks if w.number == number)

    def test_writes_the_current_week(self):
        client = FakeClient()
        context = self.load(client)
        self.run_quietly(orchestrator._generate, context, self._week(context, 1), False)
        self.assertEqual(len(client.written), 8)
        self.assertTrue(all(item.week.number == 1 for item in client.written))

    def test_already_generated_is_a_no_op(self):
        client = FakeClient(generated=[1])
        context = self.load(client)
        self.run_quietly(orchestrator._generate, context, self._week(context, 1), False)
        self.assertEqual(client.written, [])
        self.assertIn("already exists", self.output)

    def test_dry_run_writes_nothing(self):
        client = FakeClient()
        context = self.load(client)
        self.run_quietly(orchestrator._generate, context, self._week(context, 1), True)
        self.assertEqual(client.written, [])
        self.assertIn("would write 8 assignments", self.output)

    def test_inactive_week_writes_nothing(self):
        client = FakeClient()
        context = self.load(client)
        self.run_quietly(orchestrator._generate, context, self._week(context, 9), False)
        self.assertEqual(client.written, [])

    def test_outside_the_term_writes_nothing(self):
        client = FakeClient()
        context = self.load(client)
        self.run_quietly(orchestrator._generate, context, None, False)
        self.assertEqual(client.written, [])

    def test_running_the_whole_term_writes_seventy_two(self):
        client = FakeClient()
        context = self.load(client)
        for week in context.weeks:
            self.run_quietly(orchestrator._generate, context, week, False)
        self.assertEqual(len(client.written), 72)

    def test_running_the_whole_term_twice_still_writes_seventy_two(self):
        client = FakeClient()
        context = self.load(client)
        for _ in range(2):
            for week in context.weeks:
                self.run_quietly(orchestrator._generate, context, week, False)
        self.assertEqual(len(client.written), 72)


class TestApiCallBudget(OrchestratorCase):
    """Airtable's free tier meters API calls per month across a workspace.

    A run that reads the same table four times burns the budget for no
    reason, so the count is pinned rather than left to drift.
    """

    class CountingClient(FakeClient):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.reads = Counter()

        def roster(self):
            self.reads["Roster"] += 1
            return super().roster()

        def chores(self):
            self.reads["Chores"] += 1
            return super().chores()

        def rules(self):
            self.reads["Rules"] += 1
            return super().rules()

        def cleaner_visits(self):
            self.reads["Cleaner Visits"] += 1
            return super().cleaner_visits()

        def assignments(self, roster):
            self.reads["Assignments"] += 1
            return super().assignments(roster)

    def test_assignments_is_read_exactly_once_per_run(self):
        client = self.CountingClient(generated=[1])
        context = self.load(client)
        week = next(w for w in context.weeks if w.number == 1)
        self.run_quietly(orchestrator._generate, context, week, False)
        self.run_quietly(orchestrator._nudge, context,
                         datetime(2026, 10, 5, 7, 0, tzinfo=CHICAGO), False)
        self.assertEqual(client.reads["Assignments"], 1)

    def test_a_whole_run_reads_five_tables_and_no_more(self):
        client = self.CountingClient(generated=[1])
        context = self.load(client)
        week = next(w for w in context.weeks if w.number == 1)
        self.run_quietly(orchestrator._generate, context, week, False)
        self.run_quietly(orchestrator._send_digest, context, week, False)
        self.run_quietly(orchestrator._nudge, context,
                         datetime(2026, 10, 5, 7, 0, tzinfo=CHICAGO), False)
        self.assertEqual(sum(client.reads.values()), 5, dict(client.reads))

    def test_writing_a_week_does_not_re_read_the_table(self):
        client = self.CountingClient()
        context = self.load(client)
        week = next(w for w in context.weeks if w.number == 1)
        self.run_quietly(orchestrator._generate, context, week, False)
        self.assertEqual(client.reads["Assignments"], 1)
        self.assertEqual(len(client.written), 8)


class TestDigest(OrchestratorCase):

    def _week(self, context, number):
        return next(w for w in context.weeks if w.number == number)

    def test_goes_to_everyone(self):
        context = self.load(FakeClient())
        self.run_quietly(orchestrator._send_digest, context, self._week(context, 1), False)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][2], tuple(p.email for p in PEOPLE))

    def test_body_names_everyone(self):
        context = self.load(FakeClient())
        self.run_quietly(orchestrator._send_digest, context, self._week(context, 1), False)
        body = self.sent[0][1]
        for person in PEOPLE:
            self.assertIn(person.name, body)

    def test_digest_carries_the_sender_name(self):
        from config.sender import SENDER_NAME
        context = self.load(FakeClient())
        self.run_quietly(orchestrator._send_digest, context, self._week(context, 1), False)
        self.assertEqual(self.sent[0][3], SENDER_NAME)

    def test_an_html_alternative_goes_with_it(self):
        context = self.load(FakeClient())
        self.run_quietly(orchestrator._send_digest, context, self._week(context, 1), False)
        html = self.sent[0][4]
        self.assertIsNotNone(html)
        self.assertIn("Times New Roman", html)
        for person in PEOPLE:
            self.assertIn(person.name, html)

    def test_nudges_send_no_html(self):
        """A nudge is one short paragraph. Dressing it up would be worse."""
        client = FakeClient(generated=[1])
        context = self.load(client)
        client._record_ids = {
            (i.week.number, i.chore.key, i.assignee): "recA%d" % n
            for n, i in enumerate(context.scheduled) if i.week.number == 1
        }
        context = _resnapshot(client, context)
        self.run_quietly(orchestrator._nudge, context,
                         datetime(2026, 10, 5, 7, 0, tzinfo=CHICAGO), False)
        self.assertTrue(self.sent)
        self.assertTrue(all(html is None for _, _, _, _, html in self.sent))

    def test_dry_run_sends_nothing(self):
        context = self.load(FakeClient())
        self.run_quietly(orchestrator._send_digest, context, self._week(context, 1), True)
        self.assertEqual(self.sent, [])
        self.assertIn("would send", self.output)

    def test_inactive_week_sends_nothing(self):
        context = self.load(FakeClient())
        self.run_quietly(orchestrator._send_digest, context, self._week(context, 9), False)
        self.assertEqual(self.sent, [])

    def test_outside_the_term_sends_nothing(self):
        context = self.load(FakeClient())
        self.run_quietly(orchestrator._send_digest, context, None, False)
        self.assertEqual(self.sent, [])

    def test_never_says_who_is_behind(self):
        completed = frozenset()
        context = self.load(FakeClient(completed=completed))
        self.run_quietly(orchestrator._send_digest, context, self._week(context, 1), False)
        body = self.sent[0][1].lower()
        for word in ("overdue", "late", "behind", "missed"):
            self.assertNotIn(word, body)


class TestNudge(OrchestratorCase):

    def _overdue_setup(self, log=(), completed=frozenset()):
        """A client where week 1 exists in Airtable and is now long past."""
        client = FakeClient(generated=[1], log=log, completed=completed)
        context = self.load(client)
        record_ids = {
            (item.week.number, item.chore.key, item.assignee): "recA%d" % index
            for index, item in enumerate(context.scheduled)
            if item.week.number == 1
        }
        client._record_ids = record_ids
        return client, _resnapshot(client, context)

    def test_nothing_overdue_before_the_due_date(self):
        client, context = self._overdue_setup()
        now = datetime(2026, 9, 29, 7, 0, tzinfo=CHICAGO)
        self.run_quietly(orchestrator._nudge, context, now, False)
        self.assertEqual(self.sent, [])
        self.assertIn("nothing overdue", self.output.lower())

    def test_overdue_sends_one_first_nudge_each(self):
        client, context = self._overdue_setup()
        now = datetime(2026, 10, 5, 7, 0, tzinfo=CHICAGO)
        self.run_quietly(orchestrator._nudge, context, now, False)
        self.assertEqual(len(self.sent), 8)
        self.assertTrue(all(len(to) == 1 for _, _, to, _, _ in self.sent))

    def test_each_nudge_goes_only_to_its_assignee(self):
        client, context = self._overdue_setup()
        now = datetime(2026, 10, 5, 7, 0, tzinfo=CHICAGO)
        self.run_quietly(orchestrator._nudge, context, now, False)
        week1 = {
            (item.name, item.assignee.email)
            for item in context.scheduled
            if item.week.number == 1
        }
        for subject, body, to, _, _ in self.sent:
            matches = [email for name, email in week1 if name in subject or name in body]
            self.assertIn(to[0], matches)

    def test_nudges_carry_the_sender_name(self):
        """A reminder must not look like one housemate chasing another."""
        from config.sender import SENDER_NAME
        client, context = self._overdue_setup()
        now = datetime(2026, 10, 5, 7, 0, tzinfo=CHICAGO)
        self.run_quietly(orchestrator._nudge, context, now, False)
        self.assertTrue(all(name == SENDER_NAME for _, _, _, name, _ in self.sent))

    def test_every_nudge_is_recorded(self):
        client, context = self._overdue_setup()
        now = datetime(2026, 10, 5, 7, 0, tzinfo=CHICAGO)
        self.run_quietly(orchestrator._nudge, context, now, False)
        self.assertEqual(len(client.recorded), 8)
        self.assertTrue(all(kind == nudge.FIRST for _, kind, _ in client.recorded))
        self.assertTrue(all(when == now for _, _, when in client.recorded))

    def test_completed_assignments_are_not_nudged(self):
        client, context = self._overdue_setup()
        done = frozenset(
            (item.week.number, item.chore.key, item.assignee)
            for item in context.scheduled
            if item.week.number == 1
        )
        client._completed = done
        context = _resnapshot(client, context)
        now = datetime(2026, 10, 5, 7, 0, tzinfo=CHICAGO)
        self.run_quietly(orchestrator._nudge, context, now, False)
        self.assertEqual(self.sent, [])

    def test_a_logged_first_nudge_is_not_repeated(self):
        client, context = self._overdue_setup()
        first_sent = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
        log = [
            nudge.NudgeRecord(
                week_number=item.week.number,
                chore_key=item.chore.key,
                assignee=item.assignee,
                nudge_type=nudge.FIRST,
                sent_at=first_sent,
            )
            for item in context.scheduled
            if item.week.number == 1
        ]
        client._log = log
        context = _resnapshot(client, context)
        # 24 hours later: too early for the follow-up, and the first is spent.
        self.run_quietly(
            orchestrator._nudge, context, first_sent + timedelta(hours=24), False
        )
        self.assertEqual(self.sent, [])

    def test_follow_up_goes_out_after_forty_eight_hours(self):
        client, context = self._overdue_setup()
        first_sent = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
        client._log = [
            nudge.NudgeRecord(
                week_number=item.week.number,
                chore_key=item.chore.key,
                assignee=item.assignee,
                nudge_type=nudge.FIRST,
                sent_at=first_sent,
            )
            for item in context.scheduled
            if item.week.number == 1
        ]
        context = _resnapshot(client, context)
        self.run_quietly(
            orchestrator._nudge, context, first_sent + timedelta(hours=49), False
        )
        self.assertEqual(len(self.sent), 8)
        self.assertTrue(all(kind == nudge.FOLLOWUP for _, kind, _ in client.recorded))

    def test_there_is_never_a_third(self):
        client, context = self._overdue_setup()
        first_sent = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
        client._log = [
            nudge.NudgeRecord(
                week_number=item.week.number,
                chore_key=item.chore.key,
                assignee=item.assignee,
                nudge_type=kind,
                sent_at=first_sent,
            )
            for item in context.scheduled
            if item.week.number == 1
            for kind in (nudge.FIRST, nudge.FOLLOWUP)
        ]
        context = _resnapshot(client, context)
        self.run_quietly(
            orchestrator._nudge, context, first_sent + timedelta(days=30), False
        )
        self.assertEqual(self.sent, [])

    def test_dry_run_sends_nothing_and_records_nothing(self):
        client, context = self._overdue_setup()
        now = datetime(2026, 10, 5, 7, 0, tzinfo=CHICAGO)
        self.run_quietly(orchestrator._nudge, context, now, True)
        self.assertEqual(self.sent, [])
        self.assertEqual(client.recorded, [])
        self.assertIn("would send", self.output)

    def test_an_ungenerated_week_is_skipped_not_crashed(self):
        client, context = self._overdue_setup()
        client._record_ids = {}
        context = _resnapshot(client, context)
        now = datetime(2026, 10, 5, 7, 0, tzinfo=CHICAGO)
        self.run_quietly(orchestrator._nudge, context, now, False)
        self.assertEqual(self.sent, [])
        self.assertEqual(client.recorded, [])
        self.assertIn("no Airtable row", self.output)


class TestClientConstruction(unittest.TestCase):

    def setUp(self):
        self._saved = {
            name: os.environ.get(name)
            for name in (orchestrator.API_KEY_ENV, orchestrator.BASE_ID_ENV)
        }

    def tearDown(self):
        for name, value in self._saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_missing_key_raises_naming_it(self):
        os.environ.pop(orchestrator.API_KEY_ENV, None)
        os.environ[orchestrator.BASE_ID_ENV] = "appX"
        with self.assertRaises(RuntimeError) as ctx:
            orchestrator._client()
        self.assertIn(orchestrator.API_KEY_ENV, str(ctx.exception))

    def test_missing_base_id_raises_naming_it(self):
        os.environ[orchestrator.API_KEY_ENV] = "patX"
        os.environ.pop(orchestrator.BASE_ID_ENV, None)
        with self.assertRaises(RuntimeError) as ctx:
            orchestrator._client()
        self.assertIn(orchestrator.BASE_ID_ENV, str(ctx.exception))

    def test_blank_counts_as_missing(self):
        os.environ[orchestrator.API_KEY_ENV] = "  "
        os.environ[orchestrator.BASE_ID_ENV] = "appX"
        with self.assertRaises(RuntimeError):
            orchestrator._client()


if __name__ == "__main__":
    unittest.main()
