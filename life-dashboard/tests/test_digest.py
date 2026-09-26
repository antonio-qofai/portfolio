import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from dashboard import actions, digest
from dashboard.config import load_config
from dashboard.pipeline import save_brief
from dashboard.schema import ConnectorResult, Item

TZ = ZoneInfo("America/Chicago")
NOW = datetime(2026, 9, 26, 6, 5, tzinfo=TZ)


def t(hour, minute=0, day=26):
    return datetime(2026, 9, day, hour, minute, tzinfo=TZ)


@pytest.fixture
def config():
    return load_config()


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(digest, "load_env", lambda: None)
    monkeypatch.setenv("GMAIL_ADDRESS", "me@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app-password")
    monkeypatch.setenv("DASHBOARD_URL", "https://mac.example.ts.net")


def brief(tmp_path, note=None, calendar_error=None):
    mail = Item(source="email.uchicago", title="Office hours?", urgency_hints=["reply_needed"])
    b = {
        "generated_at": NOW.isoformat(),
        "results": {
            "weather": ConnectorResult("weather", [Item(source="weather", title="58°F, clear", summary="High 66 / low 52.")]),
            "calendar": ConnectorResult("calendar", [
                Item(source="calendar.uchicago", title="STAT 22000", timestamp="2026-09-26T13:30:00-05:00"),
                Item(source="calendar.uchicago", title="Midterm", timestamp="2026-09-30T09:30:00-05:00"),
                Item(source="calendar.qofai", title="QofAI launch review", timestamp="2026-09-26T15:00:00-05:00",
                     section="qofai"),
            ], error=calendar_error),
            "email": ConnectorResult("email", [mail]),
            "chores": ConnectorResult("chores", [
                Item(source="chores", title="Take out recycling", due="2026-09-26", urgency_hints=["due_today"])]),
        },
        "actions": [actions.surfaced(mail, "Prof. Lee is waiting on a time")],
        "upcoming": [],
        "actions_note": note,
    }
    save_brief(b, tmp_path / "brief.json")
    return json.loads((tmp_path / "brief.json").read_text())


def test_body_has_top_of_brief_and_no_qofai(tmp_path):
    body = digest.compose_body(brief(tmp_path), "https://mac.example.ts.net")
    assert body.startswith("58°F, clear. High 66 / low 52.")
    assert "1. Office hours?\n   Prof. Lee is waiting on a time" in body
    assert "1:30 PM  STAT 22000" in body
    assert "Midterm" not in body, "upcoming event listed as today"
    assert "QofAI" not in body
    assert "Take out recycling · due today · due Sep 26" in body
    assert body.rstrip().endswith("Open the dashboard: https://mac.example.ts.net")


def test_body_shows_fallbacks_and_failures(tmp_path):
    body = digest.compose_body(brief(tmp_path, note="AI unavailable, simple rules stood in.", calendar_error="boom"), "")
    assert "AI unavailable" in body and "Failed to load: Calendar." in body
    assert "The full brief is on the dashboard." in body


def test_message_goes_to_self(tmp_path):
    msg = digest.compose(brief(tmp_path), "me@example.com", "")
    assert msg["From"] == msg["To"] == "me@example.com"
    assert msg["Subject"] == "Morning brief, Sat Sep 26"


def test_send_window(config):
    assert not digest.in_window(config, t(6, 59))
    assert digest.in_window(config, t(7)) and digest.in_window(config, t(11, 59))
    assert not digest.in_window(config, t(12))


def test_run_sends_once_a_day(config, tmp_path, env):
    brief(tmp_path)
    sent = []

    def fake_send(msg, address, password):
        sent.append((msg["Subject"], address, password))

    assert digest.run(config, t(6, 30), tmp_path, True, fake_send) == "outside the send window"
    assert digest.run(config, t(7), tmp_path, False, fake_send).startswith("today's brief isn't built")
    assert digest.run(config, t(7, 15), tmp_path, True, fake_send) == "sent"
    assert digest.run(config, t(7, 30), tmp_path, True, fake_send) == "already sent today"
    assert sent == [("Morning brief, Sat Sep 26", "me@example.com", "app-password")]
    assert digest.run(config, t(7, 15, day=27), tmp_path, True, fake_send) == "sent"


def test_run_retries_after_failure(config, tmp_path, env):
    brief(tmp_path)

    def broken(*_):
        raise digest.DigestError("SMTPAuthenticationError: bad credentials")

    assert digest.run(config, t(7), tmp_path, True, broken).startswith("failed, will retry")
    assert digest.last_sent(tmp_path) is None
    assert digest.run(config, t(7, 15), tmp_path, True, lambda *_: None) == "sent"


def test_run_needs_credentials(config, tmp_path, monkeypatch):
    brief(tmp_path)
    monkeypatch.setattr(digest, "load_env", lambda: None)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    out = digest.run(config, t(7), tmp_path, True, lambda *_: pytest.fail("sent without credentials"))
    assert "GMAIL_APP_PASSWORD" in out
