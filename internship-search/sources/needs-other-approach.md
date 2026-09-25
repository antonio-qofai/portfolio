# Companies the per-company watcher does not cover

These are in the PRD's target list but do not expose a Greenhouse, Lever, or Ashby
JSON board. Probed on 2026-08-06 with `tools.probe_tokens`, all three platforms,
multiple token spellings each. None resolved.

Rewritten 2026-08-07 after Milestone 3. Aggregator feeds now cover most of what this
file used to describe as a gap, so the point of this file has changed. It is no longer
a list of companies nobody watches. It is a list of companies watched indirectly, at
lower fidelity, plus the few still genuinely uncovered.

## What a feed covers and what it does not

A feed carries the title, company, location, term and degree level. It does not carry
the posting body, so Stage 0 has less to read. It is also second-hand: a role appears
when a volunteer or a scraper adds it, not when the company posts it. Success criterion
2 in `PRD.md` asks for 6 hour latency, and a feed cannot promise that.

So a feed is a safety net, not a substitute. Any company here that later exposes a
supported board should be moved into `companies.toml`, where it gets polled directly.

## Now covered by the aggregator feeds

Counted from `state.db` on 2026-08-07, open and matching the digest keywords:

    TikTok            140      Microsoft          17
    Tesla              90      Apple              13
    ByteDance          65      DRW                13
    Susquehanna (SIG)  25      Meta               12
    Citadel            25      Amazon              9
    Citadel Securities 11      Google              3
    Applied Intuition   3      Two Sigma           2
    NVIDIA              2      BlackRock           1

Nothing in that list needs a Workday fetcher urgently any more. The feed is the cheaper
answer and it already works. Note how thin the Google, Amazon and Nvidia coverage is
compared to what those companies actually post. A feed is a net, not a mirror.

One thing to watch. The feed writes Susquehanna three different ways: "Susquehanna
International Group (SIG)", "Susquehanna", and "Susquehanna Investment Group". If any
of these companies later gets its own board in `companies.toml`, add the aggregator's
spellings to that entry's `feed_aliases` list, or the same role will be tracked twice
under two source keys.

## Still uncovered, and still worth building for

Bridgewater Associates, Balyasny Asset Management, Castelion, Hadrian, Clay, Greylock,
Insight Partners.

These appear in neither a supported board nor the feeds. They are a real gap. Hand-check
them during peak season.

### Example Capital, found 2026-08-09

Example Capital was in `PRD.md` section 8 as a target and was in neither `companies.toml` nor
this file, so nothing was watching it and nothing said so. Found while seeding the
contacts table, because it is the one company the owner has a named referral at, which
makes it the worst possible company to be silently missing.

Its careers page runs on Newton Software, now Paycor Recruiting. That is a fourth ATS
none of the three fetchers speak, and `tools.probe_tokens` returns no hits on any
spelling across Greenhouse, Lever and Ashby, which is expected rather than surprising.

It now has a row in the `companies` table with no ATS and no token, created by the
contacts loader so the referral has something to attach to. Nothing polls it. Until a
Newton fetcher exists or Example Capital moves platforms, this one is hand-checked, and the
referral is the reason to bother.

## USAJOBS, and therefore NASA, not attempted

Added 2026-08-13. The owner raised NASA Pathways and said aerospace matters to him more than the
current coverage implies. Worth being precise about what is wrong, because it is not what it
looks like: NASA is not being filtered out, NASA never arrives. There is no NASA entry in
`companies.toml` and zero NASA postings in the database.

NASA Pathways runs on USAJOBS, the federal hiring system, which none of Greenhouse, Lever or
Ashby touch. USAJOBS does publish a documented public API, so a fetcher is buildable.

**Decided 2026-08-13: do not build it.** the owner said NASA is the only federal employer he wants
to work for, and he will check it by hand. A whole fetcher for one organization is not worth it,
and the breadth argument for USAJOBS (every federal lab and agency at once) is worth nothing if
he does not want the rest of the federal government. Do not re-propose this without new
information; the reasoning is his preference, not a technical limit.

NASA Pathways is therefore a hand-check, tracked in `MY_SEARCH.md`. His aerospace interest is
private-sector, which is a token map problem rather than a fetcher problem.

## Google DeepMind, removed from the token map

Removed 2026-09-25. `greenhouse:deepmind` returned 404 on every run from 2026-09-06, and none of
`googledeepmind`, `google-deepmind`, `deepmindtechnologies`, `gdm` or `deepmindcareers` answers
on Greenhouse, Lever or Ashby. Its roles now appear to live on Google's own careers site, which
no fetcher here reads. The owner checks it by hand, alongside Google itself. Re-add it only if a
probe finds a live board; its stored rows were closed by `tools.retire_sources`.

