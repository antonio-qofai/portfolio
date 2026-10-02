"""Tests for the layout guard — clipped, overlapping and off-slide content.

Runs against SYNTHETIC slides, not the shipped scaffolds: the guard's job is to
detect a geometric fact, and a hand-built slide with a known 100px overflow proves
that far better than a real deck whose numbers move whenever the house style is
tuned. The scaffolds are covered by rendering them through the real pipeline and by
`scripts/check_deck_layout.py`.

Needs a local Chrome/Chromium/Edge (set `CHROME_BIN` to pin one). Skips cleanly
when there is none, since the guard itself is designed to skip in that case.

Run with: python3 tests/test_layout_guard.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from layout_guard import (
    LayoutError,
    check_layout,
    describe_finding,
    find_chrome,
)

_HAVE_CHROME = find_chrome() is not None


def _deck(body, extra_css=""):
    """A minimal two-slide deck: fixed 1280x720 frames, no external anything."""
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
      *{{box-sizing:border-box;margin:0;padding:0}}
      body{{font-family:Arial,sans-serif}}
      .slide{{position:relative;width:1280px;height:720px;padding:40px;overflow:hidden}}
      .panel{{width:600px;height:200px;overflow:hidden;border:1px solid #000}}
      .bar{{height:30px;white-space:nowrap;overflow:hidden;font-size:12px}}
      .roomy{{height:30px;white-space:nowrap;font-size:12px}}
      {extra_css}
    </style></head><body>
      <section class="slide"><h1>Slide one</h1></section>
      <section class="slide">{body}</section>
    </body></html>"""


def test_clean_deck_has_no_findings():
    if not _HAVE_CHROME:
        return
    report = check_layout(_deck('<div class="bar" style="width:400px">short</div>'))
    assert report["checked"] is True, report
    assert report["ok"] is True, report["summary"]
    assert report["slides"] == 2, report
    assert report["findings"] == [], report["summary"]


def test_clipped_text_is_caught_with_slide_and_pixels():
    if not _HAVE_CHROME:
        return
    long_text = "Switch production traffic to the new pipeline and retire the old one"
    report = check_layout(_deck(f'<div class="bar" style="width:60px">{long_text}</div>'))
    assert report["ok"] is False, report
    clipped = [f for f in report["findings"] if f["kind"] == "clipped"]
    assert clipped, report["findings"]
    finding = clipped[0]
    # It lands on the right slide, names the element, and quantifies the loss.
    assert finding["slide"] == 2, finding
    assert "bar" in finding["element"], finding
    assert finding["axis"] == "horizontal", finding
    assert finding["overflow_px"] > 100, finding
    assert "Switch production traffic" in finding["text"], finding


def test_content_escaping_its_container_is_caught():
    if not _HAVE_CHROME:
        return
    # The inner block is wider than the clipping .panel, so its right edge is cut.
    report = check_layout(_deck(
        '<div class="panel"><div class="roomy" style="width:900px">'
        'Pushed past the panel edge</div></div>'
    ))
    assert report["ok"] is False, report
    escaped = [f for f in report["findings"] if f["kind"] == "escaped"]
    assert escaped, report["findings"]
    finding = escaped[0]
    assert finding["slide"] == 2, finding
    assert finding["side"] == "right", finding
    assert "panel" in finding["container"], finding
    assert finding["overflow_px"] > 250, finding


def test_content_pushed_below_a_slide_is_caught():
    if not _HAVE_CHROME:
        return
    # A slide is a fixed 720px frame with overflow:hidden — content past the
    # bottom is invisible, which is the "does not fit on the slide" defect.
    report = check_layout(_deck(
        '<div class="roomy" style="height:900px">Taller than the frame</div>'
    ))
    assert report["ok"] is False, report
    vertical = [f for f in report["findings"]
                if f["kind"] == "escaped" and f.get("side") == "bottom"]
    assert vertical, report["summary"]
    assert "slide" in vertical[0]["container"], vertical[0]


