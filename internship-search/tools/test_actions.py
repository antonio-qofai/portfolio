"""The action block, the applied date, and the owner's own closure. 2026-08-19.

    .venv/bin/python -m tools.test_actions

Spends nothing, sends nothing, and never touches state.db.

Three features are tested together because they are one loop: he opens the
Interested view, flips Applied status or ticks Closed, and the digest stops
asking him about it. Each of the three has a way of failing that leaves no trace
anywhere, which is why they are tested rather than looked at.

    the action block   repeats forever by design, so a bug that stamps it would
                       silently empty his to-do list one item at a time
    applied_at         written once, on a transition the sync sees and nothing
                       records afterwards. Miss it and it is unrecoverable
    closed_by_me       prunes an Airtable row. Prune without the matching push
                       exclusion and the row is deleted and recreated forever,
                       which is CLAUDE.md rule 9

Run this after editing agent/delivery.action_content, agent/db.action_items,
the [actions] block in sources/email.toml, or the pull step of
agent/airtable_sync.
"""

import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent import airtable, airtable_sync, db, delivery, notify

PASS, FAIL = "ok  ", "FAIL"
results: list[tuple[bool, str, str]] = []


def check(name: str, got, want, note: str = "") -> None:
    results.append((got == want, name, note or f"got {got!r}, wanted {want!r}"))


