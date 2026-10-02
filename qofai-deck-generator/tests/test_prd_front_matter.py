"""Tests for what a PRD says about itself, and which opportunity it names.

Two halves, failing for different reasons.

THE READ. The labelled front-matter block, in both shapes the corpus carries: a
.docx renders it as markdown table rows and a PDF as a label followed by
whitespace. The cases below are the four real QofAI PRDs' own front matter,
copied verbatim, so a template change in the next document shows up here rather
than on a deck.

THE MATCH. Which published opportunity a stated title names. The rule is exactly
one, and every case here is one of the five ways a looser rule gets it wrong:
the short label swallowed by a longer stated title, two labels that both match,
connector variance, a mid-word hit, and two labels that match exactly.

Run with: python3 tests/test_prd_front_matter.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import prd_front_matter as prd  # noqa: E402

# The four documents' front matter, verbatim. The .docx ones arrive as markdown
# table rows (item 25 keeps Word's tables); the PDF one as label-then-whitespace.
SAGE = """Product Requirements Document
AI Advisor
Pipeline Cockpit
for Contoso
| Prepared by | QofAI, Forward-Deployed Engineering |
| --- | --- |
| Prepared for | Contoso |
| Opportunity | Pipeline Cockpit (published Sep 4, 2026) |
"""

TIG = """Product Requirements Document
AI-Powered Quoting &
Margin Modeling Model
for Tailspin Industrial Group
| Prepared for | Tailspin Industrial Group (TIG) |
| Opportunity | AI-Powered Quoting & Margin Modeling Model Built on Quotebase Repair History (validated; not yet published) |
"""

FBK = """PRODUCT REQUIREMENTS DOCUMENT
Production Dashboard &
Mobile Field Application
for Fabrikam Marine, a Woodgrove Partners portfolio company
PREPARED BY  QofAI - Forward-Deployed Engineering
PREPARED FOR  Fabrikam Marine & Woodgrove Partners
VERSION  Draft v0.1
"""


def choice(label, ident):
    return {"id": ident, "label": label}


# --- the read ---------------------------------------------------------------

def test_a_docx_front_matter_row_yields_the_client_and_the_opportunity():
    got = prd.read(SAGE)
    assert got["client_candidates"][0] == "Contoso"
    assert got["opportunity"] == "Pipeline Cockpit"
    assert got["opportunity_note"] == "published Sep 4, 2026"
    assert got["opportunity_selectable"] is True


def test_a_pdf_front_matter_row_parses_the_same_way():
    got = prd.read(FBK)
    assert got["client_candidates"][0] == "Fabrikam Marine"
    assert got["opportunity"] == ""


def test_an_abbreviation_is_its_own_candidate():
    """Measured live 2026-09-20: "Tailspin Industrial Group" does not resolve on
    the platform and "TIG" does, so the bracketed form has to be tried."""
    got = prd.read(TIG)
    assert got["client_candidates"] == ["Tailspin Industrial Group", "TIG"]


def test_the_client_side_of_an_ampersand_row_is_tried_first():
    """The row joins the client and the PE firm, and the firm may be a registry
    record of its own. The subject line says which side is the client."""
    got = prd.read(FBK)
    assert got["client_candidates"][0] == "Fabrikam Marine"
    assert "Woodgrove Partners" in got["client_candidates"]
    assert got["client_candidates"].index("Fabrikam Marine") < \
        got["client_candidates"].index("Woodgrove Partners")


def test_an_unpublished_opportunity_is_reported_rather_than_selectable():
    """The platform lists published opportunities only, so this one cannot be
    picked. Saying so is the difference between a branch that stops and one
    that quietly builds the deck from a different opportunity's paper."""
    got = prd.read(TIG)
    assert got["opportunity"].startswith("AI-Powered Quoting")
    assert got["opportunity_note"] == "validated; not yet published"
    assert got["opportunity_selectable"] is False


def test_a_document_that_states_nothing_yields_empty_rather_than_absent():
    got = prd.read("Some prose with no front matter at all.")
    assert got["client_candidates"] == []
    assert got["opportunity"] == ""
    assert got["opportunity_selectable"] is False


# --- the match --------------------------------------------------------------

def test_an_exact_title_selects():
    listed = [choice("Pipeline Cockpit", "A"),
              choice("Compensation & Incentive Analytics", "B")]
    assert prd.match_opportunity("Pipeline Cockpit", listed)["id"] == "A"


def test_an_exact_match_outranks_a_label_swallowed_by_the_stated_title():
    """Without the tiers this is ambiguous, and it is not: one of the two is the
    title the document wrote down."""
    listed = [choice("Cockpit", "A"),
              choice("Pipeline Cockpit for Growth", "B")]
    got = prd.match_opportunity("Pipeline Cockpit for Growth", listed)
    assert got["id"] == "B"


def test_two_labels_matching_the_same_stated_title_select_nothing():
    """The rule this module exists for. Taking the first hit broke the tie on
    `published_at`, because that is the order the list arrives in when no
    project name is given, and the opportunity supplies the research paper: the
    wrong one is the least visible kind of wrong."""
    listed = [choice("Quoting Margin Modeling", "A"),
              choice("Quoting Margin Model", "B")]
    assert prd.match_opportunity("Quoting Margin", listed) is None


def test_two_labels_matching_exactly_select_nothing_either():
    listed = [choice("Same Title", "A"), choice("Same  title", "B")]
    assert prd.match_opportunity("Same Title", listed) is None


def test_a_connector_is_not_a_difference():
    """"&" against "and" is the same title, and a character test misses it."""
    listed = [choice("Quoting and Margin Modeling", "A")]
    assert prd.match_opportunity("Quoting & Margin Modeling", listed)["id"] == "A"


def test_a_mid_word_hit_is_not_a_match():
    """A character substring test reads "Quoting" inside "Requoting"."""
    listed = [choice("Requoting Automation", "A")]
    assert prd.match_opportunity("Quoting", listed) is None


def test_the_matches_can_be_listed_so_a_reviewer_can_pick():
    listed = [choice("Quoting Margin Modeling", "A"),
              choice("Quoting Margin Model", "B"),
              choice("Something Else", "C")]
    ids = [c["id"] for c in prd.opportunity_matches("Quoting Margin", listed)]
    assert ids == ["A", "B"]


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"PASS  {test.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL  {test.__name__}: {e}")
    print(f"\n{len(tests) - failures} of {len(tests)} passed")
    sys.exit(1 if failures else 0)
