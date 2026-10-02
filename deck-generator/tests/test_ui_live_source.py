"""The studio switch: which provider a run goes through (E9d).

Flask's test client drives the app with the pipeline stubbed, so no API key, no
network and no real render are needed. The stub captures the provider the studio
handed over, which is the one thing the switch decides; everything downstream of
the seam is identical on both paths and is covered by `test_live_seam.py`.

No live MCP call is made here or anywhere in the suite. Rule 7 in
`data-provider/CLAUDE.md` requires every call to the QofAI server, read-only ones
included, to be proposed and approved before it runs.
"""

import ast
import os
import pathlib
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ui"))

pytest.importorskip("flask")

from data_source_adapter import FixtureProvider  # noqa: E402
from live_proposal_provider import LiveProposalProvider  # noqa: E402
from deck_run import run_deck

_ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURE_PACKET = "proposal-data-packet-EXAMPLE.md"


@pytest.fixture
def studio(tmp_path, monkeypatch):
    """The app with the pipeline stubbed and the live client never constructed.

    `_live_provider` is replaced rather than left alone because building the real
    one reads a bearer key and would reach the network on the first poll.
    """
    import app as ui_app

    captured = {}

    def _stub(deck_type, company, project, provider, **kwargs):
        captured["provider"] = provider
        captured["company"] = company
        captured["project"] = project
        captured["kwargs"] = kwargs
        return {"status": "ok", "prompt": "(design prompt body)",
                "applied_preferences": [], "number": 1,
                "render_fidelity": {"ok": True, "missing_values": {}},
                "layout": {"ok": True, "checked": False, "skipped": "not rendered",
                           "slides": 0, "findings": [], "summary": []}}

    monkeypatch.setattr(ui_app, "generate_and_save_deck", _stub)
    monkeypatch.setattr(
        ui_app, "_live_provider",
        lambda: LiveProposalProvider(object(), generated_at="2026-08-15T00:00:00Z"),
    )
    monkeypatch.setattr(ui_app, "DECKS_ROOT", str(tmp_path))
    ui_app.app.testing = True
    ui_app._LAST_RESULT.clear()
    return ui_app.app.test_client(), captured


def test_the_default_source_is_the_fixture_path_and_it_is_unchanged(studio):
    """Nothing about an existing run moves: a form with no `data_source` at all
    still reads its frozen packet off disk through `FixtureProvider`."""
    client, captured = studio
    response = run_deck(client, data={
        "deck_type": "proposal", "company": "Any Client", "project": "Any Project",
        "packet": FIXTURE_PACKET,
    })
    assert response.status_code == 200
    assert isinstance(captured["provider"], FixtureProvider)


def test_choosing_live_hands_the_seam_object_over_instead(studio):
    client, captured = studio
    response = run_deck(client, data={
        "data_source": "live", "deck_type": "proposal",
        "company": "Any Client", "project": "Any Project",
        "opportunity_id": "OPP-1",
    })
    assert response.status_code == 200
    assert isinstance(captured["provider"], LiveProposalProvider)
    assert captured["company"] == "Any Client"
    assert captured["project"] == "Any Project"


def test_the_live_path_needs_no_packet_file_and_the_fixture_path_still_does(studio):
    """The packet path is the fixture path's own input. A live run with none is a
    run, not a `packet_not_found`; a fixture run with none is still an error."""
    client, captured = studio
    live = run_deck(client, data={
        "data_source": "live", "deck_type": "proposal",
        "company": "Any Client", "project": "Any Project", "packet": "",
        "opportunity_id": "OPP-1",
    })
    assert isinstance(captured["provider"], LiveProposalProvider)
    assert b"packet_not_found" not in live.data

    captured.clear()
    fixture = run_deck(client, data={
        "deck_type": "proposal", "company": "Any Client",
        "project": "Any Project", "packet": "no-such-packet.md",
    })
    assert b"packet_not_found" in fixture.data
    assert "provider" not in captured


