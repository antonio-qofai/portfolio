"""Tests for src/placement.py, the after_cleaner cadence.

Placeholder people and chore keys throughout. Dates are Autumn 2026 because
the worked example in PRD-v1.2-after-cleaner.md uses them.
"""

import unittest
from collections import Counter
from datetime import date

from config.cadences import CADENCES, PLACED_CADENCES
from config.calendar import AUTUMN_2026, Term
from src import placement, rotation
from src.rotation import Chore
from src.schedule import CleanerVisit

ROSTER = ("A", "B", "C")
PLACED = Chore(key="placed", cadence="after_cleaner", seed=0)


def visit(day, confirmed=True):
    return CleanerVisit(visit_date=day, confirmed=confirmed)


def place(visits=(), written=(1,), history=((1, "placed", "A"),), chore=PLACED, term=AUTUMN_2026):
    return placement.place(
        term, (chore,), ROSTER, PLACED_CADENCES, visits, frozenset(written), history
    )


def turns(assignments):
    return [(a.week.number, a.assignee) for a in assignments]


class TestWorkedExample(unittest.TestCase):
    """PRD-v1.2 §6."""

    def test_visit_mid_october(self):
        # Oct 15 is in week 3. Plus 21 days is Nov 5, week 6, across the
        # November daylight saving change. Week 10 is the end-of-term rule.
        self.assertEqual(
            turns(place([visit(date(2026, 10, 15))])),
            [(1, "A"), (6, "B"), (10, "C")],
        )

    def test_second_visit_changes_nothing(self):
        self.assertEqual(
            turns(place([visit(date(2026, 10, 15)), visit(date(2026, 11, 12))])),
            [(1, "A"), (6, "B"), (10, "C")],
        )

    def test_no_visits_cap_alone(self):
        self.assertEqual(turns(place()), [(1, "A"), (5, "B"), (10, "C")])

    def test_unconfirmed_visit_places_nothing(self):
        self.assertEqual(
            turns(place([visit(date(2026, 10, 15), confirmed=False)])),
            turns(place()),
        )


class TestTargets(unittest.TestCase):

    # 13 weeks with only week 9 inactive, so the end-of-term rule is far
    # enough away not to hide the rule under test.
    LONG = Term(
        name="long", start_date=date(2026, 9, 28), week_count=13,
        inactive_weeks=frozenset({9}),
    )

    def test_target_in_inactive_week_rolls_forward(self):
        # Nov 5 is week 6. Plus 21 days is Nov 26, week 9, inactive.
        result = place(
            [visit(date(2026, 11, 5))],
            written=range(1, 6),
            history=((1, "placed", "A"), (5, "placed", "B")),
            term=self.LONG,
        )
        self.assertEqual(turns(result), [(1, "A"), (5, "B"), (10, "C")])

    def test_target_past_the_term_gives_nothing(self):
        active = rotation.active_weeks(AUTUMN_2026)
        targets = placement._target_positions(
            (visit(date(2026, 12, 3)),), PLACED_CADENCES["after_cleaner"],
            AUTUMN_2026, active, set(),
        )
        self.assertEqual(targets, set())

    def test_target_in_week_eleven_gives_nothing(self):
        # Nov 19 is week 8. Plus 21 days is Dec 10, week 11, inactive, and
        # there is no active week after it.
        active = rotation.active_weeks(AUTUMN_2026)
        targets = placement._target_positions(
            (visit(date(2026, 11, 19)),), PLACED_CADENCES["after_cleaner"],
            AUTUMN_2026, active, set(),
        )
        self.assertEqual(targets, set())

    def test_target_week_with_another_visit_is_skipped(self):
        # Oct 15 targets week 6, but the cleaner is there on Nov 5.
        result = place([visit(date(2026, 10, 15)), visit(date(2026, 11, 5))])
        self.assertNotIn(6, [week for week, _ in turns(result)])


