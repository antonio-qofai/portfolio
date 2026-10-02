"""Durable deck history — one table, four functions, one SQLite file.

``ui/app.py``'s ``list_recent_decks`` reads the filesystem, and the hosted
service starts every deploy from a fresh copy of the code, so a deck a reviewer
generated on the hosted studio is gone the next time anyone pushes. This module
is where a deck goes so it survives that.

The file sits in whatever directory ``store_dir_resolver`` resolves, which is
the persistent disk volume on the hosted service and a repo-local directory on a
developer machine. SQLite rather than a client/server database because the
volume is what the service actually has; it is a real relational database with
real indexes, it is in the standard library, and the service runs
``gunicorn -w 1`` (see ``railway.json``), so there is one writing process and
SQLite's concurrency ceiling never comes into play.

One table, deliberately. A deck history is a list of decks, and the failure mode
here is a normalized design (decks, revisions, flags, preferences, resolutions)
for something nobody queries relationally. Everything the Decks tab filters and
sorts on is a real column with an index; everything else rides in one JSON
column. If a later prompt needs to query INSIDE that JSON, that is the prompt
that normalizes it.

Nothing here changes ``deck_generator.generate_and_save_deck`` — storage is
additive, and the contract the in-portal integration imports (its keyword
parameters and its returned dict keys) is untouched.
"""

import json
import sqlite3
from datetime import datetime, timezone

from store_dir_resolver import resolve_store_dir


class DeckStoreUnavailable(Exception):
    """The resolved store file could not be opened."""


# The whole schema. `details` carries the prompt, the applied preferences, the
# fidelity report, the flags and their resolutions, and the edit revision chain.
# The indexes are exactly the columns the Decks tab filters and sorts on.
# `created_at` is a UTC ISO-8601 string, which sorts lexicographically in the
# same order it sorts chronologically, so the index still does its job.
_NOW_SQL = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"

_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS decks (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        deck_type   TEXT    NOT NULL,
        company     TEXT    NOT NULL,
        project     TEXT    NOT NULL,
        created_at  TEXT    NOT NULL
                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        is_final    INTEGER NOT NULL DEFAULT 0,
        html        TEXT    NOT NULL,
        details     TEXT    NOT NULL DEFAULT '{}'
    )
    """,
    "CREATE INDEX IF NOT EXISTS decks_company_idx ON decks (company)",
    "CREATE INDEX IF NOT EXISTS decks_project_idx ON decks (project)",
    "CREATE INDEX IF NOT EXISTS decks_deck_type_idx ON decks (deck_type)",
    "CREATE INDEX IF NOT EXISTS decks_created_at_idx ON decks (created_at DESC)",
)

# Everything but `html`, which is the one column worth not carrying into a list
# of sixty rows. `get_deck` returns it.
_LIST_COLUMNS = ("id", "deck_type", "company", "project", "created_at", "is_final", "details")
_FULL_COLUMNS = ("id", "deck_type", "company", "project", "created_at", "is_final",
                 "html", "details")

_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def _connect(store_path=None):
    """Open the store file and make sure the table and its indexes exist."""
    if store_path is None:
        store_path = resolve_store_dir().store_path
    try:
        conn = sqlite3.connect(store_path)
        with conn:
            for statement in _SCHEMA_STATEMENTS:
                conn.execute(statement)
    except sqlite3.Error as exc:
        raise DeckStoreUnavailable(
            f"could not open the deck store at {store_path}: {exc}"
        ) from exc
    return conn


def _rows_to_dicts(columns, rows):
    """Rows as plain dicts, in the shape callers already expect.

    SQLite has no boolean, no JSON, and no timestamp type, so three columns come
    back narrower than they went in and are widened here. Missing any of these
    breaks the Decks tab quietly: `is_final` would be 1 rather than True,
    `details` a JSON string rather than a dict, and `created_at` a string that
    the tab's date rendering cannot format.
    """
    out = []
    for row in rows:
        item = dict(zip(columns, row))
        item["is_final"] = bool(item["is_final"])
        item["details"] = json.loads(item["details"])
        item["created_at"] = datetime.strptime(
            item["created_at"], _TIMESTAMP_FORMAT
        ).replace(tzinfo=timezone.utc)
        out.append(item)
    return out


def save_deck(deck_type, company, project, html, *, is_final=False, details=None,
              store_path=None):
    """Store one deck, or update the row already standing for it. Returns its id.

    A deck is identified by client, project and deck type (prompt B3a). A
    reviewer who saves, edits, and saves again settled on the deck rather than on
    one of its versions, so the second save updates that row instead of leaving
    two rows behind for one deck. ``created_at`` moves with it, because the row
    records what was saved and when it was saved.

    ``details`` is everything that is not a filter column — the prompt, the
    applied preferences, the fidelity report, the flags and their resolutions,
    the edit revision chain — stored verbatim as JSON and handed back unchanged
    by ``get_deck``.
    """
    payload = json.dumps(details if details is not None else {})
    conn = _connect(store_path)
    try:
        with conn:
            standing = conn.execute(
                "SELECT id FROM decks WHERE deck_type = ? AND company = ? AND"
                " project = ? ORDER BY id DESC LIMIT 1",
                (deck_type, company, project),
            ).fetchone()
            if standing:
                conn.execute(
                    "UPDATE decks SET created_at = " + _NOW_SQL
                    + ", is_final = ?, html = ?, details = ? WHERE id = ?",
                    (int(bool(is_final)), html, payload, standing[0]),
                )
                return standing[0]
            cur = conn.execute(
                """
                INSERT INTO decks (deck_type, company, project, is_final, html, details)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (deck_type, company, project, int(bool(is_final)), html, payload),
            )
            return cur.lastrowid
    finally:
        conn.close()


