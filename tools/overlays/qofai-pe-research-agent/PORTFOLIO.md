Writes a sourced dossier on a private equity firm: its profile, its portfolio companies,
revenue and EBITDA estimates for the private ones from public comparables, and recent
activity. Eight skills with written contracts run under Claude Code on Sonnet 4.6, with one
parallel subagent per portfolio company and a final audit pass that critiques the dossier.
Every claim carries a confidence band and a source type, and an interrupted run resumes from
assembly or audit. In an eval on 50 labeled claims, adding a one-line source summary per
claim raised the API labeler from 25 of 50 correct to 39 of 49 scored (one failed on an API
overload), against 43 of 50 for a human scorer. Internship pre-work.

Python, Claude Code subagents, Claude API.
