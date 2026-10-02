"""The commercial slide's blocks, drawn from data rather than by the render model.

`build-plan-commercial-slide.md`. Slide 5 is an adaptive deal sheet: INVESTMENT
(the PRD's cost total), RETURN (the cases the source names), TERMS (the deal,
as named rows), an optional value chart, and a footnote. Every block is
optional except TERMS, which with nothing entered shows one box holding its
`[MISSING: terms_rows]` marker.

WHY CODE WRITES THE BLOCKS AFTER THE MODEL HAS. The prompt carries every value,
because the saved prompt is also the Claude Design deliverable, and the render
model draws the slide from it like any other. `normalise` then rewrites each
block's contents from the same placeholder map with the renderers below. Three
reasons, all from this repo's history: a figure a model re-types is the one
place a digit can change with no parser being wrong; the studio's editor has to
read the slide back exactly, which model-drawn markup does not guarantee (the
old terms reader documented its first read as "best effort"); and the value
chart's geometry already moved out of the model for the same reason
(`commercial_terms.bar_widths`).

EACH ROW CARRIES ITS DATA IN `data-` ATTRIBUTES, verbatim, so a reader never has
to parse display text back into values.

REPORT, NEVER RAISE. A render missing a block container is left exactly as the
model drew it and the miss is reported. Refusing would throw away a ten-minute
render over a layout detail the reviewer can see on the slide.

THE SLIDE IS FOUND BY ITS BLOCKS, NEVER BY ITS NUMBER. It is template block 5
but renders as slide 8 on a two-opportunity deck.
"""

import html as _html
import re

INVEST_OPEN = re.compile(r'<div class="invest"[^>]*>')
RETURNS_OPEN = re.compile(r'<div class="returns"[^>]*>')
TERMS_OPEN = re.compile(r'<div class="deal-terms"[^>]*>')
VM_ROWS_OPEN = re.compile(r'<div class="vm-rows"[^>]*>')

TERMS_MARKER = "[MISSING: terms_rows]"

# The RETURN table's columns, in order: (record field, header). Fixed names
# rather than each PRD's own header, because one table on a two-PRD deck has
# one header row and two current PRDs name their dollar column differently
# ("Annual Value", "Incremental EBITDA"); each PRD's §1.1 calls that figure
# its EBITDA uplift.
RETURN_COLUMNS = (
    ("annual_ebitda", "Annual EBITDA"),
    ("margin", "Margin"),
    ("payback", "Payback"),
)

# At most three terms side by side; more wrap onto the next row.
_TERMS_PER_ROW = 3


def _esc(text):
    return _html.escape(str(text or ""), quote=False)


def _attr(text):
    return _html.escape(str(text or ""), quote=True)


def render_investment(rows):
    """The `.invest` block's contents, grouped by opportunity where labelled."""
    out = ['<div class="blk-t">Investment</div>']
    last = None
    for row in rows:
        opportunity = row.get("opportunity") or ""
        if opportunity and opportunity != last:
            out.append(f'<div class="blk-opp">{_esc(opportunity)}</div>')
        last = opportunity
        out.append(
            f'<div class="inv-row" data-label="{_attr(row.get("label"))}" '
            f'data-value="{_attr(row.get("value"))}" '
            f'data-opportunity="{_attr(opportunity)}">'
            f'<span class="inv-l">{_esc(row.get("label"))}</span>'
            f'<span class="inv-v">{_esc(row.get("value"))}</span></div>')
    return "".join(out)


def return_columns(rows):
    """The columns any row carries, in `RETURN_COLUMNS` order."""
    return [(field, header) for field, header in RETURN_COLUMNS
            if any(row.get(field) for row in rows)]


def render_returns(rows):
    """The `.returns` block's contents: a table of the source's cases.

    A column no case carries is not drawn. On a deck carrying several
    opportunities, each opportunity's cases sit under a row naming it, and no
    total row is ever drawn.
    """
    columns = return_columns(rows)
    head = "".join(f"<th>{_esc(header)}</th>" for _field, header in columns)
    body, last = [], None
    for row in rows:
        opportunity = row.get("opportunity") or ""
        if opportunity and opportunity != last:
            body.append(f'<tr class="ret-opp"><td colspan="{len(columns) + 1}">'
                        f"{_esc(opportunity)}</td></tr>")
        last = opportunity
        data = " ".join(
            f'data-{field.replace("_", "-")}="{_attr(row.get(field))}"'
            for field in ("scenario",) + tuple(f for f, _h in RETURN_COLUMNS))
        cells = "".join(f"<td>{_esc(row.get(field))}</td>" for field, _h in columns)
        body.append(f'<tr {data} data-opportunity="{_attr(opportunity)}">'
                    f'<td class="ret-case">{_esc(row.get("scenario"))}</td>'
                    f"{cells}</tr>")
    return ('<div class="blk-t">Return</div><table class="ret"><thead><tr>'
            f'<th>Case</th>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>')


