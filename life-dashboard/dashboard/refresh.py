"""The page's "refresh now" button: rebuild the brief in a child process.

A child process (`run.py --trigger refresh`) keeps a slow or crashing build
out of the always-on server and picks up code changes without a restart.
One build at a time; a second press while one runs just reports it.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from typing import Any, Callable

from dashboard.config import ROOT

TIMEOUT_SECONDS = 300


def _spawn() -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, str(ROOT / "run.py"), "--trigger", "refresh"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


class Refresher:
    def __init__(self, spawn: Callable[[], Any] = _spawn, clock: Callable[[], float] = time.monotonic):
        self._spawn, self._clock = spawn, clock
        self._lock = threading.Lock()
        self._proc: Any = None
        self._started = 0.0
        self._failed = False

    def _poll(self) -> bool:
        """True while a build is running. Caller holds the lock."""
        if self._proc is None:
            return False
        code = self._proc.poll()
        if code is None and self._clock() - self._started > TIMEOUT_SECONDS:
            self._proc.kill()
            code = -1
        if code is None:
            return True
        self._failed, self._proc = code != 0, None
        return False

    def start(self) -> dict[str, bool]:
        with self._lock:
            if self._poll():
                return {"running": True, "started": False}
            self._proc, self._started, self._failed = self._spawn(), self._clock(), False
            return {"running": True, "started": True}

    def status(self) -> dict[str, bool]:
        with self._lock:
            return {"running": self._poll(), "failed": self._failed}
