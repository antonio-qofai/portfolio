"""The monthly assembly: build next month's calendar for every lens, on schedule.

Run once a day by the monthly-assembly workflow. On most days it works out
that assembly is not due yet and exits having done and written nothing. On
the due day, and on any later day in the month where a lens still has no
window, it runs `assemble.main` for each live lens with the window file as
`--out`.

When it is due. The `day_of_month` of the `assembly_schedule` block in
sequencing_criteria.yaml, in the month before the one being assembled.
Nothing about the date is written here. See the comment on that block for
why it is that day, and why it is a plain day rather than a number derived
from the approval horizon.

Three refusals, each checked before any model call:

- The target window already exists. A standing window may carry approvals,
  those are keyed by the positional slot_id, and the store has no update
  call, so nothing here ever writes over one. `assemble.main` refuses too,
  and writes by exclusive create, so the protection does not depend on this
  module remembering to check. On a scheduled run this is the normal state
  for every day after the due day, so it exits 0. With `--month`, somebody
  asked for a month that exists, so it exits 1.
- A `.rejected.json` for the target exists. The last attempt failed
  validation and nobody has dealt with it. Paying for three more model calls
  every morning until someone notices is the wrong way to tell them, so this
  refuses and reds the run instead.
- The target window would open in the past. That only happens with an
  explicit `--month`, and it means the wrong month was typed.

One lens failing does not stop the others, as in `refresh.py --all-lenses`.
The run still exits non-zero, so red still means look at this.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_AGENT_ROOT = _HERE.parent

sys.path.insert(0, str(_AGENT_ROOT / "calendar_model"))
sys.path.insert(0, str(_HERE))

import slots as slots_mod  # noqa: E402
import state_store  # noqa: E402
import assemble as assemble_mod  # noqa: E402

# How the outcome of a lens is recorded. Not policy: these are the words the
# run log uses.
ASSEMBLED = "assembled"
ALREADY_EXISTS = "refused-exists"
REJECTED_PENDING = "refused-rejected-pending"
FAILED = "failed"


def _warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def schedule_policy(criteria: dict) -> tuple[dict, list[str]]:
    """The `assembly_schedule` block and whatever is wrong with it."""
    block = criteria.get("assembly_schedule")
    if not isinstance(block, dict):
        return {}, ["sequencing_criteria.yaml has no assembly_schedule block"]
    problems = []
    day = block.get("day_of_month")
    if isinstance(day, bool) or not isinstance(day, int) or not 1 <= day <= 28:
        # 28 so the day exists in every month. A founder saying "the 30th"
        # is a conversation, not a date February can honour.
        problems.append(f"assembly_schedule.day_of_month must be 1 to 28, not {day!r}")
    if not str(block.get("run_store") or "").strip():
        problems.append("assembly_schedule.run_store names no store")
    return block, problems


def first_of_next_month(today: date) -> date:
    return (today.replace(day=1) + timedelta(days=32)).replace(day=1)


def due_date(block: dict, target_start: date) -> date:
    """The day the window opening on `target_start` should be assembled.

    `day_of_month` of the month before it. `schedule_policy` has already
    refused a day no month can hold, so there is nothing unanswerable here
    and no guess to make.
    """
    previous_month = (target_start - timedelta(days=1)).replace(day=1)
    return previous_month.replace(day=int(block["day_of_month"]))


def target_path(state_root: Path, lens: str, start: date) -> Path:
    return state_root / slots_mod.window_filename(lens, start.strftime("%Y-%m"))


def rejected_path(target: Path) -> Path:
    # Must agree with where assemble.main keeps a rejected window.
    return target.with_suffix(".rejected.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Assemble next month's calendar for every lens once it is due."
    )
    parser.add_argument("--criteria", default=str(assemble_mod.DEFAULT_CRITERIA_PATH))
    parser.add_argument("--threads", default=str(slots_mod.DEFAULT_TAXONOMY_PATH))
    parser.add_argument("--state-root", default=str(state_store.DEFAULT_STATE_ROOT))
    parser.add_argument("--today", default=None, help="reference date (default: today UTC)")
    parser.add_argument("--month", default=None,
                        help="assemble this month (YYYY-MM) now, ignoring the due date")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be sent for each lens; no model call, no write")
    args = parser.parse_args(argv)

    criteria = assemble_mod.load_criteria(args.criteria)
    block, problems = schedule_policy(criteria)
    if problems:
        for problem in problems:
            _warn(problem)
        print("Cannot schedule: the assembly schedule is not usable.", file=sys.stderr)
        return 1

    taxonomy = slots_mod.load_taxonomy(args.threads)
    lenses = list(taxonomy.v1_lens_order)
    if not lenses:
        print(f"Cannot assemble: {args.threads} declares no live calendar.", file=sys.stderr)
        return 1

    today = slots_mod._as_date(args.today) or datetime.now(timezone.utc).date()
    if args.month:
        start = slots_mod._as_date(f"{args.month}-01")
        if start is None:
            print(f"Cannot assemble: --month {args.month!r} is not YYYY-MM.", file=sys.stderr)
            return 1
        if start <= today:
            print(f"Cannot assemble: a window for {args.month} would open on or before "
                  f"today ({today}).", file=sys.stderr)
            return 1
    else:
        start = first_of_next_month(today)
        due = due_date(block, start)
        if today < due:
            print(f"Not due. The {start:%Y-%m} calendars are assembled on {due} "
                  f"({(start - due).days} days before they open); today is {today}.")
            return 0
        print(f"Due since {due}: assembling the {start:%Y-%m} calendars, "
              f"which open in {(start - today).days} days.")

    state_root = Path(args.state_root)
    outcomes: list[dict] = []
    worst = 0
    for lens in lenses:
        target = target_path(state_root, lens, start)
        print(f"\n{'=' * 64}\n{lens} calendar -> {target.name}\n{'=' * 64}")
        if target.exists():
            print(f"{target.name} already exists; refusing to write over it. A standing "
                  "window may already carry approvals, which are keyed by slot position.")
            outcomes.append({"lens": lens, "outcome": ALREADY_EXISTS, "path": target.name})
            if args.month:
                worst = 1
            continue
        rejected = rejected_path(target)
        if rejected.exists():
            print(f"{rejected.name} is waiting from an earlier attempt. Not paying for "
                  "another model call until it is dealt with: repair it with "
                  "synthesis/repair_rejected.py, or delete it to re-run.", file=sys.stderr)
            outcomes.append({"lens": lens, "outcome": REJECTED_PENDING, "path": rejected.name})
            worst = 1
            continue
        forwarded = ["--criteria", args.criteria, "--threads", args.threads,
                     "--lens", lens, "--start", start.isoformat(), "--out", str(target),
                     "--state-root", str(state_root), "--today", today.isoformat()]
        if args.dry_run:
            forwarded.append("--dry-run")
        code = assemble_mod.main(forwarded)
        if args.dry_run:
            worst = max(worst, code)
            continue
        written = target.exists() and code == 0
        outcomes.append({
            "lens": lens,
            "outcome": ASSEMBLED if written else FAILED,
            "path": (target if written else rejected).name,
        })
        worst = max(worst, 0 if written else 1)

    # A scheduled day after the month is done finds every window already
    # there. That is the steady state for half of each month, and recording
    # it would make a daily commit saying nothing happened.
    settled = all(o["outcome"] == ALREADY_EXISTS for o in outcomes) and not args.month
    if outcomes and not args.dry_run and not settled:
        store = str(block["run_store"])
        recorded = state_store.record_state(store, [{
            "action": "monthly-assembly",
            "entry_key": start.strftime("%Y-%m"),
            "value": {"today": today.isoformat(), "start": start.isoformat(),
                      "explicit_month": bool(args.month), "lenses": outcomes},
        }], root=state_root)
        if not recorded.get("ok"):
            _warn(f"the assembly ran but was not recorded to {store}")

    print(f"\n{len(lenses)} calendar(s) considered for {start:%Y-%m}; "
          f"{'nothing needs reading' if worst == 0 else 'at least one needs reading'}.")
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
