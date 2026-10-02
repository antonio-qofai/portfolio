"""Tests for company/project resolution and the KG gate (E1, carrying E2).

Uses a hand-authored stub client rather than a real `QofaiMcpClient`, so the
suite stays fast and offline. The stub's shapes mirror what `list_companies`
and `list_projects` actually return, confirmed live against the real
reference companies (Northwind, Fabrikam Marine) during this build; the
stub itself uses fictitious names, since production code here must never be
built around one company's shape.
"""

import builtins

import pytest

from live_proposal_provider import (
    ProviderError,
    candidate_of,
    opportunity_choices,
    check_kg_gate,
    fetch_opportunity_paper,
    fetch_published_papers,
    list_published_opportunities,
    resolve_company,
    resolve_project,
)


class _StubClient:
    def __init__(self, companies=None, projects=None, opportunities=None, details=None):
        self._companies = companies or []
        self._projects = projects or []
        self._opportunities = opportunities or []
        self._details = details or {}

    def call_tool_json(self, name, arguments):
        if name == "list_companies":
            search = arguments["search"].lower()
            return {
                "ok": True,
                "companies": [
                    c for c in self._companies if search in c["name"].lower()
                ],
            }
        if name == "list_projects":
            assert arguments["company_id"] == "acme-co"
            return {"ok": True, "projects": self._projects}
        if name == "list_opportunities":
            stage = arguments.get("stage")
            matches = [
                o for o in self._opportunities if stage is None or o["stage"] == stage
            ]
            return {"ok": True, "opportunities": matches}
        if name == "get_opportunity_details":
            return self._details[arguments["opportunity_id"]]
        raise AssertionError(f"unexpected tool: {name}")


_ACME = {"id": "acme-co", "name": "Acme Robotics", "has_kg": True}
_ACME_LOGISTICS = {"id": "acme-log", "name": "Acme Logistics", "has_kg": False}
_ZEPHYR = {"id": "zephyr-co", "name": "Zephyr Analytics", "has_kg": False}

_ROLLOUT = {"id": "proj-rollout", "name": "Warehouse Rollout"}
_PILOT = {"id": "proj-pilot", "name": "Warehouse Pilot Phase"}

_OPP_LATE = {
    "id": "opp-b",
    "company_id": "acme-co",
    "title": "Late Opportunity",
    "stage": "published",
    "published_at": "2026-05-01T00:00:00+00:00",
}
_OPP_EARLY = {
    "id": "opp-a",
    "company_id": "acme-co",
    "title": "Early Opportunity",
    "stage": "published",
    "published_at": "2026-01-01T00:00:00+00:00",
}
_OPP_DISCOVERED = {
    "id": "opp-c",
    "company_id": "acme-co",
    "title": "Discovered Opportunity",
    "stage": "discovered",
    "published_at": None,
}

_DETAILS = {
    "opp-a": {
        "ok": True,
        "opportunity": {**_OPP_EARLY, "research_paper_natural": "paper text a"},
    },
    "opp-b": {
        "ok": True,
        "opportunity": {**_OPP_LATE, "research_paper_natural": "paper text b"},
    },
    "opp-c": {
        "ok": True,
        "opportunity": dict(_OPP_DISCOVERED),
        "note": "This opportunity is not published; its research paper is withheld.",
    },
}


def test_resolve_company_single_match():
    client = _StubClient(companies=[_ACME, _ZEPHYR])
    company = resolve_company(client, "Acme Robotics")
    assert company["id"] == "acme-co"


def test_resolve_company_ambiguous_reports_all_candidates():
    client = _StubClient(companies=[_ACME, _ACME_LOGISTICS])
    with pytest.raises(ProviderError) as exc:
        resolve_company(client, "Acme")
    assert exc.value.code == "E_AMBIGUOUS_COMPANY"
    names = {c["name"] for c in exc.value.details["candidates"]}
    assert names == {"Acme Robotics", "Acme Logistics"}


