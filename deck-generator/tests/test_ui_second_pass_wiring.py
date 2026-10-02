"""The studio turns the LLM passes ON (E11 Stage 2g; the ranker 2026-08-19).

Six stages shipped the second extraction pass and the writing pass, tested them
in isolation, and left `ui/app.py:252` constructing the provider with neither, so
every one of them was switched off in the one place a reviewer clicks. This file
pins the wiring itself: what `_live_provider()` hands the provider, in both key
states, and what that construction does and does not reach.

No live call is made here. The MCP client is a stub from `test_live_seam`, the
model client is a module that raises if anything constructs it, and both LLM
passes are faked through the real verifiers. Rule 7 in
`data-provider/CLAUDE.md` requires every call to the QofAI server, read-only ones
included, to be proposed and approved before it runs, and an Anthropic call costs
money the suite must never spend.

The tests reach into `_extractor` and `_writer` on the provider. There is no
public accessor and there should not be one: which passes a caller attached is
exactly what this stage decides, and asserting on the constructed object is the
only way to prove it without making the call.
"""

import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ui"))

pytest.importorskip("flask")

import extraction_cache  # noqa: E402
import live_proposal_provider  # noqa: E402
import paper_writing  # noqa: E402
import second_pass  # noqa: E402
from data_source_adapter import FixtureProvider, _split_sections  # noqa: E402
from live_proposal_provider import LiveProposalProvider  # noqa: E402
from test_live_seam import CLIENTS, STAMP, StubClient  # noqa: E402
from test_second_pass import extractor_returning  # noqa: E402
from deck_run import run_deck

KEY = "ANTHROPIC_API_KEY"
FIXTURE_PACKET = "proposal-data-packet-EXAMPLE.md"


def _request(spec):
    return {"company": spec["company"]["name"],
            "project": spec["project"]["name"],
            "pe_firm": spec["pe_firm"], "proposal_date": "2026-08-15",
            "options": {"min_data_completeness": 0.70}}


def writer_returning(payload, record=None):
    """A fake writing pass: the real verifier, driven by a scripted answer.

    The same shape as `test_second_pass.extractor_returning` and for the same
    reason. Verification is what makes a generated sentence admissible, so a fake
    answer goes through `paper_writing.read_response` exactly as a live one would.
    """
    calls = record if record is not None else []

    def writer(paper, description, requested):
        calls.append((description, requested))
        return paper_writing.read_response(
            paper, description, requested,
            type("M", (), {"content": [type("B", (), {
                "type": "text", "text": json.dumps(payload)})()]})(),
        )

    return writer


@pytest.fixture
def no_model_client(monkeypatch):
    """Constructing a real model client fails loudly, wherever it is attempted.

    Stop condition 3 is asserted rather than assumed. Every lazy `import
    anthropic` in this repo resolves through `sys.modules`, so replacing the
    module means any construction anywhere below the studio raises here instead
    of reaching the network. Stage 2 already paid for the assumed version of
    this once: constructing a client whenever one could be constructed took the
    suite from 44s to 215s of real calls.
    """
    module = types.ModuleType("anthropic")

    class _Forbidden:
        def __init__(self, *args, **kwargs):
            raise AssertionError(
                "a real Anthropic client was constructed inside the test suite"
            )

    module.Anthropic = _Forbidden
    monkeypatch.setitem(sys.modules, "anthropic", module)
    return module


@pytest.fixture
def studio(monkeypatch, no_model_client):
    """`ui.app` with the MCP client stubbed and the clock pinned.

    `_live_client` is replaced rather than left alone because building the real
    one reads a bearer key and would reach the platform. `_stamp` is pinned so
    two round trips are comparable byte for byte; it is test scaffolding, not the
    construction under test.
    """
    import app as ui_app

    stub = StubClient(CLIENTS["one"])
    monkeypatch.setattr(ui_app, "_live_client", lambda: stub)
    monkeypatch.setattr(live_proposal_provider, "_stamp", lambda: STAMP)
    extraction_cache.clear()
    ui_app.app.testing = True
    ui_app._LAST_RESULT.clear()
    return ui_app, stub


# --- what the studio constructs, in both key states -------------------------

def test_with_a_key_the_studio_attaches_every_pass(studio, monkeypatch):
    """The finding this stage exists for. Every pass reaches the provider, and
    each is the one its documented factory builds rather than something
    assembled here. The ranking pass (2026-08-19) is listed here so it cannot
    repeat the failure: a pass nothing calls is a pass that does not exist."""
    ui_app, _stub = studio
    monkeypatch.setenv(KEY, "test-key-not-a-real-one")

    provider = ui_app._live_provider()

    assert provider._extractor is not None
    assert provider._writer is not None
    assert provider._ranker is not None
    assert provider._extractor.__qualname__.startswith("make_extractor")
    assert provider._writer.__qualname__.startswith("make_writer")
    assert provider._ranker.__qualname__.startswith("make_ranker")


