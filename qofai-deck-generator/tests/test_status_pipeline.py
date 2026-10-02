"""Tests for the status deck path (PRD §5, success criteria S1–S13).

Mirrors the proposal test files, exercising the deterministic status pipeline —
mapping (`map_status_packet`), assembler (variable-count workstream slides),
guards (coverage + render fidelity), and numbering — with a stub renderer, no
API key needed. Runs against the frozen status fixture
(`status-data-packet-EXAMPLE.md`, Northwind, 2 workstreams) and a second synthetic
packet (`templates/packets/status-data-packet-second.md`, Vantgo, 3 workstreams)
for the variable-count (S7) and anti-hardcoding (S8) criteria.

Run with: python3 tests/test_status_pipeline.py
"""

import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from coverage_guard import CoverageError, check_coverage
from data_source_adapter import (
    FixtureProvider,
    map_status_packet,
    run_adapter,
    shape_request,
    STATUS_ALL_SECTIONS,
)
from deck_generator import generate_and_save_deck, generate_deck_prompt
from prompt_assembler import assemble_prompt
from render_guard import RenderFidelityError, check_render_fidelity
from template_loader import load_template

STATUS_PACKET_PATH = os.path.join(
    os.path.dirname(__file__), "..", "status-data-packet-EXAMPLE.md"
)
SECOND_PACKET_PATH = os.path.join(
    os.path.dirname(__file__), "..", "templates", "packets", "status-data-packet-second.md"
)
STATUS_TEMPLATE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "templates", "status-template.md"
)

STATUS_PROJECT = "Implementation Project"


def _no_sleep(_):
    pass


def _packet_with(confidence, data_completeness):
    return (
        f'---\nconfidence: "{confidence}"\n'
        f"data_completeness: {data_completeness}\n---\n\n# stub body\n"
    )


def _error_envelope(code, message="msg", remediation="do this"):
    return {
        "status": "error",
        "error": {
            "code": code,
            "message": f"{code}: {message}",
            "remediation": remediation,
            "details": {},
        },
    }


def _status_prompt(packet_path=STATUS_PACKET_PATH, project=STATUS_PROJECT,
                   company="Northwind", **kwargs):
    provider = FixtureProvider.from_packet_file(packet_path)
    result = generate_deck_prompt(
        "status", company, project, provider,
        poll_interval=0.0, sleep=_no_sleep, **kwargs,
    )
    return result


def _slide_headers(prompt):
    return re.findall(r"## Slide (\d+) — (.+)", prompt)


# ---- S1 / S2: clean packet → one section per slide, variable structure ----

def test_status_clean_packet_returns_full_prompt():
    result = _status_prompt()
    assert result["status"] == "ok", result
    assert result["confidence"] == "high", result
    assert result["data_completeness"] == 0.9, result
    assert result["prompt"].rstrip().endswith("Not a finished or sendable deck.")


def test_status_section_list_is_cover_tracking_N_workstreams_next_steps():
    # S2: the section list is cover + tracking + N workstreams + next_steps, in
    # packet order, numbered sequentially 1..(N+3). Northwind's N = 2 → 5 slides.
    result = _status_prompt()
    headers = _slide_headers(result["prompt"])
    numbers = [int(n) for n, _ in headers]
    assert numbers == [1, 2, 3, 4, 5], numbers
    titles = [t for _, t in headers]
    assert "Cover" in titles[0]
    assert "Project Tracking" in titles[1]
    assert "Workstream Status" in titles[2] and "Workstream Status" in titles[3]
    assert "Next Steps" in titles[4]


def test_status_wired_prompt_matches_calling_modules_directly():
    wired = _status_prompt()
    template = load_template(STATUS_TEMPLATE_PATH)
    provider = FixtureProvider.from_packet_file(STATUS_PACKET_PATH)
    manual = run_adapter(
        "Northwind", STATUS_PROJECT, provider, deck_type="status",
        poll_interval=0.0, sleep=_no_sleep,
    )
    manual_prompt = assemble_prompt(template, manual["placeholder_map"])
    assert wired["prompt"] == manual_prompt


# ---- S4: time-aware fields come from the packet, never the render date ----

