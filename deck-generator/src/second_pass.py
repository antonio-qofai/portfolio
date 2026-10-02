"""Precedence and merge for the second extraction pass (E11 Stage 2).

`paper_extraction` is the model. This is the policy, and the policy is one
sentence: THE DETERMINISTIC PASS ALWAYS WINS.

The LLM pass may only fill a packet field the parsers left ABSENT. It may never
overwrite, correct, refine, re-round or re-word a value a parser already sourced.
That is implemented as a structural property rather than as a convention someone
has to remember, and it is enforced TWICE at two different moments:

  * `absent` composes the request. The extractor is told only the paths the
    deterministic pass left empty, and `paper_extraction.extract` discards
    anything handed back for a path it did not ask about.
  * `merge_packet` and `merge_fill` check again on the way in, against the packet
    and the fill map as they actually stand at merge time. A returned field that
    is present is dropped and recorded as a disagreement, never merged.

The second check is not redundant with the first. Between them the packet is
rebuilt: filling a roster field changes what `packet_fill.fill_map` derives, so a
path absent when the request was composed can be filled by the time the answer
comes back. Only the second check sees that.

A disagreement is never a silent preference. Every path where both passes
produced a value is recorded with BOTH values, the parser's stands, and the pair
is carried into packet section 8 where a reviewer reads it. It is carried in a
`second_pass` block of its own and deliberately NOT in `gaps`: `gaps` is what
`data_source_adapter._gap_flagged_roles` turns into the deck's "unconfirmed"
marker, and a field two passes agree exists is not a gap.

What this module does NOT do. It does not touch `ROSTER`, which stays at six
fields: filling one of the six from prose raises `data_completeness` legitimately,
while adding a seventh would move the denominator, drop a live packet to roughly
0.15 and mean no live deck ever renders again (`E9-RESCOPE-DESIGN.md` section
3.4). It does not convert units. It does not modify a guard. It does not
second-guess the extractor's reading with heuristics: where a current-state
roster figure was read under a scenario, projection, benchmark, competitor or
vendor heading, it is FLAGGED for the reviewer, not dropped and not corrected.

Degradation, stated rather than hidden. This pass is additive: everything the
deterministic pass produced is already in hand before it runs. So an extractor
that raises -- no API key, an outage, a truncated answer -- records the failure
in section 8 and leaves the deterministic packet standing, rather than sinking a
deck the first pass could already render. The gate reads the deterministic
roster either way, so a failed second pass cannot let a thin packet through.

Not hardcoded to any client. Every path here is a schema path and every value
arrives from the paper through `paper_extraction`.
"""

import dataclasses
import logging

import packet_assembly
import packet_fill
import paper_extraction
import source_span
import paper_writing
from packet_assembly import ScenarioCase
from source_span import SourcedFigure

# Where a swallowed pass failure is written down. Both passes here catch every
# exception by design and hand back a degraded-but-renderable result, which is
# correct, and until 2026-09-02 that left NO trace anywhere: not the studio, not
# the server output, not a log line. A reviewer reading "the packet scores 0.33"
# had no way to learn that a model call had timed out, and the operator running
# the studio had no way either. The degradation stays; the silence does not.
LOGGER = logging.getLogger("deck.second_pass")

# Where a second-pass value came from, for section 8's `sources` ledger, so a
# reviewer can tell a parser's answer from a model's reading of the same paper.
SECOND_PASS = "get_opportunity_details.research_paper_natural (second pass)"

# Where a GENERATED sentence came from, and it says GENERATED in the ledger
# rather than leaving a reader to infer it from the path. An extracted value is
# a quotation the paper states; a generated sentence is one the model wrote from
# named sections of the paper. Section 8 must never let the two be confused,
# which is why they carry different origins and sit in different blocks.
GENERATED = "written by the second pass from named sections (GENERATED, not quoted)"

# The roster paths whose MEANING is current-state, and the only ones a section
# heading can be wrong for in a way worth flagging. A scenario legitimately comes
# from `Projected Impact` and a plan legitimately comes from `Timeline`, so
# flagging those would produce a flag on every run, and a flag that always fires
# is a flag a reviewer stops reading.
CURRENT_STATE = (packet_assembly.REVENUE, packet_assembly.EBITDA,
                 packet_assembly.MARGIN)

# Headings that describe something other than the company as it is today. Matched
# case-insensitively as substrings of the section path the extractor recorded.
NOT_CURRENT_STATE = ("scenario", "projected", "projection", "forecast",
                     "benchmark", "competitor", "competitive", "vendor",
                     "budget", "appendix")

# The fill-map paths this pass may fill, and the shape each carries. A record
# path is supplied whole; `packet_document._is_absent` reads the first record, so
# a partially-filled list still names its own unfilled leaves in section 8 gaps.
# The two record lists that were TEMPLATED deck standards until 2026-08-19 and
# are paper-sourced where the paper carries them. That reclassification is the
# human decision `packet_document._check_templated_is_closed`'s own comment
# defers to, and Antonio made it on 2026-08-18 when he asked for the whole deck
# to be filled from the source. The deck standard stays the FALLBACK
# (`packet_fill.apply_templated_defaults`), so a paper that describes no
# platform and states no next steps still renders a complete deck; what changes
# is that a paper which DOES describe them is no longer overwritten by a house
# template identical on every deck for every client.
RECLASSIFIED = ("platform_layers", "next_steps")
RECORD_PATHS = ("today_metrics", "target_metrics") + RECLASSIFIED
STRING_PATHS = ("today_pain_points", "target_capabilities")

# A slot whose ONE record spreads across sibling scalar paths in the fill map,
# rather than becoming a list. `build_summary.duration` is the only one: the deck
# needs the number and its unit in two slots, side by side and never converted,
# which is the shape Stage 1 gave the timeline. The slot path is also the fill
# path of its own first leaf, so absence is answered at one key.
SCALAR_PATHS = {
    "build_summary.duration": (("value", "build_summary.duration"),
                               ("unit", "build_summary.duration_unit")),
}

