"""The reviewer's own commercial terms: one form, three cases, five regions.

Every figure on a proposal's Commercial Terms slide is QofAI's per-deal arithmetic
and none of it comes from a source. The studio used to collect the strip rows on
the Generate tab, before the deck existed, and leave the other eleven figures to
arrive as `[MISSING: ...]` rows in the supply-missing card afterwards. Two places,
two shapes, one slide. Antonio, 2026-08-20: "the commercial terms editing part is a
bit confusing ... I think I want to structure the commercial terms part of the
editing portion of the UI to just have three cases: a conservative case, a base
case, an optimistic case ... Honestly, I think open text is the best."

What these pin, hardest first:

  1. THE CHART CANNOT CONTRADICT ITS OWN LABELS. The bars are drawn to scale from
     the figures printed on them. The deck as rendered carries placeholder widths
     (20/30/30), and the reviewer supplies these numbers after the render, so the
     geometry has to be computed here or the chart lies.
  2. OPEN TEXT REACHES THE SLIDE VERBATIM, and the form reloads exactly, em dash
     and ampersand included. A form that silently alters a reviewer's own words is
     worse than one that does not reload.
  3. ALWAYS THREE CASES, including one the render never emitted.
  4. A PARTLY FILLED CASE IS REFUSED, a wholly blank one is simply absent.
  5. A BLANK WRITE-IN RESTORES ITS MARKER, because blank means not supplied.

Run with: python3 -m pytest tests/test_commercial_terms.py
"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ui"))

from commercial_terms import (  # noqa: E402
    BASE_SCENARIO,
    DEFAULT_ROW_LABELS,
    SCENARIOS,
    TermsRejected,
    bar_widths,
    clean_cases,
    clean_rows,
    form_state,
    parse_money,
    split_schedule,
)
from html_edit_layer import (  # noqa: E402
    EditNotApplicable,
    list_missing_markers,
    load_edit_log,
    read_commercial_terms,
    set_commercial_terms,
    set_commercial_terms_and_save,
    undo_last_edit,
)
from deck_run import run_deck

DECK_FIXTURE = os.path.join(
    os.path.dirname(__file__), "fixtures", "missing-values",
    "proposal-deck-30-markers.html")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def deck():
    return _read(DECK_FIXTURE)


def _case(scenario, gain, comp, retained, ev):
    return {"scenario": scenario, "ebitda_gain": gain, "qofai_comp": comp,
            "client_retained_ebitda": retained, "enterprise_value": ev}


# FBK's own figures, which are the shape a real submission takes.
FBK_CASES = [
    _case("Conservative", "+1.4pp", "$486K", "$2.76M", "$12.0M"),
    _case("Base Case", "+1.9pp", "$654K", "$3.70M", "$14.3M"),
    _case("Optimistic", "+2.4pp", "$672K (cap)", "$4.81M", "$16.5M"),
]


def _terms(cases=None, rows=None, **write_ins):
    return {
        "cases": clean_cases(cases if cases is not None else FBK_CASES),
        "rows": clean_rows(rows if rows is not None else []),
        "client_retention": write_ins.get("client_retention", ""),
        "downside_protection": write_ins.get("downside_protection", ""),
        "terms_footnote": write_ins.get("terms_footnote", ""),
    }


# ------------------------------ reading a figure ----------------------------

@pytest.mark.parametrize("text,expected", [
    ("$486K", 486_000), ("486,000", 486_000), ("~$2.76M", 2_760_000),
    ("$672,000 (cap)", 672_000), ("$0", 0), ("$12.0M", 12_000_000),
    ("$1.2bn", 1_200_000_000), ("$486K per year", 486_000),
])
def test_a_dollar_figure_is_read_however_a_reviewer_writes_it(text, expected):
    """Open text means the FORMAT is the reviewer's. The parse is generous for
    exactly that reason; what it needs is a number, not a convention."""
    assert parse_money(text) == expected


@pytest.mark.parametrize("text", ["", None, "TBD", "to be agreed", "n/a"])
def test_a_figure_with_no_number_reads_as_nothing(text):
    assert parse_money(text) is None


def test_a_parenthetical_note_is_not_part_of_the_figure():
    """"(cap)" is a note about the number, and reading it as part of one would put
    a wrong-width bar on the slide."""
    assert parse_money("$672,000 (cap)") == parse_money("$672,000")


# ------------------------------ the three cases -----------------------------

def test_the_three_cases_are_the_slide_s_own_convention():
    assert SCENARIOS == ("Conservative", "Base Case", "Optimistic")
    assert BASE_SCENARIO == "Base Case"


def test_a_wholly_blank_case_is_absent_not_refused():
    """How a deck ends up showing two cases: the reviewer had two to state."""
    cases = clean_cases([FBK_CASES[0], _case("Base Case", "", "", "", ""),
                         FBK_CASES[2]])
    assert [c["scenario"] for c in cases] == ["Conservative", "Optimistic"]


def test_a_partly_filled_case_is_refused_with_every_blank_named():
    """The one refusal Antonio kept. A row with a comp figure and no retained
    EBITDA cannot be drawn to scale, and that is a fact about the row rather than
    about what a source emitted. Every blank at once, so the form takes one pass."""
    with pytest.raises(TermsRejected) as caught:
        clean_cases([_case("Base Case", "+1.9pp", "$654K", "", "")])
    message = str(caught.value)
    assert "Base Case" in message
    assert "client retained EBITDA" in message
    assert "enterprise value at exit" in message
    assert "all four figures or none" in message


def test_a_figure_the_chart_cannot_size_is_refused_naming_the_field():
    """Open text, with one exception that is not a formatting rule but the chart:
    a bar drawn from a figure with no number in it contradicts the label printed on
    it, and it would look deliberate."""
    with pytest.raises(TermsRejected) as caught:
        clean_cases([_case("Base Case", "+1.9pp", "to be agreed", "$3.70M",
                           "$14.3M")])
    message = str(caught.value)
    assert "QofAI compensation" in message
    assert "to be agreed" in message
    assert "exactly as typed" in message


def test_no_case_at_all_is_refused():
    with pytest.raises(TermsRejected) as caught:
        clean_cases([_case(name, "", "", "", "") for name in SCENARIOS])
    assert "nothing to put on the chart" in str(caught.value)


# --------------------------------- the rows ---------------------------------

def test_the_default_labels_are_the_three_that_were_asked_for():
    assert DEFAULT_ROW_LABELS == ("QOFAI INVESTMENT", "CLIENT UP-FRONT",
                                  "COMP SCHEDULE")


def test_a_half_filled_row_is_refused():
    with pytest.raises(TermsRejected) as caught:
        clean_rows([{"label": "COMP SCHEDULE", "value": ""}])
    assert "has no value" in str(caught.value)
    with pytest.raises(TermsRejected):
        clean_rows([{"label": "", "value": "$0"}])


def test_rows_may_be_renamed_and_extended():
    """A flat fee, a retainer plus a success fee and a performance schedule are all
    just a label and a value, which is why `commercial_rows` stays generic."""
    rows = clean_rows([{"label": "FLAT FEE", "value": "$100K"},
                       {"label": "PAYMENT TERMS", "value": "Net 30"},
                       {"label": "TERM", "value": "24 months"},
                       {"label": "RENEWAL", "value": "Annual"}])
    assert [r["label"] for r in rows] == ["FLAT FEE", "PAYMENT TERMS", "TERM",
                                          "RENEWAL"]


# ------------------------------ the bar geometry ----------------------------

def test_bars_are_drawn_to_scale_against_the_largest_enterprise_value():
    """M is the biggest EV across the cases, so the rows are comparable. The ev
    segment is the REMAINDER after comp and retained, because the bar stacks TO the
    enterprise value rather than beside it."""
    cases = clean_cases(FBK_CASES)
    widths = bar_widths(cases)
    assert len(widths) == 3
    # Optimistic's EV is the largest, so its three segments fill the track.
    assert round(sum(widths[2].values()), 2) == 100.0
    # And every row is measured against that same maximum.
    assert widths[0]["comp"] == round(100 * 486_000 / 16_500_000, 2)
    assert widths[1]["ret"] == round(100 * 3_700_000 / 16_500_000, 2)
    assert round(sum(widths[0].values()), 2) == round(
        100 * 12_000_000 / 16_500_000, 2)


def test_a_remainder_below_zero_is_clamped_rather_than_drawn_backwards():
    """A reviewer stating an enterprise value under the comp plus retained EBITDA it
    is supposed to contain. The figures still print verbatim, so nothing is
    misreported, and a negative width would silently break the chart."""
    widths = bar_widths(clean_cases([
        _case("Base Case", "+1.9pp", "$8M", "$8M", "$10M")]))
    assert widths[0]["ev"] == 0.0


def test_all_zero_figures_draw_no_bar_rather_than_dividing_by_zero():
    widths = bar_widths(clean_cases([_case("Base Case", "flat", "$0", "$0", "$0")]))
    assert widths == [{"comp": 0.0, "ret": 0.0, "ev": 0.0}]


# ----------------------- writing the slide, and reading it ------------------

def test_the_form_writes_five_regions_and_leaves_payment_mechanics_alone(deck):
    """Eleven markers on slide 5 become none. `payment_mechanics` is not the form's
    to write — it is the one region that says the same thing on every engagement, so
    it belongs to the standing-language button (see
    `test_commercial_defaults.py`). Asking a reviewer to retype QofAI's own
    boilerplate in this form would undo the point of that button."""
    out, applied = set_commercial_terms(deck, _terms(
        rows=[{"label": "QOFAI INVESTMENT", "value": "~$224K"}],
        client_retention="Client retains 80-84%.",
        downside_protection="If margins don't improve, QofAI earns nothing.",
        terms_footnote="Directional, for decision support."))
    assert [r["role"] for r in applied] == [
        "commercial_rows", "client_retention", "downside_protection",
        "value_mapping", "terms_footnote"]
    left = [m["field"] for m in list_missing_markers(out) if m["slide"] == 5]
    assert left == ["payment_mechanics", "deck_date"], left


def test_a_base_case_the_render_never_emitted_is_written(deck):
    """The fixture deck carries Conservative and Optimistic only, because the
    paper's table had two rows. The base case is the one an operating partner reads
    first, and with the gain figure a write-in there is no sourced value in the row
    for a refusal to protect."""
    assert [c["scenario"] for c in read_commercial_terms(deck)["cases"]] == [
        "Conservative", "Optimistic"]
    out, _applied = set_commercial_terms(deck, _terms())
    scenarios = [c["scenario"] for c in read_commercial_terms(out)["cases"]]
    assert scenarios == ["Conservative", "Base Case", "Optimistic"]
    assert '<div class="vm-row base">' in out, "the base row carries its accent"


def test_the_strip_is_rebuilt_with_its_own_column_count(deck):
    """Why the strip replaces its whole element. The count lives in an inline style
    on the open tag, and the empty state renders ONE box spanning
    `repeat(1,1fr)`, so writing three boxes into it is not a text swap."""
    assert 'grid-template-columns:repeat(1,1fr)' in deck
    out, _applied = set_commercial_terms(deck, _terms(rows=[
        {"label": "QOFAI INVESTMENT", "value": "~$224K"},
        {"label": "CLIENT UP-FRONT", "value": "$0"},
        {"label": "COMP SCHEDULE", "value": "Year 1 20%"}]))
    assert 'grid-template-columns:repeat(3,1fr)' in out
    assert out.count('<div class="tbox"') == 3
    assert '<div class="tbox tbox--empty">' not in out


def test_a_leading_dollar_figure_is_set_large_above_the_rest(deck):
    out, _applied = set_commercial_terms(deck, _terms(rows=[
        {"label": "QOFAI INVESTMENT", "value": "~$224K, absorbed upfront"},
        {"label": "COMP SCHEDULE", "value": "Year 1 20% then 10%"}]))
    assert '<div class="big">~$224K</div>' in out
    assert '<div class="bv">absorbed upfront</div>' in out
    # No leading figure, so no `.big` for that row: the whole line is the value.
    assert '<div class="bv">Year 1 20% then 10%</div>' in out


# ---------------------------- the comp schedule ------------------------------

FBK_SCHEDULE = ("YEAR 1 20% · YEAR 2 10% · YEAR 3 5% · YEAR 4+ 0% · Capped at 2.5x "
                "initial investment or 3 years, whichever first; ~$18.7K/yr "
                "support after that. Client retains 80-84%, 100% thereafter.")


def test_a_comp_schedule_s_periods_are_read_out_of_the_open_text():
    """The FBK deck sets a comp schedule as a row of period/share tiles with the
    conditions as prose beneath, and the stylesheet documents `--accent-tint` as the
    "pale blue fill (comp boxes, phase bands)" for exactly that. Typed as one
    open-text line it came out a run-on paragraph in the small body font (Antonio,
    2026-08-20: "the comp schedule percentages shoud be fixed too"). The structure
    is read out of the text rather than asked for in more fields, which keeps the
    form open text."""
    tiles, caption = split_schedule(FBK_SCHEDULE)
    assert [(t["period"], t["percent"]) for t in tiles] == [
        ("YEAR 1", "20%"), ("YEAR 2", "10%"), ("YEAR 3", "5%"), ("YEAR 4+", "0%")]
    assert caption.startswith("Capped at 2.5x initial investment")
    assert "Client retains 80-84%" in caption


def test_a_share_inside_the_conditions_stays_prose():
    """Tiles are found only at the HEAD. "Client retains 80-84%" is a sentence about
    the schedule, not a column in it, and promoting it to a tile would invent a
    period that does not exist."""
    _tiles, caption = split_schedule(FBK_SCHEDULE)
    assert "80-84%" in caption


def test_a_schedule_with_no_conditions_has_no_caption():
    tiles, caption = split_schedule("Year 1 20% · Year 2 10% · Year 3 5%")
    assert len(tiles) == 3
    assert caption == ""


def test_one_period_is_not_a_schedule():
    """A lone "YEAR 1 20%" is a sentence fragment, and turning it into a single tile
    would be reading intent into one phrase."""
    tiles, caption = split_schedule("YEAR 1 20%")
    assert tiles == []
    assert caption == "YEAR 1 20%"


@pytest.mark.parametrize("value", [
    "Capped at 2.5x initial investment or 3 years.",
    "$0, no capital outlay.",
    "Net 30",
    "",
])
def test_prose_is_left_alone(value):
    tiles, caption = split_schedule(value)
    assert tiles == []
    assert caption == value.strip()


def test_the_tiles_are_rendered_with_the_spent_period_muted(deck):
    """The shape of the decline is the message, which is the reason for tiles at
    all: in the reference deck the paying years carry the pale blue fill and the
    0% year is grey. Styled inline because the edit layer writes into a stylesheet
    the render already fixed, so a new class name would arrive with no rules."""
    out, _applied = set_commercial_terms(deck, _terms(rows=[
        {"label": "COMP SCHEDULE", "value": FBK_SCHEDULE}]))
    assert out.count("var(--accent-tint)") >= 3, "the paying years are filled"
    for period, percent in (("YEAR 1", "20%"), ("YEAR 2", "10%"),
                            ("YEAR 3", "5%"), ("YEAR 4+", "0%")):
        assert f">{period}</div>" in out, period
        assert f">{percent}</div>" in out, percent
    # The 0% tile takes the outline-and-muted treatment, not the fill.
    spent = out.split(">YEAR 4+</div>")[1].split("</div>")[0]
    assert "var(--mute)" in spent, spent
    assert "Capped at 2.5x initial investment" in out
    # And the schedule is NOT put through the leading-figure split.
    assert '<div class="big">' not in out.split('COMP SCHEDULE')[1].split(
        "</div></div>")[0]


def test_a_schedule_round_trips_exactly(deck):
    """Tiles are a display of the line, not a replacement for it, so the form
    reloads the reviewer's own text and re-writing it changes nothing."""
    out, _applied = set_commercial_terms(deck, _terms(rows=[
        {"label": "COMP SCHEDULE", "value": FBK_SCHEDULE}]))
    back = read_commercial_terms(out)
    assert back["rows"] == [{"label": "COMP SCHEDULE", "value": FBK_SCHEDULE}]
    twice, _applied = set_commercial_terms(out, _terms(rows=back["rows"]))
    assert twice == out


