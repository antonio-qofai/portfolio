"""Tests for the packet document (E9b, structure and absence).

Every test here runs the emitted document through the real pipeline: the real
proposal template, the real `coverage_guard`, the real `map_packet`, the real
`prompt_assembler`. Nothing is asserted against a hand-written expectation of
what the document should look like, because the guarantee this window carries is
not "the markdown reads nicely" but "the four consumers downstream of it agree
the absences are visible".

`_document_absent_paths` is the reason this file can make that claim. It reads a
slot's value back out of the RENDERED document, resolving each dotted path
against the parsed sections, so the absent set it returns is independent evidence
rather than a second look at the table that produced the gaps. Comparing it to
the gap paths `map_packet` extracted is therefore a real assertion and not a
tautology, which is what the done-when asked for.

Where the numbers come from. 18 render roles and 67 contract leaves in sections 1
through 7 are both counted by walking the code (`COVERAGE_MAP` and `SLOTS`)
rather than written down here, so a schema change moves them together instead of
reddening a stale constant.
"""

import pytest

from coverage_guard import COVERAGE_MAP, CoverageError, check_coverage
from data_source_adapter import _section_yaml, _split_sections, map_packet
from packet_assembly import assemble
from prompt_assembler import MISSING_MARKER, assemble_prompt
from template_loader import load_template
import packet_document
from packet_document import (
    REASONS,
    SLOTS,
    SOURCED,
    TEMPLATED,
    TEMPLATED_DEFAULTS,
    UNSOURCEABLE,
    build,
)

TEMPLATE_PATH = "templates/proposal-template.md"

# A request, not client data: the document echoes it into §0 for traceability and
# the names are placeholders chosen here so no real client appears in a test.
REQUEST = {
    "intent": "generate_project_planning_proposal",
    "company": "Test Subject One",
    "project": "Test Project",
    "proposal_date": "2026-08-13",
}


@pytest.fixture
def template():
    return load_template(TEMPLATE_PATH)


def pipeline(markdown, template):
    """The document's full downstream run: map, assemble, guard."""
    placeholder_map = map_packet(markdown, REQUEST)
    prompt = assemble_prompt(template, placeholder_map)
    check_coverage(markdown, template, placeholder_map, prompt)
    return placeholder_map, prompt


def _document_absent_paths(markdown):
    """Slot paths the RENDERED document carries no value for.

    Read back out of the document rather than out of `_absent_paths`, so the set
    is independent of the table that wrote the gaps. A `[]` path segment is a
    record list: descend into its first record, and an empty or omitted list
    means every leaf beneath it is absent.
    """
    sections = {
        number: _section_yaml(text)
        for number, text in _split_sections(markdown).items()
    }
    absent = set()
    for path, section, _kind, _origin in SLOTS:
        node = sections.get(section, {})
        if section == 2:
            # Section 2 is a list of per-opportunity entries since 2026-09-13,
            # and a slot path is read inside one. Independent evidence still:
            # this descends the rendered document rather than consulting the
            # table that wrote the gaps.
            entries = node.get(packet_document.OPPORTUNITY_SECTIONS) or [{}]
            node = entries[0]
        for segment in path.split("."):
            key = segment[:-2] if segment.endswith("[]") else segment
            node = node.get(key) if isinstance(node, dict) else None
            if isinstance(node, list):
                node = node[0] if node else None
        if node is None or node == "" or node == []:
            absent.add(path)
    return absent


def test_the_document_passes_the_coverage_guard_against_the_real_template(template):
    """No section 1 through 7 leaf is unaccounted, which is the constraint that
    decides how absence is written. Reds if an absent scalar list is emitted as
    `null` (the leaf loses its `[]` and falls out of COVERAGE_MAP), if an absent
    record list is emitted as `[]` (the bare `foo[]` leaf is in neither the map
    nor the allowlist), or if a leaf the frozen fixtures do not emit is added."""
    pipeline(build(REQUEST), template)