def list_decks(*, company=None, project=None, deck_type=None, limit=60,
               store_path=None):
    """Stored decks, newest first, as display rows without the HTML.

    Filters are the indexed columns and each is optional; passing none lists
    everything up to ``limit``. The id is a tie-break on ``created_at`` so a
    batch saved inside the same clock tick still reads as a stable order.
    """
    clauses, params = [], []
    for column, value in (("company", company), ("project", project),
                          ("deck_type", deck_type)):
        if value is not None:
            clauses.append(f"{column} = ?")
            params.append(value)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    conn = _connect(store_path)
    try:
        cur = conn.execute(
            "SELECT " + ", ".join(_LIST_COLUMNS) + " FROM decks" + where
            + " ORDER BY created_at DESC, id DESC LIMIT ?",
            params + [limit],
        )
        return _rows_to_dicts(_LIST_COLUMNS, cur.fetchall())
    finally:
        conn.close()


def get_deck(deck_id, *, store_path=None):
    """One stored deck by id, HTML and details included, or ``None``."""
    conn = _connect(store_path)
    try:
        cur = conn.execute(
            "SELECT " + ", ".join(_FULL_COLUMNS) + " FROM decks WHERE id = ?",
            (deck_id,),
        )
        row = cur.fetchone()
        return _rows_to_dicts(_FULL_COLUMNS, [row])[0] if row else None
    finally:
        conn.close()


def delete_decks(deck_ids, *, store_path=None):
    """Delete decks by id. Returns how many rows were actually removed.

    A fourth function, added now that the Decks tab reads this store directly
    (prompt B3): its per-row and bulk delete both need to remove a row, and
    nothing here could do that before. One function covers both, a single id
    or many, since a bulk delete is just a delete with more ids.
    """
    ids = list(deck_ids)
    if not ids:
        return 0
    conn = _connect(store_path)
    try:
        with conn:
            cur = conn.execute(
                "DELETE FROM decks WHERE id IN ({})".format(
                    ", ".join("?" for _ in ids)),
                ids,
            )
            return cur.rowcount
    finally:
        conn.close()
