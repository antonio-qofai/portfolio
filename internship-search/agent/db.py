"""SQLite state. This is the source of truth for every posting.

The schema carries columns that Milestone 2 does not populate yet (scores, labels,
Airtable fields, Stage 0 tagging). They are created now so later milestones never
have to migrate a live database.
"""

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS postings (
    id                 INTEGER PRIMARY KEY,
    -- The permanent row key. Assigned from the posting's content at discovery
    -- and never recomputed, because Airtable joins on it and a record that
    -- changed its key would orphan the owner's label. Identity lives in the
    -- identity column below; this is only a name for the row.
    hash               TEXT NOT NULL UNIQUE,
    company            TEXT NOT NULL,
    title              TEXT NOT NULL,
    location           TEXT,
    url                TEXT,
    source             TEXT NOT NULL,
    ats_platform       TEXT,
    external_id        TEXT,
    description        TEXT,

    fit_score          INTEGER,
    reach_score        INTEGER,
    tier               INTEGER,
    reason             TEXT,
    fit_override       INTEGER,
    reach_override     INTEGER,

    first_seen         TEXT NOT NULL,
    last_seen_open     TEXT NOT NULL,
    closed_detected_at TEXT,
    consecutive_misses INTEGER NOT NULL DEFAULT 0,

    stated_deadline    TEXT,
    term               TEXT NOT NULL DEFAULT 'unknown',
    weekly_hours       TEXT,
    term_evidence      TEXT,
    hours_evidence     TEXT,
    tagged_at          TEXT,

    -- What the posting said, before sources/prefilter.toml decided what it
    -- meant. term is the conclusion; this is the raw input to it.
    term_stated        TEXT,
    -- 'feed' when the aggregator's own term field answered it for free,
    -- 'model' when a Haiku call was needed. Cost telemetry lives on this.
    stage0_source      TEXT,

    -- Stage A. 'killed', 'surface', or 'pending'. NULL means not yet examined.
    prefilter_verdict  TEXT,
    prefilter_reason   TEXT,
    prefilter_at       TEXT,

    -- Raw strings as the aggregator published them, never interpreted here.
    -- Stage 0 owns the term column; these are the evidence it reads first.
    feed_terms         TEXT,
    feed_degrees       TEXT,

    flags              TEXT NOT NULL DEFAULT '',
    alerted_at         TEXT,
    alert_type         TEXT,

    label              TEXT,
    applied_status     TEXT NOT NULL DEFAULT 'not_applied',
    label_reason       TEXT,

    -- Milestone 5. Which Airtable row this posting was pushed to, if any.
    airtable_record_id TEXT,
    airtable_synced_at TEXT
);


CREATE TABLE IF NOT EXISTS companies (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    ats_platform    TEXT,
    token           TEXT,
    tier_bias       REAL NOT NULL DEFAULT 0,
    rolling_yes_rate REAL
);

CREATE TABLE IF NOT EXISTS contacts (
    id             INTEGER PRIMARY KEY,
    name           TEXT NOT NULL,
    company_id     INTEGER REFERENCES companies(id),
    relationship   TEXT,
    notes          TEXT,
    last_contacted TEXT
);

-- Completion state for the preparation tasks defined in sources/resources.toml.
-- The TOML holds definitions, SQLite holds progress, so editing the file never
-- wipes what is already done.
CREATE TABLE IF NOT EXISTS prep_tasks (
    task_key     TEXT PRIMARY KEY,
    resource_key TEXT NOT NULL,
    done_at      TEXT,
    notes        TEXT
);

-- Small key/value scratch for facts about the agent itself rather than about a
-- posting. Milestone 7 stores when the Sunday roundup last went out, which no
-- posting column can answer: a roundup that carried only a closure summary
-- stamps no posting at all, and "when did the last one send" is exactly what
-- decides whether one is owed. Values are strings; the caller decides what they
-- mean.
CREATE TABLE IF NOT EXISTS agent_state (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY,
    timestamp       TEXT NOT NULL,
    postings_seen   INTEGER NOT NULL DEFAULT 0,
    new_postings    INTEGER NOT NULL DEFAULT 0,
    closed_detected INTEGER NOT NULL DEFAULT 0,
    sources_ok      INTEGER NOT NULL DEFAULT 0,
    sources_failed  INTEGER NOT NULL DEFAULT 0,
    model_calls     INTEGER NOT NULL DEFAULT 0,
    estimated_cost  REAL NOT NULL DEFAULT 0,
    score_variance  REAL
);
"""


# Indexes are created AFTER migrate() runs, never inside SCHEMA. An index on a
# column that MIGRATIONS is about to add does not exist yet on a live database,
# and CREATE INDEX on a missing column is a hard error. Learned the hard way
# when prefilter_verdict was added; see CHANGELOG.md for 2026-08-07.
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_postings_source ON postings(source);
CREATE INDEX IF NOT EXISTS idx_postings_open ON postings(closed_detected_at);
CREATE INDEX IF NOT EXISTS idx_postings_verdict ON postings(prefilter_verdict);
CREATE INDEX IF NOT EXISTS idx_postings_identity ON postings(identity);
CREATE INDEX IF NOT EXISTS idx_postings_content ON postings(content_hash);
"""
# Neither index above is UNIQUE, on purpose. A unique index would turn a board
# that starts reusing one id across two live postings into a crash on a
# scheduled run at three in the morning, and a duplicate identity is a
# reconcilable annoyance rather than a corruption. tools.reconcile_identity with
# no flags counts duplicates so they stay visible;
# agent/fetchers.resolve_identities prevents the common cause.


