"""Tests for the HTML edit layer — deterministic in-place reviewer edits.

Pure text functions run in memory; the ``*_and_save`` wrapper is exercised against
a temp directory so revision numbering and the edit log are tested for real without
touching any committed deck. No API key, no network.

Run with: python3 tests/test_html_edit_layer.py
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from display_text_guard import visible_tags
from html_edit_layer import (
    AMBIGUOUS_SOURCE,
    EditNotApplicable,
    apply_edit_and_save,
    apply_edits_and_save,
    apply_text_edit,
    base_stem,
    edit_log_path,
    find_slides,
    list_missing_markers,
    list_progress_items,
    load_edit_log,
    next_revision_path,
    slide_count,
    stamp_slide_ids,
    toggle_progress_item,
    toggle_progress_item_and_save,
    undo_last_edit,
)

# A minimal but realistic two-slide deck: a dark cover and a body slide carrying
# both a filled value and a `[MISSING: ...]` marker wrapped in the flag span the
# renderer uses. The word "Overview" appears on both slides (ambiguity fixture).
DECK = (
    "<!doctype html>\n<html><head><style>.slide{}</style></head><body>\n"
    '<section class="slide slide--dark">\n'
    "  <h1>Ridgeline Overview</h1>\n"
    "  <p>Prepared for the board.</p>\n"
    "</section>\n"
    '<section class="slide">\n'
    "  <h2>Overview</h2>\n"
    "  <p>Revenue was <b>$40M</b> last year.</p>\n"
    '  <p>Owner: <span class="flag">[MISSING: engagement_lead]</span></p>\n'
    "</section>\n"
    "</body></html>\n"
)


# A slide carrying a real progress tracker (the shape ``deck_renderer`` emits):
# two groups, one item with a ``.pdetail`` sub-span, one duplicate label across
# groups for the ambiguity case.
PROGRESS_DECK = (
    "<!doctype html>\n<html><body>\n"
    '<section class="slide">\n'
    '  <div class="pgroup">\n'
    '    <div class="pgroup-head"><span class="pgroup-name">DATA REQUESTS</span>'
    '<span class="badge">RECEIVED</span></div>\n'
    '    <div class="pitem"><span class="chk done">&#10003;</span>'
    '<span class="ptext">Quote templates</span></div>\n'
    '    <div class="pitem"><span class="chk pending"></span>'
    '<span class="ptext">Quentin<span class="pdetail">needed?</span></span></div>\n'
    "  </div>\n"
    '  <div class="pgroup">\n'
    '    <div class="pgroup-head"><span class="pgroup-name">WORKFLOW</span>'
    '<span class="badge">IN PROCESS</span></div>\n'
    '    <div class="pitem"><span class="chk in_process"></span>'
    '<span class="ptext">Mapping workflow</span></div>\n'
    "  </div>\n"
    "</section>\n"
    '<section class="slide">\n'
    '  <div class="pitem"><span class="chk done">&#10003;</span>'
    '<span class="ptext">Quote templates</span></div>\n'
    "</section>\n"
    "</body></html>\n"
)


def _write_deck(name="output-7.html", text=DECK):
    tmp = tempfile.mkdtemp(prefix="edit-layer-")
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


# ------------------------------- slide model -------------------------------

def test_find_slides_counts_and_orders():
    slides = find_slides(DECK)
    assert len(slides) == 2, slides
    assert [s["index"] for s in slides] == [1, 2]
    assert slide_count(DECK) == 2


def test_stamp_slide_ids_is_stable_and_idempotent():
    stamped = stamp_slide_ids(DECK)
    assert 'data-slide="1"' in stamped and 'data-slide="2"' in stamped
    # the dark variant class survives the stamp
    assert 'class="slide slide--dark"' in stamped
    # stamping again changes nothing
    assert stamp_slide_ids(stamped) == stamped


def test_stamp_is_noop_without_slides():
    plain = "<!doctype html>\n<html></html>"
    assert stamp_slide_ids(plain) == plain


# ------------------------------- edits -------------------------------------

def test_apply_edit_replaces_unique_source():
    new = apply_text_edit(DECK, 2, "$40M", "$42M")
    assert "$42M" in new and "$40M" not in new
    # only slide 2 changed; slide 1 text is intact
    assert "Ridgeline Overview" in new


def test_apply_edit_not_found_raises():
    try:
        apply_text_edit(DECK, 2, "nowhere-string", "x")
    except EditNotApplicable as e:
        assert "not found" in str(e)
    else:
        raise AssertionError("expected EditNotApplicable for missing source")


def test_apply_edit_ambiguous_within_slide_raises():
    # "Overview" appears once on slide 2 (unique there) but the guard is per-slide;
    # craft an ambiguous case: "Owner" repeated on the same slide.
    deck = DECK.replace("Owner:", "Owner: Owner:")
    try:
        apply_text_edit(deck, 2, "Owner:", "Lead:")
    except EditNotApplicable as e:
        assert "ambiguous" in str(e)
    else:
        raise AssertionError("expected EditNotApplicable for ambiguous source")


def test_apply_edit_same_text_on_two_slides_is_not_ambiguous():
    # "Overview" is on both slides but once each; addressing a slide disambiguates.
    new = apply_text_edit(DECK, 1, "Overview", "Summary")
    assert "Ridgeline Summary" in new
    # slide 2's "Overview" is untouched
    assert new.count("Overview") == 1


def test_apply_edit_bad_slide_index_raises():
    for bad in (0, 3, 99):
        try:
            apply_text_edit(DECK, bad, "Overview", "x")
        except EditNotApplicable as e:
            assert "does not exist" in str(e)
        else:
            raise AssertionError(f"expected EditNotApplicable for slide {bad}")


def test_tag_bearing_replacement_on_a_plain_target_lands_as_text():
    # Handed over from C1: refusing this (the old behaviour) is what made a
    # markup-to-plain-text edit un-reversible, because its reverse looks exactly
    # like this. Tags carry no display text of their own, so they are dropped
    # rather than escaped or inserted: the slide's own markup is untouched, the
    # words land, and nothing prints as a tag.
    new = apply_text_edit(DECK, 2, "$40M", '<span class="base">$42M</span>')
    assert "<b>$42M</b>" in new
    assert "&lt;span" not in new and not visible_tags(new)


def test_a_script_tag_in_a_replacement_never_reaches_the_slide():
    # The injection case, which is the same rule seen from the other side.
    new = apply_text_edit(DECK, 2, "$40M", "<script>alert(1)</script>")
    assert "<script>" not in new and "&lt;script&gt;" not in new
    assert "<b>alert(1)</b>" in new


def test_markup_source_with_a_plain_replacement_keeps_the_element():
    # The 2026-07-23 leak from the side C1 left open: the interpreter copies a
    # tag-bearing span as `source` and returns plain text as `replacement`. The
    # edit is about the text INSIDE the span, so the span survives and only its
    # words change — before, the whole span was replaced and the tags were gone.
    deck = (
        '<section class="slide">\n'
        '  <h1>Operational Intelligence <span class="base">Project Planning.</span></h1>\n'
        "</section>\n"
    )
    new = apply_text_edit(
        deck, 1, '<span class="base">Project Planning.</span>', "Project Design."
    )
    assert '<span class="base">Project Design.</span>' in new
    assert not visible_tags(new)


def test_a_source_that_spans_markup_is_refused():
    # There is no single place the new text belongs, and flattening the element
    # would silently drop the `<b>`. Refuse rather than guess.
    try:
        apply_text_edit(DECK, 2, "was <b>$40M</b> last", "was $42M last")
    except EditNotApplicable as e:
        assert "spans markup" in str(e)
    else:
        raise AssertionError("expected EditNotApplicable for a source crossing markup")


def test_reviewer_arithmetic_brackets_are_escaped_not_eaten():
    # "margin < 5 and headcount > 3" is arithmetic, not markup: it must survive as
    # the reviewer typed it, escaped for the document and read back identically.
    new = apply_text_edit(DECK, 2, "$40M", "margin < 5 and headcount > 3")
    assert "margin &lt; 5 and headcount &gt; 3" in new
    assert not visible_tags(new)


def test_plain_replacement_special_chars_are_escaped():
    # Plain reviewer text with special chars (no tags) is still escaped as text.
    new = apply_text_edit(DECK, 2, "$40M", "R&D spend")
    assert "R&amp;D spend" in new
    assert "R&D spend" not in new


def test_markup_source_replacement_keeps_tags_live_not_escaped():
    # Antonio's bug (2026-07-23 sync): removing "Planning" from a title. The
    # interpreter copies the whole span as source and returns the same span with
    # edited inner text as replacement. The tags must stay live markup, not get
    # escaped into visible text on the slide.
    deck = (
        '<section class="slide slide--dark">\n'
        '  <h1>Operational Intelligence '
        '<span class="base">Project Planning.</span></h1>\n'
        "</section>\n"
    )
    new = apply_text_edit(
        deck, 1,
        '<span class="base">Project Planning.</span>',
        '<span class="base">Project.</span>',
    )
    assert '<span class="base">Project.</span>' in new
    assert "Project Planning." not in new
    # the tag characters must NOT be escaped into literal text
    assert "&lt;span" not in new and "&gt;" not in new


def test_edit_can_delete_source_with_empty_replacement():
    marker = '<span class="flag">[MISSING: engagement_lead]</span>'
    new = apply_text_edit(DECK, 2, marker, "")
    assert "[MISSING:" not in new
    assert 'class="flag"' not in new


# ------------------------------- missing markers ---------------------------

def test_list_missing_markers_surfaces_field_and_span_source():
    markers = list_missing_markers(DECK)
    assert len(markers) == 1, markers
    m = markers[0]
    assert m["slide"] == 2
    assert m["field"] == "engagement_lead"
    # source is the whole flag span so filling it drops the highlight too
    assert m["source"] == '<span class="flag">[MISSING: engagement_lead]</span>'


def test_fill_missing_marker_via_source():
    m = list_missing_markers(DECK)[0]
    new = apply_text_edit(DECK, m["slide"], m["source"], "Dana Wu")
    assert "Dana Wu" in new
    assert "[MISSING:" not in new
    assert 'class="flag"' not in new  # highlight removed with the span


def test_no_missing_markers_when_deck_is_complete():
    complete = DECK.replace(
        '<span class="flag">[MISSING: engagement_lead]</span>', "Dana Wu"
    )
    assert list_missing_markers(complete) == []


# ------------------------------ progress checkbox ---------------------------

def test_list_progress_items_reads_label_and_state_in_order():
    items = list_progress_items(PROGRESS_DECK)
    assert [(i["slide"], i["label"], i["state"]) for i in items] == [
        (1, "Quote templates", "done"),
        (1, "Quentinneeded?", "pending"),
        (1, "Mapping workflow", "in_process"),
        (2, "Quote templates", "done"),
    ]


def test_toggle_moves_class_and_glyph_together():
    new = toggle_progress_item(PROGRESS_DECK, 1, "Quentinneeded?", "done")
    assert '<span class="chk done">&#10003;</span><span class="ptext">Quentin' in new
    assert '<span class="chk pending">' not in new
    # the label (and its pdetail) is untouched
    assert '<span class="pdetail">needed?</span>' in new


def test_toggle_to_pending_clears_the_glyph():
    done_first = toggle_progress_item(PROGRESS_DECK, 1, "Quentinneeded?", "in_process")
    new = toggle_progress_item(done_first, 1, "Mapping workflow", "pending")
    assert '<span class="chk pending"></span><span class="ptext">Mapping workflow' in new


def test_toggle_leaves_the_other_slide_untouched():
    # "Quote templates" is on both slides; addressing slide 1 leaves slide 2 alone.
    new = toggle_progress_item(PROGRESS_DECK, 1, "Quote templates", "pending")
    assert new.count('<span class="chk done">&#10003;</span><span class="ptext">Quote') == 1


def test_toggle_missing_label_raises():
    try:
        toggle_progress_item(PROGRESS_DECK, 1, "Nonexistent item", "done")
    except EditNotApplicable as e:
        assert "no progress item" in str(e)
    else:
        raise AssertionError("expected EditNotApplicable for a missing label")


def test_toggle_ambiguous_label_raises():
    # "Quote templates" appears on both slides but the guard is per-slide;
    # duplicate it within slide 1 to force an in-slide ambiguity.
    dup_item = ('    <div class="pitem"><span class="chk done">&#10003;</span>'
                '<span class="ptext">Quote templates</span></div>\n')
    dup = PROGRESS_DECK.replace(dup_item, dup_item * 2, 1)
    try:
        toggle_progress_item(dup, 1, "Quote templates", "pending")
    except EditNotApplicable as e:
        assert "ambiguous" in str(e)
    else:
        raise AssertionError("expected EditNotApplicable for an ambiguous label")


def test_toggle_invalid_state_raises():
    try:
        toggle_progress_item(PROGRESS_DECK, 1, "Quote templates", "complete")
    except EditNotApplicable as e:
        assert "not a progress state" in str(e)
    else:
        raise AssertionError("expected EditNotApplicable for an invalid state")


def test_apply_text_edit_cannot_make_this_edit():
    # The reason the toggle needs its own function rather than a wider
    # apply_text_edit: naming the whole chk+ptext fragment as a source (the
    # only way to include the checkbox at all) crosses the nested `.pdetail`
    # markup inside `.ptext`, which apply_text_edit refuses by design.
    source = ('<span class="chk pending"></span>'
              '<span class="ptext">Quentin<span class="pdetail">needed?</span></span>')
    try:
        apply_text_edit(PROGRESS_DECK, 1, source, '<span class="chk done">&#10003;</span>')
    except EditNotApplicable as e:
        assert "spans markup" in str(e)
    else:
        raise AssertionError("expected apply_text_edit to refuse a source spanning markup")


# ------------------------------- revisions ---------------------------------

def test_base_stem_strips_revision_suffix():
    assert base_stem("output-4.html") == "output-4"
    assert base_stem("/a/b/output-4-r3.html") == "output-4"
    assert base_stem("output-12-r10.html") == "output-12"


def test_revision_numbering_increments():
    path = _write_deck("output-7.html")
    try:
        assert os.path.basename(next_revision_path(path)) == "output-7-r1.html"
        # simulate an existing r1, r2
        directory = os.path.dirname(path)
        for k in (1, 2):
            open(os.path.join(directory, f"output-7-r{k}.html"), "w").close()
        assert os.path.basename(next_revision_path(path)) == "output-7-r3.html"
        # numbering keys off the base, so asking from a revision agrees
        r2 = os.path.join(directory, "output-7-r2.html")
        assert os.path.basename(next_revision_path(r2)) == "output-7-r3.html"
    finally:
        _cleanup(path)


def test_apply_edit_and_save_writes_revision_and_log():
    path = _write_deck("output-7.html")
    try:
        result = apply_edit_and_save(
            path, 2, "$40M", "$42M", kind="content", author="Antonio",
            now="2026-07-21T00:00:00+00:00",
        )
        # original is preserved, revision written separately
        assert os.path.isfile(path), "original must survive"
        assert result["revision_name"] == "output-7-r1.html"
        assert os.path.isfile(result["revision_path"])
        with open(result["revision_path"], encoding="utf-8") as f:
            assert "$42M" in f.read()
        with open(path, encoding="utf-8") as f:
            assert "$40M" in f.read(), "original unchanged"
        # log entry recorded with the full audit trail
        log = load_edit_log(path)
        assert len(log) == 1
        e = log[0]
        assert e["slide"] == 2 and e["kind"] == "content"
        assert e["before"] == "$40M" and e["after"] == "$42M"
        assert e["author"] == "Antonio" and e["revision"] == "output-7-r1.html"
        assert e["created"] == "2026-07-21T00:00:00+00:00"
    finally:
        _cleanup(path)


def test_toggle_progress_item_and_save_writes_revision_and_log():
    path = _write_deck("output-7.html", PROGRESS_DECK)
    try:
        result = toggle_progress_item_and_save(
            path, 1, "Quentinneeded?", "done", author="Antonio",
            now="2026-08-09T00:00:00+00:00",
        )
        assert os.path.isfile(path), "original must survive"
        assert result["revision_name"] == "output-7-r1.html"
        with open(result["revision_path"], encoding="utf-8") as f:
            html = f.read()
        assert '<span class="chk done">&#10003;</span><span class="ptext">Quentin' in html
        with open(path, encoding="utf-8") as f:
            assert '<span class="chk pending">' in f.read(), "original unchanged"
        log = load_edit_log(path)
        assert len(log) == 1
        e = log[0]
        # before/after name the item and both states, so the reviewer reading
        # the history sees which item moved and which way.
        assert e["slide"] == 1 and e["kind"] == "content"
        assert e["before"] == "Quentinneeded?: pending"
        assert e["after"] == "Quentinneeded?: done"
        assert e["author"] == "Antonio" and e["revision"] == "output-7-r1.html"
    finally:
        _cleanup(path)


def test_second_edit_chains_and_appends_log():
    path = _write_deck("output-7.html")
    try:
        first = apply_edit_and_save(path, 2, "$40M", "$42M", now="2026-07-21T00:00:00+00:00")
        # edit the revision, not the original
        second = apply_edit_and_save(
            first["revision_path"], 1, "Ridgeline Overview", "Ridgeline Review",
            now="2026-07-21T00:01:00+00:00",
        )
        assert second["revision_name"] == "output-7-r2.html"
        with open(second["revision_path"], encoding="utf-8") as f:
            html = f.read()
        assert "Ridgeline Review" in html and "$42M" in html
        # both edits share the one base-keyed log
        log = load_edit_log(path)
        assert len(log) == 2
        assert log[1]["from"] == "output-7-r1.html"
        assert os.path.basename(edit_log_path(first["revision_path"])) == "output-7.edits.json"
    finally:
        _cleanup(path)


def test_failed_edit_writes_nothing():
    path = _write_deck("output-7.html")
    try:
        try:
            apply_edit_and_save(path, 2, "not-present", "x")
        except EditNotApplicable:
            pass
        else:
            raise AssertionError("expected EditNotApplicable")
        # no revision, no log
        directory = os.path.dirname(path)
        assert os.listdir(directory) == ["output-7.html"], os.listdir(directory)
        assert load_edit_log(path) == []
    finally:
        _cleanup(path)


# ------------------------- batch edits (free-text) -------------------------
# `apply_edits_and_save` applies a whole interpreter-produced batch as ONE
# revision, logs each swap, preserves the original, and skips (not aborts on) an
# edit whose source can't be located.

def test_apply_edits_and_save_writes_one_revision_for_a_batch():
    path = _write_deck("output-7.html")
    try:
        edits = [
            {"slide": 2, "source": "$40M", "replacement": "$42M", "note": "revenue"},
            {"slide": 1, "source": "Ridgeline Overview",
             "replacement": "Ridgeline Review", "note": "title"},
        ]
        result = apply_edits_and_save(
            path, edits, kind="content", author="Antonio",
            instruction="bump revenue to $42M and rename the cover",
            now="2026-07-21T00:00:00+00:00",
        )
        # exactly one revision file for the whole batch
        assert result["revision_name"] == "output-7-r1.html"
        assert _revisions(path) == ["output-7-r1.html"]
        assert len(result["applied"]) == 2 and result["failed"] == []
        # both swaps landed in the single revision
        with open(result["revision_path"], encoding="utf-8") as f:
            rev = f.read()
        assert "$42M" in rev and "Ridgeline Review" in rev
        # original preserved untouched
        with open(path, encoding="utf-8") as f:
            orig = f.read()
        assert "$40M" in orig and "Ridgeline Overview" in orig
        # one log entry per applied edit, each carrying the instruction
        log = load_edit_log(path)
        assert len(log) == 2, log
        assert all(e["revision"] == "output-7-r1.html" for e in log)
        assert all(e["instruction"] == "bump revenue to $42M and rename the cover"
                   for e in log)
        assert all(e["author"] == "Antonio" for e in log)
        assert {e["before"] for e in log} == {"$40M", "Ridgeline Overview"}
    finally:
        _cleanup(path)


def test_apply_edits_and_save_skips_a_failed_edit_but_keeps_the_good_ones():
    path = _write_deck("output-7.html")
    try:
        edits = [
            {"slide": 2, "source": "$40M", "replacement": "$42M"},
            {"slide": 2, "source": "not-on-this-slide", "replacement": "x"},
        ]
        result = apply_edits_and_save(path, edits, now="2026-07-21T00:00:00+00:00")
        # the good edit applied; the bad one is reported, not fatal
        assert len(result["applied"]) == 1
        assert len(result["failed"]) == 1
        assert result["failed"][0]["source"] == "not-on-this-slide"
        assert "not found" in result["failed"][0]["reason"]
        with open(result["revision_path"], encoding="utf-8") as f:
            assert "$42M" in f.read()
        # only the applied edit is logged
        assert len(load_edit_log(path)) == 1
    finally:
        _cleanup(path)


def test_apply_edits_and_save_raises_and_writes_nothing_when_none_apply():
    path = _write_deck("output-7.html")
    try:
        edits = [
            {"slide": 2, "source": "absent-one", "replacement": "x"},
            {"slide": 2, "source": "absent-two", "replacement": "y"},
        ]
        try:
            apply_edits_and_save(path, edits)
        except EditNotApplicable as e:
            # the combined reason mentions each failure
            assert "absent-one" not in str(e)  # reason is about the source text
            assert "not found" in str(e)
        else:
            raise AssertionError("expected EditNotApplicable when no edit applies")
        assert _revisions(path) == [], "nothing written when the whole batch fails"
        assert load_edit_log(path) == []
    finally:
        _cleanup(path)


# --------------------- aiming at a repeated source --------------------------

# Four next-step rows on one slide, each carrying its own `[MISSING: week]`. The
# marker text is identical on all four, which is the whole point: the string
# cannot say which row it means and only the position can. The shape the live
# proposal deck of 2026-08-20 carried, minus the surrounding copy.
REPEATED_DECK = (
    "<!doctype html>\n<html><body>\n"
    '<section class="slide">\n'
    "  <h2>Next steps</h2>\n"
    '  <p>01 Approve plan <span class="flag">[MISSING: week]</span></p>\n'
    '  <p>02 Sign contract <span class="flag">[MISSING: week]</span></p>\n'
    '  <p>03 Kick off <span class="flag">[MISSING: week]</span></p>\n'
    '  <p>04 Pick a pilot <span class="flag">[MISSING: week]</span></p>\n'
    "</section>\n"
    "</body></html>\n"
)

MARKER = '<span class="flag">[MISSING: week]</span>'


def _weeks(html):
    """What each of the four rows reads as, in document order."""
    import re
    return re.findall(r"<p>0\d [^<]*?(?:<span class=\"flag\">)?(\[MISSING: week\]|"
                      r"WK \d+)", html)


def test_an_unindexed_edit_still_refuses_a_repeated_source():
    """The contract the free-text interpreter depends on, unchanged. It names spans
    by text alone, so a second match means it has not identified one place, and
    editing the first would be a guess wearing the look of a decision."""
    try:
        apply_text_edit(REPEATED_DECK, 1, MARKER, "WK 2")
    except EditNotApplicable as exc:
        assert "ambiguous" in str(exc) and "4 times" in str(exc), str(exc)
    else:
        assert False, "a repeated source with no position must be refused"


def test_an_indexed_edit_fills_exactly_the_one_it_names():
    for nth in (1, 2, 3, 4):
        out = apply_text_edit(REPEATED_DECK, 1, MARKER, "WK 2", occurrence=nth)
        assert out.count(MARKER) == 3, nth
        assert _weeks(out) == ["WK 2" if k == nth else "[MISSING: week]"
                              for k in (1, 2, 3, 4)], nth


def test_an_index_past_the_end_is_refused_and_says_how_many_there_are():
    try:
        apply_text_edit(REPEATED_DECK, 1, MARKER, "WK 2", occurrence=5)
    except EditNotApplicable as exc:
        assert "occurrence 5" in str(exc) and "appears 4 times" in str(exc), str(exc)
    else:
        assert False, "an index the slide cannot satisfy must be refused"


def test_occurrence_zero_is_refused_rather_than_read_as_the_first():
    """1-based, and a 0 is a caller bug rather than a synonym for the first. Reading
    it as the first would fill a marker nobody named."""
    try:
        apply_text_edit(REPEATED_DECK, 1, MARKER, "WK 2", occurrence=0)
    except EditNotApplicable as exc:
        assert "not a position" in str(exc), str(exc)
    else:
        assert False, "occurrence 0 must be refused"


def test_an_index_of_one_on_a_unique_source_is_the_same_edit():
    """Every marker carries a position, including the ones whose text is already
    unique, so the indexed path has to agree with the unique path there."""
    assert (apply_text_edit(DECK, 2, "$40M", "$42M", occurrence=1)
            == apply_text_edit(DECK, 2, "$40M", "$42M"))


def test_list_missing_markers_numbers_a_repeat_in_document_order():
    got = [(m["field"], m["occurrence"], m["occurrences"])
           for m in list_missing_markers(REPEATED_DECK)]
    assert got == [("week", 1, 4), ("week", 2, 4), ("week", 3, 4),
                   ("week", 4, 4)], got


def test_the_numbering_is_what_apply_text_edit_takes():
    """The two halves of the fix, joined. Filling each marker the listing reported
    with its own value has to leave the four rows reading in the order the listing
    reported them, which is the only thing that makes the enumeration trustworthy
    as an address.

    Backwards, because a fill consumes its marker and renumbers the ones after it,
    so an enumeration taken once and spent over several edits is only valid from
    the end. That is the rule `apply_edits_and_save` applies for its callers."""
    html = REPEATED_DECK
    for marker in reversed(list_missing_markers(REPEATED_DECK)):
        html = apply_text_edit(html, marker["slide"], marker["source"],
                              f"WK {marker['occurrence']}",
                              occurrence=marker["occurrence"])
    assert _weeks(html) == ["WK 1", "WK 2", "WK 3", "WK 4"], _weeks(html)
    assert "[MISSING: week]" not in html


def test_a_stale_index_alone_misfires_silently():
    """Why the guard below exists. Spending a one-shot enumeration front to back
    asks for occurrence 3 of a string that now appears three times, and there IS a
    third — the one that used to be fourth. So the position resolves, the edit
    applies, and a reviewer's value lands on a row nobody named with nothing on the
    deck to show it. Pinned as the behaviour of an index passed on its own, which is
    why `expect_occurrences` is the advised way to pass one."""
    markers = list_missing_markers(REPEATED_DECK)
    html = apply_text_edit(REPEATED_DECK, 1, markers[0]["source"], "WK 1",
                           occurrence=1)
    html = apply_text_edit(html, 1, markers[2]["source"], "WK 3",
                           occurrence=markers[2]["occurrence"])
    assert _weeks(html) == ["WK 1", "[MISSING: week]", "[MISSING: week]", "WK 3"], (
        "the value landed on row 4, which is the misfire the guard has to catch")


def test_the_expected_total_turns_a_stale_index_into_a_refusal():
    """The guard. A total that no longer matches is proof the enumeration is stale,
    and that is checkable where the position alone is not. Refusing beats writing a
    wrong number onto a client deck."""
    markers = list_missing_markers(REPEATED_DECK)
    html = apply_text_edit(REPEATED_DECK, 1, markers[0]["source"], "WK 1",
                           occurrence=1, expect_occurrences=4)
    try:
        apply_text_edit(html, 1, markers[2]["source"], "WK 3",
                        occurrence=markers[2]["occurrence"],
                        expect_occurrences=markers[2]["occurrences"])
    except EditNotApplicable as exc:
        assert "3 times, not the 4" in str(exc), str(exc)
        assert "reload the deck" in str(exc), str(exc)
    else:
        assert False, "a stale enumeration must be refused, never acted on"


def test_the_expected_total_does_not_refuse_a_batch_s_own_fills():
    """The guard has to tell a stale list apart from a batch doing its job. Four
    edits from one listing legitimately take the count from four to zero, and
    reading the batch's own progress as staleness would refuse every fan-out."""
    path = _write_deck("output-9.html", REPEATED_DECK)
    try:
        edits = [{"slide": 1, "source": MARKER, "replacement": f"WK {nth}",
                  "occurrence": nth, "occurrences": 4} for nth in (1, 2, 3, 4)]
        result = apply_edits_and_save(path, edits, atomic=True)
        assert len(result["applied"]) == 4, result["failed"]
        with open(result["revision_path"], encoding="utf-8") as f:
            assert _weeks(f.read()) == ["WK 1", "WK 2", "WK 3", "WK 4"]
    finally:
        _cleanup(path)


