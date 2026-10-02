"""What went out, and how it did.

Why this exists, and why it is not per slot
-------------------------------------------
PRD Section 4 records the cost of the two deferred inputs plainly: v1 places a
post by internal coherence alone, with no engagement weighting and no
continuity against measured performance of what was published before. Once
`portal_reader.py` can fetch both, this is the module that turns them into
something a founder can read.

It deliberately does **not** attach performance to a slot on the calendar, and
that is the honest answer rather than a smaller one. Joining a published post
to a slot needs a link that nothing in this repo creates: a slot references a
corpus item (`content-atomizer/post-3`), a published post is a LinkedIn URL,
and nobody records the URL of a slot when it goes out. `published` is already
a slot state that no human may set through the queue, so the place that link
belongs is whatever eventually marks a slot published. Matching on title would
look like the same feature and would be a guess, silently wrong whenever an
editor changed a headline, which is the class of thing this build refuses.

So this reads as a retrospective: here is what we published, here is what it
drew, newest first. That is genuinely useful next to a forward calendar and it
claims nothing it cannot support.

Where it sits
-------------
Beside `gap_detection.py` for the same reason that one is here: both are reads
over what the daily run already recorded, with no network call, no dependency
agent and no model. Everything it reports comes out of `ingestion_log`.

It reports an absence as an absence
-----------------------------------
Nothing has been published and no engagement exists, so today this returns
empty, and it says which kind of empty. The two that matter are different
facts: nobody has set `PORTAL_TOKEN`, so the question was never asked, versus
the portal was asked and has nothing. A page that showed one blank panel for
both would be the page lying by omission, and this build has had to be stopped
from doing that three times.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

_HERE = Path(__file__).resolve().parent
_AGENT_ROOT = _HERE.parent

if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

from calendar_model.state_store import DEFAULT_STATE_ROOT, read_state  # noqa: E402

# The two sources this reads, and the two stores it reads them from. Restated
# rather than imported from `daily_ingest`, following `gap_detection.py`, which
# is the other module here that reads the stores without doing any ingesting
# and declares `DEFAULT_RUN_STORE` for itself.
#
# The reason is not style. Importing the runner drags in every reader and with
# them `requests`, and this module is on the served page's request path, where
# `ui/app.py` has the agent's own folders on `sys.path`. `approval/queue.py`
# then shadows the standard library's `queue` for urllib3, which imports it,
# and the page dies on `module 'queue' has no attribute 'Queue'` before it
# renders. That is the same name collision that answered 500 under gunicorn on
# 2026-09-20, pointing the other way.
#
# A restated constant can drift from the runner, so it is pinned:
# `test_performance.py` fails if these four stop matching `daily_ingest`.
SOURCE_PUBLISHED = "published-content"
SOURCE_ENGAGEMENT = "linkedin-post-engagement"

DEFAULT_LOG_STORE = "ingestion_log"
DEFAULT_RUN_STORE = "ingest_runs"


# The numeric id inside a LinkedIn post URL, in either of the two forms the
# two sides of this join actually use. Long enough to be an id rather than a
# page number, which is what the length bound is for.
_ACTIVITY_ID = re.compile(r"(?:activity|share)[:\-](\d{6,})")


def normalise_url(url: str) -> str:
    """A LinkedIn post URL reduced to what identifies the post.

    The two sides of this join never agree on a URL, and that is not sloppiness
    on anyone's part. A published row is pasted into the Content Library by a
    person reading the post's own address bar, which gives the share form,
    `/posts/<author>_<slug>-activity-7123...-AbCd`. An engagement row carries
    PhantomBuster's `postsUrl`, which is the feed form,
    `/feed/update/urn:li:activity:7123...`. Those are the same post and no
    amount of trimming slashes makes the strings equal.

    What they share is the activity id, so that is the key whenever one is
    there. A reader that matched on the path would report zero engagement on
    every post that got some, and report it as a clean read: the failure this
    build keeps meeting, where the number is wrong and nothing looks broken.

    Anything without an id falls back to host and path with the differences
    pasted URLs always have removed, which is a tracking query string, a
    trailing slash, the scheme and a leading www.

    Deliberately not more than this. Resolving a shortened link needs a
    network call, and a page render must not depend on the internet.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    found = _ACTIVITY_ID.search(raw)
    if found:
        return f"activity:{found.group(1)}"
    if "//" not in raw:
        raw = "https://" + raw
    parts = urlsplit(raw)
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parts.path.rstrip("/")
    return f"{host}{path}" if host else path


@dataclass
class PublishedRecord:
    """One post that went out, with what it drew."""

    url: str
    title: str
    channel: str
    date_published: str
    themes: list = field(default_factory=list)
    engagements: int = 0
    # Who engaged, deduplicated, newest read first. Names rather than counts
    # because "Jane at Acme reacted" is the thing a founder acts on and a
    # number is not.
    engagers: list = field(default_factory=list)


