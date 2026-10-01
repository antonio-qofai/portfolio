# Group Trip Planner PRD

The specification of record. `CLAUDE.md` covers how to work in the repo; this
covers what the system must do. If code and this file disagree, one of them is
wrong. Say which before changing either.

## Overview

Planning a trip for a group fails in a predictable way. Someone starts a group
chat, nobody's dates line up, the cheapest person goes quiet, and the trip
never happens. This agent collects everyone's constraints through one link and
returns a plan that works for the whole group, with the tradeoffs stated.

It began as a class project (winter 2026, `docs/original-brief.md`): a
deterministic scorer over a CSV of survey answers. This version keeps that
scorer as the decision-maker and adds a shareable trip flow, two Claude steps,
and live weather, travel time, and prices.

## Goals

1. A group of 2 to 20 people gets one plan that every member can attend, afford,
   and reach within their stated flight limit, or a clear explanation of who or
   what is in the way and how to fix it.
2. The decision is explainable. Every plan shows its score components, what
   limited the options, and which data sources it used.
3. Language-model output never decides where the group goes. Claude reads notes
   and writes the itinerary; deterministic code picks the destination and dates.
4. It works as a public demo at near-zero cost: the sample trip runs on cached
   data and costs about two cents in Claude usage.

## Non-goals

- Booking anything. The plan links to Google Flights and Google Hotels searches.
- Accounts or logins. Links are the access model.
- Live data for every destination. Prices are checked for the winner (and the
  next candidate if the winner fails), not for all 61.
- Choosing from the whole world. The destination list is curated data.

## Users and flow

- Organizer: creates a trip (name, their name, rough date window), shares the
  link, watches answers, removes people, runs the planner, re-plans after edits.
- Friend: opens the link, answers the form, can edit later from the same
  browser, sees the plan on the same link.
- Recruiter or visitor: clicks "Try a sample trip", gets a private copy with six
  made-up friends, plans it, and can join as a seventh person.

## Inputs

Per person (`planner/schema.ts`, validated in the browser and again on the server):

| Field | Required | Notes |
|---|---|---|
| name | yes | unique within the trip, case-insensitive |
| departure_city | yes | geocoded for travel time |
| home_airport | no | three-letter code; needed for live flight prices |
| availability | yes | 1 to 5 date ranges |
| budget_usd | yes | total for the trip |
| budget_flex_pct | yes | 0, 10, 20, or 30 percent |
| interests | yes | 1 to 8 from a fixed list |
| climate_preference | no | warm, mild, cool, dry |
| passport_ready | yes | |
| avoid_crowds | yes | |
| max_travel_hours | no | longest flight they would take |
| notes | no | free text, up to 500 characters, read by Claude |

Older CSV surveys (`data/samples/`) still parse through `planner/survey.ts` and
are used as test fixtures.

## Planning rules

Dates. Find the days everyone is free. Use the earliest run of 4 consecutive
days; failing that 3, then 2, with a warning. With no 2-day overlap, return
`error` and name the one person whose absence would open the longest window.

Hard filters. A destination is out if it:

- matches anyone's home city by whole words,
- is outside the US (including Puerto Rico) and anyone has no passport or asked
  to stay domestic,
- costs more than anyone can spend: with live prices, each person's real cost
  against their own budget plus flex; otherwise the static estimate against the
  tightest budget,
- needs a longer flight than anyone's `max_travel_hours`.

Scoring. Five components, each normalized to 0 to 1, weighted in
`planner/scoring.ts`:

| Component | Weight | Meaning |
|---|---|---|
| interests | 0.40 | share of each person's interests covered, averaged |
| fairness | 0.15 | the same for the least-served person |
| climate | 0.20 | share of people whose preference matches (real weather when fetched) |
| budget | 0.15 | headroom for whoever is closest to their limit |
| crowds | -0.10 | applied when most of the group avoids crowds and the place is popular |
| dealbreakers | -0.20 | scaled by the share of people with something to avoid there |

