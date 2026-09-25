# Scoring rubric

This file is the scoring rubric and it is authoritative at runtime. The ranker reads it on
every run, so changing how postings are scored is an edit to this file and nothing else. No
code change, no restart, no redeploy.

`PRD.md` section 5 is the text this was built from and nothing more. Where the two disagree,
this file wins and `PRD.md` is allowed to drift.

## How to change ranking

Edit the prose, the anchors, or the settings block at the bottom, then run:

    .venv/bin/python -m tools.rubric_check

That parses the settings block, checks the tier bands cover every possible score with no gap
and no overlap, and prints exactly what the ranker will use. It reads nothing else and writes
nothing.

Everything below the horizontal rule is sent to the model verbatim. The note you are reading
now sits above the rule and never reaches it, so you can write to yourself up here freely.

## What belongs in this file, and what does not

Here: what fit means, what reach means, the anchors for both, the tier bands, and which
postings are worth the more expensive model.

Not here: the hard exclusions and timing rules, which are data in `sources/prefilter.toml`;
company names and ATS tokens, which are data in `sources/companies.toml`; and the Stage 0
extraction instructions, which are in `sources/intake_prompt.md`.

The line between this file and the prefilter, in one sentence. `sources/prefilter.toml`
decides what gets looked at. This file decides what it is worth. A posting killed by the
prefilter never reaches the ranker at all, so writing "never score quant trading roles" here
would do nothing.

## Two things worth knowing before editing

The per-point anchors are what keeps scores stable across runs. A model given categories but
no scale clusters everything at 6 and 7, and a rubric that returns 7 for everything is worth
nothing. If you loosen the anchors, expect the spread to collapse.

Raising `stage_c_min_combined` is the cost knob. Stage B is cheap and runs on everything that
survives the prefilter; Stage C is the expensive model and runs only on what clears that line.
The $10 per month budget in `PRD.md` section 2 mostly lives here.

## Where this diverges from `PRD.md` section 5, on purpose

Recorded so a future reader does not mistake these for drift. All three are in `CHANGELOG.md`
under 2026-08-09.

1. Section 5 puts the REACH label on tier 1 only. Here it applies to any posting that gets
   emailed, because the caveat is just as useful on a tier 2 role.
2. Section 5 gives categories of high-fit and low-fit work but no scale. The 1 to 10 anchors
   below are new and are the load-bearing part of the file.
3. Section 4 sets the self-check threshold at "10 points", which assumes a 100 point scale
   that does not exist anywhere in this system. It is expressed here as a mean delta of 1.0 on
   the 1 to 10 axes, which is the same idea at the scale actually in use.

---

You score one job posting for one specific candidate. You return JSON and never prose.

Two scores, both integers from 1 to 10, plus one sentence of reasoning. Nothing else is asked
of you. Tier, delivery, and every flag are derived from your scores in code, so do not report
them and do not let them influence the numbers.

## The two scores

**fit** is how much this candidate would want this role if it were handed to him today. It is
about the work, not about whether he would get it.

**reach** is how likely he is to clear the bar, where 10 means he clears it comfortably and 1
means a long shot. Judge it against his profile, the posting's stated requirements, and the
employer's known selectivity.

The two are independent. A dream role at the most selective lab in the world is fit 10, reach
2, and that is a useful, correct answer. Do not let a low reach pull the fit down or the other
way round. Averaging them into one hedged pair of numbers destroys the only thing this system
is for, which is telling him what is worth his time and what is a stretch.

## The candidate

This section is an example profile. The live copy of this file describes the real candidate
and is not published.

Third-year undergraduate, graduating June 2028. Double major in economics and physics. Not a
computer science major.

For the Summer 2027 cycle he is a rising fourth year, which is the standard target class for a
junior-year summer internship. He satisfies "rising senior" and "graduating 2028". A posting
restricted to students graduating in 2026 or 2027 is a genuine mismatch and should score low
on reach.

Experience:

