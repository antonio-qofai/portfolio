"""Tests for src/nudge.py.

All nudge logic is pure, so tests drive it directly with explicit inputs.
now is always a timezone-aware America/Chicago datetime. The DT helper
constructs them without repetition.
"""

import unittest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from config.nudge_copy import DEFAULT_NUDGE_COPY
from src.nudge import (
    FIRST,
    FOLLOWUP,
    FOLLOWUP_DELAY,
    NudgeEmail,
    NudgeRecord,
    PendingNudge,
    pending_nudges,
    render_nudge,
)
from src.rotation import Chore, Week
from src.schedule import ScheduledChore

CHICAGO = ZoneInfo("America/Chicago")

# Week 1: Mon Sep 28 – Sun Oct 4, 2026.
WEEK_1 = Week(number=1, start_date=date(2026, 9, 28), end_date=date(2026, 10, 4), active=True)
WEEK_2 = Week(number=2, start_date=date(2026, 10, 5), end_date=date(2026, 10, 11), active=True)

CHORE_A = Chore(key="chore-a", cadence="weekly", offset=0, seed=0)
CHORE_B = Chore(key="chore-b", cadence="weekly", offset=0, seed=1)

# Normal due time for week 1: Sunday Oct 4 at 20:00 Chicago.
DUE_W1 = datetime(2026, 10, 4, 20, 0, tzinfo=CHICAGO)
DUE_W2 = datetime(2026, 10, 11, 20, 0, tzinfo=CHICAGO)


def DT(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=CHICAGO)


def make_sc(week=WEEK_1, chore=CHORE_A, assignee="alice", due=DUE_W1, name="Test chore", task="Do the thing", is_prep=False):
    return ScheduledChore(
        week=week,
        chore=chore,
        occurrence_index=0,
        assignee=assignee,
        name=name,
        task=task,
        due=due,
        is_prep=is_prep,
    )


def first_record(week_number, chore_key, assignee, sent_at):
    return NudgeRecord(week_number=week_number, chore_key=chore_key, assignee=assignee, nudge_type=FIRST, sent_at=sent_at)


def followup_record(week_number, chore_key, assignee, sent_at):
    return NudgeRecord(week_number=week_number, chore_key=chore_key, assignee=assignee, nudge_type=FOLLOWUP, sent_at=sent_at)


class TestPendingNudgesBasicRules(unittest.TestCase):

    def test_past_due_no_log_gives_first_nudge(self):
        sc = make_sc()
        now = DT(2026, 10, 5, 8)
        result = pending_nudges([sc], frozenset(), [], now)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].nudge_type, FIRST)
        self.assertIs(result[0].scheduled_chore, sc)

    def test_returns_tuple(self):
        result = pending_nudges([], frozenset(), [], DT(2026, 10, 5, 8))
        self.assertIsInstance(result, tuple)

    def test_empty_schedule_returns_empty(self):
        result = pending_nudges([], frozenset(), [], DT(2026, 10, 5, 8))
        self.assertEqual(result, ())

    def test_not_yet_past_due_no_nudge(self):
        sc = make_sc(due=DUE_W1)
        now = DT(2026, 10, 4, 19, 59)
        result = pending_nudges([sc], frozenset(), [], now)
        self.assertEqual(result, ())

    def test_exactly_at_due_no_nudge(self):
        sc = make_sc(due=DUE_W1)
        result = pending_nudges([sc], frozenset(), [], DUE_W1)
        self.assertEqual(result, ())

    def test_completed_assignment_not_nudged(self):
        sc = make_sc()
        now = DT(2026, 10, 5, 8)
        completed = frozenset({(1, "chore-a", "alice")})
        result = pending_nudges([sc], completed, [], now)
        self.assertEqual(result, ())

    def test_completed_check_uses_week_chore_assignee(self):
        sc = make_sc(week=WEEK_2, due=DUE_W2)
        now = DT(2026, 10, 12, 8)
        # Mark week 1 completed, not week 2 — week 2 should still nudge.
        completed = frozenset({(1, "chore-a", "alice")})
        result = pending_nudges([sc], completed, [], now)
        self.assertEqual(len(result), 1)


