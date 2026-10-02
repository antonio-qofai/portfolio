"""Packet-consistency guard — a packet may not contradict itself.

The failure this exists to kill: a status deck shipped with a footer reading
"Week 5 of 16" over a Gantt whose dated columns stopped at W10 (Antonio,
2026-07-24). Nothing in the pipeline was broken. The packet said the project ran
16 weeks in one field and 10 weeks in another, the renderer faithfully rendered
both, and the deck told a client two different things about the same schedule.

The two guards that already existed cannot catch this by construction:

- ``coverage_guard`` proves the packet-to-prompt boundary is drop-free. Both
  contradicting fields were slotted and carried through perfectly.
- ``render_guard`` proves the prompt-to-HTML boundary is drop-free. Both
  contradicting values survived into the HTML perfectly.

Faithfully rendering an incoherent packet produces an incoherent deck. So this
module checks the packet against ITSELF, before a prompt is assembled and before
an API call is spent: cross-field invariants that must hold for the deck to make
sense, whatever the values are. A violation raises ``ConsistencyError`` naming
every contradiction found (all of them, not just the first — a re-baselined plan
usually breaks several at once), so the fix is one pass over the data source.

Nothing here is keyed to a client's values. Every check reads two or more packet
fields and asserts a relationship between them, so it holds for any packet
conforming to the schema.

Scope: the status packet today, whose time-aware fields are the ones that can
disagree (dated columns, a TODAY marker, "Week N of M", a per-slide count). The
proposal packet's relative week buckets carry no equivalent cross-field
arithmetic, so ``check_consistency`` is a no-op there rather than a fabricated
check.
"""

import re

from data_source_adapter import _section_yaml, _split_sections


class ConsistencyError(AssertionError):
    """A packet contradicts itself across two or more fields.

    Subclasses ``AssertionError`` so a run that trips the guard fails as loudly
    as a broken invariant, and so the lightweight test runners in ``tests/``
    (which catch ``AssertionError``) report it as a clean failure — the same
    contract as ``CoverageError`` and ``RenderFidelityError``.
    """


# A column id of the form W<number> (the contract's weekly-cadence id). Ids that
# do not match are not week-numbered, so the ordinal position is used instead.
_WEEK_ID_RE = re.compile(r"^[Ww](\d+)$")

# "Week 5 of 16" in any casing, as it appears in `engagement.project_week` and,
# uppercased, inside `tracking.section_label` ("PHASED ROLLOUT · WEEK 5 OF 16").
_WEEK_N_OF_M_RE = re.compile(r"week\s+(\d+)\s+of\s+(\d+)", re.IGNORECASE)


def _week_number(column, position):
    """The plan week a column represents: its ``W<n>`` id, else its 1-based
    position. Falling back to position means a packet using a non-``W<n>`` id
    scheme still gets a coherent length check instead of being skipped."""
    match = _WEEK_ID_RE.match(str(column.get("id") or ""))
    return int(match.group(1)) if match else position


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _check_timeline_length(tracking, engagement, problems):
    """The dated columns must reach at least the last week of the plan.

    The contract lets the column set run PAST ``project_week_m`` (a plan can show
    buffer or phases beyond the reporting window). It never lets the columns stop
    short: a deck whose footer claims a 16-week project over a 10-week timeline
    is the exact defect this module was written for.
    """
    columns = tracking.get("columns") or []
    week_m = _int_or_none(engagement.get("project_week_m"))
    if not columns or week_m is None:
        return
    last_week = max(
        _week_number(column, position)
        for position, column in enumerate(columns, start=1)
    )
    if last_week < week_m:
        problems.append(
            f"the Gantt's dated columns stop at week {last_week} but "
            f"engagement.project_week_m says the plan runs {week_m} weeks, so the "
            f"footer would read \"Week N of {week_m}\" over a {last_week}-week "
            "timeline. Extend tracking.columns through week "
            f"{week_m} (they may run past it), or correct project_week_m."
        )


def _check_today_marker(tracking, engagement, problems):
    """The TODAY marker must sit on a real column, and on the current week."""
    columns = tracking.get("columns") or []
    if not columns:
        return
    on_week = (tracking.get("today_marker") or {}).get("on_week")
    if not on_week:
        return
    ids = [str(column.get("id")) for column in columns]
    if str(on_week) not in ids:
        problems.append(
            f"tracking.today_marker.on_week is {on_week!r}, which is not one of "
            f"the dated columns ({', '.join(ids)}), so the TODAY line has no "
            "column to land on."
        )
        return
    week_n = _int_or_none(engagement.get("project_week_n"))
    if week_n is None:
        return
    position = ids.index(str(on_week)) + 1
    marker_week = _week_number(columns[position - 1], position)
    if marker_week != week_n:
        problems.append(
            f"tracking.today_marker.on_week ({on_week}) is plan week "
            f"{marker_week} but engagement.project_week_n says the check-in is "
            f"week {week_n}, so the TODAY line and the footer would disagree "
            "about which week it is."
        )


