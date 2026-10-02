#!/usr/bin/env python3
"""Read-only probe: does Agent OS hold QofAI's own commercial terms anywhere?

One question, asked of the live server. `data-provider/PRD.md` §2.1c already
measured 0/8 and 0/13 papers carrying a QofAI fee or commercial term, but that
census read `research_paper_natural` prose. It did not walk the OBJECT SCHEMAS
the granted tools return, and it did not ask the knowledge graph. This probe
closes that gap so the auto-populate-commercial-terms decision rests on a
measurement instead of an assumption either way.

What it does, all reads:

  1. `tools/list`, to see whether the grant is still exactly the ten read tools
     confirmed on 2026-07-28, or whether Blake has added anything since.
  2. `list_event_kinds`, scanned for any deal / pricing / contract shaped kind.
  3. `get_project_details` and `get_opportunity_details` on targets you name,
     walked to every leaf key path and matched against pricing vocabulary.
  4. `kg_query` in read mode only, asked for pricing-shaped node types.

What it deliberately does NOT do. It never prints a value for a key that
matches the pricing vocabulary. It reports the key path, the value's type, and
whether the value is populated. If our own fee terms really are sitting in that
store, the finding we need is "they exist, at this path," and dumping the
figures into a terminal and a session transcript adds risk for no information.
Non-matching keys are counted, not printed.

Read-only by construction: every tool it calls is in READ_ONLY_TOOLS, and
kg_query is pinned to read mode. Nothing here can change platform data.

Nothing company-specific is baked in. Every target is an argument.

Run it under the project venv (system python3 cannot TLS-handshake the
endpoint), with the key exported:

    cd repos/project-status-deck-generator
    set -a; . ./.env; set +a
    .venv/bin/python scripts/probe_commercial_source.py --company <company_id>

Add --project <id> and --opportunity <id> to walk those schemas too; without
them the probe resolves the first project and opportunity under --company and
says which it picked.

Approved by Antonio on 2026-08-12 as a specific live read, per
`data-provider/CLAUDE.md` rule 7. That approval covers this probe as written.
Widening it to other tools or to write mode is a new approval, not this one.
"""

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
# Derived, not hardcoded: from `scripts/` the root is one level up. Override
# with --root when running the script from somewhere else entirely.
_DEFAULT_ROOT = os.path.dirname(_HERE)

READ_ONLY_TOOLS = frozenset(
    {
        "get_status",
        "get_request_status",
        "list_event_kinds",
        "kg_query",
        "list_companies",
        "list_projects",
        "list_opportunities",
        "get_preliminary_assessment",
        "get_opportunity_details",
        "get_project_details",
    }
)

# The ten confirmed against a live tools/list on 2026-07-28. Anything outside
# this set is news worth reporting.
GRANT_AS_OF_2026_07_28 = set(READ_ONLY_TOOLS)

# Vocabulary that would indicate OUR commercial terms rather than the client's
# operating figures. Deliberately wide, because a false positive costs one line
# of human reading and a false negative costs the decision.
PRICING_WORDS = (
    "price", "pricing", "fee", "invest", "upfront", "comp", "contract",
    "sow", "engagement", "terms", "retainer", "rate", "billing", "invoice",
    "payment", "deal", "quote", "proposal_value", "margin_share",
)

# Words that look like pricing but are almost certainly the CLIENT's operating
# costs, which the papers already carry and which are not what we are hunting.
LIKELY_CLIENT_SIDE = ("implementation_cost", "cost_per", "unit_cost", "opex", "capex")


def _matches_pricing(path):
    low = path.lower()
    return any(w in low for w in PRICING_WORDS)


def _client_side_noise(path):
    low = path.lower()
    return any(w in low for w in LIKELY_CLIENT_SIDE)


def _walk(node, prefix=""):
    """Yield (key_path, value) for every leaf in a nested dict/list."""
    if isinstance(node, dict):
        for k, v in node.items():
            child = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, (dict, list)):
                yield from _walk(v, child)
            else:
                yield child, v
    elif isinstance(node, list):
        # Collapse list indices so paths aggregate rather than explode.
        for v in node:
            if isinstance(v, (dict, list)):
                yield from _walk(v, f"{prefix}[]")
            else:
                yield f"{prefix}[]", v
    else:
        yield prefix, node


