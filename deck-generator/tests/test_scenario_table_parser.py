"""Tests for the scenario table parser (E5b).

Everything here runs against the eight committed excerpts under
`data-provider/fixtures/`. Nothing is hand-built, because unlike E4's two traps
every shape this parser has to discriminate is present in the captured data:
both orientations, five unlabelled decoys, and a paper with no scenario table at
all. The expected figures in `EXPECTED` were read off the fixtures' own markdown
tables by hand on 2026-08-11 before the parser existed, so they are a check on
the parser rather than a recording of it.

Fixtures are named by coverage shape, never by company, and no assertion here
reads a client name, a project name, or a chart or table title.

Five tests carry a decoy in their name. Each one asserts the specific wrong
answer that decoy produces, not merely that the right answer came back, because
a decoy that is rejected for the wrong reason still reports green.
"""

import json
import pathlib

import pytest

from scenario_table_parser import (
    DOLLAR_FIELD,
    MARGIN_FIELD,
    _money,
    is_margin_label,
    is_return_table,
    parse_scenario_cases,
    read_cases,
    read_pp,
    tables,
)
from source_span import PAPER, Opportunity, MissingFields

# The opportunity these readings are FOR (item 15, 2026-09-13). A figure names
# one the way it names its document, so a parser test has to state one, and
# stating a stand-in here is the same trade `PAPER` makes: these tests are about
# reading a table, not about which engagement asked for it.
OPPORTUNITY = Opportunity(id="OPP-PARSER-TEST")


FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "data-provider" / "fixtures"

K = 1e3
M = 1e6

# label, (low, high) dollars, (low, high) pp or None. Read by hand from the
# fixtures' markdown tables, 2026-08-11.
EXPECTED = {
    "assumption-table-and-scenario-rows": [
        ("Conservative", (632 * K, 632 * K), (2.3, 2.3)),
        ("Base Case", (949 * K, 949 * K), (3.2, 3.2)),
        ("Upside", (1260 * K, 1260 * K), (4.2, 4.2)),
    ],
    "no-timeline-chart": [
        ("Conservative", (1.78 * M, 1.78 * M), (2.3, 2.3)),
        ("Base Case", (2.55 * M, 2.55 * M), (3.3, 3.3)),
        ("Optimistic", (3.23 * M, 3.23 * M), (4.1, 4.1)),
    ],
    "scenario-both-orientations": [
        ("Conservative", (728 * K, 728 * K), (1.4, 1.4)),
        ("Mid-Range", (910 * K, 910 * K), (1.7, 1.7)),
        ("Optimistic", (1090 * K, 1090 * K), (2.0, 2.0)),
    ],
    "scenario-columns": [
        ("Conservative", (774 * K, 774 * K), (3.0, 3.0)),
        ("Moderate", (1130 * K, 1130 * K), (4.3, 4.3)),
        ("Optimistic", (1500 * K, 1500 * K), (5.7, 5.7)),
    ],
    "scenario-rows-canonical": [
        ("Conservative", (1.07 * M, 1.07 * M), (1.2, 1.2)),
        ("Base Case", (2.69 * M, 2.69 * M), (3.0, 3.0)),
        ("Optimistic", (5.37 * M, 5.37 * M), (6.0, 6.0)),
    ],
    "scenario-rows-multi-table": [
        ("Conservative", (575 * K, 862 * K), None),
        ("Moderate", (1.15 * M, 1.59 * M), None),
        ("Aggressive", (1.72 * M, 2.16 * M), None),
    ],
    "sparse-no-scenario-table": [],
    "validated-assumption-table": [
        ("Conservative", (0.638 * M, 0.722 * M), None),
        ("Base Case", (0.85 * M, 0.935 * M), None),
        ("Optimistic", (1.06 * M, 1.19 * M), None),
    ],
}

CASES_AS_COLUMNS = ("scenario-both-orientations", "scenario-columns")


def paper(shape):
    path = FIXTURES / f"paper-excerpt-{shape}.json"
    return json.loads(path.read_text())["opportunity"]["research_paper_natural"]


