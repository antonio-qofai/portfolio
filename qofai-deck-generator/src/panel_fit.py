"""How much a slide-2 panel actually holds, computed before the deck is built.

The defect this exists for. On 2026-08-19 a live deck rendered slide 2 with seven
bullets in the TODAY panel and nine in the AFTER panel. The panels hold about two
each at the sizes involved. The surplus did not clip and did not leave the slide;
it was simply painted on top of the build band below, and the deck shipped with
both panels' text lying across the band, unreadable. Antonio's words: "way too
much text, and it's really messy and impossible to read."

Nothing capped it. `templates/proposal-template.md` documents both slots as "1 to
2 supporting lines" and no code enforced that, so `data_source_adapter` passed
through every item the packet carried. The lists used to be short because only the
deterministic parsers filled them; the second pass added on 2026-08-19 reads the
paper's PROSE, which is a far more generous source, and the panels overflowed the
first time it ran against a real paper.

Why a number of bullets is the wrong cap. The room a panel has for bullets is not
a constant. It is what is left after the metrics block above it, and that block is
one line tall for `12.4%` and three lines tall for a value the extraction pass
returned as a whole phrase. The room also moves with the headline's line count and
with how many phases the build band carries. A fixed "keep 3" either overflows a
crowded slide or wastes half a sparse one. So this module computes the room and
fits against it.

What it does NOT do, and the distinction matters. It never writes, shortens,
rewrites or summarises a bullet. It chooses how many of them fit and at what type
size. A bullet that survives is byte-identical to the sourced string that came in,
so nothing here can put an unsourced word on a deck. Ranking which bullets matter
most is a judgment and lives in `bullet_ranking`; this module only measures.

Dropping is visible, never silent. `fit_bullets` reports what it dropped so the
caller can record it, on the same principle as every other absence in this build:
a reader must be able to tell a panel that showed everything from one that showed
the top three of nine.

The estimator, and how it was calibrated. Text width is estimated as
`characters * CHAR_WIDTH_RATIO * font_px`, checked against Chrome on 2026-08-19
across the sixteen real bullets of a rendered deck plus six sample strings at
three type sizes. Arial's advance width for ordinary English copy measured 0.414
to 0.482 of the font size (mean 0.450), and the ratio is exactly linear in size.
`CHAR_WIDTH_RATIO` sits at 0.47, above the mean and just under the worst case, and
`WRAP_EFFICIENCY` accounts for word wrap leaving a ragged right edge. Against the
sixteen measured bullets the estimator was exact on fifteen and over-counted one
by a line. It never under-counted, which is the direction that matters: over-
counting drops a bullet that would have fitted, under-counting ships the defect
this module exists to prevent.

The geometry constants below MIRROR the pinned stylesheet in `deck_renderer`.
They are not a second source of truth: `tests/test_panel_fit.py` parses
`THEME_CSS` and `PROPOSAL_SLIDES_CSS` and fails if any of them drifts, so a house
style change cannot silently invalidate the fit.

Nothing here is client or deck specific. It takes strings and returns how many of
them fit.
"""

import math

# --- The estimator ---------------------------------------------------------

# Advance width per character as a fraction of font size, for Arial body copy.
# Measured, not assumed; see the module docstring.
CHAR_WIDTH_RATIO = 0.47

# Word wrap does not fill a line to its last pixel. Ragged-right copy averages
# well above this, so it is a floor rather than an average.
WRAP_EFFICIENCY = 0.95

# Chrome's `line-height: normal` for Arial, as a multiple of font size. Used for
# the label-style elements that do not set their own line height.
NORMAL_LINE_HEIGHT = 1.15


def estimate_lines(text, *, font_px, width_px):
    """Lines ``text`` wraps to at ``font_px`` in a ``width_px`` column.

    Biased to over-count rather than under-count. Empty text is one line, not
    zero, because an empty element still occupies its line box.
    """
    if width_px <= 0 or font_px <= 0:
        return 1
    usable = width_px * WRAP_EFFICIENCY
    text_px = len(text or "") * CHAR_WIDTH_RATIO * font_px
    return max(1, math.ceil(text_px / usable))


# --- Slide 2's geometry, mirroring the pinned stylesheet -------------------