def test_absence_is_type_preserving_scalar_null_list_empty_block_intact():
    """`E9-RESCOPE-DESIGN.md` section 3.1's rule, read back off the document.

    An absent scalar is `null`, an absent list of scalars is `[]`, and an absent
    block is the block with its own leaves absent in their own types, never
    `null` on the block itself.
    """
    # Section 2 is a list of per-opportunity entries since 2026-09-13, and the
    # type-preserving rule holds INSIDE an entry: one entry per opportunity, and
    # a deck with one opportunity still emits one.
    section_2 = _section_yaml(_split_sections(build(REQUEST))[2])
    entries = section_2[packet_document.OPPORTUNITY_SECTIONS]
    assert len(entries) == 1
    opportunity = entries[0]
    assert opportunity["today_pain_points"] == []
    assert opportunity["target_capabilities"] == []
    assert isinstance(opportunity["build_summary"], dict)
    assert opportunity["build_summary"]["duration_weeks"] is None


def test_an_absent_record_list_is_omitted_rather_than_emitted_empty():
    """The one place the design doc is wrong, measured rather than inherited.

    Section 3.1 says an absent list of records emits `[]`, but the same walk
    collapses `today_metrics: []` to the leaf `today_metrics[]`, which is in
    neither COVERAGE_MAP nor EXCLUSION_ALLOWLIST, so `check_coverage` raises. An
    absent record list is therefore omitted from the document entirely.
    """
    markdown = build(REQUEST)
    sections = _split_sections(markdown)
    opportunity = _section_yaml(sections[2])[
        packet_document.OPPORTUNITY_SECTIONS][0]
    assert "today_metrics" not in opportunity
    assert "phases" not in opportunity["build_summary"]
    assert "platform_layers" not in _section_yaml(sections[4])
    assert "phases" not in _section_yaml(sections[5])["timeline"]
    assert "scenarios" not in _section_yaml(sections[6])["commercial"]
    assert "next_steps" not in _section_yaml(sections[7])


def test_every_absent_path_is_named_in_section_8_gaps(template):
    """The failure mode worth a test of its own: a field absent in the value but
    missing from section 8 `gaps` is invisible on the deck. Asserted as set
    equality between what the document carries no value for and what `map_packet`
    extracted from the gaps block, not by eyeballing the document."""
    markdown = build(REQUEST)
    placeholder_map, _prompt = pipeline(markdown, template)
    assert _document_absent_paths(markdown) == set(placeholder_map["_gaps"])
    assert len(placeholder_map["_gaps"]) == len(SLOTS)


def test_every_gap_reason_is_drawn_from_the_four_origin_classes():
    """A gap with no reason, or a reason invented outside the four classes, would
    tell a reviewer nothing about whether to wait for the platform or to type the
    value in themselves."""
    gaps = _section_yaml(_split_sections(build(REQUEST))[8])["provenance"]["gaps"]
    assert {gap["reason"] for gap in gaps} == set(REASONS.values())


# The one role exempt from the marker rule, and the exemption is deliberately
# pinned by name and by size below rather than left as a smaller count. `pe_firm`
# has no rendered slot on any slide (absent from the page-footer format and from
# the cover's fields) and the platform never supplies it, so it is cosmetic and
# un-slotted. `data_source_adapter` states the governing rule beside its mapping:
# MISSING markers are reserved for load-bearing, un-defaultable fields. Declared
# `optional` in both templates 2026-08-17, after a hosted render was refused
# because the renderer had nowhere to put `[MISSING: pe_firm]` and dropped it.
#
# THREE MORE, 2026-09-23, and each one by a human decision. The commercial slide
# became an adaptive deal sheet (`build-plan-commercial-slide.md`) whose blocks
# are drawn ONLY when something states them: Antonio approved the plan's "each
# block optional" on 2026-09-22 and ruled the same day that the value chart is
# hidden when there are no comp, retained EBITDA and EV figures. So an empty
# INVESTMENT, RETURN or chart is not drawn at all, rather than drawn as a
# marker. TERMS is deliberately NOT exempt: with no deal entered it keeps its
# `[MISSING: terms_rows]` marker, because the deal is the one thing a reviewer
# must supply before the deck ships.
MARKER_EXEMPT_ROLES = {"pe_firm", "investment_rows", "return_rows", "value_mapping"}


