"""Cases the failure alert must get right. Run with:

    .venv/bin/python -m tools.test_health

No test framework and no email. Each case builds a throwaway heartbeat file,
feeds it a sequence of run outcomes, and checks which alerts it decided to send.

This exists because the alerting is the one part of the system whose own failure
is invisible. Everything else announces a mistake by producing a wrong email;
this announces one by producing no email, which is exactly what a healthy quiet
day looks like. The two directions it can be wrong are equally bad and neither
shows up in a log:

  a missed alert     the agent is dead and nothing says so, which is the
                     42 hour outage of 2026-08-17 happening again
  a false alert      an email on every run, which trains him to ignore the one
                     message in this system that is never routine

Add a case here whenever the alerting rules change.
"""

import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from agent import health


def make(tmp: Path, name: str, **overrides) -> health.Health:
    rules = health.HealthRules(
        silent_after_hours=overrides.pop("silent_after_hours", 14),
        repeat_after_hours=overrides.pop("repeat_after_hours", 12),
    )
    return health.Health(tmp / f"{name}.json", rules)


def shift(state: health.Health, hours: float) -> None:
    """Move every timestamp in the heartbeat back, to fake the passage of time.

    Rewinding the file rather than mocking the clock keeps the module's own
    `_now()` honest: the test exercises the same comparison the real run does.
    """
    for key in ("last_success_at", "failing_since", "last_alert_at", "last_run_at"):
        stamp = health._parse(state.data.get(key))
        if stamp is not None:
            state.data[key] = (stamp - timedelta(hours=hours)).isoformat(
                timespec="seconds"
            )


def fail(state: health.Health, step: str = "watcher") -> health.Report:
    return state.record_failure(
        "poll", step, f"{step} exited with code 1 after 32s", ["Traceback", "IntegrityError"]
    )


# Each case takes a fresh heartbeat and returns a list of failure strings.
def case_first_failure_alerts(state) -> list[str]:
    """The 2026-08-17 outage. The first crash must reach him immediately."""
    state.record_success("poll")
    report = fail(state)

    out = []
    if not report:
        out.append("the first failure of an incident sent nothing")
    elif report.kind != "failure":
        out.append(f"expected a failure alert, got {report.kind!r}")
    if report and "watcher" not in report.subject:
        out.append(f"the subject does not name the failing step: {report.subject!r}")
    if report and "IntegrityError" not in report.body:
        out.append("the alert body does not carry the step's output")
    return out


def case_repeat_is_throttled(state) -> list[str]:
    """Four runs a day against one fault must not mean four emails a day."""
    state.record_success("poll")
    fail(state)
    state.record_alert_sent()

    out = []
    if fail(state):
        out.append("a second failure four hours later alerted again")
    shift(state, 6)
    if fail(state):
        out.append("a failure six hours into the incident alerted again")
    shift(state, 8)  # now past repeat_after_hours since the alert
    if not fail(state):
        out.append("a failure past the repeat window did not alert")
    return out


def case_unsent_alert_is_not_stamped(state) -> list[str]:
    """CLAUDE.md rule 10, applied to the alerting itself.

    record_alert_sent is called only when the mail actually left. If a failed
    send stamped anyway it would buy twelve hours of silence about a broken
    agent, which is the exact failure this module exists to prevent.
    """
    state.record_success("poll")
    first = fail(state)
    # Deliberately do NOT call record_alert_sent: the send failed.
    second = fail(state)

    out = []
    if not first:
        out.append("the first failure did not alert")
    if not second:
        out.append("an unsent alert silenced the next run's alert")
    return out


def case_first_email_never_says_still(state) -> list[str]:
    """Found by running the real wrapper end to end on 2026-08-18.

    The first alert that actually reaches him must not open with "still
    failing". An incident whose earlier alerts were suppressed, by
    health.enabled or by a mail server that was down, would otherwise tell him
    he had already been warned about something he is hearing for the first time.
    """
    state.record_success("poll")
    fail(state)          # first failure, alert suppressed: never sent
    report = fail(state)  # the first one he will actually read

    out = []
    if not report:
        out.append("the second failure of an unalerted incident sent nothing")
    elif "still failing" in report.subject:
        out.append(f"the first email he receives says 'still': {report.subject!r}")

    # And once he HAS been told, the follow-up should say so.
    state.record_alert_sent()
    shift(state, 24)
    followup = fail(state)
    if followup and "still failing" not in followup.subject:
        out.append(f"a follow-up alert does not say 'still': {followup.subject!r}")
    return out


