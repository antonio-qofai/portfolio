Schedules the founders' LinkedIn posts from the output of QofAI's other content agents. A
daily job ingests five sources (two upstream content agents, an Airtable conference tracker,
an RSS news scan and post engagement from the internal portal) and flags any that go quiet or
arrive late. Once a month a single Claude Opus 5 call at high effort sequences the posts into
a calendar per founder, returning schema-constrained JSON under a policy defined entirely in
YAML. Before saving, every slot's thread, form and lens tags are checked against the content
taxonomy. Each calendar is re-checked weekly and repaired on failure. Founders approve,
decline, swap and reschedule on a served page through one approval queue, and the reason
required on every decline feeds the next run. The agent never posts. Taken over from a
teammate and largely rebuilt, with 173 tests.

Python, Claude API, Flask, Airtable REST API, RSS, PyYAML, GitHub Actions, Railway.
