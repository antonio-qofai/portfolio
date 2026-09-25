"""Google Calendar: today's events from the calendars listed in config.yaml.

Named gcal to avoid shadowing the stdlib `calendar` module. Read-only scope
(calendar.readonly) through one Google account; UChicago, QofAI and Canvas
are calendars subscribed into that account. Only title, time, location and
link are fetched, never descriptions.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dashboard.google_auth import CALENDAR_READONLY, session
from dashboard.schema import Item

EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/{cal}/events"
FIELDS = (
    "nextPageToken,"
    "items(summary,start,end,htmlLink,location,status,eventType,attendees(self,responseStatus))"
)
SKIP_EVENT_TYPES = {"workingLocation", "outOfOffice", "focusTime"}


def day_window(config: dict, now: datetime | None = None) -> tuple[str, str]:
    tz = ZoneInfo(config.get("timezone", "America/Chicago"))
    now = now or datetime.now(tz)
    start = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return start.isoformat(), (start + timedelta(days=1)).isoformat()


def _declined(event: dict) -> bool:
    return any(a.get("self") and a.get("responseStatus") == "declined" for a in event.get("attendees", []))


def parse_events(events: list[dict], cal: dict) -> list[Item]:
    """Convert raw Calendar API events for one configured calendar into Items."""
    items = []
    for ev in events:
        if ev.get("status") == "cancelled" or _declined(ev) or ev.get("eventType") in SKIP_EVENT_TYPES:
            continue
        start = ev.get("start", {})
        when = start.get("dateTime") or start.get("date")
        if not when:
            continue
        is_deadline = cal.get("kind") == "deadlines"
        items.append(
            Item(
                source=f"calendar.{cal['id']}",
                title=ev.get("summary") or "(no title)",
                summary=ev.get("location", ""),
                link=ev.get("htmlLink", ""),
                timestamp=when,
                due=when if is_deadline else None,
                urgency_hints=["due_today"] if is_deadline else [],
                section=cal["section"],
            )
        )
    return items


def fetch(config: dict) -> list[Item]:
    http = session(config["google_account"], CALENDAR_READONLY)
    time_min, time_max = day_window(config)
    items = []
    for cal in config["calendars"]:
        if not cal.get("google_id"):
            continue  # not mapped yet; see `scripts/google_auth.py calendars`
        params = {
            "timeMin": time_min,
            "timeMax": time_max,
            "singleEvents": "true",
            "orderBy": "startTime",
            "timeZone": config.get("timezone", "America/Chicago"),
            "fields": FIELDS,
            "maxResults": 250,
        }
        events = []
        while True:
            resp = http.get(EVENTS_URL.format(cal=cal["google_id"]), params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            events.extend(data.get("items", []))
            if not data.get("nextPageToken"):
                break
            params["pageToken"] = data["nextPageToken"]
        items.extend(parse_events(events, cal))
    return items
