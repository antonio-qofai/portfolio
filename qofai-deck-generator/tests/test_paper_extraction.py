"""Tests for the second extraction pass's model half (E11 Stage 2).

Everything here runs against an INJECTED FAKE client. No test in this file, or
anywhere in this suite, reaches the network: `paper_extraction.extract` takes a
`client` and `second_pass.run` takes the whole pass as a callable, so the network
is something a caller has to hand over rather than something a default reaches
for. That is checked directly by `test_nothing_is_called_when_nothing_is_absent`
and by the seam tests in `test_second_pass.py`.

What is asserted here is the verification half, and it is asserted by handing the
verifier answers that are WRONG in each of the specific ways a model can be wrong
-- a span that is not in the paper, a leaf paraphrased out of its own span, a
field nobody asked for, a value with no section heading -- and showing each one
never becomes a value. The happy path is one test; the refusals are the module.

The papers are the committed excerpts under `data-provider/fixtures/` and the
hand-built prose paper under `tests/fixtures/second-pass/`. No client name, no
project name and no real figure is written into `src/`.
"""

import json
import pathlib

from source_span import PAPER, Opportunity

# The opportunity these readings are FOR (item 15, 2026-09-13). A figure names
# one the way it names its document, so a parser test has to state one, and
# stating a stand-in here is the same trade `PAPER` makes: these tests are about
# reading a table, not about which engagement asked for it.
OPPORTUNITY = Opportunity(id="OPP-ONE")

# What a scripted item says it is about (item 15). The pass refuses an item
# attributed to nothing and one attributed elsewhere, so every scripted answer
# has to carry one, and the default is this module's own subject label. A fake
# driven through the live provider gets a titled subject instead, which is what
# `attributed` below restamps for.
ABOUT = OPPORTUNITY.label

import pytest

import packet_assembly
import paper_extraction
from paper_extraction import Extraction, ExtractionError, extract, read_response

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "data-provider" / "fixtures"
PROSE = pathlib.Path(__file__).resolve().parent / "fixtures" / "second-pass"


def paper(shape):
    path = FIXTURES / f"paper-excerpt-{shape}.json"
    return json.loads(path.read_text())["opportunity"]["research_paper_natural"]


def prose_paper():
    path = PROSE / "paper-synthetic-prose.json"
    return json.loads(path.read_text())["opportunity"]["research_paper_natural"]


def range_paper():
    """The prose paper whose figures are stated as ranges (E11 Stage 2e).

    A second file rather than an edit to `prose_paper`, because the tests that
    read that one assert on the single figures it states and a range there would
    be a change to their subject rather than a new case beside it.
    """
    path = PROSE / "paper-synthetic-prose-ranges.json"
    return json.loads(path.read_text())["opportunity"]["research_paper_natural"]


def platform_paper():
    """The prose paper that describes a platform and states next steps (Stage 2c).

    A third file rather than an edit to `prose_paper`, on the same reasoning the
    range paper was: the tests reading that one assert on what it states, and
    adding a platform description and a next-steps section there would change
    their subject rather than put a new one beside it.
    """
    path = PROSE / "paper-synthetic-prose-platform.json"
    return json.loads(path.read_text())["opportunity"]["research_paper_natural"]


class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Message:
    def __init__(self, text):
        self.content = [_Block(text)]


class FakeClient:
    """A stand-in for `anthropic.Anthropic`, recording what it was asked.

    Deliberately not a mock library: the assertions below are about the exact
    request shape (the model, the thinking mode, the output format, where the
    paper sits and whether it is marked for caching), and a recorded dict is what
    those read.
    """

    def __init__(self, payload):
        self.payload = payload
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        body = self.payload
        return _Message(body if isinstance(body, str) else json.dumps(body))


def answer(*fields):
    return {"fields": list(fields)}


def field(path, *items):
    return {"path": path, "items": list(items)}


def item(span, section, about=None, **leaves):
    return {
        "span": span,
        "section": section,
        "about": ABOUT if about is None else about,
        "leaves": [{"name": name, "text": text} for name, text in leaves.items()],
    }


