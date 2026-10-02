"""Tests for the seam object and the studio switch (E9d).

Everything here runs the real `LiveProposalProvider` against a stub MCP client
whose `call_tool_json` returns the shapes the granted tools return, with a real
committed excerpt under `data-provider/fixtures/` standing in for the research
paper the live server would hand back. No live call is made: rule 7 in
`data-provider/CLAUDE.md` requires every call to the QofAI server, read-only
ones included, to be proposed and approved before it runs.

The two clients are placeholders chosen here, the same way E9c chose its own, so
nothing in `src/` is written around one company: no client name, no project
name, no PE firm and no figure crosses into production code. Two of them exist so
"nothing of one run appears in the other" is a comparison rather than a claim.
"""

import ast
import json
import pathlib
import re

import pytest

import completeness_score
import live_proposal_provider
from coverage_guard import COVERAGE_MAP
from data_source_adapter import _section_yaml, _split_sections, dispatch_and_gate
from deck_generator import generate_deck_prompt
from live_proposal_provider import (
    CONFIDENCE_BASIS,
    LiveProposalProvider,
    ProviderError,
    confidence_band,
    error_envelope,
)
from packet_assembly import ROSTER

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "data-provider" / "fixtures"

# Every code the contract defines (proposal-data-request-CONTRACT.md section 5).
CONTRACT_CODES = (
    "E_COMPANY_NOT_FOUND", "E_AMBIGUOUS_COMPANY", "E_PROJECT_REQUIRED",
    "E_AMBIGUOUS_PROJECT", "E_PROJECT_NOT_FOUND", "E_NO_KG", "E_KG_UNREACHABLE",
    "E_NO_CORPUS", "E_LOW_CONFIDENCE", "E_BAD_REQUEST", "E_TEMPLATE_UNKNOWN",
)

CLIENTS = {
    "one": {
        "shape": "scenario-rows-canonical",
        "company": {"id": "company-one", "name": "Test Subject One", "has_kg": True},
        "project": {"id": "project-one", "name": "First Test Project"},
        "pe_firm": "First Test Capital",
        "opportunity": {
            "id": "OPP-ONE",
            "title": "First Test Opportunity",
            "description": "A first placeholder description.",
            "ebitda_impact": {"min": 1.2, "max": 6.0, "unit": "pp"},
            "stage": "published",
            "published_at": "2026-01-01T00:00:00+00:00",
        },
    },
    "two": {
        "shape": "scenario-columns",
        "company": {"id": "company-two", "name": "Test Subject Two", "has_kg": True},
        "project": {"id": "project-two", "name": "Second Test Project"},
        "pe_firm": "Second Test Capital",
        "opportunity": {
            "id": "OPP-TWO",
            "title": "Second Test Opportunity",
            "description": "A second placeholder description.",
            "ebitda_impact": {"min": 0.8, "max": 3.4, "unit": "pp"},
            "stage": "published",
            "published_at": "2026-02-01T00:00:00+00:00",
        },
    },
}

STAMP = "2026-08-15T00:00:00Z"


def paper(shape):
    path = FIXTURES / f"paper-excerpt-{shape}.json"
    return json.loads(path.read_text())["opportunity"]["research_paper_natural"]


