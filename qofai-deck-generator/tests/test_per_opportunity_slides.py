"""One Platform, one Timeline and one Next Steps slide per opportunity.

Phase 3 of `build-plan-per-opportunity-slides.md`. Antonio, 2026-09-22, on the
first two-opportunity deck: "we need two platform slides and 2 next steps slides
for two opportunities", and the timeline repeats too ("makes no sense for two
different opportunities to have the same timeline").

Asserted on the GENERATED PROMPT, which is what the renderer reads, and the
footer arithmetic on the prompt's own slide headers. The two opportunities are
the two real Contoso build plans (labels and spans verbatim, from
`test_base_document`), so the timeline check is the 44-versus-24 weeks a
client would act on.
"""

import json
import os
import pathlib
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import paper_writing
from coverage_guard import check_coverage
from data_source_adapter import map_packet
from live_proposal_provider import LiveProposalProvider
from prompt_assembler import assemble_prompt, rendered_slide_count
from template_loader import load_template
from test_base_document import (ONBOARDING_PHASES, RECRUITING_PHASES, STAMP,
                                paper, plan_phases_chart, request,
                                two_opportunity_client)

TEMPLATE = load_template("templates/proposal-template.md")

# Each paper's own implementation section, so a written line can quote the
# paper it was written from and the span check accepts it.
STATEMENT = "The {} build runs in four phases."


def build_paper(which, phases):
    return (paper("no-timeline-chart")
            + f"\n\n## Build Plan\n\n{STATEMENT.format(which)}\n"
            + plan_phases_chart(f"{which} build", phases))


PAPERS = {"onboarding": ONBOARDING_PHASES, "recruiting": RECRUITING_PHASES,
          "third": ((("Only phase", [0, 12]),))}


def writer(text, description, requested):
    """Writes each opportunity's platform headline from its own paper."""
    which = next(name for name in PAPERS if STATEMENT.format(name) in text)
    body = json.dumps({"sentences": [{
        "path": "copy.platform_headline",
        "text": f"The {which} platform, in four phases.",
        "sections": ["Build Plan"],
        "evidence": [STATEMENT.format(which)],
    }]})
    message = type("M", (), {"content": [
        type("B", (), {"type": "text", "text": body})()]})()
    return paper_writing.read_response(text, description, requested, message)


def run(names):
    """A deck for the named opportunities, as `(prompt, placeholder_map, packet)`."""
    papers = [build_paper(name, PAPERS[name]) for name in names]
    spec, client = two_opportunity_client(first_paper=papers[0],
                                          second_paper=papers[1]
                                          if len(papers) > 1 else "")
    if len(papers) > 2:
        extra = dict(spec["opportunity"], id="OPP-THREE", title="Third Opportunity")
        client = type(client)(spec, opportunities=[
            (dict(spec["opportunity"], id="OPP-ONE", title="First Opportunity"),
             papers[0]),
            (dict(spec["opportunity"], id="OPP-TWO", title="Second Opportunity"),
             papers[1]),
            (extra, papers[2]),
        ])
    ids = ["OPP-ONE", "OPP-TWO", "OPP-THREE"][:len(names)]
    provider = LiveProposalProvider(client, generated_at=STAMP, writer=writer)
    body = request(spec, floor=0.0, opportunity_ids=ids)
    envelope = provider.poll(provider.submit(body))["envelope"]
    assert envelope["status"] == "ok", envelope
    placeholder_map = map_packet(envelope["packet"], body)
    prompt = assemble_prompt(TEMPLATE, placeholder_map)
    return prompt, placeholder_map, envelope["packet"]


def slides(prompt, title):
    """Every slide under `title`, whitespace normalised, in deck order."""
    return [" ".join(chunk.split()) for chunk in prompt.split("## Slide ")[1:]
            if title in chunk.split("\n", 1)[0]]


def headers(prompt):
    return [line for line in prompt.splitlines() if line.startswith("## Slide ")]


def test_two_opportunities_get_two_platform_slides_with_their_own_headlines():
    prompt, _map, _packet = run(["onboarding", "recruiting"])
    platform = slides(prompt, "The Platform")
    assert len(platform) == 2
    assert "platform_headline: The onboarding platform, in four phases." in platform[0]
    assert "platform_headline: The recruiting platform, in four phases." in platform[1]


