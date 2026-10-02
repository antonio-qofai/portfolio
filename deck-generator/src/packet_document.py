"""The packet document (E9b, structure and absence).

The missing half of the provider. `packet_assembly` decides what a paper-derived
FIELD is; this module decides what the whole DOCUMENT is, and emits the
contract's full packet markdown (frontmatter plus sections 0 through 8) in the
shape of `proposal-data-packet-EXAMPLE.md`. Nothing here parses a paper and
nothing here computes a figure.

Three layers and no more.

1. `SLOTS`, a declarative table with one entry per contract leaf in sections 1
   through 7, carrying the packet path, the section, the leaf's type, and its
   origin class. This is the only place the packet's shape is written down, so a
   richer Agent OS is a data change here rather than a rewrite.
2. A fill map from packet path to value, passed in. Empty in this window: no
   value is sourced, so every slot is absent. E9c supplies it.
3. `_emit`, a renderer that walks the table in section order and writes each
   section's fenced YAML in the type each slot declares.

Absence is type-preserving, which is a correctness constraint rather than a
style preference. `coverage_guard._walk_leaves` reads a null scalar as a leaf at
its own path, so `today_pain_points: null` produces the unaccounted leaf
`today_pain_points` and `check_coverage` raises, while `today_pain_points: []`
produces the slotted leaf `today_pain_points[]` and passes. Reproduced
2026-08-13 before this module was written. So an absent scalar emits `null`, an
absent list of scalars emits `[]`, and an absent block emits its own leaves
absent in their own types rather than `null` on the block itself.

One correction to `E9-RESCOPE-DESIGN.md` section 3.1, measured the same way. Its
rule that an absent list of RECORDS also emits `[]` is wrong: the same walk
collapses `today_metrics: []` to the leaf `today_metrics[]`, which is in neither
`COVERAGE_MAP` (holding `today_metrics[].value`) nor `EXCLUSION_ALLOWLIST`, so
`check_coverage` raises. An absent record list is therefore omitted from the
document entirely. That is also what the rule against emitting a section 1
through 7 leaf the frozen fixtures do not already emit requires, since no
fixture emits a bare `foo[]` for a record list.

Absence is marked in two places and always both: the typed value above, which is
what a human reading the packet sees, and the path in section 8 `gaps` as
`{field, reason}`, which is what the deck sees. `_gap_flagged_roles` turns a gap
path into a role and `prompt_assembler` renders an empty role as
`[MISSING: role]`. A field absent in the value but missing from `gaps` would be
invisible on the deck, so the two sets are asserted equal in the tests.

Not hardcoded to any client. `SLOTS` is keyed on schema paths and every value
arrives through the fill map, the request echo, or the `Packet`.
"""

import completeness_score

SCHEMA_VERSION = "0.1"
PACKET_TYPE = "project_planning_proposal"
GENERATED_BY = "proposal-data-provider/packet_document"
TITLE = "# Proposal Data Packet"

# A slot's declared type. `RECORDS` is not a leaf type: a record list is written
# in a path as a `[]` segment with children hanging off it.
SCALAR = "scalar"
LIST = "list"

# The four origin classes, which are the vocabulary for absence.
SOURCED = "SOURCED"
TEMPLATED = "TEMPLATED"
REVIEWER = "REVIEWER"
UNSOURCEABLE = "UNSOURCEABLE"

# Closed constant, read off the contract's own section 8 `templated_defaults`
# (`proposal-data-packet-EXAMPLE.md`). Exactly these three paths may carry the
# TEMPLATED class; a fourth templated default is a decision for a human, not a
# judgment this module gets to make. `_check_templated_is_closed` enforces it at
# import time so the table cannot drift.
TEMPLATED_DEFAULTS = ("platform_layers", "next_steps", "commercial.comp_schedule")

# One reason per origin class. A roster field E7a already recorded an absence for
# passes its own words through instead (see `_reasons`).
REASONS = {
    SOURCED: (
        "The provider can source this field, but no value reached the "
        "assembler for this run, so it is absent rather than defaulted."
    ),
    TEMPLATED: (
        "Deck-standard templated default (contract section 8 "
        "templated_defaults), not company data; not applied on this run."
    ),
    REVIEWER: (
        "Reviewer input by design. QofAI's own per-deal terms are not "
        "platform data and the provider was never meant to supply them."
    ),
    UNSOURCEABLE: (
        "No source exists. No tool in the grant returns this field and no "
        "label in the graph carries it."
    ),
}

