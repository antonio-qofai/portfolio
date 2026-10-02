"""Tests for gap detection (PRD.md build order item 11).

Why this module gets tests when the readers do not. The readers are verified
by running them against real dependency-agent output and reading what comes
back, which is the right check for code whose job is to survive someone
else's file format. A detector is different in a way that matters: its
healthy output and its broken output look identical. A reader that breaks
returns nothing and somebody notices. A detector that breaks reports no gaps,
which is what a good week looks like, and nobody notices until a month has
been assembled around an input that never arrived.

So the assertions here are mostly about the cases where being wrong is
invisible:

  - Silence is measured from a first sighting, not from the last run. A
    source that answers every day with yesterday's items is the failure this
    module exists to catch, and it is the one a naive implementation passes
    with flying colours. Asserted by building a history where a source
    reports the same item thirty days running.
  - The thresholds come from configuration. A test against the real
    gap_expectations.yaml cannot tell a correct read from a lucky constant,
    so every threshold test writes a temporary config with deliberately
    different numbers and asserts the verdict follows the file.
  - The three silences stay distinguishable. Unconfigured, failed, quiet and
    never-asked are four different findings, and collapsing any pair of them
    is the specific regression that would make the flags stop being worth
    reading.
  - A stale run is one finding rather than one per source. The wrong version
    of this points a reader at four innocent dependency agents, so it is
    asserted on both the count and the suppression list.
  - The anchor is silent before its deadline and speaks after it. A detector
    that flags a not-yet-late briefing on the 2nd of every month trains its
    reader to ignore it by the 7th, when it matters.
  - A volatile source's falling row count is an observation and never a
    finding, because the conference table shrinking is it working correctly.
  - An unreadable config reports rather than returning no gaps. This is the
    invisible-failure case in its purest form.

Run it standalone:

    ../.venv/bin/python3 test_gap_detection.py

It also runs under pytest if that is present, but needs neither pytest nor
any dependency the folder does not already have.
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_AGENT_ROOT = _HERE.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_AGENT_ROOT))

import gap_detection  # noqa: E402
from calendar_model.state_store import record_state  # noqa: E402

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------
# Fixtures built by hand, so a config change cannot make a test pass
# ---------------------------------------------------------------------

def _expectations_yaml(
    *,
    silence_days: int = 5,
    severity: str = "flag",
    run_staleness_days: int = 2,
    anchor_deadline_day: int = 7,
    volatile: bool = False,
    include_anchor: bool = True,
    include_deferred: bool = True,
) -> str:
    """A minimal expectations file with whatever numbers the test wants."""
    lines = [
        "version: 1",
        "defaults:",
        "  max_silence_days: 99",
        "  severity: note",
        f"run_staleness_days: {run_staleness_days}",
        f"anchor_deadline_day: {anchor_deadline_day}",
        "sources:",
        "  - id: alpha-source",
        "    cadence: continuous",
        f"    max_silence_days: {silence_days}",
        f"    severity: {severity}",
    ]
    if volatile:
        lines.append("    volatile: true")
    if include_anchor:
        lines += [
            "  - id: anchor-source",
            "    cadence: monthly_anchor",
            "    severity: tentpole",
        ]
    if include_deferred:
        lines += [
            "  - id: deferred-source",
            "    cadence: deferred",
        ]
    return "\n".join(lines) + "\n"


def _taxonomy_yaml(anchor_source: str = "anchor-source") -> str:
    """Just enough taxonomy for the anchor source to be readable from it."""
    return (
        "version: 1\n"
        "sequencing:\n"
        "  monthly_anchor:\n"
        f"    source: {anchor_source}\n"
        "    expected: first week of month\n"
        "  approved_horizon_days: 14\n"
        "  draft_horizon_days: 30\n"
    )


class Fixture:
    """A temp directory holding a config, a taxonomy and two empty stores."""

    def __init__(self, tmp: Path, **config: object) -> None:
        self.root = tmp
        self.state_root = tmp / "state"
        self.expectations_path = tmp / "gap_expectations.yaml"
        self.taxonomy_path = tmp / "threads.yaml"
        self.expectations_path.write_text(_expectations_yaml(**config), encoding="utf-8")
        self.taxonomy_path.write_text(_taxonomy_yaml(), encoding="utf-8")

    def saw_item(self, source: str, item_id: str, when: datetime, **value: object) -> None:
        """Record that an item was seen on a given day, as the runner would."""
        record_state(
            "ingestion_log",
            [
                {
                    "action": "seen",
                    "entry_key": f"{source}/{item_id}",
                    "value": {"source": source, **value},
                }
            ],
            root=self.state_root,
            now=when,
        )

    def asked(
        self,
        source: str,
        when: datetime,
        *,
        items: int = 1,
        status: str = "ok",
        detail: str | None = None,
    ) -> None:
        """Record that a source was asked on a given day, as the runner would."""
        value = {"source": source, "ok": status == "ok", "status": status, "items": items}
        if detail:
            value["detail"] = detail
        record_state(
            "ingest_runs",
            [{"action": "asked", "entry_key": source, "quantity": items, "value": value}],
            root=self.state_root,
            now=when,
        )

    def detect(self, now: datetime = NOW):
        return gap_detection.detect_gaps(
            expectations_path=self.expectations_path,
            taxonomy_path=self.taxonomy_path,
            state_root=self.state_root,
            now=now,
        )


def _kinds(report, source: str | None = None) -> set:
    return {
        f.kind for f in report.findings if source is None or f.source == source
    }


def _finding(report, kind: str, source: str | None = None):
    for f in report.findings:
        if f.kind == kind and (source is None or f.source == source):
            return f
    return None


# ---------------------------------------------------------------------
# Silence is measured from a first sighting, not from the last run
# ---------------------------------------------------------------------

def test_a_source_repeating_old_items_every_day_is_still_silent():
    """The headline case. Answering is not producing.

    Thirty consecutive days of the runner asking, the source answering, and
    the same single item coming back. Every naive freshness check passes this
    and it is exactly the state worth a flag.
    """
    with tempfile.TemporaryDirectory() as tmp:
        fx = Fixture(Path(tmp), silence_days=5)
        for day in range(30, 0, -1):
            when = NOW - timedelta(days=day)
            fx.saw_item("alpha-source", "post-1", when)
            fx.asked("alpha-source", when, items=1)
        fx.asked("alpha-source", NOW, items=1)

        report = fx.detect()
        finding = _finding(report, gap_detection.KIND_SILENT, "alpha-source")
        assert finding is not None, "a source re-reporting one old item was not flagged"
        assert finding.detail["limit_days"] == 5
        assert finding.detail["age_days"] >= 29
        print("ok  repeated old items still count as silence")


def test_a_genuinely_new_item_clears_the_flag():
    """The other half of the same assertion, so the check is not just always-on."""
    with tempfile.TemporaryDirectory() as tmp:
        fx = Fixture(Path(tmp), silence_days=5)
        fx.saw_item("alpha-source", "post-1", NOW - timedelta(days=30))
        fx.saw_item("alpha-source", "post-2", NOW - timedelta(days=1))
        fx.asked("alpha-source", NOW, items=2)

        report = fx.detect()
        assert gap_detection.KIND_SILENT not in _kinds(report, "alpha-source")
        print("ok  one new item inside the window clears it")


def test_the_silence_threshold_comes_from_the_config_file():
    """Same history, two configs, two verdicts. A constant cannot pass both."""
    for silence_days, expect_flag in ((5, True), (40, False)):
        with tempfile.TemporaryDirectory() as tmp:
            fx = Fixture(Path(tmp), silence_days=silence_days)
            fx.saw_item("alpha-source", "post-1", NOW - timedelta(days=30))
            fx.asked("alpha-source", NOW, items=1)

            report = fx.detect()
            flagged = gap_detection.KIND_SILENT in _kinds(report, "alpha-source")
            assert flagged is expect_flag, (
                f"max_silence_days={silence_days} should "
                f"{'flag' if expect_flag else 'not flag'} a 30-day-old item"
            )
    print("ok  the threshold is read from config, not embedded")


def test_the_severity_comes_from_the_config_file():
    with tempfile.TemporaryDirectory() as tmp:
        fx = Fixture(Path(tmp), silence_days=1, severity="tentpole")
        fx.saw_item("alpha-source", "post-1", NOW - timedelta(days=30))
        fx.asked("alpha-source", NOW, items=1)

        finding = _finding(fx.detect(), gap_detection.KIND_SILENT, "alpha-source")
        assert finding is not None and finding.severity == "tentpole"
        print("ok  severity is read from config too")


# ---------------------------------------------------------------------
# The four silences stay four different findings
# ---------------------------------------------------------------------

def test_an_unconfigured_source_is_its_own_finding_and_outranks_its_config():
    """Config says note; a source that cannot be read at all is still a flag.

    The severity in the file describes patience with a quiet source. A source
    nobody can read is not quiet, it is unread, and the distinction is the
    difference between waiting on Alex and setting a token ourselves.
    """
    with tempfile.TemporaryDirectory() as tmp:
        fx = Fixture(Path(tmp), severity="note", silence_days=1)
        fx.saw_item("alpha-source", "post-1", NOW - timedelta(days=30))
        fx.asked("alpha-source", NOW, items=0, status="unconfigured", detail="no token")

        report = fx.detect()
        finding = _finding(report, gap_detection.KIND_UNCONFIGURED, "alpha-source")
        assert finding is not None, "an unconfigured source was not reported as one"
        assert finding.severity == "flag", "unconfigured was downgraded by the config"
        assert finding.detail.get("reported") == "no token"
        assert gap_detection.KIND_SILENT not in _kinds(report, "alpha-source"), (
            "an unconfigured source was also reported as silent, which blames the "
            "source for our missing credential"
        )
        print("ok  unconfigured is its own finding at its own severity")


def test_a_failed_source_is_not_reported_as_a_quiet_one():
    with tempfile.TemporaryDirectory() as tmp:
        fx = Fixture(Path(tmp), silence_days=1)
        fx.saw_item("alpha-source", "post-1", NOW - timedelta(days=30))
        fx.asked("alpha-source", NOW, items=0, status="failed", detail="timeout")

        report = fx.detect()
        assert _finding(report, gap_detection.KIND_SOURCE_FAILED, "alpha-source") is not None
        assert gap_detection.KIND_SILENT not in _kinds(report, "alpha-source")
        print("ok  a failed source reads as failed, not as quiet")


def test_never_asked_fires_only_when_other_sources_were_asked():
    """A source missing from a run that happened is a real finding.

    Distinct from the case below, where nothing was asked at all. This is the
    "in expectations, not in the runner" mistake, and it is worth naming
    because nothing upstream is wrong.
    """
    with tempfile.TemporaryDirectory() as tmp:
        fx = Fixture(Path(tmp))
        fx.asked("anchor-source", NOW, items=3)

        report = fx.detect()
        assert _finding(report, gap_detection.KIND_NEVER_ASKED, "alpha-source") is not None
        print("ok  a source left out of a real run is reported")


# ---------------------------------------------------------------------
# One dead schedule is one finding
# ---------------------------------------------------------------------

def test_a_stale_run_is_one_finding_and_suppresses_per_source_silence():
    with tempfile.TemporaryDirectory() as tmp:
        fx = Fixture(Path(tmp), silence_days=1, run_staleness_days=2)
        stale = NOW - timedelta(days=9)
        fx.saw_item("alpha-source", "post-1", stale)
        fx.asked("alpha-source", stale, items=1)

        report = fx.detect()
        assert _finding(report, gap_detection.KIND_RUN_STALE) is not None
        assert gap_detection.KIND_SILENT not in _kinds(report), (
            "a stale run reported per-source silence, which blames the sources "
            "for the schedule"
        )
        assert "alpha-source" in report.suppressed
        print("ok  a stale run is the finding, and says which checks it withheld")


def test_an_empty_run_store_reports_once_not_once_per_source():
    """One finding about the run, not one per source that looks unasked.

    The anchor is given this month's item deliberately. The anchor check reads
    the item store and is meant to escape the suppression rule, as the test
    further down asserts, so leaving it unsatisfied here would make this test
    fail for a reason that has nothing to do with what it is checking.
    """
    with tempfile.TemporaryDirectory() as tmp:
        fx = Fixture(Path(tmp), silence_days=1)
        fx.saw_item("alpha-source", "post-1", NOW - timedelta(days=30))
        fx.saw_item("anchor-source", "august", NOW - timedelta(days=20), month="2026-08")

        report = fx.detect()
        assert len(report.findings) == 1, (
            f"expected one finding for an empty run store, got "
            f"{[f.kind for f in report.findings]}"
        )
        assert report.findings[0].kind == gap_detection.KIND_RUN_STALE
        assert "alpha-source" in report.suppressed
        print("ok  no run evidence is one finding about the run")


def test_the_run_staleness_limit_comes_from_the_config_file():
    for limit, expect_stale in ((2, True), (30, False)):
        with tempfile.TemporaryDirectory() as tmp:
            fx = Fixture(Path(tmp), silence_days=99, run_staleness_days=limit)
            when = NOW - timedelta(days=9)
            fx.saw_item("alpha-source", "post-1", when)
            fx.asked("alpha-source", when, items=1)

            report = fx.detect()
            stale = _finding(report, gap_detection.KIND_RUN_STALE) is not None
            assert stale is expect_stale, f"run_staleness_days={limit} was not honoured"
    print("ok  run staleness is read from config")


# ---------------------------------------------------------------------
# The monthly anchor
# ---------------------------------------------------------------------

def test_the_anchor_is_silent_before_its_deadline_day():
    """Nothing is late on the 3rd. Flagging it teaches the reader to ignore it."""
    with tempfile.TemporaryDirectory() as tmp:
        fx = Fixture(Path(tmp), anchor_deadline_day=7)
        early = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
        fx.asked("alpha-source", early, items=1)
        fx.asked("anchor-source", early, items=0)

        report = fx.detect(now=early)
        assert gap_detection.KIND_ANCHOR_MISSING not in _kinds(report)
        print("ok  the anchor is silent inside its window")


def test_a_missing_anchor_is_a_finding_once_the_window_closes():
    with tempfile.TemporaryDirectory() as tmp:
        fx = Fixture(Path(tmp), anchor_deadline_day=7)
        fx.asked("alpha-source", NOW, items=1)
        fx.asked("anchor-source", NOW, items=0)
        # Last month's briefing arrived; this month's did not.
        fx.saw_item("anchor-source", "july", NOW - timedelta(days=40), month="2026-07")

        report = fx.detect()
        finding = _finding(report, gap_detection.KIND_ANCHOR_MISSING, "anchor-source")
        assert finding is not None, "a missing monthly anchor was not reported"
        assert finding.severity == "tentpole"
        assert finding.detail["month"] == "2026-08"
        print("ok  a missing anchor is a tentpole finding after the window")


def test_an_arrived_anchor_is_not_reported():
    with tempfile.TemporaryDirectory() as tmp:
        fx = Fixture(Path(tmp), anchor_deadline_day=7)
        fx.asked("alpha-source", NOW, items=1)
        fx.asked("anchor-source", NOW, items=1)
        fx.saw_item("anchor-source", "august", NOW - timedelta(days=20), month="2026-08")

        report = fx.detect()
        assert gap_detection.KIND_ANCHOR_MISSING not in _kinds(report)
        print("ok  an anchor that arrived is not reported")


def test_the_anchor_deadline_day_comes_from_the_config_file():
    for deadline_day, expect_finding in ((7, True), (28, False)):
        with tempfile.TemporaryDirectory() as tmp:
            fx = Fixture(Path(tmp), anchor_deadline_day=deadline_day)
            fx.asked("alpha-source", NOW, items=1)
            fx.asked("anchor-source", NOW, items=0)

            report = fx.detect()
            found = gap_detection.KIND_ANCHOR_MISSING in _kinds(report)
            assert found is expect_finding, (
                f"anchor_deadline_day={deadline_day} was not honoured on day {NOW.day}"
            )
    print("ok  the anchor deadline is read from config")


def test_a_missing_anchor_is_reported_even_when_the_run_never_happened():
    """The anchor check reads the item store, so it survives a dead schedule.

    Worth asserting because the suppression rule above is deliberately broad,
    and this is the one check that must escape it: a month with no tentpole is
    a fact about the month whether or not today's run fired.
    """
    with tempfile.TemporaryDirectory() as tmp:
        fx = Fixture(Path(tmp), anchor_deadline_day=7)
        report = fx.detect()
        assert _finding(report, gap_detection.KIND_ANCHOR_MISSING, "anchor-source") is not None
        print("ok  the anchor check survives an empty run store")


def test_the_anchor_source_is_read_from_the_taxonomy():
    """Rename the anchor in the taxonomy and the disagreement is visible.

    The taxonomy owns which source is the tentpole, because the calendar is
    built off that same block. This asserts the read happens rather than the
    name being duplicated into the expectations file.
    """
    with tempfile.TemporaryDirectory() as tmp:
        fx = Fixture(Path(tmp))
        fx.taxonomy_path.write_text(_taxonomy_yaml("someone-else"), encoding="utf-8")
        expectations = gap_detection.load_expectations(fx.expectations_path, fx.taxonomy_path)
        assert expectations.anchor_source == "someone-else"
        print("ok  the anchor source comes from the taxonomy")


# ---------------------------------------------------------------------
# Volatile sources
# ---------------------------------------------------------------------

def test_a_falling_row_count_on_a_volatile_source_is_an_observation():
    """The conference case. Losing rows is the source working correctly."""
    with tempfile.TemporaryDirectory() as tmp:
        fx = Fixture(Path(tmp), silence_days=99, volatile=True)
        for index in range(80):
            fx.saw_item("alpha-source", f"row-{index}", NOW - timedelta(days=1))
        fx.asked("alpha-source", NOW, items=61)

        report = fx.detect()
        assert gap_detection.KIND_COUNT_DROP not in _kinds(report), (
            "a volatile source's falling count was reported as a finding"
        )
        kinds = {o.kind for o in report.observations}
        assert gap_detection.KIND_COUNT_DROP in kinds, "the drop was not recorded at all"
        observation = next(
            o for o in report.observations if o.kind == gap_detection.KIND_COUNT_DROP
        )
        assert observation.detail == {"returned": 61, "ever_recorded": 80}
        print("ok  a volatile source's drop is an observation with the numbers")


def test_a_non_volatile_source_gets_no_count_observation():
    with tempfile.TemporaryDirectory() as tmp:
        fx = Fixture(Path(tmp), silence_days=99, volatile=False)
        for index in range(80):
            fx.saw_item("alpha-source", f"row-{index}", NOW - timedelta(days=1))
        fx.asked("alpha-source", NOW, items=61)

        report = fx.detect()
        assert not report.observations
        print("ok  the volatile rule applies only where it is configured")


# ---------------------------------------------------------------------
# Deferred sources, and failing loudly
# ---------------------------------------------------------------------

def test_deferred_sources_are_listed_and_never_flagged():
    with tempfile.TemporaryDirectory() as tmp:
        fx = Fixture(Path(tmp))
        fx.asked("alpha-source", NOW, items=1)
        fx.asked("anchor-source", NOW, items=1)
        fx.saw_item("alpha-source", "post-1", NOW)
        fx.saw_item("anchor-source", "august", NOW, month="2026-08")

        report = fx.detect()
        assert "deferred-source" in report.sources_deferred
        assert "deferred-source" not in report.sources_checked
        assert all(f.source != "deferred-source" for f in report.findings)
        print("ok  a deferred source is listed, not flagged")


def test_an_unreadable_config_reports_instead_of_returning_no_gaps():
    """The invisible failure, asserted directly.

    A detector that cannot read its own config and returns an empty finding
    list is indistinguishable from a healthy week, which is the one outcome
    this module must never produce.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        report = gap_detection.detect_gaps(
            expectations_path=root / "does-not-exist.yaml",
            taxonomy_path=root / "also-missing.yaml",
            state_root=root / "state",
            now=NOW,
        )
        assert report.findings, "a missing config produced no findings at all"
        assert report.ok is False
        print("ok  an unreadable config is reported, not silently passed")


