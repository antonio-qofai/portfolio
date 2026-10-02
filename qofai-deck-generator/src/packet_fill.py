"""The fill layer (E9c), layer 2 of the packet document.

`packet_document` decides what the document's SHAPE is and what an absence
looks like; this module decides which of its slots have a source. It returns the
map from packet path to value that `packet_document.build` takes as `fill`, plus
the map from a filled path to the tool that returned it. A path this module does
not put in the map is absent in the document's value and named in section 8
`gaps` at once, which is the whole reason absence never has to be written down
here.

The rule this module exists to hold, and it points one way. A figure with no
source is a missing field, never a value. So the map carries the resolved
company's name, the `pe_firm` the request itself sent, the three baseline
figures and the scenario cases E7a derived from a paper, the phase labels E4
read off a chart, the opportunity's own `ebitda_impact`, and the contract's
three declared templated defaults. Nothing else. Seven of the eighteen render
roles come out absent and that is the correct outcome rather than an unfinished
one: no tool in the grant returns a current-state metric, a pain point, a
capability, a workstream, or a milestone, so this module does not compose one.

Two constraints decided elsewhere and enforced here.

Months against weeks, and the plan renders in the unit the chart itself states.
Every schedule field in the contract is week-denominated. All eight committed
excerpts are month-denominated, verified 2026-08-15 and again 2026-08-18 by
running `chart_timeline_parser.parse_timeline` over them: seven carry a timeline
and all seven are in months. The wider corpus is NOT months-only, and an earlier
claim here that it was generalised from what the fixtures happen to hold: E11's
12-paper survey found published papers stating `Phase 1: Foundation (Weeks 1-6)`.
So converting between the two is what stays refused (it needs a multiplier, and
choosing one is not this provider's call), while the span itself crosses under
unit-neutral paths that carry the chart's own unit beside its own numbers:
`timeline.phases[].start` / `end` / `unit`, `timeline.columns[]` for the axis,
and `timeline.milestones[].position` for each phase boundary. The five
week-denominated contract fields stay absent, and `record_unit_mismatch` gives
them a reason that says why rather than the generic no-source line.

`target_metrics[].label`. Absent, ruled by Antonio on 2026-08-15 against the
design's own recommendation. The value is real platform data; the label is a
unit-naming string no tool returns. E9a's `_metric` renders whichever half it
has, so refusing costs a caption rather than a role.

The copy channel, added 2026-08-15 and governed by nothing upstream.
`coverage_guard` excludes the prose copy lines because they carry no field path,
so the 18-role table is silent on the roles that occupy most of the visible deck.
Measured on the document this module built: 32 roles rendered `[MISSING: ...]`,
only 11 of them governed, and the cover carried no title and no client name at
all. `map_packet` reads those roles out of a section's PROSE rather than its
yaml, so `copy_lines` writes the cover bullets and the `Headline:` / `Subhead:`
lines the document was never emitting.

Two kinds of copy and the line between them. The cover's own bullets and the
opportunity headline are real data, composed from the resolved records and the
opportunity's own title. The four section framing lines are deck standards in
the same class as `CONFIDENTIALITY_DEFAULT`: Agent OS was never asked to supply
them, and leaving them empty makes a template gap look like a platform gap,
which is the one thing the two-deck demo exists to tell apart. A framing
constant names what its section contains and asserts nothing about the client,
the opportunity, the figures, the schedule or the outcome. A headline that
cannot be written without saying something specific about this engagement is
left missing instead.

`plan_summary` WAS the one framing role with no home here, and closing it on
2026-09-03 is what fixed the garbled slide 4 description. `map_packet` composed
it from `build_summary.phases[].summary` — every phase's caveat note joined on a
space, labels and punctuation dropped — which produced a five-line run-on on the
2026-08-26 WTG deck and an ungrammatical two-note splice on the 2026-09-02 one,
in both cases restating what slide 2's build strip and slide 4's own Gantt had
already drawn. It is now the same shape as the other three section subheads:
section 5 carries a `Subhead:` line, `copy.plan_summary` is in
`paper_writing.WRITTEN_SCOPE` so a run with a writing pass gets an
engagement-specific sentence under that pass's evidence rule, and `PLAN_SUMMARY`
below is the deck standard every other run keeps. It names what the slide
contains and asserts nothing about the client, exactly like `PLATFORM_SUMMARY`
and `TERMS_SUMMARY`. It is not one of the 18 roles, so it moves `role_coverage`
by zero either way.

`subtitle` and `opportunity_summary` stay absent by design: both come from the
opportunity description, 1,772 characters on the one real record measured
2026-08-15 (the figure read "roughly 1KB" before that, from an estimate no live
call had ever checked), which is far too long for a cover subhead and whose
compression is F1's job under a diff guard. `opportunity_record` parks the
description verbatim in section 3, which nothing on the proposal path parses and
the coverage walk never reaches, so F1 has its input untouched.

Not hardcoded to any client. Every value arrives through a resolved record, the
request, the opportunity detail, or the `Packet`; the only literals are the
contract's own deck standards and the section framing, which name no company.
"""

import dataclasses

import packet_assembly
from coverage_guard import COVERAGE_MAP
from data_source_adapter import DECK_TYPE_LABEL_DEFAULT, _fmt_deck_date
# `ORIGINS` is deliberately NOT imported any more: `role_coverage` reads the
# document's own section 8 rather than the static table, so a run whose class
# differs from the schema's is counted as it actually rendered (E11 Stage 2c).

