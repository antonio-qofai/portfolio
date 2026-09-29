"""NYT Top Stories: today's headlines plus stories for my two macro classes.

One Top Stories call for `home` plus one per section in `news.sections`
(under the API's 5-a-minute limit). Items, up to `news.cap` in all, chosen by rule, no model call:

    1. course stories, up to `news.courses.cap`: stories from any section,
       published in the last `news.courses.max_age_hours`, whose NYT topic tags
       (the first 3 of des_facet and org_facet) match a course's topics; courses take turns,
       newest first.
       Their source is `nyt.<course id>`, so the page can label them.
    2. headlines, in the home section's order (source `nyt.top`), to fill the cap.

A story shows once, in the first group that takes it. Summaries are the NYT abstract.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from connectors._time import now
from dashboard.config import load_env
from dashboard.schema import Item

API_URL = "https://api.nytimes.com/svc/topstories/v2/{section}.json?"
TAG_DEPTH = 3


def _get(section: str, key: str) -> dict:
    url = API_URL.format(section=section) + urllib.parse.urlencode({"api-key": key})
    with urllib.request.urlopen(url, timeout=20) as resp:
        return json.load(resp)


def _usable(story: dict) -> bool:
    return bool(story.get("title") and story.get("url", "").startswith("https://") and story.get("item_type") == "Article")


def _item(story: dict, sub: str) -> Item:
    return Item(
        source=f"nyt.{sub}",
        title=story["title"].strip(),
        summary=" ".join((story.get("abstract") or "").split()),
        link=story["url"],
        timestamp=story.get("published_date") or None,
    )


def _sections(config: dict) -> list[str]:
    return list(dict.fromkeys(["home", *config["news"]["sections"]]))


def course_for(story: dict, courses: list[dict]) -> str | None:
    """The first course whose topics include one of the story's leading tags.

    NYT lists tags roughly by relevance; the tail holds passing mentions (a
    birthrate story tagged "Mortgages"), so only the first TAG_DEPTH count.
    """
    def lead(tags: list[str]) -> list[str]:
        return [t for t in tags if not t.startswith("internal-")][:TAG_DEPTH]
    tags = {t.lower() for t in lead(story.get("des_facet", [])) + lead(story.get("org_facet", []))}
    for course in courses:
        if tags & {t.lower() for t in course["topics"]}:
            return course["id"]
    return None


def _take_turns(matched: list, course_ids: list[str], cap: int) -> list:
    """Newest story per course in turn, so one busy topic (tariffs, say) doesn't crowd out the other class."""
    queues = {c: [m for m in matched if m[1] == c] for c in course_ids}
    picked = []
    while len(picked) < cap and any(queues.values()):
        for c in course_ids:
            if queues[c] and len(picked) < cap:
                picked.append(queues[c].pop(0))
    return picked


def pick_items(feeds: dict[str, dict], config: dict, as_of: datetime) -> list[Item]:
    news = config["news"]
    courses = news.get("courses", {})
    cutoff = as_of - timedelta(hours=courses.get("max_age_hours", 48))

    stories, seen = [], set()
    for section in _sections(config):
        for story in feeds.get(section, {}).get("results", []):
            if _usable(story) and story["url"] not in seen:
                seen.add(story["url"])
                stories.append(story)

    matched = []
    for story in stories:
        course = course_for(story, courses.get("list", []))
        published = story.get("published_date")
        if course and published and datetime.fromisoformat(published) >= cutoff:
            matched.append((datetime.fromisoformat(published), course, story))
    matched.sort(key=lambda m: m[0], reverse=True)
    picked = _take_turns(matched, [c["id"] for c in courses.get("list", [])], min(courses.get("cap", 0), news["cap"]))

    items = [_item(story, course) for _, course, story in picked]
    taken = {story["url"] for _, _, story in picked}
    home = [s for s in feeds.get("home", {}).get("results", []) if _usable(s) and s["url"] not in taken]
    items += [_item(s, "top") for s in home[: news["cap"] - len(items)]]
    return items


def fetch(config: dict, get=_get) -> list[Item]:
    load_env()
    key = os.environ.get("NYT_API_KEY", "").strip()
    if not key:
        raise RuntimeError("NYT_API_KEY is not set in .env")
    feeds = {s: get(s, key) for s in _sections(config)}
    return pick_items(feeds, config, now(config))
