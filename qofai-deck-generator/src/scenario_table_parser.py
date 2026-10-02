"""The scenario table parser (E5b): incremental EBITDA per case, and the margin.

A research paper's scenario table gives two to five cases and, for each, the
incremental EBITDA in dollars and sometimes the EBITDA margin impact in
percentage points. It comes in two orientations and both appear on both
reference companies, so orientation is not a per-company setting: cases as ROWS
under a `| Scenario |` header, or cases as COLUMNS with each row an impact
category and a first header cell that varies (`Impact Lever`, `Impact Category`,
`Metric` are the three in the committed excerpts).

Every rule below discriminates on COLUMNS and ROWS, never on a table's position
in the paper and never on a case name. That is deliberate: measured 2026-08-11
across the eight excerpts under `data-provider/fixtures/`, five real decoys sit
next to the real tables and not one of them is labelled. Taking the first table
that matches picks a component table over the consolidated one on the fixture
that carries both, and taking a table by name is impossible anyway, since one
paper writes three tables under identical headers with identical case names.

What makes a table a candidate. Its cases must number at least two, and it must
carry a field whose label names an impact (`impact`, `incremental`,
`contribution`, `gain`, `savings`) without naming revenue, cost, investment or
payback, and whose cell parses as dollars in EVERY case. That last clause is
what rejects a value-stream table, where the other columns hold a confidence and
a time to value rather than a second case.

What disqualifies a table outright. A net-of-investment ROI table reads exactly
like an impact table, down to a correct-looking EBITDA column, so a table naming
both a cost or investment AND a payback or ROI is read as a return table rather
than an impact table. Naming a cost alone is not enough: one real consolidated
table subtracts amortized implementation cost on its way to net EBITDA.

Which candidate wins. The one carrying a margin impact, then the one covering
more cases. On the fixture with a consolidated table and two component tables
that roll up into it, only the consolidated one carries the margin row, and that
is what picks it. Position is never a criterion and is only the final tie-break
between readings that are otherwise identical in completeness.

The margin. A margin impact is stated in percentage points, so the label must
name both EBITDA and margin AND every cell must carry the unit: `pp` or, since
E11 Stage 2b on 2026-08-19, `percentage point` / `percentage points` spelled out.
That rejects a gross-margin driver (no EBITDA), a resulting margin level (a
percent, not pp), and `% of Current EBITDA`, which is a share of the base rather
than a margin impact and would be wrong by construction. A paper with no margin column
yields the dollars and records the margin as missing. That is a normal outcome,
not an error, and borrowing a percentage that is not a margin impact to fill the
field is the failure this parser exists to prevent.

Provenance is E5a's. Each figure carries the table row it was read from as its
span. E5a's honest limit applies with full force here: `SourcedFigure` proves
the span EXISTS in the paper, never that the value FOLLOWS from it. Picking the
wrong table produces a figure that passes every type check in this repo, which
is why the discrimination above is the whole of the work.

SUPERSEDED 2026-08-12. This docstring reported one finding, that
`PAPER_FIELD_NAMES["scenario_cases"]` in `src/source_span.py` stored
Conservative, Base and Upside while the committed excerpts use Conservative,
Base Case, Optimistic, Moderate, Aggressive, Mid-range and Mid-Range, several
of them decorated with a figure or a parenthetical. The finding held and the
map was corrected on 2026-08-12: `scenario_cases` now holds the seven bare
spellings, and `incremental_ebitda` and `margin_impact_pp`, which this
docstring did not check, were wrong too and were corrected with it. Upside is
absent from the corrected tuple because it occurs only decorated, and
stripping a decoration was out of scope for that correction. See the
2026-08-12 entry in `CHANGELOG.md`.

Nothing about this module changed and nothing needed to. It reads no case
name to decide anything, spells its own column and row labels below, and
never consults that map, which is why the corrected entries are a record
rather than a dependency.
"""

import dataclasses
import re

from source_span import MissingFields, SourcedFigure

DOLLAR_FIELD = "incremental_ebitda"
MARGIN_FIELD = "margin_impact_pp"

