from datetime import datetime
from zoneinfo import ZoneInfo

from dashboard.config import load_config
from run import last_scheduled_time, needs_run
from scripts.launchd import plists

TZ = ZoneInfo("America/Chicago")


def t(day, hour, minute=0):
    return datetime(2026, 9, day, hour, minute, tzinfo=TZ)


def test_last_scheduled_time():
    config = load_config()
    assert last_scheduled_time(config, t(25, 6, 30)) == t(25, 6)
    assert last_scheduled_time(config, t(25, 5, 59)) == t(24, 6)


def test_needs_run():
    config = load_config()
    assert needs_run(config, t(25, 6), None)                   # never ran
    assert needs_run(config, t(25, 6), t(24, 6, 1))            # 6:00 trigger, last run yesterday
    assert needs_run(config, t(25, 9), t(24, 6, 1))            # woke at 9, missed 6:00
    assert not needs_run(config, t(25, 9), t(25, 6, 1))        # already ran today
    assert not needs_run(config, t(25, 3), t(24, 6, 1))        # before today's schedule


def test_launchd_plists():
    jobs = plists(load_config())
    build = jobs["com.user.life-dashboard.build"]
    serve = jobs["com.user.life-dashboard.serve"]
    assert build["ProgramArguments"][1:] == ["run.py", "--catch-up"]
    assert build["StartCalendarInterval"] == [{"Hour": 6, "Minute": 0}]
    assert build["RunAtLoad"]
    assert serve["ProgramArguments"][1:] == ["run.py", "--serve", "--no-build"]
    assert serve["KeepAlive"]
    digest = jobs["com.user.life-dashboard.digest"]
    assert digest["ProgramArguments"][1:] == ["run.py", "--digest"]
    assert digest["StartCalendarInterval"] == [{"Hour": 7, "Minute": 0}] and digest["RunAtLoad"]


def test_network_up_waits_then_gives_up(monkeypatch):
    import socket

    import run

    calls = []

    def dns(host, port):
        calls.append(host)
        if len(calls) < 3:
            raise socket.gaierror(8, "nodename nor servname provided")

    monkeypatch.setattr(socket, "getaddrinfo", dns)
    monkeypatch.setattr(run.time, "sleep", lambda _: None)
    assert run.network_up(wait=60) and len(calls) == 3

    monkeypatch.setattr(socket, "getaddrinfo", lambda *_: (_ for _ in ()).throw(socket.gaierror(8, "down")))
    assert not run.network_up(wait=0)