def attributed(payload, opportunity):
    """A scripted answer restamped for whichever subject the pass was handed.

    A fake extractor stands in for a model that was TOLD a subject, so it has to
    answer as one: a scripted payload carrying this module's own default label
    would be refused the moment a test drives the pass through the live
    provider, where the subject is the platform's record and its label is the
    opportunity's title. Restamping here keeps every scripted answer correct
    without each test having to know which subject reached it.

    Deliberately not a loosening of the check. The refusal is exercised by tests
    that stamp a DIFFERENT subject on purpose.
    """
    return {"fields": [
        {**field_entry,
         "items": [{**entry, "about": opportunity.label}
                   for entry in field_entry.get("items") or ()]}
        for field_entry in payload.get("fields") or ()
    ]}


# ---------------------------------------------------------------------------
# The request: the whole paper, once, cached, and on the current API.
# ---------------------------------------------------------------------------

def test_the_whole_paper_goes_over_and_is_marked_for_caching():
    """Section-picking is what loses the information this pass exists to recover,
    so the paper crosses whole, in its own system block, marked for prompt
    caching because the same paper is read for every field in one call and again
    on any later call against it."""
    text = prose_paper()
    client = FakeClient(answer())
    extract(text, ["today_pain_points"], client=client, document=PAPER, opportunity=OPPORTUNITY)

    system = client.calls[0]["system"]
    carried = [block for block in system if text in block["text"]]
    assert len(carried) == 1
    assert carried[0]["cache_control"] == {"type": "ephemeral"}
    # Sized on the paper, not on a hand-picked slice of it.
    assert len(carried[0]["text"]) > len(text)


def test_the_call_uses_the_current_api_and_never_budget_tokens():
    """`budget_tokens` is removed on this model and returns a 400, the deprecated
    `output_format` parameter is not the structured-outputs one, and prefill is
    gone. Adaptive thinking with an effort setting is what replaces all three."""
    client = FakeClient(answer())
    extract(prose_paper(), ["today_pain_points"], client=client, document=PAPER, opportunity=OPPORTUNITY)

    call = client.calls[0]
    assert call["model"] == "claude-opus-5"
    assert call["thinking"] == {"type": "adaptive"}
    assert "budget_tokens" not in json.dumps(call["thinking"])
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["output_config"]["format"]["schema"] == paper_extraction.EXTRACTION_SCHEMA
    assert "output_format" not in call
    assert call["messages"][-1]["role"] == "user"


def test_the_request_names_only_the_absent_paths():
    """The precedence rule starts here: a field the first pass sourced is never
    mentioned to the model at all."""
    client = FakeClient(answer())
    extract(prose_paper(), ["today_pain_points", packet_assembly.TIMELINE],
            client=client, document=PAPER, opportunity=OPPORTUNITY)

    asked = json.loads(client.calls[0]["messages"][0]["content"])
    paths = [entry["path"] for entry in asked["fields_absent_from_the_packet"]]
    assert paths == ["timeline.phases", "today_pain_points"]
    assert packet_assembly.REVENUE not in json.dumps(asked)


def test_nothing_is_called_when_nothing_is_absent():
    """A run the deterministic pass already filled costs nothing. The fake would
    record a call if one were made."""
    client = FakeClient(answer())
    result = extract(prose_paper(), [], client=client, document=PAPER, opportunity=OPPORTUNITY)

    assert client.calls == []
    assert result.records == {}


def test_an_unknown_path_is_refused_before_any_call_is_made():
    client = FakeClient(answer())
    result = extract(prose_paper(), ["commercial.qofai_comp_usd"], client=client, document=PAPER, opportunity=OPPORTUNITY)

    assert client.calls == []
    assert [path for path, _why in result.dropped] == ["commercial.qofai_comp_usd"]


