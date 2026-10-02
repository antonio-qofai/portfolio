"""Tests for the PRD section parsers: a plan read from an uploaded PRD.

Two halves, the same split `test_chart_timeline_parser` uses and for the same
reason. The SHAPE tests run against hand-built documents spelled out in this
file, because they cover traps the corpus does not carry: a phase table with no
week span, a section whose subsections must not end it, and two sections that
disagree. The REGRESSION test pins the exact defect this module was written for,
using the numbers measured on the live Contoso render of 2026-09-20.

Nothing here names a real client. The hand-built documents use neutral names,
and the regression test names only the numbers, which are the thing under test:
a plan of five phases over ten weeks must not come back as four over 24.
"""

import pytest

import prd_section_parsers as parsers
from prd_section_parsers import (
    FIELD,
    cross_check,
    parse_phases,
    phase_records,
    section,
)
from source_span import PAPER, MissingFields, Opportunity, SourcedFigure

# The opportunity these readings are FOR. A figure names one the way it names
# its document, so a parser test has to state one; this is about reading a
# table, not about which engagement asked for it.
OPPORTUNITY = Opportunity(id="OPP-PRD-PARSER-TEST")


def _doc(body):
    """A minimal document in the shape `document_text` yields for a .docx."""
    return body.strip("\n")


THREE_PHASES = _doc("""
# 6. Phased Scope

The build is scoped to a focused 9-week program.

| Phase | Focus | Key Deliverables |
| --- | --- | --- |
| Phase 1 Data Foundation Wks 1–3 | Get the records into a usable form | Export pipeline |
| Phase 2 Pricing Model Wks 2–6 | Train the recommendation logic | Price model |
| Phase 3 Delivery & Handover Wks 5–9 | Hand over a working system | Assembly |

# 7. Functional Requirements

Not a phase table.
""")


def test_reads_every_phase_row_in_order():
    records = phase_records(THREE_PHASES)
    assert [record["label"] for record in records] == [
        "Phase 1 Data Foundation",
        "Phase 2 Pricing Model",
        "Phase 3 Delivery & Handover",
    ]
    assert [(record["start"], record["end"]) for record in records] == [
        (1.0, 3.0), (2.0, 6.0), (5.0, 9.0)
    ]


def test_every_phase_carries_the_unit_it_was_stated_in():
    assert {record["unit"] for record in phase_records(THREE_PHASES)} == {"weeks"}


def test_overlapping_spans_are_read_as_stated():
    """The TIG PRD's phases overlap. A parser that assumed contiguity would
    silently repair a plan the document actually states."""
    records = phase_records(THREE_PHASES)
    assert records[1]["start"] < records[0]["end"]


def test_the_header_row_is_not_a_phase():
    """Skipped because it states no week span, not because of its wording. A
    parser matching on "Phase | Focus" breaks when a template renames a
    column."""
    assert all("Key Deliverables" not in record["label"]
               for record in phase_records(THREE_PHASES))


def test_the_paragraph_above_the_table_is_not_a_phase():
    """"a focused 9-week program" is the engagement's length, not a phase's."""
    assert len(phase_records(THREE_PHASES)) == 3


def test_a_document_with_no_phase_table_yields_nothing():
    assert phase_records(_doc("# 6. Phased Scope\n\nProse only.\n")) == []


def test_a_section_keeps_its_own_subsections():
    """The bug that made the first cut of this module read nothing: §11 ended at
    §11.1, so everything below it was invisible."""
    document = _doc("""
# 11. Make-or-Buy Analysis

Intro.

## 11.1 Evaluation Criteria

Criteria.

## 11.2 Make: QofAI FDE Build

### Scope of the build

Discovery (Wks 1–2): co-design.

# 12. Build Milestones & Rollout

Milestones.
""")
    body = section(document, 11)
    assert "Scope of the build" in body
    assert "Milestones." not in body


def test_a_subsection_number_does_not_open_a_section():
    """Searching for 11 must match "# 11. " and not "## 11.2 "."""
    document = _doc("## 11.2 Make: QofAI FDE Build\n\nNo parent heading.\n")
    assert section(document, 11) == ""


# --- the cross-check -------------------------------------------------------

AGREEING = _doc("""
# 6. Phased Scope

| Phase | Focus | Deliverables |
| --- | --- | --- |
| Phase 1 Alpha Wks 1–2 | Focus one | Deliverable one |
| Phase 2 Beta Wks 3–4 | Focus two | Deliverable two |

# 12. Build Milestones & Rollout

| Milestone | Target | Deliverable |
| --- | --- | --- |
| M1 | Wks 1–2 | Alpha |
| M2 | Wks 3–4 | Beta |
""")

DISAGREEING = AGREEING.replace("| M2 | Wks 3–4 | Beta |", "| M2 | Wks 3–9 | Beta |")


def test_agreeing_sections_pass_the_cross_check():
    assert cross_check(AGREEING, phase_records(AGREEING)) is None


def test_a_disagreement_refuses_the_plan_rather_than_picking_one():
    records = phase_records(DISAGREEING)
    reason = cross_check(DISAGREEING, records)
    assert reason is not None
    assert "disagree" in reason
    missing = MissingFields()
    assert parse_phases(DISAGREEING, missing, document=PAPER,
                        opportunity=OPPORTUNITY) is None


