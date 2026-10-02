"""Tests for Module 3 — Prompt Assembler.

Covers the assembler against the frozen example packet's mapped output (via
Module 1's ``load_template`` and Module 2's ``run_adapter``), plus small
synthetic maps for the marker and skipped-section cases. Maps to PRD criteria
1, 3, 7, 8, and 10 (see PRD.md and build-plan-v2.md "Module 3").

Run with: python3 tests/test_prompt_assembler.py
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from prompt_assembler import assemble_prompt, rendered_slide_count
from template_loader import load_template
from data_source_adapter import FixtureProvider, run_adapter

PACKET_PATH = os.path.join(
    os.path.dirname(__file__), "..", "proposal-data-packet-EXAMPLE.md"
)
TEMPLATE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "templates", "proposal-template.md"
)

FIXTURE_PROJECT = "Operational Intelligence Platform"


def _no_sleep(_):
    pass


def _real_template_and_map():
    template = load_template(TEMPLATE_PATH)
    provider = FixtureProvider.from_packet_file(PACKET_PATH)
    result = run_adapter(
        "Ridgeline Site Services", FIXTURE_PROJECT, provider,
        poll_interval=0.0, sleep=_no_sleep,
    )
    assert result["status"] == "ok", result
    return template, result["placeholder_map"]


# A minimal synthetic template: two slides, one scalar role, one list role,
# one list_of_records role, plus a recurring field, used for the marker and
# skipped-section cases so they do not depend on the real template's shape.
SYNTHETIC_TEMPLATE = {
    "deck_type": "proposal",
    "slide_count": 2,
    "slides": [
        {
            "number": 1,
            "title": "First",
            "roles": [
                {"name": "headline", "type": "string"},
                {"name": "bullets", "type": "list"},
            ],
        },
        {
            "number": 2,
            "title": "Second",
            "roles": [
                {
                    "name": "rows",
                    "type": "list_of_records",
                    "fields": ["a", "b"],
                },
            ],
        },
    ],
    "recurring_fields": [{"name": "client_short", "type": "string"}],
}


def test_real_template_produces_six_sections_in_order():
    template, placeholder_map = _real_template_and_map()
    prompt = assemble_prompt(template, placeholder_map)
    for number, title in [
        (1, "Title / Cover"), (2, "The Opportunity"), (3, "The Platform"),
        (4, "Phased Rollout"), (5, "Commercial Terms"), (6, "Next Steps"),
    ]:
        assert f"## Slide {number} — {title}" in prompt, prompt

    positions = [
        prompt.index(f"## Slide {n} —") for n in range(1, 7)
    ]
    assert positions == sorted(positions), positions


def test_every_value_traces_to_the_map():
    template, placeholder_map = _real_template_and_map()
    prompt = assemble_prompt(template, placeholder_map)

    assert placeholder_map["client_full"] in prompt, prompt
    assert placeholder_map["deck_date"] in prompt, prompt
    assert placeholder_map["opportunity_headline"] in prompt, prompt
    assert placeholder_map["components"][0]["title"] in prompt, prompt
    assert placeholder_map["value_mapping"][0]["scenario"] in prompt, prompt

    for banned in ("Fabrikam Marine", "FBK", "the inventory app", "satellite link", "vessel"):
        assert banned not in prompt, f"invented/leaked content: {banned}"


def test_missing_field_marker_on_empty_role_within_present_slide():
    placeholder_map = {
        "headline": "",
        "bullets": [],
        "rows": [{"a": "1", "b": "2"}],
        "client_short": "",
        "_skipped": [],
        "_gap_roles": [],
    }
    prompt = assemble_prompt(SYNTHETIC_TEMPLATE, placeholder_map)
    assert "headline: [MISSING: headline]" in prompt, prompt
    assert "bullets: [MISSING: bullets]" in prompt, prompt
    assert "client_short: [MISSING: client_short]" in prompt, prompt


def test_client_short_used_when_present_and_falls_back_when_absent():
    # (a) Present: the fixture packet carries company.client_short "Ridgeline",
    # so the deck-wide field uses it verbatim.
    template = load_template(TEMPLATE_PATH)
    with open(PACKET_PATH, encoding="utf-8") as f:
        packet_md = f.read()

    present_map = run_adapter(
        "Ridgeline Site Services", FIXTURE_PROJECT,
        FixtureProvider.from_packet_markdown(packet_md),
        poll_interval=0.0, sleep=_no_sleep,
    )["placeholder_map"]
    assert present_map["client_short"] == "Ridgeline", present_map["client_short"]
    prompt_present = assemble_prompt(template, present_map)
    assert "client_short: Ridgeline" in prompt_present, prompt_present

    # (b) Absent: strip the packet's client_short line. The prompt must fall back
    # to client_full and contain no [MISSING marker anywhere — a cosmetic
    # short-form degrades gracefully rather than leaking a MISSING string.
    stripped = "\n".join(
        line for line in packet_md.splitlines()
        if not line.strip().startswith("client_short:")
    )
    absent_map = run_adapter(
        "Ridgeline Site Services", FIXTURE_PROJECT,
        FixtureProvider.from_packet_markdown(stripped),
        poll_interval=0.0, sleep=_no_sleep,
    )["placeholder_map"]
    prompt_absent = assemble_prompt(template, absent_map)
    assert "client_short: Ridgeline Site Services" in prompt_absent, prompt_absent
    # The fixture states its own deal, so TERMS carries rows and no marker
    # (2026-09-23), and nothing else on the deck leaks one either.
    missing = set(re.findall(r"\[MISSING: [^\]]+\]", prompt_absent))
    assert missing == set(), missing


def test_unconfirmed_marker_on_gap_flagged_role():
    placeholder_map = {
        "headline": "Some headline",
        "bullets": ["one", "two"],
        "rows": [{"a": "1", "b": "2"}],
        "client_short": "Acme",
        "_skipped": [],
        "_gap_roles": ["headline", "rows"],
    }
    prompt = assemble_prompt(SYNTHETIC_TEMPLATE, placeholder_map)
    assert "headline: Some headline (unconfirmed, see gaps)" in prompt, prompt
    assert "rows:\n1.\n  a: 1\n  b: 2\n  (unconfirmed, see gaps)" in prompt, prompt
    # A role not in _gap_roles carries no flag.
    assert "bullets" in prompt and "(unconfirmed, see gaps)" not in prompt.split("bullets:")[1].split("rows:")[0]


def test_a_gap_on_a_deal_figure_flags_the_terms_rows_that_carry_it():
    # commercial.qofai_investment_usd is §8-gap-flagged in the fixture packet.
    # From D1a (2026-08-10) to 2026-09-23 no role rendered it, so the flag was
    # real but inert. The adaptive slide carries a packet's own deal as TERMS
    # rows, so the flag has a home again and lands on the role that prints the
    # figure, which is what PRD criterion 10's mechanism is for.
    template, placeholder_map = _real_template_and_map()
    assert placeholder_map["_gap_roles"] == ["terms_rows"], placeholder_map["_gap_roles"]
    prompt = assemble_prompt(template, placeholder_map)
    terms = prompt.split("terms_rows:")[1].split("value_mapping:")[0]
    assert "(unconfirmed, see gaps)" in terms, terms
    assert prompt.count("(unconfirmed, see gaps)") == 1, prompt


def test_timeline_rows_show_distinct_per_workstream_weeks_milestones_intact():
    # Each timeline row renders its own per-workstream span within its phase's
    # bounds (not one repeated phase span), and the milestones still render at
    # weeks 2 / 8 / 16.
    template, placeholder_map = _real_template_and_map()
    prompt = assemble_prompt(template, placeholder_map)

    rows = placeholder_map["timeline_rows"]
    spans = [r["weeks"] for r in rows]
    assert len(set(spans)) > 1, spans
    for r in rows:
        assert f"weeks: {r['weeks']}" in prompt, (r, prompt)
        start, end = (int(x) for x in r["weeks"].split("–"))
        lo, hi = (1, 8) if "PHASE 1" in r["phase"] else (8, 16)
        assert lo <= start <= end <= hi, r

    assert placeholder_map["milestones"] == [
        {"id": "M0", "week": 2, "label": "SCOPE LOCKED"},
        {"id": "M1", "week": 8, "label": "PILOT VALIDATED"},
        {"id": "M2", "week": 16, "label": "FLEET LIVE"},
    ], placeholder_map["milestones"]
    for wk in (2, 8, 16):
        assert f"week: {wk}" in prompt, (wk, prompt)


def test_record_field_missing_flagged_per_field_not_bare_none():
    # An empty field within a present record is flagged per-field (this is how a
    # timeline workstream missing its weeks surfaces as load-bearing), rather
    # than rendering a bare None.
    template = {
        "deck_type": "proposal", "slide_count": 1,
        "slides": [{
            "number": 1, "title": "T",
            "roles": [{
                "name": "rows", "type": "list_of_records",
                "fields": ["phase", "workstream", "weeks"],
            }],
        }],
        "recurring_fields": [],
    }
    placeholder_map = {
        "rows": [{"phase": "P1", "workstream": "Setup", "weeks": None}],
        "_skipped": [], "_gap_roles": [],
    }
    prompt = assemble_prompt(template, placeholder_map)
    assert "weeks: [MISSING: weeks]" in prompt, prompt
    assert "None" not in prompt, prompt


def test_skipped_slide_omitted_cleanly_no_marker():
    placeholder_map = {
        "headline": "H",
        "bullets": ["x"],
        "rows": [],
        "client_short": "Acme",
        "_skipped": [2],
        "_gap_roles": [],
    }
    prompt = assemble_prompt(SYNTHETIC_TEMPLATE, placeholder_map)
    assert "## Slide 1 —" in prompt, prompt
    assert "## Slide 2 —" not in prompt, prompt
    assert "rows" not in prompt, prompt
    assert "MISSING" not in prompt, prompt


def test_skipped_and_missing_distinguished_in_one_prompt():
    # PRD criterion 7's combined test: one skipped section, one present section
    # with a null/empty field, in the same run.
    placeholder_map = {
        "headline": "",
        "bullets": ["x"],
        "rows": [{"a": "1", "b": "2"}],
        "client_short": "Acme",
        "_skipped": [2],
        "_gap_roles": [],
    }
    prompt = assemble_prompt(SYNTHETIC_TEMPLATE, placeholder_map)
    assert "## Slide 1 —" in prompt, prompt
    assert "## Slide 2 —" not in prompt, prompt
    assert "headline: [MISSING: headline]" in prompt, prompt
    assert "rows" not in prompt, prompt


def test_real_skipped_sections_omitted_end_to_end():
    template = load_template(TEMPLATE_PATH)
    provider = FixtureProvider.from_packet_file(PACKET_PATH)
    result = run_adapter(
        "Ridgeline", FIXTURE_PROJECT, provider,
        sections_requested=["cover", "opportunity", "platform"],
        poll_interval=0.0, sleep=_no_sleep,
    )
    prompt = assemble_prompt(template, result["placeholder_map"])
    assert "## Slide 1 —" in prompt, prompt
    assert "## Slide 2 —" in prompt, prompt
    assert "## Slide 3 —" in prompt, prompt
    assert "## Slide 4 —" not in prompt, prompt
    assert "## Slide 5 —" not in prompt, prompt
    assert "## Slide 6 —" not in prompt, prompt


def test_records_rendered_with_declared_fields():
    template, placeholder_map = _real_template_and_map()
    prompt = assemble_prompt(template, placeholder_map)
    first_component = placeholder_map["components"][0]
    assert f"number: {first_component['number']}" in prompt, prompt
    assert f"kicker: {first_component['kicker']}" in prompt, prompt
    assert f"title: {first_component['title']}" in prompt, prompt
    assert f"description: {first_component['description']}" in prompt, prompt


def test_preamble_carries_recurring_fields_once():
    template, placeholder_map = _real_template_and_map()
    prompt = assemble_prompt(template, placeholder_map)
    assert "# Deck-wide fields (apply to every slide)" in prompt, prompt
    for name in (
        "client_full", "client_short", "pe_firm", "project_name",
        "deck_date", "confidentiality", "deck_type_label",
    ):
        assert prompt.count(f"\n{name}:") == 1, (name, prompt)


def test_review_framing_marker_present_and_last():
    template, placeholder_map = _real_template_and_map()
    prompt = assemble_prompt(template, placeholder_map)
    assert prompt.rstrip().endswith(
        "Not a finished or sendable deck."
    ), prompt[-200:]
    assert "Claude Design" in prompt, prompt
    assert "human review" in prompt, prompt


def test_labeled_role_emits_render_under_heading_instruction():
    # A role carrying a display label emits its value under a render-under-
    # heading instruction so Claude Design titles the block; an unlabeled role
    # emits the plain role name unchanged. Covers scalar and list_of_records.
    template = {
        "deck_type": "proposal", "slide_count": 1,
        "slides": [{
            "number": 1, "title": "T",
            "roles": [
                {"name": "payment_mechanics", "type": "string",
                 "label": "HOW PAYMENT WORKS"},
                {"name": "plain", "type": "string"},
                {"name": "rows", "type": "list_of_records",
                 "fields": ["a"], "label": "A TABLE"},
            ],
        }],
        "recurring_fields": [],
    }
    placeholder_map = {
        "payment_mechanics": "Measured monthly. Converted. Share paid.",
        "plain": "no heading here",
        "rows": [{"a": "1"}],
        "_skipped": [], "_gap_roles": [],
    }
    prompt = assemble_prompt(template, placeholder_map)
    assert (
        'payment_mechanics [render under heading "HOW PAYMENT WORKS"]: '
        "Measured monthly. Converted. Share paid." in prompt
    ), prompt
    assert 'rows [render under heading "A TABLE"]:' in prompt, prompt
    # Unlabeled role is untouched — no heading instruction on its line.
    assert "plain: no heading here" in prompt, prompt
    assert "plain [render under heading" not in prompt, prompt


def test_real_payment_steps_travel_as_a_terms_row():
    # End-to-end against the real template + fixture. The payment-process steps
    # had their own headed block on the retired layout; on the adaptive slide
    # they are one TERMS row whose label says what they are.
    template, placeholder_map = _real_template_and_map()
    prompt = assemble_prompt(template, placeholder_map)
    assert "payment_mechanics" not in prompt, prompt
    assert "  label: How payment works" in prompt, prompt


def test_prompt_is_deterministic():
    template, placeholder_map = _real_template_and_map()
    assert assemble_prompt(template, placeholder_map) == assemble_prompt(template, placeholder_map)


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


# ---- pe_firm is optional: un-slotted and cosmetic, so absence emits no marker ----
#
# Why this exists. `pe_firm` has no rendered slot on any slide (it is absent from
# the page-footer format and from the cover's fields) and the platform never
# supplies it: `pe_firm` is None on every company in the registry, because the
# request contract treats it as a caller-supplied disambiguator rather than deck
# content. So it was structurally guaranteed absent on every live run, and while it
# was declared non-optional the assembler emitted `pe_firm: [MISSING: pe_firm]`,
# which the renderer had nowhere to place, dropped, and the render-fidelity guard
# then refused. One hosted render was refused for exactly this on 2026-08-17.
#
# `data_source_adapter` states the governing rule three lines above `pe_firm`'s own
# mapping: MISSING markers are reserved for load-bearing, un-defaultable fields.
# These pin that pe_firm is treated as the cosmetic field it is, in both templates.

def _pe_firm_role(template_path):
    template = load_template(template_path)
    for role in template["recurring_fields"]:
        if role["name"] == "pe_firm":
            return role
    raise AssertionError(f"pe_firm is not a recurring field in {template_path}")


def test_pe_firm_is_declared_optional_in_both_templates():
    status_path = os.path.join(
        os.path.dirname(__file__), "..", "templates", "status-template.md"
    )
    for path in (TEMPLATE_PATH, status_path):
        role = _pe_firm_role(path)
        assert role.get("optional") is True, (path, role)


def test_an_absent_pe_firm_emits_no_line_and_no_marker():
    template, placeholder_map = _real_template_and_map()
    source_map = dict(placeholder_map)
    source_map["pe_firm"] = ""
    prompt = assemble_prompt(template, source_map)
    assert "[MISSING: pe_firm]" not in prompt, prompt[:600]
    assert not re.search(r"^\s*pe_firm\s*:", prompt, re.M), prompt[:600]


def test_a_supplied_pe_firm_still_renders():
    # Optional must mean "omitted when absent", never "dropped when present": a
    # reviewer who types the sponsor in has to see it reach the deck.
    template, placeholder_map = _real_template_and_map()
    source_map = dict(placeholder_map)
    source_map["pe_firm"] = "Woodgrove Partners"
    prompt = assemble_prompt(template, source_map)
    assert "pe_firm: Woodgrove Partners" in prompt, prompt[:600]


def test_a_load_bearing_field_still_gets_its_marker():
    # The guard against over-applying this: making pe_firm optional must not have
    # made absence silent in general. A roster-adjacent field with no value still
    # marks, which is what keeps the golden rule visible on the deck.
    template, placeholder_map = _real_template_and_map()
    source_map = dict(placeholder_map)
    # Slide 2 repeats since 2026-09-13, so it renders against its own entry in
    # `opportunities` rather than against the flat map. Blanking the role there
    # is what blanks it on the slide.
    source_map["opportunities"] = [
        dict(item, opportunity_summary="")
        for item in (placeholder_map.get("opportunities") or [{}])
    ]
    source_map["opportunity_summary"] = ""
    prompt = assemble_prompt(template, source_map)
    assert "[MISSING: opportunity_summary]" in prompt, prompt[:600]


def test_a_record_that_never_carried_a_field_emits_no_line_for_it():
    """The distinction a phase-only timeline row needs (E11 Stage 1).

    A field the record carries EMPTY is a gap and keeps its per-field marker
    (the test above). A field the record never carried at all says nothing
    about itself: no paper in the corpus decomposes a phase into workstreams,
    so a phase row has no workstream key, and stamping a marker inside every
    bar would report a gap the packet already reports once, on the role.
    """
    template = {
        "deck_type": "proposal", "slide_count": 1,
        "slides": [{
            "number": 1, "title": "T",
            "roles": [{
                "name": "rows", "type": "list_of_records",
                "fields": ["phase", "workstream", "weeks"],
            }],
        }],
        "recurring_fields": [],
    }
    placeholder_map = {
        "rows": [{"phase": "P1", "weeks": "0–3"}],
        "_skipped": [], "_gap_roles": [],
    }
    prompt = assemble_prompt(template, placeholder_map)
    assert "phase: P1" in prompt, prompt
    assert "weeks: 0–3" in prompt, prompt
    assert "workstream" not in prompt, prompt


# ---------------------------------------------------------------------------
# ITEM 15: slide 2 repeats, and the footer counts what is actually emitted.
# ---------------------------------------------------------------------------

def _two_opportunity_map(placeholder_map):
    """The same map with a second opportunity, distinguishable from the first."""
    first = (placeholder_map.get("opportunities") or [{}])[0]
    second = dict(first, opportunity_headline="A SECOND OPPORTUNITY",
                  opportunity_summary="Its own today-versus-after story.")
    return dict(placeholder_map, opportunities=[first, second])


def test_two_opportunities_render_ten_slides_from_six_blocks():
    """The shape settled 2026-09-22. Slide 2 repeats because each opportunity
    has its own today-versus-after story, and the Platform, the Phased Rollout
    and the Next Steps repeat because each opportunity is its own build. The
    cover and the commercial terms belong to the engagement and do not."""
    template, placeholder_map = _real_template_and_map()
    prompt = assemble_prompt(template, _two_opportunity_map(placeholder_map))

    headers = [line for line in prompt.splitlines()
               if line.startswith("## Slide ")]
    assert len(headers) == 10
    assert sum("The Opportunity" in line for line in headers) == 2
    assert sum("The Platform" in line for line in headers) == 2
    assert sum("Phased Rollout" in line for line in headers) == 2
    assert sum("Next Steps" in line for line in headers) == 2
    assert sum("Commercial Terms" in line for line in headers) == 1
    assert sum("Title / Cover" in line for line in headers) == 1


def test_the_slides_are_numbered_sequentially_across_the_whole_deck():
    """Not by their declared block numbers, which would print two slide 2s and
    stop at six."""
    template, placeholder_map = _real_template_and_map()
    prompt = assemble_prompt(template, _two_opportunity_map(placeholder_map))

    numbers = [int(line.split()[2]) for line in prompt.splitlines()
               if line.startswith("## Slide ")]
    assert numbers == list(range(1, 11))


def test_each_opportunity_renders_from_its_own_entry():
    """The whole point of the repeat, and the property item 15 exists for: one
    opportunity's slide must never be written from another's material."""
    template, placeholder_map = _real_template_and_map()
    prompt = assemble_prompt(template, _two_opportunity_map(placeholder_map))

    assert prompt.count("A SECOND OPPORTUNITY") == 1
    assert "Its own today-versus-after story." in prompt


def test_one_opportunity_still_renders_six_slides_numbered_one_to_six():
    """Every deck that worked before this, unchanged in shape."""
    template, placeholder_map = _real_template_and_map()
    prompt = assemble_prompt(template, placeholder_map)

    numbers = [int(line.split()[2]) for line in prompt.splitlines()
               if line.startswith("## Slide ")]
    assert numbers == [1, 2, 3, 4, 5, 6]


def test_the_footer_denominator_counts_what_was_actually_emitted():
    """Read from data rather than written into the template, because six blocks
    render six slides for one opportunity and ten for two."""
    template, placeholder_map = _real_template_and_map()

    assert "total_slides: 6" in assemble_prompt(template, placeholder_map)
    assert "total_slides: 10" in assemble_prompt(
        template, _two_opportunity_map(placeholder_map))


def test_a_stated_total_slides_wins_over_the_derived_one():
    """The status packet states its own, which `coverage_guard` slots and the
    mapping half reads, so deriving over the top of it would replace platform
    data with an inference."""
    template, placeholder_map = _real_template_and_map()
    prompt = assemble_prompt(template, dict(placeholder_map, total_slides="99"))
    assert "total_slides: 99" in prompt


def test_the_count_is_the_same_walk_the_emitting_is():
    """The two cannot disagree, because a footer that says one thing while the
    deck does another is worse than no footer."""
    template, placeholder_map = _real_template_and_map()
    for source_map in (placeholder_map, _two_opportunity_map(placeholder_map)):
        prompt = assemble_prompt(template, source_map)
        emitted = sum(1 for line in prompt.splitlines()
                      if line.startswith("## Slide "))
        assert rendered_slide_count(template, source_map) == emitted


def test_a_skipped_section_is_not_counted_in_the_denominator():
    """The count walks the same skip rules the emitting does."""
    template, placeholder_map = _real_template_and_map()
    source_map = dict(_two_opportunity_map(placeholder_map), _skipped=[5])
    prompt = assemble_prompt(template, source_map)

    assert "total_slides: 9" in prompt
    assert sum(1 for line in prompt.splitlines()
               if line.startswith("## Slide ")) == 9


def test_the_template_writes_no_slide_count_into_the_footer():
    """A hardcoded denominator is exactly what a variable-count deck cannot
    have, so the template states the role rather than the number."""
    spec = open(TEMPLATE_PATH, encoding="utf-8").read()
    assert "{total_slides}" in spec
    assert "NN / 06" not in spec
    assert "02 / 06" not in spec
