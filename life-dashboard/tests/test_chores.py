import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from connectors import chores
from dashboard.config import load_config
from dashboard.schema import AgentReportError

TZ = ZoneInfo("America/Chicago")


@pytest.fixture
def config(tmp_path):
    return {**load_config(), "agent_reports_dir": str(tmp_path)}


def write(config, hours_old=1, status="ok", items=None):
    generated = (datetime.now(TZ) - timedelta(hours=hours_old)).isoformat()
    items = items if items is not None else [
        {"title": "Kitchen reset", "summary": "Wipe counters", "due": "2026-10-04T20:00:00-05:00",
         "urgency": "overdue", "link": ""},
    ]
    (chores.ROOT / config["agent_reports_dir"] / "chores.json").write_text(
        json.dumps({"generated_at": generated, "status": status, "items": items}))


def test_reads_fresh_report(config):
    write(config)
    [item] = chores.fetch(config)
    assert item.source == "chores" and item.title == "Kitchen reset"
    assert item.urgency_hints == ["overdue"] and item.section == "personal"


def test_missing_report_is_an_error_not_sample_data(config):
    (chores.ROOT / config["agent_reports_dir"] / "chores.sample.json").write_text("{}")
    with pytest.raises(chores.ReportMissing, match="no chores report"):
        chores.fetch(config)


def test_stale_report_is_an_error(config):
    write(config, hours_old=config["agent_report_max_age_hours"] + 1)
    with pytest.raises(chores.ReportMissing, match="stale"):
        chores.fetch(config)


def test_agent_error_status_is_an_error(config):
    write(config, status="error", items=[])
    with pytest.raises(AgentReportError):
        chores.fetch(config)


def test_empty_report_is_fine(config):
    write(config, items=[])
    assert chores.fetch(config) == []
