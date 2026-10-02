"""Repairs a rejected window when the fault is mechanical, and refuses when it is not.

`assemble.py` keeps a window that fails validation at
`window-<lens>-<YYYY-MM>.rejected.json`, so a paid-for answer survives its own
errors. Until 2026-09-18 nothing read one, and the only way forward was
paying for the whole model call again. Most of what can go wrong with a
window is mechanical: an id that can be derived, a vocabulary entry that can
be dropped. Fixing those needs nothing the model was paid for.

Repairable, with no model call (the code each repair answers):

    missing-slot-id, duplicate-slot-id   renumber `<window_id>-<nn>` by
                                         (date, item_ref), the way
                                         assemble.window_from_payload numbers
    missing-window-id                    `<lens>-<start>`, then renumber
    inverted-range, bad end_date         end from `slots.window_bounds`
    unknown-lens, lens-mismatch          the lens the filename names
    unknown-thread, unknown-form         drop the id the taxonomy lacks
    bad-responds-to-ref                  drop the malformed reference
    responds-to-self                     drop the self-reference
    unknown-approval-state, and any      back to draft; nobody has approved
    state other than draft               a rejected window

Not repairable, and a re-run instead:

    bad-date (a slot, or the start)      where the post goes is the judgment
    date-outside-window                  the model was paid for, and guessing
    bad-item-ref                         a date or a post here would be this
    responds-to-later-item               module quietly doing the sequencing

THE ONE CONSTRAINT THAT IS NOT NEGOTIABLE. Renumbering slot ids is the same
operation that slides an approval onto a post nobody approved, because
approvals are keyed by slot id. So this cannot be pointed at a standing
window, and that is enforced rather than documented:

- The only entry point takes a path and refuses anything not named
  `window-<lens>-<YYYY-MM>.rejected.json`. There is no function here that
  takes a window object from a caller.
- The output path is derived from that name, never passed in, and written by
  exclusive create, so a standing window at that path is never overwritten.
- It refuses if the approvals store already holds a decision for any slot id
  the repaired window would carry. That is the case a deleted window leaves
  behind, where renumbering would hand its old approvals to new posts.

EVERY REPAIR IS RECORDED in the written window's `repairs` list (code, where,
what changed) beside `repaired_from`. A repaired window that looked identical
to a clean one would destroy the only signal that the prompt needs work.
Nothing is half-written: if any judgment error remains, nothing is written.
"""

from __future__ import annotations

import argparse
import copy
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_AGENT_ROOT = _HERE.parent

sys.path.insert(0, str(_AGENT_ROOT / "calendar_model"))
sys.path.insert(0, str(_HERE))

import slots as slots_mod  # noqa: E402
import state_store  # noqa: E402
import assemble as assemble_mod  # noqa: E402

# The only input this module accepts. Lens, then month, then `.rejected.json`,
# exactly as assemble.main names a rejected window.
_REJECTED_NAME = re.compile(r"^window-(?P<lens>[a-z0-9][a-z0-9-]*?)-(?P<month>\d{4}-\d{2})"
                            r"\.rejected\.json$")

# The codes a repair cannot answer. Listed so a refusal can say why, not used
# to decide: whatever still fails validation after the repairs is refused.
JUDGMENT_CODES = frozenset({
    "bad-date", "date-outside-window", "bad-item-ref", "responds-to-later-item",
})


class RepairRefused(RuntimeError):
    """The file is not something this module may touch, or the repair is unsafe."""


@dataclass
class RepairResult:
    written: Path | None
    target: Path
    repairs: list = field(default_factory=list)
    remaining: list = field(default_factory=list)  # blocking Issues left after repair


