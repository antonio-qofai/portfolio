"""Tests for the weekly digest renderer.

Two things matter most here. The digest must show the whole week's split,
because a rotation only feels fair if the fairness is visible. And it must
never say anything about who is behind, because the moment a shared email can
embarrass someone it stops being a chore tracker.

Task names below are placeholders.
"""

import os
import sys
import unittest
from dataclasses import replace
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.cadences import CADENCES
from config.calendar import AUTUMN_2026
from config.cleaner import CLEANER_BEHAVIOURS
from config.digest_copy import DEFAULT_DIGEST_COPY
from config.due_policy import DEFAULT_DUE_POLICY
from config.digest_style import DEFAULT_DIGEST_STYLE
from config.rules import RULE_CATEGORIES, RuleCategory
from src import digest, rotation, schedule
from src.digest import Rule
from src.rotation import Chore
from src.schedule import ChoreDetail, CleanerVisit

CHICAGO = ZoneInfo("America/Chicago")
ROSTER = ("One", "Two", "Three")
COPY = DEFAULT_DIGEST_COPY

CHORES = (
    Chore(key="w1", cadence="weekly", seed=0),
    Chore(key="w2", cadence="weekly", seed=1),
    Chore(key="w3", cadence="weekly", seed=2),
    Chore(key="p1", cadence="every_3", offset=2, seed=0),
)
DETAILS = {
    "w1": ChoreDetail(
        name="Alpha",
        task="do alpha",
        cleaner_behaviour="convert_to_prep",
        prep_task="prep alpha",
    ),
    "w2": ChoreDetail(name="Bravo", task="do bravo", cleaner_behaviour="normal"),
    "w3": ChoreDetail(name="Charlie", task="do charlie", cleaner_behaviour="normal"),
    "p1": ChoreDetail(name="Delta", task="do delta", cleaner_behaviour="normal"),
}
VISIT = CleanerVisit(visit_date=date(2026, 10, 15), confirmed=True)

RULES = (
    Rule(text="rule one", category="override", number=1),
    Rule(text="rule two", category="override", number=2),
    Rule(text="rule nine", category="standing", number=9),
    Rule(
        text="rule ten",
        category="standing",
        number=10,
        active_from=date(2026, 11, 1),
    ),
)

WEEKS = {week.number: week for week in rotation.weeks(AUTUMN_2026)}


def person_blocks(body, roster=ROSTER):
    """Parse the rendered body back into {person: [chore names, in order]}."""
    blocks, current = {}, None
    for line in body.splitlines():
        if line in roster:
            current = line
            blocks[current] = []
        elif current is not None and line.startswith("  - "):
            blocks[current].append(line[4:].split(",")[0])
        elif not line:
            current = None
    return blocks


def scheduled(visits=()):
    return schedule.build(
        AUTUMN_2026, CHORES, ROSTER, CADENCES, DETAILS,
        DEFAULT_DUE_POLICY, CLEANER_BEHAVIOURS, visits,
    )


def render(week_number, visits=(), rules=RULES, roster=ROSTER, copy=COPY,
           categories=RULE_CATEGORIES, items=None):
    if items is None:
        items = tuple(
            i for i in scheduled(visits) if i.week.number == week_number
        )
    return digest.render(
        WEEKS[week_number], items, roster, rules, copy, categories
    )


class TestSubjectAndHeader(unittest.TestCase):
    def test_subject_names_the_week_and_its_dates(self):
        self.assertEqual(
            "Chores for week 3, Oct 12 to Oct 18", render(3).subject
        )

    def test_body_opens_with_the_week(self):
        self.assertTrue(render(3).body.startswith("Week 3, Oct 12 to Oct 18"))

    def test_body_ends_with_a_single_newline(self):
        body = render(3).body
        self.assertTrue(body.endswith("\n"))
        self.assertFalse(body.endswith("\n\n"))


