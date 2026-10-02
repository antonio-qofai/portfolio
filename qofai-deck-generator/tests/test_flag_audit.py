"""The flag audit tells a parser gap from a fact the document does not state.

Part A4 of `build-plan-phase4-flags-and-fit.md`. Two halves:

  * unit tests on synthetic §14s, which run everywhere
  * a CORPUS CHECK over every PRD in `PRDs Casey/`, which fails when the
    reader leaves a Next Steps week or owner empty that the document states.
    A PRD added to the folder later is covered without editing this file. The
    folder holds client documents and is not committed, so where it is absent
    the check skips and says why.
"""

import glob
import os
import sys

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import flag_audit
import prd_section_parsers
from document_text import extract_text

CORPUS = os.path.join(HERE, "..", "PRDs Casey")

SCHEDULED = """# 14. Next Steps

A one-week scoping sprint.

### Interviews (60 minutes each)

Head of Sales: pipeline walkthrough. Live screen-share of the pipeline.

Finance Lead: the margin model. Where the numbers live today.

### Data requests

Pipeline export, last 12 months. One row per deal.

Margin model. The current spreadsheet, with its inputs.

### Sprint schedule

| Day | Activity | Owner |
| --- | --- | --- |
| Day 1 | Data requests issued; interviews scheduled | QofAI · CEO |
| Days 2–3 | Head of Sales interview | QofAI · Head of Sales |
| Days 3–4 | Finance Lead interview | QofAI · Finance Lead |

# 15. Appendix
"""


def test_a_scheduled_step_with_no_week_is_a_parser_gap():
    records = [{"number": "01", "title": "Head of Sales: pipeline walkthrough",
                "owner": "Head of Sales", "week": ""}]
    finding, = flag_audit.audit_next_steps(SCHEDULED, records)
    assert (finding["field"], finding["status"]) == ("week", flag_audit.STATED)


def test_a_step_no_line_schedules_is_not_stated():
    records = [{"number": "09", "title": "Board sign-off", "owner": "Board",
                "week": ""}]
    finding, = flag_audit.audit_next_steps(SCHEDULED, records)
    assert finding["status"] == flag_audit.NOT_STATED


def test_a_collapsed_step_is_named_by_its_heading_noun():
    records = [{"number": "03", "title": "Two data requests", "owner": "",
                "week": ""}]
    findings = {f["field"]: f["status"]
                for f in flag_audit.audit_next_steps(SCHEDULED, records)}
    assert findings == {"week": flag_audit.STATED, "owner": flag_audit.STATED}


def test_a_day_token_is_not_evidence_of_an_owner():
    """The line that schedules a step names its day; it only names an owner
    when it is a table row with an owner cell or carries an Owner label."""
    text = SCHEDULED.replace("| Days 3–4 | Finance Lead interview | QofAI · Finance Lead |",
                             "Finance Lead meets on Days 3–4.")
    records = [{"number": "02", "title": "Finance Lead: the margin model",
                "owner": "", "week": ""}]
    # With no owner the subject is the title; "Finance Lead: the margin model"
    # is one part, and only its own item line names it.
    findings = {f["field"]: f["status"]
                for f in flag_audit.audit_next_steps(text, records)}
    assert findings["owner"] == flag_audit.NOT_STATED


def test_a_complete_step_is_never_audited():
    records = [{"number": "01", "title": "x", "owner": "CEO", "week": "DAY 1"}]
    assert flag_audit.audit_next_steps(SCHEDULED, records) == []


def test_a_role_matches_by_its_parts_but_never_by_a_loose_word():
    assert prd_section_parsers.role_parts("President & Owner") == ("President", "Owner")
    assert prd_section_parsers.names("President interview", "President")
    assert not prd_section_parsers.names("Presidential review", "President")


def _corpus():
    paths = sorted(glob.glob(os.path.join(CORPUS, "*.docx"))
                   + glob.glob(os.path.join(CORPUS, "*.pdf")))
    if not paths:
        pytest.skip(f"no PRDs at {CORPUS}; the corpus is client documents and "
                    "is not committed")
    return paths


