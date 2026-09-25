"""Two-way sync between SQLite and the Airtable base. Milestone 5.

SQLite is the source of truth. Airtable is where the owner reads and labels, and
those labels come back here. PRD section 6.

Direction matters and the order is deliberate:

  1. Reconcile. Read every Airtable posting row once and match it to a posting
     by its Hash field, repairing any stored record id that went stale because a
     row was deleted by hand.
  2. Pull. Read the owner's five editable fields into SQLite. This runs first so a
     label written since the last sync is never lost.
  3. Prune. Delete rows for postings that are closed and already labelled. They
     are resolved, SQLite still has them, and the free plan caps a base at
     roughly a thousand records.
  4. Push. Refresh the agent-written fields on rows that exist, then create rows
     for the newest surfaced postings up to the cap.

The push never writes an editable field. The owner's edits are inputs, and an
agent that overwrites its own input surface destroys the only training signal
the ranker has.

Which Airtable field carries which SQLite column is `sources/airtable.toml`.
Nothing in this module names a field.
"""

from dataclasses import dataclass, field

from . import airtable, config, db, delivery, grouping, rubric


@dataclass
class Report:
    reconciled: int = 0
    labels_pulled: int = 0
    applied_stamped: int = 0
    pruned: int = 0
    companies_pruned: int = 0
    orphan_names: list[str] = field(default_factory=list)
    updated: int = 0
    unchanged: int = 0
    created: int = 0
    slice_size: int = 0
    collapsed_rows: int = 0
    collapsed_pruned: int = 0
    contacts: int = 0
    surfaced_total: int = 0
    records_after: int = 0
    cap: int = 0
    dry_run: bool = False

    def over_cap(self) -> bool:
        return self.records_after >= self.cap


def _as_date(value):
    """Airtable date fields want YYYY-MM-DD; SQLite holds full timestamps."""
    if not value:
        return None
    return str(value).split("T")[0]


def _airtable_value(fld: airtable.Field, row, company_ids: dict[str, str]):
    """Turn one SQLite column into the value Airtable expects for one field."""
    if fld.type == "multipleRecordLinks":
        rec = company_ids.get(str(row[fld.column] or "").strip().lower())
        return [rec] if rec else []
    value = row[fld.column] if fld.column in row.keys() else None
    if fld.type == "date":
        return _as_date(value)
    if fld.type == "number":
        return value
    if value is None:
        return None
    return str(value)


def _posting_payload(schema_table, row, company_ids) -> dict:
    """Every agent-written field. Editable fields are deliberately excluded."""
    out = {}
    for fld in schema_table.agent_fields:
        if not fld.column:
            continue
        value = _airtable_value(fld, row, company_ids)
        if value is not None:
            out[fld.name] = value
    return out


