"""The studio's half of item 14: attaching the documents a deck is written from.

Four things are held here and the last one is why the other three exist.

  1. THE FORM posts multipart and carries a file input whose accept list is read
     off `document_text.ACCEPTED_EXTENSIONS`, so the markup and the validation
     cannot drift apart.
  2. THE BYTES ARE READ IN THE REQUEST. A run has executed on a worker thread
     since 2026-08-26, and `request.files` is a handle on the live request, so a
     thread that tried to read one would work locally whenever the request
     happened to outlive it and fail on the service. The test for this asserts
     the worker has NO request context and can still read every byte.
  3. VALIDATION is the studio's job and not `document_text`'s, because the
     answer to a file that is too large is a form rejection with a message. Each
     refusal costs no run, no thread and no API spend.
  4. THE PANEL tells the reviewer which document filled which field, where the
     documents disagreed, and what was stated and not used. Quiet when there is
     nothing to say: no attachment, no panel.
  5. THE RECORD A SAVED DECK KEEPS (step 5): the extracted text plus each
     file's name, kind, size and SHA-256, written into the store's `details`
     column and reached again when the deck is reopened, on a machine where the
     run and its files are long gone.

Flask's test client drives the app with the pipeline stubbed, so no API key, no
network and no real render. Every filename here is meaningless on purpose.
"""

import hashlib
import io
import os
import pathlib
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ui"))

pytest.importorskip("flask")

import base_document  # noqa: E402
import provenance_report  # noqa: E402
from document_text import ACCEPTED_EXTENSIONS  # noqa: E402
from live_proposal_provider import LiveProposalProvider  # noqa: E402
from packet_assembly import assemble  # noqa: E402
from source_span import PAPER, Document  # noqa: E402
from test_paper_extraction import (answer, field, item,  # noqa: E402
                                   platform_paper)
from deck_run import run_deck  # noqa: E402
from test_base_document import (BASELINE_ONLY, OLDER_FULL_BASELINE,  # noqa: E402
                                REVENUE_ONLY, SCENARIOS_ONLY)

_ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURE_PACKET = "proposal-data-packet-EXAMPLE.md"

OK_RESULT = {
    "status": "ok", "prompt": "(design prompt body)",
    "applied_preferences": [], "number": 1,
    "render_fidelity": {"ok": True, "missing_values": {}},
    "layout": {"ok": True, "checked": False, "skipped": "not rendered",
               "slides": 0, "findings": [], "summary": []},
}


@pytest.fixture
def studio(tmp_path, monkeypatch):
    """The app with the pipeline stubbed, recording what the worker received.

    The stub runs ON THE WORKER THREAD, which is what makes it the right place
    to check that the attachments survived the boundary: it records whether a
    request context was available to it and reads every byte it was handed.
    """
    import flask

    import app as ui_app

    captured = {}
    captured["result"] = dict(OK_RESULT)

    def _stub(deck_type, company, project, provider, **kwargs):
        captured["kwargs"] = kwargs
        captured["had_request_context"] = flask.has_request_context()
        uploads = kwargs.get("uploads") or ()
        # Read on the worker, which is the whole point: if the route had passed
        # a handle on `request.files` instead of bytes, this is where it would
        # break, and it would break only when the request had already ended.
        captured["read"] = [(upload.filename, len(upload.data)) for upload in uploads]
        captured["first_bytes"] = [upload.data[:24] for upload in uploads]
        return captured["result"]

    monkeypatch.setattr(ui_app, "generate_and_save_deck", _stub)
    monkeypatch.setattr(
        ui_app, "_live_provider",
        lambda: LiveProposalProvider(object(), generated_at="2026-08-15T00:00:00Z"),
    )
    monkeypatch.setattr(ui_app, "DECKS_ROOT", str(tmp_path))
    ui_app.app.testing = True
    ui_app._LAST_RESULT.clear()
    return ui_app.app.test_client(), captured


def attachment(text, name="attachment.md"):
    return (io.BytesIO(text.encode()), name)


def live_run(client, uploads=None, **overrides):
    data = {
        "data_source": "live", "deck_type": "proposal",
        "company": "Any Client", "project": "Any Project",
        "opportunity_id": "OPP-1",
    }
    data.update(overrides)
    if uploads is not None:
        data["documents"] = uploads
    return run_deck(client, data=data)


# --- the form ---------------------------------------------------------------


def generate_panel():
    """The Generate tab's markup. Rendered inside a request context because the
    form's action is a `url_for`, the same as every other template here."""
    import app as ui_app

    with ui_app.app.test_request_context("/"):
        return ui_app._generate_panel_html()


def test_the_generate_form_posts_multipart():
    """One attribute, and no JavaScript: the form posts natively and the page
    script only drives the progress overlay."""
    assert 'enctype="multipart/form-data"' in generate_panel()


def test_the_file_input_accepts_exactly_what_the_extractor_accepts():
    """Read off `document_text.ACCEPTED_EXTENSIONS`, which step 1 exported for
    this, rather than restated in markup where the two could drift."""
    panel = generate_panel()
    assert 'type="file"' in panel
    assert f'accept="{",".join(ACCEPTED_EXTENSIONS)}"' in panel
    for extension in ACCEPTED_EXTENSIONS:
        assert extension in panel


