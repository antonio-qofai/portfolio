---
# ============================================================================
# AGENT OS → PROPOSAL BUILDER · RESPONSE DATA PACKET (EXAMPLE / SCHEMA v0.1)
# ----------------------------------------------------------------------------
# This is a REPRESENTATIVE example of the markdown Agent OS returns when a
# proposal-building agent requests the data needed to populate a Project
# Planning proposal (modeled on the FBK / Woodgrove deck, 6 slides).
#
# The consuming agent maps each field below to a placeholder in its template.
# All values here are illustrative for a SYNTHETIC client ("Fabrikam
# Marine"), a second synthetic proposal packet beside the FBK one. Field NAMES and STRUCTURE are the contract; values are examples.
# ============================================================================
schema_version: "0.1"
packet_type: "project_planning_proposal"
generated_at: "2026-07-06T14:30:00Z"
generated_by: "agent-os/pe-firm-kg + walker-simulation-pipeline"
kg_source:
  company_id: "REPLACED-WITH-REAL-UUID"
  neo4j_reachable: true
  last_kg_refresh: "2026-06-30"
confidence: "high"          # high | medium | low — driven by data completeness
data_completeness: 0.92     # 0–1; share of required fields populated from KG vs. defaulted
---

# Proposal Data Packet — Fabrikam Marine

> **How to read this file.** Sections map 1:1 to proposal slides. Field keys are
> stable; a template placeholder like `{{opportunity.today_metrics[0].value}}`
> resolves against the structured blocks below. Prose under each block is
> ready-to-use slide copy (already in QofAI voice); numeric blocks are for the
> agent to render into charts/callouts. If a field is `null`, it was not
> derivable from the KG — see **§8 Provenance & Gaps** before using a default.

---

## 0 · Request Context (what triggered this packet)

The proposal agent sent Agent OS the following (echoed back for traceability):

```yaml
request:
  intent: "generate_project_planning_proposal"
  company: "Fabrikam Marine"          # name OR company_id
  pe_firm: "Woodgrove Partners"              # optional; resolved from KG if omitted
  proposal_date: "2026-07-06"
  template: "project_planning_v2"              # which deck template to fill
  sections_requested:                          # omit for "all"
    - cover
    - opportunity
    - platform
    - rollout
    - commercial_terms
    - next_steps
```

Agent OS resolved `Fabrikam Marine` → one KG match (company_id above),
confirmed the Neo4j instance was reachable, and assembled the packet below.

---

## 1 · Company Profile & Baseline  → *Cover slide + Opportunity "TODAY"*

```yaml
company:
  name: "Fabrikam Marine"
  legal_name: "Fabrikam Marine, LLC"
  client_short: "FBK"     # short brand form for deck footers; optional-but-expected. If null, consumer falls back to name.
  pe_firm: "Woodgrove Partners"
  sector: "Commercial site preparation & earthwork"
  hq: "Boise, ID"
  employees: 310
  fleet_size: 84                # heavy equipment units
  active_projects: 52
baseline:
  revenue_ttm_usd: 38_400_000
  adjusted_ebitda_usd: 6_900_000
  adjusted_ebitda_pct: 17.9
  ebitda_volatility_note: "−41.2% – +58.4% month-over-month swings"
  reporting_lag: "~26 days after project close"
  baseline_locked_date: null    # set at contract signing; null pre-signature
```

**Cover copy**
- **Eyebrow:** `PROJECT PLANNING · JULY 6 2026`
- **Prepared for:** `FABRIKAM MARINE · WOODGROVE PARTNERS`
- **Title:** `Operational Intelligence Project Planning.`
- **Subhead:** `Standing up the data foundation, the mobile field app, and real-time dashboards — one integrated build, delivered in phases.`

---

## 2 · The Opportunity  → *Slide "THE OPPORTUNITY"*

One entry per opportunity, and the deck repeats this slide once for each
(item 15, 2026-09-13). Each entry carries its own headline and one-liner,
its TODAY metrics and pain points, its AFTER target metrics and
capabilities, and its build strip. A deck with one opportunity emits one
entry: the shape does not branch on the count.

