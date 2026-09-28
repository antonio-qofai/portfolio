# PRD — Apartment Chore Agent

**Version:** v1.3
**Date:** September 20, 2026, updated September 28, 2026
**Changed in v1.1:** cleaner handling made event-driven with her real scope and
prep list; per-chore prep tasks added; completion logging simplified to the
Airtable app
**Changed in v1.3 (Sept 28, 2026):** chore list, totals, and success criteria
brought in line with the live base. Dust, Supply run, and Bathroom deep were
dropped on Sept 22. Bathroom clean moved from weekly to the `after_cleaner`
cadence on Sept 28 (`PRD-v1.2-after-cleaner.md`)
**Apartment:** 123 Example St, Chicago, IL
**Household:** 3 roommates, move-in Saturday September 26, 2026
**Scope period:** UChicago Autumn Quarter 2026 — Monday Sept 28 through Friday Dec 11

---

## 1. Goal

A system that assigns, tracks, and reminds on shared apartment chores so that the
common areas stay clean without anyone having to organize it, chase anyone, or
argue about fairness.

The fairness requirement is specific and is the core design constraint:

> Over the quarter, every roommate does every rotating chore the same number of
> times. Not preference-weighted, not points-based. Pure rotation.

The system is explicitly **not** an enforcement tool. It assigns, reminds, and
makes state visible. Humans do the enforcing.

### Why this is buildable rather than just a spreadsheet

Three things have to happen on a schedule without anyone remembering to do them:
assignments get generated, a digest gets sent, and overdue chores get nudged.
That is the automation. Everything else is storage and display.

---

## 2. The quarter arithmetic

This drives the whole design and should not be changed without redoing the math.

Autumn Quarter 2026 runs Mon Sept 28 – Fri Dec 11 (quarter formally ends Sat Dec 12).
That is **11 calendar weeks**, which is not divisible by 3 and would make even
rotation impossible.

Two of those weeks are not real chore weeks:

| Week | Dates | Status |
|---|---|---|
| 1 | Sep 28 – Oct 4 | Active |
| 2 | Oct 5 – 11 | Active |
| 3 | Oct 12 – 18 | Active |
| 4 | Oct 19 – 25 | Active |
| 5 | Oct 26 – Nov 1 | Active |
| 6 | Nov 2 – 8 | Active |
| 7 | Nov 9 – 15 | Active |
| 8 | Nov 16 – 22 | Active |
| 9 | Nov 23 – 29 | **Inactive** — Thanksgiving Break (Nov 23–27) |
| 10 | Nov 30 – Dec 6 | Active |
| 11 | Dec 7 – 13 | **Inactive** — Reading period Dec 5–7, finals Dec 8–11 |

**9 active weeks. 9 ÷ 3 = 3.** Every weekly chore is done exactly 3 times by each
person. No remainders, nothing to argue about.

Weeks 9 and 11 get one-off shutdown tasks instead of the rotation (§4.3).

### Cadences

Only cadences whose occurrence count divides by 3 are permitted:

| Cadence | Occurrences | Per person |
|---|---|---|
| `weekly` | 9 | exactly 3 |
| `every_3` (every 3rd active week) | 3 | exactly 1 |
| `after_cleaner` (placed around confirmed cleaner visits) | 3 | exactly 1 |

`after_cleaner` gives one turn per person by construction, so it always
divides. Its weeks come from Cleaner Visits rather than an offset: 21 days
after a confirmed visit, never more than 4 active weeks after the last clean,
with a visit counting as a clean. The full rule is in
`PRD-v1.2-after-cleaner.md`.

**Biweekly is forbidden.** Over 9 active weeks it yields 5 occurrences and breaks
evenness. If a chore feels like it needs biweekly, make it weekly or `every_3`.

### Expected totals

- 5 weekly chores × 9 weeks = 45 assignments
- 3 `every_3` chores × 3 occurrences = 9 assignments
- 1 `after_cleaner` chore × 3 turns = 3 assignments
- **Total 57, exactly 19 per person**

