"""Cases the posting identity rule must get right. Run with:

    .venv/bin/python -m tools.test_identity

No test framework and no live database. Each case builds a throwaway SQLite file,
polls it twice with postings that differ the way a real board differs between two
runs, and checks what the second poll did to the first poll's rows.

This exists because the failure it guards is silent in both directions. Before
2026-08-12 a company editing a location produced a closure email for a job that
was still open and a discovery email for a job already seen, and neither looked
wrong from the outside. The opposite mistake, merging two postings that only
look alike, would hide a real job and produce no evidence at all.

Add a case here whenever the identity rule changes.
"""

import sys
import tempfile
from pathlib import Path

from agent import db
from agent.fetchers import Posting, resolve_identities


def posting(**kw) -> Posting:
    base = {
        "company": "Example Corp",
        "title": "Software Engineer Intern",
        "location": "San Francisco, CA",
        "url": "https://example.com/1",
        "source": "greenhouse:example",
        "ats": "greenhouse",
        "external_id": "1001",
        "description": "Build things.",
    }
    base.update(kw)
    return Posting(**base)


def poll(conn, postings, source="greenhouse:example", stats=None) -> tuple[list, list]:
    """One full run against one source: store what came back, age what did not."""
    resolve_identities(postings)
    new = db.upsert_seen(conn, postings, stats)
    closed = db.age_missing(conn, source, {p.identity for p in postings})
    return new, closed


