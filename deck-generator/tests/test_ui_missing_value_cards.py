"""The supply-missing surface, driven through Flask as a reviewer drives it.

`test_missing_value_groups.py` pins the classification and the fan-out at the
seam. These pin the one function a reviewer actually clicks: the three cards on
the Result tab, and the route their inputs post to. E11 Stage 2g's lesson — every
seam green and the feature switched off in the one place it is reached from —
applied to this change.

The pipeline is stubbed, so no API key, no network and no real render are needed.
Every deck is written into a temp directory and every packet is a temp COPY,
because the committed fixtures are test inputs and a route may write beside a deck.

Run with: python3 -m pytest tests/test_ui_missing_value_cards.py
"""

import filecmp
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ui"))

pytest.importorskip("flask")

from html_edit_layer import list_missing_markers, load_edit_log  # noqa: E402
from deck_run import run_deck

_ROOT = os.path.join(os.path.dirname(__file__), "..")
DECK_FIXTURE = os.path.join(
    os.path.dirname(__file__), "fixtures", "missing-values",
    "proposal-deck-30-markers.html")

RUN_RESULT = {
    "status": "ok",
    "prompt": "(design prompt body)",
    "applied_preferences": [],
    "number": 3,
    "render_fidelity": {"ok": True, "missing_values": {}},
    "layout": {"ok": True, "checked": True, "skipped": "", "slides": 6,
               "findings": [], "summary": []},
}

# The three headings, in the order the card renders them: the real gaps first.
HEADINGS = ("Supply missing values (5)",
            "Values we expect you to supply (14)",
            "Commercial terms, entered by hand (11)")


def _studio(tmp_path, deck_html):
    """The app with a stubbed pipeline that "renders" ``deck_html`` into tmp_path."""
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
    # The per-deck-type expectations are read from the templates once and kept;
    # cleared so a test never inherits another's lookup.
    ui_app._FILL_EXPECTATIONS.clear()
    return ui_app, ui_app.app.test_client(), deck_path


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def studio(tmp_path):
    return _studio(tmp_path, _read(DECK_FIXTURE))


def _run(client, deck_type="proposal"):
    return run_deck(client, data={
        "deck_type": deck_type, "company": "Any Client", "project": "Any Project",
        "packet": "proposal-data-packet-EXAMPLE.md",
    }).get_data(as_text=True)


def _targets_for(deck_path, field):
    """The markers the card would post for one field, as the card posts them."""
    return [m for m in list_missing_markers(_read(deck_path))
            if m["field"] == field]


def _supply(client, deck_path, field, value, targets):
    """Post what the card's form posts: one value and one hidden group per place.

    The repeated `slide` / `source` / `occurrence` / `occurrences` fields ride as
    lists, which is how the browser sends several inputs of one name and what
    `request.form.getlist` reads back. The position is what lets a repeated marker
    be aimed at, and the count is what makes a page gone stale refuse rather than
    land on whatever now sits at that position.
    """
    return client.post("/supply-missing", data={
        "path": deck_path, "field": field, "replacement": value,
        "deck_type": "proposal", "company": "Any Client",
        "project": "Any Project", "packet": "proposal-data-packet-EXAMPLE.md",
        "slide": [str(t["slide"]) for t in targets],
        "source": [t["source"] for t in targets],
        "occurrence": [str(t["occurrence"]) for t in targets],
        "occurrences": [str(t["occurrences"]) for t in targets],
    }).get_data(as_text=True)


# ------------------------------- the three cards ----------------------------

def test_the_card_splits_into_three_headings_counting_five_fourteen_eleven(studio):
    """The regression. One heading reading "Supply missing values (30)" told a
    reviewer that thirty things were wrong with the deck when five were."""
    _ui, client, _deck = studio
    html = _run(client)
    for heading in HEADINGS:
        assert heading in html, heading
    assert "Supply missing values (30)" not in html


def test_the_real_gaps_are_listed_before_the_deliberate_ones(studio):
    _ui, client, _deck = studio
    html = _run(client)
    positions = [html.index(heading) for heading in HEADINGS]
    assert positions == sorted(positions), (
        "the five real gaps are the list; the other two groups follow")


def test_the_commercial_group_says_the_markers_are_deliberate(studio):
    _ui, client, _deck = studio
    html = _run(client)
    assert "AWAITING COMMERCIAL TERMS INPUT" in html
    assert "Deliberately not sourced" in html


