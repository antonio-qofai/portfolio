"""Tests for packet assembly (E7a, assembly half).

Everything here runs against the eight committed excerpts under
`data-provider/fixtures/`. The expected numbers in `BASELINE_FIGURES` were
derived by hand from the value strings in `tests/test_baseline_parser.py`'s own
`BASELINES` table, which were read off the fixtures before E5d existed. So this
file checks the conversion rather than recording it: the strings are independent
evidence and the floats beside them are what the packet's declared
representation must turn each string into.

`scenario-rows-canonical` and `no-timeline-chart` are the pair that matters most
here. One states its baseline as `$67,600,000` and the other as `$59.2M`, the
two notations E5d hands over, and both must arrive in the packet as the same
`(low, high)` shape.

Two tests use a hand-built paper rather than a captured one and say so in their
own docstring, because no committed excerpt reaches either path: all eight state
all three baseline figures, so nothing captured exercises a baseline absence.
"""

import dataclasses
import json
import pathlib

from source_span import PAPER, Opportunity

# The opportunity these readings are FOR (item 15, 2026-09-13). A figure names
# one the way it names its document, so a parser test has to state one, and
# stating a stand-in here is the same trade `PAPER` makes: these tests are about
# reading a table, not about which engagement asked for it.
OPPORTUNITY = Opportunity(id="OPP-PARSER-TEST")

import pytest

from packet_assembly import (
    EBITDA,
    GAIN,
    MARGIN,
    REVENUE,
    REVIEWER_INPUT,
    ROSTER,
    TIMELINE,
    UPLIFT,
    assemble,
    read_percent,
)
from scenario_table_parser import parse_scenario_cases

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "data-provider" / "fixtures"

# shape -> (revenue usd, EBITDA usd, EBITDA margin percent)
BASELINE_FIGURES = {
    "assumption-table-and-scenario-rows": (27_900_000.0, 6_780_000.0, 25.0),
    "no-timeline-chart": (59_200_000.0, 24_100_000.0, 40.8),
    "scenario-both-orientations": (53_700_000.0, 22_900_000.0, 42.5),
    "scenario-columns": (26_200_000.0, 6_410_000.0, 24.4),
    "scenario-rows-canonical": (67_600_000.0, 12_000_000.0, 17.8),
    "scenario-rows-multi-table": (27_900_000.0, 6_780_000.0, 25.0),
    "sparse-no-scenario-table": (55_500_000.0, 24_200_000.0, 43.5),
    "validated-assumption-table": (55_600_000.0, 24_200_000.0, 43.6),
}
ALL_FIXTURES = tuple(BASELINE_FIGURES)

# shape -> which roster fields the paper supplies. Read off the four parsers'
# coverage, not off this module's output: `no-timeline-chart` has no timeline
# chart, `scenario-rows-multi-table` and `validated-assumption-table` have no
# percentage-point column, and `sparse-no-scenario-table` has no scenario table.
PRESENT_COUNTS = {
    "assumption-table-and-scenario-rows": 6,
    "no-timeline-chart": 5,
    "scenario-both-orientations": 6,
    "scenario-columns": 6,
    "scenario-rows-canonical": 6,
    "scenario-rows-multi-table": 5,
    "sparse-no-scenario-table": 4,
    "validated-assumption-table": 5,
}

NO_BASELINE_PAPER = """## Executive Summary

The engagement would standardise quoting across the branch network.

### Projected Impact

| Scenario | Incremental EBITDA | EBITDA Margin Impact (pp) |
|---|---|---|
| Conservative | $500K | 1.0 pp |
| Base Case | $750K | 1.5 pp |
"""


def paper(shape):
    path = FIXTURES / f"paper-excerpt-{shape}.json"
    return json.loads(path.read_text())["opportunity"]["research_paper_natural"]


def packet(shape):
    return assemble(shape, paper(shape))


def figure(assembled, name):
    return next((f for f in assembled.fields if f.field == name), None)


@pytest.mark.parametrize("shape", ALL_FIXTURES)
def test_every_assembled_field_carries_the_paper_text_it_came_from(shape):
    """Provenance on every field, which is what `SourcedFigure` is for."""
    assembled = packet(shape)
    assert assembled.fields
    for f in assembled.fields:
        assert f.span.strip()
        assert f.span in paper(shape)


