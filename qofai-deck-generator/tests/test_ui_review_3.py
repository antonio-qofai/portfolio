"""Antonio's third UI review, pinned on the rendered page.

WHY THIS FILE EXISTS. A green suite has hidden an inert feature on this build
five separate times, and every one of them was found by LOOKING at the artifact
rather than by running the tests. The bullet switches were the fourth: the whole
feature hung on a flag that was false on every live run, 2257 tests passed, and
nobody could press anything. The empty Project column was the fifth.

So each item Antonio asked for on 2026-09-21 is asserted here against the HTML
the studio actually serves, not against the function that is supposed to produce
it. Source:
`conversations/2026-09-21-meeting-fathom-deck-generator-review-3.md`.

WHITESPACE IS NORMALISED before anything is counted. Template copy wraps across
source lines, so the rendered HTML carries a newline and several spaces in the
middle of a sentence, and an exact-string search reports a line missing while it
is plainly on screen. That has now cost two sessions; `_flat` is the fix.

Run with: python3 -m pytest tests/test_ui_review_3.py
"""

import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ui"))

from test_ui_attachments import (OK_RESULT, attachment, live_run,  # noqa: E402,F401
                                 studio)
from test_ui_review_surface import _copy_packet, _run, _studio  # noqa: E402

try:
    import flask  # noqa: F401
    _HAVE_FLASK = True
except ImportError:
    _HAVE_FLASK = False


def _flat(html):
    """One space for every run of whitespace. See the module docstring."""
    return re.sub(r"\s+", " ", html)


# A panel with room for one of its two bullets, which is what gives the page one
# switch in each position without needing a real render.
SELECTION = {
    "panels": [
        {"panel": "today", "label": "TODAY", "role": "today_pain_bullets",
         "shown": 1, "of": 2,
         "kept": [{"text": "Captains handwrite the daily report on paper",
                   "source_position": 1}],
         "dropped": [{"text": "Fuel entry is optional across live projects",
                      "source_position": 2}],
         "ranked": True, "reordered": False, "room_px": 96.4,
         "overflowed": False, "note": "1 of 2 shown"},
    ],
    "notes": [], "trimmed": True, "ranked": True,
}

# The same panel with almost no room, so the fitter refuses the bullet that is
# not already on the slide and the template takes its `b.blocked` branch.
BLOCKED_SELECTION = {
    "panels": [
        dict(SELECTION["panels"][0], room_px=14.0),
    ],
    "notes": [], "trimmed": True, "ranked": True,
}

SHA = "b" * 64
ATTACHMENTS = [{"filename": "Client_PRD.docx", "kind": "docx",
                "size_bytes": 4096, "sha256": SHA, "text": "the PRD text"}]


def _prd_run(studio, selection=None):
    client, captured = studio
    captured["result"] = dict(OK_RESULT, attachments=ATTACHMENTS,
                              bullet_selection=selection or SELECTION)
    return _flat(live_run(
        client, uploads=[attachment("PRD body", "Client_PRD.docx")]
    ).get_data(as_text=True))


# ---------------------------------------------------------------- the switches

def test_the_bullet_control_is_one_switch_and_not_two_buttons(studio):
    """Antonio: "the toggle bullets should be like a toggle, literally like a
    switch ... I don't want an on/off and off/on button.\""""
    html = _prd_run(studio)
    assert html.count('class="inline bullet-switch-form"') == 2, (
        "still one form per bullet"
    )
    assert html.count('<button class="switch') == 2, (
        "each bullet gets exactly one switch"
    )
    assert ">on</button>" not in html and ">off</button>" not in html, (
        "the two labelled buttons are what the switch replaced"
    )


def test_the_switch_is_coloured_when_the_bullet_is_on_the_slide(studio):
    """Antonio: "it makes it easier to see the ones that are already on because
    they're just toggled on, and they have the color."

    `is-on` is the only class carrying the accent, so this asserts the colour by
    asserting the class the colour is attached to.
    """
    html = _prd_run(studio)
    assert html.count('class="switch is-on"') == 1, "the kept bullet reads on"
    assert html.count('class="switch is-off"') == 1, "the dropped bullet reads off"


def test_a_switch_posts_the_opposite_of_what_it_shows(studio):
    """What a switch MEANS. The on one has to turn the bullet off and the off one
    has to turn it on; a switch that posts its own state does nothing twice."""
    html = _prd_run(studio)
    on = re.search(r'<button class="switch is-on"[^>]*>', html).group(0)
    off = re.search(r'<button class="switch is-off"[^>]*>', html).group(0)
    assert 'value="off"' in on, "the lit switch must switch the bullet OFF"
    assert 'value="on"' in off, "the dark switch must switch the bullet ON"
    assert 'aria-checked="true"' in on and 'aria-checked="false"' in off


def test_the_switch_still_posts_without_javascript(studio):
    """Each switch is its own form post, so it must be a submit button and not a
    checkbox that needs a script to send anything."""
    html = _prd_run(studio)
    for m in re.finditer(r'<button class="switch[^>]*>', html):
        assert 'type="submit"' in m.group(0), m.group(0)
    assert '<input type="checkbox" class="switch' not in html


def test_a_blocked_bullet_keeps_a_dead_switch_and_its_reason(studio):
    """A bullet that cannot fit the panel at the smallest readable size must not
    lose its control: a missing one reads as a bug, a dead one reads as a
    reason, and the reason is the title."""
    html = _prd_run(studio, BLOCKED_SELECTION)
    dead = re.search(r'<button class="switch is-off"[^>]*disabled[^>]*>', html)
    assert dead, "the blocked bullet must still render a switch, disabled"
    assert "do not fit this panel" in dead.group(0), (
        "the explanation Antonio's reviewers get is in the title attribute"
    )


def test_the_reset_is_absent_until_a_switch_has_been_set(studio):
    """`auto` is the absence of a decision, not a third setting. Nothing has
    been switched on this run, so there is nothing to hand back to the fitter."""
    html = _prd_run(studio)
    assert '<button class="ghost switch-reset"' not in html


# ------------------------------------------------------- what he asked deleted

def test_the_render_again_blurb_is_gone(studio):
    """Antonio: "the text after it, 'Same Deck Next Revision, Your Edits Are
    Replayed', we can delete that text.\""""
    assert "Same deck, next revision" not in _prd_run(studio)


