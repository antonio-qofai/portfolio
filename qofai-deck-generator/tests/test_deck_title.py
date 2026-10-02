"""Tests for the deck's title when the platform has no project to take it from.

Every engagement project on the platform contributes exactly one string to a
proposal deck: the cover `Title`, plus the same string in the page marks. A
company can carry published opportunities (the research paper nearly every
other role is written from) and no project at all, which used to stop the run at
`E_PROJECT_NOT_FOUND` before the paper was ever read. This is that case, and the
two ways a title now reaches the cover without one: a title the studio supplies,
and, failing that, the selected opportunity's own title.

What must NOT move is the guard from 98e5d15: a company that DOES list projects
still has to name one, and a typed name matching none of them is still refused
rather than guessed at. Those two tests are here for that reason.

Stub clients are hand-authored per the house pattern, with fictitious names, so
nothing in `src/` is built around one company's shape. No test here reaches the
network.
"""

import json
import pathlib

import pytest

from data_source_adapter import map_packet, shape_request
from live_proposal_provider import (
    LiveProposalProvider,
    ProviderError,
    resolve_project,
)

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "data-provider" / "fixtures"
ROOT = pathlib.Path(__file__).resolve().parent.parent

_COMPANY = {"id": "subject-co", "name": "Test Subject Three", "has_kg": True}
_LISTED = {"id": "proj-listed", "name": "Listed Test Project"}
_OPPORTUNITY = {
    "id": "OPP-THREE",
    "title": "Third Test Opportunity",
    "description": "A third placeholder description.",
    "ebitda_impact": {"min": 1.1, "max": 2.2, "unit": "pp"},
    "stage": "published",
    "published_at": "2026-03-01T00:00:00+00:00",
}

STAMP = "2026-08-20T00:00:00Z"
STUDIO_TITLE = "Studio-Named Test Engagement"


def paper():
    path = FIXTURES / "paper-excerpt-scenario-rows-canonical.json"
    return json.loads(path.read_text())["opportunity"]["research_paper_natural"]


