Built during my internship at QofAI, as pre-work, this agent writes a sourced dossier on a
middle-market private equity firm, covering the firm, its portfolio companies, estimated
financials for the private ones, and recent activity. It is split into eight skills with
written contracts, orchestrated through Claude Code with one parallel subagent per portfolio
company and a final audit pass that critiques the dossier. Every claim is labeled with a
confidence band and a source type. On a 50-claim eval set, an API-based labeler went from
50% to 79.6% accuracy once it saw a one-line summary of each claim's source, against 86% for
a human scorer.

Python, Claude Code subagents, Claude API.