def test_the_applied_preferences_card_is_gone():
    """Antonio: "Remove the section on applied styling preferences.\""""
    if not _HAVE_FLASK:
        return
    ui_app, client, deck_path = _studio(tempfile.mkdtemp(prefix="review3-"))
    packet = _copy_packet()
    try:
        assert "Applied standing preferences" not in _flat(_run(client, packet))
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(os.path.dirname(deck_path), ignore_errors=True)


def test_the_committed_store_ships_no_standing_preference():
    """Antonio: "let's just remove that styling preference altogether because
    it's really old and doesn't apply, because the code already removes them.
    Let's remove that from every iteration of the deck generator from now on."

    He is right that the code already removes them: `html_edit_layer.apply_edit`
    HTML-ESCAPES every replacement, so a tag in one is inert rather than
    refused, and `display_text_guard` checks the span an edit writes. The
    preference was asking a model not to do something the layer cannot let it
    do.

    `standing-preferences.json` is committed AND is what `seed_store_if_absent`
    copies onto a hosted volume on first run, so an entry here is an entry in
    every future deployment. That is the "every iteration" he meant.
    """
    from preference_store import DEFAULT_STORE_PATH, load_store
    notes = [p.get("note", "") for p in load_store(DEFAULT_STORE_PATH)["preferences"]]
    assert not [n for n in notes if "html tag" in n.lower()], (
        f"the html-tags preference is back in the committed store: {notes}"
    )


# -------------------------------------------------------------- the edit card

def test_the_edit_card_loses_its_redundant_label_and_its_dead_space():
    """Antonio: "let's just remove the 'What should change?' part completely ...
    There's so much empty space below 'Author optional' ... so much space above
    'Author optional' as well.\""""
    if not _HAVE_FLASK:
        return
    ui_app, client, deck_path = _studio(tempfile.mkdtemp(prefix="review3-"))
    packet = _copy_packet()
    try:
        html = _flat(_run(client, packet))
        assert "What should change" not in html
        assert '<div class="edit-submit-row">' in html, (
            "author and submit share one row, which is what closed the gap"
        )
        assert 'name="instruction"' in html and 'name="author"' in html, (
            "both fields still post"
        )
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(os.path.dirname(deck_path), ignore_errors=True)


def test_the_edit_card_is_marked_out_from_the_rest_of_the_column():
    """Antonio: "there should be some separation between the 'Edit this deck'
    box and the rest of everything ... I don't want to do a box, but
    something." An accent rule down one edge, which is the class below."""
    if not _HAVE_FLASK:
        return
    ui_app, client, deck_path = _studio(tempfile.mkdtemp(prefix="review3-"))
    packet = _copy_packet()
    try:
        html = _flat(_run(client, packet))
        assert '<div class="card edit-card">' in html
        assert ".studio-side > .card.edit-card {" in html, "the rule must ship too"
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(os.path.dirname(deck_path), ignore_errors=True)


def test_the_primary_buttons_in_the_controls_column_are_compact():
    """Antonio named "Apply edit", "Render again with these switches" and
    "Export Claude Design prompt" as too big. All three are unclassed buttons
    getting the Generate-deck style, and the rule that shrinks them must not
    touch the Generate button itself."""
    if not _HAVE_FLASK:
        return
    ui_app, client, deck_path = _studio(tempfile.mkdtemp(prefix="review3-"))
    packet = _copy_packet()
    try:
        html = _flat(_run(client, packet))
        assert ".studio-side button:not(.ghost) {" in html
        # Scoped to the column: the Generate tab's button keeps the full size.
        rule = re.search(r'\.studio-side button:not\(\.ghost\) \{[^}]*\}', html)
        assert "padding: 8px 15px" in rule.group(0)
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(os.path.dirname(deck_path), ignore_errors=True)


# ------------------------------------------------------------------- the deck

def test_the_three_deliverable_links_sit_together():
    """Antonio: "under the deck ... I want the three buttons to be next to each
    other. They're far apart." They were `space-between` across the deck's full
    width."""
    if not _HAVE_FLASK:
        return
    ui_app, client, deck_path = _studio(tempfile.mkdtemp(prefix="review3-"))
    packet = _copy_packet()
    try:
        html = _flat(_run(client, packet))
        rule = re.search(r'\.deck-toolbar \{[^}]*\}', html).group(0)
        assert "justify-content: flex-start" in rule
        assert "space-between" not in rule
        # And Save deck joined the row rather than costing the deck another one.
        bar = html[html.find('<div class="deck-toolbar">'):]
        bar = bar[:bar.find("</div>", bar.find("save-deck-form"))]
        for link in ("open full screen", "download HTML", "download PDF"):
            assert link in bar, f"{link} left the toolbar"
        assert "save-deck-form" in bar, "Save deck should share the toolbar row"
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(os.path.dirname(deck_path), ignore_errors=True)


def test_the_viewport_fits_a_whole_slide():
    """Antonio: "the window to view the slide should be the size of the slide,
    so you can see the whole slide without scrolling."

    The fit used to be width-only, so the frame was as tall as the viewport
    happened to be and a reviewer landed between two slides. Asserted on the
    script the page serves, because there is no headless browser in this suite
    and an unshipped fix is exactly the failure this file exists to catch.
    """
    if not _HAVE_FLASK:
        return
    ui_app, client, deck_path = _studio(tempfile.mkdtemp(prefix="review3-"))
    packet = _copy_packet()
    try:
        html = _flat(_run(client, packet))
        assert "var DECK_BASE_H = 720" in html, "the slide's own height"
        assert "availH / DECK_BASE_H" in html, (
            "the scale must be bounded by height as well as width"
        )
        # And the window is sized to the fitted slide rather than to the column
        # (2026-09-21), so the leftover falls outside it as page rather than
        # inside it as a gap. Measured on the WRAPPER: reading the window's own
        # width here would feed the last fit into the next one.
        assert "wrap.clientWidth" in html and "wrap.clientHeight" in html
        assert "vp.style.width = Math.round(layoutW * scale)" in html
        # And the deck's own file is never written to: the snap rule is injected
        # into the live preview document at runtime.
        assert "scroll-snap-type:y mandatory" in html
        assert "getElementById('studio-snap')" in html
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(os.path.dirname(deck_path), ignore_errors=True)


