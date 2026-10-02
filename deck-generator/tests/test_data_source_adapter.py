"""Tests for Module 2 — Data Source Adapter, transport half.

Covers the transport half against the frozen example packet as fixture: request
shaping (contract §2), background dispatch with polling (PRD §2.C), the two
branch points (envelope status, then the gates), and the three discriminated
outcomes. The mapping half (clean-ok packet → flat placeholder map) is tested
separately once it is built.

Maps to PRD criterion 9 (one adapter, transport/mapping split behind one
interface), criterion 5 (any error envelope surfaced, no map), and criterion 4
(below-threshold confidence/completeness declines to render, returns for review).

Run with: python3 tests/test_data_source_adapter.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from data_source_adapter import (
    ALL_SECTIONS,
    DEFAULT_OPTIONS,
    INTENT,
    SCHEMA_VERSION,
    FixtureProvider,
    dispatch_and_gate,
    map_packet,
    map_status_packet,
    run_adapter,
    shape_request,
    _fmt_deck_date,
    _fmt_usd,
    _axis_columns,
    _metric,
    _parse_packet_yaml,
    _phase_rows,
    _workstream_weeks,
)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from template_loader import load_template

PACKET_PATH = os.path.join(
    os.path.dirname(__file__), "..", "proposal-data-packet-EXAMPLE.md"
)
TEMPLATE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "templates", "proposal-template.md"
)

FIXTURE_PROJECT = "Operational Intelligence Platform"

# The map's non-role keys: everything else must be a template role Module 1 declares.
SIDE_CHANNELS = {"_gaps", "_provenance", "_gap_roles", "_skipped", "_panel_fit",
                 "_bullet_order",
                 # Slide 2 repeats once per opportunity since 2026-09-13, so
                 # the map carries the list the assembler repeats over and the
                 # per-opportunity halves of the two slide-2 reports. None of
                 # the three is a role: `opportunities` is the repeat container,
                 # named by the template's own `Repeat:` directive, exactly as
                 # `workstreams` is on the status path.
                 "opportunities", "_panel_fit_per_opportunity",
                 "_bullet_order_per_opportunity"}

# A minimal ok-packet: the transport half only reads frontmatter for the gate,
# so a frontmatter block plus a stub body is enough to exercise gating without
# the full structured packet (which is the mapping half's concern).
def _packet_with(confidence, data_completeness):
    return (
        f'---\nconfidence: "{confidence}"\n'
        f"data_completeness: {data_completeness}\n---\n\n# stub body\n"
    )


def _no_sleep(_):
    pass


# Most codes' `details` are keyed on the resolved company. `E_SOURCE_UNREACHABLE`
# deliberately is not: a transport failure can happen before resolution, and the
# absence of `company_id` is exactly what distinguishes it from
# `E_KG_UNREACHABLE` in contract §5. A fixture handing it one would contradict
# the row it stands for, so the details are per-code rather than one shape for
# every envelope.
DEFAULT_DETAILS = {"company_id": "some-uuid"}
DETAILS_BY_CODE = {
    "E_SOURCE_UNREACHABLE": {"endpoint_kind": "agent_os_mcp",
                             "error_type": "McpProtocolError"},
}


def _error_envelope(code):
    return {
        "status": "error",
        "error": {
            "code": code,
            "message": f"{code}: human-readable explanation",
            "remediation": f"what to do about {code}",
            "details": DETAILS_BY_CODE.get(code, DEFAULT_DETAILS),
        },
    }


ALL_ERROR_CODES = [
    "E_COMPANY_NOT_FOUND",
    "E_AMBIGUOUS_COMPANY",
    "E_NO_KG",
    "E_KG_UNREACHABLE",
    "E_SOURCE_UNREACHABLE",
    "E_NO_CORPUS",
    "E_LOW_CONFIDENCE",
    "E_BAD_REQUEST",
    "E_TEMPLATE_UNKNOWN",
]

PROJECT_ERROR_CODES = [
    "E_PROJECT_REQUIRED",
    "E_AMBIGUOUS_PROJECT",
    "E_PROJECT_NOT_FOUND",
]


def _run(provider, company="Ridgeline Site Services", project="Production Dashboard",
         **kwargs):
    return dispatch_and_gate(
        company, project, provider,
        poll_interval=0.0, sleep=_no_sleep, **kwargs,
    )


# ---- request shaping (contract §2) ----

def test_shape_request_matches_contract():
    req = shape_request("Ridgeline Site Services", "Production Dashboard")
    assert req["intent"] == INTENT, req["intent"]
    assert req["schema_version"] == SCHEMA_VERSION, req["schema_version"]
    assert req["company"] == "Ridgeline Site Services", req["company"]
    assert req["project"] == "Production Dashboard", req["project"]
    assert tuple(req["sections_requested"]) == ALL_SECTIONS, req["sections_requested"]
    # Options default to the contract's server-side values.
    assert req["options"]["min_confidence"] == "medium", req["options"]
    assert req["options"]["min_data_completeness"] == 0.70, req["options"]
    assert req["options"]["include_prose"] is True, req["options"]
    # No optional fields unless supplied.
    assert "pe_firm" not in req and "proposal_date" not in req, req


def test_shape_request_passes_client_values_through_not_hardcoded():
    # Any client/project must flow through unchanged — nothing baked in.
    req = shape_request(
        "Acme Fabrication", "8f2a1e64-uuid", pe_firm="Some PE Firm",
        proposal_date="2026-07-09",
    )
    assert req["company"] == "Acme Fabrication", req
    assert req["project"] == "8f2a1e64-uuid", req
    assert req["pe_firm"] == "Some PE Firm", req
    assert req["proposal_date"] == "2026-07-09", req


def test_shape_request_passes_opportunity_id_through_when_given():
    # The reviewer's pick (E9e): present only when supplied, never invented.
    req = shape_request("Acme Fabrication", "8f2a1e64-uuid", opportunity_id="OPP-9")
    assert req["opportunity_id"] == "OPP-9", req
    assert "opportunity_id" not in shape_request("Acme Fabrication", "8f2a1e64-uuid")


def test_shape_request_options_override_merge():
    req = shape_request(
        "Co", "Proj",
        options={"min_confidence": "high", "include_prose": False},
    )
    assert req["options"]["min_confidence"] == "high", req["options"]
    assert req["options"]["include_prose"] is False, req["options"]
    # Un-overridden defaults survive the merge.
    assert req["options"]["min_data_completeness"] == 0.70, req["options"]
    # The module-level default dict is not mutated by a merge.
    assert DEFAULT_OPTIONS["min_confidence"] == "medium", DEFAULT_OPTIONS


def test_missing_project_is_rejected_before_dispatch():
    # Project is never optional (PRD §3). An empty project is a caller-side
    # violation, distinct from the provider's E_PROJECT_REQUIRED.
    try:
        shape_request("Co", "")
    except ValueError as e:
        assert "project" in str(e), str(e)
    else:
        raise AssertionError("expected ValueError on missing project")


# ---- clean ok-packet clears both gates (hand-off to mapping half) ----

def test_frozen_packet_clears_gates_and_hands_off():
    provider = FixtureProvider.from_packet_file(PACKET_PATH)
    result = _run(provider)
    assert result["status"] == "ok", result
    assert result["confidence"] == "high", result
    assert result["data_completeness"] == 0.92, result
    # The raw packet is carried forward for the mapping half; no map yet.
    assert result["packet"].startswith("---"), result["packet"][:20]
    assert "placeholder_map" not in result, result


# ---- gates decline to render, return for review (PRD criterion 4) ----

def test_low_confidence_returns_review_no_map():
    provider = FixtureProvider.from_packet_markdown(_packet_with("low", 0.92))
    result = _run(provider)
    assert result["status"] == "review", result
    assert result["confidence"] == "low", result
    assert result["data_completeness"] == 0.92, result
    assert "packet" not in result, result
    assert "placeholder_map" not in result, result


def test_low_completeness_returns_review_no_map():
    provider = FixtureProvider.from_packet_markdown(_packet_with("high", 0.55))
    result = _run(provider)
    assert result["status"] == "review", result
    assert result["data_completeness"] == 0.55, result
    assert "packet" not in result, result


def test_gate_threshold_is_configurable():
    # A medium-confidence packet clears the default medium gate but not a
    # caller-raised high gate.
    packet = _packet_with("medium", 0.92)
    passes = _run(FixtureProvider.from_packet_markdown(packet))
    assert passes["status"] == "ok", passes
    fails = _run(
        FixtureProvider.from_packet_markdown(packet),
        options={"min_confidence": "high"},
    )
    assert fails["status"] == "review", fails


# ---- error envelopes surfaced, never a map (PRD criterion 5) ----

def test_every_error_code_is_surfaced_and_produces_no_map():
    for code in ALL_ERROR_CODES:
        provider = FixtureProvider.from_envelope(_error_envelope(code))
        result = _run(provider)
        assert result["status"] == "error", (code, result)
        assert result["code"] == code, (code, result)
        assert result["message"], (code, result)
        assert result["remediation"], (code, result)
        assert "packet" not in result, (code, result)
        assert "placeholder_map" not in result, (code, result)


def test_the_unreachable_source_fixture_names_no_company(monkeypatch):
    """The fixture has to match the row it stands for. Contract §5 gives
    `E_SOURCE_UNREACHABLE` `{ endpoint_kind, error_type }` and deliberately no
    `company_id`, because a transport failure can happen before resolution, and
    that absence is what separates it from `E_KG_UNREACHABLE`. Without this the
    shared default would hand it the one field the row omits and nothing would
    go red."""
    details = _error_envelope("E_SOURCE_UNREACHABLE")["error"]["details"]
    assert details == {"endpoint_kind": "agent_os_mcp",
                       "error_type": "McpProtocolError"}
    assert _error_envelope("E_KG_UNREACHABLE")["error"]["details"] == {
        "company_id": "some-uuid"
    }


def test_project_level_errors_stop_with_no_company_fallback():
    # E_PROJECT_* must be surfaced and must not fall back to company-level data
    # or guess the project (PRD criterion 5, contract §5).
    for code in PROJECT_ERROR_CODES:
        provider = FixtureProvider.from_envelope(_error_envelope(code))
        result = _run(provider)
        assert result["status"] == "error", (code, result)
        assert result["code"] == code, (code, result)
        assert "packet" not in result, (code, result)


# ---- background dispatch (PRD §2.C) ----

def test_background_dispatch_polls_until_ready():
    # ready_after=3 means the provider reports done only on the third poll,
    # standing in for the live 30-90s assembly. Two sleeps happen between the
    # three polls, proving the transport half actually loops rather than reading
    # a result synchronously.
    sleeps = {"n": 0}

    def counting_sleep(_):
        sleeps["n"] += 1

    provider = FixtureProvider.from_packet_file(PACKET_PATH, ready_after=3)
    result = dispatch_and_gate(
        "Ridgeline Site Services", "Production Dashboard", provider,
        poll_interval=0.01, max_polls=10, sleep=counting_sleep,
    )
    assert result["status"] == "ok", result
    assert sleeps["n"] == 2, sleeps


def test_dispatch_times_out_if_provider_never_completes():
    provider = FixtureProvider.from_packet_file(PACKET_PATH, ready_after=99)
    try:
        dispatch_and_gate(
            "Co", "Proj", provider,
            poll_interval=0.0, max_polls=3, sleep=_no_sleep,
        )
    except TimeoutError as e:
        assert "3 polls" in str(e), str(e)
    else:
        raise AssertionError("expected TimeoutError when provider never completes")


# ---- fixture-tolerant packet parser (the not-strictly-valid-YAML quirks) ----

def test_parser_handles_space_separated_inline_map():
    # The milestones / workstreams shape: multiple pairs on one line, no commas.
    parsed = _parse_packet_yaml('rows:\n  - id: "M0"  week: 2   label: "SCOPE LOCKED"')
    assert parsed == {"rows": [{"id": "M0", "week": 2, "label": "SCOPE LOCKED"}]}, parsed


def test_parser_handles_brace_comma_inline_map_and_underscore_int():
    parsed = _parse_packet_yaml(
        'sched:\n  - { year: "YEAR 1", pct: 25 }\nbig: 38_400_000'
    )
    assert parsed["sched"] == [{"year": "YEAR 1", "pct": 25}], parsed
    assert parsed["big"] == 38_400_000, parsed


def test_parser_handles_flow_list_and_nested_block():
    parsed = _parse_packet_yaml(
        'timeline:\n  cols: ["1–2", "3–4"]\n  phase:\n    - id: "p1"\n      label: "L"'
    )
    assert parsed["timeline"]["cols"] == ["1–2", "3–4"], parsed
    assert parsed["timeline"]["phase"] == [{"id": "p1", "label": "L"}], parsed


def test_parser_strips_inline_comments_but_not_inside_quotes():
    parsed = _parse_packet_yaml('a: 5   # a comment\nb: "has # hash inside"')
    assert parsed == {"a": 5, "b": "has # hash inside"}, parsed


# ---- mapping half: the flat placeholder map (build plan role tables) ----

def _mapped():
    provider = FixtureProvider.from_packet_file(PACKET_PATH)
    result = run_adapter(
        "Ridgeline Site Services", FIXTURE_PROJECT, provider,
        poll_interval=0.0, sleep=_no_sleep,
    )
    assert result["status"] == "ok", result
    return result["placeholder_map"]


def test_map_fills_exactly_the_declared_roles_once():
    # The map must fill every role Module 1 declares, and add nothing beyond the
    # two side channels. This is the one-to-one seam with Modules 1 and 3.
    #
    # Two documented exceptions as of D1a (2026-08-10). qofai_investment /
    # client_upfront / comp_schedule are computed here (map_packet) but are no
    # longer template roles — the commercial section's investment/upfront/comp-
    # schedule figures became reviewer-named commercial_rows, and that packet
    # computation is left in place rather than touched, since this module is
    # off limits to this change (a live-provider window owns it concurrently).
    # commercial_rows itself is the mirror image: a template role this module
    # never fills, because it is studio input merged in afterward by
    # deck_generator, not packet data.
    ORPHAN_MAPPED_ROLES = {"qofai_investment", "client_upfront", "comp_schedule"}
    # `total_slides` joins it 2026-09-13: the footer's denominator is a fact
    # about the template and the map together, so `deck_generator` fills it
    # after both are in hand and this module never sees a slide count.
    NOT_PACKET_SOURCED_ROLES = {"commercial_rows", "total_slides"}

    template = load_template(TEMPLATE_PATH)
    roles = set()
    for slide in template["slides"]:
        roles |= {r["name"] for r in slide["roles"]}
    roles |= {r["name"] for r in template["recurring_fields"]}

    m = _mapped()
    mapped_roles = set(m) - SIDE_CHANNELS
    assert mapped_roles - ORPHAN_MAPPED_ROLES == roles - NOT_PACKET_SOURCED_ROLES, (
        roles - mapped_roles, mapped_roles - roles
    )


def test_copy_roles_carried_verbatim_from_the_right_labels():
    m = _mapped()
    # §1 cover copy comes from backtick bullets.
    assert m["deck_kicker"] == "PROJECT PLANNING · JULY 6 2026", m["deck_kicker"]
    assert m["project_title"] == "Operational Intelligence Project Planning.", m["project_title"]
    # §2 uses One-liner; §4/§6 use Subhead (the non-uniform labels).
    assert m["opportunity_headline"] == "Mobile Field Capture & Project Dashboards.", m
    assert m["opportunity_summary"].startswith("A connected tablet app"), m["opportunity_summary"]
    assert m["platform_summary"].startswith("A mobile capture layer"), m["platform_summary"]
    # §6 Subhead keeps the full sentence, not just the italic lead.
    assert "You retain the value." in m["terms_summary"], m["terms_summary"]
    assert "QofAI bears all up-front" in m["terms_summary"], m["terms_summary"]
    # §7's summary is the italic sub-line sharing the Headline line.
    assert m["next_steps_summary"] == "Each action has a named owner and a target week.", m


def test_record_roles_intact_with_declared_fields():
    m = _mapped()
    assert len(m["components"]) == 3, m["components"]
    assert set(m["components"][0]) == {"number", "kicker", "title", "description"}, m["components"][0]
    # timeline_rows: 5 Phase-1 + 4 Phase-2 workstreams, each carrying its own
    # per-workstream week span (start_week–end_week), not the phase span.
    assert len(m["timeline_rows"]) == 9, len(m["timeline_rows"])
    assert m["timeline_rows"][0]["weeks"] == "1–2", m["timeline_rows"][0]
    assert m["timeline_rows"][-1]["weeks"] == "13–16", m["timeline_rows"][-1]
    # Name and detail are separate roles: name is the row label (left of the
    # bars), detail rides inside the bar. Neither is the old concatenated string.
    assert m["timeline_rows"][0]["workstream"] == "Scoping & setup", m["timeline_rows"][0]
    assert m["timeline_rows"][0]["workstream_detail"] == "Lock scope", m["timeline_rows"][0]
    assert len(m["milestones"]) == 3, m["milestones"]
    assert m["milestones"][0] == {"id": "M0", "week": 2, "label": "SCOPE LOCKED"}, m["milestones"][0]
    assert len(m["value_mapping"]) == 3, m["value_mapping"]
    assert set(m["value_mapping"][0]) == {
        "scenario", "ebitda_gain", "qofai_comp",
        "client_retained_ebitda", "enterprise_value",
    }, m["value_mapping"][0]
    # The optimistic scenario's cap note is carried, not dropped.
    assert m["value_mapping"][2]["qofai_comp"] == "$685,000 (cap)", m["value_mapping"][2]
    assert len(m["action_items"]) == 4, m["action_items"]
    assert set(m["action_items"][0]) == {"number", "week", "owner", "title", "description"}, m


def test_timeline_rows_carry_distinct_per_workstream_weeks_within_phase_bounds():
    # Each workstream carries its own start–end span, not the phase span, and
    # each span falls inside its phase's bounds (phase 1 = wks 1–8, phase 2 =
    # 8–16). This is what constrains the rendered Gantt geometry.
    m = _mapped()
    rows = m["timeline_rows"]
    for row in rows:
        start, end = (int(x) for x in row["weeks"].split("–"))
        assert start <= end, row
        lo, hi = (1, 8) if "PHASE 1" in row["phase"] else (8, 16)
        assert lo <= start and end <= hi, row
    # Not every row shares one span — the phase-span bug would collapse them.
    spans = [r["weeks"] for r in rows]
    assert len(set(spans)) > 1, spans


def test_missing_workstream_weeks_maps_to_none_not_phase_span():
    # A workstream lacking start_week/end_week is load-bearing on a timeline
    # template: weeks comes back None (→ Module 3 flags MISSING), never the
    # silently-back-filled phase span.
    assert _workstream_weeks({"name": "x", "detail": "y"}) is None
    assert _workstream_weeks({"start_week": 3}) is None
    assert _workstream_weeks({"start_week": 3, "end_week": 7}) == "3–7"


# ---- an absent scenario leaf is a missing field, never the word "None"
# (2026-08-13 fix; E9-RESCOPE-DESIGN.md §3.3) ----

_FULL_SCENARIO = [
    'name: "BASE CASE"',
    "margin_gain_pp: 1.9",
    "direct_uplift_usd_yr: 1_500_000",
    "qofai_comp_usd: 700_000",
    "client_retained_ebitda_usd: 3_000_000",
    "enterprise_value_at_exit_usd: 12_000_000",
]


def _scenario_dropping(*keys):
    return [f for f in _FULL_SCENARIO if f.split(":")[0] not in keys]


def _mapped_commercial(packet_fields, role):
    # Only §6 is present; the other sections' label lookups already return ""
    # on a missing section, which is what keeps this minimal.
    lines = [f"    - {packet_fields[0]}"] + [f"      {f}" for f in packet_fields[1:]]
    packet_md = (
        '---\nconfidence: "high"\ndata_completeness: 1.0\n---\n\n'
        "## 6 · Commercial\n\n```yaml\ncommercial:\n  scenarios:\n"
        + "\n".join(lines) + "\n```\n"
    )
    return map_packet(packet_md, {"project": "x"})[role]


def _mapped_scenario(*fields):
    """The one RETURN row a one-case packet maps to (2026-09-23)."""
    rows = _mapped_commercial(fields, "return_rows")
    assert len(rows) == 1, rows
    return rows[0]


def test_each_absent_return_figure_leaves_only_its_own_cell_out():
    # Table-driven: dropping the source figure behind one RETURN cell leaves
    # that cell out of the record (an optional field emits no line and no
    # marker), every other cell keeps its real figure, and the row is never
    # dropped: the case name survives.
    for dropped, cell in (("margin_gain_pp", "margin"),
                          ("direct_uplift_usd_yr", "annual_ebitda")):
        row = _mapped_scenario(*_scenario_dropping(dropped))
        assert row["scenario"] == "BASE CASE", (dropped, row)
        assert cell not in row, (dropped, row)
        for name, value in row.items():
            assert value and value != "None", (dropped, name, row)


def test_the_value_chart_is_drawn_only_from_cases_carrying_all_three_figures():
    # Antonio, 2026-09-22: the chart is hidden without comp, retained EBITDA and
    # EV. A case missing any one of them is left out, because a stacked bar
    # cannot be drawn to scale from part of its stack.
    full = _mapped_commercial(_FULL_SCENARIO, "value_mapping")
    assert len(full) == 1 and full[0]["ebitda_gain"] == "+1.9pp · $1,500,000/yr", full
    for dropped in ("qofai_comp_usd", "client_retained_ebitda_usd",
                    "enterprise_value_at_exit_usd"):
        assert _mapped_commercial(_scenario_dropping(dropped), "value_mapping") == [], dropped


def test_present_figure_beside_an_absent_one_still_renders():
    # The exact shape E5b returns for a paper whose scenario table has no
    # percentage-point column: the dollar uplift is real, and it renders
    # rather than being dropped for want of the pp figure beside it.
    row = _mapped_scenario('name: "BASE CASE"', "direct_uplift_usd_yr: 1_500_000")
    assert row["scenario"] == "BASE CASE", row
    assert row["annual_ebitda"] == "$1,500,000/yr", row
    assert "margin" not in row, row


# ---- a stated range renders as a range (E11 Stage 2e) ----


def test_a_dollar_range_renders_with_the_sign_on_both_endpoints():
    # The house style the template's own example states (`$1.5M–$2.6M / yr
    # direct`) and the two reference packets carry. Each endpoint is formatted
    # exactly as a lone figure would be, so no arithmetic and no re-rounding
    # happens on either half.
    assert _fmt_usd(575000) == "$575,000"
    assert _fmt_usd("575000–862000") == "$575,000–$862,000"
    assert _fmt_usd("900000–1800000") == "$900,000–$1,800,000"
    # Not a range in the form `packet_fill._stated` writes: passes through as it
    # arrived rather than being half-formatted.
    assert _fmt_usd("about $2M") == "about $2M"
    assert _fmt_usd(None) == ""


def test_a_percentage_point_range_names_its_unit_once_after_both_endpoints():
    # Rule 4 of the stage, and it needs no adapter change: the cell already
    # names `pp` once after the figure, so a two-endpoint figure names it once
    # after the second endpoint, the way `_target_metrics` does.
    row = _mapped_scenario('name: "BASE CASE"', 'margin_gain_pp: "0.9–1.9"')
    assert row["margin"] == "+0.9–1.9pp", row


def test_a_range_on_both_source_figures_renders_both():
    row = _mapped_scenario('name: "BASE CASE"', 'margin_gain_pp: "0.9–1.9"',
                           'direct_uplift_usd_yr: "900000–1800000"')
    assert row["margin"] == "+0.9–1.9pp", row
    assert row["annual_ebitda"] == "$900,000–$1,800,000/yr", row


def test_a_single_figure_beside_a_range_keeps_its_single_form():
    # A case set may mix the two: one paper states a single pp figure on the
    # base case and a range on the others. Neither reading changes the other.
    row = _mapped_scenario('name: "BASE CASE"', "margin_gain_pp: 3.1",
                           'direct_uplift_usd_yr: "1800000–2900000"')
    assert row["margin"] == "+3.1pp", row
    assert row["annual_ebitda"] == "$1,800,000–$2,900,000/yr", row


def test_both_return_figures_absent_leaves_the_row_with_its_name_alone():
    row = _mapped_scenario('name: "BASE CASE"')
    assert row == {"scenario": "BASE CASE"}, row


def test_qofai_comp_note_alone_draws_no_chart():
    # A cap note with no amount behind it is not renderable data, so the case
    # carries no chart figures and the chart is not drawn.
    assert _mapped_commercial(['name: "BASE CASE"', 'qofai_comp_note: "cap"'],
                   "value_mapping") == []


def test_metric_folds_whichever_half_is_present():
    assert _metric({"value": "5%", "label": "Uplift"}) == "5% · Uplift"
    assert _metric({"value": "5%"}) == "5%"
    assert _metric({"label": "Uplift"}) == "Uplift"
    assert _metric({}) == ""


def test_list_roles_are_lists_of_strings():
    m = _mapped()
    assert m["today_pain_bullets"] and all(isinstance(x, str) for x in m["today_pain_bullets"]), m
    assert m["after_capability_bullets"], m["after_capability_bullets"]
    assert m["timeline_columns"][0] == "1–2", m["timeline_columns"]


def test_derived_roles_formatted_from_structured_fields_never_invented():
    m = _mapped()
    # after_horizon from build_summary.duration_weeks.
    assert m["after_horizon"] == "AFTER — TARGET IN ~16 WEEKS", m["after_horizon"]
    # footer_right derived from the project name (no packet field).
    assert m["footer_right"] == "OPERATIONAL INTELLIGENCE PLATFORM", m["footer_right"]
    # deck_type_label is the deck-type text, not the literal "PROPOSAL".
    assert m["deck_type_label"] == "PROJECT PLANNING", m["deck_type_label"]
    # metric roles fold value + label (the template declares no label role).
    # Since 2026-08-19 the TODAY slots carry the baseline financials rather than
    # whichever metrics the paper happened to state, so that both boxes on slide
    # 2 measure the same quantity: the AFTER box is an EBITDA impact, so TODAY is
    # the EBITDA it acts on. Both figures come off `baseline` verbatim, with no
    # arithmetic and no re-rounding.
    assert m["today_metric_1"] == "17.9% · Adjusted EBITDA margin", m["today_metric_1"]
    assert m["today_metric_2"] == "$6.9M · Adjusted EBITDA", m["today_metric_2"]
    # What they displaced is not lost: the paper's own metrics come back as
    # bullets, where the panel fitter weighs them against the other bullets.
    bullets = " · ".join(m["today_pain_bullets"])
    assert "−41.2 – +58.4%" in bullets, bullets
    assert "Month-over-month EBITDA" in bullets, bullets


# ---------------------------------------------------------------------------
# Slide 2, 2026-08-19. Three defects Antonio reported off a rendered deck: the
# bullet lists overflowed their panels and were painted across the build band;
# the TODAY and AFTER boxes stated different quantities so the comparison was not
# one; and a second AFTER metric that no deck can ever have was reported missing.
# ---------------------------------------------------------------------------

def _mapped_with_bullets(today, after):
    """The reference map with slide 2's two bullet lists replaced."""
    from data_source_adapter import _fit_slide_two_bullets
    m = _mapped()
    m["today_pain_bullets"] = list(today)
    m["after_capability_bullets"] = list(after)
    phases = [{"label": "Phase 1", "summary": "Build and pilot."},
              {"label": "Phase 2", "summary": "Fleet rollout."}]
    report = _fit_slide_two_bullets(m, phases)
    return m, report


