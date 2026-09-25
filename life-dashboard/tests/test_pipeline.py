import json

import pytest

from connectors import REGISTRY
from dashboard.config import load_config
from dashboard.pipeline import build_brief, save_brief
from dashboard.render import CARD_ORDER, render, write_page
from dashboard.schema import AgentReportError, Item, parse_agent_report


def fake_weather(_config):
    return [Item(source="weather", title="60°F, overcast", summary="High 63 / low 57.", timestamp="2026-09-25T06:00")]


def fake_calendar(_config):
    return [
        Item(source="calendar.uchicago", title="CMSC 14100", timestamp="2026-09-25T09:30:00-05:00"),
        Item(source="calendar.canvas", title="PSet 1 due", timestamp="2026-09-25T23:59:00-05:00",
             due="2026-09-25T23:59:00-05:00", urgency_hints=["due_today"]),
        Item(source="calendar.qofai", title="QofAI standup", timestamp="2026-09-25T15:00:00-05:00", section="qofai"),
    ]


def fake_email(_config):
    return [
        Item(source="email.personal", title="Coffee Friday?", summary="Jane Doe: Are you free?",
             timestamp="2026-09-24T18:00:00-05:00", urgency_hints=["reply_needed"]),
        Item(source="email.personal", title="Weekly deals", summary="Shop: 20% off",
             timestamp="2026-09-24T09:00:00-05:00"),
    ]


@pytest.fixture
def config():
    return load_config()


@pytest.fixture
def registry():
    """Real stubs, fake network connectors: tests never hit the network."""
    return {**REGISTRY, "weather": fake_weather, "calendar": fake_calendar, "email": fake_email}


@pytest.fixture
def cache(tmp_path):
    return tmp_path / "cache"


def test_stub_pipeline_end_to_end(config, registry, cache, tmp_path):
    brief = build_brief(config, registry, cache)
    assert all(r.error is None for r in brief["results"].values())
    assert all(isinstance(i, Item) for r in brief["results"].values() for i in r.items)

    save_brief(brief, tmp_path / "brief.json")
    saved = json.loads((tmp_path / "brief.json").read_text())
    assert set(saved["results"]) == set(REGISTRY)

    page = write_page(render(brief, config), tmp_path / "index.html").read_text()
    positions = [page.index(f'id="{card}"') for card in CARD_ORDER]
    assert positions == sorted(positions), "cards out of PRD layout order"
    assert "failed to load" not in page
    assert "60°F, overcast" in page
    assert "First up: 9:30 AM CMSC 14100" in page
    inbox = page[page.index('id="inbox"'):page.index('id="qofai"')]
    assert "Coffee Friday?" in inbox and "Everything else (1)" in inbox


def test_failing_connector_shows_error_card(config, registry, cache):
    def broken(_config):
        raise RuntimeError("boom")

    brief = build_brief(config, {**registry, "weather": broken}, cache)
    assert brief["results"]["weather"].error == "RuntimeError: boom"
    assert not brief["results"]["weather"].stale

    page = render(brief, config)
    assert "weather failed to load" in page
    assert all(f'id="{card}"' in page for card in CARD_ORDER)


def test_failure_falls_back_to_last_good_result(config, registry, cache):
    build_brief(config, registry, cache)  # populates the cache

    def broken(_config):
        raise TimeoutError("timed out")

    brief = build_brief(config, {**registry, "weather": broken}, cache)
    weather = brief["results"]["weather"]
    assert weather.stale and weather.error == "TimeoutError: timed out"
    assert weather.items[0].title == "60°F, overcast"

    page = render(brief, config)
    assert "60°F, overcast" in page
    assert "First up: 9:30 AM CMSC 14100" in page
    assert "Showing last good result from" in page


def test_caps_and_qofai_kept_out_of_actions(config, registry, cache):
    brief = build_brief(config, registry, cache)
    assert len(brief["actions"]) <= config["actions"]["cap"]
    assert brief["actions"], "stub data should produce some placeholder actions"
    assert all(a.section == "personal" for a in brief["actions"])

    page = render(brief, config)
    reading = page[page.index('id="reading"'):]
    assert reading.count("Placeholder") <= config["news"]["cap"] + config["ai_daily_brief"]["cap"]
    assert "story 6" not in reading  # NYT stub returns 7; cap is 5


def test_agent_report_contract():
    good = {
        "generated_at": "2026-09-25T06:00:00-05:00",
        "status": "ok",
        "items": [{"title": "t", "summary": "s", "due": None, "urgency": "overdue", "link": ""}],
    }
    [item] = parse_agent_report(good, agent="chores")
    assert item.source == "chores" and item.urgency_hints == ["overdue"]

    with pytest.raises(AgentReportError):
        parse_agent_report({"status": "ok", "items": []}, agent="chores")
    with pytest.raises(AgentReportError):
        parse_agent_report({**good, "status": "error"}, agent="chores")
    with pytest.raises(AgentReportError):
        parse_agent_report({**good, "items": [{"title": "t"}]}, agent="chores")


def test_source_text_is_escaped(config, registry, cache):
    def hostile(_config):
        return [Item(source="job_search", title="<script>alert(1)</script>", link="javascript:alert(1)")]

    brief = build_brief(config, {**registry, "job_search": hostile}, cache)
    page = render(brief, config)
    assert "<script>alert(1)" not in page
    assert "javascript:" not in page
