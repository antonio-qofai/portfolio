#!/usr/bin/env python3
"""Assembles a 30-day forward calendar by asking a model to sequence the corpus.

Build order item 9, and item 10 folded into it.

WHAT THIS DOES

Reads every post the content agents currently hold, reads what the outside
world published recently and what QofAI is currently saying, hands all of it
to Claude with the sequencing criteria, and gets back a dated ordering with a
written reason for every placement plus a stated arc for the month. The
result is a `CalendarWindow` from `calendar_model/slots.py`, validated by the
same schema everything else in this build already uses, so the display, the
state store, and the approval horizons all work on it unchanged.

WHY A MODEL CALL RATHER THAN A SCORING FUNCTION

The original plan scored a proposed ordering against the thread taxonomy in
`narrative/threads.yaml`. Two things killed it, and both are recorded at
length in `sequencing_criteria.yaml`. The tags carried almost no signal
(`pilot-to-production-gap` sits on 8 of 8 atomizer posts while most other
threads appear exactly once, so "these two are similar" never fires in
between), and a fixed vocabulary describing a company that repositioned once
in three weeks goes stale faster than anyone maintains it. Reading the posts
answers the same question with nothing to keep current.

The taxonomy is still loaded and still authoritative, for the approval
horizons, the lens vocabulary, and the monthly anchor. It is no longer a
scoring vocabulary.

WHAT IS CONFIGURATION AND WHAT IS CODE

Every criterion, the cadence, the model, the alternatives count, which
sources supply candidates, and which files supply context live in
`sequencing_criteria.yaml`. This file contains no criterion text and no
cadence number. The prompt is assembled from that config at runtime, so
changing how the calendar thinks never means editing Python.

HOW THIS DEGRADES, AND WHY DIFFERENTLY FROM THE READERS

The ingestion readers degrade to an empty list on failure, because one dead
source must not end a four-source run. Assembly is a single operation with no
partial result, and an empty calendar returned quietly would be
indistinguishable from a month with nothing to say. So a failed model call
reports and exits non-zero. A *thin* calendar, one the corpus genuinely
cannot fill, is a success and says so in its own arc.

Usage:

    ../.venv/bin/python3 assemble.py --dry-run
    ../.venv/bin/python3 assemble.py --out ../state/window-2026-09.json
    ../.venv/bin/python3 assemble.py --against ../state/window-2026-09.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

_HERE = Path(__file__).resolve().parent
_AGENT_ROOT = _HERE.parent

sys.path.insert(0, str(_AGENT_ROOT / "calendar_model"))
sys.path.insert(0, str(_AGENT_ROOT / "ingestion"))

import slots as slots_mod  # noqa: E402
import state_store  # noqa: E402
from slots import CalendarWindow, Slot, Taxonomy  # noqa: E402

DEFAULT_CRITERIA_PATH = _HERE / "sequencing_criteria.yaml"
DEFAULT_TAGS_PATH = _AGENT_ROOT / "narrative" / "corpus_tags.yaml"
DEFAULT_ATOMIZER_PATH = _AGENT_ROOT.parent / "content-atomizer" / "output"
DEFAULT_VCB_PATH = _AGENT_ROOT.parent / "value-creation-briefing" / "drafts"

# Where the model's answer is bounded. Anything the model may name has to
# come from data we already hold, so a hallucinated item id or thread cannot
# survive into a window.
_MAX_RATIONALE_CHARS = 1200


def _warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def load_criteria(path: str | Path = DEFAULT_CRITERIA_PATH) -> dict:
    """Reads the sequencing policy.

    Returns an empty dict on any failure rather than raising, so a caller can
    report one clear reason instead of a traceback. An empty policy is caught
    by `validate_criteria`, which is what actually stops a run.
    """
    path = Path(path)
    try:
        with path.open("r", encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle)
    except FileNotFoundError:
        _warn(f"sequencing criteria not found at {path}")
        return {}
    except (OSError, yaml.YAMLError) as exc:
        _warn(f"could not read sequencing criteria at {path}: {exc}")
        return {}
    if not isinstance(loaded, dict):
        _warn(f"sequencing criteria at {path} is not a mapping")
        return {}
    return loaded


def validate_criteria(criteria: dict) -> list[str]:
    """Returns the reasons this policy cannot drive an assembly, if any.

    Deliberately strict about the two things whose absence would otherwise
    produce a confident, wrong calendar: an empty criteria list would ask the
    model to sequence with no rules, and a missing cadence would let it pick
    its own posting rate.
    """
    problems: list[str] = []
    if not criteria:
        return ["the sequencing criteria file is missing or empty"]

    rules = criteria.get("criteria")
    if not isinstance(rules, list) or not rules:
        problems.append("no `criteria:` rules are defined, so there is no policy to apply")
    else:
        for index, rule in enumerate(rules):
            if not isinstance(rule, dict) or not str(rule.get("rule") or "").strip():
                problems.append(f"criteria entry {index} has no `rule:` text")

    problems += _cadence_problems(criteria.get("cadence"))

    model = criteria.get("model")
    if not isinstance(model, dict) or not str(model.get("id") or "").strip():
        problems.append("no `model.id`, so there is nothing to call")

    overrides = criteria.get("lenses")
    if overrides is not None and not isinstance(overrides, dict):
        problems.append("`lenses:` must map a lens id to its overrides")
    for lens, override in (overrides or {}).items() if isinstance(overrides, dict) else ():
        if not isinstance(override, dict):
            problems.append(f"`lenses.{lens}` must be a mapping")
            continue
        unknown = sorted(set(override) - LENS_OVERRIDABLE)
        if unknown:
            problems.append(
                f"`lenses.{lens}` overrides {', '.join(unknown)}; only "
                f"{', '.join(sorted(LENS_OVERRIDABLE))} may differ by lens"
            )
        if "cadence" in override:
            problems += [f"`lenses.{lens}`: {p}" for p in _cadence_problems(override["cadence"])]

    return problems


def _cadence_problems(cadence) -> list[str]:
    if not isinstance(cadence, dict):
        return ["no `cadence:` block, so posts per week is undefined"]
    low = cadence.get("posts_per_week_min")
    high = cadence.get("posts_per_week_max")
    if not isinstance(low, int) or not isinstance(high, int):
        return ["cadence needs integer posts_per_week_min and posts_per_week_max"]
    if low < 1 or high < low:
        return [f"cadence range {low}-{high} is not a usable floor and ceiling"]
    return []


# What a lens may set for itself. Everything else is the shared policy,
# since a per-lens copy of the criteria would let the calendars drift apart.
LENS_OVERRIDABLE = frozenset({"cadence", "posting_days"})


def criteria_for_lens(criteria: dict, lens: str | None) -> dict:
    """The policy as one lens sees it: the shared file plus that lens's overrides.

    Callers apply this once, right after resolving the lens, so every reader
    downstream (the prompt, the slot count, the refresh's runway) sees the
    lens's cadence and days without knowing an override exists.
    """
    override = (criteria.get("lenses") or {}).get(lens) if lens else None
    if not isinstance(override, dict):
        return criteria
    merged = dict(criteria)
    for key in LENS_OVERRIDABLE & set(override):
        merged[key] = override[key]
    return merged


def resolve_lens(criteria: dict, taxonomy: Taxonomy, override: str | None = None) -> str | None:
    """Which calendar to assemble.

    Null in config means "the v1 lens the taxonomy declares", so standing up
    the Casey and Blake calendars is a taxonomy change rather than an edit
    to the sequencing policy.

    `override` is the `--lens` flag, and it wins over both. Three v1 lenses
    means "which calendar" is a question about this run rather than a
    property of the policy, and the alternative, three near-identical
    criteria files differing in one key, would fork the policy that every
    lens is supposed to share. The flag chooses among lenses the taxonomy
    already declares; it cannot invent one.
    """
    if override:
        if override not in taxonomy.calendar_lens_ids:
            _warn(
                f"--lens {override!r} is not a calendar lens in the taxonomy; "
                f"known: {', '.join(sorted(taxonomy.calendar_lens_ids)) or 'none'}"
            )
            return None
        return str(override)

    configured = criteria.get("lens")
    if configured:
        if configured not in taxonomy.calendar_lens_ids:
            _warn(
                f"configured lens {configured!r} is not a calendar lens in the taxonomy; "
                f"known: {', '.join(sorted(taxonomy.calendar_lens_ids)) or 'none'}"
            )
            return None
        return str(configured)

    v1 = sorted(taxonomy.v1_lens_ids)
    if not v1:
        _warn("the taxonomy declares no v1 lens and none is configured")
        return None
    if len(v1) > 1:
        _warn(
            f"the taxonomy declares several v1 lenses ({', '.join(v1)}) and this run named "
            f"none; using {v1[0]}. Pass --lens to say which calendar you meant."
        )
    return v1[0]


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------


@dataclass
class Candidate:
    """One piece of real content the calendar may schedule."""

    item_ref: str  # "<source-agent>/<item-id>", the same ref the store uses
    source: str
    persona: str | None
    title: str
    text: str
    word_count: int
    source_path: str
    note: str = ""


def _load_tags(path: str | Path = DEFAULT_TAGS_PATH) -> dict:
    path = Path(path)
    try:
        with path.open("r", encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle) or {}
    except FileNotFoundError:
        _warn(f"corpus tags not found at {path}; nothing will be excluded as unschedulable")
        return {}
    except (OSError, yaml.YAMLError) as exc:
        _warn(f"could not read corpus tags at {path}: {exc}")
        return {}
    items = loaded.get("items")
    return items if isinstance(items, dict) else {}


def _source_config(criteria: dict, source_id: str) -> dict:
    for entry in criteria.get("candidate_sources") or []:
        if isinstance(entry, dict) and entry.get("id") == source_id:
            return entry
    return {}


def gather_candidates(
    criteria: dict,
    lens: str,
    atomizer_path: str | Path = DEFAULT_ATOMIZER_PATH,
    vcb_path: str | Path = DEFAULT_VCB_PATH,
    tags_path: str | Path = DEFAULT_TAGS_PATH,
    excluded: list[str] | None = None,
) -> list[Candidate]:
    """Reads the schedulable corpus live from the dependency agents' folders.

    Nothing is copied into this folder and nothing is read from our own
    store, so the candidate set cannot go stale against what Alex and Robin
    currently hold. Content is read here (rather than referenced, as the
    store does) because the model has to read the posts to sequence them.

    `excluded` collects the refs dropped and why, so a caller can report a
    shrinking corpus instead of silently sequencing less than it thinks.
    """
    tags = _load_tags(tags_path)
    dropped = excluded if excluded is not None else []
    candidates: list[Candidate] = []

    def unschedulable(ref: str) -> str:
        entry = tags.get(ref)
        if isinstance(entry, dict) and entry.get("schedulable") is False:
            return str(entry.get("note") or "marked unschedulable in corpus_tags.yaml").strip()
        return ""

    atomizer_conf = _source_config(criteria, "content-atomizer")
    if atomizer_conf:
        try:
            import atomizer_reader

            posts = atomizer_reader.read_atomizer_posts(str(atomizer_path))
        except Exception as exc:  # the reader itself, or a missing sibling folder
            _warn(f"could not read Content Atomizer posts at {atomizer_path}: {exc}")
            posts = []
        for post in posts:
            ref = f"content-atomizer/{post.post_id}"
            if atomizer_conf.get("filter_by_persona") and not _persona_matches(post.persona, lens):
                dropped.append(f"{ref} (persona {post.persona!r}, not the {lens} lens)")
                continue
            reason = unschedulable(ref)
            if reason:
                dropped.append(f"{ref} ({reason})")
                continue
            candidates.append(
                Candidate(
                    item_ref=ref,
                    source="content-atomizer",
                    persona=post.persona,
                    title=post.post_id,
                    text=post.body,
                    word_count=len(post.body.split()),
                    source_path=post.source_path,
                )
            )

    vcb_conf = _source_config(criteria, "value-creation-briefing")
    if vcb_conf:
        try:
            import vcb_reader

            drafts = vcb_reader.read_persona_blog_drafts(str(vcb_path))
        except Exception as exc:
            _warn(f"could not read Value Creation Briefing drafts at {vcb_path}: {exc}")
            drafts = []
        for draft in drafts:
            stem = Path(draft.source_path).stem
            ref = f"value-creation-briefing/{stem}"
            if vcb_conf.get("filter_by_persona") and not _persona_matches(draft.persona, lens):
                dropped.append(f"{ref} (persona {draft.persona!r}, not the {lens} lens)")
                continue
            reason = unschedulable(ref)
            if reason:
                dropped.append(f"{ref} ({reason})")
                continue
            candidates.append(
                Candidate(
                    item_ref=ref,
                    source="value-creation-briefing",
                    persona=draft.persona,
                    title=draft.title,
                    text=draft.text,
                    word_count=len(draft.text.split()),
                    source_path=draft.source_path,
                )
            )

        if vcb_conf.get("include_briefings"):
            try:
                import vcb_reader

                briefings = vcb_reader.read_briefing_drafts(str(vcb_path))
            except Exception as exc:
                _warn(f"could not read Value Creation Briefing briefings: {exc}")
                briefings = []
            for briefing in briefings:
                stem = Path(briefing.source_path).stem
                ref = f"value-creation-briefing/{stem}"
                reason = unschedulable(ref)
                if reason:
                    dropped.append(f"{ref} ({reason})")
                    continue
                candidates.append(
                    Candidate(
                        item_ref=ref,
                        source="value-creation-briefing",
                        persona=None,
                        title=f"Monthly briefing {briefing.month}",
                        text=briefing.text,
                        word_count=len(briefing.text.split()),
                        source_path=briefing.source_path,
                    )
                )

    candidates.sort(key=lambda c: c.item_ref)
    return candidates


def _persona_matches(persona: str | None, lens: str) -> bool:
    """Personas and lens ids are both free text across agents, so compare loosely.

    The agents disagree about spelling ("Casey" vs "casy" was found and
    normalized once already), and a strict match would silently drop content
    the moment someone capitalizes differently.
    """
    if not persona:
        return False
    return persona.strip().casefold() == lens.strip().casefold()


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


@dataclass
class Context:
    """Everything the model reads that it may not schedule."""

    market_items: list = field(default_factory=list)
    documents: list = field(default_factory=list)  # (label, path, text)
    published: list = field(default_factory=list)  # (label, path, text)
    notes: list = field(default_factory=list)  # what could not be read
    feedback: list = field(default_factory=list)  # declined posts and why, see gather_feedback
    season: tuple | None = None  # (label, path, text), the atomizer's current season brief


def find_season_brief(conf: dict, agent_root: Path = _AGENT_ROOT) -> Path | None:
    """The newest season brief the content agent has written, or None.

    Added 2026-09-23. The posts on the calendar were written by Content
    Atomizer against its season brief, so that brief is the arc they were
    built to make. The newest season wins by the number in its file name, so
    Season 2 takes over the day the atomizer adds its brief, with no change
    here.
    """
    root = agent_root / str(conf.get("root") or "")
    pattern = str(conf.get("pattern") or "")
    if not pattern or not root.is_dir():
        return None
    numbered = []
    for path in root.glob(pattern):
        match = re.search(r"season-(\d+)", path.name)
        if match:
            numbered.append((int(match.group(1)), path))
    return max(numbered)[1] if numbered else None


def gather_context(criteria: dict, agent_root: Path = _AGENT_ROOT) -> Context:
    """Reads the arc inputs: the outside world, and what QofAI is saying now.

    Every failure here is a note rather than an abort. Losing one input makes
    the arc thinner and the run still produces a calendar, which is the right
    trade for context as opposed to for candidates.
    """
    context = Context()
    conf = criteria.get("context") or {}

    # Value Creation Briefing's scored insights first, since 2026-09-22. The
    # RSS scan stays as the fallback, so losing the insights read leaves the
    # arc exactly as informed as it was before the swap, and says so.
    insights = conf.get("briefing_insights") or {}
    if insights.get("enabled"):
        try:
            import briefing_insights_reader

            items, why = briefing_insights_reader.read_briefing_insights(
                insights["base_id"],
                insights["table"],
                lookback_days=insights.get("lookback_days", 14),
                max_items=insights.get("max_items", 25),
                relevance=insights.get("relevance"),
            )
            context.market_items = items
            if not items:
                context.notes.append(
                    f"briefing insights unavailable ({why}); used the RSS market scan instead"
                )
        except Exception as exc:
            context.notes.append(
                f"briefing insights unavailable ({exc}); used the RSS market scan instead"
            )

    market = conf.get("market_scan") or {}
    if market.get("enabled") and not context.market_items:
        try:
            import market_scan_reader

            context.market_items = market_scan_reader.read_market_scan(
                lookback_days=market.get("lookback_days"),
                max_items_total=market.get("max_items"),
            )
        except Exception as exc:
            context.notes.append(f"market scan unavailable: {exc}")

    season = conf.get("season_arc") or {}
    if season.get("enabled"):
        path = find_season_brief(season, agent_root)
        if path is None:
            context.notes.append(
                f"no season brief matching {season.get('pattern')!r} under {season.get('root')!r}"
            )
        else:
            try:
                label = str(season.get("label") or "The content agent's season arc")
                context.season = (label, str(path), path.read_text(encoding="utf-8"))
            except OSError as exc:
                context.notes.append(f"season brief unavailable ({path}): {exc}")

    for key, sink in (("narrative_files", context.documents), ("published_history", context.published)):
        for entry in conf.get(key) or []:
            if not isinstance(entry, dict) or not entry.get("path"):
                continue
            path = agent_root / str(entry["path"])
            label = str(entry.get("label") or entry["path"])
            try:
                sink.append((label, str(path), path.read_text(encoding="utf-8")))
            except OSError as exc:
                context.notes.append(f"{label} unavailable ({path}): {exc}")

    return context


# ---------------------------------------------------------------------------
# The request
# ---------------------------------------------------------------------------


# Fallbacks for a policy file that names no `limits:` block. Zero means
# "unbounded", which is what the module did before the block existed, so an
# older config keeps working and simply says nothing about length.
_NO_LIMITS = {"summary_words": 0, "rationale_words": 0, "arc_sentences": 0}


def gather_feedback(criteria: dict, lens: str, state_root: str | Path) -> list[dict]:
    """What this calendar's founders declined, and the reason each gave.

    Added 2026-09-23. A decline has always had to carry a reason, and the
    queue's own docstring said the refresh "needs to know what was wrong with
    the last answer", but nothing read it: the reason sat in the store and
    the next proposal was made without it. It now goes into the prompt, for
    the monthly assembly and for the weekly refresh alike, because both go
    through `assemble`.

    Only the newest decision per slot counts, so a decline that was taken
    back is not feedback any more. Only this lens's windows count, so Jordan's
    reasons do not steer Blake's calendar, and only the most recent
    `feedback_lookback_windows` of them, so a reason from a year ago does not
    outweigh last week's. A read failure is a warning and an empty list: an
    assembly without feedback is worse, and it is still a calendar.
    """
    try:
        paths = slots_mod._standing_window_paths(state_root, lens, "gather_feedback")
        lookback = criteria.get("feedback_lookback_windows")
        if isinstance(lookback, int) and lookback > 0:
            paths = paths[-lookback:]
        windows = {}
        for path in paths:
            window = slots_mod.read_window(path)
            if window is not None:
                windows[window.window_id] = window
        decisions = state_store.latest_decisions(root=state_root)
    except Exception as exc:  # noqa: BLE001
        _warn(f"could not read the decline reasons ({type(exc).__name__}: {exc}); assembling without them")
        return []

    feedback = []
    for slot_id, value in decisions.items():
        if not isinstance(value, dict) or value.get("state") != slots_mod.REJECTED:
            continue
        note = " ".join(str(value.get("note") or "").split())
        if not note or value.get("window_id") not in windows:
            continue
        feedback.append({
            "slot_id": slot_id,
            "item_ref": str(value.get("item_ref") or ""),
            "date": str(value.get("date") or ""),
            "by": str(value.get("by") or ""),
            "note": note,
        })
    return sorted(feedback, key=lambda f: (f["date"], f["slot_id"]))


def resolve_limits(criteria: dict) -> dict:
    """The length bounds this run applies, read from the policy.

    Missing or unreadable values fall back to unbounded rather than to a
    number chosen here, because a length judgement invented in Python is
    exactly the kind of policy `test_no_criterion_text_is_hardcoded` exists
    to keep out of this file.
    """
    configured = criteria.get("limits")
    if not isinstance(configured, dict):
        return dict(_NO_LIMITS)
    resolved = dict(_NO_LIMITS)
    for key in resolved:
        value = configured.get(key)
        if isinstance(value, int) and value > 0:
            resolved[key] = value
        elif value is not None:
            _warn(f"limits.{key} is {value!r}, which is not a positive whole number; ignoring it")
    return resolved


def build_output_schema(
    candidates: list[Candidate],
    taxonomy: Taxonomy,
    alternatives: int,
    limits: dict | None = None,
) -> dict:
    """Bounds the model's answer to things that exist.

    `item_ref` is an enum of the candidates actually read from disk, so the
    model cannot invent a post. Threads and forms are enums from the taxonomy,
    so tagging stays inside the known vocabulary even though the vocabulary is
    no longer what drives the ordering.
    """
    refs = [c.item_ref for c in candidates]
    thread_ids = sorted(taxonomy.thread_ids)
    form_ids = sorted(taxonomy.form_ids)
    limits = limits or _NO_LIMITS

    def bound(words: int, sentence: str) -> str:
        return f"{sentence} At most {words} words." if words else sentence

    alternative = {
        "type": "object",
        "properties": {
            "item_ref": {"type": "string", "enum": refs},
            "why": {"type": "string"},
        },
        "required": ["item_ref", "why"],
        "additionalProperties": False,
    }

    slot = {
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "ISO 8601 calendar date, YYYY-MM-DD"},
            "item_ref": {"type": "string", "enum": refs},
            "threads": {"type": "array", "items": {"type": "string", "enum": thread_ids}},
            "forms": {"type": "array", "items": {"type": "string", "enum": form_ids}},
            "responds_to": {
                "type": "array",
                "items": {"type": "string"},
                # Cannot be an enum: a legitimate reference may point at an
                # already-published post, which is not in the candidate set.
                # So the shape is stated instead, and enforced on the way in.
                "description": (
                    "Earlier posts this one answers or continues, published or scheduled. "
                    "Each must be a '<source-agent>/<item-id>' reference, exactly the form "
                    "used by item_ref. Prose descriptions are discarded, so omit a post "
                    "you cannot name in that form."
                ),
            },
            "summary": {
                "type": "string",
                "description": bound(
                    limits["summary_words"],
                    "What this post argues, in your own words, for a reader deciding "
                    "whether to open it. Not a title, and not a reason for the date: "
                    "the rationale field answers that.",
                ),
            },
            "rationale": {
                "type": "string",
                "description": bound(
                    limits["rationale_words"],
                    "Why this post is on this date, naming the criterion that drove it.",
                ),
            },
            "alternatives": {
                "type": "array",
                "items": alternative,
                # The cap is stated rather than enforced here. Structured
                # outputs reject array constraints (`maxItems` returns a 400,
                # "For 'array' type, property 'maxItems' is not supported"),
                # and the SDK only strips unsupported keywords when the schema
                # comes from a Pydantic model, not from a dict like this one.
                # So the number is carried three ways instead: in this
                # description, in the system prompt built from the same config
                # value, and enforced for real in `window_from_payload`, which
                # is the only one of the three that cannot be ignored.
                "description": (
                    f"At most {max(0, alternatives)} alternatives. Anything beyond "
                    f"that is discarded, so order them best first."
                ),
            },
        },
        "required": [
            "date", "item_ref", "threads", "forms", "responds_to",
            "summary", "rationale", "alternatives",
        ],
        "additionalProperties": False,
    }

    return {
        "type": "object",
        "properties": {
            "arc": {
                "type": "string",
                "description": (
                    "The argument the window makes as a whole."
                    + (f" At most {limits['arc_sentences']} sentences."
                       if limits["arc_sentences"] else "")
                ),
            },
            "slots": {"type": "array", "items": slot},
            "supply_note": {
                "type": "string",
                # Widened 2026-09-07. It used to fire only on a shortfall, which
                # meant the first window at two posts a week came back silent:
                # nine candidates landed on exactly nine posting days, so
                # nothing was missing and nothing was said. Running out is as
                # important as falling short, and it is less visible, because a
                # full calendar looks like health right up to the month that
                # has nothing behind it.
                "description": (
                    "Say something here whenever supply is the story. Two cases. If the corpus "
                    "could not fill the window at cadence, say which dates went unfilled and "
                    "why. If filling the window consumed all or nearly all of the schedulable "
                    "corpus, say so explicitly and say how much is left for the next window, "
                    "even though this one is complete. Empty only when neither is true."
                ),
            },
        },
        "required": ["arc", "slots", "supply_note"],
        "additionalProperties": False,
    }


def build_system_prompt(criteria: dict) -> str:
    """Assembles the instruction from the policy file.

    No rule is written here. Everything numbered below comes from
    `sequencing_criteria.yaml`, which is what makes the policy editable
    without touching code.
    """
    cadence = criteria.get("cadence") or {}
    low = cadence.get("posts_per_week_min")
    high = cadence.get("posts_per_week_max")
    alternatives = criteria.get("alternatives_per_slot", 0)
    days = [str(d).strip() for d in (criteria.get("posting_days") or []) if str(d).strip()]

    lines = [
        "You sequence a thought-leadership calendar. You do not write posts.",
        "",
        "Every post you place already exists and was written by another agent. Your job is",
        "to decide which existing post goes on which date, why, and what argument the month",
        "makes as a whole. Never invent a post, never reword one, and never schedule a post",
        "that is not in the candidate list.",
        "",
        (f"Cadence: schedule exactly {low} post{'' if low == 1 else 's'} per week."
         if low == high else
         f"Cadence: schedule {low} to {high} posts per week. That is a floor and a ceiling."),
    ]
    if days:
        # Stated as an override rather than as a bare rule, because the
        # canonical LinkedIn document is in the model's context and says
        # something different. Told plainly which one wins, a model follows
        # the instruction; left to infer it, it splits the difference and
        # explains the conflict in the supply note instead of scheduling.
        named = " or ".join(days) if len(days) < 3 else ", ".join(days[:-1]) + f", or {days[-1]}"
        lines += [
            "",
            f"Posting days: every post goes out on a {named}. Fill every one of those days "
            "inside the window that the corpus can cover, and place nothing on any other day.",
            "This overrides the cadence and posting-day rules in the canonical LinkedIn "
            "document you have been given, including its minimum gap between posts and its "
            "named posting slot. That document remains authoritative on everything else: "
            "voice, structure, length, format, and distribution.",
        ]
    if alternatives:
        lines.append(
            f"Every scheduled day carries exactly one post, which is your recommendation for "
            f"that date and not a question. Alongside it, offer up to {alternatives} swap "
            "candidates: posts that would also work on that date if a human rejects your pick, "
            "each with a one-line reason. Swap candidates must come from the candidate list and "
            "must not duplicate the post you scheduled there."
        )
    limits = resolve_limits(criteria)
    stated = []
    if limits["summary_words"]:
        stated.append(f"a summary of at most {limits['summary_words']} words saying what the "
                      "post argues")
    if limits["rationale_words"]:
        stated.append(f"a rationale of at most {limits['rationale_words']} words saying why it "
                      "is on that date")
    if stated:
        lines += [
            "",
            f"Length: every slot carries {' and '.join(stated)}. These are read on a call and "
            "shown on a card, so they are ceilings rather than targets. Going under is fine; "
            "going over means the card is cut off and nobody reads either one.",
        ]
    if limits["arc_sentences"]:
        lines.append(
            f"The window's arc is at most {limits['arc_sentences']} sentences, for the same "
            "reason."
        )

    lines += ["", "Rules, all of which apply:", ""]

    for index, rule in enumerate(criteria.get("criteria") or [], start=1):
        if not isinstance(rule, dict):
            continue
        text = " ".join(str(rule.get("rule") or "").split())
        if not text:
            continue
        lines.append(f"{index}. {text}")
        why = " ".join(str(rule.get("why") or "").split())
        if why:
            lines.append(f"   Why this matters: {why}")
        lines.append("")

    lines += [
        "Write summaries and rationales for an operating partner who will read them on a call.",
        "Be specific and concrete. A rationale that would be true of any post on any date is a",
        "failure, and so is a summary that would be true of any post in the corpus.",
    ]
    return "\n".join(lines)


def build_user_content(
    candidates: list[Candidate],
    context: Context,
    window_start: str,
    window_end: str,
    lens_label: str,
    standing: CalendarWindow | None = None,
    as_of: str | date | None = None,
) -> str:
    """Lays out the corpus, the context, and the window to fill.

    `as_of` is the date a refresh is being run on, and it only ever matters
    alongside `standing`. A date that has already passed cannot be
    rescheduled, so a slot sitting on one is as fixed as an approved slot,
    whatever its approval state says. The refresh cannot express that by
    marking those slots approved, because writing an approval nobody gave is
    the one thing this build exists to prevent, so it is said in the prompt
    instead and verified on the way back.
    """
    parts: list[str] = [
        f"Assemble the {lens_label} calendar for {window_start} through {window_end}.",
        "",
        f"## Candidate posts ({len(candidates)})",
        "",
        "These are every post available to schedule. The full text of each is included",
        "because you have to read them to sequence them.",
        "",
    ]
    for candidate in candidates:
        parts += [
            f"### {candidate.item_ref}",
            f"Source: {candidate.source}. Persona: {candidate.persona or 'none'}. "
            f"Words: {candidate.word_count}.",
            "",
            candidate.text.strip(),
            "",
        ]

    if context.season is not None:
        label, path, text = context.season
        parts += [
            f"## {label}",
            f"(from {path})",
            "",
            "The arc these posts were written for. Not schedulable.",
            "",
            text.strip(),
            "",
        ]

    if context.market_items:
        parts += [
            f"## What the outside world published recently ({len(context.market_items)} items)",
            "",
            "Context for timing and impetus. None of this is schedulable.",
            "",
        ]
        for item in context.market_items:
            when = getattr(item, "published_at", None) or "undated"
            source = getattr(item, "source_name", "") or getattr(item, "source_id", "")
            parts.append(f"- [{when}] {source}: {getattr(item, 'title', '')}")
            summary = " ".join(str(getattr(item, "summary", "") or "").split())
            if summary:
                parts.append(f"  {summary[:300]}")
        parts.append("")

    for label, path, text in context.documents:
        parts += [f"## {label}", f"(from {path})", "", text.strip(), ""]

    for label, path, text in context.published:
        parts += [
            f"## {label}",
            f"(from {path})",
            "",
            "Already published. Not schedulable. The arc continues from these.",
            "",
            text.strip(),
            "",
        ]

    if standing is not None:
        parts += [
            "## The standing calendar",
            "",
            "This window already exists and Jordan has acted on part of it. Slots marked",
            "approved or published are fixed: keep them exactly where they are. Propose",
            "changes only to draft slots, and only where something has actually changed.",
        ]
        if as_of is not None:
            parts += [
                "",
                f"Today is {_as_iso(as_of)}. Slots dated before today are equally fixed,",
                "whatever their approval state: a date that has passed cannot be",
                "rescheduled, and moving one would rewrite history rather than plan.",
            ]
        parts += [
            "",
            "```json",
            json.dumps(standing.to_dict(), indent=2, sort_keys=True),
            "```",
            "",
        ]

    if context.feedback:
        parts += [
            f"## What the founders declined, and why ({len(context.feedback)})",
            "",
            "Each of these was declined by a person reading this calendar, with the reason",
            "in their own words. Treat the reasons as direct editorial feedback: do not",
            "repeat what a reason objects to, whether that is the post, its date, or where",
            "it sits in the sequence. A declined post may still be scheduled if the reason",
            "was about its date or its neighbours rather than the post itself.",
            "",
        ]
        for item in context.feedback:
            who = f", declined by {item['by']}" if item.get("by") else ""
            when = item.get("date") or "undated"
            parts.append(f"- {item['item_ref']} on {when}{who}: \"{item['note']}\"")
        parts.append("")

    if context.notes:
        parts += ["## Context that could not be read", ""]
        parts += [f"- {note}" for note in context.notes]
        parts += [
            "",
            "Sequence with what you have and do not speculate about what these would have said.",
            "",
        ]

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# The call
# ---------------------------------------------------------------------------


class AssemblyError(RuntimeError):
    """The assembly could not be produced. Distinct from producing a thin one."""


def call_model(criteria: dict, system: str, user_content: str, schema: dict) -> dict:
    """One request. Returns the parsed payload, or raises AssemblyError.

    Imported lazily so the rest of this module, and its tests, run on a
    machine with no `anthropic` installed and no key set.
    """
    try:
        import anthropic
    except ImportError as exc:
        raise AssemblyError(
            "the `anthropic` package is not installed; "
            "run `.venv/bin/python3 -m pip install -r requirements.txt`"
        ) from exc

    model = criteria.get("model") or {}
    model_id = str(model.get("id"))
    max_tokens = int(model.get("max_tokens") or 16000)
    effort = str(model.get("effort") or "high")

    request: dict[str, Any] = {
        "model": model_id,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user_content}],
        "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": schema}},
    }
    if str(model.get("thinking") or "adaptive") == "adaptive":
        request["thinking"] = {"type": "adaptive"}

    # Every failure below becomes one sentence rather than a traceback. The
    # broad final clause is deliberate and was earned: with no credentials
    # resolvable the SDK raises a bare `TypeError` from header validation at
    # request time, which is neither an `anthropic.*` class nor something the
    # constructor raises, so a chain of specific handlers alone let the
    # commonest setup mistake in this whole module escape unformatted.
    try:
        client = anthropic.Anthropic()
        response = client.messages.create(**request)
    except anthropic.AuthenticationError as exc:
        raise AssemblyError(
            "the Anthropic API rejected the credentials; check ANTHROPIC_API_KEY"
        ) from exc
    except anthropic.RateLimitError as exc:
        raise AssemblyError(f"rate limited by the Anthropic API: {exc}") from exc
    except anthropic.APIStatusError as exc:
        raise AssemblyError(f"the Anthropic API returned {exc.status_code}: {exc}") from exc
    except anthropic.APIConnectionError as exc:
        raise AssemblyError(f"could not reach the Anthropic API: {exc}") from exc
    except TypeError as exc:
        raise AssemblyError(
            "no Anthropic credentials are available; export ANTHROPIC_API_KEY "
            f"(see README.md) or run `ant auth login`. The SDK said: {exc}"
        ) from exc
    except Exception as exc:  # noqa: BLE001 - a traceback here helps nobody
        raise AssemblyError(f"the model call failed: {type(exc).__name__}: {exc}") from exc

    if getattr(response, "stop_reason", None) == "refusal":
        detail = getattr(response, "stop_details", None)
        raise AssemblyError(f"the model declined the request ({getattr(detail, 'category', 'unknown')})")

    if getattr(response, "stop_reason", None) == "max_tokens":
        raise AssemblyError(
            f"the model ran out of room at max_tokens {max_tokens} before finishing its answer; "
            "raise model.max_tokens in sequencing_criteria.yaml"
        )

    text = next((b.text for b in response.content if b.type == "text"), None)
    if not text:
        raise AssemblyError("the model returned no text block to parse")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AssemblyError(f"the model's answer was not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise AssemblyError("the model's answer was not a JSON object")
    return payload


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def _clean_responds_to(raw: Any, item_ref: str) -> list[str]:
    """Keeps only references the validator will accept.

    `responds_to` is the one model-supplied field the output schema cannot
    bound with an enum, because a legitimate reference may point at an
    already-published post that is not in the candidate set. That made it the
    only field arriving unfiltered, and `bad-responds-to-ref` is an error
    rather than a warning, so a single prose reference ("the published
    adoption-gap post") failed an entire window that was otherwise sound.
    Dropping the reference loses one edge in the narrative graph; failing the
    window loses the whole month and the call that produced it.

    A self-reference is dropped for the same reason: `responds-to-self` is
    also an error, and a post answering itself is a slip rather than a claim
    worth failing a window over.
    """
    kept: list[str] = []
    for value in raw or []:
        text = str(value).strip()
        if not slots_mod.is_item_ref(text):
            _warn(f"dropping responds_to {text!r} on {item_ref}: not a '<agent>/<id>' reference")
            continue
        if text == item_ref:
            _warn(f"dropping responds_to {text!r} on {item_ref}: a post cannot answer itself")
            continue
        if text not in kept:
            kept.append(text)
    return kept


def _checked_arc(raw: Any, allowed: int) -> str:
    """The arc as written, with a warning if it runs past the sentence bound.

    Counted rather than cut, for the same reason the rationale is. Sentence
    counting is deliberately crude, a count of terminal punctuation, because
    the number exists to catch a paragraph where three sentences were asked
    for and not to adjudicate an abbreviation.
    """
    arc = " ".join(str(raw or "").split())
    if allowed and arc:
        sentences = len([p for p in re.split(r"[.!?]+(?:\s|$)", arc) if p.strip()])
        if sentences > allowed:
            _warn(f"the arc runs to {sentences} sentences against a limit of {allowed}; "
                  "kept whole, but it is longer than the page has room for")
    return arc


def window_from_payload(
    payload: dict,
    taxonomy: Taxonomy,
    lens: str,
    start: str | date,
    candidates: list[Candidate],
    generated_at: str | None = None,
    max_alternatives: int | None = None,
    limits: dict | None = None,
) -> CalendarWindow:
    """Turns the model's answer into a validated window.

    Slot ids are assigned here rather than asked for, because they must be
    unique and stable and there is no reason to spend model attention on a
    counter. Alternatives ride in `Slot.extra`, which the schema preserves
    verbatim, so offering options costs no schema change.

    `max_alternatives` is where the per-slot cap is actually enforced. The
    output schema cannot carry it, because structured outputs reject
    `maxItems`, so a model that returns nine alternatives gets a valid
    response and the trimming has to happen on the way in. `None` means no
    cap, which is what a caller that has no config to read should get.

    `limits` is checked here and never enforced. An over-long rationale is
    reported and kept whole, because truncating prose mid-sentence puts a
    worse card on the page than a long one does, and a model that has
    quietly stopped honouring the bound is something to see rather than to
    paper over. The hard `_MAX_RATIONALE_CHARS` cut below is a different
    thing: an upper bound on what the schema will hold at all, not a
    judgement about how much anyone will read.
    """
    limits = limits or _NO_LIMITS

    def over(text: str, allowed: int) -> int:
        words = len(text.split())
        return words if allowed and words > allowed else 0

    window = slots_mod.new_window(
        window_id=f"{lens}-{_as_iso(start)}",
        lens=lens,
        start=start,
        taxonomy=taxonomy,
        arc=_checked_arc(payload.get("arc"), limits["arc_sentences"]),
        generated_at=generated_at,
    )

    known_refs = {c.item_ref for c in candidates}
    raw_slots = payload.get("slots")
    if not isinstance(raw_slots, list):
        _warn("the model's answer had no slots array; the window will be empty")
        raw_slots = []

    ordered = sorted(
        (s for s in raw_slots if isinstance(s, dict)),
        key=lambda s: (str(s.get("date") or ""), str(s.get("item_ref") or "")),
    )
    for index, raw in enumerate(ordered, start=1):
        ref = str(raw.get("item_ref") or "")
        if ref not in known_refs:
            _warn(f"dropping a slot naming {ref!r}, which is not in the candidate set")
            continue
        rationale = " ".join(str(raw.get("rationale") or "").split())[:_MAX_RATIONALE_CHARS]
        summary = " ".join(str(raw.get("summary") or "").split())[:_MAX_RATIONALE_CHARS]
        for label, text, allowed in (
            ("summary", summary, limits["summary_words"]),
            ("rationale", rationale, limits["rationale_words"]),
        ):
            count = over(text, allowed)
            if count:
                _warn(f"the {label} for {ref} runs to {count} words against a limit of "
                      f"{allowed}; kept whole, but the card will be long")
        extra: dict[str, Any] = {}
        alternatives = [
            {"item_ref": a.get("item_ref"), "why": " ".join(str(a.get("why") or "").split())}
            for a in raw.get("alternatives") or []
            if isinstance(a, dict) and a.get("item_ref") in known_refs and a.get("item_ref") != ref
        ]
        if max_alternatives is not None and len(alternatives) > max_alternatives:
            _warn(
                f"slot {ref} returned {len(alternatives)} alternatives; keeping the "
                f"first {max_alternatives} per the configured cap"
            )
            alternatives = alternatives[:max_alternatives]
        if alternatives:
            extra["alternatives"] = alternatives

        window.slots.append(
            Slot(
                slot_id=f"{window.window_id}-{index:02d}",
                date=str(raw.get("date") or ""),
                item_ref=ref,
                lens=lens,
                threads=[t for t in raw.get("threads") or [] if t in taxonomy.thread_ids],
                forms=[f for f in raw.get("forms") or [] if f in taxonomy.form_ids],
                approval=slots_mod.DRAFT,
                responds_to=_clean_responds_to(raw.get("responds_to"), ref),
                summary=summary,
                rationale=rationale,
                extra=extra,
            )
        )

    supply = " ".join(str(payload.get("supply_note") or "").split())
    if supply:
        window.extra["supply_note"] = supply
    return window


def assemble(
    criteria: dict,
    taxonomy: Taxonomy,
    lens: str,
    start: str | date,
    candidates: list[Candidate],
    context: Context,
    standing: CalendarWindow | None = None,
    caller=call_model,
    as_of: str | date | None = None,
) -> CalendarWindow:
    """Builds the request, makes the call, and returns a validated window.

    `caller` is injectable so the tests exercise every step around the model
    without spending a request or needing a key. `as_of` is passed through to
    the standing-calendar section and means nothing without one.
    """
    if not candidates:
        raise AssemblyError(
            "no schedulable content was found, so there is nothing to sequence; "
            "check the sibling folders are in the sparse checkout"
        )

    first, last = slots_mod.window_bounds(taxonomy, start)
    alternatives_cap = int(criteria.get("alternatives_per_slot") or 0)
    limits = resolve_limits(criteria)
    schema = build_output_schema(candidates, taxonomy, alternatives_cap, limits)
    system = build_system_prompt(criteria)
    user_content = build_user_content(
        candidates=candidates,
        context=context,
        window_start=first,
        window_end=last or first,
        lens_label=taxonomy.lens_label(lens),
        standing=standing,
        as_of=as_of,
    )

    # This function's contract is "a window, or an AssemblyError". `caller` is
    # injectable, so enforcing that here rather than only inside `call_model`
    # keeps the contract true for any caller a later build swaps in.
    try:
        payload = caller(criteria, system, user_content, schema)
    except AssemblyError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AssemblyError(f"the model call failed: {type(exc).__name__}: {exc}") from exc

    return window_from_payload(
        payload, taxonomy, lens, start, candidates,
        max_alternatives=alternatives_cap, limits=limits,
    )


def _as_iso(value: str | date) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


# Moved to `calendar_model/slots.py` on 2026-09-15, where the approval state
# machine already lives, so that the display can ask the same question without
# importing this module and the Anthropic SDK with it. Kept as a name here
# because it is part of this module's interface: what comes back is exactly
# the approval queue's worklist (item 13), and `refresh.py` and the tests call
# it by this name.


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def placed_elsewhere(
    criteria: dict,
    lens: str,
    state_root: str | Path,
    today: str | date,
    *,
    skip: Iterable = (),
) -> dict:
    """item_ref -> window_id for every post another window of `lens` already holds.

    Which placements count is `already_placed` in sequencing_criteria.yaml:
    any listed state on a window still running, and only the listed states on
    a window that has passed. Approval state is the window overlaid with the
    store, exactly as the page and the queue see it, since an approval lives
    in the store and not in the file. `skip` names the window being built or
    refreshed, which is not "elsewhere". Empty when the policy is off.
    """
    policy = criteria.get("already_placed") or {}
    if not policy.get("enabled"):
        return {}
    running_states = set(policy.get("running_window_states") or [])
    past_states = set(policy.get("past_window_states") or [])
    root = Path(state_root)
    skipped = {Path(p).resolve() for p in skip}
    live = {p.resolve() for p in slots_mod.live_window_paths(root, lens=lens, today=today)}
    decisions = state_store.states_of(state_store.latest_decisions(root=root))

    placed: dict = {}
    for path in slots_mod._standing_window_paths(root, lens, "placed_elsewhere"):
        if path.resolve() in skipped:
            continue
        window = slots_mod.read_window(path)
        if window is None:
            continue
        window = slots_mod.apply_approvals(window, decisions)
        counting = running_states if path.resolve() in live else past_states
        for slot in window.slots:
            if slot.approval in counting:
                placed.setdefault(slot.item_ref, window.window_id)
    return placed


def empty_window(
    criteria: dict, taxonomy: Taxonomy, lens: str, start: str | date, placed: dict
) -> CalendarWindow:
    """The honest answer when every schedulable post is already on another calendar.

    No model call: with nothing to sequence there is no judgment to pay for.
    The words are policy, in `already_placed`, so that the window reads as a
    finding rather than a fault. `extra` records that no model was asked and
    what each post is already placed on, so the page and a reader of the file
    can tell this from a model that returned nothing.
    """
    policy = criteria.get("already_placed") or {}
    windows = ", ".join(sorted(set(placed.values())))
    fill = {"count": len(placed), "windows": windows}
    window = slots_mod.new_window(
        window_id=f"{lens}-{_as_iso(start)}",
        lens=lens,
        start=start,
        taxonomy=taxonomy,
        arc=" ".join(str(policy.get("empty_window_arc") or "").format(**fill).split()),
    )
    note = " ".join(str(policy.get("empty_window_supply_note") or "").format(**fill).split())
    if note:
        window.extra["supply_note"] = note
    window.extra["assembled_without_model"] = True
    window.extra["already_placed"] = dict(sorted(placed.items()))
    return window


def write_new_window(path: str | Path, window: CalendarWindow) -> Path:
    """Write `window` to `path` only if nothing is there. Raises FileExistsError.

    Exclusive create rather than exists-then-write, so the refusal holds even
    against a second run writing the same month between the check and the
    write. `slots.write_window` overwrites, and stays that way because the
    refresh and the rejected copy both rely on it; this is the one writer for
    a standing window.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(slots_mod.dumps(window))
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Assemble a forward calendar by sequencing the existing corpus."
    )
    parser.add_argument("--criteria", default=str(DEFAULT_CRITERIA_PATH), help="sequencing policy")
    parser.add_argument("--threads", default=str(slots_mod.DEFAULT_TAXONOMY_PATH), help="threads.yaml")
    parser.add_argument("--tags", default=str(DEFAULT_TAGS_PATH), help="corpus_tags.yaml")
    parser.add_argument("--atomizer", default=str(DEFAULT_ATOMIZER_PATH), help="Content Atomizer output/")
    parser.add_argument("--vcb", default=str(DEFAULT_VCB_PATH), help="Value Creation Briefing drafts/")
    parser.add_argument("--lens", default=None,
                        help="which calendar to build (default: the taxonomy's v1 lens)")
    parser.add_argument("--start", default=None, help="window start (YYYY-MM-DD), default today UTC")
    parser.add_argument("--out", default=None, help="write the window here")
    parser.add_argument("--against", default=None, help="standing window to refresh rather than replace")
    parser.add_argument("--state-root", default=str(state_store.DEFAULT_STATE_ROOT),
                        help="where the other windows and the approvals live")
    parser.add_argument("--today", default=None,
                        help="reference date for which windows are still running (default: today UTC)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="gather and report what would be sent, make no API call, write nothing",
    )
    args = parser.parse_args(argv)

    # Never over an existing window. A standing window may already carry
    # approvals, those are keyed by the positional slot_id, and the store has
    # no update call, so a fresh assembly written over it would slide every
    # approval onto whatever post now holds that position. Checked here,
    # before any money is spent, and again at write time by an exclusive
    # create, so two runs racing for the same month cannot both win. Replacing
    # a window is a deliberate act: delete it first, and git keeps the old one.
    if args.out and Path(args.out).exists() and not args.dry_run:
        print(f"Cannot assemble: {args.out} already exists, and a window is never "
              "overwritten. Delete it deliberately first if it really is to be replaced.",
              file=sys.stderr)
        return 1

    criteria = load_criteria(args.criteria)
    problems = validate_criteria(criteria)
    if problems:
        for problem in problems:
            _warn(problem)
        print("Cannot assemble: the sequencing policy is not usable.", file=sys.stderr)
        return 1

    taxonomy = slots_mod.load_taxonomy(args.threads)
    lens = resolve_lens(criteria, taxonomy, args.lens)
    if not lens:
        print("Cannot assemble: no calendar lens to build.", file=sys.stderr)
        return 1
    criteria = criteria_for_lens(criteria, lens)

    start = slots_mod._as_date(args.start) or datetime.now(timezone.utc).date()
    first, last = slots_mod.window_bounds(taxonomy, start)

    excluded: list[str] = []
    candidates = gather_candidates(
        criteria, lens, args.atomizer, args.vcb, args.tags, excluded=excluded
    )
    # A post another calendar already holds is not a candidate, so November
    # cannot re-schedule October. If that leaves nothing while the corpus
    # itself is not empty, the window is written empty below with no model
    # call: there is nothing to sequence, and that is the corpus talking.
    today = slots_mod._as_date(args.today) or datetime.now(timezone.utc).date()
    placed = placed_elsewhere(criteria, lens, args.state_root, today,
                              skip=[p for p in (args.out, args.against) if p])
    placed = {c.item_ref: placed[c.item_ref] for c in candidates if c.item_ref in placed}
    for ref, window_id in placed.items():
        excluded.append(f"{ref}: already placed on {window_id}")
    candidates = [c for c in candidates if c.item_ref not in placed]
    all_placed = bool(placed) and not candidates
    context = gather_context(criteria)
    context.feedback = gather_feedback(criteria, lens, args.state_root)

    standing = None
    if args.against:
        standing = slots_mod.read_window(args.against)
        if standing is None:
            _warn(f"could not read the standing window at {args.against}; assembling fresh")

    cadence = criteria.get("cadence") or {}
    span_weeks = max(1, ((slots_mod._as_date(last) - slots_mod._as_date(first)).days + 1) / 7)
    wanted_low = int(cadence.get("posts_per_week_min", 1) * span_weeks)
    wanted_high = int(cadence.get("posts_per_week_max", 1) * span_weeks)

    print(f"Policy: {args.criteria}")
    print(f"  model {criteria['model']['id']}, effort {criteria['model'].get('effort', 'high')}")
    print(f"  {len(criteria.get('criteria') or [])} criteria, cadence "
          f"{cadence.get('posts_per_week_min')}-{cadence.get('posts_per_week_max')} per week, "
          f"{criteria.get('alternatives_per_slot')} alternatives per slot")
    print(f"Window: {lens} lens, {first} to {last}, wants {wanted_low} to {wanted_high} slots")
    print(f"Candidates: {len(candidates)} schedulable")
    for candidate in candidates:
        print(f"  {candidate.item_ref}  ({candidate.word_count} words)")
    if excluded:
        print(f"Excluded: {len(excluded)}")
        for reason in excluded:
            print(f"  {reason}")
    print(f"Context: {len(context.market_items)} market items, "
          f"{len(context.documents)} narrative documents, "
          f"{len(context.published)} published-history documents, "
          f"{len(context.feedback)} decline reason(s)")
    for note in context.notes:
        print(f"  note: {note}")
    if standing is not None:
        approved = sum(1 for s in standing.slots if s.approval in ("approved", "published"))
        print(f"Refreshing against {args.against}: {len(standing.slots)} slots, {approved} fixed")

    if len(candidates) < wanted_low:
        print(
            f"\nSupply warning: {len(candidates)} schedulable items against a window that wants "
            f"at least {wanted_low}. Expect a short calendar; that is the corpus, not a bug."
        )

    if args.dry_run and all_placed:
        print("\n--- dry run, nothing sent ---")
        print(f"Every schedulable post ({len(placed)}) is already placed on another calendar, "
              "so the window would be written empty with no model call.")
        return 0

    if args.dry_run:
        system = build_system_prompt(criteria)
        schema = build_output_schema(
            candidates, taxonomy, int(criteria.get("alternatives_per_slot") or 0),
            resolve_limits(criteria),
        )
        user_content = build_user_content(
            candidates, context, first, last or first, taxonomy.lens_label(lens), standing
        )
        print("\n--- dry run, nothing sent ---")
        print(f"system prompt: {len(system)} chars, {len(system.splitlines())} lines")
        print(f"user content: {len(user_content)} chars")
        print(f"schema bounds item_ref to {len(candidates)} refs, "
              f"threads to {len(taxonomy.thread_ids)}, forms to {len(taxonomy.form_ids)}")
        limits = resolve_limits(criteria)
        print("length limits: " + ", ".join(
            f"{key.replace('_', ' ')} {value}" if value else f"{key.replace('_', ' ')} unbounded"
            for key, value in limits.items()
        ))
        return 0

    try:
        window = (
            empty_window(criteria, taxonomy, lens, start, placed) if all_placed
            else assemble(criteria, taxonomy, lens, start, candidates, context, standing)
        )
    except AssemblyError as exc:
        print(f"\nAssembly failed: {exc}", file=sys.stderr)
        return 1

    issues = slots_mod.validate_window(window, taxonomy, item_refs={c.item_ref for c in candidates})
    errors = slots_mod.errors(issues)
    warnings = slots_mod.warnings(issues)

    print(f"\nAssembled {window.window_id}: {len(window.slots)} slots")
    for slot in sorted(window.slots, key=lambda s: (s.date, s.slot_id)):
        alts = slot.extra.get("alternatives") or []
        print(f"  {slot.date}  {slot.item_ref}"
              f"{f'  (+{len(alts)} alternatives)' if alts else ''}")
        if slot.rationale:
            print(f"      {slot.rationale}")
    if window.arc:
        print(f"\nArc: {window.arc}")
    if window.extra.get("supply_note"):
        print(f"\nSupply: {window.extra['supply_note']}")

    print(f"\nEvery slot is a draft. Nothing publishes without a human approving it, "
          f"and nobody is late: {len(window.slots)} post(s) are there to sign off whenever "
          "Jordan, Casey or Blake want to.")

    for issue in warnings:
        print(f"  warning: {issue}", file=sys.stderr)
    if errors:
        for issue in errors:
            print(f"  error: {issue}", file=sys.stderr)
        # A rejected window still gets saved, beside the real output rather
        # than at it. This is the only module that spends money to produce its
        # answer, and the first live run threw a good five-slot month away
        # over one malformed reference, leaving nothing to diagnose from and
        # no way to see the error except by paying again. Writing it here
        # keeps the guard intact (nothing publishes from a `.rejected.` file,
        # and no caller reads one) while making the failure inspectable.
        if args.out:
            rejected = Path(args.out).with_suffix(".rejected.json")
            print(f"\n{len(errors)} validation error(s); not written to {args.out}.",
                  file=sys.stderr)
            print(f"The assembled window was kept at {slots_mod.write_window(rejected, window)} "
                  "so the answer is not lost. If the errors are mechanical, "
                  "synthesis/repair_rejected.py fixes them with no second call and records "
                  "what it changed; otherwise delete it and re-run.", file=sys.stderr)
        else:
            print(f"\n{len(errors)} validation error(s); the window was not written, and "
                  "without --out there is nowhere to keep it. Pass --out to preserve a "
                  "rejected copy next time.", file=sys.stderr)
        return 1

    if args.out:
        try:
            written = write_new_window(args.out, window)
        except FileExistsError:
            print(f"\n{args.out} appeared while the model was answering; not overwritten. "
                  "Another run assembled this month first.", file=sys.stderr)
            return 1
        print(f"\nWrote {written}")
    else:
        print("\nNot written. Pass --out to keep it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
