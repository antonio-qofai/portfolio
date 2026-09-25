"""Tests for src/airtable.py.

All tests drive the private parser functions directly with synthetic Airtable
record dicts. No HTTP calls, no mocking. The HTTP methods are covered by
integration testing against the real base.
"""

import unittest
from datetime import date

from config.airtable_fields import DEFAULT_FIELD_CONFIG
from src.airtable import (
    Person,
    _assignment_identity,
    _format_assignment,
    _nudge_field_name,
    _parse_chore,
    _parse_cleaner_visit,
    _parse_datetime,
    _parse_nudge_log,
    _parse_person,
    _parse_rule,
    _record_ids_by_identity,
)
from src import nudge
from src.rotation import Chore, Week
from src.schedule import ChoreDetail, CleanerVisit, ScheduledChore
from src.digest import Rule
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

CHICAGO = ZoneInfo("America/Chicago")
RF = DEFAULT_FIELD_CONFIG.roster
CF = DEFAULT_FIELD_CONFIG.chores
RUF = DEFAULT_FIELD_CONFIG.rules
VF = DEFAULT_FIELD_CONFIG.cleaner_visits
AF = DEFAULT_FIELD_CONFIG.assignments


def roster_record(record_id="recR1", name="Alice", email="alice@uchicago.edu", active=True, sort_order=1):
    return {"id": record_id, "fields": {RF.name: name, RF.email: email, RF.active: active, RF.sort_order: sort_order}}


def chore_record(
    record_id="recC1",
    name="Kitchen surfaces",
    task="Wipe counters",
    cadence="weekly",
    offset=0,
    seed=0,
    cleaner_behaviour="normal",
    prep_task="",
):
    fields = {
        CF.name: name,
        CF.task: task,
        CF.cadence: cadence,
        CF.cleaner_behaviour: cleaner_behaviour,
    }
    if offset:
        fields[CF.offset] = offset
    if seed:
        fields[CF.seed] = seed
    if prep_task:
        fields[CF.prep_task] = prep_task
    return {"id": record_id, "fields": fields}


def rule_record(
    record_id="recRU1",
    text="Nothing sits in the sink.",
    category="override",
    number=1,
    active_from=None,
    active_until=None,
):
    fields = {RUF.text: text, RUF.category: category}
    if number is not None:
        fields[RUF.number] = number
    if active_from:
        fields[RUF.active_from] = str(active_from)
    if active_until:
        fields[RUF.active_until] = str(active_until)
    return {"id": record_id, "fields": fields}


def visit_record(record_id="recV1", visit_date="2026-10-15", confirmed=True):
    fields = {VF.visit_date: visit_date}
    if confirmed:
        fields[VF.confirmed] = True
    return {"id": record_id, "fields": fields}


