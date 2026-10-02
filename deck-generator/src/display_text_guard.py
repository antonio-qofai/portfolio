"""Display-text guard — no HTML tag ever reaches the words on a slide.

The failure this exists to catch was demonstrated live at the 2026-07-23 sync:
a reviewer edit put raw markup into a slide's visible text, so the deck read
``<span class="base">Project Planning.</span>`` on screen instead of reading
``Project Planning.``. A tag printed as text is a client-facing defect that no
other guard can see — the value is present, the packet is coherent, the layout
measures clean, and the slide still looks broken.

Fifth guard, fifth boundary. The other four ask whether the deck says the right
things: ``packet_consistency`` (the packet against itself), ``coverage_guard``
(packet to prompt), ``render_guard`` (prompt to HTML), ``layout_guard`` (HTML to
pixels). This one asks whether the deck's own markup is showing: HTML to WORDS.

What counts as display text here: the document with its ``<style>`` and
``<script>`` blocks dropped (a stylesheet is never read on screen), its real tags
removed, and its entities resolved — which is what a reader sees. A tag has
"reached display text" when that resolved text still contains something shaped
like a tag, which is exactly what an escaped ``&lt;span&gt;`` becomes on screen.

Deliberately narrow about what reads as a tag: an angle bracket followed by a
letter, then tag-name characters, then attributes with no bracket of their own.
So a genuine ``<15%`` or ``a < b`` in copy is never flagged, and a printed
``<span class="base">`` always is. Like the reviewer-marker half of the render
guard, a hit is unambiguous, so it always raises rather than returning a report
for a human to weigh.
"""

import html as _html
import re

# Dropped whole, opening tag to closing tag: a stylesheet or a script is markup a
# reader never sees, and CSS carries brackets of its own (`.head > .eyebrow`).
_STYLE_SCRIPT_RE = re.compile(
    r"<(style|script)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL
)
_TAG_RE = re.compile(r"<[^>]+>")

# What a tag looks like once it is being READ rather than parsed. The leading
# letter is what keeps `<15%`, `a < b`, and `<= 3` out of it.
_VISIBLE_TAG_RE = re.compile(r"</?[a-zA-Z][a-zA-Z0-9-]*(?:\s[^<>]*)?/?>")


class DisplayTextError(AssertionError):
    """An HTML tag reached the display text of a deck.

    Subclasses ``AssertionError`` for the same reason ``RenderFidelityError``
    does: it fails as loudly as a broken invariant, and the lightweight test
    runners in ``tests/`` report it as a clean failure.
    """


def display_text(document):
    """The words a reader actually sees: no stylesheets, no tags, no entities."""
    text = _STYLE_SCRIPT_RE.sub(" ", document)
    text = _TAG_RE.sub(" ", text)
    return _html.unescape(text)


def visible_tags(document):
    """Every run in ``document`` that would print as an HTML tag on a slide."""
    return _VISIBLE_TAG_RE.findall(display_text(document))


def check_display_text(document, *, where="the rendered deck"):
    """Assert no HTML tag is visible as text in ``document``.

    Raises ``DisplayTextError``, naming every offending run, when one is. Returns
    ``{"ok": True, "visible_tags": []}`` otherwise, so a caller can record that
    the boundary was checked the way the other guards' reports do.
    """
    found = visible_tags(document)
    if found:
        listing = "\n".join(f"  - {tag}" for tag in sorted(set(found)))
        raise DisplayTextError(
            f"HTML tag(s) reached the display text of {where}, so a reader sees "
            f"markup printed on the slide instead of the words it wraps:\n"
            f"{listing}"
        )
    return {"ok": True, "visible_tags": []}
