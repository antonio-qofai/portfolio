"""The baseline parser (E5d): the subject company's own stated financials.

A baseline statement is a figure describing the SUBJECT company's own current
or historical financial position, revenue, EBITDA, or EBITDA margin, for a
named period, carrying a citation marker or sitting in a cited table. A
projection, a target, a budget, a scenario outcome, and any figure about
another company are not baseline statements.

That definition, not a count, is what this module implements. Two earlier
counts of the baseline headings on the same two fixtures disagreed (eight and
six on 2026-08-10, three and four on 2026-08-11) because "a baseline
statement" was undefined, so the number here falls out of the definition
rather than being inherited from any document.

Two source shapes, and no line scanning. A reference line in the paper's own
citation list, and a table whose axes cross a metric with a period. Prose is
deliberately not read, which is what rejects the benchmark decoy: on
`validated-assumption-table`, `### Industry Overview` states a cited revenue,
EBITDA and margin for a named benchmark company, and nothing structural
separates that sentence from a sentence about the subject. `<Chart>` tags are
stripped before anything is scanned, which is what rejects the second decoy:
on `scenario-columns`, `#### Return on Investment` is a real prose heading,
but the figure under it ("from $6.44M base") exists only inside a `<Chart>`
`description` attribute. Six of the eight fixtures carry a qualifying
reference line and two rely on a table, so both shapes are load-bearing.
The cost of not reading prose is a paper whose baseline is stated in prose
alone: that paper records missing fields, drags `data_completeness`, and lets
the 0.70 gate decide, which is the golden rule working rather than failing.

Subject company, on a reference line, is a filter on document type: the line
must cite the company's own records (`COMPANY_SOURCES`) and must not name a
projection (`NOT_BASELINE`). This is a filter, not a proof of identity, and a
benchmark company's own financial statements cited in a reference line would
pass it.

Cited table is read as the paper's own financial-summary table rather than as
a table carrying a `[n]` marker. Two fixtures state their EBITDA margin only
in an uncited appendix table (`scenario-columns` at 24.4%, `scenario-rows-
canonical` at 17.8%), so requiring a marker inside the table would record the
margin missing on both. Recorded here as an interpretation of the definition,
because it is the one place this module reads the definition loosely.

One statement wins per paper, not one figure per field. A field taken from a
different period than its neighbours produces a coherent-looking, incoherent
packet: FY2025 revenue beside a TTM margin. So candidates are ranked whole,
by fields covered, then basis (an annualized figure loses to an actual one),
then an annual period over a quarter or a year-to-date, then the later year,
then a reference line over a table, which is where "prefer the most precise
statement" lands. `shape` records which one was taken. Ties resolve to
document order.

Report the basis, never normalize it. `scenario-rows-canonical` states six
bases for one company in one table, and an annualized figure presented as an
actual is a real misstatement on a client-facing deck. A period label with no
qualifier on it is reported as `actual`, since projections are excluded before
ranking; the label itself is in `period` for the reviewer to read.

NO CROSS-PAPER COMPARISON EXISTS IN THIS MODULE. The papers disagree with
each other about the same company on the committed fixtures, FY2025 revenue
appearing as $59.2M, $53.7M, $57.7M and $55.6M. A packet is per-opportunity,
so a figure is correct relative to its own paper. Nothing here reconciles,
averages, picks a winner between papers, or penalizes confidence for
disagreement, and `parse_baseline` reads one paper and cannot see a second.

Values are raw cleaned strings, following E5c: the span is the evidence, and
`float` would drop the approximation marker `~$27.9M` and `~25%` carry.
Provenance is E5a's, and its limit applies with full force here: the span
proves the text EXISTS in the paper, never that the value FOLLOWS from it.
Picking the wrong period produces a figure that passes every type check in
this repo.

Two findings, reported rather than acted on. `PAPER_FIELD_NAMES` in
`src/source_span.py` carries no baseline metric label; its `ltm_revenue`,
`ltm_ebitda` and `ltm_ebitda_margin` are E5c's assumption-table row labels on
one fixed LTM basis, and a baseline's period varies per paper. `METRIC_LABELS`
below is therefore new vocabulary rather than a second copy of an existing
entry or a workaround for one of that map's four known defects; whether it
belongs in that file is for the queued correction to decide. And
`| Assumption |` tables are skipped whole, since they are E5c's and their rows
would otherwise resolve as baselines.

UPDATED 2026-08-12, on the first finding only. The queued correction ran and
decided the question it was left: `METRIC_LABELS` stays here and was not
merged into `PAPER_FIELD_NAMES`, on the reasoning this docstring gave, that a
baseline's period varies per paper while those entries are E5c's row labels on
one fixed LTM basis. Whether they eventually belong together is a design
question and is E9's, not that correction's. Two details of the sentence
above are now stale and worth correcting for whoever reads it next. The count
of four defects was low: seven of that map's ten entries were wrong,
incomplete or unverifiable, four being the assumption half alone. And
`ltm_ebitda_margin` is not a label needing correction at all, since no
assumption table in the corpus carries an EBITDA margin row; the margin sits
inside the EBITDA row's own value cell. The second finding, that
`| Assumption |` tables are skipped whole, is unaffected. See the 2026-08-12
entry in `CHANGELOG.md`.

`tables` is imported from E5b rather than copied. Two copies of that splitter
already exist in `src/` and a third is worse; its rows keep the source line,
which is exactly the span a figure needs.
"""