def test_the_commercial_fields_are_not_in_scope_at_all():
    """QofAI's per-deal pricing is founder-set, 0 of 21 papers carry it, and
    Antonio has stated he cannot supply it. It is not an extraction target and
    the scope table is where that is enforced rather than in a prompt."""
    for path in packet_assembly.REVIEWER_INPUT:
        assert path not in paper_extraction.BY_PATH
    assert "subtitle" not in paper_extraction.BY_PATH
    assert "opportunity_summary" not in paper_extraction.BY_PATH
    assert "target_metrics[].label" not in paper_extraction.BY_PATH


# ---------------------------------------------------------------------------
# The verification: every way a value can fail to be a quotation.
# ---------------------------------------------------------------------------

def test_a_value_whose_span_is_not_in_the_paper_never_becomes_a_value():
    """The fabrication case, and the one span verification closes mechanically."""
    text = prose_paper()
    result = read_response(text, ["today_pain_points"], _Message(json.dumps(
        answer(field("today_pain_points",
                     item("Schedulers waste most of the week on rework.",
                          "Financial Analysis > Current State",
                          value="Schedulers waste most of the week on rework.")))
    )), document=PAPER, opportunity=OPPORTUNITY)

    assert result.records == {}
    assert "does not occur in the paper" in result.dropped[0][1]


def test_a_leaf_paraphrased_out_of_its_own_span_is_refused():
    """The span is real and the value is not in it. Requiring each leaf's text to
    occur inside its own span is what makes this a quoting pass: a summary, a
    rounding, or a rewording fails here even though the span verifies."""
    text = prose_paper()
    span = "Average quote turnaround runs 9 days"
    assert span in text
    result = read_response(text, ["today_metrics"], _Message(json.dumps(
        answer(field("today_metrics",
                     item(span, "Financial Analysis > Current State",
                          value="about nine days", label="quote turnaround")))
    )), document=PAPER, opportunity=OPPORTUNITY)

    assert result.records == {}
    assert "paraphrase rather than a quotation" in result.dropped[0][1]


def test_a_field_nobody_asked_for_is_dropped_on_the_way_back_in():
    """The second half of the precedence rule at this layer: the request named
    one path and the answer named two."""
    text = prose_paper()
    result = read_response(text, ["today_pain_points"], _Message(json.dumps(
        answer(field(packet_assembly.REVENUE,
                     item("adjusted EBITDA of $12.6M",
                          "Financial Analysis > Current State",
                          value="$12.6M")))
    )), document=PAPER, opportunity=OPPORTUNITY)

    assert result.records == {}
    assert result.dropped[0][0] == packet_assembly.REVENUE
    assert "fills absences only" in result.dropped[0][1]


def test_a_value_with_no_section_heading_is_refused():
    """The section heading is the reviewer's only handle on misattribution, which
    is the error class no check here can catch. A value that arrives without one
    cannot be reviewed, so it does not enter."""
    text = prose_paper()
    result = read_response(text, ["today_pain_points"], _Message(json.dumps(
        answer(field("today_pain_points",
                     item("No one can say which jobs lost money", "",
                          value="No one can say which jobs lost money")))
    )), document=PAPER, opportunity=OPPORTUNITY)

    assert result.records == {}
    assert "no section heading" in result.dropped[0][1]


def test_a_record_missing_a_required_leaf_is_dropped_whole():
    """A phase with no label is not a partial phase, it is a different thing."""
    text = prose_paper()
    span = "| **Phase 1: Scheduling Foundation** | Months 1-3 |"
    assert span in text
    result = read_response(text, [packet_assembly.TIMELINE], _Message(json.dumps(
        answer(field(packet_assembly.TIMELINE,
                     item(span, "Implementation Approach > Timeline",
                          start="1", end="3", unit="months")))
    )), document=PAPER, opportunity=OPPORTUNITY)

    assert result.records == {}
    assert "dropped whole" in result.dropped[0][1]
    assert "was not stated at all" in result.dropped[0][1]