def test_the_file_input_takes_more_than_one_document():
    assert "multiple" in generate_panel()


def test_the_form_states_both_ceilings():
    import app as ui_app

    panel = generate_panel()
    assert ui_app._megabytes(ui_app.MAX_ATTACHMENT_BYTES) in panel
    assert str(ui_app.MAX_ATTACHMENTS) in panel


def test_the_request_ceiling_is_derived_from_the_two_named_ones():
    """The backstop under them, and it can never be the binding limit on a
    legitimate run."""
    import app as ui_app

    assert app_limit(ui_app) > ui_app.MAX_ATTACHMENT_BYTES * ui_app.MAX_ATTACHMENTS


def app_limit(ui_app):
    return ui_app.app.config["MAX_CONTENT_LENGTH"]


# --- the bytes are read in the request --------------------------------------


def test_the_worker_reads_every_byte_and_has_no_request_of_its_own(studio):
    """THE TRAP, asserted from the worker's side.

    `request.files` does not survive the thread, so if the route had passed a
    handle instead of bytes this test is where it would fail, and on the service
    it would fail only sometimes. The worker is asserted to have no request
    context at all AND to be able to read the whole attachment.
    """
    client, captured = studio
    live_run(client, uploads=[attachment(SCENARIOS_ONLY)])
    assert captured["had_request_context"] is False
    assert captured["read"] == [("attachment.md", len(SCENARIOS_ONLY.encode()))]
    assert captured["first_bytes"][0] == SCENARIOS_ONLY.encode()[:24]


def test_the_uploads_reach_the_pipeline_as_upload_objects(studio):
    client, captured = studio
    live_run(client, uploads=[attachment(SCENARIOS_ONLY)])
    uploads = captured["kwargs"]["uploads"]
    assert all(isinstance(upload, base_document.Upload) for upload in uploads)


def test_a_run_with_no_attachment_passes_no_uploads_at_all(studio):
    """The run that worked before attachments existed submits the same request
    it always did, with no `uploads` key to be found."""
    client, captured = studio
    live_run(client)
    assert "uploads" not in captured["kwargs"]


def test_an_empty_file_input_is_no_attachment(studio):
    """A browser posts an empty part for a file input nobody used."""
    client, captured = studio
    live_run(client, uploads=[(io.BytesIO(b""), "")])
    assert "uploads" not in captured["kwargs"]


def test_the_order_the_form_posted_them_is_the_order_the_pipeline_gets(studio):
    """The precedence order, preserved. `base_document` makes the first document
    the base, so this order is the whole of which document wins."""
    client, captured = studio
    live_run(client, uploads=[
        attachment(BASELINE_ONLY, "first.md"),
        attachment(SCENARIOS_ONLY, "second.md"),
        attachment(REVENUE_ONLY, "third.md"),
    ])
    assert [name for name, _size in captured["read"]] == [
        "first.md", "second.md", "third.md",
    ]


def test_the_reverse_order_reaches_the_pipeline_reversed(studio):
    """So the test above is about order and not about alphabetising."""
    client, captured = studio
    live_run(client, uploads=[
        attachment(REVENUE_ONLY, "third.md"),
        attachment(SCENARIOS_ONLY, "second.md"),
        attachment(BASELINE_ONLY, "first.md"),
    ])
    assert [name for name, _size in captured["read"]] == [
        "third.md", "second.md", "first.md",
    ]


# --- validation, which is the studio's job ----------------------------------


def test_more_attachments_than_the_ceiling_is_refused_before_any_run(studio):
    client, captured = studio
    import app as ui_app

    response = live_run(client, uploads=[
        attachment(SCENARIOS_ONLY, f"n{n}.md")
        for n in range(ui_app.MAX_ATTACHMENTS + 1)
    ])
    body = response.get_data(as_text=True)
    assert "too_many_attachments" in body
    assert "kwargs" not in captured


def test_exactly_the_ceiling_is_accepted(studio):
    client, captured = studio
    import app as ui_app

    live_run(client, uploads=[
        attachment(SCENARIOS_ONLY, f"n{n}.md")
        for n in range(ui_app.MAX_ATTACHMENTS)
    ])
    assert len(captured["read"]) == ui_app.MAX_ATTACHMENTS


def test_a_file_over_the_per_file_ceiling_is_refused_before_any_run(studio, monkeypatch):
    client, captured = studio
    import app as ui_app

    monkeypatch.setattr(ui_app, "MAX_ATTACHMENT_BYTES", 64)
    response = live_run(client, uploads=[attachment("x" * 200, "big.md")])
    body = response.get_data(as_text=True)
    assert "attachment_too_large" in body
    assert "big.md" in body
    assert "kwargs" not in captured


def test_the_too_large_refusal_says_what_to_do_about_it(studio, monkeypatch):
    """Every refusal here carries a next step, the way every `document_text`
    code does: a reviewer told only "too large" does not know that the reason is
    usually that the file is a scan."""
    client, _captured = studio
    import app as ui_app

    monkeypatch.setattr(ui_app, "MAX_ATTACHMENT_BYTES", 64)
    body = live_run(client, uploads=[attachment("x" * 200)]).get_data(as_text=True)
    assert "scan" in body
    assert "text can be" in body


