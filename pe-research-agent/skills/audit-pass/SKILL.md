# audit-pass
*QofAI Research Agent — Skill v2*

---

## Purpose

Self-critiques the assembled dossier against the known failure modes and refusal behavior rules
in research-agent.md. Produces a structured audit report and determines whether the dossier
can be finalized or must be routed back to an upstream skill for correction.

This skill is a blocking gate. No dossier is written to its final output path until audit-pass
completes without critical flags.

**Default posture: prefer advisory over critical for polish and non-material issues.** Only
use critical flags for findings that would materially mislead a reader or violate a refusal
behavior rule. Isolated labeling noise, wording drift, or supporting sentences that lack a
label when they do not change a material conclusion are advisory, not critical.

---

## Trigger Condition

Call this skill after dossier-assembler produces a draft at the specified output path.

This is the final step before the dossier is considered complete. Do not call this skill in
parallel with any other skill.

---

## Input Contract

Required inputs:

| Field | Type | Description |
|---|---|---|
| `dossier_path` | path | Path to the assembled draft dossier |
| `run_log_path` | path | Path to the run log for this execution |
| `prompts_dir` | path | Path to prompts/ directory (research-agent.md, confidence-rubric.md, source-rubric.md) |
| `research_date` | string | Date of research, YYYY-MM-DD |

---

## Output Contract

The skill writes an audit report to `runs/[timestamp]/audit-report.md`.

### Audit report structure

```
# Audit Report
Dossier: [firm_name] — [version]
Research date: [YYYY-MM-DD]
Audit date: [YYYY-MM-DD]

## Result
PASS / FAIL

## Critical Flags  ← present only if result is FAIL
[List of critical flags]

## Advisory Flags  ← present whether pass or fail
[List of advisory flags, or "None"]

## Routing Decisions  ← present only if result is FAIL
[Which skill each critical flag is routed back to]
```

### Critical flag definition

**There are exactly four blocking critical flags.** Everything else is advisory. When in
doubt, it is advisory. The bar for a critical flag is: a reader would be actively and
materially misled in a way they cannot self-correct from the surrounding text.

### The four blocking critical flags

**CF-A — Self-reported figure labeled high confidence.**
Any AUM figure, headcount, fund size, or portfolio metric drawn solely from ADV filings,
firm press releases, firm website, or portfolio company's own materials that carries a
`confidence: high` label. The figure may remain; only the label is wrong. This is the only
confidence-calibration issue severe enough to block finalization.

**CF-B — Deal pace or frequency claim without date verification math.**
Any sentence asserting a number of investments within a time window (e.g., "fifth platform
investment in 18 months") where the dossier does not show the underlying close-date
arithmetic. A reader cannot independently verify the claim without the math.

**CF-C — Conflicting sources silently resolved.**
Two sources give materially different values for the same fact (different AUM figures,
different deal dates, different ownership percentages) and only one value appears with no
acknowledgment of the discrepancy. The reader is actively deceived about source agreement.

**CF-D — Required section missing entirely.**
One of the expected sections (Firm Overview, Investment Strategy, Leadership, Portfolio,
Recent Activity) is absent from the assembled dossier. Structural incompleteness.

### Everything else is advisory

All other quality issues — including but not limited to the following — are **advisory**
and do not block finalization:

- Missing staleness flags on any figure (headcount, AUM, roles, fund status)
- Confidence band set one level too high or too low on a non-self-reported claim
- Unlabeled sentence, even a material one, when the surrounding prose conveys the same
  information or acknowledges uncertainty
- Comparative descriptors without a published benchmark
- Former executive not flagged in the Leadership section
- Aggregator-only source (Crunchbase, PitchBook) used for a medium-confidence claim
- Aggregate portfolio metrics from a promotional source
- Firm narrative not clearly attributed
- Wide strategy range without a multi-segment note
- Platform company founding date without a vehicle-vs-organic note
- Multi-source synthesis labeled above the weakest contributing source
- Source type mislabeled (public inference vs. public document)
- Missing reasoning chain on a public inference claim
- Formatting artifacts, duplicate source blocks, reviewer markers in prose
- Labeling inconsistencies across sections for the same fact
- Any claim that is advisory per the examples above but does not meet CF-A through CF-D

### Advisory flag definition

Advisory flags are quality concerns that should be appended to the Audit Notes section of
the final dossier for human review. They do not block finalization and do not trigger a
retry. Collect all advisory findings and report them in the audit report regardless of
whether the result is PASS or FAIL.

### Routing decisions for critical flags

When audit-pass returns FAIL, it produces a routing decision for each critical flag:

| Flag | Route to |
|---|---|
| CF-A (self-reported labeled high) | confidence-scorer |
| CF-B (deal pace without date math) | firm-profiler (Recent Activity section) |
| CF-C (conflicting sources silently resolved) | firm-profiler or portco-profiler |
| CF-D (missing section) | dossier-assembler |

The orchestrator retries once per critical flag. If the flag persists after one retry, the
orchestrator halts, preserves the flagged draft and audit report in the run log, and surfaces
both to a human reviewer.

---

## Worked Examples

See example-01.md (check-by-check audit run on Example Capital v3) and example-02.md
(qualitative bar: how audit-pass distinguishes v3 from v2).

---

## Failure Modes to Avoid

1. Inventing a fifth critical flag category beyond CF-A through CF-D. There are exactly four.
2. Treating a labeling inconsistency, missing staleness flag, or unlabeled supporting sentence
   as critical. These are always advisory.
3. Failing the dossier because a conflict is noted in prose but lacks a full inline label on
   one side of the conflict.
4. Routing all critical flags to dossier-assembler. Route to the skill that produced the content.
5. Flagging the same issue as both critical and advisory.
6. Running more than one retry per critical flag.