# ----------------------------------------------------------- the Generate tab

def test_the_optional_fields_fold_away_but_still_post():
    """Antonio: "Can we simplify the Generate tab so I don't need to scroll all
    the way down to generate?"

    The three fields nobody edits on the PRD path are behind one `<details>`.
    A native disclosure posts its fields whether it is open or shut, and that
    is the half worth pinning: hiding an input that then stops posting would
    break every run that relies on a default.
    """
    if not _HAVE_FLASK:
        return
    import ui.app as a
    a.app.testing = True
    html = _flat(a.app.test_client().get("/?tab=generate").get_data(as_text=True))
    block = html[html.find('<details class="more-options">'):]
    block = block[:block.find("</details>")]
    assert block, "the disclosure must be on the Generate tab"
    for field in ('name="check_in_date"', 'name="pe_firm"', 'name="proposal_date"'):
        assert field in block, f"{field} must still post from inside the fold"
    # The fold sits AFTER the opportunity list, so the path to Generate is
    # upload, deck type, company, opportunities, Generate.
    assert html.find('id="opportunity_ids"') < html.find('<details class="more-options">')
    assert html.find('<details class="more-options">') < html.find(">Generate deck<")


def test_syncdecktype_can_still_hide_the_checkin_row_inside_the_fold():
    """`syncDeckType` hides `#checkin_row` on a proposal deck. Moving the row
    into the disclosure must not have moved the id it reaches for."""
    if not _HAVE_FLASK:
        return
    import ui.app as a
    a.app.testing = True
    html = _flat(a.app.test_client().get("/?tab=generate").get_data(as_text=True))
    assert 'id="checkin_row"' in html, "the row the script hides"
    body = html[html.find("window.syncDeckType = function"):][:420]
    assert body, "syncDeckType must still be defined"
    assert "getElementById('checkin_row')" in body, (
        "the script must still reach the row by the id it carries"
    )


# ------------------------------------------- the switch stops reloading the page

def _deck_html_for(selection):
    """A deck whose slide 2 carries exactly the bullets ``selection`` kept.

    BUILT FROM THE SELECTION rather than written out by hand, so the fixture
    cannot drift from the thing under test: a toggle finds its bullet by text,
    and a deck whose copy says something slightly different would make every
    test here pass or fail for the wrong reason.

    The shape is the renderer's own: a `<section class="slide">` holding
    `.panel--today` and `.panel--after`, each with a `<ul class="bullets">`.
    That is what `locate_bullet_list` navigates.
    """
    panels = []
    for panel in selection["panels"]:
        cls = ("panel--today" if panel["role"] == "today_pain_bullets"
               else "panel--after")
        items = "\n".join(
            f'            <li>{row["text"]}</li>' for row in panel["kept"])
        panels.append(
            f'        <div class="panel {cls}">\n'
            f'          <div class="panel-head">{panel["label"]}</div>\n'
            f'          <ul class="bullets">\n{items}\n          </ul>\n'
            f'        </div>')
    body = "\n".join(panels)
    return (
        '<!doctype html><html><head><style>'
        '.slide{width:1280px;height:720px}.bullets li{font-size:12.5px}'
        '</style></head><body>'
        '<section class="slide" data-slide="1"><h1>Cover</h1>'
        '<p>Investment of $275,000 this year.</p></section>\n'
        '<section class="slide" data-slide="2">\n'
        '      <div class="opp-grid">\n' + body + '\n      </div>\n'
        '</section>\n'
        '<section class="slide" data-slide="3"><h1>Terms</h1>'
        '<p>Lock date <span class="flag">[MISSING: baseline_locked_date]</span></p>'
        '</section></body></html>')


def _switch_studio(monkeypatch, *, fits=True, deck_html=None):
    """A studio whose Result tab carries a real selection and a real deck.

    Three things are redirected, and each would otherwise reach outside the
    test. The toggle store goes to a temp file, because `bullet_toggles`
    refuses a test-time write to the committed one and these tests record real
    switches. The deck on disk is replaced with one whose slide 2 actually
    carries the selection's bullets, because a switch now EDITS the deck and
    the surface fixture's toy deck has no bullet list to edit. And
    `check_layout` is stubbed, because measuring a candidate drives headless
    Chrome for about a second per switch-on and these tests are not about
    whether Chrome works.

    ``fits=False`` makes the stub report an overflow, which is how the refusal
    path is exercised without needing a deck that genuinely clips.
    """
    from test_bullet_selection_surface import SELECTION as FULL_SELECTION
    from test_bullet_selection_surface import _run as run_with
    from test_bullet_selection_surface import _studio_with
    import app as ui_app
    client, packet, deck_dir = _studio_with(FULL_SELECTION)
    monkeypatch.setattr(ui_app, "TOGGLES_PATH",
                        os.path.join(deck_dir, "toggles.json"))
    deck_path = os.path.join(deck_dir, "output-7.html")
    with open(deck_path, "w", encoding="utf-8") as f:
        f.write(deck_html if deck_html is not None
                else _deck_html_for(FULL_SELECTION))

    def _stub_check_layout(html, **kw):
        if fits:
            return {"ok": True, "checked": True, "slides": 3, "findings": []}
        return {"ok": False, "checked": True, "slides": 3,
                "findings": [{"slide": 2, "element": "ul.bullets",
                              "text": "...", "kind": "clipped", "axis": "y",
                              "overflow_px": 14}]}
    monkeypatch.setattr(ui_app, "check_layout", _stub_check_layout)

    run_with(client, packet)
    return client, packet, deck_path, deck_dir


def _toggle(client, packet, deck_path, text, state, background=True):
    data = {
        "scope": packet, "opportunity": "0", "role": "today_pain_bullets",
        "text": text, "state": state, "deck_path": deck_path,
        "deck_type": "status", "company": "Northwind", "project": "Impl",
        "packet": packet, "check_in_date": "2026-05-22",
    }
    if background:
        data["background"] = "1"
    return client.post("/bullet-toggle", data=data)


