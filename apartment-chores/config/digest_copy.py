"""Every word the digest can say.

All of it lives here so the wording can be changed without touching code, and
so no English sits in `src/`. Month and weekday names are spelled out rather
than taken from strftime, which keeps the output identical regardless of the
locale the machine happens to run under.

Line templates are format strings. Any field listed in their docstring below
is available, so adding the definition of done to the ordinary chore line is
a matter of putting {task} in it.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DigestCopy:
    """Templates and fixed strings for the weekly digest.

    subject and header take {number}, {start}, {end}.
    due_note takes {due}; blank to put the deadline back on every line.
    cleaner_note takes {date} and {due}.
    chore_line and prep_chore_line take {name}, {task}, {due}.
    prep_note takes {task} and is the HTML digest's equivalent.
    rule_numbered takes {number} and {text}; rule_unnumbered takes {text}.
    due_format takes {weekday}, {month}, {day}, {hour}, {minute}, {meridiem}.
    date_format takes {weekday}, {month}, {day}. range_format takes {month}
    and {day}.
    """

    subject: str
    header: str
    due_note: str
    cleaner_note: str
    no_chores: str
    person_heading: str
    chore_line: str
    prep_chore_line: str
    nothing_for_person: str
    prep_note: str
    rules_heading: str
    rule_numbered: str
    rule_unnumbered: str
    footer: str
    due_format: str
    date_format: str
    range_format: str
    weekday_names: tuple
    month_names: tuple
    am: str
    pm: str


DEFAULT_DIGEST_COPY = DigestCopy(
    subject="Chores for week {number}, {start} to {end}",
    header="Week {number}, {start} to {end}",
    # Said once here instead of on all seven lines. Every ordinary chore
    # in a week shares a deadline, so repeating it is noise.
    due_note="Due {due} unless a line says otherwise.",
    cleaner_note=(
        "The cleaner comes {date}. Prep tasks are due {due}, before she "
        "arrives. Everything else is due as usual."
    ),
    no_chores="No rotating chores this week.",
    person_heading="{person}",
    chore_line="  - {name}",
    prep_chore_line="  - {name}, prep only this week: {task}",
    nothing_for_person="  - nothing this week",
    # Used by the HTML digest, where the name is its own element
    # and the prep wording hangs off it rather than being inline.
    prep_note="prep only this week: {task}",
    rules_heading="House rules",
    rule_numbered="  {number}. {text}",
    rule_unnumbered="  - {text}",
    # The chore list, shared read-only. The definitions of done are long
    # enough to settle an argument but too long to put on every digest line,
    # so the digest stays short and points at them instead.
    footer=(
        "Tick chores off in the app as you finish them.\n"
        "Not sure what done means? Every chore is spelled out here:\n"
        "https://airtable.com/appXXXXXXXXXXXXXX/shrXXXXXXXXXXXXXX"
    ),
    due_format="{weekday} {month} {day}, {hour}{minute}{meridiem}",
    date_format="{weekday} {month} {day}",
    range_format="{month} {day}",
    weekday_names=("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
    month_names=(
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ),
    am="am",
    pm="pm",
)
