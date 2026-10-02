# Proposal Deck Template — Structure Spec

Deck type: `proposal`
Source: a synthetic proposal for a fictional client (Fabrikam Marine / Woodgrove Partners). The
structure below is the real one; every example value is invented.
Slide count: 6

`Slide count` is the number of slide BLOCKS in this spec, not the number of
slides a deck renders. Slides 2, 3, 4 and 6 are marked `Repeat: opportunities`
and the assembler emits one copy of each per opportunity the data source
returns, so a two-opportunity deck renders ten slides from these six blocks. Nothing
downstream assumes a fixed count: the page-footer denominator reads
`{total_slides}` from data, the same way the status template's does.

## How to read this file

This is the structure the generated Claude Design prompt must follow: one prompt section
per slide below, in this order. Each slide lists its **content roles** (the fields the agent
must fill from the data source) and a **filled example** (a synthetic client's content, kept so the
agent can see what a completed slide looks like). The agent replaces the example values with
the selected client/project's data; it never carries the example's values into a real client's deck.

Each content role is a token in curly braces followed by a machine-readable type annotation,
so the loader parses the type deterministically instead of guessing it from the prose. The
annotation is exactly one of:

- `[type: string]` — a single scalar value.
- `[type: list]` — a list of strings.
- `[type: list_of_records; fields: a, b, c]` — a list of records, each record carrying the
  named fields.

Any role may append an optional `; label: "DISPLAY HEADING"` to its annotation (for example
`[type: string; label: "HOW PAYMENT WORKS"]`). When present, the assembler emits the field
into the prompt under that visible heading, so Claude Design titles the block instead of
rendering the content unlabeled. Use it for any block whose meaning is not self-evident from
its content alone.

Any role, and any single field of a `list_of_records` role, may also declare WHO is expected
to fill it. Three flags, appended to the annotation after any `fields:` and `label:`, in any
order and any combination:

- `reviewer` — we ask a human for this value and never asked a source for it. A
  `[MISSING: ...]` marker on it is a reviewer input still outstanding, not a hole in the data.
- `reviewer: sensitive` — the same, and the value must not come from a source at all: QofAI's
  own per-deal arithmetic. A marker on it is the deck honestly stating that a figure belongs
  here and that no human has entered it yet.
- `deck_wide` — one value covers every occurrence of this role across the deck (a date
  repeated in each slide footer), so the studio may offer it as ONE input that fans out to
  each occurrence. The default is per-instance: a role or field whose occurrences each carry
  their own value must NOT be collapsed, because all but one of them would become unfillable.
  A record field is per-instance by construction and so never takes this flag.

Written down here, beside the field, for the same reason `optional` is: whether a slide
expects a human or a source to fill a field is a question about the slide. It is also what
lets the studio split "the source had nothing" from "we are waiting on you", instead of
listing both under one heading where the second kind drowns the first.

At a field of a `list_of_records` role the flags go in parentheses after the field name and
are separated by SPACES — `week (reviewer)`, `qofai_comp (reviewer: sensitive)` — because a
`;` would cut the enclosing `fields:` list short and a `,` would split the entry.

Only a line that carries a type annotation is a content role. A token that appears inside an
example string (for instance a footer line that repeats a recurring field) is illustration,
not a role declaration, and carries no annotation. The example under each role is a synthetic
value and is never emitted as content.

## Section keys (for the contract's `sections_requested`)

Each slide corresponds 1:1 to one `sections_requested` key in the request contract
(`proposal-data-request-CONTRACT.md` §2). When a section is outside `sections_requested` and
comes back with the `skipped: "not_requested"` marker, this is the slide the assembler omits:

| `sections_requested` key | Slide |
|---|---|
| `cover` | Slide 1 — Title / Cover |
| `opportunity` | Slide 2 — The Opportunity |
| `platform` | Slide 3 — The Platform / Solution |
| `rollout` | Slide 4 — Phased Rollout / Project Plan |
| `commercial_terms` | Slide 5 — Commercial Terms |
| `next_steps` | Slide 6 — Next Steps |

---

## Slide 1 — Title / Cover

