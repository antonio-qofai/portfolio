"""Splitting "Supply missing values" into what it claims to be (2026-08-20).

The card listed every `[MISSING: ...]` marker on a deck under one heading. On the
live proposal deck of 2026-08-20 that was thirty rows, of which FIVE were values a
source failed to provide; the other twenty-five were fields the deck deliberately
asks a human for and never asks a source for. A list where 83% of the rows are
working as intended teaches a reviewer to skip the list, which defeats its only
job.

The regression these tests pin is the split and its counts:

    5   the source had nothing   subtitle, opportunity_summary, description x3
    14  we expect you to supply  deck_date x6, owner x4, week x4
    11  commercial, sensitive    the scenario table's three figures x2, plus
                                 commercial_rows, client_retention,
                                 downside_protection, payment_mechanics,
                                 terms_footnote

`tests/fixtures/missing-values/proposal-deck-30-markers.html` is that deck, with
the client's name and its dollar figures replaced by neutral stand-ins and every
tag, class, marker and slide boundary left exactly as rendered. It carries the same
thirty markers in the same places, which is what the counts are about. Where the
real deck is still on this machine the same assertions run against it too, so the
fixture cannot drift away from the artifact it stands for.

Run with: python3 -m pytest tests/test_missing_value_groups.py
"""

import filecmp
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ui"))

import missing_values  # noqa: E402
import template_loader  # noqa: E402
from html_edit_layer import (  # noqa: E402
    EditNotApplicable,
    apply_edits_and_save,
    list_missing_markers,
    load_edit_log,
)
from missing_values import (  # noqa: E402
    GROUP_ORDER,
    REVIEWER,
    SENSITIVE,
    SOURCE,
    group_missing_markers,
    marker_counts,
    merge_expectations,
)
from template_loader import fill_expectations, load_template  # noqa: E402

_ROOT = os.path.join(os.path.dirname(__file__), "..")
PROPOSAL_TEMPLATE = os.path.join(_ROOT, "templates", "proposal-template.md")
STATUS_TEMPLATE = os.path.join(_ROOT, "templates", "status-template.md")
DECK_FIXTURE = os.path.join(
    os.path.dirname(__file__), "fixtures", "missing-values",
    "proposal-deck-30-markers.html")

# The live deck the counts were taken from, if it is still on this machine. Not a
# committed input and never required: the fixture above is the portable regression.
LIVE_DECK = os.path.join(
    os.path.expanduser("~"), "deck-demo-out", "decks",
    "01 - Lamna (Demo)", "claude code", "output-3.html")

# The split, by MARKERS, on that deck. The number Antonio checks.
EXPECTED_COUNTS = {SOURCE: 5, REVIEWER: 14, SENSITIVE: 11}

# Which field names land in which group, and how many markers each contributes.
EXPECTED_FIELDS = {
    SOURCE: {"subtitle": 1, "opportunity_summary": 1, "description": 3},
    REVIEWER: {"deck_date": 6, "week": 4, "owner": 4},
    SENSITIVE: {
        "commercial_rows": 1, "client_retention": 1, "downside_protection": 1,
        "payment_mechanics": 1, "terms_footnote": 1,
        "qofai_comp": 2, "client_retained_ebitda": 2, "enterprise_value": 2,
    },
}


def _deck_paths():
    """The fixture, plus the real deck when this machine still has it."""
    paths = [DECK_FIXTURE]
    if os.path.isfile(LIVE_DECK):
        paths.append(LIVE_DECK)
    return paths


def _grouped(html):
    expectations = fill_expectations(load_template(PROPOSAL_TEMPLATE))
    return group_missing_markers(list_missing_markers(html), expectations)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


# --------------------------- the declaration itself -------------------------

def test_a_role_declares_who_fills_it_and_how_widely():
    roles = template_loader._extract_roles(
        '- `{a}` `[type: string]`\n'
        '- `{b}` `[type: string; reviewer]`\n'
        '- `{c}` `[type: string; reviewer: sensitive]`\n'
        '- `{d}` `[type: string; reviewer; deck_wide]`\n'
    )
    by_name = {role["name"]: role for role in roles}
    assert "fill" not in by_name["a"] and "deck_wide" not in by_name["a"], (
        "a role that declares nothing must parse to the dict it always did"
    )
    assert by_name["b"]["fill"] == REVIEWER
    assert by_name["c"]["fill"] == SENSITIVE
    assert by_name["d"]["fill"] == REVIEWER and by_name["d"]["deck_wide"] is True


