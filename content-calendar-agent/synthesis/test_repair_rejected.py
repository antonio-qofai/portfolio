"""Tests for the rejected-window repair pass.

No rejected window has ever existed on disk, so every fixture here is the
real `state/window-jordan-2026-10.json` broken one code at a time and saved
under the rejected name in a temporary folder. That gives the negative cases
too: a window broken with a judgment error must come back refused, with
nothing written.

The property that matters most is not that repairs work. It is that the
renumbering cannot reach a standing window, so the refusals are asserted by
the bytes on disk being unchanged, not by an exit code alone.
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_AGENT_ROOT = _HERE.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_AGENT_ROOT / "calendar_model"))

import repair_rejected as repair  # noqa: E402
import slots as slots_mod  # noqa: E402
import state_store  # noqa: E402

SOURCE = _AGENT_ROOT / "state" / "window-jordan-2026-10.json"
REJECTED = "window-jordan-2026-10.rejected.json"
TODAY = date(2026, 9, 18)


def _fixture(tmp: Path, breaker) -> Path:
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    breaker(data)
    path = tmp / REJECTED
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def _repaired(breaker, expected_code: str):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        path = _fixture(tmp, breaker)
        before = slots_mod.read_window(path)
        issues = slots_mod.errors(slots_mod.validate_window(
            before, slots_mod.load_taxonomy(), today=TODAY))
        assert issues, f"the fixture for {expected_code} is not actually broken"
        result = repair.repair_rejected_file(path, today=TODAY)
        assert not result.remaining, f"{expected_code} left {result.remaining}"
        assert result.written and result.written.name == "window-jordan-2026-10.json"
        written = json.loads(result.written.read_text(encoding="utf-8"))
        codes = {r["code"] for r in written["repairs"]}
        assert expected_code in codes, f"{expected_code} was repaired but not recorded: {codes}"
        assert written["repaired_from"] == REJECTED
        assert path.exists(), "the rejected file is the evidence and must be kept"
        return written


def _slot(data, n=0):
    return data["slots"][n]


def test_a_duplicate_slot_id_is_renumbered_and_recorded():
    def breaker(d):
        _slot(d, 1)["slot_id"] = _slot(d, 0)["slot_id"]
    written = _repaired(breaker, "duplicate-slot-id")
    ids = [s["slot_id"] for s in written["slots"]]
    assert len(set(ids)) == len(ids), ids
    assert ids == [f"jordan-2026-10-01-{n:02d}" for n in range(1, len(ids) + 1)], ids
    print("ok  duplicate-slot-id: renumbered by position, and recorded")


def test_a_missing_slot_id_is_renumbered_and_recorded():
    _repaired(lambda d: _slot(d, 3).update(slot_id=""), "missing-slot-id")
    print("ok  missing-slot-id: regenerated from position, and recorded")


def test_a_missing_window_id_is_derived():
    written = _repaired(lambda d: d.update(window_id=""), "missing-window-id")
    assert written["window_id"] == "jordan-2026-10-01"
    assert all(s["slot_id"].startswith("jordan-2026-10-01-") for s in written["slots"])
    print("ok  missing-window-id: derived from lens and start, and the slots follow it")


def test_an_inverted_range_takes_its_end_from_the_start():
    written = _repaired(lambda d: d.update(end_date="2026-09-01"), "inverted-range")
    assert written["end_date"] == "2026-10-31", written["end_date"]
    print("ok  inverted-range: the end comes from the start, and is recorded")


def test_an_unknown_lens_is_taken_from_the_file_name():
    written = _repaired(lambda d: d.update(lens="jordann"), "unknown-lens")
    assert written["lens"] == "jordan"
    print("ok  unknown-lens: the lens the file is named for, and recorded")


def test_unknown_threads_and_forms_are_dropped():
    def breaker(d):
        _slot(d)["threads"] = list(_slot(d)["threads"]) + ["not-a-thread"]
        _slot(d)["forms"] = list(_slot(d)["forms"]) + ["not-a-form"]
    written = _repaired(breaker, "unknown-thread")
    assert "unknown-form" in {r["code"] for r in written["repairs"]}
    assert "not-a-thread" not in written["slots"][0]["threads"]
    print("ok  unknown-thread and unknown-form: the unknown id is dropped, the slot kept")


def test_bad_and_self_references_are_dropped_and_the_slot_kept():
    def breaker(d):
        _slot(d)["responds_to"] = ["not a ref", _slot(d)["item_ref"]]
    written = _repaired(breaker, "bad-responds-to-ref")
    assert "responds-to-self" in {r["code"] for r in written["repairs"]}
    assert written["slots"][0]["responds_to"] == []
    print("ok  bad-responds-to-ref and responds-to-self: dropped, slot kept")


def test_an_approval_state_in_a_rejected_window_goes_back_to_draft():
    written = _repaired(lambda d: _slot(d).update(approval="maybe"), "unknown-approval-state")
    assert all(s["approval"] == "draft" for s in written["slots"])
    print("ok  a rejected window comes out all draft, since nobody has decided on it")


def _refused_for_judgment(breaker, code: str):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        path = _fixture(tmp, breaker)
        result = repair.repair_rejected_file(path, today=TODAY)
        assert result.written is None, f"{code} was repaired by guessing"
        assert any(i.code == code for i in result.remaining), result.remaining
        assert not (tmp / "window-jordan-2026-10.json").exists(), "a half-repair was written"


def test_a_bad_slot_date_is_refused_not_guessed():
    _refused_for_judgment(lambda d: _slot(d).update(date="someday"), "bad-date")
    print("ok  bad-date: refused, nothing written, a re-run is needed")


def test_a_date_outside_the_window_is_refused_not_moved():
    _refused_for_judgment(lambda d: _slot(d).update(date="2026-11-15"), "date-outside-window")
    print("ok  date-outside-window: refused, nothing written")


def test_one_judgment_error_blocks_every_repair():
    def breaker(d):
        _slot(d, 1)["slot_id"] = _slot(d, 0)["slot_id"]
        _slot(d, 2)["date"] = "2026-12-01"
    _refused_for_judgment(breaker, "date-outside-window")
    print("ok  a mechanical fault beside a judgment one writes nothing, not a half-repair")


def test_a_standing_window_cannot_be_repaired():
    """The renumbering must be unable to reach a window that may carry approvals."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for name in ("window-jordan-2026-10.json", "window-jordan-2026-10.proposed.json",
                     "rejected.json", "window-jordan-2026-10.rejected.json.bak"):
            path = tmp / name
            path.write_bytes(SOURCE.read_bytes())
            before = path.read_bytes()
            try:
                repair.repair_rejected_file(path, today=TODAY)
            except repair.RepairRefused:
                pass
            else:
                raise AssertionError(f"{name} was accepted for repair")
            assert path.read_bytes() == before, f"{name} was modified"
        assert repair.main([str(tmp / "window-jordan-2026-10.json")]) == 1
    print("ok  anything but a .rejected.json is refused, and left byte-for-byte alone")


