"""Tests for the calendar data model and the daily state store.

This folder had no test convention before this module, and most of it does
not need one: the ingestion readers are verified by running them against
real dependency-agent output and reading what comes back, which is the right
check for code whose job is to survive someone else's file format. A data
model is different. Everything downstream (synthesis, scoring, gap
detection, the approval queue) reads and writes through this schema, so a
silent change in what a round trip preserves would be discovered late and in
the wrong place.

What is worth asserting here, and why each one earns its line:

  - Round trips are lossless, including keys this schema does not know
    about, because a human will hand-edit a window file and their note must
    not vanish on the next write.
  - The horizons come from configuration. A test with the real threads.yaml
    cannot tell a correct read from a lucky constant, so the horizon tests
    run against a temporary taxonomy with deliberately different numbers.
  - The thread and form vocabularies come from configuration too, checked
    the same way: rename every thread in a temporary taxonomy and the same
    window must stop validating.
  - The store persists across processes, which is the entire reason it
    exists. Asserted by reading it back from a subprocess, not from this
    one, since an in-process read would pass even against a dictionary.
  - The store has no update call, asserted directly, because "append-only"
    is a property that erodes the first time someone adds a convenience.

Run it standalone:

    ../.venv/bin/python3 test_calendar_model.py

It also runs under pytest if that is present, but it needs neither pytest
nor any dependency the folder does not already have.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import example_window  # noqa: E402
import slots  # noqa: E402
import state_store  # noqa: E402

TODAY = date(2026, 8, 18)


# ---------------------------------------------------------------------
# Fixtures built by hand, so a config change cannot make a test pass
# ---------------------------------------------------------------------

def _taxonomy_yaml(approved: int, window: int, thread_prefix: str = "") -> str:
    """A minimal taxonomy with whatever horizons and ids the test wants."""
    threads = [
        f"{thread_prefix}decision-architecture",
        f"{thread_prefix}exit-preparation",
    ]
    lines = [
        "version: 1",
        "sequencing:",
        f"  approved_horizon_days: {approved}",
        f"  draft_horizon_days: {window}",
        "lenses:",
        "  - id: jordan",
        "    calendar: true",
        "    v1: true",
        "  - id: blake",
        "    calendar: true",
        "  - id: all",
        "    calendar: false",
        "threads:",
    ]
    for thread in threads:
        lines.append(f"  - id: {thread}")
        lines.append("    lens: jordan")
    lines.append("forms:")
    lines.append("  - id: historical-analogy")
    return "\n".join(lines) + "\n"


def _write_taxonomy(tmp: Path, approved: int, window: int, thread_prefix: str = "") -> slots.Taxonomy:
    tmp.mkdir(parents=True, exist_ok=True)
    path = tmp / "threads.yaml"
    path.write_text(_taxonomy_yaml(approved, window, thread_prefix), encoding="utf-8")
    return slots.load_taxonomy(path)


def _slot(day: date, **overrides) -> slots.Slot:
    values = dict(
        slot_id=f"s-{day.isoformat()}",
        date=day.isoformat(),
        item_ref="content-atomizer/anchor-03-the-electricity-post",
        lens="jordan",
        threads=["decision-architecture"],
        forms=["historical-analogy"],
        approval=slots.APPROVED,
        responds_to=[],
        summary="what the post argues, per the test",
        rationale="because the test says so",
    )
    values.update(overrides)
    return slots.Slot(**values)


def _window(taxonomy: slots.Taxonomy, slot_list) -> slots.CalendarWindow:
    window = slots.new_window(
        "w-1", "jordan", TODAY, taxonomy, arc="an arc", generated_at="2026-08-18T00:00:00+00:00"
    )
    window.slots = list(slot_list)
    return window


# ---------------------------------------------------------------------
# Schema: round trips
# ---------------------------------------------------------------------

def test_round_trip_is_byte_identical():
    """Write, read, write again produces the same bytes. The headline claim."""
    taxonomy = slots.load_taxonomy()
    window = example_window.build(taxonomy, TODAY)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "window.json"
        slots.write_window(path, window)
        first = path.read_bytes()
        reread = slots.read_window(path)
        assert reread is not None
        slots.write_window(path, reread)
        assert path.read_bytes() == first
        assert len(reread.slots) == len(window.slots)


def test_round_trip_preserves_every_field():
    taxonomy = slots.load_taxonomy()
    window = example_window.build(taxonomy, TODAY)
    reread = slots.loads(slots.dumps(window))
    assert reread.to_dict() == window.to_dict()
    original = {s.slot_id: s for s in window.slots}
    for slot in reread.slots:
        before = original[slot.slot_id]
        assert slot.date == before.date
        assert slot.item_ref == before.item_ref
        assert slot.threads == before.threads
        assert slot.forms == before.forms
        assert slot.lens == before.lens
        assert slot.approval == before.approval
        assert slot.responds_to == before.responds_to
        assert slot.rationale == before.rationale


def test_round_trip_preserves_unknown_keys():
    """A human's hand-added note must survive the next write."""
    with tempfile.TemporaryDirectory() as tmp:
        taxonomy = _write_taxonomy(Path(tmp), 14, 30)
        window = _window(taxonomy, [_slot(TODAY + timedelta(days=1))])
        raw = json.loads(slots.dumps(window))
        raw["reviewed_by"] = "a human"
        raw["slots"][0]["note_from_jordan"] = "move this after the briefing"
        recovered = slots.CalendarWindow.from_dict(raw)
        assert recovered.extra["reviewed_by"] == "a human"
        assert recovered.slots[0].extra["note_from_jordan"] == "move this after the briefing"
        assert json.loads(slots.dumps(recovered)) == raw
        # And it is stable, not merely present once.
        assert slots.dumps(slots.loads(slots.dumps(recovered))) == slots.dumps(recovered)


