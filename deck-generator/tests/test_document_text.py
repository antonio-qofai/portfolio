"""Tests for the uploaded-document text extraction leg (item 14, step 1).

Weighted towards the ugly cases on purpose. The happy path is one call per
format; the rest of this file is the file that is empty, the file that carries no
text, the file that is not what its name claims, the type we do not take, and the
parser that is not installed. Each has to come back as a named failure, because
the failure this module exists to prevent is a deck written from an empty string.

Every fixture here is built in-process rather than committed, so there is no
binary blob in the repo and no filename with meaning. The names are deliberately
generic and the content is invented: this module decides by extension and by
content, and a test that leaned on a real document's name would be testing
something the module is forbidden to do.
"""

import io
import zipfile

import pytest

import document_text
from document_text import (
    ACCEPTED_EXTENSIONS,
    ExtractedDocument,
    ExtractionFailure,
    extract_text,
    kind_for_filename,
)

pypdf = pytest.importorskip("pypdf")
docx = pytest.importorskip("docx")


PROSE = "Total fee of 480,000 dollars across a 14 week engagement."


def pdf_bytes(*lines):
    """A minimal one-page PDF whose content stream draws `lines`.

    Hand-assembled rather than written with a library, so the suite gains no
    dependency it does not already need to read a PDF, and so the "no text
    layer" case below can be the same construction minus the text.
    """
    body = b"BT /F1 12 Tf 72 720 Td "
    for line in lines:
        body += b"(" + line.encode("latin-1") + b") Tj 0 -16 Td "
    body += b"ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length "
        + str(len(body)).encode()
        + b" >>\nstream\n"
        + body
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(index).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
    start_xref = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n0000000000 65535 f \n"
    for offset in offsets:
        out += ("%010d 00000 n \n" % offset).encode()
    out += (
        b"trailer\n<< /Size "
        + str(len(objects) + 1).encode()
        + b" /Root 1 0 R >>\nstartxref\n"
        + str(start_xref).encode()
        + b"\n%%EOF\n"
    )
    return bytes(out)


def pdf_without_a_text_layer():
    """A structurally valid PDF with a page and no text on it, which is what a
    scan of a printed document looks like to a parser."""
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def password_protected_pdf():
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.encrypt("a password the reviewer did not give us")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def docx_bytes(paragraphs=(), table_rows=(), styled=()):
    """A .docx fixture. `styled` carries `(text, style)` pairs for item 25.

    `paragraphs` stays a plain tuple so every test written before item 25 builds
    the same bytes it always did.
    """
    document = docx.Document()
    for text in paragraphs:
        document.add_paragraph(text)
    for text, style in styled:
        document.add_paragraph(text, style=style)
    if table_rows:
        table = document.add_table(rows=len(table_rows), cols=len(table_rows[0]))
        for row, values in zip(table.rows, table_rows):
            for cell, value in zip(row.cells, values):
                cell.text = value
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------- the surface


def test_the_accepted_types_are_the_four_that_were_settled():
    assert set(ACCEPTED_EXTENSIONS) == {
        ".pdf",
        ".docx",
        ".md",
        ".markdown",
        ".txt",
        ".text",
    }
    kinds = {kind_for_filename("document" + ext) for ext in ACCEPTED_EXTENSIONS}
    assert kinds == {"pdf", "docx", "markdown", "text"}


def test_an_unaccepted_extension_has_no_kind():
    assert kind_for_filename("document.xlsx") == ""
    assert kind_for_filename("document") == ""


def test_both_outcomes_answer_ok_so_a_caller_never_branches_on_a_type():
    good = extract_text(PROSE.encode(), "document.txt")
    bad = extract_text(b"", "document.txt")
    assert good.ok is True
    assert bad.ok is False
    assert isinstance(good, ExtractedDocument)
    assert isinstance(bad, ExtractionFailure)


def test_a_failure_carries_the_four_fields_a_provider_error_takes():
    failure = extract_text(b"anything", "document.xlsx")
    assert failure.code and failure.message and failure.remediation
    assert isinstance(failure.details, dict)


def test_bytes_are_required_and_anything_else_is_a_call_site_bug():
    with pytest.raises(TypeError):
        extract_text(PROSE, "document.txt")
    with pytest.raises(TypeError):
        extract_text(None, "document.txt")


