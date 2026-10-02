# CLAUDE.md - Data Provider (sub-agent of the Project Status Deck Generator)

This folder owns one thing, the live data provider: the piece that reaches real
QofAI / Agent OS data and returns a contract-conforming packet, so decks build
from real company and project data instead of hand-authored fixture packets.

It is a sub-agent of the parent Project Status Deck Generator build, owned by
Antonio Rodriguez Diaz. The parent repo's CLAUDE.md still applies. Where this file
is stricter, this file wins.

## The golden rule, which outranks everything else here

Never fabricate data to fill a packet field.

Both request contracts state it directly, and both mean it as a stop condition, not
a preference. A proposal with invented opportunities or EBITDA numbers is worse
than no proposal. A check-in with invented progress or a fabricated TODAY marker is
worse than no deck. If the platform cannot supply a field, the field is absent, it
is named in `missing_fields`, and it lowers `data_completeness` by the documented
rule. It is never filled with a plausible value, an average, a value borrowed from
a different company or project, or anything a model produced on its own.

There is no deadline, demo, or partially populated packet that makes an invented
number acceptable. When the choice is a guess or an error code, return the error
code.

## Standing rules

1. Ask before committing. Ask before pushing. Never push to main on your own, even
   in auto-accept mode.
2. Never assume. Ask when something is unclear rather than guessing. Several of
   the load-bearing facts in this folder are still open questions for Blake, and a
   plausible guess written into a document reads exactly like a settled fact three
   weeks later.
3. Never hardcode a company, project, PE firm, tool name, endpoint, or environment
   variable name. Every one of those is a parameter with a default that lives in
   one place. The test company is a test company, not a shape to build around.
4. Never print, log, echo, or commit the bearer key. Not in debug output, not in an
   exception message, not in a packet, not in a provenance entry. Read it from the
   environment at call time and let it stay there.
5. Do not change the seam. The provider interface in `src/data_source_adapter.py`
   is `submit(request)` plus `poll(handle)`, and the point of this work is that
   nothing upstream of it moves. No edits to the template loader, the prompt
   assembler, the mapping half, `src/coverage_guard.py`, `src/deck_renderer.py`, or
   `src/render_guard.py` to accommodate this provider. That is PRD criterion 9 in
   the parent repo. If the provider seems to need such a change, that is a finding
   to raise, not a change to make.
6. Read outside this folder freely. Write outside it only with permission. The
   contracts, the frozen example packets, the adapter, and the MCP client are
   reference material here.
7. Every live call to the QofAI server gets proposed before it runs, including
   read-only ones. Antonio approves the call.
8. Keep the four scoping elements current in `PRD.md`: the goal and success
   criteria, the constraints (tools, dependencies, what is pending), the variables
   (inputs and outputs), and the architecture (how it runs). The program manager
   agent reads them to verify milestones, so a stale PRD is a reporting failure,
   not just untidiness. `CHANGELOG.md`, `README.md`, and `INDEX.md` stay current
   for the same reason.
9. Label uncertainty in these documents. Confidence bands (high, medium, low) and
   source types (public document, public inference, private inference) on anything
   load-bearing. Speculation is fine when it says it is speculation.

## What lives here

Scoping and documentation for the provider, plus the recorded-response fixtures the
build needs. The provider module itself lands in the parent repo's `src/` beside
`qofai_mcp_client.py`, decided 2026-07-28, because the wire-together layer imports
from `src/` and a path hack in production code is not worth folder tidiness. The wire
client and the seam (`src/data_source_adapter.py`) already exist in the parent repo
and are not this folder's to rewrite.

## Recorded fixtures and the scrubbing rule

Recorded MCP responses get committed, scrubbed, and the scrubbing rule gets written
into `SCRUBBING.md` in this folder before the first capture is saved. Decided
2026-07-28. They stay in their native JSON, because a fixture is a verbatim wire
capture and a prose summary is not a substitute for one; the house preference for
markdown applies to documents, not to captures.

Superseded 2026-08-10, second paragraph above, on the words "verbatim wire capture."
What replaced it: a research paper is committed as an excerpt rather than whole. The
excerpt is still native JSON and is still a capture rather than a summary, so the
first half of the 2026-07-28 decision stands and only "verbatim" gives way.

Why. The papers run 36KB to 48KB and identify both reference companies through sector,
geography, unit of production, named vessels, and named terminal customers, none of
which `SCRUBBING.md` had a category for and none of which a name substitution reaches.
Measured on 2026-08-10 across the eight committed fixtures: tables and chart tags are
about 35% of a paper and the identifying vocabulary sits overwhelmingly in the other
65%, so cutting to structure plus the prose the parsers actually need drops
sector-identifying tokens by 76% to 94% before any scrubbing happens. That turns
scrubbing the sector from a rewrite of 42KB of prose into a substitution across roughly
two dozen tokens inside structured blocks, which is affordable and is now required.
What an excerpt must contain is in `SCRUBBING.md` step 3, and it is written so a
parser still faces every discrimination it would face on the whole paper: a
pre-filtered document would prove nothing.

Real company names, project names, UUIDs, people, and financials get replaced with
stable synthetic values, and the mapping from real to synthetic stays out of the
repo. Git history is permanent, so a capture goes in scrubbed the first time or not
at all. When in doubt about whether a field is identifying, scrub it.