def test_with_no_key_the_studio_attaches_no_pass_at_all(studio, monkeypatch):
    """A missing key is a configuration state, not a failure: the provider is
    still built, with the first pass alone."""
    ui_app, _stub = studio
    monkeypatch.delenv(KEY, raising=False)

    provider = ui_app._live_provider()

    assert provider._extractor is None
    assert provider._writer is None
    assert provider._ranker is None


def test_a_missing_key_is_not_an_error_envelope(studio, monkeypatch):
    """Deliberately unlike the MCP key. Without the MCP key there is no data at
    all and `_live_provider_or_error` returns an envelope; without an Anthropic
    key there is still a complete first-pass deck, so the run proceeds."""
    ui_app, _stub = studio
    monkeypatch.delenv(KEY, raising=False)

    provider, config_error = ui_app._live_provider_or_error()

    assert config_error is None
    assert isinstance(provider, LiveProposalProvider)


def test_the_studio_asks_for_the_key_only_through_the_existing_predicate(studio,
                                                                        monkeypatch):
    """One way to ask, not two. `_has_api_key` is the studio's existing key
    predicate and four call sites already gate rendering on it; the live path
    reads the same one, so a change to it moves both together."""
    ui_app, _stub = studio
    monkeypatch.setattr(ui_app, "_has_api_key", lambda: False)
    monkeypatch.setenv(KEY, "a-key-the-predicate-says-to-ignore")

    provider = ui_app._live_provider()

    assert provider._extractor is None
    assert provider._writer is None


# --- neither construction reaches the network -------------------------------

def test_neither_construction_reaches_the_network(studio, monkeypatch):
    """Asserted, not assumed. Building the provider makes no MCP tool call and
    constructs no model client in either key state: the factories return
    callables, and the client each one would build is built at call time."""
    ui_app, stub = studio

    monkeypatch.setenv(KEY, "test-key-not-a-real-one")
    with_key = ui_app._live_provider()
    monkeypatch.delenv(KEY, raising=False)
    without_key = ui_app._live_provider()

    assert stub.calls == []
    assert with_key._extractor is not None
    assert without_key._extractor is None


def test_the_no_key_round_trip_constructs_no_model_client(studio, monkeypatch):
    """The whole live path, end to end, with no key: resolve, gate, assemble,
    build the document. The forbidden client would raise if anything below the
    studio tried to construct one."""
    ui_app, stub = studio
    monkeypatch.delenv(KEY, raising=False)

    provider = ui_app._live_provider()
    envelope = provider.poll(provider.submit(_request(CLIENTS["one"])))["envelope"]

    assert envelope["status"] == "ok"
    assert stub.calls


# --- with no key, behaviour is what it was ----------------------------------

def test_with_no_key_the_packet_is_byte_identical_to_the_old_construction(studio,
                                                                         monkeypatch):
    """Stop condition 4, proved rather than asserted.

    `LiveProposalProvider(client)` is exactly what `ui/app.py:252` built before
    this stage. Two round trips over the same stub, one through that
    construction and one through the studio's own with no key, must produce the
    same envelope byte for byte and ask the platform for the same things in the
    same order.
    """
    ui_app, stub = studio
    monkeypatch.delenv(KEY, raising=False)
    request = _request(CLIENTS["one"])

    before_client = StubClient(CLIENTS["one"])
    before = LiveProposalProvider(before_client)
    before_envelope = before.poll(before.submit(request))["envelope"]

    after = ui_app._live_provider()
    after_envelope = after.poll(after.submit(request))["envelope"]

    assert after_envelope["packet"] == before_envelope["packet"]
    assert after_envelope == before_envelope
    assert stub.calls == before_client.calls


# --- the section 8 blocks follow the passes that actually ran ---------------

def test_the_studio_construction_carries_both_passes_into_the_artifact(studio,
                                                                      monkeypatch):
    """The wiring, end to end, without a live call.

    Both factories are replaced with fakes that go through the real verifiers,
    so what is measured is whether the studio's own construction reaches the
    passes at all. Section 8's two blocks are the artifact-visible proof: each
    one exists only when its pass ran, and records what it was asked for.
    """
    ui_app, _stub = studio
    monkeypatch.setenv(KEY, "test-key-not-a-real-one")
    extractor_calls, writer_calls = [], []
    monkeypatch.setattr(
        second_pass, "make_extractor",
        lambda **options: extractor_returning({"fields": []}, extractor_calls),
    )
    monkeypatch.setattr(
        second_pass, "make_writer",
        lambda **options: writer_returning({"sentences": []}, writer_calls),
    )

    provider = ui_app._live_provider()
    packet = provider.poll(provider.submit(_request(CLIENTS["one"])))["envelope"]["packet"]
    section8 = _split_sections(packet)[8]

    assert extractor_calls, "the extraction pass was never called"
    assert writer_calls, "the writing pass was never called"
    assert "second_pass:" in section8
    assert "generated:" in section8