def test_a_silent_section_is_not_a_disagreement():
    """§12 is absent here. Silence must not be read as a conflicting plan."""
    only_six = AGREEING.split("# 12.")[0]
    assert cross_check(only_six, phase_records(only_six)) is None


# --- the figure ------------------------------------------------------------

def test_parse_phases_returns_a_sourced_figure_on_the_timeline_field():
    figure = parse_phases(THREE_PHASES, document=PAPER, opportunity=OPPORTUNITY)
    assert isinstance(figure, SourcedFigure)
    assert figure.field == FIELD
    assert len(figure.value) == 3


def test_absence_is_recorded_with_a_reason_rather_than_raising():
    missing = MissingFields()
    assert parse_phases(_doc("# 1. Executive Summary\n\nNo plan.\n"), missing,
                        document=PAPER, opportunity=OPPORTUNITY) is None
    assert missing.entries
    assert any(path == FIELD for path, _reason in missing.entries)


def test_an_opportunity_paper_yields_nothing_here():
    """Every published paper reaches this parser and none of them has a §6
    phase table. The paper's own parser fills the field, as it always did."""
    paper = "Total planned duration is 24 weeks from the go-ahead."
    assert parse_phases(paper, document=PAPER, opportunity=OPPORTUNITY) is None


# --- the regression --------------------------------------------------------

def test_a_ten_week_five_phase_prd_does_not_read_as_four_phases():
    """THE DEFECT, pinned to its numbers.

    On 2026-09-20 a deck generated from a ten-week PRD told a reviewer the build
    ran 24 weeks in four phases, because the PRD's plan was unreadable and the
    opportunity paper's plan won by absence. The shape below is the Contoso PRD's, with neutral labels.
    """
    document = _doc("""
# 6. Phased Scope

The build is a fixed ten-week engagement.

| Phase | Focus | Key Deliverables |
| --- | --- | --- |
| Discovery Co-design & scope Wks 1–2 | Lock scope | Profile definition |
| Phase 1 Pipeline & screening Wks 3–4 | Stand up the pipeline | Staged pipeline |
| Phase 2 Diligence & modeling Wks 5–6 | Price the offers | Pack generator |
| Phase 3 Attribution & scale Wks 7–8 | Learn from closed deals | Attribution |
| Hardening Validate & hand over Wks 9–10 | Prove the release | Acceptance testing |

# 7. Functional Requirements
""")
    records = phase_records(document)
    assert len(records) == 5, "five phases stated, five phases read"
    assert max(record["end"] for record in records) == 10.0, "a ten-week build"
    assert records[0]["label"].startswith("Discovery")
    assert records[-1]["label"].startswith("Hardening")


def test_no_client_name_is_load_bearing_in_the_parser():
    """The folder rule. A parser keyed on a company would work exactly once.

    Checks the EXECUTABLE code, with comments and docstrings stripped, because
    the rule is about behaviour. Citing which document a measurement came from
    is what a docstring is for, and `chart_timeline_parser` does the same.
    """
    import io
    import tokenize

    with open(parsers.__file__, encoding="utf-8") as handle:
        tokens = list(tokenize.generate_tokens(handle.readline))
    code = " ".join(
        token.string for token in tokens
        if token.type not in (tokenize.COMMENT, tokenize.STRING, tokenize.NL)
    ).lower()
    for name in ("contoso", "tig", "fabrikam", "ridgeline", "northwind",
                 "vantgo", "salesbox", "advisor"):
        assert name not in code, f"{name!r} must not steer the parser"


# --- the wiring (step 2) ---------------------------------------------------
# These are the tests that would have caught an inert feature. The parser above
# can be perfect and change nothing if `packet_assembly` never calls it.

import packet_assembly
from source_span import Document


PRD_DOCUMENT = Document(name="a-prd.docx", kind="docx")

CHART_PAPER = (
    "Total planned duration is 24 weeks.\n"
    "<Chart name='plan' config='"
    '{"type":"bar","data":{"labels":["Alpha","Beta"],'
    '"datasets":[{"label":"Weeks","data":[[0,6],[6,24]]}]},'
    '"options":{"indexAxis":"y","scales":{"x":{"title":{"text":"Week"}}}}}'
    "' />"
)


def test_a_prd_now_produces_a_timeline_through_assembly():
    """The whole point of step 2. Before it, this packet had no timeline."""
    packet = packet_assembly.assemble("OPP", THREE_PHASES, PRD_DOCUMENT)
    timelines = [figure for figure in packet.fields
                 if figure.field == packet_assembly.TIMELINE]
    assert len(timelines) == 1
    assert len(timelines[0].value) == 3


def test_a_paper_still_reads_its_chart_and_is_unchanged():
    """The older path must be byte-identical: every run that worked before this
    change reads a chart on the first call and never reaches the new reader."""
    packet = packet_assembly.assemble("OPP", CHART_PAPER, packet_assembly.source_span.PAPER)
    timelines = [figure for figure in packet.fields
                 if figure.field == packet_assembly.TIMELINE]
    assert len(timelines) == 1
    assert [(phase["start"], phase["end"]) for phase in timelines[0].value] == [
        (0, 6), (6, 24)
    ]