def test_every_render_role_but_the_exempt_one_comes_out_as_a_missing_marker(template):
    """Absence reaches the deck. Every role COVERAGE_MAP resolves to renders as
    `[MISSING: role]`, which `render_guard` treats as a hard failure if a render
    drops it — except the un-slotted cosmetic ones named above."""
    _placeholder_map, prompt = pipeline(build(REQUEST), template)
    roles = sorted(set(COVERAGE_MAP.values()))
    assert len(roles) == 18
    unmarked = [
        role for role in roles
        if MISSING_MARKER.format(role=role) not in prompt
    ]
    assert set(unmarked) == MARKER_EXEMPT_ROLES, unmarked


def test_the_marker_exemption_is_exactly_four_roles_wide(template):
    """The exemption is the loophole in "absence reaches the deck", so its size is
    pinned. A second exempt role is a decision for a human, and it would silently
    hide a gap on a client-facing slide, which is the failure the marker exists to
    prevent."""
    assert MARKER_EXEMPT_ROLES == {"pe_firm", "investment_rows", "return_rows",
                                   "value_mapping"}
    _placeholder_map, prompt = pipeline(build(REQUEST), template)
    roles = sorted(set(COVERAGE_MAP.values()))
    marked = [
        role for role in roles
        if MISSING_MARKER.format(role=role) in prompt
    ]
    assert len(marked) == 14, marked
    assert "terms_rows" in marked, "the deal is never silently absent"


def test_the_exempt_role_is_still_visible_to_the_reviewer_in_section_8(template):
    """The exemption removes the marker from the SLIDE, not the gap from the
    record. If it did both, an absent sponsor would be invisible everywhere, which
    is the thing the golden rule forbids."""
    markdown = build(REQUEST)
    placeholder_map, _prompt = pipeline(markdown, template)
    gaps = set(placeholder_map["_gaps"])
    assert "company.pe_firm" in gaps, sorted(gaps)


def test_templated_carries_exactly_the_contracts_three_declared_paths():
    """TEMPLATED is closed. A fourth templated default is a decision for a human,
    so the table cannot acquire one without the import-time check firing."""
    declared = {
        path for path, _section, _kind, origin in SLOTS if origin == TEMPLATED
    }
    assert declared
    for path in declared:
        assert any(path.startswith(default) for default in TEMPLATED_DEFAULTS)
    assert set(TEMPLATED_DEFAULTS) == {
        "platform_layers", "next_steps", "commercial.comp_schedule",
    }


def test_a_fourth_templated_default_is_refused_at_import_time(monkeypatch):
    """The closed class enforces itself rather than relying on a reader noticing."""
    monkeypatch.setattr(
        packet_document, "ORIGINS", {"commercial.cap_note": TEMPLATED}
    )
    with pytest.raises(ValueError, match="closed class"):
        packet_document._check_templated_is_closed()


def test_map_packet_ignores_the_fourth_provenance_key(template):
    """Section 8 can carry richer provenance with zero upstream impact.

    `map_packet` reads exactly `from_kg`, `derived` and `templated_defaults` into
    `_provenance` and reads `gaps` separately, so the `sources` key recording
    which tool or span a figure arrived with is carried and ignored. Verified
    here rather than assumed, because adding a key upstream reads on it.
    """
    markdown = build(REQUEST, sources={"company.name": "list_companies"})
    ledger = _section_yaml(_split_sections(markdown)[8])["provenance"]
    assert ledger["sources"] == [
        {"field": "company.name", "origin": "list_companies"}
    ]
    placeholder_map, _prompt = pipeline(markdown, template)
    assert set(placeholder_map["_provenance"]) == {
        "from_kg", "derived", "templated_defaults",
    }


