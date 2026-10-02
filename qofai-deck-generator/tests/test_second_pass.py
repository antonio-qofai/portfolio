"""Tests for the second pass's precedence and merge half (E11 Stage 2).

The load-bearing claim of this window is not "the model reads well". It is that
the deterministic pass ALWAYS wins, that a value never enters without a verified
span, and that an absence still renders as a marker. Those are what is asserted
here, and they are asserted against the real `packet_assembly`, the real
`packet_document`, the real `map_packet` / `prompt_assembler` / `coverage_guard`,
with only the model itself faked.

Three corpora, doing three different jobs, and the difference matters:

  * The COMMITTED excerpts under `data-provider/fixtures/` are where precedence
    is tested, because there the parsers really do source values and a competing
    second-pass value has something to lose to. Three of the eight also carry a
    genuine roster gap (`no-timeline-chart` at 0.83, `scenario-rows-multi-table`
    and `validated-assumption-table` at 0.83, `sparse-no-scenario-table` at
    0.67), so the roster upside is measurable on committed data rather than only
    on a hand-built file.
  * The hand-built prose paper under `tests/fixtures/second-pass/` is where the
    prose paths are tested, because the committed excerpts dropped their prose at
    capture (`prose_between_headings_dropped: true` in every one of them).
  * The two placeholder clients in `test_packet_fill` are where the seam is
    measured end to end, so `role_coverage` before and after is read off the same
    harness Stage 1 used.
"""

import json

import completeness_score
import packet_assembly
import packet_document
import packet_fill
import paper_extraction
import second_pass
from coverage_guard import COVERAGE_MAP, EXCLUSION_ALLOWLIST, check_coverage
from data_source_adapter import _section_yaml, _split_sections, map_packet
from packet_assembly import ROSTER, assemble
from packet_document import ORIGINS, SOURCED, UNSOURCEABLE
from prompt_assembler import MISSING_MARKER, assemble_prompt
import base_document
import source_span
from base_document import PAPER_KIND
from source_span import PAPER, Opportunity

# The opportunity a reading is FOR (item 15). The fakes below default to it the
# way they default to `PAPER`, so a test about the pass itself states one once
# rather than at every call.
OPPORTUNITY = Opportunity(id="OPP-ONE")

from template_loader import load_template
from test_packet_fill import CLIENTS
from test_paper_extraction import (answer, attributed, field, item, paper,
                                   platform_paper, prose_paper, range_paper)

TEMPLATE_PATH = "templates/proposal-template.md"


def extractor_returning(payload, record=None):
    """A fake second pass: the real verifier, driven by a scripted answer.

    Deliberately NOT a stub returning ready-made `Extracted` records. The
    verification is the thing under test everywhere else in this file, so every
    fake answer here goes through `paper_extraction.read_response` exactly as a
    live answer would, and a test that scripts an unverifiable value gets the
    same refusal a live run would get.
    """
    calls = record if record is not None else []

    def extract(text, requested, labels=(), document=PAPER,
                opportunity=OPPORTUNITY, description=""):
        calls.append((requested, labels))
        # Restamped for whichever subject the pass was handed, because a fake
        # stands in for a model that was TOLD one and has to answer as one.
        answered = attributed(payload, opportunity)
        return paper_extraction.read_response(
            text, requested,
            type("M", (), {"content": [type("B", (), {"type": "text",
                                                      "text": json.dumps(answered)})()]})(),
            document=document, opportunity=opportunity,
        )

    return extract


def raising_extractor(error):
    def extract(text, requested, labels=(), document=PAPER,
                opportunity=OPPORTUNITY, description=""):
        raise error

    return extract


# ---------------------------------------------------------------------------
# The precedence rule, which is the load-bearing decision.
# ---------------------------------------------------------------------------

def test_a_paper_the_first_pass_read_whole_asks_the_second_for_no_roster_field():
    """`scenario-rows-canonical` scores 1.00 deterministically. Every roster path
    is therefore absent from the request, and the model is never told a figure it
    could contradict."""
    text = paper("scenario-rows-canonical")
    packet = assemble("OPP", text)
    assert completeness_score.data_completeness(packet) == 1.0

    requested = second_pass.absent(packet, packet_fill.fill_map(packet=packet)[0])

    assert set(requested).isdisjoint(ROSTER)
    assert "commercial.scenarios" not in requested


def test_a_second_pass_figure_never_overwrites_a_parsers_and_the_parsers_stands():
    """The rule, at the merge. The fake returns a revenue figure that verifies
    perfectly against the paper -- real span, real notation -- for a field the
    parser already sourced. It is discarded and both values are recorded."""
    text = paper("scenario-rows-canonical")
    packet = assemble("OPP", text)
    before = {figure.field: figure.value for figure in packet.fields}
    span = "Q1-2026 actual revenue of $22.7M"
    assert span in text

    result = second_pass.run(text, packet, {}, extractor=extractor_returning(
        answer(field(packet_assembly.REVENUE,
                     item(span, "Financial Analysis > Current State",
                          value="$22.7M")))
    ))

    after = {figure.field: figure.value for figure in result.packet.fields}
    assert after[packet_assembly.REVENUE] == before[packet_assembly.REVENUE]
    assert result.merged == ()
    assert result.dropped and packet_assembly.REVENUE == result.dropped[0][0]
    assert "fills absences only" in result.dropped[0][1]


def test_the_merge_checks_precedence_again_even_when_the_request_did_not():
    """The second check, exercised on its own.

    The two checks are not redundant, and this is the case that proves it. The
    request-time check catches a path that was already present when the request
    was composed -- so a test that only goes through `run` never reaches the
    merge-time one, which is exactly what a fail-first withdrawal of the merge
    check showed on 2026-08-18: the test still passed with the check removed.

    Here the extractor is handed the path as REQUESTED, which is what happens
    when a field is absent at request time, and the merge then meets a packet
    where it is present. Only the check inside `_merge_packet` can refuse this.
    """
    text = paper("scenario-rows-canonical")
    packet = assemble("OPP", text)
    assert packet_assembly.REVENUE in packet.present
    span = "Q1-2026 actual revenue of $22.7M"

    # Requested, verified, and genuinely wanting to be merged.
    extraction = paper_extraction.read_response(
        text, [packet_assembly.REVENUE],
        type("M", (), {"content": [type("B", (), {
            "type": "text",
            "text": json.dumps(answer(field(
                packet_assembly.REVENUE,
                item(span, "Financial Analysis > Current State",
                     value="$22.7M")))),
        })()]})(),
        document=PAPER, opportunity=OPPORTUNITY,
    )
    assert extraction.records[packet_assembly.REVENUE]

    merged, disagreements, flagged = [], [], []
    after = second_pass._merge_packet(packet, extraction, merged, disagreements,
                                      flagged)

    standing = {f.field: f.value for f in packet.fields}[packet_assembly.REVENUE]
    assert {f.field: f.value for f in after.fields}[packet_assembly.REVENUE] == standing
    assert merged == []
    assert disagreements and disagreements[0][0] == packet_assembly.REVENUE
    assert "The deterministic parser's stands" in disagreements[0][1]


def test_a_disagreement_records_both_values_and_never_lands_in_gaps():
    """A field both passes produced is not a gap. `gaps` is what
    `_gap_flagged_roles` turns into the deck's "unconfirmed" marker, so putting a
    disagreement there would flag a role on the deck over a field that is not
    missing. It goes in a block of its own instead."""
    text = paper("validated-assumption-table")
    packet = assemble("OPP", text)
    case = packet.scenarios[0]
    span, uplift, _pp = _row(text, packet, 0)

    result = second_pass.run(text, packet, {}, extractor=extractor_returning(
        answer(field("commercial.scenarios",
                     item(span, "Financial Analysis > Projected Impact",
                          name=case.label, direct_uplift_usd_yr=uplift)))
    ))

    assert result.disagreements
    path, note = result.disagreements[0]
    assert path == packet_assembly.UPLIFT
    assert "The deterministic parser's stands" in note
    block = second_pass.ledger(result)
    assert block["disagreements"]
    assert "gaps" not in block


def test_the_roster_stays_at_six_fields_and_the_floor_stays_at_seventy():
    """The stop condition that would sink the window if missed. Filling a roster
    field is correct; adding one moves the denominator `data_completeness`
    divides by and no live deck ever renders again."""
    assert len(ROSTER) == 6
    assert set(ROSTER) == {
        "baseline.revenue_ttm_usd", "baseline.adjusted_ebitda_usd",
        "baseline.adjusted_ebitda_pct",
        "commercial.scenarios[].direct_uplift_usd_yr",
        "commercial.scenarios[].margin_gain_pp", "timeline.phases",
    }
    # Nothing this pass can fill is outside the roster AND in the denominator:
    # the denominator IS the roster, and the scope table adds no member to it.
    for slot in paper_extraction.SCOPE:
        assert slot.path not in ROSTER or slot.path in (
            packet_assembly.REVENUE, packet_assembly.EBITDA,
            packet_assembly.MARGIN, packet_assembly.TIMELINE,
        )


def test_no_extractor_means_no_second_pass_and_no_call():
    """The seam is off unless a caller hands the pass over, which is what keeps
    the whole suite off the network by construction rather than by discipline."""
    text = paper("no-timeline-chart")
    packet = assemble("OPP", text)

    result = second_pass.run(text, packet, {})

    assert result.packet is packet
    assert result.requested == () and result.merged == ()
    assert second_pass.ledger(result) is None


def test_a_failing_pass_degrades_to_the_deterministic_packet_with_its_reason():
    """Additive by construction: everything the first pass produced is already in
    hand, so an outage costs the second pass's additions and not the deck. The
    gate still reads the deterministic roster, so this cannot let a thin packet
    through."""
    text = paper("no-timeline-chart")
    packet = assemble("OPP", text)

    result = second_pass.run(text, packet, {}, extractor=raising_extractor(
        RuntimeError("no credentials")
    ))

    assert result.packet is packet
    assert completeness_score.data_completeness(result.packet) == round(5 / 6, 4) \
        or completeness_score.data_completeness(result.packet) == 5 / 6
    assert result.merged == ()
    assert any("did not complete" in why for _path, why in result.dropped)
    assert "no credentials" in second_pass.ledger(result)["not_filled"][0]["reason"]