import dataclasses
import re

from scenario_table_parser import tables
from source_span import MissingFields, SourcedFigure

REVENUE = "baseline_revenue"
EBITDA = "baseline_ebitda"
MARGIN = "baseline_ebitda_margin"
FIELDS = (REVENUE, EBITDA, MARGIN)

METRIC_LABELS = {
    REVENUE: ("revenue", "total revenue"),
    EBITDA: ("ebitda", "adjusted ebitda", "adj. ebitda"),
    MARGIN: ("ebitda margin", "adjusted ebitda margin"),
}
METRIC_WORDS = {REVENUE: "revenue", EBITDA: "ebitda"}
COMPANY_SOURCES = ("financial statement", "financial record", "p&l", "company data",
                   "company overview", "knowledge base")
NOT_BASELINE = ("budget", "projected", "projection", "forecast", "target", "plan",
                "variance", "change", "growth", "gain", "incremental", "impact")
BASIS_WORDS = ("annualized", "ytd", "ltm", "ttm")
PARTIAL_PERIOD_WORDS = ("ytd", "month")
ASSUMPTION_HEADER = "assumption"

_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
_MONEY = r"~?[-−]?\$\s?[\d,]+(?:\.\d+)?\s*(?:[KMB]|million|billion|thousand)?"
_CHART = re.compile(r"<Chart\b.*?/>", re.DOTALL)
_REFERENCE = re.compile(r"^\[\d+\]\s")
_PERIOD_IN_TEXT = re.compile(
    rf"(?:FY|CY|Q[1-4])[\s-]?\d{{4}}(?:\s*\([^)]*\))?"
    rf"|(?:LTM|TTM)(?:\s+{_MONTH})?(?:\s+\d{{4}})?"
    rf"|\b(?:19|20)\d{{2}}\b"
)
_PERIOD_TOKEN = re.compile(
    r"(?:FY|CY|Q[1-4])[\s-]*\d{4}|Q[1-4]\b|\b(?:LTM|TTM|YTD|FY|CY)\b|\b\d{4}\b",
    re.IGNORECASE,
)
_PARENTHETICAL = re.compile(r"\([^)]*\)")
# A month name alone is not enough: "margin" starts with "Mar". Only a month
# token directly followed by a number (a day or a year, "Jan 2025") counts.
_PERIOD_PAREN = re.compile(
    rf"\([^)]*(?:\b{_MONTH}\.?\s+\d|\b(?:19|20)\d{{2}}\b)[^)]*\)", re.IGNORECASE
)
_MONEY_CELL = re.compile(rf"^\(?{_MONEY}\)?$", re.IGNORECASE)
_PERCENT_CELL = re.compile(r"^~?[+-]?\d+(?:\.\d+)?\s*%$")
_PERCENT = re.compile(r"~?\d+(?:\.\d+)?\s*%")
_YEAR = re.compile(r"(?:19|20)\d{2}")


@dataclasses.dataclass(frozen=True)
class Baseline:
    """One baseline statement: its period, its basis, and up to three figures.

    `period` is the paper's own label for the period, verbatim. `basis` is the
    qualifier that label carries. `shape` is which source shape it was taken
    from. Each figure is a `SourcedFigure` or None, and a None is recorded in
    `missing_fields` with a reason by `parse_baseline`.
    """

    period: str
    basis: str
    shape: str
    revenue: object
    ebitda: object
    ebitda_margin: object


@dataclasses.dataclass
class _Candidate:
    period: str
    shape: str
    figures: dict