def test_a_document_with_neither_records_both_reasons():
    """One absence, both failures named, the shape `_baseline` already uses."""
    packet = packet_assembly.assemble("OPP", "Prose with no plan.", PRD_DOCUMENT)
    reasons = dict(packet.missing_fields)
    assert packet_assembly.TIMELINE in reasons
    reason = reasons[packet_assembly.TIMELINE]
    assert "<Chart>" in reason and "section 6" in reason


def test_the_prd_timeline_wins_the_merge_over_the_papers():
    """Precedence end to end: the PRD is the base, so its plan is the packet's.

    This is the defect in one assertion. The paper says 24 weeks in two phases,
    the PRD says three phases ending at week 9, and the merged packet must
    carry the PRD's.
    """
    import base_document

    prd_packet = packet_assembly.assemble("OPP", THREE_PHASES, PRD_DOCUMENT)
    paper_packet = packet_assembly.assemble(
        "OPP", CHART_PAPER, packet_assembly.source_span.PAPER)
    merged = base_document.merge_packets(iter([prd_packet, paper_packet]))
    timelines = [figure for figure in merged.fields
                 if figure.field == packet_assembly.TIMELINE]
    assert len(timelines[0].value) == 3
    assert max(phase["end"] for phase in timelines[0].value) == 9.0


# --- the economics (steps 4 and 5) -----------------------------------------

from prd_section_parsers import EBITDA, MARGIN, REVENUE, baseline, scenario_margins

SCENARIO_TABLE = _doc("""
Appendix A: Financial Model Reference

Base (TTM May 2025 – Apr 2026). Total revenue $14.8M; COGS 45.7% of revenue; EBITDA $3.72M (25.2% margin, −4.5 pp vs. prior TTM).

Impact model. Incremental EBITDA = attributable share × revenue.

| Scenario | Attributable Share | Incremental EBITDA | Margin Uplift | Interpretation |
| --- | --- | --- | --- | --- |
| Conservative | 0.5% of revenue | $74,000 | +0.50 pp | A small minority share |
| Ambitious | 2.5% of revenue | $370,000 | +2.50 pp | Roughly a third of one book |
""")


def test_scenario_margins_are_read_per_case():
    assert {k: v[0] for k, v in scenario_margins(SCENARIO_TABLE).items()} == {
        "conservative": 0.50, "ambitious": 2.50
    }


def test_points_and_pp_are_both_accepted():
    """The corpus writes the same movement as "pp" and as "pts"."""
    assert scenario_margins(SCENARIO_TABLE.replace("pp |", "pts |"))


def test_a_percent_is_not_a_margin_movement():
    """A percent is a LEVEL. `scenario_table_parser` refuses it and so does
    this; borrowing one would be wrong by construction."""
    assert scenario_margins(SCENARIO_TABLE.replace("+0.50 pp", "25.2%")
                            .replace("+2.50 pp", "27.7%")) == {}


def test_a_scenario_table_with_no_ebitda_anywhere_is_refused():
    """The gross-margin case the guard exists for. Neither the headers nor the
    prose above the table mentions EBITDA, so the pp column is not ours."""
    gross = SCENARIO_TABLE.replace("Incremental EBITDA", "Gross Contribution")
    gross = gross.replace("EBITDA $3.72M (25.2% margin, −4.5 pp vs. prior TTM)",
                          "operating profit $3.72M")
    gross = gross.replace("Impact model. Incremental EBITDA = attributable share × revenue.",
                          "Impact model. Contribution = attributable share × revenue.")
    assert scenario_margins(gross) == {}


def test_ebitda_named_only_in_the_prose_above_still_counts():
    """The Client Onboarding PRD's shape: the table's own headers never say EBITDA."""
    prose = SCENARIO_TABLE.replace("| Incremental EBITDA |", "| Annual Value |")
    assert scenario_margins(prose)


def test_the_three_baseline_figures_come_off_the_base_sentence():
    read = {path: value for path, (value, _span) in baseline(SCENARIO_TABLE).items()}
    assert read[REVENUE] == 14_800_000.0
    assert read[EBITDA] == 3_720_000.0
    assert read[MARGIN] == 25.2


def test_plain_dollar_amounts_parse_as_well_as_suffixed_ones():
    plain = SCENARIO_TABLE.replace("$14.8M", "$14,785,000").replace("$3.72M", "$780,000")
    read = {path: value for path, (value, _s) in baseline(plain).items()}
    assert read[REVENUE] == 14_785_000.0
    assert read[EBITDA] == 780_000.0


def test_a_document_with_no_base_sentence_yields_no_baseline():
    assert baseline("# 1. Executive Summary\n\nNo appendix here.\n") == {}


def test_the_margin_is_only_read_from_a_parenthetical_naming_itself():
    """Never derived from revenue and EBITDA: that needs the two to share a
    basis, and choosing that is not this module's call."""
    no_margin = SCENARIO_TABLE.replace("(25.2% margin, −4.5 pp vs. prior TTM)", "")
    assert MARGIN not in baseline(no_margin)


