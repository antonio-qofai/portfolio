"""The two live entry points fail visibly rather than usefully (E10).

Both `POST /run` on the live source and `GET /live-opportunities` build an MCP
client outside any catch that would turn a failure into something a reviewer can
read. This file forces each failure and asserts on what the studio returns.

No live MCP call is made here. Every failure is forced by removing the key or by
stubbing the client, so nothing in this file opens a socket.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ui"))

pytest.importorskip("flask")

from qofai_mcp_client import DEFAULT_KEY_ENV  # noqa: E402
from deck_run import run_deck


@pytest.fixture
def studio(tmp_path, monkeypatch):
    """The app with no MCP key in the environment and nothing else stubbed.

    `_live_provider` and `_live_client` are deliberately left alone: the point is
    that building the real client raises, so replacing it would remove the thing
    under test.
    """
    import app as ui_app
    import deck_generator

    # Pin the real pipeline back. `test_ui_gap_decision.py` replaces this module
    # attribute by plain assignment rather than through monkeypatch, so its stub
    # survives that file and every later test in the session sees a run that
    # always succeeds. These tests are about what a *failing* run produces, so
    # they would pass for the wrong reason under it.
    monkeypatch.setattr(
        ui_app, "generate_and_save_deck", deck_generator.generate_and_save_deck
    )
    monkeypatch.delenv(DEFAULT_KEY_ENV, raising=False)
    # Both roots move to the temp tree: the fixture-path test below drives a real
    # run rather than a stubbed one, and a test that writes into the repo leaves
    # untracked output behind on every invocation.
    monkeypatch.setattr(ui_app, "DECKS_ROOT", str(tmp_path))
    monkeypatch.setattr(ui_app, "PROMPTS_ROOT", str(tmp_path))
    ui_app.app.testing = False  # let a 500 be a 500 rather than re-raising
    ui_app._LAST_RESULT.clear()
    return ui_app.app.test_client()


def test_a_missing_key_does_not_500_the_generate_page(studio):
    """The client is built at the seam, outside the pipeline's try, so with no
    key this used to propagate and take the page down."""
    response = run_deck(studio, data={
        "data_source": "live", "deck_type": "proposal",
        "company": "Any Client", "project": "Any Project",
        "opportunity_id": "OPP-1",
    })
    assert response.status_code == 200


def test_the_generate_page_names_the_variable_it_looked_in(studio):
    """The message is the deliverable. A reviewer who cannot read the Railway
    dashboard has to learn which variable to set from the studio itself."""
    body = run_deck(studio, data={
        "data_source": "live", "deck_type": "proposal",
        "company": "Any Client", "project": "Any Project",
        "opportunity_id": "OPP-1",
    }).get_data(as_text=True)
    assert DEFAULT_KEY_ENV in body


def test_a_missing_key_is_not_labelled_a_render_error(studio):
    """Nothing was rendered and no API call was spent, so the `render_error`
    label would send a reviewer to retry a run that cannot work yet."""
    body = run_deck(studio, data={
        "data_source": "live", "deck_type": "proposal",
        "company": "Any Client", "project": "Any Project",
        "opportunity_id": "OPP-1",
    }).get_data(as_text=True)
    assert "data_source_not_configured" in body
    assert "render_error" not in body


def test_a_missing_key_leaves_the_fixture_path_working(studio):
    """The key is the live path's requirement alone. A studio with no key still
    generates from a frozen packet."""
    response = run_deck(studio, data={
        "deck_type": "proposal", "company": "Any Client", "project": "Any Project",
        "packet": "proposal-data-packet-EXAMPLE.md",
    })
    assert response.status_code == 200
    assert "data_source_not_configured" not in response.get_data(as_text=True)


def test_a_missing_key_does_not_500_the_picker_route(studio):
    """The picker route builds its own client and has no broad catch, so this
    reached the browser as 500 HTML in answer to a request for JSON."""
    response = studio.get("/live-opportunities?company=Any+Client")
    assert response.status_code == 200
    assert response.mimetype == "application/json"


def test_the_picker_route_names_the_variable_it_looked_in(studio):
    data = studio.get("/live-opportunities?company=Any+Client").get_json()
    assert data["opportunities"] == []
    assert DEFAULT_KEY_ENV in data["error"]


# --- The transport failure, enveloped by the seam and read in the studio ----

TRANSPORT_MESSAGE = "the platform did not answer"


@pytest.fixture
def dead_transport(studio, monkeypatch):
    """A live studio whose platform connection fails on the first call.

    Driven through the real `LiveProposalProvider` rather than by raising from a
    stubbed pipeline, so the envelope under test is the one `submit` actually
    builds. Until 2026-08-18 the transport's exceptions escaped the seam, because
    `submit` caught `ProviderError` only and the two hierarchies are disjoint;
    `submit` now maps them to `E_SOURCE_UNREACHABLE` and this drives that path.
    """
    import app as ui_app
    from live_proposal_provider import LiveProposalProvider
    from qofai_mcp_client import McpProtocolError

    class _DeadClient:
        def call_tool_json(self, name, arguments):
            raise McpProtocolError(TRANSPORT_MESSAGE)

    monkeypatch.setattr(
        ui_app, "_live_provider", lambda: LiveProposalProvider(_DeadClient())
    )
    return studio


def _live_run(client):
    """A live run that gets past the studio's own input check, so what it hits is
    the transport. The `opportunity_id` is required of every live run now (an
    unpicked one is refused before the seam), and it is not what these tests are
    about."""
    return run_deck(client, data={
        "data_source": "live", "deck_type": "proposal",
        "company": "Any Client", "project": "Any Project",
        "opportunity_id": "OPP-1",
    }).get_data(as_text=True)


def test_a_transport_failure_is_not_labelled_a_render_error(dead_transport):
    """The defect this fixes. Nothing was rendered and no API call was spent, so
    `render_error` sends a reviewer to retry a run that cannot work yet - the
    same hazard the `CoverageError` handler above it was written for.

    The code moved 2026-08-18 and the coverage did not. It was the studio's own
    `data_source_unavailable` until `submit` gained its `McpError` clause; now
    the seam turns the same failure into an `E_SOURCE_UNREACHABLE` envelope
    before it can reach the studio's handler at all. What this test guards is
    unchanged: whatever the reviewer reads, it is not "render failed".
    """
    body = _live_run(dead_transport)
    assert "E_SOURCE_UNREACHABLE" in body
    assert "render_error" not in body


def test_a_transport_failure_names_what_actually_failed(dead_transport):
    """`str(exc)` alone reads as a bare sentence with no attribution. The
    exception's type is what tells a reviewer the data source was the thing that
    broke."""
    body = _live_run(dead_transport)
    assert TRANSPORT_MESSAGE in body
    assert "McpProtocolError" in body


def test_the_fixture_path_still_labels_an_unknown_failure_a_render_error(studio, monkeypatch):
    """A genuine render failure keeps the label it has always had.

    This asserts the label only. It carried a paired
    `"data_source_unavailable" not in body` exclusion, which the studio handler's
    removal turned into an assertion that could not fail; repointing it at
    `E_SOURCE_UNREACHABLE` did not help, because this test drives the FIXTURE path,
    where `submit` is not involved and no seam code can produce that string. The
    real form of that claim is the live-path test below, where it can fail and is
    shown to.
    """
    import app as ui_app

    def _boom(*args, **kwargs):
        raise ValueError("something else went wrong")

    monkeypatch.setattr(ui_app, "generate_and_save_deck", _boom)
    body = run_deck(studio, data={
        "deck_type": "proposal", "company": "Any Client", "project": "Any Project",
        "packet": "proposal-data-packet-EXAMPLE.md",
    }).get_data(as_text=True)
    assert "render_error" in body


def test_a_failure_inside_assembly_is_not_blamed_on_the_platform(dead_transport,
                                                                 monkeypatch):
    """The boundary of the new code, and the reason `submit` catches `McpError`
    rather than `Exception`.

    A parser raising inside assembly is still outside the contract's vocabulary
    and still escapes the seam - a known open finding. What must NOT happen is it
    being swept into `E_SOURCE_UNREACHABLE`, because that tells a reviewer the
    platform was unreachable when the platform answered fine and our own parsing
    broke. Widening the clause to `Exception` is the tempting way to close the
    remaining leak, and this is what stops it.
    """
    import live_proposal_provider

    def _boom(*_args, **_kwargs):
        raise ValueError("a parser broke on a real answer")

    monkeypatch.setattr(live_proposal_provider, "resolve_company", _boom)
    body = _live_run(dead_transport)
    assert "E_SOURCE_UNREACHABLE" not in body


# --- The picker route, which never goes through the seam at all -------------


@pytest.fixture
def dead_picker(studio, monkeypatch):
    """The picker route against a platform that will not answer."""
    import app as ui_app
    from qofai_mcp_client import McpProtocolError

    class _DeadClient:
        def call_tool_json(self, name, arguments):
            raise McpProtocolError(TRANSPORT_MESSAGE)

    monkeypatch.setattr(ui_app, "_live_client", lambda: _DeadClient())
    return studio


def test_a_transport_failure_does_not_500_the_picker_route(dead_picker):
    """The route caught `ProviderError` only, so this came back as 500 HTML to a
    `fetch` that asked for JSON."""
    response = dead_picker.get("/live-opportunities?company=Any+Client")
    assert response.status_code == 200
    assert response.mimetype == "application/json"


def test_the_picker_route_names_a_transport_failure(dead_picker):
    """An empty dropdown reads as "this company has no published opportunities"
    whatever went wrong, so the envelope has to say which it was."""
    data = dead_picker.get("/live-opportunities?company=Any+Client").get_json()
    assert data["opportunities"] == []
    assert TRANSPORT_MESSAGE in data["error"]
    assert "McpProtocolError" in data["error"]


def test_the_picker_markup_has_somewhere_to_put_an_error(studio):
    """The route's `error` field has been returned since E9e and no element ever
    displayed it. Bug two of two: without a target the handler has nowhere to
    write, and the reviewer sees an empty dropdown either way."""
    import app as ui_app

    assert 'id="opportunity_error"' in ui_app.GENERATE_PANEL


def test_the_picker_handler_reads_the_error_field_and_catches_a_rejection(studio):
    """Both halves of the browser-side bug, asserted on the shipped script since
    there is no JS harness in this repo. Weaker than running it, which is why the
    forced local run is reported alongside."""
    import app as ui_app

    # The DEFINITION, not the first mention: the ambiguous-company chooser
    # (item 17) calls `window.loadOpportunities()` from above it.
    handler = ui_app.BASE.split("window.loadOpportunities = function")[1]
    handler = handler.split("window.addCommercialRow")[0]
    assert "data.error" in handler
    assert ".catch(" in handler
