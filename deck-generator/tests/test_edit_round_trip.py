"""Round-trip tests for reviewer edits — the 2026-07-23 failure and its shape.

An edit is only trustworthy if it is reversible: stating an edit and then stating
its reverse has to put the deck back byte-for-byte, on a proposal deck and on a
status deck alike. These run against the file chain (revision files plus the
shared edit log), not just in memory, because that is where the failure
demonstrated at the 2026-07-23 sync lived: an edit computed from an older file
was numbered as if it followed the newest one, so the deck lost a change its own
edit history still listed.

Run with: python3 tests/test_edit_round_trip.py
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from html_edit_layer import (
    EditNotApplicable,
    apply_edit_and_save,
    apply_edits_and_save,
    current_revision_path,
    load_edit_log,
)

# Two decks in the two shapes the pipeline produces. Both carry a title slide and
# a figure a reviewer would restate, and neither is keyed to a real client. The
# proposal deck runs to five slides so the commercial figure sits on slide 5, the
# slide the failure was demonstrated on; slides are addressed by document order.
PROPOSAL_DECK = (
    "<!doctype html>\n<html><head><style>.slide{}</style></head><body>\n"
    '<section class="slide slide--dark" data-slide="1">\n'
    "  <h1>Northwind Operations Assessment</h1>\n"
    "  <p>Prepared for the board.</p>\n"
    "</section>\n"
    '<section class="slide" data-slide="2">\n  <h2>What we found</h2>\n</section>\n'
    '<section class="slide" data-slide="3">\n  <h2>What we would build</h2>\n</section>\n'
    '<section class="slide" data-slide="4">\n  <h2>How it runs</h2>\n</section>\n'
    '<section class="slide" data-slide="5">\n'
    "  <h2>Commercial terms</h2>\n"
    "  <p>QofAI investment: $250K</p>\n"
    "  <p>Client upfront: $75K</p>\n"
    "</section>\n"
    "</body></html>\n"
)

STATUS_DECK = (
    "<!doctype html>\n<html><head><style>.slide{}</style></head><body>\n"
    '<section class="slide slide--dark" data-slide="1">\n'
    "  <h1>Northwind Weekly Status</h1>\n"
    "</section>\n"
    '<section class="slide" data-slide="2">\n'
    "  <h2>Where the work stands</h2>\n"
    "  <p>Invoices processed per week: 1,400</p>\n"
    "</section>\n"
    "</body></html>\n"
)


# Two more shapes, for the edits whose source is markup rather than visible text:
# a title carrying an inline span (what the free-text interpreter copies to make a
# match unique) and a slide carrying the renderer's highlighted `[MISSING: ...]`
# marker (what the supply-missing form fills).
MARKUP_DECK = (
    "<!doctype html>\n<html><head><style>.slide{}</style></head><body>\n"
    '<section class="slide slide--dark" data-slide="1">\n'
    '  <h1>Operational Intelligence <span class="base">Project Planning.</span></h1>\n'
    "</section>\n"
    "</body></html>\n"
)

MARKER_DECK = (
    "<!doctype html>\n<html><head><style>.slide{}</style></head><body>\n"
    '<section class="slide" data-slide="1">\n'
    '  <p>Engagement lead: <span class="flag">[MISSING: engagement_lead]</span></p>\n'
    "</section>\n"
    "</body></html>\n"
)


def _write_deck(text, name="output-9.html"):
    tmp = tempfile.mkdtemp(prefix="round-trip-")
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _round_trip(deck, slide, before, after):
    """Apply ``before -> after`` on a deck file, then state the reverse. Returns
    ``(original_text, text_after_the_reverse)`` for an exact-equality assert."""
    path = _write_deck(deck)
    try:
        forward = apply_edit_and_save(path, slide, before, after)
        assert after in _read(forward["revision_path"]), "the stated edit must apply"
        back = apply_edit_and_save(forward["revision_path"], slide, after, before)
        return deck, _read(back["revision_path"])
    finally:
        _cleanup(path)


def test_the_2026_07_23_investment_edit_round_trips():
    # "On slide 5, change QofAI investment to 275K", then back again.
    original, reverted = _round_trip(PROPOSAL_DECK, 5, "$250K", "$275K")
    assert reverted == original, "the reverse must restore the deck exactly"


def test_title_slide_edit_round_trips_on_a_proposal_deck():
    original, reverted = _round_trip(
        PROPOSAL_DECK, 1, "Northwind Operations Assessment", "Northwind Assessment"
    )
    assert reverted == original


def test_title_slide_edit_round_trips_on_a_status_deck():
    original, reverted = _round_trip(
        STATUS_DECK, 1, "Northwind Weekly Status", "Northwind Status"
    )
    assert reverted == original


def test_figure_edit_round_trips_on_a_status_deck():
    original, reverted = _round_trip(STATUS_DECK, 2, "1,400", "1,650")
    assert reverted == original


def test_a_markup_source_with_a_plain_replacement_round_trips():
    # The case C1 handed to C2. The free-text interpreter copies a tag-bearing span
    # as `source` and returns plain text as `replacement` (its system prompt tells
    # it to repeat the tags; it does not always). That edit used to replace the
    # whole span, and its reverse was refused as a tag-bearing replacement on a
    # plain-text target — undoable through the revision chain, but not reversible
    # by a stated edit. Both directions are plain text swaps now.
    original, reverted = _round_trip(
        MARKUP_DECK, 1,
        '<span class="base">Project Planning.</span>',
        "Project Design.",
    )
    assert reverted == original


def test_supplying_a_missing_value_round_trips_with_its_flag_badge():
    # The other markup-to-plain-text shape: filling a `[MISSING: ...]` marker whose
    # source is the whole highlighted span. Filling it drops the badge with the
    # marker, and restoring the marker brings the badge back, byte for byte.
    path = _write_deck(MARKER_DECK)
    try:
        span = '<span class="flag">[MISSING: engagement_lead]</span>'
        filled = apply_edit_and_save(path, 1, span, "Dana Wu")
        text = _read(filled["revision_path"])
        assert "Dana Wu" in text
        assert 'class="flag"' not in text, "a supplied value is not a gap"
        back = apply_edit_and_save(
            filled["revision_path"], 1, "Dana Wu", "[MISSING: engagement_lead]"
        )
        assert _read(back["revision_path"]) == MARKER_DECK
    finally:
        _cleanup(path)


def test_the_same_edit_applied_twice_leaves_the_deck_alone_the_second_time():
    path = _write_deck(PROPOSAL_DECK)
    try:
        first = apply_edit_and_save(path, 5, "$250K", "$275K")
        once = _read(first["revision_path"])
        try:
            apply_edit_and_save(path, 5, "$250K", "$275K")
        except EditNotApplicable as exc:
            assert "not found" in str(exc)
        else:
            raise AssertionError("the source is already gone; the repeat must refuse")
        # nothing written, nothing logged twice, and the deck still reads as it did
        assert os.path.basename(current_revision_path(path)) == "output-9-r1.html"
        assert _read(current_revision_path(path)) == once
        assert len(load_edit_log(path)) == 1
    finally:
        _cleanup(path)


def test_two_different_edits_in_a_row_both_survive():
    path = _write_deck(PROPOSAL_DECK)
    try:
        apply_edit_and_save(path, 5, "$250K", "$275K")
        second = apply_edit_and_save(path, 5, "$75K", "$80K")
        head = _read(second["revision_path"])
        assert "$275K" in head and "$80K" in head
        apply_edit_and_save(path, 5, "$80K", "$75K")  # and the pair reverses
        last = apply_edit_and_save(path, 5, "$275K", "$250K")
        assert _read(last["revision_path"]) == PROPOSAL_DECK
    finally:
        _cleanup(path)


def test_an_edit_stated_from_an_older_path_does_not_drop_the_edits_since():
    # The 2026-07-23 failure. The Decks tab lists an original beside its revisions
    # and a reloaded page carries the path it was rendered with, so an edit can be
    # posted against the original after a revision exists. It must land on the deck
    # as it now stands rather than fork a new revision off the original.
    path = _write_deck(PROPOSAL_DECK)
    try:
        apply_edit_and_save(path, 5, "$250K", "$275K")
        forked = apply_edit_and_save(path, 1, "Northwind Operations Assessment",
                                     "Northwind Assessment")
        head = _read(forked["revision_path"])
        assert "Northwind Assessment" in head
        assert "$275K" in head, "the earlier edit must not be silently dropped"
        # the history and the deck agree: both edits are logged and both are on it
        log = load_edit_log(path)
        assert [e["after"] for e in log] == ["$275K", "Northwind Assessment"]
        assert log[1]["from"] == "output-9-r1.html"
    finally:
        _cleanup(path)


def test_a_free_text_batch_from_an_older_path_also_lands_on_the_current_deck():
    path = _write_deck(STATUS_DECK)
    try:
        apply_edit_and_save(path, 2, "1,400", "1,650")
        result = apply_edits_and_save(
            path, [{"slide": 1, "source": "Northwind Weekly Status",
                    "replacement": "Northwind Status"}],
            instruction="shorten the cover title",
        )
        head = _read(result["revision_path"])
        assert "Northwind Status" in head and "1,650" in head
    finally:
        _cleanup(path)


def _cleanup(path):
    shutil.rmtree(os.path.dirname(path), ignore_errors=True)


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"PASS  {test.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL  {test.__name__}: {e}")
    if failures:
        print(f"\n{failures} test(s) failed")
        sys.exit(1)
    print(f"\nAll {len(tests)} tests passed")
