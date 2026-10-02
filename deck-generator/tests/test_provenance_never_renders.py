"""Provenance is for the reviewer, and the deck says nothing about sources.

Settled 2026-09-07 (NEXT-BUILD-PHASE part two, answer 3): the client-facing deck
says nothing about which document filled which field, because a source
annotation does not belong in front of a client, while the studio tells the
reviewer everything. This file is the second half of that sentence, held
mechanically.

Two lists are covered, not one. `Packet.conflicts` is the fields two documents
answered differently, and `Packet.set_aside` is the figures the baseline group
rule declined to take. Both are reviewer-facing on identical terms, so both are
held out of the deck by the same five layers, and the second one matters more
here rather than less: a set-aside figure is a real number a real document
stated, sitting in the packet, that the deck must not print.

And since 2026-09-13 a figure also names the OPPORTUNITY it was read FOR
(item 15). A deck can carry more than one opportunity, and an attached document
is the base for every one of them, so the same document read twice produces
figures about different things and "which document" stopped being enough to say
which slide a figure belongs on. `opportunity` is provenance on the same terms
as `document` and is held out of the deck by the same layers.

ONE HONEST LIMIT, AND IT IS NOT SOFTENED. An opportunity's TITLE is legitimate
deck content: slide 2's headline is the opportunity's own name, and the deck is
supposed to print it. So the title cannot be canaried here, the same way the
attachment record's TEXT cannot be. What a leak would carry is the opportunity
ID, which names a row in the platform's registry and belongs on no slide, and
the figure-level attribution that says which reading produced a value. Those are
what is asserted, and the title is deliberately left alone.

And since 2026-09-07 a third thing rides beside the packet: the ATTACHMENT
RECORD (`src/attachment_record.py`), which a saved deck keeps so a reviewer can
ask later what it was built from. It carries a filename, a kind, a size and a
SHA-256, and a filename is a source annotation like any other, so it is held out
of the deck by the same layers. What it CANNOT be canaried on is its text: the
text is the base document, so its figures are the deck's figures by design and
their presence is the item working rather than a leak. The name and the hash are
what a leak would carry, and they are what is asserted.

The proof is layered, because "nothing reaches the deck" is a claim about a
pipeline and not about one function.

  L1. The PACKET DOCUMENT, which is what the pipeline hands forward, carries no
      document name and no conflict. It is the packet's only serialisation, so
      anything absent here cannot appear downstream by accident.
  L2. The PLACEHOLDER MAP, which is what the deck's roles are filled from,
      carries none of it either.
  L3. The PROMPT, which is the last thing a human writes and the only thing the
      render leg reads. A deck cannot say what the prompt does not carry.
  L4. The GUARD BOUNDARY. `display_text_guard` and `text_gate` are what read a
      rendered deck's own words, and run over deck text built from a merged
      packet they see no document name and no set-aside figure.
  L5. STRUCTURALLY. No module on the render path reads `Packet.conflicts` or a
      figure's `document`, asserted against the source rather than against an
      output, so a future edit that starts reading one fails here.

The attachment's filename and its figures are deliberately distinctive, so a
leak is a substring search rather than an interpretation.
"""

import ast
import dataclasses
import hashlib
import pathlib

import pytest

import base_document
import display_text_guard
import source_span
import text_gate
from base_document import Upload, merge_packets
from data_source_adapter import map_packet
from deck_generator import generate_deck_prompt
from live_proposal_provider import LiveProposalProvider
from packet_assembly import EBITDA, MARGIN, REVENUE, assemble
from source_span import Document
from test_base_document import (CONTESTED_ATTACHMENT, CONTESTED_PAPER, FLOOR,
                                OLDER_FULL_BASELINE, REVENUE_ONLY, SPARSE,
                                request)
from test_live_seam import CLIENTS, STAMP, StubClient
from test_paper_extraction import paper

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"

# The filename a leak would carry, and figures no paper in the corpus states.
LEAKY_NAME = "provenance-canary-attachment.md"
ATTACHED = Document(name=LEAKY_NAME, kind="markdown")


def attachment_hash(text):
    """The SHA-256 the attachment record carries for these bytes, which is the
    other half of what a record leak would put on a slide. Computed the way the
    record computes it rather than read off one, so a record that stopped
    hashing the file would fail here instead of agreeing with itself."""
    return hashlib.sha256(text.encode()).hexdigest()


# What the paper says and the attachment overrides, so a set-aside value showing
# up on a deck is as detectable as a document name.
SET_ASIDE = ("$12.0M", "12000000", "$3.0M", "3000000", "25.0%")