@pytest.mark.parametrize("shape", ALL_FIXTURES)
def test_every_absence_carries_a_reason(shape):
    for name, reason in packet(shape).missing_fields:
        assert name in ROSTER
        assert reason.strip()


@pytest.mark.parametrize("shape", ALL_FIXTURES)
def test_every_roster_field_is_either_present_or_missing(shape):
    """No third state. A field the provider claims is sourced or it is named."""
    assembled = packet(shape)
    named = {name for name, _ in assembled.missing_fields}
    assert assembled.present.isdisjoint(named)
    assert assembled.present.union(named) == set(ROSTER)


@pytest.mark.parametrize("shape", ALL_FIXTURES)
def test_present_count_matches_the_papers_own_coverage(shape):
    assert len(packet(shape).present) == PRESENT_COUNTS[shape]


@pytest.mark.parametrize("shape", ALL_FIXTURES)
def test_baseline_strings_convert_to_the_packets_declared_representation(shape):
    """The value-format divergence, reconciled once at the boundary.

    E5d hands over the paper's own string in either notation; the packet holds a
    `(low, high)` pair of floats either way.
    """
    assembled = packet(shape)
    expected = BASELINE_FIGURES[shape]
    for name, want in zip((REVENUE, EBITDA, MARGIN), expected):
        low, high = figure(assembled, name).value
        assert low == pytest.approx(want)
        assert high == pytest.approx(want)


def test_both_dollar_notations_reach_the_same_shape():
    """`$67,600,000` and `$59.2M` are the two notations E5d returns."""
    comma = figure(packet("scenario-rows-canonical"), REVENUE)
    suffix = figure(packet("no-timeline-chart"), REVENUE)
    assert "$67,600,000" in comma.span
    assert "$59.2M" in suffix.span
    assert comma.value == (67_600_000.0, 67_600_000.0)
    assert suffix.value == (59_200_000.0, 59_200_000.0)


@pytest.mark.parametrize("shape", ALL_FIXTURES)
def test_conversion_keeps_the_span_it_arrived_with(shape):
    """A converted figure is still traceable to the text that stated it."""
    from baseline_parser import parse_baseline

    stated = parse_baseline(paper(shape), document=PAPER, opportunity=OPPORTUNITY)
    assembled = packet(shape)
    for name, attribute in ((REVENUE, "revenue"), (EBITDA, "ebitda")):
        assert figure(assembled, name).span == getattr(stated, attribute).span


@pytest.mark.parametrize("shape", ALL_FIXTURES)
def test_scenario_pairs_pass_through_unchanged(shape):
    """E5b's representation is already the packet's, so nothing is re-read."""
    cases = parse_scenario_cases(paper(shape), document=PAPER, opportunity=OPPORTUNITY)
    assembled = packet(shape)
    assert len(assembled.scenarios) == len(cases)
    for case, source in zip(assembled.scenarios, cases):
        assert case.label == source.label
        assert case.direct_uplift_usd_yr.value == source.incremental_ebitda.value
        assert case.direct_uplift_usd_yr.span == source.incremental_ebitda.span


def test_a_scenario_table_with_no_pp_column_records_the_gain_missing():
    """Not borrowed from a percentage that is not one. E5b's normal outcome."""
    assembled = packet("scenario-rows-multi-table")
    assert assembled.scenarios
    assert all(case.margin_gain_pp is None for case in assembled.scenarios)
    assert GAIN in dict(assembled.missing_fields)
    assert UPLIFT in assembled.present


def test_a_paper_with_no_scenario_table_records_both_scenario_fields_missing():
    assembled = packet("sparse-no-scenario-table")
    assert assembled.scenarios == ()
    named = dict(assembled.missing_fields)
    assert UPLIFT in named and GAIN in named


def test_a_paper_with_no_timeline_chart_records_the_timeline_missing():
    assembled = packet("no-timeline-chart")
    assert TIMELINE in dict(assembled.missing_fields)
    assert figure(assembled, TIMELINE) is None


# --- the commercial denominator rule (E6's one surviving requirement) --------