SLIDE_W = 1280
SLIDE_H = 720
SLIDE_PAD_TOP = 40
SLIDE_PAD_X = 52
SLIDE_PAD_BOTTOM = 34

KICKER_FONT = 11.0
KICKER_MARGIN_TOP = 4
KICKER_MARGIN_BOTTOM = 11
HEADLINE_FONT = 33.0
HEADLINE_LINE_HEIGHT = 1.05
HEADLINE_MARGIN_BOTTOM = 10
HEADLINE_MAX_WIDTH = 1120
SUMMARY_FONT = 14.0
SUMMARY_LINE_HEIGHT = 1.5
SUMMARY_MAX_WIDTH = 1120
BODY_MARGIN_TOP = 14

# The footer sets no height. Measured at 27px on a rendered deck whose footer
# carried a `.flag` chip, which is the tallest form it takes; a plain footer is
# about 23px. Taking the tall one leaves the body slightly short, which errs
# toward fitting fewer bullets.
FOOTER_H = 27

OPP_GRID_GAP = 18
COLS2_GAP = 20

PANEL_BORDER_TOP = 4
PANEL_BORDER_BOTTOM = 1
# `.panel` carries a 1px border on all four sides, with the top one overridden to
# 4px by `.panel--today` / `.panel--after`. The side pair costs 2px of text
# column, which is small but errs the wrong way: a column modelled wider than it
# is under-counts lines, and under-counting is what ships an overflow.
PANEL_BORDER_X = 1
PANEL_PAD_Y = 20
PANEL_PAD_X = 22
PANEL_HEAD_FONT = 11.0
PANEL_HEAD_MARGIN_BOTTOM = 16

METRICS_GAP = 24
METRIC_MARGIN_BOTTOM = 6
METRIC_VAL_FONT = 24.0
METRIC_VAL_LINE_HEIGHT = 1.1
METRIC_LBL_FONT = 11.5
METRIC_LBL_LINE_HEIGHT = 1.35
METRIC_LBL_MARGIN_TOP = 4

# A reviewer marker renders inside a `.flag` chip, which sets its own 9px type
# regardless of the slot it sits in. Measuring one at its slot's size wraps a
# marker that in fact takes a single line: on a measured deck the AFTER metrics
# row came out 58.8px against Chrome's 33px until this was accounted for. The
# chip does not change the LINE height, only how much width the words need.
FLAG_FONT = 9.0
FLAG_PAD_X = 6
FLAG_BORDER = 1
MISSING_MARKER_PREFIX = "[MISSING:"


BULLETS_PAD_TOP = 12
BULLET_FONT = 12.5
BULLET_LINE_HEIGHT = 1.4
BULLET_MARGIN_BOTTOM = 7
BULLET_PAD_LEFT = 15

BAND_PAD_Y = 17
BAND_PAD_X = 22
BAND_TITLE_FONT = 11.0
BAND_TITLE_MARGIN_BOTTOM = 12
BAND_COLUMNS = 2
BAND_GAP = 22
BAND_PN_FONT = 10.5
BAND_PN_MARGIN_BOTTOM = 5
BAND_PD_FONT = 12.5
BAND_PD_LINE_HEIGHT = 1.45

# Type sizes the bullets may take, largest first. The floor is a readability
# call rather than a geometric one: below about 10.5px the copy stops being
# legible from the back of a room, and a panel that needs smaller type than that
# is carrying too much regardless of whether it technically fits.
BULLET_FONT_STEPS = (12.5, 12.0, 11.5, 11.0, 10.5)

# A panel is a summary, not a list. Geometry alone would happily accept eight
# one-line bullets at the smallest step, which fits and still reads as a wall.
MAX_BULLETS_PER_PANEL = 5

# How much room the smallest type has to buy before it is worth using. Measured
# on the deck of 2026-08-19: at every panel size on that slide, dropping from
# 12.5px to 10.5px bought exactly one more bullet. Shrinking every line on the
# panel to fit one more is the wrong trade for a deck read across a table, so a
# smaller step has to gain MORE than this to be chosen. Set to 1, meaning the
# largest size that comes within one bullet of the best possible count wins.
FONT_STEP_TOLERANCE = 1


