Writes a sourced dossier on a private equity firm: its profile, its portfolio companies,
revenue and EBITDA estimates for the private ones from public comparables, and recent
activity. Eight skills with written contracts run under Claude Code on Sonnet 4.6, with one
parallel subagent per portfolio company and a final audit pass that critiques the dossier.
Every claim carries a confidence band and a source type, and an interrupted run resumes from
assembly or audit. In an eval on 50 labeled claims, the API labeler rose from 50% to 79.6%
accuracy after adding a one-line source summary per claim, which closed 83% of the gap to a
human scorer at 86%. Internship pre-work.

Python, Claude Code subagents, Claude API.
