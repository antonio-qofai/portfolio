# Standing instructions for this repo

This project is an internship and off-cycle opportunity tracking agent, built for the owner
for the Summer 2027 recruiting cycle. The owner is both the founder and the builder, and he is
not a computer science major, so explanations matter as much as code. See `PRD.md` for the
full spec; it is the single specification of record.

## Rules

1. **Ask before any git push.** Never push to a remote without explicit confirmation in the
   conversation, even if a local commit was already approved.

2. **Never hardcode a company or posting in code.** Company names, ATS tokens, and any
   posting-specific logic belong in data files (`sources/`, the `companies` table, or
   config), not in Python source. If you catch yourself writing `if company == "Anthropic"`
   anywhere outside a data file, stop and move it to data.

3. **The rubric lives only in `rubric.md`.** Never inline scoring criteria, tier
   definitions, or fit/reach rules as a prompt string in code. Code reads `rubric.md` at
   runtime. The owner edits `rubric.md` directly to change how ranking behaves; that edit
   should never require a code change.

4. **Explain changes in plain English.** the owner is an economics and physics double major,
   not a software engineer. When you finish a change, describe what it does and why in terms
   he can act on, not just what files moved.

5. **Keep the living documents current as you build.** Four files must always reflect the
   state of the repo, updated in the same session as the work, not later. Each has one job and
   must not take on another's:

   - `PRD.md`, the specification of record and the whole plan
   - `CHANGELOG.md`, backward-looking. A dated log of what changed and why, including fixes,
     reversals, and decisions that should not be relitigated
   - `README.md`, how to run the agent, kept so a stranger could run it in minutes
   - `NEXT_STEPS.md`, forward-looking and short. What is blocked on the owner and what gets
     built next, written so he can return after days away and pick up without rereading
     anything else. No command reference, no history, no status narrative; those live in the
     three files above

   A change that is not reflected in these is not finished. Update the right existing file
   rather than adding a fifth that restates it. When something breaks or an approach turns out
   wrong, record it in `CHANGELOG.md` under today's date as a `Fixed` entry. If it is something
   that must never happen again, also add it as a rule here, because a log entry will not be
   reread and a rule will.

   And a change that is only in the working tree is not finished either. Commit before the
   session ends, every session, even when the work is mid-thought; the commit is local and
   costs nothing, and the push is a separate act that needs the owner under rule 1. On 2026-09-18
   the whole of the 2026-09-07 session, thirteen files and 1,368 insertions including the
   failure monitor the agent's reliability now rests on, was found unstaged eleven days later.
   It had satisfied every sentence above: four documents updated, all four suites passing. That
   is the trap. Writing the documents is what makes work legible and committing is what makes
   it exist, and rule 5 was silent on the second until now.

   `MY_SEARCH.md` is not one of the four and is not a fifth. It is the owner's own list of
   applications, assessments and deadlines, created 2026-08-13 at his request. It is about
   getting a job; the four above are about building the agent. Do not merge it into
   `NEXT_STEPS.md`, do not treat it as a duplicate, and do not delete it. Nothing reads it at
   runtime, and it shrinks on its own once Milestone 7 starts delivering deadlines by email.

6. **Never identify a posting by anything a company can edit.** A posting is identified by
   `(source, external_id)`, the requisition id its board issues, and by a content fingerprint
   only where a board supplies no id at all. `agent/fetchers._identity` owns that rule and is
   the only place it may live. Identifying a posting by its title or location, which is what
   the code did until 2026-08-12, means an ordinary edit closes a job that is still open and
   rediscovers it as a new one, and every label, score override and Airtable link is left
   behind on the dead row. Two related invariants:

   - `hash` is the permanent row key. It is assigned once at discovery and never recomputed,
     because Airtable joins on it. Do not repurpose it.
   - `db._match` falls back from identity to `content_hash` on purpose. That second lookup is
     what keeps the two aggregator feeds, which carry the same jobs under different ids, from
     storing 51 companies' postings twice. Removing it looks like a simplification and is not.

7. **Never clean the Airtable base by deleting rows.** The base is not edited, it is rebuilt.
   `tools.sync_airtable` fills it from whatever currently survives the prefilter, so a posting
   deleted by hand returns on the next scheduled run, six hours later, while the owner's labels and
   every `airtable_record_id` are gone for good. When something is in the base that should not be,
   the only correct route is to fix the rule that let it in, then:

       tools.prefilter_report --dry-run     # read what the change would do
       tools.prefilter_report --reapply     # store it
       tools.test_prefilter                 # 99 cases, all must pass
       tools.sync_airtable                  # the prune removes the stranded rows

   `--reapply` is the step that gets skipped, and skipping it is silent. On 2026-08-14 four fall
   2026 internships were still sitting in the base under a rule that had killed fall 2026 outright
   six days earlier, because the rule was edited and never applied to the rows already stored.
   `--reapply` only touches open postings; a closed one keeps whatever verdict it had.

   The measurement before the edit matters as much as the edit. Ask which rule is admitting a bad
   posting ON ITS OWN before changing anything, because the answer is usually one entry doing all
   the damage, and read what your rule actually killed afterwards rather than assuming it worked.
   Both halves of that caught real errors on 2026-08-14.

