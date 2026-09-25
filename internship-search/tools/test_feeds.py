"""Reading an aggregator feed, including ones outside the Simplify lineage.

    .venv/bin/python -m tools.test_feeds

The two original feeds are forks of one another and share a schema, so
`agent/feeds.py` read their field names directly for its whole life. The third
feed, added 2026-09-22, sweeps ATS endpoints itself and names the same things
differently, which is exactly why it carries postings the other two do not.

The field map that makes it readable lives in `sources/feeds.toml`, per
CLAUDE.md rule 2. These cases pin both halves: that a mapped feed reads
correctly, and that an unmapped one still reads exactly as it always did.

The last group is the one that matters most. Under CLAUDE.md rule 13 a source
that cannot be read must RAISE, because returning an empty list is read by
`db.age_missing` as every posting from that source having closed. A feed whose
response changes shape has to fail loudly, not quietly report nothing.
"""

from __future__ import annotations

import sys

from agent import feeds
from agent.fetchers import SourceError
from agent.sources import Feed

FAILED: list[str] = []
PASSED = 0


def check(label, got, want):
    global PASSED
    if got == want:
        PASSED += 1
        print(f"ok   {label}")
    else:
        FAILED.append(f"{label}: expected {want!r}, got {got!r}")
        print(f"FAIL {label}: expected {want!r}, got {got!r}")


def raises(label, fn):
    global PASSED
    try:
        fn()
    except SourceError:
        PASSED += 1
        print(f"ok   {label}")
        return
    except Exception as exc:  # noqa: BLE001
        FAILED.append(f"{label}: raised {type(exc).__name__}, wanted SourceError")
        print(f"FAIL {label}: raised {type(exc).__name__}, wanted SourceError")
        return
    FAILED.append(f"{label}: did not raise")
    print(f"FAIL {label}: did not raise")


SIMPLIFY = Feed(key="s", name="simplify-shaped", url="x")
MAPPED = Feed(
    key="z", name="mapped", url="x", root="jobs", assume_active=True,
    fields=(("company_name", "company"), ("locations", "location"),
            ("terms", "season")),
)


def test_simplify_shape_unchanged():
    """The regression that matters: the two live feeds must not move."""
    listing = {
        "company_name": "Anduril", "title": "2027 Mechanical Engineer Intern",
        "locations": ["Costa Mesa, CA", "Seattle, WA"], "url": "https://x/1",
        "id": "abc", "active": True, "is_visible": True,
        "terms": ["Summer 2027"], "degrees": ["Bachelor's"],
    }
    posts, closed, dup = feeds.normalize(SIMPLIFY, [listing], {})
    check("an unmapped feed still parses", len(posts), 1)
    check("company", posts[0].company, "Anduril")
    check("locations join with a semicolon",
          posts[0].location, "Costa Mesa, CA; Seattle, WA")
    check("terms", posts[0].feed_terms, "Summer 2027")
    check("degrees", posts[0].feed_degrees, "Bachelor's")
    check("external id", posts[0].external_id, "abc")


def test_inactive_is_still_dropped():
    off = {"company_name": "X", "title": "T", "active": False}
    hidden = {"company_name": "X", "title": "T", "active": True, "is_visible": False}
    posts, closed, _ = feeds.normalize(SIMPLIFY, [off, hidden], {})
    check("an inactive listing is dropped", len(posts), 0)
    check("and counted as closed upstream", closed, 2)


def test_mapped_shape():
    listing = {
        "company": "KLA", "title": "Software Engineering Intern",
        "location": "Milpitas, CA", "url": "https://x/2", "id": "workday:kla:1",
        "season": "Summer 2027",
    }
    posts, closed, _ = feeds.normalize(MAPPED, [listing], {})
    check("a mapped feed parses", len(posts), 1)
    check("company comes through the map", posts[0].company, "KLA")
    check("a single location string becomes the location",
          posts[0].location, "Milpitas, CA")
    check("season is read as the term", posts[0].feed_terms, "Summer 2027")
    check("nothing is counted closed when assume_active is on", closed, 0)


def test_assume_active():
    """A feed with no `active` flag must not read as entirely closed."""
    listing = {"company": "KLA", "title": "T", "location": "X"}
    posts, closed, _ = feeds.normalize(MAPPED, [listing], {})
    check("presence is the open signal when declared", len(posts), 1)
    # The same listing against a feed that did NOT declare it.
    strict = Feed(key="q", name="strict", url="x",
                  fields=(("company_name", "company"), ("locations", "location")))
    posts2, closed2, _ = feeds.normalize(strict, [listing], {})
    check("and without the declaration it is treated as closed", len(posts2), 0)
    check("which is counted, not silently dropped", closed2, 1)


def test_own_board_still_wins():
    listing = {"company": "Anduril", "title": "T", "location": "X"}
    posts, _, dup = feeds.normalize(MAPPED, [listing], {"anduril": "Anduril"})
    check("a company with its own board is skipped", len(posts), 0)
    check("and counted as a duplicate", dup, 1)


def test_a_broken_response_raises():
    """Rule 13. An unreadable source raises; it never reports zero listings.

    Returning an empty list here would be read by `db.age_missing` as every
    posting from this feed having closed, turning one bad deploy upstream into
    a mass closure.
    """
    class FakeClient:
        def __init__(self, payload):
            self.payload = payload

        def get(self, *a, **k):
            raise AssertionError("unused")

    def fetch(payload, feed):
        # Exercise the real shape handling by monkeypatching the getter only.
        original = feeds._get
        feeds._get = lambda client, url: payload
        try:
            return feeds.fetch_feed(None, feed, {})
        finally:
            feeds._get = original

    raises("a root key that is missing raises",
           lambda: fetch({"other": []}, MAPPED))
    raises("a root declared against a list raises",
           lambda: fetch([], MAPPED))
    raises("a non-list payload with no root raises",
           lambda: fetch({"jobs": []}, SIMPLIFY))

    posts, stats = fetch({"jobs": [
        {"company": "KLA", "title": "T", "location": "X"}]}, MAPPED)
    check("a well-formed wrapped payload reads", len(posts), 1)
    check("and reports what it saw", stats["listings"], 1)


def main() -> int:
    for fn in (
        test_simplify_shape_unchanged,
        test_inactive_is_still_dropped,
        test_mapped_shape,
        test_assume_active,
        test_own_board_still_wins,
        test_a_broken_response_raises,
    ):
        fn()
    total = PASSED + len(FAILED)
    print()
    if FAILED:
        print(f"{len(FAILED)} of {total} FAILED")
        for f in FAILED:
            print(f"  {f}")
        return 1
    print(f"{PASSED} of {total} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
