"""What the studio shows when a model call was bounded, failed, or was ignored.

The three surfaces the 2026-09-02 batch added or repaired, driven through the
real app rather than asserted on the strings that feed it:

  * a gate failure that coincides with a failed extraction pass says a MODEL CALL
    failed, above the score, because the score is otherwise read as a verdict on
    the source data;
  * a bounded leg that gave up carries remediation, where a bare
    `str(exc)` used to leave "The read operation timed out" on screen with
    nothing to do about it after ten-plus minutes;
  * the progress bar's stage table is baselined on the measured run rather than
    on the assumption that the render is almost all of it.

No network. Every failure is injected.
"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ui"))

pytest.importorskip("flask")

import model_call  # noqa: E402
from deck_run import run_deck  # noqa: E402


@pytest.fixture
def studio(tmp_path, monkeypatch):
    import app as ui_app
    import deck_generator

    # Pinned back for the same reason `test_ui_live_failures` pins it: another
    # file replaces this attribute by plain assignment, and a run that always
    # succeeds would make every test here pass for the wrong reason.
    monkeypatch.setattr(
        ui_app, "generate_and_save_deck", deck_generator.generate_and_save_deck
    )
    monkeypatch.setattr(ui_app, "DECKS_ROOT", str(tmp_path))
    monkeypatch.setattr(ui_app, "PROMPTS_ROOT", str(tmp_path))
    ui_app.app.testing = False
    ui_app._LAST_RESULT.clear()
    return ui_app


@pytest.fixture
def client(studio):
    return studio.app.test_client()


FIXTURE_RUN = {"deck_type": "proposal", "company": "Any Client",
               "project": "Any Project",
               "packet": "proposal-data-packet-EXAMPLE.md"}


# --- a bounded leg that gave up --------------------------------------------

def test_a_leg_that_gave_up_says_how_long_it_was_given(studio, client, monkeypatch):
    """`model_call.ModelCallError`'s own message, on screen. The SDK's is "The
    read operation timed out", which does not say whether to wait or to re-run."""
    def give_up(*args, **kwargs):
        raise model_call.ModelCallError(
            "render", 2, 420.0, TimeoutError("The read operation timed out"))

    monkeypatch.setattr(studio, "generate_and_save_deck", give_up)
    body = run_deck(client, data=FIXTURE_RUN).get_data(as_text=True)
    assert "render" in body
    assert "2 attempts" in body
    assert "420s" in body


def test_a_leg_that_gave_up_carries_remediation(studio, client, monkeypatch):
    """The half that was missing entirely. A reviewer ten minutes into a stalled
    run needs to be told that a re-run is the move and that the wait was not the
    problem."""
    def give_up(*args, **kwargs):
        raise model_call.ModelCallError(
            "render", 2, 420.0, TimeoutError("The read operation timed out"))

    monkeypatch.setattr(studio, "generate_and_save_deck", give_up)
    body = run_deck(client, data=FIXTURE_RUN).get_data(as_text=True)
    assert "Nothing was written." in body
    assert "Re-run" in body
    assert "not a slow answer that needed more time" in body


def test_an_ordinary_render_failure_still_reads_as_it_did(studio, client, monkeypatch):
    """No remediation is invented for a failure that carries none."""
    def blow_up(*args, **kwargs):
        raise RuntimeError("simulated API failure")

    monkeypatch.setattr(studio, "generate_and_save_deck", blow_up)
    body = run_deck(client, data=FIXTURE_RUN).get_data(as_text=True)
    assert "simulated API failure" in body
    assert "not a slow answer that needed more time" not in body


# --- a gate failure after a failed pass ------------------------------------

FAILED_PASS_ENVELOPE = {
    "status": "error",
    "code": "E_LOW_CONFIDENCE",
    "message": ("The assembled packet scores 0.33 against a 0.7 floor. This "
                "score is the deterministic parsers' alone: the second "
                "extraction pass did not complete (ReadTimeout: The read "
                "operation timed out), so the 10 field(s) it was asked to read "
                "out of the paper's prose were never read."),
    "remediation": "A model call failed on this run, so re-run before ...",
    "details": {
        "confidence": "low",
        "data_completeness": 0.3333333333333333,
        "missing_fields": ["baseline.revenue_ttm_usd"],
        "second_pass_failed": "ReadTimeout: The read operation timed out",
        "absent_after_failed_pass": [
            {"field": "baseline.revenue_ttm_usd",
             "reason": "the second extraction pass did not complete (...)"},
            {"field": "baseline.adjusted_ebitda_usd",
             "reason": "the second extraction pass did not complete (...)"},
        ],
    },
}


def _with_envelope(studio, client, monkeypatch, envelope):
    monkeypatch.setattr(studio, "generate_and_save_deck",
                        lambda *args, **kwargs: dict(envelope))
    return run_deck(client, data=FIXTURE_RUN).get_data(as_text=True)