# Columns added after the first database existed. CREATE TABLE IF NOT EXISTS does
# nothing to a table that is already there, so a new column has to be added by hand.
# Adding a name here is all that is needed; the column appears on the next run.
MIGRATIONS = {
    "postings": {
        "feed_terms": "TEXT",
        "feed_degrees": "TEXT",
        # Stage 0, Milestone 4. term_stated is what the posting said; term is
        # what sources/prefilter.toml decided that means. Keeping both means a
        # change to the term vocabulary can be replayed without re-tagging.
        "term_stated": "TEXT",
        "hours_evidence": "TEXT",
        "stage0_source": "TEXT",
        # Stage A, Milestone 4. killed, surface, or pending. NULL means the
        # prefilter has not looked at this posting yet.
        "prefilter_verdict": "TEXT",
        "prefilter_reason": "TEXT",
        "prefilter_at": "TEXT",
        # Milestone 5. The Airtable record this posting was pushed to, so the
        # sync updates in place instead of searching the base by hash on every
        # run. Empty means the posting has never been pushed.
        "airtable_record_id": "TEXT",
        "airtable_synced_at": "TEXT",
        # The posting identity fix, 2026-08-12. identity is what makes two
        # sightings the same posting and is stable across an edit to the
        # listing; content_hash is what the posting currently says and is
        # refreshed on every sighting. agent/fetchers._identity owns the rule.
        "identity": "TEXT",
        "content_hash": "TEXT",
        # Milestone 6, the ranker. fit_score, reach_score, tier and reason have
        # existed since the first schema; these two say when the score was
        # written and which stage wrote it. scored_by is 'stage_b' when the cheap
        # model's answer stood and 'stage_c' when the expensive one replaced it,
        # which is the only way to tell afterwards what a score cost.
        "scored_at": "TEXT",
        "scored_by": "TEXT",
        # 2026-08-15. When an email told the owner that a posting he marked
        # interested had closed. Separate from alerted_at, which records the
        # opposite event, the posting's discovery. A posting gets both over its
        # life and neither stamp can stand in for the other.
        "closure_alerted_at": "TEXT",
        # Milestone 7, the two urgent triggers in PRD section 3. One stamp per
        # trigger rather than one shared stamp, because the triggers are
        # different questions: a posting can go stale unactioned and then, weeks
        # later, publish a deadline. A single stamp would swallow the second
        # alert, which is the one that actually costs him the role. Neither can
        # stand in for alerted_at, which records the posting's discovery.
        "urgent_deadline_alerted_at": "TEXT",
        "urgent_stale_alerted_at": "TEXT",
        # 2026-08-19. When the owner first set Applied status to anything other
        # than not_applied, stamped by the sync rather than typed by him. He
        # sets a dropdown; the date is the agent's job. Nothing else can measure
        # how long an application has been silent, because Airtable records the
        # state and never when it changed.
        #
        # It is cleared if he sets the status back to not applied, because the
        # only honest reading of that is that the first setting was a mistake.
        "applied_at": "TEXT",
        # 2026-08-19. The owner's own closure, set from the Interested view when
        # he learns a role is gone before the watcher does. Deliberately NOT
        # closed_detected_at: that column belongs to the watcher, which clears
        # it the moment the board shows the posting again, so his answer would
        # survive until the next poll and no longer. A board that still lists a
        # dead posting is exactly the case this exists for.
        "closed_by_me": "INTEGER NOT NULL DEFAULT 0",
        # The two columns the dashboard's interview panel reads, added
        # 2026-09-20. `applied_status` says where an application stands and has
        # since Milestone 5; neither it nor anything else in this table could
        # say WHEN the next thing happens, so "any interviews coming up and the
        # date" had nothing to read.
        #
        # Deliberately one date and one note rather than an events table. What
        # The owner asked for is the next thing in his calendar, a full history of
        # every round would be a second schema to keep in sync with Airtable for
        # no gain today, and a column he can overwrite is something he will
        # actually keep current. Revisit only if he asks to see past rounds.
        "next_event_at": "TEXT",
        "next_event_note": "TEXT",
    },
}