# SECTION 2 REPEATS (item 15, 2026-09-13). A deck can carry more than one
# opportunity, and the today-versus-after story is the slide that repeats,
# because each opportunity has its own. So section 2's yaml is a LIST under this
# key, one entry per opportunity, and its slot paths are emitted INSIDE each
# entry rather than at the section's root.
#
# Always a list, including for one opportunity. Two shapes for one thing is a
# branch, and this repo has declined that everywhere else; the cost is that the
# packet markdown moves once, is documented, and the DECK does not move at all
# for a single-opportunity run.
#
# The key echoes section 3's own `opportunities`, which holds the platform's
# source records for the same opportunities. They never meet: sections are split
# by number before any yaml is read, section 3 is parsed by nothing on the
# proposal path, and the coverage walk never reaches it. One word for one set of
# things is worth more than avoiding the echo.
OPPORTUNITY_SECTIONS = "opportunities"

# The copy channel inside an opportunity's entry, which is not a slot and is
# deliberately not one. `coverage_guard`'s own docstring excludes prose copy from
# the walk because those lines carry no field path; these two DO carry a path now
# that they sit in yaml, so they are allowlisted there rather than slotted, which
# keeps them out of section 8's gaps exactly as they were when they were prose.
#
# They moved out of the prose for one reason: prose sits between a heading and
# its yaml, and a section that repeats has no way to pair N prose blocks with N
# yaml entries. The status packet already carries its repeating slide's copy as
# yaml fields for the same reason.
OPPORTUNITY_COPY = ("headline", "one_liner")

SECTION_TITLES = {
    0: "Request Context (what triggered this packet)",
    1: "Company Profile & Baseline",
    2: "The Opportunity",
    3: "Opportunities & Priority Initiatives (KG core)",
    4: "The Platform",
    5: "Phased Rollout",
    6: "Commercial Terms",
    7: "Next Steps",
    8: "Provenance & Gaps  *(not rendered — for the agent's guardrails)*",
}

