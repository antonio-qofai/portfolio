# Changelog

One entry per push, newest at the bottom.

## 1 — Scoping (Sept 20, 2026)

`PRD.md` v1.1 and `CLAUDE.md`. No code.

## 2 — Rotation and calendar engine (Sept 20, 2026, not yet committed)

The first module. Pure arithmetic: which weeks are active, when a chore
occurs, and whose turn it is. No Airtable, no email, no network, no Actions.

Added:

- `config/calendar.py` — the `Term` shape and `AUTUMN_2026` as data.
- `config/cadences.py` — the `Cadence` shape and the `weekly` / `every_3`
  registry. `src/` contains no cadence name at all.
- `src/rotation.py` — `weeks`, `active_weeks`, `occurrence_index`,
  `occurrences`, `occurrence_count`, `assignee`, `validate_plan`, `generate`,
  `generate_week`.
- `tests/test_rotation.py` — 32 tests, all passing.

Decisions made this session:

- Cadence meaning lives in config, not in source. Adding a cadence is a config
  edit. Biweekly is not blocked by name; it is rejected because 5 occurrences
  over 9 active weeks does not divide by 3.
- Uneven division raises rather than rebalancing. Roster is always 3 and 9
  divides by 3, so the check should never fire in practice; it exists so that
  a bad edit fails loudly instead of quietly producing an unfair quarter.
- Due dates are not in this module. Weeks carry start and end dates only.
  Due-date policy, including the Thursday 11:00 cleaner shift, belongs with
  the generation and cleaner module.

Verified: full quarter generates 72 assignments, exactly 24 per person, weeks
9 and 11 empty, every active week is 6 weekly plus 2 periodic, and every
person does exactly 2 weekly chores every week.

Open for the next session: the PRD's seeds `[0,0,1,1,2,2]` for the six
`every_3` chores put both of a week's periodic chores on the same person (week
1: one person has 4 chores, the other two have 2). Quarter totals stay exactly
even, so this is a comfort question, not a fairness one. Seeds
`[0,1,1,2,2,0]` spread it and preserve every total. This is Airtable data, not
code, so decide it when the base is seeded.

### Handoff