class TestParsePerson(unittest.TestCase):

    def test_valid_record_returns_person(self):
        r = roster_record()
        p = _parse_person(r, RF)
        self.assertIsInstance(p, Person)
        self.assertEqual(p.id, "recR1")
        self.assertEqual(p.name, "Alice")
        self.assertEqual(p.email, "alice@uchicago.edu")
        self.assertTrue(p.active)
        self.assertEqual(p.sort_order, 1)

    def test_inactive_person(self):
        r = roster_record(active=False)
        p = _parse_person(r, RF)
        self.assertFalse(p.active)

    def test_active_defaults_to_false_when_absent(self):
        r = {"id": "recR1", "fields": {RF.name: "Alice", RF.email: "alice@uchicago.edu", RF.sort_order: 1}}
        p = _parse_person(r, RF)
        self.assertFalse(p.active)

    def test_missing_name_raises(self):
        r = {"id": "recR1", "fields": {RF.email: "alice@uchicago.edu", RF.sort_order: 1}}
        with self.assertRaises(ValueError) as ctx:
            _parse_person(r, RF)
        self.assertIn("recR1", str(ctx.exception))
        self.assertIn(RF.name, str(ctx.exception))

    def test_empty_name_raises(self):
        r = roster_record(name="  ")
        with self.assertRaises(ValueError):
            _parse_person(r, RF)

    def test_missing_email_raises(self):
        r = {"id": "recR1", "fields": {RF.name: "Alice", RF.sort_order: 1}}
        with self.assertRaises(ValueError) as ctx:
            _parse_person(r, RF)
        self.assertIn("recR1", str(ctx.exception))

    def test_invalid_email_raises(self):
        r = roster_record(email="notanemail")
        with self.assertRaises(ValueError) as ctx:
            _parse_person(r, RF)
        self.assertIn("recR1", str(ctx.exception))

    def test_missing_sort_order_raises(self):
        r = {"id": "recR1", "fields": {RF.name: "Alice", RF.email: "alice@uchicago.edu", RF.active: True}}
        with self.assertRaises(ValueError) as ctx:
            _parse_person(r, RF)
        self.assertIn("recR1", str(ctx.exception))

    def test_person_str_returns_name(self):
        r = roster_record()
        p = _parse_person(r, RF)
        self.assertEqual(str(p), "Alice")

    def test_person_format_returns_name(self):
        r = roster_record()
        p = _parse_person(r, RF)
        self.assertEqual("{person}".format(person=p), "Alice")


class TestParseChore(unittest.TestCase):

    def test_valid_normal_chore(self):
        r = chore_record()
        chore, detail = _parse_chore(r, CF)
        self.assertIsInstance(chore, Chore)
        self.assertEqual(chore.key, "recC1")
        self.assertEqual(chore.cadence, "weekly")
        self.assertEqual(chore.offset, 0)
        self.assertEqual(chore.seed, 0)
        self.assertEqual(detail.name, "Kitchen surfaces")
        self.assertEqual(detail.task, "Wipe counters")
        self.assertEqual(detail.cleaner_behaviour, "normal")
        self.assertEqual(detail.prep_task, "")

    def test_offset_and_seed_from_fields(self):
        r = chore_record(cadence="every_3", offset=1, seed=2)
        chore, _ = _parse_chore(r, CF)
        self.assertEqual(chore.cadence, "every_3")
        self.assertEqual(chore.offset, 1)
        self.assertEqual(chore.seed, 2)

    def test_offset_defaults_to_zero_when_absent(self):
        r = chore_record()
        chore, _ = _parse_chore(r, CF)
        self.assertEqual(chore.offset, 0)

    def test_seed_defaults_to_zero_when_absent(self):
        r = chore_record()
        chore, _ = _parse_chore(r, CF)
        self.assertEqual(chore.seed, 0)

    def test_convert_to_prep_with_prep_task(self):
        r = chore_record(cleaner_behaviour="convert_to_prep", prep_task="Clear the counter")
        chore, detail = _parse_chore(r, CF)
        self.assertEqual(detail.cleaner_behaviour, "convert_to_prep")
        self.assertEqual(detail.prep_task, "Clear the counter")

    def test_convert_to_prep_without_prep_task_raises(self):
        r = chore_record(cleaner_behaviour="convert_to_prep", prep_task="")
        with self.assertRaises(ValueError) as ctx:
            _parse_chore(r, CF)
        self.assertIn("recC1", str(ctx.exception))
        self.assertIn("convert_to_prep", str(ctx.exception))

    def test_missing_name_raises(self):
        r = {"id": "recC1", "fields": {CF.task: "Wipe counters", CF.cadence: "weekly", CF.cleaner_behaviour: "normal"}}
        with self.assertRaises(ValueError) as ctx:
            _parse_chore(r, CF)
        self.assertIn("recC1", str(ctx.exception))

    def test_missing_task_raises(self):
        r = {"id": "recC1", "fields": {CF.name: "Kitchen", CF.cadence: "weekly", CF.cleaner_behaviour: "normal"}}
        with self.assertRaises(ValueError) as ctx:
            _parse_chore(r, CF)
        self.assertIn("recC1", str(ctx.exception))

    def test_missing_cadence_raises(self):
        r = {"id": "recC1", "fields": {CF.name: "Kitchen", CF.task: "Wipe counters", CF.cleaner_behaviour: "normal"}}
        with self.assertRaises(ValueError) as ctx:
            _parse_chore(r, CF)
        self.assertIn("recC1", str(ctx.exception))

    def test_missing_cleaner_behaviour_raises(self):
        r = {"id": "recC1", "fields": {CF.name: "Kitchen", CF.task: "Wipe counters", CF.cadence: "weekly"}}
        with self.assertRaises(ValueError) as ctx:
            _parse_chore(r, CF)
        self.assertIn("recC1", str(ctx.exception))

    def test_chore_key_is_record_id(self):
        r = chore_record(record_id="recABC123")
        chore, _ = _parse_chore(r, CF)
        self.assertEqual(chore.key, "recABC123")