- Investment analyst intern at a large institutional investment office
- Intern at an AI startup, building agentic AI systems with Claude Code for private equity
- Data intern at a nonprofit, automating pipelines in Python
- Mission assurance engineer on a student CubeSat
- Equity research and valuation through a student investment group

Skills: Python, R, Claude Code, agent architecture, Excel. Physics coursework through quantum
mechanics, electricity and magnetism, differential equations and linear algebra. Econometrics.

What he is aiming at, in order: building AI agents, then applied AI engineering, forward
deployed engineering, and AI investing.

Separately from that ordering he is drawn to a domain: vehicles, autonomy and robotics.
Self-driving and driver assistance, automotive and electric vehicles, and robots as physical
systems. Read this as a domain preference rather than a fifth target. It lifts a role that
would otherwise sit mid-range, and it does not displace agent and applied AI work at the top.

## Fit, 1 to 10

Score the role as described in the posting, not the company's reputation. A brand name company
with a generic operations internship is a low fit score.

**9 to 10.** The primary target, directly. Building AI agents or agentic systems as the actual
work. Applied AI or ML engineering at a frontier lab, an AI-first startup, or a serious
institution standing up an AI team. Forward deployed engineer roles anywhere, which value the
economics plus technical profile and rarely require a CS degree.

**7 to 8.** Strongly adjacent. AI or ML research and product internships. Software engineering
at an AI-first company. AI and agent teams inside finance employers, which is a real and
growing category that the institutional investing plus AI combination fits unusually well; score
these high, not low, despite the finance employer. Investment or platform roles at AI-focused
venture and growth funds where the work is evaluating AI companies. Defense, aerospace, vehicle,
autonomy and robotics roles where a physics and CubeSat background carries weight.

**5 to 6.** Real technical or analytical work with a credible AI component but not at the
center of it. General software engineering, data science, or data engineering at a strong
technology or product employer with no AI content. Investment roles at generalist funds with
some AI exposure.

**3 to 4.** Technical or quantitative work with no AI component at all. Traditional finance,
banking, and consulting. General data analysis and reporting.

**1 to 2.** General business, marketing, sales, operations, HR, communications, recruiting.
Anything requiring a PhD or a publication record. Anything where none of his background is
relevant.

### Engineering with no AI content: decide the domain, then stop

This is the one case where the bands above are not enough, because the same job title means
opposite things in two different industries. Do not average the two answers, do not compromise
between them, and do not place a role between the bands because it has features of both. Work
through this in order and stop at the first line that matches.

1. Does the posting describe any AI or ML content, on any team, at any depth? If yes, this
   section does not apply at all. Score it on the bands above, where the AI and finance overlap
   is explicitly 7 to 8.
2. Is the work vehicles, autonomy, robotics, defense, aerospace or space? Score 7 to 8.
   Embedded, controls, mechanical, manufacturing, electrical and hardware engineering all
   count. This is the domain he named and where the physics and CubeSat background lands.
3. Is the employer a trading firm, a proprietary trading shop, a bank, an asset manager or a
   finance corporation? Score 3 to 4. Employer prestige does not lift it, and a well-known
   name is not a reason to move up.
4. Anything else. Score on the bands above, which for solid engineering at a strong technology
   or product employer means 5 to 6.

When an employer spans both, the work decides and the employer never does. A robotics or
autonomy team inside a bank is step 2. A trading firm's own settlement and accounting
infrastructure is step 3, whatever else the firm builds.

Two things that are never fit penalties. Timing, which is already handled by the prefilter and
carried to him as a flag, so a winter or off-cycle role is scored on its work like any other.
And missing information: a thin posting is scored on what it does say.

## Reach, 1 to 10

**9 to 10.** Explicitly open to second and third year students of any major. No requirement he
does not already meet. A broad program or a smaller employer that hires widely.

**7 to 8.** A standard undergraduate internship at a company that hires a real cohort. He
meets the stated requirements on paper. Nothing in the posting is a hard blocker.

**5 to 6.** Competitive but plausible. He meets most requirements and misses preferred ones,
for example a preference for CS coursework, a specific framework, or prior experience in the
exact domain.

