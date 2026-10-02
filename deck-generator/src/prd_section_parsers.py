"""A QofAI PRD read by its own sections, for the fields the paper's parsers miss.

WHY THIS EXISTS. Antonio ruled on 2026-09-17 that "everything in an uploaded PRD
should outrank the opportunity paper, especially economics", and
`base_document.precedence` has implemented that ordering ever since: an attached
document is the base and the paper is always last. The ordering was never the
problem. The problem is that a PRD had no answer to win WITH.

Measured 2026-09-20 on the Contoso Pipeline Cockpit PRD, run
through the shipped assembler: the PRD produced two scenario cases and ZERO
fields, while the paper produced the timeline. The parsers record their own
reason, and it is the whole diagnosis:

    timeline.phases  <-  "no <Chart> in this document is a timeline."

The paper carries its plan as a Chart.js `<Chart>` config with a
`qofaiProvenance` block of knowledge-graph node UUIDs, which is what
`chart_timeline_parser` reads. A PRD carries its plan as a Word table. No
human-authored document will ever carry a `qofaiProvenance` block, so the paper
won every plan field by absence and the deck told a reviewer the build was 24
weeks long when its PRD said ten.

WHICH SECTION STATES THE PLAN, AND WHY §6 WON. Three places in a QofAI PRD state
it, and the first draft of this module chose the wrong one. Measured across the
three current PRDs:

  * §6 "Phased Scope" carries a table whose first cell is the phase's name, its
    short focus title and its week span run together, as
    "Phase 1 Data Foundation Wks 1-3". Present in ALL THREE.
  * §11.2 "Scope of the build" states one phase per line as
    "<Label> (Wks N-M): <detail>", which is tidier. Present in only TWO: the
    TIG PRD has no such block, and a parser keyed on it read nothing there.
  * §12's milestone table states spans cleanly but names phases inside a prose
    deliverable.

So §6 is the source. Its first cell needs NO guess if the week span is removed
and what remains is taken whole: "Phase 1 Data Foundation" is both faithful and
a better Gantt label than the bare "Phase 1" that §11.2 yields. §11.2 and §12
become cross-checks on the spans, which is worth more than their labels.

Nothing is inferred: a document whose §6 states no phase row yields no phases and
the paper fills the gap exactly as before.

NOT HARDCODED TO ANY CLIENT. Every anchor here is a heading in QofAI's PRD
TEMPLATE, which is a document-format contract, not a company fact. Nothing in
this module names a client, a project or an opportunity, and the three current
PRDs differ in phase count (3, 4 and 5), in label scheme ("Discovery"/"Phase N"/
"Hardening" against "Phase N" alone) and in whether their spans abut or overlap.
So nothing here assumes a count, a naming scheme or contiguity.

A REGEX AND NOT A MODEL, for the reason `prd_front_matter` gives: this reads an
UPLOADED document, which is the exposure recorded in
`prompt-injection-unresolved-FINDING.md`. A table parser cannot be talked into
reporting a different plan.

UNITS ARE REPORTED, NEVER CONVERTED, which is `chart_timeline_parser`'s rule and
is kept here deliberately. A PRD states "Wks", so the unit is weeks; a PRD that
someday states months yields months, and reconciling the two is a multiplier this
provider does not choose.
"""

import re

from source_span import MissingFields, SourcedFigure

# The packet path this fills. The SAME field `chart_timeline_parser` fills, on
# purpose: it is an existing ROSTER entry, so a PRD-sourced plan raises
# `data_completeness` legitimately instead of moving the denominator. Adding a
# new roster field would drop a live packet to roughly 0.15 and mean no live deck
# ever renders again (`E9-RESCOPE-DESIGN.md` §3.4, and `second_pass`'s docstring
# says the same).
FIELD = "timeline"

UNIT = "weeks"