def test_a_file_at_the_ceiling_exactly_is_accepted(studio, monkeypatch):
    client, captured = studio
    import app as ui_app

    monkeypatch.setattr(ui_app, "MAX_ATTACHMENT_BYTES", 64)
    live_run(client, uploads=[attachment("x" * 64)])
    assert captured["read"] == [("attachment.md", 64)]


def test_an_attachment_on_a_fixture_run_is_refused_with_the_reason(studio):
    """The fixture provider replays a frozen packet and refuses an attachment
    rather than dropping one. Said here so the reviewer reads a sentence about
    the data source instead of a ValueError out of a worker thread."""
    client, captured = studio
    response = run_deck(client, data={
        "deck_type": "proposal", "company": "Any Client",
        "project": "Any Project", "packet": FIXTURE_PACKET,
        "documents": [attachment(SCENARIOS_ONLY)],
    })
    body = response.get_data(as_text=True)
    assert "attachment_needs_live_source" in body
    assert "live" in body
    assert "kwargs" not in captured


def test_a_fixture_run_with_no_attachment_is_untouched(studio):
    client, captured = studio
    run_deck(client, data={
        "deck_type": "proposal", "company": "Any Client",
        "project": "Any Project", "packet": FIXTURE_PACKET,
    })
    assert "kwargs" in captured
    assert "uploads" not in captured["kwargs"]


def test_the_ceilings_are_overridable_by_environment_the_way_the_paths_are():
    """Named constants read from the environment, so the hosted service can be
    tightened without a deploy of new code."""
    source = (_ROOT / "ui" / "app.py").read_text()
    assert 'os.environ.get("MAX_ATTACHMENT_BYTES"' in source
    assert 'os.environ.get("MAX_ATTACHMENTS"' in source


# --- the request ceiling, which refuses before any of this runs -------------
#
# `MAX_CONTENT_LENGTH` is a limit on the REQUEST, so Werkzeug turns an oversize
# body away before `_read_attachments` is reached and before `run()` is entered.
# Until the handler below existed that reviewer got Werkzeug's bare "413 Request
# Entity Too Large" page: no studio around it, no remediation on it, and the form
# gone. A scanned contract over the ceiling is the file that produces it.

# A ceiling in real megabytes rather than a token one, because the refusal states
# it and "larger than 0.0 MB" is not a sentence to put in front of a reviewer.
TEST_REQUEST_CEILING = 1024 * 1024


def oversize_post(client, monkeypatch, ceiling=TEST_REQUEST_CEILING):
    """One request bigger than the request ceiling, posted as a browser posts it."""
    import app as ui_app

    monkeypatch.setitem(ui_app.app.config, "MAX_CONTENT_LENGTH", ceiling)
    return client.post("/run", content_type="multipart/form-data", data={
        "data_source": "live", "deck_type": "proposal",
        "company": "Any Client", "project": "Any Project",
        "opportunity_id": "OPP-1",
        "documents": (io.BytesIO(b"x" * (ceiling * 2)), "big.pdf"),
    })


def test_a_request_over_the_ceiling_gets_the_studios_own_refusal(studio, monkeypatch):
    """The whole point: the studio answers, in the studio, with a next step."""
    client, captured = studio
    response = oversize_post(client, monkeypatch)
    body = response.get_data(as_text=True)
    assert response.status_code == 413
    assert "request_too_large" in body
    assert "kwargs" not in captured


def test_the_refusal_arrives_inside_the_studio_and_not_on_a_page_of_its_own(
        studio, monkeypatch):
    """Werkzeug's page is a title and two lines. This one is the studio: the
    three tabs, the Generate form to re-run from, and the Result tab open."""
    client, _captured = studio
    body = oversize_post(client, monkeypatch).get_data(as_text=True)
    assert 'data-active-tab="result"' in body
    assert 'enctype="multipart/form-data"' in body
    assert "Generate deck" in body
    assert "The data value transmitted exceeds the capacity limit" not in body
    assert "Request Entity Too Large" not in body


def test_the_refusal_says_what_the_per_file_ceiling_says(studio, monkeypatch):
    """One file, one reason, one sentence. Which ceiling caught it is the only
    difference between the two refusals, so the remediation is shared rather
    than restated — two accounts of one refusal is two things to reconcile."""
    client, _captured = studio
    import app as ui_app

    body = oversize_post(client, monkeypatch).get_data(as_text=True)
    assert ui_app.ATTACHMENT_TOO_LARGE_REMEDIATION in body

    monkeypatch.setattr(ui_app, "MAX_ATTACHMENT_BYTES", 64)
    per_file = live_run(client, uploads=[attachment("x" * 200)]).get_data(as_text=True)
    assert ui_app.ATTACHMENT_TOO_LARGE_REMEDIATION in per_file