def parse(shape):
    missing = MissingFields()
    return parse_scenario_cases(paper(shape), missing, document=PAPER, opportunity=OPPORTUNITY), missing


def dollars(cases):
    return [case.incremental_ebitda.value for case in cases]


def margins(cases):
    return [c.margin_impact_pp.value if c.margin_impact_pp else None for c in cases]


@pytest.mark.parametrize("shape", sorted(EXPECTED))
def test_every_fixture_parses_to_its_measured_cases(shape):
    cases, _ = parse(shape)
    assert [case.label for case in cases] == [e[0] for e in EXPECTED[shape]]
    assert dollars(cases) == pytest.approx([e[1] for e in EXPECTED[shape]])
    assert margins(cases) == [e[2] for e in EXPECTED[shape]]


@pytest.mark.parametrize("shape", sorted(EXPECTED))
def test_every_returned_figure_carries_a_span_from_its_own_paper(shape):
    text = paper(shape)
    for case in parse(shape)[0]:
        assert case.incremental_ebitda.span in text
        assert case.incremental_ebitda.field == DOLLAR_FIELD
        if case.margin_impact_pp is not None:
            assert case.margin_impact_pp.span in text
            assert case.margin_impact_pp.field == MARGIN_FIELD


@pytest.mark.parametrize("shape", CASES_AS_COLUMNS)
def test_cases_as_columns_are_read_without_a_scenario_header(shape):
    """The column-shaped tables, one of which has no `| Scenario |` anywhere.

    A parser keying on that string finds nothing on `scenario-columns`, which
    carries a perfectly good scenario table. Every case in a column-shaped
    reading is read off one impact row, so the cases share a span.
    """
    cases, _ = parse(shape)
    assert len({case.incremental_ebitda.span for case in cases}) == 1
    if shape == "scenario-columns":
        assert "| Scenario |" not in paper(shape)


@pytest.mark.parametrize(
    "shape", [s for s in sorted(EXPECTED) if EXPECTED[s] and s not in CASES_AS_COLUMNS]
)
def test_cases_as_rows_are_read_one_case_per_row(shape):
    cases, _ = parse(shape)
    assert len({case.incremental_ebitda.span for case in cases}) == len(cases)


def test_decoy_component_tables_lose_to_the_consolidated_table():
    """Decoy 2. Two row-shaped component tables roll up into a column-shaped
    consolidated one, and only the consolidated table carries the margin row.
    """
    text = paper("scenario-both-orientations")
    components = [t for t in tables(text) if t.header[0] == "Scenario"]
    assert len(components) == 2
    assert all(read_cases(t, text, document=PAPER, opportunity=OPPORTUNITY) for t in components)  # genuine candidates, not junk
    assert all(c.margin_impact_pp is None for t in components for c in read_cases(t, text, document=PAPER, opportunity=OPPORTUNITY))

    cases, _ = parse("scenario-both-orientations")
    cogs_component = [(522 * K, 522 * K), (652 * K, 652 * K), (783 * K, 783 * K)]
    payroll_component = [(367 * K, 367 * K), (459 * K, 459 * K), (551 * K, 551 * K)]
    assert dollars(cases) != pytest.approx(cogs_component)
    assert dollars(cases) != pytest.approx(payroll_component)
    consolidated = [e[1] for e in EXPECTED["scenario-both-orientations"]]
    assert dollars(cases) == pytest.approx(consolidated)


def rejected_readings(shape, marker):
    """What a table naming `marker` reads as on its own, and whether it survives.

    Both return-table decoys parse perfectly well as scenario tables, so
    asserting only on the final answer proves nothing: on one of them the right
    table happens to come first, and a parser that had lost the rule entirely
    would still report green. These tests assert the rejection itself.
    """
    text = paper(shape)
    found = [t for t in tables(text) if any(marker in cell for cell in t.header)]
    if not found:
        found = [t for t in tables(text) if any(marker in r.cells[0] for r in t.rows)]
    assert len(found) == 1
    return read_cases(found[0], text, document=PAPER, opportunity=OPPORTUNITY), is_return_table(found[0])


