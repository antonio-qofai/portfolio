"""Tests for the fill layer (E9c).

Everything here runs a real committed excerpt under `data-provider/fixtures/`
through the real `packet_assembly`, the real `packet_document`, and then the
real `map_packet` / `prompt_assembler` / `coverage_guard`. Nothing is asserted
against a hand-written idea of what the fill map should hold, because the claim
this window carries is not "the map looks right" but "the roles that render carry
platform data and the roles that do not say so on the deck".

The company record, the request and the opportunity detail are placeholders
chosen here, and that is the point: no client name, no project name and no
figure is written into `src/`. Two of them exist so the anti-hardcoding check has
two clients to compare.

`ebitda_impact` is supplied by the test rather than read off a fixture because
the capture rule scrubbed it: every excerpt's `_fixture.fields_the_capture_did_
not_include` names `title`, `description` and `ebitda_impact`. The paper text
beside it is the real captured thing.
"""

import json
import pathlib
import re

import pytest

import packet_fill
from coverage_guard import COVERAGE_MAP, check_coverage
from data_source_adapter import _section_yaml, _split_sections, map_packet
from packet_assembly import assemble
from packet_document import ORIGINS, REVIEWER, SOURCED, TEMPLATED, build
from packet_fill import UNIT_MISMATCH, fill_map, record_unit_mismatch
from prompt_assembler import MISSING_MARKER, assemble_prompt
from template_loader import load_template

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "data-provider" / "fixtures"
TEMPLATE_PATH = "templates/proposal-template.md"

# Two placeholder clients, so "no value crosses between two runs" is a real
# comparison rather than a claim about one document.
CLIENTS = {
    "one": {
        "shape": "scenario-rows-canonical",
        "company": {"id": "company-one", "name": "Test Subject One", "has_kg": True},
        "request": {
            "intent": "generate_project_planning_proposal",
            "company": "Test Subject One",
            "project": "First Test Project",
            "pe_firm": "First Test Capital",
            "proposal_date": "2026-08-15",
        },
        "project": {"id": "project-one", "name": "First Test Project"},
        "opportunity": {
            "id": "OPP-ONE",
            "title": "First Test Opportunity",
            "description": "A first placeholder description, the roughly 1KB "
                           "prose block get_opportunity_details returns.",
            "ebitda_impact": {"min": 1.2, "max": 6.0, "unit": "pp"},
        },
    },
    "two": {
        "shape": "scenario-columns",
        "company": {"id": "company-two", "name": "Test Subject Two", "has_kg": True},
        "request": {
            "intent": "generate_project_planning_proposal",
            "company": "Test Subject Two",
            "project": "Second Test Project",
            "pe_firm": "Second Test Capital",
            "proposal_date": "2026-08-15",
        },
        "project": {"id": "project-two", "name": "Second Test Project"},
        "opportunity": {
            "id": "OPP-TWO",
            "title": "Second Test Opportunity",
            "description": "A second placeholder description, distinct from the "
                           "first so nothing can cross between the two runs.",
            "ebitda_impact": {"min": 0.8, "max": 3.4, "unit": "pp"},
        },
    },
}


@pytest.fixture
def template():
    return load_template(TEMPLATE_PATH)


def paper(shape):
    path = FIXTURES / f"paper-excerpt-{shape}.json"
    return json.loads(path.read_text())["opportunity"]["research_paper_natural"]


def document(client, coverage=None):
    """One client's whole document, built the way the seam will build it.

    `coverage` is the section 8 role-coverage number, which only a first pass
    through `map_packet` can measure. Section 8 feeds no role, so the second
    build differs from the first in that number alone.
    """
    packet = assemble(client["opportunity"]["id"], paper(client["shape"]))
    fill, sources = fill_map(
        company=client["company"],
        request=client["request"],
        opportunity=client["opportunity"],
        packet=packet,
    )
    # The pipeline's own last fill step since E11 Stage 2c: `platform_layers` and
    # `next_steps` are held out of `fill_map` so the second pass is told they are
    # absent, and the deck standard goes back on whichever one the paper did not
    # carry. No second pass runs on this harness, so both fall back here.
    fill, sources, fell_back = packet_fill.apply_templated_defaults(fill, sources)
    return build(
        client["request"], record_unit_mismatch(packet), fill, sources,
        {"generated_at": "2026-08-15T00:00:00Z", "company_id": client["company"]["id"]},
        templated_fallbacks=fell_back,
        derived=packet_fill.DERIVED_PATHS,
        copy=packet_fill.copy_lines(
            company=client["company"],
            request=client["request"],
            project=client["project"],
            opportunity=client["opportunity"],
        ),
        opportunities=packet_fill.opportunity_record(client["opportunity"]),
        # Section 2 repeats (item 15). One entry here, carrying this
        # opportunity's own fill map and its own two copy lines.
        sections=[{
            "fill": fill,
            "copy": packet_fill.opportunity_copy(client["opportunity"]),
        }],
        role_coverage=coverage,
    ), fill


def rendered(client, template):
    """The document, its fill map, its placeholder map and its prompt.

    Two passes, because the section 8 role-coverage number is measured on the
    first one. The guard runs on the document that is actually returned.
    """
    markdown, _fill = document(client)
    coverage = packet_fill.role_coverage(map_packet(markdown, client["request"]))
    markdown, fill = document(client, coverage=coverage)
    placeholder_map = map_packet(markdown, client["request"])
    prompt = assemble_prompt(template, placeholder_map)
    check_coverage(markdown, template, placeholder_map, prompt)
    return markdown, fill, placeholder_map, prompt


