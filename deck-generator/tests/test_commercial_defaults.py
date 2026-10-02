"""QofAI's standing commercial language, and the one click that writes it.

Two regions of a proposal's Commercial Terms slide say the same thing on every
engagement: the payment mechanism under HOW PAYMENT WORKS, and the risk-reversal
clause in the blue box above the value map. A reviewer typing those on every deck
is typing QofAI's own boilerplate from memory. Antonio, 2026-08-20: "I want a
default payment, 'How Payment Works,' to be an option on the deck ... after the
deck is generated, someone can press 'Default, How Payment Works,' and then it
just appears on the deck", and separately: "there should always be 'if margins
don't improve above your locked baseline, QofAI earns nothing' ... that should be
part of the default how payment works part, but it is in the blue box above, like
in FBK deck."

What these pin, in order of what would hurt most if it broke:

  1. NO DEAL TERMS IN THE DEFAULT. Mechanism only, asked and answered. A share, a
     cap, a term or a dollar figure reaching a deck from a default is last deal's
     economics wearing this deal's letterhead, and it arrives looking considered.
  2. NO CLIENT DETAIL. The text goes on a deck for any client.
  3. THREE COLUMNS, THREE STEPS. `.mech-steps` is a three-column grid and the
     render leaves ONE child holding the marker, so a text swap would stack all
     three steps in the left third. This is why the write is structural.
  4. BOTH OR NEITHER. The blue box and the payment block are one statement.

Run with: python3 -m pytest tests/test_commercial_defaults.py
"""

import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ui"))