class _StubClient:
    """One company, its published opportunity, and a project list a test sets.

    `projects=[]` is the case this file exists for: a real company on the
    platform carrying opportunities and no engagement project.
    """

    def __init__(self, projects=None):
        self._projects = [] if projects is None else projects
        self.calls = []

    def call_tool_json(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "list_companies":
            found = arguments["search"].lower() in _COMPANY["name"].lower()
            return {"companies": [_COMPANY] if found else []}
        if name == "list_projects":
            return {"projects": list(self._projects)}
        if name == "list_opportunities":
            return {"opportunities": [_OPPORTUNITY]}
        if name == "get_opportunity_details":
            record = dict(_OPPORTUNITY)
            record["research_paper_natural"] = paper()
            return {"opportunity": record}
        raise AssertionError(f"unexpected tool call: {name}")


def envelope(client, **request_overrides):
    """The raw envelope the seam returned, before any gate or mapping."""
    request = {
        "company": _COMPANY["name"],
        "project": "",
        "opportunity_id": _OPPORTUNITY["id"],
        "options": {"min_data_completeness": 0.70},
    }
    request.update(request_overrides)
    provider = LiveProposalProvider(client, generated_at=STAMP)
    return provider.poll(provider.submit(request))["envelope"]


def cover_title(packet_md):
    """The cover's `Title` bullet, read off the assembled packet's section 1."""
    for line in packet_md.splitlines():
        if line.startswith("- **Title:**"):
            return line.split("`")[1]
    return ""


# --- resolution: the two new ways a title arrives ---------------------------


def test_a_company_with_no_listed_project_defers_the_title():
    """No project to resolve and no studio title, so resolution has nothing to
    name the deck yet and says so by returning nothing, rather than raising.
    The caller fills it from the opportunity it selects next."""
    client = _StubClient(projects=[])
    assert resolve_project(client, "subject-co", "") is None


def test_a_studio_title_names_the_deck_when_the_platform_lists_no_project():
    client = _StubClient(projects=[])
    project = resolve_project(client, "subject-co", "", deck_title=STUDIO_TITLE)
    assert project["name"] == STUDIO_TITLE
    assert project["title_source"] == "studio"


def test_a_studio_title_overrides_a_matched_project_name():
    """The studio-input half: a reviewer who types a title gets it on the cover
    even where a project record exists. The record is still resolved, so which
    engagement the deck belongs to stays recorded on its id."""
    client = _StubClient(projects=[_LISTED])
    project = resolve_project(client, "subject-co", "Listed", deck_title=STUDIO_TITLE)
    assert project["name"] == STUDIO_TITLE
    assert project["id"] == "proj-listed"
    assert project["title_source"] == "studio"


# --- resolution: the guard that must not weaken -----------------------------


def test_a_typed_name_matching_no_listed_project_is_still_refused():
    """98e5d15's guard. The company lists projects, so a name that matches none
    of them is a reviewer error, not an invitation to invent a title — even with
    a studio title present to fall back on."""
    client = _StubClient(projects=[_LISTED])
    with pytest.raises(ProviderError) as exc:
        resolve_project(client, "subject-co", "No Such Project",
                        deck_title=STUDIO_TITLE)
    assert exc.value.code == "E_PROJECT_NOT_FOUND"


def test_a_listed_project_and_no_name_is_still_refused():
    client = _StubClient(projects=[_LISTED])
    with pytest.raises(ProviderError) as exc:
        resolve_project(client, "subject-co", "")
    assert exc.value.code == "E_PROJECT_REQUIRED"


# --- the request contract ---------------------------------------------------


def test_a_studio_title_travels_in_place_of_a_project():
    request = shape_request("Co", "", deck_title=STUDIO_TITLE)
    assert request["deck_title"] == STUDIO_TITLE


def test_an_opportunity_alone_names_the_deck_by_derivation():
    """The WTG shape: no project on the platform, no title typed, and the
    opportunity the reviewer picked is what the title comes from. The request is
    accepted because it says what the deck is about."""
    request = shape_request("Co", "", opportunity_id="OPP-THREE")
    assert request["opportunity_id"] == "OPP-THREE"
    assert "deck_title" not in request


def test_a_request_that_names_the_deck_no_way_at_all_is_refused():
    with pytest.raises(ValueError):
        shape_request("Co", "")


# --- the mapper: one title, two places on the deck -------------------------


def test_the_page_marks_read_the_studio_title_when_one_is_given():
    packet_md = (ROOT / "proposal-data-packet-EXAMPLE.md").read_text()
    request = {"company": "Co", "project": "Typed Project",
               "deck_title": STUDIO_TITLE}
    mapped = map_packet(packet_md, request)
    assert mapped["project_name"] == STUDIO_TITLE
    # The page marks carry the same string, upper-cased by the mapper.
    assert mapped["footer_right"] == STUDIO_TITLE.upper()


def test_the_page_marks_keep_reading_the_project_when_no_title_is_given():
    """Nothing moves for a run that names a project, which is every run that
    worked before this change."""
    packet_md = (ROOT / "proposal-data-packet-EXAMPLE.md").read_text()
    mapped = map_packet(packet_md, {"company": "Co", "project": "Typed Project"})
    assert mapped["project_name"] == "Typed Project"
    assert mapped["footer_right"] == "TYPED PROJECT"


# --- the seam, end to end ---------------------------------------------------


def test_a_company_with_no_project_takes_its_title_from_the_opportunity():
    result = envelope(_StubClient(projects=[]))
    assert result["status"] == "ok", result
    assert cover_title(result["packet"]) == _OPPORTUNITY["title"]


def test_the_derived_title_reaches_the_page_marks_too():
    """The 98e5d15 invariant on the new path: whatever names the cover names the
    page marks, so a deck cannot be titled one thing and marked another."""
    result = envelope(_StubClient(projects=[]))
    assert result["request_echo"]["deck_title"] == _OPPORTUNITY["title"]
    mapped = map_packet(result["packet"], result["request_echo"])
    assert mapped["project_title"] == _OPPORTUNITY["title"]
    assert mapped["project_name"] == _OPPORTUNITY["title"]
    assert mapped["footer_right"] == _OPPORTUNITY["title"].upper()


def test_a_studio_title_beats_the_opportunity_title_on_the_seam():
    result = envelope(_StubClient(projects=[]), deck_title=STUDIO_TITLE)
    assert cover_title(result["packet"]) == STUDIO_TITLE
    assert result["request_echo"]["deck_title"] == STUDIO_TITLE


def test_a_run_that_names_a_project_echoes_no_title_of_its_own():
    """A platform project still names its own deck, and the echo stays exactly
    what the caller sent, so the page marks keep reading the typed reference."""
    result = envelope(_StubClient(projects=[_LISTED]), project="Listed")
    assert cover_title(result["packet"]) == _LISTED["name"]
    assert "deck_title" not in result["request_echo"]
