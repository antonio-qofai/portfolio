import io
import urllib.error

import pytest

from connectors import ai_daily_brief as aib
from dashboard.config import load_config

EDITION = {
    "date": "2026-09-24",
    "canonicalUrl": "https://aidailybrief.ai/e/2026-09-24",
    "thesis": {"headline": "Agents move into the real world", "sub": "One. Two. " + "Long sentence. " * 30},
    "nuggets": [
        {"segment": "main", "type": "insight", "headline": "Main insight 1", "body": "b1"},
        {"segment": "headlines", "type": "insight", "headline": "Headline 1", "body": "h1"},
        {"segment": "main", "type": "quote", "headline": "A quote", "body": "q"},
        {"segment": "main", "type": "take", "headline": "A take", "body": "t"},
        {"segment": "headlines", "type": "quote", "headline": "Headline 2", "body": "h2"},
        {"segment": "main", "type": "insight", "headline": "Main insight 2", "body": "b2"},
        {"segment": "main", "type": "insight", "headline": "Main insight 3", "body": "b3"},
    ],
}


@pytest.fixture
def config():
    return load_config()


def test_thesis_then_headlines_then_insights(config):
    items = aib.pick_items(EDITION, 5)
    assert [i.title for i in items] == [
        "Agents move into the real world", "Headline 1", "Headline 2", "Main insight 1", "Main insight 2"]
    assert all(i.link == EDITION["canonicalUrl"] and i.timestamp == "2026-09-24" for i in items)
    assert "A quote" not in [i.title for i in items] and "A take" not in [i.title for i in items]


def test_cap_and_short_summaries(config):
    items = aib.pick_items(EDITION, 3)
    assert len(items) == 3
    assert len(items[0].summary) <= aib.SUMMARY_CHARS and items[0].summary.endswith(".")


def test_edition_without_thesis(config):
    items = aib.pick_items({**EDITION, "thesis": None}, 2)
    assert [i.title for i in items] == ["Headline 1", "Headline 2"]


def fake_get(pages):
    def get(url):
        page = pages[url]
        if isinstance(page, int):
            raise urllib.error.HTTPError(url, page, "err", {}, io.BytesIO())
        return page
    return get


def test_falls_back_to_the_feed_when_today_is_not_out(config, monkeypatch):
    monkeypatch.setattr(aib, "now", lambda _c: __import__("datetime").datetime(2026, 9, 28, 6))
    feed = {"editions": [{"date": "2026-09-25", "json": "https://x/25.json"},
                         {"date": "2026-09-27", "json": "https://x/27.json"}]}
    get = fake_get({aib.EDITION_URL.format(day="2026-09-28"): 404,
                    config["ai_daily_brief"]["feed_url"]: feed, "https://x/27.json": {"date": "2026-09-27"}})
    assert aib.latest_edition(config, get)["date"] == "2026-09-27"


def test_todays_edition_is_used_when_out(config, monkeypatch):
    monkeypatch.setattr(aib, "now", lambda _c: __import__("datetime").datetime(2026, 9, 28, 6))
    get = fake_get({aib.EDITION_URL.format(day="2026-09-28"): {"date": "2026-09-28"}})
    assert aib.latest_edition(config, get)["date"] == "2026-09-28"


def test_other_http_errors_fail_the_connector(config, monkeypatch):
    monkeypatch.setattr(aib, "now", lambda _c: __import__("datetime").datetime(2026, 9, 28, 6))
    with pytest.raises(urllib.error.HTTPError):
        aib.latest_edition(config, fake_get({aib.EDITION_URL.format(day="2026-09-28"): 500}))
