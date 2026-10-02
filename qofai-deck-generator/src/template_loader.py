"""Module 1 — Template Loader.

Reads a markdown template structure spec (e.g. templates/proposal-template.md)
and turns it into an ordered list of slides, each with its typed content
roles. Structure only — never touches client data. The filled examples in the
spec (e.g. FBK's values) are illustration only and are never surfaced by this
module as content.

Role types are declared in the spec itself, per role, as a machine-readable
annotation immediately after the role token:

    `{deck_kicker}` `[type: string]`
    `{today_pain_bullets}` `[type: list]`
    `{components}` `[type: list_of_records; fields: number, kicker, title, description]`

A role may also declare an optional display heading:

    `{payment_mechanics}` `[type: string; label: "HOW PAYMENT WORKS"]`

When present, the loader carries it as the role's ``label`` and Module 3 emits
it into the prompt as a render-under-heading instruction, so Claude Design
titles the block instead of leaving it unlabeled.

Finally a role (or one field of a record role) may declare WHO is expected to
fill it, and whether one value covers the whole deck:

    `{deck_date}` `[type: string; reviewer; deck_wide]`
    `{payment_mechanics}` `[type: string; label: "..."; reviewer: sensitive]`
    `{action_items}` `[type: list_of_records; fields: number, week (reviewer), ...]`

Those two flags are what let the studio tell a value the SOURCE failed to
provide from one we deliberately ask a human for. See ``fill_expectations``.

The loader parses those annotations directly. It does not guess a role's type
from surrounding prose (the v1 keyword heuristic is gone), and it does not hold
a hardcoded role registry: the type, a record role's field schema, and any
label all come from the spec. Only a line carrying a `[type: ...]` annotation is
a role, so tokens that appear inside example strings are never mistaken for
declarations.
"""

import re

# A content role: the `{name}` token followed by its `[type: ...]` annotation,
# both backtick-wrapped. For list_of_records, the annotation also declares the
# record's field schema as a comma-separated `fields:` list. Any role may also
# declare an optional `label:` — a display heading the assembler emits into the
# prompt so Claude Design renders the field's block under that visible heading
# (e.g. payment_mechanics under "HOW PAYMENT WORKS") instead of unlabeled — and
# an `optional` flag, which tells the assembler to omit the role's line entirely
# when it is empty rather than emit a `[MISSING: ...]` marker (for a role that is
# legitimately absent by design, e.g. a progress tracker's right-hand label on a
# workstream that has none). The `fields:` capture stops at a `;` so a trailing
# `label:` / `optional` is not swept into it.
#
# A single field of a `list_of_records` role carries the same flag as `name
# (optional)`, and it means the same thing one level down: a record that does not
# state that field emits no line for it and no marker, while a record that does
# state it renders it. That distinction is the deck's to make, not the source's —
# whether a card still reads as complete without the field is a question about
# the slide — which is why it is declared here beside the field rather than
# inferred from how often a paper happens to fill it.
#
# The last two flags answer a different question from `optional`: not "may this
# be absent" but "who was supposed to fill it".
#
#   `reviewer`            we ask a human for this value; the source was never
#                         asked. A marker on it is a reviewer input we are still
#                         waiting for, not a hole in the data.
#   `reviewer: sensitive` the same, and the value must not come from a source at
#                         all — QofAI's own per-deal arithmetic. A marker on it
#                         is the deck honestly stating that a figure belongs here
#                         and a human has not entered it yet.
#   `deck_wide`           one value covers every occurrence of this role on the
#                         deck (a date in every slide footer), so the studio may
#                         offer it as ONE input that fans out to each one. The
#                         default is per-instance, because collapsing a role that
#                         carries a different value per occurrence would make all
#                         but one of them unfillable.
#
# Declared here rather than inferred, and declared beside `optional` for the same
# reason `optional` is: whether a slide expects a human or a source to fill a
# field is a question about the SLIDE, which is what this file is for. It is also
# the only declaration keyed on the name a rendered `[MISSING: ...]` marker
# actually carries, which is all the studio has to classify one by.
#
# The bare flags may appear in any order and in any combination; only `fields:`
# and `label:` are positional (unchanged, so no existing template moves).
_ROLE_FLAG = r"optional|reviewer(?::\s*sensitive)?|deck_wide"

