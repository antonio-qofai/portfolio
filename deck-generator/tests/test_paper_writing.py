"""Tests for the GENERATED half of the second pass (E11 Stage 2c).

Everything here runs against an INJECTED FAKE client, like every other model
test in this suite: `paper_writing.write` takes a `client` and
`second_pass.write` takes the whole pass as a callable, so the network is
something a caller hands over rather than something a default reaches for.

What is asserted is the verification half, and it is asserted the same way
`test_paper_extraction` asserts its own: by handing the verifier sentences that
are WRONG in each of the specific ways a writing model can be wrong -- a section
that is not in the paper, evidence quoted from somewhere else, a number nobody
stated, a name nobody used, a line too long to render, a path nobody asked for
-- and showing that none of them reaches a deck. The happy path is one test; the
refusals are the module.

The distinction this file exists to hold: an EXTRACTED value is a quotation and
carries a span; a GENERATED sentence is one the model wrote and cannot. The
generated rule is not weaker, it is different, and every part of it that can be
checked mechanically is checked here.
"""

import json

import pytest

import paper_writing
from paper_writing import (DESCRIPTION_SECTION, Writing, WritingError,
                           read_response, resolve_section, write)
from test_paper_extraction import platform_paper

DESCRIPTION = (
    "A field service business running dispatch off a whiteboard and billing off "
    "paper tickets. The engagement builds the capture, dispatch and reporting "
    "layers that close the gap between the two."
)


class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Message:
    def __init__(self, text):
        self.content = [_Block(text)]