def test_a_unit_the_span_does_not_state_is_refused():
    """No conversion, at the one point a unit could be swapped for another. The
    span says months; weeks does not verify against it."""
    text = prose_paper()
    span = "| **Phase 1: Scheduling Foundation** | Months 1-3 |"
    result = read_response(text, [packet_assembly.TIMELINE], _Message(json.dumps(
        answer(field(packet_assembly.TIMELINE,
                     item(span, "Implementation Approach > Timeline",
                          label="Phase 1: Scheduling Foundation",
                          start="1", end="3", unit="weeks")))
    )), document=PAPER, opportunity=OPPORTUNITY)

    assert result.records == {}


def test_a_scalar_field_keeps_the_first_value_and_drops_the_rest():
    text = prose_paper()
    result = read_response(text, [packet_assembly.MARGIN], _Message(json.dumps(
        answer(field(packet_assembly.MARGIN,
                     item("adjusted EBITDA margin of 15.0%",
                          "Financial Analysis > Current State", value="15.0%"),
                     item("on-time delivery sits at 71%",
                          "Financial Analysis > Current State", value="71%")))
    )), document=PAPER, opportunity=OPPORTUNITY)

    assert len(result.records[packet_assembly.MARGIN]) == 1
    assert result.records[packet_assembly.MARGIN][0].leaves["value"] == (15.0, 15.0)
    assert "more than one" in result.dropped[0][1]


def test_an_empty_answer_is_a_normal_outcome_and_not_an_error():
    """Returning nothing for a field the paper does not state is the correct and
    common result. It records no value and raises nothing."""
    result = read_response(prose_paper(), ["today_pain_points"],
                           _Message(json.dumps(answer())), document=PAPER, opportunity=OPPORTUNITY)

    assert isinstance(result, Extraction)
    assert result.records == {} and result.dropped == ()


def test_an_unreadable_answer_raises_rather_than_being_half_read():
    """Guessing at half an answer is exactly the invention this pass forbids."""
    with pytest.raises(ExtractionError):
        read_response(prose_paper(), ["today_pain_points"], _Message("not json"), document=PAPER, opportunity=OPPORTUNITY)


# ---------------------------------------------------------------------------
# The readers: the paper's own notation, converted by the first pass's own code.
# ---------------------------------------------------------------------------

def test_figures_are_read_by_the_deterministic_passs_own_readers():
    """The model hands over the paper's notation and never a converted number.
    `$84.0M` becomes a `(low, high)` pair through `packet_assembly.read_usd`, the
    same call E7a already makes on parser output, so there is no second
    arithmetic in this repo to disagree with the first."""
    text = prose_paper()
    result = read_response(
        text,
        [packet_assembly.REVENUE, packet_assembly.MARGIN, "commercial.scenarios"],
        _Message(json.dumps(answer(
            field(packet_assembly.REVENUE,
                  item("The company reported LTM revenue of $84.0M",
                       "Financial Analysis > Current State", value="$84.0M")),
            field(packet_assembly.MARGIN,
                  item("adjusted EBITDA margin of 15.0%",
                       "Financial Analysis > Current State", value="15.0%")),
            field("commercial.scenarios",
                  item("| Base Case | $2.6M | 3.1pp |",
                       "Financial Analysis > Projected Impact",
                       name="Base Case", direct_uplift_usd_yr="$2.6M",
                       margin_gain_pp="3.1pp")),
        ))), document=PAPER, opportunity=OPPORTUNITY)

    assert result.records[packet_assembly.REVENUE][0].leaves["value"] == (84e6, 84e6)
    assert result.records[packet_assembly.MARGIN][0].leaves["value"] == (15.0, 15.0)
    case = result.records["commercial.scenarios"][0].leaves
    assert case["direct_uplift_usd_yr"] == (2.6e6, 2.6e6)
    assert case["margin_gain_pp"] == (3.1, 3.1)
    # The paper's own strings are kept beside the converted pair, for the ledger.
    assert result.records[packet_assembly.REVENUE][0].stated["value"] == "$84.0M"