def test_the_refusal_names_both_ceilings(studio, monkeypatch):
    """The one that caught this request, and the per-file one the reviewer has
    to get under, because "too large" without a number is not actionable."""
    client, _captured = studio
    import app as ui_app

    body = oversize_post(client, monkeypatch).get_data(as_text=True)
    assert ui_app._megabytes(TEST_REQUEST_CEILING) in body
    assert ui_app._megabytes(ui_app.MAX_ATTACHMENT_BYTES) in body


def test_the_refusal_says_the_form_is_gone_rather_than_pretending_otherwise(
        studio, monkeypatch):
    """Nothing typed into the form can be recovered — the request carrying it is
    what was refused — so the page says so instead of rendering a Generate tab
    that looks like it kept the inputs."""
    client, _captured = studio
    body = oversize_post(client, monkeypatch).get_data(as_text=True)
    assert "did not survive" in body
    assert "filling in again" in body


def test_the_handler_never_touches_the_body(studio, monkeypatch):
    """The trap in writing one. `request.form` and `request.files` parse the
    body, which is the thing that raised this, so either one inside the handler
    would raise 413 inside the 413 handler and turn a refusal into a 500. Held
    both ways: on the source, and on the status of a real oversize post."""
    import ast
    import inspect
    import textwrap

    import app as ui_app

    # Off the PARSED source, not by substring: the docstring names both of them
    # to say why they are not there, and a substring search cannot tell the
    # warning from the mistake.
    tree = ast.parse(textwrap.dedent(inspect.getsource(ui_app._request_too_large)))
    reads = {node.attr for node in ast.walk(tree)
             if isinstance(node, ast.Attribute)
             and isinstance(node.value, ast.Name) and node.value.id == "request"}
    assert not reads & {"form", "files", "data", "values", "json", "stream"}

    client, _captured = studio
    assert oversize_post(client, monkeypatch).status_code == 413


def test_an_oversize_request_does_not_take_the_deck_on_screen_with_it(
        studio, monkeypatch, tmp_path):
    """A reviewer who has a deck and then over-attaches keeps the deck. The
    refusal is the response to this request; it does not become the session's
    remembered result."""
    import app as ui_app

    client, captured = studio
    deck = tmp_path / "output-1.html"
    deck.write_text("<html><body>a deck</body></html>", encoding="utf-8")
    ui_app._LAST_RESULT.update({"result": dict(OK_RESULT), "status": "ok",
                                "deck_path": str(deck)})

    oversize_post(client, monkeypatch)
    assert ui_app._LAST_RESULT.get("deck_path") == str(deck)
    assert "kwargs" not in captured


def test_an_extraction_failure_surfaces_with_its_own_remediation(studio):
    """Every `document_text` code was written to tell a reviewer what to do
    next, so the studio shows that sentence rather than composing a second one."""
    from document_text import extract_text

    client, captured = studio
    failure = extract_text(b"", "attachment.pdf")
    captured["result"] = {
        "status": "error", "code": failure.code, "message": failure.message,
        "remediation": failure.remediation, "details": failure.details,
    }
    body = live_run(client, uploads=[attachment(SCENARIOS_ONLY)]).get_data(as_text=True)
    assert failure.code in body
    assert "re-attach it" in body


# --- the panel --------------------------------------------------------------


def report_for(*texts):
    """A real provenance report, built the way a run builds one."""
    documents = [Document(name=f"doc{n}.md", kind="markdown")
                 for n, _text in enumerate(texts[:-1])]
    sources = [
        base_document.Source(name=document.name, kind=document.kind, text=text)
        for document, text in zip(documents, texts[:-1])
    ]
    sources.append(base_document.Source(name="", kind=base_document.PAPER_KIND,
                                        text=texts[-1]))
    packets = [assemble("OPP-1", text, document)
               for text, document in zip(texts, list(documents) + [PAPER])]
    return provenance_report.build(base_document.merge_packets(packets), sources)


def panel_for(report, result=None, status="ok"):
    import app as ui_app

    ui_app._LAST_RESULT.clear()
    with ui_app.app.test_request_context("/"):
        ctx = ui_app._build_result_ctx(
            result=result or dict(OK_RESULT, provenance=report), status=status,
            deck_type="proposal", company="Any Client",
        )
        return ui_app.render_studio("result", result_ctx=ctx)


def test_a_run_with_no_attachment_grows_no_panel_about_sources(studio):
    """The quiet case, and the one that matters most: every run that worked
    before attachments existed looks exactly as it did."""
    client, _captured = studio
    body = live_run(client).get_data(as_text=True)
    assert "Which document this deck was written from" not in body


def test_an_empty_report_renders_no_panel():
    assert "Which document this deck was written from" not in panel_for({})


def test_a_run_where_nothing_disagreed_shows_no_disagreement_table():
    """Nobody should have to read a table to learn that nothing disagreed."""
    report = report_for(SCENARIOS_ONLY, OLDER_FULL_BASELINE)
    assert report["conflicts"] == []
    body = panel_for(report)
    assert "Both documents answered, differently" not in body


def test_a_run_that_set_nothing_aside_shows_no_set_aside_table():
    report = report_for(BASELINE_ONLY, OLDER_FULL_BASELINE)
    assert report["set_aside"] == []
    assert "Stated, and not used" not in panel_for(report)