def render_terms(rows):
    """The whole `.deal-terms` element, its column count on the open tag.

    The element and not only its contents, because the count lives in an inline
    style. With no rows it is ONE box labelled "Terms" holding the role's
    marker, so filling that marker from the studio's missing-values card turns
    it into a real row and the slide reads right either way.
    """
    if not rows:
        return ('<div class="deal-terms" style="grid-template-columns:repeat(1,1fr)">'
                '<div class="term term--empty"><div class="term-l">Terms</div>'
                f'<div class="term-v"><span class="flag">{TERMS_MARKER}</span></div>'
                "</div></div>")
    count = min(len(rows), _TERMS_PER_ROW)
    boxes = "".join(
        f'<div class="term" data-label="{_attr(row.get("label"))}" '
        f'data-value="{_attr(row.get("value"))}">'
        f'<div class="term-l">{_esc(row.get("label"))}</div>'
        f'<div class="term-v">{_esc(row.get("value"))}</div></div>'
        for row in rows)
    return (f'<div class="deal-terms" style="grid-template-columns:'
            f'repeat({count},1fr)">{boxes}</div>')


_DIV_TAG = re.compile(r"<(/?)div\b[^>]*>")


def _element_end(html, start):
    """The offset just past the `</div>` closing the div opening at ``start``."""
    depth = 0
    for found in _DIV_TAG.finditer(html, start):
        depth += -1 if found.group(1) else 1
        if depth == 0:
            return found.end()
    return -1


def _replace_inner(html, pattern, inner):
    found = pattern.search(html)
    if not found:
        return html, False
    end = _element_end(html, found.start())
    if end < 0:
        return html, False
    return html[:found.end()] + inner + html[end - len("</div>"):], True


def _replace_outer(html, pattern, outer):
    found = pattern.search(html)
    if not found:
        return html, False
    end = _element_end(html, found.start())
    if end < 0:
        return html, False
    return html[:found.start()] + outer + html[end:], True


def _value_map_rows(cases):
    """The chart's rows, sized from the figures, or None if any figure won't parse."""
    from commercial_terms import bar_widths, parse_money
    from html_edit_layer import render_value_map_rows

    parsed = []
    for case in cases:
        numbers = {name: parse_money(case.get(name))
                   for name in ("qofai_comp", "client_retained_ebitda",
                                "enterprise_value")}
        if any(value is None for value in numbers.values()):
            return None
        parsed.append({**case, **{f"_{name}_value": value
                                  for name, value in numbers.items()}})
    return render_value_map_rows(parsed, bar_widths(parsed),
                                 base_scenario="Base Case")


def normalise(html, placeholder_map):
    """Rewrite slide 5's blocks from ``placeholder_map``; ``(html, report)``.

    ``report`` is ``{"written": [...], "missing": [...]}``: the blocks written,
    and the blocks the map has data for that the render drew no container for.
    A block the map has no data for is left alone, whatever the render drew.
    A deck with no commercial slide at all (a status deck) comes back unchanged
    with nothing reported.
    """
    placeholder_map = placeholder_map or {}
    report = {"written": [], "missing": []}
    if not TERMS_OPEN.search(html or ""):
        if "terms_rows" in placeholder_map:
            report["missing"].append("terms")
        return html, report

    # A role left empty can arrive as its `[MISSING: ...]` marker string rather
    # than a list, so anything that is not a list is read as no rows.
    def rows_of(role):
        value = placeholder_map.get(role)
        return value if isinstance(value, list) else []

    investment = rows_of("investment_rows")
    returns = rows_of("return_rows")
    terms_rows = rows_of("terms_rows")
    cases = rows_of("value_mapping")

    steps = [
        ("investment", investment, INVEST_OPEN, render_investment, _replace_inner),
        ("return", returns, RETURNS_OPEN, render_returns, _replace_inner),
    ]
    for name, rows, pattern, render, replace in steps:
        if not rows:
            continue
        html, done = replace(html, pattern, render(rows))
        report["written" if done else "missing"].append(name)

    # TERMS is written whether or not it has rows: its empty state is designed.
    html, done = _replace_outer(html, TERMS_OPEN, render_terms(terms_rows))
    report["written" if done else "missing"].append("terms")

    if cases:
        rows = _value_map_rows(cases)
        if rows is None:
            report["missing"].append("chart (a figure did not parse)")
        else:
            html, done = _replace_inner(html, VM_ROWS_OPEN, rows)
            report["written" if done else "missing"].append("chart")
    return html, report
