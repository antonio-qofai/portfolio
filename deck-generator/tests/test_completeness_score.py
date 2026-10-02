"""Tests for the completeness score (E7a, scoring half).

The expected scores below are the six-field roster divided by hand from the
`PRESENT_COUNTS` table in `tests/test_packet_assembly.py`, which was itself read
off the four parsers' coverage rather than off any module's output.

Two tests here are about what the score must NOT do. The score may not weight,
award partial credit, or reach back into what assembly counted as present, and
`test_the_score_is_a_pure_function_of_what_assembly_produced` plus
`test_no_field_is_worth_more_than_any_other` are what hold that.
"""

import pytest

from completeness_score import E_LOW_CONFIDENCE, data_completeness, gate
from data_source_adapter import DEFAULT_OPTIONS
from packet_assembly import ROSTER, assemble
from test_packet_assembly import ALL_FIXTURES, PRESENT_COUNTS, packet

CONTRACT_DEFAULT = DEFAULT_OPTIONS["min_data_completeness"]


class FakePacket:
    """A packet shape with a chosen `present` set, for the arithmetic tests."""

    def __init__(self, present):
        self.present = frozenset(present)


def test_the_contract_default_gate_is_the_one_the_score_is_read_against():
    assert CONTRACT_DEFAULT == 0.70


@pytest.mark.parametrize("shape", ALL_FIXTURES)
def test_the_score_is_present_over_the_roster(shape):
    assert data_completeness(packet(shape)) == pytest.approx(
        PRESENT_COUNTS[shape] / len(ROSTER)
    )


def test_a_complete_packet_scores_one_and_an_empty_one_scores_zero():
    assert data_completeness(FakePacket(ROSTER)) == 1.0
    assert data_completeness(FakePacket(())) == 0.0


def test_no_field_is_worth_more_than_any_other():
    """No weighting. Any one field is worth exactly one sixth of the score."""
    for field in ROSTER:
        assert data_completeness(FakePacket([field])) == pytest.approx(
            1 / len(ROSTER)
        )


def test_the_score_is_a_pure_function_of_what_assembly_produced():
    """Same `present` set, same score, whatever else the packet carries.

    The score cannot consult the fields, the spans, the reasons, or the number of
    scenario cases, because a second input is where partial credit gets in.
    """
    real = packet("no-timeline-chart")
    assert data_completeness(real) == data_completeness(FakePacket(real.present))


def test_scoring_a_packet_does_not_change_it():
    real = packet("sparse-no-scenario-table")
    before = (real.present, real.fields, real.missing_fields)
    data_completeness(real)
    gate(real, CONTRACT_DEFAULT)
    assert (real.present, real.fields, real.missing_fields) == before


@pytest.mark.parametrize("shape", ALL_FIXTURES)
def test_the_gate_returns_the_contract_code_below_the_bar_and_nothing_above(shape):
    assembled = packet(shape)
    verdict = gate(assembled, CONTRACT_DEFAULT)
    if data_completeness(assembled) < CONTRACT_DEFAULT:
        assert verdict == E_LOW_CONFIDENCE
    else:
        assert verdict is None


def test_the_one_fixture_below_the_bar_returns_e_low_confidence():
    """Four of six fields is 0.667, and a mixed result is the correct result."""
    assembled = packet("sparse-no-scenario-table")
    assert data_completeness(assembled) == pytest.approx(4 / 6)
    assert gate(assembled, CONTRACT_DEFAULT) == E_LOW_CONFIDENCE


def test_five_of_six_clears_the_bar():
    assembled = packet("no-timeline-chart")
    assert data_completeness(assembled) == pytest.approx(5 / 6)
    assert gate(assembled, CONTRACT_DEFAULT) is None


def test_the_threshold_is_the_callers_and_not_the_modules():
    """The gate holds no copy of 0.70, so a stricter request is stricter."""
    assembled = packet("no-timeline-chart")
    assert gate(assembled, 0.70) is None
    assert gate(assembled, 0.90) == E_LOW_CONFIDENCE


def test_a_hand_built_paper_with_two_fields_fails_the_gate():
    """The scenario-only paper from the assembly tests scores 2 of 6."""
    from test_packet_assembly import NO_BASELINE_PAPER

    assembled = assemble("hand-built", NO_BASELINE_PAPER)
    assert data_completeness(assembled) == pytest.approx(2 / 6)
    assert gate(assembled, CONTRACT_DEFAULT) == E_LOW_CONFIDENCE
