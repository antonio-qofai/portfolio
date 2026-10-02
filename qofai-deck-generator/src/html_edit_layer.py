"""HTML edit layer — deterministic, localized reviewer edits on a rendered deck.

This is the artifact-side of the editability work (build-plan-ui.md, phases 1-3).
It never touches the deterministic content pipeline and never re-renders through
the model. It operates on an already-rendered ``output-N.html`` deck and applies
a reviewer's edit as an exact, in-place text swap inside one slide, so the change
is deterministic and does not re-flow the rest of the deck.

Two axes from the build plan stay separate here (this module only does the "this
deck only" half; learning a format edit is the preference store's job):

- Direct text edit. Replace an exact source string within a chosen slide with a
  replacement. Named by text alone, the edit refuses when the source is not found
  or is ambiguous (appears more than once) within that slide — so a reviewer can
  never silently hit the wrong occurrence. A caller that can name the occurrence
  BY POSITION passes it and gets that one, which is a different claim than naming
  it by text and is why it has to be asked for explicitly.
- Supply a missing value. A ``[MISSING: ...]`` marker the render left on a slide
  is just a specific source string; ``list_missing_markers`` surfaces each one so
  the UI can offer to fill it, and the fill is the same exact-swap edit (replacing
  the whole highlighted marker span with the supplied value). Because that listing
  numbers the markers in document order, it is the caller that can name a position,
  which is what makes a marker repeated on one slide fillable rather than merely
  reported.

One rule underneath both: an edit changes DISPLAY TEXT and never markup. A source
may name a whole element to make its match unique, but the tags at the ends of
that span belong to the document and stay exactly as they were, and the only
thing ever inserted is escaped text. That is what keeps a tag off a slide (the
2026-07-23 leak) and what makes an edit reversible — a swap and its reverse are
both text swaps, so stating one and then the other restores the deck byte for
byte. `display_text_guard` checks it on every span an edit writes.

The original model-rendered deck is never overwritten. An edit writes a numbered
revision file beside it (``output-N-r1.html``, ``output-N-r2.html``, ...) and
appends one entry to a co-located edit log (``output-N.edits.json``), so the
render-fidelity audit trail survives and every change is traceable to a slide, a
kind (format / content), a before, an after, an author, and a timestamp.

That chain is single-threaded: a deck has one current revision, and every edit is
applied to it (:func:`current_revision_path`), whatever path the caller passes.
The counter and the log key off the shared base, so an edit computed from an
older file would be numbered as if it followed the newest one and would silently
drop everything in between — the deck losing changes its own history still lists.

Slides are addressed by their 1-based document order (the Nth
``<section class="slide">``). ``stamp_slide_ids`` writes that same order onto each
slide as a ``data-slide`` attribute so the rendered deck carries a stable, visible
identifier; addressing here works whether or not a deck was stamped, so it also
edits older decks that predate the stamp.

The text functions are pure (they take and return strings). The ``*_and_save``
wrapper owns the filesystem, mirroring how ``gap_resolver`` keeps its write in the
caller.
"""

import html as _html
import json
import os
import re
from datetime import datetime, timezone

import text_gate

from display_text_guard import visible_tags

# A slide is a `<section ... class="... slide ...">` block. The class list may
# carry variants (e.g. `slide slide--dark`); we only require the `slide` token.
_SECTION_OPEN_RE = re.compile(
    r"<section\b[^>]*\bclass=\"[^\"]*\bslide\b[^\"]*\"[^>]*>", re.IGNORECASE
)
_SECTION_CLOSE = "</section>"

# Reviewer markers the render carries through verbatim (deck_renderer wraps each
# in `<span class="flag">`). `[MISSING: ...]` is an absent value the reviewer can
# supply; `(unconfirmed, see gaps)` is a present-but-unconfirmed claim handled by
# the packet-level gap resolver, listed here only for completeness.
_FLAG_SPAN_RE = re.compile(r"<span class=\"flag\">(.*?)</span>", re.IGNORECASE | re.DOTALL)
_MISSING_RE = re.compile(r"\[MISSING:[^\]]*\]")
_UNCONFIRMED = "(unconfirmed, see gaps)"

# A deck filename is `output-<N>.html`; a revision is `output-<N>-r<K>.html`. The
# base stem (`output-<N>`) is shared by an original and all its revisions, so the
# revision counter and the edit log key off the base, never off a revision file.
_DECK_NAME_RE = re.compile(r"^(?P<base>.+?)(?:-r(?P<rev>\d+))?\.html$", re.IGNORECASE)

# A run of characters that reads as an HTML tag (`<...>`). A literal `<` in slide
# *content* is escaped to `&lt;` in a well-formed deck, so `<...>` in a string
# taken from the document is genuine markup, not text with a stray bracket.
_TAG_RE = re.compile(r"<[^>]+>")

# A tag inside text a HUMAN typed. Stricter than `_TAG_RE` on purpose: a reviewer
# writing "margin < 5 and headcount > 3" is writing arithmetic, and treating that
# as markup would delete the words between the brackets. Requires a tag name right
# after the bracket, which is what the display-text guard looks for too.
_TAGGISH_RE = re.compile(r"</?[a-zA-Z][a-zA-Z0-9-]*(?:\s[^<>]*)?/?>")

# The markup that can sit at the ENDS of a matched span: an edit that names a
# whole element (`<span class="base">Project Planning.</span>`) is naming the text
# inside it, and those tags belong to the document rather than to the edit.
_LEADING_TAGS_RE = re.compile(r"^(?:<[^>]+>)+")
_TRAILING_TAGS_RE = re.compile(r"(?:<[^>]+>)+$")

# The renderer's reviewer-marker badge. The wrapper is derived from the marker
# text rather than owned by the reviewer, so filling a marker drops the badge and
# restoring the marker brings it back (see `_canonical_markers`).
_FLAG_OPEN = '<span class="flag">'
_UNWRAPPED_MISSING_RE = re.compile(
    rf"(?<!{re.escape(_FLAG_OPEN)})(\[MISSING:[^\]]*\])"
)


def _looks_like_markup(text):
    """True when ``text`` contains what reads as an HTML tag (``<...>``)."""
    return bool(_TAG_RE.search(text))


def _split_outer_markup(fragment):
    """``(leading_tags, body, trailing_tags)`` for a span matched in the deck.

    Splits off the tags sitting at each end, leaving the text the edit is really
    about. A tag left inside ``body`` means the match crosses an element boundary,
    which the caller refuses rather than guessing where the new text belongs.
    """
    lead = _LEADING_TAGS_RE.match(fragment)
    prefix = lead.group(0) if lead else ""
    rest = fragment[len(prefix):]
    trail = _TRAILING_TAGS_RE.search(rest)
    suffix = trail.group(0) if trail else ""
    return prefix, rest[:len(rest) - len(suffix)], suffix


def _display_text(text):
    """What ``text`` reads as on a slide: markup carries no display text of its own.

    A replacement the free-text interpreter returns repeats the source's tags (its
    system prompt tells it to) or drops them (it sometimes does anyway); either
    way the only part that belongs on the slide is the text between them, with its
    entities resolved so re-escaping it on insertion is exact rather than doubled.
    Reviewer-typed text carries no tags and is taken as it stands.
    """
    if not _TAGGISH_RE.search(text):
        return text
    return _html.unescape(_TAGGISH_RE.sub("", text))


def _canonical_markers(fragment):
    """Keep the reviewer-marker badge tied to the marker text it annotates.

    The renderer wraps every ``[MISSING: ...]`` marker in ``<span class="flag">``
    so a gap is visible. Since an edit changes text and never markup, filling a
    marker would otherwise leave the supplied value wearing the gap badge, and
    restoring the marker would leave it bare. Deriving the badge from the text
    keeps the two exact inverses: a flag span whose body is no longer a marker is
    unwrapped, and a bare marker is wrapped. Idempotent, and applied only to the
    span an edit actually touched, so a badge elsewhere on the slide is untouched.
    """
    fragment = _FLAG_SPAN_RE.sub(
        lambda m: m.group(0) if _MISSING_RE.fullmatch(m.group(1).strip())
        else m.group(1),
        fragment,
    )
    return _UNWRAPPED_MISSING_RE.sub(rf"{_FLAG_OPEN}\1</span>", fragment)


class EditNotApplicable(Exception):
    """A reviewer edit could not be applied deterministically.

    Raised when the target slide does not exist, or the source string is not
    found or is ambiguous (appears more than once) within that slide. Carries a
    human-readable message; nothing is written when it is raised.
    """


class BulletDoesNotFit(EditNotApplicable):
    """A bullet was switched on, and the panel could not show it.

    A subclass rather than a message, because the two refusals need different
    words from a reviewer. `EditNotApplicable` on this path means the deck is
    not in a shape the edit understands, and the answer is to look at the deck
    or re-run it. This one means the edit was understood and applied, the
    result was measured, and the panel clipped -- the answer is to switch
    something else off or shorten the line.

    It is raised only AFTER the candidate document has been thrown away, so a
    deck that cannot show a bullet is a deck that was never written to.
    """



# ------------------------------- slide model -------------------------------

def find_slides(html):
    """Return the slide blocks in document order.

    Each entry is ``{"index", "open_start", "inner_start", "inner_end",
    "close_end"}`` where ``index`` is 1-based, ``inner_start:inner_end`` bounds the
    slide's inner HTML (between the open tag and its ``</section>``), and
    ``open_start:close_end`` bounds the whole ``<section>...</section>`` block.
    Slides do not nest, so each open tag pairs with the next ``</section>``.
    """
    slides = []
    for index, match in enumerate(_SECTION_OPEN_RE.finditer(html), start=1):
        inner_start = match.end()
        close_at = html.find(_SECTION_CLOSE, inner_start)
        if close_at == -1:
            # An unclosed final section: treat the rest of the document as its
            # body so a malformed tail never silently drops a slide.
            inner_end = close_end = len(html)
        else:
            inner_end = close_at
            close_end = close_at + len(_SECTION_CLOSE)
        slides.append({
            "index": index,
            "open_start": match.start(),
            "inner_start": inner_start,
            "inner_end": inner_end,
            "close_end": close_end,
        })
    return slides


def slide_count(html):
    """Number of ``<section class="slide">`` blocks in the deck."""
    return len(find_slides(html))


def stamp_slide_ids(html):
    """Stamp each slide with its 1-based ``data-slide`` order, idempotently.

    Adds ``data-slide="N"`` to every ``<section class="slide">`` open tag that
    lacks one, so the rendered deck carries a stable, visible per-slide identifier
    matching the order this module addresses by. A tag that already has a
    ``data-slide`` is left untouched, so re-stamping (or stamping a hand-edited
    revision) is a no-op. A document with no slide sections is returned unchanged,
    which keeps it safe as a render post-process step.
    """
    slides = find_slides(html)
    if not slides:
        return html
    # Rewrite from the end so earlier offsets stay valid as we splice.
    out = html
    for slide in reversed(slides):
        open_tag = out[slide["open_start"]:slide["inner_start"]]
        if "data-slide=" in open_tag.lower():
            continue
        stamped = open_tag[:-1] + f' data-slide="{slide["index"]}">'
        out = out[:slide["open_start"]] + stamped + out[slide["inner_start"]:]
    return out


def _slide_inner_bounds(html, slide_index):
    slides = find_slides(html)
    if slide_index < 1 or slide_index > len(slides):
        raise EditNotApplicable(
            f"slide {slide_index} does not exist (deck has {len(slides)} slide"
            f"{'s' if len(slides) != 1 else ''})"
        )
    slide = slides[slide_index - 1]
    return slide["inner_start"], slide["inner_end"]


# ------------------------------- edits -------------------------------------

# The opening words of the ambiguity refusal, stated once so a caller can tell
# an ambiguous target from an absent one without matching a literal of its own.
# The two refusals need different remedies from a reviewer: an absent source is
# retyped, an ambiguous one is named again over a wider span. `ui/app.py` reads
# this to word the free-text edit's notice; see `AMBIGUOUS_SOURCE_REMEDY` there.
AMBIGUOUS_SOURCE = "source text is ambiguous"