def case_recovery_closes_the_incident(state) -> list[str]:
    """He was told it broke, so he gets told it is fixed."""
    state.record_success("poll")
    fail(state)
    state.record_alert_sent()
    shift(state, 20)
    report = state.record_success("digest")

    out = []
    if not report:
        out.append("recovering after a failure sent nothing")
    elif report.kind != "recovered":
        out.append(f"expected a recovery notice, got {report.kind!r}")
    if state.failing_since is not None:
        out.append("the incident stayed open after a successful run")
    if state.failed_runs != 0:
        out.append("the failed run count survived the recovery")
    return out


def case_healthy_run_is_silent(state) -> list[str]:
    """The common case. A good run after a good run is not news."""
    state.record_success("poll")
    shift(state, 4)
    report = state.record_success("poll")

    out = []
    if report:
        out.append(f"a healthy run sent a {report.kind!r} email")
    return out


def case_silent_gap_is_reported(state) -> list[str]:
    """The failure no failure alert can catch.

    Nothing ran for two days: an unloaded plist, a shut laptop, a machine that
    was off. No run failed, so nothing raised an alarm at the time. The next
    successful run is the only chance to notice it happened at all.
    """
    state.record_success("poll")
    shift(state, 48)
    report = state.record_success("poll")

    out = []
    if not report:
        out.append("a two day silence was never reported")
    elif report.kind != "gap":
        out.append(f"expected a gap notice, got {report.kind!r}")
    return out


def case_gap_inside_the_window_is_silent(state) -> list[str]:
    """The overnight gap is 10 hours and must never alert."""
    state.record_success("poll")
    shift(state, 10)
    report = state.record_success("poll")

    out = []
    if report:
        out.append(f"the normal overnight gap sent a {report.kind!r} email")
    return out


def case_recovery_wins_over_gap(state) -> list[str]:
    """A long outage is one story, not two.

    After a multi-day failure both conditions are true: it recovered, and the
    last success was ages ago. He gets the recovery, which explains why, rather
    than a gap notice saying nothing ran and leaving him to guess.
    """
    state.record_success("poll")
    fail(state)
    state.record_alert_sent()
    shift(state, 72)
    report = state.record_success("poll")

    out = []
    if report and report.kind != "recovered":
        out.append(f"a recovery from a long outage reported {report.kind!r}")
    return out


def case_first_ever_run_is_silent(state) -> list[str]:
    """A fresh install has nothing to compare against and must not cry wolf."""
    report = state.record_success("poll")

    out = []
    if report:
        out.append(f"the first run ever recorded sent a {report.kind!r} email")
    if state.is_overdue():
        out.append("a run that just succeeded was called overdue")
    return out


def case_corrupt_heartbeat_does_not_crash(state) -> list[str]:
    """The monitor must never be what takes the run down.

    A half-written or hand-edited file starts fresh rather than raising. It
    loses history, which is worth strictly less than a run.
    """
    out = []
    state.path.parent.mkdir(parents=True, exist_ok=True)
    state.path.write_text("{not json at all", encoding="utf-8")
    try:
        reloaded = health.Health(state.path, state.rules)
        reloaded.record_success("poll")
        reloaded.save()
    except Exception as exc:  # noqa: BLE001
        out.append(f"a corrupt heartbeat raised {exc!r}")
        return out

    if reloaded.last_success_at is None:
        out.append("a run after a corrupt heartbeat recorded no heartbeat")
    return out


def case_state_survives_a_reload(state) -> list[str]:
    """Each run is a new process, so everything has to come back off disk."""
    state.record_success("poll")
    fail(state)
    state.record_alert_sent()
    state.save()

    reloaded = health.Health(state.path, state.rules)
    out = []
    if reloaded.failing_since is None:
        out.append("the open incident did not survive being written and read back")
    if reloaded.failed_runs != 1:
        out.append(f"the failed run count came back as {reloaded.failed_runs}")
    # The throttle has to survive too, or every run alerts again.
    if fail(reloaded):
        out.append("the repeat throttle was lost across a reload, so it alerted again")
    return out


