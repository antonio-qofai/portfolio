# Status Deck Template — Structure Spec

Deck type: `status`
Source: a synthetic check-in for a fictional client (Northwind / Woodgrove Partners). The
structure below is the real one; every example value is invented.
Slide count: 4

> Active phase (PRD §5). This spec defines the status path's slide structure the same way the
> proposal template defines the proposal path's. Same "PDF is rendered output, confirm the
> editable slide set with the founder advisor" caveat applies (PRD open question 1); the workstream slide
> shape and progress-tracker groups are provisional at schema_version 0.1.

## How to read this file

One prompt section per slide below, in order. Each slide lists its **content roles** (fields
filled from the data source) and a **filled example** (a synthetic client's content, kept as
illustration). The agent swaps the example values for the selected client/project's data and
never carries them into a real client's deck.

Each content role is a token in curly braces followed by a machine-readable type annotation,
so the loader parses the type deterministically instead of guessing it from the prose — the
same convention as the proposal template. The annotation is exactly one of:

- `[type: string]` — a single scalar value.
- `[type: list]` — a list of strings.
- `[type: list_of_records; fields: a, b, c]` — a list of records, each carrying the named
  fields.

Any role may append an optional `; label: "DISPLAY HEADING"`. Only a line carrying a type
annotation is a content role; a token inside an example string is illustration.

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

The defining difference from the proposal template: a status deck is **time-aware** (dated
weeks, a TODAY marker, "Week N of M") and its slide count is **variable** — one status slide
per active workstream. The workstream slide below is marked `Repeat: workstreams`: the
assembler emits one copy per active workstream the data source returns, in packet order, so
the deck length flexes (two workstreams → 5 rendered slides). Nothing downstream assumes a
fixed count; the footer denominator reads `{total_slides}` from data.

Placeholders are `{like_this}`.

## Section keys (for the contract's `sections_requested`)

Each slide carries a `Section key:` line matching one `sections_requested` key in the request
contract (`status-data-request-CONTRACT.md` §2). When a section is outside `sections_requested`
and comes back `skipped: "not_requested"`, the assembler omits that slide:

| `sections_requested` key | Slide |
|---|---|
| `cover` | Slide 1 — Title / Cover |
| `tracking` | Slide 2 — Project Tracking |
| `workstreams` | Slide 3 — Workstream Status (one per active workstream) |
| `next_steps` | Slide 4 — Next Steps |

---

## Slide 1 — Title / Cover

Section key: `cover`

Content roles:
- `{deck_kicker}` `[type: string]` — top label, deck type + date. Example: "PROJECT CHECK-IN · APRIL 9 2026"
- `{prepared_for}` `[type: string]` — client + PE firm. Example: "PREPARED FOR NORTHWIND · WOODGROVE PARTNERS"
- `{deck_title_accent}` `[type: string]` — first title line, in the accent color. Example: "Implementation"
- `{deck_title_primary}` `[type: string]` — second title line, primary color. Example: "Project Check-in."
- `{status_subtitle}` `[type: string]` — what this update covers, naming the active workstreams.
  Example: "Status update: Order Intake Automation and Inventory Forecasting."

Time fields introduced here and reused throughout: `{check_in_date}`, `{project_week}` (e.g. "Week 3 of 12").

---

## Slide 2 — Project Tracking (dated Gantt with TODAY marker)

Section key: `tracking`

Content roles:
- `{tracking_section_label}` `[type: string]` — "PHASED ROLLOUT · WEEK N OF M". Example: "PHASED ROLLOUT · WEEK 4 OF 10"
- `{tracking_headline}` `[type: string]` — Example: "Project Tracking."
- `{tracking_summary}` `[type: string]` — one-to-two sentences on where each workstream stands,
  calling out on-track vs. slipped.
  Example: "Order Intake Automation on track to deliver its PRD in Week 5; Inventory Forecasting slips one week, waiting on warehouse data access."
- `{timeline_columns}` `[type: list_of_records; fields: id, date, label]` — DATED weeks (W1 May 4,
  W2 May 11, …), unlike the proposal's relative weeks. `id` is the column key bars refer to.
