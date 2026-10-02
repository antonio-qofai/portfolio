"""Finds the directory the deck store's SQLite file lives in, without knowing
what the hosted service calls it.

The hosted service has a persistent disk volume, not a database (see "Two
findings that shaped the order" in PROMPT-QUEUE.md, revised 2026-08-07). Nobody
has committed to what the mount path variable is called, and hardcoding a guess
would break the day it is renamed, so this module tries an ordered list of
candidates and reports which one it used.

A candidate only counts if the directory exists and is writable. A variable
naming a path that was never mounted is the exact silent failure this pattern
exists to catch, so an unmounted or read-only candidate is skipped rather than
trusted.

There is always a location, because the repo-local development directory is the
last candidate and this module creates it. So the result reports whether the
location is *durable* rather than whether one was found: false only when the app
is running on the hosting platform and resolution fell through to a container
path, which is wiped on every deploy. Every local run is durable, because a
notice that fires on every local run is a notice nobody reads.
"""

import os

# Order matters. DECK_STORE_DIR is an explicit override that costs nothing and
# settles any argument; RAILWAY_VOLUME_MOUNT_PATH is what the platform injects
# on its own; MOUNTED_VOLUME_PATH is where the volume was actually mounted.
STORE_DIR_VARS = ("DECK_STORE_DIR", "RAILWAY_VOLUME_MOUNT_PATH")
MOUNTED_VOLUME_PATH = "/data"

# Last resort, and the only candidate this module will create. Inside the repo,
# so a local run needs no setup; wiped on every deploy, so it is not durable on
# the platform. Gitignored — a development database never gets committed.
REPO_LOCAL_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deck-store"
)

# One SQLite file per directory. Named rather than passed, because two callers
# picking different filenames would each see an empty history.
STORE_FILENAME = "decks.sqlite3"

# The platform always sets this and a local run never does, so it is what
# separates "fell through to a container path" from "ran on a laptop". Read at
# call time, not import time, so the durability branch stays testable.
PLATFORM_VAR = "RAILWAY_ENVIRONMENT"


class StoreDirResolution:
    """A resolved store directory, the candidate it came from, every candidate
    that was checked, and whether the location survives a redeploy."""

    def __init__(self, directory, source, checked, durable):
        self.directory = directory
        self.source = source
        self.checked = list(checked)
        self.durable = durable

    @property
    def store_path(self):
        """The SQLite file itself, which is what the store opens."""
        return os.path.join(self.directory, STORE_FILENAME)

    def __repr__(self):
        return "StoreDirResolution({} via {}; durable={})".format(
            self.directory, self.source, self.durable
        )


def _usable(path):
    """A candidate counts only if it is a directory that can be written to."""
    return bool(path) and os.path.isdir(path) and os.access(path, os.W_OK)


def resolve_store_dir():
    """Tries each candidate in order, returns a StoreDirResolution."""
    checked = []
    for name in STORE_DIR_VARS:
        checked.append(name)
        value = os.environ.get(name)
        if _usable(value):
            return StoreDirResolution(value, name, checked, True)

    checked.append(MOUNTED_VOLUME_PATH)
    if _usable(MOUNTED_VOLUME_PATH):
        return StoreDirResolution(
            MOUNTED_VOLUME_PATH, MOUNTED_VOLUME_PATH, checked, True
        )

    checked.append(REPO_LOCAL_DIR)
    os.makedirs(REPO_LOCAL_DIR, exist_ok=True)
    on_platform = bool(os.environ.get(PLATFORM_VAR))
    return StoreDirResolution(
        REPO_LOCAL_DIR, REPO_LOCAL_DIR, checked, not on_platform
    )