ROLE_RE = re.compile(
    r"`\{([a-zA-Z0-9_]+)\}`\s*"
    r"`\[type:\s*(string|list|list_of_records)"
    r"(?:;\s*fields:\s*([^\];]+))?"
    r"(?:;\s*label:\s*\"([^\"]*)\")?"
    r"((?:;\s*(?:" + _ROLE_FLAG + r"))*)\]`"
)

# One bare flag, for scanning a matched flag tail (role level) or a matched
# parenthetical (field level).
_FLAG_SCAN_RE = re.compile(_ROLE_FLAG)

# The two fill classes a `reviewer` flag can declare, as the studio names them.
REVIEWER = "reviewer"
SENSITIVE = "sensitive"
# The absence of a flag: the source was asked for this value and did not supply
# it. Not spelled in any template — it is what a role means by saying nothing.
SOURCE = "source"


def _parse_flags(text):
    """The bare flags in ``text`` as a set, spelling normalised.

    ``reviewer: sensitive`` (any inner whitespace) collapses to
    ``"reviewer:sensitive"`` so callers compare against one spelling.
    """
    return {
        re.sub(r"\s+", "", match.group(0))
        for match in _FLAG_SCAN_RE.finditer(text or "")
    }


def _fill_class(flags):
    """The fill class a flag set declares: SENSITIVE, REVIEWER, or SOURCE."""
    if "reviewer:sensitive" in flags:
        return SENSITIVE
    if "reviewer" in flags:
        return REVIEWER
    return SOURCE

SLIDE_HEADING_RE = re.compile(r"^##\s+Slide\s+(\d+)\s*(?:[—-]\s*(.*))?$")

# Optional per-slide directives, read from the lines just under a slide heading.
# `Section key:` ties the slide to a `sections_requested` key (so a not-requested
# section can be omitted by key, independent of slide number). `Repeat:` names a
# placeholder-map list the slide repeats over — the assembler emits one copy of
# the slide per item, which is how the status deck gets a variable slide count
# (one workstream slide per active workstream). A slide without either directive
# is a plain singleton, exactly as the proposal template's slides are.
SECTION_KEY_RE = re.compile(r"^Section key:\s*`([^`]+)`", re.MULTILINE)
REPEAT_RE = re.compile(r"^Repeat:\s*([a-zA-Z0-9_]+)", re.MULTILINE)


# One entry of a `fields:` list: the field name, and its flags in parentheses.
# `(optional)` says a record with no such field still reads as complete;
# `(reviewer)` / `(reviewer: sensitive)` say who is expected to fill it.
#
# Inside the parentheses flags are separated by SPACES, not by `;` or `,`: a `;`
# would cut the enclosing `fields:` capture short and a `,` would split the entry.
# `deck_wide` is not accepted here and has no meaning here — a record field is
# per-instance by construction, one value per record, which is exactly what a
# record list is.
_FIELD_FLAG = r"optional|reviewer(?::\s*sensitive)?"
FIELD_RE = re.compile(
    r"^([a-zA-Z0-9_]+)"
    r"(?:\s*\(\s*((?:" + _FIELD_FLAG + r")(?:\s+(?:" + _FIELD_FLAG + r"))*)\s*\))?$"
)


def _field_entries(fields_str):
    """Walk a `fields:` annotation value as ``(name, flags)`` pairs, in order.

    An entry that does not parse keeps its text as a name with no flags, which is
    the same way the role regex treats an annotation it does not recognise: a
    template typo shows up as a field nothing fills rather than as a crash at load.
    """
    for raw in fields_str.split(","):
        entry = raw.strip()
        if not entry:
            continue
        match = FIELD_RE.match(entry)
        if not match:
            yield entry, set()
            continue
        yield match.group(1), _parse_flags(match.group(2))


def _parse_fields(fields_str):
    """Split a `fields:` annotation value into its field names and its optionals.

    Returns ``(names, optional_names)``. ``names`` stays a plain ordered list of
    field names, flags stripped, because it is the record's field schema and every
    consumer reads it as such; the flags ride alongside so nothing that only wants
    the schema has to know about them.
    """
    names, optional = [], []
    for name, flags in _field_entries(fields_str):
        names.append(name)
        if "optional" in flags:
            optional.append(name)
    return names, optional


