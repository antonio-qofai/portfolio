---
# ============================================================================
# AGENT OS → STATUS BUILDER · RESPONSE DATA PACKET (EXAMPLE / SCHEMA v0.1)
# ----------------------------------------------------------------------------
# This is a REPRESENTATIVE, SYNTHETIC example fixture of the markdown Agent OS
# returns when a status-deck-building agent requests the data needed to populate
# a Project Status / Check-in deck (modeled on the Northwind / Woodgrove Partners check-in
# deck, 5 slides on this run: cover + tracking + 2 workstreams + next steps).
#
# The consuming agent maps each field below to a placeholder in its template.
# All values here are illustrative for a representative client ("Northwind"), the
# same way proposal-data-packet-EXAMPLE.md uses synthetic "Ridgeline". Field
# NAMES and STRUCTURE are the contract; values are examples and must never be
# emitted for another client.
#
# PROVISIONAL (v0.1): this fixture and its contract are provisional until Casey
# confirms the Northwind check-in PDF's slide structure matches the intended editable
# slide set. The workstream slide shape and progress-tracker groups may change.
# ============================================================================
schema_version: "0.1"
packet_type: "project_status_check_in"
generated_at: "2026-05-22T15:00:00Z"
generated_by: "agent-os/pe-firm-kg + project-plan-schedule-source"
kg_source:
  company_id: "REPLACED-WITH-REAL-UUID"
  project_id: "REPLACED-WITH-REAL-UUID"
  neo4j_reachable: true
  last_kg_refresh: "2026-05-20"
confidence: "high"          # high | medium | low — driven by data completeness
data_completeness: 0.90     # 0–1; share of required fields populated from source vs. defaulted
---

# Status Data Packet — Northwind · Implementation Project Check-in

> **How to read this file.** Sections map to status slides. The cover, tracking,
> and next-steps sections are singletons; the **workstreams** section is an
> ARRAY — the renderer emits one status slide per entry, so the deck length
> flexes with how many workstreams are active (see `deck.total_slides`). Field
> keys are stable; a template placeholder like
> `{{workstreams[0].today.metrics[0].value}}` resolves against the structured
> blocks below. Prose is ready-to-use slide copy (already in QofAI voice);
> numeric/tracker blocks are for the agent to render into the Gantt, progress
> trackers, and callouts. If a field is `null`, it was not derivable from the
> source — see the final **Provenance & Gaps** section before using a default.
>
> **Status decks are time-aware.** Everything hangs off `check_in_date` and the
> locked plan start: the dated week columns, the TODAY marker, and "Week N of M"
> are all functions of those two inputs, not free text.

---

## 0 · Request Context (what triggered this packet)

The status agent sent Agent OS the following (echoed back for traceability):

```yaml
request:
  intent: "generate_project_status_deck"
  company: "Northwind"                              # name OR company_id
  project: "Implementation Project"            # name OR project_id within the company
  pe_firm: "Woodgrove Partners"              # optional; resolved from KG if omitted
  check_in_date: "2026-05-22"
  template: "status_check_in_v1"               # which deck template to fill
  sections_requested:                          # omit for "all"
    - cover
    - tracking
    - workstreams
    - next_steps
  options:
    workstream_filter: "active"
```

Agent OS resolved `Northwind` → one KG match (company_id above), resolved
`Implementation Project` → one project within it, confirmed the Neo4j instance
was reachable, confirmed a locked plan and schedule exist, found 2 active
workstreams, and assembled the packet below as of the check-in date.

---

## 1 · Deck & Engagement Context  → *Cover slide + fields that recur on every slide*

