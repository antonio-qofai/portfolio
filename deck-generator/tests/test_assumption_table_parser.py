"""Tests for the assumption table parser (E5c).

Everything here runs against the eight committed excerpts under
`data-provider/fixtures/`. Two carry a table with a first header cell reading
`Assumption`. Six carry none. The values below were read off the fixtures'
own markdown tables by hand on 2026-08-11 before the parser existed, so they
are a check on the parser rather than a recording of it.

`test_every_canonical_field_is_returned_or_missing_never_neither` is the
invariant this parser exists to hold: a packet field is a contract term, so
for every paper each of the seven canonical fields is either returned with a
span or recorded missing with a reason, never neither and never both. A
derived-name fallback that returns a slug of an unmatched row's own label
would put that field on neither side of the ledger, which is a hole in the
0.70 gate's `data_completeness` ratio, and it went undetected once already.

`test_read_value_takes_the_validated_value_not_the_opportunity_claim` is the
one test in this file guarding a mechanism no fixture exercises end to end.
`validated-assumption-table` is a validated-claims table, not a second
spelling of this parser's seven fields (see
`test_validated_assumption_table_carries_none_of_the_seven_fields`), so none
of its rows match a canonical field and `read_value` is never reached through
`parse_assumptions` on committed data. This test calls `read_value` directly
against the table object instead of re-deriving its column selection, because
a test that recomputes `len(header) - 2` in its own body asserts a fact about
the fixture rather than a property of the parser: it stays green even if
`read_value` is rewritten to a hardcoded wrong column, since shape one's
`len(header) - 2` and a hardcoded 1 happen to agree, and no fixture ever
exercises the four-column path end to end to expose the disagreement any
other way.
"""

import json
import pathlib

import pytest

from assumption_table_parser import (
    ASSUMPTION_FIELDS,
    EBITDA_FIELD,
    MARGIN_FIELD,
    find_assumption_table,
    parse_assumptions,
    read_value,
)
from source_span import PAPER, Opportunity, MissingFields

# The opportunity these readings are FOR (item 15, 2026-09-13). A figure names
# one the way it names its document, so a parser test has to state one, and
# stating a stand-in here is the same trade `PAPER` makes: these tests are about
# reading a table, not about which engagement asked for it.
OPPORTUNITY = Opportunity(id="OPP-PARSER-TEST")


FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "data-provider" / "fixtures"

NO_ASSUMPTION_TABLE = (
    "no-timeline-chart",
    "scenario-both-orientations",
    "scenario-columns",
    "scenario-rows-canonical",
    "scenario-rows-multi-table",
    "sparse-no-scenario-table",
)
ALL_FIXTURES = (
    *NO_ASSUMPTION_TABLE,
    "assumption-table-and-scenario-rows",
    "validated-assumption-table",
)


def paper(shape):
    path = FIXTURES / f"paper-excerpt-{shape}.json"
    return json.loads(path.read_text())["opportunity"]["research_paper_natural"]


def parse(shape):
    missing = MissingFields()
    return parse_assumptions(paper(shape), missing, document=PAPER, opportunity=OPPORTUNITY), missing


def values(figures):
    return {figure.field: figure.value for figure in figures}


@pytest.mark.parametrize("shape", ALL_FIXTURES)
def test_every_canonical_field_is_returned_or_missing_never_neither(shape):
    figures, missing = parse(shape)
    ledger = sorted(figure.field for figure in figures) + sorted(missing.names)
    assert sorted(ledger) == sorted(ASSUMPTION_FIELDS)


