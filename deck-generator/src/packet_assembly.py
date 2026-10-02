"""Packet assembly with provenance (E7a, assembly half).

Turns one opportunity's research paper into one immutable packet whose every
field carries the paper text it came from. The four parsers do the reading; this
module decides what a packet FIELD is, what it is called, and what its absence
says. Nothing here computes a score: `src/completeness_score.py` counts what
this module produced and cannot reach back into it.

What a field is. `ROSTER` is the six packet fields the provider derives from a
research paper, named by their path in `proposal-data-packet-EXAMPLE.md`. Each
enters only as a `SourcedFigure`, so a value without a span cannot enter a
packet, and each absence is recorded with a reason in packet-path space: a
parser's own field names never reach the contract's `missing_fields` list.

Where the boundary is drawn, in one line: the provider claims the paper-derived
roster and nothing else, so QofAI's per-deal terms (`REVIEWER_INPUT`) are not
packet fields at all, and registry-resolved identity fields are outside the
roster because resolution has already failed loudly if they are absent.

Why commercial terms are not claimed. They are QofAI's per-deal pricing rather
than facts about the client, and no paper carries them: 0 of 21 published papers
in `data-provider/PRD.md` section 2.1c, re-verified 2026-08-12 against all eight
committed excerpts, where every pricing hit is client product pricing, a
competitor benchmark, a third-party vendor quote, or a management fee to a
holding company. `margin_gain_pp` and `direct_uplift_usd_yr` are the exception
and parse like any other figure, being the scenario table's own numbers.

The representation, decided once. Every figure is a `(low, high)` pair of floats
in its field's declared unit, equal when the paper states one value. That is
E5b's shape already, so E5b passes through under a packet name; E5c and E5d hand
over the paper's own strings in two notations and convert at the boundary,
keeping the span they arrived with. Measured across all eight excerpts
2026-08-12: comma-grouped full figures reach the baseline E5d selects on exactly
ONE, `scenario-rows-canonical`, at `$67,600,000` and `$12,000,000`, and the
`$59.2M` suffix notation covers the other seven. Two further excerpts carry
comma-grouped figures in tables E5d does not select, which is why the count is
one rather than three. So the conversion handles the comma-grouped case on one
fixture, and both notations still have to arrive in the same shape. A string
stating no single figure in its unit is not converted into one: it becomes a
missing field with a reason, which is the golden rule at the one point in the
pipeline where a value changes shape.

Baseline sources, in preference order. E5d first, because it returns a whole
statement with the period and basis a deck must show, and reads 21 of 21 papers
against E5c's 1 of 21. E5c's assumption row is the per-figure fallback, carrying
a span but no period, which is why it is second. On the eight excerpts E5d
states all three figures on all eight, so the fallback never fires there.
"""

import dataclasses
import re

import assumption_table_parser
import baseline_parser
import chart_timeline_parser
import prd_section_parsers
import scenario_table_parser
from scenario_table_parser import _money as read_usd
import source_span
from source_span import MissingFields, SourcedFigure

USD = "usd"
PERCENT = "percent"

REVENUE = "baseline.revenue_ttm_usd"
EBITDA = "baseline.adjusted_ebitda_usd"
MARGIN = "baseline.adjusted_ebitda_pct"
UPLIFT = "commercial.scenarios[].direct_uplift_usd_yr"
GAIN = "commercial.scenarios[].margin_gain_pp"
TIMELINE = "timeline.phases"

# The fields the deck needs and the provider can derive from a paper. The score
# is a ratio over exactly this tuple.
ROSTER = (REVENUE, EBITDA, MARGIN, UPLIFT, GAIN, TIMELINE)

# QofAI's own per-deal arithmetic. Reviewer input by design, so they are named
# here as the fields the provider does NOT supply rather than left unstated.
REVIEWER_INPUT = (
    "commercial.qofai_comp_usd",
    "commercial.client_retained_ebitda_usd",
    "commercial.no_improvement_clause",
    "commercial.how_payment_works",
    "commercial.enterprise_value_at_exit_usd",
)

# packet field -> (E5d absence name, E5d attribute, E5c field, unit).
BASELINE_SOURCES = (
    (REVENUE, baseline_parser.REVENUE, "revenue", "ltm_revenue", USD),
    (EBITDA, baseline_parser.EBITDA, "ebitda", "ltm_ebitda", USD),
    (MARGIN, baseline_parser.MARGIN, "ebitda_margin", "ltm_ebitda_margin", PERCENT),
)