def test_the_pe_firm_and_the_proposal_date_reach_the_request(studio):
    """Both are contract request fields the cover reads. Supplied, they cross;
    left blank, nothing is invented in their place."""
    client, captured = studio
    run_deck(client, data={
        "data_source": "live", "deck_type": "proposal", "company": "Any Client",
        "project": "Any Project", "pe_firm": "Any Capital",
        "proposal_date": "2026-08-15", "opportunity_id": "OPP-1",
    })
    assert captured["kwargs"]["pe_firm"] == "Any Capital"
    assert captured["kwargs"]["proposal_date"] == "2026-08-15"

    captured.clear()
    run_deck(client, data={
        "data_source": "live", "deck_type": "proposal", "company": "Any Client",
        "project": "Any Project", "opportunity_id": "OPP-1",
    })
    assert "pe_firm" not in captured["kwargs"]
    assert "proposal_date" not in captured["kwargs"]


def test_the_generate_form_offers_the_live_source_and_no_other():
    """Antonio, 2026-09-20: every deck from here is built from live platform
    data, so the form states the source rather than asking about it. The frozen
    packet is not offered and cannot be reached from this page; the route still
    honours a request that names it, which is how the suite runs offline."""
    import app as ui_app

    assert f'name="data_source" value="{ui_app.SOURCE_LIVE}"' in ui_app.GENERATE_PANEL
    assert f'value="{ui_app.SOURCE_FIXTURE}"' not in ui_app.GENERATE_PANEL
    assert 'name="fixture_id"' not in ui_app.GENERATE_PANEL


def test_the_generate_form_offers_an_opportunity_picker():
    """E9e: the reviewer picks the opportunity rather than the first-paper
    default always winning."""
    import app as ui_app

    # `multiple` since 2026-09-13 (item 15): a deck can carry more than one
    # opportunity, so the picker names them in the plural and reports a list.
    assert 'name="opportunity_ids"' in ui_app.GENERATE_PANEL
    assert "multiple" in ui_app.GENERATE_PANEL


def test_the_picked_opportunity_id_reaches_the_request(studio):
    client, captured = studio
    run_deck(client, data={
        "data_source": "live", "deck_type": "proposal", "company": "Any Client",
        "project": "Any Project", "opportunity_ids": "OPP-9",
    })
    # A list even when it holds one, because the contract takes a list and the
    # provider treats one as a deck of one.
    assert captured["kwargs"]["opportunity_ids"] == ["OPP-9"]

    # The counterpart. This half used to assert that an unpicked live run still
    # crossed the seam with no `opportunity_id`, leaving the provider to choose a
    # paper; it is refused now, so nothing reaches the request at all.
    captured.clear()
    refused = run_deck(client, data={
        "data_source": "live", "deck_type": "proposal", "company": "Any Client",
        "project": "Any Project",
    })
    assert b"opportunity_required" in refused.data
    assert "kwargs" not in captured


def test_the_opportunity_listing_route_uses_list_opportunities_only(studio, monkeypatch):
    """No fifth tool: the route the picker calls reaches the registry and
    `list_opportunities`, nothing else."""
    client, _captured = studio
    import app as ui_app

    calls = []

    class _Stub:
        def call_tool_json(self, name, arguments):
            calls.append(name)
            if name == "list_companies":
                return {"companies": [{"id": "c1", "name": "Any Client", "has_kg": True}]}
            if name == "list_opportunities":
                return {"opportunities": [{"id": "o1", "title": "Opp One",
                                            "stage": "published",
                                            "published_at": "2026-01-01T00:00:00Z"}]}
            raise AssertionError(f"unexpected tool call: {name}")

    monkeypatch.setattr(ui_app, "_live_client", lambda: _Stub())
    response = client.get("/live-opportunities?company=Any+Client")
    assert response.get_json() == {"opportunities": [{"id": "o1", "label": "Opp One"}]}
    assert set(calls) == {"list_companies", "list_opportunities"}