def _demotion(schema, never_tiers) -> tuple[str, tuple]:
    """One predicate for "this posting does not deserve an Airtable slot".

    Returned as a SQL fragment so the prune can use it as an OR and the push as
    a NOT of the same string. CLAUDE.md rule 9 says every prune must be paired
    with a push exclusion or the row is deleted and recreated on every run
    forever; building one string and negating it makes that pairing structural,
    where two hand-written clauses are two things that can drift apart. The
    sixth prune rule had them written out twice for about an hour and that was
    already one copy too many.

    Two reasons a posting is demoted, and one set of exemptions.

    TIER. A band `rubric.md` marks delivery "never". Nothing will ever email it,
    so it should not hold one of 600 slots either.

    AGE. Added 2026-09-20 because this was the real complaint. Rows only ever
    left the base when they resolved, so a posting that stayed open and
    unlabelled held its slot indefinitely. Measured that day: 335 of 600 slots
    were held by postings 30 or more days old while 1,040 postings from the
    previous month could not get in. The base was not a view of the search, it
    was a photograph of early August.

    Age is the only safe basis for an eviction here, and that is the whole
    reason it is age rather than score. A posting only gets older, so eviction
    is one-way: nothing evicted for age can become young again and be recreated
    next run. An eviction on rank would oscillate every time a score moved,
    which is the churn rule 9 is written about and would burn the monthly quota
    while stranding record ids. Do not replace this with a score.

    EXEMPTIONS, which are the same principle as the prefilter's label override.
    A role he marked interested stays whatever its age or tier, because his yes
    outranks anything computed. A role he applied to stays, because the pipeline
    is the one thing the base must never lose. And the tiers in
    `age_exempt_tiers` stay however old they are, because a tier 1 role open for
    two months is still the best thing on the page.
    """
    tests: list[str] = []
    params: list = []
    if never_tiers:
        marks = ", ".join("?" for _ in never_tiers)
        # COALESCE, not a bare `tier IN (...)`. An unscored posting has a NULL
        # tier, `NULL IN (4)` is NULL rather than false, and the NULL survives
        # the OR and the ANDs to make the whole predicate NULL. The prune reads
        # that as "do not prune", which is harmless, but the push reads
        # `NOT NULL` as NULL and drops the row from the candidate set entirely.
        # That silently locked all 733 unscored postings out of the base on the
        # first dry run of this rule, which is the same three-valued-logic trap
        # the not_interested clause below already carries a comment about.
        tests.append(f"COALESCE(tier, 0) IN ({marks})")
        params.extend(never_tiers)
    if schema.max_row_age_days and schema.max_row_age_days > 0:
        age = "julianday('now') - julianday(first_seen) > ?"
        params.append(schema.max_row_age_days)
        exempt = [t for t in (schema.age_exempt_tiers or []) if t]
        if exempt:
            marks = ", ".join("?" for _ in exempt)
            age += f" AND COALESCE(tier, 0) NOT IN ({marks})"
            params.extend(exempt)
        tests.append(f"({age})")
    if not tests:
        return "", ()
    return (
        "(("
        + " OR ".join(tests)
        + ") AND COALESCE(label, '') <> 'interested'"
        + " AND COALESCE(applied_status, 'not_applied') = 'not_applied')"
    ), tuple(params)


# The push candidate query, as one string with its parameters, so the assembly
# is testable without a client or a network. It lived inline in `run` until
# 2026-09-20, which is how a parameter-binding bug reached the base: the WHERE
# clause is built before ORDER BY so its placeholders bind first, the two
# parameter tuples were concatenated the other way round, and `tier IN (?)` was
# handed the feed prefix while `source LIKE ?` was handed a tier number. Both
# the push exclusion and the provenance ordering silently stopped working and
# the dry run reported nothing unusual. `tools.test_airtable_slice` calls this
# function, so the ordering is now covered by a case rather than by care.
CANDIDATE_BASE = (
    "prefilter_verdict='surface' "
    "AND COALESCE(closed_by_me, 0) = 0 "
    # A closed posting keeps its row when he labelled it interested OR applied
    # to it. The applied half was added 2026-09-25: his JP Morgan application
    # vanished from the base the day that posting closed, so the Applied view
    # could never show it. An application is a fact about him, not about whether
    # the employer is still listing the role.
    "AND (closed_detected_at IS NULL "
    "     OR COALESCE(label, '') = 'interested' "
    "     OR COALESCE(applied_status, 'not_applied') <> 'not_applied') "
    "AND NOT (COALESCE(label, '') = 'not_interested' "
    "         AND TRIM(COALESCE(label_reason, '')) <> '')"
)


def candidate_query(schema, never_tiers, select: str = "*") -> tuple[str, tuple]:
    """(sql, params) for the postings that deserve an Airtable slot, best first.

    Concatenated in statement order, always: WHERE parameters then ORDER BY
    parameters. Returning them together is the point; two tuples assembled by
    the caller is what went wrong.
    """
    demote_clause, demote_params = _demotion(schema, never_tiers)
    order = "first_seen DESC, id DESC"
    order_params: tuple = ()
    if schema.prefer_company_boards:
        order = "(source LIKE ?) ASC, " + order
        order_params = (schema.feed_source_prefix + "%",)
    if getattr(schema, "sort_by_tier", False):
        order = "CASE WHEN COALESCE(tier, 0) = 0 THEN 9 ELSE tier END ASC, " + order
    push = f" AND NOT {demote_clause}" if demote_clause else ""
    sql = f"SELECT {select} FROM postings WHERE {CANDIDATE_BASE}{push} ORDER BY {order}"
    return sql, demote_params + order_params