def _populated(value):
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _report_schema(label, payload):
    """Print pricing-matching key paths only, with types and populated-ness."""
    leaves = list(_walk(payload))
    total = len(leaves)
    seen = {}
    for path, value in leaves:
        if not _matches_pricing(path):
            continue
        entry = seen.setdefault(
            path,
            {"type": type(value).__name__, "populated": False,
             "noise": _client_side_noise(path)},
        )
        if _populated(value):
            entry["populated"] = True

    print(f"\n--- {label} ---")
    print(f"    leaf key paths walked: {total}")
    if not seen:
        print("    pricing-vocabulary matches: NONE")
        return 0
    print(f"    pricing-vocabulary matches: {len(seen)} (values withheld by design)")
    for path in sorted(seen):
        e = seen[path]
        tag = "  [likely client-side, not our fee]" if e["noise"] else ""
        state = "POPULATED" if e["populated"] else "empty/null"
        print(f"      {path}  ({e['type']}, {state}){tag}")
    return len(seen)


def _safe(fn, what):
    try:
        return fn(), None
    except Exception as exc:  # noqa: BLE001 - a probe reports, it does not die
        print(f"    ! {what} failed: {type(exc).__name__}: {exc}")
        return None, exc


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=_DEFAULT_ROOT,
                    help="repo root, for importing src/qofai_mcp_client.py")
    ap.add_argument("--company", help="company_id to walk (optional but recommended)")
    ap.add_argument("--project", help="project_id to walk (default: first under --company)")
    ap.add_argument("--opportunity", help="opportunity_id to walk (default: first under --company)")
    args = ap.parse_args()

    sys.path.insert(0, os.path.join(args.root, "src"))
    from qofai_mcp_client import QofaiMcpClient, McpMissingKeyError  # noqa: E402

    def call(mcp, name, arguments=None):
        if name not in READ_ONLY_TOOLS:
            raise SystemExit(f"refused: {name} is not in READ_ONLY_TOOLS")
        if name == "kg_query" and (arguments or {}).get("mode") not in (None, "read"):
            raise SystemExit("refused: kg_query is allowed in read mode only")
        return mcp.call_tool_json(name, arguments or {})

    print("PROBE: is QofAI's own commercial/pricing data anywhere in Agent OS?")
    print("Read-only. Values for pricing-matching keys are withheld deliberately.")

    try:
        client = QofaiMcpClient()
    except McpMissingKeyError as exc:
        raise SystemExit(
            f"{exc}\n\nRun:  set -a; . ./.env; set +a   then re-run this script."
        )

    hits = 0
    with client as mcp:
        # 1. The grant itself.
        print("\n=== 1. tools/list, the current grant ===")
        tools, _ = _safe(mcp.list_tools, "tools/list")
        if tools is not None:
            names = sorted(
                t.get("name") for t in tools if isinstance(t, dict) and t.get("name")
            )
            print(f"    {len(names)} tools granted:")
            for n in names:
                print(f"      {n}")
            added = set(names) - GRANT_AS_OF_2026_07_28
            removed = GRANT_AS_OF_2026_07_28 - set(names)
            print(f"    NEW since the 2026-07-28 walk: {sorted(added) or 'none'}")
            print(f"    GONE since the 2026-07-28 walk: {sorted(removed) or 'none'}")
            pricing_tools = [n for n in names if _matches_pricing(n)]
            print(f"    tool names matching pricing vocabulary: {pricing_tools or 'none'}")

        # 2. Event kinds.
        print("\n=== 2. list_event_kinds ===")
        kinds, _ = _safe(lambda: call(mcp, "list_event_kinds"), "list_event_kinds")
        if kinds is not None:
            blob = json.dumps(kinds)
            leaves = [v for _, v in _walk(kinds) if isinstance(v, str)]
            matching = sorted({v for v in leaves if _matches_pricing(v)})
            print(f"    payload size: {len(blob)} chars, {len(leaves)} string leaves")
            print(f"    kinds matching pricing vocabulary: {matching or 'NONE'}")

        # 3. Object schemas.
        company = args.company
        project = args.project
        opportunity = args.opportunity

        if company and not project:
            print("\n=== resolving first project under --company ===")
            projects, _ = _safe(
                lambda: call(mcp, "list_projects", {"company_id": company}),
                "list_projects",
            )
            if projects is not None:
                ids = [v for p, v in _walk(projects)
                       if p.endswith("id") and isinstance(v, str)]
                project = ids[0] if ids else None
                print(f"    picked project_id: {project}")

        if company and not opportunity:
            print("\n=== resolving first opportunity under --company ===")
            opps, _ = _safe(
                lambda: call(mcp, "list_opportunities", {"company_id": company}),
                "list_opportunities",
            )
            if opps is not None:
                ids = [v for p, v in _walk(opps)
                       if p.endswith("id") and isinstance(v, str)]
                opportunity = ids[0] if ids else None
                print(f"    picked opportunity_id: {opportunity}")

        print("\n=== 3. object schemas, walked to every leaf ===")
        if project:
            payload, _ = _safe(
                lambda: call(mcp, "get_project_details", {"project_id": project}),
                "get_project_details",
            )
            if payload is not None:
                hits += _report_schema(f"get_project_details({project})", payload)
        else:
            print("    (no project to walk; pass --company or --project)")

        if opportunity:
            payload, _ = _safe(
                lambda: call(mcp, "get_opportunity_details",
                             {"opportunity_id": opportunity}),
                "get_opportunity_details",
            )
            if payload is not None:
                hits += _report_schema(
                    f"get_opportunity_details({opportunity})", payload
                )
        else:
            print("    (no opportunity to walk; pass --company or --opportunity)")

        if company:
            payload, _ = _safe(
                lambda: call(mcp, "get_preliminary_assessment",
                             {"company_id": company}),
                "get_preliminary_assessment",
            )
            if payload is not None:
                hits += _report_schema(
                    f"get_preliminary_assessment({company})", payload
                )

        # 4. The knowledge graph. kg_query takes company_id + cypher + mode
        # (read only in this grant), per data-provider/PRD.md §2.1b. Two
        # questions: has a pricing-shaped LABEL appeared since the 2026-07-28
        # walk, and does Engagement carry any money property. The 07-28 walk
        # recorded 20 labels with no Contract / Deal / SOW / Pricing among them,
        # and Engagement holding only commitment_summary, program_count,
        # project_id, status, type, version, topological_rank and timestamps.
        # This re-asks rather than trusting that record.
        print("\n=== 4. kg_query, read mode ===")
        if not company:
            print("    (kg_query is company-scoped; pass --company to run it)")
        else:
            probes = (
                ("label inventory",
                 "MATCH (n) RETURN DISTINCT labels(n) AS labels LIMIT 500"),
                ("Engagement properties",
                 "MATCH (n:Engagement) RETURN keys(n) AS props LIMIT 100"),
            )
            for what, cypher in probes:
                res, _ = _safe(
                    lambda c=cypher: call(
                        mcp,
                        "kg_query",
                        {"company_id": company, "mode": "read", "cypher": c},
                    ),
                    f"kg_query({what})",
                )
                if res is None:
                    continue
                strings = sorted(
                    {v for _, v in _walk(res) if isinstance(v, str) and v.strip()}
                )
                matching = [s for s in strings if _matches_pricing(s)]
                print(f"\n    {what}: {len(strings)} distinct string values")
                for s in strings[:40]:
                    mark = "  <-- pricing vocabulary" if _matches_pricing(s) else ""
                    print(f"      {s}{mark}")
                if len(strings) > 40:
                    print(f"      ... {len(strings) - 40} more")
                print(f"    pricing-vocabulary matches: {matching or 'NONE'}")
                hits += len(matching)

    print("\n=== VERDICT INPUT ===")
    print(f"pricing-vocabulary key paths found across walked schemas: {hits}")
    print("A nonzero count is NOT proof our fee terms are there. Read the paths:")
    print("  - client-side operating costs are tagged and do not count")
    print("  - an empty/null field means the schema has a slot nobody fills")
    print("A POPULATED, QofAI-scoped pricing field is the only thing that would")
    print("support auto-populating commercial terms onto a deck.")


if __name__ == "__main__":
    main()