def test_a_background_switch_returns_the_card_it_changed(monkeypatch):
    """Antonio: "when I switch a toggle, it jumps me back to the top of the
    page ... then I have to scroll back down to where I was toggling."

    The fix is a background post, and a background post is only worth having if
    the response carries the re-rendered card: a switch that reported a notice
    and left itself pointing the wrong way would be worse than the jump.
    """
    if not _HAVE_FLASK:
        return
    client, packet, deck_path, deck_dir = _switch_studio(monkeypatch)
    try:
        out = _toggle(client, packet, deck_path,
                      "Captains handwrite the daily report on paper", "off").get_json()
        assert out["bad"] is False, out
        assert "bullets_html" in out, (
            "the background response must carry the re-rendered card"
        )
        card = _flat(out["bullets_html"]).strip()
        assert card.startswith('<div id="bullets-card">'), (
            "the fragment carries its own wrapper, so the id survives the swap"
        )
        assert card.endswith("</div>")
        assert "Slide 2 bullet selection" in card
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


def test_the_returned_card_shows_the_switch_in_its_new_position(monkeypatch):
    """The whole point of returning the card. Switching a kept bullet off has to
    come back with that bullet's switch reading off, or the reviewer presses it
    again."""
    if not _HAVE_FLASK:
        return
    client, packet, deck_path, deck_dir = _switch_studio(monkeypatch)
    text = "Captains handwrite the daily report on paper"
    try:
        before = _flat(_toggle(client, packet, deck_path, text, "off").get_json()
                       ["bullets_html"])
        # The row for that bullet, up to the end of its switch form.
        row = before[before.find(text):]
        row = row[:row.find("</form>")]
        assert 'class="switch is-off"' in row, (
            f"the bullet just switched off must read off: {row[:400]}"
        )
        assert 'value="on"' in row, "and pressing it again must switch it back on"

        after = _flat(_toggle(client, packet, deck_path, text, "on").get_json()
                      ["bullets_html"])
        row = after[after.find(text):]
        row = row[:row.find("</form>")]
        assert 'class="switch is-on"' in row, "and back on again"
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


def test_a_reset_appears_once_a_switch_has_been_set(monkeypatch):
    """`auto` is still reachable: the reset is what hands a bullet back to the
    fitter, and it must show up in the card the background post returns."""
    if not _HAVE_FLASK:
        return
    client, packet, deck_path, deck_dir = _switch_studio(monkeypatch)
    text = "Captains handwrite the daily report on paper"
    try:
        card = _flat(_toggle(client, packet, deck_path, text, "off").get_json()
                     ["bullets_html"])
        assert '<button class="ghost switch-reset"' in card, (
            "a decided bullet offers the way back to the fitter"
        )
        cleared = _flat(_toggle(client, packet, deck_path, text, "auto").get_json()
                        ["bullets_html"])
        assert '<button class="ghost switch-reset"' not in cleared, (
            "and the offer goes away once there is nothing to undo"
        )
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


def test_the_page_ships_the_script_that_binds_the_switches(monkeypatch):
    """The failure this whole file exists for. A background handler that is
    never bound leaves every switch posting natively and jumping the page, and
    nothing about that is visible to a route test."""
    if not _HAVE_FLASK:
        return
    client, packet, deck_path, deck_dir = _switch_studio(monkeypatch)
    try:
        html = _flat(client.get("/?tab=result").get_data(as_text=True))
        assert "function bindSwitchForms" in html
        assert "bindSwitchForms(document);" in html, "it has to actually run"
        assert "data.set('background', '1')" in html
        assert "side.scrollTop = keepScroll" in html, (
            "the column is put back where the reviewer left it"
        )
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


def test_a_switch_still_posts_natively_without_javascript(monkeypatch):
    """No JavaScript is still a supported path: the same post with no
    `background` flag must re-render the whole Result tab as it always did."""
    if not _HAVE_FLASK:
        return
    client, packet, deck_path, deck_dir = _switch_studio(monkeypatch)
    try:
        resp = _toggle(client, packet, deck_path,
                       "Captains handwrite the daily report on paper", "off",
                       background=False)
        html = _flat(resp.get_data(as_text=True))
        assert resp.status_code == 200
        assert "Slide 2 bullet selection" in html, "a whole page, not a fragment"
        assert "Edit this deck" in html
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


# ------------------------------------- the render-again button, 2026-09-22

def test_the_render_again_button_and_route_are_gone(studio):
    """Antonio pressed that button on 2026-09-22 and got a red refusal; on
    2026-09-22 evening it was deleted outright, because a switch now edits the
    deck and there is nothing left to ask for afterwards.

    It was offered on every live run and worked on none: `/rerender` rebuilt
    from `FixtureProvider.from_packet_file`, so it needed a packet FILE, and a
    live run posts no packet path at all. Rather than fix a button whose
    purpose had evaporated, the whole path went.
    """
    import app as ui_app
    html = _prd_run(studio)
    # The BUTTON, not the phrase: that phrase still appears in a CSS comment
    # on this page, which is how the first version of this test failed on a
    # page with no button.
    assert ">Render again with these switches</button>" not in html
    assert "/rerender" not in html, "no form may still post to the dead route"

    assert not hasattr(ui_app, "rerender_route"), "the route is gone"
    assert not hasattr(ui_app, "_packet_file"), (
        "the predicate existed only to decide whether to draw the button"
    )
    assert "/rerender" not in {r.rule for r in ui_app.app.url_map.iter_rules()}, (
        "and it is off the url map, so a hand-built POST gets a 404 rather "
        "than a half-working render"
    )


def test_nothing_tells_the_reviewer_to_re_run_for_a_bullet_any_more(studio):
    """The line that replaced the button this morning said the next render
    would build the deck with the switch. That stopped being what happens."""
    html = _prd_run(studio)
    assert "the next render will build the deck with it" not in html
    assert "Attach the same PRD on the Generate tab and the next deck" not in html


# ================= real-time toggles: the switch edits the deck =============
# Antonio, 2026-09-22: "Why can't the toggle switches edit the deck in real
# time, without having to re-generate a deck?" Plan:
# build-plan-realtime-bullet-toggles.md. These assert on the FILE ON DISK,
# because the whole claim is that the deliverable changed, and a response body
# saying so is not the same fact.

KEPT_TODAY = "Captains handwrite the daily report on paper"
DROPPED_TODAY = "A competitor benchmark that ranked down"


