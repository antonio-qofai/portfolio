"""Tests for the weekly calendar refresh (PRD.md build order item 17).

Why this module gets tests, and what the tests are actually protecting.

The refresh is the only job that runs unattended, spends money, and touches
the calendar a founder has approved part of. Two of its failure modes are
invisible from the outside and one is expensive:

  - It proposes a change to something already agreed to. The prompt asks the
    model to leave approved slots alone, and asking is not a guarantee. The
    verification is the guarantee, so it is asserted directly: a proposal
    that moves, drops, or repoints a fixed slot is refused, not repaired.
    Repairing would hide that the model was told and did it anyway.
  - It fires on nothing. A weekly model call that re-proposes an unchanged
    calendar spends a request to move posts under someone who has already
    read the reasons for where they were. Asserted by running a whole refresh
    against a quiet week with a caller that raises if invoked.
  - It fires on a hardcoded threshold rather than on the policy. Every
    threshold test builds a policy with deliberately different numbers and
    asserts the verdict follows the file, so a constant in the code cannot
    pass.

Two more that are specific to this module:

  - A date that has passed is as fixed as an approval, and it cannot be
    expressed as one, because writing an approval nobody gave is the failure
    the whole build is arranged around. So it is a rule about time and is
    asserted on a draft slot dated yesterday.
  - A proposal must never be mistaken for the calendar. Asserted through
    `slots.newest_window_path`, since that is the function the page and the
    approval queue both use to decide what they are looking at.

Run it standalone:

    ../.venv/bin/python3 test_refresh.py

It needs pyyaml, which the folder already requires, and nothing else. It
never calls the API: every test that reaches the model injects a caller.
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
sys.path.insert(0, str(_AGENT_ROOT / "calendar_model"))

import assemble as assemble_mod  # noqa: E402
import refresh as refresh_mod  # noqa: E402
import slots as slots_mod  # noqa: E402
import state_store  # noqa: E402

TODAY = "2026-09-13"
WRITTEN_AT = "2026-09-07T12:00:00Z"

POLICY_YAML = """
version: 1
triggers:
  new_schedulable_content:
    enabled: {new_enabled}
    minimum: {minimum}
  content_declined:
    enabled: {declined_enabled}
  narrative_changed:
    enabled: {narrative_enabled}
  window_running_out:
    enabled: true
    days_left_at_or_below: {days_left}
    proposes: false
always: {always}
output:
  proposal_suffix: {suffix}
  record_runs: false
  store: refresh_runs
supply:
  runway_warn_weeks: {warn}
"""


def _policy(
    tmp: Path,
    *,
    minimum=1,
    new_enabled="true",
    declined_enabled="true",
    narrative_enabled="true",
    days_left=10,
    always="false",
    suffix=".proposed",
    warn=4,
):
    path = tmp / "refresh_policy.yaml"
    path.write_text(
        POLICY_YAML.format(
            minimum=minimum,
            new_enabled=new_enabled,
            declined_enabled=declined_enabled,
            narrative_enabled=narrative_enabled,
            days_left=days_left,
            always=always,
            suffix=suffix,
            warn=warn,
        ),
        encoding="utf-8",
    )
    return refresh_mod.load_policy(path)


TAXONOMY_YAML = """
lenses:
  - id: jordan
    name: Jordan, PE lens
    calendar: true
    v1: true
threads:
  - id: ai-spend-visibility
    name: AI spend visibility
forms:
  - id: research-anchored-gap
    description: a gap named by research
sequencing:
  approved_horizon_days: 14
  draft_horizon_days: 30