History: 72 and 24 each at scoping, 63 and 21 after Sept 22, 57 and 19 since
Sept 28. The numbers change with the chore list. The rule that every chore's
occurrence count divides by the roster size does not.

With 5 weekly chores and 3 people, each week one person has 1 weekly chore and
the other two have 2. One `every_3` chore lands each week, and the bathroom
turn lands in 3 weeks of the quarter, so a week is uneven by a chore or so
while the quarter is exactly even.

---

## 3. Success criteria

These are the bar the build is judged against. Each is verifiable.

| # | Criterion | How it's verified |
|---|---|---|
| SC1 | By 8:00 CT Monday of each active week, the week's assignments exist in Airtable: 5 weekly, plus whichever `every_3` chore is due, plus the `after_cleaner` turn if it lands that week. No chore unassigned. | Query Assignments for that week |
| SC2 | At quarter end, each roommate has exactly 19 assignments; each weekly chore appears exactly 3× per person, each `every_3` and `after_cleaner` chore exactly 1× per person | Group-by query on Assignments |
| SC3 | Monday digest email delivered to all 3 addresses on all 9 active weeks | Send log / inbox check |
| SC4 | A completion can be logged from a phone in under 10 seconds, without logging in | Time it with a stopwatch, on a roommate's phone, not yours |
| SC5 | A chore open past its due date generates exactly one nudge to its assignee, and at most one follow-up. Never nudges anyone else about it. | Inspect nudge log for one overdue case |
| SC6 | Adding a chore is one new Airtable row. The next run picks it up with zero code change. | Add a throwaway chore mid-quarter, confirm it appears |
| SC7 | Zero chores, people, or rooms hardcoded in source | `grep` the repo for chore names, roommate names, room names — must return nothing outside config/tests |
| SC8 | In a confirmed cleaner week, chores flagged `convert_to_prep` swap their task text but keep the same assignee and still count toward quarter totals | Set a test cleaner date, inspect generated week |
| SC9 | Every digest includes the current active House Rules, pulled from Airtable | Read any digest |
| SC10 | Runs the full quarter with no manual intervention other than editing Airtable | No unscheduled commits during the quarter |

### Non-goals

Stated explicitly because each was considered and deliberately rejected:

- **No verification.** No photo proof, no approval flow, no "did they really do it."
- **No reporting on other people.** Nobody can flag a roommate. Self-reporting only.
  The moment the system can be used against someone it becomes a grievance log.
- **No penalties, points, or scores.**
- **Bedrooms out of scope** entirely.
- **Laundry, personal dishes, and personal boxes out of scope** — those are
  individually owned, not shared.
- **No landlord email parsing in v1** (see §7).

---

## 4. Variables

### 4.1 Inputs

| Input | Source | Changes |
|---|---|---|
| Roster (names, emails, active) | Airtable | Rarely |
| Chore definitions (name, area, cadence, offset, seed, definition of done, cleaner behavior) | Airtable | Occasionally, by hand |
| House Rules | Airtable | Rarely |
| Cleaner Visits (date + time) | Airtable, entered by hand when the landlord's email arrives | ~Monthly, unpredictable |
| Completions | `Assignments.Status` toggled in the Airtable app | Constantly |
| Override Log | Airtable app | Constantly |
| Current date | System clock, `America/Chicago` | — |
| Academic calendar constants | Config file | Once per quarter |

### 4.2 Outputs

| Output | Destination |
|---|---|
| Assignment records for the coming week | Airtable |
| Monday digest email | All 3 roommates |
| Overdue nudge email | The one assignee only |
| `CURRENT.md` (optional pull view) | Repo |

### 4.3 The chore list

The live base as of Sept 28, 2026. Airtable holds the full definition of done
for each chore. These are summaries.

**Weekly, 5 chores, 3× each per person per quarter**