def _find_unique(haystack, needle):
    """Return the single index of ``needle`` in ``haystack``.

    ``None`` when ``needle`` is absent; raises ``EditNotApplicable`` when it
    appears more than once (an ambiguous edit target).
    """
    if not needle:
        return None
    first = haystack.find(needle)
    if first == -1:
        return None
    if haystack.find(needle, first + 1) != -1:
        count = haystack.count(needle)
        raise EditNotApplicable(
            f"{AMBIGUOUS_SOURCE} — it appears {count} times on this slide; "
            f"nothing changed"
        )
    return first


def _find_nth(haystack, needle, n):
    """Return the index of the ``n``-th (1-based) occurrence of ``needle``.

    ``None`` when ``needle`` occurs fewer than ``n`` times. Where
    :func:`_find_unique` refuses a repeated string as ambiguous, this one resolves
    it by position, which is what a caller holding an enumeration of the document
    already knows and the string alone cannot say.
    """
    if not needle:
        return None
    if n < 1:
        raise EditNotApplicable(
            f"occurrence {n} is not a position on the slide; the first is 1"
        )
    at = -1
    for _ in range(n):
        at = haystack.find(needle, at + 1)
        if at == -1:
            return None
    return at


def _locate(haystack, needle, occurrence):
    """Where an edit's source sits: by uniqueness, or by position when given one."""
    if occurrence is None:
        return _find_unique(haystack, needle)
    return _find_nth(haystack, needle, occurrence)


def _match_in(inner, source, occurrence):
    """Locate ``source`` in one slide's inner HTML, literal form then escaped.

    Returns ``(matched, at)``, or ``(source, None)`` when it is not there. Stated
    once because two callers need the same answer: the edit itself, and
    :func:`gated_replacement`, which has to know WHERE the text lands before it
    can gate it the way that spot would have been gated.
    """
    at = _locate(inner, source, occurrence)
    if at is not None:
        return source, at
    escaped = _html.escape(source, quote=False)
    if escaped != source:
        at = _locate(inner, escaped, occurrence)
        if at is not None:
            return escaped, at
    return source, None


def _gated_at(inner, at, replacement):
    """The replacement text as it will land at ``at`` within ``inner``.

    THE one statement of how a reviewer's text is gated, and it is one statement
    on purpose. It was two until 2026-09-16, and the review window found the
    hazard by mutation with the suite green: `gated_replacement` decided `prose`
    for the log and `apply_text_edit` decided it again for the slide, so the two
    could disagree about the same edit. The studio reaches the edit layer only
    through the `*_and_save` wrappers, which gate here FIRST, and the second gate
    is then a no-op by idempotence -- so the site with no test was the one
    deciding what every real edit looks like, and pinning it to prose left 2050
    tests passing while a header edit landed reading "Overview, phase two"
    instead of "Overview - phase two".

    `at` is None when the source is not on the slide. The edit that follows is
    about to be refused, so the text is gated as non-prose rather than guessed at.
    """
    text = _display_text(replacement)
    if not text:
        return text
    prose = text_gate.prose_at(inner, at) if at is not None else False
    return text_gate.gate_fragment(text, prose=prose).html


def gated_replacement(html, slide_index, source, replacement, occurrence=None):
    """The reviewer's replacement text as it will actually land on the slide.

    PART ONE ITEM 6. An em dash reached a rendered slide during a live edit in
    front of Casey and Jordan ([17:50] of the 2026-08-20 demo). The guardrail did
    not leak: `text_gate` runs inside the render leg (`deck_renderer`
    `_apply_house_text_gate`) and the edit path never called it at all. So text a
    reviewer typed went onto a slide under no format rules, while every line the
    render wrote went under all of them.

    The unit gated is the reviewer's text and never the deck. Running the whole
    gate over a revision file would re-derive a factual index for a document this
    path is not rewriting, to prove something about content it has no prompt for.

    WHERE this runs is the load-bearing part, and it is why this is a function
    rather than three lines inside the swap. The edit layer's guarantee is that a
    swap and its reverse restore the deck byte for byte, and a gate that rewrites
    what a reviewer typed breaks that the moment the LOG records the typed text
    while the SLIDE carries the rewritten text: the logged "after" would name a
    string that is not on the deck. So the gate runs before the swap is recorded,
    and both save paths log what this returns.

    Gated as the spot itself would have been gated: `text_gate.prose_at` reads
    the open-tag stack at the match, so a dash inside a `<p>` becomes a comma and
    one in a header becomes a spaced hyphen, exactly as the render leg would have
    written it. An unlocatable source gates as non-prose, since the edit that
    follows is about to be refused anyway.
    """
    try:
        inner_start, inner_end = _slide_inner_bounds(html, slide_index)
    except EditNotApplicable:
        return _gated_at("", None, replacement)
    inner = html[inner_start:inner_end]
    _matched, at = _match_in(inner, source, occurrence)
    return _gated_at(inner, at, replacement)


def apply_text_edit(html, slide_index, source, replacement, occurrence=None,
                    expect_occurrences=None):
    """Replace one occurrence of ``source`` on slide ``slide_index``.

    Returns the new document. ``source`` is matched exactly within that slide's
    inner HTML; if the literal string is not found, its HTML-escaped form is tried
    too, so a reviewer can paste the visible text even when it contains ``&``,
    ``<`` or ``>``. Raises ``EditNotApplicable`` when the slide does not exist, the
    source is empty, or the source is not found on the slide — the edit must hit
    exactly one known spot or fail.

    WHICH occurrence, when the string repeats, is the caller's to say. With
    ``occurrence=None`` (the default) a repeated source is ambiguous and the edit
    is refused, which is the contract the free-text interpreter depends on: it
    names spans by text alone, so a second match means it has not identified a
    single place and guessing would edit whichever came first. With ``occurrence``
    set to a 1-based position, that position IS the identification and the edit
    lands on it; fewer occurrences than the index asks for is a refusal.

    Only a caller holding an enumeration of the document may pass an index, since
    the index means nothing without one. That caller is
    :func:`list_missing_markers`, which numbers every marker in document order:
    four ``[MISSING: week]`` markers on one next-steps slide are four different
    weeks wearing one string, and their position is the only thing that tells them
    apart. Opt-in rather than the default, so nothing that names text by text
    alone can silently start editing a guess.

    ``expect_occurrences`` is how many of ``source`` the caller's enumeration saw
    on this slide, and passing it alongside an index is strongly advised. AN INDEX
    GOES STALE. Filling one occurrence consumes it and renumbers every occurrence
    after it, so a listing taken once and spent over several edits stops describing
    the document after the first one lands. The failure is silent where it matters
    most: with four markers, filling #1 and then asking for #3 finds a #3 — the
    one that used to be #4 — and writes a reviewer's value onto a row nobody named,
    with nothing on the deck to show it happened. Stating the expected total turns
    that into a refusal, because a total that no longer matches is proof the
    enumeration is stale. :func:`apply_edits_and_save` orders a batch so indices
    stay valid within it; this guard is what catches an enumeration stale before
    the batch even began, which ordering cannot.

    An edit changes DISPLAY TEXT and never markup, which is what keeps tags off
    the slide and what makes an edit reversible. The tags at the ends of the
    matched span (the ``<span class="base">`` an interpreter includes to make a
    match unique) belong to the document and are kept exactly as they were; the
    text between them is replaced by the replacement's display text, HTML-escaped,
    so nothing a reviewer or the interpreter supplies can print as a tag or inject
    markup into the fixed slide frame. Tags in the replacement are therefore
    ignored rather than refused: repeating the source's tags and dropping them
    produce the same slide. Deleting the source (removing a marker) is a valid
    edit: pass an empty replacement.

    Raises ``EditNotApplicable`` when the matched span CROSSES markup (a tag sits
    inside it rather than at its ends), because there is no single place the new
    text belongs and flattening the element would silently drop it.

    One derived detail, in :func:`_canonical_markers`: the renderer's
    ``<span class="flag">`` badge follows the marker text it wraps, so filling a
    ``[MISSING: ...]`` marker drops the badge with it and restoring that marker
    brings the badge back, byte for byte.
    """
    if not source:
        raise EditNotApplicable("no source text given; nothing to change")

    inner_start, inner_end = _slide_inner_bounds(html, slide_index)
    inner = html[inner_start:inner_end]

    matched, at = _match_in(inner, source, occurrence)
    if at is None:
        if occurrence is None:
            raise EditNotApplicable(
                "source text was not found on this slide; nothing changed"
            )
        # An index that overshoots is its own failure, and saying how many the
        # slide holds is what tells a reviewer whether the deck moved under them.
        held = inner.count(source) or inner.count(
            _html.escape(source, quote=False))
        raise EditNotApplicable(
            f"occurrence {occurrence} of that text is not on this slide (it "
            f"appears {held} time{'s' if held != 1 else ''} there); nothing changed"
        )

    if expect_occurrences is not None:
        held = inner.count(matched)
        if held != expect_occurrences:
            raise EditNotApplicable(
                f"this slide holds that text {held} time{'s' if held != 1 else ''}, "
                f"not the {expect_occurrences} the list of gaps was taken from — the "
                f"deck has changed since then, so a position from that list no "
                f"longer names the same place. Nothing was written; reload the deck "
                f"and supply it again."
            )

    # The matched span, split into the document's own markup and the text the edit
    # is about. Only the text is replaced, and only ever with escaped text, so the
    # rewrite cannot put a tag on the slide in either direction.
    prefix, body, suffix = _split_outer_markup(inner[at:at + len(matched)])
    if _looks_like_markup(body):
        raise EditNotApplicable(
            "the source spans markup on this slide rather than sitting inside one "
            "element; narrow it to the text you want changed"
        )
    # The house format rules, on the text this edit writes and on nothing else
    # (part one item 6). Idempotent, so a caller that already gated the text --
    # both save paths do, because they log what lands -- gets the same string
    # back. Here as well as there so a direct caller cannot route around it.
    gated = _gated_at(inner, at, replacement)
    new_span = _canonical_markers(
        prefix + _html.escape(gated, quote=False) + suffix
    )
    # The guard at this boundary, on the one span this edit writes. By construction
    # nothing here can print as a tag; the check is what makes that a guarantee
    # rather than an argument, and refusing the edit (rather than raising through)
    # means the batch layer reports it and nothing reaches the deck.
    leaked = visible_tags(new_span)
    if leaked:
        raise EditNotApplicable(
            f"this edit would print {leaked[0]} as text on the slide; refused so "
            f"markup can never show up in the deck's copy"
        )
    new_inner = inner[:at] + new_span + inner[at + len(matched):]
    return html[:inner_start] + new_inner + html[inner_end:]


def list_missing_markers(html):
    """Surface every ``[MISSING: ...]`` marker the render left on the deck.

    Returns ``[{"slide", "field", "marker", "source", "occurrence",
    "occurrences"}, ...]`` in document order, one per marker. ``field`` is the text
    inside the marker (e.g. ``client_short`` from ``[MISSING: client_short]``);
    ``marker`` is the marker itself; ``source`` is the exact string the UI should
    pass to :func:`apply_text_edit` to fill it — the whole
    ``<span class="flag">[MISSING: ...]</span>`` when the marker is wrapped (so
    filling it also drops the highlight), otherwise the bare marker.

    ``occurrence`` is WHICH of that ``source`` string's appearances on that slide
    this marker is, 1-based in document order, and ``occurrences`` is HOW MANY
    there are. Every marker carries both, so ``occurrence`` is 1 of 1 for a marker
    whose text is unique.

    Together they are what makes a repeated marker fillable. Four
    ``[MISSING: week]`` markers on one next-steps slide are four different values
    wearing the same string; the string cannot tell them apart but this
    enumeration can, and ``occurrence`` is exactly what
    :func:`apply_text_edit` takes to aim at one of them. Passing it is how a
    caller says it knows which one it means, and this is the only function in the
    module that can honestly say so.

    These are the values a reviewer supplies by writing into the slide: filling one
    is a content edit that applies to this deck only. It is never written back to
    the packet and never learned as a preference, so the never-fabricate guarantee
    holds — a supplied value is a visible, logged reviewer edit on the artifact.
    """
    out = []
    for slide in find_slides(html):
        inner = html[slide["inner_start"]:slide["inner_end"]]
        spanned = {}  # marker text -> full span, so a wrapped marker fills cleanly
        for span in _FLAG_SPAN_RE.finditer(inner):
            body = span.group(1)
            for marker in _MISSING_RE.findall(body):
                spanned.setdefault(marker, span.group(0))
        # How many of each source string this slide's walk has passed, so the
        # next one gets the position after it. Keyed on `source` rather than on
        # the marker text, because `source` is the string `apply_text_edit`
        # searches and an index has to count the same thing the search will.
        seen = {}
        for marker in _MISSING_RE.finditer(inner):
            text = marker.group(0)
            field = text[len("[MISSING:"):-1].strip()
            source = spanned.get(text, text)
            seen[source] = seen.get(source, 0) + 1
            out.append({
                "slide": slide["index"],
                "field": field,
                "marker": text,
                "source": source,
                "occurrence": seen[source],
                # Counted on this slide's inner HTML, the same span
                # `apply_text_edit` searches, so the two always agree.
                "occurrences": inner.count(source),
            })
    return out


