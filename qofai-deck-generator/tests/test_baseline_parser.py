"""Tests for the baseline parser (E5d).

Everything here runs against the eight committed excerpts under
`data-provider/fixtures/`. The expected values in `BASELINES` were read off
the fixtures' own reference lines and tables by hand on 2026-08-11 before the
parser existed, so they are a check on the parser rather than a recording of
it. Two of them are worth reading twice: `scenario-rows-canonical` states six
bases for one company in one table and the TTM row is the latest of them, and
`sparse-no-scenario-table` states an annualized Q1 2026 alongside CY 2025 and
the annualized figure is the one a deck must not present as an actual.

`test_no_figure_is_ever_compared_across_papers` is the constraint this parser
exists under rather than a property of any one paper. The papers disagree with
each other about the same company on committed data, so a test suite that
asserted agreement would be asserting a bug. The disagreement is measured
here and left alone.

Two tests use a hand-built paper rather than a captured one, and both say so
in their own docstring: the missing-fields path and the no-baseline path. No
committed fixture reaches either, because all eight state all three fields.
"""

import json
import pathlib

import pytest

from baseline_parser import (
    FIELDS,
    Baseline,
    basis_of,
    is_company_source,
    is_period,
    metric_of,
    parse_baseline,
    strip_charts,
)
from source_span import PAPER, Opportunity, MissingFields

# The opportunity these readings are FOR (item 15, 2026-09-13). A figure names
# one the way it names its document, so a parser test has to state one, and
# stating a stand-in here is the same trade `PAPER` makes: these tests are about
# reading a table, not about which engagement asked for it.
OPPORTUNITY = Opportunity(id="OPP-PARSER-TEST")


FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "data-provider" / "fixtures"

# shape -> (period, basis, source shape, revenue, EBITDA, EBITDA margin)
BASELINES = {
    "assumption-table-and-scenario-rows": (
        "LTM Sep 2025", "ltm", "reference line", "~$27.9M", "~$6.78M", "~25%"),
    "no-timeline-chart": (
        "FY2025", "actual", "reference line", "$59.2M", "$24.1M", "40.8%"),
    "scenario-both-orientations": (
        "FY2025", "actual", "table row", "$53.7M", "$22.9M", "42.5%"),
    "scenario-columns": (
        "LTM Sep 2025", "ltm", "table row", "$26.2M", "$6.41M", "24.4%"),
    "scenario-rows-canonical": (
        "TTM (Apr 2025–Mar 2026)", "ttm", "table row",
        "$67,600,000", "$12,000,000", "17.8%"),
    "scenario-rows-multi-table": (
        "LTM", "ltm", "reference line", "~$27.9M", "~$6.78M", "25%"),
    "sparse-no-scenario-table": (
        "CY 2025", "actual", "table row", "$55.5M", "$24.2M", "43.5%"),
    "validated-assumption-table": (
        "FY2025", "actual", "reference line", "$55.6M", "$24.2M", "43.6%"),
}
ALL_FIXTURES = tuple(BASELINES)


def paper(shape):
    path = FIXTURES / f"paper-excerpt-{shape}.json"
    return json.loads(path.read_text())["opportunity"]["research_paper_natural"]


def parse(shape):
    missing = MissingFields()
    return parse_baseline(paper(shape), missing, document=PAPER, opportunity=OPPORTUNITY), missing


def reading(baseline):
    return (
        baseline.period,
        baseline.basis,
        baseline.shape,
        baseline.revenue.value,
        baseline.ebitda.value,
        baseline.ebitda_margin.value,
    )


@pytest.mark.parametrize("shape", ALL_FIXTURES)
def test_every_fixture_yields_revenue_ebitda_and_margin_with_a_period_and_a_basis(shape):
    baseline, missing = parse(shape)
    assert reading(baseline) == BASELINES[shape]
    assert missing.names == []


@pytest.mark.parametrize("shape", ALL_FIXTURES)
def test_every_figure_carries_a_span_from_its_own_paper(shape):
    text = paper(shape)
    baseline, _ = parse(shape)
    for figure in (baseline.revenue, baseline.ebitda, baseline.ebitda_margin):
        assert figure.span in text


