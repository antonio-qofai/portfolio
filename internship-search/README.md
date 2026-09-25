# Internship Opportunity Agent

Watches 180 company job boards and 3 community aggregator feeds on a schedule, stores every
posting in SQLite, works out what is genuinely new and what has closed, filters out everything
that cannot apply to the owner, and emails a short digest. Built for the Summer 2027 recruiting
cycle.

`PRD.md` is the specification of record. `CLAUDE.md` holds the standing rules. `CHANGELOG.md`
is the dated log of what has changed. `NEXT_STEPS.md` is what to do next, and is the right
file to open first after a break.

## Run it

Every command runs from the repo root. Writing `.venv/bin/python` instead of `python` needs no
setup at all.

    .venv/bin/python -m agent.run                    # poll everything, send or print the digest
    .venv/bin/python -m agent.run --no-email         # poll, send nothing at all
    .venv/bin/python -m agent.run --no-digest        # poll, hold the digest, still send urgent
    .venv/bin/python -m agent.run --quiet            # poll without per-source output
    .venv/bin/python -m agent.run --skip-feeds       # company boards only, much faster
    .venv/bin/python -m agent.run --skip-triage      # poll and store only, no filtering, no cost
    .venv/bin/python -m tools.dashboard --open       # build the dashboard and open it
    .venv/bin/python -m tools.log_application --find anduril   # record that you applied
    .venv/bin/python -m tools.airtable_view_urls     # the URL of every Airtable view
    .venv/bin/python -m tools.airtable_coverage      # is anything good missing from the base
    .venv/bin/python -m tools.rescore --all --dry-run # re-judge postings after labelling
    .venv/bin/python -m tools.backlog_report         # everything open now, printed
    .venv/bin/python -m tools.prefilter_report       # what the filter kept and killed
    .venv/bin/python -m tools.rank_report            # what the ranker would do and what it decided
    .venv/bin/python -m tools.test_prefilter         # 122 checks on the filter rules, one second
    .venv/bin/python -m tools.test_airtable_slice    # 18 checks on who gets an Airtable row
    .venv/bin/python -m tools.test_feeds             # 23 checks on reading aggregator feeds
    .venv/bin/python -m tools.test_identity          # 9 checks on how postings are identified
    .venv/bin/python -m tools.test_digest            # 102 checks on the emails, the HTML and the budget
    .venv/bin/python -m tools.test_health            # 22 checks on the failure alert
    .venv/bin/python -m tools.test_actions           # 53 checks on your own to-do loop
    .venv/bin/python -m tools.test_workday           # 23 checks on the Workday fetcher
    .venv/bin/python -m tools.test_gcal              # 10 checks on calendar event dates
    .venv/bin/python -m tools.test_airtable_calls    # 19 checks on the Airtable call counter
    .venv/bin/python -m tools.reconcile_identity     # find postings stored on more than one row
    .venv/bin/python -m tools.verify_tokens          # prove every token in the map still works
    .venv/bin/python -m tools.probe_tokens cohere    # hunt one company across all three platforms
    .venv/bin/python -m tools.sync_calendar --dry-run
    .venv/bin/python -m tools.sync_airtable          # move labels and postings both ways
    .venv/bin/python -m tools.prep                   # preparation tasks, highest leverage first
    .venv/bin/python -m tools.health                 # is the agent alive right now
    .venv/bin/python -m tools.schedule --status      # is the agent actually scheduled

A first run on an empty database seeds it, so everything looks new exactly once. Every run
after that reports only real changes.

## The schedule

The agent is meant to run without being asked. Four times a day it polls every source and
syncs the calendar. One of those four runs sends the digest and the other three are
silent. Airtable syncs on the digest run only, once a day, because its free plan meters
API calls by the month rather than by the second; see the API call budget below.

    .venv/bin/python -m tools.schedule --dry-run     # print the plists, write nothing
    .venv/bin/python -m tools.schedule --install     # write them and start scheduling
    .venv/bin/python -m tools.schedule --status      # installed? loaded? when did it last run?
    .venv/bin/python -m tools.schedule --uninstall   # stop scheduling, delete the plists

`--install` writes two files into `~/Library/LaunchAgents` and loads them. That is the only
step that changes how the machine behaves. `--uninstall` reverses it completely and touches
nothing else: `state.db`, the logs and every posting stay exactly where they are.

The times, the labels, the log paths and the three steps a run consists of are all in
`sources/schedule.toml`. To move a run to a different hour, edit that file and run
`--install` again. Nothing about the schedule is in the Python.

### Why launchd and not cron

Cron fires on the clock and has no memory. A run scheduled for a time when the laptop was
asleep does not happen, is not logged, and raises no error, so on a machine that closes
overnight cron silently discards most of its runs. launchd runs a missed job once as soon
as the machine wakes. That converts a lost run into a late one, and a late run is a latency
problem while a lost run is a coverage problem. Coverage is the whole point of this system.

Neither scheduler can wake a sleeping laptop, so the honest description of the cadence is
"at the first opportunity after each scheduled time that the machine is awake". That is why
the hours are ones the lid is realistically open, and why there are four of them. Four
attempts finds nothing one attempt would miss, since a posting that appeared this morning is
still there tonight. What it buys is four chances for a run to land instead of one chance
whose failure costs a full day. The extra runs are free: polling is ordinary HTTP, and a
posting is intake-tagged once for its lifetime, so spending tracks new postings and not run
count.

### Why only one run emails

Email volume is a success criterion, at or under 10 messages a week, and it should not move
just because polling got more frequent. So the 18:10 run sends the digest and the other
three poll quietly. The exception is urgent mail, below, which fires from any run.

That creates one problem worth knowing about. A posting the 08:10 run finds is no longer new
by 18:10, so without help it would be filed correctly and never mentioned. The digest run
therefore also picks up anything surfaced in the last 36 hours that no email has carried
yet, and stamps each posting as it goes out so it is never sent twice. That window is
`args_when_digest` on the watcher step in `sources/schedule.toml`. It is deliberately much
shorter than the age of the seeded backlog, which is released by
`tools.backlog_report --mark-alerted` and is your decision, not the digest's.

### Reading the logs