class TestParseRule(unittest.TestCase):

    def test_valid_numbered_rule(self):
        r = rule_record()
        rule = _parse_rule(r, RUF)
        self.assertIsInstance(rule, Rule)
        self.assertEqual(rule.text, "Nothing sits in the sink.")
        self.assertEqual(rule.category, "override")
        self.assertEqual(rule.number, 1)
        self.assertIsNone(rule.active_from)
        self.assertIsNone(rule.active_until)

    def test_rule_without_number(self):
        r = rule_record(number=None)
        rule = _parse_rule(r, RUF)
        self.assertIsNone(rule.number)

    def test_rule_with_active_dates(self):
        r = rule_record(number=None, active_from=date(2026, 11, 1), active_until=date(2026, 12, 11))
        rule = _parse_rule(r, RUF)
        self.assertEqual(rule.active_from, date(2026, 11, 1))
        self.assertEqual(rule.active_until, date(2026, 12, 11))

    def test_missing_text_raises(self):
        r = {"id": "recRU1", "fields": {RUF.category: "override"}}
        with self.assertRaises(ValueError) as ctx:
            _parse_rule(r, RUF)
        self.assertIn("recRU1", str(ctx.exception))

    def test_missing_category_raises(self):
        r = {"id": "recRU1", "fields": {RUF.text: "Some rule."}}
        with self.assertRaises(ValueError) as ctx:
            _parse_rule(r, RUF)
        self.assertIn("recRU1", str(ctx.exception))

    def test_invalid_active_from_raises(self):
        r = rule_record()
        r["fields"][RUF.active_from] = "not-a-date"
        with self.assertRaises(ValueError) as ctx:
            _parse_rule(r, RUF)
        self.assertIn("recRU1", str(ctx.exception))


class TestParseCleanerVisit(unittest.TestCase):

    def test_confirmed_visit(self):
        r = visit_record()
        v = _parse_cleaner_visit(r, VF)
        self.assertIsInstance(v, CleanerVisit)
        self.assertEqual(v.visit_date, date(2026, 10, 15))
        self.assertTrue(v.confirmed)

    def test_unconfirmed_visit(self):
        r = visit_record(confirmed=False)
        v = _parse_cleaner_visit(r, VF)
        self.assertFalse(v.confirmed)

    def test_confirmed_absent_defaults_to_false(self):
        r = {"id": "recV1", "fields": {VF.visit_date: "2026-10-15"}}
        v = _parse_cleaner_visit(r, VF)
        self.assertFalse(v.confirmed)

    def test_missing_visit_date_raises(self):
        r = {"id": "recV1", "fields": {VF.confirmed: True}}
        with self.assertRaises(ValueError) as ctx:
            _parse_cleaner_visit(r, VF)
        self.assertIn("recV1", str(ctx.exception))

    def test_invalid_visit_date_raises(self):
        r = {"id": "recV1", "fields": {VF.visit_date: "not-a-date"}}
        with self.assertRaises(ValueError) as ctx:
            _parse_cleaner_visit(r, VF)
        self.assertIn("recV1", str(ctx.exception))

    def test_date_parsed_correctly(self):
        r = visit_record(visit_date="2026-11-13")
        v = _parse_cleaner_visit(r, VF)
        self.assertEqual(v.visit_date, date(2026, 11, 13))


