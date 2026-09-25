"""Stage 0, intake tagging. PRD.md section 4.

Reads the term, the weekly hours, and any stated deadline off a posting, once,
at first sight, and stores the result. It never re-runs on a posting already
tagged. The timing rules in PRD section 5 all key off the term, and postings
state their term too loosely for string matching alone to be safe, so a missed
term would be a silent coverage miss. That is the failure this whole system
exists to prevent, which is why one small model call is worth it.

Two things keep this inside the ten dollar monthly budget in PRD section 2.

The cheap path runs first. An aggregator feed states the term outright in a
field, so when that field maps cleanly onto a term in sources/prefilter.toml
the answer is already known and no call is made. 1,527 of the open postings
arrived that way.

The prefilter runs before this, not after. A posting killed on its title, its
location, or its degree requirement never reaches Stage 0, so the agent never
pays to read it. That is a departure from the order PRD section 4 implies and
it is deliberate; see CHANGELOG.md for 2026-08-07.

The instruction text is in sources/intake_prompt.md and never in this file, on
the same rule that keeps the rubric out of code. Output is structured JSON
enforced by the API, never prose, per PRD section 4.
"""

import json
import re
from datetime import date

from . import config, prefilter

# Anthropic's minimum cacheable prefix on Haiku 4.5 is 4,096 tokens and this
# prompt is a few hundred, so prompt caching is deliberately not used here. It
# would cost the cache-write premium and never produce a read. Caching belongs
# on the rubric plus few-shot prefix in the Milestone 6 ranker, where the prefix
# is large and genuinely repeated, which is what PRD section 4 is describing.

# The API enforces this shape, so downstream code never parses free text. Every
# field is required and every field accepts "unknown", because a model that
# cannot answer must be able to say so rather than inventing a term.
SCHEMA = {
    "type": "object",
    "properties": {
        "term_stated": {
            "type": "string",
            "description": "The term the role runs in, in the posting's own "
                           "words, or 'unknown'. Semicolon-separated if several.",
        },
        "term_evidence": {
            "type": "string",
            "description": "Shortest quoted phrase the term came from, or ''.",
        },
        "weekly_hours": {
            "type": "string",
            "description": "Stated weekly commitment: a number, a range, a "
                           "phrase the posting uses, or 'unknown'.",
        },
        "hours_evidence": {
            "type": "string",
            "description": "Shortest quoted phrase the hours came from, or ''.",
        },
        "stated_deadline": {
            "type": "string",
            "description": "Explicit application deadline as YYYY-MM-DD, or "
                           "'unknown'. Rolling review is not a deadline.",
        },
    },
    "required": [
        "term_stated", "term_evidence", "weekly_hours", "hours_evidence",
        "stated_deadline",
    ],
    "additionalProperties": False,
}

UNKNOWN = "unknown"
_PROMPT: str | None = None


class TaggerUnavailable(RuntimeError):
    """No API key, or the SDK is missing. Tagging is skipped, never fatal."""


def load_prompt(path=None) -> str:
    """The instruction text below the horizontal rule in intake_prompt.md.

    Everything above the rule is a note to the owner about what the file is for
    and must not reach the model.
    """
    global _PROMPT
    if _PROMPT is None or path is not None:
        text = (path or config.INTAKE_PROMPT_PATH).read_text()
        _, _, body = text.partition("\n---\n")
        prompt = (body or text).strip()
        if path is not None:
            return prompt
        _PROMPT = prompt
    return _PROMPT


def _client():
    if not config.stage0_configured():
        raise TaggerUnavailable("ANTHROPIC_API_KEY is not set")
    try:
        import anthropic
    except ImportError as exc:
        raise TaggerUnavailable(f"anthropic SDK not installed: {exc}") from exc
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


def _clean(value, default: str = "") -> str:
    value = (value or "").strip()
    return default if value.lower() in ("", UNKNOWN, "n/a", "none") else value


def _normalize_deadline(value: str) -> str:
    """Keep only a real ISO date, and only one that has not already passed.

    Postings often print a date with no year, and the model then supplies one.
    On the first live run it read "July 26" off an open Anthropic posting and
    returned 2024-07-26. A deadline in the past on a posting that is open today
    is a misread, not a fact, and PRD section 12 pushes this column straight to
    Google Calendar, so a wrong date becomes a wrong reminder. Drop it and let
    the posting carry no deadline, which is the honest answer.
    """
    match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", value or "")
    if not match:
        return ""
    try:
        if date.fromisoformat(match.group(1)) < date.today():
            return ""
    except ValueError:
        return ""
    return match.group(1)