def test_a_bytes_like_buffer_is_accepted_because_a_reader_returns_one():
    result = extract_text(bytearray(PROSE.encode()), "document.txt")
    assert result.text == PROSE
    assert extract_text(memoryview(PROSE.encode()), "document.txt").text == PROSE


# ------------------------------------------------------------- the happy path


def test_plain_text_extracts():
    result = extract_text(PROSE.encode(), "document.txt")
    assert result.ok
    assert result.kind == "text"
    assert result.text == PROSE
    assert result.filename == "document.txt"
    assert result.characters == len(PROSE)


def test_markdown_extracts_and_keeps_its_markup():
    source = "# Heading\n\n- a bullet\n- another\n\n| Fee | 90,000 |\n"
    result = extract_text(source.encode(), "document.md")
    assert result.kind == "markdown"
    assert "# Heading" in result.text
    assert "| Fee | 90,000 |" in result.text
    assert "- a bullet" in result.text


def test_a_pdf_extracts_its_text():
    result = extract_text(pdf_bytes(PROSE), "document.pdf")
    assert result.ok
    assert result.kind == "pdf"
    assert "480,000" in result.text
    assert "14 week" in result.text


def test_a_docx_extracts_its_paragraphs():
    data = docx_bytes(paragraphs=("A heading", PROSE))
    result = extract_text(data, "document.docx")
    assert result.ok
    assert result.kind == "docx"
    assert "A heading" in result.text
    assert PROSE in result.text


def test_a_docx_table_extracts_because_the_figures_live_in_the_tables():
    data = docx_bytes(
        paragraphs=("Pricing",),
        table_rows=(("Line item", "Amount"), ("Build fee", "90,000")),
    )
    result = extract_text(data, "document.docx")
    assert "Line item | Amount" in result.text
    assert "Build fee | 90,000" in result.text


def test_a_docx_reads_in_document_order_so_a_table_stays_with_its_sentence():
    data = docx_bytes(
        paragraphs=("Before the table", "After the table"),
        table_rows=(("Build fee", "90,000"),),
    )
    text = extract_text(data, "document.docx").text
    assert text.index("Before the table") < text.index("Build fee")
    # `add_table` appends after both paragraphs, so this is the assertion that
    # would fail if the reader read `paragraphs` and `tables` as two lists.
    assert text.index("After the table") < text.index("Build fee")


def test_an_extension_is_read_case_insensitively():
    assert extract_text(PROSE.encode(), "DOCUMENT.TXT").kind == "text"
    assert extract_text(pdf_bytes(PROSE), "DOCUMENT.PDF").kind == "pdf"


def test_the_extracted_document_is_immutable():
    result = extract_text(PROSE.encode(), "document.txt")
    with pytest.raises(Exception):
        result.text = "something else"


# ------------------------------------------------------- normalisation, lightly


def test_windows_newlines_and_a_byte_order_mark_are_normalised_away():
    result = extract_text(b"\xef\xbb\xbffirst\r\nsecond\r\n", "document.txt")
    assert result.text == "first\nsecond"


def test_runs_of_blank_lines_collapse_and_the_document_is_trimmed():
    result = extract_text(b"\n\nfirst\n\n\n\n\nsecond   \n\n\n", "document.md")
    assert result.text == "first\n\nsecond"


def test_indentation_inside_the_document_survives_because_markdown_means_it():
    result = extract_text(b"- a bullet\n    - a nested bullet\n", "document.md")
    assert result.text == "- a bullet\n    - a nested bullet"


def test_a_utf8_document_keeps_its_characters():
    source = "Fee of 480.000 €, ~12% margin, café rollout"
    result = extract_text(source.encode("utf-8"), "document.txt")
    assert result.text == source


def test_a_windows_encoded_document_still_reads():
    source = "Fee of 480.000 € across the engagement"
    result = extract_text(source.encode("cp1252"), "document.txt")
    assert "480.000" in result.text
    assert result.ok


# ------------------------------------------------------------ the empty file