LONG_BULLET = ("Lamna's consumer wireless retail postpaid churn of 1.12% in "
               "Q4 2024 is higher than T-Mobile's full-year postpaid churn")


def test_a_bullet_list_longer_than_its_panel_is_trimmed_to_what_fits():
    """The regression. Seven and nine bullets went into panels holding a few, and
    the surplus was painted over the build band rather than clipped or pushed off
    the slide, so no guard saw it."""
    m, report = _mapped_with_bullets([LONG_BULLET] * 7, [LONG_BULLET] * 9)
    assert len(m["today_pain_bullets"]) < 7
    assert len(m["after_capability_bullets"]) < 9
    assert m["today_pain_bullets"], "a panel must not be emptied"
    assert m["after_capability_bullets"]


def test_a_trim_is_reported_rather_than_silent():
    _m, report = _mapped_with_bullets([LONG_BULLET] * 7, [LONG_BULLET] * 9)
    entry = report["today_pain_bullets"]
    assert entry["of"] == 7
    assert entry["shown"] == len(_m["today_pain_bullets"])
    assert entry["dropped"], entry
    assert entry["note"]


def test_trimming_keeps_the_leading_bullets_byte_for_byte():
    """It chooses how many fit. It never rewrites one, which is what keeps an
    unsourced word out of a panel."""
    bullets = [f"{n}. {LONG_BULLET}" for n in range(7)]
    m, _report = _mapped_with_bullets(bullets, [])
    kept = m["today_pain_bullets"]
    assert kept == bullets[:len(kept)]