class StubClient:
    """One company, one project, and its published opportunities.

    `calls` records every tool name and argument set, so a test can assert what
    the seam asked for rather than only what it produced.
    """

    def __init__(self, client, opportunities=None):
        self._client = client
        self._opportunities = opportunities
        self.calls = []

    def _published(self):
        if self._opportunities is not None:
            return self._opportunities
        opportunity = dict(self._client["opportunity"])
        return [(opportunity, paper(self._client["shape"]))]

    def call_tool_json(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "list_companies":
            company = self._client["company"]
            found = arguments["search"].lower() in company["name"].lower()
            return {"companies": [company] if found else []}
        if name == "list_projects":
            return {"projects": [self._client["project"]]}
        if name == "list_opportunities":
            return {"opportunities": [o for o, _p in self._published()]}
        if name == "get_opportunity_details":
            for opportunity, text in self._published():
                if opportunity["id"] == arguments["opportunity_id"]:
                    record = dict(opportunity)
                    if text is not None:
                        record["research_paper_natural"] = text
                    return {"opportunity": record}
        raise AssertionError(f"unexpected tool call: {name}")


def run(key, client=None, **overrides):
    """One client through the whole pipeline, seam included."""
    spec = CLIENTS[key]
    kwargs = {
        "pe_firm": spec["pe_firm"],
        "proposal_date": "2026-08-15",
        "poll_interval": 0.0,
        "sleep": lambda _seconds: None,
    }
    kwargs.update(overrides)
    provider = LiveProposalProvider(client or StubClient(spec), generated_at=STAMP)
    return generate_deck_prompt(
        "proposal", spec["company"]["name"], spec["project"]["name"], provider,
        **kwargs,
    )


def envelope(key, client=None, **request_overrides):
    """The raw envelope the seam returned, before any gate or mapping."""
    spec = CLIENTS[key]
    request = {
        "company": spec["company"]["name"],
        "project": spec["project"]["name"],
        "pe_firm": spec["pe_firm"],
        "proposal_date": "2026-08-15",
        "options": {"min_data_completeness": 0.70},
    }
    request.update(request_overrides)
    provider = LiveProposalProvider(client or StubClient(spec), generated_at=STAMP)
    return provider.poll(provider.submit(request))["envelope"]


# --- the confidence band ----------------------------------------------------


@pytest.mark.parametrize("ratio,band", [
    (1.0, "high"), (0.90, "high"), (0.8999, "medium"), (0.70, "medium"),
    (0.6999, "low"), (0.0, "low"),
])
def test_the_band_is_a_pure_function_of_the_ratio_at_every_boundary(ratio, band):
    """Antonio's 2026-08-15 ruling, uncapped, tested at each threshold and just
    under it. Sanctioned by `completeness_score.py` lines 21 through 23, which
    say deriving a band from the ratio is the wiring window's call."""
    assert confidence_band(ratio) == band
    assert confidence_band(ratio) == confidence_band(ratio)


def test_a_live_packet_carries_the_band_its_own_ratio_earns():
    """The blocker this window existed to clear. Measured at `f7517a2`: a live
    packet scored 1.0, carried no band, failed `_passes_gates` on the absent band
    alone, and the review branch returned before `map_packet` ever ran."""
    packet = envelope("one")["packet"]
    frontmatter = packet.split("---")[1]
    assert "data_completeness: 1.0" in frontmatter
    assert 'confidence: "high"' in frontmatter


def test_the_band_and_the_role_coverage_disagree_and_section_eight_says_so():
    """The tradeoff Antonio took deliberately, made visible rather than softened.
    The band reads high off a full six-field roster while seven of eighteen
    render roles are still missing (eleven before E11 Stage 1 filled slide 4,
    eight before Stage 2c gave the horizon its own unit), so the artifact carries
    both numbers and the sentence that tells them apart. Neither the sentence nor
    `role_coverage` is in the frontmatter, because the frontmatter is what the
    gates read."""
    packet = envelope("one")["packet"]
    ledger = _section_yaml(_split_sections(packet)[8])["provenance"]
    assert ledger["role_coverage"] == 0.44
    assert ledger["confidence_basis"] == CONFIDENCE_BASIS
    assert "does not measure how much of the deck has a source" in CONFIDENCE_BASIS
    frontmatter = packet.split("---")[1]
    assert "role_coverage" not in frontmatter
    assert "confidence_basis" not in frontmatter


# --- the seam ---------------------------------------------------------------


def test_the_studio_reaches_map_packet_and_renders_a_live_deck():
    """The done-when. Through the seam, both gates cleared, a prompt out the far
    end rather than a review envelope."""
    result = run("one")
    assert result["status"] == "ok"
    assert result["confidence"] == "high"
    assert result["data_completeness"] == 1.0
    assert result["prompt"]


def test_the_deck_shows_six_missing_roles_four_governed_and_two_copy():
    """E9c's measurement, re-measured through the seam rather than inherited from
    its test. A role filled here that has no source, or dropped here that has
    one, moves one of the three numbers.

    21 / 11 / 10 before E11 Stage 1; 18 / 8 / 10 after it, when slide 4's three
    governed roles took the chart's own plan. 16 / 7 / 9 after Stage 2c: the same
    chart's last phase boundary fills `build_summary` on the governed side and
    `after_horizon` on the copy side. 13 / 6 / 7 after the slide-2 pass of
    2026-08-19, which moved three roles off this list for two different reasons.
    `today_metric_1` (governed) and `today_metric_2` (copy) are now filled from
    the baseline financials the packet already carried and the deck had never
    rendered, so they are absent no longer. `after_metric_2` (copy) is off the
    list because nothing is absent there to report: the AFTER block is filled
    from one opportunity figure on every deck in the corpus, so a required second
    metric marked every deck as missing a thing it was never going to have, and
    the role is now declared `optional`.

    12 / 6 / 6 after the timeline pass of 2026-09-03, which took the last copy
    role off the list. `plan_summary` had no home on this layer: it was composed
    by joining `build_summary.phases[].summary` on a space, so a run whose
    phases carried no summary rendered the marker and a run whose phases did
    carried a run-on of their caveat notes. Section 5 now carries its own
    `Subhead:` line like sections 4, 6 and 7, `packet_fill.PLAN_SUMMARY` is its
    deck standard, and `paper_writing`'s `copy.plan_summary` slot writes the
    engagement-specific line where a run has a writing pass. Nothing is absent
    there to report any more.
    """
    prompt = run("one")["prompt"]
    missing = set(re.findall(r"\[MISSING: ([a-z0-9_]+)\]", prompt))
    governed = set(COVERAGE_MAP.values())
    # 6 / 4 / 2 since the adaptive commercial slide (2026-09-23). The retired
    # fixed boxes (retention, downside, payment steps) are gone, and the value
    # chart is not drawn without its figures, so it no longer leaves its four
    # reviewer markers. What remains governed is the two slide 2 bullet lists
    # and TERMS with its footnote, which are the deal a reviewer supplies.
    assert len(missing) == 6
    assert sorted(missing & governed) == [
        "after_capability_bullets", "terms_footnote", "terms_rows",
        "today_pain_bullets",
    ]
    assert sorted(missing - governed) == ["opportunity_summary", "subtitle"]
    assert len(re.findall(r"\[MISSING: [a-z0-9_]+\]", prompt)) == 6
    # Named, so a change to WHICH role moved reds here rather than passing on a
    # count that happens to still add up.
    for role in ("today_metric_1", "today_metric_2", "after_metric_2",
                 "plan_summary"):
        assert role not in missing, role


def test_the_seam_submits_and_polls_the_way_the_transport_half_expects():
    """`submit` returns a handle and `poll` reports done with the envelope, which
    is the whole interface. Two submissions get their own handles, so a provider
    is not a one-shot object."""
    provider = LiveProposalProvider(StubClient(CLIENTS["one"]), generated_at=STAMP)
    request = {"company": "Test Subject One", "project": "First Test Project",
               "options": {"min_data_completeness": 0.70}}
    first, second = provider.submit(request), provider.submit(request)
    assert first != second
    for handle in (first, second):
        status = provider.poll(handle)
        assert status["done"] is True
        assert status["envelope"]["status"] == "ok"


def test_the_seam_asks_for_the_published_stage_and_stops_at_the_first_paper():
    """Selection is deterministic and cheap: published order, first paper wins,
    and the second opportunity's 40KB paper is never fetched."""
    spec = CLIENTS["one"]
    second = dict(spec["opportunity"], id="OPP-ONE-B",
                  published_at="2026-03-01T00:00:00+00:00")
    client = StubClient(spec, opportunities=[
        (spec["opportunity"], paper(spec["shape"])), (second, paper(spec["shape"])),
    ])
    envelope("one", client=client)
    assert ("list_opportunities",
            {"company_id": "company-one", "stage": "published"}) in client.calls
    fetched = [args["opportunity_id"] for name, args in client.calls
               if name == "get_opportunity_details"]
    assert fetched == ["OPP-ONE"]


def test_the_request_echo_is_the_request_the_transport_half_shaped():
    result = envelope("one")
    assert result["request_echo"]["company"] == "Test Subject One"
    assert result["request_echo"]["pe_firm"] == "First Test Capital"


# --- every contract error code comes back as an envelope --------------------


@pytest.mark.parametrize("code", CONTRACT_CODES)
def test_every_contract_error_code_returns_as_an_envelope(code, monkeypatch):
    """None of the eleven escapes the seam as an exception. The mapping is
    mechanical because `ProviderError` already carries the envelope's four
    fields, so this raises each code from the first leg and reads the result."""

    def _raise(*_args, **_kwargs):
        raise ProviderError(code, f"{code} happened", "Do the remediation.",
                            {"company_id": "company-one"})

    monkeypatch.setattr(live_proposal_provider, "resolve_company", _raise)
    result = envelope("one")
    assert result == {"status": "error", "error": {
        "code": code, "message": f"{code} happened",
        "remediation": "Do the remediation.",
        "details": {"company_id": "company-one"},
    }}


@pytest.mark.parametrize("code", CONTRACT_CODES)
def test_every_contract_error_code_surfaces_through_the_studio_with_no_deck(
    code, monkeypatch
):
    """The golden rule at the far end: an error outcome carries no prompt, for
    every code, all the way through `generate_deck_prompt`."""

    def _raise(*_args, **_kwargs):
        raise ProviderError(code, f"{code} happened", "Do the remediation.", {})

    monkeypatch.setattr(live_proposal_provider, "resolve_company", _raise)
    result = run("one")
    assert result == {"status": "error", "code": code, "message": f"{code} happened",
                      "remediation": "Do the remediation.", "details": {}}


# --- the transport's own failures, which Agent OS never answered to code ----


@pytest.mark.parametrize("exc_name", [
    "McpError", "McpMissingKeyError", "McpAuthError", "McpProtocolError",
    "McpEdgeChallengeError",
])
def test_a_transport_failure_comes_back_as_e_source_unreachable(exc_name, monkeypatch):
    """The whole hierarchy, not just the one that was reproduced live.

    These descend from `Exception` rather than from `ProviderError`, checked
    below, so before 2026-08-18 every one of them left the seam as an exception
    and each caller of `submit` had to catch it themselves.
    """
    import qofai_mcp_client

    exc_class = getattr(qofai_mcp_client, exc_name)

    def _raise(*_args, **_kwargs):
        raise exc_class("the platform did not answer")

    monkeypatch.setattr(live_proposal_provider, "resolve_company", _raise)
    result = envelope("one")
    assert result["status"] == "error"
    assert result["error"]["code"] == "E_SOURCE_UNREACHABLE"
    assert result["error"]["message"].endswith(
        f"{exc_name}: the platform did not answer"
    )
    assert result["error"]["remediation"]


def test_e_source_unreachable_carries_no_company_id(monkeypatch):
    """Contract §5: the absent `company_id` is what distinguishes this from
    `E_KG_UNREACHABLE`. A transport failure can happen before resolution, so
    there may be no company to name, and inventing one would misreport how far
    the request got."""
    from qofai_mcp_client import McpProtocolError

    def _raise(*_args, **_kwargs):
        raise McpProtocolError("no response")

    monkeypatch.setattr(live_proposal_provider, "resolve_company", _raise)
    details = envelope("one")["error"]["details"]
    assert details == {"endpoint_kind": "agent_os_mcp",
                       "error_type": "McpProtocolError"}
    assert "company_id" not in details


def test_the_transport_hierarchy_is_disjoint_from_the_contract_one():
    """The reason the leak existed at all, pinned so nobody 'simplifies' the
    second `except` clause away on the assumption one catch covers both."""
    from qofai_mcp_client import (McpAuthError, McpError, McpMissingKeyError,
                                  McpProtocolError, McpToolError)

    for cls in (McpError, McpMissingKeyError, McpAuthError, McpProtocolError,
                McpToolError):
        assert not issubclass(cls, ProviderError), cls
    assert not issubclass(ProviderError, McpError)


def test_a_transport_failure_surfaces_through_the_studio_with_no_deck(monkeypatch):
    """The golden rule at the far end, same as every contract code: an
    unreachable source produces an error outcome and no prompt."""
    from qofai_mcp_client import McpProtocolError

    def _raise(*_args, **_kwargs):
        raise McpProtocolError("no response")

    monkeypatch.setattr(live_proposal_provider, "resolve_company", _raise)
    result = run("one")
    assert result["status"] == "error"
    assert result["code"] == "E_SOURCE_UNREACHABLE"
    assert "prompt" not in result
    assert "packet" not in result


def test_an_unresolvable_company_comes_back_as_an_envelope_rather_than_a_raise():
    """Not a monkeypatched code but the real resolution leg, so the wiring
    between `submit` and `resolve_company` is exercised too."""
    provider = LiveProposalProvider(StubClient(CLIENTS["one"]))
    request = {"company": "No Such Company", "project": "First Test Project",
               "options": {"min_data_completeness": 0.70}}
    result = provider.poll(provider.submit(request))["envelope"]
    assert result["status"] == "error"
    assert result["error"]["code"] == "E_COMPANY_NOT_FOUND"


def test_a_company_with_no_knowledge_graph_stops_before_any_opportunity_call():
    """The KG gate fails loudly and early rather than producing a thin packet."""
    spec = dict(CLIENTS["one"], company=dict(CLIENTS["one"]["company"], has_kg=False))
    client = StubClient(spec)
    provider = LiveProposalProvider(client)
    request = {"company": "Test Subject One", "project": "First Test Project",
               "options": {"min_data_completeness": 0.70}}
    result = provider.poll(provider.submit(request))["envelope"]
    assert result["error"]["code"] == "E_NO_KG"
    assert not [name for name, _args in client.calls if name == "list_opportunities"]


def test_a_company_whose_opportunities_carry_no_paper_returns_no_corpus():
    """A published opportunity without a research paper is nothing to derive
    from, which is exactly what `E_NO_CORPUS` names."""
    spec = CLIENTS["one"]
    client = StubClient(spec, opportunities=[(spec["opportunity"], None)])
    result = envelope("one", client=client)
    assert result["error"]["code"] == "E_NO_CORPUS"
    assert result["error"]["details"] == {"company_id": "company-one"}


def test_a_packet_under_the_completeness_floor_returns_low_confidence():
    """The contract's own rule: below the floor the provider returns the code
    instead of a partial packet. The sparse excerpt states no scenario table and
    no timeline, so its ratio cannot clear 0.70."""
    spec = dict(CLIENTS["two"], shape="sparse-no-scenario-table")
    client = StubClient(spec, opportunities=[
        (spec["opportunity"], paper("sparse-no-scenario-table")),
    ])
    provider = LiveProposalProvider(client)
    request = {"company": "Test Subject Two", "project": "Second Test Project",
               "options": {"min_data_completeness": 0.70}}
    result = provider.poll(provider.submit(request))["envelope"]
    assert result["error"]["code"] == completeness_score.E_LOW_CONFIDENCE
    assert result["error"]["details"]["data_completeness"] < 0.70
    assert result["error"]["details"]["confidence"] == "low"
    assert len(result["error"]["details"]["missing_fields"]) <= len(ROSTER)


def test_a_provider_error_becomes_an_envelope_without_the_seam_running():
    """`error_envelope` is the whole mapping, so it is checked on its own too."""
    error = ProviderError("E_BAD_REQUEST", "no company", "Supply one.", {"f": 1})
    assert error_envelope(error) == {"status": "error", "error": {
        "code": "E_BAD_REQUEST", "message": "no company",
        "remediation": "Supply one.", "details": {"f": 1},
    }}


# --- the reviewer's opportunity pick (E9e) -----------------------------------


def test_opportunity_choices_lists_the_resolved_companys_published_opportunities():
    """What the studio offers a reviewer to pick from: `list_opportunities`,
    already on this path, filtered to published, through the same resolution
    and KG gate a run itself performs. No fifth tool."""
    spec = CLIENTS["one"]
    second = dict(spec["opportunity"], id="OPP-ONE-B", title="Second Test Opportunity B",
                  published_at="2026-03-01T00:00:00+00:00")
    client = StubClient(spec, opportunities=[
        (spec["opportunity"], paper(spec["shape"])), (second, paper(spec["shape"])),
    ])
    choices = live_proposal_provider.opportunity_choices(client, spec["company"]["name"])
    assert choices == [
        {"id": "OPP-ONE", "label": "First Test Opportunity"},
        {"id": "OPP-ONE-B", "label": "Second Test Opportunity B"},
    ]


# --- ranking the pick by the chosen project ---------------------------------
# The platform records no link between a project and an opportunity (checked on
# `list_projects`, `list_opportunities`, `get_opportunity_details`, and the KG,
# where the Engagement node has no edge to any Opportunity). So the pick cannot
# be derived, only ordered: a company with two dozen published opportunities
# hands the reviewer a list in `published_at` order where the one belonging to
# the project they typed could sit anywhere. Ranking moves the likely ones to the
# top and marks the leader. It never chooses, and never removes an option.


def _choice(cid, label):
    return {"id": cid, "label": label}


def test_ranking_puts_the_titles_that_share_words_with_the_project_first():
    ranked = live_proposal_provider.rank_choices("Autonomous Network Platform", [
        _choice("a", "Predictive Maintenance For Fibre Routes"),
        _choice("b", "Autonomous Network Operations Rollout"),
    ])
    assert [c["id"] for c in ranked] == ["b", "a"]


def test_a_word_in_every_title_carries_no_weight():
    """The reason the scoring is inverse-document-frequency weighted rather than a
    plain overlap count. At a real company "ai", "deploy" and "platform" appear in
    nearly every title, so a plain count ranks on noise: here the project shares
    "deploy" and "ai" with BOTH candidates and only "billing" with one, and that
    one word has to decide the order."""
    ranked = live_proposal_provider.rank_choices("Deploy AI Billing Agents", [
        _choice("a", "Deploy AI Across Network Maintenance"),
        _choice("b", "Deploy AI Across Billing Operations"),
    ])
    assert [c["id"] for c in ranked] == ["b", "a"]


def test_ranking_never_drops_dedupes_or_adds_a_choice():
    """The invariant that keeps ranking from becoming choosing. Every published
    opportunity stays selectable however badly it scores, including the ones
    sharing no word with the project at all."""
    given = [_choice(str(i), f"Opportunity Number {i} Widgets") for i in range(25)]
    given.append(_choice("hit", "A Matching Engagement Title"))
    ranked = live_proposal_provider.rank_choices("A Matching Engagement", given)
    assert len(ranked) == len(given)
    assert sorted(c["id"] for c in ranked) == sorted(c["id"] for c in given)
    assert ranked[0]["id"] == "hit"


def test_a_blank_project_leaves_the_platforms_own_order_alone():
    """The behaviour that predates this change, held. With nothing to rank
    against, the list is whatever `list_opportunities` gave, in `published_at`
    order, and no option is marked."""
    given = [_choice("a", "First"), _choice("b", "Second"), _choice("c", "Third")]
    for blank in ("", "   ", None):
        ranked = live_proposal_provider.rank_choices(blank, given)
        assert ranked == given
        assert not any("best" in c for c in ranked)


def test_equal_scores_keep_the_order_the_platform_gave():
    """Ties are not reordered arbitrarily: the sort is stable, so two candidates
    the project matches equally well stay in `published_at` order between
    themselves."""
    given = [_choice("a", "Shared Word Alpha"), _choice("b", "Shared Word Beta")]
    ranked = live_proposal_provider.rank_choices("Shared Word", given)
    assert [c["id"] for c in ranked] == ["a", "b"]


def test_the_leader_is_marked_only_when_it_actually_leads():
    """The mark is a claim, so it is made only when the evidence supports it: the
    top candidate must share something with the project AND beat the runner-up.
    A project matching nothing, and a project matching two candidates equally,
    both get an order and no mark."""
    marked = live_proposal_provider.rank_choices("Billing Automation", [
        _choice("a", "Network Maintenance Rollout"),
        _choice("b", "Billing Automation Rollout"),
    ])
    assert marked[0]["id"] == "b" and marked[0]["best"] is True
    assert "best" not in marked[1]

    nothing_shared = live_proposal_provider.rank_choices("Zebra Xylophone", [
        _choice("a", "Network Maintenance Rollout"),
        _choice("b", "Billing Automation Rollout"),
    ])
    assert not any("best" in c for c in nothing_shared)

    tied = live_proposal_provider.rank_choices("Shared Word", [
        _choice("a", "Shared Word Alpha"), _choice("b", "Shared Word Beta"),
    ])
    assert not any("best" in c for c in tied)


def test_ranking_does_not_mutate_what_it_was_handed():
    given = [_choice("a", "Billing Automation Rollout")]
    live_proposal_provider.rank_choices("Billing Automation", given)
    assert given == [{"id": "a", "label": "Billing Automation Rollout"}]


def test_opportunity_choices_ranks_by_the_project_when_one_is_given():
    """End to end through the same resolution and KG gate: the project reaches
    the listing, and the choice whose title matches it comes back first and
    marked."""
    spec = CLIENTS["one"]
    second = dict(spec["opportunity"], id="OPP-ONE-B",
                  title="Second Test Opportunity B",
                  published_at="2026-03-01T00:00:00+00:00")
    client = StubClient(spec, opportunities=[
        (spec["opportunity"], paper(spec["shape"])), (second, paper(spec["shape"])),
    ])
    choices = live_proposal_provider.opportunity_choices(
        client, spec["company"]["name"], project="Second B Programme")
    assert choices == [
        {"id": "OPP-ONE-B", "label": "Second Test Opportunity B", "best": True},
        {"id": "OPP-ONE", "label": "First Test Opportunity"},
    ]


def test_opportunity_choices_surfaces_the_same_resolution_error_a_run_would():
    client = StubClient(CLIENTS["one"])
    with pytest.raises(ProviderError) as excinfo:
        live_proposal_provider.opportunity_choices(client, "No Such Company")
    assert excinfo.value.code == "E_COMPANY_NOT_FOUND"


def test_the_reviewers_pick_reaches_the_seam_instead_of_the_first_paper():
    """The policy E9e replaces: `_select` no longer always takes the first
    published opportunity carrying a paper once the request names one."""
    spec = CLIENTS["one"]
    second = dict(spec["opportunity"], id="OPP-ONE-B",
                  published_at="2026-03-01T00:00:00+00:00")
    client = StubClient(spec, opportunities=[
        (spec["opportunity"], paper(spec["shape"])), (second, paper(spec["shape"])),
    ])
    result = envelope("one", client=client, opportunity_id="OPP-ONE-B")
    assert result["status"] == "ok"
    fetched = [args["opportunity_id"] for name, args in client.calls
               if name == "get_opportunity_details"]
    assert fetched == ["OPP-ONE-B"]


def test_a_picked_opportunity_short_on_baselines_gets_low_confidence_not_no_corpus():
    """An opportunity that refuses still refuses (E9e): picking the thinner,
    later-published opportunity overrides the default that would otherwise
    render clean, and the gate does not move just because a human chose it."""
    spec = CLIENTS["two"]
    sparse = dict(spec["opportunity"], id="OPP-TWO-SPARSE",
                  published_at="2026-03-01T00:00:00+00:00")
    client = StubClient(spec, opportunities=[
        (spec["opportunity"], paper(spec["shape"])),
        (sparse, paper("sparse-no-scenario-table")),
    ])
    default = envelope("two", client=client)
    assert default["status"] == "ok"

    picked = envelope("two", client=client, opportunity_id="OPP-TWO-SPARSE")
    assert picked["error"]["code"] == completeness_score.E_LOW_CONFIDENCE
    assert picked["error"]["details"]["data_completeness"] < 0.70


def test_the_reviewers_pick_flows_through_the_full_transport_half():
    """`opportunity_id` reaches the seam via `shape_request`, not only via a
    hand-built request dict, proving the plumbing the transport half owns."""
    spec = CLIENTS["one"]
    second = dict(spec["opportunity"], id="OPP-ONE-B",
                  published_at="2026-03-01T00:00:00+00:00")
    client = StubClient(spec, opportunities=[
        (spec["opportunity"], paper(spec["shape"])), (second, paper(spec["shape"])),
    ])
    result = run("one", client=client, opportunity_id="OPP-ONE-B")
    assert result["status"] == "ok"
    fetched = [args["opportunity_id"] for name, args in client.calls
               if name == "get_opportunity_details"]
    assert fetched == ["OPP-ONE-B"]


# --- the two-client anti-hardcoding check -----------------------------------


def test_no_value_crosses_between_two_clients_through_the_seam():
    """Two clients, two excerpts, two live runs, and nothing of one appears in
    the other's deck. The deck standards are supposed to be identical, so they
    are excluded by naming the distinctive values rather than by diffing."""
    first, second = run("one")["prompt"], run("two")["prompt"]
    for spec, other in ((CLIENTS["one"], second), (CLIENTS["two"], first)):
        distinctive = (
            spec["company"]["name"], spec["project"]["name"], spec["pe_firm"],
            spec["opportunity"]["title"], spec["opportunity"]["id"],
        )
        for value in distinctive:
            assert value not in other
    assert first != second


def test_the_provider_names_no_company_no_endpoint_and_no_environment_variable():
    """The client is passed in rather than built here, which is what keeps this
    module free of an endpoint and a key name, and keeps `httpx` off the hosted
    module-level import path.

    Narrowed 2026-08-18 (E10 checkpoint three-b) from "at any level" to "runs on
    import", which is what the stated rationale was always about. `submit` now
    names `McpError` to map it to `E_SOURCE_UNREACHABLE`, and it does so inside
    the method: `LiveProposalProvider.submit` is the live path by definition, so
    the fixture path still never pulls `httpx`.

    The predicate is a function-body check rather than an indentation check,
    because indentation is a proxy for deferral and not the thing itself. A
    module-level `try: import httpx / except ImportError:` indents to
    `col_offset == 4` and still executes on every import of this module,
    including every fixture run, so a `col_offset > 0` test would pass it. Only
    being inside a function body actually defers execution to call time.
    """
    source = pathlib.Path(live_proposal_provider.__file__).read_text()
    tree = ast.parse(source)
    deferred = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            deferred.update(
                id(child) for child in ast.walk(node)
                if isinstance(child, (ast.Import, ast.ImportFrom))
            )
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            names = {node.module}
        else:
            continue
        if names & {"httpx", "qofai_mcp_client"}:
            assert id(node) in deferred, (
                f"{names} is imported where it runs on import (line "
                f"{node.lineno}), which puts httpx on the hosted import path "
                "for every fixture run too"
            )
    for literal in ("QOFAI_MCP", "mcp.example.test"):
        assert literal not in source
    for spec in CLIENTS.values():
        assert spec["company"]["name"] not in source
        assert spec["pe_firm"] not in source


# --- the fixture path is untouched ------------------------------------------


def test_the_fixture_path_still_returns_the_frozen_packets_byte_for_byte():
    """`FixtureProvider` is not touched by this window, so both frozen packets
    still arrive at the gate exactly as they sit on disk."""
    from data_source_adapter import FixtureProvider

    packets = sorted(
        (pathlib.Path(__file__).resolve().parent.parent / "templates" / "packets")
        .glob("*.md")
    )
    assert packets
    for path in packets:
        provider = FixtureProvider.from_packet_file(str(path))
        result = dispatch_and_gate(
            "Any Client", "Any Project", provider,
            poll_interval=0.0, sleep=lambda _s: None,
        )
        assert result["status"] == "ok"
        assert result["packet"] == path.read_text(encoding="utf-8")


# --- the two LLM legs, injected through the provider itself -----------------

def test_the_provider_carries_both_llm_legs_and_neither_is_on_by_default():
    """The seam, at the object that actually ships (E11 Stage 2c).

    Two separate legs, and the separation is the point: `extractor` quotes facts
    under span discipline, `writer` writes the deck's framing under the generated
    rules. Both are off unless a caller hands them over, which is what keeps the
    whole suite off the network, and with either off the packet omits that leg's
    section 8 block entirely rather than claiming a pass ran and found nothing.
    """
    import json as _json

    import paper_writing
    from data_source_adapter import _section_yaml, _split_sections

    quoted = "Structured storage for project, equipment, and schedule data"

    def writer(paper, description, requested):
        assert set(requested) == set(paper_writing.PATHS)
        assert quoted in paper
        return paper_writing.read_response(
            paper, description, requested,
            type("M", (), {"content": [type("B", (), {
                "type": "text",
                "text": _json.dumps({"sentences": [{
                    "path": "copy.platform_summary",
                    "text": "Structured storage for project, equipment and "
                            "schedule data.",
                    "sections": ["Implementation Approach > Technical Requirements"],
                    "evidence": [quoted],
                }]}),
            })()]})(),
        )

    spec = CLIENTS["one"]
    request = {"company": spec["company"]["name"],
               "project": spec["project"]["name"],
               "pe_firm": spec["pe_firm"], "proposal_date": "2026-08-15",
               "options": {"min_data_completeness": 0.70}}

    plain = LiveProposalProvider(StubClient(spec), generated_at=STAMP)
    plain_packet = plain.poll(plain.submit(request))["envelope"]["packet"]
    assert "generated:" not in _split_sections(plain_packet)[8]
    assert "second_pass:" not in _split_sections(plain_packet)[8]

    provider = LiveProposalProvider(StubClient(spec), generated_at=STAMP,
                                    writer=writer)
    packet = provider.poll(provider.submit(request))["envelope"]["packet"]
    ledger = _section_yaml(_split_sections(packet)[8])["provenance"]
    written = ledger["generated"]["written"]
    assert [entry["field"] for entry in written] == ["copy.platform_summary"]
    assert written[0]["kind"] == "GENERATED, not quoted"
    assert "Structured storage" in _split_sections(packet)[4]
    # And the quoting leg is still absent on this run, so the two are wired
    # independently rather than as one switch.
    assert "second_pass:" not in _split_sections(packet)[8]


def test_the_envelope_carries_the_writing_ledger_beside_the_packet():
    """Item 24. The provider is the ORIGIN of this record and the only place
    that holds it as a structure: a moment later it is text inside section 8 of
    a packet that never leaves `generate_and_save_deck` and is never written to
    disk. Carried beside the packet like `provenance` and `attachments`, so a
    run can be diagnosed after it ends.

    Written as a provider test on purpose. Cutting the carry here leaves every
    downstream test green, because they all read a ledger the provider is no
    longer putting in, and that is the failure this whole item exists to stop
    happening a third time.
    """
    import json as _json
    import paper_writing

    quoted = "Structured storage for project, equipment, and schedule data"

    def writer(paper, description, requested):
        return paper_writing.read_response(
            paper, description, requested,
            type("M", (), {"content": [type("B", (), {
                "type": "text",
                "text": _json.dumps({"sentences": [{
                    "path": "copy.platform_summary",
                    "text": "Structured storage for project, equipment and "
                            "schedule data.",
                    "sections": ["Implementation Approach > Technical Requirements"],
                    "evidence": [quoted],
                }]}),
            })()]})(),
        )

    spec = CLIENTS["one"]
    request = {"company": spec["company"]["name"],
               "project": spec["project"]["name"],
               "pe_firm": spec["pe_firm"], "proposal_date": "2026-08-15",
               "options": {"min_data_completeness": 0.70}}
    provider = LiveProposalProvider(StubClient(spec), generated_at=STAMP,
                                    writer=writer)
    envelope_out = provider.poll(provider.submit(request))["envelope"]

    ledger = envelope_out["writing_ledger"]
    assert ledger is not None, "the provider must carry it out"
    assert [row["field"] for row in ledger["written"]] == ["copy.platform_summary"]
    # The slots the pass was asked for and did not write, each with its reason,
    # which is the half that answers "why does this headline read generic".
    refused = {row["field"]: row["reason"] for row in ledger["not_written"]}
    assert "copy.plan_summary" in refused
    assert refused["copy.plan_summary"].strip()

    # And a run with no writing pass says nothing rather than claiming an empty
    # one, so no run that worked before this existed grows a card.
    plain = LiveProposalProvider(StubClient(spec), generated_at=STAMP)
    assert plain.poll(plain.submit(request))["envelope"]["writing_ledger"] is None
