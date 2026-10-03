Tracks internship postings across 179 company job boards (Greenhouse, Lever, Ashby, Workday)
and three aggregator feeds, four times a day. As of October 2026 it has tracked 40,659
postings. A rules filter defined as data cuts the 27,968 open ones to 2,485 (8.9%). Claude
Haiku 4.5 reads the term and weekly hours once from each new posting that passes the first
rules and that no feed has already labelled. A two-stage ranker then scores survivors against a
fixed-anchor rubric. Haiku scores every posting, and only those above a routing threshold go to
Claude Sonnet 5. Each prompt carries all 53 of my Airtable labels as few-shot examples, which
also pushed the prompt past Haiku's caching threshold, so every call after the first in a run
reads it at a tenth of the input price. A hard per-run call cap bounds spending. Output goes to
a daily email, an Airtable base and a local dashboard, on a launchd schedule with health
alerts. About 11,000 lines of Python including tests, and 456 checks.

Python, SQLite, Claude API, Airtable API, launchd.