def test_the_opportunity_route_with_no_company_makes_no_call(studio, monkeypatch):
    client, _captured = studio
    import app as ui_app

    def _no_client():
        raise AssertionError("should not build a client with no company to look up")

    monkeypatch.setattr(ui_app, "_live_client", _no_client)
    response = client.get("/live-opportunities?company=")
    assert response.get_json() == {"opportunities": []}


def test_the_opportunity_route_surfaces_a_resolution_error_visibly(studio, monkeypatch):
    client, _captured = studio
    import app as ui_app

    class _Stub:
        def call_tool_json(self, name, arguments):
            if name == "list_companies":
                return {"companies": []}
            raise AssertionError(f"unexpected tool call: {name}")

    monkeypatch.setattr(ui_app, "_live_client", lambda: _Stub())
    response = client.get("/live-opportunities?company=Nobody")
    data = response.get_json()
    assert data["opportunities"] == []
    assert "matched no company" in data["error"]


def test_the_studio_names_no_company_no_endpoint_and_no_key_variable():
    """The switch is a parameter, not a client. The endpoint and the key's
    environment variable name stay defaulted in `qofai_mcp_client`, and no
    reference company appears anywhere in the studio."""
    source = (_ROOT / "ui" / "app.py").read_text()
    for literal in ("QOFAI_MCP", "mcp.example.test", "Northwind", "Fabrikam"):
        assert literal not in source


def test_httpx_is_not_a_module_level_import_on_the_hosted_path():
    """E10's trap, held shut. The moment `qofai_mcp_client` is imported so that it
    runs on import anywhere the hosted service imports, `import httpx` runs there
    too, and a service without `httpx` fails every deck run on import. The live
    client is built inside the one branch that needs it.

    The predicate is function-body containment rather than `col_offset > 0`,
    corrected 2026-08-18 alongside the same fix in `test_live_seam.py`. Indentation
    is a proxy for deferral and not the thing itself: a module-level
    `try: import httpx / except ImportError:` indents to `col_offset == 4`, passes
    an indentation test, and still executes on every import of the module,
    including every fixture run. Only being inside a function body defers
    execution to call time.
    """
    for module in ("ui/app.py", "src/deck_generator.py", "src/data_source_adapter.py",
                   "src/live_proposal_provider.py", "src/packet_fill.py",
                   "src/packet_document.py"):
        tree = ast.parse((_ROOT / module).read_text())
        deferred = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                deferred.update(
                    id(child) for child in ast.walk(node)
                    if isinstance(child, (ast.Import, ast.ImportFrom))
                )
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            names = ({alias.name for alias in node.names}
                     if isinstance(node, ast.Import) else {node.module})
            if not names & {"httpx", "qofai_mcp_client"}:
                continue
            assert id(node) in deferred, (
                f"{module} imports {names} at line {node.lineno} where it runs on "
                "import, which puts httpx on the hosted import path for every "
                "fixture run too"
            )


# ---- The live path requires the reviewer's opportunity pick -----------------
# A deck's cover title comes from the project and nearly all of its content comes
# from the chosen opportunity's research paper. The two were resolved
# independently, and only the project half was validated, so an unpicked live run
# titled the deck from one engagement and wrote every slide from another. The
# provider-side default stays (a library caller sweeping a corpus wants "any
# opportunity with a paper"); the studio, which has a human in front of it, now
# requires the pick.


def test_a_live_run_with_no_opportunity_picked_is_refused(studio):
    """The hole this closes. Nothing is rendered and no pipeline call is spent:
    the studio asks for the missing pick instead of quietly choosing a paper."""
    client, captured = studio
    response = run_deck(client, data={
        "data_source": "live", "deck_type": "proposal",
        "company": "Any Client", "project": "Any Project",
    })
    assert response.status_code == 200
    assert b"opportunity_required" in response.data
    assert "provider" not in captured


def test_a_live_run_with_a_blank_opportunity_field_is_refused_too(studio):
    """The form posts the field with an empty value rather than omitting it, so
    the check has to read a blank string as no pick and not as a pick."""
    client, captured = studio
    response = run_deck(client, data={
        "data_source": "live", "deck_type": "proposal",
        "company": "Any Client", "project": "Any Project",
        "opportunity_id": "   ",
    })
    assert b"opportunity_required" in response.data
    assert "provider" not in captured