# ------------------------------ clear a marker -------------------------------

# The separator the deck joins a tag's parts with: "DAYS 2–3 · CCO".
_SEPARATOR = "\u00b7"
_DOUBLED_SEPARATOR_RE = re.compile(r"\s*\u00b7\s*(?:\u00b7\s*)+")
_LOOSE_SEPARATOR_RE = re.compile(r"^\s*(?:\u00b7\s*)+|(?:\s*\u00b7)+\s*$")
_OPEN_TAG_RE = re.compile(r"<([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>")


def _tidy_separators(text):
    """A tag's text with the separators a cleared part left behind taken out.

    "[MISSING: week] · CCO" cleared reads " · CCO", which would print a dangling
    dot; this makes it "CCO". A separator between two surviving parts stays.
    """
    return _LOOSE_SEPARATOR_RE.sub("", _DOUBLED_SEPARATOR_RE.sub(" \u00b7 ", text))


def _enclosing_element(inner, at):
    """``(open_tag, ordinal)`` of the innermost element holding position ``at``.

    ``ordinal`` counts that exact opening tag's appearances on the slide up to
    and including this one, 1-based. Clearing a marker removes only the flag
    badge, never an element around it, so the n-th ``<div class="tag">`` before
    the clear is the n-th after it, which is how the tidy finds the same tag.
    """
    best = None
    for match in _OPEN_TAG_RE.finditer(inner, 0, at):
        tag = match.group(1).lower()
        if tag in ("br", "img", "hr", "input") or match.group(0).startswith(_FLAG_OPEN):
            continue
        if _element_end(inner, match.start(), tag) > at:
            best = match
    if best is None:
        return None
    opening = best.group(0)
    return opening, inner.count(opening, 0, best.start()) + 1


def clear_marker_edits(html, markers):
    """The batch that clears ``markers`` and tidies what clearing leaves behind.

    Part A3 of `build-plan-phase4-flags-and-fit.md`. Antonio, 2026-09-23: "maybe
    there is no owner, or there is no designated week", and "you press ... and
    then it just goes away." ``markers`` are rows of :func:`list_missing_markers`.

    TWO KINDS OF EDIT, because one swap may not cross markup. Each marker is
    replaced with nothing, which is a valid edit and takes its flag badge with it
    (:func:`_canonical_markers`). The separator beside it is NOT inside the
    badge: ``<div class="tag"><span class="flag">[MISSING: week]</span> · CCO
    </div>`` clears to ``<div class="tag"> · CCO</div>``, so each element that
    held a cleared marker gets a second edit that swaps its whole text for the
    tidied text, keeping its tags. Those are computed against the document as
    the removals leave it, which is the document :func:`apply_edits_and_save`
    has in hand when it reaches them, since it applies groups in the order they
    first appear and every tidy comes after every removal.

    An element that still holds other markup after the clear is left alone: its
    text is not one run, and a separator in it is not this function's to judge.

    Returns the edit list, removals first. Nothing is written here.
    """
    removals = [{
        "slide": marker["slide"], "source": marker["source"], "replacement": "",
        "occurrence": marker.get("occurrence"),
        "occurrences": marker.get("occurrences"),
        "note": f"cleared {marker.get('marker') or marker.get('field', '')}",
    } for marker in markers]

    # Where each marker sits, in the document as it stands now.
    held = []
    for marker in markers:
        start, end = _slide_inner_bounds(html, int(marker["slide"]))
        inner = html[start:end]
        at = _locate(inner, marker["source"], marker.get("occurrence"))
        if at is None:
            continue
        element = _enclosing_element(inner, at)
        if element:
            held.append((marker["slide"],) + element)

    # The document as the removals leave it.
    cleared = html
    for position in _application_order(removals):
        edit = removals[position]
        cleared = apply_text_edit(
            cleared, int(edit["slide"]), edit["source"], "",
            occurrence=_edit_occurrence(edit),
            expect_occurrences=_expected_occurrences(edit, removals))

    tidies = []
    for slide, opening, ordinal in dict.fromkeys(held):
        start, end = _slide_inner_bounds(cleared, int(slide))
        inner = cleared[start:end]
        at = -1
        for _ in range(ordinal):
            at = inner.find(opening, at + 1)
            if at == -1:
                break
        if at == -1:
            continue
        tag = _OPEN_TAG_RE.match(inner, at).group(1).lower()
        outer = inner[at:_element_end(inner, at, tag)]
        body = outer[len(opening):-len(f"</{tag}>")]
        if "<" in body:
            continue
        tidied = _tidy_separators(body)
        if tidied == body:
            continue
        tidies.append({
            "slide": slide, "source": outer, "replacement": tidied,
            "occurrence": inner.count(outer, 0, at) + 1,
            "occurrences": inner.count(outer),
            "note": "tidied the separator a cleared marker left",
        })
    return removals + tidies


# --------------------------- progress checkbox ------------------------------

# The renderer writes a checkbox and its label adjacently, with the state in
# the class attribute and the check mark (when present) as the chk span's own
# text: `<span class="chk done">&#10003;</span><span class="ptext">Label</span>`.
# `pending` and `in_process` carry no glyph — `in_process`'s half-fill is a CSS
# background gradient on the class, not text — so the class and the glyph
# below are the two things a toggle must move together.
_CHK_OPEN_RE = re.compile(r'<span class="chk (done|pending|in_process)">')
_PTEXT_OPEN = '<span class="ptext">'
_CHK_CLOSE = "</span>"
_CHK_GLYPH = {"done": "&#10003;", "pending": "", "in_process": ""}
_PROGRESS_STATES = frozenset(_CHK_GLYPH)


def _element_end(text, open_at, tag):
    """Index just past the ``</tag>`` that closes the ``<tag ...>`` at ``open_at``.

    Counts nested opens against closes, so an element nested inside cannot fool a
    naive "next close tag" search into ending the outer one early — a ``.pdetail``
    inside a ``.ptext``, or the ``.st`` children of a ``.mech-steps``.
    """
    close = f"</{tag}>"
    depth = 1
    at = text.index(">", open_at) + 1
    while depth > 0:
        next_open = text.find(f"<{tag}", at)
        next_close = text.find(close, at)
        if next_close == -1:
            return len(text)
        if next_open != -1 and next_open < next_close:
            depth += 1
            at = text.index(">", next_open) + 1
        else:
            depth -= 1
            at = next_close + len(close)
    return at


def _span_end(text, open_at):
    """Index just past the ``</span>`` closing the ``<span ...>`` at ``open_at``."""
    return _element_end(text, open_at, "span")


def _find_progress_items(inner):
    """Every progress-tracker item on one slide's inner HTML, in document order.

    A chk span not immediately followed by a ptext span is not a progress
    item and is skipped. Returns ``[{"label", "state", "start", "chk_end"},
    ...]``: ``start:chk_end`` bounds the chk span alone, which is all a toggle
    ever rewrites — the ``.ptext`` label after it is untouched.
    """
    items = []
    for match in _CHK_OPEN_RE.finditer(inner):
        chk_end = inner.find(_CHK_CLOSE, match.end())
        if chk_end == -1:
            continue
        chk_end += len(_CHK_CLOSE)
        if inner[chk_end:chk_end + len(_PTEXT_OPEN)] != _PTEXT_OPEN:
            continue
        ptext_end = _span_end(inner, chk_end)
        body = inner[chk_end + len(_PTEXT_OPEN):ptext_end - len(_CHK_CLOSE)]
        label = _html.unescape(_TAG_RE.sub("", body)).strip()
        items.append({
            "label": label, "state": match.group(1),
            "start": match.start(), "chk_end": chk_end,
        })
    return items


def _locate_progress_item(inner, label):
    """The single progress item with this exact label on one slide.

    Raises ``EditNotApplicable`` when no item carries that label or more than
    one does — the same "hit exactly one known spot or fail" rule
    :func:`apply_text_edit` applies to a text source.
    """
    if not label:
        raise EditNotApplicable("no item label given; nothing to change")
    matches = [item for item in _find_progress_items(inner) if item["label"] == label]
    if not matches:
        raise EditNotApplicable(
            "no progress item with that label was found on this slide; nothing changed"
        )
    if len(matches) > 1:
        raise EditNotApplicable(
            f"that label is ambiguous — it matches {len(matches)} progress items "
            f"on this slide; nothing changed"
        )
    return matches[0]


def list_progress_items(html):
    """Every progress-tracker item across the deck, in document order.

    Returns ``[{"slide", "label", "state"}, ...]`` — what a reviewer sees and
    picks from to correct one item's checkbox. ``label`` is the exact text to
    pass to :func:`toggle_progress_item`; it must still read on the slide when
    the toggle runs, the same as a text edit's source.
    """
    out = []
    for slide in find_slides(html):
        inner = html[slide["inner_start"]:slide["inner_end"]]
        for item in _find_progress_items(inner):
            out.append({
                "slide": slide["index"], "label": item["label"], "state": item["state"],
            })
    return out


def toggle_progress_item(html, slide_index, label, new_state):
    """Set one progress item's checkbox state on one slide; return the new document.

    A checkbox's state lives in a class attribute, so this is a MARKUP change
    and is never routed through :func:`apply_text_edit`, which by construction
    changes display text and never markup. Locates the item by its exact
    label text (:func:`_locate_progress_item`) and rewrites its whole
    ``<span class="chk {state}">...</span>`` — class and glyph together, never
    the class alone, because `done` carries a check mark while `pending` and
    `in_process` carry none, so leaving the old glyph in place would print the
    wrong mark on the new color. The ``.ptext`` label is untouched.
    """
    if new_state not in _PROGRESS_STATES:
        raise EditNotApplicable(f"'{new_state}' is not a progress state")
    inner_start, inner_end = _slide_inner_bounds(html, slide_index)
    inner = html[inner_start:inner_end]
    item = _locate_progress_item(inner, label)
    new_chk = f'<span class="chk {new_state}">{_CHK_GLYPH[new_state]}</span>'
    new_inner = inner[:item["start"]] + new_chk + inner[item["chk_end"]:]
    return html[:inner_start] + new_inner + html[inner_end:]


# ---------------------- slide 2 bullets, on and off -------------------------

# A reviewer's bullet switch, applied to the deck itself rather than recorded
# for the next render (Antonio, 2026-09-22: "Why can't the toggle switches edit
# the deck in real time, without having to re-generate a deck?").
#
# WHY THIS IS NOT `apply_text_edit`. A bullet is a whole `<li>`, so switching
# one off DELETES an element and switching one on CREATES one. Text swaps by
# construction change display text and never markup. This is the
# `toggle_progress_item` pattern one step further: find a known element the
# renderer emits, and add or remove one of its children.
#
# WHAT KEEPS THE TWO GUARANTEES THE TEXT SWAP HAS.
#
# Tags still cannot reach a slide. The studio GENERATES the markup here rather
# than accepting it: an inserted bullet is `<li>` + escaped text + `</li>`, and
# a tag in the bullet's text is escaped exactly as `apply_text_edit` escapes a
# replacement. Nothing a model or a reviewer supplies becomes markup.
#
# It is still reversible, and needs no inverse recorded, because undo on this
# deck is FILE-based: `undo_last_edit` deletes the top revision and the prior
# one becomes current. A structural edit writes a revision like any other, so
# it undoes like any other. (The build plan called for recording the removed
# element and its index to invert; reading `undo_last_edit` showed that the
# revision chain already is the inverse.)
#
# WHAT THIS DOES NOT DO: decide whether the bullet FITS. A panel is a fixed box
# with `overflow:hidden`, so an added line can be clipped rather than visibly
# overflowing, and nothing in a string can see that. The caller measures the
# candidate document and throws it away if it does not fit; see the `verify`
# argument on `set_bullet_presence_and_save`.