def test_status_time_fields_from_packet():
    result = _status_prompt()
    prompt = result["prompt"]
    # project week from the packet's derived project_week (Week 3 of 12).
    assert "project_week: Week 3 of 12" in prompt, prompt
    # TODAY marker lands on the packet's today_marker.on_week column (W3).
    assert "today_marker_week: W3" in prompt, prompt
    assert "today_marker_label: TODAY" in prompt, prompt
    # check-in date is the packet's display form, not an ISO or render date.
    assert "check_in_date: MAY 22 2026" in prompt, prompt


def test_status_map_reads_time_fields_from_engagement_block():
    provider = FixtureProvider.from_packet_file(STATUS_PACKET_PATH)
    result = run_adapter(
        "Northwind", STATUS_PROJECT, provider, deck_type="status",
        poll_interval=0.0, sleep=_no_sleep,
    )
    m = result["placeholder_map"]
    assert m["project_week"] == "Week 3 of 12", m["project_week"]
    assert m["today_marker_week"] == "W3", m["today_marker_week"]
    assert m["month_year"] == "MAY 2026", m["month_year"]


# ---- S5: confidence / completeness gate declines to render ----

def test_status_low_confidence_declines():
    provider = FixtureProvider.from_packet_markdown(_packet_with("low", 0.92))
    result = generate_deck_prompt(
        "status", "Some Co", "Some Project", provider,
        poll_interval=0.0, sleep=_no_sleep,
    )
    assert result["status"] == "review", result
    assert result["confidence"] == "low", result
    assert "prompt" not in result, result


def test_status_low_completeness_declines():
    provider = FixtureProvider.from_packet_markdown(_packet_with("high", 0.55))
    result = generate_deck_prompt(
        "status", "Some Co", "Some Project", provider,
        poll_interval=0.0, sleep=_no_sleep,
    )
    assert result["status"] == "review", result
    assert "prompt" not in result, result


# ---- S6: error handling, including the two status-only schedule-gate codes ----

def test_status_schedule_gate_codes_surface_no_prompt():
    for code in ("E_NO_SCHEDULE", "E_NO_ACTIVE_WORKSTREAMS"):
        provider = FixtureProvider.from_envelope(_error_envelope(code))
        result = generate_deck_prompt(
            "status", "Some Co", "Some Project", provider,
            poll_interval=0.0, sleep=_no_sleep,
        )
        assert result["status"] == "error", (code, result)
        assert result["code"] == code, (code, result)
        assert result["message"] and result["remediation"], (code, result)
        assert "prompt" not in result, (code, result)


def test_status_shared_error_codes_surface_no_prompt():
    for code in ("E_NO_KG", "E_PROJECT_NOT_FOUND", "E_BAD_REQUEST"):
        provider = FixtureProvider.from_envelope(_error_envelope(code))
        result = generate_deck_prompt(
            "status", "Some Co", "Some Project", provider,
            poll_interval=0.0, sleep=_no_sleep,
        )
        assert result["status"] == "error", (code, result)
        assert result["code"] == code, (code, result)
        assert "prompt" not in result, (code, result)


# ---- S7: variable slide count, enforced end to end ----

def test_status_footer_denominator_reads_total_slides():
    result = _status_prompt()
    assert "total_slides: 5" in result["prompt"], result["prompt"]


def test_status_three_workstreams_produce_six_slides_no_code_change():
    # A synthetic packet with N = 3 produces 6 slides and total_slides 6, with
    # no code change — nothing assumes five (or any fixed count).
    result = _status_prompt(
        packet_path=SECOND_PACKET_PATH, project="Fulfillment Automation",
        company="Vantgo Logistics",
    )
    assert result["status"] == "ok", result
    numbers = [int(n) for n, _ in _slide_headers(result["prompt"])]
    assert numbers == [1, 2, 3, 4, 5, 6], numbers
    assert "total_slides: 6" in result["prompt"], result["prompt"]


def test_status_request_profile_uses_status_intent_and_sections():
    request = shape_request("Northwind", STATUS_PROJECT, deck_type="status",
                            check_in_date="2026-05-22")
    assert request["intent"] == "generate_project_status_deck", request
    assert request["sections_requested"] == list(STATUS_ALL_SECTIONS), request
    assert request["check_in_date"] == "2026-05-22", request
    assert request["options"]["workstream_filter"] == "active", request