# ---------------------------------------------------------------------------
# The roster upside, on committed data.
# ---------------------------------------------------------------------------

def test_a_paper_stating_its_plan_in_a_table_reaches_one_from_five_of_six():
    """`no-timeline-chart` is a committed excerpt at 0.83: it carries no
    `<Chart>` that is a timeline, so `timeline.phases` is absent, and it states
    the same plan in a markdown table the chart parser does not read. That is
    exactly what this pass is for, and the gain is measured on committed data
    rather than on a hand-built file."""
    text = paper("no-timeline-chart")
    packet = assemble("OPP", text)
    assert packet_assembly.TIMELINE not in packet.present
    assert completeness_score.data_completeness(packet) == 5 / 6

    rows = [line for line in text.splitlines()
            if line.startswith("| **Phase")]
    assert len(rows) == 3
    result = second_pass.run(text, packet, {}, extractor=extractor_returning(
        answer(field(packet_assembly.TIMELINE, *(
            item(row, "Implementation Approach > Timeline",
                 label=row.split("|")[1].strip().strip("*"),
                 start=start, end=end, unit="months")
            for row, start, end in zip(rows, ("1", "4", "10"), ("3", "9", "18"))
        )))
    ))

    assert packet_assembly.TIMELINE in result.packet.present
    assert completeness_score.data_completeness(result.packet) == 1.0
    phases = [figure for figure in result.packet.fields
              if figure.field == packet_assembly.TIMELINE][0]
    assert [phase["label"] for phase in phases.value] == [
        "Phase 1: CRM Foundation", "Phase 2: Cross-Sell Intelligence",
        "Phase 3: Predictive Analytics",
    ]
    assert {phase["unit"] for phase in phases.value} == {"months"}
    # The figure's own span is a real slice of the paper covering every phase.
    assert phases.span in text


def test_the_margin_column_a_table_did_not_carry_fills_case_by_case():
    """`validated-assumption-table` reads 0.83: its scenario table carries the
    dollars and no percentage-point column, so `margin_gain_pp` is the one roster
    field absent. The case SET stays the parser's; only the leaf it left None is
    filled, matched on the case's own name."""
    text = paper("validated-assumption-table")
    packet = assemble("OPP", text)
    assert packet_assembly.GAIN not in packet.present
    assert all(case.margin_gain_pp is None for case in packet.scenarios)
    lines = {case.label: [line for line in text.splitlines()
                          if case.label in line][0]
             for case in packet.scenarios}
    # A percentage-point figure the paper really states, quoted from its own line.
    scripted = [
        item(lines[label], "Financial Analysis > Projected Impact", name=label,
             direct_uplift_usd_yr=uplift, margin_gain_pp=pp)
        for label, uplift, pp in _pp_rows(text, packet)
    ]

    result = second_pass.run(text, packet, {}, extractor=extractor_returning(
        answer(field("commercial.scenarios", *scripted))
    ))

    assert len(result.packet.scenarios) == len(packet.scenarios)
    assert [case.label for case in result.packet.scenarios] == \
        [case.label for case in packet.scenarios]
    # The parser's dollars are untouched; only the absent leaf moved.
    for before, after in zip(packet.scenarios, result.packet.scenarios):
        assert after.direct_uplift_usd_yr.value == before.direct_uplift_usd_yr.value


def _row(text, packet, index):
    """One case's own table line, the dollar token on it, and its pp token."""
    import re
    case = packet.scenarios[index]
    line = [row for row in text.splitlines() if case.label in row][0]
    money = re.search(r"\$[\d.,]+[KMB]?", line)
    pp = re.search(r"[+-]?\d+(?:\.\d+)?\s*pp\b", line)
    return line, money.group(0), (pp.group(0) if pp else None)


def _pp_rows(text, packet):
    """Each case's own line, and a pp figure literally present on it.

    Written this way rather than hardcoded so the test states what it needs from
    the fixture instead of restating the fixture's numbers.
    """
    import re
    rows = []
    for case in packet.scenarios:
        line = [row for row in text.splitlines() if case.label in row][0]
        pp = re.search(r"[+-]?\d+(?:\.\d+)?\s*pp\b", line)
        money = re.search(r"\$[\d.,]+[KMB]?", line)
        if pp and money:
            rows.append((case.label, money.group(0), pp.group(0)))
    return rows


def test_a_prose_only_paper_goes_from_nothing_to_a_full_roster():
    """The hand-built prose paper reads 0.00 through every parser in this repo,
    verified in its own `_fixture` block. Everything it states is material the
    first pass genuinely could not reach."""
    text = prose_paper()
    packet = assemble("OPP", text)
    assert completeness_score.data_completeness(packet) == 0.0

    result = second_pass.run(text, packet, {},
                             extractor=extractor_returning(_full_answer(text)))

    assert completeness_score.data_completeness(result.packet) == 1.0
    assert set(result.packet.present) == set(ROSTER)


def _full_answer(text):
    rows = [line for line in text.splitlines() if line.startswith("| **Phase")]
    cases = [line for line in text.splitlines()
             if line.startswith("| Conservative") or line.startswith("| Base Case")
             or line.startswith("| Optimistic")]
    return answer(
        field(packet_assembly.REVENUE,
              item("The company reported LTM revenue of $84.0M",
                   "Financial Analysis > Current State", value="$84.0M")),
        field(packet_assembly.EBITDA,
              item("adjusted EBITDA of $12.6M",
                   "Financial Analysis > Current State", value="$12.6M")),
        field(packet_assembly.MARGIN,
              item("adjusted EBITDA margin of 15.0%",
                   "Financial Analysis > Current State", value="15.0%")),
        field("commercial.scenarios", *(
            item(line, "Financial Analysis > Projected Impact",
                 name=line.split("|")[1].strip(),
                 direct_uplift_usd_yr=line.split("|")[2].strip(),
                 margin_gain_pp=line.split("|")[3].strip())
            for line in cases
        )),
        field(packet_assembly.TIMELINE, *(
            item(row, "Implementation Approach > Timeline",
                 label=row.split("|")[1].strip().strip("*"),
                 start=start, end=end, unit="months")
            for row, start, end in zip(rows, ("1", "3", "6"), ("3", "6", "12"))
        )),
    )


# ---------------------------------------------------------------------------
# The document half: records, markers, and what an absence still says.
# ---------------------------------------------------------------------------

def test_an_applicable_leaf_the_pass_did_not_get_keeps_its_key_and_marks():
    """Stage 1 made an omitted key say NOTHING on the deck and an empty key still
    mark. Those are two different claims and the merge has to make the right one:
    a metric with a value and no label was not obtained, and that is an absence
    the deck should show."""
    text = prose_paper()
    packet = assemble("OPP", text)
    result = second_pass.run(text, packet, {}, extractor=extractor_returning(
        answer(field("today_metrics",
                     item("Average quote turnaround runs 9 days",
                          "Financial Analysis > Current State", value="9 days")))
    ))
    fill, sources, result = second_pass.merge_fill({}, {}, result)

    assert fill["today_metrics"] == [{"value": "9 days", "label": ""}]
    assert sources["today_metrics"] == second_pass.SECOND_PASS
    assert packet_document._is_absent(fill, "today_metrics[].label")
    assert not packet_document._is_absent(fill, "today_metrics[].value")


def test_a_filled_document_path_is_never_overwritten_either():
    """The precedence rule checked the SECOND time, against the fill map as it
    actually stands rather than as it stood when the request was composed."""
    text = prose_paper()
    packet = assemble("OPP", text)
    result = second_pass.run(text, packet, {}, extractor=extractor_returning(
        answer(field("target_metrics",
                     item("delivery reaches 92% at steady state",
                          "Financial Analysis > Projected Impact", value="92%")))
    ))
    standing = [{"value": "1.2-6.0pp"}]

    fill, _sources, result = second_pass.merge_fill(
        {"target_metrics": standing}, {}, result
    )

    assert fill["target_metrics"] == standing
    assert any(path == "target_metrics" for path, _note in result.disagreements)


def test_a_phase_summary_lands_on_its_own_phase_and_never_on_position():
    """Matched on the label the first pass already read. A summary on the wrong
    bar of the plan is the same class of error as a figure in the wrong field, and
    a phase with nothing keeps an empty summary so the deck marks it."""
    text = prose_paper()
    packet = assemble("OPP", text)
    result = second_pass.run(text, packet, {}, extractor=extractor_returning(
        answer(field("build_summary.phases[].summary",
                     item("| **Phase 2: Pricing Rules** | Months 3-6 | Move "
                          "pricing out of the spreadsheet and into the rules "
                          "engine |",
                          "Implementation Approach > Timeline",
                          label="Phase 2: Pricing Rules",
                          summary="Move pricing out of the spreadsheet and "
                                  "into the rules engine")))
    ))
    phases = [{"label": "Phase 1: Scheduling Foundation"},
              {"label": "Phase 2: Pricing Rules"}]

    fill, _sources, result = second_pass.merge_fill(
        {"build_summary.phases": phases}, {}, result
    )

    assert fill["build_summary.phases"][1]["summary"].startswith("Move pricing")
    assert fill["build_summary.phases"][0]["summary"] == ""
    # `_is_absent` reads the FIRST record of a record list by its own documented
    # rule, so a list whose first phase has no summary still names the path in
    # section 8 gaps. That is the honest reading of a partially-filled list and
    # it is why the empty key matters: the deck marks phase 1 and renders phase
    # 2, and the packet says the field is not fully sourced.
    assert packet_document._is_absent(fill, "build_summary.phases[].summary")


