"""Cadence definitions.

A cadence is a name plus how many active weeks pass between occurrences.
The engine never knows what a cadence is called; it only does arithmetic on
the interval it is handed, so adding one is an edit here and nothing else.

There is no list of forbidden cadences. Evenness is checked arithmetically at
generation time: a cadence is usable in a given term only if it produces a
number of occurrences divisible by the roster size. Over 9 active weeks with
3 people, an interval of 2 yields 5 occurrences and is rejected on those
grounds rather than by name.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Cadence:
    """How often a chore occurs, counted in active weeks.

    interval of 1 means every active week, 3 means every third active week.
    A chore's offset selects which slot within the interval it lands on and
    must be less than the interval.
    """

    name: str
    interval: int


CADENCES = {
    cadence.name: cadence
    for cadence in (
        Cadence(name="weekly", interval=1),
        Cadence(name="every_3", interval=3),
    )
}
