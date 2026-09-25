"""The page's write endpoints, kept apart from the HTTP handler so tests can call them.

GET  /api/state     which items in the current brief are checked, and their feedback
POST /api/check     {"key": str, "done": bool}
POST /api/feedback  {"key": str, "feedback": "too_early" | "too_late" | "not_needed"}

Keys must belong to the current data/brief.json; item details come from the
brief, never from the request.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from dashboard import store

MAX_BODY = 1024


def _brief(data_dir: Path) -> dict[str, Any]:
    try:
        return json.loads((data_dir / "brief.json").read_text())
    except (OSError, ValueError):
        return {}


def handle(method: str, path: str, body: bytes, data_dir: Path, now: datetime) -> tuple[int, dict]:
    brief = _brief(data_dir)
    if method == "GET" and path == "/api/state":
        return 200, store.page_state(data_dir, brief)
    if method != "POST" or path not in ("/api/check", "/api/feedback"):
        return 404, {"error": "not found"}
    try:
        req = json.loads(body)
        entry = store.find_surfaced(brief, str(req["key"]))
    except (ValueError, KeyError, TypeError):
        return 400, {"error": "bad request"}
    if entry is None:
        return 404, {"error": "item is not in the current brief"}
    if path == "/api/check":
        if not isinstance(req.get("done"), bool):
            return 400, {"error": "done must be true or false"}
        store.set_checked(data_dir, entry, req["done"], now)
    else:
        if req.get("feedback") not in store.FEEDBACK_KINDS:
            return 400, {"error": "unknown feedback"}
        store.add_feedback(data_dir, entry, req["feedback"], brief["generated_at"][:10], now)
    return 200, {"ok": True}