def test_a_bare_number_alone_in_a_row_is_set_large(deck):
    """Found on a real slide (Antonio, 2026-08-20: "the font is not good for the 0
    for client up front"). A reviewer typed `0` for CLIENT UP-FRONT and got it in
    the small body font, because the split only recognised a figure carrying a `$`.
    With no prose beside it a bare number can only be the figure, so it takes the
    large treatment. Verbatim still: `0` is set large as `0`, not as `$0` --
    inventing a currency symbol nobody typed is the small helpfulness that ends up
    on a client deck."""
    out, _applied = set_commercial_terms(deck, _terms(rows=[
        {"label": "CLIENT UP-FRONT", "value": "0"},
        {"label": "QOFAI INVESTMENT", "value": "$500K"},
        {"label": "SUPPORT", "value": "1,250,000"}]))
    assert '<div class="big">0</div>' in out
    assert "<div class=\"bv\">0</div>" not in out, "no longer the body font"
    assert '<div class="big">$500K</div>' in out
    assert '<div class="big">1,250,000</div>' in out


def test_a_bare_number_leading_prose_is_not_the_figure(deck):
    """The reason the `$` is still required where prose follows. "3 years of
    support" must not set a giant "3" over the word "years"."""
    out, _applied = set_commercial_terms(deck, _terms(rows=[
        {"label": "TERM", "value": "3 years of support"},
        {"label": "PAYMENT TERMS", "value": "Net 30 from invoice"}]))
    assert '<div class="big">3</div>' not in out
    assert '<div class="bv">3 years of support</div>' in out
    assert '<div class="bv">Net 30 from invoice</div>' in out