# Where a filled path came from, recorded into section 8's `sources` ledger so a
# reviewer can tell a tool's answer from a paper's and both from a deck standard.
LIST_COMPANIES = "list_companies"
REQUEST = "request.pe_firm"
OPPORTUNITY_DETAILS = "get_opportunity_details"
RESEARCH_PAPER = "get_opportunity_details.research_paper_natural"
CONTRACT = "contract section 8 templated_defaults"
# Computed off another packet field rather than read off a source. Section 8
# reports these under `derived`, so a reviewer is told which of the two they are
# looking at (E11 Stage 2c).
DERIVED_HORIZON = "derived from timeline.phases[].end"

# The three the contract declares in its own section 8, and only those three.
# Deck standards rather than company data, which is why they name no client, no
# source system, and no duration: the horizon is week-denominated and this
# provider has no week-denominated source for it.
PLATFORM_LAYERS = (
    {"number": "01", "kicker": "INPUTS · FIELD CAPTURE",
     "title": "Capture the work one time, where it happens.",
     "body": "A guided capture workflow at the point of work, replacing the "
             "paper and spreadsheet steps that lose the detail."},
    {"number": "02", "kicker": "DATA FOUNDATION",
     "title": "Integrate it into one data store.",
     "body": "A cloud warehouse joining operational data to the financials, "
             "through connectors to the systems already in place."},
    {"number": "03", "kicker": "OUTPUTS · DASHBOARDS & ALERTS",
     "title": "Live performance, role by role.",
     "body": "Operations, leadership, and finance dashboards over the same "
             "data, each one drillable down to the day."},
)

NEXT_STEPS = (
    {"number": "01", "week": "WK 0", "owner": "CLIENT LEADERSHIP",
     "title": "Approve Project Plan",
     "body": "Leadership reviews and approves the platform scope and the "
             "phased plan before the build begins."},
    {"number": "02", "week": "WK 0", "owner": "QOFAI + CLIENT LEADERSHIP",
     "title": "Sign Performance-Based Contract",
     "body": "Execute the performance-based agreement: QofAI bears the "
             "upfront cost and is paid from realized EBITDA gains."},
    {"number": "03", "week": "WK 1–2", "owner": "QOFAI ENGINEERING",
     "title": "Kick Off QofAI FDEs",
     "body": "Forward-deployed engineers begin the build, standing up the "
             "data store, the connectors, and the first dashboard."},
    {"number": "04", "week": "WK 1–2", "owner": "OPERATIONS",
     "title": "Select The Pilot Team",
     "body": "Choose the team that runs the pilot and lock the first "
             "phase's scope with them."},
)

COMP_SCHEDULE = (
    {"year": "YEAR 1", "pct": 25},
    {"year": "YEAR 2", "pct": 15},
    {"year": "YEAR 3", "pct": 5},
    {"year": "YEAR 4+", "pct": 0},
)

# The contract declares three paths that MAY be templated, and
# `packet_document.TEMPLATED_DEFAULTS` is the closed constant holding them. Two
# are applied here.
#
# `commercial.comp_schedule` IS NO LONGER APPLIED (2026-09-23). It was the FBK
# performance-partnership schedule (20/10/5/0%), templated into every packet
# and printed by no role since `commercial_rows` replaced it, so it sat in each
# fill map unseen. Antonio retired that standard on 2026-09-22 ("it's outdated.
# I don't think we run all our financials like that."), and the adaptive slide
# carries a packet's own schedule as a terms row, so templating it now would
# put the retired schedule on every deck. The path stays declared TEMPLATED in
# the contract, which is permission rather than obligation, and its reason
# reads "not applied on this run". `COMP_SCHEDULE` above is kept as the
# contract's example value, unapplied.
TEMPLATED = (
    ("platform_layers", PLATFORM_LAYERS),
    ("next_steps", NEXT_STEPS),
)

# Two of those three moved from "always the deck standard" to "the deck standard
# only where the paper carries nothing" on 2026-08-19 (E11 Stage 2c), which is
# the human decision `packet_document._check_templated_is_closed`'s comment
# defers to and which Antonio made on 2026-08-18. They are held out of
# `fill_map` so the second pass sees them ABSENT and asks the paper for them,
# and `apply_templated_defaults` puts the deck standard back afterwards wherever
# the paper said nothing. `comp_schedule` is NOT one of them: QofAI's per-deal
# compensation is founder-set, no paper carries it, and it stays a deck standard
# unconditionally.
FALLBACK_DEFAULTS = ("platform_layers", "next_steps")

# The five week-denominated fields a months-only corpus cannot reach, and the one
# reason that says so. `packet_document._reasons` matches an entry to a slot path
# by exact name, so each is named at its own path.
WEEK_FIELDS = (
    "timeline.week_buckets[]",
    "timeline.total_weeks",
    "build_summary.duration_weeks",
    "timeline.phases[].workstreams[].start_week",
    "timeline.phases[].workstreams[].end_week",
)
UNIT_MISMATCH = (
    "This field is week-denominated, and the plan is rendered in whatever unit "
    "the paper's own chart states: all eight committed excerpts are "
    "month-denominated and the wider corpus also carries week-denominated "
    "plans. Converting between the two needs a multiplier, and choosing one is "
    "not this provider's call, so the span crosses at timeline.phases[].start / "
    "end / unit with its own unit beside it and this field stays absent."
)

# Section framing, a deck standard rather than company data. Each line names what
# its own section contains and nothing about this engagement: no client, no
# opportunity, no figure, no schedule, no outcome. Section 5 gets a headline and
# no summary because `map_packet` reads no section 5 subhead line.
PLATFORM_HEADLINE = "How the platform is built."
PLATFORM_SUMMARY = "What gets captured, where the data lands, and who sees it."
PLAN_HEADLINE = "The build, phase by phase."
PLAN_SUMMARY = "Each phase, its span, and the milestone that closes it."
TERMS_HEADLINE = "Commercial terms."
# Neutral, because the slide is an adaptive deal sheet (2026-09-23) and the old
# line ("What QofAI invests, what the client pays, and how compensation is
# scheduled") described the retired performance deal.
TERMS_SUMMARY = (
    "What the build costs, what it returns, and the terms of the engagement."
)
NEXT_STEPS_HEADLINE = "What happens next."
NEXT_STEPS_SUMMARY = "Each action has a named owner."