def _parse_field_fills(fields_str):
    """The non-SOURCE fill class of each flagged field, as ``{name: class}``.

    Only fields that declare one appear, so a record role whose fields are all
    source-supplied contributes nothing and its parsed dict is byte-for-byte what
    it was before this flag existed.
    """
    return {
        name: _fill_class(flags)
        for name, flags in _field_entries(fields_str)
        if _fill_class(flags) != SOURCE
    }


def _extract_roles(section_text):
    """Extract annotated content roles from a section of markdown, in
    first-appearance order. Each role is `{"name", "type"}`; a
    `list_of_records` role also carries `{"fields": [...]}`, plus
    `{"optional_fields": [...]}` when any of those fields is declared optional
    and `{"field_fills": {name: class}}` when any declares a fill class.

    A role that declares a fill class of its own carries `{"fill": class}`, and
    one declared `deck_wide` carries `{"deck_wide": True}`. Each key is present
    only when the template says so, so a role that declares nothing new parses
    to byte-for-byte the dict it did before these flags existed.
    """
    roles = []
    seen = set()
    for match in ROLE_RE.finditer(section_text):
        name, role_type, fields_str, label, flag_tail = (
            match.group(1), match.group(2), match.group(3),
            match.group(4), match.group(5),
        )
        if name in seen:
            continue
        seen.add(name)
        flags = _parse_flags(flag_tail)
        role = {"name": name, "type": role_type}
        if role_type == "list_of_records":
            fields, optional_fields = _parse_fields(fields_str or "")
            role["fields"] = fields
            # Only when there are any, so a role that declares none is byte-for-
            # byte the dict it was before per-field flags existed.
            if optional_fields:
                role["optional_fields"] = optional_fields
            field_fills = _parse_field_fills(fields_str or "")
            if field_fills:
                role["field_fills"] = field_fills
        if label:
            role["label"] = label
        if "optional" in flags:
            role["optional"] = True
        fill = _fill_class(flags)
        if fill != SOURCE:
            role["fill"] = fill
        if "deck_wide" in flags:
            role["deck_wide"] = True
        roles.append(role)
    return roles


def load_template(path):
    """Parse a template structure spec into deck metadata and ordered slides.

    Returns a dict:
        {
            "deck_type": str,
            "slide_count": int,
            "slides": [
                {
                    "number": int,
                    "title": str,
                    "roles": [
                        {"name": str, "type": "string" | "list"},
                        {"name": str, "type": "list_of_records", "fields": [str, ...]},
                        # any role may also carry "label": str (display heading)
                        ...
                    ],
                    # optional: "section_key": str (ties the slide to a
                    # sections_requested key) and "repeat_over": str (a
                    # placeholder-map list the slide repeats over, one copy per
                    # item — the status deck's variable-count workstream slide).
                },
                ...
            ],
            "recurring_fields": [{"name": str, "type": str, ...}, ...],
        }
    """
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    deck_type_match = re.search(r"^Deck type:\s*`([^`]+)`", content, re.MULTILINE)
    deck_type = deck_type_match.group(1) if deck_type_match else None

    slide_count_match = re.search(r"^Slide count:\s*(\d+)", content, re.MULTILINE)
    declared_slide_count = int(slide_count_match.group(1)) if slide_count_match else None

    lines = content.splitlines()

    # Locate every top-level (##) heading and its line index, so each
    # section runs from its heading to the next top-level heading.
    heading_indices = [i for i, line in enumerate(lines) if line.startswith("## ")]
    heading_indices.append(len(lines))

    slides = []
    recurring_fields = []

    for idx in range(len(heading_indices) - 1):
        start = heading_indices[idx]
        end = heading_indices[idx + 1]
        heading_line = lines[start]
        section_text = "\n".join(lines[start + 1:end])

        slide_match = SLIDE_HEADING_RE.match(heading_line)
        if slide_match:
            number = int(slide_match.group(1))
            title = (slide_match.group(2) or "").strip()
            slide = {
                "number": number,
                "title": title,
                "roles": _extract_roles(section_text),
            }
            section_key_match = SECTION_KEY_RE.search(section_text)
            if section_key_match:
                slide["section_key"] = section_key_match.group(1)
            repeat_match = REPEAT_RE.search(section_text)
            if repeat_match:
                slide["repeat_over"] = repeat_match.group(1)
            slides.append(slide)
        elif heading_line.strip().startswith("## Fields that recur on every slide"):
            recurring_fields = _extract_roles(section_text)

    slides.sort(key=lambda s: s["number"])

    # If the spec declares a slide count, it must equal the number of slides
    # actually parsed from the headings. A mismatch means the spec was edited
    # inconsistently (a slide added or removed without updating the count, or a
    # malformed heading that stopped matching). Fail loudly here rather than
    # returning a slide_count that disagrees with the slides list, which would
    # let a short prompt silently pass PRD criterion 2's slide-for-slide check.
    if declared_slide_count is not None and declared_slide_count != len(slides):
        raise ValueError(
            f"template declares {declared_slide_count} slides "
            f"but {len(slides)} parsed from headings"
        )

    return {
        "deck_type": deck_type,
        "slide_count": declared_slide_count if declared_slide_count is not None else len(slides),
        "slides": slides,
        "recurring_fields": recurring_fields,
    }