def from_feed(posting: dict) -> dict | None:
    """The free path. Returns a tag record, or None if the feed cannot answer.

    The feeds publish the term as a field. When that field maps onto exactly one
    term in prefilter.toml, the answer is already known and a model call would
    be pure waste. Feeds carry no description, so the hours and the deadline
    stay unknown, and the part-time-only terms will surface those with an
    unclear commitment flag. That is the correct outcome, not a gap.
    """
    stated = (posting.get("feed_terms") or "").strip()
    key, _ = prefilter.match_term(stated, prefilter.load_rules())
    if not key:
        return None
    return {
        "term": key,
        "term_stated": stated,
        "term_evidence": f"aggregator feed term field: {stated}",
        "weekly_hours": "",
        "hours_evidence": "",
        "stated_deadline": "",
        "stage0_source": "feed",
        "input_tokens": 0,
        "output_tokens": 0,
    }


def _posting_text(posting: dict) -> str:
    """What the model reads. Kept small; this is billed per token."""
    parts = [
        f"Company: {posting.get('company') or ''}",
        f"Title: {posting.get('title') or ''}",
        f"Location: {posting.get('location') or ''}",
    ]
    if posting.get("feed_terms"):
        parts.append(f"Term stated by the source feed: {posting['feed_terms']}")
    description = (posting.get("description") or "")[: config.DESCRIPTION_CHARS]
    parts.append(f"\nPosting text:\n{description or '(no description available)'}")
    return "\n".join(parts)


def from_model(client, posting: dict) -> dict:
    """The paid path. One small Haiku call, structured JSON out."""
    response = client.messages.create(
        model=config.STAGE0_MODEL,
        max_tokens=config.STAGE0_MAX_TOKENS,
        system=load_prompt(),
        messages=[{"role": "user", "content": _posting_text(posting)}],
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
    )

    text = next((b.text for b in response.content if b.type == "text"), "{}")
    data = json.loads(text)

    stated = _clean(data.get("term_stated"))
    key, _ = prefilter.match_term(stated, prefilter.load_rules())

    return {
        # The term column stays Stage 0's to write, but the mapping from what a
        # posting says to a term key belongs to prefilter.toml. The model reports
        # words; the data file decides what they mean.
        "term": key or "unknown",
        "term_stated": stated,
        "term_evidence": _clean(data.get("term_evidence")),
        "weekly_hours": _clean(data.get("weekly_hours")),
        "hours_evidence": _clean(data.get("hours_evidence")),
        "stated_deadline": _normalize_deadline(_clean(data.get("stated_deadline"))),
        "stage0_source": "model",
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }


def estimate_cost(input_tokens: int, output_tokens: int) -> float:
    return (
        input_tokens / 1_000_000 * config.STAGE0_INPUT_PRICE
        + output_tokens / 1_000_000 * config.STAGE0_OUTPUT_PRICE
    )


def tag_batch(postings: list[dict], max_calls: int | None = None, verbose=False,
              errors: list | None = None):
    """Tag a batch, feed-first, stopping at the model call budget.

    Yields (posting, tag) pairs. A posting the budget could not reach is simply
    not yielded; it stays untagged and is picked up on the next run, which is
    why hitting the cap loses nothing.

    One posting failing must not end the batch, but a failure must not vanish
    either: pass a list as `errors` and every failure is appended to it, so the
    caller can report them whether or not it asked for per-posting output.
    """
    budget = config.STAGE0_MAX_CALLS if max_calls is None else max_calls
    client = None

    for posting in postings:
        tag = from_feed(posting)
        if tag is None:
            if budget <= 0:
                continue
            if client is None:
                client = _client()
            try:
                tag = from_model(client, posting)
            except TaggerUnavailable:
                raise
            except Exception as exc:  # one bad posting must not end the batch
                if errors is not None:
                    errors.append((posting.get("company"), posting.get("title"), str(exc)))
                if verbose:
                    print(f"  tag FAIL {posting.get('company')}: {exc}")
                continue
            budget -= 1

        if verbose:
            print(
                f"  tag  {tag['stage0_source']:5s}  {posting.get('company')}: "
                f"{posting.get('title')} -> {tag['term']}"
                f"{', ' + tag['weekly_hours'] if tag['weekly_hours'] else ''}"
            )
        yield posting, tag
