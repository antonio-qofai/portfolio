An agent that watches about 175 company job boards and three aggregator feeds on a schedule,
stores every posting in SQLite, works out what is new and what has closed, and filters out
anything that does not apply. Survivors go through a staged LLM ranker. A cheap model
scores every posting against a rubric with fixed anchors, and only the strongest go to a
larger model. The results reach me as a daily email digest, an Airtable base I label, and a
local dashboard. My labels feed back into the ranker as few-shot examples. It also has
mutation-tested health alerting and runs itself on a launchd schedule.

Python, SQLite, Claude API, Airtable API, Greenhouse/Lever/Ashby/Workday fetchers, launchd.