def strip_charts(paper):
    """The paper with every `<Chart>` tag removed, prose and tables intact.

    A chart's `description` attribute states figures in prose, and one of them
    sits under a real prose heading on `scenario-columns`, so a scanner that
    reads chart metadata reads a figure the paper never wrote in its text.
    """
    return _CHART.sub("", paper or "")


def parse_baseline(paper, missing=None, *, document, opportunity):
    """The paper's baseline statement, or None with every field recorded missing.

    `document` is which document `paper` is and `opportunity` is which
    opportunity it is being read FOR, both keyword-only and both required, so a
    figure out of here can say where it came from and which slide it belongs on
    (`source_span.Document`, `source_span.Opportunity`). The second matters only
    once a document can be shared between opportunities, which item 15 made
    possible and item 14 made likely.
    """
    missing = missing if missing is not None else MissingFields()
    best = max(_candidates(strip_charts(paper)), key=_rank, default=None)
    if best is None:
        for field in FIELDS:
            missing.record(field,
                           "no baseline statement in this document is one this "
                           "parser can read.")
        return None
    for field in FIELDS:
        if field not in best.figures:
            missing.record(
                field,
                f"the baseline statement for {best.period} states no {field}.",
            )
    figures = {
        field: SourcedFigure(field, value, span, paper, document, opportunity)
        for field, (value, span) in best.figures.items()
    }
    return Baseline(
        period=best.period,
        basis=basis_of(best.period),
        shape=best.shape,
        revenue=figures.get(REVENUE),
        ebitda=figures.get(EBITDA),
        ebitda_margin=figures.get(MARGIN),
    )


def _rank(candidate):
    """Whole statements are ranked, never single figures. See the module docstring."""
    label = candidate.period.lower()
    return (
        len(candidate.figures),
        "annualized" not in label,
        not any(word in label for word in PARTIAL_PERIOD_WORDS) and "q" != label[:1],
        max((int(year) for year in _YEAR.findall(label)), default=0),
        candidate.shape == "reference line",
    )


def basis_of(period):
    """The qualifier the period label carries, or `actual` where it carries none."""
    stated = [word for word in BASIS_WORDS if word in period.lower()]
    return stated[0] if stated else "actual"


def _candidates(paper):
    grouped = {}
    for key, period, shape, field, value, span in _readings(paper):
        candidate = grouped.setdefault((key, period), _Candidate(period, shape, {}))
        candidate.figures.setdefault(field, (value, span))
    return list(grouped.values())


def _readings(paper):
    """Every (metric, period, value, span) the two source shapes state."""
    for line in paper.split("\n"):
        if _REFERENCE.match(line.strip()) and is_company_source(line):
            for period, text in _statements(line.strip()):
                if any(word in text.lower() for word in NOT_BASELINE):
                    continue
                for field, value in _read_statement(text).items():
                    yield line, period, "reference line", field, value, line
    for index, table in enumerate(tables(paper)):
        if _clean(table.header[0]).lower() == ASSUMPTION_HEADER:
            continue
        fallback_period = None
        for row in table.rows:
            for column in range(1, len(table.header)):
                reading = _read_cell(table, row, column, fallback_period)
                if reading is not None:
                    field, period, value = reading
                    yield index, period, "table row", field, value, row.line
                    if is_period(row.cells[0]) or is_period(table.header[column]):
                        fallback_period = period


def is_company_source(line):
    """A citation of the company's own records, a filter on document type.

    Whether a given figure IN the line is itself a projection is a separate,
    per-statement question `_readings` answers with the same `NOT_BASELINE`
    list, since a heading word like "growth analysis" elsewhere on the line
    must not exclude a plain actual the line also states.
    """
    text = line.lower()
    return any(source in text for source in COMPANY_SOURCES)


def _statements(line):
    """A reference line split at each period it names, figures with their period.

    `LTM Revenue ~$27.9M, LTM EBITDA ~$6.78M (~25%), LTM Sep 2025` names one
    period, not three: a segment labelled `LTM` takes the longest label on the
    same line that extends it, or the winning figure carries a basis and no
    period at all.

    A period range inside a parenthetical, `LTM Revenue (Jan 2025-Dec 2025) of
    $18.5M`, states one period next to the metric it qualifies rather than a
    second period the sentence names, so it is removed before marks are found:
    left in, its own bare years would mark a second, spurious period and split
    the metric word from its own value across two segments.
    """
    line = _PERIOD_PAREN.sub(" ", line)
    marks = list(_PERIOD_IN_TEXT.finditer(line))
    labels = [mark.group(0).strip() for mark in marks]
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(line)
        period = mark.group(0).strip()
        extended = max((l for l in labels if l.startswith(period)), key=len)
        yield extended, line[mark.end() : end]