def test_a_config_with_no_usable_sources_reports_too():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / "empty.yaml"
        path.write_text("version: 1\nsources: []\n", encoding="utf-8")
        report = gap_detection.detect_gaps(
            expectations_path=path,
            taxonomy_path=root / "missing.yaml",
            state_root=root / "state",
            now=NOW,
        )
        assert report.findings and report.ok is False
        print("ok  an empty source list is reported, not silently passed")


# ---------------------------------------------------------------------
# Persisting flags, and the CLI contract
# ---------------------------------------------------------------------

def test_recorded_flags_are_keyed_by_kind_and_source():
    """A flag has to outlive the run that raised it, and stay addressable."""
    with tempfile.TemporaryDirectory() as tmp:
        fx = Fixture(Path(tmp), silence_days=1)
        fx.saw_item("alpha-source", "post-1", NOW - timedelta(days=30))
        fx.asked("alpha-source", NOW, items=1)
        fx.asked("anchor-source", NOW, items=0)

        report = fx.detect()
        written = gap_detection.record_gaps(report, state_root=fx.state_root, now=NOW)
        assert written["ok"] and written["stored"] == len(report.findings)

        from calendar_model.state_store import read_state

        rows = read_state("gaps", root=fx.state_root, latest_per_key=True)
        keys = {r.entry_key for r in rows}
        assert f"{gap_detection.KIND_SILENT}/alpha-source" in keys
        assert all(isinstance(r.value.get("summary"), str) for r in rows)
        print("ok  findings persist, keyed by kind and source")