def _deck_text(deck_path):
    """The deck's CURRENT revision, read off disk."""
    from html_edit_layer import current_revision_path
    with open(current_revision_path(deck_path), encoding="utf-8") as f:
        return f.read()


def test_switching_a_bullet_off_removes_it_from_the_file(monkeypatch):
    client, packet, deck_path, deck_dir = _switch_studio(monkeypatch)
    try:
        assert KEPT_TODAY in _deck_text(deck_path), "it starts on the slide"
        out = _toggle(client, packet, deck_path, KEPT_TODAY, "off").get_json()
        assert out["bad"] is False, out
        assert KEPT_TODAY not in _deck_text(deck_path), (
            "the bullet must leave the deck FILE, not just the response"
        )
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


def test_switching_a_dropped_bullet_on_puts_it_in_the_file(monkeypatch):
    client, packet, deck_path, deck_dir = _switch_studio(monkeypatch)
    try:
        assert DROPPED_TODAY not in _deck_text(deck_path)
        out = _toggle(client, packet, deck_path, DROPPED_TODAY, "on").get_json()
        assert out["bad"] is False, out
        assert DROPPED_TODAY in _deck_text(deck_path)
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


def test_a_toggle_writes_a_revision_and_logs_it(monkeypatch):
    """Same chain as every other edit, which is what makes undo and the edit
    history work on a toggle with no other code changing."""
    from html_edit_layer import current_revision_path, load_edit_log
    client, packet, deck_path, deck_dir = _switch_studio(monkeypatch)
    try:
        assert current_revision_path(deck_path) == deck_path, "no revisions yet"
        _toggle(client, packet, deck_path, KEPT_TODAY, "off")
        assert current_revision_path(deck_path) != deck_path, (
            "a toggle must write the next revision, not overwrite the deck"
        )
        log = load_edit_log(deck_path)
        assert len(log) == 1, log
        assert "bullet off" in log[0]["after"], log[0]
        assert log[0]["replay"] is False, (
            "a structural bullet edit must not be replayed onto a later render"
        )
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


def test_undo_restores_the_deck_byte_for_byte(monkeypatch):
    client, packet, deck_path, deck_dir = _switch_studio(monkeypatch)
    try:
        before = _deck_text(deck_path)
        _toggle(client, packet, deck_path, KEPT_TODAY, "off")
        assert _deck_text(deck_path) != before
        client.post("/undo", data={"path": deck_path, "deck_type": "status",
                                   "company": "Northwind", "project": "Impl",
                                   "packet": packet})
        assert _deck_text(deck_path) == before, (
            "undo is file-based, so a structural edit reverses exactly"
        )
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


def test_a_bullet_that_would_overflow_is_refused_and_nothing_is_written(monkeypatch):
    """The panel is a fixed box with overflow:hidden, so a clipped line is
    still in the HTML and every string-reading guard passes while the slide
    reads wrong. The candidate is measured and thrown away."""
    client, packet, deck_path, deck_dir = _switch_studio(monkeypatch, fits=False)
    try:
        before = _deck_text(deck_path)
        out = _toggle(client, packet, deck_path, DROPPED_TODAY, "on").get_json()
        assert out["bad"] is True, out
        assert "past the edge of its panel" in out["notice"], out["notice"]
        assert _deck_text(deck_path) == before, (
            "a refused switch-on must leave the file byte for byte as it was"
        )
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


def test_an_unrecognised_slide_refuses_and_changes_nothing(monkeypatch):
    """The slide-2 markup is regenerated by a model on every run and will
    eventually come back in a shape this does not understand. Refusing loudly
    is correct; guessing is not."""
    odd = ('<!doctype html><html><body>'
           '<section class="slide" data-slide="1"><h1>Cover</h1></section>'
           '<section class="slide" data-slide="2"><p>No panels here at all.</p>'
           '</section></body></html>')
    client, packet, deck_path, deck_dir = _switch_studio(monkeypatch, deck_html=odd)
    try:
        out = _toggle(client, packet, deck_path, KEPT_TODAY, "off").get_json()
        assert out["bad"] is True, out
        assert "could not be applied" in out["notice"], out["notice"]
        assert _deck_text(deck_path) == odd, "nothing may be written on a refusal"
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


def test_a_refused_switch_is_not_recorded(monkeypatch):
    """The store must never claim something the deck does not show. If the
    edit does not land, the switch is not written."""
    from bullet_toggles import overrides_for
    import app as ui_app
    client, packet, deck_path, deck_dir = _switch_studio(monkeypatch, fits=False)
    try:
        _toggle(client, packet, deck_path, DROPPED_TODAY, "on")
        assert not overrides_for(packet, path=ui_app.TOGGLES_PATH), (
            "a switch that could not be applied must not be recorded"
        )
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


def test_the_switch_position_is_read_from_the_deck(monkeypatch):
    """The drift this prevents: `bullet_selection.kept` describes the ORIGINAL
    render and stops being true the moment anyone flips anything. A dropped
    bullet switched on is on the slide, and its switch has to say so."""
    client, packet, deck_path, deck_dir = _switch_studio(monkeypatch)
    try:
        card = _flat(_toggle(client, packet, deck_path, DROPPED_TODAY, "on")
                     .get_json()["bullets_html"])
        row = card[card.find(DROPPED_TODAY):]
        row = row[:row.find("</form>")]
        assert 'class="switch is-on"' in row, (
            f"the deck has this bullet now, so the switch must read on: {row[:300]}"
        )
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


def test_the_response_points_the_page_at_the_new_revision(monkeypatch):
    """A toggle writes a new revision, so the file the page is showing is no
    longer current. The iframe and every hidden path field have to follow or
    the reviewer acts on the revision before last."""
    client, packet, deck_path, deck_dir = _switch_studio(monkeypatch)
    try:
        out = _toggle(client, packet, deck_path, KEPT_TODAY, "off").get_json()
        assert out["deck_path"] and out["deck_path"] != deck_path
        assert out["old_deck_path"] == deck_path
        assert out["preview_url"] and "preview" in out["preview_url"]
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