def test_a_summary_whose_label_matches_no_phase_is_dropped_rather_than_placed():
    text = prose_paper()
    packet = assemble("OPP", text)
    result = second_pass.run(text, packet, {}, extractor=extractor_returning(
        answer(field("build_summary.phases[].summary",
                     item("| **Phase 3: Margin Visibility** | Months 6-12 | "
                          "Close the loop with job-level margin reporting at "
                          "close |",
                          "Implementation Approach > Timeline",
                          label="Phase 3: Margin Visibility",
                          summary="Close the loop with job-level margin "
                                  "reporting at close")))
    ))

    fill, _sources, _result = second_pass.merge_fill(
        {"build_summary.phases": [{"label": "Phase 1: Scheduling Foundation"}]},
        {}, result,
    )

    assert "summary" not in fill["build_summary.phases"][0]


# ---------------------------------------------------------------------------
# The reviewer's channel: provenance, section headings, and the placement flag.
# ---------------------------------------------------------------------------

def test_every_filled_value_carries_its_section_and_its_span_into_section_eight():
    """Span verification closes fabrication; misattribution verifies fine and no
    check here catches it. So the heading and the quoted span go on the artifact
    and a human looks, which is the only defence there is."""
    text = prose_paper()
    packet = assemble("OPP", text)
    result = second_pass.run(text, packet, {}, extractor=extractor_returning(
        answer(field(packet_assembly.REVENUE,
                     item("The company reported LTM revenue of $84.0M",
                          "Financial Analysis > Current State", value="$84.0M")))
    ))

    entry = second_pass.ledger(result)["filled"][0]
    assert entry["field"] == packet_assembly.REVENUE
    assert entry["section"] == "Financial Analysis > Current State"
    assert entry["span"] == "The company reported LTM revenue of $84.0M"
    assert "review" not in entry


def test_a_current_state_figure_read_under_a_projection_heading_is_flagged():
    """Flagged for review, never dropped and never corrected: it may well be
    right, and no heuristic here gets to decide that."""
    text = prose_paper()
    packet = assemble("OPP", text)
    result = second_pass.run(text, packet, {}, extractor=extractor_returning(
        answer(field(packet_assembly.EBITDA,
                     item("adjusted EBITDA of $12.6M",
                          "Financial Analysis > Projected Impact",
                          value="$12.6M")))
    ))

    assert result.flagged
    assert "review" in second_pass.ledger(result)["filled"][0]
    # Flagged, and still merged: the reviewer decides, not this module.
    assert packet_assembly.EBITDA in result.packet.present


def test_a_scenario_read_under_projected_impact_is_not_flagged():
    """A flag that always fires is a flag a reviewer stops reading. Only the
    three current-state figures can be wrong about their own heading."""
    text = prose_paper()
    packet = assemble("OPP", text)
    result = second_pass.run(text, packet, {}, extractor=extractor_returning(
        answer(field("commercial.scenarios",
                     item("| Base Case | $2.6M | 3.1pp |",
                          "Financial Analysis > Projected Impact",
                          name="Base Case", direct_uplift_usd_yr="$2.6M",
                          margin_gain_pp="3.1pp")))
    ))

    assert result.flagged == ()


def test_the_ledger_collapses_a_span_that_would_break_the_packets_own_yaml():
    """The packet writes a scalar as one quoted line and `_section_yaml` parses it
    back, so a span carrying a newline would break section 8 and take `gaps` with
    it. The verification ran against the untouched original; this is presentation.
    """
    text = prose_paper()
    packet = assemble("OPP", text)
    span = ("Average quote turnaround runs 9 days against an industry norm "
            "nearer three, and\non-time delivery sits at 71% across the last "
            "four quarters.")
    assert span in text and "\n" in span
    result = second_pass.run(text, packet, {}, extractor=extractor_returning(
        answer(field("today_pain_points",
                     item(span, "Financial Analysis > Current State",
                          value="on-time delivery sits at 71%")))
    ))
    fill, sources, result = second_pass.merge_fill({}, {}, result)

    entry = second_pass.ledger(result)["filled"][0]
    assert "\n" not in entry["span"]
    markdown = packet_document.build(
        packet=packet, fill=fill, sources=sources,
        meta={"generated_at": "2026-08-18T00:00:00Z", "company_id": "c"},
        second_pass=second_pass.ledger(result),
    )
    ledger = map_packet(markdown, {"project": "p"})
    # Section 8 still parses, so `gaps` survived the span going in beside it.
    assert ledger["_gaps"]


# ---------------------------------------------------------------------------
# The seam, end to end, with the guard running.
# ---------------------------------------------------------------------------

def _seam(client_key, payload, text=None, writer=None, description=None):
    """One placeholder client's whole document, the way the provider builds it.

    `text` overrides the client's committed excerpt, so the same harness measures
    a hand-built prose paper without a second copy of the build sequence.
    `writer` turns on the SECOND, separate LLM leg (E11 Stage 2c): the writing
    pass, which produces the deck's framing sentences under the generated rules
    rather than quotations under span discipline. Off, and every framing line is
    the deck standard it has always been.
    """
    client = CLIENTS[client_key]
    text = paper(client["shape"]) if text is None else text
    packet = assemble(client["opportunity"]["id"], text)
    fill, _sources = packet_fill.fill_map(
        company=client["company"], request=client["request"],
        opportunity=client["opportunity"], packet=packet,
    )
    result = second_pass.run(text, packet, fill,
                             extractor=extractor_returning(payload))
    fill, sources = packet_fill.fill_map(
        company=client["company"], request=client["request"],
        opportunity=client["opportunity"], packet=result.packet,
    )
    fill, sources, result = second_pass.merge_fill(fill, sources, result)
    # E11 Stage 2c's last fill step: the deck standard goes back on whichever of
    # `platform_layers` / `next_steps` the paper did not carry, and `fell_back`
    # is what section 8 reports as templated rather than paper-sourced.
    fill, sources, fell_back = packet_fill.apply_templated_defaults(fill, sources)
    written = second_pass.write(
        text,
        client["opportunity"]["description"] if description is None else description,
        writer=writer,
    )
    framing, sources = second_pass.merge_written(sources, written)

    def build(coverage=None):
        return packet_document.build(
            client["request"], packet_fill.record_unit_mismatch(result.packet),
            fill, sources,
            {"generated_at": "2026-08-18T00:00:00Z",
             "company_id": client["company"]["id"]},
            copy=packet_fill.copy_lines(
                company=client["company"], request=client["request"],
                project=client["project"], opportunity=client["opportunity"],
                written=framing,
            ),
            opportunities=packet_fill.opportunity_record(client["opportunity"]),
            # Section 2 repeats (item 15). One entry, carrying this
            # opportunity's own fill map and its own two copy lines.
            sections=[{
                "fill": fill,
                "copy": packet_fill.opportunity_copy(client["opportunity"],
                                                     framing),
            }],
            role_coverage=coverage,
            second_pass=second_pass.ledger(result),
            templated_fallbacks=fell_back,
            derived=packet_fill.DERIVED_PATHS,
            generated=second_pass.written_ledger(written),
        )

    coverage = packet_fill.role_coverage(map_packet(build(), client["request"]))
    markdown = build(coverage=coverage)
    placeholder_map = map_packet(markdown, client["request"])
    prompt = assemble_prompt(load_template(TEMPLATE_PATH), placeholder_map)
    check_coverage(markdown, load_template(TEMPLATE_PATH), placeholder_map, prompt)
    return markdown, placeholder_map, prompt, coverage


def _slide_two_answer(text):
    """The slide 2 narrative, quoted from a committed excerpt's own prose.

    The committed excerpts dropped prose between headings at capture, so what is
    quotable in them is the baseline sentences the capture kept. That is the
    point of the test: it uses what is actually there rather than what would be
    convenient, and it is why the prose paths are exercised on the hand-built
    paper instead.
    """
    lines = [line.strip() for line in text.splitlines()
             if line.strip() and not line.startswith(("#", "|", "<", "["))
             and len(line.strip()) > 60]
    return answer(field("today_pain_points", *(
        item(line, "Financial Analysis > Current State", value=line)
        for line in lines[:2]
    )))


def test_role_coverage_rises_when_the_second_pass_fills_a_narrative_role():
    """The number that tells the truth about this work. Stage 1 left it at 0.39,
    seven of eighteen; `today_pain_bullets` is one of the roles the prose pass can
    reach and it carries here."""
    text = paper(CLIENTS["one"]["shape"])
    _markdown, placeholder_map, prompt, coverage = _seam(
        "one", _slide_two_answer(text)
    )

    assert coverage > 0.39
    assert placeholder_map["today_pain_bullets"]
    assert MISSING_MARKER.format(role="today_pain_bullets") not in prompt


def test_a_role_the_second_pass_could_not_fill_still_renders_its_marker():
    """An absent field is a normal outcome and it stays visible. Nothing here
    turns a gap into silence.

    `today_metric_1` used to be the second assertion here and no longer belongs:
    since 2026-08-19 it is filled deterministically from the baseline EBITDA
    margin, so it is not absent whether the second pass runs or not.
    `after_capability_bullets` is the role this fixture leaves unfilled, and it
    is the one the case is about.
    """
    text = paper(CLIENTS["one"]["shape"])
    _markdown, placeholder_map, prompt, _coverage = _seam(
        "one", _slide_two_answer(text)
    )

    assert MISSING_MARKER.format(role="after_capability_bullets") in prompt
    # And the role that stopped being absent really is filled, rather than
    # having quietly become an empty string nobody flags.
    assert placeholder_map["today_metric_1"]
    assert MISSING_MARKER.format(role="today_metric_1") not in prompt


def test_the_two_placeholder_clients_share_no_second_pass_value():
    """The anti-hardcoding check, on the new channel. Each client's values come
    from its own paper and nothing crosses."""
    one, two = CLIENTS["one"], CLIENTS["two"]
    first, _pm, _p, _c = _seam("one", _slide_two_answer(paper(one["shape"])))
    second, _pm, _p, _c = _seam("two", _slide_two_answer(paper(two["shape"])))

    for name in ("Test Subject One", "First Test Capital"):
        assert name not in second
    for name in ("Test Subject Two", "Second Test Capital"):
        assert name not in first


# ---------------------------------------------------------------------------
# E11 Stage 2e — a stated range reaches the deck instead of being dropped.
# ---------------------------------------------------------------------------

