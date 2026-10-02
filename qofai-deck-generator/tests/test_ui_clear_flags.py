"""The Clear buttons take a missing-field flag off the deck, and only that.

Part A3 of `build-plan-phase4-flags-and-fit.md`. Antonio, 2026-09-23: "maybe
there is no owner, or there is no designated week", and "you press ... and then
it just goes away." Driven through Flask on the committed 30-marker proposal
deck, as a reviewer drives it. Asserted on the deck's markup, whitespace
normalised.
"""

import hashlib
import os
import re
import sys

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, os.path.join(HERE, "..", "ui"))
sys.path.insert(0, HERE)

pytest.importorskip("flask")

from html_edit_layer import clear_marker_edits, list_missing_markers  # noqa: E402
from test_ui_missing_value_cards import DECK_FIXTURE, _read, _run, _studio  # noqa: E402

CONTEXT = {"deck_type": "proposal", "company": "Any Client",
           "project": "Any Project", "packet": "proposal-data-packet-EXAMPLE.md"}

# One next step with an owner the document stated, so the tidy has a separator
# to take out rather than a whole tag to empty.
WITH_OWNER = _read(DECK_FIXTURE).replace(
    '<span class="flag">[MISSING: week]</span> · <span class="flag">[MISSING: owner]</span>',
    '<span class="flag">[MISSING: week]</span> · CCO', 1)


@pytest.fixture
def studio(tmp_path):
    return _studio(tmp_path, WITH_OWNER)


def _current(deck_dir):
    """The newest revision in the studio's deck directory."""
    names = sorted((n for n in os.listdir(deck_dir) if n.startswith("output-3")
                    and n.endswith(".html")),
                   key=lambda n: int(re.search(r"-r(\d+)", n).group(1))
                   if "-r" in n else 0)
    return os.path.join(deck_dir, names[-1])


def _clear(client, deck_path, markers):
    return client.post("/clear-flags", data=dict(
        CONTEXT, path=deck_path,
        slide=[str(m["slide"]) for m in markers],
        source=[m["source"] for m in markers],
        occurrence=[str(m["occurrence"]) for m in markers],
        occurrences=[str(m["occurrences"]) for m in markers],
    )).get_data(as_text=True)


def _tags(html):
    return [" ".join(t.split()) for t in re.findall(r'<div class="tag">(.*?)</div>', html)]


def _next_steps(deck_path):
    return [m for m in list_missing_markers(_read(deck_path))
            if m["field"] in ("week", "owner")]


def test_clearing_the_next_steps_leaves_clean_tags_in_one_revision(studio):
    ui_app, client, deck_path = studio
    _run(client)
    page = _clear(client, deck_path, _next_steps(deck_path))
    now = _current(os.path.dirname(deck_path))

    assert now.endswith("output-3-r1.html")
    assert "Cleared 7 missing-field flags" in page
    assert _tags(_read(now)) == ["CCO", "", "", ""]
    assert _next_steps(now) == []


def test_undo_takes_a_whole_press_back_byte_for_byte(studio):
    ui_app, client, deck_path = studio
    _run(client)
    before = hashlib.sha1(_read(deck_path).encode()).hexdigest()
    _clear(client, deck_path, _next_steps(deck_path))
    client.post("/undo", data=dict(CONTEXT, path=_current(os.path.dirname(deck_path))))
    now = _current(os.path.dirname(deck_path))
    assert now == deck_path
    assert hashlib.sha1(_read(now).encode()).hexdigest() == before


def test_a_commercial_terms_marker_is_refused_even_if_posted(studio):
    ui_app, client, deck_path = studio
    _run(client)
    commercial = [m for m in list_missing_markers(_read(deck_path))
                  if m["field"] == "qofai_comp"]
    page = _clear(client, deck_path, commercial)
    assert "commercial terms are entered by hand, never cleared" in page
    assert _current(os.path.dirname(deck_path)) == deck_path


def test_a_position_the_deck_has_moved_past_is_refused(studio):
    ui_app, client, deck_path = studio
    _run(client)
    stale = _next_steps(deck_path)
    _clear(client, deck_path, stale[:1])
    page = _clear(client, deck_path, stale[1:2])
    assert "the deck has changed since this page was drawn" in page


def test_one_row_clears_on_its_own(studio):
    ui_app, client, deck_path = studio
    _run(client)
    first = [m for m in _next_steps(deck_path) if m["field"] == "week"][:1]
    _clear(client, deck_path, first)
    assert _tags(_read(_current(os.path.dirname(deck_path))))[0] == "CCO"


def test_the_cards_offer_clear_and_the_commercial_card_does_not(studio):
    ui_app, client, deck_path = studio
    page = " ".join(_run(client).split())
    cards = page.split("<h2>")
    reviewer = next(c for c in cards if c.startswith("Values we expect you to supply"))
    commercial = next(c for c in cards if c.startswith("Commercial terms, entered by hand"))
    assert 'action="/clear-flags" data-keep-slide' in reviewer
    assert "Clear all 13" in reviewer
    assert "/clear-flags" not in commercial.split("</div>")[0]


def test_the_edits_never_write_a_value():
    html = WITH_OWNER
    markers = [m for m in list_missing_markers(html) if m["field"] in ("week", "owner")]
    edits = clear_marker_edits(html, markers)
    removals = [e for e in edits if "[MISSING:" in e["source"]]
    assert removals and all(e["replacement"] == "" for e in removals)
    # A tidy only ever writes text the tag already held.
    for tidy in (e for e in edits if "[MISSING:" not in e["source"]):
        assert tidy["replacement"] in tidy["source"]


# ---- the adaptive commercial slide (2026-09-23) ----
#
# Audit D of `build-plan-commercial-slide.md`: the Clear buttons spare only
# markers the template declares `reviewer: sensitive`, and the deal a reviewer
# supplies is exactly that. So a TERMS marker is refused, and so is a marker a
# deck rendered BEFORE the rebuild still carries under a retired role name.

def test_a_terms_rows_marker_is_refused_even_if_posted(tmp_path):
    new_layout = WITH_OWNER.replace("[MISSING: commercial_rows]",
                                    "[MISSING: terms_rows]")
    ui_app, client, deck_path = _studio(tmp_path, new_layout)
    _run(client)
    terms = [m for m in list_missing_markers(_read(deck_path))
             if m["field"] == "terms_rows"]
    assert terms, "the fixture must carry a TERMS marker for this to mean anything"
    page = _clear(client, deck_path, terms)
    assert "commercial terms are entered by hand, never cleared" in page
    assert _current(os.path.dirname(deck_path)) == deck_path


def test_an_old_decks_retired_commercial_marker_is_still_refused(studio):
    ui_app, client, deck_path = studio
    _run(client)
    retired = [m for m in list_missing_markers(_read(deck_path))
               if m["field"] == "commercial_rows"]
    assert retired, "the committed old-layout deck carries this marker"
    page = _clear(client, deck_path, retired)
    assert "commercial terms are entered by hand, never cleared" in page
    assert _current(os.path.dirname(deck_path)) == deck_path