Every scheduled run appends to `logs/run.log` with a local timestamp on every line,
including every line the three steps print. `logs/` is gitignored.

    tail -40 logs/run.log

The log rotates once it passes five megabytes, keeping exactly one previous copy as
`run.log.1`, so it is two bounded files and never needs cleaning up. A run ends with one of
four lines: `ok`, `ok ... stepped over` when a sync failed and was skipped, `DEGRADED` when
every step finished but the run reached almost none of its sources, or `FAILED` when the
watcher itself did not finish.

`logs/launchd.log` should stay empty. Anything in it means a run failed before it got as far
as opening its own log, which in practice means a broken plist or a missing interpreter.

### Is the agent alive

    .venv/bin/python -m tools.health

One command, spends nothing, and it is the thing to run when you have not looked in a while.
It prints when the last successful run was, whether one is currently failing, and how the
last few runs went. It exits non-zero when something is wrong, so a shell alias or an
external watchdog can use it directly.

You should rarely need it, because the agent now emails when it breaks. `agent/health.py`
sends on four events and nothing else:

    a required step failed          immediately, then at most once every 12 hours
    a run reached almost nothing    every step finished and it polled no sources
    a run succeeded far too late    the agent was quietly not running at all
    the first success after a fault the incident is over, and what it cost

The middle one is the one to understand. A failure alert needs a run to reach the end of the
wrapper, so it cannot cover a run that never starts: an unloaded launchd job, a laptop shut
for a week, an interpreter that no longer exists. Instead every successful run compares
itself against a heartbeat in `logs/health.json`, and a gap longer than `silent_after_hours`
is reported the moment anything runs again. It is retrospective by nature, which is the
honest best a system can do about its own absence.

A run that finished is not a run that worked, and since 2026-09-07 the check knows the
difference. The watcher reports how many of its sources answered, and a run that reached fewer
than `min_sources_ok_fraction` of them is recorded as a fault rather than a success, logged as
DEGRADED rather than ok, and reported here. Before that the check asked only whether a run
finished, and 22 of the 57 runs between 2026-08-21 and 2026-09-07 asked all 176 sources, had all
176 fail on DNS, stored nothing, exited cleanly, and were every one of them called healthy.

The floor is a quarter of the sources tried, set in `sources/schedule.toml` with the reasoning
next to it. Raise it to catch more, set it to 0 to switch it off. `tools.health` also prints what
the last run actually polled, so the number the verdict was reached on is visible without opening
the log.

What it genuinely does not cover: if nothing ever runs again, nothing ever fires. Closing that
needs a watchdog outside this repo.

All of it is tuned in `sources/schedule.toml` under `[health]`, including `enabled = false`
to switch the emails off while keeping the heartbeat.

This exists because on 2026-08-17 four runs crashed in a row and nothing said so for 42
hours. Every other signal this system produces travels down one channel, so when the channel
stops, the silence reads as a quiet day.

### Running one by hand

The wrapper is the same program launchd starts, so running it by hand is an exact rehearsal:

    .venv/bin/python -m tools.scheduled_run --job poll --dry-run   # print the commands
    .venv/bin/python -m tools.scheduled_run --job poll             # a real quiet run
    .venv/bin/python -m tools.scheduled_run --job digest           # a real run that emails

To make launchd itself start one immediately rather than waiting for the clock:

    launchctl kickstart -k gui/$UID/com.user.internship-search.poll

### What a run does, and what can fail

Three steps in order: the watcher, then the Airtable sync, then the calendar sync. The
watcher is the spine. If it fails the run stops, because the other two work on what it
stored, and a failed run emails you about it. Both syncs are optional: a failure in either
is logged and stepped over, so an expired Google token cannot cost a day of polling. A step
that is stepped over is not a failed run and does not alert. The Airtable step carries
`digest_only = true` in `sources/schedule.toml`, so the three quiet runs skip it and log
one line saying so.

A mail problem is not a watcher problem, and since 2026-09-07 the two read differently. If the
mail server cannot be reached, `notify.send` returns False rather than raising, so the run carries
on to both syncs and the failure is reported as a mail fault of its own. Nothing the unsent email
carried is stamped, so the next run offers it again. Until that date the exception escaped the
watcher instead: six days in the fortnight to 2026-09-07 were recorded as a failed watcher when
the watcher had polled all 176 boards perfectly well, and each of those runs stopped before the
Airtable sync, which only runs on the digest job.

Each step has a timeout, because a hung
network read would otherwise leave launchd believing the job is still running, and launchd
will not start a second copy of a job it thinks is already going.

## The filter

Polling finds roughly 18,000 open postings. Almost none of them are jobs the owner could take.
The filter is what turns that into a readable list, and it runs on every poll in three passes.

The first pass applies the exclusions that need nothing but the posting itself. Is this a
student or early-career role at all, is it a quant trading or quant research title, does it
resolve to somewhere confidently outside the United States, does it demand a security
clearance he does not have, is it open only to graduate degrees. Most of the volume dies here
and none of it costs anything.

The second pass, intake tagging, reads the term and the weekly hours off whatever survived.
When an aggregator feed already stated the term, that is free. When it did not, one small
Claude Haiku call reads the posting text, roughly a fifth of a cent each. It runs once per
posting, ever.

The third pass applies the timing rules. Summer 2027 goes straight through. Fall 2026 and
spring 2027 are term-time, so in-person full-time roles are dropped silently and part-time or
remote ones survive. Winter 2027 survives with a LEAVE REQUIRED label. One exception: a
full-time spring 2027 role in Chicago survives, labelled FULL TIME, IN CHICAGO, because
The owner is already living there that term.

Anything the filter cannot resolve is surfaced with a flag in capitals rather than dropped:
UNCLEAR TERM, UNCLEAR COMMITMENT, UNCLEAR LOCATION, UNCLEAR QUANT. That is deliberate and it
is the single most important rule in the whole system. A posting wrongly dropped is invisible,
because the owner never sees what was filtered. A posting wrongly kept costs him three seconds.
Those are not the same cost and the filter does not treat them as if they are.