def test_an_uploaded_documents_margin_outranks_the_opportunity_record():
    """STEP 5, and the reason a corrected deck still read wrong.

    `target_metrics` is taken off the platform record, not from any document,
    so on 2026-09-20 a deck whose timeline had been corrected still headlined
    the record's 0.62-1.24pp beside the PRD's own scenario dollars.
    """
    import packet_assembly
    import packet_fill
    from source_span import Document

    record = {"ebitda_impact": {"min": 0.62, "max": 1.24, "unit": "pp"}}
    assert packet_fill._target_metrics(record, None) == [{"value": "0.62–1.24pp"}]

    packet = packet_assembly.assemble("OPP", SCENARIO_TABLE,
                                      Document(name="a-prd.docx", kind="docx"))
    assert packet_fill._target_metrics(record, packet) == [{"value": "0.50–2.50pp"}]


def test_a_papers_own_margins_do_not_displace_the_record():
    """Scoped to UPLOADS. Every run without an attachment is unchanged, which
    is the property that keeps this from being a behaviour change everywhere."""
    import packet_assembly
    import packet_fill
    import source_span

    record = {"ebitda_impact": {"min": 0.62, "max": 1.24, "unit": "pp"}}
    packet = packet_assembly.assemble("OPP", SCENARIO_TABLE, source_span.PAPER)
    assert packet_fill._target_metrics(record, packet) == [{"value": "0.62–1.24pp"}]


def test_a_prd_packet_survives_the_whole_fill_map_path():
    """THE TEST THAT WAS MISSING, and the render found the bug instead.

    Every figure crosses as an E7a `(low, high)` pair and `packet_fill._one`
    unpacks it. A PRD-sourced baseline built as a bare float passed 2170 green
    tests and then raised `cannot unpack non-iterable float object` inside a
    live render. Asserting the parsers' output in isolation cannot see that;
    driving the layer that consumes it can.
    """
    import packet_assembly
    import packet_fill
    from source_span import Document

    packet = packet_assembly.assemble("OPP", SCENARIO_TABLE,
                                      Document(name="a-prd.docx", kind="docx"))
    baselines = packet_fill._baseline(packet)
    assert baselines[REVENUE] == 14_800_000.0
    assert baselines[EBITDA] == 3_720_000.0
    assert baselines[MARGIN] == 25.2
    # And the scenario leaves render rather than raising.
    assert packet_fill._scenarios(packet)


def test_every_prd_sourced_figure_is_a_pair():
    """The invariant behind the bug above, asserted directly so a third reader
    added later cannot reintroduce it."""
    import packet_assembly
    from source_span import Document

    packet = packet_assembly.assemble("OPP", SCENARIO_TABLE,
                                      Document(name="a-prd.docx", kind="docx"))
    for figure in packet.fields:
        if figure.field.startswith("baseline."):
            assert isinstance(figure.value, tuple), figure.field
    for case in packet.scenarios:
        assert isinstance(case.margin_gain_pp.value, tuple)


# --- section 14, the next-steps slide --------------------------------------

from prd_section_parsers import next_steps

SECTION_14 = _doc("""
# 14. Next Steps

A one-week scoping sprint converts the ranges into a fixed quoted price.

### Interviews (Days 1–3; three, 30–60 minutes each)

Alpha Lead (60 min). Confirm the modules and the one-pager as the v1 output.

Beta Lead with the platform administrator (45 min). Confirm the edition and whether API access is entitled.

Gamma Lead (30 min). Walk through where the economics live today.

### Data requests (issued Day 1, due Day 4)

Platform entitlement and record schema. Edition and a field list. Owner: Beta Lead.

Vendor terms. Pricing quotes from up to two platforms. Owner: Alpha Lead.

Comp worksheet (redacted). The worksheet used to price an offer. Owner: Gamma Lead.

### Output (Day 5)

Acme issues a fixed quoted price per line and in total, replacing the ranges.

# 15. Appendix
""")


def test_eleven_stated_items_become_five_numbered_steps():
    """The template contract says "a numbered list (01-04)". Interviews expand
    one step each; every other group collapses to one, so a §14 of any length
    lands inside the frame."""
    steps = next_steps(SECTION_14)
    assert [step["number"] for step in steps] == ["01", "02", "03", "04", "05"]


def test_every_step_carries_the_day_its_group_states():
    """§14 measures a scoping sprint in DAYS. The schema's field is
    week-denominated, which is why every item rendered "[MISSING: week]": a
    schema mismatch, not a data gap."""
    weeks = [step["week"] for step in next_steps(SECTION_14)]
    assert weeks[:3] == ["DAYS 1–3"] * 3
    assert "DAY 1" in weeks[3] and "DAY 4" in weeks[3]
    assert weeks[4] == "DAY 5"


def test_owners_come_from_the_three_shapes_section_14_uses():
    owners = [step["owner"] for step in next_steps(SECTION_14)]
    assert owners[0] == "Alpha Lead"              # leading role, bracket-ended
    assert owners[1] == "Beta Lead"               # "X with Y" names X
    assert owners[2] == "Gamma Lead"
    assert owners[4] == "Acme"                    # the sentence's own subject


def test_a_collapsed_group_names_everything_it_holds():
    """Nothing §14 states may leave the deck just because it was grouped."""
    grouped = next_steps(SECTION_14)[3]
    assert grouped["title"] == "Three data requests"
    for name in ("Platform entitlement", "Vendor terms", "Comp worksheet"):
        assert name in grouped["body"]