def test_the_bare_flags_compose_in_any_order_with_fields_and_label():
    """Order-independence is not cosmetic: an annotation the regex does not match
    is not a role at all, so a template writing two flags the other way round
    would silently drop the whole line."""
    for tail in ("optional; deck_wide", "deck_wide; optional",
                 "reviewer: sensitive; optional", "optional; reviewer: sensitive"):
        roles = template_loader._extract_roles(
            '- `{x}` `[type: list_of_records; fields: p, q; label: "H"; '
            + tail + ']`\n')
        assert len(roles) == 1, tail
        role = roles[0]
        assert role["fields"] == ["p", "q"], (tail, role)
        assert role["label"] == "H", (tail, role)
        assert role["optional"] is True, (tail, role)


def test_an_unrecognised_flag_still_means_the_line_is_not_a_role():
    """Unchanged behaviour: only an annotation the loader understands declares a
    role, so a typo shows up as a field nothing fills rather than as a wrong class.
    """
    assert template_loader._extract_roles(
        '- `{x}` `[type: string; reviwer]`\n') == []


def test_a_record_field_declares_the_same_thing_one_level_down():
    roles = template_loader._extract_roles(
        "- `{r}` `[type: list_of_records; fields: number, week (reviewer), "
        "kicker (optional), fee (reviewer: sensitive)]`\n")
    role = roles[0]
    assert role["fields"] == ["number", "week", "kicker", "fee"], role
    assert role["optional_fields"] == ["kicker"], role
    assert role["field_fills"] == {"week": REVIEWER, "fee": SENSITIVE}, role


def test_a_record_field_takes_several_flags_separated_by_spaces():
    roles = template_loader._extract_roles(
        "- `{r}` `[type: list_of_records; fields: a (optional reviewer)]`\n")
    role = roles[0]
    assert role["optional_fields"] == ["a"], role
    assert role["field_fills"] == {"a": REVIEWER}, role


# ------------------------- collapsing a disagreement ------------------------

def test_two_declarations_disagreeing_on_a_name_collapse_to_a_source_gap():
    """A field name is the only key a marker carries, so the same name under two
    roles is one entry. Mis-filing a real gap as deliberate hides it; mis-filing a
    deliberate value as a gap costs a second look. So a disagreement takes the
    second."""
    template = {"slides": [{"roles": [
        {"name": "r1", "type": "list_of_records", "fields": ["week"],
         "field_fills": {"week": REVIEWER}},
        {"name": "r2", "type": "list_of_records", "fields": ["week"]},
    ]}], "recurring_fields": []}
    assert fill_expectations(template)["week"]["fill"] == SOURCE


def test_a_disagreement_about_width_is_never_resolved_by_collapsing():
    """Collapsing a role whose occurrences each carry their own value would leave
    all but one of them unfillable, so per-instance wins."""
    template = {"slides": [
        {"roles": [{"name": "d", "type": "string", "deck_wide": True}]},
        {"roles": [{"name": "d", "type": "string"}]},
    ], "recurring_fields": []}
    assert fill_expectations(template)["d"]["deck_wide"] is False


def test_a_record_field_never_claims_to_be_deck_wide():
    template = {"slides": [{"roles": [
        {"name": "r", "type": "list_of_records", "fields": ["label"]}]}],
        "recurring_fields": []}
    assert fill_expectations(template)["label"]["deck_wide"] is False


def test_merging_templates_applies_the_same_rule_one_level_up():
    merged = merge_expectations([
        {"week": {"fill": REVIEWER, "deck_wide": False},
         "owner": {"fill": REVIEWER, "deck_wide": False}},
        {"week": {"fill": SOURCE, "deck_wide": False}},
    ])
    assert merged["week"] == {"fill": SOURCE, "deck_wide": False}
    assert merged["owner"]["fill"] == REVIEWER, "an uncontested name is unchanged"


def test_an_undeclared_field_name_is_a_source_gap():
    assert missing_values.classify_marker("no_such_field", {}) == SOURCE


# --------------------- the live deck: five, fourteen, eleven -----------------

@pytest.mark.parametrize("deck_path", _deck_paths())
def test_the_thirty_markers_split_five_fourteen_eleven(deck_path):
    groups = _grouped(_read(deck_path))
    counts = marker_counts(groups)
    assert counts == EXPECTED_COUNTS, counts
    assert sum(counts.values()) == 30, (
        "the three headings must still add up to the markers on the deck"
    )


@pytest.mark.parametrize("deck_path", _deck_paths())
def test_each_group_holds_exactly_the_fields_it_should(deck_path):
    groups = _grouped(_read(deck_path))
    for group, expected in EXPECTED_FIELDS.items():
        got = {}
        for row in groups[group]:
            got[row["field"]] = got.get(row["field"], 0) + row["count"]
        assert got == expected, (group, got)