def contested_packet():
    return merge_packets([
        assemble("OPP-ONE", CONTESTED_ATTACHMENT, ATTACHED),
        assemble("OPP-ONE", CONTESTED_PAPER, source_span.PAPER),
    ])


def declined_packet():
    """The other shape: an attachment stating revenue only, so the group rule
    declines the paper's EBITDA and margin and the packet records them."""
    return merge_packets([
        assemble("OPP-ONE", REVENUE_ONLY, ATTACHED),
        assemble("OPP-ONE", OLDER_FULL_BASELINE, source_span.PAPER),
    ])


# What the group rule declined, in the notations a deck could print it in. These
# are the figures most at risk of leaking, because unlike a conflict's
# alternative they are not superseded by a value the deck carries: the field is
# reported MISSING, so a leak would put the only number on the slide.
DECLINED_VALUES = ("$3.0M", "3000000", "25.0%")

# The common PRD shape for the provider runs below: it prices the work and
# restates no margin, so the paper's margin is declined by the group rule while
# the two figures both documents state are contested. Scored high enough that a
# prompt is actually produced, which a revenue-only attachment is not.
NO_MARGIN = """# Engagement plan

### Current State

| Metric | CY 2024 | CY 2025 | Change |
|---|---|---|---|
| **Revenue** | $30.1M | $38.4M | +27.6% |
| **EBITDA** | $10.2M | $14.6M | +43.1% |
"""


def envelope_with_attachment(text=CONTESTED_ATTACHMENT, spec=CLIENTS["one"]):
    provider = LiveProposalProvider(StubClient(spec), generated_at=STAMP)
    handle = provider.submit(
        request(spec, floor=0.0),
        uploads=[Upload(filename=LEAKY_NAME, data=text.encode())],
    )
    return provider.poll(handle)["envelope"]


def assert_says_nothing_about_sources(rendered, *, where):
    lowered = rendered.lower()
    assert LEAKY_NAME not in rendered, where
    assert "provenance-canary" not in lowered, where
    for token in ("conflict", "set_aside", "set aside", "declined",
                  "opportunity_paper", "base_document", "which document",
                  # The attachment record's own two: the hash by name, and any
                  # hash of a document this file attaches.
                  "sha256", attachment_hash(CONTESTED_ATTACHMENT),
                  attachment_hash(NO_MARGIN)):
        assert token not in lowered, f"{token} reached {where}"


# --- L1: the packet document -------------------------------------------------


def test_the_packet_document_names_no_document_and_no_conflict():
    built = envelope_with_attachment()
    assert built["status"] == "ok"
    assert_says_nothing_about_sources(built["packet"], where="the packet document")


def test_the_packet_document_carries_none_of_the_set_aside_figures():
    """The attachment won every baseline field, so the paper's own figures are
    what a provenance leak would drag along behind them."""
    built = envelope_with_attachment()
    for value in SET_ASIDE:
        assert value not in built["packet"], value


def without_provenance(packet):
    """The same packet with both reviewer-facing lists dropped and every
    figure's document swapped for the paper's, which is what a packet looked
    like before today."""
    swapped = tuple(
        dataclasses.replace(figure, document=source_span.PAPER)
        for figure in packet.fields
    )
    cases = tuple(
        dataclasses.replace(
            case,
            direct_uplift_usd_yr=dataclasses.replace(
                case.direct_uplift_usd_yr, document=source_span.PAPER),
            margin_gain_pp=(
                None if case.margin_gain_pp is None
                else dataclasses.replace(case.margin_gain_pp,
                                         document=source_span.PAPER)
            ),
        )
        for case in packet.scenarios
    )
    return dataclasses.replace(packet, fields=swapped, scenarios=cases,
                               conflicts=(), set_aside=())


def test_the_packet_document_is_a_pure_function_of_the_values():
    """The strongest form of L1. Two packets holding the same figures and
    different provenance serialise to the same markdown, byte for byte, so the
    serialisation cannot be carrying provenance in any form: not the conflict
    list, not a document name, not a marker that a field was contested."""
    import packet_document

    contested = contested_packet()
    plain = without_provenance(contested)
    assert contested.conflicts and not plain.conflicts
    assert {figure.document for figure in contested.fields} == {ATTACHED}
    assert {figure.document for figure in plain.fields} == {source_span.PAPER}
    assert packet_document.build(packet=contested) == packet_document.build(
        packet=plain
    )