def test_a_switch_that_changes_nothing_writes_no_revision(monkeypatch):
    """Turning on a bullet already on the slide is a reviewer confirming the
    state, not an edit. It must not pile up empty revisions."""
    from html_edit_layer import current_revision_path
    client, packet, deck_path, deck_dir = _switch_studio(monkeypatch)
    try:
        out = _toggle(client, packet, deck_path, KEPT_TODAY, "on").get_json()
        assert out["bad"] is False, out
        assert current_revision_path(deck_path) == deck_path, "no revision"
        assert not out["deck_path"], "and nothing for the page to follow"
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


def test_a_reset_restores_the_fitters_own_answer(monkeypatch):
    """`auto` hands the bullet back to the fitter, and the fitter already
    answered once in the run's selection. Reset restores the slide to the
    answer the render was built from, not to wherever the last switch left
    it."""
    client, packet, deck_path, deck_dir = _switch_studio(monkeypatch)
    try:
        _toggle(client, packet, deck_path, KEPT_TODAY, "off")
        assert KEPT_TODAY not in _deck_text(deck_path)
        _toggle(client, packet, deck_path, KEPT_TODAY, "auto")
        assert KEPT_TODAY in _deck_text(deck_path), (
            "the fitter kept this bullet, so a reset must put it back"
        )
        _toggle(client, packet, deck_path, DROPPED_TODAY, "on")
        assert DROPPED_TODAY in _deck_text(deck_path)
        _toggle(client, packet, deck_path, DROPPED_TODAY, "auto")
        assert DROPPED_TODAY not in _deck_text(deck_path), (
            "the fitter dropped this one, so a reset must take it back off"
        )
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


def test_a_toggle_never_calls_the_model(monkeypatch):
    """The whole point. A bullet decision that costs an API call is the thing
    being removed."""
    import app as ui_app
    client, packet, deck_path, deck_dir = _switch_studio(monkeypatch)
    calls = []
    monkeypatch.setattr(ui_app, "generate_and_save_deck",
                        lambda *a, **k: calls.append(1))
    try:
        _toggle(client, packet, deck_path, KEPT_TODAY, "off")
        _toggle(client, packet, deck_path, DROPPED_TODAY, "on")
        assert calls == [], "no render leg may run on the toggle path"
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


def test_the_page_script_follows_the_deck(monkeypatch):
    """The handler that does it has to actually ship, or every toggle leaves
    the reviewer looking at the previous revision."""
    client, packet, deck_path, deck_dir = _switch_studio(monkeypatch)
    try:
        html = _flat(client.get("/?tab=result").get_data(as_text=True))
        assert "if (out.preview_url)" in html
        # Through the helper since 2026-09-23, which also keeps the slide.
        assert "reloadPreviewKeepingSlide(frame, out.preview_url)" in html
        assert "frame.src = url;" in html
        assert "input.value = out.deck_path" in html
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


# ============ the panel resizes to hold a bullet, and grows back ============
# The gap this closes: a full render spends the type ladder on any panel a
# reviewer has decided about, so a switch-on that refused at the deck's
# current size would refuse bullets the NEXT generated deck goes on to show.

def _panel_rules(deck_path):
    from bullet_type import read_panel_type
    return read_panel_type(_deck_text(deck_path))


def _sized_studio(monkeypatch, verdicts):
    """A studio whose measurements are scripted.

    ``verdicts`` is one entry per `check_layout` call, True for clean. Real
    measurement drives headless Chrome for about a second a go and the point
    of these tests is the LADDER, not whether Chrome works, so the browser is
    replaced by a list of answers and the test asserts which sizes were tried.
    """
    import app as ui_app
    client, packet, deck_path, deck_dir = _switch_studio(monkeypatch)
    tried = []

    answers = list(verdicts)

    def scripted(html, **kw):
        from bullet_type import read_panel_type
        rules = read_panel_type(html)
        tried.append(next(iter(rules.values())) if rules else 12.5)
        ok = answers.pop(0) if answers else True
        return {"ok": ok, "checked": True, "slides": 3,
                "findings": [] if ok else [{"slide": 2, "element": "ul.bullets",
                                            "text": "...", "kind": "clipped",
                                            "axis": "y", "overflow_px": 9}]}
    monkeypatch.setattr(ui_app, "check_layout", scripted)
    return client, packet, deck_path, deck_dir, tried


def test_a_bullet_that_only_fits_smaller_shrinks_the_panel(monkeypatch):
    """Clips at the house size, fits one step down. The panel is resized and
    the bullet goes on, instead of being refused."""
    client, packet, deck_path, deck_dir, tried = _sized_studio(
        monkeypatch, [False, True])
    try:
        out = _toggle(client, packet, deck_path, DROPPED_TODAY, "on").get_json()
        assert out["bad"] is False, out
        assert DROPPED_TODAY in _deck_text(deck_path)
        assert _panel_rules(deck_path) == {(2, "today"): 11.5}, _panel_rules(deck_path)
        assert "type is now 11.5px" in out["notice"], out["notice"]
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


def test_the_house_size_is_tried_first_and_kept_when_it_works(monkeypatch):
    """The estimator is more pessimistic than a browser, so asking it first
    shrank decks that did not need it. The browser goes first."""
    client, packet, deck_path, deck_dir, tried = _sized_studio(monkeypatch, [True])
    try:
        out = _toggle(client, packet, deck_path, DROPPED_TODAY, "on").get_json()
        assert out["bad"] is False, out
        assert tried == [12.5], f"one measurement, at the house size: {tried}"
        assert _panel_rules(deck_path) == {}, "no rule for a panel that did not move"
        assert "type is now" not in out["notice"], out["notice"]
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


def test_the_ladder_stops_at_the_largest_size_that_holds(monkeypatch):
    """House, then the middle of the ladder, then the floor. Three tries at
    most: a deck shrunk further than it needs is a worse deck, and a fourth
    measurement is another second of a reviewer's time."""
    client, packet, deck_path, deck_dir, tried = _sized_studio(
        monkeypatch, [False, False, True])
    try:
        out = _toggle(client, packet, deck_path, DROPPED_TODAY, "on").get_json()
        assert out["bad"] is False, out
        assert tried == [12.5, 11.5, 10.5], tried
        assert _panel_rules(deck_path) == {(2, "today"): 10.5}
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