def test_the_committed_fixture_carries_the_same_markers_as_the_live_deck():
    """The fixture stands in for the artifact, so it has to keep standing in: same
    markers, same fields, same slides. Skipped where the real deck is not on this
    machine, which is every machine but the one it was rendered on."""
    if not os.path.isfile(LIVE_DECK):
        pytest.skip("the live deck of 2026-08-20 is not on this machine")
    def shape(html):
        return [(m["slide"], m["field"], m["occurrences"])
                for m in list_missing_markers(html)]
    assert shape(_read(DECK_FIXTURE)) == shape(_read(LIVE_DECK))


def test_no_client_name_reached_the_committed_fixture():
    body = _read(DECK_FIXTURE)
    for fragment in ("erizon", "ERIZON"):
        assert fragment not in body, fragment


# ------------------------------ deduplication -------------------------------

@pytest.mark.parametrize("deck_path", _deck_paths())
def test_the_deck_wide_date_is_one_row_covering_six_slides(deck_path):
    rows = [r for r in _grouped(_read(deck_path))[REVIEWER]
            if r["field"] == "deck_date"]
    assert len(rows) == 1, "one value repeated in six footers is one input"
    assert rows[0]["count"] == 6
    assert rows[0]["slides"] == [1, 2, 3, 4, 5, 6]
    assert rows[0]["deck_wide"] is True


@pytest.mark.parametrize("deck_path", _deck_paths())
def test_nothing_else_collapses_by_field_name(deck_path):
    """The trap in the deduplication. `description` is three different next steps,
    `week` and `owner` four different rows, `qofai_comp` two different scenarios —
    collapsing any of them would leave all but one occurrence unfillable."""
    groups = _grouped(_read(deck_path))
    rows = [row for group in GROUP_ORDER for row in groups[group]]
    per_instance = {"description": 3, "week": 4, "owner": 4,
                    "qofai_comp": 2, "client_retained_ebitda": 2,
                    "enterprise_value": 2}
    for field, expected in per_instance.items():
        matching = [r for r in rows if r["field"] == field]
        assert len(matching) == expected, (field, matching)
        assert all(r["count"] == 1 and r["deck_wide"] is False for r in matching), (
            field, matching)


# --------------------- aiming at a marker that repeats ----------------------

@pytest.mark.parametrize("deck_path", _deck_paths())
def test_every_marker_carries_a_position_to_be_aimed_at(deck_path):
    """The defect this split used to make visible rather than fix. Four
    `[MISSING: week]` on one slide are four values wearing one string, so the
    string is ambiguous and `apply_text_edit` refused it by design; the card
    reported 17 of the deck's 30 markers as unfillable and sent the reviewer to the
    exact-text edit to name a wider span by hand. Every target now carries the
    position instead, so there is nothing left to report."""
    groups = _grouped(_read(deck_path))
    rows = [row for group in GROUP_ORDER for row in groups[group]]
    repeated = {"description": 3, "week": 4, "owner": 4, "qofai_comp": 2,
                "client_retained_ebitda": 2, "enterprise_value": 2}
    assert sum(repeated.values()) == 17, "the markers that used to be refused"

    for field, count in repeated.items():
        targets = [t for row in rows if row["field"] == field
                   for t in row["targets"]]
        assert len(targets) == count, (field, targets)
        # One row per marker, each aimed at its own position, and between them
        # they cover every occurrence on the slide exactly once. A duplicate or a
        # gap here would mean two rows fighting over one marker or one marker
        # unreachable, which is the failure this replaces.
        assert sorted(t["occurrence"] for t in targets) == list(range(1, count + 1)), (
            field, targets)
        assert all(t["occurrences"] == count for t in targets), (field, targets)

    for row in rows:
        for target in row["targets"]:
            assert 1 <= target["occurrence"] <= target["occurrences"], (row, target)


@pytest.mark.parametrize("deck_path", _deck_paths())
def test_no_row_reports_itself_as_unfillable(deck_path):
    """`fillable` is gone rather than always True. A flag every row sets the same
    way is a flag a caller keeps branching on for no reason, and the branch was the
    thing that offered an explanation in place of an input."""
    groups = _grouped(_read(deck_path))
    for group in GROUP_ORDER:
        for row in groups[group]:
            assert "fillable" not in row, row


