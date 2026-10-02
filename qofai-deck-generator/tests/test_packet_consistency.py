"""Tests for the packet-consistency guard — a packet may not contradict itself.

The regression these pin down: a status deck rendered with a footer reading
"Week 5 of 16" over a Gantt whose dated columns stopped at W10. Every existing
guard passed, because both halves of the contradiction were carried faithfully.

Operates on packet TEXT in memory: each test takes a known-clean packet and
mutates one field, so a check is proven to fire on the contradiction rather than
on some incidental property of a hand-written fixture. Both shipped status
packets must be clean as-is.

Run with: python3 tests/test_packet_consistency.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from packet_consistency import (
    ConsistencyError,
    check_consistency,
    check_status_consistency,
)

_ROOT = os.path.join(os.path.dirname(__file__), "..")
STATUS_PACKET = os.path.join(_ROOT, "status-data-packet-EXAMPLE.md")
STATUS_PACKET_2 = os.path.join(_ROOT, "templates", "packets", "status-data-packet-second.md")
PROPOSAL_PACKET = os.path.join(_ROOT, "templates", "packets", "proposal-data-packet-fbk.md")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _expect_error(packet_text, *fragments):
    """Assert the packet trips the guard, and that the message names what broke."""
    try:
        check_status_consistency(packet_text)
    except ConsistencyError as exc:
        message = str(exc)
        for fragment in fragments:
            assert fragment in message, f"{fragment!r} not in:\n{message}"
        return message
    raise AssertionError("expected ConsistencyError, packet passed")


# --- the shipped packets are coherent --------------------------------------

def test_reference_status_packet_is_consistent():
    assert check_status_consistency(_read(STATUS_PACKET)) == []


def test_second_status_packet_is_consistent():
    # The packet that produced the bad deck, after its timeline was extended to
    # cover the 16-week plan it declares.
    assert check_status_consistency(_read(STATUS_PACKET_2)) == []


def test_proposal_packet_has_no_checks_rather_than_invented_ones():
    assert check_consistency(_read(PROPOSAL_PACKET), deck_type="proposal") == []


def test_unknown_deck_type_is_a_caller_error():
    try:
        check_consistency("", deck_type="pitch")
    except ValueError as exc:
        assert "pitch" in str(exc)
        return
    raise AssertionError("expected ValueError for an unknown deck_type")


# --- the reported defect ---------------------------------------------------

def test_timeline_shorter_than_the_plan_is_caught():
    # THE regression: 14 dated columns, but the plan re-baselined to 20 weeks.
    text = _read(STATUS_PACKET).replace(
        "project_week_m: 12", "project_week_m: 20"
    ).replace("Week 3 of 12", "Week 3 of 20").replace(
        "WEEK 3 OF 12", "WEEK 3 OF 20"
    )
    _expect_error(text, "columns stop at week 14", "plan runs 20 weeks")


def test_timeline_running_past_the_plan_is_allowed():
    # The contract explicitly permits columns beyond project_week_m (buffer or
    # phases past the reporting window) — the reference packet is exactly this
    # case: 14 columns for a 12-week plan. Only stopping SHORT is a defect.
    assert check_status_consistency(_read(STATUS_PACKET)) == []


# --- the neighbouring invariants -------------------------------------------

def test_today_marker_off_the_grid_is_caught():
    text = _read(STATUS_PACKET).replace('on_week: "W3"', 'on_week: "W99"')
    _expect_error(text, "'W99'", "not one of the dated columns")


def test_today_marker_on_the_wrong_week_is_caught():
    # Marker moved to W7 while the check-in is still declared as week 3, so the
    # TODAY line and the footer would disagree.
    text = _read(STATUS_PACKET).replace('on_week: "W3"', 'on_week: "W7"')
    _expect_error(text, "plan week 7", "week 3")


def test_bar_on_an_undated_week_is_caught():
    text = _read(STATUS_PACKET).replace('start_week: "W1"', 'start_week: "W40"', 1)
    _expect_error(text, "W40", "cannot be placed on the grid")


def test_backwards_bar_is_caught():
    text = _read(STATUS_PACKET).replace(
        'start_week: "W1", end_week: "W2"', 'start_week: "W2", end_week: "W1"', 1
    )
    _expect_error(text, "runs backwards")


def test_stale_week_label_is_caught():
    # A re-baselined plan that left the prose "Week N of M" behind.
    text = _read(STATUS_PACKET).replace('project_week: "Week 3 of 12"',
                                        'project_week: "Week 4 of 12"')
    _expect_error(text, "engagement.project_week", "week 4 of 12", "week 3 of 12")


def test_stale_tracking_eyebrow_is_caught():
    text = _read(STATUS_PACKET).replace("WEEK 3 OF 12", "WEEK 3 OF 14")
    _expect_error(text, "tracking.section_label")


def test_wrong_total_slides_is_caught():
    text = _read(STATUS_PACKET).replace("total_slides: 5", "total_slides: 6")
    _expect_error(text, "deck.total_slides says 6", "wrong denominator")


def test_wrong_workstream_count_is_caught():
    text = _read(STATUS_PACKET).replace("n_workstreams: 2", "n_workstreams: 3")
    _expect_error(text, "deck.n_workstreams says 3", "carries 2")


def test_every_problem_is_reported_at_once():
    # A re-baseline usually breaks several invariants together; the reviewer
    # should get the whole list in one pass, not one per run.
    text = (
        _read(STATUS_PACKET)
        .replace("project_week_m: 12", "project_week_m: 20")
        .replace("total_slides: 5", "total_slides: 9")
    )
    message = _expect_error(text, "columns stop at week 14", "deck.total_slides says 9")
    assert message.count("  - ") >= 3, message  # timeline + label mismatches + slides


def test_missing_fields_do_not_crash_the_guard():
    # A packet with no tracking section at all must not raise a TypeError; the
    # gates and the coverage guard are what speak to missing data.
    assert check_status_consistency("## 1 · Deck\n\n```yaml\ndeck: {}\n```\n") == []


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
