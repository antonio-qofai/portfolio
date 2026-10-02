"""Tests for the durable deck store (one table, four functions).

The round trip runs against a real SQLite file in a temporary directory, because
the point of this module is what survives a deploy and an in-memory fake would
prove nothing about that. Nothing skips any more: the store is a file, so every
machine that can run the suite can run these.

No client, project, or deck name is written into an assertion: the round-trip
deck is a real render the tests own under `tests/fixtures/`, and the filter tests
use values the test itself invents.

Run with: python3 tests/test_deck_store.py
"""

import glob
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from deck_store import delete_decks, get_deck, list_decks, save_deck

_TAG = "test-deck"


class _TempStore:
    """A store file of its own, removed with its directory afterwards."""

    def __enter__(self):
        self._dir = tempfile.mkdtemp(prefix="deck-store-")
        return os.path.join(self._dir, "decks.sqlite3")

    def __exit__(self, *exc):
        shutil.rmtree(self._dir, ignore_errors=True)
        return False


def _any_rendered_deck():
    """A deck the pipeline actually rendered, whichever client it is for.

    Read from the fixtures the tests own rather than from `decks/`, which is a
    working directory a cleanup is entitled to empty.
    """
    fixtures = sorted(glob.glob(
        os.path.join(os.path.dirname(__file__), "fixtures", "text_gate", "*.html")))
    for path in fixtures:
        with open(path, encoding="utf-8") as f:
            return f.read()
    return None


def test_a_generated_deck_round_trips_unchanged():
    """The done-when check: save a real generated deck, read it back identical."""
    html = _any_rendered_deck()
    if html is None:
        print("SKIP: no rendered deck in the repo to round-trip")
        return

    details = {
        "prompt": "a design prompt with unicode: – and a quote \" and a \\ backslash",
        "applied_preferences": ["tighten the tracker whitespace"],
        "render_fidelity": {"ok": True, "missing_values": []},
        "flags": [{"field": "budget_usd", "resolution": "confirmed"}],
        "revisions": [{"revision": "output-9-r1.html", "edit": "swap a figure"}],
    }
    with _TempStore() as store:
        deck_id = save_deck("proposal", f"{_TAG}-company", f"{_TAG}-project", html,
                            is_final=True, details=details, store_path=store)
        row = get_deck(deck_id, store_path=store)
        assert row is not None
        assert row["html"] == html, "the deck HTML came back changed"
        assert row["details"] == details, "the JSON column came back changed"
        assert row["deck_type"] == "proposal"
        assert row["company"] == f"{_TAG}-company"
        assert row["project"] == f"{_TAG}-project"
        assert row["is_final"] is True
        assert row["created_at"] is not None
        # Whatever a caller stored has to survive a JSON round trip too, since
        # that is what a later prompt will read back out of the column.
        assert json.loads(json.dumps(row["details"])) == details


def test_the_three_columns_sqlite_narrows_come_back_widened():
    """SQLite has no boolean, no JSON and no timestamp type, so `is_final`,
    `details` and `created_at` would come back as 1, a string and a string. Each
    of those breaks the Decks tab quietly, so each is pinned here as well as in
    the round trip, on a list row and a full row alike.
    """
    with _TempStore() as store:
        deck_id = save_deck("status", f"{_TAG}-c", f"{_TAG}-p", "<a/>",
                            is_final=True, details={"n": 1}, store_path=store)
        for row in (get_deck(deck_id, store_path=store),
                    list_decks(store_path=store)[0]):
            assert row["is_final"] is True, "is_final came back as %r" % row["is_final"]
            assert row["details"] == {"n": 1}, "details came back as %r" % row["details"]
            assert isinstance(row["created_at"], datetime), row["created_at"]
            assert row["created_at"].tzinfo is not None, "created_at is not tz-aware"
            assert row["created_at"].utcoffset().total_seconds() == 0, "not UTC"


def test_save_defaults_and_get_on_an_unknown_id():
    with _TempStore() as store:
        deck_id = save_deck("status", f"{_TAG}-c", f"{_TAG}-p", "<html></html>",
                            store_path=store)
        row = get_deck(deck_id, store_path=store)
        assert row["is_final"] is False, "a deck is not final unless it says so"
        assert row["details"] == {}, "no details means an empty object, not null"

        assert delete_decks([deck_id], store_path=store) == 1
        assert get_deck(deck_id, store_path=store) is None, "deleted row still readable"
        assert get_deck(-1, store_path=store) is None


def test_list_decks_filters_on_the_indexed_columns_and_lists_newest_first():
    company_a, company_b = f"{_TAG}-a", f"{_TAG}-b"
    with _TempStore() as store:
        first = save_deck("proposal", company_a, f"{_TAG}-p1", "<a/>", store_path=store)
        second = save_deck("status", company_a, f"{_TAG}-p2", "<b/>", store_path=store)
        third = save_deck("proposal", company_b, f"{_TAG}-p1", "<c/>", store_path=store)

        rows = list_decks(company=company_a, store_path=store)
        assert [r["id"] for r in rows] == [second, first], "not newest first"
        assert "html" not in rows[0], "a list row should not carry the whole deck"
        assert rows[0]["details"] == {}

        assert [r["id"] for r in list_decks(company=company_a, deck_type="proposal",
                                            store_path=store)] == [first]
        assert [r["id"] for r in list_decks(project=f"{_TAG}-p1",
                                            store_path=store)] == [third, first]
        assert [r["id"] for r in list_decks(company=company_a, project=f"{_TAG}-p1",
                                            deck_type="status",
                                            store_path=store)] == []

        limited = list_decks(company=company_a, limit=1, store_path=store)
        assert [r["id"] for r in limited] == [second]

        # No filters lists everything, so the three rows above are all in there.
        every_id = [r["id"] for r in list_decks(limit=1000, store_path=store)]
        for deck_id in (first, second, third):
            assert deck_id in every_id


def test_delete_decks_removes_by_id_and_reports_the_count():
    """Added for the Decks tab's per-row and bulk delete (prompt B3), which had
    no way to remove a row until this existed."""
    with _TempStore() as store:
        first = save_deck("proposal", f"{_TAG}-c", f"{_TAG}-p1", "<a/>", store_path=store)
        second = save_deck("proposal", f"{_TAG}-c", f"{_TAG}-p2", "<b/>", store_path=store)
        third = save_deck("proposal", f"{_TAG}-c", f"{_TAG}-p3", "<c/>", store_path=store)

        assert delete_decks([], store_path=store) == 0, "nothing to delete is not an error"
        assert delete_decks([first, second], store_path=store) == 2
        assert get_deck(first, store_path=store) is None
        assert get_deck(second, store_path=store) is None
        assert get_deck(third, store_path=store) is not None, "an unselected row must survive"
        # A repeat delete of already-gone ids removes nothing, rather than erroring.
        assert delete_decks([first, second], store_path=store) == 0


if __name__ == "__main__":
    test_a_generated_deck_round_trips_unchanged()
    test_the_three_columns_sqlite_narrows_come_back_widened()
    test_save_defaults_and_get_on_an_unknown_id()
    test_list_decks_filters_on_the_indexed_columns_and_lists_newest_first()
    test_delete_decks_removes_by_id_and_reports_the_count()
    print("All tests passed.")
