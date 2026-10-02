"""A run outlives its request (2026-08-26).

Held inside its own POST, a five-minute render loses its RESPONSE to whatever
proxy sits in front of the studio. Behind the GTM portal that showed up as a bare
"upstream error" at roughly 300 seconds, on runs that were perfectly healthy and
went on to finish. Our own gunicorn timeout is 600 seconds and never fired: the
limit belonged to a hop we do not control.

So `POST /run` now starts the pipeline on a thread and answers at once with a run
id, the page polls `/run-status`, and the deck is collected from
`GET /?tab=result&run=<id>`.

What these tests are for is the plumbing, not the deck. The deck content is
covered across the rest of the suite and none of it changed. What could break
here is a run finishing while nobody is watching, a poll that disagrees with
reality, a thread that dies without saying so, and two reviewers colliding. Every
run in this file is a stub, so nothing opens a socket or spends an API call.
"""

import os
import re
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ui"))

pytest.importorskip("flask")

from deck_run import run_deck, start_run, wait_for_run  # noqa: E402

PACKET = "proposal-data-packet-EXAMPLE.md"
FORM = {"deck_type": "proposal", "company": "Any Client",
        "project": "Any Project", "packet": PACKET}


@pytest.fixture
def studio(tmp_path, monkeypatch):
    """The app with the pipeline stubbed and both output roots in a temp tree."""
    import app as ui_app

    monkeypatch.setattr(ui_app, "DECKS_ROOT", str(tmp_path))
    monkeypatch.setattr(ui_app, "PROMPTS_ROOT", str(tmp_path))
    ui_app.app.testing = True
    ui_app._LAST_RESULT.clear()
    with ui_app._RUNS_LOCK:
        ui_app._RUNS.clear()
    return ui_app


def _stub_pipeline(ui_app, monkeypatch, deck_html="<html>deck</html>",
                   gate=None, boom=None):
    """Replace the pipeline with something instant, blocking, or exploding.

    ``gate`` is an ``Event`` the fake waits on, which is how a test can hold a run
    open and assert on what the studio does while it is still working.
    """
    deck = os.path.join(str(ui_app.DECKS_ROOT), "output-1.html")

    def _fake(*args, **kwargs):
        if gate is not None:
            gate.wait(timeout=10)
        if boom is not None:
            raise boom
        with open(deck, "w") as handle:
            handle.write(deck_html)
        return {"status": "ok", "deck_path": deck, "applied_preferences": []}

    monkeypatch.setattr(ui_app, "generate_and_save_deck", _fake)
    return deck


# --- the request no longer waits ------------------------------------------


def test_the_post_returns_while_the_run_is_still_working(studio, monkeypatch):
    """The whole point. The response must come back with the pipeline mid-flight.

    Asserted by holding the run open on an Event: if `POST /run` waited for the
    pipeline the way it used to, this test would deadlock until the gate's own
    timeout rather than pass.
    """
    gate = threading.Event()
    _stub_pipeline(studio, monkeypatch, gate=gate)
    client = studio.app.test_client()

    response, run_id = start_run(client, FORM)

    assert response.status_code == 200
    assert run_id, "the response must carry a run id for the page to poll"
    assert not gate.is_set(), "the run has not been released yet"
    assert client.get(f"/run-status?id={run_id}").get_json()["state"] == "running"

    gate.set()
    assert wait_for_run(client, run_id)


def test_the_status_route_reports_running_then_done(studio, monkeypatch):
    gate = threading.Event()
    _stub_pipeline(studio, monkeypatch, gate=gate)
    client = studio.app.test_client()

    _, run_id = start_run(client, FORM)
    running = client.get(f"/run-status?id={run_id}").get_json()
    assert running["state"] == "running"
    assert running["elapsed"] >= 0

    gate.set()
    wait_for_run(client, run_id)
    assert client.get(f"/run-status?id={run_id}").get_json()["state"] == "done"