def test_a_list_that_fits_is_left_alone_and_reported_clean():
    m, report = _mapped_with_bullets(["short one", "short two"], ["short three"])
    assert m["today_pain_bullets"] == ["short one", "short two"]
    assert m["after_capability_bullets"] == ["short three"]
    # Reported, and reported as clean. The panel is still named, because a
    # reviewer asking "is this all of it?" needs "all 2 shown" said out loud;
    # an entry only on the drop path leaves silence standing for both answers.
    for role, count in (("today_pain_bullets", 2),
                        ("after_capability_bullets", 1)):
        entry = report[role]
        assert entry["dropped"] == []
        assert entry["note"] == ""
        assert (entry["shown"], entry["of"]) == (count, count)
        assert not entry["overflowed"]


def test_every_kept_bullet_says_where_it_sat_in_the_packets_list():
    """The trim is only reviewable if a reviewer can find the bullet in the
    source, and counting an unranked list by hand is exactly what they should not
    have to do."""
    m, report = _mapped_with_bullets(["short one", "short two"], [])
    rows = report["today_pain_bullets"]["kept"]
    assert [row["text"] for row in rows] == m["today_pain_bullets"]
    assert [row["source_position"] for row in rows] == [1, 2]


def test_a_blank_bullet_does_not_shift_the_positions_of_the_real_ones():
    """`fit_bullets` drops blanks before it measures, so a caller lining its own
    indices up against `kept` has to drop exactly the same items. A blank in the
    middle used to be the way to mislabel every bullet after it."""
    m, report = _mapped_with_bullets(["short one", "   ", "short two"], [])
    rows = report["today_pain_bullets"]["kept"]
    assert [row["text"] for row in rows] == ["short one", "short two"]
    assert [row["source_position"] for row in rows] == [1, 3]