```yaml
deck:
  deck_type: "status"
  template: "status_check_in_v1"
  # Slide count is DERIVED, never hardcoded. Footer numbering (NN / total_slides)
  # must read from total_slides, not a literal 05.
  slide_count_formula: "cover + tracking + N_workstreams + next_steps"
  n_workstreams: 2
  total_slides: 5              # 1 (cover) + 1 (tracking) + 2 (workstreams) + 1 (next_steps)

company:
  name: "Northwind"
  client_short: "Northwind"          # short brand form for deck footers; optional-but-expected. If null, consumer falls back to name.
  pe_firm: "Woodgrove Partners"

engagement:
  check_in_date: "2026-05-22"   # ISO; consumer formats to "MAY 22 2026" for display
  check_in_date_display: "MAY 22 2026"
  check_in_month_year: "MAY 2026"   # for the page footer
  project_week: "Week 3 of 12"      # current week N of plan length M; derived from check_in_date vs. plan start
  project_week_n: 3
  project_week_m: 12
  confidentiality: "QOFAI CONFIDENTIAL"

cover:
  deck_kicker: "PROJECT CHECK-IN · MAY 22 2026"
  prepared_for: "PREPARED FOR NORTHWIND · Woodgrove Partners"
  deck_title_accent: "Implementation"       # accent-color line
  deck_title_primary: "Project Check-in."    # primary line
  status_subtitle: "Status update: Dynamic Pricing & Quoting and Production Scheduling."
```

**Cover copy**
- **Kicker:** `PROJECT CHECK-IN · MAY 22 2026`
- **Prepared for:** `PREPARED FOR NORTHWIND · Woodgrove Partners`
- **Title:** `Implementation` (accent) / `Project Check-in.` (primary)
- **Subtitle:** `Status update: Dynamic Pricing & Quoting and Production Scheduling.`

**Page footer (every slide):** `QOFAI + NORTHWIND · IMPLEMENTATION PROJECT CHECK-IN · MAY 2026 · NN / 05`
(the `05` denominator is `deck.total_slides`, rendered from data.)

---

## 2 · Project Tracking  → *Slide "PHASED ROLLOUT · WEEK N OF M" (dated Gantt with TODAY marker)*

Section label: **PHASED ROLLOUT · WEEK 3 OF 12**
Headline: **Project Tracking.**
Summary: *Dynamic Pricing & Quoting on track to deliver PRD and Project Plan Week 4; Production Scheduling slips one week, waiting on NetSuite integration.*

