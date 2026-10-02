"""The base document rule (item 14, step 2).

A proposal is written from the published opportunity paper until a reviewer
attaches something, and then the attachment is the base and the paper answers
only what the attachment does not. Casey's reason, 2026-09-04: a PRD carries
execution detail, pricing, timelines and deliverable dates the paper does not,
while the paper is the document that always exists.

THE FIRST TEST IN THIS FILE IS THE LAST ONE TO BREAK. A run with no attachment
has to behave exactly as it did, and "exactly" is asserted three ways: the same
single call into assembly with the same text, the merge of one packet being that
packet by identity, and `submit` still being reachable with a request alone, so
every provider double in this suite keeps working.

The attached documents here are synthetic markdown authored for their SHAPE
rather than their content, and the paper is a committed excerpt. Both carry
invented figures, and the filenames are deliberately meaningless: the rule is
about which document is the base, and a test that leaned on a name would be
testing something the rule is forbidden to do.
"""

import dataclasses
import json

import pathlib

import base_document
from data_source_adapter import _section_yaml, _split_sections, map_packet
import extraction_cache
import source_span
from base_document import (PAPER_KIND, Upload, apply_shared,
                           engagement_precedence, merge_packets,
                           precedence, take_whole)
from data_source_adapter import FixtureProvider, _await_envelope, dispatch_and_gate
from document_text import extract_text
from live_proposal_provider import LiveProposalProvider, ProviderError
from packet_assembly import EBITDA, MARGIN, REVENUE, ROSTER, TIMELINE, assemble
from source_span import Document, Opportunity, SourcedFigure

# The opportunity a reading is FOR (item 15). The fakes below default to it the
# way they default to `PAPER`, so a test about the pass itself states one once
# rather than at every call.
OPPORTUNITY = Opportunity(id="OPP-ONE")

from test_live_seam import CLIENTS, STAMP, StubClient
from test_paper_extraction import paper

import pytest

# The paper that lands BELOW the completeness floor on the deterministic
# parsers alone: 4 of 6, no scenario table. Chosen because the gap it leaves is
# exactly the gap an attachment can close, so "the attachment answered what the
# paper did not" is a run that clears the gate rather than an assertion about a
# tuple.
SPARSE = dict(CLIENTS["one"], shape="sparse-no-scenario-table")
FLOOR = 0.70

# An attachment carrying a scenario table and no baseline. The two roster fields
# a scenario table satisfies are the two the sparse paper is missing.
SCENARIOS_ONLY = """# Engagement plan

## Financial Impact Scenarios

| Scenario | Throughput Improvement | Incremental Annual Revenue | Incremental Annual EBITDA | EBITDA Margin Impact (pp) |
|----------|----------------------|---------------------------|--------------------------|--|
| **Conservative** | 2% | $2.10M | $1.40M | ~1.5 pp |
| **Base Case** | 5% | $5.20M | $3.10M | ~3.4 pp |
| **Optimistic** | 9% | $9.90M | $5.90M | ~6.6 pp |
"""

# An attachment carrying its own baseline, in figures that appear in no paper,
# so a merged packet holding them can only have taken them from here.
BASELINE_ONLY = """# Engagement plan

### Current State

| Metric | CY 2024 | CY 2025 | Change | Q1 2026 (Annualized) |
|---|---|---|---|---|
| **Revenue** | $30.1M | $38.4M | +27.6% | ~$60.2M |
| **EBITDA** | $10.2M | $14.6M | +43.1% | ~$24.4M |
| **EBITDA Margin** | 33.9% | 38.0% | +4.1pp | ~40.5% |
"""


# An attachment stating a baseline REVENUE and nothing else, which is the common
# PRD shape: it prices the work and restates no margin. Its period differs from
# the paper's below, which is what makes a mixed set detectable.
REVENUE_ONLY = """# Engagement plan

### Current State

| Metric | CY 2024 | CY 2025 | Change |
|---|---|---|---|
| **Revenue** | $30.1M | $38.4M | +27.6% |
"""

# A paper stating all three over a different, earlier period.
OLDER_FULL_BASELINE = """# Opportunity paper

### Current State

| Metric | CY 2022 | CY 2023 | Change |
|---|---|---|---|
| **Revenue** | $10.0M | $12.0M | +20.0% |
| **EBITDA** | $2.5M | $3.0M | +20.0% |
| **EBITDA Margin** | 25.0% | 25.0% | +0.0pp |
"""


def upload(text, name="attachment.md"):
    return Upload(filename=name, data=text.encode())


def document(text, name="attachment.md"):
    result = extract_text(text.encode(), name)
    assert result.ok, result
    return result


def request(spec=SPARSE, floor=FLOOR, **overrides):
    body = {"company": spec["company"]["name"],
            "project": spec["project"]["name"],
            "pe_firm": spec["pe_firm"],
            "proposal_date": "2026-08-15",
            "options": {"min_data_completeness": floor}}
    body.update(overrides)
    return body


def envelope(uploads=(), spec=SPARSE, client=None, **overrides):
    provider = LiveProposalProvider(client or StubClient(spec), generated_at=STAMP)
    handle = (provider.submit(request(spec, **overrides), uploads=uploads)
              if uploads else provider.submit(request(spec, **overrides)))
    return provider.poll(handle)["envelope"]


def figures(packet):
    return {figure.field: figure.value for figure in packet.fields}


# ---------------------------------------------- a run with no attachment


def test_a_run_with_no_attachment_reaches_assembly_once_with_the_paper(monkeypatch):
    """The first test written and the last one allowed to break. Nothing about
    the shipped path may move: one call into assembly, with the paper's text."""
    calls = []
    real = extraction_cache.assemble_cached

    def spy(opportunity_id, text, document, opportunity=None):
        calls.append((opportunity_id, text, document))
        return real(opportunity_id, text, document)

    monkeypatch.setattr(extraction_cache, "assemble_cached", spy)
    monkeypatch.setattr("live_proposal_provider.extraction_cache.assemble_cached", spy)
    result = envelope()
    assert result["status"] in ("ok", "error")
    assert len(calls) == 1
    assert calls[0][1] == paper(SPARSE["shape"])
    # And the one document it named is the published paper, not an attachment.
    assert calls[0][2] == source_span.PAPER


def test_a_run_with_no_attachment_returns_what_it_returned_before():
    """The sparse paper is 4 of 6 on the parsers alone, so this run is refused
    for the reason it has always been refused, at the score it always scored."""
    result = envelope()
    assert result["status"] == "error"
    assert result["error"]["code"] == "E_LOW_CONFIDENCE"
    assert result["error"]["details"]["data_completeness"] == pytest.approx(4 / 6)


def test_submit_is_still_reachable_with_a_request_alone():
    """Every provider double in this suite has a one-argument `submit`."""
    provider = LiveProposalProvider(StubClient(SPARSE), generated_at=STAMP)
    assert provider.poll(provider.submit(request()))["envelope"]["status"] == "error"


def test_the_transport_half_passes_one_argument_when_nothing_is_attached():
    """So a provider that never heard of attachments stays drivable."""
    seen = []

    class OneArgumentProvider:
        def submit(self, request):
            seen.append(request)
            return 0

        def poll(self, handle):
            return {"done": True, "envelope": {"status": "ok", "packet": ""}}

    _await_envelope(OneArgumentProvider(), {"a": 1}, 0.0, 2, lambda _s: None)
    assert seen == [{"a": 1}]


def test_merging_one_packet_returns_that_packet_by_identity():
    packet = assemble("OPP-ONE", paper(SPARSE["shape"]))
    assert merge_packets([packet]) is packet


def test_a_chain_of_nothing_is_the_paper_alone():
    sources = precedence(paper(SPARSE["shape"]))
    assert len(sources) == 1
    assert sources[0].kind == PAPER_KIND
    assert sources[0].uploaded is False
    assert base_document.supporting(sources) == ()


def test_an_empty_paper_still_makes_a_chain_because_a_picked_one_can_be_empty():
    sources = precedence("")
    assert len(sources) == 1
    assert sources[0].text == ""


def test_a_paperless_opportunity_assembles_exactly_what_it_assembled_before():
    """`fetch_opportunity_paper` returns None for an opportunity whose paper the
    platform withheld, and a picked one goes straight to assembly. A source
    holds "" instead, and the packet is the same packet: identical fields,
    identical absences, identical reasons."""
    sources = precedence(None)
    assert sources[0].text == ""
    assert assemble("OPP-ONE", sources[0].text) == assemble("OPP-ONE", None)


# --------------------------------------------------------- the precedence rule


def test_an_attachment_becomes_the_base_and_the_paper_falls_behind_it():
    sources = precedence(paper(SPARSE["shape"]), [document(SCENARIOS_ONLY)])
    assert base_document.base(sources).uploaded is True
    assert base_document.base(sources).text == SCENARIOS_ONLY.strip()
    assert [source.kind for source in sources] == ["markdown", PAPER_KIND]


def test_several_attachments_win_in_the_order_the_reviewer_attached_them():
    sources = precedence(
        paper(SPARSE["shape"]),
        [document(BASELINE_ONLY, "first.md"), document(SCENARIOS_ONLY, "second.md")],
    )
    assert [source.name for source in sources] == ["first.md", "second.md", ""]
    assert base_document.base(sources).name == "first.md"


def test_the_paper_is_always_last_however_many_documents_arrive():
    for count in range(1, 4):
        documents = [document(SCENARIOS_ONLY, f"n{n}.md") for n in range(count)]
        sources = precedence(paper(SPARSE["shape"]), documents)
        assert sources[-1].kind == PAPER_KIND
        assert len(sources) == count + 1


def test_an_empty_paper_leaves_the_chain_when_a_document_is_attached():
    sources = precedence("", [document(SCENARIOS_ONLY)])
    assert [source.kind for source in sources] == ["markdown"]


def test_precedence_reads_nothing_about_the_document_but_its_order():
    """Two attachments identical but for their names take the same positions,
    which is the mechanical form of "never a branch on a filename"."""
    first = precedence("paper text", [document(SCENARIOS_ONLY, "a-name.md")])
    second = precedence("paper text", [document(SCENARIOS_ONLY, "an-utterly-different-name.md")])
    assert [s.kind for s in first] == [s.kind for s in second]
    assert [s.text for s in first] == [s.text for s in second]


# ---------------------------------------------------------------- the merge


