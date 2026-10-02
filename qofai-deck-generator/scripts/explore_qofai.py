#!/usr/bin/env python3
"""Read-only terminal browser for the QofAI Platform MCP server.

A throwaway exploration tool, not part of the deck pipeline. It exists so a human
can poke at the data the ``QOFAI_MCP_SERVER_KEY`` grants — list companies, drill
into a company's projects and opportunities, and dump a preliminary assessment or
an opportunity/project detail — without writing code or curl each time.

Safety: this tool is read-only by construction. It refuses to call anything
outside ``READ_ONLY_TOOLS`` below, every one of which only reads. That is belt
and suspenders on top of the key itself, which is scoped to read tools only (no
write / trigger / publish / delete tool is even present), so nothing here can
change or damage platform data.

Run it under the project venv (the system Python 3.9 cannot TLS-handshake the
endpoint)::

    .venv/bin/python scripts/explore_qofai.py companies --search logistics
    .venv/bin/python scripts/explore_qofai.py projects <company_id>
    .venv/bin/python scripts/explore_qofai.py opportunities <company_id>
    .venv/bin/python scripts/explore_qofai.py assessment <company_id>
    .venv/bin/python scripts/explore_qofai.py opportunity <opportunity_id>
    .venv/bin/python scripts/explore_qofai.py tools

The key is read from the environment (``QOFAI_MCP_SERVER_KEY``); ``ui/app.py``'s
loader is not in play here, so either export it or prefix the command by sourcing
``.env``. Nothing company-specific is baked in — every target is an argument.
"""

import argparse
import json
import os
import sys

# Make ``src`` importable whether run from the repo root or elsewhere.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "src"))

from qofai_mcp_client import (  # noqa: E402
    McpAuthError,
    McpError,
    McpMissingKeyError,
    QofaiMcpClient,
)

# The only tools this browser will ever call. All are reads. Any attempt to call
# a tool outside this set is refused before it reaches the server.
READ_ONLY_TOOLS = frozenset(
    {
        "get_status",
        "get_request_status",
        "list_event_kinds",
        "kg_query",  # read mode only; enforced below
        "list_companies",
        "list_projects",
        "list_opportunities",
        "get_preliminary_assessment",
        "get_opportunity_details",
        "get_project_details",
    }
)


def _guarded_call(mcp, name, arguments=None):
    """Call a tool only if it is on the read-only allowlist."""
    if name not in READ_ONLY_TOOLS:
        raise SystemExit(f"refused: {name!r} is not a read-only tool")
    if name == "kg_query" and (arguments or {}).get("mode") not in (None, "read"):
        raise SystemExit("refused: kg_query is allowed in read mode only")
    return mcp.call_tool_json(name, arguments or {})


def _print_json(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def _print_rows(rows, columns):
    """Print a compact fixed-width table for a list of dict rows."""
    if not rows:
        print("(none)")
        return
    widths = {c: len(c) for c in columns}
    for r in rows:
        for c in columns:
            widths[c] = max(widths[c], len(str(r.get(c, ""))))
    header = "  ".join(c.ljust(widths[c]) for c in columns)
    print(header)
    print("  ".join("-" * widths[c] for c in columns))
    for r in rows:
        print("  ".join(str(r.get(c, "")).ljust(widths[c]) for c in columns))


def cmd_tools(mcp, args):
    tools = mcp.list_tools()
    print(f"{len(tools)} tools granted to this key:\n")
    for t in tools:
        print(f"  {t['name']}")
        desc = (t.get("description") or "").strip().splitlines()
        if desc:
            print(f"      {desc[0]}")


def cmd_companies(mcp, args):
    call_args = {"limit": args.limit}
    if args.search:
        call_args["search"] = args.search
    data = _guarded_call(mcp, "list_companies", call_args)
    rows = data.get("companies", [])
    _print_rows(rows, ["name", "status", "onboarding_status", "has_kg", "id"])
    if data.get("truncated"):
        print(f"\n(truncated at {args.limit}; raise --limit to see more)")


def cmd_projects(mcp, args):
    data = _guarded_call(mcp, "list_projects", {"company_id": args.company_id})
    _print_rows(data.get("projects", []), ["name", "status", "id"])


def cmd_opportunities(mcp, args):
    call_args = {"company_id": args.company_id}
    if args.stage:
        call_args["stage"] = args.stage
    data = _guarded_call(mcp, "list_opportunities", call_args)
    _print_rows(data.get("opportunities", []), ["title", "stage", "id"])


def cmd_assessment(mcp, args):
    _print_json(
        _guarded_call(mcp, "get_preliminary_assessment", {"company_id": args.company_id})
    )


def cmd_opportunity(mcp, args):
    call_args = {"opportunity_id": args.opportunity_id}
    if args.company_id:
        call_args["company_id"] = args.company_id
    _print_json(_guarded_call(mcp, "get_opportunity_details", call_args))


def cmd_project(mcp, args):
    call_args = {"project_id": args.project_id}
    if args.company_id:
        call_args["company_id"] = args.company_id
    _print_json(_guarded_call(mcp, "get_project_details", call_args))


def build_parser():
    p = argparse.ArgumentParser(
        prog="explore_qofai",
        description="Read-only browser for the QofAI Platform MCP server.",
    )
    p.add_argument(
        "--endpoint",
        default=None,
        help="Override the MCP endpoint (defaults to the client's default).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("tools", help="List the tools this key grants").set_defaults(
        func=cmd_tools
    )

    sp = sub.add_parser("companies", help="List companies")
    sp.add_argument("--search", help="Substring filter on company name")
    sp.add_argument("--limit", type=int, default=50)
    sp.set_defaults(func=cmd_companies)

    sp = sub.add_parser("projects", help="List a company's projects")
    sp.add_argument("company_id")
    sp.set_defaults(func=cmd_projects)

    sp = sub.add_parser("opportunities", help="List a company's opportunities")
    sp.add_argument("company_id")
    sp.add_argument("--stage", help="Optional stage filter")
    sp.set_defaults(func=cmd_opportunities)

    sp = sub.add_parser("assessment", help="A company's published preliminary assessment")
    sp.add_argument("company_id")
    sp.set_defaults(func=cmd_assessment)

    sp = sub.add_parser("opportunity", help="One opportunity's detail")
    sp.add_argument("opportunity_id")
    sp.add_argument("--company-id", dest="company_id")
    sp.set_defaults(func=cmd_opportunity)

    sp = sub.add_parser("project", help="One project's detail")
    sp.add_argument("project_id")
    sp.add_argument("--company-id", dest="company_id")
    sp.set_defaults(func=cmd_project)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    client_kwargs = {}
    if args.endpoint:
        client_kwargs["endpoint"] = args.endpoint
    try:
        with QofaiMcpClient(**client_kwargs) as mcp:
            args.func(mcp, args)
    except McpMissingKeyError as exc:
        raise SystemExit(f"{exc}")
    except McpAuthError as exc:
        raise SystemExit(f"auth failed: {exc}")
    except McpError as exc:
        raise SystemExit(f"MCP error: {exc}")


if __name__ == "__main__":
    main()
