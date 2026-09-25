#!/usr/bin/env python3
"""Send a digest to named addresses, for testing delivery.

The PRD lists "test digest sent" as a move-in task, and the risk table puts
a digest silently landing in spam second only to logging going stale. Both
need a real send before the first Monday that matters, so this exists to
produce one on demand.

It renders exactly what the Monday run would render, through the same
schedule and digest code, and differs from the real thing in one way only:
recipients come from --to rather than from the roster. That is deliberate.
A test send must never be able to reach the household by accident, so --to
is required and there is no roster fallback.

Usage:
    python3 scripts/send_test_digest.py --to you@example.com --dry-run
    python3 scripts/send_test_digest.py --to you@example.com
    python3 scripts/send_test_digest.py --to a@x.com --to b@y.com --week 3

Environment: AIRTABLE_API_KEY, AIRTABLE_BASE_ID, and for a real send
GMAIL_ADDRESS and GMAIL_APP_PASSWORD.
"""

import argparse
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

# This script lives in scripts/, so the repo root is not on the path when it
# runs directly. Put it there before importing anything from config or src.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.digest_copy import DEFAULT_DIGEST_COPY
from config.digest_style import DEFAULT_DIGEST_STYLE
from config.due_policy import DEFAULT_DUE_POLICY as POLICY
from config.rules import RULE_CATEGORIES
from config.sender import SENDER_NAME
from src import digest, orchestrator
from src.email_sender import send_email


def main(argv=None):
    args = _parse_args(argv)

    # The orchestrator already knows how to read the base and build the term.
    # Reaching into it keeps one loading path rather than two that can drift.
    context = orchestrator._load(orchestrator._client())
    week = _pick_week(context, args.week)

    scheduled = [i for i in context.scheduled if i.week.number == week.number]
    # Both bodies, exactly as the Monday run builds them. A test send that
    # is not byte-for-byte what people will actually receive tests nothing.
    render_args = (
        week, scheduled, context.roster, context.rules,
        DEFAULT_DIGEST_COPY, RULE_CATEGORIES,
    )
    message = digest.render(*render_args)
    body_html = digest.render_html(*render_args, DEFAULT_DIGEST_STYLE)

    print("Week %d (%s to %s), %d assignments."
          % (week.number, week.start_date, week.end_date, len(scheduled)))
    print("To: %s" % ", ".join(args.to))
    print("From: %s" % SENDER_NAME)
    print("Subject: %s" % message.subject)
    print("Parts: plain text + HTML (%d bytes)" % len(body_html))
    print()
    print(message.body)

    if args.dry_run:
        print("--- DRY RUN: nothing sent ---")
        return 0

    send_email(message.subject, message.body, args.to, SENDER_NAME, body_html)
    print("--- Sent. ---")
    print("Check the inbox, not just Promotions or Spam. If it landed "
          "anywhere but the inbox, mark it not spam and add the sender to "
          "contacts before the first real Monday.")
    return 0


def _pick_week(context, requested):
    """Return the week to render: the one asked for, today's, or the first."""
    active = [w for w in context.weeks if w.active]
    if requested is not None:
        for week in context.weeks:
            if week.number == requested:
                if not week.active:
                    raise SystemExit(
                        "week %d is inactive and has no rotation to show"
                        % requested
                    )
                return week
        raise SystemExit(
            "week %d is not in the term, which runs weeks %d to %d"
            % (requested, context.weeks[0].number, context.weeks[-1].number)
        )

    today = datetime.now(ZoneInfo(POLICY.timezone)).date()
    current = orchestrator._week_containing(today, context.weeks)
    if current is not None and current.active:
        return current
    # Outside the term, or in an inactive week: show the first real one.
    return active[0]


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="python3 scripts/send_test_digest.py",
        description="Send a digest to named addresses, to test delivery.",
    )
    parser.add_argument(
        "--to", action="append", required=True, metavar="ADDRESS",
        help="recipient; repeat for several. Required, and never defaults "
             "to the roster",
    )
    parser.add_argument(
        "--week", type=int, default=None, metavar="N",
        help="which week to render; defaults to the current one, or the "
             "first active week when today is outside the term",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="print the digest and send nothing",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