def prune_query(schema, never_tiers) -> tuple[str, tuple]:
    """(sql, params) for base rows that no longer deserve their slot.

    The demotion half is the exact clause `candidate_query` negates. Rule 9's
    pairing is structural because both read the same `_demotion`.
    """
    demote_clause, demote_params = _demotion(schema, never_tiers)
    extra = f" OR {demote_clause}" if demote_clause else ""
    sql = (
        "SELECT hash FROM postings WHERE airtable_record_id IS NOT NULL "
        "AND (prefilter_verdict = 'killed' "
        "     OR COALESCE(closed_by_me, 0) = 1 "
        # Paired with the candidate clause above. Both halves gained the
        # applied exemption together on 2026-09-25; separating them deletes and
        # recreates every applied-and-closed row on every run.
        "     OR (closed_detected_at IS NOT NULL "
        "         AND COALESCE(label, '') <> 'interested' "
        "         AND COALESCE(applied_status, 'not_applied') = 'not_applied') "
        "     OR (closed_detected_at IS NULL "
        "         AND COALESCE(label, '') = 'not_interested' "
        "         AND TRIM(COALESCE(label_reason, '')) <> '')"
        + extra + ")"
    )
    return sql, demote_params


def _create_payload(schema_table, row, company_ids) -> dict:
    """The agent's fields, plus any label the owner has already written.

    A create carries the editable fields; an update never does. The asymmetry is
    the point. An update must leave them alone because Airtable is the input
    surface and he may be mid-edit, but a brand new row has nothing in those
    cells, and step 2 of the sync reads Airtable as authoritative for them.

    Without this, creating a row for a posting that already carries a label in
    SQLite writes a blank Label to Airtable, and the next sync pulls that blank
    back down and erases the label. That is not hypothetical: on 2026-08-15,
    re-creating the seven closed postings marked interested queued exactly that,
    seven labels and four reasons about to be destroyed on the following run.

    Harmless before then only because every create had been a genuinely new
    posting with nothing in SQLite to lose.
    """
    out = _posting_payload(schema_table, row, company_ids)
    for fld in schema_table.editable_fields:
        if not fld.column:
            continue
        stored = row[fld.column] if fld.column in row.keys() else None
        if stored is None or str(stored).strip() == "":
            continue
        # A stored value equal to the field's empty_value is already what a
        # blank Airtable cell means, so writing it says nothing and only adds
        # noise. applied_status is the case that matters: every posting starts
        # 'not_applied', so without this every create would stamp "not applied"
        # across the whole base to no effect.
        if fld.empty_value is not None and stored == fld.empty_value:
            continue
        shown = fld.to_airtable(stored)
        if shown is not None:
            out[fld.name] = shown
    return out


def _applied_stamp(row, incoming):
    """When applied_at should change, or None when it should not.

    The owner sets a dropdown in Airtable and never types a date, so the date has
    to come from the sync noticing the dropdown move. Airtable records what a
    cell says and never when it changed, which is why this cannot be read back
    later and has to be caught as it happens.

    Deliberately computed outside the "did any field differ" test in the caller.
    A row that already said applied before this column existed differs in
    nothing, so a stamp gated on a change would never reach the applications he
    had already logged, which are exactly the ones with the longest silence.

    Setting the status back to not applied clears it. The only honest reading of
    that move is that the first one was a mistake, and a stale applied_at is
    worse than none: it would date an application that was never sent.
    """
    if "applied_status" not in incoming:
        return None
    status = incoming["applied_status"] or "not_applied"
    stamped = (row["applied_at"] or "").strip()
    if status != "not_applied" and not stamped:
        return db.now()
    if status == "not_applied" and stamped:
        return ""
    return None


