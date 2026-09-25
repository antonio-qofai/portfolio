"""Tests for the rotation engine.

These cover the invariants in CLAUDE.md. The arithmetic here is the part of
the system least likely to fail loudly if it is wrong: a rotation that is
subtly uneven still produces a plausible-looking schedule every Monday and is
only noticed at the end of the quarter, by whoever got the extra work.

Chores are built with placeholder keys rather than real chore names. Nothing
in this file needs to know what any of them actually are.
"""

import os
import sys
import unittest
from collections import Counter
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.cadences import CADENCES, Cadence
from config.calendar import AUTUMN_2026, Term
from src import rotation
from src.rotation import Chore

ROSTER = ("A", "B", "C")

# The PRD's original shape: 6 weekly chores and 6 every-third-week ones.
# The live base dropped three periodics in Sept 2026 (see
# TestLiveApartmentShape below), but this denser configuration is kept as
# the main fixture because two chores sharing an offset is the harder case
# for the engine, and it must stay correct for anyone reusing this.
WEEKLY_CHORES = tuple(
    Chore(key="weekly-%d" % (i + 1), cadence="weekly", offset=0, seed=seed)
    for i, seed in enumerate((0, 0, 1, 1, 2, 2))
)
PERIODIC_CHORES = tuple(
    Chore(key="every3-%d" % (i + 1), cadence="every_3", offset=offset, seed=seed)
    for i, (offset, seed) in enumerate(
        zip((0, 0, 1, 1, 2, 2), (0, 0, 1, 1, 2, 2))
    )
)
ALL_CHORES = WEEKLY_CHORES + PERIODIC_CHORES


class TestTermCalendar(unittest.TestCase):
    def test_autumn_2026_has_eleven_weeks_nine_of_them_active(self):
        self.assertEqual(11, len(rotation.weeks(AUTUMN_2026)))
        self.assertEqual(9, len(rotation.active_weeks(AUTUMN_2026)))

    def test_weeks_are_monday_to_sunday_and_contiguous(self):
        for week in rotation.weeks(AUTUMN_2026):
            self.assertEqual(0, week.start_date.weekday(), week)
            self.assertEqual(6, week.end_date.weekday(), week)
            self.assertEqual(6, (week.end_date - week.start_date).days, week)

    def test_first_and_last_week_match_the_prd_dates(self):
        all_weeks = rotation.weeks(AUTUMN_2026)
        self.assertEqual(date(2026, 9, 28), all_weeks[0].start_date)
        self.assertEqual(date(2026, 10, 4), all_weeks[0].end_date)
        self.assertEqual(date(2026, 12, 7), all_weeks[-1].start_date)
        self.assertEqual(date(2026, 12, 13), all_weeks[-1].end_date)

    def test_weeks_nine_and_eleven_are_inactive(self):
        inactive = [w.number for w in rotation.weeks(AUTUMN_2026) if not w.active]
        self.assertEqual([9, 11], inactive)


class TestQuarterTotals(unittest.TestCase):
    """The core invariant: 72 assignments, exactly 24 per person."""

    def setUp(self):
        self.assignments = rotation.generate(
            AUTUMN_2026, ALL_CHORES, ROSTER, CADENCES
        )

    def test_full_quarter_is_72_assignments(self):
        self.assertEqual(72, len(self.assignments))

    def test_every_roommate_gets_exactly_24(self):
        counts = Counter(a.assignee for a in self.assignments)
        self.assertEqual({"A": 24, "B": 24, "C": 24}, dict(counts))

    def test_each_weekly_chore_is_done_three_times_by_each_person(self):
        for chore in WEEKLY_CHORES:
            counts = Counter(
                a.assignee for a in self.assignments if a.chore.key == chore.key
            )
            self.assertEqual({"A": 3, "B": 3, "C": 3}, dict(counts), chore.key)

    def test_each_every_3_chore_is_done_once_by_each_person(self):
        for chore in PERIODIC_CHORES:
            counts = Counter(
                a.assignee for a in self.assignments if a.chore.key == chore.key
            )
            self.assertEqual({"A": 1, "B": 1, "C": 1}, dict(counts), chore.key)

    def test_inactive_weeks_produce_no_assignments(self):
        scheduled = {a.week.number for a in self.assignments}
        self.assertNotIn(9, scheduled)
        self.assertNotIn(11, scheduled)
        self.assertEqual({1, 2, 3, 4, 5, 6, 7, 8, 10}, scheduled)

    def test_every_active_week_has_six_weekly_plus_two_periodic(self):
        for week in rotation.active_weeks(AUTUMN_2026):
            in_week = [a for a in self.assignments if a.week.number == week.number]
            weekly = [a for a in in_week if a.chore.cadence == "weekly"]
            periodic = [a for a in in_week if a.chore.cadence == "every_3"]
            self.assertEqual(6, len(weekly), week.number)
            self.assertEqual(2, len(periodic), week.number)

    def test_each_person_does_exactly_two_weekly_chores_every_week(self):
        for week in rotation.active_weeks(AUTUMN_2026):
            counts = Counter(
                a.assignee
                for a in self.assignments
                if a.week.number == week.number and a.chore.cadence == "weekly"
            )
            self.assertEqual({"A": 2, "B": 2, "C": 2}, dict(counts), week.number)


