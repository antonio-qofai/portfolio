"""Tests for the display-text guard — the HTML-to-words boundary.

Two things have to hold at once for this guard to be worth having: it must catch
markup that a reader would see printed on a slide, and it must stay quiet on the
brackets that legitimately appear in deck copy (a `<15%` margin, a `>` in a CSS
selector). A guard that fires on real decks gets turned off, so the false-positive
half is tested against the committed renders rather than against invented copy.

Run with: python3 tests/test_display_text_guard.py
"""

import glob
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from display_text_guard import DisplayTextError, check_display_text, visible_tags

_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "text_gate")

CLEAN = (
    "<!doctype html>\n<html><head><style>.head > .eyebrow{color:#000}</style></head>\n"
    "<body>\n"
    '<section class="slide"><h1>Operational Intelligence '
    '<span class="base">Project Planning.</span></h1>\n'
    "<p>Gross margin held under &lt;15% through the period.</p>\n"
    "</section>\n</body></html>\n"
)

# The 2026-07-23 leak, as it reaches a reader: the tags are escaped, so the
# browser prints them instead of applying them.
LEAKED = CLEAN.replace(
    '<span class="base">Project Planning.</span>',
    "&lt;span class=&quot;base&quot;&gt;Project Planning.&lt;/span&gt;",
)


def test_a_clean_deck_passes():
    assert visible_tags(CLEAN) == []
    assert check_display_text(CLEAN) == {"ok": True, "visible_tags": []}


def test_an_escaped_tag_in_slide_text_fails_and_names_it():
    try:
        check_display_text(LEAKED, where="the proposal deck")
    except DisplayTextError as e:
        assert "the proposal deck" in str(e)
        assert '<span class="base">' in str(e), str(e)
    else:
        raise AssertionError("expected DisplayTextError for a printed tag")


def test_css_selectors_and_scripts_are_not_display_text():
    # A stylesheet is markup a reader never sees, and CSS carries brackets of its
    # own. Reading it as copy would fail every deck this repo renders.
    deck = (
        "<html><head><style>.a > .b{content:'<x>'}</style>"
        "<script>if (a<b) { load('<p>') }</script></head>"
        "<body><section class=\"slide\"><p>Fine.</p></section></body></html>"
    )
    assert visible_tags(deck) == []


def test_arithmetic_brackets_in_copy_are_not_tags():
    deck = (
        '<section class="slide"><p>Margin &lt; 5% and headcount &gt; 3, with '
        "&lt;15% churn and a &lt;= 3 week gap.</p></section>"
    )
    assert visible_tags(deck) == []


def test_every_committed_render_is_clean():
    # The false-positive half, measured rather than asserted: the four text-gate
    # fixtures are real renders of both deck types, pre-gate and current.
    decks = sorted(glob.glob(os.path.join(_FIXTURES, "*.html")))
    assert len(decks) == 4, decks
    for deck in decks:
        with open(deck, encoding="utf-8") as f:
            assert visible_tags(f.read()) == [], os.path.basename(deck)


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
