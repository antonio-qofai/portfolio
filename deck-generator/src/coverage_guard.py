"""Field-coverage guard — no packet field falls on the floor silently.

The failure this exists to kill: the agent used to drop populated packet fields
that had no corresponding template slot. No error, no marker — the field simply
vanished from the generated prompt. For a tool whose entire value is faithful
reproduction of the packet, a silent drop is the worst possible failure mode.

This guard closes that hole. On the clean ok path (after the prompt is
assembled) it walks every structured leaf field in the packet's *rendered*
sections (§1, §2, §4, §5, §6, §7 — the sections that map 1:1 to slides) and
asserts that each leaf is one of exactly two things:

  (a) SLOTTED — its path is declared in ``COVERAGE_MAP`` (it maps to a template
      role), and, when the leaf is populated and its slide is not skipped, that
      role actually appears in the assembled prompt; or
  (b) ALLOWLISTED — its path is declared in ``EXCLUSION_ALLOWLIST`` with a
      one-line reason for why it is deliberately not on the deck.

A leaf that is neither slotted nor allowlisted makes the run fail loudly with a
``CoverageError`` naming the exact field path (e.g.
``commercial.client_retention_note``). There is no third outcome: "we forgot"
is not representable. Adding a field to the packet schema now forces a
deliberate choice — give it a slot or allowlist it — instead of letting it be
dropped in silence.

Scope note. The guard governs the packet's *structured* YAML leaf fields, which
are the fields that carry stable dotted paths (the paths §8 provenance/gaps and
the contract talk about) and the fields that were being dropped. The prose copy
lines (``Headline:`` / ``One-liner:`` / ``Subhead:`` / the cover bullets) are a
separate content channel with no field path; their faithful carry-through is
covered by the mapping tests (e.g. ``test_copy_roles_carried_verbatim...``), not
here. If prose copy ever needs the same guarantee, extend the walker to emit
paths for those lines and slot/allowlist them the same way.

Not hardcoded to the Ridgeline fixture: ``COVERAGE_MAP`` and
``EXCLUSION_ALLOWLIST`` are keyed on schema field *paths*, not on this client's
values, so the guard holds for any packet conforming to the schema. When the
live ``proposal-data-provider`` ships, its packets run through the same guard.
"""

from data_source_adapter import _section_yaml, _split_sections

# The proposal packet sections that map 1:1 to a rendered slide. §0 (request
# context), §3 (opportunities / analytical source data) and §8 (provenance /
# gaps) are non-rendered by design and are excluded from the walk entirely.
RENDERED_SECTIONS = (1, 2, 4, 5, 6, 7)

# The status packet's rendered sections (status-data-packet-EXAMPLE.md): §1 deck
# & engagement, §2 tracking, §3 workstreams, §4 next steps. §0 (request context)
# and §5 (provenance / gaps) are non-rendered and excluded, the same way §0/§8
# are on the proposal path.
STATUS_RENDERED_SECTIONS = (1, 2, 3, 4)


class CoverageError(AssertionError):
    """A rendered-section packet field is neither slotted nor allowlisted.

    Subclasses ``AssertionError`` so a run that trips the guard fails as loudly
    as a broken invariant, and so the lightweight test runners in ``tests/``
    (which catch ``AssertionError``) report it as a clean failure rather than an
    uncaught crash.
    """


