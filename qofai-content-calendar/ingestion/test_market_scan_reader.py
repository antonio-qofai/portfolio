"""The market scan's two filter rules added on 2026-09-22.

Run it:  cd ingestion && ../.venv/bin/python test_market_scan_reader.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import market_scan_reader as m  # noqa: E402

PASSED: list = []
FAILED: list = []

RELEVANCE = {
    "mode": "any",
    "match_fields": ["title", "summary"],
    "terms": [" ai ", "private equity"],
    "exclude_fields": ["title"],
    "exclude_terms": ["share your"],
}


def check(name):
    def wrap(fn):
        try:
            fn()
            PASSED.append(f"ok  {name}")
        except AssertionError as exc:
            FAILED.append(f"FAIL  {name}: {exc}")
        return fn
    return wrap


@check("an excluded title is dropped even when it matches a term")
def _():
    hits = m.matched_terms({"title": "Share your private equity news", "summary": ""}, RELEVANCE)
    assert hits is None, hits


@check("an excluded title is dropped even with the filter off")
def _():
    off = {**RELEVANCE, "mode": "off"}
    assert m.matched_terms({"title": "Share Your Latest Transaction", "summary": ""}, off) is None


@check("with the filter off, an on-lens headline with no term still passes")
def _():
    off = {**RELEVANCE, "mode": "off"}
    assert m.matched_terms({"title": "Fort Point acquires Spencer Technologies", "summary": ""}, off) == []


@check("exclusions check the title, not the summary")
def _():
    hits = m.matched_terms(
        {"title": "AI in private equity", "summary": "share your thoughts below"}, RELEVANCE
    )
    assert hits, hits


@check("a feed marked relevance off keeps headlines that match no term, in both YAML spellings")
def _():
    feed = (b'<?xml version="1.0"?><rss><channel><item><title>Fort Point acquires Spencer'
            b'</title><pubDate>Tue, 22 Sep 2026 12:00:00 +0000</pubDate>'
            b'<link>https://x/1</link></item></channel></rss>')
    original = m.fetch_feed
    m.fetch_feed = lambda *a, **k: feed
    try:
        # A bare `off` in YAML arrives as False, which is the bug this pins.
        for value in (False, "off", None):
            into: list = []
            m._read_one_source(
                source={"id": "pe", "url": "https://x/feed", "relevance": value},
                cutoff=None, per_source_cap=5, summary_chars=200, keep_undated=True,
                relevance=RELEVANCE, timeout=1, user_agent="t", into=into,
            )
            expected = 0 if value is None else 1
            assert len(into) == expected, (value, into)
    finally:
        m.fetch_feed = original


if __name__ == "__main__":
    for line in PASSED:
        print(line)
    for line in FAILED:
        print(line, file=sys.stderr)
    print(f"\n{len(PASSED)}/{len(PASSED) + len(FAILED)} passed")
    sys.exit(1 if FAILED else 0)
