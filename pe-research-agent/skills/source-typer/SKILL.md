# source-typer
*QofAI Research Agent — Skill v2*

---

## Purpose

Assigns a source type (public document, public inference, or private inference) to every
**materially load-bearing claim** in a draft dossier section. Works alongside
confidence-scorer; the two skills apply different, independent dimensions of the QofAI
claim labeling system.

Source type and confidence band are independent labels. A claim can be a public document at
low confidence (e.g., a Crunchbase entry). A claim can be a public inference at medium
confidence (e.g., a strategy conclusion drawn from multiple disclosed deals). Private
inference is always low confidence, but that is the only pair with a fixed relationship.

This skill is a reviewer, not a writer. It annotates existing material claims. It does not
rewrite content and does not emit reviewer notes into the section body.

### What counts as a materially load-bearing claim (same scope as confidence-scorer)

Label only:

- Numeric figures (AUM, headcount, fund count, deal size, revenue estimates)
- Dates (founding year, fund vintage, deal close dates)
- Current named roles or employment status
- Sector or strategy statements (stated or inferred)
- Portfolio company status, acquisition dates, or exit dates
- Deal pace or frequency claims
- Any claim that directly drives a reader judgment about the firm

Do NOT label every contrastive, explanatory, or transitional sentence. A single label may
cover a tightly coupled factual unit spanning one sentence pair if both sentences together
express one indivisible material claim.

---

## Trigger Condition

Call this skill after confidence-scorer has completed its pass on a given section. Running
source-typer after confidence-scorer ensures the two passes do not conflict, and source-typer
can reference confidence-scorer's annotations as context.

**Preferred: combined pass.** When cost and latency matter, confidence-scorer and source-typer
should be combined into a single model call per section. The system prompt includes both skill
contracts; the output is fully labeled (both confidence and source type) in one pass. The
output goes directly to `typed/[section_slug].md`.

Do not call this skill before a draft section exists.

---

## Input Contract

Required inputs:

| Field | Type | Description |
|---|---|---|
| `scored_draft` | string | The output of confidence-scorer for this section (contains confidence labels) |
| `section_name` | string | Which section this is |
| `sources_available` | list of strings | All source documents available for this run |
| `prompts_dir` | path | Path to prompts/ directory containing source-rubric.md |
| `research_date` | string | Date of research, YYYY-MM-DD |

---

## Output Contract

The skill produces a fully labeled draft at `runs/[timestamp]/typed/[section_slug].md` with
source type tags added or verified for each materially load-bearing claim.

### Combined label format (output of both confidence-scorer and source-typer)

`(confidence: [high/medium/low] | source: [public document / public inference / private inference]
— [specific document or basis], [date accessed or filed])`

The source-typer output is the final combined label, not a separate annotation layer. After
source-typer runs, the label field contains both dimensions.

**Do not emit `[SOURCE-TYPER NOTE]`, `[ACCURACY NOTE]`, or any similar reviewer marker into the
section body.** If reviewer notes are needed for debugging, write them to a separate
`runs/[timestamp]/labeling-notes/[section_slug].md` artifact. The section body must be clean,
readable prose with only standard inline labels.

### Typing rules (applied in order, stop at first match)

1. Is the claim directly stated in a named, retrievable document in the public record?
   Type: public document.
   Subcheck: is the named document self-reported (published by the subject firm, portfolio
   company, or their agent)? If yes, note this in the label: "public document — firm press
   release, self-reported." Self-reporting does not change the source type; it affects
   confidence.

2. Is the claim a conclusion drawn by connecting evidence across two or more public documents,
   where no single document states the conclusion directly?
   Type: public inference.
   Requirement: the reasoning chain must be shown in the label or in the adjacent prose.
   If the chain cannot be reconstructed from public documents, downgrade to private inference.
   Do not add block-level reviewer text; include a brief basis in the label field.

3. Does the claim require assumptions, estimates, or industry knowledge that no public
   document provides?
   Type: private inference.
   Subcheck: is the basis especially thin? If yes, add "— speculation" to the label.

4. Does the claim contain both a public inference component and a private inference component
   in the same sentence?
   Type: private inference (the weaker type governs the whole claim).

### Special cases

**Absence of a named person from current sources:**
Type: public document (absence finding).
Label: "public document — last reference [source], [date]; absent from all current firm
materials reviewed [research date]."

**Crunchbase or user-edited aggregator claims:**
Type: public document (weak). Low reliability is a confidence issue, not a source type issue.

**Firm-stated strategy vs. analyst-observed strategy:**
If the firm stated it directly: public document.
If the analyst inferred it from portfolio deal announcements: public inference.

**Motivation and intent:**
Inferring why a firm did something from public signals: private inference.
Stating that a firm said it did something for a given reason: public document.

---

## Worked Examples

See example-01.md (stated sector focus and add-on strategy correction) and example-02.md
(motivation inference and revenue range).

---

## Failure Modes to Avoid

1. Downgrading a weak public document to private inference. Crunchbase and user-edited sources
   are public documents with low confidence, not private inferences.
2. Labeling analyst-inferred strategy as public inference without showing the reasoning chain.
3. Labeling the firm's stated rationale as a public inference. If the firm said it, it is a
   public document.
4. Emitting `[SOURCE-TYPER NOTE]`, `[ACCURACY NOTE]`, or similar reviewer artifacts into the
   section body. These must go to a separate notes file or be omitted.
5. Treating the motivation portion of a claim the same as the observation portion.
