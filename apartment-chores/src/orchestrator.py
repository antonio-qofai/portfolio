"""Scheduler entry point. With src/report.py, one of the two modules that read
a clock, an environment, or argv.

Everything below it is pure or is a thin client. This file is the wiring: it
loads the term from config, reads Airtable, builds the schedule, and decides
what to write and what to send today.

Two jobs, either or both:

  --generate  write this week's assignments, and on a Monday send the digest
  --nudge     email whoever is past due, at most twice per assignment

With neither flag it does both, so the cron job is a bare invocation.

All date arithmetic happens in the policy timezone. The clock is read once,
at the top of the run, and passed down, so a run that straddles midnight or a
daylight saving boundary cannot disagree with itself halfway through.

No chore, person, room, or date appears here. The term comes from
config/calendar.py; everything else comes from Airtable.
"""

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from config.cadences import CADENCES
from config.calendar import AUTUMN_2026
from config.cleaner import CLEANER_BEHAVIOURS
from config.digest_copy import DEFAULT_DIGEST_COPY
from config.due_policy import DEFAULT_DUE_POLICY
from config.nudge_copy import DEFAULT_NUDGE_COPY
from config.digest_style import DEFAULT_DIGEST_STYLE
from config.rules import RULE_CATEGORIES
from config.sender import SENDER_NAME
from src import digest, nudge, rotation, schedule
from src.airtable import AirtableClient
from src.email_sender import send_email

TERM = AUTUMN_2026
POLICY = DEFAULT_DUE_POLICY
MONDAY = 0

API_KEY_ENV = "AIRTABLE_API_KEY"
BASE_ID_ENV = "AIRTABLE_BASE_ID"


@dataclass(frozen=True)
class Context:
    """Everything one run needs, read once."""

    client: object
    roster: tuple
    rules: tuple
    scheduled: tuple
    weeks: tuple
    assignments: object


def main(argv=None):
    args = _parse_args(argv)
    do_generate, do_nudge = _jobs(args)

    now = datetime.now(ZoneInfo(POLICY.timezone))
    _say("Run at %s (%s)." % (now.isoformat(timespec="seconds"), POLICY.timezone))
    if args.dry_run:
        _say("DRY RUN: nothing will be written and no mail will be sent.")

    context = _load(_client())
    week = _week_containing(now.date(), context.weeks)
    _report_week(week, now, context.weeks)

    if do_generate:
        _generate(context, week, args.dry_run)
        if now.weekday() == MONDAY:
            _send_digest(context, week, args.dry_run)
        else:
            _say("Digest: not a Monday, skipping.")

    if do_nudge:
        _nudge(context, now, args.dry_run)

    return 0


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="python3 -m src.orchestrator",
        description=(
            "Generate this week's chore assignments and nudge whoever is "
            "overdue. With no flags it does both."
        ),
    )
    parser.add_argument(
        "--generate",
        action="store_true",
        help="write this week's assignments; send the digest if today is Monday",
    )
    parser.add_argument(
        "--nudge", action="store_true", help="email whoever is past due"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would be written and emailed, and touch nothing",
    )
    return parser.parse_args(argv)


def _jobs(args):
    """Return (do_generate, do_nudge). Neither flag means both."""
    if not args.generate and not args.nudge:
        return True, True
    return args.generate, args.nudge


def _client():
    api_key = os.environ.get(API_KEY_ENV, "").strip()
    base_id = os.environ.get(BASE_ID_ENV, "").strip()
    missing = [
        name
        for name, value in ((API_KEY_ENV, api_key), (BASE_ID_ENV, base_id))
        if not value
    ]
    if missing:
        raise RuntimeError(
            "cannot reach Airtable: environment variable(s) %s are unset or "
            "empty" % ", ".join(missing)
        )
    return AirtableClient(api_key, base_id)


def _load(client):
    """Read Airtable once and build the term's whole schedule."""
    roster = tuple(client.roster())
    if not roster:
        raise RuntimeError(
            "the roster has no active people; there is nobody to assign to"
        )
    pairs = client.chores()
    chores = tuple(chore for chore, _ in pairs)
    details = {chore.key: detail for chore, detail in pairs}
    rules = tuple(client.rules())
    visits = tuple(client.cleaner_visits())

    scheduled = schedule.build(
        TERM, chores, roster, CADENCES, details, POLICY, CLEANER_BEHAVIOURS, visits
    )
    _report_totals(scheduled, roster)

    for visit in schedule.unhandled_visits(TERM, visits):
        _say(
            "WARNING: a confirmed cleaner visit on %s falls in a week with no "
            "rotation. Fold the prep into that week's one-off task by hand."
            % visit.visit_date
        )

    return Context(
        client=client,
        roster=roster,
        rules=rules,
        scheduled=scheduled,
        weeks=rotation.weeks(TERM),
        # One read of Assignments for the whole run. Taken before anything
        # is written, which is safe because the only week a run writes is
        # the current one and nothing in it can be overdue yet.
        assignments=client.assignments(roster),
    )