"""


def _taxonomy(tmp: Path):
    path = tmp / "threads.yaml"
    path.write_text(TAXONOMY_YAML, encoding="utf-8")
    return slots_mod.load_taxonomy(path)


def _slot(day, n, approval=slots_mod.DRAFT):
    return slots_mod.Slot(
        slot_id=f"jordan-{day}-01",
        date=day,
        item_ref=f"value-creation-briefing/post_{n}",
        lens="jordan",
        threads=["ai-spend-visibility"],
        forms=["research-anchored-gap"],
        approval=approval,
        rationale="because it answers the last one",
    )


def _standing(approvals=None):
    approvals = approvals or {}
    days = ["2026-09-10", "2026-09-14", "2026-09-17", "2026-10-01"]
    return slots_mod.CalendarWindow(
        window_id="jordan-2026-09",
        lens="jordan",
        start_date="2026-09-07",
        end_date="2026-10-06",
        generated_at=WRITTEN_AT,
        arc="an arc",
        slots=[
            _slot(day, n, approvals.get(f"jordan-{day}-01", slots_mod.DRAFT))
            for n, day in enumerate(days, start=1)
        ],
    )


def _candidates(refs):
    return [
        assemble_mod.Candidate(
            item_ref=ref,
            source=ref.split("/")[0],
            persona="jordan",
            title=ref,
            text="body",
            word_count=2,
            source_path="somewhere",
        )
        for ref in refs
    ]


CORPUS = [f"value-creation-briefing/post_{n}" for n in range(1, 7)]


class _Entry:
    """An ingestion_log row, in the shape gap_detection reads."""

    def __init__(self, entry_key, event_at):
        self.entry_key = entry_key
        self.event_at = event_at
        self.created_at = event_at
        self.value = {"source": entry_key.split("/")[0]}


def _entries(pairs):
    return [_Entry(ref, when) for ref, when in pairs]


def _decision(state, at, item_ref="value-creation-briefing/post_2"):
    return {"state": state, "by": "antonio", "at": at, "item_ref": item_ref}


def _context():
    return assemble_mod.Context(market_items=[], documents=[], published=[], notes=[])


def _explodes(*_args, **_kwargs):
    raise AssertionError("the model was called on a week where nothing changed")


def _returns(window):
    """A caller that hands back `window` as the model's payload."""
    def caller(_criteria, _system, _user, _schema):
        return window.to_dict()
    return caller


# ---------------------------------------------------------------------
# What changed
# ---------------------------------------------------------------------

def test_only_what_arrived_after_the_window_was_written_counts_as_new():
    with tempfile.TemporaryDirectory() as tmp:
        _policy(Path(tmp))
        signals = refresh_mod.changes_since(
            _standing(),
            schedulable=set(CORPUS),
            entries=_entries([
                ("value-creation-briefing/post_1", "2026-09-01T00:00:00Z"),
                ("value-creation-briefing/post_5", "2026-09-09T00:00:00Z"),
                ("content-atomizer/anchor-09", "2026-09-11T00:00:00Z"),
            ]),
            decisions={},
            today=TODAY,
        )
        assert signals.arrived == [
            "content-atomizer/anchor-09",
            "value-creation-briefing/post_5",
        ], signals.arrived
        assert signals.arrived_schedulable == ["value-creation-briefing/post_5"], (
            "an arrival outside the schedulable corpus is not a reason to re-sequence"
        )
        assert signals.days_left == 23, signals.days_left
        print("ok  new means first seen after the window was written, and schedulable")


def test_decisions_are_split_by_kind_and_by_when():
    with tempfile.TemporaryDirectory() as tmp:
        _policy(Path(tmp))
        signals = refresh_mod.changes_since(
            _standing(),
            schedulable=set(CORPUS),
            entries=[],
            decisions={
                "jordan-2026-09-10-01": _decision(slots_mod.APPROVED, "2026-09-06T00:00:00Z"),
                "jordan-2026-09-14-01": _decision(slots_mod.APPROVED, "2026-09-11T00:00:00Z"),
                "jordan-2026-09-17-01": _decision(slots_mod.REJECTED, "2026-09-12T00:00:00Z"),
            },
            today=TODAY,
        )
        assert signals.approved == ["jordan-2026-09-14-01"], signals.approved
        assert signals.declined == ["jordan-2026-09-17-01"], signals.declined
        print("ok  decisions taken before the window was written are not news")


