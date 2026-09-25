"""Shared data shapes.

Every connector returns a list of `Item`. Agent report files follow the
contract in PRD.md ("Agent report contract") and are parsed by
`parse_agent_report`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Section = Literal["personal", "qofai"]
SECTIONS: tuple[str, ...] = ("personal", "qofai")


@dataclass
class Item:
    """One normalized thing to show on the page.

    source         connector id, optionally with a sub-source, e.g. "gmail.uchicago"
    title          one line
    summary        a sentence or two
    link           URL back to the source ("" if none)
    timestamp      ISO 8601 time the item happened or starts (email sent, event start)
    due            ISO 8601 date or datetime when action is due, or None
    urgency_hints  plain flags for ranking, e.g. ["reply_needed", "overdue", "due_today"]
    section        "personal" or "qofai"; qofai items never enter Pressing actions
    """

    source: str
    title: str
    summary: str = ""
    link: str = ""
    timestamp: str | None = None
    due: str | None = None
    urgency_hints: list[str] = field(default_factory=list)
    section: Section = "personal"

    def __post_init__(self) -> None:
        if self.section not in SECTIONS:
            raise ValueError(f"section must be one of {SECTIONS}, got {self.section!r}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ConnectorResult:
    """What the pipeline stores per connector: items or an error, plus freshness.

    When a fetch fails but a cached result exists, `items` holds the cached
    items, `last_updated` is when they were fetched, and `stale` is True.
    """

    name: str
    items: list[Item] = field(default_factory=list)
    last_updated: str | None = None
    error: str | None = None
    stale: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "items": [i.to_dict() for i in self.items],
            "last_updated": self.last_updated,
            "error": self.error,
            "stale": self.stale,
        }


# --- Agent report contract -------------------------------------------------
# agent-reports/<agent>.json:
# {
#   "generated_at": ISO 8601,
#   "status": "ok" | "error",
#   "items": [{"title", "summary", "due", "urgency", "link"}, ...]
# }

REPORT_KEYS = ("generated_at", "status", "items")
REPORT_ITEM_KEYS = ("title", "summary", "due", "urgency", "link")
REPORT_STATUSES = ("ok", "error")


class AgentReportError(ValueError):
    pass


def parse_agent_report(data: dict[str, Any], agent: str, section: Section = "personal") -> list[Item]:
    """Validate an agent report dict and convert its items to `Item`s."""
    missing = [k for k in REPORT_KEYS if k not in data]
    if missing:
        raise AgentReportError(f"{agent} report missing keys: {missing}")
    if data["status"] not in REPORT_STATUSES:
        raise AgentReportError(f"{agent} report has unknown status {data['status']!r}")
    if data["status"] == "error":
        raise AgentReportError(f"{agent} agent reported status=error")
    if not isinstance(data["items"], list):
        raise AgentReportError(f"{agent} report 'items' must be a list")

    items = []
    for n, raw in enumerate(data["items"]):
        missing = [k for k in REPORT_ITEM_KEYS if k not in raw]
        if missing:
            raise AgentReportError(f"{agent} report item {n} missing keys: {missing}")
        items.append(
            Item(
                source=agent,
                title=str(raw["title"]),
                summary=str(raw["summary"] or ""),
                link=str(raw["link"] or ""),
                timestamp=data["generated_at"],
                due=raw["due"],
                urgency_hints=[raw["urgency"]] if raw["urgency"] else [],
                section=section,
            )
        )
    return items