CASES_AS_ROWS_HEADER = "scenario"
# "benefit" (opportunity 0000001e), "reduction" and "optimization" (0000000f,
# 00000024): synonyms E7c observed in the live corpus, 2026-08-15. Not a
# general vocabulary expansion.
IMPACT_WORDS = ("impact", "incremental", "contribution", "gain", "savings",
                "benefit", "reduction", "optimization")
NOT_IMPACT_WORDS = ("revenue", "cost", "investment", "payback")
RETURN_TABLE_WORDS = (("investment", "cost"), ("payback", "roi"))

# E11 Stage 2b, 2026-08-19. Both readers accepted the letter suffixes and
# nothing spelled out, so a paper writing its numbers in words lost them on BOTH
# passes. Widened to the spelled magnitudes and the spelled unit. The `$` is
# optional where a magnitude WORD is present, because the word is itself a
# currency-shaped claim, but a number carrying neither is still not money.
# Ordered longest-first: `([KMB])?` alone matched the `m` of `million` and left
# `illion` as residue, which is the mechanism that dropped `$3.571 million`.
_MAGNITUDE = r"thousand|million|billion|K|M|B"
_MONEY = re.compile(
    r"\$\s*([\d,]+(?:\.\d+)?)\s*(" + _MAGNITUDE + r")?\b"
    r"|\b([\d,]+(?:\.\d+)?)\s*(thousand|million|billion)\b",
    re.IGNORECASE,
)
# E11 Stage 2d, 2026-08-19. One reader, two patterns, because a percentage-point
# range is a single claim and had to stop being read as two unrelated tokens.
# `_PP` is byte-for-byte the pattern E11 Stage 2b left, so every single figure
# reads exactly as it did; `_PP_RANGE` is new and is tried first at each
# position. The unit is optional on the FIRST endpoint (`0.9-1.9pp` states it
# once for both) and the space before that optional unit belongs INSIDE the
# optional group, so that `1.2 to 6.0pp` still has the whitespace `\s+to\s+`
# needs after skipping it.
_PP_NUMBER = r"[+-]?\d+(?:\.\d+)?"
_PP_UNIT = r"(?:pp|percentage[\s-]+points?)"
_PP_SEPARATOR = r"(?:\s*[-\u2013\u2014]\s*|\s+to\s+)"
_PP_RANGE = re.compile(
    r"(" + _PP_NUMBER + r")(?:\s*" + _PP_UNIT + r")?" + _PP_SEPARATOR
    + r"(" + _PP_NUMBER + r")\s*" + _PP_UNIT + r"\b",
    re.IGNORECASE,
)
_PP = re.compile(
    r"(" + _PP_NUMBER + r")\s*" + _PP_UNIT + r"\b", re.IGNORECASE
)
# What may remain of a parenthesised cell once its figures are removed, before
# the parens count as accounting notation for a negative. Deliberately the same
# character set `_MONEY_CELL_RESIDUE` allows, minus the `/yr` suffix, which is a
# money unit. This gates the SIGN only; it is not a residue check on what
# `read_pp` accepts, and `read_pp` still has none.
_PP_PAREN_RESIDUE = re.compile(r"^[\s()~+\-\u2013\u2014]*$")
_WORDS = re.compile(r"[a-z]+")
_RULE_CELL = re.compile(r"^:?-+:?$")
_PAREN = re.compile(r"\([^)]*\)")
_MONEY_CELL_RESIDUE = re.compile(
    r"^[\s()~+\-–—]*(?:/(?:yr|year|mo|month))?[\s()~+\-–—]*$", re.IGNORECASE
)
_SCALE = {"": 1.0, "K": 1e3, "M": 1e6, "B": 1e9,
          "THOUSAND": 1e3, "MILLION": 1e6, "BILLION": 1e9}


@dataclasses.dataclass(frozen=True)
class ScenarioCase:
    """One case: its name, its dollars, and its margin impact where there is one.

    `incremental_ebitda` and `margin_impact_pp` hold a `(low, high)` pair, equal
    when the cell states a single figure, because the fixtures state some cells
    as ranges and dropping half a range is a judgment this parser does not make.
    """

    label: str
    incremental_ebitda: SourcedFigure
    margin_impact_pp: object