# ---------------------------------------------------------------------------
# COVERAGE_MAP — the slotting declaration.
#
# Maps each rendered-section packet leaf path to the template role that carries
# it into the prompt. This is the authoritative "this field has a slot" record;
# it is the reverse of the build plan's per-slide mapping tables, declared as
# data so the guard can answer "is this leaf slotted, and into which role".
#
# Path convention (the guard's own, applied consistently): dict keys join with
# ``.``; a list collapses to ``[]`` regardless of length, and record fields hang
# off it (``commercial.scenarios[].name``). Several leaves fold into one role
# (e.g. the four EV assumptions all feed ``terms_footnote``); that is
# expected — the role only has to render once.
# ---------------------------------------------------------------------------
COVERAGE_MAP = {
    # §1 Company Profile → cover + recurring fields
    "company.name": "client_full",
    "company.client_short": "client_short",
    "company.pe_firm": "pe_firm",

    # §2 The Opportunity → TODAY / AFTER / build strip.
    #
    # Flat, and deliberately still flat after 2026-09-13. Section 2's yaml is a
    # LIST since item 15, one entry per opportunity, but the container is
    # stripped by the walker below rather than written into every path here.
    # One place on the write side knows about it (`packet_document`'s emitter)
    # and one on the read side (`rendered_leaf_paths`), and the rest of the repo
    # keeps the §2 vocabulary it has always had: this map, section 8's own gaps
    # and ledger, `ROLE_SOURCE_PATHS`, `role_coverage`, and the fill map every
    # one of them is keyed against.
    "today_metrics[].value": "today_metric_1",
    "today_metrics[].label": "today_metric_1",
    "today_pain_points[]": "today_pain_bullets",
    "target_metrics[].value": "after_metric_1",
    "target_metrics[].label": "after_metric_1",
    "target_capabilities[]": "after_capability_bullets",
    "build_summary.duration_weeks": "build_summary",
    # The unit-neutral horizon (E11 Stage 2c). `duration_weeks` is the
    # contract's own week-denominated field and stays absent on a plan the
    # paper states in months; these two carry the number and the unit the paper
    # itself states, and both feed the same role, which only has to render once.
    "build_summary.duration": "build_summary",
    "build_summary.duration_unit": "build_summary",
    "build_summary.phases[].label": "build_summary",
    "build_summary.phases[].summary": "build_summary",

    # §4 The Platform → numbered components
    "platform_layers[].number": "components",
    "platform_layers[].kicker": "components",
    "platform_layers[].title": "components",
    "platform_layers[].body": "components",

    # §5 Phased Rollout → timeline. Two axis paths and two span paths, because
    # the packet may state the axis itself (week_buckets, on the frozen fixture
    # packets) or state the chart's own phase spans and let the axis read off
    # them (columns, on a paper-derived packet). Both feed the one role; a role
    # only has to render once.
    "timeline.week_buckets[]": "timeline_columns",
    "timeline.columns[].label": "timeline_columns",
    "timeline.columns[].unit": "timeline_columns",
    "timeline.phases[].label": "timeline_rows",
    "timeline.phases[].start": "timeline_rows",
    "timeline.phases[].end": "timeline_rows",
    "timeline.phases[].unit": "timeline_rows",
    "timeline.phases[].workstreams[].name": "timeline_rows",
    "timeline.phases[].workstreams[].detail": "timeline_rows",
    "timeline.phases[].workstreams[].start_week": "timeline_rows",
    "timeline.phases[].workstreams[].end_week": "timeline_rows",
    "timeline.milestones[].position": "milestones",
    "timeline.milestones[].unit": "milestones",
    "timeline.milestones[].id": "milestones",
    "timeline.milestones[].week": "milestones",
    "timeline.milestones[].label": "milestones",

    # §6 Commercial Terms → the adaptive deal sheet (2026-09-23,
    # `build-plan-commercial-slide.md`). INVESTMENT from §11.2's cost total,
    # RETURN from the scenario cases, TERMS from whatever deal fields a packet
    # states, the value chart from the three chart figures.
    "commercial.investment[].label": "investment_rows",
    "commercial.investment[].value": "investment_rows",
    # The indicative flag is carried by the footnote it selects, and it rides
    # on the investment rows, so it is slotted with them.
    "commercial.investment[].basis": "investment_rows",
    "commercial.investment[].opportunity": "investment_rows",
    "commercial.scenarios[].name": "return_rows",
    # Which opportunity a case belongs to, on a deck carrying more than one
    # (item 15). A label on its row, never a reason to sum.
    "commercial.scenarios[].opportunity": "return_rows",
    "commercial.scenarios[].margin_gain_pp": "return_rows",
    "commercial.scenarios[].direct_uplift_usd_yr": "return_rows",
    "commercial.scenarios[].payback": "return_rows",
    "commercial.scenarios[].qofai_comp_usd": "value_mapping",
    "commercial.scenarios[].qofai_comp_note": "value_mapping",
    "commercial.scenarios[].client_retained_ebitda_usd": "value_mapping",
    "commercial.scenarios[].enterprise_value_at_exit_usd": "value_mapping",
    # A packet's own deal terms, each carried as one TERMS row
    # (`data_source_adapter._TERM_FIELDS`). These were the FBK layout's fixed
    # boxes, then studio-only input; the adaptive slide holds them as rows.
    "commercial.qofai_investment_usd": "terms_rows",
    "commercial.qofai_investment_note": "terms_rows",
    "commercial.client_upfront_usd": "terms_rows",
    "commercial.client_upfront_note": "terms_rows",
    "commercial.comp_schedule[].year": "terms_rows",
    "commercial.comp_schedule[].pct": "terms_rows",
    "commercial.cap_note": "terms_rows",
    "commercial.client_retention_note": "terms_rows",
    "commercial.no_improvement_clause": "terms_rows",
    "commercial.how_payment_works[]": "terms_rows",
    "commercial.ev_footnote": "terms_footnote",
    # EV footnote assumptions — composed into the footnote text (the reporting-
    # readiness dollar contribution among them), not copied blind from the prose.
    # They are slotted, not allowlisted, because the composed footnote genuinely
    # carries them. See data_source_adapter._ev_footnote_role.
    "commercial.ev_assumptions.exit_ebitda_multiple": "terms_footnote",
    "commercial.ev_assumptions.reporting_readiness_multiple_uplift": "terms_footnote",
    "commercial.ev_assumptions.adjusted_ebitda_base_usd": "terms_footnote",
    "commercial.ev_assumptions.reporting_readiness_ev_usd": "terms_footnote",

    # §7 Next Steps → numbered actions
    "next_steps[].number": "action_items",
    "next_steps[].week": "action_items",
    "next_steps[].owner": "action_items",
    "next_steps[].title": "action_items",
    "next_steps[].body": "action_items",
}