def test_a_collapsed_group_is_counted_only_when_its_noun_is_plural():
    """"Three data requests" reads; "four sprint schedule" does not. Two of the
    three current PRDs close §14 with a "Sprint schedule" table rather than an
    "Output" paragraph, so both endings have to land."""
    schedule = SECTION_14.replace(
        "### Output (Day 5)\n\nAcme issues a fixed quoted price per line and in total, replacing the ranges.",
        "### Sprint schedule\n\nDay 1. Requests issued.\n\nDay 5. Quoted price delivered.")
    titles = [step["title"] for step in next_steps(schedule)]
    assert "Sprint schedule" in titles, titles
    assert "Two sprint schedule" not in titles


def test_a_single_item_group_titles_itself_from_its_own_sentence():
    """A group of one has nothing to count, so counting its heading would read
    worse than the sentence it holds."""
    assert next_steps(SECTION_14)[4]["title"].startswith("Acme issues")


def test_a_colon_delimited_role_does_not_swallow_the_title():
    """Two of the three PRDs write "VP Operations: integration walkthrough"."""
    colons = SECTION_14.replace("Alpha Lead (60 min).",
                                "Alpha Lead: integration walkthrough.")
    step = next_steps(colons)[0]
    assert step["owner"] == "Alpha Lead"
    assert "integration walkthrough" in step["title"]


def test_a_document_with_no_section_14_states_no_steps():
    """Every opportunity paper. The deck standard still applies behind this."""
    assert next_steps("Total planned duration is 24 weeks.") == []


def test_the_count_is_deterministic_across_repeated_reads():
    """THE DEFECT. The second pass produced 11 steps on one run and 8 on
    another from the same PRD, and 11 overflowed the slide by 319px."""
    assert len({len(next_steps(SECTION_14)) for _ in range(5)}) == 1


def test_next_steps_reaches_the_fill_map_without_moving_completeness():
    """Wired, and not at the cost of the render gate. `next_steps` is NOT a
    roster path, and `Packet.present` intersects with ROSTER, so this field
    cannot raise a completeness score or move its denominator."""
    import completeness_score
    import packet_assembly
    import packet_fill
    from source_span import Document

    packet = packet_assembly.assemble("OPP", SECTION_14 + "\n" + SCENARIO_TABLE,
                                      Document(name="a-prd.md", kind="markdown"))
    before = completeness_score.data_completeness(packet)
    fill, _sources = packet_fill.fill_map(company={"name": "X"}, request={},
                                          opportunity={}, packet=packet)
    assert len(fill["next_steps"]) == 5
    assert completeness_score.data_completeness(packet) == before


def test_a_long_sentence_is_cut_to_a_clause_for_the_title():
    """§14's closing paragraph is one semicolon-chained sentence, and taking the
    whole of it put a 200-character paragraph in the title slot on the first
    clean render of output-6.html."""
    long_output = SECTION_14.replace(
        "Acme issues a fixed quoted price per line and in total, replacing the ranges.",
        "Acme issues a fixed quoted price per line and in total, replacing the "
        "ranges; a confirmed start date for the build; and a short list of any "
        "scope items the interviews moved in or out of v1.")
    step = next_steps(long_output)[4]
    assert step["title"] == "Acme issues a fixed quoted price per line and in total"


def test_shortening_a_title_never_drops_what_the_document_stated():
    """The clause the title left behind leads the body."""
    long_output = SECTION_14.replace(
        "Acme issues a fixed quoted price per line and in total, replacing the ranges.",
        "Acme issues a fixed quoted price per line and in total, replacing the "
        "ranges; a confirmed start date for the build.")
    step = next_steps(long_output)[4]
    assert "replacing the ranges" in step["body"]
    assert "confirmed start date" in step["body"]


def test_a_title_with_no_break_before_the_limit_is_left_whole():
    """Truncating on a word boundary would invent an ellipsis the document does
    not have, and the full text is on the body either way."""
    unbroken = SECTION_14.replace(
        "Acme issues a fixed quoted price per line and in total, replacing the ranges.",
        "Acme delivers one single unbroken clause of considerable length without punctuation.")
    assert next_steps(unbroken)[4]["title"].startswith("Acme delivers one single")


def test_no_step_title_runs_past_the_limit_on_any_current_prd_shape():
    """Both §14 endings, across every group kind."""
    schedule = SECTION_14.replace(
        "### Output (Day 5)\n\nAcme issues a fixed quoted price per line and in total, replacing the ranges.",
        "### Sprint schedule\n\nDay 1. Requests issued.\n\nDay 5. Quoted price delivered.")
    for document in (SECTION_14, schedule):
        for step in next_steps(document):
            assert len(step["title"]) <= 64, step["title"]


# --- section 7, the platform slide -----------------------------------------

from prd_section_parsers import platform_layers

SECTION_7 = _doc("""
# 7. Functional Requirements

## 7.1 Pipeline Of Record

| ID | Requirement | Priority | Phase |
| --- | --- | --- | --- |
| FR-1.1 | Staged pipeline with a timestamp on every stage transition | Must | 1 |
| FR-1.2 | Candidate records with book size and custody mix | Must | 1 |

## 7.2 Market-Signal Screening

| ID | Requirement | Priority | Phase |
| --- | --- | --- | --- |
| FR-2.1 | Target-profile definition: revenue band, book size, geography | Must | 1 |

## 7.3 Capacity Gate & Governance

| ID | Requirement | Priority | Phase |
| --- | --- | --- | --- |
| FR-5.1 | Capacity check before a term sheet, recorded against measured capacity | Must | 2 |

# 8. Non-Functional Requirements
""")


