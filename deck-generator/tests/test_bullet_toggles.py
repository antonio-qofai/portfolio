"""Tests for the reviewer's slide 2 bullet switches (item 16's toggle rider).

Three things are guarded here and they fail for different reasons.

THE STORE. A switch is reviewer state, keyed by the bullet's TEXT rather than
its position, because the ranking pass reorders these lists between runs. If
keying ever moves back to an index, the "a reordered list keeps its switches"
case is the one that goes red.

THE FITTER. What a switch means geometrically: a switched-on bullet is kept and
the type ladder is spent to keep it, a switched-off one leaves without being
called a drop-for-room, and a request that cannot fit even at the smallest
readable size comes back refused rather than silently honoured. The measured
case is the important one: forcing a bullet on must never make a panel carry
FEWER bullets than the automatic fit did.

THE DELIVERY. A type size below the house size reaches the deck as a rule
addressed to the right slide and the right panel, and a deck where nothing was
resized comes out byte-identical.

Run with: python3 tests/test_bullet_toggles.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import bullet_toggles  # noqa: E402
import bullet_type  # noqa: E402
import panel_fit  # noqa: E402

# The six real bullets of the Ridgeline slide 2 rendered 2026-09-02, which is
# what every geometric case below is measured against. Invented strings would
# prove the arithmetic and nothing about the copy this build actually writes.
REAL = [
    "Foremen handwrite daily field tickets on paper; keyed into Excel next morning (12–24h lag)",
    "Fuel and equipment-hour entry is optional; ~52 active jobs live in disconnected workbooks",
    "−41.2 – +58.4% · Month-over-month EBITDA % swings, with little real-time visibility",
    "~26 days · Reporting lag after project close prevents real-time intervention",
    "Connected tablet app with enforced daily fuel + equipment-hour capture and automatic job allocation",
    "Live per-job production and financial performance",
]

# A crowded panel: the room a slide 2 has when its metrics block runs long and
# its build band carries several phases. The roomy case does not exercise the
# ladder at all, because nothing is ever refused there.
CROWDED_ROOM = 110.0


def _store(tmp):
    return os.path.join(tmp, "bullet-toggles.json")


def _tmpdir():
    import tempfile
    return tempfile.mkdtemp(prefix="bullet-toggles-")


def test_a_switch_is_recorded_and_read_back():
    path = _store(_tmpdir())
    bullet_toggles.set_toggle("packet.md", 0, "today_pain_bullets", REAL[0],
                              bullet_toggles.ON, path=path)
    overrides = bullet_toggles.overrides_for("packet.md", path=path)
    key = bullet_toggles.bullet_key(REAL[0])
    assert overrides[0]["today_pain_bullets"][key] == bullet_toggles.ON


def test_a_packet_with_no_switches_reads_as_nothing():
    path = _store(_tmpdir())
    assert bullet_toggles.overrides_for("packet.md", path=path) == {}


def test_setting_the_same_bullet_twice_replaces_rather_than_stacks():
    path = _store(_tmpdir())
    for state in (bullet_toggles.ON, bullet_toggles.OFF):
        bullet_toggles.set_toggle("p.md", 0, "today_pain_bullets", REAL[0],
                                  state, path=path)
    rows = bullet_toggles.load_toggles(path)["toggles"]
    assert len(rows) == 1
    assert rows[0]["state"] == bullet_toggles.OFF


def test_clearing_is_different_from_switching_off():
    """Cleared means nobody decided; off means a reviewer decided against."""
    path = _store(_tmpdir())
    bullet_toggles.set_toggle("p.md", 0, "today_pain_bullets", REAL[0],
                              bullet_toggles.OFF, path=path)
    bullet_toggles.clear_toggle("p.md", 0, "today_pain_bullets", REAL[0],
                                path=path)
    assert bullet_toggles.overrides_for("p.md", path=path) == {}


def test_a_switch_is_keyed_by_text_so_a_reordered_list_keeps_it():
    """The ranking pass reorders these lists, so an index would key the wrong
    bullet on the next run. The key travels with the sentence."""
    path = _store(_tmpdir())
    bullet_toggles.set_toggle("p.md", 0, "today_pain_bullets", REAL[3],
                              bullet_toggles.ON, path=path)
    recorded = bullet_toggles.overrides_for("p.md", path=path)[0]["today_pain_bullets"]
    reordered = [REAL[3], REAL[0], REAL[1]]
    assert bullet_toggles.bullet_key(reordered[0]) in recorded
    assert bullet_toggles.bullet_key(REAL[0]) not in recorded


def test_whitespace_does_not_make_a_second_bullet():
    assert (bullet_toggles.bullet_key("  a   b ")
            == bullet_toggles.bullet_key("a b"))


def test_two_opportunities_keep_their_own_switches():
    """One set of switches per opportunity (Antonio, 2026-09-20): the same
    sentence on two slide 2s is two decisions."""
    path = _store(_tmpdir())
    bullet_toggles.set_toggle("p.md", 0, "today_pain_bullets", REAL[0],
                              bullet_toggles.ON, path=path)
    bullet_toggles.set_toggle("p.md", 1, "today_pain_bullets", REAL[0],
                              bullet_toggles.OFF, path=path)
    overrides = bullet_toggles.overrides_for("p.md", path=path)
    key = bullet_toggles.bullet_key(REAL[0])
    assert overrides[0]["today_pain_bullets"][key] == bullet_toggles.ON
    assert overrides[1]["today_pain_bullets"][key] == bullet_toggles.OFF


def test_a_corrupt_store_reads_as_empty_rather_than_raising():
    """Losing the switches hands every panel back to the fitter, which is the
    deck that rendered before anyone touched a switch. Refusing to open the
    studio is not."""
    directory = _tmpdir()
    path = _store(directory)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    assert bullet_toggles.load_toggles(path) == bullet_toggles._empty_store()


def test_an_untouched_panel_fits_exactly_as_it_always_did():
    room = CROWDED_ROOM
    automatic = panel_fit.fit_bullets(REAL, room_px=room)
    override = panel_fit.fit_bullets_with_overrides(REAL, room_px=room)
    assert override.kept == automatic.kept
    assert override.font_px == automatic.font_px


def test_switching_one_on_never_costs_the_panel_bullets():
    """The trap this catches: sizing off the forced bullets alone. At 110px the
    automatic fit holds four at 11.5px; asking for a fifth must not snap the
    size back to 12.5 and lose two of the four."""
    room = CROWDED_ROOM
    automatic = panel_fit.fit_bullets(REAL, room_px=room)
    forced = panel_fit.fit_bullets_with_overrides(
        REAL, room_px=room, forced_on=[REAL[4]])
    assert REAL[4] in forced.kept
    assert len(forced.kept) >= len(automatic.kept)
    assert forced.font_px <= automatic.font_px


def test_the_ladder_is_spent_only_when_a_switch_asks_for_it():
    """`FONT_STEP_TOLERANCE` keeps the automatic fit from shrinking a whole
    panel to gain one line. An explicit request overrides that and nothing
    else does."""
    room = CROWDED_ROOM
    automatic = panel_fit.fit_bullets(REAL, room_px=room)
    forced = panel_fit.fit_bullets_with_overrides(
        REAL, room_px=room, forced_on=REAL[:5])
    assert automatic.font_px > panel_fit.BULLET_FONT_STEPS[-1]
    assert forced.font_px == panel_fit.BULLET_FONT_STEPS[-1]


def test_the_floor_is_never_crossed():
    forced = panel_fit.fit_bullets_with_overrides(
        REAL, room_px=CROWDED_ROOM, forced_on=REAL)
    assert forced.font_px >= panel_fit.BULLET_FONT_STEPS[-1]


def test_a_request_that_cannot_fit_comes_back_refused():
    """Six bullets do not fit 110px even at 10.5px, so the surplus is reported
    rather than dropped quietly: the studio is meant to have refused the switch
    first, and if it did not, the reviewer has to be told which line went."""
    forced = panel_fit.fit_bullets_with_overrides(
        REAL, room_px=CROWDED_ROOM, forced_on=REAL)
    assert forced.blocked
    assert all(text not in forced.kept for text in forced.blocked)


def test_switching_off_is_not_a_drop_for_room():
    off = panel_fit.fit_bullets_with_overrides(
        REAL, room_px=CROWDED_ROOM, forced_off=[REAL[0]])
    assert REAL[0] not in off.kept
    assert REAL[0] not in off.dropped


def test_the_limit_is_the_room_and_not_a_count():
    """Short bullets fit where long ones do not, at the same room and size,
    which is why the block is geometric (Antonio, 2026-09-20)."""
    short = ["Short line %d" % n for n in range(6)]
    room = CROWDED_ROOM
    long_fit = panel_fit.fit_bullets_with_overrides(
        REAL, room_px=room, forced_on=REAL[:4])
    short_fit = panel_fit.fit_bullets_with_overrides(
        short, room_px=room, forced_on=short[:4])
    assert not short_fit.blocked
    assert len(short_fit.kept) >= len(long_fit.kept)


def test_passing_the_editorial_cap_warns_rather_than_refuses():
    """`MAX_BULLETS_PER_PANEL` is a judgment about what a panel should be, and a
    reviewer looking at the slide may pass it. It is reported, not enforced."""
    roomy = 400.0
    short = ["Short line %d" % n for n in range(7)]
    fit = panel_fit.fit_bullets_with_overrides(
        short, room_px=roomy, forced_on=short)
    assert len(fit.kept) > panel_fit.MAX_BULLETS_PER_PANEL
    assert fit.over_cap


def test_a_house_size_panel_puts_no_rule_on_the_deck():
    """A deck where nothing was resized comes out exactly as it did before this
    existed, which is what makes the style block readable as a signal."""
    fits = [{"today_pain_bullets": {"panel": "today",
                                    "font_px": panel_fit.BULLET_FONT}}]
    assert bullet_type.panel_type_rules(fits) == []
    html = "<html><body><section></section></body></html>"
    assert bullet_type.apply_panel_type(html, fits) == html


def test_a_stepped_panel_is_addressed_by_slide_and_by_panel():
    fits = [
        {"today_pain_bullets": {"panel": "today", "font_px": 11.0}},
        {"after_capability_bullets": {"panel": "after", "font_px": 10.5}},
    ]
    block = bullet_type.type_style_block(bullet_type.panel_type_rules(fits))
    assert '[data-slide="2"] .panel--today .bullets li{font-size:11px}' in block
    assert '[data-slide="3"] .panel--after .bullets li{font-size:10.5px}' in block


def test_the_rule_goes_inside_the_document_so_it_wins_by_order():
    fits = [{"today_pain_bullets": {"panel": "today", "font_px": 11.0}}]
    html = "<html><body><p>deck</p></body></html>"
    out = bullet_type.apply_panel_type(html, fits)
    assert out.index("<style>") < out.index("</body>")
    assert out.endswith("</body></html>")




# --- The re-render: one deck, and what the replay can and cannot carry -------

def _deck_dir():
    import tempfile
    return tempfile.mkdtemp(prefix="rerender-")


def _write(path, html):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)


def _deck(*lines):
    slides = "".join(
        '<section class="slide" data-slide="%d"><p>%s</p></section>' % (i + 1, line)
        for i, line in enumerate(lines))
    return "<html><body>" + slides + "</body></html>"


def test_a_rerender_lands_as_a_revision_of_the_same_deck():
    """Not a second deck. The whole point of the ruling: a reviewer keeps one
    deck with its history, rather than a fork per render."""
    import html_edit_layer as hel
    directory = _deck_dir()
    base = os.path.join(directory, "output-7.html")
    _write(base, _deck("original one", "original two"))
    out = hel.save_rerender_and_replay(base, _deck("fresh one", "fresh two"))
    assert out["revision_name"] == "output-7-r1.html"
    assert os.path.isfile(out["revision_path"])
    # The original render is never overwritten.
    with open(base, encoding="utf-8") as fh:
        assert "original one" in fh.read()


def test_an_edit_whose_line_survives_is_replayed():
    import html_edit_layer as hel
    directory = _deck_dir()
    base = os.path.join(directory, "output-7.html")
    _write(base, _deck("the cost is 41m", "second slide"))
    hel.apply_edit_and_save(base, 1, "41m", "42m")
    # The model writes the same sentence again on the next render.
    out = hel.save_rerender_and_replay(base, _deck("the cost is 41m", "second slide"))
    with open(out["revision_path"], encoding="utf-8") as fh:
        rendered = fh.read()
    assert "42m" in rendered
    assert len(out["replayed"]) == 1
    assert out["unapplied"] == []


def test_an_edit_whose_line_came_back_differently_is_named_not_lost():
    """The honest half. The replay is partial by nature, so what did not carry
    is reported with its text rather than counted or swallowed."""
    import html_edit_layer as hel
    directory = _deck_dir()
    base = os.path.join(directory, "output-7.html")
    _write(base, _deck("the cost is 41m", "second slide"))
    hel.apply_edit_and_save(base, 1, "41m", "42m")
    out = hel.save_rerender_and_replay(base, _deck("the price is 40m", "second slide"))
    assert out["replayed"] == []
    assert len(out["unapplied"]) == 1
    assert out["unapplied"][0]["before"] == "41m"
    assert out["unapplied"][0]["reason"]


def test_the_log_records_the_rerender_beside_the_edits():
    """A re-render is a thing that happened to the deck, so it is in the deck's
    own history next to the edits, and it is marked as its own kind so the
    preference gate can never read one as a format edit."""
    import html_edit_layer as hel
    directory = _deck_dir()
    base = os.path.join(directory, "output-7.html")
    _write(base, _deck("the cost is 41m"))
    hel.apply_edit_and_save(base, 1, "41m", "42m")
    hel.save_rerender_and_replay(base, _deck("the price is 40m"))
    log = hel.load_edit_log(base)
    assert [e["kind"] for e in log] == ["content", hel.RERENDER_KIND]
    assert log[-1]["unapplied"][0]["before"] == "41m"
    # The edit that did not carry is still in the history as itself.
    assert log[0]["before"] == "41m"



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