# The three cases the range fixture states in prose, and the exact fragment the
# model is scripted to quote for each leaf. `to` is used on a pp figure (the live
# corpus writes it 30 times) and never on a dollar figure, because `_money`
# refuses `to` as residue and the record would drop whole for want of a required
# leaf, which is the residue check's subject rather than this stage's.
RANGE_CASES = (
    ("Conservative", "0.9–1.9pp", "$900K–$1.8M"),
    ("Base Case", "3.1pp", "$1.8M–$2.9M"),
    ("Optimistic", "4.1 to 4.9pp", "$3.8M–$4.6M"),
)
PROJECTED_IMPACT = "Financial Analysis > Projected Impact"
CURRENT_STATE = "Financial Analysis > Current State"


def _range_sentence(text, name):
    """The prose sentence stating one case, quoted out of the paper itself."""
    return next(line for line in text.splitlines()
                if line.startswith("In the") and name in line)


def _range_answer(text, *, baseline=True):
    """A scripted answer quoting the range fixture's own prose.

    The baseline field is asked for too, so one run measures both halves of the
    scope line: the two scenario leaves carry their range, and the baseline
    figure whose range this stage deliberately does not carry stays absent.
    """
    fields = [field("commercial.scenarios", *(
        item(_range_sentence(text, name), PROJECTED_IMPACT, name=name,
             margin_gain_pp=pp, direct_uplift_usd_yr=usd)
        for name, pp, usd in RANGE_CASES))]
    if baseline:
        fields.append(field(packet_assembly.REVENUE, item(
            next(line for line in text.splitlines() if "LTM revenue" in line),
            CURRENT_STATE, value="$84.0M–$88.0M")))
    return answer(*fields)


def test_a_percentage_point_range_the_prose_states_reaches_the_deck():
    """The measured win. No committed excerpt states a pp range in a table, so
    the pp half of this stage is exercised on the hand-built prose paper: both
    endpoints cross, on the deck's own en dash, with `pp` named once after the
    second one."""
    text = range_paper()
    _markdown, placeholder_map, prompt, _coverage = _seam(
        "one", _range_answer(text), text=text)

    # On the RETURN table since 2026-09-23, each figure in its own column.
    assert [(row["margin"], row["annual_ebitda"])
            for row in placeholder_map["return_rows"]] == [
        ("+0.9–1.9pp", "$900,000–$1,800,000/yr"),
        ("+3.1pp", "$1,800,000–$2,900,000/yr"),
        ("+4.1–4.9pp", "$3,800,000–$4,600,000/yr"),
    ]
    assert MISSING_MARKER.format(role="return_rows") not in prompt


def test_a_range_that_crosses_still_carries_the_span_it_came_from():
    """A value never enters without a verified span, and a range is no exception:
    the sentence the endpoints were read out of is in the document beside them."""
    text = range_paper()
    markdown, _placeholder_map, _prompt, _coverage = _seam(
        "one", _range_answer(text), text=text)

    for name, _pp, _usd in RANGE_CASES:
        assert _range_sentence(text, name) in markdown


def test_the_word_to_is_read_as_a_separator_and_not_dropped():
    """Stage 2d's reader work, still standing one layer up. The Optimistic case
    states `4.1 to 4.9pp`, the live corpus writes that notation 30 times, and
    both endpoints reach the deck rather than the high one alone."""
    text = range_paper()
    _markdown, placeholder_map, _prompt, _coverage = _seam(
        "one", _range_answer(text), text=text)

    assert placeholder_map["return_rows"][2]["margin"] == "+4.1–4.9pp"


def test_a_baseline_figure_stated_as_a_range_stays_absent_with_its_gap():
    """The Stage 2e scope line, asserted rather than assumed.

    `_one` is untouched, so a baseline figure the paper states as a range stays
    absent and section 8 names it. Whether it should print as a range is a
    deck-design question for Antonio, sharpened by what the coverage guard
    already says about these three paths: they are internal underwriting inputs
    that no slide field reads, so carrying one would move no rendered role.
    """
    text = range_paper()
    _markdown, placeholder_map, _prompt, _coverage = _seam(
        "one", _range_answer(text), text=text)

    assert "baseline.revenue_ttm_usd" in placeholder_map["_gaps"]
    assert "not a slide field" in EXCLUSION_ALLOWLIST["baseline.revenue_ttm_usd"]
    # The two leaves this stage DOES carry are on the same document, so the
    # baseline's absence is a scope decision rather than a run that did nothing.
    assert placeholder_map["return_rows"][0]["annual_ebitda"]


def test_carrying_a_range_does_not_move_data_completeness():
    """`Packet.present` adds `GAIN` when every scenario merely HAS a
    `margin_gain_pp` figure and never inspects whether that figure is a range,
    so `_one` never touched the score and neither does its sibling. This stage
    changes what the deck SHOWS, not what the gate reads."""
    text = range_paper()
    markdown, _placeholder_map, _prompt, _coverage = _seam(
        "one", _range_answer(text), text=text)

    packet = assemble("OPP-RANGES", text)
    result = second_pass.run(text, packet, {},
                             extractor=extractor_returning(_range_answer(text)))
    assert completeness_score.data_completeness(result.packet) == 0.5
    assert "data_completeness: 0.5" in markdown


def test_the_document_omits_the_second_pass_block_when_none_ran():
    """Its presence means a pass ran. An empty block claiming one ran and found
    nothing is a different and untrue statement."""
    client = CLIENTS["one"]
    packet = assemble("OPP", paper(client["shape"]))
    result = second_pass.run(paper(client["shape"]), packet, {})

    markdown = packet_document.build(
        packet=packet, meta={"generated_at": "2026-08-18T00:00:00Z"},
        second_pass=second_pass.ledger(result),
    )
    assert "second_pass" not in markdown


# ---------------------------------------------------------------------------
# The reclassification, per path and on the evidence.
# ---------------------------------------------------------------------------

def test_the_reclassified_paths_are_exactly_the_five_the_pass_can_source():
    """`UNSOURCEABLE` says "no source exists". For these five that was a claim
    about structured tool output and graph labels, never about the research
    paper, which is in the grant and which the parsers already read for charts
    and tables. Every path this pass can fill is SOURCED, and every path it
    cannot is untouched."""
    reclassified = {
        "today_metrics[].value", "today_metrics[].label", "today_pain_points[]",
        "target_capabilities[]", "build_summary.phases[].summary",
    }
    for path in reclassified:
        assert ORIGINS[path] == SOURCED

    # The one label deliberately left alone, by Antonio's ruling of 2026-08-15.
    assert ORIGINS["target_metrics[].label"] == UNSOURCEABLE
    # The week-denominated fields stay unsourceable: no conversion, still.
    assert ORIGINS["build_summary.duration_weeks"] == UNSOURCEABLE
    assert ORIGINS["timeline.week_buckets[]"] == UNSOURCEABLE
    # QofAI's own terms are reviewer input and were never an extraction target.
    for path in packet_assembly.REVIEWER_INPUT:
        assert ORIGINS.get(path) in (None, "REVIEWER")


def test_every_reclassified_path_has_a_role_that_can_actually_carry_it():
    """A path declared SOURCED that no role reads would raise `role_coverage`
    without putting anything on a deck, which is the exact overstatement E9c's
    `role_coverage` docstring exists to prevent."""
    for path in ("today_metrics[].value", "today_pain_points[]",
                 "target_capabilities[]", "build_summary.phases[].summary"):
        assert path in COVERAGE_MAP


def test_the_scope_table_and_the_slot_table_agree_on_what_is_sourceable():
    """The two tables are edited in different files and a path added to one and
    forgotten in the other is a silent hole. Every extractable leaf must be a
    SOURCED slot, or the pass fills a field the document still calls
    unsourceable."""
    for slot in paper_extraction.SCOPE:
        for leaf in slot.leaves:
            for candidate in (f"{slot.path}[].{leaf.name}", slot.path,
                              f"{slot.path}[]"):
                if candidate in ORIGINS:
                    assert ORIGINS[candidate] == SOURCED, candidate
                    break


# ---------------------------------------------------------------------------
# E11 Stage 2c — slide 3's platform and slide 6's next steps, from the paper.
#
# Both were TEMPLATED deck standards: three layers and four actions identical on
# every deck for every client, with invented owners and invented weeks. They are
# paper-sourced now WHERE THE PAPER CARRIES THEM, and the deck standard is the
# fallback rather than the answer. The two directions are tested separately,
# because "the paper's platform rendered" and "the house platform rendered" have
# to be different provenance or the reclassification is just a louder default.
# ---------------------------------------------------------------------------

def _platform_answer(text):
    """Slide 3's components, quoted out of the platform paper's own prose."""
    components = [
        ("A mobile ticket capture app records the work at the job site, so the "
         "ticket\nleaves with the crew rather than arriving days later.",
         "The Proposed Solution", "A mobile ticket capture app"),
        ("A scheduling service holds one live dispatch board every branch reads "
         "from,\nreplacing the whiteboard rebuilt each morning.",
         "The Proposed Solution", "A scheduling service"),
        ("An operations data store joins the captured tickets to the "
         "financials, so\njob-level margin is readable the day a job closes.",
         "The Proposed Solution > Technical Requirements",
         "An operations data store"),
    ]
    for span, _section, _title in components:
        assert span in text, span
    return field("platform_layers", *(
        item(span, section, title=title, body=span)
        for span, section, title in components
    ))


def _next_steps_answer(text):
    """Slide 6's actions, quoted out of the platform paper's own Next Steps."""
    steps = [
        ("Leadership approves the scope and the phased plan before any build "
         "begins.", "Leadership approves the scope and the phased plan",
         "Leadership"),
        ("The finance team supplies read access to the accounting system so the "
         "billing\nintegration can be built against it.",
         "supplies read access to the accounting system", "The finance team"),
        ("Operations names the branch that runs the first phase.",
         "names the branch that runs the first phase", None),
    ]
    for span, _title, _owner in steps:
        assert span in text, span
    return field("next_steps", *(
        item(span, "Next Steps", title=title, body=span, **(
            {"owner": owner} if owner else {}
        ))
        for span, title, owner in steps
    ))