# --------------------------------- item 17, the candidate a reviewer picked

def test_a_picked_candidate_id_resolves_to_that_candidate():
    """The ambiguity stops being a wall. Same name, same search, one of its
    results chosen by id."""
    client = _StubClient(companies=[_ACME, _ACME_LOGISTICS])
    company = resolve_company(client, "Acme", company_id="acme-log")
    assert company["id"] == "acme-log"
    assert company["name"] == "Acme Logistics"


def test_the_pick_re_runs_the_REVIEWERS_SEARCH_and_not_the_candidates_name():
    """The seam the grant forces, and the one thing that would be wrong to
    "simplify". There is no lookup-by-id tool, so the id has to be found in a
    search; searching the candidate's own exact name instead is not equivalent,
    because an exact name that four registry rows share still returns four."""
    seen = []

    class _Watched(_StubClient):
        def call_tool_json(self, name, arguments):
            if name == "list_companies":
                seen.append(arguments["search"])
            return super().call_tool_json(name, arguments)

    client = _Watched(companies=[_ACME, _ACME_LOGISTICS])
    resolve_company(client, "Acme", company_id="acme-log")
    assert seen == ["Acme"], seen


def test_an_id_that_matches_nothing_in_the_search_is_an_error():
    """Never a quiet fall back to the name match. A stale id and a typed one are
    the same shape from here, and resolving either to whatever the name happens
    to match builds a deck for a company nobody picked."""
    client = _StubClient(companies=[_ACME, _ACME_LOGISTICS])
    with pytest.raises(ProviderError) as exc:
        resolve_company(client, "Acme", company_id="acme-gone")
    assert exc.value.code == "E_COMPANY_NOT_FOUND"
    assert exc.value.details["company_id"] == "acme-gone"
    # The list comes back with it, so the picker can be offered again rather
    # than leaving the reviewer somewhere with nothing to press.
    assert {c["company_id"] for c in exc.value.details["candidates"]} == {
        "acme-co", "acme-log"}


def test_a_stale_id_does_not_fall_back_even_when_the_name_matches_one_company():
    """The unambiguous case is where a silent fallback would be invisible: the
    name resolves fine on its own, so nothing would look wrong."""
    client = _StubClient(companies=[_ACME])
    with pytest.raises(ProviderError) as exc:
        resolve_company(client, "Acme Robotics", company_id="acme-log")
    assert exc.value.code == "E_COMPANY_NOT_FOUND"


def test_an_unambiguous_name_with_its_own_id_still_resolves():
    """The id the picker posts is the one it was given, so the ordinary path
    must not start refusing runs when it happens to carry one."""
    client = _StubClient(companies=[_ACME])
    assert resolve_company(client, "Acme Robotics", company_id="acme-co")["id"] == "acme-co"


def test_no_id_leaves_every_existing_outcome_exactly_where_it_was():
    """One match resolves, more than one is still ambiguous."""
    client = _StubClient(companies=[_ACME, _ACME_LOGISTICS])
    assert resolve_company(client, "Acme Robotics")["id"] == "acme-co"
    with pytest.raises(ProviderError) as exc:
        resolve_company(client, "Acme")
    assert exc.value.code == "E_AMBIGUOUS_COMPANY"


def test_a_candidate_row_carries_a_firm_when_the_registry_gives_one():
    """The contract's example (section 6.2) shows `pe_firm` on every candidate
    and the registry records this module has seen carry none. Carried when it is
    there, absent when it is not, rather than written in as an empty string: it
    is the one field that tells two same-named companies apart, and an empty one
    would read as "no firm" rather than "not answered"."""
    with_firm = {"id": "acme-x", "name": "Acme Holdings", "has_kg": True,
                 "pe_firm": "Any Capital"}
    client = _StubClient(companies=[_ACME, with_firm])
    with pytest.raises(ProviderError) as exc:
        resolve_company(client, "Acme")
    rows = {c["company_id"]: c for c in exc.value.details["candidates"]}
    assert rows["acme-x"]["pe_firm"] == "Any Capital"
    assert "pe_firm" not in rows["acme-co"]


