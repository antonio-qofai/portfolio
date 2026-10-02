"""Module 3 — Prompt Assembler.

Takes the slide structure (Module 1, ``load_template``) and the flat
placeholder map (Module 2, ``run_adapter``) and writes a single design prompt
for a human to paste into Claude Design: one labeled section per slide, in
template order, followed by a deck-wide preamble of the recurring fields.
Only this module sees both structure and content, and it invents nothing —
every value it emits comes straight from the map it is handed, or is a
template-structural label (a slide title, a role name, a role's declared
display heading, a marker).

Two reviewer markers, distinct from each other:

- Missing-field marker — a role whose value is empty within a rendered
  slide (or a recurring field left empty). Rendered as ``[MISSING: role_name]``
  in place of the value. Never a hard failure. Within a record it is per-field,
  and only for a field the record actually carries: a field the record never
  carried is omitted with no marker, which is how a record shape that legitimately
  has no such leaf (a timeline row that is a phase rather than a workstream)
  stays quiet while a leaf that is genuinely empty still speaks up. A field the
  template declares ``optional`` is the third case: empty, it emits no line and no
  marker, because the deck has said a record without it still reads as complete.
- Unconfirmed marker — a role listed in the map's ``_gap_roles`` side channel:
  present, packet-sourced, but flagged in the packet's own gaps block as
  needing confirmation before anything ships. Rendered as the value plus a
  visible ``(unconfirmed, see gaps)`` flag.

A slide whose number appears in the map's ``_skipped`` side channel is left
out of the prompt entirely, with no marker of any kind — its absence was the
request's own choice, not a gap.

The prompt always ends with a marker that it is a draft for Claude Design and
needs human review, never a finished or sendable deck.
"""

# The one role this module DERIVES rather than reads, because it is a fact about
# the emitting rather than about the data: how many slides the deck ends up with.
# A template block marked `Repeat:` emits one slide per item, so the rendered
# count differs from the template's own block count, and no number may be written
# down in either file (item 15, 2026-09-13).
#
# Derived only when the map leaves it empty. The status packet STATES its own
# `deck.total_slides`, which `coverage_guard` slots and the mapping half reads,
# so deriving over the top of it would replace platform data with an inference.
# Named here rather than in a caller so the footer cannot say one thing while the
# deck does another: the count and the emitting are the same walk.
TOTAL_SLIDES_ROLE = "total_slides"

MISSING_MARKER = "[MISSING: {role}]"
UNCONFIRMED_MARKER = "(unconfirmed, see gaps)"

CLOSING_MARKER = (
    "---\n"
    "END OF DESIGN PROMPT. This is a draft for Claude Design and requires "
    "human review before anything ships. Not a finished or sendable deck."
)


def _is_empty(value):
    return value is None or value == "" or value == []


def _render_scalar(value, unconfirmed):
    text = str(value)
    if unconfirmed:
        text = f"{text} {UNCONFIRMED_MARKER}"
    return text


def _render_list(items, unconfirmed):
    lines = [f"- {item}" for item in items]
    if unconfirmed:
        lines.append(f"  {UNCONFIRMED_MARKER}")
    return "\n".join(lines)


def _render_records(records, fields, unconfirmed, optional_fields=()):
    optional_fields = set(optional_fields or ())
    blocks = []
    for i, record in enumerate(records, start=1):
        lines = []
        for field in fields:
            # A field the record never carried says nothing about itself, so it
            # emits no line: a timeline row that IS a phase has no workstream,
            # because no paper in the corpus decomposes one, and stamping a
            # marker inside every bar would report on the deck an absence the
            # packet already records in its §8 gaps. A field the record carries
            # EMPTY is the other case entirely, and keeps its marker below.
            if field not in record:
                continue
            value = record.get(field)
            # An empty field the template declares OPTIONAL emits nothing at all,
            # the same way an empty optional role does one level up. A marker has
            # to mean a source exists and did not arrive; for a field the deck
            # says it does not need, it would mean neither. This is the defect
            # found on the live deck of 2026-08-19: five components whose paper
            # named no category word each rendered `[MISSING: kicker]` on a card
            # that was complete without one.
            if _is_empty(value) and field in optional_fields:
                continue
            # An empty field within a present record is flagged, not rendered as
            # a bare None. This is how a load-bearing per-record field (e.g. a
            # timeline workstream's weeks) surfaces for the reviewer instead of
            # silently degrading.
            if _is_empty(value):
                lines.append(f"  {field}: {MISSING_MARKER.format(role=field)}")
            else:
                lines.append(f"  {field}: {value}")
        blocks.append(f"{i}.\n" + "\n".join(lines))
    text = "\n".join(blocks)
    if unconfirmed:
        text = f"{text}\n  {UNCONFIRMED_MARKER}"
    return text


def _role_key(role):
    """The line key emitted for a role. When the template declares a display
    ``label`` for the role, the key carries a render-under-heading instruction
    so Claude Design titles the block (e.g. payment_mechanics under
    "HOW PAYMENT WORKS") instead of leaving the content unlabeled. Without a
    label it is just the role name, unchanged."""
    label = role.get("label")
    if label:
        return f'{role["name"]} [render under heading "{label}"]'
    return role["name"]