class TestTheWeeksSplit(unittest.TestCase):
    def test_everyone_on_the_roster_appears(self):
        body = render(3).body
        for person in ROSTER:
            self.assertIn(person, body)

    def test_the_deadline_is_stated_once_not_on_every_line(self):
        """Seven identical "due Sunday 8pm" strings is noise, not information."""
        body = render(3).body
        self.assertIn("Due Sun Oct 18, 8pm unless a line says otherwise.", body)
        self.assertEqual(1, body.count("Sun Oct 18, 8pm"))

    def test_a_blank_line_separates_each_person(self):
        body = render(3).body
        for person in ROSTER[1:]:
            self.assertIn("\n\n" + person + "\n", body)

    def test_people_appear_in_roster_order(self):
        body = render(3).body
        positions = [body.index(person) for person in ROSTER]
        self.assertEqual(sorted(positions), positions)

    def test_every_assignment_appears_exactly_once(self):
        items = tuple(i for i in scheduled() if i.week.number == 3)
        body = render(3).body
        for item in items:
            self.assertEqual(1, body.count("  - " + item.name), item.name)

    def test_each_persons_chores_are_ordered_by_due_then_name(self):
        items = tuple(i for i in scheduled((VISIT,)) if i.week.number == 3)
        blocks = person_blocks(render(3, visits=(VISIT,)).body)
        for person in ROSTER:
            expected = [
                item.name
                for item in sorted(
                    (i for i in items if i.assignee == person),
                    key=lambda i: (i.due, i.name),
                )
            ]
            self.assertEqual(expected, blocks[person], person)

    def test_prep_comes_before_end_of_week_work_for_the_same_person(self):
        # One person holding both a converted chore and an ordinary one should
        # see the Thursday prep first, because it is due four days earlier.
        chores = CHORES + (Chore(key="w4", cadence="weekly", seed=1),)
        details = dict(
            DETAILS,
            w4=ChoreDetail(
                name="Echo",
                task="do echo",
                cleaner_behaviour="convert_to_prep",
                prep_task="prep echo",
            ),
        )
        items = tuple(
            i
            for i in schedule.build(
                AUTUMN_2026, chores, ROSTER, CADENCES, details,
                DEFAULT_DUE_POLICY, CLEANER_BEHAVIOURS, (VISIT,),
            )
            if i.week.number == 3
        )
        blocks = person_blocks(render(3, items=items).body)
        self.assertEqual(["Echo", "Bravo", "Delta"], blocks["One"])

    def test_somebody_with_nothing_is_still_listed(self):
        items = tuple(
            i for i in scheduled() if i.week.number == 3 and i.assignee != "Two"
        )
        body = render(3, items=items).body
        self.assertIn("Two\n  - nothing this week", body)

    def test_a_week_with_no_chores_says_so(self):
        self.assertIn(COPY.no_chores, render(3, items=()).body)


class TestCleanerWeek(unittest.TestCase):
    def test_the_cleaner_note_names_the_day_and_the_prep_deadline(self):
        body = render(3, visits=(VISIT,)).body
        self.assertIn("The cleaner comes Thu Oct 15", body)
        self.assertIn("due Thu Oct 15, 11am", body)

    def test_converted_chores_say_they_are_prep_only(self):
        body = render(3, visits=(VISIT,)).body
        self.assertIn("Alpha, prep only this week: prep alpha", body)

    def test_the_prep_deadline_is_stated_once_in_the_cleaner_note(self):
        """Not repeated on each prep line; the note above carries it."""
        body = render(3, visits=(VISIT,)).body
        self.assertEqual(1, body.count("Thu Oct 15, 11am"))

    def test_unconverted_chores_read_normally_in_a_cleaner_week(self):
        body = render(3, visits=(VISIT,)).body
        self.assertIn("  - Bravo", body)
        self.assertNotIn("Bravo, prep", body)

    def test_an_ordinary_week_has_no_cleaner_note(self):
        self.assertNotIn("The cleaner comes", render(3).body)

    def test_an_ordinary_week_says_nothing_about_prep(self):
        self.assertNotIn("Prep only", render(3).body)


