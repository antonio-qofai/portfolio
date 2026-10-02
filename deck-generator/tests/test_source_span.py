"""Tests for the source-span primitive (E5a).

These are adversarial on purpose. The module's whole claim is that a figure
without a span is impossible to construct rather than merely discouraged, so
most of what follows tries to build one anyway and asserts that it cannot.

The paper text here is invented and names no company, per the repo's
anti-hardcoding rule. Its only job is to be a source a span can occur in.
"""

import dataclasses

import pytest

from source_span import (
    PAPER,
    PAPER_FIELD_NAMES,
    PAPER_KIND,
    Answer,
    Conflict,
    Document,
    MissingDocumentError,
    MissingFields,
    MissingOpportunityError,
    MissingSpanError,
    Opportunity,
    SourcedFigure,
)

# The text. `PAPER` imported above is the published paper's DOCUMENT, which is
# the identity of this text rather than the text, and the two are deliberately
# not the same object: a figure carries both, one to verify a span against and
# one to say which document the span belongs to.
PAPER_TEXT = (
    "## Financial baseline\n\n"
    "| Metric | Value |\n"
    "| Current LTM Revenue | $41.3M |\n\n"
    "FY2025 adjusted EBITDA of approximately $18.7M.\n"
)

# The opportunity a reading is FOR, which joined the figure on 2026-09-13. Named
# here beside the text and the document for the same reason those two are named
# apart: a figure carries all three, and they answer three different questions.
OPPORTUNITY = Opportunity(id="OPP-ONE", title="An opportunity")


def test_a_figure_carries_its_value_the_span_and_the_document():
    figure = SourcedFigure("ltm_revenue", 41_300_000,
                           "| Current LTM Revenue | $41.3M |", PAPER_TEXT, PAPER,
                           OPPORTUNITY)
    assert figure.value == 41_300_000
    assert figure.span in PAPER_TEXT
    assert figure.document == PAPER
    assert figure.opportunity == OPPORTUNITY


def test_a_figure_cannot_be_constructed_without_a_span():
    with pytest.raises(TypeError):
        SourcedFigure("ltm_revenue", 41_300_000)


def test_a_figure_cannot_be_constructed_without_a_source_to_check_the_span_against():
    with pytest.raises(TypeError):
        SourcedFigure("ltm_revenue", 41_300_000, "| Current LTM Revenue | $41.3M |")


def test_a_figure_cannot_be_constructed_without_a_document():
    """The argument that joined on 2026-09-07, and it joined for the same reason
    the span is there: the span knew its text and not its document, which was
    true enough while every span in a packet came from the same place and
    stopped being true the moment a reviewer could attach one."""
    with pytest.raises(TypeError):
        SourcedFigure("ltm_revenue", 41_300_000,
                      "| Current LTM Revenue | $41.3M |", PAPER_TEXT)


@pytest.mark.parametrize("document", ["", "the paper", None, 41_300_000,
                                      ("a name", "a kind")])
def test_a_document_that_is_not_a_document_is_no_document_at_all(document):
    """A string naming a document is not a document. The type is the check,
    because a figure whose provenance is a bare string is a figure whose
    provenance nobody validated."""
    with pytest.raises(MissingDocumentError):
        SourcedFigure("ltm_revenue", 41_300_000,
                      "| Current LTM Revenue | $41.3M |", PAPER_TEXT, document,
                      OPPORTUNITY)


def test_a_figure_cannot_be_constructed_without_an_opportunity():
    """The argument that joined on 2026-09-13 (item 15), and it joined for the
    reason the document did one axis over: a run can now hold more than one
    opportunity, and an attached document is the base for every one of them, so
    "which document" stopped being enough to say which slide a figure belongs
    on."""
    with pytest.raises(TypeError):
        SourcedFigure("ltm_revenue", 41_300_000,
                      "| Current LTM Revenue | $41.3M |", PAPER_TEXT, PAPER)


@pytest.mark.parametrize("opportunity", ["", "an opportunity", None, 41_300_000,
                                         ("an id", "a title")])
def test_an_opportunity_that_is_not_an_opportunity_is_none_at_all(opportunity):
    """Same check and same reason as the document above. A bare string naming an
    opportunity is provenance nobody validated."""
    with pytest.raises(MissingOpportunityError):
        SourcedFigure("ltm_revenue", 41_300_000,
                      "| Current LTM Revenue | $41.3M |", PAPER_TEXT, PAPER,
                      opportunity)