# ---- S8: anti-hardcoding — zero cross-client leakage, filed per client ----

def test_status_second_client_no_northwind_leakage():
    result = _status_prompt(
        packet_path=SECOND_PACKET_PATH, project="Fulfillment Automation",
        company="Vantgo Logistics",
    )
    prompt = result["prompt"]
    for banned in ("Woodgrove Partners", "Northwind", "Dynamic Pricing", "Production Scheduling",
                   "Planwright", "−14.0pp", "Coil", "Charlotte"):
        assert banned not in prompt, f"leaked Northwind content: {banned}"
    assert "client_short: Vantgo" in prompt, prompt
    assert result["client_short"] == "Vantgo", result


def test_status_two_clients_file_into_separate_folders():
    def stub(prompt, **kwargs):
        return "<!doctype html><body>D<p>(unconfirmed, see gaps)</p></body>"

    with tempfile.TemporaryDirectory() as root_p, tempfile.TemporaryDirectory() as root_d:
        northwind = generate_and_save_deck(
            "status", "Northwind", STATUS_PROJECT,
            FixtureProvider.from_packet_file(STATUS_PACKET_PATH),
            prompts_root=root_p, decks_root=root_d, renderer=stub,
            poll_interval=0.0, sleep=_no_sleep,
        )
        vantgo = generate_and_save_deck(
            "status", "Vantgo Logistics", "Fulfillment Automation",
            FixtureProvider.from_packet_file(SECOND_PACKET_PATH),
            prompts_root=root_p, decks_root=root_d, renderer=stub,
            poll_interval=0.0, sleep=_no_sleep,
        )
        assert northwind["status"] == "ok" and vantgo["status"] == "ok"
        # Both start at their own N=1 in their own client folder.
        assert northwind["number"] == 1 and vantgo["number"] == 1, (northwind, vantgo)
        assert os.sep + "Northwind" + os.sep in northwind["prompt_path"], northwind["prompt_path"]
        assert os.sep + "Vantgo" + os.sep in vantgo["prompt_path"], vantgo["prompt_path"]


# ---- S9: null-vs-skipped handled distinctly ----

def test_status_stage_unused_framing_blocks_omitted_not_flagged():
    # A new-stage workstream's where_we_are/target are null by design; an
    # existing-stage workstream's today/after likewise. Neither surfaces as a
    # missing marker — the mapping selects the framing by stage (S9/S12).
    result = _status_prompt()
    prompt = result["prompt"]
    assert "[MISSING" not in prompt, prompt


def test_status_skipped_section_omitted_cleanly():
    # Requesting only cover + tracking + workstreams drops the next_steps slide
    # cleanly, with no marker. Slide count falls to 4 (cover + tracking + 2 ws).
    result = _status_prompt(
        sections_requested=["cover", "tracking", "workstreams"],
    )
    prompt = result["prompt"]
    numbers = [int(n) for n, _ in _slide_headers(prompt)]
    assert numbers == [1, 2, 3, 4], numbers
    assert "Next Steps" not in prompt, prompt
    assert "[MISSING" not in prompt, prompt


def test_status_optional_progress_right_label_omitted_when_absent():
    # The new workstream has no progress right-label (null); it must be omitted
    # cleanly, not rendered as a missing marker. The existing workstream has one.
    result = _status_prompt()
    prompt = result["prompt"]
    assert prompt.count("progress_right_label:") == 1, prompt
    assert "progress_right_label: WEEK 3 OF 12" in prompt, prompt


# ---- S11: Gantt bars colored by category, state is annotation only ----

