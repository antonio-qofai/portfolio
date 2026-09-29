from datetime import datetime

from dashboard import api
from dashboard.refresh import TIMEOUT_SECONDS, Refresher
from dashboard.render import _greeting

NOW = datetime.fromisoformat("2026-09-29T15:00:00-05:00")


class FakeProc:
    def __init__(self):
        self.code, self.killed = None, False

    def poll(self):
        return self.code

    def kill(self):
        self.killed = True


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def refresher():
    procs, clock = [], Clock()
    def spawn():
        procs.append(FakeProc())
        return procs[-1]
    return Refresher(spawn=spawn, clock=clock), procs, clock


def test_one_build_at_a_time():
    r, procs, _ = refresher()
    assert r.status() == {"running": False, "failed": False}
    assert r.start() == {"running": True, "started": True}
    assert r.start() == {"running": True, "started": False}
    assert len(procs) == 1 and r.status()["running"]

    procs[0].code = 0
    assert r.status() == {"running": False, "failed": False}
    assert r.start()["started"] and len(procs) == 2


def test_failed_build_is_reported_until_the_next_one():
    r, procs, _ = refresher()
    r.start()
    procs[0].code = 1
    assert r.status() == {"running": False, "failed": True}
    r.start()
    assert r.status() == {"running": True, "failed": False}


def test_hung_build_is_killed():
    r, procs, clock = refresher()
    r.start()
    clock.t = TIMEOUT_SECONDS + 1
    assert r.status() == {"running": False, "failed": True} and procs[0].killed


def test_api_routes_refresh(tmp_path):
    r, procs, _ = refresher()
    assert api.handle("POST", "/api/refresh", b"{}", tmp_path, NOW, r) == (202, {"running": True, "started": True})
    assert api.handle("GET", "/api/refresh", b"", tmp_path, NOW, r)[1]["running"]
    # Without a refresher (tests, other callers) the route doesn't exist.
    assert api.handle("POST", "/api/refresh", b"{}", tmp_path, NOW)[0] == 404


def test_greeting_follows_the_hour():
    at = lambda h: NOW.replace(hour=h)
    assert [_greeting(at(h)) for h in (6, 11, 12, 16, 17, 23, 2)] == [
        "Good morning", "Good morning", "Good afternoon", "Good afternoon", "Good evening", "Good evening", "Good evening"]