def test_one_row_posted_from_a_stale_page_is_refused():
    """The realistic staleness case, because the card posts one target per row. The
    page says this is #2 of 4; two of those four have since been filled from
    another tab, so #2 now names a different row. Refused, and the message tells the
    reviewer to reload rather than leaving them to wonder."""
    two_left = REPEATED_DECK.replace(MARKER, "WK 0", 2)
    path = _write_deck("output-9.html", two_left)
    try:
        try:
            apply_edits_and_save(path, [{"slide": 1, "source": MARKER,
                                         "replacement": "WK 9", "occurrence": 2,
                                         "occurrences": 4}], atomic=True)
        except EditNotApplicable as exc:
            assert "deck has changed" in str(exc), str(exc)
        else:
            assert False, "a stale position must not be applied"
        assert not _revisions(path), "nothing written"
    finally:
        _cleanup(path)


def test_a_whole_batch_from_a_stale_listing_writes_nothing():
    """The same page, submitting all four. The refusals arrive as overshoots rather
    than as the staleness message (the batch's own subtraction accounts for the two
    it expects to consume), but atomic is what matters here: two values landing on
    rows picked by arithmetic is the outcome being refused."""
    two_left = REPEATED_DECK.replace(MARKER, "WK 0", 2)
    path = _write_deck("output-9.html", two_left)
    try:
        edits = [{"slide": 1, "source": MARKER, "replacement": f"WK {nth}",
                  "occurrence": nth, "occurrences": 4} for nth in (1, 2, 3, 4)]
        try:
            apply_edits_and_save(path, edits, atomic=True)
        except EditNotApplicable as exc:
            assert "has to reach all of them or none" in str(exc), str(exc)
        else:
            assert False, "a stale listing must not be applied"
        assert not _revisions(path), "nothing written"
    finally:
        _cleanup(path)