_BULLETS_UL_RE = re.compile(r'<ul class="bullets"[^>]*>')
_PANEL_OPEN_RE = re.compile(r'<div class="panel[^"]*"[^>]*>')
_LI_OPEN_RE = re.compile(r"<li[^>]*>")

# The panel each slide-2 bullet role is rendered into. These are the class names
# `deck_renderer`'s scaffold uses and the prompt asks a model to reproduce; a
# deck that comes back without them cannot be edited here and says so rather
# than being guessed at.
_BULLET_ROLE_PANEL = {
    "today_pain_bullets": "panel--today",
    "after_capability_bullets": "panel--after",
}


def opportunity_slides(html):
    """The slides that carry a slide-2 bullet list, in document order.

    A deck with several opportunities carries one slide 2 EACH, in the order
    the reviewer picked them, and a switch belongs to exactly one of them. The
    switch form posts that pick order as an integer, so this is what turns the
    integer into a slide: the Nth slide holding a bullet list is opportunity N.

    Keyed on the bullet list rather than on a slide number, because slide 2 is
    only slide 2 on a single-opportunity deck; on a three-opportunity deck the
    third one is slide 4.
    """
    out = []
    for slide in find_slides(html):
        inner = html[slide["inner_start"]:slide["inner_end"]]
        if _BULLETS_UL_RE.search(inner):
            out.append(slide)
    return out


def locate_bullet_list(html, *, opportunity, role):
    """Offsets of one opportunity's one bullet list.

    ``{"slide", "inner_start", "inner_end"}``, where inner bounds the contents
    of the ``<ul class="bullets">`` between its tags.

    Raises ``EditNotApplicable`` naming WHICH step failed, because the render is
    model-produced and every one of these is a real possibility rather than an
    impossible state. A message that says only "not found" sends a reviewer to
    look at the wrong half of the deck.
    """
    panel_class = _BULLET_ROLE_PANEL.get(role)
    if not panel_class:
        raise EditNotApplicable(
            f"'{role}' is not a slide 2 bullet role, so there is no panel on "
            f"the deck it belongs to"
        )

    slides = opportunity_slides(html)
    if not slides:
        raise EditNotApplicable(
            "this deck carries no slide 2 bullet list at all, so there is "
            "nothing on it to switch"
        )
    if opportunity < 0 or opportunity >= len(slides):
        raise EditNotApplicable(
            f"this deck has {len(slides)} opportunity slide"
            f"{'s' if len(slides) != 1 else ''} and the switch names number "
            f"{opportunity + 1}"
        )
    slide = slides[opportunity]
    inner = html[slide["inner_start"]:slide["inner_end"]]

    panel_at = None
    for match in _PANEL_OPEN_RE.finditer(inner):
        if panel_class in match.group(0):
            panel_at = match
            break
    if panel_at is None:
        raise EditNotApplicable(
            f"the {panel_class.replace('panel--', '').upper()} panel is not on "
            f"slide {slide['index']} of this deck, so its bullets cannot be "
            f"edited here. The deck was written by a model and this one came "
            f"back in a different shape; re-run it, or edit the bullet as text."
        )
    panel_end = _element_end(inner, panel_at.start(), "div")

    ul = _BULLETS_UL_RE.search(inner, panel_at.end(), panel_end)
    if ul is None:
        raise EditNotApplicable(
            f"the {panel_class.replace('panel--', '').upper()} panel on slide "
            f"{slide['index']} carries no bullet list, so there is nothing to "
            f"switch inside it"
        )
    ul_end = _element_end(inner, ul.start(), "ul")
    return {
        "slide": slide["index"],
        "inner_start": slide["inner_start"] + ul.end(),
        "inner_end": slide["inner_start"] + ul_end - len("</ul>"),
    }


def list_slide_bullets(html, *, opportunity, role):
    """The bullets currently ON the slide, in the order the slide shows them.

    Each entry is ``{"text", "start", "end"}`` with offsets into ``html``.
    ``text`` is DISPLAY text: whatever markup a model put inside the ``<li>``
    is stripped and entities are resolved, so it compares equal to the plain
    text the packet carries and the switch posts.
    """
    region = locate_bullet_list(html, opportunity=opportunity, role=role)
    inner = html[region["inner_start"]:region["inner_end"]]
    out = []
    for match in _LI_OPEN_RE.finditer(inner):
        end = _element_end(inner, match.start(), "li")
        body = inner[match.end():end - len("</li>")]
        # THE WHITESPACE IN FRONT OF THE ITEM BELONGS TO THE ITEM. Removing a
        # `<li>` and leaving its newline and indentation behind fills the list
        # with blank gaps, one per switch, and the deck is a file a reviewer
        # downloads and reads. `lead` is carried so a removal takes its line
        # with it and an insertion can match its siblings.
        lead_start = match.start()
        while lead_start > 0 and inner[lead_start - 1] in " \t":
            lead_start -= 1
        if lead_start > 0 and inner[lead_start - 1] == "\n":
            lead_start -= 1
        out.append({
            "text": " ".join(_display_text(body).split()),
            "lead": inner[lead_start:match.start()],
            "lead_start": region["inner_start"] + lead_start,
            "start": region["inner_start"] + match.start(),
            "end": region["inner_start"] + end,
        })
    return out


def _same_bullet(a, b):
    """Whether two bullet texts are the same line, ignoring how they wrapped."""
    return " ".join((a or "").split()) == " ".join((b or "").split())


def set_bullet_presence(html, *, opportunity, role, text, on, position=None):
    """Put one slide-2 bullet on the slide or take it off; return the new document.

    ``position`` is the bullet's place in the PACKET's list for that panel, used
    only when switching one on: it is where the bullet goes back, so a bullet
    switched off and on again lands where it was rather than at the end.
    Clamped to the list's current length, since the bullets around it may
    themselves be switched off.

    A switch that would change nothing is not an error and not a write: turning
    on a bullet already on the slide returns the document unchanged, and the
    caller decides whether that is worth a revision. It is not.
    """
    bullets = list_slide_bullets(html, opportunity=opportunity, role=role)
    hit = next((b for b in bullets if _same_bullet(b["text"], text)), None)

    if on:
        if hit:
            return html
        region = locate_bullet_list(html, opportunity=opportunity, role=role)
        # Stripped then escaped, which is exactly what `apply_text_edit` does to
        # a replacement: markup in the text is IGNORED rather than refused, and
        # what is left is escaped so it cannot become markup on the way in.
        item = f"<li>{_html.escape(_display_text(text).strip(), quote=False)}</li>"
        index = len(bullets) if position is None else max(0, min(position, len(bullets)))
        if index < len(bullets):
            sibling = bullets[index]
            return (html[:sibling["lead_start"]] + sibling["lead"] + item
                    + html[sibling["lead_start"]:])
        if bullets:
            last = bullets[-1]
            return html[:last["end"]] + last["lead"] + item + html[last["end"]:]
        return html[:region["inner_end"]] + item + html[region["inner_end"]:]

    if not hit:
        # Already off, or never on the slide the model wrote. Both read the same
        # from here and neither is a failure: the switch's job is the end state.
        return html
    return html[:hit["lead_start"]] + html[hit["end"]:]


def set_bullet_presence_and_save(deck_path, *, opportunity, role, text, on,
                                 position=None, author="", now=None,
                                 settle=None):
    """Switch one slide-2 bullet on the deck, write a revision, and log it.

    Same chain as every other edit -- the deck's CURRENT revision in, the next
    revision out, one entry appended to the shared log -- so undo, the edit
    history and a saved deck's chain all work on a bullet switch with no other
    code changing. That is also why nothing here records an inverse: undo is
    file-based, so the revision chain IS the inverse.

    ``settle`` is an optional callable taking the candidate HTML and returning
    ``(final_html, refusal)``. IT IS CALLED BEFORE ANYTHING IS WRITTEN, and a
    refusal raises :class:`BulletDoesNotFit` with the candidate discarded, so
    a bullet that does not fit leaves the deck byte for byte as it was.

    ONE HOOK AND NOT TWO, because settling a panel is a LOOP and not two
    steps. A bullet may fit at the deck's current type size, or only at a
    smaller one, and the only way to know is to measure, change the size and
    measure again. An earlier split into "recompute the size" and "check the
    result" could not express that: it asked the fitter's estimate first, and
    the estimate is more pessimistic than a browser, so decks were being
    shrunk that did not need it.

    All of that lives in the caller. A panel overflowing is geometry, it needs
    a browser to see, and the size that fixes it depends on the panel's
    measured room, which is in the run record. This module knows about
    strings.

    Returns ``{"changed", "revision_path", "revision_name", "entry"}``.
    ``changed`` is False, with no revision written, when the slide already
    reads the way the switch asks -- turning on a bullet that is already there
    is a reviewer confirming the state, not an edit to record.
    """
    deck_path = current_revision_path(deck_path)
    with open(deck_path, encoding="utf-8") as f:
        html = f.read()

    new_html = set_bullet_presence(html, opportunity=opportunity, role=role,
                                   text=text, on=on, position=position)
    if new_html == html:
        return {"changed": False, "revision_path": deck_path,
                "revision_name": os.path.basename(deck_path), "entry": None}

    if settle is not None:
        new_html, refusal = settle(new_html)
        if refusal:
            raise BulletDoesNotFit(refusal)

    revision_path = next_revision_path(deck_path)
    with open(revision_path, "w", encoding="utf-8") as f:
        f.write(new_html)

    line = " ".join(_display_text(text).split())
    short = line if len(line) <= 60 else line[:57] + "..."
    entry = {
        "revision": os.path.basename(revision_path),
        "from": os.path.basename(deck_path),
        "slide": locate_bullet_list(new_html, opportunity=opportunity,
                                    role=role)["slide"],
        "kind": "content",
        "before": f"bullet {'off' if on else 'on'}: {short}",
        "after": f"bullet {'on' if on else 'off'}: {short}",
        "author": (author or "").strip(),
        "created": now or _now_iso(),
        # Read by `save_rerender_and_replay`: a structural bullet edit must not
        # be replayed onto a later render, because the switch behind it is
        # recorded separately and the next render already builds the right
        # bullets from it. Replaying would either no-op or double it.
        "replay": False,
    }
    _append_edit_log(deck_path, entry)
    return {
        "changed": True,
        "revision_path": revision_path,
        "revision_name": os.path.basename(revision_path),
        "entry": entry,
    }


# ------------------------ commercial terms regions --------------------------

# The two regions on a proposal's Commercial Terms slide that carry QofAI's
# standing language, as `deck_renderer` emits them: `.downside` is the blue
# risk-reversal box above the value map, `.mech-steps` is the three-column grid
# under the "HOW PAYMENT WORKS" heading inside `.mechanics`.
#
# WHY THESE ARE NOT TEXT EDITS. `apply_text_edit` changes display text and never
# markup, which is the guarantee that keeps every other edit reversible and keeps
# tags off the slide. Neither region can be written that way:
#
#   `.mech-steps` is `grid-template-columns:repeat(3,1fr)`, and with nothing
#   supplied the render leaves ONE `.st` child holding the marker. Three steps
#   have to become three `.st` children or they stack into the left third and
#   leave two columns empty. Each step also renders its lead phrase bold, which
#   is a `<b>`, which is markup by definition.
#
#   `.downside` could take a text swap when it holds a marker, since the marker
#   is a known string. It goes through here anyway, so one button writes both
#   regions the same way and works whether the box currently holds a marker or a
#   value the reviewer is replacing. Writing half of it through one mechanism and
#   half through another would mean two failure modes for one click.
#
# So this is the `toggle_progress_item` pattern rather than the `apply_text_edit`
# one: find a known element the renderer emits, rewrite its contents, leave the
# document around it alone.
_REGION_OPEN_RE = {
    "commercial_rows": re.compile(r'<div class="terms-strip"[^>]*>'),
    "client_retention": re.compile(r'<div class="caption"[^>]*>'),
    "downside_protection": re.compile(r'<div class="downside"[^>]*>'),
    "value_mapping": re.compile(r'<div class="vm-rows"[^>]*>'),
    "payment_mechanics": re.compile(r'<div class="mech-steps"[^>]*>'),
    "terms_footnote": re.compile(r'<div class="footnote"[^>]*>'),
}

