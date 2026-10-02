"""The PACKET-to-PRD boundary: does the packet contradict the document it came from.

WHY THIS EXISTS, AND WHY NO EXISTING GUARD COULD HAVE CAUGHT IT. There are three
guards on the way to a deck and all three look the same direction:

    coverage_guard      packet  -> prompt      (is every role filled)
    render_guard        prompt  -> HTML        (did every value survive)
    layout_guard        HTML    -> PIXELS      (is every value visible)

Every one of them compares the deck to the PACKET. None of them compares the
packet to the DOCUMENT a reviewer uploaded. So on 2026-09-20 a deck built from a
ten-week PRD stated a 24-week plan in four phases, and all three guards passed:
the packet said 24 weeks, the prompt said 24 weeks, the HTML said 24 weeks, and
every value was present and visible. The deck was internally perfect and
externally wrong, and the only thing that found it was Antonio reading it.

This guard closes that direction. It re-reads the PRD and asserts the packet does
not contradict it on the facts that carry the engagement.

INDEPENDENT OF THE PARSERS ON PURPOSE, and this is the whole design. A guard that
re-used `prd_section_parsers` would agree with it by construction and would have
passed the very bug it exists for. So every check here reads a DIFFERENT part of
the PRD than the parser that produced the value it is checking:

    fact              parser reads              this guard reads
    duration          §6's table cells          §6's prose sentence
    milestone count   §6's phase rows           §12's milestone table
    impact range      Appendix A's table        §3.2's KPI table

A QofAI PRD states each of these twice, in different words, in different
sections. That redundancy is what makes an independent check possible at all,
and it is why a disagreement is worth stopping a run over: two statements that
agree across the whole corpus disagreeing now means something misread one.

SILENCE IS NOT A CONTRADICTION. A PRD that does not state a fact in the place
this guard looks yields no finding for it. The guard reports what it CHECKED as
well as what it found, so "clean" and "not measured" are never confused, which
is the mistake `layout_guard` already learned not to make.
"""

import re

# Number words the corpus writes durations in. "The build is a fixed ten-week
# engagement" is the Contoso PRD; the TIG PRD writes "a focused 9-week
# program". Both shapes are live, so both are read.
_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "twenty-four": 24, "thirty": 30,
}

_DURATION = re.compile(
    r"\b(?P<n>\d+|" + "|".join(sorted(_WORDS, key=len, reverse=True)) + r")[\s-]week\b",
    re.IGNORECASE)

_SEPARATOR = re.compile(r"^\s*\|[\s|:-]+\|\s*$")

# A pp range: "+0.50 to +2.50 pts", "+1.18 to +2.96 pts", "(+2.5 to +4.5 pts)".
_PP_RANGE = re.compile(
    r"([+-]?\d+(?:\.\d+)?)\s*(?:to|–|—|-)\s*([+-]?\d+(?:\.\d+)?)\s*"
    r"(?:pp\b|pts?\b|percentage[\s-]?points?\b)", re.IGNORECASE)

# How far two readings of the same number may differ and still agree. A PRD may
# write a margin as "+0.50" in one section and "+0.5" in another, and one
# decimal place of formatting is not a contradiction. Anything larger is.
TOLERANCE = 0.011


def _section(text, number):
    """A numbered section's body, ended by the next heading of equal or
    shallower depth. Deliberately a private copy rather than an import of
    `prd_section_parsers.section`: the independence this guard relies on is
    worth twenty lines of duplication, and a shared helper is one edit away
    from making the guard agree with the parser it checks."""
    opening = re.compile(
        r"^(?P<hashes>#{1,6})[ \t]*" + re.escape(str(number)) + r"\.[ \t]",
        re.MULTILINE)
    found = opening.search(text or "")
    if not found:
        return ""
    rest = (text or "")[found.end():]
    closing = re.search(r"^#{1,%d}[ \t]" % len(found.group("hashes")), rest,
                        re.MULTILINE)
    return rest[:closing.start()] if closing else rest


def _prose(body):
    """A section's text before its first table, which is where a PRD states its
    duration in words."""
    return body.split("\n|")[0]


def stated_duration(text):
    """The build length §6's PROSE states, in weeks, or None.

    Read from the sentence, never from the table, because the table is what the
    parser reads. "The build is a fixed ten-week engagement" yields 10.
    """
    found = _DURATION.search(_prose(_section(text, 6)))
    if not found:
        return None
    token = found.group("n").lower()
    return float(_WORDS[token]) if token in _WORDS else float(token)