# ---------------------------------------------------------------------------
# EXCLUSION_ALLOWLIST — fields deliberately NOT on the deck.
#
# Every entry is a rendered-section leaf that we have consciously decided not to
# render, with a one-line reason. This is where a field goes when the answer is
# "deliberately off the deck", never "we forgot" — a forgotten field has no
# entry here and trips the guard. Keyed on schema paths, so it holds for any
# conforming packet, not just this client's values.
# ---------------------------------------------------------------------------
EXCLUSION_ALLOWLIST = {
    # §2's copy channel, which moved out of the section's prose and into each
    # opportunity's entry on 2026-09-13 (item 15). Prose sits between a heading
    # and its yaml, and a section that repeats has no way to pair N prose blocks
    # with N yaml entries. Allowlisted rather than slotted, which keeps them out
    # of section 8's gaps exactly as they were when they were prose: this
    # guard's own docstring excludes the copy channel because those lines carry
    # no field path, and the only thing that changed is that these two now do.
    # Their carry-through is held by the mapping tests, as it always was.
    "headline": "Copy channel: the headline of an opportunity's own slide 2, Platform, Timeline or Next Steps slide, carried by the mapping tests.",
    "one_liner": "Copy channel: slide 2's one-liner, carried by the mapping tests.",
    "summary": "Copy channel: the subhead of an opportunity's own Platform, Timeline or Next Steps slide, carried by the mapping tests.",

    # §1 — firmographic / financial baseline: internal scoping & underwriting
    # inputs that inform the analysis but are not proposal-slide content.
    "company.legal_name": "Legal entity name; the deck uses the display/brand name, not the LLC.",
    "company.sector": "Firmographic context for scoping; not a slide field.",
    "company.hq": "Firmographic context for scoping; not a slide field.",
    "company.employees": "Baseline headcount; internal scoping input, not rendered.",
    "company.fleet_size": "Operational baseline; internal scoping input, not rendered.",
    "company.active_projects": "Operational baseline; internal scoping input, not rendered.",
    "baseline.revenue_ttm_usd": "Financial baseline; internal underwriting input, not a slide field.",
    "baseline.adjusted_ebitda_usd": "Financial baseline; internal underwriting input, not rendered.",
    "baseline.adjusted_ebitda_pct": "Financial baseline; internal underwriting input, not rendered.",
    "baseline.ebitda_volatility_note": "Baseline narrative; the rendered TODAY metric comes from today_metrics.",
    "baseline.reporting_lag": "Baseline narrative; the rendered TODAY metric comes from today_metrics.",
    "baseline.baseline_locked_date": "Set at contract signing; null pre-signature (§8 gap). Not rendered.",

    # §2 — internal phase key.
    "build_summary.phases[].id": "Internal phase key for sequencing/joins; label + summary render, id does not.",

    # §5 — redundant horizon + internal phase key.
    "timeline.total_weeks": "Redundant horizon; rendered horizon derives from build_summary.duration_weeks / week_buckets.",
    "timeline.phases[].id": "Internal phase key used to join workstreams; label renders, id does not.",
}