def test_a_batch_fills_four_identical_markers_in_one_revision():
    """The hazard the batch layer has to handle. Filling occurrence 1 consumes it,
    so occurrences 2-4 renumber down; applying the edits in the caller's order
    would send edit 2 at what was edit 3's marker. Only the values landing in the
    order they were given proves the batch walked them backwards."""
    path = _write_deck("output-9.html", REPEATED_DECK)
    try:
        edits = [{"slide": 1, "source": MARKER, "replacement": f"WK {nth}",
                  "occurrence": nth} for nth in (1, 2, 3, 4)]
        result = apply_edits_and_save(path, edits, atomic=True,
                                      now="2026-08-20T00:00:00+00:00")
        with open(result["revision_path"], encoding="utf-8") as f:
            out = f.read()
        assert _weeks(out) == ["WK 1", "WK 2", "WK 3", "WK 4"], _weeks(out)
        assert "[MISSING: week]" not in out
        # One revision, four log entries, reported in the caller's order rather
        # than the order the batch happened to walk them in.
        assert len(result["applied"]) == 4
        assert [e["replacement"] for e in result["applied"]] == [
            "WK 1", "WK 2", "WK 3", "WK 4"]
        log = load_edit_log(path)
        assert [e["occurrence"] for e in log] == [1, 2, 3, 4], log
        assert len({e["revision"] for e in log}) == 1, "one reviewer action, one revision"
    finally:
        _cleanup(path)