def test_the_margin_is_not_taken_from_a_different_period_than_the_revenue():
    """One statement wins, not one figure per field. On
    `scenario-rows-canonical` a per-field parser takes revenue and EBITDA from
    the reference line, which states FY2025 and no margin, and then has to
    reach into an appendix row of some other period for the margin: FY2025
    revenue beside a TTM margin, coherent-looking and incoherent. All three
    figures here come off one row of one table.
    """
    text = paper("scenario-rows-canonical")
    assert "FY2025 revenue of $57.7M" in text
    baseline, _ = parse("scenario-rows-canonical")
    assert baseline.revenue.value != "$57.7M"
    assert baseline.revenue.span == baseline.ebitda.span == baseline.ebitda_margin.span
    assert baseline.revenue.span.startswith("| TTM (Apr 2025–Mar 2026) |")


@pytest.mark.parametrize("shape", ALL_FIXTURES)
def test_no_baseline_is_anchored_on_a_heading(shape):
    """`### Current State` is not where the baseline lives: two fixtures carry
    none under it, and the placements verified 2026-08-11 span thirteen
    headings including `## References` and `### Appendix B`. Deleting every
    heading in the paper changes nothing here, which is stronger than asserting
    the winning span sits under some other heading.
    """
    text = paper(shape)
    headless = "\n".join(l for l in text.split("\n") if not l.lstrip().startswith("#"))
    assert reading(parse_baseline(headless, document=PAPER, opportunity=OPPORTUNITY)) == BASELINES[shape]


def test_decoy_the_benchmark_company_under_industry_overview_is_not_a_baseline():
    """`### Industry Overview` on `validated-assumption-table` states a cited
    revenue, EBITDA and margin for a named benchmark contractor. Every
    combination a detector could key on is there, and the figures are about
    another company, so the definition says subject company and this parser
    reads no prose at all. The sentence is present and survives chart
    stripping, so it is the parser rejecting it rather than the text being
    absent.
    """
    text = paper("validated-assumption-table")
    benchmark = "reported 2024 revenues of $648M with a gross margin of 21.1%"
    assert benchmark in strip_charts(text)
    baseline, _ = parse("validated-assumption-table")
    for figure in (baseline.revenue, baseline.ebitda, baseline.ebitda_margin):
        assert "$648M" not in figure.value and "$116M" not in figure.value
        assert benchmark not in figure.span


def test_decoy_the_return_on_investment_figure_lives_only_in_chart_metadata():
    """`#### Return on Investment` on `scenario-columns` is a real prose
    heading, appearing exactly once and surviving chart stripping. What is
    chart metadata is the FIGURE under it: that section's whole body is one
    `<Chart>` tag whose `description` states an EBITDA base of $6.44M, which no
    prose in the paper states. Stripping the tags takes the figure out and
    leaves the heading, so a line scanner would read a baseline the paper never
    wrote.
    """
    text = paper("scenario-columns")
    assert text.count("#### Return on Investment") == 1
    assert "#### Return on Investment" in strip_charts(text)
    assert 'description="Waterfall bridge chart' in text
    assert "$6.44M" in text and "6.44M" not in strip_charts(text)
    baseline, _ = parse("scenario-columns")
    assert baseline.ebitda.value == "$6.41M"


def test_a_reference_line_is_read_as_a_baseline_statement():
    """The reference list is often the most precise statement in the paper, and
    on four of the eight fixtures it is the one taken. The verbatim line here
    states all three fields for one period in one sentence.
    """
    baseline, _ = parse("validated-assumption-table")
    assert baseline.shape == "reference line"
    assert baseline.revenue.span.startswith("[1] Company Financial Statements:")
    assert "revenue of $55.6M, EBITDA of $24.2M (43.6% margin)" in baseline.revenue.span


def test_a_budget_reference_line_is_not_a_baseline_statement():
    """Two shapes of it. `validated-assumption-table` cites its FY2026 budget in
    the same list as its financial statements, stating revenue, EBITDA and a
    margin in the same shape as a baseline. And `sparse-no-scenario-table`'s
    only reference line cites the company's own knowledge base, so it clears
    the document-type filter, but it is a budget-versus-actual comparison
    carrying a budgeted revenue, an actual revenue and an EBITDA variance in
    one sentence. Both are excluded, which is why that fixture's baseline comes
    from a table.
    """
    assert "[4] FY2026 Budget:" in paper("validated-assumption-table")
    assert parse("validated-assumption-table")[0].revenue.value != "$78M"
    sparse = paper("sparse-no-scenario-table")
    assert "[3] LTS Knowledge Base: 2025 Budget vs. Actual" in sparse
    assert "Budgeted revenue of $65.2M vs. actual $55.5M" in sparse
    baseline, _ = parse("sparse-no-scenario-table")
    assert baseline.shape == "table row" and baseline.revenue.value != "$65.2M"