| # | Chore | Area | Seed | Definition of done | Cleaner behavior | Prep task |
|---|---|---|---|---|---|---|
| 1 | Dishwasher duty | Kitchen | 0 | Own the dishwasher for the week. Run it when full, empty it when clean, however many cycles that takes. Not hand-washing anyone's dishes | `normal` | none |
| 2 | Kitchen surfaces | Kitchen | 1 | Counters and stovetop cleared and wiped, microwave door and handle, sink basin rinsed | `convert_to_prep` | Clear the counters and stovetop, run or empty the dishwasher, sink empty. Take the garbage and recycling down |
| 3 | Bathroom restock & bin | Bathroom | 1 | TP and hand soap checked and replaced, bin out | `normal` | none |
| 4 | Floors — high traffic | Kitchen / Bath / Entry | 2 | Pick up, sweep, wet Swiffer if it needs it | `convert_to_prep` | Pick up everything off the kitchen, bathroom, and entry floors, and clear the bathroom counter |
| 5 | Common reset | Living / Dining | 2 | Dining table clear, living room tidy, entryway free of shoes and bags | `normal` | none |

**Every 3 active weeks, 3 chores, 1× each per person per quarter**

| # | Chore | Area | Offset | Seed | Definition of done | Cleaner behavior | Prep task |
|---|---|---|---|---|---|---|---|
| 6 | Floors — low traffic | Living / Dining | 0 | 0 | Pick up, sweep including under the couch and table, wet Swiffer if it needs it | `convert_to_prep` | Pick up everything off the living and dining floors |
| 7 | Fridge purge | Kitchen | 1 | 1 | Toss the expired and abandoned, wipe one shelf, rotating shelves over the quarter | `normal` | none |
| 8 | Appliance care | Kitchen | 2 | 2 | Microwave inside, oven wipe, descale kettle and coffee maker, run the disposal clear | `normal` | none |

Offset 0 lands on active weeks 1/4/7 → calendar weeks 1, 4, 7.
Offset 1 lands on active weeks 2/5/8 → calendar weeks 2, 5, 8.
Offset 2 lands on active weeks 3/6/9 → calendar weeks 3, 6, 10.

One `every_3` chore per offset, so exactly one lands each week.

**After the cleaner, 1 chore, 1× each per person per quarter**

| # | Chore | Area | Seed | Definition of done | Cleaner behavior | Prep task |
|---|---|---|---|---|---|---|
| 9 | Bathroom clean | Bathroom | 0 | Toilet, shower, sink and taps, mirror. Bath mat and hand towels in the wash. Grout, drains, and behind the toilet are Maria's | `convert_to_prep` | Clear the bathroom counter and floor, nothing left on surfaces |

Placed per `PRD-v1.2-after-cleaner.md`, in rotation order from the seed:
The owner (week 1, generated before the change and counted as his turn), then
Blake, then Casey.

**Dropped from the scoping list.** Dust, Supply run, and Bathroom deep were
removed on Sept 22, 2026 once the cleaner's real scope was known: she dusts and
does the bathroom deep work, and supplies are covered by the restock chore and
the group-chat rule. Bathroom clean was weekly until Sept 28, 2026.

**One-offs — not part of the rotation, assigned individually**

| Task | When |
|---|---|
| Move-in setup — stock supplies, seed Airtable, home-screen shortcuts on all 3 phones | Sept 26–27 |
| Smoke + CO detector test, fresh batteries | Week 1 |
| Winter prep — salt, shovel, entry mat, radiator check | ~Week 5 (late Oct) |
| Thanksgiving shutdown — fridge cleared, trash out, heat down | Week 9 |
| Winter break shutdown — full purge, trash, thermostat, windows latched | Week 11 |

### 4.4 House Rules

Rules are never assigned and never completed. They are standing conditions,
included verbatim in every digest.

**Override rules** — whoever hits the condition handles it, regardless of whose
week it is. These exist specifically so "not my week" can't become an excuse.

1. Nothing sits in the sink. Wash it or load it.
2. Trash is full → you take it out.
3. Recycling is full → you take it out.
4. Your boxes, you break them down.
5. Last of the TP → you replace the roll, then add it to the supply list.
6. Take the last of anything shared → it goes on the supply list.
7. No food left out overnight.
8. Your guests, your cleanup.

