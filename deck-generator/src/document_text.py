"""Text out of an uploaded document (item 14, step 1): bytes in, text or a
stated failure out.

The extraction pass reads a base document with a model rather than with a strict
parser, so an uploaded PRD substitutes for the published opportunity paper by
being handed to that pass as text. This module is the only thing between a
reviewer's file and that text, and its whole job is to produce either text worth
extracting from or a failure a reviewer can act on. It never returns an empty
string and calls it success: a document that carries no text is a stated failure,
because the alternative is a deck silently written from nothing.

Pure. No UI, no provider, no store, no network, and nothing about any one client,
project, or document name. `extract_text(data, filename)` is the whole surface,
and it decides by extension and by content, never by recognizing a file.

WHAT IS NOT HERE, DELIBERATELY.

  - Size validation. A ceiling on how large an upload may be is a studio
    question, because the answer is a form rejection with a message, and the
    studio is where the form lives. This module extracts whatever it is given.
  - The extraction pass's own token bound. A larger base document means more
    input tokens on that pass; that bound is measured in `src/model_call.py`
    against the leg that spends it (see the 2026-09-02 and 2026-09-03 CHANGELOG
    entries on headroom), not guessed at here.
  - Any store schema. Whether a saved deck keeps the original bytes or only this
    text is not this module's call, and nothing here forecloses either.

TWO THINGS THE FAILURES DO ON PURPOSE.

  1. A cause is named by CLASS, never by its text. A parser's message quotes the
     bytes it choked on verbatim (`pypdf` on a mislabelled file: "invalid pdf
     header: b'just '"), and a failure from here reaches the reviewer's panel and
     the deck store. That is the same rule the 2026-09-03 entry set for
     `ModelCallError` after h11 was found putting an API key into an error
     string, and it holds for the same reason.
  2. A parser dependency that is not installed is a stated failure, not an
     ImportError. `pypdf` and `python-docx` are imported inside the leg that
     needs them, the way `anthropic` and `flask` are, so importing this module
     costs nothing and a missing wheel names itself.

The failure fields are `code`, `message`, `remediation`, `details`, which is
exactly what `live_proposal_provider.ProviderError` takes, so the provider leg
builds a contract error envelope straight off a failure rather than re-deriving
one.
"""

import dataclasses
import os
import re


# The accepted types, in one place, so the studio's file input and its
# validation both read from here rather than restating a list. Markdown and
# plain text cost nothing; PDF and docx each bring a parser (see
# `requirements.txt`, where both are ceilinged).
KIND_PDF = "pdf"
KIND_DOCX = "docx"
KIND_MARKDOWN = "markdown"
KIND_TEXT = "text"

_EXTENSION_KINDS = {
    ".pdf": KIND_PDF,
    ".docx": KIND_DOCX,
    ".md": KIND_MARKDOWN,
    ".markdown": KIND_MARKDOWN,
    ".txt": KIND_TEXT,
    ".text": KIND_TEXT,
}

ACCEPTED_EXTENSIONS = tuple(sorted(_EXTENSION_KINDS))

# Extensions a reviewer plausibly reaches for that we do not accept, each with
# the one sentence that turns a refusal into a next step. A word processor's own
# format is a save-as away from one we take; a Google Doc is an export away, and
# that export step is the open risk part two names against this item.
_UNSUPPORTED_HINTS = {
    ".doc": "Open it and save it as .docx.",
    ".rtf": "Open it and save it as .docx.",
    ".odt": "Open it and save it as .docx.",
    ".pages": "Export it as PDF or Word from Pages.",
    ".gdoc": "Export the Google Doc as PDF or Word, then upload the export.",
}

# Leading bytes that identify a format regardless of what a file is called, so a
# renamed file is told it is renamed instead of being told it is corrupt.
_MAGIC = (
    (b"%PDF-", KIND_PDF),
    (b"PK\x03\x04", KIND_DOCX),
)

_KIND_LABELS = {
    KIND_PDF: "a PDF",
    KIND_DOCX: "a Word document",
    KIND_MARKDOWN: "markdown",
    KIND_TEXT: "plain text",
}