def test_the_packet_document_is_the_same_with_and_without_the_set_aside_list():
    """The same proof for the other list, and the one that needs it more: a
    set-aside figure is a number a document really stated, sitting in the
    packet, for a field the deck reports as missing. A leak would put the only
    number on the slide."""
    import packet_document

    withheld = declined_packet()
    plain = without_provenance(withheld)
    assert withheld.set_aside and not plain.set_aside
    assert packet_document.build(packet=withheld) == packet_document.build(
        packet=plain
    )


# --- L2: the placeholder map -------------------------------------------------


def test_the_placeholder_map_carries_no_document_name_and_no_conflict():
    built = envelope_with_attachment()
    placeholders = map_packet(built["packet"], built["request_echo"])
    assert_says_nothing_about_sources(str(placeholders), where="the placeholder map")


def test_no_placeholder_key_is_about_a_source():
    built = envelope_with_attachment()
    placeholders = map_packet(built["packet"], built["request_echo"])
    for key in placeholders:
        lowered = str(key).lower()
        assert "conflict" not in lowered
        assert "document" not in lowered
        assert "source" not in lowered or lowered.startswith("_")


# --- L3: the prompt, which is all the render leg reads -----------------------


def test_the_prompt_says_nothing_about_sources():
    spec = CLIENTS["one"]
    result = generate_deck_prompt(
        "proposal", spec["company"]["name"], spec["project"]["name"],
        LiveProposalProvider(StubClient(spec), generated_at=STAMP),
        pe_firm=spec["pe_firm"], proposal_date="2026-08-15",
        options={"min_data_completeness": 0.0},
        uploads=[Upload(filename=LEAKY_NAME, data=CONTESTED_ATTACHMENT.encode())],
        poll_interval=0.0, sleep=lambda _s: None,
    )
    assert result["status"] == "ok", result
    assert_says_nothing_about_sources(result["prompt"], where="the prompt")
    for value in SET_ASIDE:
        assert value not in result["prompt"], value


def test_the_same_run_with_no_attachment_produces_a_prompt_of_the_same_shape():
    """So the test above is not passing because the run failed quietly."""
    spec = CLIENTS["one"]
    common = dict(
        pe_firm=spec["pe_firm"], proposal_date="2026-08-15",
        options={"min_data_completeness": 0.0},
        poll_interval=0.0, sleep=lambda _s: None,
    )
    plain = generate_deck_prompt(
        "proposal", spec["company"]["name"], spec["project"]["name"],
        LiveProposalProvider(StubClient(spec), generated_at=STAMP), **common
    )
    attached = generate_deck_prompt(
        "proposal", spec["company"]["name"], spec["project"]["name"],
        LiveProposalProvider(StubClient(spec), generated_at=STAMP),
        uploads=[Upload(filename=LEAKY_NAME, data=CONTESTED_ATTACHMENT.encode())],
        **common
    )
    assert plain["status"] == attached["status"] == "ok"
    # Different figures, because the attachment is the base. Same shape, because
    # provenance changes what a deck says and never that it says it.
    assert plain["prompt"] != attached["prompt"]
    assert abs(len(plain["prompt"]) - len(attached["prompt"])) < len(plain["prompt"])


# --- L4: the guard boundary --------------------------------------------------


def deck_text_from(packet, request_echo, packet_md):
    """Deck-shaped HTML built from the placeholder map, which is every word a
    deck takes from a packet."""
    placeholders = map_packet(packet_md, request_echo)
    body = "\n".join(
        f'<p class="role">{value}</p>'
        for key, value in placeholders.items()
        if not str(key).startswith("_") and isinstance(value, (str, int, float))
    )
    return f"<html><body><section class=\"slide\">{body}</section></body></html>"


def test_the_display_text_guard_sees_no_document_name_and_no_set_aside_figure():
    built = envelope_with_attachment()
    html = deck_text_from(contested_packet(), built["request_echo"], built["packet"])
    display_text_guard.check_display_text(html, where="a deck built from an attachment")
    visible = display_text_guard.display_text(html)
    assert_says_nothing_about_sources(visible, where="the deck's display text")
    for value in SET_ASIDE:
        assert value not in visible, value


def test_the_text_gate_sees_no_document_name_and_flags_nothing_about_sources():
    built = envelope_with_attachment()
    html = deck_text_from(contested_packet(), built["request_echo"], built["packet"])
    gated = text_gate.apply_text_gate(html)
    rendered = gated["html"] if isinstance(gated, dict) else str(gated)
    assert_says_nothing_about_sources(rendered, where="the gated deck")


