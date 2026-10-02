# Agent OS ← Status Builder · REQUEST CONTRACT (Schema v0.1)

Companion to `status-data-packet-EXAMPLE.md` (the response). This defines what
the status-deck-building agent **sends** to Agent OS, how Agent OS resolves the
company **and the project within it**, gates on KG **and on an in-flight project
plan/schedule**, and every response it can get back — including the error cases
the agent must handle before it tries to render a check-in deck.

This mirrors `proposal-data-request-CONTRACT.md`. The resolution, envelope,
provenance, and error machinery are reused verbatim where they apply; only the
status-specific pieces are added (a check-in date, an as-of gate, and the
tracking/workstream/progress fields a status deck needs that a proposal does not).

> **Provisional (v0.1).** This contract and its example packet are provisional
> until Casey confirms the Northwind check-in PDF's slide structure matches the
> intended editable slide set for the status path. Field names may still move
> before v1. The status path is v2 scope; the proposal path ships first.

One company can have more than one project. A status deck is always scoped to a
single project, so the request must name both the company and the project, and
Agent OS resolves and returns data at the company + project level. Unlike a
proposal, a status deck also requires that the project already has a **locked
plan and a schedule to track against** — there is nothing to report status on
otherwise.

---

## 1 · How the agent contacts Agent OS

Agent OS is reached by **dispatching to an agent**, not by calling an HTTP
endpoint. The status builder invokes a dedicated data agent and passes the
request as a fenced YAML block in the prompt. Two supported invocation modes:

```bash
# CLI (background recommended — KG + schedule assembly takes 30–90s)
claude --agent status-data-provider --background "<request YAML below>"
```

```jsonc
// Agent tool (from another agent/orchestrator)
{ "subagent_type": "status-data-provider", "prompt": "<request YAML below>" }
```

> **Note:** `status-data-provider` is the agent that owns this contract. It does
> not exist yet — it would wrap `pe-firm-kg` (company + project + KG-credential
> resolution) and the project **plan/schedule source** (the locked timeline,
> workstream states, and progress trackers), and emit the response packet. Until
> it exists, the request can be sent to `pe-firm-kg` directly, but the packet
> shape is only guaranteed once the dedicated agent wraps it. This is the missing
> piece on the status side, the same way `proposal-data-provider` is on the
> proposal side.

The agent MUST run in **background mode** and poll for completion (per the
Cowork rules in `CLAUDE.md`) — foreground dispatch will appear to time out.

---

## 2 · Request schema

```yaml
request:
  # ---- required ----
  intent: "generate_project_status_deck"         # enum; only value for now
  company: "Northwind"                                # name OR company_id (UUID)
  project: "Implementation Project"              # project name OR project_id (UUID) within the company
  schema_version: "0.1"                          # request contract version

  # ---- optional (sensible defaults applied server-side) ----
  client_short: "Northwind"                           # short brand form for the footers; no source supplies it
  pe_firm: "Woodgrove Partners"                # disambiguates same-named cos; else resolved from KG
  check_in_date: "2026-05-22"                    # the date this check-in reports as of; default: today
  as_of_week: null                               # optional explicit "current project week"; else derived from check_in_date vs. plan start
  template: "status_check_in_v1"                 # default: status_check_in_v1
  sections_requested:                            # default: all
    - cover
    - tracking            # dated Gantt with TODAY marker
    - workstreams         # one status slide per active workstream (variable count)
    - next_steps
  options:
    currency: "USD"                              # default: USD
    voice: "qofai_standard"                      # copy tone; default: qofai_standard
    min_confidence: "medium"                     # abort if packet confidence below this; default: medium
    min_data_completeness: 0.70                  # abort below this; default: 0.70
    include_prose: true                          # false = structured fields only, no slide copy
    workstream_filter: "active"                  # active | all — which workstreams get a status slide; default: active
    include_completed_workstreams: false         # default: false — finished workstreams drop off the deck
```

### Field rules