def test_decoy_net_of_investment_roi_table_is_not_selected():
    """Decoy 1. Three tables under identical `| Scenario |` headers with
    identical case names; the third is a net-of-investment ROI table, separable
    only by its `Annual Investment`, `Net EBITDA Gain` and `Payback` columns.
    """
    decoy, is_return = rejected_readings("scenario-rows-multi-table", "Payback")
    on_its_own = [(575 * K, 575 * K), (1.3 * M, 1.3 * M), (2.16 * M, 2.16 * M)]
    assert dollars(decoy) == pytest.approx(on_its_own)
    assert is_return
    assert dollars(parse("scenario-rows-multi-table")[0]) != pytest.approx(on_its_own)


def test_decoy_cost_payback_and_roi_table_is_not_selected():
    """Decoy 3. A column-shaped `| Metric | Conservative | Base Case |
    Optimistic |` of implementation cost, payback and ROI rather than impact. It
    even carries an EBITDA row whose figures match the real table's, so being
    right about the numbers is not evidence of having read the right table.
    """
    decoy, is_return = rejected_readings("no-timeline-chart", "Simple Payback")
    year_one_partial_ramp = [(510 * K, 510 * K), (0.85 * M, 0.85 * M), (1.28 * M, 1.28 * M)]
    assert dollars(decoy) == pytest.approx(year_one_partial_ramp)
    assert is_return
    cases, _ = parse("no-timeline-chart")
    assert dollars(cases) != pytest.approx(year_one_partial_ramp)
    assert len({case.incremental_ebitda.span for case in cases}) == len(cases)


def test_decoy_gain_percentage_sensitivity_table_is_not_selected():
    """Decoy 4. `| Baseline Used | 2% Gain | 5% Gain | 10% Gain |`, whose columns
    are gain percentages and whose rows are baselines rather than cases.
    """
    text = paper("scenario-rows-canonical")
    sensitivity = [t for t in tables(text) if t.header[0] == "Baseline Used"]
    assert len(sensitivity) == 1
    assert read_cases(sensitivity[0], text, document=PAPER, opportunity=OPPORTUNITY) is None  # no row of it names an impact

    cases, _ = parse("scenario-rows-canonical")
    assert [case.label for case in cases] != ["2% Gain", "5% Gain", "10% Gain"]
    assert len({case.incremental_ebitda.span for case in cases}) == 3


def test_decoy_percent_rows_that_are_not_a_margin_impact_are_not_read_as_margin():
    """Decoy 5. Three percent-shaped rows and only one is the margin impact: a
    gross margin driver, a resulting margin level, and the EBITDA margin lift.
    And on another paper `% of Current EBITDA` is a share of the base, so a
    parser reading it as percentage points is wrong by construction.
    """
    columns, _ = parse("scenario-columns")
    gross_margin_recovery = [(3.0, 3.0), (4.0, 4.0), (5.0, 5.0)]
    resulting_margin_level = [(27.4, 27.4), (28.7, 28.7), (30.1, 30.1)]
    assert margins(columns) != gross_margin_recovery
    assert margins(columns) != resulting_margin_level
    assert margins(columns) == [(3.0, 3.0), (4.3, 4.3), (5.7, 5.7)]

    multi, missing = parse("scenario-rows-multi-table")
    share_of_current_ebitda = [(8.5, 12.7), (17.0, 23.3), (25.4, 31.8)]
    assert margins(multi) != share_of_current_ebitda
    assert margins(multi) == [None, None, None]
    assert MARGIN_FIELD in missing.names


def test_a_margin_label_must_name_both_ebitda_and_margin():
    """The first of the two guards on the margin, asserted on its own.

    Both guards independently refuse `% of Current EBITDA`, so loosening either
    one alone left every other test green and the parser still correct. Each is
    now caught by itself. This one refuses a share of the base, which is
    EBITDA-named but not a margin, and a gross margin driver, which is
    margin-named but not EBITDA.
    """
    assert not is_margin_label("% of Current EBITDA")
    assert not is_margin_label("% of Current EBITDA ($6.78M)")
    assert not is_margin_label("Gross Margin Recovery (pp improvement)")
    assert is_margin_label("EBITDA Margin Impact (pp)")
    assert is_margin_label("EBITDA Margin Lift")