def test_a_bullet_that_fits_at_no_size_is_still_refused(monkeypatch):
    """Shrinking is a way to say yes more often, not a way to stop saying no."""
    client, packet, deck_path, deck_dir, tried = _sized_studio(
        monkeypatch, [False, False, False])
    try:
        before = _deck_text(deck_path)
        out = _toggle(client, packet, deck_path, DROPPED_TODAY, "on").get_json()
        assert out["bad"] is True, out
        assert "10.5px, the smallest readable size" in out["notice"], out["notice"]
        assert _deck_text(deck_path) == before, (
            "every attempted size was a candidate; none of them was written"
        )
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


def test_the_panel_grows_back_when_the_bullet_goes(monkeypatch):
    """A panel shrunk to hold a bullet has to grow back when that bullet
    goes, or the deck quietly gets smaller as a reviewer works."""
    client, packet, deck_path, deck_dir, tried = _sized_studio(
        monkeypatch, [False, True, True])
    try:
        _toggle(client, packet, deck_path, DROPPED_TODAY, "on")
        assert _panel_rules(deck_path) == {(2, "today"): 11.5}
        out = _toggle(client, packet, deck_path, DROPPED_TODAY, "off").get_json()
        assert out["bad"] is False, out
        assert _panel_rules(deck_path) == {}, "the rule must go with the bullet"
        assert "back at its full type size" in out["notice"], out["notice"]
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


def test_the_type_block_is_rewritten_and_never_stacked(monkeypatch):
    """`apply_panel_type` appends, which is right once per render. A switch
    runs on a document that may already carry a block, so it rewrites."""
    client, packet, deck_path, deck_dir, tried = _sized_studio(
        monkeypatch, [False, True, False, True])
    try:
        _toggle(client, packet, deck_path, DROPPED_TODAY, "on")
        _toggle(client, packet, deck_path,
                "A data-availability caveat that ranked down", "on")
        html = _deck_text(deck_path)
        assert html.count("Bullet type set by panel_fit") == 1, (
            "one block, rewritten, not one per switch"
        )
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


def test_size_holding_all_is_not_fit_bullets():
    """The bug that made the whole hook inert for an afternoon. `fit_bullets`
    picks a size by how many bullets it KEEPS and is free to drop the tail, so
    asked about a panel with one over-long line it answers "house size, and
    drop that line" — which is the line the reviewer just switched on."""
    import panel_fit
    lines = ["Captains handwrite the daily report on paper",
             "Fuel entry is optional across live projects",
             ("A deliberately very long bullet " * 14).strip()]
    fit = panel_fit.fit_bullets(lines, room_px=96.4, max_bullets=len(lines))
    assert fit.font_px == panel_fit.BULLET_FONT, "it does not step down"
    assert fit.dropped, "it drops instead"
    assert panel_fit.size_holding_all(lines, room_px=96.4) is None, (
        "the question a reviewer's switch asks has a different answer"
    )


# ================== more than one opportunity on a deck ====================
# A deck carries one slide 2 PER OPPORTUNITY, in the order the reviewer picked
# them, and a switch belongs to exactly one of them. Every deck this build has
# rendered has a single opportunity, so nothing exercised that until now and
# an off-by-one here would edit the wrong client's slide. Contoso lists
# eight opportunities on the platform, so this is reachable in one click.

def _two_opportunity_deck():
    """A deck with two opportunity slides, each with its own bullets.

    Slide numbering follows the renderer's: slide 1 is the cover, then one
    slide per opportunity, then the rest. `prompt_assembler.rendered_slide_count`
    is where that repeat is defined.
    """
    def panel(cls, label, items):
        lis = "\n".join(f"            <li>{t}</li>" for t in items)
        return (f'        <div class="panel {cls}">\n'
                f'          <div class="panel-head">{label}</div>\n'
                f'          <ul class="bullets">\n{lis}\n          </ul>\n'
                f'        </div>')

    def opp_slide(n, prefix):
        return (f'<section class="slide" data-slide="{n}">\n'
                '      <div class="opp-grid">\n'
                + panel("panel--today", "TODAY",
                        [f"{prefix} pain one", "A line both opportunities carry",
                         f"{prefix} pain three"]) + "\n"
                + panel("panel--after", "AFTER", [f"{prefix} after one"]) + "\n"
                '      </div>\n</section>')

    return ('<!doctype html><html><head><style>'
            '.slide{width:1280px;height:720px}.bullets li{font-size:12.5px}'
            '</style></head><body>'
            '<section class="slide" data-slide="1"><h1>Cover</h1></section>\n'
            + opp_slide(2, "FIRST") + "\n" + opp_slide(3, "SECOND") + "\n"
            '<section class="slide" data-slide="4"><h1>Terms</h1></section>'
            '</body></html>')


def test_each_opportunity_is_found_on_its_own_slide():
    from html_edit_layer import list_slide_bullets, locate_bullet_list, opportunity_slides
    deck = _two_opportunity_deck()
    assert [s["index"] for s in opportunity_slides(deck)] == [2, 3], (
        "the cover and the terms slide carry no bullet list and are not "
        "opportunities; the two that do are, in document order"
    )
    for opp, slide, prefix in ((0, 2, "FIRST"), (1, 3, "SECOND")):
        got = locate_bullet_list(deck, opportunity=opp, role="today_pain_bullets")
        assert got["slide"] == slide, got
        first = list_slide_bullets(deck, opportunity=opp,
                                   role="today_pain_bullets")[0]["text"]
        assert first.startswith(prefix), first


def test_a_switch_on_one_opportunity_leaves_the_other_alone():
    """The off-by-one this exists to catch would edit the wrong client's
    slide, and the deck would still render, so nothing else would notice."""
    from html_edit_layer import list_slide_bullets, set_bullet_presence
    deck = _two_opportunity_deck()
    after = set_bullet_presence(deck, opportunity=1, role="today_pain_bullets",
                                text="SECOND pain three", on=False)
    kept = [b["text"] for b in list_slide_bullets(
        after, opportunity=0, role="today_pain_bullets")]
    moved = [b["text"] for b in list_slide_bullets(
        after, opportunity=1, role="today_pain_bullets")]
    assert len(kept) == 3, "the first opportunity must not move"
    assert len(moved) == 2 and "SECOND pain three" not in moved