def test_a_budget_reference_line_is_rejected_by_the_predicate_itself():
    """`test_a_budget_reference_line_is_not_a_baseline_statement` above proves
    the two budget lines lose the ranking, not that the filter excludes them:
    a per-field parser recomputing the wrong result in its own body would pass
    that test too.

    Corrected 2026-08-15 (E7d), on where the filter lives. `is_company_source`
    is a citation-type filter only; excluding a projection is a per-statement
    question, because a heading word like "growth analysis" elsewhere on a
    line must not exclude a plain actual the line also states (E7c). The LTS
    line genuinely is a company source (it cites the knowledge base), so
    `is_company_source` now says so; parsing it alone, isolated from every
    other candidate the ranking could fall back on, is what proves the
    exclusion still bites.
    """
    validated = paper("validated-assumption-table")
    budget_line = (
        "[4] FY2026 Budget: Litware Terminal Services combined income "
        "statement budget showing $78M revenue, $39.5M EBITDA, $22.2M net "
        "income."
    )
    assert budget_line in validated
    assert is_company_source(budget_line) is False

    sparse = paper("sparse-no-scenario-table")
    lts_line = (
        "[3] LTS Knowledge Base: 2025 Budget vs. Actual comparison. Budgeted "
        "revenue of $65.2M vs. actual $55.5M; cumulative EBITDA variance of "
        "−$2.41M."
    )
    assert lts_line in sparse
    assert is_company_source(lts_line) is True
    assert parse_baseline(lts_line, document=PAPER, opportunity=OPPORTUNITY) is None


def test_the_gross_margin_beside_an_ebitda_figure_is_not_the_ebitda_margin():
    """The same reference line states both margins: `EBITDA of $24.2M (43.6%
    margin), gross margin of 51.3% for FY2025`. Reading the nearest percentage
    without that guard would put a gross margin in an EBITDA margin field.

    The second statement below is hand-built, and it has to be: no committed
    fixture states a gross margin BEFORE the EBITDA margin in the reach after
    an EBITDA figure, so on committed data the guard never fires and the
    ordering does the work on its own. Both orderings occur on real papers.
    """
    baseline, _ = parse("validated-assumption-table")
    assert baseline.ebitda_margin.value == "43.6%"
    assert "gross margin of 51.3%" in baseline.ebitda_margin.span
    built = "[1] Company Financial Statements: FY2025 EBITDA of $24.2M, gross"
    built += " margin of 51.3%, EBITDA margin of 43.6%.\n"
    reversed_order = parse_baseline(built, document=PAPER, opportunity=OPPORTUNITY)
    assert reversed_order.ebitda.value == "$24.2M"
    assert reversed_order.ebitda_margin is None


def test_a_figure_stated_before_its_metric_belongs_to_that_metric():
    """`[26] Company Financial Statements: Q1 2026 (Jan–Mar) showing $22.7M
    revenue, $10.6M EBITDA (46.7% margin), $5.78M net income` is verbatim from
    `validated-assumption-table`, and it puts each figure BEFORE its metric
    name. It is exercised in isolation here because Q1 2026 is a quarter and
    loses that paper's ranking to FY2025, so the fixture never surfaces it.
    Reading forward past the comma returns the EBITDA figure as the revenue.
    """
    assert "showing $22.7M revenue, $10.6M EBITDA (46.7% margin)" in paper(
        "validated-assumption-table"
    )
    line = "[26] Company Financial Statements: Q1 2026 (Jan–Mar) showing $22.7M"
    line += " revenue, $10.6M EBITDA (46.7% margin), $5.78M net income.\n"
    baseline = parse_baseline(line, document=PAPER, opportunity=OPPORTUNITY)
    assert baseline.period == "Q1 2026 (Jan–Mar)" and baseline.basis == "actual"
    assert baseline.revenue.value == "$22.7M"
    assert baseline.ebitda.value == "$10.6M"
    assert baseline.ebitda_margin.value == "46.7%"


def test_the_annualized_column_loses_to_the_actual_one():
    """`sparse-no-scenario-table` states CY 2025 actuals and a Q1 2026
    annualization in one table. The annualized revenue is over $90M against
    $55.5M actual, and an annualized figure presented as an actual is a real
    misstatement on a client-facing deck.
    """
    text = paper("sparse-no-scenario-table")
    assert "| Metric | CY 2024 | CY 2025 | Change | Q1 2026 (Annualized) |" in text
    baseline, _ = parse("sparse-no-scenario-table")
    assert baseline.basis == "actual" and baseline.revenue.value == "$55.5M"


