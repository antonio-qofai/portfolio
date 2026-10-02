"""Tests for the standing-preferences store (`src/preference_store.py`), the
learning half of the reviewer feedback loop.

Covers: an absent store reads as empty; a note round-trips through add/load;
ids increment and bad input raises; scope filtering applies global-plus-deck-type
and honors the active flag; and the rendered block is empty when nothing applies
and byte-shaped as bullets when it does.

Also covers the two deployment safeguards: the guard that blocks a test-time
write to the committed repo store (a mispatched test once wiped it silently), and
the idempotent boot seed that carries the committed store onto a fresh hosted
volume without ever overwriting one already there.

Run with: python3 tests/test_preference_store.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from preference_store import (
    ALLOW_REPO_WRITE_ENV,
    DEFAULT_STORE_PATH,
    PREFERENCES_BLOCK_HEADING,
    RepoStoreWriteBlocked,
    add_preference,
    applicable_preferences,
    delete_preference,
    load_store,
    preferences_block_for,
    render_preferences_block,
    save_store,
    seed_store_if_absent,
    set_active,
)


def _store_path(tmp):
    return os.path.join(tmp, "standing-preferences.json")


def test_absent_store_reads_as_empty():
    with tempfile.TemporaryDirectory() as tmp:
        store = load_store(_store_path(tmp))
        assert store["preferences"] == [], store
        assert applicable_preferences("proposal", path=_store_path(tmp)) == []
        assert preferences_block_for("status", path=_store_path(tmp)) == ""


def test_add_preference_round_trips_and_sets_fields():
    with tempfile.TemporaryDirectory() as tmp:
        path = _store_path(tmp)
        entry = add_preference(
            "  tighten the tracker whitespace  ",
            scope="status", author="Casey", path=path, now="2026-07-20T00:00:00+00:00",
        )
        assert entry["id"] == 1
        assert entry["note"] == "tighten the tracker whitespace", "note is trimmed"
        assert entry["scope"] == "status"
        assert entry["author"] == "Casey"
        assert entry["active"] is True
        assert entry["created"] == "2026-07-20T00:00:00+00:00"

        reloaded = load_store(path)
        assert reloaded["preferences"] == [entry], reloaded


def test_ids_increment_across_adds():
    with tempfile.TemporaryDirectory() as tmp:
        path = _store_path(tmp)
        a = add_preference("first", path=path)
        b = add_preference("second", path=path)
        assert (a["id"], b["id"]) == (1, 2)


def test_empty_note_and_bad_scope_raise():
    with tempfile.TemporaryDirectory() as tmp:
        path = _store_path(tmp)
        for bad in ("", "   ", None):
            try:
                add_preference(bad, path=path)
                assert False, "empty note should raise"
            except ValueError:
                pass
        try:
            add_preference("ok", scope="client:Ridgeline", path=path)
            assert False, "unknown scope should raise"
        except ValueError:
            pass


def test_scope_filtering_global_and_deck_type():
    with tempfile.TemporaryDirectory() as tmp:
        path = _store_path(tmp)
        add_preference("everywhere", scope="global", path=path)
        add_preference("proposal only", scope="proposal", path=path)
        add_preference("status only", scope="status", path=path)

        proposal_notes = [p["note"] for p in applicable_preferences("proposal", path=path)]
        status_notes = [p["note"] for p in applicable_preferences("status", path=path)]
        assert proposal_notes == ["everywhere", "proposal only"], proposal_notes
        assert status_notes == ["everywhere", "status only"], status_notes


def test_deactivated_preference_is_not_applied_but_is_retained():
    with tempfile.TemporaryDirectory() as tmp:
        path = _store_path(tmp)
        entry = add_preference("mute me", scope="global", path=path)
        add_preference("keep me", scope="global", path=path)

        updated = set_active(entry["id"], False, path=path)
        assert updated["active"] is False
        notes = [p["note"] for p in applicable_preferences("proposal", path=path)]
        assert notes == ["keep me"], notes
        # Retained in the store, just inactive.
        assert len(load_store(path)["preferences"]) == 2
        # Re-activating brings it back.
        set_active(entry["id"], True, path=path)
        notes = [p["note"] for p in applicable_preferences("proposal", path=path)]
        assert notes == ["mute me", "keep me"], notes


def test_set_active_unknown_id_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        path = _store_path(tmp)
        assert set_active(999, False, path=path) is None


def test_delete_preference_removes_the_row_entirely():
    with tempfile.TemporaryDirectory() as tmp:
        path = _store_path(tmp)
        first = add_preference("drop me", scope="global", path=path)
        add_preference("keep me", scope="global", path=path)

        removed = delete_preference(first["id"], path=path)
        assert removed["note"] == "drop me"
        remaining = load_store(path)["preferences"]
        assert [p["note"] for p in remaining] == ["keep me"]
        # Unlike deactivate, the row is gone, not retained.
        assert all(p["id"] != first["id"] for p in remaining)


def test_delete_preference_unknown_id_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        path = _store_path(tmp)
        add_preference("only one", scope="global", path=path)
        assert delete_preference(999, path=path) is None
        assert len(load_store(path)["preferences"]) == 1


def test_render_block_empty_when_no_preferences():
    assert render_preferences_block([]) == ""


def test_render_block_lists_notes_under_heading():
    prefs = [{"note": "alpha"}, {"note": "beta"}]
    block = render_preferences_block(prefs)
    assert block.startswith(PREFERENCES_BLOCK_HEADING), block
    assert "- alpha" in block
    assert "- beta" in block
    # Only note text is emitted, no bookkeeping.
    assert "id" not in block.lower().split("\n")[1]


def test_test_time_write_to_the_committed_store_is_blocked():
    """The guard that turns a silent overwrite into a loud failure.

    A test that redirects the store by patching the wrong attribute used to fall
    through to the committed repo file and wipe a reviewer's captured
    preferences. Writes to the real store must raise while tests are running.
    """
    before = None
    if os.path.exists(DEFAULT_STORE_PATH):
        with open(DEFAULT_STORE_PATH, encoding="utf-8") as f:
            before = f.read()

    # `set_active` and `delete_preference` only reach a write when the id is
    # actually in the store, so probing them with a hardcoded id tests the
    # guard only while the committed store happens to hold that id. It held
    # exactly one preference until 2026-09-21, when Antonio had it deleted, and
    # the probe then passed through without writing and without raising —
    # reporting a guard failure that was really an empty store. Probe with a
    # LIVE id when there is one, and assert the no-op separately when there is
    # not.
    live_ids = [p["id"] for p in load_store(DEFAULT_STORE_PATH)["preferences"]]
    probe_id = live_ids[0] if live_ids else None

    checks = [
        ("save_store", lambda: save_store(load_store(DEFAULT_STORE_PATH),
                                          DEFAULT_STORE_PATH)),
        ("add_preference", lambda: add_preference("guard probe",
                                                 path=DEFAULT_STORE_PATH)),
    ]
    if probe_id is not None:
        checks += [
            ("set_active",
             lambda: set_active(probe_id, False, path=DEFAULT_STORE_PATH)),
            ("delete_preference",
             lambda: delete_preference(probe_id, path=DEFAULT_STORE_PATH)),
        ]

    for label, call in checks:
        try:
            call()
        except RepoStoreWriteBlocked:
            pass
        else:
            raise AssertionError(f"{label} was allowed to write the committed store")

    if probe_id is None:
        # An id that is not in the store must be a silent no-op rather than a
        # write, on the committed store as on any other. Nothing to block,
        # nothing to raise, and nothing on disk changes — which the byte
        # comparison at the end of this test proves.
        assert set_active(1, False, path=DEFAULT_STORE_PATH) is None
        assert delete_preference(1, path=DEFAULT_STORE_PATH) is None

    # A relative path pointing at the same file is blocked too, not just the
    # exact default string.
    sneaky = os.path.join(os.path.dirname(DEFAULT_STORE_PATH), ".",
                          os.path.basename(DEFAULT_STORE_PATH))
    try:
        add_preference("guard probe", path=sneaky)
    except RepoStoreWriteBlocked:
        pass
    else:
        raise AssertionError("an equivalent path to the committed store was allowed")

    after = None
    if os.path.exists(DEFAULT_STORE_PATH):
        with open(DEFAULT_STORE_PATH, encoding="utf-8") as f:
            after = f.read()
    assert after == before, "the committed store was modified by a blocked write"


def test_guard_leaves_temp_stores_alone():
    """The guard must not get in the way of a normal hermetic test."""
    with tempfile.TemporaryDirectory() as tmp:
        path = _store_path(tmp)
        add_preference("a normal temp-store write", path=path)
        assert len(load_store(path)["preferences"]) == 1


def test_guard_has_an_explicit_escape_hatch():
    """An intended write to the committed store is still possible, loudly."""
    with tempfile.TemporaryDirectory() as tmp:
        # Point DEFAULT_STORE_PATH at a temp file so the escape hatch is exercised
        # without touching the real committed store.
        import preference_store as ps

        original = ps.DEFAULT_STORE_PATH
        ps.DEFAULT_STORE_PATH = _store_path(tmp)
        try:
            try:
                ps.add_preference("blocked", path=ps.DEFAULT_STORE_PATH)
            except ps.RepoStoreWriteBlocked:
                pass
            else:
                raise AssertionError("guard did not fire on the stand-in default")

            os.environ[ps.ALLOW_REPO_WRITE_ENV] = "1"
            try:
                ps.add_preference("allowed", path=ps.DEFAULT_STORE_PATH)
                prefs = ps.load_store(ps.DEFAULT_STORE_PATH)["preferences"]
                assert [p["note"] for p in prefs] == ["allowed"], prefs
            finally:
                del os.environ[ps.ALLOW_REPO_WRITE_ENV]
        finally:
            ps.DEFAULT_STORE_PATH = original


def test_seed_copies_the_committed_store_to_a_fresh_path():
    """Hosted boot: a volume that has no store yet gets the committed one."""
    with tempfile.TemporaryDirectory() as tmp:
        source = _store_path(tmp)
        add_preference("carried to the volume", path=source)
        dest = os.path.join(tmp, "volume", "standing-preferences.json")

        assert seed_store_if_absent(dest, source=source) is True
        notes = [p["note"] for p in load_store(dest)["preferences"]]
        assert notes == ["carried to the volume"], notes


def test_seed_is_idempotent_and_never_overwrites():
    """Every later boot must leave the team's edited store untouched."""
    with tempfile.TemporaryDirectory() as tmp:
        source = _store_path(tmp)
        add_preference("committed copy", path=source)
        dest = os.path.join(tmp, "volume", "standing-preferences.json")

        assert seed_store_if_absent(dest, source=source) is True
        # The team then edits the hosted store.
        add_preference("captured on the hosted studio", path=dest)
        # Two more boots.
        assert seed_store_if_absent(dest, source=source) is False
        assert seed_store_if_absent(dest, source=source) is False

        notes = [p["note"] for p in load_store(dest)["preferences"]]
        assert "captured on the hosted studio" in notes, notes
        assert len(notes) == 2, notes


def test_seed_is_a_noop_when_source_and_destination_are_one_file():
    """Local dev: no environment variables set, so both paths are the repo file."""
    with tempfile.TemporaryDirectory() as tmp:
        source = _store_path(tmp)
        add_preference("local", path=source)
        assert seed_store_if_absent(source, source=source) is False
        # Also true for an equivalent-but-different path string.
        equivalent = os.path.join(os.path.dirname(source), ".",
                                  os.path.basename(source))
        assert seed_store_if_absent(equivalent, source=source) is False
        assert len(load_store(source)["preferences"]) == 1


def test_seed_skips_a_missing_source():
    with tempfile.TemporaryDirectory() as tmp:
        missing = os.path.join(tmp, "nope.json")
        dest = os.path.join(tmp, "volume", "standing-preferences.json")
        assert seed_store_if_absent(dest, source=missing) is False
        assert not os.path.exists(dest)


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
