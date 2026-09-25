"""Is anything worth seeing failing to get an Airtable row?

    .venv/bin/python -m tools.airtable_coverage

Read-only, no API calls, answers in a second.

This exists because the obvious signal is the wrong one. `tools.sync_airtable`
prints "The base is at its cap" whenever the slice is larger than the cap, and
that was written down on 2026-09-22 as the trigger to revisit capacity. Adding
the third aggregator feed made it fire within hours: 1,136 eligible against 900
slots. The right response was to do nothing, because all 163 tier 1 and all 300
tier 2 postings had rows and the 236 excluded were tier 3 and older.

A full base is the sort working. What matters is WHAT is excluded, so this
reports the excluded set by tier and says plainly whether anything important is
missing.
"""

from __future__ import annotations

import sys
from collections import Counter

from agent import airtable, airtable_sync, db, rubric

# Bands whose exclusion is a real problem. Read from rubric.md rather than
# written here, on the same rule that puts every tier definition in one file.
IMPORTANT = (1, 2)


def main() -> int:
    conn = db.connect()
    schema = airtable.load_schema()
    never = rubric.tiers_with_delivery("never")
    sql, params = airtable_sync.candidate_query(
        schema, never, "hash, tier, first_seen, company, title"
    )
    rows = [dict(r) for r in conn.execute(sql, params)]
    cap = schema.max_posting_records
    kept, excluded = rows[:cap], rows[cap:]

    print(f"{len(rows)} posting(s) deserve a slot, {cap} slots available")
    print(f"  in the base:  {len(kept)}")
    print(f"  excluded:     {len(excluded)}")

    def by_tier(items):
        return dict(sorted(Counter(r["tier"] or 0 for r in items).items()))

    print(f"\n  in the base by tier:  {by_tier(kept)}")
    if not excluded:
        print("\nNothing is excluded. The cap does not bind.")
        return 0
    print(f"  excluded by tier:     {by_tier(excluded)}")
    newest = max(r["first_seen"] for r in excluded)[:10]
    print(f"  newest excluded posting was first seen {newest}")

    missing = [r for r in excluded if r["tier"] in IMPORTANT]
    print()
    if not missing:
        print("HEALTHY: every tier 1 and tier 2 posting has a row. The excluded "
              "set is\nlower-tier overflow, which is the sort working rather "
              "than a problem.")
        return 0

    print(f"ACT: {len(missing)} posting(s) in tier {' or '.join(map(str, IMPORTANT))} "
          "have no row.")
    for r in missing[:15]:
        print(f"  tier {r['tier']}  {str(r['company'])[:24]:24} {str(r['title'])[:44]}")
    print("\nRaise max_posting_records if the record budget allows (the free plan "
          "caps the\nwhole base at 1,000), or shorten max_row_age_days. Both are "
          "in sources/airtable.toml.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