def repair_rejected_file(
    path: str | Path,
    *,
    threads: str | Path = slots_mod.DEFAULT_TAXONOMY_PATH,
    state_root: str | Path | None = None,
    today=None,
    dry_run: bool = False,
) -> RepairResult:
    """Repair the rejected window at `path` and write it beside as the standing one.

    Raises RepairRefused when `path` is not a rejected window, the standing
    window already exists, or a decision already stands against an id the
    repair would produce. Returns with `written=None` and `remaining` set when
    judgment errors are left, having written nothing.
    """
    path = Path(path)
    match = _REJECTED_NAME.match(path.name)
    if match is None:
        raise RepairRefused(
            f"{path.name} is not a rejected window (window-<lens>-<YYYY-MM>.rejected.json). "
            "Only a rejected window may be repaired: renumbering slot ids on a standing "
            "one would move its approvals onto other posts."
        )
    lens, month = match["lens"], match["month"]
    target = path.with_name(slots_mod.window_filename(lens, month))
    if target.exists():
        raise RepairRefused(f"{target.name} already exists; a repair never writes over a window.")
    rejected = slots_mod.read_window(path)
    if rejected is None:
        raise RepairRefused(f"{path} could not be read as a window.")

    taxonomy = slots_mod.load_taxonomy(threads)
    window, repairs = _repair(copy.deepcopy(rejected), taxonomy, lens)

    root = Path(state_root) if state_root is not None else path.parent
    decided = state_store.latest_decisions(root=root)
    clashing = sorted(s.slot_id for s in window.slots if s.slot_id in decided)
    if clashing:
        raise RepairRefused(
            f"the store already holds decisions for {', '.join(clashing)}, which the repaired "
            "window would carry. They belong to an earlier window with these ids, and "
            "writing this one would hand them to different posts. Re-run assembly instead."
        )

    issues = slots_mod.validate_window(
        window, taxonomy, slots_mod.known_item_refs(),
        today=today or datetime.now(timezone.utc).date(),
    )
    remaining = slots_mod.errors(issues)
    result = RepairResult(written=None, target=target, repairs=repairs, remaining=remaining)
    if remaining or dry_run:
        return result

    window.extra["repaired_from"] = path.name
    window.extra["repaired_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    window.extra["repairs"] = repairs
    result.written = assemble_mod.write_new_window(target, window)
    return result


def _repair(window, taxonomy, lens: str) -> tuple:
    """Every deterministic repair, applied to a private copy. Returns (window, repairs).

    Private, and only ever handed a deep copy of a window read from a
    `.rejected.json`, because renumbering is only safe on a window nobody has
    decided on.
    """
    repairs: list = []

    def record(code: str, where: str, change: str) -> None:
        repairs.append({"code": code, "where": where, "change": change})

    if lens in taxonomy.calendar_lens_ids and window.lens != lens:
        record("unknown-lens", window.window_id or "<window>",
               f"lens {window.lens!r} -> {lens!r}, from the file name")
        window.lens = lens

    start = slots_mod._as_date(window.start_date)
    renumber = False
    if not window.window_id and start is not None:
        window.window_id = f"{window.lens}-{start.isoformat()}"
        record("missing-window-id", window.window_id, "window_id derived from lens and start")
        renumber = True

    if start is not None:
        end = slots_mod._as_date(window.end_date)
        if end is None or end < start:
            _, fixed = slots_mod.window_bounds(taxonomy, start)
            record("inverted-range" if end is not None else "bad-date", window.window_id,
                   f"end_date {window.end_date!r} -> {fixed!r}, from the start")
            window.end_date = fixed

    ids = [s.slot_id for s in window.slots]
    if any(not i for i in ids):
        renumber = True
    if len(set(ids)) != len(ids):
        renumber = True

    for slot in window.slots:
        where = slot.slot_id or "<slot with no id>"
        if slot.lens != window.lens:
            record("lens-mismatch", where, f"lens {slot.lens!r} -> {window.lens!r}")
            slot.lens = window.lens
        if taxonomy.ok and taxonomy.thread_ids:
            dropped = [t for t in slot.threads if t not in taxonomy.thread_ids]
            if dropped:
                slot.threads = [t for t in slot.threads if t in taxonomy.thread_ids]
                record("unknown-thread", where, f"dropped {', '.join(map(repr, dropped))}")
        if taxonomy.ok and taxonomy.form_ids:
            dropped = [f for f in slot.forms if f not in taxonomy.form_ids]
            if dropped:
                slot.forms = [f for f in slot.forms if f in taxonomy.form_ids]
                record("unknown-form", where, f"dropped {', '.join(map(repr, dropped))}")
        kept = []
        for ref in slot.responds_to:
            if not slots_mod._is_item_ref(ref):
                record("bad-responds-to-ref", where, f"dropped {ref!r}")
            elif ref == slot.item_ref:
                record("responds-to-self", where, f"dropped the reference to its own {ref}")
            else:
                kept.append(ref)
        slot.responds_to = kept
        if slot.approval != slots_mod.DRAFT:
            record("unknown-approval-state", where,
                   f"approval {slot.approval!r} -> 'draft'; nobody has decided on a "
                   "rejected window")
            slot.approval = slots_mod.DRAFT

    if renumber and window.window_id:
        window.slots.sort(key=lambda s: (str(s.date), str(s.item_ref)))
        seen: set = set()
        for index, slot in enumerate(window.slots, start=1):
            new_id = f"{window.window_id}-{index:02d}"
            if slot.slot_id != new_id:
                code = ("missing-slot-id" if not slot.slot_id
                        else "duplicate-slot-id" if slot.slot_id in seen
                        else "renumbered")
                record(code, slot.slot_id or "<slot with no id>",
                       f"slot_id -> {new_id}, by position in (date, item_ref) order")
            seen.add(slot.slot_id)
            slot.slot_id = new_id
    return window, repairs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Repair a rejected window's mechanical errors, or say why a re-run is needed."
    )
    parser.add_argument("rejected", help="a window-<lens>-<YYYY-MM>.rejected.json file")
    parser.add_argument("--threads", default=str(slots_mod.DEFAULT_TAXONOMY_PATH))
    parser.add_argument("--state-root", default=None,
                        help="where approvals live (default: the rejected file's folder)")
    parser.add_argument("--dry-run", action="store_true", help="report the repairs, write nothing")
    args = parser.parse_args(argv)

    try:
        result = repair_rejected_file(args.rejected, threads=args.threads,
                                      state_root=args.state_root, dry_run=args.dry_run)
    except RepairRefused as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 1

    print(f"{len(result.repairs)} repair(s):" if result.repairs else "No repair was needed.")
    for repair in result.repairs:
        print(f"  {repair['code']:<24} {repair['where']}: {repair['change']}")
    if result.remaining:
        print(f"\nNot written. {len(result.remaining)} error(s) need the judgment the model "
              "was paid for, so this month needs a re-run rather than a repair:", file=sys.stderr)
        for issue in result.remaining:
            print(f"  {issue}", file=sys.stderr)
        print(f"Delete {Path(args.rejected).name} to let the monthly assembly run again.",
              file=sys.stderr)
        return 1
    if args.dry_run:
        print(f"\nDry run: {result.target.name} would pass validation. Nothing written.")
        return 0
    print(f"\nWrote {result.written}, with every repair recorded in its `repairs` list. "
          f"{Path(args.rejected).name} is kept as the evidence.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