# The fill-map key a path is written under, where it is not the path itself.
# `packet_document._tree` looks a value up by the SLOT path, and the slot path
# for a LIST OF SCALARS carries the `[]` (`today_pain_points[]`) while a RECORD
# list is keyed by its container without one (`today_metrics`). Writing a list of
# scalars under the bracketless key puts it in the fill map and nowhere in the
# document, which is a silent drop rather than an error -- found exactly that way
# on 2026-08-18, with the value present in the map and the deck's role empty.
FILL_KEY = {"today_pain_points": "today_pain_points[]",
            "target_capabilities": "target_capabilities[]"}
PHASE_SUMMARY = "build_summary.phases[].summary"
BUILD_PHASES = "build_summary.phases"

ROSTER_FIGURES = (packet_assembly.REVENUE, packet_assembly.EBITDA,
                  packet_assembly.MARGIN)
SCENARIOS = "commercial.scenarios"

# The extractable paths that answer a ROSTER field, which is what the
# completeness gate reads and what a run is refused over. Their absence is a
# pure function of the packet, unlike the document half's, which is a question
# about a fill map -- and that is what lets `rescue` ask a second source for
# them without rebuilding one.
ROSTER_SCOPE = ROSTER_FIGURES + (packet_assembly.TIMELINE, SCENARIOS)


@dataclasses.dataclass(frozen=True)
class SecondPass:
    """One second pass: what it asked for, what it merged, and what it refused.

    `packet` is the deterministic packet with the roster absences it could fill
    filled -- a new `Packet`, since E7a's is frozen by design. `extraction` is
    the verified answer, kept whole so `merge_fill` can run later against the
    fill map rebuilt from `packet`. `disagreements` and `dropped` are
    `(path, reason)` pairs, the shape `source_span.MissingFields` uses, and
    `flagged` is `(path, span, section)` for the reviewer's placement check.
    """

    packet: object
    extraction: object = None
    requested: tuple = ()
    merged: tuple = ()
    disagreements: tuple = ()
    dropped: tuple = ()
    flagged: tuple = ()
    failure: str = ""

    @property
    def records(self):
        return getattr(self.extraction, "records", None) or {}

    @property
    def failed(self):
        """Whether the pass itself broke, as opposed to reading a thin paper.

        The distinction the reviewer needs and the one the packet could not make
        before 2026-09-02. `dropped` carries both kinds of absence -- a path the
        pass was not allowed to fill, a path the paper does not state, and every
        path at once when the call never returned -- so a consumer that wants
        only the third asks here rather than matching on a reason string.
        `live_proposal_provider._gate` is that consumer.
        """
        return bool(self.failure)


def run(paper, packet, fill=None, *, extractor=None, document=source_span.PAPER,
        opportunity=None, description=""):
    """Read the base document for what the first pass missed, and merge the
    roster half.

    `document` is which document `paper` is. It defaults to the published paper,
    which is what this read for its whole life, and the live path
    (`live_proposal_provider._assemble`) passes the base document explicitly
    whenever a reviewer attached one. Every figure this produces names it, so a
    default here is a default about which document, never about whether one.

    `opportunity` is which opportunity the pass is reading FOR (item 15). It
    defaults to the one its own `packet` already names, which is the second of
    the two defaults in this provenance chain and is derived rather than
    invented: two opportunities default to two different records, so the default
    can never make one opportunity's reading look like another's. The live path
    passes the real record, title and all.

    `description` is what that opportunity IS, in the platform's own words, and
    it reaches the prompt rather than the figure. It is the disambiguator that
    does the work when one document describes two opportunities whose titles a
    reader could not tell apart out of context, and it is a separate argument
    from `opportunity` for the reason `Opportunity` carries no description:
    1,772 characters on the one real record measured is prompt content, not
    something to hang on every figure in a packet.

    `packet` is E7a's `Packet` and `fill` the fill map `packet_fill.fill_map`
    derived from it. Returns a `SecondPass` whose `packet` is ready for the gate;
    the document half is merged by `merge_fill` once the fill map has been
    rebuilt from that packet.

    `extractor` is the pass itself, a callable
    `(paper, requested, labels, document, opportunity, description) ->
    Extraction`, and NO extractor means no second pass: this returns the
    deterministic packet untouched, having called nothing. That is the same seam
    shape `text_gate.apply_text_gate(..., voice_pass=)` uses for the other LLM
    leg in this repo, and it is deliberate rather than cautious. A module that
    built its own client whenever one could be constructed would make every
    caller that never asked for a model call into one that makes one, including
    the whole test suite. `make_extractor` is how a caller turns it on.
    """
    fill = fill or {}
    if extractor is None:
        return SecondPass(packet=packet)
    requested = absent(packet, fill)
    if not requested:
        return SecondPass(packet=packet)
    opportunity = opportunity or source_span.Opportunity(id=packet.opportunity_id)
    labels = _phase_labels(packet, fill)
    try:
        extraction = extractor(paper, requested, labels, document, opportunity,
                               description)
    except Exception as error:  # noqa: BLE001 -- see the module docstring
        # Logged as well as recorded. The record is for the reviewer reading the
        # packet or the gate error; the log is for whoever is watching the
        # process, who otherwise sees a run that took ten minutes and produced a
        # thin packet with nothing anywhere saying why. `exc_info` because the
        # useful half of a stalled stream is the traceback.
        failure = f"{type(error).__name__}: {error}"
        LOGGER.error(
            "the second extraction pass did not complete (%s); %d requested "
            "field(s) stay absent and the deterministic packet stands: %s",
            failure, len(requested), ", ".join(requested), exc_info=error,
        )
        return SecondPass(
            packet=packet,
            requested=requested,
            failure=failure,
            dropped=tuple(
                (path, "the second extraction pass did not complete "
                       f"({failure}), so this field is "
                       "absent for the reason the first pass gave it.")
                for path in requested
            ),
        )
    merged, disagreements, flagged = [], [], []
    packet = _merge_packet(packet, extraction, merged, disagreements, flagged)
    return SecondPass(
        packet=packet, extraction=extraction, requested=requested,
        merged=tuple(merged), disagreements=tuple(disagreements),
        dropped=tuple(extraction.dropped), flagged=tuple(flagged),
    )


