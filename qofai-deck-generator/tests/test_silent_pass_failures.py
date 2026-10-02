"""A failed model call must never present itself as thin source data.

The defect this file locks shut, root-caused on 2026-09-02. A live WTG run
returned `E_LOW_CONFIDENCE`, "The assembled packet scores 0.33 against a 0.7
floor", naming `baseline.revenue_ttm_usd`, `baseline.adjusted_ebitda_usd`,
`baseline.adjusted_ebitda_pct` and `commercial.scenarios[].margin_gain_pp` as
missing. The same opportunity returned `ok` twice the same day and rendered a
full six-slide deck once. It was not model variance: `data_completeness` is
`len(packet.present) / 6`, so 0.33 is exactly 2 of 6, and running the
deterministic parsers alone on that paper -- no model call at all -- produces
exactly 2 of 6 and exactly those four absences. The second extraction pass had
timed out and contributed nothing.

`second_pass.run` catches every exception out of the extractor by design and
records a reason per requested field, which is correct. The bug was that those
reasons reached nobody: `_gate` raises BEFORE `_document` is called, so the
packet document is never built, section 8 never exists, and
`second_pass.ledger(...)` is discarded. The reviewer was handed an envelope
saying the research paper was missing four figures the paper states in full, and
went to inspect the paper, which was the wrong place.

Three properties are held here, and each one is a separate way the same lie got
out:

  * the pass writes down that it FAILED, distinguishably from finding nothing;
  * it leaves a trace in the log, so the failure is visible to whoever is
    watching the process and not only to whoever reads the packet;
  * a gate failure that coincides with a failed pass says so in the envelope.

The degradation seam itself is not under attack and is asserted intact: a failed
nicety still must not sink a render, and the deterministic pass still wins.
"""

import logging

import httpx

import completeness_score
import second_pass
from live_proposal_provider import LiveProposalProvider
from packet_assembly import assemble
import pytest

from source_span import PAPER, Opportunity

# The opportunity a reading is FOR (item 15). The fakes below default to it the
# way they default to `PAPER`, so a test about the pass itself states one once
# rather than at every call.
OPPORTUNITY = Opportunity(id="OPP-ONE")

from test_live_seam import CLIENTS, STAMP, StubClient
from test_paper_extraction import answer, paper
from test_second_pass import extractor_returning


# The WTG shape, on committed data. `sparse-no-scenario-table` is the one
# committed excerpt whose deterministic packet lands BELOW the 0.70 floor -- 4 of
# 6, 0.667, the same "4 of 6 is not 5 of 6" quantisation that made the live run
# score 0.33 -- so the gate fires on it for the real reason rather than on a
# floor invented to force it. The second pass is exactly what would lift it, and
# a pass that fails is exactly what leaves it short.
SPARSE = dict(CLIENTS["one"], shape="sparse-no-scenario-table")
SPARSE_FLOOR = 0.70


def _request(spec, floor=SPARSE_FLOOR):
    return {"company": spec["company"]["name"],
            "project": spec["project"]["name"],
            "pe_firm": spec["pe_firm"],
            "proposal_date": "2026-08-15",
            "options": {"min_data_completeness": floor}}


def stalling(paper_text, requested, labels=(), document=PAPER,
             opportunity=OPPORTUNITY, description=""):
    """The observed failure, by its own class name. A stream that stalls inside
    `get_final_message()` raises this raw rather than as an SDK wrapper."""
    raise httpx.ReadTimeout("The read operation timed out")


def stalling_writer(paper_text, description, requested):
    raise httpx.ReadTimeout("The read operation timed out")


# --- the pass writes down that it failed ------------------------------------

def test_a_pass_that_failed_is_distinguishable_from_one_that_found_nothing():
    """The distinction the packet could not make before, and the one a reviewer
    needs: `dropped` carries both kinds of absence, so a consumer that wants only
    the failure asks `failed` rather than matching on a reason string."""
    text = paper("scenario-rows-canonical")
    packet = assemble("OPP-ONE", text)

    failed = second_pass.run(text, packet, {}, extractor=stalling)
    assert failed.failed is True
    assert "ReadTimeout" in failed.failure
    assert "timed out" in failed.failure

    found_nothing = second_pass.run(text, packet, {},
                                    extractor=extractor_returning(answer()))
    assert found_nothing.failed is False
    assert found_nothing.failure == ""


def test_the_failure_reason_is_recorded_against_every_field_it_was_asked_for():
    text = paper("scenario-rows-canonical")
    packet = assemble("OPP-ONE", text)
    result = second_pass.run(text, packet, {}, extractor=stalling)

    assert result.requested, "the fixture must leave the pass something to ask"
    reasons = dict(result.dropped)
    assert set(reasons) == set(result.requested)
    for path in result.requested:
        assert "did not complete" in reasons[path], path
        assert "ReadTimeout" in reasons[path], path