def rows(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM postings ORDER BY id")]


# Each case takes a fresh connection and returns a list of failure strings.
def case_location_edit(conn) -> list[str]:
    """The bug this fix exists for. Editing a location must not churn the row."""
    poll(conn, [posting(location="San Francisco, CA")])
    first_seen = rows(conn)[0]["first_seen"]

    # Two polls, because closure needs CLOSURE_MISS_THRESHOLD consecutive misses.
    new, closed = poll(conn, [posting(location="New York, NY")])
    poll(conn, [posting(location="New York, NY")])

    out = []
    stored = rows(conn)
    if len(stored) != 1:
        out.append(f"expected 1 row after a location edit, got {len(stored)}")
    if new:
        out.append("an edited posting was reported as a new discovery")
    if closed:
        out.append("an edited posting was reported as closed")
    if stored and stored[0]["location"] != "New York, NY":
        out.append(f"location was not updated: {stored[0]['location']}")
    if stored and stored[0]["first_seen"] != first_seen:
        out.append("first_seen was reset by an edit")
    if stored and stored[0]["closed_detected_at"] is not None:
        out.append("an open posting was marked closed")
    return out


def case_title_edit(conn) -> list[str]:
    """A rename is the same bug. It must also re-open the Stage A question."""
    poll(conn, [posting(title="Trainer, Air Defense")])
    conn.execute("UPDATE postings SET prefilter_verdict='killed', label='no'")
    conn.commit()

    stats: dict = {}
    poll(conn, [posting(title="Trainer, Counter Intrusion")], stats=stats)

    out = []
    stored = rows(conn)
    if len(stored) != 1:
        out.append(f"expected 1 row after a rename, got {len(stored)}")
    if stats.get("retitled") != 1:
        out.append(f"the run did not report the rename: {stats}")
    if stored and stored[0]["title"] != "Trainer, Counter Intrusion":
        out.append("title was not updated")
    if stored and stored[0]["prefilter_verdict"] is not None:
        out.append("a renamed posting kept a verdict about its old title")
    if stored and stored[0]["label"] != "no":
        out.append("a rename lost the owner's label")
    return out


def case_real_closure(conn) -> list[str]:
    """The fix must not stop genuine closures being detected."""
    poll(conn, [posting()])
    poll(conn, [])
    _, closed = poll(conn, [])

    out = []
    if len(closed) != 1:
        out.append(f"a posting that vanished was not closed, got {len(closed)}")
    if rows(conn)[0]["closed_detected_at"] is None:
        out.append("closed_detected_at was not stamped")
    return out


def case_distinct_ids_stay_distinct(conn) -> list[str]:
    """Some boards issue one id per city. Those are separate rows, as before."""
    new, _ = poll(
        conn,
        [
            posting(external_id="a", location="San Francisco"),
            posting(external_id="b", location="New York"),
        ],
    )
    out = []
    if len(rows(conn)) != 2:
        out.append(f"per-city ids were collapsed into {len(rows(conn))} row(s)")
    if len(new) != 2:
        out.append("per-city postings were not both reported as new")
    return out


def case_id_collision(conn) -> list[str]:
    """One id on two live postings tells us nothing, so content decides instead."""
    poll(
        conn,
        [
            posting(external_id="dup", title="Trainer, Air Defense"),
            posting(external_id="dup", title="Trainer, Counter Intrusion"),
        ],
    )
    out = []
    stored = rows(conn)
    if len(stored) != 2:
        out.append(f"a colliding id collapsed two live postings into {len(stored)}")
    if len({r["identity"] for r in stored}) != 2:
        out.append("colliding postings were given the same identity")
    return out


def case_cross_source_dedup(conn) -> list[str]:
    """Both feeds carry the same jobs under their own ids. Store the job once."""
    poll(conn, [posting(source="feed:simplify", external_id="s1")], "feed:simplify")
    new, _ = poll(
        conn, [posting(source="feed:vanshb03", external_id="v1")], "feed:vanshb03"
    )

    out = []
    stored = rows(conn)
    if len(stored) != 1:
        out.append(f"the same job from two feeds made {len(stored)} rows")
    if new:
        out.append("a job already known from another feed was reported as new")
    if stored and stored[0]["source"] != "feed:simplify":
        out.append("the second feed took ownership of a row it did not discover")
    return out


def case_no_external_id(conn) -> list[str]:
    """With no id from the board, content is the only thing left to key on."""
    poll(conn, [posting(external_id="")])
    poll(conn, [posting(external_id="")])

    out = []
    stored = rows(conn)
    if len(stored) != 1:
        out.append(f"an id-less posting was stored {len(stored)} times")
    if stored and not stored[0]["identity"].startswith("content|"):
        out.append(f"unexpected identity: {stored[0]['identity']}")
    return out


def case_recycled_row_key(conn) -> list[str]:
    """A second requisition inheriting an edited posting's old content hash.

    This is the crash of 2026-08-17. The row key is frozen at discovery and the
    content hash follows the company's edits, so the two drift apart; a
    different requisition that happens to carry the pre-edit title and location
    then computes the key the first row is still using. It is a genuinely new
    posting, so neither lookup in `_match` finds it, and before the fix the
    insert hit the unique index and took the whole run down with it.
    """
    poll(conn, [posting(external_id="5304879008", location="San Francisco, CA")])
    stranded = rows(conn)[0]["hash"]

    # The company edits the stored posting, which moves its content hash and
    # leaves its row key pointing at a location it no longer claims.
    poll(conn, [posting(external_id="5304879008", location="San Francisco, CA | Seattle, WA")])

    # A different requisition now arrives carrying the original location.
    new, _ = poll(
        conn,
        [
            posting(external_id="5304879008", location="San Francisco, CA | Seattle, WA"),
            posting(external_id="5188391008", location="San Francisco, CA"),
        ],
    )

    out = []
    stored = rows(conn)
    if len(stored) != 2:
        out.append(f"two requisitions were stored as {len(stored)} rows")
    if len({r["hash"] for r in stored}) != len(stored):
        out.append("two rows were given the same permanent row key")
    if len(new) != 1:
        out.append(f"the second requisition was reported as {len(new)} new postings")
    if new and new[0]["hash"] not in {r["hash"] for r in stored}:
        out.append("the reported new posting names a key the table does not hold")

    first = [r for r in stored if r["identity"].endswith("5304879008")]
    if not first:
        out.append("the edited posting lost its row")
    elif first[0]["hash"] != stranded:
        out.append("an edit recomputed the permanent row key")
    return out


def case_backfill(conn) -> list[str]:
    """A row stored under the old rule must be recognised, not rediscovered."""
    poll(conn, [posting()])
    # Put the row back the way the old code left it: no identity, no content_hash.
    conn.execute("UPDATE postings SET identity = NULL, content_hash = NULL")
    conn.commit()
    db.migrate(conn)

    stored = rows(conn)[0]
    out = []
    if stored["identity"] != "greenhouse:example|1001":
        out.append(f"backfill produced {stored['identity']}")

    new, _ = poll(conn, [posting(location="Austin, TX")])
    if new:
        out.append("a backfilled row was rediscovered after an edit")
    if len(rows(conn)) != 1:
        out.append("a backfilled row was duplicated by an edit")
    return out


CASES = [
    ("a location edit keeps one open row", case_location_edit),
    ("a rename keeps the row and re-opens the verdict", case_title_edit),
    ("a genuine closure is still detected", case_real_closure),
    ("one id per city stays one row per city", case_distinct_ids_stay_distinct),
    ("one id on two live postings falls back to content", case_id_collision),
    ("the same job from both feeds is stored once", case_cross_source_dedup),
    ("a posting with no id keys on content", case_no_external_id),
    ("a row from before the fix is backfilled, not rediscovered", case_backfill),
    ("a second requisition inherits an edited row's old key", case_recycled_row_key),
]


def main() -> int:
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        for i, (label, fn) in enumerate(CASES):
            conn = db.connect(Path(tmp) / f"case{i}.db")
            problems = fn(conn)
            conn.close()
            if problems:
                failures += 1
                print(f"FAIL  {label}")
                for p in problems:
                    print(f"        {p}")
            else:
                print(f"ok    {label}")

    print()
    if failures:
        print(f"{failures} of {len(CASES)} cases failed.")
        return 1
    print(f"All {len(CASES)} cases pass.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