def test_the_opportunity_list_takes_the_pick_too():
    """Where a reviewer actually meets the ambiguity is the picker on company
    blur, not the run."""
    client = _StubClient(companies=[_ACME, _ACME_LOGISTICS],
                         opportunities=[_OPP_LATE, _OPP_EARLY])
    choices = opportunity_choices(client, "Acme", company_id="acme-co")
    # Both of the picked company's published opportunities, in the order
    # `list_published_opportunities` already puts them in. What is under test is
    # that a name matching two companies produced a list at all.
    assert [c["id"] for c in choices] == ["opp-a", "opp-b"]

    # And the gate still runs against the company that was PICKED: Acme
    # Logistics has no KG, so picking it refuses exactly as naming it would.
    with pytest.raises(ProviderError) as exc:
        opportunity_choices(client, "Acme", company_id="acme-log")
    assert exc.value.code == "E_NO_KG"


def test_resolve_company_no_match():
    client = _StubClient(companies=[_ACME])
    with pytest.raises(ProviderError) as exc:
        resolve_company(client, "Nonexistent Corp")
    assert exc.value.code == "E_COMPANY_NOT_FOUND"
    assert exc.value.details["query"] == "Nonexistent Corp"


def test_resolve_company_requires_a_name():
    client = _StubClient()
    with pytest.raises(ProviderError) as exc:
        resolve_company(client, "")
    assert exc.value.code == "E_BAD_REQUEST"


def test_resolve_project_single_match():
    client = _StubClient(projects=[_ROLLOUT])
    project = resolve_project(client, "acme-co", "Rollout")
    assert project["id"] == "proj-rollout"


def test_resolve_project_ambiguous_reports_all_candidates():
    client = _StubClient(projects=[_ROLLOUT, _PILOT])
    with pytest.raises(ProviderError) as exc:
        resolve_project(client, "acme-co", "Warehouse")
    assert exc.value.code == "E_AMBIGUOUS_PROJECT"
    assert exc.value.details["company_id"] == "acme-co"
    names = {c["name"] for c in exc.value.details["candidates"]}
    assert names == {"Warehouse Rollout", "Warehouse Pilot Phase"}


def test_resolve_project_no_match():
    client = _StubClient(projects=[_ROLLOUT])
    with pytest.raises(ProviderError) as exc:
        resolve_project(client, "acme-co", "Nonexistent Project")
    assert exc.value.code == "E_PROJECT_NOT_FOUND"


def test_resolve_project_never_falls_back_to_company_level():
    client = _StubClient(projects=[_ROLLOUT])
    with pytest.raises(ProviderError) as exc:
        resolve_project(client, "acme-co", None)
    assert exc.value.code == "E_PROJECT_REQUIRED"
    assert exc.value.details == {"company_id": "acme-co"}


def test_kg_gate_passes_a_company_with_a_knowledge_graph():
    assert check_kg_gate(_ACME) is _ACME


def test_kg_gate_blocks_a_company_with_no_knowledge_graph():
    with pytest.raises(ProviderError) as exc:
        check_kg_gate(_ZEPHYR)
    assert exc.value.code == "E_NO_KG"
    assert exc.value.details == {"company_id": "zephyr-co"}