def test_the_two_timelines_carry_their_own_durations():
    """44 weeks and 24, which is the same assertion phase 1 makes about the
    build band. The axis's last column is where the horizon lives."""
    prompt, _map, _packet = run(["onboarding", "recruiting"])
    timelines = slides(prompt, "Phased Rollout")
    assert len(timelines) == 2
    assert "timeline_columns: - WEEKS 0–8 - 8–20 - 20–32 - 32–44" in timelines[0]
    assert "timeline_columns: - WEEKS 0–6 - 6–11 - 11–17 - 17–24" in timelines[1]
    assert "Market-signal screening" in timelines[1]
    assert "Market-signal screening" not in timelines[0]
    assert "Baseline measurement" not in timelines[1]


def test_two_next_steps_slides_and_one_commercial_terms():
    prompt, _map, _packet = run(["onboarding", "recruiting"])
    assert len(slides(prompt, "Next Steps")) == 2
    assert len(slides(prompt, "Commercial Terms")) == 1
    assert len(slides(prompt, "Title / Cover")) == 1


def test_the_deck_reads_in_template_order():
    prompt, _map, _packet = run(["onboarding", "recruiting"])
    titles = [line.split(" — ", 1)[1] for line in headers(prompt)]
    assert [title.split(" (")[0].split(" /")[0] for title in titles] == [
        "Title", "The Opportunity", "The Opportunity", "The Platform",
        "The Platform", "Phased Rollout", "Phased Rollout", "Commercial Terms",
        "Next Steps", "Next Steps"]


def test_the_slide_count_and_the_footer_agree_for_one_two_and_three():
    for names, expected in ((["onboarding"], 6),
                            (["onboarding", "recruiting"], 10),
                            (["onboarding", "recruiting", "third"], 14)):
        prompt, placeholder_map, _packet = run(names)
        numbers = [int(line.split()[2]) for line in headers(prompt)]
        assert numbers == list(range(1, expected + 1)), names
        assert rendered_slide_count(TEMPLATE, placeholder_map) == expected
        assert f"total_slides: {expected}" in prompt


def test_no_repeated_slide_renders_a_missing_marker_the_flat_map_would_not():
    """A repeated slide resolves its roles against the item ALONE, so an item
    missing a role the flat map carries would print `[MISSING: ...]` where a
    one-opportunity deck prints a value."""
    one, _m, _p = run(["onboarding"])
    two, _m, _p = run(["onboarding", "recruiting"])
    for title in ("The Platform", "Phased Rollout", "Next Steps"):
        baseline = {marker for slide in slides(one, title)
                    for marker in slide.split() if marker == "[MISSING:"}
        for slide in slides(two, title):
            assert ("[MISSING:" in slide) == bool(baseline), (title, slide[:300])


def test_coverage_guard_passes_on_a_two_opportunity_run():
    prompt, placeholder_map, packet = run(["onboarding", "recruiting"])
    check_coverage(packet, TEMPLATE, placeholder_map, prompt)


def test_each_repeated_platform_and_next_steps_slide_says_which_opportunity():
    """The problem slide 2's label solved, on the slides that now repeat
    beside it (Antonio, 2026-09-22)."""
    prompt, _map, _packet = run(["onboarding", "recruiting"])
    platform = slides(prompt, "The Platform")
    steps = slides(prompt, "Next Steps")
    assert ("platform_section_label: THE PLATFORM · OPPORTUNITY 1 · FIRST OPPORTUNITY"
            in platform[0])
    assert ("platform_section_label: THE PLATFORM · OPPORTUNITY 2 · SECOND OPPORTUNITY"
            in platform[1])
    assert ("next_steps_section_label: NEXT STEPS · OPPORTUNITY 2 · SECOND OPPORTUNITY"
            in steps[1])


def test_each_timeline_names_its_opportunity_and_still_leads_with_timeline():
    """Antonio on output-17: "the timelines dont name the opportunity"."""
    prompt, _map, _packet = run(["onboarding", "recruiting"])
    timelines = slides(prompt, "Phased Rollout")
    assert ("plan_section_label: TIMELINE · OPPORTUNITY 1 · FIRST OPPORTUNITY"
            in timelines[0])
    assert ("plan_section_label: TIMELINE · OPPORTUNITY 2 · SECOND OPPORTUNITY"
            in timelines[1])


def test_a_one_opportunity_deck_carries_no_label_line():
    prompt, _map, _packet = run(["onboarding"])
    assert "platform_section_label" not in prompt
    assert "next_steps_section_label" not in prompt
    assert "plan_section_label" not in prompt