Every rule is in `sources/prefilter.toml`, as data. To change what gets filtered, edit that
file. There is no company, city, job title, or term name anywhere in the Python.

    .venv/bin/python -m tools.prefilter_report --samples 5   # examples of every kill
    .venv/bin/python -m tools.prefilter_report --dry-run     # try edited rules, change nothing
    .venv/bin/python -m tools.prefilter_report --reapply     # keep the edited rules
    .venv/bin/python -m tools.prefilter_report --surfaced    # everything that survived

`--samples` is the one to run first. It prints real postings the rules killed and the reason
each died, which is the only way a wrong rule ever becomes visible. Four genuine mistakes were
caught this way on 2026-08-07, including the filter dropping Vancouver, Washington because
Vancouver is also in Canada.

After editing `sources/prefilter.toml` the loop is `--dry-run`, then `--reapply`. `--dry-run`
applies the edited rules to the whole database in memory, writing nothing and spending nothing,
so a change can be read before it is kept. `--reapply` forgets every stored decision and makes
them again. It costs nothing either: intake tags are kept, so no posting is ever read twice.
`.venv/bin/python -m tools.test_prefilter` checks 99 rules in about a second and is worth
running after any edit.

Nothing is ever deleted. A filtered posting stays in SQLite with the reason it was filtered.

## What the filter excludes

Five kinds of rule, all data in `sources/prefilter.toml`, none of them in code.

Eligibility. Location outside the US, an existing security clearance requirement, and postings
restricted to PhD, MBA or master's students.

Timing. Summer 2027 goes straight through. Winter 2027 surfaces with a LEAVE REQUIRED flag.
Spring 2027 surfaces if it is part time, or full time in Chicago. Fall 2026 is killed outright,
because you cannot do a fall internship. Terms already past are killed; anything beyond the cycle
surfaces with a flag rather than dying, because that boundary is a judgement call.

Student roles only. A company board carries every role the company has open, mostly senior
full-time positions, and this gate is what stops the agent paying to read sixteen thousand of
them. New grad, early career and rotational titles were removed from it on 2026-08-12: those are
full-time jobs you start after graduating, not internships. On 2026-08-14 the words "campus",
"apprentice", "trainee" and "resident" came out too, because every posting they admitted on their
own was a full-time hire, a facilities job or a skilled trade. Fellowships and residencies are
kept.

Excluded categories, in `[excluded_roles]`. Whole kinds of work that would be wrong at any
employer: hiring and HR, campus ambassador and brand promotion, fields with their own degree
track such as law and nursing, skilled trades, non-technical business roles such as legal,
compliance and sales development, and talent-community listings, which are mailing lists with an
apply button. This is the one to edit when something obviously wrong reaches you.

Employer kind, in `[employer]`. The only rule that looks at the company rather than the role, and
it exists because no title rule can express it: another university's "Undergraduate Research
Assistant" is open only to that university's own students, and its title is identical to one you
could take. It is a generic name test rather than a list of companies, so a new university
appearing in a feed is caught without an edit. `allow_company` keeps Chicago, and keeps
separately incorporated employers that carry a university's name, such as UVA's endowment fund.

The line between this file and the rubric, in one sentence. `sources/prefilter.toml` decides what
gets looked at; `rubric.md` decides what it is worth. The rubric cannot resurrect a posting the
filter killed, so a rule written there would do nothing. Conversely, anything that depends on the
company, the team or the description is a rubric question, not a filter one.

After any edit to the filter, in this order:

    .venv/bin/python -m tools.prefilter_report --dry-run   # what would change, writes nothing
    .venv/bin/python -m tools.prefilter_report --reapply   # apply it
    .venv/bin/python -m tools.test_prefilter               # 99 cases that must still pass

The test suite is not optional. It has already caught a rule that looked right and did nothing.

## The rubric

The filter decides what gets looked at. `rubric.md` decides what it is worth, and it is the
one file to edit to change how ranking behaves. No code change, no restart: the ranker reads
it at runtime.

It holds two scales from 1 to 10. Fit is how much the owner would want the role if it were
handed to him today. Reach is how likely he is to clear the bar, where 10 is comfortable and
1 is a long shot. They are independent on purpose: a dream role at the most selective lab in
the world is fit 10 and reach 2, and saying so is more useful than averaging the two into a
hedge. Each scale has band-by-band anchors, which is what stops a model returning 6 and 7 for
everything.

Everything below the horizontal rule in the file is sent to the model word for word. The note
above the rule explains how to edit it and never reaches the model, so it can be written
freely.

Near the bottom is a settings block in TOML that the code parses. It holds the tier bands
(fit 8 and above is tier 1 and goes in the daily digest, 6 or 7 is tier 2, 4 or 5 waits for
the Sunday roundup, 3 and below is stored and never emailed), the reach score at or below
which a posting is labelled REACH in the email, and the thresholds that decide which postings
are worth the more expensive model. Moving a tier boundary is an edit to that block.

After any edit:

    .venv/bin/python -m tools.rubric_check

It writes nothing. It parses the file, checks the tier bands cover every possible score with
no gap and no overlap, and prints exactly what the ranker will apply, including which tier
each fit score from 1 to 10 lands in. Run it every time, because the failure it guards is
silent: a band edited to leave a gap means a posting gets scored, lands in no tier, is never
emailed, and never appears anywhere you would notice.

## The ranker

One rule sits on top of the model's score. At the vehicle and autonomy employers added on
2026-09-25 (category `vehicles-autonomy` in `sources/companies.toml`), a role whose title names
no AI or ML work is held to tier 2 at best, so only their AI roles reach the daily email. The
rule is the `[[tier_cap]]` block in `rubric.md`; the model's own score is still stored, and your
own fit override is never capped. To put another employer under the same rule, give it that
category.

Built 2026-08-12. It scores every posting the filter surfaced, on the two scales above, and
derives the tier from the settings block. Two stages, because the two questions cost very
different amounts to answer well.

Stage B runs the cheap model, Haiku, on every surfaced posting that has no score. Most postings
are decided there and never cost anything more. Stage C runs the expensive model, Sonnet, only
on postings whose Stage B answer clears the routing thresholds in `rubric.md`, and its answer
replaces the cheap one. Raising `stage_c_min_combined` is the cost dial.

To look at it without spending anything:

    .venv/bin/python -m tools.rank_report

