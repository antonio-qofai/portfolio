"""Points every test's deck store at a directory of its own.

The store now resolves a directory rather than a connection string, and the
last candidate is a repo-local development directory that always exists — so
there is no "no store" state to fall into. Left alone, every test in the suite
would read and write the developer's own deck history, which is how prompt B2c
found a batch of UI tests silently writing rows into a real local Postgres.

Setting ``DECK_STORE_DIR`` to a per-test temporary directory closes that rather
than only defusing it: each test gets an empty store, writes into it freely, and
it goes away with the tmp_path. ``RAILWAY_*`` is cleared too, so a test never
inherits a hosted-platform environment it did not ask for.

A test that wants a different location (the durability notice needs resolution
to fall through) overrides these itself.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from store_dir_resolver import PLATFORM_VAR, STORE_DIR_VARS


@pytest.fixture(autouse=True)
def _isolated_deck_store(tmp_path, monkeypatch):
    store_dir = tmp_path / "deck-store"
    store_dir.mkdir()
    for name in STORE_DIR_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(PLATFORM_VAR, raising=False)
    monkeypatch.setenv("DECK_STORE_DIR", str(store_dir))
    yield