def test_taller_metrics_leave_room_for_fewer_bullets():
    """The reason a fixed cap would be wrong: the room is what is left after the
    metrics block, and that block is one line tall for `12.4%` and three for a
    value the extraction pass returned as a whole phrase."""
    from data_source_adapter import _fit_slide_two_bullets
    phases = [{"label": "Phase 1", "summary": "Build and pilot."}]

    def kept_with(metric):
        m = _mapped()
        m["today_metric_1"] = metric
        m["today_metric_2"] = ""
        m["today_pain_bullets"] = [LONG_BULLET] * 6
        m["after_capability_bullets"] = []
        _fit_slide_two_bullets(m, phases)
        return len(m["today_pain_bullets"])

    short = kept_with("12.4% · Adjusted EBITDA margin")
    tall = kept_with("approximately 120 million wireless retail connections "
                     "and 14 million broadband connections · connections")
    assert short > tall, (short, tall)


def test_a_metric_slot_dollar_figure_is_abbreviated_to_its_magnitude():
    """`_fmt_usd` spells every digit, which is right for a table cell and wrong
    for a 24px display slot: a live deck rendered a baseline EBITDA as
    `$40,500,000,000` on 2026-08-19."""
    from data_source_adapter import _fmt_usd_display

    assert _fmt_usd_display(40_500_000_000) == "$40.5B"
    assert _fmt_usd_display(6_900_000) == "$6.9M"
    assert _fmt_usd_display(1_000_000) == "$1M"


def test_the_tilde_appears_only_when_something_was_actually_rounded():
    """A figure shown to one decimal of its magnitude has lost precision and the
    deck says so. One that has not should not apologise for it."""
    from data_source_adapter import _fmt_usd_display

    assert _fmt_usd_display(40_534_710_000) == "~$40.5B"
    assert _fmt_usd_display(40_500_000_000) == "$40.5B"


def test_a_figure_small_enough_to_show_exactly_is_shown_exactly():
    from data_source_adapter import _fmt_usd_display

    assert _fmt_usd_display(575_000) == "$575,000"
    assert _fmt_usd_display(999_999) == "$999,999"


def test_the_sign_sits_outside_the_dollar_mark():
    from data_source_adapter import _fmt_usd_display

    assert _fmt_usd_display(-2_400_000) == "-$2.4M"


def test_a_non_numeric_amount_passes_through_the_display_formatter():
    from data_source_adapter import _fmt_usd_display

    assert _fmt_usd_display(None) == ""
    assert _fmt_usd_display("not a number") == "not a number"


def test_the_table_cell_formatter_is_left_alone():
    """`_fmt_usd` is still exact everywhere it was: the value map, the scenario
    cells and the EV footnote all read full figures."""
    from data_source_adapter import _fmt_usd

    assert _fmt_usd(40_500_000_000) == "$40,500,000,000"
    assert _fmt_usd(6_900_000) == "$6,900,000"


def test_both_slide_two_boxes_state_the_same_quantity():
    """Antonio, 2026-08-19: "the 2 boxes should measure the same quantity". The
    AFTER box carries an EBITDA impact, so TODAY carries the EBITDA it acts on."""
    m = _mapped()
    assert "EBITDA" in m["today_metric_1"], m["today_metric_1"]
    assert "EBITDA" in m["today_metric_2"], m["today_metric_2"]
    assert "EBITDA" in m["after_metric_1"], m["after_metric_1"]


def test_the_after_figure_is_captioned_with_the_field_it_came_from():
    m = _mapped()
    value, _, label = m["after_metric_1"].partition(" · ")
    assert value, m["after_metric_1"]
    assert label, "a bare figure means nothing without the name of the metric"


def test_a_second_metric_renders_when_the_source_names_two():
    """The slot is not removed, it is made optional. A packet carrying two AFTER
    figures still renders both; the reference packet is such a packet."""
    m = _mapped()
    assert m["after_metric_1"], m["after_metric_1"]
    assert m["after_metric_2"], m["after_metric_2"]


