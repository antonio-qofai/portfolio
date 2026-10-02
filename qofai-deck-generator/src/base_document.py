"""The base document rule (item 14, step 2): which document a deck is written
from, and what the others still contribute.

A proposal is written from the published opportunity paper. When a reviewer
attaches a document, that document becomes the BASE and the paper drops to a
SUPPORTING source: the base wins every field it carries, and the paper answers
only what the base does not. Casey's reason, from the 2026-09-04 walkthrough, is
that a PRD carries execution detail, pricing, timelines and deliverable dates at
a specificity the paper does not, while the paper is the one document that always
exists.

THE RULE IS ABOUT ORDER, AND THAT IS THE WHOLE RULE. `precedence` returns the
documents in the order their answers win, and the published paper is always last.
That generalises without a second rule: no attachment leaves a chain of one and a
pipeline that behaves exactly as it did, one attachment puts the upload ahead of
the paper, and several attachments win in the order the reviewer attached them.
Nothing here reads a client, a project, a filename or a content type to decide
anything. A document's position is decided by whether it was attached and when,
which is a fact about the request rather than a fact about any company.

WHERE THIS LIVES AND WHERE IT DOES NOT. `precedence` and `merge_packets` are the
only place the rule is expressed. `live_proposal_provider._assemble` calls them
and hands the base's TEXT to the extraction legs, which is why nothing downstream
of that call learns that a document was ever attached: the passes read text and
the packet holds figures, exactly as before.

WHAT MERGING IS AND IS NOT. `merge_packets` folds the deterministic packets, one
per source, in precedence order. Each figure keeps the `SourcedFigure` it arrived
as, so it keeps the span and the DOCUMENT of its own source, and no figure is
ever restated against text it did not come from.

AND WHAT THE REVIEWER IS SHOWN, IN TWO LISTS THAT ARE NOT THE SAME LIST.

`Packet.conflicts` is the fields more than one source answered where the answers
differ. Not the fields the base simply won, which is every field the base
carries and is no news to anybody: a conflict is a real disagreement between two
documents about the same thing. A reviewer reading one has a DECISION to make.

`Packet.set_aside` is the figures a source stated that the merge declined to
take, under a rule about a unit larger than the field. Only one document
answered, so there is nothing to choose and it is not a conflict. A reviewer
reading one has only something to KNOW, and they do need to know it: without
this list a reviewer attaches a PRD that prices the work and restates no margin,
sees EBITDA reported missing, and types in by hand the figure the published
paper was holding the whole time.

Both are reviewer-facing only. Nothing in either reaches a rendered deck,
because a source annotation does not belong in front of a client, and the studio
that shows them is a separate step from the one that produces them.
"""

import dataclasses
import logging

import source_span
from packet_assembly import EBITDA, MARGIN, REVENUE, TIMELINE
from source_span import Answer, Conflict, Document, SetAside

_LOG = logging.getLogger(__name__)

# What the published opportunity paper is called as a source. Not a filename and
# not a type: the paper arrives from the platform rather than from a file, and it
# is the one source in a chain that is never uploaded. Defined in `source_span`
# beside the primitive that carries it, so a figure's document and a source's
# kind cannot drift apart.
PAPER_KIND = source_span.PAPER_KIND


# The scenario table as one name, which is what it already is to the extraction
# pass (`second_pass.SCENARIOS`) and to the fill map.
SCENARIOS = "commercial.scenarios"

# Which rule declined a figure, carried on a `SetAside` so the studio can say
# why without matching on a field name. The name exists so the next unit larger
# than a field would not need a second record type. Item 15 added a timeline
# rule here and 2026-09-22 took it out again; see `SHARED_UNITS`.
BASELINE_GROUP_RULE = "baseline_group"


@dataclasses.dataclass(frozen=True)
class Unit:
    """A unit LARGER THAN A FIELD: names that move together, from one lead.

    The generalisation of the baseline group rule, which was right and was the
    only one of its kind for six days. A unit is the set of names a reader takes
    as one statement because the deck prints them as one, so the highest-
    precedence source carrying ANY of them supplies ALL of them, and a name that
    source does not state stays absent rather than being filled from below.

    `labels` are packet members that describe the unit rather than belonging to
    it (the baseline's period and basis), so they come from the same lead by
    construction rather than by a second rule that could disagree with the
    first. That disagreement is exactly what went wrong on 2026-09-07: deciding
    where the LABEL came from and leaving the FIGURES free to come from anywhere
    printed three numbers whose own arithmetic contradicted them.

    `rule` is what a `SetAside` carries, so the studio can say why a figure was
    declined without matching on a field name.
    """

    rule: str
    fields: tuple
    labels: tuple = ()