# The proposal template's commercial roles retired on 2026-09-23 when slide 5
# became an adaptive deal sheet (`build-plan-commercial-slide.md`). Every one was
# declared `reviewer: sensitive`, and decks rendered before that date still carry
# their `[MISSING: ...]` markers, so the names stay declared for classification
# only. No template emits them any more.
RETIRED_SENSITIVE_ROLES = ("commercial_rows", "client_retention",
                           "downside_protection", "payment_mechanics")


def fill_expectations(template):
    """Who each named field on this deck expects to fill it, and how widely.

    Returns ``{name: {"fill": SOURCE | REVIEWER | SENSITIVE, "deck_wide": bool}}``
    over every role and every record field the template declares, keyed on the
    NAME — because the name inside a rendered ``[MISSING: <name>]`` marker is the
    only key the studio has when it classifies one, and a record field's marker
    carries the bare field name (``prompt_assembler._render_records``).

    Keying on the name means two declarations can land on the same key: the same
    field name under two roles (``week`` under both ``action_items`` and
    ``milestones``), or a role that also exists in the other deck's template. Where
    they agree, the answer is theirs. Where they DISAGREE, the entry collapses to
    the conservative answer — ``SOURCE`` for the fill class, per-instance for the
    width — rather than picking a winner:

    - ``SOURCE`` is conservative because it is the group a reviewer is meant to
      work through. Mis-filing a deliberate value there costs a second look;
      mis-filing a real gap under "deliberate, nothing to do" hides it, which is
      the whole defect this classification exists to fix.
    - per-instance is conservative because collapsing a role that actually carries
      a different value per occurrence would leave all but one of them unfillable.

    A template that disagrees with itself is a template bug rather than a runtime
    error, so it is reported by the tests (which walk the declarations directly)
    and degraded here, not raised: this runs on every render of the reviewer's
    edit cards and must not be able to take the page down.
    """
    fills, widths = {}, {}
    for slide in template.get("slides", ()):
        _collect_expectations(slide.get("roles", ()), fills, widths)
    _collect_expectations(template.get("recurring_fields", ()), fills, widths)
    # Decks rendered before the commercial slide was rebuilt still carry their
    # old sensitive markers, and a marker is classified by its NAME alone. Left
    # undeclared, those names would fall to SOURCE and become clearable, which
    # is the one thing a sensitive marker must never be. Only for the template
    # that carries the new slide, so the status deck is untouched.
    if "terms_rows" in fills:
        for name in RETIRED_SENSITIVE_ROLES:
            fills.setdefault(name, {SENSITIVE})
            widths.setdefault(name, {False})
    return {
        name: {"fill": _collapse_fill(fills[name]),
               "deck_wide": widths.get(name) == {True}}
        for name in fills
    }


def _collect_expectations(roles, fills, widths):
    """Accumulate every declaration each name carries, without collapsing yet."""
    for role in roles:
        name = role["name"]
        fills.setdefault(name, set()).add(role.get("fill", SOURCE))
        widths.setdefault(name, set()).add(bool(role.get("deck_wide")))
        field_fills = role.get("field_fills", {})
        for field in role.get("fields", ()):
            fills.setdefault(field, set()).add(field_fills.get(field, SOURCE))
            # A record field is one value per record, so it is per-instance by
            # construction and never contributes a deck-wide claim.
            widths.setdefault(field, set()).add(False)


def _collapse_fill(declared):
    """One fill class from every class declared for a name; SOURCE if they differ."""
    return next(iter(declared)) if len(declared) == 1 else SOURCE