def test_a_source_naming_one_metric_leaves_the_second_slot_empty():
    """And empty is all it is. A marker promises a source exists and did not
    arrive, which is untrue of a second figure the source never had. On the
    paper-derived path `packet_fill._target_metrics` writes exactly one record
    from `ebitda_impact`, so a required second metric reported every such deck as
    missing something it was never going to have (see the live-seam count)."""
    from data_source_adapter import _after_metric, _metric
    assert _metric({}) == ""
    assert _after_metric({}, "get_opportunity_details") == ""


def test_ev_footnote_composed_with_reporting_readiness_dollar_figure():
    # The footnote is composed from ev_assumptions, not copied from the prose
    # line. The reporting-readiness dollar contribution (reporting_readiness_ev_usd
    # = 1_725_000 → ~$1.7M) must appear explicitly, matching the reference deck —
    # the prose ev_footnote omits it, which was the silent gap.
    m = _mapped()
    footnote = m["terms_footnote"]
    assert "~$1.7M" in footnote, footnote          # 0.25 × 6.9M, was absent before
    assert "6× EBITDA multiple" in footnote, footnote
    assert "0.25× reporting-readiness" in footnote, footnote
    assert "~$6.9M adjusted EBITDA" in footnote, footnote
    assert footnote.endswith("Directional, for decision support."), footnote


def test_ev_footnote_falls_back_to_prose_when_assumptions_incomplete():
    # With ev_assumptions absent there is nothing to compose from, so the packet's
    # ev_footnote prose is carried verbatim rather than dropped.
    with open(PACKET_PATH, encoding="utf-8") as f:
        packet_md = f.read()
    stripped = []
    skipping = False
    for line in packet_md.splitlines():
        if line.strip().startswith("ev_assumptions:"):
            skipping = True
            continue
        if skipping:
            # ev_assumptions children are indented deeper than the key; a line at
            # the key's indent or shallower ends the block.
            if line.strip() and not line.startswith("    "):
                skipping = False
            else:
                continue
        stripped.append(line)
    provider = FixtureProvider.from_packet_markdown("\n".join(stripped))
    m = run_adapter(
        "Ridgeline Site Services", FIXTURE_PROJECT, provider,
        poll_interval=0.0, sleep=_no_sleep,
    )["placeholder_map"]
    assert m["terms_footnote"].startswith(
        "Enterprise value at exit assumes a 6× EBITDA multiple"
    ), m["terms_footnote"]


def test_client_short_sourced_from_packet_not_invented():
    m = _mapped()
    # client_short now ships in the packet's company block; it is read verbatim,
    # not fabricated, and is no longer a residual gap.
    assert m["client_short"] == "Ridgeline", m["client_short"]


def test_client_short_falls_back_to_client_full_when_absent():
    # A packet without company.client_short must degrade to the full client name,
    # never leave the cosmetic short-form empty (which would leak a MISSING
    # marker toward a client-facing render). MISSING is reserved for load-bearing
    # fields; a footer short-form is defaultable.
    with open(PACKET_PATH, encoding="utf-8") as f:
        packet_md = f.read()
    stripped = "\n".join(
        line for line in packet_md.splitlines()
        if not line.strip().startswith("client_short:")
    )
    provider = FixtureProvider.from_packet_markdown(stripped)
    m = run_adapter(
        "Ridgeline Site Services", FIXTURE_PROJECT, provider,
        poll_interval=0.0, sleep=_no_sleep,
    )["placeholder_map"]
    assert m["client_short"] == m["client_full"] == "Ridgeline Site Services", m["client_short"]


def test_recurring_fields_from_packet_and_request():
    m = _mapped()
    assert m["client_full"] == "Ridgeline Site Services", m["client_full"]
    assert m["pe_firm"] == "Woodgrove Partners", m["pe_firm"]
    # project comes from the request (the frozen packet predates the field).
    assert m["project_name"] == FIXTURE_PROJECT, m["project_name"]
    # deck_date is formatted to the template's deck-date form, not raw ISO.
    assert m["deck_date"] == "JULY 6 2026", m["deck_date"]


def test_side_channels_extracted_from_section_8():
    m = _mapped()
    assert m["_gaps"] == [
        "baseline.baseline_locked_date",
        "commercial.qofai_investment_usd",
    ], m["_gaps"]
    prov = m["_provenance"]
    assert set(prov) == {"from_kg", "derived", "templated_defaults"}, prov
    # The §8 templated_defaults are the shared-across-clients set (criterion 6).
    assert "commercial.comp_schedule" in prov["templated_defaults"], prov
    assert "platform_layers" in prov["templated_defaults"], prov
    # gaps must not leak into _provenance.
    assert "gaps" not in prov, prov


def test_deck_date_formatter():
    assert _fmt_deck_date("2026-07-06") == "JULY 6 2026", _fmt_deck_date("2026-07-06")
    assert _fmt_deck_date("2026-12-25") == "DECEMBER 25 2026", _fmt_deck_date("2026-12-25")
    # Empty stays empty; a non-ISO value passes through rather than being guessed.
    assert _fmt_deck_date("") == "", _fmt_deck_date("")
    assert _fmt_deck_date("JULY 6 2026") == "JULY 6 2026", _fmt_deck_date("JULY 6 2026")


def test_gap_roles_translate_gaps_into_role_space():
    # Blocker-2 fix: _gaps is packet paths; _gap_roles is the template roles
    # those gaps affect, so Module 3 can flag the right role (criterion 10).
    m = _mapped()
    # commercial.qofai_investment_usd feeds a TERMS row since 2026-09-23.
    assert m["_gap_roles"] == ["terms_rows"], m["_gap_roles"]
    # baseline.baseline_locked_date maps to no rendered role, so it drops out.
    assert "baseline.baseline_locked_date" in m["_gaps"], m["_gaps"]
    # Every _gap_roles entry is an actual role in the map.
    for role in m["_gap_roles"]:
        assert role in m and role not in SIDE_CHANNELS, role


def test_skipped_is_empty_when_all_sections_requested():
    # Blocker-1 fix: default request asks for all six sections → nothing skipped.
    m = _mapped()
    assert m["_skipped"] == [], m["_skipped"]


def test_skipped_reports_slide_numbers_for_unrequested_sections():
    provider = FixtureProvider.from_packet_file(PACKET_PATH)
    result = run_adapter(
        "Ridgeline", FIXTURE_PROJECT, provider,
        sections_requested=["cover", "opportunity", "platform"],
        poll_interval=0.0, sleep=_no_sleep,
    )
    # rollout=4, commercial_terms=5, next_steps=6 were not requested.
    assert result["placeholder_map"]["_skipped"] == [4, 5, 6], result["placeholder_map"]["_skipped"]


def test_no_foreign_client_content_leaks_in():
    # Nothing from the template's FBK illustration should appear in a map built
    # from the Ridgeline packet.
    m = _mapped()
    blob = repr(m)
    for banned in ("Fabrikam Marine", "FBK", "vessel", "satellite link", "the inventory app"):
        assert banned not in blob, f"leaked foreign content: {banned}"


# ---- one interface: run_adapter over both halves ----

def test_run_adapter_gate_failure_returns_no_map():
    provider = FixtureProvider.from_packet_markdown(_packet_with("low", 0.92))
    result = run_adapter("Co", "Proj", provider, poll_interval=0.0, sleep=_no_sleep)
    assert result["status"] == "review", result
    assert "placeholder_map" not in result, result


def test_run_adapter_error_returns_no_map():
    provider = FixtureProvider.from_envelope(_error_envelope("E_NO_KG"))
    result = run_adapter("Co", "Proj", provider, poll_interval=0.0, sleep=_no_sleep)
    assert result["status"] == "error", result
    assert result["code"] == "E_NO_KG", result
    assert "placeholder_map" not in result, result


def test_map_is_deterministic():
    # Same packet + same request → identical map (deterministic script, PRD §4).
    assert _mapped() == _mapped()


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


# ---------------------------------------------------------------------------
# E11 Stage 1 — the phase is its own timeline row, and the axis carries the
# plan's own unit.
#
# `map_packet` used to build timeline_rows as "for phase, for workstream in
# phase", and no paper in the corpus decomposes a phase into workstreams, so
# the inner loop yielded zero rows however well the phases parsed. The frozen
# fixture packet does carry workstreams, and its nine rows are unchanged (see
# test_record_roles_intact_with_declared_fields above); these cover the shape
# the papers actually supply.
# ---------------------------------------------------------------------------

def _mapped_timeline(*lines):
    # Only §5 is present; every other section's lookup already returns "" or []
    # on a missing section, which is what keeps this minimal.
    packet_md = (
        '---\nconfidence: "high"\ndata_completeness: 1.0\n---\n\n'
        "## 5 · Phased Rollout\n\n```yaml\ntimeline:\n"
        + "\n".join(lines) + "\n```\n"
    )
    return map_packet(packet_md, {"project": "x"})