def test_a_failed_pass_is_named_above_the_score(studio, client, monkeypatch):
    """The defect this surface exists for: the reviewer's next move after
    reading "the packet scores 0.33" was to go and inspect the research paper,
    which contained all four figures."""
    body = _with_envelope(studio, client, monkeypatch, FAILED_PASS_ENVELOPE)
    assert "A model call failed on this run" in body
    assert "not a verdict on the source data" in body
    # Collapsed, because the template wraps the sentence across source lines.
    assert "Re-run before going to" in " ".join(body.split())


def test_the_fields_the_failed_pass_never_read_are_listed(studio, client, monkeypatch):
    body = _with_envelope(studio, client, monkeypatch, FAILED_PASS_ENVELOPE)
    assert "never read" in body
    assert "baseline.adjusted_ebitda_usd" in body


def test_the_per_field_reason_is_not_printed_once_per_field(studio, client, monkeypatch):
    """Every entry carries the same sentence, so printing it per field turned the
    details row into ten identical paragraphs."""
    body = _with_envelope(studio, client, monkeypatch, FAILED_PASS_ENVELOPE)
    assert body.count("the second extraction pass did not complete (...)") == 0


def test_an_error_envelope_with_no_failed_pass_shows_no_callout(studio, client, monkeypatch):
    envelope = dict(FAILED_PASS_ENVELOPE)
    envelope["details"] = {"confidence": "low", "data_completeness": 0.5,
                           "missing_fields": []}
    envelope["message"] = "The assembled packet scores 0.50 against a 0.7 floor."
    envelope["remediation"] = "Do not render; return for human review."
    body = _with_envelope(studio, client, monkeypatch, envelope)
    assert "A model call failed on this run" not in body
    assert "confidence: low" in body


def test_an_error_envelope_whose_details_is_a_bare_string_still_renders(studio, client, monkeypatch):
    """Two studio-side envelopes carry `details: ""`, so nothing may read a key
    off it."""
    body = _with_envelope(studio, client, monkeypatch, {
        "status": "error", "code": "packet_inconsistent",
        "message": "the packet contradicts itself",
        "remediation": "fix it at the source", "details": "",
    })
    assert "packet_inconsistent" in body
    assert "A model call failed on this run" not in body


# --- the progress bar's baseline -------------------------------------------

def _legs(body):
    """The per-leg durations the served page carries, as `{key: (seconds, label)}`."""
    block = re.search(r"var LEGS = \{(.*?)\};", body, re.DOTALL)
    assert block, "the served page carries no leg table"
    rows = re.findall(r"(\w+):\s*\[(\d+),\s*.(.*?).\]", block.group(1))
    return {key: (int(seconds), label) for key, seconds, label in rows}


def _shapes(body):
    """The legs each run shape has, as `{shape: [leg, ...]}`."""
    block = re.search(r"var SHAPES = \{(.*?)\};", body, re.DOTALL)
    assert block, "the served page carries no shape table"
    rows = re.findall(r"'([\w-]+)':\s*\[(.*?)\]", block.group(1), re.DOTALL)
    return {shape: re.findall(r"'(\w+)'", legs) for shape, legs in rows}


def _stages(body, shape="live-render"):
    """The stage table for one shape, computed the way the page's `stagesFor`
    computes it: `[(second the leg begins, percent, label)]`.

    Read off the served page rather than off the source, so a template that
    stopped emitting either table fails here.
    """
    legs, shapes = _legs(body), _shapes(body)
    keys = shapes[shape]
    total = sum(legs[key][0] for key in keys)
    stages, at = [], 0
    for key in keys:
        stages.append((at, 2 + round(93 * at / total), legs[key][1]))
        at += legs[key][0]
    return stages, total + 15


# The measured clean live run of 2026-09-02: MCP fetch 1.3s, parsers 0.0s,
# extraction 102s, writing 59s, ranking 3s, render 229s, layout guard 2.1s, for
# about 397s in total with the render beginning around second 166.
MEASURED_RENDER_START_S = 166
MEASURED_RUN_S = 397


def test_the_render_stage_starts_when_the_render_starts(client):
    """It began at second 12 until 2026-09-02, so the bar spent the first two and
    a half minutes claiming to render while the extraction and writing passes
    were what was actually running."""
    stages, _expected = _stages(client.get("/").get_data(as_text=True))
    render = [second for second, _percent, label in stages
              if "Rendering" in label]
    assert len(render) == 1, stages
    assert abs(render[0] - MEASURED_RENDER_START_S) <= 15, render[0]


def test_the_model_passes_that_run_first_are_named(client):
    """A bar that says "rendering" for the first 166 seconds is not merely
    imprecise: it hides which leg a slow run is slow in."""
    stages, _expected = _stages(client.get("/").get_data(as_text=True))
    labels = " ".join(label for _s, _p, label in stages)
    assert "prose" in labels, labels
    assert "Writing the framing lines" in labels, labels
    assert "Ranking" in labels, labels