# THE UNITS THE MERGE ITSELF ENFORCES, which is one, and the reason it is one is
# worth writing down rather than discovering. A unit rule only has anything to
# do when a unit spans SEVERAL names: it is what stops a source supplying some
# of them while a higher-precedence source supplied the rest. The baseline is
# three figures over one period and is exactly that.
#
# `timeline.phases` is deliberately NOT here. It is ONE field holding a whole
# phase list, built from one document's chart, so it is taken whole by
# construction and was before units existed -- the 2026-09-07 entry says so in
# as many words, and it is still true. Adding it here would be a rule that can
# never decline anything, because a one-name unit's lead states its only name by
# definition. Two documents that both state a timeline disagree, and a
# disagreement is a `Conflict` rather than a `SetAside`.
UNITS = (
    Unit(rule=BASELINE_GROUP_RULE, fields=(REVENUE, EBITDA, MARGIN),
         labels=("baseline_period", "baseline_basis")),
)

# THE NAMES THE ENGAGEMENT OWNS rather than any one opportunity, which is a
# different question from the one above and has a different answer. The baseline
# is here because the company's trailing revenue, EBITDA and margin are facts
# about the COMPANY, the same whichever of its opportunities is being described.
#
# THE TIMELINE IS NOT, and it used to be. Item 15 shared it because the deck
# printed one phased plan for the engagement, and `apply_shared` then put the
# lead opportunity's schedule on every other opportunity's packet. On the first
# two-opportunity Contoso deck (2026-09-22) that printed the onboarding
# paper's four phases and "THE 44-WEEK BUILD" on the pipeline cockpit's slide,
# whose own paper states four different phases over 24 weeks. The timeline is
# a fact about one opportunity's build, and Antonio's call the same day is that
# every opportunity gets its own timeline slide ("makes no sense for two
# different opportunities to have the same timeline").
SHARED_UNITS = UNITS

# The three figures that share one period and one basis, read off the unit table
# so there is one statement of them rather than two that can drift.
_BASELINE_FIELDS = UNITS[0].fields

# The order the reviewer's conflict list reads in: the way the deck reads,
# rather than the order the documents happened to state things in.
_CONFLICT_ORDER = (REVENUE, EBITDA, MARGIN, SCENARIOS, TIMELINE)


@dataclasses.dataclass(frozen=True)
class Upload:
    """One attachment as it arrives, before anything has read it.

    `filename` and `data` and nothing else. A browser also sends a content type
    and this deliberately does not carry one: `document_text` decides what a
    file is from its extension and its opening bytes, so a declared type held
    here would be a field inviting someone to trust it.
    """

    filename: str
    data: bytes


@dataclasses.dataclass(frozen=True)
class Source:
    """One document in a precedence chain: its text, and how to name it.

    `kind` is a `document_text` kind for an attachment and `PAPER_KIND` for the
    published paper, so `uploaded` is a question about the source rather than a
    flag someone has to set consistently.

    `document` is the same source without its text: what a figure read out of
    here carries, so a span in a merged packet can say which document it belongs
    to (`source_span.Document`).
    """

    name: str
    kind: str
    text: str

    @property
    def uploaded(self):
        return self.kind != PAPER_KIND

    @property
    def document(self):
        return Document(name=self.name, kind=self.kind)


def paper_source(paper):
    """The published paper as a source. It has no filename, and it never had.

    A `None` paper becomes "". `fetch_opportunity_paper` returns None for an
    opportunity whose paper the platform withheld and a picked one goes straight
    to assembly, so this is a real state rather than a defensive default. It
    changes nothing: `packet_assembly.assemble` produces the identical packet
    from None and from "", down to the reason on every absence, and the model
    legs' span check reads a string either way instead of raising on None.
    """
    return Source(name="", kind=PAPER_KIND, text=paper or "")


