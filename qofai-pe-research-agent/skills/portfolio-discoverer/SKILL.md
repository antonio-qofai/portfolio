# portfolio-discoverer
*QofAI Research Agent — Skill v1*

---

## Purpose

Enumerates the complete list of portfolio companies for a PE firm from the available source set.
Produces a structured inventory that feeds into portco-profiler (one call per company) and
ultimately into the Portfolio section of the dossier.

This skill does not profile individual companies. Its job is to produce an accurate, conflict-free
inventory: company name, sector, status (active or exited), acquisition date (if known), and the
sources that support each entry. Conflicts between sources about company status or sector are
surfaced here so portco-profiler can resolve them with additional sourcing or flag them as
unresolved.

---

## Trigger Condition

Call this skill after firm-profiler completes. The orchestrator may run this skill in parallel
with firm-profiler if the source set is already confirmed, since these two skills do not depend
on each other's output.

Do not call this skill if the source set does not include at least one document that lists
portfolio companies (firm website portfolio page, press releases, or third-party coverage with
named holdings). If no portfolio source exists, halt and flag the section as unsourceable.

---

## Input Contract

Required inputs:

| Field | Type | Description |
|---|---|---|
| `firm_name` | string | Legal or operating name of the PE firm |
| `sources_dir` | path | Path to the sources/ directory |
| `research_date` | string | Date research was conducted, format YYYY-MM-DD |

The skill reads source documents directly. It does not accept a pre-built company list as input.
Building the list from raw sources is the skill's core task.

---

## Output Contract

The skill writes a structured inventory to `runs/[timestamp]/portfolio-inventory.md`.

### Inventory structure

Each entry in the inventory contains:

| Field | Required | Notes |
|---|---|---|
| `company_name` | yes | As stated in sources; note alternative names if sources disagree |
| `sector` | yes | As stated in primary source; note if sources disagree |
| `status` | yes | active or exited; if sources conflict, list both with sources |
| `acquisition_date` | if available | Date of Example Capital acquisition, not company founding date |
| `platform_type` | if determinable | New platform acquisition or add-on to an existing holding |
| `source_basis` | yes | One or more sources supporting this entry |
| `confidence` | yes | Applied per the rules below |
| `conflicts` | if any | Explicit statement of what each conflicting source says |

### Confidence rules for inventory entries

- Active status confirmed by firm website updated within 12 months: medium (self-reported,
  firm controls the website)
- Active status confirmed by a recent independent press mention in addition to firm website: medium
  (independent corroboration upgrades from low but does not reach high without audited filing)
- Exited status confirmed by firm website and PitchBook: medium (two sources, both have
  institutional incentive to be current)
- Exited status confirmed only by PitchBook with no press release or firm website confirmation:
  low (PitchBook data quality for exits varies; user-contributed data is possible)
- Company listed on firm website but no press release, news coverage, or third-party source
  exists: medium (self-reported only)
- Company found only in an aggregator (PitchBook, Crunchbase) with no primary source: low

### Conflict protocol

When two sources disagree on company status, sector description, or deal type:
1. List both source claims verbatim with source names and dates.
2. Do not adopt either as the definitive answer.
3. Flag the conflict explicitly in the inventory entry.
4. Assign the confidence of the weaker source to the inventory entry.
5. Pass the conflict to portco-profiler so it can attempt resolution or surface it in the
   dossier.

The skill does not attempt to resolve conflicts. It surfaces them.

### Add-on vs. platform distinction

Identify and flag add-on acquisitions: a deal where the acquired company becomes part of an
existing portfolio holding rather than a new standalone platform. Add-ons are not new portfolio
companies; they expand an existing one. Misclassifying an add-on as a new platform inflates
the portfolio count and misrepresents deal pace.

Signals that an acquisition is an add-on:
- Press release describes it as an "add-on," "bolt-on," or "tuck-in"
- Press release names an existing portfolio company as the acquirer
- PitchBook records the parent company as an existing Example Capital holding
- The deal involves a company acquired by an existing Example Capital portco

---

## Worked Examples

See example-01.md (Example Services status conflict and Example Packaging add-on) and
example-02.md (Example Care description discrepancy and Example Retail deal type).

---

## Failure Modes to Avoid

1. Counting add-on acquisitions as new platforms. An add-on to an existing holding does not add
   a company to the portfolio count; it adds an acquisition to an existing company's history.
2. Adopting the firm website as the sole source for active status without noting it is
   self-reported and may lag actual exits.
3. Silently resolving a name or sector conflict by choosing the most authoritative-sounding source.
   Both versions must appear in the output.
4. Treating PitchBook data as equivalent to a firm press release in terms of reliability. PitchBook
   combines proprietary research with user-contributed data; its reliability varies by claim type
   and should be assessed individually.
5. Treating all company "founding dates" as organic founding dates. A company described as
   "founded in 2019" that already operates across 12 states was almost certainly a platform vehicle
   built through acquisitions. Flag the distinction.
