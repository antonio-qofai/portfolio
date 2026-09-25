"""One polling cycle. Run with: python -m agent.run

Poll every company board in the token map and every aggregator feed, store what comes
back, work out what disappeared, and email what is new.

Company boards and feeds are two halves of the same layer and are treated identically
once their postings exist. Both produce Postings, both get a source key, both age
their own postings for closure, and neither is allowed to count as evidence about the
other's postings.
"""

import argparse
import sys

from . import (
    config, contacts, db, delivery, feeds, fetchers, health, notify, ranker,
    triage,
)
from .sources import load_companies, load_feed_settings, load_feeds


def deliver(subject: str, body: str, which: str, failures: list[str]) -> bool:
    """Send one email and remember whether it went, for the run report.

    `notify.send` returns False for two different situations and only one of
    them is a fault. An unconfigured mailbox is a state the owner chose, or a
    manual run on a machine with no credentials, and reporting it every time
    would mean an alert on every run forever, which is the false-alarm direction
    CLAUDE.md rule 12 names as equally bad as a missed one. A configured mailbox
    that could not be reached is a real fault and is worth one.

    The distinction is drawn here rather than inside `send`, so that function
    keeps the one-sentence contract every other caller relies on.
    """
    if notify.send(subject, body):
        return True
    if config.email_configured():
        failures.append(which)
    return False


def digest_worthy(conn, postings: list[dict]) -> tuple[list[dict], int]:
    """Narrow the email to what the Stage A prefilter surfaced.

    Storage is untouched either way: killed postings stay in SQLite so the
    coverage audit in PRD success criterion 1 can still see them. A posting
    still pending intake tagging is held back rather than emailed, because its
    timing rules have not been applied yet; it reaches the digest on the run
    that tags it.
    """
    if not postings:
        return [], 0
    marks = ",".join("?" for _ in postings)
    # Read the stored rows back rather than filtering the fetched Postings: the
    # rows carry the flags and the term that Stage A and Stage 0 just wrote, and
    # the digest shows those next to the role.
    kept = [
        dict(r)
        for r in conn.execute(
            f"SELECT * FROM postings WHERE identity IN ({marks}) "
            "AND prefilter_verdict = 'surface' ORDER BY company, title",
            [p["identity"] for p in postings],
        )
    ]
    return kept, len(postings) - len(kept)


class Cycle:
    """Accumulates one run's results. Company boards and feeds both report into it."""

    def __init__(self, conn, verbose=True):
        self.conn = conn
        self.verbose = verbose
        # Which sources already existed when this run began. Anything not in here is
        # being polled for the first time, so everything it returns is new by
        # definition and belongs in the backlog report, not the digest.
        self.preexisting = db.known_sources(conn)

        self.new: list[dict] = []
        self.seeded: list[dict] = []
        self.closed: list[dict] = []
        self.failures: list[tuple[str, str]] = []
        self.empty: list[str] = []
        self.seeded_sources: list[str] = []
        self.seen_total = 0
        self.ok = 0
        # Postings a company edited since the last run, which now stay on their
        # own row instead of churning. Reported so the fix stays visible.
        self.edits: dict = {}

    def fail(self, name: str, exc) -> None:
        self.failures.append((name, str(exc)))
        if self.verbose:
            print(f"  FAIL  {name}: {exc}", file=sys.stderr)

    def record(self, name: str, source_key: str, postings) -> list[dict]:
        self.ok += 1
        self.seen_total += len(postings)

        # A source that answers 200 with an empty list is the dangerous case: it
        # reports ok forever while covering nothing. For a board that usually means
        # the company moved off the ATS its token assumes; for a feed it usually means
        # the repo renamed its branch. Name it either way so it cannot hide.
        if not postings:
            self.empty.append(name)
            if self.verbose:
                print(f"  EMPTY {name}: 0 postings, source may be stale")

        # A poll can hand back two postings under one external_id. Settle that
        # before storage, so upsert_seen and age_missing below agree on what
        # every posting in this batch is called.
        fetchers.resolve_identities(postings)

        new = db.upsert_seen(self.conn, postings, self.edits)

        first_time = source_key not in self.preexisting
        if first_time and new:
            self.seeded.extend(new)
            self.seeded_sources.append(name)
        else:
            self.new.extend(new)

        # Only a source that answered can tell us something disappeared.
        self.closed.extend(
            db.age_missing(self.conn, source_key, {p.identity for p in postings})
        )
        return new

    def stats(self) -> dict:
        return {
            # No source had ever stored anything, so this is the very first run.
            "seeding": not self.preexisting,
            "sources_ok": self.ok,
            "sources_failed": len(self.failures),
            "postings_seen": self.seen_total,
            "open_total": db.open_count(self.conn),
            "failures": self.failures,
            "empty_sources": self.empty,
            "seeded_sources": self.seeded_sources,
            "seeded_count": len(self.seeded),
            "retitled": self.edits.get("retitled", 0),
        }


