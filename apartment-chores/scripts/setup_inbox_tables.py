#!/usr/bin/env python3
"""One-time Airtable setup for the landlord email reader (PRD-v1.1).

Creates the Requests table, and adds the Source message and Raw excerpt
fields to Cleaner Visits. Skips anything that already exists, so it is
safe to re-run. Changes the base's structure only; writes no records and
touches no existing field.

Needs a token with the schema.bases:read and schema.bases:write scopes. If
the scheduler's token lacks them, it prints a 403 and changes nothing; add
the fields by hand instead, using the names and types printed by --dry-run.

Usage:
    AIRTABLE_API_KEY=<key> AIRTABLE_BASE_ID=<id> python3 scripts/setup_inbox_tables.py --dry-run
    AIRTABLE_API_KEY=<key> AIRTABLE_BASE_ID=<id> python3 scripts/setup_inbox_tables.py
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

# This script lives in scripts/, so the repo root is not on the path when it
# runs directly. Put it there before importing anything from config.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.airtable_fields import DEFAULT_FIELD_CONFIG
from config.due_policy import DEFAULT_DUE_POLICY
from config.proposal_fields import DEFAULT_PROPOSAL_FIELDS as F

META_URL = "https://api.airtable.com/v0/meta/bases"
ISO_DATE = {"dateFormat": {"name": "iso"}}


def requests_table():
    r = F.requests
    return {
        "name": F.table_requests,
        "fields": [
            {"name": r.summary, "type": "singleLineText"},  # primary
            {"name": r.detail, "type": "multilineText"},
            {"name": r.due, "type": "date", "options": ISO_DATE},
            {
                "name": F.request_confirmed,
                "type": "checkbox",
                "options": {"icon": "check", "color": "greenBright"},
            },
            {"name": r.source_message, "type": "singleLineText"},
            {
                "name": r.received,
                "type": "dateTime",
                "options": {
                    "dateFormat": {"name": "iso"},
                    "timeFormat": {"name": "24hour"},
                    "timeZone": DEFAULT_DUE_POLICY.timezone,
                },
            },
            {"name": r.raw_excerpt, "type": "multilineText"},
        ],
    }


def visit_fields():
    v = F.visits
    return [
        {"name": v.source_message, "type": "singleLineText"},
        {"name": v.raw_excerpt, "type": "multilineText"},
    ]


def main(argv):
    dry_run = "--dry-run" in argv
    api_key = os.environ.get("AIRTABLE_API_KEY", "").strip()
    base_id = os.environ.get("AIRTABLE_BASE_ID", "").strip()
    if not api_key or not base_id:
        print("ERROR: set AIRTABLE_API_KEY and AIRTABLE_BASE_ID before running")
        return 1
    headers = {"Authorization": "Bearer " + api_key, "Content-Type": "application/json"}
    if dry_run:
        print("--- DRY RUN: nothing will be created ---")

    tables = {t["name"]: t for t in _call("GET", "%s/%s/tables" % (META_URL, base_id), headers)["tables"]}

    visits_name = DEFAULT_FIELD_CONFIG.table_cleaner_visits
    if visits_name not in tables:
        print("ERROR: %s table not found. Run scripts/setup_base.py first." % visits_name)
        return 1
    visits = tables[visits_name]
    have = {f["name"] for f in visits["fields"]}
    for field in visit_fields():
        if field["name"] in have:
            print("%s.%s: already exists, skipping" % (visits_name, field["name"]))
        elif dry_run:
            print("DRY RUN: would add %s.%s (%s)" % (visits_name, field["name"], field["type"]))
        else:
            url = "%s/%s/tables/%s/fields" % (META_URL, base_id, urllib.parse.quote(visits["id"]))
            _call("POST", url, headers, field)
            print("%s.%s: added" % (visits_name, field["name"]))

    defn = requests_table()
    if defn["name"] in tables:
        print("%s: already exists, skipping" % defn["name"])
    elif dry_run:
        print("DRY RUN: would create %s with fields:" % defn["name"])
        for field in defn["fields"]:
            print("  %s (%s)" % (field["name"], field["type"]))
    else:
        result = _call("POST", "%s/%s/tables" % (META_URL, base_id), headers, defn)
        print("%s: created (id=%s)" % (defn["name"], result["id"]))
    return 0


def _call(method, url, headers, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print("ERROR: %s %s returned HTTP %d\n%s" % (method, url, e.code, e.read().decode()))
        sys.exit(1)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
