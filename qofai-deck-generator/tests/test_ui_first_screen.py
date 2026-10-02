"""The first screen, and the two defects a reviewer meets before a deck exists.

Casey spent two sessions on the Generate form without reaching a deck. These
are what stopped him, from `NEXT-BUILD-PHASE.md` part two:

Item 18, the check-in date. It was asked on every run and belongs to a status
deck only, so half the runs were asked for a value their deck has nowhere to
put, and it was a free-text box with an example date in the placeholder rather
than a calendar.

Item 17, the ambiguous company. A name matching more than one registry record
raised `E_AMBIGUOUS_COMPANY` and told the reviewer to re-request with a
`company_id`, which is a value no reviewer has and no screen offered.

There is no JS harness in this repo, so the browser-side halves are asserted on
the shipped script the way `test_ui_walkthrough_fixes` already asserts on it,
and each was also driven in a real browser.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ui"))

pytest.importorskip("flask")

import app as ui_app  # noqa: E402
from deck_run import run_deck  # noqa: E402
from live_proposal_provider import LiveProposalProvider  # noqa: E402

FIXTURE_PACKET = "proposal-data-packet-EXAMPLE.md"
STATUS_PACKET = "status-data-packet-EXAMPLE.md"


@pytest.fixture
def studio(tmp_path, monkeypatch):
    """The app with the pipeline stubbed, so a run costs no render and no key."""
    captured = {}

    def _stub(deck_type, company, project, provider, **kwargs):
        captured["deck_type"] = deck_type
        captured["kwargs"] = kwargs
        return {"status": "ok", "prompt": "(design prompt body)",
                "applied_preferences": [], "number": 1,
                "render_fidelity": {"ok": True, "missing_values": {}},
                "layout": {"ok": True, "checked": False, "skipped": "not rendered",
                           "slides": 0, "findings": [], "summary": []}}

    monkeypatch.setattr(ui_app, "generate_and_save_deck", _stub)
    # Replaced rather than left alone: building the real one reads a bearer key
    # and would reach the network on the first poll.
    monkeypatch.setattr(
        ui_app, "_live_provider",
        lambda: LiveProposalProvider(object(), generated_at="2026-08-15T00:00:00Z"),
    )
    monkeypatch.setattr(ui_app, "DECKS_ROOT", str(tmp_path))
    ui_app.app.testing = True
    ui_app._LAST_RESULT.clear()
    return ui_app.app.test_client(), captured


def deck_type_script():
    """Just the deck-type sync, off the shipped page script."""
    return ui_app.BASE.split("window.syncDeckType = function")[1].split("};")[0]


# --------------------------------------------- item 18, the check-in date field

def test_the_checkin_field_is_addressable_as_a_row():
    """Hiding it needs something to hide, the same way `fixture_row` does."""
    assert 'id="checkin_row"' in ui_app.GENERATE_PANEL


def test_the_checkin_field_is_a_calendar_and_not_a_typed_string():
    """Casey asked for a date picker. A `date` input is the browser's own, so
    the format the route reads is the one the field can produce."""
    assert 'type="date" name="check_in_date"' in ui_app.GENERATE_PANEL
    # The placeholder existed only because the box could not say what it wanted.
    assert "e.g. 2026-07-20" not in ui_app.GENERATE_PANEL


def test_a_status_deck_shows_the_checkin_row_and_a_proposal_hides_it():
    script = deck_type_script()
    assert "checkin_row" in script
    assert "deckType.value === 'status' ? '' : 'none'" in script


def test_changing_the_deck_type_syncs_the_form():
    """The select already carried `id="deck_type"`; nothing hides without a
    listener on it."""
    assert 'id="deck_type"' in ui_app.GENERATE_PANEL
    assert "deckType.addEventListener('change'" in ui_app.BASE


def test_the_form_syncs_the_deck_type_on_load_and_after_a_fixture_autofill():
    """`fillFixture` writes `.value` directly, which fires no `change`, so a
    fixture carrying a status deck would land with the row still hidden."""
    fill = ui_app.BASE.split("window.fillFixture = function")[1].split("---- Which half")[0]
    assert "syncDeckType" in fill
    boot = ui_app.BASE.split("---- Initial tab")[0]
    assert "if (window.syncDeckType) window.syncDeckType();" in boot


def test_a_proposal_run_posted_with_a_checkin_date_carries_none(studio):
    """The guarantee behind the hiding. A form posted with the date anyway --
    an older bookmark, a saved form, a script -- is dropped rather than refused,
    because the date is inert on a proposal downstream rather than wrong."""
    client, captured = studio
    page = run_deck(client, data={
        "deck_type": "proposal", "company": "Any Client", "project": "Any Project",
        "packet": FIXTURE_PACKET, "check_in_date": "2026-05-22",
    }).get_data(as_text=True)

    assert captured["deck_type"] == "proposal", "the run has to have happened"
    assert "check_in_date" not in captured["kwargs"]
    # Not in the request echo, and not in the run context the edit forms on the
    # Result tab carry forward either.
    assert "2026-05-22" not in page
    assert ui_app._LAST_RESULT.get("check_in_date", "") == ""


def test_a_status_run_still_carries_its_checkin_date(studio):
    """The counterpart, and the reason this is a drop on one deck type rather
    than a field the studio stopped collecting."""
    client, captured = studio
    run_deck(client, data={
        "deck_type": "status", "company": "Any Client", "project": "Any Project",
        "packet": STATUS_PACKET, "check_in_date": "2026-05-22",
    })

    assert captured["deck_type"] == "status"
    assert captured["kwargs"]["check_in_date"] == "2026-05-22"
    assert ui_app._LAST_RESULT.get("check_in_date", "") == "2026-05-22"


# ------------------------------------------ item 17, the ambiguous-company pick

_ONE = {"id": "c1", "name": "Any Client", "has_kg": True}
_TWO = {"id": "c2", "name": "Any Client Logistics", "has_kg": True}


class _Registry:
    """A stub platform whose company search is a substring match, like the real
    one. Records every call so a test can say which search actually ran."""

    def __init__(self, companies, opportunities=None):
        self.companies = companies
        self.opportunities = opportunities or [
            {"id": "o1", "title": "Opp One", "stage": "published",
             "published_at": "2026-01-01T00:00:00Z"}]
        self.calls = []

    def call_tool_json(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "list_companies":
            search = arguments["search"].lower()
            return {"companies": [c for c in self.companies
                                  if search in c["name"].lower()]}
        if name == "list_opportunities":
            return {"opportunities": list(self.opportunities)}
        raise AssertionError(f"unexpected tool call: {name}")


def test_the_form_carries_the_pick_and_somewhere_to_render_the_choice():
    assert 'id="company_choices"' in ui_app.GENERATE_PANEL
    assert 'type="hidden" name="company_id" id="company_id"' in ui_app.GENERATE_PANEL


def test_no_uuid_field_is_put_on_the_screen():
    """The defect was a remediation telling a reviewer to supply a UUID. A box to
    type one into would be the same defect with a nicer label."""
    panel = ui_app.GENERATE_PANEL
    assert 'type="text" name="company_id"' not in panel
    assert "company_id" not in panel.split('<div class="row">')[0]


def test_an_ambiguous_company_returns_its_candidates_to_the_picker(studio, monkeypatch):
    """The wall a reviewer actually hits, and it is this route on company blur
    rather than the run. It used to answer with the message alone."""
    client, _captured = studio
    registry = _Registry([_ONE, _TWO])
    monkeypatch.setattr(ui_app, "_live_client", lambda: registry)

    data = client.get("/live-opportunities?company=Any+Client").get_json()
    assert data["opportunities"] == []
    assert "matched 2 companies" in data["error"]
    # `has_kg` rides along from item 22: it is the fact the gate will act on,
    # carried from the record we already have so the picker can say so before
    # the click rather than after it.
    assert data["candidates"] == [
        {"name": "Any Client", "company_id": "c1", "has_kg": True},
        {"name": "Any Client Logistics", "company_id": "c2", "has_kg": True},
    ]


def test_picking_one_lists_that_company_s_opportunities(studio, monkeypatch):
    client, _captured = studio
    registry = _Registry([_ONE, _TWO])
    monkeypatch.setattr(ui_app, "_live_client", lambda: registry)

    data = client.get(
        "/live-opportunities?company=Any+Client&company_id=c2").get_json()
    assert data["opportunities"] == [{"id": "o1", "label": "Opp One"}]
    assert "error" not in data
    # The REVIEWER'S search ran again, not a search for the candidate's own
    # name. There is no lookup-by-id tool; see `resolve_company`.
    searches = [a["search"] for n, a in registry.calls if n == "list_companies"]
    assert searches == ["Any Client"]


def test_a_pick_that_no_longer_matches_offers_the_choice_again(studio, monkeypatch):
    """A stale id and a typed one are the same shape. Neither resolves to the
    name match, and the reviewer lands back at a choice rather than at a dead
    end with nothing to press."""
    client, _captured = studio
    monkeypatch.setattr(ui_app, "_live_client", lambda: _Registry([_ONE, _TWO]))

    data = client.get(
        "/live-opportunities?company=Any+Client&company_id=gone").get_json()
    assert data["opportunities"] == []
    assert {c["company_id"] for c in data["candidates"]} == {"c1", "c2"}


def test_an_unambiguous_company_is_unchanged(studio, monkeypatch):
    """One match, no chooser, no extra click, and the same envelope as before."""
    client, _captured = studio
    monkeypatch.setattr(ui_app, "_live_client", lambda: _Registry([_ONE]))

    data = client.get("/live-opportunities?company=Any+Client").get_json()
    assert data == {"opportunities": [{"id": "o1", "label": "Opp One"}]}


def test_the_picked_company_id_reaches_the_request(studio):
    """The run half. Posted by the chooser's hidden field."""
    client, captured = studio
    run_deck(client, data={
        "data_source": "live", "deck_type": "proposal", "company": "Any Client",
        "project": "Any Project", "opportunity_ids": "OPP-1", "company_id": "c2",
    })
    assert captured["kwargs"]["company_id"] == "c2"

    # And nothing is invented for the runs that never needed one.
    captured.clear()
    run_deck(client, data={
        "data_source": "live", "deck_type": "proposal", "company": "Any Client",
        "project": "Any Project", "opportunity_ids": "OPP-1",
    })
    assert "company_id" not in captured["kwargs"]


