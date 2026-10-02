# SCRUBBING.md — Rule for committing recorded MCP responses as fixtures

Written before the first capture, per `data-provider/CLAUDE.md`: git history is
permanent, so a capture goes in scrubbed the first time or not at all.

This rule covers the ten tools in the grant (`data-provider/PRD.md` §2.1), and is
written against what those tools actually return (§2.1b, §2.1c), not against
imagined fields.

## What gets scrubbed

Replace every instance of the following with a stable synthetic value before a
response is committed. "Stable" means the same real value maps to the same
synthetic value everywhere in the fixture set, so cross-references between two
captured responses (a company `id` reappearing in a `list_projects` response, for
example) still line up. The mapping from real to synthetic values itself never
goes in the repo.

- **Client-identifying detail.** Company names (`list_companies.name`,
  `list_projects` company context), PE firm names (`pe_firm`), project names and
  descriptions (`list_projects.description`, `get_project_details`), and any
  company or person name that appears inside prose fields (`get_opportunity_details.description`,
  `research_paper_natural`, `get_preliminary_assessment.paper_natural` /
  `.paper_abstract`, KG node text properties). A prose field needs a full read,
  not a find-and-replace on one name string, because a paper restates the client's
  name, its subsidiaries, and named people throughout 36KB to 48KB of text.
- **Financial figures.** Every dollar amount, percentage, ratio, and multiple tied
  to the real company: `ebitda_impact.min` / `.max`, revenue and EBITDA figures
  inside `research_paper_natural` prose or tables (assumption tables, scenario
  tables, implementation cost, payback), scores (`impact_score`,
  `technical_feasibility_score`, `ease_of_adoption_score`) if they are specific
  enough to be identifying in context, and any commercial or fee figure. Replace
  with a plausible synthetic figure in the same unit and rough order of magnitude,
  not with a placeholder string, because a parser fixture needs a realistic number
  to parse.
- **Credentials and tokens.** The bearer key, any session id
  (`MCP-Session-Id`), and any value that looks like a secret even if its purpose
  is unclear. If in doubt, remove the field entirely rather than synthesize a
  replacement, since a fixture does not need a fake credential to be useful.
- **Internal IDs.** Every UUID (`id`, `company_id`, `project_id`,
  `latest_analysis_id`, KG node ids) becomes a newly generated, stable synthetic
  UUID. Do not reuse a real UUID's structure or partial value.
- **Audit fields.** `created_at`, `updated_at`, `published_at`, and any other
  timestamp get shifted by a fixed, arbitrary offset (the same offset across the
  whole fixture set) rather than replaced with a round or obviously fake date, so
  relative ordering between records is preserved for tests that check it.

## What gets kept

A fixture is worthless if scrubbing removes what makes it a fixture. Keep:

- **Shape.** Every key, every nesting level, every type (string, number, null,
  list, object), and which fields are present versus absent versus null. The
  defect where `get_preliminary_assessment` returns null `paper_natural` on 12 of
  15 companies (PRD §2.1b) is only visible if the null is kept.
- **Structure inside prose.** Table layout, `<Chart>` tag structure and its JSON
  `config` attribute's keys (values inside get the same financial-figure
  scrubbing above), section headings, and citation markers. A parser fixture
  needs the real shape a paper uses, so every shape needs at least one kept
  example.

  Corrected 2026-08-10. This bullet used to say the two companies use different
  shapes, "Northwind tabulates, FBK narrates," which it inherited from
  `data-provider/PRD.md` §2.1c. Measured across all 21 published papers, that is
  wrong for the scenario table and backwards for the baseline. Both companies
  tabulate the scenario table, 8/8 and 12/13, and no paper on either company
  narrates scenarios in place of a table. Both state the baseline in prose on
  every paper, and FBK is the one that also tabulates it more often, 4/13 against
  2/8. What actually varies is the table's orientation, cases as rows against
  cases as columns, and both orientations appear on both companies. So the shape
  to cover is orientation, not company, and a fixture set that keeps one paper
  per company covers neither reliably.
- **Stage, status, and enum values.** `stage` (discovered / validated /
  published), `status`, `onboarding_status`, `has_kg`, `type`. None of these are
  identifying.
- **Approximate scale markers that are not the figure itself.** "roughly 42KB",
  "three-row table" — descriptive facts about the response, not values pulled
  from inside it.

## Mechanical procedure

1. Capture the raw response into a scratch file outside the repo (this repo's
   scratchpad convention or `/tmp`), never directly into a path git will see.
