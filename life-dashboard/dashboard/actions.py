"""Pressing actions (M5): lead times for upcoming events, then an LLM ranking.

1. Lead time: the model rates each upcoming personal event (title, calendar,
   start time only) and says how many days ahead it should surface. Events
   inside their lead time appear under Coming up and become candidates.
2. Ranking: the model picks 3 to `actions.cap` candidates from the personal
   modules (calendar, pressing email, chores), most pressing first,
   each with a one-line why. QofAI items are never candidates.

Both prompts get the user's recent feedback. Items the user checked off or
marked not needed are dropped before ranking. If a call fails, simple rules
stand in and the page says so.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import anthropic

from dashboard import store
from dashboard.config import ROOT, load_env
from dashboard.schema import ConnectorResult, Item

LEAD_PROMPT = ROOT / "prompts" / "lead_time.md"
RANK_PROMPT = ROOT / "prompts" / "rank_actions.md"
# job_search joins in M7; until then it returns stub items that must not rank.
CANDIDATE_CONNECTORS = ("calendar", "email", "chores")
URGENCY_ORDER = {"overdue": 0, "due_today": 1, "reply_needed": 2, "deadline": 3}
# Without the model, only deadlines this close surface early.
FALLBACK_LEAD_DAYS = 3

LEAD_SCHEMA = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "importance": {"type": "string", "enum": ["high", "medium", "low"]},
                    "lead_days": {"type": "integer"},
                    "prep": {"type": "string"},
                },
                "required": ["id", "importance", "lead_days", "prep"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["events"],
    "additionalProperties": False,
}

RANK_SCHEMA = {
    "type": "object",
    "properties": {
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "why": {"type": "string"}},
                "required": ["id", "why"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["actions"],
    "additionalProperties": False,
}


class ActionsError(RuntimeError):
    pass


@dataclass
class Surfaced:
    """An item shown with feedback buttons: a Pressing action or a Coming up event."""

    item: Item
    key: str
    why: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "why": self.why, "item": self.item.to_dict()}


def surfaced(item: Item, why: str = "") -> Surfaced:
    return Surfaced(item=item, key=store.item_key(item), why=why)


def local_date(iso: str, now: datetime) -> date:
    """Calendar date of an ISO date or datetime, in the brief's timezone."""
    if len(iso) == 10:
        return date.fromisoformat(iso)
    return datetime.fromisoformat(iso).astimezone(now.tzinfo).date()


def split_events(events: list[Item], now: datetime) -> tuple[list[Item], list[Item]]:
    """(today's events, later events)."""
    today = now.date()
    todays = [e for e in events if e.timestamp and local_date(e.timestamp, now) <= today]
    later = [e for e in events if e.timestamp and local_date(e.timestamp, now) > today]
    return todays, later


# --- Claude ----------------------------------------------------------------

def _client() -> Any:
    load_env()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise ActionsError("ANTHROPIC_API_KEY is not set in .env")
    return anthropic.Anthropic()


def _call(client: Any, config: dict, prompt: str, schema: dict) -> dict:
    try:
        response = client.messages.create(
            model=config["actions"]["model"],
            max_tokens=16000,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": config["actions"]["effort"], "format": {"type": "json_schema", "schema": schema}},
        )
    except anthropic.AnthropicError as e:
        raise ActionsError(f"Claude API call failed: {e}") from e
    if response.stop_reason != "end_turn":
        raise ActionsError(f"stopped early: {response.stop_reason}")
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except ValueError as e:
        raise ActionsError(f"unreadable output: {e}") from e


def _fill(template: Path, **values: str) -> str:
    text = template.read_text()
    for name, value in values.items():
        text = text.replace("{" + name + "}", value)
    return text


def _dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1)


# --- Lead time -------------------------------------------------------------

def event_payload(events: list[Item], now: datetime) -> list[dict]:
    """Title, calendar and start only: never location or descriptions."""
    return [
        {
            "id": f"e{n}",
            "calendar": e.source,
            "title": e.title,
            "starts": e.timestamp,
            "days_until": (local_date(e.timestamp, now) - now.date()).days,
            "deadline": "deadline" in e.urgency_hints,
        }
        for n, e in enumerate(events)
    ]


def build_lead_prompt(payload: list[dict], feedback: list[dict], now: datetime, max_days: int) -> str:
    return _fill(
        LEAD_PROMPT,
        today=now.date().isoformat(),
        count=str(len(payload)),
        max_days=str(max_days),
        feedback_json=_dump(feedback),
        events_json=_dump(payload),
    )


def rate_lead_times(
    events: list[Item], feedback: list[dict], now: datetime, config: dict, client: Any
) -> list[Surfaced]:
    """Upcoming events inside their model-rated lead time, soonest first."""
    if not events:
        return []
    max_days = config["calendar"]["lookahead_days"]
    payload = event_payload(events, now)
    out = _call(client, config, build_lead_prompt(payload, feedback, now, max_days), LEAD_SCHEMA)
    try:
        verdicts = {v["id"]: v for v in out["events"]}
    except (KeyError, TypeError) as e:
        raise ActionsError(f"unreadable lead-time output: {e}") from e
    shown = []
    for p, event in zip(payload, events):
        v = verdicts.get(p["id"])
        if not v:
            continue  # skipped by the model: not surfaced early
        lead = max(0, min(int(v["lead_days"]), max_days))
        if lead and p["days_until"] <= lead:
            shown.append(surfaced(event, v["prep"] or f"{v['importance'].title()} importance"))
    return shown


