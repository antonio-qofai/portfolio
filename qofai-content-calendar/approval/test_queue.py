"""Tests for the approval queue (PRD.md build order item 13).

Why this module gets tests. It is the only code in the agent that writes a
human's decision, and PRD Section 2 makes exactly one hard constraint:
nothing publishes without explicit human approval. Every failure mode here
is a variation on writing an approval nobody gave, or losing one somebody
did, and both are invisible from the outside. A queue that silently drops a
sign-off looks identical to a founder who has not got round to it.

So the assertions are mostly about refusal and about the join:

  - A decision that is not well formed is refused, not warned about. The
    store is append-only with no update call, so a bad row is permanent and
    a silently ignored one reads later as a slot nobody looked at. Covered
    for an unknown state, an unknown slot, an anonymous approval, and a
    rejection with no reason.
  - `published` can be set from here only as a checkable claim (2026-09-23):
    with the link to the post as it went out, and only on a post that is
    approved now. It is a fact about the outside world and v1 still does not
    publish, so without both conditions the calendar could claim something
    that did not happen.
  - The recorded decision names the post, not just the date. A slot_id
    outlives its content, so an approval that transfers to a swapped-in post
    is the exact failure this build exists to prevent. Asserted by swapping
    the item on an approved slot and checking `stale_decisions` says so.
  - Withdrawal works by appending, never by editing. Asserted on the history
    length as well as the current state, because an implementation that
    rewrote the row would pass a check on state alone.
  - The queue and the page agree on what is due. `pending` is asserted to
    follow a temporary taxonomy with deliberately different horizons, so a
    hardcoded fourteen cannot pass.

Run it standalone:

    ../.venv/bin/python3 test_queue.py

It needs neither pytest nor any dependency the folder does not already have,
though it runs under pytest if that is present.
"""
from __future__ import annotations

import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_AGENT_ROOT = _HERE.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_AGENT_ROOT))
sys.path.insert(0, str(_AGENT_ROOT / "calendar_model"))

import queue as queue_mod  # noqa: E402
import slots as slots_mod  # noqa: E402
import state_store  # noqa: E402

NOW = datetime(2026, 9, 12, 9, 0, tzinfo=timezone.utc)
TODAY = "2026-09-12"

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
  approved_horizon_days: {approved}
  draft_horizon_days: {draft}
