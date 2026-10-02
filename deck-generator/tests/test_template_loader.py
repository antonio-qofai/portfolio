"""Tests for Module 1 — Template Loader (v2).

v2 role typing: every role is `string`, `list`, or `list_of_records`, read from
the template spec's own per-role annotations (no keyword heuristics). Each
`list_of_records` role carries its declared field schema. This matches the
build plan's "Shared contract" section so Modules 2 and 3 can build on it.

Run with: python3 tests/test_template_loader.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from template_loader import load_template

TEMPLATE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "templates", "proposal-template.md"
)

VALID_TYPES = ("string", "list", "list_of_records")

# The record roles the template declares, with the exact field schema the
# loader must carry through (build plan, "Shared contract" section).
EXPECTED_RECORD_ROLES = {
    "components": ["number", "kicker", "title", "description"],
    "timeline_rows": ["phase", "workstream", "workstream_detail", "weeks"],
    "milestones": ["id", "week", "label"],
    # `opportunity` leads the row since 2026-09-13 (item 15): one more label on
    # a named row, carried on a deck that renders more than one opportunity and
    # absent from the record entirely on a deck that does not.
    "value_mapping": [
        "opportunity", "scenario", "ebitda_gain", "qofai_comp",
        "client_retained_ebitda", "enterprise_value",
    ],
    "action_items": ["number", "week", "owner", "title", "description"],
    # Slide 5's adaptive deal sheet (2026-09-23): named rows, so any deal is
    # expressible as data.
    "investment_rows": ["opportunity", "label", "value"],
    "return_rows": ["opportunity", "scenario", "annual_ebitda", "margin", "payback"],
    "terms_rows": ["label", "value"],
}


def _roles_by_slide(result):
    return {
        s["number"]: {r["name"]: r for r in s["roles"]}
        for s in result["slides"]
    }


def test_loads_six_slides_in_order():
    result = load_template(TEMPLATE_PATH)
    assert result["deck_type"] == "proposal", result["deck_type"]
    assert result["slide_count"] == 6, result["slide_count"]
    assert len(result["slides"]) == 6, len(result["slides"])
    assert [s["number"] for s in result["slides"]] == [1, 2, 3, 4, 5, 6]


def test_every_role_has_a_valid_type():
    result = load_template(TEMPLATE_PATH)
    for slide in result["slides"]:
        assert slide["roles"], f"slide {slide['number']} has no roles"
        for role in slide["roles"]:
            assert role["type"] in VALID_TYPES, role


def test_spot_check_scalar_and_list_roles():
    roles_by_slide = _roles_by_slide(load_template(TEMPLATE_PATH))
    # Scalars.
    assert roles_by_slide[2]["today_metric_1"]["type"] == "string"
    assert roles_by_slide[2]["build_summary"]["type"] == "string"
    assert roles_by_slide[5]["terms_footnote"]["type"] == "string"
    # Lists of strings.
    assert roles_by_slide[2]["today_pain_bullets"]["type"] == "list"
    assert roles_by_slide[2]["after_capability_bullets"]["type"] == "list"
    assert roles_by_slide[4]["timeline_columns"]["type"] == "list"


def test_terms_rows_is_a_sensitive_record_role_that_always_renders():
    # The deal as named rows (label + value), so row labels are data, not code.
    # Sensitive, so the studio never offers to Clear its marker, and NOT
    # optional, so a deck with no deal entered says so on the slide.
    roles_by_slide = _roles_by_slide(load_template(TEMPLATE_PATH))
    role = roles_by_slide[5]["terms_rows"]
    assert role["type"] == "list_of_records", role
    assert role["fields"] == ["label", "value"], role
    assert not role.get("optional"), role


def test_the_prd_blocks_and_the_chart_are_optional():
    # Drawn only when something states them (2026-09-22/23).
    roles_by_slide = _roles_by_slide(load_template(TEMPLATE_PATH))
    for name in ("investment_rows", "return_rows", "value_mapping"):
        assert roles_by_slide[5][name].get("optional"), name


def test_record_roles_carry_declared_fields():
    roles_by_slide = _roles_by_slide(load_template(TEMPLATE_PATH))
    all_roles = {}
    for slide_roles in roles_by_slide.values():
        all_roles.update(slide_roles)

    for name, expected_fields in EXPECTED_RECORD_ROLES.items():
        assert name in all_roles, f"missing record role: {name}"
        role = all_roles[name]
        assert role["type"] == "list_of_records", role
        assert role["fields"] == expected_fields, role


def test_only_the_declared_record_roles_are_records():
    result = load_template(TEMPLATE_PATH)
    record_names = set()
    for slide in result["slides"]:
        for role in slide["roles"]:
            if role["type"] == "list_of_records":
                record_names.add(role["name"])
    assert record_names == set(EXPECTED_RECORD_ROLES), record_names


def test_no_fbk_example_content_is_emitted():
    result = load_template(TEMPLATE_PATH)
    serialized = repr(result)
    for banned in ("Fabrikam Marine", "FBK", "Woodgrove Partners", "37.8", "the inventory app"):
        assert banned not in serialized, f"leaked example content: {banned}"


def test_recurring_fields_parsed_separately():
    result = load_template(TEMPLATE_PATH)
    names = {f["name"] for f in result["recurring_fields"]}
    assert names == {
        "client_full", "client_short", "pe_firm", "project_name",
        "deck_date", "confidentiality", "deck_type_label",
        # The footer's own denominator, added 2026-09-13 with the repeating
        # slide 2: six blocks render six slides for one opportunity and seven
        # for two, so the count is data rather than a number written down here.
        "total_slides",
    }, names
    for role in result["recurring_fields"]:
        assert role["type"] in VALID_TYPES, role
    # Slides may legitimately re-reference a recurring field inline (e.g.
    # slide 2's own footer line repeats {client_short}) — but those inline
    # mentions carry no type annotation, so they are not parsed as slide roles.


def test_the_retired_commercial_roles_are_gone_from_the_template():
    # The FBK performance-partnership layout, retired 2026-09-22.
    roles_by_slide = _roles_by_slide(load_template(TEMPLATE_PATH))
    for name in ("commercial_rows", "client_retention", "downside_protection",
                 "payment_mechanics", "qofai_investment", "client_upfront",
                 "comp_schedule"):
        assert name not in roles_by_slide[5], name


def test_label_parses_alongside_fields_without_polluting_them():
    # A trailing `label:` must not be swept into a preceding `fields:` capture,
    # and a label may attach to any role type including list_of_records.
    spec = (
        "Deck type: `proposal`\n"
        "Slide count: 1\n\n"
        "## Slide 1 — Only\n"
        '- `{rows}` `[type: list_of_records; fields: a, b; label: \"MY HEADING\"]`\n'
        '- `{scalar}` `[type: string; label: \"OTHER HEADING\"]`\n'
    )
    with tempfile.NamedTemporaryFile(
        "w", suffix=".md", delete=False, encoding="utf-8"
    ) as f:
        f.write(spec)
        tmp_path = f.name
    try:
        result = load_template(tmp_path)
    finally:
        os.unlink(tmp_path)
    roles = {r["name"]: r for r in result["slides"][0]["roles"]}
    assert roles["rows"]["fields"] == ["a", "b"], roles["rows"]
    assert roles["rows"]["label"] == "MY HEADING", roles["rows"]
    assert roles["scalar"]["label"] == "OTHER HEADING", roles["scalar"]


def test_parses_repeat_section_key_and_optional_directives():
    # The status template's per-slide directives: a Section key, a Repeat (the
    # variable-count workstream slide), and an optional role that is omitted
    # cleanly when empty rather than flagged missing.
    spec = (
        "Deck type: `status`\n"
        "Slide count: 2\n\n"
        "## Slide 1 — Cover\n"
        "Section key: `cover`\n"
        "- `{title}` `[type: string]`\n\n"
        "## Slide 2 — Workstream\n"
        "Section key: `workstreams`\n"
        "Repeat: workstreams\n"
        "- `{name}` `[type: string]`\n"
        "- `{right_label}` `[type: string; optional]`\n"
    )
    with tempfile.NamedTemporaryFile(
        "w", suffix=".md", delete=False, encoding="utf-8"
    ) as f:
        f.write(spec)
        tmp_path = f.name
    try:
        result = load_template(tmp_path)
    finally:
        os.unlink(tmp_path)
    cover, ws = result["slides"]
    assert cover["section_key"] == "cover", cover
    assert "repeat_over" not in cover, cover
    assert ws["section_key"] == "workstreams", ws
    assert ws["repeat_over"] == "workstreams", ws
    roles = {r["name"]: r for r in ws["roles"]}
    assert roles["right_label"].get("optional") is True, roles["right_label"]
    assert "optional" not in roles["name"], roles["name"]


def test_status_template_loads_with_repeat_slide():
    status_path = os.path.join(
        os.path.dirname(__file__), "..", "templates", "status-template.md"
    )
    result = load_template(status_path)
    assert result["deck_type"] == "status", result["deck_type"]
    by_key = {s.get("section_key"): s for s in result["slides"]}
    assert set(by_key) == {"cover", "tracking", "workstreams", "next_steps"}, by_key
    assert by_key["workstreams"]["repeat_over"] == "workstreams", by_key["workstreams"]
    # progress_right_label is the optional role on the workstream slide.
    ws_roles = {r["name"]: r for r in by_key["workstreams"]["roles"]}
    assert ws_roles["progress_right_label"].get("optional") is True, ws_roles["progress_right_label"]


def test_declared_count_mismatch_raises():
    # A spec that declares more slides than it actually carries must fail
    # loudly, not return a slide_count that disagrees with the slides list.
    spec = (
        "Deck type: `proposal`\n"
        "Slide count: 6\n\n"
        "## Slide 1 — Only\n"
        "- `{a}` `[type: string]`\n\n"
        "## Slide 2 — Second\n"
        "- `{b}` `[type: string]`\n"
    )
    with tempfile.NamedTemporaryFile(
        "w", suffix=".md", delete=False, encoding="utf-8"
    ) as f:
        f.write(spec)
        tmp_path = f.name
    try:
        load_template(tmp_path)
    except ValueError as e:
        assert "declares 6" in str(e) and "2 parsed" in str(e), str(e)
    else:
        raise AssertionError("expected ValueError on count mismatch")
    finally:
        os.unlink(tmp_path)


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