def uploaded_source(document):
    """An `ExtractedDocument` from `document_text` as a source."""
    return Source(name=document.filename, kind=document.kind, text=document.text)


def _is_prd_chain(sources):
    """Whether the BASE document is a QofAI PRD, which is what excludes the paper.

    The base is the first attachment, the one whose answers win, so it is the
    one that decides whether this run is a PRD run. A PRD with a supporting
    spreadsheet behind it is still a PRD run; a spreadsheet with a PRD behind it
    is not, because the reviewer put the spreadsheet first.

    Imported here rather than at module scope to keep the import one-directional:
    `prd_section_parsers` is a reader and this is the chain it gets read into.
    """
    from prd_section_parsers import is_prd

    return bool(sources) and is_prd(sources[0].text)


def precedence(paper, documents=()):
    """The sources in the order their answers win, the paper always last.

    `documents` are `document_text.ExtractedDocument`s in the order the reviewer
    attached them; the first of them is the base. With none, the chain is the
    paper alone and every caller behaves as it did before this module existed.

    The paper joins the chain even when it is empty, because a picked
    opportunity carrying no paper is a real state (`_select` hands one straight
    to assembly) and the packet assembled from an empty paper is what the
    completeness gate refuses today. The one case it is dropped is a chain that
    already has an attachment: there, an empty paper would be a source with no
    text, contributing nothing and claiming a place in a list the reviewer is
    about to be shown.
    """
    sources = [uploaded_source(document) for document in documents or ()]
    if sources and _is_prd_chain(sources):
        # AN UPLOADED PRD IS THE ONLY SOURCE. Antonio, 2026-09-20: "I don't want
        # the sources to be merged. I don't even want the opportunity paper to
        # even be read if the PRD is uploaded." So the paper is not appended
        # here at all, and a run with a PRD is answered from that PRD or is
        # refused.
        #
        # GATED ON THE DOCUMENT ACTUALLY BEING A PRD, which the first cut of
        # this was not. A reviewer may attach a supporting note or a
        # spreadsheet, and item 14 has always let those join a chain that still
        # holds the paper. Dropping the paper for ANY attachment rewrote that
        # behaviour for every one of them and broke 30 tests that were right.
        #
        # This used to append the paper whenever it had any text, which made
        # every attached run a MERGE: the PRD won each field it could state and
        # the paper quietly filled the rest. That is how a deck built from a
        # ten-week PRD came to state a 24-week plan. The PRD had no readable
        # timeline, the paper did, and nothing said so.
        #
        # Measured 2026-09-20, this costs nothing today: with
        # `prd_section_parsers` in, the PRD yields 2 of the 6 roster fields
        # deterministically and the paper yields 1, which is the timeline the
        # PRD now states itself. The paper contributes no field the PRD does
        # not. What the rule buys is the guarantee going forward, so a thinner
        # PRD is REFUSED rather than silently completed from another document.
        return tuple(sources)
    # EVERY OTHER CHAIN IS EXACTLY THE ONE THAT SHIPPED. A non-PRD attachment
    # keeps its place at the front and the paper still joins behind it, which is
    # item 14's behaviour and is what the rule above must not disturb.
    #
    # SAID OUT LOUD IN THE LOG, because this branch is where a recognition
    # defect hides: an attachment the reviewer believed was a PRD falls through
    # to here and the paper rejoins without a word. That is exactly what
    # happened while `is_prd` was keyed on markdown headings, which only one
    # extraction path ever produced (the PRD-chain finding in this folder,
    # 2026-09-20). Logged
    # and not surfaced, matching the standing rule for a displaced value: the
    # reviewer attached a document and a merge is the normal outcome for one
    # that is not a PRD, so this is for whoever is reading the log after a deck
    # comes out wrong.
    if sources:
        _LOG.info(
            "attachment chain is not a PRD chain, so the opportunity paper "
            "joins it: base=%r", getattr(sources[0], "name", "?"))
    paper = paper_source(paper)
    if paper.text.strip() or not sources:
        sources.append(paper)
    return tuple(sources)


