"""One scheduled run: poll, then sync Airtable, then sync the calendar.

    .venv/bin/python -m tools.scheduled_run --job poll      # a quiet run
    .venv/bin/python -m tools.scheduled_run --job digest    # the run that emails
    .venv/bin/python -m tools.scheduled_run --job poll --dry-run   # print the commands

This is what launchd starts. It exists because launchd runs one program, and a
run is three programs with an order and a failure rule.

The order and the rule are in `sources/schedule.toml`. The watcher is required:
if it fails the run stops, because everything after it works on what it stored,
and PRD section 4 calls it the spine. The two syncs are not required. An expired
Google token or an Airtable outage is logged, stepped over, and costs nothing
but itself.

When a required step fails, the run also emails the owner about it, and says so
again when the next run recovers. That is `agent/health.py`, and the reason it
exists is that on 2026-08-17 four runs crashed in a row and nothing said so for
42 hours: a dead agent sends no email, and no email looks exactly like a quiet
day. The alert is best effort and never affects the run's exit code, because a
mail server being down must not turn a working agent into a failed one.

A run that finishes is not automatically a run that worked, and since 2026-09-07
this file checks the difference. The watcher prints one machine-readable line
saying how many sources it reached and whether its email went out; `run_step`
picks it out of the stream and `degraded_verdict` rules on it. A run below the
floor in `sources/schedule.toml` is recorded as a fault and logged as DEGRADED
rather than ok. Before that, 22 of the 57 runs in a fortnight polled 0 of 176
boards on a laptop with no network, exited zero, and were all counted healthy.

Every line, including every line the three steps print, is written to
`logs/run.log` with the local time in front of it. A scheduled run has no
terminal to print to, and a run with no timestamps cannot answer the only
question worth asking afterwards, which is whether it happened at all. Run by
hand in a terminal, it also prints as it goes.
"""

import argparse
import collections
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from agent import config, health, notify, schedule


class Log:
    """Appends timestamped lines to the run log, and echoes to a terminal."""

    def __init__(self, path: Path, echo: bool):
        self.handle = open(path, "a", buffering=1, encoding="utf-8")
        self.echo = echo

    def line(self, text: str = "") -> None:
        stamp = datetime.now().isoformat(timespec="seconds")
        entry = f"{stamp}  {text}".rstrip()
        self.handle.write(entry + "\n")
        if self.echo:
            print(entry, flush=True)

    def close(self) -> None:
        self.handle.close()


@dataclass
class StepResult:
    """How one step went, in the terms the failure alert needs to describe it.

    `tail` is the last few lines the step printed. A failure email that says a
    step failed and nothing more is an alarm; one carrying the traceback is
    something the owner can act on from his phone without opening the laptop.
    """

    ok: bool
    seconds: float
    detail: str = ""
    tail: list[str] = field(default_factory=list)
    # What the step said it achieved, parsed from the one machine-readable line
    # `agent/run.py` prints. Empty for the two syncs, which print no such line,
    # and empty for a watcher that died before reaching the end of its own main.
    report: dict = field(default_factory=dict)


