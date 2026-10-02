"""§14's schedule table puts its days on the steps it schedules.

Part A1 of `build-plan-phase4-flags-and-fit.md`. Two of the three PRDs in the
corpus head their interviews "(60 minutes each)" and state the days only in a
Day | Activity | Owner table, which the reader used to turn into a step of its
own. Every step it schedules then read `[MISSING: week]` on the deck: five on
output-18, all stated in the document.

Synthetic cases carry the rule everywhere; the real PRDs, which are client
documents and not committed, are checked where `PRDs Casey/` exists.
"""

import os
import sys

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, HERE)

import prd_section_parsers
from document_text import extract_text
from test_flag_audit import SCHEDULED

CORPUS = os.path.join(HERE, "..", "PRDs Casey")


def steps(text):
    return prd_section_parsers.next_steps(text)


def by_title(text):
    return {step["title"]: step for step in steps(text)}


def test_each_scheduled_step_takes_its_rows_day():
    found = by_title(SCHEDULED)
    assert found["Head of Sales: pipeline walkthrough"]["week"] == "DAYS 2–3"
    assert found["Finance Lead: the margin model"]["week"] == "DAYS 3–4"


def test_a_collapsed_step_takes_the_rows_owner_only_because_it_has_none():
    found = by_title(SCHEDULED)
    requests = next(step for title, step in found.items() if "data requests" in title.lower())
    assert (requests["week"], requests["owner"]) == ("DAY 1", "QofAI · CEO")
    # An interview keeps its own counterpart, not the row's attendee list.
    assert found["Head of Sales: pipeline walkthrough"]["owner"] == "Head of Sales"


def test_the_table_stops_being_a_step_once_its_rows_have_joined():
    assert not any("schedule" in step["title"].lower() for step in steps(SCHEDULED))
    assert [step["number"] for step in steps(SCHEDULED)] == ["01", "02", "03"]


def test_a_row_that_joins_nothing_becomes_a_step_so_nothing_is_lost():
    text = SCHEDULED.replace(
        "| Days 3–4 | Finance Lead interview | QofAI · Finance Lead |",
        "| Days 3–4 | Finance Lead interview | QofAI · Finance Lead |\n"
        "| Day 5 | Quoted price delivered | QofAI |")
    last = steps(text)[-1]
    assert (last["week"], last["owner"], last["title"]) == (
        "DAY 5", "QofAI", "Quoted price delivered")


def test_a_step_two_rows_could_schedule_keeps_its_empty_week():
    text = SCHEDULED.replace("| Days 3–4 | Finance Lead interview |",
                             "| Days 3–4 | Finance Lead and Head of Sales review |")
    assert by_title(text)["Head of Sales: pipeline walkthrough"]["week"] == ""


def test_a_whole_part_matches_and_a_loose_word_does_not():
    text = (SCHEDULED
            .replace("Head of Sales: pipeline walkthrough.",
                     "President & Owner: pricing. How prices are set.")
            .replace("| Days 2–3 | Head of Sales interview |",
                     "| Days 2–3 | President interview |"))
    assert by_title(text)["President & Owner: pricing"]["week"] == "DAYS 2–3"
    presidential = text.replace("| Days 2–3 | President interview |",
                                "| Days 2–3 | Presidential review |")
    assert by_title(presidential)["President & Owner: pricing"]["week"] == ""


def test_a_table_no_row_of_which_joins_keeps_its_step():
    text = SCHEDULED.replace("Head of Sales interview", "Kickoff call").replace(
        "Finance Lead interview", "Wrap-up").replace(
        "Data requests issued; interviews scheduled", "Planning")
    assert any("schedule" in step["title"].lower() for step in steps(text))


def test_a_heading_day_range_is_never_overridden():
    text = SCHEDULED.replace("### Interviews (60 minutes each)",
                             "### Interviews (Days 1–3; 60 minutes each)")
    assert by_title(text)["Head of Sales: pipeline walkthrough"]["week"] == "DAYS 1–3"


# --- the real corpus, where it is checked out ---------------------------------

EXPECTED = {
    "QofAI_Contoso_Agentic_Client Onboarding_Onboarding_PRD_090726.docx": [
        ("DAYS 2–3", "VP Operations"), ("DAYS 2–3", "CCO"),
        ("DAYS 3–4", "Director of Client Services"), ("DAY 1", "QofAI · COO"),
        ("DAY 5", "QofAI")],
    "TIG_Quoting_Margin_Intelligence_PRD_v1_DRAFT.docx": [
        ("DAYS 2–3", "Operations Manager"), ("DAYS 3–4", "President & Owner"),
        ("DAYS 2–3", "Sales Team"), ("DAY 1", "QofAI · TIG President"),
        ("DAY 5", "QofAI")],
}


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_the_real_schedule_tables_join(name):
    path = os.path.join(CORPUS, name)
    if not os.path.isfile(path):
        pytest.skip(f"{name} is not checked out (client document)")
    text = extract_text(open(path, "rb").read(), path).text
    assert [(s["week"], s["owner"]) for s in steps(text)] == EXPECTED[name]
    assert not any(s["title"] == "Sprint schedule" for s in steps(text))


def test_the_joined_days_reach_the_generated_prompt():
    """On the artifact: a PRD run through the real provider, mapped and
    assembled, and the Next Steps slide read off the prompt."""
    from base_document import Upload
    from data_source_adapter import map_packet
    from live_proposal_provider import LiveProposalProvider
    from prompt_assembler import assemble_prompt
    from template_loader import load_template
    from test_base_document import CLIENTS, STAMP, StubClient, request
    from test_document_routing import prd

    spec = CLIENTS["one"]
    provider = LiveProposalProvider(StubClient(spec), generated_at=STAMP)
    body = request(spec, floor=0.0)
    envelope = provider.poll(provider.submit(body, uploads=(
        Upload(filename="a.md", data=(prd() + SCHEDULED).encode("utf-8")),
    )))["envelope"]
    assert envelope["status"] == "ok", envelope
    prompt = assemble_prompt(load_template("templates/proposal-template.md"),
                             map_packet(envelope["packet"], body))
    steps_slide = " ".join(next(chunk for chunk in prompt.split("## Slide ")
                                if chunk.split("\n", 1)[0].endswith(
                                    "Next Steps (numbered actions with owners)")
                                ).split())
    assert "week: DAYS 2–3 owner: Head of Sales" in steps_slide
    assert "week: DAYS 3–4 owner: Finance Lead" in steps_slide
    assert "week: DAY 1 owner: QofAI · CEO" in steps_slide
    assert "[MISSING: week]" not in steps_slide
    assert "Sprint schedule" not in steps_slide