That makes no model call and writes nothing. It prints how many postings are waiting, how long
the assembled prompt is, whether each stage will cache it, a bracket for what draining the queue
would cost, and the next few postings in line. `--prompt` prints the exact text the model
receives, which is the fastest way to see what your labels are teaching it. `--scores 20` reads
back the twenty highest-scored open postings with the one-sentence reason for each.

To score for real without waiting for a scheduled run:

    .venv/bin/python -m tools.rank_report --run 20     # spends at most 20 model calls

Scoring otherwise happens inside a normal run, after the filter. `--skip-ranking` turns it off
for a run and `--max-rank-calls N` changes the per-run ceiling from its default of 150.

Three things worth knowing.

Scoring is resumable and capped. There are 1,526 surfaced postings waiting as of 2026-09-07 and
clearing them costs roughly eleven to fifteen dollars, which is more than a month's budget in one
go. The cap spreads that over about ten runs, or three days at the current schedule. Nothing is
lost by hitting the cap: a posting that was not reached is simply picked up next time. After the
backlog clears, spend tracks new postings rather than run count, because a posting is scored once.

Run `tools.rank_report` for the current bracket rather than trusting the figure above, which is
dated the day it was written and grows with the queue.

Your overrides beat the model. If you set `fit_override` or `reach_override` in Airtable, the
tier is derived from your number. The model's own answer is still recorded next to it, so you
can see where the two disagree rather than losing the evidence.

Labels make it cheaper as well as better. Your labelled postings are pasted into the prompt of
every scoring call, so the model sees your actual taste rather than only the rubric's anchors.
There is a second, less obvious effect, and it has already paid off. Haiku will not cache a
prompt under 4,096 tokens, and the prompt was 2,412 tokens when the ranker was built, so Stage B
paid full price on every call. The 52 labels added during August took it to 4,697 tokens, built
from 46 examples, so both stages cache now and Stage B's input cost is roughly a tenth of what it
was. Sonnet's threshold is 1,024, so Stage C always cached. `tools.rank_report` prints which
stages cache today.

If a posting is retitled by the company, its score is cleared and it is scored again on the new
title. Your overrides and labels are never cleared.

## The dashboard

    .venv/bin/python -m tools.dashboard --open

One page answering two questions: what is the highest-leverage thing to do right now, and how is
the search actually going. It is rebuilt on every scheduled run, so the copy on disk at
`build/dashboard.html` is never more than a few hours old, and it is also published as a hosted
page you can open from a phone.

It exists because the Airtable base cannot be that page, for a reason that is structural rather
than a matter of taste. The free plan caps a base at 1,000 records, `max_posting_records` caps
postings at 600, and there are more surfaced postings than that, so the base can only ever show a
rotating fraction of the search, ordered by provenance rather than by anything useful.

Five panels:

- **Do next**, ranked by urgency. Not the same ranking as the tiers: the ranker says how good a
  role is, this says how urgent it is that you act, so a tier 2 closing on Friday beats a tier 1
  that is rolling. Every row prints the reasons that put it there.
- **Closing windows**, from `sources/cycle_windows.toml`. These shut whether or not a posting is
  still listed.
- **Upcoming**, interviews and assessments with a date.
- **Pipeline**, everywhere you have applied, with anything silent for three weeks in red.
- **How it is going**, including a response rate that counts a rejection as a response.

The leverage weights are `WEIGHTS` at the top of `agent/dashboard.py`, named and commented. The
scale is arbitrary and only the order matters.

### Logging an application from the page

The hosted page can take input: "Log it" on any row, or "Add an application" for somewhere the
agent never saw. It stores what you enter with the page, not in SQLite, and the next session
pulls it into the database. The page says so in its own footer so it never looks like it saved
somewhere it did not.

This is a second route to the same field, not a replacement for Airtable. `applied_status` is
still editable there and still pulled on every sync. It exists because a tracker that depends on
a control you do not use stays empty.

## The three emails

Built 2026-08-16. Everything about who gets what is in two files and neither is Python.

The daily digest carries tiers 1 and 2 plus anything not scored yet, capped at 8 items, and
never sends when it is empty. It leads with any posting you marked interested that has since
closed, then YOUR MOVE, then lists roles by tier and company, each with its score, its one-line
reason, its REACH caveat if it is a stretch, and a REFERRAL line if you know someone there.

YOUR MOVE, added 2026-08-19, is the only part of any email that is about you rather than about
what the agent found. Four things, in this order: an offer waiting on you, roles you marked
interested and never applied to, applications nothing has come back on for three weeks, and a
count of the rest still waiting. It is capped at 12 named roles with the remainder as a count.

It repeats on every digest until you move it, and that is deliberate. Everything else in these
emails is sent once and marked as sent, so it never comes back. This is a to-do list; a to-do
list that disappears after you read it is not one. Nothing in this block is ever marked as sent,
so nothing in it can be lost.

Three things take an item out of it: applying, which means setting Applied status in the base;
ticking Closed; or changing your mind and unlabelling it.

It does not make an empty digest send. If you would rather hear about your own open loops even
on a day the agent finds nothing, set `send_on_actions_alone = true` in `sources/email.toml`.

The closed section lists only roles the filter had surfaced to you. Everything else that closed,
usually a hundred or more senior roles and chefs a day, is one count line, and a closure of that
kind never makes a digest send on its own. Changed 2026-09-25, when 117 of the 160 closures in
one email were postings the filter had already dropped.

Every email is sent twice over, as plain text and as HTML, and each mail client shows one.
`agent/emailhtml.py` builds the HTML by reading the plain text's indentation, so tier roles
become cards with a "View posting" button and the footer is small and grey. If it ever meets a
shape it cannot read it falls back to plain lines rather than failing, and `tools.test_digest`
checks that no line of the text goes missing from the HTML.

The Sunday roundup carries tier 3 and a summary of what closed in the week. It is worth
skimming, not acting on, which is why it is weekly.

Urgent mail fires the moment a trigger does, on any of the four runs a day rather than only
the evening one. Two triggers and no others: a tier 1 posting whose stated deadline is inside
72 hours, and a tier 1 posting open more than 10 days that you have neither labelled nor
applied to. Each posting can raise each trigger once in its life, so this cannot nag. Marking
something interested counts as action and stops it appearing.

