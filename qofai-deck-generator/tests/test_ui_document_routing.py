"""The Generate tab shows which opportunity each attachment writes.

Phase 2 of `build-plan-per-opportunity-slides.md`. The page used to scan the
first attachment only ("The FIRST document only: it is the base the deck is
written from"), and the run's own fallback read the first upload only, so two
PRDs naming two opportunities picked one opportunity and attached the other
PRD to nothing. Both now read every file, and the list says beside each file
what it was matched to or why it was not.

The page half is asserted on MARKUP rather than prose, because a prose
assertion passed once on a CSS comment for a button that did not exist.

Nothing here names a real client.
"""

import io
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ui"))

pytest.importorskip("flask")

import app as ui_app


def prd(opportunity):
    front = "| Prepared for | Stub Company |\n| --- | --- |\n"
    if opportunity:
        front += f"| Opportunity | {opportunity} |\n"
    return f"""Product Requirements Document

{front}
# 6. Phased Scope

| Phase | Focus | Key Deliverables |
| --- | --- | --- |
| Phase 1 Alpha Wks 1-2 | Do it | A thing |

# 7. Functional Requirements
"""


OPPORTUNITIES = [
    {"id": "o1", "title": "First Published Thing", "published_at": "2026-09-01"},
    {"id": "o2", "title": "Second Published Thing", "published_at": "2026-09-02"},
]


class _Platform:
    """One company, two published opportunities."""

    def call_tool_json(self, name, arguments=None):
        if name == "list_companies":
            return {"companies": [{"id": "c1", "name": "Stub Company",
                                   "has_kg": True, "status": "ACTIVE"}]}
        if name == "list_projects":
            return {"projects": []}
        if name == "list_opportunities":
            return {"opportunities": OPPORTUNITIES}
        raise AssertionError(f"unexpected tool call: {name}")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(ui_app, "_live_client", lambda: _Platform())
    ui_app.app.config["TESTING"] = True
    return ui_app.app.test_client()


def _files(*named):
    return [(io.BytesIO(text.encode("utf-8")), name) for name, text in named]


def _scan(client, *named):
    return client.post("/prd-scan", data={ui_app.ATTACHMENT_FIELD: _files(*named)},
                       content_type="multipart/form-data").get_json()


# --- the scan ----------------------------------------------------------------

def test_the_scan_matches_every_file_not_only_the_first(client):
    found = _scan(client, ("b.md", prd("Second Published Thing")),
                  ("a.md", prd("First Published Thing")))
    assert [(d["filename"], d["opportunity_id"]) for d in found["documents"]] == [
        ("b.md", "o2"), ("a.md", "o1")]
    # Both selected, the first file's own match leading.
    assert found["matched_opportunity_ids"] == ["o2", "o1"]
    assert found["matched_opportunity_id"] == "o2"


def test_a_file_the_scan_cannot_place_says_why(client):
    """A failed MATCH must be visible, because a PRD whose title matched
    nothing writes every slide and only the reviewer can tell that from a
    supporting note meant to."""
    found = _scan(client, ("a.md", prd("First Published Thing")),
                  ("note.md", prd("")), ("odd.md", prd("Nothing Like Either")))
    by_name = {d["filename"]: d for d in found["documents"]}
    assert by_name["note.md"]["opportunity_id"] == ""
    assert by_name["note.md"]["reason"] == "names no opportunity"
    assert by_name["odd.md"]["opportunity_id"] == ""
    assert "matches none or several" in by_name["odd.md"]["reason"]
    assert found["matched_opportunity_ids"] == ["o1"]


def test_one_file_scans_exactly_as_before(client):
    found = _scan(client, ("a.md", prd("First Published Thing")))
    assert found["matched_opportunity_id"] == "o1"
    assert found["matched_opportunity_ids"] == ["o1"]


# --- the run's own fallback --------------------------------------------------

def test_the_run_reads_every_upload_when_nothing_was_picked(client, monkeypatch):
    """The page's scan is a convenience; the run does the same read when the
    form arrives without an opportunity picked."""
    seen = {}

    def capture(*args, **kwargs):
        seen.update(kwargs)
        return "captured"

    monkeypatch.setattr(ui_app, "_run_and_render", capture)
    response = client.post("/run", data={
        "deck_type": "proposal", "data_source": ui_app.SOURCE_LIVE,
        ui_app.ATTACHMENT_FIELD: _files(("b.md", prd("Second Published Thing")),
                                        ("a.md", prd("First Published Thing"))),
    }, content_type="multipart/form-data")

    assert response.status_code == 200
    assert seen["opportunity_ids"] == ["o2", "o1"]
    assert seen["company_id"] == "c1"


def test_a_picked_opportunity_is_never_overridden_by_the_documents(client,
                                                                   monkeypatch):
    seen = {}
    monkeypatch.setattr(ui_app, "_run_and_render",
                        lambda *a, **k: seen.update(k) or "captured")
    client.post("/run", data={
        "deck_type": "proposal", "data_source": ui_app.SOURCE_LIVE,
        "company": "Stub Company", "opportunity_ids": ["o1"],
        ui_app.ATTACHMENT_FIELD: _files(("b.md", prd("Second Published Thing"))),
    }, content_type="multipart/form-data")
    assert seen["opportunity_ids"] == ["o1"]


# --- the page ----------------------------------------------------------------

def _page():
    with ui_app.app.test_request_context("/"):
        return " ".join(ui_app.render_studio("generate").split())


def test_the_page_posts_every_file_to_the_scan():
    page = _page()
    assert "[].slice.call(input.files).forEach(function (f) { body.append('documents', f); });" in page
    assert "body.append('documents', input.files[0]);" not in page


def test_the_page_selects_every_matched_opportunity():
    assert "if (matchedIds(data).indexOf(String(o.id)) !== -1) opt.selected = true;" in _page()


def test_the_list_draws_each_files_match_and_its_reason():
    page = _page()
    assert "tag.className = where.opportunity_id ? 'route' : 'route unrouted';" in page
    assert "'every opportunity \u00b7 ' + where.reason" in page
    assert "window.renderAttachments = render;" in page
    assert "if (window.renderAttachments) window.renderAttachments();" in page


def test_the_scan_waits_for_the_list_to_merge_a_new_pick():
    page = _page()
    assert "input.addEventListener('change', function () { setTimeout(scan, 0); });" in page
