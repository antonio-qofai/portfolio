"""Tests for the panel fitter — how much slide 2's panels actually hold.

Two kinds of case here, and they guard different things.

The MEASURED cases pin numbers taken from Chrome on 2026-08-19, against the live
deck that shipped the overflow this module exists to prevent and against a type
specimen rendered at three sizes. They need no browser to run: the measurements
are the fixture. If the estimator drifts away from what Chrome does, these fail.

The DRIFT case reads the pinned stylesheet out of `deck_renderer` and checks every
geometry constant against it, so a house style change cannot quietly invalidate a
fit computed from stale numbers.

Run with: python3 tests/test_panel_fit.py
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import panel_fit  # noqa: E402
from deck_renderer import PROPOSAL_SLIDES_CSS, THEME_CSS  # noqa: E402

# Every bullet of the deck rendered 2026-08-19, as `(characters, lines)` measured
# in Chrome at 12.5px in a 517px column. Seven TODAY bullets then nine AFTER.
MEASURED_BULLETS = [
    (98, 2), (82, 1), (116, 2), (98, 2), (145, 2), (140, 2), (135, 2),
    (78, 1), (108, 2), (123, 2), (92, 2), (89, 1), (113, 2), (64, 1),
    (65, 1), (67, 1),
]
MEASURED_BULLET_WIDTH = 517
MEASURED_BULLET_FONT = 12.5

# The same deck's slide 2, box by box, measured in Chrome.
DECK_HEADLINE = ("Deploy Predictive Maintenance AI Across Network Infrastructure "
                 "to Reduce Outages and MTTR by 30-40%")
DECK_SUMMARY = ("Network reliability is Lamna's leading competitive "
                "differentiator, and proactive maintenance addresses the "
                "increasing burden from aging network elements while reducing "
                "repair costs and SLA-related revenue impacts.")
DECK_PHASES = [
    ("Phase 1: Pilot (Months 0-6)",
     "500-1,000 RAN sites in 2-3 markets; fiber monitoring expansion in 1 metro. "
     "Expected outcomes: Validate prediction accuracy (target >=80%); establish "
     "baseline MTTR."),
    ("Phase 2: Expansion (Months 6-12)",
     "Scale to 10,000+ RAN sites; add data center PdM; begin tower drone "
     "inspection. Expected outcomes: 15-20% MTTR reduction; measurable truck "
     "roll reduction."),
    ("Phase 3: Full Rollout (Months 12-24)",
     "National deployment across all network layers; NOC integration complete. "
     "Expected outcomes: 30-40% MTTR reduction; full financial run rate."),
    ("Phase 4: Optimization (Months 24-36)",
     "Model refinement; autonomous remediation; cross-domain correlation. "
     "Expected outcomes: Incremental gains beyond initial targets."),
]
DECK_TODAY_METRICS = [
    ("approximately 120 million wireless retail connections",
     "wireless retail connections"),
    ("14 million broadband connections", "broadband connections"),
]
DECK_AFTER_METRICS = [("0.6-1.3pp", ""), ("[MISSING: after_metric_2]", "")]

MEASURED_GEOMETRY = {           # name -> (model callable, Chrome's number)
    "build band": 187,
    "today metrics": 105,
    "after metrics": 33,
    "today bullet room": 63,
    "after bullet room": 135,
}

# Chrome and the model may not agree to the pixel; they must agree closely, and
# the model must never claim MORE room than there is.
GEOMETRY_TOLERANCE_PX = 4

SAMPLE = "Reducing unplanned outages directly reduces SLA penalty exposure."


def _css_value(selector, prop):
    """One declared value from the pinned stylesheet, for the drift check."""
    css = THEME_CSS + PROPOSAL_SLIDES_CSS
    pattern = re.compile(
        r"(?:^|[},;/*\s])" + re.escape(selector) + r"\s*\{([^}]*)\}", re.S)
    match = pattern.search(css)
    assert match, f"selector {selector} not found in the pinned stylesheet"
    body = match.group(1)
    found = re.search(re.escape(prop) + r"\s*:\s*([^;}]+)", body)
    assert found, f"{selector} declares no {prop}"
    return found.group(1).strip()


def _px(selector, prop):
    return float(re.search(r"(-?[\d.]+)px", _css_value(selector, prop)).group(1))


# --- the estimator ---------------------------------------------------------

def test_estimator_never_under_counts_a_measured_bullet():
    """Under-counting is what ships an overflow, so it is the failure that matters."""
    under = []
    for chars, actual in MEASURED_BULLETS:
        estimated = panel_fit.estimate_lines(
            "x" * chars, font_px=MEASURED_BULLET_FONT,
            width_px=MEASURED_BULLET_WIDTH)
        if estimated < actual:
            under.append((chars, actual, estimated))
    assert not under, under


def test_estimator_is_close_rather_than_merely_safe():
    """A safe estimator that always says ten lines would pass the test above."""
    over = 0
    for chars, actual in MEASURED_BULLETS:
        estimated = panel_fit.estimate_lines(
            "x" * chars, font_px=MEASURED_BULLET_FONT,
            width_px=MEASURED_BULLET_WIDTH)
        over += estimated - actual
    assert over <= 2, f"over-counted by {over} lines across 16 bullets"


def test_estimator_floor_and_degenerate_widths():
    assert panel_fit.estimate_lines("", font_px=12.5, width_px=500) == 1
    assert panel_fit.estimate_lines("x", font_px=12.5, width_px=0) == 1
    assert panel_fit.estimate_lines("x", font_px=0, width_px=500) == 1


def test_estimator_scales_with_size_and_width():
    long_text = "word " * 200
    assert (panel_fit.estimate_lines(long_text, font_px=12.5, width_px=500)
            > panel_fit.estimate_lines(long_text, font_px=10.5, width_px=500))
    assert (panel_fit.estimate_lines(long_text, font_px=12.5, width_px=300)
            > panel_fit.estimate_lines(long_text, font_px=12.5, width_px=600))


# --- the geometry ----------------------------------------------------------

def test_text_columns_match_the_rendered_deck():
    assert panel_fit.panel_text_width() == 532
    assert panel_fit.bullet_text_width() == MEASURED_BULLET_WIDTH


def test_geometry_matches_chrome_and_never_claims_extra_room():
    model = {
        "build band": panel_fit.build_band_height(DECK_PHASES),
        "today metrics": panel_fit.metrics_height(DECK_TODAY_METRICS),
        "after metrics": panel_fit.metrics_height(DECK_AFTER_METRICS),
        "today bullet room": panel_fit.bullet_room(
            headline=DECK_HEADLINE, summary=DECK_SUMMARY, phases=DECK_PHASES,
            metrics=DECK_TODAY_METRICS),
        "after bullet room": panel_fit.bullet_room(
            headline=DECK_HEADLINE, summary=DECK_SUMMARY, phases=DECK_PHASES,
            metrics=DECK_AFTER_METRICS),
    }
    for name, measured in MEASURED_GEOMETRY.items():
        assert abs(model[name] - measured) <= GEOMETRY_TOLERANCE_PX, \
            f"{name}: model {model[name]:.1f} vs Chrome {measured}"
    for name in ("today bullet room", "after bullet room"):
        assert model[name] <= MEASURED_GEOMETRY[name], \
            f"{name} claims more room than Chrome measured"


def test_a_reviewer_marker_is_measured_at_the_chip_size():
    """A marker sets its own 9px type, so measuring it at the metric's 24px
    wraps a chip that in fact takes one line, and steals room from the bullets."""
    marker = panel_fit.metrics_height([("[MISSING: after_metric_2]", "")])
    plain = panel_fit.metrics_height([("0.6-1.3pp", "")])
    assert marker == plain, (marker, plain)


def test_room_shrinks_when_the_metrics_above_it_grow():
    small = panel_fit.bullet_room(headline=DECK_HEADLINE, summary=DECK_SUMMARY,
                                  phases=DECK_PHASES,
                                  metrics=[("12.4%", "Adjusted EBITDA margin")])
    large = panel_fit.bullet_room(headline=DECK_HEADLINE, summary=DECK_SUMMARY,
                                  phases=DECK_PHASES,
                                  metrics=DECK_TODAY_METRICS)
    assert small > large, (small, large)


def test_room_shrinks_when_the_headline_wraps_further():
    one_line = panel_fit.bullet_room(headline="Short headline",
                                     summary=DECK_SUMMARY, phases=DECK_PHASES,
                                     metrics=DECK_TODAY_METRICS)
    assert one_line > panel_fit.bullet_room(
        headline=DECK_HEADLINE, summary=DECK_SUMMARY, phases=DECK_PHASES,
        metrics=DECK_TODAY_METRICS)


def test_a_band_with_more_phases_leaves_the_panels_less_room():
    few = panel_fit.bullet_room(headline=DECK_HEADLINE, summary=DECK_SUMMARY,
                                phases=DECK_PHASES[:2],
                                metrics=DECK_TODAY_METRICS)
    assert few > panel_fit.bullet_room(
        headline=DECK_HEADLINE, summary=DECK_SUMMARY,
        phases=DECK_PHASES + DECK_PHASES, metrics=DECK_TODAY_METRICS)


def test_no_band_means_no_band_height_and_no_grid_gap():
    assert panel_fit.build_band_height([]) == 0.0
    assert panel_fit.build_band_height(None) == 0.0
    with_band = panel_fit.bullet_room(headline=DECK_HEADLINE,
                                      summary=DECK_SUMMARY, phases=DECK_PHASES,
                                      metrics=DECK_TODAY_METRICS)
    without = panel_fit.bullet_room(headline=DECK_HEADLINE,
                                    summary=DECK_SUMMARY, phases=[],
                                    metrics=DECK_TODAY_METRICS)
    assert without - with_band >= panel_fit.OPP_GRID_GAP


def test_room_is_never_negative():
    assert panel_fit.bullet_room(
        headline="Headline " * 40, summary="Summary " * 60,
        phases=DECK_PHASES * 4,
        metrics=[("a value that runs on " * 6, "and a label " * 6)]) >= 0.0


# --- the fit ---------------------------------------------------------------

def test_the_shipped_panel_would_have_been_cut_to_what_fits():
    """The regression, in the numbers that produced it.

    Seven bullets went into a panel with room for two, and the surplus was
    painted across the build band.
    """
    bullets = [SAMPLE] * 7
    room = panel_fit.bullet_room(headline=DECK_HEADLINE, summary=DECK_SUMMARY,
                                 phases=DECK_PHASES,
                                 metrics=DECK_TODAY_METRICS)
    fit = panel_fit.fit_bullets(bullets, room_px=room)
    assert fit.used_px <= room, (fit.used_px, room)
    assert len(fit.kept) < 7
    assert len(fit.kept) + len(fit.dropped) == 7


def test_what_fits_is_never_rewritten():
    bullets = ["  spacing and punctuation: kept exactly.  ", "Second bullet."]
    fit = panel_fit.fit_bullets(bullets, room_px=400)
    assert fit.kept == bullets, fit.kept


def test_nothing_is_lost_between_kept_and_dropped():
    bullets = [f"bullet number {n} with some words after it" for n in range(9)]
    fit = panel_fit.fit_bullets(bullets, room_px=90)
    assert fit.kept + fit.dropped == bullets


def test_the_house_type_size_is_kept_when_shrinking_buys_only_one_bullet():
    """Measured on the 2026-08-19 deck: every step down bought exactly one more
    bullet, which is not worth shrinking every line on the panel for."""
    bullets = [SAMPLE] * 5
    fit = panel_fit.fit_bullets(bullets, room_px=113)
    assert fit.font_px == panel_fit.BULLET_FONT, fit


def test_type_steps_down_when_it_buys_more_than_one():
    # Two lines at the house size, one line at the smaller one.
    two_liner = ("Extending this to proactive splice-point degradation "
                 "monitoring and environmental damage prediction.")
    at_house = panel_fit.fit_bullets([two_liner] * 6, room_px=135,
                                     font_steps=(panel_fit.BULLET_FONT,))
    stepped = panel_fit.fit_bullets([two_liner] * 6, room_px=135,
                                    font_steps=(panel_fit.BULLET_FONT, 6.0))
    assert len(stepped.kept) - len(at_house.kept) > panel_fit.FONT_STEP_TOLERANCE
    assert stepped.font_px == 6.0, stepped
    # And with room to spare it stays at the house size.
    roomy = panel_fit.fit_bullets([two_liner] * 3, room_px=1000,
                                  font_steps=(panel_fit.BULLET_FONT, 6.0))
    assert roomy.font_px == panel_fit.BULLET_FONT, roomy


def test_the_panel_cap_holds_even_with_room_to_spare():
    fit = panel_fit.fit_bullets(["short"] * 12, room_px=5000)
    assert len(fit.kept) == panel_fit.MAX_BULLETS_PER_PANEL
    assert len(fit.dropped) == 12 - panel_fit.MAX_BULLETS_PER_PANEL


def test_everything_fits_when_there_is_room():
    bullets = ["one", "two", "three"]
    fit = panel_fit.fit_bullets(bullets, room_px=5000)
    assert fit.kept == bullets
    assert fit.dropped == []
    assert fit.note == ""
    assert fit.overflowed is False


def test_no_bullets_is_not_a_defect():
    for empty in ([], None, ["", "  "]):
        fit = panel_fit.fit_bullets(empty, room_px=100)
        assert fit.kept == [] and fit.dropped == [] and fit.note == ""
        assert fit.overflowed is False


def test_a_panel_with_no_room_still_shows_one_and_says_so():
    """An empty panel hides that there was anything to show. One bullet plus a
    reported overflow is the honest outcome, and the layout guard sees the clip.
    """
    fit = panel_fit.fit_bullets([SAMPLE, SAMPLE], room_px=1)
    assert len(fit.kept) == 1
    assert fit.overflowed is True
    assert fit.font_px == panel_fit.BULLET_FONT_STEPS[-1]


def test_dropping_is_reported_for_the_reviewer():
    fit = panel_fit.fit_bullets([SAMPLE] * 7, room_px=60)
    assert fit.note, "a panel that dropped content has to say so"
    assert str(len(fit.kept)) in fit.note and "7" in fit.note


# --- the pinned stylesheet -------------------------------------------------

def test_geometry_constants_still_match_the_pinned_stylesheet():
    """The fitter's constants mirror the CSS; this is what catches the drift."""
    assert _px(".slide", "width") == panel_fit.SLIDE_W
    assert _px(".slide", "height") == panel_fit.SLIDE_H
    pad = _css_value(".slide", "padding").split()
    assert float(pad[0].rstrip("px")) == panel_fit.SLIDE_PAD_TOP
    assert float(pad[1].rstrip("px")) == panel_fit.SLIDE_PAD_X
    assert float(pad[2].rstrip("px")) == panel_fit.SLIDE_PAD_BOTTOM

    assert _px(".headline", "font-size") == panel_fit.HEADLINE_FONT
    assert _px(".headline", "max-width") == panel_fit.HEADLINE_MAX_WIDTH
    assert _px(".headline", "margin-bottom") == panel_fit.HEADLINE_MARGIN_BOTTOM
    assert _px(".summary", "font-size") == panel_fit.SUMMARY_FONT
    assert float(_css_value(".summary", "line-height")) == panel_fit.SUMMARY_LINE_HEIGHT
    assert _px(".kicker", "font-size") == panel_fit.KICKER_FONT
    assert _px(".body", "margin-top") == panel_fit.BODY_MARGIN_TOP

    assert _px(".opp-grid", "gap") == panel_fit.OPP_GRID_GAP
    assert _px(".cols2", "gap") == panel_fit.COLS2_GAP
    panel_pad = _css_value(".panel", "padding").split()
    assert float(panel_pad[0].rstrip("px")) == panel_fit.PANEL_PAD_Y
    assert float(panel_pad[1].rstrip("px")) == panel_fit.PANEL_PAD_X
    assert _px(".panel--today", "border-top") == panel_fit.PANEL_BORDER_TOP
    assert _px(".panel", "border") == panel_fit.PANEL_BORDER_X
    assert _px(".panel-head", "font-size") == panel_fit.PANEL_HEAD_FONT
    assert _px(".panel-head", "margin-bottom") == panel_fit.PANEL_HEAD_MARGIN_BOTTOM

    assert _px(".metrics", "gap") == panel_fit.METRICS_GAP
    assert _px(".metric", "margin-bottom") == panel_fit.METRIC_MARGIN_BOTTOM
    assert _px(".metric .val", "font-size") == panel_fit.METRIC_VAL_FONT
    assert float(_css_value(".metric .val", "line-height")) == panel_fit.METRIC_VAL_LINE_HEIGHT
    assert _px(".metric .lbl", "font-size") == panel_fit.METRIC_LBL_FONT
    assert _px(".metric .lbl", "margin-top") == panel_fit.METRIC_LBL_MARGIN_TOP

    assert _px(".bullets", "padding-top") == panel_fit.BULLETS_PAD_TOP
    assert _px(".bullets li", "font-size") == panel_fit.BULLET_FONT
    assert float(_css_value(".bullets li", "line-height")) == panel_fit.BULLET_LINE_HEIGHT
    assert _px(".bullets li", "margin-bottom") == panel_fit.BULLET_MARGIN_BOTTOM
    assert _px(".bullets li", "padding-left") == panel_fit.BULLET_PAD_LEFT

    band_pad = _css_value(".build-band", "padding").split()
    assert float(band_pad[0].rstrip("px")) == panel_fit.BAND_PAD_Y
    assert float(band_pad[1].rstrip("px")) == panel_fit.BAND_PAD_X
    assert _px(".build-phases", "gap") == panel_fit.BAND_GAP
    assert _px(".build-phases .pn", "font-size") == panel_fit.BAND_PN_FONT
    assert _px(".build-phases .pn", "margin-bottom") == panel_fit.BAND_PN_MARGIN_BOTTOM
    assert _px(".build-phases .pd", "font-size") == panel_fit.BAND_PD_FONT
    assert float(_css_value(".build-phases .pd", "line-height")) == panel_fit.BAND_PD_LINE_HEIGHT
    assert _px(".flag", "font-size") == panel_fit.FLAG_FONT

    assert panel_fit.BULLET_FONT_STEPS[0] == panel_fit.BULLET_FONT


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"PASS  {test.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL  {test.__name__}: {e}")
    print(f"\n{len(tests) - failures} of {len(tests)} passed")
    sys.exit(1 if failures else 0)
