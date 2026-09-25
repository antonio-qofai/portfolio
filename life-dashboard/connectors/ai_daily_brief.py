"""AI Daily Brief from the site's agent feed (config ai_daily_brief.feed_url). Stub until M8."""

from __future__ import annotations

from connectors._time import at
from dashboard.schema import Item


def fetch(config: dict) -> list[Item]:
    return [
        Item(
            source="ai_daily_brief",
            title=f"Placeholder AI brief item {n + 1}",
            summary="Stub item (M0).",
            timestamp=at(config, 5),
        )
        for n in range(3)
    ]