def test_the_fixture_path_still_needs_no_opportunity(studio):
    """The requirement belongs to the live path alone. A fixture run reads a
    frozen packet and has no opportunity to pick."""
    client, captured = studio
    response = run_deck(client, data={
        "deck_type": "proposal", "company": "Any Client",
        "project": "Any Project", "packet": FIXTURE_PACKET,
    })
    assert b"opportunity_required" not in response.data
    assert isinstance(captured["provider"], FixtureProvider)


def test_the_form_no_longer_advertises_an_opportunity_default():
    """The studio used to recommend the behaviour that produced the wrong deck:
    the dropdown's first option and the field's own label both told the reviewer
    to leave the pick alone."""
    import app as ui_app

    assert "default: first published opportunity" not in ui_app.GENERATE_PANEL
    assert "leave on the" not in ui_app.GENERATE_PANEL


# ---- The picker is ordered by the project the reviewer named ----------------
# Nothing on the platform links a project to an opportunity, so the studio cannot
# pair them. It can order the list so the likely pick is near the top and mark
# the leader, which is what these cover. The reviewer still picks.


def _ranking_stub(monkeypatch, ui_app, titles, calls=None):
    """A company with several published opportunities, so ordering has something
    to do."""
    class _Stub:
        def call_tool_json(self, name, arguments):
            if calls is not None:
                calls.append((name, arguments))
            if name == "list_companies":
                return {"companies": [{"id": "c1", "name": "Any Client",
                                       "has_kg": True}]}
            if name == "list_opportunities":
                return {"opportunities": [
                    {"id": f"o{i}", "title": t, "stage": "published",
                     "published_at": f"2026-0{i + 1}-01T00:00:00Z"}
                    for i, t in enumerate(titles)]}
            raise AssertionError(f"unexpected tool call: {name}")

    monkeypatch.setattr(ui_app, "_live_client", lambda: _Stub())


def test_the_route_orders_the_choices_by_the_project_it_was_given(studio, monkeypatch):
    """The project reaches the listing route and decides the order. Without it
    the reviewer's own engagement can sit anywhere in a list of 25."""
    client, _captured = studio
    import app as ui_app

    _ranking_stub(monkeypatch, ui_app,
                  ["Deploy AI Across Network Maintenance",
                   "Deploy AI Across Billing Operations"])
    data = client.get("/live-opportunities?company=Any+Client"
                      "&project=Billing+Agents+Programme").get_json()
    assert [o["id"] for o in data["opportunities"]] == ["o1", "o0"]
    assert data["opportunities"][0]["best"] is True


def test_the_route_with_no_project_keeps_the_platforms_order(studio, monkeypatch):
    """The pre-existing behaviour, held at the route: a reviewer who has not
    typed a project yet still gets every choice, in `published_at` order, with
    nothing marked."""
    client, _captured = studio
    import app as ui_app

    _ranking_stub(monkeypatch, ui_app, ["First Opportunity", "Second Opportunity"])
    data = client.get("/live-opportunities?company=Any+Client").get_json()
    assert [o["id"] for o in data["opportunities"]] == ["o0", "o1"]
    assert not any("best" in o for o in data["opportunities"])


def test_ordering_the_route_costs_no_extra_tool_call(studio, monkeypatch):
    """Ranking is arithmetic over titles already in hand. It must not turn the
    picker into a second round trip per candidate."""
    client, _captured = studio
    import app as ui_app

    calls = []
    _ranking_stub(monkeypatch, ui_app, ["One Title", "Two Title"], calls=calls)
    client.get("/live-opportunities?company=Any+Client&project=Two")
    assert [name for name, _args in calls] == ["list_companies", "list_opportunities"]


