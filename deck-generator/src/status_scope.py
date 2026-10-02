"""Status from an assessment of scope (H1). Conceptually correct, and no further.

Why this is not a status provider. A status deck reports progress against a plan
of record, and Agent OS has none. Antonio's 2026-08-03 read walk found no `Plan`,
`Milestone`, `Phase`, `Workstream`, `Task` or `Schedule` label in any graph, no
date-bearing property beyond audit fields, and untouched engagement projects
where `created_at` equals `updated_at`. Blake confirmed on 2026-08-04 that no
formal milestone record exists, that QofAI intends to build one, and that the
graphs and workflows are not currently updated with this information. His
substitute direction is that the agent consider the full scope of work and make
its own assessment, and his bar is that "something conceptually there is probably
enough right now". So this module builds the conceptual path and stops.

REDO THIS WHEN QOFAI SHIPS THE MILESTONE RECORD. Everything below is a stand-in
for a source that does not exist yet. When one does, the completion state stops
being an inference and becomes a parsed field with a span, the gaps this module
writes stop being written, and the reviewer stops confirming what the platform
can state. Assume this file is thrown away rather than extended.

What the assessment reads, and what it cannot. The only scope that exists is the
`<Chart>` implementation timeline inside a published opportunity's research
paper: a proposed plan in relative months, with no start date and no progress
state. That is enough to enumerate what was SCOPED. It is not enough to know what
is DONE, and nothing in Agent OS carries what is done.

The conflict, named rather than worked around. Every guard in this repo exists to
stop the deck asserting something no source supports, and the golden rule is that
a figure with no source span is a missing field rather than a value. A completion
state the agent produces itself is, by construction, a claim with no span. So it
cannot ship the way a parsed figure ships. It ships this way instead:

- Labeled. Each scope item's own text names both halves: the phase label is
  SOURCED (it is verbatim from the chart config, which is its span) and the
  completion state is INFERRED or UNKNOWN. That text is packet copy, so it
  survives `strip_gap_flags` onto the client-facing deck, which the
  `(unconfirmed, see gaps)` marker deliberately does not.
- Flagged. Every state gets its own `gaps` entry in §5, so it reaches the
  reviewer checklist through C4's flag-resolution flow and the whole progress
  list renders with the unconfirmed marker in the design prompt.
- Correctable. The states are ordinary `progress_tracker` items, so C3's progress
  toggle is the affordance a reviewer uses to fix one, and the toggle is recorded
  as a revision.

Two honest compromises, recorded rather than hidden.

1. `state` stays in the packet's own `done` / `pending` / `in_process` vocabulary,
   because that is what the renderer draws a checkbox from and what C3's toggle
   moves. An UNKNOWN state therefore renders as `pending`, which is a shape the
   deck already has rather than a claim the packet makes: the item's own text says
   UNKNOWN, and the gap entry says it is unconfirmed.
2. `data_completeness` measures the scope this packet claims to carry, which is
   fully sourced whenever a timeline parses. It does not measure completion, of
   which nothing is sourced. §5 says so in `confidence_basis`, the same way the
   proposal path records the divergence between completeness and role coverage
   rather than folding one into the other.

`E_NO_SCHEDULE` keeps its contract meaning and is what comes back when there is
not even a scope to assess (`status-data-request-CONTRACT.md` §5). No client,
project or company detail is written here; every value arrives as an argument.
"""

import datetime

import chart_timeline_parser
import source_span
from live_proposal_provider import (ProviderError, confidence_band,
                                    error_envelope, subject_of)
from source_span import MissingFields