def test_the_panel_comes_back_after_a_tab_switch(studio):
    """The report rides on the remembered result, so it is rebuilt rather than
    lost the moment a reviewer looks at the Decks tab."""
    import app as ui_app

    client, captured = studio
    captured["result"] = dict(
        OK_RESULT, provenance=report_for(REVENUE_ONLY, OLDER_FULL_BASELINE))
    live_run(client, uploads=[attachment(REVENUE_ONLY)])
    recalled = ui_app._recall_result_ctx()
    assert recalled["provenance"]["set_aside"]


# --- what the model legs took, which the parsers cannot see -----------------
#
# THE DEFECT, from the first live run of item 14 (2026-09-08). A real 392KB
# client PRD, attached as the base, reshaped the deck's substance: terms that
# appear in the PRD and in no published paper are on the rendered slides. The
# panel reported it at "0 fields" under a sentence saying a source showing zero
# contributed nothing the parsers could read.
#
# The number was right and the label was the total. `counts` tallied
# `SourcedFigure` fields, which is what the DETERMINISTIC PARSERS lift, and
# those parsers are written for the published paper's markdown table shapes, so
# a PDF-extracted document scores zero there by construction. The PRD's
# contribution arrived through the two MODEL legs, which read `base.text` and
# therefore read the PRD, and the panel did not count them at all.
#
# This is the artifact Casey sees, and shown that panel he concludes the
# feature does not work. The fix is three counts rather than a bigger one: a
# figure a parser lifted, a value a model quoted and a sentence a model wrote
# are different things and this build does not blur them.

PROSE_BASE = platform_paper()


def platform_answer(text):
    """One slot the extraction pass quotes out of the base document's PROSE.

    Every span is asserted present in the document first, so a test that
    scripted an unverifiable answer fails here rather than passing on a refusal:
    these go through `paper_extraction.read_response` exactly as a live answer
    would.
    """
    components = [
        ("A mobile ticket capture app records the work at the job site, so the "
         "ticket\nleaves with the crew rather than arriving days later.",
         "The Proposed Solution", "A mobile ticket capture app"),
        ("A scheduling service holds one live dispatch board every branch reads "
         "from,\nreplacing the whiteboard rebuilt each morning.",
         "The Proposed Solution", "A scheduling service"),
    ]
    for span, _section, _title in components:
        assert span in text, span
    return answer(field("platform_layers", *(
        item(span, section, title=title, body=span)
        for span, section, title in components
    )))


def framing_answer(text):
    """Two framing lines the writing pass writes from the base document's own
    sections. GENERATED, never quoted, which is the distinction the counts keep."""
    evidence = ("A mobile ticket capture app records the work at the job site, "
                "so the ticket\nleaves with the crew rather than arriving days "
                "later.")
    assert evidence in text
    return {"sentences": [
        {"path": "copy.platform_headline",
         "text": "One live picture of the work, from the job site to the ledger.",
         "sections": ["The Proposed Solution"],
         "evidence": [evidence]},
        {"path": "copy.platform_summary",
         "text": "The ticket leaves with the crew instead of arriving days later.",
         "sections": ["The Proposed Solution"],
         "evidence": [evidence]},
    ]}


def report_with_both_passes(uploads=(), spec=None):
    """A real provider run with both model legs faked through their real
    verifiers, which is the only way to hold this: the counts are about what the
    passes did, so a report built without them would test nothing."""
    from test_base_document import request as live_request
    from test_live_seam import CLIENTS, STAMP, StubClient
    from test_second_pass import extractor_returning, writer_returning

    spec = spec or CLIENTS["one"]
    provider = LiveProposalProvider(
        StubClient(spec), generated_at=STAMP,
        extractor=extractor_returning(platform_answer(PROSE_BASE)),
        writer=writer_returning(framing_answer(PROSE_BASE)),
    )
    handle = provider.submit(live_request(spec, floor=0.0), uploads=list(uploads))
    envelope = provider.poll(handle)["envelope"]
    assert envelope["status"] == "ok", envelope
    return envelope["provenance"]


def prose_upload(name="a-prose-document.md"):
    return base_document.Upload(filename=name, data=PROSE_BASE.encode())


def test_the_base_document_is_counted_for_what_the_model_legs_took_from_it():
    """The defect, reproduced and closed. This attachment is prose: it carries
    no markdown table any deterministic parser reads, exactly like the live PRD,
    so the parsed column is 0. The panel used to stop there."""
    report = report_with_both_passes([prose_upload()])
    row = {count["document"]: count for count in report["counts"]}
    base = row["a-prose-document.md"]

    assert base["parsed"] == 0
    assert base["quoted"] >= 1
    assert base["written"] >= 1
    assert base["total"] == base["parsed"] + base["quoted"] + base["written"]
    assert base["total"] > 0


def test_the_paper_is_still_counted_for_what_its_tables_gave():
    """And the other half is unchanged: the published paper's own figures are
    still what the parsers lifted out of it."""
    report = report_with_both_passes([prose_upload()])
    row = {count["document"]: count for count in report["counts"]}
    assert row["the published opportunity paper"]["parsed"] > 0


