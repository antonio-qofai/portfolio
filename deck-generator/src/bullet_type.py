"""Delivering a panel's bullet type size to the rendered deck.

Why this exists. ``panel_fit`` has always been able to step slide 2's bullet
type down through ``BULLET_FONT_STEPS`` to hold more of a list, and the build
has never used it: ``data_source_adapter._panel_fit`` fits at the house size
only, because "delivering that to the renderer needs a per-panel role the
template does not declare". This module is that delivery, and it declares no
role.

HOW, AND WHY NOT THROUGH THE MODEL. The deck's HTML comes back from a model, so
anything asked for in the prompt is asked for rather than guaranteed, and a type
size that sometimes arrives is worse than one that never does: the fitter would
be keeping bullets on the promise of a size the slide might not be set at, and
the overflow it exists to prevent would come back intermittently. The rendered
deck already carries the two things needed to address a panel exactly —
``data-slide="N"`` on each slide and ``.panel--today`` / ``.panel--after`` on
each panel — so the size is applied deterministically afterwards, as one
stylesheet block appended to the finished document.

WHERE IT RUNS. After the flag strip and before the display-text and layout
guards, so what the guards measure is the deck as it will be opened. A rule
here changes type size and nothing else: no text is added, removed or rewritten,
so the render-fidelity guard's account of which values survived is untouched.

Nothing here is client or deck specific. It takes fits and HTML and returns HTML.
"""

import re

import panel_fit

# Slide 2 repeats once per opportunity and the deck's first slide is the cover,
# so opportunity i sits on slide 2 + i. The renderer numbers slides with
# `data-slide`, which is what makes this addressable at all.
FIRST_OPPORTUNITY_SLIDE = 2

_PANEL_CLASS = {"today": "panel--today", "after": "panel--after"}


def panel_type_rules(fits):
    """The panels that need a type size stated, as ``(slide, panel, font_px)``.

    ``fits`` is ``_panel_fit_per_opportunity``: one report per opportunity, each
    keyed by bullet role. Only a panel set BELOW the house size produces a rule.
    A panel at the house size is what the pinned stylesheet already says, and
    restating it would put a rule in every deck for no change, which makes the
    block itself unreadable as a signal that something was resized.
    """
    rules = []
    for index, report in enumerate(fits or ()):
        slide = FIRST_OPPORTUNITY_SLIDE + index
        for entry in (report or {}).values():
            font_px = entry.get("font_px")
            panel = entry.get("panel")
            if not font_px or panel not in _PANEL_CLASS:
                continue
            if font_px >= panel_fit.BULLET_FONT:
                continue
            rules.append((slide, panel, float(font_px)))
    return rules


def type_style_block(rules):
    """The stylesheet block for ``rules``, or "" when nothing was resized."""
    if not rules:
        return ""
    lines = [
        "<style>",
        "/* Bullet type set by panel_fit, applied after the render. A panel",
        "   below the house size is holding more bullets than it holds at",
        "   12.5px; see src/bullet_type.py for why this is not asked of the",
        "   model. */",
    ]
    for slide, panel, font_px in rules:
        size = ("%g" % round(font_px, 2))
        lines.append(
            '[data-slide="%d"] .%s .bullets li{font-size:%spx}'
            % (slide, _PANEL_CLASS[panel], size)
        )
    lines.append("</style>")
    return "\n".join(lines) + "\n"


# Finding the block again, to CHANGE it rather than add a second one.
#
# Why this is needed at all. `apply_panel_type` appends, which is right once,
# at the end of a render. A bullet switched on or off in the studio changes
# what a panel has to hold, so its type has to be recomputed on a document
# that may already carry a block from the render (2026-09-22). Appending again
# would stack a second `<style>` per toggle, and the last one would win by
# order, so the deck would go on working while accumulating dead rules.
#
# Matched on the comment rather than on a marker attribute, because every deck
# already rendered carries the comment and none of them carry an attribute.
_BLOCK_MARK = "Bullet type set by panel_fit"
_BLOCK_RE = re.compile(r"<style>(?:(?!</style>).)*?" + _BLOCK_MARK +
                       r"(?:(?!</style>).)*?</style>\n?", re.S)
_RULE_RE = re.compile(
    r'\[data-slide="(\d+)"\]\s*\.(panel--\w+)\s*\.bullets li\{font-size:([\d.]+)px\}')


def read_panel_type(html):
    """The panel type rules a document already carries, as ``{(slide, panel): px}``.

    ``panel`` is the short name (``today`` / ``after``), matching what
    :func:`panel_type_rules` emits, so a rule read back out and written again
    round-trips.
    """
    block = _BLOCK_RE.search(html or "")
    if not block:
        return {}
    by_class = {v: k for k, v in _PANEL_CLASS.items()}
    out = {}
    for slide, panel_class, size in _RULE_RE.findall(block.group(0)):
        panel = by_class.get(panel_class)
        if panel:
            out[(int(slide), panel)] = float(size)
    return out


def set_panel_type(html, slide, panel, font_px):
    """Set (or clear) ONE panel's bullet type size on an already-rendered deck.

    ``font_px`` of None, or of the house size, clears the rule: the pinned
    stylesheet already says 12.5px, and a rule restating it makes the block
    unreadable as a signal that something was resized -- the same reason
    :func:`panel_type_rules` skips it.

    Rewrites the existing block in place, or writes one when there is none, or
    removes it when the last rule goes. Idempotent, which :func:`apply_panel_type`
    deliberately is not: that one runs once per render, this one runs once per
    switch.

    ``slide`` is the slide's real 1-based number, which the caller takes from
    the deck itself rather than from ``FIRST_OPPORTUNITY_SLIDE + index``. The
    arithmetic is right for every deck this pipeline has produced, and reading
    it off the document cannot be wrong for one it has not.
    """
    if panel not in _PANEL_CLASS:
        raise ValueError(f"{panel!r} is not a slide 2 panel")
    rules = read_panel_type(html)
    if not font_px or float(font_px) >= panel_fit.BULLET_FONT:
        rules.pop((int(slide), panel), None)
    else:
        rules[(int(slide), panel)] = float(font_px)

    block = type_style_block([(s, p, px) for (s, p), px in sorted(rules.items())])
    existing = _BLOCK_RE.search(html or "")
    if existing:
        return html[:existing.start()] + block + html[existing.end():]
    if not block:
        return html
    closing = "</body>"
    index = (html or "").rfind(closing)
    if index == -1:
        return (html or "") + block
    return html[:index] + block + html[index:]


def apply_panel_type(html, fits):
    """Return ``html`` with the panel type sizes applied.

    Appended before ``</body>`` so it wins over the pinned stylesheet by order,
    without either rule needing an ``!important`` that a later change would have
    to fight. A document with no ``</body>`` (which nothing this pipeline writes
    produces, but a test fixture might) gets the block at the end, where it still
    applies.
    """
    block = type_style_block(panel_type_rules(fits))
    if not block:
        return html
    closing = "</body>"
    index = (html or "").rfind(closing)
    if index == -1:
        return (html or "") + block
    return html[:index] + block + html[index:]