SOURCE_NOTE = "sourced from the paper's implementation timeline chart"
CONFIDENCE_BASIS = (
    "data_completeness measures the SCOPE this packet carries, which is sourced "
    "in full from the paper's implementation timeline chart. It does not measure "
    "COMPLETION: no source in Agent OS records what is done, so every completion "
    "state below is the agent's own claim, labeled in the item text and flagged "
    "in gaps for reviewer confirmation."
)
WARNINGS = (
    "Progress state is load-bearing and is NOT sourced on this path. Every state "
    "here is inferred or unknown, and none of it may reach a client before a "
    "reviewer confirms it.",
    "Conceptual only, per Blake 2026-08-04. Redo this against the milestone "
    "record QofAI intends to build; do not extend it.",
)


def _state(phase, elapsed_months):
    """One phase's completion state, and the words that say where it came from.

    The whole inference, deliberately. With an elapsed position supplied by the
    reviewer, a phase is judged against its own relative-month window and nothing
    else; with none, and none exists in the platform, the state is unknown. This
    is not a schema to build an inference engine against (Blake: the graphs are
    being reworked), so it stays a comparison.
    """
    window = f"months {phase['start']}–{phase['end']} of the proposed plan"
    if elapsed_months is None:
        return "pending", "UNKNOWN", (
            f"scope {SOURCE_NOTE}, {window} · completion state UNKNOWN, no "
            "source records what is done"
        )
    if elapsed_months >= phase["end"]:
        state, said = "done", "its window has closed"
    elif elapsed_months > phase["start"]:
        state, said = "in_process", "its window is open"
    else:
        state, said = "pending", "its window has not opened"
    return state, "INFERRED", (
        f"scope {SOURCE_NOTE}, {window} · completion state INFERRED at "
        f"elapsed month {elapsed_months} because {said}; unconfirmed"
    )


def assess_scope(paper, elapsed_months=None, opportunity=None):
    """The paper's scope as assessed items, or `E_NO_SCHEDULE` if there is none.

    Returns `(figure, items)`, where `figure` is the timeline's `SourcedFigure`
    (held so a reviewer can be shown the span the labels came from) and each item
    carries the phase label verbatim, the state, the state's origin, and the note
    that says which half is sourced.

    `opportunity` is the platform's opportunity record, which the figure names
    (item 15). A status deck carries one opportunity and nothing on this path
    changed with item 15, so it is optional and a caller without one gets a
    stand-in: this path has no multi-opportunity story to get wrong, and the
    requirement lives at the primitive rather than here.
    """
    missing = MissingFields()
    # The published paper, because the status path has no upload of its own
    # yet: part two says status decks inherit the base document rule once
    # they have a path at all, and that path is not this one.
    figure = chart_timeline_parser.parse_timeline(
        paper or "", missing, document=source_span.PAPER,
        opportunity=subject_of(opportunity)
        if (opportunity or {}).get("id")
        else source_span.Opportunity(id="status-scope"),
    )
    if figure is None:
        reason = "; ".join(why for _field, why in missing.entries)
        raise ProviderError(
            "E_NO_SCHEDULE",
            "No scope exists to assess: " + reason,
            "Generate a project-planning proposal and lock a plan first. There "
            "is nothing to report status on without one.",
            {"missing_fields": missing.names},
        )
    items = []
    for phase in figure.value:
        state, origin, note = _state(phase, elapsed_months)
        items.append({
            "label": phase["label"],
            "detail": note,
            "state": state,
            "origin": origin,
        })
    return figure, items


def _item_path(index):
    return f"workstreams[0].progress_tracker.groups[0].items[{index}].state"


def _gaps(items):
    """One gap per completion state, so none of them can ship unconfirmed."""
    return [
        {
            "field": _item_path(index),
            "reason": (
                f"{item['label']}: the completion state is the agent's own "
                f"{item['origin']} claim and carries no source span. Confirm it, "
                "correct it with the progress toggle, or send it back before "
                "this deck goes to a client."
            ),
        }
        for index, item in enumerate(items)
    ]


def _quoted(value):
    if value is None:
        return "null"
    return '"{}"'.format(str(value).replace('"', "'"))


