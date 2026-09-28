"""Read an agent report file (agent report contract, PRD.md) for a connector.

A missing report, one older than `agent_report_max_age_hours`, or one with
`"status": "error"` is an error, so a card never shows old items as current;
the pipeline then falls back to the last good result with its age.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from connectors._time import now
from dashboard.config import ROOT
from dashboard.schema import Item, parse_agent_report


class ReportMissing(RuntimeError):
    pass


def read(config: dict, agent: str, missing_hint: str) -> list[Item]:
    path = ROOT / config["agent_reports_dir"] / f"{agent}.json"
    if not path.exists():
        raise ReportMissing(f"no {agent} report yet; {missing_hint}")
    with open(path) as f:
        data = json.load(f)
    items = parse_agent_report(data, agent=agent)
    age = now(config) - datetime.fromisoformat(data["generated_at"])
    if age > timedelta(hours=config["agent_report_max_age_hours"]):
        raise ReportMissing(f"{agent} report is stale ({int(age.total_seconds() // 3600)} hours old)")
    return items
