"""Reviewer decisions on flagged claims, stored OUTSIDE the data packet.

A packet's provenance block declares which values the data source could not
confirm. The review UI lists them, and a reviewer either confirms one (resolve)
or sends it back to be verified (send_back). This module is where that decision
lives, and BOTH directions are recorded: a claim somebody routed for verification
must not read like a claim nobody has opened.

The packet is never written. It is the data source's record of what it sent, and
the agent's job is to reproduce it faithfully, so nothing in this repo may edit one
(Antonio, 2026-07-28). An earlier build recorded a resolve by deleting the gap entry
from the packet on disk, which was wrong twice over: it edited a committed fixture
whenever a reviewer confirmed a value locally (removing a declared gap and turning
four tests red, the same failure the 2026-07-23 changelog entry records), and on the
hosted service, where packets live in the deployed repo image, a resolve was
silently discarded on the very next deploy. A reviewer's judgment is reviewer state,
not source data, so it belongs in its own store.

This changes no deck output. Gap flags never print on a rendered deck — the render
strips them (``deck_renderer.strip_gap_flags``) after the render-fidelity guard has
confirmed they survived, before the file is written — so whether a gap is resolved
only ever affected the reviewer's checklist, never the artifact.

A decision is keyed by (packet, field). The packet key is its repo-relative path
when the packet lives inside the repo, so a decision survives the absolute paths
differing between a laptop and the hosted service; anything outside falls back to a
resolved absolute path.

Pure-ish by the module's own convention: the read/write helpers own one JSON file
and nothing else, the same shape as ``preference_store``.
"""

import json
import os
import sys
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

ALLOW_REPO_WRITE_ENV = "GAP_DECISIONS_ALLOW_REPO_WRITE"

# Repo-root default. Overridable so the hosted service can point it at its
# persistent volume (a service redeploys from a fresh copy of the code, so a
# store written under the repo would be discarded on the next push) and so tests
# stay hermetic.
DEFAULT_DECISIONS_PATH = os.path.join(_ROOT, "gap-decisions.json")

SCHEMA_VERSION = "1"

# The two decisions a reviewer can record on a flagged claim. A claim with no
# entry in the store is simply open: nobody has looked at it yet. Both decisions
# are recorded, because "I checked this" and "somebody needs to check this" are
# different states and a reviewer who sent a claim back has to still see that on
# the next page render (C4, 2026-08-09). Before that, sending one back wrote
# nothing at all and was indistinguishable from never having opened the deck.
CONFIRMED = "confirmed"
NEEDS_VERIFICATION = "verify"


def packet_key(packet_path):
    """A stable key for one packet, independent of where the repo is checked out.

    Repo-relative (with forward slashes) for a packet inside the repo, so the same
    packet resolves to the same key locally and on the hosted service; an absolute
    real path otherwise. An empty input returns ``""``, which no decision matches.
    """
    if not packet_path:
        return ""
    candidate = packet_path
    if not os.path.isabs(candidate):
        candidate = os.path.join(_ROOT, candidate)
    real = os.path.realpath(candidate)
    root = os.path.realpath(_ROOT)
    if real == root or real.startswith(root + os.sep):
        return os.path.relpath(real, root).replace(os.sep, "/")
    return real


def _empty_store():
    return {"schema_version": SCHEMA_VERSION, "decisions": []}


def load_decisions(path=None):
    """The decision store, or an empty one when the file is absent or unreadable.

    A corrupt or unreadable store degrades to empty rather than raising: losing the
    record of which claims were confirmed re-flags them for review, which is the
    safe direction. It must never take the studio down or block a render.
    """
    path = path or DEFAULT_DECISIONS_PATH
    if not os.path.isfile(path):
        return _empty_store()
    try:
        with open(path, encoding="utf-8") as f:
            store = json.load(f)
    except (OSError, ValueError):
        return _empty_store()
    if not isinstance(store, dict) or not isinstance(store.get("decisions"), list):
        return _empty_store()
    return store


class RepoDecisionsWriteBlocked(Exception):
    """A test tried to write the repo-root decision store."""