# Filling identity for rows that predate the column. Every posting stored before
# 2026-08-12 has an external_id, so the first branch covers all of them, but the
# second exists because a source that supplies no id is allowed by the schema.
# This mirrors agent/fetchers._identity exactly and the two must not drift.
BACKFILL = """
UPDATE postings SET
    identity = CASE
        WHEN external_id IS NOT NULL AND TRIM(external_id) <> ''
        THEN source || '|' || TRIM(external_id)
        ELSE 'content|' || hash
    END
WHERE identity IS NULL;

UPDATE postings SET content_hash = hash WHERE content_hash IS NULL;
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Add any column in MIGRATIONS the live database does not have yet."""
    added = []
    for table, columns in MIGRATIONS.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, coltype in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {coltype}")
                added.append(f"{table}.{name}")
    if added:
        conn.commit()

    # Runs on every open rather than only after the ALTER, because a row left
    # without an identity matches nothing and would be rediscovered as new. The
    # WHERE clause makes it free once the column is full.
    conn.executescript(BACKFILL)
    conn.commit()
    return added


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    migrate(conn)
    conn.executescript(INDEXES)
    return conn


def sync_companies(conn: sqlite3.Connection, companies) -> None:
    """Mirror the token map into the companies table. The file stays authoritative."""
    for c in companies:
        conn.execute(
            """INSERT INTO companies (name, ats_platform, token) VALUES (?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET ats_platform=excluded.ats_platform,
                                              token=excluded.token""",
            (c.name, c.ats, c.token),
        )
    conn.commit()


def _match(conn: sqlite3.Connection, p) -> sqlite3.Row | None:
    """Find the stored row a fetched posting belongs to, or None if it is new.

    Two lookups, in order, and the order is the whole point.

    Identity first. That is the ATS requisition id where there is one, so a
    posting that has been edited since we last saw it still lands on its own row
    instead of closing the old one and arriving as a discovery.

    Content second, which catches a posting already stored under a different
    source key. Both aggregator feeds carry the same jobs under their own ids,
    so without this the same posting would be stored twice and reach the digest
    twice. That row keeps its original source and identity; this run is a
    sighting of it, not a claim on it.
    """
    row = conn.execute(
        "SELECT id, title, prefilter_verdict FROM postings WHERE identity = ?",
        (p.identity,),
    ).fetchone()
    if row is not None:
        return row
    return conn.execute(
        "SELECT id, title, prefilter_verdict FROM postings WHERE content_hash = ?",
        (p.content_hash,),
    ).fetchone()


def _row_key(conn: sqlite3.Connection, p) -> str:
    """Pick the permanent row key for a posting being stored for the first time.

    `hash` is that key. It is assigned once here and never recomputed, because
    Airtable joins on it and a row that changed its key would strand the owner's
    label on a record nothing points at any more.

    It starts life as the posting's content hash, which is what makes a stored
    row readable against a fresh poll. But it cannot simply BE the content hash,
    because the two answer different questions and drift apart the moment a
    company edits a listing: the content hash follows the edit and the row key,
    correctly, does not.

    That drift is what killed every run between 2026-08-17 and 2026-08-18. An
    Anthropic requisition stored on 2026-08-13 under `SF | NYC` kept the key it
    was given while the company added Seattle and moved its content hash on. A
    genuinely different requisition then arrived still carrying `SF | NYC`,
    computed the same key, matched no stored row by identity or by content
    because it really was a different posting, and hit the unique index on
    insert. The watcher is a required step, so all four runs stopped there.

    So: take the content hash when the table does not already hold it, which is
    the overwhelming majority of inserts, and otherwise derive a key from the
    identity, which is one per requisition by construction. The counter covers
    the case where even that is taken, which needs two boards to collide on a
    derived digest and has never happened.

    This is a storage rule, not an identity rule. It decides what a new row is
    called and never which row a posting belongs to; `_match` and
    `fetchers._identity` still own that and are untouched by it.
    """

    def taken(candidate: str) -> bool:
        return conn.execute(
            "SELECT 1 FROM postings WHERE hash = ?", (candidate,)
        ).fetchone() is not None

    if not taken(p.hash):
        return p.hash

    for attempt in range(1, 100):
        candidate = hashlib.sha256(
            f"{p.identity}|{attempt}".encode()
        ).hexdigest()[:32]
        if not taken(candidate):
            return candidate

    raise RuntimeError(f"no free row key for {p.company}: {p.title}")