def classify(placeholder_map, prompt):
    """Each of the 18 render roles as sourced / templated / reviewer / absent.

    Read off the origin class of the role's own COVERAGE_MAP paths and off
    whether the assembled prompt carries a value for it, so the split is measured
    on what the deck receives rather than on what the fill map intended. A role
    whose every path is a deck standard or reviewer input is that, whether or not
    it renders; anything else that renders carries data, and anything else that
    does not is absent.
    """
    split = {}
    provenance = placeholder_map.get("_provenance") or {}
    from_kg = set(provenance.get("from_kg") or ()) | set(
        provenance.get("derived") or ()
    )
    templated_now = set(provenance.get("templated_defaults") or ())
    for role in sorted(set(COVERAGE_MAP.values())):
        paths = [path for path, named in COVERAGE_MAP.items() if named == role]
        # An OPTIONAL role with no value emits no marker and no line, so the
        # absence of a marker alone would count it as rendering. It renders only
        # when it carries a value (2026-09-23, the optional commercial blocks).
        renders = (MISSING_MARKER.format(role=role) not in prompt
                   and placeholder_map.get(role) not in (None, "", [], {}))
        # Read off the document's own section 8 rather than the static table,
        # for the two roles whose class is a fact about the RUN since E11 Stage
        # 2c: `components` and `action_items` are the paper's where the paper
        # carried them and the deck standard where it did not.
        if any(path in templated_now for path in paths) and not any(
            path in from_kg for path in paths
        ):
            split[role] = "templated"
        elif {ORIGINS[path] for path in paths} == {REVIEWER}:
            split[role] = "reviewer"
        else:
            split[role] = "sourced" if renders else "absent"
    return split


def test_the_filled_document_runs_the_whole_pipeline(template):
    """A document built from a committed excerpt plus a resolved company record
    passes the coverage guard with every record list this layer fills partial,
    which is the case E9b's empty fill map never reached."""
    rendered(CLIENTS["one"], template)


def test_the_eighteen_roles_split_ten_sourced_two_templated_two_reviewer_four_absent(
    template,
):
    """The count, measured on the deck rather than asserted.

    Five sourced before E11 Stage 1, eight after it, nine after Stage 2c: slide
    4's three roles moved off the chart `chart_timeline_parser` was already
    reading, and `build_summary` moved off the same chart's own last phase
    boundary. Ten after the slide-2 pass of 2026-08-19, which gave
    `today_metric_1` the baseline EBITDA margin.

    THE COMMERCIAL SLIDE, 2026-09-23 (`build-plan-commercial-slide.md`). Still
    eighteen roles: three retired (`client_retention`, `downside_protection`,
    `payment_mechanics`) and three added (`investment_rows`, `return_rows`,
    `terms_rows`). Still ten sourced, because `return_rows` now carries the
    scenario figures `value_mapping` used to, and `value_mapping` is left with
    only the chart's three reviewer figures. A paper states no §11.2 cost table
    and no deal terms, so `investment_rows` and `terms_rows` are absent here.

    Reds if this layer fills a role it has no source for (absent drops below
    four) or drops one it does (sourced falls below ten).
    """
    _markdown, _fill, placeholder_map, prompt = rendered(CLIENTS["one"], template)
    split = classify(placeholder_map, prompt)
    counted = {state: sum(1 for s in split.values() if s == state) for state in
               ("sourced", "templated", "reviewer", "absent")}
    assert len(split) == 18
    assert counted == {"sourced": 10, "templated": 2, "reviewer": 2, "absent": 4}
    assert split["after_metric_1"] == "sourced"
    assert split["today_metric_1"] == "sourced"
    assert split["return_rows"] == "sourced"
    assert split["value_mapping"] == "reviewer"
    for role in ("timeline_columns", "timeline_rows", "milestones"):
        assert split[role] == "sourced", role
    # The four, named, so a change to which role is absent reds here rather
    # than passing on the count alone. Two are slide 2 bullet lists, whose prose
    # is the second pass's to read; two are the commercial blocks a paper never
    # states.
    assert sorted(r for r, s in split.items() if s == "absent") == [
        "after_capability_bullets", "investment_rows", "terms_rows",
        "today_pain_bullets",
    ]
    assert split["build_summary"] == "sourced"


def test_every_absent_path_is_named_in_the_gaps_with_all_four_lists_filled(template):
    """E9b's set equality, measured on a document whose four sourced record lists
    are all filled and all partial. An empty document proves nothing here: the
    container-granularity defect only appears once something is filled."""
    markdown, _fill, placeholder_map, _prompt = rendered(CLIENTS["one"], template)
    gaps = set(placeholder_map["_gaps"])
    for path in (
        "target_metrics[].label", "build_summary.phases[].id",
        "build_summary.phases[].summary", "timeline.phases[].id",
        "timeline.phases[].workstreams[].name",
        "timeline.phases[].workstreams[].end_week",
        "commercial.scenarios[].qofai_comp_usd",
        "commercial.scenarios[].enterprise_value_at_exit_usd",
    ):
        assert path in gaps
    for path in (
        "company.name", "company.pe_firm", "baseline.revenue_ttm_usd",
        "target_metrics[].value", "timeline.phases[].label",
        "commercial.scenarios[].name", "next_steps[].title",
    ):
        assert path not in gaps


def test_the_week_denominated_fields_say_why_rather_than_saying_no_source(template):
    """Every schedule field in the contract is week-denominated, so those five
    absences carry the unit mismatch as their reason instead of the generic
    no-source line.

    The reason no longer over-generalises. Verified 2026-08-15 and again
    2026-08-18: seven of the eight committed excerpts carry a timeline and all
    seven are month-denominated. That is true of the FIXTURES and false of the
    corpus, which carries week-denominated plans too (E11's 12-paper survey), so
    the reason says the plan renders in whatever unit the chart states rather
    than claiming the corpus speaks one unit.
    """
    markdown, _fill, _placeholder_map, _prompt = rendered(CLIENTS["one"], template)
    reasons = {
        gap["field"]: gap["reason"]
        for gap in _section_yaml(_split_sections(markdown)[8])["provenance"]["gaps"]
    }
    for path in (
        "timeline.week_buckets[]", "timeline.total_weeks",
        "build_summary.duration_weeks",
        "timeline.phases[].workstreams[].start_week",
        "timeline.phases[].workstreams[].end_week",
    ):
        assert reasons[path] == UNIT_MISMATCH
    stated = reasons["timeline.total_weeks"]
    assert "month-denominated" in stated and "week-denominated" in stated
    assert "every timeline" not in stated and "the corpus states" not in stated
    # The label and the span that did cross keep their own values, not a gap.
    for path in ("timeline.phases[].label", "timeline.phases[].start",
                 "timeline.phases[].end", "timeline.phases[].unit",
                 "timeline.columns[].label", "timeline.milestones[].position"):
        assert path not in reasons, path