def test_a_percentage_cell_stating_two_periods_yields_nothing():
    """`packet_assembly.read_percent` refuses a cell stating two percentages,
    because on the excerpts that is two periods rather than a range and choosing
    one is not this provider's call. The second pass inherits that refusal rather
    than reimplementing a looser one."""
    text = prose_paper()
    span = "Gross margin ran 38.2% in the first half and 34.9% in the second"
    assert span in text
    result = read_response(text, [packet_assembly.MARGIN], _Message(json.dumps(
        answer(field(packet_assembly.MARGIN,
                     item(span, "Financial Analysis > Current State",
                          value="38.2% in the first half and 34.9%")))
    )), document=PAPER, opportunity=OPPORTUNITY)
    assert result.records == {}


def test_every_verified_record_carries_its_span_and_its_section():
    """Both, on every record, because the ledger a reviewer reads is built from
    them and a value with only one of the two cannot be checked."""
    text = prose_paper()
    result = read_response(text, ["today_pain_points"], _Message(json.dumps(
        answer(field("today_pain_points",
                     item("No one can say which jobs lost money until the "
                          "quarter closes.",
                          "Financial Analysis > Current State",
                          value="No one can say which jobs lost money until "
                                "the quarter closes.")))
    )), document=PAPER, opportunity=OPPORTUNITY)

    record = result.records["today_pain_points"][0]
    assert record.span in text
    assert record.section == "Financial Analysis > Current State"
    assert record.figure.field == "today_pain_points"


def test_a_sound_quotation_in_a_notation_the_reader_rejects_says_so():
    """Found on a live paper 2026-08-18, and the reason it produces matters.

    The model quoted `$3.571 million` correctly from the paper. The span
    verified, the text really was inside it, and the figure still did not enter,
    because `packet_assembly.read_usd` read the `$3.571M` suffix notation and
    not the spelled-out word. The reason must name the reader rather than blame
    the span, or it sends the next reader of the gaps list after the wrong thing.

    PARTLY SUPERSEDED 2026-08-19 by E11 Stage 2b, which widened the magnitude
    vocabulary: `$84.0 million` now reads, and the finding above is fixed rather
    than merely reported. What this test still holds is unchanged and is the
    point of it, because a reader can never accept every notation. The refusal
    demonstrated here is now the residue check's -- a figure stated inside
    supporting prose -- and it produces the same reason naming the same reader.
    """
    text = prose_paper()
    span = "The company reported LTM revenue of $84.0M"
    # What Stage 2b changed, and what it deliberately did not.
    assert packet_assembly.read_usd("$84.0 million") == (84e6, 84e6)
    assert packet_assembly.read_usd("USD 84.0 million") is None

    result = read_response(text, [packet_assembly.REVENUE], _Message(json.dumps(
        answer(field(packet_assembly.REVENUE,
                     item(span, "Financial Analysis > Current State",
                          value="LTM revenue of $84.0M")))
    )), document=PAPER, opportunity=OPPORTUNITY)

    assert result.records == {}
    reason = result.dropped[0][1]
    assert "usd reader does not read as one figure" in reason
    assert "The quotation is sound" in reason
    assert "would change what the FIRST pass reads" in reason


def test_a_phase_summarys_label_is_keyed_to_the_first_pass_not_to_its_span():
    """The label says WHICH phase a summary belongs to. It is never rendered --
    the merge keeps the phase's own label -- so span containment is the wrong
    check for it, and a measurably costly one: on a live paper 2026-08-18 all
    three phase summaries were refused because the model quoted the scope
    sentence as its span and named the phase from its heading, which is the
    correct reading AND the correct span.

    Membership in the labels the first pass already read is the right check and
    a stricter one: it cannot name a phase that does not exist.
    """
    text = prose_paper()
    span = ("| **Phase 2: Pricing Rules** | Months 3-6 | Move pricing out of "
            "the spreadsheet and into the rules engine |")
    labels = ("Phase 1: Scheduling Foundation", "Phase 2: Pricing Rules")

    def read(label):
        return read_response(
            text, ["build_summary.phases[].summary"],
            _Message(json.dumps(answer(field(
                "build_summary.phases[].summary",
                item(span, "Implementation Approach > Timeline", label=label,
                     summary="Move pricing out of the spreadsheet"))))),
            phase_labels=labels, document=PAPER, opportunity=OPPORTUNITY)

    # A label the first pass read: accepted, even though the summary's span is
    # not where the label was written.
    kept = read("Phase 2: Pricing Rules")
    assert kept.records["build_summary.phases[].summary"][0].leaves["label"] == \
        "Phase 2: Pricing Rules"

    # A label naming no phase the first pass read: refused, however plausible.
    refused = read("Phase 4: Continuous Improvement")
    assert refused.records == {}
    assert "not one of the phase labels the first pass read" in refused.dropped[0][1]


