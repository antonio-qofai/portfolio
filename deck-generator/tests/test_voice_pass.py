"""Tests for the LLM voice pass (src/voice_pass.py), prompt F1.

The prose pass is the cheap half. What is pinned here is the expensive half,
which is everything that stops a model rewrite from changing what the deck
asserts:

  1. The pass never sees markup. Only running-prose text nodes are offered to
     the model, and a revision is spliced back at the byte offsets it came
     from, so the stylesheet, the attributes, and every tag survive intact.
     A revision carrying a tag is dropped rather than spliced.
  2. Headlines, eyebrows, labels, and table cells are packet field values and
     are never offered at all.
  3. Every cut is put to the diff guard the seam already owns before it is
     applied, and the seam runs that same guard again over the finished
     document. Three tests force a factual change to prove both fire.
  4. The words a cut may never remove, from `src/voice-rules.json`: negations
     and projection hedges. A subsequence test cannot see meaning, and neither
     can the diff guard when no figure moves.
  5. The request shape the model accepts: no `temperature` / `top_p` / `top_k`
     (rejected outright), no trailing assistant prefill (a 400), a schema in
     `output_config.format`, and the model as a parameter with one default.

The evidence is the real rendered deck under `tests/fixtures/text_gate/`, so
these run against copy the pipeline actually produced.

Run with: python3 tests/test_voice_pass.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import voice_pass as vp
from text_gate import (
    FactualChangeError,
    Tag,
    TextNode,
    _segments,
    apply_text_gate,
    assert_no_factual_change,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "text_gate")


def _deck(name="proposal-current.html"):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


class _FakeMessage:
    def __init__(self, payload):
        self.content = [type("Block", (), {"type": "text", "text": payload})()]


class _FakeClient:
    """Records the request and replays a scripted set of revisions.

    ``revise`` maps a line index to either a literal replacement or a callable
    taking the original line text.
    """

    def __init__(self, revise=None):
        self.revise = revise or {}
        self.calls = []

    @property
    def messages(self):
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        lines = json.loads(kwargs["messages"][0]["content"])
        revisions = []
        for line in lines:
            rule = self.revise.get(line["index"])
            if rule is None:
                continue
            text = rule(line["text"]) if callable(rule) else rule
            revisions.append({"index": line["index"], "text": text})
        return _FakeMessage(json.dumps({"revisions": revisions}))


def _sent_lines(client):
    return [line["text"] for line in json.loads(client.calls[0]["messages"][0]["content"])]


def test_only_running_prose_is_offered_to_the_model():
    html = _deck()
    client = _FakeClient()
    vp.make_voice_pass(client=client)(html)

    sent = _sent_lines(client)
    assert sent, "no prose lines were offered to the model"
    assert any("replaces paper field tickets" in line for line in sent), sent
    # Packet field values: a slogan headline, an eyebrow, a milestone label, a
    # metric label. Each is extraction-side and must never reach the pass.
    for value in ("A partnership built on performance.", "THE OPPORTUNITY",
                  "SCOPE LOCKED", "CLIENT UPFRONT"):
        assert all(value not in line for line in sent), value


def test_the_pass_rewrites_text_nodes_and_leaves_every_tag_byte_for_byte():
    html = _deck()
    client = _FakeClient({
        0: lambda text: text.replace("integrated ", ""),
        7: lambda text: text.replace("We invest. We deliver. You retain the value. ", ""),
    })
    revised = vp.make_voice_pass(client=client)(html)

    assert revised != html
    assert "one build, delivered in phases" in revised
    assert "We invest. We deliver." not in revised
    before = [s for s in _segments(html) if isinstance(s, Tag)]
    after = [s for s in _segments(revised) if isinstance(s, Tag)]
    assert [(t.name, t.closing) for t in before] == [(t.name, t.closing) for t in after]
    assert html[: html.index("<body")] == revised[: revised.index("<body")]
    # Every text node the pass did not revise is byte-identical.
    untouched_before = [s.text for s in _segments(html) if isinstance(s, TextNode)]
    untouched_after = [s.text for s in _segments(revised) if isinstance(s, TextNode)]
    assert len(untouched_before) == len(untouched_after)
    differing = sum(1 for a, b in zip(untouched_before, untouched_after) if a != b)
    assert differing == 2, differing


def test_a_revision_carrying_markup_is_dropped_rather_than_spliced():
    # The cut rule below rejects a tag in every real case, since a tag's letters
    # are words the line does not contain. This document is built so they are,
    # which leaves the markup filter as the only thing standing between the
    # model and the document's own markup.
    html = "<html><body><p>b b b field</p></body></html>"
    client = _FakeClient({0: "<b>b</b> field"})
    assert vp.make_voice_pass(client=client)(html) == html


def test_a_revision_filed_under_the_wrong_index_is_dropped():
    # Misnumbering would move prose between slides while leaving every fact in
    # place and in order, which the diff guard cannot see. The cut rule catches
    # it for free: another line's text is not a cut of this one.
    html = _deck()

    class _Shifted(_FakeClient):
        def create(self, **kwargs):
            self.calls.append(kwargs)
            lines = json.loads(kwargs["messages"][0]["content"])
            return _FakeMessage(json.dumps({"revisions": [
                {"index": line["index"] - 1, "text": "A shifted revision."}
                for line in lines[1:]
            ]}))

    assert vp.make_voice_pass(client=_Shifted())(html) == html


def test_a_revision_that_adds_a_word_is_dropped():
    # The pass cuts; it does not write. Deck prose is packet-supplied, so a
    # paraphrase is a claim with no source, and the diff guard cannot see one:
    # it checks figures and names, not whether a sentence still means what its
    # packet field said.
    html = _deck()
    assert vp.is_a_cut_of("The platform scope and the plan", "The scope and the plan")
    assert not vp.is_a_cut_of("The platform scope", "The scope as agreed")
    assert not vp.is_a_cut_of("data foundation, mobile app", "mobile app, data foundation")

    client = _FakeClient({0: (lambda t: t + " Borrowed from another slide.")})
    assert vp.make_voice_pass(client=client)(html) == html


# The three inversions orchestration review reproduced on 2026-08-16. Each is a
# genuine subsequence of its original, so the cut rule accepts all three, and
# none of them moves a number, a date, or a name, so the diff guard accepts them
# too. Only the stop-list stands between them and a deck.
INVERSIONS = (
    ("The vendor did not meet the target.", "The vendor did meet the target."),
    ("Savings may reach the target.", "Savings reach the target."),
    ("We did not deliver. Value was retained.", "We did deliver value."),
)


def test_a_cut_that_drops_a_negation_or_a_hedge_is_refused():
    for original, revised in INVERSIONS:
        assert vp.is_a_cut_of(original, revised), original  # the hole, reproduced
        assert not vp.keeps_every_protected_word(original, revised), original

    # Counted, not merely present: a line with two negations that keeps one has
    # still inverted a sentence.
    assert not vp.keeps_every_protected_word(
        "We did not ship. We did not bill.", "We did not ship. We did bill."
    )
    # And a cut that touches no protected word is still a cut.
    assert vp.keeps_every_protected_word(
        "Savings may not reach the seamless target.", "Savings may not reach the target."
    )

    # End to end: the pass leaves the deck alone rather than shipping the claim.
    html = _deck()
    client = _FakeClient({i: (lambda t: t.replace("not ", "")) for i in range(40)})
    assert vp.make_voice_pass(client=client)(html) == html


def test_the_stop_list_is_data_and_names_both_families():
    words = vp.protected_words()
    for negation in ("not", "no", "never", "without", "cannot", "didn't"):
        assert negation in words, negation
    for hedge in ("may", "could", "expected", "approximately", "target", "subject"):
        assert hedge in words, hedge

    # It is read from the data file, not compiled into the module: a caller
    # handing over a different table gets that table's answer.
    other = {"protected_words": {"custom": ["Bespoke"]}}
    assert vp.protected_words(other) == frozenset({"bespoke"})
    assert not vp.keeps_every_protected_word(
        "A bespoke build", "A build", words=vp.protected_words(other)
    )
    # The model is told, as well as being enforced against.
    assert "Never drop a negation" in vp.build_system_prompt()


def _drops_a_cut(cut, expected):
    """A cut the guard rejects is not applied, and the guard is why."""
    html = _deck()
    client = _FakeClient({i: cut for i in range(40)})
    assert vp.make_voice_pass(client=client)(html) == html

    # The same cut, spliced past the pass, is what the guard refuses. Without
    # this the test above would also pass on a pass that revised nothing.
    nodes = vp.prose_lines(html)
    forced = {i: cut(" ".join(n.text.split())) for i, n in enumerate(nodes)}
    try:
        assert_no_factual_change(html, vp._splice(html, nodes, forced))
    except FactualChangeError as error:
        assert expected in str(error), str(error)
    else:
        raise AssertionError(f"the guard did not refuse: {expected}")


def test_the_guard_drops_a_cut_that_would_take_a_figure_with_it():
    # A cut can still move a fact, by cutting the fact: "capped at 2.5× cost over
    # a three-year term" loses its multiple. This is the failure mode the cut
    # rule cannot catch and the guard exists for.
    _drops_a_cut(lambda text: text.replace("2.5× ", ""), "number, date, or figure")


def test_the_guard_drops_a_cut_that_would_take_a_proper_noun_with_it():
    _drops_a_cut(lambda text: text.replace("QofAI ", ""), "proper noun or entity name")


def test_the_seam_refuses_the_render_when_a_pass_moves_a_fact():
    # The pass filters its own cuts, so the seam's guard is the net under a bug
    # in that filtering. This forces one: a callable that edits the document
    # directly, the way a pass returning model-authored HTML would.
    html = _deck()
    try:
        apply_text_gate(html, voice_pass=lambda document: document.replace("2.5×", "4×"))
    except FactualChangeError as error:
        assert "number, date, or figure" in str(error), str(error)
        assert "'2.5×' became '4×'" in str(error), str(error)
    else:
        raise AssertionError("the seam let a changed multiplier through")


def test_the_request_omits_the_parameters_this_model_rejects():
    client = _FakeClient()
    vp.make_voice_pass(client=client)(_deck())
    call = client.calls[0]

    for rejected in ("temperature", "top_p", "top_k"):
        assert rejected not in call, rejected
    roles = [message["role"] for message in call["messages"]]
    assert roles == ["user"], roles  # a trailing assistant prefill returns a 400
    assert call["output_config"]["format"]["schema"] == vp.REVISION_SCHEMA
    assert call["max_tokens"] == vp.DEFAULT_MAX_TOKENS >= 16000


def test_the_model_is_one_parameter_with_one_default():
    assert vp.DEFAULT_MODEL == "claude-opus-5"

    client = _FakeClient()
    vp.make_voice_pass(client=client)(_deck())
    assert client.calls[0]["model"] == "claude-opus-5"

    other = _FakeClient()
    vp.make_voice_pass(client=other, model="claude-sonnet-5")(_deck())
    assert other.calls[0]["model"] == "claude-sonnet-5"


def test_the_house_voice_and_every_rule_reach_the_system_prompt():
    client = _FakeClient()
    vp.make_voice_pass(client=client)(_deck())
    prompt = client.calls[0]["system"]

    assert prompt == vp.build_system_prompt()
    assert "operating partner at a middle-market private equity firm" in prompt
    assert "No\nem dashes" in prompt and "leverage as a verb" in prompt
    for rule in vp.VOICE_RULES["rules"]:
        assert rule["id"] in prompt and rule["text"] in prompt, rule["id"]
    # The golden rule, stated to the model as the reason a line is left alone,
    # and the cut rule the code enforces behind it.
    assert "Only cut what is on the page" in prompt
    assert "You may not add a\nword" in prompt


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
