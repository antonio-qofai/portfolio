"""QofAI to-dos typed into the page's quick-add box, kept in data/qofai_todos.json.

Stands in for reading Slack: when a commitment comes up at work ("demo ready
by Thu"), I type it and it stays on the QofAI card until I check it off.
Nothing here leaves this Mac, and nothing reaches Pressing actions or the digest.

A trailing date phrase becomes the due date and is dropped from the text:
"today", "tomorrow", a weekday ("Thu", the next one, today included), "Oct 3"
or "10/3", optionally after "by", "on", "due" or "before".
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

MAX_TEXT = 200
MAX_OPEN = 50
_lock = threading.Lock()


class TodoError(ValueError):
    """A to-do the page should explain rather than just reject."""

_WEEKDAYS = {
    "mon": 0, "monday": 0, "tue": 1, "tues": 1, "tuesday": 1, "wed": 2, "weds": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3, "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5, "sun": 6, "sunday": 6,
}
_MONTHS = {m: n for n, names in enumerate(
    [("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",), ("jun", "june"),
     ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
     ("dec", "december")], start=1) for m in names}
_TRAILING = re.compile(
    r"^(?P<text>.*?)[\s,]+(?:(?:by|on|due|before)\s+)?(?P<when>"
    r"today|tonight|eod|tomorrow|tmrw|tmr|[a-z]+\.?\s+\d{1,2}|\d{1,2}/\d{1,2}|[a-z]+"
    r")\.?$",
    re.IGNORECASE,
)


def _when(word: str, today: date) -> date | None:
    w = word.lower().rstrip(".")
    if w in ("today", "tonight", "eod"):
        return today
    if w in ("tomorrow", "tmrw", "tmr"):
        return today + timedelta(days=1)
    if w in _WEEKDAYS:
        return today + timedelta(days=(_WEEKDAYS[w] - today.weekday()) % 7)
    month_day = re.fullmatch(r"([a-z]+)\.?\s+(\d{1,2})", w) or re.fullmatch(r"(\d{1,2})/(\d{1,2})", w)
    if not month_day:
        return None
    m, d = month_day.groups()
    month = _MONTHS.get(m) if not m.isdigit() else int(m)
    if not month:
        return None
    for year in (today.year, today.year + 1):
        try:
            due = date(year, month, int(d))
        except ValueError:
            return None
        if due >= today - timedelta(days=30):  # "Jan 5" typed in December means next year
            return due
    return None


def parse_due(text: str, today: date) -> tuple[str, date | None]:
    """("demo ready", Thu) from "demo ready by Thu"; the text unchanged when no date trails it."""
    text = " ".join(text.split())
    m = _TRAILING.match(text)
    if m and m.group("text").strip():
        due = _when(m.group("when"), today)
        if due:
            return m.group("text").strip(" ,-"), due
    return text, None


def _path(data_dir: Path) -> Path:
    return data_dir / "qofai_todos.json"


def _load(data_dir: Path) -> list[dict[str, Any]]:
    try:
        return json.loads(_path(data_dir).read_text())
    except (OSError, ValueError):
        return []


def _save(data_dir: Path, todos: list[dict[str, Any]]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    tmp = _path(data_dir).with_suffix(".tmp")
    tmp.write_text(json.dumps(todos, indent=1, ensure_ascii=False))
    tmp.replace(_path(data_dir))


def visible(data_dir: Path, today: date) -> list[dict[str, Any]]:
    """Open to-dos, plus ones checked off today (so a mis-tap can be undone); soonest due first."""
    shown = [t for t in _load(data_dir) if not t.get("done_at") or t["done_at"][:10] >= today.isoformat()]
    return sorted(shown, key=lambda t: (bool(t.get("done_at")), t.get("due") or "9999", t["created"]))


def add(data_dir: Path, raw: str, now: datetime) -> dict[str, Any]:
    raw = "".join(ch for ch in raw if ch.isprintable()).strip()
    if not raw or len(raw) > MAX_TEXT:
        raise TodoError(f"Type a to-do of up to {MAX_TEXT} characters")
    text, due = parse_due(raw, now.date())
    todo = {
        "id": uuid.uuid4().hex[:12],
        "text": text,
        "due": due.isoformat() if due else None,
        "created": now.isoformat(timespec="seconds"),
        "done_at": None,
    }
    with _lock:
        todos = _load(data_dir)
        if sum(1 for t in todos if not t.get("done_at")) >= MAX_OPEN:
            raise TodoError(f"Already {MAX_OPEN} open to-dos; check some off first")
        # Done items older than a week are dropped, so the file stays small.
        cutoff = (now.date() - timedelta(days=7)).isoformat()
        todos = [t for t in todos if not t.get("done_at") or t["done_at"][:10] >= cutoff]
        todos.append(todo)
        _save(data_dir, todos)
    return todo


def set_done(data_dir: Path, todo_id: str, done: bool, now: datetime) -> bool:
    """False when there's no such to-do."""
    with _lock:
        todos = _load(data_dir)
        for t in todos:
            if t["id"] == todo_id:
                t["done_at"] = now.isoformat(timespec="seconds") if done else None
                _save(data_dir, todos)
                return True
    return False