# E11 Stage 2b, 2026-08-19: `percent` alongside `%`, so a paper spelling its
# unit out keeps the figure. `percent\b` deliberately does not reach
# `percentage`, which keeps `1.9 percentage points` out of a percent field;
# that unit has its own reader in `scenario_table_parser.read_pp`.
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent\b)", re.IGNORECASE)
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


@dataclasses.dataclass(frozen=True)
class ScenarioCase:
    """One scenario case under the packet's own field names.

    `margin_gain_pp` is None where the paper's table carries no percentage-point
    column, which E5b records as a normal outcome rather than an error.
    """

    label: str
    direct_uplift_usd_yr: SourcedFigure
    margin_gain_pp: object


@dataclasses.dataclass(frozen=True)
class Packet:
    """One opportunity's packet, immutable once assembled (2026-07-28).

    Frozen, and every member is a tuple of frozen records, so there is no
    mutable handle to the assembled result -- including `missing_fields`, which
    is `(name, reason)` pairs rather than the `MissingFields` recorder.
    `present` is assembly's own answer to which roster fields it produced; the
    score reads it and never recomputes it. `conflicts` and `set_aside`
    are the two members assembly never fills: one document cannot disagree with
    itself, and a merge of one declines nothing.
    """

    opportunity_id: str
    fields: tuple
    scenarios: tuple
    baseline_period: object
    baseline_basis: object
    missing_fields: tuple
    # The fields more than one source answered where the answers differ
    # (`source_span.Conflict`), filled by `base_document.merge_packets` and
    # empty on a packet assembled from one document, which is every packet a run
    # with no attachment produces. Reviewer-facing only: nothing here is
    # rendered, because a source annotation does not belong in front of a
    # client, and `packet_document` never reads it.
    conflicts: tuple = ()
    # The figures a source stated that the merge declined to take under a rule
    # about a unit larger than the field (`source_span.SetAside`), which today
    # means the baseline group. Filled by the same merge, empty on a packet from
    # one document, and reviewer-facing on the same terms as `conflicts`.
    set_aside: tuple = ()

    @property
    def present(self):
        names = {figure.field for figure in self.fields}
        if self.scenarios:
            names.add(UPLIFT)
            if all(case.margin_gain_pp for case in self.scenarios):
                names.add(GAIN)
        return frozenset(names.intersection(ROSTER))


def assemble(opportunity_id, paper, document=source_span.PAPER,
             opportunity=None):
    """Assemble one opportunity's packet from one document.

    `document` is which document `paper` is (`source_span.Document`). It
    defaults to the published paper, which is what it was for every caller
    before a reviewer could attach anything, and it is one of the two defaults
    in this provenance chain: every figure still names a document, and the
    default names the document this function read for its whole life. A caller
    reading something else says so, and `base_document.precedence` is the only
    place that happens.

    `opportunity` is which opportunity this packet is FOR
    (`source_span.Opportunity`), and it is the second default, added 2026-09-13
    with item 15. Left unset it is DERIVED FROM `opportunity_id`, which the
    caller has already passed and which this packet already carries, so the
    default cannot collide: two opportunities default to two different
    `Opportunity` records rather than to one shared sentinel, and a cache keyed
    on the figure's opportunity keeps telling them apart. What the default
    cannot supply is the TITLE, so it builds one with none and `label` falls
    back to the id. The live path passes the real record, title and all.

    The alternative was adding an argument to about 135 call sites that assemble
    a packet to test a parser rather than to test provenance, which is the same
    trade `document` took on 2026-09-07 and for the same reason: the strictness
    that matters is at the primitive, where a figure with no opportunity is
    impossible rather than merely discouraged.
    """
    opportunity = opportunity or source_span.Opportunity(id=opportunity_id)
    missing = MissingFields()
    baseline, fields = _baseline(paper, missing, document, opportunity)
    scenarios = _scenarios(paper, missing, document, opportunity)
    fields += _timeline(paper, missing, document, opportunity)
    fields += _next_steps(paper, document, opportunity)
    fields += _platform_layers(paper, document, opportunity)
    fields += _capabilities(paper, document, opportunity)
    fields += _milestone_ids(paper, document, opportunity)
    fields += _cost_total(paper, document, opportunity)
    fields += _payback(paper, scenarios, document, opportunity)
    return Packet(
        opportunity_id=opportunity_id,
        fields=tuple(fields),
        scenarios=scenarios,
        baseline_period=getattr(baseline, "period", None),
        baseline_basis=getattr(baseline, "basis", None),
        missing_fields=missing.entries,
    )


