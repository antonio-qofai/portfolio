from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from connectors.coursework import derive
from dashboard.schema import Item

TZ = ZoneInfo("America/Chicago")
TUE_NOON = datetime(2026, 9, 29, 12, 0, tzinfo=TZ)


def cls(title, when, link="https://calendar.google.com/x"):
    return Item(source="calendar.uchicago", title=title, timestamp=when, link=link)


CLASSES = [
    cls("colonizations", "2026-09-29T14:00:00-05:00"),
    cls("colonizations", "2026-10-01T14:00:00-05:00"),
    cls("Colonizations", "2026-09-29T09:00:00-05:00"),  # already started: skipped
]


def test_weekly_rule_is_the_next_weekday():
    [item] = derive([], [{"title": "M&B homework", "weekly": "Wed"}], TUE_NOON)
    assert (item.due, item.urgency_hints, item.source) == ("2026-09-30", ["deadline"], "calendar.coursework")
    [today] = derive([], [{"title": "M&B homework", "weekly": "Tuesday"}], TUE_NOON)
    assert (today.due, today.urgency_hints) == ("2026-09-29", ["due_today"])


def test_weekly_rule_respects_from_and_time():
    [item] = derive([], [{"title": "M&B", "weekly": "Wed", "from": "2026-10-07"}], TUE_NOON)
    assert item.due == "2026-10-07"
    [timed] = derive([], [{"title": "PSet", "weekly": "Tue", "time": "09:00"}], TUE_NOON)  # 9 AM passed
    assert timed.due == "2026-10-06T09:00:00-05:00"


def test_reading_is_due_at_the_next_class():
    [item] = derive(CLASSES, [{"title": "Colonizations reading", "before_class": "Colonizations"}], TUE_NOON)
    assert item.due == item.timestamp == "2026-09-29T14:00:00-05:00"
    assert item.urgency_hints == ["due_today"] and item.link.startswith("https://")
    later = datetime(2026, 9, 29, 15, 0, tzinfo=TZ)
    assert derive(CLASSES, [{"title": "R", "before_class": "colonizations"}], later)[0].due.startswith("2026-10-01")


def test_missing_class_yields_nothing_and_bad_rules_fail_loudly():
    assert derive(CLASSES, [{"title": "R", "before_class": "epa"}], TUE_NOON) == []
    with pytest.raises(ValueError):
        derive([], [{"title": "X", "weekly": "Someday"}], TUE_NOON)
    with pytest.raises(ValueError):
        derive([], [{"title": "X"}], TUE_NOON)


def test_finish_early_reminds_on_the_target_day_until_the_deadline():
    rule = {"title": "M&B homework", "weekly": "Wed", "finish_early_days": 1}
    mon = datetime(2026, 9, 28, 9, 0, tzinfo=TZ)
    wed = datetime(2026, 9, 30, 9, 0, tzinfo=TZ)
    thu = datetime(2026, 10, 1, 9, 0, tzinfo=TZ)
    [before] = derive([], [rule], mon)
    [target] = derive([], [rule], TUE_NOON)
    [late] = derive([], [rule], wed)
    assert (before.due, before.urgency_hints) == ("2026-09-29", ["deadline"])
    assert (target.due, target.urgency_hints, target.summary) == ("2026-09-29", ["due_today"], "Due Wed Sep 30")
    assert (late.due, late.urgency_hints) == ("2026-09-29", ["overdue"])  # same item, so a check-off holds
    assert derive([], [rule], thu)[0].due == "2026-10-06"  # next week's Tuesday