# --- L5: structurally, so a future edit fails here ---------------------------


# Every module the packet passes through on its way to a rendered deck. None of
# them has any business reading provenance, and the two that produce it are not
# on the list.
RENDER_PATH = (
    "packet_document.py", "packet_fill.py", "data_source_adapter.py",
    "deck_generator.py", "deck_renderer.py", "coverage_guard.py",
    "render_guard.py", "prompt_assembler.py", "layout_guard.py",
    "panel_fit.py", "display_text_guard.py", "text_gate.py", "voice_pass.py",
    "html_edit_layer.py", "html_edit_interpreter.py", "design_export_prompt.py",
)


# The attribute names provenance is reached through. Read off the parsed source
# rather than by substring, because several of these modules take a parameter
# called `document` (the deck's own HTML) and two carry attachments through in a
# docstring or a dict key, and none of that is a read of a figure's provenance.
# `attachments` is here for the same reason as the other three: the modules that
# carry the record hand a dict along by key and never reach into it, so a module
# that started reading one has started reading provenance.
PROVENANCE_ATTRIBUTES = {"conflicts", "set_aside", "document", "attachments",
                         # Which opportunity a figure was read FOR (item 15).
                         # It needs no exemption, unlike `document`: no module
                         # on this path reads an attribute by this name today,
                         # so a module that starts has started reading
                         # provenance.
                         "opportunity",
                         # The report's own two model-leg lists (2026-09-08).
                         # `provenance_report` composes them for the studio; a
                         # module on this path reaching for one has started
                         # reading provenance, whatever it means to do with it.
                         "quoted", "counts"}


def attributes_read(name):
    tree = ast.parse((SRC / name).read_text(), filename=name)
    return {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}


def modules_imported(name):
    tree = ast.parse((SRC / name).read_text(), filename=name)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


@pytest.mark.parametrize("name", RENDER_PATH)
def test_no_module_on_the_render_path_reads_provenance(name):
    assert not attributes_read(name) & PROVENANCE_ATTRIBUTES, name
    assert "base_document" not in modules_imported(name), name
    assert "source_span" not in modules_imported(name), name
    assert "attachment_record" not in modules_imported(name), name


def test_the_three_modules_that_do_produce_it_are_not_on_the_render_path():
    """A guard on the guard: if provenance moved into a rendering module, the
    test above would have to be edited, and this says which files are allowed to
    know about it at all."""
    producers = {"source_span.py", "base_document.py", "attachment_record.py"}
    assert not producers & set(RENDER_PATH)
    for name in producers:
        assert (SRC / name).exists()


def test_the_conflict_list_is_reachable_from_the_packet_the_provider_returned():
    """The other side of the same coin, and the reason none of the above is
    vacuous: the provenance exists, it is just not on the deck. The studio
    reaches it in step 4."""
    packet = contested_packet()
    assert packet.conflicts
    assert all(answer.document for conflict in packet.conflicts
               for answer in conflict.answers)


# --- the set-aside list, through every layer --------------------------------


def test_the_packet_document_carries_no_figure_the_group_rule_declined():
    """Asserted on a packet whose every value is authored here, so "the paper's
    EBITDA is not in this document" is an exact claim rather than a guess about
    a corpus excerpt's own numbers."""
    import packet_document

    withheld = declined_packet()
    assert {record.field for record in withheld.set_aside}
    written = packet_document.build(packet=withheld)
    assert_says_nothing_about_sources(written, where="the packet document")
    for value in DECLINED_VALUES:
        assert value not in written, value


def test_the_packet_document_reports_the_declined_field_as_absent():
    """The other half, and the reason the test above is not vacuous: the field
    IS in the document, as an absence with a reason. What is missing from the
    document is the number, not the field."""
    import packet_document

    written = packet_document.build(packet=declined_packet())
    assert "adjusted_ebitda_usd" in written


def test_a_run_whose_group_rule_declines_something_still_says_nothing():
    """Through the provider, at a score that clears both gates so a prompt is
    actually produced. The attachment states revenue and EBITDA and no margin,
    which is the common PRD shape, so the paper's margin is declined and the
    two figures it also states are contested.
    """
    spec = CLIENTS["one"]
    result = generate_deck_prompt(
        "proposal", spec["company"]["name"], spec["project"]["name"],
        LiveProposalProvider(StubClient(spec), generated_at=STAMP),
        pe_firm=spec["pe_firm"], proposal_date="2026-08-15",
        options={"min_data_completeness": 0.0},
        uploads=[Upload(filename=LEAKY_NAME, data=NO_MARGIN.encode())],
        poll_interval=0.0, sleep=lambda _s: None,
    )
    assert result["status"] == "ok", result
    assert_says_nothing_about_sources(result["prompt"], where="the prompt")