| Field | Rule |
|---|---|
| `company` | Accepts a display name (fuzzy-matched) **or** a `company_id` UUID (exact). UUID is preferred — it skips resolution ambiguity entirely. |
| `project` | **Required.** Accepts a project name (fuzzy-matched within the resolved company) **or** a `project_id` UUID (exact). Resolved within the company, since one company can have multiple projects. UUID is preferred. If absent, Agent OS returns `E_PROJECT_REQUIRED`; if the name matches more than one project, `E_AMBIGUOUS_PROJECT`; if none, `E_PROJECT_NOT_FOUND`. Agent OS never falls back to company-level data or guesses which project was meant. |
| `check_in_date` | The as-of date for the whole deck. Drives `{check_in_date}`, the dated week columns, and where the **TODAY marker** lands. Default: today. Must fall on or after the plan start; otherwise `E_BAD_REQUEST`. |
| `as_of_week` | Optional override for the current project week ("Week N of M"). If omitted, Agent OS derives N from `check_in_date` against the locked plan start, and M from the plan length. |
| `client_short` | The deck's short brand form, in every page footer and in the folder its decks are filed under. **No data source supplies it**, and none ever has: `company.client_short` is UNSOURCEABLE in the packet's own slot table and is filled on no path, so a request that omits it renders every footer with the company's full registry name. Studio input; given it wins, blank it degrades to the full name rather than showing a missing marker, because a footer short-form is cosmetic and defaultable. The same field and the same rule as the proposal contract's, because the footers are the same and neither deck type has a source for it. |
| `pe_firm` | Only needed to disambiguate a name that matches companies under multiple firms. Ignored when `company` is a UUID. |
| `template` | Determines which sections are valid. A template that has no `next_steps` slide will null that section even if requested. |
| `min_confidence` / `min_data_completeness` | Hard gates. If the assembled packet falls below either, Agent OS returns `E_LOW_CONFIDENCE` instead of a partial deck. |
| `workstream_filter` | `active` (default) returns only workstreams in flight; `all` includes not-yet-started and (with `include_completed_workstreams`) finished ones. The count of returned workstreams sets the deck's slide count — see §4. |
| `sections_requested` | The response only populates these; others are returned as `null` with a `skipped: "not_requested"` marker, so the template renderer stays deterministic. |

---

## 3 · Company & project resolution, KG + schedule gating

Agent OS resolves `company` first, then `project` within that company, before
assembling anything, in this order:

1. **UUID given** → direct lookup. Skip to step 4.
2. **Name given** → case-insensitive fuzzy match against the company registry.
   - **1 match** → proceed.
   - **>1 match** → if `pe_firm` narrows it to one, proceed; else return
     `E_AMBIGUOUS_COMPANY` with the candidate list.
   - **0 matches** → return `E_COMPANY_NOT_FOUND`.
3. **Firm filter** applied if `pe_firm` present.
4. **Project gate** — resolve `project` within the resolved company:
   - **Missing** (`project` not supplied) → return `E_PROJECT_REQUIRED`. Agent OS
     does not fall back to company-level data.
   - **`project_id` UUID given** → direct lookup within the company; **0 matches**
     → `E_PROJECT_NOT_FOUND`.
   - **Name given** → fuzzy match against the company's projects. **1 match** →
     proceed. **>1 match** → `E_AMBIGUOUS_PROJECT` with the candidate list. **0
     matches** → `E_PROJECT_NOT_FOUND`.
5. **KG gate** — call `get_kg_credentials(company_id)`:
   - No KG settings / credential error → `E_NO_KG`.
   - Credentials present but Bolt handshake fails / times out → `E_KG_UNREACHABLE`.
     (Same historically-flaky instances as the proposal path — Lucerne,
     Proseware, Adatum, Adatum Safety Group. Treat a name-resolves-but-KG-down case
     distinctly from no-KG-at-all.)
6. **Schedule gate** *(status-specific)* — confirm the resolved project has a
   **locked plan and a schedule** to track against:
   - No plan / no timeline on the project → `E_NO_SCHEDULE`. A status deck reports
     progress against a plan; with no plan there is nothing to report. Route the
     caller to the proposal/planning path first.
   - Plan exists but **no active workstreams** (nothing in flight as of
     `check_in_date`, and `workstream_filter: active`) → `E_NO_ACTIVE_WORKSTREAMS`.

Only after all of these pass does Agent OS assemble the packet for that company +
project as of the requested check-in date.

---

## 4 · Response envelope

Success and error share one envelope so the agent can branch on `status` alone.

```yaml
response:
  status: "ok"                     # ok | error
  request_echo: { ... }            # the request, for traceability
  # on status: ok ↓
  packet: "<the markdown from status-data-packet-EXAMPLE.md>"
  # on status: error ↓
  error:
    code: "E_NO_SCHEDULE"
    message: "Human-readable explanation"
    remediation: "What the caller (or a human) should do next"
    details: { ... }               # code-specific payload (e.g. candidate list)
```

On `status: ok`, `packet` carries the full response markdown (its own
frontmatter reports `confidence` and `data_completeness` — the agent should
re-check these against its request thresholds as a belt-and-suspenders step).

**Variable slide count.** A status deck is **not** a fixed five slides. The deck
length is `cover + tracking + N workstreams + next_steps`, where `N` is the count
of workstreams the packet returns (Northwind returned 2 → 5 slides). The packet
reports `deck.total_slides` and `deck.slide_count_formula` so the renderer can
set the footer page numbering (`NN / {total_slides}`) from data, never from a
hardcoded `05`. Nothing downstream may assume a fixed count.

---

## 5 · Error codes