# The slot table, grouped by (section, type, origin) so the four columns stay
# explicit without one line per leaf. Flattened into `SLOTS` below, one entry per
# contract leaf. Every path here is a leaf the frozen fixtures already emit;
# adding one they do not is a CoverageError, and the fix would be an edit to
# `coverage_guard.py`, which is out of bounds.
_SLOT_GROUPS = (
    # §1 Company Profile & Baseline.
    (1, SCALAR, SOURCED, ("company.name", "company.pe_firm")),
    (1, SCALAR, UNSOURCEABLE, (
        "company.client_short", "company.legal_name", "company.sector",
        "company.hq", "company.employees", "company.fleet_size",
        "company.active_projects",
    )),
    (1, SCALAR, SOURCED, (
        "baseline.revenue_ttm_usd", "baseline.adjusted_ebitda_usd",
        "baseline.adjusted_ebitda_pct",
    )),
    (1, SCALAR, REVIEWER, ("baseline.baseline_locked_date",)),
    (1, SCALAR, UNSOURCEABLE, (
        "baseline.ebitda_volatility_note", "baseline.reporting_lag",
    )),

    # §2 The Opportunity. Reclassified 2026-08-18 (E11 Stage 2), per path and
    # never in bulk. The today-versus-after narrative reads UNSOURCEABLE here
    # until this window, on a reason ("no tool in the grant returns this field
    # and no label in the graph carries it") that is a claim about STRUCTURED
    # tool output and graph labels. It was never a claim about the research
    # paper, which `get_opportunity_details` returns, which is in the grant, and
    # which the parsers already read for its charts and its tables. What was
    # true is that nothing read its PROSE. `paper_extraction` does, under span
    # discipline, so these five paths have a source and say so.
    #
    # The evidence, per E11's 12-paper corpus survey of 2026-08-18: the papers
    # consistently carry `Financial Analysis > Current State`, `Financial
    # Analysis > Projected Impact`, `The Proposed Solution`, `Technical
    # Requirements` and `Implementation Approach > Timeline`, at an average of
    # 5.2 of 6 mappable sections present. SOURCED does not promise a value on
    # every paper -- a paper missing a section is a normal outcome and renders
    # as a marker -- it promises that a source EXISTS, and its own reason says
    # "no value reached the assembler for this run" rather than "no source
    # exists", which is the honest sentence for a paper that states nothing here.
    (2, SCALAR, SOURCED, ("today_metrics[].value", "today_metrics[].label")),
    (2, LIST, SOURCED, ("today_pain_points[]",)),
    (2, SCALAR, SOURCED, ("target_metrics[].value",)),
    # NOT reclassified, and deliberately. `target_metrics[].value` is
    # `ebitda_impact`, a figure a tool returns with no name attached, so its
    # label is a unit-naming string rather than something the paper states about
    # it. Antonio ruled on it 2026-08-15 and E9c left it to him; a window that
    # widens extraction is not where that gets overturned.
    (2, SCALAR, UNSOURCEABLE, ("target_metrics[].label",)),
    (2, LIST, SOURCED, ("target_capabilities[]",)),
    # E4's phase label is real; the duration is week-denominated and no chart
    # states a duration in weeks, and the internal id has no source. The phase
    # SPAN is section 5's, where the axis it belongs to lives; this block would
    # carry it with nothing to render it against. The per-phase summary is the
    # fifth path reclassified above: the papers state what each phase does, in
    # prose, next to the phase label the first pass already read.
    (2, SCALAR, SOURCED, ("build_summary.phases[].label",
                          "build_summary.phases[].summary")),
    (2, SCALAR, UNSOURCEABLE, (
        "build_summary.duration_weeks", "build_summary.phases[].id",
    )),
    # The horizon, unit-neutral, added 2026-08-19 (E11 Stage 2c). The contract's
    # own `duration_weeks` above stays UNSOURCEABLE and stays absent, because it
    # is week-denominated and converting a months-denominated plan into it needs
    # a multiplier this provider does not get to choose. These two carry the
    # number and the unit the PAPER states, beside each other, which is the same
    # shape Stage 1 gave `timeline.phases[].start` / `end` / `unit` and the same
    # refusal to convert. `duration` is the end of the plan's own last phase
    # where the paper drew a plan, and the horizon the paper states in prose
    # where it drew none.
    (2, SCALAR, SOURCED, (
        "build_summary.duration", "build_summary.duration_unit",
    )),

    # §4 The Platform. Reclassified 2026-08-19 (E11 Stage 2c) from TEMPLATED to
    # SOURCED, and this is the human decision `_check_templated_is_closed`'s own
    # comment defers to rather than a judgment this module took: Antonio made it
    # on 2026-08-18 when he asked for the whole deck to be filled from the
    # source. The paper's `The Proposed Solution` and `Technical Requirements`
    # describe the platform THIS engagement would build, in prose, and
    # `paper_extraction` reads prose under span discipline. So a source exists,
    # which is all SOURCED claims -- and `number` is the record's own ordinal
    # over the components the paper stated, the same ordinal-label call
    # `timeline.milestones` carries.
    #
    # The deck standard is not gone, it is the FALLBACK: a paper that describes
    # no platform still renders three complete layers, and on that run these
    # leaves are TEMPLATED again through `build(templated_fallbacks=...)`. The
    # class is therefore decided per run, because "the house template rendered"
    # and "the paper's own platform rendered" are different provenance and
    # counting them alike would let `role_coverage` read a template as coverage.
    (4, SCALAR, SOURCED, (
        "platform_layers[].number", "platform_layers[].kicker",
        "platform_layers[].title", "platform_layers[].body",
    )),

    # §5 Phased Rollout. The contract's own schedule fields are all
    # week-denominated. All eight committed excerpts are month-denominated and
    # the wider corpus carries week-denominated plans too (E11's 12-paper survey,
    # 2026-08-18, correcting an earlier claim here that the corpus was months
    # only), so picking a multiplier is still not this provider's call and the
    # week-denominated fields stay absent. What the chart does state crosses
    # under its own unit instead: the axis, each phase's span, and each phase
    # boundary. A milestone's id and label stay absent because the chart names no
    # outcome for a boundary; the ordinal a reader sees is deck framing composed
    # in `map_packet`, not a claim this document makes.
    (5, LIST, UNSOURCEABLE, ("timeline.week_buckets[]",)),
    (5, SCALAR, UNSOURCEABLE, ("timeline.total_weeks",)),
    (5, SCALAR, SOURCED, (
        "timeline.columns[].label", "timeline.columns[].unit",
    )),
    (5, SCALAR, SOURCED, (
        "timeline.phases[].label", "timeline.phases[].start",
        "timeline.phases[].end", "timeline.phases[].unit",
    )),
    (5, SCALAR, UNSOURCEABLE, (
        "timeline.phases[].id",
        "timeline.phases[].workstreams[].name",
        "timeline.phases[].workstreams[].detail",
        "timeline.phases[].workstreams[].start_week",
        "timeline.phases[].workstreams[].end_week",
    )),
    (5, SCALAR, SOURCED, (
        "timeline.milestones[].position", "timeline.milestones[].unit",
    )),
    (5, SCALAR, UNSOURCEABLE, (
        "timeline.milestones[].id", "timeline.milestones[].week",
        "timeline.milestones[].label",
    )),

    # §6 Commercial Terms. QofAI's per-deal pricing and underwriting, reviewer
    # input by design (E7a's REVIEWER_INPUT, plus the figures D1a moved to
    # studio input). The scenario table's own name and two figures are E5b's.
    (6, SCALAR, REVIEWER, (
        "commercial.qofai_investment_usd", "commercial.qofai_investment_note",
        "commercial.client_upfront_usd", "commercial.client_upfront_note",
        "commercial.client_retention_note", "commercial.cap_note",
        "commercial.no_improvement_clause",
    )),
    # comp_schedule is both a contract templated default and, since D1a, a
    # studio input. The contract's own declaration wins the class.
    (6, SCALAR, TEMPLATED, (
        "commercial.comp_schedule[].year", "commercial.comp_schedule[].pct",
    )),
    (6, SCALAR, REVIEWER, (
        "commercial.ev_assumptions.exit_ebitda_multiple",
        "commercial.ev_assumptions.reporting_readiness_multiple_uplift",
        "commercial.ev_assumptions.adjusted_ebitda_base_usd",
        "commercial.ev_assumptions.reporting_readiness_ev_usd",
    )),
    (6, SCALAR, SOURCED, (
        "commercial.scenarios[].name", "commercial.scenarios[].margin_gain_pp",
        "commercial.scenarios[].direct_uplift_usd_yr",
        # Which opportunity a case belongs to (item 15, 2026-09-13). Slide 5 is
        # one combined set for the deck, because QofAI contracts the engagement
        # rather than the opportunity, and the rows in it are per opportunity
        # and never summed. So the opportunity is one more LABEL on a row, and
        # it is absent on a deck with nothing to disambiguate: it comes from the
        # opportunity record, which is platform data, so its class is SOURCED
        # like the case name beside it.
        "commercial.scenarios[].opportunity",
    )),
    (6, SCALAR, REVIEWER, (
        "commercial.scenarios[].qofai_comp_usd",
        "commercial.scenarios[].qofai_comp_note",
        "commercial.scenarios[].client_retained_ebitda_usd",
        "commercial.scenarios[].enterprise_value_at_exit_usd",
    )),
    (6, LIST, REVIEWER, ("commercial.how_payment_works[]",)),
    (6, SCALAR, REVIEWER, ("commercial.ev_footnote",)),
    # THE COMMERCIAL SLIDE'S PRD HALF (`build-plan-commercial-slide.md`,
    # 2026-09-23). §11.2's cost total, one record per cost column, and each
    # case's payback from the paragraph under it. SOURCED, because a PRD states
    # them; outside the roster, so completeness does not move. `opportunity`
    # labels a row on a deck carrying several, exactly as on the scenarios.
    (6, SCALAR, SOURCED, (
        "commercial.investment[].label", "commercial.investment[].value",
        "commercial.investment[].basis", "commercial.investment[].opportunity",
        "commercial.scenarios[].payback",
    )),

    # §7 Next Steps. Reclassified with §4 above and on the same decision. Where
    # the paper states a real next step, an owner or a prerequisite, it is
    # extracted with its span; where it states none, the deck standard is the
    # fallback and these leaves are TEMPLATED again for that run. An OWNER and a
    # WEEK are never generated: both are commitments about a person and a date,
    # so each is carried only where the paper itself states it and left empty
    # otherwise.
    (7, SCALAR, SOURCED, (
        "next_steps[].number", "next_steps[].week", "next_steps[].owner",
        "next_steps[].title", "next_steps[].body",
    )),
)

