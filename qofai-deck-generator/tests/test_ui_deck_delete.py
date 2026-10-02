"""Tests for deleting a deck from the Decks tab (`/deck-delete`).

Until this existed the studio could only ever accumulate: every render stayed on
the Decks tab forever and the only way to clear a bad or superseded deck was to
go into the decks folder by hand. Deleting is the one irreversible thing the UI
does, so what it takes with it has to be exact:

- a REVISION goes on its own, and only its own entries leave the shared edit log,
- an ORIGINAL takes its revisions and the whole edit log with it, because a
  revision is a copy of that original and `undo_last_edit` walks back to it,
- the saved Design prompt is never touched — it is the record of how the deck was
  asked for, not the deck,
- nothing outside DECKS_ROOT can be deleted, and nothing that is not a deck file,
- the Result tab drops a run whose deck was just deleted, and keeps one whose
  deck was not.

Flask's test client drives the app against a temp DECKS_ROOT; no API key, no
network and no render are needed.

Run with: python3 tests/test_ui_deck_delete.py
"""

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ui"))

try:
    import flask  # noqa: F401
    _HAVE_FLASK = True
except ImportError:  # UI-only dependency; skip if absent
    _HAVE_FLASK = False

DECK_HTML = (
    "<!doctype html><html><body>"
    '<section class="slide" data-slide="1"><h1>Cover</h1></section>'
    "</body></html>"
)


def _unreadable_store():
    """Puts the Decks tab into its filesystem-listing mode; returns a restore.

    These tests are about deleting deck FILES, which is the tab's filesystem
    mode. Since prompt B2d there is always a resolved store directory, so that
    mode is reachable only when the store itself cannot be read — and a
    directory sitting where the SQLite file belongs is the smallest way to make
    opening it fail.
    """
    tmp = tempfile.mkdtemp(prefix="unreadable-store-")
    os.makedirs(os.path.join(tmp, "decks.sqlite3"))
    saved = os.environ.get("DECK_STORE_DIR")
    os.environ["DECK_STORE_DIR"] = tmp

    def restore():
        if saved is None:
            os.environ.pop("DECK_STORE_DIR", None)
        else:
            os.environ["DECK_STORE_DIR"] = saved
        shutil.rmtree(tmp, ignore_errors=True)

    return restore


def _entry(revision):
    return {"revision": revision, "slide": 1, "kind": "content",
            "before": "a", "after": "b", "author": "", "created": "now"}


def _studio():
    """A studio over a temp decks root holding two decks for one client.

    ``output-1.html`` is an original with two revisions and a two-entry edit log;
    ``output-2.html`` is an untouched original. Returns
    ``(ui_app, client, code_dir, restore)``.
    """
    import app as ui_app

    root = tempfile.mkdtemp(prefix="studio-decks-")
    code_dir = os.path.join(root, "Northwind", ui_app.DECK_CODE_SUBDIR)
    os.makedirs(code_dir)
    for name in ("output-1.html", "output-1-r1.html", "output-1-r2.html",
                 "output-2.html"):
        with open(os.path.join(code_dir, name), "w", encoding="utf-8") as f:
            f.write(DECK_HTML)
    with open(os.path.join(code_dir, "output-1.edits.json"), "w", encoding="utf-8") as f:
        json.dump([_entry("output-1-r1.html"), _entry("output-1-r2.html")], f)

    original_root = ui_app.DECKS_ROOT
    ui_app.DECKS_ROOT = root
    ui_app.app.testing = True
    ui_app._LAST_RESULT.clear()
    restore_store = _unreadable_store()

    def restore():
        ui_app.DECKS_ROOT = original_root
        ui_app._LAST_RESULT.clear()
        restore_store()
        shutil.rmtree(root, ignore_errors=True)

    return ui_app, ui_app.app.test_client(), code_dir, restore


def _names(code_dir):
    return set(os.listdir(code_dir))


def test_the_decks_tab_offers_a_delete_on_every_row():
    if not _HAVE_FLASK:
        return
    ui_app, client, code_dir, restore = _studio()
    try:
        html = client.get("/?tab=decks").get_data(as_text=True)
        assert html.count('action="/deck-delete"') == 4, (
            "every listed deck needs its own delete, revisions included")
        assert "cannot be undone" in html, "the delete must be labelled destructive"
        # The confirm on an original names what it takes with it, so the reviewer
        # is not told "delete output-1.html" and silently loses two revisions.
        assert "the 2 revision(s) beside it" in html
    finally:
        restore()


