# Agent OS ← Proposal Builder · REQUEST CONTRACT (Schema v0.1)

Companion to `proposal-data-packet-EXAMPLE.md` (the response). This defines what
the proposal-building agent **sends** to Agent OS, how Agent OS resolves the
company **and the project within it** and gates on KG availability, and every
response it can get back — including the error cases the agent must handle before
it tries to render a deck.

One company can have more than one project. A proposal is always scoped to a
single project, so the request must name both the company and the project, and
Agent OS resolves and returns data at the company + project level.

---

## 1 · How the agent contacts Agent OS

Agent OS is reached by **dispatching to an agent**, not by calling an HTTP
endpoint. The proposal builder invokes a dedicated data agent and passes the
request as a fenced YAML block in the prompt. Two supported invocation modes:

```bash
# CLI (background recommended — KG + walker assembly takes 30–90s)
claude --agent proposal-data-provider --background "<request YAML below>"
```

```jsonc
// Agent tool (from another agent/orchestrator)
{ "subagent_type": "proposal-data-provider", "prompt": "<request YAML below>" }
```

> **Note:** `proposal-data-provider` is the agent that owns this contract. It
> does not exist yet — it would wrap `pe-firm-kg` (company + project + KG-credential
> resolution) and `walker-simulation-pipeline` (opportunities, EBITDA deltas),
> and emit the response packet. Until it exists, the request can be sent to
> `pe-firm-kg` directly, but the packet shape is only guaranteed once the
> dedicated agent wraps it. Flagging this as the missing piece.

The agent MUST run in **background mode** and poll for completion (per the
Cowork rules in `CLAUDE.md`) — foreground dispatch will appear to time out.

---

## 2 · Request schema

```yaml
request:
  # ---- required ----
  intent: "generate_project_planning_proposal"   # enum; only value for now
  company: "Ridgeline Site Services"             # name OR company_id (UUID)
  project: "Production Dashboard & Mobile Field App"  # project name OR project_id (UUID) within the company
  schema_version: "0.1"                          # request contract version

  # ---- optional (sensible defaults applied server-side) ----
  deck_title: "Production Dashboard Rollout"     # names the cover + page marks; default: the project's name
  company_id: "0000000b-0000-4000-8000-00000000000b"  # narrows `company` to one candidate; sent WITH the name
  pe_firm: "Woodgrove Partners"                # disambiguates same-named cos; else resolved from KG
  proposal_date: "2026-07-06"                    # default: today
  client_short: "FBK"                            # short brand form for the footers; no source supplies it
  opportunity_ids:                               # which opportunities the deck is FOR
    - "0000002b-0000-4000-8000-00000000002b"     # one slide 2 each, in this order
    - "fabe10ab-..."                             # no ceiling on the count
  opportunity_id: "ede31378-..."                 # the singular form; means a list of one
  template: "project_planning_v2"                # default: project_planning_v2
  sections_requested:                            # default: all
    - cover
    - opportunity                                # this one slide REPEATS, once per opportunity
    - platform
    - rollout
    - commercial_terms
    - next_steps
  options:
    currency: "USD"                              # default: USD
    voice: "qofai_standard"                      # copy tone; default: qofai_standard
    min_confidence: "medium"                     # abort if packet confidence below this; default: medium
    min_data_completeness: 0.70                  # abort below this; default: 0.70
    include_prose: true                          # false = structured fields only, no slide copy
    scenario_set: ["conservative", "base", "optimistic"]   # default: all three
```

### Field rules

