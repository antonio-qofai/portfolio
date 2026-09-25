"""Run every connector, normalize, pick Pressing actions, save the brief.

One failing connector produces an error result instead of stopping the run.
Each connector's last good result is cached in data/cache/, so a failure
shows the previous data (marked stale) rather than a blank card.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from connectors import REGISTRY
from dashboard.config import ROOT
from dashboard.schema import ConnectorResult, Item

Fetch = Callable[[dict], list[Item]]

DATA_DIR = ROOT / "data"
URGENCY_ORDER = {"overdue": 0, "due_today": 1, "reply_needed": 2, "deadline": 3}


def _now(config: dict) -> datetime:
    return datetime.now(ZoneInfo(config.get("timezone", "America/Chicago")))


def _write_cache(cache_dir: Path, result: ConnectorResult) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{result.name}.json").write_text(json.dumps(result.to_dict(), ensure_ascii=False))


def _read_cache(cache_dir: Path, name: str) -> ConnectorResult | None:
    path = cache_dir / f"{name}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        items = [Item(**i) for i in data["items"]]
    except (ValueError, KeyError, TypeError):
        return None
    return ConnectorResult(name=name, items=items, last_updated=data.get("last_updated"))


def run_connectors(
    config: dict,
    registry: dict[str, Fetch] | None = None,
    cache_dir: Path | None = None,
) -> dict[str, ConnectorResult]:
    registry = REGISTRY if registry is None else registry
    cache_dir = cache_dir or DATA_DIR / "cache"
    results = {}
    for name, fetch in registry.items():
        stamp = _now(config).isoformat(timespec="seconds")
        try:
            result = ConnectorResult(name=name, items=fetch(config), last_updated=stamp)
            _write_cache(cache_dir, result)
        except Exception as e:  # noqa: BLE001 - any connector failure becomes an error card
            error = f"{type(e).__name__}: {e}"
            result = _read_cache(cache_dir, name) or ConnectorResult(name=name, last_updated=stamp)
            result.error = error
            result.stale = bool(result.items)
        results[name] = result
    return results


def pressing_actions(results: dict[str, ConnectorResult], cap: int) -> list[Item]:
    """M0 placeholder ranking: personal items with urgency hints, most urgent first.

    Replaced by the LLM ranking in M5. QofAI items are always excluded.
    """
    candidates = [
        item
        for r in results.values()
        for item in r.items
        if item.section == "personal" and item.urgency_hints
    ]

    def key(item: Item) -> tuple[int, str]:
        rank = min(URGENCY_ORDER.get(h, 9) for h in item.urgency_hints)
        return rank, item.due or "9999"

    return sorted(candidates, key=key)[:cap]


def build_brief(
    config: dict,
    registry: dict[str, Fetch] | None = None,
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    results = run_connectors(config, registry, cache_dir)
    return {
        "generated_at": _now(config).isoformat(timespec="seconds"),
        "results": results,
        "actions": pressing_actions(results, config["actions"]["cap"]),
    }


def save_brief(brief: dict[str, Any], path: Path | None = None) -> Path:
    path = path or DATA_DIR / "brief.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "generated_at": brief["generated_at"],
        "results": {k: v.to_dict() for k, v in brief["results"].items()},
        "actions": [i.to_dict() for i in brief["actions"]],
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return path


def last_generated_at(path: Path | None = None) -> datetime | None:
    path = path or DATA_DIR / "brief.json"
    try:
        return datetime.fromisoformat(json.loads(path.read_text())["generated_at"])
    except (OSError, ValueError, KeyError):
        return None