def test_a_percentage_that_is_not_percentage_points_is_not_a_margin_figure():
    """The second guard. A margin impact is stated in pp, so a bare percent is
    not one, whatever its column is called. `~27.4%` is a resulting margin level
    and `12.7%` is a share of current EBITDA; neither is a margin impact.
    """
    assert read_pp("12.7%") is None
    assert read_pp("~27.4%") is None
    assert read_pp("8.5–12.7%") is None
    assert read_pp("+2.3 pp") == (2.3, 2.3)
    assert read_pp("~1.2 pp") == (1.2, 1.2)


@pytest.mark.parametrize(
    "shape", [s for s in sorted(EXPECTED) if EXPECTED[s] and EXPECTED[s][0][2] is None]
)
def test_a_paper_with_no_margin_column_records_the_margin_missing(shape):
    cases, missing = parse(shape)
    assert all(case.margin_impact_pp is None for case in cases)
    assert missing.names == [MARGIN_FIELD]
    assert all(reason.strip() for _, reason in missing.entries)


def test_a_paper_with_no_scenario_table_is_missing_fields_rather_than_an_error():
    cases, missing = parse("sparse-no-scenario-table")
    assert cases == []
    assert missing.names == [DOLLAR_FIELD, MARGIN_FIELD]


def test_a_case_label_drops_the_figure_or_parenthetical_it_carries():
    """`Conservative: $1.15M commodity volume` and `**Conservative (4%)**` are
    both the case named Conservative, and the raw cell is still the span.
    """
    decorated, _ = parse("assumption-table-and-scenario-rows")
    assert decorated[0].label == "Conservative"
    assert "Conservative: $1.15M" in decorated[0].incremental_ebitda.span

    parenthesised, _ = parse("no-timeline-chart")
    assert parenthesised[0].label == "Conservative"
    assert "**Conservative (4%)**" in parenthesised[0].incremental_ebitda.span


def test_cell_shapes_that_break_a_naive_number_parse():
    """Ranges, tildes, markdown bold and comma grouping, all from the fixtures."""
    ranges, _ = parse("validated-assumption-table")
    assert ranges[0].incremental_ebitda.value == pytest.approx((638 * K, 722 * K))

    tildes, _ = parse("assumption-table-and-scenario-rows")
    assert "~$632K" in tildes[0].incremental_ebitda.span
    assert tildes[2].incremental_ebitda.value == pytest.approx((1260 * K, 1260 * K))

    bold, _ = parse("scenario-columns")
    assert "**$1,130K**" in bold[1].incremental_ebitda.span
    assert bold[1].incremental_ebitda.value == pytest.approx((1130 * K, 1130 * K))


# --- E11 Stage 2b: notation, widened 2026-08-19 ------------------------------
#
# Both readers accepted the letter suffixes and nothing spelled out, so a paper
# writing its numbers in words lost them on BOTH passes. Measured on a live
# paper 2026-08-18: the model quoted `$3.571 million` correctly, the span
# verified, and the figure was still dropped. Widening the magnitude vocabulary
# is strictly additive; the residue check that bounds false positives is
# untouched, and the tests below hold it to that.


def test_money_reads_a_spelled_magnitude_as_well_as_a_letter_suffix():
    """`$3.571 million` and `$3.571M` are the same figure and now read alike.

    Before Stage 2b every spelled form on the right returned None while the
    letter form beside it read correctly.
    """
    assert _money("$3.571M") == (3_571_000.0, 3_571_000.0)
    assert _money("$3.571 million") == (3_571_000.0, 3_571_000.0)
    assert _money("$1.8B") == (1.8e9, 1.8e9)
    assert _money("$1.8 billion") == (1.8e9, 1.8e9)
    assert _money("$149K") == (149_000.0, 149_000.0)
    assert _money("$149 thousand") == (149_000.0, 149_000.0)


def test_a_spelled_magnitude_is_read_whatever_its_case():
    """Papers capitalise mid-sentence and in a header, so the word is folded."""
    assert _money("$3.571 MILLION") == (3_571_000.0, 3_571_000.0)
    assert _money("$3.571 Million") == (3_571_000.0, 3_571_000.0)


