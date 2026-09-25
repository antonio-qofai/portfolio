"""Academic calendar constants.

Data only. Nothing here is logic, and nothing in src/ contains a date.
Editing this file (or constructing a different Term) is how the engine is
pointed at a different apartment, quarter, or term length.
"""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Term:
    """One scheduling period, expressed as whole Monday-anchored weeks.

    start_date must be the Monday of calendar week 1. Calendar weeks are
    numbered 1..week_count. Weeks listed in inactive_weeks exist on the
    calendar but carry no rotation; they get one-off tasks instead.
    """

    name: str
    start_date: date
    week_count: int
    inactive_weeks: frozenset


# UChicago Autumn Quarter 2026.
# Instruction runs Mon Sep 28 through Fri Dec 11; week 11 is a full calendar
# week that extends two days past the last day of instruction.
# Week 9  (Nov 23-29) is Thanksgiving break.
# Week 11 (Dec 7-13)  is reading period and finals.
AUTUMN_2026 = Term(
    name="UChicago Autumn Quarter 2026",
    start_date=date(2026, 9, 28),
    week_count=11,
    inactive_weeks=frozenset({9, 11}),
)