| Field | Rule |
|---|---|
| `company` | Accepts a display name (fuzzy-matched) **or** a `company_id` UUID (exact). UUID is preferred — it skips resolution ambiguity entirely. **Not implemented on the current tool grant (2026-09-15):** neither `list_companies` nor `list_projects` accepts a lookup by id, only a name search, so a request carrying a UUID here and no name resolves as `E_COMPANY_NOT_FOUND` rather than being dereferenced. Send the name, and narrow it with the separate `company_id` field below. This row describes the intended behaviour once a by-id tool exists. |
| `company_id` | The candidate a caller picked out of a previous call's `E_AMBIGUOUS_COMPANY` list. It **narrows** `company`, it does not replace it, so the request carries both. Agent OS re-runs the same name search and returns the candidate whose id matches; an id matching nothing that search returns is `E_COMPANY_NOT_FOUND` carrying the candidate list again, never a quiet fall back to the name match, because a stale id and a mistyped one are the same shape from here. Narrowing rather than dereferencing is forced by the grant and not a design preference: see the `company` row. Added 2026-09-15 (part two item 17), and it is what the studio's ambiguous-company picker posts. |
| `project` | **Required where the company has any project.** Accepts a project name (fuzzy-matched within the resolved company) **or** a `project_id` UUID (exact). Resolved within the company, since one company can have multiple projects. UUID is preferred. If absent, Agent OS returns `E_PROJECT_REQUIRED`; if the name matches more than one project, `E_AMBIGUOUS_PROJECT`; if none, `E_PROJECT_NOT_FOUND`. Agent OS never falls back to company-level data or guesses which project was meant. A company with **no** project at all is the one exception (2026-08-20): there is nothing to resolve and nothing to refuse, so the title falls to `deck_title`, or to the selected opportunity's title. |
| `deck_title` | The deck's title: the cover `Title` and the same string in the page marks, which is the only thing a project record contributes to a deck. Studio input — no data source supplies it. Given, it wins over the project's name. Absent, the project names the deck; absent with no project listed for the company, the selected opportunity's title does. Whichever wins is echoed back as `deck_title` in `request_echo`, so the cover and the page marks always read one string. |
| `opportunity_ids` | **Which opportunities the deck is for**, in the order their slides read in. Each one supplies the research paper its own slide 2 is written from, so this is what most of the deck's substance comes from. Added 2026-09-13 (part two item 15); before that a deck carried exactly one. There is **no ceiling** on the count: what scales with it is the render, not the data. Absent, the provider falls back to the first published opportunity carrying a paper, which is a default a programmatic caller sweeping a corpus wants and a reviewer in front of a form does not — the studio refuses a live run that picks none, because no automatic pairing exists (the platform records no project-to-opportunity link, so any pairing would be a guess). Ids are deduplicated in order: the same opportunity twice would be two identical slides. |
| `opportunity_id` | The singular form, and still accepted: it means a list of one, so every caller that sent it before 2026-09-13 sends exactly the request it did. Given alongside `opportunity_ids`, it is appended if it is not already there. |
| `client_short` | The deck's short brand form, in every page footer and in the folder its decks are filed under. **No data source supplies it**, and none ever has: `company.client_short` is UNSOURCEABLE in the packet's own slot table and is filled on no path, so a request that omits it renders every footer with the company's full registry name. Studio input, on the same terms as `deck_title`: given, it wins; blank, it degrades to the full name rather than showing a missing marker, because a footer short-form is cosmetic and defaultable. Added 2026-09-15 after the first live deck carried the long name on all seven footers. |
| `pe_firm` | Only needed to disambiguate a name that matches companies under multiple firms. Ignored when `company` is a UUID. |
| `template` | Determines which sections are valid. A template that has no `commercial_terms` slide will null that section even if requested. |
| `min_confidence` / `min_data_completeness` | Hard gates, applied PER OPPORTUNITY SECTION since 2026-09-13. A section below either is marked in `opportunities` and its slide renders with its own missing-field markers; the deck is refused with `E_LOW_CONFIDENCE` only when NO section clears. For a deck carrying one opportunity that is exactly the refusal that has always happened, and the floor itself has not moved. |
| `sections_requested` | The response only populates these; others are returned as `null` with a `skipped: "not_requested"` marker, so the template renderer stays deterministic. |

---

## 3 · Company & project resolution, KG gating

Agent OS resolves `company` first, then `project` within that company, before
assembling anything, in this order:

1. **UUID given as `company`** → direct lookup. Skip to step 4. **Unimplemented
   as of 2026-09-15, and unimplementable on the current grant:** no granted tool
   accepts a lookup by id. A request whose `company` is a UUID therefore falls
   into step 2, searches for the UUID as a name, and returns
   `E_COMPANY_NOT_FOUND`. This step describes intended behaviour, not behaviour.
   Callers holding an id send the name and narrow it, per step 2's third branch.
