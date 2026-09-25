"""Schedule builder.

Turns the rotation engine's bare assignments into the things that actually get
written and emailed: what the task says, when it is due, and whether a
confirmed cleaner visit turned it into prep.

Pure functions. No file reads, no network, no clock. The current date is not
an input here; this module says what a term's schedule is, not what is due
today. Everything it needs about a chore beyond the rotation arrives in a
details mapping supplied by the caller, so no chore, room, or person name
appears in this file.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src import rotation


@dataclass(frozen=True)
class ChoreDetail:
    """Everything about a chore that the rotation engine does not need.

    name is what the chore is called. task is what doing it means in an
    ordinary week, the definition of done. prep_task is what it means instead
    in a confirmed cleaner week, and is required when the behaviour converts.
    cleaner_behaviour is a key into the behaviour registry.

    The name and the task are separate because a digest line needs both: the
    name is how someone recognises their slot, the task is what they actually
    have to do.
    """

    name: str
    task: str
    cleaner_behaviour: str
    prep_task: str = ""


@dataclass(frozen=True)
class CleanerVisit:
    """One row of the Cleaner Visits table.

    Only confirmed visits affect generation. A projected row is planning
    scaffolding and changes nothing, which is why it is a field here rather
    than something the caller filters out before calling.
    """

    visit_date: object
    confirmed: bool = False


@dataclass(frozen=True)
class ScheduledChore:
    """One assignment, ready to be written out.

    assignee, week, and occurrence_index come straight from the rotation and
    are never altered by cleaner handling, which is the whole point of
    converting rather than cancelling.
    """

    week: object
    chore: object
    occurrence_index: int
    assignee: object
    name: str
    task: str
    due: datetime
    is_prep: bool
    cleaner_visit: object = None


def build(
    term,
    chores,
    roster,
    cadences,
    details,
    policy,
    behaviours,
    cleaner_visits=(),
):
    """Return the full schedule for the term, in week then chore order.

    Pure and deterministic: the same inputs always produce the same output, so
    regenerating a week that already exists yields identical records rather
    than different ones.
    """
    zone = ZoneInfo(policy.timezone)
    confirmed = _confirmed_visits_by_week(term, cleaner_visits)

    scheduled = []
    for assignment in rotation.generate(term, chores, roster, cadences):
        detail = _detail_for(assignment.chore, details)
        behaviour = _behaviour_for(assignment.chore, detail, behaviours)
        visit = confirmed.get(assignment.week.number)

        if visit is not None and behaviour.converts:
            task = detail.prep_task
            due = _prep_due(visit, policy, zone)
            is_prep = True
        else:
            task = detail.task
            due = _normal_due(assignment.week, policy, zone)
            is_prep = False
            visit = None

        scheduled.append(
            ScheduledChore(
                week=assignment.week,
                chore=assignment.chore,
                occurrence_index=assignment.occurrence_index,
                assignee=assignment.assignee,
                name=detail.name,
                task=task,
                due=due,
                is_prep=is_prep,
                cleaner_visit=visit,
            )
        )
    return tuple(scheduled)


def build_week(
    term,
    week_number,
    chores,
    roster,
    cadences,
    details,
    policy,
    behaviours,
    cleaner_visits=(),
):
    """Return the schedule for a single calendar week of the term."""
    return tuple(
        item
        for item in build(
            term, chores, roster, cadences, details, policy, behaviours,
            cleaner_visits,
        )
        if item.week.number == week_number
    )


def unhandled_visits(term, cleaner_visits):
    """Return confirmed visits that no rotation week can absorb.

    A visit in an inactive week or outside the term entirely is not an error:
    prep still has to happen, it just has no rotation to convert and has to be
    folded into that week's one-off task instead. Returning them rather than
    ignoring them keeps that from happening silently.
    """
    weeks_by_number = {week.number: week for week in rotation.weeks(term)}
    stranded = []
    for visit in cleaner_visits:
        if not visit.confirmed:
            continue
        week = _week_containing(visit.visit_date, weeks_by_number)
        if week is None or not week.active:
            stranded.append(visit)
    return tuple(stranded)


def normal_due(week, policy):
    """Return the ordinary due datetime for a week, timezone-aware."""
    return _normal_due(week, policy, ZoneInfo(policy.timezone))


def prep_due(visit, policy):
    """Return the prep due datetime for a visit, timezone-aware."""
    return _prep_due(visit, policy, ZoneInfo(policy.timezone))


def _normal_due(week, policy, zone):
    day = week.start_date + timedelta(days=policy.normal_day_offset)
    if not week.start_date <= day <= week.end_date:
        raise ValueError(
            "due policy offset of %d days falls outside week %d (%s to %s)"
            % (policy.normal_day_offset, week.number, week.start_date, week.end_date)
        )
    return datetime.combine(day, policy.normal_time, tzinfo=zone)


def _prep_due(visit, policy, zone):
    return datetime.combine(visit.visit_date, policy.prep_time, tzinfo=zone)


def _confirmed_visits_by_week(term, cleaner_visits):
    """Map week number to the one confirmed visit in it.

    Two confirmed visits in the same week is a hand-edit error rather than a
    scenario with an obvious answer, so it raises instead of picking one.
    """
    weeks_by_number = {week.number: week for week in rotation.weeks(term)}
    found = {}
    for visit in cleaner_visits:
        if not visit.confirmed:
            continue
        week = _week_containing(visit.visit_date, weeks_by_number)
        if week is None or not week.active:
            continue
        if week.number in found:
            raise ValueError(
                "two confirmed cleaner visits fall in week %d (%s and %s); "
                "only one can set the prep due time"
                % (week.number, found[week.number].visit_date, visit.visit_date)
            )
        found[week.number] = visit
    return found


def _week_containing(day, weeks_by_number):
    for week in weeks_by_number.values():
        if week.start_date <= day <= week.end_date:
            return week
    return None


def _detail_for(chore, details):
    try:
        detail = details[chore.key]
    except KeyError:
        raise ValueError(
            "chore %r has no detail record, so there is no task text to "
            "schedule" % (chore.key,)
        ) from None
    if not detail.name.strip():
        raise ValueError("chore %r has an empty name" % (chore.key,))
    if not detail.task.strip():
        raise ValueError("chore %r has an empty task" % (chore.key,))
    return detail


def _behaviour_for(chore, detail, behaviours):
    try:
        behaviour = behaviours[detail.cleaner_behaviour]
    except KeyError:
        raise ValueError(
            "chore %r has unknown cleaner behaviour %r; known behaviours are %s"
            % (chore.key, detail.cleaner_behaviour, sorted(behaviours))
        ) from None
    if behaviour.converts and not detail.prep_task.strip():
        raise ValueError(
            "chore %r uses cleaner behaviour %r but has no prep task, so a "
            "cleaner week would blank it out" % (chore.key, behaviour.name)
        )
    return behaviour
