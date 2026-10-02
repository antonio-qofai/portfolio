"""What the retrospective must not claim.

Every test here is about a number being honest rather than about it being
produced. A count of engagements on a calendar page is the kind of thing
people repeat in meetings, so the ways it can be quietly wrong are the point:
counting a like on a post the library never listed, counting one like thirty
times because it was read thirty mornings, or reporting an empty panel for two
situations that mean opposite things.

Run it:  cd ingestion && ../.venv/bin/python test_performance.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_AGENT_ROOT = _HERE.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_AGENT_ROOT))

import performance  # noqa: E402
from calendar_model.state_store import record_state  # noqa: E402

PASSED: list = []
FAILED: list = []


def check(name):
    def wrap(fn):
        try:
            fn()
        except AssertionError as exc:
            FAILED.append(f"FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            FAILED.append(f"ERROR {name}: {type(exc).__name__}: {exc}")
        else:
            PASSED.append(f"ok  {name}")
        return fn

    return wrap


def _root():
    return Path(tempfile.mkdtemp())


def _published(root, url, title, date, **extra):
    value = {"source": performance.SOURCE_PUBLISHED, "title": title, "url": url}
    value.update(extra)
    record_state(
        performance.DEFAULT_LOG_STORE,
        [{"value": value, "entry_key": f"{performance.SOURCE_PUBLISHED}/{url}", "event_at": date}],
        root=root,
    )


def _engagement(root, post_url, who, profile="https://linkedin.com/in/x"):
    value = {
        "source": performance.SOURCE_ENGAGEMENT,
        "title": who,
        "profile_url": profile,
    }
    if post_url is not None:
        value["post_url"] = post_url
    record_state(
        performance.DEFAULT_LOG_STORE,
        [{
            "value": value,
            "entry_key": f"{performance.SOURCE_ENGAGEMENT}/{post_url}::{profile}",
            "event_at": "2026-09-19",
        }],
        root=root,
    )


def _run(root, source, status):
    record_state(
        performance.DEFAULT_RUN_STORE,
        [{"value": {"source": source, "status": status}, "entry_key": source}],
        root=root,
    )


# --- the names this reads under -----------------------------------------------


@check("the source names match the runner that writes them")
def _():
    """`performance.py` restates these rather than importing `daily_ingest`,
    to keep `requests` off the served page's import path. A restated constant
    can drift, so this is the pin: if a rename here goes one way and the
    runner goes the other, this module reads a source nothing writes and the
    panel is silently empty forever."""
    sys.path.insert(0, str(_HERE))
    import daily_ingest

    assert performance.SOURCE_PUBLISHED == daily_ingest.SOURCE_PUBLISHED
    assert performance.SOURCE_ENGAGEMENT == daily_ingest.SOURCE_ENGAGEMENT
    assert performance.DEFAULT_LOG_STORE == daily_ingest.DEFAULT_STORE
    assert performance.DEFAULT_RUN_STORE == daily_ingest.DEFAULT_RUN_STORE


# --- joining a like to a post --------------------------------------------------


@check("the share URL a person pastes and the feed URL the scrape returns are one post")
def _():
    """The join that would otherwise silently match nothing. The library holds
    what somebody copied out of the address bar and the scrape holds
    PhantomBuster's `postsUrl`, and LinkedIn writes those two differently. A
    reader matching on the path reports zero engagement on every post and
    reports it as a clean read."""
    share = "https://www.linkedin.com/posts/jordan_ai-in-pe-activity-7123456789012345678-AbCd"
    feed = "https://www.linkedin.com/feed/update/urn:li:activity:7123456789012345678"
    urn_share = "https://www.linkedin.com/feed/update/urn:li:share:7123456789012345678"
    assert performance.normalise_url(share) == performance.normalise_url(feed)
    assert performance.normalise_url(urn_share) == performance.normalise_url(feed)

    other = "https://www.linkedin.com/feed/update/urn:li:activity:7999999999999999999"
    assert performance.normalise_url(share) != performance.normalise_url(other), (
        "two different posts collapsed into one, which would pool their engagement"
    )

    root = _root()
    _published(root, share, "The pilot-to-production gap", "2026-09-01")
    _engagement(root, feed, "Jane Doe", "https://li/in/jane")
    history = performance.read_history(root=root)
    assert history.posts[0].engagements == 1, "the two URL forms did not join"
    assert history.unattached_engagements == 0


@check("URLs entered by two different hands still join")
def _():
    """One side is pasted into Airtable by a person, the other is whatever the
    scrape's CSV carried. They differ in the ways pasted URLs always differ,
    and a join that misses on a trailing slash reports zero engagement on a
    post that got plenty."""
    same = [
        "https://www.linkedin.com/posts/jordan_activity-123",
        "https://linkedin.com/posts/jordan_activity-123/",
        "http://www.linkedin.com/posts/jordan_activity-123",
        "linkedin.com/posts/jordan_activity-123",
        "https://www.linkedin.com/posts/jordan_activity-123?utm_source=share",
    ]
    keys = {performance.normalise_url(u) for u in same}
    assert len(keys) == 1, f"these should all be one post: {keys}"
    assert performance.normalise_url("") == ""
    # Different posts must not collapse into one.
    assert performance.normalise_url(same[0]) != performance.normalise_url(
        "https://www.linkedin.com/posts/jordan_activity-124"
    )


@check("engagement counts against the post it landed on")
def _():
    root = _root()
    _published(root, "https://www.linkedin.com/posts/a", "First post", "2026-09-01")
    _engagement(root, "https://linkedin.com/posts/a/", "Jane Doe", "https://li/in/jane")
    _engagement(root, "https://linkedin.com/posts/a", "Sam Roe", "https://li/in/sam")

    history = performance.read_history(root=root)
    assert len(history.posts) == 1, history.posts
    assert history.posts[0].engagements == 2, history.posts[0].engagements
    # Newest read first, which is what the panel shows when it can only show
    # three names. The store returns newest first and this preserves it.
    assert history.posts[0].engagers == ["Sam Roe", "Jane Doe"], history.posts[0].engagers
    assert history.total_engagements == 2
    assert history.unattached_engagements == 0


@check("a like on a post the library never listed is not counted as zero")
def _():
    """It is also not counted against some other post. The library is
    maintained by hand, so this is the normal case, and the page says the
    count is partial rather than implying it is complete."""
    root = _root()
    _published(root, "https://www.linkedin.com/posts/a", "First post", "2026-09-01")
    _engagement(root, "https://www.linkedin.com/posts/never-logged", "Jane Doe")

    history = performance.read_history(root=root)
    assert history.posts[0].engagements == 0
    assert history.unattached_engagements == 1
    assert history.unreadable_engagements == 0
    assert history.total_engagements == 1


@check("a row with no post column is unreadable, which is our problem not theirs")
def _():
    """Distinct from unattached on purpose. Unattached means somebody did not
    log a post; unreadable means the column names in portal_sources.yaml are
    wrong, and only one of those is fixable here."""
    root = _root()
    _published(root, "https://www.linkedin.com/posts/a", "First post", "2026-09-01")
    _engagement(root, None, "Jane Doe")

    history = performance.read_history(root=root)
    assert history.unreadable_engagements == 1, history.unreadable_engagements
    assert history.unattached_engagements == 0


@check("the same like read on thirty mornings is one like")
def _():
    """The store is append-only, so the daily run writes the same engagement
    every day it is still visible. The key is the (post, person) pair and the
    read collapses on it. If it did not, a month of reading would report
    thirty reactions from one person."""
    root = _root()
    _published(root, "https://www.linkedin.com/posts/a", "First post", "2026-09-01")
    for _ in range(30):
        _engagement(root, "https://www.linkedin.com/posts/a", "Jane Doe", "https://li/in/jane")

    history = performance.read_history(root=root)
    assert history.posts[0].engagements == 1, history.posts[0].engagements
    assert history.posts[0].engagers == ["Jane Doe"]


@check("posts come back newest first")
def _():
    root = _root()
    _published(root, "https://x/1", "Older", "2026-07-04")
    _published(root, "https://x/2", "Newer", "2026-09-04")
    history = performance.read_history(root=root)
    assert [p.title for p in history.posts] == ["Newer", "Older"]


# --- the empty panel says which kind of empty ----------------------------------


@check("the page says nothing until there is something a founder can act on")
def _():
    """Four states, and only two of them earn words on the calendar.

    Nobody has asked, and the token is missing, are both facts about our
    onboarding rather than about his content, and printing either over a
    forward calendar is the overexplaining that got the "Read this first"
    callout deleted on 2026-09-21. The README and the run log hold them.

    A failed read and a genuinely empty library do earn a line, because one
    means the number cannot be trusted and the other is a real answer."""
    nothing = performance.read_history(root=_root())
    assert not nothing.has_history
    assert nothing.why_empty() == "", nothing.why_empty()

    root = _root()
    _run(root, performance.SOURCE_PUBLISHED, "unconfigured")
    _run(root, performance.SOURCE_ENGAGEMENT, "unconfigured")
    assert performance.read_history(root=root).why_empty() == ""

    root = _root()
    _run(root, performance.SOURCE_PUBLISHED, "failed")
    _run(root, performance.SOURCE_ENGAGEMENT, "failed")
    failed = performance.read_history(root=root).why_empty()
    assert "unknown history" in failed, failed
    assert len(failed) < 120, f"too long for a calendar page: {failed}"

    root = _root()
    _run(root, performance.SOURCE_PUBLISHED, "ok")
    _run(root, performance.SOURCE_ENGAGEMENT, "ok")
    asked = performance.read_history(root=root).why_empty()
    assert asked == "Nothing published yet.", asked


@check("a history that exists explains nothing, because there is nothing to explain")
def _():
    """Same rule the approved-horizon band was fixed to follow on 2026-09-15:
    say nothing rather than explain an absence that is not there."""
    root = _root()
    _published(root, "https://x/1", "A post", "2026-09-01")
    _run(root, performance.SOURCE_PUBLISHED, "ok")
    history = performance.read_history(root=root)
    assert history.has_history
    assert history.why_empty() == "", history.why_empty()


if __name__ == "__main__":
    for line in PASSED:
        print(line)
    for line in FAILED:
        print(line, file=sys.stderr)
    print(f"\n{len(PASSED)}/{len(PASSED) + len(FAILED)} passed")
    sys.exit(1 if FAILED else 0)
