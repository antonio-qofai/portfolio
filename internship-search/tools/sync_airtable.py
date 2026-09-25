"""Sync SQLite and Airtable in both directions. Milestone 5.

    python -m tools.sync_airtable --dry-run   # show what would move, write nothing
    python -m tools.sync_airtable             # do it

Reads the owner's labels out of Airtable into SQLite, drops rows he has finished
with, and pushes the newest surfaced postings up to the record cap in
`sources/airtable.toml`.

Safe to run as often as you like. Rows are matched by their Hash field, so
repeat runs update in place rather than duplicating, and a row deleted by hand
in Airtable is noticed and re-created rather than silently lost.
"""

import argparse

from agent import airtable, airtable_sync, config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change without writing to Airtable or SQLite",
    )
    args = parser.parse_args()

    if not config.airtable_configured():
        print("AIRTABLE_TOKEN and AIRTABLE_BASE_ID are not set in .env.")
        print("The sync is optional; nothing else in the agent needs them.")
        return 1

    try:
        report = airtable_sync.sync(dry_run=args.dry_run)
    except airtable.AirtableError as exc:
        print(f"Airtable refused the sync: {exc}")
        return 1

    verb = "would be" if report.dry_run else "were"

    if report.reconciled:
        print(f"{report.reconciled} stored record ids {verb} repaired")
    print(f"{report.labels_pulled} postings {verb} updated from your Airtable edits")
    if report.applied_stamped:
        print(
            f"{report.applied_stamped} application date(s) {verb} stamped "
            "from an Applied status you moved"
        )
    print(f"{report.pruned} resolved rows {verb} removed from Airtable")
    if report.companies_pruned:
        print(
            f"{report.companies_pruned} company rows {verb} removed, "
            "linked to nothing: " + ", ".join(report.orphan_names)
        )
    print(
        f"{report.updated} rows {verb} refreshed "
        f"({report.unchanged} already matched and {verb} skipped)"
    )
    print(f"{report.created} rows {verb} created")
    print(f"{report.contacts} contacts {verb} pushed")

    if report.collapsed_rows:
        print(
            f"\n{report.collapsed_rows} posting rows {verb} folded into another "
            "row as the same role in a different city, of which "
            f"{report.collapsed_pruned} {verb} already in the base and removed. "
            "Every city keeps its own SQLite row; only the base shows one. Turn "
            "this off with collapse_locations in sources/airtable.toml."
        )

    print(
        f"\n{report.records_after} of {report.cap} posting rows in the base, "
        f"out of {report.surfaced_total} surfaced and open in SQLite."
    )

    if report.over_cap():
        print(
            "\nThe base is at its cap, so newer postings are waiting in SQLite "
            "rather than appearing in Airtable. Label and resolve what is there, "
            "or raise max_posting_records in sources/airtable.toml if the free "
            "plan has room."
        )

    if report.dry_run:
        print("\nNothing was written.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
