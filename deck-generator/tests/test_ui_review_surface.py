"""Review-surface regressions found by walking the UI as a reviewer would.

These cover the defects a 2026-07-28 end-to-end pass through the studio turned up
(NEXT-STEPS item 1: use the thing the way Casey or Jordan would, instead of
picking off bugs one at a time). Each test pins one of them, so the surface cannot
quietly regress to the state that pass found it in:

- A flag decision or an edit dropped the whole run record off the Result tab —
  the outcome, the guard reports, the applied preferences and the saved Design
  prompt — because those routes have no pipeline result of their own.
- The guard reports must NOT be presented as describing an edited copy: both ran
  on the model's render and neither re-runs on an edit.
- Filling a supply-missing marker dropped the flagged-claims checklist, because
  that form forwarded no run context.
- A deck already on disk was unreachable once a second run replaced it.

Flask's test client drives the app and the pipeline is stubbed, so no API key, no
network and no real render are needed. The packet is always a temp COPY, because
resolving a gap writes the packet back and the committed fixtures are test inputs.

Run with: python3 tests/test_ui_review_surface.py
"""

import json
import os
import re
import shutil
import sys
import tempfile
from deck_run import run_deck

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ui"))

try:
    import flask  # noqa: F401
    _HAVE_FLASK = True
except ImportError:  # UI-only dependency; skip if absent
    _HAVE_FLASK = False

try:
    import playwright  # noqa: F401
    _HAVE_PLAYWRIGHT = True
except ImportError:  # PDF-export-only dependency; skip if absent
    _HAVE_PLAYWRIGHT = False

_ROOT = os.path.join(os.path.dirname(__file__), "..")
STATUS_PACKET = os.path.join(_ROOT, "status-data-packet-EXAMPLE.md")

# Double-quoted class attributes, matching what the renderer emits — that is what
# html_edit_layer.find_slides keys on, so a single-quoted fixture parses as a deck
# with zero slides and every edit assertion silently tests nothing.
DECK_HTML = (
    "<!doctype html><html><body>"
    '<section class="slide" data-slide="1"><h1>Cover</h1>'
    "<p>Investment of $275,000 this year.</p></section>"
    '<section class="slide" data-slide="2"><h1>Terms</h1>'
    '<p>Lock date <span class="flag">[MISSING: baseline_locked_date]</span></p>'
    "</section></body></html>"
)

# Carries its own fixed-size style, unlike DECK_HTML above, so the PDF export
# test can check that a printed page keeps the deck's own 1280x720 slide size
# rather than reflowing into a browser's default page format.
PDF_DECK_HTML = (
    "<!doctype html><html><head><style>"
    "*{margin:0}"
    ".slide{width:1280px;height:720px;background:#fff;"
    "page-break-after:always;break-after:page}"
    "</style></head><body>"
    '<section class="slide" data-slide="1"><h1>Cover</h1></section>'
    '<section class="slide" data-slide="2"><h1>Terms</h1></section>'
    "</body></html>"
)

RUN_RESULT = {
    "status": "ok",
    "prompt": "(design prompt body)",
    "applied_preferences": [],
    "number": 7,
    "render_fidelity": {"ok": True, "missing_values": {}},
    "layout": {"ok": True, "checked": True, "skipped": "", "slides": 2,
               "findings": [], "summary": []},
}