def test_the_chooser_never_picks_for_the_reviewer():
    """The contract's `pe_firm` narrowing is the mechanism for a caller that can
    narrow. There is no signal here that would make one of four rows obviously
    right, so the script offers and never selects."""
    chooser = ui_app.BASE.split("function offerCompanies")[1].split(
        "window.loadOpportunities = function")[0]
    # The id is written in a click handler and nowhere else in the chooser.
    assert chooser.count("hidden.value = c.company_id") == 1
    assert "addEventListener('click'" in chooser
    # Submit buttons would run the pipeline instead of choosing a company.
    assert "b.type = 'button'" in chooser


def test_editing_the_company_name_drops_the_pick():
    """An id belongs to the name it was picked under. Carried into another name
    it resolves to nothing, which is a refused run for a reason the reviewer did
    nothing to cause."""
    assert "company.addEventListener('input', window.clearCompanyPick)" in ui_app.BASE
    clear = ui_app.BASE.split("window.clearCompanyPick = function")[1].split("}")[0]
    assert "hidden.value = ''" in clear


def test_rendering_the_chooser_voids_the_pick_that_led_to_it():
    """Found by driving the shipped handler, not by reading it. The route
    returns candidates only when nothing resolved, so a pick that went stale is
    void by the time the chooser is drawn; left in the hidden field, a reviewer
    could press Run with the chooser on screen and spend a run posting the id
    the route had just refused."""
    chooser = ui_app.BASE.split("function offerCompanies")[1].split(
        "window.loadOpportunities = function")[0]
    head = chooser.split("box.innerHTML")[0]
    assert "hidden.value = ''" in head