def test_a_comma_after_the_figure_is_punctuation_not_part_of_it(deck):
    """Found by looking at the slide. "$0, no capital outlay" was setting a `.big`
    of "$0," and the stray comma read as a typo on a client deck. The digit run has
    to end in a digit; thousands separators inside it are still part of the number.
    """
    out, _applied = set_commercial_terms(deck, _terms(rows=[
        {"label": "CLIENT UP-FRONT", "value": "$0, no capital outlay."},
        {"label": "YEAR ONE", "value": "$1,250,000 in year one"}]))
    assert '<div class="big">$0</div>' in out
    assert '<div class="bv">no capital outlay.</div>' in out
    assert '<div class="big">$1,250,000</div>' in out
    assert parse_money("$1,250,000 in year one") == 1_250_000


def test_clearing_every_row_restores_the_render_s_own_empty_state(deck):
    """A way back, not a broken deck. The slide is designed to say it is awaiting
    input rather than show a blank or an invented figure."""
    out, _applied = set_commercial_terms(deck, _terms(rows=[]))
    assert "AWAITING COMMERCIAL TERMS INPUT" in out
    assert "[MISSING: commercial_rows]" in out
    assert 'grid-template-columns:repeat(1,1fr)' in out


def test_a_blank_write_in_puts_its_marker_back(deck):
    """Blank means not supplied, and the marker is how the deck says so. An empty
    div would hide a gap the slide exists to show."""
    out, _applied = set_commercial_terms(deck, _terms(
        client_retention="", downside_protection="Something.",
        terms_footnote=""))
    fields = {m["field"] for m in list_missing_markers(out) if m["slide"] == 5}
    assert "client_retention" in fields
    assert "terms_footnote" in fields
    assert "downside_protection" not in fields


