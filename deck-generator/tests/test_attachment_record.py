"""What a saved deck keeps about the documents it was written from.

Item 14, step 5. The schema was decided by Antonio on 2026-09-07 and is not
rediscovered here: the extracted TEXT, plus the filename, the kind, the size and
a SHA-256 of the original bytes, and never the bytes themselves.

Two claims are held in this file and they are different claims.

  THE RECORD IS HONEST. The model never saw the file; it read the text. So the
  text is what produced a slide, and a record built from the file alone would
  answer a question nobody asked. The size and the hash are the FILE's, because
  a reviewer asking "is this the same document" means the file, and two exports
  of one source produce the same text and different bytes.

  THE RECORD TRAVELS BESIDE THE PACKET. It leaves the provider in the envelope,
  crosses the adapter and the generator untouched, and arrives at the studio,
  which is what makes saving it possible at all. That it never reaches a deck is
  `tests/test_provenance_never_renders.py`, not this file; that it reaches the
  studio is here, because otherwise that file's guarantees would be vacuous.

Every filename here is meaningless on purpose, and every fixture is built in
process, so there is no binary blob in the repo and no name with meaning in it.
"""

import hashlib
import json
import pathlib

import attachment_record
import document_text
from attachment_record import build, build_all
from base_document import Upload
from live_proposal_provider import LiveProposalProvider
from test_base_document import CONTESTED_ATTACHMENT, SCENARIOS_ONLY, request
from test_live_seam import CLIENTS, STAMP, StubClient

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"

# Bytes whose text is deliberately not their decoding: a byte order mark and
# Windows line endings, both of which `document_text` normalises away. So a test
# that confuses the file with its text fails rather than passing by coincidence.
WINDOWS_BYTES = b"\xef\xbb\xbf# A document\r\n\r\nOne line of text.\r\n"


def extracted(data, name="a-document.md"):
    result = document_text.extract_text(data, name)
    assert result.ok, result
    return result


def record_for(data, name="a-document.md"):
    return build(Upload(filename=name, data=data), extracted(data, name))


# --- the schema -------------------------------------------------------------


def test_the_record_is_five_fields_and_no_others():
    """Named in the module so a reader of a row saved months ago can find out
    what its keys were."""
    assert set(record_for(WINDOWS_BYTES)) == set(attachment_record.FIELDS)
    assert set(attachment_record.FIELDS) == {
        "filename", "kind", "size_bytes", "sha256", "text",
    }


def test_the_record_holds_the_text_and_not_the_bytes():
    """The decision itself. Nothing in a record is bytes, so nothing downstream
    can be tempted to treat the record as the file."""
    record = record_for(WINDOWS_BYTES)
    assert record["text"] == extracted(WINDOWS_BYTES).text
    for value in record.values():
        assert not isinstance(value, (bytes, bytearray))


def test_the_text_is_the_extracted_text_and_not_the_decoded_file():
    """A byte order mark and CRLF line endings are gone from the text and
    present in the bytes, so the two halves of the record cannot be the same
    thing under two names."""
    record = record_for(WINDOWS_BYTES)
    assert "\r" not in record["text"]
    assert not record["text"].startswith("﻿")
    assert record["text"] != WINDOWS_BYTES.decode("utf-8")


def test_the_size_is_the_files_and_not_the_texts():
    """It is what the reviewer's own filesystem says about the thing they
    attached, and it is the number the per-file ceiling refuses against."""
    record = record_for(WINDOWS_BYTES)
    assert record["size_bytes"] == len(WINDOWS_BYTES)
    assert record["size_bytes"] != len(record["text"])


def test_the_hash_is_the_sha256_of_the_original_bytes():
    record = record_for(WINDOWS_BYTES)
    assert record["sha256"] == hashlib.sha256(WINDOWS_BYTES).hexdigest()
    assert len(record["sha256"]) == 64


def test_the_kind_is_the_one_the_extraction_decided():
    record = record_for(WINDOWS_BYTES, "a-document.md")
    assert record["kind"] == document_text.KIND_MARKDOWN


def test_the_filename_comes_from_the_extraction():
    """The same string as the upload's today, and the one the kind was decided
    against, so a record can never name one reading of a file and describe
    another."""
    upload = Upload(filename="a-document.md", data=WINDOWS_BYTES)
    document = extracted(WINDOWS_BYTES, "a-document.md")
    assert build(upload, document)["filename"] == document.filename


# --- what the hash is for ---------------------------------------------------


def test_the_same_bytes_under_a_different_name_hash_the_same():
    """"Two decks came from the same document", which is half of why the hash
    is here at all."""
    first = record_for(WINDOWS_BYTES, "one-name.md")
    second = record_for(WINDOWS_BYTES, "another-name.md")
    assert first["sha256"] == second["sha256"]
    assert first["filename"] != second["filename"]


def test_two_files_with_the_same_text_hash_differently():
    """The other half: a changed version under an unchanged name. These two
    extract to the same text, so only the hash can tell them apart, which is
    exactly why it is taken over the bytes rather than over the text."""
    crlf = record_for(WINDOWS_BYTES, "a-document.md")
    plain = record_for(
        WINDOWS_BYTES.replace(b"\xef\xbb\xbf", b"").replace(b"\r\n", b"\n"),
        "a-document.md",
    )
    assert crlf["text"] == plain["text"]
    assert crlf["sha256"] != plain["sha256"]


