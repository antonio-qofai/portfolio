"""Reads Value Creation Briefing (Robin) output: the monthly briefing draft
and the persona-tagged blog drafts it generates.

Robin's PRD.md (read 2026-07-22) describes the Airtable base
(appXXXXXXXXXXXXXX, table "Briefing Insights") as the source of record for
raw sourced insights, and a monthly briefing_draft_YYYY-MM.md as the
assembled output. Checking the real repo on 2026-07-23 found both that file
and something more directly useful for C2: persona-tagged blog drafts
already sitting in the same drafts/ folder,
drafts/blog_<persona>_<YYYY-MM>.md, one per persona per month. Those are
closer to what C2 actually needs (ready, persona-specific posts) than the
raw insight rows, so this reader reads local files rather than hitting
Robin's Airtable base directly. If that turns out to be wrong once the
central GTM database exists, only this module needs to change (see PRD.md
Section 5).

One data-quality note found while building this: Content Atomizer (Alex)
labels this persona "Casey" inside its post files, while these filenames
use "casy". Both are normalized to "Casey" here so downstream calendar
logic doesn't see them as two different people. Worth raising as a naming
inconsistency across agents rather than quietly papering over it long-term.
This said "flag to Pat/Jordan" until 2026-09-21; Pat has left QofAI, so it
goes to Alex, who owns the atomizer and writes one of the two spellings, with
Robin as the other owner. Fixing the spelling is theirs either way: this
reader normalises for display and does not rewrite anybody's content.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import read_status

_PERSONA_NORMALIZE = {
    "jordan": "Jordan",
    "casy": "Casey",
    "casey": "Casey",
    "blake": "Blake",
}

_BLOG_FILENAME_RE = re.compile(r"^blog_([a-zA-Z]+)_(\d{4}-\d{2})\.md$")
_BRIEFING_FILENAME_RE = re.compile(r"^briefing_draft_(\d{4}-\d{2})\.md$")


@dataclass
class PersonaBlogDraft:
    persona: str
    month: str
    title: str
    text: str
    source_path: str


@dataclass
class BriefingDraft:
    month: str
    text: str
    source_path: str


def _first_heading(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""


def read_persona_blog_drafts(vcb_drafts_path: str, status=None) -> list[PersonaBlogDraft]:
    """Reads every drafts/blog_<persona>_<YYYY-MM>.md file.

    Returns an empty list, not an error, if the folder doesn't exist yet or
    has no blog drafts for the current cycle. `status` is an optional
    `read_status.ReadStatus` recording which of those two it was.
    """
    drafts_dir = Path(vcb_drafts_path)
    if not drafts_dir.is_dir():
        read_status.fill(
            status, read_status.MISSING,
            f"{drafts_dir} does not exist; the value-creation-briefing folder is "
            "probably not in this checkout",
        )
        return []

    results: list[PersonaBlogDraft] = []
    for file in sorted(drafts_dir.glob("blog_*.md")):
        match = _BLOG_FILENAME_RE.match(file.name)
        if not match:
            continue
        raw_persona, month = match.groups()
        persona = _PERSONA_NORMALIZE.get(raw_persona.lower(), raw_persona)
        text = file.read_text(encoding="utf-8")
        results.append(
            PersonaBlogDraft(
                persona=persona,
                month=month,
                title=_first_heading(text),
                text=text,
                source_path=str(file),
            )
        )
    if not results:
        read_status.fill(
            status, read_status.EMPTY, f"{drafts_dir} holds no blog_*.md"
        )
    return results


def read_briefing_drafts(vcb_drafts_path: str, status=None) -> list[BriefingDraft]:
    """Reads every drafts/briefing_draft_<YYYY-MM>.md file (the monthly
    seminal piece, not persona-tagged). Returns an empty list, not an
    error, if none exist yet. `status` is an optional
    `read_status.ReadStatus` recording whether the folder was missing or the
    month's briefing simply has not landed.
    """
    drafts_dir = Path(vcb_drafts_path)
    if not drafts_dir.is_dir():
        read_status.fill(
            status, read_status.MISSING,
            f"{drafts_dir} does not exist; the value-creation-briefing folder is "
            "probably not in this checkout",
        )
        return []

    results: list[BriefingDraft] = []
    for file in sorted(drafts_dir.glob("briefing_draft_*.md")):
        match = _BRIEFING_FILENAME_RE.match(file.name)
        if not match:
            continue
        (month,) = match.groups()
        results.append(
            BriefingDraft(
                month=month,
                text=file.read_text(encoding="utf-8"),
                source_path=str(file),
            )
        )
    if not results:
        read_status.fill(
            status, read_status.EMPTY, f"{drafts_dir} holds no briefing_draft_*.md"
        )
    return results


if __name__ == "__main__":
    import sys

    # Default assumes this script runs from its own folder inside
    # repos/thought-leadership-calendar-manager/ingestion/, matching the
    # real monorepo layout: repos/value-creation-briefing is a sibling.
    path = sys.argv[1] if len(sys.argv) > 1 else "../../value-creation-briefing/drafts"

    blogs = read_persona_blog_drafts(path)
    print(f"Found {len(blogs)} persona blog draft(s) under {path}")
    for b in blogs:
        print(f"- {b.month} / {b.persona}: {b.title!r} ({len(b.text)} chars)")

    briefings = read_briefing_drafts(path)
    print(f"Found {len(briefings)} monthly briefing draft(s) under {path}")
    for b in briefings:
        print(f"- {b.month} ({len(b.text)} chars)")
