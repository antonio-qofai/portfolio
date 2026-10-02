"""The Decks tab says which document each saved deck was written from.

Antonio, 2026-09-20, removing the "Which document this deck was written from"
card from the Result tab: "the user knows because they uploaded the PRD ... how
about this? When we press save deck, it has the PRD like in the decks tab. In
the Decks tab, it can just have like a column where it says like source and
then it just says like the PRD and it has like a link to the file or something."

So the information did not go away, it moved. These tests hold the new home.

The link opens the extracted TEXT rather than the file, because the file is
never stored: `attachment_record` keeps the filename, the kind, the size, a
sha256 and the text the extractor produced, and the text is what the model
actually read.

Run with: python3 -m pytest tests/test_decks_tab_names_its_source.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ui"))

try:
    import flask  # noqa: F401
    _HAVE_FLASK = True
except ImportError:
    _HAVE_FLASK = False

RECORDS = [
    {"filename": "Contoso_PRD.docx", "kind": "docx", "size_bytes": 125574,
     "sha256": "c" * 64, "text": "3. Goals & Success Metrics\nthe PRD body"},
    {"filename": "supporting-note.md", "kind": "md", "size_bytes": 120,
     "sha256": "d" * 64, "text": "a note"},
]


def _saved(details):
    """One row in the store, and the studio pointed at it."""
    import app as ui_app
    from deck_store import save_deck

    deck_id = save_deck("proposal", "Contoso", "Client Onboarding",
                        "<html><body>deck</body></html>",
                        is_final=True, details=details)
    ui_app.app.testing = True
    return ui_app.app.test_client(), deck_id


def _decks_tab(client):
    return client.get("/?tab=decks").get_data(as_text=True)


def test_the_column_names_the_document_the_deck_came_from():
    if not _HAVE_FLASK:
        return
    client, _id = _saved({"attachments": RECORDS})
    html = _decks_tab(client)
    assert "<th>Source</th>" in html, "the column has to exist"
    assert "Contoso_PRD.docx" in html, (
        "the base document is the first attachment and is what names the row"
    )


def test_more_than_one_attachment_is_counted_not_listed():
    """A row is a row. The base names it and the rest are a count."""
    if not _HAVE_FLASK:
        return
    client, _id = _saved({"attachments": RECORDS})
    html = _decks_tab(client)
    assert "+1 more" in html
    assert "supporting-note.md" not in html, "the second one is counted, not listed"


def test_a_deck_saved_before_attachments_existed_still_renders():
    """Every late-added `details` key behaves this way: absent reads as nothing,
    and the row opens rather than breaking."""
    if not _HAVE_FLASK:
        return
    client, _id = _saved({"raw_company": "Contoso"})
    html = _decks_tab(client)
    assert "<th>Source</th>" in html
    assert "Contoso" in html, "the row itself must still be listed"


def test_the_link_serves_the_text_the_model_read():
    if not _HAVE_FLASK:
        return
    client, deck_id = _saved({"attachments": RECORDS})
    response = client.get("/attachment-text", query_string={"id": deck_id,
                                                            "index": 0})
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert response.mimetype == "text/plain"
    assert "3. Goals & Success Metrics" in body, "the extracted text itself"
    assert "Contoso_PRD.docx" in body, "and which file it came out of"
    assert "c" * 64 in body, "with the hash that says which version it was"


def test_the_second_attachment_is_reachable_by_index():
    if not _HAVE_FLASK:
        return
    client, deck_id = _saved({"attachments": RECORDS})
    body = client.get("/attachment-text",
                      query_string={"id": deck_id, "index": 1}).get_data(as_text=True)
    assert "a note" in body


def test_an_index_that_is_not_there_is_a_404_not_a_crash():
    if not _HAVE_FLASK:
        return
    client, deck_id = _saved({"attachments": RECORDS})
    assert client.get("/attachment-text",
                      query_string={"id": deck_id, "index": 9}).status_code == 404
    assert client.get("/attachment-text",
                      query_string={"id": 999999, "index": 0}).status_code == 404