> This is the time-aware core of a status deck. Columns are **dated** weeks (not
> the proposal's relative "1–2, 3–4" buckets), and a **TODAY marker** is dropped
> on the current week. Each bar carries an explicit `category` that names its
> **color group** (see `bar_categories`), so the renderer colors bars
> deterministically from data rather than from progress or position. Color is
> NOT progress: in the reference deck two completed bars (Cycle 1, Rob intro) are
> different colors (blue vs. maroon), because color tracks phase/track, not
> done-vs-in-progress. The `state` field is annotation only — it drives the
> dashed buffer fill and slip callouts, never the fill color. The column set can
> run past `project_week_m` when the plan shows buffer/phases beyond the reporting
> window (here 14 dated columns for a 12-week project).

```yaml
tracking:
  plan_revision: "r3"              # bumps when the plan is re-baselined; part of the determinism key
  section_label: "PHASED ROLLOUT · WEEK 3 OF 12"
  headline: "Project Tracking."
  summary: "Dynamic Pricing & Quoting on track to deliver PRD and Project Plan Week 4; Production Scheduling slips one week, waiting on NetSuite integration."

  # Dated week columns. Weekly cadence, Monday-anchored. label is the display form.
  columns:
    - { id: "W1",  date: "2026-05-04", label: "May 4"  }
    - { id: "W2",  date: "2026-05-11", label: "May 11" }
    - { id: "W3",  date: "2026-05-18", label: "May 18" }
    - { id: "W4",  date: "2026-05-25", label: "May 25" }
    - { id: "W5",  date: "2026-06-01", label: "Jun 1"  }
    - { id: "W6",  date: "2026-06-08", label: "Jun 8"  }
    - { id: "W7",  date: "2026-06-15", label: "Jun 15" }
    - { id: "W8",  date: "2026-06-22", label: "Jun 22" }
    - { id: "W9",  date: "2026-06-29", label: "Jun 29" }
    - { id: "W10", date: "2026-07-06", label: "Jul 6"  }
    - { id: "W11", date: "2026-07-13", label: "Jul 13" }
    - { id: "W12", date: "2026-07-20", label: "Jul 20" }
    - { id: "W13", date: "2026-07-27", label: "Jul 27" }
    - { id: "W14", date: "2026-08-03", label: "Aug 3"  }

  # The TODAY marker: which column the current date lands on. Derived from
  # check_in_date (2026-05-22 falls in the W3 week beginning May 18).
  today_marker:
    on_week: "W3"
    label: "TODAY"

  # Color legend for the Gantt. Each bar names one of these categories; the
  # renderer maps category → fill color from this table, so coloring is fully
  # data-driven and deterministic. Categories track PHASE / TRACK, not progress.
  # Grouping reproduced from the Northwind reference deck:
  bar_categories:
    - { id: "scoping_discovery", color: "green",  covers: "Scoping / discovery" }
    - { id: "build_mirror",      color: "blue",   covers: "Build cycles + QofAI mirror phases" }
    - { id: "integration_golive", color: "maroon", covers: "Integration + production / go-live phases" }
    - { id: "data_access",       color: "navy",   covers: "Data & access" }
    - { id: "buffer",            color: "pink",   covers: "Schedule buffer" }

  # Lanes = phase/workstream rows, top to bottom, each with one or more bars.
  # start_week / end_week are inclusive column ids.
  # `category` drives the FILL COLOR (maps to bar_categories above).
  # `state` is ANNOTATION ONLY (complete | in_progress | upcoming | buffer) —
  # it drives the dashed buffer fill and slip callouts, never the color. Note a
  # lane's bars usually share one category, but not always: PHASE 4 holds a
  # maroon go-live bar and a pink buffer bar, so category lives on each bar.
  lanes:
    - name: "SCOPING · DYNAMIC PRICING / QUOTING"
      bars:
        - { label: "Discovery + Project Planning", start_week: "W2", end_week: "W3", category: "scoping_discovery", state: "in_progress" }
    - name: "BUILD"
      bars:
        - { label: "Cycle 1 · Hardening + auth/users", start_week: "W1", end_week: "W2", category: "build_mirror", state: "complete" }
        - { label: "Cycle 2 · QA + flexibility",        start_week: "W3", end_week: "W4", category: "build_mirror", state: "in_progress" }
    - name: "INTEGRATION"
      bars:
        - { label: "Rob intro · Planwright + Access pull", start_week: "W1", end_week: "W2", category: "integration_golive", state: "complete" }
        - { label: "NetSuite + Planwright integrations",   start_week: "W3", end_week: "W5", category: "integration_golive", state: "in_progress" }
    - name: "DATA & ACCESS"
      bars:
        - { label: "Machine list + cut rates", start_week: "W6", end_week: "W6", category: "data_access", state: "upcoming" }
    - name: "PHASE 1 · QofAI MIRRORS"
      bars:
        - { label: "QofAI Schedule mirrors Planwright", start_week: "W6", end_week: "W7", category: "build_mirror", state: "upcoming" }
    - name: "PHASE 2 · QofAI → PLANWRIGHT"
      bars:
        - { label: "QofAI Schedule input into Ralph / Planwright", start_week: "W8", end_week: "W9", category: "build_mirror", state: "upcoming" }
    - name: "PHASE 3 · ONE LINE"
      bars:
        - { label: "QofAI controls 1-line production", start_week: "W10", end_week: "W11", category: "integration_golive", state: "upcoming" }
    - name: "PHASE 4 · ALL LINES"
      bars:
        - { label: "QofAI live on all lines",   start_week: "W12", end_week: "W13", category: "integration_golive", state: "upcoming" }
        - { label: "Deployment delay buffer",   start_week: "W14", end_week: "W14", category: "buffer", state: "buffer" }

  # Explicit slip/buffer callouts. kind: slip | buffer. Keep separate from bars
  # so the renderer can annotate them (dashed fill, label) distinctly.
  slip_or_buffer_markers:
    - { label: "Deployment delay buffer", week: "W14", kind: "buffer" }
    - { label: "Production Scheduling slips one week (NetSuite integration)", week: "W4", kind: "slip", workstream: "production_scheduling" }
```

---

## 3 · Workstreams  → *one status slide per active workstream (Slides 3…N)*

> **Variable count.** This is an array. The renderer emits one slide per entry,
> in order. Northwind has 2 active workstreams → 2 slides here. Do NOT hardcode two.
>
> Each workstream declares a `stage` (`new` | `existing`) that selects its
> framing: a `new` workstream uses **TODAY → AFTER** (current-state pain vs. a
> target horizon); an `existing` workstream (already in build) uses
> **WHERE WE ARE → TARGET**. Both stages carry the status-specific
> `progress_tracker` (done vs. pending against the plan) and `next_steps`. Fields
> not used by a given stage are `null`.

```yaml
workstreams:
  # ---------- Workstream 1: new project ----------
  - id: "dynamic_pricing_quoting"
    stage: "new"
    section_label: "NEW PROJECT · STATUS"
    name_accent: "Dynamic Pricing & Quoting"        # accent-color span
    name_full: "AI Dynamic Pricing & Quoting."
    summary: "An AI pricing & quoting engine with embedded steel escalation, segment floor prices, and discount governance."
    summary_hook: "Stop the margin bleed before steel spikes again."   # bolded tail

    # TODAY block (stage: new) — current-state metrics + pain
    today:
      label: "TODAY"
      metrics:
        - { value: "−14.0pp", label: "Gross margin compressed from 58.0% to 44.0%" }
        - { value: "+40%",    label: "Coil steel: $0.46 → $0.64/lb · ~$1.0–1.5M COGS impact" }
      pain_bullets:
        - "4 salespeople pricing independently from individual Excel sheets"
        - "No standardized cost inputs, margin floors, or discount governance"

    # AFTER block (stage: new) — target horizon
    after:
      horizon: "AFTER — TARGET IN 6 MONTHS"
      metrics:
        - { value: ">50%", label: "Gross margin stabilized; recover 3–5pp of compression" }
      capability_bullets:
        - "Real-time steel cost inputs + automatic escalation clauses"
        - "Capacity-aware quotes tied to live production schedule"
        - "Discount governance with automated approval workflows"

    # WHERE WE ARE / TARGET blocks unused for a new-stage workstream
    where_we_are: null
    target: null

    # PROGRESS block (status-specific — this is what makes it a status slide).
    # groups[].status_label is the badge (RECEIVED / 2 OF 3 / IN PROCESS / COMPLETE).
    # item.state: done | pending | in_process.
    progress_tracker:
      label: "TWO-WEEK PROJECT PLANNING SPRINT"
      right_label: null
      groups:
        - name: "DATA REQUESTS"
          status_label: "RECEIVED"
          items:
            - { label: "Quote templates",   detail: null, state: "done" }
            - { label: "Pricing templates", detail: null, state: "done" }
            - { label: "Material costing",  detail: null, state: "done" }
        - name: "INTERVIEWS"
          status_label: "2 OF 3"
          items:
            - { label: "Charlotte",         detail: "Quoting process & best practices", state: "done" }
            - { label: "Brent",             detail: "Market intel, historical practices", state: "done" }
            - { label: "Quentin — needed?", detail: "Oversight, deal desk, corporate goals", state: "pending" }
        - name: "WORKFLOW MAPPING"
          status_label: "IN PROCESS"
          items:
            - { label: "Mapping current quoting workflow", detail: "Cost inputs · margin logic · approval paths", state: "in_process" }

    next_steps:
      - "Deliver project requirements document"
      - "Implementation development plan"

  # ---------- Workstream 2: existing project (already in build) ----------
  - id: "production_scheduling"
    stage: "existing"
    section_label: "EXISTING PROJECT · STATUS"
    name_accent: "Production Scheduling"
    name_full: "AI-Powered Production Scheduling & Backlog Management."
    summary: "A scheduler that fits into your existing workflow, but solves production schedules to maximize production and gross margin based on backlog and plant constraints."
    summary_hook: null

    # TODAY / AFTER unused for an existing-stage workstream
    today: null
    after: null

    # WHERE WE ARE block (stage: existing) — current position with progress detail.
    # metrics carry a big value + supporting lines; notes are standalone status lines.
    where_we_are:
      label: "WHERE WE ARE"
      metrics:
        - { value: "Week 3/12", label: "Build cycles 1 & 2 complete · on schedule" }
        - { value: "Phase 1",   label: "QofAI mirrors Planwright · begins Week 5" }
      notes:
        - "Solver running against current Planwright logic"
        - "Auth, users, and tenant provisioning complete"

    # TARGET block (stage: existing)
    target:
      horizon: "TARGET — ALL LINES LIVE BY WEEK 12"
      metrics:
        - { value: "4 phases", label: "Mirror → input → one line → all lines" }
        - { value: "26 wks",   label: "1, 4, 13, and 26-week schedule views" }
      notes:
        - "Constraint-aware solver with human-in-the-loop reruns"
        - "Per-line one-sheeter, yard fill, inventory min/max alerts"

    progress_tracker:
      label: "BUILD & DISCOVERY — WEEKS 1–3"
      right_label: "WEEK 3 OF 12"
      groups:
        - name: "INTERVIEWS"
          status_label: "COMPLETE"
          items:
            - { label: "Rob",   detail: "Planwright logic walkthrough",        state: "done" }
            - { label: "Derek", detail: "Floor operations & changeovers",      state: "done" }
            - { label: "Ralph", detail: "Scheduling heuristics & priorities",  state: "done" }
        - name: "DATA & ACCESS"
          status_label: "2 OF 4"
          items:
            - { label: "Planwright / MS Access database", detail: null, state: "done" }
            - { label: "Quotes and pricing",              detail: null, state: "done" }
            - { label: "NetSuite / backlog access",       detail: null, state: "pending" }
            - { label: "Machine list + cut rates",       detail: null, state: "pending" }
        - name: "WORKFLOW MAPPING"
          status_label: "IN PROCESS"
          items:
            - { label: "Labor settings",      detail: null, state: "done" }
            - { label: "Scheduling",          detail: null, state: "done" }
            - { label: "Order / backlog input", detail: null, state: "in_process" }
            - { label: "Revenue / value input", detail: null, state: "in_process" }

    next_steps:
      - "Week 4–5: Pricing inputs · NetSuite integration for backlog ingestion"
      - "Week 6+: QofAI mirrors Planwright · track real-time line production (TBD)"
```

---

## 4 · Next Steps  → *Slide "NEXT STEPS" (parallel columns, one per workstream)*

Section label: **NEXT STEPS**
Headline: **Two projects, parallel next moves.**
Summary: *Pricing & quoting moves from discovery into specification; production scheduling integrates with real orders.*

> One column per active workstream, each a numbered list (01/02/03). The column
> count tracks the workstream count — do not assume two. `workstream_id` lets the
> renderer match a column to its workstream's accent color.

```yaml
next_steps_slide:
  section_label: "NEXT STEPS"
  headline: "Two projects, parallel next moves."
  summary: "Pricing & quoting moves from discovery into specification; production scheduling integrates with real orders."
  columns:
    - workstream_id: "dynamic_pricing_quoting"
      title: "DYNAMIC PRICING & QUOTING"
      steps:
        - { number: "01", title: "Product requirements document", body: "Document quoting workflow, cost inputs, escalation logic, and discount governance for build sign-off." }
        - { number: "02", title: "Implementation plan",           body: "Phasing, owners, integration points, and a Phase 1 deployment target inside 4–6 weeks." }
        - { number: "03", title: "Performance-based pricing analysis", body: "Baseline margin, scenario modeling, and proposed compensation tied to realized EBITDA gains." }
    - workstream_id: "production_scheduling"
      title: "PRODUCTION SCHEDULING"
      steps:
        - { number: "01", title: "Integrate pricing information from quotes", body: "Pull quote-level margin into the solver so the schedule optimizes against true contribution, not volume." }
        - { number: "02", title: "Integrate with NetSuite",                   body: "Live backlog, orders, and inventory from NetSuite — replacing the manual hand-offs in place today." }
        - { number: "03", title: "Prep to mirror production",                 body: "Calibrate the solver against the next two weeks of Planwright output so Phase 1 mirroring lands clean — line capabilities, changeovers, and labor coverage validated against actuals." }
```

---

## 5 · Provenance & Gaps  *(not rendered — for the agent's guardrails)*

```yaml
provenance:
  from_kg:                         # fields pulled directly from Neo4j / company registry
    - company.*
    - company.client_short         # short brand form; from KG when present, else consumer defaults to company.name
    - engagement.pe_firm
  from_plan_schedule:              # from the locked project plan / schedule source
    - engagement.project_week      # N and M from plan start + plan length
    - tracking.columns             # dated week grid from plan start + weekly cadence
    - tracking.lanes               # phase/workstream bars, start/end weeks, category (color group), state (annotation)
    - tracking.bar_categories      # color legend; category→color mapping, tracks phase/track not progress
    - tracking.slip_or_buffer_markers
    - workstreams[].progress_tracker   # done vs. pending against plan tasks — load-bearing, never synthesized
    - workstreams[].where_we_are
  derived:                         # computed by the provider, not raw facts
    - engagement.check_in_date_display   # formatted from check_in_date
    - engagement.check_in_month_year
    - deck.total_slides            # cover + tracking + N_workstreams + next_steps
    - tracking.today_marker        # which column check_in_date lands on
  templated_defaults:              # NOT from this company's data — deck standards
    - engagement.confidentiality   # standard "QOFAI CONFIDENTIAL"
    - deck.slide_count_formula
  modeled_or_prose:                # copy/targets written by the provider in QofAI voice
    - cover.status_subtitle
    - tracking.summary
    - workstreams[].summary
    - workstreams[].summary_hook
    - workstreams[].after           # target horizon + capabilities (aspirational, not measured)
    - workstreams[].target
    - next_steps_slide.*
  gaps:                            # null / low-confidence fields the agent must flag
    - field: "workstreams[0].after.metrics[0].value"
      reason: "Target (>50% gross margin) is a modeled goal, not a measured actual; label as target, not achieved."
    - field: "tracking.slip_or_buffer_markers[1]"
      reason: "Slip attribution (NetSuite dependency) is a judgment call; confirm with delivery lead before it drives the summary line."
warnings:
  - "Progress state (done/pending/in_process) and the TODAY marker are load-bearing. Do NOT synthesize them — an invented progress tracker is worse than no deck. If the plan/schedule source is stale, return E_LOW_CONFIDENCE."
  - "If neo4j_reachable=false, no locked plan exists, or data_completeness < 0.7, do NOT auto-generate — return to human review."
  - "PROVISIONAL (v0.1): workstream slide shape (new: TODAY/AFTER vs. existing: WHERE WE ARE/TARGET) and progress-tracker groups are subject to change pending Casey's confirmation that the Northwind PDF matches the intended editable slide set."
```