def test_the_deterministic_packet_still_stands_after_a_failed_pass():
    """The degradation seam, unchanged. A failed nicety must not sink a render."""
    text = paper("scenario-rows-canonical")
    packet = assemble("OPP-ONE", text)
    result = second_pass.run(text, packet, {}, extractor=stalling)

    assert result.packet is packet
    assert (completeness_score.data_completeness(result.packet)
            == completeness_score.data_completeness(packet))


def test_the_writing_pass_records_its_failure_the_same_way():
    result = second_pass.write("A paper.", "A description.",
                               writer=stalling_writer)
    assert result.failed is True
    assert "ReadTimeout" in result.failure
    assert result.sentences == {}, "a failed writer writes nothing"


# --- and leaves a trace where the process can be watched --------------------

def test_a_swallowed_extraction_failure_is_logged(caplog):
    """It left no trace anywhere before 2026-09-02: not the UI, not the server
    output. An operator watching a run that took ten minutes and produced a thin
    packet had nothing at all to read."""
    text = paper("scenario-rows-canonical")
    packet = assemble("OPP-ONE", text)
    with caplog.at_level(logging.ERROR, logger="deck.second_pass"):
        second_pass.run(text, packet, {}, extractor=stalling)
    assert "second extraction pass did not complete" in caplog.text
    assert "ReadTimeout" in caplog.text
    assert "ReadTimeout" in caplog.records[0].exc_text, "the traceback is the useful half"


def test_a_swallowed_writing_failure_is_logged(caplog):
    with caplog.at_level(logging.ERROR, logger="deck.second_pass"):
        second_pass.write("A paper.", "A description.", writer=stalling_writer)
    assert "writing pass did not complete" in caplog.text


def test_a_pass_that_simply_found_nothing_logs_nothing(caplog):
    """A log line on every clean run is a log line nobody reads."""
    text = paper("scenario-rows-canonical")
    packet = assemble("OPP-ONE", text)
    with caplog.at_level(logging.ERROR, logger="deck.second_pass"):
        second_pass.run(text, packet, {},
                        extractor=extractor_returning(answer()))
    assert caplog.text == ""


# --- and the gate error names it --------------------------------------------

def _envelope(spec=SPARSE, extractor=None, floor=SPARSE_FLOOR):
    provider = LiveProposalProvider(StubClient(spec), generated_at=STAMP,
                                    extractor=extractor)
    return provider.poll(provider.submit(_request(spec, floor)))["envelope"]


def test_a_gate_failure_after_a_failed_pass_names_the_failure():
    """The whole defect, at the seam it escaped from. Before this, the envelope
    said the packet scored below the floor and nothing else, so nothing
    distinguished 'absent because a model call failed' from 'absent because the
    paper does not state it'."""
    envelope = _envelope(extractor=stalling)
    assert envelope["status"] == "error"
    error = envelope["error"]
    assert error["code"] == "E_LOW_CONFIDENCE"
    assert "ReadTimeout" in error["message"]
    assert "did not complete" in error["message"]
    assert "ReadTimeout" in error["details"]["second_pass_failed"]
    # Re-running is the remediation, and it has to be said: the same paper has
    # cleared this floor on a run where the pass completed.
    assert "Re-run" in error["remediation"]


def test_the_gate_error_carries_the_passs_own_per_field_reasons():
    """`section 8`'s `not_filled` block, in the one place it can still be read
    when the document was never built."""
    envelope = _envelope(extractor=stalling)
    absent = envelope["error"]["details"]["absent_after_failed_pass"]
    assert absent, "the pass's own reasons must survive the gate"
    for entry in absent:
        assert entry["field"]
        assert "did not complete" in entry["reason"]


def test_the_envelope_still_names_the_failure_after_a_round_trip():
    """The property the repro script asserts: whatever a reviewer or a machine
    reads out of this envelope, the words are in it somewhere."""
    envelope = _envelope(extractor=stalling)
    blob = repr(envelope)
    assert any(word in blob for word in
               ("ReadTimeout", "timed out", "did not complete"))


def test_a_gate_failure_with_no_failed_pass_says_nothing_extra():
    """'The paper does not state it' is what the unadorned message already
    means, and a note about a pass that did not fail would be noise on every
    genuinely thin packet."""
    envelope = _envelope(extractor=extractor_returning(answer()))
    error = envelope["error"]
    assert error["code"] == "E_LOW_CONFIDENCE"
    assert "second_pass_failed" not in error["details"]
    assert "absent_after_failed_pass" not in error["details"]
    assert "did not complete" not in error["message"]


def test_a_gate_failure_with_no_second_pass_at_all_says_nothing_extra():
    envelope = _envelope(extractor=None)
    assert "second_pass_failed" not in envelope["error"]["details"]


