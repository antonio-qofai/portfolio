"""What a PRD says about itself, read from its own front matter.

Why this exists. Antonio, 2026-09-20: "if I upload a PRD I don't want to also
have to select a company and an opportunity for speed reasons, that information
is already in the PRD." It is. Every document in the current corpus opens with a
labelled block stating who it was prepared for and, in most cases, which
opportunity it is about, and this module reads that block.

WHY A REGEX AND NOT A MODEL. Reading a client name out of a document is the
obvious thing to hand a model, and the corpus makes it unnecessary: the values
sit in labelled rows. It is also the one place in this build where a model would
be reading an UPLOADED document and steering a platform lookup with what it
found, which is the shape of the robustness problem recorded in
`prompt-injection-unresolved-FINDING.md`. A labelled row cannot be talked into
naming a different company. Nothing here is inferred: a document that states no
opportunity yields no opportunity, and the reviewer picks one.

THE TWO SHAPES, and both are in the corpus. A .docx renders the block as
markdown table rows, because item 25 taught `document_text` to keep Word's
tables (`| Prepared for | Contoso |`). A PDF has no styles, so `pypdf`
returns the label and the value separated by whitespace (`PREPARED FOR
Fabrikam Marine & Woodgrove Partners`). Both parse here.

WHAT COMES BACK IS CANDIDATES, NOT AN ANSWER. The client row can name a client
and its PE firm joined by an ampersand, and the firm may be a registry record in
its own right, so this returns the client names to TRY, in the order to try
them, and the caller resolves. The subject line ("for Fabrikam Marine
marine works, a Woodgrove Partners portfolio company") is read too, because it
states the same fact a second time and states it more precisely.

Nothing in this module names a client, a firm, a project or an opportunity.
"""

import re

# How far in the block can be. Every document in the corpus states it inside the
# first dozen non-empty lines; sixty is slack for a cover page, a table of
# contents line or a repeated header, and it stops this from reading a whole
# 14-page document looking for a row that is not there.
SCAN_LINES = 60

# The labels, lower-cased and stripped of punctuation before matching, so
# "PREPARED FOR", "Prepared for" and "Prepared For:" are one label.
CLIENT_LABELS = ("prepared for",)
OPPORTUNITY_LABELS = ("opportunity",)

# "for <client>" as its own line, under the title. A second statement of the
# client, and the only one that survives the ampersand problem intact.
_SUBJECT = re.compile(r"^for\s+(.+)$", re.IGNORECASE)

# "<client>, a <firm> portfolio company" — the subject line's own shape, which
# says which side of it is the client.
_PORTFOLIO_OF = re.compile(r"^(.*?),\s*an?\s+(.+?)\s+portfolio\s+company\.?$",
                           re.IGNORECASE)

# A parenthetical the opportunity row carries: the stage, and sometimes a date.
# Kept separately rather than dropped, because "not yet published" is the one
# note that decides whether the opportunity can be selected at all.
_TRAILING_NOTE = re.compile(r"\s*\(([^()]*)\)\s*$")

# The stage words that mean the platform will not list it. `list_opportunities`
# asks for `stage="published"`, so anything else is not selectable, and saying so
# is the difference between a branch that stops and one that quietly picks a
# different opportunity.
_UNPUBLISHED = ("not yet published", "unpublished", "validated", "draft")


def _clean(text):
    return " ".join((text or "").split())


def _label_of(text):
    """A row's label, normalised, or "" when the line carries none."""
    return _clean(text).rstrip(":").lower()


def _rows(lines):
    """Every ``(label, value)`` the block states, in both of the two shapes."""
    found = []
    for line in lines:
        stripped = _clean(line)
        if not stripped:
            continue
        if stripped.startswith("|"):
            # A markdown table row: | Label | Value |. A separator row
            # (| --- | --- |) has no value worth reading and falls out here
            # because its cells are dashes, which no label matches.
            cells = [_clean(cell) for cell in stripped.strip("|").split("|")]
            if len(cells) >= 2:
                found.append((_label_of(cells[0]), cells[1]))
            continue
        # A PDF row: the label in caps, then whitespace, then the value. Matched
        # against the known labels rather than by splitting on whitespace, so an
        # ordinary sentence starting with the same words is not read as a row.
        for label in CLIENT_LABELS + OPPORTUNITY_LABELS:
            if stripped.lower().startswith(label):
                value = stripped[len(label):].lstrip(" :\t")
                if value:
                    found.append((label, value))
                break
    return found


def _client_candidates(stated, subject):
    """The client names to try against the registry, best first.

    The order is the one piece of care here. A stated row can join the client
    and the PE firm with an ampersand and the firm may be its own registry
    record, so a naive search can resolve the wrong one. The subject line states
    which side is the client when it uses the portfolio-company form, so it goes
    first; an abbreviation in brackets goes in as its own candidate, because a
    registry record may carry either form.
    """
    candidates = []

    def add(value):
        value = _clean(value)
        if value and value not in candidates:
            candidates.append(value)

    if subject:
        portfolio = _PORTFOLIO_OF.match(subject)
        add(portfolio.group(1) if portfolio else subject)

    for value in (stated or ""), :
        if not value:
            continue
        # "Tailspin Industrial Group (TIG)" is two names for one company, and the
        # registry may hold either, so both are tried.
        without_brackets = re.sub(r"\s*\([^()]*\)\s*$", "", value)
        bracketed = re.search(r"\(([^()]*)\)\s*$", value)
        add(without_brackets)
        if bracketed:
            add(bracketed.group(1))
        # The ampersand split is LAST, because it is the shape that cannot say
        # which side is the client.
        if "&" in without_brackets:
            for part in without_brackets.split("&"):
                add(part)
    return candidates


