# apartment-chores

Runs the chores in my three-person apartment. It assigns every shared chore on a rotation that
comes out exactly even over the quarter, emails a weekly digest, privately nudges whoever is
overdue, and reads the landlord's emails for cleaner visits. It has run on GitHub Actions since
September 2026.

This is a sanitized copy. Roommates and the landlord are renamed, and the README is written for
this public copy. The code is unchanged.

## The scheduler

Every chore, person, rule and cleaner visit is an Airtable row. The code holds no chore, name,
room or date, so pointing it at a different apartment is a data edit.

The rotation is one line of arithmetic, `roster[(seed + occurrence) % len(roster)]`, and the
interesting part is the check around it. Over 9 active weeks with 3 people, a weekly chore
occurs 9 times and an every-third-week chore occurs 3 times, so both divide evenly and everyone
does everything the same number of times. A cadence that does not divide by the roster size,
such as biweekly (5 occurrences), is rejected when the term is generated instead of rounded
off. The dry run prints the term's totals, and the current chore list must produce 57
assignments, 19 each.

Other rules the code enforces:

- Weeks are generated forward and never rewritten, so adding a chore mid-term changes only
  future weeks. Re-running a week that exists is a no-op.
- In a confirmed cleaner week, chores the cleaner covers turn into prep tasks with the same
  assignee, due before she arrives. They still count toward the quarter, so the totals hold.
- A nudge goes only to the person who is overdue, with one follow-up 48 hours later and never
  a third. The group digest never says who is behind.
- Airtable is edited by hand, so every row is validated on read. A bad row fails the run with
  its record ID and field name rather than being skipped.

## The landlord email reader

The landlord emails about cleaner visits, requests and nothing in particular. A second
workflow reads that mail and proposes what it finds. It is the only model call in the repo
(`src/extract.py`), and the model's answer is not trusted.

1. IMAP fetches unseen mail, only from an allowlisted sender address.
2. Claude Sonnet 5 returns candidate items through structured output, so the shape is
   guaranteed. Everything else is checked in plain code.
3. Each item must quote a sentence that actually appears in the email. An item whose quote
   cannot be found is dropped as invented.
4. A proposed date must fall between the send date and a set horizon. If the quoted sentence
   names a weekday or a day of the month, the date has to match it, because "Thursday the
   16th" read as a Friday would move the wrong week's chores.
5. A cleaner visit whose date fails a check is not dropped. It becomes an undated request, so
   a person still sees that a visit was mentioned and fills in the day.
6. Every proposal is written unconfirmed, and the writer raises if it is ever asked to set the
   Confirmed field. Only a roommate's tick changes the schedule, so an email that says "cancel
   all the chores this week" produces a row a person reads and deletes, not an action.

A model or mailbox failure changes nothing and exits cleanly. The reader imports nothing from
the scheduler, so it cannot stop the Monday digest.

## Code

    src/rotation.py       the rotation arithmetic and the divisibility check
    src/schedule.py       builds a week: active weeks, cleaner conversions, due dates
    src/placement.py      places the after-cleaner chore relative to confirmed visits
    src/orchestrator.py   the weekday run: generate, digest on Mondays, nudge
    src/nudge.py          overdue reminders and the 48-hour follow-up
    src/digest.py         the Monday email
    src/extract.py        the landlord reader's model call and every check on its answer
    src/proposals.py      writes unconfirmed proposals, refuses to confirm
    src/read_inbox.py     the reader's entry point
    config/               term calendar, cadences, field names and email copy, no secrets

437 tests, standard library `unittest`, no network:

    python3 -m unittest discover -s tests -t .

## Running it

    python3 -m src.orchestrator --dry-run    # print the term totals, the week and the emails
    python3 -m src.read_inbox --dry-run      # print what the reader would propose

Both need an Airtable base (`scripts/setup_base.py` builds the tables) and its credentials.
The reader also needs `ANTHROPIC_API_KEY` and a mailbox. In production both run from
`.github/workflows/` with credentials in Actions secrets.

Python, Airtable API, Gmail SMTP and IMAP, Claude API (Sonnet 5), GitHub Actions.
