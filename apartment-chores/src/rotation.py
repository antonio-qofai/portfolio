"""Rotation and calendar engine.

Pure functions. No file reads, no network, no clock, no global state. Every
chore, person, week, and cadence arrives as an argument and every result is a
returned value, so the whole module is testable by calling it.

The engine knows two things: which weeks of a term are active, and how to walk
a chore's own occurrence counter around a roster. It knows nothing about any
particular apartment.
"""

from dataclasses import dataclass
from datetime import date, timedelta

DAYS_PER_WEEK = 7
MONDAY = 0


@dataclass(frozen=True)
class Week:
    """One calendar week of a term.

    number is 1-based within the term. start_date is a Monday, end_date the
    following Sunday. active is False for weeks the term marks as inactive.
    """

    number: int
    start_date: date
    end_date: date
    active: bool


@dataclass(frozen=True)
class Chore:
    """A rotating chore, as far as the rotation engine is concerned.

    key is an opaque identifier chosen by the caller (an Airtable record ID,
    for example). The engine never interprets it.

    cadence is a key into the cadence registry passed alongside. offset selects
    which slot inside the cadence interval the chore lands on, counted in
    active weeks, and must be less than the interval. seed is the starting
    index into the roster.
    """

    key: object
    cadence: str
    offset: int = 0
    seed: int = 0


@dataclass(frozen=True)
class Assignment:
    """One chore, in one week, assigned to one member of the roster."""

    week: Week
    chore: Chore
    occurrence_index: int
    assignee: object


def weeks(term):
    """Return every calendar week of the term, in order."""
    _validate_term(term)
    return tuple(
        Week(
            number=number,
            start_date=term.start_date + timedelta(days=(number - 1) * DAYS_PER_WEEK),
            end_date=term.start_date
            + timedelta(days=(number - 1) * DAYS_PER_WEEK + DAYS_PER_WEEK - 1),
            active=number not in term.inactive_weeks,
        )
        for number in range(1, term.week_count + 1)
    )


def active_weeks(term):
    """Return only the weeks that carry rotation, in order.

    This is the sequence everything else counts against. A chore's occurrence
    index is derived from a week's position in this list, never from its
    calendar number, so inserting an inactive week shifts nothing.
    """
    return tuple(week for week in weeks(term) if week.active)


def occurrence_index(chore, week, term, cadences):
    """Return the chore's 0-based occurrence index in that week, or None.

    None means the chore does not occur that week, either because the week is
    inactive or because the week does not fall on the chore's cadence.
    """
    cadence = _cadence_for(chore, cadences)
    _validate_chore(chore, cadence)

    position = _active_position(week, term)
    if position is None:
        return None

    steps = position - chore.offset
    if steps < 0 or steps % cadence.interval != 0:
        return None
    return steps // cadence.interval


def occurrences(chore, term, cadences):
    """Return every (week, occurrence_index) pair for the chore in the term."""
    return tuple(
        (week, index)
        for week in active_weeks(term)
        for index in (occurrence_index(chore, week, term, cadences),)
        if index is not None
    )


def occurrence_count(chore, term, cadences):
    """Return how many times the chore occurs across the term."""
    return len(occurrences(chore, term, cadences))


def assignee(chore, index, roster):
    """Return whose turn it is for the given occurrence of the chore.

    The rotation rule, and the only one: roster[(seed + k) % len(roster)].
    The counter k is the chore's own occurrence index, so each chore advances
    on its own clock and is unaffected by any other chore.
    """
    _validate_roster(roster)
    if index < 0:
        raise ValueError(
            "occurrence index must be 0 or greater, got %r for chore %r"
            % (index, chore.key)
        )
    return roster[(chore.seed + index) % len(roster)]


def validate_plan(term, chores, roster, cadences):
    """Raise if the term, chores, and roster cannot divide evenly.

    Even rotation requires every chore's occurrence count to be divisible by
    the roster size. This is the arithmetic the whole design rests on, so a
    plan that fails it is an error rather than something to round off.
    """
    _validate_roster(roster)
    for chore in chores:
        count = occurrence_count(chore, term, cadences)
        if count % len(roster) != 0:
            raise ValueError(
                "chore %r occurs %d times over %d active weeks, which does not "
                "divide evenly among %d people; rotation would be unfair"
                % (chore.key, count, len(active_weeks(term)), len(roster))
            )


def generate(term, chores, roster, cadences):
    """Return every assignment for the term, in week then chore order.

    Pure: the same inputs always produce the same output, so a caller can
    generate a single week by filtering, and regenerating an existing week
    produces identical records rather than different ones.
    """
    validate_plan(term, chores, roster, cadences)
    return tuple(
        Assignment(
            week=week,
            chore=chore,
            occurrence_index=index,
            assignee=assignee(chore, index, roster),
        )
        for week in active_weeks(term)
        for chore in chores
        for index in (occurrence_index(chore, week, term, cadences),)
        if index is not None
    )


def generate_week(term, week_number, chores, roster, cadences):
    """Return the assignments for a single calendar week of the term."""
    return tuple(
        assignment
        for assignment in generate(term, chores, roster, cadences)
        if assignment.week.number == week_number
    )


def _active_position(week, term):
    """Return the week's 0-based index among active weeks, or None."""
    for position, active in enumerate(active_weeks(term)):
        if active.number == week.number:
            return position
    if 1 <= week.number <= term.week_count:
        return None
    raise ValueError(
        "week %r is not part of term %r, which runs weeks 1 to %d"
        % (week.number, term.name, term.week_count)
    )


def _cadence_for(chore, cadences):
    try:
        return cadences[chore.cadence]
    except KeyError:
        raise ValueError(
            "chore %r has unknown cadence %r; known cadences are %s"
            % (chore.key, chore.cadence, sorted(cadences))
        ) from None


def _validate_chore(chore, cadence):
    if cadence.interval < 1:
        raise ValueError(
            "cadence %r has interval %d; it must be 1 or greater"
            % (cadence.name, cadence.interval)
        )
    if chore.offset < 0 or chore.offset >= cadence.interval:
        raise ValueError(
            "chore %r has offset %d, which is outside cadence %r (valid "
            "offsets are 0 to %d)"
            % (chore.key, chore.offset, cadence.name, cadence.interval - 1)
        )
    if chore.seed < 0:
        raise ValueError(
            "chore %r has seed %d; it must be 0 or greater" % (chore.key, chore.seed)
        )


def _validate_roster(roster):
    if len(roster) == 0:
        raise ValueError("roster is empty; there is nobody to assign chores to")


def _validate_term(term):
    if term.week_count < 1:
        raise ValueError(
            "term %r has %d weeks; it must have at least 1"
            % (term.name, term.week_count)
        )
    if term.start_date.weekday() != MONDAY:
        raise ValueError(
            "term %r starts on %s, but weeks are Monday-anchored; start_date "
            "must be a Monday" % (term.name, term.start_date.strftime("%A"))
        )
    out_of_range = sorted(
        number for number in term.inactive_weeks if not 1 <= number <= term.week_count
    )
    if out_of_range:
        raise ValueError(
            "term %r lists inactive weeks %s, which are outside weeks 1 to %d"
            % (term.name, out_of_range, term.week_count)
        )