_MONTH_PHASES = (
    "  columns:",
    '    - label: "0–3"',
    '      unit: "months"',
    '    - label: "3–6"',
    '      unit: "months"',
    "  phases:",
    '    - label: "Phase 1: Foundation"',
    "      start: 0",
    "      end: 3",
    '      unit: "months"',
    '    - label: "Phase 2: Rollout"',
    "      start: 3",
    "      end: 6",
    '      unit: "months"',
    "  milestones:",
    "    - position: 3",
    '      unit: "months"',
    "    - position: 6",
    '      unit: "months"',
)


def test_a_phase_with_no_workstreams_becomes_its_own_timeline_row():
    rows = _mapped_timeline(*_MONTH_PHASES)["timeline_rows"]
    assert rows == [
        {"phase": "Phase 1: Foundation", "weeks": "0–3"},
        {"phase": "Phase 2: Rollout", "weeks": "3–6"},
    ]


def test_the_workstream_keys_are_absent_from_a_phase_row_not_nulled():
    """The workstream leaves stay optional and absent, exactly as they are
    today: the paper never carried them, so the record carries no key for them
    and the assembler emits no line. The absence is still recorded, in the
    packet's own §8 gaps — it does not need a marker inside every bar as
    well."""
    rows = _mapped_timeline(*_MONTH_PHASES)["timeline_rows"]
    for row in rows:
        assert "workstream" not in row and "workstream_detail" not in row


def test_a_phase_row_with_no_span_keeps_its_weeks_marker():
    """`weeks` is load-bearing on a timeline template (it sets the bar's
    geometry), so a phase carrying only a label renders the missing marker
    rather than a bar placed on nothing."""
    rows = _mapped_timeline(
        "  phases:", '    - label: "Phase 1: Foundation"',
    )["timeline_rows"]
    assert rows == [{"phase": "Phase 1: Foundation", "weeks": None}]


def test_the_axis_names_the_unit_once_and_carries_the_charts_own_numbers():
    mapped = _mapped_timeline(*_MONTH_PHASES)
    assert mapped["timeline_columns"] == ["MONTHS 0–3", "3–6"]


def test_a_week_denominated_axis_reads_weeks_with_nothing_converted():
    mapped = _mapped_timeline(
        "  columns:", '    - label: "0–6"', '      unit: "weeks"',
        '    - label: "6–12"', '      unit: "weeks"',
        "  milestones:", "    - position: 6", '      unit: "weeks"',
    )
    assert mapped["timeline_columns"] == ["WEEKS 0–6", "6–12"]
    assert mapped["milestones"] == [
        {"id": "M1", "week": "WEEK 6", "label": "MILESTONE 1"},
    ]


def test_the_packets_own_week_buckets_still_win_where_it_carries_them():
    """The frozen packets state their axis directly. A packet that carries both
    keeps the one it stated; nothing derived overrides a stated value."""
    mapped = _mapped_timeline(
        '  week_buckets: ["1–2", "3–4"]',
        "  columns:", '    - label: "0–3"', '      unit: "months"',
    )
    assert mapped["timeline_columns"] == ["1–2", "3–4"]


# ---------------------------------------------------------------------------
# The axis is a ruler, not a phase list (2026-09-03).
#
# `_columns` composes the axis from the chart's own phase spans, which is right:
# they are the only sourced boundaries the paper states. But a plan whose phases
# run CONCURRENTLY states overlapping spans, and handing both to the renderer as
# axis entries gave two header cells a column they shared. A grid pushes the
# second occupant of a column onto an implicit second row, so the header wrapped
# under itself and painted over the cells before it — measured on
# `decks/WTG/claude code/output-2.html`, slide 4.
# ---------------------------------------------------------------------------

def test_the_axis_clamps_a_concurrent_phase_so_no_two_columns_overlap():
    """The real WTG shape of 2026-08-26: Phase 2 over months 2–4 and Phase 3
    over months 3–6, which share month 3. The ruler's boundaries stay the
    chart's own numbers; only the later entry's START moves, up to where the
    entry before it ended."""
    mapped = _mapped_timeline(
        "  columns:",
        '    - label: "0–2"', '      unit: "months"',
        '    - label: "2–4"', '      unit: "months"',
        '    - label: "3–6"', '      unit: "months"',
        '    - label: "6–12"', '      unit: "months"',
    )
    assert mapped["timeline_columns"] == ["MONTHS 0–2", "2–4", "4–6", "6–12"]
    # Monotonic, stated as the property rather than as one expected list: each
    # entry starts exactly where the previous one ended.
    bounds = [
        [float(n) for n in entry.replace("MONTHS ", "").split("–")]
        for entry in mapped["timeline_columns"]
    ]
    for earlier, later in zip(bounds, bounds[1:]):
        assert later[0] == earlier[1], (earlier, later)


def test_a_phase_span_inside_the_one_before_it_adds_no_axis_column():
    """A phase wholly concurrent with the one before it reaches no further on the
    ruler, so it contributes no column rather than a zero-width or a backwards
    one. Its BAR still draws its true span."""
    mapped = _mapped_timeline(
        "  columns:",
        '    - label: "1–8"', '      unit: "weeks"',
        '    - label: "2–5"', '      unit: "weeks"',
        '    - label: "8–16"', '      unit: "weeks"',
    )
    assert mapped["timeline_columns"] == ["WEEKS 1–8", "8–16"]


def test_a_non_numeric_axis_label_is_carried_as_stated():
    """Clamping only applies to a bare numeric range. Anything else passes
    through untouched rather than being reshaped into a range it never was."""
    mapped = _mapped_timeline(
        "  columns:",
        '    - label: "Q1"', '      unit: "quarters"',
        '    - label: "Q2"', '      unit: "quarters"',
    )
    assert mapped["timeline_columns"] == ["QUARTERS Q1", "Q2"]


def test_an_already_monotonic_axis_is_left_exactly_as_stated():
    """The clamp is a no-op on every non-concurrent plan, so it cannot change a
    deck it was not written for."""
    stated = _axis_columns({"columns": [
        {"label": "0–3", "unit": "months"}, {"label": "3–6"},
        {"label": "6–9"}, {"label": "9–12"},
    ]})
    assert stated == ["MONTHS 0–3", "3–6", "6–9", "9–12"]
    assert _axis_columns({"week_buckets": ["1–2", "3–4"], "columns": [
        {"label": "0–2", "unit": "months"}, {"label": "1–5"},
    ]}) == ["1–2", "3–4"]


# ---------------------------------------------------------------------------
# Slide 4's description role (2026-09-03). Casey, 2026-08-20: "the timeline
# description text was too verbose".
# ---------------------------------------------------------------------------

def test_the_plan_summary_reads_section_fives_own_subhead():
    """`plan_summary` used to be composed here by joining
    `build_summary.phases[].summary` on a space, which dropped every phase label
    and every separator: the 2026-09-02 WTG deck carried two phases' caveat
    notes spliced into one ungrammatical line, restating what slide 2's build
    strip and the Gantt underneath had both already shown. It now reads section
    5's own `Subhead:` line, the same way §4's `platform_summary` and §6's
    `terms_summary` do."""
    packet_md = (
        '---\nconfidence: "high"\ndata_completeness: 1.0\n---\n\n'
        # Section 2 is a list of per-opportunity entries since 2026-09-13.
        "## 2 · The Opportunity\n\n```yaml\nopportunities:\n"
        "  - build_summary:\n"
        "      duration: 12\n      duration_unit: \"months\"\n      phases:\n"
        '        - label: "Phase 1: Harden the count"\n'
        '          summary: "process hardening must precede AI deployment"\n'
        '        - label: "Phase 3: Extend to the second entity"\n'
        '          summary: "entity-level balances are not in the materials"\n'
        "```\n\n"
        "## 5 · Phased Rollout\n\n"
        "Headline: **The build, phase by phase.**\n"
        "Subhead: *Four sequential phases over twelve months, with milestones "
        "at months 3, 6, 9 and 12.*\n"
    )
    mapped = map_packet(packet_md, {"project": "x"})
    assert mapped["plan_summary"] == (
        "Four sequential phases over twelve months, with milestones at months "
        "3, 6, 9 and 12."
    )
    # The phase notes still render, ONCE, on slide 2's build strip.
    assert "process hardening must precede AI deployment" in mapped["build_summary"]
    assert "entity-level balances are not in the materials" in mapped["build_summary"]
    # And nowhere near the timeline description, in either order.
    for note in ("process hardening must precede AI deployment",
                 "entity-level balances are not in the materials"):
        assert note not in mapped["plan_summary"], note


def test_a_packet_with_no_section_five_subhead_leaves_plan_summary_empty():
    """Empty, so Module 3 renders its missing marker — never a splice of the
    phase notes standing in for a line nobody wrote."""
    mapped = _mapped_timeline(*_MONTH_PHASES)
    assert mapped["plan_summary"] == ""


