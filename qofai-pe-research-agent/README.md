# pe-research-agent

Built as pre-work for my internship at QofAI. It produces a sourced dossier on a
middle-market private equity firm: the firm's profile, its portfolio companies, revenue and
EBITDA estimates for the private ones, and recent activity. Every load-bearing claim carries
two labels: a confidence band (high, medium or low) and a source type (public document,
public inference or private inference).

This is a sanitized copy. The firm it was built against, its portfolio companies and its
people are renamed, and the source documents, runs, dossiers and eval set are left out.

## How it works

The agent is split into eight skills, each with its own contract in `skills/<name>/SKILL.md`
(trigger, input, output, failure modes):

| Skill | What it does |
|---|---|
| firm-profiler | The firm-level profile |
| portfolio-discoverer | The portfolio company inventory, with status conflicts and add-ons resolved |
| portco-profiler | One portfolio company, run as a parallel subagent per company |
| private-data-approximator | Revenue and EBITDA bands for private companies, from public comparables |
| confidence-scorer | Applies the confidence rubric to every claim |
| source-typer | Applies the source rubric to every claim |
| dossier-assembler | Assembles the dossier (plain Python, no model call) |
| audit-pass | Critiques the finished dossier and flags weak claims |

`scripts/run_research.py` orchestrates them through Claude Code. Firm profiling and portfolio
discovery run in parallel, then one portco-profiler subagent per company
(`.claude/agents/portco-profiler.md`), then estimates, a combined labeling pass, assembly and
the audit. Every run writes a plan and a log under `runs/<timestamp>/`, and a partial run can
resume from assembly or from the audit.

`prompts/` holds the agent's instructions and the two rubrics, with worked examples of each
label and of the errors an earlier version made.

## Evaluation

`scripts/api_confidence_scorer.py` scores claims through the Claude API with a forced,
machine-readable label. On a 50-claim eval set with reference labels, it was right 50% of the
time when it saw only the claim text. Adding a one-line summary of each claim's source raised
that to 79.6%, against 86% for the human scorer, which closed 83% of the gap. The eval set
itself is not published because it quotes the real dossier.

## Running it

```
python3 scripts/run_research.py "Example Capital" --dry-run     # prints the plan, no model calls
python3 scripts/run_research.py "Example Capital" --skip-financials --portco-workers 10
```

A full run needs Claude Code installed and authenticated, and source documents under
`sources/`, which this copy does not include.