```yaml
opportunities:
  - headline: "Mobile Field Capture & Project Dashboards."
    one_liner: "A connected tablet app that replaces paper field tickets, feeding live project, equipment, and fuel margin — so cost overruns get caught while the job is still open."
    today_metrics:
      - value: "−41.2 – +58.4%"
        label: "Month-over-month EBITDA % swings, with little real-time visibility"
      - value: "~26 days"
        label: "Reporting lag after project close prevents real-time intervention"
    today_pain_points:
      - "Foremen handwrite daily field tickets on paper; keyed into Excel next morning (12–24h lag)"
      - "Fuel and equipment-hour entry is optional; ~52 active jobs live in disconnected workbooks"
    target_metrics:
      - value: "+1.6–2.7pp"
        label: "EBITDA margin uplift — $1.5M–$2.6M / yr direct"
      - value: "<24 hrs"
        label: "Data latency — real-time field sync from job sites"
    target_capabilities:
      - "Connected tablet app with enforced daily fuel + equipment-hour capture and automatic job allocation"
      - "Live per-job production and financial performance"
    build_summary:
      duration_weeks: 16
      phases:
        - id: "phase_1"
          label: "PHASE 1 · WKS 1–8 · FOUNDATION & PILOT"
          summary: "Cloud data warehouse + accounting connector, the Operations dashboard, and a single-crew field-capture pilot run in parallel with paper."
        - id: "phase_2"
          label: "PHASE 2 · WKS 8–16 · FLEET ROLLOUT"
          summary: "Fleet-wide capture, the fuel/equipment-allocation engine, CEO / Finance dashboards with variance alerts, combined-entity reporting, and asset-tracking integration."
```

## 3 · Opportunities & Priority Initiatives (KG core)  → *drives §2 metrics, §4 rollout, §7 scenarios*

> This is the analytical heart of the packet: the ranked opportunities the KG /
> walker-simulation surfaced for this company, each with a quantified EBITDA
> delta, sequencing, dependencies, and the phase it lands in. The proposal
> agent uses these to build the "after" metrics, the timeline workstreams, and
> the commercial scenario math — it should NOT invent initiatives.

```yaml
opportunities:
  - id: "OPP-01"
    title: "Enforced real-time field capture"
    priority_rank: 1
    kg_evidence: "12 workflow nodes show paper→Excel transcription with 12–24h lag and optional fuel entry"
    current_state: "Paper field tickets, manual re-keying, ~26-day close"
    target_state: "Tablet capture with enforced fuel/equipment hours, <24h sync"
    ebitda_delta_pp: 0.9
    ebitda_delta_usd_yr: 850_000
    effort_weeks: 8
    phase: "phase_1"
    depends_on: []
  - id: "OPP-02"
    title: "Automated fuel & equipment-hour allocation to jobs"
    priority_rank: 2
    kg_evidence: "Fuel-card GL entries not joined to job codes; ~$4.1M annual fuel spend unallocated"
    current_state: "Fuel booked at company level, allocated by estimate at month-end"
    target_state: "Per-job allocation engine driven by captured hours"
    ebitda_delta_pp: 0.7
    ebitda_delta_usd_yr: 650_000
    effort_weeks: 6
    phase: "phase_2"
    depends_on: ["OPP-01"]
  - id: "OPP-03"
    title: "Live variance alerting (EBITDA / fuel / production)"
    priority_rank: 3
    kg_evidence: "No intervention loop exists between job start and close"
    current_state: "Overruns discovered ~26 days post-close"
    target_state: "Threshold alerts to CEO/Finance during the open job"
    ebitda_delta_pp: 0.6
    ebitda_delta_usd_yr: 560_000
    effort_weeks: 4
    phase: "phase_2"
    depends_on: ["OPP-01", "OPP-02"]
  - id: "OPP-04"
    title: "Reporting-readiness / combined-entity reporting"
    priority_rank: 4
    kg_evidence: "Manual month-end consolidation across 2 entities; investor reporting is bespoke"
    current_state: "Spreadsheet consolidation, ~26-day lag"
    target_state: "Automated combined-entity + job post-mortem package"
    ebitda_delta_pp: 0.2       # margin effect small; drives EV multiple, see §7
    ebitda_delta_usd_yr: 190_000
    effort_weeks: 4
    phase: "phase_2"
    depends_on: ["OPP-01"]

initiative_rollup:
  total_ebitda_delta_pp_conservative: 1.6
  total_ebitda_delta_pp_base: 2.2
  total_ebitda_delta_pp_optimistic: 2.7
  total_direct_uplift_usd_yr_base: 2_050_000
```

---

## 4 · The Platform  → *Slide "THE PLATFORM"*

Headline: **One Operational Intelligence Platform, two connected layers.**
Subhead: *A mobile capture layer that replaces clipboards, and an analytics layer that turns captured data into live metrics — layered on top of the accounting and asset systems, not replacing them.*

