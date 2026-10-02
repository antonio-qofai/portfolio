"""Reads Conference / Event Intelligence (Taylor) output: conference and
CFP tracking rows from Taylor's sandbox Airtable base.

Unlike the Content Atomizer and Value Creation Briefing readers in this
folder, which read local repo files, Taylor's agent writes to Airtable, so
this reader hits the Airtable REST API directly. The base and table were
confirmed with Taylor on 2026-07-24:

    base:  appXXXXXXXXXXXXXX
    table: tblXXXXXXXXXXXXXX   ("Sandbox Conferences")

The fields C2 pulls for calendar assembly and speaking-slot planning:

    Conference Name, Start Date, End Date, CFP Deadline,
    Speaker Applications/CFP, CFP Summary, Applying to Speak,
    Speaker Application Draft, Status

Access: an Airtable personal access token is read from the environment
(AIRTABLE_API_KEY, or AIRTABLE_TOKEN / AIRTABLE_PAT as fallbacks). Per Pat's
architecture call (PRD Section 4), Claude Code's Airtable connection is
separate from the Cowork/Claude-chat one, and the token is never hardcoded
here. Base id, table id and the field list are all parameterized with the
confirmed values as defaults, so this module can be pointed at the future
central GTM database without code changes (PRD Section 5).

Like the sibling readers, this degrades gracefully rather than crashing the
ingestion run: a missing token, an unreachable API, an auth/permission
error, or an empty table all yield an empty list plus a warning on stderr,
never an exception that would take down C2's daily run.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Any

import read_status
import requests

DEFAULT_BASE_ID = "appXXXXXXXXXXXXXX"
DEFAULT_TABLE_ID = "tblXXXXXXXXXXXXXX"  # "Sandbox Conferences"

# Airtable field name -> ConferenceRecord attribute name. Kept explicit so a
# rename on Taylor's side surfaces here as a clearly missing field rather than
# silently reshaping downstream data.
FIELD_MAP: dict[str, str] = {
    "Conference Name": "conference_name",
    "Start Date": "start_date",
    "End Date": "end_date",
    "CFP Deadline": "cfp_deadline",
    "Speaker Applications/CFP": "speaker_applications_cfp",
    "CFP Summary": "cfp_summary",
    "Applying to Speak": "applying_to_speak",
    "Speaker Application Draft": "speaker_application_draft",
    "Status": "status",
}

_TOKEN_ENV_VARS = ("AIRTABLE_API_KEY", "AIRTABLE_TOKEN", "AIRTABLE_PAT")
_API_ROOT = "https://api.airtable.com/v0"


@dataclass
class ConferenceRecord:
    record_id: str
    conference_name: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    cfp_deadline: str | None = None
    speaker_applications_cfp: Any = None
    cfp_summary: str | None = None
    applying_to_speak: Any = None
    speaker_application_draft: str | None = None
    status: str | None = None
    # Everything Airtable returned for this row, so nothing is lost if Taylor
    # adds fields C2 does not yet map.
    raw_fields: dict[str, Any] = field(default_factory=dict)


def _resolve_token(token: str | None) -> str | None:
    if token:
        return token
    for var in _TOKEN_ENV_VARS:
        value = os.environ.get(var)
        if value:
            return value
    return None


def token_configured(token: str | None = None) -> bool:
    """Whether a token is available at all, without reading the table.

    Exists for the ingest runner, and specifically for gap detection behind
    it. `read_conferences` degrades an unset token to an empty list on
    purpose, so that one absent source cannot take the daily run down. The
    cost of that contract is that "the table is empty", "the token is unset",
    and "the token is wrong" all arrive downstream as the same empty list, and
    the first is normal while the second is a five-minute fix nobody knows is
    needed. This answers the second case cheaply and truthfully: it checks the
    environment, never the network, so it is safe to call on every run and
    says nothing about whether a token that exists actually works.
    """
    return _resolve_token(token) is not None


def _record_from_airtable(row: dict[str, Any], fields_wanted: list[str]) -> ConferenceRecord:
    fields = row.get("fields", {})
    record = ConferenceRecord(record_id=row.get("id", ""), raw_fields=fields)
    for airtable_name in fields_wanted:
        attr = FIELD_MAP.get(airtable_name)
        if attr is not None:
            setattr(record, attr, fields.get(airtable_name))
    return record


def read_conferences(
    base_id: str = DEFAULT_BASE_ID,
    table_id: str = DEFAULT_TABLE_ID,
    token: str | None = None,
    fields: list[str] | None = None,
    timeout: float = 30.0,
    status=None,
) -> list[ConferenceRecord]:
    """Reads conference/CFP rows from Taylor's Airtable "Sandbox Conferences".

    Returns an empty list (never raises) if the token is missing, the API is
    unreachable, the request is rejected, or the table is empty, so a hiccup
    in one dependency doesn't crash C2's whole ingestion run. Warnings go to
    stderr.

    `status` is an optional `read_status.ReadStatus` saying which of those it
    was. All four used to arrive at the runner as an empty list, so a morning
    where Airtable started refusing our credentials was indistinguishable from
    a morning where Taylor had cleared the table. Since the daily workflow now
    reddens the build on a rejected or unreachable source and stays green on
    an empty one, the difference decides whether anyone is told.
    """
    resolved_token = _resolve_token(token)
    if not resolved_token:
        print(
            "conference_reader: no Airtable token found "
            f"(checked {', '.join(_TOKEN_ENV_VARS)}); returning no conferences.",
            file=sys.stderr,
        )
        read_status.fill(
            status, read_status.UNCONFIGURED,
            f"no Airtable token in {', '.join(_TOKEN_ENV_VARS)}",
        )
        return []

    fields_wanted = fields if fields is not None else list(FIELD_MAP.keys())
    url = f"{_API_ROOT}/{base_id}/{table_id}"
    headers = {"Authorization": f"Bearer {resolved_token}"}

    results: list[ConferenceRecord] = []
    offset: str | None = None
    try:
        while True:
            params: list[tuple[str, str]] = [("fields[]", name) for name in fields_wanted]
            if offset:
                params.append(("offset", offset))

            response = requests.get(url, headers=headers, params=params, timeout=timeout)
            if response.status_code != 200:
                detail = response.text.strip()
                print(
                    f"conference_reader: Airtable returned HTTP {response.status_code} "
                    f"for {base_id}/{table_id}: {detail[:300]}; returning what was read so far.",
                    file=sys.stderr,
                )
                # 401 and 403 are the case worth separating: the token is set
                # and Airtable will not accept it. That is somebody reissuing
                # a credential, not somebody setting one, and until now it
                # arrived at the runner looking like an empty table.
                read_status.fill(
                    status,
                    read_status.REJECTED
                    if response.status_code in (401, 403)
                    else read_status.FAILED,
                    f"Airtable returned HTTP {response.status_code} for "
                    f"{base_id}/{table_id}: {detail[:200]}",
                )
                return results

            payload = response.json()
            for row in payload.get("records", []):
                results.append(_record_from_airtable(row, fields_wanted))

            offset = payload.get("offset")
            if not offset:
                break
    except requests.RequestException as exc:
        print(
            f"conference_reader: could not reach Airtable ({exc}); "
            "returning what was read so far.",
            file=sys.stderr,
        )
        read_status.fill(
            status, read_status.UNREACHABLE, f"could not reach Airtable: {exc}"
        )
        return results

    if not results:
        read_status.fill(
            status, read_status.EMPTY,
            f"Airtable answered and {base_id}/{table_id} holds no rows",
        )
    return results


if __name__ == "__main__":
    # Standalone test: reads the real "Sandbox Conferences" table using the
    # confirmed base/table defaults and the Airtable token from the
    # environment. Optional argv overrides: [base_id] [table_id].
    base = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BASE_ID
    table = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_TABLE_ID

    conferences = read_conferences(base_id=base, table_id=table)
    print(f"Found {len(conferences)} conference row(s) in {base}/{table}")
    for c in conferences:
        applying = c.applying_to_speak
        print(
            f"- {c.conference_name!r} "
            f"[{c.start_date or '?'} -> {c.end_date or '?'}] "
            f"status={c.status!r} cfp_deadline={c.cfp_deadline!r} "
            f"applying_to_speak={applying!r}"
        )