def test_the_attachment_answers_what_it_carries_and_the_paper_the_rest():
    attached = assemble("OPP-ONE", SCENARIOS_ONLY)
    published = assemble("OPP-ONE", paper(SPARSE["shape"]))
    merged = merge_packets([attached, published])

    assert set(merged.present) == set(ROSTER)
    # The scenario half came from the attachment, the baselines and the timeline
    # from the paper, and neither packet holds all six alone.
    assert set(attached.present) | set(published.present) == set(ROSTER)
    assert figures(merged)[REVENUE] == figures(published)[REVENUE]
    assert merged.scenarios == attached.scenarios


def test_the_attachment_wins_a_field_both_documents_carry():
    attached = assemble("OPP-ONE", BASELINE_ONLY)
    published = assemble("OPP-ONE", paper(SPARSE["shape"]))
    merged = merge_packets([attached, published])

    for field in (REVENUE, EBITDA, MARGIN):
        assert figures(merged)[field] == figures(attached)[field]
        assert figures(merged)[field] != figures(published)[field]
    # And the paper still answers what the attachment left alone.
    assert TIMELINE in figures(merged)
    assert figures(merged)[TIMELINE] == figures(published)[TIMELINE]


def test_precedence_runs_across_three_documents_in_order():
    first = assemble("OPP-ONE", BASELINE_ONLY)
    second = assemble("OPP-ONE", SCENARIOS_ONLY)
    published = assemble("OPP-ONE", paper(SPARSE["shape"]))
    merged = merge_packets([first, second, published])
    assert figures(merged)[REVENUE] == figures(first)[REVENUE]
    assert merged.scenarios == second.scenarios
    assert figures(merged)[TIMELINE] == figures(published)[TIMELINE]


def test_every_figure_keeps_the_span_of_its_own_document():
    """No figure is ever restated against text it did not come from, which is
    what keeps `SourcedFigure`'s guarantee true across two sources."""
    attached = assemble("OPP-ONE", SCENARIOS_ONLY)
    published = assemble("OPP-ONE", paper(SPARSE["shape"]))
    merged = merge_packets([attached, published])
    published_text = paper(SPARSE["shape"])
    for figure in merged.fields:
        assert figure.span in SCENARIOS_ONLY or figure.span in published_text
    for case in merged.scenarios:
        assert case.direct_uplift_usd_yr.span in SCENARIOS_ONLY


def test_a_scenario_table_merges_whole_and_not_case_by_case():
    """Taking one case from each document would state a table neither states."""
    attached = assemble("OPP-ONE", SCENARIOS_ONLY)
    canonical = assemble("OPP-ONE", paper("scenario-rows-canonical"))
    assert attached.scenarios and canonical.scenarios
    merged = merge_packets([attached, canonical])
    assert merged.scenarios == attached.scenarios
    assert len(merged.scenarios) == len(attached.scenarios)


def test_the_baseline_period_travels_with_the_document_that_leads_the_baseline():
    attached = assemble("OPP-ONE", BASELINE_ONLY)
    published = assemble("OPP-ONE", paper(SPARSE["shape"]))
    merged = merge_packets([attached, published])
    assert merged.baseline_period == attached.baseline_period
    assert merged.baseline_basis == attached.baseline_basis


def test_a_document_with_no_baseline_does_not_take_the_papers_period():
    """A period lifted off another document would date a figure that document
    never stated, so a base leading no baseline contributes no label either."""
    attached = assemble("OPP-ONE", SCENARIOS_ONLY)
    published = assemble("OPP-ONE", paper(SPARSE["shape"]))
    merged = merge_packets([attached, published])
    assert merged.baseline_period == published.baseline_period
    assert figures(merged)[REVENUE] == figures(published)[REVENUE]


def test_a_partial_baseline_is_not_completed_from_a_lower_source():
    """The defect of 2026-09-07, fixed the same day, and the case a PRD makes
    common: it prices the work and restates no margin.

    Field by field, an attachment stating CY 2025 revenue of $38.4M against a
    paper stating CY 2023 revenue, EBITDA and margin merged into a "CY 2025"
    baseline of $38.4M revenue, $3.0M EBITDA and a 25.0% margin. Three figures
    that read as one set, printed side by side on slide 2's strip, whose own
    arithmetic disagrees: $3.0M over $38.4M is 7.8 percent, not 25, and two of
    the three had never been stated for CY 2025 by anybody.
    """
    attached = assemble("OPP-ONE", REVENUE_ONLY)
    published = assemble("OPP-ONE", OLDER_FULL_BASELINE)
    assert set(published.present) >= {REVENUE, EBITDA, MARGIN}
    assert attached.baseline_period != published.baseline_period

    merged = merge_packets([attached, published])
    got = figures(merged)
    assert got[REVENUE] == figures(attached)[REVENUE]
    assert merged.baseline_period == attached.baseline_period
    assert EBITDA not in got
    assert MARGIN not in got
    # And the two that did not come are missing fields with reasons, which is
    # the golden rule's own answer rather than a silent gap.
    absent = dict(merged.missing_fields)
    assert absent[EBITDA].strip()
    assert absent[MARGIN].strip()


def test_the_baseline_the_lead_does_state_is_never_the_lower_sources():
    """The pairing is the hazard, so the assertion is about what did NOT arrive."""
    attached = assemble("OPP-ONE", REVENUE_ONLY)
    published = assemble("OPP-ONE", OLDER_FULL_BASELINE)
    merged = merge_packets([attached, published])
    for figure in merged.fields:
        if figure.field in (REVENUE, EBITDA, MARGIN):
            assert figure.span in REVENUE_ONLY
            assert figure.span not in OLDER_FULL_BASELINE


def test_a_full_baseline_on_the_attachment_is_unchanged_by_the_group_rule():
    """The reverse direction. An attachment stating all three still supplies all
    three, and the paper still answers everything outside the baseline."""
    attached = assemble("OPP-ONE", BASELINE_ONLY)
    published = assemble("OPP-ONE", paper(SPARSE["shape"]))
    merged = merge_packets([attached, published])
    got = figures(merged)
    for field in (REVENUE, EBITDA, MARGIN):
        assert got[field] == figures(attached)[field]
    assert merged.baseline_period == attached.baseline_period
    assert got[TIMELINE] == figures(published)[TIMELINE]
    assert not [name for name, _why in merged.missing_fields
                if name in (REVENUE, EBITDA, MARGIN)]


def test_a_partial_baseline_on_the_paper_is_not_completed_from_below_either():
    """The rule is about precedence, not about attachments. A paper that leads
    the baseline and states two of three leaves the third absent, whatever a
    lower source says."""
    attached = assemble("OPP-ONE", SCENARIOS_ONLY)
    published = assemble("OPP-ONE", REVENUE_ONLY)
    third = assemble("OPP-ONE", OLDER_FULL_BASELINE)
    merged = merge_packets([attached, published, third])
    got = figures(merged)
    assert got[REVENUE] == figures(published)[REVENUE]
    assert EBITDA not in got
    assert merged.baseline_period == published.baseline_period


def test_the_lead_is_the_highest_precedence_source_carrying_any_baseline():
    """A source carrying no baseline figure at all does not lead it, so the
    group falls whole to the next source that states one."""
    attached = assemble("OPP-ONE", SCENARIOS_ONLY)
    published = assemble("OPP-ONE", OLDER_FULL_BASELINE)
    merged = merge_packets([attached, published])
    got = figures(merged)
    for field in (REVENUE, EBITDA, MARGIN):
        assert got[field] == figures(published)[field]
    assert merged.baseline_period == published.baseline_period
    assert merged.scenarios == attached.scenarios


def test_every_merged_baseline_figure_comes_from_the_lead_in_any_ordering():
    """The property behind all of the above, over every ordering of three of
    four documents that state the baseline differently.

    Asserted against the LEAD rather than by counting distinct sources, because
    two of these fixtures share a table row and a shared span would read as
    mixing when it is only ambiguity.
    """
    import itertools

    texts = (REVENUE_ONLY, OLDER_FULL_BASELINE, BASELINE_ONLY, SCENARIOS_ONLY)
    baseline_names = (REVENUE, EBITDA, MARGIN)
    for chosen in itertools.permutations(texts, 3):
        packets = [assemble("OPP-ONE", text) for text in chosen]
        lead_text = next(
            (text for text, packet in zip(chosen, packets)
             if any(figure.field in baseline_names for figure in packet.fields)),
            chosen[0],
        )
        merged = merge_packets(packets)
        for figure in merged.fields:
            if figure.field in baseline_names:
                assert figure.span in lead_text, chosen
        assert merged.baseline_period == assemble(
            "OPP-ONE", lead_text
        ).baseline_period


def test_a_baseline_figure_absent_from_the_lead_can_still_be_read_from_prose():
    """The group rule closes the merge, not the extraction pass. A base document
    stating a figure in prose rather than in a table still gets it read, because
    the second pass reads the BASE for exactly the fields left absent, and it is
    then the base's own figure beside the base's own period."""
    import second_pass

    attached = assemble("OPP-ONE", REVENUE_ONLY)
    published = assemble("OPP-ONE", OLDER_FULL_BASELINE)
    merged = merge_packets([attached, published])
    requested = second_pass.absent(merged, {})
    assert EBITDA in requested
    assert MARGIN in requested


def test_an_absence_one_document_answered_stops_being_missing():
    attached = assemble("OPP-ONE", SCENARIOS_ONLY)
    published = assemble("OPP-ONE", paper(SPARSE["shape"]))
    assert [name for name, _why in published.missing_fields]
    merged = merge_packets([attached, published])
    assert merged.missing_fields == ()


def test_an_absence_no_document_answered_keeps_a_reason():
    attached = assemble("OPP-ONE", SCENARIOS_ONLY)
    thin = assemble("OPP-ONE", "A document that states no figure at all.\n")
    merged = merge_packets([attached, thin])
    names = [name for name, _why in merged.missing_fields]
    assert REVENUE in names
    assert TIMELINE in names
    for _name, reason in merged.missing_fields:
        assert reason.strip()


def test_a_merged_absence_is_named_once_however_many_documents_missed_it():
    thin = assemble("OPP-ONE", "A document that states no figure at all.\n")
    other = assemble("OPP-ONE", "A second document that states no figure.\n")
    merged = merge_packets([thin, other])
    names = [name for name, _why in merged.missing_fields]
    assert len(names) == len(set(names))