```yaml
platform_layers:
  - number: "01"
    kicker: "INPUTS · MOBILE FIELD CAPTURE"
    title: "Capture crew activity one time, in real-time."
    body: "Connected tablet app: a guided, button-driven workflow with dependent dropdowns, the full earthwork + equipment-delay taxonomy, crew, and enforced fuel — buffering locally through connectivity dropouts."
  - number: "02"
    kicker: "DATA FOUNDATION"
    title: "Integrate into one data store."
    body: "A cloud warehouse joining field data, fuel, and accounting financials. Accounting + GL/fuel-card connectors, asset-tracking and pipeline ingestion, scheduled batch where APIs are constrained."
  - number: "03"
    kicker: "OUTPUTS · DASHBOARDS & ALERTS"
    title: "Live project performance, role by role."
    body: "Operations, CEO, and Finance dashboards with drill-down from fleet → job → crew → day."
```

---

## 5 · Phased Rollout  → *Slide "PHASED ROLLOUT · 16 WEEKS"*

Headline: **8 weeks to a validated pilot, 16 weeks to fleet-wide live.**
Subhead: *Two phases over sixteen weeks: build the platform and validate it on a single crew, then roll it out to the whole fleet, with checkpoints at weeks 2, 8 and 16.*

```yaml
timeline:
  total_weeks: 16
  week_buckets: ["1–2", "3–4", "5–6", "7–8", "9–10", "11–12", "13–14", "15–16"]
  phases:
    - id: "phase_1"
      label: "PHASE 1 · BUILD & PILOT — WKS 1–8"
      # start_week / end_week are 1-indexed against total_weeks and must fall
      # within this phase's span (weeks 1–8). Modeled by walker-simulation
      # (see §8 provenance: derived, not raw KG facts).
      workstreams:
        - name: "Scoping & setup"                            detail: "Lock scope"                       start_week: 1  end_week: 2
        - name: "Data foundation — warehouse + connectors"   detail: "Accounting + GL connectors"       start_week: 2  end_week: 6
        - name: "Field application development"               detail: "Daily-ticket forms + real-time sync"  start_week: 3  end_week: 7
        - name: "Operations dashboard"                       detail: "Fleet production & downtime"       start_week: 5  end_week: 8
        - name: "Single-crew field-capture pilot"            detail: "Parallel paper + digital"         start_week: 6  end_week: 8
    - id: "phase_2"
      label: "PHASE 2 · FLEET ROLLOUT — WKS 8–16"
      # start_week / end_week fall within this phase's span (weeks 8–16).
      workstreams:
        - name: "Fleet-wide field-capture rollout"           detail: "Crew-by-crew; retire paper"       start_week: 8   end_week: 14
        - name: "Fuel & equipment-allocation engine"         detail: "Auto-allocate by job"             start_week: 9   end_week: 13
        - name: "CEO / Finance dashboards + alerts"          detail: "EBITDA, variance & fuel alerts"   start_week: 11  end_week: 15
        - name: "Reporting package + asset tracking"         detail: "Combined-entity + Job Post-Mortem"  start_week: 13  end_week: 16
  milestones:
    - id: "M0"  week: 2   label: "SCOPE LOCKED"
    - id: "M1"  week: 8   label: "PILOT VALIDATED"
    - id: "M2"  week: 16  label: "FLEET LIVE"
```

---

## 6 · Commercial Terms  → *Slide "COMMERCIAL TERMS · A PERFORMANCE PARTNERSHIP"*

Headline: **A partnership built on performance.**
Subhead: *We invest. We deliver. You retain the value.* QofAI bears all up-front build, licensing, and hosting cost; the client only pays from realized EBITDA gains — and keeps the rest.