def test_resolution_and_gate_errors_write_zero_files(monkeypatch):
    """The escalation guarantee (`scripts/eval_negative_path.py`) is that an
    error outcome writes nothing to disk. Nothing in this module opens a file
    on any path, success or failure; prove it by making `open` fail loudly if
    anything here ever calls it."""

    def _forbidden_open(*args, **kwargs):
        raise AssertionError("resolution/gate logic must never touch the filesystem")

    monkeypatch.setattr(builtins, "open", _forbidden_open)

    client = _StubClient(companies=[_ACME, _ACME_LOGISTICS], projects=[_ROLLOUT])

    with pytest.raises(ProviderError):
        resolve_company(client, "Acme")
    with pytest.raises(ProviderError):
        resolve_project(client, "acme-co", None)
    with pytest.raises(ProviderError):
        check_kg_gate(_ZEPHYR)

    company = resolve_company(client, "Acme Robotics")
    check_kg_gate(company)
    resolve_project(client, "acme-co", "Rollout")


def test_list_published_opportunities_is_stage_filtered_and_ordered():
    client = _StubClient(opportunities=[_OPP_LATE, _OPP_DISCOVERED, _OPP_EARLY])
    opportunities = list_published_opportunities(client, "acme-co")
    assert [o["id"] for o in opportunities] == ["opp-a", "opp-b"]


def test_list_published_opportunities_order_is_deterministic_regardless_of_input_order():
    client_a = _StubClient(opportunities=[_OPP_LATE, _OPP_EARLY])
    client_b = _StubClient(opportunities=[_OPP_EARLY, _OPP_LATE])
    order_a = [o["id"] for o in list_published_opportunities(client_a, "acme-co")]
    order_b = [o["id"] for o in list_published_opportunities(client_b, "acme-co")]
    assert order_a == order_b == ["opp-a", "opp-b"]


def test_fetch_opportunity_paper_returns_the_paper_for_a_published_opportunity():
    client = _StubClient(details=_DETAILS)
    opportunity, paper = fetch_opportunity_paper(client, "acme-co", "opp-a")
    assert paper == "paper text a"
    assert opportunity["id"] == "opp-a"


def test_fetch_opportunity_paper_returns_none_rather_than_erroring_when_withheld():
    client = _StubClient(details=_DETAILS)
    opportunity, paper = fetch_opportunity_paper(client, "acme-co", "opp-c")
    assert paper is None
    assert opportunity["id"] == "opp-c"


def test_fetch_published_papers_selects_and_fetches_in_order():
    client = _StubClient(
        opportunities=[_OPP_LATE, _OPP_DISCOVERED, _OPP_EARLY], details=_DETAILS
    )
    fetched = fetch_published_papers(client, "acme-co")
    assert [f["opportunity"]["id"] for f in fetched] == ["opp-a", "opp-b"]
    assert [f["paper"] for f in fetched] == ["paper text a", "paper text b"]


def test_a_candidate_row_reads_a_missing_kg_flag_as_a_refusal():
    """The rule item 22's whole argument rests on, and nothing held it until
    2026-09-16: no test called `candidate_of` at all, so defaulting the missing
    key to True left the suite green.

    It must match `check_kg_gate`, which reads the same absence as `E_NO_KG`. A
    picker that let a row through on a field the registry did not state would
    offer a choice the gate then rejects, which is the defect item 22 exists to
    remove.
    """
    silent = {"id": "c9", "name": "Any Client"}
    assert candidate_of(silent)["has_kg"] is False
    with pytest.raises(ProviderError) as exc:
        check_kg_gate(silent)
    assert exc.value.code == "E_NO_KG"

    # And the flag is carried as it is stated, both ways round.
    assert candidate_of({"id": "a", "name": "A", "has_kg": True})["has_kg"] is True
    assert candidate_of({"id": "b", "name": "B", "has_kg": False})["has_kg"] is False


def test_a_candidate_row_carries_status_only_when_the_registry_states_one():
    """Absent means not answered, not ACTIVE, on the same footing as `pe_firm`.
    `status` is not a gate anywhere, so an invented one would be the only thing
    on the row claiming something the registry did not say."""
    assert "status" not in candidate_of({"id": "a", "name": "A", "has_kg": True})
    assert candidate_of(
        {"id": "a", "name": "A", "has_kg": True, "status": "DRAFT"})["status"] == "DRAFT"