def test_merging_nothing_is_a_call_site_bug():
    with pytest.raises(ValueError):
        merge_packets([])


def test_the_merge_does_not_mutate_what_it_was_handed():
    attached = assemble("OPP-ONE", BASELINE_ONLY)
    published = assemble("OPP-ONE", paper(SPARSE["shape"]))
    before = (attached.fields, attached.scenarios, attached.missing_fields,
              published.fields, published.scenarios, published.missing_fields)
    merge_packets([attached, published])
    assert (attached.fields, attached.scenarios, attached.missing_fields,
            published.fields, published.scenarios, published.missing_fields) == before


# ------------------------------------------------- the provider, end to end


def test_an_attachment_turns_a_refused_run_into_a_deck():
    """The whole item in one assertion. The sparse paper alone scores 4 of 6 and
    the gate refuses it; the same run with a document carrying the two fields it
    lacks clears the floor and returns a packet."""
    refused = envelope()
    assert refused["status"] == "error"
    assert refused["error"]["code"] == "E_LOW_CONFIDENCE"

    built = envelope(uploads=[upload(SCENARIOS_ONLY)])
    assert built["status"] == "ok"
    assert built["packet"]


def test_the_model_legs_read_the_base_document_and_not_the_paper():
    read = []

    def extractor(text, requested, labels=(), document=None,
                  opportunity=OPPORTUNITY, description=""):
        read.append(("extract", text))
        raise RuntimeError("this pass is not what is under test here")

    def writer(text, description, requested):
        read.append(("write", text))
        raise RuntimeError("this pass is not what is under test here")

    provider = LiveProposalProvider(StubClient(SPARSE), generated_at=STAMP,
                                    extractor=extractor, writer=writer)
    handle = provider.submit(request(), uploads=[upload(SCENARIOS_ONLY)])
    provider.poll(handle)
    assert read
    for leg, text in read:
        assert text == SCENARIOS_ONLY.strip(), leg


def test_the_model_legs_read_the_paper_when_nothing_is_attached():
    read = []

    def extractor(text, requested, labels=(), document=None,
                  opportunity=OPPORTUNITY, description=""):
        read.append((text, document))
        raise RuntimeError("this pass is not what is under test here")

    provider = LiveProposalProvider(StubClient(SPARSE), generated_at=STAMP,
                                    extractor=extractor)
    provider.poll(provider.submit(request()))
    assert read
    assert read[0] == (paper(SPARSE["shape"]), source_span.PAPER)


def test_an_attachment_never_reaches_the_request_echo():
    """The echo is written into the packet document and the deck store, so a
    file's bytes have no business in it. The attachment record is step 5."""
    built = envelope(uploads=[upload(SCENARIOS_ONLY)])
    assert built["status"] == "ok"
    echo = built["request_echo"]
    assert "uploads" not in echo
    for value in echo.values():
        assert "Financial Impact Scenarios" not in str(value)


def test_two_attachments_reach_the_provider_in_order():
    built = envelope(uploads=[upload(BASELINE_ONLY, "first.md"),
                              upload(SCENARIOS_ONLY, "second.md")])
    assert built["status"] == "ok"
    # The first attachment's own baseline is what the packet carries, which is
    # only true if it led the chain: the second attachment states no baseline
    # and the paper states a different one.
    assert "revenue_ttm_usd: 38400000" in built["packet"]
    assert "adjusted_ebitda_usd: 14600000" in built["packet"]


def test_the_uploads_argument_reaches_the_provider_through_the_transport_half():
    result = dispatch_and_gate(
        SPARSE["company"]["name"], SPARSE["project"]["name"],
        LiveProposalProvider(StubClient(SPARSE), generated_at=STAMP),
        pe_firm=SPARSE["pe_firm"], proposal_date="2026-08-15",
        options={"min_data_completeness": FLOOR},
        uploads=[upload(SCENARIOS_ONLY)],
        poll_interval=0.0, sleep=lambda _s: None,
    )
    assert result["status"] == "ok"


# --------------------------------------- a file that will not read, surfaced


def test_an_unreadable_attachment_comes_back_as_the_refusal_it_already_was():
    """Off the four fields the extraction module already wrote, not re-derived:
    the reviewer reads the sentence written by whatever refused the file."""
    failure = extract_text(b"", "attachment.pdf")
    result = envelope(uploads=[Upload(filename="attachment.pdf", data=b"")])
    assert result["status"] == "error"
    assert result["error"]["code"] == failure.code == "E_EMPTY_FILE"
    assert result["error"]["message"] == failure.message
    assert result["error"]["remediation"] == failure.remediation
    assert result["error"]["details"] == failure.details


@pytest.mark.parametrize("name,data,code", [
    ("attachment.xlsx", b"anything at all", "E_UNSUPPORTED_TYPE"),
    ("attachment.pdf", b"", "E_EMPTY_FILE"),
    ("attachment.md", b"   \n  \n", "E_NO_TEXT"),
    ("attachment.docx", b"%PDF-1.4 and then some", "E_TYPE_MISMATCH"),
    ("", b"anything at all", "E_UNNAMED_FILE"),
])
def test_every_kind_of_unreadable_attachment_stops_the_run(name, data, code):
    result = envelope(uploads=[Upload(filename=name, data=data)])
    assert result["status"] == "error"
    assert result["error"]["code"] == code
    assert "packet" not in result


def test_an_unreadable_attachment_never_falls_back_to_the_paper():
    """The one outcome part two's criteria rule out by name. The paper here is
    good enough to build from, which is what makes the absence of a packet the
    assertion rather than an accident."""
    good = envelope(spec=CLIENTS["one"])
    assert good["status"] == "ok"
    refused = envelope(spec=CLIENTS["one"],
                       uploads=[Upload(filename="attachment.xlsx", data=b"x")])
    assert refused["status"] == "error"
    assert "packet" not in refused


def test_an_unreadable_attachment_costs_no_platform_call():
    client = StubClient(SPARSE)
    result = envelope(client=client,
                      uploads=[Upload(filename="attachment.xlsx", data=b"x")])
    assert result["status"] == "error"
    assert client.calls == []


def test_a_bad_attachment_raises_a_provider_error_the_envelope_is_built_from():
    provider = LiveProposalProvider(StubClient(SPARSE), generated_at=STAMP)
    with pytest.raises(ProviderError) as raised:
        provider._documents([Upload(filename="attachment.xlsx", data=b"x")])
    assert raised.value.code == "E_UNSUPPORTED_TYPE"
    assert raised.value.remediation


# ----------------------------------------------- the fixture provider refuses


def test_the_fixture_provider_refuses_an_attachment_rather_than_dropping_it():
    provider = FixtureProvider.from_packet_markdown("---\n---\n")
    with pytest.raises(ValueError) as raised:
        provider.submit({}, uploads=[upload(SCENARIOS_ONLY)])
    assert "attachment" in str(raised.value)
    # And it is unchanged for every run that attaches nothing.
    assert provider.submit({}) == 0


# ------------------------------------------------------------ no hardcoding


def test_nothing_in_the_module_names_a_client_a_project_or_a_document():
    source = open(base_document.__file__).read().lower()
    for token in ("fabrikam", "wtg", "northwind", "ridgeline", "lts",
                  ".pdf", ".docx", ".md", ".txt"):
        assert token not in source


def test_the_same_documents_merge_the_same_under_any_filename():
    one = precedence("paper text", [document(SCENARIOS_ONLY, "one.md")])
    two = precedence("paper text", [document(SCENARIOS_ONLY, "two.markdown")])
    assert merge_packets([assemble("OPP", s.text) for s in one]).fields == \
        merge_packets([assemble("OPP", s.text) for s in two]).fields


# --------------------------------------------------- what a conflict is


# An attachment and a paper that both state all three baseline figures and both
# price the scenarios, differently, which is the only shape that produces a
# conflict on every row at once.
CONTESTED_ATTACHMENT = """# Engagement plan

### Current State

| Metric | CY 2024 | CY 2025 | Change |
|---|---|---|---|
| **Revenue** | $30.1M | $38.4M | +27.6% |
| **EBITDA** | $10.2M | $14.6M | +43.1% |
| **EBITDA Margin** | 33.9% | 38.0% | +4.1pp |

## Financial Impact Scenarios

| Scenario | Improvement | Incremental Annual Revenue | Incremental Annual EBITDA | EBITDA Margin Impact (pp) |
|---|---|---|---|--|
| **Conservative** | 2% | $2.10M | $1.40M | ~1.5 pp |
| **Base Case** | 5% | $5.20M | $3.10M | ~3.4 pp |
"""

CONTESTED_PAPER = """# Opportunity paper

### Current State

| Metric | CY 2022 | CY 2023 | Change |
|---|---|---|---|
| **Revenue** | $10.0M | $12.0M | +20.0% |
| **EBITDA** | $2.5M | $3.0M | +20.0% |
| **EBITDA Margin** | 25.0% | 25.0% | +0.0pp |

## Financial Impact Scenarios

| Scenario | Improvement | Incremental Annual Revenue | Incremental Annual EBITDA | EBITDA Margin Impact (pp) |
|---|---|---|---|--|
| **Conservative** | 2% | $0.50M | $0.30M | ~0.4 pp |
| **Base Case** | 5% | $1.10M | $0.70M | ~0.9 pp |
"""

ATTACHED = Document(name="attachment.md", kind="markdown")

# The period the attachment states, read off the parser rather than typed here.
ATTACHED_PERIOD = assemble("OPP-ONE", REVENUE_ONLY, ATTACHED).baseline_period


def contested():
    return merge_packets([
        assemble("OPP-ONE", CONTESTED_ATTACHMENT, ATTACHED),
        assemble("OPP-ONE", CONTESTED_PAPER, source_span.PAPER),
    ])


def conflict_on(packet, field):
    found = [conflict for conflict in packet.conflicts if conflict.field == field]
    assert len(found) <= 1, field
    return found[0] if found else None


def test_a_packet_from_one_document_has_no_conflicts():
    """One document cannot disagree with itself, and this is the packet every
    run with no attachment produces."""
    single = assemble("OPP-ONE", paper(SPARSE["shape"]))
    assert single.conflicts == ()
    assert merge_packets([single]).conflicts == ()


