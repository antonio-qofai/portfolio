"""Apply rubric.md's [[tier_cap]] rules to postings that are already scored.

    .venv/bin/python -m tools.reapply_caps --dry-run    # what would move, and why
    .venv/bin/python -m tools.reapply_caps              # store it

WHY THIS EXISTS.

A cap is applied in `ranker.settle`, the moment a posting is scored, and a
posting is scored once. So a cap added to `rubric.md` reaches only postings
scored after it, exactly as a prefilter edit reaches only postings decided after
it until `tools.prefilter_report --reapply` runs. This is that step for caps.
It makes no model call and costs nothing; `tools.rescore` is the one that
re-asks the model.

ONE-WAY, ON PURPOSE. It only ever lowers a tier (raises the number), starting
from the tier already stored. Removing a cap from rubric.md does not lift the
postings it held, because the stored tier no longer says what the model scored
them before the cap; `tools.rescore` on those postings is the way back.

WHAT IT NEVER TOUCHES. A posting with the owner's own `fit_override` is skipped,
because his number is never capped. Stamps, labels and applied status are left
alone, per CLAUDE.md rule 10: moving a posting out of tier 1 must not re-send or
un-send anything. Only `tier` changes. Run `tools.sync_airtable` afterwards so the
base shows it.
"""

from __future__ import annotations

import argparse
import sys

from agent import db, ranker, rubric


def changes(conn, cfg: dict | None = None) -> list[tuple[dict, int]]:
    """Every scored posting whose tier a cap would lower, with its new tier."""
    cfg = cfg or rubric.settings()
    out = []
    for row in conn.execute(
        "SELECT id, company, title, tier FROM postings "
        "WHERE tier IS NOT NULL AND fit_override IS NULL"
    ):
        row = dict(row)
        new = rubric.capped_tier(
            row["tier"], ranker.company_category(row["company"]), row["title"], cfg
        )
        if new != row["tier"]:
            out.append((row, new))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="print, store nothing")
    args = parser.parse_args()

    conn = db.connect()
    moved = changes(conn)
    for row, new in sorted(moved, key=lambda m: (m[0]["tier"], m[0]["company"])):
        print(f"  tier {row['tier']} -> {new}  {row['company']}: {row['title']}")
    if args.dry_run:
        print(f"\n{len(moved)} posting(s) would move. Nothing was written.")
        return 0
    conn.executemany(
        "UPDATE postings SET tier=? WHERE id=?", [(new, row["id"]) for row, new in moved]
    )
    conn.commit()
    print(f"\n{len(moved)} posting(s) moved. Run tools.sync_airtable so the base shows it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