def test_slide_three_describes_this_engagements_platform_and_not_the_template():
    """The single biggest visible win Stage 2c was asked for.

    Slide 3 rendered `INPUTS · FIELD CAPTURE` / `DATA FOUNDATION` / `OUTPUTS ·
    DASHBOARDS & ALERTS` on every deck for every client, whatever the paper said.
    Here the paper describes its own platform and that is what the slide carries,
    with each layer quoted from the paper and its span verified against it.
    """
    text = platform_paper()
    _markdown, placeholder_map, prompt, _coverage = _seam(
        "one", answer(_platform_answer(text)), text=text
    )

    components = placeholder_map["components"]
    assert [component["title"] for component in components] == [
        "A mobile ticket capture app", "A scheduling service",
        "An operations data store",
    ]
    assert [component["number"] for component in components] == ["01", "02", "03"]
    # Quoted from the paper, with only the paper's own hard line wrap collapsed
    # so the packet document can carry the sentence whole (`second_pass._display`).
    for component in components:
        assert component["description"] in " ".join(text.split())
    for layer in packet_fill.PLATFORM_LAYERS:
        assert layer["title"] not in prompt
    assert MISSING_MARKER.format(role="components") not in prompt


def test_a_paper_that_describes_no_platform_keeps_the_deck_standard():
    """The fallback, and it is why the reclassification is safe. A thin paper
    still renders three complete layers rather than a hollow slide, and section 8
    says they are the contract's deck standard rather than this paper's."""
    markdown, placeholder_map, prompt, _coverage = _seam("one", answer())

    assert [component["title"] for component in placeholder_map["components"]] == [
        layer["title"] for layer in packet_fill.PLATFORM_LAYERS
    ]
    assert MISSING_MARKER.format(role="components") not in prompt
    ledger = _section_yaml(_split_sections(markdown)[8])["provenance"]
    assert "platform_layers[].title" in ledger["templated_defaults"]
    assert "platform_layers[].title" not in ledger["from_kg"]


def test_the_paper_sourced_platform_is_reported_as_the_papers_not_the_templates():
    """The other direction of the same claim, and the one that keeps
    `role_coverage` honest: a slide 3 the paper wrote is `from_kg`, and nothing
    on that run is reported as a templated default."""
    text = platform_paper()
    markdown, _placeholder_map, _prompt, _coverage = _seam(
        "one", answer(_platform_answer(text)), text=text
    )

    ledger = _section_yaml(_split_sections(markdown)[8])["provenance"]
    assert "platform_layers[].title" in ledger["from_kg"]
    assert "platform_layers[].title" not in ledger["templated_defaults"]
    # Slide 6 said nothing on this run, so it took the deck standard and says so.
    assert "next_steps[].title" in ledger["templated_defaults"]


def test_a_house_template_slide_three_does_not_raise_role_coverage():
    """Stage 2e's finding, applied before it could bite. `role_coverage` counted
    a role as carrying off the STATIC origin table, so reclassifying these two
    paths to SOURCED would have raised the number by 0.11 on every deck that
    still rendered the house template. It reads the run's own `from_kg` now."""
    text = platform_paper()
    # BOTH runs on the same paper, because a coverage number only compares to
    # another measured on the same corpus. Stage 2e's retraction came from
    # comparing across two.
    _md_default, _map_default, _prompt, templated = _seam(
        "one", answer(), text=text
    )
    _md_paper, _map_paper, _prompt, sourced = _seam(
        "one", answer(_platform_answer(text), _next_steps_answer(text)), text=text
    )

    assert sourced > templated


def test_a_next_step_carries_an_owner_the_paper_named_and_never_one_it_did_not():
    """The line no generosity crosses. An owner is a commitment about a person:
    it is carried where the paper itself names one, and left EMPTY where it does
    not, which marks on the deck for a reviewer to fill. The deck standard's own
    invented owners (`QOFAI ENGINEERING` and the rest) are gone with it."""
    text = platform_paper()
    _markdown, placeholder_map, prompt, _coverage = _seam(
        "one", answer(_next_steps_answer(text)), text=text
    )

    actions = placeholder_map["action_items"]
    assert [action["owner"] for action in actions] == [
        "Leadership", "The finance team", "",
    ]
    # No paper in this corpus states a week for a next step, so every one of
    # them is empty and marks. Nothing invents `WK 0`.
    assert [action["week"] for action in actions] == ["", "", ""]
    for step in packet_fill.NEXT_STEPS:
        assert step["owner"] not in prompt
    assert "[MISSING: owner]" in prompt and "[MISSING: week]" in prompt


def test_the_second_pass_is_asked_for_the_two_reclassified_lists_when_absent():
    """`fill_map` holds both out deliberately, so the request the model gets
    names them. Held IN, the second pass would have been told slide 3 was already
    filled -- by the house template -- and never read the paper for it."""
    fill, _sources = packet_fill.fill_map()

    assert "platform_layers" not in fill and "next_steps" not in fill
    packet = assemble("OPP", platform_paper())
    requested = second_pass.absent(packet, fill)
    assert "platform_layers" in requested and "next_steps" in requested


def test_a_quotation_carrying_the_papers_own_line_wrap_reaches_the_deck_whole():
    """A defect found building Stage 2c, and it is a WRONG VALUE, not an absence.

    `packet_document._scalar_text` writes a scalar as one quoted line and
    `_section_yaml` reads it back a line at a time, so a quotation carrying the
    paper's own hard wrap arrived on the deck cut off at the wrap with its
    opening quote still attached and the rest of the sentence gone, silently. It
    reached every prose path Stage 2 opened, not only the ones Stage 2c adds; it
    survived because the hand-built fixture's own assertions quoted single lines.
    """
    text = platform_paper()
    wrapped = ("Crews are dispatched from a whiteboard rebuilt every morning, "
               "and the rebuild\ntakes two dispatchers most of the first hour of "
               "every shift.")
    assert "\n" in wrapped and wrapped in text

    _markdown, placeholder_map, _prompt, _coverage = _seam(
        "one",
        answer(field("today_pain_points",
                     item(wrapped, "Financial Analysis > Current State",
                          value=wrapped))),
        text=text,
    )

    assert placeholder_map["today_pain_bullets"] == [" ".join(wrapped.split())]
    assert not any('"' in bullet
                   for bullet in placeholder_map["today_pain_bullets"])


def test_a_paper_that_drew_no_plan_still_states_its_horizon_in_prose():
    """The other half of block 3. Where the paper drew a plan, the horizon is its
    last boundary; where it drew none, the horizon is the sentence the paper
    wrote, quoted, with the unit it stated beside the number and no conversion
    between the two."""
    text = platform_paper()
    span = ("The whole programme runs 14 months from approval to the last view "
            "shipping.")
    assert span in text
    packet = assemble("OPP", text)
    result = second_pass.run(text, packet, {}, extractor=extractor_returning(
        answer(field("build_summary.duration",
                     item(span, "Implementation Approach > Timeline",
                          value="14", unit="months")))
    ))
    fill, sources, result = second_pass.merge_fill({}, {}, result)

    assert fill["build_summary.duration"] == 14
    assert fill["build_summary.duration_unit"] == "months"
    assert sources["build_summary.duration"] == second_pass.SECOND_PASS
    assert "build_summary.duration" in result.merged


def test_a_horizon_without_its_unit_is_not_merged_at_all():
    """All or nothing across the two leaves. A number with no unit is a horizon
    the deck would have to guess the unit of, and the guess is what this whole
    stage refuses."""
    text = platform_paper()
    span = ("The whole programme runs 14 months from approval to the last view "
            "shipping.")
    result = second_pass.run(text, assemble("OPP", text), {},
                             extractor=extractor_returning(
        answer(field("build_summary.duration",
                     item(span, "Implementation Approach > Timeline",
                          value="14")))
    ))
    fill, _sources, result = second_pass.merge_fill({}, {}, result)

    assert "build_summary.duration" not in fill
    assert "build_summary.duration_unit" not in fill
    assert "build_summary.duration" not in result.merged


def test_the_derived_horizon_wins_and_the_prose_sentence_is_recorded_beside_it():
    """Precedence, at the merge, against the map as it actually stands. The
    deterministic layer DERIVES the horizon from the plan's own last phase, and
    on a paper whose plan the second pass itself supplied that derivation lands
    between the request and the merge. The derived value stands and the sentence
    the paper stated is kept beside it for the reviewer."""
    text = platform_paper()
    span = ("The whole programme runs 14 months from approval to the last view "
            "shipping.")
    result = second_pass.run(text, assemble("OPP", text), {},
                             extractor=extractor_returning(
        answer(field("build_summary.duration",
                     item(span, "Implementation Approach > Timeline",
                          value="14", unit="months")))
    ))
    standing = {"build_summary.duration": 12,
                "build_summary.duration_unit": "months"}
    fill, _sources, result = second_pass.merge_fill(standing, {}, result)

    assert fill["build_summary.duration"] == 12
    assert "build_summary.duration" not in result.merged
    note = dict(result.disagreements)["build_summary.duration"]
    assert "the derived value" in note and "14" in note


def test_the_horizon_is_not_asked_for_when_the_plan_already_gave_one():
    """The request is composed off the absences, so a packet whose plan already
    yields a horizon never has the model read for one."""
    text = platform_paper()
    filled = {"build_summary.duration": 14, "build_summary.duration_unit": "months"}
    packet = assemble("OPP", text)

    assert "build_summary.duration" not in second_pass.absent(packet, filled)
    assert "build_summary.duration" in second_pass.absent(packet, {})
    assert "build_summary.duration" in second_pass.absent(
        packet, {"build_summary.duration": 14}
    )


# ---------------------------------------------------------------------------
# E11 Stage 2c blocks 4-5 — the GENERATED half, end to end.
#
# The distinction the whole stage turns on. Everything above this line moves
# QUOTATIONS with verified spans. These move SENTENCES THE MODEL WROTE, which
# have no span because nobody wrote them in the paper, and which are governed
# instead by the sections they name. What is asserted here is that the two never
# blur: a generated sentence never becomes a packet field, never moves a gate,
# and never appears in section 8 anywhere a quotation appears.
# ---------------------------------------------------------------------------