8. **Airtable is metered per month, so count calls per run, not per second.** The free plan
   allows about 1,000 API calls per workspace per month. `agent/airtable.py` writes 10 records
   per call and paces itself to stay under 5 requests a second, which is the wrong limit to
   worry about; the monthly one is what actually bites. On 2026-08-15 the workspace hit it and
   Airtable opened a 30 day grace period, because the sync pushed all 282 posting rows on every
   run regardless of whether anything had changed, four times a day, roughly 4,300 calls a month.

   Two invariants came out of that fix and both are easy to undo by accident:

   - The push compares each payload against the live record and skips rows that already match
     (`_payload_differs`). The live records are downloaded anyway, so this is free. A change to
     `_comparable` that produces a false negative silently stops real updates from reaching the
     base, so test that function directly, not just through a dry run.
   - The sync runs on the digest job only, via `digest_only` in `sources/schedule.toml`. A step
     whose cost is metered by someone else's monthly quota does not belong on the watcher's
     cadence.

   Before adding any Airtable call, mirroring another table, or raising `max_posting_records`,
   work out what it costs per run and multiply by 30. `tools.sync_airtable --dry-run` reports
   how many rows would be written, and every 10 rows is one call.

9. **A create carries the owner's editable fields; an update never does.** The sync reads Airtable
   as authoritative for Label, Label reason, Applied status and the two overrides, and pushes a
   payload that omits them. That is right for an update, because he may be mid-edit. It is wrong
   for a create: the new row has those cells empty, and the next sync pulls the blanks down over
   whatever SQLite held. On 2026-08-15 that queued the destruction of seven labels and four
   reasons, and it went unnoticed for a year only because every create until then had been a new
   posting with no history to lose. `_create_payload` owns this and must stay the only path used
   for creates.

   Deleting a posting row from Airtable never frees a record. The push refills to
   `max_posting_records` on the same run, so a delete changes which postings occupy the slots and
   never how many records exist. Only lowering the cap or deleting company rows moves that number.

   Anything that prunes must be paired with an exclusion from the push, or the row is deleted and
   recreated on every run forever. The two older prune rules are safe by accident, because closed
   and killed postings are already excluded from the push. Nothing new gets that for free.

   A dry run that under-reports a deletion is worse than no dry run. Derive what will survive from
   what the caller knows, never from a column the real run writes and the dry run does not.

10. **Only stamp what an email actually carried, and never anything it merely considered.**
    `alerted_at`, `closure_alerted_at` and the two urgent stamps are what stop a posting being
    sent twice, and they are also what success criterion 1 audits coverage against. A stamped
    posting is offered by nothing, ever again, so a stamp written on a posting no email carried
    destroys that posting's coverage permanently and silently.

    Three things do not send, and all three must leave the stamp alone. A send that failed or was
    never configured, which was already true. A posting past the daily cap, which is new with
    Milestone 7 and is the trap this rule was written for: capping at 8 items and stamping all of
    them looks identical in the code and loses everything after the eighth forever. And a tier
    routed to a different email, which has not been carried by anything yet.

    `agent/run.py` stamps from `digest_rows`, derived from the groups the email actually printed.
    Deriving it from the candidate list instead is the mistake, and it reads as a simplification.

    Two smaller rules sit under this. A surfaced posting with no tier goes in the daily digest
    marked NOT YET SCORED rather than waiting, because ranking is capped and can be paused, so
    "wait until it is scored" can mean "never".

    That switch, `daily.include_unscored`, went false on 2026-09-20, so read the paragraph above
    as the reason it must go back on rather than as a description of what it does today. The owner
    asked for an inbox carrying only what he should really apply to, and the objection above is
    about a posting reaching NOTHING, not about it reaching the daily email specifically. Three
    conditions replaced it and the switch is only safe while all three hold: the ranker is
    running rather than paused, `queue.max_age_days` in `rubric.md` is 0 so every surfaced
    posting is eventually scored rather than only recent ones, and `tools.dashboard` exists and
    lists unscored postings. Break any one of them and an unscored posting is invisible again,
    which is the state this rule was written to prevent. The pairing of a scoring window with
    this switch off is the specific combination to never ship: it strands every posting older
    than the window, permanently and silently.

    And the location collapse has two different lead
    rows on purpose: the digest leads with the best-scored member, Airtable leads with the oldest,
    and swapping Airtable onto the score is the rule 9 churn trap wearing a different hat.