def test_missing_required_field_is_reported_not_raised_at_the_file_level():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "broken.json"
        path.write_text('{"window_id": "w", "lens": "jordan"}', encoding="utf-8")
        assert slots.read_window(path) is None
        assert slots.read_window(Path(tmp) / "absent.json") is None


# ---------------------------------------------------------------------
# Schema: the horizons and the vocabularies come from configuration
# ---------------------------------------------------------------------

def test_horizons_are_read_from_config_not_stated_in_python():
    with tempfile.TemporaryDirectory() as tmp:
        taxonomy = _write_taxonomy(Path(tmp), 3, 9)
        assert taxonomy.approved_horizon_days == 3
        assert taxonomy.draft_horizon_days == 9
        # The draft horizon's one remaining reader: a slot past it is a
        # window that runs long, which is a planning question and a warning.
        window = _window(taxonomy, [_slot(TODAY + timedelta(days=20))])
        codes = [i.code for i in slots.check_horizons(window, taxonomy, TODAY)]
        assert codes == ["beyond-window"], codes
        # The window's length is its calendar month, not either horizon.
        start, end = slots.window_bounds(taxonomy, TODAY)
        assert (start, end) == (TODAY.isoformat(), "2026-08-31")

    with tempfile.TemporaryDirectory() as tmp:
        taxonomy = _write_taxonomy(Path(tmp), 20, 40)
        # The same slot, no longer past a longer draft horizon.
        window = _window(taxonomy, [_slot(TODAY + timedelta(days=20))])
        assert [i.code for i in slots.check_horizons(window, taxonomy, TODAY)] == []
        _, end = slots.window_bounds(taxonomy, TODAY)
        assert end == "2026-08-31", "a different draft horizon changed the window's length"


def test_a_window_is_its_calendar_month():
    """2026-12-31 is a Thursday posting day that a 30-day window never reached."""
    with tempfile.TemporaryDirectory() as tmp:
        taxonomy = _write_taxonomy(Path(tmp), 14, 30)
        assert slots.window_bounds(taxonomy, "2026-12-01") == ("2026-12-01", "2026-12-31")
        assert slots.window_bounds(taxonomy, "2027-02-01") == ("2027-02-01", "2027-02-28")
        assert slots.window_bounds(taxonomy, "2028-02-01") == ("2028-02-01", "2028-02-29")
        # A late start stays inside its own month, and so inside its own file.
        assert slots.window_bounds(taxonomy, "2026-10-15") == ("2026-10-15", "2026-10-31")



