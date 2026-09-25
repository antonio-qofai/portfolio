"""Airtable client.

Reads Roster, Chores, Rules, Cleaner Visits, and Assignments; writes
Assignments. `assignment_rows` is read-only and exists for the report
exporter (src/report.py). Converts raw Airtable records into the shapes the pure modules
expect, and raises loudly with the record ID and offending field when a row
is malformed.

Nothing in this file knows which apartment it is running in. All field and
table names come from the AirtableFieldConfig passed at construction time.
"""

import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime, timezone

from config.airtable_fields import AirtableFieldConfig
from src import nudge
from src.digest import Rule
from src.rotation import Chore
from src.schedule import ChoreDetail, CleanerVisit

_BASE_URL = "https://api.airtable.com/v0"
_BATCH_SIZE = 10  # Airtable create limit per request


@dataclass(frozen=True)
class Person:
    """One roster member.

    id is the Airtable record ID and is used when writing linked records.
    sort_order is the value of the Sort order field and determines rotation
    position; it is not meaningful beyond that.
    """

    id: str
    name: str
    email: str
    active: bool
    sort_order: int

    def __str__(self):
        return self.name

    def __format__(self, spec):
        return format(self.name, spec)


@dataclass(frozen=True)
class AssignmentSnapshot:
    """Everything one run needs from the Assignments table, read once.

    All four of these derive from the same rows. Fetching them separately
    meant reading the table four times per run, and Airtable's free tier
    meters API calls per month over a whole workspace, so the difference
    is not academic.

    Taken before anything is written, which is safe: the only week a run
    writes is the current one, and nothing in the current week can be past
    due yet, so a freshly written week has nothing to say about nudges.
    """

    week_numbers: frozenset
    completed_ids: frozenset
    nudge_log: tuple
    record_ids: dict


@dataclass(frozen=True)
class AssignmentRow:
    """One Assignments row as the report exporter needs it.

    Read straight off the row rather than rebuilt from the schedule, because
    the row is what people tick off: its label, task text, and due time are
    the ones they see.
    """

    label: str
    task: str
    due: datetime  # aware, UTC
    done: bool
    is_prep: bool
    assignee: object  # Person