def upsert_seen(conn: sqlite3.Connection, postings, stats: dict | None = None) -> list[dict]:
    """Insert postings we have not seen before, refresh the ones we have.

    Returns only the genuinely new ones, which are what the digest reports on.
    Seeing a posting again always resets its miss counter and clears any closure,
    so a posting that briefly vanished and came back is treated as open.

    An edited posting is an update here rather than an insert, so the label, the
    score overrides and the Airtable link stay attached to the role they were
    about. Pass stats to count how often that happens.
    """
    ts = now()
    new_rows: list[dict] = []

    for p in postings:
        existing = _match(conn, p)

        if existing is None:
            key = _row_key(conn, p)
            conn.execute(
                """INSERT INTO postings
                   (hash, identity, content_hash, company, title, location, url,
                    source, ats_platform, external_id, description, feed_terms,
                    feed_degrees, first_seen, last_seen_open)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    key, p.identity, p.content_hash, p.company, p.title,
                    p.location, p.url, p.source, p.ats, p.external_id,
                    p.description, p.feed_terms or None, p.feed_degrees or None,
                    ts, ts,
                ),
            )

            # The caller compares these dicts against rows read back out of the
            # table, so what they call the posting has to be what the table
            # called it. Handing back the hash the Posting computed would be
            # right in every case except the one _row_key exists for.
            row = p.as_dict()
            row["hash"] = key
            new_rows.append(row)
            continue

        # A feed publishes no description, so an empty one is the absence of
        # information rather than a posting that lost its text. Never let it
        # overwrite a body a company board already gave us.
        conn.execute(
            """UPDATE postings
               SET last_seen_open = ?, consecutive_misses = 0,
                   closed_detected_at = NULL, url = ?, location = ?, title = ?,
                   company = ?, content_hash = ?,
                   description = CASE WHEN ? <> '' THEN ? ELSE description END
               WHERE id = ?""",
            (
                ts, p.url, p.location, p.title, p.company, p.content_hash,
                p.description, p.description, existing["id"],
            ),
        )

        # Both sides were written through Posting, which normalises whitespace,
        # so this compares like with like.
        if existing["title"] == p.title:
            continue

        # The title changed on a posting we already hold. Before this fix a
        # rename produced a new row that Stage A filtered from scratch; now that
        # the row survives the rename, its verdict has to be reconsidered too, or
        # a role renamed into scope stays killed on a title it no longer has.
        # Stage 0 tagging is deliberately kept: re-tagging costs model calls and
        # the term almost never changes with the title.
        if stats is not None:
            stats["retitled"] = stats.get("retitled", 0) + 1
        if existing["prefilter_verdict"] is not None:
            conn.execute(
                "UPDATE postings SET prefilter_verdict = NULL, "
                "prefilter_reason = NULL, prefilter_at = NULL WHERE id = ?",
                (existing["id"],),
            )
        # The score goes with the verdict, for the same reason. Fit is a
        # judgement about the role the title describes, so a score carried over
        # from a title the posting no longer has is a stale answer to a question
        # that changed. The owner's own overrides are never touched here; they are
        # his opinion of the posting, not the model's, and agent/ranker.py
        # applies them on top of whatever the re-score returns.
        conn.execute(
            "UPDATE postings SET fit_score = NULL, reach_score = NULL, "
            "tier = NULL, reason = NULL, scored_at = NULL, scored_by = NULL "
            "WHERE id = ?",
            (existing["id"],),
        )

    conn.commit()
    return new_rows


def age_missing(conn: sqlite3.Connection, source_key: str, seen: set[str]) -> list[dict]:
    """Increment the miss counter for postings absent from a source we polled successfully.

    Only call this for sources that actually returned data. A source that errored
    tells us nothing about its postings, and counting that as a miss would
    manufacture false closures. See PRD section 4.

    seen holds identities, not hashes. A posting whose location or title was
    edited is still present under the identity it has always had, so it is no
    longer counted as missing and no longer closed for having been edited.
    """
    ts = now()
    closed: list[dict] = []

    rows = conn.execute(
        "SELECT * FROM postings WHERE source = ? AND closed_detected_at IS NULL",
        (source_key,),
    ).fetchall()

    for row in rows:
        if row["identity"] in seen:
            continue
        misses = row["consecutive_misses"] + 1
        if misses >= config.CLOSURE_MISS_THRESHOLD:
            conn.execute(
                "UPDATE postings SET consecutive_misses = ?, closed_detected_at = ? WHERE id = ?",
                (misses, ts, row["id"]),
            )
            closed.append(dict(row))
        else:
            conn.execute(
                "UPDATE postings SET consecutive_misses = ? WHERE id = ?",
                (misses, row["id"]),
            )

    conn.commit()
    return closed


def record_run(conn: sqlite3.Connection, **kwargs) -> None:
    fields = {"timestamp": now(), **kwargs}
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    conn.execute(f"INSERT INTO runs ({cols}) VALUES ({marks})", tuple(fields.values()))
    conn.commit()


def known_sources(conn: sqlite3.Connection) -> set[str]:
    """Every source key that has ever stored a posting.

    A source missing from this set has never been polled, so everything it returns is
    new by definition. That is a seeding run for that one source, not news, and
    agent/run.py suppresses it from the digest on the same reasoning that suppresses
    the very first run. See PRD section 10, Milestone 2.5.
    """
    return {r["source"] for r in conn.execute("SELECT DISTINCT source FROM postings")}


def open_count(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM postings WHERE closed_detected_at IS NULL"
    ).fetchone()["n"]


# ------------------------------------------------------- Stage A and Stage 0

def _merge_flags(existing: str, new: list[str]) -> str:
    """Flags are a comma-separated set. Order is stable so the column diffs cleanly."""
    seen = [f for f in (existing or "").split(",") if f]
    for flag in new:
        if flag not in seen:
            seen.append(flag)
    return ",".join(seen)


def untriaged(conn: sqlite3.Connection) -> list[dict]:
    """Open postings the prefilter has never looked at."""
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM postings "
            "WHERE closed_detected_at IS NULL AND prefilter_verdict IS NULL"
        )
    ]


def pending(conn: sqlite3.Connection, tagged_only: bool = False) -> list[dict]:
    """Postings that cleared the hard exclusions and await the timing pass.

    tagged_only returns just the ones Stage 0 has already answered for, which is
    what the timing pass can actually decide.
    """
    sql = (
        "SELECT * FROM postings "
        "WHERE closed_detected_at IS NULL AND prefilter_verdict = 'pending'"
    )
    if tagged_only:
        sql += " AND tagged_at IS NOT NULL"
    return [dict(r) for r in conn.execute(sql)]


def needs_description(
    conn: sqlite3.Connection, platforms, limit: int
) -> list[dict]:
    """Postings that cleared the hard rules and arrived with no description.

    Only Workday listings land here today. Its board response carries a title, a
    location and a requisition id and no text at all, so every prefilter rule
    that reads a description is blind until something goes back for it, one
    request per posting.

    Restricted to `pending`, meaning the hard rules already looked at this
    posting and did not kill it. That ordering is the whole cost control: a
    Workday tenant answers with hundreds of postings and almost all of them die
    on the title, which is free. Oldest first, so a capped run drains the queue
    in discovery order rather than re-reading its head.
    """
    if not platforms or limit <= 0:
        return []
    marks = ",".join("?" for _ in platforms)
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM postings "
            "WHERE prefilter_verdict = 'pending' AND closed_detected_at IS NULL "
            f"AND ats_platform IN ({marks}) "
            "AND TRIM(COALESCE(description, '')) = '' "
            "ORDER BY first_seen LIMIT ?",
            list(platforms) + [limit],
        )
    ]


def set_description(conn: sqlite3.Connection, posting_id: int, text: str) -> None:
    """Store a description fetched after discovery.

    Deliberately not routed through upsert_seen. That path treats an empty
    description as "this source does not publish one" and keeps what it already
    had, which is right for a feed re-listing a posting and wrong here, where
    the empty string is exactly what is being replaced.
    """
    conn.execute(
        "UPDATE postings SET description = ? WHERE id = ?",
        ((text or "")[: config.DESCRIPTION_CHARS], posting_id),
    )


def record_verdict(conn: sqlite3.Connection, posting: dict, verdict) -> None:
    """Store a Stage A verdict. The caller already holds the row, so the
    existing flags come from it rather than from a second query per posting."""
    conn.execute(
        """UPDATE postings
           SET prefilter_verdict = ?, prefilter_reason = ?, prefilter_at = ?,
               flags = ?
           WHERE id = ?""",
        (
            verdict.outcome,
            verdict.reason,
            now(),
            _merge_flags(posting.get("flags"), verdict.flags),
            posting["id"],
        ),
    )


def clear_verdicts(conn: sqlite3.Connection, owned_flags) -> int:
    """Forget every Stage A verdict so edited rules can be re-applied.

    Stage 0 tags are kept, so re-running costs nothing. Only the flags this
    filter owns are removed; anything a later milestone writes is left alone.
    """
    rows = conn.execute(
        "SELECT id, flags FROM postings WHERE closed_detected_at IS NULL "
        "AND prefilter_verdict IS NOT NULL"
    ).fetchall()
    owned = set(owned_flags)
    conn.executemany(
        "UPDATE postings SET prefilter_verdict = NULL, prefilter_reason = NULL, "
        "prefilter_at = NULL, flags = ? WHERE id = ?",
        [
            (",".join(f for f in (r["flags"] or "").split(",") if f and f not in owned),
             r["id"])
            for r in rows
        ],
    )
    conn.commit()
    return len(rows)


def record_tag(conn: sqlite3.Connection, posting_id: int, tag: dict) -> None:
    """Write Stage 0's answer. Written once per posting and never revisited."""
    conn.execute(
        """UPDATE postings
           SET term = ?, term_stated = ?, term_evidence = ?, weekly_hours = ?,
               hours_evidence = ?, stated_deadline = ?, stage0_source = ?,
               tagged_at = ?
           WHERE id = ?""",
        (
            tag["term"], tag["term_stated"], tag["term_evidence"],
            tag["weekly_hours"], tag["hours_evidence"],
            tag["stated_deadline"] or None, tag["stage0_source"],
            now(), posting_id,
        ),
    )


def triage_counts(conn: sqlite3.Connection) -> dict:
    """How the open set currently splits. This is the Milestone 4 measurement."""
    rows = conn.execute(
        "SELECT COALESCE(prefilter_verdict, 'untriaged') AS v, COUNT(*) AS n "
        "FROM postings WHERE closed_detected_at IS NULL GROUP BY v"
    ).fetchall()
    counts = {r["v"]: r["n"] for r in rows}
    counts["total"] = sum(counts.values())
    return counts


# ------------------------------------------------------------ Stage B and C

def unscored(
    conn: sqlite3.Connection,
    max_age_days: int = 0,
    newest_first: bool = False,
    always_labels: tuple = (),
) -> list[dict]:
    """Open postings the prefilter surfaced that carry no score yet.

    The three arguments are the queue policy from `rubric.md`, passed in rather
    than read here, because this module must not depend on the rubric. Their
    defaults reproduce the pre-2026-09-20 behaviour exactly: no window, oldest
    first, no label exemption.

    `max_age_days` of 0 means no window at all. A label in `always_labels`
    escapes the window whatever the posting's age, because an explicit yes from
    The owner is the best thing the next dollar can be spent on.
    """
    where = [
        "closed_detected_at IS NULL",
        "prefilter_verdict = 'surface'",
        "COALESCE(closed_by_me, 0) = 0",
        "fit_score IS NULL",
    ]
    params: list = []
    if max_age_days and max_age_days > 0:
        window = "julianday('now') - julianday(first_seen) <= ?"
        params.append(max_age_days)
        if always_labels:
            marks = ", ".join("?" for _ in always_labels)
            window = f"({window} OR label IN ({marks}))"
            params.extend(always_labels)
        where.append(window)
    order = "first_seen DESC" if newest_first else "first_seen"
    sql = (
        "SELECT * FROM postings WHERE "
        + " AND ".join(where)
        + f" ORDER BY {order}"
    )
    return [dict(r) for r in conn.execute(sql, params)]


def record_score(conn: sqlite3.Connection, posting_id: int, score: dict) -> None:
    """Write a ranking result. Overwrites, because a re-score is the point.

    Unlike record_tag this is not gated on being unwritten. Stage C deliberately
    overwrites Stage B's answer on the same posting within a single run, and a
    retitled posting is scored again from scratch.
    """
    conn.execute(
        """UPDATE postings
           SET fit_score = ?, reach_score = ?, tier = ?, reason = ?,
               flags = ?, scored_at = ?, scored_by = ?
           WHERE id = ?""",
        (
            score["fit"], score["reach"], score["tier"], score["reason"],
            score["flags"], now(), score["stage"], posting_id,
        ),
    )


def labeled_examples(conn: sqlite3.Connection, per_label: int) -> dict[str, list[dict]]:
    """The owner's labelled postings, newest first, for the few-shot block.

    Keyed by label so the caller never has to know which values exist. Postings
    carrying an explicit score override come first within each list: PRD section
    4 gives an override the highest weight, and a row he bothered to renumber is
    a stronger statement of taste than one he only ticked.

    Returns empty lists when nothing is labelled, which is the expected state on
    the first run and for as long as the Airtable base has no labels in it.
    """
    if per_label <= 0:
        return {}
    rows = conn.execute(
        "SELECT company, title, location, term, label, label_reason, "
        "       fit_override, reach_override "
        "FROM postings WHERE label IS NOT NULL AND TRIM(label) <> '' "
        "ORDER BY label, "
        "  CASE WHEN fit_override IS NOT NULL OR reach_override IS NOT NULL "
        "       THEN 0 ELSE 1 END, "
        "  COALESCE(airtable_synced_at, first_seen) DESC"
    ).fetchall()

    grouped: dict[str, list[dict]] = {}
    for row in rows:
        bucket = grouped.setdefault(row["label"], [])
        if len(bucket) < per_label:
            bucket.append(dict(row))
    return grouped


def score_counts(conn: sqlite3.Connection) -> dict:
    """How the surfaced set currently splits by tier. Read-only."""
    rows = conn.execute(
        "SELECT COALESCE(tier, 0) AS tier, COUNT(*) AS n FROM postings "
        "WHERE closed_detected_at IS NULL AND prefilter_verdict = 'surface' "
        "GROUP BY tier"
    ).fetchall()
    counts = {f"tier_{r['tier']}" if r["tier"] else "unscored": r["n"] for r in rows}
    counts["total"] = sum(counts.values())
    return counts


def surfaced(conn: sqlite3.Connection, unalerted_only: bool = False) -> list[dict]:
    sql = (
        "SELECT * FROM postings "
        "WHERE closed_detected_at IS NULL AND prefilter_verdict = 'surface' "
        "AND COALESCE(closed_by_me, 0) = 0"
    )
    if unalerted_only:
        sql += " AND alerted_at IS NULL"
    return [dict(r) for r in conn.execute(sql + " ORDER BY company, title")]


def carryover(conn: sqlite3.Connection, hours: float) -> list[dict]:
    """Surfaced postings found recently that no email has carried yet.

    Once the watcher runs on a schedule, most runs are quiet ones that send
    nothing. A posting they find is no longer new by the time the digest run
    comes round, so without this it would be filed correctly and never reach the
    inbox, which is exactly the coverage miss the system exists to prevent.

    The window is what keeps this from turning into the backlog report. Every
    posting older than it stays where it is; releasing those is
    tools.backlog_report --mark-alerted, and the owner's call.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(
        timespec="seconds"
    )
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM postings "
            "WHERE closed_detected_at IS NULL AND prefilter_verdict = 'surface' "
            "AND COALESCE(closed_by_me, 0) = 0 "
            "AND alerted_at IS NULL AND first_seen >= ? "
            "ORDER BY company, title",
            (cutoff,),
        )
    ]


def closed_interested(conn: sqlite3.Connection) -> list[dict]:
    """Postings the owner marked interested that have since closed, unannounced.

    The digest already lists what closed on the run that found it, but three of
    the four scheduled runs are quiet and send nothing, so a closure detected at
    08:10 or 22:10 was never carried by any email. That is how a Mistral role he
    was applying to closed on 2026-08-12 and he found out three days later by
    asking why the company had disappeared from Airtable.

    No time window, unlike carryover(). A closure he has not been told about
    stays owed no matter how old, because there is no equivalent of the backlog
    report to release these and the volume is inherently tiny: something only
    lands here if he took the trouble to mark it interested.
    """
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM postings "
            "WHERE closed_detected_at IS NOT NULL AND label = 'interested' "
            "AND closure_alerted_at IS NULL "
            "ORDER BY closed_detected_at DESC, company, title"
        )
    ]


def mark_closure_alerted(conn: sqlite3.Connection, rows: list[dict]) -> int:
    """Stamp postings whose closure an email actually carried."""
    ts = now()
    unmarked = [r["id"] for r in rows if not r.get("closure_alerted_at")]
    conn.executemany(
        "UPDATE postings SET closure_alerted_at = ? WHERE id = ?",
        [(ts, i) for i in unmarked],
    )
    conn.commit()
    return len(unmarked)


def closed_since(conn: sqlite3.Connection, days: float) -> list[dict]:
    """Everything detected closed in the last N days, for the weekly summary.

    Unlike closed_interested() this repeats: it is a summary of the week rather
    than a debt owed on a specific posting, so it carries no stamp and the same
    closure legitimately appears in one roundup only because the window moves on.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(
        timespec="seconds"
    )
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM postings WHERE closed_detected_at >= ? "
            "AND prefilter_verdict = 'surface' "
            "ORDER BY closed_detected_at DESC, company, title",
            (cutoff,),
        )
    ]