def test_the_deck_is_collected_from_the_run_id(studio, monkeypatch):
    """The finished run renders the Result tab, which is what the poll navigates to."""
    _stub_pipeline(studio, monkeypatch)
    client = studio.app.test_client()

    _, run_id = start_run(client, FORM)
    wait_for_run(client, run_id)
    collected = client.get(f"/?tab=result&run={run_id}")

    assert collected.status_code == 200
    body = collected.get_data(as_text=True)
    assert 'data-active-tab="result"' in body
    # The run's own inputs came back with it, five minutes after the form was gone.
    assert "Any Client" in body


# --- a run that nobody is watching ----------------------------------------


def test_a_reload_mid_run_keeps_polling_instead_of_losing_the_run(studio, monkeypatch):
    """A reviewer who refreshes during a render must not lose it.

    The run id rides in a cookie scoped to this app, so a plain `GET /` with no
    query string reattaches the overlay and the polling.
    """
    gate = threading.Event()
    _stub_pipeline(studio, monkeypatch, gate=gate)
    client = studio.app.test_client()

    _, run_id = start_run(client, FORM)
    reloaded = client.get("/").get_data(as_text=True)
    assert f'data-run-id="{run_id}"' in reloaded

    gate.set()
    wait_for_run(client, run_id)


def test_a_finished_run_does_not_hijack_a_later_page_load(studio, monkeypatch):
    """The cookie reattaches a RUNNING run and nothing else.

    A finished run must not force the Result tab onto every later navigation, and
    must not stamp a run id that would set the page polling for a run that landed
    long ago.
    """
    _stub_pipeline(studio, monkeypatch)
    client = studio.app.test_client()

    _, run_id = start_run(client, FORM)
    wait_for_run(client, run_id)
    client.get(f"/?tab=result&run={run_id}")          # collect it

    later = client.get("/?tab=decks").get_data(as_text=True)
    assert 'data-active-tab="decks"' in later
    assert "data-run-id=" not in later


def test_a_run_that_no_longer_exists_says_so(studio, monkeypatch):
    """A restart or a redeploy ends a run. Both surfaces have to be honest.

    Not a spinner that never fills, and not a bare Generate tab either: a reviewer
    who waited five minutes is told the run is gone and that nothing was saved.
    """
    _stub_pipeline(studio, monkeypatch)
    client = studio.app.test_client()

    assert client.get("/run-status?id=nosuchrun").get_json() == {"state": "unknown"}

    page = client.get("/?tab=result&run=nosuchrun")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert "no longer available" in body
    assert "Save deck" in body


def test_a_thread_that_explodes_still_lands_the_run(studio, monkeypatch):
    """An unexpected exception must not strand a run on "running" forever.

    Without the worker's `finally` this is the cruellest failure available: the bar
    creeps, the poll says running, and the run died minutes ago.
    """
    _stub_pipeline(studio, monkeypatch, boom=RuntimeError("kaboom"))
    client = studio.app.test_client()

    _, run_id = start_run(client, FORM)
    assert wait_for_run(client, run_id)

    body = client.get(f"/?tab=result&run={run_id}").get_data(as_text=True)
    assert "kaboom" in body


# --- two reviewers --------------------------------------------------------


def test_two_runs_each_collect_their_own_result(studio, monkeypatch):
    """The name tag. Before run ids there was one result slot for the whole app.

    Two reviewers finishing within a few minutes of each other meant the second
    one's deck replaced the first one's, and the first reviewer's Result tab showed
    somebody else's client. Nobody hit it because nobody could finish a run.
    """
    decks = {}

    def _fake(deck_type, company, project, provider, **kwargs):
        path = os.path.join(str(studio.DECKS_ROOT), f"{company}.html")
        with open(path, "w") as handle:
            handle.write(f"<html>{company}</html>")
        decks[company] = path
        return {"status": "ok", "deck_path": path, "applied_preferences": []}

    monkeypatch.setattr(studio, "generate_and_save_deck", _fake)
    first = studio.app.test_client()
    second = studio.app.test_client()

    _, run_a = start_run(first, dict(FORM, company="Client A"))
    wait_for_run(first, run_a)
    _, run_b = start_run(second, dict(FORM, company="Client B"))
    wait_for_run(second, run_b)

    # B finished last, which under the single slot is exactly when A lost its deck.
    body_a = first.get(f"/?tab=result&run={run_a}").get_data(as_text=True)
    assert "Client A" in body_a
    assert "Client B" not in body_a


