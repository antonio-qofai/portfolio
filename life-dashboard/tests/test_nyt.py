import urllib.error
from datetime import datetime

import pytest

from connectors import nyt
from dashboard.config import load_config

AS_OF = datetime.fromisoformat("2026-09-29T12:00:00-05:00")


def story(title, tags=(), orgs=(), published="2026-09-29T08:00:00-04:00", item_type="Article", url=None):
    return {
        "title": title, "abstract": f"About {title}.", "item_type": item_type, "published_date": published,
        "url": url or f"https://www.nytimes.com/2026/09/29/{title.replace(' ', '-').lower()}.html",
        "des_facet": list(tags), "org_facet": list(orgs),
    }


@pytest.fixture
def config():
    return load_config()


def feeds(home=(), business=(), upshot=()):
    return {"home": {"results": list(home)}, "business": {"results": list(business)}, "upshot": {"results": list(upshot)}}


def test_class_stories_first_then_headlines(config):
    fed = story("Fed cuts rates", ["Interest Rates", "Federal Reserve System"])
    tariff = story("New tariffs on steel", ["Customs (Tariff)"], published="2026-09-29T09:00:00-04:00")
    items = nyt.pick_items(
        feeds(home=[story(f"Headline {n}") for n in range(1, 6)] + [fed], business=[tariff]), config, AS_OF)
    assert [i.title for i in items] == ["Fed cuts rates", "New tariffs on steel", "Headline 1", "Headline 2", "Headline 3"]
    assert [i.source for i in items[:3]] == ["nyt.money_banking", "nyt.econ_policy", "nyt.top"]
    assert items[0].summary == "About Fed cuts rates." and items[0].link.startswith("https://www.nytimes.com/")


def test_courses_take_turns_and_respect_the_cap(config):
    trade = [story(f"Trade {n}", ["Customs (Tariff)"], published=f"2026-09-29T0{n}:00:00-04:00") for n in range(1, 6)]
    bank = story("Bank earnings", ["Banking and Financial Institutions"])
    items = nyt.pick_items(feeds(business=trade + [bank]), config, AS_OF)
    classes = [i for i in items if i.source != "nyt.top"]
    assert len(classes) == config["news"]["courses"]["cap"]
    assert [i.title for i in classes] == ["Bank earnings", "Trade 5", "Trade 4"]  # alternate, newest first
    assert len(items) <= config["news"]["cap"]


def test_only_leading_tags_count(config):
    """A passing mention deep in the tag list doesn't make a story course material."""
    births = story("Homeownership and birthrates", ["Birth Rates", "Research", "Baby Boomers", "Mortgages"])
    items = nyt.pick_items(feeds(home=[births]), config, AS_OF)
    assert [i.source for i in items] == ["nyt.top"]


def test_old_stories_and_non_articles_skip_the_class_list(config):
    old = story("Old Fed story", ["Federal Reserve System"], published="2026-09-20T08:00:00-04:00")
    quiz = story("Econ quiz", ["Inflation (Economics)"], item_type="Interactive")
    items = nyt.pick_items(feeds(upshot=[old, quiz]), config, AS_OF)
    assert items == []


def test_story_shows_once_across_sections(config):
    fed = story("Fed cuts rates", ["Federal Reserve System"])
    items = nyt.pick_items(feeds(home=[fed], business=[fed]), config, AS_OF)
    assert [i.title for i in items] == ["Fed cuts rates"]


def test_fetch_needs_a_key_and_passes_http_errors(config, monkeypatch):
    monkeypatch.setattr(nyt, "load_env", lambda: None)
    monkeypatch.delenv("NYT_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="NYT_API_KEY"):
        nyt.fetch(config, get=lambda s, k: {})

    monkeypatch.setenv("NYT_API_KEY", "test-key")
    def limited(section, key):
        raise urllib.error.HTTPError("https://api.nytimes.com", 429, "Too Many Requests", {}, None)
    with pytest.raises(urllib.error.HTTPError):
        nyt.fetch(config, get=limited)

    calls = []
    nyt.fetch(config, get=lambda s, k: calls.append((s, k)) or {"results": []})
    assert calls == [("home", "test-key"), ("business", "test-key"), ("upshot", "test-key")]