2. Build or reuse the real-to-synthetic mapping for every company, project,
   person, and figure the capture touches. Keep the mapping itself out of the
   repo, in the same scratch location as the raw capture.
3. Apply the mapping to every field listed under "What gets scrubbed," reading
   prose fields in full rather than pattern-matching a single name.

   3b. **A research paper is committed as an excerpt, not whole.** Added
   2026-08-10, and it supersedes the "verbatim wire capture" line in
   `data-provider/CLAUDE.md`, which carries the reasoning. A 36KB to 48KB paper
   identifies its client through sector, geography, unit of production, named
   vessels, and named customers, which no name substitution reaches and which
   this rule had no category for. Cutting to structure plus the prose the
   parsers need removes 76% to 94% of that vocabulary before scrubbing begins,
   which is what makes scrubbing the rest affordable. So cut first, then scrub
   what is left, and scrub it fully, sector and unit of production included.

   An excerpt keeps, and a fixture that drops any of these is not usable, because
   a parser tested on a pre-filtered document proves nothing:

   - The document's heading skeleton, headings only, with the prose between them
     dropped, so a parser still searches a paper-shaped document rather than a
     pile of tables.
   - Every `<Chart>` tag, not only the one being parsed. Selecting one chart out
     of three to six is the parser's real job.
   - Every table, decoys included. A cost-scenario table headed `| Scenario |` has
     to be rejected by a scenario-table parser, so it has to be present.
   - Every statement of the figures a parser looks for, in every place it occurs,
     each under its own heading. The baseline in particular is not confined to
     one section: it appears in the executive summary, inside the impact section,
     in appendices, and in the reference list.

   Two constraints the parsers depend on, and both are mechanical rather than a
   matter of care. Map numeric tokens through a pure function of the value rather
   than by editing prose, so a figure and every restatement of it inside one
   paper land on the same synthetic value by construction. And scale dollar
   figures by one factor per company while leaving percentages, ratios, multiples,
   and percentage points untouched, so a figure stays derivable from the ones it
   was derived from. Chart `name` values get the client token replaced and nothing
   else, because a selector reads them.
4. Diff the scrubbed version against the raw capture side by side and confirm
   every field under "What gets scrubbed" changed and every field under "What
   gets kept" did not. On an excerpt, diff against the excerpt rather than the
   whole paper, and check the count of headings, tables, charts, and citation
   markers, the row and column count of every table, and the keys, nesting, and
   types inside every chart `config`.
5. Only then write the scrubbed version into a path under `data-provider/`, and
   delete the raw capture and the mapping from every scratch location holding a
   copy, not only the one you wrote from.

When a field's category is unclear, scrub it. A fixture that is slightly less
useful is a cheap price for a fixture that never identifies a real client.

## A category this rule was missing. Added 2026-08-11

**Third-party real companies whose sector membership identifies the client's sector.**
Scrub them. The "Client-identifying detail" bullet above covers company, PE firm,
project, and person names, all of them the client's own, and it has no category for a
real company that is not the client but is named inside the client's paper. That gap is
not theoretical: five such names survived the first fixture set and were found by
reading, after a case-insensitive sweep over every real token came back clean. The sweep
could not see them because the denylist was built from the client's own identifiers, and
these were never on it.

Where they turn up. Competitors, customers, and approved-vendor or qualified-vendor
lists inside an appendix, which is where a paper is most likely to name a dozen real
firms in a row. The bar is one hop: if recognising the company tells a reader what
industry the client is in, it goes. A named marine works contractor or conduit manufacturer
clears that bar on its own.

What does not clear it, and this boundary is deliberate rather than a starting point to
extend. State names, public programs and standards bodies, universities, and software
vendors all stay, including the ones a paper names as candidate tooling. Those name a
market or name QofAI's own proposed stack rather than the client, and no single one of
them narrows the client's industry the way a sector peer does.

A replacement has four requirements, learned by getting each of them wrong first.
Confirm the invented name is not a real company before using it, because trading one
real name for another defeats the change and short coined single words collide with real
small firms surprisingly often; verifying the full multi-word name is the practical
check. Put the replacement in the synthetic sector rather than the real one, or it
reintroduces exactly what was removed. Do not collide with a synthetic already in use,
because a vendor table's value as a fixture depends on its manufacturers staying
distinct. And leave row counts, column counts, bold, citation markers, and the table
grid exactly as they were.

