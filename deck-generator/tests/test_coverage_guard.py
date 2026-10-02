"""Tests for the field-coverage guard (`src/coverage_guard.py`).

The guard is the real fix for silent field drops: every structured leaf in a
packet's rendered sections (§1, §2, §4, §5, §6, §7) must either be slotted
(COVERAGE_MAP) or allowlisted (EXCLUSION_ALLOWLIST); anything else fails the run
loudly, naming the field path.

These tests are schema-driven, not tied to the Ridgeline fixture's values:
  (a) the guard FAILS when a populated rendered-section field has no slot and no
      allowlist entry,
  (b) the guard PASSES when every field is either slotted or allowlisted,
plus a standing demonstration that the frozen fixture currently trips the guard
on exactly the two undecided commercial clauses (this expectation flips once the
client_retention_note / no_improvement_clause decision is implemented; see the
note on that test).

Run with: python3 tests/test_coverage_guard.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from coverage_guard import (
    CoverageError,
    COVERAGE_MAP,
    EXCLUSION_ALLOWLIST,
    check_coverage,
    rendered_leaf_paths,
)
from data_source_adapter import FixtureProvider, run_adapter
from prompt_assembler import assemble_prompt
from template_loader import load_template

PACKET_PATH = os.path.join(
    os.path.dirname(__file__), "..", "proposal-data-packet-EXAMPLE.md"
)
TEMPLATE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "templates", "proposal-template.md"
)

FIXTURE_PROJECT = "Operational Intelligence Platform"


def _no_sleep(_):
    pass


# A minimal synthetic packet with a single rendered section (§6) whose every
# field is accounted for by the real COVERAGE_MAP / EXCLUSION_ALLOWLIST, so the
# guard's partition logic is exercised without depending on the full fixture or
# the undecided clauses. `_extra_commercial_lines` lets a test inject a field.
def _synthetic_packet(extra_commercial_lines=""):
    return (
        '---\nconfidence: "high"\ndata_completeness: 0.92\n---\n\n'
        "# Synthetic packet\n\n"
        "## 6 · Commercial Terms\n\n"
        "```yaml\n"
        "commercial:\n"
        "  qofai_investment_usd: 100000\n"
        "  qofai_investment_note: \"note\"\n"
        "  client_upfront_usd: 0\n"
        "  client_upfront_note: \"none\"\n"
        "  cap_note: \"capped\"\n"
        "  ev_footnote: \"directional\"\n"
        "  comp_schedule:\n"
        "    - { year: \"YEAR 1\", pct: 25 }\n"
        "  scenarios:\n"
        "    - name: \"BASE\"\n"
        "      margin_gain_pp: 2.2\n"
        "      direct_uplift_usd_yr: 2050000\n"
        "      qofai_comp_usd: 685000\n"
        "      client_retained_ebitda_usd: 4900000\n"
        "      enterprise_value_at_exit_usd: 14000000\n"
        "  how_payment_works:\n"
        "    - \"measured monthly\"\n"
        f"{extra_commercial_lines}"
        "```\n"
    )


def _template():
    return load_template(TEMPLATE_PATH)


def _map_and_prompt(packet_md, sections_requested=None):
    provider = FixtureProvider.from_packet_markdown(packet_md)
    result = run_adapter(
        "Any Co", "Any Project", provider,
        sections_requested=sections_requested,
        poll_interval=0.0, sleep=_no_sleep,
    )
    template = _template()
    prompt = assemble_prompt(template, result["placeholder_map"])
    return template, result["placeholder_map"], prompt


# ---- (a) guard fails on an unaccounted populated field ----

def test_guard_fails_on_unaccounted_populated_field():
    # A brand-new populated field with no slot and no allowlist entry.
    packet = _synthetic_packet('  brand_new_undecided_field: "real content"\n')
    template, pmap, prompt = _map_and_prompt(packet)
    try:
        check_coverage(packet, template, pmap, prompt)
    except CoverageError as e:
        assert "commercial.brand_new_undecided_field" in str(e), str(e)
    else:
        raise AssertionError(
            "expected CoverageError for an unaccounted populated field"
        )


def test_guard_error_names_every_unaccounted_field():
    packet = _synthetic_packet(
        '  first_orphan: "a"\n  second_orphan: "b"\n'
    )
    template, pmap, prompt = _map_and_prompt(packet)
    try:
        check_coverage(packet, template, pmap, prompt)
    except CoverageError as e:
        assert "commercial.first_orphan" in str(e), str(e)
        assert "commercial.second_orphan" in str(e), str(e)
    else:
        raise AssertionError("expected CoverageError naming both orphans")


# ---- (b) guard passes when every field is slotted or allowlisted ----

def test_guard_passes_when_all_fields_slotted_or_allowlisted():
    # Every field in this synthetic §6 is in COVERAGE_MAP; nothing orphaned.
    packet = _synthetic_packet()
    template, pmap, prompt = _map_and_prompt(packet)
    check_coverage(packet, template, pmap, prompt)  # must not raise


def test_guard_passes_when_field_is_allowlisted_not_slotted():
    # A firmographic field (company.sector) is allowlisted — deliberately off the
    # deck as internal scoping context — not slotted. Present in the packet, it
    # must satisfy the guard via the allowlist path, not the slot path.
    packet = (
        '---\nconfidence: "high"\ndata_completeness: 0.92\n---\n\n'
        "## 1 · Company Profile\n\n"
        "```yaml\n"
        "company:\n"
        '  sector: "Commercial site preparation & earthwork"\n'
        "```\n\n"
        "## 6 · Commercial Terms\n\n"
        "```yaml\n"
        "commercial:\n"
        "  qofai_investment_usd: 100000\n"
        '  qofai_investment_note: "note"\n'
        "  client_upfront_usd: 0\n"
        '  client_upfront_note: "none"\n'
        '  cap_note: "capped"\n'
        '  ev_footnote: "directional"\n'
        "  comp_schedule:\n"
        '    - { year: "YEAR 1", pct: 25 }\n'
        "  scenarios:\n"
        '    - name: "BASE"\n'
        "      margin_gain_pp: 2.2\n"
        "      direct_uplift_usd_yr: 2050000\n"
        "      qofai_comp_usd: 685000\n"
        "      client_retained_ebitda_usd: 4900000\n"
        "      enterprise_value_at_exit_usd: 14000000\n"
        "  how_payment_works:\n"
        '    - "measured monthly"\n'
        "```\n"
    )
    template, pmap, prompt = _map_and_prompt(packet)
    check_coverage(packet, template, pmap, prompt)  # must not raise


def test_ev_assumptions_are_slotted_into_the_footnote_role():
    # The four ev_assumptions fields are slotted to terms_footnote (composed into
    # the footnote), not allowlisted. Present in the packet, they clear the guard
    # via the slot path, and none is left on the allowlist.
    for path in (
        "commercial.ev_assumptions.exit_ebitda_multiple",
        "commercial.ev_assumptions.reporting_readiness_multiple_uplift",
        "commercial.ev_assumptions.adjusted_ebitda_base_usd",
        "commercial.ev_assumptions.reporting_readiness_ev_usd",
    ):
        assert COVERAGE_MAP.get(path) == "terms_footnote", path
        assert path not in EXCLUSION_ALLOWLIST, path
    packet = _synthetic_packet(
        "  ev_assumptions:\n"
        "    exit_ebitda_multiple: 6.0\n"
        "    reporting_readiness_multiple_uplift: 0.25\n"
        "    adjusted_ebitda_base_usd: 6900000\n"
        "    reporting_readiness_ev_usd: 1725000\n"
    )
    template, pmap, prompt = _map_and_prompt(packet)
    check_coverage(packet, template, pmap, prompt)  # must not raise


# ---- guard tolerates skipped slides (request-driven omission, not a drop) ----

def test_guard_ignores_fields_whose_slide_is_skipped():
    # When commercial_terms is not requested, slide 5's roles are absent from
    # the prompt by design. That is not a silent drop, so the guard must not
    # flag the §6 fields' roles for failing to appear.
    packet = _synthetic_packet()
    template, pmap, prompt = _map_and_prompt(
        packet, sections_requested=["cover", "opportunity", "platform"]
    )
    assert 5 in pmap["_skipped"], pmap["_skipped"]
    check_coverage(packet, template, pmap, prompt)  # must not raise


# ---- dead-slot detection ----

def test_guard_detects_dead_slot():
    # A COVERAGE_MAP entry pointing at a role the template does not declare must
    # be caught, not silently trusted.
    packet = _synthetic_packet()
    template, pmap, prompt = _map_and_prompt(packet)
    original = COVERAGE_MAP.get("commercial.ev_footnote")
    COVERAGE_MAP["commercial.ev_footnote"] = "role_that_does_not_exist"
    try:
        check_coverage(packet, template, pmap, prompt)
    except CoverageError as e:
        assert "role_that_does_not_exist" in str(e), str(e)
    else:
        raise AssertionError("expected CoverageError for a dead slot")
    finally:
        COVERAGE_MAP["commercial.ev_footnote"] = original


# ---- no path is both slotted and allowlisted (the partition is clean) ----

def test_no_path_is_both_slotted_and_allowlisted():
    overlap = set(COVERAGE_MAP) & set(EXCLUSION_ALLOWLIST)
    assert not overlap, overlap


def test_a_packets_own_deal_fields_are_slotted_as_terms_rows():
    # D1a (2026-08-10) moved these off the deck as studio-only input. The
    # adaptive commercial slide (2026-09-23) carries a packet's own deal as
    # TERMS rows, so a packet that states them shows them, and a PRD, which
    # states none, leaves the marker for a reviewer.
    for path in (
        "commercial.qofai_investment_usd",
        "commercial.qofai_investment_note",
        "commercial.client_upfront_usd",
        "commercial.client_upfront_note",
        "commercial.comp_schedule[].year",
        "commercial.comp_schedule[].pct",
        "commercial.cap_note",
        "commercial.client_retention_note",
        "commercial.no_improvement_clause",
        "commercial.how_payment_works[]",
    ):
        assert COVERAGE_MAP.get(path) == "terms_rows", path
        assert path not in EXCLUSION_ALLOWLIST, path


def test_the_prd_cost_total_and_payback_are_slotted():
    for path in ("commercial.investment[].label", "commercial.investment[].value",
                 "commercial.investment[].basis",
                 "commercial.investment[].opportunity"):
        assert COVERAGE_MAP.get(path) == "investment_rows", path
    assert COVERAGE_MAP.get("commercial.scenarios[].payback") == "return_rows"


def test_every_allowlist_entry_has_a_reason():
    for path, reason in EXCLUSION_ALLOWLIST.items():
        assert isinstance(reason, str) and reason.strip(), path


# ---- the frozen fixture: every rendered-section field is accounted for ----

def test_fixture_has_no_unaccounted_rendered_fields():
    # DECISION IMPLEMENTED. commercial.client_retention_note and
    # commercial.no_improvement_clause are now slotted on Slide 5
    # (client_retention / downside_protection), so no rendered-section leaf in
    # the frozen packet is left unaccounted. If a future schema change adds a
    # field with no slot and no allowlist entry, this test (and the wired guard)
    # goes red until it is deliberately handled.
    unaccounted = sorted(
        p
        for p in rendered_leaf_paths(open(PACKET_PATH, encoding="utf-8").read())
        if p not in COVERAGE_MAP and p not in EXCLUSION_ALLOWLIST
    )
    assert unaccounted == [], unaccounted


def test_fixture_passes_the_wired_guard_end_to_end():
    # The real entry point runs clean now: guard raises nothing on the fixture.
    template, pmap, prompt = _map_and_prompt(
        open(PACKET_PATH, encoding="utf-8").read()
    )
    check_coverage(
        open(PACKET_PATH, encoding="utf-8").read(), template, pmap, prompt
    )  # must not raise
    # And the two once-dropped clauses now actually reach the prompt.
    assert "Client retains 80–84%, 100% thereafter." in prompt, prompt
    assert (
        "If margins don't improve above your locked baseline, QofAI earns nothing."
        in prompt
    ), prompt


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
