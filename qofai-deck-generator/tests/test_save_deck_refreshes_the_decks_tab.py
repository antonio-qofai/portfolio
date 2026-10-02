"""Saving a deck has to show it on the Decks tab.

Antonio, 2026-09-20: "I just saved the deck and then I switched to the decks
tab and it didn't appear. So I don't know if that's a bug. I think it is."

It was, and the save itself was never broken. All four panels are rendered
server-side into one page at load and the tab bar only shows and hides them.
Saving used to post the form and re-render the whole page; it was changed to a
background `fetch` so a reviewer would not lose their scroll position, and that
fetch updated only the status text beside the button. `#panel-decks` went on
holding the list as it stood before the save, so the deck really was saved and
really was not on the tab.

These tests pin the answer at the seam that was missing: the background save
returns the refreshed panel, and the panel names the deck that was just saved.

Run with: python3 -m pytest tests/test_save_deck_refreshes_the_decks_tab.py
"""

import json
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ui"))

try:
    import flask  # noqa: F401
    _HAVE_FLASK = True
except ImportError:
    _HAVE_FLASK = False


def _run_and_save(background):
    from test_ui_review_surface import _copy_packet, _studio
    import tempfile

    deck_dir = tempfile.mkdtemp(prefix="save-refresh-")
    ui_app, client, deck_path = _studio(deck_dir)
    packet = _copy_packet()
    data = {"path": deck_path, "deck_type": "status", "company": "Northwind",
            "project": "Impl", "packet": packet, "check_in_date": "2026-05-22"}
    if background:
        data["background"] = "1"
    response = client.post("/save-deck", data=data)
    return response, deck_path, packet, deck_dir


def test_a_background_save_answers_with_the_refreshed_decks_tab():
    """THE REGRESSION. Without this the reviewer's next click shows a stale list."""
    if not _HAVE_FLASK:
        return
    response, deck_path, packet, deck_dir = _run_and_save(background=True)
    try:
        out = json.loads(response.get_data(as_text=True))
        assert "decks_html" in out, (
            "a background save must return the refreshed Decks tab, or the tab "
            "keeps the list it was rendered with at page load"
        )
        assert os.path.basename(deck_path) in out["decks_html"] \
            or "Northwind" in out["decks_html"], (
            "the refreshed panel must actually name the deck just saved"
        )
        assert out["deck_count"], "the tab badge must count the saved deck"
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


def test_the_no_javascript_save_still_re_renders_the_whole_page():
    """The fallback is the old behaviour, not no behaviour. A form posted
    without the background flag must still come back as a page."""
    if not _HAVE_FLASK:
        return
    response, _deck_path, packet, deck_dir = _run_and_save(background=False)
    try:
        body = response.get_data(as_text=True)
        assert body.lstrip().startswith("<"), "the fallback must return HTML"
        # Not JSON. (The page's own script mentions `decks_html`, so the test is
        # that the response is a document, not that the string is absent.)
        try:
            json.loads(body)
        except ValueError:
            pass
        else:
            raise AssertionError("the fallback must be a page, not a JSON body")
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


def test_the_badge_has_a_handle_the_script_can_find():
    """The count is updated by id. If the element is renamed, the save stops
    updating it silently, which is the class of bug this whole file is about."""
    import app as ui_app

    assert 'id="decks_count"' in ui_app.BASE
    assert "getElementById('decks_count')" in ui_app.BASE