def test_the_three_kinds_stay_in_three_lists_and_are_never_added_together():
    """A figure a parser lifted, a value a model quoted and a sentence a model
    wrote are three different claims. Section 8 keeps them in separate blocks
    and so does this: three lists, three columns, and no field in two of them."""
    report = report_with_both_passes([prose_upload()])
    assert report["quoted"] and report["written"]
    quoted_fields = {row["field"] for row in report["quoted"]}
    written_fields = {row["field"] for row in report["written"]}
    figure_fields = {row["field"] for row in report["filled"]}
    assert not quoted_fields & written_fields
    assert not quoted_fields & figure_fields


def test_a_written_line_says_it_was_written_and_not_quoted():
    report = report_with_both_passes([prose_upload()])
    for row in report["written"]:
        assert row["kind"] == "written, not quoted"
        assert row["text"]
        assert row["written_from"]


def test_a_quoted_value_carries_the_span_it_was_read_from():
    """So the count can be checked rather than believed, which is the whole
    complaint about the number it replaces."""
    report = report_with_both_passes([prose_upload()])
    for row in report["quoted"]:
        assert row["span"]
        assert row["document"] == "a-prose-document.md"


def test_a_figure_the_extraction_pass_filled_is_quoted_and_not_parsed():
    """No double counting, and the direction matters: a model's reading counted
    as a parser's is exactly the blur these three columns exist to prevent. The
    packet figure carries the base document either way, so `filled` cannot tell
    them apart on its own and the pass's own `merged` list is what decides."""
    import types

    from packet_assembly import REVENUE

    report = report_for(BASELINE_ONLY, OLDER_FULL_BASELINE)
    parsed_only = {row["document"]: row for row in report["counts"]}["doc0.md"]
    assert parsed_only["parsed"] >= 1

    packets = [assemble("OPP-1", BASELINE_ONLY, Document(name="doc0.md", kind="markdown")),
               assemble("OPP-1", OLDER_FULL_BASELINE, PAPER)]
    sources = [
        base_document.Source(name="doc0.md", kind="markdown", text=BASELINE_ONLY),
        base_document.Source(name="", kind=base_document.PAPER_KIND,
                             text=OLDER_FULL_BASELINE),
    ]
    pass_filled = provenance_report.build(
        base_document.merge_packets(packets), sources,
        extraction=types.SimpleNamespace(
            merged=(REVENUE,),
            records={REVENUE: (types.SimpleNamespace(
                span="stated in the document's prose", section="Current State"),)},
        ),
    )
    row = {count["document"]: count for count in pass_filled["counts"]}["doc0.md"]
    assert row["quoted"] == 1
    assert row["parsed"] == parsed_only["parsed"] - 1
    assert row["total"] == parsed_only["parsed"]


def test_a_run_with_no_model_legs_counts_none_rather_than_claiming_a_total():
    """The two lists are empty and the columns are zero, which is what a run
    with no passes actually did. `report_for` builds exactly that run."""
    report = report_for(BASELINE_ONLY, OLDER_FULL_BASELINE)
    assert report["quoted"] == []
    assert report["written"] == []
    assert all(count["written"] == 0 for count in report["counts"])


def test_a_run_with_no_attachment_grows_no_panel_even_with_both_passes():
    """The quiet case, held against the change rather than assumed through it.
    Every field came from the paper, every reviewer knows it, and a panel saying
    so is a panel in the way."""
    assert report_with_both_passes() == {}


def test_a_refused_run_counts_the_model_legs_too():
    """The gate failure is where the panel matters most, and it is where the
    counting is easiest to get wrong: `_document` is never called on that path,
    so the pass's document half is never merged there unless the report asks for
    it itself. A refused run that quoted half a deck out of an attachment must
    not report that attachment at zero."""
    from test_base_document import SPARSE
    from test_base_document import request as live_request
    from test_live_seam import STAMP, StubClient
    from test_second_pass import extractor_returning, writer_returning

    # The sparse paper scores 4 of 6 and this attachment carries no figure any
    # parser reads, so the run is refused for the reason it has always been
    # refused — while the extraction pass reads its prose all the same.
    provider = LiveProposalProvider(
        StubClient(SPARSE), generated_at=STAMP,
        extractor=extractor_returning(platform_answer(PROSE_BASE)),
        writer=writer_returning(framing_answer(PROSE_BASE)),
    )
    envelope = provider.poll(provider.submit(
        live_request(SPARSE), uploads=[prose_upload()]))["envelope"]

    assert envelope["status"] == "error"
    assert envelope["error"]["code"] == "E_LOW_CONFIDENCE"
    report = envelope["error"]["details"]["provenance"]
    base = {count["document"]: count for count in report["counts"]}[
        "a-prose-document.md"]
    assert base["quoted"] >= 1
    assert base["written"] >= 1


