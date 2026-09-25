"""Merge the duplicate rows the old identity rule left behind.

    .venv/bin/python -m tools.reconcile_identity              # read only, shows the plan
    .venv/bin/python -m tools.reconcile_identity --samples 8  # show example merges
    .venv/bin/python -m tools.reconcile_identity --apply      # do it, after a backup

Until 2026-08-12 a posting was identified by its company, title and location, so
a company editing any of those closed the old row and stored the edited posting
as a new one. agent/fetchers._identity now keys on the ATS requisition id
instead, which stops it happening again, but it does nothing about the rows
already split. This does that, once.

For each group of rows sharing one identity it keeps a single survivor, gives it
the earliest first_seen and everything the owner or the pipeline had written
across the group, and deletes the rest.

Two things it deliberately does not do.

It does not merge a group whose rows were all seen in the same most recent poll.
Those are not an edit history, they are a source using one id for two live
postings, and collapsing them would lose a real posting. Those rows are given
the content-hash identity instead, which keeps them apart, and are counted
separately below.

It does not touch Airtable. Where a merged row carried an Airtable record, the
survivor inherits that record's hash along with the link, because the base joins
on hash and a survivor with its own hash would orphan the label. The owner's
labels move to the surviving posting rather than being stranded on a phantom.
"""

import argparse
import shutil
import sys
from collections import defaultdict

from agent import config, db
from agent.fetchers import CONTENT_PREFIX

# Columns worth rescuing from a row that is about to be deleted, in the sense
# that losing one would lose work. The survivor keeps its own value wherever it
# has one; these fill only the gaps it has.
INHERITED = [
    "label",
    "label_reason",
    "applied_status",
    "fit_override",
    "reach_override",
    "fit_score",
    "reach_score",
    "tier",
    "reason",
    "alerted_at",
    "alert_type",
    "airtable_record_id",
    "airtable_synced_at",
    "tagged_at",
    "term",
    "term_stated",
    "term_evidence",
    "weekly_hours",
    "hours_evidence",
    "stated_deadline",
    "stage0_source",
]

# Values that mean "nothing has been written here yet" and so must not win over
# a real value on another row in the group.
EMPTY = {None, "", "unknown", "not_applied"}


def identity_of(row: dict) -> str:
    ext = (row["external_id"] or "").strip()
    if ext:
        return f"{row['source']}|{ext}"
    return f"{CONTENT_PREFIX}|{row['hash']}"


def groups(conn) -> dict[str, list[dict]]:
    found = defaultdict(list)
    for r in conn.execute("SELECT * FROM postings"):
        found[identity_of(dict(r))].append(dict(r))
    return {k: v for k, v in found.items() if len(v) > 1}


def is_collision(rows: list[dict]) -> bool:
    """True when the source is using one id for several postings at once.

    An edit history has exactly one row that the most recent poll saw. A
    collision has more than one, because the board really did emit both.
    """
    newest = max(r["last_seen_open"] for r in rows)
    live = [r for r in rows if r["last_seen_open"] == newest]
    return len(live) > 1


def plan_merge(rows: list[dict]) -> tuple[dict, list[dict], dict]:
    """Pick the survivor and work out what it should end up holding.

    The survivor is whichever row the most recent poll saw, because that row
    carries what the posting currently says. Everything of value on the others
    is folded into it.
    """
    ordered = sorted(rows, key=lambda r: (r["last_seen_open"], r["id"]))
    survivor = ordered[-1]
    losers = ordered[:-1]

    updates: dict = {
        # The posting was first seen when the earliest of these rows saw it. Any
        # later date is an artefact of the bug, and success criterion 1 measures
        # coverage against this.
        "first_seen": min(r["first_seen"] for r in rows),
        "identity": identity_of(survivor),
        "content_hash": survivor["hash"],
    }

    # Any row still open means the posting is open. The closures being undone
    # here are the false ones the old rule manufactured.
    if any(r["closed_detected_at"] is None for r in rows):
        updates["closed_detected_at"] = None
        updates["consecutive_misses"] = 0

    for col in INHERITED:
        if survivor.get(col) not in EMPTY:
            continue
        for other in reversed(losers):
            if other.get(col) not in EMPTY:
                updates[col] = other[col]
                break

    # Airtable joins on hash, so the link and the key have to travel together.
    if survivor["airtable_record_id"] is None:
        donor = next(
            (r for r in reversed(losers) if r["airtable_record_id"] is not None), None
        )
        if donor is not None:
            updates["hash"] = donor["hash"]
            updates["content_hash"] = survivor["hash"]

    return survivor, losers, updates


