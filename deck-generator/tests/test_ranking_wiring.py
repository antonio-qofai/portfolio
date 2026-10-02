"""The ranking pass reaches the deck — the seam, not the module.

`test_bullet_ranking` proves the pass works. This file proves something else, and
the difference is the whole lesson of E11 Stage 2g: six stages were built, tested,
green and switched off in the one place a reviewer clicks, because every test
injected at the seam and nobody measured the caller.

So these run the REAL `LiveProposalProvider` with a fake ranker and follow the
order all the way to the placeholder map: provider records it in section 8, the
adapter reads it back, the fitter trims from the tail of the reordered list. A
break anywhere in that chain reds here.

No network. The MCP client is `test_live_seam`'s stub and the ranker is a plain
callable, so nothing constructs a model client.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from bullet_ranking import AFTER, TODAY, Ranking  # noqa: E402
from data_source_adapter import _section_yaml, _split_sections, map_packet  # noqa: E402
from live_proposal_provider import LiveProposalProvider  # noqa: E402
from test_live_seam import CLIENTS, STAMP, StubClient  # noqa: E402
from test_second_pass import extractor_returning, paper  # noqa: E402
from test_paper_extraction import answer, field, item  # noqa: E402

TODAY_ROLE = "today_pain_bullets"
AFTER_ROLE = "after_capability_bullets"


def _request(key):
    spec = CLIENTS[key]
    return {"company": spec["company"]["name"],
            "project": spec["project"]["name"],
            "pe_firm": spec["pe_firm"],
            "proposal_date": "2026-08-15",
            "options": {"min_data_completeness": 0.70}}


def _bullets_answer(text):
    """A second-pass answer that fills both of slide 2's bullet lists.

    There is nothing to rank on a packet whose lists are empty, and the
    deterministic parsers fill neither of these: they are prose, and prose is the
    second pass's to read. So the seam has to be driven with both passes on,
    which is also the configuration a live studio run uses.
    """
    lines = [line.strip() for line in text.splitlines()
             if line.strip() and not line.startswith(("#", "|", "<", "["))
             and len(line.strip()) > 60]
    return answer(
        field("today_pain_points", *(
            item(line, "Financial Analysis > Current State", value=line)
            for line in lines[:3])),
        field("target_capabilities", *(
            item(line, "Financial Analysis > Current State", value=line)
            for line in lines[3:6])),
    )


def _document(key="one", ranker=None, with_bullets=True):
    """One client's packet markdown, built by the real provider."""
    spec = CLIENTS[key]
    extractor = None
    if with_bullets:
        extractor = extractor_returning(_bullets_answer(paper(spec["shape"])))
    provider = LiveProposalProvider(StubClient(spec), generated_at=STAMP,
                                    ranker=ranker, extractor=extractor)
    envelope = provider.poll(provider.submit(_request(key)))["envelope"]
    assert envelope["status"] == "ok", envelope
    return envelope["packet"]


def _priority(markdown):
    """Section 8's `display_priority`, read with the adapter's own parser rather
    than a yaml library the system interpreter does not have."""
    section8 = _section_yaml(_split_sections(markdown).get(8, ""))
    return (section8 or {}).get("provenance", {}).get("display_priority")


def reversing_ranker(record=None):
    """A fake pass that reverses every panel, so a change is unmistakable."""
    def ranker(panels, headline="", summary=""):
        if record is not None:
            record.append({"panels": {k: list(v) for k, v in panels.items()},
                           "headline": headline, "summary": summary})
        return Ranking(orders={panel: tuple(reversed(range(len(bullets))))
                               for panel, bullets in panels.items()})
    return ranker


# --- the provider records it ------------------------------------------------

def test_with_no_ranker_the_packet_claims_no_ranking_ran():
    """Absent, not empty. A block saying nothing was reordered would claim a pass
    ran and found nothing to do, which is a different fact."""
    assert _priority(_document(ranker=None)) is None


def test_a_packet_with_no_bullets_to_rank_asks_nothing_and_records_nothing():
    seen = []
    assert _priority(_document(ranker=reversing_ranker(seen),
                               with_bullets=False)) is None
    assert seen == [], "a packet with empty lists must not pay for a call"


