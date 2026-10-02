# Data Provider

The live data path for the Project Status Deck Generator. It reaches real QofAI /
Agent OS data over the QofAI Platform MCP server and returns a packet conforming to
the proposal or status request contract, so decks build from real company and project
data instead of hand-authored fixtures.

## Status, read this first

No code exists yet. As of 2026-07-28 this folder holds scoping documents only:
`PRD.md`, `interview-transcript.md`, this file, `CHANGELOG.md`, and `INDEX.md`. The
build is a later session.

Before starting it, read `PRD.md` section 1.1. A read-only walk of the live server on
2026-07-28 established that the status path has no schedule to report against and the
proposal path has no dollar figures or scenario sets, so a correct provider returns
error codes and low-completeness packets today rather than decks. That is a data supply
problem, not a design problem, and the ask is `PRD.md` section 5.1. One question is
left for Blake, in `interview-transcript.md` section 5.

Everything below marked NOT BUILT describes the intended entry point, so the first
build session has a target and a stranger can see where this is going. Everything
marked WORKS TODAY can be run right now.

## Prerequisites

The project virtual environment. Not optional: the system Python 3.9 on this machine
cannot complete a TLS handshake with the MCP endpoint, so every command here uses
the venv interpreter explicitly rather than relying on an activated shell.

```bash
cd repos/project-status-deck-generator
.venv/bin/python --version        # the interpreter every command below uses
```

The MCP bearer key, in the environment. One variable:

```
QOFAI_MCP_SERVER_KEY
```

The value is a machine bearer token Blake issued on 2026-07-23 and sent on Slack. It
is not in this repo and never will be. It lives in `.env`, which is gitignored. Do
not print it, log it, paste it into a document, or commit it. If it is ever exposed,
ask Blake to re-issue it rather than hoping.

The key is read from the environment at call time. `.env` is loaded by the app's own
loader, which is not in play for the command-line paths below, so either export it in
the shell or source `.env` first.

## Five minutes, once it is built

Step 1. Confirm the endpoint answers and the key is accepted, and see what the key
grants. WORKS TODAY.

```bash
.venv/bin/python scripts/explore_qofai.py tools
```

This is the existing read-only browser. It refuses to call anything outside its
ten-tool read allowlist, so it cannot change platform data. Other useful reads:
`companies --search <term>`, `projects <company_id>`, `opportunities <company_id>`,
`assessment <company_id>`, `opportunity <opportunity_id>`, `project <project_id>`.

Known gap: three allowlisted tools have no CLI subcommand, `kg_query`, `get_status`,
and `list_event_kinds`. Reaching those means using `QofaiMcpClient` directly. What the
ten tools actually return, verified on 2026-07-28, is written up in `PRD.md` section
2.1b, so read that before writing a query rather than rediscovering the shapes.

Step 2. Smoke-check the provider itself: resolve one company and one project, and
report reachability without building a deck. NOT BUILT. Intended shape:

```bash
.venv/bin/python scripts/check_provider.py --company "<name or UUID>" \
                                           --project "<name or UUID>"
```

Step 3. Build a deck from live data. NOT BUILT. The intended change is one argument
at the call site, because that is the point of the seam. Today the pipeline is handed
a `FixtureProvider`; with the provider built it is handed an `McpProvider` instead,
and nothing else in the pipeline changes.

```python
# intended, NOT BUILT
from mcp_provider import McpProvider          # lands in src/, decided 2026-07-28
from data_source_adapter import run_adapter   # exists today, unchanged

result = run_adapter(
    company="<name or UUID>",
    project="<name or UUID>",
    provider=McpProvider(),                   # was FixtureProvider.from_packet_file(...)
    deck_type="status",                       # or "proposal"
)
```

`result["status"]` is one of three things, all of them normal outcomes. `ok` carries
the placeholder map and the raw packet. `error` carries a contract error code with a
message and a remediation, and no packet. `review` means the packet came back below
the confidence or completeness gate, so nothing renders and the missing fields go to
a human.

## What it does and does not do

It reads. It resolves a company and a project, gates on the knowledge graph and on
the deck type's precondition, retrieves the requested sections, and assembles a
packet with provenance, gaps, and honest completeness numbers.

It never invents a field. A field the platform cannot supply is absent, listed in
`missing_fields`, and reflected in a lower `data_completeness`. A packet that fails
the gates is the correct output when the data is thin. See `CLAUDE.md` in this folder.

It never writes anything to the platform, renders a deck, assembles a prompt, or
sends anything anywhere. Those belong to the parent pipeline and to a human
reviewer.

## When something fails

`McpMissingKeyError` means `QOFAI_MCP_SERVER_KEY` is unset or empty in the
environment the command actually ran in. Sourcing `.env` in a different shell is the
usual cause.

`McpAuthError` means the server rejected the key (HTTP 401 or 403). The key was
revoked, rotated, or truncated on the way into the environment. Ask Blake to
re-issue.

A TLS handshake failure means the command ran under the system Python instead of the
venv interpreter.

A tool-not-found or out-of-scope error means the key's grant does not include what
was called. Blake said twice on 2026-07-23 that tools can be added if the agent
needs them, so this is a request, not a wall.

A `TimeoutError` from the adapter's poll loop means the provider never reported done
within the allowed polls. Raise the poll budget at the call site before assuming the
provider is broken.

## Where to read next

`PRD.md` for the goal, the criteria, the constraints, the variables, and the
architecture, with section 2.1b for what the ten granted tools actually return and
section 5.1 for the tool request both paths depend on.
`interview-transcript.md` for what Blake settled on 2026-07-23, what he did not, which
of the twelve questions are now answered and how, and the one left for him.
`INDEX.md` for the file list. The parent repo's `../NEXT-STEPS.md` item 3 for how this
fits the wider queue.