def _standing(directory: Path, lens: str, month: str, start: str, end: str, suffix=""):
    window = slots.CalendarWindow(window_id=f"{lens}-{start}", lens=lens,
                                  start_date=start, end_date=end)
    name = slots.window_filename(lens, month, suffix)
    return slots.write_window(directory / name, window)


def test_live_windows_keep_this_month_visible_once_next_month_exists():
    """The newest file alone hid October's second half the day November landed."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        october = _standing(root, "jordan", "2026-10", "2026-10-01", "2026-10-30")
        november = _standing(root, "jordan", "2026-11", "2026-11-01", "2026-11-30")
        _standing(root, "casey", "2026-11", "2026-11-01", "2026-11-30")
        _standing(root, "jordan", "2026-11", "2026-11-01", "2026-11-30", "proposed")
        _standing(root, "jordan", "2026-12", "2026-12-01", "2026-12-31", "rejected")

        assert slots.newest_window_path(root, lens="jordan") == november
        live = slots.live_window_paths(root, lens="jordan", today="2026-10-16")
        assert live == [october, november], f"expected both months, got {live}"
        # October's last day passes and it drops out; nothing else changes.
        assert slots.live_window_paths(root, lens="jordan", today="2026-10-31") == [november]
        assert slots.live_window_paths(root, lens="jordan", today="2026-12-01") == []
        # The dot-count rule holds: neither qualifier is ever live.
        names = [p.name for p in slots.live_window_paths(root, lens="jordan", today="2026-01-01")]
        assert not any(n.count(".") > 1 for n in names), names


def test_a_window_with_an_unreadable_end_stays_live():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        broken = _standing(root, "jordan", "2026-10", "2026-10-01", "not-a-date")
        assert slots.live_window_paths(root, lens="jordan", today="2027-01-01") == [broken], \
            "a standing window vanished because its end date could not be read"


def test_missing_horizons_report_rather_than_guess():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "threads.yaml"
        path.write_text("version: 1\nthreads: []\nforms: []\n", encoding="utf-8")
        taxonomy = slots.load_taxonomy(path)
        assert taxonomy.approved_horizon_days is None
        assert taxonomy.draft_horizon_days is None
        window = _window(taxonomy, [_slot(TODAY + timedelta(days=1))])
        # Nothing to say and nothing to guess: a missing horizon cannot make
        # a finding, because no finding here depends on one being present.
        assert slots.check_horizons(window, taxonomy, TODAY) == []


def test_thread_and_form_ids_are_read_from_config():
    """Rename the taxonomy's threads and the same window stops validating."""
    with tempfile.TemporaryDirectory() as tmp:
        good = _write_taxonomy(Path(tmp) / "good", 14, 30)
        renamed = _write_taxonomy(Path(tmp) / "renamed", 14, 30, thread_prefix="v2-")
        window = _window(good, [_slot(TODAY + timedelta(days=1))])
        assert slots.errors(slots.validate_window(window, good, today=TODAY)) == []
        codes = [i.code for i in slots.errors(slots.validate_window(window, renamed, today=TODAY))]
        assert "unknown-thread" in codes


def test_lens_vocabulary_comes_from_config_and_excludes_the_aggregate():
    with tempfile.TemporaryDirectory() as tmp:
        taxonomy = _write_taxonomy(Path(tmp), 14, 30)
        assert taxonomy.calendar_lens_ids == frozenset({"jordan", "blake"})
        assert taxonomy.v1_lens_ids == frozenset({"jordan"})
        window = _window(taxonomy, [_slot(TODAY + timedelta(days=1), lens="all")])
        codes = [i.code for i in slots.errors(slots.validate_window(window, taxonomy, today=TODAY))]
        assert "unknown-lens" in codes