@dataclasses.dataclass(frozen=True)
class _Row:
    cells: tuple
    line: str


@dataclasses.dataclass(frozen=True)
class _Table:
    header: tuple
    rows: tuple


def parse_scenario_cases(paper, missing=None, *, document, opportunity):
    """The paper's scenario cases, or an empty list with the absence recorded.

    `document` is which document `paper` is and `opportunity` is which
    opportunity it is being read FOR, both keyword-only and both required, so a
    figure out of here can say where it came from and which slide it belongs on
    (`source_span.Document`, `source_span.Opportunity`). The second matters only
    once a document can be shared between opportunities, which item 15 made
    possible and item 14 made likely.
    """
    missing = missing if missing is not None else MissingFields()
    readings = [
        cases
        for table in tables(paper)
        if not is_return_table(table)
        for cases in [read_cases(table, paper, document=document,
                                 opportunity=opportunity)]
        if cases and len(cases) >= 2
    ]
    best = max(readings, key=_completeness, default=None)
    if best is None:
        for field in (DOLLAR_FIELD, MARGIN_FIELD):
            missing.record(field,
                           "no table in this document is a scenario impact "
                           "table.")
        return []
    if best[0].margin_impact_pp is None:
        missing.record(
            MARGIN_FIELD,
            "the paper's scenario table states no EBITDA margin impact in pp.",
        )
    return best


def _completeness(cases):
    return (cases[0].margin_impact_pp is not None, len(cases))


def read_cases(table, paper, *, document, opportunity):
    """The table's cases, in whichever orientation its header puts them."""
    if _clean(table.header[0]).lower() == CASES_AS_ROWS_HEADER:
        return _read_rows(table, paper, document, opportunity)
    return _read_columns(table, paper, document, opportunity)


def _read_rows(table, paper, document, opportunity):
    """Cases as rows: each row is a case and the impact fields are columns."""
    dollars = _pick_column(table, _is_dollar_label, _money)
    if dollars is None:
        return None
    margins = _pick_column(table, is_margin_label, read_pp)
    return [
        _case(paper, document, opportunity, row.cells[0],
              _cell(row, dollars), row.line,
              None if margins is None else _cell(row, margins), row.line)
        for row in table.rows
    ]


def _read_columns(table, paper, document, opportunity):
    """Cases as columns: each row is an impact category and the cases are headers."""
    picked = _pick_row(table, _is_dollar_label, _money)
    if picked is None:
        return None
    dollars, cols = picked
    margins_picked = _pick_row(table, is_margin_label, read_pp)
    margins = margins_picked[0] if margins_picked else None
    return [
        _case(paper, document, opportunity, table.header[i],
              _cell(dollars, i), dollars.line,
              None if margins is None else _cell(margins, i),
              None if margins is None else margins.line)
        for i in cols
    ]


def _pick_column(table, is_label, read):
    """The column whose header names the field and whose every case cell parses.

    A header naming EBITDA wins over one that does not, which is what separates
    net EBITDA impact from a gross profit subtotal sitting in the same table.
    """
    matches = [
        i
        for i, label in enumerate(table.header)
        if i and is_label(label) and _all_parse(read, [_cell(r, i) for r in table.rows])
    ]
    return max(matches, key=lambda i: _names_ebitda(table.header[i]), default=None)


def _pick_row(table, is_label, read):
    """The row whose label names the field, and which of its own cells parse.

    A row's own case columns are the ones its cells parse as the field, not
    every column after the label: a trailing descriptive column (Confidence,
    Basis, Timing) never qualifies as a case just for sitting beside real ones.
    """
    matches = [
        (row, cols)
        for row in table.rows
        if is_label(row.cells[0])
        for cols in [tuple(i for i in range(1, len(table.header))
                            if read(_cell(row, i)) is not None)]
        if len(cols) >= 2
    ]
    return max(
        matches,
        key=lambda m: (_names_ebitda(m[0].cells[0]), _is_total(m[0].cells[0]), len(m[1])),
        default=None,
    )


def _is_total(label):
    return "total" in _clean(label).lower()