### Changing any of it

Which tier goes to which email is `delivery` on the tier bands in `rubric.md`, because a tier
and where it is delivered are one statement about how much a posting is worth. Tier 1 and 2
say `daily`, tier 3 says `sunday`, tier 4 says `never`. Moving tier 4 into the roundup, or
tier 3 into the daily digest, is one word in that file.

Everything else is `sources/email.toml`: the 8 item cap, the 72 hours, the 10 days, which
tiers can raise an urgent alert, which day the roundup is due, how many locations a collapsed
role shows, and the standing reminder lines. It is commented throughout with why each number
is what it is.

    .venv/bin/python -m tools.test_digest    # 46 checks, one second, spends nothing

Run that after editing either file. Every case in it is named after the thing that breaks if
it regresses, and two of them guard failures that are silent: a cap that stamps its own
overflow would lose every posting past the eighth forever, and a roundup that tests for
"today is Sunday" loses the week whenever the laptop is shut.

One thing worth pasting in once. `sources/email.toml` has an empty
`links.airtable_unlabeled_url`. Open the base, click the Unlabeled view, copy the address bar
into it, and every email ends with a link straight to what you have not labelled. Airtable's
API cannot read a view URL, but it returns the base, table and view ids, and a view URL is
exactly those three joined. `tools.airtable_view_urls` prints them; nothing needs copying out
of an address bar.

### One role, several cities

The same role posted in four cities used to be four rows in Airtable and four lines in the
digest. It is now one of each, with the cities joined into the Location field. About 68 of the
then 690 surfaced postings were duplicates of this kind when it was built on 2026-08-16, mostly
Palantir, RTX and TikTok. The share has not been re-measured since.

This is presentation only. Every city keeps its own row in SQLite, its own requisition id and
its own closure clock, because a board issuing one id per city is issuing genuinely separate
postings and closure detection depends on tracking each one. Nothing about how postings are
identified changed.

Two consequences. The row shown in Airtable is the oldest of the group, not the best scored,
because a row that moved when a score landed would be deleted and recreated on every sync; the
Tier, Fit and Reason shown are the best any city scored. And a label you wrote on a city that
is now hidden still exists in SQLite and still teaches the ranker, it is simply no longer
visible in the base. A city you labelled is never the one hidden.

Turn it off with `collapse_locations` in `sources/airtable.toml` and `collapse.enabled` in
`sources/email.toml`.

## The backlog report

The digest only ever shows what changed since the last run, which means everything that was
already open when the database was first seeded is invisible to it. The backlog report is the
one command that shows that baseline:

    .venv/bin/python -m tools.backlog_report                  # print it
    .venv/bin/python -m tools.backlog_report --email          # send it
    .venv/bin/python -m tools.backlog_report --mark-alerted   # record that it was surfaced

It counts from the database every time it runs, so the number moves. Run it once with
`--email --mark-alerted` to clear the backlog properly; the stamp is what lets the coverage
audit tell "already shown to the owner" apart from "never surfaced". Repeat runs of
`--mark-alerted` do nothing to rows already stamped.

## Airtable, and why labelling matters

Airtable is where you read and label postings. It is not storage. SQLite holds all
~18,000 postings and stays the source of truth; Airtable holds a slice you can actually
work through, and the labels you write there come back into SQLite on the next sync.

Labelling is not optional busywork. Every ranking call in Milestone 6 carries your 15
most recent interested and 15 most recent not-interested examples, with your written
reasons. That is the only signal the ranker ever gets about your taste. With zero labels
it is guessing. Label aggressively for the first two weeks rather than skimming, and
write a sentence in Label reason rather than one word.

You edit six fields and nothing else. Label, Applied status, Label reason, Fit override,
Reach override, and Closed. Everything else is agent-written and gets overwritten on the next
sync.

Two views, and they are different jobs.

`Unlabeled` is the agent asking you something: every row it has no answer for. Set Label and
write a sentence in Label reason. Clearing this view is what teaches the ranker.

`Interested` is you working your own list. It holds everything you said yes to, grouped by
Applied status so the ones you have not applied to sit at the top, sorted by deadline and then
by age. Apply, then flip the dropdown, and the row drops into the applied group and out of the
YOUR MOVE block in tomorrow's digest. The agent stamps the date for you; you never type one.

Tick `Closed` when you find out a role is gone before the agent does, which happens: boards
list dead postings for weeks. That takes the posting out of every email and prunes its row from
the base on the next sync. Untick it and the posting comes back, so a mis-tick costs nothing.

Never delete a row to get rid of it. The base is rebuilt from SQLite on every run, so a row you
delete comes back six hours later with your label and your reason gone for good. `Closed` is
the way to say the same thing durably.

    .venv/bin/python -m tools.sync_airtable --dry-run   # what would move
    .venv/bin/python -m tools.sync_airtable             # move it

The sync reads your edits first, then removes rows you have finished with, then pushes
new postings. It matches rows by their Hash field, so it updates in place rather than
duplicating, and a row you delete by hand is noticed and recreated rather than lost.

### Setting up the base from scratch

Create an empty base in Airtable and copy its ID from the URL, the part starting `app`.
Create a personal access token with `data.records:read`, `data.records:write`,
`schema.bases:read` and `schema.bases:write`, scoped to that base only. Put both in
`.env` as `AIRTABLE_TOKEN` and `AIRTABLE_BASE_ID`. Then:

    .venv/bin/python -m tools.airtable_bootstrap --dry-run
    .venv/bin/python -m tools.airtable_bootstrap

That creates all three tables and every field from `sources/airtable.toml`. It only ever
creates what is missing, so re-run it after editing that file to add a field.

Two things it cannot do, because Airtable's API cannot. It cannot create views, so build
these two on Postings by hand:

    Unlabeled    grid, filter: where Label is empty
    Interested   grid, filter: where Label is "interested"
                 group by Applied status
                 sort by Deadline ascending, then First seen ascending

And it cannot delete Airtable's default `Table 1`, so delete that by hand too. The bootstrap
tells you when a view is missing, and prints the filter, grouping and sort for each, rather
than pretending.

`schema.bases:write` is only needed for the bootstrap and can be revoked afterwards.

### The API call budget