@pytest.mark.parametrize("filename", ["document.txt", "document.md", "document.pdf", "document.docx"])
def test_an_empty_file_is_a_stated_failure_for_every_type(filename):
    failure = extract_text(b"", filename)
    assert failure.code == "E_EMPTY_FILE"
    assert not failure.ok
    assert failure.remediation


def test_a_whitespace_only_text_file_is_a_failure_and_not_an_empty_string():
    failure = extract_text(b"   \n\n\t\n  ", "document.txt")
    assert failure.code == "E_NO_TEXT"
    assert not hasattr(failure, "text")


# ------------------------------------------- the file whose text will not extract


def test_a_pdf_with_no_text_layer_is_a_stated_failure():
    failure = extract_text(pdf_without_a_text_layer(), "document.pdf")
    assert failure.code == "E_NO_TEXT"
    assert failure.details["kind"] == "pdf"
    assert "text" in failure.remediation


def test_a_docx_with_no_content_is_a_stated_failure():
    failure = extract_text(docx_bytes(), "document.docx")
    assert failure.code == "E_NO_TEXT"
    assert failure.details["kind"] == "docx"


def test_a_docx_of_empty_paragraphs_is_a_stated_failure():
    failure = extract_text(docx_bytes(paragraphs=("", "   ", "")), "document.docx")
    assert failure.code == "E_NO_TEXT"


def test_a_password_protected_pdf_says_so_rather_than_saying_corrupt():
    failure = extract_text(password_protected_pdf(), "document.pdf")
    assert failure.code == "E_ENCRYPTED_FILE"
    assert "password" in failure.remediation.lower()


# ------------------------------------------------- the unsupported type


@pytest.mark.parametrize("filename", ["document.xlsx", "document.pptx", "document.csv", "document.zip"])
def test_a_type_we_do_not_take_is_refused_by_name(filename):
    failure = extract_text(b"whatever these bytes are", filename)
    assert failure.code == "E_UNSUPPORTED_TYPE"
    assert failure.details["accepted"] == list(ACCEPTED_EXTENSIONS)


def test_a_word_processor_format_we_do_not_take_says_what_to_do_instead():
    failure = extract_text(b"whatever", "document.doc")
    assert failure.code == "E_UNSUPPORTED_TYPE"
    assert ".docx" in failure.remediation


def test_a_google_doc_is_refused_with_the_export_step_named():
    failure = extract_text(b"whatever", "document.gdoc")
    assert failure.code == "E_UNSUPPORTED_TYPE"
    assert "xport" in failure.remediation


def test_a_file_with_no_extension_is_refused_because_its_type_is_unknown():
    failure = extract_text(b"whatever", "document")
    assert failure.code == "E_UNSUPPORTED_TYPE"
    assert failure.details["extension"] == ""


def test_an_upload_with_no_filename_is_refused_before_anything_is_read():
    failure = extract_text(b"whatever", "")
    assert failure.code == "E_UNNAMED_FILE"
    assert extract_text(b"whatever", None).code == "E_UNNAMED_FILE"
    assert extract_text(b"whatever", "   ").code == "E_UNNAMED_FILE"


def test_the_unsupported_check_runs_before_the_empty_check():
    # An empty file of a type we do not take is refused for its type, because
    # that is the sentence that tells the reviewer something they can act on.
    assert extract_text(b"", "document.xlsx").code == "E_UNSUPPORTED_TYPE"


# --------------------------------------- the file that is not what it claims


def test_a_pdf_named_docx_says_it_is_a_pdf():
    failure = extract_text(pdf_bytes(PROSE), "document.docx")
    assert failure.code == "E_TYPE_MISMATCH"
    assert failure.details["content_kind"] == "pdf"
    assert failure.details["kind"] == "docx"


def test_a_docx_named_pdf_says_it_is_a_word_document():
    failure = extract_text(docx_bytes(paragraphs=(PROSE,)), "document.pdf")
    assert failure.code == "E_TYPE_MISMATCH"
    assert failure.details["content_kind"] == "docx"


def test_a_pdf_renamed_to_markdown_is_caught_too():
    failure = extract_text(pdf_bytes(PROSE), "document.md")
    assert failure.code == "E_TYPE_MISMATCH"
    assert failure.details["content_kind"] == "pdf"