def test_arrival_is_decided_by_the_instant_not_by_how_it_was_spelled():
    """The store writes +00:00 and several callers write Z for the same time.

    "Z" sorts after "+" in ASCII, so a string comparison of two timestamps in
    the same second answers by format rather than by time. Asserted on a pair
    that differ only in spelling, where a text comparison gets it wrong.
    """
    standing = _standing()
    standing.generated_at = "2026-09-07T12:00:00+00:00"
    signals = refresh_mod.changes_since(
        standing,
        schedulable=set(CORPUS),
        entries=_entries([
            ("value-creation-briefing/post_5", "2026-09-07T12:00:00Z"),   # the same instant
            ("value-creation-briefing/post_6", "2026-09-07T12:00:01Z"),   # one second later
        ]),
        decisions={},
        today=TODAY,
    )
    assert signals.arrived == ["value-creation-briefing/post_6"], (
        f"the same instant is not an arrival, and a second later is: {signals.arrived}"
    )
    print("ok  arrival compares instants, not the way a timestamp happened to be written")


def test_a_window_with_no_timestamp_does_not_make_everything_look_new():
    standing = _standing()
    standing.generated_at = ""
    standing.start_date = ""
    signals = refresh_mod.changes_since(
        standing,
        schedulable=set(CORPUS),
        entries=_entries([("value-creation-briefing/post_5", "2026-09-09T00:00:00Z")]),
        decisions={"x": _decision(slots_mod.REJECTED, "2026-09-12T00:00:00Z")},
        today=TODAY,
    )
    assert signals.arrived == [] and signals.declined == [], signals
    assert signals.notes, "an undateable window has to say so rather than fire everything"
    print("ok  an undateable standing window reports rather than firing every trigger")


def test_a_changed_narrative_document_is_noticed():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        steady, moving = tmp / "voice.md", tmp / "threads.md"
        steady.write_text("unchanged", encoding="utf-8")
        moving.write_text("before", encoding="utf-8")
        paths = [steady, moving]

        first = refresh_mod.changes_since(
            _standing(), schedulable=set(CORPUS), entries=[], decisions={},
            narrative_paths=paths, baseline={}, today=TODAY,
        )
        assert first.narrative_changed == [], "a first sighting is not a change"
        assert first.notes and "first digest" in first.notes[0], first.notes
        assert set(first.digests) == {"voice.md", "threads.md"}, first.digests

        moving.write_text("after", encoding="utf-8")
        second = refresh_mod.changes_since(
            _standing(), schedulable=set(CORPUS), entries=[], decisions={},
            narrative_paths=paths, baseline=first.digests, today=TODAY,
        )
        assert second.narrative_changed == ["threads.md"], second.narrative_changed
        print("ok  a narrative document whose content moved is the trigger, once")