# ---------------------------------------------------------------------------
# The subject: which opportunity this reading is FOR (item 15, 2026-09-13).
#
# The hazard is item 14's own success. An attached PRD is the BASE for every
# opportunity in a run, so on a multi-opportunity deck the same document is read
# once per opportunity and the same prose says different things about each. The
# pass could not previously be told which half it was reading, and a figure it
# returned named its document honestly while saying nothing about which slide it
# belonged on.
# ---------------------------------------------------------------------------

OTHER = Opportunity(id="OPP-TWO", title="Real-time operational dashboards")
DESCRIPTION = ("Replacing handwritten daily reports with a tablet capture app "
               "at the point of work.")


def test_the_request_names_the_one_opportunity_this_reading_is_for():
    client = FakeClient(answer())
    extract(prose_paper(), ["today_pain_points"], client=client, document=PAPER,
            opportunity=OPPORTUNITY, description=DESCRIPTION)

    asked = json.loads(client.calls[0]["messages"][0]["content"])
    subject = asked["read_this_paper_for_this_opportunity_only"]
    assert subject["name"] == OPPORTUNITY.label
    assert subject["what_this_opportunity_is"] == DESCRIPTION


def test_the_subject_is_volatile_and_stays_out_of_the_cached_block():
    """The reason it is in the user message. On a multi-opportunity run the same
    document is read once per opportunity, so the cached half is identical
    across those calls and the subject is the whole of what differs. In the
    cached block it would either break the cache on every call or share one
    opportunity's subject with the next."""
    text = prose_paper()
    client = FakeClient(answer())
    extract(text, ["today_pain_points"], client=client, document=PAPER,
            opportunity=OPPORTUNITY, description=DESCRIPTION)

    blocks = client.calls[0]["system"]
    system = json.dumps(blocks)
    assert OPPORTUNITY.label not in system
    assert DESCRIPTION not in system
    # Not vacuous: the paper really is in the cached block, so the two absences
    # above are about the subject rather than about an empty system prompt.
    assert any(text in block["text"] for block in blocks)


def test_two_opportunities_over_one_document_ask_two_different_questions():
    """THE ITEM 15 CASE AT THIS SEAM. Byte-identical paper, byte-identical
    requested paths, and the two calls still differ, because the subject is what
    a multi-opportunity run varies."""
    text = prose_paper()
    client = FakeClient(answer())
    extract(text, ["today_pain_points"], client=client, document=PAPER,
            opportunity=OPPORTUNITY, description=DESCRIPTION)
    extract(text, ["today_pain_points"], client=client, document=PAPER,
            opportunity=OTHER, description="A different thing entirely.")

    first, second = (json.loads(call["messages"][0]["content"])
                     for call in client.calls)
    assert first != second
    assert first["fields_absent_from_the_packet"] == \
           second["fields_absent_from_the_packet"]
    assert (first["read_this_paper_for_this_opportunity_only"]["name"]
            != second["read_this_paper_for_this_opportunity_only"]["name"])


def test_a_value_attributed_to_nothing_is_not_returned():
    """The golden rule arriving at a seam where it was previously unstated. A
    figure the pass cannot attribute to the named opportunity is a missing
    field, never a value."""
    text = prose_paper()
    span = _pain_span(text)
    result = read_response(
        text, ["today_pain_points"],
        _message(answer(field("today_pain_points",
                              item(span, "The Operational Problem", about="",
                                   value=span)))),
        document=PAPER, opportunity=OPPORTUNITY,
    )

    assert result.records.get("today_pain_points") in (None, [])
    reasons = dict(result.dropped)
    assert "attributed to no opportunity" in reasons["today_pain_points"]


