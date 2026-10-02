"""What a saved deck keeps about the documents it was written from (item 14,
step 5): one record per attachment, as plain data the store can hold.

THE SCHEMA, DECIDED BY ANTONIO 2026-09-07. The extracted TEXT, plus the
filename, the kind, the size and a SHA-256 of the original bytes. Not the bytes.

THE REASONING MATTERS MORE THAN THE RULE. The model never saw the file. It saw
the text this record holds: `document_text` read the file, `base_document` made
that text the base, and both model legs read that text and nothing else. So the
text is the honest record of what produced a slide, and the bytes are a
different artifact that merely looks more authoritative. A reviewer opening a
saved deck six months later and asking "where did this figure come from" is
asking about the text, and holding the PDF instead would answer a question
nobody asked while implying an answer to the one they did.

WHAT THE HASH IS FOR, AND IT IS THE ONLY THING HERE THE TEXT CANNOT SAY. Two
decks came from the same document, or a changed version arrived under an
unchanged name. It is taken over the ORIGINAL BYTES rather than over the text,
because that is the identity of the file the reviewer attached: a PDF re-exported
from the same source produces the same text and different bytes, and a reviewer
asking whether this is the same file means the file.

THE SIZE IS THE FILE'S, NOT THE TEXT'S, for the same reason. It is what the
reviewer's own filesystem says about the thing they attached, and it is the
figure the studio's per-file ceiling refuses against, so a record and a refusal
talk about the same number.

KEEPING THE ORIGINAL DOWNLOADABLE LATER is a pointer to a file on a volume
rather than a column in this table, and nothing here forecloses it: a record
that names the file and hashes it is exactly what such a pointer would hang off.

PLAIN DATA, NOT OBJECTS, the same as `provenance_report`. Every value is a
string or an int, so a record renders in a template, serialises into a run
record, and goes through `json.dumps` into the deck store's `details` column
with nothing having to know about `document_text` or `base_document`.

IN PRECEDENCE ORDER, which is the order the reviewer attached them, so the first
record is the base document the deck was written from. The order carries the
meaning; nothing in a record repeats it, because a rank stored beside a list
that already has one is a second thing to keep true.

NOT ON THE RENDER PATH. A record names a file, and a filename is a source
annotation, which does not belong in front of a client
(NEXT-BUILD-PHASE part two, answer 3). It travels beside the packet exactly as
provenance does and never inside it, and
`tests/test_provenance_never_renders.py` is what holds that.
"""

import hashlib

# The whole schema. Named here rather than left implicit in a dict literal
# because the store holds these for as long as it holds a deck, and a reader of
# a row saved months ago should be able to find out what its keys were.
FIELDS = ("filename", "kind", "size_bytes", "sha256", "text")


def build(upload, document):
    """One record from the file as it arrived and the text that came out of it.

    `upload` is a `base_document.Upload` (the filename and the original bytes)
    and `document` is the `document_text.ExtractedDocument` those bytes
    produced. Both, because neither half knows the whole record: the bytes are
    gone by the time anything holds an extraction, and the extraction is the
    only thing that knows what the text turned out to be.

    The filename comes from the EXTRACTION rather than from the upload. They are
    the same string today (`document_text` echoes it back), and the extraction's
    is the one the kind was decided against, so the name and the kind in a
    record can never describe two different readings of one file.
    """
    return {
        "filename": document.filename,
        "kind": document.kind,
        "size_bytes": len(upload.data),
        "sha256": hashlib.sha256(upload.data).hexdigest(),
        "text": document.text,
    }


def build_all(uploads, documents):
    """Every attachment's record, in the order the reviewer attached them.

    `uploads` and `documents` are parallel: `live_proposal_provider._documents`
    extracts in order and raises on the first failure, so a caller holding both
    holds one extraction per upload, in the same order. Zipping them would hide
    a length mismatch, which would silently pair a record's bytes with another
    file's text, so it is an error instead.

    Returns a list, because that is what `json.dumps` writes into the store's
    `details` column, and an empty one for a run with no attachment.
    """
    uploads = tuple(uploads or ())
    documents = tuple(documents or ())
    if len(uploads) != len(documents):
        raise ValueError(
            "an attachment record needs one extraction per upload: "
            f"{len(uploads)} upload(s) against {len(documents)} extraction(s)."
        )
    return [build(upload, document)
            for upload, document in zip(uploads, documents)]