def absent(packet, fill=None):
    """The extractable paths the deterministic pass left empty, in `SCOPE` order.

    Roster absence is read off `Packet.present`, which assembly decided and which
    the score already reads, so there is no second opinion here about what counts
    as present. Document absence is read off the fill map, which is the same
    question `packet_document._is_absent` answers when it writes section 8.
    """
    fill = fill or {}
    wanted = []
    for slot in paper_extraction.SCOPE:
        path = slot.path
        if path in ROSTER_SCOPE:
            if _roster_absent(packet, path):
                wanted.append(path)
        elif path == PHASE_SUMMARY:
            if not _any_summary(fill.get(BUILD_PHASES)):
                wanted.append(path)
        elif path in SCALAR_PATHS:
            if not all(fill.get(target) for _leaf, target in SCALAR_PATHS[path]):
                wanted.append(path)
        elif not fill.get(FILL_KEY.get(path, path)):
            wanted.append(path)
    return tuple(wanted)


def roster_absent(packet):
    """The ROSTER paths still absent from `packet`, in `SCOPE` order.

    A pure function of the packet, which is what makes the chain below possible:
    document absence is a question about a fill map, and a fill map needs the
    company, the request and the opportunity, none of which this module has. The
    six roster fields are the ones the completeness gate reads, and they are the
    ones a run is refused over.
    """
    return tuple(slot.path for slot in paper_extraction.SCOPE
                 if slot.path in ROSTER_SCOPE and _roster_absent(packet, slot.path))


def _roster_absent(packet, path):
    """Whether one roster path is still unanswered. One reading, used twice.

    Roster absence is read off `Packet.present`, which assembly decided and
    which the score already reads, so there is no second opinion anywhere about
    what counts as present.
    """
    if path in ROSTER_FIGURES or path == packet_assembly.TIMELINE:
        return path not in (getattr(packet, "present", frozenset()) or frozenset())
    if path == SCENARIOS:
        # Two different absences behind one path: no cases at all, or cases
        # whose table carried no percentage-point column. Both are asked for;
        # `_merge_scenarios` keeps them apart on the way back in.
        scenarios = getattr(packet, "scenarios", ()) or ()
        return not scenarios or any(case.margin_gain_pp is None
                                    for case in scenarios)
    return False


def rescue(result, sources, *, extractor, opportunity=None, description=""):
    """Ask each supporting source for the ROSTER fields still absent.

    THE REGRESSION THIS CLOSES, from a live run on 2026-09-12. The extraction
    pass is what rescues figures the deterministic parsers cannot lift out of a
    document's tables, and it reads the BASE document only. With nothing
    attached the base IS the published paper, so the pass read the paper and the
    packet reached 0.83. Attach a PRD and the base becomes the PRD, the pass
    reads the PRD, and the client's trailing revenue and adjusted EBITDA are not
    in it -- because no PRD of any quality states them -- so those fields stayed
    absent and the same opportunity scored 0.17 and was refused. Attaching a
    document made a working run fail.

    The deterministic merge already had the rule right: `base_document` gives
    every field to the base and lets the paper answer what the base does not.
    The model leg did not, because it was written when there was only ever one
    document to read.

    PER FIELD, NEVER PER DOCUMENT (Antonio, 2026-09-12). This is not a judgement
    about whether a document is good enough, and there is deliberately no such
    test anywhere: the PRD that triggered this supplied 39 quoted values and the
    deck's entire specific substance while lacking the baseline P&L, so an
    all-or-nothing rule either discards what it gave or loses what it did not.
    Each source answers only what the sources ahead of it left absent.

    ONE DOCUMENT PER CALL, which is the other reason this is a chain of calls
    rather than one prompt holding every document. A figure rescued from the
    paper must NAME the paper: the call is handed that source's text and that
    source's `Document`, so the span verifies against the document it was read
    from and `SourcedFigure` carries it. A single call over two documents could
    not say which one it had read.

    WHAT IT COSTS. One extraction call per source, and only while a roster field
    is still absent, so a base document that answers everything costs nothing at
    all and a run with no attachment never enters this function (its chain has
    no supporting source). Each call is the same bounded leg as the first
    (`model_call`, 400s over a measured 102-216s), so the bound does not move;
    what moves is the wall clock of a run that needed the rescue.

    Only the roster is asked for. The document half (the metrics, the pain
    points, the platform layers, the next steps) stays with the base, which is
    what it has always been and what the reviewer is shown: those are the
    engagement's own specifics and the base document is the document about this
    engagement. The roster is the company's own financial state, which is what a
    published paper carries and a PRD does not.

    Degradation is stated the way the first call states it: a call that raises
    leaves every field it asked about absent, records the failure, and lets the
    chain stop, because a pass that broke is not a pass that found nothing.
    """
    if result is None or extractor is None:
        return result
    opportunity = opportunity or source_span.Opportunity(
        id=result.packet.opportunity_id)
    for source in sources or ():
        wanted = roster_absent(result.packet)
        if not wanted:
            break
        result = _read_one_more(result, source, wanted, extractor, opportunity,
                                description)
        if result.failed:
            break
    return result