def test_every_figure_reaches_the_slide_verbatim(deck):
    """Including the cap note, which the geometry ignores and the slide keeps."""
    out, _applied = set_commercial_terms(deck, _terms())
    for figure in ("$486K", "$2.76M", "$12.0M", "$672K (cap)", "$16.5M"):
        assert figure in out, figure


def test_the_form_reloads_exactly_including_an_em_dash_and_an_ampersand(deck):
    """A form that quietly returns a reviewer's own words slightly altered is worse
    than one that does not reload at all. The display split drops the separator
    between a figure and its sentence, which is right on the slide and lossy as a
    round trip, so the row keeps its value verbatim in an inert attribute."""
    value = "~$224K — engineering & all implementation risk, absorbed."
    out, _applied = set_commercial_terms(deck, _terms(
        rows=[{"label": "QOFAI INVESTMENT", "value": value}]))
    assert read_commercial_terms(out)["rows"] == [
        {"label": "QOFAI INVESTMENT", "value": value}]


def test_writing_what_was_read_back_changes_nothing(deck):
    """Idempotent through the reader, which is what makes "fix one figure and
    resubmit" safe: the eleven fields a reviewer did not touch come back unchanged.
    """
    once, _applied = set_commercial_terms(deck, _terms(
        rows=[{"label": "QOFAI INVESTMENT", "value": "~$224K, absorbed"}],
        client_retention="Client retains 80-84%.",
        downside_protection="If margins don't improve, QofAI earns nothing.",
        terms_footnote="Directional."))
    back = read_commercial_terms(once)
    twice, _applied = set_commercial_terms(once, {
        "cases": clean_cases(back["cases"]), "rows": clean_rows(back["rows"]),
        "client_retention": back["client_retention"],
        "downside_protection": back["downside_protection"],
        "terms_footnote": back["terms_footnote"]})
    assert twice == once


