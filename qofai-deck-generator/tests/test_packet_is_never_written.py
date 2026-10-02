"""The data packet is immutable. Nothing in this repo may write one.

The packet is the data source's record of what it sent, and the agent's entire
guarantee is faithful reproduction of it. If the agent can edit a packet, the
packet stops being evidence of anything (Antonio, 2026-07-28).

An earlier build broke this in one place: confirming a flagged claim in the review
UI deleted that gap entry from the packet on disk. Two ways that was wrong. Locally
it edited a committed fixture — it removed ``commercial.qofai_investment_usd`` from
``proposal-data-packet-EXAMPLE.md`` during a review pass and turned four tests red,
the same failure the 2026-07-23 changelog entry records. On the hosted service,
where packets live in the deployed repo image, the edit was silently discarded on
the very next deploy, so a reviewer's confirmation quietly evaporated.

These tests are the enforcement, at two levels:

1. Behavioural: drive the full resolve / keep / reopen flow through the UI against a
   real packet copy and assert the file is byte-for-byte identical afterwards.
2. Structural: assert no module opens a packet for writing, and that the old
   packet-editing entry point is gone rather than merely unused — dead code that
   edits packets is one import away from being live again.

Run with: python3 tests/test_packet_is_never_written.py
"""

import hashlib
import os
import re
import shutil
import sys
import tempfile
from deck_run import run_deck

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ui"))

import gap_decisions

try:
    import flask  # noqa: F401
    _HAVE_FLASK = True
except ImportError:  # UI-only dependency; skip the route-level checks if absent
    _HAVE_FLASK = False

_ROOT = os.path.join(os.path.dirname(__file__), "..")
STATUS_PACKET = os.path.join(_ROOT, "status-data-packet-EXAMPLE.md")
PROPOSAL_PACKET = os.path.join(_ROOT, "proposal-data-packet-EXAMPLE.md")


def _digest(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _temp_copy(src):
    tmp = tempfile.mkdtemp(prefix="packet-immutable-")
    dst = os.path.join(tmp, os.path.basename(src))
    shutil.copyfile(src, dst)
    return dst


def _studio(decisions_path):
    import app as ui_app

    ui_app.generate_and_save_deck = lambda *a, **k: {
        "status": "ok", "prompt": "(design prompt body)", "prompt_path": None,
        "applied_preferences": [], "number": 1,
    }
    ui_app.DECISIONS_PATH = decisions_path
    ui_app.app.testing = True
    ui_app._LAST_RESULT.clear()
    return ui_app, ui_app.app.test_client()


def test_confirming_a_claim_leaves_the_packet_byte_identical():
    """The whole point. A reviewer confirms a value; the packet does not move."""
    if not _HAVE_FLASK:
        return
    packet = _temp_copy(STATUS_PACKET)
    decisions = os.path.join(os.path.dirname(packet), "decisions.json")
    _, client = _studio(decisions)
    before = _digest(packet)
    try:
        html = client.post("/gap-decision", data={
            "action": "resolve",
            "field": "workstreams[0].after.metrics[0].value",
            "deck_type": "status", "company": "Northwind", "project": "Impl",
            "packet": packet, "check_in_date": "2026-05-22",
        }).get_data(as_text=True)
        assert _digest(packet) == before, "the packet was modified by a confirm"
        # The claim moved to the confirmed list rather than vanishing. Read from
        # the store, not the page: the flagged-claims card was removed from the
        # studio on 2026-09-20 and the decision outlived its display.
        assert gap_decisions.resolved_fields(packet, path=decisions) == {
            "workstreams[0].after.metrics[0].value"}
        # And the packet still declares it — that is the source record, untouched.
        with open(packet, encoding="utf-8") as f:
            assert "modeled goal, not a measured actual" in f.read()
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)


def test_keep_and_reopen_also_leave_the_packet_byte_identical():
    if not _HAVE_FLASK:
        return
    packet = _temp_copy(PROPOSAL_PACKET)
    decisions = os.path.join(os.path.dirname(packet), "decisions.json")
    _, client = _studio(decisions)
    before = _digest(packet)
    field = "commercial.qofai_investment_usd"
    common = {
        "deck_type": "proposal", "company": "Ridgeline", "project": "OIP",
        "packet": packet, "check_in_date": "",
    }
    try:
        for action in ("keep", "resolve", "resolve", "reopen", "reopen"):
            client.post("/gap-decision", data={**common, "action": action,
                                               "field": field})
            assert _digest(packet) == before, f"packet modified by '{action}'"
        # Reopened, so it is back on the open checklist where it started.
        client.post("/gap-decision", data={**common, "action": "keep",
                                           "field": field})
        assert field not in gap_decisions.resolved_fields(packet, path=decisions), \
            "reopen should restore the open claim"
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)