# ------------------------------------------------------- Milestone 7 delivery

def _tier_clause(tiers: list[int]) -> tuple[str, list]:
    """An IN clause over tier numbers, or a clause that matches nothing.

    Matching nothing is the right answer for an empty list: no tier is routed
    here, so no posting is either. Building no clause at all would silently
    widen the query to every tier, which on the urgent path would email him
    about the whole surfaced set.
    """
    if not tiers:
        return "0", []
    return "tier IN (" + ",".join("?" for _ in tiers) + ")", list(tiers)


def roundup_candidates(
    conn: sqlite3.Connection, tiers: list[int], days: float
) -> list[dict]:
    """Open postings routed to the weekly roundup that no email has carried.

    Bounded by a window for the same reason carryover() is: unbounded, the first
    roundup empties the seeded backlog into one email, and releasing that is
    The owner's call through tools.backlog_report, not the schedule's.
    """
    clause, params = _tier_clause(tiers)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(
        timespec="seconds"
    )
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM postings "
            "WHERE closed_detected_at IS NULL AND prefilter_verdict = 'surface' "
            "AND COALESCE(closed_by_me, 0) = 0 "
            f"AND alerted_at IS NULL AND {clause} AND first_seen >= ? "
            "ORDER BY tier, fit_score DESC, company, title",
            params + [cutoff],
        )
    ]


