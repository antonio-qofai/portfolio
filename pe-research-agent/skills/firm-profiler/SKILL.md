# firm-profiler
*QofAI Research Agent — Skill v1*

---

## Purpose

Produces Sections 1 (Firm Overview), 2 (Investment Strategy), and 3 (Leadership) of the dossier
from the available source set. Does not produce portfolio or activity content; those sections
belong to portfolio-discoverer, portco-profiler, and the activity subsection of dossier-assembler.

This skill is the first content-producing step in the pipeline. Its output feeds directly into
confidence-scorer and source-typer before reaching dossier-assembler.

---

## Trigger Condition

Call this skill when:
- The orchestrator has confirmed the source set meets the minimum viability threshold (at least 15
  distinct source documents per research-agent.md).
- No prior run of firm-profiler exists in the current run directory, OR the orchestrator has
  explicitly requested a fresh run.

Do not call this skill if the source set does not clear the minimum viability threshold. In that
case, the orchestrator routes to a sourcing gap report and halts.

---

## Input Contract

Required inputs:

| Field | Type | Description |
|---|---|---|
| `firm_name` | string | The legal or operating name of the PE firm to profile |
| `sources_dir` | path | Path to the sources/ directory containing all source documents for this run |
| `prompts_dir` | path | Path to the prompts/ directory (research-agent.md, confidence-rubric.md, source-rubric.md) |
| `research_date` | string | Date research was conducted, format YYYY-MM-DD |

Optional inputs:

| Field | Type | Description |
|---|---|---|
| `prior_dossier_path` | path | If a prior dossier exists for this firm, its path. Skill uses it to flag changes in key figures. |

The skill reads source files directly from `sources_dir`. It does not accept pre-summarized
content. Raw source documents are the authoritative inputs.

---

## Output Contract

The skill produces three markdown sections, written to `runs/[timestamp]/firm-profiler-output.md`.

### Section 1: Firm Overview (200-350 words)
Covers: legal name, former names and rebrand dates, founding year, headquarters and satellite
offices, headcount, regulatory AUM with required disclosure, fund count and capital raised.

Required disclosures (from research-agent.md):
- Regulatory AUM noted as distinct from committed/invested capital, with 90-day ADV lag
- Rebrand rationale attributed to firm's own account, not stated as fact
- Satellite office marked as potentially registered rather than operating if uncorroborated

### Section 2: Investment Strategy (150-250 words)
Covers: target sectors (stated and observable), deal size range, geographic focus, control vs.
minority preference, and any divergence between stated strategy and observable portfolio.

Required disclosures:
- Stated ranges wider than one order of magnitude flagged as imprecise
- Strategy framing from firm website labeled self-reported, not presented as independent finding

### Section 3: Leadership (200-300 words)
Covers: named partners with tenure and current roles, co-founders and current status, significant
departures or org history gaps.

Required disclosures:
- Every named individual includes source for current role
- Former named executives flagged if absent from current materials, with last known reference
- No comparative tenure claims without a cited published benchmark

### Quality gates enforced by this skill (not delegated to confidence-scorer)

These checks run before the skill writes its output:

1. No self-reported figure (ADV, firm website, firm press release) labeled high confidence.
   Maximum confidence for self-reported figures is medium.
2. No aggregator-only source (Crunchbase, PitchBook user content, Wikipedia) used for any
   medium or high confidence claim. Aggregator-only sources produce low confidence claims
   regardless of what they assert.
3. No editorial comparative ("unusually long," "notably high") without a cited published
   benchmark. Remove the comparative, not the underlying data point.
4. All conflicting sources surfaced inline with both values shown and neither silently discarded.
5. Absence of a previously named person from current materials flagged explicitly, not omitted.

Each output section ends with a "Sources" subheading listing all documents consulted for that
section, in the format: [short title] — [URL or filing identifier] — [date accessed or filed].

---

## Worked Examples

See example-01.md (AUM conflict, headcount conflict, rebrand rationale) and example-02.md
(Robin Partner gap, Sam Partner aggregator-only, average tenure comparative).

---

## Failure Modes to Avoid

1. Copying the firm's stated rationale for strategic moves (rebrands, hires, fund closes) as
   independent reporting. Always attribute narrative framing to the firm explicitly.
2. Treating ADV headcount and AUM as high confidence because the ADV is a regulatory filing.
   ADV financial figures are self-reported and carry medium confidence.
3. Bundling weak sources to inflate confidence. "ZoomInfo, Crunchbase, press releases" as a
   combined citation does not exceed the weakest individual source in the bundle.
4. Reporting satellite office addresses without noting the possibility of registered-only addresses.
5. Omitting former named executives because their current status is uncertain. Uncertainty is
   itself the finding. Report it.