def _read_one_more(result, source, wanted, extractor, opportunity,
                   description=""):
    """One more extraction call, against one more source, for `wanted` only.

    `opportunity` rides along because a rescue reads a SUPPORTING source for a
    field the base did not answer, and on a multi-opportunity run that
    supporting source is this opportunity's own paper while the base is shared.
    The figure has to name both.
    """
    try:
        extraction = extractor(source.text, wanted,
                               _phase_labels(result.packet, {}),
                               source.document, opportunity, description)
    except Exception as error:  # noqa: BLE001 -- see the module docstring
        failure = f"{type(error).__name__}: {error}"
        LOGGER.error(
            "a second extraction call against a supporting source did not "
            "complete (%s); %d roster field(s) stay absent: %s",
            failure, len(wanted), ", ".join(wanted), exc_info=error,
        )
        return dataclasses.replace(
            result,
            requested=_union(result.requested, wanted),
            failure=result.failure or failure,
            dropped=_redropped(result.dropped, wanted, tuple(
                (path, "a second extraction call against a supporting source "
                       f"did not complete ({failure}), so this field is absent "
                       "for the reason the pass before it gave.")
                for path in wanted)),
        )
    merged, disagreements, flagged = [], [], []
    packet = _merge_packet(result.packet, extraction, merged, disagreements,
                           flagged)
    return dataclasses.replace(
        result,
        packet=packet,
        # The readings of every call so far, under one path each. A path is only
        # ever asked again when it is still absent, so a later reading can only
        # replace one that did not become a figure, and `ledger` and
        # `merge_fill` read this without knowing how many calls produced it.
        extraction=paper_extraction.Extraction(
            requested=_union(result.requested, wanted),
            records=dict(result.records, **extraction.records),
            dropped=extraction.dropped,
        ),
        requested=_union(result.requested, wanted),
        merged=result.merged + tuple(merged),
        disagreements=result.disagreements + tuple(disagreements),
        # The reason a field is STILL missing is the last source's, not the
        # first's: a reviewer reading section 8 wants to know that every source
        # was asked and none of them stated it, which one entry per field says
        # and two entries per field obscure.
        dropped=_redropped(result.dropped, wanted, tuple(
            entry for entry in extraction.dropped if entry[0] not in merged)),
        flagged=result.flagged + tuple(flagged),
    )


def _union(standing, wanted):
    """`standing` plus whatever of `wanted` it does not already carry, in order."""
    return tuple(standing) + tuple(path for path in wanted
                                   if path not in set(standing))


def _redropped(standing, wanted, replacements):
    """`standing` with the re-asked paths' reasons replaced by this call's."""
    asked = set(wanted)
    return tuple((path, reason) for path, reason in standing
                 if path not in asked) + tuple(replacements)


def merge_fill(fill, sources, result):
    """`(fill, sources, result)` with the document half of the second pass in.

    Called AFTER the fill map has been rebuilt from `result.packet`, so the
    absence check here sees the map as it will actually be written. New maps and
    a new result rather than edited ones: the caller's are left as they were and
    `SecondPass` stays frozen.
    """
    fill, sources = dict(fill or {}), dict(sources or {})
    if result is None or not result.records:
        return fill, sources, result
    merged, disagreements = list(result.merged), list(result.disagreements)
    for path in RECORD_PATHS + STRING_PATHS:
        records = result.records.get(path)
        if not records:
            continue
        key = FILL_KEY.get(path, path)
        if fill.get(key):
            disagreements.append((path, _both(path, fill[key], records)))
            continue
        slot = paper_extraction.BY_PATH[path]
        fill[key] = (
            [_record(slot, record, index if path in RECLASSIFIED else None)
             for index, record in enumerate(records, start=1)]
            if path in RECORD_PATHS
            else [_display(record.leaves["value"]) for record in records]
        )
        sources[key] = SECOND_PASS
        merged.append(path)
    fill, sources, merged, disagreements = _merge_scalars(
        fill, sources, result, merged, disagreements
    )
    fill, sources, merged, disagreements = _merge_phase_summaries(
        fill, sources, result, merged, disagreements
    )
    return fill, sources, dataclasses.replace(
        result, merged=tuple(merged), disagreements=tuple(disagreements)
    )


def ledger(result):
    """Section 8's `second_pass` block: what it filled, refused, and disagreed on.

    The reviewer's misattribution check, in the one place the provenance already
    lives. Span verification closes fabrication mechanically; a value in the
    wrong field verifies fine and no automated check in this pipeline catches it,
    so the section heading and the quoted span go on the artifact and a human
    looks. `review` marks a current-state roster figure read under a heading that
    is not about the company today; it may well be right, and the reviewer
    decides.

    Returns None when no second pass ran, so `packet_document` emits nothing at
    all rather than an empty block claiming one ran and found nothing.
    """
    if result is None or not result.requested:
        return None
    flagged = {(path, span) for path, span, _section in result.flagged}
    filled = []
    for path in result.merged:
        for record in result.records.get(path, ()):
            entry = {
                "field": path,
                "section": _line(record.section),
                "span": _line(record.span),
            }
            if (path, record.span) in flagged:
                entry["review"] = (
                    "read under a heading that is not this company's current "
                    "state; confirm the figure belongs to this field."
                )
            filled.append(entry)
    block = {
        "asked_for": list(result.requested),
        "filled": filled,
        "disagreements": [
            {"field": path, "note": note} for path, note in result.disagreements
        ],
        "not_filled": [
            {"field": path, "reason": _line(reason, 400)}
            for path, reason in result.dropped
        ],
    }
    return block


# --------------------------------------------------------------------------
# The roster half.
# --------------------------------------------------------------------------

