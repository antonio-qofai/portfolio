"""Placement for chores that follow the cleaner instead of a fixed interval.

Pure functions. No file reads, no network, no clock. See
PRD-v1.2-after-cleaner.md for the rule this implements.

A placed chore gets one turn per person per term. The rotation engine can say
where a fixed-interval chore lands in any week of the term from its offset
alone. A placed chore's weeks depend on when the cleaner actually comes, and
on what has already been written, so this module takes both as inputs:

  written_weeks  the week numbers already in Assignments. Their rows are
                 history and are returned exactly as written, never
                 re-decided, so a visit confirmed or moved later cannot
                 change a week that has started.
  history        (week_number, chore_key, person) for every written row.

Every week not yet written is projected from today's confirmed visits. Only
the current week's projection is ever written, on its Monday, and a week that
exists is never regenerated, so the projection for later weeks is free to
change as visits are confirmed.

A turn lands in week W when any of these holds:

  target      W is the active week containing a confirmed visit plus the
              delay, rolled forward past inactive weeks, and no other
              confirmed visit falls in W.
  cap         the last clean, a roommate turn or a confirmed visit, was
              cap_active_weeks or more active weeks before W.
  end of term the people still owed a turn are at least as many as the
              active weeks left, counting W.

Turns go in rotation order from the chore's seed, skipping anyone who has
already had one. No chore, person, or room name appears here.
"""

from datetime import timedelta

from src import rotation

# Where the cap counts from when nobody has cleaned yet this term: as if the
# last clean were just before the first active week.
_BEFORE_TERM = -1


def place(term, chores, roster, cadences, cleaner_visits, written_weeks, history):
    """Return rotation.Assignment tuples for every placed chore, in week order.

    chores must all use a cadence found in cadences. history rows for other
    chores are ignored.
    """
    if not roster:
        raise ValueError("roster is empty; there is nobody to assign chores to")
    active = rotation.active_weeks(term)
    in_term = _confirmed_in_term(term, cleaner_visits)
    visit_positions = tuple(_position_at_or_before(v.visit_date, active) for v in in_term)
    visit_weeks = {_week_containing(v.visit_date, rotation.weeks(term)).number for v in in_term}

    placed = []
    for chore in chores:
        cadence = _cadence_for(chore, cadences)
        targets = _target_positions(in_term, cadence, term, active, visit_weeks)
        placed.extend(
            _place_one(
                chore, cadence, roster, active, visit_positions, targets,
                frozenset(written_weeks), history,
            )
        )
    return tuple(sorted(placed, key=lambda a: a.week.number))


def _place_one(chore, cadence, roster, active, visit_positions, targets, written_weeks, history):
    by_week = {}
    for week_number, chore_key, person in history:
        if chore_key == chore.key:
            by_week.setdefault(week_number, []).append(person)

    had = []
    last_turn = _BEFORE_TERM
    result = []
    for position, week in enumerate(active):
        if week.number in written_weeks:
            for person in by_week.get(week.number, ()):
                if person in had:
                    raise ValueError(
                        "chore %r: %s already had a turn this term and is "
                        "assigned it again in week %d; each person gets one"
                        % (chore.key, person, week.number)
                    )
                result.append(_assignment(week, chore, len(had), person))
                had.append(person)
                last_turn = position
            if len(had) > len(roster):
                raise ValueError(
                    "chore %r has %d turns written this term but the roster "
                    "has %d people" % (chore.key, len(had), len(roster))
                )
            continue

        owed = len(roster) - len(had)
        if owed == 0:
            continue

        last_clean = max(
            (last_turn,) + tuple(p for p in visit_positions if p < position)
        )
        due = (
            position in targets
            or position - last_clean >= cadence.cap_active_weeks
            or owed >= len(active) - position
        )
        if not due:
            continue

        person = _next_in_rotation(chore, roster, had)
        result.append(_assignment(week, chore, len(had), person))
        had.append(person)
        last_turn = position
    return result


def _target_positions(visits, cadence, term, active, visit_weeks):
    """Active positions that a visit plus the delay lands on."""
    all_weeks = rotation.weeks(term)
    positions = set()
    for visit in visits:
        day = visit.visit_date + timedelta(days=cadence.delay_days)
        week = _week_containing(day, all_weeks)
        if week is None:
            continue
        # Roll forward past inactive weeks. Past the last active week there
        # is nothing to roll to, and the visit gives no target.
        later = [w for w in active if w.number >= week.number]
        if not later:
            continue
        target = later[0]
        if target.number in visit_weeks:
            continue
        positions.add(active.index(target))
    return positions


def _confirmed_in_term(term, cleaner_visits):
    weeks = rotation.weeks(term)
    return tuple(
        visit for visit in cleaner_visits
        if visit.confirmed and _week_containing(visit.visit_date, weeks) is not None
    )


def _position_at_or_before(day, active):
    """Index of the last active week starting on or before day.

    A visit in an inactive week counts from the active week before it, so a
    clean over Thanksgiving still resets the cap.
    """
    position = _BEFORE_TERM
    for index, week in enumerate(active):
        if week.start_date <= day:
            position = index
    return position


def _week_containing(day, weeks):
    for week in weeks:
        if week.start_date <= day <= week.end_date:
            return week
    return None


def _next_in_rotation(chore, roster, had):
    for step in range(len(roster)):
        person = roster[(chore.seed + len(had) + step) % len(roster)]
        if person not in had:
            return person
    raise AssertionError("no one owed a turn; the caller checks this first")


def _assignment(week, chore, index, person):
    return rotation.Assignment(week=week, chore=chore, occurrence_index=index, assignee=person)


def _cadence_for(chore, cadences):
    try:
        cadence = cadences[chore.cadence]
    except KeyError:
        raise ValueError(
            "chore %r has cadence %r, which is not a placed cadence; known "
            "placed cadences are %s" % (chore.key, chore.cadence, sorted(cadences))
        ) from None
    if cadence.delay_days < 0 or cadence.cap_active_weeks < 1:
        raise ValueError(
            "placed cadence %r needs delay_days of 0 or more and "
            "cap_active_weeks of 1 or more" % (cadence.name,)
        )
    if chore.seed < 0:
        raise ValueError("chore %r has seed %d; it must be 0 or greater" % (chore.key, chore.seed))
    return cadence