def test_the_annualized_rank_term_is_not_covered_by_the_quarter_rule():
    """No committed fixture reaches this: the only annualized column in the
    eight papers is labelled `Q1 2026 (Annualized)`, which starts with a Q, so
    the quarter rule above already loses it and the annualized term is never
    the deciding one there. This paper is built so the annualized column
    carries the LATER year instead of a quarter label. The quarter rule ties
    (`FY2026 Annualized` is not a quarter, not a YTD, not a month), every other
    term ties or ranks in the wrong direction, and only the later-year term is
    left to decide, which would hand the win to the annualized column. Do not
    "simplify" this paper by dropping a year or starting the annualized column
    with a quarter label; either change removes the one term this test exists
    to exercise.
    """
    built = (
        "| Metric | FY2025 | FY2026 Annualized |\n"
        "|---|---|---|\n"
        "| Revenue | $40.0M | $52.0M |\n"
        "| EBITDA | $10.0M | $13.0M |\n"
        "| EBITDA Margin | 25.0% | 25.0% |\n"
    )
    baseline = parse_baseline(built, document=PAPER, opportunity=OPPORTUNITY)
    assert baseline.period == "FY2025"
    assert baseline.basis == "actual"
    assert baseline.revenue.value == "$40.0M"


def test_the_consolidated_period_row_wins_over_the_monthly_rows():
    """`scenario-both-orientations` has no reference line at all, so its
    baseline comes from an appendix table whose twelve monthly rows and one
    FY2025 total row all state revenue, EBITDA and a margin.
    """
    baseline, _ = parse("scenario-both-orientations")
    assert baseline.period == "FY2025"
    assert baseline.revenue.span.startswith("| **FY2025** |")


def test_the_assumption_table_is_left_to_e5c():
    """`| Assumption | ... |` tables carry baseline figures and belong to the
    assumption table parser. Their rows resolve as baselines otherwise:
    `Current LTM Revenue` normalizes to `revenue` and names a period, and on
    `validated-assumption-table` the row `FY2025 Revenue | $57.2M | $55.6M`
    would hand back $57.2M, the opportunity claim that paper exists to correct.

    The table below is hand-built, and it has to be: on both fixtures carrying
    an assumption table the rows that survive a value check add up to a
    one-field candidate, which loses the ranking to a three-field reference
    line either way, so removing the skip changes no committed result. A table
    that would win is the only way to show the boundary holds.
    """
    assert "| FY2025 Revenue | $57.2M | $55.6M | Company P&L [1] |" in paper(
        "validated-assumption-table"
    )
    built = (
        "| Assumption | Value | Source |\n"
        "|---|---|---|\n"
        "| FY2025 Revenue | $10.5M | Company P&L [1] |\n"
        "| FY2025 EBITDA | $2.1M | Company P&L [1] |\n"
        "| FY2025 EBITDA Margin | 20% | Company P&L [1] |\n"
    )
    missing = MissingFields()
    assert parse_baseline(built, missing, document=PAPER, opportunity=OPPORTUNITY) is None
    assert sorted(missing.names) == sorted(FIELDS)


def test_no_figure_is_ever_compared_across_papers():
    """The papers contradict each other about the same company, visibly, on
    committed data. That is correct rather than a bug, because a packet is
    per-opportunity, so this test measures the disagreement and asserts only
    that the parser reads one paper at a time. Nothing in the module
    reconciles, averages, picks a winner, or penalizes confidence for it.
    """
    one = {parse(shape)[0].revenue.value for shape in
           ("no-timeline-chart", "scenario-both-orientations",
            "scenario-rows-canonical", "validated-assumption-table")}
    assert one == {"$59.2M", "$53.7M", "$67,600,000", "$55.6M"}
    other = {parse(shape)[0].revenue.value for shape in
             ("assumption-table-and-scenario-rows", "scenario-columns",
              "scenario-rows-multi-table")}
    assert other == {"~$27.9M", "$26.2M"}


