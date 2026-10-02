---
# ============================================================================
# AGENT OS → STATUS BUILDER · RESPONSE DATA PACKET (SECOND SYNTHETIC FIXTURE)
# ----------------------------------------------------------------------------
# A second, deliberately different synthetic status packet, authored to the same
# schema_version 0.1 shape as status-data-packet-EXAMPLE.md but for a different
# client ("Vantgo Logistics" / Northgate Capital) with THREE active workstreams,
# so the pipeline's variable slide count (PRD S7: N=3 → 6 slides) and its
# anti-hardcoding guarantee (PRD S8: zero cross-client leakage) can both be
# tested against a genuinely distinct packet. All values are synthetic and must
# never be emitted for another client.
# ============================================================================
schema_version: "0.1"
packet_type: "project_status_check_in"
generated_at: "2026-06-15T15:00:00Z"
generated_by: "agent-os/pe-firm-kg + project-plan-schedule-source"
kg_source:
  company_id: "REPLACED-WITH-REAL-UUID"
  project_id: "REPLACED-WITH-REAL-UUID"
  neo4j_reachable: true
  last_kg_refresh: "2026-06-12"
confidence: "high"
data_completeness: 0.88
---

# Status Data Packet — Vantgo · Fulfillment Automation Check-in

> Second synthetic fixture. Three active workstreams → six rendered slides.

---

## 0 · Request Context (what triggered this packet)

```yaml
request:
  intent: "generate_project_status_deck"
  company: "Vantgo Logistics"
  project: "Fulfillment Automation"
  pe_firm: "Northgate Capital"
  check_in_date: "2026-06-15"
  template: "status_check_in_v1"
  sections_requested:
    - cover
    - tracking
    - workstreams
    - next_steps
  options:
    workstream_filter: "active"
```

---

## 1 · Deck & Engagement Context  → *Cover slide + fields that recur on every slide*

```yaml
deck:
  deck_type: "status"
  template: "status_check_in_v1"
  slide_count_formula: "cover + tracking + N_workstreams + next_steps"
  n_workstreams: 3
  total_slides: 6

company:
  name: "Vantgo Logistics"
  client_short: "Vantgo"
  pe_firm: "Northgate Capital"

engagement:
  check_in_date: "2026-06-15"
  check_in_date_display: "JUN 15 2026"
  check_in_month_year: "JUN 2026"
  project_week: "Week 5 of 16"
  project_week_n: 5
  project_week_m: 16
  confidentiality: "QOFAI CONFIDENTIAL"

cover:
  deck_kicker: "PROJECT CHECK-IN · JUN 15 2026"
  prepared_for: "PREPARED FOR VANTGO · NORTHGATE CAPITAL"
  deck_title_accent: "Fulfillment"
  deck_title_primary: "Automation Check-in."
  status_subtitle: "Status update: Dispatch Optimization, Warehouse Robotics, and Freight Audit."
```

---

## 2 · Project Tracking  → *Slide "PHASED ROLLOUT · WEEK N OF M" (dated Gantt with TODAY marker)*