def test_a_deck_missing_a_region_is_refused_whole(deck):
    """The slide is one statement about a deal. A strip of figures above a chart
    still reading `[MISSING: qofai_comp]` is not a shorter version of it."""
    broken = deck.replace('<div class="vm-rows">', '<div class="gone">')
    with pytest.raises(EditNotApplicable) as caught:
        set_commercial_terms(broken, _terms())
    assert "value_mapping" in str(caught.value)


def test_nothing_a_reviewer_types_can_print_as_a_tag(deck):
    out, _applied = set_commercial_terms(deck, _terms(
        rows=[{"label": "A & B", "value": "$1M > $0"}],
        client_retention="5 < 6"))
    assert "A &amp; B" in out
    assert read_commercial_terms(out)["client_retention"] == "5 < 6"


def test_a_status_deck_has_no_terms_to_read():
    assert read_commercial_terms(
        '<html><body><section class="slide"><p>x</p></section></body></html>'
    ) is None


# ------------------------------- the form state ------------------------------

def test_the_form_shows_the_cases_the_deck_carries_and_no_others(deck):
    """ITEM 26, and this test asserted the opposite until 2026-09-18.

    The fixture deck carries two cases. The form used to show three, inserting a
    blank "Base Case" between them out of the fixed tuple. A deck stating two
    cases now shows two: no third box, and nothing invented.
    """
    state = form_state(read_commercial_terms(deck), downside="standing clause")
    assert [c["scenario"] for c in state["cases"]] == ["Conservative", "Optimistic"]
    assert all(c["ebitda_gain"] for c in state["cases"])
    assert "Base Case" not in [c["scenario"] for c in state["cases"]]