# The two roles the standing-language button writes, and the five the reviewer's
# own terms form writes. Two lists rather than one, because each feature is offered
# only where everything IT needs is on the deck: the button appears on a deck whose
# blue box and payment block are present even if the strip is not, and the form
# needs its own five. `payment_mechanics` is the button's alone -- the form does not
# ask for it, since it is the one region on the slide that is the same every time.
DEFAULT_REGION_ROLES = ("downside_protection", "payment_mechanics")
TERMS_REGION_ROLES = ("commercial_rows", "client_retention",
                      "downside_protection", "value_mapping", "terms_footnote")


def _locate_region(html, role):
    """Where one commercial region sits, as a dict of offsets.

    ``{"slide", "inner_start", "inner_end", "outer_start", "outer_end"}``. Inner is
    the element's contents; outer includes its own tags, which is what
    ``commercial_rows`` needs -- the strip carries its column count in an INLINE
    STYLE on the open tag (``grid-template-columns:repeat(N,1fr)``), so writing
    three boxes into a strip laid out for one means replacing the element and not
    just its contents.

    Raises ``EditNotApplicable`` when the region is not on the deck. The render is
    model-produced, so its absence is a real possibility rather than an impossible
    state, and a refusal naming the region beats a silent no-op that reports
    success.
    """
    pattern = _REGION_OPEN_RE[role]
    for slide in find_slides(html):
        inner = html[slide["inner_start"]:slide["inner_end"]]
        found = pattern.search(inner)
        if not found:
            continue
        outer_end = slide["inner_start"] + _element_end(inner, found.start(), "div")
        return {
            "slide": slide["index"],
            "inner_start": slide["inner_start"] + found.end(),
            "inner_end": outer_end - len("</div>"),
            "outer_start": slide["inner_start"] + found.start(),
            "outer_end": outer_end,
        }
    raise EditNotApplicable(
        f"this deck has no {role} region on any slide, so there is nowhere to "
        f"write it; nothing changed"
    )


def _render_steps(steps):
    """The ``.st`` children of a `.mech-steps` grid, one per step.

    Both halves are escaped, so nothing the defaults file says can put a tag on
    the slide. The ``<b>`` around the lead is this function's own and is the
    emphasis the slide's design calls for.
    """
    return "".join(
        f'<div class="st"><b>{_html.escape(step["lead"], quote=False)}</b> '
        f'{_html.escape(step["rest"], quote=False)}</div>'
        for step in steps
    )


# ------------------- the reviewer's own commercial terms --------------------
#
# Five regions, written from one form. Every figure on this slide is QofAI's
# per-deal arithmetic, so all of it is reviewer input and none of it is sourced,
# which is why this writes the regions wholesale rather than filling markers one at
# a time. `commercial_terms` does the validation and the geometry; everything here
# is escaping and document surgery.
#
# `_ESC` is applied to every reviewer string on the way in, and the guard on the
# assembled fragment is what turns that from a habit into a guarantee.

def _esc(text):
    return _html.escape(str(text or ""), quote=False)


# A leading dollar figure in a strip row's value, which the slide sets in a `.big`
# above the rest. The renderer's own rule ("put a leading dollar figure in a `.big`
# above the rest of the value when the value starts with one"), applied here for the
# same reason the bar widths are: the render never sees these values.
# A figure at the head of a strip row's value, which the slide sets large in a
# `.big` above the rest of the line. The digit run must END in a digit: "$0, no
# capital outlay" is a `.big` of "$0" and a sentence, not a `.big` of "$0," -- a
# trailing comma is punctuation between the figure and the line after it, and the
# stray one read as a typo on the slide.
#
# The `$` is required HERE, where prose follows the figure, because a bare number
# at the head of a sentence is usually not the figure: "3 years of support" would
# otherwise set a giant "3" over the word "years".
_LEADING_MONEY_RE = re.compile(
    r"^\s*~?\s*\$\s*\d(?:[\d,]*\d)?(?:\.\d+)?\s*(?:bn|[kmb])?\b",
    re.IGNORECASE)

# A value that is ENTIRELY a figure, where the `$` is optional. With no prose to be
# confused with, a bare number can only be the figure, so it gets the large
# treatment: a reviewer typing "0" for CLIENT UP-FRONT means the number zero and
# means it to look like the number zero (Antonio, 2026-08-20: "the font is not good
# for the 0 for client up front"). It stays VERBATIM -- "0" is set large as "0" and
# not as "$0", since inventing a currency symbol nobody typed is the sort of small
# helpfulness that ends up on a client deck.
_WHOLE_FIGURE_RE = re.compile(
    r"^~?\s*\$?\s*\d(?:[\d,]*\d)?(?:\.\d+)?\s*(?:bn|[kmb])?\s*%?$",
    re.IGNORECASE)


def _split_leading_money(value):
    """``(big, rest)`` for a strip row's value, ``big`` empty when there is no
    leading figure. The separator between them (a dash, a comma, a middot) belongs
    to neither, so it is dropped rather than left leading the second line."""
    stripped = value.strip()
    if _WHOLE_FIGURE_RE.match(stripped):
        return stripped, ""
    found = _LEADING_MONEY_RE.match(value)
    if not found:
        return "", stripped
    big = found.group(0).strip()
    rest = value[found.end():].lstrip().lstrip("-–—,·:;").strip()
    return big, rest


# A comp-schedule tile, styled INLINE rather than by class. The edit layer writes
# into a document whose stylesheet the render already fixed, so a new class name
# would arrive with no rules behind it and the tiles would render as bare text.
# Inline geometry is the established pattern here anyway (the bar segments carry
# `style="width:..%"`, the strip carries its column count). The theme's own
# variables resolve, and `--accent-tint` is documented in the stylesheet as the
# "pale blue fill (comp boxes, phase bands)" -- this is the element it was for.
_TILE_WRAP_STYLE = ("display:grid;gap:6px;margin:2px 0 8px;"
                    "grid-template-columns:repeat({count},1fr)")
_TILE_STYLE = ("background:var(--accent-tint);border-radius:5px;padding:6px 4px;"
               "text-align:center")
# A spent period reads as spent: no fill, muted figure. In the reference deck the
# YEAR 4+ 0% tile is grey where the paying years are blue, which is the whole
# point of showing the schedule as tiles -- the shape of the decline is the message.
_TILE_STYLE_SPENT = ("background:transparent;border:1px solid var(--line);"
                     "border-radius:5px;padding:6px 4px;text-align:center")
_TILE_PERIOD_STYLE = ("font-family:var(--label);font-size:8.5px;letter-spacing:.1em;"
                      "text-transform:uppercase;color:var(--mute);font-weight:700")
_TILE_PERCENT_STYLE = "font-size:17px;font-weight:700;line-height:1.15;color:{color}"


def _render_schedule_tiles(tiles):
    """A row of period/share tiles for a comp schedule."""
    cells = []
    for tile in tiles:
        spent = _ZERO_PERCENT_RE.match(tile["percent"]) is not None
        cells.append(
            f'<div style="{_TILE_STYLE_SPENT if spent else _TILE_STYLE}">'
            f'<div style="{_TILE_PERIOD_STYLE}">{_esc(tile["period"])}</div>'
            f'<div style="'
            + _TILE_PERCENT_STYLE.format(
                color="var(--mute)" if spent else "var(--accent)")
            + f'">{_esc(tile["percent"])}</div>'
            f"</div>")
    return (f'<div style="{_TILE_WRAP_STYLE.format(count=len(tiles))}">'
            + "".join(cells) + "</div>")


_ZERO_PERCENT_RE = re.compile(r"^0(?:\.0+)?%$")


def render_terms_strip(rows):
    """The whole `.terms-strip` element for ``rows``, its column count included.

    The element and not just its contents, because the count lives in an inline
    style on the open tag. With no rows this rebuilds the render's OWN empty state
    -- one dashed box spanning the strip, carrying both the marker and
    "AWAITING COMMERCIAL TERMS INPUT" -- so clearing the form is a way back to the
    designed blank rather than a way to an empty strip.
    """
    if not rows:
        return ('<div class="terms-strip" style="grid-template-columns:repeat(1,1fr)">'
                '<div class="tbox tbox--empty">'
                '<span class="flag">[MISSING: commercial_rows]</span>'
                '<span class="flag">AWAITING COMMERCIAL TERMS INPUT</span>'
                "</div></div>")
    from commercial_terms import split_schedule

    boxes = []
    for row in rows:
        # A comp schedule first: its periods are a row of tiles, not a figure with
        # a sentence after it, so the money split must not get to it.
        tiles, caption = split_schedule(row["value"])
        if tiles:
            body = _render_schedule_tiles(tiles)
            if caption:
                body += f'<div class="bv">{_esc(caption)}</div>'
            boxes.append(
                f'<div class="tbox" data-value="{_html.escape(row["value"])}">'
                f'<div class="bt">{_esc(row["label"])}</div>{body}</div>')
            continue
        big, rest = _split_leading_money(row["value"])
        body = f'<div class="big">{_esc(big)}</div>' if big else ""
        if rest:
            body += f'<div class="bv">{_esc(rest)}</div>'
        # The value verbatim, so reading the form back is EXACT. The display split
        # above drops the separator between the figure and the sentence (a dash
        # belongs to neither line), which is right on the slide and lossy as a
        # round trip -- and a form that quietly returns a reviewer's own words
        # slightly altered is worse than one that does not reload at all. The
        # attribute is inert, renders nothing, and is ignored when absent, which is
        # how a model-rendered deck reads back (best effort, from the split).
        boxes.append(f'<div class="tbox" data-value="{_html.escape(row["value"])}">'
                     f'<div class="bt">{_esc(row["label"])}</div>'
                     f"{body}</div>")
    return (f'<div class="terms-strip" style="grid-template-columns:'
            f'repeat({len(rows)},1fr)">' + "".join(boxes) + "</div>")


def render_value_map_rows(cases, widths, *, base_scenario):
    """The `.vm-rows` children: one `.vm-row` per case, sized by ``widths``.

    The base case carries ``class="vm-row base"``, which is how the stylesheet
    accents it. A case the render never emitted is just another row here, which is
    what lets a reviewer type in a base case the source never modelled.
    """
    out = []
    for case, width in zip(cases, widths):
        classes = "vm-row base" if case["scenario"] == base_scenario else "vm-row"
        out.append(
            f'<div class="{classes}">'
            f'<div class="vm-label">'
            f'<div class="vm-scenario">{_esc(case["scenario"])}</div>'
            f'<div class="vm-gain">{_esc(case["ebitda_gain"])}</div>'
            f"</div>"
            f'<div class="vm-track">'
            f'<div class="vm-comp-tag">{_esc(case["qofai_comp"])}</div>'
            f'<div class="vm-bar">'
            f'<div class="vm-seg comp" style="width:{width["comp"]}%"></div>'
            f'<div class="vm-seg ret" style="width:{width["ret"]}%">'
            f'{_esc(case["client_retained_ebitda"])}</div>'
            f'<div class="vm-seg ev" style="width:{width["ev"]}%">'
            f'{_esc(case["enterprise_value"])}</div>'
            f"</div></div></div>"
        )
    return "".join(out)