class AirtableClient:
    """HTTP client for the chore scheduler's Airtable base."""

    def __init__(self, api_key, base_id, fields=None):
        import requests as _requests
        from config.airtable_fields import DEFAULT_FIELD_CONFIG
        self._base_id = base_id
        self._fields = fields if fields is not None else DEFAULT_FIELD_CONFIG
        self._session = _requests.Session()
        self._session.headers["Authorization"] = "Bearer " + api_key

    def roster(self):
        """Return active Person objects sorted by sort_order."""
        f = self._fields.roster
        records = self._fetch_all(self._fields.table_roster)
        people = [_parse_person(r, f) for r in records]
        active = [p for p in people if p.active]
        return sorted(active, key=lambda p: p.sort_order)

    def chores(self):
        """Return list of (Chore, ChoreDetail) pairs for all chore rows."""
        f = self._fields.chores
        records = self._fetch_all(self._fields.table_chores)
        return [_parse_chore(r, f) for r in records]

    def rules(self):
        """Return list of Rule objects for all rule rows."""
        f = self._fields.rules
        records = self._fetch_all(self._fields.table_rules)
        return [_parse_rule(r, f) for r in records]

    def cleaner_visits(self):
        """Return list of CleanerVisit objects for all visit rows."""
        f = self._fields.cleaner_visits
        records = self._fetch_all(self._fields.table_cleaner_visits)
        return [_parse_cleaner_visit(r, f) for r in records]

    def assignments(self, roster):
        """Return an AssignmentSnapshot from a single read of the table."""
        f = self._fields.assignments
        by_id = {p.id: p for p in roster}
        records = self._fetch_all(self._fields.table_assignments)

        weeks = set()
        completed = set()
        for r in records:
            week_num = r["fields"].get(f.week_number)
            if week_num is not None:
                weeks.add(int(week_num))
            if not r["fields"].get(f.done, False):
                continue
            identity = _assignment_identity(r, f, by_id)
            if identity is not None:
                completed.add(identity)

        return AssignmentSnapshot(
            week_numbers=frozenset(weeks),
            completed_ids=frozenset(completed),
            nudge_log=tuple(_parse_nudge_log(records, f, by_id)),
            record_ids=_record_ids_by_identity(records, f, by_id),
        )

    def assignment_rows(self, roster):
        """Return an AssignmentRow for every row whose assignee is on the roster.

        Rows missing an assignee, or assigned to someone no longer on the
        roster, are skipped, as in `_assignment_identity`. A row with an
        assignee but no label or a bad due time raises.
        """
        f = self._fields.assignments
        by_id = {p.id: p for p in roster}
        rows = []
        for r in self._fetch_all(self._fields.table_assignments):
            fields = r["fields"]
            assignee_ids = fields.get(f.assignee, [])
            person = by_id.get(assignee_ids[0]) if assignee_ids else None
            if person is None:
                continue
            if fields.get(f.due) is None:
                raise ValueError(
                    "Assignments record %s: field %r is empty" % (r["id"], f.due)
                )
            rows.append(
                AssignmentRow(
                    label=_require_str(fields, f.label, r["id"]),
                    task=str(fields.get(f.task, "")).strip(),
                    due=_parse_datetime(fields[f.due], r["id"], f.due),
                    done=bool(fields.get(f.done, False)),
                    is_prep=bool(fields.get(f.is_prep, False)),
                    assignee=person,
                )
            )
        return tuple(rows)

    def generated_week_numbers(self):
        """Return frozenset of week numbers that already have assignments."""
        f = self._fields.assignments
        records = self._fetch_all(self._fields.table_assignments)
        numbers = set()
        for r in records:
            week_num = r["fields"].get(f.week_number)
            if week_num is not None:
                numbers.add(int(week_num))
        return frozenset(numbers)

    def completed_assignment_ids(self, roster):
        """Return frozenset of (week_number, chore_key, assignee) for done rows.

        roster is the list returned by self.roster(). It is used to map
        Airtable record IDs back to Person objects so the identity tuples
        match those produced by schedule.build.
        """
        f = self._fields.assignments
        by_id = {p.id: p for p in roster}
        records = self._fetch_all(self._fields.table_assignments)
        ids = set()
        for r in records:
            if not r["fields"].get(f.done, False):
                continue
            identity = _assignment_identity(r, f, by_id)
            if identity is not None:
                ids.add(identity)
        return frozenset(ids)

    def assignment_record_ids(self, roster):
        """Return {(week_number, chore_key, assignee): Airtable record ID}.

        The nudge log is written back onto the assignment row, so recording a
        nudge needs the row's record ID. The pure nudge module works in
        identity tuples and knows nothing about Airtable, so this is the map
        between the two.
        """
        f = self._fields.assignments
        by_id = {p.id: p for p in roster}
        records = self._fetch_all(self._fields.table_assignments)
        return _record_ids_by_identity(records, f, by_id)

    def nudge_log(self, roster):
        """Return a NudgeRecord for every nudge already sent.

        The log lives in two datetime fields on the assignment row rather than
        in a table of its own: an assignment can be nudged at most twice, so
        there is nothing to accumulate.
        """
        f = self._fields.assignments
        by_id = {p.id: p for p in roster}
        records = self._fetch_all(self._fields.table_assignments)
        return _parse_nudge_log(records, f, by_id)

    def record_nudge(self, assignment_record_id, nudge_type, sent_at):
        """Write the moment a nudge was sent onto its assignment row.

        sent_at must be timezone-aware; it is stored as UTC. A naive datetime
        raises rather than being guessed at, because the wrong reading of it
        would shift the 48-hour follow-up window by hours.
        """
        f = self._fields.assignments
        field_name = _nudge_field_name(nudge_type, f)
        if sent_at.tzinfo is None or sent_at.utcoffset() is None:
            raise ValueError(
                "Assignments record %s: nudge timestamp %r is naive; it must "
                "carry a timezone" % (assignment_record_id, sent_at)
            )
        value = sent_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        self._update_record(
            self._fields.table_assignments,
            assignment_record_id,
            {field_name: value},
        )

    def write_assignments(self, scheduled_chores, existing=None):
        """Write assignments to Airtable, skipping weeks that already exist.

        Re-running for a week that is already present is a no-op: the existing
        rows are not modified or duplicated.

        existing is the set of week numbers already present. Pass one from a
        snapshot the caller already holds to avoid re-reading the table; left
        out, it is fetched.
        """
        if existing is None:
            existing = self.generated_week_numbers()
        by_week = {}
        for sc in scheduled_chores:
            if sc.week.number in existing:
                continue
            by_week.setdefault(sc.week.number, []).append(sc)

        f = self._fields.assignments
        for _, week_chores in sorted(by_week.items()):
            records = [_format_assignment(sc, f) for sc in week_chores]
            self._create_records(self._fields.table_assignments, records)

    def _fetch_all(self, table):
        url = "{}/{}/{}".format(
            _BASE_URL, self._base_id, urllib.parse.quote(table, safe="")
        )
        records = []
        params = {}
        while True:
            response = self._session.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            records.extend(data.get("records", []))
            offset = data.get("offset")
            if not offset:
                break
            params = {"offset": offset}
        return records

    def _update_record(self, table, record_id, field_dict):
        url = "{}/{}/{}/{}".format(
            _BASE_URL,
            self._base_id,
            urllib.parse.quote(table, safe=""),
            urllib.parse.quote(record_id, safe=""),
        )
        response = self._session.patch(url, json={"fields": field_dict}, timeout=30)
        response.raise_for_status()

    def _create_records(self, table, field_dicts):
        url = "{}/{}/{}".format(
            _BASE_URL, self._base_id, urllib.parse.quote(table, safe="")
        )
        for i in range(0, len(field_dicts), _BATCH_SIZE):
            batch = field_dicts[i : i + _BATCH_SIZE]
            payload = {"records": [{"fields": r} for r in batch]}
            response = self._session.post(url, json=payload, timeout=30)
            response.raise_for_status()


