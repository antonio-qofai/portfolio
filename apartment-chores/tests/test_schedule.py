"""Tests for the schedule builder.

The thing most worth protecting here is SC8: a confirmed cleaner week changes
what a chore says and when it is due, and changes nothing else. If conversion
ever starts dropping occurrences or moving assignees, the quarter stops being
even and nobody finds out until December.

Chore keys and task strings are placeholders. Nothing in this file needs to
know what any real chore is.
"""

import os
import sys
import unittest
from collections import Counter
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.cadences import CADENCES
from config.calendar import AUTUMN_2026
from config.cleaner import CLEANER_BEHAVIOURS, CleanerBehaviour
from config.due_policy import DEFAULT_DUE_POLICY, DuePolicy
from src import rotation, schedule
from src.rotation import Chore
from src.schedule import ChoreDetail, CleanerVisit

CHICAGO = ZoneInfo("America/Chicago")
ROSTER = ("A", "B", "C")

# Two chores that convert in a cleaner week and two that do not, mirroring the
# PRD's mix without naming anything real.
CHORES = (
    Chore(key="w1", cadence="weekly", seed=0),
    Chore(key="w2", cadence="weekly", seed=1),
    # Offset 2 lands on calendar weeks 3, 6 and 10, so it falls in the
    # cleaner week below and the periodic conversion path is covered.
    Chore(key="p1", cadence="every_3", offset=2, seed=0),
)
DETAILS = {
    "w1": ChoreDetail(
        name="chore one",
        task="task one",
        cleaner_behaviour="convert_to_prep",
        prep_task="prep one",
    ),
    "w2": ChoreDetail(name="chore two", task="task two", cleaner_behaviour="normal"),
    "p1": ChoreDetail(
        name="chore three",
        task="task three",
        cleaner_behaviour="convert_to_prep",
        prep_task="prep three",
    ),
}

# Week 3 of Autumn 2026 is Oct 12-18. A Thursday inside it.
VISIT_DAY = date(2026, 10, 15)
CONFIRMED_VISIT = CleanerVisit(visit_date=VISIT_DAY, confirmed=True)
PROJECTED_VISIT = CleanerVisit(visit_date=VISIT_DAY, confirmed=False)


def build(visits=(), chores=CHORES, details=DETAILS, policy=DEFAULT_DUE_POLICY):
    return schedule.build(
        AUTUMN_2026,
        chores,
        ROSTER,
        CADENCES,
        details,
        policy,
        CLEANER_BEHAVIOURS,
        visits,
    )


class TestOrdinaryWeeks(unittest.TestCase):
    def test_task_text_is_the_chores_normal_task(self):
        for item in build():
            self.assertEqual(DETAILS[item.chore.key].task, item.task)
            self.assertFalse(item.is_prep)
            self.assertIsNone(item.cleaner_visit)

    def test_due_is_sunday_at_eight_in_the_evening(self):
        for item in build():
            self.assertEqual(item.week.end_date, item.due.date())
            self.assertEqual(6, item.due.weekday())
            self.assertEqual(time(20, 0), item.due.timetz().replace(tzinfo=None))

    def test_every_due_datetime_is_timezone_aware_and_central(self):
        for item in build():
            self.assertIsNotNone(item.due.tzinfo)
            self.assertIn(item.due.tzname(), ("CDT", "CST"))

    def test_daylight_saving_is_handled_rather_than_assumed(self):
        # Autumn quarter crosses the fall-back on Sunday Nov 1, 2026, which is
        # the last day of week 5. Week 4 ends in CDT, week 5 in CST. Naive
        # datetimes would silently get one of these an hour wrong.
        by_week = {item.week.number: item.due for item in build()}
        self.assertEqual("CDT", by_week[4].tzname())
        self.assertEqual("CST", by_week[5].tzname())
        self.assertNotEqual(by_week[4].utcoffset(), by_week[5].utcoffset())

    def test_the_chore_name_travels_with_the_assignment(self):
        for item in build():
            self.assertEqual(DETAILS[item.chore.key].name, item.name)

    def test_schedule_covers_exactly_the_rotation(self):
        assignments = rotation.generate(AUTUMN_2026, CHORES, ROSTER, CADENCES)
        self.assertEqual(len(assignments), len(build()))


