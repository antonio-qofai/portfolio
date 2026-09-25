"""Turn extracted items into unconfirmed Airtable rows, and write them.

Two halves. plan() is pure: given what already exists and what was just
extracted, it returns the rows to create. ProposalStore is the thin I/O
shell around the v1 Airtable client: one read per table, one write per
table that has anything to write.

Every row this module produces has no Confirmed field at all, so Airtable
creates it unticked, and nothing unticked affects generation
(schedule._confirmed_visits_by_week). _refuse_confirmation() checks that on
the way out, so a later edit that tries to confirm something fails before
reaching the network (criterion L5).

De-duplication is by Message-ID, which makes a re-run a no-op (L3), and
for visits also by date. The landlord sends a reminder the day before a
visit with a new Message-ID, and two confirmed rows for the same week would
stop the Monday run with an error.
"""

from dataclasses import dataclass
from datetime import timezone

from config.airtable_fields import DEFAULT_FIELD_CONFIG
from src.extract import VISIT


@dataclass(frozen=True)
class Existing:
    """What the base already holds, as far as proposing is concerned."""

    message_ids: frozenset
    visit_dates: frozenset  # ISO date strings, confirmed or not


@dataclass(frozen=True)
class Row:
    table: str
    fields: dict
    kind: str
    item: object  # the extract.Item it came from


def plan(extracted, existing, visit_table, visit_date_field, fields):
    """Return (rows, notes).

    extracted is a list of (Message, [Item]) pairs. fields is a
    ProposalFieldConfig. Messages whose Message-ID is already in the base
    are skipped whole.
    """
    rows, notes = [], []
    seen_dates = set(existing.visit_dates)
    for message, items in extracted:
        if message.message_id in existing.message_ids:
            notes.append("%s already proposed; skipped." % message.message_id)
            continue
        for item in items:
            if item.kind == VISIT:
                day = item.date.isoformat()
                if day in seen_dates:
                    notes.append("A visit on %s is already in the base; skipped." % day)
                    continue
                seen_dates.add(day)
                rows.append(Row(visit_table, _visit_fields(item, message, visit_date_field, fields), VISIT, item))
            else:
                rows.append(Row(fields.table_requests, _request_fields(item, message, fields), item.kind, item))
    _refuse_confirmation(rows, fields)
    return rows, notes


def _visit_fields(item, message, visit_date_field, fields):
    f = fields.visits
    return {
        f.name: item.summary,
        visit_date_field: item.date.isoformat(),
        f.source_message: message.message_id,
        f.raw_excerpt: item.excerpt,
    }


def _request_fields(item, message, fields):
    f = fields.requests
    row = {
        f.summary: item.summary,
        f.detail: item.detail,
        f.source_message: message.message_id,
        f.received: message.sent_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        f.raw_excerpt: item.excerpt,
    }
    if item.date is not None:
        row[f.due] = item.date.isoformat()
    return row


def _refuse_confirmation(rows, fields):
    forbidden = {
        fields.request_confirmed.casefold(),
        DEFAULT_FIELD_CONFIG.cleaner_visits.confirmed.casefold(),
    }
    for row in rows:
        touched = {name.casefold() for name in row.fields} & forbidden
        if touched:
            raise AssertionError(
                "refusing to write %s to %s: the inbox reader never confirms anything"
                % (sorted(touched), row.table)
            )


class ProposalStore:
    """Reads what exists and writes new proposals, through the v1 client.

    Uses the client's generic fetch and create helpers rather than adding
    methods to it, so the Monday run's client is unchanged by v1.1.
    """

    def __init__(self, client, visit_table, visit_date_field, fields):
        self._client = client
        self._visit_table = visit_table
        self._visit_date_field = visit_date_field
        self._fields = fields

    def existing(self):
        """Two API calls: Cleaner Visits and Requests, once each."""
        visits = self._client._fetch_all(self._visit_table)
        requests = self._client._fetch_all(self._fields.table_requests)
        ids, dates = set(), set()
        for record in visits:
            f = record.get("fields", {})
            if f.get(self._fields.visits.source_message):
                ids.add(str(f[self._fields.visits.source_message]).strip())
            if f.get(self._visit_date_field):
                dates.add(str(f[self._visit_date_field])[:10])
        for record in requests:
            value = record.get("fields", {}).get(self._fields.requests.source_message)
            if value:
                ids.add(str(value).strip())
        return Existing(message_ids=frozenset(ids), visit_dates=frozenset(dates))

    def write(self, rows):
        _refuse_confirmation(rows, self._fields)
        by_table = {}
        for row in rows:
            by_table.setdefault(row.table, []).append(row.fields)
        for table, field_dicts in by_table.items():
            self._client._create_records(table, field_dicts)