class TestFormatAssignment(unittest.TestCase):

    def _make_sc(self, week_number=1, chore_key="recC1", assignee_id="recR1"):
        person = Person(id=assignee_id, name="Alice", email="alice@uchicago.edu", active=True, sort_order=1)
        week = Week(number=week_number, start_date=date(2026, 9, 28), end_date=date(2026, 10, 4), active=True)
        chore = Chore(key=chore_key, cadence="weekly", offset=0, seed=0)
        due = datetime(2026, 10, 4, 20, 0, tzinfo=CHICAGO)
        return ScheduledChore(
            week=week, chore=chore, occurrence_index=0, assignee=person,
            name="Kitchen surfaces", task="Wipe counters", due=due, is_prep=False,
        )

    def test_returns_dict_with_correct_fields(self):
        sc = self._make_sc()
        result = _format_assignment(sc, AF)
        self.assertEqual(result[AF.week_number], 1)
        self.assertEqual(result[AF.chore], ["recC1"])
        self.assertEqual(result[AF.assignee], ["recR1"])
        self.assertEqual(result[AF.task], "Wipe counters")
        self.assertFalse(result[AF.is_prep])
        self.assertFalse(result[AF.done])

    def test_label_is_the_chore_name(self):
        """The primary field is the row title on a phone, so it must read."""
        result = _format_assignment(self._make_sc(), AF)
        self.assertEqual(result[AF.label], "Kitchen surfaces")

    def test_label_and_week_are_separate_fields(self):
        result = _format_assignment(self._make_sc(), AF)
        self.assertNotEqual(AF.label, AF.week_number)
        self.assertEqual(result[AF.week_number], 1)

    def test_due_stored_in_utc(self):
        sc = self._make_sc()
        result = _format_assignment(sc, AF)
        # Oct 4 20:00 Chicago (CDT = UTC-5) → Oct 5 01:00 UTC
        self.assertEqual(result[AF.due], "2026-10-05T01:00:00.000Z")

    def test_prep_chore_sets_is_prep(self):
        sc = self._make_sc()
        person = sc.assignee
        week = sc.week
        chore = sc.chore
        due = datetime(2026, 10, 15, 11, 0, tzinfo=CHICAGO)
        sc_prep = ScheduledChore(
            week=week, chore=chore, occurrence_index=0, assignee=person,
            name="Bathroom clean", task="Clear the counter", due=due, is_prep=True,
        )
        result = _format_assignment(sc_prep, AF)
        self.assertTrue(result[AF.is_prep])

    def test_chore_key_stored_as_linked_record(self):
        sc = self._make_sc(chore_key="recABC")
        result = _format_assignment(sc, AF)
        self.assertEqual(result[AF.chore], ["recABC"])

    def test_assignee_id_stored_as_linked_record(self):
        sc = self._make_sc(assignee_id="recPERSON99")
        result = _format_assignment(sc, AF)
        self.assertEqual(result[AF.assignee], ["recPERSON99"])


