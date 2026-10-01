"""Recurring coursework that no calendar holds, from rules in config.yaml `coursework`.

Canvas covers classes that post assignments there. These rules fill the gaps
(homework handed to a TA, readings before class) and become deadline items
like Canvas ones (source `calendar.coursework`), so they get lead times and
can rank in Pressing actions. Each rule yields only its next occurrence.

    - title: Money & Banking homework
      weekly: Wed            # due that weekday; `time: "16:30"` makes it a timed deadline
      from: 2026-10-07       # optional first due date
      finish_early_days: 1   # optional: remind on my own target day, the day before
    - title: Colonizations reading
      before_class: colonizations   # due at the start of the next calendar event with this title (any case)
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from dashboard.schema import Item

WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _weekly(rule: dict, now: datetime) -> str | None:
    weekday = WEEKDAYS.get(str(rule["weekly"]).lower()[:3])
    if weekday is None:
        raise ValueError(f"coursework rule {rule['title']!r}: unknown weekday {rule['weekly']!r}")
    first = max(now.date(), date.fromisoformat(str(rule["from"])) if rule.get("from") else now.date())
    day = first + timedelta(days=(weekday - first.weekday()) % 7)
    if not rule.get("time"):
        return day.isoformat()
    hour, minute = (int(x) for x in str(rule["time"]).split(":"))
    due = datetime.combine(day, datetime.min.time(), now.tzinfo).replace(hour=hour, minute=minute)
    if due < now:
        due += timedelta(days=7)
    return due.isoformat()


def _before_class(rule: dict, events: list[Item], now: datetime) -> tuple[str, str] | None:
    """(start, link) of the next matching class that hasn't started."""
    name = str(rule["before_class"]).strip().lower()
    for e in sorted(events, key=lambda e: e.timestamp or ""):
        if e.title.strip().lower() == name and e.timestamp and len(e.timestamp) > 10 and not e.due:
            if datetime.fromisoformat(e.timestamp) >= now:
                return e.timestamp, e.link
    return None


def _item(rule: dict, due: str, link: str, now: datetime) -> Item:
    """With finish_early_days, `due` is my target day and the summary gives the real deadline.

    Past the target but before the deadline the item is overdue, not gone. The
    item stays the same across those days, so a check-off on the target day holds.
    """
    today = now.date().isoformat()
    early = int(rule.get("finish_early_days") or 0)
    if not early:
        return Item(source="calendar.coursework", title=rule["title"], link=link, timestamp=due, due=due,
                    urgency_hints=["due_today" if due[:10] <= today else "deadline"])
    real = datetime.fromisoformat(due)
    target = (real.date() - timedelta(days=early)).isoformat()
    hint = "overdue" if target < today else "due_today" if target == today else "deadline"
    deadline = real.strftime("%a %b %-d") + ("" if len(due) == 10 else real.strftime(", %-I:%M %p"))
    return Item(source="calendar.coursework", title=rule["title"], summary=f"Due {deadline}", link=link,
                timestamp=target, due=target, urgency_hints=[hint])


def derive(events: list[Item], rules: list[dict], now: datetime) -> list[Item]:
    items = []
    for rule in rules or []:
        link = ""
        if "weekly" in rule:
            due = _weekly(rule, now)
        elif "before_class" in rule:
            found = _before_class(rule, events, now)
            if not found:
                continue  # no such class in the calendar window
            due, link = found
        else:
            raise ValueError(f"coursework rule {rule.get('title')!r} needs `weekly` or `before_class`")
        items.append(_item(rule, due, link, now))
    return items