class TestHouseRules(unittest.TestCase):
    def test_rules_are_grouped_under_their_category_headings(self):
        body = render(3).body
        self.assertIn(RULE_CATEGORIES["override"].heading, body)
        self.assertIn(RULE_CATEGORIES["standing"].heading, body)

    def test_override_rules_come_before_standing_rules(self):
        body = render(3).body
        self.assertLess(
            body.index(RULE_CATEGORIES["override"].heading),
            body.index(RULE_CATEGORIES["standing"].heading),
        )

    def test_numbers_are_shown_as_given_not_recalculated(self):
        body = render(3).body
        self.assertIn("  9. rule nine", body)

    def test_an_unnumbered_rule_gets_a_bullet(self):
        rules = (Rule(text="no number", category="standing"),)
        self.assertIn("  - no number", render(3, rules=rules).body)

    def test_rules_are_printed_in_number_order_within_a_category(self):
        """Airtable hands rules over in row order, which is not number order."""
        rules = (
            Rule(text="rule six", category="override", number=6),
            Rule(text="rule one", category="override", number=1),
            Rule(text="rule three", category="override", number=3),
        )
        body = render(3, rules=rules).body
        self.assertLess(body.index("rule one"), body.index("rule three"))
        self.assertLess(body.index("rule three"), body.index("rule six"))

    def test_number_order_does_not_cross_categories(self):
        rules = (
            Rule(text="standing nine", category="standing", number=9),
            Rule(text="override ten", category="override", number=10),
        )
        body = render(3, rules=rules).body
        self.assertLess(body.index("override ten"), body.index("standing nine"))

    def test_unnumbered_rules_follow_the_numbered_ones(self):
        rules = (
            Rule(text="no number", category="override"),
            Rule(text="rule two", category="override", number=2),
        )
        body = render(3, rules=rules).body
        self.assertLess(body.index("rule two"), body.index("no number"))

    def test_unnumbered_rules_keep_their_given_order(self):
        rules = (
            Rule(text="first given", category="override"),
            Rule(text="second given", category="override"),
        )
        body = render(3, rules=rules).body
        self.assertLess(body.index("first given"), body.index("second given"))

    def test_sorting_does_not_renumber(self):
        rules = (
            Rule(text="rule six", category="override", number=6),
            Rule(text="rule one", category="override", number=1),
        )
        body = render(3, rules=rules).body
        self.assertIn("  1. rule one", body)
        self.assertIn("  6. rule six", body)

    def test_active_rules_returns_them_in_the_same_order(self):
        rules = (
            Rule(text="rule six", category="override", number=6),
            Rule(text="rule one", category="override", number=1),
        )
        live = digest.active_rules(rules, WEEKS[3], RULE_CATEGORIES)
        self.assertEqual([r.number for r in live], [1, 6])

    def test_a_rule_that_activates_later_is_absent_before_then(self):
        # Week 4 is Oct 19-25; the rule switches on Nov 1.
        self.assertNotIn("rule ten", render(4).body)

    def test_a_rule_appears_in_the_week_it_switches_on(self):
        # Week 5 is Oct 26 to Nov 1, so Nov 1 is inside it.
        self.assertIn("rule ten", render(5).body)

    def test_a_rule_stays_once_active(self):
        self.assertIn("rule ten", render(10).body)

    def test_a_rule_that_has_expired_is_absent(self):
        rules = (
            Rule(
                text="gone",
                category="standing",
                active_until=date(2026, 10, 11),
            ),
        )
        self.assertNotIn("gone", render(3, rules=rules).body)

    def test_a_rule_expiring_during_the_week_still_shows(self):
        rules = (
            Rule(
                text="last call",
                category="standing",
                active_until=date(2026, 10, 14),
            ),
        )
        self.assertIn("last call", render(3, rules=rules).body)

    def test_no_rules_means_no_rules_section(self):
        self.assertNotIn(COPY.rules_heading, render(3, rules=()).body)