# "Wks 1-2", "Wk 1-2", with an ASCII hyphen, an en dash or an em dash. Word
# emits the en dash and the corpus carries it throughout.
_SPAN = re.compile(
    r"\bWks?\.?\s*(\d+(?:\.\d+)?)\s*[-–—]\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

# One phase line of §11.2's "Scope of the build", read only as a cross-check:
#   "Discovery (Wks 1-2): co-design sessions with the CGO; ..."
_PHASE_LINE = re.compile(
    r"^[ \t]*(?P<label>[A-Z][^()\n]{0,60}?)\s*\(\s*"
    r"Wks?\.?\s*(?P<start>\d+(?:\.\d+)?)\s*[-–—]\s*(?P<end>\d+(?:\.\d+)?)"
    r"\s*\)\s*:",
    re.MULTILINE,
)

_SCOPE_HEADING = re.compile(r"^.*Scope of the build.*$", re.MULTILINE | re.IGNORECASE)

# A markdown table's separator row, "| --- | --- |". Skipped rather than read as
# a phase, and its presence is what tells a row apart from a stray pipe.
_SEPARATOR = re.compile(r"^\s*\|[\s|:-]+\|\s*$")


_MD_HEADING = re.compile(r"^#{1,6}[ \t]", re.MULTILINE)

# A HEADING IN A DOCUMENT THAT CARRIES NO MARKDOWN AT ALL.
#
# The `#` characters come from the .docx converter, not from the document, so
# every reader keyed on them was really asking "was this a .docx" while
# appearing to ask "is this a PRD". A PDF extraction of the SAME PRD returns the
# same words with no `#` anywhere, scored zero sections, and let the opportunity
# paper back into the chain in silence (`prd-chain-pdf-FINDING.md`).
#
# Fussy about SHAPE on purpose. In a flat document the only thing separating
# "6. Phased Scope" from a numbered list item in prose is that a heading is a
# short line which does not read as a sentence, so a candidate must be brief and
# must not end like running text. Two digits at most, because the template stops
# at 14 and a year or a figure should not open a section.
_FLAT_HEADING = re.compile(
    r"^[ \t]*(?P<number>\d{1,2})\.[ \t]+(?P<title>\S[^\n]*?)[ \t]*$",
    re.MULTILINE,
)
_FLAT_TITLE_MAX = 80


def _flat_headings(text):
    """Every bare numbered heading, as ``(number, title, line_start, body_start)``."""
    out = []
    for found in _FLAT_HEADING.finditer(text or ""):
        title = found.group("title")
        if len(title) > _FLAT_TITLE_MAX or title.endswith((".", ",", ";")):
            continue
        out.append((int(found.group("number")), title,
                    found.start(), found.end()))
    return out


def _section_span(text, number):
    """``(body_start, body_end)`` for one numbered section, or ``None``.

    MARKDOWN FIRST, and unchanged: a document with `#` headings is read exactly
    as it always was, so every existing PRD and every existing test is on the
    path it was already on. The fallback below cannot fire for one.
    """
    text = text or ""
    # The number must be followed by whitespace, so a search for section 11
    # matches "# 11. Make-or-Buy Analysis" and NOT "## 11.2 Make: QofAI FDE
    # Build". Getting this wrong is not a near miss: it ends §11 at its own
    # first subsection and hides everything below it.
    opening = re.compile(
        r"^(?P<hashes>#{1,6})[ \t]*" + re.escape(str(number)) + r"\.[ \t]",
        re.MULTILINE,
    )
    found = opening.search(text)
    if found:
        depth = len(found.group("hashes"))
        closing = re.search(r"^#{1,%d}[ \t]" % depth, text[found.end():],
                            re.MULTILINE)
        end = found.end() + closing.start() if closing else len(text)
        return found.end(), end
    if _MD_HEADING.search(text):
        # It HAS markdown headings and this is not one of them, so the section
        # is genuinely absent rather than written in another style.
        return None

    headings = _flat_headings(text)
    for index, (num, _title, _line_start, body_start) in enumerate(headings):
        if num != number:
            continue
        # Ends at the next heading with a HIGHER number, not merely the next
        # numbered line: a list that restarts at 1. inside §6 is not the end
        # of §6, and the template's own numbering only ever climbs.
        end = len(text)
        for later_num, _t, later_start, _b in headings[index + 1:]:
            if later_num > number:
                end = later_start
                break
        return body_start, end
    return None


def heading_title(text, number):
    """The title on a numbered section's own heading line, or ""."""
    text = text or ""
    opening = re.search(
        r"^#{1,6}[ \t]*" + re.escape(str(number)) + r"\.[ \t]*(?P<title>[^\n]*)",
        text, re.MULTILINE)
    if opening:
        return opening.group("title")
    if _MD_HEADING.search(text):
        return ""
    for num, title, _line_start, _body_start in _flat_headings(text):
        if num == number:
            return title
    return ""


def section(text, number):
    """The text of a numbered PRD section, heading excluded, or "".

    Keyed on the template's own numbering, and ended by the next heading of the
    SAME OR SHALLOWER depth rather than by the next number, so a section
    carrying subsections (§11 carries §11.2, §3 carries §3.2) returns all of
    itself.
    """
    span = _section_span(text, number)
    if span is None:
        return ""
    return (text or "")[span[0]:span[1]]


def _rows(body):
    """The table rows of a section as lists of cells, separators dropped."""
    rows = []
    for line in body.split("\n"):
        stripped = line.strip()
        if not stripped.startswith("|") or _SEPARATOR.match(line):
            continue
        rows.append([cell.strip() for cell in stripped.strip("|").split("|")])
    return rows


def phase_records(text):
    """One record per phase row in §6 "Phased Scope", in the order stated.

    The record carries the four keys `packet_fill._phases` reads (`label`,
    `start`, `end`, `unit`) plus `focus`, the phase's stated purpose. `focus`
    rides along because §6 states it in its own column and dropping it would
    mean re-reading the document to get it back; `_phases` selects the four keys
    it knows and ignores this one.

    THE LABEL IS THE CELL MINUS ITS SPAN, taken whole. Word stacks the phase's
    name, its focus title and its weeks into one cell, which arrives as
    "Phase 1 Data Foundation Wks 1-3". Removing the span leaves "Phase 1 Data
    Foundation", which is the PRD's own words in the PRD's own order. Splitting
    that into a name and a title would be a guess, and this module does not
    guess.

    A row whose first cell states no week span is not a phase row. That is what
    skips the header row without matching on its wording, which would break the
    moment a template renames a column.
    """
    records = []
    for cells in _rows(section(text, 6)):
        if not cells:
            continue
        span = _SPAN.search(cells[0])
        if not span:
            continue
        label = _SPAN.sub("", cells[0]).strip(" .,;:–-")
        label = re.sub(r"\s{2,}", " ", label)
        if not label:
            continue
        records.append({
            "label": label,
            "start": float(span.group(1)),
            "end": float(span.group(2)),
            "unit": UNIT,
            "focus": (cells[1].strip().rstrip(".") if len(cells) > 1 else ""),
        })
    return records


def scope_of_build(text):
    """The "Scope of the build" block of §11.2, or "".

    Located by its own label rather than by a section number, because it sits
    inside §11.2 and is absent entirely from one of the three current PRDs.
    Read only for the cross-check.
    """
    body = section(text, 11) or (text or "")
    heading = _SCOPE_HEADING.search(body)
    if not heading:
        return ""
    rest = body[heading.end():]
    stop = re.search(r"^#{1,6}[ \t]|^\s*Build timeline\s*$", rest, re.MULTILINE)
    return rest[:stop.start()] if stop else rest


def _spans_in_rows(body):
    """Every week span stated in a section's table ROWS, in order.

    Rows only, so the paragraph above §6's table, which states the whole
    engagement's length rather than a phase's, cannot be read as a phase span.
    """
    spans = []
    for cells in _rows(body):
        for cell in cells:
            for start, end in _SPAN.findall(cell):
                spans.append((float(start), float(end)))
    return spans


def cross_check(text, records):
    """Why another section disagrees with the phases read from §6, or None.

    THE POINT OF THIS. A QofAI PRD states its plan up to three times, and the
    statements agreed on every document in the corpus when this was written. So
    a disagreement does not mean the PRD contradicts itself, it means this
    module misread one of them, and a misread plan is exactly the failure that
    put a 24-week timeline in front of a reviewer. Checking costs one pass over
    text already in memory.

    A section that states no spans is NOT a disagreement. §11.2's scope block is
    absent from one of the three current PRDs and §6's table from another
    template's. Silence is silence; only a stated, different set of spans is a
    conflict.
    """
    stated = [(record["start"], record["end"]) for record in records]
    checks = (("section 12", _spans_in_rows(section(text, 12))),
              ("the scope of the build in section 11.2",
               [(float(match.group("start")), float(match.group("end")))
                for match in _PHASE_LINE.finditer(scope_of_build(text))]))
    for where, spans in checks:
        if spans and spans != stated:
            return (f"this PRD's phase plan reads {stated} from section 6 and "
                    f"{spans} from {where}; they disagree, so the plan is not "
                    f"read rather than guessed at.")
    return None


def parse_phases(text, missing=None, *, document, opportunity):
    """The PRD's phase plan as a `SourcedFigure`, or None.

    Signature deliberately mirrors `chart_timeline_parser.parse_timeline`: same
    field, same keyword-only `document` / `opportunity`, same `missing`
    contract, so `packet_assembly` can try one and then the other without either
    knowing about the other.

    Absence is a normal, recorded outcome. A document with no §6 phase table is
    every opportunity paper ever published, and on that input this returns None
    with a reason and the paper's own parser fills the field.
    """
    missing = missing if missing is not None else MissingFields()
    records = phase_records(text)
    if not records:
        missing.record(FIELD, "no table in this document's section 6 states a "
                              "phase with a week span.")
        return None
    conflict = cross_check(text, records)
    if conflict:
        missing.record(FIELD, conflict)
        return None
    return SourcedFigure(field=FIELD, value=records,
                         span=section(text, 6).strip(), source=text,
                         document=document, opportunity=opportunity)


# The template's own numbered sections, used only to RECOGNISE a QofAI PRD.
# Deliberately the stable spine of the template rather than all of it: every one
# of these is present in all three current PRDs, and a document needs several
# before it is treated as one.
TEMPLATE_SECTIONS = (
    (3, "Goals & Success Metrics"),
    (5, "Current-State Workflow"),
    (6, "Phased Scope"),
    (7, "Functional Requirements"),
    (12, "Build Milestones"),
    (14, "Next Steps"),
)

# How many of them a document must carry. Three is enough to separate a PRD from
# any other attachment by a wide margin and leaves room for a template that
# renumbers or drops one: the three current PRDs carry all six, and an
# opportunity paper carries none.
_PRD_SECTIONS_REQUIRED = 3


def is_prd(text):
    """Whether a document is a QofAI PRD, by its template's own spine.

    WHY THIS EXISTS AND WHY IT IS NOT "does it have a phase table". Antonio
    ruled on 2026-09-20 that an uploaded PRD is the only source and the
    opportunity paper is not read at all beside one. That rule is about PRDs. A
    reviewer may also attach a supporting document, a one-pager or a
    spreadsheet, and those have always joined a chain that still holds the
    paper; dropping the paper for any attachment at all would silently rewrite
    item 14's behaviour for every one of them.

    So the question this answers is "is this the kind of document that rule is
    about", NOT "does it carry everything a deck needs". A PRD that is missing
    figures is still a PRD, and it must produce an insufficient-PRD refusal
    rather than fall back to the paper. Those are different questions and
    conflating them would reintroduce the merge for exactly the documents the
    rule was written for.

    Structural, and keyed on the template rather than on any client: it counts
    how many of the template's numbered sections a document actually carries.
    """
    found = 0
    for number, heading in TEMPLATE_SECTIONS:
        body = section(text, number)
        if not body:
            continue
        # The heading text is checked on the line the section opened at, so a
        # document that merely numbers its sections 1 to 14 is not mistaken for
        # a PRD. `heading_title` reads a markdown heading where there is one and
        # a bare numbered line where the document carries no markdown at all,
        # so the question stays "is this a PRD" rather than becoming "was this a
        # .docx" (`prd-chain-pdf-FINDING.md`). The title match is what makes the
        # flat fallback safe: a numbered list item in prose does not happen to
        # be called "Phased Scope".
        if heading.lower() in heading_title(text, number).lower():
            found += 1
    return found >= _PRD_SECTIONS_REQUIRED


# A percentage-point figure: "+0.50 pp", "2.5 percentage points".
# A percentage-point figure. `pp`, `pts` and `percentage point(s)` are all in
# the current corpus and all mean the same movement: the Contoso PRD
# writes "+0.50 pp" in its scenario table and "+0.50 to +2.50 pts" in its KPI
# table for the identical fact, and the TIG PRD writes "+2.5 pts". A bare `%`
# is still refused, because a percent is a LEVEL and this field is a MOVEMENT;
# that is `scenario_table_parser`'s rule and it is kept.
_PP = re.compile(
    r"([+-]?\d+(?:\.\d+)?)\s*(?:pp\b|pts?\b|percentage[\s-]?points?\b)",
    re.IGNORECASE)


def tables(text):
    """Every pipe table in a document as `{"headers": [...], "rows": [[...]]}`."""
    found, current, offset = [], None, 0
    for line in (text or "").split("\n"):
        here, offset = offset, offset + len(line) + 1
        stripped = line.strip()
        if not stripped.startswith("|"):
            if current:
                found.append(current)
                current = None
            continue
        if _SEPARATOR.match(line):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if current is None:
            current = {"headers": cells, "rows": [], "start": here}
        else:
            current["rows"].append(cells)
    if current:
        found.append(current)
    return found


def scenario_margins(text):
    """`{scenario label: (pp value, row text)}` from a PRD's scenario table.

    WHY THIS IS NOT A CHANGE TO `scenario_table_parser`. That parser requires a
    margin column's own label to name BOTH "ebitda" and "margin", and its
    docstring argues the case: the rule rejects a gross-margin driver, a
    resulting margin LEVEL stated as a percent, and "% of Current EBITDA", which
    is a share of the base rather than a movement in the margin. Borrowing a
    percentage that is not a margin impact is the failure that parser exists to
    prevent, and loosening it would weaken every published paper it reads.

    A QofAI PRD states the same fact in a differently shaped table. Its Appendix
    A scenario table heads the column "Margin Uplift" and names EBITDA in the
    SIBLING column, "Incremental EBITDA". So the safety argument is kept and
    moved one level out: the TABLE must name EBITDA, the column must name a
    margin, and every cell read must carry the unit in pp. A table with a gross
    margin column and no EBITDA column is rejected here exactly as it is there.

    Measured 2026-09-20: this reads +0.50 and +2.50 pp for the Contoso
    Pipeline Cockpit PRD, against the +0.62 to +1.24 pp its opportunity paper
    states, which is the discrepancy that started this work.
    """
    margins = {}
    for table in tables(text):
        headers = [header.lower() for header in table["headers"]]
        if not headers or "scenario" not in headers[0]:
            continue
        # The table must be about EBITDA. `scenario_table_parser` applies that
        # guard to the margin column's own label; this applies it to the
        # column's SIBLINGS, and failing that to the prose immediately above
        # the table. Both are needed by the corpus: the TIG PRD names it in a
        # sibling ("Annual EBITDA Impact") and the Client Onboarding PRD names it only in
        # the paragraph before ("points of EBITDA margin"). What is never
        # accepted is a scenario table with no mention of EBITDA anywhere near
        # it, which is the gross-margin case the guard exists for.
        lead = (text or "")[max(0, table["start"] - 800):table["start"]].lower()
        if not any("ebitda" in header for header in headers) and "ebitda" not in lead:
            continue
        columns = [index for index, header in enumerate(headers)
                   if "margin" in header]
        if not columns:
            continue
        column = columns[0]
        for row in table["rows"]:
            if len(row) <= column or not row[0].strip():
                continue
            found = _PP.search(row[column])
            if not found:
                continue
            margins[row[0].strip().lower()] = (float(found.group(1)),
                                               " | ".join(row))
    return margins


# The packet's three baseline paths, named here so this module states them once.
REVENUE = "baseline.revenue_ttm_usd"
EBITDA = "baseline.adjusted_ebitda_usd"
MARGIN = "baseline.adjusted_ebitda_pct"

# "$14.8M", "$5,511,000", "$780,000". The suffix is optional and scales.
_USD = r"\$\s*([\d,]+(?:\.\d+)?)\s*([MK])?"
_SCALE = {"M": 1_000_000, "K": 1_000, None: 1, "": 1}

# The Appendix A opener. Every PRD in the corpus states its base period this way
# and states revenue, EBITDA and margin in the sentence that follows.
_BASE_BLOCK = re.compile(r"^\s*Base\s*\(([^)]*)\)\.(?P<body>[^\n]*)",
                         re.MULTILINE | re.IGNORECASE)
_REVENUE = re.compile(r"(?:total\s+)?revenue\s+" + _USD, re.IGNORECASE)
_EBITDA = re.compile(r"\bEBITDA\s+" + _USD, re.IGNORECASE)
_MARGIN = re.compile(r"\(\s*([\d.]+)\s*%\s*margin", re.IGNORECASE)


def _usd(match):
    return float(match.group(1).replace(",", "")) * _SCALE.get(match.group(2), 1)


def baseline(text):
    """`{packet path: (value, span)}` for the three baseline figures a PRD states.

    Read from Appendix A's "Base (<period>)." sentence, which every PRD in the
    corpus carries and which states all three in one line:

        Base (TTM May 2025 - Apr 2026). Total revenue $14.8M; advisor fees
        $14.79M; COGS 45.7% of revenue; EBITDA $3.72M (25.2% margin, ...)

    Prose rather than a table, which is why `baseline_parser` cannot read it and
    says so: "no baseline statement in this document is one this parser can
    read." That parser reads the published paper's table shapes and is left
    alone here for the same reason `is_margin_label` is.

    STRICT ABOUT WHICH NUMBER IS WHICH. Revenue is the figure the word
    "revenue" introduces, EBITDA the one "EBITDA" introduces, and the margin is
    read only from the parenthetical that names itself a margin. A sentence
    missing one of the three yields the two it has; nothing is derived from the
    others, because revenue and EBITDA imply a margin only if the two are the
    same basis and choosing that is not this module's call.
    """
    found = {}
    for block in _BASE_BLOCK.finditer(text or ""):
        body, span = block.group("body"), block.group(0).strip()
        for path, pattern, read in ((REVENUE, _REVENUE, _usd),
                                    (EBITDA, _EBITDA, _usd),
                                    (MARGIN, _MARGIN,
                                     lambda m: float(m.group(1)))):
            if path in found:
                continue
            hit = pattern.search(body)
            if hit:
                found[path] = (read(hit), span)
    return found


# §14's group headings, and the one whose items each earn a numbered step.
# Every other group collapses to one, which is what keeps the slide inside its
# own template contract ("a numbered list (01-04)") on every PRD in the corpus:
# three interviews plus one grouped requests item plus one grouped closing item
# is five, whether the PRD closes §14 with an "Output" paragraph (Contoso)
# or a "Sprint schedule" table (Client Onboarding, TIG).
_EXPANDS = "interview"

# The day tag a group heading states, e.g. "Interviews (Days 1-3; three, 30-60
# minutes each)" or "Data requests (issued Day 1, due Day 4)". Read only when
# the parenthetical actually names a DAY: two of the three current PRDs head
# their interviews "(60 minutes each)", which is a duration and not a schedule,
# and tagging a step with it would be worse than leaving it untagged.
_GROUP_DAYS = re.compile(r"\(([^)]*\bday[s]?\b[^)]*)\)", re.IGNORECASE)

# "Owner: COO / Salesbox admin." at the end of a data-request item.
_OWNER = re.compile(r"\bOwner:\s*([^.]+?)\s*\.", re.IGNORECASE)

# A leading role, which is how §14 names an interview's counterpart. Three
# shapes are live across the corpus and all three end the role at a different
# character: "CGO (60 min). Confirm ..." ends it at the bracket,
# "Controller (30 min)." at the bracket too, and
# "VP Operations: integration walkthrough. Live screen-share ..." at the colon.
_LEADING_ROLE = re.compile(r"^([A-Z][^.(:]{0,72}?)\s*(?:\(|\.|:)")

# "COO / VP Operations WITH the Salesbox administrator" names a counterpart and
# then who else is in the room. The owner is the counterpart.
_WITH = re.compile(r"\s+with\s+", re.IGNORECASE)

# "QofAI issues a fixed quoted price ..." — a capitalised subject followed by a
# lower-case verb. Used only for a closing paragraph, where §14 states no owner
# row and the sentence's own subject is the owner.
_SUBJECT = re.compile(r"^([A-Z][A-Za-z]*)\s+[a-z]")

_COUNT_WORDS = ("", "one", "two", "three", "four", "five", "six", "seven",
                "eight", "nine", "ten")

# How a step's role splits into the parts a schedule row may name on their own.
# TIG's "President & Owner" is scheduled by a row reading "President interview",
# so the whole role string never appears; each part is matched as a WHOLE
# PHRASE, never as a loose word. "Owner" is a part here and matches no row.
_ROLE_SPLIT = re.compile(r"\s*(?:&|/|,|\band\b)\s*", re.IGNORECASE)

# A collapsed step's owner when it holds several: "four owners".
_COUNTED_OWNERS = re.compile(r"^\w+\s+owners$", re.IGNORECASE)


def role_parts(text):
    """A role or subject as the whole phrases a line may name it by."""
    return tuple(part for part in (piece.strip()
                                   for piece in _ROLE_SPLIT.split(text or ""))
                 if part)


def step_subject(record):
    """What names a Next Steps record in §14: its role, else its title's noun.

    An interview step is named by its counterpart ("VP Operations"). A
    collapsed group carries a count and no single owner ("Six data requests",
    "four owners"), so it is named by its heading noun, which is its title
    with the count word taken off.
    """
    owner = (record.get("owner") or "").strip()
    if owner and not _COUNTED_OWNERS.match(owner):
        return owner
    words = (record.get("title") or "").split()
    if words and words[0].lower() in _COUNT_WORDS[1:]:
        words = words[1:]
    return " ".join(words)


def names(line, part):
    """Whether a line names a role part, as a whole phrase."""
    return bool(re.search(r"(?<!\w)" + re.escape(part) + r"(?!\w)", line,
                          re.IGNORECASE))


def _groups(body):
    """§14 as `[(heading, [item, ...]), ...]`, in the order stated."""
    groups, heading, items = [], "", []
    for line in body.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        head = re.match(r"^#{1,6}\s*(.+)$", stripped)
        if head:
            if heading or items:
                groups.append((heading, items))
            heading, items = head.group(1).strip(), []
            continue
        items.append(stripped)
    if heading or items:
        groups.append((heading, items))
    return groups


# How long a step's title may run before it stops reading as a title. §14's
# closing paragraph is one semicolon-chained sentence ("QofAI issues a fixed
# quoted price per line and in total, replacing the §11.2 ranges; a confirmed
# Week 1 start date ...; and a short list ..."), and taking the whole of it put
# a 200-character paragraph in the title slot on the first clean render.
TITLE_LIMIT = 64


def _clause(sentence):
    """A sentence's opening clause, where the sentence is too long to be a title.

    Cut at the last semicolon or comma BEFORE the limit, so the title ends where
    the document's own punctuation ends a thought rather than mid-phrase. A
    sentence with no break before the limit is returned whole: truncating on a
    word boundary would invent an ellipsis the document does not have, and the
    full text is on the step's body either way.
    """
    if len(sentence) <= TITLE_LIMIT:
        return sentence
    cut = max(sentence.rfind("; ", 0, TITLE_LIMIT),
              sentence.rfind(", ", 0, TITLE_LIMIT))
    return sentence[:cut].rstrip(" ;,") if cut > 0 else sentence


def _title_and_rest(item):
    """An item's first sentence as its title, the remainder as its description.

    §14 writes every item that way: a short subject, a full stop, then what it
    is for. "Salesbox API entitlement and record schema. Edition, API/
    integration-user availability, and a field list ..."
    """
    parts = re.split(r"(?<=\.)\s+", item, maxsplit=1)
    sentence = parts[0].strip().rstrip(".")
    rest = parts[1].strip() if len(parts) > 1 else ""
    title = _clause(sentence)
    # What the title left behind leads the body, so shortening the title never
    # costs the deck a word the document stated.
    if title != sentence:
        remainder = sentence[len(title):].lstrip(" ;,")
        rest = f"{remainder}. {rest}".strip() if rest else remainder
    return title, rest


def _collapsed_title(heading, count):
    """A collapsed group's title: its heading, counted where that reads."""
    noun = heading.split("(")[0].strip().rstrip(":").strip()
    plural = noun.split()[-1].lower().endswith("s") if noun.split() else False
    if plural and count < len(_COUNT_WORDS):
        return f"{_COUNT_WORDS[count].capitalize()} {noun[0].lower()}{noun[1:]}"
    return noun


def next_steps(text):
    """§14 as the deck's numbered action items, or [].

    WHY DETERMINISTIC AT ALL. `next_steps` is held out of `packet_fill.fill_map`
    on purpose so the second pass reads it from prose, which is right for a
    published paper and wrong for a PRD. Measured 2026-09-20: the same PRD
    produced 11 items on one run and 8 on another, and 11 overflowed the slide
    by 319px across 26 elements. A count that changes between runs is not a
    fact about the document.

    WHAT EACH STEP CARRIES, all of it stated in §14 and none of it derived:

      * `week` is the group heading's own day range, so a step reads "DAYS 1-3"
        rather than the "[MISSING: week]" the schema's week-denominated field
        produced on every item. §14 measures a scoping sprint in DAYS; the
        markers were a schema mismatch, not a data gap.
      * `owner` is the "Owner:" row where §14 states one, the leading role where
        it names the counterpart, and the sentence's own subject for a closing
        paragraph.
      * a collapsed group's `body` lists its items by title, so nothing §14
        states leaves the deck.
    """
    groups = [(heading, items) for heading, items in _groups(section(text, 14))
              if heading]
    records = []
    # Where §14's own schedule table became a step, and its rows, so the join
    # below can put each row's day on the step it schedules.
    schedule_at, schedule_rows = None, []
    for heading, items in groups:
        days = _GROUP_DAYS.search(heading)
        week = (days.group(1).split(";")[0].strip().upper() if days else "")
        if _EXPANDS in heading.lower():
            for item in items:
                title, rest = _title_and_rest(item)
                role = _LEADING_ROLE.match(item)
                owner = _WITH.split(role.group(1), 1)[0].strip() if role else ""
                records.append({
                    "week": week,
                    "owner": owner,
                    "title": title,
                    # `body`, not `description`: the packet schema names this
                    # leaf and `coverage_guard` refuses a field with no template
                    # slot, which is how a rename got caught here rather than
                    # silently dropping the text from the prompt.
                    "body": rest or title,
                })
            continue
        # Every other group becomes ONE step whose description names what it
        # holds, which is how eleven stated items reach a five-step slide with
        # nothing dropped.
        titles, owners = [], []
        for item in items:
            title, _rest = _title_and_rest(item)
            owner = _OWNER.search(item)
            if owner:
                owners.append(owner.group(1).strip())
            titles.append(title)
        if not titles:
            continue
        rows = [row for row in _rows("\n".join(items)) if row]
        if rows:
            # A "Sprint schedule" table rather than a paragraph list: its rows
            # already read as "Day 1 | Activity | Owner".
            titles = [" ".join(cell for cell in row[:2] if cell)
                      for row in rows[1:]] or titles
            owners = [row[2] for row in rows[1:] if len(row) > 2 and row[2]]
            if _is_schedule(rows):
                schedule_at, schedule_rows = len(records), rows[1:]
        count = len(titles)
        single = count == 1
        subject = _SUBJECT.match(items[0]) if single else None
        records.append({
            "week": week,
            "owner": (owners[0] if single and owners else
                      subject.group(1) if subject else
                      f"{_COUNT_WORDS[len(set(owners))]} owners"
                      if 1 < len(set(owners)) < len(_COUNT_WORDS) else ""),
            # The heading's own noun phrase, counted only when it is PLURAL:
            # "Data requests" takes a count and reads "Six data requests",
            # "Sprint schedule" does not and would read "four sprint schedule".
            "title": (titles[0] if single else _collapsed_title(heading, count)),
            "body": (_title_and_rest(items[0])[1] or titles[0]
                     if single else "; ".join(titles)),
        })
    if schedule_at is not None:
        records = _join_schedule(records, schedule_at, schedule_rows)
    for ordinal, record in enumerate(records, start=1):
        record["number"] = f"{ordinal:02d}"
    return records


def _is_schedule(rows):
    """Whether a §14 table is a schedule: a header reading Day | ... | Owner.

    Read off the header's own words rather than the group's heading, so a PRD
    that calls it "Timeline" or "Week plan" is read the same way.
    """
    header = [cell.strip().lower() for cell in (rows[0] if rows else [])]
    return (len(header) >= 3 and header[0] in ("day", "days", "week", "weeks")
            and header[2].startswith("owner"))


def _join_schedule(records, schedule_at, rows):
    """Put each schedule row's day onto the step it schedules (2026-09-23).

    THE SECOND §14 LAYOUT. Two of the three PRDs in the corpus head their
    interviews "(60 minutes each)", a duration, and state the days only in a
    Day | Activity | Owner table at the end of §14. Read as a step of its own,
    that table left every step it schedules at `[MISSING: week]`: five of them
    on the first two-PRD deck (output-18), each one stated in the document.

    A step with no week is matched to a row by its role, split into WHOLE
    parts ("President & Owner" is scheduled by a row reading "President
    interview"), against the row's ACTIVITY cell, never its owner cell, which
    names everyone in the room. EXACTLY ONE row or no join: a step that two
    rows could schedule keeps its empty week rather than a guess. The row's day
    becomes the step's week, and its owner cell becomes the step's owner only
    where the step names none.

    Once rows have joined, the table stops being a step (Antonio, 2026-09-23),
    because it would only repeat their timing. A row that joined nothing is not
    dropped with it: it becomes a step of its own, in the shape the recruiting
    PRD's "Output (Day 5)" step already has, so nothing the table states leaves
    the deck. A table none of whose rows join keeps its step exactly as before.
    """
    joined, used = [dict(record) for record in records], set()
    for index, record in enumerate(joined):
        if index == schedule_at or record.get("week"):
            continue
        parts = role_parts(step_subject(record))
        hits = [at for at, row in enumerate(rows)
                if len(row) > 1 and any(names(row[1], part) for part in parts)]
        if len(hits) != 1:
            continue
        row = rows[hits[0]]
        record["week"] = row[0].strip().upper()
        if not record.get("owner") and len(row) > 2 and row[2].strip():
            record["owner"] = row[2].strip()
        used.add(hits[0])
    if not used:
        return joined
    left = []
    for at, row in enumerate(rows):
        if at in used or len(row) < 2 or not row[1].strip():
            continue
        title, rest = _title_and_rest(row[1].strip())
        left.append({
            "week": row[0].strip().upper(),
            "owner": row[2].strip() if len(row) > 2 else "",
            "title": title,
            "body": rest or title,
        })
    return joined[:schedule_at] + left + joined[schedule_at + 1:]


# A §7 subsection heading: "## 7.1 Pipeline of Record".
_SUBSECTION = re.compile(r"^#{1,6}[ \t]*(?P<number>7\.(?P<index>\d+))[ \t]+"
                         r"(?P<title>[^\n]+)$", re.MULTILINE)


def platform_layers(text):
    """§7's functional components as the deck's numbered platform records, or [].

    WHY §7 AND NOT §1. `platform_layers` is the twin of `next_steps`: the other
    field `packet_fill.FALLBACK_DEFAULTS` holds back for the second pass, and on
    2026-09-20 it was inventing. The deck carried six components where the PRD's
    executive summary states three modules, with lower-case titles and
    descriptions that restated their own titles word for word, all three of
    which Antonio raised in review.

    §1 states a module count in ONE of the three current PRDs; §7 has numbered
    subsections in all three (5, 6 and 4). It is also the PRD's own functional
    decomposition rather than its pitch framing, so it restores two components
    the pitch leaves out and the deck had lost entirely: the pipeline of record,
    which §5 calls the thing to build FIRST, and the capacity gate, which §2
    calls the condition that governs everything. Antonio chose §7 on 2026-09-20
    with both readings in front of him.

    THE TITLE IS THE HEADING, which is why the capitalisation complaint
    disappears rather than being fixed: a PRD's headings are already written in
    title case, and the lower-case titles came from a model paraphrasing prose.

    THE BODY IS THE FIRST REQUIREMENT, never the title again. Each §7 subsection
    is a requirement table whose first row (FR-N.1) states the defining
    capability, so a component describes itself with something it did not
    already say.
    """
    body = section(text, 7)
    if not body:
        return []
    marks = list(_SUBSECTION.finditer(body))
    records = []
    for position, mark in enumerate(marks):
        end = marks[position + 1].start() if position + 1 < len(marks) else len(body)
        rows = _rows(body[mark.end():end])
        first = ""
        for row in rows:
            # The requirement cell, which sits beside an "FR-N.N" id. Keyed on
            # the id's shape rather than on a column name, so a template that
            # renames its headers still reads.
            if len(row) > 1 and re.match(r"^FR-[\d.]+$", row[0].strip()):
                first = row[1].strip()
                break
        records.append({
            "number": f"{position + 1:02d}",
            "title": mark.group("title").strip().rstrip("."),
            "body": first,
        })
    return [record for record in records if record["body"]]


def subsection(text, number):
    """A numbered SUBSECTION's body ("3.1", "11.2"), heading excluded, or "".

    Separate from `section` because the two anchor differently: a top-level
    heading is "# 6. Phased Scope", with a dot after the number, and a
    subsection is "## 3.1 Product Goals", with none. `section(text, 3)` would
    also swallow §3.2 and §3.3, which is exactly what must not happen here.
    """
    opening = re.compile(
        r"^(?P<hashes>#{1,6})[ \t]*" + re.escape(str(number)) + r"[ \t]+[^\n]*\n",
        re.MULTILINE)
    found = opening.search(text or "")
    if not found:
        return ""
    rest = (text or "")[found.end():]
    closing = re.search(r"^#{1,%d}[ \t]" % len(found.group("hashes")), rest,
                        re.MULTILINE)
    return rest[:closing.start()] if closing else rest


# A lead-in ("The goals are:") rather than a goal, and too short to be one.
_MIN_BULLET = 40


def capability_bullets(text):
    """§3.1's product goals, as slide 2's AFTER bullets, or [].

    SCOPED TO §3.1 AND NEVER §3, which is the whole reason `subsection` exists.
    §3.3 Non-Goals sits in the same section in the IDENTICAL shape — a
    capitalised label, a separator, then prose — so a §3-scoped reader would put
    "Replacing the external recruiter on day one" on a client deck as a
    capability when the PRD explicitly rules it out. That is a worse failure
    than the one this fixes.

    WHY THE WHOLE LINE. Antonio, 2026-09-20: "it'd be more professional if the
    bullets on the right started capitalized because bullets on the left side on
    the today box start capitalized and bullets on the right side don't." They
    did not because the bullets carried only the clause AFTER the goal's
    separator, which is mid-sentence by construction. Taking the PRD's own line
    whole fixes the capitalisation at its source rather than upper-casing a
    letter the document wrote in lower case, and it makes each bullet lead with
    its point. The separator varies across the corpus (a colon in two PRDs, a
    full stop in the third), which is a second reason not to split on it.

    Every goal is returned, not a chosen few. Slide 2's panel decides what fits:
    `panel_fit` measures real room at the 10.5px floor and `bullet_ranking`
    orders them. Handing those two all seven is what lets them do their job; the
    second pass was writing two or three and the choice of WHICH was a model's.
    One of the five it dropped was "Protect the onboarding constraint", which
    §2 of this PRD calls the condition that governs everything.
    """
    bullets = []
    for line in subsection(text, "3.1").split("\n"):
        line = line.strip()
        if (not line or line.startswith("|") or line.startswith("#")
                or len(line) < _MIN_BULLET or line.endswith(":")):
            continue
        bullets.append(line.rstrip("."))
    return bullets


def milestone_ids(text):
    """§12's milestone identifiers, in the order stated, or [].

    The deck generated M1 through M5 where the Contoso PRD names them M0
    through M4, because `packet_fill._milestones` derives one milestone per
    PHASE BOUNDARY and `data_source_adapter._milestone_role` falls back to an
    ordinal when the record states no id. The count and the weeks were right
    and the names were ours. `_milestone_role` already carries a stated id
    verbatim, so this only has to state one.

    A DATA ROW IS ONE WHOSE TARGET CELL STATES A WEEK SPAN. That is what skips
    the header without matching on the word "Milestone", which a template is
    free to rename, and it is the same test `phase_records` uses on §6. The
    identifier is the first cell as written: M0-based in one PRD and M1-based in
    the other two, and nothing here renumbers or normalises it.

    Safe to pair with phase boundaries by index because `cross_check` has
    already refused the plan if §6 and §12 disagree about their spans, which is
    what makes "the nth milestone closes the nth phase" a fact rather than an
    assumption.
    """
    ids = []
    for cells in _rows(section(text, 12)):
        if len(cells) < 2 or not cells[0].strip():
            continue
        if not _SPAN.search(cells[1]):
            continue
        ids.append(cells[0].strip())
    return ids


# ----------------------- §11.2: what the build costs ------------------------
#
# The commercial slide's PRD half (`build-plan-commercial-slide.md`). §11.2
# "Make: QofAI FDE Build" carries an "Indicative cost (QofAI build)" table headed
# | Item | Year 1 | Ongoing |, and the paragraph under it states the basis and
# the payback per case. Measured on all three current PRDs, 2026-09-23.
#
# ONLY THE TOTAL ROW IS READ. The line items are QofAI's internal build
# breakdown of an estimate, and Antonio ruled on 2026-09-22 that they stay off
# the slide, so nothing reads them and nothing can leak them.
#
# ONLY INSIDE THE ITEM TABLE. §11 carries other rows that begin "Total", on
# both sides of it: "Total cost (Yr 1 + ongoing)" in the evaluation criteria
# before it, "Total cost fit (10%)" in the decision matrix after it, and a Pros
# & cons cell that begins "Lowest total cost". A reader scanning §11 for a
# total row would take the first of those. `tables` splits the section into
# its tables and only the one headed "Item" is read.

_COST_HEADER = "item"

# "Indicative total" in two PRDs and "Indicative total (midpoint $100K / $40K
# per yr)" in the third. The label is matched and then DISCARDED: rows are named
# by the column they sit under, so the midpoint, which is QofAI's internal
# method, can never reach the slide.
_TOTAL_LABEL = re.compile(r"^\s*(?:indicative\s+)?total\b", re.IGNORECASE)

# A cell stating no cost for its column ("n/a" under Ongoing for a one-off).
_NO_VALUE = re.compile(r"^\s*(?:n/?a|none|[-–—])?\s*$", re.IGNORECASE)

# "13–17 months", "4.1–6.0 months", "15 months". The singular is accepted
# because "month" is how the corpus writes a range used as a noun, and the rule
# below (the FIRST range after a case's name) is what keeps that one out.
_MONTHS = re.compile(
    r"(?P<low>\d+(?:\.\d+)?)(?:\s*[-–—]\s*(?P<high>\d+(?:\.\d+)?))?\s*months?\b",
    re.IGNORECASE)


def _cost_table(text):
    """``(table, body)`` for §11.2's cost table, or ``(None, body)``."""
    body = subsection(text, "11.2")
    for table in tables(body):
        headers = [header.strip().lower() for header in table["headers"]]
        if len(headers) > 1 and headers[0] == _COST_HEADER:
            return table, body
    return None, body


def _paragraph_after(body, table):
    """The first prose line after ``table`` in ``body``, or ""."""
    rest = body[table["start"]:]
    lines = rest.split("\n")
    index = 0
    while index < len(lines) and lines[index].strip().startswith("|"):
        index += 1
    for line in lines[index:]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("|", "#")):
            return ""
        return stripped
    return ""