def read(text):
    """What this document states about itself.

    Returns a dict with:

    ``client_candidates`` — names to try against the registry, best first.
    ``client_stated``     — the client row exactly as written, for the screen.
    ``opportunity``       — the stated opportunity title, note removed.
    ``opportunity_note``  — the parenthetical, e.g. "published Sep 4, 2026".
    ``opportunity_selectable`` — False when that note says it is not published.
    ``subject``           — the "for <client>" line, as written.

    Every key is present on every call. A document that states nothing yields
    empty values rather than absent ones, so a caller reads the same shape
    whatever it was handed.
    """
    lines = [line for line in (text or "").splitlines() if line.strip()][:SCAN_LINES]
    rows = _rows(lines)

    client_stated = ""
    opportunity_raw = ""
    for label, value in rows:
        if not client_stated and label in CLIENT_LABELS:
            client_stated = value
        elif not opportunity_raw and label in OPPORTUNITY_LABELS:
            opportunity_raw = value

    subject = ""
    for line in lines:
        match = _SUBJECT.match(_clean(line))
        if match:
            subject = match.group(1)
            break

    note = ""
    opportunity = _clean(opportunity_raw)
    trailing = _TRAILING_NOTE.search(opportunity)
    if trailing:
        note = _clean(trailing.group(1))
        opportunity = _clean(opportunity[: trailing.start()])

    selectable = True
    if note:
        lowered = note.lower()
        selectable = not any(word in lowered for word in _UNPUBLISHED)

    return {
        "client_candidates": _client_candidates(client_stated, subject),
        "client_stated": _clean(client_stated),
        "opportunity": opportunity,
        "opportunity_note": note,
        "opportunity_selectable": bool(opportunity) and selectable,
        "subject": _clean(subject),
    }


# Words that carry no meaning about WHICH opportunity a title names. Dropped
# from both sides before matching, so "Quoting & Margin Modeling" and
# "Quoting and Margin Modeling" are the same title, which they are.
CONNECTORS = frozenset({
    "and", "the", "of", "for", "on", "in", "to", "with", "an", "at", "by",
})


def _content_words(text):
    """A title reduced to the words that say which opportunity it is.

    Words rather than characters, which is the point: a character substring test
    reads "Quoting" as a match inside "Requoting Automation", and a title that
    swallows a shorter unrelated one matches it too. Single characters are
    dropped as noise, the same rule `live_proposal_provider._title_words`
    applies when it ranks these labels for a human.
    """
    normalised = (text or "").replace("&", " and ")
    words = [w for w in re.findall(r"[a-z0-9]+", normalised.lower()) if len(w) > 1]
    return {w for w in words if w not in CONNECTORS}


def match_opportunity(stated_title, choices):
    """The ONE listed opportunity a PRD's stated title names, or None.

    ``choices`` are the platform's published opportunities, each a dict with an
    ``id`` and a ``label``. Returns the matching choice, or None when none match
    or when more than one does.

    EXACTLY ONE, AND THIS IS THE WHOLE RULE. An earlier version of this took the
    first hit, which meant that where two labels both matched, the one published
    earliest won by accident of ordering — `rank_choices` leaves the order at
    `published_at` when no project name is given, so the tie was being broken by
    a date nobody was thinking about. That is the posture `resolve_company`
    refuses four lines away in its own module: more than one match is an outcome
    to report, not something to pick around. The stake here is higher than it
    looks, because the opportunity supplies the research paper: with a PRD as the
    base document a reviewer reads the PRD's own prose on the slides while the
    figures come from the wrong paper, which is the least visible kind of wrong.

    Containment in either direction is allowed, because a PRD may name a shorter
    or a longer form of the platform's label, and it sits BEHIND the uniqueness
    gate rather than in front of it. As of 2026-09-20 no measured case needs it:
    Contoso's title is verbatim, TIG's cannot be measured because it is
    unpublished, and FBK states none.
    """
    exact, loose = _tiered_matches(stated_title, choices)
    # AN EXACT MATCH OUTRANKS A CONTAINMENT ONE, and the gate applies inside
    # each tier rather than across both. Without this a company holding both
    # "Cockpit" and "Pipeline Cockpit for Growth" makes a PRD
    # naming the second one ambiguous, because the first is contained in it —
    # and it is not ambiguous at all: one of the two is the title the document
    # wrote down.
    if len(exact) == 1:
        return exact[0]
    if exact:
        return None
    return loose[0] if len(loose) == 1 else None


def opportunity_matches(stated_title, choices):
    """Every listed opportunity the stated title could name.

    The same rule as :func:`match_opportunity` without the gate, so a caller can
    say how many matched and name them rather than only that it could not
    choose. A reviewer told "two of these match, here they are" can pick in one
    click; one told "no match" goes looking through the whole list.
    """
    exact, loose = _tiered_matches(stated_title, choices)
    # The exact tier alone when it has anything in it, for the same reason
    # `match_opportunity` prefers it: naming the looser matches beside an exact
    # one would ask a reviewer to choose between a title their document states
    # and a title that merely overlaps it.
    return exact or loose


def _tiered_matches(stated_title, choices):
    """``(exact, loose)``: labels whose content words equal the stated title's,
    and labels that contain it or are contained by it."""
    wanted = _content_words(stated_title)
    if not wanted:
        return [], []
    exact, loose = [], []
    for choice in choices or ():
        listed = _content_words(choice.get("label"))
        if not listed:
            continue
        if listed == wanted:
            exact.append(choice)
        elif wanted <= listed or listed <= wanted:
            loose.append(choice)
    return exact, loose