def test_a_batch_given_the_positions_out_of_order_still_lands_them_right():
    """The ordering is the batch layer's job, not the caller's. A form posting its
    rows in any order gets the same deck."""
    path = _write_deck("output-9.html", REPEATED_DECK)
    try:
        edits = [{"slide": 1, "source": MARKER, "replacement": f"WK {nth}",
                  "occurrence": nth} for nth in (3, 1, 4, 2)]
        result = apply_edits_and_save(path, edits, atomic=True)
        with open(result["revision_path"], encoding="utf-8") as f:
            assert _weeks(f.read()) == ["WK 1", "WK 2", "WK 3", "WK 4"]
    finally:
        _cleanup(path)


def test_a_blank_occurrence_from_a_form_reads_as_no_position():
    """An HTML form says "not given" with an empty string, and the absent reading is
    the safe one: it asks for a unique match instead of aiming somewhere nobody
    named. So an older page that posts no positions keeps working."""
    path = _write_deck("output-9.html")
    try:
        result = apply_edits_and_save(
            path, [{"slide": 2, "source": "$40M", "replacement": "$42M",
                    "occurrence": ""}])
        assert len(result["applied"]) == 1
        assert "occurrence" not in result["applied"][0]
        assert "occurrence" not in load_edit_log(path)[0]
    finally:
        _cleanup(path)


