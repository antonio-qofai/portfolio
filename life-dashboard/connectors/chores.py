"""Chores from the chore agent's report file (agent report contract, PRD.md).

Reads agent-reports/chores.json, written each morning on this Mac by the
chore agent's exporter (~/agents/chores, `src/report.py`, run by its own
launchd job at 05:45). Missing, stale or error reports are errors (see
connectors/_report.py).
"""

from __future__ import annotations

from connectors._report import ReportMissing, read
from dashboard.config import ROOT
from dashboard.schema import Item

__all__ = ["ROOT", "ReportMissing", "fetch"]


def fetch(config: dict) -> list[Item]:
    return read(config, "chores", "install the chore agent's report job (see README)")
