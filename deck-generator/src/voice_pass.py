"""LLM voice pass — the model half of the anti-AI-voice gate (PROMPT-QUEUE F1).

NEXT-STEPS item 10. ``text_gate`` (prompt A2) applies the rules that can be
written as rules; what a rule cannot reach is generic phrasing that says nothing
specific about the client, and that needs a model.

This module builds the callable ``apply_text_gate(..., voice_pass=)`` takes. It
does NOT build a guard: ``_run_voice_pass_seam`` already runs
``assert_no_factual_change`` around whatever this returns, and a violation
refuses the render rather than falling back silently (Antonio, 2026-08-06:
factual content must NEVER change).

Three constraints make that guard hard to trip rather than merely watched.

  1. TEXT ONLY, never markup. Prose text nodes go out as numbered lines and come
     back spliced at the byte offsets they came from, so the stylesheet, the
     attributes, and every tag are untouched.
  2. Running-prose paragraphs and nothing else. A headline, an eyebrow, a
     milestone label, a metric label, and a table cell are packet field values;
     rewriting one would put the deck at odds with its own source data. That is
     the same line the deterministic gate draws.
  3. It CUTS (``is_a_cut_of``), so it can delete but never write. A sentence
     that could only be made specific by knowing something the deck does not
     carry is left alone, because the alternative is invention. What a cut may
     not delete is a negation or a projection hedge
     (``keeps_every_protected_word``): dropping one inverts or hardens a claim
     while every figure stays put, so no guard downstream would notice.

The rules, the house voice, and the system prompt are data, in
``voice-rules.json`` beside this file, the way A2 keeps the vocabulary table.
The 2026-08-16 changelog entry carries the live evidence behind all three.
"""

import json
import os
import re
from collections import Counter

# The gate owns the document segmentation, and duplicating it here is how the
# two halves would drift apart on a markup case.
from text_gate import FactualChangeError, _text_nodes, assert_no_factual_change

# One place, per the no-hardcoding rule. NOT ``deck_renderer.DEFAULT_MODEL``:
# that one is the render leg's and is correct for it.
DEFAULT_MODEL = "claude-opus-5"

# ``max_tokens`` bounds thinking plus response text together and thinking is on
# by default, so this leaves headroom rather than being sized to the answer.
# ``temperature``, ``top_p`` and ``top_k`` are rejected on this model, so
# prompting is the lever; a trailing prefill is a 400, so the response shape
# comes from ``output_config.format``.
DEFAULT_MAX_TOKENS = 16000
DEFAULT_EFFORT = "high"

VOICE_RULES_PATH = os.path.join(os.path.dirname(__file__), "voice-rules.json")


def load_voice_rules(path=VOICE_RULES_PATH):
    """Read the rule table, the house voice, and the prompt from their data file.

    The rules were collected from the rendered decks under ``decks/``, and each
    is fixable by cutting. What is NOT here (slogan headlines, category labels,
    prose two clients share verbatim) is extraction-side: a packet field value,
    fixed at the packet.
    """
    with open(path, encoding="utf-8") as f:
        return json.load(f)


VOICE_RULES = load_voice_rules()