def test_status_gantt_bars_carry_category_and_state_independently():
    provider = FixtureProvider.from_packet_file(STATUS_PACKET_PATH)
    result = run_adapter(
        "Northwind", STATUS_PROJECT, provider, deck_type="status",
        poll_interval=0.0, sleep=_no_sleep,
    )
    bars = result["placeholder_map"]["gantt_bars"]
    by_label = {b["label"]: b for b in bars}
    # Two completed bars carry DIFFERENT categories (color tracks phase, not
    # progress): Cycle 1 (build_mirror) vs Rob intro (integration_golive).
    cycle1 = by_label["Cycle 1 · Hardening + auth/users"]
    bob = by_label["Rob intro · Planwright + Access pull"]
    assert cycle1["state"] == "complete" and bob["state"] == "complete"
    assert cycle1["category"] != bob["category"], (cycle1, bob)
    # Two bars of the SAME category differ in state but keep one category
    # (color must not follow state).
    cycle2 = by_label["Cycle 2 · QA + flexibility"]
    assert cycle2["category"] == cycle1["category"], (cycle1, cycle2)
    assert cycle2["state"] != cycle1["state"], (cycle1, cycle2)


def test_status_bar_categories_are_data_driven():
    provider = FixtureProvider.from_packet_file(STATUS_PACKET_PATH)
    result = run_adapter(
        "Northwind", STATUS_PROJECT, provider, deck_type="status",
        poll_interval=0.0, sleep=_no_sleep,
    )
    cats = {c["id"]: c["color"] for c in result["placeholder_map"]["bar_categories"]}
    assert cats["build_mirror"] == "blue", cats
    assert cats["integration_golive"] == "maroon", cats


# ---- S12: framing selected from stage, not guessed from content ----

def test_status_new_workstream_frames_today_after():
    provider = FixtureProvider.from_packet_file(STATUS_PACKET_PATH)
    result = run_adapter(
        "Northwind", STATUS_PROJECT, provider, deck_type="status",
        poll_interval=0.0, sleep=_no_sleep,
    )
    ws0 = result["placeholder_map"]["workstreams"][0]  # stage: new
    assert ws0["frame_a_label"] == "TODAY", ws0
    assert ws0["frame_b_label"] == "AFTER — TARGET IN 6 MONTHS", ws0


def test_status_existing_workstream_frames_where_we_are_target():
    provider = FixtureProvider.from_packet_file(STATUS_PACKET_PATH)
    result = run_adapter(
        "Northwind", STATUS_PROJECT, provider, deck_type="status",
        poll_interval=0.0, sleep=_no_sleep,
    )
    ws1 = result["placeholder_map"]["workstreams"][1]  # stage: existing
    assert ws1["frame_a_label"] == "WHERE WE ARE", ws1
    assert ws1["frame_b_label"] == "TARGET — ALL LINES LIVE BY WEEK 12", ws1


# ---- S13: progress state load-bearing; gap-flagged field carries marker ----

def test_status_progress_states_carried_verbatim():
    provider = FixtureProvider.from_packet_file(STATUS_PACKET_PATH)
    result = run_adapter(
        "Northwind", STATUS_PROJECT, provider, deck_type="status",
        poll_interval=0.0, sleep=_no_sleep,
    )
    ws0 = result["placeholder_map"]["workstreams"][0]
    states = {i["label"].split(" · ")[0]: i["state"] for i in ws0["progress_items"]}
    assert states["Quote templates"] == "done", states
    assert states["Quentin — needed?"] == "pending", states
    assert states["Mapping current quoting workflow"] == "in_process", states


def test_status_gap_flagged_metric_renders_unconfirmed_marker():
    # The fixture flags workstreams[0].after.metrics[0].value under §5 gaps; the
    # new workstream's frame_b_metrics (the >50% target) must render with the
    # visible unconfirmed marker (S13/criterion 10).
    result = _status_prompt()
    prompt = result["prompt"]
    ws_section = "## Slide 3 —" + prompt.split("## Slide 3 —")[1].split("## Slide 4 —")[0]
    assert "frame_b_metrics:" in ws_section, ws_section
    # the marker sits under the frame_b_metrics list on the new workstream slide.
    assert "(unconfirmed, see gaps)" in ws_section, ws_section


def test_status_slip_marker_gap_renders_unconfirmed():
    # The fixture also flags tracking.slip_or_buffer_markers[1]; the tracking
    # slide's slip_or_buffer_markers role must carry the unconfirmed marker.
    result = _status_prompt()
    prompt = result["prompt"]
    tracking = "## Slide 2 —" + prompt.split("## Slide 2 —")[1].split("## Slide 3 —")[0]
    assert "slip_or_buffer_markers:" in tracking
    assert "(unconfirmed, see gaps)" in tracking, tracking


