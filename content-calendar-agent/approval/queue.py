"""The human approval queue: what needs deciding, and the record of deciding it.

Build order item 13 in PRD.md Section 5. This is the half of the approval
path that writes.

What this module is not
-----------------------
It does not write posts, edit them, or judge them. C2 is a synthesis agent:
Alex's Content Atomizer and Robin's Value Creation Briefing produce the
content, and this agent decides which of it goes on which date. A decision
recorded here is about a date and a sign-off, never about prose. The only
content this file ever touches is an `item_ref`, which is a pointer of the
form "<source-agent>/<item-id>" and never the text itself.

Why it exists
-------------
PRD Section 2 makes one hard constraint: nothing publishes without explicit
human approval, and the next one to two weeks are always fully approved. As
of 2026-09-07 the reading half of that was built and the writing half was
not. `slots.apply_approvals` joins recorded approvals onto a window and
`display/render_calendar.py --state-root` overlays them on the page, but no
caller anywhere appended an approval, so the store those two read had never
been created. The loop was wired to an empty socket: the page reported nine
drafts, the standing rule said two of them were due, and a human agreeing
had nowhere to put it. This module closes that.

The shape of the interaction, from the founder call on 2026-08-19: Jordan
wants a queue rather than a review. Tell him he needs a post on Monday, show
him what fits, take his answer. So `pending()` answers "what needs you
today" in date order and `decide()` records the answer.

What a decline can actually do
------------------------------
Checked against the real window on 2026-09-12: all eight distinct
alternatives it offers across nine slots point at items already scheduled on
another date in the same window. That follows from nine schedulable items
landing on nine posting days, and it matches the owner's 2026-09-07 decision
that alternatives are swap candidates rather than options. So declining
cannot pull in an unscheduled post, because there is no such post. It leaves
the date empty for the weekly refresh (item 17) to re-propose against
whatever has arrived since. That is why `decide()` records a rejection and
stops there rather than pretending to fill the hole: reordering the calendar
is a change to the plan, which is a window file, and approval state is an
overlay on top of one. Conflating them would let a sign-off silently rewrite
what was signed off.

Why the recorded decision names the post
----------------------------------------
Approval is keyed by slot_id and a slot_id outlives its content: swap two
dates and the approval stays attached to the date while the post moves. So
every decision records the `item_ref` that was on the slot when the human
looked at it, and `stale_decisions()` reports any slot whose window content
has changed since. An approval that silently transfers to a post nobody
approved is the one failure this build exists to prevent, and without the
recorded ref it is undetectable rather than merely unlikely.

Degradation follows this folder's convention. Every read returns something
usable with a warning on stderr rather than raising. Writes are the
exception and are refused outright when the decision is not well formed,
because the store is append-only and has no update call, so a bad row is
permanent and a silently ignored one reads later as an approval nobody can
explain.

Run it standalone:

    ../.venv/bin/python3 queue.py
    ../.venv/bin/python3 queue.py --approve jordan-2026-09-07-01 --by antonio
    ../.venv/bin/python3 queue.py --reject jordan-2026-09-10-01 --by antonio \
        --note "reruns the Deloitte figure from last week"
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_AGENT_ROOT = _HERE.parent

sys.path.insert(0, str(_AGENT_ROOT / "calendar_model"))

import slots as slots_mod  # noqa: E402
import state_store  # noqa: E402
from slots import (  # noqa: E402
    APPROVAL_STATES,
    APPROVED,
    DRAFT,
    PUBLISHED,
    REJECTED,
    CalendarWindow,
    Taxonomy,
)

DEFAULT_STATE_ROOT = state_store.DEFAULT_STATE_ROOT
APPROVALS_STORE = state_store.DEFAULT_APPROVALS_STORE

# The action written on every row. One value, because the store is shared
# with the ingestion log and the run log and a reader filtering by action
# needs decisions to be one thing rather than four spellings of one thing.
DECISION_ACTION = "approval"

# There is no due date here, and no reason column, since 2026-09-19. The
# queue used to be built from `check_horizons`: a slot was listed because a
# rule said a decision was due on it, and it was flagged OVERDUE once that
# date passed. Antonio's call is that approving posts is a feature for the
# founders rather than something the agent requires, so the queue lists what
# nobody has decided yet, in date order, and says nothing about lateness.

# States a human decision may write.
#
# `published` joined on 2026-09-23, by owner decision, and on two conditions.
# Until then it was deliberately left out: it is a fact about the outside
# world, and v1 does not publish, so accepting it would let the calendar claim
# something happened that did not. That reason still holds, which is why the
# conditions exist. The agent still never posts anything. What changed is
# that a person can now record that a post went out, and a claim that a post
# went out has to be checkable: so it needs the link to the post as it went
# out, and only a post someone approved can have gone out from this calendar.
DECIDABLE_STATES = (APPROVED, REJECTED, DRAFT, PUBLISHED)

# What a published link must look like. A web address and nothing narrower:
# the calendar is LinkedIn today, and nothing here should break the day a
# post goes out somewhere else.
_LINK = re.compile(r"^https?://\S+$", re.IGNORECASE)


def _warn(message: str) -> None:
    print(f"queue: {message}", file=sys.stderr)


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------
# What needs deciding
# ---------------------------------------------------------------------

@dataclass
class QueueEntry:
    """One slot awaiting a human, with what a person needs to decide it."""

    slot_id: str
    date: str
    item_ref: str
    current: str
    days_out: int | None = None
    rationale: str = ""
    alternatives: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "slot_id": self.slot_id,
            "date": self.date,
            "item_ref": self.item_ref,
            "current": self.current,
            "days_out": self.days_out,
            "rationale": self.rationale,
            "alternatives": list(self.alternatives),
        }


def pending(
    window: CalendarWindow,
    taxonomy: Taxonomy,
    today: str | date,
) -> list:
    """Slots nobody has decided on yet, soonest first.

    A slot is here because it is still a draft, not because a rule says a
    decision is due: this is a list of what is available to sign off, and a
    founder who signs off nothing is not behind on anything. Approved,
    declined and published slots have all had their decision and are not
    listed. `taxonomy` is unused since 2026-09-19 and kept so no caller had
    to change.
    """
    reference = slots_mod.as_date(today)
    entries = []
    for slot in window.slots:
        if slot.approval != DRAFT:
            continue
        target = slots_mod.as_date(slot.date)
        entries.append(
            QueueEntry(
                slot_id=slot.slot_id,
                date=slot.date,
                item_ref=slot.item_ref,
                current=slot.approval,
                days_out=(target - reference).days if target and reference else None,
                rationale=slot.rationale,
                alternatives=list((slot.extra or {}).get("alternatives") or []),
            )
        )
    entries.sort(key=lambda e: (e.date, e.slot_id))
    return entries


def stale_decisions(window: CalendarWindow, decisions: dict) -> list:
    """Slots whose recorded decision was taken against a different post.

    A decision is keyed by slot_id, and a slot_id is a date's identity rather
    than a post's. If the plan moves another item onto that date, the stored
    approval would otherwise transfer to content nobody approved. Returns one
    dict per affected slot naming both refs, for a caller to report. Silent
    when the decision recorded no ref, which is true of anything hand-appended
    before this module existed: unknown is not evidence of a mismatch.
    """
    stale = []
    for slot in window.slots:
        value = decisions.get(slot.slot_id)
        if not isinstance(value, dict):
            continue
        decided_on = value.get("item_ref")
        if not decided_on or decided_on == slot.item_ref:
            continue
        stale.append(
            {
                "slot_id": slot.slot_id,
                "date": slot.date,
                "state": value.get("state"),
                "decided_on": decided_on,
                "now_holds": slot.item_ref,
                "by": value.get("by"),
                "at": value.get("at"),
            }
        )
    return stale


# ---------------------------------------------------------------------
# Recording a decision
# ---------------------------------------------------------------------

def decide(
    window: CalendarWindow | None,
    slot_id: str,
    state: str,
    *,
    by: str,
    note: str = "",
    url: str = "",
    root: str | Path = DEFAULT_STATE_ROOT,
    store: str = APPROVALS_STORE,
    now: datetime | None = None,
    event_at: str | None = None,
) -> dict:
    """Appends one human decision about one slot. Returns a result dict.

    `{"ok": bool, "slot_id": ..., "state": ..., "detail": ...}`. Refuses
    rather than warns, because the store has no update call: a row written
    here cannot be taken back, only superseded, and a decision that fails
    silently would read later as a slot nobody ever looked at.

    Four things are checked before anything is written. The state has to be
    one a human may set. `by` has to name someone, because an approval with
    no one behind it does not satisfy "explicit human approval" in any sense
    worth having. The slot has to exist in the window, when a window is
    given, since a typo'd id writes a decision that joins onto nothing and
    is invisible forever after. And a rejection has to carry a note, because
    it empties a date and the refresh that re-proposes it needs to know what
    was wrong with the last answer.

    Marking a post published (2026-09-23) needs two more: a web link to the
    post as it went out, and the post has to be approved now, according to
    the store rather than to the window file, since the file never records a
    decision and the CLI reads windows without the store laid over them.

    Passing `window=None` skips only the existence check, for a caller that
    genuinely has no window in hand. Everything else still applies.
    """
    slot_id = (slot_id or "").strip()
    state = (state or "").strip()
    by = (by or "").strip()
    note = (note or "").strip()
    url = (url or "").strip()

    def refused(detail: str) -> dict:
        _warn(f"refusing to record: {detail}")
        return {"ok": False, "slot_id": slot_id, "state": state, "detail": detail}

    if not slot_id:
        return refused("no slot id given")
    if state not in DECIDABLE_STATES:
        return refused(
            f"{state!r} is not a decision a human may record "
            f"({', '.join(DECIDABLE_STATES)})"
        )
    if not by:
        return refused(f"no one named for the {state} of {slot_id}; --by is required")

    slot = None
    if window is not None:
        slot = next((s for s in window.slots if s.slot_id == slot_id), None)
        if slot is None:
            return refused(f"{slot_id} is not a slot in {window.window_id}")
    if state == REJECTED and not note:
        return refused(
            f"a rejection needs a note saying what was wrong with {slot_id}; "
            "the date it empties gets re-proposed against it"
        )
    if state == PUBLISHED:
        if not _LINK.match(url):
            return refused(
                f"marking {slot_id} published needs the link to the post as it went out "
                "(an http or https address); --url is required"
            )
        latest = state_store.latest_decisions(store, root=root).get(slot_id) or {}
        now_state = latest.get("state") or (slot.approval if slot is not None else "")
        if now_state != APPROVED:
            return refused(
                f"{slot_id} is {now_state or 'undecided'}, and only an approved post can "
                "be marked published"
            )

    moment = _now(now)
    value = {
        "state": state,
        "by": by,
        "at": _iso(moment),
    }
    if note:
        value["note"] = note
    if state == PUBLISHED:
        value["url"] = url
    if slot is not None:
        value["item_ref"] = slot.item_ref
        value["date"] = slot.date
        value["was"] = slot.approval
    if window is not None:
        value["window_id"] = window.window_id

    written = state_store.record_state(
        store,
        [
            {
                "action": DECISION_ACTION,
                "entry_key": slot_id,
                "event_at": event_at or _iso(moment),
                "value": value,
            }
        ],
        root=root,
        now=moment,
    )
    if not written.get("ok"):
        return refused(f"the store would not accept the decision about {slot_id}")
    return {
        "ok": True,
        "slot_id": slot_id,
        "state": state,
        "detail": f"{slot_id} recorded as {state} by {by}",
        "value": value,
    }


def current(
    window: CalendarWindow,
    *,
    root: str | Path = DEFAULT_STATE_ROOT,
    store: str = APPROVALS_STORE,
    decisions: dict | None = None,
) -> CalendarWindow:
    """The window as it stands once recorded decisions are laid over it.

    The window file says what was planned and the store says what a human
    has since decided, and the store wins because it is the newer statement.

    `decisions` is there for a caller that has already read them and wants
    the full payload too, so asking both questions costs one read.
    """
    if decisions is None:
        decisions = state_store.latest_decisions(store, root=root)
    return slots_mod.apply_approvals(window, state_store.states_of(decisions))


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------

def _resolve_lens(args, taxonomy) -> str | None:
    """Which calendar this invocation is acting on.

    `--lens` wins. Otherwise the taxonomy's v1 lenses decide, and a single
    one is taken silently while several is refused rather than guessed.
    Guessing here would record a real human decision against whichever
    calendar happened to sort first, and an approval on the wrong post is
    the one failure this module exists to prevent.
    """
    if args.lens:
        return str(args.lens)
    v1 = sorted(taxonomy.v1_lens_ids)
    if len(v1) == 1:
        return v1[0]
    if not v1:
        _warn(f"{args.taxonomy} declares no v1 lens; pass --lens.")
        return None
    _warn(
        f"{args.taxonomy} declares several calendars ({', '.join(v1)}). "
        "Pass --lens to say which one you are deciding on."
    )
    return None


def _load(args, today: str) -> tuple:
    """(taxonomy, [(window, path), ...]) for every live window of the lens.

    Live means not fully passed as of `today`, via `slots.live_window_paths`.
    From the day next month is assembled until this month ends a lens has
    two, and this month's second half still needs deciding while next month
    is read, so the queue lists both and a decision goes to whichever holds
    the slot. `--window` still names exactly one.
    """
    taxonomy = slots_mod.load_taxonomy(args.taxonomy)
    if not taxonomy.ok:
        _warn(f"could not read the taxonomy at {args.taxonomy}; horizons are unknown.")

    if args.window:
        paths = [Path(args.window)]
    else:
        lens = _resolve_lens(args, taxonomy)
        if lens is None:
            return None, []
        paths = slots_mod.live_window_paths(Path(args.state_root), lens=lens, today=today)
        if not paths:
            _warn(
                f"no assembled window for the {lens} lens under {args.state_root} covers "
                f"{today} or later; run synthesis/monthly_assembly.py first."
            )
            return None, []
    loaded = []
    for path in paths:
        window = slots_mod.read_window(path)
        if window is None:
            return None, []
        loaded.append((window, path))
    return taxonomy, loaded


def _print_queue(entries: list, window: CalendarWindow, today: str) -> None:
    if not entries:
        print(f"Nothing awaiting a decision in {window.window_id} as of {today}.")
        return
    print(f"{len(entries)} slot(s) awaiting a decision in {window.window_id}, as of {today}:\n")
    for entry in entries:
        when = f"{entry.date}"
        if entry.days_out is not None:
            when += f" ({entry.days_out:+d}d)" if entry.days_out else " (today)"
        print(f"  {entry.slot_id}")
        print(f"    {when}  {entry.current}")
        print(f"    {entry.item_ref}")
        if entry.rationale:
            print(f"    why here: {entry.rationale[:160]}")
        for alt in entry.alternatives:
            ref = alt.get("item_ref") if isinstance(alt, dict) else alt
            print(f"    swap: {ref}")
        print()
    print("Approve with:  queue.py --approve <slot_id> --by <name>")
    print("Decline with:  queue.py --reject <slot_id> --by <name> --note <why>")
    print("Published:     queue.py --publish <slot_id> --by <name> --url <link>")


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="List what needs a human decision, and record the decision."
    )
    parser.add_argument("--window", default=None,
                        help="window file (default: every live window for --lens in state/)")
    parser.add_argument("--lens", default=None,
                        help="which calendar to act on (default: the v1 lens, if there is one)")
    parser.add_argument("--taxonomy", default=str(slots_mod.DEFAULT_TAXONOMY_PATH))
    parser.add_argument("--state-root", default=str(DEFAULT_STATE_ROOT))
    parser.add_argument("--store", default=APPROVALS_STORE)
    parser.add_argument("--as-of", default=None, help="reference date (default: today)")
    parser.add_argument("--approve", default=None, metavar="SLOT_ID")
    parser.add_argument("--reject", default=None, metavar="SLOT_ID")
    parser.add_argument("--unapprove", default=None, metavar="SLOT_ID",
                        help="return a slot to draft, withdrawing an approval")
    parser.add_argument("--publish", default=None, metavar="SLOT_ID",
                        help="record that an approved slot went out; needs --url")
    parser.add_argument("--url", default="", help="the published post's link; required for --publish")
    parser.add_argument("--by", default=None, help="who is deciding; required to decide")
    parser.add_argument("--note", default="", help="why; required for a rejection")
    parser.add_argument("--dry-run", action="store_true",
                        help="say what would be recorded, write nothing")
    args = parser.parse_args(argv)

    today = args.as_of or date.today().isoformat()
    taxonomy, loaded = _load(args, today)
    if not loaded:
        return 1

    decisions = [
        (args.approve, APPROVED),
        (args.reject, REJECTED),
        (args.unapprove, DRAFT),
        (args.publish, PUBLISHED),
    ]
    asked = [(slot_id, state) for slot_id, state in decisions if slot_id]

    if not asked:
        decisions = state_store.latest_decisions(args.store, root=args.state_root)
        for index, (window, window_path) in enumerate(loaded):
            if index:
                print()
            standing = current(window, decisions=decisions)
            entries = pending(standing, taxonomy, today)
            print(f"Window {standing.start_date} to {standing.end_date}, from {window_path}")
            _print_queue(entries, standing, today)
            for row in stale_decisions(standing, decisions):
                _warn(
                    f"{row['slot_id']} was {row['state']} against {row['decided_on']} "
                    f"and now holds {row['now_holds']}; that decision does not carry over."
                )
        return 0

    if len(asked) > 1:
        _warn("one decision at a time; pass a single --approve, --reject, --unapprove or --publish.")
        return 1

    slot_id, state = asked[0]
    # The slot id names its window (`<window_id>-<n>`), so the window a
    # decision applies to is found rather than assumed. With no match the
    # first window goes to `decide`, whose own existence check refuses it.
    window = next(
        (w for w, _ in loaded if any(s.slot_id == slot_id for s in w.slots)),
        loaded[0][0],
    )
    if args.dry_run:
        slot = next((s for s in window.slots if s.slot_id == slot_id), None)
        if slot is None:
            _warn(f"{slot_id} is not a slot in {window.window_id}")
            return 1
        print(f"Would record {slot_id} as {state}, by {args.by or '<nobody>'}, "
              f"holding {slot.item_ref} on {slot.date}. Nothing written.")
        return 0

    result = decide(
        window,
        slot_id,
        state,
        by=args.by or "",
        note=args.note,
        url=args.url,
        root=args.state_root,
        store=args.store,
    )
    print(result["detail"])
    if not result["ok"]:
        return 1

    standing = current(window, root=args.state_root, store=args.store)
    left = pending(standing, taxonomy, today)
    print(f"{len(left)} slot(s) still awaiting a decision.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