class TestOccurrences(unittest.TestCase):
    def test_every_3_offsets_land_on_the_calendar_weeks_the_prd_lists(self):
        expected = {0: [1, 4, 7], 1: [2, 5, 8], 2: [3, 6, 10]}
        for offset, calendar_weeks in expected.items():
            chore = Chore(key="c", cadence="every_3", offset=offset)
            landed = [
                week.number
                for week, _ in rotation.occurrences(chore, AUTUMN_2026, CADENCES)
            ]
            self.assertEqual(calendar_weeks, landed, offset)

    def test_occurrence_index_counts_per_chore_not_by_calendar(self):
        chore = Chore(key="c", cadence="every_3", offset=2)
        indexes = [
            index for _, index in rotation.occurrences(chore, AUTUMN_2026, CADENCES)
        ]
        self.assertEqual([0, 1, 2], indexes)

    def test_weekly_chore_occurs_in_every_active_week(self):
        chore = Chore(key="c", cadence="weekly")
        landed = [
            week.number
            for week, _ in rotation.occurrences(chore, AUTUMN_2026, CADENCES)
        ]
        self.assertEqual([1, 2, 3, 4, 5, 6, 7, 8, 10], landed)

    def test_occurrence_index_is_none_in_an_inactive_week(self):
        chore = Chore(key="c", cadence="weekly")
        week_nine = rotation.weeks(AUTUMN_2026)[8]
        self.assertEqual(9, week_nine.number)
        self.assertIsNone(
            rotation.occurrence_index(chore, week_nine, AUTUMN_2026, CADENCES)
        )


class TestRotationFormula(unittest.TestCase):
    def test_assignee_walks_the_roster_from_the_seed(self):
        chore = Chore(key="c", cadence="weekly", seed=1)
        picked = [rotation.assignee(chore, k, ROSTER) for k in range(5)]
        self.assertEqual(["B", "C", "A", "B", "C"], picked)

    def test_seed_shifts_the_whole_rotation(self):
        for seed in range(3):
            chore = Chore(key="c", cadence="weekly", seed=seed)
            self.assertEqual(ROSTER[seed], rotation.assignee(chore, 0, ROSTER))

    def test_roster_order_is_what_determines_turns(self):
        chore = Chore(key="c", cadence="weekly", seed=0)
        self.assertEqual("C", rotation.assignee(chore, 0, ("C", "A", "B")))


class TestGenerateForwardNeverRewrite(unittest.TestCase):
    def test_adding_a_chore_does_not_change_any_existing_assignment(self):
        before = rotation.generate(AUTUMN_2026, ALL_CHORES, ROSTER, CADENCES)
        added = ALL_CHORES + (Chore(key="new", cadence="weekly", seed=0),)
        after = rotation.generate(AUTUMN_2026, added, ROSTER, CADENCES)

        unchanged = [a for a in after if a.chore.key != "new"]
        self.assertEqual(list(before), unchanged)

    def test_generation_is_deterministic_so_a_rerun_writes_the_same_rows(self):
        first = rotation.generate(AUTUMN_2026, ALL_CHORES, ROSTER, CADENCES)
        second = rotation.generate(AUTUMN_2026, ALL_CHORES, ROSTER, CADENCES)
        self.assertEqual(first, second)

    def test_generate_week_matches_that_slice_of_the_full_run(self):
        full = rotation.generate(AUTUMN_2026, ALL_CHORES, ROSTER, CADENCES)
        week_four = rotation.generate_week(
            AUTUMN_2026, 4, ALL_CHORES, ROSTER, CADENCES
        )
        self.assertEqual(
            [a for a in full if a.week.number == 4], list(week_four)
        )