def test_a_ranker_reaches_the_packet_and_its_order_is_recorded():
    markdown = _document(ranker=reversing_ranker())
    priority = _priority(markdown)
    assert priority, markdown
    panels = {entry["panel"]: entry for entry in priority["panels"]}
    assert panels, priority
    for entry in panels.values():
        assert entry["order"] == sorted(entry["order"], reverse=True), entry
        assert entry["reordered"] is True


def test_the_ranker_is_given_the_bullets_and_the_slide_context():
    seen = []
    _document(ranker=reversing_ranker(seen))
    assert len(seen) == 1, "the pass must run once per render, not once per build"
    call = seen[0]
    assert set(call["panels"]) == {TODAY, AFTER}
    assert any(call["panels"].values()), call["panels"]
    assert call["headline"] or call["summary"], call


def test_the_pass_runs_once_even_though_the_document_is_built_twice():
    """`_document` builds twice to measure `role_coverage`. A ranker called from
    inside `build` would be paid for twice."""
    seen = []
    _document(ranker=reversing_ranker(seen))
    assert len(seen) == 1, seen


def test_the_packets_own_lists_keep_the_order_their_source_gave_them():
    """The pass records an order and changes no list, so a reviewer comparing the
    packet against the paper sees the paper's order, not a reordered one."""
    plain = _document(ranker=None)
    ranked = _document(ranker=reversing_ranker())
    for marker in ("today_pain_points:", "target_capabilities:"):
        if marker in plain:
            before = plain.split(marker, 1)[1][:400]
            after = ranked.split(marker, 1)[1][:400]
            assert before == after, marker


# --- the adapter applies it -------------------------------------------------

def test_the_recorded_order_reaches_the_placeholder_map():
    request = _request("one")
    plain = map_packet(_document(ranker=None), request)
    ranked = map_packet(_document(ranker=reversing_ranker()), request)
    moved = False
    for role in (TODAY_ROLE, AFTER_ROLE):
        if len(plain.get(role) or []) > 1:
            assert set(ranked[role]) <= set(plain[role]) | set(ranked[role])
            if ranked[role] != plain[role]:
                moved = True
    assert moved, "reversing every panel changed nothing on the deck"


def test_reordering_never_adds_or_loses_a_bullet_on_the_deck():
    request = _request("one")
    ranked_map = map_packet(_document(ranker=reversing_ranker()), request)
    for role in (TODAY_ROLE, AFTER_ROLE):
        for bullet in ranked_map.get(role) or []:
            assert isinstance(bullet, str) and bullet.strip()


# --- the displaced-metric case, which is where the chain broke --------------
#
# `map_packet` gives slide 2's two TODAY metric slots to the baseline financials
# and appends the paper-derived metrics they displace to the panel's BULLETS, so
# the displayed list is the packet's list plus a tail the ranking pass never saw.
# Until 2026-09-02 `_apply_display_priority` compared the recorded order against
# the displayed length, found (say) 5 against 7, and dropped the entire order
# without a word. The panel then showed whichever bullets came first in the
# packet, the review surface said "no ranking pass ran on this panel", and every
# displaced metric sat at the tail where the fitter trims first. On a live WTG run
# that cost the panel the $0.85M shrinkage figure, the $193K, the $170,000 net
# adjustments and the 41.07% margin, and reported that no pass had run.
#
# These tests drive the real chain with an extractor that fills BOTH bullet lists
# and `today_metrics`, which is what a live WTG run looks like, so the mismatch is
# produced by the real mapping layer rather than hand-built.