def test_the_quoted_column_and_its_list_report_the_same_number():
    """They did not, and one thing described by two numbers that disagree is one
    number a reviewer has to decide is lying: the column said the PRD gave 6
    fields and the list under it said 39 values. Both count quotations now."""
    report = report_with_both_passes([prose_upload()])
    counted = {count["document"]: count for count in report["counts"]}
    quoted_here = [row for row in report["quoted"]
                   if row["document"] == "a-prose-document.md"]
    assert counted["a-prose-document.md"]["quoted"] == len(quoted_here)


# --- the record a saved deck keeps (step 5) ---------------------------------
#
# DECIDED by Antonio 2026-09-07: a saved deck keeps the extracted TEXT plus the
# filename, the kind, the size and a SHA-256 of the original bytes, and not the
# bytes. The model never saw the file, it read the text, so the text is the
# honest record of what produced a slide; the hash is what says two decks came
# from the same document, or that a changed version arrived under an unchanged
# name. `tests/test_attachment_record.py` holds the record itself. What is held
# here is the studio's half: it reaches the store, and reopening reaches it.

STORE_FILE = "decks.sqlite3"


@pytest.fixture
def saving_studio(tmp_path, monkeypatch):
    """The studio with a real deck file, a real store, and the REAL record.

    The pipeline is stubbed the way `studio` stubs it, but the record it returns
    is built by `attachment_record` out of the bytes the route actually read and
    the text `document_text` actually extracted — so what gets saved here is
    what a run saves, and only the legs between them are faked. That the
    provider produces this record on a live run is
    `tests/test_attachment_record.py`, not this fixture.
    """
    import attachment_record
    import document_text

    import app as ui_app

    deck_dir = tmp_path / "decks" / "any-client" / "claude code"
    deck_dir.mkdir(parents=True)
    deck = deck_dir / "output-1.html"
    deck.write_text("<html><body>a rendered deck</body></html>", encoding="utf-8")

    def _stub(deck_type, company, project, provider, **kwargs):
        uploads = kwargs.get("uploads") or ()
        documents = [document_text.extract_text(upload.data, upload.filename)
                     for upload in uploads]
        return dict(OK_RESULT, deck_path=str(deck),
                    attachments=attachment_record.build_all(uploads, documents))

    monkeypatch.setattr(ui_app, "generate_and_save_deck", _stub)
    monkeypatch.setattr(
        ui_app, "_live_provider",
        lambda: LiveProposalProvider(object(), generated_at="2026-08-15T00:00:00Z"),
    )
    monkeypatch.setattr(ui_app, "DECKS_ROOT", str(tmp_path / "decks"))
    monkeypatch.setenv("DECK_STORE_DIR", str(tmp_path))
    ui_app.app.testing = True
    ui_app._LAST_RESULT.clear()
    return ui_app.app.test_client(), str(deck), str(tmp_path / STORE_FILE)


def save_the_deck(client, deck_path):
    return client.post("/save-deck", data={
        "path": deck_path, "deck_type": "proposal",
        "company": "Any Client", "project": "Any Project", "packet": "",
    })


def saved_row(store_path):
    from deck_store import get_deck, list_decks

    rows = list_decks(store_path=store_path)
    assert rows, "expected the save to have written a row"
    return get_deck(rows[0]["id"], store_path=store_path)


def run_and_save(client, deck_path, store_path, uploads=None):
    live_run(client, uploads=uploads)
    save_the_deck(client, deck_path)
    return saved_row(store_path)


def test_a_saved_deck_keeps_the_record_of_what_it_was_built_from(saving_studio):
    import attachment_record

    client, deck_path, store_path = saving_studio
    row = run_and_save(client, deck_path, store_path,
                       uploads=[attachment(SCENARIOS_ONLY, "a-document.md")])
    records = row["details"]["attachments"]
    assert len(records) == 1
    assert set(records[0]) == set(attachment_record.FIELDS)
    assert records[0]["filename"] == "a-document.md"
    assert records[0]["kind"] == "markdown"
    assert records[0]["size_bytes"] == len(SCENARIOS_ONLY.encode())
    assert records[0]["sha256"] == hashlib.sha256(SCENARIOS_ONLY.encode()).hexdigest()
    assert records[0]["text"].strip() == SCENARIOS_ONLY.strip()


def test_the_row_keeps_the_text_and_not_the_bytes(saving_studio):
    """The decision itself, at the only place it can actually be checked: the
    column, after a trip through `json.dumps`."""
    client, deck_path, store_path = saving_studio
    row = run_and_save(client, deck_path, store_path,
                       uploads=[attachment(SCENARIOS_ONLY, "a-document.md")])
    for record in row["details"]["attachments"]:
        for value in record.values():
            assert not isinstance(value, (bytes, bytearray))


def test_the_record_sits_in_details_beside_everything_else_the_row_keeps(
        saving_studio):
    """One JSON column, one more key in it. The store is one table on purpose
    (`src/deck_store.py`), so an attachment record is not a second table and not
    a column of its own."""
    client, deck_path, store_path = saving_studio
    row = run_and_save(client, deck_path, store_path,
                       uploads=[attachment(SCENARIOS_ONLY, "a-document.md")])
    assert "attachments" in row["details"]
    assert {"packet", "deck_path", "edits"} <= set(row["details"])