def engagement_precedence(papers, documents=()):
    """Every source in the RUN, uploads first and then each opportunity's paper.

    THE SECOND CHAIN, AND WHY A RUN NEEDS TWO (item 15, 2026-09-13).
    `precedence` above is one opportunity's chain and stays exactly as it was:
    the shared uploads, then that opportunity's OWN paper and no other. That is
    what makes "opportunity A's slide is never written from B's paper" a fact
    about how the chain is built rather than a check somewhere downstream.

    But three of the names in the roster are not facts about an opportunity at
    all. The company's trailing revenue, its adjusted EBITDA and its margin are
    facts about the COMPANY, the same whichever of its opportunities is being
    described, and the deck prints them once. Under per-opportunity chains alone
    a thin paper B would lose the baseline that paper A states about the very
    same company, which is the 2026-09-12 regression one axis over: a rule that
    was right about documents, applied to a level it was not written for.

    The timeline used to resolve here too and no longer does (2026-09-22): a
    schedule is a fact about one opportunity's build, and sharing it put a
    44-week plan on a 24-week build. See `SHARED_UNITS`.

    So the baseline resolves once, over every source in the run, and
    `apply_shared` below puts the answer on each opportunity's packet. The order
    is the same rule as ever: an attachment outranks a paper, and papers rank in
    the order their opportunities were asked for.

    ONE PRESENTATION LIMIT, NOT A CORRECTNESS ONE, AND IT IS NOTED RATHER THAN
    FIXED HERE. Every published paper is `Document(name="", kind=PAPER_KIND)`,
    so two papers in one chain are the same `Document`. Nothing keys on that and
    nothing collides: the deterministic cache keys on the paper TEXT beside the
    document, the extraction cache carries the opportunity too, and a figure
    names its opportunity, so which paper a figure came from is always
    recoverable. What is ambiguous is the reviewer-facing LABEL, which reads
    "the published opportunity paper" for both. Naming the document would move
    that label on every single-opportunity run too, and the studio already holds
    the opportunity beside the document, so this belongs to the report that
    composes the sentence rather than to the record.
    """
    sources = [uploaded_source(document) for document in documents or ()]
    if sources and _is_prd_chain(sources):
        # The same rule `precedence` states, at the engagement level: an
        # uploaded PRD is the only source, so no paper joins this chain either.
        # A PRD is written for the whole engagement and states one schedule and
        # one baseline for it, which is the shape this chain exists to resolve,
        # so completing it from a paper would reintroduce the merge one level up.
        return tuple(sources)
    for paper in papers or ():
        source = paper_source(paper)
        if source.text.strip() or not sources:
            sources.append(source)
    return tuple(sources)


def apply_shared(packet, engagement):
    """One opportunity's packet carrying the ENGAGEMENT's shared units.

    The transplant `engagement_precedence` exists for. `packet` is what this
    opportunity's own chain merged, so its scenarios and every other
    opportunity-specific figure, its timeline included, are its own and stay
    untouched; `engagement` is what the whole run's chain merged, and the
    baseline group comes from there.

    WHY A TRANSPLANT RATHER THAN A WIDER CHAIN. Letting every paper into every
    opportunity's chain and then restricting what each may answer would need a
    per-source field allowlist, which is machinery, and the restriction would be
    the real rule while the chain pretended otherwise. This says what it means:
    `SHARED_UNITS` belong to the engagement, and here is where they are put.

    A figure of this opportunity's own that the transplant displaces is RECORDED
    rather than dropped, under the same unit rule and in the same `set_aside`
    list the merge uses, because it is the same fact: a document stated a figure
    and the deck is not printing it. A figure the transplant replaces with an
    equal one is not recorded, since nothing was declined.

    `missing_fields` is recomputed rather than kept, for the reason
    `merge_packets` recomputes it: a name this opportunity left absent and the
    engagement answered is not missing, and a name absent after the transplant
    keeps the reason its own source gave it.

    `conflicts` is deliberately NOT merged in. A disagreement between two
    documents about the company's baseline is one fact about the run, not one
    fact per opportunity, and copying it onto every opportunity's packet would
    show a reviewer the same row as many times as the deck has slides. It stays
    on the engagement packet, which is where the report reads it.

    A run whose engagement chain IS this opportunity's chain passes through
    untouched, by identity, which is every single-opportunity run.
    """
    if engagement is None or engagement is packet:
        return packet
    by_field = {field: unit for unit in SHARED_UNITS for field in unit.fields}
    taken = {figure.field: figure for figure in engagement.fields
             if figure.field in by_field}
    declined = [
        _declined(figure, packet, engagement, by_field[figure.field])
        for figure in packet.fields
        if figure.field in by_field and taken.get(figure.field) != figure
    ]
    fields = tuple(
        figure for figure in packet.fields if figure.field not in by_field
    ) + tuple(taken.values())
    labels = {
        label: getattr(engagement, label)
        for unit in SHARED_UNITS for label in unit.labels
    }
    shared = dataclasses.replace(
        packet, fields=fields, missing_fields=(), **labels
    )
    answered = set(shared.present) | set(taken)
    return dataclasses.replace(
        shared,
        missing_fields=_absences((packet, engagement), answered),
        set_aside=_ordered(tuple(packet.set_aside) + tuple(declined)),
    )