def test_a_running_run_is_never_evicted_by_retention(studio, monkeypatch):
    """Retention drops finished runs. Evicting a live one would strand its deck."""
    gate = threading.Event()
    _stub_pipeline(studio, monkeypatch, gate=gate)
    client = studio.app.test_client()

    _, live = start_run(client, FORM)
    with studio._RUNS_LOCK:
        for n in range(studio._RUN_RETENTION + 4):
            studio._RUNS[f"old-{n}"] = {"state": "done", "started": float(n),
                                        "elapsed": 0.0, "result": {"status": "ok"},
                                        "inputs": {}}
        studio._prune_runs_locked()
        assert live in studio._RUNS
        assert len([r for r in studio._RUNS.values() if r["state"] == "done"]) \
            == studio._RUN_RETENTION

    gate.set()
    assert wait_for_run(client, live)


# --- the client script the whole thing depends on -------------------------


def test_the_client_script_carries_real_values_for_the_run_constants(studio):
    """The polling constants must be SERVER-FILLED, not left empty.

    Written because this shipped broken and no test noticed. `run_poll_ms` was
    passed to the wrong `render_template_string` call, so `var RUN_POLL_MS = ;`
    reached the browser, the whole inline script died on a SyntaxError, and the
    page rendered with no overlay and no polling at all. Every test still passed,
    because nothing here executes the script.

    A missing Jinja variable renders as EMPTY rather than leaving `{{ ... }}`
    behind, so a leftover-brace check would not have caught it either. These
    assertions read the emitted values.
    """
    body = studio.app.test_client().get("/").get_data(as_text=True)

    poll = re.search(r"var RUN_POLL_MS = (\d+);", body)
    assert poll, "RUN_POLL_MS must be emitted as a number"
    assert int(poll.group(1)) > 0

    for name in ("RUN_STATUS_URL", "RUN_RESULT_URL"):
        found = re.search(rf"var {name} = '([^']+)';", body)
        assert found, f"{name} must be emitted with a path"
        assert found.group(1).startswith("/")

    # The general shape of the failure: any `var X = ;` in the script is a
    # template variable that did not arrive.
    assert not re.search(r"var \w+ = ;", body)


# --- what did not change --------------------------------------------------


def test_a_refusal_still_answers_instantly_with_no_run_started(studio, monkeypatch):
    """The cheap checks stay in the request.

    A packet that is not there costs nothing to detect, so it must not become a
    run the reviewer has to poll. Asserted through `start_run`, which reports an
    empty run id when the studio refused before starting anything.
    """
    _stub_pipeline(studio, monkeypatch)
    client = studio.app.test_client()

    response, run_id = start_run(client, dict(FORM, packet="does-not-exist.md"))

    assert run_id == "", "a refusal must not start a run"
    assert "Packet not found" in response.get_data(as_text=True)


def test_the_run_is_reachable_across_clients_by_id(studio, monkeypatch):
    """The run id, not the cookie, is what owns the result.

    This is what lets the poll deliver a deck after a reload, and what would let a
    reviewer finish on a second device. The cookie is a convenience on top of it.
    """
    _stub_pipeline(studio, monkeypatch)
    starter = studio.app.test_client()
    _, run_id = start_run(starter, FORM)
    wait_for_run(starter, run_id)

    elsewhere = studio.app.test_client()          # no cookie at all
    body = elsewhere.get(f"/?tab=result&run={run_id}").get_data(as_text=True)
    assert 'data-active-tab="result"' in body
    assert "Any Client" in body
