"""Close postings stored under a source the agent no longer polls.

    .venv/bin/python -m tools.retire_sources --dry-run   # what would close
    .venv/bin/python -m tools.retire_sources             # close them

WHY THIS EXISTS.

A posting closes when the source that listed it answers without it
(`db.age_missing`). A source removed from `sources/companies.toml` or
`sources/feeds.toml` never answers again, so everything it stored stays open
forever: counted as open, eligible for Airtable, and never closed. Added
2026-09-25, when four companies were repointed to new boards and 516 rows were
left behind under the old keys.

Nothing here names a company (CLAUDE.md rule 2). A source is retired exactly
when its key is in the database and in neither token map.

WHAT IT SKIPS. A posting the owner labelled or applied to is left open and
listed. Closing it would put it in the digest's "CLOSED, AND YOU MARKED THESE
INTERESTED" block, which would be a false alarm: the role usually still exists
on the company's new board. He decides those by ticking Closed.

It writes `closed_detected_at` only, as the watcher would, and no stamp
(CLAUDE.md rule 10). Run `tools.sync_airtable` afterwards so the base drops them.
"""

from __future__ import annotations

import argparse
import sys

from agent import db, sources


def live_source_keys() -> set[str]:
    return ({c.source_key for c in sources.load_companies()}
            | {f.source_key for f in sources.load_feeds()})


def retired(conn, live: set[str]) -> tuple[list[dict], list[dict]]:
    """Open rows under a retired source, split into (to close, to leave for him)."""
    close, keep = [], []
    for row in conn.execute(
        "SELECT id, source, company, title, label, applied_status FROM postings "
        "WHERE closed_detected_at IS NULL"
    ):
        row = dict(row)
        if row["source"] in live:
            continue
        if row["label"] or (row["applied_status"] or "not_applied") != "not_applied":
            keep.append(row)
        else:
            close.append(row)
    return close, keep


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="print, store nothing")
    args = parser.parse_args()

    conn = db.connect()
    live = live_source_keys()
    # A token map that failed to load would make every source look retired and
    # close the whole database. Refuse rather than trust an empty map.
    if len(live) < 10:
        print(f"Only {len(live)} live sources loaded; refusing to retire anything.")
        return 1

    close, keep = retired(conn, live)
    by_source: dict[str, int] = {}
    for row in close:
        by_source[row["source"]] = by_source.get(row["source"], 0) + 1
    for source, n in sorted(by_source.items()):
        print(f"  {source}: {n} open posting(s) to close")
    for row in keep:
        print(f"  LEFT OPEN, yours to decide: {row['company']}: {row['title']} "
              f"({row['source']})")

    if args.dry_run:
        print(f"\n{len(close)} posting(s) would close. Nothing was written.")
        return 0
    stamp = db.now()
    conn.executemany(
        "UPDATE postings SET closed_detected_at=? WHERE id=?",
        [(stamp, row["id"]) for row in close],
    )
    conn.commit()
    print(f"\n{len(close)} posting(s) closed. Run tools.sync_airtable so the base drops them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