@pytest.mark.parametrize("span", ["", "   ", "\n", None, 41_300_000])
def test_a_blank_or_non_text_span_is_no_span_at_all(span):
    with pytest.raises(MissingSpanError):
        SourcedFigure("ltm_revenue", 41_300_000, span, PAPER_TEXT, PAPER,
                      OPPORTUNITY)


def test_a_span_that_does_not_occur_in_the_source_is_refused():
    """The failure this guards is a figure with a plausible-looking span the
    paper never contained, which is fabrication wearing provenance."""
    with pytest.raises(MissingSpanError):
        SourcedFigure("ltm_revenue", 99_000_000, "LTM revenue of $99.0M",
                      PAPER_TEXT, PAPER, OPPORTUNITY)


def test_the_source_is_not_carried_into_equality_or_repr():
    """A packet must be able to serialize a figure without dragging 40KB of
    paper with it, and two figures agreeing on field, value, and span agree."""
    span = "FY2025 adjusted EBITDA of approximately $18.7M."
    one = SourcedFigure("ltm_ebitda", 18_700_000, span, PAPER_TEXT, PAPER,
                        OPPORTUNITY)
    two = SourcedFigure("ltm_ebitda", 18_700_000, span,
                        PAPER_TEXT + "\nappendix\n", PAPER, OPPORTUNITY)
    assert one == two
    assert PAPER_TEXT not in repr(one)


def test_the_document_is_carried_into_equality_and_repr_and_the_source_is_not():
    """The other half of the line above, and the reason the two fields differ.
    Two figures with the same value from different documents are not the same
    figure, and that difference is the whole of what the studio has to show."""
    span = "FY2025 adjusted EBITDA of approximately $18.7M."
    attached = Document(name="a-name.md", kind="markdown")
    one = SourcedFigure("ltm_ebitda", 18_700_000, span, PAPER_TEXT, PAPER,
                        OPPORTUNITY)
    two = SourcedFigure("ltm_ebitda", 18_700_000, span, PAPER_TEXT, attached,
                        OPPORTUNITY)
    assert one != two
    assert "markdown" in repr(two)


def test_the_opportunity_is_carried_into_equality_and_repr_too():
    """THE ITEM 15 CASE, and it is the one the cache would otherwise get wrong.
    One document read for two opportunities produces two readings, not one, so
    two figures identical in every other respect are not equal."""
    span = "FY2025 adjusted EBITDA of approximately $18.7M."
    attached = Document(name="a-prd.md", kind="markdown")
    other = Opportunity(id="OPP-TWO", title="Another opportunity")
    one = SourcedFigure("ltm_ebitda", 18_700_000, span, PAPER_TEXT, attached,
                        OPPORTUNITY)
    two = SourcedFigure("ltm_ebitda", 18_700_000, span, PAPER_TEXT, attached,
                        other)
    assert one != two
    assert "OPP-TWO" in repr(two)


def test_a_figure_cannot_be_edited_into_an_unsourced_one_after_construction():
    figure = SourcedFigure("ltm_ebitda", 18_700_000, "$18.7M", PAPER_TEXT, PAPER,
                           OPPORTUNITY)
    with pytest.raises(dataclasses.FrozenInstanceError):
        figure.value = 99_000_000
    with pytest.raises(MissingSpanError):
        dataclasses.replace(figure, span="invented")
    with pytest.raises(MissingDocumentError):
        dataclasses.replace(figure, document=None)
    with pytest.raises(MissingOpportunityError):
        dataclasses.replace(figure, opportunity=None)


# --- the document ------------------------------------------------------------


def test_the_published_paper_is_the_one_document_with_no_name():
    assert PAPER.kind == PAPER_KIND
    assert PAPER.name == ""
    assert PAPER.uploaded is False


def test_an_attachment_is_a_document_that_knows_it_was_uploaded():
    for kind in ("pdf", "docx", "markdown", "text"):
        document = Document(name="a-name", kind=kind)
        assert document.uploaded is True


def test_an_attachment_with_no_name_is_refused():
    """A reviewer told which of two documents filled a field cannot be told
    "the attachment" when there were two."""
    for name in ("", "   ", None):
        with pytest.raises(ValueError):
            Document(name=name, kind="pdf")


def test_a_document_with_no_kind_is_refused():
    for kind in ("", "   ", None):
        with pytest.raises(ValueError):
            Document(name="a-name", kind=kind)