def test_the_magnitude_word_stands_in_for_the_dollar_sign_but_a_bare_number_does_not():
    """A spelled magnitude is itself a currency-shaped claim, so the `$` is
    optional beside it. A number with neither is not money and never was: that
    is what keeps a plain count column from reading as dollars.
    """
    assert _money("3.571 million") == (3_571_000.0, 3_571_000.0)
    assert _money("3.571") is None
    assert _money("12") is None
    assert _money("million") is None


def test_every_decoration_the_narrow_reader_accepted_still_reads_and_now_reads_spelled():
    """The sign paren, the tilde, the explicit `+`, the `/yr` suffix and the
    two-endpoint range, each confirmed live on real corpus cells, all carry over
    to the spelled forms rather than being suffix-only privileges.
    """
    assert _money("+$0.69M") == (690_000.0, 690_000.0)
    assert _money("$1.0M+") == (1e6, 1e6)
    assert _money("$149K/yr") == (149_000.0, 149_000.0)
    assert _money("($250K)") == (-250_000.0, -250_000.0)
    assert _money("~$18.5M") == (18_500_000.0, 18_500_000.0)
    assert _money("$575K-$862K") == (575_000.0, 862_000.0)

    assert _money("+$0.69 million") == (690_000.0, 690_000.0)
    assert _money("$149 thousand/yr") == (149_000.0, 149_000.0)
    assert _money("($250 thousand)") == (-250_000.0, -250_000.0)
    assert _money("~$18.5 million") == (18_500_000.0, 18_500_000.0)
    assert _money("$1.5 million-$2.0 million") == (1.5e6, 2e6)
    assert _money("$1.5M-2.0 million") == (1.5e6, 2e6)


def test_money_refuses_a_spelled_figure_buried_in_prose_exactly_as_it_refuses_a_suffixed_one():
    """The residue check is the false-positive defence and Stage 2b leaves it
    alone, so widening the vocabulary must not widen what gets through.

    `4-6% of CY2025 administrative payroll ($1.93M) [27]` is the live Basis
    cell E7d caught on 2026-08-16. Every spelled rewriting of it is refused for
    the same reason: what is left after the money is removed is prose.
    """
    assert _money("4-6% of CY2025 administrative payroll ($1.93M) [27]") is None
    assert _money("4-6% of CY2025 administrative payroll ($1.93 million) [27]") is None
    assert _money(
        "4-6 percent of CY2025 administrative payroll (2.33 million) [27]"
    ) is None
    assert _money("roughly $3.5 million of annual savings") is None
    assert _money("3.5 million hours of senior operator time") is None
    # A spelled currency word is not acceptable residue either, because Stage 2b
    # widened the magnitudes and deliberately not the residue.
    assert _money("USD 3.5 million") is None
    assert _money("$3.5 million dollars") is None


def test_money_refuses_three_or_more_figures_however_they_are_written():
    """One or two tokens is a figure or a range; three is a sentence."""
    assert _money("$1.0M / $2.0M / $3.0M") is None
    assert _money("$1.0 million / $2.0 million / $3.0 million") is None


def test_money_refuses_a_range_whose_first_endpoint_elides_the_magnitude():
    """`$1.5-2.0 million` states one magnitude for two endpoints, so the low one
    could be 1.5 dollars or 1.5 million and the cell does not say which.

    This is the one hazard the `$` becoming optional creates, and refusing is
    the answer: before Stage 2b the cell was refused because only `$1.5` matched
    and `-2.0 million` was left over as residue. Reading it as
    `(1.5, 2000000.0)` would be a wrong figure where there used to be an honest
    absence, which is the trade the golden rule forbids. Every token in a cell
    must state its magnitude the same way.
    """
    assert _money("$1.5-2.0 million") is None
    assert _money("1.5-2.0 million") is None
    assert _money("$1.5M-$2.0M") == (1.5e6, 2e6)