| Code | Meaning | `details` payload | Agent should… |
|---|---|---|---|
| `E_COMPANY_NOT_FOUND` | No registry match for the name | `{ query, closest: [names] }` | Surface to human; suggest closest / ask for UUID |
| `E_AMBIGUOUS_COMPANY` | Name matched >1 company | `{ candidates: [{name, company_id, pe_firm}] }` | Re-request with a UUID or `pe_firm` |
| `E_PROJECT_REQUIRED` | Company resolved but no `project` supplied | `{ company_id }` | **Stop.** Ask the user which project; do not fall back to company-level data |
| `E_AMBIGUOUS_PROJECT` | Project name matched >1 project in the company | `{ company_id, candidates: [{name, project_id}] }` | Re-request with a `project_id`; do not guess |
| `E_PROJECT_NOT_FOUND` | No project match within the resolved company | `{ company_id, query, closest: [names] }` | Surface to human; suggest closest / ask for `project_id` |
| `E_NO_KG` | Company exists but has no knowledge graph provisioned | `{ company_id }` | **Stop.** Route to KG-provisioning |
| `E_KG_UNREACHABLE` | KG provisioned but Bolt handshake failed | `{ company_id, neo4j_url, last_ok }` | Retry with backoff; escalate to human if persistent |
| `E_SOURCE_UNREACHABLE` | Agent OS could not be reached at all: no response, a rejected request, or a reply the agent could not parse. Raised BY THE AGENT, not returned by Agent OS, which by definition never answered. The only code in this table with that origin. | `{ endpoint_kind, error_type }` | Retry once with backoff; escalate to whoever can see the platform's status. Do not re-run the deck. |
| `E_NO_SCHEDULE` | Project resolved but has no locked plan/timeline to track against | `{ company_id, project_id }` | **Stop.** No status deck is possible; route to the proposal/planning path first |
| `E_NO_ACTIVE_WORKSTREAMS` | Plan exists but nothing is in flight as of `check_in_date` | `{ company_id, project_id, check_in_date }` | Confirm the date, or re-request with `workstream_filter: all` |
| `E_LOW_CONFIDENCE` | Packet assembled but below `min_confidence` / `min_data_completeness` | `{ confidence, data_completeness, missing_fields: [] }` | Do NOT render; return for human review |
| `E_BAD_REQUEST` | Missing/invalid required field, bad `check_in_date`, or unknown `intent` | `{ field, reason }` | Fix payload; do not retry verbatim |
| `E_TEMPLATE_UNKNOWN` | `template` not registered | `{ template, available: [] }` | Pick a valid template |

**Golden rule for the caller:** never fabricate a slide to paper over an error.
`E_NO_KG`, `E_NO_SCHEDULE`, `E_NO_ACTIVE_WORKSTREAMS`, and `E_LOW_CONFIDENCE` all
mean *stop and escalate* — a check-in with invented progress or a fabricated
TODAY marker is worse than no deck. Progress state (done vs. pending) and the
TODAY marker are load-bearing; do not synthesize them.

---

## 6 · Examples

### 6.1 Minimal valid request

```yaml
request:
  intent: "generate_project_status_deck"
  company: "0000000b-0000-4000-8000-00000000000b"   # UUID: skips company resolution
  project: "0000001b-0000-4000-8000-00000000001b"    # UUID: skips project resolution
  check_in_date: "2026-05-22"
  schema_version: "0.1"
```

### 6.2 No-schedule error response (status-specific)

```yaml
response:
  status: "error"
  error:
    code: "E_NO_SCHEDULE"
    message: "Northwind · 'Discovery Engagement' resolved, but no locked plan or timeline exists to track against."
    remediation: "Generate a project-planning proposal and lock the plan first. A status check-in reports progress against a plan; there is nothing to report without one."
    details: { company_id: "0000000b-0000-4000-8000-00000000000b", project_id: "…" }
```

### 6.3 No-active-workstreams error response

```yaml
response:
  status: "error"
  error:
    code: "E_NO_ACTIVE_WORKSTREAMS"
    message: "The plan exists but no workstream is in flight as of 2026-05-22."
    remediation: "Confirm the check_in_date, or re-request with workstream_filter: all to include not-yet-started workstreams."
    details: { company_id: "…", project_id: "…", check_in_date: "2026-05-22" }
```

---

## 7 · Versioning & guarantees

- `schema_version` is sent by the caller and echoed in the response. Agent OS
  supports the current major; a mismatch returns `E_BAD_REQUEST`.
- **Determinism:** same request + same `check_in_date` + same `last_kg_refresh` +
  same plan revision → same packet. The dated week columns and the TODAY marker
  are a pure function of `check_in_date` and the locked plan start, so they are
  reproducible. If the plan was re-baselined or the KG refreshed between calls,
  `generated_at`, the schedule, and figures may change; the packet's
  `kg_source.last_kg_refresh` and `tracking.plan_revision` let the caller detect
  drift.
- Field **names** in both contract docs are stable within a major version;
  values and prose are not.
- **Provisional until Casey sign-off:** while at v0.1, treat the workstream slide
  shape (TODAY/AFTER vs. WHERE WE ARE/TARGET) and the progress-tracker groups as
  subject to change pending confirmation that the Northwind PDF matches the intended
  editable slide set.