# --- many attachments -------------------------------------------------------


def uploads_and_documents(*pairs):
    uploads = [Upload(filename=name, data=data) for name, data in pairs]
    documents = [extracted(data, name) for name, data in pairs]
    return uploads, documents


def test_the_records_are_in_the_order_the_reviewer_attached_them():
    """The order carries the meaning — the first is the base document — so
    nothing in a record repeats it."""
    uploads, documents = uploads_and_documents(
        ("first.md", b"# First\n\nText.\n"),
        ("second.md", b"# Second\n\nText.\n"),
        ("third.md", b"# Third\n\nText.\n"),
    )
    records = build_all(uploads, documents)
    assert [row["filename"] for row in records] == [
        "first.md", "second.md", "third.md",
    ]


def test_no_attachment_is_an_empty_list():
    """Which is the truthful record of a run with no attachment, and what the
    store column holds for one."""
    assert build_all((), ()) == []
    assert build_all(None, None) == []


def test_a_missing_extraction_is_an_error_rather_than_a_short_list():
    """Zipping would pair one file's bytes with another file's text and say
    nothing, which is a wrong record rather than a missing one."""
    uploads, documents = uploads_and_documents(
        ("first.md", b"# First\n\nText.\n"),
        ("second.md", b"# Second\n\nText.\n"),
    )
    try:
        build_all(uploads, documents[:1])
    except ValueError as exc:
        assert "one extraction per upload" in str(exc)
    else:
        raise AssertionError("a length mismatch has to be an error")


# --- what the store needs of it ---------------------------------------------


def test_a_record_survives_json_unchanged():
    """It rides in the deck store's `details` column, which is `json.dumps` of
    a dict, so anything here that is not plain data is a save that fails at the
    moment the reviewer presses the button."""
    records = build_all(*uploads_and_documents(("a-document.md", WINDOWS_BYTES)))
    assert json.loads(json.dumps(records)) == records


def test_the_module_names_no_client_no_project_and_no_document():
    source = (SRC / "attachment_record.py").read_text()
    for token in ("Fabrikam", "WTG", "Northwind", "Ridgeline", ".pdf", ".docx"):
        assert token not in source, token


# --- and it reaches the studio, which is what makes saving it possible ------


def envelope_with(*texts, spec=CLIENTS["one"]):
    provider = LiveProposalProvider(StubClient(spec), generated_at=STAMP)
    handle = provider.submit(
        request(spec, floor=0.0),
        uploads=[Upload(filename=f"document-{n}.md", data=text.encode())
                 for n, text in enumerate(texts)],
    )
    return provider.poll(handle)["envelope"]


def test_the_envelope_carries_one_record_per_attachment():
    built = envelope_with(CONTESTED_ATTACHMENT, SCENARIOS_ONLY)
    assert built["status"] == "ok"
    assert [row["filename"] for row in built["attachments"]] == [
        "document-0.md", "document-1.md",
    ]
    assert built["attachments"][0]["text"].strip() == CONTESTED_ATTACHMENT.strip()


def test_the_envelope_of_a_run_with_no_attachment_carries_an_empty_record():
    """The run that shipped before attachments existed, saying so rather than
    saying nothing."""
    built = envelope_with()
    assert built["status"] == "ok"
    assert built["attachments"] == []


def test_the_record_is_the_text_the_deck_was_actually_written_from():
    """The claim the schema rests on, checked rather than asserted in prose: the
    text in the record is the text `document_text` produced from those bytes,
    which is the text `base_document` made the base and both model legs read."""
    data = CONTESTED_ATTACHMENT.encode()
    built = envelope_with(CONTESTED_ATTACHMENT)
    assert built["attachments"][0]["text"] == extracted(data, "document-0.md").text
    assert built["attachments"][0]["sha256"] == hashlib.sha256(data).hexdigest()


def test_the_record_reaches_the_pipeline_result_the_studio_reads():
    """Through the adapter and the generator, neither of which looks inside it.
    Without this the store has nothing to save."""
    from deck_generator import generate_deck_prompt

    spec = CLIENTS["one"]
    result = generate_deck_prompt(
        "proposal", spec["company"]["name"], spec["project"]["name"],
        LiveProposalProvider(StubClient(spec), generated_at=STAMP),
        pe_firm=spec["pe_firm"], proposal_date="2026-08-15",
        options={"min_data_completeness": 0.0},
        uploads=[Upload(filename="document-0.md",
                        data=CONTESTED_ATTACHMENT.encode())],
        poll_interval=0.0, sleep=lambda _s: None,
    )
    assert result["status"] == "ok", result
    assert [row["filename"] for row in result["attachments"]] == ["document-0.md"]
    assert set(result["attachments"][0]) == set(attachment_record.FIELDS)


def test_the_same_result_with_no_attachment_carries_an_empty_record():
    """So the test above is about the attachment rather than about the run."""
    from deck_generator import generate_deck_prompt

    spec = CLIENTS["one"]
    result = generate_deck_prompt(
        "proposal", spec["company"]["name"], spec["project"]["name"],
        LiveProposalProvider(StubClient(spec), generated_at=STAMP),
        pe_firm=spec["pe_firm"], proposal_date="2026-08-15",
        options={"min_data_completeness": 0.0},
        poll_interval=0.0, sleep=lambda _s: None,
    )
    assert result["status"] == "ok", result
    assert result["attachments"] == []