class TestLiveApartmentShape(unittest.TestCase):
    """The configuration actually in Airtable: 6 weekly, 3 every_3.

    Dust, Bathroom deep, and Supply run were dropped in Sept 2026 once the
    cleaner's real scope was known. The totals changed from 72 and 24 each
    to 63 and 21 each, which is fine because the invariant is that every
    chore's occurrence count divides by the roster, not that the total is
    any particular number. These tests pin the new arithmetic so a future
    edit cannot quietly break it.
    """

    WEEKLY = tuple(
        Chore(key="w%d" % i, cadence="weekly", offset=0, seed=seed)
        for i, seed in enumerate((0, 0, 1, 1, 2, 2))
    )
    # One per offset, so every week carries exactly one periodic chore.
    PERIODIC = tuple(
        Chore(key="p%d" % i, cadence="every_3", offset=i, seed=i)
        for i in range(3)
    )
    CHORES = WEEKLY + PERIODIC

    def generate(self):
        return rotation.generate(AUTUMN_2026, self.CHORES, ROSTER, CADENCES)

    def test_sixty_three_assignments(self):
        self.assertEqual(len(self.generate()), 63)

    def test_twenty_one_each(self):
        counts = Counter(a.assignee for a in self.generate())
        self.assertEqual(set(counts.values()), {21})

    def test_each_weekly_chore_three_times_per_person(self):
        assignments = self.generate()
        for chore in self.WEEKLY:
            counts = Counter(
                a.assignee for a in assignments if a.chore.key == chore.key
            )
            self.assertEqual(set(counts.values()), {3}, chore.key)

    def test_each_periodic_chore_once_per_person(self):
        assignments = self.generate()
        for chore in self.PERIODIC:
            counts = Counter(
                a.assignee for a in assignments if a.chore.key == chore.key
            )
            self.assertEqual(set(counts.values()), {1}, chore.key)

    def test_every_week_carries_seven_chores(self):
        by_week = Counter(a.week.number for a in self.generate())
        self.assertEqual(set(by_week.values()), {7})

    def test_every_week_splits_three_two_two(self):
        """One periodic per offset is what buys this. Two would make it 4/2/2."""
        assignments = self.generate()
        for week in rotation.active_weeks(AUTUMN_2026):
            counts = Counter(
                a.assignee for a in assignments if a.week.number == week.number
            )
            self.assertEqual(
                sorted(counts.values(), reverse=True), [3, 2, 2], week.number
            )

    def test_the_plan_validates(self):
        rotation.validate_plan(AUTUMN_2026, self.CHORES, ROSTER, CADENCES)

    def test_two_periodics_on_one_offset_and_seed_skews_a_week(self):
        """Why the offsets are spread. Totals survive; the week does not."""
        clashing = self.WEEKLY + (
            Chore(key="p0", cadence="every_3", offset=0, seed=0),
            Chore(key="p1", cadence="every_3", offset=0, seed=0),
        )
        assignments = rotation.generate(AUTUMN_2026, clashing, ROSTER, CADENCES)
        week_one = Counter(
            a.assignee for a in assignments if a.week.number == 1
        )
        self.assertEqual(sorted(week_one.values(), reverse=True), [4, 2, 2])
        # Still even overall, which is exactly why nothing catches it.
        rotation.validate_plan(AUTUMN_2026, clashing, ROSTER, CADENCES)