SLOTS = tuple(
    (path, section, kind, origin)
    for section, kind, origin, paths in _SLOT_GROUPS
    for path in paths
)

ORIGINS = {path: origin for path, _section, _kind, origin in SLOTS}


def effective_origins(templated_fallbacks=()):
    """`ORIGINS` with one run's templated fallbacks reclassified back to TEMPLATED.

    The static table says which class a path carries when its source produced
    it. Two record lists (`platform_layers`, `next_steps`) are paper-sourced when
    the paper carries them and the contract's deck standard when it does not, so
    their class is a fact about the RUN and not about the schema. Everything that
    reads a class per run -- section 8's `from_kg` and `templated_defaults`, and
    `packet_fill.role_coverage` through them -- reads this rather than the table,
    so a deck showing the house platform never reports the paper's.

    `templated_fallbacks` is the container paths that took the deck standard,
    exactly as `packet_fill.apply_templated_defaults` returns them. An unknown
    path is ignored rather than raising: this is a reporting refinement, and a
    caller naming a path with no leaves has said nothing about any leaf.
    """
    if not templated_fallbacks:
        return dict(ORIGINS)
    return {
        path: (TEMPLATED
               if any(path.startswith(container + "[].")
                      or path == container
                      or path.startswith(container + ".")
                      for container in templated_fallbacks)
               else origin)
        for path, origin in ORIGINS.items()
    }