def _collapsed_row(group) -> dict:
    """One Airtable row standing for a role posted in several cities.

    The lead row supplies everything except three things.

    Location becomes every city the group covers, joined. That is the whole
    point of the collapse and the only field whose meaning changes.

    Tier, Fit, Reach and Reason come from the best-scored member rather than the
    lead. The lead is the oldest row, chosen for stability, and the oldest row is
    frequently the unscored one; showing its blank score would file a tier 1 role
    under no tier at all.

    The owner's five editable fields are deliberately NOT merged. The sync reads
    them back from this row by its Hash, which is the lead's, so they have to
    mean the lead's row and nothing else. Merging a label off a sibling would
    write that label onto a different posting on the next pull.
    """
    row = dict(group.lead)
    if group.collapsed:
        # Every city is shown here, never truncated. The digest truncates
        # because an email is read in a narrow column; an Airtable cell is not,
        # and a hidden city in the base is a city he cannot filter on. The
        # joiner is read from sources/email.toml so the two surfaces cannot
        # disagree about how a multi-city role prints.
        joiner = delivery.rules().get("collapse", {}).get("join", "; ")
        row["location"] = group.location_text(joiner, 0)
        best = min(group.rows, key=grouping.by_best)
        for column in ("tier", "fit_score", "reach_score", "reason", "flags"):
            row[column] = best.get(column)
    return row


