#!/usr/bin/env python3
"""One-time Airtable base setup.

Creates Chores, Rules, Cleaner Visits, and Assignments tables with the
correct fields and types. Skips any table that already exists. Does not
insert records — data is entered manually in Airtable after this runs.

Prerequisites: build and seed the Roster table manually first (Name,
Email, Active, Sort order). This script needs it to exist so it can link
Assignments to it.

Usage:
    AIRTABLE_API_KEY=<key> AIRTABLE_BASE_ID=<id> python3 scripts/setup_base.py
    AIRTABLE_API_KEY=<key> AIRTABLE_BASE_ID=<id> python3 scripts/setup_base.py --dry-run
"""

import json
import os
import sys
import urllib.error
import urllib.request

META_URL = "https://api.airtable.com/v0/meta/bases"


def main():
    dry_run = "--dry-run" in sys.argv
    api_key = os.environ.get("AIRTABLE_API_KEY", "").strip()
    base_id = os.environ.get("AIRTABLE_BASE_ID", "").strip()

    if not api_key or not base_id:
        print("ERROR: set AIRTABLE_API_KEY and AIRTABLE_BASE_ID before running")
        sys.exit(1)

    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
    }

    if dry_run:
        print("--- DRY RUN: nothing will be created ---")

    # ------------------------------------------------------------------ #
    # 1. Read existing tables
    # ------------------------------------------------------------------ #
    print("Reading existing tables...")
    existing = _fetch_tables(base_id, headers)
    by_name = {t["name"]: t for t in existing}
    print("  Found:", list(by_name))

    if "Roster" not in by_name:
        print("ERROR: Roster table not found. Create it manually first.")
        sys.exit(1)
    roster_id = by_name["Roster"]["id"]

    # ------------------------------------------------------------------ #
    # 2. Chores
    # ------------------------------------------------------------------ #
    if "Chores" in by_name:
        chores_id = by_name["Chores"]["id"]
        print("Chores: already exists, skipping")
    else:
        defn = {
            "name": "Chores",
            "fields": [
                # Primary field — must be a supported primary type
                {"name": "Name", "type": "singleLineText"},
                {"name": "Task", "type": "multilineText"},
                {
                    "name": "Cadence",
                    "type": "singleSelect",
                    "options": {
                        "choices": [{"name": "weekly"}, {"name": "every_3"}]
                    },
                },
                {
                    "name": "Cleaner behaviour",
                    "type": "singleSelect",
                    "options": {
                        "choices": [
                            {"name": "normal"},
                            {"name": "convert_to_prep"},
                        ]
                    },
                },
                {"name": "Prep task", "type": "multilineText"},
                {"name": "Offset", "type": "number", "options": {"precision": 0}},
                {"name": "Seed", "type": "number", "options": {"precision": 0}},
            ],
        }
        if dry_run:
            print("DRY RUN: would create Chores table")
            chores_id = None
        else:
            result = _create_table(base_id, defn, headers)
            chores_id = result["id"]
            print(f"Chores: created (id={chores_id})")

    # ------------------------------------------------------------------ #
    # 3. Rules
    # ------------------------------------------------------------------ #
    if "Rules" in by_name:
        print("Rules: already exists, skipping")
    else:
        defn = {
            "name": "Rules",
            "fields": [
                # "Text" as primary (singleLineText so it works as primary)
                {"name": "Text", "type": "singleLineText"},
                {
                    "name": "Category",
                    "type": "singleSelect",
                    "options": {
                        "choices": [{"name": "override"}, {"name": "standing"}]
                    },
                },
                {"name": "Number", "type": "number", "options": {"precision": 0}},
                {
                    "name": "Active from",
                    "type": "date",
                    "options": {"dateFormat": {"name": "iso"}},
                },
                {
                    "name": "Active until",
                    "type": "date",
                    "options": {"dateFormat": {"name": "iso"}},
                },
            ],
        }
        if dry_run:
            print("DRY RUN: would create Rules table")
        else:
            result = _create_table(base_id, defn, headers)
            print(f"Rules: created (id={result['id']})")

    # ------------------------------------------------------------------ #
    # 4. Cleaner Visits
    # ------------------------------------------------------------------ #
    if "Cleaner Visits" in by_name:
        print("Cleaner Visits: already exists, skipping")
    else:
        defn = {
            "name": "Cleaner Visits",
            "fields": [
                # Airtable requires a text/number/etc primary field.
                # "Name" here lets you label each visit (e.g. "Oct 2026").
                # The scheduler ignores this field.
                {"name": "Name", "type": "singleLineText"},
                {
                    "name": "Visit date",
                    "type": "date",
                    "options": {"dateFormat": {"name": "iso"}},
                },
                {
                    "name": "Confirmed",
                    "type": "checkbox",
                    "options": {"icon": "check", "color": "greenBright"},
                },
            ],
        }
        if dry_run:
            print("DRY RUN: would create Cleaner Visits table")
        else:
            result = _create_table(base_id, defn, headers)
            print(f"Cleaner Visits: created (id={result['id']})")

    # ------------------------------------------------------------------ #
    # 5. Assignments (links to Chores and Roster)
    # ------------------------------------------------------------------ #
    if "Assignments" in by_name:
        print("Assignments: already exists, checking fields")
        _add_missing_fields(
            base_id,
            by_name["Assignments"],
            [
                {"name": "Week", "type": "number", "options": {"precision": 0}},
                _nudge_field("First nudge sent"),
                _nudge_field("Followup sent"),
            ],
            headers,
            dry_run,
        )
    elif dry_run:
        print("DRY RUN: would create Assignments table (links to Chores + Roster)")
    elif chores_id is None:
        print("SKIPPING Assignments: Chores table ID unknown (it already existed?)")
        print("  Re-run the script — it will pick up Chores from existing tables.")
    else:
        defn = {
            "name": "Assignments",
            "fields": [
                # Airtable always makes the first field primary and uses it as
                # the row title in the mobile app, which is where chores get
                # ticked off. A bare week number there means every row reads
                # "1", so the primary field carries the chore name instead.
                {"name": "Assignment", "type": "singleLineText"},
                {"name": "Week", "type": "number", "options": {"precision": 0}},
                {
                    "name": "Chore",
                    "type": "multipleRecordLinks",
                    "options": {"linkedTableId": chores_id},
                },
                {
                    "name": "Assignee",
                    "type": "multipleRecordLinks",
                    "options": {"linkedTableId": roster_id},
                },
                {"name": "Task", "type": "multilineText"},
                {
                    "name": "Due",
                    "type": "dateTime",
                    "options": {
                        "timeZone": "America/Chicago",
                        "dateFormat": {"name": "iso"},
                        "timeFormat": {"name": "24hour"},
                    },
                },
                {
                    "name": "Prep",
                    "type": "checkbox",
                    "options": {"icon": "check", "color": "greenBright"},
                },
                {
                    "name": "Done",
                    "type": "checkbox",
                    "options": {"icon": "check", "color": "greenBright"},
                },
                # The nudge log. Two datetime fields rather than a table:
                # an assignment can be nudged at most twice, so there is
                # nothing to accumulate.
                _nudge_field("First nudge sent"),
                _nudge_field("Followup sent"),
            ],
        }
        result = _create_table(base_id, defn, headers)
        print(f"Assignments: created (id={result['id']})")

    print("\nDone. Seed data (roster rows, chores, rules) in Airtable manually.")