def test_a_value_attributed_to_another_opportunity_is_not_returned():
    """The more useful of the two refusals. It gives the pass a way to say "the
    document states this, and it belongs to your other opportunity" instead of
    choosing between dropping it silently and putting it on the wrong slide."""
    text = prose_paper()
    span = _pain_span(text)
    result = read_response(
        text, ["today_pain_points"],
        _message(answer(field("today_pain_points",
                              item(span, "The Operational Problem",
                                   about=OTHER.label, value=span)))),
        document=PAPER, opportunity=OPPORTUNITY,
    )

    assert result.records.get("today_pain_points") in (None, [])
    reason = dict(result.dropped)["today_pain_points"]
    assert OTHER.label in reason
    assert OPPORTUNITY.label in reason
    assert "another opportunity" in reason


def test_the_same_item_attributed_to_the_named_opportunity_is_returned():
    """The control for the two refusals above, so neither is passing because the
    item was malformed in some other way."""
    text = prose_paper()
    span = _pain_span(text)
    result = read_response(
        text, ["today_pain_points"],
        _message(answer(field("today_pain_points",
                              item(span, "The Operational Problem",
                                   about=OPPORTUNITY.label, value=span)))),
        document=PAPER, opportunity=OPPORTUNITY,
    )

    assert result.records["today_pain_points"]
    assert result.dropped == ()


def test_the_attribution_is_matched_on_whitespace_and_case_only():
    """The model is handed the exact string to copy and rule 1 already makes
    this a copying pass, so the match is equality. Normalising whitespace and
    case is the whole of the tolerance, and it is not a fuzzy match: a different
    opportunity's name is refused above however it is spelled."""
    text = prose_paper()
    span = _pain_span(text)
    for spelling in (OPPORTUNITY.label.upper(), f"  {OPPORTUNITY.label}  "):
        result = read_response(
            text, ["today_pain_points"],
            _message(answer(field("today_pain_points",
                                  item(span, "The Operational Problem",
                                       about=spelling, value=span)))),
            document=PAPER, opportunity=OPPORTUNITY,
        )
        assert result.records["today_pain_points"], spelling


def test_the_subject_decides_what_is_asked_and_never_what_verification_means():
    """THE PROPERTY THAT MUST NOT DRIFT. A span that does not occur in the
    document is refused as a fabrication whatever it is attributed to, and
    correct attribution cannot rescue it. The span check runs FIRST, so the
    reason a reviewer reads names the fabrication rather than the attribution."""
    text = prose_paper()
    invented = "LTM revenue of $99.0M, which this paper never states"
    assert invented not in text

    for about in (OPPORTUNITY.label, OTHER.label, ""):
        result = read_response(
            text, ["today_pain_points"],
            _message(answer(field("today_pain_points",
                                  item(invented, "The Operational Problem",
                                       about=about, value=invented)))),
            document=PAPER, opportunity=OPPORTUNITY,
        )
        assert result.records.get("today_pain_points") in (None, []), about
        assert "does not occur in the paper" in dict(result.dropped)["today_pain_points"]


def test_a_correctly_attributed_value_still_has_to_verify_leaf_by_leaf():
    """The other half of the line above. Attribution is not a second way to get a
    value in; every check that stood before this one still stands after it."""
    text = prose_paper()
    span = _pain_span(text)
    result = read_response(
        text, ["today_pain_points"],
        _message(answer(field("today_pain_points",
                              item(span, "The Operational Problem",
                                   about=OPPORTUNITY.label,
                                   value="a paraphrase the paper never wrote")))),
        document=PAPER, opportunity=OPPORTUNITY,
    )

    assert result.records.get("today_pain_points") in (None, [])
    assert result.dropped