def ts(days_ago: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(
        timespec="seconds"
    )


def make_db() -> sqlite3.Connection:
    tmp = Path(tempfile.mkdtemp()) / "test.db"
    return db.connect(tmp)


def add(conn, **kw) -> int:
    row = {
        "hash": kw.get("hash") or f"h{kw.get('n', 0)}",
        "identity": kw.get("identity") or f"i{kw.get('n', 0)}",
        "content_hash": kw.get("hash") or f"h{kw.get('n', 0)}",
        "company": "Acme",
        "title": f"Intern {kw.get('n', 0)}",
        "location": "Chicago, IL",
        "url": "https://example.test/1",
        "source": "greenhouse:acme",
        "first_seen": ts(10),
        "last_seen_open": ts(0),
        "prefilter_verdict": "surface",
        "applied_status": "not_applied",
        "flags": "",
        "term": "summer2027",
    }
    row.update({k: v for k, v in kw.items() if k != "n"})
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    cur = conn.execute(
        f"INSERT INTO postings ({cols}) VALUES ({marks})", list(row.values())
    )
    conn.commit()
    return cur.lastrowid


CFG = {
    "enabled": True,
    "max_items": 3,
    "waiting_statuses": ["applied", "interviewing"],
    "decision_statuses": ["offer"],
    "silent_after_days": 21,
}
RULES = {"actions": CFG, "collapse": {"enabled": True, "join": "; ", "max_locations": 4}}


# --------------------------------------------------------------- the block

def test_only_interested_and_unapplied_is_work():
    """The list is what he said yes to and has not done. Nothing else."""
    conn = make_db()
    add(conn, n=1, label="interested")
    add(conn, n=2, label="interested", applied_status="applied")
    add(conn, n=3, label="not_interested")
    add(conn, n=4)  # unlabelled: the agent's job to rank, not his to act on
    add(conn, n=5, label="interested", prefilter_verdict="killed")
    add(conn, n=6, label="interested", closed_detected_at=ts(1))

    content = delivery.action_content(conn, cfg=CFG, email_rules=RULES)
    check(
        "only the interested and unapplied are work",
        sorted(g.lead["title"] for g in content["apply"]),
        ["Intern 1"],
    )
    check("an applied role is counted as waiting", content["waiting"], 1)


def test_deadline_leads_then_age():
    """Whatever is closest to being lost is first, then the oldest."""
    conn = make_db()
    add(conn, n=1, label="interested", first_seen=ts(30))
    add(conn, n=2, label="interested", first_seen=ts(2), stated_deadline="2027-01-01")
    add(conn, n=3, label="interested", first_seen=ts(60))

    content = delivery.action_content(conn, cfg=CFG, email_rules=RULES)
    check(
        "a stated deadline outranks age, then oldest first",
        [g.lead["title"] for g in content["apply"]],
        ["Intern 2", "Intern 3", "Intern 1"],
    )


def test_nothing_is_ever_stamped():
    """CLAUDE.md rule 10. This is a to-do list, so it repeats until he moves it.

    The failure this guards against is the one that leaves no evidence: an
    action block that stamped alerted_at would look identical on the day it ran
    and would have quietly destroyed the coverage of every posting it listed.
    """
    conn = make_db()
    add(conn, n=1, label="interested")
    before = dict(conn.execute("SELECT * FROM postings WHERE hash='h1'").fetchone())

    delivery.action_content(conn, cfg=CFG, email_rules=RULES)
    notify._action_lines(
        delivery.action_content(conn, cfg=CFG, email_rules=RULES), RULES["collapse"]
    )

    after = dict(conn.execute("SELECT * FROM postings WHERE hash='h1'").fetchone())
    check("building the block writes nothing at all", after, before)
    check(
        "and it is still there the second time",
        len(delivery.action_content(conn, cfg=CFG, email_rules=RULES)["apply"]),
        1,
    )


def test_cap_reports_the_remainder():
    conn = make_db()
    for i in range(1, 6):
        add(conn, n=i, label="interested", first_seen=ts(30 - i))
    content = delivery.action_content(conn, cfg=CFG, email_rules=RULES)
    check("the cap shows three", len(content["apply"]), 3)
    check("and counts the other two", content["over_cap"], 2)
    body = "\n".join(notify._action_lines(content, RULES["collapse"]))
    check("the total is what the heading says", "not applied (5)" in body, True)


def test_silence_needs_a_date_to_be_measured():
    """An application with no applied_at is waiting, never silent.

    Every application logged before 2026-08-19 has no date, because nothing was
    recording one. Calling those silent would be inventing a date the database
    does not have; calling them nothing at all would lose them.
    """
    conn = make_db()
    add(conn, n=1, label="interested", applied_status="applied", applied_at=ts(60))
    add(conn, n=2, label="interested", applied_status="applied", applied_at=ts(3))
    add(conn, n=3, label="interested", applied_status="applied")

    content = delivery.action_content(conn, cfg=CFG, email_rules=RULES)
    check("only the old one is called silent", [r["hash"] for r in content["silent"]], ["h1"])
    check("all three are still counted as waiting", content["waiting"], 3)


def test_an_offer_leads_everything():
    conn = make_db()
    add(conn, n=1, label="interested", applied_status="offer", applied_at=ts(5))
    content = delivery.action_content(conn, cfg=CFG, email_rules=RULES)
    check("an offer is a decision, not a wait", len(content["decide"]), 1)
    check("and is not counted as waiting", content["waiting"], 0)
    body = "\n".join(notify._action_lines(content, RULES["collapse"]))
    check("it leads the block", body.splitlines()[2].strip().startswith("OFFER"), True)


# ------------------------------------------------------------- applied_at

def test_applied_at_is_stamped_on_the_transition():
    row = {"applied_at": None}
    stamp = airtable_sync._applied_stamp(row, {"applied_status": "applied"})
    check("moving off not_applied stamps a date", bool(stamp), True)

    row = {"applied_at": "2026-08-01T00:00:00+00:00"}
    check(
        "a date already there is never rewritten",
        airtable_sync._applied_stamp(row, {"applied_status": "interviewing"}),
        None,
    )
    check(
        "going back to not applied clears it",
        airtable_sync._applied_stamp(row, {"applied_status": "not_applied"}),
        "",
    )
    check(
        "and clearing an empty one is not a change",
        airtable_sync._applied_stamp({"applied_at": None}, {"applied_status": "not_applied"}),
        None,
    )
    check(
        "a base with no Applied status field stamps nothing",
        airtable_sync._applied_stamp({"applied_at": None}, {"label": "interested"}),
        None,
    )


def test_checkbox_survives_a_round_trip():
    """A checkbox Airtable omits when unticked must still reach SQLite as 0.

    Returning null would compare unequal to the stored 0 on every single sync,
    so every row in the base would report as changed forever.
    """
    field = airtable.Field(name="Closed", type="checkbox", column="closed_by_me")
    check("an unticked box is absent from the payload", field.to_db(None), 0)
    check("an explicit false is also 0", field.to_db(False), 0)
    check("a tick is 1", field.to_db(True), 1)
    check("0 is never pushed back as a value", field.to_airtable(0), None)
    check("1 is", field.to_airtable(1), True)


# ------------------------------------------------------------ closed_by_me

def test_his_closure_removes_a_posting_from_every_offer():
    """One tick has to reach all of them. A path that missed it would keep
    emailing him about a role he has already told the agent is gone."""
    conn = make_db()
    add(conn, n=1, label="interested", tier=1, first_seen=ts(30), closed_by_me=1)
    add(conn, n=2, label="interested", tier=1, first_seen=ts(30))

    live = ["h2"]
    check("surfaced", [r["hash"] for r in db.surfaced(conn)], live)
    check("carryover", [r["hash"] for r in db.carryover(conn, hours=24 * 40)], live)
    check("unscored, so it is never paid to rank", [r["hash"] for r in db.unscored(conn)], live)
    check(
        "roundup",
        [r["hash"] for r in db.roundup_candidates(conn, [1], days=40)],
        live,
    )
    check(
        "urgent by age",
        [r["hash"] for r in db.urgent_by_age(conn, [1], days=1, needs_action=False)],
        live,
    )
    check(
        "the action block",
        [g.lead["hash"] for g in delivery.action_content(conn, cfg=CFG, email_rules=RULES)["apply"]],
        live,
    )


def test_closure_is_reversible_and_is_not_the_watchers_column():
    """His tick and the watcher's closure are different facts.

    If the tick wrote closed_detected_at, the next poll that sees the posting
    still listed would clear it, and the role he told the agent was gone would
    come back. Boards list dead postings for weeks, which is the entire reason
    this exists.
    """
    conn = make_db()
    add(conn, n=1, label="interested", closed_by_me=1)
    row = dict(conn.execute("SELECT * FROM postings WHERE hash='h1'").fetchone())
    check("the watcher's column is untouched", row["closed_detected_at"], None)

    conn.execute("UPDATE postings SET closed_by_me=0 WHERE hash='h1'")
    conn.commit()
    check(
        "unticking brings it back",
        [r["hash"] for r in db.surfaced(conn)],
        ["h1"],
    )


def test_an_application_always_keeps_its_airtable_row():
    """Two bugs found on 2026-09-25 when the Applied view came up empty-ish.

    A posting he applied to must have a row whatever else is true of it, because
    the Applied view is the only place that answers "where have I applied". Both
    failures were silent and both hid a real application.
    """
    from agent import airtable, airtable_sync, grouping

    schema = airtable.load_schema()
    push, _ = airtable_sync.candidate_query(schema, [4])
    prune, _ = airtable_sync.prune_query(schema, [4])
    # 1. A closed posting he applied to used to fall out, because only
    #    label='interested' excused a closure. His JP Morgan application
    #    vanished from the base the day that posting closed.
    check("a closed application is still a push candidate",
          "applied_status, 'not_applied') <> 'not_applied'" in push, True)
    check("and the prune carries the matching exemption",
          "applied_status, 'not_applied') = 'not_applied'" in prune, True)

    # 2. The location collapse led with the oldest city, so the group showed a
    #    role as not applied while his application sat on a hidden sibling.
    applied = {"applied_status": "applied", "id": 999}
    older = {"id": 1}
    check("the collapse leads with the row he applied to",
          grouping.by_oldest(applied) < grouping.by_oldest(older), True)
    labelled = {"label": "interested", "id": 1}
    check("an application outranks a label", 
          grouping.by_oldest(applied) < grouping.by_oldest(labelled), True)
    # skipped and missed mean he did NOT apply, so they must not win the lead.
    skipped = {"applied_status": "skipped", "id": 1}
    check("a skipped duplicate never beats a real application",
          grouping.by_oldest(applied) < grouping.by_oldest(skipped), True)


def test_a_rescore_clears_only_the_score():
    """`tools.rescore` must never touch anything the owner wrote.

    The tool exists because a posting is scored once, so a new label never
    revises a stored verdict. Clearing a score is cheap; clearing a label, an
    applied status or an alert stamp is not. An override is his own number and
    outranks the model's, and a cleared `alerted_at` would re-email a posting
    that was already sent, which is rule 10.

    Pinned as an explicit column list rather than "did the row survive", because
    the failure mode is one extra column in the UPDATE and that reads as a
    harmless tidy-up in a diff.
    """
    from tools import rescore

    protected = {
        "hash", "identity", "content_hash", "label", "label_reason",
        "applied_status", "applied_at", "fit_override", "reach_override",
        "alerted_at", "alert_type", "closure_alerted_at",
        "urgent_deadline_alerted_at", "urgent_stale_alerted_at",
        "closed_by_me", "airtable_record_id", "first_seen", "term", "tagged_at",
    }
    cleared = set(rescore.SCORE_COLUMNS)
    check("a rescore clears nothing the owner wrote", sorted(cleared & protected), [])
    check("and it does clear the tier", "tier" in cleared, True)
    check("and the score itself", "fit_score" in cleared, True)

    # The filter must exclude an overridden posting outright, not merely spare
    # its override column.
    class Args:
        all = True
        tier: list = []
        company = None
        since = None
        include_closed = False
        include_killed = False
    clause, _ = rescore.build_filter(Args())
    check("an overridden posting is excluded from the filter",
          "fit_override IS NULL" in clause and "reach_override IS NULL" in clause,
          True)
    check("and only already-scored rows are selected",
          "fit_score IS NOT NULL" in clause, True)


def test_a_bare_date_does_not_crash_the_watcher():
    """The 2026-09-22 outage. A naive applied_at must not raise.

    `tools.log_application` wrote applied_at as "2026-08-15" while the sync had
    always written a full db.now() with an offset. `fromisoformat` returns a
    naive datetime for the first, comparing it to an aware cutoff raised
    TypeError inside action_content, and because the watcher is a required step
    every run stopped there. Seven runs over two days polled nothing.

    Both halves are pinned. The parser must always return something aware, and
    the action block must survive a column holding either shape, because any
    column a person or a later tool can fill will eventually hold a date.
    """
    from agent import delivery as d

    naive = d._parse("2026-08-15")
    check("a bare date parses", naive is not None, True)
    check("and comes back timezone aware", naive.tzinfo is not None, True)
    aware = d._parse("2026-08-15T00:00:00+00:00")
    check("a full timestamp still parses", aware is not None, True)
    check("the two agree", naive, aware)
    check("junk is still None", d._parse("not a date"), None)
    check("empty is still None", d._parse(""), None)

    # End to end, the way it actually broke: a row whose applied_at has no
    # offset, run through the real action block.
    conn = make_db()
    add(conn, n=1, hash="hb1", identity="ib1", label="interested",
        applied_status="applied", applied_at="2026-08-15")
    try:
        content = delivery.action_content(conn)
        raised = None
    except TypeError as exc:  # noqa: BLE001 - the exact failure being pinned
        content, raised = None, exc
    check("action_content survives a bare date", raised, None)
    check("and counts it as an application", content["waiting"], 1)


def test_the_prune_and_the_push_agree():
    """CLAUDE.md rule 9. Every prune rule needs the matching push exclusion.

    Asked of the assembled SQL, not of the source text. This case used to search
    `agent/airtable_sync.py` for the literal "rows = [" and read the slice
    between two markers, which broke the moment that query moved into
    `candidate_query` on 2026-09-20 without anything about the invariant
    changing. A test that pins the shape of the source fails on refactors and
    passes on real regressions, which is the wrong way round.

    The demotion half of the pairing is checked properly, with fixtures, in
    `tools.test_airtable_slice`. What is left here is the closed_by_me rule,
    which is the one the owner drives by hand.
    """
    from agent import airtable, airtable_sync

    schema = airtable.load_schema()
    prune_sql, _ = airtable_sync.prune_query(schema, [4])
    push_sql, _ = airtable_sync.candidate_query(schema, [4])
    check(
        "the prune takes rows he closed",
        "COALESCE(closed_by_me, 0) = 1" in prune_sql,
        True,
    )
    check(
        "and the push refuses to recreate them",
        "COALESCE(closed_by_me, 0) = 0" in push_sql,
        True,
    )
    check(
        "the push excludes exactly what the prune demotes",
        airtable_sync._demotion(schema, [4])[0] in push_sql,
        True,
    )
    check(
        "and the prune demotes exactly what the push excludes",
        airtable_sync._demotion(schema, [4])[0] in prune_sql,
        True,
    )


def main() -> int:
    for fn in [
        test_an_application_always_keeps_its_airtable_row,
        test_a_rescore_clears_only_the_score,
        test_a_bare_date_does_not_crash_the_watcher,
        test_only_interested_and_unapplied_is_work,
        test_deadline_leads_then_age,
        test_nothing_is_ever_stamped,
        test_cap_reports_the_remainder,
        test_silence_needs_a_date_to_be_measured,
        test_an_offer_leads_everything,
        test_applied_at_is_stamped_on_the_transition,
        test_checkbox_survives_a_round_trip,
        test_his_closure_removes_a_posting_from_every_offer,
        test_closure_is_reversible_and_is_not_the_watchers_column,
        test_the_prune_and_the_push_agree,
    ]:
        try:
            fn()
        except Exception as exc:
            # A case that raises is a failure, not the end of the run. Without
            # this one broken assumption hides every case after it, which is
            # exactly what a regression looks like from the outside.
            results.append((False, fn.__name__, f"raised {type(exc).__name__}: {exc}"))

    failed = 0
    for ok, name, note in results:
        if ok:
            print(f"{PASS} {name}")
        else:
            failed += 1
            print(f"{FAIL} {name}: {note}")

    print(f"\n{len(results) - failed} of {len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