def _check_templated_is_closed():
    """A fourth templated default is a stop-and-report, so the table cannot
    quietly acquire one: every TEMPLATED slot must hang off one of the contract's
    own three declared paths."""
    stray = sorted(
        path for path, origin in ORIGINS.items()
        if origin == TEMPLATED
        and not any(path.startswith(default) for default in TEMPLATED_DEFAULTS)
    )
    if stray:
        raise ValueError(
            "TEMPLATED is a closed class holding only the contract's own "
            f"section 8 templated_defaults {TEMPLATED_DEFAULTS}; these do not "
            f"belong to it: {stray}"
        )


_check_templated_is_closed()


def _record_list(path):
    """The record-list path a leaf hangs off, or None for a plain leaf.

    `timeline.phases[].workstreams[].name` hangs off `timeline.phases`, the
    outermost list, because that is the one the fill map supplies whole and the
    one the renderer omits when it is absent.
    """
    head, _marker, tail = path.partition("[].")
    return head if tail else None


def _place(root, path, value):
    """Set `value` at a dotted packet path, creating blocks on the way down."""
    segments = path.split(".")
    node = root
    for segment in segments[:-1]:
        node = node.setdefault(segment, {})
    leaf = segments[-1]
    node[leaf[:-2] if leaf.endswith("[]") else leaf] = value


def _opportunity_entry(section):
    """One opportunity's section 2 entry: its copy, then its slotted values."""
    entry = {name: (section.get("copy") or {}).get(name)
             for name in OPPORTUNITY_COPY
             if (section.get("copy") or {}).get(name)}
    entry.update(_tree(2, section.get("fill") or {}))
    return entry


# The sections whose slide repeats once per opportunity BESIDE slide 2
# (2026-09-22): The Platform, Phased Rollout and Next Steps. Each keeps its flat
# yaml and prose, which are the lead opportunity's and are exactly what a
# one-opportunity packet has always carried, and on a deck with several gains an
# `opportunities` list with every opportunity's own values and its own two copy
# lines. A one-opportunity packet carries no list at all, so it cannot move.
PER_OPPORTUNITY_SLIDES = (4, 5, 7)

# An entry's copy, as yaml keys. `headline` is slide 2's key already; `summary`
# is the subhead.
SLIDE_COPY = ("headline", "summary")


def _slide_entry(section, entry):
    """One opportunity's own Platform, Timeline or Next Steps values."""
    copy = ((entry.get("slide_copy") or {}).get(section) or {})
    slide = {name: copy[name] for name in SLIDE_COPY if copy.get(name)}
    slide.update(_tree(section, entry.get("fill") or {}))
    return slide


def _sections_of(fill, sections):
    """The per-opportunity sections, defaulting to one built from `fill`.

    A caller with one opportunity passes nothing and gets exactly the document
    it got before section 2 repeated, which is every caller until step five
    wires the provider's several.
    """
    if sections is not None:
        return tuple(sections)
    return ({"fill": fill or {}, "copy": {}},)


def _tree(section, fill):
    """One section's value tree, in slot-table order.

    A filled slot carries its value. An absent scalar carries `null` and an
    absent scalar list carries `[]`, per the type-preserving rule. An absent
    record list is omitted, because emitting `[]` for one would add an
    unaccounted `foo[]` leaf and raise `CoverageError`.
    """
    root = {}
    for path, slot_section, kind, _origin in SLOTS:
        if slot_section != section:
            continue
        container = _record_list(path)
        if container is not None:
            if container in fill:
                _place(root, container, fill[container])
            continue
        _place(root, path, fill.get(path, [] if kind == LIST else None))
    return root


