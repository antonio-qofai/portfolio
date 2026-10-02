"""Splitting the studio's "Supply missing values" list into what it claims to be.

The card listed every `[MISSING: ...]` marker the render left on a deck under one
heading. On the live proposal deck of 2026-08-20 that was thirty rows, of which
five were values a source failed to provide; the other twenty-five were fields we
deliberately require a human to supply, working exactly as designed. A list where
83% of the rows are working as intended teaches a reviewer to skip the list, which
defeats its only job (Antonio, 2026-08-20: "Missing values should just be missing
values unrelated to values that are missing on purpose, because those are the
commercial rows. I want the user to put those in because those are sensitive.").

Three groups, not two. The routine reviewer inputs -- a deck date, a next step's
owner and target week -- are neither sensitive nor absences, and burying them with
either kind is what made the list unreadable:

    SOURCE      the real gaps. This is the list the heading means.
    REVIEWER    we expect you to supply it; nothing went wrong.
    SENSITIVE   QofAI's own per-deal arithmetic, deliberately not sourced.

Every marker stays reachable in every group, including the sensitive ones: the
commercial-terms section does not exist yet, so a reviewer must still be able to
fill any marker from here.

WHERE THE ANSWER COMES FROM

`html_edit_layer.list_missing_markers` reads the rendered artifact and nothing
else -- deliberately, because the edit layer works on the deck alone and the
packet is long out of scope by then. So the only key available to classify a
marker by is its FIELD NAME, and the classification has to be a declaration that
can be looked up by that name.

The template is that declaration (`template_loader.fill_expectations`). It is the
only one of the four overlapping lists in this codebase keyed on the name a
marker actually carries; the other three -- `packet_assembly.REVIEWER_INPUT`,
`coverage_guard`'s path-to-reason maps, and `packet_document.ORIGINS` -- are keyed
on packet schema paths, which is the provider's key space, not the deck's. It is
also the only one that CAN answer for every marker: `commercial_rows` has no
packet path at all (D1a, 2026-08-10 moved it to studio input), so no path-keyed
list reaches it. The tests assert the path-keyed lists agree with the template
rather than compete with it.

Nothing here guesses from spelling. A name with no declaration is SOURCE, which is
what a template means by saying nothing about a field: the source was asked.
"""

from template_loader import REVIEWER, SENSITIVE, SOURCE

# Group order is the reading order of the card: the real gaps first, because they
# are the ones that need work.
GROUP_ORDER = (SOURCE, REVIEWER, SENSITIVE)


def merge_expectations(declared_maps):
    """Merge several templates' `fill_expectations` into one lookup by field name.

    For the studio's fallback when it does not know which deck type is on screen.
    Same rule as `fill_expectations` uses within one template, applied one level
    up: where two templates disagree about a name, the entry collapses to a source
    gap and per-instance. A field name means what the deck it belongs to says it
    means — `week` is a reviewer-supplied commitment under a proposal's next steps
    and a sourced schedule fact under a status deck's slip markers — so with no
    deck type to ask, the honest answer is the one that puts the marker in front of
    a reviewer rather than filing it away as deliberate.
    """
    merged = {}
    for declared in declared_maps:
        for name, entry in declared.items():
            seen = merged.get(name)
            if seen is None:
                merged[name] = dict(entry)
            elif seen != entry:
                merged[name] = {"fill": SOURCE, "deck_wide": False}
    return merged


def classify_marker(field, expectations):
    """The fill class declared for one marker's field name.

    ``SOURCE`` for a name the template says nothing about -- an undeclared name
    cannot be a deliberate omission, since deliberate is exactly what a template
    has to say out loud.
    """
    return (expectations.get(field) or {}).get("fill", SOURCE)


def group_missing_markers(markers, expectations):
    """Split `list_missing_markers` output into the three groups, deduplicated.

    Returns ``{class: [row, ...]}`` for every class in :data:`GROUP_ORDER`, each
    list in first-appearance order. A row is::

        {"field", "fill", "deck_wide", "count", "slides", "targets"}

    ``targets`` is the ``[{"slide", "source", "occurrence", "occurrences"}, ...]``
    the fill must be written to, one per marker the row covers, and ``count`` is
    its length. ``slides`` is the distinct slide numbers, for display.

    EVERY ROW IS FILLABLE. It did not used to be. A target whose marker text
    repeated on its own slide was ambiguous to `apply_text_edit`, which refuses a
    guess by design, so the card reported such a row and offered no input for it
    and sent the reviewer to the exact-text edit to name a wider span by hand. On
    the live proposal deck of 2026-08-20 that was 17 of 30 markers, which is to say
    the card refused most of its own job and taught the reviewer that supplying a
    value means pasting HTML context. `apply_text_edit` now takes the position
    (`occurrence`), and `list_missing_markers` numbers every marker in document
    order, so a repeated marker is addressed by which one it is instead of by a
    string that cannot say. Nothing here has to gate on it any more.

    DEDUPLICATION. One row per marker, EXCEPT for a field the template declares
    ``deck_wide``, whose markers collapse into a single row carrying all of them.
    The rule is deliberately not "collapse by field name": on the live deck
    `deck_date` is one value repeated in six slide footers and is the only field
    for which collapsing is right. `description` appears three times as three
    different next steps, `week` and `owner` four times as four different rows,
    and `qofai_comp` twice as two different scenarios -- collapsing any of those
    would leave all but one occurrence unfillable. So the width is a declaration
    the template makes, not something inferred from the repetition.

    A collapsed row's fill therefore fans out to several edits, which is why
    `targets` is a list even when it holds one. What happens when part of that
    fan-out cannot be applied is decided in
    `html_edit_layer.apply_edits_and_save(atomic=True)`, not here.
    """
    groups = {name: [] for name in GROUP_ORDER}
    collapsed = {}  # field name -> the one row it collapses into
    for marker in markers:
        field = marker["field"]
        fill = classify_marker(field, expectations)
        target = {"slide": marker["slide"], "source": marker["source"],
                  # Which of that marker text's appearances on the slide this is,
                  # and how many there are. Both default to 1 for a caller passing
                  # hand-built markers, which describes a unique marker.
                  "occurrence": marker.get("occurrence", 1),
                  "occurrences": marker.get("occurrences", 1)}
        row = collapsed.get(field)
        if row is not None:
            row["targets"].append(target)
            continue
        deck_wide = bool((expectations.get(field) or {}).get("deck_wide"))
        row = {"field": field, "fill": fill, "deck_wide": deck_wide,
               "targets": [target]}
        if deck_wide:
            collapsed[field] = row
        groups[fill].append(row)
    for rows in groups.values():
        for row in rows:
            row["count"] = len(row["targets"])
            # Distinct, in first-appearance order: a footer field repeated on six
            # slides reads as six slides, and two markers on one slide read as one.
            row["slides"] = list(dict.fromkeys(t["slide"] for t in row["targets"]))
    return groups


def marker_counts(groups):
    """``{class: markers}`` -- MARKERS, not rows, so the counts still sum to the
    number of `[MISSING: ...]` markers on the deck even where a row collapsed
    several. The card's heading count is what a reviewer checks against the deck.
    """
    return {name: sum(row["count"] for row in rows) for name, rows in groups.items()}