Airtable's free plan allows exactly 1,000 API calls per workspace per month, confirmed on
the workspace billing page. That is the limit that bites, not the 5 per second one the
client paces itself against. Only two things in the repo spend it: the scheduled
`tools.sync_airtable` step and the one-off `tools.airtable_bootstrap`. The watcher never
touches Airtable, so polling four times a day costs nothing here.

Going over does not create a charge. Airtable blocks calls until the month resets and
says so in the warning email; there is no overage billing on the free plan.

Reads are metered as well as writes. A sync downloads the whole postings table at 100
records a call, so 849 rows is 9 calls, plus 3 for companies and contacts, before it
writes anything. Writes go 10 records per call. Measured on 2026-09-25, a sync that
changes nothing costs 12 calls, and an ordinary day adds 6 to 10 for new and aged-out
rows. A `--dry-run` reads the live base, so it costs the same 12.

Every sync now prints what it actually spent, dry runs included:
`Airtable API calls this run: 12 (12 read, 0 write). Roughly 360 a month at one sync a day.`
Trust that line over any estimate in this file.

Two things keep it cheap, and both are easy to undo by accident:

- The push skips rows whose agent-written fields already match what Airtable holds. The
  live records are downloaded anyway, so the comparison is free. Watch for it in the
  output: `0 rows were refreshed (282 already matched and were skipped)`.
- The sync runs once a day rather than four times, via `digest_only` in
  `sources/schedule.toml`.

Together that is roughly 360 to 650 calls a month, which fits the free plan but not by a
wide margin. Before adding a call, mirroring another table,
or raising the record cap below, multiply the per-run cost by 30 and check it still fits.
`--dry-run` tells you how many rows would be written, and every 10 rows is one call.

### The record cap

The free plan holds 1,000 records per base, and the filter currently surfaces more
postings than that. So the sync pushes a bounded slice, set by `max_posting_records` in
`sources/airtable.toml`, and reports how much is waiting:

    600 of 600 posting rows in the base, out of 693 surfaced and open in SQLite.

Nothing is lost when the cap bites. SQLite has everything, and scoring reads SQLite, so a
posting that never reaches Airtable is still ranked and still emailed. The base is only
what you can read and label by hand.

A posting is not the only record it costs. Each one links to a company, so the base holds
more rows than the cap suggests:

    Postings   600
    Companies  211
    Contacts     1
    total      812 of 1,000

Deleting posting rows does not free records. The push refills to the cap on the same run,
so a delete changes which postings occupy the 600 slots and never how many records exist.
Only lowering the cap or deleting company rows moves the total. Check it before raising
the cap again:

    .venv/bin/python -m tools.sync_airtable --dry-run   # what would move

### What the sync removes

Four things leave the base, and one deliberately does not.

A posting killed by a prefilter change, so tightening a rule takes it back out.

A posting that closed and you either marked not interested or never labelled.

A posting still open that you marked not interested and wrote a reason for. The reason is
the condition on purpose: a pruned row can never be edited again, so a blank-reason row
stays in front of you until you write the sentence.

A company row that no posting and no contact links to. These used to accumulate forever,
since companies were created on demand and never removed.

Not removed: a posting that closed and you marked interested. Those stay, and the digest
now leads with them so a door closing on something you wanted reaches your inbox instead
of the row quietly disappearing.

Until the ranker exists there is no score to rank the slice by, so it prefers postings
from the 171 curated company boards over aggregator listings. That is
`prefer_company_boards`, and it should become a sort on tier once Milestone 6 lands.

## The mailbox, and why the agent does not read one

It was going to. Milestones 6.5 and 7.5 were to read the Careers in AI newsletter as a source
of postings and application mail as a status feed. Both were shelved on 2026-08-16 because
every route into `owner@example.edu` is closed by university policy: app passwords are
disabled, so IMAP cannot authenticate; the OAuth route needs a restricted Gmail scope that an
unverified personal app cannot hold without re-authorising in a browser every seven days;
automatic forwarding is disabled, so the Forwarding section is absent from Gmail's settings
entirely; and pulling from the other side asks for the same password. `CHANGELOG.md` for that
date has each one.

What that means in practice is short. The owner reads the newsletter himself, as he already did.
Applied status is set by hand in Airtable, which was always one of the five fields he edits
there.

`agent/inbox.py` and `tools/probe_inbox.py` are the reader built before the decision. They stay
in the repo, dormant. Nothing imports them and nothing on the schedule runs them. They work and
have simply never been let into a mailbox, so any account that allows an app password revives
them by setting `IMAP_USER` and `IMAP_PASSWORD` in `.env` and running:

    .venv/bin/python -m tools.probe_inbox

Do not re-investigate the uchicago mailbox without a changed policy or a different account.

## Referrals

A referral converts far better than a cold application, and the agent already knows the
company on every posting. People go in `sources/contacts.toml` by hand, and are loaded
into SQLite on every run. Nothing discovers contacts automatically.

A company you name there does not have to be one the agent polls. Example Capital is not on any
supported job board, and naming it creates a company row anyway so the contact links to
something.

## Two kinds of source

A company board is polled directly. It is fast, authoritative, carries the full posting text,
and updates the moment the company posts. Those live in `sources/companies.toml`, 180 of them
across four job boards: Greenhouse, Lever, Ashby and Workday.

Workday, added 2026-08-19, works differently enough to be worth knowing about. It hands over 20
postings per request where the others hand over a whole board in one, so the agent asks it for
its internships specifically rather than reading everything and filtering. It works that out by
reading the employer's own list of job types and picking the ones that say Intern. Some
employers do not file interns that way and cannot be polled at all; `tools.probe_workday`
answers that in one command:

    .venv/bin/python -m tools.probe_workday https://some-co.wd1.myworkdayjobs.com/en-US/Careers

Paste the address bar of the company's careers page. It prints what the employer calls its own
job types and says outright whether the board is addable. Nothing is written; adding a board is
an edit to `sources/companies.toml`.

Workday also publishes no posting text in its board response, so the agent goes back for the
description of anything that survives the title filter, one request at a time. That is why a
Workday posting can take a run or two longer to be judged than a Greenhouse one.

