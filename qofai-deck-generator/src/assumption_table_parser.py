"""The assumption table parser (E5c): the paper's stated assumptions, verbatim.

There are two table shapes and no third. Shape one, three columns, `| Assumption
| Value | Source |`, value in column two. Shape two, four columns, `| Assumption
| Opportunity Claim | Validated Value | Source |`, value in column three. The
two share no header, no column count, and no row label, so this reads the value
by position from the end (`len(header) - 2`, second-to-last, before `Source`)
rather than by a fixed index, which is what keeps it from ever taking column
two on the four-column shape: that column is the claim the paper is
CORRECTING, and taking it returns a wrong figure carrying a real span.

The one table this must not be confused for is a decoy: `| Metric | Value |
Source |`, three columns with a `Source` column and a value in column two,
exactly like shape one. The only thing that discriminates it is the first
header cell, which reads `Metric`, not `Assumption`.

A packet field is a contract term, not a description of whatever the paper
happens to tabulate, so a row whose label matches none of a field's known
aliases yields no figure. There is no third state: every one of the seven
canonical fields below is, for every paper, either returned with a span or
recorded in `missing_fields` with a reason. Six of the eight committed
fixtures carry no assumption table at all and record all seven. The
`validated-assumption-table` fixture carries a table, but it is a
validated-claims table (FY2025 revenue, metric tons handled, revenue per ton,
gross margin, projected EBITDA, equipment utilization) rather than a second
spelling of this parser's seven fields, so it also records all seven missing:
returning a slug of one of its own row labels in place of a canonical field
would put a figure where the contract expects one on neither side of the
0.70 gate's `data_completeness` ratio.

Every value, including the derived margin, is a raw cleaned string rather than
a parsed number. The span is the evidence; every transformation between span
and value is an inference nothing here proves, and a float would drop the
approximation marker `~25%` carries. The cells do not fit one numeric type
honestly anyway: `51.3% (full-year) / 46.7% (Q1 2026)` is two measurements on
different bases rather than a range, `$14.9M+` is a floor, `25–30%` is a
range, `2,812,195 MT` is neither money nor percent, and
`~$19.8 ($55.6M ÷ 2.812M MT)` is a rate carrying its own derivation. Nothing
consumes these figures yet, so coercion belongs at the first real consumer,
where the requirement is known, not here. This diverges from E5b, which
returns `(low, high)` float pairs: E5b's figures are what the deck computes
with, over cells that are homogeneous money and percentage points, while
these are heterogeneous context, so E7a meets two representations in one
packet by design.

SUPERSEDED 2026-08-12. This docstring carried four findings against
`PAPER_FIELD_NAMES` in `src/source_span.py`, reported rather than fixed on
2026-08-11 because a correction there was a stop-and-report while parallel
parser windows were running. Those windows finished and the map was corrected
on 2026-08-12 against all eight fixtures. The findings held, and the count of
four did not: seven of the map's ten entries were wrong, incomplete or
unverifiable, because that count covered the assumption half only. Its
replacement, and the record of what changed, is the 2026-08-12 entry in
`CHANGELOG.md`. What the four findings said, and what became of each:

`ltm_ebitda` and `capacity_utilization` stored a short alias that happened to
substring-match the real row label (`LTM EBITDA` inside `Current LTM EBITDA`,
`Capacity Utilization` inside `Current Capacity Utilization`). Both now store
the real label first and keep the short alias as a second element, so nothing
that matched before stops matching. Behavior here is unchanged and no test
bites for it: the correction ends a substring coincidence that a paper with a
different prefix would have ended silently.

`ltm_ebitda_margin` named a row that does not exist. It still does, and that
is now recorded as measured rather than as a defect: no assumption table in
the corpus carries an EBITDA margin row, the margin lives inside the EBITDA
row's own value cell, and `parse_assumptions` lifts it from there below. There
was nothing to respell, so the entry was left in place with a comment.

`target_margin` stored `Target Margin at Scale`, which occurs nowhere in the
corpus and is not a substring of the real label, `Target EBITDA Margin at
Scale`. It fails outright NO LONGER: the entry now holds the real label, the
row resolves, and this paper records no missing field at all. The two tests
that encoded the old behavior were rewritten, not deleted, and
`test_target_margin_resolves_from_the_real_row_label` in
`tests/test_assumption_table_parser.py` is the test that bites for the
correction.
"""

import dataclasses
import re