# ---------------------------------------------------------------------------
# STATUS_COVERAGE_MAP — the status path's slotting declaration.
#
# Same convention as COVERAGE_MAP, against the status packet's rendered sections
# (§1–§4) and the status template's roles. The one structural note: the two
# framing blocks each workstream carries are stage-selected (PRD S12), so both
# the `new` blocks (today / after) and the `existing` blocks (where_we_are /
# target) slot into the SAME neutral Frame A / Frame B roles — whichever pair a
# workstream's stage populates renders; the other pair is null and slots anyway
# (a slot is by schema path, not by value).
# ---------------------------------------------------------------------------
STATUS_COVERAGE_MAP = {
    # §1 Deck & Engagement → cover + recurring fields
    "company.name": "client_full",
    "company.client_short": "client_short",
    "company.pe_firm": "pe_firm",
    "deck.total_slides": "total_slides",
    "engagement.check_in_date_display": "check_in_date",
    "engagement.check_in_month_year": "month_year",
    "engagement.confidentiality": "confidentiality",
    "engagement.project_week": "project_week",
    # N and M compose the "Week N of M" string carried in project_week.
    "engagement.project_week_n": "project_week",
    "engagement.project_week_m": "project_week",
    "cover.deck_kicker": "deck_kicker",
    "cover.prepared_for": "prepared_for",
    "cover.deck_title_accent": "deck_title_accent",
    "cover.deck_title_primary": "deck_title_primary",
    "cover.status_subtitle": "status_subtitle",

    # §2 Project Tracking → dated Gantt with TODAY marker
    "tracking.section_label": "tracking_section_label",
    "tracking.headline": "tracking_headline",
    "tracking.summary": "tracking_summary",
    "tracking.columns[].id": "timeline_columns",
    "tracking.columns[].date": "timeline_columns",
    "tracking.columns[].label": "timeline_columns",
    "tracking.today_marker.on_week": "today_marker_week",
    "tracking.today_marker.label": "today_marker_label",
    "tracking.bar_categories[].id": "bar_categories",
    "tracking.bar_categories[].color": "bar_categories",
    "tracking.bar_categories[].covers": "bar_categories",
    "tracking.lanes[].name": "gantt_bars",
    "tracking.lanes[].bars[].label": "gantt_bars",
    "tracking.lanes[].bars[].start_week": "gantt_bars",
    "tracking.lanes[].bars[].end_week": "gantt_bars",
    "tracking.lanes[].bars[].category": "gantt_bars",
    "tracking.lanes[].bars[].state": "gantt_bars",
    "tracking.slip_or_buffer_markers[].label": "slip_or_buffer_markers",
    "tracking.slip_or_buffer_markers[].week": "slip_or_buffer_markers",
    "tracking.slip_or_buffer_markers[].kind": "slip_or_buffer_markers",
    "tracking.slip_or_buffer_markers[].workstream": "slip_or_buffer_markers",

    # §3 Workstreams → one status slide each (variable count)
    "workstreams[].section_label": "section_label",
    "workstreams[].name_full": "workstream_name_full",
    "workstreams[].name_accent": "workstream_name_accent",
    "workstreams[].summary": "workstream_summary",
    "workstreams[].summary_hook": "workstream_summary",
    # Frame A (current state): TODAY for a new workstream, WHERE WE ARE for an
    # existing one — both slot into the Frame A roles.
    "workstreams[].today.label": "frame_a_label",
    "workstreams[].today.metrics[].value": "frame_a_metrics",
    "workstreams[].today.metrics[].label": "frame_a_metrics",
    "workstreams[].today.pain_bullets[]": "frame_a_bullets",
    "workstreams[].where_we_are.label": "frame_a_label",
    "workstreams[].where_we_are.metrics[].value": "frame_a_metrics",
    "workstreams[].where_we_are.metrics[].label": "frame_a_metrics",
    "workstreams[].where_we_are.notes[]": "frame_a_bullets",
    # Frame B (target): AFTER for a new workstream, TARGET for an existing one.
    "workstreams[].after.horizon": "frame_b_label",
    "workstreams[].after.metrics[].value": "frame_b_metrics",
    "workstreams[].after.metrics[].label": "frame_b_metrics",
    "workstreams[].after.capability_bullets[]": "frame_b_bullets",
    "workstreams[].target.horizon": "frame_b_label",
    "workstreams[].target.metrics[].value": "frame_b_metrics",
    "workstreams[].target.metrics[].label": "frame_b_metrics",
    "workstreams[].target.notes[]": "frame_b_bullets",
    # Progress tracker (load-bearing; PRD S13).
    "workstreams[].progress_tracker.label": "progress_label",
    "workstreams[].progress_tracker.right_label": "progress_right_label",
    "workstreams[].progress_tracker.groups[].name": "progress_items",
    "workstreams[].progress_tracker.groups[].status_label": "progress_items",
    "workstreams[].progress_tracker.groups[].items[].label": "progress_items",
    "workstreams[].progress_tracker.groups[].items[].detail": "progress_items",
    "workstreams[].progress_tracker.groups[].items[].state": "progress_items",
    "workstreams[].next_steps[]": "ws_next_steps",

    # §4 Next Steps → one column per active workstream
    "next_steps_slide.section_label": "next_steps_section_label",
    "next_steps_slide.headline": "next_steps_headline",
    "next_steps_slide.summary": "next_steps_summary",
    "next_steps_slide.columns[].title": "next_steps_items",
    "next_steps_slide.columns[].workstream_id": "next_steps_items",
    "next_steps_slide.columns[].steps[].number": "next_steps_items",
    "next_steps_slide.columns[].steps[].title": "next_steps_items",
    "next_steps_slide.columns[].steps[].body": "next_steps_items",
}