def fill_map(company=None, request=None, opportunity=None, packet=None):
    """The fill map and its source ledger, as `(fill, sources)`.

    `company` is the record `resolve_company` returned, `request` the contract
    request the caller sent, `opportunity` the detail `get_opportunity_details`
    returned, and `packet` the `Packet` E7a assembled. Every argument is
    optional: a caller with less to hand fills less, and the document says so.
    """
    company, request = company or {}, request or {}
    fill, sources = {}, {}

    def take(path, value, origin):
        """A value only enters the map when there is one; absence needs no entry."""
        if value not in (None, "", [], {}):
            fill[path] = value
            sources[path] = origin

    take("company.name", company.get("name"), LIST_COMPANIES)
    take("company.pe_firm", request.get("pe_firm"), REQUEST)
    for path, value in _baseline(packet).items():
        take(path, value, RESEARCH_PAPER)
    # THE OVERRIDE IS RECORDED, not merely performed. Antonio ruled on
    # 2026-09-20 that a PRD displacing a platform value wins SILENTLY and is
    # LOGGED, and the logging half was missing: slide 2's headline impact went
    # from the opportunity record's range to the PRD's with nothing anywhere
    # saying so. The displaced value travels in the origin string, so section
    # 8's own sources ledger carries both -- reviewer-facing, never rendered,
    # which is the channel `conflicts` and `set_aside` already use.
    take("target_metrics", _target_metrics(opportunity or {}, packet),
         _target_metrics_origin(opportunity or {}, packet))
    take("commercial.scenarios", _scenarios(packet), RESEARCH_PAPER)
    # §11.2's cost total, one row per cost column, labelled by the column.
    # A record list keyed by its container, bracketless, the same as
    # `commercial.scenarios` above.
    take("commercial.investment", _investment(packet), RESEARCH_PAPER)
    phases = _phases(packet)
    take("timeline.phases", phases, RESEARCH_PAPER)
    take("timeline.columns", _columns(phases), RESEARCH_PAPER)
    take("timeline.milestones", _milestones(phases, _stated_milestone_ids(packet)),
         RESEARCH_PAPER)
    # Section 2's build band reads the label alone: its siblings there are the
    # week-denominated horizon and a per-phase summary, neither of which the
    # chart carries, so the span would sit in that block with nothing to render
    # against. Slide 4 is where the span belongs and where section 5 puts it.
    take("build_summary.phases",
         [_record({"label": phase.get("label")}) for phase in phases],
         RESEARCH_PAPER)
    for path, value in _horizon(phases).items():
        take(path, value, DERIVED_HORIZON)
    # §14 READ DETERMINISTICALLY, where the document states it (2026-09-20).
    # This is the one path that leaves `FALLBACK_DEFAULTS`' hold-back early, and
    # only when a reader actually answered it. `next_steps` is held back so the
    # second pass can write it from prose, which is right for a published paper
    # and wrong for a PRD whose §14 states its steps, owners and day cadence in
    # labelled groups. Measured: the same PRD produced 11 steps on one run and 8
    # on another, and 11 overflowed the slide by 319px across 26 elements. A
    # count that changes between runs is not a fact about the document.
    #
    # Taken BEFORE the hold-back loop, so the pass is told this field is filled
    # and `apply_templated_defaults` leaves it alone. A document that states no
    # §14 puts nothing here and both of those behave exactly as they did.
    stated_steps = _stated_next_steps(packet)
    if stated_steps:
        take("next_steps", stated_steps, RESEARCH_PAPER)
    # §7 READ DETERMINISTICALLY, on exactly the same terms. These are the two
    # fields `FALLBACK_DEFAULTS` holds back, and both were inventing: slide 3
    # carried six components where the PRD states five, with lower-case titles
    # and descriptions that restated their own titles.
    stated_layers = _stated_platform_layers(packet)
    if stated_layers:
        take("platform_layers", stated_layers, RESEARCH_PAPER)
    # §3.1 READ DETERMINISTICALLY. `target_capabilities` reaches the deck by a
    # different route from the two above -- it is a `second_pass.STRING_PATHS`
    # entry rather than a `FALLBACK_DEFAULTS` one -- but the rule that governs
    # it is the same: the pass only fills what the deterministic side left
    # ABSENT, so taking it here is what stops a model writing slide 2's
    # capability bullets. It also hands `bullet_ranking` and `panel_fit` all
    # SEVEN goals the PRD states instead of the two or three a model chose,
    # which is how "Protect the onboarding constraint" came to be missing from
    # a deck whose PRD calls it the condition that governs everything.
    stated_caps = _stated_capabilities(packet)
    if stated_caps:
        # `target_capabilities[]`, WITH the brackets, and this is not a detail.
        # `packet_document._tree` looks a value up by its SLOT path, and the
        # slot path for a LIST OF SCALARS carries the brackets while a RECORD
        # list is keyed by its container without them -- which is why
        # `next_steps` and `platform_layers` above are bracketless and this is
        # not. `second_pass.FILL_KEY` states the same mapping and its comment
        # records the consequence of getting it wrong: the value lands in the
        # fill map and NOWHERE in the document, a silent drop rather than an
        # error. That happened on 2026-08-18 and it happened again here on
        # 2026-09-20: seven bullets sat in the map, the deck kept the two the
        # model had written, and every test passed. The render caught it.
        take("target_capabilities[]", stated_caps, RESEARCH_PAPER)
    for path, records in TEMPLATED:
        if path in FALLBACK_DEFAULTS:
            # Held back deliberately; `apply_templated_defaults` fills it after
            # the second pass has had its chance at the paper.
            continue
        take(path, [dict(record) for record in records], CONTRACT)
    return fill, sources


