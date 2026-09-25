"""Airtable client and schema bootstrap. Milestone 5.

Airtable is an input surface, never the state store. SQLite holds every posting;
this base holds only what the owner looks at and labels, and those labels are read
back into SQLite. See PRD section 6.

The base is described in `sources/airtable.toml`, not here. This module contains
no table name, no field name, and no select option; it reads all of them at
runtime, on the same rule that keeps company tokens and the rubric out of code.

Schema creation is idempotent. It adds tables, fields and views that are missing
and leaves everything else alone, so re-running after editing the TOML is the
normal way to change the base.
"""

import time
import tomllib
from dataclasses import dataclass, field
from itertools import islice

import httpx

from . import config

# Airtable takes at most 10 records per create, update or delete request, and
# rate limits a base to 5 requests a second. Both are its numbers, not choices.
BATCH = 10
WRITE_INTERVAL = 0.25


def _batched(items, size):
    it = iter(items)
    while chunk := list(islice(it, size)):
        yield chunk


class AirtableError(RuntimeError):
    """Airtable refused a request. Never treated as a reason to fail a run."""


# How each type in sources/airtable.toml becomes an Airtable field definition.
# Airtable rejects a type it does not know, so an unmapped type is a loud error
# here rather than a confusing 422 from the API.
def _field_options(spec: "Field", table_ids: dict[str, str]) -> dict | None:
    if spec.type == "number":
        return {"precision": spec.precision}
    if spec.type == "date":
        return {"dateFormat": {"name": "iso"}}
    if spec.type == "singleSelect":
        return {"choices": [{"name": c} for c in spec.choices]}
    if spec.type == "multipleRecordLinks":
        target = table_ids.get(spec.link_to)
        if not target:
            raise AirtableError(
                f"field {spec.name!r} links to table {spec.link_to!r}, "
                "which does not exist yet"
            )
        return {"linkedTableId": target}
    if spec.type == "checkbox":
        # Airtable rejects a checkbox created with no options, 422
        # INVALID_FIELD_TYPE_OPTIONS_FOR_CREATE. It is the only type here that
        # needs options it cannot infer, and grouping it with the plain text
        # types below meant `Closed` could never be created: every run of
        # `tools.airtable_bootstrap` raised, and `--dry-run` never called the
        # API at all, so the failure was invisible and the field sat on
        # NEXT_STEPS as an unexplained manual step from 2026-08-21 to
        # 2026-09-20.
        #
        # The icon and colour are Airtable's own defaults, chosen so a field
        # created here is indistinguishable from one added by hand.
        return {"icon": "check", "color": "greenBright"}
    if spec.type in {"singleLineText", "multilineText", "url"}:
        return None
    raise AirtableError(f"unsupported field type {spec.type!r} on {spec.name!r}")


@dataclass(frozen=True)
class Field:
    name: str
    type: str
    column: str = ""
    editable: bool = False
    precision: int = 0
    choices: tuple[str, ...] = ()
    link_to: str = ""
    # Airtable choice -> SQLite value. Empty means the two are identical.
    values: dict = field(default_factory=dict)
    # What a blank Airtable cell means in the database. None means null, which
    # is right for most fields; a NOT NULL column has to say otherwise.
    empty_value: str | None = None

    def to_db(self, shown):
        """Translate what Airtable shows into what the database stores.

        A checkbox is handled before the empty test rather than through
        empty_value, because Airtable omits the field entirely when it is
        unticked. Absent and false are the same fact, and both have to reach
        SQLite as 0 rather than as null: the column is NOT NULL, and a pull that
        compares null against 0 reports a change on every run forever.
        """
        if self.type == "checkbox":
            return 1 if shown else 0
        if shown is None or shown == "":
            return self.empty_value
        if not self.values:
            return shown
        return self.values.get(shown, shown)

    def to_airtable(self, stored):
        """Translate a stored value into the choice Airtable shows."""
        if self.type == "checkbox":
            return True if stored else None
        if stored is None or not self.values:
            return stored
        for shown, value in self.values.items():
            if value == stored:
                return shown
        return stored

    def payload(self, table_ids: dict[str, str]) -> dict:
        body: dict = {"name": self.name, "type": self.type}
        options = _field_options(self, table_ids)
        if options is not None:
            body["options"] = options
        return body


@dataclass(frozen=True)
class View:
    """A view the owner builds by hand, described here so it is described once.

    Airtable's API can read view metadata and cannot create one, so every field
    below is instructions rather than configuration: tools.airtable_bootstrap
    prints them and checks whether a view of that name exists. Nothing here is
    enforced against the live base, so a view whose filter he changes will not
    be corrected and will not complain.
    """

    name: str
    type: str = "grid"
    filter_field: str = ""
    filter_operator: str = ""
    filter_value: str = ""
    group_field: str = ""
    sort_by: tuple[str, ...] = ()
    purpose: str = ""