@dataclass
class History:
    """The retrospective, and an honest account of what is missing from it."""

    posts: list = field(default_factory=list)
    total_engagements: int = 0
    # Engagement rows whose post is not in the published library. Not an
    # error: somebody engaging with a post nobody logged is the normal result
    # of the library being maintained by hand. Reported so the panel can say
    # the count is partial rather than implying it is complete.
    unattached_engagements: int = 0
    # Engagement rows with no resolvable post column at all, which means the
    # column candidates in `portal_sources.yaml` are wrong. A different
    # problem from the line above and fixable by us.
    unreadable_engagements: int = 0
    # Per source: what the last run made of it. `None` when the source has
    # never appeared in a run record at all.
    asked: dict = field(default_factory=dict)

    @property
    def has_history(self) -> bool:
        return bool(self.posts)

    def why_empty(self) -> str:
        """One sentence naming which kind of nothing this is, for the page.

        Empty when there is history to show, so a caller can print it without
        checking, and so the page never carries a note explaining an absence
        that is not there. Same rule the approved-horizon band was fixed to
        follow on 2026-09-15.
        """
        if self.has_history:
            return ""
        states = {self.asked.get(SOURCE_PUBLISHED), self.asked.get(SOURCE_ENGAGEMENT)}
        # Silence, not a sentence, for the two states that mean "not set up
        # yet". Trimmed 2026-09-21: the page was carrying three lines about a
        # portal token on a calendar nobody had asked a question of, which is
        # the overexplaining Antonio asked to be taken out. Both of these are
        # facts about our onboarding rather than about his content, and the
        # README and the run log already hold them.
        if states == {None} or "unconfigured" in states:
            return ""
        if "failed" in states:
            return "The read failed, so this is an unknown history rather than an empty one."
        return "Nothing published yet."


def _latest_run_states(root, run_store: str) -> dict:
    """What the newest run record says about each of the two sources."""
    states: dict = {}
    for entry in read_state(run_store, latest_per_key=True, root=root) or []:
        value = entry.value or {}
        source = value.get("source")
        if source in (SOURCE_PUBLISHED, SOURCE_ENGAGEMENT):
            states[source] = value.get("status")
    return states


def read_history(
    *,
    root=DEFAULT_STATE_ROOT,
    store: str = DEFAULT_LOG_STORE,
    run_store: str = DEFAULT_RUN_STORE,
    limit: int | None = None,
) -> History:
    """The published posts and their engagement, newest first.

    `latest_per_key=True` on both reads, because the log is append-only and a
    post read on thirty mornings is one post. The key is already the post URL
    for published rows and the (post, person) pair for engagement rows, so the
    collapse is exactly right: one row per post, one row per person per post.
    """
    history = History(asked=_latest_run_states(root, run_store))

    published = []
    for entry in read_state(store, latest_per_key=True, root=root) or []:
        value = entry.value or {}
        if value.get("source") != SOURCE_PUBLISHED:
            continue
        published.append(
            PublishedRecord(
                url=str(value.get("url") or ""),
                title=str(value.get("title") or ""),
                channel=str(value.get("channel") or ""),
                date_published=str(entry.event_at or "")[:10],
                themes=list(value.get("themes") or []),
            )
        )

    by_url = {}
    for record in published:
        key = normalise_url(record.url)
        if key:
            by_url.setdefault(key, record)

    for entry in read_state(store, latest_per_key=True, root=root) or []:
        value = entry.value or {}
        if value.get("source") != SOURCE_ENGAGEMENT:
            continue
        history.total_engagements += 1
        post_url = str(value.get("post_url") or "")
        if not post_url:
            history.unreadable_engagements += 1
            continue
        record = by_url.get(normalise_url(post_url))
        if record is None:
            history.unattached_engagements += 1
            continue
        record.engagements += 1
        who = str(value.get("title") or value.get("profile_url") or "").strip()
        if who and who not in record.engagers:
            record.engagers.append(who)

    published.sort(key=lambda r: r.date_published, reverse=True)
    history.posts = published[:limit] if limit else published
    return history


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--state-root", default=str(DEFAULT_STATE_ROOT))
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    history = read_history(root=Path(args.state_root), limit=args.limit)
    if not history.has_history:
        print(history.why_empty())
    for record in history.posts:
        line = f"{record.date_published or '????-??-??'}  {record.engagements:4} engagements  {record.title}"
        print(line)
    if history.unattached_engagements:
        print(
            f"\n{history.unattached_engagements} engagement(s) on posts the library does not list."
        )
    if history.unreadable_engagements:
        print(
            f"{history.unreadable_engagements} engagement(s) carry no post column; "
            "portal_sources.yaml needs the real column name."
        )