def test_subpixel_overflow_is_tolerated():
    if not _HAVE_CHROME:
        return
    # Rounding and trailing letter-spacing routinely produce a fraction of a pixel
    # of overflow. Flagging it would bury the real 100px truncations in noise.
    report = check_layout(
        _deck('<div class="bar" style="width:400px">tight fit</div>'),
        tolerance=0.0,
    )
    strict_findings = len(report["findings"])
    default = check_layout(_deck('<div class="bar" style="width:400px">tight fit</div>'))
    assert default["ok"] is True, default["summary"]
    assert strict_findings >= 0  # tolerance is a knob, not a hardcoded constant


def test_strict_raises_and_lists_every_finding():
    if not _HAVE_CHROME:
        return
    html = _deck(
        '<div class="bar" style="width:40px">first long label that cannot fit</div>'
        '<div class="bar" style="width:40px">second long label that cannot fit</div>'
    )
    try:
        check_layout(html, strict=True)
    except LayoutError as exc:
        message = str(exc)
        assert message.count("  - ") >= 2, message
        assert "first long label" in message and "second long label" in message
        return
    raise AssertionError("expected LayoutError under strict=True")


def test_missing_browser_is_a_reported_skip_not_a_failure():
    # The guard must never block a deck on a machine with no browser — but it must
    # say so, because "could not check" is not "checked and clean".
    report = check_layout(_deck("<h1>x</h1>"), chrome="/nonexistent/chrome")
    assert report["checked"] is False, report
    assert report["ok"] is True, report
    assert report["skipped"], report
    assert report["findings"] == [], report


def test_missing_browser_does_not_raise_even_under_strict():
    report = check_layout(_deck("<h1>x</h1>"), chrome="/nonexistent/chrome",
                          strict=True)
    assert report["checked"] is False and report["ok"] is True, report


def _spill(items, band_children=1, box_height=60):
    """A box too short for its own items, above a band the spill lands on.

    Nothing here clips and nothing leaves the slide, which is the whole point:
    this is the shape the guard used to report as clean.
    """
    lines = "".join(f'<div class="roomy">item {n}</div>' for n in range(items))
    band = "".join(f'<div class="roomy">band line {n}</div>'
                   for n in range(band_children))
    return _deck(
        f'<div class="stack"><div class="box" style="height:{box_height}px">'
        f'{lines}</div><div class="band">{band}</div></div>',
        extra_css=".stack{width:600px}.band{height:120px;background:#ddd}",
    )


def test_text_painted_over_other_text_is_caught():
    if not _HAVE_CHROME:
        return
    report = check_layout(_spill(items=4))
    overlaps = [f for f in report["findings"] if f["kind"] == "overlapped"]
    assert report["ok"] is False, report["summary"]
    assert len(overlaps) == 1, report["summary"]
    found = overlaps[0]
    assert found["slide"] == 2, found
    assert found["element"] == "div.box", found
    assert found["other"] == "div.band", found
    assert found["overflow_px"] > 20, found


def test_overlap_is_neither_clipped_nor_escaped():
    """The regression this check exists for.

    Every box in the chain has ``overflow:visible`` and the spill stays inside
    the slide, so both of the original checks are silent by construction. If this
    ever starts reporting a clipped or escaped finding instead, the fixture has
    drifted and the overlap case is no longer being exercised.
    """
    if not _HAVE_CHROME:
        return
    kinds = {f["kind"] for f in check_layout(_spill(items=4))["findings"]}
    assert kinds == {"overlapped"}, kinds


def test_a_long_spill_reports_once_per_region_not_once_per_pair():
    if not _HAVE_CHROME:
        return
    report = check_layout(_spill(items=9, band_children=4))
    overlaps = [f for f in report["findings"] if f["kind"] == "overlapped"]
    assert len(overlaps) == 1, [f["element"] + " over " + f["other"]
                                for f in overlaps]


def test_overlap_measures_the_whole_covered_region():
    """Not the depth of the worst single pair.

    Nine 30px items spilling onto a band cover it one line at a time, so a
    per-pair number would report about one line height. The band is buried under
    far more than that, and the report has to say so.
    """
    if not _HAVE_CHROME:
        return
    report = check_layout(_spill(items=9, band_children=4))
    overlap = [f for f in report["findings"] if f["kind"] == "overlapped"][0]
    assert overlap["overflow_px"] > 60, overlap