def test_list_missing_markers_counts_a_marker_texts_occurrences_on_its_slide():
    html = ('<!doctype html><html><body>'
            '<section class="slide" data-slide="1">'
            '<p><span class="flag">[MISSING: week]</span></p>'
            '<p><span class="flag">[MISSING: week]</span></p>'
            '<p><span class="flag">[MISSING: owner]</span></p>'
            '</section>'
            '<section class="slide" data-slide="2">'
            '<p><span class="flag">[MISSING: week]</span></p>'
            '</section></body></html>')
    got = [(m["slide"], m["field"], m["occurrences"])
           for m in list_missing_markers(html)]
    assert got == [(1, "week", 2), (1, "week", 2), (1, "owner", 1),
                   (2, "week", 1)], got


# ------------------------- the fan-out, and its failure ---------------------

def _deck_with_footer_dates(tmp_path, slides=6):
    """A deck carrying one `[MISSING: deck_date]` in each of ``slides`` footers."""
    body = "".join(
        f'<section class="slide" data-slide="{n}"><h1>Slide {n}</h1>'
        f'<p class="footer"><span class="flag">[MISSING: deck_date]</span></p>'
        f"</section>"
        for n in range(1, slides + 1))
    path = os.path.join(str(tmp_path), "output-1.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write("<!doctype html><html><body>" + body + "</body></html>")
    return path


def test_one_deck_wide_value_fans_out_to_every_slide_as_one_revision(tmp_path):
    deck = _deck_with_footer_dates(tmp_path)
    expectations = {"deck_date": {"fill": REVIEWER, "deck_wide": True}}
    row = group_missing_markers(
        list_missing_markers(_read(deck)), expectations)[REVIEWER][0]
    assert row["count"] == 6

    result = apply_edits_and_save(
        deck,
        [{"slide": t["slide"], "source": t["source"], "replacement": "AUGUST 20 2026"}
         for t in row["targets"]],
        kind="content", author="reviewer", atomic=True)

    assert len(result["applied"]) == 6 and result["failed"] == []
    written = _read(result["revision_path"])
    assert written.count("AUGUST 20 2026") == 6
    assert "[MISSING: deck_date]" not in written
    # One reviewer action, one revision, so undo reverses exactly what they did.
    log = load_edit_log(deck)
    assert len({e["revision"] for e in log}) == 1, log
    assert len(log) == 6, log
    assert sorted(e["slide"] for e in log) == [1, 2, 3, 4, 5, 6]


def test_a_fan_out_that_cannot_reach_every_slide_writes_nothing(tmp_path):
    """Five slides updated and one refused must not report success — and must not
    leave the deck in the state the deck-wide declaration says cannot exist."""
    deck = _deck_with_footer_dates(tmp_path)
    before = os.path.join(str(tmp_path), "before.html")
    shutil.copyfile(deck, before)
    targets = [{"slide": t["slide"], "source": t["source"]}
               for t in group_missing_markers(
                   list_missing_markers(_read(deck)),
                   {"deck_date": {"fill": REVIEWER, "deck_wide": True}},
               )[REVIEWER][0]["targets"]]
    # One target the document cannot satisfy, standing in for a slide whose
    # marker text is not where the page thought it was.
    targets[4] = {"slide": 5, "source": "[MISSING: not_on_this_slide]"}

    with pytest.raises(EditNotApplicable) as raised:
        apply_edits_and_save(
            deck,
            [dict(t, replacement="AUGUST 20 2026") for t in targets],
            kind="content", atomic=True)

    message = str(raised.value)
    assert "slide 5" in message, message
    assert "nothing was written" in message, message
    assert filecmp.cmp(deck, before, shallow=False), "the deck must be untouched"
    assert not [n for n in os.listdir(str(tmp_path)) if "-r" in n], (
        "no revision may be written when the fan-out is refused")
    assert load_edit_log(deck) == []


def test_a_free_text_batch_is_still_best_effort(tmp_path):
    """`atomic` is opt-in. A batch from one free-text instruction is a set of
    independent changes, and one bad target must not lose the good ones."""
    deck = _deck_with_footer_dates(tmp_path, slides=2)
    result = apply_edits_and_save(deck, [
        {"slide": 1, "source": '<span class="flag">[MISSING: deck_date]</span>',
         "replacement": "AUGUST 20 2026"},
        {"slide": 2, "source": "[MISSING: not_on_this_slide]", "replacement": "x"},
    ], kind="content")
    assert len(result["applied"]) == 1 and len(result["failed"]) == 1


# ------------- the other declarations agree, rather than compete ------------

def test_every_reviewer_input_field_the_provider_names_is_declared_on_the_deck():
    """`packet_assembly.REVIEWER_INPUT` and `packet_document`'s REVIEWER origin
    class say the same thing as the template, keyed on packet schema paths instead
    of on role names. The template is the source of truth — it is the only one of
    the four keyed on the name a marker carries, and the only one that can reach
    `commercial_rows`, which has no packet path at all since D1a (2026-08-10) made
    it studio input. These assert the others have not drifted away from it.
    """
    import coverage_guard
    import packet_assembly
    import packet_document

    template = load_template(PROPOSAL_TEMPLATE)
    reviewer_supplied = set()
    for slide in template["slides"]:
        for role in slide["roles"]:
            fills = dict(role.get("field_fills", {}))
            if role.get("fill"):
                reviewer_supplied.add(role["name"])
            reviewer_supplied.update(
                name for name, fill in fills.items() if fill != SOURCE)
            if any(fill != SOURCE for fill in fills.values()):
                # A record role whose FIELDS are reviewer-supplied counts as
                # answered: the marker carries the field name, not the role's.
                reviewer_supplied.add(role["name"])

    paths = set(packet_assembly.REVIEWER_INPUT) | {
        path for path, origin in packet_document.ORIGINS.items()
        if origin == packet_document.REVIEWER}
    slotted = {path: coverage_guard.COVERAGE_MAP[path] for path in paths
               if path in coverage_guard.COVERAGE_MAP}
    assert slotted, "the mapping from packet path to role must not be empty"
    undeclared = {path: role for path, role in slotted.items()
                  if role not in reviewer_supplied}
    assert not undeclared, (
        "these packet fields are reviewer input by design on the provider side but "
        f"the template does not say so: {undeclared}")


def test_the_studio_reads_the_committed_templates_without_changing_them():
    """The templates are the declaration, so a run that reads them must leave them
    byte-identical — checked, not eyeballed."""
    for path in (PROPOSAL_TEMPLATE, STATUS_TEMPLATE):
        copy = path + ".cmp"
        shutil.copyfile(path, copy)
        try:
            fill_expectations(load_template(path))
            assert filecmp.cmp(path, copy, shallow=False), path
        finally:
            os.remove(copy)

def test_a_field_is_never_both_optional_and_expected_from_a_human():
    """The two answers are exclusive, for the same reason `optional` and a
    deliberate marker are: an optional field emits no marker at all, so declaring
    that we are waiting on a human for it would record an expectation the deck
    never surfaces. Checked across both templates, at both levels."""
    for path in (PROPOSAL_TEMPLATE, STATUS_TEMPLATE):
        template = load_template(path)
        groups = list(template["slides"]) + [
            {"roles": template["recurring_fields"]}]
        for slide in groups:
            for role in slide["roles"]:
                if role.get("optional"):
                    assert "fill" not in role, (path, role)
                optional_fields = set(role.get("optional_fields", ()))
                overlap = optional_fields & set(role.get("field_fills", {}))
                assert not overlap, (path, role["name"], overlap)


def test_a_field_we_expect_from_a_human_is_recorded_as_a_wanted_marker():
    """`tests/test_optional_record_fields.py` already records, per record field,
    why the deck WANTS a marker rather than declaring the field optional. A field
    the template now says a human fills is exactly such a field — the marker is how
    the deck asks for it — so it must be recorded there too, or the reason the
    marker exists is written down in one place and contradicted by silence in the
    other.

    Scoped to the fields that table governs (a paper-sourced record field whose
    source leaf is optional), because those are the ones its guard covers. No
    string matching on the recorded reasons: the assertion is that an entry
    exists, not what it says.
    """
    import paper_extraction
    import test_optional_record_fields as deliberate_table

    roles = {}
    for slide in load_template(PROPOSAL_TEMPLATE)["slides"]:
        for role in slide["roles"]:
            roles[role["name"]] = role

    governed = deliberate_table.PAPER_SOURCED_RECORD_FIELDS
    checked = 0
    for role_name, (slot_path, mapping) in governed.items():
        leaves = {leaf.name: leaf
                  for leaf in paper_extraction.BY_PATH[slot_path].leaves}
        field_fills = roles[role_name].get("field_fills", {})
        for field, fill in field_fills.items():
            leaf_name = mapping.get(field)
            if leaf_name is None or leaves[leaf_name].required:
                continue
            checked += 1
            assert (role_name, field) in deliberate_table.MARKER_IS_DELIBERATE, (
                f"the template says a human fills {role_name}.{field} ({fill}), so "
                f"the marker on it is wanted rather than a defect — record why in "
                f"MARKER_IS_DELIBERATE, the same as the fields already there"
            )
    assert checked == 2, (
        f"expected slide 6's week and owner to be the governed pair, got {checked}")