# ------------------------------- undo --------------------------------------

def test_undo_removes_top_revision_and_reverts_to_prior():
    path = _write_deck("output-7.html")
    try:
        first = apply_edit_and_save(path, 2, "$40M", "$42M", now="2026-07-21T00:00:00+00:00")
        apply_edit_and_save(first["revision_path"], 2, "$42M", "$44M",
                            now="2026-07-21T00:01:00+00:00")
        assert _revisions(path) == ["output-7-r1.html", "output-7-r2.html"]
        out = undo_last_edit(path)
        assert out["undone"] is True
        assert out["removed_revision"] == "output-7-r2.html"
        assert os.path.basename(out["current_path"]) == "output-7-r1.html"
        # r2 is gone, r1 stays; the log is trimmed back to the first edit only
        assert _revisions(path) == ["output-7-r1.html"]
        log = load_edit_log(path)
        assert len(log) == 1 and log[0]["after"] == "$42M"
    finally:
        _cleanup(path)


def test_undo_from_only_revision_reverts_to_original_and_clears_log():
    path = _write_deck("output-7.html")
    try:
        apply_edit_and_save(path, 2, "$40M", "$42M")
        out = undo_last_edit(path)
        assert out["undone"] is True
        assert os.path.basename(out["current_path"]) == "output-7.html"
        assert _revisions(path) == []
        assert load_edit_log(path) == [], "empty log file is removed"
    finally:
        _cleanup(path)