# ---------------------------------------------------------------------
# Schema: validation
# ---------------------------------------------------------------------

def test_the_example_window_validates_clean():
    taxonomy = slots.load_taxonomy()
    window = example_window.build(taxonomy, TODAY)
    found = slots.validate_window(window, taxonomy, slots.known_item_refs(), today=TODAY)
    assert found == [], "\n".join(str(i) for i in found)


def test_validation_catches_what_it_claims_to():
    with tempfile.TemporaryDirectory() as tmp:
        taxonomy = _write_taxonomy(Path(tmp), 14, 30)
        cases = {
            "bad-date": _slot(TODAY + timedelta(days=1), date="the third of never"),
            "bad-item-ref": _slot(TODAY + timedelta(days=1), item_ref="anchor-03"),
            "unknown-form": _slot(TODAY + timedelta(days=1), forms=["a-form-nobody-declared"]),
            "unknown-approval-state": _slot(TODAY + timedelta(days=1), approval="probably"),
            "responds-to-self": _slot(
                TODAY + timedelta(days=1),
                responds_to=["content-atomizer/anchor-03-the-electricity-post"],
            ),
            "date-outside-window": _slot(TODAY + timedelta(days=90)),
            "declined-slot-holds-a-date": _slot(
                TODAY + timedelta(days=1), approval=slots.REJECTED
            ),
            "missing-rationale": _slot(TODAY + timedelta(days=1), rationale="  "),
            "missing-summary": _slot(TODAY + timedelta(days=1), summary="  "),
            "no-threads": _slot(TODAY + timedelta(days=1), threads=[]),
        }
        for code, slot in cases.items():
            window = _window(taxonomy, [slot])
            codes = [i.code for i in slots.validate_window(window, taxonomy, today=TODAY)]
            assert code in codes, f"{code} not caught, got {codes}"


def test_a_slot_nobody_has_decided_on_is_not_a_finding():
    """Approval is an affordance: its presence is respected, its absence is not a fault.

    Owner decision 2026-09-19. Until then every draft slot inside the
    approved horizon was a validation error, so a healthy freshly assembled
    calendar came back full of errors and every caller had to partition them
    back out.
    """
    with tempfile.TemporaryDirectory() as tmp:
        taxonomy = _write_taxonomy(Path(tmp), 14, 30)
        window = _window(taxonomy, [_slot(TODAY + timedelta(days=n)) for n in (1, 3, 7)])
        issues = slots.validate_window(window, taxonomy, today=TODAY)
        assert slots.errors(issues) == [], slots.errors(issues)
        for issue in issues:
            assert "approv" not in issue.code, f"{issue.code} still asks for a decision"
        assert not hasattr(slots, "required_approval"), "the requirement is back"
        assert not hasattr(slots, "approval_boundary"), "the deadline is back"
        assert not hasattr(slots, "partition_issues"), "the workaround outlived the finding"
    print("ok  a window nobody has decided on is a valid window")


def test_duplicate_slot_ids_and_repeated_items_are_caught():
    with tempfile.TemporaryDirectory() as tmp:
        taxonomy = _write_taxonomy(Path(tmp), 14, 30)
        first = _slot(TODAY + timedelta(days=1))
        second = _slot(TODAY + timedelta(days=2), slot_id=first.slot_id)
        codes = [i.code for i in slots.validate_window(_window(taxonomy, [first, second]), taxonomy, today=TODAY)]
        assert "duplicate-slot-id" in codes
        assert "item-scheduled-twice" in codes


