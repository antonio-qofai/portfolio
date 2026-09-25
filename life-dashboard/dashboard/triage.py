"""LLM email triage: keep only pressing school, work and internship email.

The model sees sender, subject, snippet and date per thread, never bodies.
If the call fails (no API key, network, bad output), callers fall back to
the rule-based reply-needed flags.
"""

from __future__ import annotations

import json
import os
from datetime import date
from typing import Any

import anthropic

from dashboard.config import ROOT, load_env

PROMPT = ROOT / "prompts" / "email_triage.md"

SCHEMA = {
    "type": "object",
    "properties": {
        "emails": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "pressing": {"type": "boolean"},
                    "reply_needed": {"type": "boolean"},
                    "reason": {"type": "string"},
                    "due": {"type": "string"},
                },
                "required": ["id", "pressing", "reply_needed", "reason", "due"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["emails"],
    "additionalProperties": False,
}


class TriageError(RuntimeError):
    pass


def build_prompt(emails: list[dict[str, str]], today: date) -> str:
    return (
        PROMPT.read_text()
        .replace("{today}", today.isoformat())
        .replace("{emails_json}", json.dumps(emails, ensure_ascii=False, indent=1))
    )


def parse_response(text: str, ids: set[str]) -> dict[str, dict[str, Any]]:
    """Map email id -> verdict. Unknown ids are dropped; missing ids raise."""
    try:
        verdicts = {v["id"]: v for v in json.loads(text)["emails"] if v["id"] in ids}
    except (ValueError, KeyError, TypeError) as e:
        raise TriageError(f"unreadable triage output: {e}") from e
    missing = ids - verdicts.keys()
    if missing:
        raise TriageError(f"triage skipped {len(missing)} email(s)")
    for v in verdicts.values():
        try:
            v["due"] = date.fromisoformat(v["due"]).isoformat() if v["due"] else None
        except ValueError:
            v["due"] = None
    return verdicts


def classify(emails: list[dict[str, str]], model: str, today: date, client: Any = None) -> dict[str, dict[str, Any]]:
    """emails: [{id, inbox, from, subject, snippet, date}]. Returns id -> verdict."""
    if not emails:
        return {}
    if client is None:
        load_env()
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise TriageError("ANTHROPIC_API_KEY is not set in .env")
        client = anthropic.Anthropic()
    try:
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            messages=[{"role": "user", "content": build_prompt(emails, today)}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        )
    except anthropic.AnthropicError as e:
        raise TriageError(f"Claude API call failed: {e}") from e
    if response.stop_reason != "end_turn":
        raise TriageError(f"triage stopped early: {response.stop_reason}")
    text = next((b.text for b in response.content if b.type == "text"), "")
    return parse_response(text, {e["id"] for e in emails})
