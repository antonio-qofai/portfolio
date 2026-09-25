"""The run schedule, read from sources/schedule.toml.

This module contains no hour, no launchd label and no log path. It reads all of
them at runtime, on the same rule that keeps company tokens out of the fetchers
and the rubric out of the ranker: changing when the agent runs should be an edit
to a data file, never a code change.

Two things live here. Loading and validating the schedule, and turning a job in
it into a launchd property list. Installing that plist is `tools/schedule.py`,
and running one job is `tools/scheduled_run.py`.

Why launchd rather than cron is PRD section 4.
"""

import os
import plistlib
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .health import HealthRules

# The module launchd actually starts. It is the thing that reads the schedule,
# so it is deliberately not configured by the schedule; a file naming the
# program that reads it is a circle, not a setting.
WRAPPER_MODULE = "tools.scheduled_run"

# launchd starts a job with an almost empty environment and no shell. Nothing
# here shells out, but a subprocess that expects a usable PATH should find one.
JOB_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


class ScheduleError(RuntimeError):
    """sources/schedule.toml says something that cannot be turned into a job."""


@dataclass(frozen=True)
class Step:
    """One command in a run, and whether the run can survive it failing."""

    name: str
    module: str
    args: tuple[str, ...] = ()
    args_when_quiet: tuple[str, ...] = ()
    args_when_digest: tuple[str, ...] = ()
    # PRD section 4 calls the watcher the spine. Everything after it works on
    # what it stored, so a failure there stops the run and a failure anywhere
    # else is logged and stepped over.
    required: bool = False
    timeout_seconds: float = 900.0
    # Run this step only on the digest job, skipping it on the quiet runs. Added
    # 2026-08-15 for the Airtable sync, whose cost is metered by an external API
    # quota rather than by wall time. A step that talks to a rate-limited third
    # party does not need to keep pace with the watcher.
    digest_only: bool = False

    def command(self, python: Path, digest: bool) -> list[str]:
        extra = self.args_when_digest if digest else self.args_when_quiet
        return [str(python), "-m", self.module, *self.args, *extra]

    def runs_on(self, digest: bool) -> bool:
        return digest or not self.digest_only


@dataclass(frozen=True)
class Job:
    """One launchd agent: a set of times, and whether its run sends the digest."""

    key: str
    hours: tuple[int, ...]
    minute: int
    digest: bool
    description: str = ""


@dataclass(frozen=True)
class Schedule:
    label_prefix: str
    launch_agents_dir: Path
    python: str
    log_dir: str
    run_log: str
    launchd_log: str
    max_log_bytes: int
    health: HealthRules = field(default_factory=HealthRules)
    jobs: tuple[Job, ...] = ()
    steps: tuple[Step, ...] = ()

    # Paths are all resolved against the repo root, so nothing depends on the
    # directory launchd happens to start the job in.
    @property
    def python_path(self) -> Path:
        return config.ROOT / self.python

    @property
    def log_path(self) -> Path:
        return config.ROOT / self.log_dir

    @property
    def run_log_path(self) -> Path:
        return self.log_path / self.run_log

    @property
    def launchd_log_path(self) -> Path:
        return self.log_path / self.launchd_log

    @property
    def health_state_path(self) -> Path:
        return self.log_path / self.health.state_file

    def label(self, job: Job) -> str:
        return f"{self.label_prefix}.{job.key}"

    def plist_path(self, job: Job) -> Path:
        return self.launch_agents_dir / f"{self.label(job)}.plist"

    def job(self, key: str) -> Job:
        for j in self.jobs:
            if j.key == key:
                return j
        known = ", ".join(j.key for j in self.jobs)
        raise ScheduleError(f"no job named {key!r} in the schedule. Known jobs: {known}")