def test_a_case_the_deck_names_is_never_relabelled_from_the_fixed_tuple():
    """The defect in one assertion: Ambitious came back as "Base Case" with its
    own figures attached, which is what all three of Casey's 2026 PRDs state."""
    state = form_state({
        "cases": [_case("Conservative", "+2.5 pts", "$139,000/yr", "", ""),
                  _case("Ambitious", "+4.5 pts", "$249,000/yr", "", "")],
        "rows": [], "client_retention": "", "downside_protection": "",
        "terms_footnote": "",
    })
    assert [c["scenario"] for c in state["cases"]] == ["Conservative", "Ambitious"]
    ambitious = state["cases"][1]
    assert ambitious["ebitda_gain"] == "+4.5 pts"
    assert ambitious["qofai_comp"] == "$249,000/yr"


def test_a_source_stating_more_cases_than_the_tuple_keeps_all_of_them():
    """The tuple is not a ceiling either. A case dropped for not fitting is the
    same defect wearing the other face."""
    state = form_state({
        "cases": [_case(name, "g", "c", "r", "e")
                  for name in ("Downside", "Base", "Upside", "Blue Sky")],
        "rows": [], "client_retention": "", "downside_protection": "",
        "terms_footnote": "",
    })
    assert [c["scenario"] for c in state["cases"]] == [
        "Downside", "Base", "Upside", "Blue Sky"]


def test_a_deck_with_a_terms_slide_but_no_cases_still_offers_the_defaults():
    """The opening offer survives, which is the whole point of keeping SCENARIOS."""
    state = form_state({"cases": [], "rows": [], "client_retention": "",
                        "downside_protection": "", "terms_footnote": ""})
    assert [c["scenario"] for c in state["cases"]] == list(SCENARIOS)
    assert all(not c["ebitda_gain"] for c in state["cases"])


def test_a_case_with_no_name_of_its_own_takes_an_unused_default():
    """There is no stated name to preserve and an unlabelled box helps nobody.
    The default it takes must not be one the deck already uses."""
    state = form_state({
        "cases": [_case("Conservative", "a", "b", "c", "d"),
                  _case("", "e", "f", "g", "h")],
        "rows": [], "client_retention": "", "downside_protection": "",
        "terms_footnote": "",
    })
    names = [c["scenario"] for c in state["cases"]]
    assert names[0] == "Conservative"
    assert names[1] in SCENARIOS and names[1] != "Conservative"
    assert state["cases"][1]["qofai_comp"] == "f"


def test_the_form_pre_fills_the_blue_box_with_the_standing_clause(deck):
    state = form_state(read_commercial_terms(deck), downside="standing clause")
    assert state["downside_protection"] == "standing clause"


def test_the_deck_s_own_clause_wins_over_the_standing_one(deck):
    out, _applied = set_commercial_terms(deck, _terms(
        downside_protection="A clause this deal negotiated."))
    state = form_state(read_commercial_terms(out), downside="standing clause")
    assert state["downside_protection"] == "A clause this deal negotiated."


def test_a_scenario_under_another_name_keeps_its_values_and_now_its_name(deck):
    """A render that said "Mid Case" should still pre-fill rather than making a
    reviewer retype it. That intent predates item 26 and survives it: the case
    keeps its values, and since 2026-09-18 it keeps its own name too instead of
    arriving in whichever fixed slot was free."""
    state = form_state({"cases": [_case("Mid Case", "+1.9pp", "$654K", "$3.70M",
                                        "$14.3M")],
                        "rows": [], "client_retention": "",
                        "downside_protection": "", "terms_footnote": ""})
    filled = [c for c in state["cases"] if c["qofai_comp"]]
    assert len(filled) == 1
    assert filled[0]["qofai_comp"] == "$654K"
    assert filled[0]["scenario"] == "Mid Case"


def test_the_form_starts_from_the_defaults_on_a_deck_with_no_slide():
    state = form_state(None, downside="standing clause")
    assert [r["label"] for r in state["rows"]] == list(DEFAULT_ROW_LABELS)
    assert all(not r["value"] for r in state["rows"])
    assert state["downside_protection"] == "standing clause"


# --------------------------- the revision chain -----------------------------