Content roles:
- `{deck_kicker}` `[type: string]` — top label, deck type + date. Example: "PROJECT PLANNING · MARCH 4 2026"
- `{prepared_for}` `[type: string]` — client + PE firm. Example: "PREPARED FOR FABRIKAM MARINE · WOODGROVE PARTNERS"
- `{project_title}` `[type: string]` — the project name, two-line treatment with an accent-colored first line.
  Example: "Operational Intelligence" (accent) / "Project Planning." (primary)
- `{subtitle}` `[type: string]` — one-sentence description of what's being stood up.
  Example: "Standing up a shared job record, a crew check-in app, and live job costing — one build, delivered in phases."
- `{footer_left}` `[type: string]` — confidentiality mark. Example: "QOFAI CONFIDENTIAL"
- `{footer_right}` `[type: string]` — platform/engagement label. Example: "OPERATIONAL INTELLIGENCE PLATFORM"

---

## Slide 2 — The Opportunity (Today vs. After)

Section key: `opportunity`
Repeat: opportunities

Section label: `{section_label}` — "THE OPPORTUNITY" on a deck carrying one
opportunity, and "OPPORTUNITY 1 · <NAME>" on a deck carrying several. A role
rather than a fixed string since 2026-09-15: two opportunity slides reading the
same label, above the same company-baseline TODAY figures, are told apart only
by reading the headline, and a reader flicking through attributes one slide's
numbers to the other. The position says where in the deck, the name says which,
and each answers a question the other does not.

ONE COPY PER OPPORTUNITY, in the order the data source returns them. Each
opportunity has its own today-versus-after story, and this is the slide carrying
it, so the assembler emits one of these per entry and numbers them sequentially
with the rest of the deck. The Platform, the Phased Rollout and the Next Steps
repeat the same way (2026-09-22): each opportunity is its own build, with its
own components, its own schedule and its own first moves, and two opportunities
sharing one timeline is how a 24-week build came to be printed as a 44-week
one. The cover and the commercial terms belong to the engagement and do not
repeat.

There is no ceiling on the count. A deck with one opportunity renders exactly
what it always did.

Content roles:
- `{section_label}` `[type: string]` — the slide's own section label, top left, small caps.
  Example (one opportunity): "THE OPPORTUNITY"
  Example (several): "OPPORTUNITY 1 · CREW CHECK-IN APP"
  Joined with the middot this deck already uses for a two-part label, the same way slide 4
  reads "PHASED ROLLOUT · 16 WEEKS" and slide 5 "COMMERCIAL TERMS · A PERFORMANCE
  PARTNERSHIP". Composed from the opportunity's own title rather than sourced separately.
- `{opportunity_headline}` `[type: string]` — the product/solution name as a headline.
  Example: "Crew Check-In & Live Job Costing."
- `{opportunity_summary}` `[type: string]` — one-to-two sentence framing, with a bolded closing hook.
  Example: "One job record: a phone app crews use at the start and end of every shift, feeding live labor and materials cost per job. Catch an overrun while the job is still open."

Both panels carry one or TWO metrics, and the second is `optional` in the role sense:
a source that names one metric renders one, with no `[MISSING: ...]` marker, because
nothing is absent. On the paper-derived path the AFTER block is filled from a single
opportunity figure, so a required `{after_metric_2}` marked every such deck as missing
something it was never going to have (measured 2026-08-19, across the corpus). A packet
that does name two still renders two.

TODAY block (current-state pain):
- `{today_metric_1}` `[type: string]` + label. Example: "±18%" / "Swing in job margin between estimate and close, found only after close"
- `{today_metric_2}` `[type: string; optional]` + label. Example: "~3 weeks" / "Time from job close to a costed job report"
- `{today_pain_bullets}` `[type: list]` — 1–2 supporting lines.
  Example: "Crew leads text hours to the office; a coordinator re-keys them each evening" · "Materials are logged per truck, not per job, in separate spreadsheets"