def test_plain_text_named_pdf_says_it_is_plain_text():
    failure = extract_text(PROSE.encode(), "document.pdf")
    assert failure.code == "E_TYPE_MISMATCH"
    assert failure.details["content_kind"] == "text"


def test_binary_that_is_no_format_we_know_is_unreadable_rather_than_mismatched():
    failure = extract_text(b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4, "document.pdf")
    assert failure.code == "E_UNREADABLE_FILE"
    assert failure.details["kind"] == "pdf"


def test_a_truncated_pdf_is_unreadable():
    failure = extract_text(pdf_bytes(PROSE)[:60], "document.pdf")
    assert failure.code == "E_UNREADABLE_FILE"


def test_a_zip_that_is_not_a_docx_is_unreadable():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("something.txt", "not an office document")
    failure = extract_text(buffer.getvalue(), "document.docx")
    assert failure.code == "E_UNREADABLE_FILE"


def test_binary_bytes_named_txt_are_refused_rather_than_decoded_as_gibberish():
    # latin-1 would decode this happily, which is exactly why it is not a
    # fallback: the reviewer would get a deck written from mojibake.
    failure = extract_text(bytes(range(256)) * 8, "document.txt")
    assert failure.code == "E_UNREADABLE_FILE"


def test_text_carrying_null_bytes_is_refused():
    failure = extract_text(b"first\x00second", "document.md")
    assert failure.code == "E_UNREADABLE_FILE"


def test_a_parser_message_never_reaches_the_failure_because_it_quotes_the_file():
    # pypdf's own message for this is "invalid pdf header: b'<the bytes>'". The
    # cause is named by class, per the rule the 2026-09-03 entry set after h11
    # was found putting an API key into an error string.
    secret = "a-line-a-reviewer-would-not-want-in-the-store"
    failure = extract_text(secret.encode() + b"\x00\xff", "document.pdf")
    assert failure.code == "E_UNREADABLE_FILE"
    assert secret not in failure.message
    assert secret not in str(failure.details)
    assert failure.details["cause"].endswith("Error")


# ---------------------------------------------- the parser that is not installed


def test_a_missing_pdf_parser_is_a_stated_failure_and_not_an_import_error(monkeypatch):
    monkeypatch.setattr(
        document_text,
        "_read_pdf",
        lambda data, name, extension, kind: (
            "",
            document_text._dependency_failure("pypdf", name, extension, kind),
        ),
    )
    failure = extract_text(pdf_bytes(PROSE), "document.pdf")
    assert failure.code == "E_EXTRACT_DEPENDENCY"
    assert failure.details["package"] == "pypdf"
    assert "requirements.txt" in failure.remediation


def test_the_dependency_failure_names_the_package_for_each_binary_format():
    for package, kind in (("pypdf", "pdf"), ("python-docx", "docx")):
        failure = document_text._dependency_failure(
            package, "document." + kind, "." + kind, kind
        )
        assert failure.code == "E_EXTRACT_DEPENDENCY"
        assert package in failure.message
        assert failure.details["package"] == package


def test_importing_this_module_does_not_import_a_parser():
    # The parsers are imported inside the leg that needs them, the way
    # `anthropic` and `flask` are, so the prompt pipeline pays nothing for a
    # dependency it never reaches.
    source = open(document_text.__file__).read()
    header = source.split("def extract_text", 1)[0]
    assert "import pypdf" not in header
    assert "import docx" not in header


# ------------------------------------------------------------ no hardcoding


def test_nothing_in_the_module_names_a_client_a_project_or_a_document():
    source = open(document_text.__file__).read().lower()
    for token in ("fabrikam", "wtg", "northwind", "casey", "prd.pdf", "contract.pdf"):
        assert token not in source


def test_the_same_bytes_extract_the_same_under_any_filename():
    data = docx_bytes(paragraphs=(PROSE,))
    first = extract_text(data, "one-name.docx")
    second = extract_text(data, "an-entirely-different-name.docx")
    assert first.text == second.text
    assert first.filename != second.filename


# ------------------------------------- item 25, an uploaded document keeps its
# structure. Word records which paragraphs are headings and which blocks are
# tables; rendering `.text` alone threw both away, so a 50,000-character PRD
# reached `paper_writing.headings` with zero sections in it and its tables
# reached the table parsers as prose. These assert the two things downstream
# needs, against the real helpers rather than against a restated shape, because
# the defect was precisely that the old output LOOKED like a table and was not.