def fallback_upcoming(events: list[Item], now: datetime) -> list[Surfaced]:
    return [
        surfaced(e, "Deadline")
        for e in events
        if "deadline" in e.urgency_hints
        and (local_date(e.timestamp, now) - now.date()).days <= FALLBACK_LEAD_DAYS
    ]


# --- Ranking ---------------------------------------------------------------

def candidates(
    results: dict[str, ConnectorResult], upcoming: list[Surfaced], now: datetime, hidden: set[str]
) -> list[Surfaced]:
    """Personal items that could need action today, minus checked-off and not-needed ones."""
    out: list[Surfaced] = []
    for name in CANDIDATE_CONNECTORS:
        result = results.get(name)
        if not result:
            continue
        items = [i for i in result.items if i.section == "personal"]
        if name == "calendar":
            items = split_events(items, now)[0]
        elif name == "email":
            items = [i for i in items if {"reply_needed", "deadline"} & set(i.urgency_hints)]
        out.extend(surfaced(i) for i in items)
    out.extend(u for u in upcoming if u.item.section == "personal")
    seen: set[str] = set()
    unique = []
    for c in out:
        if c.key not in hidden and c.key not in seen:
            seen.add(c.key)
            unique.append(c)
    return unique


def candidate_payload(cands: list[Surfaced]) -> list[dict]:
    """Email timestamps are send times and event timestamps are start times; label them so."""
    out = []
    for n, c in enumerate(cands):
        when = "sent" if c.item.source.startswith("email.") else "starts"
        out.append({
            "id": f"c{n}",
            "source": c.item.source,
            "title": c.item.title,
            "summary": c.why or c.item.summary,
            when: c.item.timestamp,
            "due": c.item.due,
            "hints": c.item.urgency_hints,
        })
    return out


def build_rank_prompt(payload: list[dict], feedback: list[dict], now: datetime, cap: int) -> str:
    return _fill(
        RANK_PROMPT,
        today=now.date().isoformat(),
        cap=str(cap),
        feedback_json=_dump(feedback),
        candidates_json=_dump(payload),
    )


def rank(cands: list[Surfaced], feedback: list[dict], now: datetime, config: dict, client: Any) -> list[Surfaced]:
    if not cands:
        return []
    cap = config["actions"]["cap"]
    payload = candidate_payload(cands)
    by_id = {p["id"]: c for p, c in zip(payload, cands)}
    out = _call(client, config, build_rank_prompt(payload, feedback, now, cap), RANK_SCHEMA)
    try:
        picks = [(a["id"], a["why"]) for a in out["actions"]]
    except (KeyError, TypeError) as e:
        raise ActionsError(f"unreadable ranking output: {e}") from e
    ranked, seen = [], set()
    for cid, why in picks:
        if cid in by_id and cid not in seen:
            seen.add(cid)
            ranked.append(Surfaced(item=by_id[cid].item, key=by_id[cid].key, why=why))
    return ranked[:cap]


def fallback_rank(cands: list[Surfaced], cap: int) -> list[Surfaced]:
    """Items with urgency hints, most urgent then soonest due."""
    flagged = [c for c in cands if c.item.urgency_hints]

    def key(c: Surfaced) -> tuple[int, str]:
        return min(URGENCY_ORDER.get(h, 9) for h in c.item.urgency_hints), c.item.due or "9999"

    return [
        Surfaced(item=c.item, key=c.key, why=c.why or ", ".join(h.replace("_", " ") for h in c.item.urgency_hints))
        for c in sorted(flagged, key=key)[:cap]
    ]


# --- Entry point -----------------------------------------------------------

def select(
    results: dict[str, ConnectorResult], config: dict, now: datetime, data_dir: Path, client: Any = None
) -> dict[str, Any]:
    """{"actions": [Surfaced], "upcoming": [Surfaced], "note": str | None}."""
    feedback = store.recent_feedback(data_dir, config["actions"]["feedback_limit"])
    cal = results.get("calendar")
    later = split_events([e for e in (cal.items if cal else []) if e.section == "personal"], now)[1]
    errors = []
    try:
        client = client or _client()
    except ActionsError as e:
        errors.append(str(e))
        client = None

    upcoming = None
    if client:
        try:
            upcoming = rate_lead_times(later, feedback, now, config, client)
        except ActionsError as e:
            errors.append(f"lead times: {e}")
    if upcoming is None:
        upcoming = fallback_upcoming(later, now)

    cands = candidates(results, upcoming, now, store.hidden_keys(data_dir))
    actions = None
    if client:
        try:
            actions = rank(cands, feedback, now, config, client)
        except ActionsError as e:
            errors.append(f"ranking: {e}")
    if actions is None:
        actions = fallback_rank(cands, config["actions"]["cap"])

    note = "AI unavailable, simple rules stood in (" + "; ".join(errors) + ")." if errors else None
    return {"actions": actions, "upcoming": upcoming, "note": note}
