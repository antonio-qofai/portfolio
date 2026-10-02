"""Where the append-only store lives, when the app is not on a laptop.

Why this exists
---------------
Until the app is hosted there is one state directory, `state/`, it is in the
repo, and git history is the audit trail. That works because the only writer is
a local process that commits.

A hosted writer breaks it. Railway builds a fresh container from the repo on
every deploy and discards anything written under it, so a decision recorded
through the page at 11am is gone at the next push. The decision on 2026-09-15
(build plan Q1) was a mounted volume plus a scheduled job that commits the store
back, and this module is the first half: finding the volume without knowing what
the platform calls it.

The split this module assumes
-----------------------------
Two roots, because two different things write them:

- **Windows** (`window-<lens>-<YYYY-MM>.json`) are written by the monthly
  assembly, the weekly refresh and the repair pass, all of which run in GitHub
  Actions against the repo and commit what they produce. Git is their producer,
  so the hosted app reads them from the repo checkout, which is refreshed on
  every deploy. It never writes one.
- **Decisions** (`approvals.jsonl`) are written by a founder pressing a button,
  which only ever happens in the running container. They go here.

Keeping them apart is what makes this small. There is no merge, because no file
has two writers. The one join is the first boot on an empty volume, where any
decision recorded before it lives only in the repo copy, so that file is seeded
across once and never again.

On a laptop every candidate below falls through to the repo's own `state/`, both
roots resolve to the same directory, the seed is a no-op, and nothing about a
local run changes.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_AGENT_ROOT = _HERE.parent

# Order matters. The explicit override costs nothing and settles any argument
# about what the volume is called; RAILWAY_VOLUME_MOUNT_PATH is what the
# platform injects on its own; /data is where the volume is asked to be
# mounted. Nobody has committed to which of the three will be set on the
# service, so this tries rather than guesses, the way the deck generator's
# `src/store_dir_resolver.py` does.
STORE_DIR_VARS = ("CALENDAR_STATE_DIR", "RAILWAY_VOLUME_MOUNT_PATH")
MOUNTED_VOLUME_PATH = "/data"

# Last candidate, and the authority for windows in every case. Committed, so it
# always exists and a local run needs no setup.
REPO_STATE_DIR = _AGENT_ROOT / "state"

# The one file that is seeded across, because the page is its only writer and a
# fresh volume would otherwise read as nobody having decided anything.
SEEDED_STORE = "approvals.jsonl"

# The platform always sets this and a laptop never does, so it is what separates
# "fell through to a container path" from "ran locally". Read at call time, not
# at import, so the durability branch stays testable.
PLATFORM_VAR = "RAILWAY_ENVIRONMENT"


class StateDirResolution:
    """A resolved store directory, where it came from, everything that was
    tried, and whether what is written there survives a redeploy."""

    def __init__(self, directory, source, checked, durable, seeded=None):
        self.directory = Path(directory)
        self.source = source
        self.checked = list(checked)
        self.durable = durable
        # Files copied in from the repo on this resolution. Empty on every run
        # after the first, and on every local run.
        self.seeded = list(seeded or ())

    def __repr__(self):
        return "StateDirResolution({} via {}; durable={})".format(
            self.directory, self.source, self.durable
        )

    def describe(self) -> str:
        """One line for a startup log, which is where anyone will look when a
        decision has gone missing."""
        where = f"decisions -> {self.directory} (via {self.source})"
        if not self.durable:
            return where + "; NOT durable, this is wiped on the next deploy"
        seeded = f"; seeded {', '.join(self.seeded)}" if self.seeded else ""
        return where + "; durable" + seeded


def window_root() -> Path:
    """The repo's own state directory. Windows are read from here always.

    Not a candidate list, because there is no question to answer: the jobs that
    write a window run against the repo, and the container gets their output by
    being rebuilt from it.
    """
    return REPO_STATE_DIR


def _usable(path) -> bool:
    """A candidate counts only if it is a directory that can be written to.

    A variable naming a path that was never mounted is the exact silent failure
    this pattern exists to catch, so an unmounted or read-only candidate is
    skipped rather than trusted.
    """
    return bool(path) and os.path.isdir(path) and os.access(path, os.W_OK)


def resolve_store_dir(seed: bool = True) -> StateDirResolution:
    """Tries each candidate in order and returns where decisions should go."""
    checked = []
    chosen = None
    source = None

    for name in STORE_DIR_VARS:
        checked.append(name)
        value = os.environ.get(name)
        if _usable(value):
            chosen, source = Path(value), name
            break

    if chosen is None:
        checked.append(MOUNTED_VOLUME_PATH)
        if _usable(MOUNTED_VOLUME_PATH):
            chosen, source = Path(MOUNTED_VOLUME_PATH), MOUNTED_VOLUME_PATH

    if chosen is None:
        checked.append(str(REPO_STATE_DIR))
        REPO_STATE_DIR.mkdir(parents=True, exist_ok=True)
        on_platform = bool(os.environ.get(PLATFORM_VAR))
        # Durable everywhere but on the platform. A notice that fires on every
        # local run is a notice nobody reads.
        return StateDirResolution(
            REPO_STATE_DIR, str(REPO_STATE_DIR), checked, not on_platform
        )

    seeded = seed_store(chosen) if seed else []
    return StateDirResolution(chosen, source, checked, True, seeded)


def seed_store(directory) -> list:
    """Copies the committed decision log across, once, if the target lacks it.

    Only ever adds a file that is not there. A volume that already holds
    decisions is the authority for them and is never written over from the
    repo, because the repo copy is at best an hour behind it and at worst the
    state of the last deploy.
    """
    directory = Path(directory)
    if directory.resolve() == REPO_STATE_DIR.resolve():
        return []
    source = REPO_STATE_DIR / SEEDED_STORE
    target = directory / SEEDED_STORE
    if not source.is_file() or target.exists():
        return []
    directory.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return [SEEDED_STORE]


if __name__ == "__main__":
    print(resolve_store_dir().describe())
    print(f"windows  -> {window_root()}")