def test_a_word_heading_arrives_as_a_markdown_heading():
    import paper_writing

    data = docx_bytes(styled=(("1. Executive Summary", "Heading 1"),))
    text = extract_text(data, "document.docx").text
    assert "# 1. Executive Summary" in text
    found = paper_writing.headings(text)
    assert [(level, title) for level, title, _s, _e in found] == [
        (1, "1. Executive Summary")
    ]


def test_heading_depth_survives_so_a_nested_section_resolves_under_its_parent():
    import paper_writing

    data = docx_bytes(styled=(
        ("1. Executive Summary", "Heading 1"),
        ("1.1 Opportunity at a Glance", "Heading 2"),
        ("2. Background", "Heading 1"),
    ))
    text = extract_text(data, "document.docx").text
    assert [level for level, _t, _s, _e in paper_writing.headings(text)] == [1, 2, 1]
    # The nested path is the one the writing pass actually asks for.
    assert paper_writing.resolve_section(
        text, "1. Executive Summary > 1.1 Opportunity at a Glance",
        source=paper_writing.PAPER,
    ) is not None


def test_an_ordinary_paragraph_does_not_become_a_heading():
    """The guard on the other side: styling is what makes a heading, not looks."""
    import paper_writing

    data = docx_bytes(paragraphs=("Not a heading, just a line.", PROSE))
    text = extract_text(data, "document.docx").text
    assert paper_writing.headings(text) == []
    assert "#" not in text


def test_a_table_is_one_the_table_parsers_can_actually_see():
    """The load-bearing assertion. Both parsers collect a table by taking lines
    that start with `|`, so the old `" | ".join(...)` row was never a table row
    to them, which is why a PRD contributed zero figures from its tables."""
    import assumption_table_parser
    import scenario_table_parser

    data = docx_bytes(table_rows=(
        ("Scenario", "Annual EBITDA Impact", "Margin Uplift"),
        ("Conservative", "139,000", "2.5 pts"),
        ("Ambitious", "249,000", "4.5 pts"),
    ))
    text = extract_text(data, "document.docx").text
    readers = (
        ("assumption_table_parser", assumption_table_parser._tables),
        ("scenario_table_parser", scenario_table_parser.tables),
    )
    for name, read in readers:
        tables = read(text)
        assert len(tables) == 1, name
        table = tables[0]
        assert table.header == ("Scenario", "Annual EBITDA Impact", "Margin Uplift")
        # The separator row is dropped as a rule, so both data rows survive.
        assert len(table.rows) == 2, name
        assert table.rows[0].cells[0] == "Conservative"


def test_a_table_carries_a_separator_row_matching_its_header():
    data = docx_bytes(table_rows=(("Line item", "Amount"), ("Build fee", "90,000")))
    lines = [
        line for line in extract_text(data, "document.docx").text.splitlines()
        if line.strip()
    ]
    assert lines[0] == "| Line item | Amount |"
    assert lines[1] == "| --- | --- |"
    assert lines[2] == "| Build fee | 90,000 |"


def test_a_pipe_inside_a_cell_does_not_shift_the_columns_after_it():
    """A pipe in a cell must not push the figure beside it into another column,
    which is the silent version of the defect this item exists to fix."""
    import assumption_table_parser

    data = docx_bytes(table_rows=(
        ("Metric", "Range"),
        ("Margin | uplift", "2.5 to 4.5 pts"),
    ))
    text = extract_text(data, "document.docx").text
    table = assumption_table_parser._tables(text)[0]
    row = table.rows[0]
    assert len(row.cells) == len(table.header)
    # The figure stays in the column its header named.
    assert row.cells[1] == "2.5 to 4.5 pts"


def test_a_heading_deeper_than_markdown_goes_is_still_a_heading():
    import paper_writing

    data = docx_bytes(styled=(("Deeply nested", "Heading 7"),))
    text = extract_text(data, "document.docx").text
    levels = [level for level, _t, _s, _e in paper_writing.headings(text)]
    assert levels == [6]