import paper_writing  # noqa: E402  -- read with the block above


def writer_returning(payload, record=None):
    """A fake writing pass: the real verifier, driven by a scripted answer.

    Deliberately not a stub returning ready-made `Written` records, on exactly
    the reasoning `extractor_returning` gives: the verification is the thing
    under test, so a scripted sentence that could not verify gets refused here
    the way a live one would.
    """
    calls = record if record is not None else []

    def written(paper, description, requested):
        calls.append((requested, description))
        return paper_writing.read_response(
            paper, description, requested,
            type("M", (), {"content": [type("B", (), {"type": "text",
                                                      "text": json.dumps(payload)})()]})(),
        )

    return written


PLATFORM_SPAN = (
    "A mobile ticket capture app records the work at the job site, so the "
    "ticket\nleaves with the crew rather than arriving days later."
)
NEXT_STEPS_SPAN = (
    "Leadership approves the scope and the phased plan before any build begins."
)


def _framing_answer():
    """Slide 3, 4 and 6's framing, written from the platform paper's sections."""
    return {"sentences": [
        {"path": "copy.platform_headline",
         "text": "One live picture of the work, from the job site to the ledger.",
         "sections": ["The Proposed Solution"],
         "evidence": [PLATFORM_SPAN]},
        {"path": "copy.platform_summary",
         "text": "The ticket leaves with the crew instead of arriving days later.",
         "sections": ["The Proposed Solution"],
         "evidence": [PLATFORM_SPAN]},
        {"path": "copy.next_steps_headline",
         "text": "What has to happen before the build begins.",
         "sections": ["Next Steps"],
         "evidence": [NEXT_STEPS_SPAN]},
    ]}


def test_a_generated_headline_reaches_the_slide_and_replaces_the_deck_standard():
    """Block 5. The framing lines were fixed constants naming what a section
    contains, identical on every deck. Written from the paper's own sections they
    say what THIS engagement's slide is about, and they are labelled generated so
    nobody mistakes one for something the paper stated."""
    text = platform_paper()
    _markdown, placeholder_map, prompt, _coverage = _seam(
        "one", answer(), text=text, writer=writer_returning(_framing_answer())
    )

    assert placeholder_map["platform_headline"] == (
        "One live picture of the work, from the job site to the ledger."
    )
    assert placeholder_map["platform_summary"].startswith("The ticket leaves")
    assert placeholder_map["next_steps_headline"] == (
        "What has to happen before the build begins."
    )
    assert packet_fill.PLATFORM_HEADLINE not in prompt
    assert packet_fill.NEXT_STEPS_HEADLINE not in prompt
    # The line the pass did NOT write keeps its deck standard, which is what
    # makes a refusal cost the deck nothing.
    assert placeholder_map["plan_headline"] == packet_fill.PLAN_HEADLINE
    assert placeholder_map["next_steps_summary"] == packet_fill.NEXT_STEPS_SUMMARY


def test_no_writing_pass_means_every_framing_line_is_the_deck_standard():
    """The seam, and it is the same shape both LLM legs use. With no writer this
    provider behaves exactly as it did before Stage 2c and the packet omits the
    section 8 `generated` block entirely rather than claiming a pass ran."""
    text = platform_paper()
    markdown, placeholder_map, _prompt, _coverage = _seam("one", answer(), text=text)

    assert placeholder_map["platform_headline"] == packet_fill.PLATFORM_HEADLINE
    assert placeholder_map["subtitle"] == ""
    assert "generated:" not in _split_sections(markdown)[8]


def test_section_eight_says_which_sentences_were_written_and_which_were_quoted():
    """Rule 3 of the generated contract, and the one a reviewer uses. The written
    lines are in their own block, labelled, with the sections they came from and
    the evidence they restate; the quoted values are in the block beside it. The
    two are never in one list."""
    text = platform_paper()
    markdown, _placeholder_map, _prompt, _coverage = _seam(
        "one", answer(_platform_answer(text)), text=text,
        writer=writer_returning(_framing_answer()),
    )
    ledger = _section_yaml(_split_sections(markdown)[8])["provenance"]

    written = {entry["field"]: entry for entry in ledger["generated"]["written"]}
    assert set(written) == {"copy.platform_headline", "copy.platform_summary",
                            "copy.next_steps_headline"}
    for entry in written.values():
        assert entry["kind"] == "GENERATED, not quoted"
        assert entry["written_from"] and entry["restating"]
    # The quoted half is in the OTHER block, and nothing crosses between them.
    filled = {entry["field"] for entry in ledger["second_pass"]["filled"]}
    assert "platform_layers" in filled
    assert not filled & set(written)
    # And the source ledger already says it before a reviewer reaches either.
    origins = {entry["field"]: entry["origin"] for entry in ledger["sources"]}
    assert origins["copy.platform_headline"] == second_pass.GENERATED
    assert "GENERATED" in origins["copy.platform_headline"]


def test_a_generated_sentence_never_becomes_a_packet_field_or_moves_a_gate():
    """The line between the two halves, asserted rather than trusted. A written
    sentence reaches the deck through the COPY channel, which carries no field
    path; it never enters the fill map, never enters `Packet.present`, and so
    cannot move `data_completeness` or the 0.70 floor in either direction."""
    text = platform_paper()
    packet = assemble("OPP", text)
    before = completeness_score.data_completeness(packet)
    markdown, _placeholder_map, _prompt, _coverage = _seam(
        "one", answer(), text=text, writer=writer_returning(_framing_answer())
    )

    assert f"data_completeness: {before}" in markdown.split("---")[1]
    assert len(ROSTER) == 6
    for section in (2, 4, 5, 7):
        assert "One live picture" not in _section_yaml(
            _split_sections(markdown)[section]
        ).__repr__()


def test_a_refused_sentence_is_named_with_its_reason_and_costs_the_deck_nothing():
    """A refusal is a correct outcome. It is recorded where a reviewer reads it,
    and the line it was for keeps the standard framing."""
    text = platform_paper()
    markdown, placeholder_map, _prompt, _coverage = _seam(
        "one", answer(), text=text, writer=writer_returning({"sentences": [
            {"path": "copy.plan_headline", "text": "Three phases in 9 months.",
             "sections": ["Implementation Approach > Timeline"],
             "evidence": ["| **Phase 1: Capture** | Months 1-4 | Ship the "
                          "mobile ticket app to one branch |"]},
        ]}),
    )
    ledger = _section_yaml(_split_sections(markdown)[8])["provenance"]

    assert ledger["generated"]["written"] == []
    refused = {entry["field"]: entry["reason"]
               for entry in ledger["generated"]["not_written"]}
    assert "9" in refused["copy.plan_headline"]
    assert placeholder_map["plan_headline"] == packet_fill.PLAN_HEADLINE


def test_a_writer_that_raises_leaves_the_deck_standard_and_records_the_failure():
    """Degradation, stated rather than hidden. The framing pass is additive: a
    deck that rendered before this pass existed still renders when it fails."""
    def raising(paper, description, requested):
        raise RuntimeError("no api key")

    text = platform_paper()
    markdown, placeholder_map, _prompt, _coverage = _seam(
        "one", answer(), text=text, writer=raising
    )
    ledger = _section_yaml(_split_sections(markdown)[8])["provenance"]

    assert placeholder_map["platform_headline"] == packet_fill.PLATFORM_HEADLINE
    reasons = {entry["field"]: entry["reason"]
               for entry in ledger["generated"]["not_written"]}
    assert "RuntimeError" in reasons["copy.platform_headline"]


def test_the_cover_subtitle_and_the_opportunity_line_come_off_the_description():
    """Block 4. Both were absent by design, waiting on a compression of the
    opportunity description; both are written from it now, under the same rules,
    and section 3 still parks the description verbatim and untouched."""
    text = platform_paper()
    description = CLIENTS["one"]["opportunity"]["description"]
    quote = "the roughly 1KB prose block get_opportunity_details returns"
    assert quote in description

    markdown, placeholder_map, prompt, _coverage = _seam(
        "one", answer(), text=text, writer=writer_returning({"sentences": [
            {"path": "copy.subtitle", "text": "A placeholder description.",
             "sections": [paper_writing.DESCRIPTION_SECTION],
             "evidence": ["A first placeholder description"]},
            {"path": "copy.opportunity_summary",
             "text": "The prose block this opportunity returns.",
             "sections": [paper_writing.DESCRIPTION_SECTION],
             "evidence": [quote]},
        ]}),
    )

    assert placeholder_map["subtitle"] == "A placeholder description."
    assert placeholder_map["opportunity_summary"] == (
        "The prose block this opportunity returns."
    )
    assert MISSING_MARKER.format(role="subtitle") not in prompt
    parked = _section_yaml(_split_sections(markdown)[3])["opportunities"]
    assert parked[0]["description"] == description


def test_the_commercial_slides_framing_is_never_written(monkeypatch):
    """The non-negotiable, held at the scope table rather than at the prompt.
    QofAI's terms are founder-set, 0 of 21 papers carry them, and the five
    commercial fields are designed to read AWAITING COMMERCIAL TERMS INPUT.
    Framing written around a deliberately blank slide is the one thing that
    slide must not have, so those two lines are not writable at all."""
    text = platform_paper()
    _markdown, placeholder_map, _prompt, _coverage = _seam(
        "one", answer(), text=text, writer=writer_returning({"sentences": [
            {"path": "copy.terms_headline", "text": "How the value is shared.",
             "sections": ["The Proposed Solution"], "evidence": [PLATFORM_SPAN]},
        ]}),
    )

    assert placeholder_map["terms_headline"] == packet_fill.TERMS_HEADLINE
    assert placeholder_map["terms_summary"] == packet_fill.TERMS_SUMMARY
    assert "copy.terms_headline" not in paper_writing.PATHS


def test_the_writing_pass_is_asked_only_for_lines_it_may_write():
    """The request names the copy paths and nothing else. No packet path, no
    roster field, no commercial field."""
    calls = []
    text = platform_paper()
    _seam("one", answer(), text=text,
          writer=writer_returning({"sentences": []}, record=calls))

    requested, description = calls[0]
    assert set(requested) == set(paper_writing.PATHS)
    assert description == CLIENTS["one"]["opportunity"]["description"]
    assert not set(requested) & set(paper_extraction.PATHS)


