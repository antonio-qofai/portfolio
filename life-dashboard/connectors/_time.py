from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def now(config: dict) -> datetime:
    return datetime.now(ZoneInfo(config.get("timezone", "America/Chicago")))


def at(config: dict, hour: int, minute: int = 0, days: int = 0) -> str:
    """ISO string for today (+days) at a local time. Used by stubs."""
    t = now(config).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return (t + timedelta(days=days)).isoformat()


def on(config: dict, days: int = 0) -> str:
    """ISO date string for today (+days)."""
    return (now(config) + timedelta(days=days)).date().isoformat()
