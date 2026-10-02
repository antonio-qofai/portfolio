Built during my internship at QofAI, this agent plans the founders' LinkedIn calendar. It
does not write posts. Each morning it reads what the company's other content agents produced,
plus a weekly scan of AI and private equity news, and flags any source that has gone quiet.
Once a month it asks Claude to sequence the available posts into a calendar for each founder
under a policy kept entirely in configuration. It re-checks every calendar weekly and
repairs any month that fails schema validation. Founders approve, decline, swap and
reschedule posts on a served page. Every action goes through a single approval queue,
declines need a reason that the agent reads next time, and the agent never posts anything
itself. A teammate started it and I took it over and rebuilt most of it.

Python, Claude API, Flask, Airtable REST API, RSS, PyYAML, GitHub Actions, Railway.
