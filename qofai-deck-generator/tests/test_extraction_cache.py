"""Tests for the extraction cache (E8).

`packet_assembly.assemble` is a pure function of paper text, so a cache in
front of it is trustworthy only if cold and warm produce the identical packet,
field for field, on every committed fixture — not because the cache's own
bookkeeping says it reused something.
"""

import json
import pathlib

from source_span import PAPER, Opportunity

# The opportunity a reading is FOR (item 15). The fakes below default to it the
# way they default to `PAPER`, so a test about the pass itself states one once
# rather than at every call.
OPPORTUNITY = Opportunity(id="OPP-ONE")

import pytest

import extraction_cache
import packet_assembly

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "data-provider" / "fixtures"

ALL_FIXTURES = (
    "assumption-table-and-scenario-rows",
    "no-timeline-chart",
    "scenario-both-orientations",
    "scenario-columns",
    "scenario-rows-canonical",
    "scenario-rows-multi-table",
    "sparse-no-scenario-table",
    "validated-assumption-table",
)


def paper(shape):
    path = FIXTURES / f"paper-excerpt-{shape}.json"
    return json.loads(path.read_text())["opportunity"]["research_paper_natural"]


@pytest.fixture(autouse=True)
def cold_cache():
    """Every test starts and ends with an empty cache, so one test's warmth
    never reads as another test's result."""
    extraction_cache.clear()
    yield
    extraction_cache.clear()


@pytest.mark.parametrize("shape", ALL_FIXTURES)
def test_cache_cold_and_warm_assemble_the_identical_packet(shape):
    text = paper(shape)
    cold = extraction_cache.assemble_cached(shape, text, document=PAPER)
    warm = extraction_cache.assemble_cached(shape, text, document=PAPER)
    assert cold == warm
    assert cold == packet_assembly.assemble(shape, text)


def test_a_second_identical_run_reuses_the_cached_extraction(monkeypatch):
    text = paper("scenario-rows-canonical")
    calls = []
    real_assemble = packet_assembly.assemble

    def counting_assemble(opportunity_id, paper_text, document=PAPER,
                          opportunity=None):
        calls.append(opportunity_id)
        return real_assemble(opportunity_id, paper_text, document)

    monkeypatch.setattr(packet_assembly, "assemble", counting_assemble)

    first = extraction_cache.assemble_cached("opp-a", text, document=PAPER)
    second = extraction_cache.assemble_cached("opp-a", text, document=PAPER)

    assert len(calls) == 1
    assert first == second


def test_a_changed_paper_invalidates_its_own_entry(monkeypatch):
    original = paper("scenario-rows-canonical")
    rewritten = original + "\n\nAn added paragraph that changes the paper text."
    calls = []
    real_assemble = packet_assembly.assemble

    def counting_assemble(opportunity_id, paper_text, document=PAPER,
                          opportunity=None):
        calls.append(paper_text)
        return real_assemble(opportunity_id, paper_text, document)

    monkeypatch.setattr(packet_assembly, "assemble", counting_assemble)

    extraction_cache.assemble_cached("opp-a", original, document=PAPER)
    extraction_cache.assemble_cached("opp-a", rewritten, document=PAPER)

    assert len(calls) == 2


def test_a_cache_hit_still_carries_the_requested_opportunity_id():
    """Two calls sharing paper text (a re-render of the same opportunity, or
    two opportunities that happen to share text) must not silently also share
    whichever opportunity id populated the cache first."""
    text = paper("scenario-columns")
    first = extraction_cache.assemble_cached("opp-a", text, document=PAPER)
    second = extraction_cache.assemble_cached("opp-b", text, document=PAPER)
    assert first.opportunity_id == "opp-a"
    assert second.opportunity_id == "opp-b"
    assert [(f.field, f.value, f.span) for f in first.fields] == \
           [(f.field, f.value, f.span) for f in second.fields]


def test_a_cache_hit_replays_the_figures_under_the_requested_opportunity_too():
    """ITEM 15, AND IT IS WHY THE REPLAY HAD TO GO DEEPER. A figure names the
    opportunity it was read for, so replaying a cached packet under a new
    `opportunity_id` while leaving its figures naming the first one would hand
    the second opportunity a packet whose every figure claims to belong to the
    first. This is not a rare path: a multi-opportunity run reads the same
    attached document once per opportunity, so the hit happens on every
    opportunity after the first, on every run with an attachment."""
    text = paper("scenario-columns")
    first = extraction_cache.assemble_cached("opp-a", text, document=PAPER)
    second = extraction_cache.assemble_cached("opp-b", text, document=PAPER)

    assert {f.opportunity.id for f in first.fields} == {"opp-a"}
    assert {f.opportunity.id for f in second.fields} == {"opp-b"}
    # The scenario cases hold figures inside them, so they are replayed too
    # rather than carried across naming the first caller's opportunity.
    assert {case.direct_uplift_usd_yr.opportunity.id
            for case in second.scenarios} == {"opp-b"}


def test_a_replay_carries_the_caller_own_title_and_not_the_first_callers():
    """The title rides on the record, so a replay that took the cached figure's
    opportunity whole would name the first caller's engagement on the second
    caller's slide."""
    text = paper("scenario-columns")
    extraction_cache.assemble_cached(
        "opp-a", text, document=PAPER,
        opportunity=Opportunity(id="opp-a", title="Field capture"))
    second = extraction_cache.assemble_cached(
        "opp-b", text, document=PAPER,
        opportunity=Opportunity(id="opp-b", title="Live dashboards"))

    assert {f.opportunity.title for f in second.fields} == {"Live dashboards"}
