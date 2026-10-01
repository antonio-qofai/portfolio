import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from dashboard import actions, api, worklist
from dashboard.config import load_config
from dashboard.render import render
from dashboard.schema import ConnectorResult, Item
from tests.test_actions import EXAM, FakeClient, ev

TZ = ZoneInfo("America/Chicago")
NOW = datetime(2026, 9, 29, 8, 0, tzinfo=TZ)  # a Tuesday
TODAY = NOW.date()
DEMO = ev("Client demo", "2026-10-01T14:00:00-05:00", source="calendar.qofai", section="qofai")      # Thu
SYNC = ev("Weekly sync", "2026-09-29T13:30:00-05:00", source="calendar.qofai", section="qofai")      # today
NEXT_WEEK = ev("Planning", "2026-10-07T10:00:00-05:00", source="calendar.qofai", section="qofai")   # outside 7 days


@pytest.fixture
def config():
    return load_config()


# --- Prep notes ------------------------------------------------------------

def test_qofai_prep_stays_off_personal_lists(config, tmp_path):
    client = FakeClient(
        {"events": [{"id": "e0", "importance": "high", "lead_days": 7, "prep": "Review notes"},
                    {"id": "e1", "importance": "high", "lead_days": 3, "prep": "Rehearse the demo flow"}]},
        {"actions": []},
    )
    cal = ConnectorResult("calendar", [EXAM, DEMO])
    out = actions.select({"calendar": cal}, config, NOW, tmp_path, client)
    assert [u.item.title for u in out["upcoming"]] == ["CMSC 14100 midterm"]
    assert [(u.item.title, u.why) for u in out["qofai_prep"]] == [("Client demo", "Rehearse the demo flow")]
    assert "Client demo" in client.prompts[0] and "Client demo" not in client.prompts[1]


def brief_with(config, cal_items, qofai_prep=()):
    return {
        "generated_at": NOW.isoformat(),
        "results": {"calendar": ConnectorResult("calendar", list(cal_items))},
        "actions": [], "upcoming": [], "qofai_prep": list(qofai_prep), "actions_note": None,
    }


def test_qofai_card_lists_the_week_with_prep(config):
    prep = [actions.surfaced(DEMO, "Rehearse the demo flow")]
    page = render(brief_with(config, [SYNC, DEMO, NEXT_WEEK], prep), config)
    card = page[page.index('id="qofai"'):page.index('id="job-search"')]
    assert "Today 1:30 PM" in card and "Thu 2:00 PM" in card and "Planning" not in card
    assert "Rehearse the demo flow" in card and 'data-date="2026-10-01"' in card
    assert 'id="qofai-add"' in card and 'id="qofai-todos"' in card
    assert "Rehearse the demo flow" not in page[:page.index('id="qofai"')]


def test_qofai_card_with_no_meetings(config):
    card = render(brief_with(config, []), config)
    assert "No work meetings this week." in card and 'id="qofai-add"' in card


# --- Quick-add -------------------------------------------------------------

@pytest.mark.parametrize("raw, text, due", [
    ("demo ready by Thu", "demo ready", date(2026, 10, 1)),
    ("send deck tomorrow", "send deck", date(2026, 9, 30)),
    ("review PR today", "review PR", TODAY),
    ("draft memo Tue", "draft memo", TODAY),                   # a weekday includes today
    ("board prep, due Oct 3", "board prep", date(2026, 10, 3)),
    ("invoice 10/15", "invoice", date(2026, 10, 15)),
    ("plan offsite Jan 5", "plan offsite", date(2027, 1, 5)),  # past dates roll to next year
    ("call with Sam", "call with Sam", None),
    ("meet at 3", "meet at 3", None),
    ("Thursday", "Thursday", None),                           # nothing left as text
])
def test_parse_due(raw, text, due):
    assert worklist.parse_due(raw, TODAY) == (text, due)


def test_add_check_off_and_drop_the_next_day(tmp_path):
    later = worklist.add(tmp_path, "write spec Fri", NOW)
    soon = worklist.add(tmp_path, "demo ready by Thu", NOW)
    undated = worklist.add(tmp_path, "call with Sam", NOW)
    assert [t["id"] for t in worklist.visible(tmp_path, TODAY)] == [soon["id"], later["id"], undated["id"]]

    assert worklist.set_done(tmp_path, soon["id"], True, NOW)
    assert worklist.visible(tmp_path, TODAY)[-1]["id"] == soon["id"]  # checked off today: still shown, last
    assert soon["id"] not in [t["id"] for t in worklist.visible(tmp_path, TODAY + timedelta(days=1))]
    assert not worklist.set_done(tmp_path, "nope", True, NOW)


def test_add_rejects_empty_long_and_control_text(tmp_path):
    for bad in ["", "   ", "x" * 201]:
        with pytest.raises(worklist.TodoError):
            worklist.add(tmp_path, bad, NOW)
    todo = worklist.add(tmp_path, "ship\x00 it\x1b", NOW)
    assert todo["text"] == "ship it"


def test_api_round_trip(tmp_path):
    def call(method, path, body=None):
        return api.handle(method, path, json.dumps(body).encode() if body is not None else b"", tmp_path, NOW)

    status, out = call("POST", "/api/qofai/add", {"text": "demo ready by Thu"})
    assert status == 200 and out["todo"]["due"] == "2026-10-01"
    assert call("POST", "/api/qofai/done", {"id": out["todo"]["id"], "done": True})[0] == 200
    assert call("GET", "/api/qofai")[1]["todos"][0]["done_at"]

    assert call("POST", "/api/qofai/add", {"text": 5})[0] == 400
    assert call("POST", "/api/qofai/add", {"text": ""}) == (400, {"error": "Type a to-do of up to 200 characters"})
    assert call("POST", "/api/qofai/done", {"id": "nope", "done": True})[0] == 404
    assert call("POST", "/api/qofai/done", {"id": out["todo"]["id"], "done": "yes"})[0] == 400
    assert call("POST", "/api/qofai/other", {})[0] == 404
    assert api.handle("POST", "/api/qofai/add", b"not json", tmp_path, NOW)[0] == 400