2. **Name given** → case-insensitive fuzzy match against the company registry.
   - **1 match** → proceed.
   - **>1 match** → if `pe_firm` narrows it to one, proceed; else return
     `E_AMBIGUOUS_COMPANY` with the candidate list.
   - **`company_id` given alongside the name** → the same search runs, and the
     candidate whose id matches is the resolved company. This NARROWS the name's
     own result set and never replaces the search, which is what step 1 would do
     if it could; the two are different mechanisms and only this one exists. On
     the same footing as the `pe_firm` filter in step 3, and applied before the
     ambiguity check, so a picked id resolves a name that would otherwise be
     ambiguous. An id matching none of the search's results returns
     `E_COMPANY_NOT_FOUND` carrying the candidate list, never a fall back to the
     name match: a stale id and a mistyped one are indistinguishable from here,
     and resolving either would build a deck for a company nobody picked.
     Added 2026-09-15 (part two item 17).
   - **0 matches** → return `E_COMPANY_NOT_FOUND`.
3. **Firm filter** applied if `pe_firm` present.
4. **Project gate** — resolve `project` within the resolved company:
   - **The company lists no project at all** → nothing to resolve. The deck is
     titled from `deck_title`, or from the opportunity selected in step 6. No
     other role is affected, because the project contributes nothing else.
   - **Missing** (`project` not supplied, and the company does list projects) →
     return `E_PROJECT_REQUIRED`. Agent OS does not fall back to company-level
     data.
   - **`project_id` UUID given** → direct lookup within the company; **0 matches**
     → `E_PROJECT_NOT_FOUND`.
   - **Name given** → fuzzy match against the company's projects. **1 match** →
     proceed. **>1 match** → `E_AMBIGUOUS_PROJECT` with the candidate list. **0
     matches** → `E_PROJECT_NOT_FOUND`.
5. **KG gate** — call `get_kg_credentials(company_id)`:
   - No KG settings / credential error → `E_NO_KG`.
   - Credentials present but Bolt handshake fails / times out → `E_KG_UNREACHABLE`.
     (This is the failure mode seen on the historically-flaky instances —
     Lucerne, Proseware, Adatum, Adatum Safety Group. The agent must treat a
     name-resolves-but-KG-down case distinctly from no-KG-at-all.)