class TestPendingNudgesFollowupLogic(unittest.TestCase):

    def test_first_nudge_less_than_48h_ago_no_followup(self):
        sc = make_sc()
        first_sent = DT(2026, 10, 5, 8)
        now = first_sent + timedelta(hours=47, minutes=59)
        log = [first_record(1, "chore-a", "alice", first_sent)]
        result = pending_nudges([sc], frozenset(), log, now)
        self.assertEqual(result, ())

    def test_first_nudge_exactly_48h_ago_gives_followup(self):
        sc = make_sc()
        first_sent = DT(2026, 10, 5, 8)
        now = first_sent + FOLLOWUP_DELAY
        log = [first_record(1, "chore-a", "alice", first_sent)]
        result = pending_nudges([sc], frozenset(), log, now)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].nudge_type, FOLLOWUP)

    def test_first_nudge_more_than_48h_ago_gives_followup(self):
        sc = make_sc()
        first_sent = DT(2026, 10, 5, 8)
        now = first_sent + timedelta(hours=72)
        log = [first_record(1, "chore-a", "alice", first_sent)]
        result = pending_nudges([sc], frozenset(), log, now)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].nudge_type, FOLLOWUP)

    def test_followup_already_sent_no_third_nudge(self):
        sc = make_sc()
        first_sent = DT(2026, 10, 5, 8)
        followup_sent = first_sent + FOLLOWUP_DELAY
        now = followup_sent + timedelta(hours=48)
        log = [
            first_record(1, "chore-a", "alice", first_sent),
            followup_record(1, "chore-a", "alice", followup_sent),
        ]
        result = pending_nudges([sc], frozenset(), log, now)
        self.assertEqual(result, ())

    def test_followup_only_in_log_no_third(self):
        # Odd state: followup logged without a first. Must not produce a third.
        sc = make_sc()
        now = DT(2026, 10, 10, 8)
        log = [followup_record(1, "chore-a", "alice", DT(2026, 10, 7, 8))]
        result = pending_nudges([sc], frozenset(), log, now)
        self.assertEqual(result, ())

    def test_multiple_first_records_uses_earliest_for_delay(self):
        # Odd state: two FIRST records. The earliest governs the 48h window.
        sc = make_sc()
        early = DT(2026, 10, 5, 0)
        late = DT(2026, 10, 5, 12)
        now = early + FOLLOWUP_DELAY  # 48h from early, only 36h from late
        log = [
            first_record(1, "chore-a", "alice", early),
            first_record(1, "chore-a", "alice", late),
        ]
        result = pending_nudges([sc], frozenset(), log, now)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].nudge_type, FOLLOWUP)


class TestPendingNudgesMultipleAssignments(unittest.TestCase):

    def test_assignments_tracked_independently(self):
        sc_a = make_sc(chore=CHORE_A, assignee="alice")
        sc_b = make_sc(chore=CHORE_B, assignee="bob")
        first_sent = DT(2026, 10, 5, 8)
        now = first_sent + FOLLOWUP_DELAY
        log = [first_record(1, "chore-a", "alice", first_sent)]
        # alice gets followup (48h passed), bob gets first (no log)
        result = pending_nudges([sc_a, sc_b], frozenset(), log, now)
        self.assertEqual(len(result), 2)
        by_chore = {r.scheduled_chore.chore.key: r.nudge_type for r in result}
        self.assertEqual(by_chore["chore-a"], FOLLOWUP)
        self.assertEqual(by_chore["chore-b"], FIRST)

    def test_one_completed_one_not(self):
        sc_a = make_sc(chore=CHORE_A, assignee="alice")
        sc_b = make_sc(chore=CHORE_B, assignee="bob")
        now = DT(2026, 10, 5, 8)
        completed = frozenset({(1, "chore-a", "alice")})
        result = pending_nudges([sc_a, sc_b], completed, [], now)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].scheduled_chore.chore.key, "chore-b")

    def test_same_chore_different_weeks_tracked_separately(self):
        sc_w1 = make_sc(week=WEEK_1, due=DUE_W1)
        sc_w2 = make_sc(week=WEEK_2, due=DUE_W2)
        now = DT(2026, 10, 12, 8)
        # First nudge only sent for week 1; week 2 should still get a first.
        log = [first_record(1, "chore-a", "alice", DT(2026, 10, 5, 8))]
        result = pending_nudges([sc_w1, sc_w2], frozenset(), log, now)
        self.assertEqual(len(result), 2)
        by_week = {r.scheduled_chore.week.number: r.nudge_type for r in result}
        self.assertEqual(by_week[1], FOLLOWUP)
        self.assertEqual(by_week[2], FIRST)

    def test_log_for_different_assignee_does_not_affect_other(self):
        sc = make_sc(assignee="alice")
        now = DT(2026, 10, 5, 8)
        # First nudge logged for bob, not alice.
        log = [first_record(1, "chore-a", "bob", DT(2026, 10, 5, 0))]
        result = pending_nudges([sc], frozenset(), log, now)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].nudge_type, FIRST)