def test_responds_to_ordering_is_enforced_inside_the_window():
    """The reason the field exists: a sequel cannot precede what it answers."""
    with tempfile.TemporaryDirectory() as tmp:
        taxonomy = _write_taxonomy(Path(tmp), 14, 30)
        original = _slot(
            TODAY + timedelta(days=8),
            slot_id="original",
            item_ref="content-atomizer/anchor-02-the-next-paradigm",
            approval=slots.APPROVED,
        )
        sequel = _slot(
            TODAY + timedelta(days=2),
            slot_id="sequel",
            responds_to=["content-atomizer/anchor-02-the-next-paradigm"],
        )
        codes = [i.code for i in slots.validate_window(_window(taxonomy, [original, sequel]), taxonomy, today=TODAY)]
        assert "responds-to-later-item" in codes

        sequel.date = (TODAY + timedelta(days=11)).isoformat()
        sequel.approval = slots.APPROVED
        codes = [i.code for i in slots.validate_window(_window(taxonomy, [original, sequel]), taxonomy, today=TODAY)]
        assert "responds-to-later-item" not in codes

        # A reference to something already published is satisfied by
        # definition, and must not be reported as an ordering problem.
        sequel.responds_to = ["sources/jordan-published-linkedin-posts.md#post-1"]
        codes = [i.code for i in slots.validate_window(_window(taxonomy, [original, sequel]), taxonomy, today=TODAY)]
        assert "responds-to-later-item" not in codes


def test_several_slots_on_one_date_is_valid():
    """Cadence is unanswered, so the schema must not assume one post per day."""
    with tempfile.TemporaryDirectory() as tmp:
        taxonomy = _write_taxonomy(Path(tmp), 14, 30)
        day = TODAY + timedelta(days=3)
        a = _slot(day, slot_id="a", item_ref="content-atomizer/anchor-03-the-electricity-post")
        b = _slot(day, slot_id="b", item_ref="content-atomizer/anchor-06-the-walker-receipts")
        window = _window(taxonomy, [a, b])
        assert slots.validate_window(window, taxonomy, today=TODAY) == []
        assert len(window.slots_on(day.isoformat())) == 2
        assert window.dates() == [day.isoformat()]


def test_a_broken_taxonomy_degrades_instead_of_failing_every_slot():
    taxonomy = slots.load_taxonomy(Path(tempfile.gettempdir()) / "does-not-exist-threads.yaml")
    assert taxonomy.ok is False
    window = slots.CalendarWindow(
        window_id="w", lens="jordan", start_date=TODAY.isoformat(),
        end_date=(TODAY + timedelta(days=29)).isoformat(), arc="an arc",
        slots=[_slot(TODAY + timedelta(days=1), threads=["whatever-this-is"])],
    )
    codes = [i.code for i in slots.validate_window(window, taxonomy, today=TODAY)]
    assert "unknown-thread" not in codes
    assert "unknown-lens" not in codes


# ---------------------------------------------------------------------
# Schema: the join with the store
# ---------------------------------------------------------------------

def test_apply_approvals_takes_the_stores_newer_word():
    with tempfile.TemporaryDirectory() as tmp:
        taxonomy = _write_taxonomy(Path(tmp), 14, 30)
        slot = _slot(TODAY + timedelta(days=20), approval=slots.DRAFT, slot_id="s1")
        window = _window(taxonomy, [slot])
        updated = slots.apply_approvals(window, {"s1": slots.APPROVED})
        assert updated.slots[0].approval == slots.APPROVED
        assert window.slots[0].approval == slots.DRAFT, "the original must not be mutated"
        # An unknown state is refused rather than written in.
        untouched = slots.apply_approvals(window, {"s1": "aproved"})
        assert untouched.slots[0].approval == slots.DRAFT


# ---------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------

def test_append_then_read_newest_first_with_incrementing_ids():
    with tempfile.TemporaryDirectory() as tmp:
        result = state_store.record_state(
            "ingestion_log",
            [
                {"action": "seen", "entry_key": "a", "event_at": "2026-08-17T09:00:00Z"},
                {"action": "seen", "entry_key": "b", "event_at": "2026-08-18T09:00:00Z", "quantity": 4},
            ],
            root=tmp,
        )
        assert result == {"stored": 2, "quantity": 5, "ok": True}
        rows = state_store.read_state("ingestion_log", root=tmp)
        assert [r.entry_key for r in rows] == ["b", "a"]
        assert [r.id for r in rows] == [2, 1]
        again = state_store.record_state("ingestion_log", [{"entry_key": "c"}], root=tmp)
        assert again["stored"] == 1
        assert max(r.id for r in state_store.read_state("ingestion_log", root=tmp)) == 3