def _nudge_field(name):
    """A datetime field definition for one half of the nudge log."""
    return {
        "name": name,
        "type": "dateTime",
        "options": {
            "timeZone": "America/Chicago",
            "dateFormat": {"name": "iso"},
            "timeFormat": {"name": "24hour"},
        },
    }


def _add_missing_fields(base_id, table, field_defns, headers, dry_run):
    """Create any of field_defns that the table does not already have.

    Additive and idempotent: an existing field is left exactly as it is, so
    re-running this never touches data.
    """
    have = {f["name"] for f in table["fields"]}
    for defn in field_defns:
        if defn["name"] in have:
            print(f"  {defn['name']}: already exists, skipping")
        elif dry_run:
            print(f"  DRY RUN: would add {defn['name']} ({defn['type']})")
        else:
            result = _create_field(base_id, table["id"], defn, headers)
            print(f"  {defn['name']}: created (id={result['id']})")


def _create_field(base_id, table_id, defn, headers):
    url = f"{META_URL}/{base_id}/tables/{table_id}/fields"
    data = json.dumps(defn).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"ERROR creating field {defn['name']!r}: HTTP {e.code}\n{body}")
        sys.exit(1)


def _fetch_tables(base_id, headers):
    url = f"{META_URL}/{base_id}/tables"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())["tables"]
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"ERROR fetching tables: HTTP {e.code}\n{body}")
        sys.exit(1)


def _create_table(base_id, defn, headers):
    url = f"{META_URL}/{base_id}/tables"
    data = json.dumps(defn).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"ERROR creating table {defn['name']!r}: HTTP {e.code}\n{body}")
        sys.exit(1)


if __name__ == "__main__":
    main()