def test_read_pp_reads_spelled_percentage_points_as_well_as_the_pp_token():
    """A margin impact spelled out is still a margin impact. Before Stage 2b
    every spelled form here returned None.
    """
    assert read_pp("1.9pp") == (1.9, 1.9)
    assert read_pp("1.9 percentage points") == (1.9, 1.9)
    assert read_pp("1.0 percentage point") == (1.0, 1.0)
    assert read_pp("1.9 percentage-point") == (1.9, 1.9)
    assert read_pp("+2.5 PERCENTAGE POINTS") == (2.5, 2.5)


def test_read_pp_still_refuses_a_percent_and_a_token_that_merely_starts_with_pp():
    """The pp/percent separation is the whole reason `read_pp` exists, and the
    spelled unit must not blur it: `1.9 percentage points` is a margin impact,
    `1.9%` is a margin level, and `ppt` is neither of this repo's tokens.
    """
    assert read_pp("12.7%") is None
    assert read_pp("~27.4%") is None
    assert read_pp("1.9 ppt") is None
    assert read_pp("1.9 percentage") is None


def test_read_pp_reads_both_endpoints_of_a_range_whatever_separates_them():
    """E11 Stage 2d, 2026-08-19. Replaces Stage 2b's KNOWN_DEFECT test, which
    pinned the wrong values rather than asserting the right ones.

    Two defects, one cell. `_PP`'s number group carried an optional sign, so the
    separator in `0.9-1.9pp` was read as a minus; and the unit had to follow the
    number immediately, so an endpoint that does not carry its own `pp` was not
    matched at all. Together `0.9-1.9pp` came back `(-1.9, -1.9)`: the low
    endpoint gone and the high one negated. This is the only failure mode in the
    repo that produces a WRONG FIGURE rather than an absence, which is why it
    outranks a missing field: no completeness gate can catch it, because the
    field is not missing.

    A range separator is a hyphen, an en dash, an em dash or the word `to`, and
    either endpoint may carry the unit or leave it to the other.
    """
    assert read_pp("0.9-1.9pp") == (0.9, 1.9)
    assert read_pp("0.9\u20131.9pp") == (0.9, 1.9)
    assert read_pp("0.9\u20141.9pp") == (0.9, 1.9)
    assert read_pp("1.2 to 6.0pp") == (1.2, 6.0)
    assert read_pp("1.2 TO 6.0 PP") == (1.2, 6.0)
    assert read_pp("0.9-1.9 percentage points") == (0.9, 1.9)
    assert read_pp("0.9 \u2013 1.9 pp") == (0.9, 1.9)
    # Both endpoints carrying the unit is the shape `_money` was always immune
    # on, and the hyphen form of it was still wrong: `(-1.9, 0.9)`.
    assert read_pp("0.9pp-1.9pp") == (0.9, 1.9)
    assert read_pp("0.9 pp - 1.9 pp") == (0.9, 1.9)
    assert read_pp("0.9pp to 1.9pp") == (0.9, 1.9)


def test_read_pp_reads_the_two_prose_ranges_the_committed_corpus_states():
    """Both are real. `sparse-no-scenario-table` and `no-timeline-chart` each
    state a percentage-point RANGE in prose, and every pp cell in every table in
    the corpus states a single figure. That is the whole exposure map for this
    defect: nothing on a deck is wrong today, and everything is wrong the moment
    the second pass quotes one of these two sentences.
    """
    assert read_pp("0.9\u20131.9 percentage points") == (0.9, 1.9)
    assert read_pp("2.3\u20134.1 percentage-point") == (2.3, 4.1)
    assert read_pp(
        "the EBITDA margin impact is approximately **0.9\u20131.9 percentage points**"
    ) == (0.9, 1.9)
    assert read_pp(
        "representing a **2.3\u20134.1 percentage-point** improvement"
    ) == (2.3, 4.1)