class TestCap(unittest.TestCase):

    def test_visit_resets_the_cap(self):
        # Without the visit the cap forces week 5. With a week 3 visit the
        # next turn waits for its target in week 6.
        with_visit = turns(place([visit(date(2026, 10, 15))]))
        self.assertNotIn(5, [week for week, _ in with_visit])

    def test_cap_counts_active_weeks(self):
        # From a turn in week 5, four active weeks on is week 10 because
        # week 9 does not count. Calendar weeks would say week 9 or 8.
        result = place(written=range(1, 6), history=((1, "placed", "A"), (5, "placed", "B")))
        self.assertEqual(turns(result)[-1], (10, "C"))

    def test_no_history_starts_the_cap_before_the_term(self):
        result = place(written=(), history=())
        self.assertEqual(turns(result), [(4, "A"), (8, "B"), (10, "C")])

    def test_gaps_never_exceed_the_cap_with_no_visits(self):
        active = [w.number for w in rotation.active_weeks(AUTUMN_2026)]
        positions = [active.index(week) for week, _ in turns(place())]
        gaps = [b - a for a, b in zip(positions, positions[1:])]
        self.assertTrue(all(gap <= 4 for gap in gaps), gaps)


class TestTurns(unittest.TestCase):

    def test_one_turn_each(self):
        for visits in ((), [visit(date(2026, 10, 15))], [visit(date(2026, 10, 1))]):
            counts = Counter(person for _, person in turns(place(visits)))
            self.assertEqual(counts, Counter(ROSTER), visits)

    def test_rotation_order_from_seed(self):
        chore = Chore(key="placed", cadence="after_cleaner", seed=1)
        result = place(written=(), history=(), chore=chore)
        self.assertEqual([p for _, p in turns(result)], ["B", "C", "A"])

    def test_occurrence_index_counts_turns(self):
        self.assertEqual([a.occurrence_index for a in place()], [0, 1, 2])

    def test_never_two_turns_in_a_week(self):
        weeks = [week for week, _ in turns(place([visit(date(2026, 10, 1))]))]
        self.assertEqual(len(weeks), len(set(weeks)))

    def test_other_chores_history_is_ignored(self):
        self.assertEqual(
            turns(place(history=((1, "placed", "A"), (1, "other", "B")))),
            turns(place()),
        )


class TestHistory(unittest.TestCase):

    def test_written_week_is_not_redecided(self):
        # Week 2 is written with no turn. A visit whose target would be
        # week 2 must not put one there.
        result = place([visit(date(2026, 9, 10))], written=(1, 2))
        self.assertNotIn(2, [week for week, _ in turns(result)])

    def test_visit_added_later_leaves_written_weeks_alone(self):
        history = ((1, "placed", "A"), (5, "placed", "B"))
        before = place(written=range(1, 7), history=history)
        after = place([visit(date(2026, 9, 30))], written=range(1, 7), history=history)
        self.assertEqual(
            [t for t in turns(before) if t[0] <= 6],
            [t for t in turns(after) if t[0] <= 6],
        )

    def test_written_assignee_kept_even_out_of_order(self):
        result = place(history=((1, "placed", "C"),))
        self.assertEqual(turns(result)[0], (1, "C"))
        self.assertEqual(Counter(p for _, p in turns(result)), Counter(ROSTER))

    def test_same_person_twice_raises(self):
        with self.assertRaisesRegex(ValueError, "already had a turn"):
            place(written=(1, 2), history=((1, "placed", "A"), (2, "placed", "A")))

    def test_more_turns_than_people_raises(self):
        history = ((1, "placed", "A"), (2, "placed", "B"), (3, "placed", "C"), (3, "placed", "D"))
        with self.assertRaisesRegex(ValueError, "roster has 3"):
            place(written=(1, 2, 3), history=history)

    def test_unknown_cadence_raises(self):
        with self.assertRaisesRegex(ValueError, "not a placed cadence"):
            place(chore=Chore(key="placed", cadence="weekly"))


class TestLiveTotals(unittest.TestCase):
    """The live base after v1.2: 5 weekly, 3 every_3, 1 after_cleaner."""

    WEEKLY = tuple(
        Chore(key="w%d" % i, cadence="weekly", seed=seed)
        for i, seed in enumerate((0, 1, 1, 2, 2))
    )
    PERIODIC = tuple(Chore(key="p%d" % i, cadence="every_3", offset=i, seed=i) for i in range(3))

    def everything(self, visits):
        fixed = rotation.generate(AUTUMN_2026, self.WEEKLY + self.PERIODIC, ROSTER, CADENCES)
        return fixed + place(visits)

    def test_fifty_seven_and_nineteen_each(self):
        for visits in ((), [visit(date(2026, 10, 15))]):
            assignments = self.everything(visits)
            self.assertEqual(len(assignments), 57)
            self.assertEqual(set(Counter(a.assignee for a in assignments).values()), {19})


if __name__ == "__main__":
    unittest.main()
