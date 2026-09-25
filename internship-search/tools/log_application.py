"""Record that the owner applied somewhere, so it survives the next sync.

    .venv/bin/python -m tools.log_application --find "scale ai" --status applied
    .venv/bin/python -m tools.log_application --id 19084 --status applied --date 2026-08-15
    .venv/bin/python -m tools.log_application --id 6682 --status interviewing \\
        --event 2026-10-02 --note "phone screen"
    .venv/bin/python -m tools.log_application --find anduril --dry-run

WHY THIS EXISTS RATHER THAN AN UPDATE STATEMENT.

`applied_status` is one of the owner's five editable fields, and step 2 of the
sync reads Airtable as authoritative for all five. So for any posting that
already has an `airtable_record_id`, writing SQLite alone is undone within six
hours: the pull reads the blank Airtable cell, maps it through `empty_value` to
`not_applied`, and overwrites what was just written. The push cannot save it
either, because an update deliberately never carries an editable field
(CLAUDE.md rule 9).

So this writes BOTH, and the Airtable half is the one that makes it stick. A
posting with no Airtable row needs only the SQLite write, because
`_create_payload` carries editable fields upward when the row is eventually
created, which is the same rule 9 asymmetry working in the other direction.

Cost, per CLAUDE.md rule 8: one call per ten records, and this writes only the
postings named on the command line. Logging every application of a whole cycle
is a handful of calls.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

from agent import airtable, config, db

STATUSES = ("applied", "interviewing", "rejected", "offer", "skipped", "missed",
            "not_applied")

# Which statuses mean an application was actually sent, and so should carry a
# date. "skipped" and "missed" are the opposite: they record a role he did NOT
# apply to, so stamping them would put a date on a thing that never happened and
# would inflate the count the response rate divides by.
APPLIED_STATUSES = ("applied", "interviewing", "rejected", "offer")


def find(conn, needle: str) -> list[sqlite3.Row]:
    """Postings whose company or title contains `needle`, best candidates first.

    Ordered so the ones most likely to be meant come first: surfaced before
    killed, in Airtable before not, open before closed. A posting he applied to
    is almost always one the agent surfaced and showed him.
    """
    conn.row_factory = sqlite3.Row
    like = f"%{needle.lower()}%"
    return conn.execute(
        "SELECT * FROM postings WHERE lower(company) LIKE ? OR lower(title) LIKE ? "
        "ORDER BY (prefilter_verdict = 'surface') DESC, "
        "(airtable_record_id IS NOT NULL) DESC, "
        "(closed_detected_at IS NULL) DESC, company, title",
        (like, like),
    ).fetchall()


def describe(row) -> str:
    state = "closed" if row["closed_detected_at"] else "open"
    where = "in Airtable" if row["airtable_record_id"] else "not in Airtable"
    return (f"  id={row['id']:<6} {state:6} {row['prefilter_verdict'] or '-':8} "
            f"{where:15} {row['company'][:22]:22} {row['title'][:46]}")


def push_to_airtable(rows_and_status: list[tuple]) -> int:
    """Set Applied status on the Airtable rows, so the pull cannot undo it.

    Returns how many records were written. Anything without a record id is
    skipped here and relies on the SQLite write alone, which is correct for it.
    """
    targets = [(r, s) for r, s in rows_and_status if r["airtable_record_id"]]
    if not targets:
        return 0
    schema = airtable.load_schema()
    table = schema.table("postings")
    field = next(
        (f for f in table.editable_fields if f.column == "applied_status"), None
    )
    if field is None:
        raise SystemExit("no Applied status field in sources/airtable.toml")

    records = []
    for row, status in targets:
        shown = field.to_airtable(status)
        if shown is None:
            continue
        records.append({"id": row["airtable_record_id"], "fields": {field.name: shown}})
    if not records:
        return 0
    with airtable.Client() as client:
        return client.update_records(table.name, records)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--id", type=int, action="append", default=[],
                    help="posting id, repeatable")
    ap.add_argument("--find", help="search company and title, then list candidates")
    ap.add_argument("--status", default="applied", choices=STATUSES)
    ap.add_argument("--date", help="when you applied, ISO. Defaults to today.")
    ap.add_argument("--event", help="next interview or assessment date, ISO")
    ap.add_argument("--note", default="", help="what the next event is")
    ap.add_argument("--dry-run", action="store_true", help="say what would change")
    args = ap.parse_args()

    conn = db.connect()
    conn.row_factory = sqlite3.Row

    if args.find and not args.id:
        rows = find(conn, args.find)
        if not rows:
            print(f"Nothing matches {args.find!r}.")
            return 1
        print(f"{len(rows)} match(es) for {args.find!r}, best candidates first:\n")
        for row in rows[:20]:
            print(describe(row))
        if len(rows) > 20:
            print(f"  ... and {len(rows) - 20} more")
        print("\nRe-run with --id to log one, for example:")
        print(f"  .venv/bin/python -m tools.log_application --id {rows[0]['id']} "
              f"--status {args.status}")
        return 0

    if not args.id:
        ap.error("give --id, or --find to search for one")

    rows = []
    for pid in args.id:
        row = conn.execute("SELECT * FROM postings WHERE id = ?", (pid,)).fetchone()
        if row is None:
            print(f"No posting with id {pid}.", file=sys.stderr)
            return 1
        rows.append(row)

    # A full timestamp, matching what `airtable_sync._applied_stamp` writes.
    # This wrote `db.now()[:10]` until 2026-09-22, a bare date with no offset,
    # and `delivery._parse` returned it naive: comparing that against an aware
    # cutoff raised TypeError inside the action block, the watcher exited 1, and
    # since the watcher is required every run stopped there. Seven runs over two
    # days. `_parse` is now defensive as well, but two writers of one column
    # must agree on its format regardless.
    when = db.now()
    if args.date:
        # A date he typed means midnight UTC that day, not "now".
        when = f"{args.date.strip()[:10]}T00:00:00+00:00"
    print(f"Setting applied_status = {args.status!r}"
          + (f", applied_at = {when}" if args.status in APPLIED_STATUSES else "")
          + (f", next event {args.event}" if args.event else ""))
    for row in rows:
        print(describe(row))

    if args.dry_run:
        in_at = sum(1 for r in rows if r["airtable_record_id"])
        print(f"\nDry run. {len(rows)} row(s) in SQLite, {in_at} also pushed to "
              f"Airtable ({(in_at + 9) // 10} call(s)). Nothing was written.")
        return 0

    # SQLite first. The Airtable push can fail on a network the database does
    # not need, and a recorded application that has not reached the base yet is
    # a far better state than one that reached neither.
    for row in rows:
        conn.execute(
            "UPDATE postings SET applied_status = ?, applied_at = ?, "
            "next_event_at = COALESCE(?, next_event_at), "
            "next_event_note = COALESCE(?, next_event_note) WHERE id = ?",
            (args.status,
             when if args.status in APPLIED_STATUSES else None,
             args.event or None,
             (args.note or None) if args.event else None,
             row["id"]),
        )
    conn.commit()
    print(f"\nWrote {len(rows)} row(s) to SQLite.")

    if not config.airtable_configured():
        print("Airtable is not configured, so nothing was pushed. Any of these "
              "rows already in the base will be overwritten on the next sync.")
        return 0
    try:
        pushed = push_to_airtable([(r, args.status) for r in rows])
    except Exception as exc:  # noqa: BLE001 - the SQLite write already landed
        print(f"\nAirtable push FAILED: {exc!r}", file=sys.stderr)
        print("The SQLite write stands, but any row already in the base will be "
              "overwritten on the next sync. Re-run this command, or set the "
              "dropdown by hand.", file=sys.stderr)
        return 1
    skipped = len(rows) - pushed
    print(f"Pushed {pushed} record(s) to Airtable"
          + (f"; {skipped} had no Airtable row and did not need it." if skipped else "."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