def test_that_run_really_did_decline_a_figure():
    """So the test above is not passing because nothing was set aside."""
    packet = merge_packets([
        assemble("OPP-ONE", NO_MARGIN, ATTACHED),
        assemble("OPP-ONE", paper(CLIENTS["one"]["shape"]), source_span.PAPER),
    ])
    assert [record.field for record in packet.set_aside] == [MARGIN]
    assert {conflict.field for conflict in packet.conflicts} >= {REVENUE, EBITDA}


def test_the_guards_see_no_figure_the_group_rule_declined():
    built = envelope_with_attachment(text=NO_MARGIN)
    html = deck_text_from(declined_packet(), built["request_echo"], built["packet"])
    display_text_guard.check_display_text(html, where="a deck built from an attachment")
    visible = display_text_guard.display_text(html)
    gated = text_gate.apply_text_gate(html)
    rendered = gated["html"] if isinstance(gated, dict) else str(gated)
    for surface, where in ((visible, "the display text"), (rendered, "the gated deck")):
        assert_says_nothing_about_sources(surface, where=where)


def test_the_set_aside_list_is_reachable_from_the_packet_the_merge_returned():
    """So none of the above is vacuous: the record exists, it is just not on the
    deck. The studio reaches it in step 4."""
    packet = declined_packet()
    assert packet.set_aside
    for record in packet.set_aside:
        assert record.answer.document
        assert record.answer.value
        assert record.answer.span.strip()
        assert record.lead


# --- the two ways a deck leaves the studio ----------------------------------
#
# Both are functions of the rendered deck HTML, which is a function of the
# prompt, which the layers above hold free of provenance. Asserted anyway, and
# separately, because "it cannot carry it because of what it is derived from" is
# an argument and these are the two files a client actually receives.


def test_the_design_prompt_export_carries_no_provenance():
    """The Claude Design handoff is built from the deck's own HTML, so it can
    only carry what the deck carries. Run over deck text built from a packet
    holding both a conflict and a set-aside figure."""
    from design_export_prompt import assemble_design_export_prompt

    built = envelope_with_attachment(text=NO_MARGIN)
    html = deck_text_from(contested_packet(), built["request_echo"], built["packet"])
    exported = assemble_design_export_prompt(html)
    assert_says_nothing_about_sources(exported, where="the design export prompt")
    for value in DECLINED_VALUES:
        assert value not in exported, value


def test_the_design_prompt_export_reads_the_deck_and_nothing_else():
    """Structurally: its only input is HTML, so there is no path by which a
    result's provenance could reach it."""
    import inspect

    from design_export_prompt import assemble_design_export_prompt

    parameters = list(
        inspect.signature(assemble_design_export_prompt).parameters
    )
    assert parameters == ["html"]


def test_the_deck_download_serves_the_file_and_adds_nothing():
    """The download hands over bytes off disk. A deck rendered from a clean
    prompt downloads clean, and the route has no result, no packet and no
    provenance in reach to add any."""
    pytest.importorskip("flask")
    import os
    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ui"))
    import app as ui_app

    built = envelope_with_attachment(text=NO_MARGIN)
    html = deck_text_from(contested_packet(), built["request_echo"], built["packet"])

    import tempfile

    root = tempfile.mkdtemp(prefix="provenance-download-")
    path = os.path.join(root, "output-1.html")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(html)

    previous = ui_app.DECKS_ROOT
    ui_app.DECKS_ROOT = root
    ui_app.app.testing = True
    try:
        client = ui_app.app.test_client()
        served = client.get(f"/download?path={path}").get_data(as_text=True)
    finally:
        ui_app.DECKS_ROOT = previous

    assert served == html
    assert_says_nothing_about_sources(served, where="the downloaded deck")
    for value in DECLINED_VALUES:
        assert value not in served, value


# --- the attachment record: the store keeps it, the deck never sees it ------
#
# Item 14, step 5. A saved deck keeps the extracted text plus each file's name,
# kind, size and SHA-256, so a reviewer can ask months later what the deck was
# built from. That record travels beside the packet exactly as provenance does,
# and it is held out of the deck by the same layers.
#
# WHAT CAN BE CANARIED AND WHAT CANNOT. The name and the hash are what a leak
# would carry, and they are asserted through every layer. The TEXT cannot be: it
# is the base document, so its figures are the deck's figures on purpose, and
# their presence is this item working rather than a leak. There is a test below
# that says so out loud, because a reader who assumes the text is canaried here
# would draw a guarantee this file does not give.


