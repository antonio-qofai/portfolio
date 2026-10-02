"""Tests for the six parser bugs E7c diagnosed and E7d fixes (2026-08-15), plus
a seventh found by E7d's own live re-measurement (2026-08-16): a false
positive in E7d's own dominant-bug fix.

Each fixture under `data-provider/fixtures/e7c-regression-drafts/` is
hand-authored synthetic evidence for one bug class; see each file's own
`_fixture.bug` field for the defect it reproduces.
"""

import json
import pathlib

from baseline_parser import _resolve, parse_baseline, EBITDA
from scenario_table_parser import DOLLAR_FIELD, MARGIN_FIELD, parse_scenario_cases
from source_span import PAPER, Opportunity, MissingFields

# The opportunity these readings are FOR (item 15, 2026-09-13). A figure names
# one the way it names its document, so a parser test has to state one, and
# stating a stand-in here is the same trade `PAPER` makes: these tests are about
# reading a table, not about which engagement asked for it.
OPPORTUNITY = Opportunity(id="OPP-PARSER-TEST")


FIXTURES = (
    pathlib.Path(__file__).resolve().parent.parent
    / "data-provider" / "fixtures" / "e7c-regression-drafts"
)


def paper(name):
    path = FIXTURES / f"bugfixture-{name}.json"
    return json.loads(path.read_text())["opportunity"]["research_paper_natural"]


def test_scenario_extra_descriptive_column_returns_the_total_not_a_line_item():
    """The dominant bug: a trailing Confidence column no longer disqualifies
    the whole row, and the total row wins over a line item that also fits.
    """
    cases = parse_scenario_cases(paper("scenario-extra-descriptive-column"), document=PAPER, opportunity=OPPORTUNITY)
    assert [c.label for c in cases] == ["Conservative", "Optimistic"]
    assert [c.incremental_ebitda.value for c in cases] == [
        (140000.0, 140000.0),
        (224000.0, 224000.0),
    ]
    assert all("Total Annual Impact" in c.incremental_ebitda.span for c in cases)


def test_scenario_narrow_impact_vocabulary_reads_benefit_as_an_impact_word():
    cases = parse_scenario_cases(paper("scenario-narrow-impact-vocabulary"), document=PAPER, opportunity=OPPORTUNITY)
    assert [c.label for c in cases] == ["Bear Case", "Base Case", "Bull Case"]
    assert [c.incremental_ebitda.value for c in cases] == [
        (95000.0, 95000.0), (190000.0, 190000.0), (310000.0, 310000.0),
    ]


def test_scenario_revenue_inside_a_parenthetical_does_not_exclude_the_column():
    cases = parse_scenario_cases(paper("scenario-revenue-substring-false-exclusion"), document=PAPER, opportunity=OPPORTUNITY)
    assert [c.label for c in cases] == ["Conservative", "Base Case", "Optimistic"]
    assert [c.incremental_ebitda.value for c in cases] == [
        (1.10e6, 1.10e6), (1.45e6, 1.45e6), (1.80e6, 1.80e6),
    ]


def test_baseline_before_and_after_regex_gaps_both_now_match():
    """A trailing space before EBITDA's metric word, and an adverb between a
    connector and the money, both used to lose EBITDA while revenue survived.
    """
    missing = MissingFields()
    baseline = parse_baseline(paper("baseline-before-after-regex-gaps"), missing, document=PAPER, opportunity=OPPORTUNITY)
    assert baseline.period == "TTM"
    assert baseline.revenue.value == "$18.5M"
    assert baseline.ebitda.value == "$4.2M"
    assert baseline.ebitda_margin.value == "22.7%"
    assert missing.names == []


def test_baseline_segmentation_colon_and_overexclusion_all_fixed_together():
    """Three compounding misses on one fixture: a date-range parenthetical
    split a metric from its value, a colon connector was unreadable, and
    `is_company_source` excluded a whole line for one unrelated word.
    """
    baseline = parse_baseline(paper("baseline-segmentation-colon-overexclusion"), document=PAPER, opportunity=OPPORTUNITY)
    assert baseline.revenue.value == "$18,500,000"
    assert baseline.ebitda.value == "$4,200,000"


def test_baseline_table_row_period_gap_resolve_inherits_the_row_above():
    """`_resolve` no longer drops a bare metric row when the row above it in
    the same table stated the period explicitly.

    This fixture's own EBITDA cell, `~$4.2M (~22.7% margin)`, combines two
    figures in one cell, a second, undiagnosed limitation outside the six bug
    classes E7c named: `_read_cell`'s whole-cell money/percent match can't
    split it, so `parse_baseline` on the full fixture is unchanged before and
    after this fix (EBITDA stays correctly missing, never a wrong value). The
    diagnosed mechanism itself is proven directly against `_resolve`.
    """
    assert _resolve("EBITDA", "Value", fallback_period="LTM Revenue") == (
        EBITDA, "LTM Revenue"
    )
    assert _resolve("EBITDA", "Value") is None

    missing = MissingFields()
    baseline = parse_baseline(paper("baseline-table-row-period-gap"), missing, document=PAPER, opportunity=OPPORTUNITY)
    assert baseline.revenue.value == "~$18.5M"
    assert baseline.ebitda is None
    assert "baseline_ebitda" in missing.names


def test_scenario_descriptive_column_with_an_incidental_dollar_stays_missing():
    """A false positive found on live data 2026-08-16, in E7d's own dominant
    fix. `_pick_row` requiring only that a row's own cells parse as money
    (rather than every column) is correct for a plain-text Confidence column,
    but a Basis column stating '$1.93M' inside supporting prose ('4-6% of
    CY2025 administrative payroll ($1.93M) [27]') is not a case value, and
    `_money` accepted it anyway because a dollar-shaped substring is present
    somewhere in the cell. Tightened so the money must be essentially the
    whole cell; refusing (missing) is correct here, not a floor to clear.
    """
    missing = MissingFields()
    cases = parse_scenario_cases(
        paper("scenario-descriptive-column-incidental-dollar"), missing
    , document=PAPER, opportunity=OPPORTUNITY)
    assert cases == []
    assert missing.names == [DOLLAR_FIELD, MARGIN_FIELD]