def test_read_pp_keeps_a_genuine_negative_negative():
    """A `-` leading the FIRST token is a minus and stays one. The fix removes
    the sign only where a `-` sits between two numbers, which is a separator.

    `-2.3 pp` and `-2.4 pp` are real cells on `no-timeline-chart`, on the two
    margin rows of a variance table, and they are the reason a blunt "strip the
    sign" fix would have been worse than the defect.
    """
    assert read_pp("-2.4 pp") == (-2.4, -2.4)
    assert read_pp("-2.3 pp") == (-2.3, -2.3)
    assert read_pp("-0.9pp") == (-0.9, -0.9)
    assert read_pp("-2.4 percentage points") == (-2.4, -2.4)
    # Both endpoints of a range may carry their own sign, in either order.
    assert read_pp("-0.9 to -1.9pp") == (-1.9, -0.9)
    assert read_pp("-1.9 to -0.9pp") == (-1.9, -0.9)
    # A row of a variance table, quoted whole, still reads its one negative.
    assert read_pp("| 60.4% | 58.1% | 65.1% | -2.3 pp |") == (-2.3, -2.3)


def test_read_pp_reads_a_parenthesised_figure_as_negative_the_way_money_does():
    """`(2.4 pp)` is accounting notation for a negative, and `_money` has read it
    that way since E7d. `read_pp` did not, so the one cell shape where the two
    readers disagreed produced a positive where the paper stated a loss.

    The rule is scoped the way `_money`'s residue check scopes it: the parens
    must wrap the whole cell and what is left after the figure must be nothing
    but signs, tildes and separators. `(a 1.9 pp gain in the base case)` is prose
    inside parens, states no sign, and is unchanged.

    An explicit sign wins. `_money`'s number group carries no sign so it cannot
    double-negate; `read_pp`'s does, and `(-2.4 pp)` reads -2.4 today.
    """
    assert read_pp("(2.4 pp)") == (-2.4, -2.4)
    assert read_pp("(2.4pp)") == (-2.4, -2.4)
    assert read_pp("(1.9 percentage points)") == (-1.9, -1.9)
    assert read_pp("(0.9-1.9pp)") == (-1.9, -0.9)
    # Stated sign wins over the notation, and nothing double-negates.
    assert read_pp("(-2.4 pp)") == (-2.4, -2.4)
    assert read_pp("(+2.4 pp)") == (2.4, 2.4)
    # Prose in parens states no sign of its own, so it takes none.
    assert read_pp("(a 1.9 pp gain in the base case)") == (1.9, 1.9)


def test_read_pp_does_not_turn_a_hyphenated_token_into_a_range():
    """The separator rule reads a hyphen BETWEEN TWO NUMBERS as a separator, and
    a paper is full of hyphens that are not that: quarters, plan horizons, and a
    percent range that is not the pp figure the sentence ends on.
    """
    assert read_pp("Q3-2025 margin of 1.9pp") == (1.9, 1.9)
    assert read_pp("the 5-year plan delivers 1.9pp") == (1.9, 1.9)
    assert read_pp("4-6% of payroll, worth 1.9pp") == (1.9, 1.9)
    assert read_pp("against the 2026 budget baseline \u2014 a 1.9pp gain") == (1.9, 1.9)


def test_read_pp_still_refuses_three_figures_however_they_are_written():
    """One figure or two is a reading; three is a sentence. A range counts as the
    two endpoints it states, so a range and a third figure is refused.
    """
    assert read_pp("1.9pp and 2.4pp and 3.0pp") is None
    assert read_pp("1.0pp / 2.0pp / 3.0pp") is None
    assert read_pp("1.2 to 6.0pp, and 3.0pp elsewhere") is None
    assert read_pp("no figure here") is None
    assert read_pp("Margin Impact (pp)") is None


