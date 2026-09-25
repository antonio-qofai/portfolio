"""Which postings deserve an Airtable slot, and which get evicted.

    .venv/bin/python -m tools.test_airtable_slice

This exists because two bugs shipped through a dry run on 2026-09-20 and both
were invisible in its output. Neither would have survived a case here.

  1. `tier IN (?)` is NULL when tier is NULL, so `NOT (NULL AND ...)` is NULL
     and every unscored posting silently vanished from the push candidates. 733
     of them. The dry run reported a small number of creates and looked fine.

  2. The candidate query concatenates its WHERE clause before ORDER BY, so
     WHERE placeholders bind first. The parameters were passed the other way
     round, handing the feed prefix to `tier IN (?)` and a tier number to
     `source LIKE ?`. That disabled the push exclusion and the provenance
     ordering at once, reported nothing, and made a churn test pass for the
     wrong reason: the base was full, so no creates were possible either way.

The lesson in both is the same and it is why these cases run the REAL query
rather than testing `_demotion` in isolation. A predicate that is correct on its
own can still be bound to the wrong values, and the assembled statement is the
thing that runs.

The invariant the last group checks is CLAUDE.md rule 9: the prune and the push
must be exact complements. Every posting is either kept or demoted, never both
and never neither, or a row is deleted and recreated on every run forever.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent import airtable, airtable_sync, db

FAILED: list[str] = []
PASSED = 0


def check(label, got, want):
    global PASSED
    if got == want:
        PASSED += 1
        print(f"ok   {label}")
    else:
        FAILED.append(f"{label}: expected {want!r}, got {got!r}")
        print(f"FAIL {label}: expected {want!r}, got {got!r}")


def ts(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(
        timespec="seconds"
    )


def make_db() -> sqlite3.Connection:
    tmp = Path(tempfile.mkdtemp()) / "slice.db"
    conn = db.connect(tmp)
    conn.row_factory = sqlite3.Row
    return conn


def add(conn, n, age_days=1.0, **kw) -> None:
    row = {
        "hash": f"h{n}", "identity": f"i{n}", "content_hash": f"h{n}",
        "company": f"Co{n}", "title": f"Role {n}", "location": "Chicago, IL",
        "url": f"https://example.test/{n}", "source": "greenhouse:acme",
        "first_seen": ts(age_days), "last_seen_open": ts(0),
        "prefilter_verdict": "surface", "applied_status": "not_applied",
        "flags": "", "term": "summer2027",
    }
    row.update(kw)
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO postings ({cols}) VALUES ({marks})", tuple(row.values()))
    conn.commit()


class FakeSchema:
    """Only the fields `_demotion` and the candidate query read."""

    def __init__(self, age=30, exempt=(1, 2), prefer=True, sort_by_tier=True):
        self.sort_by_tier = sort_by_tier
        self.max_row_age_days = age
        self.age_exempt_tiers = exempt
        self.prefer_company_boards = prefer
        self.feed_source_prefix = "feed:"


BASE = (
    "prefilter_verdict='surface' AND COALESCE(closed_by_me, 0) = 0 "
    "AND (closed_detected_at IS NULL OR COALESCE(label, '') = 'interested') "
    "AND NOT (COALESCE(label, '') = 'not_interested' "
    "         AND TRIM(COALESCE(label_reason, '')) <> '')"
)


def candidates(conn, schema, never_tiers=(4,)) -> set[str]:
    """Run the sync's OWN query, assembled by the sync's own code.

    Calling `airtable_sync.candidate_query` rather than rebuilding the
    statement here is the whole point. A first version of this file assembled
    its own SQL and bound the parameters correctly, so it passed while the real
    sync was binding them backwards; the mutation that restored that bug did
    not fail a single case. A test that reimplements the thing it is testing
    tests nothing.
    """
    sql, params = airtable_sync.candidate_query(schema, list(never_tiers), "hash")
    return {r["hash"] for r in conn.execute(sql, params)}


def demoted(conn, schema, never_tiers=(4,)) -> set[str]:
    """Rows the prune would take, from the sync's own prune query.

    Restricted to rows that are actually in the base, the way the real prune is,
    then relaxed here because the fixtures carry no record ids: this asks the
    demotion half only, which is the half that must complement the push.
    """
    clause, params = airtable_sync._demotion(schema, list(never_tiers))
    if not clause:
        return set()
    return {
        r["hash"]
        for r in conn.execute(
            f"SELECT hash FROM postings WHERE {BASE} AND {clause}", params
        )
    }


def test_unscored_survives():
    """Bug 1. A NULL tier must not poison the predicate."""
    conn = make_db()
    add(conn, 1, age_days=2, tier=None)
    add(conn, 2, age_days=2, tier=3)
    got = candidates(conn, FakeSchema())
    check("a recent unscored posting is a candidate", "h1" in got, True)
    check("a recent tier 3 posting is a candidate", "h2" in got, True)
    check("a NULL tier does not empty the candidate set", len(got), 2)


def test_tier_and_age_evict():
    conn = make_db()
    add(conn, 1, age_days=2, tier=4)
    add(conn, 2, age_days=90, tier=3)
    add(conn, 3, age_days=90, tier=None)
    add(conn, 4, age_days=2, tier=3)
    got = candidates(conn, FakeSchema())
    check("a tier the rubric never delivers is evicted", "h1" in got, False)
    check("an old scored posting is evicted", "h2" in got, False)
    check("an old unscored posting is evicted", "h3" in got, False)
    check("a recent posting stays", "h4" in got, True)


def test_exemptions():
    conn = make_db()
    add(conn, 1, age_days=90, tier=1)
    add(conn, 2, age_days=90, tier=2)
    add(conn, 3, age_days=90, tier=None, label="interested")
    add(conn, 4, age_days=90, tier=4, label="interested")
    add(conn, 5, age_days=90, tier=4, applied_status="applied")
    got = candidates(conn, FakeSchema())
    check("tier 1 never ages out", "h1" in got, True)
    check("tier 2 never ages out", "h2" in got, True)
    check("a role you marked interested never ages out", "h3" in got, True)
    check("interested beats a never-delivered tier", "h4" in got, True)
    check("a role you applied to is never evicted", "h5" in got, True)


def test_prune_and_push_are_complements():
    """CLAUDE.md rule 9. The whole point of one shared predicate.

    Every posting must be on exactly one side. A posting on both is deleted and
    recreated on every run forever; a posting on neither silently disappears.
    """
    conn = make_db()
    n = 0
    for age in (1, 20, 60):
        for tier in (None, 1, 2, 3, 4):
            for label in ("", "interested"):
                for applied in ("not_applied", "applied"):
                    n += 1
                    add(conn, n, age_days=age, tier=tier, label=label,
                        applied_status=applied)
    schema = FakeSchema()
    keep = candidates(conn, schema)
    drop = demoted(conn, schema)
    allh = {r["hash"] for r in conn.execute("SELECT hash FROM postings")}
    check("no posting is both kept and demoted", sorted(keep & drop), [])
    check("no posting is neither", sorted(allh - keep - drop), [])
    check("every posting is accounted for", len(keep) + len(drop), n)


def test_switching_off():
    conn = make_db()
    add(conn, 1, age_days=900, tier=4)
    got = candidates(conn, FakeSchema(age=0), never_tiers=())
    check("with both rules off nothing is evicted", "h1" in got, True)


def test_real_schema_loads():
    """The live config must produce a usable predicate, not just a fake one."""
    schema = airtable.load_schema()
    clause, params = airtable_sync._demotion(schema, [4])
    check("the real schema builds a predicate", bool(clause), True)
    check("its parameter count matches its placeholders",
          clause.count("?"), len(params))


def main() -> int:
    for fn in (
        test_unscored_survives,
        test_tier_and_age_evict,
        test_exemptions,
        test_prune_and_push_are_complements,
        test_switching_off,
        test_real_schema_loads,
    ):
        fn()
    total = PASSED + len(FAILED)
    print()
    if FAILED:
        print(f"{len(FAILED)} of {total} FAILED")
        for f in FAILED:
            print(f"  {f}")
        return 1
    print(f"{PASSED} of {total} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