def _baseline(paper, missing, document, opportunity):
    """The three baseline figures, E5d preferred and E5c the per-figure fallback."""
    baseline, baseline_why = _parse(baseline_parser.parse_baseline, paper,
                                    document, opportunity)
    rows, row_why = _parse(assumption_table_parser.parse_assumptions, paper,
                           document, opportunity)
    rows = {figure.field: figure for figure in rows}
    # A PRD states its base in Appendix A's prose rather than in either table
    # shape above, so neither E5d nor E5c can read it. Empty for every
    # published paper, so the paper path is untouched.
    prd_baseline = prd_section_parsers.baseline(paper)
    fields = []
    for packet_field, absence, attribute, row_field, unit in BASELINE_SOURCES:
        stated = getattr(baseline, attribute, None) or rows.get(row_field)
        if stated is None and packet_field in prd_baseline:
            value, span = prd_baseline[packet_field]
            # E7a's `(low, high)` pair, the shape `packet_fill._one` unpacks.
            # A PRD states one number, so both endpoints are that number. A
            # bare float here passed 2170 green tests and then killed a live
            # render in `_baseline`, which is the second time this shape caught
            # this work out.
            fields.append(SourcedFigure(field=packet_field, value=(value, value),
                                        span=span, source=paper,
                                        document=document,
                                        opportunity=opportunity))
            continue
        if stated is None:
            missing.record(
                packet_field,
                _joined(baseline_why.get(absence), row_why.get(row_field)),
            )
        elif (converted := _convert(packet_field, stated, unit, paper,
                                    document, opportunity)) is None:
            missing.record(
                packet_field,
                f"the paper states {stated.value!r} here, which is not one "
                f"{unit} figure, and choosing one is not this provider's call.",
            )
        else:
            fields.append(converted)
    return baseline, fields


def _scenarios(paper, missing, document, opportunity):
    """E5b's cases under the packet's own field names, values and spans as given."""
    cases, why = _parse(scenario_table_parser.parse_scenario_cases, paper,
                        document, opportunity)
    # The PRD's own scenario table, read only for the cases E5b left without a
    # margin. Empty for every published paper, so the paper path never changes.
    # See `prd_section_parsers.scenario_margins` for why this is a second reader
    # rather than a loosening of `is_margin_label`.
    prd_margins = prd_section_parsers.scenario_margins(paper)
    scenarios = tuple(
        ScenarioCase(
            label=case.label,
            direct_uplift_usd_yr=_restate(UPLIFT, case.incremental_ebitda,
                                          paper, document, opportunity),
            margin_gain_pp=(
                _restate(GAIN, case.margin_impact_pp, paper, document,
                         opportunity)
                if case.margin_impact_pp
                else _prd_margin(case.label, prd_margins, paper, document,
                                 opportunity)
            ),
        )
        for case in cases
    )
    if not scenarios:
        missing.record(UPLIFT, why[scenario_table_parser.DOLLAR_FIELD])
    if not scenarios or not all(case.margin_gain_pp for case in scenarios):
        missing.record(
            GAIN,
            why.get(
                scenario_table_parser.MARGIN_FIELD,
                "the paper's scenario table states no margin impact for every case.",
            ),
        )
    return scenarios


# NOT a ROSTER path, deliberately. `Packet.present` intersects with `ROSTER`,
# so this raises no completeness score and moves no denominator; it is a
# deterministic answer for a field the second pass would otherwise write from
# prose, and it wins for the reason every deterministic reading wins.
NEXT_STEPS = "next_steps"
PLATFORM_LAYERS = "platform_layers"
CAPABILITIES = "target_capabilities"
MILESTONE_IDS = "milestone_ids"
# The commercial slide's PRD half (`build-plan-commercial-slide.md`), on the
# same terms as the four above: not roster paths, deterministic only, and no
# `MissingFields` entry on absence, because a document stating no §11.2 cost
# table is a paper or an older PDF and the slide simply shows no INVESTMENT
# block. A table that is PRESENT and unreadable is refused upstream by
# `prd_section_parsers.unreadable_sections`, which is where loud belongs.
COST_TOTAL = "commercial.cost_total"
PAYBACK = "commercial.payback"


