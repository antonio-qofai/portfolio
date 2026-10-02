"""Which empty Next Steps fields the document states and the reader missed.

Part A4 of `build-plan-phase4-flags-and-fit.md`. Antonio, 2026-09-23: "I don't
want things like this to happen with other decks and not be resolved. How are
we dealing with the problem of unnecessary missing flags in the long run?"

A `[MISSING: week]` or `[MISSING: owner]` on a Next Steps slide is one of two
things. Either the document does not state it, and the reviewer clears it, or
the document states it in a shape `prd_section_parsers.next_steps` does not
read, and that is OUR gap. On output-18 all five of the onboarding PRD's real
flags were the second kind: the days were in §14's schedule table, which the
reader never joined back to the steps it schedules.

This tells the two apart, deterministically and WITHOUT the parser's own
structure. It scans §14 line by line for lines that name the step (a whole
part of its role, or a collapsed step's heading noun) and asks whether one of
them carries the missing fact: a day or week token for a week, an `Owner:`
label or a table row's owner cell for an owner. It deliberately does not reuse
the reader's matching: an audit built on the reader would agree with it by
construction, and the corpus check that runs this over every PRD could never
fail.

It can only ever say "this looks stated". It never fills a field and nothing
it returns reaches a slide.
"""

import re

import prd_section_parsers

# A day or a week with a number after it: "Day 1", "Days 2–3", "Week 4", "Wks 1".
DAY_TOKEN = re.compile(r"\b(?:Days?|Weeks?|Wks?)\s*\d", re.IGNORECASE)

# "Owner: COO / Salesbox admin." — the label a data-request item states.
OWNER_LABEL = re.compile(r"\bOwner\s*:", re.IGNORECASE)

STATED = "stated but not read"
NOT_STATED = "not stated"

FIELDS = ("week", "owner")


def _owner_cell(line):
    """A table row whose third cell names someone, e.g. `| Day 1 | ... | COO |`."""
    stripped = line.strip()
    if not stripped.startswith("|"):
        return False
    cells = [cell.strip() for cell in stripped.strip("|").split("|")]
    return len(cells) >= 3 and bool(cells[2]) and set(cells[2]) - set("-: ")


def audit_next_steps(text, records):
    """One finding per empty week or owner on `records`, in record order.

    `text` is the base document the records were read from, `records` the Next
    Steps records as the deck carries them. Returns
    `[{"number", "title", "field", "status"}]`, where `status` is `STATED` or
    `NOT_STATED`. A record with nothing empty contributes nothing, so a
    document whose steps are complete returns `[]`.
    """
    body = prd_section_parsers.section(text or "", 14) or ""
    lines = [line for line in body.splitlines() if line.strip()]
    findings = []
    for record in records or ():
        empty = [field for field in FIELDS if not (record.get(field) or "").strip()]
        if not empty:
            continue
        parts = prd_section_parsers.role_parts(
            prd_section_parsers.step_subject(record))
        named = [line for line in lines
                 if any(prd_section_parsers.names(line, part) for part in parts)]
        for field in empty:
            if field == "week":
                stated = any(DAY_TOKEN.search(line) for line in named)
            else:
                stated = any(OWNER_LABEL.search(line) or _owner_cell(line)
                             for line in named)
            findings.append({
                "number": record.get("number") or "",
                "title": record.get("title") or "",
                "field": field,
                "status": STATED if stated else NOT_STATED,
            })
    return findings


def stated_but_not_read(text, records):
    """Only the findings that are our gap, not the document's."""
    return [finding for finding in audit_next_steps(text, records)
            if finding["status"] == STATED]