def test_every_row_offers_an_input(studio):
    """Every row, with no exceptions left. Eight of the 25 used to carry an input
    and the other 17 carried an explanation of why they could not, which is a card
    refusing most of its own job. Each row now posts a position, so all 25 are
    fillable; the 26th `replacement` on the page belongs to the attach-a-note card.
    """
    _ui, client, deck = studio
    html = _run(client)
    for field in ("subtitle", "deck_date", "commercial_rows", "terms_footnote"):
        assert f"<code>{field}</code>" in html, field
    # 25, not 26: the "Attach a note to a slide" card carried a `replacement`
    # field of its own and was removed on 2026-09-20.
    assert html.count('name="replacement"') == 25, html.count('name="replacement"')


def test_no_row_sends_the_reviewer_to_the_attach_a_note_card(studio):
    """The old fallback, gone. A repeated marker was the one thing the card could
    not fill, and its advice was to paste surrounding HTML into the exact-text edit
    by hand. The card itself still exists for a reviewer-named swap; nothing is
    routed to it as a workaround any more."""
    _ui, client, _deck = studio
    html = _run(client)
    assert "This marker text repeats on slide" not in html
    assert "paste in enough surrounding text" not in html


def test_a_repeated_marker_says_which_one_it_is(studio):
    """Four `[MISSING: week]` rows on one slide look identical in the card, so each
    says its position. The deck is on the same page and the rows are in document
    order, which is how a reviewer maps a row to the slot they can see."""
    _ui, client, _deck = studio
    html = _run(client)
    for nth in (1, 2, 3, 4):
        assert f"{nth} of 4 on slide" in html, nth
    assert 'name="occurrence" value="3"' in html


def test_every_target_posts_its_position(studio):
    """The position rides on the form next to the (slide, source) pair. Without it
    the route falls back to the unique-match rule and a repeated marker is refused,
    so a row that posts no occurrence is a row that cannot land."""
    _ui, client, _deck = studio
    html = _run(client)
    assert html.count('name="occurrence"') == html.count('name="source" value=')


def test_the_deck_wide_date_is_offered_once_and_says_how_far_it_reaches(studio):
    _ui, client, _deck = studio
    html = _run(client)
    assert "one value, 6 places" in html
    assert html.count("<code>deck_date</code>") == 1, (
        "six footers carrying one date is one input, not six")


# ------------------------------ the fan-out, live ---------------------------

def test_supplying_the_deck_wide_date_writes_all_six_footers_in_one_revision(studio):
    _ui, client, deck = studio
    targets = _targets_for(deck, "deck_date")
    assert len(targets) == 6

    html = _supply(client, deck, "deck_date", "AUGUST 20 2026", targets)

    assert "Supplied `deck_date` to 6 places on the deck" in html, html[:600]
    revision = os.path.join(os.path.dirname(deck), "output-3-r1.html")
    assert os.path.isfile(revision)
    written = _read(revision)
    assert written.count("AUGUST 20 2026") == 6
    assert "[MISSING: deck_date]" not in written
    log = load_edit_log(deck)
    assert len(log) == 6 and len({e["revision"] for e in log}) == 1


def test_a_fan_out_that_cannot_reach_every_slide_reports_failure_not_success(studio):
    """Five footers written and one refused must not come back as "applied"."""
    _ui, client, deck = studio
    before = deck + ".before"
    shutil.copyfile(deck, before)
    targets = _targets_for(deck, "deck_date")
    targets[3] = {"slide": 4, "source": "[MISSING: not_on_this_slide]",
                  "occurrence": 1, "occurrences": 1}

    html = _supply(client, deck, "deck_date", "AUGUST 20 2026", targets)

    assert "`deck_date` was not written" in html, html[:600]
    assert "slide 4" in html
    assert "nothing was written" in html
    assert not os.path.isfile(os.path.join(os.path.dirname(deck), "output-3-r1.html"))
    assert filecmp.cmp(deck, before, shallow=False), "the deck must be untouched"
    assert load_edit_log(deck) == []


