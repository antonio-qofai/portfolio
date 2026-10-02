"""A hand-constructed 30-day window, used to demonstrate and test the schema.

This is an example, not an output. Deciding which item goes on which date is
build order item 9 and the hard part of the agent; nothing here decides
anything. The window exists so the schema has something real to be checked
against before the synthesis that will produce real ones is written.

What is real about it, and what is not:

  - The item ids are real. Every one appears in narrative/corpus_tags.yaml
    and resolves to a file in Alex's or Robin's folder through the ingestion
    readers. Nothing is invented and no content is copied here.
  - The thread and form ids are real, taken from the same tags file, so the
    example validates against narrative/threads.yaml rather than against a
    convenient fiction.
  - The ordering dependency is real: anchor-01 opens by answering an
    already-published post, which threads.yaml records as a tension and
    which is why `responds_to` exists.
  - The dates are relative to a reference day, not written down, so the
    example lands in the window wherever it runs rather than aging out of
    it. Every slot comes back a draft, because nobody has decided on an
    example: approval is something a person records, never something the
    dates imply (owner decision 2026-09-19).
  - The arrangement itself is illustrative. It is spaced to show what the
    schema can express: gaps, two slots on one date, a sequel behind the
    post it answers, and a monthly anchor near the front. It is not a
    recommendation about what to publish.

One deliberate omission. `content-atomizer/anchor-08-the-naming-post` is not
scheduled, because it announces the discipline's name as "Bananas", which
reads as an unfilled placeholder. That is recorded in corpus_tags.yaml and
in threads.yaml as something to confirm with Alex before it is ever
scheduled, and an example that ignored it would be teaching the wrong habit.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from slots import (  # noqa: E402
    DRAFT,
    CalendarWindow,
    Slot,
    Taxonomy,
    new_window,
)

ARC = (
    "Open on the exit problem, since that is what a middle-market operating "
    "partner is actually stuck on, then argue that margin moves when the gap "
    "between a signal and the decision it informs closes, then show it "
    "happening in a named company with numbers, and close the month on what "
    "makes a forward number underwritable. The monthly briefing anchors the "
    "front of the window because Jordan called it the seminal piece and fixed "
    "it to the first week."
)

# (day offset from the reference date, item id, threads, forms, responds_to,
#  summary, rationale). Offsets rather than dates, so the example sits inside
#  its own window whatever day it is built on; a written-down date would be
#  in the past for most of the year.
#
# The summaries here are illustrative in exactly the way the rationales beside
# them are. This file is hand-built and says so wherever it is rendered, so it
# shows the shape of the two fields rather than reporting what a model said
# about these seven posts. A real window gets both from assembly.
_PLAN = [
    (
        2,
        "value-creation-briefing/briefing_draft_2026-08",
        ["governance-ownership", "named-workflow-targets"],
        ["briefing-launch"],
        [],
        "The month's Value Creation Briefing: where value creation work "
        "is going and what a portfolio company should own rather than "
        "buy.",
        "The monthly briefing is the tentpole and Jordan fixed it to the first "
        "week of the month, so it opens the window and everything else is "
        "arranged around it.",
    ),
    (
        4,
        "content-atomizer/anchor-03-the-electricity-post",
        ["decision-architecture", "pilot-to-production-gap"],
        ["historical-analogy"],
        [],
        "Argues from a historical analogy that the gains arrive only once "
        "the work is rearranged around the new capability, not when it is "
        "installed.",
        "Follows the briefing with the frame the rest of the month argues "
        "from, while the briefing is still the thing people are replying to.",
    ),
    (
        9,
        "value-creation-briefing/blog_jordan_2026-08",
        ["governance-ownership", "named-workflow-targets"],
        ["research-anchored-gap", "the-question-worth-asking"],
        [],
        "The Jordan-lens cut of the month's briefing, on the gap between "
        "what a diligence process measures and what the research "
        "supports.",
        "Robin's Jordan-lens draft for the month, held back a week so it is "
        "not competing with the briefing it was cut from.",
    ),
    (
        12,
        "content-atomizer/anchor-06-the-walker-receipts",
        ["resolution-gap", "exit-multiple-premium", "compounding-capability"],
        ["named-case-with-receipts"],
        [],
        "A named company with the numbers attached, offered as evidence "
        "rather than as a story.",
        "A named company with numbers, placed after the frame so the receipts "
        "land as evidence for an argument already made rather than as an "
        "anecdote.",
    ),
    (
        18,
        "content-atomizer/anchor-01-the-consultant-with-leverage",
        ["consulting-model-inversion", "pilot-to-production-gap", "compounding-capability"],
        [],
        ["sources/jordan-published-linkedin-posts.md#post-1"],
        "Answers the published pyramid post: what changes for advisory "
        "work when the leverage stops being headcount.",
        "Opens by answering the published pyramid post, so it can only run "
        "after that post, which it does. This is the ordering dependency the "
        "schema exists to record.",
    ),
    (
        25,
        "content-atomizer/anchor-07-the-return-regime",
        ["orchestration-layer-value", "exit-multiple-premium", "last-mile-data-moat"],
        ["historical-analogy"],
        [],
        "Where the return math goes when the capability compounds, and "
        "which part of the old underwriting stops holding.",
        "Closes the month on the return math, which is where the arc has been "
        "heading since the briefing.",
    ),
    (
        25,
        "value-creation-briefing/blog_jordan_2026-07",
        ["data-readiness", "labor-cost-model-gap", "emerging-diligence-items"],
        ["research-anchored-gap"],
        [],
        "The prior month's Jordan-lens draft on data readiness, and what "
        "it costs to discover the gap late.",
        "Shares a date with the closing post on purpose: cadence is unanswered "
        "(PRD Section 7), so the example shows two slots on one date rather "
        "than assuming a grid of one per day.",
    ),
]


def build(taxonomy: Taxonomy, today: date, lens: str | None = None) -> CalendarWindow:
    """Builds the example window for `today`.

    `lens` defaults to the single v1 lens declared in threads.yaml, so the
    example follows the taxonomy rather than naming a founder here. Every
    slot is a draft: an approval is a decision a person recorded, and an
    example window nobody has read cannot carry one.
    """
    if lens is None:
        lens = sorted(taxonomy.v1_lens_ids)[0] if taxonomy.v1_lens_ids else (
            sorted(taxonomy.calendar_lens_ids)[0] if taxonomy.calendar_lens_ids else "jordan"
        )

    window = new_window(
        window_id=f"{lens}-{today.isoformat()}",
        lens=lens,
        start=today,
        taxonomy=taxonomy,
        arc=ARC,
        generated_at=f"{today.isoformat()}T00:00:00+00:00",
    )

    for index, (
        offset, item_ref, threads, forms, responds_to, summary, rationale
    ) in enumerate(_PLAN, 1):
        day = today + timedelta(days=offset)
        window.slots.append(
            Slot(
                slot_id=f"{window.window_id}-{index:02d}",
                date=day.isoformat(),
                item_ref=item_ref,
                lens=lens,
                threads=list(threads),
                forms=list(forms),
                approval=DRAFT,
                responds_to=list(responds_to),
                summary=summary,
                rationale=rationale,
            )
        )
    # The example opens on `today`, which means it rarely starts on the
    # first of a month. An
    # assembled window runs to the end of its month (`slots.window_bounds`);
    # this one runs to its last planned post instead, so the demonstration
    # does not lose slots depending on which day it is built.
    last = max((s.date for s in window.slots), default=window.end_date)
    window.end_date = max(window.end_date, last)
    return window
