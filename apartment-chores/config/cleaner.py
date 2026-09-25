"""Cleaner behaviour definitions.

What a chore does in a confirmed cleaner week. Same pattern as the cadence
registry: the engine never compares against one of these names, it reads the
`converts` flag off whichever behaviour it was handed, so adding a third
behaviour is an edit here and nothing else.

Cancelling a chore is deliberately not one of the options. Cancelling removes
an occurrence and breaks the quarter arithmetic; conversion preserves the
count and lightens the work.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CleanerBehaviour:
    """How a chore responds to a confirmed cleaner visit in its week.

    converts False means the chore is untouched: the cleaner does not do that
    work, so it still has to be done as normal.

    converts True means the chore keeps its assignee, its week, and its place
    in the quarter totals, but swaps its task text for the chore's prep task
    and moves its due time to before the visit.
    """

    name: str
    converts: bool


CLEANER_BEHAVIOURS = {
    behaviour.name: behaviour
    for behaviour in (
        CleanerBehaviour(name="normal", converts=False),
        CleanerBehaviour(name="convert_to_prep", converts=True),
    )
}