def _cost_total(paper, document, opportunity):
    """§11.2's total row, labelled by column, where the document states one.

    The line items are never read (Antonio, 2026-09-22): they are QofAI's
    internal breakdown of an estimate and stay off the slide.
    """
    found = prd_section_parsers.cost_total(paper)
    if not found:
        return []
    return [SourcedFigure(field=COST_TOTAL,
                          value={"rows": found["rows"], "basis": found["basis"]},
                          span=found["span"], source=paper, document=document,
                          opportunity=opportunity)]


def _payback(paper, scenarios, document, opportunity):
    """Each case's payback from §11.2's paragraph, keyed by the case's label.

    The case names come from THIS document's scenario table, so a payback can
    only ever join a case the document states. `packet_fill._scenarios` makes
    the join, by the same lower-cased label `_prd_margin` uses.
    """
    # The case names are the document's OWN: every case the scenario reader
    # parsed, and every case `scenario_margins` reads off the PRD's scenario
    # table. The second source matters. A PRD whose dollar column the scenario
    # reader does not recognise ("Annual Value") has its cases filled later by
    # the second pass, and a payback keyed only on the deterministic cases
    # would then have nothing to join to. Both sources read the same table, so
    # no name here is one the document does not state.
    names = ([case.label for case in scenarios]
             + [name for name in prd_section_parsers.scenario_margins(paper)])
    found, _reasons = prd_section_parsers.payback(paper, names)
    if not found:
        return []
    return [SourcedFigure(field=PAYBACK, value=found,
                          span=prd_section_parsers.cost_note(paper),
                          source=paper, document=document,
                          opportunity=opportunity)]


def _next_steps(paper, document, opportunity):
    """§14's action items as a packet field, where the document states them.

    No `MissingFields` entry on absence, and that is the difference between
    this and every other reader here. `next_steps` is not a roster path and it
    has a deck standard behind it (`packet_fill.apply_templated_defaults`), so a
    document that states none is a normal, already-handled state rather than a
    gap; recording one would put a reason in the packet for a field nothing was
    waiting on.
    """
    records = prd_section_parsers.next_steps(paper)
    if not records:
        return []
    return [SourcedFigure(field=NEXT_STEPS, value=records,
                          span=prd_section_parsers.section(paper, 14).strip(),
                          source=paper, document=document,
                          opportunity=opportunity)]


def _platform_layers(paper, document, opportunity):
    """§7's functional components as a packet field, where stated.

    The twin of `_next_steps`, on the same terms and for the same reason: the
    other field `packet_fill.FALLBACK_DEFAULTS` hands to the second pass, not a
    roster path, and with a deck standard behind it, so absence is a handled
    state rather than a gap.
    """
    records = prd_section_parsers.platform_layers(paper)
    if not records:
        return []
    return [SourcedFigure(field=PLATFORM_LAYERS, value=records,
                          span=prd_section_parsers.section(paper, 7).strip(),
                          source=paper, document=document,
                          opportunity=opportunity)]


def _milestone_ids(paper, document, opportunity):
    """§12's milestone identifiers, so the deck stops inventing ordinals.

    Not a ROSTER path and not a deck ROLE: it names milestones the deck already
    draws from phase boundaries. `data_source_adapter._milestone_role` carries a
    stated id verbatim and only falls back to "M{ordinal}", which is how a PRD
    numbering its milestones M0..M4 got a deck numbering them M1..M5.
    """
    ids = prd_section_parsers.milestone_ids(paper)
    if not ids:
        return []
    return [SourcedFigure(field=MILESTONE_IDS, value=ids,
                          span=prd_section_parsers.section(paper, 12).strip(),
                          source=paper, document=document,
                          opportunity=opportunity)]


def _capabilities(paper, document, opportunity):
    """§3.1's product goals as slide 2's AFTER bullets, where stated.

    The third and last of the model-written content lists, after `next_steps`
    and `platform_layers`. Not a ROSTER path either, so it moves no score, and
    no `MissingFields` entry on absence: a document stating no §3.1 leaves the
    field to the second pass exactly as before.
    """
    bullets = prd_section_parsers.capability_bullets(paper)
    if not bullets:
        return []
    return [SourcedFigure(field=CAPABILITIES, value=bullets,
                          span=prd_section_parsers.subsection(paper, "3.1").strip(),
                          source=paper, document=document,
                          opportunity=opportunity)]


