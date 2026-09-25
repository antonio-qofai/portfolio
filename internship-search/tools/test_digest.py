"""Milestone 7's delivery rules, tested against a throwaway database.

    .venv/bin/python -m tools.test_digest

Spends nothing, sends nothing, and never touches state.db. Every case here is a
rule PRD section 3 states or a trap the build hit, and each one is named after
the thing that breaks if it regresses.

Run this after editing sources/email.toml, agent/delivery.py, agent/grouping.py
or the tier bands in rubric.md. A dry run of agent.run proves the wiring; this
proves the decisions.
"""

import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent import db, delivery, grouping, notify, rubric

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
    """Insert one posting. Everything not named takes a sane default."""
    row = {
        "hash": kw.get("hash") or f"h{kw.get('n', 0)}",
        "identity": kw.get("identity") or f"i{kw.get('n', 0)}",
        "content_hash": kw.get("hash") or f"h{kw.get('n', 0)}",
        "company": "Acme",
        "title": "Software Engineer Intern",
        "location": "Chicago, IL",
        "url": "https://example.test/1",
        "source": "greenhouse:acme",
        "first_seen": ts(1),
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


def rows(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM postings ORDER BY id")]


# --------------------------------------------------------------------- tests

def test_tier_routing():
    """Which email a tier goes to comes out of rubric.md, never out of code."""
    cfg = rubric.settings()
    check("tier 1 routes daily", rubric.delivery_for(1, cfg), "daily")
    # Tier 2 moved to the Sunday roundup on 2026-09-20, so only tier 1
    # interrupts him on a weekday. These assertions read the live rubric on
    # purpose: they pin that routing comes OUT of that file, and they are
    # expected to be edited whenever he edits the bands.
    check("tier 2 routes sunday", rubric.delivery_for(2, cfg), "sunday")
    check("tier 3 routes sunday", rubric.delivery_for(3, cfg), "sunday")
    check("tier 4 routes never", rubric.delivery_for(4, cfg), "never")
    check(
        "daily tiers read off the bands",
        rubric.tiers_with_delivery("daily", cfg),
        [1],
    )
    # An unscored posting still has no tier and still routes to its own bucket.
    # Whether that bucket is SHOWN is `daily.include_unscored`, which went false
    # on 2026-09-20; the classification below must not change with it, because
    # the count is what stops the exclusion being silent.
    check("unscored routes to its own bucket", delivery.route({"tier": None}), "unscored")
    check("tier 0 is not a tier", delivery.route({"tier": 0}), "unscored")


def test_split_holds_back_the_right_things():
    conn = make_db()
    for i, tier in enumerate([1, 2, 3, 4, None]):
        add(conn, n=i, hash=f"h{i}", identity=f"i{i}", tier=tier, fit_score=tier,
            title=f"Role {i}")
    split = delivery.split_daily(rows(conn))
    check("daily carries tier 1 alone", len(split["shown"]), 1)
    check("tiers 2 and 3 are held for the roundup", split["held_for_roundup"], 2)
    check("tier 4 is never emailed", split["not_emailed"], 1)
    # The load-bearing half of excluding unscored postings. They are counted
    # whether or not they are shown, so a digest that carries none of them still
    # says how many it passed over. An exclusion that also stopped counting
    # would hide a paused ranker completely, which is the failure CLAUDE.md
    # rule 10 was written about.
    check("unscored is counted even though it is not shown", split["unscored"], 1)
    check(
        "tier 1 leads the digest",
        split["shown"][0].get("tier"),
        1,
    )
    check(
        "no unscored posting reaches the daily digest",
        [r for r in split["shown"] if r.get("tier") in (None, 0)],
        [],
    )


def test_cap_does_not_consume_what_it_hides():
    """The trap: capping the digest at 8 must not stamp the 9th as alerted.

    A stamped posting is never offered again, so a cap that stamped its overflow
    would silently destroy coverage for everything past the eighth item.
    """
    conn = make_db()
    for i in range(12):
        add(conn, n=i, hash=f"h{i}", identity=f"i{i}", tier=1, fit_score=9,
            title=f"Role {i}")
    split = delivery.split_daily(rows(conn))
    check("cap holds at eight", len(split["shown"]), 8)
    check("overflow is reported", split["overflow"], 4)
    shown_hashes = {r["hash"] for g in split["shown"] for r in g.rows}
    check("overflow is not in the shown rows", len(shown_hashes), 8)


def test_collapse_groups_one_role_across_cities():
    conn = make_db()
    cities = ["Denver, CO", "New York, NY", "Washington, D.C.", "New York, NY"]
    for i, city in enumerate(cities):
        add(conn, n=i, hash=f"h{i}", identity=f"i{i}", tier=1, fit_score=9,
            title="Software Engineer, Internship", location=city)
    add(conn, n=9, hash="h9", identity="i9", tier=1, fit_score=9,
        title="Product Designer, Internship", location="Denver, CO")

    split = delivery.split_daily(rows(conn))
    check("four listings become one role", len(split["shown"]), 2)
    group = split["shown"][0]
    check("every city is carried", len(group.rows), 4)
    check("a repeated city is shown once", len(group.locations), 3)
    check(
        "the collapsed rows are all stamped",
        len(group.hashes()),
        4,
    )
    # Punctuation and case must not split a role, and a genuinely different
    # title must not be merged into one.
    check(
        "title normalisation ignores punctuation",
        grouping.normalize_title("Software Engineer, Internship"),
        grouping.normalize_title("software engineer internship"),
    )
    check(
        "two different roles stay apart",
        grouping.normalize_title("Software Engineer Intern")
        == grouping.normalize_title("Hardware Engineer Intern"),
        False,
    )


def test_airtable_lead_is_stable_and_the_email_lead_is_not():
    """The two callers want different leads and that is deliberate.

    Airtable takes the oldest row, because a lead that moved when a score landed
    would delete one record and create another on every sync. CLAUDE.md rule 9.
    The digest takes the best-scored row, because showing a group's blank lead
    would file a tier 1 role under no tier.
    """
    conn = make_db()
    add(conn, n=0, hash="old", identity="i0", tier=None, location="Denver, CO")
    add(conn, n=1, hash="new", identity="i1", tier=1, fit_score=9,
        location="New York, NY")
    groups = grouping.collapse(rows(conn), lead_key=grouping.by_oldest)
    check("airtable leads with the oldest row", groups[0].lead["hash"], "old")
    groups = grouping.collapse(rows(conn), lead_key=grouping.by_best)
    check("the digest leads with the scored row", groups[0].lead["hash"], "new")

    # A label beats age. Hiding the row he labelled would show a blank Label in
    # Airtable for a role he had already answered, which reads as lost data.
    conn = make_db()
    add(conn, n=0, hash="old", identity="i0", location="Denver, CO")
    add(conn, n=1, hash="labelled", identity="i1", location="New York, NY",
        label="interested")
    groups = grouping.collapse(rows(conn), lead_key=grouping.by_oldest)
    check("a labelled city leads its group", groups[0].lead["hash"], "labelled")


def test_urgent_triggers():
    conn = make_db()
    soon = (datetime.now(timezone.utc) + timedelta(days=2)).date().isoformat()
    late = (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()
    past = (datetime.now(timezone.utc) - timedelta(days=2)).date().isoformat()

    add(conn, n=0, hash="a", identity="a", tier=1, stated_deadline=soon)
    add(conn, n=1, hash="b", identity="b", tier=1, stated_deadline=late)
    add(conn, n=2, hash="c", identity="c", tier=1, stated_deadline=past)
    add(conn, n=3, hash="d", identity="d", tier=3, stated_deadline=soon)

    found = db.urgent_by_deadline(conn, [1], 72)
    check("a deadline inside the window fires", [r["hash"] for r in found], ["a"])

    # Ageing. first_seen is what the clock runs from.
    conn2 = make_db()
    add(conn2, n=0, hash="stale", identity="a", tier=1, first_seen=ts(20))
    add(conn2, n=1, hash="fresh", identity="b", tier=1, first_seen=ts(2))
    add(conn2, n=2, hash="labelled", identity="c", tier=1, first_seen=ts(20),
        label="interested")
    add(conn2, n=3, hash="applied", identity="d", tier=1, first_seen=ts(20),
        applied_status="applied")
    add(conn2, n=4, hash="lowtier", identity="e", tier=3, first_seen=ts(20))
    found = db.urgent_by_age(conn2, [1], 10)
    check(
        "only unactioned tier 1 postings age into urgent",
        [r["hash"] for r in found],
        ["stale"],
    )

    # The stamp is per trigger. Firing the age trigger must not silence a
    # deadline that appears later on the same posting.
    db.mark_urgent(conn2, found, delivery.STALE_STAMP)
    check("the age trigger fires once", len(db.urgent_by_age(conn2, [1], 10)), 0)
    conn2.execute(
        "UPDATE postings SET stated_deadline = ? WHERE hash = 'stale'", (soon,)
    )
    conn2.commit()
    check(
        "a later deadline still fires",
        len(db.urgent_by_deadline(conn2, [1], 72)),
        1,
    )

    # An empty tier list must match nothing, not everything.
    check("no urgent tiers means no urgent mail", len(db.urgent_by_age(conn2, [], 10)), 0)


def test_roundup_is_owed_by_the_week_not_by_the_day():
    """The trap: a laptop closed all Sunday must not lose the week's roundup.

    launchd runs a missed job once on wake, so the Sunday digest job may really
    run on Monday. A plain "is today Sunday" test would drop that week.
    """
    conn = make_db()
    monday = datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc)   # a Monday
    saturday = datetime(2026, 8, 22, 9, 0, tzinfo=timezone.utc)

    check("owed when none has ever been sent", delivery.roundup_owed(conn, when=monday), True)

    # Sent on the Sunday just gone.
    db.set_state(conn, delivery.LAST_ROUNDUP_KEY,
                 datetime(2026, 8, 16, 18, 0, tzinfo=timezone.utc).isoformat())
    check(
        "not owed again on the Monday after",
        delivery.roundup_owed(conn, when=monday),
        False,
    )
    check(
        "still not owed on the Saturday",
        delivery.roundup_owed(conn, when=saturday),
        False,
    )
    check(
        "owed again the following Sunday",
        delivery.roundup_owed(
            conn, when=datetime(2026, 8, 23, 18, 0, tzinfo=timezone.utc)
        ),
        True,
    )
    # A run that slips to Monday still owes the Sunday roundup.
    db.set_state(conn, delivery.LAST_ROUNDUP_KEY,
                 datetime(2026, 8, 16, 18, 0, tzinfo=timezone.utc).isoformat())
    check(
        "a slipped run still owes it",
        delivery.roundup_owed(
            conn, when=datetime(2026, 8, 24, 9, 0, tzinfo=timezone.utc)
        ),
        True,
    )


def test_roundup_window_leaves_the_backlog_alone():
    conn = make_db()
    add(conn, n=0, hash="recent", identity="a", tier=3, fit_score=5, first_seen=ts(2))
    add(conn, n=1, hash="ancient", identity="b", tier=3, fit_score=5, first_seen=ts(60))
    add(conn, n=2, hash="alerted", identity="c", tier=3, fit_score=5, first_seen=ts(2),
        alerted_at=ts(1))
    found = db.roundup_candidates(conn, [3], 8)
    check(
        "the roundup window excludes the seeded backlog",
        [r["hash"] for r in found],
        ["recent"],
    )


def test_emails_build_and_suppress():
    conn = make_db()
    add(conn, n=0, hash="a", identity="a", tier=1, fit_score=9, reach_score=3,
        reason="agent work at a frontier lab", flags="reach")
    split = delivery.split_daily(rows(conn))
    subject, body = notify.build_digest(split, [], {"sources_ok": 1})
    check("the digest names its tier", "TIER 1" in body, True)
    check("the REACH caveat is printed", "REACH" in body, True)
    check("the subject counts tier 1", "tier 1" in subject, True)
    check("the score is shown", "fit 9" in body, True)

    # An override is shown next to the model's number rather than replacing it.
    conn2 = make_db()
    add(conn2, n=0, hash="a", identity="a", tier=1, fit_score=6, fit_override=9)
    split = delivery.split_daily(rows(conn2))
    _, body = notify.build_digest(split, [], {})
    check("an override shows both numbers", "yours, model said 6" in body, True)

    # Empty suppression, PRD success criterion 5.
    conn3 = make_db()
    split = delivery.split_daily(rows(conn3))
    check("nothing to say means nothing to show", len(split["shown"]), 0)


def test_referrals_reach_the_email():
    """PRD section 7. contacts.for_company was built 2026-08-09 and called by
    nothing until Milestone 7."""
    conn = make_db()
    conn.execute("INSERT INTO companies (name) VALUES ('Example Capital Partners')")
    conn.execute(
        "INSERT INTO contacts (name, company_id, relationship) "
        "VALUES ('Jordan Example', 1, 'family friend')"
    )
    conn.commit()
    add(conn, n=0, hash="a", identity="a", tier=1, fit_score=9,
        company="Example Capital Partners")
    split = delivery.split_daily(rows(conn))
    referrals = delivery.referrals_for(conn, split["shown"])
    _, body = notify.build_digest(split, [], {}, referrals=referrals)
    check("a referral appears beside the role", "REFERRAL: Jordan Example" in body, True)



def test_send_never_raises():
    """`notify.send` answers with False and never with an exception.

    Added 2026-09-07. Every caller is written against the one sentence in that
    function's docstring, and until this date the sentence was only half true:
    an unconfigured mailbox returned False, and an unreachable one raised, all
    the way out of `agent/run.py:main`. Six days in the fortnight to 2026-09-07
    were recorded as a crashed watcher when the watcher had polled 176 boards
    perfectly well and only the mail was down.

    The mail failure is still reported, by the run report and CLAUDE.md rule 12,
    just not as a failure of the wrong component. Nothing is stamped either way,
    which is rule 10 and is why no coverage was lost by any of it.
    """
    import io
    import smtplib as real
    from contextlib import redirect_stderr

    from agent import config as cfg

    saved = (cfg.SMTP_USER, cfg.SMTP_PASSWORD, cfg.EMAIL_FROM, cfg.SMTP_PORT)
    saved_smtplib = notify.smtplib
    cfg.SMTP_USER, cfg.SMTP_PASSWORD, cfg.EMAIL_FROM = "user", "password", "a@b.test"
    cfg.SMTP_PORT = 465

    def offline(*_args, **_kwargs):
        # Exactly what a Mac with no network raises, errno and all.
        raise OSError(8, "nodename nor servname provided, or not known")

    def refused(*_args, **_kwargs):
        raise real.SMTPAuthenticationError(535, b"application-specific password required")

    def broken(*_args, **_kwargs):
        raise ValueError("this is a bug in the message, not a delivery problem")

    class Fake:
        SMTPException = real.SMTPException

    try:
        for raiser, name in [
            (offline, "an unreachable mail server returns False rather than raising"),
            (refused, "a rejected password returns False rather than raising"),
        ]:
            Fake.SMTP_SSL = staticmethod(raiser)
            notify.smtplib = Fake
            try:
                with redirect_stderr(io.StringIO()):
                    got = notify.send("subject", "body")
            except Exception as exc:  # noqa: BLE001
                # Caught so this reports as a failed check rather than taking
                # the whole suite down with a traceback. A regression here is
                # the exact bug being guarded, so it has to read clearly.
                got = f"raised {exc!r}"
            check(name, got, False)

        # The other half of the rule. Swallowing everything would hide a bug in
        # the email this module just built, and a digest that silently never
        # sends is the failure this whole area exists to prevent.
        Fake.SMTP_SSL = staticmethod(broken)
        notify.smtplib = Fake
        raised = False
        try:
            with redirect_stderr(io.StringIO()):
                notify.send("subject", "body")
        except ValueError:
            raised = True
        check("a bug in the message still raises, and is not read as a mail outage",
              raised, True)
    finally:
        notify.smtplib = saved_smtplib
        cfg.SMTP_USER, cfg.SMTP_PASSWORD, cfg.EMAIL_FROM, cfg.SMTP_PORT = saved


def main() -> int:
    for fn in [
        test_tier_routing,
        test_split_holds_back_the_right_things,
        test_cap_does_not_consume_what_it_hides,
        test_collapse_groups_one_role_across_cities,
        test_airtable_lead_is_stable_and_the_email_lead_is_not,
        test_urgent_triggers,
        test_roundup_is_owed_by_the_week_not_by_the_day,
        test_roundup_window_leaves_the_backlog_alone,
        test_emails_build_and_suppress,
        test_referrals_reach_the_email,
        test_send_never_raises,
    ]:
        fn()

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