def _unreadable_store():
    """Puts the Decks tab into its filesystem-listing mode; returns a restore.

    Since prompt B2d there is always a resolved store directory, so the tab's
    filesystem listing is reachable only when the store itself cannot be read —
    and a directory sitting where the SQLite file belongs is the smallest way to
    make opening it fail.
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


def _copy_packet():
    tmp = tempfile.mkdtemp(prefix="review-ui-")
    dst = os.path.join(tmp, "packet.md")
    shutil.copyfile(STATUS_PACKET, dst)
    return dst


def _studio(deck_dir):
    """The app with a stubbed pipeline that "renders" DECK_HTML into ``deck_dir``.

    ``DECKS_ROOT`` is repointed at the temp root, because every deck-serving and
    deck-editing route refuses a path outside it.
    """
    import app as ui_app

    deck_path = os.path.join(deck_dir, "output-7.html")
    with open(deck_path, "w", encoding="utf-8") as f:
        f.write(DECK_HTML)
    prompt_path = os.path.join(deck_dir, "generated-prompt-7.txt")
    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write("(design prompt body)")

    ui_app.generate_and_save_deck = lambda *a, **k: dict(
        RUN_RESULT, deck_path=deck_path, prompt_path=prompt_path
    )
    ui_app.DECKS_ROOT = deck_dir
    # Reviewer gap decisions must land in a temp store, never the repo's. The
    # module guard blocks the repo write outright, so forgetting this is loud.
    ui_app.DECISIONS_PATH = os.path.join(deck_dir, "gap-decisions.json")
    ui_app.app.testing = True
    ui_app._LAST_RESULT.clear()
    return ui_app, ui_app.app.test_client(), deck_path


def _run(client, packet):
    return run_deck(client, data={
        "deck_type": "status", "company": "Northwind", "project": "Impl",
        "packet": packet, "check_in_date": "2026-05-22",
    }).get_data(as_text=True)


def _edit(client, deck_path, packet, source, replacement):
    """One deterministic edit, which writes the next revision beside ``deck_path``."""
    return client.post("/edit", data={
        "path": deck_path, "slide": "1", "kind": "content",
        "source": source, "replacement": replacement,
        "deck_type": "status", "company": "Northwind", "project": "Impl",
        "packet": packet, "check_in_date": "2026-05-22",
    }).get_data(as_text=True)


def _save(client, deck_path, packet):
    """Press Save deck on whatever revision ``deck_path`` names."""
    return client.post("/save-deck", data={
        "path": deck_path, "deck_type": "status", "company": "Northwind",
        "project": "Impl", "packet": packet, "check_in_date": "2026-05-22",
    }).get_data(as_text=True)


def _export(client, path, packet):
    """Press Export Claude Design prompt on whatever revision ``path`` names
    (a real file, or a `db-deck:` sentinel)."""
    return client.post("/export-design-prompt", data={
        "path": path, "deck_type": "status", "company": "Northwind",
        "project": "Impl", "packet": packet, "check_in_date": "2026-05-22",
    }).get_data(as_text=True)


def test_gap_decision_keeps_the_run_record_on_screen():
    """Resolving one of two flags used to wipe the run outcome, the fidelity and
    layout reports, the applied-preferences list and the saved Design prompt — the
    reviewer lost the whole record of the run to confirm a single value."""
    if not _HAVE_FLASK:
        return
    ui_app, client, deck_path = _studio(tempfile.mkdtemp(prefix="review-deck-"))
    packet = _copy_packet()
    try:
        first = _run(client, packet)
        assert "Edit this deck" in first

        html = client.post("/gap-decision", data={
            "action": "resolve",
            "field": "workstreams[0].after.metrics[0].value",
            "deck_type": "status", "company": "Northwind", "project": "Impl",
            "packet": packet, "check_in_date": "2026-05-22",
            "deck_path": deck_path,
        }).get_data(as_text=True)

        assert "Confirmed" in html, "expected the confirm notice"
        # The whole run record survives the decision.
        assert "Edit this deck" in html, "the result panel must not vanish"
        assert "layout" in html, "the guards' report must not vanish"
        # Nothing was re-rendered, so the guards still describe the deck on screen.
        assert "Both checks above ran on" not in html, "no staleness note is due here"
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(os.path.dirname(deck_path), ignore_errors=True)


def test_edit_keeps_the_run_record_but_marks_the_guards_stale():
    """An edit keeps the record on screen AND says the guards ran on the original.

    Both guards measured the model's render; an edit writes a new revision and
    neither re-runs. Showing "layout: clean" against an edited copy would vouch
    for text nobody measured, so the reports stay visible and say which file they
    describe.
    """
    if not _HAVE_FLASK:
        return
    ui_app, client, deck_path = _studio(tempfile.mkdtemp(prefix="review-deck-"))
    packet = _copy_packet()
    try:
        _run(client, packet)
        ui_app.interpret_edit = lambda *a, **k: {
            "edits": [{"slide": 1, "source": "$275,000", "replacement": "$310,000"}],
            "unresolved": "",
        }
        os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")
        html = client.post("/edit-ai", data={
            "path": deck_path, "instruction": "change the investment to $310,000",
            "deck_type": "status", "company": "Northwind", "project": "Impl",
            "packet": packet, "check_in_date": "2026-05-22",
        }).get_data(as_text=True)

        assert "Applied 1 edit" in html, html[:400]
        assert "Edit this deck" in html, "the result panel must survive an edit"
        # ...and the guard reports are labelled as describing the pre-edit render.
        assert "Both checks above ran on" in html and "output-7.html" in html, (
            "an edited copy must not be presented as guard-checked"
        )
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(os.path.dirname(deck_path), ignore_errors=True)


def test_supply_missing_form_forwards_the_run_context():
    """Filling a `[MISSING: ...]` marker must not drop the flagged-claims card.

    The form posts to the deterministic /edit route, which rebuilds the panel from
    the run context on the form. It carried none, so `packet` came back empty,
    `show_gaps` went false, and the checklist disappeared.
    """
    if not _HAVE_FLASK:
        return
    ui_app, client, deck_path = _studio(tempfile.mkdtemp(prefix="review-deck-"))
    packet = _copy_packet()
    try:
        first = _run(client, packet)
        assert "Supply missing values (1)" in first, "expected the marker offered"
        # The offered form carries the run context, not just the swap.
        assert 'name="packet"' in first, "supply-missing must forward the packet"

        html = client.post("/edit", data={
            "path": deck_path, "slide": "2", "kind": "content",
            "source": '<span class="flag">[MISSING: baseline_locked_date]</span>',
            "replacement": "2026-08-01",
            "deck_type": "status", "company": "Northwind", "project": "Impl",
            "packet": packet, "check_in_date": "2026-05-22",
        }).get_data(as_text=True)

        assert "Edit applied to slide 2" in html, html[:400]
        assert f'value="{packet}"' in html, (
            "the run context must survive a supply-missing fill"
        )
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(os.path.dirname(deck_path), ignore_errors=True)


def test_export_design_prompt_reflects_the_edited_deck_not_the_original():
    """C6. The export must read the current revision, edits included, not
    whatever path the button's hidden field happens to hold — the same C1
    lesson (`current_revision_path`) applied to a new export path."""
    if not _HAVE_FLASK:
        return
    ui_app, client, deck_path = _studio(tempfile.mkdtemp(prefix="review-deck-"))
    packet = _copy_packet()
    try:
        _run(client, packet)
        _edit(client, deck_path, packet, "$275,000", "$310,000")

        # Posting the ORIGINAL (now-superseded) path, the way a stale page would.
        html = _export(client, deck_path, packet)
        panel = _result_panel(html)
        assert "Claude Design export prompt" in panel
        # Scoped to the embedded HTML itself, anchored on the export prompt's own
        # opening line: the confirmation notice above it also says "Claude Design
        # export prompt", and the edit-history table further down also names
        # "$275,000" as the "before" side of the logged swap, so an unscoped check
        # proves nothing about what got embedded.
        export_block = panel.split("Recreate the deck below", 1)[1].split(
            "END OF DESIGN EXPORT PROMPT", 1)[0]
        assert "$310,000" in export_block, "the export must carry the applied edit"
        assert "$275,000" not in export_block, (
            "the export must not fall back to the pre-edit revision"
        )
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(os.path.dirname(deck_path), ignore_errors=True)


def test_export_design_prompt_works_for_a_deck_opened_from_history():
    """A deck opened out of deck history may have no file at all (the `db-deck:`
    sentinel, B3). The export is offered there too, reading the row's own
    stored HTML — which is already the deck's current state, edits included,
    per B3a — rather than being silently unavailable."""
    if not _HAVE_FLASK:
        return
    from deck_store import list_decks

    store_dir = tempfile.mkdtemp(prefix="review-store-")
    deck_dir = tempfile.mkdtemp(prefix="review-deck-")
    os.environ["DECK_STORE_DIR"] = store_dir
    ui_app, client, deck_path = _studio(deck_dir)
    packet = _copy_packet()
    try:
        _run(client, packet)
        _edit(client, deck_path, packet, "$275,000", "$310,000")
        edited_path = os.path.join(deck_dir, "output-7-r1.html")
        _save(client, edited_path, packet)
        rows = list_decks(company="Northwind", project="Impl",
                          store_path=os.path.join(store_dir, "decks.sqlite3"))
        assert rows, "expected the save to have written a row to the store"
        deck_id = rows[0]["id"]

        shutil.rmtree(deck_dir, ignore_errors=True)  # a redeploy wipes the files

        html = _export(client, f"db-deck:{deck_id}", packet)
        panel = _result_panel(html)
        assert "Claude Design export prompt" in panel
        export_block = panel.split("Recreate the deck below", 1)[1].split(
            "END OF DESIGN EXPORT PROMPT", 1)[0]
        assert "$310,000" in export_block, "the stored HTML already carries the edit"
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)
        shutil.rmtree(store_dir, ignore_errors=True)


def test_decks_tab_lists_decks_on_disk_and_reopens_one():
    """A second run used to make the first deck unreachable from the UI."""
    if not _HAVE_FLASK:
        return
    root = tempfile.mkdtemp(prefix="review-root-")
    import app as ui_app
    code_dir = os.path.join(root, "Northwind", ui_app.DECK_CODE_SUBDIR)
    os.makedirs(code_dir)
    for name in ("output-1.html", "output-2.html", "output-2-r1.html"):
        with open(os.path.join(code_dir, name), "w", encoding="utf-8") as f:
            f.write(DECK_HTML)
    with open(os.path.join(code_dir, "output-2.edits.json"), "w", encoding="utf-8") as f:
        json.dump([{"revision": "output-2-r1.html", "slide": 1, "kind": "content",
                    "before": "a", "after": "b", "author": "", "created": "now"}], f)
    original_root = ui_app.DECKS_ROOT
    ui_app.DECKS_ROOT = root
    ui_app.app.testing = True
    restore_store = _unreadable_store()
    try:
        rows = ui_app.list_recent_decks(root)
        assert len(rows) == 3, rows
        assert {r["name"] for r in rows} == {
            "output-1.html", "output-2.html", "output-2-r1.html"}
        rev = next(r for r in rows if r["revision"] == 1)
        assert rev["edits"] == 1, "a revision should report its logged edits"
        assert all(r["client"] == "Northwind" for r in rows)

        client = ui_app.app.test_client()
        html = client.get("/?tab=decks").get_data(as_text=True)
        assert "Decks already generated" in html
        assert ">(3)<" in html, "expected the deck count in the heading"
        assert "output-2-r1.html" in html and "revision r1" in html

        # And "open" gives back a reviewable deck, not just a raw preview.
        opened = client.get(
            "/deck-view", query_string={"path": os.path.join(code_dir, "output-1.html")}
        ).get_data(as_text=True)
        assert "Editing output-1.html" in opened
        assert "Edit this deck" in opened
    finally:
        ui_app.DECKS_ROOT = original_root
        restore_store()
        shutil.rmtree(root, ignore_errors=True)


def test_an_unrelated_deck_does_not_inherit_the_last_runs_outcome():
    """The carried run record is keyed to the deck it came from.

    Opening some other client's deck from the Decks tab must not show the last
    run's status, guard reports or Design prompt as if they described it.
    """
    if not _HAVE_FLASK:
        return
    deck_dir = tempfile.mkdtemp(prefix="review-deck-")
    ui_app, client, deck_path = _studio(deck_dir)
    packet = _copy_packet()
    try:
        _run(client, packet)
        other = os.path.join(deck_dir, "output-99.html")
        with open(other, "w", encoding="utf-8") as f:
            f.write(DECK_HTML)
        html = client.get("/deck-view",
                          query_string={"path": other}).get_data(as_text=True)
        assert "Editing output-99.html" in html
        assert "Slide 2 bullet selection" not in html, (
            "an unrelated deck must not inherit another run's outcome"
        )
        assert "(design prompt body)" not in html
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


def _result_panel(html):
    """Just the Result panel, so an assertion cannot be satisfied by the Decks tab.

    Every page render carries all four panels, so a bare ``"output-3" in html``
    passes on the Decks tab's own row for that file and proves nothing about what
    the Result tab is showing.
    """
    return html.split('id="panel-result"', 1)[1]


def _decks_panel(html):
    """Just the Decks panel, scoped the same way `_result_panel` scopes the
    Result tab — the Generate tab's own deck-type select shares the word
    "proposal" or "status" with a Decks-tab row, so an unscoped check proves
    nothing about what the Decks tab itself rendered."""
    return html.split('id="panel-decks"', 1)[1].split('id="panel-preferences"', 1)[0]


def test_the_result_tab_keeps_the_generated_run_for_the_whole_session():
    """Opening another deck from the Decks tab used to lose the generated deck.

    The reproduction: generate, edit, then open any other deck from the Decks tab.
    The Result tab is rebuilt on every page render from one module-level slot, and
    that slot was overwritten by whatever was rendered last — including a deck-view,
    which has no run outcome of its own. So the run record was gone for good: the
    fidelity report, the applied preferences, the flagged-claims checklist, the edit
    history and the saved Design prompt could not be recovered by returning to the
    deck, because the slot no longer knew the run existed.

    Deleting that unrelated deck then finished the job. ``/deck-delete`` clears the
    slot when the deck on the Result tab is one of the files removed, and the slot
    now named the unrelated deck — so an unrelated delete left "No deck yet" behind,
    which is the reported symptom in full.
    """
    if not _HAVE_FLASK:
        return
    deck_dir = tempfile.mkdtemp(prefix="review-deck-")
    ui_app, client, deck_path = _studio(deck_dir)
    packet = _copy_packet()
    try:
        _run(client, packet)
        # An edit, so there is an edit history and a revision to lose as well.
        client.post("/edit", data={
            "path": deck_path, "slide": "1", "kind": "content",
            "source": "$275,000", "replacement": "$310,000",
            "deck_type": "status", "company": "Northwind", "project": "Impl",
            "packet": packet, "check_in_date": "2026-05-22",
        })
        other = os.path.join(deck_dir, "output-3.html")
        with open(other, "w", encoding="utf-8") as f:
            f.write(DECK_HTML)

        # The detour: open an unrelated deck, then come back to the Result tab.
        client.get("/deck-view", query_string={"path": other})
        panel = _result_panel(client.get("/?tab=result").get_data(as_text=True))
        assert "No deck yet" not in panel, "the generated deck must survive a detour"
        for card in ("layout", "Edit history"):
            assert card in panel, f"{card} must survive opening another deck"
        assert "output-7-r1.html" in panel, "the edited revision should be on screen"
        assert "output-3.html" not in panel, (
            "the detour must not take over the Result tab"
        )

        # ...and deleting that unrelated deck must not clear the Result tab.
        client.post("/deck-delete", data={"path": other})
        panel = _result_panel(client.get("/?tab=result").get_data(as_text=True))
        assert "No deck yet" not in panel, (
            "deleting an unrelated deck must not empty the Result tab"
        )
        assert "Edit this deck" in panel and "Edit history" in panel
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


def test_a_new_run_takes_the_result_tab_back_even_with_no_deck():
    """The remembered run is superseded by the next run, whatever that run produced.

    The guard that keeps a detour from evicting a generated run must not also keep a
    fresh run out: a run that escalates to review writes no deck, and showing the
    previous run's deck instead of the escalation would report the opposite of what
    happened.
    """
    if not _HAVE_FLASK:
        return
    deck_dir = tempfile.mkdtemp(prefix="review-deck-")
    ui_app, client, deck_path = _studio(deck_dir)
    packet = _copy_packet()
    try:
        _run(client, packet)
        ui_app.generate_and_save_deck = lambda *a, **k: {
            "status": "review", "confidence": "low", "data_completeness": 0.4,
            "missing_fields": ["ltm_revenue"],
        }
        _run(client, packet)
        panel = _result_panel(client.get("/?tab=result").get_data(as_text=True))
        assert "Escalated to human review" in panel, (
            "a fresh escalation must replace the previous run on the Result tab"
        )
        assert "output-7.html" not in panel, "the superseded deck must be gone"
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


def test_coverage_failure_is_a_data_error_not_a_render_error():
    """A packet field with no slot is caught before any API call, so it must not
    be labelled a render failure — a reviewer who reads "render failed" retries
    and waits out another render for the same result."""
    if not _HAVE_FLASK:
        return
    import app as ui_app
    from coverage_guard import CoverageError

    def _raise(*_a, **_k):
        raise CoverageError("packet field(s) have no template slot:\n- a.b\n- c.d")

    ui_app.generate_and_save_deck = _raise
    ui_app.app.testing = True
    client = ui_app.app.test_client()
    packet = _copy_packet()
    try:
        html = _run(client, packet)
        assert "packet_not_mappable" in html, "expected a data-error code"
        assert "render_error" not in html, "a coverage failure is not a render failure"
        assert "no API call was spent" in html, "expected the remediation"
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)


def test_a_render_stays_out_of_the_store_until_the_reviewer_saves_it():
    """B3a's inversion of B2c's first state: entry into deck history is the
    reviewer's decision, so a successful render writes no row at all. Pressing
    Save deck is what writes one, and on durable storage it does so without a
    warning — a warning on every local run is a warning nobody reads."""
    if not _HAVE_FLASK:
        return
    from deck_store import list_decks

    store_dir = tempfile.mkdtemp(prefix="review-store-")
    deck_dir = tempfile.mkdtemp(prefix="review-deck-")
    store_path = os.path.join(store_dir, "decks.sqlite3")
    try:
        os.environ["DECK_STORE_DIR"] = store_dir
        ui_app, client, deck_path = _studio(deck_dir)
        packet = _copy_packet()
        try:
            panel = _result_panel(_run(client, packet))
            assert "Save deck" in panel, "the Result tab must offer the save"
            assert list_decks(store_path=store_path) == [], (
                "a render must not enter deck history on its own"
            )

            panel = _result_panel(_save(client, deck_path, packet))
            assert "not persisting" not in panel, "a durable store needs no notice"
            assert "Saved to deck history" in panel, "the save must confirm itself"
            rows = list_decks(company="Northwind", project="Impl", store_path=store_path)
            assert rows, "the saved deck should be in the store"
        finally:
            shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
    finally:
        shutil.rmtree(store_dir, ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


def test_the_durability_notice_fires_on_the_save_not_on_the_render():
    """B2c's second state, moved by B3a to where it belongs. On the hosting
    platform with no volume mounted, resolution falls through to a container path
    that a deploy wipes. The render itself says nothing, because it was never
    going to persist; the notice lands on the save, where it tells the reviewer
    that the thing they just chose to keep will not survive a redeploy. It names
    every candidate it checked and is on screen rather than only in a log, since
    a silent miss reads identically to a working history right up until the next
    deploy."""
    if not _HAVE_FLASK:
        return
    import store_dir_resolver

    deck_dir = tempfile.mkdtemp(prefix="review-deck-")
    container_dir = tempfile.mkdtemp(prefix="review-container-")
    saved_local = store_dir_resolver.REPO_LOCAL_DIR
    # _studio imports app.py first, so RAILWAY_ENVIRONMENT is set only after the
    # module-level hosted-login gate has already read it as absent.
    ui_app, client, deck_path = _studio(deck_dir)
    try:
        store_dir_resolver.REPO_LOCAL_DIR = os.path.join(container_dir, "deck-store")
        os.environ.pop("DECK_STORE_DIR", None)
        os.environ[store_dir_resolver.PLATFORM_VAR] = "production"
        packet = _copy_packet()
        try:
            panel = _result_panel(_run(client, packet))
            assert "not persisting" not in panel, (
                "a render persists nothing, so it has nothing to warn about"
            )

            panel = _result_panel(_save(client, deck_path, packet))
            assert "not persisting" in panel, "expected a visible notice"
            assert "not on durable storage" in panel, "expected the durability reason"
            assert "DECK_STORE_DIR" in panel, "the notice should name what it checked"
            assert os.path.isfile(deck_path), "the filesystem write must still happen"
        finally:
            shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
    finally:
        store_dir_resolver.REPO_LOCAL_DIR = saved_local
        os.environ.pop(store_dir_resolver.PLATFORM_VAR, None)
        shutil.rmtree(container_dir, ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


def test_decks_tab_reads_from_the_store_not_the_filesystem():
    """B3's first behavior: with a durable store, the Decks tab lists what the
    store knows about, not what happens to be sitting on disk."""
    if not _HAVE_FLASK:
        return
    from deck_store import save_deck

    store_dir = tempfile.mkdtemp(prefix="review-store-")
    deck_dir = tempfile.mkdtemp(prefix="review-deck-")
    os.environ["DECK_STORE_DIR"] = store_dir
    import app as ui_app
    ui_app.DECKS_ROOT = deck_dir
    ui_app.app.testing = True
    ui_app._LAST_RESULT.clear()
    # A file on disk that was never saved to the store.
    stray_dir = os.path.join(deck_dir, "Northwind", ui_app.DECK_CODE_SUBDIR)
    os.makedirs(stray_dir)
    with open(os.path.join(stray_dir, "output-99.html"), "w", encoding="utf-8") as f:
        f.write(DECK_HTML)
    save_deck("status", "Northwind", "Impl", DECK_HTML,
             store_path=os.path.join(store_dir, "decks.sqlite3"))
    try:
        client = ui_app.app.test_client()
        panel = _decks_panel(client.get("/?tab=decks").get_data(as_text=True))
        assert "read from the database" in panel, "expected the store-backed listing copy"
        assert "output-99.html" not in panel, (
            "a file never saved to the store must not appear in a store-backed listing"
        )
        assert '<span class="pill">Northwind</span>' in panel
    finally:
        shutil.rmtree(store_dir, ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)


def test_decks_tab_row_shows_client_project_type_timestamp_and_final_state():
    """B3's second behavior: each stored row reports the fields the tab promises,
    including the final flag — the column that reads false on every row today,
    per PROMPT-QUEUE's note that B4, not this prompt, decides what marks a
    render final."""
    if not _HAVE_FLASK:
        return
    from deck_store import save_deck, list_decks

    store_dir = tempfile.mkdtemp(prefix="review-store-")
    os.environ["DECK_STORE_DIR"] = store_dir
    import app as ui_app
    ui_app.app.testing = True
    ui_app._LAST_RESULT.clear()
    store_path = os.path.join(store_dir, "decks.sqlite3")
    save_deck("proposal", "Northwind", "Rollout", DECK_HTML, is_final=True, store_path=store_path)
    try:
        row = list_decks(store_path=store_path)[0]
        client = ui_app.app.test_client()
        panel = _decks_panel(client.get("/?tab=decks").get_data(as_text=True))
        assert '<span class="pill">Northwind</span>' in panel
        assert "<td>Rollout</td>" in panel
        assert '<td class="muted">proposal</td>' in panel
        assert row["created_at"].strftime("%Y-%m-%d %H:%M") in panel
        assert ">final<" in panel, "an is_final row should read as final, not not-final"
    finally:
        shutil.rmtree(store_dir, ignore_errors=True)


def test_opening_a_stored_deck_restores_the_full_result_view():
    """B3's third behavior: `/deck-view?id=` gives back the whole run record —
    fidelity, applied preferences, flagged claims, and edit history — not just
    the bare HTML, because a row opened from another session has nothing else to
    rebuild the view from."""
    if not _HAVE_FLASK:
        return
    from deck_store import list_decks

    store_dir = tempfile.mkdtemp(prefix="review-store-")
    deck_dir = tempfile.mkdtemp(prefix="review-deck-")
    os.environ["DECK_STORE_DIR"] = store_dir
    ui_app, client, deck_path = _studio(deck_dir)
    packet = _copy_packet()
    try:
        _run(client, packet)
        _edit(client, deck_path, packet, "$275,000", "$310,000")
        _save(client, os.path.join(deck_dir, "output-7-r1.html"), packet)
        rows = list_decks(company="Northwind", project="Impl",
                          store_path=os.path.join(store_dir, "decks.sqlite3"))
        assert rows, "expected the save to have written a row to the store"
        deck_id = rows[0]["id"]

        html = client.get("/deck-view", query_string={"id": deck_id}).get_data(as_text=True)
        panel = _result_panel(html)
        assert "layout" in panel, "the guards' report must come back"
        assert "Edit history" in panel, "the stored deck's edits must come back"
        assert "Edit history" in panel, "the edit revision chain must come back"
        assert "output-7-r1.html" in panel, "the edit made after the run should be in it"
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)
        shutil.rmtree(store_dir, ignore_errors=True)


def test_deck_store_delete_removes_a_row_and_bulk_delete_removes_several():
    """B3's fourth behavior: per-row delete and bulk delete both work against
    the store, the same way they already do against the filesystem."""
    if not _HAVE_FLASK:
        return
    from deck_store import save_deck, list_decks

    store_dir = tempfile.mkdtemp(prefix="review-store-")
    os.environ["DECK_STORE_DIR"] = store_dir
    import app as ui_app
    ui_app.app.testing = True
    ui_app._LAST_RESULT.clear()
    store_path = os.path.join(store_dir, "decks.sqlite3")
    # Three different projects, because since B3a a second save of the same
    # client/project/deck type updates the standing row rather than adding one.
    ids = [save_deck("status", "Northwind", project, DECK_HTML, store_path=store_path)
           for project in ("Impl", "Rollout", "Phase 2")]
    client = ui_app.app.test_client()
    try:
        resp = client.post("/deck-delete", data={"id": str(ids[0])})
        assert resp.status_code == 302
        remaining = {row["id"] for row in list_decks(store_path=store_path)}
        assert remaining == set(ids[1:]), "per-row delete should remove exactly that row"

        resp = client.post("/deck-delete-bulk",
                           data={"selected": [str(ids[1]), str(ids[2])]})
        assert resp.status_code == 302
        assert list_decks(store_path=store_path) == [], (
            "bulk delete should remove every selected row"
        )
    finally:
        shutil.rmtree(store_dir, ignore_errors=True)


def test_decks_tab_falls_back_to_filesystem_with_a_visible_notice_when_the_store_is_unreadable():
    """B3's fifth behavior: an unreadable store must not read as an empty
    history. The tab still lists what is on disk, and says why it is not
    showing the durable history — the fallback prompt B2c and B2d built for the
    save path, exercised here from the Decks tab's own load instead."""
    if not _HAVE_FLASK:
        return
    root = tempfile.mkdtemp(prefix="review-root-")
    import app as ui_app
    code_dir = os.path.join(root, "Northwind", ui_app.DECK_CODE_SUBDIR)
    os.makedirs(code_dir)
    with open(os.path.join(code_dir, "output-1.html"), "w", encoding="utf-8") as f:
        f.write(DECK_HTML)
    original_root = ui_app.DECKS_ROOT
    ui_app.DECKS_ROOT = root
    ui_app.app.testing = True
    ui_app._LAST_RESULT.clear()
    restore_store = _unreadable_store()
    try:
        client = ui_app.app.test_client()
        panel = _decks_panel(client.get("/?tab=decks").get_data(as_text=True))
        assert "output-1.html" in panel, "expected the filesystem fallback listing"
        assert "not persisting" in panel, (
            "an unreadable store must not look like an empty history"
        )
        assert "could not be read" in panel
    finally:
        ui_app.DECKS_ROOT = original_root
        restore_store()
        shutil.rmtree(root, ignore_errors=True)