AFTER block (target state, with time horizon):
- `{after_horizon}` `[type: string]` — label. Example: "AFTER — TARGET IN ~12 WEEKS"
- `{after_metric_1}` `[type: string]` + label. Example: "+1.0–1.8pp" / "Job margin uplift — $300K–$550K / yr direct"
- `{after_metric_2}` `[type: string; optional]` + label. Example: "Same day" / "Job cost visible the day the work happens"
- `{after_capability_bullets}` `[type: list]` — 1–2 supporting lines.
  Example: "Shift check-in that will not close without hours and materials" · "Live cost against estimate for every open job"

Build-overview strip (optional, phase summary):
- `{build_summary}` `[type: string]` — e.g. "THE 12-WEEK BUILD · SCOPING COMPLETE" with a one-line description per phase.
  Example: Phase 1 · Wks 1–6 · Foundation & Pilot; Phase 2 · Wks 6–12 · Company Rollout.

Footer: page mark "QOFAI + {client_short} · {deck_type_label} · {month year} · 02 / {total_slides}"

---

## Slide 3 — The Platform / Solution (numbered components)

Repeat: opportunities

Section label: "THE PLATFORM" (or solution-area equivalent)

Content roles:
- `{platform_section_label}` `[type: string; optional]` — which opportunity this
  platform is, on a deck carrying several: "THE PLATFORM · OPPORTUNITY 2 · <NAME>",
  the same position-and-name idiom as slide 2's label. Absent on a deck with one
  opportunity, where there is nothing to tell apart, and the slide's label is then
  as above.
- `{platform_headline}` `[type: string]` — headline naming the solution and its shape.
  Example: "One job record, two connected layers."
- `{platform_summary}` `[type: string]` — one-to-two sentence description.
  Example: "A capture layer the crews carry, and a costing layer that turns what they enter into live job margin — on top of the existing accounting system, not replacing it."
- `{components}` `[type: list_of_records; fields: number, kicker (optional), title, description]` — a numbered list of 3 components (01/02/03), each with a short title and a
  2–3 sentence description.
  `kicker` is the component's own category word where the source states one. It is
  `optional` because a card carrying a number, a title and a description reads as
  complete without it: a source that names no category word is not missing
  anything, so no marker fires and the card renders with no kick line. A live deck
  on 2026-08-19 rendered five `[MISSING: kicker]` chips on this slide for exactly
  that reason, beside five complete cards.
  Example:
  - 01 · INPUTS · CREW CHECK-IN — "Enter hours and materials once, on site." (description)
  - 02 · DATA FOUNDATION — "One record per job" (description)
  - 03 · OUTPUTS · JOB COSTING & ALERTS — "Live margin, job by job." (description)

---

## Slide 4 — Phased Rollout / Project Plan (timeline)

Repeat: opportunities