def test_the_metric_label_stays_absent_from_the_packet_and_the_deck_captions_it(
    template,
):
    """Antonio's 2026-08-15 ruling, and what 2026-08-19 changed about it.

    The ruling is about the PACKET and it stands, untouched: the value is real
    platform data, the label is a unit-naming string no tool returns, so
    `target_metrics[].label` is still unfilled and still carries its gap.

    What the ruling left on the deck was a 24px `1.2–6.0pp` with no caption at
    all, beside a TODAY panel whose figures had captions. Antonio, 2026-08-19:
    "0.6-1.3pp means nothing if we dont know what the metric is." So the DECK now
    captions it, in `data_source_adapter` where the rest of slide 2's composed
    copy already lives (`after_horizon`, the build band's title). The caption
    names the field the figure came from rather than making any claim about the
    paper, which is why it needs no span, and it is applied only where section 8
    records that field as the source.
    """
    _markdown, fill, placeholder_map, prompt = rendered(CLIENTS["one"], template)
    # The packet half of the ruling, unchanged.
    assert fill["target_metrics"] == [{"value": "1.2–6.0pp"}]
    assert "target_metrics[].label" in placeholder_map["_gaps"]
    # The deck half.
    assert placeholder_map["after_metric_1"] == "1.2–6.0pp · EBITDA margin impact"
    assert MISSING_MARKER.format(role="after_metric_1") not in prompt


def test_a_dollar_range_the_paper_stated_reaches_the_deck_as_stated(template):
    """E11 Stage 2e. Two committed excerpts state their scenario uplift as a
    range, and until this stage the fill layer discarded all six of those
    figures: `_one` returned None for any pair whose endpoints differ, so slide
    5's whole EBITDA-gain column rendered blank on those two papers while the
    packet held a correct, sourced, span-verified range for every case.

    Both endpoints now cross, on the deck's own en dash, in the form
    `_target_metrics` already writes. No endpoint is picked and no arithmetic is
    done: `575000–862000` is exactly what `_money` read off the paper."""
    client = dict(CLIENTS["two"], shape="scenario-rows-multi-table")
    _markdown, fill, placeholder_map, prompt = rendered(client, template)
    assert fill["commercial.scenarios"] == [
        {"name": "Conservative", "direct_uplift_usd_yr": "575000–862000"},
        {"name": "Moderate", "direct_uplift_usd_yr": "1150000–1590000"},
        {"name": "Aggressive", "direct_uplift_usd_yr": "1720000–2160000"},
    ]
    # The gap is gone because the field is no longer absent, and the deck shows
    # it in the house style the template's own example states (`$1.5M–$2.6M`).
    assert "commercial.scenarios[].direct_uplift_usd_yr" not in placeholder_map["_gaps"]
    # On the RETURN table since 2026-09-23, where slide 5's scenario figures live.
    assert [row["annual_ebitda"] for row in placeholder_map["return_rows"]] == [
        "$575,000–$862,000/yr", "$1,150,000–$1,590,000/yr",
        "$1,720,000–$2,160,000/yr",
    ]
    assert MISSING_MARKER.format(role="return_rows") not in prompt


def test_a_single_stated_figure_is_still_a_number_and_not_a_range(template):
    """The other five committed excerpts state one figure per cell, and the
    range-aware sibling returns for them exactly what `_one` returned: a number,
    not a one-endpoint display string. Nothing that rendered before moves."""
    _markdown, fill, placeholder_map, _prompt = rendered(CLIENTS["one"], template)
    assert fill["commercial.scenarios"] == [
        {"name": "Conservative", "margin_gain_pp": 1.2,
         "direct_uplift_usd_yr": 1070000},
        {"name": "Base Case", "margin_gain_pp": 3, "direct_uplift_usd_yr": 2690000},
        {"name": "Optimistic", "margin_gain_pp": 6, "direct_uplift_usd_yr": 5370000},
    ]
    assert [(row["margin"], row["annual_ebitda"])
            for row in placeholder_map["return_rows"]] == [
        ("+1.2pp", "$1,070,000/yr"), ("+3pp", "$2,690,000/yr"),
        ("+6pp", "$5,370,000/yr"),
    ]


def test_neither_endpoint_of_a_range_is_chosen_rounded_or_reordered():
    """The four rules this stage is bound by, on the function itself.

    A range is two numbers and both are carried or neither is; no endpoint's
    precision is normalised onto the other's; the pair's ORDER is the reader's,
    since `read_pp` and `_money` both return `(min, max)` and Stage 2d owns
    that; and no unit is written here, because each consumer names its own once.
    """
    assert packet_fill._stated((1.9, 1.9)) == 1.9
    assert packet_fill._stated((6.0, 6.0)) == 6
    assert packet_fill._stated((0.9, 1.9)) == "0.9–1.9"
    # 1.2 keeps its decimal and 6.0 loses its trailing zero, exactly as each
    # would alone: re-rounding one half to match the other is the arithmetic
    # this refuses.
    assert packet_fill._stated((1.2, 6.0)) == "1.2–6"
    assert packet_fill._stated((575000.0, 862000.0)) == "575000–862000"
    for pair in ((0.9, 1.9), (1.2, 6.0), (575000.0, 862000.0)):
        stated = packet_fill._stated(pair)
        assert "pp" not in stated and "$" not in stated and "%" not in stated
        assert stated.count("–") == 1


def test_the_single_number_slots_keep_their_single_number_contract():
    """`_stated` is a sibling rather than a replacement. `_one` is unchanged and
    still guards every slot that genuinely cannot show two numbers, which is the
    Stage 2e scope line: whether a baseline figure should print as a range is a
    deck-design question, and this window measures it rather than deciding it."""
    assert packet_fill._one((1.9, 1.9)) == 1.9
    assert packet_fill._one((6.0, 6.0)) == 6
    assert packet_fill._one((0.9, 1.9)) is None
    assert packet_fill._one((575000.0, 862000.0)) is None


