"""The append-only store where C2's daily runs accumulate.

Build order item 8 in PRD.md Section 5, the half that persists. The shape of
what gets stored is slots.py alongside this file.

Why this exists at all
----------------------
C2 runs daily and assembles monthly (PRD Section 5, runtime). A GitHub
Actions runner's disk does not survive the run, so with no store every
morning starts blank and the month-end assembly has nothing to assemble.
Three other build-order items depend on it: gap detection (item 11) can only
know the Value Creation Briefing is late by remembering it never arrived,
deduplication needs somewhere to have seen an id before, and approval state
has to outlive the run that produced it.

Why not the GitHub Actions cache
--------------------------------
Because it evicts after 7 days unused, so a monthly assembly silently starts
from nothing and reports "no new inputs" rather than "I lost my baseline".
The portal's own contract records this as a failure QofAI has already hit
once, in exactly these words: a cap reading its history off a file that is
not there concludes nothing has been sent and releases the full allowance,
every run. So this store is append-only files inside this folder, written by
the run and committed back by the workflow (build order item 12). Git
history is then the audit trail for free, and a lost record is a visible
diff rather than a silent zero.

Why this exact interface
------------------------
The QofAI Agent Portal has a durable per-agent state store with precisely
this shape: `record_state` to append, `read_state` to read back with
`latest_per_key` for a snapshot, `state_stores` to ask whether anything is
being persisted at all, an entry of {action, quantity, entry_key, event_at,
value}, and no update call anywhere. Portal onboarding is deferred by a
recorded decision (PRD Section 3) because the two agents supplying the
calendar's actual content do not publish there yet. Matching the portal's
shape now makes that later swap a module replacement rather than a rewrite:
a portal-backed version of this file keeps the same three function names and
builds StateEntry from the JSON rows the portal returns. The interface is
read here and reimplemented, never imported, since C2 is not onboarded and
must not call the portal.

Append-only, and there is no update call
----------------------------------------
Correcting something means appending a newer entry, and readers take the
newest. That is the portal's rule and it is kept here deliberately rather
than copied thoughtlessly: an update call would let a run rewrite yesterday,
which is precisely what an audit trail must not permit, and it would make
git history stop meaning what happened.

Conventional stores for C2, none of them registered anywhere; a store exists
once something writes to it:

    ingestion_log   one entry per input item seen, keyed by its stable id.
                    Deduplication is read_state(..., latest_per_key=True).
    approvals       one entry per human decision, keyed by slot_id, with the
                    new state in `value`. This is what makes approval
                    survive a run:

                        window = slots.apply_approvals(window, latest_approvals())

                    `approval/queue.py` is the only thing that writes it.
                    Reading it is `latest_approvals` for the states alone or
                    `latest_decisions` for who decided and what they were
                    looking at when they did.

    windows         one entry per assembled window, keyed by window_id.

Degradation follows this folder's convention: an unwritable directory, a
missing store, a corrupt line, or an unusable store name each produce a
warning on stderr and a degraded result, never an exception. A daily run
must not die on a bad line in a log.

One caveat worth stating rather than discovering. An empty read means the
store is genuinely empty *or* the read failed, and a failure prints a line
above it. Do not read an empty list as "nothing has happened" without
looking; that conflation is the failure this whole module exists to prevent.

Verified 2026-08-18: entries survive a fresh process, ordering is newest
first, latest_per_key collapses correctly, and a corrupt line is skipped
with a warning rather than taking down the read.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

_HERE = Path(__file__).resolve().parent
_AGENT_ROOT = _HERE.parent

# Committed back by the daily workflow, which is the point: the store has to
# outlive the runner, and a tracked file is the cheapest durable thing that
# also explains itself in a diff.
DEFAULT_STATE_ROOT = _AGENT_ROOT / "state"

STORE_SUFFIX = ".jsonl"
_STORE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

__all__ = [
    "StateEntry",
    "record_state",
    "read_state",
    "state_stores",
    "store_path",
    "DEFAULT_STATE_ROOT",
]


def _warn(message: str) -> None:
    print(f"state_store: {message}", file=sys.stderr)


@dataclass
class StateEntry:
    """One appended fact. `id` is its position in the store, 1-based.

    `event_at` is when the thing happened and `created_at` is when this
    store wrote it down. They are separate, as they are in the portal, so a
    backfill or a replayed write is visible as one instead of reading as
    today's activity.
    """

    id: int
    store: str
    event_at: str  # ISO 8601 UTC
    created_at: str  # ISO 8601 UTC
    action: str | None = None
    quantity: int = 1
    entry_key: str | None = None
    value: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = {
            "id": self.id,
            "event_at": self.event_at,
            "created_at": self.created_at,
            "action": self.action,
            "quantity": self.quantity,
            "entry_key": self.entry_key,
            "value": self.value,
        }
        for key, item in (self.extra or {}).items():
            if key not in data and key != "store":
                data[key] = item
        return data

    @classmethod
    def from_row(cls, row: dict, store: str) -> "StateEntry":
        known = {"id", "event_at", "created_at", "action", "quantity", "entry_key", "value"}
        value = row.get("value")
        return cls(
            id=int(row.get("id") or 0),
            store=store,
            event_at=str(row.get("event_at") or ""),
            created_at=str(row.get("created_at") or ""),
            action=row.get("action"),
            quantity=int(row.get("quantity") or 1),
            entry_key=row.get("entry_key"),
            value=value if isinstance(value, dict) else ({} if value is None else {"value": value}),
            extra={k: v for k, v in row.items() if k not in known},
        )


# ---------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------

def store_path(store: str, root: str | Path = DEFAULT_STATE_ROOT) -> Path | None:
    """Path of one store's file, or None if the name is unusable.

    Names are restricted to lowercase letters, digits, hyphen and
    underscore, which is narrower than the portal allows. The store name
    becomes a filename here, and a name containing a path separator would
    write outside the state directory.
    """
    if not isinstance(store, str) or not _STORE_NAME_RE.match(store):
        _warn(f"{store!r} is not a usable store name (expected [a-z0-9][a-z0-9_-]*).")
        return None
    return Path(root) / f"{store}{STORE_SUFFIX}"


# ---------------------------------------------------------------------
# Append
# ---------------------------------------------------------------------

def record_state(
    store: str,
    entries: Iterable,
    *,
    root: str | Path = DEFAULT_STATE_ROOT,
    now: datetime | None = None,
) -> dict:
    """Appends entries to a store. Returns {"stored": n, "quantity": n, "ok": bool}.

    Each entry is a mapping and every field is optional:

        action     what happened, e.g. "seen" or "approved".
        quantity   how many real things this entry stands for, default 1.
        entry_key  the natural id: an item id, a slot id, a window id.
        event_at   when it happened (ISO 8601), defaulting to now.
        value      the payload.

    Any other key is stored verbatim, so a caller can hand over its own row
    shape unchanged. There is no update call and there will not be one; to
    correct something, append a newer entry.

    `now` is a parameter so a test or a replay can write deterministic
    timestamps instead of wall-clock ones.
    """
    result = {"stored": 0, "quantity": 0, "ok": False}
    path = store_path(store, root)
    if path is None:
        return result

    rows = list(entries or [])
    if not rows:
        result["ok"] = True
        return result

    stamp = _iso(now or datetime.now(timezone.utc))
    next_id = _count(path) + 1

    lines = []
    stored = quantity = 0
    for row in rows:
        if not isinstance(row, dict):
            _warn(f"skipping a non-mapping entry ({type(row).__name__}) for {store}.")
            continue
        entry = StateEntry(
            id=next_id + stored,
            store=store,
            event_at=_normalize_ts(row.get("event_at")) or stamp,
            created_at=stamp,
            action=_optional_str(row.get("action")),
            quantity=_quantity(row.get("quantity"), store),
            entry_key=_optional_str(row.get("entry_key")),
            value=row.get("value") if isinstance(row.get("value"), dict) else (
                {} if row.get("value") is None else {"value": row.get("value")}
            ),
            extra={
                k: v
                for k, v in row.items()
                if k not in ("action", "quantity", "entry_key", "event_at", "value", "id", "created_at")
            },
        )
        try:
            lines.append(json.dumps(entry.to_dict(), ensure_ascii=False))
        except (TypeError, ValueError) as exc:
            _warn(f"skipping an entry for {store} that is not JSON-serializable ({exc}).")
            continue
        stored += 1
        quantity += entry.quantity

    if not lines:
        return result

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError as exc:
        # Nothing was written, so say nothing was written. Reporting a
        # success here would let a caller believe a record survived a run
        # that in fact lost it, which is the exact failure this store exists
        # to prevent.
        _warn(f"could not append to {path} ({exc}); nothing was stored.")
        return result

    return {"stored": stored, "quantity": quantity, "ok": True}


# ---------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------

def read_state(
    store: str,
    *,
    entry_key: str | None = None,
    action: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int | None = None,
    latest_per_key: bool = False,
    root: str | Path = DEFAULT_STATE_ROOT,
) -> list:
    """Entries from a store, newest first. Full history by default.

    `latest_per_key=True` collapses to the newest entry per `entry_key`,
    which is what a snapshot wants: the current approval state of each slot,
    or the ids already ingested. Entries carrying no `entry_key` are each
    returned on their own rather than dropped, since silently losing rows
    from a read that looks complete is worse than returning one more.

    `since` and `until` filter on `event_at`, not on write time, so
    backfilled history does not read as today's activity.

    Returns an empty list on a missing store or an unreadable file, with a
    warning. An empty list means empty *or* failed; check for the warning
    before concluding nothing has happened.
    """
    path = store_path(store, root)
    if path is None:
        return []
    if not path.is_file():
        _warn(f"no store at {path}; returning nothing.")
        return []

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        _warn(f"could not read {path} ({exc}); returning nothing.")
        return []

    entries = []
    damaged = 0
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            damaged += 1
            continue
        if not isinstance(row, dict):
            damaged += 1
            continue
        entries.append(StateEntry.from_row(row, store))
    if damaged:
        _warn(f"{path}: skipped {damaged} unreadable line(s).")

    since_ts = _normalize_ts(since)
    until_ts = _normalize_ts(until)
    if since is not None and since_ts is None:
        _warn(f"could not read since={since!r} as a timestamp; ignoring it.")
    if until is not None and until_ts is None:
        _warn(f"could not read until={until!r} as a timestamp; ignoring it.")

    filtered = [
        e
        for e in entries
        if (entry_key is None or e.entry_key == entry_key)
        and (action is None or e.action == action)
        and (since_ts is None or e.event_at >= since_ts)
        and (until_ts is None or e.event_at <= until_ts)
    ]

    # Newest first. event_at is normalized to UTC on write, so a string sort
    # is a chronological sort; id breaks ties in append order.
    filtered.sort(key=lambda e: (e.event_at, e.id), reverse=True)

    if latest_per_key:
        seen = set()
        collapsed = []
        for entry in filtered:
            if entry.entry_key is None:
                collapsed.append(entry)
                continue
            if entry.entry_key in seen:
                continue
            seen.add(entry.entry_key)
            collapsed.append(entry)
        filtered = collapsed

    if limit is not None and limit >= 0:
        filtered = filtered[:limit]
    return filtered


DEFAULT_APPROVALS_STORE = "approvals"


def latest_decisions(
    store: str = DEFAULT_APPROVALS_STORE,
    *,
    root: str | Path = DEFAULT_STATE_ROOT,
) -> dict:
    """slot_id -> the `value` of the newest decision recorded about it.

    The whole payload rather than just the state, because a decision records
    what was approved and not only that something was: `item_ref` at the
    moment of signing off is what lets a later read notice the calendar has
    since moved a different post onto that date.

    Rows with no `entry_key` are skipped. An entry key is a slot id here, and
    a decision that names no slot cannot be joined onto a window, so carrying
    it forward would only put an unattributable approval into the answer.
    """
    decisions = {}
    for row in read_state(store, root=root, latest_per_key=True):
        if not row.entry_key:
            continue
        value = row.value if isinstance(row.value, dict) else {}
        decisions[row.entry_key] = value
    return decisions


def states_of(decisions: dict) -> dict:
    """slot_id -> state, from decisions already read.

    Separate from the read so a caller that needs both the states and the
    rest of each decision pays for one read rather than two, which also
    stops a fresh checkout reporting its empty store twice in one command.

    Decisions carrying no state are dropped rather than passed through as
    None, because `apply_approvals` reads a missing key as "no decision"
    and would read a None as a decision it could not understand.
    """
    return {
        slot_id: value["state"]
        for slot_id, value in (decisions or {}).items()
        if isinstance(value, dict)
        and isinstance(value.get("state"), str)
        and value["state"]
    }


def latest_approvals(
    store: str = DEFAULT_APPROVALS_STORE,
    *,
    root: str | Path = DEFAULT_STATE_ROOT,
) -> dict:
    """slot_id -> current approval state, which is what apply_approvals wants.

        window = slots.apply_approvals(window, latest_approvals())
    """
    return states_of(latest_decisions(store, root=root))


def state_stores(root: str | Path = DEFAULT_STATE_ROOT) -> list:
    """Which stores have anything in them, with counts and the newest entry.

    The answer to "is my state actually being persisted", which is worth
    asking before trusting a run that passed. A store expected here and not
    listed has never been written.
    """
    root = Path(root)
    if not root.is_dir():
        _warn(f"no state directory at {root}; nothing has been persisted.")
        return []
    summaries = []
    for path in sorted(root.glob(f"*{STORE_SUFFIX}")):
        name = path.name[: -len(STORE_SUFFIX)]
        entries = read_state(name, root=root)
        summaries.append(
            {
                "store": name,
                "path": str(path),
                "events": len(entries),
                "quantity": sum(e.quantity for e in entries),
                "newest_event_at": entries[0].event_at if entries else None,
            }
        )
    return summaries


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _count(path: Path) -> int:
    """How many entries a store already holds, so ids keep counting up.

    Unreadable lines still count: an id is a position, and renumbering
    around a damaged line would make two entries share an id.
    """
    if not path.is_file():
        return 0
    try:
        with path.open("r", encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError as exc:
        _warn(f"could not count existing entries in {path} ({exc}); ids may repeat.")
        return 0


def _iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _normalize_ts(value: Any) -> str | None:
    """Normalizes a timestamp to UTC ISO 8601 so string comparison is chronological.

    Accepts a datetime, a full ISO timestamp with or without an offset, a
    trailing Z (which Python 3.9's fromisoformat rejects), or a bare date.
    Returns None if it is none of those, and the caller decides what that
    means rather than having a guess written in for it.
    """
    if isinstance(value, datetime):
        return _iso(value)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00").replace("z", "+00:00")
    for candidate in (text, text[:19], f"{text[:10]}T00:00:00"):
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        return _iso(parsed)
    return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _quantity(value: Any, store: str) -> int:
    """How many real things one entry stands for. Default 1.

    The portal's contract is emphatic about this and the reason carries over
    unchanged: an entry standing for a batch of twenty that reports 1
    undercounts by the batch size, and nothing errors while it happens.
    """
    if value is None:
        return 1
    try:
        quantity = int(value)
    except (TypeError, ValueError):
        _warn(f"quantity {value!r} in {store} is not a number; recording 1.")
        return 1
    if quantity < 0:
        _warn(f"quantity {quantity} in {store} is negative; recording 1.")
        return 1
    return quantity


if __name__ == "__main__":
    # Standalone run: exercises the whole interface against a real directory.
    # By default it writes to a fresh temporary directory, so a demo run
    # never puts noise in the store the daily workflow commits. Point
    # --state-root at the real one to see entries accumulate across runs,
    # which is the property the whole module exists for.
    import tempfile

    parser = argparse.ArgumentParser(
        description="Append-only daily state store for the Thought Leadership Calendar Manager."
    )
    parser.add_argument(
        "--state-root",
        default=None,
        help=f"where the stores live (default: a temp dir; the real one is {DEFAULT_STATE_ROOT})",
    )
    parser.add_argument("--ingestion-store", default="ingestion_log")
    parser.add_argument("--approvals-store", default="approvals")
    args = parser.parse_args()

    root = Path(args.state_root) if args.state_root else Path(tempfile.mkdtemp())
    print(f"State root: {root}")
    if args.state_root is None:
        print("  (temporary; pass --state-root to accumulate across runs)")

    # A day's ingestion. Ids are the stable content-derived ones the readers
    # already emit, which is what makes the second day's read a deduplication
    # rather than a guess.
    day_one = [
        {"action": "seen", "entry_key": "market-scan/2f1c9a04b7d51e63",
         "event_at": "2026-08-17T09:00:00Z",
         "value": {"source_id": "middle-market-growth", "title": "A piece published yesterday"}},
        {"action": "seen", "entry_key": "content-atomizer/anchor-06-the-walker-receipts",
         "event_at": "2026-08-17T09:00:00Z", "value": {"words": 312}},
    ]
    print(f"\nDay one append: {record_state(args.ingestion_store, day_one, root=root)}")

    # The same item shows up again the next day, plus one new one. Nothing is
    # updated; the newer sighting is simply appended.
    day_two = [
        {"action": "seen", "entry_key": "market-scan/2f1c9a04b7d51e63",
         "event_at": "2026-08-18T09:00:00Z",
         "value": {"source_id": "middle-market-growth", "title": "A piece published yesterday"}},
        {"action": "seen", "entry_key": "value-creation-briefing/briefing_draft_2026-08",
         "event_at": "2026-08-18T09:00:00Z", "value": {"month": "2026-08"}},
    ]
    print(f"Day two append: {record_state(args.ingestion_store, day_two, root=root)}")

    history = read_state(args.ingestion_store, root=root)
    latest = read_state(args.ingestion_store, root=root, latest_per_key=True)
    print(f"\nFull history: {len(history)} entries, newest first")
    for entry in history:
        print(f"  #{entry.id} {entry.event_at} {entry.action} {entry.entry_key}")
    print(f"Latest per key: {len(latest)} distinct items, which is the deduplicated set")
    for entry in latest:
        print(f"  {entry.entry_key} last seen {entry.event_at}")

    # Approval state, which is the other thing that has to survive a run.
    # A human approves a slot, then withdraws it. There is no update call, so
    # the withdrawal is a newer entry and the reader takes the newest.
    decisions = [
        {"action": "approval", "entry_key": "jordan-2026-08-18-01",
         "event_at": "2026-08-18T14:00:00Z", "value": {"state": "approved", "by": "human"}},
        {"action": "approval", "entry_key": "jordan-2026-08-18-02",
         "event_at": "2026-08-18T14:05:00Z", "value": {"state": "approved", "by": "human"}},
        {"action": "approval", "entry_key": "jordan-2026-08-18-02",
         "event_at": "2026-08-18T16:30:00Z",
         "value": {"state": "rejected", "by": "human", "note": "holds the same stat as slot 01"}},
    ]
    print(f"\nApprovals append: {record_state(args.approvals_store, decisions, root=root)}")
    current = {
        entry.entry_key: (entry.value or {}).get("state")
        for entry in read_state(args.approvals_store, root=root, latest_per_key=True)
    }
    print("Current approval state, newest entry per slot:")
    for slot_id in sorted(current):
        print(f"  {slot_id}: {current[slot_id]}")
    print("  (the withdrawal is a newer entry, not an edit; both are still on disk)")

    print("\nWhat is being persisted:")
    for summary in state_stores(root=root):
        print(
            f"  {summary['store']}: {summary['events']} events, "
            f"quantity {summary['quantity']}, newest {summary['newest_event_at']}"
        )
        print(f"    {summary['path']}")

    print("\nDegradation, none of which raises:")
    print(f"  unknown store        -> {read_state('never-written', root=root)}")
    print(f"  unusable store name  -> {record_state('../escape', [{'action': 'x'}], root=root)}")
    print(f"  nothing to append    -> {record_state(args.ingestion_store, [], root=root)}")