@dataclasses.dataclass(frozen=True)
class ExtractedDocument:
    """Text that came out of a named document, with the kind it came out of.

    `text` is non-empty and not merely whitespace; an extraction that produced
    nothing is an `ExtractionFailure`, so a caller holding one of these holds
    something worth extracting figures from. `filename` and `kind` are carried
    because the leg that names which document filled which field needs them
    (`src/source_span.py`), and because the attachment record needs them.
    """

    filename: str
    kind: str
    text: str

    ok = True

    @property
    def characters(self):
        return len(self.text)


@dataclasses.dataclass(frozen=True)
class ExtractionFailure:
    """A refusal a reviewer can act on: what happened, and what to do about it.

    The four fields match `live_proposal_provider.ProviderError`'s arguments on
    purpose. `details` never carries a parser's own message, only facts about the
    file: its name, its extension, the kind we expected, the class of the cause.
    """

    code: str
    message: str
    remediation: str
    details: dict = dataclasses.field(default_factory=dict)

    ok = False


def kind_for_filename(filename):
    """The kind an extension claims, or "" for anything we do not accept."""
    return _EXTENSION_KINDS.get(_extension(filename), "")


def extract_text(data, filename):
    """Text out of `data`, named `filename`, or the reason there is none.

    Returns an `ExtractedDocument` or an `ExtractionFailure`; both answer `ok`,
    so a caller branches on that rather than on a type. `data` must be bytes:
    handing this function something else is a call-site bug and raises
    `TypeError` the way `source_span.SourcedFigure` does, because a wrong type
    there is not a document a reviewer can fix.
    """
    if isinstance(data, (bytearray, memoryview)):
        data = bytes(data)
    if not isinstance(data, bytes):
        raise TypeError(
            f"extract_text needs the file's bytes, got {type(data).__name__}."
        )

    name = (filename or "").strip()
    if not name:
        return ExtractionFailure(
            "E_UNNAMED_FILE",
            "An upload arrived with no filename, so its type cannot be read.",
            "Re-attach the document from the file picker.",
            {"bytes": len(data)},
        )

    extension = _extension(name)
    kind = _EXTENSION_KINDS.get(extension)
    if kind is None:
        return _unsupported(name, extension)

    if not data:
        return ExtractionFailure(
            "E_EMPTY_FILE",
            f"{name!r} is empty, so there is nothing to read from it.",
            "Check the file opens on your own machine, then re-attach it.",
            {"filename": name, "extension": extension, "kind": kind},
        )

    mismatch = _mismatch(data, kind)
    if mismatch is not None:
        return ExtractionFailure(
            "E_TYPE_MISMATCH",
            f"{name!r} is named {extension} but its content is "
            f"{_KIND_LABELS[mismatch]}.",
            f"Rename it to match its content, or re-export it as "
            f"{_KIND_LABELS[kind]}.",
            {
                "filename": name,
                "extension": extension,
                "kind": kind,
                "content_kind": mismatch,
            },
        )

    if kind == KIND_PDF:
        text, failure = _read_pdf(data, name, extension, kind)
    elif kind == KIND_DOCX:
        text, failure = _read_docx(data, name, extension, kind)
    else:
        text, failure = _read_plain(data, name, extension, kind)
    if failure is not None:
        return failure

    text = _normalize(text)
    if not text:
        return ExtractionFailure(
            "E_NO_TEXT",
            f"{name!r} was read but carries no text to extract from.",
            "A scanned or image-only document has no text layer; supply a "
            "version whose text can be selected, or attach a different "
            "document.",
            {"filename": name, "extension": extension, "kind": kind},
        )
    return ExtractedDocument(filename=name, kind=kind, text=text)


def _extension(filename):
    return os.path.splitext((filename or "").strip())[1].lower()