def test_the_floor_and_the_code_do_not_move_for_a_failed_pass():
    """Naming the reason correctly is not softening the refusal. A packet short
    of the roster is still refused, with the same code and the same figure."""
    failed = _envelope(extractor=stalling)["error"]
    clean = _envelope(extractor=None)["error"]
    assert failed["code"] == clean["code"] == "E_LOW_CONFIDENCE"
    assert (failed["details"]["data_completeness"]
            == clean["details"]["data_completeness"])
    assert failed["details"]["missing_fields"] == clean["details"]["missing_fields"]


def test_a_failed_pass_does_not_by_itself_refuse_a_deck():
    """The seam again, from the provider's side: a packet that clears the floor
    on the deterministic parsers alone still renders when the pass fails."""
    envelope = _envelope(spec=CLIENTS["one"], extractor=stalling)
    assert envelope["status"] == "ok", envelope
    # And the packet it built says the pass failed, in its own ledger.
    assert "did not complete" in envelope["packet"]


# ---------------------------------------------------------------------------
# A LEG THAT BROKE ON A RUN THAT SUCCEEDED (2026-09-13).
#
# Until today a failed pass reached the reviewer only through the gate, so it
# was visible exactly when the deck was refused and invisible whenever the
# safety net held. A live two-opportunity run lost eleven fields to one
# truncated answer, had every one covered by the rescue and the parsers, scored
# 1.0 on both sections and reported ok, with nothing anywhere saying a leg had
# failed. That is worse than the refusal case rather than better: a run that is
# turned away gets looked at and a run that succeeds does not.
# ---------------------------------------------------------------------------

def test_a_successful_run_still_says_a_model_leg_failed():
    from test_base_document import (SCENARIOS_ONLY, request, two_opportunity_client,
                                    upload)

    spec, client = two_opportunity_client()
    provider = LiveProposalProvider(client, generated_at=STAMP,
                                    extractor=stalling)
    envelope = provider.poll(provider.submit(
        request(spec, floor=0.0, opportunity_ids=["OPP-ONE", "OPP-TWO"]),
        uploads=[upload(SCENARIOS_ONLY)],
    ))["envelope"]

    assert envelope["status"] == "ok", envelope
    marks = envelope["opportunities"]
    assert marks
    for mark in marks:
        assert mark["pass_failed"] is True
        assert mark["pass_failure"]
        assert mark["absent_after_failed_pass"]


def test_a_run_whose_legs_all_completed_says_nothing_about_a_failure():
    """The other half, so the flag means something. A pass that ran and simply
    found nothing is not a pass that broke, which is the distinction
    `SecondPass.failed` exists for."""
    from test_base_document import (SCENARIOS_ONLY, request, two_opportunity_client,
                                    upload)

    spec, client = two_opportunity_client()
    provider = LiveProposalProvider(client, generated_at=STAMP)
    envelope = provider.poll(provider.submit(
        request(spec, floor=0.0, opportunity_ids=["OPP-ONE", "OPP-TWO"]),
        uploads=[upload(SCENARIOS_ONLY)],
    ))["envelope"]

    assert envelope["status"] == "ok", envelope
    for mark in envelope["opportunities"]:
        assert mark["pass_failed"] is False
        assert mark["pass_failure"] == ""
        assert mark["absent_after_failed_pass"] == []


def test_the_refusal_says_which_bug_it_was_rather_than_only_that_there_was_one():
    """A truncated answer and a malformed one raise the same JSONDecodeError and
    are different bugs: one wants a bigger ceiling and the other wants a look at
    the request. So the record a failed pass keeps IS the measurement, rather
    than a prompt to go and take one on some later run."""
    import paper_extraction

    class _Truncated:
        stop_reason = "max_tokens"
        usage = type("_U", (), {"input_tokens": 9000, "output_tokens": 24000})()
        content = [type("_B", (), {"type": "text",
                                   "text": '{"fields": [{"path": "a'})()]

    with pytest.raises(paper_extraction.ExtractionError) as raised:
        paper_extraction._answer(_Truncated())

    message = str(raised.value)
    assert "stop_reason='max_tokens'" in message
    assert "output_tokens=24000" in message
    assert "body_chars=" in message


def test_the_diagnostic_survives_a_response_that_carries_none_of_it():
    """Every test in this repo injects a two-line stand-in with a `content` list
    and nothing else, and a diagnostic that crashed on the object it is
    diagnosing would be worse than no diagnostic."""
    import paper_extraction

    bare = type("_M", (), {"content": [
        type("_B", (), {"type": "text", "text": "not json"})()
    ]})()
    with pytest.raises(paper_extraction.ExtractionError) as raised:
        paper_extraction._answer(bare)
    assert "stop_reason=None" in str(raised.value)