def _under_test():
    """True when this process is running tests. Mirrors ``preference_store``.

    Flask is read out of ``sys.modules`` rather than imported, because this module
    sits under the pipeline and must work with no Flask installed.
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    flask = sys.modules.get("flask")
    if flask is not None:
        try:
            return bool(flask.current_app.testing)
        except Exception:
            return False
    return False


def _guard_repo_write(path):
    """Refuse a test-time write to the repo-root store.

    The same trap ``preference_store`` already carries, and worth carrying here for
    the same reason: a test that forgets to redirect the store falls through to the
    real file and leaves a stray committed artifact behind — or worse, one test's
    decisions leak into another's assertions. Blocked loudly, with the fix named.
    """
    if not _under_test() or os.environ.get(ALLOW_REPO_WRITE_ENV):
        return
    if os.path.realpath(path) != os.path.realpath(DEFAULT_DECISIONS_PATH):
        return
    raise RepoDecisionsWriteBlocked(
        "Blocked a test-time write to the repo decision store at "
        f"{os.path.realpath(DEFAULT_DECISIONS_PATH)}. Point it at a temp file: "
        "patch ui.app.DECISIONS_PATH (the global the routes read) or pass an "
        f"explicit path=. If the write is intended, set {ALLOW_REPO_WRITE_ENV}=1."
    )


def save_decisions(store, path=None):
    path = path or DEFAULT_DECISIONS_PATH
    _guard_repo_write(path)
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2)
        f.write("\n")


def decisions_by_field(packet, path=None):
    """``{field: status}`` for every decision recorded against this packet.

    An entry written before the two statuses existed carries none, and counts as
    confirmed: recording a decision at all used to mean confirming it.
    """
    key = packet_key(packet)
    if not key:
        return {}
    return {
        d["field"]: d.get("status") or CONFIRMED
        for d in load_decisions(path).get("decisions", [])
        if isinstance(d, dict) and d.get("packet") == key and d.get("field")
    }


def resolved_fields(packet, path=None):
    """The set of gap field paths a reviewer has confirmed for this packet."""
    return {
        field for field, status in decisions_by_field(packet, path).items()
        if status == CONFIRMED
    }


def _record(packet, field, status, author, path):
    """Write one reviewer decision, replacing any earlier one on the same field.

    Returns True when the store changed, False when this exact decision was
    already recorded. Changing your mind is a change: a claim sent back to be
    verified and then confirmed rewrites its entry rather than being refused, so
    the store always holds the reviewer's latest word on each claim and never two
    entries disagreeing about one field. The packet on disk is not touched, read
    or written.
    """
    key = packet_key(packet)
    if not key or not field:
        raise ValueError("both a packet and a field are required")
    store = load_decisions(path)
    for decision in store["decisions"]:
        if decision.get("packet") != key or decision.get("field") != field:
            continue
        if (decision.get("status") or CONFIRMED) == status:
            return False
        decision["status"] = status
        decision["author"] = author or ""
        decision["decided_at"] = _now()
        save_decisions(store, path)
        return True
    store["decisions"].append({
        "packet": key,
        "field": field,
        "status": status,
        "author": author or "",
        "decided_at": _now(),
    })
    save_decisions(store, path)
    return True


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve(packet, field, *, author="", path=None):
    """Record that a reviewer confirmed ``field`` on ``packet``. Idempotent."""
    return _record(packet, field, CONFIRMED, author, path)


def send_back(packet, field, *, author="", path=None):
    """Record that ``field`` was sent back to be verified. Idempotent.

    The claim stays on the open checklist — it has not been settled, it has been
    routed — but the routing is now on the record, so it survives a page render,
    a new run, and a redeploy, and a later reader can tell a claim somebody sent
    back from one nobody has opened. Changes neither the packet nor the deck.
    """
    return _record(packet, field, NEEDS_VERIFICATION, author, path)


def reopen(packet, field, *, path=None):
    """Drop a recorded decision, putting the claim back to undecided.

    Returns True when something was removed. This is the reverse of both
    ``resolve`` and ``send_back``, which the packet-editing version could not
    offer: undoing a resolve then meant hand-editing the packet's gaps block back
    in.
    """
    key = packet_key(packet)
    if not key or not field:
        return False
    store = load_decisions(path)
    before = len(store["decisions"])
    store["decisions"] = [
        d for d in store["decisions"]
        if not (d.get("packet") == key and d.get("field") == field)
    ]
    if len(store["decisions"]) == before:
        return False
    save_decisions(store, path)
    return True