@pytest.mark.parametrize("path", _corpus() if os.path.isdir(CORPUS) else [],
                         ids=os.path.basename)
def test_the_corpus_leaves_nothing_the_document_states_unread(path):
    text = extract_text(open(path, "rb").read(), path).text
    if not prd_section_parsers.is_prd(text):
        pytest.skip(f"{os.path.basename(path)} is not a QofAI PRD")
    missed = flag_audit.stated_but_not_read(
        text, prd_section_parsers.next_steps(text))
    assert missed == [], [f"{m['number']} {m['title']}: {m['field']}"
                          for m in missed]


def test_the_corpus_check_says_why_it_skipped():
    if not os.path.isdir(CORPUS):
        pytest.skip(f"no PRDs at {CORPUS}")
    assert _corpus()


# --- carried to the reviewer ------------------------------------------------------

# A THIRD LAYOUT, the one this audit is for: the days in prose, in no heading
# and no table, so the reader leaves the weeks empty and the audit says so.
UNREAD = SCHEDULED.split("### Sprint schedule")[0] + """### Timing

The Head of Sales interview runs on Days 2–3, and the Finance Lead's on Day 4.

# 15. Appendix
"""


def test_the_provider_reports_what_the_document_states_and_the_reader_missed():
    from base_document import Upload
    from live_proposal_provider import LiveProposalProvider
    from test_base_document import CLIENTS, STAMP, StubClient, request
    from test_document_routing import prd

    spec = CLIENTS["one"]
    provider = LiveProposalProvider(StubClient(spec), generated_at=STAMP)
    envelope = provider.poll(provider.submit(request(spec, floor=0.0), uploads=(
        Upload(filename="a.md", data=(prd() + UNREAD).encode("utf-8")),
    )))["envelope"]
    assert envelope["status"] == "ok", envelope
    missed = {(f["title"], f["field"]) for f in envelope["flag_audit"]}
    assert ("Head of Sales: pipeline walkthrough", "week") in missed
    assert all(f["status"] == flag_audit.STATED for f in envelope["flag_audit"])


def test_a_paper_run_carries_no_audit():
    from live_proposal_provider import LiveProposalProvider
    from test_base_document import CLIENTS, STAMP, StubClient, request

    spec = CLIENTS["one"]
    provider = LiveProposalProvider(StubClient(spec), generated_at=STAMP)
    envelope = provider.poll(provider.submit(request(spec, floor=0.0)))["envelope"]
    assert envelope["flag_audit"] == []


def test_the_reviewer_card_names_what_the_reader_missed(tmp_path):
    pytest.importorskip("flask")
    sys.path.insert(0, os.path.join(HERE, "..", "ui"))
    import test_ui_missing_value_cards as cards

    ui_app, client, deck_path = cards._studio(tmp_path, cards._read(cards.DECK_FIXTURE))
    finding = {"number": "02", "title": "CCO: approval gates", "field": "week",
               "status": flag_audit.STATED, "opportunity": "Onboarding"}
    stub = ui_app.generate_and_save_deck
    ui_app.generate_and_save_deck = lambda *a, **k: dict(stub(*a, **k), flag_audit=[finding])
    page = " ".join(cards._run(client).split())
    reviewer = next(c for c in page.split("<h2>")
                    if c.startswith("Values we expect you to supply"))
    assert "The document seems to state 1 of these, and our reader missed it." in reviewer
    assert "<li>Onboarding · step 02 CCO: approval gates: <code>week</code></li>" in reviewer


def test_the_card_says_nothing_when_the_audit_is_empty(tmp_path):
    pytest.importorskip("flask")
    sys.path.insert(0, os.path.join(HERE, "..", "ui"))
    import test_ui_missing_value_cards as cards

    ui_app, client, deck_path = cards._studio(tmp_path, cards._read(cards.DECK_FIXTURE))
    page = cards._run(client)
    assert 'class="flag-audit"' not in page
