"""End-to-end test for the review UI's flagged-claims panel.

Drives the Flask app with its test client. The pipeline
(``generate_and_save_deck``) is stubbed to a canned OK result, so no API key,
no network, and no output files are needed — the test targets the routing, the
flagged-claims card, and the resolve/keep write-back only. The packet is a temp
COPY, so the resolve write-back never mutates a real packet.

Run with: python3 tests/test_ui_gap_decision.py
"""

import os
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

_ROOT = os.path.join(os.path.dirname(__file__), "..")
STATUS_PACKET = os.path.join(_ROOT, "status-data-packet-EXAMPLE.md")


def _ok_result(*_args, **_kwargs):
    return {
        "status": "ok",
        "prompt": "(design prompt body)",
        "prompt_path": None,
        "applied_preferences": [],
        "number": 1,
    }


def _client_with_stub():
    import app as ui_app

    ui_app.generate_and_save_deck = _ok_result
    ui_app.app.testing = True
    return ui_app, ui_app.app.test_client()


def _copy_packet():
    tmp = tempfile.mkdtemp(prefix="gap-ui-")
    dst = os.path.join(tmp, "packet.md")
    shutil.copyfile(STATUS_PACKET, dst)
    return dst


def test_layout_findings_are_surfaced_on_the_result_tab():
    """A clipped label is invisible in the HTML, so the UI has to say it out loud —
    with the slide to go look at (Antonio, 2026-07-24)."""
    if not _HAVE_FLASK:
        return
    import app as ui_app

    deck = os.path.join(tempfile.mkdtemp(prefix="layout-ui-"), "output-1.html")
    with open(deck, "w", encoding="utf-8") as f:
        f.write("<!doctype html><html><body><section class='slide'></section></body></html>")
    ui_app.generate_and_save_deck = lambda *a, **k: {
        "status": "ok", "prompt": "(design prompt body)", "prompt_path": None,
        "applied_preferences": [], "number": 1, "deck_path": deck,
        "render_fidelity": {"ok": True, "missing_values": {}},
        "layout": {
            "ok": False, "checked": True, "skipped": "", "slides": 6,
            "findings": [{
                "kind": "clipped", "axis": "horizontal", "slide": 4,
                "element": "div.bar.bar--p2", "overflow_px": 109.0,
                "text": "Reporting package + asset tracking",
            }],
            "summary": ["Slide 4: div.bar.bar--p2 clips its own text by 109px"],
        },
    }
    ui_app.app.testing = True
    client = ui_app.app.test_client()
    packet = _copy_packet()
    try:
        resp = run_deck(client, data={
            "deck_type": "status", "company": "Northwind", "project": "Impl",
            "packet": packet, "check_in_date": "2026-05-22",
        })
        html = resp.get_data(as_text=True)
        assert "Layout problems (1)" in html, "expected the layout card"
        assert "Slide 4" in html and "109.0px" in html, "expected slide and pixels"
        assert "Reporting package + asset tracking" in html, "expected the cut text"
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)
        shutil.rmtree(os.path.dirname(deck), ignore_errors=True)


def test_deleted_preference_drops_off_the_result_tab():
    """Deleting a standing preference removes it from the Result tab's applied
    list too — it must not linger as something still shaping the deck."""
    if not _HAVE_FLASK:
        return
    ui_app, client = _client_with_stub()
    packet = _copy_packet()
    store = os.path.join(os.path.dirname(packet), "prefs.json")
    original_store = ui_app.STORE_PATH
    ui_app.STORE_PATH = store
    try:
        pref = ui_app.add_preference("tighten the tracker whitespace",
                                     scope="status", path=store)
        # A run whose result carries that preference as applied.
        ui_app.generate_and_save_deck = lambda *a, **k: {
            "status": "ok", "prompt": "(design prompt body)", "prompt_path": None,
            "applied_preferences": ["tighten the tracker whitespace"], "number": 1,
        }
        resp = run_deck(client, data={
            "deck_type": "status", "company": "Northwind", "project": "Impl",
            "packet": packet, "check_in_date": "2026-05-22",
        })
        # ASSERTED ON THE CONTEXT, NOT ON THE PAGE (2026-09-21). The card that
        # printed this list is gone (Antonio: "Remove the section on applied
        # styling preferences"), and the filtering it demonstrated is not: the
        # applied list still rides on the run outcome and is still filtered
        # against the store on every render.
        #
        # Grepping the page for the note would now pass for the wrong reason.
        # All four panels render server-side into one document, so the
        # PREFERENCES tab prints every note in the store — a test looking for
        # the note in `html` would find the Preferences row and report the
        # Result tab working whatever the Result tab did.
        assert resp.status_code in (200, 302)
        ctx = ui_app._recall_result_ctx()
        assert "tighten the tracker whitespace" in (ctx or {}).get("applied", []), (
            "a live preference must stay on the run's applied list"
        )

        # Delete it: the cached run is unchanged, but the applied list is
        # filtered against the store on every render.
        ui_app.delete_preference(pref["id"], path=store)
        ctx = ui_app._recall_result_ctx()
        assert "tighten the tracker whitespace" not in (ctx or {}).get("applied", []), (
            "a deleted preference must drop off the run's applied list"
        )

        # And the card itself stays gone, whatever the list holds.
        html = client.get("/?tab=result").get_data(as_text=True)
        assert "Applied standing preferences" not in html
    finally:
        ui_app.STORE_PATH = original_store
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)


if __name__ == "__main__":
    if not _HAVE_FLASK:
        print("SKIP  flask not installed")
        sys.exit(0)
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