def test_a_confirmation_survives_without_the_packet_changing():
    """The decision has to persist across runs — that is why it was written into
    the packet in the first place. It now persists beside it instead."""
    if not _HAVE_FLASK:
        return
    packet = _temp_copy(STATUS_PACKET)
    decisions = os.path.join(os.path.dirname(packet), "decisions.json")
    ui_app, client = _studio(decisions)
    before = _digest(packet)
    field = "workstreams[0].after.metrics[0].value"
    try:
        client.post("/gap-decision", data={
            "action": "resolve", "field": field, "deck_type": "status",
            "company": "Northwind", "project": "Impl", "packet": packet,
            "check_in_date": "2026-05-22",
        })
        # A fresh pipeline run against the same packet still sees it confirmed.
        run_deck(client, data={
            "deck_type": "status", "company": "Northwind", "project": "Impl",
            "packet": packet, "check_in_date": "2026-05-22",
        })
        assert field in gap_decisions.resolved_fields(packet, path=decisions), \
            "the confirmation should persist across a fresh run"
        assert _digest(packet) == before, "the packet was modified"
    finally:
        shutil.rmtree(os.path.dirname(packet), ignore_errors=True)


# ---- structural: the capability is gone, not just unused -------------------

def test_no_module_opens_a_packet_for_writing():
    """No source file may open a `.md` packet path for writing.

    Scans src/, ui/ and scripts/ for a write-mode open whose target names a packet.
    A blunt check on purpose: the guarantee is "never", so the cheapest way to keep
    it is to make any new packet write fail here loudly.
    """
    write_open = re.compile(r"open\(\s*([^)]*?)\s*,\s*[\"'][wa]", re.DOTALL)
    offenders = []
    for folder in ("src", "ui", "scripts"):
        base = os.path.join(_ROOT, folder)
        for name in sorted(os.listdir(base)):
            if not name.endswith(".py"):
                continue
            path = os.path.join(base, name)
            with open(path, encoding="utf-8") as f:
                source = f.read()
            for match in write_open.finditer(source):
                target = match.group(1)
                if "packet" in target.lower():
                    offenders.append(f"{folder}/{name}: open({target}, 'w')")
    assert not offenders, (
        "a packet must never be opened for writing; found:\n  "
        + "\n  ".join(offenders)
    )


def test_the_packet_editing_entry_point_is_gone():
    """`gap_resolver.resolve_gap` cut an entry out of a packet's gaps block.

    Asserting it is absent, rather than just unused, because dead code that edits a
    packet is one import away from being live again.
    """
    import gap_resolver

    assert not hasattr(gap_resolver, "resolve_gap"), (
        "gap_resolver.resolve_gap edits the packet and must stay removed"
    )
    with open(os.path.join(_ROOT, "src", "gap_resolver.py"), encoding="utf-8") as f:
        source = f.read()
    assert "def resolve_gap" not in source


def test_the_decision_store_is_the_only_thing_that_moves():
    """A confirmation writes exactly one file: the decision store."""
    import gap_decisions

    packet = _temp_copy(STATUS_PACKET)
    folder = os.path.dirname(packet)
    decisions = os.path.join(folder, "decisions.json")
    before = _digest(packet)
    try:
        assert gap_decisions.resolve(packet, "some.field", path=decisions) is True
        # Idempotent, so a double submission is not a second record.
        assert gap_decisions.resolve(packet, "some.field", path=decisions) is False
        assert gap_decisions.resolved_fields(packet, path=decisions) == {"some.field"}
        assert _digest(packet) == before
        assert sorted(os.listdir(folder)) == sorted(
            [os.path.basename(packet), "decisions.json"]
        ), os.listdir(folder)
        assert gap_decisions.reopen(packet, "some.field", path=decisions) is True
        assert gap_decisions.resolved_fields(packet, path=decisions) == set()
        assert _digest(packet) == before
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_packet_key_is_stable_across_checkout_locations():
    """A decision must still match after a deploy, where absolute paths differ."""
    import gap_decisions

    relative = "status-data-packet-EXAMPLE.md"
    absolute = os.path.abspath(os.path.join(_ROOT, relative))
    assert gap_decisions.packet_key(relative) == gap_decisions.packet_key(absolute)
    assert gap_decisions.packet_key(relative) == relative
    assert gap_decisions.packet_key("") == ""


if __name__ == "__main__":
    if not _HAVE_FLASK:
        print("flask not installed; running structural checks only")
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all packet-immutability tests passed")