class TestRenderNudge(unittest.TestCase):

    def _sc(self, name="Kitchen surfaces", task="Wipe counters", due=DUE_W1, is_prep=False):
        return make_sc(name=name, task=task, due=due, is_prep=is_prep)

    def test_returns_nudge_email(self):
        sc = self._sc()
        result = render_nudge(PendingNudge(sc, FIRST), DEFAULT_NUDGE_COPY)
        self.assertIsInstance(result, NudgeEmail)

    def test_first_subject_contains_name(self):
        sc = self._sc()
        result = render_nudge(PendingNudge(sc, FIRST), DEFAULT_NUDGE_COPY)
        self.assertIn("Kitchen surfaces", result.subject)

    def test_followup_subject_contains_name(self):
        sc = self._sc()
        result = render_nudge(PendingNudge(sc, FOLLOWUP), DEFAULT_NUDGE_COPY)
        self.assertIn("Kitchen surfaces", result.subject)

    def test_first_and_followup_subjects_differ(self):
        sc = self._sc()
        first = render_nudge(PendingNudge(sc, FIRST), DEFAULT_NUDGE_COPY)
        followup = render_nudge(PendingNudge(sc, FOLLOWUP), DEFAULT_NUDGE_COPY)
        self.assertNotEqual(first.subject, followup.subject)

    def test_body_contains_chore_name(self):
        sc = self._sc()
        result = render_nudge(PendingNudge(sc, FIRST), DEFAULT_NUDGE_COPY)
        self.assertIn("Kitchen surfaces", result.body)

    def test_body_contains_task_text(self):
        sc = self._sc()
        result = render_nudge(PendingNudge(sc, FIRST), DEFAULT_NUDGE_COPY)
        self.assertIn("Wipe counters", result.body)

    def test_body_contains_formatted_due_weekday(self):
        # Oct 4 2026 is a Sunday.
        sc = self._sc(due=DT(2026, 10, 4, 20))
        result = render_nudge(PendingNudge(sc, FIRST), DEFAULT_NUDGE_COPY)
        self.assertIn("Sun", result.body)

    def test_body_contains_formatted_due_month_and_day(self):
        sc = self._sc(due=DT(2026, 10, 4, 20))
        result = render_nudge(PendingNudge(sc, FIRST), DEFAULT_NUDGE_COPY)
        self.assertIn("Oct", result.body)
        self.assertIn("4", result.body)

    def test_body_contains_formatted_due_time(self):
        sc = self._sc(due=DT(2026, 10, 4, 20))
        result = render_nudge(PendingNudge(sc, FIRST), DEFAULT_NUDGE_COPY)
        self.assertIn("8pm", result.body)

    def test_body_ends_with_newline(self):
        sc = self._sc()
        result = render_nudge(PendingNudge(sc, FIRST), DEFAULT_NUDGE_COPY)
        self.assertTrue(result.body.endswith("\n"))

    def test_followup_body_ends_with_newline(self):
        sc = self._sc()
        result = render_nudge(PendingNudge(sc, FOLLOWUP), DEFAULT_NUDGE_COPY)
        self.assertTrue(result.body.endswith("\n"))

    def test_first_and_followup_bodies_differ(self):
        sc = self._sc()
        first = render_nudge(PendingNudge(sc, FIRST), DEFAULT_NUDGE_COPY)
        followup = render_nudge(PendingNudge(sc, FOLLOWUP), DEFAULT_NUDGE_COPY)
        self.assertNotEqual(first.body, followup.body)

    def test_prep_chore_nudge_uses_sc_task_field(self):
        # In a cleaner week, sc.task already holds the prep text. The nudge
        # just uses sc.task directly, so prep chores render correctly.
        sc = self._sc(name="Bathroom clean", task="Clear the counter", is_prep=True)
        result = render_nudge(PendingNudge(sc, FIRST), DEFAULT_NUDGE_COPY)
        self.assertIn("Clear the counter", result.body)

    def test_due_time_with_minutes_renders_correctly(self):
        sc = self._sc(due=DT(2026, 10, 4, 11, 30))
        result = render_nudge(PendingNudge(sc, FIRST), DEFAULT_NUDGE_COPY)
        self.assertIn("11:30am", result.body)


if __name__ == "__main__":
    unittest.main()