def test_the_same_bullet_text_on_both_slides_is_scoped_to_one():
    """The nastiest case, and the reason the locator navigates to a slide
    before it searches for text. Both opportunities carry the same line; a
    switch names only one of them."""
    from html_edit_layer import list_slide_bullets, set_bullet_presence
    deck = _two_opportunity_deck()
    shared = "A line both opportunities carry"
    after = set_bullet_presence(deck, opportunity=0, role="today_pain_bullets",
                                text=shared, on=False)
    first = [b["text"] for b in list_slide_bullets(
        after, opportunity=0, role="today_pain_bullets")]
    second = [b["text"] for b in list_slide_bullets(
        after, opportunity=1, role="today_pain_bullets")]
    assert shared not in first, "removed where it was named"
    assert shared in second, "and nowhere else"


def test_a_type_rule_lands_on_the_opportunity_it_belongs_to():
    """`bullet_type` addresses a panel by slide number. The render path
    computes that as FIRST_OPPORTUNITY_SLIDE + index and the edit path reads
    it off the document; they agree here, and only the second can be right
    for a deck shaped in a way nobody anticipated."""
    from bullet_type import read_panel_type, set_panel_type
    from html_edit_layer import locate_bullet_list
    deck = _two_opportunity_deck()
    slide = locate_bullet_list(deck, opportunity=1,
                               role="today_pain_bullets")["slide"]
    sized = set_panel_type(deck, slide, "today", 11.0)
    assert read_panel_type(sized) == {(3, "today"): 11.0}, read_panel_type(sized)


def test_naming_an_opportunity_the_deck_does_not_have_is_refused():
    from html_edit_layer import EditNotApplicable, locate_bullet_list
    deck = _two_opportunity_deck()
    try:
        locate_bullet_list(deck, opportunity=4, role="today_pain_bullets")
    except EditNotApplicable as exc:
        assert "2 opportunity slides" in str(exc), str(exc)
        assert "number 5" in str(exc), str(exc)
    else:
        raise AssertionError("an opportunity off the end must be refused")


# ============ picking more than one opportunity, 2026-09-22 ================

def test_nothing_requires_the_picked_opportunities_to_be_adjacent():
    """Antonio: "when selecting multiple opportunities, they have to be next
    to each other in the dropdown."

    Nothing in the code does. The route takes every posted id, in order, once
    each. Asserted so a later change cannot quietly introduce the restriction
    the UI appeared to have.
    """
    if not _HAVE_FLASK:
        return
    import app as ui_app
    import inspect
    src = inspect.getsource(ui_app.run)
    assert 'request.form.getlist("opportunity_ids")' in src
    # No slicing, no first/last, no range: every id survives.
    assert "opportunity_ids[0]" not in src and "opportunity_ids[-1]" not in src


def test_a_plain_click_toggles_one_row_instead_of_replacing_the_selection():
    """The real cause. A native `<select multiple>` REPLACES its selection on
    a plain click, so picking the first and the fourth by clicking each in
    turn leaves one picked and the run goes ahead with a deck the reviewer did
    not ask for. Nothing refuses, which is the worst shape."""
    if not _HAVE_FLASK:
        return
    import app as ui_app
    ui_app.app.testing = True
    html = _flat(ui_app.app.test_client().get("/?tab=generate").get_data(as_text=True))
    handler = html[html.find("Picking more than one opportunity"):]
    handler = handler[:handler.find("})();")]
    assert "picker.addEventListener('mousedown'" in handler
    assert "opt.selected = !opt.selected" in handler, "it must TOGGLE"
    assert "ev.metaKey || ev.ctrlKey || ev.shiftKey" in handler, (
        "cmd-click and shift-click must keep working for anyone who knows them"
    )
    assert "opt.disabled" in handler, "the placeholder row is not pickable"
    assert "picker.focus()" in handler, (
        "preventDefault on mousedown suppresses focus, and a picker that "
        "cannot be tabbed out of is worse than the bug"
    )
    assert "new Event('change'" in handler, (
        "a selection changed by script fires no event, and the project field "
        "is derived from this one"
    )


def test_the_picker_no_longer_claims_the_order_is_the_order_you_clicked():
    """A form submits a multiple select's options in DOCUMENT order, so the
    slides follow the list and not the clicks, and "titled after the first"
    meant the highest one picked."""
    if not _HAVE_FLASK:
        return
    import app as ui_app
    ui_app.app.testing = True
    html = _flat(ui_app.app.test_client().get("/?tab=generate").get_data(as_text=True))
    assert "in the order you pick them" not in html
    assert "in the order they appear in this list" in html


def test_the_route_keeps_every_picked_id_in_list_order_once_each():
    """The behaviour the copy now describes."""
    if not _HAVE_FLASK:
        return
    from werkzeug.datastructures import MultiDict
    import app as ui_app
    ui_app.app.testing = True
    with ui_app.app.test_request_context(
            "/run", method="POST",
            data=MultiDict([("opportunity_ids", "a"), ("opportunity_ids", "c"),
                            ("opportunity_ids", "a"), ("opportunity_ids", "b")])):
        from flask import request
        ids = [v.strip() for v in request.form.getlist("opportunity_ids") if v.strip()]
        assert list(dict.fromkeys(ids)) == ["a", "c", "b"], (
            "every pick survives, deduplicated, in the order posted"
        )


def test_the_progress_estimate_is_whole_seconds():
    """Antonio, 2026-09-22, screenshot of the loading screen: "these usually
    take 5m 45.69999999999999s to 6m 47.39999999999998s".

    The elapsed counter is whole seconds and always read correctly. The RANGE
    is quantiles over recorded durations, which are floats, and `s % 60` on a
    float returns a float straight into the sentence.
    """
    if not _HAVE_FLASK:
        return
    import app as ui_app
    ui_app.app.testing = True
    html = _flat(ui_app.app.test_client().get("/").get_data(as_text=True))
    fmt = html[html.find("function fmtElapsed"):]
    fmt = fmt[:fmt.find("}", fmt.find("return"))]
    assert "Math.round" in fmt, (
        f"the formatter must round before it splits minutes off: {fmt}"
    )
    # And it still splits the same way, so a rounding fix cannot quietly
    # change 90 seconds into something other than 1m 30s.
    assert "Math.floor(t / 60)" in fmt and "t % 60" in fmt, fmt
