"""Value Creation Briefing's daily Briefing Insights, as the arc's outside world.

What this is for
----------------
The arc needs to know what the outside world is saying. Until 2026-09-22 that
came only from `market_scan_reader.py`, ten public RSS feeds we chose as a
stand-in, filtered by a deliberately wide keyword list and weighted toward
general AI tech press. PRD Section 5 item 6 said a QofAI market brief from
another agent would be a better input and that the scan should be replaced
when one existed.

One exists. Robin's Value Creation Briefing agent runs
`ai_brief_to_airtable.js` every morning: it reads about eleven AI sources, has
Claude score each item for relevance to a middle-market company, and writes
the High and Medium items to a "Briefing Insights" table. That is the same job
the RSS scan does, already filtered for our market by a model rather than a
keyword list. This module reads it.

What it returns
---------------
`MarketScanItem`s, the same type the RSS scan returns, so synthesis prompts
them the same way and either source can stand in for the other. The title is
recovered from the `Notes` field, which Robin's script writes as
"Article: <title>" so its own runs can deduplicate. The summary carries the
insight plus its relevance, theme, and industry vertical.

Access
------
The table sits in Robin's own Airtable base, not the program base the
conference reader uses, so a token that reads conferences may not read this.
The token comes from the same environment variables as the conference reader.
Nothing here has been verified against the live table yet: on 2026-09-22 the
first live read was blocked before it ran, so field names are Robin's as
written in `repos/value-creation-briefing/ai_brief_to_airtable.js`.

Degradation follows `conference_reader.py`: a missing token, a refused token,
an unreachable API, or an empty window returns no items and a sentence saying
which, never an exception. `synthesis/assemble.py` falls back to the RSS scan
when that happens, so the arc is never thinner than it was before this module.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import requests

from market_scan_reader import MarketScanItem

_API_ROOT = "https://api.airtable.com/v0"
_TOKEN_ENV_VARS = ("AIRTABLE_API_KEY", "AIRTABLE_TOKEN", "AIRTABLE_PAT")

SOURCE_ID = "value-creation-briefing-insights"

# Robin's field names, exactly as `ai_brief_to_airtable.js` writes them.
FIELD_THEME = "Theme"
FIELD_SUMMARY = "Insight Summary"
FIELD_SOURCE = "Source Name"
FIELD_RELEVANCE = "Middle Market Relevance"
FIELD_VERTICAL = "Industry Vertical"
FIELD_NOTES = "Notes"


def _resolve_token(token: str | None) -> str | None:
    if token:
        return token
    for var in _TOKEN_ENV_VARS:
        value = os.environ.get(var)
        if value:
            return value
    return None


def _title_from_notes(notes: str) -> str:
    """The article title Robin's script prepends to Notes, if it is there."""
    first = (notes or "").strip().splitlines()[0] if (notes or "").strip() else ""
    return first[len("Article:"):].strip() if first.startswith("Article:") else ""


def _item_from_record(record: dict[str, Any]) -> MarketScanItem:
    fields = record.get("fields") or {}
    source = str(fields.get(FIELD_SOURCE) or "").strip() or "unknown source"
    theme = str(fields.get(FIELD_THEME) or "").strip()
    title = _title_from_notes(str(fields.get(FIELD_NOTES) or "")) or theme or "untitled insight"
    labels = [
        f"{label}: {value}"
        for label, value in (
            ("relevance", fields.get(FIELD_RELEVANCE)),
            ("theme", theme),
            ("vertical", fields.get(FIELD_VERTICAL)),
        )
        if value
    ]
    summary = " ".join(str(fields.get(FIELD_SUMMARY) or "").split())
    if labels:
        summary = f"{summary} ({'; '.join(labels)})" if summary else f"({'; '.join(labels)})"
    return MarketScanItem(
        item_id=f"{SOURCE_ID}/{record.get('id')}",
        id_basis="airtable",
        source_id=SOURCE_ID,
        source_name=f"{source} (via Value Creation Briefing)",
        title=title,
        published_at=record.get("createdTime"),
        summary=summary,
    )


def read_briefing_insights(
    base_id: str,
    table: str,
    *,
    lookback_days: int = 14,
    max_items: int = 25,
    relevance: list[str] | None = None,
    token: str | None = None,
    timeout: float = 30.0,
    now: datetime | None = None,
    get: Callable[..., Any] = requests.get,
) -> tuple[list[MarketScanItem], str]:
    """Insights created in the lookback window, newest first, capped.

    Returns `(items, detail)`. `detail` is empty when items came back, and
    otherwise one sentence naming which kind of nothing this is, for the
    assembly notes and the fallback decision.
    """
    resolved = _resolve_token(token)
    if not resolved:
        return [], f"no Airtable token in {', '.join(_TOKEN_ENV_VARS)}"

    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=lookback_days)
    wanted = {r.lower() for r in (relevance or [])}
    url = f"{_API_ROOT}/{base_id}/{requests.utils.quote(table)}"
    headers = {"Authorization": f"Bearer {resolved}"}
    # Filtered server-side so the read stays one or two pages however large
    # the table grows. Airtable evaluates the formula against record creation.
    formula = f"IS_AFTER(CREATED_TIME(), DATEADD(NOW(), -{int(lookback_days)}, 'days'))"

    records: list[dict[str, Any]] = []
    offset: str | None = None
    try:
        while True:
            params = {"filterByFormula": formula, "pageSize": 100}
            if offset:
                params["offset"] = offset
            response = get(url, headers=headers, params=params, timeout=timeout)
            if response.status_code != 200:
                kind = "refused the token" if response.status_code in (401, 403) else "failed"
                return [], (
                    f"Airtable {kind} (HTTP {response.status_code}) for {base_id}/{table}"
                )
            payload = response.json()
            records.extend(payload.get("records") or [])
            offset = payload.get("offset")
            if not offset:
                break
    except requests.RequestException as exc:
        return [], f"could not reach Airtable: {exc}"

    items = []
    for record in records:
        fields = record.get("fields") or {}
        if wanted and str(fields.get(FIELD_RELEVANCE) or "").lower() not in wanted:
            continue
        created = record.get("createdTime") or ""
        try:
            if datetime.fromisoformat(created.replace("Z", "+00:00")) < cutoff:
                continue
        except ValueError:
            pass  # an undated record is kept; the server-side filter already ran
        items.append(_item_from_record(record))

    items.sort(key=lambda item: item.published_at or "", reverse=True)
    items = items[:max_items] if max_items else items
    if not items:
        return [], f"no insights in the last {lookback_days} days"
    return items, ""


if __name__ == "__main__":
    # A read-only look at the live table: base and table as arguments,
    # token from the environment.
    if len(sys.argv) < 3:
        print("usage: briefing_insights_reader.py <base_id> <table>", file=sys.stderr)
        sys.exit(2)
    found, why = read_briefing_insights(sys.argv[1], sys.argv[2])
    print(f"{len(found)} insight(s){': ' + why if why else ''}")
    for item in found[:10]:
        print(f"- [{item.published_at}] {item.source_name}: {item.title}")