**Standing rules**

9. Clean dishes in the dishwasher are communal to put away. "Your own dishes"
   covers dirty dishes only.
10. Quiet hours during reading period and finals (weeks 10–11). *Activate Nov 1.*

### 4.5 Override Log

A one-tap log for handling an override item — no chore, no assignment, just a tap
when you empty a bin or break down someone's box.

This exists because override rules resolve to *whoever notices first*, which is
reliably the person with the lowest mess tolerance. Over 9 weeks that can become
one person doing it 100 times and quietly resenting it.

**Review it in week 5 (late October).** If the split is roughly even, the override
model works. If it's badly lopsided, that item moves back into the weekly
rotation — with data behind the change instead of someone having to raise it as a
complaint.

---

## 5. Architecture

```
GitHub Actions (cron, weekday mornings, America/Chicago)
        │
        ▼
  Python scheduler
        │
        ├── reads  ──► Airtable API  (Roster, Chores, Rules, Cleaner Visits,
        │                             Completions, Override Log)
        ├── writes ──► Airtable API  (Assignments)
        └── sends  ──► Gmail SMTP    (digest + nudges → 3 UChicago addresses)

  Roommates ──► Airtable form view (home-screen shortcut) ──► Completions
           └──► Airtable app (optional) ──► Assignments.Status
```

### 5.1 Why Airtable

Considered and rejected: YAML config in the repo with completions via email reply
links. Rejected because logging has to be reachable **at the moment the chore is
finished** — standing in the bathroom on Wednesday night, not scrolling back to
Monday's email. A home-screen icon wins that test; an inbox does not.

The cost is losing free git history on chore edits. Airtable has revision history
and chores change rarely, so this is acceptable.

### 5.2 Why GitHub Actions

The scheduler must run whether or not a laptop is open. Actions is free, already
adjacent to the repo, and needs no server.

Note: GitHub cron is best-effort and can be delayed under load. Do not promise an
exact send time in the digest.

### 5.3 Rotation algorithm

For chore `c` with seed `s_c ∈ {0,1,2}` and 0-based occurrence index `k`:

```
assignee = roster[(s_c + k) % len(roster)]
```

Occurrence index is **per chore**, not calendar-derived, so each chore advances on
its own clock.

Seeds are staggered `[0,1,1,2,2]` across the 5 weekly chores, so every week one
person has 1 weekly chore and the others 2, and the `every_3` chores take seeds
0, 1, and 2 at offsets 0, 1, and 2.

`after_cleaner` chores use the same formula, with `k` counting turns taken this
term rather than weeks elapsed. Which week a turn lands in comes from
`PRD-v1.2-after-cleaner.md`, not from the calendar.

Because both 9 and 3 are divisible by 3, every chore lands exactly evenly with no
rebalancing pass required.

### 5.4 Generate forward, never rewrite

Assignments are materialized into their own table, not computed on read.
**Edits affect future weeks only.** Adding a chore on Wednesday means it enters
the rotation at the next generation run. This prevents history from being rewritten
under someone who already did the work.

### 5.5 Cleaner weeks

A cleaner (Maria) visits roughly once a month, somewhere around the middle of the
month. **The exact dates are not predictable and must not be predicted.**

The landlord (Pat) emails about a week ahead with the date and time, then sends a
reminder the day before. That lead time is enough to enter the visit by hand, so
the system is **purely event-driven**: it reacts to confirmed rows in Cleaner
Visits and never generates or guesses a schedule of its own.

- A row with `Confirmed` checked affects generation.
- A row with `Source = Projected` is planning scaffolding only and changes nothing.
- No confirmed row → the week generates normally. A missed visit degrades to an
  ordinary week, which is the correct failure mode.

**Her scope**, per the landlord: kitchen, bathroom, floors, dusting blinds and
baseboards, occasionally windows. Takes 3.5–4 hours. She does **not** do dishes,
laundry, beds, or any bedroom. Bedrooms were already out of scope.