def _check_bar_weeks(tracking, problems):
    """Every bar's start and end week must be a real column, in that order.

    A bar naming a week the header does not carry leaves the renderer with a bar
    it cannot place — it either invents a column or drops the bar, both silent.
    """
    columns = tracking.get("columns") or []
    if not columns:
        return
    index = {
        str(column.get("id")): position
        for position, column in enumerate(columns, start=1)
    }
    for lane in tracking.get("lanes") or []:
        lane_name = lane.get("name")
        for bar in lane.get("bars") or []:
            label = bar.get("label")
            start, end = str(bar.get("start_week")), str(bar.get("end_week"))
            unknown = [week for week in (start, end) if week not in index]
            if unknown:
                problems.append(
                    f"Gantt bar {label!r} in lane {lane_name!r} names week(s) "
                    f"{', '.join(unknown)}, which are not dated columns, so the "
                    "bar cannot be placed on the grid."
                )
                continue
            if index[start] > index[end]:
                problems.append(
                    f"Gantt bar {label!r} in lane {lane_name!r} starts on "
                    f"{start} and ends on {end}, which is earlier — the bar runs "
                    "backwards."
                )


def _check_week_labels(tracking, engagement, problems):
    """Any "Week N of M" text on the deck must match project_week_n / _m.

    ``engagement.project_week`` prints in the footer and
    ``tracking.section_label`` prints as the tracking slide's eyebrow. Both are
    prose restatements of the same two numbers, so they are exactly where a
    re-baselined plan leaves a stale week behind.
    """
    week_n = _int_or_none(engagement.get("project_week_n"))
    week_m = _int_or_none(engagement.get("project_week_m"))
    if week_n is None or week_m is None:
        return
    for field, text in (
        ("engagement.project_week", engagement.get("project_week")),
        ("tracking.section_label", tracking.get("section_label")),
    ):
        match = _WEEK_N_OF_M_RE.search(str(text or ""))
        if not match:
            continue
        found_n, found_m = int(match.group(1)), int(match.group(2))
        if (found_n, found_m) != (week_n, week_m):
            problems.append(
                f"{field} reads \"week {found_n} of {found_m}\" but "
                f"engagement.project_week_n / _m say week {week_n} of {week_m}."
            )


def _check_slide_count(deck, workstreams, problems):
    """The footer denominator must equal the deck the packet describes.

    A status deck is cover + tracking + one slide per workstream + next steps, so
    ``total_slides`` is derivable. It is carried in the packet (never hardcoded at
    render time, PRD S7), which means it can also be carried WRONG — and it is the
    denominator printed on every single slide.
    """
    n_declared = _int_or_none(deck.get("n_workstreams"))
    n_actual = len(workstreams)
    if n_declared is not None and n_declared != n_actual:
        problems.append(
            f"deck.n_workstreams says {n_declared} but the workstreams section "
            f"carries {n_actual}."
        )
    total = _int_or_none(deck.get("total_slides"))
    expected = n_actual + 3  # cover + tracking + N workstreams + next steps
    if total is not None and total != expected:
        problems.append(
            f"deck.total_slides says {total} but the deck is cover + tracking + "
            f"{n_actual} workstream slide(s) + next steps = {expected}, so every "
            "slide's footer would print the wrong denominator."
        )


def check_status_consistency(packet_md):
    """Assert a status packet does not contradict itself. Raises ``ConsistencyError``.

    Runs every check and reports all violations at once. Returns the (empty)
    problem list on success so a caller can log "checked, clean" rather than
    inferring it from the absence of a raise.
    """
    sections = _split_sections(packet_md)
    y1 = _section_yaml(sections.get(1, ""))
    y2 = _section_yaml(sections.get(2, ""))
    y3 = _section_yaml(sections.get(3, ""))

    deck = y1.get("deck") or {}
    engagement = y1.get("engagement") or {}
    tracking = y2.get("tracking") or {}
    workstreams = y3.get("workstreams") or []

    problems = []
    _check_timeline_length(tracking, engagement, problems)
    _check_today_marker(tracking, engagement, problems)
    _check_bar_weeks(tracking, problems)
    _check_week_labels(tracking, engagement, problems)
    _check_slide_count(deck, workstreams, problems)

    if problems:
        listing = "\n".join(f"  - {problem}" for problem in problems)
        raise ConsistencyError(
            "the data packet contradicts itself, so the deck would carry two "
            "different versions of the same fact:\n"
            f"{listing}\n"
            "Fix the packet at the data source. The pipeline renders faithfully "
            "and will not paper over a contradiction."
        )
    return problems


# Per-deck-type dispatch. The proposal packet carries no cross-field time
# arithmetic (its week buckets are relative, e.g. "WKS 1-8", and its slide count
# is fixed), so it has no checks rather than invented ones.
_CHECKERS = {
    "status": check_status_consistency,
    "proposal": lambda packet_md: [],
}


def check_consistency(packet_md, *, deck_type="proposal"):
    """Run the consistency checks for ``deck_type``. Raises ``ConsistencyError``
    on a self-contradicting packet; returns the empty problem list otherwise.

    Raises ``ValueError`` for an unknown ``deck_type`` — a caller-side violation,
    matching ``coverage_guard.check_coverage``.
    """
    checker = _CHECKERS.get(deck_type)
    if checker is None:
        raise ValueError(f"unknown deck_type: {deck_type!r}")
    return checker(packet_md)
