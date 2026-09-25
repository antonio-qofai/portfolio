# Apartment Chore Agent

Assigns shared apartment chores to a roster of roommates on a fixed rotation,
emails a weekly digest, and privately nudges whoever is past due. State lives
in Airtable. It runs itself on GitHub Actions.

Nothing about a particular apartment is in the code. The chores, the people,
the rules, and the cleaner's visits are all Airtable rows; the term dates are
in `config/calendar.py`. Pointing this at a different apartment is a data
edit, not a code change.

`PRD.md` is the spec. `CLAUDE.md` is the standing rules for working in the
repo. `INDEX.md` is one line per file.

---

## Setup in five minutes

### 1. Get the code

```
git clone <your fork> && cd chores
pip install requests
```

Python 3.11 or newer. `requests` is the only dependency.

### 2. Make an Airtable base

Create an empty base and note its ID (the `app...` string in the URL).
Create a personal access token with `schema.bases:read`,
`schema.bases:write`, `data.records:read`, and `data.records:write` on it.

Build the **Roster** table by hand first, with these four fields:

| Field | Type |
|---|---|
| Name | Single line text |
| Email | Email |
| Active | Checkbox |
| Sort order | Number, integer — this is rotation position |

Add one row per roommate. Then let the script build the rest:

```
AIRTABLE_API_KEY=<token> AIRTABLE_BASE_ID=<base> python3 scripts/setup_base.py --dry-run
AIRTABLE_API_KEY=<token> AIRTABLE_BASE_ID=<base> python3 scripts/setup_base.py
```

It creates Chores, Rules, Cleaner Visits, and Assignments with the right
field types, and skips anything that already exists, so it is safe to
re-run.

Every table and field name it uses comes from `config/airtable_fields.py`. To
rename a field in Airtable, change the string there and nowhere else.

### 3. Seed the data

Enter these by hand in Airtable. `PRD.md` section 4.3 has a chore list and
section 4.4 has a set of house rules you can copy.

**Chores.** One row per chore. `Cadence` is `weekly` or `every_3`. `Seed` is
0, 1, or 2 and sets who does the first occurrence. `Offset` only applies to
`every_3` and picks which of the three weeks it lands on. `Cleaner behaviour`
is `normal` or `convert_to_prep`; anything set to `convert_to_prep` must also
have a `Prep task`.

Give two `every_3` chores that share an `Offset` two different `Seed` values.
They occur in the same weeks, so a shared seed drops both on one person and
makes that week lopsided. Quarter totals come out even either way, which is
why nothing catches this for you.

The arithmetic has to divide. Over 9 active weeks with 3 people, `weekly`
gives 9 occurrences and `every_3` gives 3 — both divide by 3, so everyone
does everything the same number of times. A cadence that does not divide by
the roster size is rejected at generation time rather than rounded off.

**Rules.** One row per rule, `Category` of `override` or `standing`. Leave
`Active from` blank unless the rule switches on midway through the term.

**Cleaner Visits.** Only when you actually have a date. A row with
`Confirmed` ticked moves that week's `convert_to_prep` chores to prep tasks
due at 11:00 on the visit date. An unticked row changes nothing.

### 4. Set up the phone views

Do this in the Airtable UI. The API cannot create views, so this is the one
part of the base that the setup script does not build for you.

This step decides whether the whole thing survives. Logging has to be
reachable in the moment a chore is finished, standing in the bathroom on a
Wednesday night. If it takes more than about ten seconds, people stop doing
it around week 3 and the data goes quiet.

On the **Assignments** table, make one view per person:

- Duplicate Grid view and name it for that person
- Filter: `Done` is unchecked, **and** `Assignee` has the person
- Sort: `Due` ascending
- Hide every field except `Task`, `Due`, and `Done`

Then, on each person's phone, install the Airtable app, open that person's
own view, and add it to the home screen. One tap to the right list, one tap
to tick something off.

A single shared view grouped by `Assignee` also works and is less to
maintain, but it costs a scroll every time. Per-person views are worth the
extra two minutes.

### 5. Make a sender account

Must be a **personal** Gmail, not a Workspace or university account —
Workspace accounts cannot generate app passwords. Turn on 2FA, then create an
app password and keep it. Once it works, do not touch that account.

### 6. Set four secrets

In the GitHub repo, under Settings → Secrets and variables → Actions:

```
AIRTABLE_API_KEY
AIRTABLE_BASE_ID
GMAIL_ADDRESS
GMAIL_APP_PASSWORD
```

None of these go in the repo.

### 7. Try it

```
export AIRTABLE_API_KEY=... AIRTABLE_BASE_ID=...
python3 -m src.orchestrator --dry-run
```

A dry run reads Airtable, prints the whole term's totals, prints the
assignments it would write and the emails it would send, and changes nothing.
It needs the Airtable secrets but not the Gmail ones.

Check the totals line. With 3 people it should read 72 assignments, 24 each.
If it does not, the seeded chores do not divide evenly and the digest is not
the thing to fix.

Then run the workflow by hand: Actions → Chore scheduler → Run workflow. It
is set to `--dry-run`. When the output looks right, delete ` --dry-run` from
the last line of `.github/workflows/scheduler.yml` and it goes live.

---

## Running it

```
python3 -m src.orchestrator              # generate and nudge
python3 -m src.orchestrator --generate   # write this week's assignments only
python3 -m src.orchestrator --nudge      # send overdue reminders only
python3 -m src.orchestrator --dry-run    # print everything, touch nothing
```

`--dry-run` works with any of them.

The workflow runs at 07:17 UTC Monday through Friday, which is 02:17 in
Chicago before Nov 1 and 01:17 after. It is that early because GitHub often
starts scheduled jobs hours late, and the week must exist by 08:00 Monday.
Generating a week that already exists is a no-op, but on Mondays every run
also sends the digest, so a second Monday run (by hand or on a second
schedule) sends it twice. On other days an extra run is harmless.

## What it does each run

**Generate.** Works out which week today is in, and writes that week's
assignments if they are not already there. Weeks that have already started
are never rewritten, so adding a chore mid-term affects future weeks only.

**Digest.** Mondays only, to everyone. The whole week's split plus the house
rules in force. It never says who is behind.

**Nudge.** One email to the assignee when a chore is past due, one follow-up
48 hours later, then nothing. Never to the group, never naming anyone
publicly. The two timestamps are stored on the assignment row itself, in
`First nudge sent` and `Followup sent`.

## Logging a chore as done

Tick `Done` on your own row in the Airtable app. That is the whole thing.
No form, no login each time, no reconciliation step.

## Report exporter (optional, runs on your own Mac)

`src/report.py` writes one person's open chores to a JSON file for another
program to read (the Life Dashboard reads it as `agent-reports/chores.json`).
It only reads Airtable: Roster and Assignments, two API calls per run.

1. Make a second Airtable personal access token with only
   `data.records:read`, on this base only. Do not reuse the scheduler's token.
2. Create `.env` in the repo root (gitignored):

   ```
   AIRTABLE_API_KEY=<the read-only token>
   AIRTABLE_BASE_ID=<the app... ID>
   CHORES_REPORT_EMAIL=<your email as it appears in Roster>
   ```

3. Try it: `uv run --no-project --python 3.12 --with requests python -m src.report --out /tmp/chores.json --dry-run`
4. Schedule it: `python3 scripts/report_launchd.py install --out <path to the report file>`.
   It runs at 05:45 daily (and on wake if the Mac slept through it) and logs
   to `~/Library/Logs/chores-report.log`. `status` and `uninstall` do what
   they say.

An item is a chore of yours that is not ticked done and is overdue or due in
the next 7 days (`--days-ahead` changes that). If a run fails, the file gets
`"status": "error"` so the reader shows an error rather than a stale list.

## Tests

```
python3 -m unittest discover -s tests -t .
```

Everything below the orchestrator is pure functions, so the rotation
arithmetic, the cleaner conversion, the digest, and the nudge rules are all
tested without a network.

## When it breaks

**The run fails with a record ID and a field name.** Someone hand-edited that
Airtable row into something invalid. Fix the row. Failing loudly is
deliberate: a run that quietly skipped a bad row would go unnoticed for weeks.

**"does not divide evenly among N people."** A chore's cadence produces a
number of occurrences that the roster size does not divide. Change the
cadence or the roster, not the code.

**The digest stops arriving.** Check the Actions log first. If the job ran
and the send failed, the app password was probably revoked — generate a new
one and update the `GMAIL_APP_PASSWORD` secret. If it landed in Promotions,
everyone should mark it not-spam and add the sender to contacts.

**A cleaner visit produced a warning about an inactive week.** There is no
rotation that week to convert. Fold the prep into that week's one-off task by
hand.