An aggregator feed is a community-maintained repository that tracks internships across
thousands of employers and publishes one big JSON file. Those live in `sources/feeds.toml`.
Feeds are how the agent sees Microsoft, Apple, Meta, Amazon, Tesla and Citadel at all, since
none of them expose a job board it can poll. A feed is a safety net rather than a mirror: it
carries no posting text, its coverage of any one company is partial, and a role appears when a
volunteer adds it rather than when the company posts it. Anything a feed covers is better
covered by adding that company's own board when one exists.

Three rules keep the halves from fighting. A feed listing for a company already in
`companies.toml` is skipped, so nothing is tracked twice. A feed's own `active` flag is not
treated as instant closure; an inactive listing just stops being reported and closes through
the same two-poll rule everything else uses. And since 2026-08-19 a feed listing is judged on
its title exactly like a company board posting.

That third one changed because the base filled up with things that were not internships. A
feed listing used to be in scope on provenance alone, on the reasoning that the feeds track
internships and nothing else, so its title did not matter. That let in "Modelling Resident",
"GTM Engineer - Data & Analytics" and "Sys & Development Analyst". Turning it off removed 22
surfaced postings and no labels. It also removed a few real internship programmes with names
that say nothing, SharkNinja's "Codeshark" among them, which is the price. The switch is
`trust_feed_provenance` in `sources/prefilter.toml` and turning it back on re-admits all 22.

## How the agent knows two postings are the same posting

This decides more than it sounds like it does, so it is worth a paragraph.

Every job board issues a requisition id for each posting and keeps that id when a recruiter
edits the listing. The agent identifies a posting by that id, paired with the source it came
from. So when a company changes a title from "Senior+ Software Engineer" to "Software
Engineer", or narrows a location from two cities to one, the agent updates the posting it
already had rather than concluding that one job closed and another appeared.

That matters because everything you write lives on that row. Your label, your score overrides
and the link to the Airtable record all sit on the posting, and if an edit created a new row
your judgement would stay behind on a row marked closed. Until 2026-08-12 that is exactly what
happened; `CHANGELOG.md` for that date has the story.

Two smaller rules sit underneath it. A board that supplies no id at all falls back to a
fingerprint of the company, title and location, which is the old behaviour and the best
available answer. And where a single poll hands back one id on two postings that are both
live, the id has told the agent nothing, so those two also fall back to the fingerprint and
stay separate.

If you ever suspect a posting is stored twice:

    .venv/bin/python -m tools.reconcile_identity            # read-only, shows what it found
    .venv/bin/python -m tools.reconcile_identity --apply    # merge them, after a backup

`--apply` copies `state.db` to a timestamped backup beside it before writing anything.

## When a new source is added

A source polled for the first time records everything it returns as a baseline instead of
reporting it as new, and the digest says which sources did that. This is the same reasoning
that makes the very first run quiet. Everything a brand new source returns is new by
definition, so calling it news would bury the real news. Adding the two feeds recorded 1,656
postings this way in one run.

To see what a newly added source brought in, run the backlog report.

## When a source goes quiet

A job board that answers correctly with an empty list is the failure mode to watch. It reports
`ok` forever while covering nothing, which usually means the company moved to a different
applicant tracking system and the token in `sources/companies.toml` now points at an abandoned
board. Three companies were invisible this way until 2026-08-07.

Both the run and the digest now name these, and `tools.verify_tokens` prints `EMPTY` rather
than `ok` and exits non-zero. When one appears, find the real board:

    .venv/bin/python -m tools.probe_tokens optiver optiverus optiver-us

If nothing hits, open the company's careers page and look at where the "apply" links point.
That is how all three were found. Put the answer in `sources/companies.toml`, never in code.

## Setup from a fresh clone

`.venv` and `state.db` are gitignored, so a new machine needs this once:

    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt

A virtual environment is a folder holding a private copy of Python and this project's
libraries, so this project's versions never collide with anything else on the machine. It does
not expire and is not something to set up each session. If you prefer plain `python`, run
`source .venv/bin/activate` once per terminal window.

Then create `.env` in the repo root:

    ANTHROPIC_API_KEY=sk-ant-...        # required for Stage 0 tagging and the ranker
    SMTP_USER=your.address@gmail.com    # optional; without it the digest prints instead
    SMTP_PASSWORD=<Gmail app password, not the account password>
    EMAIL_FROM=your.address@gmail.com
    EMAIL_TO=owner@example.edu
    AIRTABLE_TOKEN=pat...               # optional; without it the sync is skipped
    AIRTABLE_BASE_ID=app...
Missing email credentials degrade to printing the digest. Missing Airtable credentials
skip the sync. Neither ever crashes a run: the watcher is the spine and nothing optional
should be able to stop it.

`IMAP_USER` and `IMAP_PASSWORD` are read by `agent/config.py` and nothing on the schedule
uses them. See The mailbox below for why.

Google Calendar sync needs a separate one-time OAuth setup, roughly fifteen minutes, in
`docs/calendar-setup.md`. Until that is done, `tools.sync_calendar` only works with
`--dry-run`.

Finally, put it on a schedule, or it only ever runs when you remember to run it:

    .venv/bin/python -m tools.schedule --dry-run
    .venv/bin/python -m tools.schedule --install