def case_failure_survives_an_alert_that_cannot_send(state) -> list[str]:
    """2026-08-20. The network that broke the run also broke the alert.

    This is the only case that drives `tools.scheduled_run.report_health` rather
    than the Health object underneath it, because the bug it guards was in the
    order of two calls there and was invisible from inside `agent/health.py`.

    The digest failed at the watcher, the alert could not resolve DNS, and the
    save that persists the failure sat downstream of the send. The exception
    skipped it, and the heartbeat was left saying the agent was fine. The email
    is the small loss; `failed_runs` is what the escalation counts, and the
    offline case is the one most likely to fail several runs in a row.
    """
    from agent import notify
    from tools import scheduled_run

    class Boom:
        """A mail server that is unreachable, which is how DNS fails here."""

        def __call__(self, subject, body):
            raise OSError(8, "nodename nor servname provided, or not known")

    sched = SimpleNamespace(health_state_path=state.path, health=state.rules)
    log = SimpleNamespace(line=lambda text="": None)

    original = notify.send
    notify.send = Boom()
    try:
        scheduled_run.report_health(sched, "digest", "watcher", None, log)
    finally:
        notify.send = original

    out = []
    reloaded = health.Health(state.path, state.rules)
    if reloaded.failed_runs != 1:
        out.append(
            f"the failure was not persisted: failed_runs came back as "
            f"{reloaded.failed_runs}, so a send that raised erased it"
        )
    if reloaded.data.get("last_run_ok") is not False:
        out.append("the heartbeat still reads last_run_ok, after a run that failed")
    if reloaded.failing_since is None:
        out.append("no incident was opened, so the escalation has nothing to count from")
    # The mail never left, so nothing may claim he was told. Rule 12, first
    # invariant: a stamp here would buy 12 hours of silence about a dead agent.
    if reloaded.last_alert_at is not None:
        out.append("an alert that raised was stamped as sent")
    return out



def case_the_source_floor_passes_weather_and_catches_an_outage(state) -> list[str]:
    """Where the floor sits, on the four runs that actually happened.

    The two ends are easy and the middle is the whole judgment. 87 of 176
    failing on 2026-08-21 was a network returning part way through a run: real,
    self-correcting, covered three hours later, and alerting on it would train
    him to ignore these. 29 of 176 on 2026-09-03 was not.
    """
    rules = state.rules
    out = []
    for ok, total, want_alert, what in [
        (174, 176, False, "the ordinary two-source failure"),
        (89, 176, False, "the network returning mid-run, 2026-08-21"),
        (29, 176, True, "a run that reached a sixth of the boards, 2026-09-03"),
        (0, 176, True, "the offline laptop, 22 times in a fortnight"),
        (0, 0, False, "a run with no sources configured at all"),
    ]:
        got = health.sources_shortfall(ok, total, rules) is not None
        if got != want_alert:
            verb = "flagged" if got else "passed"
            out.append(f"{what}: {ok} of {total} was {verb}")

    # The off switch has to work, or the only way out of a false alarm at
    # 03:00 is editing Python.
    off = health.HealthRules(min_sources_ok_fraction=0)
    if health.sources_shortfall(0, 176, off) is not None:
        out.append("setting the fraction to 0 did not switch the check off")
    return out


def case_a_run_that_polled_nothing_is_not_a_success(state) -> list[str]:
    """The hole this was all built for. 2026-08-21 to 2026-09-07.

    Every step exits zero, the run stores nothing, and the heartbeat has to
    come out of it saying the agent is broken rather than saying it is fine.
    """
    state.record_success("poll")
    good_at = state.data["last_success_at"]

    report = state.record_degraded("poll", "sources", "polled 0 of 176 sources")

    out = []
    if not report:
        out.append("a run that polled nothing sent no alert")
    if state.data.get("last_run_ok") is not False:
        out.append("the heartbeat still reads last_run_ok after a run that did nothing")
    if state.failing_since is None:
        out.append("no incident was opened, so the escalation has nothing to count")
    # The one that matters most. If this moves, the gap check is fooled too,
    # and a month of degraded runs looks like a month of healthy ones.
    if state.data.get("last_success_at") != good_at:
        out.append("a run that achieved nothing was recorded as the last success")
    if "stopped" in report.body:
        out.append("the alert describes a crash, which is not what happened")
    return out