def is_marker(text):
    """True for a reviewer marker, which renders as a chip at its own size."""
    return (text or "").strip().startswith(MISSING_MARKER_PREFIX)


def is_usable(text):
    """Whether a bullet is worth a line at all: non-empty once stripped.

    `fit_bullets` drops the rest before it measures anything, so a caller that
    needs its own indices to line up with `Fit.kept` and `Fit.dropped` has to
    drop exactly the same items — which is why this is a named predicate here
    rather than a comprehension in two places.
    """
    return bool(text and str(text).strip())


def _measured_font(text, font_px):
    """The size the words are actually set at, which a marker overrides."""
    return FLAG_FONT if is_marker(text) else font_px


def content_width():
    """The slide's content column, inside its horizontal padding."""
    return SLIDE_W - 2 * SLIDE_PAD_X


def panel_width():
    """One of the two side-by-side panels, border box."""
    return (content_width() - COLS2_GAP) / 2


def panel_text_width():
    """The text column inside a panel, inside its borders and padding."""
    return panel_width() - 2 * PANEL_BORDER_X - 2 * PANEL_PAD_X


def bullet_text_width():
    """The text column inside a bullet, inside the list's bullet gutter."""
    return panel_text_width() - BULLET_PAD_LEFT


def head_block_height(headline, summary):
    """Everything above `.body`: the kicker, the headline, and the summary."""
    kicker = KICKER_MARGIN_TOP + KICKER_FONT * NORMAL_LINE_HEIGHT + KICKER_MARGIN_BOTTOM
    head_width = min(content_width(), HEADLINE_MAX_WIDTH)
    headline_lines = estimate_lines(headline, font_px=HEADLINE_FONT,
                                    width_px=head_width)
    head = (headline_lines * HEADLINE_FONT * HEADLINE_LINE_HEIGHT
            + HEADLINE_MARGIN_BOTTOM)
    summary_width = min(content_width(), SUMMARY_MAX_WIDTH)
    summary_lines = estimate_lines(summary, font_px=SUMMARY_FONT,
                                   width_px=summary_width)
    sub = summary_lines * SUMMARY_FONT * SUMMARY_LINE_HEIGHT
    return kicker + head + sub


def body_height(headline, summary):
    """The region between the head block and the footer."""
    used = SLIDE_PAD_TOP + head_block_height(headline, summary)
    used += BODY_MARGIN_TOP + FOOTER_H + SLIDE_PAD_BOTTOM
    return max(0.0, SLIDE_H - used)


def build_band_height(phases):
    """The dark band under the two panels, one cell per phase in two columns.

    ``phases`` is a sequence of ``(label, description)`` pairs. An empty sequence
    means the band is omitted entirely, which the renderer already does.
    """
    phases = list(phases or ())
    if not phases:
        return 0.0
    cell_width = (content_width() - 2 * BAND_PAD_X
                  - (BAND_COLUMNS - 1) * BAND_GAP) / BAND_COLUMNS
    rows = math.ceil(len(phases) / BAND_COLUMNS)
    row_heights = []
    for index in range(rows):
        cells = phases[index * BAND_COLUMNS:(index + 1) * BAND_COLUMNS]
        tallest = 0.0
        for label, description in cells:
            label_lines = estimate_lines(label, font_px=BAND_PN_FONT,
                                         width_px=cell_width)
            desc_lines = estimate_lines(description, font_px=BAND_PD_FONT,
                                        width_px=cell_width)
            cell = (label_lines * BAND_PN_FONT * NORMAL_LINE_HEIGHT
                    + BAND_PN_MARGIN_BOTTOM
                    + desc_lines * BAND_PD_FONT * BAND_PD_LINE_HEIGHT)
            tallest = max(tallest, cell)
        row_heights.append(tallest)
    title = BAND_TITLE_FONT * NORMAL_LINE_HEIGHT + BAND_TITLE_MARGIN_BOTTOM
    return (2 * BAND_PAD_Y + title + sum(row_heights)
            + (rows - 1) * BAND_GAP)