def test_only_two_templated_defaults_are_supplied_and_never_the_comp_schedule():
    """A new deck standard is a decision for a human, and so is retiring one.
    The two carry no client, no source system and no duration.

    `commercial.comp_schedule` is no longer applied (2026-09-23). It was the
    retired FBK performance schedule, templated into every packet unseen, and
    the adaptive commercial slide prints a packet's own schedule as a TERMS row,
    so templating it would put the retired schedule on every deck. The contract
    still DECLARES it templatable (`packet_document.TEMPLATED_DEFAULTS`), which
    is permission, not obligation."""
    assert [path for path, _records in packet_fill.TEMPLATED] == [
        "platform_layers", "next_steps",
    ]
    # Both are FALLBACKS since E11 Stage 2c: held out of `fill_map` so the
    # second pass sees them absent and reads the paper for them, and put back
    # afterwards only where the paper carried nothing.
    fill, sources = fill_map()
    assert set(fill) == set()
    fill, sources, fell_back = packet_fill.apply_templated_defaults(fill, sources)
    assert set(fill) == {"platform_layers", "next_steps"}
    assert fell_back == ("platform_layers", "next_steps")
    assert set(sources.values()) == {packet_fill.CONTRACT}

    # A paper that carried them is not overwritten, and does not report a
    # fallback it did not take.
    carried = {"platform_layers": [{"number": "01", "title": "A layer"}]}
    kept, _sources, fell_back = packet_fill.apply_templated_defaults(carried, {})
    assert kept["platform_layers"] == carried["platform_layers"]
    assert fell_back == ("next_steps",)


def test_no_value_crosses_between_two_clients(template):
    """The anti-hardcoding check. Two clients, two excerpts, two documents, and
    nothing of one appears in the other except the deck standards, which are
    supposed to be identical."""
    first, _f1, map_one, _p1 = rendered(CLIENTS["one"], template)
    second, _f2, map_two, _p2 = rendered(CLIENTS["two"], template)
    for source, other in ((first, second), (second, first)):
        mapped = map_packet(source, {})
        # The scenario NAMES are deliberately left out: `Conservative` and
        # `Base Case` are the papers' own standard case labels and recur across
        # companies, so their coinciding is the corpus rather than a leak. The
        # figures beside them are what one client's paper stated.
        distinctive = {
            mapped["client_full"], mapped["pe_firm"], mapped["after_metric_1"],
            # The copy channel too, which carries the cover and the headline.
            mapped["prepared_for"], mapped["project_title"],
            mapped["opportunity_headline"],
        } | {row["annual_ebitda"] for row in mapped["return_rows"]}
        assert len(distinctive) > 6
        for value in distinctive:
            assert value and value not in other
    assert map_one["components"] == map_two["components"]
    assert map_one["action_items"] == map_two["action_items"]


def test_the_completeness_ratio_stays_over_the_six_field_roster(template):
    """The denominator does not move. Adding the full packet shape would land a
    live packet near 0.15, fail the 0.70 gate and render no deck at all; counting
    templated or reviewer fields as present would inflate it on data no client
    supplied."""
    markdown, _fill, _placeholder_map, _prompt = rendered(CLIENTS["one"], template)
    assert "data_completeness: 1.0" in markdown.split("---")[1]
    assert "role" not in markdown.split("---")[1]


def test_recording_the_unit_mismatch_leaves_e7as_packet_untouched():
    """E7a's packet is frozen by design, so the reasons this layer knows are
    added to a copy. The roster, the figures and the score are unchanged."""
    packet = assemble("OPP-ONE", paper("scenario-rows-canonical"))
    noted = record_unit_mismatch(packet)
    assert noted is not packet
    assert packet.missing_fields == ()
    assert noted.fields == packet.fields and noted.present == packet.present
    assert dict(noted.missing_fields)["timeline.total_weeks"] == UNIT_MISMATCH


def test_a_caller_with_nothing_to_hand_fills_nothing_it_cannot_source():
    """Every argument is optional and absence is never defaulted around: with no
    company, no request, no opportunity and no packet, only the deck standards
    enter the map."""
    fill, _sources = fill_map(company=None, request=None, opportunity=None, packet=None)
    assert "company.name" not in fill and "target_metrics" not in fill
    assert record_unit_mismatch(None) is None


def test_the_cover_carries_the_client_the_firm_and_the_project(template):
    """The measurement that widened this window: before the copy channel the
    live deck's cover had no title and no client name on it at all. Each bullet
    is composed from a record that was already resolved, on the deck's own
    separator, and `deck_type_label` still derives back out of the eyebrow."""
    _markdown, _fill, placeholder_map, prompt = rendered(CLIENTS["one"], template)
    assert placeholder_map["prepared_for"] == "TEST SUBJECT ONE · FIRST TEST CAPITAL"
    assert placeholder_map["project_title"] == "First Test Project"
    assert placeholder_map["deck_kicker"] == "PROJECT PLANNING · AUGUST 15 2026"
    assert placeholder_map["deck_type_label"] == "PROJECT PLANNING"
    for role in ("prepared_for", "project_title", "deck_kicker"):
        assert MISSING_MARKER.format(role=role) not in prompt


def test_the_opportunity_headline_carries_the_real_title_verbatim(template):
    """`get_opportunity_details.title` is real platform data and goes across
    untouched. The subhead beside it stays absent: it comes from the roughly 1KB
    description, and compressing that is F1's job under a diff guard."""
    _markdown, _fill, placeholder_map, prompt = rendered(CLIENTS["one"], template)
    assert placeholder_map["opportunity_headline"] == "First Test Opportunity"
    assert placeholder_map["subtitle"] == ""
    assert placeholder_map["opportunity_summary"] == ""
    assert MISSING_MARKER.format(role="opportunity_headline") not in prompt
    assert MISSING_MARKER.format(role="subtitle") in prompt