# ---------------------------------------------------------------------------
# STATUS_EXCLUSION_ALLOWLIST — status fields deliberately NOT on the deck.
# ---------------------------------------------------------------------------
STATUS_EXCLUSION_ALLOWLIST = {
    # §1 — request-echo / derivation inputs, not slide content.
    "deck.deck_type": "Deck-type flag; the rendered footer label comes from deck_type_label, not this.",
    "deck.template": "Request echo (which template to fill); not a slide field.",
    "deck.slide_count_formula": "Templated default describing the count rule; total_slides is what renders.",
    "deck.n_workstreams": "Drives the slide count; total_slides is the rendered footer denominator.",
    "engagement.check_in_date": "ISO source date; the display form (check_in_date_display) renders.",

    # §2 — determinism key.
    "tracking.plan_revision": "Plan re-baseline key, part of the determinism contract; not rendered.",

    # §3 — internal keys and stage-selected null blocks.
    "workstreams[].id": "Internal join key; next_steps columns carry their own workstream_id.",
    "workstreams[].stage": "Selects the framing (PRD S12); drives Frame A/B, not rendered verbatim.",
    "workstreams[].today": "Null block for an existing-stage workstream; its populated leaves are slotted (PRD S9).",
    "workstreams[].after": "Null block for an existing-stage workstream; its populated leaves are slotted (PRD S9).",
    "workstreams[].where_we_are": "Null block for a new-stage workstream; its populated leaves are slotted (PRD S9).",
    "workstreams[].target": "Null block for a new-stage workstream; its populated leaves are slotted (PRD S9).",
}


