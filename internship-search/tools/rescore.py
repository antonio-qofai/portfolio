"""Clear stored scores so the ranker judges those postings again.

    .venv/bin/python -m tools.rescore --all --dry-run       # what it would cost
    .venv/bin/python -m tools.rescore --tier 3 --tier 4     # just the low bands
    .venv/bin/python -m tools.rescore --company palantir
    .venv/bin/python -m tools.rescore --since 2026-09-01    # scored before a date

WHY THIS EXISTS.

The ranker reads `db.unscored`, which selects on `fit_score IS NULL`, so a
posting is scored exactly once in its life. That is right for cost and wrong for
learning: the owner's labels are injected into every ranking prompt as examples, so
a new label changes how FUTURE postings are judged and never revises a score
already stored. Asked on 2026-09-25 whether the agent improves over time, the
honest answer was "yes, and only forward". This is the tool that makes it also
work backward, on demand.

DELIBERATELY NOT AUTOMATIC. Re-scoring on every label change would spend money
every time he touches a dropdown, and one new example among fifty rarely moves a
tier. Worth running after labelling a batch, not after labelling a row.

WHAT IT NEVER TOUCHES. `fit_override` and `reach_override` are the owner's own
numbers and outrank the model's by design, so they are left alone; a re-score
that wiped them would silently discard the strongest signal in the database. It
also leaves `label`, `applied_status`, `alerted_at` and the urgent stamps
untouched: a posting already emailed must not be emailed again just because it
was re-judged, which is CLAUDE.md rule 10.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

from agent import config, db

# Exactly the columns `db.record_score` writes, and nothing else. Kept beside
# that function in spirit: if it starts writing another column, this list has to
# grow with it or a re-score leaves half the old verdict behind.
SCORE_COLUMNS = ("fit_score", "reach_score", "tier", "reason", "scored_at", "scored_by")


def build_filter(args) -> tuple[str, list]:
    """The WHERE clause for what to clear, and its parameters."""
    where = ["fit_score IS NOT NULL"]
    params: list = []

    if not args.include_closed:
        where.append("closed_detected_at IS NULL")
    if not args.include_killed:
        where.append("prefilter_verdict = 'surface'")

    if args.tier:
        marks = ", ".join("?" for _ in args.tier)
        where.append(f"tier IN ({marks})")
        params.extend(args.tier)
    if args.company:
        where.append("lower(company) LIKE ?")
        params.append(f"%{args.company.lower()}%")
    if args.since:
        where.append("scored_at < ?")
        params.append(args.since)
    # An override means he has already judged it himself, so the model's opinion
    # is not what decides its tier and re-asking buys nothing.
    where.append("fit_override IS NULL AND reach_override IS NULL")
    return " AND ".join(where), params


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--all", action="store_true", help="every scored posting")
    ap.add_argument("--tier", type=int, action="append", default=[],
                    help="only this tier, repeatable")
    ap.add_argument("--company", help="company name contains this")
    ap.add_argument("--since", help="only postings scored before this ISO date")
    ap.add_argument("--include-closed", action="store_true",
                    help="also re-score closed postings, which is rarely useful")
    ap.add_argument("--include-killed", action="store_true",
                    help="also re-score postings the prefilter killed")
    ap.add_argument("--dry-run", action="store_true",
                    help="count and price it, write nothing")
    args = ap.parse_args()

    if not (args.all or args.tier or args.company or args.since):
        ap.error("say what to re-score: --all, --tier, --company or --since")

    conn = db.connect()
    conn.row_factory = sqlite3.Row
    clause, params = build_filter(args)

    rows = conn.execute(
        f"SELECT id, company, title, tier FROM postings WHERE {clause}", params
    ).fetchall()
    if not rows:
        print("Nothing matches. Already unscored, or the filter excluded it all.")
        return 0

    from collections import Counter
    tiers = dict(sorted(Counter(r["tier"] or 0 for r in rows).items()))
    print(f"{len(rows)} posting(s) would be re-scored, by current tier: {tiers}")
    print("\n  a sample:")
    for r in rows[:6]:
        print(f"    tier {r['tier']}  {str(r['company'])[:24]:24} {str(r['title'])[:44]}")

    calls = int(len(rows) * 1.3)
    print(f"\n  roughly {calls:,} model call(s) at about 1.3 per posting.")
    print("  `tools.rank_report` prices it once these are cleared; both stages "
          "cache,\n  so the real figure is far under a naive per-call estimate.")
    print(f"  At {config.RANK_MAX_CALLS} calls per run that is about "
          f"{max(1, calls // max(1, config.RANK_MAX_CALLS))} scheduled run(s) to drain.")

    if args.dry_run:
        print("\nDry run. Nothing was written.")
        return 0

    sets = ", ".join(f"{c} = NULL" for c in SCORE_COLUMNS)
    conn.execute(f"UPDATE postings SET {sets} WHERE {clause}", params)
    conn.commit()
    print(f"\nCleared the score on {len(rows)} posting(s). Your overrides, labels, "
          "applied\nstatuses and alert stamps are untouched. The ranker picks these "
          "up on the next\nrun, or run `tools.rank_report --run N` to start now.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