**Prep, per the landlord:** dishes done, garbage and recycling taken down, and as
much put away as possible so she can get straight to work.

In a confirmed cleaner week, chores flagged `convert_to_prep` **keep the same
assignee and the same slot** but swap their task text to that chore's `Prep task`.

Cancelling instead would remove an occurrence and break the quarter arithmetic.
Conversion preserves the count, lightens the work, and gets more out of the visit.
Chores whose work she does not cover keep `normal` behavior.

**Due dates shift.** In a cleaner week, converted chores are due at **11:00 on the
morning of the visit**, derived from the Cleaner Visits row — not the usual
end-of-week. Prep completed after she arrives is worthless. Do not hardcode a day
of the week; read the date off the row.

**Visits also place `after_cleaner` chores.** A confirmed visit sets when the
next Bathroom clean turn falls (21 days later) and resets the 4-week cap. A
visit in the week the cap would force a turn cancels that turn, since she does
the bathroom that week. Projected rows place nothing, as above. See
`PRD-v1.2-after-cleaner.md`.

**A visit landing in an inactive week** (9 or 11) has no rotation to convert. Prep
still has to happen, so fold it into that week's one-off task. For week 11 this is
natural — the winter-break shutdown already clears the fridge and takes out trash.

### 5.6 Logging completions

All three roommates are installing the Airtable app, so completion is a `Status`
toggle on your own row in the *My open* view. No form, no reconciliation step, no
separate Completions table.

If someone later stops using the app, add a form view writing to an append-only
Completions table and match on person + chore + week. Not needed for v1.

### 5.7 Nudges

- Chore open past its due date → one nudge to that assignee only.
- Still open 48h later → one final nudge.
- Never a third. Never to the group. Never naming anyone in the group digest.

Dishwasher duty gets a faster first nudge than anything else, because House Rule 1
("nothing sits in the sink") is physically impossible to follow when the dishwasher
is full of clean dishes. Its failure cascades into a rule everyone agreed to.

### 5.8 Input validation

Airtable is hand-edited, so it will eventually contain a typo. **Validate on read
and fail loudly.** A run that silently skips a malformed row will go unnoticed for
two weeks.

Use single-select fields for Area, Cadence, and Cleaner behavior. Free text is
only for names and definitions of done.

---

## 6. Constraints and dependencies

| Dependency | Status | Note |
|---|---|---|
| Airtable account + API key + base ID | To set up | Free tier: 1,000 records/base. Expected usage ~57 assignments a quarter — comfortable |
| Gmail account for sending | To set up | **Must be a personal Gmail, not Workspace.** App passwords are unavailable on Workspace accounts, so the UChicago address cannot be the sender. Create a throwaway personal Gmail, enable 2FA, generate an app password, store as an Actions secret |
| GitHub repo + Actions | To set up | Free |
| Roommate email addresses | Resolved | Blake `roommate-b@example.edu`, Casey `roommate-c@example.edu`, plus your own |
| Roommate buy-in on the rules | **Pending** | Have the conversation before Sept 28, not after |
| Cleaner's scope | Resolved | Kitchen, bathroom, floors, blinds and baseboards, occasionally windows. Not dishes, laundry, beds, or bedrooms |
| Cleaner's visit dates | Ongoing | Not predictable. Entered by hand when the landlord emails, roughly a week ahead |
| Timezone handling | Design note | Actions runs UTC; all logic must convert to `America/Chicago` |

### Secrets required

`AIRTABLE_API_KEY`, `AIRTABLE_BASE_ID`, `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`

All in GitHub Actions secrets. None in the repo.

---

## 7. Deliberately deferred

**Landlord email parsing.** The cleaner's date arrives by email each month.
Auto-parsing it would be the system's most fragile dependency, and an email that
silently reshapes the schedule is painful to debug. v1: type the date into
Airtable manually — ten seconds a month. v1.1: parse and *propose* a date for
confirmation, never write it directly.

