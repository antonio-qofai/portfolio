"""Layer 1 watchers, aggregator half. Same contract as agent/fetchers.py.

A feed is a community-maintained repository that tracks internship postings across
thousands of employers and publishes JSON. It covers the companies the per-company
watcher structurally cannot poll, because they run Workday or an in-house system.

Two things make a feed different from a company board and both are handled here.

A feed carries an explicit active flag, so it already knows what closed. That is not
wired in as a second closure path. Inactive listings are simply not emitted, so they
fall out of the seen set and the existing consecutive_misses logic in agent/db.py
closes them on its normal schedule. One closure rule, not two.

A feed also carries companies the watcher already polls directly. Those listings are
skipped, because the company's own board is authoritative, carries the description
text Stage 0 needs, and updates sooner. Without that skip the same posting lands twice
under two source keys and is aged for closure twice.
"""

import httpx

from . import config
from .fetchers import Posting, SourceError, _get
from .sources import Company, Feed, company_name_index, normalize_company

# Feeds in this family disagree on how they name the term. Simplify publishes a
# "terms" list, vanshb03 publishes a single "season" string. Read whichever exists
# rather than configuring it per feed; a new feed in this lineage will use one of them.
TERM_KEYS = ("terms", "season", "seasons", "term")


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [str(v) for v in value if str(v).strip()]


def _terms(listing: dict, field_map: dict | None = None) -> str:
    mapped = (field_map or {}).get("terms")
    for key in ((mapped,) if mapped else ()) + TERM_KEYS:
        values = _as_list(listing.get(key))
        if values:
            return "; ".join(values)
    return ""


def _pick(listing: dict, field_map: dict, canonical: str, default=None):
    """Read one canonical field, through the feed's own name for it.

    Feeds outside the Simplify lineage name the same things differently:
    `company` for `company_name`, a single `location` string where Simplify has
    a `locations` list. The mapping lives in `sources/feeds.toml` so a new feed
    is a data edit, per CLAUDE.md rule 2, and this function is the only place
    that consults it.
    """
    key = field_map.get(canonical, canonical)
    value = listing.get(key)
    return default if value is None else value


def _is_open(listing: dict, require_visible: bool, feed: "Feed | None" = None) -> bool:
    """Is this listing currently open?

    `assume_active` exists for feeds that publish a snapshot of open roles and
    carry no `active` flag at all. For those, presence in the file IS the
    signal, and demanding a flag they never publish would read every listing as
    closed. It stays off by default because getting it wrong in the other
    direction resurrects postings that have actually closed.

    Either way this is only ever a reason to SKIP a listing. A feed dropping a
    posting lets the existing `consecutive_misses` logic close it on its normal
    schedule, which keeps one closure path rather than two. See the note on
    `require_visible` in sources/feeds.toml.
    """
    if feed is not None and feed.assume_active:
        return True
    if not listing.get("active"):
        return False
    if require_visible and not listing.get("is_visible", True):
        return False
    return True


def normalize(
    feed: Feed,
    listings: list[dict],
    skip_names: dict[str, str],
    require_visible: bool = True,
) -> tuple[list[Posting], int, int]:
    """Turn raw feed JSON into Postings.

    Returns the postings, how many listings were dropped as closed, and how many were
    skipped because the company already has its own watcher.
    """
    postings: list[Posting] = []
    closed = 0
    duplicated = 0

    field_map = feed.field_map

    for listing in listings:
        if not _is_open(listing, require_visible, feed):
            closed += 1
            continue

        company = str(_pick(listing, field_map, "company_name", "")).strip()
        title = str(_pick(listing, field_map, "title", "")).strip()
        if not company or not title:
            continue

        if normalize_company(company) in skip_names:
            duplicated += 1
            continue

        postings.append(
            Posting(
                company=company,
                title=title,
                location="; ".join(_as_list(_pick(listing, field_map, "locations"))),
                url=(_pick(listing, field_map, "url", "")
                     or _pick(listing, field_map, "company_url", "")),
                source=feed.source_key,
                ats="feed",
                external_id=str(_pick(listing, field_map, "id", "")),
                # Feeds publish a title and metadata, never the posting body. Stage 0
                # gets the term and degrees below instead of description text.
                description="",
                feed_terms=_terms(listing, field_map),
                feed_degrees="; ".join(
                    _as_list(_pick(listing, field_map, "degrees"))
                ),
            )
        )

    return postings, closed, duplicated


def fetch_feed(
    client: httpx.Client,
    feed: Feed,
    skip_names: dict[str, str],
    require_visible: bool = True,
) -> tuple[list[Posting], dict]:
    data = _get(client, feed.url)
    if feed.root:
        # A feed that wraps its listings in an object, e.g. {"jobs": [...]}.
        # Named in sources/feeds.toml rather than sniffed, so a response that
        # changes shape fails loudly instead of silently reading zero listings,
        # which `db.age_missing` would take as every posting having closed.
        if not isinstance(data, dict):
            raise SourceError(
                f"feed declares root {feed.root!r} but returned "
                f"{type(data).__name__}, not an object"
            )
        data = data.get(feed.root)
        if data is None:
            raise SourceError(f"feed has no {feed.root!r} key")
    if not isinstance(data, list):
        raise SourceError(f"expected a JSON list, got {type(data).__name__}")

    postings, closed, duplicated = normalize(feed, data, skip_names, require_visible)
    stats = {
        "listings": len(data),
        "closed_upstream": closed,
        "skipped_own_board": duplicated,
    }
    return postings, stats


def skip_index(companies: list[Company], settings: dict) -> dict[str, str]:
    """Which company names a feed should defer to. Empty means defer to nothing."""
    if not settings.get("prefer_company_boards", True):
        return {}
    return company_name_index(companies)


def make_client() -> httpx.Client:
    """Feeds are single large JSON files, so they get a longer timeout than a board."""
    return httpx.Client(
        timeout=config.FEED_TIMEOUT,
        headers={"User-Agent": config.USER_AGENT, "Accept": "application/json"},
        follow_redirects=True,
    )