def test_reviewer_input_fields_are_not_packet_fields():
    """QofAI's per-deal arithmetic is not in the roster, so not in the ratio."""
    assert set(REVIEWER_INPUT).isdisjoint(ROSTER)


@pytest.mark.parametrize("shape", ALL_FIXTURES)
def test_no_packet_ever_claims_or_misses_a_reviewer_input_field(shape):
    """Absent by design is not the same as missing, so neither list names one."""
    assembled = packet(shape)
    named = {f.field for f in assembled.fields}
    named.update(name for name, _ in assembled.missing_fields)
    assert named.isdisjoint(REVIEWER_INPUT)


def test_the_two_paper_derived_commercial_fields_are_in_the_roster():
    """`margin_gain_pp` and `direct_uplift_usd_yr` parse like any other figure."""
    assert UPLIFT in ROSTER and GAIN in ROSTER


# --- the golden rule at the conversion boundary -----------------------------


def test_read_percent_reads_a_cell_stating_one_percentage():
    assert read_percent("40.8%") == (40.8, 40.8)
    assert read_percent("~25%") == (25.0, 25.0)


def test_read_percent_refuses_a_cell_stating_more_than_one_percentage():
    """Two percentages are two periods, not a range, so the cell yields nothing.

    `51.3% (full-year) / 46.7% (Q1 2026)` is a real E5c cell shape on
    `validated-assumption-table`. Picking one of them is the judgment this
    provider does not make, and a range endpoint reads the same way.
    """
    assert read_percent("51.3% (full-year) / 46.7% (Q1 2026)") is None
    assert read_percent("35-40%") is None
    assert read_percent("no percentage here") is None


def test_read_percent_reads_the_spelled_unit_as_well_as_the_sign():
    """E11 Stage 2b, 2026-08-19. `%` was the only unit this reader accepted, so a
    paper writing `12.4 percent` lost the figure on both passes. Before Stage 2b
    every spelled form here returned None.
    """
    assert read_percent("12.4 percent") == (12.4, 12.4)
    assert read_percent("40.8 PERCENT") == (40.8, 40.8)
    assert read_percent("~25 percent") == (25.0, 25.0)
    # And the sign is unchanged.
    assert read_percent("12.4%") == (12.4, 12.4)


def test_read_percent_keeps_every_refusal_the_spelled_unit_could_have_loosened():
    """The one-percentage-one-number guard, untouched, is what keeps a spelled
    unit from dragging a figure out of prose. And `percentage points` is a
    different unit with a different reader: reading pp as percent would be wrong
    by construction, which is the distinction `scenario_table_parser.read_pp`
    exists to hold.
    """
    assert read_percent("35-40 percent") is None
    assert read_percent("12.4 percent of CY2025 administrative payroll") is None
    assert read_percent("1.9 percentage points") is None
    assert read_percent("1.9 percentage point") is None
    assert read_percent("1.9pp") is None


def test_a_paper_stating_no_baseline_records_all_three_with_reasons():
    """Hand-built paper: all eight excerpts state a baseline, so none reaches this."""
    assembled = assemble("hand-built", NO_BASELINE_PAPER)
    named = dict(assembled.missing_fields)
    for name in (REVENUE, EBITDA, MARGIN):
        assert name in named
        assert named[name].strip()
    assert figure(assembled, REVENUE) is None
    assert UPLIFT in assembled.present


def test_a_paper_stating_no_baseline_still_assembles_the_fields_it_has():
    """Hand-built paper, same reason. An absence is not an error."""
    assembled = assemble("hand-built", NO_BASELINE_PAPER)
    assert len(assembled.scenarios) == 2
    assert assembled.present == {UPLIFT, GAIN}


# --- immutability, per the 2026-07-28 change --------------------------------


def test_a_packet_cannot_be_changed_once_assembled():
    assembled = packet("scenario-rows-canonical")
    with pytest.raises(dataclasses.FrozenInstanceError):
        assembled.fields = ()
    with pytest.raises(dataclasses.FrozenInstanceError):
        assembled.missing_fields = ()