def test_a_saved_deck_comes_back_edited_once_its_files_are_gone():
    """B3a's real bug. The store used to be written once, at generation, and
    never again: the edit routes did not touch it and `details` carried a
    `deck_path` pointing back at the local filesystem. Locally that is invisible
    because the file is still sitting there — so the deck directory is deleted
    here before the row is read back, which is exactly what a redeploy does to
    the hosted service and the only version of this test that proves anything.

    What must survive: the HTML of the revision on screen, not the original
    render, plus the edit chain that produced it."""
    if not _HAVE_FLASK:
        return
    from deck_store import get_deck, list_decks

    store_dir = tempfile.mkdtemp(prefix="review-store-")
    deck_dir = tempfile.mkdtemp(prefix="review-deck-")
    os.environ["DECK_STORE_DIR"] = store_dir
    store_path = os.path.join(store_dir, "decks.sqlite3")
    ui_app, client, deck_path = _studio(deck_dir)
    packet = _copy_packet()
    try:
        _run(client, packet)
        _edit(client, deck_path, packet, "$275,000", "$310,000")
        _save(client, os.path.join(deck_dir, "output-7-r1.html"), packet)

        shutil.rmtree(deck_dir, ignore_errors=True)
        row = get_deck(list_decks(store_path=store_path)[0]["id"],
                       store_path=store_path)
        assert "$310,000" in row["html"], "the saved deck must come back edited"
        assert "$275,000" not in row["html"], (
            "the row must carry the revision the reviewer saved, not the render"
        )
        edits = row["details"]["edits"]
        assert edits and edits[0]["after"] == "$310,000", (
            "the edit chain must survive the files it was made against"
        )

        panel = _result_panel(
            client.get("/deck-view", query_string={"id": row["id"]}).get_data(as_text=True))
        assert "Edit history" in panel, "the chain must be visible, not just stored"
        assert "Apply edit" not in panel, (
            "a deck with no file behind it must not offer edits against it"
        )
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)
        shutil.rmtree(store_dir, ignore_errors=True)