def test_the_description_reaches_the_prompt_and_never_a_figure():
    """It is prompt CONTENT and not provenance, which is why it is a separate
    argument from the opportunity rather than a field on it. 1,772 characters on
    the one real record measured is not something to hang on every figure in a
    packet."""
    text = prose_paper()
    span = _pain_span(text)
    result = read_response(
        text, ["today_pain_points"],
        _message(answer(field("today_pain_points",
                              item(span, "The Operational Problem",
                                   about=OPPORTUNITY.label, value=span)))),
        document=PAPER, opportunity=OPPORTUNITY,
    )

    for record in result.records["today_pain_points"]:
        assert DESCRIPTION not in repr(record.figure.opportunity)
        assert record.figure.opportunity == OPPORTUNITY


def _pain_span(text):
    """A sentence the paper really states, so these tests turn on attribution
    rather than on whether the span verifies."""
    span = next(line.strip() for line in text.splitlines()
                if line.strip() and not line.startswith(("#", "|", "<")))
    assert span in text
    return span


def _message(payload):
    return type("M", (), {"content": [
        type("B", (), {"type": "text", "text": json.dumps(payload)})()
    ]})()


# ---------------------------------------------------------------------------
# THE SCHEMA AND THE VERIFIER HAVE TO AGREE, and until 2026-09-13 nothing said
# so. The item object sets `additionalProperties: False`, so a key the prompt
# asks for and the schema does not declare is a key the model CANNOT EMIT under
# structured output. `about` was added to the prompt and to `_record` and not to
# the schema, so every item arrived with no attribution, every one was refused,
# and the pass returned nothing on every document of every live run.
#
# No fixture could see it. Every test here drives `read_response` with a
# scripted payload, and a scripted payload is not constrained by the schema: the
# only thing that enforces it is the API. So the guard is structural, and it
# reads what `_record` requires off the PARSED SOURCE rather than listing it, so
# the next key added to one half cannot drift from the other.
# ---------------------------------------------------------------------------

def _item_schema():
    schema = paper_extraction.EXTRACTION_SCHEMA
    return schema["properties"]["fields"]["items"]["properties"]["items"]["items"]


def _keys_record_reads():
    """Every key `_record` pulls off one item, read off the source."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(paper_extraction._record).strip())
    keys = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "item"
                and node.args
                and isinstance(node.args[0], ast.Constant)):
            keys.add(node.args[0].value)
    return keys


def test_every_key_the_verifier_reads_is_one_the_model_can_emit():
    """THE GUARD. `additionalProperties: False` means a key this object does not
    declare is a key that cannot arrive, so a verifier requiring one would
    refuse every item forever."""
    declared = set(_item_schema()["properties"])
    missing = _keys_record_reads() - declared
    assert not missing, (
        f"{sorted(missing)} is required by the verifier and not declared in "
        "EXTRACTION_SCHEMA, so the model cannot emit it"
    )


def test_the_item_object_really_does_forbid_undeclared_keys():
    """A guard on the guard: the test above only means something while the
    schema is closed. If it ever opens, the failure mode changes and this says
    so rather than leaving the other test quietly vacuous."""
    assert _item_schema()["additionalProperties"] is False


def test_the_attribution_key_is_declared_and_required():
    """Named specifically because it is the one that broke, and because
    declaring it optional would let a model omit it and have every item refused
    just as thoroughly."""
    item_schema = _item_schema()
    assert "about" in item_schema["properties"]
    assert "about" in item_schema["required"]


def test_the_prompt_names_every_value_the_model_has_to_compose():
    """The other direction, and it stops at the keys a model actually writes.

    `leaves` is a container the schema shapes; `span`, `section` and `about` are
    strings the model composes per item, and a string it is asked for without
    being told what it means is how a quoting pass starts inventing. Derived
    from the schema's own types rather than listed, so a fourth one is covered
    the day it is added."""
    written = [name for name, shape in _item_schema()["properties"].items()
               if shape.get("type") == "string"]
    assert len(written) >= 3
    for key in written:
        assert f"`{key}`" in paper_extraction.SYSTEM_PROMPT, key
