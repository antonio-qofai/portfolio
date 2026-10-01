"""Blocked publishes of the public portfolio, from the portfolio sync's status file.

~/portfolio/tools/sync.py runs after every commit to a published agent repo and
records each project's last result in a status file (`portfolio_status_file`).
A project whose last sync was blocked by the leak scan or the review, or failed,
becomes one item here, so it reaches Pressing actions instead of only a
notification. Published and current projects produce nothing.

There is no age limit: a blocked project stays blocked until a later sync of it
succeeds, however old the entry is. A missing file means no sync has run on this
Mac yet, which is not an error.
"""

from __future__ import annotations

import json
from pathlib import Path

from dashboard.schema import Item

BLOCKED_STATES = {"blocked", "error"}
STATUS_COMMAND = "uv run --script ~/portfolio/tools/sync.py --status"


def fetch(config: dict) -> list[Item]:
    path = Path(config["portfolio_status_file"]).expanduser()
    if not path.exists():
        return []
    status = json.loads(path.read_text())
    items = []
    for project, entry in sorted(status.items()):
        if entry.get("state") not in BLOCKED_STATES:
            continue
        findings = entry.get("findings") or []
        if entry["state"] == "blocked":
            reason = f"The review flagged {len(findings)} item(s) as private."
        else:
            first = (entry.get("message") or "").splitlines()[:1]
            reason = f"The sync failed: {first[0]}" if first else "The sync failed."
        items.append(Item(
            source="portfolio",
            title=f"Portfolio sync blocked: {project}",
            summary=f"{reason} Nothing new is public until it is resolved. Run `{STATUS_COMMAND}`.",
            timestamp=entry.get("at"),
            due=(entry.get("at") or "")[:10] or None,
            urgency_hints=["due_today"],
        ))
    return items