def _stated_milestone_ids(packet):
    """§12's milestone identifiers off the packet, in order."""
    for figure in _fields(packet):
        if figure.field == "milestone_ids":
            return [str(name) for name in figure.value or [] if name]
    return []


def _stated_capabilities(packet):
    """§3.1's goals off the packet, as a list of strings.

    A plain list, not records: `coverage_guard` maps `target_capabilities[]` to
    `after_capability_bullets` as a bare string list, and that is what the
    ranking pass orders and the panel fits.
    """
    for figure in _fields(packet):
        if figure.field == "target_capabilities":
            return [str(bullet) for bullet in figure.value or [] if bullet]
    return []


def _stated_platform_layers(packet):
    """§7's components off the packet, in the deck's own record shape.

    `kicker` is deliberately not emitted. It is an allowed leaf and the second
    pass was filling it with "three modules" on every component, which is the
    executive summary's framing repeated five times rather than a label for the
    component it sits on. Omitted reads as absent, which is true.
    """
    for figure in _fields(packet):
        if figure.field == "platform_layers":
            return [_record({key: (record.get(key) or None) for key in
                             ("number", "kicker", "title", "body")})
                    for record in figure.value or []]
    return []


def _stated_next_steps(packet):
    """§14's action items off the packet, in the deck's own record shape.

    `body` is the packet schema's name for the leaf, not `description`:
    `coverage_guard` holds the closed map and refuses a packet field with no
    template slot, which is how a renamed leaf was caught at the render rather
    than dropped out of the prompt in silence.

    A leaf the document did not state crosses as None, never as "". `_record`
    omits None so `_is_absent` reads it as absent and section 8 names it; an
    empty string would claim the field was supplied blank, which is a different
    and untrue claim. That matters here because two of the three PRDs head their
    interviews with a duration rather than a day range, so those steps
    genuinely have no week.
    """
    for figure in _fields(packet):
        if figure.field == "next_steps":
            return [_record({key: (record.get(key) or None) for key in
                             ("number", "week", "owner", "title", "body")})
                    for record in figure.value or []]
    return []


DERIVED_PATHS = ("build_summary.duration", "build_summary.duration_unit")


def _horizon(phases):
    """The plan's own horizon: the end of its last phase, in that phase's unit.

    The unblocking of `after_horizon` (E11 Stage 2c). The role reads
    `build_summary.duration_weeks`, which is week-denominated and which stays
    absent on a plan the paper states in months, so slide 2's AFTER band carried
    no horizon at all on a corpus that speaks in both units. Nothing is converted
    here either -- what crosses is the number the chart states and the unit it
    states it in, side by side, which is exactly the shape Stage 1 gave
    `timeline.phases[].start` / `end` / `unit` and for the same reason.

    DERIVED rather than read: the numbers are the paper's and the selection is
    ours, so section 8 reports these two under `derived` rather than beside the
    figures a parser lifted whole. The selection is the largest `end` the plan
    states, which is where the plan finishes; a paper listing its phases out of
    order still yields its own last boundary rather than whichever record came
    last.

    Refused, and each refusal leaves the field absent with its own gap rather
    than producing a horizon:

      * a plan whose phases do not all state an `end`, since the missing one may
        be the last;
      * a plan stating more than one unit across its phases, because reconciling
        months against weeks is the multiplier this provider does not choose.

    `_stated` is not reached from here. A chart-derived horizon is one number by
    construction -- `end` is a single number per phase, not a `(low, high)` pair
    -- so there is no range to carry. A paper that states its horizon as a range
    in PROSE states no single duration and `paper_extraction` returns nothing for
    it, which is that stage's own rule rather than a silent pick of an endpoint.
    """
    ends = [phase.get("end") for phase in phases]
    units = {phase.get("unit") for phase in phases}
    if not phases or any(end is None for end in ends) or len(units) != 1:
        return {}
    unit = units.pop()
    if not unit:
        return {}
    return {"build_summary.duration": _number(max(ends)),
            "build_summary.duration_unit": unit}


def apply_templated_defaults(fill, sources):
    """`(fill, sources, fell_back)` with the deck standard put back where the
    paper carried nothing.

    Runs AFTER `second_pass.merge_fill`, which is the whole point of the split:
    `fill_map` leaves `platform_layers` and `next_steps` empty so the second pass
    is told they are absent and reads the paper for them, and whichever of the
    two the paper did not carry gets its deck standard here rather than never
    being asked about. A thin paper still renders a complete deck; a paper that
    describes its own platform no longer has that description overwritten by a
    house template identical on every deck for every client.

    `fell_back` is the paths that took the deck standard on this run, which is
    what `packet_document.build` needs to say TEMPLATED rather than SOURCED in
    section 8 for those leaves. Without it a run whose slide 3 is the house
    template would report the same provenance as one whose slide 3 came out of
    the paper, and `role_coverage` would count a house template as coverage.

    New maps rather than edited ones, matching `merge_fill`'s own shape.
    """
    fill, sources = dict(fill or {}), dict(sources or {})
    fell_back = []
    for path, records in TEMPLATED:
        if path not in FALLBACK_DEFAULTS or fill.get(path):
            continue
        fill[path] = [dict(record) for record in records]
        sources[path] = CONTRACT
        fell_back.append(path)
    return fill, sources, tuple(fell_back)