def cost_total(text):
    """§11.2's total row as ``{"rows", "basis", "span"}``, or None.

    ``rows`` is one ``{"label", "value"}`` per cost column that states a cost,
    labelled by the COLUMN HEADER ("Year 1", "Ongoing") and valued with the
    PRD's own cell text ("$9K–$14K/yr est."), so nothing is reformatted and the
    total row's own label never travels. ``basis`` is "indicative" when the
    paragraph under the table says so, which all three current PRDs do, and ""
    otherwise. ``span`` is the total row as the document states it.

    Generic by construction: no column count, line count or column name is
    assumed beyond the "Item" header that identifies the table. A table with no
    total row, or with two, yields None. Summing the lines instead would print a
    figure the PRD does not state.
    """
    table, body = _cost_table(text)
    if table is None:
        return None
    totals = [row for row in table["rows"]
              if row and _TOTAL_LABEL.match(row[0])]
    if len(totals) != 1:
        return None
    total = totals[0]
    rows = []
    for header, cell in zip(table["headers"][1:], total[1:]):
        if _NO_VALUE.match(cell):
            continue
        rows.append({"label": header.strip(), "value": cell.strip()})
    if not rows:
        return None
    note = _paragraph_after(body, table)
    return {
        "rows": rows,
        "basis": "indicative" if re.search(r"\bindicative\b", note, re.I) else "",
        # The row's OWN line, byte for byte, because `SourcedFigure` refuses a
        # span that does not occur in its source. Rebuilding it from the cells
        # would normalise the spacing and fail that check.
        "span": _raw_row(body, total),
    }