def test_no_framing_constant_says_anything_about_the_engagement(template):
    """The line a deck standard may not cross. A framing line names what its own
    section contains; it may not assert a client, a figure, a schedule or an
    outcome. Checked mechanically: every constant is identical across two
    different clients and carries no digit, so none of them can be quoting this
    engagement's numbers or its horizon."""
    _md_one, _f1, map_one, _p1 = rendered(CLIENTS["one"], template)
    _md_two, _f2, map_two, _p2 = rendered(CLIENTS["two"], template)
    framing = (
        packet_fill.PLATFORM_HEADLINE, packet_fill.PLATFORM_SUMMARY,
        packet_fill.PLAN_HEADLINE, packet_fill.TERMS_HEADLINE,
        packet_fill.TERMS_SUMMARY, packet_fill.NEXT_STEPS_HEADLINE,
        packet_fill.NEXT_STEPS_SUMMARY,
    )
    for constant in framing:
        assert not any(character.isdigit() for character in constant)
    for role in ("platform_headline", "platform_summary", "plan_headline",
                 "terms_headline", "terms_summary", "next_steps_headline",
                 "next_steps_summary"):
        assert map_one[role] and map_one[role] == map_two[role]


def test_the_description_is_parked_in_section_three_untouched(template):
    """Section 3 is the contract's home for analytical source data, parsed by
    nothing on the proposal path and outside the coverage walk, so F1 gets the
    description verbatim without this layer compressing a word of it."""
    markdown, _fill, _placeholder_map, _prompt = rendered(CLIENTS["one"], template)
    parked = _section_yaml(_split_sections(markdown)[3])["opportunities"]
    assert parked[0]["description"] == CLIENTS["one"]["opportunity"]["description"]
    assert parked[0]["title"] == CLIENTS["one"]["opportunity"]["title"]


def test_section_eight_reports_role_coverage_and_no_gate_reads_it(template):
    """The second figure the design asked for: 8 of the 18 render roles carry
    real platform data (4 before E11 Stage 1, 7 after it), measured on what
    `map_packet` produced rather than on the fill map. `build_summary` is the
    eighth and it is Stage 2c's: it held E4's real phase label and rendered
    nothing, because the only horizon the deck could read was week-denominated
    and this corpus states months. `data_completeness` is untouched and stays the
    only number a gate reads."""
    markdown, _fill, placeholder_map, _prompt = rendered(CLIENTS["one"], template)
    ledger = _section_yaml(_split_sections(markdown)[8])["provenance"]
    assert ledger["role_coverage"] == 0.44
    assert packet_fill.role_coverage(placeholder_map) == 0.44
    frontmatter = markdown.split("---")[1]
    assert "role_coverage" not in frontmatter and "data_completeness: 1.0" in frontmatter


def test_the_copy_channel_leaves_six_roles_missing_and_every_one_has_a_reason(template):
    """The number that matters now the governed split is settled. 32 distinct
    roles rendered `[MISSING: ...]` before the copy channel, 11 of them governed
    and 21 copy; 21 rendered it after, 11 governed and 10 copy; 18 render it once
    E11 Stage 1 filled slide 4, 8 governed and the same 10 copy; 16 once Stage 2c
    filled the horizon, 7 governed and 9 copy; 13 after the slide-2 pass of
    2026-08-19, 6 governed and 7 copy.

    That pass retired the whole "second element" reason, which had covered two of
    the nine and was never a real absence. `today_metric_2` is now filled from
    the baseline financials the packet already carried. `after_metric_2` is
    declared `optional`, because the AFTER block is filled from a single
    opportunity figure on every deck in the corpus, so a required second metric
    reported every deck as missing something it was never going to have. A marker
    has to mean a source exists and did not arrive; that one meant neither.

    The timeline pass of 2026-09-03 took the seventh off the list.
    `plan_summary` was the standing finding here: this layer wrote no section 5
    subhead, so `map_packet` composed the role by joining
    `build_summary.phases[].summary` on a space, and the deck carried either the
    marker or a run-on of the phase caveat notes. Section 5 now carries a
    `Subhead:` line like sections 4, 6 and 7, `PLAN_SUMMARY` is its deck
    standard, and `paper_writing`'s `copy.plan_summary` slot writes the
    engagement-specific line on a run with a writing pass.

    Each of the remaining six falls under one of two stated reasons: F1 owns it,
    or it is reviewer input.
    """
    _markdown, _fill, _placeholder_map, prompt = rendered(CLIENTS["one"], template)
    governed = set(COVERAGE_MAP.values())
    missing = set(re.findall(r"\[MISSING: ([a-z0-9_]+)\]", prompt))
    assert sorted(missing & governed) == sorted(
        role for role in governed
        if MISSING_MARKER.format(role=role) in prompt
    )
    # Four since the adaptive commercial slide (2026-09-23): the two slide 2
    # bullet lists, and TERMS and its footnote, which are reviewer input on a
    # deck whose source states no deal. The value chart no longer leaves four
    # reviewer markers behind, because it is not drawn without its figures.
    assert len(missing & governed) == 4
    reasons = {
        # F1's, both from the roughly 1KB description.
        "subtitle": "f1", "opportunity_summary": "f1",
    }
    assert sorted(missing - governed) == sorted(reasons)
    # The three that left, named: none is absent any more, and none was ever
    # a source that failed to arrive.
    assert "today_metric_2" not in missing
    assert "after_metric_2" not in missing
    # The third left for its own reason: it now has a home on this layer.
    assert "plan_summary" not in missing


def test_section_five_carries_a_subhead_like_the_other_framed_sections():
    """Closing the standing `plan_summary` finding (2026-09-03). Sections 4, 6
    and 7 have always carried both a `Headline:` and a `Subhead:` line; section
    5 carried only the headline, which is why `map_packet` had nothing to read
    for `plan_summary` and composed it by joining the phase caveat notes. The
    constant names what the slide contains and asserts nothing about the client,
    exactly like `PLATFORM_SUMMARY` and `TERMS_SUMMARY`."""
    lines = packet_fill.copy_lines()
    for section in (4, 5, 6):
        heads = [line for line in lines[section] if line.startswith("Headline:")]
        subs = [line for line in lines[section] if line.startswith("Subhead:")]
        assert len(heads) == 1 and len(subs) == 1, (section, lines[section])
    assert f"Subhead: *{packet_fill.PLAN_SUMMARY}*" in lines[5]
    # A framing constant names no company, no figure and no schedule.
    assert packet_fill.PLAN_SUMMARY == (
        "Each phase, its span, and the milestone that closes it."
    )