def test_an_e7a_reason_for_a_roster_field_passes_through_verbatim(template):
    """Where E7a already said why a paper did not state a figure, the document
    says the same thing rather than restating it as a class label. A reviewer
    reads "no table in the paper is a scenario impact table", not "the provider
    can source this field"."""
    packet = assemble("OPP-TEST", "A paper stating no table and no chart at all.")
    stated = dict(packet.missing_fields)
    markdown = build(REQUEST, packet=packet)
    reasons = {
        gap["field"]: gap["reason"]
        for gap in _section_yaml(_split_sections(markdown)[8])["provenance"]["gaps"]
    }
    # An exact roster path, and a roster name that is the prefix of three leaves.
    assert reasons["commercial.scenarios[].margin_gain_pp"] == stated[
        "commercial.scenarios[].margin_gain_pp"
    ]
    assert reasons["timeline.phases[].label"] == stated["timeline.phases"]
    assert reasons["baseline.revenue_ttm_usd"] == stated["baseline.revenue_ttm_usd"]
    # And it really is a pass-through, not the SOURCED class reason.
    assert reasons["baseline.revenue_ttm_usd"] != REASONS[SOURCED]
    # A field E7a never spoke about keeps its own class reason.
    assert reasons["company.hq"] == REASONS[UNSOURCEABLE]
    pipeline(markdown, template)


def test_a_filled_slot_leaves_the_gaps_and_enters_the_ledger(template):
    """Layer 2 is wired, and the two-places rule holds in both directions: a path
    the fill map carries renders its value, drops out of `gaps`, and appears in
    the ledger under its origin class. The fill map is empty in this window; this
    is what E9c fills."""
    fill = {
        "company.name": "Test Subject One",
        "next_steps": [{
            "number": "01", "week": "WK 0", "owner": "CLIENT",
            "title": "Approve", "body": "Leadership approves the plan.",
        }],
    }
    markdown = build(REQUEST, fill=fill, templated_fallbacks=("next_steps",))
    placeholder_map, prompt = pipeline(markdown, template)
    gaps = set(placeholder_map["_gaps"])
    assert _document_absent_paths(markdown) == gaps
    assert "company.name" not in gaps
    assert "next_steps[].title" not in gaps
    ledger = _section_yaml(_split_sections(markdown)[8])["provenance"]
    assert ledger["from_kg"] == ["company.name"]
    # `next_steps` is SOURCED in the slot table since E11 Stage 2c and TEMPLATED
    # on a run that took the deck standard, which this one declares it did.
    assert "next_steps[].title" in ledger["templated_defaults"]
    paper_sourced = build(REQUEST, fill=fill)
    paper_ledger = _section_yaml(_split_sections(paper_sourced)[8])["provenance"]
    assert "next_steps[].title" in paper_ledger["from_kg"]
    assert paper_ledger["templated_defaults"] == []
    assert "Test Subject One" in prompt
    assert MISSING_MARKER.format(role="client_full") not in prompt
    assert MISSING_MARKER.format(role="action_items") not in prompt


def test_a_partial_record_still_names_its_unfilled_leaves_in_the_gaps(template):
    """Absence is judged leaf by leaf rather than container by container.

    E9b keyed a record leaf on its parent list, so a filled `target_metrics`
    took `target_metrics[].label` out of section 8 `gaps` while the document
    still carried no value for it. The fill map here is the shape E9c produces
    -- all four sourced record lists filled and every one of them partial -- and
    against the container-granularity version twelve leaves come out absent in
    the value and unnamed in the gaps, which is the one failure mode this
    module's docstring calls out. The single filled record E9b tested populated
    all five of its leaves, so the case never arose there.
    """
    fill = {
        "target_metrics": [{"value": "1.2-6.0pp"}],
        "build_summary.phases": [{"label": "Phase 1"}],
        "timeline.phases": [{"label": "Phase 1"}],
        "commercial.scenarios": [
            {"name": "CASE", "margin_gain_pp": 1.2, "direct_uplift_usd_yr": 1070000},
        ],
    }
    markdown = build(REQUEST, fill=fill)
    placeholder_map, _prompt = pipeline(markdown, template)
    assert _document_absent_paths(markdown) == set(placeholder_map["_gaps"])
    invisible = [
        path for path in (
            "target_metrics[].label",
            "build_summary.phases[].id", "build_summary.phases[].summary",
            "timeline.phases[].id",
            "timeline.phases[].workstreams[].name",
            "timeline.phases[].workstreams[].detail",
            "timeline.phases[].workstreams[].start_week",
            "timeline.phases[].workstreams[].end_week",
            "commercial.scenarios[].qofai_comp_usd",
            "commercial.scenarios[].qofai_comp_note",
            "commercial.scenarios[].client_retained_ebitda_usd",
            "commercial.scenarios[].enterprise_value_at_exit_usd",
        ) if path not in placeholder_map["_gaps"]
    ]
    assert invisible == []
    # And the leaves the records do carry really did leave the gaps.
    for path in (
        "target_metrics[].value", "build_summary.phases[].label",
        "timeline.phases[].label", "commercial.scenarios[].name",
    ):
        assert path not in placeholder_map["_gaps"]