def base(sources):
    """The document the deck is written from."""
    return sources[0]


def supporting(sources):
    """Every source behind the base, in the order they answer."""
    return tuple(sources[1:])


def merge_packets(packets):
    """One packet from many, the earliest source winning field by field.

    `packets` are the deterministic packets of a `precedence` chain, in the same
    order. A chain of one returns ITS OWN packet unchanged, by identity and not
    merely by value, so a run with no attachment carries the object the cached
    assembly returned and nothing about that path moves.

    Field by field, and the field is the unit because that is what the success
    criterion asks for: the figures come from the attachment wherever it carries
    them and from the paper wherever it does not.

    THE THREE EXCEPTIONS, EACH BECAUSE A UNIT LARGER THAN A FIELD IS AT STAKE.

    `scenarios` merge whole rather than case by case. A scenario table is one
    document's own set of cases and their arithmetic relates them; taking case
    one from an attachment and case two from a paper would state a table neither
    document states. The first source carrying any case supplies them all.

    THE BASELINE MOVES AS A GROUP, and this is the exception that was wrong for
    a day. The three figures and their period are one statement about one span
    of time, and a reader takes them as stated together because the deck prints
    them together. So the LEAD, the highest-precedence source carrying any of
    the three, supplies all three, and a figure the lead does not state stays
    ABSENT rather than being filled from a lower-precedence source.

    What that prevents, reproduced against the field-by-field version before it
    was replaced: an attachment stating CY 2025 revenue of $38.4M against a
    paper stating CY 2023 revenue, EBITDA and margin merged to a "CY 2025"
    baseline of $38.4M revenue, $3.0M EBITDA and a 25.0% margin. Three figures
    that read as one set, printed side by side on slide 2's strip, whose own
    arithmetic disagrees: $3.0M over $38.4M is 7.8 percent, and two of the three
    had never been stated for CY 2025 by anybody. Deciding only where the LABEL
    came from left the FIGURES free to come from anywhere, and the hazard is the
    pairing rather than the label.

    An absent figure is a missing field with a reason, which is what the rest of
    this repo does everywhere: the golden rule is that a figure with no source
    behind it is a missing field, never a value, and a figure paired with
    another document's period has no source behind it as a pair. The reason
    comes from the packet whose parsers recorded it. A base document that states
    the figure in PROSE rather than in a table still gets it read, because the
    second extraction pass reads the base for exactly the fields left absent.

    And every figure declined this way is written down, in `set_aside`. The rule
    is right and a reviewer left to discover it is not: a document did state
    that figure, and a reviewer who is not told will type it in by hand from the
    document that was holding it all along.

    `baseline_period` and `baseline_basis` are the lead's, and stay None when
    the lead states none. By construction they now label the lead's own figures
    and no others.

    `missing_fields` is recomputed rather than concatenated. A field the base
    left absent and a lower source answered is not missing, and a field absent
    after the merge keeps the reason its highest-precedence source gave it.

    `conflicts` and `set_aside` are what `_conflicts` and `_set_aside` below
    define, and they are the two members of the packet that exist for the
    reviewer rather than for the deck.
    """
    packets = tuple(packets)
    if not packets:
        raise ValueError("a packet merge needs at least one packet.")
    if len(packets) == 1:
        return packets[0]

    leads, refused = {}, {}
    for unit in UNITS:
        lead, others = take_whole(
            _unit_figures(packet, unit) for packet in packets
        )
        leads[unit.rule] = packets[lead]
        refused[unit.rule] = set(others)
    by_field = {field: unit for unit in UNITS for field in unit.fields}

    fields, taken, declined = [], set(), []
    for index, packet in enumerate(packets):
        for figure in packet.fields:
            if figure.field in taken:
                continue
            # The lines that make a unit a unit. A figure belonging to one
            # enters only from that unit's lead, so a name the lead does not
            # state is not offered to the source below it and falls through to
            # `_absences` with that source's reason. Recorded HERE, at the
            # moment it is declined, because this is the only point that knows
            # both what was declined and what took its place.
            unit = by_field.get(figure.field)
            if unit is not None and index in refused[unit.rule]:
                declined.append(_declined(figure, packet, leads[unit.rule], unit))
                continue
            taken.add(figure.field)
            fields.append(figure)

    scenarios = ()
    for packet in packets:
        if packet.scenarios:
            scenarios = packet.scenarios
            break

    merged = dataclasses.replace(
        packets[0],
        fields=tuple(fields),
        scenarios=scenarios,
        missing_fields=(),
        **_labels(leads),
    )
    # `present` reads the merged fields and scenarios, including the two roster
    # names a scenario table satisfies without a field of its own, so it is the
    # honest test of what is still absent and it can only be asked of the merged
    # packet. Which is why this is two steps rather than one.
    answered = set(merged.present) | taken
    return dataclasses.replace(
        merged,
        missing_fields=_absences(packets, answered),
        conflicts=_conflicts(packets),
        set_aside=_ordered(declined),
    )