# Per-deck-type coverage profile, selected by ``deck_type`` in ``check_coverage``.
_COVERAGE_PROFILES = {
    "proposal": {
        "sections": RENDERED_SECTIONS,
        "map": COVERAGE_MAP,
        "allowlist": EXCLUSION_ALLOWLIST,
    },
    "status": {
        "sections": STATUS_RENDERED_SECTIONS,
        "map": STATUS_COVERAGE_MAP,
        "allowlist": STATUS_EXCLUSION_ALLOWLIST,
    },
}


def _walk_leaves(value, prefix, out):
    """Collect canonical leaf paths from a parsed section value.

    Dict keys join with ``.``; a list collapses to ``[]`` (indices dropped) and
    records recurse beneath it. A scalar (including ``None`` — a declared-but-null
    field still counts, so a future non-null value can't slip in unaccounted) is
    a leaf. An empty list still registers its ``[]`` path so the field is not
    invisible to the guard.
    """
    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else key
            _walk_leaves(child, child_prefix, out)
    elif isinstance(value, list):
        list_prefix = f"{prefix}[]"
        if not value:
            out.add(list_prefix)
        for item in value:
            if isinstance(item, (dict, list)):
                _walk_leaves(item, list_prefix, out)
            else:
                out.add(list_prefix)
    else:
        out.add(prefix)


# The container section 2's yaml became on 2026-09-13 (item 15), one entry per
# opportunity. `_walk_leaves` collapses indexes, so every §2 leaf comes back
# under this prefix however many opportunities the deck carries, and it is
# stripped so the rest of this module, section 8's gaps, `ROLE_SOURCE_PATHS` and
# the fill map all keep the flat §2 vocabulary they have always had. Section 2's
# yaml is ONLY this container, so nothing at its root can collide with a
# stripped path.
OPPORTUNITY_CONTAINER = "opportunities[]."


def rendered_leaf_paths(packet_md, sections=RENDERED_SECTIONS):
    """Every structured leaf path in the packet's rendered sections.

    Deterministic set; parsed with the same reader Module 2 uses to map, so the
    guard sees exactly the fields the mapper had a chance to slot. ``sections``
    selects which packet sections to walk (proposal vs. status).

    Section 2's repeating container is stripped, so one leaf that a
    two-opportunity deck states twice is one path here, which is what the rest
    of the guard, the slot table and the coverage map are all keyed on.
    """
    parsed = _split_sections(packet_md)
    paths = set()
    for number in sections:
        section_yaml = _section_yaml(parsed.get(number, ""))
        _walk_leaves(section_yaml, "", paths)
    return {
        path[len(OPPORTUNITY_CONTAINER):]
        if path.startswith(OPPORTUNITY_CONTAINER) else path
        for path in paths
    }


def _skipped_roles(template, placeholder_map):
    """Roles that legitimately do not appear because their slide was not
    requested (``_skipped``); a slotted leaf whose only role is skipped is not a
    silent drop, it is a request-driven omission (PRD criterion 7 / S9). ``_skipped``
    holds slide numbers on the proposal path and section keys on the status path,
    so a slide is skipped when either its number or its ``section_key`` is in it."""
    skipped = set(placeholder_map.get("_skipped", []))
    roles = set()
    for slide in template["slides"]:
        if slide["number"] in skipped or slide.get("section_key") in skipped:
            roles |= {role["name"] for role in slide["roles"]}
    return roles


def _populated_roles(template, placeholder_map):
    """Role names carrying a non-empty value, across the flat map and every
    repeating slide's per-item maps (the status workstreams). Used by the
    render-check so a role that renders inside a repeated slide (its value lives
    in an item map, not the flat map) is not mistaken for an unpopulated field.
    """
    def nonempty(value):
        return value not in (None, "", [])

    roles = {
        name for name, value in placeholder_map.items()
        if not name.startswith("_") and nonempty(value)
    }
    for slide in template["slides"]:
        repeat_over = slide.get("repeat_over")
        if not repeat_over:
            continue
        for item in placeholder_map.get(repeat_over, []) or []:
            roles |= {
                key for key, value in item.items()
                if not key.startswith("_") and nonempty(value)
            }
    return roles


