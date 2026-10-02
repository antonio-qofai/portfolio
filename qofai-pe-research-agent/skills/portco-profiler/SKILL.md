# portco-profiler
*QofAI Research Agent — Skill v1*

---

## Purpose

Produces a one-paragraph profile of a single portfolio company from the available source set.
Called once per company in the portfolio inventory produced by portfolio-discoverer. Profiles
feed into Section 4 (Portfolio) of the dossier assembled by dossier-assembler.

This skill writes at the individual company level. It does not aggregate across companies, compute
totals, or draw conclusions about the firm's overall strategy. Those tasks belong to firm-profiler
and dossier-assembler.

---

## Trigger Condition

Call this skill once per company in the portfolio inventory. The orchestrator may run multiple
instances of this skill in parallel, one per company. Each instance is independent.

Call only after portfolio-discoverer has completed and the inventory is available as input.

Do not call this skill for add-on acquisitions that portfolio-discoverer has classified as
expansions of an existing holding rather than standalone platforms. Add-ons appear in the
profile of their parent company, not as separate profiler calls.

---

## Input Contract

Required inputs:

| Field | Type | Description |
|---|---|---|
| `company_name` | string | As it appears in the portfolio inventory |
| `firm_name` | string | Parent PE firm name |
| `research_date` | string | Date of research, YYYY-MM-DD |
| `source_files` | list of absolute paths | The exact source files to read for this company. Read ONLY these files. Do not scan sources/ or read any other file not listed here. If the company cannot be sourced from these files alone, state that in the output. |

Optional inputs:

| Field | Type | Description |
|---|---|---|
| `acquisition_date` | string | Date the PE firm acquired this company, if known |
| `deal_type` | string | Platform, add-on, recapitalization, etc. if classified by portfolio-discoverer |
| `inventory_conflicts` | string | Any conflicts flagged for this company by portfolio-discoverer |

The `inventory_conflicts` field is critical. If portfolio-discoverer flagged a conflict about
this company's status, description, or deal type, portco-profiler must reflect that conflict in
the output. It does not resolve the conflict; it surfaces it in the profile.

**Source constraint:** The orchestrator passes the specific source files relevant to this
company. Read only those files. Do not independently scan the sources/ directory — doing so
reads every firm-level and unrelated company source file, which is wasteful and may introduce
information from unrelated companies. If no relevant source files are passed, write the profile
from the inventory entry alone and label all claims low confidence.

---

## Output Contract

The skill produces a single paragraph (60-120 words) for each company, written to
`runs/[timestamp]/portco-profiles/[company_slug].md`.

### Paragraph structure

1. Business description: what the company does, its end market, and its service or product model.
2. Scale signals: any observable operational metrics (locations, headcount, geographies) if
   available from a sourceable document.
3. Acquisition note: deal type and date if known.
4. Conflict note: if portfolio-discoverer flagged a conflict, the paragraph ends with an explicit
   note on the unresolved conflict.

Every factual claim carries an inline confidence and source type label.

### Confidence rules

- Business description sourced to firm website: medium (self-reported)
- Business description independently corroborated by trade press or company website: medium
  (company website is also self-reported; independent trade press would upgrade to high only if
  the description is not marketing language)
- Operational metrics from company website: medium (company-reported, not audited)
- Operational metrics from independent reporting: medium (independent but may be based on
  company-provided figures)
- Deal date from press release: medium (self-reported timing; high only if corroborated by
  independent deal coverage)
- Deal date corroborated by PitchBook and press release: medium (PitchBook corroboration is
  meaningful but not equivalent to an audited filing)

Note: high confidence for a portfolio company description is rare. It requires an independently
reported, non-marketing source that describes the business. Most portfolio company descriptions
in PE dossiers are medium confidence because the most detailed sources are the company itself
or its PE sponsor.

### Prohibited claims

Do not include:
- Revenue, EBITDA, or margin estimates without explicitly routing the output through
  private-data-approximator. Estimates included without that routing are unsourced.
- Comparative claims ("market leader," "one of the largest," "fastest-growing") without a
  cited independent ranking or market study.
- Characterizations of business quality ("well-run," "high-quality," "exceptional") without
  a cited source. Quality judgments without citations are editorial.
- Platform "founding dates" that are actually vehicle formation dates, without the distinction
  noted.

---

## Worked Examples

See example-01.md (Example Imaging — v1 error and corrected output) and example-02.md
(Example Energy — correct output with deal date and platform note).

---

## Failure Modes to Avoid

1. Assigning high confidence to operational metrics from a company's own website. Company-owned
   websites are marketing assets. The figures may be accurate but they are unaudited. Medium
   is the correct ceiling.
2. Including revenue or EBITDA estimates without routing through private-data-approximator.
   Estimates without that routing appear in the output without methodological disclosure.
3. Stating a company was "founded" in a year that is actually the platform vehicle formation date.
   Always check whether the date follows a pattern of rapid multi-state acquisition activity,
   which is a signal the founding date is a vehicle date, not an organic founding.
4. Omitting or minimizing conflicts flagged by portfolio-discoverer. If the inventory flagged a
   conflict about this company, the profile must reflect it.
5. Adding comparative adjectives ("leading provider," "one of the largest") from the company's
   own website without labeling them as marketing claims from that source.
