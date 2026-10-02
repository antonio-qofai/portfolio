"""Tests for the deck store directory resolver.

Covers the candidate order, the skip of a candidate that names a path nothing
was ever mounted at, the durability flag, and a real SQLite file opened in the
directory that comes back — since a directory that resolves but cannot hold a
database would be the same silent failure by another route.

Run with: python3 tests/test_store_dir_resolver.py
"""

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import store_dir_resolver
from store_dir_resolver import (
    MOUNTED_VOLUME_PATH,
    PLATFORM_VAR,
    STORE_DIR_VARS,
    resolve_store_dir,
)

_ALL_CANDIDATE_VARS = tuple(STORE_DIR_VARS) + (PLATFORM_VAR,)


def _clear_candidate_vars():
    """Pops every candidate var; returns a restore function."""
    saved = {name: os.environ.pop(name, None) for name in _ALL_CANDIDATE_VARS}

    def restore():
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    return restore


def test_finds_the_first_candidate_present():
    restore = _clear_candidate_vars()
    tmp = tempfile.mkdtemp(prefix="store-dir-")
    try:
        os.environ["DECK_STORE_DIR"] = tmp
        result = resolve_store_dir()
        assert result.directory == tmp
        assert result.source == "DECK_STORE_DIR"
        assert result.durable, "an explicit override is trusted as durable"
        assert result.store_path == os.path.join(tmp, "decks.sqlite3")
    finally:
        os.rmdir(tmp)
        restore()


def test_falls_back_through_candidates_in_order():
    restore = _clear_candidate_vars()
    tmp = tempfile.mkdtemp(prefix="store-dir-")
    try:
        os.environ["RAILWAY_VOLUME_MOUNT_PATH"] = tmp
        result = resolve_store_dir()
        assert result.source == "RAILWAY_VOLUME_MOUNT_PATH"
        assert result.directory == tmp
    finally:
        os.rmdir(tmp)
        restore()


def test_a_candidate_naming_an_unmounted_path_is_skipped_rather_than_trusted():
    """The whole point of the exists-and-writable check: a variable pointing at
    a directory nothing was mounted at must not win."""
    restore = _clear_candidate_vars()
    tmp = tempfile.mkdtemp(prefix="store-dir-")
    try:
        os.environ["DECK_STORE_DIR"] = os.path.join(tmp, "never-mounted")
        os.environ["RAILWAY_VOLUME_MOUNT_PATH"] = tmp
        result = resolve_store_dir()
        assert result.source == "RAILWAY_VOLUME_MOUNT_PATH", result.source

        # A directory that exists but cannot be written to is skipped too.
        readonly = os.path.join(tmp, "readonly")
        os.mkdir(readonly, 0o500)
        os.environ["DECK_STORE_DIR"] = readonly
        assert resolve_store_dir().source == "RAILWAY_VOLUME_MOUNT_PATH"
        os.chmod(readonly, 0o700)
        os.rmdir(readonly)
    finally:
        os.rmdir(tmp)
        restore()


def test_the_container_fallback_is_durable_locally_and_not_on_the_platform():
    """There is always a location now, so the result reports durability rather
    than whether one was found — and it names every candidate it checked."""
    restore = _clear_candidate_vars()
    tmp = tempfile.mkdtemp(prefix="store-dir-")
    saved_local = store_dir_resolver.REPO_LOCAL_DIR
    store_dir_resolver.REPO_LOCAL_DIR = os.path.join(tmp, "deck-store")
    try:
        local = resolve_store_dir()
        assert local.source == store_dir_resolver.REPO_LOCAL_DIR
        assert local.durable, "a local run must not raise a durability notice"
        assert os.path.isdir(local.directory), "the fallback directory is created"
        for name in STORE_DIR_VARS:
            assert name in local.checked, local.checked
        assert MOUNTED_VOLUME_PATH in local.checked

        os.environ[PLATFORM_VAR] = "production"
        hosted = resolve_store_dir()
        assert hosted.directory == local.directory
        assert not hosted.durable, "a container path on the platform is not durable"
    finally:
        store_dir_resolver.REPO_LOCAL_DIR = saved_local
        restore()


def test_the_resolved_directory_really_holds_a_sqlite_file():
    """Done-when check: resolution reports a location a database can be opened
    in, not just a string that looks like a path."""
    restore = _clear_candidate_vars()
    tmp = tempfile.mkdtemp(prefix="store-dir-")
    try:
        os.environ["DECK_STORE_DIR"] = tmp
        path = resolve_store_dir().store_path
        conn = sqlite3.connect(path)
        try:
            conn.execute("CREATE TABLE probe (x INTEGER)")
            conn.execute("INSERT INTO probe VALUES (1)")
            assert conn.execute("SELECT x FROM probe").fetchone()[0] == 1
        finally:
            conn.close()
        assert os.path.isfile(path), "the store file was not created on disk"
        os.remove(path)
    finally:
        os.rmdir(tmp)
        restore()


if __name__ == "__main__":
    test_finds_the_first_candidate_present()
    test_falls_back_through_candidates_in_order()
    test_a_candidate_naming_an_unmounted_path_is_skipped_rather_than_trusted()
    test_the_container_fallback_is_durable_locally_and_not_on_the_platform()
    test_the_resolved_directory_really_holds_a_sqlite_file()
    print("All tests passed.")
