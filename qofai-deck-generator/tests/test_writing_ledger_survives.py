"""Item 24: the record of how the framing copy came out survives the run.

Two live renders in a row could not be diagnosed. Both times the run knew the
answer and discarded it: `second_pass.written_ledger` records, per slot, the line
that was written or the fact that it was not and the reason, the provider
serialises it into section 8, and the packet it lives in never leaves
`generate_and_save_deck` and is never written to disk (Antonio, 2026-07-28).

The concrete question this had to make answerable: on the 2026-09-16 Northwind render
`copy.plan_summary` and `copy.next_steps_summary` came back as their deck
standards where the day before they carried written lines. Either the model
declined the slot or it restated the standard and `restates_deck_standard`
refused it. Different problems, different fixes, and the ledger already tells
them apart.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ui"))

pytest.importorskip("flask")

import data_source_adapter  # noqa: E402
import packet_fill  # noqa: E402
import paper_writing  # noqa: E402
import second_pass  # noqa: E402

# Just enough packet for the transport half to read its own gates off. The
# mapping half is not what is under test here; the side channel beside the
# packet is.
PACKET = ('---\n'
          'confidence: "high"\n'
          'data_completeness: 0.95\n'
          'packet_type: "project_planning_proposal"\n'
          '---\n')

# The two outcomes that look identical on a slide and must not look identical
# here: a slot the model declined, and a slot whose answer restated the standard.
DECLINED = ("copy.plan_summary", "named no source section, and a generated "
                                 "sentence with no stated source is exactly "
                                 "what this pass refuses.")


def _writing(lines, dropped=()):
    return paper_writing.Writing(
        requested=tuple(lines) + tuple(path for path, _ in dropped),
        sentences={
            path: paper_writing.Written(path=path, text=text,
                                        sections=("The Platform",),
                                        evidence=("a quoted span",))
            for path, text in lines.items()
        },
        dropped=tuple(dropped),
    )


def test_the_ledger_tells_a_decline_from_a_restatement():
    """The distinction the whole item exists for. On the slide these are the
    same string, because `framed()` is `written.get(path) or constant`."""
    result = _writing(
        {"copy.next_steps_summary": packet_fill.NEXT_STEPS_SUMMARY,
         "copy.plan_headline": "From connectivity to AI sequencing"},
        dropped=(DECLINED,),
    )
    ledger = second_pass.written_ledger(result)
    refused = {row["field"]: row["reason"] for row in ledger["not_written"]}

    assert "restated the deck standard" in refused["copy.next_steps_summary"]
    assert "named no source section" in refused["copy.plan_summary"]
    assert [row["field"] for row in ledger["written"]] == ["copy.plan_headline"]


def test_the_reason_is_a_sentence_and_not_a_code():
    """A reviewer reading this needs no key. The refusal sentences are written
    for a person and are carried through verbatim."""
    result = _writing({}, dropped=(DECLINED,))
    reason = second_pass.written_ledger(result)["not_written"][0]["reason"]
    assert reason == DECLINED[1]
    assert " " in reason and not reason.isupper()


def test_the_adapter_carries_the_ledger_out_of_the_envelope():
    """Beside the packet, the way `provenance` and `attachments` already travel.
    Not re-parsed out of section 8: the provider serialised it a moment earlier,
    so reading it back out of the document would be reading back what we wrote."""
    envelope = {"status": "ok", "packet": PACKET, "request_echo": {},
                "writing_ledger": {"asked_for": ["copy.plan_summary"],
                                   "written": [],
                                   "not_written": [{"field": "copy.plan_summary",
                                                    "reason": "declined."}]}}

    class _Provider:
        def submit(self, request, uploads=()):
            return "h"

        def poll(self, handle):
            return {"done": True, "envelope": envelope}

    out = data_source_adapter.dispatch_and_gate(
        "Any Client", "Any Project", _Provider(), poll_interval=0.0,
        sleep=lambda _s: None, deck_title="T")
    assert out["writing_ledger"]["not_written"][0]["field"] == "copy.plan_summary"


def test_a_run_with_no_writing_pass_carries_no_ledger():
    """Quiet rather than empty. A fixture run ran no pass, and a card claiming
    an empty one would be a different statement from saying nothing."""
    envelope = {"status": "ok", "packet": PACKET, "request_echo": {}}

    class _Provider:
        def submit(self, request, uploads=()):
            return "h"

        def poll(self, handle):
            return {"done": True, "envelope": envelope}

    out = data_source_adapter.dispatch_and_gate(
        "Any Client", "Any Project", _Provider(), poll_interval=0.0,
        sleep=lambda _s: None, deck_title="T")
    assert out["writing_ledger"] is None


def test_the_studio_renders_the_refusals_and_says_why():
    """The reviewer-facing end. This is where someone looks when a line reads
    generic, so the slot and the reason both have to be on screen."""
    import app as ui_app

    ctx = ui_app._build_result_ctx(
        result={"status": "ok",
                "writing_ledger": {
                    "asked_for": ["copy.plan_summary"],
                    "written": [{"field": "copy.plan_headline",
                                 "kind": "GENERATED, not quoted",
                                 "text": "From connectivity to AI sequencing",
                                 "written_from": ["Implementation"],
                                 "restating": ["a span"]}],
                    "not_written": [{"field": "copy.plan_summary",
                                     "reason": "restated the deck standard it "
                                               "was asked to replace."}]}},
        status="ok", deck_type="proposal", company="Any Client")

    assert ctx["writing_ledger"]["not_written"][0]["field"] == "copy.plan_summary"
    # The card was removed on 2026-09-20 ("I don't think we need the how the
    # framing lines came out box either"). The ledger is still assembled and
    # still rides into the saved deck row; it simply has no display.
    assert "writing_ledger" not in ui_app.RESULT_PANEL
    assert "How the framing lines came out" not in ui_app.RESULT_PANEL


def test_the_ledger_never_reaches_a_role_a_prompt_or_a_deck():
    """The same rule `provenance` and `attachments` run on: a record about the
    run is not content. Held here so the card cannot drift into the deck."""
    import app as ui_app

    assert "writing_ledger" not in ui_app.GENERATE_PANEL
    for source in ("deck_renderer", "prompt_assembler", "packet_fill"):
        module = __import__(source)
        assert "writing_ledger" not in open(module.__file__).read(), (
            f"{source} must not learn about the ledger")


def test_a_slot_the_model_never_answered_is_reported_rather_than_silent():
    """The fourth state, and it was invisible until this item.

    `read_response` records what the model RETURNED: a written line, or one it
    returned that failed verification. A slot it simply omitted appeared in
    neither list, so the ledger said nothing about it at all. That is one of the
    two candidates in the 2026-09-16 defect, so a ledger that stayed silent
    about it would have answered the question it exists to answer with a silence
    that reads like no problem.
    """
    result = _writing({"copy.plan_headline": "From connectivity to sequencing"})
    result = paper_writing.Writing(
        requested=("copy.plan_headline", "copy.plan_summary"),
        sentences=result.sentences,
        dropped=(),
    )
    ledger = second_pass.written_ledger(result)
    refused = {row["field"]: row["reason"] for row in ledger["not_written"]}

    assert "copy.plan_summary" in refused
    assert "returned nothing for it" in refused["copy.plan_summary"]
    # And it is not double-counted against a slot that WAS answered.
    assert "copy.plan_headline" not in refused


def test_the_three_refusal_states_stay_distinguishable():
    """Declined, returned-and-rejected, and restated-the-standard are three
    different problems with three different fixes, and the whole value of this
    record is that it does not collapse them."""
    result = paper_writing.Writing(
        requested=("copy.plan_summary", "copy.platform_summary",
                   "copy.next_steps_summary", "copy.plan_headline"),
        sentences={
            "copy.next_steps_summary": paper_writing.Written(
                path="copy.next_steps_summary",
                text=packet_fill.NEXT_STEPS_SUMMARY,
                sections=("S",), evidence=("e",)),
            "copy.plan_headline": paper_writing.Written(
                path="copy.plan_headline", text="A real headline",
                sections=("S",), evidence=("e",)),
        },
        dropped=(("copy.platform_summary",
                  "runs 240 characters against a 200 limit for this line."),),
    )
    refused = {row["field"]: row["reason"]
               for row in second_pass.written_ledger(result)["not_written"]}

    assert "returned nothing for it" in refused["copy.plan_summary"]
    assert "against a 200 limit" in refused["copy.platform_summary"]
    assert "restated the deck standard" in refused["copy.next_steps_summary"]
    assert len(refused) == 3


def test_the_ledger_survives_a_save_and_a_reopen(tmp_path, monkeypatch):
    """"Survives the process" is the whole of this item. A run that ends takes
    its answer with it unless the store keeps it, and a deck reopened months
    later is exactly when "why does this headline read like a template" gets
    asked."""
    monkeypatch.setenv("DECK_STORE_DIR", str(tmp_path))
    from deck_store import get_deck, list_decks, save_deck

    ledger = {"asked_for": ["copy.plan_summary"], "written": [],
              "not_written": [{"field": "copy.plan_summary",
                               "reason": "the pass was asked for this line and "
                                         "returned nothing for it."}]}
    save_deck("proposal", "Any Client", "Any Project", "<html></html>",
              is_final=True,
              details={"attachments": [], "writing_ledger": ledger})
    saved = get_deck(list_decks()[0]["id"])["details"]["writing_ledger"]
    assert saved == ledger

    # A row saved before this key existed reads as no ledger and renders
    # nothing, exactly as `attachments` did when it was added.
    save_deck("proposal", "Older Client", "Older", "<html></html>",
              is_final=True, details={"attachments": []})
    assert get_deck(list_decks()[0]["id"])["details"].get("writing_ledger") is None
