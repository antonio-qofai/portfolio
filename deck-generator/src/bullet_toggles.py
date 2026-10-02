"""Which slide 2 bullets a reviewer switched on or off, stored OUTSIDE the packet.

What this is for. The fitter keeps as many of a panel's bullets as the panel
holds and drops the rest, and the studio has always shown which ones went and
which stayed. Casey asked for the next step (relayed by Antonio, 2026-09-20):
a switch beside each bullet, so including one or excluding it is one click
rather than an edit to the copy upstream. This module is where those switches
live.

THE PACKET IS NEVER WRITTEN. It is the data source's record of what it sent, and
nothing in this repo may edit one (Antonio, 2026-07-28). A reviewer's choice
about what a slide shows is reviewer state, exactly like a resolved flag, so it
goes in its own store next to ``gap_decisions`` and under the same rules: repo
root by default, redirected by environment variable onto the mounted volume on
the hosted service, and blocked from writing the repo file under test.

A BULLET IS KEYED BY ITS TEXT, NOT BY ITS POSITION. The ranking pass reorders
these lists between runs and the second extraction pass can lengthen them, so an
index means a different bullet on the next render while the text means the same
one. The key is a hash of the text rather than the text itself, to keep the
store small and readable; a bullet whose copy is edited upstream is a new bullet
here, which is correct, because the reviewer approved the sentence they saw.

WHAT A MISSING ENTRY MEANS. Nobody has touched that bullet, so the fitter
decides, exactly as it does today. The store holds decisions, not a mirror of
the deck: a panel nobody has switched anything on has no rows at all.
"""

import hashlib
import json
import os
import sys

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

ALLOW_REPO_WRITE_ENV = "BULLET_TOGGLES_ALLOW_REPO_WRITE"

# Repo-root default, overridable for the same two reasons as the decision store:
# the hosted service points it at its volume, and tests stay hermetic.
DEFAULT_TOGGLES_PATH = os.path.join(_ROOT, "bullet-toggles.json")

SCHEMA_VERSION = "1"

ON = "on"
OFF = "off"


def bullet_key(text):
    """A stable key for one bullet's text.

    Whitespace-normalised before hashing, so a line that arrives with a trailing
    space on one run and without it on the next is the same bullet to a reviewer
    and the same row here.
    """
    normalised = " ".join((text or "").split())
    if not normalised:
        return ""
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:16]


# WHAT A SWITCH IS REMEMBERED AGAINST, on a run with no packet file.
#
# The frozen-packet source left the studio on 2026-09-20 and a live run posts no
# packet path at all, so `packet_key` had nothing to key on and the switches were
# never offered (see `tests/test_bullet_switches_reach_a_prd_run.py`). Antonio
# chose the uploaded PRD itself, by content hash: re-upload an edited PRD and the
# switches reset to auto, because those are different bullets now.
#
# The prefix is what keeps the two kinds of key apart in one store. A row keyed
# `prd:...` came from a document and a row keyed `templates/packets/...` came
# from a packet file, and neither can be mistaken for the other or for a path.
SOURCE_PREFIX = "prd:"


def source_key(sha256):
    """A stable key for the base document one deck was written from.

    Truncated for the same reason `bullet_key` is: the store is read by humans
    deciding whether a row is stale, and 64 hex characters in every row makes it
    unreadable. 32 is far past the point where a collision is a real concern for
    a per-machine reviewer store.
    """
    if not sha256:
        return ""
    return SOURCE_PREFIX + sha256[:32]


def packet_key(packet_path):
    """A stable key for one packet, independent of where the repo is checked out.

    Identical rule to ``gap_decisions.packet_key`` and deliberately so: the two
    stores key their rows the same way, so a packet is one thing across both.

    A `source_key` passes straight through. It is already a key and is not a
    path, so joining it to the repo root and resolving it would be nonsense.
    """
    if not packet_path:
        return ""
    if packet_path.startswith(SOURCE_PREFIX):
        return packet_path
    candidate = packet_path
    if not os.path.isabs(candidate):
        candidate = os.path.join(_ROOT, candidate)
    real = os.path.realpath(candidate)
    root = os.path.realpath(_ROOT)
    if real == root or real.startswith(root + os.sep):
        return os.path.relpath(real, root).replace(os.sep, "/")
    return real


def _empty_store():
    return {"schema_version": SCHEMA_VERSION, "toggles": []}


