from datetime import datetime
from zoneinfo import ZoneInfo

from dashboard.config import load_config
from run import last_scheduled_time, needs_retry, needs_run, retries_today
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


def test_needs_retry_until_the_cap():
    config = load_config()
    config["schedule"]["retries"] = 3
    assert needs_retry(config, t(25, 7), ["email"], 0)
    assert needs_retry(config, t(25, 7), ["email", "ai_daily_brief"], 2)
    assert not needs_retry(config, t(25, 7), ["email"], 3)     # cap reached
    assert not needs_retry(config, t(25, 7), [], 0)            # nothing failed


def test_retries_today_counts_only_todays_retry_builds(tmp_path):
    config = load_config()
    log = tmp_path / "runs.log"
    log.write_text(
        "2026-09-24T06:40:00-05:00\tretry\tok\n"                        # yesterday
        "2026-09-25T06:18:00-05:00\tscheduled\tfailed=email\n"
        "2026-09-25T06:48:00-05:00\tretry\tfailed=email\n"
        "2026-09-25T07:18:00-05:00\tretry\tskipped=no_network\n"        # no build happened
        "2026-09-25T07:48:00-05:00\tretry\tfailed=email\n"
        "2026-09-25T08:00:00-05:00\trefresh\tfailed=email\n"
    )
    assert retries_today(config, t(25, 9), log) == 2
    assert retries_today(config, t(25, 9), tmp_path / "missing.log") == 0