def test_every_subsection_becomes_one_numbered_component():
    layers = platform_layers(SECTION_7)
    assert [layer["number"] for layer in layers] == ["01", "02", "03"]


def test_the_title_is_the_heading_so_capitalisation_is_never_ours_to_fix():
    """Antonio's review: slide 3's titles arrived lower-case because a model
    paraphrased prose. A PRD's headings are already written in title case."""
    titles = [layer["title"] for layer in platform_layers(SECTION_7)]
    assert titles == ["Pipeline Of Record", "Market-Signal Screening",
                      "Capacity Gate & Governance"]
    for title in titles:
        assert title[0].isupper()


def test_the_body_is_the_first_requirement_and_never_the_title_again():
    """The other review complaint: "continuous market-signal screening" whose
    description read "continuous market-signal screening scored against ..."."""
    for layer in platform_layers(SECTION_7):
        assert layer["body"]
        assert not layer["body"].lower().startswith(layer["title"].lower())


def test_the_requirement_is_found_by_its_id_shape_not_a_column_name():
    """A template that renames "Requirement" still reads."""
    renamed = SECTION_7.replace("| ID | Requirement | Priority | Phase |",
                                "| Ref | What it must do | Pri | Ph |")
    assert len(platform_layers(renamed)) == 3


def test_a_subsection_with_no_requirement_row_is_not_a_component():
    """A heading with prose under it states no capability to render."""
    prose = SECTION_7.replace(
        "| FR-2.1 | Target-profile definition: revenue band, book size, geography | Must | 1 |",
        "Prose about screening with no requirement table.")
    assert [layer["title"] for layer in platform_layers(prose)] == [
        "Pipeline Of Record", "Capacity Gate & Governance"]


def test_a_document_with_no_section_7_states_no_components():
    assert platform_layers("Total planned duration is 24 weeks.") == []


def test_the_component_count_is_deterministic_across_repeated_reads():
    """The defect: slide 3 carried six components where the PRD states five,
    because the second pass wrote them from prose on every run."""
    assert len({len(platform_layers(SECTION_7)) for _ in range(5)}) == 1


def test_platform_layers_reaches_the_fill_map_without_moving_completeness():
    import completeness_score
    import packet_assembly
    import packet_fill
    from source_span import Document

    packet = packet_assembly.assemble("OPP", SECTION_7 + "\n" + SCENARIO_TABLE,
                                      Document(name="a-prd.md", kind="markdown"))
    before = completeness_score.data_completeness(packet)
    fill, _sources = packet_fill.fill_map(company={"name": "X"}, request={},
                                          opportunity={}, packet=packet)
    assert [layer["title"] for layer in fill["platform_layers"]] == [
        "Pipeline Of Record", "Market-Signal Screening",
        "Capacity Gate & Governance"]
    assert completeness_score.data_completeness(packet) == before


def test_no_kicker_is_invented_for_a_component():
    """The second pass was stamping "three modules" on all of them, which is the
    executive summary's framing repeated rather than a label for the component."""
    import packet_assembly
    import packet_fill
    from source_span import Document

    packet = packet_assembly.assemble("OPP", SECTION_7,
                                      Document(name="a-prd.md", kind="markdown"))
    for layer in packet_fill._stated_platform_layers(packet):
        assert "kicker" not in layer


# --- section 3.1, slide 2's capability bullets -----------------------------

from prd_section_parsers import capability_bullets, subsection

SECTION_3 = _doc("""
# 3. Goals & Success Metrics

## 3.1 Product Goals

Own the market view: a continuous, scored screen of movement signals against the firm's target profile.

Instrument the funnel from day one: a staged pipeline of record with timestamps per stage.

Protect the operating constraint: an explicit capacity check tied to programme milestones.

## 3.2 Success Metrics (KPIs)

| KPI | Baseline | Target |
| --- | --- | --- |
| EBITDA margin uplift | None | +0.50 to +2.50 pts |

## 3.3 Non-Goals (Initial Release)

Replacing the external recruiter on day one: he remains a data source and a relationship channel.

A heavyweight CRM workflow build: the tool is standalone and exports summaries only.

# 4. Users & Personas
""")


def test_the_goals_are_read_from_section_3_1():
    bullets = capability_bullets(SECTION_3)
    assert len(bullets) == 3
    assert bullets[0].startswith("Own the market view")


def test_every_bullet_starts_capitalised():
    """THE COMPLAINT. Antonio, 2026-09-20: "it'd be more professional if the
    bullets on the right started capitalized because bullets on the left side
    on the today box start capitalized and bullets on the right side don't."
    They did not because the bullet carried only the clause AFTER the goal's
    separator, which is mid-sentence by construction."""
    for bullet in capability_bullets(SECTION_3):
        assert bullet[0].isupper(), bullet


