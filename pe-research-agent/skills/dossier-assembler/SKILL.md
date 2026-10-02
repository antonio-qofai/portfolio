# dossier-assembler
*QofAI Research Agent — Skill v1*

---

## Purpose

Assembles the final dossier markdown file from the fully labeled section outputs produced by
firm-profiler, portfolio-discoverer, portco-profiler, confidence-scorer, and source-typer.

This skill does not produce content. It validates structure, merges section outputs into a
single document, applies final formatting, and writes the output file. Content errors in the
assembled sections must be corrected at their source skills before they reach this skill.

---

## Trigger Condition

Call this skill after:
1. firm-profiler has produced Sections 1, 2, and 3
2. portfolio-discoverer has produced the portfolio inventory
3. portco-profiler has produced individual company profiles
4. confidence-scorer has run on all sections
5. source-typer has run on all sections (after confidence-scorer)

Do not call this skill before all five prerequisites are complete. Calling with partial inputs
produces an incomplete dossier that audit-pass will reject.

---

## Input Contract

Required inputs:

| Field | Type | Description |
|---|---|---|
| `firm_name` | string | Legal or operating name of the PE firm |
| `research_date` | string | Date research was conducted, YYYY-MM-DD |
| `firm_overview_section` | path | Path to typed output of firm-profiler Section 1 |
| `investment_strategy_section` | path | Path to typed output of firm-profiler Section 2 |
| `leadership_section` | path | Path to typed output of firm-profiler Section 3 |
| `portfolio_section` | path | Path to assembled portfolio section (portco profiles merged) |
| `recent_activity_section` | path | Path to recent activity section |
| `run_log_path` | path | Path to the current run log for this execution |
| `output_path` | path | Target path for the assembled dossier (e.g., dossiers/v3-skillified.md) |

Optional inputs:

| Field | Type | Description |
|---|---|---|
| `audit_notes` | path | Path to audit notes file if audit-pass has already flagged advisory items |
| `prior_dossier_path` | path | If a prior run exists, its path (used for "Changes from prior run" note) |

---

## Output Contract

The skill writes a complete dossier markdown file to `output_path`.

### Required document structure

```
# [Firm Name] — Firm Dossier
Research date: YYYY-MM-DD
Version: [as specified]

---

## Firm Overview
[Section 1 content]

**Sources:**
[Per-section source list]

---

## Investment Strategy
[Section 2 content]

**Sources:**
[Per-section source list]

---

## Leadership
[Section 3 content]

**Sources:**
[Per-section source list]

---

## Portfolio
[Section 4 content]

**Sources:**
[Per-section source list]

---

## Recent Activity
[Section 5 content]

**Sources:**
[Per-section source list]

---

## Audit Notes        ← only present if audit-pass flagged advisory items
[Advisory flags, if any]

---

## Changes from Prior Run   ← only present if a prior dossier exists
[Material differences in key figures]
```

### Pre-assembly validation checks

Before writing the output file, the skill verifies:

1. All five required sections are present and non-empty.
2. Each section is within the target word count range from research-agent.md. A section more
   than 20% below the lower bound triggers an advisory flag, not a halt.
3. Every section ends with a "Sources" subheading containing at least one entry.
4. The header contains a research date in YYYY-MM-DD format.
5. No section header name differs from the five required names.

Checks 1, 4, and 5 failing: write to run log and halt. Do not produce a partial dossier.
Checks 2 and 3 triggering an advisory: write to run log, proceed, and include the flag in
the Audit Notes section.

### Source list format

Each section's source list:
- [Short title] — [URL or filing identifier] — [date accessed or filed]

Sources are listed per section even when the same document is cited in multiple sections.

### What the assembler does not do

Dossier-assembler does not edit content passed in from upstream skills. Conflicts flagged by
portfolio-discoverer or portco-profiler appear in the assembled dossier verbatim. If a section
needs correction, the orchestrator routes it back to the originating skill before assembly.

---

## Worked Examples

See example-01.md (validation checks and conflict preservation) and example-02.md (changes
from prior run note).

---

## Failure Modes to Avoid

1. Resolving conflicts that upstream skills flagged. If a conflict was surfaced, it survives
   into the assembled dossier unchanged.
2. Producing a partial dossier when a validation check fails. A dossier that looks complete but
   has a missing section is worse than no dossier.
3. Adding editorial commentary or content improvements during assembly. This skill organizes;
   it does not write.
4. Omitting the "Changes from prior run" note when a prior dossier exists.
5. Treating advisory flags as blockers. They go into Audit Notes; they do not halt assembly.