def _report_totals(scheduled, roster):
    """State the quarter arithmetic out loud on every run.

    rotation.validate_plan has already refused anything that cannot divide
    evenly, so this is not a check. It is the number a human can glance at to
    confirm the run understood the term the way they do.
    """
    per_person = {}
    for item in scheduled:
        per_person[item.assignee] = per_person.get(item.assignee, 0) + 1
    split = ", ".join(
        "%s %d" % (person, per_person.get(person, 0)) for person in roster
    )
    _say("Term schedule: %d assignments (%s)." % (len(scheduled), split))


def _week_containing(day, weeks):
    for week in weeks:
        if week.start_date <= day <= week.end_date:
            return week
    return None


def _report_week(week, now, weeks):
    if week is None:
        _say(
            "Today (%s) is outside %s, which runs %s to %s."
            % (now.date(), TERM.name, weeks[0].start_date, weeks[-1].end_date)
        )
        return
    _say(
        "Today is in week %d (%s to %s), which is %s."
        % (
            week.number,
            week.start_date,
            week.end_date,
            "active" if week.active else "inactive",
        )
    )


def _this_week(context, week):
    return tuple(item for item in context.scheduled if item.week.number == week.number)


def _generate(context, week, dry_run):
    if week is None or not week.active:
        _say("Generate: no rotation for today's week, nothing to write.")
        return

    scheduled = _this_week(context, week)
    if week.number in context.assignments.week_numbers:
        _say(
            "Generate: week %d already exists in Airtable, nothing written."
            % week.number
        )
        return

    if dry_run:
        _say("Generate: would write %d assignments for week %d:" % (len(scheduled), week.number))
        for item in scheduled:
            _say(
                "    %s — %s, due %s%s"
                % (
                    item.assignee,
                    item.name,
                    item.due.isoformat(timespec="minutes"),
                    " (prep)" if item.is_prep else "",
                )
            )
        return

    context.client.write_assignments(scheduled, context.assignments.week_numbers)
    _say("Generate: wrote %d assignments for week %d." % (len(scheduled), week.number))


def _send_digest(context, week, dry_run):
    if week is None or not week.active:
        _say("Digest: today's week carries no rotation, not sending.")
        return

    scheduled = _this_week(context, week)
    args = (
        week, scheduled, context.roster, context.rules,
        DEFAULT_DIGEST_COPY, RULE_CATEGORIES,
    )
    message = digest.render(*args)
    body_html = digest.render_html(*args, DEFAULT_DIGEST_STYLE)
    recipients = [person.email for person in context.roster]

    if dry_run:
        _say("Digest: would send to %s" % ", ".join(recipients))
        _say("    Subject: %s" % message.subject)
        _say(_indent(message.body))
        return

    send_email(
        message.subject, message.body, recipients, SENDER_NAME, body_html
    )
    _say("Digest: sent to %s" % ", ".join(recipients))


def _nudge(context, now, dry_run):
    client = context.client
    snapshot = context.assignments
    record_ids = snapshot.record_ids

    pending = nudge.pending_nudges(
        context.scheduled, snapshot.completed_ids, snapshot.nudge_log, now
    )
    if not pending:
        _say("Nudge: nothing overdue.")
        return

    sent = 0
    for item in pending:
        chore = item.scheduled_chore
        identity = (chore.week.number, chore.chore.key, chore.assignee)
        record_id = record_ids.get(identity)
        if record_id is None:
            _say(
                "Nudge: week %d %r has no Airtable row, skipping. It was never "
                "generated, or the row was edited by hand."
                % (chore.week.number, chore.name)
            )
            continue

        message = nudge.render_nudge(item, DEFAULT_NUDGE_COPY)
        if dry_run:
            _say(
                "Nudge: would send %s nudge to %s"
                % (item.nudge_type, chore.assignee.email)
            )
            _say("    Subject: %s" % message.subject)
            _say(_indent(message.body))
        else:
            # One recipient, always. A nudge to the group would make someone
            # publicly late, which PRD 5.7 rules out.
            send_email(
                message.subject, message.body, [chore.assignee.email], SENDER_NAME
            )
            client.record_nudge(record_id, item.nudge_type, now)
        sent += 1

    _say("Nudge: %d nudge(s) %s." % (sent, "to send" if dry_run else "sent"))


def _indent(text):
    return "\n".join("    " + line for line in text.rstrip("\n").split("\n"))


def _say(message):
    print(message, flush=True)


if __name__ == "__main__":
    sys.exit(main())