def test_supplying_a_single_marker_names_its_slide(studio):
    _ui, client, deck = studio
    targets = _targets_for(deck, "subtitle")
    assert len(targets) == 1
    html = _supply(client, deck, "subtitle", "One line on what is being stood up.",
                   targets)
    assert "Supplied `subtitle` to slide 1" in html, html[:600]


# -------------- filling a marker whose text repeats, end to end -------------

def test_four_identical_week_markers_fill_in_one_submission(studio):
    """The change this card exists for. Four `[MISSING: week]` on slide 6 are four
    different weeks wearing one string; every one of them used to be refused as
    ambiguous, so the card offered no input at all and named the attach-a-note card
    instead. Posted together they land on their own rows in one revision, which is
    the shape the commercial-terms form will need.

    One listing, spent once, which is the only way a listing may be spent: the
    route hands the whole batch to `apply_edits_and_save`, and it is that call that
    reconciles four positions against a document its own fills are changing."""
    _ui, client, deck = studio
    markers = _targets_for(deck, "week")
    assert len(markers) == 4
    assert [m["occurrence"] for m in markers] == [1, 2, 3, 4]

    # One value per marker is not what this route takes — it writes ONE value to
    # every target it is given — so the four rows get one week between them. What
    # is being pinned is that four positions on one slide all resolve.
    html = _supply(client, deck, "week", "WK 4", markers)

    assert "was not written" not in html, html[:600]
    revision = os.path.join(os.path.dirname(deck), "output-3-r1.html")
    assert os.path.isfile(revision)
    written = _read(revision)
    assert "[MISSING: week]" not in written
    assert written.count("WK 4") == 4
    log = load_edit_log(deck)
    assert [e["occurrence"] for e in log] == [1, 2, 3, 4], log
    assert len({e["revision"] for e in log}) == 1, "one reviewer action, one revision"


def test_a_reviewer_re_reading_the_card_between_fills_never_sees_a_stale_position(
        studio):
    """The realistic loop, and the reason the reviewer is not exposed to the
    ordering rule. Fill the first marker the card offers, reload, fill the first
    one again, and so on: each reload renumbers, so front-to-back works."""
    _ui, client, deck = studio
    current = deck
    for nth in (1, 2, 3, 4):
        marker = _targets_for(current, "week")[0]
        assert marker["occurrence"] == 1, marker
        assert marker["occurrences"] == 5 - nth, marker
        html = _supply(client, current, "week", f"WK {nth}", [marker])
        assert "was not written" not in html, html[:600]
        current = os.path.join(os.path.dirname(deck), f"output-3-r{nth}.html")

    assert "[MISSING: week]" not in _read(current)


def test_a_position_posted_from_a_page_the_deck_has_moved_past_is_refused(studio):
    """Two tabs, or a back button. The page counted four; two have since been
    filled, so its "#2 of 4" now names a different row. Writing the value there
    would put a wrong week on a client deck with nothing to show it happened, so
    the route refuses and says to reload."""
    _ui, client, deck = studio
    stale = _targets_for(deck, "week")[1]
    assert (stale["occurrence"], stale["occurrences"]) == (2, 4)

    current = deck
    for nth in (1, 2):
        marker = _targets_for(current, "week")[0]
        _supply(client, current, "week", f"WK {nth}", [marker])
        current = os.path.join(os.path.dirname(deck), f"output-3-r{nth}.html")

    html = _supply(client, current, "week", "WK 9", [stale])

    assert "`week` was not written" in html, html[:600]
    assert "deck has changed" in html
    assert "reload the deck" in html
    assert "WK 9" not in _read(current)
    assert not os.path.isfile(os.path.join(os.path.dirname(deck), "output-3-r3.html"))


def test_a_listing_is_single_use_even_spent_back_to_front(studio):
    """How strict the guard is, stated on purpose. Walking a stale listing backwards
    keeps every POSITION valid, but each fill changes the TOTAL, so the second post
    from one listing is refused anyway. Strictness in the right direction: a listing
    describes a document, that document is gone after the first fill, and only a
    single call can reconcile several positions against its own changes."""
    _ui, client, deck = studio
    markers = _targets_for(deck, "week")

    html = _supply(client, deck, "week", "WK 4", [markers[3]])
    assert "was not written" not in html, html[:600]
    revision = os.path.join(os.path.dirname(deck), "output-3-r1.html")

    html = _supply(client, revision, "week", "WK 3", [markers[2]])
    assert "deck has changed" in html
    assert not os.path.isfile(os.path.join(os.path.dirname(deck), "output-3-r2.html"))