def _metric_answer(text):
    """A second-pass answer that fills both bullet lists AND `today_metrics`.

    The metric spans are verbatim substrings of the fixture paper, because the
    real verifier runs on this answer: a value with no span in the paper is
    refused, which is the property the rest of this suite exists to hold."""
    lines = [line.strip() for line in text.splitlines()
             if line.strip() and not line.startswith(("#", "|", "<", "["))
             and len(line.strip()) > 60]
    section = "Financial Analysis > Current State"
    metrics = (
        ("all four loaders and three stackers run at 100% utilization",
         "100%", "loader and stacker utilization"),
        ("operating expenses are largely fixed** at approximately $2.55M per month",
         "$2.55M per month", "fixed operating expenses"),
    )
    return answer(
        field("today_pain_points", *(
            item(line, section, value=line) for line in lines[:5])),
        field("target_capabilities", *(
            item(line, section, value=line) for line in lines[5:9])),
        field("today_metrics", *(
            item(span, section, value=value, label=label)
            for span, value, label in metrics)),
    )


def _with_displaced_metrics(ranker=None):
    """The packet markdown and the placeholder map for the WTG-shaped run."""
    spec = CLIENTS["one"]
    provider = LiveProposalProvider(
        StubClient(spec), generated_at=STAMP, ranker=ranker,
        extractor=extractor_returning(_metric_answer(paper(spec["shape"]))),
    )
    envelope = provider.poll(provider.submit(_request("one")))["envelope"]
    assert envelope["status"] == "ok", envelope
    request = _request("one")
    return envelope["packet"], map_packet(envelope["packet"], request)


def test_the_fixture_really_does_displace_metrics_into_the_bullets():
    """Guard on the guard. If the mapping layer stopped appending, every test
    below would pass for the wrong reason."""
    markdown, placeholder_map = _with_displaced_metrics()
    # Section 2 is a list of per-opportunity entries since 2026-09-13.
    section2 = (_section_yaml(_split_sections(markdown).get(2, ""))
                .get("opportunities") or [{}])[0]
    assert len(section2.get("today_metrics") or []) == 2
    fit = placeholder_map["_panel_fit"][TODAY_ROLE]
    assert fit["of"] == len(section2["today_pain_points"]) + 2, fit


def test_an_order_shorter_than_the_displayed_list_still_reaches_the_deck():
    """The defect. The rank ran on the packet's five bullets, the slide holds
    seven, and the order used to be discarded whole."""
    _markdown, ranked = _with_displaced_metrics(reversing_ranker())
    fit = ranked["_panel_fit"][TODAY_ROLE]
    assert fit["ranked"] is True, fit
    assert fit["rank_refused"] == "", fit
    assert fit["reordered"] is True, fit


def test_the_ranked_bullets_are_the_ones_that_survive_the_trim():
    """What the rank is FOR. The fitter trims from the tail, so a discarded order
    meant the panel showed whichever bullets the packet happened to list first."""
    markdown, ranked = _with_displaced_metrics(reversing_ranker())
    # Section 2 is a list of per-opportunity entries since 2026-09-13.
    section2 = (_section_yaml(_split_sections(markdown).get(2, ""))
                .get("opportunities") or [{}])[0]
    packet_bullets = list(section2["today_pain_points"])
    kept = [row["text"] for row in ranked["_panel_fit"][TODAY_ROLE]["kept"]]
    assert kept, kept
    # The ranker reversed the packet's list, so its LAST bullet is now first.
    assert kept[0] == packet_bullets[-1], (kept[0], packet_bullets[-1])


def test_the_appended_tail_keeps_its_place_and_nothing_is_lost():
    """The tail was never ranked, so nothing here knows where those items belong
    among the ranked ones and none of them may be invented a position. What is
    guaranteed is that the displayed set is unchanged: a permutation of the head
    plus an untouched tail can neither lose nor duplicate a bullet."""
    _markdown, plain = _with_displaced_metrics(None)
    _markdown, ranked = _with_displaced_metrics(reversing_ranker())
    for role in (TODAY_ROLE, AFTER_ROLE):
        before = plain["_panel_fit"][role]
        after = ranked["_panel_fit"][role]
        assert after["of"] == before["of"], role
        assert (sorted(row["text"] for row in before["kept"] + before["dropped"])
                == sorted(row["text"] for row in after["kept"] + after["dropped"])), role
    order = ranked["_bullet_order"]["roles"][TODAY_ROLE]["order"]
    assert sorted(order) == list(range(len(order))), order
    assert order[-2:] == order[-2:] and order[-1] == len(order) - 1, order