def _template_role_names(template):
    names = set()
    for slide in template["slides"]:
        names |= {role["name"] for role in slide["roles"]}
    names |= {role["name"] for role in template["recurring_fields"]}
    return names


def _role_renders(role, prompt):
    """True if ``role`` appears as a rendered line in the prompt. Module 3 emits
    every role on a line that begins with the role name, followed either directly
    by ``:`` or, when the role declares a display label, by a
    ``[render under heading "..."]`` instruction and then ``:``. A skipped
    slide's roles are absent; an empty role renders as
    ``role_name: [MISSING: role_name]`` — still present, so the reviewer sees the
    gap. Both are handled by the caller."""
    for line in prompt.splitlines():
        if line.startswith(f"{role}:") or line.startswith(f"{role} ["):
            return True
    return False


def check_coverage(packet_md, template, placeholder_map, prompt, *, deck_type="proposal"):
    """Assert every rendered-section leaf is slotted or allowlisted.

    Raises ``CoverageError`` (naming the exact paths) when a leaf is neither.
    Also raises when the coverage map points a leaf at a role the loaded template
    does not declare (a dead slot — the map lying about a slot is as bad as no
    slot), and when a populated, non-skipped slotted leaf's role fails to appear
    in the assembled prompt (a drop inside the pipeline rather than at the
    schema edge). Returns silently on success.

    ``deck_type`` selects the coverage profile — which packet sections to walk,
    and which coverage map / allowlist to partition against — so the guard covers
    the status packet/prompt the same way it covers the proposal path.
    """
    profile = _COVERAGE_PROFILES.get(deck_type)
    if profile is None:
        raise ValueError(f"unknown deck_type: {deck_type!r}")
    coverage_map = profile["map"]
    allowlist = profile["allowlist"]
    leaves = rendered_leaf_paths(packet_md, profile["sections"])

    # 1. The core partition: every leaf is slotted or allowlisted.
    unaccounted = sorted(
        path
        for path in leaves
        if path not in coverage_map and path not in allowlist
    )
    if unaccounted:
        listing = "\n".join(f"  - {path}" for path in unaccounted)
        raise CoverageError(
            "packet field(s) in a rendered section have no template slot and "
            "no exclusion-allowlist entry, so they would be dropped from the "
            "prompt with no signal:\n"
            f"{listing}\n"
            "Give each field a slot in the coverage map (add a template role "
            "that renders it) or add it to the exclusion allowlist with a reason."
        )

    # 2. No dead slots: a slotted leaf must point at a role the template declares.
    template_roles = _template_role_names(template)
    dead_slots = sorted(
        f"{path} -> {coverage_map[path]}"
        for path in leaves
        if path in coverage_map and coverage_map[path] not in template_roles
    )
    if dead_slots:
        listing = "\n".join(f"  - {entry}" for entry in dead_slots)
        raise CoverageError(
            "the coverage map slots a packet field into a role the template "
            "does not declare (a dead slot):\n"
            f"{listing}"
        )

    # 3. No silent drop inside the pipeline: a populated, non-skipped slotted
    #    leaf's role must actually render in the prompt.
    skipped_roles = _skipped_roles(template, placeholder_map)
    populated_roles = _populated_roles(template, placeholder_map)
    dropped = []
    for path in sorted(leaves):
        if path not in coverage_map:
            continue
        role = coverage_map[path]
        if role in skipped_roles:
            continue
        if role not in populated_roles:
            # Role carries nothing to render; Module 3 emits a MISSING marker (or
            # cleanly omits an optional role), a visible outcome, not a silent
            # drop. Populated roles include those living in a repeating slide's
            # per-item maps (the status workstreams).
            continue
        if not _role_renders(role, prompt):
            dropped.append(f"{path} -> {role}")
    if dropped:
        listing = "\n".join(f"  - {entry}" for entry in dropped)
        raise CoverageError(
            "a populated, non-skipped packet field's role did not appear in "
            "the assembled prompt (silent drop inside the pipeline):\n"
            f"{listing}"
        )