def test_a_written_plan_summary_replaces_the_deck_standard_subhead():
    """The same seam every other framed line has: a sentence the writing pass
    produced wins, and a run with no writing pass keeps the constant. That is
    what makes a refused sentence cost the deck nothing."""
    written = packet_fill.copy_lines(
        written={"copy.plan_summary": "Four phases over twelve months."}
    )
    assert "Subhead: *Four phases over twelve months.*" in written[5]
    assert packet_fill.PLAN_SUMMARY not in "\n".join(written[5])
    assert (f"Subhead: *{packet_fill.PLAN_SUMMARY}*"
            in packet_fill.copy_lines(written={})[5])


# ---------------------------------------------------------------------------
# E11 Stage 1 — slide 4, the phased plan, in the paper's own unit.
#
# The four independent breaks these cover, each verified in code on 2026-08-18
# before a line was changed: `_phases` dropped the span and the unit the parser
# had already produced; `map_packet` built `timeline_rows` by iterating the
# workstreams of phases that carry none, so it yielded zero rows; the column
# axis read `timeline.week_buckets`, which is UNSOURCEABLE and never written;
# and the milestones had three mapped paths, all three UNSOURCEABLE. Fixing any
# one alone changes nothing on the deck.
# ---------------------------------------------------------------------------

# Hand-built, not a capture. All eight committed excerpts are month-denominated,
# so nothing under `data-provider/fixtures/` exercises the week branch; the
# 12-paper corpus survey of 2026-08-18 found week-denominated plans in published
# papers. It lives here rather than beside the excerpts because the parser's
# census tests glob that directory and this file is evidence about the code, not
# about the corpus.
SYNTHETIC = (
    pathlib.Path(__file__).resolve().parent
    / "fixtures" / "timeline" / "paper-synthetic-weeks-timeline.json"
)

WEEKS_CLIENT = {
    "shape": None,
    "company": {"id": "company-weeks", "name": "Test Subject Weeks", "has_kg": True},
    "request": {
        "intent": "generate_project_planning_proposal",
        "company": "Test Subject Weeks",
        "project": "Week Denominated Test Project",
        "pe_firm": "Third Test Capital",
        "proposal_date": "2026-08-18",
    },
    "project": {"id": "project-weeks", "name": "Week Denominated Test Project"},
    "opportunity": {
        "id": "OPP-WEEKS",
        "title": "Third Test Opportunity",
        "description": "A third placeholder description.",
        "ebitda_impact": {"min": 2.0, "max": 2.0, "unit": "pp"},
    },
}


def synthetic_weeks_paper():
    return json.loads(SYNTHETIC.read_text())["opportunity"]["research_paper_natural"]


def rendered_from_paper(client, paper_text, template):
    """The same pipeline as `rendered`, over a paper supplied directly.

    `rendered` keys off a committed excerpt's shape name; the week-denominated
    plan is hand-built and deliberately not one of those, so this takes the
    paper itself and changes nothing else about the run.
    """
    packet = assemble(client["opportunity"]["id"], paper_text)
    fill, sources = fill_map(
        company=client["company"], request=client["request"],
        opportunity=client["opportunity"], packet=packet,
    )
    markdown = build(
        client["request"], record_unit_mismatch(packet), fill, sources,
        {"generated_at": "2026-08-18T00:00:00Z", "company_id": client["company"]["id"]},
        copy=packet_fill.copy_lines(
            company=client["company"], request=client["request"],
            project=client["project"], opportunity=client["opportunity"],
        ),
        opportunities=packet_fill.opportunity_record(client["opportunity"]),
    )
    placeholder_map = map_packet(markdown, client["request"])
    prompt = assemble_prompt(template, placeholder_map)
    check_coverage(markdown, template, placeholder_map, prompt)
    return markdown, fill, placeholder_map, prompt


def test_the_phase_span_and_its_unit_cross_into_the_packet_beside_the_label():
    """The first of the four breaks. `chart_timeline_parser` already returns
    `start`, `end` and `unit` per phase, with the config text as the span; the
    fill layer used to keep the label and drop the rest, which is why every
    downstream consumer had nothing but a name to render."""
    packet = assemble("OPP-ONE", paper(CLIENTS["one"]["shape"]))
    fill, sources = fill_map(packet=packet)
    assert fill["timeline.phases"] == [
        {"label": "Phase 1: Decision Rule Digitization (Months 1-3)",
         "start": 0, "end": 3, "unit": "months"},
        {"label": "Phase 2: Optimization Engine Deployment (Months 4-6)",
         "start": 3, "end": 6, "unit": "months"},
        {"label": "Phase 3: Real-Time Integration (Months 7-12)",
         "start": 6, "end": 12, "unit": "months"},
    ]
    assert sources["timeline.phases"] == packet_fill.RESEARCH_PAPER
    # The chart's own numbers, never re-based: the label says "Months 1-3" and
    # the data says 0 to 3, and picking one over the other would manufacture a
    # boundary the paper never drew.
    assert [phase["start"] for phase in fill["timeline.phases"]] == [0, 3, 6]


def test_the_column_axis_and_the_milestones_are_sourced_off_the_same_chart():
    """The other two breaks, at the packet layer. The axis had exactly one
    mapped path (`timeline.week_buckets[]`, UNSOURCEABLE) and the milestones had
    three, all UNSOURCEABLE, so filling either value without a sourced path
    would put a real axis on the deck and move `role_coverage` by zero."""
    packet = assemble("OPP-ONE", paper(CLIENTS["one"]["shape"]))
    fill, _sources = fill_map(packet=packet)
    assert fill["timeline.columns"] == [
        {"label": "0–3", "unit": "months"},
        {"label": "3–6", "unit": "months"},
        {"label": "6–12", "unit": "months"},
    ]
    # One per phase boundary: the end of each phase the chart drew.
    assert fill["timeline.milestones"] == [
        {"position": 3, "unit": "months"},
        {"position": 6, "unit": "months"},
        {"position": 12, "unit": "months"},
    ]
    for path in ("timeline.columns[].label", "timeline.columns[].unit",
                 "timeline.phases[].start", "timeline.phases[].end",
                 "timeline.phases[].unit", "timeline.milestones[].position",
                 "timeline.milestones[].unit"):
        assert ORIGINS[path] == SOURCED, path