def test_the_envelope_carries_the_record_and_it_names_the_file():
    """So none of the assertions below are vacuous: the record exists, it names
    the file, it hashes it, and it is simply not on the deck."""
    import attachment_record

    built = envelope_with_attachment()
    records = built["attachments"]
    assert [row["filename"] for row in records] == [LEAKY_NAME]
    assert set(records[0]) == set(attachment_record.FIELDS)
    assert records[0]["sha256"] == attachment_hash(CONTESTED_ATTACHMENT)


def test_the_packet_document_carries_no_filename_and_no_hash():
    built = envelope_with_attachment()
    assert_says_nothing_about_sources(built["packet"], where="the packet document")
    assert attachment_hash(CONTESTED_ATTACHMENT) not in built["packet"]


def test_the_placeholder_map_carries_no_filename_and_no_hash():
    built = envelope_with_attachment()
    placeholders = str(map_packet(built["packet"], built["request_echo"]))
    assert_says_nothing_about_sources(placeholders, where="the placeholder map")
    assert attachment_hash(CONTESTED_ATTACHMENT) not in placeholders


def test_the_prompt_carries_no_filename_and_no_hash():
    """The last layer that matters, since a deck cannot say what the prompt does
    not. Run through the whole pipeline, which is what carries the record."""
    spec = CLIENTS["one"]
    result = generate_deck_prompt(
        "proposal", spec["company"]["name"], spec["project"]["name"],
        LiveProposalProvider(StubClient(spec), generated_at=STAMP),
        pe_firm=spec["pe_firm"], proposal_date="2026-08-15",
        options={"min_data_completeness": 0.0},
        uploads=[Upload(filename=LEAKY_NAME, data=CONTESTED_ATTACHMENT.encode())],
        poll_interval=0.0, sleep=lambda _s: None,
    )
    assert result["status"] == "ok", result
    assert result["attachments"], "the record has to be on the result to be saved"
    assert_says_nothing_about_sources(result["prompt"], where="the prompt")
    assert attachment_hash(CONTESTED_ATTACHMENT) not in result["prompt"]


def test_the_record_travels_beside_the_prompt_and_not_inside_it():
    """The shape of the whole arrangement in one assertion: one result, carrying
    both, and the half that names the file is not in the half the render leg
    reads."""
    spec = CLIENTS["one"]
    result = generate_deck_prompt(
        "proposal", spec["company"]["name"], spec["project"]["name"],
        LiveProposalProvider(StubClient(spec), generated_at=STAMP),
        pe_firm=spec["pe_firm"], proposal_date="2026-08-15",
        options={"min_data_completeness": 0.0},
        uploads=[Upload(filename=LEAKY_NAME, data=CONTESTED_ATTACHMENT.encode())],
        poll_interval=0.0, sleep=lambda _s: None,
    )
    assert result["attachments"][0]["filename"] == LEAKY_NAME
    assert LEAKY_NAME not in result["prompt"]


def test_the_records_text_is_the_deck_and_that_is_the_item_working():
    """Said out loud so nobody reads a guarantee here that this file does not
    give. The record's text IS the base document, so the figures in it are the
    figures on the deck by design; what must not reach the deck is the record's
    account of where they came from. This asserts both halves at once."""
    spec = CLIENTS["one"]
    result = generate_deck_prompt(
        "proposal", spec["company"]["name"], spec["project"]["name"],
        LiveProposalProvider(StubClient(spec), generated_at=STAMP),
        pe_firm=spec["pe_firm"], proposal_date="2026-08-15",
        options={"min_data_completeness": 0.0},
        uploads=[Upload(filename=LEAKY_NAME, data=NO_MARGIN.encode())],
        poll_interval=0.0, sleep=lambda _s: None,
    )
    assert result["status"] == "ok", result
    # A figure only the attachment states, on the deck, which is the point of
    # the whole item. The paper this run resolves states no such EBITDA.
    assert "$14.6M" in result["prompt"]
    # And nothing about which file it came from.
    assert_says_nothing_about_sources(result["prompt"], where="the prompt")