**3 to 4.** Very selective, or he meets the requirements only partly. Small cohorts at the
frontier labs. Roles asking for significant production engineering experience or a stack that
does not appear anywhere in his background.

**1 to 2.** A stated requirement he plainly does not meet, short of the ones that would have
killed the posting earlier. Two or more years of professional experience. A graduate degree
preferred so strongly that undergraduates are not realistically considered. A graduation year
that excludes him. Or the very smallest and most selective programs.

Rules that override the anchors:

- Not being a CS major does not reduce reach on its own, for any role, including software and
  ML roles. Reduce it only when the posting itself states a requirement he does not meet.
- GPA is not a penalty unless the posting states a higher floor.
- The finance, AI and CubeSat roles are real, substantive experience. Count them.
  He is not a candidate with no track record.
- When a posting states no requirements at all, do not assume the worst. Score the reach on
  the employer's selectivity alone and stay near the middle.

## Calibration

Use the whole range. If most postings in a batch land on 6 or 7 you are hedging, and a rubric
that returns 7 for everything tells him nothing.

Score the same posting the same way twice. A random sample is re-scored each run and compared,
and a mean movement above 1.0 on either axis raises an instability warning in his email.

Judge only what the posting says, plus what is common knowledge about the employer. Do not
invent responsibilities the posting does not mention.

A posting arriving with a flag such as UNCLEAR TERM, UNCLEAR COMMITMENT or UNCLEAR LOCATION
already carries that caveat to him in the email. It is not your job to price it in, and doing
so would penalize the posting twice.

## The reason field

One sentence, at most 200 characters. Name the specific thing in the posting that decided the
fit score. Do not restate the job title, do not repeat the numbers, and do not hedge.

Good: "Builds internal LLM agents for the investment team, which is the exact overlap of the
AI and institutional investing experience."

Bad: "This is a good fit for the candidate given his background and interests."

## Examples from his own labels

Labeled examples are appended below this rubric when any exist: his most recent postings marked
interested and his most recent marked not interested, with whatever he wrote in his own words.
Any explicit score override he has written comes first and carries the most weight.

Those examples are ground truth about his taste and should move your scores on similar
postings. They do not override anything stated explicitly above. Early on there will be few
examples or none, which is expected; fall back on the anchors.

## Settings the code reads

These are parsed from this file, never hardcoded. They are here so that changing a tier
boundary stays an edit to the rubric. Report only fit, reach and the reason; everything below
is applied afterwards in code.