- `{today_marker_week}` `[type: string]` — the column `id` the current date lands on. Example: "W4"
- `{today_marker_label}` `[type: string]` — the marker text dropped on that column. Example: "TODAY"
- `{bar_categories}` `[type: list_of_records; fields: id, color, covers]` — the Gantt color legend:
  each bar names one `id`, and the renderer colors it from this table. Categories track
  phase/track, never progress.
  Example: scoping_discovery → green; build → blue; integration_golive → maroon; data_access → navy; buffer → pink.
- `{gantt_bars}` `[type: list_of_records; fields: lane, label, start_week, end_week, category, state]` —
  the timeline bars, flattened one record per bar. `lane` is the phase/workstream row it sits on
  (bars sharing a `lane` render on one row); `start_week`/`end_week` are column `id`s (inclusive);
  `category` selects the fill color from `bar_categories`; `state` (complete / in_progress /
  upcoming / buffer) is annotation only — it drives the dashed buffer fill and slip callouts,
  never the color.
  Example rows: Scoping · Order Intake; Build; Integration; Data & Access; Phase 1 · Shadow Mode; Phase 2 · Live on One Site; Phase 3 · All Sites.
- `{slip_or_buffer_markers}` `[type: list_of_records; fields: label, week, kind]` — explicit
  delay/buffer callouts. REFERENCE ONLY, not displayed: they tell the renderer which weeks carry a
  buffer or a slip, which shows on the deck as the bar's dashed `state-buffer` annotation and in
  `tracking_summary`. Same standing as `bar_categories.covers` — it feeds a rendering decision
  without itself appearing on the slide. The callout strip that used to print them under the Gantt
  was removed (2026-07-24): two bullets beside a timeline that already showed both read as
  unexplained decoration. Example: "Deployment delay buffer" (buffer, W14).

---

## Slide 3 — Workstream Status (one per active workstream)

Section key: `workstreams`
Repeat: workstreams

One status slide per active workstream, in packet order (two workstreams → 2 of these slides). The
`stage` selects the framing: a `new` workstream fills Frame A with TODAY and Frame B with AFTER;
an `existing` workstream fills Frame A with WHERE WE ARE and Frame B with TARGET. The framing is
selected from `stage` by the mapping half, not guessed from content — so these neutral Frame A /
Frame B roles carry whichever pair the stage dictates, and the `{frame_a_label}` / `{frame_b_label}`
values name it on the slide.

Content roles:
- `{section_label}` `[type: string]` — the workstream's stage label. Example: "NEW PROJECT · STATUS" / "EXISTING PROJECT · STATUS"
- `{workstream_name_full}` `[type: string]` — full name. Example: "AI Order Intake Automation."
- `{workstream_name_accent}` `[type: string]` — the span of the name to color as accent. Example: "Order Intake Automation"
- `{workstream_summary}` `[type: string]` — what it is + the stakes, bolded hook.
  Example: "An intake agent that reads emailed purchase orders, checks them against the price list, and keys them into the order system. Clear the morning backlog before the trucks are loaded."

Frame A (current state — TODAY for new, WHERE WE ARE for existing):
- `{frame_a_label}` `[type: string]` — Example: "TODAY" / "WHERE WE ARE"
- `{frame_a_metrics}` `[type: list]` — big metric callouts, each "VALUE · LABEL".
  Example: "~2 days · Average time from emailed order to order entry"
- `{frame_a_bullets}` `[type: list]` — supporting status/pain lines.
  Example: "Two coordinators re-key every order by hand from PDFs"

Frame B (target — AFTER for new, TARGET for existing):
- `{frame_b_label}` `[type: string]` — horizon label. Example: "AFTER — TARGET IN 4 MONTHS" / "TARGET — ALL SITES LIVE BY WEEK 10"
- `{frame_b_metrics}` `[type: list]` — target metric callouts, each "VALUE · LABEL".
  Example: "Same day · Orders entered the day they arrive"
- `{frame_b_bullets}` `[type: list]` — target capability/notes lines.
  Example: "Price-list checks before an order is accepted"

