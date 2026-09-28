import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from connectors import _report, chores, job_search
from dashboard.config import load_config
from dashboard.schema import AgentReportError
from dashboard.store import item_key

TZ = ZoneInfo("America/Chicago")
OFFER = {"title": "Offer: Acme, Intern", "summary": "You have an offer to answer.", "due": None,
         "urgency": "reply_needed", "link": "https://example.com/1"}


@pytest.fixture
def config(tmp_path):
    return {**load_config(), "agent_reports_dir": str(tmp_path)}


def write(config, agent="internship", hours_old=1, status="ok", items=(OFFER,)):
    generated = (datetime.now(TZ) - timedelta(hours=hours_old)).isoformat()
    (_report.ROOT / config["agent_reports_dir"] / f"{agent}.json").write_text(
        json.dumps({"generated_at": generated, "status": status, "items": list(items)}))


def test_reads_the_internship_report_as_job_search(config):
    write(config)
    [item] = job_search.fetch(config)
    assert item.source == "job_search" and item.title == "Offer: Acme, Intern"
    assert item.urgency_hints == ["reply_needed"] and item.section == "personal"


def test_missing_stale_and_error_reports_are_errors(config):
    with pytest.raises(_report.ReportMissing, match="no internship report"):
        job_search.fetch(config)
    write(config, hours_old=40)
    with pytest.raises(_report.ReportMissing, match="stale"):
        job_search.fetch(config)
    write(config, status="error")
    with pytest.raises(AgentReportError):
        job_search.fetch(config)


def test_check_off_survives_a_new_export(config):
    """The key must not change when the agent rewrites its report with the same item."""
    write(config, agent="chores", hours_old=20, items=[{**OFFER, "title": "Dishwasher duty", "urgency": None}])
    first = item_key(chores.fetch(config)[0])
    write(config, agent="chores", hours_old=1, items=[{**OFFER, "title": "Dishwasher duty", "urgency": None}])
    assert item_key(chores.fetch(config)[0]) == first