def test_the_copy_hook_carries_prose_the_yaml_channel_cannot(template):
    """The three hooks added 2026-08-15, and the reason the first one exists.

    `map_packet` reads the cover bullets and the `Headline:` / `Subhead:` lines
    out of a section's PROSE, so a document emitting yaml alone leaves every copy
    role empty however full the fill map is. Section 3's payload and section 8's
    `role_coverage` are the other two: the first is the contract's home for
    analytical source data, the second is reported and never gated, which is why
    `_provenance` still resolves to exactly three keys upstream.
    """
    markdown = build(
        REQUEST,
        copy={1: ["- **Title:** `A Test Project`"]},
        # Slide 2's two copy lines moved out of the section's prose and into its
        # own entry on 2026-09-13, because a section that repeats cannot pair N
        # prose blocks with N yaml entries. The cover's still come through the
        # prose channel, which is what this test is really about.
        sections=[{"fill": {}, "copy": {"headline": "A Test Headline"}}],
        opportunities=[{"id": "OPP-TEST", "description": "Parked for F1."}],
        role_coverage=0.22,
    )
    placeholder_map, _prompt = pipeline(markdown, template)
    assert placeholder_map["project_title"] == "A Test Project"
    assert placeholder_map["opportunity_headline"] == "A Test Headline"
    section_3 = _section_yaml(_split_sections(markdown)[3])
    assert section_3["opportunities"][0]["description"] == "Parked for F1."
    ledger = _section_yaml(_split_sections(markdown)[8])["provenance"]
    assert ledger["role_coverage"] == 0.22
    assert set(placeholder_map["_provenance"]) == {
        "from_kg", "derived", "templated_defaults",
    }
    # Reported, never gated: the frontmatter the gates read is unchanged.
    assert "role_coverage" not in markdown.split("---")[1]


def test_no_leaf_is_added_to_the_rendered_sections_that_the_fixtures_do_not_emit():
    """A new leaf in sections 1 through 7 is a `CoverageError` whose fix would be
    an edit to `coverage_guard.py`, which is out of bounds. So every slot path is
    checked against the guard's own declarations rather than against the fixture
    files, which is the same set and does not depend on a fixture staying put."""
    from coverage_guard import EXCLUSION_ALLOWLIST

    stray = sorted(
        path for path, section, _kind, _origin in SLOTS
        if section in (1, 2, 4, 5, 6, 7)
        and path not in COVERAGE_MAP
        and path not in EXCLUSION_ALLOWLIST
    )
    assert stray == []


def test_a_null_block_would_fail_the_guard_which_is_why_absence_is_typed(template):
    """The premise this window was told to verify rather than inherit, pinned so
    it cannot silently stop being true. `today_pain_points: null` is an
    unaccounted leaf at its own path; `today_pain_points: []` is the slotted
    `today_pain_points[]`."""
    markdown = build(REQUEST)
    broken = markdown.replace("today_pain_points: []", "today_pain_points: null")
    assert broken != markdown
    placeholder_map = map_packet(broken, REQUEST)
    prompt = assemble_prompt(template, placeholder_map)
    with pytest.raises(CoverageError, match="today_pain_points"):
        check_coverage(broken, template, placeholder_map, prompt)


# ---------------------------------------------------------------------------
# ITEM 15: section 2 is a list of per-opportunity entries.
# ---------------------------------------------------------------------------

