"""The ambiguous-company chooser, offered from the PRD branch too.

The last open item of the PRD-first Generate tab. `/prd-scan` has always
returned an ambiguous client's candidates in the item 17 shape, and the PRD
branch printed the refusal sentence and stopped, so a document naming a company
that matches four registry rows was a dead end on a screen that already had the
surface to ask. The chooser itself was fine; nothing offered it.

Two halves, and only one of them is a route:

  * the ROUTE now accepts the reviewer's pick, because a re-scan without it
    re-asks the same ambiguous question and the chooser reappears forever; and
  * the PAGE now offers the chooser and re-scans on a pick, which is what
    matches the document's own opportunity title against the picked company's
    list rather than leaving a reviewer to spot it by eye.

The second half is client-side, so the tests for it assert the call graph
reaches the page. That is a weaker claim than "a human clicked it" and it is
made deliberately: it catches an export that was never added or an identifier
that was typed wrong, which is the inert-feature failure this repo has hit three
times in one day. It does not catch a chooser that draws in the wrong place.

Nothing here names a real client.
"""

import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ui"))

pytest.importorskip("flask")

import app as ui_app

_AMBIGUOUS = [
    {"id": "c1", "name": "Shared Name", "has_kg": True, "status": "ACTIVE"},
    {"id": "c2", "name": "Shared Name Logistics", "has_kg": True,
     "status": "ACTIVE"},
]

PRD = """Product Requirements Document

| Prepared by | QofAI, Forward-Deployed Engineering |
| --- | --- |
| Prepared for | Shared Name |
| Opportunity | A Published Thing |

# 6. Phased Scope

| Phase | Focus | Key Deliverables |
| --- | --- | --- |
| Phase 1 Alpha Wks 1-2 | Do it | A thing |

# 7. Functional Requirements
"""


class _Registry:
    """A stub platform: company search is a substring match, like the real one."""

    def __init__(self, companies):
        self._companies = companies

    def call_tool_json(self, name, arguments=None):
        arguments = arguments or {}
        if name == "list_companies":
            term = (arguments.get("search") or "").lower()
            return {"companies": [c for c in self._companies
                                  if term in c["name"].lower()]}
        if name == "list_projects":
            return {"projects": []}
        if name == "list_opportunities":
            return {"opportunities": [
                {"id": "o1", "title": "A Published Thing",
                 "published_at": "2026-09-01"}]}
        if name == "get_opportunity_details":
            return {"opportunity": {"id": "o1", "title": "A Published Thing"}}
        raise AssertionError(f"unexpected tool call: {name}")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(ui_app, "_live_client",
                        lambda: _Registry(_AMBIGUOUS))
    ui_app.app.config["TESTING"] = True
    return ui_app.app.test_client()


def _scan(client, **extra):
    data = {"documents": (io.BytesIO(PRD.encode("utf-8")), "a-prd.md")}
    data.update(extra)
    return client.post("/prd-scan", data=data,
                       content_type="multipart/form-data").get_json()


# --- the route -------------------------------------------------------------

def test_the_candidates_arrive_in_the_item_17_shape(client):
    found = _scan(client)
    assert found["company"] == ""
    assert "matched 2 companies" in found["error"]
    assert [c["company_id"] for c in found["candidates"]] == ["c1", "c2"]
    # The name that produced the refusal is said, so a reader can tell which of
    # the document's candidates it describes.
    assert "Shared Name" in found["error"]


def test_a_pick_resolves_the_same_document(client):
    """THE HALF THAT WAS MISSING. `/prd-scan` called `resolve_company` without
    the pick, so a re-scan after choosing asked the same question and the
    chooser came back forever."""
    found = _scan(client, company_id="c1")
    assert not found.get("error")
    assert found["company"] == "Shared Name"
    assert found["company_id"] == "c1"
    assert found["candidates"] == []


def test_a_pick_lets_the_documents_own_opportunity_match(client):
    """The point of having uploaded a PRD at all. Listing the opportunities is
    not enough: the document names one, and the match only becomes possible
    once the company has resolved."""
    found = _scan(client, company_id="c1")
    assert found["opportunity_stated"] == "A Published Thing"
    assert found["matched_opportunity_id"] == "o1"


def test_an_unknown_pick_is_refused_rather_than_ignored(client):
    """A stale id, an id from another company's list and a typed one are the
    same shape from here. Resolving one of them to whatever the name happens to
    match would build a deck for a company the reviewer did not pick."""
    found = _scan(client, company_id="not-a-real-id")
    assert found["company"] == ""
    assert "not-a-real-id" in found["error"]


# --- the page --------------------------------------------------------------

def _page():
    with ui_app.app.test_request_context("/"):
        return ui_app.render_studio("generate")


def test_the_chooser_is_reachable_from_the_prd_branch():
    """It lives in the typed-company block and the PRD branch is a separate
    IIFE, which is the whole reason the PRD branch had none to offer. Exported
    rather than duplicated: two company pickers that could drift apart is how
    one ends up refusing a pick the other accepts."""
    page = _page()
    assert "window.offerCompanies = offerCompanies" in page
    assert "window.offerCompanies(data.candidates, data.error || '', scan)" in page


def test_a_pick_from_the_prd_branch_rescans_rather_than_only_listing():
    page = _page()
    assert "function offerCompanies(candidates, message, after)" in page
    assert "if (typeof after === 'function') { after(); return; }" in page


def test_the_pick_travels_with_the_rescan():
    """Without it the route re-asks and the chooser reappears forever."""
    assert "body.append('company_id', picked.value)" in _page()


def test_the_page_script_is_valid_javascript():
    """The failure this guards against is total: an unrendered template
    expression or an unbalanced brace kills the whole inline script, and with it
    every tab, the scan and the chooser at once. Skipped where node is absent
    rather than silently passing."""
    import re
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    if not node:
        pytest.skip("no node on this machine to parse with")
    scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>",
                         _page(), re.S)
    assert scripts, "the studio page carries an inline script"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                     encoding="utf-8") as handle:
        handle.write("\n;\n".join(scripts))
        path = handle.name
    try:
        done = subprocess.run([node, "--check", path], capture_output=True,
                              text=True)
        assert done.returncode == 0, done.stderr[:800]
    finally:
        os.unlink(path)