def test_a_packets_members_are_immutable_too():
    """`missing_fields` is pairs rather than the recorder, so nothing can record
    a further absence into an assembled packet."""
    assembled = packet("no-timeline-chart")
    assert isinstance(assembled.fields, tuple)
    assert isinstance(assembled.missing_fields, tuple)
    assert isinstance(assembled.scenarios, tuple)
    assert not hasattr(assembled.missing_fields, "record")


def test_the_baseline_statements_period_and_basis_travel_with_the_packet():
    """A baseline figure with no basis is not usable, per E5d."""
    assembled = packet("scenario-rows-canonical")
    assert assembled.baseline_period == "TTM (Apr 2025–Mar 2026)"
    assert assembled.baseline_basis == "ttm"


def test_read_percent_cannot_read_a_range_separator_as_a_sign():
    """E11 Stage 2d's audit of the third reader, and the answer is that it is
    immune by construction rather than needing the fix `read_pp` needed.

    `_PERCENT` has no sign group at all, so no separator can reach a value. And
    the one-percentage-one-number guard refuses every range outright, in both the
    `%` and the spelled form, so a separator never even reaches a second
    endpoint: the cell records an absence with its reason and the gate decides.

    Separately, and NOT fixed here: no sign group means a stated minus is dropped
    rather than misread, so `-2.4%` reads +2.4. That is a different defect from
    Stage 2d's, it moves numbers on any paper stating a negative percentage, and
    it is recorded rather than changed in a stage scoped to range separators.
    """
    assert read_percent("35-40%") is None
    assert read_percent("35–40%") is None
    assert read_percent("35 - 40 percent") is None
    assert read_percent("1.2 to 6.0%") is None
    assert read_percent("0.9-1.9%") is None
    # The sign is dropped, never inverted: no separator becomes a minus here.
    assert read_percent("-2.4%") == (2.4, 2.4)
    assert read_percent("-2.4 percent") == (2.4, 2.4)
    assert read_percent("(2.4%)") == (2.4, 2.4)


# --- E11 Stage 2f: one bad chart config costs its chart and nothing else -----
#
# The regression this stage exists to prevent. Before it, one `<Chart>` config
# that stopped being JSON raised out of `chart_timeline_parser` through
# `assemble`, so the three baseline figures and both scenario fields -- five of
# the six roster fields, none of them read off a chart -- were lost with it.
# Measured on the live corpus 2026-08-19: one config in 216 charts across the 49
# published demo papers, and it took one whole paper's packet down.
#
# The corruption below is applied to a CAPTURED excerpt in memory and never
# written anywhere. `\p` is not a JSON escape, which is the same class of defect
# the live config carries.

MALFORMED_ESCAPE = r'"ty\pe"'
MALFORMED_DECOY_BODY = r"""{"type": "line", "data": {"labels": ["Acme\'s option"]}}"""


def _corrupt_timeline_config(shape):
    """The excerpt's paper with its timeline chart's own config no longer JSON."""
    from chart_timeline_parser import parse_timeline

    text = paper(shape)
    span = parse_timeline(text, document=PAPER, opportunity=OPPORTUNITY).span
    broken = span.replace('"type"', MALFORMED_ESCAPE, 1)
    assert broken != span
    return text.replace(span, broken, 1)


def _with_a_broken_decoy(shape):
    """The excerpt's paper with one extra chart appended whose config is not JSON."""
    return paper(shape) + (
        "\n\n## Appendix\n\n"
        '<Chart\n  name="Hand-built decoy"\n  description="Hand-built for a test."\n'
        f"  config='{MALFORMED_DECOY_BODY}'\n/>\n"
    )


def test_a_malformed_decoy_config_costs_the_packet_nothing_at_all():
    """The live shape: the broken chart is not the timeline. Every roster field
    the intact paper supplied is still supplied."""
    intact = packet("scenario-columns")
    corrupted = assemble("scenario-columns", _with_a_broken_decoy("scenario-columns"))
    assert corrupted.present == intact.present == frozenset(ROSTER)
    assert len(corrupted.present) == PRESENT_COUNTS["scenario-columns"]
    assert dict(corrupted.missing_fields) == dict(intact.missing_fields)
    for name in ROSTER:
        before, after = figure(intact, name), figure(corrupted, name)
        assert (before is None) == (after is None), name
        if before is not None:
            assert before.value == after.value, name