# ------------------------------------------ item 22, the candidates that cannot

_NO_KG = {"id": "c3", "name": "Any Client Freight", "has_kg": False,
          "status": "DRAFT"}
_DRAFT_WITH_KG = {"id": "c4", "name": "Any Client Marine", "has_kg": True,
                  "status": "DRAFT"}


def chooser_script():
    return ui_app.BASE.split("function offerCompanies")[1].split(
        "window.loadOpportunities = function")[0]


def test_a_candidate_row_says_whether_it_can_produce_a_deck(studio, monkeypatch):
    """The fact the gate will act on, carried from the record the search already
    returned. No second call exists for it and none is made."""
    client, _captured = studio
    registry = _Registry([_ONE, _NO_KG])
    monkeypatch.setattr(ui_app, "_live_client", lambda: registry)

    data = client.get("/live-opportunities?company=Any+Client").get_json()
    rows = {c["company_id"]: c for c in data["candidates"]}
    assert rows["c1"]["has_kg"] is True
    assert rows["c3"]["has_kg"] is False
    assert rows["c3"]["status"] == "DRAFT"
    # One search, and nothing else asked about either row.
    assert [n for n, _a in registry.calls] == ["list_companies"]


def test_a_dead_candidate_is_still_listed_rather_than_dropped(studio, monkeypatch):
    """Dropping it is the same dead end one layer down: a reviewer who typed a
    name that IS in the registry would be told there are no results."""
    client, _captured = studio
    monkeypatch.setattr(ui_app, "_live_client", lambda: _Registry([_ONE, _NO_KG]))

    data = client.get("/live-opportunities?company=Any+Client").get_json()
    assert {c["company_id"] for c in data["candidates"]} == {"c1", "c3"}