Progress block (status-specific — this is what makes it a status slide):
- `{progress_label}` `[type: string]` — the sprint/phase label. Example: "TWO-WEEK DISCOVERY SPRINT"
- `{progress_right_label}` `[type: string; optional]` — optional right-aligned label, omitted cleanly when absent. Example: "WEEK 4 OF 10"
- `{progress_items}` `[type: list_of_records; fields: group, status_label, label, state]` — the
  done-vs-pending checklist, flattened one record per item. `group` is the group heading
  (items sharing a `group` render together); `status_label` is the group's badge (RECEIVED /
  2 OF 3 / IN PROCESS / COMPLETE), the same for every item in a group; `label` is the item
  (its detail folded in with a middot); `state` (done / pending / in_process) drives the
  checkbox and is load-bearing — rendered verbatim, never synthesized.
  Example: DATA REQUESTS [RECEIVED] — Sample purchase orders (done); INTERVIEWS [2 OF 3] — Warehouse lead — needed? (pending).
- `{ws_next_steps}` `[type: list]` — short forward list. Example: "Deliver project requirements document"

---

## Slide 4 — Next Steps (parallel, per workstream)

Section key: `next_steps`

Content roles:
- `{next_steps_section_label}` `[type: string]` — Example: "NEXT STEPS"
- `{next_steps_headline}` `[type: string]` — Example: "Two projects, parallel next moves."
- `{next_steps_summary}` `[type: string]` — Example: "Order intake moves from discovery into specification; forecasting connects to live stock levels."
- `{next_steps_items}` `[type: list_of_records; fields: column_title, workstream_id, number, title, body]` —
  the numbered next actions, flattened one record per step. `column_title` is the column heading
  (steps sharing a `column_title` render as one column); `workstream_id` matches the column to its
  workstream's accent color; `number` is 01/02/03; `title` + `body` are the action.
  Example (Order Intake Automation): 01 Product requirements document; 02 Implementation plan; 03 Value analysis.

Note: one column per active workstream, so the column count tracks the workstream count — do not
assume two.

---

## Fields that recur on every slide

- `{client_full}` `[type: string]` — full client name. Example: "Northwind"
- `{client_short}` `[type: string; reviewer]` — short form for footers. Example: "Northwind". Studio input, for the reason `{deck_date}` is: no data source supplies it. `company.client_short` is UNSOURCEABLE in the packet's own slot table and is filled on no path, so a deck left to itself falls back to `{client_full}` and every footer carries the company's full registry name. Entered in the studio it wins; blank, it degrades to `{client_full}` rather than showing a missing marker, because a footer short-form is cosmetic and defaultable.
- `{pe_firm}` `[type: string; optional]` — sponsor. Example: "Woodgrove Partners". Optional for the same reason as in the proposal template: no rendered slot carries it, the platform never supplies it (`pe_firm` is `None` on every company in the registry), so it is cosmetic and un-slotted rather than load-bearing. Omitted cleanly when absent; the gap reaches the reviewer through §5 provenance.
- `{check_in_date}` `[type: string; reviewer; deck_wide]` — the as-of date, display form. Example: "APRIL 9 2026"
- `{project_week}` `[type: string]` — "Week N of M". Example: "Week 4 of 10" — appears on cover and tracking slide.
- `{confidentiality}` `[type: string]` — Example: "QOFAI CONFIDENTIAL"
- `{deck_type_label}` `[type: string]` — deck type in the page footer. Example: "PROJECT CHECK-IN"
- `{month_year}` `[type: string; reviewer; deck_wide]` — footer month/year. Example: "APRIL 2026"
- `{footer_engagement}` `[type: string]` — the engagement label in the footer middle. Example: "IMPLEMENTATION PROJECT CHECK-IN"
- `{total_slides}` `[type: string]` — the footer denominator, read from data, never hardcoded. Example: "5"
- Page footer format: "QOFAI + {client_short} · {footer_engagement} · {month_year} · NN / {total_slides}"

## Notes for the agent (status-specific)

- Time-aware: dated weeks, a TODAY marker on the current week, and "Week N of M" all come from
  the packet's `check_in_date`-derived fields, never the render date.
- Variable slide count: one status slide per active workstream (the `Repeat: workstreams`
  slide), so the deck length flexes. Do not hardcode 5 slides; the footer reads `{total_slides}`.
- Gantt color is data-driven: bars are colored by `category` from `{bar_categories}`, never by
  progress; `state` is annotation only.
- Framing is stage-selected: Frame A / Frame B carry TODAY/AFTER or WHERE WE ARE/TARGET per the
  workstream's `stage`, decided by the mapping half.
- Progress is reported, not pitched: each workstream slide carries a progress tracker showing
  done vs. pending against the plan, rendered verbatim.
- All example values are synthetic — illustration only, never emitted for a real client.