def _comparable(value):
    """One field value reduced to a form that compares across the API boundary.

    Airtable leaves an empty cell out of its response entirely, while the payload
    builder still emits an empty string or an empty link list, so blank has to
    mean the same thing on both sides or every row looks changed. Numbers come
    back as ints where SQLite may hold floats, and a link field is a one element
    list whose type differs by direction.
    """
    if value is None or value == "" or value == []:
        return None
    if isinstance(value, list):
        return tuple(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    return value


def _payload_differs(payload: dict, record: dict) -> bool:
    """True when the live Airtable record does not already say what we would say.

    Only the payload's own keys are compared, which is exactly the set a PATCH
    would write. A field the payload omits is one the update could not have
    changed anyway, since Airtable's PATCH leaves unnamed fields alone.

    The owner's editable fields never appear in the payload, so a label he typed is
    never a reason to rewrite the row, and this can never overwrite one.
    """
    fields = record.get("fields", {})
    return any(
        _comparable(value) != _comparable(fields.get(name))
        for name, value in payload.items()
    )


def _existing_companies(client, conn, schema) -> dict[str, str]:
    """Map name -> record id for the company rows that already exist.

    Used when Postings no longer links to Companies. Nothing needs creating for
    a posting then, but `_prune_companies` still decides orphans from this map,
    so it has to hold every company row in the base or the rows it misses are
    never pruned and the leak continues silently.
    """
    table = schema.table("companies")
    name_field = table.fields[0].name
    out: dict[str, str] = {}
    for rec in client.records(table.name):
        key = str(rec["fields"].get(name_field, "")).strip().lower()
        if key:
            out[key] = rec["id"]
    return out


def _ensure_companies(client, conn, schema, names, dry_run) -> dict[str, str]:
    """Make sure every company in the slice has a row, and map name -> record id.

    Only companies that actually appear in the pushed slice get a row. The base
    is capped at roughly a thousand records across all tables, so syncing all
    108 tokens plus every company an aggregator feed mentions would spend the
    budget on rows nothing links to.
    """
    table = schema.table("companies")
    name_field = table.fields[0].name

    existing = {}
    for rec in client.records(table.name):
        key = str(rec["fields"].get(name_field, "")).strip().lower()
        if key:
            existing[key] = rec["id"]

    known = {
        str(r["name"]).strip().lower(): dict(r)
        for r in conn.execute("SELECT * FROM companies")
    }

    missing = [n for n in names if n and n.lower() not in existing]
    if missing and not dry_run:
        payloads = []
        for name in missing:
            row = known.get(name.lower())
            fields = {name_field: name}
            if row:
                for fld in table.fields[1:]:
                    if fld.column and row.get(fld.column) is not None:
                        fields[fld.name] = (
                            row[fld.column]
                            if fld.type == "number"
                            else str(row[fld.column])
                        )
            payloads.append(fields)
        for rec in client.create_records(table.name, payloads):
            key = str(rec["fields"].get(name_field, "")).strip().lower()
            existing[key] = rec["id"]

    return existing


def _postings_link_companies(schema) -> bool:
    """Does the Postings table still hold a link to the Companies table?

    Read from the schema rather than assumed, so unlinking is an edit to
    `sources/airtable.toml` and nothing here needs changing back if it is ever
    relinked. Two places behave differently on the answer: which companies have
    to exist before a push, and which count as linked when orphans are pruned.
    Getting the second wrong without the first is the expensive mistake, because
    every company would read as linked and none would ever be pruned, which is
    the leak this whole change exists to stop.
    """
    table = schema.table("postings")
    return any(f.type == "multipleRecordLinks" for f in table.fields)


def _prune_companies(client, conn, schema, company_ids, kept_hashes, dry_run):
    """Delete company rows that nothing links to any more.

    Companies are created on demand by `_ensure_companies` and, until 2026-08-15,
    were never deleted. Postings churn in both directions against a fixed cap
    while Companies only grew, so the base drifted toward the free plan's 1,000
    record limit carrying rows no posting and no contact pointed at. Seventeen
    had accumulated by the time anyone looked, most of them left behind when the
    filter tightenings of 2026-08-12 and 2026-08-14 pruned the last posting a
    company had. This is what stops that from being a slow leak.

    Linked-ness is decided from SQLite rather than by reading Airtable again, so
    it costs no extra API calls: `company_ids` already maps every name in the
    base to its record id.

    `kept_hashes` is the set of postings that will be in the base when this sync
    finishes, which the caller knows exactly: whatever survived the prune plus
    whatever it is about to create. Deriving it from `airtable_record_id`
    instead looks equivalent and is not, because on a dry run that column has
    not been touched yet, so the postings about to be pruned still read as
    linked. The first version did that and the dry run predicted 15 orphans
    where the real run found 23. A dry run that under-reports a deletion is
    worse than no dry run, because it is the thing being trusted before the
    deletion is allowed.

    Both link directions have to be counted. One company in the base is
    referenced only by a contact and by no posting, so a prune that looked at
    postings alone would delete the row the owner's one referral hangs off.
    """
    table = schema.table("companies")

    by_hash = {
        r[0]: r[1] for r in conn.execute("SELECT hash, company FROM postings")
    }
    # Only when postings actually link. Once Postings.Company is plain text a
    # posting's company name is just a string on the row and points at nothing,
    # so counting it as a link would keep every company row alive forever.
    linked = set()
    if _postings_link_companies(schema):
        linked = {
            str(by_hash[h]).strip().lower()
            for h in kept_hashes
            if by_hash.get(h)
        }
    linked |= {
        str(r[0]).strip().lower()
        for r in conn.execute(
            "SELECT DISTINCT c.name FROM contacts k "
            "JOIN companies c ON c.id = k.company_id"
        )
        if r[0]
    }

    orphans = {n: rid for n, rid in company_ids.items() if n not in linked}
    if orphans and not dry_run:
        client.delete_records(table.name, list(orphans.values()))
    return len(orphans), sorted(orphans)


def _push_contacts(client, conn, schema, company_ids, dry_run) -> int:
    """Mirror the contacts table into Airtable, matching on name.

    Contacts are the owner's data and few, so this pushes all of them and never
    deletes. A company a contact points at may not be in the pushed posting
    slice, so its record id is looked up and created here if missing.
    """
    table = schema.table("contacts")
    name_field = table.fields[0].name

    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT c.*, co.name AS company_name FROM contacts c "
            "LEFT JOIN companies co ON co.id = c.company_id"
        )
    ]
    if not rows:
        return 0

    needed = sorted({r["company_name"] for r in rows if r["company_name"]})
    company_ids = dict(company_ids)
    company_ids.update(
        _ensure_companies(client, conn, schema, needed, dry_run)
    )

    existing = {
        str(rec["fields"].get(name_field, "")).strip().lower(): rec["id"]
        for rec in client.records(table.name)
    }

    creates, updates = [], []
    for row in rows:
        fields = {}
        for fld in table.fields:
            if not fld.column:
                continue
            if fld.type == "multipleRecordLinks":
                rec = company_ids.get(str(row["company_name"] or "").strip().lower())
                fields[fld.name] = [rec] if rec else []
            elif fld.type == "date":
                value = _as_date(row.get(fld.column))
                if value:
                    fields[fld.name] = value
            elif row.get(fld.column) is not None:
                fields[fld.name] = str(row[fld.column])
        key = str(row["name"]).strip().lower()
        if key in existing:
            updates.append({"id": existing[key], "fields": fields})
        else:
            creates.append(fields)

    if dry_run:
        return len(creates) + len(updates)

    done = 0
    if updates:
        done += client.update_records(table.name, updates)
    if creates:
        done += len(client.create_records(table.name, creates))
    return done