```yaml
tracking:
  plan_revision: "r2"
  section_label: "PHASED ROLLOUT · WEEK 5 OF 16"
  headline: "Project Tracking."
  summary: "Dispatch Optimization on track for pilot Week 8; Warehouse Robotics integration slips one week on WMS access; Freight Audit begins Week 7."
  columns:
    - { id: "W1",  date: "2026-05-18", label: "May 18" }
    - { id: "W2",  date: "2026-05-25", label: "May 25" }
    - { id: "W3",  date: "2026-06-01", label: "Jun 1"  }
    - { id: "W4",  date: "2026-06-08", label: "Jun 8"  }
    - { id: "W5",  date: "2026-06-15", label: "Jun 15" }
    - { id: "W6",  date: "2026-06-22", label: "Jun 22" }
    - { id: "W7",  date: "2026-06-29", label: "Jun 29" }
    - { id: "W8",  date: "2026-07-06", label: "Jul 6"  }
    - { id: "W9",  date: "2026-07-13", label: "Jul 13" }
    - { id: "W10", date: "2026-07-20", label: "Jul 20" }
    - { id: "W11", date: "2026-07-27", label: "Jul 27" }
    - { id: "W12", date: "2026-08-03", label: "Aug 3"  }
    - { id: "W13", date: "2026-08-10", label: "Aug 10" }
    - { id: "W14", date: "2026-08-17", label: "Aug 17" }
    - { id: "W15", date: "2026-08-24", label: "Aug 24" }
    - { id: "W16", date: "2026-08-31", label: "Aug 31" }
  today_marker:
    on_week: "W5"
    label: "TODAY"
  bar_categories:
    - { id: "scoping_discovery", color: "green",  covers: "Scoping / discovery" }
    - { id: "build_mirror",      color: "blue",   covers: "Build cycles" }
    - { id: "integration_golive", color: "maroon", covers: "Integration + go-live" }
    - { id: "data_access",       color: "navy",   covers: "Data & access" }
    - { id: "buffer",            color: "pink",   covers: "Schedule buffer" }
  lanes:
    - name: "SCOPING · DISPATCH"
      bars:
        - { label: "Discovery + routing model", start_week: "W1", end_week: "W3", category: "scoping_discovery", state: "complete" }
    - name: "BUILD · DISPATCH"
      bars:
        - { label: "Cycle 1 · solver core", start_week: "W3", end_week: "W5", category: "build_mirror", state: "in_progress" }
        - { label: "Cycle 2 · pilot", start_week: "W6", end_week: "W8", category: "build_mirror", state: "upcoming" }
    - name: "INTEGRATION · ROBOTICS"
      bars:
        - { label: "WMS + fleet access pull", start_week: "W2", end_week: "W4", category: "integration_golive", state: "complete" }
        - { label: "Robotics control integration", start_week: "W5", end_week: "W8", category: "integration_golive", state: "in_progress" }
        - { label: "Site rollout · +2 sites", start_week: "W9", end_week: "W12", category: "integration_golive", state: "upcoming" }
    - name: "DATA & ACCESS"
      bars:
        - { label: "Freight rate feeds", start_week: "W7", end_week: "W7", category: "data_access", state: "upcoming" }
    - name: "PHASE · GO-LIVE"
      bars:
        - { label: "All-site rollout", start_week: "W13", end_week: "W15", category: "integration_golive", state: "upcoming" }
        - { label: "Deployment buffer", start_week: "W16", end_week: "W16", category: "buffer", state: "buffer" }
  slip_or_buffer_markers:
    - { label: "Deployment buffer", week: "W16", kind: "buffer" }
    - { label: "Warehouse Robotics slips one week (WMS access)", week: "W6", kind: "slip", workstream: "warehouse_robotics" }
```

---

## 3 · Workstreams  → *one status slide per active workstream (Slides 3…N)*

```yaml
workstreams:
  - id: "dispatch_optimization"
    stage: "new"
    section_label: "NEW PROJECT · STATUS"
    name_accent: "Dispatch Optimization"
    name_full: "AI Dispatch Optimization."
    summary: "A routing engine that assigns loads to drivers against live traffic, hours-of-service, and margin."
    summary_hook: "Stop paying for empty miles."
    today:
      label: "TODAY"
      metrics:
        - { value: "22%", label: "Empty-mile ratio across the fleet" }
        - { value: "3 hrs", label: "Manual dispatch planning per shift" }
      pain_bullets:
        - "Dispatchers plan routes by hand in spreadsheets"
        - "No margin-aware load assignment"
    after:
      horizon: "AFTER — TARGET IN 5 MONTHS"
      metrics:
        - { value: "<15%", label: "Empty-mile ratio after optimization" }
      capability_bullets:
        - "Margin-aware assignment tied to live rates"
        - "Automatic hours-of-service compliance"
    where_we_are: null
    target: null
    progress_tracker:
      label: "TWO-WEEK DISCOVERY SPRINT"
      right_label: null
      groups:
        - name: "DATA REQUESTS"
          status_label: "RECEIVED"
          items:
            - { label: "Load history", detail: null, state: "done" }
            - { label: "Driver roster", detail: null, state: "done" }
        - name: "INTERVIEWS"
          status_label: "IN PROCESS"
          items:
            - { label: "Dispatch lead", detail: "Current routing heuristics", state: "done" }
            - { label: "Ops director", detail: "Margin priorities", state: "in_process" }
    next_steps:
      - "Deliver routing requirements document"
      - "Pilot on one terminal"

  - id: "warehouse_robotics"
    stage: "existing"
    section_label: "EXISTING PROJECT · STATUS"
    name_accent: "Warehouse Robotics"
    name_full: "Warehouse Robotics Orchestration."
    summary: "An orchestration layer that sequences pick-and-pack robots against real order flow."
    summary_hook: null
    today: null
    after: null
    where_we_are:
      label: "WHERE WE ARE"
      metrics:
        - { value: "Week 5/16", label: "Integration in progress · one week behind" }
        - { value: "2 sites", label: "Robotics online in pilot warehouses" }
      notes:
        - "Orchestrator running against staged WMS data"
        - "Fleet auth and telemetry complete"
    target:
      horizon: "TARGET — ALL SITES BY WEEK 16"
      metrics:
        - { value: "6 sites", label: "Full network rollout" }
        - { value: "24/7", label: "Autonomous pick sequencing" }
      notes:
        - "Human-in-the-loop exception handling"
        - "Per-site throughput dashboards"
    progress_tracker:
      label: "INTEGRATION — WEEKS 2–5"
      right_label: "WEEK 5 OF 16"
      groups:
        - name: "ACCESS"
          status_label: "2 OF 3"
          items:
            - { label: "Fleet telemetry", detail: null, state: "done" }
            - { label: "WMS live access", detail: null, state: "pending" }
            - { label: "Order stream", detail: null, state: "done" }
        - name: "BUILD"
          status_label: "IN PROCESS"
          items:
            - { label: "Sequencing solver", detail: null, state: "done" }
            - { label: "Exception routing", detail: null, state: "in_process" }
    next_steps:
      - "Week 6: unblock WMS access with IT"
      - "Week 7+: add two more sites"

  - id: "freight_audit"
    stage: "new"
    section_label: "NEW PROJECT · STATUS"
    name_accent: "Freight Audit"
    name_full: "AI Freight Audit & Recovery."
    summary: "An agent that audits carrier invoices against contracted rates and recovers overcharges."
    summary_hook: "Recover the leakage no one has time to chase."
    today:
      label: "TODAY"
      metrics:
        - { value: "~$0", label: "Overcharges recovered today (no audit)" }
      pain_bullets:
        - "Invoices approved without rate verification"
    after:
      horizon: "AFTER — TARGET IN 4 MONTHS"
      metrics:
        - { value: "2–4%", label: "Freight spend recovered per year" }
      capability_bullets:
        - "Line-item audit against contracted tariffs"
        - "Automated dispute filing"
    where_we_are: null
    target: null
    progress_tracker:
      label: "KICKOFF — WEEK 5"
      right_label: null
      groups:
        - name: "DATA REQUESTS"
          status_label: "1 OF 2"
          items:
            - { label: "Carrier contracts", detail: null, state: "done" }
            - { label: "Invoice exports", detail: null, state: "pending" }
    next_steps:
      - "Ingest 12 months of invoices"
      - "Baseline the recovery opportunity"
```

