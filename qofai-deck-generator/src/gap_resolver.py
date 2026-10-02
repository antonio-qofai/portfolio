"""Gap reader — read the low-confidence flags a data packet declares.

A data packet's provenance/gaps block (status §5, proposal §8) lists the fields
that are present but unconfirmed. Module 3 renders each with a
``(unconfirmed, see gaps)`` marker so the render-fidelity guard can prove it
survived; the marker is then stripped before the deck is written, so the artifact
renders clean and the flags are purely an internal review checklist.

This module is the read side of that loop, and only the read side:

- ``list_gaps`` reads the declared gaps (field + reason), reusing the pipeline's own
  packet reader so it sees exactly the gaps the assembler flags.
- ``describe_gap`` turns a packet field path into the slide number and a
  plain-language location a reviewer can navigate to.
- ``deck_shape`` reports the workstream count that places the closing slide.

**Nothing here writes to a packet, and nothing else in this repo does either.** The
packet is the data source's record of what it sent, and the agent's whole guarantee
is to reproduce it faithfully, so it is immutable to us (Antonio, 2026-07-28). An
earlier ``resolve_gap`` deleted the confirmed gap entry from the packet on disk;
that is removed. It edited a committed fixture whenever a reviewer confirmed a
value locally, and on the hosted service — where packets live in the deployed repo
image — the edit was silently discarded on the next deploy. Reviewer decisions are
reviewer state and live in ``gap_decisions``, keyed by packet and field, leaving the
packet byte-for-byte untouched. ``tests/test_packet_is_never_written.py`` enforces
this.

All functions are pure: they take packet text and return values, and none touches
the filesystem.
"""

import re

from data_source_adapter import _section_yaml, _split_sections

# The indentation-aware `gaps:` / sequence-item / `field:` matchers that used to
# live here existed only to CUT an entry out of a packet's gaps block. They are
# gone with the write path: keeping them would leave the machinery for editing a
# packet sitting in the module that is supposed to guarantee it never happens.


def list_gaps(packet_text):
    """Return the packet's declared gaps as ``[{"field", "reason"}, ...]``.

    Reads the provenance/gaps block via the pipeline's own section reader, so the
    list matches exactly the fields the assembler stamps with the
    ``(unconfirmed, see gaps)`` marker. Returns ``[]`` when the packet declares no
    gaps (or cannot be parsed). Deck-type-agnostic: finds the provenance block
    wherever it lives (status §5, proposal §8).
    """
    try:
        sections = _split_sections(packet_text)
    except Exception:
        return []
    for _number, section_text in sections.items():
        block = _section_yaml(section_text)
        provenance = block.get("provenance") if isinstance(block, dict) else None
        if not isinstance(provenance, dict):
            continue
        gaps = provenance.get("gaps") or []
        out = []
        for gap in gaps:
            if isinstance(gap, dict) and gap.get("field"):
                out.append({"field": gap["field"], "reason": gap.get("reason", "")})
        if out:
            return out
    return []


# --- human-readable gap location -------------------------------------------
# A gap's `field` is a packet path (e.g. `workstreams[2].after.metrics[0].value`).
# That path is engineering jargon to a reviewer. describe_gap turns it into the
# slide it lands on plus a plain-language breadcrumb of where on that slide, so the
# checklist reads "Slide 5: Workstream 3 · target/after panel, 1st metric value"
# instead of a dotted path. Unknown keys degrade to a de-underscored label, so a
# new packet shape still reads sensibly rather than breaking.
#
# The slide NUMBER matters as much as the name (Antonio, 2026-07-24): a reviewer
# holding the checklist next to the deck navigates by the number printed in the
# footer, so "Workstream 3" alone means counting slides to find it.

# First path segment -> the slide it renders on (covers both deck types; the packet
# section headers document these mappings).
_SLIDE_BY_KEY = {
    # status packet
    "cover": "Cover",
    "engagement": "Recurring header/footer (every slide)",
    "tracking": "Project Tracking",
    "workstreams": "Workstream",  # a number is appended from the index
    "next_steps": "Next Steps",
    # proposal packet
    "company": "Cover / The Opportunity",
    "baseline": "Cover / The Opportunity",
    "opportunity": "The Opportunity",
    "platform": "The Platform",
    "plan": "Phased Rollout",
    "timeline": "Phased Rollout",
    "milestones": "Phased Rollout",
    "commercial": "Commercial Terms",
    "value_mapping": "Commercial Terms",
}

# Path segment -> friendly label for the on-slide breadcrumb.
_TOKEN_LABEL = {
    "after": "target / after panel",
    "target": "target panel",
    "today": "today / current-state panel",
    "where_we_are": "where-we-are panel",
    "metrics": "metric",
    "value": "value",
    "label": "label",
    "horizon": "horizon",
    "pain_bullets": "pain bullet",
    "capability_bullets": "capability bullet",
    "notes": "note",
    "progress_tracker": "progress tracker",
    "groups": "progress group",
    "items": "checklist item",
    # Not printed on the slide any more (the callout strip under the Gantt was
    # removed, Antonio 2026-07-24); it now drives a bar's dashed buffer/slip
    # annotation, so that is where a reviewer looks for it.
    "slip_or_buffer_markers": "buffer / slip annotation on the timeline",
    "today_marker": "TODAY marker",
    "columns": "week column",
    "bar_categories": "bar category",
    "lanes": "lane",
    "bars": "bar",
    "qofai_investment_usd": "QofAI investment",
    "client_upfront_usd": "client upfront",
    "baseline_locked_date": "baseline lock date",
}