def _parse_person(record, fields):
    record_id = record["id"]
    f = record["fields"]
    name = _require_str(f, fields.name, record_id)
    email = _require_str(f, fields.email, record_id)
    if "@" not in email:
        raise ValueError(
            "Roster record %s: field %r value %r is not a valid email address"
            % (record_id, fields.email, email)
        )
    active = bool(f.get(fields.active, False))
    sort_order = _require_int(f, fields.sort_order, record_id)
    return Person(id=record_id, name=name, email=email, active=active, sort_order=sort_order)


def _parse_chore(record, fields):
    record_id = record["id"]
    f = record["fields"]
    name = _require_str(f, fields.name, record_id)
    task = _require_str(f, fields.task, record_id)
    cadence = _require_str(f, fields.cadence, record_id)
    cleaner_behaviour = _require_str(f, fields.cleaner_behaviour, record_id)
    prep_task = f.get(fields.prep_task, "")
    if isinstance(prep_task, str):
        prep_task = prep_task.strip()
    else:
        prep_task = ""
    offset = int(f.get(fields.offset, 0))
    seed = int(f.get(fields.seed, 0))
    if cleaner_behaviour == "convert_to_prep" and not prep_task:
        raise ValueError(
            "Chores record %s: cleaner behaviour is %r but field %r is empty; "
            "a confirmed cleaner week would blank the task"
            % (record_id, cleaner_behaviour, fields.prep_task)
        )
    chore = Chore(key=record_id, cadence=cadence, offset=offset, seed=seed)
    detail = ChoreDetail(
        name=name, task=task, cleaner_behaviour=cleaner_behaviour, prep_task=prep_task
    )
    return chore, detail


def _parse_rule(record, fields):
    record_id = record["id"]
    f = record["fields"]
    text = _require_str(f, fields.text, record_id)
    category = _require_str(f, fields.category, record_id)
    number_raw = f.get(fields.number)
    number = int(number_raw) if number_raw is not None else None
    active_from = _parse_date(f.get(fields.active_from), record_id, fields.active_from)
    active_until = _parse_date(f.get(fields.active_until), record_id, fields.active_until)
    return Rule(
        text=text,
        category=category,
        number=number,
        active_from=active_from,
        active_until=active_until,
    )


def _parse_cleaner_visit(record, fields):
    record_id = record["id"]
    f = record["fields"]
    visit_date_raw = f.get(fields.visit_date)
    if not visit_date_raw:
        raise ValueError(
            "Cleaner Visits record %s: field %r is missing or empty"
            % (record_id, fields.visit_date)
        )
    visit_date = _parse_date(visit_date_raw, record_id, fields.visit_date)
    confirmed = bool(f.get(fields.confirmed, False))
    return CleanerVisit(visit_date=visit_date, confirmed=confirmed)


