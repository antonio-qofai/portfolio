# private-data-approximator
*QofAI Research Agent — Skill v1*

---

## Purpose

Produces revenue and EBITDA band estimates for private portfolio companies from observable public
signals. Called when a dossier section requires financial scale context for a specific company
and no public financial data exists.

Every output from this skill carries low confidence and private inference source type by
definition. Private companies do not file public financials. Any financial estimate for a
private company is extrapolation from observable proxies, regardless of how rigorous the method.
This skill makes the method visible; it does not make the output reliable.

---

## Trigger Condition

Call this skill when:
- portco-profiler has produced a company profile and needs a financial scale estimate
- The company is private (no publicly filed financials available)
- At least two of the following observable signals are present in the source set:
  - Headcount figure (from any source, even company-reported)
  - Number of locations, centers, or service territories
  - Named end markets or customer segments with known industry economics
  - A comparable public company in the same sub-sector
  - A deal size, valuation, or transaction multiple disclosed in a press release or deal card

Do not call this skill if fewer than two observable signals are present. If insufficient signals
exist, report the section as "revenue and EBITDA data not estimable from public sources" and
move on. A missing estimate is better than a low-information estimate that readers may treat as
credible.

---

## Input Contract

Required inputs:

| Field | Type | Description |
|---|---|---|
| `company_name` | string | Company being estimated |
| `sector` | string | Sub-sector classification (affects comp selection) |
| `observable_signals` | list of strings | Each signal with its source document and confidence level |
| `research_date` | string | Date of research, YYYY-MM-DD |
| `sources_dir` | path | Path to sources/ directory for verifying signals |

Optional inputs:

| Field | Type | Description |
|---|---|---|
| `comparable_public_cos` | list of strings | Named public comparables if known; skill verifies before using |
| `deal_comps` | list of strings | Named comparable PE deals if available in source set |

If `comparable_public_cos` is provided, the skill verifies each named comparable is in the same
sub-sector before applying their multiples. Do not apply public company multiples across sub-sector
boundaries.

---

## Output Contract

The skill writes a structured estimate to `runs/[timestamp]/portco-profiles/[company_slug]-financials.md`.

### Estimate structure

1. Observable signals: list each signal with its source and confidence.
2. Comparable basis: which public companies or deals were used as the benchmark, and why they are
   applicable (same sub-sector, similar scale signals, similar geography).
3. Revenue range: a range, not a point estimate, stated as $X to $Y.
4. EBITDA margin range: a range in percentage points, not an absolute dollar figure unless
   the revenue range is tight enough to make an absolute range meaningful.
5. Key assumptions: each assumption that drives the estimate, stated explicitly.
6. Confidence and source label: always low / private inference. No exceptions.
7. What would change the estimate: one or two signals that, if available, would narrow the range.

### Mandatory confidence and source statement

Every estimate must include this statement verbatim (adapted for company name):

"Revenue and EBITDA figures for [company_name] are not publicly available. The estimates above
are based on [list signals]. They carry low confidence and should not be treated as financial
data. They are appropriate for approximate scale assessment only and should not be used in
LP communications, deal analysis, or BD valuation work without independent verification.
(confidence: low | source: private inference — estimate from observable public signals; no
financial data publicly available)"

---

## Worked Examples

See example-01.md (observable signals and comparable basis for Example Energy) and
example-02.md (estimate, key assumptions, and mandatory confidence statement).

---

## Failure Modes to Avoid

1. Using this skill when fewer than two observable signals are present. The output will be too
   speculative to be useful and will look like financial analysis it is not.
2. Using public company multiples from a different sub-sector. Propane distribution multiples do
   not apply to diagnostic imaging. Always verify sub-sector match before applying a comp.
3. Presenting a range as a point estimate. "Revenue of approximately $200 million" is less honest
   than "$100M to $300M"; the former implies precision the method cannot support.
4. Omitting the mandatory confidence statement. Every output from this skill, without exception,
   must carry the low / private inference label and the warning about LP and BD use.
5. Labeling this output as anything other than private inference. Observable signals are public;
   applying them to estimate a specific private company's financials is not.
