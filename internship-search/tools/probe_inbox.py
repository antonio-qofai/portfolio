"""Prove a mailbox can be read, and read one issue of a newsletter.

SHELVED 2026-08-16, the day it was written, along with agent/inbox.py and Milestones
6.5 and 7.5. It has never been run against a real mailbox, because UChicago policy
leaves none it is allowed into. See that module's docstring and CHANGELOG.md.

It stays because it is what would settle the question in one command if the owner ever
has a mailbox that permits an app password. Set IMAP_USER and IMAP_PASSWORD in .env
and run it; nothing else needs doing first.

Two questions it was built to answer. Whether the agent can connect at all. And what
a newsletter actually looks like, since whether it links to postings directly and
whether an issue carries a handful of roles or several dozen both change what an
extraction prompt has to do, and inventing either would waste a build.

It writes nothing, spends nothing, and cannot mark a message read: every mailbox
is opened with EXAMINE and every fetch peeks. Run it as often as you like.

    .venv/bin/python -m tools.probe_inbox              # connect, list what matched
    .venv/bin/python -m tools.probe_inbox --recent 20  # headers of recent mail
    .venv/bin/python -m tools.probe_inbox --show 1     # read the newest match
    .venv/bin/python -m tools.probe_inbox --show 1 --links
"""

import argparse
import sys

from agent import config, inbox


def _print_summaries(rows: list[inbox.Summary], indent: str = "  ") -> None:
    for i, row in enumerate(rows, start=1):
        kb = f"{row.size / 1024:.0f}K" if row.size else "?"
        print(f"{indent}[{i}] {row.date}  {kb}")
        print(f"{indent}    from    {row.sender}")
        print(f"{indent}    subject {row.subject}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--recent",
        type=int,
        metavar="N",
        help="list headers of the N most recent messages in the window, whoever "
        "sent them. Headers only, never a body. Use this when a sender match "
        "returns nothing and you need to see what the mail actually looks like.",
    )
    parser.add_argument(
        "--show",
        type=int,
        metavar="N",
        help="print the text of the Nth newsletter match, newest first",
    )
    parser.add_argument(
        "--links",
        action="store_true",
        help="with --show, list every link in the message instead of its text",
    )
    parser.add_argument(
        "--chars",
        type=int,
        default=6000,
        help="how much of a shown message to print (default 6000)",
    )
    parser.add_argument(
        "--days",
        type=int,
        help="override the lookback window from sources/inbox.toml",
    )
    args = parser.parse_args(argv)

    try:
        cfg = inbox.load()
    except (OSError, inbox.InboxError) as exc:
        print(f"could not read the inbox config: {exc}", file=sys.stderr)
        return 1

    box = cfg.mailbox
    days = args.days or box.lookback_days

    if not config.inbox_configured():
        print("IMAP_USER and IMAP_PASSWORD are not set in .env.")
        print()
        print("Nothing here works without them. They are the credentials for the")
        print("mailbox the newsletter is forwarded to, not for the uchicago account,")
        print("which has app passwords disabled. See NEXT_STEPS.md.")
        return 1

    print(f"connecting to {box.host}:{box.port} as {config.IMAP_USER}")
    print(f"folder {box.folder}, read-only, looking back {days} days")
    print()

    enabled = [n for n in cfg.newsletters if n.enabled]
    if not enabled:
        print("every newsletter in sources/inbox.toml is disabled, so nothing to match")

    try:
        with inbox.connect(box) as conn:
            print("connected and authenticated. IMAP is open on this account.")
            print()

            if args.recent:
                uids = inbox.search(conn, days_back=days)
                rows = inbox.summarize(conn, uids[-args.recent :])
                rows.reverse()
                print(f"{len(uids)} messages in the last {days} days. Newest {len(rows)}:")
                _print_summaries(rows)
                print()

            matches: dict[str, list[inbox.Summary]] = {}
            for letter in enabled:
                uids = inbox.search(conn, sender=letter.sender, days_back=days)
                rows = inbox.summarize(conn, uids)
                rows.reverse()
                matches[letter.key] = rows

                print(f"{letter.name}  <{letter.sender}>")
                if not rows:
                    print("  nothing matched in the window.")
                    print("  That is either no issue having arrived yet, forwarding")
                    print("  not being set up, or forwarding rewriting the From line.")
                    print("  --recent tells the three apart.")
                else:
                    print(f"  {len(rows)} matched:")
                    _print_summaries(rows)
                print()

            if args.show:
                rows = next((r for r in matches.values() if r), [])
                if not rows:
                    print("nothing to show: no newsletter matched.", file=sys.stderr)
                    return 1
                if args.show > len(rows):
                    print(
                        f"asked for match {args.show} but only {len(rows)} matched.",
                        file=sys.stderr,
                    )
                    return 1

                chosen = rows[args.show - 1]
                msg = inbox.fetch(conn, chosen.uid)

                print("-" * 70)
                print(f"subject {chosen.subject}")
                print(f"date    {chosen.date}")
                print(f"id      {chosen.message_id}")
                print("-" * 70)

                if args.links:
                    found = inbox.links(msg)
                    print(f"{len(found)} links:")
                    for url in found:
                        print(f"  {url}")
                else:
                    text = inbox.body_text(msg)
                    print(text[: args.chars])
                    if len(text) > args.chars:
                        print()
                        print(
                            f"... {len(text) - args.chars} more characters, "
                            f"raise --chars to see them"
                        )
    except inbox.InboxError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