class TestNothingAboutWhoIsBehind(unittest.TestCase):
    """PRD 5.7 and the non-goals: the digest never reports on anyone."""

    def test_render_takes_no_completion_or_overdue_input(self):
        import inspect

        parameters = set(inspect.signature(digest.render).parameters)
        self.assertEqual(
            {"week", "scheduled", "roster", "rules", "copy", "categories"},
            parameters,
        )

    def test_the_body_says_nothing_about_lateness(self):
        body = render(3, visits=(VISIT,)).body.lower()
        for word in ("overdue", "late", "missed", "still open", "behind"):
            self.assertNotIn(word, body)


class TestFormatting(unittest.TestCase):
    def test_a_whole_hour_drops_the_minutes(self):
        moment = datetime(2026, 10, 18, 20, 0, tzinfo=CHICAGO)
        self.assertEqual("Sun Oct 18, 8pm", digest.format_due(moment, COPY))

    def test_a_part_hour_keeps_the_minutes(self):
        moment = datetime(2026, 10, 15, 9, 30, tzinfo=CHICAGO)
        self.assertEqual("Thu Oct 15, 9:30am", digest.format_due(moment, COPY))

    def test_noon_and_midnight_read_correctly(self):
        noon = datetime(2026, 10, 15, 12, 0, tzinfo=CHICAGO)
        midnight = datetime(2026, 10, 15, 0, 0, tzinfo=CHICAGO)
        self.assertEqual("Thu Oct 15, 12pm", digest.format_due(noon, COPY))
        self.assertEqual("Thu Oct 15, 12am", digest.format_due(midnight, COPY))

    def test_month_and_weekday_names_come_from_copy_not_the_locale(self):
        copy = replace(
            COPY,
            weekday_names=("a", "b", "c", "d", "e", "f", "g"),
            month_names=tuple("ABCDEFGHIJKL"),
        )
        moment = datetime(2026, 10, 15, 11, 0, tzinfo=CHICAGO)
        self.assertEqual("d J 15, 11am", digest.format_due(moment, copy))


class TestCopyIsConfigurable(unittest.TestCase):
    def test_the_chore_line_can_include_the_definition_of_done(self):
        copy = replace(COPY, chore_line="  * {name} ({task}) by {due}")
        self.assertIn("* Bravo (do bravo) by Sun Oct 18, 8pm", render(3, copy=copy).body)

    def test_the_footer_can_be_removed(self):
        copy = replace(COPY, footer="")
        self.assertNotIn(COPY.footer, render(3, copy=copy).body)

    def test_a_new_rule_category_needs_no_code_change(self):
        categories = dict(
            RULE_CATEGORIES,
            seasonal=RuleCategory(name="seasonal", heading="Winter.", order=2),
        )
        rules = (Rule(text="salt the steps", category="seasonal"),)
        body = render(3, rules=rules, categories=categories).body
        self.assertIn("Winter.", body)
        self.assertIn("salt the steps", body)


class TestFailsLoudly(unittest.TestCase):
    def test_a_chore_from_another_week_is_an_error(self):
        items = tuple(i for i in scheduled() if i.week.number == 4)
        with self.assertRaises(ValueError) as caught:
            render(3, items=items)
        self.assertIn("week 3", str(caught.exception))

    def test_an_assignee_who_is_not_on_the_roster_is_an_error(self):
        with self.assertRaises(ValueError) as caught:
            render(3, roster=("One", "Two"))
        self.assertIn("Three", str(caught.exception))

    def test_an_empty_roster_is_an_error(self):
        with self.assertRaises(ValueError):
            render(3, roster=(), items=())

    def test_an_unknown_rule_category_names_the_rule(self):
        rules = (Rule(text="mystery", category="urgent"),)
        with self.assertRaises(ValueError) as caught:
            render(3, rules=rules)
        self.assertIn("mystery", str(caught.exception))
        self.assertIn("urgent", str(caught.exception))

    def test_a_blank_rule_is_an_error(self):
        with self.assertRaises(ValueError):
            render(3, rules=(Rule(text="   ", category="standing"),))

    def test_a_rule_that_ends_before_it_starts_is_an_error(self):
        rules = (
            Rule(
                text="backwards",
                category="standing",
                active_from=date(2026, 11, 1),
                active_until=date(2026, 10, 1),
            ),
        )
        with self.assertRaises(ValueError):
            render(3, rules=rules)