Outputs. `ok` with a winner, a distinct runner-up (or a labeled closest
alternative), an itinerary, per-person fit, and a shortlist of the top 5;
`needs_info` with every missing field for every person; or `error` with
limiting factors and specific fixes.

## Agent pipeline

`server/agent.ts`, in order. Each step is optional and records itself in the
plan's `agent` trace.

1. Constraints. One Claude call (structured output, low effort) reads all notes
   and returns, per person: domestic only, mobility, diet, tags to avoid, extra
   interests, nonstop only, and a one-line summary. Tags are limited by schema
   to tags in the destination data. Cached forever by a hash of the notes and
   the prompt version. Skipped when nobody wrote notes. On failure, keyword
   rules apply.
2. Travel time. Home cities are geocoded (Open-Meteo, cached 30 days) and
   flight hours estimated as distance over 800 km/h plus 45 minutes.
3. First ranking.
4. Weather for the top 5: Open-Meteo forecast within 16 days, otherwise the mean
   of the same dates over the past three years, labeled "typical". Converted to
   climate tags, then the planner ranks again.
5. Prices for the winner: cheapest round trip from each distinct home airport
   (a separate nonstop-only search where someone asked for it) and the cheapest
   hotel rated 4.0 or higher. Per-person cost is flight plus half a room plus
   $80 a day. Real flight durations replace the estimate. If someone can no
   longer afford the winner, rank again and check the next, at most 2 checks.
6. Itinerary. One Claude call (structured output, low effort) writes each day
   with who it is for, plus a 2 to 4 sentence summary. Checks: right number of
   days, correct dates in order, no empty day, every person on at least one day,
   no unknown names, no activity over 600 characters. Failures are sent back in
   the same conversation, up to 3 drafts, then the template is used.

Model: `claude-opus-5-5` with server-side refusal fallback. Traveler notes are
wrapped in tags and marked as data in both prompts.

## External services and limits

| Service | Use | Limit and guard |
|---|---|---|
| Anthropic API | steps 1 and 6 | about $0.02 to $0.05 per plan; planning is limited to 10 runs per hour per IP |
| SerpApi (free plan) | flights and hotels | 250 searches a month; app stops at `SERPAPI_MONTHLY_CAP` (default 200); results cached 3 days |
| Open-Meteo | geocoding, forecast, history | free, no key; results cached |
| Neon Postgres | trips, responses, cache, rate limits | free plan |
| Vercel | static app and one Node function | Hobby plan; function max duration 120 s |

## Access and data

- A trip id (11 characters, random) is the share link. Anyone with it can answer
  and see the plan, never anyone's answers or the organizer key.
- The organizer key (24 characters) travels in the URL fragment, so it never
  reaches server logs.
- Each response has an edit token kept in that friend's browser.
- Trips expire after 60 days (sample trips after 7) and are deleted on the next
  trip creation. Cache entries expire on their own schedule.
- Secrets live only in `.env` locally and in Vercel's environment settings.

## Success criteria

Each is covered by a test in `tests/`.

1. The five cases from the original brief: no overlap, budget mismatch,
   constraints too tight, missing fields per person, distinct runner-up.
2. Each scoring bug fixed since the original has a regression test.
3. The public view never exposes the organizer key, edit tokens, or answers.
4. A failed itinerary draft is retried with the failures as feedback, and three
   failures fall back to the template with all plan checks passing.
5. Any Claude, weather, or price failure still returns a plan.
6. Cached prices spend no quota, and a destination is never partly price-checked.
7. Real prices that break someone's budget move the plan to the next destination.
8. No test makes a network call or spends quota (the opt-in database test aside).

## Risks and open questions

- Destination tags are thin, which drags down good cities. Enriching them with
  Claude once, offline, and reviewing the diff would help.
- Straight-line flight estimates are short for connections until prices are
  checked. A per-airport connection penalty would tighten them.
- SerpApi results are Google's, through a third party. If the free plan changes,
  the app falls back to estimates without code changes.
- Prompt injection through notes is limited by the schema (Claude can only
  return fixed fields and known tags) and by marking notes as data; the
  itinerary text is free-form and could still be steered by a determined note.