11. **`hash` names a row; `content_hash` describes a posting. They are not the same column
    twice.** Both are computed from company, title and location and they are identical the day a
    posting is discovered, which is what makes them easy to confuse. After that they diverge on
    purpose: `content_hash` follows every edit the company makes, and `hash` never moves, because
    Airtable joins on it. 343 rows had already diverged before this mattered.

    It matters when a second requisition turns up carrying the pre-edit title and location of a
    row that has since been edited. It computes the row key that first row is still using, and it
    matches nothing in `db._match` because it genuinely is a different posting. On 2026-08-17 that
    took the agent down for four consecutive runs and 42 hours, on one Anthropic posting.

    So a new row's key is allocated against the table, by `db._row_key`, never assumed from the
    posting. Anything that inserts into `postings` goes through it. Never add a UNIQUE constraint,
    a dict key or an Airtable join on the assumption that a content-derived value is unique across
    time; it is unique across one poll and nothing more.

    The 42 hours are the worse half of this. The watcher is a required step, so its failure stops
    the run, and a stopped run sends no email, which is indistinguishable from a quiet day. The
    agent cannot tell the owner it is broken using the channel that is broken. That is what
    `agent/health.py` was built for the same day; `tools.health` is the check now.

12. **The failure alert is the one thing whose own breakage is invisible.** Everything else in
    this system announces a mistake by producing a wrong email. `agent/health.py` announces one
    by producing no email, which is identical to a healthy quiet day. It can be wrong in two
    directions and neither shows up anywhere: a missed alert is the 42 hour outage happening
    again, and an alert on every run trains him to ignore the only message here that is never
    routine.

    So it is never verified by reading it. `tools.test_health` is 22 cases and they are
    mutation-tested; a change to the alerting rules adds a case there, and the way to trust that
    case is to break the code deliberately and watch it fail.

    That is not a formality. On 2026-09-07 the mutation pass found a hole the twenty other
    cases did not: deleting the one line in `run_step` that reads the watcher's report left
    every test passing, because the reader fails open by design and a run with no report is
    judged the old way. Everything on both sides of that line was covered and the line was not.
    Any fail-open seam in this module needs a case that runs the real thing end to end, not one
    that tests the two halves separately.

    Five invariants underneath, each of which looks like a detail and is not:

    - A run that finished is not a run that worked, and the monitor judges the second thing.
      Added 2026-09-07, when 22 of 57 runs over a fortnight asked all 176 sources, had all 176
      fail on DNS, stored nothing, exited zero, and were every one of them recorded as a
      success. `tools.health` called the agent healthy throughout. The floor is
      `min_sources_ok_fraction` in `sources/schedule.toml` and the verdict is
      `health.sources_shortfall`. Two consequences that are easy to undo by accident. The
      watcher reports what it achieved by printing one line that `agent/health.py` owns the
      format of, never by sharing a table, because the wrapper reading it from `agent.run`
      would drag `agent.db` into the module that has to survive a database failure. And the
      reader fails open, so a run whose report cannot be parsed is judged the old way; that is
      the right direction and it means a typo in the format silently restores the bug, which is
      why the round trip and the subprocess both have cases.
    - An alert is stamped only when the mail actually left. This is rule 10 in another costume:
      the stamp buys 12 hours of silence, so stamping a send that failed buys 12 hours of silence
      about a broken agent.
    - The heartbeat is a JSON file, not the `agent_state` table it obviously belongs in. A
      monitor sharing a dependency with the thing it monitors dies with it, and the crash that
      prompted all this was a database error. Nothing in `agent/health.py` may import `agent.db`,
      `agent.delivery` or anything reading `rubric.md`.
    - `report_health` never raises. It runs after everything else has finished, and a monitor
      that can turn a working run into a failed one is worse than no monitor.
    - The heartbeat is saved **before** the alert is attempted, never after. `record_failure`
      only mutates memory, so whatever persists it has to run before anything that can throw,
      and the send touches the network. On 2026-08-20 the save sat downstream of the send: the
      digest failed, the alert could not resolve DNS, the exception skipped the save, and the
      file was left reading `failed_runs: 0` for a run that had crashed. The lost email is the
      small half, because `failing_since` and `failed_runs` are what the escalation counts
      from, and a machine being offline is the likeliest cause of several failures in a row. So
      the fault the monitor exists for was the one it could not accumulate. This is the general
      shape and not one line: never put the write that records a problem downstream of the call
      that reports it.

    What it does not cover, which must not be quietly forgotten: nothing fires if nothing ever
    runs again. The gap check reports a silence retrospectively, the first time anything runs at
    all, and that is the honest ceiling without a watchdog outside this repo.