class TestConfirmedCleanerWeek(unittest.TestCase):
    """SC8: swap the text, keep everything else."""

    def setUp(self):
        self.plain = build()
        self.cleaned = build(visits=(CONFIRMED_VISIT,))
        self.week_three = [i for i in self.cleaned if i.week.number == 3]

    def test_converting_chores_swap_to_their_prep_task(self):
        converted = [i for i in self.week_three if i.chore.key in ("w1", "p1")]
        self.assertEqual(2, len(converted))
        for item in converted:
            self.assertEqual(DETAILS[item.chore.key].prep_task, item.task)
            self.assertTrue(item.is_prep)
            self.assertEqual(CONFIRMED_VISIT, item.cleaner_visit)

    def test_converted_chores_are_due_at_eleven_on_the_visit_day(self):
        for item in self.week_three:
            if item.is_prep:
                self.assertEqual(
                    datetime(2026, 10, 15, 11, 0, tzinfo=CHICAGO), item.due
                )

    def test_non_converting_chores_are_untouched_in_a_cleaner_week(self):
        untouched = [i for i in self.week_three if i.chore.key == "w2"]
        self.assertEqual(1, len(untouched))
        self.assertEqual("task two", untouched[0].task)
        self.assertFalse(untouched[0].is_prep)
        self.assertEqual(date(2026, 10, 18), untouched[0].due.date())

    def test_assignees_and_slots_are_identical_with_and_without_the_visit(self):
        def slots(items):
            return [
                (i.week.number, i.chore.key, i.occurrence_index, i.assignee)
                for i in items
            ]

        self.assertEqual(slots(self.plain), slots(self.cleaned))

    def test_quarter_totals_survive_conversion(self):
        self.assertEqual(len(self.plain), len(self.cleaned))
        self.assertEqual(
            Counter(i.assignee for i in self.plain),
            Counter(i.assignee for i in self.cleaned),
        )

    def test_only_the_visit_week_is_affected(self):
        elsewhere = [i for i in self.cleaned if i.week.number != 3]
        self.assertTrue(all(not i.is_prep for i in elsewhere))

    def test_the_full_prd_shaped_quarter_still_gives_24_each(self):
        chores = tuple(
            Chore(key="w%d" % (i + 1), cadence="weekly", seed=s)
            for i, s in enumerate((0, 0, 1, 1, 2, 2))
        ) + tuple(
            Chore(key="p%d" % (i + 1), cadence="every_3", offset=o, seed=s)
            for i, (o, s) in enumerate(zip((0, 0, 1, 1, 2, 2), (0, 0, 1, 1, 2, 2)))
        )
        details = {
            chore.key: ChoreDetail(
                name="chore %s" % chore.key,
                task="task %s" % chore.key,
                cleaner_behaviour="convert_to_prep",
                prep_task="prep %s" % chore.key,
            )
            for chore in chores
        }
        items = build(visits=(CONFIRMED_VISIT,), chores=chores, details=details)
        self.assertEqual(72, len(items))
        self.assertEqual(
            {"A": 24, "B": 24, "C": 24},
            dict(Counter(i.assignee for i in items)),
        )


class TestVisitsThatChangeNothing(unittest.TestCase):
    def test_an_unconfirmed_visit_is_ignored(self):
        self.assertEqual(build(), build(visits=(PROJECTED_VISIT,)))

    def test_a_visit_in_an_inactive_week_converts_nothing(self):
        # Week 9, Thanksgiving, has no rotation to convert.
        visit = CleanerVisit(visit_date=date(2026, 11, 25), confirmed=True)
        self.assertEqual(build(), build(visits=(visit,)))

    def test_a_visit_outside_the_term_converts_nothing(self):
        visit = CleanerVisit(visit_date=date(2027, 1, 14), confirmed=True)
        self.assertEqual(build(), build(visits=(visit,)))

    def test_a_missing_visit_degrades_to_an_ordinary_week(self):
        self.assertTrue(all(not i.is_prep for i in build()))


class TestUnhandledVisits(unittest.TestCase):
    def test_a_visit_in_an_inactive_week_is_reported_not_swallowed(self):
        visit = CleanerVisit(visit_date=date(2026, 11, 25), confirmed=True)
        self.assertEqual(
            (visit,), schedule.unhandled_visits(AUTUMN_2026, (visit,))
        )

    def test_a_visit_outside_the_term_is_reported(self):
        visit = CleanerVisit(visit_date=date(2027, 1, 14), confirmed=True)
        self.assertEqual(
            (visit,), schedule.unhandled_visits(AUTUMN_2026, (visit,))
        )

    def test_a_visit_in_an_active_week_is_not_reported(self):
        self.assertEqual(
            (), schedule.unhandled_visits(AUTUMN_2026, (CONFIRMED_VISIT,))
        )

    def test_an_unconfirmed_visit_is_never_reported(self):
        stranded = CleanerVisit(visit_date=date(2026, 11, 25), confirmed=False)
        self.assertEqual((), schedule.unhandled_visits(AUTUMN_2026, (stranded,)))


