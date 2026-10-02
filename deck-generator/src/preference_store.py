"""Standing-preferences store — the reviewer feedback loop's memory.

This is the second half of Pat's evals ask (2026-07-16 check-in): the part
that makes reviewer feedback *stick*. A human reviewing a rendered deck can flag
a standing formatting preference (the canonical example: "Casey flags
whitespace"), and instead of that note living only in a chat thread it is
persisted here and read back on every future run, so later decks apply it
automatically. Capturing a note and learning from it are two different things —
this module is the learning half.

Scope of what a preference is: a FORMAT-ONLY refinement layered on top of the
pinned house style. A preference never adds, drops, or alters a field's value —
that is the deterministic content pipeline's job, and it stays untouched. The
preference text is appended to the house style the render legs already carry
(`deck_renderer`), so the coverage guard and the render-fidelity guard, which
operate on content, are unaffected. This is what lets the feedback loop coexist
with the "never fabricate" guarantee.

Two scopes ship today (per the agreed build): ``global`` (applies to every deck)
and a deck-type scope naming ``proposal`` or ``status`` directly. A note is read
on a run when its scope is ``global`` or equals that run's ``deck_type``.
Per-client scope is a deliberate later extension: the schema already carries a
free-form ``scope`` string, so adding ``client:<name>`` is a filter change here,
not a redesign.

The store is a repo-local JSON file (``standing-preferences.json`` at the repo
root by default, overridable per call), so it is testable, travels with the
agent, and stays reusable across clients rather than hiding in a machine's
personal memory. Nothing here is hardcoded to one client or project.
"""

import json
import os
import sys
from datetime import datetime, timezone

_HERE = os.path.dirname(__file__)

# Repo-root store by default; every entry point takes an explicit ``path`` so
# tests stay hermetic and a caller can point at a different store.
DEFAULT_STORE_PATH = os.path.join(_HERE, "..", "standing-preferences.json")

# Escape hatch for the test guard below. Set only if a test genuinely means to
# write the committed store; the point of the guard is that no test does this by
# accident, not that it can never be done.
ALLOW_REPO_WRITE_ENV = "PREF_STORE_ALLOW_REPO_WRITE"

STORE_SCHEMA_VERSION = "1"

# The scopes a preference may carry. GLOBAL applies to every deck; a deck_type
# name applies only to that path. Kept as data so a third scope is one entry.
SCOPE_GLOBAL = "global"
DECK_TYPE_SCOPES = ("proposal", "status")
VALID_SCOPES = (SCOPE_GLOBAL,) + DECK_TYPE_SCOPES

# The heading the applicable notes are rendered under when injected into a render
# leg. Phrased so the model treats the notes as format-only refinements that
# never touch content — the same contract the house style itself carries.
PREFERENCES_BLOCK_HEADING = (
    "STANDING REVIEWER PREFERENCES (format-only refinements captured from prior "
    "human review, applied on top of the pinned house style). These refine "
    "layout and formatting ONLY. They must never add, drop, reword, or alter any "
    "field's value, and never override a content rule above — content always "
    "wins. Apply each on every applicable slide:"
)


def _empty_store():
    return {"schema_version": STORE_SCHEMA_VERSION, "preferences": []}


def load_store(path=DEFAULT_STORE_PATH):
    """Return the store dict, or an empty store if the file is absent.

    An absent file is the normal starting state (no preferences captured yet),
    not an error — it returns ``{"schema_version": ..., "preferences": []}`` so
    every reader can treat the store uniformly.
    """
    if not os.path.isfile(path):
        return _empty_store()
    with open(path, "r", encoding="utf-8") as f:
        store = json.load(f)
    # Be forgiving about a hand-edited or older file: fill in the shape rather
    # than crash a render run over a missing key.
    store.setdefault("schema_version", STORE_SCHEMA_VERSION)
    store.setdefault("preferences", [])
    return store


class RepoStoreWriteBlocked(RuntimeError):
    """A test tried to write the committed repo store instead of a temp copy."""