def test_undo_reverses_a_toggle():
    # undo_last_edit keys off revision files and log entries only, so a toggle
    # (which writes both like any other edit) is undone for free.
    path = _write_deck("output-7.html", PROGRESS_DECK)
    try:
        toggle_progress_item_and_save(path, 1, "Quentinneeded?", "done")
        assert _revisions(path) == ["output-7-r1.html"]
        out = undo_last_edit(path)
        assert out["undone"] is True
        assert os.path.basename(out["current_path"]) == "output-7.html"
        assert _revisions(path) == []
        assert load_edit_log(path) == []
        with open(path, encoding="utf-8") as f:
            assert '<span class="chk pending">' in f.read(), "packet-facing file untouched"
    finally:
        _cleanup(path)


def test_undo_of_a_batch_drops_all_that_revision_s_log_entries():
    path = _write_deck("output-7.html")
    try:
        edits = [
            {"slide": 2, "source": "$40M", "replacement": "$42M"},
            {"slide": 1, "source": "Ridgeline Overview", "replacement": "Ridgeline Review"},
        ]
        apply_edits_and_save(path, edits, now="2026-07-21T00:00:00+00:00")
        assert len(load_edit_log(path)) == 2
        out = undo_last_edit(path)
        assert out["undone"] is True
        assert _revisions(path) == []
        assert load_edit_log(path) == []
    finally:
        _cleanup(path)


def test_undo_on_unedited_deck_is_a_safe_noop():
    path = _write_deck("output-7.html")
    try:
        out = undo_last_edit(path)
        assert out["undone"] is False
        assert "nothing to undo" in out["reason"]
        assert _revisions(path) == []
    finally:
        _cleanup(path)