# ---- coverage + render-fidelity guards cover the status path ----

def test_status_coverage_guard_passes_end_to_end():
    provider = FixtureProvider.from_packet_file(STATUS_PACKET_PATH)
    template = load_template(STATUS_TEMPLATE_PATH)
    result = run_adapter(
        "Northwind", STATUS_PROJECT, provider, deck_type="status",
        poll_interval=0.0, sleep=_no_sleep,
    )
    prompt = assemble_prompt(template, result["placeholder_map"])
    check_coverage(result["packet"], template, result["placeholder_map"], prompt,
                   deck_type="status")  # must not raise


def test_status_coverage_guard_catches_unaccounted_field():
    # An unaccounted populated field in a rendered section trips the guard,
    # naming the path — the same guarantee as the proposal path, keyed on schema
    # paths so it holds for any conforming status packet.
    provider = FixtureProvider.from_packet_file(STATUS_PACKET_PATH)
    template = load_template(STATUS_TEMPLATE_PATH)
    result = run_adapter(
        "Northwind", STATUS_PROJECT, provider, deck_type="status",
        poll_interval=0.0, sleep=_no_sleep,
    )
    packet = result["packet"].replace(
        '  plan_revision: "r3"',
        '  plan_revision: "r3"\n  unexpected_new_field: "boom"',
    )
    try:
        check_coverage(packet, template, result["placeholder_map"],
                       assemble_prompt(template, result["placeholder_map"]),
                       deck_type="status")
    except CoverageError as e:
        assert "tracking.unexpected_new_field" in str(e), str(e)
    else:
        raise AssertionError("expected CoverageError for an unaccounted field")


def test_status_render_fidelity_marker_hard_enforced():
    # A stub render that drops the unconfirmed marker the fixture requires blocks
    # the run, exactly as on the proposal path.
    result = _status_prompt()
    prompt = result["prompt"]
    good = "<!doctype html><body>x<p>(unconfirmed, see gaps)</p></body>"
    report = check_render_fidelity(prompt, good, deck_type="status")
    assert "missing_values" in report, report
    try:
        check_render_fidelity(prompt, "<!doctype html><body>no markers</body>",
                              deck_type="status")
    except RenderFidelityError:
        pass
    else:
        raise AssertionError("expected RenderFidelityError for a dropped marker")


def test_status_render_fidelity_extracts_today_marker_and_project_week():
    result = _status_prompt()
    report = check_render_fidelity(
        result["prompt"], "<!doctype html><body>x<p>(unconfirmed, see gaps)</p></body>",
        deck_type="status",
    )
    checked = report["checked"]
    assert "today_marker" in checked and checked["today_marker"] >= 1, checked
    assert "project_week" in checked and checked["project_week"] >= 1, checked


# ---- save path: both deliverables, status scaffold, render-fidelity report ----

def test_status_save_writes_both_files_and_surfaces_report():
    def stub(prompt, **kwargs):
        assert kwargs.get("deck_type") == "status", kwargs
        return "<!doctype html><body>STATUS DECK<p>(unconfirmed, see gaps)</p></body>"

    provider = FixtureProvider.from_packet_file(STATUS_PACKET_PATH)
    with tempfile.TemporaryDirectory() as pdir, tempfile.TemporaryDirectory() as ddir:
        result = generate_and_save_deck(
            "status", "Northwind", STATUS_PROJECT, provider,
            prompts_dir=pdir, decks_dir=ddir, renderer=stub,
            poll_interval=0.0, sleep=_no_sleep,
        )
        assert result["status"] == "ok", result
        assert result["number"] == 1, result
        assert os.path.isfile(os.path.join(pdir, "generated-prompt-1.txt"))
        assert os.path.isfile(os.path.join(ddir, "output-1.html"))
        assert "render_fidelity" in result, result
        # The saved Design prompt leads with the status house-style header.
        assert result["prompt"].startswith("===== QOFAI DECK HOUSE STYLE"), result["prompt"][:80]


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"PASS  {test.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL  {test.__name__}: {e}")

    if failures:
        print(f"\n{failures} test(s) failed")
        sys.exit(1)
    print(f"\nAll {len(tests)} tests passed")
