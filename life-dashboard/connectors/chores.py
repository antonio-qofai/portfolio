"""Chores from the chore agent's report file (agent report contract, PRD.md).

Reads agent-reports/chores.json, written each morning on this Mac by the
chore agent's exporter (~/agents/chores, `src/report.py`, run by its own
launchd job at 05:45). A missing or stale file is an error, so the card never
shows old chores as current; the pipeline then falls back to the last good
result with its age.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from connectors._time import now
from dashboard.config import ROOT
from dashboard.schema import Item, parse_agent_report


class ReportMissing(RuntimeError):
    pass


def fetch(config: dict) -> list[Item]:
    path = ROOT / config["agent_reports_dir"] / "chores.json"
    if not path.exists():
        raise ReportMissing("no chores report yet; install the chore agent's report job (see README)")
    with open(path) as f:
        data = json.load(f)
    items = parse_agent_report(data, agent="chores")
    age = now(config) - datetime.fromisoformat(data["generated_at"])
    if age > timedelta(hours=config["agent_report_max_age_hours"]):
        raise ReportMissing(f"chores report is stale ({int(age.total_seconds() // 3600)} hours old)")
    return items
