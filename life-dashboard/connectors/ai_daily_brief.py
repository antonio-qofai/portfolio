"""AI Daily Brief: 3 to 5 key items from the latest episode, via the site's agent feed.

The feed (`ai_daily_brief.feed_url`) lists editions newest first, each with a
JSON link. An edition has a `thesis` (the episode's one idea) and `nuggets`
(headline + short body, each tagged with a segment and a type). The feed can
lag the site by a day, so today's edition is tried first at /e/<today>.json.

Items, up to `ai_daily_brief.cap`, chosen by rule, no model call:
    1. the thesis
    2. nuggets in the "headlines" segment (the news roundup, on days it runs)
    3. "insight" nuggets from the main segment, in episode order
Quotes and takes are skipped. Everything links to the episode page.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import date

from connectors._time import now
from dashboard.schema import Item

EDITION_URL = "https://aidailybrief.ai/e/{day}.json"
SUMMARY_CHARS = 240


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "life-dashboard"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def _short(text: str, limit: int = SUMMARY_CHARS) -> str:
    """Whole sentences up to about `limit` characters, so a summary stays about two lines."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text.rfind(". ", 0, limit)
    return text[: cut + 1] if cut > limit // 3 else text[:limit].rsplit(" ", 1)[0] + "…"


def latest_edition(config: dict, get=_get) -> dict:
    """Today's edition if it's out, else the newest one the feed lists."""
    today = now(config).date().isoformat()
    try:
        return get(EDITION_URL.format(day=today))
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    feed = get(config["ai_daily_brief"]["feed_url"])
    editions = [e for e in feed.get("editions", []) if e.get("json")]
    if not editions:
        raise ValueError("AI Daily Brief feed lists no editions")
    newest = max(editions, key=lambda e: e.get("date", ""))
    return get(newest["json"])


def pick_items(edition: dict, cap: int) -> list[Item]:
    link = edition.get("canonicalUrl", "")
    day = edition.get("date") or edition.get("id")
    try:
        date.fromisoformat(day)
    except (TypeError, ValueError):
        day = None
    items = []
    thesis = edition.get("thesis") or {}
    if thesis.get("headline"):
        items.append(Item(source="ai_daily_brief", title=thesis["headline"], summary=_short(thesis.get("sub", "")),
                          link=link, timestamp=day))
    nuggets = edition.get("nuggets") or []
    ordered = [n for n in nuggets if n.get("segment") == "headlines"] + [
        n for n in nuggets if n.get("segment") != "headlines" and n.get("type") == "insight"
    ]
    for n in ordered:
        if len(items) >= cap:
            break
        if n.get("headline"):
            items.append(Item(source="ai_daily_brief", title=n["headline"], summary=_short(n.get("body", "")),
                              link=link, timestamp=day))
    return items[:cap]


def fetch(config: dict) -> list[Item]:
    return pick_items(latest_edition(config), config["ai_daily_brief"]["cap"])