**LLM layer.** As specced, this system is ~95% deterministic script. Rotation is
arithmetic, nudges are date comparisons, the digest is a template. That is the
right call for something that must work every Monday for eleven weeks.

If an agent layer is wanted later, the honest places it earns its keep are:

- Answering "when did we last do the fridge?" against the completion log
- Rebalancing when someone is away for a week
- Writing nudges that vary instead of repeating the same sentence

These are additions on a working deterministic core, not the core itself. Scope
them as v1.1 with their own success criteria.

---

## 8. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| **Logging goes stale by week 3** | Highest — kills the project | Home-screen shortcut on all 3 phones during move-in setup. No login. Keep the digest short |
| Digest lands in spam/Promotions | High — silent failure | All 3 mark "not spam" and add the sender to contacts on day one. Check inbox placement in week 1 |
| Override load falls on one person | Medium | Override Log + week 5 review (§4.5) |
| App password revoked or account password changed | Medium | Don't touch the bot Gmail once working. Document regeneration steps in README |
| GitHub cron delay | Low | Don't promise exact times |
| Bad hand-edit in Airtable | Low | Validate on read, fail loudly, single-selects |
| Nudges read as nagging | Medium | Hard cap at two, never to the group, never naming anyone publicly |

---

## 9. Build plan

Move-in is Sept 26; the quarter starts Sept 28. That is tight.

**Ship a deliberately dumb v0 for move-in** — assignments generated and a digest
sent, nothing clever. Then build the real thing once there are two weeks of lived
data about where mess actually accumulates.

| Phase | Dates | Output |
|---|---|---|
| Scoping | Sept 20–22 | This PRD. Roommate conversation on the rules. Airtable base built and seeded |
| v0 build | Sept 23–25 | Generation + digest working end to end. Manual trigger acceptable |
| Move-in | Sept 26–27 | Supplies stocked, shortcuts on all 3 phones, week 1 seeded, test digest sent |
| v1 | Sept 28 – Oct 11 | Actions cron live, nudges, cleaner-week conversion, validation |
| Review | Week 5, late Oct | Override Log check. Chore list adjusted against reality |
| v1.1 | Optional, Nov | LLM layer if wanted |

---

## 10. Repo files

| File | Purpose |
|---|---|
| `CLAUDE.md` | Standing rules: never hardcode chores/people/rooms, ask before pushing, ask questions instead of assuming, keep it reusable for any apartment |
| `README.md` | How a stranger runs this in five minutes — setup, secrets, how to trigger |
| `PRD.md` | This document |
| `PRD-v1.1-landlord-reader.md` | Spec for the landlord email reader |
| `PRD-v1.2-after-cleaner.md` | Spec for the `after_cleaner` cadence |
| `CHANGELOG.md` | One entry per push. This PRD is entry 1 |
| `INDEX.md` | One line per file |

---

## 11. Open questions

Carry these into v1; none blocks the build starting.

1. **Your own name and email** for the roster. Blake and Casey are set.
2. **Roommate buy-in on the House Rules** — the override rules only work if all
   three agreed to them. Do this before Sept 28, not after the first dispute.
3. **Overlap with the cleaner.** Partly resolved. Dust and Bathroom deep were
   dropped on Sept 22, and Bathroom clean moved to `after_cleaner` on Sept 28.
   Floors low traffic still overlaps her floor work. Revisit at the week 5 review
   with real data.
4. **LLM layer** — decide by early November whether v1.1 happens.
5. **Reusability target** — this is specced for 3 people and 9 active weeks. The
   rotation math generalizes to any roster size, but *perfect* evenness requires
   active weeks divisible by roster size. Decide whether v1 should handle the
   uneven case gracefully (assign remainders to whoever is lightest overall) or
   simply report that the quarter can't divide evenly.
6. **Cleaner visit in an inactive week.** Handled by folding prep into that week's
   one-off (§5.5). Confirm this reads sensibly the first time it actually happens.