class TestFailsLoudly(unittest.TestCase):
    def test_two_confirmed_visits_in_one_week_is_an_error(self):
        second = CleanerVisit(visit_date=date(2026, 10, 16), confirmed=True)
        with self.assertRaises(ValueError) as caught:
            build(visits=(CONFIRMED_VISIT, second))
        self.assertIn("week 3", str(caught.exception))

    def test_a_chore_with_no_detail_names_the_chore(self):
        with self.assertRaises(ValueError) as caught:
            build(details={k: v for k, v in DETAILS.items() if k != "w2"})
        self.assertIn("w2", str(caught.exception))

    def test_a_blank_name_is_an_error(self):
        details = dict(
            DETAILS,
            w2=ChoreDetail(name=" ", task="task two", cleaner_behaviour="normal"),
        )
        with self.assertRaises(ValueError) as caught:
            build(details=details)
        self.assertIn("w2", str(caught.exception))

    def test_a_blank_task_is_an_error(self):
        details = dict(
            DETAILS,
            w2=ChoreDetail(name="chore two", task="  ", cleaner_behaviour="normal"),
        )
        with self.assertRaises(ValueError) as caught:
            build(details=details)
        self.assertIn("w2", str(caught.exception))

    def test_an_unknown_cleaner_behaviour_names_the_chore_and_the_value(self):
        details = dict(
            DETAILS,
            w2=ChoreDetail(
                name="chore two", task="task two", cleaner_behaviour="convert"
            ),
        )
        with self.assertRaises(ValueError) as caught:
            build(details=details)
        self.assertIn("w2", str(caught.exception))
        self.assertIn("convert", str(caught.exception))

    def test_a_converting_chore_with_no_prep_task_is_an_error(self):
        details = dict(
            DETAILS,
            w1=ChoreDetail(
                name="chore one", task="task one",
                cleaner_behaviour="convert_to_prep",
            ),
        )
        with self.assertRaises(ValueError) as caught:
            build(details=details)
        self.assertIn("w1", str(caught.exception))
        self.assertIn("prep", str(caught.exception))

    def test_a_converting_chore_with_no_prep_task_fails_without_any_visit(self):
        # The row is wrong the moment it is written, not only in the month a
        # cleaner happens to be confirmed. Fail on the first run, not later.
        details = dict(
            DETAILS,
            w1=ChoreDetail(
                name="chore one", task="task one",
                cleaner_behaviour="convert_to_prep",
            ),
        )
        with self.assertRaises(ValueError):
            build(visits=(), details=details)

    def test_a_due_offset_outside_the_week_is_an_error(self):
        policy = DuePolicy(
            timezone="America/Chicago",
            normal_day_offset=9,
            normal_time=time(20, 0),
            prep_time=time(11, 0),
        )
        with self.assertRaises(ValueError):
            build(policy=policy)


class TestConfigurability(unittest.TestCase):
    def test_the_due_day_and_time_come_from_the_policy(self):
        policy = DuePolicy(
            timezone="America/Chicago",
            normal_day_offset=5,
            normal_time=time(9, 30),
            prep_time=time(11, 0),
        )
        item = build(policy=policy)[0]
        self.assertEqual(5, item.due.weekday())
        self.assertEqual(time(9, 30), item.due.timetz().replace(tzinfo=None))

    def test_the_prep_time_comes_from_the_policy(self):
        policy = DuePolicy(
            timezone="America/Chicago",
            normal_day_offset=6,
            normal_time=time(20, 0),
            prep_time=time(7, 15),
        )
        prep = [
            i
            for i in build(visits=(CONFIRMED_VISIT,), policy=policy)
            if i.is_prep
        ]
        self.assertTrue(prep)
        for item in prep:
            self.assertEqual(time(7, 15), item.due.timetz().replace(tzinfo=None))

    def test_a_new_cleaner_behaviour_needs_no_code_change(self):
        behaviours = dict(
            CLEANER_BEHAVIOURS,
            skip_text=CleanerBehaviour(name="skip_text", converts=False),
        )
        details = dict(
            DETAILS,
            w2=ChoreDetail(
                name="chore two", task="task two", cleaner_behaviour="skip_text"
            ),
        )
        items = schedule.build(
            AUTUMN_2026, CHORES, ROSTER, CADENCES, details,
            DEFAULT_DUE_POLICY, behaviours, (CONFIRMED_VISIT,),
        )
        w2 = [i for i in items if i.chore.key == "w2"]
        self.assertTrue(all(not i.is_prep for i in w2))

    def test_the_timezone_comes_from_the_policy(self):
        policy = DuePolicy(
            timezone="UTC",
            normal_day_offset=6,
            normal_time=time(20, 0),
            prep_time=time(11, 0),
        )
        self.assertEqual("UTC", build(policy=policy)[0].due.tzname())


class TestDeterminism(unittest.TestCase):
    def test_building_twice_gives_identical_records(self):
        self.assertEqual(
            build(visits=(CONFIRMED_VISIT,)), build(visits=(CONFIRMED_VISIT,))
        )

    def test_build_week_matches_that_slice_of_the_full_build(self):
        full = build(visits=(CONFIRMED_VISIT,))
        week = schedule.build_week(
            AUTUMN_2026, 3, CHORES, ROSTER, CADENCES, DETAILS,
            DEFAULT_DUE_POLICY, CLEANER_BEHAVIOURS, (CONFIRMED_VISIT,),
        )
        self.assertEqual([i for i in full if i.week.number == 3], list(week))


if __name__ == "__main__":
    unittest.main()