"""


def _taxonomy(tmp: Path, *, approved: int = 14, draft: int = 30):
    path = tmp / "threads.yaml"
    path.write_text(TAXONOMY_YAML.format(approved=approved, draft=draft), encoding="utf-8")
    return slots_mod.load_taxonomy(path)


def _window(dates=("2026-09-14", "2026-09-17", "2026-10-05"), approvals=None):
    approvals = approvals or {}
    return slots_mod.CalendarWindow(
        window_id="jordan-2026-09",
        lens="jordan",
        start_date="2026-09-12",
        end_date="2026-10-11",
        arc="an arc",
        slots=[
            slots_mod.Slot(
                slot_id=f"jordan-{day}-01",
                date=day,
                item_ref=f"value-creation-briefing/post_{n}",
                lens="jordan",
                threads=["ai-spend-visibility"],
                forms=["research-anchored-gap"],
                approval=approvals.get(f"jordan-{day}-01", slots_mod.DRAFT),
                rationale="because it answers last week's post",
                extra={"alternatives": [{"item_ref": "content-atomizer/anchor-03", "why": "same thread"}]},
            )
            for n, day in enumerate(dates, start=1)
        ],
    )


def test_pending_lists_everything_undecided_soonest_first():
    """What is there to sign off, not what is due: nobody is behind (2026-09-19)."""
    with tempfile.TemporaryDirectory() as tmp:
        taxonomy = _taxonomy(Path(tmp))
        entries = queue_mod.pending(_window(), taxonomy, TODAY)
        assert [e.slot_id for e in entries] == [
            "jordan-2026-09-14-01",
            "jordan-2026-09-17-01",
            "jordan-2026-10-05-01",
        ], f"every undecided slot belongs in the queue, got {[e.slot_id for e in entries]}"
        assert entries[0].days_out == 2, entries[0].days_out
        assert entries[0].item_ref and entries[0].rationale, "a queue entry has to be decidable"
        assert entries[0].alternatives, "the swap candidates have to reach the queue"
        for field in ("required", "reason", "overdue"):
            assert not hasattr(entries[0], field), f"the queue grew {field} back"
        print("ok  the queue lists everything undecided, soonest first, and nothing is 'due'")


def test_the_horizons_no_longer_decide_what_is_in_the_queue():
    """The queue used to be the approved horizon's output. It is the store's now."""
    with tempfile.TemporaryDirectory() as tmp:
        narrow = _taxonomy(Path(tmp), approved=1, draft=10)
        wide = _taxonomy(Path(tmp), approved=40, draft=60)
        first = [e.slot_id for e in queue_mod.pending(_window(), narrow, TODAY)]
        second = [e.slot_id for e in queue_mod.pending(_window(), wide, TODAY)]
        assert first == second and len(first) == 3, (first, second)
        print("ok  changing either horizon changes nothing about what awaits a decision")


def test_an_approved_slot_leaves_the_queue():
    with tempfile.TemporaryDirectory() as tmp:
        taxonomy = _taxonomy(Path(tmp))
        window = _window(approvals={"jordan-2026-09-14-01": slots_mod.APPROVED})
        ids = [e.slot_id for e in queue_mod.pending(window, taxonomy, TODAY)]
        assert ids == ["jordan-2026-09-17-01", "jordan-2026-10-05-01"], ids
        print("ok  approving a slot takes it out of the queue")


def test_a_declined_slot_leaves_the_queue_and_is_reported_as_a_held_date():
    """Declining is a decision, so it leaves the queue. The date it holds is a finding."""
    with tempfile.TemporaryDirectory() as tmp:
        taxonomy = _taxonomy(Path(tmp))
        window = _window(approvals={"jordan-2026-10-05-01": slots_mod.REJECTED})
        ids = [e.slot_id for e in queue_mod.pending(window, taxonomy, TODAY)]
        assert "jordan-2026-10-05-01" not in ids, ids
        codes = [i.code for i in slots_mod.check_horizons(window, taxonomy, TODAY)]
        assert "declined-slot-holds-a-date" in codes, codes
        print("ok  a declined slot is decided, and the date it still holds is the finding")


def test_a_decision_is_recorded_where_the_page_reads_it():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "state"
        window = _window()
        result = queue_mod.decide(
            window, "jordan-2026-09-14-01", slots_mod.APPROVED,
            by="antonio", root=root, now=NOW,
        )
        assert result["ok"], result
        assert state_store.latest_approvals(root=root) == {
            "jordan-2026-09-14-01": slots_mod.APPROVED
        }
        joined = slots_mod.apply_approvals(window, state_store.latest_approvals(root=root))
        assert joined.slots[0].approval == slots_mod.APPROVED, (
            "the write has to land where apply_approvals reads it"
        )
        print("ok  a recorded decision joins onto the window through the documented path")


def test_the_decision_records_the_post_it_was_taken_against():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "state"
        window = _window()
        queue_mod.decide(
            window, "jordan-2026-09-14-01", slots_mod.APPROVED,
            by="antonio", root=root, now=NOW,
        )
        value = state_store.latest_decisions(root=root)["jordan-2026-09-14-01"]
        assert value["item_ref"] == "value-creation-briefing/post_1", value
        assert value["by"] == "antonio" and value["at"] and value["date"] == "2026-09-14", value

        assert queue_mod.stale_decisions(window, state_store.latest_decisions(root=root)) == []

        moved = _window()
        moved.slots[0].item_ref = "content-atomizer/anchor-07"
        stale = queue_mod.stale_decisions(moved, state_store.latest_decisions(root=root))
        assert len(stale) == 1 and stale[0]["decided_on"] == "value-creation-briefing/post_1", stale
        assert stale[0]["now_holds"] == "content-atomizer/anchor-07", stale
        print("ok  an approval that would transfer to a swapped-in post is reported as stale")


def test_a_malformed_decision_is_refused_rather_than_written():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "state"
        window = _window()
        cases = [
            (("jordan-2026-09-14-01", "maybe"), {"by": "antonio"}, "an unknown state"),
            (("no-such-slot", slots_mod.APPROVED), {"by": "antonio"}, "a slot not in the window"),
            (("jordan-2026-09-14-01", slots_mod.APPROVED), {"by": ""}, "nobody named"),
            (("jordan-2026-09-14-01", slots_mod.REJECTED), {"by": "antonio"}, "a rejection with no reason"),
            (("", slots_mod.APPROVED), {"by": "antonio"}, "no slot id"),
        ]
        for args, kwargs, what in cases:
            result = queue_mod.decide(window, *args, root=root, now=NOW, **kwargs)
            assert result["ok"] is False, f"{what} was accepted: {result}"
        assert state_store.latest_approvals(root=root) == {}, (
            "a refused decision must leave nothing behind"
        )
        print("ok  a decision that is not well formed is refused and writes nothing")


def test_published_needs_a_link_and_an_approval():
    link = "https://www.linkedin.com/posts/jordan_ai-activity-7123456789012345678-AbCd"
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "state"
        window, slot = _window(), "jordan-2026-09-14-01"

        no_link = queue_mod.decide(window, slot, slots_mod.PUBLISHED, by="antonio", root=root, now=NOW)
        assert no_link["ok"] is False, "published with no link is a claim nobody can check"

        on_draft = queue_mod.decide(
            window, slot, slots_mod.PUBLISHED, by="antonio", url=link, root=root, now=NOW,
        )
        assert on_draft["ok"] is False, "a draft cannot have gone out from this calendar"
        assert "approved" in on_draft["detail"], on_draft["detail"]

        not_a_link = queue_mod.decide(
            window, slot, slots_mod.PUBLISHED, by="antonio", url="linkedin post", root=root, now=NOW,
        )
        assert not_a_link["ok"] is False, "a link has to be a web address"
        assert not (root / "approvals.jsonl").exists(), "a refused publish wrote to the store"

        queue_mod.decide(window, slot, slots_mod.APPROVED, by="antonio", root=root, now=NOW)
        done = queue_mod.decide(
            window, slot, slots_mod.PUBLISHED, by="antonio", url=link, root=root, now=NOW,
        )
        assert done["ok"] is True, done["detail"]
        assert done["value"]["url"] == link and done["value"]["state"] == slots_mod.PUBLISHED, done
        print("ok  published is recorded only with a link, and only on an approved post")


def test_a_rejection_carries_its_reason():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "state"
        result = queue_mod.decide(
            _window(), "jordan-2026-09-14-01", slots_mod.REJECTED,
            by="antonio", note="reruns the Deloitte figure from last week",
            root=root, now=NOW,
        )
        assert result["ok"], result
        value = state_store.latest_decisions(root=root)["jordan-2026-09-14-01"]
        assert value["note"].startswith("reruns"), value
        print("ok  a rejection records why, because the refresh re-proposes against it")


def test_withdrawing_an_approval_appends_rather_than_edits():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "state"
        window = _window()
        queue_mod.decide(window, "jordan-2026-09-14-01", slots_mod.APPROVED,
                         by="antonio", root=root, now=NOW)
        queue_mod.decide(window, "jordan-2026-09-14-01", slots_mod.DRAFT,
                         by="antonio", note="Jordan changed his mind", root=root, now=NOW)

        assert state_store.latest_approvals(root=root) == {
            "jordan-2026-09-14-01": slots_mod.DRAFT
        }, "the newest decision has to win"
        history = state_store.read_state("approvals", root=root, entry_key="jordan-2026-09-14-01")
        assert len(history) == 2, f"the approval must still be in the history, got {history}"
        assert queue_mod.pending(
            queue_mod.current(window, root=root), _taxonomy(Path(tmp)), TODAY
        )[0].slot_id == "jordan-2026-09-14-01", "a withdrawn approval returns to the queue"
        print("ok  withdrawing an approval appends a newer decision and keeps the old one")


def test_current_overlays_the_store_on_the_window():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "state"
        window = _window()
        assert all(s.approval == slots_mod.DRAFT for s in window.slots)
        queue_mod.decide(window, "jordan-2026-09-17-01", slots_mod.APPROVED,
                         by="antonio", root=root, now=NOW)
        standing = queue_mod.current(window, root=root)
        assert [s.approval for s in standing.slots] == [
            slots_mod.DRAFT, slots_mod.APPROVED, slots_mod.DRAFT
        ]
        assert [s.approval for s in window.slots] == [slots_mod.DRAFT] * 3, (
            "the window it was handed must not be mutated"
        )
        print("ok  the store is laid over the window without changing the file's own state")


def test_the_cli_lists_and_decides_and_refuses():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        root = tmp / "state"
        root.mkdir(parents=True)
        window_path = root / "window-2026-09.json"
        slots_mod.write_window(window_path, _window())
        taxonomy_path = tmp / "threads.yaml"
        taxonomy_path.write_text(TAXONOMY_YAML.format(approved=14, draft=30), encoding="utf-8")
        base = [
            "--window", str(window_path),
            "--taxonomy", str(taxonomy_path),
            "--state-root", str(root),
            "--as-of", TODAY,
        ]
        assert queue_mod.main(base) == 0, "listing the queue must succeed"
        assert queue_mod.main(base + ["--approve", "jordan-2026-09-14-01"]) == 1, (
            "an approval with no --by must fail"
        )
        assert state_store.latest_approvals(root=root) == {}
        assert queue_mod.main(
            base + ["--approve", "jordan-2026-09-14-01", "--by", "antonio", "--dry-run"]
        ) == 0
        assert state_store.latest_approvals(root=root) == {}, "--dry-run must write nothing"
        assert queue_mod.main(base + ["--approve", "jordan-2026-09-14-01", "--by", "antonio"]) == 0
        assert state_store.latest_approvals(root=root) == {
            "jordan-2026-09-14-01": slots_mod.APPROVED
        }
        assert queue_mod.main(
            base + ["--approve", "jordan-2026-09-17-01", "--reject", "jordan-2026-09-14-01", "--by", "x"]
        ) == 1, "two decisions in one call must be refused"
        print("ok  the CLI lists, refuses an anonymous or doubled decision, and records a good one")


def test_the_newest_window_is_found_the_same_way_the_page_finds_it():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name in (
            "window-jordan-2026-08.json",
            "window-jordan-2026-09.json",
            "window-jordan-2026-09.rejected.json",
            # A second calendar, and one whose lens sorts after "jordan", which
            # is the case a lens-blind lookup gets wrong while looking right.
            "window-zoe-2026-09.json",
            # Written before the lens-first convention, so deliberately
            # invisible: the calendar points at the lens windows now.
            "window-2026-09.json",
        ):
            (root / name).write_text("{}", encoding="utf-8")
        found = slots_mod.newest_window_path(root, lens="jordan")
        assert found and found.name == "window-jordan-2026-09.json", found
        other = slots_mod.newest_window_path(root, lens="zoe")
        assert other and other.name == "window-zoe-2026-09.json", other
        assert slots_mod.newest_window_path(root, lens="casey") is None
        assert slots_mod.newest_window_path(root / "nowhere", lens="jordan") is None
        print("ok  the current window is the newest by name for that lens, never a "
              "rejected one and never another lens's calendar")


def test_a_decision_on_this_month_lands_while_next_month_exists():
    """The newest file alone would have been November, and refused October's slot."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        october = slots_mod.CalendarWindow(
            window_id="jordan-2026-10-01", lens="jordan",
            start_date="2026-10-01", end_date="2026-10-30",
            slots=[slots_mod.Slot(slot_id="jordan-2026-10-01-09", date="2026-10-29",
                                  item_ref="value-creation-briefing/post_1", lens="jordan",
                                  threads=["ai-spend-visibility"],
                                  forms=["research-anchored-gap"])],
        )
        november = slots_mod.CalendarWindow(
            window_id="jordan-2026-11-01", lens="jordan",
            start_date="2026-11-01", end_date="2026-11-30",
        )
        slots_mod.write_window(root / "window-jordan-2026-10.json", october)
        slots_mod.write_window(root / "window-jordan-2026-11.json", november)
        taxonomy = _taxonomy(root)
        base = ["--lens", "jordan", "--state-root", str(root),
                "--taxonomy", taxonomy.source_path, "--as-of", "2026-10-16"]
        assert queue_mod.main(base) == 0
        assert queue_mod.main(base + ["--approve", "jordan-2026-10-01-09", "--by", "antonio"]) == 0, \
            "an October slot could not be decided once November existed"
        decisions = state_store.latest_decisions(root=root)
        assert "jordan-2026-10-01-09" in decisions, decisions
    print("ok  this month's slots stay decidable after next month is assembled")