def urgent_by_deadline(
    conn: sqlite3.Connection, tiers: list[int], hours: float
) -> list[dict]:
    """PRD section 3, trigger one. A stated deadline inside the window.

    A deadline already past is excluded. It is no longer urgent, it is missed,
    and an email about it would be the system telling him it failed.
    """
    clause, params = _tier_clause(tiers)
    now_utc = datetime.now(timezone.utc)
    today = now_utc.date().isoformat()
    limit = (now_utc + timedelta(hours=hours)).date().isoformat()
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM postings "
            "WHERE closed_detected_at IS NULL AND prefilter_verdict = 'surface' "
            "AND COALESCE(closed_by_me, 0) = 0 "
            f"AND {clause} AND urgent_deadline_alerted_at IS NULL "
            "AND stated_deadline IS NOT NULL AND TRIM(stated_deadline) <> '' "
            "AND substr(stated_deadline, 1, 10) >= ? "
            "AND substr(stated_deadline, 1, 10) <= ? "
            "ORDER BY stated_deadline, company, title",
            params + [today, limit],
        )
    ]


def urgent_by_age(
    conn: sqlite3.Connection, tiers: list[int], days: float, needs_action: bool = True
) -> list[dict]:
    """PRD section 3, trigger two. Open too long with nothing logged against it.

    A label counts as action. Marking something interested and not having applied
    yet is a decision he made, not neglect, and nagging about it is how an urgent
    email becomes one he stops opening. Set stale_needs_action false in
    sources/email.toml to nag on age alone.
    """
    clause, params = _tier_clause(tiers)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(
        timespec="seconds"
    )
    sql = (
        "SELECT * FROM postings "
        "WHERE closed_detected_at IS NULL AND prefilter_verdict = 'surface' "
        "AND COALESCE(closed_by_me, 0) = 0 "
        f"AND {clause} AND urgent_stale_alerted_at IS NULL AND first_seen <= ?"
    )
    if needs_action:
        sql += (
            " AND COALESCE(TRIM(label), '') = '' "
            "AND COALESCE(applied_status, 'not_applied') = 'not_applied'"
        )
    sql += " ORDER BY first_seen, company, title"
    return [dict(r) for r in conn.execute(sql, params + [cutoff])]