def test_the_guards_see_no_filename_and_no_hash():
    built = envelope_with_attachment()
    html = deck_text_from(contested_packet(), built["request_echo"], built["packet"])
    display_text_guard.check_display_text(html, where="a deck built from an attachment")
    visible = display_text_guard.display_text(html)
    gated = text_gate.apply_text_gate(html)
    rendered = gated["html"] if isinstance(gated, dict) else str(gated)
    for surface, where in ((visible, "the display text"), (rendered, "the gated deck")):
        assert_says_nothing_about_sources(surface, where=where)
        assert attachment_hash(CONTESTED_ATTACHMENT) not in surface


def test_the_record_is_plain_data_so_it_cannot_carry_an_object_downstream():
    """The other reason it is safe to hand along by key: there is nothing in it
    to reach through. A module that got hold of one gets strings and an int, not
    a packet, not a span, and not a way back to either."""
    built = envelope_with_attachment()
    for row in built["attachments"]:
        for value in row.values():
            assert isinstance(value, (str, int)), value


# --- what the model legs took: the studio counts it, the deck still says none
#
# Since 2026-09-08 the report counts what the two MODEL passes took out of the
# base document as well as what the deterministic parsers lifted, because the
# parsers read the published paper's table shapes and an attached PDF scores
# zero there whatever it contributed. Two new lists, and both are asserted here
# on the same terms as everything above.
#
# The care needed is in what a canary can mean for these. A value the extraction
# pass QUOTED and a line the writing pass WROTE are both deck-bound by design --
# that is what the passes are for -- so their presence on a deck is the pipeline
# working. What must not travel with them is the record of WHICH DOCUMENT they
# came out of, which is the filename, and that is what these tests assert.
#
# Held with the passes faked through their own real verifiers and scripted here
# rather than imported from the studio's test file, so this file needs no flask
# and stays independent of the panel that renders what it guards.

PROSE_SPAN = ("A mobile ticket capture app records the work at the job site, so "
              "the ticket\nleaves with the crew rather than arriving days later.")
WRITTEN_LINE = "One live picture of the work, from the job site to the ledger."


def prose_base():
    from test_paper_extraction import platform_paper

    text = platform_paper()
    assert PROSE_SPAN in text
    return text


def passes_for(text):
    """`(extractor, writer)`, both faked through the real verifiers."""
    from test_paper_extraction import answer, field, item
    from test_second_pass import extractor_returning, writer_returning

    quoted = answer(field("platform_layers", item(
        PROSE_SPAN, "The Proposed Solution",
        title="A mobile ticket capture app", body=PROSE_SPAN)))
    written = {"sentences": [{
        "path": "copy.platform_headline", "text": WRITTEN_LINE,
        "sections": ["The Proposed Solution"], "evidence": [PROSE_SPAN]}]}
    return extractor_returning(quoted), writer_returning(written)


def run_with_both_passes():
    text = prose_base()
    extractor, writer = passes_for(text)
    spec = CLIENTS["one"]
    result = generate_deck_prompt(
        "proposal", spec["company"]["name"], spec["project"]["name"],
        LiveProposalProvider(StubClient(spec), generated_at=STAMP,
                             extractor=extractor, writer=writer),
        pe_firm=spec["pe_firm"], proposal_date="2026-08-15",
        options={"min_data_completeness": 0.0},
        uploads=[Upload(filename=LEAKY_NAME, data=text.encode())],
        poll_interval=0.0, sleep=lambda _s: None,
    )
    assert result["status"] == "ok", result
    return result


def test_the_report_names_the_document_on_every_model_leg_row():
    """Not vacuous: the studio really does hold, per row, which document a value
    was quoted out of and which one a line was written from."""
    result = run_with_both_passes()
    report = result["provenance"]
    assert report["quoted"] and report["written"]
    for row in report["quoted"] + report["written"]:
        assert row["document"] == LEAKY_NAME


def test_and_the_prompt_from_that_same_run_names_it_nowhere():
    """The whole arrangement in two assertions: the report names the file on
    every row, the prompt names it on none."""
    result = run_with_both_passes()
    assert_says_nothing_about_sources(result["prompt"], where="the prompt")


def test_a_written_line_is_deck_copy_and_its_attribution_is_not():
    """The distinction a reader could get wrong here. The line the writing pass
    wrote IS on the deck, because writing the deck's framing copy is what that
    pass is for; what may not go with it is the record that it was written from
    an attachment, or that it was written rather than quoted."""
    result = run_with_both_passes()
    written = {row["field"]: row for row in result["provenance"]["written"]}
    assert written["copy.platform_headline"]["text"] == WRITTEN_LINE
    assert WRITTEN_LINE in result["prompt"]
    assert LEAKY_NAME not in result["prompt"]
    assert "written, not quoted" not in result["prompt"]


