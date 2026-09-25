"""Push application windows into Google Calendar.

    python -m tools.sync_calendar --dry-run   # show what would be written, no auth
    python -m tools.sync_calendar             # write to Google Calendar

Safe to run as often as you like. Events are matched by their stable key and
patched in place, so repeat runs update rather than duplicate.
"""

import argparse
import datetime as dt

from agent import gcal, db


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="list the windows without touching Google"
    )
    args = parser.parse_args()

    # Cycle windows from the TOML, plus the events derived from the database
    # (PRD section 12 phase three): a follow-up two weeks after each
    # application, and a reminder for a tier 1 role left untouched.
    conn = db.connect()
    windows = gcal.load_windows() + gcal.derived_windows(conn)
    today = dt.date.today()

    open_now = [w for w in windows if w.start <= today <= w.end]
    closing = [w for w in open_now if (w.end - today).days <= 30]

    if open_now:
        print(f"Open right now ({today.isoformat()}):")
        for w in open_now:
            days = (w.end - today).days
            print(f"  {w.company}: {w.label}, ~{days} days left")
        print()

    result = gcal.sync(dry_run=args.dry_run, conn=conn)

    if result["dry_run"]:
        print(f"\n{result['total']} windows would be synced. No calendar was touched.")
    else:
        print(
            f"Synced {result['total']} windows: "
            f"{result['created']} created, {result['updated']} updated."
        )
        print(f"Calendar: {gcal.CALENDAR_NAME}")

    if closing:
        print("\nClosing within 30 days, act on these:")
        for w in closing:
            print(f"  {w.company}: {w.action or w.label}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