class TestDifferentApartment(unittest.TestCase):
    """Four people, twelve active weeks, no code changes."""

    TERM = Term(
        name="Test term",
        start_date=date(2027, 1, 4),
        week_count=12,
        inactive_weeks=frozenset(),
    )
    ROSTER = ("W", "X", "Y", "Z")
    CHORES = tuple(
        Chore(key="weekly-%d" % (i + 1), cadence="weekly", seed=i % 4)
        for i in range(4)
    ) + tuple(
        Chore(key="every3-%d" % (i + 1), cadence="every_3", offset=i % 3, seed=i % 4)
        for i in range(3)
    )

    def test_twelve_active_weeks(self):
        self.assertEqual(12, len(rotation.active_weeks(self.TERM)))

    def test_totals_stay_even_for_a_four_person_roster(self):
        assignments = rotation.generate(
            self.TERM, self.CHORES, self.ROSTER, CADENCES
        )
        # 4 weekly x 12 weeks + 3 every_3 x 4 occurrences = 60, 15 each.
        self.assertEqual(60, len(assignments))
        counts = Counter(a.assignee for a in assignments)
        self.assertEqual({"W": 15, "X": 15, "Y": 15, "Z": 15}, dict(counts))

    def test_each_chore_lands_evenly_across_four_people(self):
        assignments = rotation.generate(
            self.TERM, self.CHORES, self.ROSTER, CADENCES
        )
        expected_each = {"weekly": 3, "every_3": 1}
        for chore in self.CHORES:
            counts = Counter(
                a.assignee for a in assignments if a.chore.key == chore.key
            )
            per_person = expected_each[chore.cadence]
            self.assertEqual(
                {person: per_person for person in self.ROSTER},
                dict(counts),
                chore.key,
            )

    def test_a_term_with_inactive_weeks_still_divides(self):
        term = Term(
            name="Uneven calendar, even active weeks",
            start_date=date(2027, 1, 4),
            week_count=14,
            inactive_weeks=frozenset({5, 11}),
        )
        chore = Chore(key="c", cadence="weekly", seed=0)
        assignments = rotation.generate(term, (chore,), self.ROSTER, CADENCES)
        self.assertEqual(12, len(assignments))
        self.assertEqual(
            {"W": 3, "X": 3, "Y": 3, "Z": 3},
            dict(Counter(a.assignee for a in assignments)),
        )


class TestFailsLoudly(unittest.TestCase):
    def test_a_cadence_that_does_not_divide_evenly_is_rejected(self):
        # Every second active week over 9 gives 5 occurrences, which is why
        # biweekly is forbidden. Nothing rejects it by name; the count does.
        cadences = dict(CADENCES, biweekly=Cadence(name="biweekly", interval=2))
        chore = Chore(key="c", cadence="biweekly", offset=0)
        with self.assertRaises(ValueError) as caught:
            rotation.generate(AUTUMN_2026, (chore,), ROSTER, cadences)
        self.assertIn("does not divide evenly", str(caught.exception))

    def test_unknown_cadence_names_the_chore(self):
        chore = Chore(key="typo-row", cadence="wekly")
        with self.assertRaises(ValueError) as caught:
            rotation.generate(AUTUMN_2026, (chore,), ROSTER, CADENCES)
        self.assertIn("typo-row", str(caught.exception))
        self.assertIn("wekly", str(caught.exception))

    def test_offset_outside_the_cadence_interval_is_rejected(self):
        chore = Chore(key="c", cadence="every_3", offset=3)
        with self.assertRaises(ValueError):
            rotation.generate(AUTUMN_2026, (chore,), ROSTER, CADENCES)

    def test_negative_seed_is_rejected(self):
        chore = Chore(key="c", cadence="weekly", seed=-1)
        with self.assertRaises(ValueError):
            rotation.generate(AUTUMN_2026, (chore,), ROSTER, CADENCES)

    def test_empty_roster_is_rejected(self):
        with self.assertRaises(ValueError):
            rotation.generate(AUTUMN_2026, WEEKLY_CHORES, (), CADENCES)

    def test_a_term_that_does_not_start_on_monday_is_rejected(self):
        term = Term(
            name="Starts Tuesday",
            start_date=date(2026, 9, 29),
            week_count=9,
            inactive_weeks=frozenset(),
        )
        with self.assertRaises(ValueError):
            rotation.weeks(term)

    def test_an_inactive_week_outside_the_term_is_rejected(self):
        term = Term(
            name="Bad inactive week",
            start_date=date(2026, 9, 28),
            week_count=9,
            inactive_weeks=frozenset({12}),
        )
        with self.assertRaises(ValueError):
            rotation.weeks(term)


if __name__ == "__main__":
    unittest.main()