def _render_role(role, source_map, gap_roles):
    """Render one role's line(s) from ``source_map`` (the flat placeholder map on
    a singleton slide, or a per-item map on a repeating slide). Returns ``None``
    when an ``optional`` role is empty — the caller drops it, so a legitimately
    absent field emits no line and no marker (distinct from a missing required
    field, which still gets its ``[MISSING: ...]`` marker)."""
    name = role["name"]
    role_type = role["type"]
    value = source_map.get(name)
    unconfirmed = name in gap_roles
    key = _role_key(role)

    if _is_empty(value):
        if role.get("optional"):
            return None
        return f"{key}: {MISSING_MARKER.format(role=name)}"

    if role_type == "list":
        return f"{key}:\n{_render_list(value, unconfirmed)}"
    if role_type == "list_of_records":
        return (f"{key}:\n"
                + _render_records(value, role.get("fields", []), unconfirmed,
                                  role.get("optional_fields", ())))
    return f"{key}: {_render_scalar(value, unconfirmed)}"


def _render_roles(roles, source_map, gap_roles):
    """Render a list of roles, dropping the ``None`` an omitted optional yields."""
    lines = []
    for role in roles:
        rendered = _render_role(role, source_map, gap_roles)
        if rendered is not None:
            lines.append(rendered)
    return lines


def _render_preamble(recurring_fields, placeholder_map, gap_roles):
    lines = ["# Deck-wide fields (apply to every slide)"]
    lines.extend(_render_roles(recurring_fields, placeholder_map, gap_roles))
    return "\n".join(lines)


def _render_slide(slide, display_number, source_map, gap_roles):
    lines = [f"## Slide {display_number} — {slide['title']}"]
    lines.extend(_render_roles(slide["roles"], source_map, gap_roles))
    return "\n".join(lines)


def rendered_slide_count(template, placeholder_map):
    """How many slides this deck actually renders, which is the footer's own
    denominator.

    Not the template's `Slide count`, which counts slide BLOCKS. A block marked
    `Repeat:` emits one slide per item in the list it names, so the two numbers
    are the same only for a deck whose every block is a singleton. The proposal
    template's slide 2 repeats once per opportunity (item 15, 2026-09-13) and
    the status template's workstream slide always has, so both paths want this
    and neither may hardcode a number.

    Counted with exactly the rules `assemble_prompt` emits by, and defined here
    beside them rather than in a caller, so the footer cannot say one thing
    while the deck does another.
    """
    skipped = set(placeholder_map.get("_skipped", []))
    count = 0
    for slide in template["slides"]:
        if slide["number"] in skipped or slide.get("section_key") in skipped:
            continue
        repeat_over = slide.get("repeat_over")
        if repeat_over:
            count += len(placeholder_map.get(repeat_over) or [])
        else:
            count += 1
    return count


def assemble_prompt(template, placeholder_map):
    """Merge the loaded slide structure with the mapped placeholder map into
    one per-slide design prompt (PRD §4 step 7; PRD S2/S7 for the status path).

    ``template`` is Module 1's ``load_template`` return value. ``placeholder_map``
    is Module 2's ``run_adapter`` ``placeholder_map`` (clean ok-packet only).
    Returns the assembled prompt as a single string.

    A slide may carry ``repeat_over``: the assembler then emits one copy per item
    in ``placeholder_map[repeat_over]`` (a list of per-item role maps, in order),
    resolving that slide's roles against each item and its own ``_gap_roles``.
    This is how the status deck gets ``cover + tracking + N workstreams +
    next_steps`` from a fixed template (PRD S2). When any slide repeats, the
    emitted ``## Slide N`` headers are numbered sequentially across the whole
    deck, so the count reflects the real, variable slide total (PRD S7); with no
    repeating slide (the proposal path), each slide keeps its declared number so
    a mid-deck skip does not renumber the rest.

    A slide is omitted when its declared number is in ``_skipped`` (the proposal
    path, keyed by slide number) or its ``section_key`` is in ``_skipped`` (the
    status path, keyed by section key, since the workstreams section expands to a
    variable slide count). Either way the omission is clean, with no marker (PRD
    criterion 7 / S9).
    """
    skipped = set(placeholder_map.get("_skipped", []))
    base_gap_roles = set(placeholder_map.get("_gap_roles", []))
    slides = template["slides"]
    # Sequential renumbering only when the deck has a repeating slide; otherwise
    # keep declared numbers so proposal skip-in-the-middle behavior is unchanged.
    sequential = any(slide.get("repeat_over") for slide in slides)

    if not placeholder_map.get(TOTAL_SLIDES_ROLE):
        placeholder_map = dict(
            placeholder_map,
            **{TOTAL_SLIDES_ROLE: str(
                rendered_slide_count(template, placeholder_map))},
        )
    sections = [
        _render_preamble(template["recurring_fields"], placeholder_map, base_gap_roles)
    ]
    display = 0
    for slide in slides:
        if slide["number"] in skipped or slide.get("section_key") in skipped:
            continue
        repeat_over = slide.get("repeat_over")
        if repeat_over:
            for item in placeholder_map.get(repeat_over, []) or []:
                display += 1
                number = display if sequential else slide["number"]
                item_gap_roles = set(item.get("_gap_roles", []))
                sections.append(_render_slide(slide, number, item, item_gap_roles))
        else:
            display += 1
            number = display if sequential else slide["number"]
            sections.append(_render_slide(slide, number, placeholder_map, base_gap_roles))
    sections.append(CLOSING_MARKER)

    return "\n\n".join(sections)
