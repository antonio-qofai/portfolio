"""The whole flagged-claim resolution path, both directions, both deck types (C4).

A reviewer confirms a flagged claim or sends it back to be verified. Neither
direction edits the deck, and neither edits the packet.

Confirming does not strip a marker from the rendered deck, because a rendered deck
never carries one: `generate_and_save_deck` runs `deck_renderer.strip_gap_flags`
on every render, after the fidelity guard has confirmed the markers survived and
before the file is written, so the artifact is always clean (Antonio, 2026-07-21 —
a gap flag is an internal review note, not client-facing deck copy). That is
pinned beside the call it protects, in
`tests/test_deck_generator.py::test_a_saved_deck_carries_no_unconfirmed_marker`,
on both deck types. Read it before this file: it is why a Confirm here has nothing
to strip, and before it existed deleting that one call turned nothing red.

The half of C4 that was genuinely broken is the send-back. It wrote nothing at
all, so a claim a reviewer deliberately routed was gone on the next page render
and read exactly like one nobody had opened.

Run with: python3 tests/test_gap_flag_resolution.py
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ui"))

import gap_decisions
from deck_run import run_deck

try:
    import flask  # noqa: F401
    _HAVE_FLASK = True
except ImportError:  # UI-only dependency; skip if absent
    _HAVE_FLASK = False

_ROOT = os.path.join(os.path.dirname(__file__), "..")
MARKER = "(unconfirmed, see gaps)"

# One case per deck type: its example packet, the two gap fields that packet
# declares, and the client and project the fixture provider expects. The only
# place client details live in this file.
CASES = [
    {
        "deck_type": "status",
        "packet": os.path.join(_ROOT, "status-data-packet-EXAMPLE.md"),
        "company": "Northwind",
        "project": "Implementation Project",
        "confirm_field": "workstreams[0].after.metrics[0].value",
        "send_back_field": "tracking.slip_or_buffer_markers[1]",
    },
    {
        "deck_type": "proposal",
        "packet": os.path.join(_ROOT, "proposal-data-packet-EXAMPLE.md"),
        "company": "Ridgeline Site Services",
        "project": "Operational Intelligence Platform",
        "confirm_field": "commercial.qofai_investment_usd",
        "send_back_field": "baseline.baseline_locked_date",
    },
]


def test_a_decision_can_be_changed_or_cleared():
    """One claim holds one decision. Sending back then confirming rewrites it
    rather than stacking a second entry, and clearing reverses either direction."""
    case = CASES[0]
    field = case["send_back_field"]
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "decisions.json")
        assert gap_decisions.send_back(case["packet"], field, path=path) is True
        assert gap_decisions.send_back(case["packet"], field, path=path) is False
        assert gap_decisions.resolved_fields(case["packet"], path) == set(), (
            "sending a claim back must not settle it"
        )
        assert gap_decisions.resolve(case["packet"], field, path=path) is True
        assert len(gap_decisions.load_decisions(path)["decisions"]) == 1
        assert gap_decisions.resolved_fields(case["packet"], path) == {field}
        assert gap_decisions.reopen(case["packet"], field, path=path) is True
        assert gap_decisions.decisions_by_field(case["packet"], path) == {}


def _client(decisions_path):
    import app as ui_app

    ui_app.generate_and_save_deck = lambda *a, **k: {
        "status": "ok", "prompt": "(design prompt body)", "prompt_path": None,
        "applied_preferences": [], "number": 1,
    }
    ui_app.DECISIONS_PATH = decisions_path
    ui_app.app.testing = True
    return ui_app, ui_app.app.test_client()


def _post(client, case, action, field, packet, deck_path=""):
    return client.post("/gap-decision", data={
        "action": action, "field": field, "deck_type": case["deck_type"],
        "company": case["company"], "project": case["project"], "packet": packet,
        "check_in_date": "2026-05-22", "deck_path": deck_path,
    }).get_data(as_text=True)


def _case_dir(case, prefix):
    """A temp dir holding a COPY of the packet, so a decision can never reach the
    committed one, plus the decision store this run writes to."""
    tmp = tempfile.mkdtemp(prefix=prefix)
    packet = os.path.join(tmp, "packet.md")
    shutil.copyfile(case["packet"], packet)
    return tmp, packet, os.path.join(tmp, "decisions.json")


def test_both_directions_work_on_both_deck_types():
    """Confirm one claim and send the other back: one moves to the confirmed list,
    one stays open and marked, both are recorded, and the packet is untouched."""
    if not _HAVE_FLASK:
        return
    for case in CASES:
        tmp, packet, store = _case_dir(case, "gap-c4-")
        before = open(packet, encoding="utf-8").read()
        try:
            _, client = _client(store)
            _post(client, case, "resolve", case["confirm_field"], packet)
            _post(client, case, "verify", case["send_back_field"], packet)
            assert gap_decisions.decisions_by_field(packet, store) == {
                case["confirm_field"]: gap_decisions.CONFIRMED,
                case["send_back_field"]: gap_decisions.NEEDS_VERIFICATION,
            }, "both decisions belong in the store"
            assert open(packet, encoding="utf-8").read() == before, "packet edited"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def test_a_sent_back_claim_is_still_marked_after_a_fresh_run():
    """The routing outlives the page it was made on. This is the C4 bug: the old
    keep wrote nothing, so the next render showed the claim as though untouched."""
    if not _HAVE_FLASK:
        return
    case = CASES[0]
    tmp, packet, store = _case_dir(case, "gap-c4-reload-")
    try:
        _, client = _client(store)
        _post(client, case, "verify", case["send_back_field"], packet)
        run_deck(client, data={
            "deck_type": case["deck_type"], "company": case["company"],
            "project": case["project"], "packet": packet,
            "check_in_date": "2026-05-22",
        })
        # The routing outlives the run that follows it. Read from the store: the
        # page that used to show it was removed on 2026-09-20.
        assert gap_decisions.decisions_by_field(packet, store) == {
            case["send_back_field"]: gap_decisions.NEEDS_VERIFICATION,
        }, "a send-back must survive a fresh run"
        assert gap_decisions.resolved_fields(packet, store) == set(), (
            "a sent-back claim is not a confirmed one"
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_neither_direction_touches_the_deck():
    """A decision is a checklist action: the deck is byte-identical after both."""
    if not _HAVE_FLASK:
        return
    case = CASES[0]
    tmp, packet, store = _case_dir(case, "gap-c4-deck-")
    ui_app, client = _client(store)
    original_root = ui_app.DECKS_ROOT
    ui_app.DECKS_ROOT = tmp  # the route only accepts a deck inside the root
    try:
        deck_path = os.path.join(tmp, "output-1.html")
        with open(deck_path, "w", encoding="utf-8") as f:
            f.write('<!doctype html><html><body><section class="slide">'
                    "<p>A modeled figure</p></section></body></html>")
        before = open(deck_path, encoding="utf-8").read()
        for action, field in (("resolve", case["confirm_field"]),
                              ("verify", case["send_back_field"])):
            _post(client, case, action, field, packet, deck_path=deck_path)
            after = open(deck_path, encoding="utf-8").read()
            assert after == before, f"{action} rewrote the deck"
            assert MARKER not in after
    finally:
        ui_app.DECKS_ROOT = original_root
        shutil.rmtree(tmp, ignore_errors=True)


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
