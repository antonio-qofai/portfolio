"""Run every connector, normalize, pick Pressing actions, save the brief.

Pressing actions and Coming up come from dashboard/actions.py (LLM ranking and
lead times, with rule fallbacks).

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
from dashboard import actions
from dashboard.config import ROOT
from dashboard.schema import ConnectorResult, Item

Fetch = Callable[[dict], list[Item]]

DATA_DIR = ROOT / "data"


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


def build_brief(
    config: dict,
    registry: dict[str, Fetch] | None = None,
    cache_dir: Path | None = None,
    data_dir: Path | None = None,
    client: Any = None,
) -> dict[str, Any]:
    """data_dir holds check-offs and feedback; client is the Claude client (a fake in tests)."""
    data_dir = data_dir or DATA_DIR
    results = run_connectors(config, registry, cache_dir or data_dir / "cache")
    now = _now(config)
    picked = actions.select(results, config, now, data_dir, client)
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "results": results,
        "actions": picked["actions"],
        "upcoming": picked["upcoming"],
        "actions_note": picked["note"],
    }


def save_brief(brief: dict[str, Any], path: Path | None = None) -> Path:
    path = path or DATA_DIR / "brief.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "generated_at": brief["generated_at"],
        "results": {k: v.to_dict() for k, v in brief["results"].items()},
        "actions": [a.to_dict() for a in brief["actions"]],
        "upcoming": [u.to_dict() for u in brief["upcoming"]],
        "actions_note": brief["actions_note"],
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return path


def last_generated_at(path: Path | None = None) -> datetime | None:
    path = path or DATA_DIR / "brief.json"
    try:
        return datetime.fromisoformat(json.loads(path.read_text())["generated_at"])
    except (OSError, ValueError, KeyError):
        return None
