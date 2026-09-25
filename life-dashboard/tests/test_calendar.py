import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from connectors.gcal import day_window, parse_events
from dashboard import google_auth
from dashboard.config import load_config
from dashboard.google_auth import CALENDAR_READONLY, GMAIL_READONLY, NotAuthorized, check_readonly

CLASS = {
    "summary": "CMSC 14100",
    "location": "Ryerson 251",
    "htmlLink": "https://www.google.com/calendar/event?eid=abc",
    "status": "confirmed",
    "start": {"dateTime": "2026-09-25T09:30:00-05:00"},
    "end": {"dateTime": "2026-09-25T10:50:00-05:00"},
}
ALL_DAY = {"summary": "Mom's birthday", "start": {"date": "2026-09-25"}, "end": {"date": "2026-09-26"}}
DECLINED = {**CLASS, "summary": "Skipped talk", "attendees": [{"self": True, "responseStatus": "declined"}]}
CANCELLED = {**CLASS, "summary": "Cancelled", "status": "cancelled"}
WORKING_LOCATION = {**CLASS, "summary": "Home", "eventType": "workingLocation"}

PERSONAL = {"id": "uchicago", "section": "personal"}
CANVAS = {"id": "canvas", "section": "personal", "kind": "deadlines"}
QOFAI = {"id": "qofai", "section": "qofai"}


def test_parse_events_basic():
    [item] = parse_events([CLASS], PERSONAL)
    assert item.source == "calendar.uchicago"
    assert item.title == "CMSC 14100" and item.summary == "Ryerson 251"
    assert item.timestamp == "2026-09-25T09:30:00-05:00"
    assert item.link.startswith("https://") and item.due is None and item.section == "personal"


def test_parse_events_skips_noise():
    items = parse_events([CLASS, DECLINED, CANCELLED, WORKING_LOCATION], PERSONAL)
    assert [i.title for i in items] == ["CMSC 14100"]


def test_all_day_and_deadlines_and_section():
    [bday] = parse_events([ALL_DAY], PERSONAL)
    assert bday.timestamp == "2026-09-25"

    [pset] = parse_events([{**CLASS, "summary": "PSet 1"}], CANVAS)
    assert pset.due == pset.timestamp and pset.urgency_hints == ["due_today"]

    [standup] = parse_events([CLASS], QOFAI)
    assert standup.section == "qofai"


def test_day_window_uses_config_timezone():
    now = datetime(2026, 9, 25, 23, 30, tzinfo=ZoneInfo("America/Los_Angeles"))  # 1:30 AM Sep 26 in Chicago
    start, end = day_window(load_config(), now)
    assert start == "2026-09-26T00:00:00-05:00" and end == "2026-09-27T00:00:00-05:00"


def test_readonly_scopes_enforced():
    check_readonly([CALENDAR_READONLY, GMAIL_READONLY])
    with pytest.raises(NotAuthorized):
        check_readonly([CALENDAR_READONLY, "https://www.googleapis.com/auth/calendar"])
    with pytest.raises(NotAuthorized):
        check_readonly(["https://www.googleapis.com/auth/gmail.send"])


def test_session_refuses_missing_or_broad_tokens(tmp_path, monkeypatch):
    monkeypatch.setattr(google_auth, "TOKEN_DIR", tmp_path)
    with pytest.raises(NotAuthorized, match="no Google token"):
        google_auth.session("personal", CALENDAR_READONLY)

    (tmp_path / "google-personal.json").write_text(json.dumps({
        "token": "x", "refresh_token": "y", "client_id": "c", "client_secret": "s",
        "scopes": ["https://www.googleapis.com/auth/calendar"],
    }))
    with pytest.raises(NotAuthorized, match="non-read-only"):
        google_auth.session("personal", CALENDAR_READONLY)


def test_config_calendars_are_well_formed():
    config = load_config()
    assert config["google_account"]
    for cal in config["calendars"]:
        assert cal["section"] in ("personal", "qofai")
        assert "google_id" in cal
