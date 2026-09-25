"""Chores from the chore agent's report file (agent report contract, PRD.md).

Reads agent-reports/chores.json. Until the chore agent writes that file (M4),
falls back to the committed agent-reports/chores.sample.json.
"""

from __future__ import annotations

import json

from dashboard.config import ROOT
from dashboard.schema import Item, parse_agent_report


def fetch(config: dict) -> list[Item]:
    reports = ROOT / config["agent_reports_dir"]
    path = reports / "chores.json"
    if not path.exists():
        path = reports / "chores.sample.json"
    with open(path) as f:
        return parse_agent_report(json.load(f), agent="chores")