class TestDeterminism(unittest.TestCase):
    def test_rendering_twice_gives_the_same_email(self):
        self.assertEqual(render(3, visits=(VISIT,)), render(3, visits=(VISIT,)))

    def test_one_body_serves_every_recipient(self):
        # There is no per-recipient render; the same body goes to all three.
        self.assertEqual(render(3).body, render(3).body)


STYLE = DEFAULT_DIGEST_STYLE


def render_html(week_number, visits=(), rules=RULES, roster=ROSTER, copy=COPY,
                categories=RULE_CATEGORIES, items=None):
    if items is None:
        items = tuple(i for i in scheduled(visits) if i.week.number == week_number)
    return digest.render_html(
        WEEKS[week_number], items, roster, rules, copy, categories, STYLE
    )


class TestHtmlDigest(unittest.TestCase):
    """The HTML view. Same words as the text view, different presentation."""

    def test_it_says_the_week(self):
        self.assertIn("Week 3, Oct 12 to Oct 18", render_html(3))

    def test_everyone_appears(self):
        body = render_html(3)
        for person in ROSTER:
            self.assertIn(person, body)

    def test_every_chore_appears(self):
        items = tuple(i for i in scheduled() if i.week.number == 3)
        body = render_html(3)
        for item in items:
            self.assertIn(item.name, body)

    def test_the_requested_font_is_on_every_text_element(self):
        body = render_html(3)
        self.assertIn("Times New Roman", body)
        # Clients strip <style> blocks, so it cannot be declared once.
        self.assertGreater(body.count("font-family"), 5)

    def test_no_external_resources(self):
        """Images and web fonts are what get a message filed as Promotions."""
        body = render_html(3)
        for forbidden in ("<img", "<style", "<link", "@import", "<script"):
            self.assertNotIn(forbidden, body)

    def test_ampersands_are_escaped(self):
        items = (replace(
            tuple(i for i in scheduled() if i.week.number == 3)[0],
            name="Restock & bin",
        ),)
        body = render_html(3, items=items)
        self.assertIn("Restock &amp; bin", body)
        self.assertNotIn("Restock & bin", body)

    def test_rules_keep_their_given_numbers(self):
        body = render_html(3)
        self.assertIn("9.", body)

    def test_rules_are_not_an_ordered_list(self):
        """An <ol> would renumber whatever survives the activation filter."""
        self.assertNotIn("<ol", render_html(3))

    def test_the_footer_url_becomes_a_link(self):
        copy = replace(COPY, footer="Tick them off.\nhttps://example.com/x")
        body = render_html(3, copy=copy)
        self.assertIn('<a href="https://example.com/x"', body)

    def test_a_cleaner_week_marks_the_prep_chores(self):
        body = render_html(3, visits=(VISIT,))
        self.assertIn("The cleaner comes Thu Oct 15", body)
        self.assertIn("prep only this week: prep alpha", body)

    def test_an_ordinary_week_says_nothing_about_prep(self):
        self.assertNotIn("prep only", render_html(3))

    def test_an_empty_week_says_so(self):
        self.assertIn(COPY.no_chores, render_html(3, items=()))

    def test_it_never_says_who_is_behind(self):
        body = render_html(3).lower()
        for word in ("overdue", "late", "behind", "missed"):
            self.assertNotIn(word, body)

    def test_text_and_html_name_the_same_chores(self):
        """The two views must never be able to disagree about the week."""
        items = tuple(i for i in scheduled() if i.week.number == 3)
        text, html = render(3).body, render_html(3)
        for item in items:
            self.assertIn(item.name, text)
            self.assertIn(item.name, html)


if __name__ == "__main__":
    unittest.main()
