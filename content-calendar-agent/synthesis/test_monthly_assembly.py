"""Tests for the monthly assembly schedule and the never-overwrite rule.

What matters here is not the model call, which assemble.py's own suite
covers, but the two things a schedule can get wrong unattended:

  - When it runs. The due day is derived from the approved horizon in
    threads.yaml, so a test has to show that changing the horizon moves the
    date, or a hardcoded day would pass.
  - What it refuses to write over. A standing window may carry approvals
    keyed by positional slot_id, so an overwrite is unrecoverable. Every
    refusal is asserted by the bytes on disk being unchanged and by the
    model-calling path never being reached.

No test here makes a network call. `assemble.main` is replaced with a
recorder wherever it could otherwise be reached.
"""

from __future__ import annotations

import sys
import tempfile
from datetime import date
from pathlib import Path

import yaml

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "calendar_model"))

import assemble  # noqa: E402
import monthly_assembly as monthly  # noqa: E402
import slots as slots_mod  # noqa: E402

_TAXONOMY = {
    "threads": [{"id": "alpha-thread", "name": "Alpha"}],
    "forms": [{"id": "historical-analogy", "description": "An analogy"}],
    "lenses": [
        {"id": "jordan", "name": "Private equity", "calendar": True, "v1": True},
        {"id": "casey", "name": "Operator", "calendar": True, "v1": True},
    ],
    "sequencing": {"approved_horizon_days": 14, "draft_horizon_days": 30},
}

_SCHEDULE = {"day_of_month": 15, "run_store": "assembly_runs"}


def _write(tmp: Path, *, horizon: int = 14, schedule: dict | None = None) -> tuple[str, str]:
    taxonomy = dict(_TAXONOMY, sequencing=dict(_TAXONOMY["sequencing"],
                                               approved_horizon_days=horizon))
    threads = tmp / "threads.yaml"
    threads.write_text(yaml.safe_dump(taxonomy))
    criteria = tmp / "criteria.yaml"
    criteria.write_text(yaml.safe_dump({"assembly_schedule": schedule or _SCHEDULE}))
    return str(threads), str(criteria)


