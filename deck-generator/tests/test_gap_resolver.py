"""Tests for the gap reader — reading a packet's declared gap flags.

Read-only, on packet TEXT in memory. There is no write side any more: the
packet-editing `resolve_gap` was removed on 2026-07-28 because the packet is the
data source's record and is never edited (reviewer confirmations live in
`gap_decisions`). `tests/test_packet_is_never_written.py` enforces that; the
resolve-and-rewrite cases that used to live here went with the function.

Exercises both the status packet (gaps in §5) and a proposal packet (gaps in §8,
with a trailing comment on the `gaps:` line).

Run with: python3 tests/test_gap_resolver.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from gap_resolver import deck_shape, describe_gap, list_gaps

_ROOT = os.path.join(os.path.dirname(__file__), "..")
STATUS_PACKET = os.path.join(_ROOT, "status-data-packet-EXAMPLE.md")
PROPOSAL_PACKET = os.path.join(_ROOT, "templates", "packets", "proposal-data-packet-fbk.md")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_list_gaps_status_packet():
    gaps = list_gaps(_read(STATUS_PACKET))
    fields = [g["field"] for g in gaps]
    assert fields == [
        "workstreams[0].after.metrics[0].value",
        "tracking.slip_or_buffer_markers[1]",
    ], fields
    # reasons come through, not just field paths
    assert all(g["reason"] for g in gaps), gaps


def test_list_gaps_proposal_packet():
    gaps = list_gaps(_read(PROPOSAL_PACKET))
    fields = [g["field"] for g in gaps]
    assert "baseline.baseline_locked_date" in fields, fields
    assert "commercial.qofai_investment_usd" in fields, fields


def test_list_gaps_none_declared():
    assert list_gaps("no yaml, no gaps here") == []


def test_describe_gap_workstream_metric_reads_in_plain_language():
    d = describe_gap("workstreams[2].after.metrics[0].value", "status")
    assert d["slide"] == "Workstream 3"
    assert "target / after panel" in d["where"]
    assert "1st metric" in d["where"]
    assert d["field"] == "workstreams[2].after.metrics[0].value"


def test_describe_gap_carries_the_slide_number():
    # A status deck runs cover, tracking, then one slide per workstream, so the
    # 3rd workstream is slide 5 — the number printed in the deck's own footer.
    d = describe_gap("workstreams[2].after.metrics[0].value", "status")
    assert d["slide_number"] == 5, d
    assert d["slide_label"] == "Slide 5: Workstream 3", d
    t = describe_gap("tracking.summary", "status")
    assert t["slide_number"] == 2 and t["slide_label"] == "Slide 2: Project Tracking", t


def test_describe_gap_closing_slide_number_needs_the_workstream_count():
    # Next Steps sits after the workstream slides, so its number depends on how
    # many there are: 3 workstreams -> slide 6.
    d = describe_gap("next_steps.columns[0]", "status", 3)
    assert d["slide_number"] == 6, d
    assert d["slide_label"] == "Slide 6: Next Steps", d
    # Without the count it degrades to the bare name, never a guessed number.
    bare = describe_gap("next_steps.columns[0]", "status")
    assert bare["slide_number"] is None, bare
    assert bare["slide_label"] == "Next Steps", bare


def test_describe_gap_recurring_field_carries_no_slide_number():
    # engagement fields print on EVERY slide, so a single number would send the
    # reviewer to the wrong one.
    d = describe_gap("engagement.project_week", "status")
    assert d["slide_number"] is None, d
    assert d["slide_label"] == d["slide"], d


def test_describe_gap_tracking_slip_marker():
    d = describe_gap("tracking.slip_or_buffer_markers[1]", "status")
    assert d["slide"] == "Project Tracking"
    assert d["slide_label"] == "Slide 2: Project Tracking"
    # No longer a printed callout strip — it annotates a bar on the timeline.
    assert d["where"] == "2nd buffer / slip annotation on the timeline", d


def test_describe_gap_proposal_commercial_field():
    d = describe_gap("commercial.qofai_investment_usd", "proposal")
    assert d["slide"] == "Commercial Terms"
    assert d["where"] == "QofAI investment"
    assert d["slide_label"] == "Slide 5: Commercial Terms", d


def test_describe_gap_unknown_path_degrades_gracefully():
    d = describe_gap("mystery_section.some_field", "status")
    # unknown head becomes a Title-cased slide; unknown leaf de-underscores
    assert d["slide"] == "Mystery Section"
    assert d["where"] == "some field"
    assert d["field"] == "mystery_section.some_field"
    # and carries no invented slide number
    assert d["slide_number"] is None, d
    assert d["slide_label"] == "Mystery Section", d


def test_deck_shape_reads_the_packet_counts():
    shape = deck_shape(_read(STATUS_PACKET))
    # The reference status packet carries 2 workstreams -> 5 slides.
    assert shape["n_workstreams"] == 2, shape
    assert shape["total_slides"] == 5, shape
    # A packet with no deck block yields Nones rather than raising.
    assert deck_shape("not a packet") == {"n_workstreams": None, "total_slides": None}


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
    if failures:
        print(f"\n{failures} test(s) failed")
        sys.exit(1)
    print(f"\nAll {len(tests)} tests passed")
