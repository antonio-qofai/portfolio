# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo does

Produces confidence-banded PE firm dossiers using a multi-skill orchestration pipeline. The target firm is Example Capital (formerly Example Partners). Each run reads local markdown source documents from `sources/`, calls Claude models via the CLI, and writes a versioned dossier to `dossiers/`.

## Running the pipeline

```bash
# Full run — produces v4 dossier
python3 scripts/run_research.py "Example Capital" --skip-financials --portco-workers 10 --max-portcos 20

# Dry run (prints plan, no Claude calls)
python3 scripts/run_research.py "Example Capital" --dry-run

# Resume from assembler (Phase 9) or audit (Phase 10) after a partial run
python3 scripts/run_research.py "Example Capital" --resume-run runs/<timestamp> --from-phase 9

# Syntax check the runner
python3 -m py_compile scripts/run_research.py
```

Key flags: `--skip-financials` skips the private-data-approximator (saves ~10 Claude calls). `--portco-workers N` controls parallelism for portco subagents (default 4; raise to 10 for speed). `--max-portcos N` caps the portfolio company count.

## Architecture

### Pipeline phases (scripts/run_research.py)

| Phase | What runs | Parallelism |
|---|---|---|
| 1 | firm-profiler + portfolio-discoverer | 2 concurrent |
| 3 | portco-profiler per company | `--portco-workers` concurrent |
| 4 | private-data-approximator per company | `--financial-workers` concurrent; skip with `--skip-financials` |
| 5 | Recent Activity section generation | sequential |
| 6 | Portfolio section assembly (in-process) | — |
| 7 | Claim-labeler: combined confidence scoring + source typing | sequential per section |
| 9 | dossier-assembler | sequential |
| 10 | audit-pass | sequential |

Phase numbers are intentionally non-contiguous (8 was collapsed into 7 when the two labeling passes were merged). Resume supports phases 9 and 10 only. Outputs accumulate under `runs/<timestamp>/` with sections in `typed/` and the assembled draft in `assembled/`.

### Skill system

Each skill lives in `skills/<name>/SKILL.md` and defines an input contract, output contract, scoring rules, and failure modes. The runner loads skill contracts at runtime via `load_skill()` and injects them as system-prompt context alongside the rubric files from `prompts/`.

The portco-profiler is the only skill that runs as a named Claude Code subagent (`.claude/agents/portco-profiler.md`). All other skills run as inline system-prompt injections via `run_claude()`.

### Claim labeling

Every materially load-bearing claim in the output carries an inline label:
`(confidence: high/medium/low | source: public document/public inference/private inference — [specific basis], [date])`

Material claims are: numeric figures, dates, current named roles, AUM/headcount/fund counts, sector/strategy statements, portfolio company status or deal dates, deal pace/frequency. Transitional and explanatory prose does not require labels.

The governing contract for all labeling behavior is `prompts/research-agent.md`. The confidence and source rubrics are in `prompts/confidence-rubric.md` and `prompts/source-rubric.md`. These files are read-only inputs to the agent — never modify them as part of a pipeline run.

### Audit gate

`audit-pass` is a blocking gate: the assembled draft is only written to `dossiers/v4-orchestrated.md` if audit returns PASS. The 13 failure modes in `skills/audit-pass/SKILL.md` are always blocking. Additional check 14 (unlabeled claim) is critical only for the material claim categories above, not for every sentence with factual content. Non-material label misses are advisory.

### Data flow

```
sources/*.md  →  firm-profiler / portfolio-discoverer / portco-profiler
                        ↓
              draft_sections (in memory)
                        ↓
              claim-labeler (Phase 7) → typed/*.md
                        ↓
              dossier-assembler → assembled/v4-orchestrated-draft.md
                        ↓
              audit-pass → dossiers/v4-orchestrated.md (on PASS)
```

## Key constraints

- **No web browsing** in any skill unless the user message explicitly allows it. All source material must be pre-downloaded to `sources/`.
- **`prompts/` is read-only** — no skill or orchestration layer writes to it.
- One retry per critical audit flag. If the flag persists after retry, the runner halts and surfaces the flagged draft and audit report; it does not write a final dossier.
- The runner uses `--effort low` and `claude-sonnet-4-6` for all calls. Do not change the model without checking credit implications.