def test_slide_four_renders_a_real_phased_plan_in_the_papers_own_unit(template):
    """The whole point of the stage, measured on what the deck receives.

    Three roles that rendered nothing at all now carry the chart's own phases,
    its own numbers and its own unit. Nothing is converted: the excerpt is
    month-denominated and the word `WEEK` appears nowhere on the slide.
    """
    _markdown, _fill, placeholder_map, prompt = rendered(CLIENTS["one"], template)
    assert placeholder_map["timeline_columns"] == ["MONTHS 0–3", "3–6", "6–12"]
    assert placeholder_map["timeline_rows"] == [
        {"phase": "Phase 1: Decision Rule Digitization (Months 1-3)", "weeks": "0–3"},
        {"phase": "Phase 2: Optimization Engine Deployment (Months 4-6)", "weeks": "3–6"},
        {"phase": "Phase 3: Real-Time Integration (Months 7-12)", "weeks": "6–12"},
    ]
    assert placeholder_map["milestones"] == [
        {"id": "M1", "week": "MONTH 3", "label": "MILESTONE 1"},
        {"id": "M2", "week": "MONTH 6", "label": "MILESTONE 2"},
        {"id": "M3", "week": "MONTH 12", "label": "MILESTONE 3"},
    ]
    for role in ("timeline_columns", "timeline_rows", "milestones"):
        assert MISSING_MARKER.format(role=role) not in prompt
    # No week crosses into a month-denominated plan. Asserted on the values
    # rather than on the prompt text, because the row record's own field is
    # named `weeks` by the template and that name is not a unit claim.
    rendered_values = (
        placeholder_map["timeline_columns"]
        + [row["weeks"] for row in placeholder_map["timeline_rows"]]
        + [milestone["week"] for milestone in placeholder_map["milestones"]]
    )
    assert not any("WEEK" in value.upper() for value in rendered_values)


def test_the_ordinal_milestone_label_names_no_outcome_the_paper_never_stated(
    template,
):
    """Antonio's call, 2026-08-18. `Milestone 1` is generic framing in the same
    class as the templated section framing: it names the milestone's place in the
    sequence and asserts nothing about the engagement. `M1 · Pilot Validated`
    names an outcome the paper never stated, and is the thing this must not
    become. Checked mechanically: the framing is identical across two clients
    whose papers differ, and carries no word from either paper."""
    _md_one, _f1, map_one, _p1 = rendered(CLIENTS["one"], template)
    _md_two, _f2, map_two, _p2 = rendered(CLIENTS["two"], template)
    assert [m["label"] for m in map_one["milestones"]] == \
           [m["label"] for m in map_two["milestones"]]
    assert [m["id"] for m in map_one["milestones"]] == \
           [m["id"] for m in map_two["milestones"]]
    for milestone in map_one["milestones"]:
        assert re.fullmatch(r"MILESTONE \d+", milestone["label"]), milestone
        assert re.fullmatch(r"M\d+", milestone["id"]), milestone
    # The position beside it is the paper's own, and so is the phase label the
    # row carries: the framing is shared, the content is not.
    assert [row["phase"] for row in map_one["timeline_rows"]] != \
           [row["phase"] for row in map_two["timeline_rows"]]
    for milestone in map_one["milestones"]:
        assert re.fullmatch(r"MONTHS? \d+", milestone["week"]), milestone


def test_a_week_denominated_plan_renders_weeks_and_is_never_converted(template):
    """The branch the committed fixtures cannot reach. The corpus carries both
    units (`Phase 1: Foundation (Weeks 1-6)` on the published Lucerne papers),
    so shipping unit-carrying code tested only on months ships an untested
    branch. Nothing here is converted or normalised: the axis, the spans and the
    milestone positions all read weeks because the chart says weeks."""
    _markdown, fill, placeholder_map, prompt = rendered_from_paper(
        WEEKS_CLIENT, synthetic_weeks_paper(), template
    )
    assert [phase["unit"] for phase in fill["timeline.phases"]] == ["weeks"] * 3
    assert placeholder_map["timeline_columns"] == ["WEEKS 0–6", "6–12", "12–18"]
    assert [row["weeks"] for row in placeholder_map["timeline_rows"]] == [
        "0–6", "6–12", "12–18",
    ]
    assert [m["week"] for m in placeholder_map["milestones"]] == [
        "WEEK 6", "WEEK 12", "WEEK 18",
    ]
    slide_four = prompt.split("## Slide 4")[1].split("## Slide 5")[0]
    assert "MONTH" not in slide_four.upper()


def test_a_paper_with_no_timeline_chart_still_renders_markers_on_slide_four(
    template,
):
    """A normal outcome, not a failure. One of the eight committed excerpts
    carries no timeline chart at all, and the slide has to say so rather than
    render an invented plan."""
    client = dict(CLIENTS["two"], shape="no-timeline-chart")
    _markdown, fill, placeholder_map, prompt = rendered(client, template)
    for path in ("timeline.phases", "timeline.columns", "timeline.milestones"):
        assert path not in fill
    for role in ("timeline_columns", "timeline_rows", "milestones"):
        assert placeholder_map[role] == []
        assert MISSING_MARKER.format(role=role) in prompt


