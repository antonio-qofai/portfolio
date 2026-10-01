"""The page's write endpoints, kept apart from the HTTP handler so tests can call them.

GET  /api/state     which items in the current brief are checked, and their feedback
POST /api/check     {"key": str, "done": bool}
POST /api/feedback  {"key": str, "feedback": "too_early" | "too_late" | "not_needed"}
POST /api/refresh   start a rebuild (no body); one at a time
GET  /api/refresh   {"running": bool, "failed": bool} for the last rebuild
GET  /api/qofai     {"todos": [...]} QofAI to-dos from the quick-add box
POST /api/qofai/add   {"text": str}; a trailing date becomes the due date
POST /api/qofai/done  {"id": str, "done": bool}

Keys must belong to the current data/brief.json; item details come from the
brief, never from the request.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from dashboard import store, worklist
from dashboard.refresh import Refresher

MAX_BODY = 1024


def _brief(data_dir: Path) -> dict[str, Any]:
    try:
        return json.loads((data_dir / "brief.json").read_text())
    except (OSError, ValueError):
        return {}


def handle(
    method: str, path: str, body: bytes, data_dir: Path, now: datetime, refresher: Refresher | None = None,
) -> tuple[int, dict]:
    if path == "/api/refresh" and refresher is not None:
        if method == "POST":
            return 202, refresher.start()
        if method == "GET":
            return 200, refresher.status()
    if path.startswith("/api/qofai"):
        return _qofai(method, path, body, data_dir, now)
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


def _qofai(method: str, path: str, body: bytes, data_dir: Path, now: datetime) -> tuple[int, dict]:
    if method == "GET" and path == "/api/qofai":
        return 200, {"todos": worklist.visible(data_dir, now.date())}
    if method != "POST" or path not in ("/api/qofai/add", "/api/qofai/done"):
        return 404, {"error": "not found"}
    try:
        req = json.loads(body)
        if path == "/api/qofai/add":
            if not isinstance(req.get("text"), str):
                return 400, {"error": "text must be a string"}
            return 200, {"todo": worklist.add(data_dir, req["text"], now)}
        if not isinstance(req.get("id"), str) or not isinstance(req.get("done"), bool):
            return 400, {"error": "id and done required"}
    except worklist.TodoError as e:
        return 400, {"error": str(e)}
    except (ValueError, TypeError, AttributeError):
        return 400, {"error": "bad request"}
    if not worklist.set_done(data_dir, req["id"], req["done"], now):
        return 404, {"error": "no such to-do"}
    return 200, {"ok": True}