def test_every_shown_bullet_still_names_where_it_sat_in_the_packets_list():
    """`_bullet_lineage` reads the applied order to answer that, so an order
    extended across the appended tail has to stay a valid index map."""
    _markdown, ranked = _with_displaced_metrics(reversing_ranker())
    fit = ranked["_panel_fit"][TODAY_ROLE]
    positions = [row["source_position"]
                 for row in fit["kept"] + fit["dropped"]]
    assert all(position is not None for position in positions), positions
    assert sorted(positions) == list(range(1, len(positions) + 1)), positions


# --- a refusal that cannot be repaired is now said out loud -----------------

def test_an_order_that_lines_up_with_nothing_is_recorded_as_refused():
    """A stale or malformed block still leaves the list alone, which is right.
    What changed is that it is no longer silent: "a pass ran and its order was
    dropped" used to be indistinguishable from "no pass ran", which sent a
    reviewer looking at the wrong half of the pipeline."""
    from data_source_adapter import _apply_display_priority

    placeholder_map = {TODAY_ROLE: ["a", "b", "c"]}
    report = _apply_display_priority(placeholder_map, {"provenance": {
        "display_priority": {"panels": [{"panel": TODAY, "order": [0, 1]}]}}})
    assert placeholder_map[TODAY_ROLE] == ["a", "b", "c"]
    assert report["roles"] == {}
    assert len(report["not_applied"]) == 1
    entry = report["not_applied"][0]
    assert entry["panel"] == TODAY
    assert entry["role"] == TODAY_ROLE
    assert "2 bullet(s)" in entry["reason"]
    assert "holds 3" in entry["reason"]


def test_a_refused_order_reaches_the_panel_report_as_such():
    """Three states, where the surface could previously see two."""
    from data_source_adapter import (_apply_display_priority,
                                     _fit_slide_two_bullets)

    placeholder_map = {TODAY_ROLE: ["a", "b", "c"], AFTER_ROLE: ["d", "e"]}
    report = _apply_display_priority(placeholder_map, {"provenance": {
        "display_priority": {"panels": [
            {"panel": TODAY, "order": [0, 1]},
            {"panel": AFTER, "order": [1, 0]}]}}})
    fit = _fit_slide_two_bullets(placeholder_map, [], report)
    assert fit[TODAY_ROLE]["ranked"] is False
    assert fit[TODAY_ROLE]["rank_refused"], fit[TODAY_ROLE]
    assert fit[AFTER_ROLE]["ranked"] is True
    assert fit[AFTER_ROLE]["rank_refused"] == ""


def test_a_refusal_is_kept_apart_from_the_passs_own_repairs():
    """A repair is the pass salvaging its own answer; a refusal is the answer
    being thrown away afterwards by the mapping layer. A reviewer who cannot tell
    those apart cannot tell which half to go and look at."""
    from data_source_adapter import (_apply_display_priority, bullet_selection,
                                     _fit_slide_two_bullets)

    placeholder_map = {TODAY_ROLE: ["a", "b", "c"]}
    report = _apply_display_priority(placeholder_map, {"provenance": {
        "display_priority": {"panels": [{"panel": TODAY, "order": [0, 1]}],
                             "notes": ["today: one index was out of range."]}}})
    placeholder_map["_panel_fit"] = _fit_slide_two_bullets(
        placeholder_map, [], report)
    placeholder_map["_bullet_order"] = report
    selection = bullet_selection(placeholder_map)
    assert selection["notes"] == ["today: one index was out of range."]
    assert len(selection["not_applied"]) == 1
    assert selection["ranked"] is False