def _display_date(iso_date):
    """`2026-05-22` as the packet's own display form, or None for a blank date."""
    if not iso_date:
        return None, None
    day = datetime.date.fromisoformat(iso_date)
    return day.strftime("%b %d %Y").upper(), day.strftime("%b %Y").upper()


def _horizon(figure):
    """The proposed plan's own length and unit, read off the parsed phases."""
    last = figure.value[-1]
    return last["end"], last.get("unit") or "months"


def build_packet(request, company, project, opportunity, figure, items,
                 generated_at=None):
    """A status packet carrying an assessed scope, in the contract's own shape.

    Sections 2 (tracking) and 4 (next steps) are absent rather than empty: a
    dated Gantt and a TODAY marker need a plan start date, which does not exist,
    and inventing one is the failure this whole path is arranged to avoid. A
    caller requests `cover` and `workstreams` and gets a two-slide deck.
    """
    display, month_year = _display_date(request.get("check_in_date"))
    horizon, unit = _horizon(figure)
    title = opportunity.get("title") or opportunity.get("name") or ""
    inferred = [item for item in items if item["origin"] == "INFERRED"]
    badge = "COMPLETION INFERRED · UNCONFIRMED" if inferred else "COMPLETION UNKNOWN"
    lines = [
        "---",
        'schema_version: "0.1"',
        'packet_type: "project_status_check_in"',
        f"generated_at: {_quoted(generated_at or _stamp())}",
        'generated_by: "status-scope-assessment (conceptual; no plan of record)"',
        "kg_source:",
        f"  company_id: {_quoted(company.get('id'))}",
        f"  project_id: {_quoted(project.get('id'))}",
        "  neo4j_reachable: true",
        "  last_kg_refresh: null",
        f"confidence: {_quoted(confidence_band(1.0))}",
        "data_completeness: 1.0",
        "---",
        "",
        "# Status Data Packet — scope assessment, no plan of record",
        "",
        "## 1 · Deck & Engagement Context",
        "",
        "```yaml",
        "deck:",
        '  deck_type: "status"',
        '  template: "status_check_in_v1"',
        '  slide_count_formula: "cover + tracking + N_workstreams + next_steps"',
        "  n_workstreams: 1",
        "  total_slides: 4",
        "company:",
        f"  name: {_quoted(company.get('name'))}",
        f"  client_short: {_quoted(company.get('client_short') or company.get('name'))}",
        f"  pe_firm: {_quoted(company.get('pe_firm'))}",
        "engagement:",
        f"  check_in_date: {_quoted(request.get('check_in_date'))}",
        f"  check_in_date_display: {_quoted(display)}",
        f"  check_in_month_year: {_quoted(month_year)}",
        "  project_week: null",
        "  project_week_n: null",
        "  project_week_m: null",
        '  confidentiality: "QOFAI CONFIDENTIAL"',
        "cover:",
        f"  deck_kicker: {_quoted('PROJECT CHECK-IN' + (' · ' + display if display else ''))}",
        f"  prepared_for: {_quoted('PREPARED FOR ' + str(company.get('name') or '').upper())}",
        f"  deck_title_accent: {_quoted(project.get('name'))}",
        '  deck_title_primary: "Project Check-in."',
        f"  status_subtitle: {_quoted('Scope assessment: ' + title if title else None)}",
        "```",
        "",
        "## 3 · Workstreams",
        "",
        "```yaml",
        "workstreams:",
        '  - id: "scope_assessment"',
        '    stage: "existing"',
        '    section_label: "SCOPE ASSESSMENT · STATUS"',
        f"    name_accent: {_quoted(title)}",
        f"    name_full: {_quoted(title + '.' if title else None)}",
        f"    summary: {_quoted(_summary(items, horizon, unit))}",
        "    summary_hook: null",
        "    today: null",
        "    after: null",
        "    where_we_are:",
        '      label: "WHERE WE ARE"',
        "      metrics:",
        f"        - {{ value: {_quoted(str(len(items)) + ' phases')}, "
        f"label: {_quoted('scoped in the proposed implementation plan, ' + SOURCE_NOTE)} }}",
        f"        - {{ value: {_quoted(str(horizon) + ' ' + unit)}, "
        f"label: {_quoted('proposed plan length, relative to a start date no source records')} }}",
        "      notes:",
        f"        - {_quoted('Both figures above are sourced. Every completion state on this slide is not.')}",
        "    target: null",
        "    progress_tracker:",
        '      label: "SCOPE ASSESSED AGAINST THE PROPOSED IMPLEMENTATION PLAN"',
        "      right_label: null",
        "      groups:",
        f"        - name: {_quoted('SCOPE ITEMS · ' + SOURCE_NOTE.upper())}",
        f"          status_label: {_quoted(badge)}",
        "          items:",
    ]
    for item in items:
        lines.append(
            f"            - {{ label: {_quoted(item['label'])}, "
            f"detail: {_quoted(item['detail'])}, state: {_quoted(item['state'])} }}"
        )
    lines.extend([
        "    next_steps: []",
        "```",
        "",
        "## 5 · Provenance & Gaps  *(not rendered — for the agent's guardrails)*",
        "",
        "```yaml",
        "provenance:",
        "  from_kg:",
        "    - company.name",
        "    - workstreams[0].name_accent",
        "  from_plan_schedule: []",
        "  sourced_from_paper:",
        "    - workstreams[0].progress_tracker.groups[0].items[].label",
        "    - workstreams[0].where_we_are.metrics",
        "  inferred:",
    ])
    lines.extend(f"    - {_item_path(index)}" for index in range(len(items)))
    lines.extend([
        "  derived:",
        "    - engagement.check_in_date_display",
        "  templated_defaults:",
        "    - engagement.confidentiality",
        "  modeled_or_prose:",
        "    - workstreams[0].summary",
        f"  confidence_basis: {_quoted(CONFIDENCE_BASIS)}",
        "  gaps:",
    ])
    for gap in _gaps(items):
        lines.append(f"    - field: {_quoted(gap['field'])}")
        lines.append(f"      reason: {_quoted(gap['reason'])}")
    lines.append("warnings:")
    lines.extend(f"  - {_quoted(warning)}" for warning in WARNINGS)
    lines.append("```")
    return "\n".join(lines) + "\n"