def test_a_field_two_documents_answered_differently_is_a_conflict():
    packet = contested()
    conflict = conflict_on(packet, REVENUE)
    assert conflict is not None
    assert [answer.document for answer in conflict.answers] == [
        ATTACHED, source_span.PAPER
    ]
    assert conflict.carried.value == figures(packet)[REVENUE]
    assert conflict.alternatives[0].value == (12_000_000.0, 12_000_000.0)


def test_every_answer_carries_the_span_it_came_from():
    conflict = conflict_on(contested(), EBITDA)
    for answer in conflict.answers:
        assert answer.span.strip()
    assert conflict.carried.span in CONTESTED_ATTACHMENT
    assert conflict.alternatives[0].span in CONTESTED_PAPER


def test_a_field_the_base_simply_won_is_not_a_conflict():
    """Every field the base carries is a field the base won, and a list of those
    is the packet. A conflict is a disagreement."""
    attached = assemble("OPP-ONE", SCENARIOS_ONLY, ATTACHED)
    published = assemble("OPP-ONE", paper(SPARSE["shape"]), source_span.PAPER)
    packet = merge_packets([attached, published])
    assert set(packet.present) == set(ROSTER)
    assert packet.conflicts == ()


def test_two_documents_agreeing_is_not_a_conflict():
    twice = merge_packets([
        assemble("OPP-ONE", CONTESTED_ATTACHMENT, ATTACHED),
        assemble("OPP-ONE", CONTESTED_ATTACHMENT, source_span.PAPER),
    ])
    assert twice.conflicts == ()


def test_a_field_only_one_document_answered_is_not_a_conflict():
    attached = assemble("OPP-ONE", SCENARIOS_ONLY, ATTACHED)
    published = assemble("OPP-ONE", paper(SPARSE["shape"]), source_span.PAPER)
    packet = merge_packets([attached, published])
    assert conflict_on(packet, TIMELINE) is None
    assert conflict_on(packet, base_document.SCENARIOS) is None


def test_the_scenario_table_is_one_conflict_and_not_one_per_case():
    packet = contested()
    conflict = conflict_on(packet, base_document.SCENARIOS)
    assert conflict is not None
    assert len(conflict.answers) == 2
    assert conflict_on(packet, "commercial.scenarios[].direct_uplift_usd_yr") is None
    assert conflict_on(packet, "commercial.scenarios[].margin_gain_pp") is None
    # Every row the table stated, joined, because a table is several rows and no
    # one of them is the answer.
    for row in conflict.carried.span.split("\n"):
        assert row in CONTESTED_ATTACHMENT


def test_two_identical_scenario_tables_from_two_documents_do_not_conflict():
    """The comparison is on the cases' labels and figures, not on the
    `ScenarioCase` objects, which carry documents now and so could never compare
    equal across two sources."""
    twice = merge_packets([
        assemble("OPP-ONE", SCENARIOS_ONLY, ATTACHED),
        assemble("OPP-ONE", SCENARIOS_ONLY, source_span.PAPER),
    ])
    assert conflict_on(twice, base_document.SCENARIOS) is None


def test_differ_means_the_values_are_unequal_endpoint_by_endpoint():
    """The definition, including the range case, exercised directly.

    Directly rather than through a parser because the deterministic parsers
    cannot produce an unequal pair: `packet_assembly`'s representation is a
    `(low, high)` pair that is EQUAL when a document states one value, and a
    genuine range reaches a packet through the second extraction pass, which
    runs after this merge and on one document. So the pairs below are built by
    hand, and the rule they establish is the rule a range will meet when one
    arrives.

    No tolerance anywhere, which is the decision. A range and a point are not
    the same claim even when the point sits at the range's middle; a wider range
    is not the same claim as a narrower one; and two documents stating a figure
    to different precision disagree about what it is, which is a reviewer's call
    and not a rounding rule's.
    """
    from base_document import _conflicts

    def packet_with(value, document):
        span = "a stated figure"
        source = "before " + span + " after"
        return dataclasses.replace(
            assemble("OPP-ONE", ""),
            fields=(SourcedFigure(REVENUE, value, span, source, document,
                                  OPPORTUNITY),),
        )

    pairs = {
        ((1.0, 6.0), (3.5, 3.5)): True,
        ((1.0, 6.0), (1.0, 5.0)): True,
        ((38.4e6, 38.4e6), (38.42e6, 38.42e6)): True,
        ((1.0, 6.0), (1.0, 6.0)): False,
    }
    for (first, second), differs in pairs.items():
        conflicts = _conflicts([packet_with(first, ATTACHED),
                                packet_with(second, source_span.PAPER)])
        assert bool(conflicts) is differs, (first, second)


def test_the_conflict_list_reads_in_the_order_the_deck_reads():
    """Not the order the documents happened to state things in, so a reviewer's
    list does not reorder itself between runs."""
    packet = contested()
    assert [conflict.field for conflict in packet.conflicts] == [
        REVENUE, EBITDA, MARGIN, base_document.SCENARIOS,
    ]
    reversed_lead = merge_packets([
        assemble("OPP-ONE", CONTESTED_PAPER, ATTACHED),
        assemble("OPP-ONE", CONTESTED_ATTACHMENT, source_span.PAPER),
    ])
    assert [conflict.field for conflict in reversed_lead.conflicts] == [
        conflict.field for conflict in packet.conflicts
    ]


def test_a_conflict_survives_three_documents_and_lists_all_three_answers():
    third = """### Current State

| Metric | CY 2021 | CY 2022 | Change |
|---|---|---|---|
| **Revenue** | $4.0M | $5.0M | +25.0% |
| **EBITDA** | $1.0M | $1.2M | +20.0% |
| **EBITDA Margin** | 25.0% | 24.0% | -1.0pp |
"""
    packet = merge_packets([
        assemble("OPP-ONE", CONTESTED_ATTACHMENT, ATTACHED),
        assemble("OPP-ONE", CONTESTED_PAPER, Document(name="second.md", kind="markdown")),
        assemble("OPP-ONE", third, source_span.PAPER),
    ])
    conflict = conflict_on(packet, REVENUE)
    assert len(conflict.answers) == 3
    assert [answer.document.name for answer in conflict.answers] == [
        "attachment.md", "second.md", "",
    ]


# ------------------------------------------------- every span names a document


def test_every_figure_in_a_merged_packet_names_its_own_document():
    packet = contested()
    for figure in packet.fields:
        assert isinstance(figure.document, Document)
        assert figure.document == ATTACHED
    for case in packet.scenarios:
        assert case.direct_uplift_usd_yr.document == ATTACHED


def test_a_merged_packet_can_hold_figures_from_two_documents_at_once():
    """The state that made this whole step necessary."""
    packet = merge_packets([
        assemble("OPP-ONE", SCENARIOS_ONLY, ATTACHED),
        assemble("OPP-ONE", paper(SPARSE["shape"]), source_span.PAPER),
    ])
    documents = {figure.document for figure in packet.fields}
    documents |= {case.direct_uplift_usd_yr.document for case in packet.scenarios}
    assert documents == {ATTACHED, source_span.PAPER}


def test_a_run_with_no_attachment_names_the_published_paper_everywhere():
    """The test that must not break. Every span in a packet from a run with no
    attachment belongs to the published paper, which is what every span in this
    repo belonged to before today."""
    packet = assemble("OPP-ONE", paper("scenario-rows-canonical"))
    assert packet.fields
    for figure in packet.fields:
        assert figure.document == source_span.PAPER
        assert figure.document.uploaded is False
    for case in packet.scenarios:
        assert case.direct_uplift_usd_yr.document == source_span.PAPER
    assert packet.conflicts == ()


# --------------------------------------- the figures the group rule declined


def declined():
    """An attachment stating revenue only against a paper stating all three,
    which is the shape that makes the group rule decline something. The PRD
    shape: it prices the work and restates no margin."""
    return merge_packets([
        assemble("OPP-ONE", REVENUE_ONLY, ATTACHED),
        assemble("OPP-ONE", OLDER_FULL_BASELINE, source_span.PAPER),
    ])


def set_aside_on(packet, field):
    found = [record for record in packet.set_aside if record.field == field]
    return found[0] if found else None


def test_a_packet_from_one_document_sets_nothing_aside():
    """A merge of one declines nothing, and this is every run with no
    attachment."""
    single = assemble("OPP-ONE", paper(SPARSE["shape"]))
    assert single.set_aside == ()
    assert merge_packets([single]).set_aside == ()


def test_the_figure_the_group_rule_declined_is_written_down():
    """The gap this closes. Without it a reviewer attaches a PRD, sees EBITDA
    missing, and types in by hand the figure the published paper was holding the
    whole time, with nothing anywhere saying that happened or why."""
    packet = declined()
    # The packet does not carry it, which is the group rule and stays.
    assert EBITDA not in figures(packet)
    assert EBITDA in [name for name, _why in packet.missing_fields]

    record = set_aside_on(packet, EBITDA)
    assert record is not None
    assert record.answer.document == source_span.PAPER
    assert record.answer.value == (3_000_000.0, 3_000_000.0)
    assert record.answer.span in OLDER_FULL_BASELINE


def test_a_set_aside_record_says_what_took_the_figures_place():
    """What step 4 needs to say why: the baseline is led by another document,
    for a different period."""
    record = set_aside_on(declined(), EBITDA)
    assert record.rule == base_document.BASELINE_GROUP_RULE
    assert record.lead == ATTACHED
    assert record.lead_period == assemble(
        "OPP-ONE", REVENUE_ONLY, ATTACHED
    ).baseline_period
    assert record.period == assemble(
        "OPP-ONE", OLDER_FULL_BASELINE, source_span.PAPER
    ).baseline_period
    assert record.lead_period != record.period


def test_every_figure_the_group_rule_declined_is_recorded_and_no_others():
    packet = declined()
    assert {record.field for record in packet.set_aside} == {EBITDA, MARGIN}
    # Revenue was decided by precedence rather than by the group rule, so it is
    # a conflict and not a set-aside. The two lists do not overlap here.
    assert set_aside_on(packet, REVENUE) is None
    assert conflict_on(packet, REVENUE) is not None


def test_a_set_aside_is_not_a_conflict_and_a_conflict_is_not_a_set_aside():
    """The distinction the two lists exist to keep. A reviewer reading a
    conflict has a decision; a reviewer reading a set-aside has only something
    to know."""
    packet = declined()
    contested_fields = {conflict.field for conflict in packet.conflicts}
    declined_fields = {record.field for record in packet.set_aside}
    assert not contested_fields & declined_fields


