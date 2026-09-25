"""Install the launchd agents described in sources/schedule.toml.

    python -m tools.schedule --dry-run     # print the plists, touch nothing
    python -m tools.schedule --status      # what is installed, loaded, and when it last ran
    python -m tools.schedule --install     # write them and load them
    python -m tools.schedule --uninstall   # unload them and delete them

One launchd agent per job, generated from the TOML, so changing an hour is an
edit to that file and one `--install`. Installing writes into
`~/Library/LaunchAgents` and changes how the machine behaves, which is why it is
an explicit flag and never the default.

Nothing here is destructive to the agent's data. Uninstalling stops the runs and
leaves `state.db`, the logs and every posting exactly where they are.
"""

import argparse
import subprocess
import sys
from datetime import datetime

from agent import config, schedule


def launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["launchctl", *args], capture_output=True, text=True, check=False
    )


def describe(sched: schedule.Schedule) -> None:
    for job in sched.jobs:
        kind = "sends the digest" if job.digest else "quiet"
        times = ", ".join(f"{h:02d}:{job.minute:02d}" for h in sorted(job.hours))
        print(f"  {sched.label(job)}")
        print(f"    {times} local, {kind}")
        if job.description:
            print(f"    {job.description}")


def do_dry_run(sched: schedule.Schedule) -> int:
    print(f"From {config.SCHEDULE_PATH.name}:\n")
    describe(sched)
    print("\n  a run is:")
    for step in sched.steps:
        need = "required" if step.required else "optional"
        when = ", digest run only" if step.digest_only else ""
        print(
            f"    {step.name:<10} {need}, timeout "
            f"{step.timeout_seconds:.0f}s{when}"
        )
    print(f"\n  logs to {sched.run_log_path}, rotated past {sched.max_log_bytes} bytes")

    for job in sched.jobs:
        print(f"\n--- {sched.plist_path(job)} ---\n")
        print(schedule.plist_xml(sched, job).decode())

    print("Nothing was written. Use --install to write and load these.")
    return 0


def do_status(sched: schedule.Schedule) -> int:
    dom = schedule.domain()
    any_loaded = False

    for job in sched.jobs:
        path = sched.plist_path(job)
        label = sched.label(job)
        print(label)

        if not path.exists():
            print(f"  not installed. No file at {path}")
        else:
            current = schedule.plist_xml(sched, job)
            drift = "" if path.read_bytes() == current else "  <- differs from the TOML"
            print(f"  installed at {path}{drift}")

        printed = launchctl("print", f"{dom}/{label}")
        if printed.returncode == 0:
            any_loaded = True
            fields = {}
            for line in printed.stdout.splitlines():
                if "=" in line:
                    k, _, v = line.partition("=")
                    fields[k.strip()] = v.strip()
            state = fields.get("state", "loaded")
            last = fields.get("last exit code", "none recorded")
            print(f"  loaded, state {state}, last exit code {last}")
        else:
            print("  not loaded by launchd")

        times = ", ".join(f"{h:02d}:{job.minute:02d}" for h in sorted(job.hours))
        print(f"  scheduled {times} local\n")

    log = sched.run_log_path
    if log.exists():
        stat = log.stat()
        when = datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
        print(f"{log}: {stat.st_size} bytes, last written {when}")
        tail = [ln for ln in log.read_text(errors="replace").splitlines() if ln.strip()]
        for line in tail[-3:]:
            print(f"  {line}")
    else:
        print(f"{log} does not exist yet, so no scheduled run has ever written to it.")

    launchd_log = sched.launchd_log_path
    if launchd_log.exists() and launchd_log.stat().st_size:
        print(
            f"\n{launchd_log} is not empty, which means something failed before the "
            "run could start logging. Read it."
        )

    if not any_loaded:
        print("\nNothing is loaded. Nothing will run until you use --install.")
    return 0


def do_install(sched: schedule.Schedule) -> int:
    dom = schedule.domain()
    sched.launch_agents_dir.mkdir(parents=True, exist_ok=True)
    sched.log_path.mkdir(parents=True, exist_ok=True)

    if not sched.python_path.exists():
        print(f"No interpreter at {sched.python_path}. Fix python in the TOML first.")
        return 1

    for job in sched.jobs:
        path = sched.plist_path(job)
        label = sched.label(job)
        existed = path.exists()
        path.write_bytes(schedule.plist_xml(sched, job))
        print(f"{'rewrote' if existed else 'wrote  '}  {path}")

        # Booting a label out before booting it in is what makes a re-install
        # after a TOML edit pick up the new times. launchd will not reload a
        # plist under a label it already has.
        launchctl("bootout", f"{dom}/{label}")
        loaded = launchctl("bootstrap", dom, str(path))
        if loaded.returncode == 0:
            print(f"loaded    {label}")
        else:
            detail = (loaded.stderr or loaded.stdout).strip()
            print(f"FAILED to load {label}: {detail or loaded.returncode}")
            return 1

    print("\nInstalled. Check it with:\n")
    print("    .venv/bin/python -m tools.schedule --status")
    print(
        "\nTo prove a run works without waiting for the clock, start one by hand:\n"
    )
    print(f"    launchctl kickstart -k {dom}/{sched.label(sched.jobs[0])}")
    print(f"\nEvery run appends to {sched.run_log_path}.")
    return 0


def do_uninstall(sched: schedule.Schedule) -> int:
    dom = schedule.domain()
    removed = 0
    for job in sched.jobs:
        label = sched.label(job)
        path = sched.plist_path(job)
        out = launchctl("bootout", f"{dom}/{label}")
        print(f"{'unloaded' if out.returncode == 0 else 'not loaded'}  {label}")
        if path.exists():
            path.unlink()
            removed += 1
            print(f"deleted   {path}")

    print(
        f"\n{removed} plist(s) removed. Nothing else was touched: state.db, the logs "
        "and every posting are where they were. The agent now runs only when you "
        "run it."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dry-run", action="store_true", help="print the plists, write nothing"
    )
    group.add_argument(
        "--status", action="store_true", help="what is installed, loaded, and last logged"
    )
    group.add_argument(
        "--install", action="store_true", help="write into ~/Library/LaunchAgents and load"
    )
    group.add_argument("--uninstall", action="store_true", help="unload and delete them")
    args = parser.parse_args()

    if sys.platform != "darwin" and (args.install or args.uninstall):
        print("launchd is macOS only.")
        return 1

    try:
        sched = schedule.load()
    except schedule.ScheduleError as exc:
        print(f"sources/schedule.toml: {exc}", file=sys.stderr)
        return 1

    if args.install:
        return do_install(sched)
    if args.uninstall:
        return do_uninstall(sched)
    if args.status:
        return do_status(sched)
    return do_dry_run(sched)


if __name__ == "__main__":
    raise SystemExit(main())