def test_overlap_quotes_both_sets_of_words():
    if not _HAVE_CHROME:
        return
    report = check_layout(_spill(items=4, band_children=2))
    overlap = [f for f in report["findings"] if f["kind"] == "overlapped"][0]
    assert "item" in overlap["text"], overlap
    assert "band line" in overlap["other_text"], overlap


def test_deliberate_layering_is_not_reported_as_an_overlap():
    """A positioned element sits on top of something on purpose."""
    if not _HAVE_CHROME:
        return
    report = check_layout(_deck(
        '<div class="stack"><div class="roomy">underneath</div>'
        '<div class="badge">on top</div></div>',
        extra_css=".stack{position:relative;width:600px}"
                  ".badge{position:absolute;top:5px;left:20px;font-size:12px}",
    ))
    assert report["ok"] is True, report["summary"]


def test_boxes_that_merely_abut_are_not_an_overlap():
    if not _HAVE_CHROME:
        return
    report = check_layout(_deck(
        '<div class="roomy">first line</div><div class="roomy">second line</div>'
    ))
    assert report["ok"] is True, report["summary"]


def test_overlap_below_the_floor_is_tolerated_and_above_it_is_not():
    """Adjacent grid tracks abut and boxes carry padding, so a few pixels of box
    intersection is not two sets of words on top of each other."""
    if not _HAVE_CHROME:
        return

    def overlap_by(pixels):
        return check_layout(_deck(
            '<div class="roomy">first line</div>'
            f'<div class="roomy" style="margin-top:-{pixels}px">second line</div>'
        ))

    assert overlap_by(3)["ok"] is True, overlap_by(3)["summary"]
    deep = overlap_by(12)
    assert deep["ok"] is False, deep["summary"]
    assert deep["findings"][0]["kind"] == "overlapped", deep["findings"]


def test_a_clean_deck_still_reports_no_overlap():
    if not _HAVE_CHROME:
        return
    report = check_layout(_deck(
        '<div class="stack"><div class="roomy">one</div>'
        '<div class="roomy">two</div><div class="band">a band below</div></div>',
        extra_css=".stack{width:600px}.band{height:120px;background:#ddd}",
    ))
    assert report["ok"] is True, report["summary"]


def test_describe_finding_reads_as_a_sentence():
    clipped = describe_finding({
        "kind": "clipped", "slide": 4, "element": "div.bar", "axis": "horizontal",
        "overflow_px": 109.0, "text": "Reporting package",
    })
    assert clipped.startswith("Slide 4:"), clipped
    assert "109" in clipped and "Reporting package" in clipped, clipped
    escaped = describe_finding({
        "kind": "escaped", "slide": 2, "element": "div.bar", "side": "right",
        "container": "div.gantt", "overflow_px": 38.5, "text": "Deployment buffer",
    })
    assert "past the right edge of div.gantt" in escaped, escaped
    overlapped = describe_finding({
        "kind": "overlapped", "slide": 2, "element": "div.cols2",
        "other": "div.build-band", "overflow_px": 151.0,
        "text": "the aging asset base", "other_text": "Phase 3: Full Rollout",
    })
    assert "paints 151" in overlapped, overlapped
    assert "div.build-band" in overlapped, overlapped
    assert "Phase 3: Full Rollout" in overlapped, overlapped


def test_findings_are_ordered_worst_first():
    if not _HAVE_CHROME:
        return
    html = _deck(
        '<div class="bar" style="width:300px">a slightly too long label here</div>'
        '<div class="bar" style="width:30px">a catastrophically long label that is '
        'nowhere close to fitting inside its box at all</div>'
    )
    report = check_layout(html)
    overflows = [f["overflow_px"] for f in report["findings"]]
    assert overflows == sorted(overflows, reverse=True), overflows


if __name__ == "__main__":
    if not _HAVE_CHROME:
        print("NOTE  no Chrome found — browser-dependent cases will no-op")
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"PASS  {test.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL  {test.__name__}: {e}")

    if failures:
        print(f"\n{failures} test(s) failed")
        sys.exit(1)
    print(f"\nAll {len(tests)} tests passed")