def test_a_fresh_checkout_does_not_look_like_every_document_changed():
    """The bug this replaced, and the reason it is digests and not mtimes.

    Git leaves a working copy's mtime alone unless the content changes, so
    comparing modification times looks correct on a laptop. A CI checkout
    writes every file fresh, so every document's mtime is the checkout time.
    Wired to a cron that way, the trigger fires on every run and the refresh
    calls the model every week regardless, which is precisely the churn and
    the unattended spend the triggers exist to prevent. Simulated here by
    rewriting the same bytes, which is what a checkout does: new mtime,
    identical content.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        doc = tmp / "voice.md"
        doc.write_text("the same bytes", encoding="utf-8")
        baseline = refresh_mod.document_digests([doc])

        import os, time
        doc.write_text("the same bytes", encoding="utf-8")
        later = time.time() + 10_000
        os.utime(doc, (later, later))

        signals = refresh_mod.changes_since(
            _standing(), schedulable=set(CORPUS), entries=[], decisions={},
            narrative_paths=[doc], baseline=baseline, today=TODAY,
        )
        assert signals.narrative_changed == [], (
            "a rewritten-but-identical file is a checkout, not a change"
        )
        print("ok  a fresh checkout is not mistaken for the narrative changing")


def test_a_document_that_disappears_counts_as_a_change():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        doc = tmp / "voice.md"
        doc.write_text("here", encoding="utf-8")
        baseline = refresh_mod.document_digests([doc])
        doc.unlink()
        signals = refresh_mod.changes_since(
            _standing(), schedulable=set(CORPUS), entries=[], decisions={},
            narrative_paths=[doc], baseline=baseline, today=TODAY,
        )
        assert signals.narrative_changed == ["voice.md"], signals.narrative_changed
        print("ok  a narrative document that vanishes is a change, not a silence")


def test_the_baseline_resets_when_the_window_is_reassembled():
    previous = {"signals": {"written_at": WRITTEN_AT, "digests": {"voice.md": "abc"}}}
    assert refresh_mod.baseline_digests(previous, WRITTEN_AT) == {"voice.md": "abc"}
    assert refresh_mod.baseline_digests(previous, "2026-10-01T00:00:00Z") == {}, (
        "a baseline taken for a calendar that no longer exists must not be reused"
    )
    assert refresh_mod.baseline_digests({}, WRITTEN_AT) == {}
    print("ok  reassembling the window starts the narrative comparison over")


def test_the_baseline_comes_from_the_last_recorded_run_for_this_window():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "state"
        outcome = refresh_mod.Outcome(window_id="jordan-2026-09")
        outcome.signals = refresh_mod.Signals(
            written_at=WRITTEN_AT, digests={"voice.md": "first"}
        )
        state_store.record_state("refresh_runs", [outcome.to_entry(as_of="2026-09-13")], root=root)
        outcome.signals.digests = {"voice.md": "second"}
        state_store.record_state("refresh_runs", [outcome.to_entry(as_of="2026-09-20")], root=root)

        found = refresh_mod.last_run("jordan-2026-09", store="refresh_runs", root=root)
        assert found["signals"]["digests"] == {"voice.md": "second"}, (
            "the newest run is the baseline, not the first"
        )
        assert refresh_mod.last_run("jordan-2026-10", store="refresh_runs", root=root) == {}, (
            "a different window has no baseline of its own yet"
        )
        print("ok  the baseline is the newest recorded run for this window")


# ---------------------------------------------------------------------
# Supply
# ---------------------------------------------------------------------

def test_supply_counts_what_is_behind_the_calendar_and_says_how_long_it_lasts():
    with tempfile.TemporaryDirectory() as tmp:
        policy = _policy(Path(tmp), warn=4)
        signals = refresh_mod.Signals(arrived_schedulable=["value-creation-briefing/post_5"])
        supply = refresh_mod.supply_report(
            _standing(), set(CORPUS), signals,
            cadence={"posts_per_week_min": 2, "posts_per_week_max": 2},
            policy=policy,
        )
        assert supply.corpus == 6 and len(supply.scheduled) == 4, supply
        assert supply.unscheduled == [
            "value-creation-briefing/post_5",
            "value-creation-briefing/post_6",
        ], supply.unscheduled
        assert supply.runway_weeks == 1.0, supply.runway_weeks
        assert supply.thin, "1 week of runway is under the 4 the policy warns at"
        assert supply.arrived_unscheduled == ["value-creation-briefing/post_5"], (
            "something that arrived and is still unscheduled is the slot-this-in candidate"
        )
        print("ok  supply reports the corpus, the backlog, the runway, and what to slot in")


def test_the_runway_warning_comes_from_the_policy():
    with tempfile.TemporaryDirectory() as tmp:
        signals = refresh_mod.Signals()
        generous = refresh_mod.supply_report(
            _standing(), set(CORPUS), signals,
            cadence={"posts_per_week_max": 2}, policy=_policy(Path(tmp), warn=0.5),
        )
        assert not generous.thin, "1 week of runway is over a 0.5 week threshold"
        strict = refresh_mod.supply_report(
            _standing(), set(CORPUS), signals,
            cadence={"posts_per_week_max": 2}, policy=_policy(Path(tmp), warn=8),
        )
        assert strict.thin, "1 week of runway is under an 8 week threshold"
        print("ok  thin follows the policy's number rather than one in the code")


def test_the_cadence_comes_from_the_sequencing_criteria_not_from_here():
    with tempfile.TemporaryDirectory() as tmp:
        policy = _policy(Path(tmp))
        signals = refresh_mod.Signals()
        weekly = refresh_mod.supply_report(
            _standing(), set(CORPUS), signals,
            cadence={"posts_per_week_max": 1}, policy=policy,
        )
        twice = refresh_mod.supply_report(
            _standing(), set(CORPUS), signals,
            cadence={"posts_per_week_max": 2}, policy=policy,
        )
        assert weekly.runway_weeks == 2.0 and twice.runway_weeks == 1.0, (weekly, twice)
        print("ok  runway follows the cadence the sequencing criteria state")


# ---------------------------------------------------------------------
# Whether to ask the model
# ---------------------------------------------------------------------

def test_a_quiet_week_proposes_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        policy = _policy(Path(tmp))
        verdict = refresh_mod.should_propose(
            refresh_mod.Signals(days_left=23), refresh_mod.Supply(), policy
        )
        assert verdict.propose is False, verdict
        assert verdict.quiet, "a quiet week still has to say what it looked at"
        print("ok  nothing changed means no proposal and no model call")


def test_each_trigger_fires_on_its_own():
    with tempfile.TemporaryDirectory() as tmp:
        policy = _policy(Path(tmp))
        cases = {
            "new_schedulable_content": refresh_mod.Signals(arrived_schedulable=["a/b"]),
            "content_declined": refresh_mod.Signals(declined=["jordan-1"]),
            "narrative_changed": refresh_mod.Signals(narrative_changed=["voice.md"]),
        }
        for expected, signals in cases.items():
            verdict = refresh_mod.should_propose(signals, refresh_mod.Supply(), policy)
            assert verdict.propose, f"{expected} did not fire"
            assert [n for n, _ in verdict.fired] == [expected], verdict.fired
        print("ok  each trigger fires alone, and names itself")


def test_the_arrival_threshold_is_read_from_the_policy():
    with tempfile.TemporaryDirectory() as tmp:
        one = refresh_mod.Signals(arrived_schedulable=["a/b"])
        assert refresh_mod.should_propose(one, refresh_mod.Supply(), _policy(Path(tmp), minimum=1)).propose
        assert not refresh_mod.should_propose(
            one, refresh_mod.Supply(), _policy(Path(tmp), minimum=3)
        ).propose, "a threshold of 3 must not fire on 1 arrival"
        three = refresh_mod.Signals(arrived_schedulable=["a/b", "a/c", "a/d"])
        assert refresh_mod.should_propose(
            three, refresh_mod.Supply(), _policy(Path(tmp), minimum=3)
        ).propose
        print("ok  the arrival threshold follows the policy, not a constant")


def test_a_disabled_trigger_does_not_fire():
    with tempfile.TemporaryDirectory() as tmp:
        policy = _policy(Path(tmp), declined_enabled="false")
        verdict = refresh_mod.should_propose(
            refresh_mod.Signals(declined=["jordan-1"]), refresh_mod.Supply(), policy
        )
        assert verdict.propose is False, verdict
        print("ok  a trigger switched off in the policy stays off")


def test_the_window_running_out_reports_but_never_proposes():
    with tempfile.TemporaryDirectory() as tmp:
        policy = _policy(Path(tmp), days_left=10)
        verdict = refresh_mod.should_propose(
            refresh_mod.Signals(days_left=3), refresh_mod.Supply(), policy
        )
        assert verdict.propose is False, "assembling the next window is not the refresh's job"
        assert any("monthly_assembly.py" in sentence for sentence in verdict.quiet), verdict.quiet
        print("ok  a window running out is reported and handed to the monthly assembly")


def test_force_overrides_a_quiet_week():
    with tempfile.TemporaryDirectory() as tmp:
        verdict = refresh_mod.should_propose(
            refresh_mod.Signals(), refresh_mod.Supply(), _policy(Path(tmp)), force=True
        )
        assert verdict.propose and verdict.fired[0][0] == "forced", verdict
        print("ok  --force proposes without a trigger, and says that is why")


# ---------------------------------------------------------------------
# The guarantee
# ---------------------------------------------------------------------

def test_an_approved_slot_and_a_past_slot_are_both_fixed():
    fixed = refresh_mod.fixed_slots(
        _standing(approvals={"jordan-2026-09-14-01": slots_mod.APPROVED}), TODAY
    )
    assert set(fixed) == {"jordan-2026-09-10-01", "jordan-2026-09-14-01"}, fixed
    print("ok  a date that has passed is as fixed as an approval, without being marked as one")


def test_a_proposal_that_moves_a_fixed_slot_is_refused():
    standing = _standing(approvals={"jordan-2026-09-14-01": slots_mod.APPROVED})

    moved = _standing()
    moved.slots[1].date = "2026-09-24"
    dropped = _standing()
    dropped.slots = [s for s in dropped.slots if s.date != "2026-09-14"]
    repointed = _standing()
    repointed.slots[1].item_ref = "value-creation-briefing/post_6"

    for what, proposal, code in (
        ("moved", moved, "fixed-slot-moved"),
        ("dropped", dropped, "fixed-slot-dropped"),
        ("repointed", repointed, "fixed-slot-repointed"),
    ):
        _, issues = refresh_mod.reconcile_fixed(standing, proposal, TODAY)
        assert [i.code for i in issues] == [code], f"{what}: {issues}"

    _, clean = refresh_mod.reconcile_fixed(standing, _standing(), TODAY)
    assert clean == [], f"an untouched proposal is not a violation: {clean}"
    print("ok  moving, dropping, or repointing a fixed slot is each caught by name")


def test_a_fixed_slot_is_matched_by_its_post_and_date_not_by_its_generated_id():
    """assemble numbers slots positionally, so the id is not the identity.

    A proposal that keeps every fixed post on its date is correct even though
    every slot_id in it differs, and the reconciliation has to say so rather
    than reporting four dropped slots.
    """
    standing = _standing(approvals={"jordan-2026-09-14-01": slots_mod.APPROVED})
    proposal = _standing()
    for index, slot in enumerate(proposal.slots, start=1):
        slot.slot_id = f"jordan-2026-09-{index:02d}"       # what assemble would produce
        slot.approval = slots_mod.DRAFT                    # what a fresh assembly returns

    reconciled, issues = refresh_mod.reconcile_fixed(standing, proposal, TODAY)
    assert issues == [], f"renumbering is not a violation: {issues}"

    carried = {s.date: (s.slot_id, s.approval) for s in reconciled.slots}
    assert carried["2026-09-14"] == ("jordan-2026-09-14-01", slots_mod.APPROVED), (
        "the approved slot has to keep its id and its approval, or the approval "
        "would be lost and the post would return to the queue as undecided"
    )
    assert carried["2026-09-10"][0] == "jordan-2026-09-10-01", (
        "a past slot keeps its identity too"
    )
    assert len({s.slot_id for s in reconciled.slots}) == len(reconciled.slots), (
        "slot ids have to stay unique, since the approval store is keyed by them"
    )
    print("ok  a fixed slot is matched by its post and its date, and carries its approval over")


def test_a_violating_proposal_is_kept_for_inspection_and_the_run_fails():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        policy = _policy(tmp)
        taxonomy = _taxonomy(tmp)
        standing = _standing(approvals={"jordan-2026-09-14-01": slots_mod.APPROVED})
        standing_path = tmp / "window-2026-09.json"
        slots_mod.write_window(standing_path, standing)

        violating = _standing(approvals={"jordan-2026-09-14-01": slots_mod.APPROVED})
        violating.slots[1].date = "2026-09-24"

        outcome = refresh_mod.refresh(
            standing, standing_path,
            criteria={"cadence": {"posts_per_week_max": 2}},
            taxonomy=taxonomy, policy=policy, lens="jordan",
            candidates=_candidates(CORPUS), context=_context(),
            entries=[], decisions={},
            today=TODAY, force=True, caller=_returns(violating),
        )
        assert outcome.violations, "the violation has to be reported"
        assert outcome.ok is False, "a violating run must not report success"
        assert Path(outcome.proposal_path).is_file(), (
            "the proposal is kept so the violation can be inspected without paying again"
        )
        assert slots_mod.read_window(standing_path).slots[1].date == "2026-09-14", (
            "the standing calendar must be untouched"
        )
        report = refresh_mod.format_report(outcome, as_of=TODAY)
        assert "REFUSED" in report, report
        print("ok  a violating proposal is refused, kept for inspection, and fails the run")


# ---------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------

def test_every_live_window_is_refreshed_as_its_own_run():
    """November is where new content matters most, and October is still live.

    The per-window run is intercepted where `main` recurses, so this asserts
    the dispatch without reading the corpus or reaching a model. Each window
    becoming its own run is what keeps triggers per window: one firing for
    November cannot pay for a re-sequence of October.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for month, start, end in (("2026-09", "2026-09-01", "2026-09-30"),
                                  ("2026-10", "2026-10-01", "2026-10-30"),
                                  ("2026-11", "2026-11-01", "2026-11-30")):
            slots_mod.write_window(root / slots_mod.window_filename("jordan", month),
                                   slots_mod.CalendarWindow(window_id=f"jordan-{start}",
                                                            lens="jordan", start_date=start,
                                                            end_date=end))
        slots_mod.write_window(root / slots_mod.window_filename("jordan", "2026-11", "proposed"),
                               slots_mod.CalendarWindow(window_id="x", lens="jordan",
                                                        start_date="2026-11-01",
                                                        end_date="2026-11-30"))
        calls = []
        original = refresh_mod.main

        def recorder(argv=None):
            calls.append(list(argv))
            return 1 if len(calls) == 1 else 0

        refresh_mod.main = recorder
        try:
            code = original(["--lens", "jordan", "--state-root", str(root),
                             "--as-of", "2026-10-16", "--report-only"])
        finally:
            refresh_mod.main = original
        windows = [Path(c[c.index("--window") + 1]).name for c in calls]
        assert windows == ["window-jordan-2026-10.json", "window-jordan-2026-11.json"], windows
        assert all("--report-only" in c for c in calls), "a flag was dropped per window"
        assert code == 1, "one window failing must still red the run"
    print("ok  every live window is refreshed as its own run; past and proposed ones are not")