def test_a_field_both_documents_answered_is_never_set_aside():
    """Two answers is a disagreement, which is the conflict list's business."""
    packet = contested()
    assert packet.conflicts
    assert packet.set_aside == ()


def test_the_lead_declining_nothing_sets_nothing_aside():
    """An attachment stating all three leaves the paper's baseline with nothing
    to contribute and nothing to report: the reverse direction, unchanged."""
    packet = merge_packets([
        assemble("OPP-ONE", BASELINE_ONLY, ATTACHED),
        assemble("OPP-ONE", paper(SPARSE["shape"]), source_span.PAPER),
    ])
    assert set_aside_on(packet, EBITDA) is None
    assert {record.field for record in packet.set_aside} == set()


def test_a_source_carrying_no_baseline_at_all_declines_nothing_of_its_own():
    packet = merge_packets([
        assemble("OPP-ONE", SCENARIOS_ONLY, ATTACHED),
        assemble("OPP-ONE", OLDER_FULL_BASELINE, source_span.PAPER),
    ])
    # The paper leads the baseline here, so all three are carried and nothing is
    # declined.
    assert {REVENUE, EBITDA, MARGIN} <= set(figures(packet))
    assert packet.set_aside == ()


def test_three_documents_each_get_their_own_record_in_precedence_order():
    third = """### Current State

| Metric | CY 2020 | CY 2021 | Change |
|---|---|---|---|
| **Revenue** | $4.0M | $5.0M | +25.0% |
| **EBITDA** | $1.0M | $1.2M | +20.0% |
| **EBITDA Margin** | 25.0% | 24.0% | -1.0pp |
"""
    second = Document(name="second.md", kind="markdown")
    packet = merge_packets([
        assemble("OPP-ONE", REVENUE_ONLY, ATTACHED),
        assemble("OPP-ONE", OLDER_FULL_BASELINE, second),
        assemble("OPP-ONE", third, source_span.PAPER),
    ])
    ebitda = [record for record in packet.set_aside if record.field == EBITDA]
    assert [record.answer.document for record in ebitda] == [second, source_span.PAPER]
    for record in ebitda:
        assert record.lead == ATTACHED


def test_the_set_aside_list_reads_in_the_order_the_deck_reads():
    packet = declined()
    assert [record.field for record in packet.set_aside] == [EBITDA, MARGIN]


def test_the_records_are_frozen():
    record = set_aside_on(declined(), EBITDA)
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.field = "another.field"
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.answer.value = (1.0, 1.0)


def test_the_group_rule_itself_did_not_move():
    """Setting the figure aside is right. This step only says so."""
    packet = declined()
    assert figures(packet)[REVENUE] == (38_400_000.0, 38_400_000.0)
    assert packet.baseline_period == ATTACHED_PERIOD
    assert EBITDA not in figures(packet)
    assert MARGIN not in figures(packet)


# ------------------------------- the model leg follows the same rule
#
# THE REGRESSION, from a live run on 2026-09-12, and the most serious thing in
# item 14: attaching a document made a run that works fail.
#
#   no attachment:     completeness 0.83, ok, deck generates
#   the PRD attached:  completeness 0.17, E_LOW_CONFIDENCE, refused
#
# The extraction pass is what rescues figures the deterministic parsers cannot
# lift out of a document's tables, and it read the BASE document only. With
# nothing attached the base IS the paper, so the pass read the paper and the
# packet reached the floor. Attach a PRD and the base becomes the PRD, which
# states the engagement and not the client's trailing revenue, so three baseline
# figures the paper states in full stayed absent.
#
# It hid because the opportunity's earlier paper parsed cleanly and the rescue
# was never load-bearing. The dependency is only visible when it is.
#
# The runs below are that control, in a form a suite can hold: a published paper
# whose figures are in PROSE (`prose_paper` scores 0.00 through every parser in
# this repo), an attachment that does not state them, and one scripted answer
# whose spans decide which document each figure can verify against. Nothing here
# is a judgement about which document is better; the rule is per field.

PRD_PROSE = """# Mobile field data capture

## The Proposed Solution

A mobile ticket capture app records the work at the job site, so the ticket
leaves with the crew rather than arriving days later.

## Next Steps

Leadership approves the scope and the phased plan before any build begins.
"""

PRD_SPAN = ("A mobile ticket capture app records the work at the job site, so "
            "the ticket\nleaves with the crew rather than arriving days later.")


def chain_answer(paper_text):
    """One scripted answer covering BOTH documents, and the spans decide.

    Deliberately one payload rather than two. The fake extractor returns it
    whatever text it is handed, and `paper_extraction.read_response` verifies
    every span against that text, so the roster items can only verify against
    the paper and the platform item can only verify against the attachment.
    A call that was handed the wrong document therefore fills nothing, which is
    what the live run's PRD did and what this has to reproduce.
    """
    from test_paper_extraction import answer, field, item

    assert PRD_SPAN in PRD_PROSE
    rows = [line for line in paper_text.splitlines() if line.startswith("| **Phase")]
    cases = [line for line in paper_text.splitlines()
             if line.startswith(("| Conservative", "| Base Case", "| Optimistic"))]
    return answer(
        field("platform_layers",
              item(PRD_SPAN, "The Proposed Solution",
                   title="A mobile ticket capture app", body=PRD_SPAN)),
        field(REVENUE, item("The company reported LTM revenue of $84.0M",
                            "Financial Analysis > Current State", value="$84.0M")),
        field(EBITDA, item("adjusted EBITDA of $12.6M",
                           "Financial Analysis > Current State", value="$12.6M")),
        field(MARGIN, item("adjusted EBITDA margin of 15.0%",
                           "Financial Analysis > Current State", value="15.0%")),
        field("commercial.scenarios", *(
            item(line, "Financial Analysis > Projected Impact",
                 name=line.split("|")[1].strip(),
                 direct_uplift_usd_yr=line.split("|")[2].strip(),
                 margin_gain_pp=line.split("|")[3].strip())
            for line in cases
        )),
        field(TIMELINE, *(
            item(row, "Implementation Approach > Timeline",
                 label=row.split("|")[1].strip().strip("*"),
                 start=start, end=end, unit="months")
            for row, start, end in zip(rows, ("1", "3", "6"), ("3", "6", "12"))
        )),
    )


def prose_run(uploads=(), spec=CLIENTS["one"]):
    """One run against a paper that states its figures in prose only.

    Returns `(envelope, calls)`, where `calls` is one entry per extraction call
    the run made, so a test can assert both what came out and what it cost.
    """
    from test_paper_extraction import prose_paper
    from test_second_pass import extractor_returning

    paper_text = prose_paper()
    calls = []
    client = StubClient(spec, opportunities=[
        (dict(spec["opportunity"]), paper_text)])
    provider = LiveProposalProvider(
        client, generated_at=STAMP,
        extractor=extractor_returning(chain_answer(paper_text), calls),
    )
    handle = provider.submit(request(spec, floor=FLOOR), uploads=list(uploads))
    return provider.poll(handle)["envelope"], calls


def completeness_of(envelope):
    import data_source_adapter

    if envelope["status"] == "error":
        return envelope["error"]["details"].get("data_completeness")
    return data_source_adapter._read_packet_frontmatter(
        envelope["packet"])["data_completeness"]


def test_the_control_run_with_nothing_attached_clears_the_floor():
    """The left-hand column of the live control. The parsers read nothing off
    this paper; the extraction pass reads its prose and the packet clears."""
    envelope, calls = prose_run()
    assert envelope["status"] == "ok", envelope
    assert completeness_of(envelope) == 1.0
    assert len(calls) == 1


def test_attaching_a_document_does_not_cost_the_run_the_paper_s_own_figures():
    """The regression, and the pass condition the live run sets: parity. The
    attachment states none of the six roster figures — no PRD of any quality
    states a client's trailing revenue — and before the rescue existed this run
    scored what the parsers alone could read and was refused."""
    attached, _calls = prose_run(uploads=[upload(PRD_PROSE, "a-prd.md")])
    alone, _calls = prose_run()

    assert attached["status"] == "ok", attached
    assert completeness_of(attached) == completeness_of(alone) == 1.0


def test_a_rescued_figure_names_the_document_it_was_read_from():
    """One document per call is what keeps this true, and it is the other reason
    the rescue is a chain rather than one prompt holding both documents."""
    attached, _calls = prose_run(uploads=[upload(PRD_PROSE, "a-prd.md")])
    filled = {row["field"]: row for row in attached["provenance"]["filled"]}
    for path in (REVENUE, EBITDA, MARGIN):
        assert filled[path]["document"] == "the published opportunity paper"
        assert filled[path]["uploaded"] is False


def test_the_attachment_keeps_its_own_contributions():
    """The half an all-or-nothing fallback would have thrown away. The live
    PRD supplied 39 quoted values and the deck's whole specific substance while
    lacking the baseline P&L."""
    attached, _calls = prose_run(uploads=[upload(PRD_PROSE, "a-prd.md")])
    quoted = [row for row in attached["provenance"]["quoted"]
              if row["document"] == "a-prd.md"]
    assert quoted
    assert {row["field"] for row in quoted} == {"platform_layers"}


def test_the_second_call_asks_only_for_what_the_base_left_absent():
    """Per field, not per document. The document half is not re-asked: those are
    the engagement's own specifics and the base document is the document about
    the engagement."""
    _attached, calls = prose_run(uploads=[upload(PRD_PROSE, "a-prd.md")])
    assert len(calls) == 2
    first, second = calls[0][0], calls[1][0]
    assert "platform_layers" in first
    assert set(second) == {REVENUE, EBITDA, MARGIN, TIMELINE, "commercial.scenarios"}
    assert "platform_layers" not in second


def test_a_base_document_that_answers_everything_costs_no_second_call():
    """The cost rule. The second call fires only while a roster field is still
    absent, so a run whose base answered the roster is exactly as fast as it was
    before the chain existed."""
    _envelope, calls = prose_run(uploads=[upload(PRD_PROSE, "a-prd.md")])
    assert len(calls) == 2

    from test_paper_extraction import prose_paper

    # The same paper attached as the base: the roster verifies on the first
    # call, so there is nothing left to ask anyone else for.
    _envelope, calls = prose_run(
        uploads=[upload(prose_paper(), "the-same-prose.md")])
    assert len(calls) == 1