def test_the_page_does_not_claim_nothing_is_approved_once_something_is():
    """The regression from 2026-09-12, and the second of its kind.

    The page's honesty callout is the first thing a founder reads, and it was
    hardcoded to say none of the calendar was approved. That was true while
    nothing could be approved and became false the moment this module could
    write one, exactly as the same note had been hardcoded to "hand-built"
    until synthesis existed. A page that misstates its own approval state is
    worse than one that shows no state at all, so the claim is asserted here
    rather than left to be noticed by whoever opens it next.
    """
    sys.path.insert(0, str(_AGENT_ROOT / "display"))
    import render_calendar

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        root = tmp / "state"
        taxonomy = _taxonomy(tmp)
        window = _window()

        before = render_calendar.render_html(
            window, taxonomy, today=date.fromisoformat(TODAY), agent_decided=True
        )
        # The claim moved on 2026-09-21. The "Read this first" callout that
        # carried "none of it is approved" was deleted for being the largest
        # block of text on the page, and the counts it computed are now three
        # words in the header. The invariant is unchanged and is what this
        # tests: the page must never misstate its own approval state. With
        # nothing decided it says nothing, rather than saying "0 approved",
        # since approval stopped being something this calendar asks for on
        # 2026-09-19.
        assert "approved" not in before.split("<main>")[1].split("</header>")[0], (
            "the header claims an approval on a window where nothing is decided"
        )

        queue_mod.decide(window, "jordan-2026-09-14-01", slots_mod.APPROVED,
                         by="antonio", root=root, now=NOW)
        standing = queue_mod.current(window, root=root)
        after = render_calendar.render_html(
            standing, taxonomy, today=date.fromisoformat(TODAY), agent_decided=True
        )
        assert "1 approved" in after, after[:400]
        print("ok  the page stops claiming nothing is approved once a decision is recorded")