def test_a_quiet_run_never_reaches_the_model():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        standing_path = tmp / "window-2026-09.json"
        slots_mod.write_window(standing_path, _standing())
        outcome = refresh_mod.refresh(
            _standing(), standing_path,
            criteria={"cadence": {"posts_per_week_max": 2}},
            taxonomy=_taxonomy(tmp), policy=_policy(tmp), lens="jordan",
            candidates=_candidates(CORPUS), context=_context(),
            entries=[], decisions={},
            today=TODAY, caller=_explodes,
        )
        assert outcome.proposal_path == "" and outcome.ok, outcome
        assert outcome.supply.corpus == 6, "the free half still reports on a quiet week"
        print("ok  a quiet week reports supply and spends nothing")


def test_report_only_never_reaches_the_model_even_when_a_trigger_fired():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        standing_path = tmp / "window-2026-09.json"
        slots_mod.write_window(standing_path, _standing())
        outcome = refresh_mod.refresh(
            _standing(), standing_path,
            criteria={"cadence": {"posts_per_week_max": 2}},
            taxonomy=_taxonomy(tmp), policy=_policy(tmp), lens="jordan",
            candidates=_candidates(CORPUS), context=_context(),
            entries=_entries([("value-creation-briefing/post_5", "2026-09-09T00:00:00Z")]),
            decisions={}, today=TODAY, report_only=True, caller=_explodes,
        )
        assert outcome.verdict.propose, "the trigger still has to fire and be reported"
        assert outcome.proposal_path == "", "report-only must not call the model"
        print("ok  --report-only reports the trigger and still spends nothing")