_TBOX_RE = re.compile(
    r'<div class="tbox"(?P<attrs>[^>]*)>(?P<body>.*?)</div>\s*(?=<div class="tbox"|$)',
    re.DOTALL)
_DATA_VALUE_RE = re.compile(r'data-value="(?P<value>[^"]*)"')
_CLASS_TEXT_RE = {
    name: re.compile(rf'<div class="{cls}">(.*?)</div>', re.DOTALL)
    for name, cls in (("bt", "bt"), ("big", "big"), ("bv", "bv"))
}
_VM_ROW_RE = re.compile(r'<div class="vm-row(?: base)?">(.*?)(?=<div class="vm-row|$)',
                        re.DOTALL)
_VM_PART_RE = {
    "scenario": re.compile(r'<div class="vm-scenario">(.*?)</div>', re.DOTALL),
    "ebitda_gain": re.compile(r'<div class="vm-gain">(.*?)</div>', re.DOTALL),
    "qofai_comp": re.compile(r'<div class="vm-comp-tag">(.*?)</div>', re.DOTALL),
    "client_retained_ebitda": re.compile(
        r'<div class="vm-seg ret"[^>]*>(.*?)</div>', re.DOTALL),
    "enterprise_value": re.compile(
        r'<div class="vm-seg ev"[^>]*>(.*?)</div>', re.DOTALL),
}


def _region_text(html, role):
    """One region's display text, blank when it holds only its marker."""
    at = _locate_region(html, role)
    text = " ".join(_html.unescape(
        _display_text(html[at["inner_start"]:at["inner_end"]])).split())
    return "" if _MISSING_RE.fullmatch(text) else text


def _plain(fragment):
    """A captured fragment as the text it reads as, blank for a bare marker."""
    text = " ".join(_html.unescape(_display_text(fragment)).split())
    return "" if _MISSING_RE.fullmatch(text) else text


def read_commercial_terms(html):
    """The commercial terms currently on the deck, in submission shape, or ``None``.

    ``None`` when the slide's five regions are not all present, which is how a
    status deck answers. Otherwise the shape
    :func:`set_commercial_terms` takes, with every field as the text a reviewer
    would have typed: a region holding its `[MISSING: ...]` marker reads back
    BLANK, so the reader and `_marker_or` are inverses and an unfilled field stays
    unfilled through a round trip.

    This exists so the form reloads with what is on the slide. Without it, fixing
    one figure means retyping the other eleven, which is a new reason to avoid the
    studio rather than the simpler interface this was asked for.

    Best effort by design. The FIRST render is model-produced, so its markup is
    only as regular as the render spec makes it; a region this cannot parse comes
    back empty rather than wrong, and the reviewer types it once. Everything the
    studio itself writes round-trips exactly, which is the case that matters after
    the first submission.
    """
    try:
        strip = _locate_region(html, "commercial_rows")
    except EditNotApplicable:
        return None
    for role in TERMS_REGION_ROLES:
        try:
            _locate_region(html, role)
        except EditNotApplicable:
            return None

    rows = []
    strip_inner = html[strip["inner_start"]:strip["inner_end"]]
    if "tbox--empty" not in strip_inner:
        for box in _TBOX_RE.finditer(strip_inner):
            body = box.group("body")
            label_found = _CLASS_TEXT_RE["bt"].search(body)
            label = _plain(label_found.group(1)) if label_found else ""
            exact = _DATA_VALUE_RE.search(box.group("attrs"))
            if exact:
                value = _html.unescape(exact.group("value"))
            else:
                big_found = _CLASS_TEXT_RE["big"].search(body)
                bv_found = _CLASS_TEXT_RE["bv"].search(body)
                value = " ".join(part for part in (
                    _plain(big_found.group(1)) if big_found else "",
                    _plain(bv_found.group(1)) if bv_found else "") if part)
            if label or value:
                rows.append({"label": label, "value": value})

    vm = _locate_region(html, "value_mapping")
    cases = []
    for row in _VM_ROW_RE.finditer(html[vm["inner_start"]:vm["inner_end"]]):
        body = row.group(1)
        case = {}
        for name, pattern in _VM_PART_RE.items():
            found = pattern.search(body)
            case[name] = _plain(found.group(1)) if found else ""
        cases.append(case)

    return {
        "cases": cases,
        "rows": rows,
        "client_retention": _region_text(html, "client_retention"),
        "downside_protection": _region_text(html, "downside_protection"),
        "terms_footnote": _region_text(html, "terms_footnote"),
    }


def set_commercial_terms(html, terms):
    """Write a reviewer's commercial terms into all five regions of slide 5.

    ``terms`` is a validated submission: ``cases`` (with their parsed figures),
    ``rows``, and the three write-in strings. Returns
    ``(new_html, [{"slide", "role", "before", "after"}, ...])``, one record per
    region written, for the caller to log.

    ALL FIVE OR NONE. Every region is located before any is written, so a deck
    missing one is refused whole rather than half-filled. The slide is one
    statement about a deal, and a strip of figures above a chart that still reads
    `[MISSING: qofai_comp]` is not a shorter version of it.

    A region whose write-in is left blank is RESTORED TO ITS MARKER rather than
    emptied. Blank means "I have not supplied this", and the marker is how the deck
    says so; writing an empty div would hide a gap the slide is designed to show
    and would quietly turn an unfilled field into a finished-looking slide.
    """
    from commercial_terms import bar_widths

    widths = bar_widths(terms["cases"])
    fragments = {
        "commercial_rows": render_terms_strip(terms["rows"]),
        "client_retention": _marker_or(terms.get("client_retention"),
                                      "client_retention"),
        "downside_protection": _marker_or(terms.get("downside_protection"),
                                          "downside_protection"),
        "value_mapping": render_value_map_rows(
            terms["cases"], widths,
            base_scenario=terms.get("base_scenario", "Base Case")),
        "terms_footnote": _marker_or(terms.get("terms_footnote"),
                                     "terms_footnote"),
    }

    located = []
    for role in TERMS_REGION_ROLES:
        located.append((role, _locate_region(html, role)))

    # `commercial_rows` replaces its whole element (the column count is on the open
    # tag); every other role replaces contents only.
    def bounds(role, at):
        if role == "commercial_rows":
            return at["outer_start"], at["outer_end"]
        return at["inner_start"], at["inner_end"]

    applied = []
    for role, at in sorted(located, key=lambda pair: -bounds(*pair)[0]):
        start, end = bounds(role, at)
        fragment = fragments[role]
        leaked = visible_tags(fragment)
        if leaked:
            raise EditNotApplicable(
                f"the {role} value would print {leaked[0]} as text on the slide; "
                f"refused so markup can never show up in the deck's copy"
            )
        applied.append({
            "slide": at["slide"],
            "role": role,
            "before": " ".join(_html.unescape(
                _display_text(html[start:end])).split()),
            "after": " ".join(_html.unescape(_display_text(fragment)).split()),
        })
        html = html[:start] + fragment + html[end:]

    order = list(TERMS_REGION_ROLES)
    applied.sort(key=lambda record: order.index(record["role"]))
    return html, applied


def _marker_or(text, field):
    """A reviewer's line, or the field's own `[MISSING: ...]` marker when blank.

    Wrapped in the flag span, which is how the renderer marks a gap, so a field
    cleared back to blank looks exactly like a field never filled.
    """
    text = (text or "").strip()
    if not text:
        return f'<span class="flag">[MISSING: {field}]</span>'
    return _esc(text)


def read_commercial_regions(html):
    """What each commercial region currently reads as, or ``None`` if not present.

    Returns ``{role: display_text}`` when BOTH regions are on the deck, and
    ``None`` when either is missing — which is how a status deck answers, since
    only a proposal renders a Commercial Terms slide. A caller uses that to decide
    whether to offer the default at all, so the offer is driven by what the deck
    actually carries rather than by a deck-type name the studio would have to keep
    in step with the templates.

    The text is what a reader of the slide would see, so a region holding a marker
    comes back as ``[MISSING: payment_mechanics]`` and a caller can tell the two
    cases apart: filling an empty slot, or replacing something already there.
    Entities are resolved for the same reason — a region whose wording contains an
    ampersand reads as ``&`` on the slide, and reporting ``&amp;`` would show the
    reviewer the document's encoding rather than the slide's copy.
    """
    regions = {}
    for role in DEFAULT_REGION_ROLES:
        try:
            at = _locate_region(html, role)
        except EditNotApplicable:
            return None
        regions[role] = " ".join(_html.unescape(
            _display_text(html[at["inner_start"]:at["inner_end"]])).split())
    return regions


def set_commercial_defaults(html, *, downside, payment_steps):
    """Write the standing commercial language into both regions of one deck.

    Returns ``(new_html, [{"slide", "role", "before", "after"}, ...])`` — one
    record per region, for the caller to log. ``before`` and ``after`` are DISPLAY
    text, so a region that held a marker logs as `[MISSING: payment_mechanics]`
    and the audit trail reads as what a reader of the slide would have seen.

    BOTH OR NEITHER. Every region is located before any is written, so a deck
    missing one of them is refused whole. The two are one statement: a blue box
    promising the client that QofAI earns nothing without improvement, above a
    payment block still reading `[MISSING: payment_mechanics]`, is a worse slide
    than the two markers it replaced, and it would arrive looking deliberate.

    Whatever the regions currently hold is replaced. That is the point on a deck
    where they hold markers, and it is a real overwrite on a deck where a source
    supplied them, so the caller says so in the UI and the revision chain makes it
    undoable like any other edit.
    """
    steps_markup = _render_steps(payment_steps)
    replacements = {
        "downside_protection": _html.escape(downside, quote=False),
        "payment_mechanics": steps_markup,
    }

    # Located first, all of them, then written back to front so the earlier
    # region's offsets survive the later region's rewrite.
    located = []
    for role in DEFAULT_REGION_ROLES:
        at = _locate_region(html, role)
        located.append((role, at["slide"], at["inner_start"], at["inner_end"]))

    applied = []
    for role, slide_index, start, end in sorted(located, key=lambda r: -r[2]):
        new_inner = replacements[role]
        leaked = visible_tags(new_inner)
        if leaked:
            raise EditNotApplicable(
                f"the {role} default would print {leaked[0]} as text on the "
                f"slide; refused so markup can never show up in the deck's copy"
            )
        applied.append({
            "slide": slide_index,
            "role": role,
            "before": _display_text(html[start:end]).strip(),
            "after": _display_text(new_inner).strip(),
        })
        html = html[:start] + new_inner + html[end:]

    # Back into reading order, which is the order they appear on the slide.
    applied.sort(key=lambda record: ("downside_protection",
                                     "payment_mechanics").index(record["role"]))
    return html, applied


# ------------------------------- revisions ---------------------------------

def base_stem(deck_path):
    """The shared base name for a deck and its revisions (no ``-rK``, no ext).

    ``output-4.html`` and ``output-4-r3.html`` both yield ``output-4``. Used so an
    edit on any revision numbers the next revision from the base and appends to the
    one shared edit log, never forking a per-revision counter or log.
    """
    name = os.path.basename(deck_path)
    match = _DECK_NAME_RE.match(name)
    if not match:
        # Not an `output-N` deck name: fall back to the name without extension.
        return os.path.splitext(name)[0]
    return match.group("base")


def _revision_re(base):
    return re.compile(rf"^{re.escape(base)}-r(\d+)\.html$", re.IGNORECASE)


def _revisions_beside(deck_path):
    """``(directory, base, {K: filename})`` for the revisions sharing this base."""
    directory = os.path.dirname(os.path.abspath(deck_path))
    base = base_stem(deck_path)
    pattern = _revision_re(base)
    found = {}
    if os.path.isdir(directory):
        for name in os.listdir(directory):
            match = pattern.match(name)
            if match:
                found[int(match.group(1))] = name
    return directory, base, found


def next_revision_path(deck_path):
    """The path for the next revision file beside ``deck_path``.

    Scans the deck's directory for existing ``<base>-r<K>.html`` files and returns
    ``<dir>/<base>-r<K+1>.html`` (starting at ``-r1``). Independent of whether
    ``deck_path`` is the original or an existing revision — both share the base.
    """
    directory, base, found = _revisions_beside(deck_path)
    highest = max(found) if found else 0
    return os.path.join(directory, f"{base}-r{highest + 1}.html")