# Every single-figure notation this reader read correctly before Stage 2d, one
# assertion each. The fix is a range fix, so the whole of this list is a
# regression pin: it is the proof behind "no reading that was correct moved",
# and it was read off the pristine reader by differential run before the fix
# existed, not written from memory.
UNMOVED_SINGLE_FIGURES = [
    ("1.9pp", (1.9, 1.9)),
    ("1.9 pp", (1.9, 1.9)),
    ("3.0 pp", (3.0, 3.0)),
    ("4.0 pp", (4.0, 4.0)),
    ("5.0 pp", (5.0, 5.0)),
    ("+2.3 pp", (2.3, 2.3)),
    ("+3.2 pp", (3.2, 3.2)),
    ("+3.3 pp", (3.3, 3.3)),
    ("+4.1 pp", (4.1, 4.1)),
    ("+4.2 pp", (4.2, 4.2)),
    ("+4.6pp", (4.6, 4.6)),
    ("+0.9pp", (0.9, 0.9)),
    ("-2.3 pp", (-2.3, -2.3)),
    ("-2.4 pp", (-2.4, -2.4)),
    ("**+1.4 pp**", (1.4, 1.4)),
    ("**+1.7 pp**", (1.7, 1.7)),
    ("**+2.0 pp**", (2.0, 2.0)),
    ("**+3.0 pp**", (3.0, 3.0)),
    ("**+4.3 pp**", (4.3, 4.3)),
    ("**+5.7 pp**", (5.7, 5.7)),
    ("~1.2 pp", (1.2, 1.2)),
    ("~1.2pp", (1.2, 1.2)),
    ("~3.0 pp", (3.0, 3.0)),
    ("~6.0 pp", (6.0, 6.0)),
    ("+2.5 PERCENTAGE POINTS", (2.5, 2.5)),
    ("1.9 percentage points", (1.9, 1.9)),
    ("1.0 percentage point", (1.0, 1.0)),
    ("1.9 percentage-point", (1.9, 1.9)),
    ("1.9 percentage  points", (1.9, 1.9)),
    # A sign detached from its number was never read as a sign, and still is not.
    ("- 2.4 pp", (2.4, 2.4)),
    # Two figures separated by something that is not a range separator are two
    # figures, and were read as a pair before Stage 2d.
    ("**+1.4 pp** | **+1.7 pp**", (1.4, 1.7)),
    ("1.9 pp / 2.4 pp", (1.9, 2.4)),
    # Prose carrying one pp figure was accepted before Stage 2d and still is.
    # `read_pp` has no residue check, and adding one here would refuse the very
    # second-pass quotations this reader exists to serve.
    ("margin improves by 1.9pp in the base case", (1.9, 1.9)),
    ("a 1.9 percentage point gain across the portfolio", (1.9, 1.9)),
]


@pytest.mark.parametrize("cell,expected", UNMOVED_SINGLE_FIGURES)
def test_read_pp_reads_every_previously_correct_notation_identically(cell, expected):
    assert read_pp(cell) == expected


def test_money_cannot_read_a_range_separator_as_a_sign():
    """The other half of Stage 2d's audit, and the answer is structural rather
    than a fix: neither `_MONEY` branch has a sign group at all, so no separator
    can ever reach a value. The `$`-led branch takes its sign from a parenthesis
    and the spelled branch, added by Stage 2b, inherited no sign group either.

    Which means the spelled form is immune for a different reason than the
    `$` form: not because each endpoint carries its own `$`, but because no
    endpoint carries a sign. Asserted rather than assumed, because Stage 2d was
    told not to take the per-endpoint `$` on trust.

    A `to` range is refused, in both forms: `to` is not acceptable residue, and
    Stage 2d did not widen the residue check. That is an absence, not a wrong
    figure.
    """
    assert _money("1.5 million-2.0 million") == (1.5e6, 2.0e6)
    assert _money("1.5 million - 2.0 million") == (1.5e6, 2.0e6)
    assert _money("1.5 million – 2.0 million") == (1.5e6, 2.0e6)
    assert _money("$1.5M-$2.0M") == (1.5e6, 2.0e6)
    assert _money("$1.5M – $2.0M") == (1.5e6, 2.0e6)
    assert _money("$1.5M to $2.0M") is None
    assert _money("1.5 million to 2.0 million") is None
    # A parenthesised cell is the only negative either branch reads, and it does
    # not double-negate, because there is no stated sign for it to fight with.
    assert _money("($1.5M)") == (-1.5e6, -1.5e6)
    assert _money("(1.5 million)") == (-1.5e6, -1.5e6)
    # A leading minus is not read as a sign by either branch. It survives as
    # acceptable residue and the figure comes back positive, which is a separate
    # defect from Stage 2d's and is recorded here rather than fixed.
    assert _money("-$1.5M") == (1.5e6, 1.5e6)
    assert _money("-1.5 million") == (1.5e6, 1.5e6)