def _summary(items, horizon, unit):
    return (
        f"{len(items)} phases scoped over {horizon} {unit} in the proposed "
        "implementation plan. No plan of record exists, so every completion "
        "state below is assessed rather than reported, and each one is flagged "
        "for confirmation before this deck goes to a client."
    )


def _stamp():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class StatusScopeProvider:
    """The same `submit(request)` / `poll(handle)` seam every provider offers.

    Takes what a future status provider would fetch for itself — the resolved
    company and project, the opportunity and its research paper — because
    resolving them live is the proposal path's job and duplicating it here would
    build against a schema Blake has said is being reworked. `elapsed_months` is
    reviewer input: no source supplies it, and with none supplied every state is
    unknown rather than guessed.
    """

    def __init__(self, company, project, opportunity, paper, elapsed_months=None,
                 generated_at=None):
        self._parts = (company, project, opportunity, paper)
        self._elapsed_months = elapsed_months
        self._generated_at = generated_at
        self._envelopes = {}

    def submit(self, request):
        handle = len(self._envelopes)
        company, project, opportunity, paper = self._parts
        try:
            figure, items = assess_scope(paper, self._elapsed_months,
                                         opportunity)
            self._envelopes[handle] = {
                "status": "ok",
                "packet": build_packet(request, company, project, opportunity,
                                       figure, items, self._generated_at),
                "request_echo": request,
            }
        except ProviderError as error:
            self._envelopes[handle] = error_envelope(error)
        return handle

    def poll(self, handle):
        return {"done": True, "envelope": self._envelopes[handle]}
