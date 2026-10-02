"""Tests for the scope-assessment status path (H1) — conceptual, not a provider.

The path under test reads the only scope that exists (a published opportunity's
`<Chart>` implementation timeline, in relative months with no start date and no
progress state) and produces a status packet whose scope is sourced and whose
completion states are the agent's own. What these tests pin is the separation:
the scope carries a span and the states do not, so the states are labeled in the
packet's own copy, flagged in §5 gaps, and unconfirmable without a reviewer.

Runs against two of the committed research-paper excerpts under
`data-provider/fixtures/`, and against the one that carries no timeline chart for
the `E_NO_SCHEDULE` path. Every company, project and opportunity reference is
supplied by the test, never by the module.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import status_scope
from data_source_adapter import _status_gap_roles
from deck_generator import generate_deck_prompt
from gap_resolver import describe_gap, list_gaps
from live_proposal_provider import ProviderError

FIXTURES = os.path.join(os.path.dirname(__file__), "..", "data-provider", "fixtures")

WITH_TIMELINE = "paper-excerpt-scenario-columns.json"
SECOND_WITH_TIMELINE = "paper-excerpt-scenario-rows-canonical.json"
NO_TIMELINE = "paper-excerpt-no-timeline-chart.json"

# Two callers, so nothing below can pass by carrying one company's detail.
FIRST = {
    "company": {"id": "c-1", "name": "Meridian Plastics", "client_short": "Meridian",
                "pe_firm": None},
    "project": {"id": "p-1", "name": "Implementation"},
}
SECOND = {
    "company": {"id": "c-2", "name": "Ashgrove Terminals", "client_short": "Ashgrove",
                "pe_firm": None},
    "project": {"id": "p-2", "name": "Rollout"},
}


def _paper(name):
    with open(os.path.join(FIXTURES, name)) as handle:
        return json.load(handle)["opportunity"]


def _provider(fixture=WITH_TIMELINE, caller=FIRST, elapsed_months=4):
    opportunity = _paper(fixture)
    return status_scope.StatusScopeProvider(
        company=caller["company"],
        project=caller["project"],
        opportunity={"id": opportunity["id"]},
        paper=opportunity.get("research_paper_natural"),
        elapsed_months=elapsed_months,
        generated_at="2026-08-17T00:00:00Z",
    )


def _run(caller=FIRST, sections=("cover", "workstreams"), **kwargs):
    return generate_deck_prompt(
        "status", caller["company"]["name"], caller["project"]["name"],
        _provider(caller=caller, **kwargs),
        poll_interval=0.0, sleep=lambda _seconds: None,
        sections_requested=list(sections) if sections else None,
        check_in_date="2026-05-22",
    )


def _packet(caller=FIRST, **kwargs):
    provider = _provider(caller=caller, **kwargs)
    handle = provider.submit({"check_in_date": "2026-05-22"})
    return provider.poll(handle)["envelope"]


# ---- there is no plan of record, so a paper with no timeline has no scope ----

def test_a_paper_with_no_timeline_is_e_no_schedule():
    envelope = _packet(fixture=NO_TIMELINE)
    assert envelope["status"] == "error", envelope
    assert envelope["error"]["code"] == "E_NO_SCHEDULE", envelope
    assert envelope["error"]["remediation"], envelope
    assert "packet" not in envelope, envelope


def test_e_no_schedule_reaches_the_caller_as_an_error_with_no_prompt():
    result = _run(fixture=NO_TIMELINE)
    assert result["status"] == "error", result
    assert result["code"] == "E_NO_SCHEDULE", result
    assert "prompt" not in result, result


def test_assess_scope_raises_rather_than_returning_an_empty_scope():
    try:
        status_scope.assess_scope(_paper(NO_TIMELINE).get("research_paper_natural"))
    except ProviderError as error:
        assert error.code == "E_NO_SCHEDULE", error.code
        assert "timeline" in error.message, error.message
    else:
        raise AssertionError("a paper with no timeline must not assess as scope")


# ---- the scope is sourced: every label occurs in the span it came from ----

def test_every_scope_label_occurs_verbatim_in_its_own_source_span():
    figure, items = status_scope.assess_scope(
        _paper(WITH_TIMELINE)["research_paper_natural"], elapsed_months=4
    )
    assert items, "the fixture carries a timeline chart"
    for item in items:
        assert item["label"] in figure.span, item["label"]


# ---- the completion state is not sourced, and says so in three places ----

def test_no_elapsed_position_means_every_state_is_unknown():
    _figure, items = status_scope.assess_scope(
        _paper(WITH_TIMELINE)["research_paper_natural"], elapsed_months=None
    )
    assert {item["origin"] for item in items} == {"UNKNOWN"}, items
    for item in items:
        assert "UNKNOWN" in item["detail"], item["detail"]
        assert "INFERRED" not in item["detail"], item["detail"]


def test_an_elapsed_position_infers_a_state_and_names_it_inferred():
    _figure, items = status_scope.assess_scope(
        _paper(WITH_TIMELINE)["research_paper_natural"], elapsed_months=4
    )
    # Months 0-3, 3-6 and 6-12 against elapsed month 4: closed, open, unopened.
    assert [item["state"] for item in items] == ["done", "in_process", "pending"], items
    assert {item["origin"] for item in items} == {"INFERRED"}, items
    for item in items:
        assert "INFERRED" in item["detail"] and "unconfirmed" in item["detail"], item


def test_every_state_is_flagged_in_the_packets_own_gaps():
    envelope = _packet()
    gaps = list_gaps(envelope["packet"])
    _figure, items = status_scope.assess_scope(
        _paper(WITH_TIMELINE)["research_paper_natural"], elapsed_months=4
    )
    assert len(gaps) == len(items), gaps
    for index, gap in enumerate(gaps):
        assert gap["field"].endswith(f"items[{index}].state"), gap
        assert "no source span" in gap["reason"], gap


def test_each_flagged_state_locates_on_the_reviewers_checklist():
    gaps = list_gaps(_packet()["packet"])
    assert gaps, "an empty checklist would pass this test vacuously"
    for gap in gaps:
        located = describe_gap(gap["field"], deck_type="status", n_workstreams=1)
        assert located["slide_number"] == 3, located
        assert "progress" in located["where"], located


# ---- the adapter half: a progress-tracker gap flags the progress list ----

def test_a_progress_tracker_gap_flags_the_progress_items_role():
    top, per_ws = _status_gap_roles(
        ["workstreams[0].progress_tracker.groups[0].items[2].state"], 1
    )
    assert top == [], top
    assert per_ws[0] == {"progress_items"}, per_ws


def test_a_progress_tracker_gap_past_the_last_workstream_flags_nothing():
    _top, per_ws = _status_gap_roles(
        ["workstreams[3].progress_tracker.groups[0].items[0].state"], 1
    )
    assert per_ws[0] == set(), per_ws


# ---- rendered: a sourced item and an inferred item, side by side ----

def _workstream_slide(prompt):
    return prompt[prompt.index("## Slide 2 —"):]


def test_the_rendered_slide_carries_a_sourced_item_beside_an_inferred_one():
    result = _run()
    assert result["status"] == "ok", result
    slide = _workstream_slide(result["prompt"])
    # The sourced half: a figure read off the timeline chart, named as sourced.
    assert "3 phases · scoped in the proposed implementation plan, sourced" in slide, slide
    # The inferred half, on the same slide, named as inferred and unconfirmed.
    assert "completion state INFERRED at elapsed month 4" in slide, slide
    assert slide.index("frame_a_metrics") < slide.index("progress_items"), slide


def test_the_whole_progress_list_renders_the_unconfirmed_marker():
    slide = _workstream_slide(_run()["prompt"])
    progress = slide[slide.index("progress_items"):]
    assert "(unconfirmed, see gaps)" in progress, progress


def test_no_state_reaches_the_deck_unlabeled():
    slide = _workstream_slide(_run()["prompt"])
    states = [line for line in slide.splitlines() if line.strip().startswith("state:")]
    assert len(states) == 3, states
    labels = [line for line in slide.splitlines() if line.strip().startswith("label:")]
    for label in labels:
        assert "completion state" in label, label


# ---- the tracking slide stays empty rather than inventing a plan ----

def test_the_tracking_slide_synthesizes_no_today_marker_and_no_gantt():
    # A dated Gantt and a TODAY marker need a plan start date, which no source
    # records. The contract calls both load-bearing and forbids synthesizing
    # them, so on a full-deck request they must come through as missing rather
    # than as a drawn timeline the client would read as real.
    result = _run(sections=None)
    assert result["status"] == "ok", result
    prompt = result["prompt"]
    tracking = prompt[prompt.index("## Slide 2 —"):prompt.index("## Slide 3 —")]
    for role in ("today_marker_week", "today_marker_label", "timeline_columns",
                 "gantt_bars"):
        assert f"{role}: [MISSING: {role}]" in tracking, (role, tracking)


# ---- anti-hardcoding: a second caller, a second paper, no leakage ----

def test_a_second_caller_leaks_nothing_from_the_first():
    first = _run()["prompt"]
    second = _run(caller=SECOND, fixture=SECOND_WITH_TIMELINE)["prompt"]
    for banned in ("Meridian", "Standardized Quoting", "Dynamic Pricing Intelligence"):
        assert banned not in second, banned
    assert "Ashgrove" in second and "Ashgrove" not in first, (first[:200], second[:200])
    assert "Decision Rule Digitization" in second, second