def test_recording_a_clean_report_writes_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        fx = Fixture(Path(tmp), silence_days=99)
        fx.saw_item("alpha-source", "post-1", NOW)
        fx.saw_item("anchor-source", "august", NOW, month="2026-08")
        fx.asked("alpha-source", NOW, items=1)
        fx.asked("anchor-source", NOW, items=1)

        report = fx.detect()
        assert not report.findings, [f.kind for f in report.findings]
        written = gap_detection.record_gaps(report, state_root=fx.state_root, now=NOW)
        assert written["stored"] == 0 and written["ok"]
        assert not (fx.state_root / "gaps.jsonl").exists()
        print("ok  a clean report leaves no flag behind")


def test_the_cli_exits_zero_unless_fail_on_is_asked_for():
    """Installing this must not turn the daily run red on its own."""
    with tempfile.TemporaryDirectory() as tmp:
        fx = Fixture(Path(tmp), silence_days=1)
        fx.saw_item("alpha-source", "post-1", NOW - timedelta(days=30))
        fx.asked("alpha-source", NOW, items=1)
        fx.asked("anchor-source", NOW, items=1)
        fx.saw_item("anchor-source", "august", NOW, month="2026-08")

        base = [
            "--expectations", str(fx.expectations_path),
            "--taxonomy", str(fx.taxonomy_path),
            "--state-root", str(fx.state_root),
            "--as-of", NOW.isoformat(),
        ]
        assert gap_detection.main(base) == 0, "reporting a gap must not fail by default"
        assert gap_detection.main(base + ["--fail-on", "flag"]) == 1
        assert gap_detection.main(base + ["--fail-on", "tentpole"]) == 0, (
            "--fail-on tentpole fired on a flag-severity finding"
        )
        print("ok  exit codes are opt-in and respect severity")