# ---------------------------------------------------------------------------
# The chain: each source answers only what the sources ahead of it did not.
# ---------------------------------------------------------------------------
#
# `run` reads ONE document, which is all there ever was to read until a reviewer
# could attach one. From 2026-09-12 the base is whatever was attached, and a PRD
# does not state a client's trailing revenue, so a live run scored 0.83 with
# nothing attached and 0.17 with a PRD attached and was refused for want of
# three baseline figures the published paper states in full. `rescue` asks the
# sources behind the base for the roster fields the base left absent, one call
# per document, in precedence order.

NO_FIGURES = """# Mobile field data capture

## The Proposed Solution

A mobile ticket capture app records the work at the job site, so the ticket
leaves with the crew rather than arriving days later.
"""


def source(text, name="", kind="markdown"):
    import base_document

    return base_document.Source(name=name, kind=kind, text=text)


def base_pass(base_text, paper_text, calls):
    """The first call, against the base, exactly as the provider makes it."""
    packet = assemble("OPP", base_text)
    return second_pass.run(
        base_text, packet, {},
        extractor=extractor_returning(_full_answer(paper_text), calls),
        document=source(base_text, "a-prd.md").document,
    )


def test_the_base_alone_leaves_the_roster_absent_when_it_states_none_of_it():
    """The left half of the regression, and the reason the chain exists: this is
    a healthy pass over a document that simply is not about the company's
    financial state."""
    calls = []
    result = base_pass(NO_FIGURES, prose_paper(), calls)
    assert len(calls) == 1
    assert completeness_score.data_completeness(result.packet) == 0.0
    assert set(second_pass.roster_absent(result.packet)) == {
        packet_assembly.REVENUE, packet_assembly.EBITDA, packet_assembly.MARGIN,
        packet_assembly.TIMELINE, "commercial.scenarios",
    }


def test_the_next_source_answers_what_the_base_did_not():
    calls = []
    result = base_pass(NO_FIGURES, prose_paper(), calls)
    result = second_pass.rescue(
        result, [source(prose_paper(), "", PAPER_KIND)],
        extractor=extractor_returning(_full_answer(prose_paper()), calls),
    )
    assert completeness_score.data_completeness(result.packet) == 1.0
    assert set(result.packet.present) == set(ROSTER)


def test_only_the_still_absent_roster_paths_are_asked_for():
    """Per field, not per document, and the document half is not re-asked: those
    are the engagement's own specifics and the base is the document about the
    engagement."""
    calls = []
    result = base_pass(NO_FIGURES, prose_paper(), calls)
    second_pass.rescue(result, [source(prose_paper(), "", PAPER_KIND)],
                       extractor=extractor_returning(_full_answer(prose_paper()),
                                                     calls))
    asked = calls[1][0]
    assert set(asked) == {packet_assembly.REVENUE, packet_assembly.EBITDA,
                          packet_assembly.MARGIN, packet_assembly.TIMELINE,
                          "commercial.scenarios"}
    assert "today_metrics" not in asked
    assert "platform_layers" not in asked


def test_a_rescued_figure_names_the_document_it_was_read_from():
    """One call per document is what makes this true. A figure rescued from the
    paper names the paper even though the deck is written from the attachment,
    and a single prompt holding both documents could not say which it read."""
    calls = []
    paper_source = source(prose_paper(), "", PAPER_KIND)
    result = base_pass(NO_FIGURES, prose_paper(), calls)
    result = second_pass.rescue(
        result, [paper_source],
        extractor=extractor_returning(_full_answer(prose_paper()), calls))
    for figure in result.packet.fields:
        assert figure.document == paper_source.document
        assert figure.span in prose_paper()


def test_the_chain_stops_as_soon_as_the_roster_is_answered():
    """The cost rule. Three sources behind the base, one call, because the first
    of them answered everything still absent."""
    calls = []
    result = base_pass(NO_FIGURES, prose_paper(), calls)
    result = second_pass.rescue(
        result,
        [source(prose_paper(), "one.md"), source(NO_FIGURES, "two.md"),
         source(NO_FIGURES, "", PAPER_KIND)],
        extractor=extractor_returning(_full_answer(prose_paper()), calls),
    )
    assert len(calls) == 2
    assert completeness_score.data_completeness(result.packet) == 1.0


def test_the_chain_is_bounded_by_the_number_of_sources_behind_the_base():
    """Worst case, stated: one call per supporting source and never more, so the
    ceiling on attachments (`MAX_ATTACHMENTS`, 4 in the studio) is the ceiling on
    this too. Each call is the same bounded leg as the first — one extractor,
    one `model_call` bound — so what grows is the wall clock of a run that
    needed the rescue, not the bound on any call in it."""
    calls = []
    result = base_pass(NO_FIGURES, prose_paper(), calls)
    result = second_pass.rescue(
        result,
        [source(NO_FIGURES, "one.md"), source(NO_FIGURES, "two.md"),
         source(NO_FIGURES, "", PAPER_KIND)],
        extractor=extractor_returning(_full_answer(prose_paper()), calls),
    )
    assert len(calls) == 4  # the base, then one per source, and no more
    assert completeness_score.data_completeness(result.packet) == 0.0


def test_a_base_that_answered_everything_costs_no_call_at_all():
    calls = []
    result = base_pass(prose_paper(), prose_paper(), calls)
    assert completeness_score.data_completeness(result.packet) == 1.0
    second_pass.rescue(result, [source(prose_paper(), "", PAPER_KIND)],
                       extractor=extractor_returning(_full_answer(prose_paper()),
                                                     calls))
    assert len(calls) == 1


def test_no_supporting_source_and_no_extractor_are_both_no_ops():
    """A run with no attachment has a chain of one, so it never enters this at
    all, and a run with no key never called anything in the first place."""
    calls = []
    result = base_pass(NO_FIGURES, prose_paper(), calls)
    assert second_pass.rescue(result, (), extractor=object()) is result
    assert second_pass.rescue(result, [source(prose_paper())], extractor=None) is result


def test_a_failed_rescue_call_says_so_and_leaves_the_packet_standing():
    """Degradation stated rather than hidden, the same as the first call's. The
    pass broke, which is not the same fact as the paper not stating it, and
    `_gate` reads the difference."""
    calls = []
    result = base_pass(NO_FIGURES, prose_paper(), calls)
    before = result.packet
    result = second_pass.rescue(
        result, [source(prose_paper(), "", PAPER_KIND)],
        extractor=raising_extractor(TimeoutError("read timed out")),
    )
    assert result.failed
    assert "TimeoutError" in result.failure
    assert result.packet is before
    reasons = dict(result.dropped)
    assert "did not complete" in reasons[packet_assembly.REVENUE]


def test_a_failed_call_stops_the_chain_rather_than_asking_the_next_source():
    """A pass that broke is not a pass that found nothing, and spending another
    bounded call on the same fault is how a five-minute run becomes fifteen."""
    calls = []
    result = base_pass(NO_FIGURES, prose_paper(), calls)
    result = second_pass.rescue(
        result, [source(prose_paper(), "one.md"), source(prose_paper(), "two.md")],
        extractor=raising_extractor(TimeoutError("read timed out")),
    )
    assert result.failed
    assert len(calls) == 1


def test_a_field_no_source_states_carries_one_reason_and_not_one_per_call():
    """Section 8 answers "why is this still missing", and the answer is that
    every source was asked and none of them stated it. Two entries per field for
    one absence is the same fact twice in a list a reviewer reads."""
    calls = []
    # The same scripted answer on both calls and neither document stating any of
    # it, so the two accounts are identical and any doubling is visible.
    one_call = base_pass(NO_FIGURES, prose_paper(), calls)
    chained = second_pass.rescue(
        one_call, [source(NO_FIGURES, "", PAPER_KIND)],
        extractor=extractor_returning(_full_answer(prose_paper()), calls))

    def entries(result, path):
        return [reason for name, reason in result.dropped if name == path]

    # Asked twice, still missing, and reported once — the second source's
    # answer replaces the first's rather than queueing behind it. (A single
    # call can state several reasons for one path: a scenario table's rows are
    # refused row by row. That is one call's account, not two.)
    assert entries(chained, packet_assembly.REVENUE)
    for path in (packet_assembly.REVENUE, packet_assembly.EBITDA,
                 packet_assembly.MARGIN, "commercial.scenarios"):
        assert len(entries(chained, path)) == len(entries(one_call, path)), path


def test_what_the_chain_filled_reaches_section_eight():
    """The ledger is the reviewer's misattribution check, and a figure read on
    the second call needs it exactly as much as one read on the first."""
    calls = []
    result = base_pass(NO_FIGURES, prose_paper(), calls)
    result = second_pass.rescue(
        result, [source(prose_paper(), "", PAPER_KIND)],
        extractor=extractor_returning(_full_answer(prose_paper()), calls))
    ledger = second_pass.ledger(result)
    filled = {entry["field"] for entry in ledger["filled"]}
    assert {packet_assembly.REVENUE, packet_assembly.EBITDA,
            packet_assembly.MARGIN} <= filled
    for entry in ledger["filled"]:
        assert entry["span"]


def test_the_ledger_names_no_document_even_now_that_two_were_read():
    """It goes into the PACKET DOCUMENT, which five tests hold free of
    provenance. Which document a span came from is the studio's to say."""
    calls = []
    result = base_pass(NO_FIGURES, prose_paper(), calls)
    result = second_pass.rescue(
        result, [source(prose_paper(), "a-paper.md", PAPER_KIND)],
        extractor=extractor_returning(_full_answer(prose_paper()), calls))
    rendered = str(second_pass.ledger(result))
    assert "a-prd.md" not in rendered
    assert "a-paper.md" not in rendered


# --- the opportunity a reading is for (item 15, 2026-09-13) ------------------