def copy_lines(company=None, request=None, project=None, opportunity=None,
               written=None):
    """The document's markdown copy lines, keyed by section number.

    `map_packet` reads the cover bullets and the `Headline:` / `Subhead:` lines
    out of a section's prose rather than its yaml, so this is the only channel
    those roles have. Sections 1 and 2 carry real data and appear only when there
    is some; sections 4 through 7 carry the framing constants and always appear.

    `written` is the generated framing (E11 Stage 2c, 2026-08-19): a map from
    `paper_writing` copy path to the sentence that pass wrote and verified. A
    path with an entry uses that sentence; a path WITHOUT one keeps the constant
    below, which is why a refused sentence costs a deck nothing. The constants
    are the fallback now rather than the answer, and they are still the answer on
    every run with no writing pass, which is every run in this suite except the
    ones that inject a fake.

    Slide 6's own two framing lines are deliberately NOT generated. QofAI's
    commercial terms are founder-set, no paper carries them, and the five
    commercial fields are designed to read AWAITING COMMERCIAL TERMS INPUT;
    writing engagement-specific framing around a slide whose data is deliberately
    blank is the one thing that slide must not do.
    """
    company, request = company or {}, request or {}
    written = written or {}

    def framed(path, constant):
        return written.get(path) or constant

    lines = {}
    cover = _cover(company, request, project or {}, written)
    if cover:
        lines[1] = cover
    # Section 2's copy is NOT here any more (item 15, 2026-09-13). It moved into
    # each opportunity's own entry inside section 2's yaml, because that section
    # repeats once per opportunity and prose sits between a heading and its
    # yaml, with no way to pair N prose blocks with N entries.
    # `opportunity_copy` below is where those two lines are now built.
    per_slide = slide_copy(written)
    lines[4] = [
        f"Headline: **{per_slide[4]['headline']}**",
        f"Subhead: *{per_slide[4]['summary']}*",
    ]
    lines[5] = [
        f"Headline: **{per_slide[5]['headline']}**",
        f"Subhead: *{per_slide[5]['summary']}*",
    ]
    lines[6] = [f"Headline: **{TERMS_HEADLINE}**", f"Subhead: *{TERMS_SUMMARY}*"]
    lines[7] = [
        "Headline: "
        f"**{per_slide[7]['headline']}** "
        f"*{per_slide[7]['summary']}*"
    ]
    return lines


# The copy paths behind each per-opportunity slide's headline and summary,
# keyed by packet section. Slide 5's framing is not here, for the reason
# `copy_lines` gives: commercial terms are founder-set and not written.
SLIDE_COPY_PATHS = {
    4: (("copy.platform_headline", PLATFORM_HEADLINE),
        ("copy.platform_summary", PLATFORM_SUMMARY)),
    5: (("copy.plan_headline", PLAN_HEADLINE),
        ("copy.plan_summary", PLAN_SUMMARY)),
    7: (("copy.next_steps_headline", NEXT_STEPS_HEADLINE),
        ("copy.next_steps_summary", NEXT_STEPS_SUMMARY)),
}


def slide_copy(written=None):
    """The Platform, Timeline and Next Steps headline and summary, per section.

    `{4: {"headline", "summary"}, 5: ..., 7: ...}`: the writing pass's sentence
    where it wrote one, the deck standard where it did not. ONE STATEMENT of the
    rule `copy_lines` renders as prose for the flat sections, so a deck carrying
    several opportunities gives each of those slides its own opportunity's
    framing by the same rule the first one gets (2026-09-22).
    """
    written = written or {}
    return {
        section: {"headline": written.get(headline) or headline_default,
                  "summary": written.get(summary) or summary_default}
        for section, ((headline, headline_default), (summary, summary_default))
        in SLIDE_COPY_PATHS.items()
    }


def opportunity_copy(opportunity=None, written=None):
    """Slide 2's own two copy lines, for one opportunity's section 2 entry.

    The headline is the opportunity's own title, which is what the slide is
    called, and the one-liner is the writing pass's framing sentence where one
    was written. Both were prose under the section heading until 2026-09-13 and
    are yaml inside the entry now, for the reason `copy_lines` above states: a
    section that repeats cannot pair N prose blocks with N yaml entries.

    A key with no value is omitted rather than emitted empty, which is the same
    rule the prose followed (no title, no `Headline:` line at all).
    """
    written = written or {}
    copy = {}
    title = (opportunity or {}).get("title")
    if title:
        copy["headline"] = title
        summary = written.get("copy.opportunity_summary")
        if summary:
            copy["one_liner"] = summary
    return copy


def opportunity_record(opportunity):
    """Section 3's payload: the opportunity's own record, description and all.

    Section 3 is the contract's home for analytical source data, excluded from
    the coverage walk and parsed by nothing on the proposal path, so the
    description (1,772 characters on the one real record measured 2026-08-15)
    sits here verbatim for F1 rather than being compressed into a subhead by a
    step that carries no diff guard.
    """
    opportunity = opportunity or {}
    record = _record({
        key: opportunity.get(key) for key in ("id", "title", "description")
    })
    return [record] if record else []