def _revisions(deck_path):
    """The revision files (`<base>-rK.html`) beside a deck, sorted."""
    directory = os.path.dirname(deck_path)
    base = base_stem(deck_path)
    return sorted(n for n in os.listdir(directory)
                  if n.startswith(base + "-r") and n.endswith(".html"))


def _cleanup(path):
    import shutil
    shutil.rmtree(os.path.dirname(path), ignore_errors=True)


# ------------------------------------------------------------------------------
# PART ONE ITEM 6: the house format rules on the edit path.
#
# An em dash reached a rendered slide during a live edit in front of Casey and
# Jordan ([17:50] of the 2026-08-20 demo). The guardrail did not leak: the render
# leg gated everything it wrote and the edit path called the gate nowhere at all.
# ------------------------------------------------------------------------------

EM = "\u2014"


def test_an_edited_em_dash_never_reaches_the_slide():
    """The demo defect, as a test."""
    out = apply_text_edit(
        DECK, 2, "Revenue was", f"Revenue held {EM} broadly flat {EM} last year")
    assert EM not in out
    assert "&mdash;" not in out


def test_the_edit_is_gated_the_way_its_own_spot_would_have_been():
    """A dash inside a `<p>` becomes a comma; one in a header becomes a spaced
    hyphen. Same rule the render leg applies, decided by the same open-tag
    stack, so an edit does not read differently from the line beside it."""
    prose = apply_text_edit(DECK, 1, "Prepared for the board.",
                            f"On track {EM} three sites are live.")
    assert "On track, three sites are live." in prose

    header = apply_text_edit(DECK, 2, "Overview", f"Overview {EM} phase two")
    assert "Overview - phase two" in header


def test_a_bracketing_pair_becomes_parentheses_on_the_edit_path_too():
    out = apply_text_edit(DECK, 1, "Prepared for the board.",
                          f"Phase 2 {EM} discovery {EM} starts in May.")
    assert "Phase 2 (discovery) starts in May." in out


def test_an_edited_exclamation_point_becomes_a_period():
    out = apply_text_edit(DECK, 1, "Prepared for the board.", "A real gain!")
    assert "A real gain." in out
    assert "!" not in out.split("<body>")[1]


def test_gating_an_edit_moves_no_figure():
    """The gate's hard constraint holds on this path because it is the same
    mask: every factual span is masked before a rule runs and restored after."""
    out = apply_text_edit(DECK, 2, "Revenue was",
                          f"Revenue of $40M {EM} up 12% {EM} on 2025")
    assert "$40M" in out and "12%" in out and "2025" in out


def test_filling_a_missing_marker_is_gated_as_well():
    """A fill is the same exact swap, so it goes under the same rules."""
    out = apply_text_edit(DECK, 2, "[MISSING: engagement_lead]",
                          f"A. Rodriguez {EM} interim")
    assert EM not in out
    # A comma, not a spaced hyphen: the marker sits inside a `<p>`, so the spot
    # is prose and the gate says so. Checked against what it actually produced.
    assert "A. Rodriguez, interim" in out


def test_the_log_records_what_landed_and_not_what_was_typed():
    """The reversibility rule. A logged "after" naming a string that is not on
    the deck would be an audit trail describing a document that never existed,
    and the reverse swap would not find its source."""
    with tempfile.TemporaryDirectory() as tmp:
        deck = os.path.join(tmp, "output-3.html")
        with open(deck, "w", encoding="utf-8") as f:
            f.write(DECK)
        typed = f"On track {EM} three sites live."
        saved = apply_edit_and_save(
            deck, 1, "Prepared for the board.", typed, author="a@b.c")
        with open(saved["revision_path"], encoding="utf-8") as f:
            written = f.read()
        entry = load_edit_log(deck)[-1]

        assert entry["after"] != typed, "the typed text is not what landed"
        assert entry["after"] == "On track, three sites live."
        assert entry["after"] in written


def test_an_edit_and_its_reverse_still_restore_the_deck_byte_for_byte():
    """The edit layer's whole guarantee, and what the gate could have broken.
    Reversing uses the LOGGED after as its source, which is why that has to be
    the string the gate produced."""
    with tempfile.TemporaryDirectory() as tmp:
        deck = os.path.join(tmp, "output-4.html")
        with open(deck, "w", encoding="utf-8") as f:
            f.write(DECK)
        saved = apply_edit_and_save(
            deck, 1, "Prepared for the board.", f"On track {EM} three sites live.")
        entry = load_edit_log(deck)[-1]
        with open(saved["revision_path"], encoding="utf-8") as f:
            edited = f.read()

        back = apply_text_edit(edited, 1, entry["after"], entry["before"])
        assert back == DECK


def test_a_batch_edit_is_gated_too():
    with tempfile.TemporaryDirectory() as tmp:
        deck = os.path.join(tmp, "output-5.html")
        with open(deck, "w", encoding="utf-8") as f:
            f.write(DECK)
        saved = apply_edits_and_save(deck, [
            {"slide": 1, "source": "Prepared for the board.",
             "replacement": f"Live {EM} as of May."},
        ])
        assert not saved["failed"]
        with open(saved["revision_path"], encoding="utf-8") as f:
            written = f.read()
        assert EM not in written
        assert load_edit_log(deck)[-1]["after"] == "Live, as of May."


def test_a_clean_edit_is_untouched_by_the_gate():
    """Nothing moves for the edits that were already fine, which is nearly all
    of them."""
    clean = "Revenue grew to $52M in 2026."
    out = apply_text_edit(DECK, 2, "Revenue was", clean)
    assert clean in out