def test_state_survives_a_separate_process():
    """The property the store exists for, checked the only way that proves it."""
    with tempfile.TemporaryDirectory() as tmp:
        state_store.record_state(
            "ingestion_log",
            [{"action": "seen", "entry_key": "market-scan/2f1c9a04", "value": {"kept": True}}],
            root=tmp,
        )
        script = (
            "import sys; sys.path.insert(0, %r);"
            "import state_store;"
            "rows = state_store.read_state('ingestion_log', root=%r);"
            "print(len(rows), rows[0].entry_key, rows[0].value['kept'])" % (str(_HERE), tmp)
        )
        out = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, check=True
        )
        assert out.stdout.strip() == "1 market-scan/2f1c9a04 True"


def test_latest_per_key_is_the_deduplicated_set():
    with tempfile.TemporaryDirectory() as tmp:
        state_store.record_state(
            "ingestion_log",
            [
                {"action": "seen", "entry_key": "x", "event_at": "2026-08-16T09:00:00Z"},
                {"action": "seen", "entry_key": "y", "event_at": "2026-08-16T09:00:00Z"},
            ],
            root=tmp,
        )
        state_store.record_state(
            "ingestion_log",
            [{"action": "seen", "entry_key": "x", "event_at": "2026-08-18T09:00:00Z"}],
            root=tmp,
        )
        latest = state_store.read_state("ingestion_log", root=tmp, latest_per_key=True)
        assert len(latest) == 2
        by_key = {r.entry_key: r for r in latest}
        assert by_key["x"].event_at.startswith("2026-08-18")
        assert len(state_store.read_state("ingestion_log", root=tmp)) == 3, "history is kept"


def test_keyless_entries_are_not_dropped_by_latest_per_key():
    with tempfile.TemporaryDirectory() as tmp:
        state_store.record_state(
            "run_log",
            [{"action": "run"}, {"action": "run"}, {"action": "seen", "entry_key": "k"}],
            root=tmp,
        )
        assert len(state_store.read_state("run_log", root=tmp, latest_per_key=True)) == 3


def test_filters_and_payload_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        payload = {"nested": {"list": [1, 2, 3], "text": "an em dash — survives"}}
        state_store.record_state(
            "ingestion_log",
            [
                {"action": "seen", "entry_key": "old", "event_at": "2026-07-01T00:00:00Z"},
                {"action": "approval", "entry_key": "new", "event_at": "2026-08-18T00:00:00+02:00",
                 "value": payload, "source": "a verbatim extra key"},
            ],
            root=tmp,
        )
        assert len(state_store.read_state("ingestion_log", root=tmp, action="approval")) == 1
        assert len(state_store.read_state("ingestion_log", root=tmp, entry_key="old")) == 1
        assert len(state_store.read_state("ingestion_log", root=tmp, since="2026-08-01")) == 1
        assert len(state_store.read_state("ingestion_log", root=tmp, until="2026-08-01")) == 1
        assert len(state_store.read_state("ingestion_log", root=tmp, limit=1)) == 1
        row = state_store.read_state("ingestion_log", root=tmp, entry_key="new")[0]
        assert row.value == payload
        assert row.extra["source"] == "a verbatim extra key"
        # +02:00 normalized to UTC, so a string comparison is chronological.
        assert row.event_at == "2026-08-17T22:00:00+00:00"


def test_there_is_no_update_call():
    """Append-only is a property, and properties erode quietly."""
    for name in ("update_state", "update", "delete_state", "delete", "overwrite", "set_state"):
        assert not hasattr(state_store, name), f"state_store grew a {name}() call"
    assert "update" not in " ".join(state_store.__all__)