```yaml
commercial:
  qofai_investment_usd: 275_000
  qofai_investment_note: "Engineering, deployment, and all implementation risk — fully absorbed before the client pays a dollar."
  client_upfront_usd: 0
  client_upfront_note: "No capital outlay. No per-seat BI licensing. Tablets (~18, rugged cases) procured by client — not in contract."
  comp_schedule:                 # QofAI's declining % of EBITDA gains
    - { year: "YEAR 1",  pct: 25 }
    - { year: "YEAR 2",  pct: 15 }
    - { year: "YEAR 3",  pct: 5  }
    - { year: "YEAR 4+", pct: 0  }
  client_retention_note: "Client retains 80–84%, 100% thereafter."
  cap_note: "Capped at 2.5× initial investment or 3 years, whichever first; ~$19K/yr support after that."
  no_improvement_clause: "If margins don't improve above your locked baseline, QofAI earns nothing."

  # Scenario mapping: EBITDA margin-point gain → 3-year value.
  # Assumptions the deck footnotes: exit multiple + reporting-readiness uplift.
  ev_assumptions:
    exit_ebitda_multiple: 6.0
    reporting_readiness_multiple_uplift: 0.25
    adjusted_ebitda_base_usd: 6_900_000
    reporting_readiness_ev_usd: 1_725_000    # 0.25 × 6.9M
  scenarios:
    - name: "CONSERVATIVE"
      margin_gain_pp: 1.6
      direct_uplift_usd_yr: 1_500_000
      qofai_comp_usd: 640_000
      client_retained_ebitda_usd: 3_600_000
      enterprise_value_at_exit_usd: 10_700_000
    - name: "BASE CASE"
      margin_gain_pp: 2.2
      direct_uplift_usd_yr: 2_050_000
      qofai_comp_usd: 685_000
      client_retained_ebitda_usd: 4_900_000
      enterprise_value_at_exit_usd: 14_000_000
    - name: "OPTIMISTIC"
      margin_gain_pp: 2.7
      direct_uplift_usd_yr: 2_600_000
      qofai_comp_usd: 685_000            # cap reached
      qofai_comp_note: "cap"
      client_retained_ebitda_usd: 6_200_000
      enterprise_value_at_exit_usd: 17_300_000
  how_payment_works:
    - "Measured monthly. Actual job & fleet EBITDA margin is compared to the client's locked pre-launch baseline; improvement is expressed in margin points."
    - "Converted to EBITDA. The margin-point gain is applied to financial performance at the contribution basis locked at signing for the term."
    - "Share paid that month. QofAI receives the year's share (20% / 10% / 5%) of that figure — paid monthly until the 2.5× cap or the 3-year term."
  ev_footnote: "Enterprise value at exit assumes a 6× EBITDA multiple on the annual uplift, plus a 0.25× reporting-readiness multiple improvement on ~$6.9M adjusted EBITDA. Directional, for decision support."
```

---

## 7 · Next Steps  → *Slide "NEXT STEPS"*

Headline: **Four moves to launch the build.** *Each action has a named owner and a target week.*

```yaml
next_steps:
  - number: "01"
    week: "WK 0"
    owner: "CLIENT LEADERSHIP"
    title: "Approve Project Plan"
    body: "Leadership reviews and approves the platform scope, the two-phase plan, and the 16-week timeline before the build begins."
  - number: "02"
    week: "WK 0"
    owner: "QOFAI + CLIENT LEADERSHIP"
    title: "Sign Performance-Based Contract"
    body: "Execute the performance-based agreement: QofAI bears all upfront cost and is paid from realized EBITDA gains, capped at 2.5× cost over a three-year term."
  - number: "03"
    week: "WK 1–2"
    owner: "QOFAI ENGINEERING"
    title: "Kick Off QofAI FDEs"
    body: "QofAI's forward-deployed engineers begin the build — standing up the cloud warehouse, the accounting connector, and the Operations dashboard."
  - number: "04"
    week: "WK 1–2"
    owner: "OPERATIONS"
    title: "Select the pilot crew & foreman"
    body: "Choose a tech-receptive foreman for the single-crew pilot; lock Phase 1 scope to the daily ticket, fuel, and the financial dashboard."
```

---

## 8 · Provenance & Gaps  *(not rendered — for the agent's guardrails)*

```yaml
provenance:
  from_kg:                         # fields pulled directly from Neo4j / walker corpus
    - company.*
    - company.client_short     # short brand form; from KG when present, else consumer defaults to company.name
    - baseline.revenue_ttm_usd
    - baseline.adjusted_ebitda_usd
    - opportunities            # all initiatives + EBITDA deltas from walker-simulation
    - initiative_rollup
  derived:                         # computed by walker-simulation-pipeline
    - target_metrics
    - commercial.scenarios
    - commercial.ev_assumptions
    - timeline.phases[].workstreams[].start_week   # per-workstream schedule MODELED by walker-simulation, not measured KG facts
    - timeline.phases[].workstreams[].end_week
  templated_defaults:              # NOT from this company's data — deck standards
    - commercial.comp_schedule     # standard 20/10/5/0 unless deal-specific
    - platform_layers             # standard platform narrative
    - next_steps                  # standard 4-step launch
  gaps:                            # null / low-confidence fields the agent must flag
    - field: "baseline.baseline_locked_date"
      reason: "Set at contract signing; unavailable pre-signature"
    - field: "commercial.qofai_investment_usd"
      reason: "Estimate from effort_weeks; confirm with delivery lead before sending"
warnings:
  - "EBITDA scenario figures are directional (walker-simulation), not audited. Deck footnote is mandatory."
  - "If neo4j_reachable=false or data_completeness < 0.7, do NOT auto-generate — return to human review."
```