def test_an_existing_standing_window_is_never_overwritten():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        path = _fixture(tmp, lambda d: _slot(d, 1).update(slot_id=_slot(d, 0)["slot_id"]))
        standing = tmp / "window-jordan-2026-10.json"
        standing.write_text('{"approved": "keep me"}\n')
        before = standing.read_bytes()
        try:
            repair.repair_rejected_file(path, today=TODAY)
        except repair.RepairRefused:
            pass
        else:
            raise AssertionError("a repair was written over a standing window")
        assert standing.read_bytes() == before
    print("ok  a standing window at the target is refused, not overwritten")


def test_old_decisions_on_the_same_ids_refuse_the_repair():
    """A deleted window leaves its approvals keyed by ids the repair would reuse."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        path = _fixture(tmp, lambda d: _slot(d, 1).update(slot_id=_slot(d, 0)["slot_id"]))
        state_store.record_state("approvals", [{
            "action": "approved", "entry_key": "jordan-2026-10-01-02",
            "value": {"state": "approved", "by": "someone"},
        }], root=tmp)
        try:
            repair.repair_rejected_file(path, today=TODAY)
        except repair.RepairRefused as exc:
            assert "jordan-2026-10-01-02" in str(exc), exc
        else:
            raise AssertionError("renumbering handed an old approval to a new post")
        assert not (tmp / "window-jordan-2026-10.json").exists()
    print("ok  a standing decision on an id the repair would produce refuses the repair")


def test_a_dry_run_reports_and_writes_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        path = _fixture(tmp, lambda d: d.update(lens="jordann"))
        result = repair.repair_rejected_file(path, today=TODAY, dry_run=True)
        assert result.repairs and result.written is None
        assert not (tmp / "window-jordan-2026-10.json").exists()
    print("ok  a dry run lists the repairs and writes nothing")


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