REVISION_SCHEMA = {
    "type": "object",
    "properties": {
        "revisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "text": {"type": "string"},
                },
                "required": ["index", "text"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["revisions"],
    "additionalProperties": False,
}


def build_system_prompt(rules=None):
    """The system prompt, with the rule table rendered into it."""
    rules = rules or VOICE_RULES
    listing = "\n".join(f"- {rule['id']}. {rule['text']}" for rule in rules["rules"])
    return rules["system_prompt"].format(
        house_voice=rules["house_voice"], rules=listing
    )


def prose_lines(html):
    """The running-prose text nodes of a rendered deck, in document order.

    Everything else on a slide is a packet field value, so it is never offered
    to the model at all.
    """
    return [node for node in _text_nodes(html) if node.prose and node.text.strip()]


_WORD_RE = re.compile(r"[A-Za-z0-9]+")
# The stop-list check keeps the apostrophe, so "didn't" is one word rather than
# "didn" and "t".
_STOP_WORD_RE = re.compile(r"[A-Za-z0-9']+")


def protected_words(rules=None):
    """The words a cut may never remove, from the data file.

    Negations and the hedges that mark a projection as a projection. Dropping
    one inverts or hardens a claim without moving a number, a date, or a name,
    so ``assert_no_factual_change`` cannot see it and a subsequence test cannot
    either: "did not meet the target" -> "did meet the target" is a valid cut by
    every structural rule and the opposite claim by the only one that matters.
    """
    rules = rules or VOICE_RULES
    return frozenset(
        word.casefold()
        for group in rules["protected_words"].values()
        for word in group
    )


def keeps_every_protected_word(original, revised, words=None):
    """True when the cut removed no protected word, counting repeats.

    Counted rather than merely present, because a line with two negations that
    keeps one has still inverted a sentence.
    """
    words = protected_words() if words is None else words
    def found(text):
        tokens = _STOP_WORD_RE.findall(text.replace("’", "'").casefold())
        return Counter(token for token in tokens if token in words)
    before, after = found(original), found(revised)
    return all(after[word] >= count for word, count in before.items())


def is_a_cut_of(original, revised):
    """True when ``revised`` is ``original`` with words removed and nothing else.

    Word order and word choice stay the original's; punctuation and
    capitalization are the revision's. The golden rule in prose form: deck copy
    is packet-supplied, so a paraphrase is a claim with no source. It also makes
    moving material between lines impossible.
    """
    words = _WORD_RE.findall(revised.casefold())
    remaining = iter(_WORD_RE.findall(original.casefold()))
    return all(any(word == candidate for candidate in remaining) for word in words)


def _revisions(client, nodes, *, model, max_tokens, effort):
    """Ask the model for revised lines. Returns ``{index: text}``."""
    lines = [{"index": i, "text": " ".join(n.text.split())} for i, n in enumerate(nodes)]
    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=build_system_prompt(),
        output_config={
            "effort": effort,
            "format": {"type": "json_schema", "schema": REVISION_SCHEMA},
        },
        messages=[{"role": "user", "content": json.dumps(lines, indent=2)}],
    )
    text = "".join(
        block.text for block in message.content if getattr(block, "type", None) == "text"
    )
    out = {}
    for revision in json.loads(text)["revisions"]:
        index, revised = revision["index"], revision["text"].strip()
        if not 0 <= index < len(nodes):
            continue
        # Double duty: text that adds a word did not come from this line, so a
        # revision filed under the wrong index fails here too.
        if not is_a_cut_of(lines[index]["text"], revised):
            continue
        # A subsequence test cannot see meaning, so the words whose removal
        # changes a claim are named in the data file and checked by count.
        if not keeps_every_protected_word(lines[index]["text"], revised):
            continue
        # A revision that carries markup or empties a line is dropped too: this
        # pass rewrites text nodes only.
        if revised and "<" not in revised and ">" not in revised:
            out[index] = revised
    return out


def _splice(html, nodes, revisions):
    """Put each revised line back at the byte offsets it came from."""
    pieces, cursor = [], 0
    for index, node in enumerate(nodes):
        if index not in revisions:
            continue
        pieces.append(html[cursor:node.start])
        # The node's own leading and trailing whitespace is layout, so it stays.
        pieces.append(node.text.replace(node.text.strip(), revisions[index], 1))
        cursor = node.end
    pieces.append(html[cursor:])
    return "".join(pieces)


def _accepted(html, nodes, revisions):
    """Keep the cuts the factual guard accepts, one at a time.

    The guard is asked rather than reimplemented, because what counts as a name
    is its judgment: it indexes the text nodes joined into one string, so a
    line's opening capitalized word can belong to the previous node's run, and
    cutting it reads as a dropped name (a live status deck was refused over
    exactly that on 2026-08-16). Dropping the one cut beats refusing the deck;
    the seam runs the same guard afterwards, so nothing here is the authority.
    """
    accepted = {}
    for index in sorted(revisions):
        candidate = {**accepted, index: revisions[index]}
        try:
            assert_no_factual_change(html, _splice(html, nodes, candidate))
        except FactualChangeError:
            continue
        accepted = candidate
    return accepted


def make_voice_pass(
    *,
    client=None,
    model=DEFAULT_MODEL,
    max_tokens=DEFAULT_MAX_TOKENS,
    effort=DEFAULT_EFFORT,
    api_key=None,
):
    """Build the ``(html) -> html`` callable ``apply_text_gate`` takes.

    ``client`` may be injected (anything exposing ``messages.create(...)``);
    when omitted a real ``anthropic.Anthropic`` client is constructed and reads
    ``ANTHROPIC_API_KEY`` from the environment, or the explicit ``api_key``.
    """
    def voice_pass(html):
        nodes = prose_lines(html)
        if not nodes:
            return html
        resolved = client
        if resolved is None:
            import anthropic  # lazy: keeps the pure pipeline dependency-free

            resolved = anthropic.Anthropic(api_key=api_key)
        revisions = _accepted(html, nodes, _revisions(
            resolved, nodes, model=model, max_tokens=max_tokens, effort=effort
        ))
        return _splice(html, nodes, revisions) if revisions else html

    return voice_pass