def test_a_miscounted_tail_is_clamped_rather_than_trusted():
    """`appended` arrives from the mapping layer, so a wrong count must degrade
    safely rather than index out of the list or invert the check. Clamped to
    [0, len]: a negative count is no tail at all, which is the behaviour that
    predates `appended`, and an over-count leaves no ranked head for any order to
    be a permutation of, so the order is refused and the list stands."""
    from data_source_adapter import _apply_display_priority

    def apply(extra):
        placeholder_map = {TODAY_ROLE: ["a", "b", "c"]}
        report = _apply_display_priority(
            placeholder_map,
            {"provenance": {"display_priority": {
                "panels": [{"panel": TODAY, "order": [2, 1, 0]}]}}},
            appended={TODAY_ROLE: extra},
        )
        return placeholder_map[TODAY_ROLE], report

    # Clamped to zero: a full-length permutation, applied, as it was before.
    bullets, report = apply(-3)
    assert bullets == ["c", "b", "a"]
    assert report["not_applied"] == []

    # Clamped to the whole list: nothing was ranked, so nothing is applied — and
    # the refusal is recorded rather than swallowed.
    bullets, report = apply(99)
    assert bullets == ["a", "b", "c"]
    assert len(report["not_applied"]) == 1


def test_an_order_that_does_not_match_the_list_is_ignored():
    """A stale or malformed block leaves the list exactly as it arrived."""
    from data_source_adapter import _apply_display_priority

    bullets = ["a", "b", "c"]
    for order in ([0, 1], [0, 1, 5], [], [0, 0, 1], "nonsense"):
        placeholder_map = {TODAY_ROLE: list(bullets)}
        _apply_display_priority(placeholder_map, {"provenance": {
            "display_priority": {"panels": [{"panel": TODAY, "order": order}]}}})
        assert placeholder_map[TODAY_ROLE] == bullets, order


def test_a_packet_with_no_priority_block_is_left_alone():
    from data_source_adapter import _apply_display_priority

    placeholder_map = {TODAY_ROLE: ["a", "b", "c"]}
    for section8 in ({}, None, {"provenance": {}},
                     {"provenance": {"display_priority": None}}):
        _apply_display_priority(placeholder_map, section8)
        assert placeholder_map[TODAY_ROLE] == ["a", "b", "c"], section8


def test_ranking_happens_before_fitting_so_the_trim_takes_the_top():
    """The order the two steps run in is the point of having both: the fitter
    trims from the tail, so a rank applied afterwards would change nothing."""
    from data_source_adapter import (_apply_display_priority,
                                     _fit_slide_two_bullets)

    long_bullet = ("Maintenance and repairs are charged to cost of services as "
                   "incurred, so the line is not separately disclosed anywhere")
    bullets = [f"{n}. {long_bullet}" for n in range(6)]
    placeholder_map = {
        TODAY_ROLE: list(bullets),
        "opportunity_headline": "A headline that runs to a single line",
        "opportunity_summary": "A summary that runs to a single line as well.",
        "today_metric_1": "12.4% · Adjusted EBITDA margin",
        "today_metric_2": "",
    }
    _apply_display_priority(placeholder_map, {"provenance": {
        "display_priority": {"panels": [
            {"panel": TODAY, "order": [5, 4, 3, 2, 1, 0]}]}}})
    _fit_slide_two_bullets(placeholder_map, [{"label": "Phase 1",
                                              "summary": "Build and pilot."}])
    kept = placeholder_map[TODAY_ROLE]
    assert kept, kept
    assert len(kept) < len(bullets), "the fixture must actually overflow"
    # The last bullet was ranked first, so it survives the trim. Without the
    # rank running first it would have been the first one dropped.
    assert kept[0] == bullets[5], kept


def test_the_module_is_not_reachable_from_a_fixture_run():
    """A fixture packet carries no `display_priority`, so nothing is applied and
    no ranker is consulted."""
    from data_source_adapter import FixtureProvider, run_adapter

    packet = os.path.join(os.path.dirname(__file__), "..",
                          "proposal-data-packet-EXAMPLE.md")
    provider = FixtureProvider.from_packet_file(packet)
    result = run_adapter("Ridgeline Site Services",
                         "Operational Intelligence Platform",
                         provider, poll_interval=0.0, sleep=lambda _s: None)
    assert result["status"] == "ok", result
    assert _priority(result["packet"]) is None