def role_coverage(placeholder_map):
    """The share of the 18 render roles carrying real platform data, rounded.

    Measured on what `map_packet` produced rather than on the fill map, because a
    role can hold a sourced field and still render nothing: `build_summary`
    carries E4's real phase label and still needs `duration_weeks`, which is
    week-denominated and which no chart supplies. Counting it would overstate
    what the deck shows and understate the ask to Agent OS.

    Reported and never gated, per `E9-RESCOPE-DESIGN.md` section 3.4. It goes in
    section 8, which feeds no role, so rebuilding the document with this number
    changes nothing any consumer reads.

    Read off the document's own section 8 `from_kg` since E11 Stage 2c, rather
    than off the static `ORIGINS` table. The two are the same answer for every
    path whose class is a fact about the schema, and they are deliberately
    different for the two record lists whose class is a fact about the RUN:
    `platform_layers` and `next_steps` are paper-sourced where the paper carried
    them and the contract's deck standard where it did not. Off the table, a
    deck rendering the house platform would have counted as coverage and this
    number would have risen by 0.11 for showing exactly what it showed before.
    `from_kg` is `filled AND effectively SOURCED`, and the gap set is the
    complement of `filled`, so nothing else about the count moves.

    What this number still cannot see, and Stage 2e measured it: a role counts as
    carrying when ANY of its paths is sourced and filled, so `value_mapping` read
    as carrying while every figure in its cells was blank, because
    `scenarios[].name` was never absent. Report cells-that-render beside the
    ratio; neither number is the whole answer alone.
    """
    provenance = placeholder_map.get("_provenance") or {}
    # `from_kg` and `derived` are disjoint and their UNION is "carries real
    # platform data": the build horizon is the plan's own last boundary, the
    # paper's number in the paper's unit, and it counts here even though this
    # layer selected it rather than a parser lifting it whole.
    from_kg = set(provenance.get("from_kg") or ()) | set(
        provenance.get("derived") or ()
    )
    roles = sorted(set(COVERAGE_MAP.values()))
    carrying = [
        role for role in roles
        if placeholder_map.get(role)
        and any(path in from_kg
                for path, named in COVERAGE_MAP.items() if named == role)
    ]
    return round(len(carrying) / len(roles), 2)


def record_unit_mismatch(packet):
    """`packet` with the five week-denominated absences given their real reason.

    A new `Packet` rather than an edited one, since E7a's is frozen by design.
    `packet_document` already reads `missing_fields` for the reason a field is
    absent, so this says why the timeline does not cross into the schedule
    without the document builder learning about units.
    """
    if packet is None:
        return None
    return dataclasses.replace(
        packet,
        missing_fields=tuple(packet.missing_fields)
        + tuple((field, UNIT_MISMATCH) for field in WEEK_FIELDS),
    )


def _cover(company, request, project, written=None):
    """The section 1 cover bullets, each one only where its own source exists.

    `Subhead` is the cover subtitle and is written rather than quoted (E11 Stage
    2c): it compresses the opportunity description, which section 3 still parks
    verbatim, under the generated rules in `paper_writing`. With no writing pass
    it is simply absent, exactly as it was before, and the deck marks it. It is
    never composed here from anything else.

    The eyebrow carries the adapter's own deck-type label because `map_packet`
    derives `deck_type_label` back out of its leading segment.
    """
    date = _fmt_deck_date(request.get("proposal_date") or "")
    bullets = [
        ("Eyebrow", _joined(DECK_TYPE_LABEL_DEFAULT, date)),
        ("Prepared for", _joined(*(
            part.upper() for part in (company.get("name"), request.get("pe_firm"))
            if part
        ))),
        ("Title", project.get("name")),
        ("Subhead", (written or {}).get("copy.subtitle")),
    ]
    return [f"- **{label}:** `{value}`" for label, value in bullets if value]


def _joined(*parts):
    """Parts on the deck's own separator, skipping the ones that have no source."""
    return " · ".join(part for part in parts if part)


def _baseline(packet):
    """The three baseline figures E7a derived, under their own packet paths."""
    figures = {figure.field: figure for figure in _fields(packet)}
    return {
        path: _one(figures[path].value)
        for path in (packet_assembly.REVENUE, packet_assembly.EBITDA,
                     packet_assembly.MARGIN)
        if path in figures
    }


def _scenarios(packet):
    """E5b's cases, name and the two figures the paper stated.

    The other four leaves of a scenario are QofAI's own per-deal arithmetic and
    reviewer input by design, so each record is partial and each unfilled leaf
    keeps its own gap.

    Both figures cross as the paper stated them, range and all (E11 Stage 2e).
    These are the two leaves whose cell on slide 5 can hold two numbers, and
    papers state both as ranges constantly, so `_stated` reads them rather than
    `_one`. Nothing is converted and no unit is written here: `_scenario_ebitda_
    gain` composes the cell and names each unit once.
    """
    paybacks = _stated_payback(packet)
    return [
        _record({
            "name": case.label,
            "margin_gain_pp": (
                _stated(case.margin_gain_pp.value) if case.margin_gain_pp else None
            ),
            "direct_uplift_usd_yr": _stated(case.direct_uplift_usd_yr.value),
            # §11.2's payback for THIS case, joined on the case's own label the
            # way `packet_assembly._prd_margin` joins a margin, so a
            # conservative payback cannot land on an ambitious row. The PRD's
            # own range text ("4.1–6.0 months"), never reformatted.
            "payback": (paybacks.get((case.label or "").strip().lower())
                        or {}).get("text"),
        })
        for case in getattr(packet, "scenarios", ()) or ()
    ]


def _stated_payback(packet):
    """§11.2's payback per case off the packet, keyed by lower-cased label."""
    for figure in _fields(packet):
        if figure.field == "commercial.payback":
            return dict(figure.value or {})
    return {}


def _investment(packet):
    """§11.2's total row as the slide's INVESTMENT records.

    One record per cost column the PRD's table has, labelled by the column
    header and valued with the PRD's own cell text. ``basis`` rides on each
    record rather than as a scalar beside the list, because an absent scalar
    prints `null` into every packet, including every paper-sourced one that
    will never have a cost table.
    """
    for figure in _fields(packet):
        if figure.field == "commercial.cost_total":
            value = figure.value or {}
            basis = value.get("basis") or None
            return [_record({"label": row.get("label") or None,
                             "value": row.get("value") or None,
                             "basis": basis})
                    for row in value.get("rows") or ()]
    return []