def poll_companies(cycle: Cycle, companies) -> None:
    with fetchers.make_client() as client:
        for company in companies:
            try:
                postings = fetchers.fetch_company(client, company)
            except fetchers.SourceError as exc:
                cycle.fail(company.name, exc)
                continue

            new = cycle.record(company.name, company.source_key, postings)
            if cycle.verbose:
                print(f"  ok    {company.name}: {len(postings)} postings, {len(new)} new")


def poll_feeds(cycle: Cycle, feed_list, companies) -> None:
    settings = load_feed_settings()
    skip = feeds.skip_index(companies, settings)
    require_visible = settings.get("require_visible", True)

    with feeds.make_client() as client:
        for feed in feed_list:
            try:
                postings, fstats = feeds.fetch_feed(client, feed, skip, require_visible)
            except fetchers.SourceError as exc:
                cycle.fail(feed.name, exc)
                continue

            new = cycle.record(feed.name, feed.source_key, postings)
            if cycle.verbose:
                print(
                    f"  ok    {feed.name}: {fstats['listings']} listings, "
                    f"{len(postings)} open and not already watched, {len(new)} new "
                    f"({fstats['closed_upstream']} closed upstream, "
                    f"{fstats['skipped_own_board']} deferred to a company board)"
                )


def main() -> int:
    parser = argparse.ArgumentParser(description="Poll every source once.")
    parser.add_argument(
        "--no-email",
        action="store_true",
        help="send nothing at all, print everything. The flag for a manual run",
    )
    parser.add_argument(
        "--no-digest",
        action="store_true",
        help=(
            "print the daily digest instead of sending it, but still send urgent "
            "alerts. What the three quiet scheduled runs use: PRD section 3 says "
            "urgent fires immediately, and immediately cannot mean once a day"
        ),
    )
    parser.add_argument("--quiet", action="store_true", help="suppress per-source output")
    parser.add_argument("--skip-feeds", action="store_true", help="company boards only")
    parser.add_argument(
        "--max-tag-calls",
        type=int,
        default=None,
        help=f"cap Stage 0 model calls this run (default {config.STAGE0_MAX_CALLS})",
    )
    parser.add_argument(
        "--skip-triage",
        action="store_true",
        help="poll and store only, run no prefilter and no model calls",
    )
    parser.add_argument(
        "--max-rank-calls",
        type=int,
        default=None,
        help=f"cap ranking model calls this run (default {config.RANK_MAX_CALLS})",
    )
    parser.add_argument(
        "--skip-ranking",
        action="store_true",
        help="run the prefilter but score nothing, so the run spends nothing on ranking",
    )
    parser.add_argument(
        "--carryover-hours",
        type=float,
        default=0.0,
        help=(
            "also report surfaced postings first seen this many hours ago that no "
            "email has carried yet. Off by default. Set by the scheduled digest "
            "run, so postings found by the quiet runs are not filed and forgotten"
        ),
    )
    args = parser.parse_args()

    # Which emails this run tried to send and could not. Collected rather than
    # raised, so a mail outage costs the emails it actually stopped and nothing
    # downstream of them. See `deliver` and `notify.send`.
    mail_failures: list[str] = []

    companies = load_companies()
    feed_list = [] if args.skip_feeds else load_feeds()
    conn = db.connect()
    db.sync_companies(conn, companies)
    # Referral contacts, PRD section 7. Cheap, idempotent, and reading it here
    # means editing sources/contacts.toml is the only step to add someone.
    contacts.sync_to_db(conn)

    if not args.quiet:
        print(
            f"Polling {len(companies)} companies from {config.SOURCES_PATH.name} "
            f"and {len(feed_list)} feeds from {config.FEEDS_PATH.name}"
        )

    cycle = Cycle(conn, verbose=not args.quiet)
    poll_companies(cycle, companies)
    poll_feeds(cycle, feed_list, companies)

    new, closed, stats = cycle.new, cycle.closed, cycle.stats()

    # Stage A and Stage 0. Runs on everything open, not just this run's new
    # postings, so the backlog drains a batch at a time across runs.
    if args.skip_triage:
        triage_stats = None
    else:
        if not args.quiet:
            print("\nTriaging open postings (Stage A prefilter, Stage 0 tagging)")
        triage_stats = triage.run(conn, args.max_tag_calls, verbose=not args.quiet)
        if not args.quiet:
            for line in triage.summary_lines(triage_stats):
                print(f"  {line}")

    # Stage B and Stage C. Runs after the prefilter, on everything it surfaced
    # that has no score, so the ranking backlog drains across runs the same way
    # the tagging backlog does.
    if args.skip_triage or args.skip_ranking:
        rank_stats = None
    else:
        if not args.quiet:
            print("\nRanking surfaced postings (Stage B, Stage C)")
        rank_stats = ranker.run(conn, args.max_rank_calls, verbose=not args.quiet)
        if not args.quiet:
            for line in ranker.summary_lines(rank_stats):
                print(f"  {line}")

    tag = (triage_stats or {}).get("tag", {})
    rank = rank_stats or {}
    db.record_run(
        conn,
        postings_seen=stats["postings_seen"],
        new_postings=len(new) + stats["seeded_count"],
        closed_detected=len(closed),
        sources_ok=stats["sources_ok"],
        sources_failed=stats["sources_failed"],
        model_calls=tag.get("from_model", 0) + rank.get("calls", 0),
        estimated_cost=tag.get("cost", 0.0) + rank.get("cost", 0.0),
        score_variance=rank.get("variance"),
    )

    # Edited postings used to leave the run as one closure plus one discovery.
    # They now stay on their own row, so the only trace of them is this line.
    if stats["retitled"] and not args.quiet:
        print(
            f"\n{stats['retitled']} posting(s) were retitled by the company and "
            "kept their row. Stage A will judge them again on the new title."
        )

    # On a seeding run every posting is "new", which is noise, not signal.
    reportable_new = [] if stats["seeding"] else new
    reportable_new, hidden = digest_worthy(conn, reportable_new)
    stats["hidden_by_prefilter"] = hidden
    stats["triage"] = triage_stats

    # Postings the quiet scheduled runs found. They are no longer new, so this
    # run would not otherwise mention them, and nothing else ever would either.
    # See db.carryover; the window is what stops this becoming a backlog dump.
    stats["carried_over"] = 0
    if args.carryover_hours and not stats["seeding"]:
        seen = {p["hash"] for p in reportable_new}
        carried = [
            p for p in db.carryover(conn, args.carryover_hours) if p["hash"] not in seen
        ]
        stats["carried_over"] = len(carried)
        reportable_new = sorted(
            reportable_new + carried, key=lambda p: (p["company"], p["title"])
        )

    # Closures of postings he marked interested, however old. Not part of
    # `closed`, which only holds what this run detected: three of the four
    # scheduled runs send no email, so a closure they found was never carried.
    # See db.closed_interested.
    owed_closures = [] if stats["seeding"] else db.closed_interested(conn)

    # PRD section 4's automated self-check. The ranker has measured this since
    # Milestone 6 and nothing ever showed it to him; the digest is where the
    # section says it belongs.
    if rank_stats and rank_stats.get("unstable"):
        stats["unstable"] = (
            f"SCORE INSTABILITY: re-scoring a sample moved by "
            f"{rank_stats['variance']:.2f} on average, past the tolerance in "
            "rubric.md. Treat this run's scores as provisional."
        )

    # Milestone 7. Tier decides which email a posting belongs in, and the tier
    # bands in rubric.md decide which tier goes where. Everything not shown here
    # is left unstamped, so nothing is consumed by an email that did not carry it.
    split = delivery.split_daily(reportable_new)
    digest_rows = [r for g in split["shown"] for r in g.rows]
    referrals = delivery.referrals_for(conn, split["shown"])

    # The owner's own open loops, printed above the findings. Nothing here is
    # stamped and nothing here is consumed, so it is computed after the split
    # and takes no part in it.
    actions = {} if stats["seeding"] else delivery.action_content(conn)

    subject, body = notify.build_digest(
        split, closed, stats, owed_closures, referrals, actions=actions
    )

    # Empty digests never send. A seeding run always reports, since it is a setup step.
    #
    # The action block does not count as content by default. It repeats until he
    # moves it, so counting it would mean a digest every single day for as long
    # as one unapplied role sits in the base, and a daily email that is identical
    # to yesterday's is one he stops opening. It rides along when the digest has
    # a reason to send. send_on_actions_alone in sources/email.toml reverses that.
    action_cfg = delivery.rules().get("actions", {})
    has_content = bool(
        split["shown"] or closed or owed_closures
        or stats["seeding"] or stats["seeded_sources"]
        or (action_cfg.get("send_on_actions_alone", False)
            and delivery.has_actions(actions))
    )
    send_digest = not (args.no_email or args.no_digest)

    if not send_digest:
        print(f"\nSubject: {subject}\n")
        print(body)
    elif has_content:
        sent = deliver(subject, body, "digest", mail_failures)
        if sent:
            # Only what an email actually carried is stamped. A send that failed
            # or was never configured leaves the postings unalerted, so the next
            # run offers them again rather than losing them silently. The cap is
            # the same rule: overflow is not in digest_rows and is not stamped.
            db.mark_alerted(conn, digest_rows, "digest")
            db.mark_closure_alerted(conn, owed_closures)
            if not args.quiet:
                print(f"\nEmailed {config.EMAIL_TO}: {subject}")
    elif not args.quiet:
        print("\nNothing new and nothing closed. No email sent.")

    # The Sunday roundup. Rides on whichever run sends the digest, and is owed
    # when none has gone out since the most recent roundup weekday rather than
    # when today happens to be that day. See delivery.roundup_owed. --no-email
    # previews it and writes nothing; --no-digest is a quiet scheduled run and
    # skips it entirely, because a weekly summary has no reason to be immediate.
    if (send_digest or args.no_email) and not args.no_digest and not stats["seeding"] \
            and delivery.roundup_owed(conn):
        content = delivery.roundup_content(conn)
        if content["shown"] or content["closed"]:
            rsubject, rbody = notify.build_roundup(
                content, delivery.referrals_for(conn, content["shown"])
            )
            if args.no_email:
                print(f"\nSubject: {rsubject}\n")
                print(rbody)
            elif deliver(rsubject, rbody, "roundup", mail_failures):
                db.mark_alerted(
                    conn, [r for g in content["shown"] for r in g.rows], "roundup"
                )
                delivery.mark_roundup_sent(conn)
                if not args.quiet:
                    print(f"\nEmailed {config.EMAIL_TO}: {rsubject}")
        elif send_digest:
            # Nothing to say, but the week is still accounted for. Without this
            # the roundup stays owed and re-checks on every run for a week.
            delivery.mark_roundup_sent(conn)
            if not args.quiet:
                print("\nRoundup was due and had nothing to carry. Not sent.")

    # Urgent. Deliberately not gated on the digest flag, because PRD section 3
    # says these fire immediately and the three quiet runs are three quarters of
    # the chances to be immediate. Only --no-email silences it.
    if not stats["seeding"]:
        urgent = delivery.urgent_content(conn)
        if delivery.has_urgent(urgent):
            usubject, ubody = notify.build_urgent(
                urgent,
                delivery.referrals_for(
                    conn, urgent["deadline"] + urgent["stale"]
                ),
            )
            if args.no_email:
                print(f"\nSubject: {usubject}\n")
                print(ubody)
            elif deliver(usubject, ubody, "urgent", mail_failures):
                db.mark_urgent(conn, urgent["deadline_rows"], delivery.DEADLINE_STAMP)
                db.mark_urgent(conn, urgent["stale_rows"], delivery.STALE_STAMP)
                if not args.quiet:
                    print(f"\nEmailed {config.EMAIL_TO}: {usubject}")

    if stats["sources_failed"]:
        print(
            f"\n{stats['sources_failed']} source(s) failed. "
            "Fix or remove them in sources/companies.toml or sources/feeds.toml.",
            file=sys.stderr,
        )

    if stats["empty_sources"]:
        print(
            f"\n{len(stats['empty_sources'])} source(s) returned 0 postings: "
            f"{', '.join(stats['empty_sources'])}. "
            "Hunt the right token with tools.probe_tokens.",
            file=sys.stderr,
        )

    if mail_failures:
        print(
            f"\n{len(mail_failures)} email(s) could not be sent: "
            f"{', '.join(mail_failures)}. Nothing they carried was stamped, so "
            "the next run offers it again.",
            file=sys.stderr,
        )

    # Last, and printed whatever else happened, because the wrapper reads it to
    # decide whether this run is worth calling a success.
    print(
        health.run_report_line(
            stats["sources_ok"],
            stats["sources_ok"] + stats["sources_failed"],
            bool(mail_failures),
        )
    )

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
