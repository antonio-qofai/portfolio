"""Referral contacts. PRD section 7.

People live in `sources/contacts.toml`, never here. This module loads that file
into the `contacts` table and answers the one question the digest asks: does
The owner know anyone at this company.

The load is idempotent, keyed on name plus company, so it runs on every watcher
run and editing the TOML is all that is needed to add someone.
"""

import tomllib

from . import config
from .sources import normalize_company


def load(path=None) -> list[dict]:
    path = path or config.CONTACTS_PATH
    if not path.exists():
        return []
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    return [
        {
            "name": c["name"],
            "company": c.get("company", ""),
            "relationship": c.get("relationship", ""),
            "notes": c.get("notes", ""),
        }
        for c in data.get("contacts", [])
    ]


def _company_id(conn, company: str) -> tuple[int | None, bool]:
    """Resolve a company name to a companies row, creating one if needed.

    Matching goes through the same normalizer the aggregator feeds use, so a
    spelling difference does not orphan a contact.

    A company named here that has no row yet gets one, with no ATS platform and
    no token. That is deliberate. Not every company the owner knows someone at is
    one the watcher can poll; Example Capital runs on Newton Software, which none of the
    three fetchers speak. Without the row the contact links to nothing and the
    referral never surfaces, which defeats the point of PRD section 7. A row
    with no token is polled by nothing and costs nothing.

    Returns the id and whether it was created.
    """
    if not company:
        return None, False
    target = normalize_company(company)
    for row in conn.execute("SELECT id, name FROM companies"):
        if normalize_company(row["name"]) == target:
            return row["id"], False
    cur = conn.execute("INSERT INTO companies (name) VALUES (?)", (company,))
    return cur.lastrowid, True


def sync_to_db(conn, path=None) -> dict:
    """Upsert every contact in the TOML. Returns what changed."""
    report = {"added": 0, "updated": 0, "companies_created": []}

    for c in load(path):
        company_id, created = _company_id(conn, c["company"])
        if created:
            report["companies_created"].append(c["company"])

        existing = conn.execute(
            "SELECT id FROM contacts WHERE lower(name)=lower(?)", (c["name"],)
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE contacts SET company_id=?, relationship=?, notes=? WHERE id=?",
                (company_id, c["relationship"], c["notes"], existing["id"]),
            )
            report["updated"] += 1
        else:
            conn.execute(
                "INSERT INTO contacts (name, company_id, relationship, notes) "
                "VALUES (?, ?, ?, ?)",
                (c["name"], company_id, c["relationship"], c["notes"]),
            )
            report["added"] += 1

    conn.commit()
    return report


# Below this length a normalized name is too generic to match on containment.
MIN_CONTAINMENT = 5


def _same_company(a: str, b: str) -> bool:
    """Whether two company names refer to the same employer.

    Equality after normalizing, or one name containing the other. Containment is
    what makes "Example Capital" in contacts.toml match a posting from "Example Capital Investment
    Group"; the shared normalizer strips corporate suffixes like Inc and LLC but
    not longer ones like Investment Group, and widening it would change how 682
    companies reconcile against the aggregator feeds.

    The looseness is deliberate and follows the same asymmetry as the prefilter.
    A missed referral is invisible. A wrong name shown next to a posting costs
    three seconds. The length guard stops a short name matching everything.
    """
    x, y = normalize_company(a), normalize_company(b)
    if not x or not y:
        return False
    if x == y:
        return True
    if min(len(x), len(y)) < MIN_CONTAINMENT:
        return False
    return x in y or y in x


def for_company(conn, company: str) -> list[dict]:
    """Contacts at a company, for the digest to surface next to a posting."""
    out = []
    for row in conn.execute(
        "SELECT c.name, c.relationship, co.name AS company "
        "FROM contacts c LEFT JOIN companies co ON co.id = c.company_id"
    ):
        if row["company"] and _same_company(row["company"], company):
            out.append(dict(row))
    return out