def load(path: Path | None = None) -> Schedule:
    path = path or config.SCHEDULE_PATH
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)

    settings = raw.get("schedule", {})
    missing = [
        k
        for k in ("label_prefix", "launch_agents_dir", "python", "log_dir", "run_log")
        if not settings.get(k)
    ]
    if missing:
        raise ScheduleError(f"{path.name} [schedule] is missing: {', '.join(missing)}")

    health_raw = raw.get("health", {})
    health = HealthRules(
        enabled=bool(health_raw.get("enabled", True)),
        silent_after_hours=float(health_raw.get("silent_after_hours", 14)),
        repeat_after_hours=float(health_raw.get("repeat_after_hours", 12)),
        state_file=health_raw.get("state_file") or "health.json",
        log_tail_lines=int(health_raw.get("log_tail_lines", 25)),
        min_sources_ok_fraction=float(health_raw.get("min_sources_ok_fraction", 0.25)),
    )

    jobs = []
    for entry in raw.get("jobs", []):
        key = entry.get("key")
        hours = tuple(entry.get("hours", ()))
        if not key:
            raise ScheduleError(f"a job in {path.name} has no key")
        if not hours:
            raise ScheduleError(f"job {key!r} lists no hours, so it would never run")
        minute = int(entry.get("minute", 0))
        for h in hours:
            if not 0 <= int(h) <= 23 or not 0 <= minute <= 59:
                raise ScheduleError(f"job {key!r} has an impossible time: {h}:{minute}")
        jobs.append(
            Job(
                key=key,
                hours=tuple(int(h) for h in hours),
                minute=minute,
                digest=bool(entry.get("digest", False)),
                description=entry.get("description", ""),
            )
        )

    if not jobs:
        raise ScheduleError(f"{path.name} defines no jobs")
    if len({j.key for j in jobs}) != len(jobs):
        raise ScheduleError(f"{path.name} defines two jobs with the same key")
    # Two jobs firing at the same minute would race for the same SQLite file.
    times = [(h, j.minute) for j in jobs for h in j.hours]
    if len(set(times)) != len(times):
        raise ScheduleError(
            f"{path.name} schedules two jobs at the same time, which would run two "
            "watchers against one database"
        )
    # More than one job sending email would put success criterion 5, at or under
    # 10 emails a week, back under the control of how often the watcher polls.
    if sum(1 for j in jobs if j.digest) != 1:
        raise ScheduleError(
            f"{path.name} must have exactly one job with digest = true. See PRD "
            "section 4: the others poll quietly so email volume stays decoupled "
            "from polling frequency"
        )

    steps = []
    for entry in raw.get("steps", []):
        if not entry.get("name") or not entry.get("module"):
            raise ScheduleError(f"a step in {path.name} is missing a name or a module")
        steps.append(
            Step(
                name=entry["name"],
                module=entry["module"],
                args=tuple(entry.get("args", ())),
                args_when_quiet=tuple(entry.get("args_when_quiet", ())),
                args_when_digest=tuple(entry.get("args_when_digest", ())),
                required=bool(entry.get("required", False)),
                timeout_seconds=float(entry.get("timeout_seconds", 900)),
                digest_only=bool(entry.get("digest_only", False)),
            )
        )

    if not steps:
        raise ScheduleError(f"{path.name} defines no steps, so a run would do nothing")

    for step in steps:
        if step.digest_only and step.required:
            raise ScheduleError(
                f"step {step.name!r} in {path.name} is both required and "
                "digest_only, so the three quiet runs would skip a step the run "
                "cannot proceed without. Pick one."
            )

    return Schedule(
        label_prefix=settings["label_prefix"],
        launch_agents_dir=Path(settings["launch_agents_dir"]).expanduser(),
        python=settings["python"],
        log_dir=settings["log_dir"],
        run_log=settings["run_log"],
        launchd_log=settings.get("launchd_log", "launchd.log"),
        max_log_bytes=int(settings.get("max_log_bytes", 5_000_000)),
        health=health,
        jobs=tuple(jobs),
        steps=tuple(steps),
    )


def plist_for(sched: Schedule, job: Job) -> dict:
    """The launchd agent for one job.

    StartCalendarInterval is the whole reason this is launchd. It fires at the
    listed times, and if the machine was asleep when one passed, it runs the job
    once on wake instead of discarding it the way cron would.

    RunAtLoad is false on purpose: installing the agents should not kick off a
    several-minute polling run in the middle of whatever the owner is doing.
    """
    return {
        "Label": sched.label(job),
        "ProgramArguments": [
            str(sched.python_path),
            "-m",
            WRAPPER_MODULE,
            "--job",
            job.key,
        ],
        "WorkingDirectory": str(config.ROOT),
        "StartCalendarInterval": [
            {"Hour": h, "Minute": job.minute} for h in sorted(job.hours)
        ],
        # The wrapper writes its own timestamped log. These two catch anything
        # that fails before it gets that far, which means a broken plist or a
        # missing interpreter, and should stay empty.
        "StandardOutPath": str(sched.launchd_log_path),
        "StandardErrorPath": str(sched.launchd_log_path),
        "RunAtLoad": False,
        "EnvironmentVariables": {"PATH": JOB_PATH},
    }


def plist_xml(sched: Schedule, job: Job) -> bytes:
    return plistlib.dumps(plist_for(sched, job))


def rotate(path: Path, max_bytes: int) -> bool:
    """Keep the log to two files: the live one and one previous.

    Called before a run appends, so a run is never split across the rotation.
    Returns True if it rotated, which the caller notes in the new file so the
    trail from one to the other is readable.
    """
    if not path.exists() or path.stat().st_size <= max_bytes:
        return False
    previous = path.with_suffix(path.suffix + ".1")
    previous.unlink(missing_ok=True)
    path.rename(previous)
    return True


def domain() -> str:
    """The launchd domain a per-user agent belongs to."""
    return f"gui/{os.getuid()}"