def test_a_run_with_no_attachment_makes_exactly_the_one_call_it_always_made():
    """The chain has no supporting source on that run, so this function is not
    even entered. The run that shipped is the run that ships."""
    _envelope, calls = prose_run()
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# ITEM 15: the engagement chain, and a unit larger than a field.
#
# A run holds two kinds of chain now. `precedence` is one opportunity's, the
# shared uploads then its OWN paper and no other, which is what makes "A's slide
# is never written from B's paper" a fact about construction rather than a check
# downstream. `engagement_precedence` is the run's, every source in it, and it
# resolves the two units that are facts about the COMPANY and the ENGAGEMENT
# rather than about any one opportunity.
# ---------------------------------------------------------------------------

def packets_for(sources):
    return [assemble("OPP", source.text) for source in sources]


def test_a_per_opportunity_chain_holds_its_own_paper_and_no_other():
    """The construction that makes the whole item safe."""
    chain = precedence(OLDER_FULL_BASELINE, [document(SCENARIOS_ONLY)])
    texts = [source.text for source in chain]
    assert REVENUE_ONLY not in texts
    assert texts == [SCENARIOS_ONLY.strip(), OLDER_FULL_BASELINE]


def test_the_engagement_chain_is_uploads_first_then_every_paper_in_order():
    chain = engagement_precedence([OLDER_FULL_BASELINE, REVENUE_ONLY],
                                  [document(SCENARIOS_ONLY)])
    assert [source.uploaded for source in chain] == [True, False, False]
    assert [source.text for source in chain] == [
        SCENARIOS_ONLY.strip(), OLDER_FULL_BASELINE, REVENUE_ONLY,
    ]


def test_one_opportunity_gives_an_engagement_chain_identical_to_its_own():
    """Which is what keeps every run that works today working."""
    assert engagement_precedence([OLDER_FULL_BASELINE],
                                 [document(SCENARIOS_ONLY)]) == \
           precedence(OLDER_FULL_BASELINE, [document(SCENARIOS_ONLY)])


def test_a_paper_with_no_text_is_dropped_from_a_chain_that_has_an_attachment():
    """The same rule `precedence` applies, and for the same reason: a source
    with no text contributes nothing and would claim a place in a list the
    reviewer is about to be shown."""
    chain = engagement_precedence(["", OLDER_FULL_BASELINE],
                                  [document(SCENARIOS_ONLY)])
    assert len(chain) == 2
    assert [source.uploaded for source in chain] == [True, False]


# --------------------------------------------- the rule, stated once

def test_take_whole_picks_the_first_source_that_states_the_unit():
    assert take_whole([(), ("a",), ("b",)]) == (1, (2,))
    assert take_whole([("a",), (), ("b",)]) == (0, (2,))


def test_take_whole_declines_nothing_when_only_one_source_states_it():
    assert take_whole([(), ("a",), ()]) == (1, ())


def test_take_whole_leads_with_the_first_source_when_nobody_states_it():
    """What keeps a unit no source answered behaving exactly as it did before
    units existed: nothing is declined and every name falls through to the
    absence list with its own parsers' reason."""
    assert take_whole([(), (), ()]) == (0, ())


def test_take_whole_reads_truthiness_and_nothing_about_packets():
    """It is the one statement of the rule and it has two callers holding
    different things: `Packet`s and their figures at the packet level, fill maps
    and their record lists at the fill level. A helper that knew about either
    could not serve both, and two helpers is how the baseline label and the
    baseline figures came apart for a day."""
    assert take_whole([[], [{"a": 1}], [{"b": 2}]]) == (1, (2,))
    assert take_whole([None, "", "stated"]) == (2, ())


def test_the_merge_reads_its_units_off_the_table_rather_than_naming_them():
    """A further group is a row in `UNITS`, not an edit to the merge."""
    import base_document as module

    names = {field for unit in module.UNITS for field in unit.fields}
    assert {REVENUE, EBITDA, MARGIN} == names
    assert {unit.rule for unit in module.UNITS} == {module.BASELINE_GROUP_RULE}


def test_the_timeline_is_not_a_group_rule_and_the_merge_says_so():
    """THE CORRECTION WORTH KEEPING. A unit rule only has work to do when a unit
    spans SEVERAL names: it is what stops a lower source supplying some while a
    higher one supplied the rest. `timeline.phases` is ONE field holding a whole
    phase list, so it is taken whole by construction and was before units
    existed. A one-name unit's lead states its only name by definition, so such
    a rule could never decline anything, and a rule that can never fire is worse
    than no rule because a reader believes it is doing something."""
    import base_document as module

    assert TIMELINE not in {f for unit in module.UNITS for f in unit.fields}
    assert TIMELINE not in {f for unit in module.SHARED_UNITS for f in unit.fields}


def test_two_documents_stating_a_timeline_disagree_rather_than_declining():
    """Which is what a `Conflict` is for and a `SetAside` is not. The first
    source to state it supplies it whole, and the reviewer is shown the
    disagreement rather than a figure nobody chose between."""
    first, second = paper("scenario-rows-canonical"), paper("scenario-columns")
    merged = merge_packets([assemble("OPP", first), assemble("OPP", second)])

    timelines = [f for f in merged.fields if f.field == TIMELINE]
    assert len(timelines) == 1
    assert timelines[0].span in first
    assert [a for a in merged.set_aside if a.field == TIMELINE] == []
    assert [c for c in merged.conflicts if c.field == TIMELINE]


def test_the_transplant_leaves_an_opportunitys_own_timeline_alone():
    """The timeline is a fact about ONE opportunity's build (2026-09-22). The
    transplant used to put the engagement lead's schedule on every packet, which
    printed the onboarding paper's 44-week plan on the pipeline cockpit's
    slide. An opportunity keeps the schedule its own chain stated, and nothing
    is set aside for it, because nothing was declined."""
    first, second = paper("scenario-rows-canonical"), paper("scenario-columns")
    own = merge_packets([assemble("OPP-B", second)])
    engagement = merge_packets([assemble("OPP-A", first),
                                assemble("OPP-B", second)])

    shared = apply_shared(own, engagement)

    carried = next(f for f in shared.fields if f.field == TIMELINE)
    assert carried == next(f for f in own.fields if f.field == TIMELINE)
    assert [a for a in shared.set_aside if a.field == TIMELINE] == []


def test_the_baseline_rule_is_unchanged_by_being_generalised():
    """The rule that was right on 2026-09-07 has to still be right after being
    moved onto a table. The partial case is the one that had no coverage then
    and is the one that matters now."""
    merged = merge_packets([
        assemble("OPP", REVENUE_ONLY), assemble("OPP", OLDER_FULL_BASELINE),
    ])
    carried = {f.field: f for f in merged.fields}

    assert carried[REVENUE].value == (38_400_000.0, 38_400_000.0)
    assert EBITDA not in carried and MARGIN not in carried
    assert merged.baseline_period == "CY 2025"


# --------------------------------------------- the transplant

def test_apply_shared_puts_the_engagement_baseline_on_a_thin_opportunity():
    """THE CASE THE ENGAGEMENT CHAIN EXISTS FOR. This opportunity's own chain
    states no baseline at all; the company's baseline is stated by another
    opportunity's paper, about the same company, and the deck prints it once."""
    thin = merge_packets([assemble("OPP-B", SCENARIOS_ONLY)])
    engagement = merge_packets([
        assemble("OPP-B", SCENARIOS_ONLY), assemble("OPP-A", OLDER_FULL_BASELINE),
    ])
    assert REVENUE not in {f.field for f in thin.fields}

    shared = apply_shared(thin, engagement)

    carried = {f.field: f for f in shared.fields}
    assert carried[REVENUE].value == (12_000_000.0, 12_000_000.0)
    assert shared.baseline_period == "CY 2023"
    # And its own scenarios are untouched, because those ARE its own.
    assert shared.scenarios == thin.scenarios


def test_apply_shared_records_a_displaced_figure_rather_than_dropping_it():
    """A document stated a figure and the deck is not printing it, which is the
    same fact `set_aside` already exists for."""
    own = merge_packets([assemble("OPP-A", REVENUE_ONLY)])
    engagement = merge_packets([
        assemble("OPP-B", OLDER_FULL_BASELINE), assemble("OPP-A", REVENUE_ONLY),
    ])

    shared = apply_shared(own, engagement)

    carried = {f.field: f for f in shared.fields}
    assert carried[REVENUE].value == (12_000_000.0, 12_000_000.0)
    declined = [a for a in shared.set_aside if a.field == REVENUE]
    assert len(declined) == 1
    assert declined[0].answer.value == (38_400_000.0, 38_400_000.0)
    assert declined[0].rule == base_document.BASELINE_GROUP_RULE


def test_apply_shared_records_nothing_when_the_figure_taken_is_its_own():
    """A figure replaced by an equal one declined nothing, so a reviewer is not
    shown a row about a decision nobody made."""
    own = merge_packets([assemble("OPP-A", OLDER_FULL_BASELINE)])
    engagement = merge_packets([
        assemble("OPP-A", OLDER_FULL_BASELINE), assemble("OPP-B", SCENARIOS_ONLY),
    ])

    shared = apply_shared(own, engagement)

    assert [a for a in shared.set_aside if a.field == REVENUE] == []
    assert {f.field for f in shared.fields} == {f.field for f in own.fields}


def test_apply_shared_recomputes_what_is_still_missing():
    """A name this opportunity left absent and the engagement answered is not
    missing, and a name absent after the transplant keeps its own reason."""
    thin = merge_packets([assemble("OPP-B", SCENARIOS_ONLY)])
    engagement = merge_packets([
        assemble("OPP-B", SCENARIOS_ONLY), assemble("OPP-A", OLDER_FULL_BASELINE),
    ])

    shared = apply_shared(thin, engagement)
    absent = {name for name, _why in shared.missing_fields}

    assert REVENUE not in absent
    assert shared.present.isdisjoint(absent)
    for name, why in shared.missing_fields:
        assert why.strip()


def test_apply_shared_leaves_the_opportunitys_own_packet_alone_on_one_opportunity():
    """Every single-opportunity run, by identity."""
    own = merge_packets([assemble("OPP-A", OLDER_FULL_BASELINE)])
    assert apply_shared(own, own) is own
    assert apply_shared(own, None) is own