def _unsupported(name, extension):
    if not extension:
        message = f"{name!r} has no file extension, so its type cannot be read."
        remediation = (
            "Rename it with the extension of its format, one of "
            f"{', '.join(ACCEPTED_EXTENSIONS)}."
        )
    else:
        message = f"{extension} is not a type this accepts."
        remediation = _UNSUPPORTED_HINTS.get(
            extension,
            "Attach one of " + ", ".join(ACCEPTED_EXTENSIONS) + ".",
        )
    return ExtractionFailure(
        "E_UNSUPPORTED_TYPE",
        message,
        remediation,
        {
            "filename": name,
            "extension": extension,
            "accepted": list(ACCEPTED_EXTENSIONS),
        },
    )


def _mismatch(data, kind):
    """The kind `data` actually is, when that contradicts what `kind` claims.

    Only the two binary formats are identified positively, so this catches a
    renamed PDF or docx wherever it turns up, including under a .md or .txt name,
    and stays quiet about text, which has no header to check.
    """
    for prefix, content_kind in _MAGIC:
        # A PDF header is looked for in the opening bytes rather than only at
        # byte zero, because a real PDF sometimes carries a preamble and pypdf
        # reads it anyway; calling that file plain text would be a false
        # refusal of a document that works.
        found = data.startswith(prefix) or (
            content_kind == KIND_PDF and prefix in data[:1024]
        )
        if found:
            return None if content_kind == kind else content_kind
    if kind in (KIND_PDF, KIND_DOCX):
        return KIND_TEXT if _looks_textual(data) else None
    return None


def _looks_textual(data):
    if b"\x00" in data[:4096]:
        return False
    try:
        data[:4096].decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _read_pdf(data, name, extension, kind):
    import io

    try:
        import pypdf
    except ImportError:
        return "", _dependency_failure("pypdf", name, extension, kind)

    facts = {"filename": name, "extension": extension, "kind": kind}
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            return "", ExtractionFailure(
                "E_ENCRYPTED_FILE",
                f"{name!r} is password-protected, so its text cannot be read.",
                "Remove the password, or re-export the document without one, "
                "then re-attach it.",
                dict(facts),
            )
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as error:  # a parser refusing bytes, by class only
        return "", _unreadable(error, name, extension, kind)
    return "\n\n".join(pages), None


# A Word heading paragraph, by the style it carries. Matched on the style id
# ("Heading1") before the display name ("Heading 1") because the id stays English
# in a localized Word while the name does not, and matched by pattern rather than
# against a list of names so `Heading 4` needs no new entry. `Title` is the one
# named case: a document's own title is its shallowest heading and Word does not
# number it.
_HEADING_STYLE_RE = re.compile(r"^heading\s*(\d+)$", re.IGNORECASE)
_MARKDOWN_MAX_HEADING = 6


def _heading_level(style):
    """The markdown heading level a Word paragraph style means, or ``None``.

    Levels deeper than markdown can express are clamped to `######` rather than
    dropped, because a `Heading 7` is still a heading and losing it would put its
    text into whichever section preceded it.
    """
    if style is None:
        return None
    for candidate in (getattr(style, "style_id", None), getattr(style, "name", None)):
        text = (candidate or "").strip()
        if not text:
            continue
        if text.lower() == "title":
            return 1
        match = _HEADING_STYLE_RE.match(text)
        if match:
            return min(int(match.group(1)), _MARKDOWN_MAX_HEADING)
    return None


def _table_cell(text):
    """One cell's text, safe to sit between two pipes.

    Two characters cannot survive inside a cell. A newline would end the row, so
    it becomes a space. A literal pipe would open a column the header never
    declared, shifting every value after it in that row into the wrong column,
    so it becomes a slash.

    A MARKDOWN `\\|` ESCAPE WOULD NOT DO. Both table parsers split a row with
    `line.strip("|").split("|")` and neither honours a backslash, so an escaped
    pipe still opens a column for the consumers that actually read this text.
    Substituting is the option that keeps the figures in the columns they were
    written in, and a slash is what the separator in a cell already reads as.
    No cell in any document that prompted this carried one.
    """
    return " ".join((text or "").replace("|", "/").split())


