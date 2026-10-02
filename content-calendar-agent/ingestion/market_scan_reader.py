"""Reads the weekly market scan: what the outside world published this week.

Jordan asked for this directly on the founder call (interview-transcript.md,
21:15) and tied it to the narrative arc rather than to scheduling: "there's
sort of a research like what's happening this week, and like what's been
published, what's out there... this is good sort of secondary agent check to
say, all right, what's all out there? What's the current state of the world
today. And based on that sort of narrative, like what should be the
narrative arc."

So the output of this module feeds arc formation. It is not a content
source. It never produces a post and it never puts anything on the calendar.
It tells the synthesis step what the world is currently talking about so the
arc can respond to it. Jordan also noted the dependency agents "already have
that baked in" to a degree, which is why this is a secondary check.

Why RSS and Atom, and not a search tool
---------------------------------------
C2 runs unattended on a daily GitHub Actions schedule (PRD.md Section 5,
build order item 12). Claude Code's WebSearch and WebFetch tools do not
exist inside a CI runner, so anything built on them would only work while a
human was driving it interactively. RSS and Atom feeds run headless: no API
key, no per-call cost, no rate-limit account, deterministic output, and
parseable with the standard library's xml.etree. If a search or news API is
ever added, it belongs behind the same `read_market_scan` signature with its
key coming from an environment variable, the way conference_reader.py takes
its Airtable token.

Dependencies: `requests` and `pyyaml`, both already required by this folder
(conference_reader.py and narrative/corpus_map.py respectively). This module
adds no new dependency. Feed parsing is stdlib xml.etree.

Nothing is hardcoded
--------------------
Feed URLs, the lookback window, the per-source and total item caps, the HTTP
timeout and user agent, and the relevance terms all live in
`market_scan_sources.yaml` alongside this file, following the precedent of
narrative/threads.yaml: editorial strategy is configuration, not logic. No
publication name, no topic, and no date appears in this file. The seed
config is explicitly provisional; which publications Jordan actually reads is
an open question recorded in PRD.md Section 7.

Verified live on 2026-08-18 against the ten seeded feeds. Real findings from
that run, all handled here rather than assumed away:

  - hai.stanford.edu/news/rss.xml answers HTTP 200 with an HTML page. A 200
    is not evidence of a feed, so the parser reports "not XML" and skips.
  - middlemarketgrowth.org/feed/ is valid RSS that uses the `media:` prefix
    without declaring the namespace, which is fatal to a strict XML parse.
    Since it is the single most on-target source in the seed list, undeclared
    prefixes are repaired once and re-parsed, with a warning.
  - feeds.a.dj.com answers HTTP 200 with items roughly nineteen months old.
    A live feed can be stale; the lookback window is what catches that, and a
    source contributing zero items is reported per source rather than hidden.
  - openai.com/news/rss.xml returns 1136 items in one response, which is why
    per-source capping is a configured limit and not an afterthought.
  - Dates arrive in at least four shapes across these feeds: RFC 822 with a
    numeric offset, RFC 822 with a named zone (GMT), ISO 8601 with an offset
    or a trailing Z, and McKinsey's bare "Tue, 18 Aug 2026" with no time at
    all. That last one defeats every standard parser, and left unhandled it
    silently makes a whole feed undated and therefore immune to the window.
  - A feed can parse perfectly and still be worth nothing. FT Alphaville and
    Google's AI blog both returned clean, current items and matched zero of
    them, because Alphaville's descriptions are one-line jokes and Google's
    are image alt text. Reporting kept-of-seen per source is what made that
    visible; a single total would have hidden it.

Degradation, per conference_reader.py's precedent: a missing config file, an
unreachable host, a non-200 response, a body that is not XML, a feed with no
items, or a source list that is empty all yield an empty list plus a warning
on stderr, never an exception. One dead feed must not take down the run.

Identifiers: every item carries a stable `item_id` derived from its
canonicalized URL (or its feed guid, or source id plus title, in that order),
so a later run can recognize an item it has already seen. This module does
not deduplicate and does not persist anything. There is no state store yet;
that is build order item 8.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import read_status
import requests
import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "market_scan_sources.yaml"

# Fallbacks used only when the config omits a key. They are mechanical
# limits, not editorial choices: no topic, publication, or date is implied
# by any of them.
_FALLBACK_LOOKBACK_DAYS = 7
_FALLBACK_MAX_PER_SOURCE = 25
_FALLBACK_MAX_TOTAL = 200
_FALLBACK_TIMEOUT = 20.0
_FALLBACK_SUMMARY_CHARS = 400
_FALLBACK_USER_AGENT = "qofai-thought-leadership-calendar-manager/0.1 (market scan)"

# Feed element names this reader understands, by local name (namespace
# prefixes are stripped before matching, since feeds disagree about them).
_ITEM_TAGS = ("item", "entry")
_TITLE_TAGS = ("title",)
_LINK_TAGS = ("link",)
_DATE_TAGS = ("pubdate", "published", "updated", "date", "modified")
_SUMMARY_TAGS = ("description", "summary", "content", "encoded", "subtitle")
_GUID_TAGS = ("guid", "id")

_TRACKING_PARAM_PREFIXES = ("utm_",)
_TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source"}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_XMLNS_DECL_RE = re.compile(r'xmlns:([A-Za-z_][\w.-]*)\s*=')
_PREFIXED_ELEMENT_RE = re.compile(r"<\s*/?\s*([A-Za-z_][\w.-]*):")
_PREFIXED_ATTR_RE = re.compile(r'\s([A-Za-z_][\w.-]*):[A-Za-z_][\w.-]*\s*=\s*["\']')
_ROOT_OPEN_RE = re.compile(r"<([A-Za-z_][\w.-]*(?::[A-Za-z_][\w.-]*)?)\b")
_NORMALIZE_RE = re.compile(r"[^\w\s-]")


@dataclass
class MarketScanItem:
    """One published item the outside world put out inside the window."""

    item_id: str
    id_basis: str  # how item_id was derived: "url", "guid", or "title"
    source_id: str
    source_name: str
    title: str
    url: str | None = None
    published_at: str | None = None  # ISO 8601 UTC, or None if the feed gave none
    summary: str = ""
    lens: str | None = None  # advisory, passed through from config
    matched_terms: list[str] = field(default_factory=list)


@dataclass
class SourceReport:
    """Per-source outcome for one run, so a silent source is visible."""

    source_id: str
    source_name: str
    ok: bool
    items_kept: int = 0
    items_seen: int = 0
    detail: str = ""


def _warn(message: str) -> None:
    print(f"market_scan_reader: {message}", file=sys.stderr)


# ---------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------

def load_config(config_path: str | Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    """Loads the source list and run settings.

    Returns an empty dict (never raises) if the file is missing or is not
    readable YAML, so a config problem degrades the scan to nothing rather
    than crashing C2's daily run.
    """
    path = Path(config_path)
    if not path.is_file():
        _warn(f"config not found at {path}; no sources to scan.")
        return {}
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        _warn(f"could not parse config at {path} ({exc}); no sources to scan.")
        return {}
    if not isinstance(loaded, dict):
        _warn(f"config at {path} is not a mapping; no sources to scan.")
        return {}
    return loaded


def _config_int(config: dict[str, Any], key: str, fallback: int) -> int:
    try:
        value = int(config.get(key, fallback))
    except (TypeError, ValueError):
        _warn(f"config key {key!r} is not a number; using {fallback}.")
        return fallback
    return value if value > 0 else fallback


# ---------------------------------------------------------------------
# Fetching and parsing
# ---------------------------------------------------------------------

def _repair_undeclared_namespaces(text: str) -> str | None:
    """Binds namespace prefixes a feed uses but never declares.

    Real case, measured 2026-08-18: Middle Market Growth publishes valid RSS
    that references `media:content` without an `xmlns:media` declaration,
    which xml.etree rejects outright ("unbound prefix"). Rather than lose the
    most on-target source in the list to someone else's template bug, the
    undeclared prefixes are bound to placeholder URIs on the root element and
    the document is parsed again. Prefixes are stripped at read time anyway,
    so the placeholder URI is never observable downstream.

    Returns None if there is nothing to repair.
    """
    declared = set(_XMLNS_DECL_RE.findall(text))
    used = set(_PREFIXED_ELEMENT_RE.findall(text)) | set(_PREFIXED_ATTR_RE.findall(text))
    undeclared = sorted(p for p in used if p not in declared and p not in ("xmlns", "xml"))
    if not undeclared:
        return None

    root_open = _ROOT_OPEN_RE.search(text)
    if root_open is None:
        return None
    declarations = "".join(f' xmlns:{p}="urn:qofai-unbound:{p}"' for p in undeclared)
    return text[: root_open.end()] + declarations + text[root_open.end():]


def parse_feed(body: bytes) -> list[dict[str, Any]]:
    """Parses an RSS or Atom body into raw per-item field dicts.

    Element namespaces are stripped, so RSS `<item>` and Atom `<entry>` and
    their differently-named date and summary elements are read through one
    path. Raises ValueError if the body is not parseable XML; the caller
    turns that into a warning.
    """
    text = body.decode("utf-8", errors="replace").lstrip("﻿ \t\r\n")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        repaired = _repair_undeclared_namespaces(text)
        if repaired is None:
            raise ValueError(str(exc))
        _warn(f"feed did not parse ({exc}); retrying with undeclared namespace prefixes bound.")
        try:
            root = ET.fromstring(repaired)
        except ET.ParseError as retry_exc:
            raise ValueError(str(retry_exc))

    items: list[dict[str, Any]] = []
    for element in root.iter():
        if _local_name(element.tag) not in _ITEM_TAGS:
            continue
        fields: dict[str, Any] = {}
        for child in element:
            name = _local_name(child.tag)
            if name in _LINK_TAGS:
                # RSS puts the URL in the element text; Atom puts it in href.
                href = child.attrib.get("href")
                rel = child.attrib.get("rel", "alternate")
                candidate = href if href else (child.text or "")
                if candidate and rel == "alternate" and "link" not in fields:
                    fields["link"] = candidate.strip()
                continue
            value = "".join(child.itertext()).strip()
            if not value:
                continue
            fields.setdefault(name, value)
        items.append(fields)
    return items


def _local_name(tag: str) -> str:
    return tag.split("}")[-1].split(":")[-1].lower()


def _first(fields: dict[str, Any], names: Iterable[str]) -> str | None:
    for name in names:
        value = fields.get(name)
        if value:
            return str(value)
    return None


def fetch_feed(url: str, timeout: float, user_agent: str) -> bytes | None:
    """Fetches one feed. Returns None (with a warning) on any failure."""
    try:
        response = requests.get(
            url,
            headers={"User-Agent": user_agent, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        _warn(f"could not reach {url} ({exc}); skipping this source.")
        return None
    if response.status_code != 200:
        _warn(f"{url} returned HTTP {response.status_code}; skipping this source.")
        return None
    if not response.content:
        _warn(f"{url} returned an empty body; skipping this source.")
        return None
    return response.content


# ---------------------------------------------------------------------
# Field normalization
# ---------------------------------------------------------------------

def _clean_text(raw: str | None, max_chars: int) -> str:
    if not raw:
        return ""
    text = html.unescape(_TAG_RE.sub(" ", raw))
    text = _WS_RE.sub(" ", text).strip()
    if max_chars and len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text


def parse_date(raw: str | None) -> datetime | None:
    """Parses the date shapes these feeds actually emit, or returns None.

    Measured on 2026-08-18 across the seeded sources: RFC 822 with a numeric
    offset ("Tue, 18 Aug 2026 10:06:43 +0000"), RFC 822 with a named zone
    ("... 11:00:00 GMT"), ISO 8601 with an offset ("2026-08-18T09:00:00-04:00"),
    ISO 8601 with a trailing Z, and a bare date with no time at all
    ("Tue, 18 Aug 2026" from McKinsey). Python 3.9's fromisoformat handles
    none of the Z-suffixed cases, so it is fed a normalized string.
    """
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None

    try:
        parsed = parsedate_to_datetime(text)
        if parsed is not None:
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass

    iso = text.replace("Z", "+00:00").replace("z", "+00:00")
    # Trim fractional seconds longer than microseconds, which 3.9 rejects.
    iso = re.sub(r"(\.\d{6})\d+", r"\1", iso)
    for candidate in (iso, iso[:19], iso[:10]):
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    # Date without a time. McKinsey emits "Tue, 18 Aug 2026", which every
    # parser above rejects; left unhandled it makes a whole feed undated and
    # therefore immune to the lookback window.
    for fmt in ("%a, %d %b %Y", "%d %b %Y", "%a %d %b %Y", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def canonicalize_url(url: str | None) -> str | None:
    """Strips fragments and tracking parameters so the same article fetched
    twice, or syndicated through two feeds, yields the same identifier."""
    if not url:
        return None
    try:
        split = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    if not split.netloc:
        return url.strip()
    query = [
        (key, value)
        for key, value in parse_qsl(split.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_PARAMS
        and not key.lower().startswith(_TRACKING_PARAM_PREFIXES)
    ]
    path = split.path.rstrip("/") or "/"
    return urlunsplit((split.scheme.lower(), split.netloc.lower(), path, urlencode(query), ""))


def _stable_item_id(source_id: str, url: str | None, guid: str | None, title: str) -> tuple[str, str]:
    """Returns (item_id, id_basis).

    Derived from content, never from position or fetch time, so two runs a
    week apart agree on what is the same item. Deduplication itself is build
    order item 8 and is deliberately not done here; this only makes it
    possible later.
    """
    if url:
        return _digest(url), "url"
    if guid:
        return _digest(f"{source_id}\n{guid}"), "guid"
    return _digest(f"{source_id}\n{title}"), "title"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------
# Relevance
# ---------------------------------------------------------------------

def _normalize_for_match(text: str) -> str:
    """Lowercases and pads, replacing punctuation with spaces but keeping
    hyphens, so a config term like " ai " matches a title that starts with
    "AI," while "carve-out" still matches as written."""
    return " " + _WS_RE.sub(" ", _NORMALIZE_RE.sub(" ", text.lower())).strip() + " "


def matched_terms(item_fields: dict[str, str], relevance: dict[str, Any]) -> list[str] | None:
    """Returns the terms an item matched, or None if it should be dropped.

    An empty list means the filter is off and everything passes. The terms
    and the fields they are checked against come from config; this function
    knows nothing about what the terms mean.

    `exclude_terms` (added 2026-09-22) drop an item whatever the mode, checked
    against `exclude_fields` (default: title only), so a house ad is dropped
    even from a feed whose relevance filter is off.
    """
    excludes = [str(t).lower() for t in (relevance.get("exclude_terms") or []) if str(t).strip()]
    if excludes:
        exclude_fields = relevance.get("exclude_fields") or ["title"]
        exclude_haystack = _normalize_for_match(
            " ".join(str(item_fields.get(f, "")) for f in exclude_fields)
        )
        if any(term in exclude_haystack for term in excludes):
            return None

    mode = str(relevance.get("mode", "off")).lower()
    if mode == "off":
        return []
    terms = [str(t).lower() for t in (relevance.get("terms") or []) if str(t).strip()]
    if not terms:
        return []

    fields = relevance.get("match_fields") or ["title", "summary"]
    haystack = _normalize_for_match(" ".join(str(item_fields.get(f, "")) for f in fields))
    hits = [term.strip() for term in terms if term in haystack]

    if mode == "all":
        return hits if len(hits) == len(terms) else None
    return hits if hits else None


# ---------------------------------------------------------------------
# Public read
# ---------------------------------------------------------------------

def read_market_scan(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    lookback_days: int | None = None,
    source_ids: list[str] | None = None,
    now: datetime | None = None,
    max_items_total: int | None = None,
    reports: list[SourceReport] | None = None,
    status=None,
) -> list[MarketScanItem]:
    """Reads every configured feed and returns what was published in the window.

    All parameters override config rather than replacing it, so a caller can
    narrow a run (a single source, a longer window) without a second config
    file. Returns an empty list (never raises) when the config is missing or
    empty, when every source fails, or when nothing published inside the
    window. Pass `reports` to receive the per-source outcome of the run,
    which is what gap detection (build order item 11) will want.

    `status` is an optional `read_status.ReadStatus` carrying the one-line
    verdict for the whole scan, derived from those same per-source reports.
    Ten feeds where nine answered and one timed out is a healthy run, and ten
    where none answered is a broken network; both returned a short list, and
    until now the runner could not tell them apart.

    Items come back newest first, with undated items last.
    """
    config = load_config(config_path)
    sources = config.get("sources") or []
    if not isinstance(sources, list) or not sources:
        _warn("config lists no sources; returning nothing.")
        read_status.fill(
            status, read_status.UNCONFIGURED,
            f"{config_path} lists no sources",
        )
        return []

    if source_ids:
        wanted = set(source_ids)
        sources = [s for s in sources if isinstance(s, dict) and s.get("id") in wanted]
        missing = wanted - {s.get("id") for s in sources}
        for source_id in sorted(missing):
            _warn(f"requested source {source_id!r} is not in the config; ignoring it.")
        if not sources:
            read_status.fill(
                status, read_status.UNCONFIGURED,
                f"none of the requested sources ({', '.join(sorted(wanted))}) is in "
                f"{config_path}, so no feed was asked",
            )
            return []

    window_days = lookback_days if lookback_days is not None else _config_int(
        config, "lookback_days", _FALLBACK_LOOKBACK_DAYS
    )
    per_source_cap = _config_int(config, "max_items_per_source", _FALLBACK_MAX_PER_SOURCE)
    total_cap = max_items_total if max_items_total is not None else _config_int(
        config, "max_items_total", _FALLBACK_MAX_TOTAL
    )
    summary_chars = _config_int(config, "summary_max_chars", _FALLBACK_SUMMARY_CHARS)
    keep_undated = str(config.get("undated_items", "include")).lower() != "exclude"
    relevance = config.get("relevance") or {}
    if not isinstance(relevance, dict):
        _warn("config key 'relevance' is not a mapping; filtering nothing.")
        relevance = {}

    http = config.get("http") or {}
    if not isinstance(http, dict):
        http = {}
    timeout = float(http.get("timeout_seconds") or _FALLBACK_TIMEOUT)
    user_agent = str(http.get("user_agent") or _FALLBACK_USER_AGENT)

    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = reference - timedelta(days=window_days) if window_days > 0 else None

    collected: list[MarketScanItem] = []
    asked: list[SourceReport] = []
    for source in sources:
        if not isinstance(source, dict) or not source.get("url"):
            _warn(f"skipping malformed source entry {source!r}.")
            continue
        report = _read_one_source(
            source=source,
            cutoff=cutoff,
            per_source_cap=per_source_cap,
            summary_chars=summary_chars,
            keep_undated=keep_undated,
            relevance=relevance,
            timeout=timeout,
            user_agent=user_agent,
            into=collected,
        )
        asked.append(report)
        if reports is not None:
            reports.append(report)

    # Newest first, undated last. Two stable sorts rather than one composite
    # key, since the date half sorts descending and the dated/undated half
    # sorts ascending.
    collected.sort(key=lambda i: i.published_at or "", reverse=True)
    collected.sort(key=lambda i: i.published_at is None)

    if total_cap and len(collected) > total_cap:
        _warn(f"kept the {total_cap} newest of {len(collected)} matching items (max_items_total).")
        collected = collected[:total_cap]

    # The verdict for the scan as a whole, from the per-source reports this
    # run just produced. A feed that failed is only news when enough of them
    # did that we learned nothing: this reader's whole design is that one dead
    # feed is survivable, so one dead feed is not a broken source.
    if not asked:
        # Every configured source was filtered out, so nothing was asked and
        # an empty result says nothing about the world.
        read_status.fill(
            status, read_status.UNCONFIGURED,
            "no configured source matched this run, so no feed was asked",
        )
    else:
        failed = [r for r in asked if not r.ok]
        if len(failed) == len(asked):
            read_status.fill(
                status, read_status.UNREACHABLE,
                f"all {len(asked)} feeds failed; first was {failed[0].source_id}: "
                f"{failed[0].detail}",
            )
        elif not collected:
            read_status.fill(
                status, read_status.EMPTY,
                f"{len(asked) - len(failed)} of {len(asked)} feeds answered and nothing "
                "published inside the window",
            )
        elif failed:
            _warn(
                f"{len(failed)} of {len(asked)} feeds failed; the scan is partial but usable."
            )
    return collected


def _read_one_source(
    source: dict[str, Any],
    cutoff: datetime | None,
    per_source_cap: int,
    summary_chars: int,
    keep_undated: bool,
    relevance: dict[str, Any],
    timeout: float,
    user_agent: str,
    into: list[MarketScanItem],
) -> SourceReport:
    """Reads one feed into `into`. Never raises; failures come back as a report."""
    source_id = str(source.get("id") or source.get("url"))
    source_name = str(source.get("name") or source_id)
    lens = source.get("lens")
    # A source can turn the keyword filter off for itself (`relevance: off`),
    # for feeds whose whole output is on-lens. Exclusions still apply.
    # YAML reads a bare `off` as boolean false, so both spellings mean off.
    if source.get("relevance") is False or str(source.get("relevance", "")).lower() == "off":
        relevance = {**relevance, "mode": "off"}

    body = fetch_feed(str(source["url"]), timeout=timeout, user_agent=user_agent)
    if body is None:
        return SourceReport(source_id, source_name, ok=False, detail="unreachable or refused")

    try:
        raw_items = parse_feed(body)
    except ValueError as exc:
        looks_like_html = body.lstrip()[:200].lower().startswith((b"<!doctype html", b"<html"))
        detail = "returned HTML, not a feed" if looks_like_html else f"unparseable XML ({exc})"
        _warn(f"{source_id}: {detail}; skipping this source.")
        return SourceReport(source_id, source_name, ok=False, detail=detail)

    if not raw_items:
        _warn(f"{source_id}: feed parsed but contained no items.")
        return SourceReport(source_id, source_name, ok=True, detail="no items in feed")

    kept = 0
    for fields in raw_items:
        if kept >= per_source_cap:
            break
        title = _clean_text(_first(fields, _TITLE_TAGS), summary_chars)
        if not title:
            continue

        published = parse_date(_first(fields, _DATE_TAGS))
        if published is None and not keep_undated:
            continue
        if published is not None and cutoff is not None and published < cutoff:
            continue

        summary = _clean_text(_first(fields, _SUMMARY_TAGS), summary_chars)
        hits = matched_terms({"title": title, "summary": summary}, relevance)
        if hits is None:
            continue

        url = canonicalize_url(_first(fields, _LINK_TAGS))
        guid = _first(fields, _GUID_TAGS)
        item_id, id_basis = _stable_item_id(source_id, url, guid, title)

        into.append(
            MarketScanItem(
                item_id=item_id,
                id_basis=id_basis,
                source_id=source_id,
                source_name=source_name,
                title=title,
                url=url,
                published_at=published.astimezone(timezone.utc).isoformat() if published else None,
                summary=summary,
                lens=str(lens) if lens else None,
                matched_terms=hits,
            )
        )
        kept += 1

    return SourceReport(
        source_id, source_name, ok=True, items_kept=kept, items_seen=len(raw_items)
    )


if __name__ == "__main__":
    # Standalone run: reads the real configured feeds live and prints what
    # the outside world published inside the window. Run it from ingestion/,
    # the way README.md documents the other readers.
    parser = argparse.ArgumentParser(
        description="Weekly market scan for the Thought Leadership Calendar Manager."
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="path to the source config")
    parser.add_argument("--lookback-days", type=int, default=None,
                        help="override the configured window; 0 disables date filtering entirely")
    parser.add_argument("--source", action="append", dest="sources", default=None,
                        help="limit the run to this source id (repeatable)")
    parser.add_argument("--limit", type=int, default=None, help="override the configured total cap")
    args = parser.parse_args()

    run_reports: list[SourceReport] = []
    items = read_market_scan(
        config_path=args.config,
        lookback_days=args.lookback_days,
        source_ids=args.sources,
        max_items_total=args.limit,
        reports=run_reports,
    )

    print(f"Scanned {len(run_reports)} source(s) from {args.config}")
    for report in run_reports:
        state = "ok" if report.ok else "FAILED"
        detail = f" ({report.detail})" if report.detail else ""
        print(f"  [{state:6s}] {report.source_id}: kept {report.items_kept} of {report.items_seen}{detail}")

    print(f"\nFound {len(items)} item(s) in the window")
    for item in items:
        when = item.published_at[:10] if item.published_at else "undated"
        print(f"- [{when}] {item.source_id}: {item.title}")
        print(f"    id={item.item_id} (from {item.id_basis}) matched={item.matched_terms}")
        if item.url:
            print(f"    {item.url}")