def metrics_height(metrics):
    """The metrics row inside a panel, laid out as up to two columns.

    ``metrics`` is a sequence of ``(value, label)`` pairs; a label may be empty.
    The row is the tallest metric in it, since they sit side by side.
    """
    metrics = [m for m in (metrics or ()) if (m[0] or m[1])]
    if not metrics:
        return 0.0
    columns = min(2, len(metrics))
    cell_width = (panel_text_width() - (columns - 1) * METRICS_GAP) / columns
    tallest = 0.0
    for value, label in metrics:
        value_lines = estimate_lines(
            value, font_px=_measured_font(value, METRIC_VAL_FONT),
            width_px=cell_width)
        cell = value_lines * METRIC_VAL_FONT * METRIC_VAL_LINE_HEIGHT
        if label:
            label_lines = estimate_lines(label, font_px=METRIC_LBL_FONT,
                                         width_px=cell_width)
            cell += (METRIC_LBL_MARGIN_TOP
                     + label_lines * METRIC_LBL_LINE_HEIGHT * METRIC_LBL_FONT)
        tallest = max(tallest, cell)
    rows = math.ceil(len(metrics) / 2)
    return rows * (tallest + METRIC_MARGIN_BOTTOM)


def bullet_room(*, headline, summary, phases, metrics):
    """Vertical pixels a panel has left for its bullet list.

    Everything above the list is measured and subtracted: the slide's head block,
    the footer, the build band, the panel's own chrome, and the metrics row. The
    two panels share a row height, so they differ only by their metrics.
    """
    grid = body_height(headline, summary)
    band = build_band_height(phases)
    row = grid - band - (OPP_GRID_GAP if band else 0)
    inner = row - PANEL_BORDER_TOP - PANEL_BORDER_BOTTOM - 2 * PANEL_PAD_Y
    head = PANEL_HEAD_FONT * NORMAL_LINE_HEIGHT + PANEL_HEAD_MARGIN_BOTTOM
    return max(0.0, inner - head - metrics_height(metrics) - BULLETS_PAD_TOP)


# --- The fit ---------------------------------------------------------------

class Fit:
    """What fits, at what size, and what did not make it.

    ``kept`` and ``dropped`` are the original strings, unmodified. ``font_px`` is
    the largest type size at which ``kept`` fits. ``overflowed`` is True when not
    even one bullet fits the room, in which case ``kept`` still carries one: a
    panel that shows nothing hides the fact that there was something to show, and
    the layout guard reports the clip.
    """

    __slots__ = ("kept", "dropped", "font_px", "used_px", "room_px", "overflowed",
                 "blocked", "over_cap")

    def __init__(self, kept, dropped, font_px, used_px, room_px, overflowed,
                 blocked=None, over_cap=False):
        self.kept = kept
        self.dropped = dropped
        self.font_px = font_px
        self.used_px = used_px
        self.room_px = room_px
        self.overflowed = overflowed
        # Bullets a reviewer switched ON that still do not fit at the smallest
        # readable size. Empty on every automatic fit; only an explicit request
        # can be refused, because only an explicit request was made.
        self.blocked = list(blocked or ())
        # True when the reviewer's own choices put more than MAX_BULLETS_PER_PANEL
        # on the panel. A warning and not a refusal (Antonio, 2026-09-20): the cap
        # is an editorial judgment and a reviewer looking at the slide can see
        # what it is becoming in a way the fitter cannot.
        self.over_cap = bool(over_cap)

    def __repr__(self):
        return (f"Fit(kept={len(self.kept)}, dropped={len(self.dropped)}, "
                f"font_px={self.font_px}, used_px={round(self.used_px, 1)}, "
                f"room_px={round(self.room_px, 1)})")

    @property
    def note(self):
        """One line for the reviewer, or empty when everything was shown."""
        if not self.dropped:
            return ""
        total = len(self.kept) + len(self.dropped)
        return (f"{len(self.kept)} of {total} shown; {len(self.dropped)} did not "
                f"fit the panel and were dropped in rank order")