def _raw_row(body, cells):
    """The line in ``body`` whose cells are ``cells``, stripped, or ""."""
    for line in body.split("\n"):
        stripped = line.strip()
        if (stripped.startswith("|")
                and [cell.strip() for cell in stripped.strip("|").split("|")]
                == cells):
            return stripped
    return ""


def cost_note(text):
    """The paragraph under §11.2's cost table, verbatim, or "".

    Payback's span. It is the one paragraph both `cost_total`'s basis and
    `payback` read, and it occurs in the document exactly as returned.
    """
    table, body = _cost_table(text)
    return _paragraph_after(body, table) if table is not None else ""


def _depth_zero_split(text, separators):
    """``text`` split at any of ``separators`` outside parentheses.

    One current PRD's payback sentence carries "(16 at the midpoint; ongoing cost is
    covered roughly 2× thereafter)", so a plain split on ";" cuts inside the
    parenthetical and hands the ambitious clause a stray fragment.
    """
    parts, current, depth, index = [], [], 0, 0
    while index < len(text):
        char = text[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        if depth == 0:
            hit = next((sep for sep in separators
                        if text.startswith(sep, index)), None)
            if hit:
                parts.append("".join(current))
                current = []
                index += len(hit)
                continue
        current.append(char)
        index += 1
    parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


def _without_parentheticals(text):
    """``text`` with every parenthetical removed, nesting respected."""
    out, depth = [], 0
    for char in text:
        if char == "(":
            depth += 1
            continue
        if char == ")":
            depth = max(0, depth - 1)
            continue
        if depth == 0:
            out.append(char)
    return "".join(out)


def payback(text, names):
    """``({case: {"text", "low", "high"}}, [reasons])`` from §11.2's paragraph.

    ``names`` are the case names the PRD's own scenario table states, and the
    result is keyed by each name lower-cased, which is how `_prd_margin` joins a
    margin onto its case. Nothing is keyed by a name this function made up.

    THE RULES, each from a shape in the real text:
      * only the sentence that says "payback" is read, so a decoy dollar range
        elsewhere in the paragraph ("a $50K–$100K annual tooling budget") is
        never in play
      * clauses split at ";" and at an em dash, both outside parentheses
      * parentheticals are ignored inside a clause
      * a clause is read only if it names exactly ONE case, as a whole word,
        and its payback is the first month range AFTER that name
      * a clause naming no case is ignored with a reason: one current PRD
        ends its sentence "consistent with the 2.3–6.0 month range in the
        opportunity assessment", which is a range for neither case
      * a case stated in two clauses is refused, never chosen between

    ``text`` in each entry is the range as the PRD wrote it, with an en dash
    ("13–17 months", "4.1–6.0 months"), so "6.0" is not reformatted to "6".
    """
    table, body = _cost_table(text)
    reasons = []
    if table is None:
        return {}, reasons
    note = _paragraph_after(body, table)
    sentences = _depth_zero_split(note, (". ",))
    stated = [sentence for sentence in sentences if "payback" in sentence.lower()]
    if not stated:
        return {}, reasons
    wanted = {(name or "").strip().lower() for name in names if name}
    found, seen_twice = {}, set()
    for clause in _depth_zero_split(" ".join(stated), (";", " — ", " – ")):
        plain = _without_parentheticals(clause)
        lowered = plain.lower()
        hits = []
        for name in wanted:
            match = re.search(r"\b" + re.escape(name) + r"\b", lowered)
            if match:
                hits.append((name, match.end()))
        if len(hits) != 1:
            if not hits and _MONTHS.search(plain):
                reasons.append(
                    f"a payback clause names no case from the scenario table "
                    f"(“{clause.strip()}”), so its range is not read.")
            elif len(hits) > 1:
                reasons.append(
                    f"a payback clause names more than one case "
                    f"(“{clause.strip()}”), so neither is read from it.")
            continue
        name, after = hits[0]
        months = _MONTHS.search(plain, after)
        if not months:
            continue
        if name in found:
            seen_twice.add(name)
            continue
        low = months.group("low")
        high = months.group("high") or low
        found[name] = {
            "text": (f"{low}–{high} months" if months.group("high")
                     else f"{low} months"),
            "low": float(low),
            "high": float(high),
        }
    for name in seen_twice:
        found.pop(name, None)
        reasons.append(f"the {name} case's payback is stated twice in §11.2, "
                       f"so it is not read rather than chosen between.")
    return found, reasons


def unreadable_sections(text):
    """`[(label, reason)]` for sections that are PRESENT and yield nothing.

    THE DIFFERENCE BETWEEN SILENCE AND A MISREAD, which nothing distinguished
    until now. A document with no §6 at all and a document whose §6 is present
    but states no phase row produced the identical reason string, so template
    drift -- a renamed column, an empty draft table, a heading that stopped
    matching -- degraded exactly like a PRD that simply never had a plan.

    That matters because of what changed around it. With the paper out of the
    chain there is nothing behind a PRD any more, so a section quietly reading
    as empty is the whole of what the deck knows about that fact. The 24-week
    deck happened because a plan came from the wrong place; this is the same
    failure with the plan coming from nowhere, and it should be as loud.

    ONLY SECTIONS WITH NOTHING BEHIND THEM ARE LISTED, which is what keeps this
    from refusing runs it should not. §6's phases and Appendix A's baseline are
    ROSTER fields with no fallback once the paper is gone. §7, §14 and §3.1 all
    have a second pass reading the same document and a deck standard behind
    that, so one of those reading empty degrades rather than blinds, and a
    reader that refused over it would block a thin PRD that can still produce
    an honest deck.

    Anchor-present is judged by the section returning text, never by the reader
    returning nothing, which is the whole point.
    """
    found = []
    if section(text, 6).strip() and not phase_records(text):
        found.append((
            "section 6",
            "its Phased Scope is present but states no phase with a week span, "
            "so this document's plan could not be read. A renamed column or an "
            "empty draft table reads exactly like this."))
    if _BASE_BLOCK.search(text or "") and not baseline(text):
        found.append((
            "appendix A",
            "its “Base (period).” sentence is present but states no "
            "revenue, EBITDA or margin figure, so the baseline could not be "
            "read."))
    # The cost table has nothing behind it either: no second pass may write a
    # QofAI cost (a paper's vendor quotes would be the obvious wrong answer), so
    # a §11.2 cost table that is present and unreadable blinds slide 5's
    # INVESTMENT block rather than degrading it.
    table, _body = _cost_table(text)
    if table is not None and cost_total(text) is None:
        found.append((
            "section 11.2",
            "its cost table (headed “Item”) is present but has no single "
            "row beginning “Total” or “Indicative total” that states a "
            "cost, so what the build costs could not be read. The line items "
            "are not summed, because a sum is a figure the PRD does not state."))
    return found