def test_role_coverage_rises_by_the_three_slide_four_roles_and_the_horizon(template):
    """The number that tells the truth about this stage, per E11.

    0.22 before E11, four of eighteen (`after_metric_1`, `client_full`,
    `pe_firm`, `value_mapping`), measured on both placeholder clients on
    2026-08-18. 0.39 after Stage 1, seven of eighteen, the three additions being
    exactly slide 4's. 0.44 after Stage 2c, eight of eighteen, the addition being
    `build_summary`: the same chart's own last phase boundary, carried in the
    unit the chart states. `data_completeness` reads 1.00 throughout and says
    nothing about any of it.
    """
    _markdown, _fill, placeholder_map, _prompt = rendered(CLIENTS["one"], template)
    # Read off the document's own `from_kg` since E11 Stage 2c, which is what
    # `role_coverage` reads. `components` and `action_items` render on this
    # harness and are deliberately NOT here: they render the contract's deck
    # standard, the same three layers and four actions every deck for every
    # client gets, and counting a house template as coverage would have raised
    # this number by 0.11 for showing exactly what it showed before.
    from_kg = set(placeholder_map["_provenance"]["from_kg"]) | set(
        placeholder_map["_provenance"]["derived"]
    )
    carrying = sorted(
        role for role in set(COVERAGE_MAP.values())
        if placeholder_map.get(role)
        and any(path in from_kg
                for path, named in COVERAGE_MAP.items() if named == role)
    )
    # `return_rows` in `value_mapping`'s old place since 2026-09-23: it carries
    # the scenario figures, and the chart carries only reviewer figures.
    assert carrying == [
        "after_metric_1", "build_summary", "client_full", "milestones",
        "pe_firm", "return_rows", "timeline_columns", "timeline_rows",
    ]
    assert placeholder_map["components"] and placeholder_map["action_items"]
    assert packet_fill.role_coverage(placeholder_map) == 0.44


def test_the_six_field_roster_and_its_gate_are_untouched_by_the_new_fields(
    template,
):
    """Stop condition 1, asserted rather than trusted. The new packet fields go
    into the fill map and the slot table, never into `ROSTER`: moving that
    denominator lands a live packet near 0.15, fails the 0.70 gate, and no live
    deck ever renders again."""
    import packet_assembly
    assert len(packet_assembly.ROSTER) == 6
    markdown, _fill, _placeholder_map, _prompt = rendered(CLIENTS["one"], template)
    assert "data_completeness: 1.0" in markdown.split("---")[1]


# ---------------------------------------------------------------------------
# E11 Stage 2c block 3 — the build horizon, in the plan's own unit.
# ---------------------------------------------------------------------------

def test_the_horizon_is_the_plans_own_last_boundary_in_the_plans_own_unit(template):
    """Slide 2's AFTER band carried no horizon at all on this corpus. The role
    reads `build_summary.duration_weeks`, which is week-denominated, and every
    committed excerpt states its plan in months, so the one number the band
    exists to show was absent on every deck. Nothing is converted to fix that:
    the plan's own last boundary crosses with the plan's own unit beside it."""
    _markdown, fill, placeholder_map, prompt = rendered(CLIENTS["one"], template)

    assert fill["build_summary.duration"] == 12
    assert fill["build_summary.duration_unit"] == "months"
    assert placeholder_map["after_horizon"] == "AFTER — TARGET IN ~12 MONTHS"
    assert placeholder_map["build_summary"].startswith("THE 12-MONTH BUILD")
    assert MISSING_MARKER.format(role="after_horizon") not in prompt
    # The week-denominated contract field is untouched and still absent, with the
    # unit-mismatch reason it already had. No multiplier was chosen anywhere.
    assert "build_summary.duration_weeks" not in fill
    assert "WEEKS" not in placeholder_map["after_horizon"]


def test_a_plan_stating_two_units_yields_no_horizon_rather_than_a_reconciled_one():
    """The refusal, and it is the same one the whole timeline path is built on.
    Reconciling months against weeks needs a multiplier and choosing one is not
    this provider's call, so the field stays absent with its own gap."""
    mixed = [{"label": "One", "start": 0, "end": 6, "unit": "months"},
             {"label": "Two", "start": 6, "end": 20, "unit": "weeks"}]
    assert packet_fill._horizon(mixed) == {}


def test_a_plan_missing_an_end_yields_no_horizon_because_the_missing_one_may_be_last():
    """A phase with no end could be the one that finishes the plan, so the
    largest end still standing is not the horizon and is not reported as one."""
    partial = [{"label": "One", "start": 0, "end": 6, "unit": "months"},
               {"label": "Two", "start": 6, "unit": "months"}]
    assert packet_fill._horizon(partial) == {}
    assert packet_fill._horizon([]) == {}


def test_the_horizon_is_the_largest_boundary_and_not_the_last_record():
    """A paper listing its phases out of order still yields its own last
    boundary. The selection is stated rather than incidental."""
    out_of_order = [{"label": "Two", "start": 6, "end": 14, "unit": "months"},
                    {"label": "One", "start": 0, "end": 6, "unit": "months"}]
    assert packet_fill._horizon(out_of_order)["build_summary.duration"] == 14


def test_a_week_denominated_packet_still_renders_exactly_what_it_did_before():
    """No packet that already carried a horizon moves. `duration_weeks` wins
    where a packet states one, so the frozen fixtures and both reference packets
    render the same string they rendered before Stage 2c."""
    from data_source_adapter import _horizon_parts
    assert _horizon_parts({"duration_weeks": 16}) == (16, "weeks")
    assert _horizon_parts({"duration_weeks": 16, "duration": 4,
                           "duration_unit": "months"}) == (16, "weeks")
    # A number with no unit yields nothing: the deck would have to guess which
    # unit it was, and guessing is what the no-conversion rule refuses.
    assert _horizon_parts({"duration": 4}) == (None, "")
    assert _horizon_parts({}) == (None, "")


def test_the_derived_horizon_is_reported_as_derived_and_not_as_a_lifted_figure(
    template,
):
    """The numbers are the paper's and the selection is ours, so section 8 says
    which of the two a reviewer is looking at. The three provenance lists are
    disjoint, and `role_coverage` reads the union because a derived boundary is
    still real platform data."""
    markdown, _fill, placeholder_map, _prompt = rendered(CLIENTS["one"], template)
    ledger = _section_yaml(_split_sections(markdown)[8])["provenance"]

    assert ledger["derived"] == ["build_summary.duration",
                                 "build_summary.duration_unit"]
    for path in ledger["derived"]:
        assert path not in ledger["from_kg"]
        assert path not in ledger["templated_defaults"]
    assert packet_fill.role_coverage(placeholder_map) == 0.44