def test_the_expectation_matches_the_measured_run(client):
    """Baselined on 5 minutes against a 6.6-minute run, the old table made every
    healthy run announce that it had outlasted expectations at the moment the
    render hit its stride — which is how a genuinely stalled one stopped
    standing out."""
    _stage_rows, expected = _stages(client.get("/").get_data(as_text=True))
    assert abs(expected - MEASURED_RUN_S) <= 30, expected


def test_the_expectation_is_not_padded_out_to_cover_a_stall(client):
    """Quoting the render leg's own bound as the expectation would advertise a
    stall as a normal wait, which is the opposite of the point."""
    import deck_renderer

    _stage_rows, expected = _stages(client.get("/").get_data(as_text=True))
    assert expected < deck_renderer.DEFAULT_TIMEOUT_S


def test_the_table_rises_and_stays_under_the_finish_line(client):
    """The easing and the cap are the parts that already worked, and a
    re-baselining must not break either: the seconds have to increase, the
    percentages have to increase, and the last one has to stay short of 100 so
    only the page navigation completes the bar."""
    body = client.get("/").get_data(as_text=True)
    for shape in _shapes(body):
        stages, _expected = _stages(body, shape)
        seconds = [second for second, _p, _l in stages]
        percents = [percent for _s, percent, _l in stages]
        assert seconds == sorted(seconds), (shape, seconds)
        assert len(set(seconds)) == len(seconds), (shape, seconds)
        assert percents == sorted(percents), (shape, percents)
        assert percents[-1] < 100, (shape, percents)
        assert percents[0] >= 2, (shape, percents)


# --- and it is the table for the run being watched, not always the live one ---

def test_a_fixture_run_is_not_narrated_as_a_live_one(client):
    """The regression the shape table exists to prevent. A fixture run makes no
    model pass at all, so a bar baselined on the live run would spend its first
    169 seconds naming three passes that are not running — and a fixture render
    is the shorter half of what this studio does."""
    body = client.get("/").get_data(as_text=True)
    fixture, expected = _stages(body, "fixture-render")
    labels = " ".join(label for _s, _p, label in fixture)
    assert "prose" not in labels, labels
    assert "Ranking" not in labels, labels
    assert "Rendering the slides via Claude" in labels, labels
    # The render starts almost at once and is nearly the whole run.
    render = [second for second, _p, label in fixture if "Rendering" in label][0]
    assert render <= 10, render
    assert expected < _stages(body, "live-render")[1]


def test_a_run_with_no_render_ends_when_the_prompt_is_assembled(client):
    """It spends no time in the render leg, so an expectation that includes one
    would leave the bar at half mast for a run that has already finished."""
    body = client.get("/").get_data(as_text=True)
    for shape in ("live", "fixture"):
        stages, expected = _stages(body, shape)
        labels = " ".join(label for _s, _p, label in stages)
        assert "Rendering" not in labels, (shape, labels)
        assert "assembling the prompt" in labels, (shape, labels)
        assert expected < _stages(body, shape + "-render")[1]


def test_every_shape_the_server_can_report_has_a_table(studio, client):
    """The two halves have to agree on the names, or a reload mid-run silently
    keeps whichever table the page started with."""
    shapes = _shapes(client.get("/").get_data(as_text=True))
    for source in ("live", "fixture"):
        for render in (True, False):
            rec = {"inputs": {"data_source": source, "do_render": render}}
            assert studio._run_shape(rec) in shapes, (source, render)


def test_the_server_reports_the_shape_of_a_run_in_flight(studio, client):
    """The form can answer at submit time; only the server can answer after a
    reload has reset it."""
    assert studio._run_shape(
        {"inputs": {"data_source": "live", "do_render": True}}) == "live-render"
    assert studio._run_shape(
        {"inputs": {"data_source": "live", "do_render": False}}) == "live"
    assert studio._run_shape(
        {"inputs": {"data_source": "fixture", "do_render": True}}) == "fixture-render"
    assert studio._run_shape({"inputs": {}}) == "fixture"


def test_a_run_status_poll_carries_the_shape(studio, client):
    """A running poll answers with it; a finished one has nothing left to narrate."""
    run_id = studio._new_run({"data_source": "live", "do_render": True})
    try:
        payload = client.get("/run-status", query_string={"id": run_id}).get_json()
        assert payload["state"] == "running"
        assert payload["shape"] == "live-render"
    finally:
        studio._RUNS.pop(run_id, None)


def test_the_still_working_message_survives(client):
    """The reasoning around the table still holds; only its threshold and its
    boundaries were wrong."""
    body = client.get("/").get_data(as_text=True)
    assert "Still working" in body