def _format_assignment(sc, fields):
    due_utc = sc.due.astimezone(timezone.utc)
    return {
        # The primary field, and so the row title on a phone. Snapshotted at
        # generation time like the task text, for the same reason: a week
        # that has already started must not change under whoever did it.
        fields.label: sc.name,
        fields.week_number: sc.week.number,
        fields.chore: [sc.chore.key],
        fields.assignee: [sc.assignee.id],
        fields.task: sc.task,
        fields.due: due_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        fields.is_prep: sc.is_prep,
        fields.done: False,
    }


def _assignment_identity(record, fields, roster_by_id):
    """Return (week_number, chore_key, assignee) for a row, or None.

    None means the row cannot be matched to a generated assignment: it is
    missing a week, a chore link, or an assignee, or its assignee is not on
    the roster passed in. That is not an error. Rows are hand-editable and a
    week generated before someone left the roster should not crash a run; it
    simply has no counterpart in the current schedule.
    """
    f = record["fields"]
    week_number = f.get(fields.week_number)
    chore_ids = f.get(fields.chore, [])
    assignee_ids = f.get(fields.assignee, [])
    if week_number is None or not chore_ids or not assignee_ids:
        return None
    person = roster_by_id.get(assignee_ids[0])
    if person is None:
        return None
    return (int(week_number), chore_ids[0], person)


def _record_ids_by_identity(records, fields, roster_by_id):
    """Map assignment identity tuples to Airtable record IDs.

    Two rows sharing an identity means the same assignment was written twice,
    which would make the nudge log ambiguous, so it raises rather than
    silently picking one.
    """
    by_identity = {}
    for record in records:
        identity = _assignment_identity(record, fields, roster_by_id)
        if identity is None:
            continue
        if identity in by_identity:
            raise ValueError(
                "Assignments records %s and %s are both week %d of chore %r "
                "for %s; an assignment must appear exactly once"
                % (
                    by_identity[identity],
                    record["id"],
                    identity[0],
                    identity[1],
                    identity[2],
                )
            )
        by_identity[identity] = record["id"]
    return by_identity


def _parse_nudge_log(records, fields, roster_by_id):
    """Return NudgeRecords for every nudge timestamp present on the rows."""
    types_by_field = (
        (fields.first_nudge_sent, nudge.FIRST),
        (fields.followup_sent, nudge.FOLLOWUP),
    )
    log = []
    for record in records:
        identity = _assignment_identity(record, fields, roster_by_id)
        if identity is None:
            continue
        week_number, chore_key, person = identity
        for field_name, nudge_type in types_by_field:
            raw = record["fields"].get(field_name)
            if raw in (None, ""):
                continue
            log.append(
                nudge.NudgeRecord(
                    week_number=week_number,
                    chore_key=chore_key,
                    assignee=person,
                    nudge_type=nudge_type,
                    sent_at=_parse_datetime(raw, record["id"], field_name),
                )
            )
    return log


def _nudge_field_name(nudge_type, fields):
    names = {
        nudge.FIRST: fields.first_nudge_sent,
        nudge.FOLLOWUP: fields.followup_sent,
    }
    try:
        return names[nudge_type]
    except KeyError:
        raise ValueError(
            "unknown nudge type %r; known types are %s"
            % (nudge_type, sorted(names))
        ) from None


def _require_str(fields_dict, field_name, record_id):
    value = fields_dict.get(field_name, "")
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            "Record %s: field %r is missing or empty" % (record_id, field_name)
        )
    return value.strip()


def _require_int(fields_dict, field_name, record_id):
    value = fields_dict.get(field_name)
    if value is None:
        raise ValueError(
            "Record %s: field %r is missing" % (record_id, field_name)
        )
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(
            "Record %s: field %r value %r is not an integer" % (record_id, field_name, value)
        )


def _parse_date(raw, record_id, field_name):
    if raw is None:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        raise ValueError(
            "Record %s: field %r value %r is not a valid date (expected YYYY-MM-DD)"
            % (record_id, field_name, raw)
        )


def _parse_datetime(raw, record_id, field_name):
    """Parse an Airtable datetime into an aware UTC datetime.

    Airtable returns ISO 8601 with a trailing Z. A value without an offset is
    rejected rather than assumed local, because the 48-hour follow-up window
    is measured off it.
    """
    text = str(raw).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(
            "Record %s: field %r value %r is not a valid ISO 8601 datetime"
            % (record_id, field_name, raw)
        ) from None
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(
            "Record %s: field %r value %r has no timezone offset"
            % (record_id, field_name, raw)
        )
    return moment.astimezone(timezone.utc)