def test_the_milestone_ordinal_is_framing_and_the_position_is_the_papers():
    milestones = _mapped_timeline(*_MONTH_PHASES)["milestones"]
    assert milestones == [
        {"id": "M1", "week": "MONTH 3", "label": "MILESTONE 1"},
        {"id": "M2", "week": "MONTH 6", "label": "MILESTONE 2"},
    ]


def test_a_packet_that_states_its_own_milestone_is_carried_verbatim():
    milestones = _mapped_timeline(
        "  milestones:",
        '    - id: "M0"  week: 2  label: "SCOPE LOCKED"',
    )["milestones"]
    assert milestones == [{"id": "M0", "week": 2, "label": "SCOPE LOCKED"}]


def test_an_axis_entry_with_no_label_is_skipped_rather_than_rendered_empty():
    assert _axis_columns({"columns": [{"unit": "months"}, {"label": "3–6",
                                                           "unit": "months"}]}) \
        == ["MONTHS 3–6"]


def test_a_phase_row_never_invents_a_span_from_a_half_stated_one():
    assert _phase_rows({"label": "P", "start": 2}) == [{"phase": "P", "weeks": None}]


# ---------------------------------------------------------------------------
# ITEM 15: the map carries one slide 2 per opportunity.
# ---------------------------------------------------------------------------

def _two_opportunity_packet():
    """A packet whose section 2 states two opportunities, each distinguishable."""
    return (
        '---\nconfidence: "high"\ndata_completeness: 1.0\n---\n\n'
        "## 2 · The Opportunity\n\n```yaml\nopportunities:\n"
        '  - headline: "FIRST OPPORTUNITY"\n'
        '    one_liner: "The first story."\n'
        "    today_pain_points:\n"
        '      - "The first pain"\n'
        "    target_capabilities:\n"
        '      - "The first capability"\n'
        '  - headline: "SECOND OPPORTUNITY"\n'
        '    one_liner: "The second story."\n'
        "    today_pain_points:\n"
        '      - "The second pain"\n'
        "    target_capabilities:\n"
        '      - "The second capability"\n'
        "```\n"
    )


def test_the_map_carries_one_slide_two_per_opportunity():
    mapped = map_packet(_two_opportunity_packet(), {"project": "x"})
    items = mapped["opportunities"]

    assert len(items) == 2
    assert [item["opportunity_headline"] for item in items] == [
        "FIRST OPPORTUNITY", "SECOND OPPORTUNITY",
    ]
    assert items[0]["today_pain_bullets"] == ["The first pain"]
    assert items[1]["today_pain_bullets"] == ["The second pain"]


def test_no_opportunity_takes_another_ones_material():
    """The property the whole item exists for, at the layer that builds the
    slides."""
    items = map_packet(_two_opportunity_packet(), {"project": "x"})["opportunities"]
    first, second = items

    assert "second" not in repr(first).lower()
    assert "first" not in repr(second).lower()


def test_the_flat_map_carries_the_first_opportunity_and_they_agree():
    """Both are built by one function, so they cannot say different things about
    one opportunity, and the flat map is what `check_coverage` and every
    non-repeating consumer read."""
    mapped = map_packet(_two_opportunity_packet(), {"project": "x"})
    first = mapped["opportunities"][0]

    for role in ("opportunity_headline", "opportunity_summary",
                 "today_pain_bullets", "after_capability_bullets"):
        assert mapped[role] == first[role], role


def test_the_map_holds_no_cycle_so_a_consumer_can_copy_it():
    """The list holds the slide maps and the flat map holds the list, so aliasing
    the first item to the map itself would be a cycle every consumer that copies
    or renders one would meet."""
    import copy as copy_module

    mapped = map_packet(_two_opportunity_packet(), {"project": "x"})
    assert copy_module.deepcopy(mapped)["opportunities"][0][
        "opportunity_headline"] == "FIRST OPPORTUNITY"


def test_one_opportunity_maps_to_a_list_of_one():
    """No branch on the count anywhere, including here."""
    mapped = map_packet(
        '---\nconfidence: "high"\ndata_completeness: 1.0\n---\n\n'
        "## 2 · The Opportunity\n\n```yaml\nopportunities:\n"
        '  - headline: "ONLY OPPORTUNITY"\n'
        "```\n",
        {"project": "x"},
    )
    assert len(mapped["opportunities"]) == 1
    assert mapped["opportunity_headline"] == "ONLY OPPORTUNITY"


def test_each_opportunity_gets_its_own_trim_report():
    """Each slide 2 has its own two panels and its own room, so the reviewer's
    account of what a panel could not hold is per opportunity."""
    mapped = map_packet(_two_opportunity_packet(), {"project": "x"})

    assert len(mapped["_panel_fit_per_opportunity"]) == 2
    assert len(mapped["_bullet_order_per_opportunity"]) == 2
    assert mapped["_panel_fit"] == mapped["_panel_fit_per_opportunity"][0]


# ---------------------------------------------------------------------------
# WHICH OPPORTUNITY A SLIDE IS (Antonio, 2026-09-15, from the first rendered
# two-opportunity deck).
#
# Both opportunity slides read "THE OPPORTUNITY" and both showed the identical
# TODAY strip, because the company baseline is one fact about one company
# resolved once for the engagement and correctly repeated. Two slides with the
# same label and the same large figures are told apart only by reading the
# headline, and the deck being right does not stop a reader attributing one
# slide's numbers to the other.
# ---------------------------------------------------------------------------

def test_a_one_opportunity_deck_keeps_the_label_it_has_always_had():
    """Nothing to disambiguate, so nothing is added. Same reason the opportunity
    column is absent from a one-opportunity value-mapping table."""
    mapped = map_packet(
        '---\nconfidence: "high"\ndata_completeness: 1.0\n---\n\n'
        "## 2 · The Opportunity\n\n```yaml\nopportunities:\n"
        '  - headline: "Mobile Field Data Capture"\n'
        "```\n",
        {"project": "x"},
    )
    assert mapped["section_label"] == "THE OPPORTUNITY"
    assert mapped["opportunities"][0]["section_label"] == "THE OPPORTUNITY"


def test_each_slide_says_which_opportunity_it_is():
    mapped = map_packet(_two_opportunity_packet(), {"project": "x"})
    labels = [item["section_label"] for item in mapped["opportunities"]]

    assert labels == ["OPPORTUNITY 1 · FIRST OPPORTUNITY",
                      "OPPORTUNITY 2 · SECOND OPPORTUNITY"]


def test_the_label_carries_the_position_and_the_name_and_not_one_of_them():
    """Each half answers a question the other does not. An ordinal alone says
    there are two and not which is which; the name alone says which and not
    where in the deck, which is what someone flicking back for one of them is
    actually using."""
    for label in (item["section_label"] for item
                  in map_packet(_two_opportunity_packet(),
                                {"project": "x"})["opportunities"]):
        assert "OPPORTUNITY " in label
        assert " · " in label
        assert label.split(" · ")[1].strip()


def test_the_label_is_composed_from_the_opportunitys_own_title():
    """Not new packet material: the name is already in the packet as this
    section's headline."""
    mapped = map_packet(_two_opportunity_packet(), {"project": "x"})
    for item in mapped["opportunities"]:
        assert item["opportunity_headline"].upper() in item["section_label"]


def test_an_opportunity_with_no_title_still_says_which_one_it_is():
    """The ordinal is the half that always exists, so a nameless opportunity
    degrades to it rather than to a label that says nothing."""
    from data_source_adapter import _section_label

    assert _section_label("", 2, 2) == "OPPORTUNITY 2"
    assert _section_label(None, 1, 3) == "OPPORTUNITY 1"


def test_the_label_fits_the_row_it_renders_in():
    """Measured against the stylesheet's own geometry rather than eyeballed. The
    deck already renders a 44-character two-part label on slide 5, and the
    longest real label here is three characters past it in a row with roughly
    four times the room."""
    import panel_fit

    width = panel_fit.SLIDE_W - 2 * panel_fit.SLIDE_PAD_X
    longest = "OPPORTUNITY 2 · REAL-TIME OPERATIONAL DASHBOARD"
    assert panel_fit.estimate_lines(
        longest, font_px=panel_fit.KICKER_FONT, width_px=width) == 1


# ---------------------------------------------------------------------------
# THE DECK'S SHORT BRAND FORM (2026-09-15).
#
# `company.client_short` is UNSOURCEABLE in the packet's own slot table and is
# filled on no path, so the fallback to the full registry name fires on EVERY
# live run, always, by construction. The fixture packets carry "FBK" and
# "Ridgeline" because a human wrote them into a frozen fixture, which is why a
# fixture run looked right and the first live deck put the company's full name
# in all seven footers.
# ---------------------------------------------------------------------------

