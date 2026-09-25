"""Loads the company token map from sources/companies.toml.

No company name, token, or posting-specific rule belongs anywhere else. If you need
to change what gets polled, edit the TOML file, not this module.
"""

import re
import tomllib
from dataclasses import dataclass

from . import config


@dataclass(frozen=True)
class Company:
    name: str
    ats: str
    token: str
    category: str = ""
    notes: str = ""
    feed_aliases: tuple[str, ...] = ()

    @property
    def source_key(self) -> str:
        return f"{self.ats}:{self.token}"


@dataclass(frozen=True)
class Feed:
    key: str
    name: str
    url: str
    notes: str = ""

    # Where the list of listings sits inside the response. Empty means the
    # document IS the list, which is what the two Simplify-lineage feeds do.
    # Added 2026-09-22 for a feed that wraps its listings in an object.
    root: str = ""

    # Canonical field name -> this feed's name for it, for feeds outside the
    # Simplify lineage. Empty means the feed already uses the canonical names.
    # A mapping is DATA in sources/feeds.toml, per CLAUDE.md rule 2; adding a
    # feed must never mean editing Python.
    fields: tuple = ()

    # Some feeds publish only what is currently open and carry no `active`
    # flag at all. For those, being in the file IS the open signal. Off by
    # default because guessing this wrong resurrects closed postings.
    assume_active: bool = False

    @property
    def field_map(self) -> dict:
        return dict(self.fields)

    @property
    def source_key(self) -> str:
        return f"feed:{self.key}"


def _load_toml(path=None) -> dict:
    path = path or config.SOURCES_PATH
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def normalize_company(name: str) -> str:
    """Reduce a company name to something two sources can agree on.

    Aggregators write "Anthropic", "Scale AI" and "Susquehanna International Group
    (SIG)" where the token map may write any of a dozen variants. This strips case,
    punctuation and the usual corporate suffixes so the two can be compared. It is
    deliberately generic; no company name belongs in this function. When a name
    genuinely cannot be reconciled, add a feed_aliases entry in companies.toml.
    """
    lowered = (name or "").lower()
    lowered = re.sub(
        r"\b(inc|llc|ltd|corp|corporation|co|the|technologies|technology|labs|ai)\b",
        "",
        lowered,
    )
    return re.sub(r"[^a-z0-9]", "", lowered)


def load_companies(path=None) -> list[Company]:
    data = _load_toml(path)

    companies = []
    for entry in data.get("company", []):
        companies.append(
            Company(
                name=entry["name"],
                ats=entry["ats"].lower(),
                token=entry["token"],
                category=entry.get("category", ""),
                notes=entry.get("notes", ""),
                feed_aliases=tuple(entry.get("feed_aliases", [])),
            )
        )

    seen = set()
    for c in companies:
        if c.source_key in seen:
            raise ValueError(f"duplicate source in token map: {c.source_key}")
        seen.add(c.source_key)

    return companies


# What a Workday poll does, as opposed to which Workday boards exist. Defaults
# live here so the file stays optional, and every one of them is overridable in
# the [workday] block of sources/companies.toml.
WORKDAY_DEFAULTS = {
    # Workday caps a page at 20 whatever is asked for.
    "page_size": 20,
    # 20 pages is 400 postings per search term per company. A term that hits the
    # cap is reported by tools.probe_workday rather than silently truncated.
    "max_pages": 20,
    "search_terms": ["intern", "internship", "co-op", "student", "university"],
    # The exact path: a facet every Workday tenant publishes, and the words that
    # mark one of its values as a student job type. See fetchers.
    "worker_type_facets": ["workerSubType", "jobFamilyGroup"],
    "worker_type_matches": ["intern", "co-op", "coop", "student", "apprentice"],
    # A tenant that names no student job type raises instead of text searching.
    # See fetchers.fetch_workday for what turning this on costs.
    "search_fallback": False,
    # How many descriptions one run will go back for. See triage.detail_pass.
    "max_details_per_run": 120,
}


def load_workday_settings(path=None) -> dict:
    """The [workday] block of companies.toml, merged over the defaults.

    Kept in the company map rather than in a file of its own because it is a
    property of how those boards are polled, and splitting it out would mean two
    files to open before understanding one source.
    """
    raw = _load_toml(path).get("workday", {})
    settings = dict(WORKDAY_DEFAULTS)
    settings.update({k: v for k, v in raw.items() if k in WORKDAY_DEFAULTS})
    terms = [t.strip() for t in settings["search_terms"] if str(t).strip()]
    if not terms:
        raise ValueError(
            "sources/companies.toml [workday] search_terms is empty, which would "
            "poll every Workday board in full. Remove the key to use the defaults."
        )
    settings["search_terms"] = terms
    return settings


def load_feeds(path=None) -> list[Feed]:
    """Aggregator feeds from sources/feeds.toml. Disabled feeds are not returned."""
    data = _load_toml(path or config.FEEDS_PATH)

    feeds = []
    for entry in data.get("feed", []):
        if not entry.get("enabled", True):
            continue
        feeds.append(
            Feed(
                key=entry["key"],
                name=entry.get("name", entry["key"]),
                url=entry["url"],
                notes=entry.get("notes", ""),
                root=entry.get("root", ""),
                fields=tuple((entry.get("fields") or {}).items()),
                assume_active=bool(entry.get("assume_active", False)),
            )
        )

    seen = set()
    for f in feeds:
        if f.key in seen:
            raise ValueError(f"duplicate feed key: {f.key}")
        seen.add(f.key)

    return feeds


def load_feed_settings(path=None) -> dict:
    return _load_toml(path or config.FEEDS_PATH).get("settings", {})


def company_name_index(companies: list[Company]) -> dict[str, str]:
    """Normalized name to real name, for deciding whether a feed listing is a duplicate.

    Includes every feed_aliases entry, which is the escape hatch for a company an
    aggregator insists on calling something else.
    """
    index: dict[str, str] = {}
    for c in companies:
        for name in (c.name, *c.feed_aliases):
            key = normalize_company(name)
            if key:
                index[key] = c.name
    return index
