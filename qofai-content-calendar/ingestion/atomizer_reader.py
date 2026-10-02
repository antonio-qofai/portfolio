"""Reads Content Atomizer (Alex) output: persona-tagged LinkedIn post drafts.

Real output format confirmed 2026-07-23 by reading the actual files in
../content-atomizer/output/, since Alex's own PRD.md describes an earlier
planned structure (output/posts/<id>/post.md, with separate insights.md and
outline.md files per post, in its own subfolder) that differs from what is
actually on disk. Building against a made-up example would have missed this;
this reader is written and tested against the real files directly.

Actual structure, flat files, one per post:

    output/posts/<anchor-id>.post.md

Each file starts with a "# Post: <id>" header, a "**Persona:** <Name>" line,
a "**Source outline:** <path>" line, then "---", the post body, "---", an
image prompt, "---", and a References section. This reader pulls what C2
needs for calendar assembly: post_id, persona, and body text. It does not
touch the outlines/ or insight-pool.md files in this pass.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import read_status

_PERSONA_RE = re.compile(r"^\*\*Persona:\*\*\s*(.+)$", re.MULTILINE)
_TITLE_RE = re.compile(r"^# Post:\s*(.+)$", re.MULTILINE)


@dataclass
class AtomizerPost:
    post_id: str
    persona: str | None
    body: str
    raw_text: str
    source_path: str


def _extract_body(raw_text: str) -> str:
    """Body is the text between the first and second '---' separators.

    Falls back to the full text if the expected separators aren't found,
    rather than raising, since C2 should degrade gracefully on format drift
    in a sibling agent's output rather than crash the whole ingestion run.
    """
    parts = raw_text.split("---")
    if len(parts) >= 3:
        return parts[1].strip()
    return raw_text.strip()


def read_atomizer_posts(atomizer_output_path: str, status=None) -> list[AtomizerPost]:
    """Reads every <anchor-id>.post.md directly under
    <atomizer_output_path>/posts/.

    Returns an empty list, not an error, if the folder doesn't exist yet or
    has nothing in it, since Content Atomizer may not have produced
    anything for the current cycle when this runs.

    `status` is an optional `read_status.ReadStatus` the caller can pass to
    learn which kind of nothing it got. The two cases are worth telling apart
    on a CI runner: a folder with no posts in it means Alex has not written
    any, and a folder that is not there at all usually means a sparse checkout
    dropped the sibling repo, which looks identical from the item count and
    is somebody's bug rather than a quiet week.
    """
    posts_dir = Path(atomizer_output_path) / "posts"
    if not posts_dir.is_dir():
        read_status.fill(
            status, read_status.MISSING,
            f"{posts_dir} does not exist; the content-atomizer folder is probably "
            "not in this checkout",
        )
        return []

    results: list[AtomizerPost] = []
    for post_file in sorted(posts_dir.glob("*.post.md")):
        raw_text = post_file.read_text(encoding="utf-8")

        title_match = _TITLE_RE.search(raw_text)
        post_id = (
            title_match.group(1).strip()
            if title_match
            else post_file.name.removesuffix(".post.md")
        )

        persona_match = _PERSONA_RE.search(raw_text)
        persona = persona_match.group(1).strip() if persona_match else None

        results.append(
            AtomizerPost(
                post_id=post_id,
                persona=persona,
                body=_extract_body(raw_text),
                raw_text=raw_text,
                source_path=str(post_file),
            )
        )
    if not results:
        read_status.fill(
            status, read_status.EMPTY, f"{posts_dir} holds no *.post.md files"
        )
    return results


if __name__ == "__main__":
    import sys

    # Default path assumes this script is run from its own folder inside
    # repos/thought-leadership-calendar-manager/ingestion/, matching the
    # real monorepo layout: repos/content-atomizer is a sibling folder.
    path = sys.argv[1] if len(sys.argv) > 1 else "../../content-atomizer/output"
    posts = read_atomizer_posts(path)
    print(f"Found {len(posts)} post(s) under {path}")
    for p in posts:
        print(f"- {p.post_id} (persona={p.persona}, {len(p.body)} chars)")