class _Recorder:
    """Stands in for assemble.main and records every call it would have made."""

    def __init__(self, code: int = 0, write: bool = True):
        self.calls: list[list[str]] = []
        self.code = code
        self.write = write

    def __call__(self, argv):
        self.calls.append(list(argv))
        if self.write and self.code == 0:
            out = Path(argv[argv.index("--out") + 1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text("{}\n")
        return self.code


def _run(recorder: _Recorder, argv: list[str]) -> int:
    original = monthly.assemble_mod.main
    monthly.assemble_mod.main = recorder
    try:
        return monthly.main(argv)
    finally:
        monthly.assemble_mod.main = original


def test_the_due_day_is_the_configured_day_of_the_month_before():
    for start, due in ((date(2026, 11, 1), date(2026, 10, 15)),
                       (date(2027, 1, 1), date(2026, 12, 15)),
                       (date(2027, 3, 1), date(2027, 2, 15))):
        assert monthly.due_date(_SCHEDULE, start) == due, start
    print("ok  assembly is the 15th of the month before, in every month including February")


def test_the_day_comes_from_config_rather_than_from_a_constant():
    """A hardcoded 15 would pass the test above. It cannot pass this one."""
    assert monthly.due_date(dict(_SCHEDULE, day_of_month=3), date(2026, 11, 1)) == date(2026, 10, 3)
    print("ok  changing the configured day moves the assembly day")


def test_the_approval_horizon_does_not_reach_the_schedule():
    """It used to derive the date, which put a rejected deadline back in charge."""
    source = (_HERE / "monthly_assembly.py").read_text(encoding="utf-8")
    for banned in ("approved_horizon_days", "margin_days_beyond_approved_horizon"):
        assert banned not in source, f"{banned} is back in the assembly schedule"
    policy = yaml.safe_load((_HERE / "sequencing_criteria.yaml").read_text(encoding="utf-8"))
    assert set(policy["assembly_schedule"]) == {"day_of_month", "run_store"}, \
        "the assembly schedule grew a key back"
    print("ok  the approval horizon has nothing to do with when the agent spends money")


def test_an_unusable_schedule_is_refused():
    assert monthly.schedule_policy({})[1], "a missing block was accepted"
    assert monthly.schedule_policy({"assembly_schedule": dict(_SCHEDULE, day_of_month=31)})[1], \
        "a day February does not have was accepted"
    assert monthly.schedule_policy({"assembly_schedule": dict(_SCHEDULE, day_of_month=None)})[1], \
        "a schedule naming no day was accepted"
    assert not monthly.schedule_policy({"assembly_schedule": _SCHEDULE})[1]
    print("ok  a schedule that cannot produce a date is refused rather than guessed")


def test_the_real_schedule_is_usable():
    criteria = assemble.load_criteria(assemble.DEFAULT_CRITERIA_PATH)
    _, problems = monthly.schedule_policy(criteria)
    assert not problems, f"the checked-in assembly schedule is unusable: {problems}"
    print("ok  the checked-in assembly_schedule block is usable")


def test_a_day_before_the_due_day_does_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        threads, criteria = _write(tmp)
        recorder = _Recorder()
        code = _run(recorder, ["--threads", threads, "--criteria", criteria,
                               "--state-root", str(tmp / "state"), "--today", "2026-09-14"])
        assert code == 0
        assert not recorder.calls, "assembly was attempted before it was due"
        assert not (tmp / "state").exists(), "a not-due day wrote something"
    print("ok  a not-due day makes no call and writes nothing, so it makes no commit")


def test_the_due_day_assembles_every_lens_into_its_own_file():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        threads, criteria = _write(tmp)
        recorder = _Recorder()
        code = _run(recorder, ["--threads", threads, "--criteria", criteria,
                               "--state-root", str(tmp / "state"), "--today", "2026-09-15"])
        assert code == 0
        lenses = [c[c.index("--lens") + 1] for c in recorder.calls]
        assert lenses == ["jordan", "casey"], f"lenses assembled: {lenses}"
        for call in recorder.calls:
            assert call[call.index("--start") + 1] == "2026-10-01"
        for lens in ("jordan", "casey"):
            assert (tmp / "state" / f"window-{lens}-2026-10.json").exists()
        assert (tmp / "state" / "assembly_runs.jsonl").exists(), "the attempt was not recorded"
    print("ok  the due day assembles each live lens from threads.yaml and records the run")


def test_an_existing_window_is_never_written_over():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        threads, criteria = _write(tmp)
        state = tmp / "state"
        state.mkdir()
        standing = state / "window-jordan-2026-10.json"
        standing.write_text('{"approved": "keep me"}\n')
        before = standing.read_bytes()
        recorder = _Recorder()
        code = _run(recorder, ["--threads", threads, "--criteria", criteria,
                               "--state-root", str(state), "--today", "2026-09-20"])
        assert standing.read_bytes() == before, "a standing window was overwritten"
        lenses = [c[c.index("--lens") + 1] for c in recorder.calls]
        assert lenses == ["casey"], f"jordan should have been refused, got calls for {lenses}"
        assert code == 0, "an already-assembled month is the steady state, not a failure"

        explicit = _run(_Recorder(), ["--threads", threads, "--criteria", criteria,
                                      "--state-root", str(state), "--today", "2026-09-20",
                                      "--month", "2026-10"])
        assert explicit == 1, "asking for a month that exists should be refused out loud"
        assert standing.read_bytes() == before
    print("ok  an existing window is refused before any call, and an explicit ask for it is red")


def test_a_pending_rejected_window_stops_the_spend():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        threads, criteria = _write(tmp)
        state = tmp / "state"
        state.mkdir()
        (state / "window-jordan-2026-10.rejected.json").write_text("{}\n")
        recorder = _Recorder()
        code = _run(recorder, ["--threads", threads, "--criteria", criteria,
                               "--state-root", str(state), "--today", "2026-09-16"])
        assert code == 1, "a rejected window waiting should red the run"
        lenses = [c[c.index("--lens") + 1] for c in recorder.calls]
        assert "jordan" not in lenses, "paid for another call while a rejected window waited"
        assert lenses == ["casey"], "one lens refusing stopped the others"
    print("ok  a rejected window waiting stops that lens paying again, and not the others")


def test_a_month_in_the_past_is_refused():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        threads, criteria = _write(tmp)
        recorder = _Recorder()
        code = _run(recorder, ["--threads", threads, "--criteria", criteria,
                               "--state-root", str(tmp / "state"), "--today", "2026-10-05",
                               "--month", "2026-10"])
        assert code == 1 and not recorder.calls
    print("ok  a window that would open on or before today is refused")


def test_assemble_itself_refuses_an_existing_out_before_any_work():
    """The protection must not depend on the scheduler remembering to check."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "window-jordan-2026-10.json"
        out.write_text('{"approved": "keep me"}\n')
        before = out.read_bytes()
        code = assemble.main(["--criteria", str(Path(tmp) / "no-such-policy.yaml"),
                              "--out", str(out)])
        assert code == 1
        assert out.read_bytes() == before, "assemble.main wrote over an existing window"
    print("ok  assemble.py refuses an existing --out before reading any policy")


def test_the_window_writer_is_exclusive():
    window = slots_mod.CalendarWindow(window_id="jordan-2026-10-01", lens="jordan",
                                      start_date="2026-10-01", end_date="2026-10-30")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "window-jordan-2026-10.json"
        assemble.write_new_window(path, window)
        try:
            assemble.write_new_window(path, window)
        except FileExistsError:
            pass
        else:
            raise AssertionError("a second write to the same window succeeded")
    print("ok  the standing-window writer refuses a file that appeared mid-run")


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


if __name__ == "__main__":
    raise SystemExit(main())