def current_revision_path(deck_path):
    """The deck's CURRENT state: its highest revision, or the original if none.

    Every edit is computed from this, never from whatever path the caller happens
    to be holding, because the revision counter and the edit log key off the base
    while the document an edit is applied to came from one specific file. A caller
    holding an older path (the Decks tab lists an original and its revisions as
    separate rows; a reloaded or back-buttoned page carries the path it was
    rendered with) would otherwise fork the chain: the new revision is numbered
    from the highest file on disk but built from an older document, so it silently
    drops every edit in between while the shared log still shows them. That is the
    2026-07-23 failure — the history has the change, the deck does not.

    Falls back to ``deck_path`` unchanged when neither the head revision nor the
    base file is a readable file, so a caller passing something that is not a deck
    on disk gets the same behaviour it had before.
    """
    directory, base, found = _revisions_beside(deck_path)
    head = os.path.join(directory, found[max(found)]) if found else os.path.join(
        directory, f"{base}.html"
    )
    return head if os.path.isfile(head) else deck_path


def undo_last_edit(deck_path):
    """Undo the most recent edit on a deck: drop its revision and log entries.

    Reverts the last edit action by removing the highest-numbered
    ``<base>-r<K>.html`` beside ``deck_path`` and deleting every edit-log entry
    that pointed at it (a free-text batch logs one entry per swap under the same
    revision — all go). The now-current deck is the prior revision ``-r<K-1>``, or
    the untouched original when the last revision is removed. A deck with no
    revisions is a safe no-op.

    Returns ``{"undone", "current_path", "removed_revision", "reason"}``:
    ``undone`` is False with a ``reason`` when there was nothing to undo;
    otherwise ``current_path`` is the deck to show next and ``removed_revision``
    names the file that was deleted.
    """
    directory, base, revisions = _revisions_beside(deck_path)
    if not revisions:
        return {
            "undone": False,
            "current_path": deck_path,
            "removed_revision": None,
            "reason": "nothing to undo — this is the original, unedited deck",
        }

    top_k = max(revisions)
    top_name = revisions[top_k]
    try:
        os.remove(os.path.join(directory, top_name))
    except OSError:
        pass

    # Drop the removed revision's entries from the shared log; delete the log file
    # when it empties so a fully-undone deck reads as having no edit history.
    log = load_edit_log(deck_path)
    remaining = [e for e in log if e.get("revision") != top_name]
    log_file = edit_log_path(deck_path)
    if remaining:
        with open(log_file, "w", encoding="utf-8") as f:
            json.dump(remaining, f, indent=2, ensure_ascii=False)
            f.write("\n")
    elif os.path.isfile(log_file):
        os.remove(log_file)

    prior = os.path.join(directory, f"{base}-r{top_k - 1}.html") if top_k > 1 else ""
    current = prior if prior and os.path.isfile(prior) else os.path.join(
        directory, f"{base}.html"
    )
    return {
        "undone": True,
        "current_path": current,
        "removed_revision": top_name,
        "reason": "",
    }


def edit_log_path(deck_path):
    """Path to the co-located edit log for a deck (``<base>.edits.json``)."""
    directory = os.path.dirname(os.path.abspath(deck_path))
    return os.path.join(directory, f"{base_stem(deck_path)}.edits.json")


def load_edit_log(deck_path):
    """Return the deck's edit-log entries (``[]`` when none exist yet)."""
    path = edit_log_path(deck_path)
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


def _append_edit_log(deck_path, entry):
    path = edit_log_path(deck_path)
    log = load_edit_log(deck_path)
    log.append(entry)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def apply_edit_and_save(deck_path, slide_index, source, replacement, *,
                        kind="content", author="", occurrence=None, now=None):
    """Apply one edit to ``deck_path``, write a new revision, and log it.

    Reads the current deck (an original or a prior revision), applies the exact
    text swap via :func:`apply_text_edit` (which raises ``EditNotApplicable`` and
    writes nothing if the edit is not deterministic), writes the result to the next
    revision file beside it, and appends one entry to the shared edit log. The
    original model-rendered deck is never overwritten.

    ``deck_path`` names the deck, not the exact file to read: the edit is applied
    to that deck's CURRENT revision (:func:`current_revision_path`), so an older
    path cannot fork the chain and drop the edits made since. The log's ``from``
    records the file the edit was actually computed from.

    ``kind`` is ``"format"`` or ``"content"`` — recorded for the audit trail and,
    upstream, the gate that decides whether an edit may be promoted to a standing
    preference (only format edits may). Returns
    ``{"revision_path", "revision_name", "entry"}``.
    """
    deck_path = current_revision_path(deck_path)
    with open(deck_path, encoding="utf-8") as f:
        html = f.read()

    # Gated BEFORE the swap is recorded, so the log's "after" names the string
    # that is actually on the slide. `apply_text_edit` gates too and the gate is
    # idempotent, so this is one answer computed once and used twice rather than
    # two chances to disagree. See `gated_replacement`.
    landed = gated_replacement(html, slide_index, source, replacement,
                               occurrence=occurrence)
    new_html = apply_text_edit(html, slide_index, source, landed,
                               occurrence=occurrence)

    revision_path = next_revision_path(deck_path)
    with open(revision_path, "w", encoding="utf-8") as f:
        f.write(new_html)

    entry = {
        "revision": os.path.basename(revision_path),
        "from": os.path.basename(deck_path),
        "slide": slide_index,
        "kind": kind,
        "before": source,
        "after": landed,
        "author": (author or "").strip(),
        "created": now or _now_iso(),
    }
    if occurrence is not None:
        # Which of several identical strings this edit took, so the log says what
        # changed on a slide where the before-text alone cannot.
        entry["occurrence"] = occurrence
    _append_edit_log(deck_path, entry)
    return {
        "revision_path": revision_path,
        "revision_name": os.path.basename(revision_path),
        "entry": entry,
    }


def _edit_occurrence(edit):
    """One edit's ``occurrence``, or ``None`` when it names its target by text.

    A blank or unparseable value reads as absent rather than as an error: the
    field arrives from an HTML form, where "not given" is an empty string, and the
    absent reading is the safe one — it asks :func:`apply_text_edit` for a unique
    match instead of aiming at a position nobody specified.
    """
    raw = edit.get("occurrence")
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _expected_occurrences(edit, edits):
    """How many of ``edit``'s source the slide should hold when the batch reaches it.

    The caller's enumeration said ``occurrences``, but that was before this batch
    started filling markers, and the ones it has already consumed are the batch's
    own doing rather than evidence the list is stale. So the expectation is the
    caller's total minus the same-target edits this batch applies before this one,
    which under :func:`_application_order` are exactly the ones with a HIGHER
    occurrence. Where the caller states no total there is nothing to check.
    """
    stated = edit.get("occurrences")
    if stated in (None, ""):
        return None
    try:
        stated = int(stated)
    except (TypeError, ValueError):
        return None
    mine = _edit_occurrence(edit)
    if mine is None:
        return stated
    already = sum(
        1 for other in edits
        if other is not edit
        and other.get("slide") == edit.get("slide")
        and (other.get("source") or "") == (edit.get("source") or "")
        and (_edit_occurrence(other) or 0) > mine
    )
    return stated - already


def _application_order(edits):
    """The positions in ``edits``, ordered so every ``occurrence`` stays valid.

    Highest occurrence first within each ``(slide, source)`` group, groups in the
    order they first appear. See :func:`apply_edits_and_save` for why the order
    matters; the short version is that filling one occurrence renumbers the ones
    after it, so a batch has to walk them backwards.
    """
    def group(edit):
        return (edit.get("slide"), edit.get("source") or "")

    first_seen = {}
    for position, edit in enumerate(edits):
        first_seen.setdefault(group(edit), position)
    return sorted(
        range(len(edits)),
        key=lambda position: (first_seen[group(edits[position])],
                              -(_edit_occurrence(edits[position]) or 0)),
    )


def apply_edits_and_save(deck_path, edits, *, kind="content", author="",
                         instruction="", atomic=False, now=None):
    """Apply a batch of edits to ``deck_path`` as ONE revision, and log each.

    ``edits`` is a list of ``{"slide", "source", "replacement"}`` dicts (the shape
    ``html_edit_interpreter.interpret_edit`` returns), each optionally carrying an
    ``occurrence`` for :func:`apply_text_edit` to aim by position and the
    ``occurrences`` the caller's enumeration counted, which guards against acting
    on a listing the deck has moved out from under. Each is applied
    in turn to the in-memory document; the offsets are recomputed per edit, so
    sequential swaps compose safely. An edit whose source is missing or ambiguous
    is skipped and recorded in ``failed`` (with the reason) rather than aborting
    the whole batch, so one bad target does not lose the good edits.

    APPLICATION ORDER is not the caller's order where several edits carry an
    ``occurrence`` for the same ``(slide, source)``. Filling one occurrence
    consumes it — the marker text is gone from the slide afterwards — so every
    later occurrence of that string shifts down by one, and walking them in
    ascending order would send edit 2 at what was edit 3's marker. Within one such
    group the batch therefore applies the HIGHEST occurrence first, which leaves
    the positions below it untouched and every index valid against the document as
    the batch walks it. Groups stay in the order the caller gave them, and
    ``applied`` and the log come back in the caller's order too, so the reordering
    is invisible above this line. This is what lets one submission fill four
    identical ``[MISSING: week]`` markers in one revision.

    When at least one edit applies, the accumulated document is written to a
    single new revision beside ``deck_path`` and one log entry per applied edit is
    appended (each carrying the originating ``instruction`` for the audit trail).
    When NO edit applies, nothing is written and ``EditNotApplicable`` is raised
    with a combined reason — mirroring the single-edit contract.

    Like :func:`apply_edit_and_save`, the batch is applied to the deck's CURRENT
    revision rather than to whatever path the caller holds, so a stale path cannot
    fork the chain.

    ``atomic=True`` inverts the partial-failure rule: EVERY edit must apply or
    none does, and one that cannot raises ``EditNotApplicable`` naming the slides
    that refused, with nothing written and no revision created. That is what one
    value fanned out across a deck needs. A deck-wide field is one value BY
    DECLARATION (`template_loader`'s `deck_wide`), and it is that declaration that
    let the studio offer it as a single input in the first place; writing it to
    five slide footers and leaving a sixth reading `[MISSING: ...]` would put the
    deck in a state the declaration says cannot exist, and would report success
    while doing it. Failing whole also keeps one reviewer action equal to one
    revision, so undo reverses exactly what the reviewer did. The reviewer's route
    when one slide genuinely refuses is the exact-text edit, which can name a
    wider, unique span on that slide.

    A refusal here no longer includes "that marker text repeats", which used to be
    the common one: an indexed edit resolves a repeat by position instead.

    Best-effort (the default) stays the rule for a free-text batch, where the
    edits come from one instruction but are independent changes and one bad target
    should not lose the good ones.

    Returns ``{"revision_path", "revision_name", "applied", "failed"}`` where
    ``applied`` and ``failed`` are lists of per-edit records.
    """
    deck_path = current_revision_path(deck_path)
    with open(deck_path, encoding="utf-8") as f:
        html = f.read()

    results = [None] * len(edits)  # per-edit ("applied"|"failed", record)
    current = html
    for position in _application_order(edits):
        edit = edits[position]
        try:
            slide = int(edit.get("slide"))
        except (TypeError, ValueError):
            results[position] = ("failed", {**edit, "reason": "no valid slide number"})
            continue
        source = edit.get("source") or ""
        replacement = edit.get("replacement", "") or ""
        occurrence = _edit_occurrence(edit)
        try:
            # Against the document AS THIS BATCH FOUND IT, for the same reason
            # the occurrence indices are: earlier edits in the batch have already
            # moved the text, and the spot decides how the text is gated.
            #
            # INSIDE THE TRY, which is the whole of the 2026-09-17 fix. This call
            # sat outside it from 766b6ed until then, and it can raise: it
            # reaches `_find_unique` through `_match_in`, and an ambiguous source
            # raises there rather than reporting itself. So an ambiguous target
            # left this function by exception and took every edit that had
            # already applied with it, against the partial-failure contract this
            # docstring states. An ABSENT source never showed it, because
            # `_match_in` returns `(source, None)` for absent text and only
            # ambiguity reaches the raise, so the one test covering the contract
            # covered the half that could not fail.
            replacement = gated_replacement(current, slide, source, replacement,
                                            occurrence=occurrence)
            current = apply_text_edit(
                current, slide, source, replacement, occurrence=occurrence,
                # Counted against the document as this batch found it, not as the
                # caller's list saw it: a batch's own edits legitimately change the
                # count, and the ordering above is what keeps those indices valid.
                # See `_expected_occurrences` for what this guard is aimed at.
                expect_occurrences=_expected_occurrences(edit, edits))
        except EditNotApplicable as exc:
            results[position] = ("failed", {**edit, "reason": str(exc)})
            continue
        record = {
            "slide": slide,
            "source": source,
            "replacement": replacement,
            "note": edit.get("note", ""),
        }
        if occurrence is not None:
            record["occurrence"] = occurrence
        results[position] = ("applied", record)

    # Back into the caller's order, so what is reported and logged reads as the
    # batch that was asked for rather than the order it happened to be walked in.
    applied = [record for kind_, record in results if kind_ == "applied"]
    failed = [record for kind_, record in results if kind_ == "failed"]

    if not applied or (atomic and failed):
        reasons = "; ".join(
            f"slide {f.get('slide')}: {f['reason']}" if atomic else f["reason"]
            for f in failed
        ) or "no edits to apply"
        if atomic and applied:
            reasons = (
                f"{reasons} — nothing was written: this value covers "
                f"{len(applied) + len(failed)} places on the deck and has to reach "
                f"all of them or none"
            )
        raise EditNotApplicable(reasons)

    revision_path = next_revision_path(deck_path)
    with open(revision_path, "w", encoding="utf-8") as f:
        f.write(current)

    created = now or _now_iso()
    for edit in applied:
        entry = {
            "revision": os.path.basename(revision_path),
            "from": os.path.basename(deck_path),
            "slide": edit["slide"],
            "kind": kind,
            "before": edit["source"],
            "after": edit["replacement"],
            "instruction": (instruction or "").strip(),
            "author": (author or "").strip(),
            "created": created,
        }
        if "occurrence" in edit:
            entry["occurrence"] = edit["occurrence"]
        _append_edit_log(deck_path, entry)

    return {
        "revision_path": revision_path,
        "revision_name": os.path.basename(revision_path),
        "applied": applied,
        "failed": failed,
    }


