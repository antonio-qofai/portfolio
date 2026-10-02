"""The overlay's expectation, measured instead of asserted.

Antonio, 2026-09-20, on the loading screen: "it says a render usually takes 6
minutes and 53 seconds, which is a lie. So that's really old data." It was not
invented — it is the `LEGS` table summed, and that table came from a real
measurement of a 397-second run — but a constant measured once goes stale, and
the folder rule against hardcoded values covers it as much as a client name.

So the expectation now comes from this machine's own finished runs. These tests
cover the two things that went wrong while building it, both found by running the
studio rather than by the suite:

  * the log was written to the repo root, and the suite promptly filled it with
    25 zero-second entries, because every test that finishes a run finishes it
    instantly; and
  * a median of those zeros would have told a reviewer a render takes no time,
    which is the same lie as the stale constant pointing the other way.
"""

import json
import os
import pathlib
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ui"))

pytest.importorskip("flask")

import app as studio


@pytest.fixture
def log(tmp_path, monkeypatch):
    """A durations log of this test's own."""
    path = tmp_path / "run-durations.json"
    monkeypatch.setenv("RUN_DURATIONS_PATH", str(path))
    return path


def _write(path, mapping):
    path.write_text(json.dumps(mapping), encoding="utf-8")


# --- the floor ------------------------------------------------------------

def test_an_instant_run_is_not_remembered(log):
    """THE BUG THE SUITE CAUSED. A fixture assembly with no render and no model
    call returns in well under a second, and so does every test."""
    studio._log_run_duration("live-render", 0.04)
    studio._log_run_duration("live-render", 0.0)
    assert not log.exists()


def test_a_real_run_is_remembered(log):
    studio._log_run_duration("live-render", 412.5)
    assert json.loads(log.read_text())["live-render"] == [412.5]


def test_runs_are_kept_per_shape(log):
    """A fixture run and a live render differ by minutes. Pooling them would
    quote one's wait to someone running the other."""
    studio._log_run_duration("live-render", 400.0)
    studio._log_run_duration("fixture-render", 40.0)
    kept = json.loads(log.read_text())
    assert kept["live-render"] == [400.0] and kept["fixture-render"] == [40.0]


def test_only_the_newest_runs_are_kept(log):
    _write(log, {"live": [float(100 + i) for i in range(studio._DURATIONS_KEPT)]})
    studio._log_run_duration("live", 999.0)
    kept = json.loads(log.read_text())["live"]
    assert len(kept) == studio._DURATIONS_KEPT
    assert kept[-1] == 999.0 and 100.0 not in kept


# --- the estimate ---------------------------------------------------------

def test_no_estimate_until_three_runs_exist(log):
    """A median of two is a coin toss. Quoting one would state an expectation
    more firmly than the evidence supports, which is the original defect."""
    _write(log, {"live-render": [300.0, 500.0]})
    assert studio._duration_estimate("live-render") is None


def test_the_estimate_is_a_median_with_quartiles(log):
    _write(log, {"live-render": [100.0, 200.0, 300.0, 400.0, 500.0]})
    est = studio._duration_estimate("live-render")
    assert est["median"] == 300.0
    assert est["low"] == 200.0 and est["high"] == 400.0
    assert est["runs"] == 5


def test_one_stalled_run_does_not_move_the_expectation(log):
    """The median, not the mean, and this is why: a mean would carry a single
    stalled run for the next two dozen and make every normal run look late —
    the exact failure the `LEGS` comment records the old bar having."""
    _write(log, {"live-render": [400.0, 410.0, 405.0, 3600.0]})
    assert studio._duration_estimate("live-render")["median"] <= 410.0


def test_a_shape_with_no_history_has_no_estimate(log):
    _write(log, {"live-render": [400.0, 410.0, 405.0]})
    assert studio._duration_estimate("fixture") is None


def test_a_missing_or_corrupt_log_is_not_an_error(log):
    """Best-effort by design: this is a progress bar's estimate, so a
    read-only directory or a half-written file degrades to "no history" and
    the overlay falls back to its own table. A studio that cannot write here
    still renders decks."""
    assert studio._duration_estimate("live-render") is None
    log.write_text("{not json", encoding="utf-8")
    assert studio._duration_estimate("live-render") is None
    studio._log_run_duration("live-render", 400.0)  # must not raise


def test_the_log_is_not_written_to_the_repo_root(monkeypatch, tmp_path):
    """Where the first cut put it, which is how the suite polluted it. With no
    override the path sits inside the RESOLVED store directory, which
    `conftest`'s autouse fixture already points at a tmp_path."""
    monkeypatch.delenv("RUN_DURATIONS_PATH", raising=False)
    path = pathlib.Path(studio._durations_path())
    repo_root = pathlib.Path(studio.__file__).resolve().parent.parent
    assert path.parent != repo_root, path


# --- what the page is handed ----------------------------------------------

def test_the_page_carries_the_history_it_has(log):
    """Wired, not merely computed. The overlay reads `RUN_HISTORY`, so a value
    that never reaches the page is a feature that does nothing."""
    _write(log, {"live-render": [400.0, 410.0, 405.0]})
    with studio.app.test_request_context("/"):
        page = studio.render_studio("generate")
    assert "var RUN_HISTORY = {" in page
    assert '"live-render"' in page
    assert "run_history_json" not in page


def test_a_studio_with_no_history_still_renders_the_page(log):
    """And the script must stay valid JavaScript: an unrendered Jinja
    expression here would be `var RUN_HISTORY = ;`, a syntax error that kills
    the whole page script and with it every tab."""
    with studio.app.test_request_context("/"):
        page = studio.render_studio("generate")
    assert "var RUN_HISTORY = {}" in page