@dataclass(frozen=True)
class Table:
    key: str
    name: str
    description: str = ""
    fields: tuple[Field, ...] = ()
    views: tuple[View, ...] = ()

    @property
    def editable_fields(self) -> tuple[Field, ...]:
        """The fields the owner writes and the sync reads back into SQLite."""
        return tuple(f for f in self.fields if f.editable)

    @property
    def agent_fields(self) -> tuple[Field, ...]:
        return tuple(f for f in self.fields if not f.editable)


@dataclass(frozen=True)
class Schema:
    tables: tuple[Table, ...]
    max_posting_records: int = 300
    prefer_company_boards: bool = True
    sort_by_tier: bool = False
    prune_never_delivered_tiers: bool = False
    max_row_age_days: int = 0
    age_exempt_tiers: tuple = ()
    feed_source_prefix: str = "feed:"
    collapse_locations: bool = True

    def table(self, key: str) -> Table:
        for t in self.tables:
            if t.key == key:
                return t
        raise AirtableError(f"no table {key!r} in {config.AIRTABLE_SCHEMA_PATH.name}")


def load_schema(path=None) -> Schema:
    """Read sources/airtable.toml. The base is data; this only parses it."""
    path = path or config.AIRTABLE_SCHEMA_PATH
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)

    tables = []
    for t in raw.get("tables", []):
        fields = tuple(
            Field(
                name=f["name"],
                type=f["type"],
                column=f.get("column", ""),
                editable=bool(f.get("editable", False)),
                precision=int(f.get("precision", 0)),
                choices=tuple(f.get("choices", ())),
                link_to=f.get("link_to", ""),
                values=dict(f.get("values", {})),
                empty_value=f.get("empty_value"),
            )
            for f in t.get("fields", [])
        )
        views = tuple(
            View(
                name=v["name"],
                type=v.get("type", "grid"),
                filter_field=v.get("filter_field", ""),
                filter_operator=v.get("filter_operator", ""),
                filter_value=v.get("filter_value", ""),
                group_field=v.get("group_field", ""),
                sort_by=tuple(v.get("sort_by", ())),
                purpose=v.get("purpose", ""),
            )
            for v in t.get("views", [])
        )
        tables.append(
            Table(
                key=t["key"],
                name=t["name"],
                description=t.get("description", ""),
                fields=fields,
                views=views,
            )
        )

    base = raw.get("base", {})
    return Schema(
        tables=tuple(tables),
        max_posting_records=int(base.get("max_posting_records", 300)),
        prefer_company_boards=bool(base.get("prefer_company_boards", True)),
        sort_by_tier=bool(base.get("sort_by_tier", False)),
        prune_never_delivered_tiers=bool(
            base.get("prune_never_delivered_tiers", False)
        ),
        max_row_age_days=int(base.get("max_row_age_days", 0) or 0),
        age_exempt_tiers=tuple(base.get("age_exempt_tiers", []) or []),
        feed_source_prefix=str(base.get("feed_source_prefix", "feed:")),
        collapse_locations=bool(base.get("collapse_locations", True)),
    )


