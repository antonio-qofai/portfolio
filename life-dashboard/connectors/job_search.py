"""Job search changes, parsed from the internship agent's report email. Stub until M7."""

from __future__ import annotations

from connectors._time import at, on
from dashboard.schema import Item


def fetch(config: dict) -> list[Item]:
    return [
        Item(
            source="job_search",
            title="Placeholder status change",
            summary="Stub job-search update (M0).",
            timestamp=at(config, 5),
        ),
        Item(
            source="job_search",
            title="Placeholder application deadline",
            summary="Stub job-search update (M0).",
            timestamp=at(config, 5),
            due=on(config, days=3),
            urgency_hints=["deadline"],
        ),
    ]