---

## 4 · Next Steps  → *Slide "NEXT STEPS" (parallel columns, one per workstream)*

```yaml
next_steps_slide:
  section_label: "NEXT STEPS"
  headline: "Three tracks, parallel next moves."
  summary: "Dispatch moves to pilot; robotics unblocks WMS access; freight audit ingests invoices."
  columns:
    - workstream_id: "dispatch_optimization"
      title: "DISPATCH OPTIMIZATION"
      steps:
        - { number: "01", title: "Routing requirements document", body: "Document routing heuristics, margin rules, and compliance constraints for build sign-off." }
        - { number: "02", title: "Single-terminal pilot", body: "Run the solver live at one terminal against real loads for two weeks." }
    - workstream_id: "warehouse_robotics"
      title: "WAREHOUSE ROBOTICS"
      steps:
        - { number: "01", title: "Unblock WMS access", body: "Work with IT to grant live WMS access so the orchestrator runs against production order flow." }
        - { number: "02", title: "Add two sites", body: "Extend robotics orchestration to the next two warehouses on the rollout plan." }
    - workstream_id: "freight_audit"
      title: "FREIGHT AUDIT"
      steps:
        - { number: "01", title: "Ingest invoices", body: "Pull 12 months of carrier invoices and normalize against contracted tariffs." }
        - { number: "02", title: "Baseline recovery", body: "Quantify the recoverable overcharge opportunity to size the build." }
```

---

## 5 · Provenance & Gaps  *(not rendered — for the agent's guardrails)*

```yaml
provenance:
  from_kg:
    - company.*
    - company.client_short
    - engagement.pe_firm
  from_plan_schedule:
    - engagement.project_week
    - tracking.columns
    - tracking.lanes
    - tracking.bar_categories
    - tracking.slip_or_buffer_markers
    - workstreams[].progress_tracker
    - workstreams[].where_we_are
  derived:
    - engagement.check_in_date_display
    - engagement.check_in_month_year
    - deck.total_slides
    - tracking.today_marker
  templated_defaults:
    - engagement.confidentiality
    - deck.slide_count_formula
  modeled_or_prose:
    - cover.status_subtitle
    - tracking.summary
    - workstreams[].summary
    - workstreams[].after
    - workstreams[].target
    - next_steps_slide.*
  gaps:
    - field: "workstreams[2].after.metrics[0].value"
      reason: "Recovery rate (2-4%) is a modeled target, not a measured actual; label as target."
    - field: "tracking.slip_or_buffer_markers[1]"
      reason: "Slip attribution (WMS access) is a judgment call; confirm with delivery lead."
warnings:
  - "Progress state and the TODAY marker are load-bearing. Do NOT synthesize them."
```