def _all_parse(read, cells):
    return bool(cells) and all(read(cell) is not None for cell in cells)


def _is_dollar_label(label):
    text = _clean(label).lower()
    subject = _PAREN.sub("", text)
    if any(word in subject for word in NOT_IMPACT_WORDS):
        return False
    return any(word in text for word in IMPACT_WORDS)


def is_margin_label(label):
    text = _clean(label).lower()
    return "ebitda" in text and "margin" in text


def _names_ebitda(label):
    return "ebitda" in _clean(label).lower()


def is_return_table(table):
    """A net-of-investment ROI table, which is a return rather than an impact."""
    labels = [table.header, [row.cells[0] for row in table.rows]]
    words = set(_WORDS.findall(" ".join(_clean(c).lower() for g in labels for c in g)))
    return all(words & set(group) for group in RETURN_TABLE_WORDS)


def _case(paper, document, opportunity, label, dollar_cell, dollar_line,
          margin_cell, margin_line):
    return ScenarioCase(
        label=_case_label(label),
        incremental_ebitda=SourcedFigure(
            DOLLAR_FIELD, _money(dollar_cell), dollar_line, paper, document,
            opportunity
        ),
        margin_impact_pp=(
            None
            if margin_cell is None
            else SourcedFigure(MARGIN_FIELD, read_pp(margin_cell), margin_line,
                               paper, document, opportunity)
        ),
    )


def _case_label(cell):
    """The case name, without the figure or parenthetical some cells carry."""
    return re.split(r"[:(]", _clean(cell))[0].strip()


def _money(cell):
    """`(low, high)` US dollars from a cell, or None if the cell is not one.

    Notation, widened E11 Stage 2b 2026-08-19. `$3.571M`, `$3.571 million` and
    `3.571 million` are the same figure and read alike, case-folded. Measured on
    a live paper 2026-08-18: the model quoted `$3.571 million` correctly, its
    span verified, and the figure was still dropped, which is the information
    loss the second pass exists to prevent. The widening is a vocabulary change
    only; every rule below it is the one E7d shipped.

    The dollar figure(s) must be essentially the whole cell, not merely
    present in it: `4-6% of CY2025 administrative payroll ($1.93M) [27]`
    states a dollar figure inside supporting prose, in a column that is not a
    case, and reading it as one is a false positive the golden rule forbids,
    a wrong figure a completeness gate cannot catch because the field is no
    longer missing. What is left after the money itself is removed must be
    nothing but a range separator, a sign paren, a tilde, an explicit `+`
    (`+$0.69M`, `$1.0M+`), or a `/yr`, `/year`, `/mo`, `/month` unit suffix
    (`$149K/yr`), all confirmed live on real, previously-correct corpus cells.
    This residue check, not the magnitude vocabulary, is what bounds false
    positives, so Stage 2b deliberately left it exactly as it was: a currency
    WORD is not acceptable residue either, and `USD 3.5 million` is refused.

    Every token in the cell must state its magnitude the same way. That rule is
    Stage 2b's own, and it exists because dropping the `$` requirement made
    `$1.5-2.0 million` readable as `(1.5, 2000000.0)`, a range whose stated
    magnitude belongs to one endpoint and could belong to both. The cell does
    not say which, so it yields nothing, the way it did before Stage 2b when
    `-2.0 million` was left over as residue.
    """
    text = _clean(cell)
    # Two branches, four groups: `$`-led with an optional magnitude, and a bare
    # number whose magnitude word is required. Exactly one branch fills per match.
    found = [
        (led or bare, (suffix or spelled))
        for led, suffix, bare, spelled in _MONEY.findall(text)
    ]
    if not 1 <= len(found) <= 2:
        return None
    if not _MONEY_CELL_RESIDUE.match(_MONEY.sub("", text)):
        return None
    if len({bool(suffix) for _, suffix in found}) != 1:
        return None
    sign = -1.0 if text.startswith("(") and text.endswith(")") else 1.0
    values = [
        sign * float(n.replace(",", "")) * _SCALE[suffix.upper()]
        for n, suffix in found
    ]
    return (min(values), max(values))