def test_with_no_key_neither_section_8_block_is_written(studio, monkeypatch):
    """The other half of the same rule. Neither pass ran, so the packet omits
    both blocks entirely rather than claiming a pass ran and found nothing."""
    ui_app, _stub = studio
    monkeypatch.delenv(KEY, raising=False)

    provider = ui_app._live_provider()
    packet = provider.poll(provider.submit(_request(CLIENTS["one"])))["envelope"]["packet"]
    section8 = _split_sections(packet)[8]

    assert "second_pass:" not in section8
    assert "generated:" not in section8


def test_the_two_passes_stay_independently_optional(studio, monkeypatch):
    """The studio attaches both or neither because one key governs both, and
    that must not narrow the seam underneath it. Either one alone still runs,
    and the block for the absent one is still absent."""
    ui_app, _stub = studio
    monkeypatch.delenv(KEY, raising=False)

    extractor_only = LiveProposalProvider(
        StubClient(CLIENTS["one"]),
        extractor=extractor_returning({"fields": []}),
    )
    packet = extractor_only.poll(
        extractor_only.submit(_request(CLIENTS["one"])))["envelope"]["packet"]
    section8 = _split_sections(packet)[8]
    assert "second_pass:" in section8
    assert "generated:" not in section8

    writer_only = LiveProposalProvider(
        StubClient(CLIENTS["one"]),
        writer=writer_returning({"sentences": []}),
    )
    packet = writer_only.poll(
        writer_only.submit(_request(CLIENTS["one"])))["envelope"]["packet"]
    section8 = _split_sections(packet)[8]
    assert "generated:" in section8
    assert "second_pass:" not in section8


# --- the fixture path is untouched ------------------------------------------

def test_a_fixture_run_builds_no_client_of_any_kind(studio, monkeypatch, tmp_path):
    """A fixture run needs no key, of either kind. It reaches neither factory
    nor either client, so the offline path is offline whatever is configured."""
    ui_app, _stub = studio
    monkeypatch.setenv(KEY, "test-key-not-a-real-one")

    def _no_mcp_client():
        raise AssertionError("a fixture run built an MCP client")

    def _no_provider():
        raise AssertionError("a fixture run built the live provider")

    monkeypatch.setattr(ui_app, "_live_client", _no_mcp_client)
    monkeypatch.setattr(ui_app, "_live_provider", _no_provider)
    monkeypatch.setattr(ui_app, "DECKS_ROOT", str(tmp_path))
    captured = {}

    def _stub_pipeline(deck_type, company, project, provider, **kwargs):
        captured["provider"] = provider
        return {"status": "ok", "prompt": "(design prompt body)",
                "applied_preferences": [], "number": 1,
                "render_fidelity": {"ok": True, "missing_values": {}},
                "layout": {"ok": True, "checked": False, "skipped": "not rendered",
                           "slides": 0, "findings": [], "summary": []}}

    monkeypatch.setattr(ui_app, "generate_and_save_deck", _stub_pipeline)
    client = ui_app.app.test_client()

    response = run_deck(client, data={
        "deck_type": "proposal", "company": "Any Client",
        "project": "Any Project", "packet": FIXTURE_PACKET,
    })

    assert response.status_code == 200
    assert isinstance(captured["provider"], FixtureProvider)


# --- what the reviewer is told ----------------------------------------------

def test_the_generate_panel_states_whether_the_prose_passes_are_configured(studio,
                                                                          monkeypatch):
    """SUPERSEDED 2026-09-20 and kept as the record of what replaced it. The
    two paragraphs this asserted were cut in the Generate tab's simplification:
    they stated a fact about the environment that a reviewer could not act on,
    and with a key set the passes always run. What a reviewer CAN act on is
    recorded where it is actionable, on the Result tab, which says what each
    pass read or wrote. With no key the render warning still says the leg is
    off, and that assertion stays below."""
    ui_app, _stub = studio

    monkeypatch.setenv(KEY, "test-key-not-a-real-one")
    with ui_app.app.test_request_context("/"):
        configured = ui_app._generate_panel_html()
    monkeypatch.delenv(KEY, raising=False)
    with ui_app.app.test_request_context("/"):
        unconfigured = ui_app._generate_panel_html()

    assert "ANTHROPIC_API_KEY not set" in unconfigured
    assert "ANTHROPIC_API_KEY not set" not in configured
    assert configured != unconfigured
    # A statement, not a control: no new input of any kind is introduced.
    for markup in ("<input", "<select", "<button"):
        assert configured.count(markup) == unconfigured.count(markup)
