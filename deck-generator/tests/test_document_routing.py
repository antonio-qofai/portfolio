"""Each attached document writes the opportunity its own front matter names.

Phase 2 of `build-plan-per-opportunity-slides.md`. Until 2026-09-22 every
attachment joined every opportunity's chain, so two PRDs and two opportunities
produced two slides written from whichever PRD was attached first.

Every provider test here asserts on the GENERATED PROMPT, which is what the
renderer reads, and on a duration: a number a client acts on, and the fact two
PRDs are least likely to share by accident.
"""

import os
import pathlib
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import document_routing
from base_document import Upload
from data_source_adapter import _section_yaml, _split_sections, map_packet
from document_text import extract_text
from prompt_assembler import assemble_prompt
from template_loader import load_template
from test_base_document import (CLIENTS, _slides, request,
                                two_opportunity_envelope)

TEMPLATE = "templates/proposal-template.md"


def prd(opportunity=None, weeks=10):
    """A QofAI PRD stating an `weeks`-week build, naming `opportunity` if given.

    Its §6 prose and its §6 table agree, so `prd_loyalty_guard` passes it, and
    its milestones span the same weeks."""
    front = "| Prepared for | Stub Company |\n| --- | --- |\n"
    if opportunity is not None:
        front += f"| Opportunity | {opportunity} |\n"
    step = weeks // 2
    return f"""# A PRD
{front}
# 3. Goals & Success Metrics

| KPI | Baseline | Target |
| --- | --- | --- |
| EBITDA margin uplift | None | +0.50 to +2.50 pts |

# 6. Phased Scope

The build is a focused {weeks}-week program.

| Phase | Focus | Key Deliverables |
| --- | --- | --- |
| Phase 1 Build Wks 1–{step} | Build | Pipeline |
| Phase 2 Prove Wks {step + 1}–{weeks} | Prove | Acceptance |

# 12. Build Milestones & Rollout

| Milestone | Target | Deliverable | Exit Criteria |
| --- | --- | --- | --- |
| M1 | Wks 1–{step} | Pipeline | Live |
| M2 | Wks {step + 1}–{weeks} | Acceptance | Accepted |

# 13. Open Questions
"""


def upload(text, name):
    return Upload(filename=name, data=text.encode("utf-8"))


def document(text, name="a.md"):
    result = extract_text(text.encode("utf-8"), name)
    assert result.ok, result
    return result


PICKED = [{"id": "OPP-ONE", "title": "First Opportunity"},
          {"id": "OPP-TWO", "title": "Second Opportunity"}]
LISTED = [{"id": "OPP-ONE", "label": "First Opportunity"},
          {"id": "OPP-TWO", "label": "Second Opportunity"},
          {"id": "OPP-THREE", "label": "Third Thing Entirely"}]


# --- the routing itself ------------------------------------------------------

def test_a_document_goes_to_the_opportunity_its_front_matter_names():
    routes = document_routing.route(
        [document(prd("Second Opportunity"))], PICKED, LISTED)
    assert routes[0].opportunity_id == "OPP-TWO"
    assert routes[0].stated == "Second Opportunity"


def test_the_filename_decides_nothing():
    """The Contoso PRDs are named after their opportunities, and that is
    a coincidence the corpus does not guarantee."""
    routes = document_routing.route(
        [document(prd("Second Opportunity"), name="First Opportunity PRD.md")],
        PICKED, LISTED)
    assert routes[0].opportunity_id == "OPP-TWO"


def test_every_unplaceable_document_applies_to_the_whole_run_and_says_why():
    documents = [
        document(prd()),                                    # names nothing
        document(prd("First Opportunity (not yet published)")),
        document(prd("Nothing Listed Anywhere")),
        document(prd("Third Thing Entirely")),              # not picked
    ]
    routes = document_routing.route(documents, PICKED, LISTED)
    assert [where.routed for where in routes] == [False] * 4
    assert all(where.reason for where in routes)
    assert "not yet published" in routes[1].reason
    assert "this deck is not for" in routes[3].reason