```toml
[scores]
min = 1
max = 10

# Tier is derived from fit, highest band first. The bands must descend and the
# last one must reach scores.min, so every possible score lands in exactly one
# tier. delivery is read by the Milestone 7 email logic:
#   daily   the daily digest
#   sunday  the Sunday roundup
#   never   stored in SQLite and never emailed
[[tier]]
number = 1
min_fit = 8
delivery = "daily"

[[tier]]
number = 2
min_fit = 6
# Moved off the daily email 2026-09-20, at the owner's request that only roles he
# should "really, really, really apply to" interrupt him. Tier 1 is that set;
# tier 2 is the good-but-not-urgent band and it collects into the Sunday
# roundup, where he reads it in one sitting instead of four times a week.
delivery = "sunday"

[[tier]]
number = 3
min_fit = 4
delivery = "sunday"

[[tier]]
number = 4
min_fit = 1
delivery = "never"

[reach]
# A posting that gets emailed with reach at or below this carries the `reach`
# flag, which prints as REACH so the stretch is visible before he clicks.
flag_at_or_below = 4

[routing]
# Stage B scores every prefilter survivor with the cheap model. A posting goes
# on to Stage C and the expensive model when EITHER of these is true: the two
# scores together clear stage_c_min_combined, or fit alone clears
# stage_c_min_fit. The second exists so a high-fit long shot is never decided by
# the cheap model just because its reach was low. Raise either to cut spend.
stage_c_min_combined = 12
stage_c_min_fit = 8

[queue]
# Which surfaced postings the ranker is willing to spend on, added 2026-09-20.
#
# Before this the ranker read the whole backlog oldest first, which is right when
# the backlog is small and wrong when it is not. It had been paused since
# 2026-08-12 and the queue had grown to 2,349, so the first thing an unpause did
# was spend the entire month's budget re-reading August.
#
# The owner chose to score forward rather than backward on 2026-09-20. New
# postings arrive every day and old ones close on their own, so a posting that
# has sat unscored and unread for weeks is the worst use of the next dollar.
#
# max_age_days counts from first_seen. Raise it to widen the window; the whole
# remaining backlog is about $13 at the time of writing, so setting this to 999
# is a real option rather than a runaway. Set it to 0 to switch the window off
# entirely and score everything, which is the pre-2026-09-20 behaviour.
# Set to 0 on 2026-09-20, hours after being introduced at 14. The window was
# built to protect a budget against a $16-23 estimate, and the first real run
# measured the true cost at about $0.0005 a posting, roughly $0.85 for the
# entire backlog: `tools.rank_report` prices every call at full prompt cost and
# never deducts cache reads, which since both stages started caching is most of
# its answer. A window that saves under a dollar is not worth the hole it opens,
# because a posting outside it is never scored, and since the same day an
# unscored posting is also never emailed. The machinery stays because it is the
# right shape if the cost ever moves.
max_age_days = 0

# Newest first inside the window, so a capped run spends on the freshest thing
# it can see. This reverses the old oldest-first order, which existed to drain a
# backlog in discovery order and is the wrong instinct once a window exists.
newest_first = true

# A posting the owner marked interested is scored whatever its age. He has said
# yes to it, so it is exactly the thing worth spending on, and there are 21 of
# them. This is the same principle as the prefilter's label override.
always_score_labelled = ["interested"]

[budget]
# The monthly ceiling from PRD section 2 and success criterion 9, added
# 2026-09-25. Month-to-date spend is summed from the `runs` table, which records
# every run's estimated cost (Stage 0 tagging plus ranking), per UTC calendar
# month.
#
# Why it exists now. The backlog cleared at about $0.0004 a posting, but a normal
# run scores 5 to 30 postings and pays the cache write on both stages every
# time, since runs are hours apart and the cache lives five minutes. Measured on
# 2026-09-24 and 2026-09-25 that is $0.0045 to $0.006 a posting, which at 60 a
# day projects $3.50 to $10 a month, and October volume can push it past $10.
#
# warn_fraction puts a line in the digest and in `tools.health`. stop_fraction
# pauses ranking for the rest of the month. A pause never strands a posting:
# while it holds, the daily digest carries unscored postings marked NOT YET
# SCORED, which is the condition CLAUDE.md rule 10 sets for include_unscored
# being off. Raise monthly_usd here to lift it; nothing else needs editing.
monthly_usd = 10.0
warn_fraction = 0.8
stop_fraction = 1.0

[variance]
# The self-check in PRD section 4. Re-score this fraction of each batch a second
# time; if the mean absolute movement on either axis exceeds max_mean_delta, the
# next digest carries a one line instability warning at the top.
sample_fraction = 0.10
max_mean_delta = 1.0

[few_shot]
# How many of the owner's labelled postings are appended below the rubric, per
# label. PRD section 4 specifies 15 and 15; raised to 25 on 2026-08-15, when 25
# interested labels existed and 10 of them were being discarded on every call.
# Raising it teaches the model more about his taste and makes every ranking call
# more expensive, since these examples sit in the prompt of every single call.
# Once either side passes this number the extras are dropped again, silently, so
# check `tools.rank_report` for how many examples are actually going in.
# 35, raised from 25 on 2026-09-25. He has 31 interested labels and 21
# not_interested, so six of the interested ones were being dropped silently,
# which is the failure this setting's own comment warns about. Labels are the
# scarcest input this system has and he has stopped making new ones, so
# discarding a fifth of them to save cached tokens is the wrong trade.
per_label = 35
```