def read_pp(cell):
    """`(low, high)` percentage points from a cell, or None if it states none.

    Notation, widened E11 Stage 2b 2026-08-19: `1.9pp`, `1.9 percentage points`,
    `1.9 percentage point` and `1.9 percentage-point` all read alike, so a paper
    spelling its margin impact out no longer loses it. The unit stays a closed
    set of two spellings on purpose. `1.9%` is a margin LEVEL and `1.9 ppt` is
    neither of this repo's tokens; both are still refused, because the pp/percent
    separation is the whole reason this reader is not `read_percent`.

    Ranges, fixed E11 Stage 2d 2026-08-19. Two defects met on one cell. The
    number group carries an optional sign, so the separator in `0.9-1.9pp` was
    read as a minus; and the unit had to follow its number immediately, so an
    endpoint leaving the unit to the other endpoint was not matched at all.
    Together the cell came back `(-1.9, -1.9)`: low endpoint gone, high one
    negated. That is the only failure mode in this repo that yields a WRONG
    FIGURE rather than an absence, and no completeness gate can catch it,
    because the field is not missing.

    The fix reads a range as one claim. A hyphen, an en dash, an em dash or the
    word `to` sitting BETWEEN two numbers is a separator; a `-` leading the first
    token is still a minus, and `-2.4 pp` (a real cell) still reads -2.4. Where
    the range pattern refuses and a `-` is left attached to a number that follows
    another number, it is demoted to a separator too, because rule one is that a
    `-` between two numbers is not a sign.

    A parenthesised cell is negative, which is what `_money` has read since E7d
    and what this reader did not. The parens must wrap the whole cell and leave
    nothing but signs, tildes and separators behind, so `(a 1.9 pp gain)` is
    prose in parens and takes no sign. A STATED sign wins over the parens:
    `_money`'s number group carries no sign and cannot double-negate, and this
    one does.

    `(low, high)` is min and max, the same convention `_money` uses and the same
    one `ScenarioCase` documents. A range the paper states backwards yields the
    two endpoints it states, neither invented nor dropped; no rule beyond min and
    max decides which is which.
    """
    text = _clean(cell)
    matches = _pp_matches(text)
    endpoints = [
        _pp_endpoint(text, match, group)
        for match in matches
        for group in range(1, match.re.groups + 1)
    ]
    if not 1 <= len(endpoints) <= 2:
        return None
    values = [value for value, _ in endpoints]
    if not any(stated for _, stated in endpoints) and _is_paren_negative(text, matches):
        values = [-value for value in values]
    return (min(values), max(values))


def _pp_matches(text):
    """Every pp figure in the cell, left to right, a range read as one match.

    At each position the range pattern is tried first and wins a tie, so
    `0.9-1.9pp` is one two-endpoint claim rather than the single `-1.9pp` the
    plain pattern finds inside it.
    """
    matches, index = [], 0
    while True:
        wide = _PP_RANGE.search(text, index)
        one = _PP.search(text, index)
        if wide is not None and (one is None or wide.start() <= one.start()):
            match = wide
        elif one is not None:
            match = one
        else:
            return matches
        matches.append(match)
        index = match.end()


def _pp_endpoint(text, match, group):
    """One endpoint as `(value, sign_was_stated)`.

    The range pattern consumes its own separator, so a `-` still attached to an
    endpoint here is a sign unless what precedes it ends in a digit, which is the
    shape left behind when the range pattern refuses a cell that nonetheless puts
    a `-` between two numbers.
    """
    token = match.group(group)
    if token.startswith("-") and _follows_a_digit(text, match.start(group)):
        token = token[1:]
    return float(token), token[0] in "+-"


def _follows_a_digit(text, index):
    before = text[:index].rstrip()
    return bool(before) and before[-1].isdigit()


def _is_paren_negative(text, matches):
    """True when the cell is a figure wrapped in parens and nothing else."""
    if not (text.startswith("(") and text.endswith(")")):
        return False
    residue = text
    for match in reversed(matches):
        residue = residue[: match.start()] + residue[match.end() :]
    return bool(_PP_PAREN_RESIDUE.match(residue))


def tables(paper):
    """Every markdown table in the paper, rules dropped and rows kept whole."""
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