def _is_absent(fill, path):
    """Whether one slot path has no value, judged at LEAF granularity.

    Corrected 2026-08-15. This read the container before: a leaf under a filled
    record list counted as filled whether or not the record carried it, so
    filling `target_metrics` dropped `target_metrics[].label` out of section 8
    `gaps` while the document still said it was absent. Every record list E9c
    fills is partial, so that was twelve leaves absent in the value and invisible
    on the deck, which is the one failure mode this module's tests exist for.
    E9b never saw it because its only filled record populated all five leaves.

    A record list is supplied whole, so the first record decides, which is the
    same rule `_document_absent_paths` reads the rendered document by.
    """
    container = _record_list(path)
    if container is None:
        return path not in fill
    if container not in fill:
        return True
    node = fill[container]
    for segment in path[len(container) + 3:].split("."):
        node = (node[0] if node else None) if isinstance(node, list) else node
        if not isinstance(node, dict):
            return True
        node = node.get(segment[:-2] if segment.endswith("[]") else segment)
    return node is None or node == "" or node == []


def _absent_paths(fill, sections=()):
    """Slot paths with no value, in table order.

    A section 2 path is judged against EVERY opportunity and is absent when any
    one of them lacks it, because section 8's gaps are one list for one document
    and a gap in the second opportunity is still a gap. Which opportunity is
    missing what is a question the studio answers per slide, from the
    placeholder map, where each repeated slide renders against its own entry.
    """
    sections = tuple(sections) or ({"fill": fill or {}},)
    return tuple(
        path for path, section, _kind, _origin in SLOTS
        if (any(_is_absent(entry.get("fill") or {}, path) for entry in sections)
            if section == 2 else _is_absent(fill, path))
    )


def _reasons(packet):
    """Slot path to the reason its absence carries.

    The origin class supplies the reason, except where E7a already recorded one
    for a roster field the paper did not state: that reason passes through
    verbatim rather than being restated, so a reviewer reads the paper-level
    explanation and not a generic class label.
    """
    reasons = {path: REASONS[origin] for path, origin in ORIGINS.items()}
    for name, stated in getattr(packet, "missing_fields", ()) or ():
        for path in reasons:
            if path == name or path.startswith(name + ".") or path.startswith(name + "[]."):
                reasons[path] = stated
    return reasons


def _provenance(absent, fill, sources, reasons, role_coverage, confidence_basis,
                second_pass=None, templated_fallbacks=(), derived=(),
                generated=None, display_priority=None):
    """Section 8's ledger and gaps.

    `map_packet` reads exactly `from_kg`, `derived` and `templated_defaults` into
    `_provenance`, reads `gaps` separately, and ignores every other key (verified
    2026-08-13), so `sources` records the tool or span each sourced path arrived
    with at zero upstream cost, and `role_coverage` records the share of the 18
    render roles carrying real platform data. `role_coverage` is reported and
    never gated: `data_completeness` in the frontmatter is the one number
    `_passes_gates` reads, and a second gate there would be a seam change by the
    back door (`E9-RESCOPE-DESIGN.md` section 3.4).

    `confidence_basis` sits beside it and says in words what the frontmatter's
    band measures and what it does not (E9d, 2026-08-15). It belongs here rather
    than in the frontmatter because the frontmatter is what the gates read, and
    a run whose band and role coverage diverge should say so on the artifact.

    `second_pass` is E11 Stage 2's block: what the LLM extraction pass was asked
    for, what it filled with the section heading and the verbatim span behind
    each value, where the two passes disagreed, and what it declined. It is a
    block of its own and deliberately NOT folded into `gaps`, because
    `data_source_adapter._gap_flagged_roles` turns a gap path into the deck's
    "unconfirmed" marker and a field two passes both produced is not a gap. It
    is omitted entirely when no second pass ran, so its presence means one did.
    """
    filled = [path for path, _s, _k, _o in SLOTS if path not in set(absent)]
    # Per RUN, not per schema: the two record lists reclassified in E11 Stage 2c
    # are SOURCED where the paper carried them and the contract's deck standard
    # where it did not, and section 8 is where a reviewer is told which happened.
    origins = effective_origins(templated_fallbacks)
    # The three lists are DISJOINT, which is what makes each one readable: a
    # path computed off another packet field is reported under `derived` and
    # nowhere else, rather than appearing beside the figures a parser lifted
    # whole. Consumers that want "carries real data" read the union, the way
    # `packet_fill.role_coverage` does.
    computed = {path for path in derived if path not in set(absent)}
    return {"provenance": {
        "from_kg": [path for path in filled
                    if origins[path] == SOURCED and path not in computed],
        # A figure computed off another packet field rather than read straight
        # off a source. Empty until E11 Stage 2c, which put the build horizon
        # here: it is the end of the plan's own last phase, so its numbers are
        # the paper's and its selection is ours, and a reviewer should be told
        # which of the two they are looking at.
        "derived": [path for path in derived if path in computed],
        "templated_defaults": [path for path in filled if origins[path] == TEMPLATED],
        **({} if role_coverage is None else {"role_coverage": role_coverage}),
        **({} if not confidence_basis else {"confidence_basis": confidence_basis}),
        **({} if not second_pass else {"second_pass": second_pass}),
        # A block of its own, and never folded into `second_pass` above. That
        # one records QUOTATIONS the paper states, each with the span
        # `SourcedFigure` verified; this one records SENTENCES THE MODEL WROTE,
        # which carry no span because nobody wrote them in the paper and are
        # governed instead by the sections they name. A reviewer has to be able
        # to tell those apart at a glance, so they are never in one list.
        **({} if not generated else {"generated": generated}),
        # A third block, and never folded into either above. Those two record
        # what a model SAID, under two different disciplines. This one records
        # only an ORDER: the ranking pass answers in bullet numbers and returns
        # no text at all, so there is nothing here for a reviewer to check
        # against a source, and filing it beside them would suggest there is.
        **({} if not display_priority else {"display_priority": display_priority}),
        "sources": [
            {"field": path, "origin": origin}
            for path, origin in sorted((sources or {}).items())
        ],
        "gaps": [{"field": path, "reason": reasons[path]} for path in absent],
    }}


