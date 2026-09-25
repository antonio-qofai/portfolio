"""Every word a nudge can say.

Mirrors the pattern in config/digest_copy.py: no English in src/, wording
changes happen here. The date-formatting fields are intentionally identical
to DigestCopy so that src/digest.format_due can be reused without a
separate formatter.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class NudgeCopy:
    """Templates and fixed strings for nudge emails.

    first_subject and followup_subject take {name}.
    first_body and followup_body take {name}, {task}, {due}.
    The due_format, weekday_names, month_names, am, pm fields follow the same
    protocol as DigestCopy and are passed to src/digest.format_due.
    """

    first_subject: str
    first_body: str
    followup_subject: str
    followup_body: str
    due_format: str
    weekday_names: tuple
    month_names: tuple
    am: str
    pm: str


DEFAULT_NUDGE_COPY = NudgeCopy(
    first_subject="Reminder: {name}",
    first_body=(
        "Your chore is past due.\n"
        "\n"
        "{name}\n"
        "What it means: {task}\n"
        "Was due: {due}\n"
        "\n"
        "Mark it done in the app when you finish."
    ),
    followup_subject="Final reminder: {name}",
    followup_body=(
        "Still open — this is the last reminder.\n"
        "\n"
        "{name}\n"
        "What it means: {task}\n"
        "Was due: {due}\n"
        "\n"
        "Mark it done in the app when you finish."
    ),
    due_format="{weekday} {month} {day}, {hour}{minute}{meridiem}",
    weekday_names=("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
    month_names=(
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ),
    am="am",
    pm="pm",
)