def sync(dry_run: bool = False, conn=None) -> Report:
    schema = airtable.load_schema()
    table = schema.table("postings")
    hash_field = next(f for f in table.fields if f.column == "hash")

    owns_conn = conn is None
    conn = conn or db.connect()
    report = Report(cap=schema.max_posting_records, dry_run=dry_run)

    client = airtable.Client()
    try:
        # 1. Reconcile against what is actually in the base.
        live = {}
        for rec in client.records(table.name):
            h = rec["fields"].get(hash_field.name)
            if h:
                live[h] = rec

        stored = {
            r["hash"]: dict(r)
            for r in conn.execute(
                "SELECT hash, airtable_record_id FROM postings "
                "WHERE airtable_record_id IS NOT NULL"
            )
        }
        for h, row in stored.items():
            actual = live.get(h, {}).get("id")
            if actual != row["airtable_record_id"]:
                report.reconciled += 1
                if not dry_run:
                    conn.execute(
                        "UPDATE postings SET airtable_record_id=? WHERE hash=?",
                        (actual, h),
                    )
        if not dry_run:
            conn.commit()

        # 2. Pull the owner's edits into SQLite before anything overwrites them.
        #    Only rows whose values actually differ are written, so the count
        #    reports real changes rather than the size of the base.
        editable = [f for f in table.editable_fields if f.column]
        if editable:
            columns = ", ".join(f.column for f in editable)
            current = {
                r["hash"]: dict(r)
                for r in conn.execute(
                    f"SELECT hash, applied_at, {columns} FROM postings"
                )
            }
            for h, rec in live.items():
                row = current.get(h)
                if row is None:
                    continue
                incoming = {
                    f.column: f.to_db(rec["fields"].get(f.name)) for f in editable
                }
                changed = [c for c, v in incoming.items() if row[c] != v]
                stamp = _applied_stamp(row, incoming)
                if stamp is not None:
                    # "" is the clear case and has to reach SQLite as NULL, not
                    # as an empty string: every reader of this column tests it
                    # for null, and an empty string is truthy to SQL's IS NULL.
                    incoming["applied_at"] = stamp or None
                if not changed and stamp is None:
                    continue
                if changed:
                    report.labels_pulled += 1
                if stamp is not None:
                    report.applied_stamped += 1
                if not dry_run:
                    sets = ", ".join(f"{c}=?" for c in incoming)
                    conn.execute(
                        f"UPDATE postings SET {sets} WHERE hash=?",
                        list(incoming.values()) + [h],
                    )
            if not dry_run:
                conn.commit()

        # 3. Prune rows that no longer belong in the base. Three reasons now:
        #    the second was added 2026-08-12 and the third on 2026-08-15.
        #
        #    Killed: the base is meant to mirror what the filter surfaces, and
        #    tightening a rule has to be able to take a posting back out. Before
        #    this, editing sources/prefilter.toml removed a posting from the
        #    digest and from scoring but left it sitting in Airtable forever,
        #    because prune only ever looked at closed rows. The owner tightened
        #    four rules that day and 125 rows were stranded, including the exact
        #    "Law School Student Ambassador" posting that prompted the change.
        #
        #    Closed and not being pursued: the door is shut and he either said no
        #    or never looked. Nothing is lost, since SQLite keeps every field.
        #
        #    Judged and finished with: still open, marked not interested, and a
        #    reason written. The reason is the condition on purpose. A pruned row
        #    can never be edited again, so this only takes the ones he actually
        #    completed and leaves a blank-reason row sitting in front of him
        #    until he writes the sentence. On 2026-08-15 that split 9 and 3.
        #
        #    Deliberately NOT pruned: a closed posting he marked interested.
        #    Those used to go, which is how a Mistral role he was applying to
        #    vanished from the base on 2026-08-12 without anything saying so.
        #    Seven had disappeared that way. They cost seven records and they are
        #    the ones he most wants to still be able to see.
        #
        #    Hidden by the location collapse: one of several cities of a role
        #    that another row already stands for. Added 2026-08-16. This is the
        #    fourth prune rule and the only one whose paired push exclusion is
        #    not automatic, so the two are computed together below rather than in
        #    two places that could drift. CLAUDE.md rule 9.
        #
        #    Closed by the owner: he ticked Closed because he learned the role was
        #    gone before the watcher did. Added 2026-08-19, and it is the fifth
        #    rule whose push exclusion is not automatic, so the query below
        #    carries the matching clause. Without the pair the row is deleted
        #    here and recreated by the push on the same run, forever, which is
        #    the failure rule 9 is written about.
        #
        #    Unlike every other prune rule this one is reversible from his side.
        #    Unticking the box restores the row on the next run, because the
        #    prune is a consequence of the column rather than a decision of its
        #    own. That only works while nothing else reads the deletion as
        #    meaning anything, which is why his tick writes closed_by_me and
        #    never closed_detected_at.
        #    Sixth rule, added 2026-09-20: a tier the rubric will never deliver.
        #
        #    The tiers come from `rubric.md`, from the bands whose delivery is
        #    "never", rather than from a number written here. That keeps one
        #    definition of what a tier means, per CLAUDE.md rule 3: if the owner
        #    moves tier 4 back onto the Sunday roundup, its rows return without
        #    a code change. Today that is tier 4 alone, 43 rows of the 600.
        #
        #    Two exemptions, and both are the same principle as the prefilter's
        #    label override. A role he marked interested stays whatever the
        #    model scored it, because his yes beats the ranker's no. A role he
        #    applied to stays, because the pipeline is the one thing the base
        #    must never lose.
        #
        #    Like the closed_by_me rule above, this one's push exclusion is NOT
        #    automatic, so the candidate query below carries the matching
        #    clause. The residual churn is a re-score moving a posting out of
        #    the band, which recreates the row; scoring happens once per posting
        #    so that is rare, and a retitle already resets the score anyway.
        never_tiers = (
            rubric.tiers_with_delivery("never")
            if schema.prune_never_delivered_tiers
            else []
        )
        prune_sql, prune_params = prune_query(schema, never_tiers)

        resolved = [r["hash"] for r in conn.execute(prune_sql, prune_params)]

        # The push slice, computed here rather than after the prune because the
        # collapse decides both halves at once: a city that is not the lead of
        # its group is pruned from the base AND excluded from the push, and
        # doing either without the other deletes and recreates the same row on
        # every run forever.
        #
        # Ordering, exclusions and their parameters all come from
        # `candidate_query`, so the statement is assembled in one place and
        # `tools.test_airtable_slice` can run the real thing. See the notes on
        # that function and on `_demotion`.
        #
        # This orders CANDIDATES for slots the prune has already freed. It is
        # never an eviction on rank: a row stays until a prune rule resolves
        # it, and the only eviction is by age, which is one-way. Enforcing a
        # score order by deleting rows that fall out of it is the rule 9 churn
        # trap and would strand a label on every reshuffle.
        cand_sql, cand_params = candidate_query(schema, never_tiers)
        rows = [dict(r) for r in conn.execute(cand_sql, cand_params)]
        # by_oldest, never by_best. A lead that moved when a score landed would
        # delete one record and create another every time the ranker touched the
        # group. Row ids only increase, so the oldest member is a lead that never
        # moves while the group's membership holds.
        groups = grouping.collapse(
            rows, lead_key=grouping.by_oldest, enabled=schema.collapse_locations
        )
        candidates = [_collapsed_row(g) for g in groups]
        report.collapsed_rows = sum(g.collapsed for g in groups)

        leads = {g.lead["hash"] for g in groups}
        hidden = [r["hash"] for r in rows if r["hash"] not in leads]
        report.collapsed_pruned = sum(1 for h in hidden if h in live)
        resolved = list(dict.fromkeys(resolved + hidden))

        prunable = [live[h]["id"] for h in resolved if h in live]
        report.pruned = len(prunable)
        if prunable and not dry_run:
            client.delete_records(table.name, prunable)
            conn.executemany(
                "UPDATE postings SET airtable_record_id=NULL WHERE hash=?",
                [(h,) for h in resolved],
            )
            conn.commit()
        # Dropped from the local picture on a dry run too. `live` is never
        # written back, and leaving pruned rows in it made the dry run report
        # room as zero and creates as none, so it understated its own effect on
        # exactly the run someone reads before allowing a deletion.
        for h in resolved:
            live.pop(h, None)

        # 4. Push. Newest surfaced postings first, bounded by the record cap.
        report.surfaced_total = conn.execute(
            "SELECT COUNT(*) FROM postings "
            "WHERE prefilter_verdict='surface' AND closed_detected_at IS NULL"
        ).fetchone()[0]

        room = max(0, schema.max_posting_records - len(live))
        to_create = [r for r in candidates if r["hash"] not in live][:room]
        to_update = [r for r in candidates if r["hash"] in live]
        report.slice_size = len(to_create) + len(to_update)

        # Companies are created for the postings that link to them, and when
        # nothing links they are created only for contacts, inside
        # `_push_contacts`. `company_ids` still has to be a dict of what exists,
        # because `_prune_companies` decides orphans from it.
        if _postings_link_companies(schema):
            company_names = sorted(
                {str(r["company"]).strip() for r in to_create + to_update
                 if r["company"]}
            )
            company_ids = _ensure_companies(
                client, conn, schema, company_names, dry_run
            )
        else:
            company_ids = _existing_companies(client, conn, schema)

        # Only rows whose agent-written fields actually differ are pushed. The
        # live records were already downloaded in step 1, so this comparison
        # costs no extra API calls and saves almost all of them: on the free
        # plan a full base is 29 write calls, and until 2026-08-15 every run
        # spent them rewriting 282 rows to the values they already held. Four
        # runs a day of that is roughly 4,300 calls a month against a 1,000
        # call limit, which is what triggered Airtable's grace period notice.
        updates = []
        for r in to_update:
            record = live[r["hash"]]
            payload = _posting_payload(table, r, company_ids)
            if not _payload_differs(payload, record):
                continue
            updates.append({"id": record["id"], "fields": payload})
        report.unchanged = len(to_update) - len(updates)
        if updates and not dry_run:
            report.updated = client.update_records(table.name, updates)
        elif dry_run:
            report.updated = len(updates)

        creates = [_create_payload(table, r, company_ids) for r in to_create]
        if creates and not dry_run:
            made = client.create_records(table.name, creates)
            stamp = db.now()
            conn.executemany(
                "UPDATE postings SET airtable_record_id=?, airtable_synced_at=? "
                "WHERE hash=?",
                [
                    (rec["id"], stamp, rec["fields"].get(hash_field.name))
                    for rec in made
                ],
            )
            conn.commit()
            report.created = len(made)
        elif dry_run:
            report.created = len(creates)

        report.records_after = len(live) + report.created

        # 5. Contacts. Small, and the whole point of PRD section 7 is that a
        #    contact shows up beside a posting from that company, which needs
        #    the link to exist on the Airtable side too.
        report.contacts = _push_contacts(client, conn, schema, company_ids, dry_run)

        # 6. Companies nothing points at any more. Last, because it reads which
        #    postings ended up in the base and step 4 is what decides that.
        report.companies_pruned, report.orphan_names = _prune_companies(
            client,
            conn,
            schema,
            company_ids,
            set(live) | {r["hash"] for r in to_create},
            dry_run,
        )

        return report
    finally:
        client.close()
        if owns_conn:
            conn.close()
