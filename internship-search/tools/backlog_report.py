"""Milestone 2.5. One-time catch-up report of everything already open.

    .venv/bin/python -m tools.backlog_report                  # print it
    .venv/bin/python -m tools.backlog_report --email          # send it
    .venv/bin/python -m tools.backlog_report --mark-alerted   # record that it was surfaced

Why this exists. The seeding run recorded every posting that was already open, and
agent/run.py deliberately suppresses the digest on a seeding run, because reporting
twelve thousand postings as "new" is noise. The side effect is that everything open
at that moment was banked silently and can never appear in a future digest, since it
is no longer new. This report empties that backlog once, so the baseline is known
instead of only the delta.

Every number here is counted from state.db at runtime. Nothing is hardcoded; the
figure drifts every time the watcher runs.

--mark-alerted stamps alerted_at on the rows it listed, which is what success
criterion 1 in PRD.md audits against. Without it the report is read-only.
"""

import argparse

from agent import config, db, notify


def open_digest_worthy(conn) -> list[dict]:
    """Every open posting the Stage A prefilter surfaced, grouped by company.

    Before Milestone 4 this matched titles against a keyword list. It now reads
    the prefilter's verdict, so the report and the digest agree by construction
    instead of by two lists happening to say the same thing. Postings still
    pending intake tagging are excluded: their timing rules have not run, so
    listing them would report coverage the agent has not actually decided on.
    """
    return db.surfaced(conn)


def build_report(rows: list[dict], open_total: int) -> tuple[str, str]:
    by_company: dict[str, list[dict]] = {}
    for r in rows:
        by_company.setdefault(r["company"], []).append(r)

    lines = [
        "BACKLOG: postings already open that were never surfaced",
        "",
        f"{len(rows)} open postings clear the Stage A prefilter, out of "
        f"{open_total} open postings tracked, across {len(by_company)} companies.",
        "",
        "These were recorded during the seeding run and never emailed, because a "
        "seeding run suppresses the digest by design. They are open now. Nothing "
        "below is new; it is what the watcher already had and could not show.",
        "",
    ]

    lines.append("BY COMPANY")
    for company, items in sorted(by_company.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        lines.append(f"  {len(items):4d}  {company}")
    lines.append("")

    for company in sorted(by_company):
        lines.append(company)
        for r in by_company[company]:
            lines.append(
                f"  - {r['title']} ({r['location'] or 'location not stated'})"
                f"{notify.format_flags(r.get('flags'))}"
            )
            lines.append(f"    {r['url'] or ''}")
        lines.append("")

    lines.append("---")
    lines.append(
        "These cleared the hard exclusions and timing rules in "
        "sources/prefilter.toml. They are not ranked; that is Milestone 6. A "
        "flag in brackets means the prefilter could not resolve something and "
        "surfaced the posting rather than risk dropping it."
    )
    lines.append("")
    lines.append("Reminder: check UChicago Handshake manually. It is behind login and out of scope.")

    return f"Internship backlog: {len(rows)} open postings never surfaced", "\n".join(lines)


def mark_alerted(conn, rows: list[dict]) -> int:
    # The stamp itself lives in agent/db.py, because the scheduled digest run
    # writes the same column and the two must not drift apart.
    return db.mark_alerted(conn, rows, "backlog")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--email", action="store_true", help="send it instead of printing")
    parser.add_argument(
        "--mark-alerted",
        action="store_true",
        help="stamp alerted_at on every listed posting, for the coverage audit",
    )
    args = parser.parse_args()

    conn = db.connect()
    rows = open_digest_worthy(conn)
    subject, body = build_report(rows, db.open_count(conn))

    if args.email:
        if notify.send(subject, body):
            print(f"Emailed {config.EMAIL_TO}: {subject}")
    else:
        print(f"Subject: {subject}\n")
        print(body)

    if args.mark_alerted:
        n = mark_alerted(conn, rows)
        print(f"\nMarked {n} posting(s) as alerted. {len(rows) - n} were already marked.")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
