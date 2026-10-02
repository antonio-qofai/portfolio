"""A change to the deck leaves the reviewer on the slide they were looking at.

Part C of `build-plan-phase4-flags-and-fit.md`. Antonio, 2026-09-23: "when I
toggle a control on or off ... it takes me to the title slide. Is there a way I
can stay on the same slide so I can instantly see the change?"

Every change writes a new revision, and showing it loads a new document into a
preview that is one slide tall and scrolls inside itself, so it opened on slide
1. The reload itself is browser behaviour no Python test can watch, so these
assert the page ships the mechanism, on markup rather than prose; the studio is
where it is confirmed by hand.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ui"))

pytest.importorskip("flask")

import app as ui_app

# The forms that post natively and come back as a whole new studio page, each
# of which changes the deck on screen.
# `commercial_terms` and `commercial_defaults` left this list on 2026-09-23 with
# the two cards that posted to them (Antonio: the old commercial editors "were
# not relevant anymore"). Their routes remain until the commercial-slide build's
# Phase B deletes them, but no form on the page reaches them.
DECK_CHANGING_FORMS = ("edit_ai", "supply_missing", "toggle_progress", "undo")


def _source():
    """The studio page's template source, whitespace normalised.

    The result-tab forms render only when a deck is on screen, so the template
    source is where every one of them can be seen at once."""
    return " ".join(open(ui_app.__file__, encoding="utf-8").read().split())


def _page():
    with ui_app.app.test_request_context("/"):
        return " ".join(ui_app.render_studio("generate").split())


def test_the_switch_reloads_the_preview_through_the_helper():
    page = _page()
    assert "if (frame) reloadPreviewKeepingSlide(frame, out.preview_url);" in page
    assert "if (frame) frame.src = out.preview_url;" not in page


def test_the_helper_notes_the_slide_and_restores_it_after_the_load():
    page = _page()
    assert "var index = slideOnScreen(frame);" in page
    assert "frame.addEventListener('load', function restore() {" in page
    assert ".scrollIntoView({ block: 'center', behavior: 'instant' });" in page
    # Clamped, for a revision with fewer slides than the last one.
    assert "slides[Math.min(index, slides.length - 1)]" in page


def test_the_slide_is_found_by_its_centre_not_by_a_scroll_offset():
    """The preview snaps slide centres and the deck puts a gap between
    slides, so a raw offset would land between two."""
    assert "box.top + box.height / 2 - middle" in _page()


def test_every_form_that_changes_the_deck_keeps_the_slide():
    source = _source()
    for action in DECK_CHANGING_FORMS:
        assert (f"action=\"{{{{ url_for('{action}') }}}}\" data-keep-slide"
                in source), action


def test_a_new_run_is_not_one_of_them():
    """A new deck opens on its cover."""
    assert "action=\"{{ url_for('run') }}\" data-keep-slide" not in _source()


def test_the_kept_slide_crosses_a_page_render_once():
    page = _page()
    assert "sessionStorage.setItem(KEEP_SLIDE_KEY, String(slideOnScreen(frame)));" in page
    assert "sessionStorage.removeItem(KEEP_SLIDE_KEY);" in page
