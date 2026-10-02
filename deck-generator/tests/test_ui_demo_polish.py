"""The studio's demo polish, 2026-09-23.

Cosmetic only, built overnight before the founders' demo: a readable heading
for a saved deck, the status line as chips, missing-field rows that fit the
controls column, the last navy removed, and the old commercial editors retired.
The Generate tab was narrowed too and put back the same night (Antonio: "I don't
like the narrow UI on the generate tab"). How it looks is confirmed by hand in the
studio; these pin the parts a later edit could quietly undo.
"""

import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ui"))

pytest.importorskip("flask")

import app as ui_app


def _page(tab="generate"):
    with ui_app.app.test_request_context("/"):
        return " ".join(ui_app.render_studio(tab).split())


def _source():
    return " ".join(open(ui_app.__file__, encoding="utf-8").read().split())


# ---- the saved deck's heading ----

def test_a_saved_deck_heading_reads_as_a_date_not_a_database_value():
    """It printed "saved 2026-09-23 07:57:11.267000+00:00" on the demo screen."""
    when = datetime(2026, 9, 23, 7, 57, 11, 267000, tzinfo=timezone.utc)
    assert ui_app._saved_heading(when) == "Saved Sep 23, 2026 · 07:57 UTC"


def test_the_sqlite_string_form_reads_the_same():
    assert (ui_app._saved_heading("2026-09-23T07:57:11.267000+00:00")
            == "Saved Sep 23, 2026 · 07:57 UTC")
    assert ui_app._saved_heading("2026-09-23T07:57:11Z") == "Saved Sep 23, 2026 · 07:57 UTC"


def test_an_unreadable_timestamp_falls_back_rather_than_failing():
    assert ui_app._saved_heading("not a date") == "Saved not a date"
    assert ui_app._saved_heading(None) == "Saved deck"


def test_the_deck_is_titled_by_its_project_with_the_run_note_as_a_kicker():
    source = _source()
    assert "<h2>{{ raw_project or deck_heading }}</h2>" in source
    assert '<div class="deck-kicker">' in source


# ---- the Result tab ----

def test_the_status_line_is_chips():
    source = _source()
    assert '<div class="status-chips">' in source
    for kind in ("chip-ok", "chip-warn", "chip-bad"):
        assert f"class=\"chip {kind}\"" in source, kind


def test_missing_field_rows_stack_rather_than_overflow_the_column():
    source = _source()
    assert '<table class="fill-table">' in source
    assert 'class="fill-input"' in source
    # The inline width that pushed the buttons past the column's edge.
    assert "min-width:220px" not in source


def test_the_old_commercial_editors_are_gone_from_the_page():
    source = _source()
    assert "action=\"{{ url_for('commercial_terms') }}\"" not in source
    assert "action=\"{{ url_for('commercial_defaults') }}\"" not in source
    # The missing-values card for hand-entered terms stays.
    assert '"heading": "Commercial terms, entered by hand"' in source


# ---- copy and colour ----

def test_the_empty_result_tab_no_longer_names_the_retired_fixture_picker():
    page = _page("result")
    assert "No deck yet" in page
    assert "pick a fixture" not in page.lower()
    # This fragment is inserted without Jinja, so a template comment in it
    # prints on the page, which is how the first cut shipped one.
    assert "{#" not in page and "#}" not in page
    assert "Go to Generate" in page


def test_the_header_no_longer_calls_itself_an_internal_studio():
    assert "internal review studio" not in _page()


def test_no_navy_is_left_in_the_studio_theme():
    """The black-and-white pass of 2026-09-20 missed the table headers, the
    code chips and the code blocks."""
    page = _page()
    for navy in ("#7286ad", "#070d1c", "#9db4e0", "#c3d0ee"):
        assert navy not in page, navy


def test_css_content_glyphs_are_literal_characters():
    """The first cut wrote `content: "\\00B7"` inside the Python string that
    holds the stylesheet, and Python read `\\00` as an octal escape: the kicker
    showed "B7" after a NUL."""
    page = _page()
    assert 'content: "·"' in page
    assert 'B7"' not in page


def test_a_long_source_filename_can_break_so_the_decks_table_fits_its_card():
    """At a 1466px window the Decks table ran 14px past its card: a filename
    with no spaces beside four unbreakable buttons set its minimum width."""
    assert ".decks-table td:not(.row-actions) a { overflow-wrap: anywhere; }" in _page("decks")


# ---- the row delete on the Decks tab ----

def _decks_panel(db_mode):
    from flask import render_template_string
    when = datetime(2026, 9, 23, 9, 57, tzinfo=timezone.utc)
    decks = ([{"id": i, "company": "Client", "project": f"Project {i}",
               "details": {}, "deck_type": "proposal", "created_at": when,
               "is_final": True} for i in (7, 8)]
             if db_mode else
             [{"client": "Client", "name": f"output-{i}.html", "revision": 0,
               "edits": 0, "when": "2026-09-23 09:57", "path": f"/decks/output-{i}.html",
               "rev_count": 0} for i in (7, 8)])
    with ui_app.app.test_request_context("/"):
        return render_template_string(
            ui_app.DECKS_PANEL, decks=decks, db_mode=db_mode, notice="",
            notice_bad=False, decks_root="/decks", _db_deck_prefix="db-deck:")


@pytest.mark.parametrize("db_mode", [True, False])
def test_no_form_sits_inside_another_on_the_decks_tab(db_mode):
    """Every row's delete was a form inside the "Delete selected" form. The
    browser drops a nested form, so the row button submitted the BULK form and
    deleted the ticked decks, not its own row."""
    from html.parser import HTMLParser

    class Forms(HTMLParser):
        depth = deepest = 0
        ids = []
        def handle_starttag(self, tag, attrs):
            if tag == "form":
                self.depth += 1
                self.deepest = max(self.deepest, self.depth)
                self.ids.append(dict(attrs).get("id"))
        def handle_endtag(self, tag):
            if tag == "form":
                self.depth -= 1

    html = _decks_panel(db_mode)
    parser = Forms()
    parser.ids = []
    parser.feed(html)
    assert parser.deepest == 1, "a form is nested inside another"
    # Each row's button names a row form that exists, and they are distinct.
    targets = [t for t in ("deck-delete-1", "deck-delete-2")]
    for t in targets:
        assert f'form="{t}">delete</button>' in html, t
        assert t in parser.ids, t
