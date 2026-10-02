"""A missing marker means a source exists and did not arrive. Nothing else.

The defect. The live deck of 2026-08-19 rendered slide 3 with five component
cards, each carrying a number, a bold title and a description — and each carrying
a `[MISSING: kicker]` chip beside them. The cards were complete. Nothing was
missing. `paper_extraction` declares `platform_layers[].kicker` optional and its
own guidance says the leaf is "left out where it does not" state a category word,
so no kicker was ever going to arrive for that paper, on any run.

Every layer was behaving as built. `second_pass._record` writes a key with an
empty value for every leaf that `applies`, which by its documented rule means
"marked on the deck". `data_source_adapter` stamps every declared field of the
record. `prompt_assembler` marks a field a record carries empty. So the prompt
asked for `kicker: [MISSING: kicker]` five times and the renderer faithfully
rendered it. Render fidelity held; the deck still told a reviewer that five
sourced values had gone astray.

The fix is a declaration, not a special case. A `list_of_records` field may now be
marked `(optional)` in the template, exactly as a scalar role can be, and an empty
optional field emits no line and no marker. Whether a card still reads as complete
without a field is a question about the SLIDE, so the template is where it is
answered.

`test_a_declared_optional_source_leaf_is_optional_on_the_deck` is the part that
keeps this from happening again: it walks every record role fed by a
`paper_extraction` slot and fails when an optional source leaf maps to a field the
template does not declare optional. Adding a leaf the paper may not state now reds
here unless somebody decides, in writing, which of the two things the deck should
do about it.

Run with: python3 -m pytest tests/test_optional_record_fields.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import paper_extraction  # noqa: E402
from prompt_assembler import assemble_prompt  # noqa: E402
from template_loader import load_template, _parse_fields  # noqa: E402

_ROOT = os.path.join(os.path.dirname(__file__), "..")
PROPOSAL_TEMPLATE = os.path.join(_ROOT, "templates", "proposal-template.md")
STATUS_TEMPLATE = os.path.join(_ROOT, "templates", "status-template.md")


def _roles(template):
    """Every content role in a loaded template, by name."""
    roles = {role["name"]: role for role in template["recurring_fields"]}
    for slide in template["slides"]:
        for role in slide["roles"]:
            roles[role["name"]] = role
    return roles


# --- the template annotation ------------------------------------------------

def test_a_field_can_be_declared_optional_beside_its_name():
    fields, optional = _parse_fields("number, kicker (optional), title")
    assert fields == ["number", "kicker", "title"], fields
    assert optional == ["kicker"], optional


def test_the_flag_is_stripped_from_the_field_schema():
    """`fields` is the record's schema and every consumer reads it as such, so a
    flag must not leak into a field name and quietly stop matching the data."""
    fields, _optional = _parse_fields("a (optional), b(optional) , c")
    assert fields == ["a", "b", "c"], fields


def test_a_role_declaring_no_optional_field_carries_no_such_key():
    """Byte-for-byte the dict it was before per-field flags existed."""
    fields, optional = _parse_fields("a, b")
    assert (fields, optional) == (["a", "b"], [])
    role = _roles(load_template(PROPOSAL_TEMPLATE))["milestones"]
    assert "optional_fields" not in role, role


def test_slide_threes_kicker_is_the_declared_optional_field():
    role = _roles(load_template(PROPOSAL_TEMPLATE))["components"]
    assert role["fields"] == ["number", "kicker", "title", "description"]
    assert role["optional_fields"] == ["kicker"], role


# --- the assembler's three cases --------------------------------------------

def _one_record_prompt(record, fields="number, kicker (optional), title"):
    """A one-slide template with one record role, assembled over one record."""
    names, optional = _parse_fields(fields)
    role = {"name": "components", "type": "list_of_records", "fields": names}
    if optional:
        role["optional_fields"] = optional
    template = {
        "deck_type": "proposal", "recurring_fields": [],
        "slides": [{"number": 1, "title": "The Platform", "roles": [role]}],
    }
    return assemble_prompt(template, {"components": [record]})


def test_an_empty_optional_field_emits_no_line_and_no_marker():
    """The defect, in one assertion."""
    prompt = _one_record_prompt(
        {"number": "01", "kicker": None, "title": "RAN Equipment"})
    assert "[MISSING: kicker]" not in prompt, prompt
    assert "kicker" not in prompt, prompt
    assert "title: RAN Equipment" in prompt


def test_an_optional_field_the_source_did_state_still_renders():
    """Optional is about absence. A stated value is not affected by it."""
    prompt = _one_record_prompt(
        {"number": "01", "kicker": "INPUTS · FIELD CAPTURE", "title": "Capture"})
    assert "kicker: INPUTS · FIELD CAPTURE" in prompt, prompt


def test_a_field_that_is_not_optional_still_marks_when_it_is_empty():
    """The rule that has to keep working: a load-bearing per-record field left
    empty is exactly what the marker is for, and this must not have become a
    blanket amnesty for every empty field in a record."""
    prompt = _one_record_prompt({"number": "01", "kicker": "IN", "title": ""})
    assert "title: [MISSING: title]" in prompt, prompt


def test_the_empty_string_counts_as_absent_not_as_a_value():
    """`second_pass._record` writes an unobtained leaf as `""`, not as `None`, so
    a rule that only caught `None` would have fixed nothing on the real path."""
    for empty in (None, "", []):
        prompt = _one_record_prompt(
            {"number": "01", "kicker": empty, "title": "RAN Equipment"})
        assert "[MISSING: kicker]" not in prompt, (empty, prompt)


def test_a_field_the_record_never_carried_is_still_silent():
    prompt = _one_record_prompt({"number": "01", "title": "RAN Equipment"})
    assert "kicker" not in prompt, prompt


# --- the invariant that keeps it from happening again ------------------------

# Where each record field of a paper-sourced record role comes from: the
# `paper_extraction` slot path, and the leaf inside it. `None` means the adapter
# composes the field rather than sourcing it (`number` is the record's own
# ordinal, which no paper states — see `second_pass._record`).
#
# Only the two record roles fed by a paper-extraction slot are listed. The rest
# (`timeline_rows`, `milestones`, `commercial_rows`, `value_mapping`, and the
# status template's roles) come from the deterministic parsers, from studio input,
# or from figures the adapter composes, and this invariant has nothing to say
# about them: it is about the one mismatch that produced the 2026-08-19 defect,
# a leaf the extractor declares optional reaching a field the deck calls required.
PAPER_SOURCED_RECORD_FIELDS = {
    "components": ("platform_layers", {
        "number": None,
        "kicker": "kicker",
        "title": "title",
        "description": "body",
    }),
    "action_items": ("next_steps", {
        "number": None,
        "week": "week",
        "owner": "owner",
        "title": "title",
        "description": "body",
    }),
}

# A field whose source leaf is optional and which the template deliberately does
# NOT declare optional, because the deck wants the marker. Each entry is a
# decision somebody made, with the reason, not an exemption to grow casually.
MARKER_IS_DELIBERATE = {
    ("action_items", "week"):
        "Slide 6's week chip is reviewer input by design: `paper_writing` refuses "
        "to generate a commitment about a date, and the marker is how the deck "
        "asks a human for one (handoff 2026-08-19, decided, do not reopen).",
    ("action_items", "owner"):
        "Slide 6's owner chip, same ruling: a commitment about a PERSON is not "
        "the generator's to invent, so the marker asks the reviewer for it.",
    ("action_items", "description"):
        "An action with no sentence saying what it is reads as incomplete on the "
        "slide, so an absent body is worth reporting.",
    ("components", "description"):
        "A component card with a title and no description reads as incomplete, "
        "so an absent body is worth reporting.",
}


def _leaves(slot_path):
    return {leaf.name: leaf for leaf in paper_extraction.BY_PATH[slot_path].leaves}


def test_the_mapping_table_still_describes_the_real_templates():
    """The invariant below is only worth anything if its table has not drifted
    from the template it claims to describe."""
    roles = _roles(load_template(PROPOSAL_TEMPLATE))
    for role_name, (slot_path, mapping) in PAPER_SOURCED_RECORD_FIELDS.items():
        role = roles[role_name]
        assert role["type"] == "list_of_records", role
        assert set(role["fields"]) == set(mapping), (role_name, role["fields"])
        leaves = _leaves(slot_path)
        for field, leaf_name in mapping.items():
            assert leaf_name is None or leaf_name in leaves, (field, leaf_name)


def test_a_declared_optional_source_leaf_is_optional_on_the_deck():
    """The guard. A leaf the extractor says the paper may not state must not reach
    a deck field the template calls required — that is precisely the mismatch that
    put five `[MISSING: kicker]` chips on a slide of five complete cards.

    Adding such a leaf reds here until somebody decides which of the two things
    the deck should do: declare the field `(optional)` so its absence is silent,
    or record in `MARKER_IS_DELIBERATE` why the marker is wanted.
    """
    roles = _roles(load_template(PROPOSAL_TEMPLATE))
    for role_name, (slot_path, mapping) in PAPER_SOURCED_RECORD_FIELDS.items():
        role = roles[role_name]
        declared_optional = set(role.get("optional_fields", ()))
        leaves = _leaves(slot_path)
        for field, leaf_name in mapping.items():
            if leaf_name is None:
                continue
            leaf = leaves[leaf_name]
            if leaf.required:
                # A record missing this leaf is dropped whole upstream, so the
                # field cannot arrive empty and the marker cannot misfire.
                continue
            deliberate = (role_name, field) in MARKER_IS_DELIBERATE
            assert field in declared_optional or deliberate, (
                f"{role_name}.{field} comes from the optional leaf "
                f"{slot_path}.{leaf_name}, so a source that states nothing puts "
                f"[MISSING: {field}] on a slide that is not missing anything. "
                f"Declare the field `(optional)` in the template, or add "
                f"({role_name!r}, {field!r}) to MARKER_IS_DELIBERATE with the "
                f"reason the deck wants the marker."
            )


def test_a_deliberate_marker_is_not_also_declared_optional():
    """The two answers are exclusive. A field declared optional emits no marker,
    so listing it as a deliberate marker would record a decision the deck does not
    implement."""
    roles = _roles(load_template(PROPOSAL_TEMPLATE))
    for (role_name, field), reason in MARKER_IS_DELIBERATE.items():
        assert reason.strip(), (role_name, field)
        declared_optional = set(roles[role_name].get("optional_fields", ()))
        assert field not in declared_optional, (
            f"{role_name}.{field} is both declared optional and recorded as a "
            "deliberate marker; only one of those can be true"
        )


def test_no_status_template_record_field_is_paper_sourced():
    """The table above covers the proposal path only, and says so. This pins the
    claim: a status deck's records come from the check-in packet's own parsers, so
    no paper-extraction leaf reaches one and none of them can carry this defect."""
    status_records = {name for name, role in _roles(load_template(STATUS_TEMPLATE)).items()
                      if role["type"] == "list_of_records"}
    assert status_records, "the status template must still declare record roles"
    assert not status_records & set(PAPER_SOURCED_RECORD_FIELDS)


# --- the defect itself, through the real seam --------------------------------

def _packet_whose_paper_names_no_kicker():
    """A packet built by the real provider from a paper that names components but
    no category word for any of them — the shape the live run of 2026-08-19 hit.

    The spans are real lines of the fixture paper, because the extraction pass
    verifies every span against the paper text, and each item states `title` and
    `body` and no `kicker` at all. `second_pass._record` still writes the leaf as
    an empty string, since `kicker` applies to the record's shape, which is why a
    rule that only caught a missing KEY would have fixed nothing here.
    """
    from live_proposal_provider import LiveProposalProvider
    from test_live_seam import CLIENTS, STAMP, StubClient
    from test_paper_extraction import answer, field, item
    from test_second_pass import extractor_returning, paper

    spec = CLIENTS["one"]
    lines = [line.strip() for line in paper(spec["shape"]).splitlines()
             if line.strip() and not line.startswith(("#", "|", "<", "["))
             and len(line.strip()) > 60]
    layers = field("platform_layers", *(
        item(line, "The Proposed Solution", title=line, body=line)
        for line in lines[:3]))
    provider = LiveProposalProvider(StubClient(spec), generated_at=STAMP,
                                    extractor=extractor_returning(answer(layers)))
    request = {"company": spec["company"]["name"],
               "project": spec["project"]["name"],
               "pe_firm": spec["pe_firm"],
               "proposal_date": "2026-08-15",
               "options": {"min_data_completeness": 0.70}}
    envelope = provider.poll(provider.submit(request))["envelope"]
    assert envelope["status"] == "ok", envelope
    return envelope["packet"], request


def test_a_paper_that_names_no_kicker_puts_no_marker_on_slide_three():
    """The reported defect, reproduced end to end and then measured gone.

    Both directions are asserted in one test on purpose: the same packet through
    the same template with the `(optional)` flag stripped still produces one
    marker per component, which is what proves this fixture would have shipped the
    defect rather than passing for some unrelated reason.
    """
    from data_source_adapter import map_packet

    packet, request = _packet_whose_paper_names_no_kicker()
    placeholder_map = map_packet(packet, request)
    components = placeholder_map["components"]
    assert len(components) == 3, components
    # The packet carries the leaf EMPTY, not absent. This is the real path.
    assert all(component["kicker"] == "" for component in components), components
    assert all(component["title"] for component in components), components

    template = load_template(PROPOSAL_TEMPLATE)
    prompt = assemble_prompt(template, placeholder_map)
    assert "[MISSING: kicker]" not in prompt, (
        "a paper that names no category word is not missing one"
    )

    # Same packet, same template, flag stripped: the defect comes back.
    for slide in template["slides"]:
        for role in slide["roles"]:
            if role["name"] == "components":
                role.pop("optional_fields", None)
    before = assemble_prompt(template, placeholder_map)
    assert before.count("[MISSING: kicker]") == 3, (
        "the fixture must actually reproduce the defect without the flag"
    )


def test_the_titles_and_bodies_still_arrive_on_those_cards():
    """The fix must not have made the card quiet about anything else. A component
    is still expected to carry a title and a description, and those still render."""
    from data_source_adapter import map_packet

    packet, request = _packet_whose_paper_names_no_kicker()
    prompt = assemble_prompt(load_template(PROPOSAL_TEMPLATE),
                             map_packet(packet, request))
    assert "[MISSING: title]" not in prompt
    assert "[MISSING: description]" not in prompt
    assert prompt.count("number: 0") >= 3, "the ordinals still reach the cards"


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all optional-record-field tests passed")