def test_an_unresolvable_project_still_lists_every_choice(studio, monkeypatch):
    """The picker is not the run. A project typed halfway, or one that matches no
    title, must not empty the dropdown or raise: the reviewer needs the full list
    in front of them to pick from."""
    client, _captured = studio
    import app as ui_app

    _ranking_stub(monkeypatch, ui_app, ["First Opportunity", "Second Opportunity"])
    data = client.get("/live-opportunities?company=Any+Client"
                      "&project=Nothing+Like+These").get_json()
    assert len(data["opportunities"]) == 2
    assert "error" not in data


def test_the_picker_sends_the_project_and_refreshes_when_it_changes(studio):
    """The wiring, asserted on the page the browser actually receives rather than
    on the template constant. It does not prove the listener fires, which only a
    browser can; it proves the studio shipped the project field to the route and
    bound the refresh to it, which is the part that was missing.

    One blur listener now, not two. The project field was hidden on 2026-09-20
    and a hidden input never blurs, so its refresh moved onto the picker's own
    `change` (`syncProjectToOpportunity`)."""
    client, _captured = studio
    page = client.get("/").get_data(as_text=True)
    assert "project=" in page
    assert page.count("addEventListener('blur', window.loadOpportunities)") == 1
    assert "syncProjectToOpportunity" in page, (
        "the project is derived from the pick now, so the pick has to write it"
    )


def test_the_picker_marks_the_leader_in_the_list_the_reviewer_reads(studio):
    """A flag nobody can see is not a hint. The option text carries the mark."""
    client, _captured = studio
    page = client.get("/").get_data(as_text=True)
    assert "o.best" in page
    assert "best title match" in page


# --- the deck title, where the platform has no project (2026-08-20) ---------


def test_the_generate_form_no_longer_asks_for_a_deck_title():
    """Removed from the form 2026-09-20: the deck is titled after the
    opportunity, which the reviewer has already picked. It stays a REQUEST
    field, because a programmatic caller may still name a deck and
    `resolve_project` still honours one."""
    import app as ui_app

    assert 'name="deck_title"' not in ui_app.GENERATE_PANEL


def test_the_typed_deck_title_reaches_the_request(studio):
    client, captured = studio
    run_deck(client, data={
        "data_source": "live", "deck_type": "proposal", "company": "Any Client",
        "project": "", "opportunity_id": "OPP-9",
        "deck_title": "A Title The Reviewer Typed",
    })
    assert captured["kwargs"]["deck_title"] == "A Title The Reviewer Typed"


def test_a_live_run_with_no_project_and_no_title_still_reaches_the_seam(studio):
    """The WTG shape: nothing to resolve on the platform and no title typed, so
    the provider names the deck from the picked opportunity. The studio's job is
    to stop refusing the run before it gets there."""
    client, captured = studio
    response = run_deck(client, data={
        "data_source": "live", "deck_type": "proposal", "company": "Any Client",
        "project": "", "opportunity_id": "OPP-9",
    })
    assert response.status_code == 200
    assert isinstance(captured["provider"], LiveProposalProvider)
    assert captured["project"] == ""
    assert "deck_title" not in captured["kwargs"]


def test_a_blank_title_sends_nothing_rather_than_an_empty_string(studio):
    """An empty field is an absent request field, not a title of no characters."""
    client, captured = studio
    run_deck(client, data={
        "data_source": "live", "deck_type": "proposal", "company": "Any Client",
        "project": "Any Project", "opportunity_id": "OPP-9", "deck_title": "   ",
    })
    assert "deck_title" not in captured["kwargs"]


# ---------------------------------------------------------------------------
# ITEM 15: the picker takes more than one.
# ---------------------------------------------------------------------------

def test_every_picked_opportunity_reaches_the_request(studio):
    client, captured = studio
    run_deck(client, data={
        "data_source": "live", "deck_type": "proposal", "company": "Any Client",
        "project": "Any Project", "opportunity_ids": ["OPP-9", "OPP-4"],
    })
    assert captured["kwargs"]["opportunity_ids"] == ["OPP-9", "OPP-4"]