def test_saving_the_same_deck_twice_updates_one_row():
    """B3a's identity rule: a deck is its client, project, and deck type. Save,
    edit, save again, and history holds one row carrying the later state, because
    the reviewer settled on the deck rather than on one of its versions."""
    if not _HAVE_FLASK:
        return
    from deck_store import get_deck, list_decks

    store_dir = tempfile.mkdtemp(prefix="review-store-")
    deck_dir = tempfile.mkdtemp(prefix="review-deck-")
    os.environ["DECK_STORE_DIR"] = store_dir
    store_path = os.path.join(store_dir, "decks.sqlite3")
    ui_app, client, deck_path = _studio(deck_dir)
    packet = _copy_packet()
    try:
        _run(client, packet)
        _save(client, deck_path, packet)
        _edit(client, deck_path, packet, "$275,000", "$310,000")
        _save(client, os.path.join(deck_dir, "output-7-r1.html"), packet)

        rows = list_decks(store_path=store_path)
        assert len(rows) == 1, "a second save must update the row, not add one"
        assert "$310,000" in get_deck(rows[0]["id"], store_path=store_path)["html"], (
            "the surviving row must carry the later state"
        )
        assert rows[0]["is_final"] is True, "a saved deck is final by definition"
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(deck_dir, ignore_errors=True)
        shutil.rmtree(store_dir, ignore_errors=True)


