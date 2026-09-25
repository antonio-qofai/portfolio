"""Nudge decision logic and body renderer.

Decides which open assignments are overdue and what kind of nudge to send.
Renders the email body for each. Pure: now, the schedule, completed IDs, and
the nudge log are all arguments. No clock, no file reads, no network.

Rules (PRD 5.7):
  - One nudge when an assignment is past its due date.
  - One follow-up 48 hours after the first nudge was sent.
  - Never a third. Never to the group.

An assignment is uniquely identified by (week_number, chore_key, assignee).
The caller is responsible for passing objects that are hashable and comparable
by value (strings, ints, frozen dataclasses, etc.).
"""

from dataclasses import dataclass
from datetime import timedelta

from src.digest import format_due

FIRST = "first"
FOLLOWUP = "followup"
FOLLOWUP_DELAY = timedelta(hours=48)


@dataclass(frozen=True)
class NudgeRecord:
    """One entry in the nudge log.

    Records what was sent and when, so pending_nudges can determine what to
    send next without touching a clock or a file.
    """

    week_number: int
    chore_key: object
    assignee: object
    nudge_type: str
    sent_at: object


@dataclass(frozen=True)
class PendingNudge:
    """One nudge that should be sent now."""

    scheduled_chore: object
    nudge_type: str


@dataclass(frozen=True)
class NudgeEmail:
    """A rendered nudge email, ready for the sender."""

    subject: str
    body: str


def pending_nudges(scheduled, completed_ids, nudge_log, now):
    """Return the nudges that should be sent at this moment.

    scheduled:     sequence of ScheduledChore (all assignments, any week)
    completed_ids: frozenset of (week_number, chore_key, assignee) for done
    nudge_log:     sequence of NudgeRecord
    now:           timezone-aware datetime
    """
    log_by_assignment = {}
    for record in nudge_log:
        aid = (record.week_number, record.chore_key, record.assignee)
        log_by_assignment.setdefault(aid, []).append(record)

    result = []
    for sc in scheduled:
        aid = (sc.week.number, sc.chore.key, sc.assignee)

        if aid in completed_ids:
            continue

        if now <= sc.due:
            continue

        records = log_by_assignment.get(aid, [])
        first_records = [r for r in records if r.nudge_type == FIRST]
        followup_records = [r for r in records if r.nudge_type == FOLLOWUP]

        if followup_records:
            continue

        if first_records:
            earliest_first = min(r.sent_at for r in first_records)
            if now >= earliest_first + FOLLOWUP_DELAY:
                result.append(PendingNudge(scheduled_chore=sc, nudge_type=FOLLOWUP))
        else:
            result.append(PendingNudge(scheduled_chore=sc, nudge_type=FIRST))

    return tuple(result)


def render_nudge(pending, copy):
    """Return a NudgeEmail for one pending nudge.

    pending: PendingNudge
    copy:    NudgeCopy
    """
    sc = pending.scheduled_chore
    due_str = format_due(sc.due, copy)
    if pending.nudge_type == FIRST:
        subject = copy.first_subject.format(name=sc.name)
        body = copy.first_body.format(name=sc.name, task=sc.task, due=due_str)
    else:
        subject = copy.followup_subject.format(name=sc.name)
        body = copy.followup_body.format(name=sc.name, task=sc.task, due=due_str)
    return NudgeEmail(subject=subject, body=body + "\n")
