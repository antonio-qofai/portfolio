"""A PRD stops being recognised the moment it is not a .docx. It should not.

`prd-chain-pdf-FINDING.md` is the write-up. In short: `base_document.precedence`
drops the opportunity paper only when `is_prd` agrees the base document is a
PRD, and `is_prd` counted sections by looking for `^#{1,6} N.` — a MARKDOWN
heading. Those `#` characters come from the .docx converter, not from the
document, so the predicate was really asking "was this a .docx". A PDF
extraction of the same PRD carries the same words with no `#` anywhere, scored
zero, and let the paper silently back into the chain, which is the merge
Antonio's 2026-09-20 ruling exists to forbid.

The rule now: markdown first and unchanged, with a bare-numbered fallback that
can only fire on a document carrying no markdown headings at all.

Run with: python3 -m pytest tests/test_a_flat_prd_is_still_a_prd.py
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import base_document
import prd_section_parsers as psp

MARKDOWN_PRD = """# 1. Overview
Something about the engagement.

# 3. Goals & Success Metrics
Raise advisor throughput.

# 5. Current-State Workflow (As-Is)
Recruiters work from a spreadsheet.

# 6. Phased Scope
| Phase | Focus |
| --- | --- |
| Phase 1 Data Foundation Wks 1-3 | ingest |

# 7. Functional Requirements
The system shall do the thing.

# 12. Build Milestones & Rollout
Milestone one.

# 14. Next Steps
Sign the order form.
"""

FLAT_PRD = re.sub(r"^#{1,6}[ \t]*", "", MARKDOWN_PRD, flags=re.M)


def test_the_markdown_prd_is_recognised():
    assert psp.is_prd(MARKDOWN_PRD)


def test_the_same_prd_without_markdown_is_recognised_too():
    """THE REGRESSION. Same words, no `#`, which is what a PDF export yields."""
    assert psp.is_prd(FLAT_PRD), (
        "a PRD must be recognised by what it says, not by which converter "
        "happened to read it"
    )


def test_the_flat_prd_reads_the_same_sections():
    for number, _heading in psp.TEMPLATE_SECTIONS:
        assert psp.section(FLAT_PRD, number).strip(), (
            "section %d unreadable without markdown" % number
        )


def test_a_flat_section_ends_at_the_next_section():
    """Not at the end of the document, and not at a list item inside itself."""
    body = psp.section(FLAT_PRD, 6)
    assert "Phase 1 Data Foundation" in body
    assert "The system shall do the thing" not in body, "ran past §7"


def test_a_numbered_list_inside_a_section_is_not_a_heading():
    """The false positive the shape rules exist to stop. A restarting list must
    not end the section it sits in."""
    doc = FLAT_PRD.replace(
        "The system shall do the thing.",
        "The system shall:\n1. ingest the roster.\n2. score each advisor.\n"
        "3. publish a shortlist.")
    body = psp.section(doc, 7)
    assert "publish a shortlist" in body, (
        "a numbered list restarting at 1 must not cut its own section short"
    )
    assert psp.is_prd(doc)


def test_prose_that_merely_numbers_itself_is_not_a_prd():
    """`is_prd` still answers "is this the kind of document the rule is about".
    Numbering alone was never enough and still is not."""
    not_a_prd = "\n".join("%d. A paragraph about something else" % n
                          for n in range(1, 15))
    assert not psp.is_prd(not_a_prd)


def test_a_document_with_markdown_is_read_exactly_as_before():
    """The fallback must be unreachable for a markdown document, so no existing
    PRD changes behaviour. §9 is absent here and must stay absent rather than
    being found by the bare-numbered reader."""
    doc = MARKDOWN_PRD + "\n9. This line is not a markdown heading\nbody\n"
    assert psp.section(doc, 9) == ""


# --- and the whole point: the paper stays out of the chain ------------------

class _Doc:
    def __init__(self, text, filename):
        self.text, self.filename, self.kind = text, filename, "pdf"


def _chain_names(paper, documents):
    return [getattr(s, "name", getattr(s, "label", str(s)))
            for s in base_document.precedence(paper, documents)]


def test_a_flat_prd_keeps_the_paper_out_of_the_chain():
    """THE CONSEQUENCE, and the reason this is worth a file of its own. Before
    the fix the paper rejoined here, silently, and the run became a merge."""
    sources = base_document.precedence("the opportunity paper text",
                                       [_Doc(FLAT_PRD, "Client_PRD.pdf")])
    assert len(sources) == 1, (
        "a PRD chain drops the paper; a flat PRD is still a PRD chain"
    )


def test_a_non_prd_attachment_still_keeps_the_paper():
    """The gate that must not widen. A supporting note is not a PRD and has
    never displaced the paper."""
    note = "Some notes from the call.\nNothing structured about them."
    sources = base_document.precedence("the opportunity paper text",
                                       [_Doc(note, "notes.pdf")])
    assert len(sources) == 2, "only a PRD drops the paper"