Two things this category does not absorb, added 2026-08-11 after the second pass found both.
An affiliate or sponsor entity named as a payee, on a management-fee line for instance, is the
client's own detail and was already covered by the "Client-identifying detail" bullet above; a miss
there is a miss against an existing rule rather than a gap in this one, and the fix is a more
careful pass, not a new category. And a synthetic can hand the sector back without being a real
company: check an invented name for a real company of that name in the client's real sector before
using it, and treat a real company sharing its distinctive word in that sector as a reason to pick
again.

The next capture catches these at capture time rather than in review. Building the
denylist from the client's identifiers alone is what let them through, so the capture
pass now also reads every appendix vendor or competitor list and every named firm in
prose, and asks the one-hop question about each before anything is written to a fixture.

### One correction to the census, recorded here because E5b is written from it

The census in the 2026-08-10 handoff says Northwind `f000000d` carries a cost-scenario decoy
with rows named `Lightweight / Moderate / Full Stack` that E5b must reject. It does not.
All three of that paper's `| Scenario |` tables use Conservative / Moderate / Aggressive,
and the `Lightweight / Moderate / Full Stack` table is in Northwind paper `00000017`, which is
not in the committed fixture set at all.

The trap is real and the discriminator is not the one the census implies. In
`paper-excerpt-scenario-rows-multi-table.json` the decoy is the third table, a
net-of-investment ROI table, and it is separable only by its columns, `Annual
Investment`, `Net EBITDA Gain`, and `Payback`. Its row names are identical to the two
genuine tables'. The same fixture's `_fixture.known_properties` says so at the point of
use.

## What this rule does not govern. Added 2026-08-10, Antonio's decision

This rule governs captured client content committed as a fixture. It does not
govern this repo's own scoping and planning documents, which name clients
deliberately because their purpose is recording what we measured about which
company. `data-provider/PRD.md` in particular is not scrubbed and keeps both real
company names, both real company UUIDs, and its real figures.

The distinction is what the artifact is for. A fixture is a verbatim copy of a
client's own research that could be shared, ported into Agent OS, or read by
anyone who ends up with the test suite, so it has to stand on its own without
identifying anybody. The PRD is an internal working document in a private
QofAI-owned repo, and a reader who cannot say which company a measurement came
from cannot check it. The UUIDs are object identifiers rather than credentials:
access is gated by the read-only MCP token, which never appears in any document
here, so an id in a private repo opens nothing.

One practical reason the decision went this way rather than the other. The
material has been in committed history since 2026-08-03 and has been pushed, so
scrubbing it now would change the working tree and leave every value in history
untouched. The permanence argument that makes this rule strict about fixtures is
the same argument that makes scrubbing the PRD after the fact close to pointless.

## Hand-authored regression fixtures. Added 2026-08-15, Antonio's decision

A hand-authored regression fixture may name real opportunity UUIDs in its
`_fixture.source` provenance line. This is an exception to the rule at "Internal
IDs" above, which otherwise requires every UUID in a fixture to be replaced, and
it is written here so the next window finds the exception rather than following
the general rule and quietly stripping the traceability.

What it covers, and the boundary is the whole point. The exception applies only to
a fixture whose document content is invented outright: no captured client prose, no
real figures, no real names anywhere in the paper text. Such a file carries no
client content to scrub, so the procedure this document describes has nothing to
operate on. It does not extend to a captured excerpt. A real paper cut down is
still real client content and every rule above applies to it unchanged.

Where the id may appear, and where it may not. The `_fixture.source` line only,
which is provenance about the diagnosis rather than data. Any field a parser or a
test reads, including `opportunity.id`, still takes a synthetic value. The six
`e7c-regression-drafts` files are the shape: synthetic
`00000000-0000-0000-0000-00000000000N` in the data position, real ids named in
`_fixture.source` and labelled there as live-corpus so a reader can tell which is
which by reading rather than by guessing.

Why this is safe, and it is the same reasoning as the PRD exemption above. A UUID
is an object identifier rather than a credential; access is gated by the read-only
MCP token, which never appears in any document here, so an id in a private repo
opens nothing. Set against that, a diagnosis nobody can trace back to the
opportunity it came from cannot be checked, which is the same argument that keeps
real company UUIDs in `PRD.md`.

The residual risk, stated rather than waved off. Fixtures travel further than
planning documents: they can be shared, ported into Agent OS, or read by anyone who
ends up with the test suite, and that difference is why this rule is stricter about
them in the first place. If these files are ever ported out of this repo, the
provenance lines are what to review before they go, and `_fixture.source` is a
single well-known field precisely so that review is a grep rather than a reading.