def label_scenarios(rows, opportunity):
    """The same rows, each saying which opportunity it belongs to.

    Slide 5's value-mapping table is one combined set for the deck, because
    QofAI contracts the engagement rather than the opportunity, and the rows in
    it are per opportunity and never summed (Antonio, 2026-09-13): adding two
    opportunities' uplift together would print a figure neither paper states,
    which is what `base_document.merge_packets` already refuses for a scenario
    table.

    So the opportunity becomes one more LABEL on a row, in the spirit of
    `commercial_rows` on the same slide. That role is specified as named rows
    rather than fixed fields precisely so a flat fee, a retainer plus a success
    fee or a performance schedule are all just rows, with no code change to
    express any of them. Antonio's instruction of 2026-09-13 is the same one:
    he does not know whether QofAI still uses conservative, base and optimistic
    in its pay plans, the compensation breakdown varies by client, and the agent
    should absorb different shapes rather than assume one. Nothing here is fixed
    at three cases or at any case name. Whatever cases a document states become
    rows, and a second opportunity's cases become more rows that say whose they
    are.
    """
    return [dict(row, opportunity=opportunity) for row in rows or ()]


def _phases(packet):
    """E4's phases: the label the chart states, and the span it drew.

    The span crosses now (E11 Stage 1). `chart_timeline_parser` already returns
    `start`, `end` and `unit` per phase with the config text as the source span,
    and this layer used to keep the label and drop the other three, which left
    every consumer downstream with a name and no plan. The unit rides beside the
    numbers rather than being converted into the contract's weeks.

    A phase is still not decomposed into workstreams: no `Workstream` or `Task`
    label exists in any graph to decompose it from.
    """
    for figure in _fields(packet):
        if figure.field == packet_assembly.TIMELINE:
            return [
                _record({key: phase.get(key)
                         for key in ("label", "start", "end", "unit")})
                for phase in figure.value
            ]
    return []


def _columns(phases):
    """The plan's column axis, one entry per phase span, in the chart's unit.

    The contract's own axis field is `timeline.week_buckets`, week-denominated
    and UNSOURCEABLE, so the axis had exactly one mapped path and no source for
    it. This is the sourced one: it is the chart's own phase spans read as the
    time axis they already are, carrying the chart's numbers unchanged and its
    unit beside them. Nothing is re-based and nothing is bucketed into a unit
    the paper did not state.
    """
    return [
        _record({"label": _span(phase), "unit": phase.get("unit")})
        for phase in phases if _span(phase)
    ]


def _milestones(phases, ids=()):
    """One milestone per phase boundary: the end of each phase the chart drew.

    Position and unit, plus the document's own IDENTIFIER where it states one.
    The ordinal label a reader sees (`MILESTONE 1`) is deck framing composed in
    `map_packet`, in the same class as `after_horizon`'s "AFTER — TARGET IN ~N
    WEEKS"; naming an OUTCOME for a boundary, the way "M1 · Pilot Validated"
    does, would assert something the paper never stated, and Antonio drew that
    line on 2026-08-18. That line is unchanged: an id is what the document
    CALLS the milestone, not a claim about what it achieves.

    `ids` is §12's first column and is why this argument exists. Antonio,
    2026-09-20: the deck numbered these M1..M5 where his PRD numbers them
    M0..M4, because `data_source_adapter._milestone_role` falls back to
    "M{ordinal}" when a record states no id. Paired BY INDEX, which is sound
    because `prd_section_parsers.cross_check` has already refused the plan if
    §6 and §12 disagree about their spans -- so "the nth milestone closes the
    nth phase" is a checked fact rather than an assumption. A document stating
    no ids passes an empty tuple and every milestone keeps the ordinal it had.

    A boundary already recorded is not recorded twice: two phases ending on the
    same number are one milestone, not two identical ones.
    """
    milestones, seen = [], set()
    for phase in phases:
        end = phase.get("end")
        if end is None or end in seen:
            continue
        # The id belongs to the phase's ORDINAL POSITION in the plan, not to the
        # count of milestones kept, so a plan whose phases share a boundary does
        # not shift every later id by one.
        index = phases.index(phase)
        seen.add(end)
        milestones.append(
            _record({"id": ids[index] if index < len(ids) else None,
                     "position": _number(end), "unit": phase.get("unit")})
        )
    return milestones


def _span(phase):
    """A phase's span as the chart stated it, or None where it stated half of it.

    The numbers are the chart's own. One paper labels a phase "Months 1-3" over
    a bar drawn from 0 to 3; re-basing either onto the other would manufacture a
    boundary no paper stated, which is the same class of error as inventing a
    figure.
    """
    start, end = phase.get("start"), phase.get("end")
    if start is None or end is None:
        return None
    return f"{_number(start)}–{_number(end)}"


def _target_metrics_origin(opportunity, packet):
    """Which source answered `target_metrics`, naming what it displaced.

    `OPPORTUNITY_DETAILS` where the record answered, and where an uploaded
    document displaced it, a string carrying the record's own range as well. A
    reviewer reading the ledger can then see that the deck says one thing
    because the document said so and the platform said another, which is the
    whole of what an override needs to be auditable.
    """
    uploaded = _uploaded_impact(packet)
    if not uploaded:
        return OPPORTUNITY_DETAILS
    impact = (opportunity or {}).get("ebitda_impact") or {}
    low, high, unit = impact.get("min"), impact.get("max"), impact.get("unit")
    if low is None or high is None or not unit:
        # Nothing was displaced: the record had no range to begin with.
        return RESEARCH_PAPER
    stated = f"{low}{unit}" if low == high else f"{low}\u2013{high}{unit}"
    return (f"{RESEARCH_PAPER} (displaced {OPPORTUNITY_DETAILS}: {stated})")