def _under_test():
    """True when this process is running tests.

    Three signals, because the accident this guards against can arrive by any of
    the routes a test runs on here: pytest sets PYTEST_CURRENT_TEST for the
    duration of each test, a Flask app driven by ``test_client()`` carries
    ``app.testing``, and every test file in this repo also documents a direct
    ``python3 tests/test_*.py`` invocation, which sets neither. Without that
    third signal the guard is inert under exactly the run style the suite tells
    a stranger to use, so a mispatched test would reach the committed store with
    nothing to stop it. Flask is read out of ``sys.modules`` rather than
    imported, because this module is part of the pipeline and must keep working
    with no Flask installed.
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    if os.path.basename(sys.argv[0]).startswith("test_"):
        return True
    flask = sys.modules.get("flask")
    if flask is not None:
        try:
            return bool(flask.current_app.testing)
        except Exception:
            return False  # no app context, or an old/odd Flask: not a test signal
    return False


def _guard_repo_store_write(path):
    """Refuse a test-time write to the committed store.

    A test that redirects the store by patching the wrong attribute silently
    falls through to the real repo file and overwrites a reviewer's captured
    preferences — the store is a small JSON file, so the damage is total and
    quiet. Turning that into a loud failure is the whole point: the write is
    blocked and the message names the fix.
    """
    if not _under_test() or os.environ.get(ALLOW_REPO_WRITE_ENV):
        return
    if os.path.realpath(path) != os.path.realpath(DEFAULT_STORE_PATH):
        return
    raise RepoStoreWriteBlocked(
        "Blocked a test-time write to the committed preference store at "
        f"{os.path.realpath(path)}. A test must point the store at a temp file: "
        "patch ui.app.STORE_PATH (the module global the routes read) or pass an "
        "explicit path= to this call. If the write is genuinely intended, set "
        f"{ALLOW_REPO_WRITE_ENV}=1 for that test."
    )


def seed_store_if_absent(path, *, source=DEFAULT_STORE_PATH):
    """Copy ``source`` to ``path`` when no store exists there yet.

    A hosted deployment points the store at a persistent volume that starts
    empty, so the preferences the team already captured would not apply to the
    first hosted render. This seeds the volume from the committed store once.

    Idempotent by construction, and safe to call on every boot: it returns
    without writing when the two paths are the same file (the local default),
    when a store already exists at ``path`` (so a team's captured preferences
    are never overwritten by the committed copy), or when ``source`` is missing.
    Returns True only when it actually wrote a store.

    Nothing here is specific to one platform: the caller supplies both paths.
    """
    if not path:
        return False
    if os.path.realpath(path) == os.path.realpath(source):
        return False  # local default: source and destination are one file
    if os.path.exists(path):
        return False  # a store is already there: leave it alone
    if not os.path.exists(source):
        return False
    # Round-trip through the loader rather than copying bytes, so a corrupt
    # committed file surfaces here instead of poisoning the volume's store.
    save_store(load_store(source), path)
    return True


def save_store(store, path=DEFAULT_STORE_PATH):
    """Write the store to ``path`` as pretty JSON (created if new).

    Every mutating entry point in this module funnels through here, so the
    test-time guard on the committed store lives here too and covers all of
    them rather than only the UI routes.
    """
    _guard_repo_store_write(path)
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _next_id(preferences):
    """One past the highest existing integer id (1 when empty)."""
    highest = 0
    for pref in preferences:
        try:
            highest = max(highest, int(pref.get("id", 0)))
        except (TypeError, ValueError):
            continue
    return highest + 1


def _now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def add_preference(note, *, scope=SCOPE_GLOBAL, author="", path=DEFAULT_STORE_PATH, now=None):
    """Append one reviewer note to the store and return the created entry.

    ``note`` is the reviewer's standing preference (e.g. "tighten vertical
    whitespace between the tracker rows"). ``scope`` must be one of
    ``VALID_SCOPES``. ``author`` is a free-form attribution (a reviewer name),
    optional. ``now`` overrides the timestamp for deterministic tests.

    Raises ``ValueError`` on an empty note or an unrecognized scope — a caller
    mistake, surfaced rather than silently stored.
    """
    note = (note or "").strip()
    if not note:
        raise ValueError("preference note must be non-empty")
    if scope not in VALID_SCOPES:
        raise ValueError(f"unknown scope: {scope!r} (expected one of {VALID_SCOPES})")

    store = load_store(path)
    entry = {
        "id": _next_id(store["preferences"]),
        "created": now or _now_iso(),
        "author": (author or "").strip(),
        "scope": scope,
        "note": note,
        "active": True,
    }
    store["preferences"].append(entry)
    save_store(store, path)
    return entry


def set_active(pref_id, active, *, path=DEFAULT_STORE_PATH):
    """Toggle a preference's ``active`` flag by id and return the updated entry.

    A deactivated preference stays in the store (so the history is not lost) but
    stops being applied to future runs. Returns ``None`` if no entry has that id.
    """
    store = load_store(path)
    for pref in store["preferences"]:
        if pref.get("id") == pref_id:
            pref["active"] = bool(active)
            save_store(store, path)
            return pref
    return None


def delete_preference(pref_id, *, path=DEFAULT_STORE_PATH):
    """Delete a preference by id and return the removed entry (``None`` if absent).

    Unlike :func:`set_active` (which deactivates but keeps the row for history),
    this removes the entry entirely — the reviewer's "delete this preference"
    action. A stale id is a safe no-op returning ``None``.
    """
    store = load_store(path)
    prefs = store["preferences"]
    for i, pref in enumerate(prefs):
        if pref.get("id") == pref_id:
            removed = prefs.pop(i)
            save_store(store, path)
            return removed
    return None


def applicable_preferences(deck_type, *, path=DEFAULT_STORE_PATH, store=None):
    """Active preferences that apply to a run of ``deck_type``, in capture order.

    A preference applies when it is active and its scope is ``global`` or equals
    ``deck_type``. Pass an already-loaded ``store`` to avoid re-reading the file.
    Returns a list of entry dicts (empty when nothing applies).
    """
    if store is None:
        store = load_store(path)
    out = []
    for pref in store.get("preferences", []):
        if not pref.get("active", True):
            continue
        scope = pref.get("scope", SCOPE_GLOBAL)
        if scope == SCOPE_GLOBAL or scope == deck_type:
            out.append(pref)
    return out


def render_preferences_block(preferences):
    """Render applicable preferences into an injectable text block.

    Returns ``""`` when the list is empty, so a caller can prepend/append it
    unconditionally and get byte-identical output to the no-preferences path.
    Otherwise returns the heading followed by one ``- `` bullet per note. Only
    the note text is emitted (not ids, authors, or timestamps): the render leg
    needs the instruction, not the bookkeeping.
    """
    if not preferences:
        return ""
    lines = [PREFERENCES_BLOCK_HEADING]
    lines.extend(f"- {pref['note']}" for pref in preferences)
    return "\n".join(lines)


def preferences_block_for(deck_type, *, path=DEFAULT_STORE_PATH, store=None):
    """Convenience: the injectable block for a ``deck_type`` in one call.

    Equivalent to ``render_preferences_block(applicable_preferences(...))``.
    Returns ``""`` when no preference applies.
    """
    return render_preferences_block(
        applicable_preferences(deck_type, path=path, store=store)
    )