def test_the_reviewers_value_wins():
    from data_source_adapter import client_short

    assert client_short({"client_short": "FBK"},
                        {"name": "A Long Company Name"}) == "FBK"


def test_a_blank_value_degrades_to_the_full_name_rather_than_marking():
    """A footer short-form is cosmetic and defaultable, and markers are for
    load-bearing, un-defaultable fields."""
    from data_source_adapter import client_short

    for blank in (None, "", "   "):
        assert client_short({"client_short": blank},
                            {"name": "A Long Company Name"}) == "A Long Company Name"
    assert client_short({}, {"name": "A Long Company Name"}) == "A Long Company Name"


def test_the_platforms_value_is_still_preferred_to_the_full_name():
    """The middle term, kept for a source that does not exist yet rather than
    collapsed away: the day a tool returns one, it beats the full name without
    beating the reviewer."""
    from data_source_adapter import client_short

    assert client_short({}, {"client_short": "FROM-KG",
                             "name": "A Long Company Name"}) == "FROM-KG"
    assert client_short({"client_short": "TYPED"},
                        {"client_short": "FROM-KG",
                         "name": "A Long Company Name"}) == "TYPED"


def test_both_mappers_decide_it_with_the_same_function():
    """THE STRUCTURAL CHECK, and the reason it is structural. The quiet failure
    here is the reviewer's value winning in the proposal map and the derived
    value winning in the status map, which no single sampled run would show. A
    precedence stated twice is a precedence with two chances to be edited apart,
    which is how the baseline label and the baseline figures came apart for a
    day on 2026-09-07. Asserted off the parsed source rather than off behaviour,
    because a behavioural test passes just as happily against a restatement."""
    import ast
    import pathlib as _pathlib

    tree = ast.parse((_pathlib.Path(__file__).resolve().parent.parent
                      / "src" / "data_source_adapter.py").read_text())
    for name in ("map_packet", "map_status_packet"):
        fn = next(node for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef) and node.name == name)
        called = {ast.unparse(node.func) for node in ast.walk(fn)
                  if isinstance(node, ast.Call)}
        assert "client_short" in called, name


def test_the_status_map_reads_it_the_same_way_the_proposal_map_does():
    """The structural check above says they call one function; this says the
    answer actually arrives on both maps."""
    proposal = map_packet(
        '---\nconfidence: "high"\ndata_completeness: 1.0\n---\n\n'
        '## 1 · Company\n\n```yaml\ncompany:\n  name: "A Long Company Name"\n```\n',
        {"project": "x", "client_short": "SHORT"},
    )
    assert proposal["client_short"] == "SHORT"


# --- the unconfirmed marker ------------------------------------------------

_GAPPED = (
    '---\nconfidence: "high"\ndata_completeness: 1.0\n---\n\n'
    '## 1 · Company Profile\n\n```yaml\ncompany:\n  name: "A Long Company Name"\n```\n\n'
    '## 8 · Provenance\n\n```yaml\nprovenance:\n  gaps:\n'
    '    - field: "company.client_short"\n'
    '      reason: "No source exists."\n```\n'
)


def test_a_derived_short_form_is_still_flagged_unconfirmed():
    """The direction that must keep working. The packet path is unsourceable and
    therefore gap-flagged, and a value derived from a source that did not supply
    it is exactly what the marker is for."""
    mapped = map_packet(_GAPPED, {"project": "x"})
    assert mapped["client_short"] == "A Long Company Name"
    assert "client_short" in mapped["_gap_roles"]


def test_a_reviewer_supplied_short_form_is_not_flagged_unconfirmed():
    """And the direction that was wrong. A reviewer is not waiting on
    confirmation of their own typing: they are the confirmation. Observed on the
    first live deck as `client_short: <the full name> (unconfirmed, see gaps)`,
    on a run where nobody had typed anything, and it would have said the same
    over a value somebody had."""
    mapped = map_packet(_GAPPED, {"project": "x", "client_short": "FBK"})
    assert mapped["client_short"] == "FBK"
    assert "client_short" not in mapped["_gap_roles"]


def test_the_gap_itself_is_unchanged_because_the_packet_is_unchanged():
    """A reviewer's own string is not something the data source said, so the
    packet still reports the field absent and section 8 still carries the gap.
    What changed is what the DECK renders and what the reviewer is told is
    outstanding."""
    mapped = map_packet(_GAPPED, {"project": "x", "client_short": "FBK"})
    assert "company.client_short" in mapped["_gaps"]


def test_suppressing_one_role_suppresses_no_other():
    """The exemption is one role wide. A gap on any other path still flags its
    own role on the same run."""
    packet = _GAPPED.replace(
        '    - field: "company.client_short"\n      reason: "No source exists."\n',
        '    - field: "company.client_short"\n      reason: "No source exists."\n'
        '    - field: "company.pe_firm"\n      reason: "No source exists."\n')
    mapped = map_packet(packet, {"project": "x", "client_short": "FBK"})
    assert "client_short" not in mapped["_gap_roles"]
    assert "pe_firm" in mapped["_gap_roles"]


# --- the marker suppression, on BOTH paths ---------------------------------
#
# FOUND BY MUTATION, not by reading. The first version of this fix stated the
# suppression twice: generalised on the proposal path, and as an inline
# `role == "client_short"` filter on the status path. Replacing the status guard
# with `if True` restored the pre-fix bug on every status deck and not one test
# failed, because no test called `map_status_packet` and asserted `_gap_roles`
# at all. That is the hazard `client_short`'s own docstring names, reintroduced
# one layer down, in the same change that closed it for the value.

def _status_packet_with_gap():
    """A status packet whose §5 flags the short form, which every real one does."""
    return (
        '---\nconfidence: "high"\ndata_completeness: 1.0\n'
        'packet_type: "project_status_check_in"\n---\n\n'
        '## 1 · Deck & Engagement\n\n```yaml\ncompany:\n'
        '  name: "A Long Company Name"\n```\n\n'
        '## 5 · Provenance\n\n```yaml\nprovenance:\n  gaps:\n'
        '    - field: "company.client_short"\n'
        '      reason: "No source exists."\n'
        '    - field: "company.pe_firm"\n'
        '      reason: "No source exists."\n```\n'
    )


def test_a_derived_short_form_is_flagged_unconfirmed_on_a_status_deck_too():
    mapped = map_status_packet(_status_packet_with_gap(), {"project": "x"})
    assert mapped["client_short"] == "A Long Company Name"
    assert "client_short" in mapped["_gap_roles"]


def test_a_supplied_short_form_is_not_flagged_on_a_status_deck():
    """The direction the mutant restored and nothing caught."""
    mapped = map_status_packet(_status_packet_with_gap(),
                               {"project": "x", "client_short": "SHORT"})
    assert mapped["client_short"] == "SHORT"
    assert "client_short" not in mapped["_gap_roles"]


def test_the_status_suppression_is_one_role_wide_as_well():
    mapped = map_status_packet(_status_packet_with_gap(),
                               {"project": "x", "client_short": "SHORT"})
    assert "pe_firm" in mapped["_gap_roles"]


def test_both_mappers_suppress_through_the_same_set():
    """THE TEST THAT WOULD HAVE CAUGHT IT, and the analogue of the one holding
    the value precedence. Each path needs its own gap TRANSLATOR, because a
    status gap carries a workstream index and a proposal gap does not. Neither
    may have its own SUPPRESSION: that is one rule, and both callers pass the
    same set in. Asserted off the parsed source, because behaviour on both paths
    was correct while the rule was stated twice."""
    import ast
    import pathlib as _pathlib

    tree = ast.parse((_pathlib.Path(__file__).resolve().parent.parent
                      / "src" / "data_source_adapter.py").read_text())
    for name in ("map_packet", "map_status_packet"):
        fn = next(node for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef) and node.name == name)
        called = {ast.unparse(node.func) for node in ast.walk(fn)
                  if isinstance(node, ast.Call)}
        assert "supplied_roles" in called, name


def test_a_second_studio_field_would_reach_both_paths():
    """The second-order version of the same drift. A generalised site absorbs a
    new field as one more table entry; a hardcoded one keeps working on one deck
    type and silently not on the other. The table is what makes that impossible,
    so this exercises it rather than trusting it."""
    import data_source_adapter as adapter

    original = dict(adapter.REVIEWER_SUPPLIED_ROLES)
    try:
        adapter.REVIEWER_SUPPLIED_ROLES["pe_firm"] = "pe_firm"
        request = {"project": "x", "pe_firm": "A Firm"}
        assert "pe_firm" not in map_status_packet(
            _status_packet_with_gap(), request)["_gap_roles"]
        assert "pe_firm" not in map_packet(_GAPPED, request)["_gap_roles"]
    finally:
        adapter.REVIEWER_SUPPLIED_ROLES.clear()
        adapter.REVIEWER_SUPPLIED_ROLES.update(original)