def test_a_statement_missing_a_field_records_it_rather_than_borrowing_one():
    """A hand-built paper, not a captured one: all eight fixtures state all
    three fields, so no committed paper reaches this path. The reference line
    states revenue only, and the margin and EBITDA are absences with reasons
    rather than figures taken from a neighbouring period.
    """
    text = "[1] Company Financial Statements: FY2025 revenue of $10.5M.\n"
    missing = MissingFields()
    baseline = parse_baseline(text, missing, document=PAPER, opportunity=OPPORTUNITY)
    assert baseline.revenue.value == "$10.5M" and baseline.period == "FY2025"
    assert baseline.ebitda is None and baseline.ebitda_margin is None
    assert sorted(missing.names) == sorted(FIELDS[1:])
    assert all("FY2025" in reason for _, reason in missing.entries)


def test_a_paper_with_no_baseline_statement_records_every_field_missing():
    """Also hand-built. A paper whose only financial figures are a projection
    and a table of neither metric nor period yields nothing, which drags
    `data_completeness` and lets the 0.70 gate decide rather than erroring.
    """
    text = "## Executive Summary\n\n[2] 2026 Budget: revenue of $78M.\n"
    missing = MissingFields()
    assert parse_baseline(text, missing, document=PAPER, opportunity=OPPORTUNITY) is None
    assert sorted(missing.names) == sorted(FIELDS)


@pytest.mark.parametrize(
    "label,field",
    [
        ("Revenue", "baseline_revenue"),
        ("FY2025 Revenue", "baseline_revenue"),
        ("Total Revenue", "baseline_revenue"),
        ("EBITDA", "baseline_ebitda"),
        ("EBITDA Margin %", "baseline_ebitda_margin"),
        ("**EBITDA Margin Impact (pp)**", None),
        ("Annual EBITDA Impact", None),
        ("Incremental Annual EBITDA", None),
        ("Average Revenue per Client (FY2025)", None),
        ("2026 Budgeted Revenue", None),
        ("Gross Margin", None),
        ("% of Current EBITDA ($6.78M)", None),
        ("Value", None),
    ],
)
def test_a_metric_label_is_matched_whole_rather_than_by_substring(label, field):
    """Every rejected label here is in the fixtures and every one of them
    contains a metric name. A substring match takes all of them.
    """
    assert metric_of(label) == field


@pytest.mark.parametrize(
    "label,period",
    [
        ("FY2025", True),
        ("CY 2024", True),
        ("LTM Sep 2025", True),
        ("LTM", True),
        ("TTM (Apr 2025–Mar 2026)", True),
        ("Q1-2026 Value", True),
        ("2025 YTD (Jan–Sep)", True),
        ("2026 Budget (Full Year)", False),
        ("Growth (2024→2025)", False),
        ("% of Revenue", False),
        ("Change", False),
        ("Value", False),
    ],
)
def test_a_period_label_excludes_a_projection_and_a_change_column(label, period):
    assert is_period(label) is period


def test_a_period_label_with_no_qualifier_reports_actual():
    """The basis is reported, never normalized, and never inferred beyond this:
    projections are excluded before ranking, so an unqualified historical period
    is an actual. The label itself stays in `period` for the reviewer.
    """
    assert basis_of("Q1 2026 (Annualized)") == "annualized"
    assert basis_of("2025 YTD (Jan–Sep)") == "ytd"
    assert basis_of("LTM Sep 2025") == "ltm"
    assert basis_of("FY2025 (Jan–Dec)") == "actual"


def test_stripping_charts_removes_every_chart_and_leaves_the_tables():
    """Thirty-six `<Chart>` tags across the eight fixtures, and a config body
    that is single-quote delimited and full of prose. Nothing chart-shaped may
    survive into the text this parser scans, and no table may be lost with it.
    """
    for shape in ALL_FIXTURES:
        stripped = strip_charts(paper(shape))
        assert "<Chart" not in stripped and "config=" not in stripped
        assert "backgroundColor" not in stripped
    assert sum(paper(s).count("<Chart") for s in ALL_FIXTURES) == 36
    assert "| Metric | Value | Source |" in strip_charts(paper("no-timeline-chart"))


def test_a_baseline_is_never_built_without_a_span():
    """E5a's guarantee, checked at this parser's own boundary: every figure it
    returns is a `SourcedFigure`, so there is no path here that produces a
    value with no source text behind it.
    """
    baseline, _ = parse("no-timeline-chart")
    assert isinstance(baseline, Baseline)
    for figure in (baseline.revenue, baseline.ebitda, baseline.ebitda_margin):
        assert figure.span.strip()
        assert figure.field in FIELDS