def take_whole(stated):
    """WHICH SOURCE SUPPLIES A UNIT, AND WHICH ONES WERE DECLINED. One rule, once.

    `stated` is what each source, in precedence order, states for ONE unit: any
    truthy entry means that source states it, and what the entry actually IS is
    the caller's business. Returns `(lead, declined)` as INDEXES into `stated`.

    Pure, and index-based rather than packet-based, because the rule has two
    callers that hold different things. `merge_packets` below holds `Packet`s
    and their `SourcedFigure`s, and that is where the baseline group and the
    timeline are decided. The fill-level merge holds fill maps and their record
    lists, and that is where the platform components and the next steps are
    decided, because those are not packet fields at all. Both are the same rule
    and there is one statement of it here.

    Stating it twice is exactly how the baseline came apart for a day: one rule
    decided where the period came from, a second decided where the figures came
    from, and the two disagreed, printing three numbers whose own arithmetic
    contradicted them (2026-09-07). A rule with two implementations is a rule
    with two chances to be edited apart.

    The lead is index 0 when NOBODY states the unit, which is what keeps a unit
    no source answered behaving exactly as it did before units existed: nothing
    is declined, and every one of its names falls through to `_absences` with
    the reason its own parsers recorded.
    """
    stated = tuple(stated)
    lead = next((index for index, item in enumerate(stated) if item), 0)
    return lead, tuple(index for index, item in enumerate(stated)
                       if item and index != lead)


def _unit_figures(packet, unit):
    """What one packet states for one unit, which is what `take_whole` reads."""
    return tuple(figure for figure in packet.fields
                 if figure.field in unit.fields)


def _labels(leads):
    """Each unit's labels, taken from that unit's own lead.

    By construction rather than by a second rule, which is the whole lesson of
    2026-09-07: a rule that decided where the LABEL came from while leaving the
    FIGURES free to come from anywhere printed a set of three numbers whose own
    arithmetic contradicted them.
    """
    return {
        label: getattr(leads[unit.rule], label)
        for unit in UNITS
        for label in unit.labels
    }