def case_a_degraded_run_is_throttled_and_then_recovers(state) -> list[str]:
    """It obeys the same repeat window and the same recovery as a crash.

    Worth its own case because `record_degraded` builds a different email, and
    the easy way to write that is to duplicate the bookkeeping and get the
    throttle subtly wrong on one of the two paths.
    """
    out = []
    state.record_degraded("poll", "sources", "polled 0 of 176 sources")
    # The stamp is what silences the next run, and it is written only when the
    # mail actually left. Rule 12's first invariant, and the reason this line is
    # here rather than implied by the call above.
    state.record_alert_sent()
    if state.record_degraded("poll", "sources", "polled 0 of 176 sources"):
        out.append("the second degraded run in an hour alerted again")

    shift(state, 13)
    if not state.record_degraded("poll", "sources", "polled 0 of 176 sources"):
        out.append("a fault still there after 13 hours never repeated")

    recovery = state.record_success("poll")
    if recovery.kind != "recovered":
        out.append(f"recovering from a degraded run reported {recovery.kind!r}")
    if state.failing_since is not None:
        out.append("the incident stayed open after a good run")
    return out


def case_a_crash_and_a_shortfall_are_one_incident(state) -> list[str]:
    """A run cannot be both, and the two must not open two incidents.

    A watcher that crashes never reaches the line that reports its sources, so
    the wrapper has a crash and no report. This checks the count rather than
    the wording: two increments for one bad run would make the escalation
    ladder climb at twice the real rate.
    """
    fail(state)
    out = []
    if state.failed_runs != 1:
        out.append(f"one failed run counted as {state.failed_runs}")
    opened = state.failing_since
    state.record_degraded("poll", "sources", "polled 0 of 176 sources")
    if state.failing_since != opened:
        out.append("a degraded run after a crash restarted the incident clock")
    if state.failed_runs != 2:
        out.append(f"two bad runs counted as {state.failed_runs}")
    return out


def case_the_run_report_survives_a_round_trip(state) -> list[str]:
    """The whole check hangs on one printed string, so test the string.

    This is the fail-open seam. `parse_run_report` returns {} for anything it
    does not recognise and the wrapper then judges the run the old way, on
    whether it finished. That is the right behaviour for a monitor and it also
    means a typo in the format would restore the exact bug this was built to
    fix, silently, with every test still passing. Hence a round trip.
    """
    out = []
    line = health.run_report_line(174, 176, False)
    got = health.parse_run_report(line)
    if got != {"sources_ok": 174, "sources_total": 176, "mail": "ok"}:
        out.append(f"a report did not survive being printed and read: {got!r}")

    failed = health.parse_run_report(health.run_report_line(0, 176, True))
    if failed.get("mail") != "failed":
        out.append("a failed send did not survive the round trip")

    for noise in ["", "Polling 174 companies", "=== end job=poll ok in 201s ==="]:
        if health.parse_run_report(noise):
            out.append(f"an ordinary log line parsed as a run report: {noise!r}")
    # A count that is not a number must not crash the reader and must not be
    # believed either. `degraded_verdict` tests the type rather than the value
    # for this reason, so garbage falls through to the old behaviour of judging
    # a run on whether it finished, which is the safe direction.
    from tools import scheduled_run

    garbage = health.parse_run_report(
        health.RUN_REPORT_PREFIX + "sources_ok=nonsense sources_total=176 ==="
    )
    if isinstance(garbage.get("sources_ok"), int):
        out.append("a non-numeric count was read back as a number")
    sched = SimpleNamespace(health=state.rules)
    if scheduled_run.degraded_verdict(sched, garbage) is not None:
        out.append("an unreadable count was treated as evidence of a fault")
    return out


def case_the_wrapper_records_a_degraded_run(state) -> list[str]:
    """End to end through `tools.scheduled_run`, the way a real run arrives.

    The second case in this file that drives the wrapper rather than the Health
    object, and for the same reason as the first: the decision being tested
    lives in the wiring between them, so testing the pieces separately would
    leave the thing that broke untested.
    """
    from agent import notify
    from tools import scheduled_run

    sched = SimpleNamespace(health_state_path=state.path, health=state.rules)
    log = SimpleNamespace(line=lambda text="": None)
    out = []

    original = notify.send
    notify.send = lambda subject, body: True
    try:
        offline = {"sources_ok": 0, "sources_total": 176, "mail": "ok"}
        verdict = scheduled_run.degraded_verdict(sched, offline)
        if verdict is None:
            out.append("the wrapper called a run that polled 0 of 176 sources fine")
        scheduled_run.report_health(sched, "poll", None, None, log, verdict, offline)

        reloaded = health.Health(state.path, state.rules)
        if reloaded.last_success_at is not None:
            out.append("a run that polled nothing was written down as a success")
        if reloaded.failed_runs != 1:
            out.append(f"the degraded run was not counted: {reloaded.failed_runs}")
        if not reloaded.data.get("last_sources", "").startswith("0/"):
            out.append("the heartbeat does not say what the run actually polled")

        # And the ordinary case still passes, which is the half that stops this
        # from being an alert on every run.
        healthy = {"sources_ok": 174, "sources_total": 176, "mail": "ok"}
        if scheduled_run.degraded_verdict(sched, healthy) is not None:
            out.append("a normal run was called degraded")
        scheduled_run.report_health(
            sched, "poll", None, None, log,
            scheduled_run.degraded_verdict(sched, healthy), healthy,
        )
        recovered = health.Health(state.path, state.rules)
        if recovered.last_success_at is None:
            out.append("a good run after a degraded one recorded no success")
        if recovered.failing_since is not None:
            out.append("a good run did not close the degraded incident")
    finally:
        notify.send = original
    return out