def _read_statement(text):
    """Revenue, EBITDA, and the margin stated beside the EBITDA figure."""
    figures = {}
    for field, word in METRIC_WORDS.items():
        value = _figure_near(text, word)
        if value is not None:
            figures[field] = value
    if EBITDA in figures:
        margin = _margin_after(text, figures[EBITDA])
        if margin is not None:
            figures[MARGIN] = margin
    return figures


def _figure_near(text, word, window=25):
    """The money token the metric word owns: just after it, else just before it.

    Just after has to stop at the first comma, or `showing $22.7M revenue,
    $10.6M EBITDA` reads the EBITDA figure as the revenue.
    """
    match = re.search(word, text, re.IGNORECASE)
    if match is None:
        return None
    after = re.match(
        rf"\s*:?\s*(?:of|in|at|reached|totaled|was)?\s*(?:approximately\s+)?({_MONEY})",
        text[match.end() : match.end() + window],
        re.IGNORECASE,
    )
    if after:
        return after.group(1).strip()
    before = re.search(
        rf"({_MONEY})\s*\w{{0,12}}\s*$", text[max(0, match.start() - window) : match.start()]
    )
    return before.group(1).strip() if before else None


def _margin_after(text, ebitda, window=30):
    """The first percentage stated within a short reach after the EBITDA figure.

    A gross margin stated in that reach is not an EBITDA margin, so the reach
    is abandoned rather than read when `gross` sits between the two.
    """
    tail = text[text.index(ebitda) + len(ebitda) :][:window]
    match = _PERCENT.search(tail)
    if match is None or "gross" in tail[: match.start()].lower():
        return None
    return match.group(0).strip()


def _read_cell(table, row, column, fallback_period=None):
    """One cell as (field, period, value), where its two labels cross a metric
    with a period and the cell itself states the right kind of figure."""
    resolved = _resolve(row.cells[0], table.header[column], fallback_period)
    if resolved is None:
        return None
    field, period = resolved
    value = _clean(_cell(row, column))
    if field is MARGIN and not _PERCENT_CELL.match(value):
        return None
    if field is not MARGIN and not _MONEY_CELL.match(value):
        return None
    return field, period, value


def _resolve(row_label, column_label, fallback_period=None):
    """Which of a cell's two labels is the metric and which names the period.

    Both orientations occur, and on the `| Metric | Value | Source |` shape the
    row label carries both while the column label carries neither. Neither
    label always carries a period, on a `| Metric | Value | Source |` shape:
    a bare metric row inherits the period the row above it in the same table
    stated explicitly, `fallback_period`, since it sits in the same cited
    table under the same implicit period.
    """
    row_metric, column_metric = metric_of(row_label), metric_of(column_label)
    row_period, column_period = is_period(row_label), is_period(column_label)
    if row_metric and column_period:
        return row_metric, _clean(column_label)
    if column_metric and row_period:
        return column_metric, _clean(row_label)
    if row_metric and row_period and not (column_metric or column_period):
        return row_metric, _clean(row_label)
    if row_metric and not (column_metric or column_period) and fallback_period:
        return row_metric, fallback_period
    return None


def metric_of(label):
    """The field a label names, matched whole rather than by substring.

    `EBITDA Margin Impact (pp)`, `Annual EBITDA Impact`, `Average Revenue per
    Client` and `2026 Budgeted Revenue` all contain a metric name and none of
    them is a baseline metric, so the label is stripped of its period tokens
    and its parentheticals and then has to equal one of the names outright.
    """
    text = _PARENTHETICAL.sub("", _clean(label).lower())
    text = _PERIOD_TOKEN.sub("", text).replace("%", " ")
    for word in ("current", "actual"):
        text = text.replace(word, " ")
    text = " ".join(text.split())
    for field, names in METRIC_LABELS.items():
        if text in names:
            return field
    return None


def is_period(label):
    """A label naming a period: a year, or a trailing-twelve-months window."""
    text = _clean(label).lower()
    if "%" in text or any(word in text for word in NOT_BASELINE):
        return False
    return bool(_YEAR.search(text)) or "ltm" in text or "ttm" in text


def _cell(row, index):
    return row.cells[index] if index < len(row.cells) else ""


def _clean(text):
    return (text or "").replace("**", "").strip()
