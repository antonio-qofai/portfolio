# content-calendar-agent

Built during my internship at QofAI. A teammate started this agent and I took it over and
rebuilt it, so most of the code here is mine. It plans the founders' LinkedIn posting
calendar. It does not write posts. It reads what the company's other content agents produce,
plus a weekly scan of AI and private equity news, and decides which post goes on which day
for each founder, one month at a time. Nothing is published by the agent; a person approves
every post and records when it went out.

This is a sanitized copy. People are renamed, and the narrative files, real calendars and
run history are left out. See "What was changed for publication" below.

## How it works

- `ingestion/` reads each upstream source every morning: two agents' output folders, a
  conference tracker over the Airtable REST API, the company's internal portal, and RSS feeds
  for the market scan (`market_scan_sources.yaml`). Every run is logged to an append-only
  store, and `gap_detection.py` raises a flag when a source goes quiet or an expected input,
  such as the monthly briefing, is late.
- `synthesis/assemble.py` builds a month's calendar with one Claude call. The whole
  sequencing policy (cadence, posting days per founder, what to weigh) lives in
  `sequencing_criteria.yaml`, and the Python holds no criterion of its own.
  `monthly_assembly.py` builds next month's calendars on a schedule, `refresh.py` asks each
  week whether a standing calendar is still the right one, and `repair_rejected.py` fixes a
  window that failed validation without touching one that is already standing.
- `calendar_model/` defines the window and slot schema and validates every window against
  the thread, form and lens vocabulary before it is saved.
- `approval/queue.py` is the only thing that records a human decision. A decline needs a
  reason, which goes back to the agent for its next pass. Publishing needs an approval and a
  link to the live post.
- `ui/app.py` serves the calendar as one page with every founder's posts on it. Reviewers can
  approve, decline, undo, swap two posts, drag a post to a new date, and mark a post
  published. Every action goes through the queue and the page re-reads the store, so what it
  shows is what was saved. `display/render_calendar.py` writes the same page as a static file.

## Running it

```
pip install -r requirements.txt
cd approval && python -m pytest       # the approval queue
cd ../calendar_model && python -m pytest
cd ../ingestion && python -m pytest
cd ../synthesis && python -m pytest
cd ../ui && python app.py             # the calendar on localhost:5000
```

Run the tests folder by folder. Each folder imports its own modules by name, and
`approval/queue.py` shadows the standard library's `queue` when they are collected
together. The ingestion readers and the model calls need credentials (Airtable, the
portal, `ANTHROPIC_API_KEY`) that are not part of this repo.

## What was changed for publication

- Founders, teammates and the other agents' owners are renamed with placeholder names,
  consistently, so the tests still pass.
- `narrative/` held the company's positioning, voice notes and a founder's published posts.
  Only a synthetic `narrative/threads.yaml` is published: it has the same thread, form and
  lens ids the code uses, with every claim and note replaced.
- `state/` held real calendars and run history. Only one synthetic month is published, which
  the repair tests use.
- Left out: the PRD, changelog, build plans, handoffs, founder questions and meeting notes.
  The daily and weekly GitHub Actions workflows live at the root of the shared repo and are
  not included.