def test_shape_one_extracts_all_six_rows_and_the_margin_hidden_in_the_ebitda_cell():
    """Renamed 2026-08-12, from `..._six_matched_rows_...`. Six rows, seven
    fields: the sixth row now matches too, and the seventh field is the margin
    the EBITDA cell carries rather than a row of its own. Nothing is missing
    on this paper any more.
    """
    figures, missing = parse("assumption-table-and-scenario-rows")
    assert values(figures) == {
        "ltm_revenue": "~$27.9M",
        "ltm_ebitda": "~$6.78M (~25%)",
        "ltm_ebitda_margin": "~25%",
        "capacity_utilization": "25–30%",
        "fixed_cost_base": "~$2.4M",
        "backlog": "$14.9M+",
        "target_margin": "35–40%",
    }
    assert missing.names == []


def test_target_margin_resolves_from_the_real_row_label():
    """Renamed 2026-08-12, from
    `test_target_margin_alias_is_wrong_and_the_row_is_recorded_missing_with_the_real_label`,
    which asserted the opposite and passed only because
    `PAPER_FIELD_NAMES["target_margin"]` was wrong. It stored `Target Margin
    at Scale`, which occurs nowhere in the corpus and is not a substring of
    the real row label, so the lookup found nothing and the field was recorded
    missing. The map was corrected on 2026-08-12 and the record of the defect
    moved to `CHANGELOG.md`.

    This is the one test that bites for that correction. Restore the old
    label and it fails, which is why it asserts the value and the span rather
    than only that the field is absent from `missing`.
    """
    figures, missing = parse("assumption-table-and-scenario-rows")
    figure = next(f for f in figures if f.field == "target_margin")
    assert figure.value == "35–40%"
    assert "Target EBITDA Margin at Scale" in figure.span
    assert "target_margin" not in missing.names


def test_validated_assumption_table_carries_none_of_the_seven_fields():
    """A census error, found by reading the fixture rather than the census.
    `data-provider/PRD.md` section 2.1c treats this table as a second spelling
    of the assumption table, but its six rows, FY2025 revenue, MT handled,
    revenue per MT, gross margin, projected EBITDA, equipment utilization, are
    a validated-claims table on a different period basis than LTM. None of
    the seven canonical fields is even adjacent except the first, and it does
    not match. Every field is recorded missing, and nothing is returned.
    """
    figures, missing = parse("validated-assumption-table")
    assert figures == []
    assert set(missing.names) == set(ASSUMPTION_FIELDS)


def test_read_value_takes_the_validated_value_not_the_opportunity_claim():
    table = find_assumption_table(paper("validated-assumption-table"))
    row = next(row for row in table.rows if row.cells[0] == "FY2025 Revenue")
    assert read_value(table, row) == "$55.6M"
    assert row.cells[1] == "$57.2M"  # the opportunity claim, column two


def test_ebitda_field_carries_a_margin_only_where_the_cell_states_one():
    """Every value, including the margin, is a raw cleaned string, never a
    parsed number: the span is the evidence, and `float("25.0")` would drop
    the approximation marker `~25%` itself carries.
    """
    shape_one, _ = parse("assumption-table-and-scenario-rows")
    assert values(shape_one)[EBITDA_FIELD] == "~$6.78M (~25%)"
    assert values(shape_one)[MARGIN_FIELD] == "~25%"


@pytest.mark.parametrize("shape", ALL_FIXTURES)
def test_every_figure_carries_a_span_from_its_own_paper(shape):
    text = paper(shape)
    for figure in parse(shape)[0]:
        assert figure.span in text


@pytest.mark.parametrize("shape", NO_ASSUMPTION_TABLE)
def test_papers_without_an_assumption_table_record_every_field_missing(shape):
    figures, missing = parse(shape)
    assert figures == []
    assert set(missing.names) == set(ASSUMPTION_FIELDS)


def test_the_metric_value_source_decoy_is_not_read_as_an_assumption_table():
    """`| Metric | Value | Source |`: three columns, a `Source` column, and a
    value in column two, identical to shape one on every count that is not the
    first header cell.
    """
    text = paper("no-timeline-chart")
    assert "| Metric | Value | Source |" in text
    assert find_assumption_table(text) is None
