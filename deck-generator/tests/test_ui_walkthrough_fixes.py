"""The four small things that broke the 2026-08-24 walkthrough (fixed 2026-09-02).

Casey opened the studio to click around and give feedback. He never reached the
feedback: the first five minutes were spent on the form itself. These tests pin
the four fixes so the same five minutes are not spent again.

1. The opportunity picker went blank for the two seconds its platform call took,
   with nothing on screen to say a request was in flight ("it seems like it's not
   working"). It says so now, and a slower earlier response can no longer land on
   top of a newer list.
2. Switching the data source to `live` hid nothing and cleared nothing, so a
   frozen fixture's company stayed in the field and resolved against no company
   on the platform.
3. Deck history claimed to hold "every deck any session has generated" while only
   a saved deck is ever written, so a reviewer who rendered one and came back
   found it missing and the copy said that was impossible.
4. The progress overlay covered every tab for the length of a run, so the one
   thing a reviewer waiting on a fifteen-minute render wants to do -- go and read
   deck history -- could not be done, and the tab bar could not even be clicked.

There is no JS harness in this repo, so the browser-side halves are asserted on
the shipped script the way `test_ui_live_failures` already asserts on it. Each was
also driven in a real browser against a live run when it landed.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ui"))

pytest.importorskip("flask")

import app as ui_app  # noqa: E402


def picker_script():
    """Just the opportunity picker's handler, off the shipped page script.

    Sliced at the DEFINITION rather than at the first mention of the name: the
    ambiguous-company chooser (item 17) calls `window.loadOpportunities()` from
    above this function, and splitting on the bare name would hand back that
    call site instead of the handler and quietly assert on the wrong text.
    """
    return (ui_app.BASE.split("window.loadOpportunities = function")[1]
            .split("window.addTermsRow")[0])


# ------------------------------------------------------------------ the picker

def test_the_picker_says_it_is_loading_instead_of_going_blank():
    """The bug Casey actually hit. The list arrives about two seconds later, and
    an empty dropdown for those two seconds is indistinguishable from a broken
    one."""
    script = picker_script()
    assert "OPPORTUNITY_LOADING" in ui_app.BASE
    assert "Loading opportunities" in ui_app.BASE
    # Written BEFORE the fetch, not after it resolves.
    assert script.index("opportunityOnly(sel, OPPORTUNITY_LOADING)") < script.index("fetch(")


def test_a_slower_earlier_request_cannot_overwrite_a_newer_list():
    """Company-blur and project-blur fire in quick succession, so two fetches
    overlap routinely. Without a sequence check the loser can land last and leave
    one company's opportunities under another company's name."""
    script = picker_script()
    assert "opportunitySeq" in script
    assert script.count("seq !== opportunitySeq") == 2, "both the resolve and the reject path"


def test_opening_the_picker_fetches_when_there_is_nothing_in_it():
    """A blur is not guaranteed to have fired first: the company may have been
    written by the fixture autofill, or the source switched with it already
    filled. Opening the picker is when a reviewer expects the list."""
    # The LAST occurrence: the PRD branch also reads the picker, to fill it with
    # what the document named, and it is defined above this handler.
    listeners = ui_app.BASE.split(
        "var picker = document.getElementById('opportunity_ids')")[-1]
    assert "addEventListener('focus'" in listeners
    # And never re-fetches a list that is already populated or already coming.
    assert "opportunityLoading || picker.options.length > 1" in listeners


# ------------------------------------------------- which half of the form is live

def test_the_fixture_only_controls_are_gone_rather_than_hidden():
    """They used to be hidden on the live source. Since 2026-09-20 there is no
    other source to hide them from, so they are not on the form at all: a
    control that can only ever be wrong is worse than no control."""
    assert 'id="fixture_row"' not in ui_app.GENERATE_PANEL
    assert 'name="fixture_id"' not in ui_app.GENERATE_PANEL
    # The packet still travels, because reviewer state is keyed to it; it is
    # filled by the run rather than typed.
    assert 'type="hidden" name="packet"' in ui_app.GENERATE_PANEL


def test_the_live_source_hides_the_fixture_controls_and_clears_their_prefill():
    script = (ui_app.BASE.split("window.syncDataSource")[1]
              .split("window.loadOpportunities = function")[0])
    assert "'fixture_row', 'packet_row'" in script
    assert "live ? 'none' : ''" in script
    # Cleared only while the field still holds the fixture's own value: a company
    # the reviewer typed is theirs.
    assert "fx.dataset[id]" in script


def test_switching_the_source_syncs_the_form_before_listing_opportunities():
    """Listing against a company that is about to be cleared is a wasted call and
    a wrong error message."""
    handler = ui_app.BASE.split("if (source) source.addEventListener('change'")[1]
    handler = handler.split("if (company)")[0]
    assert handler.index("syncDataSource") < handler.index("loadOpportunities")


# ------------------------------------------------------------- deck history copy

def test_deck_history_no_longer_claims_to_hold_every_render():
    """It holds what a reviewer SAVED, and has since 2026-08-09. The copy said
    otherwise, which is what makes a missing deck read as a bug."""
    assert "Every deck any session has generated" not in ui_app.DECKS_PANEL
    assert "Every deck a reviewer <b>saved</b>" in ui_app.DECKS_PANEL
    # And still says where it reads from, which the store-backed listing test pins.
    assert "read from the database" in ui_app.DECKS_PANEL


def test_the_empty_history_says_what_to_press():
    """"Nothing rendered yet" was wrong twice over: renders had happened, and it
    named no way to make one appear."""
    assert "Nothing saved yet" in ui_app.DECKS_PANEL


# --------------------------------------------------------- the overlay mid-run

def test_the_overlay_has_a_non_blocking_form():
    assert "#progress-overlay.mini" in ui_app.BASE
    assert "pointer-events: none" in ui_app.BASE


def test_the_tab_bar_outranks_the_backdrop_while_a_run_blocks():
    """Without this the corner form is unreachable: the backdrop eats the click
    on the very tab that would switch to it."""
    assert "body.run-blocking .tabbar { z-index: 90; }" in ui_app.BASE


def test_only_the_tabs_that_show_the_deck_block():
    place = ui_app.BASE.split("window.placeOverlay = function")[1].split("function showOverlay")[0]
    assert "tab === 'generate' || tab === 'result'" in place
    assert "classList.toggle('mini', !blocking)" in place


def test_a_tab_switch_is_told_where_it_is_going():
    """`activate` writes the hash AFTER it repositions the overlay, so an overlay
    left to read the hash itself keeps the tab it just left."""
    assert "window.placeOverlay(name)" in ui_app.BASE
    assert "function (tabName)" in ui_app.BASE


def test_clearing_the_overlay_clears_both_of_its_marks():
    """A leftover `mini` or `run-blocking` would style the next run wrong."""
    assert "overlay.classList.remove('mini')" in ui_app.BASE
    assert "document.body.classList.remove('run-blocking')" in ui_app.BASE