def _prd_margin(label, margins, paper, document, opportunity):
    """One case's margin from a PRD's scenario table, or None.

    Matched on the case's own label, which is the same string both readers took
    from the same first column, so the join cannot silently pair a conservative
    dollar figure with an ambitious margin. A label the PRD's table does not
    carry yields None and the case keeps no margin, which is the outcome E5b
    already treats as normal.
    """
    found = margins.get((label or "").strip().lower())
    if not found:
        return None
    value, span = found
    # E7a's `(low, high)` pair, which is the shape every consumer reads
    # (`packet_fill._stated`). A PRD states one number per case, so both
    # endpoints are that number, exactly as the paper's own single figures
    # cross.
    return SourcedFigure(field=GAIN, value=(value, value), span=span,
                         source=paper, document=document,
                         opportunity=opportunity)


def _timeline(paper, missing, document, opportunity):
    """E4's phases, which are structure rather than a figure and so do not convert.

    TWO READERS FOR ONE FIELD, added 2026-09-20, in the same shape `_baseline`
    already uses for its two: a preferred reader, a fallback, and one absence
    carrying both reasons.

    The chart reader is tried first, which keeps every run that worked before
    this byte-identical: a published paper draws its plan as a Chart.js config
    and satisfies that reader on the first call. The section reader exists
    because a PRD never will. Antonio ruled on 2026-09-17 that an uploaded PRD
    outranks the paper, and `base_document.precedence` has ordered the sources
    that way ever since; what was missing was any way for a PRD to HAVE a plan
    to outrank it with. On 2026-09-20 that gap put a 24-week timeline in front
    of a reviewer whose PRD said ten weeks, and the parser said why in its own
    words: "no <Chart> in this document is a timeline."

    The two readers do not compete. A document carries a chart timeline or a
    §6 phase table and no document in the corpus carries both, so the order is
    a statement of which is the older path rather than a precedence rule.
    Precedence between DOCUMENTS is `base_document.precedence`'s job and stays
    there: this function reads one document and never sees the other.
    """
    timeline, why = _parse(chart_timeline_parser.parse_timeline, paper,
                           document, opportunity)
    if timeline is not None:
        return [_restate(TIMELINE, timeline, paper, document, opportunity)]
    phases, phase_why = _parse(prd_section_parsers.parse_phases, paper,
                               document, opportunity)
    if phases is not None:
        return [_restate(TIMELINE, phases, paper, document, opportunity)]
    missing.record(TIMELINE, _joined(why.get(chart_timeline_parser.FIELD),
                                     phase_why.get(prd_section_parsers.FIELD)))
    return []


def _parse(parser, paper, document, opportunity):
    """Run one parser with a `MissingFields` of its own, keeping its reasons.

    Assembly names its own absences, so the recorder handed to a parser is a
    scratch one and its field names stay out of the packet.
    """
    scratch = MissingFields()
    return (parser(paper, scratch, document=document,
                   opportunity=opportunity),
            dict(scratch.entries))


def _restate(packet_field, figure, paper, document, opportunity):
    """The same value, span, document and opportunity, under the packet's own
    field name."""
    return SourcedFigure(packet_field, figure.value, figure.span, paper,
                         document, opportunity)


def _convert(packet_field, figure, unit, paper, document, opportunity):
    """A parser's stated string as a `(low, high)` pair, or None if it is not one.

    The span comes across untouched, so a converted figure is still traceable to
    the paper text that stated it.
    """
    value = (read_usd if unit == USD else read_percent)(str(figure.value))
    if value is None:
        return None
    return SourcedFigure(packet_field, value, figure.span, paper, document,
                         opportunity)


def read_percent(text):
    """`(low, high)` percent from a cell, or None unless it states exactly one.

    Two percentages in one cell are two different periods on the excerpts
    (`51.3% (full-year) / 46.7% (Q1 2026)`) rather than a range, and a second
    bare number could be a range endpoint (`35-40%`). Either way the choice is a
    judgment this provider does not make, so the cell yields nothing and the
    field goes missing with its reason. That one-percentage-and-one-number guard
    is untouched by Stage 2b's widening, and it is what keeps a spelled unit from
    dragging a figure out of prose: `12.4 percent of CY2025 administrative
    payroll` states two numbers and yields nothing.

    `read_pp` cannot stand in: it reads percentage POINTS, a different unit, and
    reading a margin impact as a margin level would be wrong by construction.
    """
    found = _PERCENT.findall(text)
    if len(found) != 1 or len(_NUMBER.findall(text)) != 1:
        return None
    return (float(found[0]), float(found[0]))


def _joined(*reasons):
    """Both sources' reasons on one absence, so a reviewer sees each failure."""
    return " ".join(reason for reason in reasons if reason)