## What lives where

    agent/run.py         one polling cycle, the entry point
    agent/sources.py     reads the token map and the feed map
    agent/fetchers.py    Greenhouse, Lever, and Ashby clients
    agent/feeds.py       aggregator feed client and normalizer
    agent/db.py          SQLite schema, migrations, new-posting and closure logic
    agent/prefilter.py   the filter rules, applied. No names of anything, ever
    agent/tagger.py      intake tagging, the one Haiku call per posting
    agent/rubric.py      reads rubric.md. Knows its shape, never its content
    agent/triage.py      runs the three filter passes and reports what they cost
    agent/delivery.py    which email a posting belongs in, and when each one is due
    agent/notify.py      the three emails, written. Decides wording, never policy
    agent/grouping.py    collapses one role across many cities. Presentation only
    agent/airtable.py    Airtable schema loader and REST client. Names no field
    agent/airtable_sync.py  moves postings out and labels back in
    agent/contacts.py    referral contacts, loaded from data on every run
    agent/inbox.py       read-only IMAP reader. Built, then shelved; nothing calls it
    agent/gcal.py        Google Calendar sync
    agent/prep.py        preparation task tracking
    agent/schedule.py    reads sources/schedule.toml, builds the launchd plists
    agent/config.py      every setting and environment variable, in one place
    rubric.md            the scoring rubric, authoritative at runtime
    INDEX.md             a map of the repo, and where to change what
    sources/             hand-edited data: company tokens, feed URLs, filter rules
    tools/               one-off commands run by hand
    tools/scheduled_run.py  one scheduled run: watcher, then both syncs. What launchd starts
    tools/schedule.py    installs and removes the launchd agents
    tools/rubric_check.py   parses rubric.md and prints what the ranker will apply
    agent/ranker.py      Stage B and Stage C. Scores postings against rubric.md
    tools/rank_report.py what the ranker would do and what it decided. Read-only unless --run
    tools/reconcile_identity.py  merges postings split across rows. Read-only unless --apply
    tools/probe_inbox.py    proves a mailbox opens. Shelved with agent/inbox.py
    logs/                timestamped output from scheduled runs, rotated, not committed
    state.db             local SQLite, the source of truth, not committed

Company names, ATS tokens, feed URLs, and every filter rule are data, never code. They belong
in `sources/`. The scoring rubric lives in `rubric.md` and is read at runtime, so changing how
ranking behaves never requires a code change. `INDEX.md` is the full map, with a table of
where to change what.

## What it costs

Two parts spend money, intake tagging and ranking.

Intake tagging runs at roughly a fifth of a cent per posting that needs a call. Clearing the
whole 2026-08-07 backlog cost about a dollar and a half. Ordinary runs tag a handful of postings
and cost fractions of a cent, because a posting is tagged once and never re-read, and because
roughly two thirds of them are answered for free by aggregator feed metadata.

Ranking is the larger of the two, at roughly a third of a cent per Stage B call and several
times that when a posting is routed to Stage C. Clearing the current backlog of 1,526 surfaced
postings costs somewhere between eleven and fifteen dollars, spread over about ten runs by the
per-run cap. `tools.rank_report` prints the bracket before you commit to it, and is the number to
trust, since the queue grows while the ranker is paused. After the backlog clears the steady
state is small, because a posting is scored once.

Measured on 2026-09-24 and 2026-09-25, and superseding the older figures above: the backlog
cleared at about $0.0004 a posting, but an ordinary run costs $0.0045 to $0.006 a posting,
because it scores a handful and pays to set up the prompt cache on both stages every time (the
cache lasts five minutes and runs are hours apart). At about 60 new surfaced postings a day that
is $3.50 to $10 a month, so October volume can reach the budget.

Lifetime spend on 2026-09-07 was $1.47, because ranking has been paused since 2026-08-12 and
almost all of that is intake tagging.

Five guards keep it bounded. The newest is the monthly ceiling, the `[budget]` block in
`rubric.md`: at 80 percent the digest footer and `tools.health` say so, and at 100 percent
ranking pauses until the month turns. A paused month strands nothing, because the daily digest
then carries unscored postings marked NOT YET SCORED. Raise `monthly_usd` to lift it. The hard exclusions run before any model call, so a posting that was
never a candidate is never paid for. `STAGE0_MAX_CALLS` caps tagging calls per run at 400 and
`RANK_MAX_CALLS` caps ranking calls at 150, and anything over either cap waits for the next run
rather than being lost. `--skip-triage` turns all spending off and `--skip-ranking` turns off
only the expensive half. Every run records its call count and estimated cost in the `runs` table.

The one lever that changes ranking cost materially is `stage_c_min_combined` in `rubric.md`.
Raising it sends fewer postings to the expensive model. Labelling also helps, for the caching
reason explained under The ranker.

## The backlog report and the filter

The backlog report lists what is open now rather than what changed, and since Milestone 4 it
lists exactly what the filter surfaced. Before that it matched titles against a keyword list of
its own, which meant the report and the digest could disagree. They now read the same verdict.

Postings still awaiting intake tagging are left out of it. Their timing rules have not run yet,
so listing them would claim coverage the agent has not actually decided on. They appear in the
report on the run that tags them.

## Where the build is

Read on 2026-09-07. Milestones 1, 2, 2.5, 3, 4, 5, 6 and 7 are built, and so is scheduling, which
belongs to no milestone. 174 verified company boards and 2 aggregator feeds, 29,917 postings
tracked and 23,336 of them open, of which 1,526 survive the filter and 600 are in Airtable. All
232 tests pass, across `test_prefilter` (99), `test_digest` (46), `test_actions` (33),
`test_workday` (23), `test_health` (22) and `test_identity` (9). Milestones 6.5 and 7.5 were
shelved on 2026-08-16 and are not coming back. Milestone 8, the cost telemetry and the two week
soak test, is the only one left, and the failure alerting half of it was built early on
2026-08-18.

The map grew from 108 boards to 171 on 2026-08-13, when 63 aerospace, defense, robotics and AI
startups were added, and to 174 on 2026-08-19, when the Workday fetcher made NVIDIA, Northrop
Grumman and Micron reachable for the first time. Adding a company is an edit to
`sources/companies.toml` and never a code change; the section below on finding a token explains
how.

The digest gained a YOUR MOVE block on 2026-08-19, so what you have said yes to and not applied
to now leads the email you already open.

The launchd agents were installed on 2026-08-09, so the agent now runs four times a day on its
own; `tools.schedule --status` says so at any time.

The ranker started scoring on 2026-08-12 and has been paused since, so all 1,526 surfaced
postings carry no tier. It scored 101 before the pause and later filter rules have killed every
one of them, so nothing that is currently open and surfaced has ever been scored, and the urgent
email is dormant because both its triggers key on tier 1. Milestone 7 landed on 2026-08-16 and
turns tiers into a daily digest, a Sunday roundup and urgent alerts; unscored postings ride the
daily digest marked NOT YET SCORED, so nothing waits on the pause. Whether to unpause is an open
decision in `NEXT_STEPS.md`.

`NEXT_STEPS.md` is the short version, `CHANGELOG.md` carries the current gaps, and `PRD.md`
section 10 has the full build order.