def case_a_send_that_fails_is_a_fault_not_a_crash(state) -> list[str]:
    """The other half of the 2026-09-07 fix, and the risk it introduced.

    `notify.send` stopped raising on an unreachable mail server, which is what
    keeps a mail outage from being recorded as a broken watcher. Done carelessly
    that trades a wrong alert for no alert at all: the run now finishes, so the
    old code would have called it a success. The run report carries mail=failed
    precisely so it does not.
    """
    from agent import notify
    from tools import scheduled_run

    sched = SimpleNamespace(health_state_path=state.path, health=state.rules)
    log = SimpleNamespace(line=lambda text="": None)
    out = []

    fields = {"sources_ok": 176, "sources_total": 176, "mail": "failed"}
    verdict = scheduled_run.degraded_verdict(sched, fields)
    if verdict is None:
        out.append("a run whose email could not be sent was called healthy")
    elif verdict[0] != "email":
        out.append(f"a mail failure was attributed to {verdict[0]!r}")

    original = notify.send
    # The alert about the mail being down goes out by mail, so of course it
    # cannot send either. It must still be recorded, and must not be stamped.
    notify.send = lambda subject, body: False
    try:
        scheduled_run.report_health(sched, "digest", None, None, log, verdict, fields)
    finally:
        notify.send = original

    reloaded = health.Health(state.path, state.rules)
    if reloaded.failed_runs != 1:
        out.append("the mail fault was not recorded")
    if reloaded.last_alert_at is not None:
        out.append("an alert that returned False was stamped as sent")
    if reloaded.last_success_at is not None:
        out.append("a run that could not send its digest counted as a success")
    return out



def case_a_degraded_run_never_reads_as_ok_in_the_log(state) -> list[str]:
    """How the last fortnight was actually diagnosed: by scrolling run.log.

    All 22 of those runs ended with the word "ok" on the line a person greps
    for, which is why nobody noticed for seventeen days. The heartbeat and the
    alert are the machinery; this line is the human interface, and it has to be
    wrong-looking at a glance.
    """
    from tools import scheduled_run

    shortfall = ("sources", "polled 0 of 176 sources, under the floor of 44")
    out = []

    bad = scheduled_run.end_line("poll", 292, None, shortfall, [])
    if " ok " in bad:
        out.append(f"a run that polled nothing still reads as ok: {bad}")
    if "DEGRADED" not in bad:
        out.append(f"a degraded run is not labelled as one: {bad}")
    if "0 of 176" not in bad:
        out.append("the line does not say what the run actually reached")

    good = scheduled_run.end_line("poll", 201, None, None, [])
    if " ok " not in good:
        out.append(f"an ordinary run no longer reads as ok: {good}")

    # A crash still outranks a shortfall, and a stepped-over sync still shows.
    crash = scheduled_run.end_line("digest", 31, "watcher", None, [])
    if "FAILED at watcher" not in crash:
        out.append(f"a crashed run stopped saying so: {crash}")
    stepped = scheduled_run.end_line("poll", 201, None, None, ["calendar"])
    if "stepped over: calendar" not in stepped:
        out.append(f"a stepped-over optional step vanished from the line: {stepped}")
    return out



