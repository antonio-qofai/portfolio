"""The reviewer's own commercial terms, turned into the markup slide 5 renders.

Every figure on a proposal's Commercial Terms slide is QofAI's per-deal arithmetic.
The platform is not meant to supply any of it, so the render leaves markers and a
human types the numbers. This module is the half of that job that is arithmetic and
copy rather than document surgery: it validates a submission, works out the bar
geometry, and builds the fragments `html_edit_layer.set_commercial_terms` writes
into the slide.

WHY A FORM AND NOT A LIST OF MARKERS

The studio used to take these as generic label/value rows on the Generate tab,
before the deck existed, and everything else on the slide arrived as
`[MISSING: ...]` rows in the supply-missing card afterwards. Antonio, 2026-08-20:
"the commercial terms editing part is a bit confusing ... I think I want to
structure the commercial terms part of the editing portion of the UI to just have
three cases: a conservative case, a base case, an optimistic case ... Honestly, I
think open text is the best."

So the shape of the form is the shape of the slide: three cases, each with its four
figures, then the write-ins under them. A reviewer settles terms after seeing the
deck, which is also why this moved off the Generate tab entirely.

OPEN TEXT, AND THE ONE PLACE IT IS NOT

Every field is free text and every figure is shown on the slide VERBATIM as typed.
"$486K", "$486,000", "~$224K" and "$672,000 (cap)" all reach the deck exactly as
written.

The exception is not a formatting rule, it is the chart. `.vm-bar` is a stacked bar
drawn to scale across cases, so three of the four figures in a case have to be read
as numbers to size the segments. A figure with no number in it is refused, naming
the field, because the alternative is a chart whose bars contradict the labels
printed on them -- and it would look deliberate. Which format is up to the
reviewer; that there is a number in it is up to the chart.

WHAT IS REFUSED, AND WHAT IS SIMPLY ABSENT

A case left entirely blank is not on the chart. A case with SOME of its figures is
refused, naming the blanks: a row with a comp figure and no retained EBITDA cannot
be drawn to scale, and the coherence of one row is a real fact about the data
rather than a question about what the source happened to emit. That distinction is
Antonio's (2026-08-20), and it is why a base case the render never emitted can
simply be typed in -- with the gain figure a write-in too, no value in a scenario
row comes from a source, so there is nothing there for a refusal to protect.
"""

import re

# THE OPENING OFFER FOR A DECK THAT STATES NO CASES, and nothing more than that
# since item 26. These were the frame every deck's cases were fitted into, which
# relabelled a source's own case names; `form_state` now keeps the names the deck
# carries and reaches for these only when it carries none. A source stating two
# cases gets two, stating four gets four, and the count is never padded to three.
#
# Not widened to four or five names on purpose. Four documents in, none of them
# agree on a count: Fabrikam states a range rather than cases at all and Casey's
# three 2026 PRDs each state two, under names ("Ambitious") that no list we wrote
# would have guessed. A longer fixed list is the same mistake with more entries.
SCENARIOS = ("Conservative", "Base Case", "Optimistic")
# Read by nothing as of item 26: `html_edit_layer.render_value_map_rows` takes
# its `base_scenario` as an argument and `read_commercial_regions` defaults that
# from the deck. Kept as the name the renderer's `.vm-row base` accent looks for.
BASE_SCENARIO = "Base Case"

# The three figures each case needs before its bar can be drawn, and the label the
# reviewer sees for each -- used in refusal messages, so what they read names the
# box they have to go back to.
CASE_FIGURES = (
    ("qofai_comp", "QofAI compensation"),
    ("client_retained_ebitda", "client retained EBITDA"),
    ("enterprise_value", "enterprise value at exit"),
)

# The gain figure is the row's label on the chart, not part of its geometry, so it
# is required for a filled case but never parsed.
CASE_LABEL = ("ebitda_gain", "EBITDA gain")

CASE_FIELDS = (CASE_LABEL,) + CASE_FIGURES

# The three write-in rows the strip starts with. Defaults a reviewer renames or
# extends, not a fixed set: a flat fee, a retainer plus a success fee and a
# performance schedule are all just rows with a label and a value, which is the
# genericity `commercial_rows` was given in the first place (D1a, 2026-08-10) and
# the reason it survives this change.
DEFAULT_ROW_LABELS = ("QOFAI INVESTMENT", "CLIENT UP-FRONT", "COMP SCHEDULE")