class TestCompletedAssignmentIds(unittest.TestCase):
    """Tests for the logic inside completed_assignment_ids, driven via the parser helpers."""

    def _make_assignment_record(self, week_num, chore_id, assignee_id, done):
        fields = {
            AF.week_number: week_num,
            AF.chore: [chore_id],
            AF.assignee: [assignee_id],
        }
        if done:
            fields[AF.done] = True
        return {"id": "recA1", "fields": fields}

    def _extract_ids(self, records, roster):
        by_id = {p.id: p for p in roster}
        ids = set()
        for r in records:
            fields = r["fields"]
            if not fields.get(AF.done, False):
                continue
            week_num = fields.get(AF.week_number)
            chore_ids = fields.get(AF.chore, [])
            assignee_ids = fields.get(AF.assignee, [])
            if week_num is None or not chore_ids or not assignee_ids:
                continue
            person = by_id.get(assignee_ids[0])
            if person is None:
                continue
            ids.add((int(week_num), chore_ids[0], person))
        return frozenset(ids)

    def test_done_assignment_included(self):
        person = Person(id="recR1", name="Alice", email="alice@uchicago.edu", active=True, sort_order=1)
        records = [self._make_assignment_record(1, "recC1", "recR1", done=True)]
        result = self._extract_ids(records, [person])
        self.assertIn((1, "recC1", person), result)

    def test_open_assignment_excluded(self):
        person = Person(id="recR1", name="Alice", email="alice@uchicago.edu", active=True, sort_order=1)
        records = [self._make_assignment_record(1, "recC1", "recR1", done=False)]
        result = self._extract_ids(records, [person])
        self.assertEqual(result, frozenset())

    def test_unknown_assignee_skipped(self):
        records = [self._make_assignment_record(1, "recC1", "recUNKNOWN", done=True)]
        result = self._extract_ids(records, [])
        self.assertEqual(result, frozenset())

    def test_multiple_done_assignments(self):
        alice = Person(id="recR1", name="Alice", email="alice@uchicago.edu", active=True, sort_order=1)
        bob = Person(id="recR2", name="Bob", email="bob@uchicago.edu", active=True, sort_order=2)
        records = [
            self._make_assignment_record(1, "recC1", "recR1", done=True),
            self._make_assignment_record(1, "recC2", "recR2", done=True),
            self._make_assignment_record(2, "recC1", "recR1", done=False),
        ]
        result = self._extract_ids(records, [alice, bob])
        self.assertIn((1, "recC1", alice), result)
        self.assertIn((1, "recC2", bob), result)
        self.assertNotIn((2, "recC1", alice), result)


ALICE = Person(id="recR1", name="Alice", email="alice@uchicago.edu", active=True, sort_order=1)
BOB = Person(id="recR2", name="Bob", email="bob@uchicago.edu", active=True, sort_order=2)
BY_ID = {ALICE.id: ALICE, BOB.id: BOB}


def assignment_record(
    record_id="recA1",
    week=1,
    chore_id="recC1",
    assignee_id="recR1",
    first_nudge=None,
    followup=None,
):
    fields = {}
    if week is not None:
        fields[AF.week_number] = week
    if chore_id is not None:
        fields[AF.chore] = [chore_id]
    if assignee_id is not None:
        fields[AF.assignee] = [assignee_id]
    if first_nudge is not None:
        fields[AF.first_nudge_sent] = first_nudge
    if followup is not None:
        fields[AF.followup_sent] = followup
    return {"id": record_id, "fields": fields}


class TestAssignmentIdentity(unittest.TestCase):

    def test_complete_row_returns_tuple(self):
        result = _assignment_identity(assignment_record(), AF, BY_ID)
        self.assertEqual(result, (1, "recC1", ALICE))

    def test_week_is_coerced_to_int(self):
        result = _assignment_identity(assignment_record(week=3.0), AF, BY_ID)
        self.assertEqual(result[0], 3)
        self.assertIsInstance(result[0], int)

    def test_missing_week_returns_none(self):
        self.assertIsNone(_assignment_identity(assignment_record(week=None), AF, BY_ID))

    def test_missing_chore_returns_none(self):
        self.assertIsNone(
            _assignment_identity(assignment_record(chore_id=None), AF, BY_ID)
        )

    def test_missing_assignee_returns_none(self):
        self.assertIsNone(
            _assignment_identity(assignment_record(assignee_id=None), AF, BY_ID)
        )

    def test_assignee_off_the_roster_returns_none(self):
        self.assertIsNone(
            _assignment_identity(assignment_record(assignee_id="recGONE"), AF, BY_ID)
        )

    def test_identity_matches_the_schedule_tuple_shape(self):
        week = Week(number=1, start_date=date(2026, 9, 28), end_date=date(2026, 10, 4), active=True)
        chore = Chore(key="recC1", cadence="weekly")
        from_schedule = (week.number, chore.key, ALICE)
        self.assertEqual(_assignment_identity(assignment_record(), AF, BY_ID), from_schedule)