Section label: phase/timeline label. Example: "PHASED ROLLOUT · 12 WEEKS" (or
"PHASED ROLLOUT · 12 MONTHS" — the unit is the plan's own, never converted).

Content roles:
- `{plan_section_label}` `[type: string; optional]` — which opportunity this
  schedule is, on a deck carrying several: "TIMELINE · OPPORTUNITY 2 · <NAME>".
  Absent on a deck with one opportunity, whose kicker is TIMELINE alone.
- `{plan_headline}` `[type: string]` — the plan in a sentence.
  Example: "6 weeks to a validated pilot, 12 weeks to company-wide live."
- `{plan_summary}` `[type: string]` — one-to-two sentences on what each phase does.
  Example: "Phase 1 stands up the job record and proves check-in with two crews. Phase 2 rolls check-in out to every crew and turns the data into live, alerted job costing."
- `{timeline_columns}` `[type: list]` — the time axis. For a proposal deck this is relative (Wk 1–2, 3–4, …),
  not calendar dates, and it is denominated in the unit the plan itself states — weeks or months,
  whichever the source says, never converted from one to the other. The first entry names that unit
  and the rest are bare ranges under it, which is how the axis unit reaches the deck without a role
  of its own. Example (weeks): Wk 1–2 / 3–4 / 5–6 / 7–8 / 9–10 / 11–12.
  Example (months): MONTHS 0–3 / 3–6 / 6–12
- `{timeline_rows}` `[type: list_of_records; fields: phase, workstream, workstream_detail, weeks]` — the plan's bars, each spanning
  a range on the axis above. `workstream` is the
  row label to the left of the bars; `workstream_detail` is the short detail that rides inside
  the bar. `weeks` is that
  row's own start–end span (e.g. "3–7"), in the axis unit, not the phase span, so the rendered
  Gantt bar geometry reflects each row's real schedule. `weeks` is required for every row on this
  slide; a row missing it is flagged for the reviewer, never back-filled with the phase span.
  Two row shapes, and the source decides which: where the source decomposes a phase into
  workstreams, one row per workstream grouped by phase. Where it states phases only — which is
  what a research paper's implementation-timeline chart carries — the phase IS the row: `phase`
  and `weeks` are present and the two workstream fields are simply absent, with no marker, because
  the source never claimed a workstream existed.
  Example (Phase 1): Scoping & setup, Job record, Check-in app, Costing view, Two-crew pilot.
  (Phase 2): Company-wide rollout, Materials allocation, Owner and finance alerts, Monthly
  job report.
- `{milestones}` `[type: list_of_records; fields: id, week (reviewer), label]` — key markers. `week` is the marker's
  position on the axis in the plan's own unit ("Wk 8", "MONTH 3"). Example: "M0 Wk 2 · Scope Locked"
  · "M1 Wk 6 · Pilot Validated" · "M2 Wk 12 · Company Live". Where the source states a boundary but
  names no outcome for it, the label is a generic ordinal ("MILESTONE 2") and nothing is invented
  in its place.

Note: a relative timeline is the proposal convention (pre-project), whether the plan is
denominated in weeks or in months. Status decks use dated weeks with a TODAY marker — that belongs
to the status template, not here.

---

## Slide 5 — Commercial Terms (an adaptive deal sheet)

Section label: "COMMERCIAL TERMS"

Rebuilt 2026-09-23. The fixed layout that stood here (investment, up-front and schedule
boxes in one prescribed arrangement) was retired on 2026-09-22 as too specific to one
kind of deal. This slide is now the least
fixed part of the deck. It is a deal sheet of blocks, every block optional, and a block
renders only when something states a value for it. No block names a deal type, so a flat fee,
milestones, a retainer, a performance share or a condition nobody has listed are all rows.

ONE SLIDE FOR THE ENGAGEMENT. It does not repeat per opportunity. On a deck carrying several,
each row names its opportunity, and no figure is ever summed across them.

Content roles:
- `{terms_headline}` `[type: string]` — neutral. Example: "Commercial terms."
- `{terms_summary}` `[type: string]` — one neutral line. Example: "What the build costs, what it returns, and the terms of the engagement."
- `{investment_rows}` `[type: list_of_records; fields: opportunity (optional), label, value; optional]` — INVESTMENT. The PRD's cost TOTAL, one row per cost column, labelled by the column ("Year 1", "Ongoing") and valued with the PRD's own text. Never the line items.
  Example: "Year 1" / "$90K–$100K" · "Ongoing" / "$20K–$30K/yr est."
- `{return_rows}` `[type: list_of_records; fields: opportunity (optional), scenario, annual_ebitda (optional), margin (optional), payback (optional); optional]` — RETURN. One row per case the source names, in its order, any count, under its own name. A column no row carries is not drawn.
  Example: "Conservative" / "$249,000/yr" / "+1.0pp" / "4–5 months" · "Ambitious" / "$550,000/yr" / "+1.8pp" / "2–3 months"
- `{terms_rows}` `[type: list_of_records; fields: label, value; reviewer: sensitive]` — TERMS. The deal itself: how QofAI is paid, when, on what conditions. Named rows with any label, as many as the deal has. No source states these for a PRD deck, so with none entered this renders as ONE open box marked as awaiting input, never a blank and never an invented term.
  Example: "Fee" / "Fixed, per module, quoted after scoping" · "Paid at" / "Each production-gated phase exit"
- `{value_mapping}` `[type: list_of_records; fields: opportunity (optional), scenario, ebitda_gain, qofai_comp (reviewer: sensitive), client_retained_ebitda (reviewer: sensitive), enterprise_value (reviewer: sensitive); optional]` — the stacked value chart, present ONLY when a case carries QofAI comp, client retained EBITDA and enterprise value. Absent, it is not drawn at all: no chart, no heading, no marker.
- `{terms_footnote}` `[type: string; reviewer: sensitive]` — the basis line. Example: "Indicative QofAI estimate, to be confirmed in scoping. Directional, for decision support."

---

## Slide 6 — Next Steps (numbered actions with owners)

Repeat: opportunities

Section label: "NEXT STEPS"

Content roles:
- `{next_steps_section_label}` `[type: string; optional]` — which opportunity
  these steps are for, on a deck carrying several: "NEXT STEPS · OPPORTUNITY 2 ·
  <NAME>". Absent on a deck with one opportunity, and the label is then as above.
- `{next_steps_headline}` `[type: string]` — the ask in a phrase. Example: "Four moves to launch the build."
- `{next_steps_summary}` `[type: string]` — one line. Example: "Each action has a named owner and a target week."
- `{action_items}` `[type: list_of_records; fields: number, week (reviewer), owner (reviewer), title, description]` — a numbered list (01–04) of actions, each with: title, description, an
  owner tag, and a target-week tag.
  Example:
  - 01 · WK 0 · FBK LEADERSHIP — "Approve the plan" (description)
  - 02 · WK 0 · QOFAI + FBK LEADERSHIP — "Sign the engagement" (description)
  - 03 · WK 1–2 · QOFAI ENGINEERING — "Start the build" (description)
  - 04 · WK 1–2 · OPERATIONS — "Pick the two pilot crews" (description)

---

## Fields that recur on every slide (fill once, propagate)

- `{client_full}` `[type: string]` — full client name. Example: "Fabrikam Marine"
- `{client_short}` `[type: string; reviewer]` — short form for footers. Example: "FBK". Studio input, for the reason `{deck_date}` is: no data source supplies it. `company.client_short` is UNSOURCEABLE in the packet's own slot table and is filled on no path, so a deck left to itself falls back to `{client_full}` and every footer carries the company's full registry name. Entered in the studio it wins; blank, it degrades to `{client_full}` rather than showing a missing marker, because a footer short-form is cosmetic and defaultable.
- `{pe_firm}` `[type: string; optional]` — sponsor. Example: "Woodgrove Partners". Optional, and deliberately so: no rendered slot on any slide carries it (it is absent from the page-footer format above and from the cover's own fields), and the platform never supplies it — `pe_firm` is `None` on every company in the registry, because the request contract treats it as a caller-supplied disambiguator rather than deck content. So it is cosmetic and un-slotted, which is exactly the case `MISSING` markers are NOT for: those are reserved for load-bearing, un-defaultable fields. Omitted cleanly when absent, like `{client_short}` above degrades. The gap still reaches the reviewer through §5 provenance, which is where an unsourced field belongs.
- `{project_name}` `[type: string]` — Example: "Job Costing Platform"
- `{deck_date}` `[type: string; reviewer; deck_wide]` — proposal uses the meeting/delivery date, not per-slide dates. Example: "MARCH 4 2026"
- `{confidentiality}` `[type: string]` — Example: "QOFAI CONFIDENTIAL"
- `{deck_type_label}` `[type: string]` — deck type in the page footer. Example: "PROPOSAL"
- `{total_slides}` `[type: string; deck_wide]` — how many slides this deck actually has, which is
  the footer denominator. Read from data rather than written here, because slides 2, 3, 4 and 6
  repeat once per opportunity: six blocks render six slides for one opportunity and ten for two. `deck_wide`
  because one value covers every footer on the deck.
- Page footer format: "QOFAI + {client_short} · {deck_type_label} · {month year} · NN / {total_slides}"

## Notes for the agent

- Structure is fixed; content is swapped. Match these slide blocks in this order, emitting
  slide 2 once per opportunity and everything else once.
- All example values above are synthetic. They are illustration only — never emit them
  in a deck for a real client.
- Proposal decks use a relative axis (Wk 1–2 …, or Months 0–3 …), not calendar dates, on the
  timeline slide, in whatever unit the plan itself states.
- Source of every filled value is the data source for the selected client/project, not this file.