6. **Corpus gate** — confirm a walker corpus / simulation output exists for the
   resolved company + project. Missing → `E_NO_CORPUS` (KG is up but opportunities
   can't be derived).

Only after all of these pass does Agent OS assemble the packet for that company +
project.

---

## 4 · Response envelope

Success and error share one envelope so the agent can branch on `status` alone.

```yaml
response:
  status: "ok"                     # ok | error
  request_echo: { ... }            # the request, for traceability
  # on status: ok ↓
  packet: "<the markdown from proposal-data-packet-EXAMPLE.md>"
  opportunities:                   # one entry per opportunity, in deck order
    - opportunity_id: "ede31378-..."
      title: "Mobile Field Data Capture"
      data_completeness: 1.0
      confidence: "high"
      cleared: true                # whether THIS section cleared the floor
      missing_fields: []
      pass_failed: false           # whether a model leg broke on this section
      pass_failure: ""
      absent_after_failed_pass: [] # what that leg was asked for and never read
  # on status: error ↓
  error:
    code: "E_NO_KG"
    message: "Human-readable explanation"
    remediation: "What the caller (or a human) should do next"
    details: { ... }               # code-specific payload (e.g. candidate list)
```

On `status: ok`, `packet` carries the full response markdown (its own
frontmatter reports `confidence` and `data_completeness` — the agent should
re-check these against its request thresholds as a belt-and-suspenders step).

`opportunities` is the per-section account, added 2026-09-13 with item 15, and
it exists because THE GATE RUNS PER OPPORTUNITY SECTION. A deck can come back
`ok` with one section clear and another below the floor and marked, which is the
shape most likely to reach a client with nobody having noticed, so every
section's own score travels beside the packet rather than being summarised into
one number. It also carries whether a model leg failed on that section even on a
run that succeeded: a run that is refused gets looked at, and a run that
succeeds does not.

It is reviewer-facing and never deck content. Like the provenance report and the
attachment record it rides BESIDE the packet and never inside it, because a
score is an annotation about our own confidence and does not belong on a slide.

---

## 5 · Error codes

| Code | Meaning | `details` payload | Agent should… |
|---|---|---|---|
| `E_COMPANY_NOT_FOUND` | No registry match for the name | `{ query, closest: [names] }` | Surface to human; suggest closest / ask for UUID |
| `E_AMBIGUOUS_COMPANY` | Name matched >1 company | `{ candidates: [{name, company_id, pe_firm}] }` | Re-request with a UUID or `pe_firm` |
| `E_PROJECT_REQUIRED` | Company resolved but no `project` supplied | `{ company_id }` | **Stop.** Ask the user which project; do not fall back to company-level data |
| `E_AMBIGUOUS_PROJECT` | Project name matched >1 project in the company | `{ company_id, candidates: [{name, project_id}] }` | Re-request with a `project_id`; do not guess |
| `E_PROJECT_NOT_FOUND` | No project match within the resolved company | `{ company_id, query, closest: [names] }` | Surface to human; suggest closest / ask for `project_id` |
| `E_NO_KG` | Company exists but has no knowledge graph provisioned | `{ company_id }` | **Stop.** No proposal is possible; route to KG-provisioning |
| `E_KG_UNREACHABLE` | KG provisioned but Bolt handshake failed | `{ company_id, neo4j_url, last_ok }` | Retry with backoff; escalate to human if persistent |
| `E_SOURCE_UNREACHABLE` | Agent OS could not be reached at all: no response, a rejected request, or a reply the agent could not parse. Raised BY THE AGENT, not returned by Agent OS, which by definition never answered. The only code in this table with that origin. | `{ endpoint_kind, error_type }` | Retry once with backoff; escalate to whoever can see the platform's status. Do not re-run the deck. |
| `E_NO_CORPUS` | KG up but no walker/simulation corpus to derive opportunities | `{ company_id }` | Trigger corpus build (walker-corpus-builder) or notify human |
| `E_LOW_CONFIDENCE` | **No section** cleared `min_confidence` / `min_data_completeness` | `{ confidence, data_completeness, missing_fields: [], opportunities: [] }` | Do NOT render; return for human review |
| `E_BAD_REQUEST` | Missing/invalid required field or unknown `intent` | `{ field, reason }` | Fix payload; do not retry verbatim |
| `E_TEMPLATE_UNKNOWN` | `template` not registered | `{ template, available: [] }` | Pick a valid template |

### The attachment codes (item 14, added 2026-09-07)

A run may carry attachments, the documents the deck is written from. They are
NOT a request field and §2 does not list them: they are dispatched beside the
request, because the request is echoed into the packet document and the deck
store and a file's bytes have no business in either. The eight codes below
refuse one, and they share `E_SOURCE_UNREACHABLE`'s origin note: they are
raised BY THE AGENT, before Agent OS is asked anything at all, because a file
the agent cannot read costs no round trip to discover. Each carries
`{ filename, extension, kind, ... }` in `details` and never a parser's own
message, which quotes the bytes it choked on and would put an uploaded
document's own text into an error string.

| Code | Meaning | Agent should… |
|---|---|---|
| `E_UNNAMED_FILE` | An attachment arrived with no filename | Re-attach from the file picker |
| `E_UNSUPPORTED_TYPE` | Not one of PDF, docx, markdown or plain text, or no extension at all | Attach an accepted type; `details.accepted` lists them |
| `E_EMPTY_FILE` | Zero bytes | Check the file opens locally, re-attach |
| `E_TYPE_MISMATCH` | The content is a different format than the name claims | Rename to match the content, or re-export |
| `E_UNREADABLE_FILE` | The right extension, and the parser refused the bytes | Re-export and attach the export |
| `E_ENCRYPTED_FILE` | A password-protected PDF | Remove the password or re-export without one |
| `E_NO_TEXT` | Read fine and carries no text, which is what a scan is | Supply a version whose text can be selected |
| `E_EXTRACT_DEPENDENCY` | The PDF or docx parser is not installed | Install the pinned requirements; this one is an environment fault, not the reviewer's |

**No attachment code ever falls back to the published paper.** A reviewer who
attached a document and got a deck built from the paper instead would have no
way to tell, so a refused attachment refuses the run.

**Golden rule for the caller:** never fabricate a slide to paper over an error.
`E_NO_KG`, `E_NO_CORPUS`, and `E_LOW_CONFIDENCE` all mean *stop and escalate* —
a proposal with invented opportunities or EBITDA numbers is worse than no
proposal.

---

## 6 · Examples

### 6.1 Minimal valid request

```yaml
request:
  intent: "generate_project_planning_proposal"
  company: "00000023-0000-4000-8000-000000000023"   # UUID: skips company resolution
  project: "0000001b-0000-4000-8000-00000000001b"    # UUID: skips project resolution
  schema_version: "0.1"
```

### 6.1b A deck carrying two opportunities

```yaml
request:
  intent: "generate_project_planning_proposal"
  company: "00000023-0000-4000-8000-000000000023"
  project: "0000001b-0000-4000-8000-00000000001b"
  schema_version: "0.1"
  opportunity_ids:
    - "0000002b-0000-4000-8000-00000000002b"
    - "0000002d-0000-4000-8000-00000000002d"
```

Seven slides rather than six, and the shape is settled (part two item 15):

| Slide | |
|---|---|
| 1 · Cover | shared |
| 2 · The Opportunity | **once per opportunity**, each written from its own paper |
| 3 · The Platform | merged, one component list |
| 4 · Phased Plan | merged, one timeline |
| 5 · Commercial Terms | shared, one combined set, rows labelled by opportunity |
| 6 · Next Steps | shared |

Slide 2 repeats because each opportunity has its own today-versus-after story.
Slides 3 and 4 merge because an engagement builds one platform on one schedule,
and printing two timelines would describe a project nobody is running. The
commercial terms are one set because QofAI contracts the engagement rather than
the opportunity; the value-mapping rows inside them stay per opportunity and are
never summed, because adding two documents' uplift together would print a figure
neither document states.

Each opportunity's slide is written from ITS OWN paper and never another's. Any
uploaded document is shared, because a PRD describes the whole engagement and is
the base for every opportunity in it.

### 6.2 Ambiguity error response

```yaml
response:
  status: "error"
  error:
    code: "E_AMBIGUOUS_COMPANY"
    message: "'Northwind' matched 4 companies."
    remediation: "Re-request with the same name plus a company_id, or add pe_firm."
    details:
      candidates:
        - { name: "Northwind",       company_id: "0000000b-0000-4000-8000-00000000000b" }
        - { name: "NorthwindAI",     company_id: "…" }
        - { name: "Northwindfact",   company_id: "…" }
        - { name: "Northwindtracs",  company_id: "…" }
```

Corrected 2026-09-15 against the live call. The earlier version of this example
put a `pe_firm` on every candidate, and no granted tool returns one:
`list_companies` records carry `has_kg`, `id`, `name`, `status` and `website`
and nothing else, so a firm cannot appear on a candidate row today. The
implementation carries `pe_firm` when a record has one and omits the key when it
does not, so the shape above grows the field the day the tool does. Where a firm
would have been the thing telling two rows apart, `website` is the discriminator
the registry actually has. The counts are the live ones: a search for "Northwind"
returns four.

### 6.3 No-KG error response

```yaml
response:
  status: "error"
  error:
    code: "E_NO_KG"
    message: "Northwind Freight exists in the registry but has no knowledge graph provisioned."
    remediation: "Provision a KG (KG-NLB pipeline) before requesting a proposal. No proposal can be generated without opportunity data."
    details: { company_id: "…" }
```

---

## 7 · Versioning & guarantees

- `schema_version` is sent by the caller and echoed in the response. Agent OS
  supports the current major; a mismatch returns `E_BAD_REQUEST`.
- **Determinism:** same request + same `last_kg_refresh` → same packet. If the
  KG was refreshed between calls, `generated_at` and figures may change; the
  packet's `kg_source.last_kg_refresh` lets the caller detect drift.
- Field **names** in both contract docs are stable within a major version;
  values and prose are not.
- Error **codes** may be added within a major version. An agent must treat an
  unrecognized code as a stop-and-escalate rather than a retry. Existing codes'
  meanings and `details` shapes do not change within a major.