def run_step(
    step: schedule.Step, python: Path, digest: bool, log: Log, tail_lines: int
) -> StepResult:
    """Run one step, streaming its output into the log."""
    command = step.command(python, digest)
    log.line(f"[{step.name}] $ {' '.join(command)}")
    started = time.monotonic()

    # Bounded on purpose. A step that prints 20,000 postings must not be held in
    # memory just in case it fails at the end.
    recent: collections.deque[str] = collections.deque(maxlen=max(tail_lines, 1))
    report: dict = {}

    proc = subprocess.Popen(
        command,
        cwd=config.ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    # A hung HTTP read would otherwise leave launchd believing the job is still
    # running, and launchd will not start a second copy of a job that is, so one
    # stuck read could silently cost every run after it.
    timed_out = threading.Event()

    def give_up():
        timed_out.set()
        proc.kill()

    watchdog = threading.Timer(step.timeout_seconds, give_up)
    watchdog.start()
    try:
        for line in proc.stdout:
            text = line.rstrip()
            recent.append(text)
            # Picked out while streaming rather than from `recent`, which holds
            # only the last few lines and only matters on a failure. This line
            # matters most on a step that succeeded.
            report = health.parse_run_report(text) or report
            log.line(f"[{step.name}] {text}")
        code = proc.wait()
    finally:
        watchdog.cancel()
        proc.stdout.close()

    elapsed = time.monotonic() - started

    if timed_out.is_set():
        detail = f"{step.name} timed out after {step.timeout_seconds:.0f}s and was killed"
        log.line(f"[{step.name}] TIMED OUT after {step.timeout_seconds:.0f}s, killed")
        return StepResult(False, elapsed, detail, list(recent), report)
    if code != 0:
        detail = f"{step.name} exited with code {code} after {elapsed:.0f}s"
        log.line(f"[{step.name}] FAILED with exit code {code} after {elapsed:.0f}s")
        return StepResult(False, elapsed, detail, list(recent), report)

    log.line(f"[{step.name}] ok in {elapsed:.0f}s")
    return StepResult(True, elapsed, report=report)


def degraded_verdict(sched: schedule.Schedule, report: dict) -> tuple[str, str] | None:
    """Did a run that finished cleanly actually do its job? (step, detail) if not.

    Both answers here are things a run can get wrong while every step exits
    zero, which is why neither was caught until 2026-09-07.

    The sources floor is the offline laptop, and it is by far the more important
    of the two: 22 of the 57 runs in the fortnight to 2026-09-07 polled nothing
    at all and every one was recorded as a success.

    The mail one is newer than that and exists because of the fix that landed
    the same day. `notify.send` no longer raises when the mail server is
    unreachable, which is right, and it means a failed digest no longer stops
    the run. Without this the fix would have traded a wrong alert for no alert.
    """
    ok, total = report.get("sources_ok"), report.get("sources_total")
    if isinstance(ok, int) and isinstance(total, int):
        shortfall = health.sources_shortfall(ok, total, sched.health)
        if shortfall:
            return "sources", shortfall
    if report.get("mail") == "failed":
        return "email", (
            "every step finished, but an email this run had ready could not be "
            "sent. Nothing it carried was stamped, so the next run offers it "
            "again; the mail server or the network is what needs looking at"
        )
    return None


def end_line(
    job_key: str,
    seconds: float,
    stopped: str | None,
    degraded: tuple[str, str] | None,
    failed_optional: list[str],
) -> str:
    """The last line of a run in the log, and the one a person greps for.

    Its own function so the wording can be tested. That sounds fussy for a log
    line and is not: the fortnight to 2026-09-07 was diagnosed by scrolling this
    log, and every one of the 22 runs that polled nothing ended with the word
    "ok" in it. A degraded run has to be visibly not ok to someone reading fast.
    """
    stepped = (
        f", stepped over: {', '.join(failed_optional)}" if failed_optional else ""
    )
    if stopped:
        return f"=== end job={job_key} FAILED at {stopped} after {seconds:.0f}s ==="
    if degraded:
        return (
            f"=== end job={job_key} DEGRADED in {seconds:.0f}s{stepped}: "
            f"{degraded[1]} ==="
        )
    return f"=== end job={job_key} ok in {seconds:.0f}s{stepped} ==="


def report_health(
    sched: schedule.Schedule,
    job_key: str,
    stopped: str | None,
    failure: StepResult | None,
    log: Log,
    degraded: tuple[str, str] | None = None,
    report_fields: dict | None = None,
) -> None:
    """Update the heartbeat and email the owner if this run changed the story.

    Wrapped whole in a try/except and never allowed to raise. This is the step
    that runs after everything else has already succeeded or failed, and a
    monitor that can turn a working run into a crashed one is worse than no
    monitor. Whatever goes wrong here goes in the log and nowhere else.

    An alert is stamped only when it actually sent, on the same rule as
    CLAUDE.md rule 10. The stamp is what silences the next several runs, so
    writing it for an email that never left would buy twelve hours of quiet
    about a broken agent, which is precisely the failure this exists to stop.
    """
    try:
        state = health.Health(sched.health_state_path, sched.health)
        fields = report_fields or {}
        state.record_sources(fields.get("sources_ok"), fields.get("sources_total"))
        if stopped:
            report = state.record_failure(
                job_key,
                stopped,
                failure.detail if failure else f"{stopped} did not succeed",
                failure.tail if failure else [],
            )
        elif degraded:
            # A step that crashed already opened the incident above, and a run
            # that crashed at the watcher never reaches this. So this branch is
            # only the case where everything exited zero and the run was still
            # not worth having.
            report = state.record_degraded(job_key, degraded[0], degraded[1])
        else:
            report = state.record_success(job_key)

        # Written before the send is attempted, never after it. `record_failure`
        # only mutates memory, so whatever persists it has to run before
        # anything that can raise. The send touches the network, and the network
        # is the single most likely reason a run failed in the first place, so
        # putting the save downstream of it means the outage that broke the run
        # also erases the record of the run breaking.
        #
        # That is not hypothetical. On 2026-08-20 the digest failed at the
        # watcher, the alert could not resolve DNS, the exception skipped the
        # save, and the heartbeat was left reading failed_runs 0 and
        # last_run_ok true. The lost email is the small half: `failing_since`
        # and `failed_runs` are what the escalation is counted from, so a
        # sustained offline spell, which is the exact fault this monitor was
        # built for, would never have climbed the ladder at all.
        state.save()

        if report and sched.health.enabled:
            if notify.send(report.subject, report.body):
                state.record_alert_sent()
                # Saved again for the stamp alone. Only reached when the mail
                # actually left, so a failed send leaves the heartbeat holding
                # the failure with no stamp, and the next run tries again.
                state.save()
                log.line(f"=== health: emailed [{report.kind}] {report.subject} ===")
            else:
                # Not configured, or the send declined. Do not stamp: the next
                # run should try again rather than assume he was told.
                log.line(f"=== health: [{report.kind}] NOT SENT, email unavailable ===")
        elif report:
            log.line(f"=== health: [{report.kind}] suppressed, health.enabled is false ===")
    except Exception as exc:  # noqa: BLE001 - see the docstring
        log.line(f"=== health: could not record or alert: {exc!r} ===")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--job", required=True, help="which job in sources/schedule.toml")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the commands this job would run, run nothing, write no log",
    )
    args = parser.parse_args()

    try:
        sched = schedule.load()
        job = sched.job(args.job)
    except schedule.ScheduleError as exc:
        print(f"sources/schedule.toml: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        kind = "sends the digest" if job.digest else "quiet, sends no email"
        print(f"Job {job.key!r}, {kind}.")
        print(f"Runs at {', '.join(f'{h:02d}:{job.minute:02d}' for h in job.hours)} local.")
        print(f"Logging to {sched.run_log_path}\n")
        for step in sched.steps:
            if not step.runs_on(job.digest):
                print(f"  {step.name}  (digest_only, skipped on this job)")
                continue
            need = "required" if step.required else "optional, failure is stepped over"
            print(f"  {step.name}  ({need}, timeout {step.timeout_seconds:.0f}s)")
            print(f"    {' '.join(step.command(sched.python_path, job.digest))}")
        print("\nNothing was run.")
        return 0

    if not sched.python_path.exists():
        print(f"No interpreter at {sched.python_path}", file=sys.stderr)
        return 1

    sched.log_path.mkdir(parents=True, exist_ok=True)
    rotated = schedule.rotate(sched.run_log_path, sched.max_log_bytes)

    log = Log(sched.run_log_path, echo=sys.stdout.isatty())
    if rotated:
        log.line(
            f"log passed {sched.max_log_bytes} bytes, previous run log is "
            f"{sched.run_log_path.name}.1"
        )
    log.line("")
    log.line(f"=== start job={job.key} digest={job.digest} ===")

    started = time.monotonic()
    failed_optional = []
    stopped = None
    failure = None
    run_report: dict = {}

    for step in sched.steps:
        if not step.runs_on(job.digest):
            log.line(f"--- skip {step.name}: digest_only and this run is quiet ---")
            continue
        result = run_step(
            step, sched.python_path, job.digest, log, sched.health.log_tail_lines
        )
        # Only the watcher prints one, but taking whichever step produced it
        # avoids naming a step here, and a step name in this file is a thing
        # `sources/schedule.toml` is allowed to change.
        run_report = result.report or run_report
        if result.ok:
            continue
        if step.required:
            stopped = step.name
            failure = result
            log.line(f"=== stopping: {step.name} is required and did not succeed ===")
            break
        failed_optional.append(step.name)
        log.line(f"[{step.name}] not required, carrying on")

    total = time.monotonic() - started
    degraded = None if stopped else degraded_verdict(sched, run_report)
    log.line(end_line(job.key, total, stopped, degraded, failed_optional))

    report_health(sched, job.key, stopped, failure, log, degraded, run_report)
    log.close()

    # launchd records this. Non-zero on a failed watcher is what makes the
    # failure visible to `launchctl print` as well as to the log, and a degraded
    # run counts, because the whole point of the check is that such a run is not
    # a success. Nothing restarts on it: RunAtLoad is false and no job sets
    # KeepAlive, so this is a record rather than a trigger.
    return 1 if stopped or degraded else 0


if __name__ == "__main__":
    raise SystemExit(main())