def _uploaded_impact(packet):
    """The impact range an UPLOADED document states, as a display string, or "".

    THE THIRD CHANNEL, and the one that made a corrected deck still read wrong.
    Everything else on this slide arrives through the document precedence chain.
    `target_metrics` does not: it is taken off the opportunity record, so on
    2026-09-20 a deck whose timeline had been corrected to the PRD's ten weeks
    still headlined the RECORD's "0.62-1.24pp" while the same deck's scenario
    table carried the PRD's $74,000 and $370,000. Those cannot both be true of
    one revenue base, and a reviewer had no way to see which half was which.

    So an uploaded document's own margin figures win here, on the same rule
    Antonio set for every other field. Scoped deliberately to UPLOADED
    documents: a published paper's margins keep arriving through the record, so
    every run without an attachment is unchanged.
    """
    # An UPLOAD is told from the paper by having a filename. `base_document.
    # paper_source` builds the published paper as `Document(name="", ...)` and
    # `uploaded_source` builds an attachment with its own filename, so the name
    # is the distinguishing property and reading it costs this module no import.
    # `tests/test_provenance_never_renders.py` pins the import set of every
    # module on the render path, and importing `base_document` here to reach a
    # kind constant broke it.
    stated = []
    for case in getattr(packet, "scenarios", ()) or ():
        figure = getattr(case, "margin_gain_pp", None)
        document = getattr(figure, "document", None)
        if figure is None or document is None:
            continue
        if not getattr(document, "name", ""):
            continue
        # E7a carries every figure as a `(low, high)` pair, so a range
        # contributes both endpoints and a single figure contributes one number
        # twice. Reading `.value` as a scalar is what broke 36 tests the first
        # time this was written.
        value = figure.value
        stated.extend(float(end) for end in
                      (value if isinstance(value, (tuple, list)) else (value,)))
    if not stated:
        return ""
    low, high = min(stated), max(stated)
    # Both halves formatted the same way, for the reason `_target_metrics`
    # gives: a range whose halves disagree about their own precision reads as
    # two different measurements.
    return f"{low:.2f}pp" if low == high else f"{low:.2f}–{high:.2f}pp"


def _target_metrics(opportunity, packet=None):
    """`ebitda_impact` as the one metric the packet declares, value only.

    An uploaded document outranks the opportunity record, per `_uploaded_impact`.
    """
    uploaded = _uploaded_impact(packet)
    if uploaded:
        return [{"value": uploaded}]
    impact = opportunity.get("ebitda_impact") or {}
    low, high, unit = impact.get("min"), impact.get("max"), impact.get("unit")
    if low is None or high is None or not unit:
        return []
    # The bounds are carried as the tool stated them: this is a display string
    # rather than a number, and re-rounding one half of a range would make the
    # two halves disagree about their own precision.
    stated = f"{low}{unit}" if low == high else f"{low}–{high}{unit}"
    return [{"value": stated}]


def _fields(packet):
    return getattr(packet, "fields", ()) or ()


def _record(leaves):
    """A record carrying only the leaves that have a value.

    An unfilled leaf is omitted rather than nulled, so `_is_absent` reads it as
    absent and section 8 names it. A `null` inside a record would say the field
    was supplied empty, which is a different and untrue claim.
    """
    return {key: value for key, value in leaves.items() if value is not None}


def _one(pair):
    """One number from E7a's `(low, high)` pair, or None where it is a range.

    Every field this reaches declares a single number. A paper that stated a
    range leaves the field absent with its gap rather than having an endpoint
    picked for it.

    Still the reader for the three baseline figures, and deliberately so. E11
    Stage 2e added `_stated` beside it rather than widening this, because some
    deck slots genuinely cannot show two numbers and changing this contract in
    place would have moved every one of them at once. Whether a baseline figure
    should print as a range is a deck-design question, measured in that stage's
    CHANGELOG entry and left for Antonio.
    """
    low, high = pair
    return _number(low) if low == high else None


def _stated(pair):
    """Both endpoints of E7a's `(low, high)` pair, as the paper stated them.

    The range-aware sibling of `_one`, added by E11 Stage 2e for the two scenario
    leaves. Before it, `_one` returned None for any pair whose endpoints differ,
    so a paper stating `0.9–1.9pp` produced a correct, sourced, span-verified
    range and then rendered nothing: on the two committed excerpts that state
    their scenario uplift as a range, slide 5's whole EBITDA-gain column came out
    blank for all six cases. The loss was display-only, since `Packet.present`
    adds a roster field when a scenario merely HAS the figure and never inspects
    whether it is a range, which is why this moves no gate and no score.

    A single stated figure returns exactly what `_one` returns, so nothing that
    was a number becomes a string and no cell that already rendered moves. A
    range returns a display string carrying both endpoints on the deck's own en
    dash, the form `_target_metrics` writes one field over and `after_metric_1`
    already shows.

    What this does not do, and each one is a rule of the stage. No endpoint is
    picked, not the high one and not a midpoint. No arithmetic: neither
    endpoint's precision is normalised onto the other's, so `1.2–6` keeps the
    decimal on one half and drops the trailing zero on the other exactly as each
    would alone. No unit is written, because each consumer names its own once
    after the second endpoint (`+{pp}pp` on the scenario cell, `$…` per endpoint
    in the deck's dollar-range house style). And the pair's ORDER is the
    reader's: `read_pp` and `_money` both return `(min, max)`, so a range a paper
    states backwards has already been ordered by the time it arrives here, and
    Stage 2d owns that reading.
    """
    low, high = pair
    if low == high:
        return _number(low)
    return f"{_number(low)}–{_number(high)}"


def _number(value):
    """An integral float as an int, so a whole-dollar figure reads as one."""
    return int(value) if float(value).is_integer() else value