def test_apply_shared_does_not_copy_engagement_conflicts_onto_every_opportunity():
    """A disagreement between two documents about the company's baseline is one
    fact about the run, not one fact per opportunity. Copying it would show a
    reviewer the same row as many times as the deck has slides."""
    own = merge_packets([assemble("OPP-A", SCENARIOS_ONLY)])
    engagement = merge_packets([
        assemble("OPP-A", REVENUE_ONLY), assemble("OPP-B", OLDER_FULL_BASELINE),
    ])
    assert engagement.set_aside

    shared = apply_shared(own, engagement)

    assert shared.conflicts == own.conflicts


# ---------------------------------------------------------------------------
# ITEM 15, step five: the provider assembles per opportunity.
# ---------------------------------------------------------------------------

# A second opportunity for the same company, with its own paper. A DIFFERENT
# committed excerpt, so the two state different figures and different scenario
# cases and a value crossing between them is detectable rather than a matter of
# interpretation.
SECOND_PAPER = paper("scenario-columns")


def two_opportunity_client(first_paper=None, second_paper=SECOND_PAPER):
    """One company with two published opportunities, each with its own paper."""
    spec = CLIENTS["one"]
    first = dict(spec["opportunity"], id="OPP-ONE", title="First Opportunity")
    second = dict(spec["opportunity"], id="OPP-TWO", title="Second Opportunity",
                  description="A second placeholder description.")
    return spec, StubClient(spec, opportunities=[
        (first, first_paper if first_paper is not None else paper(spec["shape"])),
        (second, second_paper),
    ])


def two_opportunity_envelope(uploads=(), floor=0.0, **kwargs):
    spec, client = two_opportunity_client(**kwargs)
    provider = LiveProposalProvider(client, generated_at=STAMP)
    handle = provider.submit(
        request(spec, floor=floor, opportunity_ids=["OPP-ONE", "OPP-TWO"]),
        uploads=uploads,
    )
    return provider.poll(handle)["envelope"]


def test_a_two_opportunity_run_produces_two_section_two_entries():
    envelope = two_opportunity_envelope()
    assert envelope["status"] == "ok", envelope
    entries = _section_yaml(_split_sections(envelope["packet"])[2])["opportunities"]
    assert len(entries) == 2
    assert [entry.get("headline") for entry in entries] == [
        "First Opportunity", "Second Opportunity",
    ]


def test_no_opportunitys_section_is_written_from_the_others_paper():
    """THE PROPERTY THE WHOLE ITEM EXISTS FOR, end to end through the provider
    rather than at a seam.

    Each section carries its OWN opportunity's identity and nothing of the
    other's. What the two sections legitimately SHARE is named in
    `base_document.SHARED_UNITS` and is shared on purpose: the company's
    baseline is one fact about one company. The phased plan is NOT shared, and
    `test_each_opportunity_keeps_its_own_build_and_its_own_duration` holds it.
    """
    envelope = two_opportunity_envelope()
    entries = _section_yaml(_split_sections(envelope["packet"])[2])["opportunities"]

    assert entries[0]["headline"] == "First Opportunity"
    assert entries[1]["headline"] == "Second Opportunity"
    assert "Second Opportunity" not in repr(entries[0])
    assert "First Opportunity" not in repr(entries[1])


# --- each opportunity's own build, off the real Contoso charts ---------
#
# The two papers' `plan_phases` charts as the platform served them on
# 2026-09-22, labels and spans verbatim, so this needs no live call. On the
# first two-opportunity deck the recruiting slide printed the onboarding
# labels and "THE 44-WEEK BUILD", because `apply_shared` shared the timeline.

ONBOARDING_PHASES = (
    ("Phase 1 \u2014 Baseline measurement and target-state design", [0, 8]),
    ("Phase 2 \u2014 Document intake, entity modelling, form generation", [8, 20]),
    ("Phase 3 \u2014 Account opening, transfers, exception queue", [20, 32]),
    ("Phase 4 \u2014 Funded reconciliation, telemetry, hiring gate", [32, 44]),
)
RECRUITING_PHASES = (
    ("Market-signal screening", [0, 6]),
    ("Recruiting pipeline tracker", [6, 11]),
    ("Candidate-book diligence pack", [11, 17]),
    ("Comp and earn-out model", [17, 24]),
)


def plan_phases_chart(name, phases):
    """A `<Chart>` of the shape the platform publishes a build plan in."""
    config = {
        "type": "bar",
        "data": {"labels": [label for label, _span in phases],
                 "datasets": [{"label": "Weeks",
                               "data": [span for _label, span in phases]}]},
        "options": {"indexAxis": "y", "scales": {"x": {
            "beginAtZero": True, "title": {"display": True, "text": "Week"}}}},
        "qofaiProvenance": {"kind": "plan_phases"},
    }
    return f"\n\n<Chart\n  name=\"{name}\"\n  config='{json.dumps(config)}'\n/>\n"


def own_build_envelope():
    """Two opportunities whose papers state their builds and nothing else does.

    Both start from the committed excerpt that carries NO timeline chart, so
    the only schedule either paper states is the one appended here."""
    base = paper("no-timeline-chart")
    return two_opportunity_envelope(
        first_paper=base + plan_phases_chart("Onboarding build", ONBOARDING_PHASES),
        second_paper=base + plan_phases_chart("Recruiting build", RECRUITING_PHASES),
    )


def _slides(prompt):
    """The prompt's slides by header, whitespace normalised."""
    return {chunk.split("\n", 1)[0]: " ".join(chunk.split())
            for chunk in prompt.split("## Slide ")[1:]}


def test_each_opportunity_keeps_its_own_build_and_its_own_duration():
    envelope = own_build_envelope()
    assert envelope["status"] == "ok", envelope
    entries = _section_yaml(_split_sections(envelope["packet"])[2])["opportunities"]

    builds = [entry["build_summary"] for entry in entries]
    assert [phase["label"] for phase in builds[0]["phases"]] == [
        label for label, _span in ONBOARDING_PHASES]
    assert [phase["label"] for phase in builds[1]["phases"]] == [
        label for label, _span in RECRUITING_PHASES]
    # THE DURATION IS THE SHARPER TEST. A label set can look plausibly wrong;
    # a duration is a number a client acts on.
    assert (builds[0]["duration"], builds[0]["duration_unit"]) == (44, "weeks")
    assert (builds[1]["duration"], builds[1]["duration_unit"]) == (24, "weeks")


def test_the_prompt_prints_each_opportunitys_own_horizon():
    """On the artifact, not the packet: the recruiting slide said 44 weeks in
    the generated prompt, and that prompt is what the renderer reads."""
    from prompt_assembler import assemble_prompt
    from template_loader import load_template

    envelope = own_build_envelope()
    spec = CLIENTS["one"]
    placeholder_map = map_packet(envelope["packet"], request(spec, floor=0.0))
    slides = _slides(assemble_prompt(
        load_template("templates/proposal-template.md"), placeholder_map))
    onboarding = next(body for header, body in slides.items()
                      if "First Opportunity" in body and "Opportunity" in header)
    recruiting = next(body for header, body in slides.items()
                      if "Second Opportunity" in body and "Opportunity" in header)

    assert "build_summary: THE 44-WEEK BUILD" in onboarding
    assert "build_summary: THE 24-WEEK BUILD" in recruiting
    assert "44-WEEK" not in recruiting and "~44 WEEKS" not in recruiting
    assert "Market-signal screening" in recruiting
    assert "Baseline measurement" not in recruiting
    assert "Market-signal screening" not in onboarding


def test_the_engagement_baseline_reaches_every_opportunity():
    """The company's trailing revenue is one fact about one company, so both
    sections carry it and the deck prints it once."""
    envelope = two_opportunity_envelope()
    baseline = _section_yaml(_split_sections(envelope["packet"])[1])["baseline"]
    assert baseline["revenue_ttm_usd"] is not None


def test_each_opportunity_gets_its_own_platform_plan_and_next_steps():
    """Slides 3, 4 and 6 repeat per opportunity (Antonio, 2026-09-22). The
    flat yaml stays the lead's, which is what a one-opportunity packet has
    always carried, and each opportunity's own values ride beside it."""
    envelope = two_opportunity_envelope()
    sections = _split_sections(envelope["packet"])
    for number in (4, 5, 7):
        entries = _section_yaml(sections[number])["opportunities"]
        assert len(entries) == 2, number
        assert all(entry.get("headline") for entry in entries), number


def test_the_envelope_says_what_each_section_scored():
    """A deck quietly carrying one good section and one thin one is the shape
    most likely to reach a client with nobody having noticed, so the score is
    per opportunity and it is reviewer-facing."""
    envelope = two_opportunity_envelope()
    marks = envelope["opportunities"]

    assert [mark["opportunity_id"] for mark in marks] == ["OPP-ONE", "OPP-TWO"]
    for mark in marks:
        assert 0.0 <= mark["data_completeness"] <= 1.0
        assert isinstance(mark["cleared"], bool)
        assert mark["title"]


def test_a_thin_opportunity_is_marked_and_does_not_sink_the_deck():
    """The live possibility this rule was written for: a replaced opportunity's
    new paper parsing to one field where the old gave five."""
    envelope = two_opportunity_envelope(
        second_paper="# A paper stating nothing a parser can read.\n",
        floor=FLOOR,
    )
    assert envelope["status"] == "ok", envelope

    marks = {mark["opportunity_id"]: mark for mark in envelope["opportunities"]}
    assert marks["OPP-ONE"]["cleared"] is True
    assert marks["OPP-TWO"]["cleared"] is False
    assert marks["OPP-TWO"]["missing_fields"]


def test_a_deck_where_nothing_clears_is_still_refused():
    """The floor does not move. One opportunity reduces to exactly this, which
    is why the sparse paper is still turned away."""
    spec, client = two_opportunity_client(
        first_paper="# Nothing here.\n", second_paper="# Nor here.\n")
    provider = LiveProposalProvider(client, generated_at=STAMP)
    envelope = provider.poll(provider.submit(
        request(spec, floor=FLOOR, opportunity_ids=["OPP-ONE", "OPP-TWO"])
    ))["envelope"]

    assert envelope["status"] == "error"
    assert envelope["error"]["code"] == "E_LOW_CONFIDENCE"
    assert "No section cleared" in envelope["error"]["message"]
    assert len(envelope["error"]["details"]["opportunities"]) == 2


