"""Job search from the internship agent's report file (agent report contract, PRD.md).

Reads agent-reports/internship.json, written by the internship agent
(~/agents/internship-search, `tools/report.py`) as the last step of each of its
scheduled runs (08:10, 12:10, 18:10, 22:10): offers, interviews and events this
week, deadlines on roles marked interested, Applied status moves, and silent
applications. Missing, stale or error reports are errors (see connectors/_report.py).
"""

from __future__ import annotations

from connectors._report import read
from dashboard.schema import Item


def fetch(config: dict) -> list[Item]:
    items = read(config, "internship", "the internship agent writes it on its next scheduled run")
    return [Item(**{**i.to_dict(), "source": "job_search"}) for i in items]
