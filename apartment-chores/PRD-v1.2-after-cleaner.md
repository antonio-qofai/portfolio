# PRD v1.2 - Chores placed after the cleaner

**Status:** built and live Sept 28 2026. See CHANGELOG entries 24 and 25
**Date:** September 28, 2026
**Deadline:** live before the week 4 run, Monday Oct 19 2026, 07:17 UTC
**Depends on:** v1 (`PRD.md`) and the Cleaner Visits table

Read `PRD.md` §5.5 and `CLAUDE.md` before building. This document covers only
what v1.2 adds.

---

## 1. Goal

Bathroom clean was weekly. That is more than the bathroom needs, because
Maria deep-cleans it about once a month. The house decided on Sept 28 2026
that each roommate cleans it once a quarter, and that each clean should fall
as far from her visits as possible, so it lands when the bathroom actually
needs it.

v1.2 adds a cadence that places a chore a fixed time after a confirmed cleaner
visit, instead of on a fixed rotation.

## 2. The rule

A chore with cadence `after_cleaner` occurs once per person per term. Each
turn lands in the active week containing the confirmed visit date plus a
delay (21 days, from config), and never more than a cap (4 weeks, from
config) after the previous clean. Turns follow the usual rotation order.

Only confirmed Cleaner Visits rows count, as everywhere else. A proposed visit
from the inbox reader places nothing until a human ticks `Confirmed`.

Nothing in the rule names the bathroom. Any chore row set to `after_cleaner`
behaves this way, and the delay and cap live in config.

## 3. Placement, run by run

Placement is decided only for the week being generated, on its Monday, and
never revisits a week already written. For week W:

1. Turns taken: the assignments of this chore anywhere in this term's
   Assignments. If every person has had a turn, stop. (Week 1's Bathroom
   clean, generated Sept 28 under the old cadence, was first counted as turn
   0. It was deleted by hand on Sept 29 because the bathroom was clean at
   move-in, so Autumn 2026 starts with no turns taken. See CHANGELOG 26.)
2. Visit targets: for each confirmed visit in the term, add the delay to its
   date and take the active week containing that day. If that week is inactive,
   roll forward to the next active week. If there is none, the visit gives no
   target.
3. Drop a target if another confirmed visit falls inside that target week.
   Maria is there that week, so the turn waits for the target of that later
   visit instead.
4. Cap: find the most recent clean, either a roommate turn before W or a
   confirmed visit before or in W (§9). If W is the last active week still
   within the cap of it, W gets a turn. A visit in W itself cancels the
   forced turn, because the cleaner does the work that week.
5. End of term: if the number of people still owed is at least the number of
   active weeks left, counting W, then W gets a turn. This is what places the
   last turn when the cap would reach past the end of the term.
6. W gets a turn if any of steps 3 to 5 says so. Never more than one turn per
   week per chore.

## 4. Who gets the turn

Fixed order, the same rotation formula as every other chore:
`roster[(seed + k) % len(roster)]`, where k is the number of turns already
taken this term. With Bathroom clean's seed of 0, that is the owner, then
Blake, then Casey.

## 5. Due date and cleaner weeks

Ordinary due date, Sunday 20:00 Central. A visit-driven turn can never share a
week with a confirmed visit, because step 3 prevents it, and neither can a cap
turn, because step 4 prevents it. Only an end-of-term turn can. In that case the chore's `convert_to_prep` behaviour applies as it
does today, with the same assignee, prep text, and a due time of 11:00 on the
visit date. The turn still counts.

## 6. Worked example (Autumn 2026)

Assumes Maria comes Thursday Oct 15 and then not again before December, and
her visit counts as a clean for the cap (§9).

| Step | Result |
|---|---|
| Weeks 1 and 2 | No turn. The cap counts from just before week 1 |
| Visit Oct 15 | In week 3. It resets the cap, so the next turn is due by week 7 |
| Visit plus 21 days | Nov 5, in week 6 (Nov 2 to 8) |
| Week 6 turn | The owner. His week goes from 3 chores to 4 |
| Cap from week 6 | Would be the fourth active week after it, which is past the term (weeks 7, 8, 10 are all that is left) |
| Week 8 turn | Blake, from the end-of-term rule (2 owed, 2 weeks left). His week goes from 1 chore to 2 |
| Week 10 turn | Casey, from the end-of-term rule (1 owed, 1 week left). His week goes from 1 chore to 2 |

If a second visit is confirmed for Nov 12, its target is Dec 3, also in week
10, so the result is the same.

With no visits at all, the cap gives the owner week 4 and Blake week 8, and
the end-of-term rule gives Casey week 10. No one has more than 3 chores in any
week.

This example was recomputed Sept 29 after week 1's turn was deleted. With week
1 counted as the owner's turn, the Oct 15 case put Blake in week 6 and no one
above 3 chores.

## 7. Data and config changes

| Where | Change |
|---|---|
| Airtable, Chores.Cadence | Add single-select option `after_cleaner`. Update `scripts/setup_base.py` to match |
| Airtable, Bathroom clean | Cadence set to `after_cleaner`. Seed stays 0 and sets the order. Offset is ignored |
| `config/cadences.py` | The delay (21 days) and the cap (4 weeks) for `after_cleaner` |
| `src/rotation.py` | `validate_plan` treats `after_cleaner` as roster-size occurrences by construction |
| New pure module or function | Placement from §3 and §4. Inputs are the week, term, roster, confirmed visits, and this term's existing assignments. No I/O |
| `src/orchestrator.py` | Passes confirmed visits and existing assignments into placement |

## 8. Invariants after v1.2

- Totals for Autumn 2026 are 5 weekly × 9 = 45, 3 `every_3` × 3 = 9, and 3
  `after_cleaner` turns, for 57 in all and exactly 19 each. Update CLAUDE.md in
  the same change.
- Each person does each `after_cleaner` chore exactly once per term, in
  rotation order.
- Generate forward. A visit confirmed, moved, or unconfirmed later can change
  weeks not yet generated, never a written one.
- Re-running week W is still a no-op.

## 9. Decisions

Settled Sept 28 2026:

- Cap of 4 weeks between bathroom cleans.
- Fixed rotation order, not lightest load.
- A confirmed Maria visit counts as a clean and resets the cap. Without this,
  three roommate turns cannot cover the quarter within a 4-week cap.
- The cap counts active weeks. Inactive weeks (9 and 11) do not use it up, so
  from a clean in week 5 the next is due by week 10.
- A confirmed visit in the week the cap would force cancels that turn
  (decided Sept 28 2026, after the build). Before this, the turn became prep
  and used up the person's clean.

## 10. Tests

- Visit plus 21 days lands in the right active week, including across the
  November daylight saving change.
- A target in inactive week 9 rolls to week 10. A target in week 11, or past
  the term, gives nothing.
- A target week that holds another confirmed visit is skipped.
- Unconfirmed visits place nothing.
- The cap forces a turn with no visits, and never leaves more than 4 weeks
  between cleans.
- A confirmed visit in the cap week cancels the forced turn. An unconfirmed
  one does not.
- Turns follow roster order from the seed. Turns already written this term
  count as taken.
- Never two turns of the same chore in one week.
- Full term: 57 assignments, 19 each, one bathroom turn each.
- Re-running an existing week is a no-op. A visit added later leaves written
  weeks untouched.
- `grep` finds no chore, person, or room name in the new code (SC7).