def test_an_opportunitys_documents_keep_attach_order():
    documents = [document(prd(), "note.md"),
                 document(prd("Second Opportunity"), "two.md"),
                 document(prd("First Opportunity"), "one.md")]
    routes = document_routing.route(documents, PICKED, LISTED)
    one = document_routing.documents_for(documents, routes, "OPP-ONE")
    two = document_routing.documents_for(documents, routes, "OPP-TWO")
    assert [d.filename for d in one] == ["note.md", "one.md"]
    assert [d.filename for d in two] == ["note.md", "two.md"]


def test_nothing_needs_a_listing_when_no_document_names_an_opportunity():
    assert not document_routing.needs_listing([document(prd())])
    assert document_routing.needs_listing([document(prd("First Opportunity"))])


# --- through the real provider, asserted on the prompt -----------------------

def _prompt_slides(envelope):
    placeholder_map = map_packet(envelope["packet"], request(CLIENTS["one"],
                                                             floor=0.0))
    slides = _slides(assemble_prompt(load_template(TEMPLATE), placeholder_map))
    first = next(body for header, body in slides.items()
                 if "Opportunity" in header and "First Opportunity" in body)
    second = next(body for header, body in slides.items()
                  if "Opportunity" in header and "Second Opportunity" in body)
    return first, second


def test_two_prds_each_write_their_own_opportunity():
    """THE CASE THAT WAS BROKEN. Attached in the OPPOSITE order to the deck's,
    so attach order cannot be what gets it right."""
    envelope = two_opportunity_envelope(uploads=(
        upload(prd("Second Opportunity", weeks=6), "b.md"),
        upload(prd("First Opportunity", weeks=10), "a.md"),
    ))
    assert envelope["status"] == "ok", envelope
    first, second = _prompt_slides(envelope)

    assert "build_summary: THE 10-WEEK BUILD" in first
    assert "build_summary: THE 6-WEEK BUILD" in second
    assert [(r["filename"], r["opportunity_id"])
            for r in envelope["document_routes"]] == [
        ("b.md", "OPP-TWO"), ("a.md", "OPP-ONE")]


def test_one_prd_writes_its_opportunity_and_the_other_keeps_its_paper():
    envelope = two_opportunity_envelope(uploads=(
        upload(prd("First Opportunity", weeks=10), "a.md"),))
    assert envelope["status"] == "ok", envelope
    first, second = _prompt_slides(envelope)
    entries = _section_yaml(_split_sections(envelope["packet"])[2])["opportunities"]

    assert "build_summary: THE 10-WEEK BUILD" in first
    # The second opportunity's paper (`scenario-columns`) states a twelve-month
    # plan, and none of the PRD reaches its slide.
    assert "10-WEEK" not in second
    assert entries[1]["build_summary"]["duration_unit"] == "months"


def test_a_document_naming_no_opportunity_behaves_exactly_as_before():
    """It applies to the whole run, so both slides are written from it."""
    envelope = two_opportunity_envelope(uploads=(upload(prd(weeks=10), "a.md"),))
    assert envelope["status"] == "ok", envelope
    first, second = _prompt_slides(envelope)

    assert "build_summary: THE 10-WEEK BUILD" in first
    assert "build_summary: THE 10-WEEK BUILD" in second
    assert envelope["document_routes"][0]["opportunity_id"] == ""
    assert envelope["document_routes"][0]["reason"] == "names no opportunity"


def test_a_one_opportunity_run_routes_nothing_and_lists_nothing():
    """No new platform call and no record: every attachment belongs to the
    only opportunity there is."""
    import test_live_seam as seam
    from live_proposal_provider import LiveProposalProvider
    from test_base_document import two_opportunity_client

    spec, client = two_opportunity_client()
    calls = []
    original = client.call_tool_json

    def counting(name, arguments):
        calls.append(name)
        return original(name, arguments)

    client.call_tool_json = counting
    provider = LiveProposalProvider(client, generated_at=seam.STAMP)
    envelope = provider.poll(provider.submit(
        request(spec, floor=0.0, opportunity_id="OPP-TWO"),
        uploads=(upload(prd("First Opportunity", weeks=10), "a.md"),),
    ))["envelope"]

    assert envelope["status"] == "ok", envelope
    assert envelope["document_routes"] == []
    assert "list_opportunities" not in calls