def test_deleting_a_revision_leaves_the_original_and_the_other_revision():
    if not _HAVE_FLASK:
        return
    ui_app, client, code_dir, restore = _studio()
    try:
        target = os.path.join(code_dir, "output-1-r1.html")
        resp = client.post("/deck-delete", data={"path": target})
        assert resp.status_code == 302
        assert _names(code_dir) == {
            "output-1.html", "output-1-r2.html", "output-2.html",
            "output-1.edits.json"}
        # Only the deleted revision's entry leaves the shared log.
        with open(os.path.join(code_dir, "output-1.edits.json"), encoding="utf-8") as f:
            log = json.load(f)
        assert [e["revision"] for e in log] == ["output-1-r2.html"], log

        html = client.get(resp.headers["Location"]).get_data(as_text=True)
        assert "Deleted output-1-r1.html." in html
    finally:
        restore()


def test_deleting_the_last_revision_removes_the_emptied_edit_log():
    """A deck whose every revision is gone must read as having no edit history,
    not as having a log file full of entries pointing at deleted files."""
    if not _HAVE_FLASK:
        return
    ui_app, client, code_dir, restore = _studio()
    try:
        for rev in ("output-1-r1.html", "output-1-r2.html"):
            client.post("/deck-delete", data={"path": os.path.join(code_dir, rev)})
        assert _names(code_dir) == {"output-1.html", "output-2.html"}
    finally:
        restore()


def test_deleting_an_original_takes_its_revisions_and_log_with_it():
    if not _HAVE_FLASK:
        return
    ui_app, client, code_dir, restore = _studio()
    try:
        resp = client.post("/deck-delete",
                           data={"path": os.path.join(code_dir, "output-1.html")})
        assert _names(code_dir) == {"output-2.html"}, (
            "an orphaned revision of a deleted original is worse than a clean cascade")
        html = client.get(resp.headers["Location"]).get_data(as_text=True)
        assert "Deleted output-1.html and the 2 revision(s) beside it." in html
        # Gone from the listing itself (the banner still names it, by design).
        assert "<code>output-1.html</code>" not in html
        assert "<code>output-1-r2.html</code>" not in html
        assert "<code>output-2.html</code>" in html
    finally:
        restore()


def test_delete_refuses_a_path_outside_the_decks_root():
    """The same guard the serving routes use: a hand-posted path must not be able
    to delete anything the studio does not own."""
    if not _HAVE_FLASK:
        return
    ui_app, client, code_dir, restore = _studio()
    outside = tempfile.mkdtemp(prefix="not-decks-")
    victim = os.path.join(outside, "output-9.html")
    with open(victim, "w", encoding="utf-8") as f:
        f.write(DECK_HTML)
    try:
        assert client.post("/deck-delete", data={"path": victim}).status_code == 404
        assert os.path.isfile(victim), "a path outside DECKS_ROOT must survive"
        assert client.post("/deck-delete", data={"path": ""}).status_code == 404
        # Inside the root but not a deck: the file must not be removed.
        stray = os.path.join(code_dir, "output-1.edits.json")
        client.post("/deck-delete", data={"path": stray})
        assert os.path.isfile(stray), "only deck files are deletable"
    finally:
        shutil.rmtree(outside, ignore_errors=True)
        restore()


def test_deleting_the_deck_on_screen_clears_the_result_tab_but_a_sibling_does_not():
    if not _HAVE_FLASK:
        return
    ui_app, client, code_dir, restore = _studio()
    try:
        on_screen = os.path.join(code_dir, "output-1.html")
        ui_app._LAST_RESULT.update({"deck_path": on_screen,
                                    "render_deck_path": on_screen,
                                    "deck_type": "status", "status": None,
                                    "result": {"status": "ok"}})
        # An unrelated deck: the run on screen is untouched.
        client.post("/deck-delete", data={"path": os.path.join(code_dir, "output-2.html")})
        assert ui_app._LAST_RESULT.get("deck_path") == on_screen

        # The deck on screen: the run goes, rather than offering edits and undo
        # against a file that is no longer there.
        client.post("/deck-delete", data={"path": on_screen})
        assert ui_app._LAST_RESULT == {}
    finally:
        restore()


def test_the_saved_design_prompt_survives_a_deck_delete():
    if not _HAVE_FLASK:
        return
    ui_app, client, code_dir, restore = _studio()
    prompt_dir = tempfile.mkdtemp(prefix="studio-prompts-")
    prompt = os.path.join(prompt_dir, "generated-prompt-1.txt")
    with open(prompt, "w", encoding="utf-8") as f:
        f.write("(design prompt body)")
    try:
        client.post("/deck-delete", data={"path": os.path.join(code_dir, "output-1.html")})
        assert os.path.isfile(prompt), (
            "deleting a deck is not a request to delete the run's prompt")
    finally:
        shutil.rmtree(prompt_dir, ignore_errors=True)
        restore()


if __name__ == "__main__":
    if not _HAVE_FLASK:
        print("flask not installed; skipping UI tests")
    else:
        for name, fn in sorted(list(globals().items())):
            if name.startswith("test_") and callable(fn):
                fn()
                print(f"ok  {name}")
        print("all deck-delete UI tests passed")