def test_section_two_is_always_a_list_even_for_one_opportunity():
    """Two shapes for one thing is a branch, and this repo has declined that
    everywhere else. The cost is that the packet markdown moved once; the DECK
    for a single-opportunity run did not move at all."""
    section_2 = _section_yaml(_split_sections(build(REQUEST))[2])
    assert isinstance(section_2[packet_document.OPPORTUNITY_SECTIONS], list)
    assert len(section_2[packet_document.OPPORTUNITY_SECTIONS]) == 1


def test_each_opportunity_carries_its_own_values_and_its_own_copy():
    markdown = build(REQUEST, sections=[
        {"fill": {"today_pain_points[]": ["first pain"]},
         "copy": {"headline": "FIRST", "one_liner": "The first story."}},
        {"fill": {"today_pain_points[]": ["second pain"]},
         "copy": {"headline": "SECOND"}},
    ])
    entries = _section_yaml(_split_sections(markdown)[2])[
        packet_document.OPPORTUNITY_SECTIONS]

    assert [entry["headline"] for entry in entries] == ["FIRST", "SECOND"]
    assert entries[0]["one_liner"] == "The first story."
    assert "one_liner" not in entries[1]
    assert entries[0]["today_pain_points"] == ["first pain"]
    assert entries[1]["today_pain_points"] == ["second pain"]


def test_the_fill_map_stays_flat_and_per_opportunity():
    """Which is what keeps `packet_fill` and `second_pass` from having to know a
    deck can carry more than one: both read and write that flat map, and nesting
    it would have reached into both."""
    markdown = build(REQUEST, sections=[
        {"fill": {"today_pain_points[]": ["only pain"]}, "copy": {}},
    ])
    entry = _section_yaml(_split_sections(markdown)[2])[
        packet_document.OPPORTUNITY_SECTIONS][0]
    assert entry["today_pain_points"] == ["only pain"]


def test_a_gap_in_the_second_opportunity_is_still_a_gap():
    """Section 8's gaps are one list for one document, so a section 2 path is
    judged against EVERY opportunity. Which one is missing what is a question
    the deck answers per slide, from the placeholder map."""
    absent = packet_document._absent_paths({}, [
        {"fill": {"today_pain_points[]": ["stated"]}},
        {"fill": {}},
    ])
    assert "today_pain_points[]" in absent

    answered = packet_document._absent_paths({}, [
        {"fill": {"today_pain_points[]": ["stated"]}},
        {"fill": {"today_pain_points[]": ["also stated"]}},
    ])
    assert "today_pain_points[]" not in answered


def test_the_reader_parses_a_key_with_no_value_on_a_dash_line():
    """The gap the container exposed, and it could not have fired before:
    no emitted sequence item had ever begun with a block key. The old path
    parsed the child lines as a sequence, discarded them for not being a dict,
    and stopped -- the first key came back null and every sibling vanished."""
    from data_source_adapter import _parse_packet_yaml

    parsed = _parse_packet_yaml(
        "opportunities:\n"
        "  - today_pain_points:\n"
        '      - "one"\n'
        '      - "two"\n'
        "    target_capabilities: []\n"
        "    build_summary:\n"
        "      duration_weeks: null\n"
    )
    entry = parsed["opportunities"][0]
    assert entry["today_pain_points"] == ["one", "two"]
    assert entry["target_capabilities"] == []
    assert entry["build_summary"] == {"duration_weeks": None}


def test_the_walker_strips_the_container_so_the_rest_stays_flat():
    """One place on the write side knows about the container and one on the read
    side. The coverage map, section 8's gaps, `ROLE_SOURCE_PATHS` and the fill
    map all keep the §2 vocabulary they have always had."""
    from coverage_guard import rendered_leaf_paths

    markdown = build(REQUEST, sections=[
        {"fill": {"today_pain_points[]": ["a"]}, "copy": {}},
        {"fill": {"today_pain_points[]": ["b"]}, "copy": {}},
    ])
    paths = rendered_leaf_paths(markdown)

    assert "today_pain_points[]" in paths
    assert not any(path.startswith("opportunities[]") for path in paths)