def test_a_live_run_with_nothing_picked_is_still_refused(studio):
    """The refusal that has always happened, in the plural. The project names
    the deck; each opportunity supplies the paper its own slide is written
    from, so the studio will not choose for anyone."""
    client, captured = studio
    page = run_deck(client, data={
        "data_source": "live", "deck_type": "proposal", "company": "Any Client",
        "project": "Any Project",
    })
    assert captured == {}
    assert "at least one" in page.get_data(as_text=True)


def test_the_singular_field_still_works_for_a_form_posted_elsewhere(studio):
    client, captured = studio
    run_deck(client, data={
        "data_source": "live", "deck_type": "proposal", "company": "Any Client",
        "project": "Any Project", "opportunity_id": "OPP-9",
    })
    assert captured["kwargs"]["opportunity_ids"] == ["OPP-9"]


def test_the_same_opportunity_picked_twice_is_carried_once(studio):
    client, captured = studio
    run_deck(client, data={
        "data_source": "live", "deck_type": "proposal", "company": "Any Client",
        "project": "Any Project", "opportunity_ids": ["OPP-9", "OPP-9"],
    })
    assert captured["kwargs"]["opportunity_ids"] == ["OPP-9"]


def test_the_placeholder_row_cannot_be_picked():
    """In a multiple picker an enabled placeholder is a row a reviewer can
    select, and selecting it would submit an empty id beside the real ones."""
    import app as ui_app

    assert "opt.disabled = true" in ui_app.BASE


def test_the_picker_states_no_ceiling_on_how_many(studio):
    """Antonio's call: no fixed ceiling on the opportunity count. Still true and
    still unstated as a number; the form says each pick gets its own slide and
    names no maximum, after the small print was cut back 2026-09-20.

    The wording moved on 2026-09-22, when "in the order you pick them" turned
    out to be false (a form submits a multiple select in DOCUMENT order), so
    this asserts the CLAIM rather than the sentence: every pick gets a slide,
    and no number is named as a limit.
    """
    import re

    import app as ui_app

    assert "gets its own slide" in ui_app.GENERATE_PANEL
    assert "multiple" in ui_app.GENERATE_PANEL
    blurb = ui_app.GENERATE_PANEL[
        ui_app.GENERATE_PANEL.find("Click each one you want"):][:260]
    assert not re.search(r"\b(up to|at most|maximum|no more than)\b", blurb,
                         re.I), blurb


# ---------------------------------------------------------------------------
# THE SHORT CLIENT NAME FIELD (2026-09-15).
# ---------------------------------------------------------------------------

def test_the_generate_form_no_longer_asks_for_a_short_client_name():
    """Removed from the form 2026-09-20 with the deck title, for the same
    reason: one less box between a reviewer and a deck. The footers take the
    registry name, and the field remains a request parameter."""
    import app as ui_app

    assert 'name="client_short"' not in ui_app.GENERATE_PANEL


def test_a_typed_short_name_reaches_the_request(studio):
    client, captured = studio
    run_deck(client, data={
        "data_source": "live", "deck_type": "proposal", "company": "Any Client",
        "project": "Any Project", "opportunity_ids": "OPP-9",
        "client_short": "SHORT",
    })
    assert captured["kwargs"]["client_short"] == "SHORT"


def test_a_blank_short_name_sends_the_request_it_always_sent(studio):
    """Only when there is one, so a run that leaves it blank crosses the seam
    exactly as it did before this field existed."""
    client, captured = studio
    run_deck(client, data={
        "data_source": "live", "deck_type": "proposal", "company": "Any Client",
        "project": "Any Project", "opportunity_ids": "OPP-9",
        "client_short": "   ",
    })
    assert "client_short" not in captured["kwargs"]


def test_it_is_offered_on_the_status_path_too(studio):
    """The footers are the same on a status deck and the platform supplies no
    short form for either, so the field is not proposal-only."""
    client, captured = studio
    run_deck(client, data={
        "data_source": "live", "deck_type": "status", "company": "Any Client",
        "project": "Any Project", "opportunity_ids": "OPP-9",
        "client_short": "SHORT",
    })
    assert captured["kwargs"]["client_short"] == "SHORT"