def _tests():
    return [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]


def main() -> int:
    failures = 0
    for test in _tests():
        try:
            test()
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {test.__name__}: {exc}")
    total = len(_tests())
    print(f"\n{total - failures}/{total} passed")
    return 1 if failures else 0



def test_a_refused_source_reddens_the_build_and_an_empty_one_does_not():
    """The chain item 2 exists to close, end to end.

    Before 2026-09-17 a reader that was refused, unreachable, or pointed at a
    folder that is not there all returned an empty list, and the runner
    recorded all of them as a healthy source with zero items. The only thing
    that made it out was an unset token, and only because the runner checked
    the environment before calling.

    Now the reader says which it was, the runner records it, this module reads
    it back, and the daily gate acts on it. A source that answered and had
    nothing stays green, because that is a quiet week and not a fault.
    """
    gate = ["--fail-on-kind", *gap_detection.PIPELINE_KINDS]

    for status, should_redden in (
        ("rejected", True),
        ("unreachable", True),
        ("missing", True),
        ("failed", True),
        ("unconfigured", True),
        ("empty", False),
        ("ok", False),
    ):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime.now(timezone.utc)
            for source in ("content-atomizer", "value-creation-briefing",
                           "conference-event-intelligence", "market-scan"):
                value = {"source": source, "ok": status in ("ok", "empty"),
                         "status": status if source == "conference-event-intelligence" else "ok",
                         "items": 0 if source == "conference-event-intelligence" else 5,
                         "mode": "ingest"}
                record_state(
                    "ingest_runs",
                    [{"action": "asked", "entry_key": source,
                      "event_at": now.isoformat(), "value": value}],
                    root=root,
                )
            code = gap_detection.main(["--state-root", str(root), *gate])
            assert code == (1 if should_redden else 0), (
                f"status {status!r} gave exit {code}; expected "
                f"{'red' if should_redden else 'green'}"
            )
    print("ok  a source we could not read reddens the build; one that is merely empty does not")