_MULTIPLIER = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000,
               "bn": 1_000_000_000}

# A dollar figure as a reviewer writes one. Leading noise (`~`, `$`, `+`) and any
# trailing parenthetical (`(cap)`) are not part of the number; a K/M/B suffix is.
_MONEY_RE = re.compile(
    r"""(?P<sign>-)?          # a negative figure, kept so nonsense is visible
        \s*\$?\s*
        (?P<digits>\d(?:[\d,]*\d)?(?:\.\d+)?)   # ends in a digit, so a
                                                 # trailing comma is punctuation
        \s*(?P<suffix>bn|[kmb])?\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


class TermsRejected(Exception):
    """A submission was not coherent enough to render, so nothing is written.

    Carries one message per problem, joined, because a reviewer fixing a form wants
    every blank named at once rather than one per round trip.
    """


def parse_money(text):
    """The number in a reviewer's dollar figure, or ``None`` if there is none.

    Generous on purpose: ``$486K``, ``486,000``, ``~$2.76M``, ``$672,000 (cap)``
    and ``$0`` all read. A parenthetical suffix is ignored, since ``(cap)`` is a
    note about the figure rather than part of it. Only the FIRST number is read --
    ``$486K per year`` is 486000, and a value carrying two numbers is read as its
    first, which is what a figure written for a bar chart means.
    """
    if not text:
        return None
    without_notes = re.sub(r"\([^)]*\)", " ", str(text))
    found = _MONEY_RE.search(without_notes)
    if not found:
        return None
    value = float(found.group("digits").replace(",", ""))
    suffix = (found.group("suffix") or "").lower()
    value *= _MULTIPLIER.get(suffix, 1)
    return -value if found.group("sign") else value


def _blank(value):
    return not (value or "").strip()


def clean_cases(cases):
    """Validate the three case sections, dropping the empty ones.

    ``cases`` is ``[{"scenario", "ebitda_gain", "qofai_comp",
    "client_retained_ebitda", "enterprise_value"}, ...]``. Returns the filled ones
    with their text stripped and each figure's parsed number under
    ``_<field>_value``. Raises :class:`TermsRejected` listing every problem.

    An entirely blank case is omitted from the chart rather than refused, which is
    how a deck ends up with two cases when a reviewer only has two to state. A
    partly filled one is refused: see the module docstring.
    """
    problems = []
    filled = []
    for case in cases:
        scenario = (case.get("scenario") or "").strip()
        values = {name: (case.get(name) or "").strip() for name, _ in CASE_FIELDS}
        if all(_blank(v) for v in values.values()):
            continue

        missing = [label for name, label in CASE_FIELDS if _blank(values[name])]
        if missing:
            problems.append(
                f"the {scenario or 'unnamed'} case is missing its "
                f"{_and_list(missing)} -- a case needs all four figures or none, "
                f"because its bar is drawn to scale from three of them"
            )
            continue

        row = {"scenario": scenario, **values}
        for name, label in CASE_FIGURES:
            number = parse_money(values[name])
            if number is None:
                problems.append(
                    f"the {scenario or 'unnamed'} case's {label}, "
                    f"“{values[name]}”, carries no figure the chart can "
                    f"size a bar from; the wording reaches the slide exactly as "
                    f"typed, but there has to be a number in it"
                )
            row[f"_{name}_value"] = number
        filled.append(row)

    if problems:
        raise TermsRejected("; ".join(problems))
    if not filled:
        raise TermsRejected(
            "no case was filled in, so there is nothing to put on the chart -- "
            "fill at least one of the three"
        )
    return filled


def clean_rows(rows):
    """Validate the commercial-terms strip's rows, dropping the empty ones.

    ``rows`` is ``[{"label", "value"}, ...]``. A row with a label and no value (or
    the reverse) is refused, since a box with a heading and no figure under it is
    an unfinished slide rather than a shorter one. Returning an EMPTY list is
    valid and meaningful: it restores the render's own "awaiting commercial terms
    input" state rather than leaving an empty strip, so clearing the rows is a way
    back rather than a broken deck.
    """
    problems = []
    clean = []
    for index, row in enumerate(rows, start=1):
        label = (row.get("label") or "").strip()
        value = (row.get("value") or "").strip()
        if _blank(label) and _blank(value):
            continue
        if _blank(value):
            problems.append(f"row {index} (“{label}”) has no value")
            continue
        if _blank(label):
            problems.append(f"row {index} has a value but no label")
            continue
        clean.append({"label": label, "value": value})
    if problems:
        raise TermsRejected("; ".join(problems))
    return clean


def bar_widths(cases):
    """Segment widths for every case's bar, as percentages of a shared maximum.

    The geometry the renderer's own spec describes, moved into code. ``M`` is the
    largest enterprise value across the cases, so bars are comparable row to row;
    each segment is its figure over ``M``, and the ``ev`` segment is the remainder
    after comp and retained EBITDA, because the bar stacks to the enterprise value
    rather than beside it.

    In code rather than in the render prompt because the render no longer decides
    it. A reviewer supplies these figures AFTER the deck exists, so the model that
    drew the bars never sees them; the deck as rendered carries placeholder widths
    (20/30/30 on the deck of 2026-08-20), and filling the labels without moving
    the geometry produces a chart contradicting the numbers printed on it.
    Deterministic arithmetic is also simply better here than a model doing
    division.

    A remainder below zero is clamped to zero. It means a reviewer stated an
    enterprise value under the comp plus retained EBITDA it is supposed to
    contain, which makes the stack meaningless; the figures still print verbatim,
    so nothing is misreported, and refusing someone's own arithmetic is not this
    module's job.
    """
    largest = max(case["_enterprise_value_value"] for case in cases)
    out = []
    for case in cases:
        comp = case["_qofai_comp_value"]
        retained = case["_client_retained_ebitda_value"]
        total = case["_enterprise_value_value"]
        remainder = max(total - comp - retained, 0.0)
        if largest <= 0:
            # Every case at zero: no bar can be drawn to scale, so none is.
            out.append({"comp": 0.0, "ret": 0.0, "ev": 0.0})
            continue
        out.append({
            "comp": round(100.0 * comp / largest, 2),
            "ret": round(100.0 * retained / largest, 2),
            "ev": round(100.0 * remainder / largest, 2),
        })
    return out


# A comp schedule's periods, as a reviewer separates them. The middot is what the
# deck's own copy uses between run-in items, and a pipe or a newline read the same
# way for someone typing quickly.
_SCHEDULE_SPLIT_RE = re.compile(r"\s*[·|\n]\s*")

# One tile: a period and its share. The percent must END the segment, so
# "Capped at 2.5x initial investment" is prose and "YEAR 1 20%" is a tile.
_SCHEDULE_TILE_RE = re.compile(
    r"^(?P<period>\S.*?)\s+(?P<percent>\d+(?:\.\d+)?\s*%)$")

# Two, because one "YEAR 1 20%" is a sentence fragment rather than a schedule, and
# turning it into a lone tile would be reading intent into a single phrase.
_MIN_SCHEDULE_TILES = 2


def split_schedule(value):
    """``(tiles, caption)`` for a comp-schedule value, tiles empty when it is prose.

    The FBK deck sets a comp schedule as a row of period/share tiles with the
    conditions as prose underneath, and `--accent-tint` is documented in the
    stylesheet as the "pale blue fill (comp boxes, phase bands)" for exactly that.
    A reviewer typing the schedule as one open-text line got a run-on paragraph in
    the small body font instead (Antonio, 2026-08-20: "the comp schedule
    percentages shoud be fixed too").

    So the structure is READ OUT OF the open text rather than asked for in more
    fields. Every leading segment shaped "<period> <percent>" becomes a tile; the
    first segment that is not takes the rest of the line with it, verbatim from the
    original string so the prose keeps the reviewer's own separators. Nothing is
    reordered, reworded, or added.

    Tiles are found only at the HEAD of the value. A share mentioned inside the
    conditions ("Client retains 80-84%") is prose and stays prose, because a tile
    is a column in a schedule and prose is a sentence about it.
    """
    text = (value or "").strip()
    if not text:
        return [], ""

    tiles = []
    at = 0
    for separator in list(_SCHEDULE_SPLIT_RE.finditer(text)) + [None]:
        end = separator.start() if separator else len(text)
        found = _SCHEDULE_TILE_RE.match(text[at:end].strip())
        if not found:
            break
        tiles.append({"period": found.group("period").strip(),
                      "percent": found.group("percent").replace(" ", "")})
        if separator is None:
            at = len(text)
            break
        at = separator.end()

    if len(tiles) < _MIN_SCHEDULE_TILES:
        return [], text
    return tiles, text[at:].strip()


def _and_list(items):
    """``"a"``, ``"a and b"``, ``"a, b and c"`` -- for a message a person reads."""
    items = list(items)
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def blank_submission(*, downside=""):
    """An empty form: the three cases and the three default rows, nothing filled.

    ``downside`` pre-fills the risk-reversal line, which the studio passes from
    `commercial_defaults` so the standing clause is already there and a reviewer
    usually leaves it alone.
    """
    return {
        "cases": [{"scenario": name, "ebitda_gain": "", "qofai_comp": "",
                   "client_retained_ebitda": "", "enterprise_value": ""}
                  for name in SCENARIOS],
        "rows": [{"label": label, "value": ""} for label in DEFAULT_ROW_LABELS],
        "client_retention": "",
        "downside_protection": downside,
        "terms_footnote": "",
    }


def form_state(on_deck, *, downside=""):
    """What the form shows: the deck's own cases, under the deck's own names.

    ``on_deck`` is `html_edit_layer.read_commercial_terms` output, or ``None``.

    ITEM 26. THE DECK'S CASE NAMES ARE THE FRAME, NOT ``SCENARIOS``. This used to
    match the deck's cases against the fixed three by name, drop every case that
    did not match into a leftovers pile, then walk the fixed three and pop a
    leftover into each empty slot. On a source stating Conservative and
    Ambitious, which is what all three of Casey's 2026 PRDs state, that returned
    Conservative with its own figures, Ambitious's figures relabelled "Base
    Case", and an invented empty "Optimistic".

    That is a faithfulness defect rather than a cosmetic one. The figures survive
    and the name they belong to is replaced with one of ours, so a reviewer
    deciding what ships is shown a case the client's document never proposed with
    real money attached to it. It is also the opposite of Antonio's 2026-09-17
    ruling that an uploaded document outranks the paper: a document cannot
    outrank anything if we relabel what it says.

    THE POSITION-MATCHING WAS A REASONABLE FEATURE AND ITS INTENT SURVIVES. It
    was written so a render that said "Base" or "Mid Case" still pre-filled
    rather than making a reviewer retype it, and it still does. The case keeps
    its values AND its own name now, which is what that intent wanted in the
    first place; the fixed three were only ever the slots it had to arrive in.

    ``SCENARIOS`` is now what it reads as, an opening offer for a deck that
    states no cases at all, applied through the empty branch below and through
    :func:`blank_submission`. A case the deck carries with no name of its own
    takes the next unused default, since an unlabelled box is no use to a
    reviewer and there is no stated name to preserve.

    WHAT THIS GIVES UP, stated because it was once asked for. Antonio,
    2026-08-20, on a two-case deck: "the UI can hvae 3 scenario boxes". A deck
    stating two cases now shows two, so the third box is gone with the relabelling
    that came with it. The form names each case in a hidden field, so a reviewer
    cannot rename a box or add one; offering a spare box to type a NEW case into
    needs an editable name and is its own change.

    Rows come from the deck as they are, since a reviewer may have renamed or added
    them; the three defaults are used only when the strip is empty. ``downside``
    fills the risk-reversal line only when the deck does not already carry one.
    """
    if not on_deck:
        return blank_submission(downside=downside)

    stated = list(on_deck.get("cases") or [])
    taken = {(case.get("scenario") or "").strip().lower() for case in stated}
    spare = [name for name in SCENARIOS if name.lower() not in taken]

    cases = []
    for case in stated:
        name = (case.get("scenario") or "").strip()
        if not name:
            if not spare:
                continue
            name = spare.pop(0)
        cases.append({
            "scenario": name,
            **{field: (case.get(field) or "").strip()
               for field, _label in CASE_FIELDS},
        })
    if not cases:
        # A deck with a terms slide but no cases on it still needs boxes.
        cases = blank_submission()["cases"]

    rows = list(on_deck.get("rows") or [])
    if not rows:
        rows = [{"label": label, "value": ""} for label in DEFAULT_ROW_LABELS]

    return {
        "cases": cases,
        "rows": rows,
        "client_retention": on_deck.get("client_retention") or "",
        "downside_protection": on_deck.get("downside_protection") or downside,
        "terms_footnote": on_deck.get("terms_footnote") or "",
    }