def _scalar_text(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return '"{}"'.format(str(value).replace('"', "'"))


def _emit(mapping, indent=0):
    """A mapping as packet-YAML lines the adapter's own reader parses back."""
    pad = "  " * indent
    lines = []
    for key, value in mapping.items():
        if isinstance(value, dict):
            lines.append(f"{pad}{key}:" if value else f"{pad}{key}: {{}}")
            lines.extend(_emit(value, indent + 1))
        elif isinstance(value, list):
            lines.append(f"{pad}{key}:" if value else f"{pad}{key}: []")
            for item in value:
                if isinstance(item, dict):
                    record = _emit(item, indent + 2)
                    lines.append(f"{pad}  - {record[0].strip()}")
                    lines.extend(record[1:])
                else:
                    lines.append(f"{pad}  - {_scalar_text(item)}")
        else:
            lines.append(f"{pad}{key}: {_scalar_text(value)}")
    return lines


def _frontmatter(packet, meta):
    """The envelope the gates read. `data_completeness` is E7a's ratio over its
    six-field roster and nothing else; the full packet shape does not enter the
    denominator (`E9-RESCOPE-DESIGN.md` section 3.4)."""
    completeness = (
        round(completeness_score.data_completeness(packet), 2) if packet else 0.0
    )
    lines = [
        f"schema_version: {_scalar_text(SCHEMA_VERSION)}",
        f"packet_type: {_scalar_text(PACKET_TYPE)}",
        f"generated_at: {_scalar_text(meta.get('generated_at'))}",
        f"generated_by: {_scalar_text(GENERATED_BY)}",
        "kg_source:",
        *_emit({
            key: meta.get(key)
            for key in ("company_id", "neo4j_reachable", "last_kg_refresh")
        }, 1),
        f"data_completeness: {completeness}",
    ]
    # The provider has no measure of confidence beyond that ratio, so the band is
    # emitted only when a caller supplies one. Absent, `_passes_gates` fails
    # closed, which is the honest outcome rather than a guessed band.
    if meta.get("confidence"):
        lines.append(f"confidence: {_scalar_text(meta['confidence'])}")
    return "---\n" + "\n".join(lines) + "\n---"


def build(request=None, packet=None, fill=None, sources=None, meta=None,
          copy=None, opportunities=None, role_coverage=None,
          confidence_basis=None, second_pass=None, templated_fallbacks=(),
          derived=(), generated=None, display_priority=None, sections=None):
    """The contract's full packet markdown, frontmatter plus sections 0 to 8.

    `request` is echoed into section 0 for traceability, as the contract's own
    section 0 describes. `packet` is E7a's `Packet`, read for the completeness
    ratio and for the reasons it recorded; nothing in it is re-parsed or
    recomputed. `fill` is layer 2, the map from packet path to value, and a path
    with no entry is absent in both places at once. `sources` maps a filled path
    to the tool or span it came from. `meta` carries the envelope facts a caller
    knows (`generated_at`, `company_id`, `neo4j_reachable`, `last_kg_refresh`,
    and a `confidence` band if it has one).

    Three more, added 2026-08-15 for the copy channel `coverage_guard` does not
    govern (`E9-RESCOPE-DESIGN.md` section 2b). `copy` maps a section number to
    the markdown copy lines that go under its heading, which is the only way the
    cover bullets and the `Headline:` / `Subhead:` lines reach `map_packet`: it
    reads those out of the section's PROSE, not its yaml, so a document emitting
    yaml alone leaves 21 copy roles empty. `opportunities` is section 3's
    payload, the contract's home for analytical source data, which nothing on
    the proposal path parses and the coverage walk never reaches; the
    opportunity description (1,772 characters on the one real record measured
    2026-08-15) is parked there for F1 rather than compressed here.
    `role_coverage` is the reported-not-gated share of the 18 render roles
    carrying real platform data, and `confidence_basis` is the sentence beside it
    saying what the frontmatter's band measures and what it does not.
    `second_pass` is `second_pass.ledger(...)`, the record of the LLM extraction
    pass, or None where none ran (E11 Stage 2, 2026-08-18).

    `templated_fallbacks` is the record-list paths that took the contract's deck
    standard on THIS run because the paper carried nothing for them, exactly as
    `packet_fill.apply_templated_defaults` returns them (E11 Stage 2c,
    2026-08-19). Section 8 reports those leaves as `templated_defaults` and the
    rest as `from_kg`, so a deck showing the house platform is never reported as
    showing the paper's.

    `derived` is the packet paths computed off another packet field rather than
    read straight off a source, which section 8 reports under its own
    `derived` key (E11 Stage 2c, 2026-08-19). One entry today: the build
    horizon, which is the end of the plan's own last phase.

    `generated` is `second_pass.written_ledger(...)`, the record of the WRITING
    pass, or None where none ran. It is a block of its own beside `second_pass`
    and is never merged into it: that one holds quotations the paper states and
    this one holds sentences the model wrote, and a reviewer who cannot tell
    those apart at a glance has lost the point of the distinction.

    `sections` is the per-opportunity half of section 2 (item 15, 2026-09-13):
    one entry per opportunity, each `{"fill": <that opportunity's flat fill
    map>, "copy": {"headline": ..., "one_liner": ...}}`, in the order the deck
    reads. Section 2's yaml is a list of those entries, because the
    today-versus-after story is the slide that repeats.

    Left unset it is one entry built from `fill` itself, which produces exactly
    the document every caller got before section 2 repeated, so the fill map
    stays FLAT and per-opportunity and nothing below this function learns that a
    deck can carry several. That matters more than it looks: `packet_fill` and
    `second_pass` both read and write that flat map, and nesting it would have
    reached into both.

    `display_priority` is `bullet_ranking.ledger(...)`, the order slide 2's bullet
    lists should be CONSIDERED in, or None where no ranking pass ran (2026-08-19).
    A third block, and separate from the two above for the same reason they are
    separate from each other: it records no value and no sentence, only an order,
    because that pass answers in integers and never returns text. It is
    deliberately NOT applied to the packet's own lists, which stay in the order
    their source gave them; a consumer that shows fewer bullets than the packet
    carries reads this to decide which, and a reviewer reads it to see that the
    choice was made rather than taken off the top of the list.
    """
    fill = fill or {}
    copy = copy or {}
    sections = _sections_of(fill, sections)
    absent = _absent_paths(fill, sections)
    reasons = _reasons(packet)
    blocks = [_frontmatter(packet, meta or {}), TITLE]
    for section, title in SECTION_TITLES.items():
        if section == 0:
            mapping = {"request": request} if request else {}
        elif section == 2:
            mapping = {OPPORTUNITY_SECTIONS: [
                _opportunity_entry(entry) for entry in sections
            ]}
        elif section == 3:
            mapping = {"opportunities": opportunities or []}
        elif section == 8:
            mapping = _provenance(absent, fill, sources, reasons, role_coverage,
                                  confidence_basis, second_pass,
                                  templated_fallbacks, derived, generated,
                                  display_priority)
        else:
            mapping = _tree(section, fill)
            if section in PER_OPPORTUNITY_SLIDES and len(sections) > 1:
                entries = [_slide_entry(section, entry) for entry in sections]
                # ALL OR NONE. An opportunity with nothing for this slide leaves
                # the list off, and `map_packet` then gives every opportunity
                # the flat slide by copy; a partial list could not be paired
                # with the deck's opportunities by position.
                if all(entries):
                    mapping[OPPORTUNITY_SECTIONS] = entries
        blocks.append(f"## {section} · {title}")
        if copy.get(section):
            blocks.append("\n".join(copy[section]))
        lines = _emit(mapping)
        if lines:
            blocks.append("```yaml\n" + "\n".join(lines) + "\n```")
    return "\n\n".join(blocks) + "\n"
