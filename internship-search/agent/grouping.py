"""Collapse one role posted across many cities into one item.

Requested 2026-08-12. This is a presentation layer and nothing else. Every city
keeps its own row in SQLite, its own requisition id, its own miss counter and its
own closure date, because a board that issues one id per city is issuing
genuinely separate postings. Merging them at the identity layer would reintroduce
exactly the defect CLAUDE.md rule 6 exists to prevent: an edit to one city's
listing would move the merged key, close the whole role, and rediscover it as new
with every label and override left behind.

So the rule is: group late, never store the grouping. Two callers group, the
digest and the Airtable push, and both hand a list of stored rows in and get a
list of groups out. Nothing here writes anything.

No company name, job title or city appears in this module. The grouping key is
the company name run through the same normalizer the aggregator feeds use, plus
the title reduced to its letters and digits. A city list would be data, not code,
and is not needed: the locations come off the rows themselves.
"""

import re
from dataclasses import dataclass, field

from .sources import normalize_company


def normalize_title(title: str) -> str:
    """Reduce a title to something two listings of one role can agree on.

    Case, punctuation and runs of whitespace only. Deliberately conservative:
    anything cleverer, such as stripping a trailing city or a requisition
    number, risks merging two genuinely different roles, and a wrong merge hides
    a posting the owner would have wanted while a missed merge only costs a line
    in an email. Same asymmetry the prefilter is built on.
    """
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def group_key(row) -> tuple[str, str]:
    """What makes two rows the same role. Company plus title, both normalized."""
    return (
        normalize_company(row.get("company") or ""),
        normalize_title(row.get("title")),
    )


def by_best(row) -> tuple:
    """Lead-row order for an email: the strongest member of the group leads.

    Lowest tier number first, then highest fit, then oldest. An unscored row
    sorts last, so a group holding one scored city and three unscored ones is
    presented with the score it actually has.
    """
    tier = row.get("tier")
    fit = row.get("fit_score")
    return (
        tier if isinstance(tier, int) and tier > 0 else 99,
        -(fit or 0),
        row.get("id") or 0,
    )


# Statuses that mean an application was actually sent. "skipped" and "missed"
# record a role he decided against, which is the opposite, and letting them win
# the lead put a skipped duplicate at the head of the American Express group on
# 2026-09-25 so his real application had no row. Matches `dashboard.SENT`.
SENT_STATUSES = ("applied", "interviewing", "rejected", "offer")


def by_oldest(row) -> tuple:
    """Lead-row order for Airtable: a row the owner labelled, else the oldest.

    Deliberately not `by_best`. Airtable rows are created and deleted, and a
    lead that changed when a score landed would delete one record and create
    another on the next sync, every time the ranker touched the group. Row ids
    only ever increase, so the oldest member is a lead that never moves while
    the group's membership holds. CLAUDE.md rule 9's churn trap.

    A label overrides age, and that is not a hole in the stability argument. The
    hidden rows are not in the base, so they cannot acquire a new label; the only
    ones that carry one are historical, from before the collapse existed. Without
    this the group would show a blank Label for a role he had already said yes
    to, which reads as the base having lost his answer. One such row existed on
    2026-08-16, a Palantir FDSE listing he marked interested in a city that was
    not the oldest.

    An application overrides age for the same reason and was added 2026-09-25.
    He applied to the New York American Express listing; the collapse led with
    the older Atlanta row, so the base showed that role as not applied and his
    Applied view could not find it. What he did to a posting outranks which city
    happened to be discovered first.

    Applied beats labelled beats age. Both exceptions are stable in practice
    because both are his own actions on a row already in the base, so neither
    flips back and forth on its own.
    """
    applied = 0 if str(row.get("applied_status") or "") in SENT_STATUSES else 1
    labelled = 0 if str(row.get("label") or "").strip() else 1
    return (applied, labelled, row.get("id") or 0)


@dataclass
class Group:
    """One role, and every row that is a copy of it in another city."""

    rows: list[dict]
    lead: dict
    locations: list[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.rows)

    @property
    def collapsed(self) -> int:
        """How many rows this group hides behind its lead."""
        return len(self.rows) - 1

    def get(self, key, default=None):
        """Read a field off the lead row, so a group formats like a posting."""
        return self.lead.get(key, default)

    def __getitem__(self, key):
        return self.lead[key]

    def hashes(self) -> list[str]:
        return [r["hash"] for r in self.rows if r.get("hash")]

    def location_text(self, joiner: str = "; ", max_shown: int = 0) -> str:
        """The group's locations as one field, truncated with a count."""
        if not self.locations:
            return ""
        if max_shown and len(self.locations) > max_shown:
            shown = self.locations[:max_shown]
            return joiner.join(shown) + f" +{len(self.locations) - max_shown} more"
        return joiner.join(self.locations)


def _locations(rows) -> list[str]:
    """Every distinct location in the group, in the order first seen.

    Deduplicated because a board can list the same city twice under two
    requisition ids, which is common enough that the raw join reads as a bug.
    """
    out: list[str] = []
    for r in rows:
        value = (r.get("location") or "").strip()
        if value and value not in out:
            out.append(value)
    return out


def collapse(rows, lead_key=by_best, enabled: bool = True) -> list[Group]:
    """Group rows into roles. Order of first appearance is preserved.

    With `enabled` false every row becomes its own group, so a caller can turn
    the collapse off from settings without growing a second code path.
    """
    buckets: dict[tuple, list[dict]] = {}
    order: list[tuple] = []
    for row in rows:
        key = group_key(row) if enabled else (id(row),)
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(row)

    groups = []
    for key in order:
        members = buckets[key]
        groups.append(
            Group(
                rows=members,
                lead=min(members, key=lead_key),
                locations=_locations(members),
            )
        )
    return groups
