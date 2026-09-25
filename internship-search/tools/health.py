"""Is the agent alive?

    .venv/bin/python -m tools.health           # the one command that answers it
    .venv/bin/python -m tools.health --runs 10 # the recent scheduled runs too

Reads the heartbeat `tools/scheduled_run.py` writes after every run, and the
tail of the run log, and says whether anything is wrong. Spends nothing, sends
nothing and writes nothing.

This exists because between 2026-08-17 and 2026-08-18 the agent was dead for 42
hours and the only way to find out was to know which log to grep. The failure
alert in `agent/health.py` now pushes that answer to the owner's inbox; this is
the pull version, for when he wants to check rather than be told.

Exit code is 1 when the agent is broken or overdue, so this can be the thing a
shell alias or an external watchdog looks at.
"""

import argparse
import sys

from agent import health, schedule

# Read out of logs/run.log to show how the recent runs went. The wrapper writes
# one of these per run, and the word after the job name is the verdict.
RUN_MARKER = "=== end job="


def recent_runs(sched: schedule.Schedule, count: int) -> list[str]:
    try:
        with open(sched.run_log_path, encoding="utf-8", errors="replace") as fh:
            lines = [line.rstrip() for line in fh if RUN_MARKER in line]
    except OSError:
        return []
    return lines[-count:]


def spend_lines() -> list[str]:
    """Month-to-date model spend against rubric.md's [budget] block.

    Imported here, lazily, and never in `agent/health.py`: that module must
    survive a broken database (CLAUDE.md rule 12), and this command must still
    report health when the database cannot be read, so any failure here prints
    one line and nothing more.
    """
    try:
        from agent import db, ranker

        conn = db.connect()
        try:
            money = ranker.budget_state(conn)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return [f"Model spend this month: unavailable ({type(exc).__name__})"]
    if not money["limit"]:
        return [f"Model spend this month: ${money['spend']:.2f} (no [budget] in rubric.md)"]
    line = f"Model spend this month: ${money['spend']:.2f} of ${money['limit']:.2f}"
    if money["paused"]:
        line += "  PAUSED: ranking stops until next month"
    elif money["warn"]:
        line += "  close to the ceiling"
    return [line]


def stale_source_lines() -> list[str]:
    """Boards failing or empty for days. Lazily imported and fail-open, for the
    same reason as `spend_lines`."""
    try:
        from agent import db, delivery

        days = float(delivery.rules().get("daily", {}).get("stale_source_days", 0) or 0)
        conn = db.connect()
        try:
            stale = db.stale_sources(conn, days)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return [f"Boards down for days: unavailable ({type(exc).__name__})"]
    if not stale:
        return ["Boards down for days: none"]
    out = [f"Boards down for days: {len(stale)}"]
    for e in stale:
        out.append(f"  {e['name']}: {e['kind']} since {e['since']} ({e['days']} days)")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--runs",
        type=int,
        default=5,
        help="how many recent scheduled runs to list (default 5, 0 for none)",
    )
    args = parser.parse_args()

    try:
        sched = schedule.load()
    except schedule.ScheduleError as exc:
        print(f"sources/schedule.toml: {exc}", file=sys.stderr)
        return 1

    state = health.Health(sched.health_state_path, sched.health)

    print("Agent health")
    print("=" * 60)
    for line in state.status_lines():
        print(line)

    if not sched.health.enabled:
        print()
        print("Failure alerts are OFF (health.enabled is false in schedule.toml).")
        print("The heartbeat is still being recorded, so turning them back on")
        print("loses nothing.")

    print()
    for line in spend_lines():
        print(line)
    for line in stale_source_lines():
        print(line)

    if args.runs > 0:
        runs = recent_runs(sched, args.runs)
        print()
        print(f"Last {len(runs)} scheduled run(s):")
        if not runs:
            print("  nothing in the run log yet")
        for line in runs:
            print(f"  {line}")

    # A heartbeat this module has never seen written is not evidence of health.
    # Say so rather than printing a clean bill on an empty file.
    if state.last_success_at is None and not state.failing_since:
        print()
        print("No run has recorded a heartbeat yet. That is expected until the")
        print("next scheduled run finishes; it is not evidence that the agent is")
        print("healthy or that it is broken.")
        return 0

    if state.failing_since is not None or state.is_overdue():
        print()
        print("Something is wrong. The full log is logs/run.log.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
