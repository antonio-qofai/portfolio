# Status Deck Visual Review Rubric

A human runs this checklist against a rendered status deck
(`decks/<client>/claude code/output-N.html`) before it goes to anyone. It makes
"deliverable quality" objective by tying each visual check to a PRD §5 success
criterion (S1–S13). The automated harness (`scripts/eval_status_decks.py`) covers
the structural checks — coverage, slide count vs `total_slides`, cross-client
leakage, and the reviewer-marker fidelity check — but layout, framing, and prose
are what a person still has to look at. This rubric is that pass.

How to use it. Open the rendered HTML (or a full-height screenshot). Walk each
section top to bottom. Every line is pass/fail against the packet the deck was
built from; when a line fails, that is a real finding to fix in the packet, the
mapping, or the render scaffold — not something to wave through. A deck ships
only when every applicable line passes.

Scope note. The slide count is variable (`cover + tracking + N workstreams +
next_steps`), so the number of workstream slides differs per client. Check the
workstream section once per workstream slide.

---

## 1. Cover slide

- [ ] Two-line title renders: an accent line (`deck_title_accent`) above a
      primary line (`deck_title_primary`), styled as two distinct lines, not one
      run-on string.
- [ ] Subtitle (`status_subtitle`) names the active workstreams being reported.
- [ ] Kicker / "prepared for" line reads the packet's `cover` fields, and the
      check-in date shown is the packet's `check_in_date`, not today's date (S4).
- [ ] Slide fills the 16:9 frame; no pooled whitespace, no clipped title.

## 2. Tracking slide — dated Gantt (S4, S11)

- [ ] Week columns are dated from the packet (`tracking.columns` labels, e.g.
      "May 18 … Jul 20"), not relative "1–2 / 3–4" buckets.
- [ ] The **TODAY** marker sits on the correct week — the column named by
      `tracking.today_marker.on_week` — and reads from the packet, never the
      render date (S4).
- [ ] "Week N of M" (`engagement.project_week`) matches the packet, and the
      section label shows the same week (e.g. "WEEK 5 OF 16").
- [ ] Bars are colored by phase **category** (`tracking.bar_categories`), not by
      progress (S11). Concretely:
  - [ ] Two *completed* bars of **different** categories render in **different**
        colors (color is the category, not the "done" state).
  - [ ] Two bars of the **same** category render the **same** color regardless of
        `state` (complete / in_progress / upcoming / buffer).
- [ ] `state` shows only as annotation: dashed buffer fill for buffers, slip
      callouts for slips — never as the bar's fill color (S11).
- [ ] Slip / buffer callouts (`tracking.slip_or_buffer_markers`) are visually
      distinct from ordinary bars, and any gap-flagged one carries
      `(unconfirmed, see gaps)` (S13).
- [ ] The whole Gantt fits inside the frame: no bar, label, or week column
      clipped or spilling past the slide edge.

## 3. Workstream slides — one per active workstream (S9, S12, S13)

Run these for **each** workstream slide.

- [ ] Framing matches the workstream's `stage`, selected by the pipeline, not
      guessed from content (S12):
  - [ ] `new` → **TODAY** (current-state metrics + pain) and **AFTER** (target
        horizon).
  - [ ] `existing` → **WHERE WE ARE** (current position + progress detail) and
        **TARGET**.
- [ ] The unused framing block is absent, not shown empty: a `new` workstream has
      no WHERE WE ARE/TARGET, an `existing` one has no TODAY/AFTER, and neither
      absence is flagged as `[MISSING]` (S9).
- [ ] The progress tracker renders its groups (`progress_tracker.groups`) with
      each group's status badge (e.g. "RECEIVED", "2 OF 3", "IN PROCESS").
- [ ] Item states are visually distinct and correct: `done` vs `pending` vs
      `in_process` render differently (e.g. checked vs empty box), and each state
      matches the packet verbatim — nothing synthesized (S13).
- [ ] Any field listed under the packet's §5 gaps shows `(unconfirmed, see gaps)`
      beside its value, not a bare value (S13).
- [ ] Per-workstream next steps (if present) read the packet's `next_steps`.
- [ ] Slide fills the frame; the dense two-column layout does not overflow.

## 4. Next steps slide (S2)

- [ ] One column per active workstream (`next_steps_slide.columns`), in packet
      order — the column count matches the number of workstream slides.
- [ ] Steps are numbered (`01`, `02`, …) with title + body from the packet.
- [ ] Each column's accent / title matches its workstream (correctly colored and
      labeled, no cross-column mismatch).
- [ ] Slide fills the frame; columns are balanced, no pooled whitespace.

## 5. Global checks — every slide (S7, S8, S10)

- [ ] Slide count equals `cover + tracking + N workstreams + next_steps`, where N
      is the packet's active-workstream count (S7).
- [ ] Every footer reads `NN / {total_slides}` with the **real** total from the
      packet (e.g. `03 / 6`), never a hardcoded `05` (S7).
- [ ] Zero cross-client leakage: no other client's name, PE firm, workstream
      names, or figures appear anywhere in the deck (S8). (The harness checks this
      against the configured signatures; confirm by eye too.)
- [ ] Confidentiality and engagement footer fields are consistent across all
      slides.
- [ ] Both deliverables — the rendered deck and the saved design prompt — carry
      the "draft, requires human review before anything ships" framing; nothing
      reads as sendable or final (S10).
- [ ] No slide overflows its 16:9 frame and no slide pools whitespace; the deck
      reads as one consistent visual family top to bottom.

---

## Findings log (optional)

Record failures so a re-render can be checked against them.

| Slide | Criterion | Finding | Fix location (packet / mapping / scaffold) |
| ----- | --------- | ------- | ------------------------------------------ |
|       |           |         |                                            |