def test_a_document_is_frozen_and_compares_by_what_it_is():
    document = Document(name="a-name.md", kind="markdown")
    with pytest.raises(dataclasses.FrozenInstanceError):
        document.name = "a-different-name.md"
    assert document == Document(name="a-name.md", kind="markdown")
    assert document != Document(name="a-name.md", kind="text")
    assert document != Document(name="another-name.md", kind="markdown")


def test_a_document_carries_no_text():
    """`SourcedFigure.source` already holds it, and a document that carried it
    again would be a second copy of a 40KB string on every figure in a packet."""
    assert [f.name for f in dataclasses.fields(Document)] == ["name", "kind"]


# --- the opportunity ---------------------------------------------------------


def test_an_opportunity_with_no_id_is_refused():
    """The id is the identity. An opportunity that cannot name itself would
    compare equal to every other one that could not, which is precisely the
    collision this record exists to prevent."""
    for bad in ("", "   ", None, 41_300_000):
        with pytest.raises(ValueError):
            Opportunity(id=bad)


def test_an_opportunity_may_have_no_title_and_still_name_itself():
    """A caller that knows only the id (`packet_assembly.assemble`'s own
    default) builds one with no title, the same way the published paper is the
    one document with no name."""
    assert Opportunity(id="OPP-ONE").title == ""
    assert Opportunity(id="OPP-ONE").label == "OPP-ONE"
    assert Opportunity(id="OPP-ONE", title="Field capture").label == "Field capture"


def test_an_opportunity_is_frozen_and_compares_by_what_it_is():
    opportunity = Opportunity(id="OPP-ONE", title="Field capture")
    with pytest.raises(dataclasses.FrozenInstanceError):
        opportunity.id = "OPP-TWO"
    assert opportunity == Opportunity(id="OPP-ONE", title="Field capture")
    assert opportunity != Opportunity(id="OPP-TWO", title="Field capture")


def test_an_opportunity_carries_no_description():
    """The one real record measured 2026-08-15 carries 1,772 characters of it,
    and a figure is not a place to keep a second copy of a long string. The
    description reaches the extraction pass as a prompt argument, where it is
    content rather than provenance."""
    assert [f.name for f in dataclasses.fields(Opportunity)] == ["id", "title"]


# --- what the reviewer is shown ----------------------------------------------


def test_a_conflict_holds_its_answers_in_precedence_order():
    attached = Document(name="a-name.md", kind="markdown")
    carried = Answer(document=attached, value=(1.0, 1.0), span="a span")
    aside = Answer(document=PAPER, value=(2.0, 2.0), span="another span")
    conflict = Conflict(field="baseline.revenue_ttm_usd",
                        answers=(carried, aside))
    assert conflict.carried is carried
    assert conflict.alternatives == (aside,)


def test_a_conflict_and_its_answers_are_frozen():
    answer = Answer(document=PAPER, value=1, span="a span")
    with pytest.raises(dataclasses.FrozenInstanceError):
        answer.value = 2
    conflict = Conflict(field="a.field", answers=(answer,))
    with pytest.raises(dataclasses.FrozenInstanceError):
        conflict.field = "another.field"


def test_an_absence_is_recorded_with_its_field_name_and_reason():
    missing = MissingFields()
    missing.record("scenario_cases", "no scenario table in this paper")
    assert missing.names == ["scenario_cases"]
    assert missing.entries == (("scenario_cases", "no scenario table in this paper"),)


def test_an_absence_cannot_be_recorded_without_a_reason():
    missing = MissingFields()
    with pytest.raises(TypeError):
        missing.record("scenario_cases")
    with pytest.raises(ValueError):
        missing.record("scenario_cases", "")
    with pytest.raises(ValueError):
        missing.record("", "no scenario table in this paper")
    assert missing.names == []


def test_recorded_absences_cannot_be_rewritten_through_the_readers():
    missing = MissingFields()
    missing.record("backlog", "no backlog line")
    missing.names.append("ltm_revenue")
    with pytest.raises(AttributeError):
        missing.entries = ()
    assert missing.names == ["backlog"]


def test_every_paper_field_name_is_a_non_empty_tuple_of_labels():
    """The parsers read this map rather than spelling labels themselves, so an
    empty entry would silently mean 'never look for this field'."""
    assert PAPER_FIELD_NAMES
    for field, labels in PAPER_FIELD_NAMES.items():
        assert isinstance(labels, tuple) and labels, field
        assert all(isinstance(label, str) and label.strip() for label in labels), field