def describe(survivor: dict, losers: list[dict], updates: dict) -> str:
    lines = [f"  {survivor['company']}: {survivor['title']}"]
    lines.append(f"    keep  #{survivor['id']}  {survivor['location']}")
    for l in losers:
        state = "closed" if l["closed_detected_at"] else "open"
        lines.append(
            f"    drop  #{l['id']}  {l['title']} / {l['location']}  ({state})"
        )
    carried = sorted(set(updates) - {"identity", "content_hash"})
    if carried:
        lines.append(f"    carry {', '.join(carried)}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write the merges")
    ap.add_argument("--samples", type=int, default=4, help="example merges to print")
    args = ap.parse_args()

    conn = db.connect()
    found = groups(conn)

    merges = []
    collisions = []
    for identity, rows in found.items():
        if is_collision(rows):
            collisions.append((identity, rows))
        else:
            merges.append((identity, rows))

    rows_removed = 0
    phantom_closures = 0
    reopened = 0
    for _, rows in merges:
        survivor, losers, updates = plan_merge(rows)
        rows_removed += len(losers)
        phantom_closures += sum(1 for l in losers if l["closed_detected_at"])
        if survivor["closed_detected_at"] and "closed_detected_at" in updates:
            reopened += 1

    print(f"{len(found)} board ids point at more than one row.")
    print(f"  {len(merges)} are one posting that was edited, and merge.")
    print(f"    {rows_removed} rows go away.")
    # These are the rows PRD success criterion 1 has been reading as postings
    # that closed without ever being alerted on. They never closed and were
    # never postings; deleting them is what makes the coverage audit honest.
    print(f"    {phantom_closures} of them are closures that never happened.")
    print(f"    {reopened} postings are open again that were wrongly closed.")
    print(f"  {len(collisions)} are one id used for several live postings, not an error.")
    print("    Those keep separate rows, keyed on content as before.")

    if args.samples and merges:
        print("\nExamples:")
        for _, rows in merges[: args.samples]:
            print(describe(*plan_merge(rows)))

    if not args.apply:
        print("\nRead-only. Re-run with --apply to write it.")
        conn.close()
        return 0

    # Named to end in .db so .gitignore's *.db rule catches it. A backup of the
    # source of truth is the last thing that should end up in a commit.
    stamp = db.now().replace(":", "").replace("+0000", "")
    backup = config.DB_PATH.with_name(f"{config.DB_PATH.stem}.bak-{stamp}.db")
    conn.close()
    shutil.copy2(config.DB_PATH, backup)
    print(f"\nBacked up to {backup.name}")

    conn = db.connect()
    for _, rows in merges:
        survivor, losers, updates = plan_merge(rows)
        # Losers go first. The survivor may be adopting a loser's hash, and hash
        # is UNIQUE, so both rows cannot hold it even for one statement.
        conn.executemany(
            "DELETE FROM postings WHERE id = ?", [(l["id"],) for l in losers]
        )
        sets = ", ".join(f"{c} = ?" for c in updates)
        conn.execute(
            f"UPDATE postings SET {sets} WHERE id = ?",
            list(updates.values()) + [survivor["id"]],
        )

    for identity, rows in collisions:
        conn.executemany(
            "UPDATE postings SET identity = ?, content_hash = ? WHERE id = ?",
            [(f"{CONTENT_PREFIX}|{r['hash']}", r["hash"], r["id"]) for r in rows],
        )

    conn.commit()

    left = groups(conn)
    still = {k: v for k, v in left.items() if not is_collision(v)}
    print(f"Merged. {rows_removed} rows deleted, {len(still)} unresolved groups left.")
    conn.close()
    return 0 if not still else 1


if __name__ == "__main__":
    sys.exit(main())
