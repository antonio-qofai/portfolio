"""What the Briefing Insights read must not get wrong.

The table replaces the RSS scan as the arc's outside world, so the ways it
can fail quietly matter more than the happy path: an item the model scored
Low reaching the prompt, a stale insight passing as this week's news, or a
refused token arriving as "nothing happened this fortnight".

Run it:  cd ingestion && ../.venv/bin/python test_briefing_insights_reader.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import briefing_insights_reader as reader  # noqa: E402

PASSED: list = []
FAILED: list = []

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


def check(name):
    def wrap(fn):
        try:
            fn()
            PASSED.append(f"ok  {name}")
        except AssertionError as exc:
            FAILED.append(f"FAIL  {name}: {exc}")
        return fn
    return wrap


class _Response:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}
        self.text = str(payload)

    def json(self):
        return self._payload


def _record(rid, created, relevance="High", notes="Article: A headline", **extra):
    fields = {
        "Theme": "Agents in operations",
        "Insight Summary": "Mid-market firms are moving agents into back office work.",
        "Source Name": "AI Daily Brief",
        "Middle Market Relevance": relevance,
        "Industry Vertical": "Manufacturing",
        "Notes": notes,
    }
    fields.update(extra)
    return {"id": rid, "createdTime": created, "fields": fields}


def _read(pages, **kwargs):
    calls = []

    def get(url, headers=None, params=None, timeout=None):
        calls.append(params)
        return pages[len(calls) - 1]

    items, why = reader.read_briefing_insights(
        "appTEST", "Briefing Insights", token="t", now=NOW, get=get, **kwargs
    )
    return items, why, calls


@check("an insight becomes a market item the prompt already knows how to show")
def _():
    items, why, _ = _read([_Response(200, {"records": [_record("r1", "2026-09-21T13:30:00.000Z")]})])
    assert why == "", why
    assert len(items) == 1, items
    item = items[0]
    assert item.title == "A headline", item.title
    assert "via Value Creation Briefing" in item.source_name, item.source_name
    assert "relevance: High" in item.summary, item.summary
    assert item.published_at == "2026-09-21T13:30:00.000Z"


@check("only the relevance levels asked for reach the arc")
def _():
    records = [
        _record("hi", "2026-09-21T00:00:00.000Z", relevance="High"),
        _record("lo", "2026-09-21T00:00:00.000Z", relevance="Low"),
    ]
    items, _, _ = _read([_Response(200, {"records": records})], relevance=["High", "Medium"])
    assert [i.item_id.split("/")[-1] for i in items] == ["hi"], items


@check("an insight older than the window is not this fortnight's news")
def _():
    records = [
        _record("new", "2026-09-20T00:00:00.000Z"),
        _record("old", "2026-08-01T00:00:00.000Z"),
    ]
    items, _, _ = _read([_Response(200, {"records": records})], lookback_days=14)
    assert [i.item_id.split("/")[-1] for i in items] == ["new"], items


@check("newest first, capped, across every page")
def _():
    page1 = _Response(200, {"records": [_record("a", "2026-09-18T00:00:00.000Z")], "offset": "next"})
    page2 = _Response(200, {"records": [_record("b", "2026-09-21T00:00:00.000Z"),
                                        _record("c", "2026-09-20T00:00:00.000Z")]})
    items, _, calls = _read([page1, page2], max_items=2)
    assert len(calls) == 2 and calls[1].get("offset") == "next", calls
    assert [i.item_id.split("/")[-1] for i in items] == ["b", "c"], items


@check("a title missing from Notes falls back to the theme rather than to nothing")
def _():
    items, _, _ = _read([_Response(200, {"records": [
        _record("r", "2026-09-21T00:00:00.000Z", notes="free text")
    ]})])
    assert items[0].title == "Agents in operations", items[0].title


@check("a refused token says it was refused, not that nothing happened")
def _():
    items, why, _ = _read([_Response(403, {"error": "INVALID_PERMISSIONS"})])
    assert items == [] and "refused the token" in why, why


@check("an empty fortnight says so in its own words")
def _():
    items, why, _ = _read([_Response(200, {"records": []})])
    assert items == [] and "no insights" in why, why


@check("no token is a reason, not an exception")
def _():
    saved = {v: reader.os.environ.pop(v, None) for v in reader._TOKEN_ENV_VARS}
    try:
        items, why = reader.read_briefing_insights("appTEST", "Briefing Insights", now=NOW)
    finally:
        for var, value in saved.items():
            if value is not None:
                reader.os.environ[var] = value
    assert items == [] and "no Airtable token" in why, why


if __name__ == "__main__":
    for line in PASSED:
        print(line)
    for line in FAILED:
        print(line, file=sys.stderr)
    print(f"\n{len(PASSED)}/{len(PASSED) + len(FAILED)} passed")
    sys.exit(1 if FAILED else 0)