def _merge_packet(packet, extraction, merged, disagreements, flagged):
    """A new `Packet` carrying the roster fields the paper stated and the
    parsers could not read.

    `ROSTER` itself is untouched: this fills its six members, it never extends
    them. `dataclasses.replace` rather than mutation, since E7a's `Packet` is
    frozen by design and the deterministic result stays intact behind this one.
    """
    present = getattr(packet, "present", frozenset()) or frozenset()
    fields = list(getattr(packet, "fields", ()) or ())
    scenarios = tuple(getattr(packet, "scenarios", ()) or ())
    for path in ROSTER_FIGURES:
        records = extraction.records.get(path)
        if not records:
            continue
        if path in present:
            disagreements.append((path, _both(path, "the parser's figure",
                                              records)))
            continue
        record = records[0]
        _flag(path, record, flagged)
        fields.append(SourcedFigure(path, record.leaves["value"], record.span,
                                    record.figure.source,
                                    record.figure.document,
                                    record.figure.opportunity))
        merged.append(path)
    fields = _merge_timeline(packet, extraction, fields, merged, disagreements,
                             flagged)
    scenarios = _merge_scenarios(scenarios, extraction, merged, disagreements,
                                 flagged)
    return dataclasses.replace(packet, fields=tuple(fields), scenarios=scenarios)


def _merge_timeline(packet, extraction, fields, merged, disagreements, flagged):
    """E4's phase list, where no `<Chart>` in the paper was a timeline.

    One `SourcedFigure` over the whole list, the way `packet_assembly._timeline`
    builds it, so `packet_fill._phases` reads it without knowing which pass
    produced it. Its span is the stretch of paper covering every phase's own
    span, which is a literal slice of the paper and is verified as one; each
    phase's individual span goes to the reviewer through `ledger`.
    """
    path = packet_assembly.TIMELINE
    records = extraction.records.get(path)
    if not records:
        return fields
    if path in (getattr(packet, "present", frozenset()) or frozenset()):
        disagreements.append((path, _both(path, "the chart's own phases",
                                          records)))
        return fields
    paper = records[0].figure.source
    phases = [
        {"label": record.leaves["label"], "start": record.leaves["start"],
         "end": record.leaves["end"], "unit": record.leaves["unit"]}
        for record in records
    ]
    for record in records:
        _flag(path, record, flagged)
    merged.append(path)
    return fields + [SourcedFigure(path, phases, _covering(paper, records),
                                   paper, records[0].figure.document,
                                   records[0].figure.opportunity)]


def _merge_scenarios(scenarios, extraction, merged, disagreements, flagged):
    """E5b's cases, or the percentage-point column its table did not carry.

    Two operations behind one path, and they are not the same one. Where the
    parser read NO cases, the paper's own named cases are adopted whole. Where it
    read cases, the case SET is the parser's and stays the parser's: the only
    thing that may be filled is a `margin_gain_pp` the parser left None, matched
    on the case's own name. Every other overlap is a disagreement, including the
    dollar figure, which every returned case carries and which the parser already
    sourced for a case it read.
    """
    records = extraction.records.get(SCENARIOS)
    if not records:
        return scenarios
    if not scenarios:
        for record in records:
            _flag(SCENARIOS, record, flagged)
        merged.append(SCENARIOS)
        return tuple(
            ScenarioCase(
                label=record.leaves["name"],
                direct_uplift_usd_yr=SourcedFigure(
                    packet_assembly.UPLIFT,
                    record.leaves["direct_uplift_usd_yr"],
                    record.span, record.figure.source,
                    record.figure.document, record.figure.opportunity,
                ),
                margin_gain_pp=(
                    SourcedFigure(packet_assembly.GAIN,
                                  record.leaves["margin_gain_pp"], record.span,
                                  record.figure.source,
                                  record.figure.document,
                                  record.figure.opportunity)
                    if "margin_gain_pp" in record.leaves else None
                ),
            )
            for record in records
        )
    by_name = {record.leaves["name"]: record for record in records}
    filled, touched = [], False
    for case in scenarios:
        record = by_name.get(case.label)
        if record is None:
            filled.append(case)
            continue
        disagreements.append((packet_assembly.UPLIFT, _both(
            f"{SCENARIOS}[{case.label}].direct_uplift_usd_yr",
            case.direct_uplift_usd_yr.value,
            [record], "direct_uplift_usd_yr")))
        if case.margin_gain_pp is not None:
            if "margin_gain_pp" in record.leaves:
                disagreements.append((packet_assembly.GAIN, _both(
                    f"{SCENARIOS}[{case.label}].margin_gain_pp",
                    case.margin_gain_pp.value, [record], "margin_gain_pp")))
            filled.append(case)
            continue
        if "margin_gain_pp" not in record.leaves:
            filled.append(case)
            continue
        _flag(SCENARIOS, record, flagged)
        filled.append(dataclasses.replace(case, margin_gain_pp=SourcedFigure(
            packet_assembly.GAIN, record.leaves["margin_gain_pp"], record.span,
            record.figure.source, record.figure.document,
            record.figure.opportunity,
        )))
        touched = True
    if not touched:
        return scenarios
    merged.append(packet_assembly.GAIN)
    return tuple(filled)


def _covering(paper, records):
    """The stretch of `paper` covering every record's own span.

    A literal slice of the paper, so it verifies as a span the same way any other
    does. A paper stating its phases in one table or one section yields that
    table or that section; one stating them far apart yields the stretch between,
    which is the truthful answer to "where did this list come from".
    """
    starts = [paper.index(record.span) for record in records]
    ends = [start + len(record.span) for start, record in zip(starts, records)]
    return paper[min(starts):max(ends)]


# --------------------------------------------------------------------------
# The document half.
# --------------------------------------------------------------------------