def test_the_pass_tells_the_extractor_which_opportunity_it_is_reading_for():
    """The seam grew an argument, so this asserts it actually arrives rather
    than that the signature exists."""
    text = paper("sparse-no-scenario-table")
    packet = assemble("OPP-ONE", text)
    seen = []

    def extract(paper_text, requested, labels=(), document=PAPER,
                opportunity=None, description=""):
        seen.append(opportunity)
        return paper_extraction.Extraction(requested=tuple(requested),
                                           records={}, dropped=())

    second_pass.run(text, packet, {}, extractor=extract)

    assert seen and seen[0].id == "OPP-ONE"


def test_the_default_opportunity_is_the_one_the_packet_already_names():
    """Derived, not invented, for the reason `packet_assembly.assemble`'s own
    default is: a shared stand-in would make two opportunities' readings compare
    equal, which is the collision the field exists to prevent."""
    text = paper("sparse-no-scenario-table")
    seen = []

    def extract(paper_text, requested, labels=(), document=PAPER,
                opportunity=None, description=""):
        seen.append(opportunity.id)
        return paper_extraction.Extraction(requested=tuple(requested),
                                           records={}, dropped=())

    second_pass.run(text, assemble("OPP-ONE", text), {}, extractor=extract)
    second_pass.run(text, assemble("OPP-TWO", text), {}, extractor=extract)

    assert seen == ["OPP-ONE", "OPP-TWO"]


def test_an_explicit_opportunity_wins_over_the_packets_own():
    """The live path passes the real record, which carries the title the derived
    default cannot know."""
    text = paper("sparse-no-scenario-table")
    named = Opportunity(id="OPP-ONE", title="Mobile field capture")
    seen = []

    def extract(paper_text, requested, labels=(), document=PAPER,
                opportunity=None, description=""):
        seen.append(opportunity)
        return paper_extraction.Extraction(requested=tuple(requested),
                                           records={}, dropped=())

    second_pass.run(text, assemble("OPP-ONE", text), {}, extractor=extract,
                    opportunity=named)

    assert seen == [named]


def test_a_rescued_figure_names_the_opportunity_as_well_as_the_document():
    """The rescue reads a SUPPORTING source for what the base did not answer. On
    a multi-opportunity run that supporting source is this opportunity's own
    paper while the base is shared between all of them, so the figure has to
    name both."""
    from test_paper_extraction import prose_paper

    text = prose_paper()
    thin = assemble("OPP-ONE", "A document that states none of the roster.")
    first = second_pass.SecondPass(packet=thin)
    source = base_document.Source(name="", kind=source_span.PAPER_KIND, text=text)

    rescued = second_pass.rescue(
        first, [source],
        extractor=extractor_returning(_full_answer(text)),
        opportunity=Opportunity(id="OPP-ONE", title="Mobile field capture"),
    )

    assert rescued.packet.fields
    for figure in rescued.packet.fields:
        assert figure.opportunity.title == "Mobile field capture"
        assert figure.document.kind == source_span.PAPER_KIND


def test_the_pass_hands_the_extractor_the_subject_description_too():
    """What the opportunity IS, in the platform's own words. It is the
    disambiguator that does the work when one document describes two
    opportunities whose titles a reader could not tell apart out of context."""
    text = paper("sparse-no-scenario-table")
    seen = []

    def extract(paper_text, requested, labels=(), document=PAPER,
                opportunity=None, description=""):
        seen.append(description)
        return paper_extraction.Extraction(requested=tuple(requested),
                                           records={}, dropped=())

    second_pass.run(text, assemble("OPP-ONE", text), {}, extractor=extract,
                    description="Replacing clipboards at the point of work.")

    assert seen == ["Replacing clipboards at the point of work."]


def test_a_rescue_carries_the_subject_to_every_source_it_asks():
    """The rescue reads a SUPPORTING source for what the base did not answer, so
    it makes a call of its own and that call needs the subject as much as the
    first one did."""
    from test_paper_extraction import prose_paper

    text = prose_paper()
    thin = assemble("OPP-ONE", "A document that states none of the roster.")
    source = base_document.Source(name="", kind=source_span.PAPER_KIND, text=text)
    seen = []

    def extract(paper_text, requested, labels=(), document=PAPER,
                opportunity=None, description=""):
        seen.append((opportunity.label, description))
        return paper_extraction.Extraction(requested=tuple(requested),
                                           records={}, dropped=())

    second_pass.rescue(
        second_pass.SecondPass(packet=thin), [source], extractor=extract,
        opportunity=Opportunity(id="OPP-ONE", title="Mobile field capture"),
        description="Replacing clipboards at the point of work.",
    )

    assert seen == [("Mobile field capture",
                     "Replacing clipboards at the point of work.")]


def test_a_reading_for_the_wrong_opportunity_fills_nothing():
    """THE ITEM 15 CASE THROUGH THE PASS, not at the seam below it. The scripted
    answer attributes every item to one opportunity; the pass is run for
    another; and the packet comes back exactly as the deterministic parsers left
    it, with each refusal recorded rather than swallowed."""
    text = prose_paper()
    packet = assemble("OPP-ONE", text)
    payload = _full_answer(text)

    def extract(paper_text, requested, labels=(), document=PAPER,
                opportunity=None, description=""):
        # Attributed to a DIFFERENT opportunity than the one being read for.
        answered = attributed(payload, Opportunity(id="OPP-TWO",
                                                   title="Another thing"))
        return paper_extraction.read_response(
            paper_text, requested,
            type("M", (), {"content": [type("B", (), {
                "type": "text", "text": json.dumps(answered)})()]})(),
            document=document, opportunity=opportunity,
        )

    result = second_pass.run(text, packet, {}, extractor=extract,
                             opportunity=Opportunity(id="OPP-ONE"))

    assert result.merged == ()
    assert result.packet.present == packet.present
    assert result.dropped
    for _path, reason in result.dropped:
        assert "another opportunity" in reason


def test_and_the_same_answer_attributed_correctly_does_fill():
    """The control for the test above. Without it that one passes on any scripted
    answer the pass happens to refuse for some other reason."""
    text = prose_paper()
    packet = assemble("OPP-ONE", text)

    result = second_pass.run(
        text, packet, {},
        extractor=extractor_returning(_full_answer(text)),
        opportunity=Opportunity(id="OPP-ONE"),
    )

    assert result.merged
    assert result.packet.present > packet.present


# ---------------------------------------------------------------------------
# The slide-3 defect, 2026-09-15 live Northwind deck: the headline read "How the
# platform is built.", which is the deck standard, while platform_summary beside
# it and the plan and next-steps headlines all carried written lines.
# ---------------------------------------------------------------------------

def _writing_result(lines):
    """A finished writing pass carrying ``{path: text}``, shaped as the real one.

    `merge_written` and `written_ledger` read `requested`, `sentences` and
    `dropped` and nothing else, so this is the real dataclass rather than a stub
    of it: the sections and evidence are what a verified sentence carries.
    """
    import paper_writing
    return paper_writing.Writing(
        requested=tuple(lines),
        sentences={
            path: paper_writing.Written(
                path=path, text=text,
                sections=("The Platform",), evidence=("some quoted span",))
            for path, text in lines.items()
        },
        dropped=(),
    )


def test_the_platform_guidance_no_longer_hands_over_the_line_to_beat():
    """The cause. This was the ONLY slot whose guidance quoted its own fallback,
    so the prompt handed the model the exact sentence it was told to beat."""
    import paper_writing
    for slot in paper_writing.WRITTEN_SCOPE:
        for name, standard in vars(packet_fill).items():
            if name.isupper() and isinstance(standard, str) and len(standard) > 12:
                assert standard not in slot.guidance, (
                    f"{slot.path} quotes {name}, which offers the model the "
                    f"answer it is supposed to improve on")


def test_a_line_equal_to_the_deck_standard_is_not_treated_as_written():
    """It passes every check in `paper_writing._sentence` -- inside the limit,
    names a section, quotes evidence, states no number and no proper noun -- so
    nothing below this refuses it."""
    assert second_pass.restates_deck_standard(
        "copy.platform_headline", packet_fill.PLATFORM_HEADLINE)
    # On what it SAYS, not byte for byte.
    assert second_pass.restates_deck_standard(
        "copy.platform_headline", "  how the platform IS built  ")
    # A real headline is not a restatement.
    assert not second_pass.restates_deck_standard(
        "copy.platform_headline",
        "A scheduling layer over Planwright and NetSuite")
    # A path with no deck standard has nothing to restate.
    assert not second_pass.restates_deck_standard("copy.subtitle", "anything")


def test_the_restatement_is_dropped_from_the_copy_the_deck_uses():
    """`framed()` is `written.get(path) or constant`, so a restatement and a
    refusal render the same string. The difference has to be made before that."""
    result = _writing_result({
        "copy.platform_headline": packet_fill.PLATFORM_HEADLINE,
        "copy.plan_headline": "From connectivity to AI sequencing",
    })
    written, sources = second_pass.merge_written({}, result)
    assert "copy.platform_headline" not in written
    assert written["copy.plan_headline"] == "From connectivity to AI sequencing"
    assert "copy.platform_headline" not in sources


def test_section_8_reports_the_restatement_as_refused_rather_than_written():
    """The state that was reported was the wrong one: the ledger claimed a line
    was written for a slot whose slide says nothing about the client."""
    result = _writing_result({
        "copy.platform_headline": packet_fill.PLATFORM_HEADLINE,
        "copy.plan_headline": "From connectivity to AI sequencing",
    })
    ledger = second_pass.written_ledger(result)
    written_fields = [row["field"] for row in ledger["written"]]
    refused = {row["field"]: row["reason"] for row in ledger["not_written"]}
    assert "copy.platform_headline" not in written_fields
    assert "copy.plan_headline" in written_fields
    assert "restated the deck standard" in refused["copy.platform_headline"]


def test_a_written_headline_still_reaches_the_slide():
    """The counterpart, so this is a refusal of one answer and not of the slot."""
    result = _writing_result({
        "copy.platform_headline": "A scheduling layer over Planwright",
    })
    written, sources = second_pass.merge_written({}, result)
    assert written["copy.platform_headline"] == "A scheduling layer over Planwright"
    assert sources["copy.platform_headline"] == second_pass.GENERATED
