"""Slide 2's bullet selection reaches a human — the whole chain, not the module.

Two steps stand between the packet's bullet lists and the slide. `bullet_ranking`
decides which bullets matter most, and `panel_fit` decides how many of them the
panel holds. Both were built, tested and recorded on 2026-08-19, and neither
reached a reviewer: `_panel_fit` rode on the placeholder map, which
`generate_deck_prompt` drops on the ok path, and `display_priority` sat in the
packet's section 8 with no UI reading it. So the studio showed four bullets and
nothing anywhere said the packet had carried nine, or that a model chose which
four survived.

That is the defect these tests pin, and they follow the record the whole way:
the adapter composes it, the pipeline carries it out, the studio renders it, and
the deck store keeps it for a session that has since ended. Each link is measured
separately, because that is the one lesson of E11 Stage 2g — a chain green at
every seam and broken at the caller.

No network anywhere. The provider is the real `LiveProposalProvider` over
`test_live_seam`'s stub client with a plain callable for a ranker, the pipeline
runs against the frozen fixture packet, and the studio's pipeline is stubbed.

Run with: python3 -m pytest tests/test_bullet_selection_surface.py
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ui"))

from data_source_adapter import bullet_selection, map_packet  # noqa: E402

from test_ranking_wiring import (AFTER_ROLE, TODAY_ROLE, _document,  # noqa: E402
                                 _request, reversing_ranker)
from deck_run import run_deck

try:
    import flask  # noqa: F401
    _HAVE_FLASK = True
except ImportError:  # UI-only dependency; skip if absent
    _HAVE_FLASK = False


# --- the adapter composes it ------------------------------------------------

def _selection(ranker=None):
    """The selection record for one real client's packet, through the real
    provider and the real mapping half."""
    placeholder_map = map_packet(_document(ranker=ranker), _request("one"))
    return bullet_selection(placeholder_map), placeholder_map


def test_the_selection_names_both_panels_and_counts_them():
    selection, placeholder_map = _selection()
    assert selection, "a proposal packet with bullet lists must report a selection"
    labels = [panel["label"] for panel in selection["panels"]]
    assert labels == ["TODAY", "AFTER"], labels
    for panel in selection["panels"]:
        shown = len(placeholder_map[panel["role"]])
        assert panel["shown"] == shown
        assert panel["of"] >= panel["shown"]
        assert len(panel["kept"]) == panel["shown"]
        assert len(panel["dropped"]) == panel["of"] - panel["shown"]


def test_a_bullet_on_the_slide_is_the_string_that_renders_byte_for_byte():
    """The record must never become a second, prettier copy of the deck's copy.
    A reviewer comparing the card against the slide has to see the same string."""
    selection, placeholder_map = _selection()
    for panel in selection["panels"]:
        assert [row["text"] for row in panel["kept"]] == placeholder_map[panel["role"]]


def test_the_card_can_say_a_model_chose_the_order():
    """Unranked and ranked runs are distinguishable, which is the point: a
    reviewer signing off on four of nine bullets is signing off on a model's
    judgment and has to know that is what it is."""
    plain, _ = _selection()
    ranked, _ = _selection(ranker=reversing_ranker())
    assert not plain["ranked"], "no ranker ran, so nothing may claim one did"
    assert ranked["ranked"], "the pass ran and the record must say so"
    assert any(panel["reordered"] for panel in ranked["panels"]), (
        "reversing every panel must show up as a reordering"
    )


def test_a_reordered_panel_says_where_each_bullet_sat_in_the_packet():
    """The whole use of the position column. With the order reversed, the first
    bullet on the slide is the LAST one in the packet's list, and a reviewer
    checking the slide against the packet needs to be told that rather than left
    to count."""
    selection, _ = _selection(ranker=reversing_ranker())
    reordered = [panel for panel in selection["panels"] if panel["reordered"]]
    assert reordered, "the fixture must actually reorder something"
    for panel in reordered:
        positions = [row["source_position"] for row in panel["kept"]]
        assert all(position is not None for position in positions), positions
        # Reversed: the first bullet shown came from the end of the packet's list.
        assert positions[0] > 1, positions
    for panel in selection["panels"]:
        seen = [row["source_position"]
                for row in list(panel["kept"]) + list(panel["dropped"])]
        assert len(set(seen)) == len(seen), f"a position was reused: {seen}"


def test_a_deck_with_no_such_panel_reports_nothing_rather_than_an_empty_card():
    assert bullet_selection({}) is None
    assert bullet_selection(None) is None
    assert bullet_selection({"_panel_fit": {}, "_bullet_order": {}}) is None


# --- the pipeline carries it out --------------------------------------------

def test_the_pipeline_result_carries_the_selection_out():
    """`generate_deck_prompt` drops the placeholder map on the ok path, so a
    record left on the map reaches nobody. This is the link that was missing."""
    from data_source_adapter import FixtureProvider
    from deck_generator import generate_deck_prompt

    packet = os.path.join(os.path.dirname(__file__), "..",
                          "proposal-data-packet-EXAMPLE.md")
    provider = FixtureProvider.from_packet_file(packet)
    result = generate_deck_prompt(
        "proposal", "Ridgeline Site Services", "Operational Intelligence Platform",
        provider, poll_interval=0.0, sleep=lambda _s: None,
    )
    assert result["status"] == "ok", result
    assert "bullet_selection" in result, (
        "the ok result must carry the selection; without it no UI can show it"
    )
    selection = result["bullet_selection"]
    assert selection and selection["panels"], selection
    # A fixture packet has no ranker and no `display_priority`, so the order is
    # the packet's own and the record must not imply otherwise.
    assert not selection["ranked"]


# --- the studio renders it --------------------------------------------------

SELECTION = {
    "panels": [
        {"panel": "today", "label": "TODAY", "role": "today_pain_bullets",
         "shown": 2, "of": 4,
         "kept": [{"text": "Captains handwrite the daily report on paper",
                   "source_position": 3},
                  {"text": "Fuel entry is optional across live projects",
                   "source_position": 1}],
         "dropped": [{"text": "A competitor benchmark that ranked down",
                      "source_position": 2},
                     {"text": "A data-availability caveat that ranked down",
                      "source_position": 4}],
         "ranked": True, "reordered": True, "room_px": 96.4,
         "overflowed": False, "note": "2 of 4 shown"},
        {"panel": "after", "label": "AFTER", "role": "after_capability_bullets",
         "shown": 1, "of": 1,
         "kept": [{"text": "One connected app replacing the clipboards",
                   "source_position": 1}],
         "dropped": [],
         "ranked": False, "reordered": False, "room_px": 96.4,
         "overflowed": False, "note": ""},
    ],
    "notes": ["today: repaired a duplicate index"],
    "trimmed": True,
    "ranked": True,
}


def _studio_with(selection):
    """The review studio, with a stubbed pipeline whose result carries
    ``selection``. Returns ``(client, packet, deck_dir)``."""
    from test_ui_review_surface import RUN_RESULT, _copy_packet, _studio

    deck_dir = tempfile.mkdtemp(prefix="selection-deck-")
    ui_app, client, deck_path = _studio(deck_dir)
    prompt_path = os.path.join(deck_dir, "generated-prompt-7.txt")
    ui_app.generate_and_save_deck = lambda *a, **k: dict(
        RUN_RESULT, deck_path=deck_path, prompt_path=prompt_path,
        bullet_selection=selection,
    )
    return client, _copy_packet(), deck_dir


def _run(client, packet):
    return run_deck(client, data={
        "deck_type": "status", "company": "Northwind", "project": "Impl",
        "packet": packet, "check_in_date": "2026-05-22",
    }).get_data(as_text=True)


def test_the_result_tab_shows_the_trim_and_the_bullets_it_dropped():
    """The defect in one test: a reviewer reading two bullets could not learn
    that the packet carried four, or read the two that were cut."""
    if not _HAVE_FLASK:
        return
    client, packet, deck_dir = _studio_with(SELECTION)
    try:
        html = _run(client, packet)
        assert "Slide 2 bullet selection" in html
        # The shown-of-total counts went with the rest of the card's prose
        # (Antonio, 2026-09-20). What a reviewer acts on is the copy itself, and
        # the dropped copy is still listed in full: somebody who disagrees with
        # the cut has to be able to read what was cut.
        assert "A competitor benchmark that ranked down" in html
        assert "A data-availability caveat that ranked down" in html
        assert "Captains handwrite the daily report on paper" in html
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


def test_the_result_tab_says_when_a_model_chose_the_order():
    if not _HAVE_FLASK:
        return
    client, packet, deck_dir = _studio_with(SELECTION)
    try:
        html = _run(client, packet)
        # "chosen by the ranking pass" and the packet-position column were both
        # cut on 2026-09-20 ("they don't need to know that ... we don't need to
        # number them"). A ranking answer that needed REPAIR still speaks,
        # because that is the case where the recorded order is less trustworthy.
        assert "needed repair" in html
        assert "repaired a duplicate index" in html
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


def test_a_run_with_no_selection_shows_no_card_at_all():
    """A status deck has no slide-2 panel. An empty card would read as a finding."""
    if not _HAVE_FLASK:
        return
    client, packet, deck_dir = _studio_with(None)
    try:
        html = _run(client, packet)
        assert "Edit this deck" in html, "the run itself must still render"
        assert "Slide 2 bullet selection" not in html
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


def test_the_selection_survives_a_flag_decision():
    """A reviewer confirming one claim must not lose the record of the trim —
    the same regression `test_ui_review_surface` pins for the guard reports."""
    if not _HAVE_FLASK:
        return
    client, packet, deck_dir = _studio_with(SELECTION)
    deck_path = os.path.join(deck_dir, "output-7.html")
    try:
        _run(client, packet)
        html = client.post("/gap-decision", data={
            "action": "resolve",
            "field": "workstreams[0].after.metrics[0].value",
            "deck_type": "status", "company": "Northwind", "project": "Impl",
            "packet": packet, "check_in_date": "2026-05-22",
            "deck_path": deck_path,
        }).get_data(as_text=True)
        assert "Slide 2 bullet selection" in html
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


def test_a_saved_deck_comes_back_carrying_its_selection():
    """The store's row is all that is left once the deck files are gone, which is
    what every redeploy of the hosted service produces. A reviewer opening that
    row has no other way to learn that the slide shows two of four."""
    if not _HAVE_FLASK:
        return
    from deck_store import list_decks

    store_dir = tempfile.mkdtemp(prefix="selection-store-")
    saved_env = os.environ.get("DECK_STORE_DIR")
    os.environ["DECK_STORE_DIR"] = store_dir
    client, packet, deck_dir = _studio_with(SELECTION)
    deck_path = os.path.join(deck_dir, "output-7.html")
    try:
        _run(client, packet)
        client.post("/save-deck", data={
            "path": deck_path, "deck_type": "status", "company": "Northwind",
            "project": "Impl", "packet": packet, "check_in_date": "2026-05-22",
        })
        rows = list_decks(company="Northwind", project="Impl",
                          store_path=os.path.join(store_dir, "decks.sqlite3"))
        assert rows, "expected the save to have written a row to the store"

        # The files the run produced are gone, as they are after a redeploy.
        shutil.rmtree(deck_dir, ignore_errors=True)
        html = client.get("/deck-view",
                          query_string={"id": rows[0]["id"]}).get_data(as_text=True)
        assert "Slide 2 bullet selection" in html
        assert "A competitor benchmark that ranked down" in html
    finally:
        if saved_env is None:
            os.environ.pop("DECK_STORE_DIR", None)
        else:
            os.environ["DECK_STORE_DIR"] = saved_env
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(store_dir, ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


# --- a recorded order the mapping layer threw away --------------------------
#
# The third state, added 2026-09-02. `_apply_display_priority` used to drop an
# order it could not line up against the displayed list and say nothing, so the
# card read "No ranking pass ran on this panel" for a panel where a pass had run,
# answered, and been ignored. On a live WTG run that sent the reviewer looking at
# the ranking pass, which was working fine, instead of at the mapping layer.

REFUSED = {
    "panels": [
        dict(SELECTION["panels"][0], ranked=False, reordered=False,
             rank_refused=("the recorded order names 4 bullet(s) and this "
                           "panel's ranked list holds 6 (10 on the slide, 4 "
                           "appended by the mapping layer after the pass ran), "
                           "so it is not a permutation of that list and the "
                           "panel keeps the order the packet stated.")),
        # Ranked and applied, so the panel that was refused is the only one
        # this fixture can be describing.
        dict(SELECTION["panels"][1], ranked=True, rank_refused=""),
    ],
    "notes": [],
    "not_applied": [
        {"panel": "today", "role": "today_pain_bullets",
         "reason": "the recorded order names 4 bullet(s) and this panel's "
                   "ranked list holds 6"},
    ],
    "trimmed": True,
    "ranked": False,
}


def test_a_refused_order_does_not_read_as_no_pass_having_run():
    """The lie, in one test. Both sentences are about the SAME panel state on
    screen, and only one of them is true."""
    if not _HAVE_FLASK:
        return
    client, packet, deck_dir = _studio_with(REFUSED)
    try:
        html = _run(client, packet)
        assert "No ranking pass ran on this panel" not in html
        assert "its order was not applied" in html
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


def test_a_refused_order_says_why_and_says_what_the_slide_shows_instead():
    """A reviewer has to be able to act on it: the reason names the mismatch, and
    the consequence names what they are actually looking at."""
    if not _HAVE_FLASK:
        return
    client, packet, deck_dir = _studio_with(REFUSED)
    try:
        html = _run(client, packet)
        assert "appended by the mapping layer" in html
        assert "not the ones a model judged most important" in html
        assert "could not be applied to" in html
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


def test_a_refusal_is_not_shown_as_one_of_the_passs_own_repairs():
    """Two different facts, and the card must not blur them: a repair is the pass
    salvaging its own answer, a refusal is the answer being discarded after the
    fact by a different layer."""
    if not _HAVE_FLASK:
        return
    client, packet, deck_dir = _studio_with(REFUSED)
    try:
        html = _run(client, packet)
        assert "needed repair before it" not in html, (
            "a refusal must not be reported as a repair"
        )
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


def test_a_panel_whose_order_stood_still_says_so():
    """The other two states are untouched."""
    if not _HAVE_FLASK:
        return
    client, packet, deck_dir = _studio_with(SELECTION)
    try:
        html = _run(client, packet)
        # A panel whose order simply stood says nothing at all now: the line
        # that used to say so was the normal case, and the card only speaks
        # about ordering when something went wrong with it.
        assert "its order was not applied" not in html
        assert "No ranking pass ran on this panel" not in html
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all bullet-selection surface tests passed")