from source_span import MissingFields, PAPER_FIELD_NAMES, SourcedFigure

ASSUMPTION_HEADER = "assumption"
ASSUMPTION_FIELDS = (
    "ltm_revenue",
    "ltm_ebitda",
    "ltm_ebitda_margin",
    "capacity_utilization",
    "fixed_cost_base",
    "backlog",
    "target_margin",
)
EBITDA_FIELD = "ltm_ebitda"
MARGIN_FIELD = "ltm_ebitda_margin"

_RULE_CELL = re.compile(r"^:?-+:?$")
_PAREN_PERCENT = re.compile(r"\((~?\d+(?:\.\d+)?%)\)")


@dataclasses.dataclass(frozen=True)
class _Row:
    cells: tuple
    line: str


@dataclasses.dataclass(frozen=True)
class _Table:
    header: tuple
    rows: tuple


def read_value(table, row):
    """The row's value cell: second-to-last, before `Source`, never a fixed index.

    Shape one and shape two share no column count, so the value sits at
    `len(header) - 2` rather than at a position either shape's own column count
    would suggest on its own. On the four-column shape this is what keeps the
    read off column two, the `Opportunity Claim` the paper is CORRECTING.
    """
    return _clean(_cell(row, len(table.header) - 2))


def parse_assumptions(paper, missing=None, *, document, opportunity):
    """Every canonical field, returned with a span or recorded missing. No third state.

    `document` is which document `paper` is and `opportunity` is which
    opportunity it is being read FOR, both keyword-only and both required, so a
    figure out of here can say where it came from and which slide it belongs on
    (`source_span.Document`, `source_span.Opportunity`). The second matters only
    once a document can be shared between opportunities, which item 15 made
    possible and item 14 made likely.
    """
    missing = missing if missing is not None else MissingFields()
    table = find_assumption_table(paper)
    figures, leftover = [], []
    if table is not None:
        for row in table.rows:
            label = _clean(row.cells[0])
            field = _match_field(label)
            if field is None:
                leftover.append(label)
                continue
            value = read_value(table, row)
            figures.append(
                SourcedFigure(field, value, row.line, paper, document,
                              opportunity)
            )
            if field == EBITDA_FIELD:
                margin = _PAREN_PERCENT.search(value)
                if margin:
                    figures.append(
                        SourcedFigure(MARGIN_FIELD, margin.group(1), row.line,
                                      paper, document, opportunity)
                    )
    found = {figure.field for figure in figures}
    for field in ASSUMPTION_FIELDS:
        if field in found:
            continue
        missing.record(field, _missing_reason(field, table, found, leftover))
    return figures


def _missing_reason(field, table, found, leftover):
    if table is None:
        return "this document carries no assumption table."
    if field == MARGIN_FIELD and EBITDA_FIELD in found:
        return "the EBITDA row's own cell states no embedded margin percentage."
    reason = "no row in the paper's assumption table matches this field's known aliases."
    if leftover:
        reason += " Unmatched row labels: " + "; ".join(leftover) + "."
    return reason


def find_assumption_table(paper):
    """The one table whose first header cell reads `Assumption`.

    Column count and a trailing `Source` column both vary and neither
    discriminates: the decoy `| Metric | Value | Source |` table matches shape
    one on both counts.
    """
    for table in _tables(paper):
        if _clean(table.header[0]).lower() == ASSUMPTION_HEADER:
            return table
    return None


def _match_field(label):
    text = label.lower()
    for field in ASSUMPTION_FIELDS:
        if any(alias.lower() in text for alias in PAPER_FIELD_NAMES.get(field, ())):
            return field
    return None


def _tables(paper):
    tables, block = [], []
    for line in (paper or "").split("\n"):
        if line.lstrip().startswith("|"):
            block.append(line)
            continue
        tables.extend(_table(block))
        block = []
    tables.extend(_table(block))
    return tables


def _table(block):
    rows = [_Row(_cells(line), line) for line in block if not _is_rule(line)]
    if len(rows) < 2:
        return []
    return [_Table(rows[0].cells, tuple(rows[1:]))]


def _cells(line):
    return tuple(cell.strip() for cell in line.strip().strip("|").split("|"))


def _is_rule(line):
    cells = _cells(line)
    return bool(cells) and all(_RULE_CELL.match(cell) for cell in cells)


def _cell(row, index):
    return row.cells[index] if index < len(row.cells) else ""


def _clean(text):
    return (text or "").replace("**", "").strip()
