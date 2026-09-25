"""House rule categories.

Rules are never assigned and never completed. They are standing conditions
that appear verbatim in every digest, grouped by category.

Same registry pattern as cadences and cleaner behaviours: the digest never
compares against one of these names, it reads the heading and sort order off
whichever category it was handed.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class RuleCategory:
    """One group of house rules, and how it is introduced in the digest.

    order controls which group comes first. Override rules lead because they
    are the ones that cut across whose week it is.
    """

    name: str
    heading: str
    order: int


RULE_CATEGORIES = {
    category.name: category
    for category in (
        RuleCategory(
            name="override",
            heading="Override rules. Whoever hits the condition handles it.",
            order=0,
        ),
        RuleCategory(name="standing", heading="Standing rules.", order=1),
    )
}
