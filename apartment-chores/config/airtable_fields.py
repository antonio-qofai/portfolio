"""Airtable table and field name configuration.

Every string here is the exact name of a table or field in the Airtable base.
Build the base to match these names. To rename something in Airtable, change
the string here and nowhere else.

Table and field types are noted in comments. Single-select values that must
match the code's registry keys are called out explicitly.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class _RosterFields:
    name: str        # Text
    email: str       # Email
    active: str      # Checkbox
    sort_order: str  # Number (integer) — drives rotation order; must be unique


@dataclass(frozen=True)
class _ChoreFields:
    name: str               # Text
    cadence: str            # Single select: "weekly" or "every_3"
    offset: str             # Number (integer, 0-based) — optional, defaults to 0
    seed: str               # Number (integer, 0-based) — optional, defaults to 0
    task: str               # Long text — definition of done in a normal week
    cleaner_behaviour: str  # Single select: "normal" or "convert_to_prep"
    prep_task: str          # Long text — required when cleaner_behaviour is convert_to_prep


@dataclass(frozen=True)
class _RuleFields:
    text: str          # Long text
    category: str      # Single select: "override" or "standing"
    number: str        # Number — optional, displayed as-is
    active_from: str   # Date — optional, rule is inactive before this date
    active_until: str  # Date — optional, rule is inactive after this date


@dataclass(frozen=True)
class _CleanerVisitFields:
    visit_date: str  # Date — the day the cleaner arrives
    confirmed: str   # Checkbox — only confirmed visits affect generation


@dataclass(frozen=True)
class _AssignmentFields:
    # Airtable's first field is always the primary one, cannot be hidden or
    # moved, and is what the mobile app shows as each row's title. That is
    # the screen people tick chores off on, so it carries the chore name.
    # A number there would title every row of a week "1".
    label: str        # Single line text — the primary field
    week_number: str  # Number (integer)
    chore: str        # Linked record → Chores
    assignee: str     # Linked record → Roster
    task: str         # Long text — copied from Chore at generation time
    due: str          # Date + time (UTC)
    is_prep: str      # Checkbox — True if this is a cleaner-prep week
    done: str         # Checkbox — roommates toggle this to log completion
    # The nudge log. Two datetime fields rather than a separate table: an
    # assignment can only ever be nudged twice, so the log is bounded and
    # belongs on the row it is about.
    first_nudge_sent: str  # Date + time — when the first nudge went out
    followup_sent: str     # Date + time — when the follow-up went out


@dataclass(frozen=True)
class AirtableFieldConfig:
    table_roster: str
    table_chores: str
    table_rules: str
    table_cleaner_visits: str
    table_assignments: str
    roster: _RosterFields
    chores: _ChoreFields
    rules: _RuleFields
    cleaner_visits: _CleanerVisitFields
    assignments: _AssignmentFields


DEFAULT_FIELD_CONFIG = AirtableFieldConfig(
    table_roster="Roster",
    table_chores="Chores",
    table_rules="Rules",
    table_cleaner_visits="Cleaner Visits",
    table_assignments="Assignments",
    roster=_RosterFields(
        name="Name",
        email="Email",
        active="Active",
        sort_order="Sort order",
    ),
    chores=_ChoreFields(
        name="Name",
        cadence="Cadence",
        offset="Offset",
        seed="Seed",
        task="Task",
        cleaner_behaviour="Cleaner behaviour",
        prep_task="Prep task",
    ),
    rules=_RuleFields(
        text="Text",
        category="Category",
        number="Number",
        active_from="Active from",
        active_until="Active until",
    ),
    cleaner_visits=_CleanerVisitFields(
        visit_date="Visit date",
        confirmed="Confirmed",
    ),
    assignments=_AssignmentFields(
        label="Assignment",
        week_number="Week",
        chore="Chore",
        assignee="Assignee",
        task="Task",
        due="Due",
        is_prep="Prep",
        done="Done",
        first_nudge_sent="First nudge sent",
        followup_sent="Followup sent",
    ),
)