def set_commercial_terms_and_save(deck_path, terms, *, author="", now=None):
    """Write a reviewer's commercial terms to ``deck_path`` as ONE revision.

    Same chain as every other edit — the deck's CURRENT revision in, the next
    revision out, one log entry per region, undoable — so one submission of the
    terms form is one step of undo even though it rewrites five regions of the
    slide. ``kind`` is always ``"content"``: this is what the deck says, the packet
    is never touched, and it can no more become a standing preference than any
    other content edit.

    Returns ``{"revision_path", "revision_name", "applied"}``. Writes nothing when
    :func:`set_commercial_terms` refuses.
    """
    deck_path = current_revision_path(deck_path)
    with open(deck_path, encoding="utf-8") as f:
        html = f.read()

    new_html, applied = set_commercial_terms(html, terms)

    revision_path = next_revision_path(deck_path)
    with open(revision_path, "w", encoding="utf-8") as f:
        f.write(new_html)

    created = now or _now_iso()
    for record in applied:
        _append_edit_log(deck_path, {
            "revision": os.path.basename(revision_path),
            "from": os.path.basename(deck_path),
            "slide": record["slide"],
            "kind": "content",
            "before": record["before"],
            "after": record["after"],
            "note": f"commercial terms: {record['role']}",
            "author": (author or "").strip(),
            "created": created,
        })
    return {
        "revision_path": revision_path,
        "revision_name": os.path.basename(revision_path),
        "applied": applied,
    }


def set_commercial_defaults_and_save(deck_path, *, downside, payment_steps,
                                    author="", now=None):
    """Write both commercial regions to ``deck_path`` as ONE revision, and log each.

    Same chain as every other edit — the deck's CURRENT revision in, the next
    revision out, entries appended to the shared edit log — so undo, edit history
    and a saved deck's chain work on this with no other code changing. One
    revision for one click, which is what keeps undo equal to what the reviewer
    did: one press of the button, one press of undo.

    ``kind`` is always ``"content"``. This changes what the deck says, and the
    packet is never touched, so it can no more become a standing preference than
    any other content edit can.

    Returns ``{"revision_path", "revision_name", "applied"}``. Refuses whole (and
    writes nothing) when either region is missing from the deck; see
    :func:`set_commercial_defaults`.
    """
    deck_path = current_revision_path(deck_path)
    with open(deck_path, encoding="utf-8") as f:
        html = f.read()

    new_html, applied = set_commercial_defaults(
        html, downside=downside, payment_steps=payment_steps)

    revision_path = next_revision_path(deck_path)
    with open(revision_path, "w", encoding="utf-8") as f:
        f.write(new_html)

    created = now or _now_iso()
    for record in applied:
        _append_edit_log(deck_path, {
            "revision": os.path.basename(revision_path),
            "from": os.path.basename(deck_path),
            "slide": record["slide"],
            "kind": "content",
            "before": record["before"],
            "after": record["after"],
            "note": f"standing commercial language: {record['role']}",
            "author": (author or "").strip(),
            "created": created,
        })
    return {
        "revision_path": revision_path,
        "revision_name": os.path.basename(revision_path),
        "applied": applied,
    }


def toggle_progress_item_and_save(deck_path, slide_index, label, new_state, *,
                                  author="", now=None):
    """Toggle one progress item's state, write a new revision, and log it.

    Same chain as :func:`apply_edit_and_save` — the deck's CURRENT revision
    in, the next revision out, one entry appended to the shared edit log — so
    undo, edit history, and a saved deck's chain all work on a toggle with no
    other code changing. ``kind`` is always ``"content"``: the packet stays
    immutable (the 2026-07-28 change), so a toggle can never be promoted to a
    standing preference. ``before``/``after`` name the item and both states
    (e.g. ``"Quote templates: pending"`` -> ``"Quote templates: done"``) so
    the reviewer reading the history sees which item moved and which way.
    Returns ``{"revision_path", "revision_name", "entry"}``.
    """
    deck_path = current_revision_path(deck_path)
    with open(deck_path, encoding="utf-8") as f:
        html = f.read()
    inner_start, inner_end = _slide_inner_bounds(html, slide_index)
    old_state = _locate_progress_item(html[inner_start:inner_end], label)["state"]

    new_html = toggle_progress_item(html, slide_index, label, new_state)

    revision_path = next_revision_path(deck_path)
    with open(revision_path, "w", encoding="utf-8") as f:
        f.write(new_html)

    entry = {
        "revision": os.path.basename(revision_path),
        "from": os.path.basename(deck_path),
        "slide": slide_index,
        "kind": "content",
        "before": f"{label}: {old_state}",
        "after": f"{label}: {new_state}",
        "author": (author or "").strip(),
        "created": now or _now_iso(),
    }
    _append_edit_log(deck_path, entry)
    return {
        "revision_path": revision_path,
        "revision_name": os.path.basename(revision_path),
        "entry": entry,
    }


# The kind recorded for a revision that is a new RENDER rather than an edit.
# Kept out of the format/content pair on purpose: those two describe what a
# reviewer changed about a deck, and this describes the deck being written
# again from the pipeline. The preference gate reads `kind` and must never see
# a re-render as a format edit it could promote to a standing preference.
RERENDER_KIND = "rerender"


def save_rerender_and_replay(deck_path, new_html, *, author="", note="",
                             now=None):
    """Land a fresh render as the next revision of an existing deck.

    WHY THIS EXISTS. A reviewer's bullet switches decide what the next render is
    asked for, so acting on one means rendering again. Rendering again used to
    mean a new ``output-N.html``, which is a new deck: the one on screen, with
    its edit chain, stayed behind, and the reviewer's history forked in two.
    Antonio ruled on 2026-09-20 that a re-render stays ONE deck with its past
    edits reachable, and this is that.

    WHAT IT CAN AND CANNOT CARRY. The new HTML comes back from a model, so the
    copy a reviewer edited may not be in it word for word, and an edit is an
    exact text swap that refuses when its source is absent or ambiguous. So the
    replay is partial by nature: every prior edit is attempted, in the order it
    was made, and the ones that no longer match are REPORTED rather than
    dropped. An edit that could not be replayed is not undone — it stays in the
    log, with this entry recording that it did not carry, which is a different
    fact from a reviewer having reversed it.

    Returns ``{"revision_path", "revision_name", "replayed", "unapplied",
    "entry"}``, where ``unapplied`` carries each edit that did not survive with
    the reason it did not.

    NO CALLER AS OF 2026-09-22. Its one caller was ``/rerender``, which existed
    to act on bullet switches, and a switch now edits the deck directly
    (``set_bullet_presence``), so there is nothing left to re-render for. Kept
    rather than deleted because "land a fresh render as the next revision of
    this deck, replaying its edits" is a real capability with its own tests
    (``tests/test_bullet_toggles.py``) and the next thing that renders into an
    existing deck will want it. It is not dead by accident; it is unused on
    purpose, and this paragraph is the record of that.
    """
    deck_path = current_revision_path(deck_path)
    log = load_edit_log(deck_path)

    replayed, unapplied = [], []
    html = new_html
    # Chronological, which is how they were applied the first time: each edit
    # was computed against the document the one before it left behind, so any
    # other order can make an edit fail that would have applied. Not
    # `_application_order`, which orders a BATCH of pending edits computed
    # against one document and takes a different shape.
    for edit in [e for e in log if e.get("kind") != RERENDER_KIND]:
        # A STRUCTURAL BULLET SWITCH IS NOT REPLAYED, and that is not a
        # failure to report (2026-09-22). Its `before`/`after` are a
        # description ("bullet on: ...") and not text on any slide, so a
        # replay would refuse every one of them and fill `unapplied` with
        # noise. It does not need replaying either: the switch behind it is
        # recorded against the PRD and the fresh render already built the
        # right bullets from it. Entries written before this flag existed
        # carry no `replay` key and are replayed as they always were.
        if edit.get("replay") is False:
            continue
        source = edit.get("before") or ""
        replacement = edit.get("after") or ""
        slide = edit.get("slide")
        if not source or slide is None:
            unapplied.append(dict(edit, reason="the log entry names no text to find"))
            continue
        if source == replacement:
            continue
        try:
            html = apply_text_edit(html, slide, source, replacement,
                                   occurrence=_edit_occurrence(edit))
        except (EditNotApplicable, IndexError, ValueError) as exc:
            # The commonest reason by far: the model wrote that line differently
            # this time, so the string the edit was computed against is not on
            # the new slide. Named rather than counted, because the reviewer has
            # to decide whether to make it again.
            unapplied.append(dict(edit, reason=str(exc) or exc.__class__.__name__))
            continue
        replayed.append(edit)

    revision_path = next_revision_path(deck_path)
    with open(revision_path, "w", encoding="utf-8") as f:
        f.write(html)

    entry = {
        "revision": os.path.basename(revision_path),
        "from": os.path.basename(deck_path),
        "slide": None,
        "kind": RERENDER_KIND,
        "before": "",
        "after": "",
        "note": note or "",
        "replayed": len(replayed),
        # The text of every edit that did not survive, so the log answers "what
        # did I lose" without the reviewer holding the old file open.
        "unapplied": [{"slide": e.get("slide"), "before": e.get("before"),
                       "after": e.get("after"), "reason": e.get("reason")}
                      for e in unapplied],
        "author": (author or "").strip(),
        "created": now or _now_iso(),
    }
    _append_edit_log(deck_path, entry)
    return {
        "revision_path": revision_path,
        "revision_name": os.path.basename(revision_path),
        "replayed": replayed,
        "unapplied": unapplied,
        "entry": entry,
    }
