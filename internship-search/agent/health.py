"""Whether the agent is alive, and telling the owner when it is not.

Milestone 8, the first half. PRD success criterion 4 is fourteen consecutive
days unattended with no manual restarts, and the reason that criterion needed
its own code is what happened between 2026-08-17 and 2026-08-18: four runs in a
row crashed, and nothing said so for 42 hours.

The failure was not that it broke. Software breaks. The failure was that a dead
agent sends no email, and no email is exactly what a quiet day looks like. Every
signal this system produces travels down one channel, so when the channel stops
the silence reads as good news. This module exists to break that symmetry.

Four things it reports, and they are different questions:

  a run failed          a required step did not succeed, so the run stopped
  a run achieved nothing  every step finished and the run did not do its job
  the agent went quiet  a run succeeded, but far too long after the previous one
  it recovered          the first success after a failure, closing the incident

The second was added on 2026-09-07 and is the one that had been missing. Until
then this module asked only whether a run finished. Between 2026-08-21 and
2026-09-07, 22 of 57 runs asked all 176 sources, had all 176 fail on DNS, stored
nothing, and exited zero, and every one of them was recorded here as a success.
`tools.health` reported the agent healthy throughout. A monitor that certifies a
fortnight of doing nothing is worse than no monitor, because criterion 4 is
scored on what it says.

The middle one is the one worth having. A failure alert needs a run to reach the
end of `tools/scheduled_run.py` to send anything, so it cannot cover the case
where nothing runs at all: an unloaded plist, a laptop shut for a week, a python
that no longer starts. The gap check catches those retrospectively, the moment
anything runs again, by comparing against a heartbeat rather than by watching a
clock. It converts an invisible absence into a visible one.

What it does NOT cover, stated plainly because a monitor believed to cover more
than it does is worse than none: if nothing ever runs again, nothing here ever
fires. Closing that needs a watchdog outside this repo, and there is a note in
NEXT_STEPS about it.

Deliberately independent of the thing it watches
------------------------------------------------
The state lives in a small JSON file, not in the `agent_state` table that was
built for exactly this shape of fact. That is not an oversight. A monitor
sharing a dependency with the thing it monitors goes down with it, and SQLite is
the single biggest dependency in the run. The 2026-08-17 crash was a database
error; had the alerting needed the database to record that it alerted, it would
have been the second casualty.

For the same reason nothing here imports `agent.db`, `agent.delivery` or
anything that reads `rubric.md`. It needs `agent.notify.send`, which is
`smtplib` and a config lookup, and the standard library.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

# The alert has to survive a broken run, so it renders from plain strings and
# never from a posting, a score or anything the run produces.
SUBJECT_PREFIX = "Internship agent"

# How the watcher tells the wrapper what it achieved, added 2026-09-07 with the
# second question above.
#
# A printed line, and the format lives here rather than in `agent/run.py`, for
# the reason at the top of this file. The wrapper has to read it, and if it read
# it from the watcher it would import `agent.run`, and through it `agent.db`, so
# the module that reports a database failure would stop importing on one. That
# is the 2026-08-17 crash exactly. Stdout is a channel both ends already use and
# neither end has to import anything for.
RUN_REPORT_PREFIX = "=== run-report "


def run_report_line(sources_ok: int, sources_total: int, mail_failed: bool) -> str:
    """What one run achieved, in the terms the health check judges it on.

    The watcher prints this unconditionally, including under `--quiet`, which is
    what the three scheduled poll runs use. A diagnostic the scheduled runs
    suppress is a diagnostic that only exists when someone is already watching.
    """
    mail = "failed" if mail_failed else "ok"
    return (
        f"{RUN_REPORT_PREFIX}sources_ok={sources_ok} "
        f"sources_total={sources_total} mail={mail} ==="
    )


def parse_run_report(line: str) -> dict:
    """Read one back. Returns {} for anything that is not a run report.

    Tolerant on purpose. An unparseable field is dropped rather than raised on,
    because the caller is the failure monitor and it must never be the thing
    that breaks the run. A run whose report cannot be read is judged the way
    every run was judged before this existed, on whether it finished.
    """
    if not line.startswith(RUN_REPORT_PREFIX):
        return {}
    fields: dict = {}
    for token in line[len(RUN_REPORT_PREFIX) :].replace("===", "").split():
        key, _, value = token.partition("=")
        if not key or not value:
            continue
        fields[key] = int(value) if value.lstrip("-").isdigit() else value
    return fields


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    # A file hand-edited to a naive timestamp must not crash the alerting; it is
    # the last thing that should be fragile.
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _hours(delta: timedelta) -> float:
    return delta.total_seconds() / 3600.0


def describe_gap(delta: timedelta) -> str:
    """A duration a human reads at a glance, in the subject line of an alert."""
    hours = _hours(delta)
    if delta.total_seconds() < 60:
        return "under a minute"
    if hours < 1:
        return f"{int(delta.total_seconds() // 60)} minutes"
    if hours < 48:
        return f"{hours:.0f} hours"
    return f"{hours / 24:.1f} days"


@dataclass(frozen=True)
class HealthRules:
    """The thresholds, read from sources/schedule.toml. See that file."""

    enabled: bool = True
    silent_after_hours: float = 14.0
    repeat_after_hours: float = 12.0
    state_file: str = "health.json"
    log_tail_lines: int = 25
    min_sources_ok_fraction: float = 0.25


def sources_shortfall(ok: int, total: int, rules: HealthRules) -> str | None:
    """Did this run poll enough of the world to be worth calling a run?

    Returns the sentence to put in the alert, or None when the run was fine.

    Pure arithmetic on two numbers the watcher printed, which is what keeps this
    module free of `agent.db` under CLAUDE.md rule 12. The `runs` table has held
    `sources_ok` since Milestone 4 and reading it from there would have been one
    line, and would also have made the failure monitor depend on the database
    whose failure it was written to report.

    The floor is a fraction rather than a count because the board list grows;
    174 boards became 176 in one afternoon on 2026-08-19 and a hardcoded number
    would have been silently wrong from then on.

    Where the default sits and why. Two sources failing out of 176 is the
    ordinary weather and must never alert. Zero of 176 is the offline laptop and
    must always alert. The judgment call is the middle, and the real case that
    settled it is 2026-08-21, when 87 of 176 failed because the network returned
    part way through a run: genuinely degraded, already recovering, and covered
    by the next run three hours later. Alerting on that trains him to ignore
    these. So the floor is set low, at a quarter answering, which passes that
    run and catches both the dead ones. Set the fraction to 0 to switch the
    check off entirely.
    """
    if total <= 0 or rules.min_sources_ok_fraction <= 0:
        return None
    floor = max(1, int(total * rules.min_sources_ok_fraction))
    if ok >= floor:
        return None
    return (
        f"polled {ok} of {total} sources, under the floor of {floor}. "
        "The run finished, but it reached too little of the board list to be "
        "evidence that anything is working"
    )


@dataclass
class Report:
    """What one finished run means for the agent's health.

    `kind` is None when nothing needs sending, which is the overwhelmingly
    common case: a healthy run following a healthy run is not news.
    """

    kind: str | None = None
    subject: str = ""
    body: str = ""

    def __bool__(self) -> bool:
        return self.kind is not None


class Health:
    """The heartbeat file, and the decision about what it means.

    Loading tolerates a missing or corrupt file by starting fresh, because the
    alternative is a monitor that crashes the run it is supposed to be watching.
    A first run with no history is simply healthy with nothing to compare to.
    """

    def __init__(self, path: Path, rules: HealthRules):
        self.path = path
        self.rules = rules
        self.data = self._read()

    def _read(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as fh:
                loaded = json.load(fh)
            return loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Written whole and moved into place, so a run killed mid-write leaves
        # the previous heartbeat rather than a truncated one.
        temporary = self.path.with_suffix(".json.tmp")
        with open(temporary, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=2, sort_keys=True)
            fh.write("\n")
        temporary.replace(self.path)

    # ----------------------------------------------------------- reading it

    @property
    def last_success_at(self) -> datetime | None:
        return _parse(self.data.get("last_success_at"))

    @property
    def failing_since(self) -> datetime | None:
        return _parse(self.data.get("failing_since"))

    @property
    def last_alert_at(self) -> datetime | None:
        return _parse(self.data.get("last_alert_at"))

    @property
    def failed_runs(self) -> int:
        return int(self.data.get("failed_runs", 0))

    def is_overdue(self) -> bool:
        """True when nothing has succeeded recently enough to call this healthy.

        A heartbeat that has never been written is not overdue and not healthy
        either; it is unknown, and the caller distinguishes those.
        """
        last = self.last_success_at
        if last is None:
            return False
        return _hours(_now() - last) >= self.rules.silent_after_hours

    def status_lines(self) -> list[str]:
        """What `tools.health` prints. The one command that answers 'is it alive'."""
        now = _now()
        lines = []
        last = self.last_success_at
        if last is None:
            lines.append("No successful run has ever been recorded.")
        else:
            lines.append(
                f"Last successful run: {last.astimezone().isoformat(timespec='seconds')} "
                f"({describe_gap(now - last)} ago)"
            )
            if self.data.get("last_success_job"):
                lines.append(f"  job: {self.data['last_success_job']}")
        if self.data.get("last_sources"):
            lines.append(f"Last run polled: {self.data['last_sources']} sources")

        failing = self.failing_since
        if failing:
            lines.append("")
            lines.append(
                f"BROKEN since {failing.astimezone().isoformat(timespec='seconds')} "
                f"({describe_gap(now - failing)}), {self.failed_runs} failed run(s)."
            )
            if self.data.get("failing_step"):
                lines.append(f"  failing step: {self.data['failing_step']}")
            if self.data.get("failing_detail"):
                lines.append(f"  {self.data['failing_detail']}")
        elif last is not None:
            lines.append("")
            lines.append(
                "OVERDUE: nothing has run recently enough."
                if self.is_overdue()
                else "Healthy: the last run succeeded."
            )
        return lines

    # ---------------------------------------------------------- writing it

    def record_success(self, job: str) -> Report:
        """A run finished with every required step ok. Decide if that is news.

        Two things make it news. It is the first success after a failure, which
        closes an incident the owner was told about and should be told the end of.
        Or the previous success was so long ago that the agent was quietly not
        running, which no failure alert could have caught.
        """
        now = _now()
        previous = self.last_success_at
        was_failing = self.failing_since
        failed_runs = self.failed_runs
        failing_step = self.data.get("failing_step", "")

        report = Report()

        if was_failing:
            broken_for = describe_gap(now - was_failing)
            report = Report(
                kind="recovered",
                subject=f"{SUBJECT_PREFIX} recovered after {broken_for}",
                body="\n".join(
                    [
                        f"The {job!r} run just completed with every required step ok.",
                        "",
                        f"It had been failing since "
                        f"{was_failing.astimezone().isoformat(timespec='seconds')}, "
                        f"which is {broken_for}, across {failed_runs} run(s).",
                        f"The step that was failing: {failing_step or 'unknown'}.",
                        "",
                        "Coverage during that window is gone and cannot be "
                        "recovered: postings that opened and closed while the "
                        "agent was down were never seen.",
                    ]
                ),
            )
        elif previous is not None and _hours(now - previous) >= self.rules.silent_after_hours:
            quiet_for = describe_gap(now - previous)
            report = Report(
                kind="gap",
                subject=f"{SUBJECT_PREFIX} was silent for {quiet_for}",
                body="\n".join(
                    [
                        f"The {job!r} run just succeeded, so the agent is working now.",
                        "",
                        f"But the previous successful run was "
                        f"{previous.astimezone().isoformat(timespec='seconds')}, "
                        f"{quiet_for} ago, and the schedule expects one within "
                        f"{self.rules.silent_after_hours:.0f} hours.",
                        "",
                        "No run failed, so nothing raised an alarm at the time. "
                        "Something stopped the runs from starting at all: a "
                        "closed laptop, an unloaded launchd job, or a machine "
                        "that was off. Worth checking which.",
                        "",
                        "    launchctl list | grep internship-search",
                    ]
                ),
            )

        self.data.update(
            {
                "last_success_at": now.isoformat(timespec="seconds"),
                "last_success_job": job,
                "last_run_at": now.isoformat(timespec="seconds"),
                "last_run_ok": True,
            }
        )
        # The incident is over, so the throttle resets with it. A later failure
        # is a new incident and alerts immediately rather than waiting out a
        # repeat window belonging to the last one.
        for key in ("failing_since", "failing_step", "failing_detail", "last_alert_at"):
            self.data.pop(key, None)
        self.data["failed_runs"] = 0
        return report

    def record_sources(self, ok, total) -> None:
        """Remember what the last run actually reached, for `tools.health`.

        Nothing decides anything on this; it is here so the one command that
        answers "is it alive" can show the number the verdict was reached on
        rather than only the verdict. A heartbeat that says BROKEN without
        saying what it saw sends him to the log to find out.
        """
        if ok is None or total is None:
            return
        self.data["last_sources"] = f"{ok}/{total}"

    def _note_fault(self, step: str, detail: str) -> tuple[bool, datetime, datetime]:
        """Open or extend an incident. Returns whether an alert is due now.

        Shared by the two kinds of bad run, a step that failed and a run that
        finished having achieved nothing, because the incident bookkeeping is
        identical for both and only the wording differs. Keeping one copy is
        also what stops a degraded run opening a second incident alongside a
        crash it is already part of.

        Alerts on the first fault of an incident and then goes quiet for
        `repeat_after_hours`. Four runs a day against a fault that takes days to
        notice would otherwise mean an inbox full of the same alert, which
        trains him to ignore the one email that is never routine.
        """
        now = _now()
        first = self.failing_since is None
        if first:
            self.data["failing_since"] = now.isoformat(timespec="seconds")
        self.data.update(
            {
                "failing_step": step,
                "failing_detail": detail,
                "last_run_at": now.isoformat(timespec="seconds"),
                "last_run_ok": False,
                "failed_runs": self.failed_runs + 1,
            }
        )
        last_alert = self.last_alert_at
        due = (
            first
            or last_alert is None
            or _hours(now - last_alert) >= self.rules.repeat_after_hours
        )
        return due, now, self.failing_since or now

    def record_degraded(self, job: str, step: str, detail: str) -> Report:
        """Every step finished and the run still did not do its job.

        The case this was built for is the offline laptop. The watcher asks 176
        boards, every request fails to resolve, `SourceError` is raised for each
        one, and the run does exactly what CLAUDE.md rule 13 requires: it stores
        nothing, closes nothing, and exits zero. Rule 13 is right and this is not
        a bug in it. But a run that correctly did nothing is still a run that did
        nothing, and until 2026-09-07 it was indistinguishable here from a run
        that polled the world and found a quiet day.

        Deliberately not a variant of `record_failure`. That email says nothing
        after the failing step ran, which would be false here and would send him
        looking for a crash that did not happen.
        """
        due, now, since = self._note_fault(step, detail)
        if not due:
            return Report()

        broken_for = describe_gap(now - since)
        told_before = self.last_alert_at is not None
        headline = (
            f"{SUBJECT_PREFIX} still not polling, {broken_for}"
            if told_before
            else f"{SUBJECT_PREFIX} ran but did nothing"
        )
        lines = [
            f"The {job!r} run completed every step and achieved nothing: {detail}.",
            "",
            f"Failing since {since.astimezone().isoformat(timespec='seconds')} "
            f"({broken_for}), {self.failed_runs} run(s) so far.",
            "",
            "Nothing crashed, which is why no failure alert covered this before "
            "2026-09-07. The likeliest cause by far is that the machine had no "
            "network when the schedule fired, and the second likeliest is that "
            "every board changed at once, which has never happened.",
            "",
            "No posting was falsely closed by this. A source that cannot be "
            "reached is never read as evidence that its postings went away. "
            "What is lost is the polling itself: anything that opened and "
            "closed while this was going on was never seen.",
            "",
            "    ping -c 1 boards-api.greenhouse.io",
            "    .venv/bin/python -m tools.health",
        ]
        return Report(kind="degraded", subject=headline, body="\n".join(lines))

    def record_failure(self, job: str, step: str, detail: str, tail: list[str]) -> Report:
        """A required step did not succeed, so the run stopped."""
        due, now, since = self._note_fault(step, detail)
        if not due:
            return Report()

        broken_for = describe_gap(now - since)
        # "still failing" keys on whether an alert ever SENT, not on whether
        # this is the first failure. An incident whose first alert was
        # suppressed, by health.enabled or by a mail server that was down, would
        # otherwise open with an email saying "still", telling him he had
        # already been warned about something he is hearing for the first time.
        told_before = self.last_alert_at is not None
        headline = (
            f"{SUBJECT_PREFIX} still failing at {step}, {broken_for}"
            if told_before
            else f"{SUBJECT_PREFIX} FAILED at {step}"
        )
        last_ok = self.last_success_at
        lines = [
            f"The {job!r} run stopped: {detail}",
            "",
            f"Failing since {since.astimezone().isoformat(timespec='seconds')} "
            f"({broken_for}), {self.failed_runs} run(s) so far.",
        ]
        if last_ok:
            lines.append(
                f"Last fully successful run: "
                f"{last_ok.astimezone().isoformat(timespec='seconds')} "
                f"({describe_gap(now - last_ok)} ago)."
            )
        else:
            lines.append("No successful run has ever been recorded.")
        lines += [
            "",
            "Nothing after the failing step ran, so no posting was fetched, "
            "filtered or emailed. Every hour this stays broken is coverage that "
            "cannot be recovered afterwards.",
            "",
            f"Last {len(tail)} line(s) of output:",
            "",
        ]
        lines.extend(f"    {line}" for line in tail)
        lines += [
            "",
            "The full log is logs/run.log. To reproduce it by hand:",
            "",
            "    .venv/bin/python -m tools.scheduled_run --job " + job,
        ]
        return Report(kind="failure", subject=headline, body="\n".join(lines))

    def record_alert_sent(self) -> None:
        """Called only when an alert actually went out.

        The same rule as CLAUDE.md rule 10 and for the same reason. This stamp
        is what silences the next few runs, so writing it for an email that
        never sent buys `repeat_after_hours` of silence about a broken agent,
        which is the precise failure this whole module exists to prevent.
        """
        self.data["last_alert_at"] = _now().isoformat(timespec="seconds")