def test_download_pdf_matches_the_html_geometry():
    """Item 14 (B6): a PDF download button beside the HTML one. The HTML path
    must stay byte-for-byte untouched, and the PDF must print at the deck's own
    fixed 1280x720 slide size rather than reflow into a browser's default page
    format — a silently reflowed PDF would be worse than no PDF at all."""
    if not _HAVE_FLASK or not _HAVE_PLAYWRIGHT:
        return
    deck_dir = tempfile.mkdtemp(prefix="review-deck-pdf-")
    ui_app, client, deck_path = _studio(deck_dir)
    with open(deck_path, "w", encoding="utf-8") as f:
        f.write(PDF_DECK_HTML)
    try:
        html_resp = client.get(f"/download?path={deck_path}")
        assert html_resp.status_code == 200
        assert html_resp.get_data(as_text=True) == PDF_DECK_HTML, (
            "the existing HTML download must be untouched by adding the PDF one"
        )

        pdf_resp = client.get(f"/download-pdf?path={deck_path}")
        assert pdf_resp.status_code == 200
        assert pdf_resp.mimetype == "application/pdf"
        data = pdf_resp.get_data()
        assert data.startswith(b"%PDF"), "must be a real PDF, not an error page"

        # 1280x720 CSS px at 96dpi is 960x540pt; the MediaBox is what a PDF
        # reader actually pages by, so it is the only honest check of whether
        # the export preserved the slide geometry or reflowed it.
        boxes = re.findall(rb"/MediaBox\s*\[0 0 ([\d.]+) ([\d.]+)\]", data)
        assert len(boxes) == 2, "one PDF page per slide"
        for w, h in boxes:
            assert abs(float(w) - 960) < 2 and abs(float(h) - 540) < 2, (
                f"page {w!r}x{h!r}pt does not match the fixed slide size — "
                "the export reflowed instead of printing the deck's own geometry"
            )
    finally:
        shutil.rmtree(deck_dir, ignore_errors=True)