class Client:
    """Thin wrapper over the Airtable REST API.

    Nothing here knows what a posting is. It moves JSON.
    """

    def __init__(self, token: str | None = None, base_id: str | None = None):
        self.token = token or config.AIRTABLE_TOKEN
        self.base_id = base_id or config.AIRTABLE_BASE_ID
        if not self.token or not self.base_id:
            raise AirtableError(
                "AIRTABLE_TOKEN and AIRTABLE_BASE_ID must be set in .env"
            )
        self._http = httpx.Client(
            timeout=config.HTTP_TIMEOUT,
            headers={
                "Authorization": f"Bearer {self.token}",
                "User-Agent": config.USER_AGENT,
            },
        )
        self._last_write = 0.0

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def request(self, method: str, path: str, json: dict | None = None) -> dict:
        url = f"{config.AIRTABLE_API_ROOT}/{path.lstrip('/')}"
        try:
            r = self._http.request(method, url, json=json)
        except httpx.HTTPError as exc:
            raise AirtableError(f"{method} {path} failed: {exc}") from exc
        if r.status_code >= 400:
            raise AirtableError(f"{method} {path} -> {r.status_code}: {r.text[:400]}")
        return r.json() if r.content else {}

    # --- schema ---------------------------------------------------------

    def base_tables(self) -> list[dict]:
        return self.request("GET", f"meta/bases/{self.base_id}/tables").get(
            "tables", []
        )

    def create_table(self, body: dict) -> dict:
        return self.request("POST", f"meta/bases/{self.base_id}/tables", body)

    def create_field(self, table_id: str, body: dict) -> dict:
        return self.request(
            "POST", f"meta/bases/{self.base_id}/tables/{table_id}/fields", body
        )

    # There is deliberately no create_view. Airtable's Web API can read view
    # metadata but cannot create a view, name one, or set its filters; views are
    # interface objects and only GET exists. Verified against the live API on
    # 2026-08-09, where every payload shape returned 422. The bootstrap detects
    # a missing view and prints the manual step instead of failing.

    # --- records --------------------------------------------------------
    #
    # Airtable takes at most 10 records per write and rate limits a base to 5
    # requests a second. Both are handled here so no caller has to think about
    # either. See BATCH and _pace below.

    def records(self, table: str) -> list[dict]:
        """Every record in a table, following pagination to the end."""
        out: list[dict] = []
        offset = None
        while True:
            path = f"{self.base_id}/{table}"
            if offset:
                path += f"?offset={offset}"
            page = self.request("GET", path)
            out.extend(page.get("records", []))
            offset = page.get("offset")
            if not offset:
                return out

    def create_records(self, table: str, records: list[dict]) -> list[dict]:
        created = []
        for chunk in _batched(records, BATCH):
            self._pace()
            body = {"records": [{"fields": r} for r in chunk]}
            created.extend(self.request("POST", f"{self.base_id}/{table}", body)["records"])
        return created

    def update_records(self, table: str, records: list[dict]) -> int:
        """records is a list of {"id": recId, "fields": {...}}."""
        done = 0
        for chunk in _batched(records, BATCH):
            self._pace()
            self.request("PATCH", f"{self.base_id}/{table}", {"records": chunk})
            done += len(chunk)
        return done

    def delete_records(self, table: str, record_ids: list[str]) -> int:
        done = 0
        for chunk in _batched(record_ids, BATCH):
            self._pace()
            query = "&".join(f"records[]={rid}" for rid in chunk)
            self.request("DELETE", f"{self.base_id}/{table}?{query}")
            done += len(chunk)
        return done

    def _pace(self) -> None:
        """Stay under Airtable's 5 requests per second per base."""
        elapsed = time.monotonic() - self._last_write
        if elapsed < WRITE_INTERVAL:
            time.sleep(WRITE_INTERVAL - elapsed)
        self._last_write = time.monotonic()


def ensure_schema(
    client: Client, schema: Schema | None = None, dry_run: bool = False
) -> dict:
    """Create whatever the base is missing. Safe to run repeatedly.

    Returns a report of what it did, so the caller can print the truth rather
    than assume the call worked.

    `dry_run` reads the live base and reports the same diff without writing. It
    exists because `tools.airtable_bootstrap --dry-run` used to print the
    contents of `sources/airtable.toml` and never contact Airtable at all, so it
    could not answer the only question anyone runs it to ask, which is what the
    base is missing. It reported nothing wrong for the month in which the base
    was missing both the `Closed` checkbox and the Interested view, and
    NEXT_STEPS carried both as outstanding the whole time. A dry run that cannot
    see the thing it describes is worse than no dry run, because it reads as
    reassurance.
    """
    schema = schema or load_schema()
    report: dict = {
        "tables_created": [],
        "tables_existing": [],
        "fields_created": [],
        "views_missing": [],
    }

    live = {t["name"]: t for t in client.base_tables()}
    table_ids = {}
    for t in schema.tables:
        if t.name in live:
            table_ids[t.key] = live[t.name]["id"]

    for spec in schema.tables:
        existing = live.get(spec.name)

        if existing is None:
            if dry_run:
                report["tables_created"].append(spec.name)
                # Nothing to diff inside a table that does not exist yet, and
                # no id to hang its fields off, so stop at the table.
                continue
            body = {
                "name": spec.name,
                "description": spec.description,
                "fields": [f.payload(table_ids) for f in spec.fields],
            }
            created = client.create_table(body)
            table_ids[spec.key] = created["id"]
            report["tables_created"].append(spec.name)
            existing = created
            live[spec.name] = created
        else:
            report["tables_existing"].append(spec.name)
            table_ids[spec.key] = existing["id"]
            have = {f["name"] for f in existing.get("fields", [])}
            for f in spec.fields:
                if f.name not in have:
                    if not dry_run:
                        client.create_field(existing["id"], f.payload(table_ids))
                    report["fields_created"].append(f"{spec.name}.{f.name}")

        # Views cannot be created through the API. Report the ones that are
        # missing so the manual step is visible rather than assumed done. A
        # view that is silently absent would send the digest link nowhere.
        have_views = {v["name"] for v in existing.get("views", [])}
        for v in spec.views:
            if v.name not in have_views:
                report["views_missing"].append((spec.name, v))

    return report