class FakeClient:
    """A stand-in for `anthropic.Anthropic`, recording what it was asked."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    @property
    def messages(self):
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _Message(json.dumps(self.answer))


def answer(*sentences):
    return {"sentences": list(sentences)}


def sentence(path, text, sections, evidence):
    return {"path": path, "text": text, "sections": list(sections),
            "evidence": list(evidence)}


def read(payload, requested, paper=None, description=DESCRIPTION):
    paper = platform_paper() if paper is None else paper
    return read_response(paper, description, requested, _Message(json.dumps(payload)))


PLATFORM_EVIDENCE = (
    "A mobile ticket capture app records the work at the job site, so the "
    "ticket\nleaves with the crew rather than arriving days later."
)
STORE_EVIDENCE = (
    "An operations data store joins the captured tickets to the financials, so\n"
    "job-level margin is readable the day a job closes."
)


# ---------------------------------------------------------------------------
# The happy path, once.
# ---------------------------------------------------------------------------

def test_a_sentence_written_from_real_sections_and_real_evidence_is_kept():
    """The whole contract in one case: it names sections that exist, quotes
    evidence from inside them, states no number and no name they do not, and
    fits the line it is written for."""
    result = read(answer(sentence(
        "copy.platform_headline",
        "One place to capture the work, dispatch it and see what it earned.",
        ["The Proposed Solution", "Technical Requirements"],
        [PLATFORM_EVIDENCE, STORE_EVIDENCE],
    )), ["copy.platform_headline"])

    written = result.sentences["copy.platform_headline"]
    assert written.text.startswith("One place to capture the work")
    assert written.sections == ("The Proposed Solution", "Technical Requirements")
    assert result.dropped == ()
    assert result.paths == ("copy.platform_headline",)


# ---------------------------------------------------------------------------
# The refusals. Each is a way a writing model can be wrong.
# ---------------------------------------------------------------------------

def test_a_section_the_paper_does_not_have_discards_the_sentence():
    """Rule one, and the whole rule: no source sections, no sentence. A named
    section that is not a heading in this paper is not a source it can have been
    written from."""
    result = read(answer(sentence(
        "copy.platform_headline", "A platform for the work.",
        ["Commercial Terms"], [PLATFORM_EVIDENCE],
    )), ["copy.platform_headline"])

    assert result.sentences == {}
    assert "not a section of this source" in dict(result.dropped)["copy.platform_headline"]


def test_evidence_quoted_from_outside_the_named_sections_discards_it():
    """Naming a section and then restating a different one is the failure the
    section-naming rule would otherwise invite. The evidence has to sit inside
    the region the named heading actually covers."""
    result = read(answer(sentence(
        "copy.platform_headline", "A platform for the work.",
        ["Technical Requirements"], [PLATFORM_EVIDENCE],
    )), ["copy.platform_headline"])

    assert result.sentences == {}
    assert "outside the sections it named" in dict(result.dropped)["copy.platform_headline"]


def test_evidence_that_is_not_in_the_paper_at_all_discards_it():
    """The same literal check the extraction half applies to a span. Evidence is
    quoted, never retyped from memory."""
    result = read(answer(sentence(
        "copy.platform_headline", "A platform for the work.",
        ["The Proposed Solution"],
        ["A mobile ticket capture app records the work at every job site."],
    )), ["copy.platform_headline"])

    assert result.sentences == {}
    assert "does not occur in the source" in dict(result.dropped)["copy.platform_headline"]


def test_a_number_the_evidence_does_not_state_discards_the_whole_sentence():
    """The rule that keeps a generated sentence from becoming an unverified
    figure. A number in a framing line is held to the same span check as a
    number anywhere else on the deck, and the sentence goes with it."""
    result = read(answer(sentence(
        "copy.platform_headline", "One platform, 3 layers, one live picture.",
        ["The Proposed Solution"], [PLATFORM_EVIDENCE],
    )), ["copy.platform_headline"])

    assert result.sentences == {}
    reason = dict(result.dropped)["copy.platform_headline"]
    assert "'3'" in reason and "same span check" in reason


def test_a_number_the_evidence_does_state_is_allowed_through():
    """The other direction, so the number rule is a check rather than a ban. A
    figure quoted in the evidence may appear in the sentence."""
    evidence = "| Conservative | $0.9M | 1.1pp |"
    assert evidence in platform_paper()
    result = read(answer(sentence(
        "copy.platform_headline", "Margin impact from 1.1pp, case by case.",
        ["Financial Analysis > Projected Impact"], [evidence],
    )), ["copy.platform_headline"])

    assert result.sentences["copy.platform_headline"].text.endswith("case by case.")

    # And a figure the paper states SOMEWHERE ELSE is still refused, so the rule
    # is about this sentence's own evidence rather than about the paper at large.
    elsewhere = read(answer(sentence(
        "copy.platform_headline", "Margin impact from 3.6pp, case by case.",
        ["The Proposed Solution"], [PLATFORM_EVIDENCE],
    )), ["copy.platform_headline"])
    assert elsewhere.sentences == {}
    assert "3.6" in dict(elsewhere.dropped)["copy.platform_headline"]


def test_a_name_the_evidence_does_not_use_discards_the_sentence():
    """The fabrication surface a restatement check can actually reach. An
    invented product, party, place or system arrives capitalised, and a
    capitalised word that is not opening a sentence has to be in the evidence."""
    result = read(answer(sentence(
        "copy.platform_headline", "One platform, built on Snowflake.",
        ["The Proposed Solution"], [PLATFORM_EVIDENCE],
    )), ["copy.platform_headline"])

    assert result.sentences == {}
    reason = dict(result.dropped)["copy.platform_headline"]
    assert "Snowflake" in reason and "restatement" in reason


def test_an_ordinary_capitalised_first_word_is_not_treated_as_a_name():
    """The check has to be usable. A sentence's own first word, and the first
    word after a full stop, are capitalised because they open a sentence and are
    not claims about anything."""
    result = read(answer(sentence(
        "copy.platform_summary",
        "Tickets leave with the crew. Dispatch reads one live board.",
        ["The Proposed Solution"], [PLATFORM_EVIDENCE],
    )), ["copy.platform_summary"])

    assert result.sentences["copy.platform_summary"].text.startswith("Tickets")


def test_a_line_too_long_for_its_slot_is_refused_rather_than_cut():
    """A headline that wraps is a layout failure the deck cannot absorb, and
    truncating one would be this pass editing its own answer. It is refused and
    the deck standard stands."""
    long_line = ("One place to capture the work at the job site and dispatch it "
                 "and bill it and see exactly what every job earned the day it "
                 "closed rather than at the month end.")
    result = read(answer(sentence(
        "copy.platform_headline", long_line,
        ["The Proposed Solution"], [PLATFORM_EVIDENCE],
    )), ["copy.platform_headline"])

    assert result.sentences == {}
    assert "limit for this line" in dict(result.dropped)["copy.platform_headline"]


def test_a_sentence_with_no_evidence_at_all_is_refused():
    """Naming a section without quoting from it says nothing checkable."""
    result = read(answer(sentence(
        "copy.platform_headline", "A platform for the work.",
        ["The Proposed Solution"], [],
    )), ["copy.platform_headline"])

    assert "quoted no evidence" in dict(result.dropped)["copy.platform_headline"]


def test_a_sentence_naming_no_section_is_refused():
    """Rule one, from the other side."""
    result = read(answer(sentence(
        "copy.platform_headline", "A platform for the work.", [],
        [PLATFORM_EVIDENCE],
    )), ["copy.platform_headline"])

    assert "named no source section" in dict(result.dropped)["copy.platform_headline"]


def test_a_line_nobody_asked_for_is_dropped_with_its_reason():
    """The writing pass fills absences only, the same way the extraction pass
    does, and a line it was not asked for is discarded rather than merged."""
    result = read(answer(sentence(
        "copy.plan_headline", "Three phases, one at a time.",
        ["Implementation Approach > Timeline"],
        ["| **Phase 1: Capture** | Months 1-4 | Ship the mobile ticket app to one branch |"],
    )), ["copy.platform_headline"])

    assert result.sentences == {}
    assert "fills absences only" in dict(result.dropped)["copy.plan_headline"]


def test_a_path_that_is_not_a_copy_role_at_all_is_dropped():
    result = read(answer(sentence(
        "copy.terms_headline", "How the value is shared.",
        ["The Proposed Solution"], [PLATFORM_EVIDENCE],
    )), ["copy.platform_headline"])

    assert "not a generated copy path" in dict(result.dropped)["copy.terms_headline"]


def test_two_sentences_for_one_line_keep_the_first_and_report_the_rest():
    """The deck has one headline per slide."""
    result = read(answer(
        sentence("copy.platform_headline", "One live picture of the work.",
                 ["The Proposed Solution"], [PLATFORM_EVIDENCE]),
        sentence("copy.platform_headline", "A second try at the same line.",
                 ["The Proposed Solution"], [PLATFORM_EVIDENCE]),
    ), ["copy.platform_headline"])

    assert result.sentences["copy.platform_headline"].text == (
        "One live picture of the work."
    )
    assert "more than one" in dict(result.dropped)["copy.platform_headline"]


# ---------------------------------------------------------------------------
# The description source, which has no headings.
# ---------------------------------------------------------------------------

def test_a_cover_subtitle_is_written_from_the_description_not_the_paper():
    """Slide 1's subtitle compresses the opportunity description, which the
    packet parks verbatim in section 3. The description carries no headings, so
    it is one region under a reserved name and the evidence check runs against
    it exactly as it runs against a paper section."""
    result = read(answer(sentence(
        "copy.subtitle", "Closing the gap between dispatch and billing.",
        [DESCRIPTION_SECTION],
        ["the capture, dispatch and reporting "
         "layers that close the gap between the two."],
    )), ["copy.subtitle"])

    assert result.sentences["copy.subtitle"].text.startswith("Closing the gap")


def test_a_description_line_may_not_be_written_from_a_paper_section():
    """The source a slot declares is the source it is checked against. A
    subtitle claiming a paper heading has named a section its own source does
    not have."""
    result = read(answer(sentence(
        "copy.subtitle", "One platform for the work.",
        ["The Proposed Solution"], [PLATFORM_EVIDENCE],
    )), ["copy.subtitle"])

    assert result.sentences == {}
    assert "not a section of this source" in dict(result.dropped)["copy.subtitle"]


# ---------------------------------------------------------------------------
# The call itself, and what it never does.
# ---------------------------------------------------------------------------

def test_both_sources_go_over_whole_and_are_marked_for_caching():
    """Section-picking would be picking the answer, so both sources cross whole
    in one cached block."""
    client = FakeClient(answer())
    write(platform_paper(), DESCRIPTION, ["copy.platform_headline"], client=client)

    system = client.calls[0]["system"]
    assert system[0]["text"] == paper_writing.SYSTEM_PROMPT
    assert platform_paper() in system[1]["text"]
    assert DESCRIPTION in system[1]["text"]
    assert system[1]["cache_control"] == {"type": "ephemeral"}
    assert client.calls[0]["model"] == paper_writing.DEFAULT_MODEL


def test_nothing_is_called_when_nothing_is_asked_for():
    """A run with no framing line to write costs nothing."""
    client = FakeClient(answer())
    result = write(platform_paper(), DESCRIPTION, [], client=client)

    assert client.calls == []
    assert isinstance(result, Writing) and result.sentences == {}


def test_nothing_is_called_when_there_is_no_source_to_write_from():
    client = FakeClient(answer())
    result = write("", "", ["copy.platform_headline"], client=client)

    assert client.calls == []
    assert result.sentences == {}


def test_an_unreadable_answer_raises_rather_than_being_guessed_at():
    """Guessing at half an answer would be exactly the invention this pass
    forbids."""
    with pytest.raises(WritingError):
        read_response(platform_paper(), DESCRIPTION, ["copy.platform_headline"],
                      _Message("not json at all"))


def test_the_plan_summary_is_a_writable_line_read_from_the_paper():
    """Added 2026-09-03, closing the one framing role that had no source. It is
    read from the PAPER (not the opportunity description), because the sections
    that describe the implementation are where a plan's shape is stated, and its
    guidance names the one thing it must not do: restate the per-phase detail
    that slide 2's build strip and slide 4's own Gantt already carry."""
    assert "copy.plan_summary" in paper_writing.PATHS
    slot = paper_writing.BY_PATH["copy.plan_summary"]
    assert slot.role == "plan_summary"
    assert slot.source == paper_writing.PAPER
    assert 0 < slot.limit <= 240
    assert "must NOT restate per-phase detail" in slot.guidance
    assert "run-on" in slot.guidance


def test_the_terms_framing_is_not_a_writable_line_at_all():
    """The one place on the deck where the deck standard is the designed answer.
    QofAI's commercial terms are founder-set, no paper carries them, and the
    five commercial fields are built to read AWAITING COMMERCIAL TERMS INPUT;
    engagement-specific framing around a deliberately blank slide is the one
    thing that slide must not have. It is not in scope, so it cannot be asked
    for and cannot be returned."""
    assert "copy.terms_headline" not in paper_writing.PATHS
    assert "copy.terms_summary" not in paper_writing.PATHS


def test_a_section_path_resolves_under_its_own_parent():
    """`Financial Analysis > Current State` is the region under that heading and
    not the whole of `Financial Analysis`, so evidence from a sibling section
    does not pass as evidence from this one."""
    text = platform_paper()
    start, end = resolve_section(text, "Financial Analysis > Current State")
    assert "The company reported LTM revenue" in text[start:end]
    assert "Billing lag falls from nine days" not in text[start:end]


# ---------------------------------------------------------------------------
# PART ONE ITEM 8: descriptor altitude.
#
# Casey, [14:58] of the 2026-08-20 demo: the descriptor text is out of control
# and running a verbal assault on everything it describes. Measured on the
# 2026-09-15 live Northwind deck, NO limit binds: subtitle 90 of 160,
# opportunity_summary 182 of 240, platform_summary 168 of 200, next_steps_summary
# 148 of 160. The complaint is about altitude, not character count, so the fix is
# guidance and the limits stay where they are.
# ---------------------------------------------------------------------------

# The limits, pinned so no limit moves without someone saying why here.
#
# THE THREE HEADLINES MOVED ON 2026-09-19, ITEM 27, and the guard above still
# holds for everything else. Its concern is that "fix the descriptors" gets
# answered by quietly lowering a cap, which would truncate a line that already
# fits instead of raising where it sits. That is not what happened. The headline
# caps came down on Antonio's direction of 2026-09-18 for short titles, measured
# against the five headlines the build actually produced (61, 62, 68, 75, 79
# against 90), where the cap had never once bound and the two longest were
# three-clause lists.
#
# THE SUMMARY CAPS DID NOT MOVE, WHICH IS THE GUARD DOING ITS JOB. The two lines
# that overshot on the 2026-09-18 TIG render were `platform_summary` at 207 and
# `plan_summary` at 210, both against 200. Neither cap was raised to admit them
# and neither was lowered. Their guidance was trimmed instead, because a line
# asked for three things comes back long.
MEASURED_LIMITS = {
    "copy.subtitle": 160,
    "copy.opportunity_summary": 240,
    "copy.platform_headline": 70,
    "copy.platform_summary": 200,
    "copy.plan_headline": 70,
    "copy.plan_summary": 200,
    "copy.next_steps_headline": 70,
    "copy.next_steps_summary": 160,
}

# What the two 200-cap summaries measured when they were refused. Pinned so a
# later reader can see that neither cap was moved to make them fit.
OVERSHOT_2026_09_18 = {"copy.platform_summary": 207, "copy.plan_summary": 210}


def test_no_limit_moves_without_a_reason_recorded_here():
    assert {slot.path: slot.limit
            for slot in paper_writing.WRITTEN_SCOPE} == MEASURED_LIMITS


def test_the_overshooting_summaries_were_not_given_a_bigger_cap():
    """Antonio's instruction was shorter lines, not permission for longer ones.
    The fix for these two was their guidance; the cap must still refuse them."""
    limits = {slot.path: slot.limit for slot in paper_writing.WRITTEN_SCOPE}
    for path, measured in OVERSHOT_2026_09_18.items():
        assert limits[path] == 200, path
        assert measured > limits[path], (
            f"{path} would now be accepted at the length that was refused")


def test_every_slot_says_what_its_line_must_not_do():
    """The shape `plan_summary` demonstrated on 2026-09-03 and the rest of the
    table copied on 2026-09-16: a slot that only describes what to write gets a
    line that restates whatever the slide already shows, because the model
    cannot see the slide. Each one names its own failure."""
    prohibitions = ("must not", "must NOT", "never", "Never", "No figure",
                    "Do not", "not worth having")
    for slot in paper_writing.WRITTEN_SCOPE:
        assert any(word in slot.guidance for word in prohibitions), (
            f"{slot.path} says what to write but not what not to write")


def test_every_summary_names_what_sits_around_it_on_the_slide():
    """The specific altitude failure: a summary that restates the structured
    content directly beneath it. The model never sees the slide, so the guidance
    is the only place that geometry can come from."""
    for slot in paper_writing.WRITTEN_SCOPE:
        if not slot.path.endswith("_summary") and slot.path != "copy.subtitle":
            continue
        assert any(word in slot.guidance
                   for word in ("sits above", "sits directly above", "above",
                                "below", "under")), (
            f"{slot.path} does not say what it sits above or below")


# ---------------------------------------------------------------------------
# ITEM 27: ask each line for one thing.
#
# The cap half is provable and is proved here, against the headlines the build
# really wrote rather than against invented strings. The GUIDANCE half is not
# provable offline at all: whether a trimmed brief produces a shorter line is
# only visible in what the model returns, and nothing below should be read as
# evidence that it does. One live render on the TIG PRD answers that, with item
# 24's ledger showing whether the two 200-cap slots clear. Antonio schedules it.
# ---------------------------------------------------------------------------

# Every written headline across the two live decks of 2026-09-18, with the shape
# Antonio objected to marked. These are the measurement the cap was picked on.
WRITTEN_HEADLINES = (
    ("A pricing layer on top of Quotebase, not a replacement for it", "one idea"),
    ("A scoping sprint to turn an indicative range into a firm price", "one idea"),
    ("Bridging Planwright and NetSuite, then adding an AI scheduling layer",
     "one idea"),
    ("Price the options, test the data, then put visibility in front of the plant",
     "three-clause list"),
    ("From forward visibility, to optimized sequencing, to forecast-driven "
     "scheduling", "three-clause list"),
)


def _headline_slots():
    return [s for s in paper_writing.WRITTEN_SCOPE if s.path.endswith("_headline")]


def test_every_headline_shares_the_one_cap_so_it_moves_in_one_place():
    assert {s.limit for s in _headline_slots()} == {paper_writing.HEADLINE_LIMIT}


def test_the_new_cap_refuses_the_three_clause_headlines_and_keeps_the_others():
    """The intended effect, stated as a test rather than left as a hope. At 90
    all five fit and the cap did nothing; the two lists are what it now stops."""
    cap = paper_writing.HEADLINE_LIMIT
    refused = [text for text, shape in WRITTEN_HEADLINES if len(text) > cap]
    kept = [text for text, shape in WRITTEN_HEADLINES if len(text) <= cap]
    assert [shape for text, shape in WRITTEN_HEADLINES if text in refused] == [
        "three-clause list", "three-clause list"]
    assert [shape for text, shape in WRITTEN_HEADLINES if text in kept] == [
        "one idea", "one idea", "one idea"]


def test_the_cap_is_not_so_tight_it_refuses_a_good_headline():
    """The other side of the same choice. Below 68 the cap starts refusing
    single-idea lines that were doing their job. See `CAP_WINDOW`, which derives
    the bounds rather than restating them."""
    cap = paper_writing.HEADLINE_LIMIT
    longest_good = max(len(t) for t, shape in WRITTEN_HEADLINES if shape == "one idea")
    assert cap >= longest_good, "the cap refuses a headline that reads correctly"
    shortest_list = min(
        len(t) for t, shape in WRITTEN_HEADLINES if shape == "three-clause list")
    assert cap < shortest_list, "a three-clause list still fits"


def test_a_headline_asks_for_one_thing_and_says_so():
    """The guidance half, held structurally and no further. This cannot show that
    the lines come back shorter, only that each headline slot still asks for a
    single idea rather than enumerating several."""
    for slot in _headline_slots():
        assert "ONE" in slot.guidance, (
            f"{slot.path} no longer asks for one thing in particular")


def test_the_two_summaries_that_overshot_ask_for_one_sentence():
    for path in ("copy.platform_summary", "copy.plan_summary"):
        slot = next(s for s in paper_writing.WRITTEN_SCOPE if s.path == path)
        assert "One sentence" in slot.guidance, path
        assert "One or two sentences" not in slot.guidance, path


def test_plan_summary_no_longer_asks_for_three_contents_in_one_line():
    """It asked for what the programme does end to end, how many phases it runs
    in, and where its milestones fall, inside 200 characters. The Gantt beneath
    it already draws the last two."""
    slot = next(s for s in paper_writing.WRITTEN_SCOPE
                if s.path == "copy.plan_summary")
    assert "how many phases it runs in" not in slot.guidance
    assert "where its milestones fall" not in slot.guidance
    # And it says why they are gone, so nobody adds them back.
    assert "Gantt" in slot.guidance


# The window the cap comment states, derived rather than narrated. Added
# 2026-09-19 after that comment shipped saying "66 to 70", which is wrong at both
# ends: 66 and 67 refuse a headline that reads correctly, and 71 to 74 are inside
# the window. The cap was right and its justification was not, which is the half
# a later reader acts on when they move the number.
CAP_WINDOW = (68, 74)


def _cap_keeps_keepers_and_refuses_lists(cap):
    keepers = [len(t) for t, shape in WRITTEN_HEADLINES if shape == "one idea"]
    lists = [len(t) for t, shape in WRITTEN_HEADLINES if shape == "three-clause list"]
    return all(k <= cap for k in keepers) and all(l > cap for l in lists)


def test_the_cap_window_is_what_the_comment_says():
    """Derive the window from the recorded headlines instead of trusting the
    prose beside the number, which was wrong once."""
    low, high = CAP_WINDOW
    workable = [c for c in range(40, 120)
                if _cap_keeps_keepers_and_refuses_lists(c)]
    assert (min(workable), max(workable)) == (low, high)
    # Just outside it in both directions, so the bounds are the real edges.
    assert not _cap_keeps_keepers_and_refuses_lists(low - 1)
    assert not _cap_keeps_keepers_and_refuses_lists(high + 1)


def test_the_chosen_cap_sits_inside_that_window_with_margin_at_both_ends():
    low, high = CAP_WINDOW
    cap = paper_writing.HEADLINE_LIMIT
    assert low <= cap <= high
    # Not pinned to either edge: at `low` the longest good headline is exactly
    # maximal, and at `high` there is one character under the shortest list.
    assert cap > low and cap < high