def test_the_page_offers_a_decision_without_ever_claiming_one_was_recorded():
    """The page collects decisions; only `queue.py` records them.

    The interaction it added on 2026-09-13 is the third time this page has had
    to be stopped from overstating its own state, after the hand-built
    disclaimer and the approved-count one above. A button labelled Approve is
    the most tempting version of that mistake: clicking it must not change the
    badge, because the badge is what the approval store says and the store has
    not been told anything yet.
    """
    sys.path.insert(0, str(_AGENT_ROOT / "display"))
    import render_calendar

    with tempfile.TemporaryDirectory() as tmp:
        taxonomy = _taxonomy(Path(tmp))
        page = render_calendar.render_html(
            _window(), taxonomy, today=date.fromisoformat(TODAY), agent_decided=True
        )
        for needed in (
            'class="decide"',
            f'data-decide="{slots_mod.APPROVED}"',
            f'data-decide="{slots_mod.REJECTED}"',
            'data-act="copy-decisions"',
            "declinerow",
        ):
            assert needed in page, f"the page is missing {needed}"

        assert "records nothing" in page, (
            "the bar has to say that clicking decides nothing, in its own words"
        )
        assert "A decline needs a reason" in page, (
            "the page has to state the rule queue.py enforces, or a refused "
            "command is the first time anyone hears about it"
        )
        # Since 2026-09-20 a moved post can be decided on, because a move is
        # saved on its own through /reschedule. The old refusal must not return.
        assert "before deciding on a moved post" not in page, (
            "a moved post can carry a decision, so the page must not refuse one"
        )
        # Every badge on a page rendered from an all-draft window says draft.
        # If a future change makes the button write the badge, this fails.
        assert page.count('class="badge state-approved"') == 0, (
            "no slot in this window is approved, so no badge may say so"
        )
        print("ok  the page collects a decision and never claims it was recorded")