def test_a_malformed_timeline_config_costs_the_timeline_and_the_other_five_survive():
    """The blast radius, reduced to one field. Before this stage `assemble` raised
    `JSONDecodeError` here and all six were lost."""
    intact = packet("scenario-columns")
    corrupted = assemble("scenario-columns", _corrupt_timeline_config("scenario-columns"))

    assert TIMELINE in intact.present
    assert TIMELINE not in corrupted.present
    assert corrupted.present == intact.present - {TIMELINE}
    assert figure(corrupted, TIMELINE) is None

    for name in (REVENUE, EBITDA, MARGIN):
        assert figure(corrupted, name).value == figure(intact, name).value, name
    assert corrupted.scenarios == intact.scenarios
    assert all(case.margin_gain_pp for case in corrupted.scenarios)


def test_the_timeline_absence_from_a_malformed_config_reads_as_a_platform_signal():
    """Section 8 carries the reason, and it is not the reason a paper that simply
    has no timeline chart carries. The first is normal; the second is Blake's."""
    corrupted = assemble("scenario-columns", _corrupt_timeline_config("scenario-columns"))
    malformed = dict(corrupted.missing_fields)[TIMELINE]
    absent = dict(packet("no-timeline-chart").missing_fields)[TIMELINE]

    assert "JSON" in malformed and "JSON" not in absent
    assert malformed != absent
    assert malformed.strip()
    # `data-provider/SCRUBBING.md`: a real config body names third-party firms.
    for fragment in ("indexAxis", "datasets", "labels", MALFORMED_ESCAPE):
        assert fragment not in malformed


def test_a_malformed_config_still_leaves_every_roster_field_present_or_missing():
    """The no-third-state invariant, on the path that never had a test."""
    corrupted = assemble("scenario-columns", _corrupt_timeline_config("scenario-columns"))
    named = {name for name, _ in corrupted.missing_fields}
    assert corrupted.present.isdisjoint(named)
    assert corrupted.present.union(named) == set(ROSTER)
    for name, reason in corrupted.missing_fields:
        assert name in ROSTER and reason.strip()


# --- the opportunity a figure was read for (item 15, 2026-09-13) -------------


def test_every_figure_names_the_opportunity_the_packet_is_for():
    """The primitive requires one, so this is really a test that `assemble`
    passes the right one down rather than some other."""
    built = assemble("OPP-ONE", paper("scenario-rows-canonical"))
    assert built.fields
    for figure in built.fields:
        assert figure.opportunity.id == "OPP-ONE"
    for case in built.scenarios:
        assert case.direct_uplift_usd_yr.opportunity.id == "OPP-ONE"


def test_the_default_opportunity_is_derived_from_the_id_and_so_cannot_collide():
    """THE PROPERTY THAT MAKES THE DEFAULT SAFE, and the reason it is derived
    rather than a shared sentinel.

    `document` defaults to `PAPER`, one object, and that is fine because two
    callers reading the paper really are reading the same document. An
    opportunity default cannot work that way: a single shared stand-in would
    make every run's figures compare equal on this field, which is exactly the
    collision the field exists to prevent. So the default is built from the
    `opportunity_id` the caller already passed, and two opportunities default
    apart."""
    text = paper("scenario-rows-canonical")
    first = assemble("OPP-ONE", text)
    second = assemble("OPP-TWO", text)

    assert {f.opportunity for f in first.fields} != {f.opportunity for f in second.fields}
    assert first.fields != second.fields


def test_the_default_carries_the_id_and_no_title_and_says_so():
    """What the default cannot supply is the title, so it supplies none and
    `label` falls back to the id rather than to an invented name."""
    built = assemble("OPP-ONE", paper("scenario-rows-canonical"))
    figure = built.fields[0]
    assert figure.opportunity.title == ""
    assert figure.opportunity.label == "OPP-ONE"


def test_an_explicit_opportunity_wins_over_the_derived_one():
    """The live path passes the real record, title and all."""
    named = Opportunity(id="OPP-ONE", title="Mobile field capture")
    built = assemble("OPP-ONE", paper("scenario-rows-canonical"),
                     opportunity=named)
    assert {f.opportunity for f in built.fields} == {named}