def test_a_good_proposal_is_written_beside_the_standing_window_never_over_it():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        standing = _standing()
        standing_path = tmp / "window-2026-09.json"
        slots_mod.write_window(standing_path, standing)

        proposal = _standing()
        proposal.slots[3].date = "2026-09-28"

        outcome = refresh_mod.refresh(
            standing, standing_path,
            criteria={"cadence": {"posts_per_week_max": 2}},
            taxonomy=_taxonomy(tmp), policy=_policy(tmp), lens="jordan",
            candidates=_candidates(CORPUS), context=_context(),
            entries=[], decisions={}, today=TODAY,
            force=True, caller=_returns(proposal),
        )
        assert outcome.ok, (outcome.violations, outcome.errors, outcome.failed)
        assert outcome.proposal_path.endswith("window-2026-09.proposed.json"), outcome.proposal_path
        assert slots_mod.read_window(standing_path).slots[3].date == "2026-10-01", (
            "the standing window must not have been rewritten"
        )
        print("ok  a proposal lands beside the standing calendar and never on it")


def test_a_proposal_is_not_mistaken_for_the_calendar():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for name in (
            "window-jordan-2026-09.json",
            "window-jordan-2026-09.proposed.json",
            "window-jordan-2026-09.rejected.json",
        ):
            (tmp / name).write_text("{}", encoding="utf-8")
        found = slots_mod.newest_window_path(tmp, lens="jordan")
        assert found and found.name == "window-jordan-2026-09.json", found
        print("ok  the page and the queue see the standing calendar, not a proposal")