def action_items(
    conn: sqlite3.Connection, waiting_statuses: list[str]
) -> dict[str, list[dict]]:
    """The owner's own open loops. Two lists, and neither is a discovery alert.

    Everything else this module offers answers "what has the agent found that it
    has not told him about", and is stamped once carried so it is never offered
    again. This answers "what has he started and not finished", which is true
    until he finishes it. So nothing here is ever stamped and every list repeats
    on every digest until it empties. CLAUDE.md rule 10 governs the stamps and
    this is deliberately outside it: there is no stamp to write.

    to_apply is the whole point. A posting he marked interested and has not
    applied to is the highest-value thing in the database, because he has
    already done the judging and the only thing left is the part that gets him
    the job. Ordered by stated deadline first and age second, so whatever is
    closest to being lost leads.

    waiting is the pipeline. Which statuses count as still waiting is data in
    sources/email.toml, not a list in here, because "does an offer still need
    chasing" is a question about his search and not about the schema.
    """
    to_apply = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM postings "
            "WHERE prefilter_verdict = 'surface' AND closed_detected_at IS NULL "
            "AND COALESCE(closed_by_me, 0) = 0 "
            "AND COALESCE(label, '') = 'interested' "
            "AND COALESCE(applied_status, 'not_applied') = 'not_applied' "
            "ORDER BY CASE WHEN TRIM(COALESCE(stated_deadline, '')) = '' "
            "         THEN 1 ELSE 0 END, stated_deadline, first_seen"
        )
    ]

    waiting: list[dict] = []
    if waiting_statuses:
        marks = ",".join("?" for _ in waiting_statuses)
        waiting = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM postings "
                f"WHERE applied_status IN ({marks}) "
                "ORDER BY COALESCE(applied_at, first_seen)",
                list(waiting_statuses),
            )
        ]

    return {"to_apply": to_apply, "waiting": waiting}


