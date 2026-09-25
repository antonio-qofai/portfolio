"""NYT Top Stories for the configured sections. Stub until M8 (needs NYT_API_KEY)."""

from __future__ import annotations

from connectors._time import at
from dashboard.schema import Item


def fetch(config: dict) -> list[Item]:
    sections = config["news"]["sections"]
    return [
        Item(
            source="nyt",
            title=f"Placeholder {sections[n % len(sections)]} story {n + 1}",
            summary="Stub headline summary (M0), two lines max.",
            timestamp=at(config, 5),
        )
        for n in range(7)  # more than the cap on purpose; the pipeline enforces it
    ]
