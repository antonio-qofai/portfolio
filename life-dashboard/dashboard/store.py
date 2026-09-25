"""Check-offs and feedback from the page's buttons, stored under data/.

data/checked.json   {key: {at, source, title}} for checked-off actions
data/feedback.jsonl one line per "too early / too late / not needed" click

An item's key comes from its source, title, due date and timestamp, so a
check-off holds until the item itself changes (a new due date, a new message
in the thread). The server accepts only keys present in the current brief and
copies the item's details from the brief, never from the request.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from dashboard.schema import Item

FEEDBACK_KINDS = ("too_early", "too_late", "not_needed")
_lock = threading.Lock()


def item_key(item: Item) -> str:
    raw = "|".join([item.source, item.title, item.due or "", item.timestamp or ""])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _checked_path(data_dir: Path) -> Path:
    return data_dir / "checked.json"


def _feedback_path(data_dir: Path) -> Path:
    return data_dir / "feedback.jsonl"


def load_checked(data_dir: Path) -> dict[str, dict]:
    try:
        return json.loads(_checked_path(data_dir).read_text())
    except (OSError, ValueError):
        return {}


def load_feedback(data_dir: Path) -> list[dict]:
    try:
        lines = _feedback_path(data_dir).read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def hidden_keys(data_dir: Path) -> set[str]:
    """Items the user checked off or marked not needed; they never return as actions."""
    not_needed = {f["key"] for f in load_feedback(data_dir) if f.get("feedback") == "not_needed"}
    return set(load_checked(data_dir)) | not_needed


def recent_feedback(data_dir: Path, limit: int) -> list[dict]:
    """The last `limit` feedback entries, trimmed to what the prompts need."""
    fields = ("feedback", "source", "title", "shown_on", "due", "starts", "surfaced_as")
    return [{k: f.get(k) for k in fields} for f in load_feedback(data_dir)[-limit:]]


def find_surfaced(brief: dict[str, Any], key: str) -> dict | None:
    """The saved brief's action or upcoming entry with this key."""
    for kind in ("actions", "upcoming"):
        for entry in brief.get(kind, []):
            if entry.get("key") == key:
                return {**entry, "surfaced_as": kind}
    return None


def _item_fields(entry: dict) -> dict:
    item = entry["item"]
    return {"source": item["source"], "title": item["title"], "due": item.get("due"), "starts": item.get("timestamp")}


def set_checked(data_dir: Path, entry: dict, done: bool, now: datetime) -> None:
    with _lock:
        checked = load_checked(data_dir)
        if done:
            checked[entry["key"]] = {"at": now.isoformat(timespec="seconds"), **_item_fields(entry)}
        else:
            checked.pop(entry["key"], None)
        data_dir.mkdir(parents=True, exist_ok=True)
        tmp = _checked_path(data_dir).with_suffix(".tmp")
        tmp.write_text(json.dumps(checked, indent=1, ensure_ascii=False))
        tmp.replace(_checked_path(data_dir))


def add_feedback(data_dir: Path, entry: dict, kind: str, shown_on: str, now: datetime) -> None:
    if kind not in FEEDBACK_KINDS:
        raise ValueError(f"unknown feedback {kind!r}")
    record = {
        "at": now.isoformat(timespec="seconds"),
        "key": entry["key"],
        "feedback": kind,
        "shown_on": shown_on,
        "surfaced_as": entry["surfaced_as"],
        **_item_fields(entry),
    }
    with _lock:
        data_dir.mkdir(parents=True, exist_ok=True)
        with open(_feedback_path(data_dir), "a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def page_state(data_dir: Path, brief: dict[str, Any]) -> dict[str, Any]:
    """Which of the brief's items are checked, and the latest feedback on each."""
    keys = {e["key"] for kind in ("actions", "upcoming") for e in brief.get(kind, [])}
    checked = [k for k in load_checked(data_dir) if k in keys]
    feedback = {f["key"]: f["feedback"] for f in load_feedback(data_dir) if f.get("key") in keys}
    return {"checked": checked, "feedback": feedback}