def test_the_ci_gate_keys_on_kind_rather_than_severity():
    """The distinction the daily workflow depends on, 2026-09-17.

    Severity says how much a finding matters to the calendar. Kind says
    whether anyone reading a build log can do something about it. They are
    not the same axis, and conflating them is what makes a red build
    meaningless: a missing monthly anchor is tentpole-serious and no amount
    of build redness produces the missing briefing.

    So the workflow gates on the four kinds that mean this agent stopped
    working, and a thin corpus stays green.
    """
    assert set(gap_detection.PIPELINE_KINDS) == {
        "run_stale", "never_asked", "unconfigured", "source_failed"
    }, gap_detection.PIPELINE_KINDS

    content_kinds = {
        gap_detection.KIND_SILENT,
        gap_detection.KIND_ANCHOR_MISSING,
        gap_detection.KIND_COUNT_DROP,
    }
    assert not content_kinds & set(gap_detection.PIPELINE_KINDS), (
        "a content-supply finding is in the set that reddens the daily build"
    )

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        gate = ["--fail-on-kind", *gap_detection.PIPELINE_KINDS]

        # Nothing recorded at all is the machinery being broken, and must fail.
        empty = gap_detection.main(["--state-root", str(root), *gate])
        assert empty == 1, "a state root with no run recorded did not redden the build"

        # The same state with no gate passes, so the gate is what decides.
        assert gap_detection.main(["--state-root", str(root)]) == 0, (
            "gap detection failed without being asked to, which would redden every run"
        )
    print("ok  the daily build reddens on a broken pipeline, not on a thin corpus")

if __name__ == "__main__":
    raise SystemExit(main())