def test_the_chooser_renders_a_dead_candidate_as_words_and_not_a_button():
    """Greyed and named, with the reason in words. Not a disabled control: a
    disabled button says "no" and never says why."""
    chooser = chooser_script()
    assert "if (!c.has_kg)" in chooser
    assert "no knowledge graph" in chooser
    # It returns before a button is ever created for that row, so there is
    # nothing to press rather than something that refuses to be pressed.
    dead = chooser.split("if (!c.has_kg)")[1].split("}")[0]
    assert "createElement('button')" not in dead


def test_draft_is_said_and_not_enforced():
    """Two different facts. Nothing in the pipeline refuses a DRAFT company, so
    a row that is DRAFT and has a KG stays pickable and simply says so."""
    chooser = chooser_script()
    assert "c.status !== 'ACTIVE'" in chooser
    # The unavailable branch keys off the KG alone, never off the status.
    dead = chooser.split("if (!c.has_kg)")[1].split("return;")[0]
    assert "status" not in dead


def test_the_route_still_refuses_a_dead_pick_that_reaches_it(studio, monkeypatch):
    """Hiding is a hint and not the guarantee, the rule the whole first screen
    runs on. A posted pick with no KG is refused by the gate that has always
    refused it."""
    client, _captured = studio
    monkeypatch.setattr(ui_app, "_live_client", lambda: _Registry([_ONE, _NO_KG]))

    data = client.get(
        "/live-opportunities?company=Any+Client&company_id=c3").get_json()
    assert data["opportunities"] == []
    assert "no knowledge graph" in data["error"]


def test_a_draft_company_with_a_graph_is_not_refused(studio, monkeypatch):
    """The counterpart, and the reason the two facts are kept apart: we have no
    evidence against a DRAFT row that carries a graph, so nothing invents one."""
    client, _captured = studio
    monkeypatch.setattr(
        ui_app, "_live_client", lambda: _Registry([_ONE, _DRAFT_WITH_KG]))

    data = client.get(
        "/live-opportunities?company=Any+Client&company_id=c4").get_json()
    assert data["opportunities"] == [{"id": "o1", "label": "Opp One"}]
    assert "error" not in data