def _table_rows(table):
    """A Word table as markdown rows, header first and then the separator.

    THE LEADING PIPE AND THE SEPARATOR ARE BOTH LOAD-BEARING, which is why this
    is not `" | ".join(...)`. `assumption_table_parser._tables` and
    `scenario_table_parser._tables` both collect a table by taking lines that
    start with `|`, so a row written without one is not a table row to them at
    all, and both then drop the separator via `_is_rule`. A Word PRD carries its
    figures in its tables, so a table that no parser recognises is a PRD whose
    economics cannot compete with the paper's.
    """
    rows = []
    for row in table.rows:
        cells = [_table_cell(cell.text) for cell in row.cells]
        if not cells:
            continue
        rows.append("| " + " | ".join(cells) + " |")
        if len(rows) == 1:
            rows.append("| " + " | ".join(["---"] * len(cells)) + " |")
    return rows


def _read_docx(data, name, extension, kind):
    import io

    try:
        import docx
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError:
        return "", _dependency_failure("python-docx", name, extension, kind)

    try:
        document = docx.Document(io.BytesIO(data))
        blocks = []
        # Walk the body in document order rather than reading `paragraphs` and
        # `tables` separately, because a PRD's figures live in its tables and a
        # table that lands at the end of the text is a table detached from the
        # sentence that introduced it.
        for child in document.element.body.iterchildren():
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "p":
                paragraph = Paragraph(child, document)
                text = paragraph.text
                # ITEM 25. Word already recorded which paragraphs are headings,
                # in the style. Rendering `.text` alone threw that away, so a
                # Heading 1 reading "1. Executive Summary" arrived as an ordinary
                # line and `paper_writing.headings`, which matches markdown `#`
                # only, found nothing in a 50,000-character PRD. Every
                # PAPER-sourced slot then had no section to be written from and
                # fell back to a deck standard. The rule is not loosened to
                # accept plain-text headings; the document is made to meet it.
                level = _heading_level(paragraph.style) if text.strip() else None
                blocks.append(f"{'#' * level} {text.strip()}" if level else text)
            elif tag == "tbl":
                blocks.append("\n".join(_table_rows(Table(child, document))))
    except Exception as error:  # a parser refusing bytes, by class only
        return "", _unreadable(error, name, extension, kind)
    return "\n\n".join(block for block in blocks if block.strip()), None


def _read_plain(data, name, extension, kind):
    # utf-8 first, then the encoding a Windows editor writes, and nothing that
    # cannot fail: latin-1 decodes every byte sequence ever assembled, so
    # falling back to it would turn a binary file into confident gibberish.
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "\x00" in text:
            break
        return text, None
    return "", ExtractionFailure(
        "E_UNREADABLE_FILE",
        f"{name!r} is named {extension} but its bytes are not text.",
        "Check the file opens as text on your own machine, then re-attach it, "
        "or export it as PDF or Word.",
        {
            "filename": name,
            "extension": extension,
            "kind": kind,
            "cause": "UnicodeDecodeError",
        },
    )


def _unreadable(error, name, extension, kind):
    return ExtractionFailure(
        "E_UNREADABLE_FILE",
        f"{name!r} could not be read as {_KIND_LABELS[kind]}.",
        "Check the file opens on your own machine, then re-attach it. If it "
        "opens, re-export it and attach the export.",
        {
            "filename": name,
            "extension": extension,
            "kind": kind,
            # By class, never by message: a parser quotes the bytes it choked
            # on, and this dict reaches the reviewer's panel and the store.
            "cause": type(error).__name__,
        },
    )


def _dependency_failure(package, name, extension, kind):
    return ExtractionFailure(
        "E_EXTRACT_DEPENDENCY",
        f"{_KIND_LABELS[kind]} needs {package}, which is not installed.",
        f"Install the pinned requirements ({package} is in "
        "requirements.txt), then re-run.",
        {
            "filename": name,
            "extension": extension,
            "kind": kind,
            "package": package,
        },
    )


def _normalize(text):
    """One newline convention, no trailing blank space, no runs of blank lines.

    Line content is left alone otherwise, because markdown means something by
    its trailing spaces and by its indentation.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\ufeff", "")
    lines = [line.rstrip() for line in text.split("\n")]
    out = []
    for line in lines:
        if not line and out and not out[-1]:
            continue
        out.append(line)
    return "\n".join(out).strip()