13. **A source the agent narrows at the source is blind, not selective, and only Workday is
    allowed to be.** The other three fetchers read a whole board in one request and let
    `sources/prefilter.toml` decide what matters, so every kill is recorded and every rule change
    can be replayed over the full history. Workday hands over 20 postings per request against
    NVIDIA's 2,000, so it asks for the internships instead, and a posting outside that query is
    never seen at all. It cannot appear in a coverage audit as a kill and no prefilter edit brings
    it back.

    That is a real cost accepted once, for one ATS, with the reasoning written next to the setting
    in the `[workday]` block. Do not extend it. If a Greenhouse or Lever board ever looks
    expensive enough to narrow, the answer is a slower cadence for that source, never a narrower
    question, because the two fail differently: a slow source is behind and a narrowed one is
    wrong without saying so.

    Two invariants under it:

    - The query is built from facets the tenant publishes at runtime, never from ids written down
      here. Ids are opaque and per tenant; what is written down is which facets to read and which
      words mark a student job type, both generic. This is rule 2 applied to a query.
    - A board whose facets name no student job type **raises**. Returning an empty list is read by
      `db.age_missing` as every posting from that source having closed, so one employer's facet
      rename becomes a mass closure. `SourceError` is the documented way to say a source could not
      be polled and is never evidence a posting closed. Leidos is the live example and is
      deliberately not in the token map.

14. **the owner's to-do list is not the agent's outbox, and the difference is the stamp.** Every
    email until 2026-08-19 carried things the agent had found: offered once, stamped, never
    offered again, with rule 10 governing the stamp. The YOUR MOVE block carries things he has
    started and not finished, so it is true until he finishes them and it repeats on every digest
    until it empties. There is no stamp to write and there must never be one. A stamp here would
    silently delete his to-do list one item at a time, and `tools.test_actions` has a case that
    fails if one appears.

    The same distinction decides where his answers are written. He edits Airtable; the agent
    records what that edit means. `applied_at` is stamped when Applied status moves off
    `not_applied`, because Airtable stores what a cell says and never when it changed, so the
    move is the only chance to catch the date. It is computed outside the "did any field differ"
    test in the pull loop on purpose: an application logged before the column existed differs in
    nothing, so a stamp gated on a change would miss the longest-silent applications, which are
    the ones that matter.

    And his own closure writes `closed_by_me`, never `closed_detected_at`. That column belongs to
    the watcher, which clears it the moment a board lists the posting again, and a board still
    listing a dead role is the entire case for the checkbox existing. It prunes the Airtable row,
    which makes it the fifth prune rule needing a paired push exclusion under rule 9, and unlike
    the other four it is reversible: unticking restores the row, because the prune is a
    consequence of the column rather than a decision of its own.

15. **A tool that writes a column the agent reads must write exactly what the agent writes,
    and the spine must survive it not doing so.** Added 2026-09-22 after the agent spent 44
    hours down, seven consecutive runs, polling nothing.

    `tools.log_application` was built on 2026-09-20 to record applications, and it wrote
    `applied_at` as `db.now()[:10]`, a bare `2026-08-15`. Every other writer of that column is
    `airtable_sync._applied_stamp`, which writes a full `db.now()` carrying an offset.
    `datetime.fromisoformat` parses both without complaint and returns a NAIVE datetime for the
    short one, so the comparison against an aware cutoff in `delivery.action_content` raised
    `TypeError`. The watcher is a required step, so every run stopped there, before both syncs.

    Two separate mistakes and both need naming, because fixing either alone leaves the trap set.

    The writer was wrong, and a second writer of any column is the moment to go and read what
    the first one writes. A helper that fills a column by hand is not a lesser citizen of the
    schema than the sync is.

    And the reader was brittle, which is the worse half. Any column a person or a later tool can
    fill will eventually hold a date where a timestamp was expected, and the spine of the system
    must not care. `delivery._parse` now always returns an aware datetime and is the only place
    in that module which turns a stored string into one. A formatting difference must never be
    able to stop the watcher; it is the one step whose failure costs coverage.

    The good half of the story is that `agent/health.py` did its job: it noticed, it mailed, and
    `tools.health` said BROKEN since, with the failing step and the exit code. That is what rule
    12 was built for and it is the reason this was 44 hours rather than a fortnight.

## Build order

Follow the milestone order in `PRD.md` section 10. Do not skip ahead to the ranker (Milestone
6) before the watchers (Milestone 2) and prefilter (Milestone 4) are working. The watchers are
the spine of the system; the ranker is the least load-bearing part.

## Source of truth

- SQLite is the source of truth for posting state. Airtable is an input surface only (labels,
  overrides), synced both ways, and should never become the state store.
- The rubric is versioned in `rubric.md`. The token map lives in `sources/`.
- There is no `interview-transcript.md` and there should not be one. A past session created it
  as a duplicate of `PRD.md`, and it was deleted. `PRD.md` is the single spec.
