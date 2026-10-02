# confidence-scorer
*QofAI Research Agent — Skill v2*

---

## Purpose

Applies the QofAI confidence rubric to every **materially load-bearing claim** in a draft
dossier section. Produces an annotated version with each material claim labeled high, medium,
or low, along with a concise rationale.

This skill is a reviewer, not a writer. It does not rewrite sections or change claim content.

### What counts as a materially load-bearing claim

Label only claims in these categories:

- Numeric figures (AUM, headcount, fund count, deal size, revenue estimates, fund size)
- Dates (founding year, fund vintage, deal close dates, filing dates)
- Current named roles or employment status
- Sector or strategy statements (stated or inferred)
- Portfolio company status, acquisition dates, or exit dates
- Deal pace or frequency claims
- Any claim that directly drives a reader judgment about firm quality, strategy, or credibility

Do NOT require separate labels for:

- Contrastive, explanatory, or transitional sentences that do not independently assert a
  material fact ("This is consistent with the firm's focus on…", "By contrast, …")
- Structural or connective prose that introduces or summarizes content
- Attribution sentences that merely name a source without making a factual assertion

A single label may cover a tightly coupled factual unit spanning one sentence pair if both
sentences together express one indivisible material claim (e.g., a date plus a status that
are meaningless when separated). Do not split these into two labels.

---

## Trigger Condition

Call this skill after each section-producing skill (firm-profiler, portco-profiler) completes
its output and before dossier-assembler assembles the final document.

This skill may be run combined with source-typer in a single pass (preferred for cost and
latency). When run combined, it applies both confidence scoring and source typing in one call
per section, writing fully labeled output directly to the typed/ directory.

Do not call this skill before a draft section exists.

---

## Input Contract

Required inputs:

| Field | Type | Description |
|---|---|---|
| `draft_section` | string | The full text of the draft section to be scored |
| `section_name` | string | Which section this is (Firm Overview, Investment Strategy, etc.) |
| `sources_available` | list of strings | List of source documents available for this run |
| `prompts_dir` | path | Path to prompts/ directory containing confidence-rubric.md |
| `research_date` | string | Date of research, YYYY-MM-DD (needed for staleness checks) |

Optional inputs:

| Field | Type | Description |
|---|---|---|
| `prior_labels` | string | If the draft section already contains confidence labels, the skill checks calibration rather than assigning from scratch |

---

## Output Contract

The skill produces an annotated draft written to `runs/[timestamp]/typed/[section_slug].md`.

### Annotation format

For each materially load-bearing claim, the skill adds or verifies the inline label:
`(confidence: [high/medium/low] | source: [type] — [specific document], [date])`

If the label is wrong, the skill silently replaces it with the correct label. If a material
claim has no label, the skill adds the correct label silently.

**Do not emit `[CORRECTED]`, `[ADDED]`, `[REMOVE COMPARATIVE]`, `[REASONING CHAIN MISSING]`,
or any similar reviewer marker into the draft section body.** These are internal review
artifacts. If correction notes are needed for debugging, write them to
`runs/[timestamp]/labeling-notes/[section_slug].md`, not into the section text. The section
body must be clean, readable prose with only standard inline labels.

### Scoring rules (applied in order)

These rules implement the confidence decision tree in confidence-rubric.md. Apply in order and
stop at the first match.

1. Claim has no source at all: mark it `[UNSOURCED — critical flag]`. Do not assign a band.
   This is a refusal behavior violation per research-agent.md.

2. Claim cites only a user-edited or aggregator source (Crunchbase, Wikipedia, PitchBook
   user-contributed fields): low. Note: "Aggregator-only source; low confidence."

3. Claim is self-reported by the subject firm or portfolio company (ADV financial figures,
   firm website, firm press releases, company website metrics): medium at most. Silently
   correct to medium if labeled high.

4. Claim contains a comparative descriptor ("notably high," "unusually long," "exceptionally
   fast") without a cited published benchmark: silently remove the comparative from the text
   and label the underlying data point at its correct confidence. Note the removal in the
   labeling-notes artifact, not in the body.

5. Claim is a public inference drawn from multiple documents: medium. If the reasoning chain
   is not shown in the adjacent prose, add a brief inline note of the basis within the label
   field: `(confidence: medium | source: public inference — [brief basis])`. Do not add
   block-level reviewer text to the body.

6. Claim is independently reported by a non-subject source, corroborated by a second
   independent source, and not stale: high.

7. Claim is independently reported but from a single source (no corroboration): medium.

### Staleness flags

For these claim types, check the source date against the research date and add a staleness
flag if the threshold is exceeded:

| Claim type | Threshold |
|---|---|
| ADV headcount or AUM figures | 90 days |
| Named leadership roles | 12 months |
| Active portfolio company status | 12 months |
| Fund described as "currently raising" | 6 months |

Staleness flag format: `(staleness: source dated [source date], threshold [X days/months],
may not reflect current state)`

### Confidence floor rules

| Category | Confidence floor |
|---|---|
| Any revenue or EBITDA estimate for a private company | Low |
| Any inference about a firm's motivation or intent | Low |
| Any claim about a named person's current status when the only source is more than 12 months old | Low |
| Aggregate portfolio metrics from a firm press release | Medium (floor and ceiling) |
| Any claim corroborated only by the firm's own materials | Medium (ceiling) |

---

## Worked Examples

See example-01.md (ADV self-reported figure correction) and example-02.md (comparative claim
removal).

---

## Failure Modes to Avoid

1. Scoring a comparative claim as "low confidence" instead of removing it. Unsubstantiated
   comparatives are category errors, not uncertain claims. Remove them; do not label them.
2. Treating corroboration by multiple self-reported sources as independent corroboration.
3. Conflating source type with confidence band.
4. Labeling every sentence in a paragraph when most are explanatory or transitional. Label
   the material claims; let connective prose stand unlabeled.
5. Emitting reviewer markers (`[CORRECTED]`, `[ADDED]`, etc.) into the section body.
   These must go to a separate labeling-notes artifact or be omitted.
6. Skipping staleness checks on material numeric or role claims.