def test_a_singular_opportunity_id_still_means_a_deck_of_one():
    """Every caller that submitted a request before today submits exactly the
    request it did."""
    spec, client = two_opportunity_client()
    provider = LiveProposalProvider(client, generated_at=STAMP)
    envelope = provider.poll(provider.submit(
        request(spec, floor=0.0, opportunity_id="OPP-TWO")
    ))["envelope"]

    assert envelope["status"] == "ok", envelope
    entries = _section_yaml(_split_sections(envelope["packet"])[2])["opportunities"]
    assert len(entries) == 1
    assert entries[0]["headline"] == "Second Opportunity"


def test_asking_for_one_opportunity_twice_renders_it_once():
    spec, client = two_opportunity_client()
    provider = LiveProposalProvider(client, generated_at=STAMP)
    envelope = provider.poll(provider.submit(
        request(spec, floor=0.0, opportunity_ids=["OPP-ONE", "OPP-ONE"])
    ))["envelope"]

    entries = _section_yaml(_split_sections(envelope["packet"])[2])["opportunities"]
    assert len(entries) == 1


def test_the_fill_level_merge_calls_the_same_helper_the_packet_merge_does():
    """THE ONE-RULE PROPERTY, asserted structurally rather than by behaviour.

    `platform_layers` and `next_steps` are not packet fields, so their merge
    cannot live in `merge_packets` and has to happen at the fill level. The
    hazard is that it gets RESTATED there in fill-map terms, which is exactly
    how the baseline label and the baseline figures came apart for a day on
    2026-09-07. So the provider calls `base_document.take_whole` and the call is
    asserted off the parsed source."""
    import ast

    source = pathlib.Path(
        base_document.__file__).parent / "live_proposal_provider.py"
    tree = ast.parse(source.read_text())
    merge = next(node for node in ast.walk(tree)
                 if isinstance(node, ast.FunctionDef)
                 and node.name == "_merge_shared_records")
    called = {ast.unparse(node.func) for node in ast.walk(merge)
              if isinstance(node, ast.Call)}
    assert "base_document.take_whole" in called


def test_the_two_merged_lists_come_whole_from_one_lead():
    """Splicing two documents' numbered component lists would state a platform
    neither document states, which is the argument `merge_packets` already makes
    about scenario tables."""
    envelope = two_opportunity_envelope()
    layers = _section_yaml(_split_sections(envelope["packet"])[4])["platform_layers"]
    numbers = [layer.get("number") for layer in layers]
    assert numbers == sorted(numbers)
    assert len(numbers) == len(set(numbers))


# --- slide 5: one combined table, rows that say whose they are ---------------


def test_every_opportunitys_cases_reach_the_one_combined_table():
    """Slide 5 stays ONE combined set, because QofAI contracts the engagement
    rather than the opportunity. What changes is that a deck carrying two
    opportunities carries both their cases, as rows."""
    envelope = two_opportunity_envelope()
    scenarios = _section_yaml(_split_sections(envelope["packet"])[6])[
        "commercial"]["scenarios"]

    labelled = {row.get("opportunity") for row in scenarios}
    assert labelled == {"First Opportunity", "Second Opportunity"}


def test_the_rows_are_concatenated_and_never_summed():
    """Adding two opportunities' uplift together prints a figure neither
    document states, which is what `merge_packets` already refuses for a
    scenario table. Every row here is a case some document actually stated."""
    spec, client = two_opportunity_client()
    provider = LiveProposalProvider(client, generated_at=STAMP)

    def cases_for(opportunity_id):
        envelope = provider.poll(provider.submit(
            request(spec, floor=0.0, opportunity_id=opportunity_id)
        ))["envelope"]
        return _section_yaml(_split_sections(envelope["packet"])[6])[
            "commercial"].get("scenarios") or []

    alone = {case["name"] for case in cases_for("OPP-ONE")} | \
            {case["name"] for case in cases_for("OPP-TWO")}
    together = _section_yaml(
        _split_sections(two_opportunity_envelope()["packet"])[6]
    )["commercial"]["scenarios"]

    assert {row["name"] for row in together} == alone
    assert len(together) == sum(
        len(cases_for(name)) for name in ("OPP-ONE", "OPP-TWO")
    )


def test_nothing_here_assumes_three_cases_or_any_case_name():
    """Antonio, 2026-09-13: he does not know whether QofAI still uses
    conservative, base and optimistic in its pay plans, the compensation
    breakdown varies by client, and the agent should absorb different shapes
    rather than assume one. Named rows, like `commercial_rows` on the same
    slide, are that instruction implemented."""
    import inspect

    import packet_fill

    source = inspect.getsource(packet_fill.label_scenarios)
    for name in ("Conservative", "Base Case", "Optimistic"):
        assert name not in source
    rows = packet_fill.label_scenarios(
        [{"name": "A shape nobody has seen"}, {"name": "Nor this one"}],
        "An opportunity",
    )
    assert [row["opportunity"] for row in rows] == ["An opportunity"] * 2
    assert [row["name"] for row in rows] == ["A shape nobody has seen",
                                             "Nor this one"]


def test_a_one_opportunity_deck_carries_no_label_at_all():
    """A label that exists to tell two things apart is absent when there is one
    thing, the same way `client_short` degrades and the same way the published
    paper is the one document with no filename. The table renders exactly as it
    rendered before a deck could carry two."""
    spec, client = two_opportunity_client()
    provider = LiveProposalProvider(client, generated_at=STAMP)
    envelope = provider.poll(provider.submit(
        request(spec, floor=0.0, opportunity_id="OPP-ONE")
    ))["envelope"]

    scenarios = _section_yaml(_split_sections(envelope["packet"])[6])[
        "commercial"].get("scenarios") or []
    assert scenarios
    for row in scenarios:
        assert "opportunity" not in row


def test_the_label_emits_no_line_and_no_marker_on_a_one_opportunity_deck():
    """Through the map and the assembler, not only in the packet: a record that
    never carried a field says nothing about itself, which is the rule a
    phase-only timeline row already relies on."""
    import os

    from prompt_assembler import assemble_prompt
    from template_loader import load_template

    template_path = os.path.join(os.path.dirname(__file__), "..", "templates",
                                 "proposal-template.md")

    spec, client = two_opportunity_client()
    provider = LiveProposalProvider(client, generated_at=STAMP)
    envelope = provider.poll(provider.submit(
        request(spec, floor=0.0, opportunity_id="OPP-ONE")
    ))["envelope"]

    mapped = map_packet(envelope["packet"], envelope["request_echo"])
    # The scenario rows are the RETURN table's since 2026-09-23; the value chart
    # is drawn only from cases carrying its three figures, which these do not.
    assert mapped["return_rows"]
    for row in mapped["return_rows"]:
        assert "opportunity" not in row

    prompt = assemble_prompt(load_template(template_path), mapped)
    assert "[MISSING: opportunity]" not in prompt


# --- the panel speaks for the run, not for one of its sections --------------


def test_the_report_names_every_document_the_run_read():
    """THE LIVE DEFECT OF 2026-09-13. The panel listed two sources on a run that
    read three, because `build` is per section and is handed one opportunity's
    chain by construction: the shared uploads plus that opportunity's own paper
    and no other. No single section's report can speak for the run."""
    envelope = two_opportunity_envelope(uploads=[upload(SCENARIOS_ONLY)])
    assert envelope["status"] == "ok", envelope

    labels = [source["label"] for source in envelope["provenance"]["sources"]]
    assert len(labels) == 3, labels
    assert sum(1 for source in envelope["provenance"]["sources"]
               if source["uploaded"]) == 1


def test_every_source_is_counted_on_the_rows_it_actually_gave():
    """The trap in widening the source list alone: `_counts` walks the sources,
    so a second paper added without its rows would be reported at zero on the
    first section's data. That is the "0 fields" defect of 2026-09-08 rebuilt by
    accident."""
    envelope = two_opportunity_envelope(uploads=[upload(SCENARIOS_ONLY)])
    counts = envelope["provenance"]["counts"]

    assert {row["document"] for row in counts} == {
        source["label"] for source in envelope["provenance"]["sources"]
    }
    assert sum(row["total"] for row in counts) > 0


def test_a_shared_figure_is_reported_once_rather_than_once_per_slide():
    """The baseline is one fact about one company, carried by every section, so
    the panel states it once."""
    envelope = two_opportunity_envelope(uploads=[upload(SCENARIOS_ONLY)])
    filled = envelope["provenance"]["filled"]

    marks = [(row["field"], row["document"], row["value"]) for row in filled]
    assert len(marks) == len(set(marks))


def test_two_published_papers_are_told_apart_by_their_opportunity():
    """Every published paper is the same `Document`: it arrives from the
    platform rather than from a file and has never had a filename. Nothing keys
    on that and nothing collides, but a reviewer told which of two documents
    filled a field cannot be told "the paper" when there were two, which is the
    sentence `Document` already refuses to allow for attachments."""
    envelope = two_opportunity_envelope(uploads=[upload(SCENARIOS_ONLY)])
    labels = [source["label"] for source in envelope["provenance"]["sources"]
              if not source["uploaded"]]

    assert len(labels) == 2
    assert labels[0] != labels[1]
    for label in labels:
        assert "First Opportunity" in label or "Second Opportunity" in label


def test_a_one_opportunity_run_keeps_the_plain_paper_label():
    """A run with one paper has nothing to disambiguate, so the label does not
    move. `combine` only qualifies with more than one section in hand."""
    spec, client = two_opportunity_client()
    provider = LiveProposalProvider(client, generated_at=STAMP)
    envelope = provider.poll(provider.submit(
        request(spec, floor=0.0, opportunity_id="OPP-ONE"),
        uploads=[upload(SCENARIOS_ONLY)],
    ))["envelope"]

    labels = [source["label"] for source in envelope["provenance"]["sources"]
              if not source["uploaded"]]
    assert labels == ["the published opportunity paper"]


def test_one_opportunity_passes_its_report_through_unchanged():
    """Every single-opportunity run, by identity."""
    import provenance_report

    report = {"sources": [{"name": "a.md", "kind": "markdown",
                           "label": "a.md", "uploaded": True,
                           "characters": 10}],
              "base": "a.md", "opportunity": "", "counts": [], "filled": [],
              "quoted": [], "written": [], "conflicts": [], "set_aside": []}
    assert provenance_report.combine([report]) is report
    assert provenance_report.combine([]) == {}
    assert provenance_report.combine([{}, report]) is report