Next module, per the PRD build plan: the Airtable client — read Roster,
Chores, Rules, Cleaner Visits; validate on read and fail loudly with the
record ID and offending field. It should hand `src.rotation` plain `Chore`
objects and an ordered roster. Roster order determines rotation, so that
module needs to decide what makes the order stable (an explicit sort field
beats Airtable's view order).

Run the tests with:

    python3 -m unittest discover -s tests -t .

## 3 — Schedule builder (Sept 20, 2026, not yet committed)

Turns bare rotation assignments into records that can be written and emailed:
task text, due datetime, and cleaner prep conversion. Still pure, still no
network and no clock.

Added:

- `config/cleaner.py` — the `CleanerBehaviour` shape and the `normal` /
  `convert_to_prep` registry. Cancelling is deliberately not an option.
- `config/due_policy.py` — timezone and due times. Ordinary chores are due
  Sunday 20:00 America/Chicago, prep at 11:00 on the visit date.
- `src/schedule.py` — `build`, `build_week`, `unhandled_visits`, `normal_due`,
  `prep_due`, plus the `ChoreDetail`, `CleanerVisit` and `ScheduledChore`
  shapes.
- `tests/test_schedule.py` — 33 tests. 65 across the repo, all passing.

Decisions made this session:

- Due times live in config, like cadences and cleaner behaviours. The ordinary
  due time is Sunday 20:00 rather than 23:59 so that a Monday nudge is about a
  chore that was genuinely skipped, not one somebody was going to do Sunday
  night. The PRD never stated it.
- A chore's presentation fields (task text, prep text, cleaner behaviour)
  arrive in a details mapping keyed by chore key, rather than being added to
  `rotation.Chore`. Keeps the rotation engine ignorant of everything but
  arithmetic, and the mapping is the natural shape of an Airtable read.
- A `convert_to_prep` chore with a blank prep task raises on every run, not
  only in a month when a visit happens to be confirmed. The row is wrong the
  moment it is written and should fail then.
- A confirmed visit in an inactive week or outside the term converts nothing,
  but is returned by `unhandled_visits` rather than ignored. That is the hook
  for PRD 5.5 (fold prep into that week's one-off) when the one-off module
  gets built.

Conflict resolved, flagged for the record: CLAUDE.md says cleaner-week due
dates shift to "Thursday 11:00 local"; PRD 5.5 says 11:00 on the visit date,
read off the row, and explicitly forbids hardcoding a day of the week. The
PRD wins here, so the day comes from the Cleaner Visits row and only the time
is policy. CLAUDE.md is worth amending so this does not get re-litigated.

Verified: a confirmed cleaner week swaps only the text and the due time,
assignees and slots are byte-identical with and without the visit, and the
full PRD-shaped quarter still generates 72 assignments at 24 per person with a
visit in play (SC8). Daylight saving is covered by a real test: the quarter
crosses the fall-back on Nov 1, so week 4 is due in CDT and week 5 in CST.

### Handoff

Next: either the Airtable client or the digest renderer, both of which now
have a settled shape to work against.

The Airtable client reads Roster, Chores, Rules and Cleaner Visits and writes
Assignments. It should produce, per chore row, one `rotation.Chore` and one
`schedule.ChoreDetail`, plus an ordered roster. Field names belong in a config
map, not in source. Roster order determines rotation, so it needs an explicit
sort field rather than Airtable's view order, and that field should be added
when the base is seeded.

The digest renderer takes `ScheduledChore` objects plus House Rules and
returns strings. Nothing in it needs a clock either, so it can stay pure and
be tested the same way.

Still open from session 2: the periodic seeds `[0,0,1,1,2,2]` put both of a
week's `every_3` chores on the same person. `[0,1,1,2,2,0]` spreads it and
preserves every total. Airtable data, decide when seeding.

## 4 — Digest renderer (Sept 20, 2026, not yet committed)

The Monday email body. One shared plain text digest showing the whole week's
split, plus the house rules in force that week. Pure string building: no
clock, no network, no templates on disk.

Amended `CLAUDE.md` first, as agreed: the cleaner-week due rule now says 11:00
on the visit date read off the Cleaner Visits row, with the weekday explicitly
forbidden, and the mistakes log has its first entry recording why.

Added:

- `config/rules.py` — the `RuleCategory` shape and the override / standing
  registry, including each category's heading and sort order.
- `config/digest_copy.py` — every word the digest can say, including weekday
  and month names, so output does not depend on the machine's locale and the
  wording can be changed without touching code.
- `src/digest.py` — `render`, `active_rules`, `format_due`, `format_date`,
  plus the `Rule` and `Digest` shapes.
- `tests/test_digest.py` — 42 tests. 109 across the repo, all passing.

Changed:

- `schedule.ChoreDetail` gained a `name` field, and `ScheduledChore` carries
  it through. The two were previously collapsed into `task`, which is enough
  to compute a due date but wrong for a digest line: the name is how someone
  recognises their slot, the task is what they have to do. `tests/
  test_schedule.py` updated to match.

Decisions made this session:

- One shared body, not personalised. Seeing everyone's week is the point; a
  rotation only feels fair if the fairness is visible.
- Plain text only. Renders the same everywhere and gives the best odds of
  landing in the inbox rather than Promotions, which is the highest-severity
  risk in the PRD after logging going stale.
- Nothing about last week. No completion state, no overdue mention, and
  `render` takes no such argument, which a test asserts. Overdue work is
  private and belongs to nudges.
- Rule numbers are displayed as given rather than recalculated, so the
  numbering people refer to out loud survives rules being added or reordered.

Assumption worth a second look: a rule with an `active_from` date appears in
the digest for the week that contains that date, not the week after. The
quiet-hours rule activating Nov 1 therefore first appears in the week 5
digest sent Monday Oct 26, as a few days' warning. Flip the comparison in
`active_rules` to `week.start_date` if it should wait until week 6 instead.

### Handoff

Next: either the nudge module or the Airtable client.

Nudges are the better one to do while everything is still pure. The decision
logic (which open assignment is overdue, who gets a first nudge, who gets the
48h follow-up, who gets nothing, and the faster first nudge for whichever
chore is flagged as cascading) stays testable if `now` and the nudge log are
passed in as arguments rather than read. The nudge body is another renderer
alongside the digest, sharing the copy pattern.

The Airtable client still needs the base to exist. When it does, each chore
row produces one `rotation.Chore` plus one `schedule.ChoreDetail`, each rules
row one `digest.Rule`, and each cleaner row one `schedule.CleanerVisit`.
Field names belong in a config map. Roster order drives the rotation, so add
an explicit sort field to the Roster table when seeding rather than relying
on view order.

Still open from session 2: the periodic seeds `[0,0,1,1,2,2]` put both of a
week's `every_3` chores on the same person. `[0,1,1,2,2,0]` spreads it and
preserves every total. Airtable data, decide when seeding.

## 5 — Nudge decision logic and body renderer (Sept 20, 2026, not yet committed)

Which open assignments get a nudge and what the email says. Pure functions.
`now` and the nudge log are arguments, never read from a clock or a file.

Added:

- `config/nudge_copy.py` — the `NudgeCopy` shape and `DEFAULT_NUDGE_COPY`.
  Follows the same pattern as `config/digest_copy.py`: all English in config,
  none in source. Date-formatting fields are identical to `DigestCopy` so
  `src/digest.format_due` can be reused without a separate utility.
- `src/nudge.py` — `pending_nudges`, `render_nudge`, plus the `NudgeRecord`,
  `PendingNudge`, and `NudgeEmail` shapes. Constants `FIRST`, `FOLLOWUP`, and
  `FOLLOWUP_DELAY` (48h).
- `tests/test_nudge.py` — 31 tests. 140 across the repo, all passing.

Decisions made this session:

- No "nudges faster" chore flag. The PRD's "faster first nudge for dishwasher"
  was reconsidered: the agent has no sensor and can only see Airtable
  completion status, so there is nothing to make faster. All chores nudge
  immediately when past due. Distinct urgency can be addressed in copy later.
- "48 hours later" means 48 hours after the first nudge was sent, not 48 hours
  after the due date. If the log has multiple FIRST records (odd state), the
  earliest governs the delay.
- Assignment identity is `(week_number, chore_key, assignee)`. The caller is
  responsible for passing values that are hashable and compare by value.
- `completed_ids` is a frozenset of those tuples. `pending_nudges` receives
  all scheduled chores and filters to open ones itself.
- A FOLLOWUP-only log entry (no FIRST) is treated the same as having both:
  no third nudge is ever produced.

### Handoff

Next: the Airtable client. It reads Roster, Chores, Rules, and Cleaner Visits
and writes Assignments. It produces, per chore row, one `rotation.Chore` and
one `schedule.ChoreDetail`, plus an ordered roster. Field names belong in a
config map. Roster order drives the rotation, so add an explicit sort field to
the Roster table when seeding rather than relying on view order.

Still open from session 2: the periodic seeds `[0,0,1,1,2,2]` put both of a
week's `every_3` chores on the same person. `[0,1,1,2,2,0]` spreads it and
preserves every total. Airtable data, decide when seeding.

## 6 — Airtable client (Sept 20, 2026, not yet committed)

Reads Roster, Chores, Rules, Cleaner Visits, and Assignments; writes
Assignments. Converts raw Airtable records into the shapes the pure modules
expect, and raises loudly with the record ID and offending field on a bad row.

Added:

- `config/airtable_fields.py` — `AirtableFieldConfig` and
  `DEFAULT_FIELD_CONFIG`. Every Airtable table and field name lives here.
  Build the base to match this file exactly; rename a field by changing one
  string here and nothing elsewhere.
- `src/airtable.py` — `Person` dataclass (id, name, email, active, sort_order)
  plus `AirtableClient` with `roster`, `chores`, `rules`, `cleaner_visits`,
  `generated_week_numbers`, `completed_assignment_ids`, and `write_assignments`.
  Private parser functions are module-level and testable without HTTP. The
  `requests` import is deferred to `__init__` so parsers can be imported and
  tested without `requests` installed.
- `tests/test_airtable.py` — 42 tests. 182 across the repo, all passing.

Decisions made this session:

- `Person.__format__` returns `person.name`, so `"{person}".format(person=p)`
  works in the digest without any change to `src/digest.py`.
- Airtable record IDs serve as `chore.key`. They are globally unique, stable,
  and already present — no extra field needed.
- `write_assignments` calls `generated_week_numbers()` first and skips any
  week already present. Re-running for an existing week touches nothing.
- `completed_assignment_ids` takes the roster list and maps record IDs back to
  Person objects so identity tuples `(week_number, chore_key, assignee)` match
  those produced by `schedule.build` exactly.
- Offset and seed default to 0 when absent. Every other required field raises
  with the record ID if missing or empty.
- HTTP calls are not unit-tested; covered by integration testing once the base
  exists.

### Handoff

Build the Airtable base using `config/airtable_fields.py` as the field-name
reference. Required fields per table:

  Roster: Name (text), Email (email), Active (checkbox), Sort order (number)
  Chores: Name, Task, Cadence (single select: weekly/every_3), Cleaner
    behaviour (single select: normal/convert_to_prep), Prep task (long text),
    Offset (number, optional), Seed (number, optional)
  Rules: Text, Category (single select: override/standing), Number (number,
    optional), Active from (date, optional), Active until (date, optional)
  Cleaner Visits: Visit date (date), Confirmed (checkbox)
  Assignments: Week (number), Chore (link→Chores), Assignee (link→Roster),
    Task (long text), Due (date+time), Prep (checkbox), Done (checkbox)

Next: email sender (Gmail SMTP), then the entry-point orchestrator.

Still open from session 2: the periodic seeds `[0,0,1,1,2,2]` put both of a
week's `every_3` chores on the same person. `[0,1,1,2,2,0]` spreads it and
preserves every total. Airtable data, decide when seeding.

## 7 — Email, orchestrator, and the cron job (Sept 21, 2026, not yet committed)

The system now runs end to end. Everything that was pure is still pure; this
session added the two things that touch the outside world (mail and a clock)
and the wiring between them.

Added:

- `src/email_sender.py` — Gmail SMTP over SSL, plain text only. `send_email`
  plus `credentials`, `validate_recipients`, and `build_message` split out so
  the envelope can be tested without a socket. Raises if `GMAIL_ADDRESS` or
  `GMAIL_APP_PASSWORD` is unset.
- `src/orchestrator.py` — the entry point, and the only module that reads a
  clock, an environment, or argv. `--generate`, `--nudge`, `--dry-run`; no
  flags means both jobs, so the cron job is a bare invocation.
- `.github/workflows/scheduler.yml` — cron plus `workflow_dispatch`, four
  secrets, currently pinned to `--dry-run`.
- `README.md` — setup in five minutes, what each run does, and what to do
  when it breaks.
- `tests/test_email_sender.py` (21) and `tests/test_orchestrator.py` (47).
- `AirtableClient.nudge_log`, `.record_nudge`, and `.assignment_record_ids`,
  plus `first_nudge_sent` and `followup_sent` in `_AssignmentFields`. 29 new
  tests in `tests/test_airtable.py`.
- `scripts/setup_base.py` now defines the two nudge fields on Assignments and
  tops them up on a base where the table already exists.
- `INDEX.md` rewritten; it had been missing five files since session 6.

279 tests, all passing (was 182).

Decisions made this session:

- **The nudge log is two datetime fields on the assignment row**, not a
  table. An assignment can be nudged at most twice, so there is nothing to
  accumulate and nothing to reconcile.
- **`assignment_record_ids` was added** beyond what was asked for. Recording
  a nudge needs the Airtable record ID, but `src/nudge.py` is pure and works
  in `(week, chore_key, assignee)` tuples. Something has to map between the
  two and the client is the right place for it.
- **A pending nudge with no Airtable row is skipped, not an error.** It means
  the week was never generated or the row was hand-edited. Nudging about a
  row that does not exist is worse than staying quiet.
- **Two rows with the same identity raises.** That would make the nudge log
  ambiguous, and it can only happen if generation wrote twice.
- **Nudge timestamps must be timezone-aware.** A naive value raises rather
  than being assumed local, because misreading it shifts the 48-hour window.
- **`_parse_datetime` normalises to UTC on read**, so the follow-up window is
  compared against a single reference regardless of what Airtable returns.
- **The digest goes out on active weeks only.** Weeks 9 and 11 carry one-off
  tasks that do not live in Airtable, so a digest for them would be an empty
  email. Flagged as a judgement call, not a spec requirement.
- **The clock is read once**, at the top of the run, and passed down, so a
  run cannot disagree with itself across midnight or a DST boundary.
- **Cron is `0 12 * * 1-5`, not `0 13`.** 13:00 UTC is 08:00 Chicago during
  CDT, which is exactly the SC1 deadline, and GitHub's cron is documented as
  best-effort. 12:00 UTC gives an hour of slack and is 07:00 CDT / 06:00 CST.
- **A `concurrency` group on the workflow.** Two overlapping runs could both
  find a week ungenerated and write it twice.

### Data fixes applied to the live base

All three done in this session, before any assignments existed, so nothing
was rewritten under anyone.

- **The two nudge fields now exist.** `scripts/setup_base.py` created
  `First nudge sent` (fld3raPFeqq8ttHyy) and `Followup sent`
  (fldpUaT8h8W8aTX55) on Assignments. Nothing else was touched.
- **Three `every_3` seeds changed** so that no two chores sharing an offset
  also share a seed. Dust 0 to 1, Appliance care 1 to 2, Bathroom deep 2 to
  0. Two chores with the same offset occur in the same weeks, so a shared
  seed put both on one person and made that week 4/2/2. Every week is now
  3/3/2, which is the best a load of 8 across 3 people can do, and the light
  week rotates exactly 3 times each. Quarter totals are unaffected either
  way: 3 occurrences over 3 people gives everyone exactly 1 whatever the
  seed is.
- **`digest.active_rules` now sorts by rule number within a category.** It
  previously preserved Airtable row order, which is not number order, so the
  digest listed the override rules 1, 6, 2, 5, 8, 4, 7, 3. Numbers are still
  displayed exactly as given and are never recalculated; only the print
  order changed. Unnumbered rules follow the numbered ones in given order.
  6 new tests.

285 tests, all passing.

### Verified against the live base

A read-only run with the clock faked to the Monday of week 1 confirms:

    72 assignments total                        PASS
    24 each                                     PASS
    each weekly chore 3x per person             PASS
    each every_3 chore 1x per person            PASS
    9 active weeks, 9 and 11 excluded           PASS
    inactive weeks carry nothing                PASS
    every week splits 3/3/2                     PASS
    no biweekly cadence in use                  PASS
    nudge log reads                             PASS

### Handoff

The system is complete and the base is correct. What remains is operational,
not code.

1. Put the four secrets in GitHub Actions.
2. Trigger the workflow by hand and read the dry-run output.
3. Delete ` --dry-run` from the last line of
   `.github/workflows/scheduler.yml` to go live.

One thing worth building later, deliberately left out of this session
because it is a change to the pure engine with its own design questions:
**nothing enforces the offset-pair seeding rule.** Two `every_3` chores
sharing an offset and a seed is legal arithmetic and passes
`rotation.validate_plan`, because quarter totals stay even; it only makes a
single week lopsided. A future check could warn when a week's load is more
uneven than the roster size forces it to be. It belongs in `src/rotation.py`
as a warning rather than an error, since some roster sizes cannot avoid it.

## 8 — Override Log and the phone views (Sept 22, 2026, not yet committed)

Closing the two Airtable gaps found when planning the rest of the build. No
change to any module under `src/`; the test count is unchanged at 285.

Added:

- **Override Log table** (tblXXXXXXXXXXXXXX), built by
  `scripts/setup_base.py`. Fields: `Entry` (autoNumber primary, so logging
  needs no typing), `Who` (link to Roster), `Rule` (link to Rules), `Logged`
  (createdTime), `Note` (text).
- README section 4 on the phone views, and a section explaining what the
  Override Log is for.

Decisions made this session:

- **`Rule` links to the Rules table rather than being a single select.** The
  override items already live in Rules with their numbers. A select would
  duplicate that text and drift from it the first time a rule is reworded.
- **No code reads Override Log, and nothing was added to
  `config/airtable_fields.py`.** PRD 4.5 wants the data for one conversation
  in week 5, which is a group-by in the Airtable UI. Config entries that
  nothing reads are dead weight; add them when something reads them.
- **`Entry` is an autoNumber primary.** Airtable needs a primary field of a
  writable type, and anything typed is a tap too many for a one-tap log.

Two things the Airtable API refused, both worked around:

- `createdTime` cannot be created as part of a table-creation request, and
  rejects the request outright if any `options` are sent. `Logged` is now
  added in a second pass by `_add_missing_fields`, with no options.
- **Views cannot be created through the Meta API at all.** Every variant of
  POST `/meta/bases/{base}/tables/{table}/views` returned the same 422, with
  and without filters, with and without `visibleFieldIds`, plain grid
  included. The probe views were cleaned up and the base has no residue.
  Building the views is therefore a manual step, written up in README
  section 4.

### Not done, and it is the highest-risk item left

**The per-person Assignments views and the home-screen shortcuts do not
exist.** The base still has only the default Grid view on every table. This
is SC4 and PRD section 8 ranks it as the risk that kills the project, ahead
of anything technical. It has to happen during move-in, on all three phones,
with all three people present. README section 4 has the exact settings.

Note that a true per-user filter is not available here: `Assignee` is a
linked record, and Airtable's "current user" filter needs a Collaborator
field. Changing that would break `src/airtable.py`, which writes linked
record IDs, and would need all three on paid seats. Three per-person views
is the right answer instead.

### Handoff

Airtable is now complete. Remaining work, in dependency order:

1. Views and shortcuts, during move-in (above).
2. `git init`, first commit, push to a GitHub repo. Nothing exists yet, so
   Actions has nothing to run.
3. A personal Gmail with 2FA and an app password. Not a Workspace account.
4. The four secrets in Actions.
5. Test digest to all three addresses, everyone marks it not-spam.
6. Delete ` --dry-run` from `.github/workflows/scheduler.yml`.

Item 3 and the roommate conversation on the house rules are the two that
cannot be done from this repo, and the rules conversation needs to happen
before Sept 28 rather than after the first dispute.

## 9 — Per-person views and week 1 seeded (Sept 22, 2026, not yet committed)

No code changed. This entry records two changes to live state.

- **Three per-person views exist on Assignments**, named the owner, Blake,
  and Casey. Each filters to `Done` unchecked and `Assignee` has that
  person, sorts by `Due` ascending, and hides `Assignee`, `Prep`,
  `First nudge sent`, and `Followup sent`. Built by hand, because the Meta
  API cannot create or configure views (session 8).
- **Week 1 is generated.** 8 rows, written by `--generate` with the clock
  set to Tue Sep 29 rather than the Monday, so the digest branch stayed out
  of it while the Gmail secrets do not exist. Due dates come from the week,
  not from `now`, so the rows are identical to what Sept 28 would write.

Verified through the API against `schedule.build`: the owner 3 rows, Blake
3, Casey 2, each matching the rotation exactly, Grid view showing all 8, and
due dates at Oct 4 20:00 Central. A dry run with the clock on Mon Sep 28
confirms generation then reports week 1 already exists and writes nothing
while the digest still sends, which is the intended idempotent behaviour.

Two things the API could not check, both confirmed by eye instead:

- **Hidden fields.** Airtable returns a field when it holds a value, not
  when a view shows it, so a record read says nothing about visibility.
  `Prep`, `Done`, and the two nudge fields are absent from API responses
  purely because they are empty or false.
- **The sort.** In an ordinary week all 8 chores share one Sunday 20:00 due
  time, so any order satisfies it. It only begins to matter in a confirmed
  cleaner week, when prep items come due at 11:00 on the visit day.

**Consequence worth remembering:** week 1 no longer picks up chore edits.
Generation never rewrites a week that already exists, so a chore added or
changed now enters the rotation from week 2 onward.

### Handoff

Airtable is complete apart from the Override Log form view. Remaining work
is operational and largely outside this repo:

1. A personal Gmail with 2FA and an app password. Blocks every send, and
   nothing downstream can be tested without it.
2. A GitHub repo, then push. `git init` and the first commit are done
   (9e71e8e on `main`); no remote is configured.
3. The four secrets in Actions.
4. Test digest to all three addresses, everyone marks it not-spam.
5. Delete ` --dry-run` from `.github/workflows/scheduler.yml`.

Plus, during move-in on Sept 26: the Override Log form view, home-screen
shortcuts on all three phones, and the house rules conversation, which PRD
open question 2 wants settled before Sept 28.

Still open from session 8: the primary field on Assignments is `Week`, a
number, so the mobile app titles every row with a bare digit. Week 1 now
has real rows, so this can finally be judged on a phone. If it fails the
ten-second test the fix is rebuilding Assignments with a text primary field,
which also touches `src/airtable.py` and `config/airtable_fields.py`. That
rebuild is cheap while only week 1 exists and gets steadily less so.

## 10 — Sender display name, and the GitHub repo (Sept 22, 2026, not yet pushed)

Added:

- `config/sender.py` — `SENDER_NAME`, the display name on outgoing mail.
- `email_sender.build_message` and `send_email` take an optional
  `display_name` and build the From header with `formataddr`; the
  orchestrator passes `SENDER_NAME` on both the digest and every nudge.
  8 new tests, 293 in total.

Why it exists: the sending account is one roommate's personal Gmail rather
than a throwaway, so without a display name every digest and every overdue
reminder arrives visibly from that person. The design rests on a system
doing the assigning and the chasing so that nobody has to be the one who
nags, and a bare From line quietly undoes that. `formataddr` also quotes a
name containing a comma, which would otherwise parse as a second address.

The wording lives in `config/` rather than `src/` like all other English in
this repo. `src/email_sender.py` still imports no config; the orchestrator
supplies the name, so the sender stays a leaf utility.

Also:

- **GitHub repo created**, `antonio-qofai/apartment-chores`, private, with
  `origin` configured. **Private deliberately:** `PRD.md` carries two
  roommates' real email addresses.
- Nothing has been pushed. The commit `9e71e8e` and everything since sit
  local.

### Handoff

Still blocking, and outside this repo:

1. An app password on the sending Gmail. 2FA has to be on first.
2. The four secrets in Actions, once the repo has been pushed.
3. Test digest, everyone marks it not-spam.
4. Delete ` --dry-run` from `.github/workflows/scheduler.yml`.

Note the risk that comes with using a personal account rather than a
throwaway: changing that Google password revokes every app password on it,
and the scheduler goes quiet with no error anyone will see. If the digest
stops arriving, regenerate the app password and update the secret before
looking anywhere else.

## 11 — Runner pinned, test digest script (Sept 22, 2026)

Added:

- `scripts/send_test_digest.py` — renders a digest through the same
  schedule and digest code as the Monday run and sends it to addresses
  given with `--to`. Takes `--week N` and `--dry-run`.
- `.github/workflows/scheduler.yml` pins `runs-on: ubuntu-24.04`.

Why the script has no roster fallback: `--to` is required and recipients
never come from Airtable. A test send that could reach the household by
accident is worse than no test script, and the PRD lists "test digest sent"
as a move-in task, so this will be run by hand more than once.

Why the runner is pinned: `ubuntu-latest` migrates to Ubuntu 26 on Oct 19
2026, which falls in week 4. SC10 asks for a full quarter with no manual
intervention, and an image change mid-term is a moving part worth removing
for the cost of one word.

The script reaches into `orchestrator._load` and `_client` rather than
rebuilding the loading path. Two paths that read the base and build the
term would drift; one private import will not.

### Verified end to end

- **Actions run 35772618285 passed**, 9 seconds, on a cold runner. Proves
  checkout, Python 3.11, the dependency install, all four secrets, the
  Airtable reads, and the term-boundary logic, which correctly declined to
  do anything on Sept 22.
- **A real digest was delivered.** Week 1, sent to one address, through the
  live Gmail SMTP path with the app password from Actions. The sender line
  reads "Apartment Chores" rather than a bare address, which is what
  `config/sender.py` is for.

Everything in the system has now been exercised against live infrastructure
except two things: an actual nudge send, which needs an overdue assignment
and will first be possible after Oct 4, and a confirmed cleaner week, which
needs a row in Cleaner Visits.

### Handoff

1. Send the digest to all three addresses so everyone can mark it not-spam.
2. Delete ` --dry-run` from `.github/workflows/scheduler.yml` to go live.
3. Move-in, Sept 26: the Override Log form view, home-screen shortcuts on
   all three phones, and the house rules conversation.
4. Decide the `Week`-as-primary-field question on a phone before Sept 27,
   while a rebuild of Assignments is still cheap.

## 12 — Chore list cut to nine, definitions rewritten (Sept 22, 2026)

The cleaner's real scope turned out wider than the landlord's description:
she dusts throughout and does the bathroom deep-clean work. Three chores
were paying for work already covered, so they were dropped.

Removed from Airtable: **Dust**, **Bathroom deep**, **Supply run**. The
first two are Maria's. The third was replaced by a habit rather than a
chore — whoever notices something is out puts it in the group chat and
whoever is near a shop picks it up.

### The arithmetic moved, and that is fine

```
before   6 weekly × 9 = 54   +   6 every_3 × 3 = 18   =   72   =  24 each
after    6 weekly × 9 = 54   +   3 every_3 × 3 =  9   =   63   =  21 each
```

63 divides by 3 and every chore's occurrence count still divides by the
roster, so fairness is untouched and `validate_plan` passes unchanged. The
invariant was never the number 72; it is that each count divides by the
roster size. CLAUDE.md said 72 and 24 as hard figures, so that line has been
rewritten with a note to recompute it whenever the chore list changes.

**Appliance care moved from offset 1 to offset 2.** The three survivors sat
at offsets 0, 1, 1, which left offset 2 empty and gave weeks of 7, 8, and 6
on rotation. One periodic chore per offset makes every week a flat 7,
splitting 3/2/2, which is the best a 3-person roster can do with an odd
count. Verified across all nine active weeks.

### Definitions of done rewritten

All nine surviving chores now say what you do and, more importantly, what
the chore does not cover. Scope is where the arguments come from, so each
one names the chore that owns the adjacent work.

- **Bathroom clean** is now step by step, naming products and order, on the
  grounds that nobody in the apartment has cleaned a bathroom before. It
  also absorbed washing the bath mat and hand towels, which had been inside
  Bathroom deep. Maria does not do laundry, so without this they would
  have fallen through.
- **Both floors chores** now say to sweep and use judgement on the wet
  Swiffer rather than requiring it weekly.
- **Kitchen surfaces** prep picked up taking the garbage and recycling
  down, which was Bathroom deep's prep task and is the landlord's actual
  request before a visit.
- **House rules 5 and 6** pointed at "the supply list", which no longer
  exists. Both now say the group chat.

### Also

- `tests/test_rotation.py` gains `TestLiveApartmentShape`, 8 tests pinning
  the 6-and-3 configuration: 63 assignments, 21 each, a flat 3/2/2 every
  week. One of them deliberately reproduces the two-periodics-on-one-offset
  case and asserts it skews a week to 4/2/2 while still passing
  `validate_plan`, which is exactly why nothing catches it automatically.
- The 6-and-6 fixture stays as the main one in both rotation and
  orchestrator tests. It is now denser than the live base on purpose: two
  chores per offset is the harder case, and anyone reusing this repo may
  well have it. Both docstrings say so rather than claiming to mirror the
  base.
- 301 tests, all passing.

### Verified against the live base

63 assignments, 21 each, every chore's count dividing evenly, weeks 9 and
11 empty, and every active week splitting 3/2/2. Week 1 was deleted and
regenerated so its 7 rows carry the new task text rather than the old.

### Still open

Whether the roommates agree with these definitions. They were drafted, not
negotiated, and a definition of done only settles an argument if all three
signed off on it. That conversation was already scheduled before Sept 28
for the house rules; this belongs in the same sitting.

## 13 — Digest footer link, nudge write verified, v1.1 scoped (Sept 22, 2026)

- `config/digest_copy.py` footer now points at a shared read-only view of
  the Chores table. The definitions of done are long enough to settle an
  argument but too long to put on every digest line, so the digest stays
  short and links to them.
- `PRD-v1.1-landlord-reader.md` — a spec for the landlord email reader,
  scoped but not built. The shaping constraint is CLAUDE.md rule 4: it
  proposes unconfirmed rows and a human confirms. It reuses the pattern
  Cleaner Visits already has, where only `Confirmed` rows affect
  generation.

### record_nudge verified against the live base

The last write path that had never run for real. It matters because it
runs *after* `send_email`: a failure there means the nudge was sent, not
recorded, the run errors, and the same nudge goes out again the next
morning — the nagging failure the two-nudge cap exists to prevent.

Tested end to end on a real week 1 row: wrote a FIRST timestamp, read it
back through `nudge_log`, and confirmed the type, the UTC timestamp, and
the assignee all survived the round trip. Then fed the real log to
`pending_nudges`: nothing at +47h, a FOLLOWUP at +49h. The test timestamp
was cleared afterwards and the log is back to empty, because leaving it
would have suppressed the genuine first nudge on Oct 5.

Every code path in the system has now run against live infrastructure
except cleaner-week conversion, which needs a confirmed Cleaner Visits row
and degrades to an ordinary week if anything is wrong.

## 14 — Assignments gets a readable primary field (Sept 22, 2026)

Airtable makes the first field of a table primary: it cannot be hidden,
moved, or replaced by another field, and the mobile app uses it as each
row's title. On Assignments that was `Week`, a number, so the screen people
actually tick chores off on showed three rows all titled "1".

`Chore` could not take the slot — a linked record can never be primary — so
the primary field was renamed to `Assignment`, converted to single line
text in the UI, and the generator now writes the chore name into it. A
fresh `Week` number field carries the week.

- `config/airtable_fields.py` gains `label` on `_AssignmentFields`.
- `_format_assignment` writes `fields.label: sc.name`, snapshotted at
  generation time exactly like the task text, so a week that has already
  started never changes under whoever did it.
- `scripts/setup_base.py` builds the new shape for a fresh base and tops up
  the `Week` field on an existing one, so the script and the live base do
  not diverge.
- 2 new tests, 303 passing.

Week 1 was deleted and regenerated. Rows now title as "Bathroom clean",
"Kitchen surfaces" and so on.

**A hazard worth recording.** Between the rename and the type conversion,
the config pointed at a field name that no longer existed, so
`generated_week_numbers` returned empty and `_assignment_identity` matched
nothing. A live run in that window would have decided week 1 was never
generated and written it a second time. Nothing was at risk because the
workflow is still pinned to `--dry-run`, but renaming a field Airtable-side
before the config knows about it is a duplicate-generation bug waiting to
happen. Change the config in the same sitting.

Verified after: week numbers read back, all 7 rows match the identity map,
re-running generation is a no-op, and every invariant holds — 63
assignments, 21 each, a flat 3/2/2 in all nine active weeks.

## 15 — The digest gets an HTML view (Sept 22, 2026)

The digest now sends as `multipart/alternative`: plain text first, HTML
second. A client that renders HTML shows the HTML, and anything that does
not — including the lock-screen notification preview, which is where most
of these are first seen — gets the text.

Added:

- `config/digest_style.py` — `DigestStyle` and `DEFAULT_DIGEST_STYLE`.
  Fonts and colours only, no words. Times New Roman with Times and Georgia
  behind it.
- `digest.render_html(week, scheduled, roster, rules, copy, categories,
  style)` — a second view of the same digest. Pure, like the rest of the
  module.
- `email_sender.build_message` and `send_email` take an optional `html`
  and attach it with `add_alternative`. Plain text is added first on
  purpose: in `multipart/alternative` the last part wins, so HTML has to
  come second to be preferred.
- 20 new tests. 325 in total.

Decisions made this session:

- **Every word still comes from `DigestCopy`.** The style object carries no
  English. The two views read from one source, so they cannot drift into
  saying different things about the same week.
- **Colour is used exactly twice**, both times for the exception rather
  than the routine: the cleaner-week note, and prep chores inside it. In
  the one week in four when a chore turns into something else, that is what
  the eye should land on.
- **No images, no web fonts, no `<style>` block, no script.** Those are
  what get a message filed as Promotions, which the PRD ranks as the
  second-highest risk because it fails silently. A test asserts none of
  them can reappear.
- **Every style is inline.** Mail clients strip `<style>` blocks, so it
  cannot be declared once.
- **Rules are not an `<ol>`.** Numbers are displayed as given and never
  recalculated, and an `<ol>` would silently renumber whatever survives the
  activation-date filter.
- **Nudges stay plain text.** A one-paragraph reminder inside a styled card
  reads as more of a production than it is.
- **Everything from Airtable is escaped.** The base is hand-edited and
  already contains `&` and `→`.

### The plain text improved too

It had been repeating the same deadline on all seven lines. That is now
stated once under the header, and people are separated by blank lines:

```
Week 1, Sep 28 to Oct 4
Due Sun Oct 4, 8pm unless a line says otherwise.

The owner
  - Bathroom clean
  - Dishwasher duty
  - Floors — low traffic
```

Prep chores lost their inline deadline too, since the cleaner note above
already states it. Three existing tests asserted the old format and were
rewritten to assert the new intent rather than edited to pass.

### Handoff

Two things left before this is live:

1. Send the digest to all three addresses, everyone marks it not-spam.
2. Delete ` --dry-run` from `.github/workflows/scheduler.yml`.

## 16 — Cut the API calls per run from 8 to 5 (Sept 22, 2026)

Airtable emailed that the workspace has used 80% of the Free plan's
monthly API call allowance. Most of that is this build session, not the
scheduler, but it prompted measuring what a run actually costs.

A run made 8 requests, and 4 of them were the same table:

```
GET Assignments   x4     <- generated_week_numbers, completed_assignment_ids,
GET Chores        x1        nudge_log, assignment_record_ids
GET Cleaner Visits x1
GET Roster        x1
GET Rules         x1
```

All four derive from the same rows, so they are now one read:

- `AssignmentSnapshot` — a frozen dataclass holding `week_numbers`,
  `completed_ids`, `nudge_log` and `record_ids`.
- `AirtableClient.assignments(roster)` builds it from a single `_fetch_all`.
- `write_assignments` takes an optional `existing` so a caller holding a
  snapshot does not make it re-read the table.
- The orchestrator takes one snapshot in `_load` and both `_generate` and
  `_nudge` read from it.

5 requests per run. Over 5 weekday runs across 11 weeks that is roughly 275
calls for the whole term instead of 440.

**Why taking the snapshot before writing is safe.** The only week a run
writes is the current one, and nothing in the current week can be past due
yet, so a freshly written week has nothing to contribute to a nudge
decision. The snapshot going stale mid-run cannot change an outcome.

The four original methods are kept. They are individually tested, useful
from scripts, and cost nothing to leave in place.

3 new tests under `TestApiCallBudget` pin the count, including that
writing a week does not trigger a second read. 328 in total.

Six existing nudge tests mutated the fake client after `_load` had already
snapshotted it, so they now re-snapshot explicitly. That is a fair
reflection of the real constraint rather than a workaround: a snapshot is
a point-in-time read, and a test that changes the data underneath it
should have to say so.

## 17 — Drop the Override Log (Sept 22, 2026)

Removed from `scripts/setup_base.py`, the README, and the setup steps. The
table itself is left in Airtable, empty and unused; nothing reads it and
Airtable has no API for deleting a table.

PRD 4.5 argued for it: override rules resolve to whoever notices first,
which is reliably the person with the lowest tolerance for mess, and over
eleven weeks that becomes one person doing everything and resenting it.
The log was meant to put data behind that conversation in week 5.

Three reasons it was the wrong instrument:

- **It competes with the logging that matters.** The highest-ranked risk in
  the PRD is people quietly stopping logging around week three. A second
  tap to remember makes the first one less likely to stick.
- **Uneven use makes its data worse than no data.** Whoever logs
  diligently looks like they do everything; whoever forgets looks like they
  do nothing. You would read that table in week 5 and draw a confident
  wrong conclusion.
- **It is a tally.** The PRD rules out points, scores, and reporting on
  each other everywhere else. A count of who took the bins out is a score
  by another name.

Replaced by asking the question out loud in late October: is anyone doing
all the overflow? If so, that item moves into the weekly rotation, which is
the same remedy section 4.5 prescribed. The data was never the mechanism,
the conversation was.

Also removed `_logged_field`, which existed only to build that table's
createdTime column.

No code changed and no test changed, because nothing ever read it.

## 18 — v1.1 landlord email reader, built and not yet live (Sept 23, 2026, committed as 758546f)

Everything in PRD-v1.1 §4, behind its own entry point and its own workflow.
Nothing in v1 changed: no v1 file was edited, and the Monday workflow is
untouched.

Added:

- `config/landlord.py` — allowlist, IMAP host, lookback window, model and
  its parameters.
- `config/inbox_copy.py` — the system prompt and the summary email wording.
- `config/proposal_fields.py` — Requests table and the two new Cleaner
  Visits fields. Deliberately has no Confirmed field to write.
- `src/inbox.py` — IMAP fetch. Read-only mailbox, BODY.PEEK only, and a
  message is fetched past its headers only if From is exactly one
  allowlisted address and Gmail's own topmost Authentication-Results says
  DMARC passed for that domain. Quoted replies are cut before the model
  sees anything.
- `src/extract.py` — the one model call (`claude-sonnet-5`, adaptive
  thinking, effort medium, structured output). Checks the answer in plain
  code: the excerpt must really be in the email, the date must fall within
  60 days after sending, and must agree with any weekday or day of the
  month the excerpt names. A visit that fails becomes a dateless request.
- `src/proposals.py` — pure planning of rows plus a thin store over the v1
  client's generic fetch and create helpers. De-duplicates by Message-ID
  and, for visits, by date. Refuses any row carrying a Confirmed field.
- `src/read_inbox.py` — the entry point, `python3 -m src.read_inbox
  [--dry-run]`.
- `scripts/setup_inbox_tables.py` — creates Requests and adds the two
  Cleaner Visits fields. Needs schema scopes on the token.
- `.github/workflows/read_inbox.yml` — daily 13:00 UTC, installs
  `anthropic>=1.8,<2`, runs with `--dry-run` until someone removes it.
- Tests: `test_inbox.py` (22), `test_extract.py` (23),
  `test_read_inbox.py` (16). 389 in total, all passing.

Decisions made this session:

- Separate entry point in `src/`, not `scripts/`, so it runs as a module
  like the orchestrator and the tests import it without a path hack.
- Cleaner Visits also gets a `Raw excerpt` field. §4 listed only `Source
  message`, but §5 requires every proposal to carry its sentence.
- Airtable is only touched when there is landlord mail in the window, so
  most days cost zero API calls. A day with mail costs 2 reads plus at most
  2 writes.
- The whole run writes nothing if any model call fails, so a partial
  outage cannot leave half an email proposed.
- Messages are never marked read. The reader tracks what it has seen
  through Message-IDs in Airtable, not mailbox flags.

Known limits:

- An email that yields no items leaves no Message-ID behind, so it is sent
  to the model again on each daily run while inside the 7-day window. At
  most seven calls, a cent or two each. Recording seen-but-empty messages
  would cost an extra table and extra API calls.
- A permanently broken API key exits 0 every day, per the spec's "fails
  quiet". Only the Actions log shows it.
- L1 and the live half of L2 need a real email from the landlord.

Still open, from PRD-v1.1 §6: cadence (built as daily), who gets the
summary email (built as the account owner only), and whether confirmed
Requests appear in the digest (not built; the digest is untouched).

Handoff: before going live, run `scripts/setup_inbox_tables.py --dry-run`
then for real, trigger the workflow by hand and read its log, then remove
`--dry-run` from the workflow. Local runs need Python 3.10+ for the SDK;
the Mac's system Python is 3.9.

## 19 — Landlord email reader goes live (Sept 25, 2026)

The reader from entry 18 now runs for real. The workflow no longer passes
`--dry-run`, so from the next daily run it writes unconfirmed proposals
whenever Pat's mail arrives. Nothing it writes is ever Confirmed.

Done this session:

- Ran `scripts/setup_inbox_tables.py`, first with `--dry-run` and then for
  real, using a separate one-time token with the schema scopes. It added
  `Source message` and `Raw excerpt` to Cleaner Visits and created the
  Requests table. A second dry run found all three in place. The scheduler's
  token and its GitHub secret were not touched.
- Triggered "Landlord inbox reader" by hand (run 36075609700), still in dry
  run. It passed and logged "No mail from the landlord in the lookback
  window. Nothing to do." A login failure exits through its own error, so
  that line proves the Gmail login and the allowlisted search work from
  Actions.
- Checked the Sept 24 scheduler failure. It was a 429 from Airtable, the
  Free plan limit. The manual re-run at 23:11 UTC passed, so the Team
  upgrade has taken effect.
- Removed `--dry-run` from `read_inbox.yml` and updated its header comment
  and the PRD-v1.1 status line.
- Corrected the entry 18 heading, which said "not committed".

Still open:

- L1 and the live half of L2 need the first real email from Pat. Expect it
  to differ from the test fixtures, and read that day's Actions log.
- Before Team renews (around Oct 24): move the chores base to its own free
  workspace, update AIRTABLE_BASE_ID and the "What Done Means" shared-view
  link, then cancel Team. Not yet confirmed that the Free plan allows a
  second workspace.
- PRD-v1.1 §6 decisions still run on their defaults: daily cadence, summary
  email to the account owner only when something is new, and no confirmed
  Requests in the digest.
- Delete the one-time schema token in Airtable and the local `.env`.

## 20 — Scheduler runs at 07:17 UTC instead of 12:00 (Sept 25, 2026)

GitHub started every scheduled run 4 to 4.25 hours late (10:54 to 11:17
Chicago on Sept 23-25), which would put week 1's assignments and the first
digest after the 08:00 Monday deadline in SC1. The cron moved to
`17 7 * * 1-5`: 02:17 Chicago until Nov 1, 01:17 after, and still Monday
either way. The odd minute avoids the top-of-the-hour rush.

Deliberately not done: a second backup cron. The digest has no record of
having been sent, so any second Monday run emails all three roommates
again. A sent-already guard would make a backup run safe; it is a change to
the digest and needs somewhere in Airtable to record the send, so it was
left for later.

Side effect: weekday nudges also go out around 2am Chicago instead of 11am.

Also corrected the README, which said extra runs were always harmless, the
reader workflow comment about running an hour after the scheduler, and
added the delay to the CLAUDE.md mistakes log.

To check: on Monday Sept 28, `gh run list --workflow scheduler.yml` should
show a start well before 13:00 UTC, and the log should say "wrote N
assignments for week 1" and "Digest: sent".