def test_a_supplied_value_keeps_the_run_context_and_the_checklist(studio):
    """The 2026-07-28 defect, on the new route: the form forwards the packet, so
    filling a value does not drop the flagged-claims card off the page."""
    _ui, client, deck = studio
    _run(client)
    html = _supply(client, deck, "subtitle", "A subtitle.",
                   _targets_for(deck, "subtitle"))
    assert 'name="packet"' in html


def test_a_fill_with_no_target_is_refused_rather_than_written_blind(studio):
    _ui, client, deck = studio
    html = client.post("/supply-missing", data={
        "path": deck, "field": "subtitle", "replacement": "x",
        "deck_type": "proposal",
    }).get_data(as_text=True)
    assert "the form named no place to write this value" in html
    assert not os.path.isfile(os.path.join(os.path.dirname(deck), "output-3-r1.html"))


def test_the_packet_is_never_written_by_a_supplied_value(studio):
    """A supplied value is a logged reviewer edit on the artifact and nothing more,
    which is what keeps the never-fabricate rule intact."""
    _ui, client, deck = studio
    packet = os.path.join(_ROOT, "proposal-data-packet-EXAMPLE.md")
    copy = os.path.join(os.path.dirname(deck), "packet.cmp")
    shutil.copyfile(packet, copy)
    _run(client)
    _supply(client, deck, "deck_date", "AUGUST 20 2026",
            _targets_for(deck, "deck_date"))
    assert filecmp.cmp(packet, copy, shallow=False), (
        "the committed packet must come out of a fill byte-identical")


# --------------------- a deck whose type the studio cannot tell -------------

def test_with_no_deck_type_a_contested_name_falls_back_to_a_real_gap(tmp_path):
    """`week` is a reviewer commitment under a proposal's next steps and a sourced
    schedule fact under a status deck's slip markers. Asked about a deck of no
    known type, the studio puts it in the group a reviewer works through rather
    than filing it away as deliberate."""
    import app as ui_app
    unknown = ui_app._fill_expectations_for("")
    assert unknown["week"]["fill"] == "source"
    assert unknown["deck_date"]["fill"] == "reviewer", (
        "a name only one template declares is still answered")
    proposal = ui_app._fill_expectations_for("proposal")
    assert proposal["week"]["fill"] == "reviewer"


def test_a_deck_with_no_markers_renders_no_supply_cards(tmp_path):
    complete = ('<!doctype html><html><body>'
                '<section class="slide" data-slide="1"><h1>Cover</h1></section>'
                "</body></html>")
    _ui, client, _deck = _studio(tmp_path, complete)
    html = _run(client)
    for heading in ("Supply missing values", "Values we expect you to supply",
                    "Commercial terms, entered by hand"):
        assert heading not in html, heading

def test_the_forms_own_hidden_values_are_what_the_route_can_apply(studio):
    """The browser path end to end. A target's `source` is markup
    (`<span class="flag">[MISSING: ...]</span>`), so it is HTML-escaped into the
    hidden input and unescaped again on the way back. Posting the values SCRAPED
    from the rendered form, rather than values read off the deck, is what proves
    that round trip -- an escaping change here would otherwise show up as a fill
    that silently stops matching.
    """
    import html as html_mod
    import re

    _ui, client, deck = studio
    page = _run(client)
    form = re.search(
        r'<form[^>]*action="/supply-missing"[^>]*>(.*?)</form>', page, re.S)
    assert form, "the deck-wide date must be offered as a form"
    # The first supply-missing form on the page is the one real gap on slide 1.
    fields = re.findall(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"',
                        form.group(1))
    posted = {}
    for name, value in fields:
        posted.setdefault(name, []).append(html_mod.unescape(value))
    assert posted["source"], posted
    posted["replacement"] = ["One line on what is being stood up."]

    result = client.post("/supply-missing", data={
        key: values if len(values) > 1 else values[0]
        for key, values in posted.items()
    }).get_data(as_text=True)

    assert "was not written" not in result, result[:600]
    assert "Supplied" in result, result[:600]
    revision = os.path.join(os.path.dirname(deck), "output-3-r1.html")
    assert "One line on what is being stood up." in _read(revision)