def test_non_goals_never_reach_the_deck():
    """THE TRAP, and a worse failure than the lower-case letters. §3.3 sits in
    the same section in the IDENTICAL shape, so a §3-scoped reader would put
    things the PRD explicitly rules out onto a client deck as capabilities."""
    bullets = capability_bullets(SECTION_3)
    joined = " ".join(bullets)
    assert "Replacing the external recruiter" not in joined
    assert "heavyweight CRM" not in joined


def test_the_kpi_table_is_not_a_capability():
    """§3.2 is between the two and is a table."""
    assert not any("EBITDA margin uplift" in b for b in capability_bullets(SECTION_3))


def test_a_subsection_stops_at_its_sibling():
    body = subsection(SECTION_3, "3.1")
    assert "Own the market view" in body
    assert "Non-Goals" not in body and "Success Metrics" not in body


def test_a_top_level_section_number_is_not_a_subsection():
    """`section(text, 3)` would swallow 3.1, 3.2 AND 3.3, which is the whole
    reason `subsection` exists as its own reader."""
    assert "Non-Goals" in section(SECTION_3, 3)
    assert "Non-Goals" not in subsection(SECTION_3, "3.1")


def test_a_lead_in_line_is_not_a_goal():
    """"The goals are:" introduces them rather than being one."""
    with_leadin = SECTION_3.replace("## 3.1 Product Goals\n",
                                    "## 3.1 Product Goals\n\nThe goals for the initial release are:\n")
    assert not any(b.endswith("are") for b in capability_bullets(with_leadin))


def test_every_stated_goal_is_returned_not_a_chosen_few():
    """`panel_fit` measures real room and `bullet_ranking` orders them. Handing
    those two all the goals is what lets them do their job; the second pass was
    writing two or three and the choice of which was a model's."""
    assert len(capability_bullets(SECTION_3)) == 3


def test_a_document_with_no_section_3_1_states_no_capabilities():
    assert capability_bullets("Total planned duration is 24 weeks.") == []


def test_capabilities_reach_the_fill_map_under_the_bracketed_key():
    """THE KEY MATTERS, and getting it wrong is a SILENT drop.

    `packet_document._tree` looks a value up by its slot path. A LIST OF
    SCALARS carries the brackets (`target_capabilities[]`); a RECORD list is
    keyed by its container without them, which is why `next_steps` and
    `platform_layers` are bracketless and this is not.
    `second_pass.FILL_KEY` states the same mapping.

    Written under the bracketless key the value lands in the fill map and
    NOWHERE in the document. That is not an error anyone sees: on 2026-09-20
    seven bullets sat in the map, the deck kept the two a model had written,
    2231 tests passed, and only the rendered prompt showed it. This asserts the
    key the document actually reads.
    """
    import completeness_score
    import packet_assembly
    import packet_fill
    from source_span import Document

    packet = packet_assembly.assemble("OPP", SECTION_3, Document(name="a-prd.md",
                                                                kind="markdown"))
    before = completeness_score.data_completeness(packet)
    fill, _sources = packet_fill.fill_map(company={"name": "X"}, request={},
                                          opportunity={}, packet=packet)
    caps = fill["target_capabilities[]"]
    assert len(caps) == 3 and all(isinstance(c, str) for c in caps)
    # The bracketless key is the silent-drop shape and must stay empty.
    assert "target_capabilities" not in fill
    assert completeness_score.data_completeness(packet) == before


def test_the_bullets_reach_the_document_and_not_only_the_map():
    """The half the fill-map test cannot see. `packet_document.build` is what
    turns the map into the document `map_packet` reads, so this drives it."""
    import packet_assembly
    import packet_document
    import packet_fill
    from source_span import Document

    packet = packet_assembly.assemble("OPP", SECTION_3, Document(name="a-prd.md",
                                                                kind="markdown"))
    fill, sources = packet_fill.fill_map(company={"name": "X"}, request={},
                                         opportunity={}, packet=packet)
    document = packet_document.build(packet, fill=fill, sources=sources)
    assert "Own the market view" in document


# --- section 12, the milestone identifiers ---------------------------------

from prd_section_parsers import milestone_ids


def test_the_ids_are_the_documents_own():
    """THE DEFECT. The deck numbered these M1..M5 where the PRD numbers them
    M0..M4, because milestones are derived from phase boundaries and
    `_milestone_role` falls back to "M{ordinal}" when no id is stated."""
    assert milestone_ids(TEN_WEEK_PRD_SHAPE) == ["M0", "M1", "M2", "M3", "M4"]


TEN_WEEK_PRD_SHAPE = _doc("""
# 6. Phased Scope

| Phase | Focus | Key Deliverables |
| --- | --- | --- |
| Discovery Scope Wks 1–2 | Lock scope | Definitions |
| Phase 1 Alpha Wks 3–4 | Stand it up | Pipeline |
| Phase 2 Beta Wks 5–6 | Price it | Model |
| Phase 3 Gamma Wks 7–8 | Learn | Attribution |
| Hardening Handover Wks 9–10 | Prove it | Acceptance |

# 12. Build Milestones & Rollout

| Milestone | Target | Deliverable | Exit Criteria |
| --- | --- | --- | --- |
| M0 | Wks 1–2 | Discovery | Signed off |
| M1 | Wks 3–4 | Pipeline | Sourcing live |
| M2 | Wks 5–6 | Modeling | Model live |
| M3 | Wks 7–8 | Attribution | Criteria agreed |
| M4 | Wks 9–10 | Hardening | Release accepted |

# 13. Open Questions
""")