def test_a_failed_call_reports_rather_than_writing_anything():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        standing_path = tmp / "window-2026-09.json"
        slots_mod.write_window(standing_path, _standing())

        def angry(*_args, **_kwargs):
            raise assemble_mod.AssemblyError("the model call failed")

        outcome = refresh_mod.refresh(
            _standing(), standing_path,
            criteria={"cadence": {"posts_per_week_max": 2}},
            taxonomy=_taxonomy(tmp), policy=_policy(tmp), lens="jordan",
            candidates=_candidates(CORPUS), context=_context(),
            entries=[], decisions={}, today=TODAY, force=True, caller=angry,
        )
        assert outcome.failed and outcome.ok is False, outcome
        assert outcome.proposal_path == "", "a failed call has nothing to write"
        assert not list(tmp.glob("*.proposed.json")), "no proposal file should exist"
        print("ok  a failed model call reports and writes nothing")


def test_an_unusable_policy_stops_the_run_rather_than_defaulting():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        empty = tmp / "empty.yaml"
        empty.write_text("version: 1\n", encoding="utf-8")
        assert refresh_mod.validate_policy(refresh_mod.load_policy(empty)), (
            "a policy with no triggers has to be reported as unusable"
        )
        assert refresh_mod.validate_policy({}), "an unreadable policy is unusable"
        assert refresh_mod.main([
            "--policy", str(empty),
            "--window", str(tmp / "nothing.json"),
            "--state-root", str(tmp / "state"),
        ]) == 1
        print("ok  an unusable policy stops the run instead of falling back to code defaults")


