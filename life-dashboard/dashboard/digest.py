"""Morning email digest (M6): a short plain-text copy of the brief's top half.

Pressing actions with their why, today's personal events, chores, the weather
line, and a link to the live page for everything else and the buttons. QofAI
items are never included. It is read from data/brief.json, so it matches the
page exactly.

Sent once a day from the user's Gmail to itself over SMTP with an app password
(GMAIL_ADDRESS, GMAIL_APP_PASSWORD in .env; this is not an OAuth scope, and it
is the only thing in the project that sends). It goes out at `digest.hour`, or
on wake up to `digest.until_hour`, and only once the day's brief exists.
data/digests.log records each attempt.
"""

from __future__ import annotations

import json
import os
import smtplib
import ssl
from datetime import date, datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from dashboard.actions import split_events
from dashboard.config import load_env
from dashboard.schema import Item

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
CARDS = {"weather": "Weather", "calendar": "Calendar", "email": "Inbox", "chores": "Chores"}


class DigestError(RuntimeError):
    pass


def _time(iso: str) -> str:
    return "All day" if len(iso) == 10 else datetime.fromisoformat(iso).strftime("%-I:%M %p")


def _due(iso: str | None) -> str:
    if not iso:
        return ""
    t = datetime.fromisoformat(iso)
    return "due " + (t.strftime("%b %-d") if len(iso) == 10 else t.strftime("%b %-d, %-I:%M %p"))


def _items(brief: dict, name: str) -> list[Item]:
    result = brief["results"].get(name) or {}
    return [Item(**i) for i in result.get("items", []) if i.get("section") == "personal"]


def subject(brief: dict) -> str:
    return "Morning brief, " + datetime.fromisoformat(brief["generated_at"]).strftime("%a %b %-d")


def compose_body(brief: dict, link: str) -> str:
    now = datetime.fromisoformat(brief["generated_at"])
    lines: list[str] = []

    weather = _items(brief, "weather")
    if weather:
        lines += [f"{weather[0].title}. {weather[0].summary}".strip(), ""]

    lines.append("PRESSING ACTIONS")
    if brief.get("actions_note"):
        lines.append(brief["actions_note"])
    actions = [a for a in brief.get("actions", []) if a["item"].get("section") == "personal"]
    for n, a in enumerate(actions, 1):
        lines.append(f"{n}. {a['item']['title']}")
        if a.get("why"):
            lines.append(f"   {a['why']}")
    if not actions:
        lines.append("Nothing pressing.")

    events = sorted(split_events(_items(brief, "calendar"), now)[0], key=lambda e: e.timestamp or "")
    lines += ["", "TODAY"]
    lines += [f"{_time(e.timestamp)}  {e.title}" for e in events] or ["No events."]

    chores = _items(brief, "chores")
    lines += ["", "CHORES"]
    lines += [
        " · ".join(filter(None, [c.title, ", ".join(h.replace("_", " ") for h in c.urgency_hints), _due(c.due)]))
        for c in chores
    ] or ["No chores due."]

    failed = [
        label for name, label in CARDS.items()
        if (brief["results"].get(name) or {}).get("error")
    ]
    if failed:
        lines += ["", f"Failed to load: {', '.join(failed)}. The page shows the last good data."]

    lines += ["", f"Open the dashboard: {link}" if link else "The full brief is on the dashboard."]
    return "\n".join(lines) + "\n"


def compose(brief: dict, address: str, link: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = address
    msg["To"] = address
    msg["Subject"] = subject(brief)
    msg.set_content(compose_body(brief, link))
    return msg


def credentials() -> tuple[str, str]:
    load_env()
    address = os.environ.get("GMAIL_ADDRESS", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    if not address or not password:
        raise DigestError("GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set in .env")
    return address, password


def send(msg: EmailMessage, address: str, password: str) -> None:
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ssl.create_default_context(), timeout=30) as smtp:
            smtp.login(address, password)
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        raise DigestError(f"{type(e).__name__}: {e}") from e


# --- When to send ----------------------------------------------------------

def last_sent(data_dir: Path) -> date | None:
    try:
        lines = (data_dir / "digests.log").read_text().splitlines()
    except OSError:
        return None
    sent = [line.split("\t")[0] for line in lines if line.endswith("\tsent")]
    return datetime.fromisoformat(sent[-1]).date() if sent else None


def in_window(config: dict, now: datetime) -> bool:
    d = config["digest"]
    start = now.replace(hour=d["hour"], minute=d["minute"], second=0, microsecond=0)
    return start <= now < now.replace(hour=d["until_hour"], minute=0, second=0, microsecond=0)


def _log(data_dir: Path, now: datetime, status: str) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    with open(data_dir / "digests.log", "a") as f:
        f.write(f"{now.isoformat(timespec='seconds')}\t{status}\n")


def run(config: dict, now: datetime, data_dir: Path, brief_current: bool, sender: Any = None) -> str:
    """Send today's digest if it's due. Returns what happened, for the job log.

    `sender(msg, address, password)` defaults to SMTP; tests pass a fake.
    """
    if last_sent(data_dir) == now.date():
        return "already sent today"
    if not in_window(config, now):
        return "outside the send window"
    if not brief_current:
        return "today's brief isn't built yet; will retry"
    try:
        brief = json.loads((data_dir / "brief.json").read_text())
        address, password = credentials()
        link = os.environ.get("DASHBOARD_URL", "").strip()
        (sender or send)(compose(brief, address, link), address, password)
    except (DigestError, OSError, ValueError, KeyError) as e:
        _log(data_dir, now, f"failed: {e}")
        return f"failed, will retry: {e}"
    _log(data_dir, now, "sent")
    return "sent"