def _fits_at(bullets, font_px, room_px, width_px, max_bullets):
    """How many of ``bullets``, in order, fit ``room_px`` at ``font_px``."""
    line_px = font_px * BULLET_LINE_HEIGHT
    used = 0.0
    count = 0
    for index, text in enumerate(bullets[:max_bullets]):
        lines = estimate_lines(text, font_px=font_px, width_px=width_px)
        # The last item's bottom margin is slack inside the panel, so only the
        # gaps BETWEEN items are charged.
        cost = lines * line_px + (BULLET_MARGIN_BOTTOM if index else 0)
        if used + cost > room_px:
            break
        used += cost
        count += 1
    return count, used


def fit_bullets(bullets, *, room_px, width_px=None,
                max_bullets=MAX_BULLETS_PER_PANEL,
                font_steps=BULLET_FONT_STEPS):
    """Keep as many leading bullets as fit, at the largest type size that holds.

    ``bullets`` must already be in priority order, most important first, because
    this drops from the tail. Ranking is a judgment and belongs upstream.

    Type size steps down only when a smaller size buys enough to be worth it. The
    largest size that comes within ``FONT_STEP_TOLERANCE`` bullets of the best
    achievable count wins, so a panel does not shrink every line it has in order
    to gain one more.
    """
    bullets = [b for b in (bullets or ()) if is_usable(b)]
    width_px = bullet_text_width() if width_px is None else width_px
    if not bullets:
        return Fit([], [], font_steps[0], 0.0, room_px, False)

    counts = {}
    for font_px in font_steps:
        counts[font_px] = _fits_at(bullets, font_px, room_px, width_px, max_bullets)
    reachable = max(count for count, _ in counts.values())

    best_count, best_font, best_used = 0, font_steps[0], 0.0
    for font_px in font_steps:                       # largest first
        count, used = counts[font_px]
        if count + FONT_STEP_TOLERANCE >= reachable:
            best_count, best_font, best_used = count, font_px, used
            break

    if best_count == 0:
        # Nothing fits. Show the top one at the smallest size and let the panel's
        # backstop clip it, which the layout guard reports. Showing an empty
        # panel would hide that there was anything to show.
        smallest = font_steps[-1]
        _, used = _fits_at(bullets[:1], smallest, float("inf"), width_px, 1)
        return Fit(bullets[:1], list(bullets[1:]), smallest, used, room_px, True)

    return Fit(bullets[:best_count], list(bullets[best_count:]),
               best_font, best_used, room_px, False)


def size_holding_all(bullets, *, room_px, width_px=None,
                     font_steps=BULLET_FONT_STEPS):
    """The largest type size at which EVERY bullet fits, or ``None``.

    A DIFFERENT QUESTION FROM :func:`fit_bullets`, and the difference is the
    whole reason this exists (2026-09-22). `fit_bullets` chooses a size by how
    many bullets it can KEEP: it steps down only when a smaller size buys more
    than `FONT_STEP_TOLERANCE` extra lines, and it is free to drop the tail.
    That is right when the fitter is deciding what goes on a slide.

    A reviewer switching a bullet on has already decided. Nothing may be
    dropped, so the only question left is what size holds the set, and the
    answer can be "none of them" -- which is a refusal to report, not a
    shorter list to render.

    Returns a size from ``font_steps``, or ``None`` when even the smallest
    does not hold them all. A caller that gets ``None`` should refuse rather
    than render at the floor and hope.
    """
    bullets = [b for b in (bullets or ()) if is_usable(b)]
    if not bullets:
        return font_steps[0]
    width_px = bullet_text_width() if width_px is None else width_px
    for font_px in font_steps:                       # largest first
        held, _ = _fits_all(bullets, font_px, room_px, width_px)
        if held:
            return font_px
    return None


def _fits_all(bullets, font_px, room_px, width_px):
    """Whether every one of ``bullets`` fits ``room_px`` at ``font_px``."""
    count, used = _fits_at(bullets, font_px, room_px, width_px, len(bullets) or 1)
    return count == len(bullets), used