def test_the_records_are_saved_in_precedence_order(saving_studio):
    """The order is the meaning: the first document attached is the base the
    deck was written from, and a row that reordered them would say something
    false about which document that was."""
    client, deck_path, store_path = saving_studio
    row = run_and_save(client, deck_path, store_path, uploads=[
        attachment(SCENARIOS_ONLY, "attached-one.md"),
        attachment(BASELINE_ONLY, "attached-two.md"),
    ])
    assert [record["filename"] for record in row["details"]["attachments"]] == [
        "attached-one.md", "attached-two.md",
    ]


def test_a_deck_built_from_no_attachment_records_an_empty_list(saving_studio):
    """The truthful record of a run with nothing attached, which is every run
    that worked before attachments existed."""
    client, deck_path, store_path = saving_studio
    row = run_and_save(client, deck_path, store_path)
    assert row["details"]["attachments"] == []


def test_reopening_a_deck_with_no_attachment_grows_no_card(saving_studio):
    """Quiet on the same terms as the provenance panel: no attachment, no card,
    on every deck saved before attachments existed."""
    import app as ui_app

    client, deck_path, store_path = saving_studio
    row = run_and_save(client, deck_path, store_path)
    ui_app._LAST_RESULT.clear()
    body = client.get("/deck-view",
                      query_string={"id": row["id"]}).get_data(as_text=True)
    assert "What this deck was built from" not in body


def test_a_row_saved_before_the_record_existed_still_opens(saving_studio):
    """Every field in `details` was added to it at some point, and a row that
    predates one simply has no key. Nothing here may turn that into a 500."""
    import app as ui_app
    from deck_store import get_deck, save_deck as save_to_store

    client, _deck_path, store_path = saving_studio
    deck_id = save_to_store("proposal", "Any Client", "Any Project",
                            "<html><body>an older deck</body></html>",
                            details={"packet": ""}, store_path=store_path)
    assert "attachments" not in get_deck(deck_id, store_path=store_path)["details"]
    ui_app._LAST_RESULT.clear()
    response = client.get("/deck-view", query_string={"id": deck_id})
    assert response.status_code == 200
    assert "What this deck was built from" not in response.get_data(as_text=True)


# --- the whole way through, with no stub between ----------------------------


@pytest.fixture
def real_studio(tmp_path, monkeypatch):
    """The app with the REAL pipeline and a stub MCP client behind the provider.

    Nothing between the form and the panel is faked here: the route reads the
    bytes, the provider extracts them, `base_document` merges, `provenance_report`
    builds and the panel renders. The only stub is the platform itself, which is
    a stub everywhere in this suite (`data-provider/CLAUDE.md` rule 7).
    """
    import app as ui_app
    import deck_generator
    from test_live_seam import CLIENTS, STAMP, StubClient

    # Bind the REAL pipeline explicitly rather than trusting that nothing has
    # replaced it. `tests/test_ui_review_surface.py` assigns its stub straight
    # onto the module (`ui_app.generate_and_save_deck = ...`) instead of through
    # monkeypatch, so the stub outlives that file and any later test that wants
    # the real thing silently gets a fixed envelope back. Asserting what this
    # fixture runs against is also the honest thing for a test whose whole claim
    # is that nothing is stubbed between the form and the panel.
    monkeypatch.setattr(ui_app, "generate_and_save_deck",
                        deck_generator.generate_and_save_deck)

    spec = dict(CLIENTS["one"], shape="sparse-no-scenario-table")
    monkeypatch.setattr(
        ui_app, "_live_provider",
        lambda: LiveProposalProvider(StubClient(spec), generated_at=STAMP),
    )
    monkeypatch.setattr(ui_app, "DECKS_ROOT", str(tmp_path))
    monkeypatch.setattr(ui_app, "PROMPTS_ROOT", str(tmp_path / "prompts"))
    ui_app.app.testing = True
    ui_app._LAST_RESULT.clear()
    return ui_app.app.test_client(), spec


def test_the_same_run_with_no_attachment_says_nothing_about_sources(real_studio):
    """And it is refused for the reason it has always been refused, which is
    what makes the test above about the attachment rather than about the run."""
    client, spec = real_studio
    body = run_deck(client, data={
        "data_source": "live", "deck_type": "proposal",
        "company": spec["company"]["name"], "project": spec["project"]["name"],
        "opportunity_id": spec["opportunity"]["id"],
    }).get_data(as_text=True)

    assert "Which document this deck was written from" not in body
    assert "E_LOW_CONFIDENCE" in body


def test_an_unreadable_attachment_stops_the_run_and_says_why(real_studio):
    """Through the real provider, so the code and the sentence are the ones
    `document_text` wrote rather than any the studio composed."""
    client, spec = real_studio
    body = run_deck(client, data={
        "data_source": "live", "deck_type": "proposal",
        "company": spec["company"]["name"], "project": spec["project"]["name"],
        "opportunity_id": spec["opportunity"]["id"],
        "documents": [(io.BytesIO(b"not a spreadsheet either"), "attachment.xlsx")],
    }).get_data(as_text=True)

    assert "E_UNSUPPORTED_TYPE" in body
    assert ".pdf" in body
    assert "Which document this deck was written from" not in body
