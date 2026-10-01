import json

import pytest

from connectors import portfolio
from dashboard.config import load_config


@pytest.fixture
def config(tmp_path):
    return {**load_config(), "portfolio_status_file": str(tmp_path / "status.json")}


def write(config, status):
    with open(config["portfolio_status_file"], "w") as f:
        json.dump(status, f)


def test_missing_status_file_is_not_an_error(config):
    assert portfolio.fetch(config) == []


def test_published_and_current_projects_produce_nothing(config):
    write(config, {
        "chores": {"state": "published", "at": "2026-09-30T19:03:21-05:00", "sha": "53edf54"},
        "notes": {"state": "current", "at": "2026-09-30T19:03:21-05:00", "sha": "1a2b3c4"},
    })
    assert portfolio.fetch(config) == []


def test_blocked_review_becomes_a_pressing_candidate(config):
    write(config, {"chores": {
        "state": "blocked", "at": "2026-09-30T19:03:21-05:00", "message": "review flagged 2 item(s)",
        "findings": [{"file": "a", "excerpt": "x", "category": "person", "reason": "r"}] * 2,
    }})
    [item] = portfolio.fetch(config)
    assert item.source == "portfolio" and item.title == "Portfolio sync blocked: chores"
    assert "2 item(s)" in item.summary and "--status" in item.summary
    assert item.urgency_hints == ["due_today"] and item.due == "2026-09-30"
    assert item.section == "personal"


def test_failed_sync_shows_the_first_line_of_the_error(config):
    write(config, {"chores": {
        "state": "error", "at": "2026-09-30T19:03:21-05:00",
        "message": "chores: leak scan found 1 issue(s), nothing published\n  config.yaml:59  ...",
    }})
    [item] = portfolio.fetch(config)
    assert "leak scan found 1 issue(s)" in item.summary and "config.yaml" not in item.summary


def test_portfolio_items_are_ranking_candidates():
    from dashboard.actions import CANDIDATE_CONNECTORS
    from connectors import REGISTRY

    assert "portfolio" in REGISTRY and "portfolio" in CANDIDATE_CONNECTORS