def mark_urgent(conn: sqlite3.Connection, rows: list[dict], column: str) -> int:
    """Stamp one urgent trigger on postings an email actually carried.

    The column name is chosen by the caller from a fixed pair, never built from
    anything outside this module, so this cannot become a way to write an
    arbitrary column.
    """
    if column not in ("urgent_deadline_alerted_at", "urgent_stale_alerted_at"):
        raise ValueError(f"not an urgent stamp column: {column}")
    ts = now()
    unmarked = [r["id"] for r in rows if not r.get(column)]
    conn.executemany(
        f"UPDATE postings SET {column} = ? WHERE id = ?",
        [(ts, i) for i in unmarked],
    )
    conn.commit()
    return len(unmarked)


def get_state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute(
        "SELECT value FROM agent_state WHERE key = ?", (key,)
    ).fetchone()
    return row["value"] if row else None


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO agent_state (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
        "updated_at=excluded.updated_at",
        (key, value, now()),
    )
    conn.commit()


def mark_alerted(conn: sqlite3.Connection, rows: list[dict], alert_type: str) -> int:
    """Stamp alerted_at on postings an email actually carried.

    Success criterion 1 audits coverage by looking for postings that closed
    without ever being alerted on, so this stamp is what makes that audit mean
    anything. It also stops carryover() offering the same posting twice.
    Postings already stamped keep their original timestamp.
    """
    ts = now()
    unmarked = [r["id"] for r in rows if not r.get("alerted_at")]
    conn.executemany(
        "UPDATE postings SET alerted_at = ?, alert_type = ? WHERE id = ?",
        [(ts, alert_type, i) for i in unmarked],
    )
    conn.commit()
    return len(unmarked)