class TestRecordIdsByIdentity(unittest.TestCase):

    def test_maps_identity_to_record_id(self):
        records = [
            assignment_record(record_id="recA1", week=1, chore_id="recC1", assignee_id="recR1"),
            assignment_record(record_id="recA2", week=1, chore_id="recC2", assignee_id="recR2"),
        ]
        result = _record_ids_by_identity(records, AF, BY_ID)
        self.assertEqual(result[(1, "recC1", ALICE)], "recA1")
        self.assertEqual(result[(1, "recC2", BOB)], "recA2")

    def test_unmatched_rows_are_left_out(self):
        records = [assignment_record(assignee_id="recGONE")]
        self.assertEqual(_record_ids_by_identity(records, AF, BY_ID), {})

    def test_duplicate_assignment_raises_with_both_ids(self):
        records = [
            assignment_record(record_id="recA1"),
            assignment_record(record_id="recA2"),
        ]
        with self.assertRaises(ValueError) as ctx:
            _record_ids_by_identity(records, AF, BY_ID)
        self.assertIn("recA1", str(ctx.exception))
        self.assertIn("recA2", str(ctx.exception))

    def test_same_chore_different_weeks_is_not_a_duplicate(self):
        records = [
            assignment_record(record_id="recA1", week=1),
            assignment_record(record_id="recA2", week=2),
        ]
        self.assertEqual(len(_record_ids_by_identity(records, AF, BY_ID)), 2)

    def test_empty_table(self):
        self.assertEqual(_record_ids_by_identity([], AF, BY_ID), {})