def load_toggles(path=None):
    """The toggle store, or an empty one when absent or unreadable.

    A corrupt store degrades to empty rather than raising, which hands every
    panel back to the fitter. That is the safe direction: the deck renders what
    it would have rendered before anyone touched a switch, rather than the
    studio refusing to open.
    """
    path = path or DEFAULT_TOGGLES_PATH
    if not os.path.isfile(path):
        return _empty_store()
    try:
        with open(path, encoding="utf-8") as fh:
            store = json.load(fh)
    except (OSError, ValueError):
        return _empty_store()
    if not isinstance(store, dict) or not isinstance(store.get("toggles"), list):
        return _empty_store()
    return store


class RepoTogglesWriteBlocked(Exception):
    """A test tried to write the repo-root toggle store."""


def _under_test():
    """True when this process is running tests. Mirrors ``gap_decisions``."""
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
    """Refuse a test-time write to the repo-root store, naming the fix."""
    if not _under_test() or os.environ.get(ALLOW_REPO_WRITE_ENV):
        return
    if os.path.realpath(path) != os.path.realpath(DEFAULT_TOGGLES_PATH):
        return
    raise RepoTogglesWriteBlocked(
        "Blocked a test-time write to the repo bullet-toggle store at "
        f"{os.path.realpath(DEFAULT_TOGGLES_PATH)}. Point it at a temp file: "
        "patch ui.app.TOGGLES_PATH (the global the routes read) or pass an "
        "explicit path."
    )


def _save(store, path):
    _guard_repo_write(path)
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(store, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def set_toggle(packet, opportunity, role, text, state, *, author="", path=None):
    """Record that ``text`` is switched ``state`` on one panel of one slide 2.

    ``opportunity`` is the zero-based index of the opportunity whose slide 2 this
    panel belongs to, because a deck carries one per opportunity and the same
    bullet text could appear on two of them. ``state`` is ``ON`` or ``OFF``.
    Setting a bullet twice replaces the row rather than stacking two.
    """
    if state not in (ON, OFF):
        raise ValueError(f"state must be {ON!r} or {OFF!r}, got {state!r}")
    path = path or DEFAULT_TOGGLES_PATH
    key = bullet_key(text)
    if not key:
        raise ValueError("a bullet with no text cannot be toggled")
    store = load_toggles(path)
    row = {
        "packet": packet_key(packet),
        "opportunity": int(opportunity),
        "role": role,
        "bullet": key,
        "state": state,
        "author": author or "",
        # The text is kept beside the hash so the store is readable by a human
        # deciding whether a row is stale. Nothing reads it; the hash is the key.
        "text": " ".join((text or "").split()),
    }
    store["toggles"] = [
        existing for existing in store["toggles"]
        if not (existing.get("packet") == row["packet"]
                and existing.get("opportunity") == row["opportunity"]
                and existing.get("role") == row["role"]
                and existing.get("bullet") == row["bullet"])
    ]
    store["toggles"].append(row)
    _save(store, path)
    return row


def clear_toggle(packet, opportunity, role, text, *, path=None):
    """Forget a bullet's switch, handing it back to the fitter.

    Different from switching it off: off is a reviewer saying the slide is better
    without this line, and cleared is nobody having said anything. The second is
    what a reviewer wants when they change their mind rather than disagree.
    """
    path = path or DEFAULT_TOGGLES_PATH
    key = bullet_key(text)
    store = load_toggles(path)
    before = len(store["toggles"])
    store["toggles"] = [
        existing for existing in store["toggles"]
        if not (existing.get("packet") == packet_key(packet)
                and existing.get("opportunity") == int(opportunity)
                and existing.get("role") == role
                and existing.get("bullet") == key)
    ]
    if len(store["toggles"]) != before:
        _save(store, path)
    return before - len(store["toggles"])


def overrides_for(packet, *, path=None):
    """Every switch recorded for one packet, shaped for the fitter.

    Returns ``{opportunity_index: {role: {bullet_key: state}}}``. Empty when the
    packet has no rows, which is the normal case and means the fitter decides
    everything, exactly as it did before this module existed.
    """
    key = packet_key(packet)
    if not key:
        return {}
    out = {}
    for row in load_toggles(path).get("toggles", []):
        if row.get("packet") != key:
            continue
        if row.get("state") not in (ON, OFF):
            continue
        try:
            opportunity = int(row.get("opportunity"))
        except (TypeError, ValueError):
            continue
        role = row.get("role") or ""
        bullet = row.get("bullet") or ""
        if not role or not bullet:
            continue
        out.setdefault(opportunity, {}).setdefault(role, {})[bullet] = row["state"]
    return out