## Blue Origin, confirmed uncovered

Added 2026-08-13, resolved the same day. The suspicion was right. `lever:blueorigin` resolves,
returns HTTP 200, and carries zero postings, which is the stale-shell failure that hid Mistral,
Optiver and Hudson River Trading for a day. Nothing in `companies.toml` points at it and nothing
should until a real board is found. The single posting in the database came from an aggregator
feed, not from a board.

Blue Origin runs its own careers site rather than a supported ATS. Hand-check it, or add it if it
moves platforms.

## Probed 2026-08-13, no supported board, still uncovered

The owner asked for much wider aerospace and startup coverage. 63 companies were added to
`companies.toml` that day. These were probed across Greenhouse, Lever and Ashby with several
token spellings each and produced nothing, so they are gaps rather than oversights. Re-probe
occasionally; small companies move onto Ashby all the time.

Aerospace, space and defense: Firefly Aerospace, Sierra Space, Blue Origin, Applied Intuition
(covered thinly by a feed), Castelion, Hadrian, K2 Space, Impulse Space, Apex, Umbra, Capella
Space, Karman, Venus Aerospace, Boom Supersonic, Axiom Space, Zeno Power, Starfish Space, Turion
Space, Aetherflux, Astro Mechanica, Voyager Technologies, Overland AI, Rune Technologies,
Theseus, Firestorm Labs, Second Front Systems, Machina Labs, Freeform, Nominal.

AI: Clay, Groq, Hugging Face, Weights & Biases, Windsurf (now part of Cognition, which is
covered), Hebbia, EvenUp, Surge AI, Qdrant, Replicate, CrewAI, Hippocratic AI, Skild AI, Zipline,
Kodiak Robotics, Gatik, Sakana AI, Decart, Captions, Higgsfield, Genesis AI.

Castelion, Hadrian and Clay were already on the uncovered list above and remain there; the extra
spellings tried on 2026-08-13 did not find them either.

## Resolved, but deliberately not added

Boards that resolve and belong to the right company, left out on judgement rather than on
failure. Recorded so nobody re-probes them and assumes they were missed.

Wayve, Waabi, Parloa, PolyAI and Langfuse are real and sit almost entirely outside the United
States, so nearly every posting would die in the location filter. ClickHouse, Neon, Zilliz,
Mercury, Brex, Vanta and Sardine resolve and are healthy but are database or fintech companies
rather than AI ones, and the map is already long. Stability AI returns 5 postings, none
technical. Add any of them by copying an entry in `companies.toml`; nothing else is needed.

## Workday, built 2026-08-19, and what it did and did not reach

The fetcher exists. NVIDIA, Northrop Grumman and Micron are in `sources/companies.toml`.
Prove a new board with `python -m tools.probe_workday <careers url>` before adding it; the
command prints the tenant's own job types and says outright whether the board is addable.

**Lockheed Martin, RTX and L3Harris are not on Workday.** This file said they were, from
2026-08-13 until 2026-08-19, and it was wrong. Every plausible tenant and site was probed
(`lmco`, `lockheedmartin`, `rtx`, `raytheon`, `l3harris` against wd1, wd3 and wd5, with eight
site slugs each) and every one returned 422. Their careers pages run something else entirely:
`careers.rtx.com` and `careers.l3harris.com` are not Workday and neither is Lockheed's. They
remain uncovered and they need a different approach, not this one. Do not re-probe them for
Workday.

**Leidos is on Workday, is reachable, and is deliberately not added.** It files every intern
under job type "Regular", so the facet query the fetcher uses finds nothing to ask for. The
only remaining route is a text search, which returned 816 postings in 74 seconds, led by
"International Program Coordinator" and "Site Lead, Commercial & International Projects",
which match because "international" contains "intern". The fetcher raises on a board like this
rather than returning what it can, because returning an empty list would be read as every
posting from the source having closed. If Leidos is wanted badly enough, the switch is
`search_fallback` in the `[workday]` block, and it is global rather than per company.

Boards that answered but whose interns are unreachable this way belong in this file, not in
the token map.

## Rejected candidates, do not re-add without checking

`ashby:ssi` (Safe Superintelligence) and `greenhouse:magic` (Magic) both resolve and
both return zero postings. A board that answers correctly with nothing behind it is
indistinguishable from a healthy one, which is exactly how Mistral, Optiver and Hudson
River Trading stayed invisible for a day. They are not in `companies.toml` for that
reason. If either starts returning postings, add it then.