def test_the_run_is_recorded_in_the_shape_the_store_keeps():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "state"
        outcome = refresh_mod.Outcome(window_id="jordan-2026-09", standing_path="somewhere")
        outcome.supply = refresh_mod.Supply(corpus=6, unscheduled=["a/b"])
        state_store.record_state("refresh_runs", [outcome.to_entry(as_of=TODAY)], root=root)
        rows = state_store.read_state("refresh_runs", root=root, latest_per_key=True)
        assert len(rows) == 1 and rows[0].entry_key == "jordan-2026-09", rows
        assert rows[0].value["supply"]["corpus"] == 6, rows[0].value
        assert rows[0].value["as_of"] == TODAY, rows[0].value
        print("ok  a refresh run is one keyed row the store reads back")


def test_the_prompt_tells_the_model_which_dates_can_no_longer_move():
    standing = _standing()
    without = assemble_mod.build_user_content(
        _candidates(CORPUS[:1]), _context(), "2026-09-07", "2026-10-06", "Jordan", standing
    )
    with_date = assemble_mod.build_user_content(
        _candidates(CORPUS[:1]), _context(), "2026-09-07", "2026-10-06", "Jordan", standing,
        as_of=TODAY,
    )
    assert "cannot be" not in without, (
        "a plain assembly must not gain a refresh's rule"
    )
    assert TODAY in with_date, "the refresh has to tell the model what today is"
    assert "rescheduled" in with_date and "Slots dated before today" in with_date, (
        "the prompt has to say that a past date is fixed"
    )
    print("ok  a refresh tells the model today's date and what that fixes")


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