def _conflicts(packets):
    """The fields more than one source answered where the answers differ.

    WHAT A CONFLICT IS. Two or more documents answered the same field, and they
    do not agree. A field only one document answered is not a conflict however
    the merge treated it, and neither is a field the base simply won by agreeing
    with everyone: the point of this list is that it is short and that every row
    on it needs a person.

    WHAT "DIFFER" MEANS, AND THE RANGE CASE. Values are compared with `==` and
    nothing else. Every baseline and scenario figure is a `(low, high)` pair in
    its field's unit (`packet_assembly`'s own representation), so that means both
    endpoints, and no tolerance is applied anywhere. Three reasons, and the
    third is the one that decides it.

      - A tolerance is a threshold nobody has measured, and this repo does not
        ship numbers nobody measured.
      - Two documents stating a figure to different precision, $38.4M against
        $38.42M, genuinely disagree about what it is. Which one a deck should
        print is a reviewer's call and not a rounding rule's, and showing them
        the pair costs one row.
      - A range and a point are not the same claim even when the point sits at
        the range's middle, and a wider range is not the same claim as a
        narrower one. `(1.0, 6.0)`, `(3.5, 3.5)` and `(1.0, 5.0)` are three
        different statements about the same field and a reviewer told they
        agreed would be told something false.

    THE SCENARIO TABLE IS ONE ROW, not one row per case, for the same reason it
    merges whole: its cases are one document's set and their arithmetic relates
    them. Its answers compare by the cases' labels and figures rather than by
    the `ScenarioCase` objects, which now carry documents and so could never
    compare equal across two sources.

    Ordered the way the deck reads rather than by which document happened to
    lead, so the reviewer's list does not reorder itself between runs.
    """
    given = {}
    for packet in packets:
        for figure in packet.fields:
            given.setdefault(figure.field, []).append(
                Answer(document=figure.document, value=figure.value,
                       span=figure.span)
            )
        if packet.scenarios:
            given.setdefault(SCENARIOS, []).append(_scenario_answer(packet.scenarios))
    conflicts = [
        Conflict(field=field, answers=tuple(answers))
        for field, answers in given.items()
        if len(answers) > 1
        and any(answer.value != answers[0].value for answer in answers[1:])
    ]
    return _ordered(conflicts)


def _declined(figure, packet, lead, unit):
    """One figure a unit rule would not take, and what took its place: the
    document leading the unit and, where the unit has a period, the period each
    belongs to, which is the fact that makes the two incompatible.

    A unit with no period leaves both as None, which `SetAside` already allows
    in so many words: "Both periods are None for a rule where a period is not
    what distinguishes them." The timeline is that rule. Two documents each
    stating their own schedule are not incompatible because they cover different
    spans of time; they are incompatible because an engagement runs one plan.
    """
    period = ("baseline_period" in unit.labels)
    return SetAside(
        field=figure.field,
        answer=Answer(document=figure.document, value=figure.value,
                      span=figure.span),
        rule=unit.rule,
        # The lead's own document FOR THIS UNIT rather than whatever its first
        # field happens to be. A packet holds one document's figures, so the two
        # agree today, and asking for the unit's says what is meant.
        lead=next((other.document for other in lead.fields
                   if other.field in unit.fields), None),
        lead_period=lead.baseline_period if period else None,
        period=packet.baseline_period if period else None,
    )


def _ordered(records):
    """The reviewer's lists read the way the deck reads, and within one field in
    precedence order, which `sorted` keeps because it is stable."""
    return tuple(sorted(records, key=lambda record: _field_order(record.field)))


def _scenario_answer(cases):
    """One document's whole scenario table as a single answer.

    The value is the cases' labels and figures, which is what two tables can be
    compared on. The span is every row the table stated, joined, because a table
    is several rows and no one of them is the answer.
    """
    spans, seen = [], set()
    for case in cases:
        for figure in (case.direct_uplift_usd_yr, case.margin_gain_pp):
            if figure is not None and figure.span not in seen:
                seen.add(figure.span)
                spans.append(figure.span)
    return Answer(
        document=cases[0].direct_uplift_usd_yr.document,
        value=tuple(
            (case.label,
             case.direct_uplift_usd_yr.value,
             case.margin_gain_pp.value if case.margin_gain_pp else None)
            for case in cases
        ),
        span="\n".join(spans),
    )


def _field_order(field):
    try:
        return (0, _CONFLICT_ORDER.index(field))
    except ValueError:
        return (1, field)


def _absences(packets, answered):
    entries, named = [], set()
    for packet in packets:
        for name, reason in packet.missing_fields:
            if name in answered or name in named:
                continue
            named.add(name)
            entries.append((name, reason))
    return tuple(entries)