def test_the_generate_form_no_longer_asks_for_commercial_terms():
    """Moved to the Result tab (2026-08-20). They were collected here, before the
    deck existed, while every other figure on that slide arrived as a
    `[MISSING: ...]` row in the supply-missing card afterwards — two places and two
    shapes for one slide, which is most of why Antonio called the commercial
    editing confusing. A reviewer also settles terms after seeing the deck, not
    before it."""
    if not _HAVE_FLASK:
        return
    import app as ui_app

    ui_app.app.testing = True
    html = ui_app.app.test_client().get("/").get_data(as_text=True)
    assert 'name="commercial_row_label"' not in html
    assert 'name="commercial_row_value"' not in html
    assert "addCommercialRow" not in html


def test_a_run_forwards_no_commercial_rows_but_the_kwarg_still_exists():
    """The form is gone; the capability is not. `generate_and_save_deck` and the
    adapter still take `commercial_rows` for a programmatic caller that genuinely
    has the terms up front, so this moved a form rather than removing a feature.
    A run from the studio simply supplies none, and the render draws its own
    "awaiting commercial terms input" state."""
    if not _HAVE_FLASK:
        return
    import inspect

    import app as ui_app

    captured = {}

    def capturing_stub(*args, **kwargs):
        captured["kwargs"] = kwargs
        return dict(RUN_RESULT)

    real = ui_app.generate_and_save_deck
    ui_app.generate_and_save_deck = capturing_stub
    ui_app.app.testing = True
    ui_app._LAST_RESULT.clear()
    client = ui_app.app.test_client()
    packet = _copy_packet()
    try:
        run_deck(client, data={
            "deck_type": "proposal", "company": "Ridgeline Site Services",
            "project": "Operational Intelligence Platform", "packet": packet,
        })
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        ui_app.generate_and_save_deck = real

    assert not captured["kwargs"].get("commercial_rows")
    assert "commercial_rows" in inspect.signature(
        ui_app._run_and_render).parameters


if __name__ == "__main__":
    if not _HAVE_FLASK:
        print("flask not installed; skipping UI tests")
    else:
        for name, fn in sorted(list(globals().items())):
            if name.startswith("test_") and callable(fn):
                fn()
                print(f"ok  {name}")
        print("all review-surface UI tests passed")
