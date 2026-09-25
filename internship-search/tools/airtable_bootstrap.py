"""Create the Airtable base described in sources/airtable.toml.

    python -m tools.airtable_bootstrap --dry-run   # show the plan, touch nothing
    python -m tools.airtable_bootstrap             # create what is missing

Run this once when the base is empty, and again after editing
`sources/airtable.toml` to add a table, a field or a view. It only ever creates
what is missing, so repeat runs are safe and cost nothing.

Needs a token with `schema.bases:write`, which is the only step that does. That
scope can be revoked afterwards; the sync itself needs only record read, record
write, and `schema.bases:read`.
"""

import argparse

from agent import airtable, config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would be created without touching Airtable",
    )
    args = parser.parse_args()

    schema = airtable.load_schema()

    if not config.airtable_configured():
        print("AIRTABLE_TOKEN and AIRTABLE_BASE_ID are not set in .env.")
        return 1

    # A dry run reads the live base and diffs it. It used to print the contents
    # of sources/airtable.toml and stop, which answered a question nobody asks.
    with airtable.Client() as client:
        report = airtable.ensure_schema(client, schema, dry_run=args.dry_run)

    verb = "would create" if args.dry_run else "created"
    for name in report["tables_created"]:
        print(f"{verb} table  {name}")
    for name in report["tables_existing"]:
        print(f"already there  {name}")
    for name in report["fields_created"]:
        print(f"{verb} field  {name}")
    if not report["tables_created"] and not report["fields_created"]:
        print("Every table and field the schema asks for is already in the base.")

    if report["views_missing"]:
        print(
            "\nAirtable's API cannot create views, only read them, so these are "
            "the one manual step. In the base, create each view and set its "
            "filter:"
        )
        for table_name, view in report["views_missing"]:
            print(f"  {table_name}: new {view.type} view named {view.name!r}")
            if view.purpose:
                print(f"      what it is for: {view.purpose}")
            if view.filter_field:
                where = f"where {view.filter_field} {view.filter_operator}"
                if view.filter_value:
                    where += f" {view.filter_value}"
                print(f"      filter:   {where}")
            if view.group_field:
                print(f"      group by: {view.group_field}")
            if view.sort_by:
                print(f"      sort:     {', '.join(view.sort_by)}")
        print("\nRe-run this command afterwards to confirm they exist.")
    else:
        print("\nAll views present.")

    if not any(report[k] for k in ("tables_created", "fields_created")):
        print("Tables and fields already match the file.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