def test_degrades_rather_than_raising():
    with tempfile.TemporaryDirectory() as tmp:
        assert state_store.read_state("never-written", root=tmp) == []
        assert state_store.record_state("../escape", [{"action": "x"}], root=tmp)["ok"] is False
        assert list(Path(tmp).iterdir()) == []
        assert state_store.record_state("ok_name", [], root=tmp) == {"stored": 0, "quantity": 0, "ok": True}
        assert state_store.read_state("Bad Name", root=tmp) == []
        assert state_store.state_stores(root=Path(tmp) / "nothing-here") == []

        # A corrupt line must cost one entry, not the whole read.
        state_store.record_state("ingestion_log", [{"entry_key": "a"}, {"entry_key": "b"}], root=tmp)
        path = state_store.store_path("ingestion_log", root=tmp)
        with path.open("a", encoding="utf-8") as handle:
            handle.write("{not json at all\n")
        state_store.record_state("ingestion_log", [{"entry_key": "c"}], root=tmp)
        rows = state_store.read_state("ingestion_log", root=tmp)
        assert [r.entry_key for r in rows][-2:] == ["b", "a"]
        assert len(rows) == 3
        assert len({r.id for r in rows}) == 3, "ids must not repeat around a damaged line"


def test_quantity_defaults_to_one_and_counts_real_things():
    with tempfile.TemporaryDirectory() as tmp:
        result = state_store.record_state(
            "ingestion_log",
            [{"action": "seen"}, {"action": "seen", "quantity": 20}, {"action": "seen", "quantity": "many"}],
            root=tmp,
        )
        assert result == {"stored": 3, "quantity": 22, "ok": True}
        summary = state_store.state_stores(root=tmp)[0]
        assert (summary["events"], summary["quantity"]) == (3, 22)


def test_recording_the_same_entry_twice_appends_rather_than_replacing():
    with tempfile.TemporaryDirectory() as tmp:
        entry = {"action": "approval", "entry_key": "slot-1", "value": {"state": "approved"}}
        state_store.record_state("approvals", [entry], root=tmp, now=datetime(2026, 8, 18, tzinfo=timezone.utc))
        state_store.record_state(
            "approvals",
            [{"action": "approval", "entry_key": "slot-1", "value": {"state": "rejected"}}],
            root=tmp,
            now=datetime(2026, 8, 19, tzinfo=timezone.utc),
        )
        assert len(state_store.read_state("approvals", root=tmp)) == 2
        current = state_store.read_state("approvals", root=tmp, latest_per_key=True)
        assert current[0].value["state"] == "rejected"


# ---------------------------------------------------------------------
# End to end: the two halves together
# ---------------------------------------------------------------------

def test_a_window_survives_a_run_and_picks_up_an_approval():
    """What a daily run actually does with both halves of item 8."""
    taxonomy = slots.load_taxonomy()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        window = example_window.build(taxonomy, TODAY)
        path = slots.write_window(root / "windows" / f"{window.window_id}.json", window)
        state_store.record_state(
            "windows",
            [{"action": "assembled", "entry_key": window.window_id, "value": {"path": str(path)}}],
            root=root / "state",
        )
        draft_slot = next(s for s in window.slots if s.approval == slots.DRAFT)
        state_store.record_state(
            "approvals",
            [{"action": "approval", "entry_key": draft_slot.slot_id, "value": {"state": slots.APPROVED}}],
            root=root / "state",
        )

        # A later run: reads the window off disk, reads the decisions, joins.
        reloaded = slots.read_window(path)
        decisions = {
            r.entry_key: (r.value or {}).get("state")
            for r in state_store.read_state("approvals", root=root / "state", latest_per_key=True)
        }
        joined = slots.apply_approvals(reloaded, decisions)
        assert {s.slot_id: s.approval for s in joined.slots}[draft_slot.slot_id] == slots.APPROVED
        assert [s["store"] for s in state_store.state_stores(root=root / "state")] == ["approvals", "windows"]


def main() -> int:
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_")]
    failures = []
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failures.append((name, exc))
            print(f"FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001 - a test erroring is a failure
            failures.append((name, exc))
            print(f"ERROR {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {name}")
    print(f"\n{len(tests) - len(failures)} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