def test_an_m1_based_scheme_is_not_renumbered():
    """Two of the three current PRDs start at M1 and one starts at M0. Nothing
    normalises either."""
    m1 = TEN_WEEK_PRD_SHAPE.replace("| M0 |", "| MS-A |")
    assert milestone_ids(m1)[0] == "MS-A"


def test_the_header_row_is_not_a_milestone():
    """Skipped because its Target cell states no week span, not because of the
    word "Milestone", which a template is free to rename."""
    renamed = TEN_WEEK_PRD_SHAPE.replace("| Milestone | Target |", "| Ref | When |")
    assert milestone_ids(renamed) == ["M0", "M1", "M2", "M3", "M4"]


def test_a_document_with_no_section_12_states_no_ids():
    assert milestone_ids("Total planned duration is 24 weeks.") == []


def test_each_id_lands_on_the_phase_boundary_it_closes():
    import packet_assembly
    import packet_fill
    from source_span import Document

    packet = packet_assembly.assemble("OPP", TEN_WEEK_PRD_SHAPE,
                                      Document(name="a-prd.md", kind="markdown"))
    phases = packet_fill._phases(packet)
    stones = packet_fill._milestones(phases,
                                     packet_fill._stated_milestone_ids(packet))
    assert [(s.get("id"), s.get("position")) for s in stones] == [
        ("M0", 2), ("M1", 4), ("M2", 6), ("M3", 8), ("M4", 10)]


def test_a_plan_with_no_ids_keeps_the_ordinals_it_had():
    """The fallback is untouched: a published paper states no §12."""
    import packet_fill

    phases = [{"label": "A", "start": 0.0, "end": 6.0, "unit": "weeks"},
              {"label": "B", "start": 6.0, "end": 24.0, "unit": "weeks"}]
    stones = packet_fill._milestones(phases)
    assert all(s.get("id") is None for s in stones)


def test_a_shared_boundary_does_not_shift_later_ids():
    """The id belongs to the phase's ORDINAL POSITION, not to the count of
    milestones kept, so two phases ending on the same week do not renumber
    everything after them."""
    import packet_fill

    phases = [{"label": "A", "start": 0.0, "end": 4.0, "unit": "weeks"},
              {"label": "B", "start": 2.0, "end": 4.0, "unit": "weeks"},
              {"label": "C", "start": 4.0, "end": 9.0, "unit": "weeks"}]
    stones = packet_fill._milestones(phases, ["M1", "M2", "M3"])
    assert [(s.get("id"), s.get("position")) for s in stones] == [("M1", 4), ("M3", 9)]


def test_the_override_is_recorded_with_the_value_it_displaced():
    """PLAN CRITERION 5, which was not met. Antonio ruled that a PRD displacing
    a platform value wins SILENTLY and is LOGGED, and the logging half was
    missing: slide 2's headline impact moved from the opportunity record's range
    to the PRD's with nothing anywhere saying so.

    The displaced value travels in the origin string, so section 8's sources
    ledger carries both. Reviewer-facing and never rendered, which is the
    channel `conflicts` and `set_aside` already use.
    """
    import packet_assembly
    import packet_fill
    from source_span import Document

    packet = packet_assembly.assemble("OPP", SCENARIO_TABLE,
                                      Document(name="a-prd.md", kind="markdown"))
    record = {"ebitda_impact": {"min": 0.62, "max": 1.24, "unit": "pp"}}
    fill, sources = packet_fill.fill_map(company={"name": "X"}, request={},
                                         opportunity=record, packet=packet)
    assert fill["target_metrics"] == [{"value": "0.50–2.50pp"}]
    origin = sources["target_metrics"]
    assert "displaced" in origin
    assert "0.62–1.24pp" in origin, origin


def test_nothing_is_claimed_displaced_when_the_record_stated_nothing():
    """An override needs something to override. A record with no range is not a
    displacement and must not be logged as one."""
    import packet_assembly
    import packet_fill
    from source_span import Document

    packet = packet_assembly.assemble("OPP", SCENARIO_TABLE,
                                      Document(name="a-prd.md", kind="markdown"))
    fill, sources = packet_fill.fill_map(company={"name": "X"}, request={},
                                         opportunity={}, packet=packet)
    assert "displaced" not in sources["target_metrics"]


def test_a_run_with_no_upload_records_the_record_as_the_source():
    """Unchanged for every run without an attachment, which is the property
    that keeps this from being a behaviour change everywhere."""
    import packet_assembly
    import packet_fill
    import source_span

    packet = packet_assembly.assemble("OPP", SCENARIO_TABLE, source_span.PAPER)
    record = {"ebitda_impact": {"min": 0.62, "max": 1.24, "unit": "pp"}}
    fill, sources = packet_fill.fill_map(company={"name": "X"}, request={},
                                         opportunity=record, packet=packet)
    assert fill["target_metrics"] == [{"value": "0.62–1.24pp"}]
    assert sources["target_metrics"] == packet_fill.OPPORTUNITY_DETAILS