from commercial_defaults import (  # noqa: E402
    DEFAULTABLE,
    DEFAULTS_PATH,
    DefaultsUnavailable,
    load_defaults,
)
from html_edit_layer import (  # noqa: E402
    EditNotApplicable,
    list_missing_markers,
    load_edit_log,
    read_commercial_regions,
    set_commercial_defaults,
    set_commercial_defaults_and_save,
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
def defaults():
    return load_defaults()


@pytest.fixture
def deck():
    return _read(DECK_FIXTURE)


# --------------------------- what may be defaulted --------------------------

def test_the_file_is_the_content_and_the_code_only_writes_it():
    """The wording lives in a file so it is edited and reviewed as text. A literal
    in a module is content nobody reads as content."""
    assert os.path.isfile(DEFAULTS_PATH)
    raw = json.loads(_read(DEFAULTS_PATH))
    assert raw["downside_protection"]
    assert len(raw["payment_mechanics"]) == 3


def test_only_the_two_standing_regions_are_defaultable():
    """Named in code, so a role added to the JSON does not become defaultable by
    accident. The other sensitive roles on the slide stay reviewer input."""
    assert DEFAULTABLE == ("downside_protection", "payment_mechanics")


def test_the_default_carries_no_figure_share_cap_or_term(defaults):
    """The load-bearing rule. FBK's own third step read "the year's share (25% /
    15% / 5%) ... until the 2.5x cap or the 3-year term", and every number in it is a
    term of one deal. Antonio, asked directly: mechanism only."""
    body = " ".join(
        [defaults["downside_protection"]]
        + [f"{s['lead']} {s['rest']}" for s in defaults["payment_mechanics"]])
    assert not re.search(r"\d+\s*%", body), body
    assert not re.search(r"\$\s*\d", body), body
    assert not re.search(r"\d+\s*[x×]\b", body), body
    assert not re.search(r"\b\d+[- ]year\b", body), body
    # No bare figure at all, which is the general form of the three checks above.
    assert not re.search(r"\d", body), body


def test_the_default_names_no_client(defaults):
    """It goes onto a deck for any client. The FBK packet this wording derives from
    said "project & fleet EBITDA margin"; the fleet is FBK's."""
    body = " ".join(
        [defaults["downside_protection"]]
        + [f"{s['lead']} {s['rest']}" for s in defaults["payment_mechanics"]])
    for word in ("FBK", "Fabrikam", "fleet", "vessel", "marine"):
        assert word.lower() not in body.lower(), word
    assert "the client" in body.lower(), "it should say the client, generically"


def test_the_mechanism_is_the_three_steps_antonio_named(defaults):
    """Measured monthly, converted to EBITDA, share paid that month."""
    leads = [step["lead"].lower() for step in defaults["payment_mechanics"]]
    assert "measured monthly" in leads[0]
    assert "converted to ebitda" in leads[1]
    assert "share paid that month" in leads[2]


def test_the_blue_box_clause_is_the_one_that_was_asked_for(defaults):
    text = defaults["downside_protection"].lower()
    assert "locked baseline" in text
    assert "earns nothing" in text


def test_a_missing_or_broken_file_refuses_rather_than_half_defaulting(tmp_path):
    """Half of this text is not a lesser version of it: a blue box promising the
    client that QofAI earns nothing, above a payment block that never says how
    improvement is measured, is worse than the two markers it replaced."""
    with pytest.raises(DefaultsUnavailable):
        load_defaults(str(tmp_path / "not-there.json"))

    partial = tmp_path / "partial.json"
    partial.write_text(json.dumps({"downside_protection": "x"}), encoding="utf-8")
    with pytest.raises(DefaultsUnavailable):
        load_defaults(str(partial))

    unbolded = tmp_path / "unbolded.json"
    unbolded.write_text(
        json.dumps({"downside_protection": "x",
                    "payment_mechanics": [{"lead": "a"}]}), encoding="utf-8")
    with pytest.raises(DefaultsUnavailable):
        load_defaults(str(unbolded))


# ---------------------------- writing it onto a deck ------------------------

def test_the_regions_start_out_holding_their_markers(deck):
    assert read_commercial_regions(deck) == {
        "downside_protection": "[MISSING: downside_protection]",
        "payment_mechanics": "[MISSING: payment_mechanics]"}


def test_a_deck_with_no_commercial_slide_offers_nothing():
    """How a status deck answers, and why the studio reads the DECK rather than a
    deck-type name: the offer appears exactly where there is somewhere to write."""
    assert read_commercial_regions(
        '<html><body><section class="slide"><p>x</p></section></body></html>'
    ) is None


def test_three_steps_become_three_children_of_the_three_column_grid(deck, defaults):
    """Why this is structural and not a text swap. `.mech-steps` is
    `grid-template-columns:repeat(3,1fr)` and the render leaves ONE `.st` child
    holding the marker, so replacing that child's text would stack all three steps
    in the left third and leave two columns empty."""
    out, _applied = set_commercial_defaults(
        deck, downside=defaults["downside_protection"],
        payment_steps=defaults["payment_mechanics"])
    grid = re.search(r'<div class="mech-steps">(.*?)</div>\s*</div>', out, re.DOTALL)
    assert grid, "the grid must still be there"
    assert grid.group(1).count('<div class="st">') == 3, grid.group(1)
    for step in defaults["payment_mechanics"]:
        assert f'<b>{step["lead"]}</b>' in out, step["lead"]


def test_the_clause_lands_in_the_blue_box_above_the_value_map(deck, defaults):
    """"it is in the blue box above, like in FBK deck" — `.downside`, which sits
    between the terms strip and the value map."""
    out, _applied = set_commercial_defaults(
        deck, downside=defaults["downside_protection"],
        payment_steps=defaults["payment_mechanics"])
    box = re.search(r'<div class="downside">(.*?)</div>', out, re.DOTALL)
    assert box.group(1) == (
        "If margins don&#x27;t improve above your locked baseline, QofAI earns "
        "nothing.").replace("&#x27;", "'"), box.group(1)
    assert out.index('class="downside"') < out.index('class="valuemap"')


def test_both_markers_are_gone_and_no_other_marker_moved(deck, defaults):
    before = {(m["slide"], m["field"]) for m in list_missing_markers(deck)}
    out, _applied = set_commercial_defaults(
        deck, downside=defaults["downside_protection"],
        payment_steps=defaults["payment_mechanics"])
    after = {(m["slide"], m["field"]) for m in list_missing_markers(out)}
    assert before - after == {(5, "downside_protection"), (5, "payment_mechanics")}
    assert not after - before, "no marker may appear that was not there"


def test_a_deck_missing_one_region_is_refused_whole(deck, defaults):
    """Both or neither. The blue box and the payment block are one statement, and
    the half-written version arrives looking deliberate."""
    half = deck.replace('<div class="mech-steps">', '<div class="gone">')
    try:
        set_commercial_defaults(half, downside=defaults["downside_protection"],
                                payment_steps=defaults["payment_mechanics"])
    except EditNotApplicable as exc:
        assert "payment_mechanics" in str(exc)
    else:
        assert False, "a deck missing a region must be refused"


def test_markup_in_the_file_is_refused_not_escaped_through(deck):
    """The defaults file is content, and the `<b>` around a lead phrase is the
    writer's own. Markup in the content would escape safely — no tag reaches the
    document either way — but it would then PRINT as `<b>x</b>` on a client deck,
    so the same guard `apply_text_edit` uses refuses it instead. Loud beats
    literal."""
    try:
        set_commercial_defaults(deck, downside="ok",
                                payment_steps=[{"lead": "<b>x</b>", "rest": "y"}])
    except EditNotApplicable as exc:
        assert "print" in str(exc) and "as text" in str(exc), str(exc)
    else:
        assert False, "markup in the defaults file must be refused"


def test_an_ampersand_in_the_wording_is_escaped_and_reads_as_itself(deck):
    """Escaping is still what happens to ordinary punctuation, so the wording can
    use `&` without either breaking the document or showing an entity."""
    out, _applied = set_commercial_defaults(
        deck, downside="Margins & baselines.",
        payment_steps=[{"lead": "Measured.", "rest": "Costs & revenue."}])
    assert "Margins &amp; baselines." in out
    assert "Costs &amp; revenue." in out
    assert read_commercial_regions(out)["downside_protection"] == (
        "Margins & baselines.")


# ------------------------- the revision chain, for real ---------------------

def _deck_file(tmp_path):
    path = os.path.join(str(tmp_path), "output-3.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(_read(DECK_FIXTURE))
    return path


def test_one_click_is_one_revision_and_two_log_entries(tmp_path, defaults):
    """One press of the button, one press of undo."""
    path = _deck_file(tmp_path)
    result = set_commercial_defaults_and_save(
        path, downside=defaults["downside_protection"],
        payment_steps=defaults["payment_mechanics"], author="Antonio",
        now="2026-08-20T00:00:00+00:00")

    assert result["revision_name"] == "output-3-r1.html"
    assert os.path.isfile(path), "the original must survive"
    assert "[MISSING: payment_mechanics]" in _read(path), "original untouched"

    log = load_edit_log(path)
    assert len(log) == 2
    assert len({e["revision"] for e in log}) == 1
    assert [e["before"] for e in log] == ["[MISSING: downside_protection]",
                                          "[MISSING: payment_mechanics]"]
    assert all(e["kind"] == "content" for e in log), "never promotable"
    assert all(e["author"] == "Antonio" for e in log)


def test_undo_reverses_both_regions_together(tmp_path, defaults):
    path = _deck_file(tmp_path)
    set_commercial_defaults_and_save(
        path, downside=defaults["downside_protection"],
        payment_steps=defaults["payment_mechanics"])
    undo_last_edit(path)
    assert read_commercial_regions(_read(path)) == {
        "downside_protection": "[MISSING: downside_protection]",
        "payment_mechanics": "[MISSING: payment_mechanics]"}
    assert load_edit_log(path) == []


def test_writing_it_twice_is_idempotent(tmp_path, defaults):
    """A second click on a deck that already carries the default changes nothing on
    the slide, so a double-click is not a defect."""
    path = _deck_file(tmp_path)
    first = set_commercial_defaults_and_save(
        path, downside=defaults["downside_protection"],
        payment_steps=defaults["payment_mechanics"])
    second = set_commercial_defaults_and_save(
        path, downside=defaults["downside_protection"],
        payment_steps=defaults["payment_mechanics"])
    assert _read(first["revision_path"]) == _read(second["revision_path"])


# ------------------------- the button, through Flask ------------------------
#
# The lesson E11 Stage 2g taught and this project keeps applying: every seam green
# and the feature switched off in the one place it is reached from. These drive the
# route a reviewer's click actually posts to.

pytest.importorskip("flask")


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
    ui_app._FILL_EXPECTATIONS.clear()
    return ui_app, ui_app.app.test_client(), deck_path


RUN_RESULT = {
    "status": "ok",
    "prompt": "(design prompt body)",
    "applied_preferences": [],
    "number": 3,
    "render_fidelity": {"ok": True, "missing_values": {}},
    "layout": {"ok": True, "checked": True, "skipped": "", "slides": 6,
               "findings": [], "summary": []},
}


@pytest.fixture
def studio(tmp_path):
    return _studio(tmp_path, _read(DECK_FIXTURE))


def _run(client, deck_type="proposal"):
    return run_deck(client, data={
        "deck_type": deck_type, "company": "Any Client", "project": "Any Project",
        "packet": "proposal-data-packet-EXAMPLE.md",
    }).get_data(as_text=True)


def _press(client, deck_path):
    return client.post("/commercial-defaults", data={
        "path": deck_path, "author": "Antonio", "deck_type": "proposal",
        "company": "Any Client", "project": "Any Project",
        "packet": "proposal-data-packet-EXAMPLE.md",
    }).get_data(as_text=True)


def test_the_card_is_retired_even_on_a_deck_that_carries_both_regions(studio):
    """RETIRED 2026-09-23 (Antonio: the old commercial editors "were not
    relevant anymore"). This studio's deck carries both regions, which is
    exactly when the card used to appear, so it is the case that proves it no
    longer does. The route is still tested below; only the card is gone."""
    _ui, client, _deck = studio
    html = _run(client)
    assert "How payment works" not in html
    assert "Write the default" not in html
    assert 'action="/commercial-defaults"' not in html


def test_the_card_shows_what_each_region_reads_as_now(studio):
    _ui, client, _deck = studio
    html = _run(client)
    assert "[MISSING: downside_protection]" in html
    assert "[MISSING: payment_mechanics]" in html


def test_no_overwrite_warning_when_both_regions_are_still_markers(studio):
    _ui, client, _deck = studio
    assert "so this\n  replaces it" not in _run(client)


def test_pressing_it_writes_both_regions_and_reports_where(studio):
    _ui, client, deck = studio
    _run(client)
    html = _press(client, deck)

    assert "Wrote the default" in html, html[:800]
    assert "slide 5" in html
    revision = os.path.join(os.path.dirname(deck), "output-3-r1.html")
    assert os.path.isfile(revision)
    written = _read(revision)
    assert written.count('<div class="st">') == 3
    assert "[MISSING: payment_mechanics]" not in written
    assert "[MISSING: downside_protection]" not in written
    # Everything else the reviewer still has to supply is untouched.
    assert "[MISSING: commercial_rows]" in written
    assert "[MISSING: qofai_comp]" in written


def test_the_button_is_not_offered_on_a_deck_with_no_commercial_slide(tmp_path):
    """A status deck renders no Commercial Terms slide, so there is nowhere to
    write and nothing is offered. Read off the deck, not off the deck type."""
    _ui, client, _deck = _studio(
        tmp_path,
        '<!doctype html><html><body><section class="slide" data-slide="1">'
        "<h1>Check-in</h1></section></body></html>")
    html = _run(client, deck_type="status")
    assert "How payment works" not in html


def test_a_deck_missing_one_region_refuses_and_writes_nothing(tmp_path):
    """Both or neither, reported to the reviewer rather than half-applied."""
    broken = _read(DECK_FIXTURE).replace('<div class="mech-steps">',
                                         '<div class="gone">')
    _ui, client, deck = _studio(tmp_path, broken)
    _run(client)
    html = _press(client, deck)
    assert "was not written" in html, html[:800]
    assert not os.path.isfile(os.path.join(os.path.dirname(deck),
                                           "output-3-r1.html"))


def test_a_press_still_writes_but_the_page_offers_no_second_one(studio):
    """The overwrite warning went with the card on 2026-09-23. A post to the
    route still writes, and the page it returns offers no card to press again."""
    _ui, client, deck = studio
    _run(client)
    html = _press(client, deck)
    assert "Wrote the default" in html, html[:800]
    assert "already carries text" not in html
    assert "How payment works" not in html