def _deck_file(tmp_path):
    path = os.path.join(str(tmp_path), "output-3.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(_read(DECK_FIXTURE))
    return path


def test_one_submission_is_one_revision_and_five_log_entries(tmp_path):
    """Five regions rewritten, one step of undo. A reviewer pressed one button."""
    path = _deck_file(tmp_path)
    result = set_commercial_terms_and_save(
        path, _terms(rows=[{"label": "QOFAI INVESTMENT", "value": "~$224K"}]),
        author="Antonio", now="2026-08-20T00:00:00+00:00")
    assert result["revision_name"] == "output-3-r1.html"
    assert "[MISSING: qofai_comp]" in _read(path), "the original is untouched"
    log = load_edit_log(path)
    assert len(log) == 5
    assert len({e["revision"] for e in log}) == 1
    assert all(e["kind"] == "content" for e in log), "never promotable"
    assert all(e["author"] == "Antonio" for e in log)


def test_undo_reverses_the_whole_slide_in_one_step(tmp_path):
    path = _deck_file(tmp_path)
    set_commercial_terms_and_save(path, _terms())
    undo_last_edit(path)
    back = read_commercial_terms(_read(path))
    assert [c["scenario"] for c in back["cases"]] == ["Conservative", "Optimistic"]
    assert back["rows"] == []
    assert load_edit_log(path) == []


# ------------------------- the form, through Flask --------------------------
#
# Every seam green and the feature switched off in the one place it is reached
# from, which is the lesson this project keeps re-applying. These drive the route a
# reviewer's submission actually posts to, with the field names the form sends.

pytest.importorskip("flask")

RUN_RESULT = {
    "status": "ok",
    "prompt": "(design prompt body)",
    "applied_preferences": [],
    "number": 3,
    "render_fidelity": {"ok": True, "missing_values": {}},
    "layout": {"ok": True, "checked": True, "skipped": "", "slides": 6,
               "findings": [], "summary": []},
}


def _studio(tmp_path, deck_html):
    import app as ui_app

    deck_dir = str(tmp_path)
    deck_path = os.path.join(deck_dir, "output-3.html")
    with open(deck_path, "w", encoding="utf-8") as f:
        f.write(deck_html)
    with open(os.path.join(deck_dir, "generated-prompt-3.txt"), "w",
              encoding="utf-8") as f:
        f.write("(design prompt body)")
    ui_app.generate_and_save_deck = lambda *a, **k: dict(
        RUN_RESULT, deck_path=deck_path,
        prompt_path=os.path.join(deck_dir, "generated-prompt-3.txt"))
    ui_app.DECKS_ROOT = deck_dir
    ui_app.DECISIONS_PATH = os.path.join(deck_dir, "gap-decisions.json")
    ui_app.app.testing = True
    ui_app._LAST_RESULT.clear()
    ui_app._FILL_EXPECTATIONS.clear()
    return ui_app, ui_app.app.test_client(), deck_path


@pytest.fixture
def studio(tmp_path):
    return _studio(tmp_path, _read(DECK_FIXTURE))


def _run(client, deck_type="proposal"):
    return run_deck(client, data={
        "deck_type": deck_type, "company": "Any Client", "project": "Any Project",
        "packet": "proposal-data-packet-EXAMPLE.md",
    }).get_data(as_text=True)


def _submit(client, deck_path, cases=None, rows=None, **write_ins):
    """Post what the form posts: one parallel list per field name."""
    cases = FBK_CASES if cases is None else cases
    rows = rows if rows is not None else [
        {"label": "QOFAI INVESTMENT", "value": "~$224K"}]
    return client.post("/commercial-terms", data={
        "path": deck_path, "author": "Antonio", "deck_type": "proposal",
        "company": "Any Client", "project": "Any Project",
        "packet": "proposal-data-packet-EXAMPLE.md",
        "scenario": [c["scenario"] for c in cases],
        "ebitda_gain": [c["ebitda_gain"] for c in cases],
        "qofai_comp": [c["qofai_comp"] for c in cases],
        "client_retained_ebitda": [c["client_retained_ebitda"] for c in cases],
        "enterprise_value": [c["enterprise_value"] for c in cases],
        "terms_row_label": [r["label"] for r in rows],
        "terms_row_value": [r["value"] for r in rows],
        "client_retention": write_ins.get("client_retention", ""),
        "downside_protection": write_ins.get("downside_protection", ""),
        "terms_footnote": write_ins.get("terms_footnote", ""),
    }).get_data(as_text=True)


def test_the_card_is_retired_even_on_a_deck_that_carries_the_old_slide(studio):
    """RETIRED 2026-09-23 (Antonio: the old commercial editors "were not
    relevant anymore"). Until then this asserted one case section per case the
    deck carries (item 26). The studio's deck still carries the old slide's two
    cases, which is when the card used to appear, so it proves the card is gone.
    The route and the slide reader are still tested in this file."""
    _ui, client, _deck = studio
    html = _run(client)
    assert "<legend" not in html
    assert 'name="qofai_comp"' not in html
    assert 'name="terms_row_label"' not in html
    assert 'action="/commercial-terms"' not in html


def test_the_card_is_not_offered_on_a_deck_with_no_commercial_slide(tmp_path):
    _ui, client, _deck = _studio(
        tmp_path,
        '<!doctype html><html><body><section class="slide" data-slide="1">'
        "<h1>Check-in</h1></section></body></html>")
    html = _run(client, deck_type="status")
    assert 'name="qofai_comp"' not in html


def test_submitting_writes_the_slide_and_says_what_it_wrote(studio):
    _ui, client, deck = studio
    _run(client)
    html = _submit(client, deck, client_retention="Client retains 80-84%.")

    assert "Wrote the commercial terms: 3 cases on the chart and 1 row" in html, \
        html[:900]
    revision = os.path.join(os.path.dirname(deck), "output-3-r1.html")
    written = _read(revision)
    assert '<div class="vm-row base">' in written
    assert "$3.70M" in written
    assert "grid-template-columns:repeat(1,1fr)" in written  # one row supplied
    assert "[MISSING: qofai_comp]" not in written


def test_a_submit_still_writes_but_the_page_offers_no_form(studio):
    """The reload-with-what-was-written form went with the card on 2026-09-23.
    A post to the route still writes the slide; the page it returns offers no
    form to fill again."""
    _ui, client, deck = studio
    _run(client)
    html = _submit(client, deck, client_retention="Client retains 80-84%.")
    assert "Wrote the commercial terms" in html, html[:900]
    assert 'value="$654K"' not in html
    assert 'name="qofai_comp"' not in html


def test_a_partly_filled_case_is_refused_and_nothing_is_written(studio):
    _ui, client, deck = studio
    _run(client)
    broken = [FBK_CASES[0], _case("Base Case", "+1.9pp", "$654K", "", ""),
              FBK_CASES[2]]
    html = _submit(client, deck, cases=broken)

    assert "were not written" in html, html[:900]
    assert "Base Case" in html
    assert "all four figures or none" in html
    assert not os.path.isfile(os.path.join(os.path.dirname(deck),
                                           "output-3-r1.html"))


def test_a_figure_with_no_number_is_refused_and_nothing_is_written(studio):
    _ui, client, deck = studio
    _run(client)
    html = _submit(client, deck, cases=[
        _case("Base Case", "+1.9pp", "to be agreed", "$3.70M", "$14.3M")])
    assert "were not written" in html, html[:900]
    assert "to be agreed" in html
    assert not os.path.isfile(os.path.join(os.path.dirname(deck),
                                           "output-3-r1.html"))


def test_two_cases_are_accepted_and_the_third_is_simply_absent(studio):
    _ui, client, deck = studio
    _run(client)
    html = _submit(client, deck, cases=[
        FBK_CASES[0], _case("Base Case", "", "", "", ""), FBK_CASES[2]])
    assert "2 cases on the chart" in html, html[:900]
    written = _read(os.path.join(os.path.dirname(deck), "output-3-r1.html"))
    assert '<div class="vm-row base">' not in written


def test_clearing_the_rows_reports_the_slide_back_to_awaiting_input(studio):
    _ui, client, deck = studio
    _run(client)
    html = _submit(client, deck, rows=[{"label": "", "value": ""}])
    assert "0 rows across the top, which puts the strip back to awaiting input" \
        in html, html[:900]
    written = _read(os.path.join(os.path.dirname(deck), "output-3-r1.html"))
    assert "AWAITING COMMERCIAL TERMS INPUT" in written


def test_the_standing_language_button_and_the_form_compose(studio):
    """Both write slide 5 and neither undoes the other: the button owns
    `payment_mechanics`, the form owns the other five, and they share the blue box
    (which the form pre-fills with the standing clause)."""
    _ui, client, deck = studio
    _run(client)
    client.post("/commercial-defaults", data={
        "path": deck, "deck_type": "proposal", "company": "Any Client",
        "project": "Any Project", "packet": "proposal-data-packet-EXAMPLE.md"})
    after_button = os.path.join(os.path.dirname(deck), "output-3-r1.html")
    _submit(client, after_button,
            downside_protection="If margins don't improve above your locked "
                                "baseline, QofAI earns nothing.")
    written = _read(os.path.join(os.path.dirname(deck), "output-3-r2.html"))
    assert written.count('<div class="st">') == 3, "the payment block survives"
    assert "$3.70M" in written, "and the terms landed"
    assert "If margins don't improve above your locked baseline" in written
    # The two write-ins this submission left blank keep their markers, which is the
    # blank-means-not-supplied rule holding across both features.
    left = [m["field"] for m in list_missing_markers(written) if m["slide"] == 5]
    # `deck_date` is a footer field on every slide, nothing to do with this slide.
    assert left == ["client_retention", "terms_footnote", "deck_date"], left