def test_the_pages_commands_match_what_the_queue_requires():
    """The page's generated command and the queue's rules cannot drift apart.

    The page emits `queue.py` commands, so the flags it writes have to be
    flags the queue takes. Asserted by running the exact argument list the
    page builds, rather than by reading the JavaScript.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        root = tmp / "state"
        root.mkdir(parents=True)
        window_path = root / "window-2026-09.json"
        slots_mod.write_window(window_path, _window())
        taxonomy_path = tmp / "threads.yaml"
        taxonomy_path.write_text(TAXONOMY_YAML.format(approved=14, draft=30), encoding="utf-8")
        base = [
            "--window", str(window_path),
            "--taxonomy", str(taxonomy_path),
            "--state-root", str(root),
            "--as-of", TODAY,
        ]
        # The two command shapes the page writes, with an apostrophe in the
        # note because that is the character that breaks shell quoting and it
        # is the one a real reason is most likely to contain.
        assert queue_mod.main(
            base + ["--approve", "jordan-2026-09-14-01", "--by", "antonio"]
        ) == 0
        assert queue_mod.main(
            base + ["--reject", "jordan-2026-09-17-01", "--by", "antonio",
                    "--note", "reruns Jordan's own figure"]
        ) == 0
        recorded = state_store.latest_decisions(root=root)
        assert recorded["jordan-2026-09-14-01"]["state"] == slots_mod.APPROVED
        assert recorded["jordan-2026-09-17-01"]["note"] == "reruns Jordan's own figure", (
            "the reason has to survive the trip intact, apostrophe and all"
        )
        print("ok  the commands the page writes are commands the queue accepts")


def test_a_missing_window_reports_rather_than_crashing():
    with tempfile.TemporaryDirectory() as tmp:
        assert queue_mod.main(["--state-root", str(Path(tmp) / "state")]) == 1
        print("ok  no assembled window reports and exits non-zero rather than raising")


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