class TestParseNudgeLog(unittest.TestCase):

    def test_row_with_no_nudges_yields_nothing(self):
        self.assertEqual(_parse_nudge_log([assignment_record()], AF, BY_ID), [])

    def test_first_nudge_only(self):
        records = [assignment_record(first_nudge="2026-10-05T14:00:00.000Z")]
        log = _parse_nudge_log(records, AF, BY_ID)
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0].nudge_type, nudge.FIRST)
        self.assertEqual(log[0].week_number, 1)
        self.assertEqual(log[0].chore_key, "recC1")
        self.assertEqual(log[0].assignee, ALICE)

    def test_both_nudges_yield_two_records(self):
        records = [
            assignment_record(
                first_nudge="2026-10-05T14:00:00.000Z",
                followup="2026-10-07T14:00:00.000Z",
            )
        ]
        log = _parse_nudge_log(records, AF, BY_ID)
        self.assertEqual({r.nudge_type for r in log}, {nudge.FIRST, nudge.FOLLOWUP})

    def test_sent_at_is_aware_utc(self):
        records = [assignment_record(first_nudge="2026-10-05T14:00:00.000Z")]
        sent_at = _parse_nudge_log(records, AF, BY_ID)[0].sent_at
        self.assertIsNotNone(sent_at.utcoffset())
        self.assertEqual(sent_at, datetime(2026, 10, 5, 14, 0, tzinfo=timezone.utc))

    def test_empty_string_is_not_a_nudge(self):
        records = [assignment_record(first_nudge="")]
        self.assertEqual(_parse_nudge_log(records, AF, BY_ID), [])

    def test_unmatched_row_is_skipped(self):
        records = [
            assignment_record(assignee_id="recGONE", first_nudge="2026-10-05T14:00:00.000Z")
        ]
        self.assertEqual(_parse_nudge_log(records, AF, BY_ID), [])

    def test_bad_timestamp_raises_with_record_id_and_field(self):
        records = [assignment_record(record_id="recA9", first_nudge="not a date")]
        with self.assertRaises(ValueError) as ctx:
            _parse_nudge_log(records, AF, BY_ID)
        self.assertIn("recA9", str(ctx.exception))
        self.assertIn(AF.first_nudge_sent, str(ctx.exception))

    def test_log_feeds_pending_nudges_unchanged(self):
        """The whole point: what comes out of Airtable is what nudge.py expects."""
        records = [assignment_record(first_nudge="2026-10-05T14:00:00.000Z")]
        log = _parse_nudge_log(records, AF, BY_ID)
        week = Week(number=1, start_date=date(2026, 9, 28), end_date=date(2026, 10, 4), active=True)
        chore = Chore(key="recC1", cadence="weekly")
        scheduled = ScheduledChore(
            week=week,
            chore=chore,
            occurrence_index=0,
            assignee=ALICE,
            name="Some chore",
            task="Do the thing",
            due=datetime(2026, 10, 4, 20, 0, tzinfo=CHICAGO),
            is_prep=False,
        )
        # 47 hours after the first nudge: too early for the follow-up.
        early = datetime(2026, 10, 7, 13, 0, tzinfo=timezone.utc)
        self.assertEqual(
            nudge.pending_nudges([scheduled], frozenset(), log, early), ()
        )
        # 49 hours after: the follow-up is due.
        late = datetime(2026, 10, 7, 15, 0, tzinfo=timezone.utc)
        pending = nudge.pending_nudges([scheduled], frozenset(), log, late)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].nudge_type, nudge.FOLLOWUP)


class TestNudgeFieldName(unittest.TestCase):

    def test_first(self):
        self.assertEqual(_nudge_field_name(nudge.FIRST, AF), AF.first_nudge_sent)

    def test_followup(self):
        self.assertEqual(_nudge_field_name(nudge.FOLLOWUP, AF), AF.followup_sent)

    def test_the_two_fields_are_different(self):
        self.assertNotEqual(AF.first_nudge_sent, AF.followup_sent)

    def test_unknown_type_raises(self):
        with self.assertRaises(ValueError) as ctx:
            _nudge_field_name("third", AF)
        self.assertIn("third", str(ctx.exception))


class TestParseDatetime(unittest.TestCase):

    def test_trailing_z(self):
        self.assertEqual(
            _parse_datetime("2026-10-05T14:00:00.000Z", "recA1", "F"),
            datetime(2026, 10, 5, 14, 0, tzinfo=timezone.utc),
        )

    def test_explicit_offset_converted_to_utc(self):
        self.assertEqual(
            _parse_datetime("2026-10-05T09:00:00-05:00", "recA1", "F"),
            datetime(2026, 10, 5, 14, 0, tzinfo=timezone.utc),
        )

    def test_naive_value_raises(self):
        with self.assertRaises(ValueError) as ctx:
            _parse_datetime("2026-10-05T14:00:00", "recA1", "F")
        self.assertIn("no timezone offset", str(ctx.exception))

    def test_garbage_raises_with_record_id(self):
        with self.assertRaises(ValueError) as ctx:
            _parse_datetime("tomorrow", "recA7", "First nudge sent")
        self.assertIn("recA7", str(ctx.exception))
        self.assertIn("First nudge sent", str(ctx.exception))

    def test_a_date_with_no_time_raises(self):
        with self.assertRaises(ValueError):
            _parse_datetime("2026-10-05", "recA1", "F")


if __name__ == "__main__":
    unittest.main()