def test_a_header_edit_through_the_SAVE_PATH_keeps_the_header_form():
    """The route's own path, which is the one that was untested.

    `ui/app.py` reaches the edit layer only through the `*_and_save` wrappers,
    never through `apply_text_edit`, and those gate in `gated_replacement`
    first; the second gate inside the swap is then a no-op by idempotence. So
    the prose decision that reaches every real edit is the one made there, and
    while it was made twice, pinning that copy to prose left the whole suite
    green while a header edit landed reading like a sentence. Found by the
    review window, by mutation, 2026-09-16.
    """
    with tempfile.TemporaryDirectory() as tmp:
        deck = os.path.join(tmp, "output-6.html")
        with open(deck, "w", encoding="utf-8") as f:
            f.write(DECK)
        saved = apply_edit_and_save(deck, 2, "Overview", f"Overview {EM} phase two")
        with open(saved["revision_path"], encoding="utf-8") as f:
            written = f.read()

        assert "Overview - phase two" in written, "a header gates as a header"
        assert "Overview, phase two" not in written
        # And the log still says exactly what the slide says.
        assert load_edit_log(deck)[-1]["after"] == "Overview - phase two"


def test_a_prose_edit_through_the_save_path_keeps_the_prose_form():
    """The other half of the same decision, so the test pins a distinction
    rather than one side of it."""
    with tempfile.TemporaryDirectory() as tmp:
        deck = os.path.join(tmp, "output-7.html")
        with open(deck, "w", encoding="utf-8") as f:
            f.write(DECK)
        saved = apply_edit_and_save(
            deck, 1, "Prepared for the board.", f"On track {EM} three sites live.")
        with open(saved["revision_path"], encoding="utf-8") as f:
            written = f.read()

        assert "On track, three sites live." in written
        assert "On track - three sites live." not in written


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


# --------------------------- the batch partial-failure contract, both halves.
# `apply_edits_and_save` promises that an edit whose source is "missing OR
# ambiguous is skipped and recorded in `failed` ... rather than aborting the
# whole batch, so one bad target does not lose the good edits." From 766b6ed
# until 2026-09-17 only the missing half held: `gated_replacement` sat outside
# the per-edit try and an ambiguous source raised out of the function, taking
# every edit that had already applied with it. The existing contract test used
# "not-on-this-slide", which is the half that could not fail.

AMBIGUOUS_DECK = (
    "<!doctype html>\n<html><body>\n"
    '<section class="slide">\n'
    "  <h2>Summary</h2>\n"
    "  <p>Adjusted EBITDA margin held at 42.4 percent.</p>\n"
    "  <p>Adjusted EBITDA was flat year on year.</p>\n"
    "  <p>Fuel entry is not enforced.</p>\n"
    "</section>\n"
    "</body></html>\n"
)

_GOOD_EDIT = {
    "slide": 1,
    "source": "Fuel entry is not enforced.",
    "replacement": "Fuel entry is optional.",
}
# "Adjusted EBITDA" is on that slide twice, so it is ambiguous by text alone.
_AMBIGUOUS_EDIT = {
    "slide": 1,
    "source": "Adjusted EBITDA",
    "replacement": "Adj. EBITDA",
}


def test_an_ambiguous_edit_does_not_discard_the_good_edits_beside_it():
    path = _write_deck("output-9.html", AMBIGUOUS_DECK)
    try:
        result = apply_edits_and_save(
            path, [dict(_GOOD_EDIT), dict(_AMBIGUOUS_EDIT)],
            now="2026-09-17T00:00:00+00:00",
        )
        assert len(result["applied"]) == 1
        assert len(result["failed"]) == 1
        assert "ambiguous" in result["failed"][0]["reason"]
        with open(result["revision_path"], encoding="utf-8") as f:
            saved = f.read()
        assert "Fuel entry is optional." in saved
        # The ambiguous target is untouched rather than changed in one of the
        # two places it could have meant.
        assert saved.count("Adjusted EBITDA") == 2
    finally:
        _cleanup(path)


def test_a_good_edit_after_an_ambiguous_one_also_survives():
    """Order is not what saved it: the batch aborted on reaching the ambiguous
    target, so an edit queued behind one was lost too."""
    path = _write_deck("output-10.html", AMBIGUOUS_DECK)
    try:
        result = apply_edits_and_save(
            path, [dict(_AMBIGUOUS_EDIT), dict(_GOOD_EDIT)],
            now="2026-09-17T00:00:00+00:00",
        )
        assert len(result["applied"]) == 1
        assert len(result["failed"]) == 1
        with open(result["revision_path"], encoding="utf-8") as f:
            assert "Fuel entry is optional." in f.read()
    finally:
        _cleanup(path)


def test_a_batch_of_nothing_but_an_ambiguous_edit_still_writes_nothing():
    """The other half of the contract is unchanged: with no edit applying, the
    batch raises and no revision appears."""
    path = _write_deck("output-11.html", AMBIGUOUS_DECK)
    try:
        try:
            apply_edits_and_save(path, [dict(_AMBIGUOUS_EDIT)],
                                 now="2026-09-17T00:00:00+00:00")
        except EditNotApplicable as exc:
            assert "ambiguous" in str(exc)
        else:
            raise AssertionError("expected EditNotApplicable with nothing applied")
        beside = os.listdir(os.path.dirname(path))
        assert [n for n in beside if n.endswith(".html")] == ["output-11.html"]
    finally:
        _cleanup(path)


def test_the_ambiguity_sentinel_is_what_the_refusal_actually_says():
    """`ui/app.py` tells the two refusals apart with this constant, so it has to
    stay the opening of the sentence `_find_unique` raises."""
    path = _write_deck("output-12.html", AMBIGUOUS_DECK)
    try:
        result = apply_edits_and_save(
            path, [dict(_GOOD_EDIT), dict(_AMBIGUOUS_EDIT)],
            now="2026-09-17T00:00:00+00:00",
        )
        assert result["failed"][0]["reason"].startswith(AMBIGUOUS_SOURCE)
    finally:
        _cleanup(path)

    # And an ABSENT source must not match it, or both read as ambiguous. On its
    # own deck, because the batch above already consumed the good edit's source.
    other = _write_deck("output-13.html", AMBIGUOUS_DECK)
    try:
        absent = apply_edits_and_save(
            other, [dict(_GOOD_EDIT),
                    {"slide": 1, "source": "not-on-this-slide", "replacement": "x"}],
            now="2026-09-17T00:00:00+00:00",
        )
        assert len(absent["failed"]) == 1
        assert AMBIGUOUS_SOURCE not in absent["failed"][0]["reason"]
    finally:
        _cleanup(other)