def _merge_scalars(fill, sources, result, merged, disagreements):
    """The slots whose one record spreads across sibling scalar fill paths.

    `build_summary.duration` today, and the merge is all-or-nothing across its
    leaves: a horizon without its unit is a number the deck cannot render and
    would have to guess at, which is the guess this whole stage exists to refuse.
    The precedence check is the ordinary one and it is checked against the map as
    it stands, which matters here more than anywhere: the deterministic layer
    DERIVES this horizon from the plan's own last phase, and on a paper whose
    plan the second pass itself supplied that derivation lands between the
    request and the merge. So a paper that both draws a plan and states a horizon
    in prose keeps the plan's, and the sentence it stated is recorded as a
    disagreement with both values for the reviewer.
    """
    for path, leaves in SCALAR_PATHS.items():
        records = result.records.get(path)
        if not records:
            continue
        record = records[0]
        if any(fill.get(target) for _leaf, target in leaves):
            disagreements.append((path, _both(path, "the derived value",
                                              [record])))
            continue
        if any(leaf not in record.leaves for leaf, _target in leaves):
            continue
        for leaf, target in leaves:
            fill[target] = _display(record.leaves[leaf])
            sources[target] = SECOND_PASS
        merged.append(path)
    return fill, sources, merged, disagreements


def _merge_phase_summaries(fill, sources, result, merged, disagreements):
    """Each phase's own summary, onto the phase record the first pass named.

    Matched on the label the first pass already read, never on position: a
    summary landing on the wrong bar of the plan is the same class of error as a
    figure landing in the wrong field. A phase the second pass had nothing for
    keeps its key with an empty value rather than losing it, because Stage 1
    made an omitted key say nothing at all on the deck while an empty one still
    marks -- and "we did not get this phase's summary" is an absence the deck
    should show, not one it should swallow.
    """
    records = result.records.get(PHASE_SUMMARY)
    phases = fill.get(BUILD_PHASES)
    if not records or not phases:
        return fill, sources, merged, disagreements
    if _any_summary(phases):
        disagreements.append((PHASE_SUMMARY,
                              _both(PHASE_SUMMARY, "the first pass's summary",
                                    records, "summary")))
        return fill, sources, merged, disagreements
    by_label = {record.leaves["label"]: record for record in records}
    filled, matched = [], False
    for phase in phases:
        record = by_label.get(phase.get("label"))
        summary = _display(record.leaves["summary"]) if record else ""
        matched = matched or bool(summary)
        filled.append({**phase, "summary": summary})
    if not matched:
        return fill, sources, merged, disagreements
    fill = {**fill, BUILD_PHASES: filled}
    sources = {**sources, BUILD_PHASES: SECOND_PASS}
    return fill, sources, merged + [PHASE_SUMMARY], disagreements


def _record(slot, record, ordinal=None):
    """One fill-map record: every leaf that applies to this slot's shape.

    A leaf that applies and was not obtained carries its key with an empty value,
    so `prompt_assembler._render_records` marks it on the deck and
    `packet_document` names it in section 8 gaps. A leaf that does not apply to
    the shape at all is omitted, which says nothing. Stage 1 made those two
    different claims, and this is where they are told apart.

    `ordinal` is the record's own position in the list the paper stated, written
    as the `number` the deck's two numbered strips carry. It is not extracted and
    it is not a claim about the paper: it is the same ordinal-label call Antonio
    made for milestones on 2026-08-18, a label at a real position rather than an
    invented name for one. The alternative was to leave `number` absent, which
    would put a `[MISSING: number]` marker inside every component on slide 3 for
    a field no paper will ever state.
    """
    leaves = {
        leaf.name: _display(record.leaves.get(leaf.name, ""))
        for leaf in slot.leaves if leaf.applies
    }
    return leaves if ordinal is None else {"number": f"{ordinal:02d}", **leaves}


def _display(value):
    """One prose value as the packet document can carry it: whitespace collapsed.

    Found 2026-08-19, and it is a WRONG VALUE rather than an absence, which is
    the class this repo takes hardest. `packet_document._scalar_text` writes a
    scalar as one quoted line and `data_source_adapter._section_yaml` reads it
    back a line at a time, so a quotation carrying the paper's own hard line
    wrap reached the deck cut off at the wrap with its opening quote still
    attached: `"Crews are dispatched from a whiteboard rebuilt every morning,
    and the rebuild` and the rest of the sentence gone, silently. Papers hard-
    wrap constantly, so this was reachable by every prose path Stage 2 opened and
    not only by the ones Stage 2c adds; it went unseen because the hand-built
    fixture's assertions all quoted single lines.

    Collapsing is done HERE, at the one point where an extracted value changes
    shape from a slice of the paper into a deck cell, and it is the only change
    made to one: the span was verified against the untouched paper before this
    ran, `SourcedFigure` still holds that span, and section 8's ledger quotes it
    through `_line`, which collapses the same way for the same reason. A number
    is untouched, since only a `TEXT` leaf can carry a newline.

    The alternative was to teach the document's emitter block scalars, which
    changes the packet format every frozen fixture and both reference packets are
    written in. That is a contract change; this is not.
    """
    if not isinstance(value, str):
        return value
    return " ".join(value.split())


def _any_summary(phases):
    return any((phase or {}).get("summary") for phase in phases or ())


def _phase_labels(packet, fill):
    """The phase labels the first pass read, so a summary can be keyed to one."""
    phases = fill.get(BUILD_PHASES) or []
    labels = [phase.get("label") for phase in phases if phase.get("label")]
    if labels:
        return tuple(labels)
    for figure in getattr(packet, "fields", ()) or ():
        if figure.field == packet_assembly.TIMELINE:
            return tuple(phase.get("label") for phase in figure.value
                         if phase.get("label"))
    return ()


def _flag(path, record, flagged):
    """Record a current-state roster figure read under a heading that is not one.

    Not a heuristic that second-guesses the reading: nothing is dropped, nothing
    is corrected, and the value goes into the packet either way. It puts the
    section heading in front of the reviewer, which is the only defence this
    pipeline has against misattribution.
    """
    if path not in CURRENT_STATE:
        return
    lowered = record.section.lower()
    if any(word in lowered for word in NOT_CURRENT_STATE):
        flagged.append((path, record.span, record.section))