def stated_milestones(text):
    """How many milestones §12's table lists, or None.

    Counts data rows, so a template that renames its columns still counts. The
    header is dropped by position and the separator by shape, which is the same
    rule `prd_section_parsers._rows` uses and the only thing the two share.
    """
    rows = [line for line in _section(text, 12).split("\n")
            if line.strip().startswith("|") and not _SEPARATOR.match(line)]
    return len(rows) - 1 if len(rows) > 1 else None


def stated_impact(text):
    """The EBITDA margin range §3.2's KPI table states, as `(low, high)`, or None.

    Independent of Appendix A's scenario table, which is what
    `prd_section_parsers.scenario_margins` reads. The row must name both EBITDA
    and margin, which is `scenario_table_parser.is_margin_label`'s rule and is
    applied here strictly because a KPI table holds many rows.
    """
    for line in _section(text, 3).split("\n"):
        if not line.strip().startswith("|"):
            continue
        label = line.strip().strip("|").split("|")[0].lower()
        if "ebitda" not in label or "margin" not in label:
            continue
        found = _PP_RANGE.search(line)
        if found:
            return (float(found.group(1)), float(found.group(2)))
    return None


def _phases(packet):
    for figure in getattr(packet, "fields", ()) or ():
        if figure.field == "timeline.phases":
            return figure.value or []
    return []


def _packet_margins(packet):
    values = []
    for case in getattr(packet, "scenarios", ()) or ():
        figure = getattr(case, "margin_gain_pp", None)
        if figure is None:
            continue
        value = figure.value
        values.extend(float(end) for end in
                      (value if isinstance(value, (tuple, list)) else (value,)))
    return values


def check_prd_loyalty(text, packet):
    """`{checked, ok, findings}` comparing a packet to the PRD it was built from.

    `checked` names the facts this PRD stated in the places looked at, so a
    caller can tell a clean result from an unmeasured one. `findings` carries
    BOTH values for every contradiction, because "the deck says 24 and the
    document says 10" is the whole of what a reviewer needs and a guard that
    reported only the disagreement would send them back to the document.

    Never raises on a document it cannot read. A paper, a spreadsheet or a PRD
    whose sections are missing yields `checked: []` and `ok: True`, because a
    guard that refused everything it could not measure would refuse every run
    that has no PRD at all.
    """
    findings, checked = [], []

    duration = stated_duration(text)
    phases = _phases(packet)
    if duration is not None and phases:
        checked.append("duration")
        horizon = max(phase.get("end") or 0 for phase in phases)
        if abs(horizon - duration) > TOLERANCE:
            findings.append({
                "fact": "build duration",
                "packet": f"{horizon:g} weeks",
                "document": f"{duration:g} weeks",
                "where": "section 6's own sentence",
                "message": (f"the packet's plan runs {horizon:g} weeks and this "
                            f"document states a {duration:g}-week build."),
            })

    milestones = stated_milestones(text)
    if milestones is not None and phases:
        checked.append("milestone count")
        # One milestone per phase boundary is `packet_fill._milestones`' rule,
        # so the packet's milestone count IS its phase count.
        if len(phases) != milestones:
            findings.append({
                "fact": "milestone count",
                "packet": f"{len(phases)}",
                "document": f"{milestones}",
                "where": "section 12's milestone table",
                "message": (f"the packet carries {len(phases)} phases, so "
                            f"{len(phases)} milestones, and section 12 lists "
                            f"{milestones}."),
            })

    impact = stated_impact(text)
    margins = _packet_margins(packet)
    if impact is not None and margins:
        checked.append("impact range")
        low, high = min(margins), max(margins)
        if (abs(low - min(impact)) > TOLERANCE
                or abs(high - max(impact)) > TOLERANCE):
            findings.append({
                "fact": "EBITDA margin impact",
                "packet": f"{low:g} to {high:g}pp",
                "document": f"{min(impact):g} to {max(impact):g}pp",
                "where": "section 3.2's KPI table",
                "message": (f"the packet states {low:g} to {high:g}pp of margin "
                            f"impact and this document states "
                            f"{min(impact):g} to {max(impact):g}pp."),
            })

    return {"checked": checked, "ok": not findings, "findings": findings}