_PATH_TOKEN_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d+)\])?")

# First path segment -> the 1-based slide number it renders on. Derived from the
# per-deck slide orders the render scaffolds emit, not hardcoded per client:
#   proposal (fixed 6):  1 cover, 2 opportunity, 3 platform, 4 rollout,
#                        5 commercial, 6 closing.
#   status  (variable):  1 cover, 2 tracking, 3..2+N one per workstream,
#                        3+N next steps.
# A field that renders on more than one slide (a recurring header/footer field, or
# a company reference used on both the cover and slide 2) has no single number and
# is deliberately absent — the checklist then shows the slide name alone rather
# than a number that would send the reviewer to the wrong slide.
_SLIDE_NUMBER_BY_KEY = {
    "proposal": {
        "opportunity": 2,
        "platform": 3,
        "plan": 4,
        "timeline": 4,
        "milestones": 4,
        "commercial": 5,
        "value_mapping": 5,
    },
    "status": {
        "cover": 1,
        "tracking": 2,
        # `workstreams[i]` -> 3 + i, and `next_steps` -> 3 + n_workstreams, both
        # computed in _slide_number (they need the index / the workstream count).
    },
}


def _slide_number(deck_type, head_name, head_idx, n_workstreams):
    """The 1-based slide number for a gap's path head, or ``None`` when the field
    does not land on exactly one numbered slide.

    ``n_workstreams`` is only needed to place the status deck's closing Next Steps
    slide, whose position depends on how many workstream slides precede it. Absent
    (the caller did not read the packet's deck block), that one slide degrades to
    no number rather than to a guess.
    """
    if deck_type == "status":
        if head_name == "workstreams" and head_idx is not None:
            return 3 + head_idx
        if head_name == "next_steps":
            return 3 + n_workstreams if n_workstreams else None
    return _SLIDE_NUMBER_BY_KEY.get(deck_type, {}).get(head_name)


def deck_shape(packet_text):
    """Read the packet's ``deck`` block for the counts the slide numbering needs.

    Returns ``{"n_workstreams": int|None, "total_slides": int|None}``. Both are
    ``None`` for an unreadable packet or a deck type that declares neither, so a
    caller can pass the result through without branching.
    """
    shape = {"n_workstreams": None, "total_slides": None}
    try:
        sections = _split_sections(packet_text)
    except Exception:
        return shape
    for section_text in sections.values():
        block = _section_yaml(section_text)
        deck = block.get("deck") if isinstance(block, dict) else None
        if not isinstance(deck, dict):
            continue
        for key in shape:
            value = deck.get(key)
            if isinstance(value, int):
                shape[key] = value
        if any(value is not None for value in shape.values()):
            break
    return shape


def _ordinal(index_zero_based):
    n = index_zero_based + 1
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _humanize_token(name, index):
    label = _TOKEN_LABEL.get(name, name.replace("_", " "))
    return f"{_ordinal(index)} {label}" if index is not None else label


def describe_gap(field, deck_type="", n_workstreams=None):
    """Turn a packet field path into a display record for the review checklist.

    Returns ``{"slide", "slide_number", "slide_label", "where", "field"}``:

    - ``slide`` — the name of the slide the flagged value lands on.
    - ``slide_number`` — that slide's 1-based number as printed in the deck
      footer, or ``None`` for a field that renders on more than one slide.
    - ``slide_label`` — the two joined for display: ``"Slide 5: Workstream 3"``,
      falling back to the bare name when there is no single number.
    - ``where`` — a plain-language breadcrumb of where on that slide.
    - ``field`` — the raw path, kept for the reviewer who wants the exact source.

    ``n_workstreams`` places the status deck's closing Next Steps slide, whose
    number depends on how many workstream slides precede it (read from the packet
    via :func:`deck_shape`). Everything degrades gracefully: an unrecognized path
    keeps its de-underscored name and simply carries no number.
    """
    tokens = [
        (m.group(1), int(m.group(2)) if m.group(2) is not None else None)
        for m in _PATH_TOKEN_RE.finditer(field or "")
    ]
    if not tokens:
        return {"slide": "", "slide_number": None, "slide_label": "",
                "where": field or "", "field": field or ""}
    head_name, head_idx = tokens[0]
    slide = _SLIDE_BY_KEY.get(head_name, head_name.replace("_", " ").title())
    if head_name == "workstreams" and head_idx is not None:
        slide = f"Workstream {head_idx + 1}"
    number = _slide_number(deck_type, head_name, head_idx, n_workstreams)
    rest = tokens[1:]
    where = (", ".join(_humanize_token(n, i) for n, i in rest)
             if rest else _humanize_token(head_name, head_idx))
    return {
        "slide": slide,
        "slide_number": number,
        "slide_label": f"Slide {number}: {slide}" if number else slide,
        "where": where,
        "field": field,
    }