def fit_bullets_with_overrides(bullets, *, room_px, width_px=None,
                               forced_on=(), forced_off=(),
                               max_bullets=MAX_BULLETS_PER_PANEL,
                               font_steps=BULLET_FONT_STEPS):
    """Fit a panel a reviewer has taken decisions about.

    ``forced_on`` and ``forced_off`` are bullet strings the reviewer switched on
    and off. Everything else is fitted the way it always was, so a panel nobody
    has touched comes out of here identical to ``fit_bullets``.

    THREE RULES, AND EACH ONE IS A DECISION RECORDED IN THE BUILD PLAN.

    A switched-off bullet leaves. It is not "dropped for room", so it is not
    reported as something the panel could not hold; the caller distinguishes the
    two, because "your slide could not fit this" and "you said no to this" are
    different sentences to a reviewer.

    The type ladder is spent for a switched-on bullet and not otherwise.
    ``FONT_STEP_TOLERANCE`` makes the automatic fit refuse to shrink a whole
    panel to gain one line, which is right when the fitter is choosing and wrong
    when a human has asked for that specific line. So here the tolerance does not
    apply: every step is measured, and the size that holds the MOST bullets wins,
    with the largest such size taking a tie. Sizing off the switched-on bullets
    alone looks equivalent and is not — it makes a crowded panel worse for being
    asked, snapping back up to 12.5px to hold the one forced line and losing two
    of the four the automatic fit was holding at 11.5px.

    The limit is the room and not a count. ``MAX_BULLETS_PER_PANEL`` still caps
    what the fitter adds on its own, and a reviewer may pass it; when they do,
    ``over_cap`` says so so the studio can warn rather than refuse. What cannot
    be passed is geometry: a switched-on bullet that does not fit at the smallest
    readable size comes back in ``blocked``, and the studio's job is to have
    refused the switch before it got here.
    """
    width_px = bullet_text_width() if width_px is None else width_px
    usable = [b for b in (bullets or ()) if is_usable(b)]
    off = {" ".join((b or "").split()) for b in forced_off}
    on_order = [b for b in usable if " ".join(b.split()) in
                {" ".join((x or "").split()) for x in forced_on}]
    remaining = [b for b in usable
                 if " ".join(b.split()) not in off and b not in on_order]

    if not on_order:
        # Nothing was switched on, so this is the automatic fit with the
        # switched-off bullets already gone.
        fit = fit_bullets(remaining, room_px=room_px, width_px=width_px,
                          max_bullets=max_bullets, font_steps=font_steps)
        return fit

    # The size is chosen to hold as much as possible while GUARANTEEING every
    # switched-on bullet, which is not the same as choosing it from the
    # switched-on ones alone. Sizing off the forced bullets and stopping at the
    # first step that holds them makes a panel worse for asking: on a crowded
    # panel the automatic fit had already stepped down to hold four, and forcing
    # one bullet on would have snapped the size back up to 12.5 and lost two of
    # the other three. So every step is measured, the best total wins, and the
    # LARGEST size achieving that total is the one used.
    def _plan(font_px):
        ok, used = _fits_all(on_order, font_px, room_px, width_px)
        if not ok:
            return None
        kept = list(on_order)
        line_px = font_px * BULLET_LINE_HEIGHT
        for text in remaining:
            if len(kept) >= max_bullets:
                break
            lines = estimate_lines(text, font_px=font_px, width_px=width_px)
            cost = lines * line_px + (BULLET_MARGIN_BOTTOM if kept else 0)
            if used + cost > room_px:
                break
            used += cost
            kept.append(text)
        return kept, used

    best = None
    for font_px in font_steps:                       # largest first
        plan = _plan(font_px)
        if plan is None:
            continue
        if best is None or len(plan[0]) > len(best[1]):
            best = (font_px, plan[0], plan[1])

    if best is None:
        # Not even the floor holds every switched-on bullet. Keep as many as do,
        # in order, and hand the rest back as blocked rather than dropping them
        # quietly: the studio is supposed to have refused the switch before this,
        # and if it did not, the reviewer has to be told which line did not make
        # it.
        chosen = font_steps[-1]
        count, used = _fits_at(on_order, chosen, room_px, width_px, len(on_order))
        blocked = list(on_order[count:])
        kept = list(on_order[:count])
    else:
        chosen, kept, used = best
        blocked = []

    dropped = [b for b in remaining if b not in kept]
    return Fit(kept, dropped, chosen, used, room_px, overflowed=not kept,
               blocked=blocked, over_cap=len(kept) > max_bullets)