def test_a_quoted_value_reaches_the_deck_without_the_span_it_was_read_from():
    """The same distinction for the other pass. The VALUE is deck content; the
    span and the section it was read under are the reviewer's, and section 8
    carries those inside the packet document, which no deck renders."""
    result = run_with_both_passes()
    quoted = result["provenance"]["quoted"]
    assert quoted
    for row in quoted:
        assert row["span"]
        assert row["document"] == LEAKY_NAME
    assert "A mobile ticket capture app" in result["prompt"]
    assert LEAKY_NAME not in result["prompt"]


def test_a_run_that_rescued_a_figure_from_the_paper_still_says_nothing():
    """The chain (2026-09-12) reads a second document on one run, so two
    documents' provenance is in reach where one used to be. The studio can say
    which figure came from which; the deck names neither.

    The paper here states its figures in PROSE, which is the shape that made the
    rescue load-bearing in the first place: the parsers read nothing off it, the
    attachment states none of the roster, and the run only clears the floor
    because the second call reads the paper.
    """
    from test_base_document import PRD_PROSE, chain_answer
    from test_paper_extraction import prose_paper
    from test_second_pass import extractor_returning

    spec = CLIENTS["one"]
    text = prose_paper()
    client = StubClient(spec, opportunities=[(dict(spec["opportunity"]), text)])
    result = generate_deck_prompt(
        "proposal", spec["company"]["name"], spec["project"]["name"],
        LiveProposalProvider(client, generated_at=STAMP,
                             extractor=extractor_returning(chain_answer(text))),
        pe_firm=spec["pe_firm"], proposal_date="2026-08-15",
        options={"min_data_completeness": 0.0},
        uploads=[Upload(filename=LEAKY_NAME, data=PRD_PROSE.encode())],
        poll_interval=0.0, sleep=lambda _s: None,
    )
    assert result["status"] == "ok", result

    # The rescue really happened, so this is not passing on a run that never
    # read a second document.
    filled = {row["field"]: row for row in result["provenance"]["filled"]}
    assert filled[REVENUE]["document"] == "the published opportunity paper"
    assert filled[REVENUE]["uploaded"] is False
    assert any(row["document"] == LEAKY_NAME
               for row in result["provenance"]["quoted"])

    assert_says_nothing_about_sources(result["prompt"], where="the prompt")


# --- the opportunity a figure was read for -----------------------------------
#
# Item 15's own addition to this file. The care needed is the one the docstring
# states: the opportunity's TITLE is deck content by design, so the canary is
# the ID and the attribution, never the title.

OPPORTUNITY_ID = CLIENTS["one"]["opportunity"]["id"]
OPPORTUNITY_TITLE = CLIENTS["one"]["opportunity"]["title"]


def test_every_figure_in_a_merged_packet_names_the_opportunity_it_was_read_for():
    """Not vacuous: the attribution really is on every figure the pipeline
    produced, which is what makes the assertions below mean something."""
    packet = contested_packet()
    assert packet.fields
    for figure in packet.fields:
        assert isinstance(figure.opportunity, source_span.Opportunity)
        assert figure.opportunity.id


def test_the_packet_document_carries_no_opportunity_attribution():
    """The id appears in section 3, which is the contract's home for analytical
    source data, is parsed by nothing on the proposal path and reaches no slide.
    What must not appear is the per-figure attribution, which would say which
    reading produced which value."""
    built = envelope_with_attachment()
    assert built["status"] == "ok"
    for token in ("read for", "opportunity=", "read_for", "figure.opportunity"):
        assert token not in built["packet"].lower(), token


def test_the_placeholder_map_carries_no_opportunity_id():
    """The id names a row in the platform's registry and belongs on no slide."""
    built = envelope_with_attachment()
    rendered = repr(map_packet(built["packet"], built["request_echo"]))
    assert OPPORTUNITY_ID not in rendered


def test_the_prompt_carries_no_opportunity_id():
    result = run_with_both_passes()
    assert OPPORTUNITY_ID not in result["prompt"]


def test_and_the_title_is_deck_content_which_is_this_item_working():
    """The half this file cannot canary, asserted out loud rather than left as
    an absence a reader has to notice. Slide 2's headline is the opportunity's
    own name, so the title being on the deck is the pipeline working; the ID
    beside it is what a leak would carry."""
    result = run_with_both_passes()
    assert OPPORTUNITY_TITLE in result["prompt"]
    assert OPPORTUNITY_ID not in result["prompt"]