def _both(path, standing, records, leaf="value"):
    """The disagreement line, carrying BOTH values so a reviewer can compare."""
    offered = " / ".join(
        str(record.stated.get(leaf, "")) for record in records
        if record.stated.get(leaf)
    )
    return (f"both passes produced a value for {path}. The deterministic "
            f"parser's stands ({standing!r}); the second pass offered "
            f"{offered!r} and it is discarded.")


def _line(text, limit=240):
    """One line of prose for the section 8 ledger.

    The packet document writes a scalar as one quoted line, so a span carrying a
    newline would break the yaml its own reader parses back. This is the
    provenance a reviewer reads, not the span verification, which ran against the
    paper on the untouched original.
    """
    collapsed = " ".join(str(text).split())
    return collapsed if len(collapsed) <= limit else collapsed[:limit] + "..."


def make_extractor(**options):
    """Build the `(paper, requested, labels, document, opportunity,
    description) -> Extraction` callable `run` takes.

    The one place the real pass is turned on, mirroring
    `voice_pass.make_voice_pass` for the other LLM leg. `options` go through to
    `paper_extraction.extract`: `client` to inject a model client, `api_key`,
    `model`, `max_tokens`, `effort`. With none of them the pass constructs a real
    `anthropic.Anthropic` reading `ANTHROPIC_API_KEY`.

    Cached on the paper text, because the same paper is read for many fields and
    a re-render of the same opportunity should not re-call the API.
    `extraction_cache` already memoizes assembly that way and this rides the same
    scheme rather than inventing a second one. The import is inside the call so
    the pure pipeline stays importable with no SDK installed.
    """
    import extraction_cache

    def extract(paper, requested, labels, document, opportunity,
                description=""):
        return extraction_cache.extract_cached(paper, requested, labels,
                                               document=document,
                                               opportunity=opportunity,
                                               description=description,
                                               **options)

    return extract


# --------------------------------------------------------------------------
# The GENERATED half (E11 Stage 2c). A SECOND, SEPARATE PATH, and the
# separation is the point rather than a tidiness preference.
#
# Everything above this line moves QUOTATIONS: a value enters only with a span
# `SourcedFigure` verified against the paper, and a fabrication fails at
# construction. Everything below moves SENTENCES THE MODEL WROTE, which cannot
# carry a span because nobody wrote them in the paper. They are governed instead
# by `paper_writing`: they name the sections they were written from, those
# sections must resolve to real regions of the source, every number and every
# proper noun in them must occur in evidence quoted from those regions, and they
# are LABELLED as generated in the packet's provenance.
#
# The two never mix. A generated sentence never enters the fill map, never
# becomes a packet field, never reaches `Packet.present`, and so cannot move
# `data_completeness` or any gate. It reaches the deck only through the copy
# channel, which `coverage_guard` does not govern precisely because those lines
# carry no field path.
# --------------------------------------------------------------------------

# The deck standard each generated line replaces, and falls back to. Keyed by the
# copy path `paper_writing` declares, valued by the attribute of `packet_fill`
# holding the constant, so this module names no deck copy of its own.
FRAMING_FALLBACK = {
    "copy.platform_headline": "PLATFORM_HEADLINE",
    "copy.platform_summary": "PLATFORM_SUMMARY",
    "copy.plan_headline": "PLAN_HEADLINE",
    "copy.plan_summary": "PLAN_SUMMARY",
    "copy.next_steps_headline": "NEXT_STEPS_HEADLINE",
    "copy.next_steps_summary": "NEXT_STEPS_SUMMARY",
}


def _normalised_line(text):
    """A line reduced to what it SAYS, for comparing one against another."""
    return " ".join((text or "").split()).casefold().rstrip(".")


def restates_deck_standard(path, text):
    """Whether a generated line just hands back the standard it was to replace.

    The table above stopped being decorative on 2026-09-16. Until then it was
    declared and read by nothing at all, while the rule it describes lived only
    in a guidance string asking the model to please be more specific.

    The defect it closes, from the 2026-09-15 live Northwind deck: slide 3 read "How
    the platform is built.", which is `packet_fill.PLATFORM_HEADLINE`, while
    `platform_summary` beside it carried a written line and the plan and
    next-steps headlines on the same deck carried theirs. A line equal to the
    deck standard passes every check in `paper_writing._sentence` (it is inside
    the limit, names a section, quotes evidence, states no number and no proper
    noun), so it was accepted, recorded as GENERATED, and rendered through
    `packet_fill.copy_lines`'s `framed()` as `written.get(path) or constant` --
    which is the same string either way. A written line and a refusal were
    therefore INDISTINGUISHABLE on the deck and in section 8, and the one state
    reported was the wrong one: the ledger claimed a line was written for a slot
    whose slide says nothing about the client.

    Refused here rather than in `paper_writing`, which is stdlib-only by
    construction and holds no deck copy. The standards live in `packet_fill`
    because they ARE deck copy, and this module already declared which one each
    path falls back to.

    Compared on what the line says, not byte for byte: case, surrounding space
    and a trailing period are not the difference between a specific headline and
    a generic one.
    """
    name = FRAMING_FALLBACK.get(path)
    if not name:
        return False
    standard = getattr(packet_fill, name, "")
    return bool(standard) and _normalised_line(text) == _normalised_line(standard)


@dataclasses.dataclass(frozen=True)
class SecondWriting:
    """One writing pass: what it was asked for, what it wrote, what it refused.

    Deliberately NOT folded into `SecondPass`. The two passes answer different
    questions, run against different rules, and are reported in different blocks
    of section 8; a reviewer who cannot tell a quotation from a sentence at a
    glance has lost the only thing this stage's discipline buys.
    """

    requested: tuple = ()
    writing: object = None
    dropped: tuple = ()
    failure: str = ""

    @property
    def sentences(self):
        return getattr(self.writing, "sentences", None) or {}

    @property
    def failed(self):
        """Whether the pass itself broke, rather than declining a line."""
        return bool(self.failure)