def case_the_wrapper_reads_the_report_off_a_real_run(state) -> list[str]:
    """The seam between the two processes, tested by running one.

    Found by mutation testing on 2026-09-07: deleting the one line in
    `run_step` that picks the report out of the stream left all twenty other
    cases passing. Everything either side of that line was covered and the line
    itself was not, which is the worst possible place for a gap, because the
    reader fails open on purpose. With no report parsed the wrapper judges a run
    the way it did before any of this existed, on whether it finished, and the
    bug comes back silently.

    So this spawns an actual subprocess that prints an actual report line, the
    way the watcher does, and checks it arrives.
    """
    from tools import scheduled_run

    def fake_step(script: str):
        return SimpleNamespace(
            name="watcher",
            timeout_seconds=30,
            command=lambda python, digest: [sys.executable, "-c", script],
        )

    log = SimpleNamespace(line=lambda text="": None)
    out = []

    offline = health.run_report_line(0, 176, False)
    result = scheduled_run.run_step(
        fake_step(f"print('Polling 174 companies'); print({offline!r})"),
        Path(sys.executable), False, log, 25,
    )
    if not result.ok:
        out.append("a step that exited zero was recorded as failed")
    if result.report.get("sources_ok") != 0 or result.report.get("sources_total") != 176:
        out.append(f"the report did not survive the subprocess: {result.report!r}")

    # A step that prints no report must leave it empty rather than inventing
    # one, so the two syncs cannot be mistaken for a watcher that polled nothing.
    quiet = scheduled_run.run_step(
        fake_step("print('synced 600 records')"), Path(sys.executable), False, log, 25
    )
    if quiet.report:
        out.append(f"a step printing no report produced one: {quiet.report!r}")

    # And a crash still carries its tail, which is what makes the alert useful.
    crashed = scheduled_run.run_step(
        fake_step("import sys; print('IntegrityError'); sys.exit(1)"),
        Path(sys.executable), False, log, 25,
    )
    if crashed.ok:
        out.append("a step that exited 1 was recorded as ok")
    if "IntegrityError" not in crashed.tail:
        out.append("a failing step lost the output the alert quotes")
    return out


CASES = [
    ("the first failure alerts immediately", case_first_failure_alerts),
    ("a persistent fault is throttled, then repeats", case_repeat_is_throttled),
    ("an alert that never sent does not silence the next", case_unsent_alert_is_not_stamped),
    ("the first email he reads never says 'still'", case_first_email_never_says_still),
    ("recovering closes the incident and says so", case_recovery_closes_the_incident),
    ("a healthy run sends nothing", case_healthy_run_is_silent),
    ("a silence no run failed for is reported", case_silent_gap_is_reported),
    ("the normal overnight gap is silent", case_gap_inside_the_window_is_silent),
    ("a recovery from a long outage is one story", case_recovery_wins_over_gap),
    ("the first run ever recorded is silent", case_first_ever_run_is_silent),
    ("a corrupt heartbeat does not crash the run", case_corrupt_heartbeat_does_not_crash),
    ("the incident and its throttle survive a reload", case_state_survives_a_reload),
    (
        "a failure outlives an alert that could not send",
        case_failure_survives_an_alert_that_cannot_send,
    ),
    (
        "the source floor passes weather and catches an outage",
        case_the_source_floor_passes_weather_and_catches_an_outage,
    ),
    (
        "a run that polled nothing is not a success",
        case_a_run_that_polled_nothing_is_not_a_success,
    ),
    (
        "a degraded run is throttled, then recovers",
        case_a_degraded_run_is_throttled_and_then_recovers,
    ),
    ("a crash and a shortfall are one incident", case_a_crash_and_a_shortfall_are_one_incident),
    ("the run report survives a round trip", case_the_run_report_survives_a_round_trip),
    ("the wrapper records a degraded run", case_the_wrapper_records_a_degraded_run),
    ("a send that fails is a fault, not a crash", case_a_send_that_fails_is_a_fault_not_a_crash),
    (
        "a degraded run never reads as ok in the log",
        case_a_degraded_run_never_reads_as_ok_in_the_log,
    ),
    (
        "the wrapper reads the report off a real run",
        case_the_wrapper_reads_the_report_off_a_real_run,
    ),
]


def main() -> int:
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        for i, (label, fn) in enumerate(CASES):
            state = make(Path(tmp), f"case{i}")
            problems = fn(state)
            if problems:
                failures += 1
                print(f"FAIL  {label}")
                for p in problems:
                    print(f"        {p}")
            else:
                print(f"ok    {label}")

    print()
    if failures:
        print(f"{failures} of {len(CASES)} cases failed.")
        return 1
    print(f"All {len(CASES)} cases pass.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
