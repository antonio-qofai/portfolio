"""Airtable names the landlord email reader writes to.

Kept apart from config/airtable_fields.py so the reader can be added and
changed without touching the configuration the Monday run depends on. The
Cleaner Visits table name and date field come from there, so the two can
never disagree about which table holds visits.

There is deliberately no Confirmed field in either shape. The reader never
writes one, and a name that is not here cannot be written by accident.
scripts/setup_inbox_tables.py creates the Requests table's Confirmed
checkbox itself, for humans to tick.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class _ProposedVisitFields:
    name: str            # Single line text — primary field; the reader writes a short summary
    source_message: str  # Single line text — RFC 822 Message-ID of the email
    raw_excerpt: str     # Long text — the sentence the date was read from


@dataclass(frozen=True)
class _RequestFields:
    summary: str         # Single line text — primary field
    detail: str          # Long text
    due: str             # Date — optional
    source_message: str  # Single line text — RFC 822 Message-ID of the email
    received: str        # Date + time — when the email was sent
    raw_excerpt: str     # Long text — the sentence the request was read from


@dataclass(frozen=True)
class ProposalFieldConfig:
    table_requests: str
    visits: _ProposedVisitFields
    requests: _RequestFields
    request_confirmed: str  # Created by the setup script only; never written


DEFAULT_PROPOSAL_FIELDS = ProposalFieldConfig(
    table_requests="Requests",
    visits=_ProposedVisitFields(
        name="Name",
        source_message="Source message",
        raw_excerpt="Raw excerpt",
    ),
    requests=_RequestFields(
        summary="Summary",
        detail="Detail",
        due="Due",
        source_message="Source message",
        received="Received",
        raw_excerpt="Raw excerpt",
    ),
    request_confirmed="Confirmed",
)