def write(paper, description, absent_copy=(), *, writer=None):
    """Write the framing lines the deck has no source for. -> `SecondWriting`.

    `writer` is the pass itself, a callable `(paper, description, requested) ->
    Writing`, and NO writer means no writing pass: this returns nothing written,
    having called nothing. Same seam shape as `run` above and as
    `text_gate.apply_text_gate(..., voice_pass=)`, and for the same reason -- a
    module that built its own client whenever one could be constructed would
    turn every caller that never asked for a model call into one that makes one,
    the whole test suite included. `make_writer` is how a caller turns it on.

    `absent_copy` is the copy paths with no source on this run, which for the
    framing lines is all of them: they have never had a source, only a deck
    standard. A caller may narrow it.

    Degradation is stated rather than hidden, the same way the extraction half
    states it. A writer that raises leaves every line on its deck standard and
    records the failure, rather than sinking a deck that was already renderable.
    """
    requested = tuple(path for path in paper_writing.PATHS
                      if not absent_copy or path in set(absent_copy))
    if writer is None or not requested:
        return SecondWriting()
    try:
        writing = writer(paper, description, requested)
    except Exception as error:  # noqa: BLE001 -- see the module docstring
        failure = f"{type(error).__name__}: {error}"
        LOGGER.error(
            "the writing pass did not complete (%s); %d framing line(s) keep "
            "the deck's standard wording", failure, len(requested),
            exc_info=error,
        )
        return SecondWriting(
            requested=requested,
            failure=failure,
            dropped=tuple(
                (path, "the writing pass did not complete "
                       f"({failure}), so this line keeps "
                       "the deck's standard framing.")
                for path in requested
            ),
        )
    return SecondWriting(requested=requested, writing=writing,
                         dropped=tuple(writing.dropped))


def merge_written(sources, result):
    """`(written, sources)`: the sentences `packet_fill.copy_lines` should use.

    `written` maps a copy path to the sentence's text, and only for a path that
    verified. A path with no entry keeps its deck standard, which is what makes a
    refusal a correct outcome rather than a hole.

    The source ledger records each one under `GENERATED`, beside the tools and
    the spans, so section 8's `sources` list already tells a reviewer that this
    line was written rather than quoted before they reach the `second_pass`
    block that says it in full.
    """
    sources = dict(sources or {})
    written = {}
    if result is None:
        return written, sources
    for path, sentence in result.sentences.items():
        # A line that restates its own deck standard is no line at all, and
        # letting it through would report a written line for a slide that reads
        # exactly as it would have read with nothing written. See
        # `restates_deck_standard`.
        if restates_deck_standard(path, sentence.text):
            continue
        written[path] = sentence.text
        sources[path] = GENERATED
    return written, sources


def written_ledger(result):
    """Section 8's `generated` block: what was written, from where, and refused.

    Rule 3 of the generated contract, and the one a reviewer actually uses. Each
    entry carries the line, the sections it was written FROM and the evidence it
    restated, so the check a machine cannot make -- is this claim true of those
    sections -- is a check a person can make in one place.

    Returns None when no writing pass ran, so `packet_document` emits nothing at
    all rather than an empty block claiming one ran and wrote nothing.
    """
    if result is None or not result.requested:
        return None
    return {
        "asked_for": list(result.requested),
        "written": [
            {
                "field": path,
                "kind": "GENERATED, not quoted",
                "text": _line(sentence.text),
                "written_from": list(sentence.sections),
                "restating": [_line(span) for span in sentence.evidence],
            }
            for path, sentence in sorted(result.sentences.items())
            if not restates_deck_standard(path, sentence.text)
        ],
        # Reported as refused, with the reason, because that is what it is. The
        # slide is unchanged either way; what changes is that section 8 stops
        # claiming a line was written for it.
        "not_written": [
            {"field": path, "reason": _line(reason, 400)}
            for path, reason in result.dropped
        ] + [
            # ASKED FOR AND NEVER ANSWERED, which is a fourth state and was
            # invisible until 2026-09-16. `paper_writing.read_response` records
            # only what the model RETURNED: a line it wrote, or one it returned
            # that failed verification. A slot the model simply omitted appeared
            # in neither list, so the ledger said nothing at all about it and a
            # reviewer had to subtract two lists from a third to notice.
            #
            # That is exactly one of the two candidates in the defect this item
            # exists to diagnose: on the 2026-09-16 Northwind render `plan_summary`
            # and `next_steps_summary` came back as their deck standards, and
            # "the model declined the slot" is this state. Without it the ledger
            # would have answered the question it was built to answer with a
            # silence that reads like an absence of a problem.
            {"field": path,
             "reason": "the pass was asked for this line and returned nothing "
                       "for it, so the slide keeps its deck standard."}
            for path in result.requested
            if path not in result.sentences
            and path not in {dropped_path for dropped_path, _ in result.dropped}
        ] + [
            {"field": path,
             "reason": "restated the deck standard it was asked to replace, so "
                       "the slide keeps that standard and nothing was gained."}
            for path, sentence in sorted(result.sentences.items())
            if restates_deck_standard(path, sentence.text)
        ],
    }


def make_writer(**options):
    """Build the `(paper, description, requested) -> Writing` callable `write` takes.

    The one place the real writing pass is turned on, mirroring `make_extractor`
    beside it and `voice_pass.make_voice_pass` for the other LLM leg. `options`
    go through to `paper_writing.write`: `client`, `api_key`, `model`,
    `max_tokens`, `effort`.

    NOT cached, and that is a difference from `make_extractor` worth stating.
    `extraction_cache` memoises on the paper text because the same paper is read
    for many fields; a writing pass asks for every line it can write in one call,
    so a second call against the same paper is a re-render rather than a second
    field, and adding a cache here would be adding a mechanism nothing needs.
    """

    def writer(paper, description, requested):
        return paper_writing.write(paper, description, requested, **options)

    return writer
