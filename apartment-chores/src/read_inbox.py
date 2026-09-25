"""Entry point for the landlord email reader (PRD-v1.1).

    python3 -m src.read_inbox [--dry-run]

Reads the landlord's recent mail, asks the model what it contains, and
proposes unconfirmed rows in Cleaner Visits and Requests for a person to
confirm or delete. Then sends that person one summary email.

It is a separate entry point with its own workflow, and imports nothing from
the orchestrator, so nothing here can stop the Monday digest.

Exit codes. A model error, no landlord mail, or nothing worth proposing all
exit 0 and change nothing (PRD-v1.1 §5). A setup problem (bad allowlist,
missing credentials, a mailbox or Airtable that refuses the connection)
raises, so the workflow fails visibly instead of looking like a quiet month.

Order matters for the Airtable budget: the mailbox is read first, and
Airtable is only touched if the landlord has actually written.
"""

import argparse
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from config import landlord
from config.airtable_fields import DEFAULT_FIELD_CONFIG
from config.due_policy import DEFAULT_DUE_POLICY
from config.inbox_copy import DEFAULT_INBOX_COPY
from config.proposal_fields import DEFAULT_PROPOSAL_FIELDS
from config.sender import SENDER_NAME
from src import extract, inbox, proposals
from src.email_sender import credentials, send_email

API_KEY_ENV = "AIRTABLE_API_KEY"
BASE_ID_ENV = "AIRTABLE_BASE_ID"
AIRTABLE_LINK = "https://airtable.com/{base_id}"

VISIT_TABLE = DEFAULT_FIELD_CONFIG.table_cleaner_visits
VISIT_DATE_FIELD = DEFAULT_FIELD_CONFIG.cleaner_visits.visit_date


def main(argv=None):
    args = _parse_args(argv)
    zone = ZoneInfo(DEFAULT_DUE_POLICY.timezone)
    now = datetime.now(zone)
    _say("Inbox run at %s (%s)." % (now.isoformat(timespec="seconds"), zone.key))
    if args.dry_run:
        _say("DRY RUN: nothing will be written and no mail will be sent.")

    allowlist = inbox.validate_allowlist(landlord.LANDLORD_ADDRESSES)
    address, password = credentials()
    since = now.date() - timedelta(days=landlord.LOOKBACK_DAYS)

    conn = inbox.connect(landlord.IMAP_HOST, address, password)
    try:
        messages, notes = inbox.fetch(
            conn, allowlist, since, landlord.TRUSTED_AUTHSERV_ID, landlord.MAX_BODY_CHARS
        )
    finally:
        try:
            conn.logout()
        except Exception:
            pass

    base_id = os.environ.get(BASE_ID_ENV, "").strip()
    return run(
        messages,
        notes,
        zone,
        args.dry_run,
        store_factory=_store,
        send_factory=lambda: extract.sdk_sender(landlord),
        notify=lambda subject, body: send_email(
            subject, body, [address], display_name=SENDER_NAME
        ),
        link=AIRTABLE_LINK.format(base_id=base_id),
    )


def run(messages, notes, zone, dry_run, store_factory, send_factory, notify, link,
        copy=DEFAULT_INBOX_COPY, settings=landlord, fields=DEFAULT_PROPOSAL_FIELDS):
    """Everything after the mailbox read. Returns the exit code.

    The factories are called only when needed, so a day with no landlord
    mail makes no Airtable call and no model call.
    """
    for note in notes:
        _say(note)
    if not messages:
        _say("No mail from the landlord in the lookback window. Nothing to do.")
        return 0
    _say("%d message(s) from the landlord in the lookback window." % len(messages))

    store = store_factory()
    existing = store.existing()
    new = [m for m in messages if m.message_id not in existing.message_ids]
    if not new:
        _say("All of them have been proposed before. Nothing to do.")
        return 0

    try:
        send = send_factory()
        extracted = []
        for message in new:
            items, item_notes = extract.extract(message, send, zone, copy, settings)
            for note in item_notes:
                _say(note)
            extracted.append((message, items))
    except extract.ExtractionError as exc:
        _say("Model call failed (%s). Changing nothing; the next run will retry." % exc)
        return 0

    rows, plan_notes = proposals.plan(
        extracted, existing, VISIT_TABLE, VISIT_DATE_FIELD, fields
    )
    for note in plan_notes:
        _say(note)
    if not rows:
        _say("Nothing in the new mail to propose.")
        return 0

    subject = copy.summary_subject.format(count=len(rows))
    body = summary_body(rows, link, copy)
    _say("%s %d row(s):" % ("Would propose" if dry_run else "Proposing", len(rows)))
    for row in rows:
        _say("  %s: %s" % (row.table, row.fields))
    if dry_run:
        _say("Would email the account owner:\n  Subject: %s\n%s" % (subject, _indent(body)))
        return 0

    store.write(rows)
    notify(subject, body)
    _say("Wrote %d row(s) and sent the summary." % len(rows))
    return 0


def summary_body(rows, link, copy=DEFAULT_INBOX_COPY):
    lines = [copy.summary_intro, ""]
    for row in rows:
        item = row.item
        if row.kind == extract.VISIT:
            lines.append(copy.visit_line.format(date=_format_date(item.date, copy)))
        else:
            due = _format_date(item.date, copy) if item.date else copy.no_due
            lines.append(copy.request_line.format(summary=item.summary, due=due))
        lines.append(copy.excerpt_line.format(excerpt=item.excerpt))
        lines.append("")
    lines.append(copy.summary_footer.format(link=link))
    return "\n".join(lines)


def _format_date(day, copy):
    return "%s %d %s %d" % (
        copy.weekday_names[day.weekday()], day.day, copy.month_names[day.month - 1], day.year
    )


def _store():
    from src.airtable import AirtableClient

    api_key = os.environ.get(API_KEY_ENV, "").strip()
    base_id = os.environ.get(BASE_ID_ENV, "").strip()
    missing = [n for n, v in ((API_KEY_ENV, api_key), (BASE_ID_ENV, base_id)) if not v]
    if missing:
        raise RuntimeError(
            "cannot reach Airtable: environment variable(s) %s are unset or empty"
            % ", ".join(missing)
        )
    return proposals.ProposalStore(
        AirtableClient(api_key, base_id), VISIT_TABLE, VISIT_DATE_FIELD, DEFAULT_PROPOSAL_FIELDS
    )


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="python3 -m src.read_inbox",
        description=(
            "Read the landlord's recent email and propose unconfirmed rows "
            "for a person to confirm or delete in Airtable."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="read mail, call the model, and print what would be written and emailed; touch nothing",
    )
    return parser.parse_args(argv)


def _indent(text):
    return "\n".join("    " + line for line in text.rstrip("\n").split("\n"))


def _say(message):
    print(message, flush=True)


if __name__ == "__main__":
    sys.exit(main())
