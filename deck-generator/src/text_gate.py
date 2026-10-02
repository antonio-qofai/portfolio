"""Deterministic text gate — the rule-driven half of the anti-AI-voice gate.

NEXT-STEPS item 10. The deck copy reads like AI wrote it, and the reader is an
operating partner who can tell a deck written about their business from a deck
written about businesses in general. This module runs over the RENDERED deck HTML
and applies two families of rules:

  1. Format rules, Antonio's spec of 2026-08-06: "No em dashes. Use commas,
     periods, parentheses, or spaced hyphens instead. No bold text inside
     paragraphs. No exclamation points. Minimize colons. Use them only when
     introducing an actual list." Casey at the 2026-07-23 sync: "I hate that, no
     M dashes throughout."
  2. Banned vocabulary, the QofAI house voice's "Vocabulary to Avoid" list. The
     table is data, in ``vocabulary-rules.json`` beside this file.

Not a standing preference. A preference (``src/preference_store.py``) is a
reviewer's format-only choice; these are hard house rules that hold on every deck,
and the vocabulary family does change wording.

THE HARD CONSTRAINT: it must be impossible for this pass to change factual
content. That holds by construction, in three layers.

  L1. Only text nodes are rewritten. The document is walked as a token stream, so
      tags, attributes, and asset bodies are copied byte-for-byte. The one markup
      edit is deleting a bold tag pair, which moves no text.
  L2. Inside a text node every factual span is MASKED to an opaque placeholder
      before a rule runs and restored verbatim after, so a rule cannot see a
      figure, a date, a week, a time, a URL, or a name. ``protect=`` extends the
      mask; the render leg passes everything ``render_guard`` checks, so the two
      guarantees compose.
  L3. The vocabulary rules match a closed lexicon of about a dozen English terms
      and substitute literals from the table. No rule matches open text.

``assert_no_factual_change`` re-derives the factual index of input and output and
refuses a result whose facts moved. It is a net for a bug in L1-L3, not the
guarantee itself.

Where rules stop. Three format rules rewrite; the colon rule only flags, because
deciding whether a real list follows cost more than the rule was worth. Label
colons ("Status update:", "Week 4-5:") are exempt, since the rule is about prose.
The vocabulary forms whose replacement would invent meaning are flagged the same
way. Nothing here is specific to one client, project, or deck type.

THE F1 SEAM: ``apply_text_gate(..., voice_pass=...)``, built out in
``voice_pass.py`` as of 2026-08-16. See ``_run_voice_pass_seam``.
"""

import json
import os
import re
import unicodedata
from collections import Counter, namedtuple

from crude_fact_check import crude_fact_delta

# Two vocabulary rules ("pilot", "portco") apply only to external copy. A deck is
# client-facing, so the render leg passes EXTERNAL.
AUDIENCE_EXTERNAL = "external"
AUDIENCE_INTERNAL = "internal"
AUDIENCE_ANY = "any"


class FactualChangeError(AssertionError):
    """The gate's output differs factually from its input.

    Subclasses ``AssertionError`` so the repo's test runners report it cleanly,
    matching ``render_guard.RenderFidelityError``. Reaching it means a rule
    escaped the masking layer, which is a defect in this module.
    """


# ---------------------------------------------------------------------------
# L1 — document segmentation. The gate never parses the HTML into a tree and
# re-serializes it: a re-serializer rewrites markup it did not intend to touch.
# ---------------------------------------------------------------------------
SKIP_ELEMENTS = ("style", "script", "svg", "noscript", "template")
BOLD_ELEMENTS = ("b", "strong")

# "No bold text inside paragraphs" means paragraphs of running prose and nothing
# else, so the bold rule runs inside these and nowhere else. ``<p>`` is the one
# element that MEANS running prose; a slide header, eyebrow, label, table cell,
# and bullet lead-in are all something else and keep their bold.
PROSE_ELEMENTS = ("p",)

# Elements that open a new text flow, used by the bold rule to find the block a
# bold run lives in.
BLOCK_ELEMENTS = (
    "p", "div", "li", "td", "th", "dd", "dt", "section", "article", "header",
    "footer", "aside", "main", "blockquote", "figcaption", "caption", "legend",
    "h1", "h2", "h3", "h4", "h5", "h6", "body", "summary",
)

_TOKEN_RE = re.compile(r"<!--.*?-->|<[^>]*>", re.DOTALL)
_TAG_NAME_RE = re.compile(r"^</?\s*([A-Za-z][A-Za-z0-9]*)")

Tag = namedtuple("Tag", "start end name closing self_closing")
# ``prose`` is set by ``_text_nodes``: True when the node sits inside a
# running-prose paragraph. Both the bold rule and the dash rule turn on it.
TextNode = namedtuple("TextNode", "start end text prose", defaults=(False,))


def _segments(document):
    """Split ``document`` into Tag and TextNode records covering every byte.

    A caller that rewrites only TextNode spans (and deletes whole Tag spans)
    reproduces the rest of the file byte-for-byte.
    """
    out = []
    cursor = 0
    for match in _TOKEN_RE.finditer(document):
        if match.start() > cursor:
            out.append(TextNode(cursor, match.start(), document[cursor:match.start()]))
        raw = match.group(0)
        name = _TAG_NAME_RE.match(raw)
        if name and not raw.startswith("<!"):
            out.append(Tag(
                match.start(), match.end(), name.group(1).lower(),
                raw.startswith("</"), raw.rstrip().endswith("/>"),
            ))
        cursor = match.end()
    if cursor < len(document):
        out.append(TextNode(cursor, len(document), document[cursor:]))
    return out


def _text_nodes(document):
    """Every text node that is deck copy, in order, skipping asset bodies.

    Each carries ``prose``, whether it sits inside a running-prose paragraph.
    """
    out, stack = [], []
    for seg in _segments(document):
        if isinstance(seg, Tag):
            if seg.self_closing:
                continue
            if not seg.closing:
                stack.append(seg.name)
                continue
            for i in range(len(stack) - 1, -1, -1):
                if stack[i] == seg.name:
                    del stack[i:]
                    break
        elif seg.text.strip() and not any(name in SKIP_ELEMENTS for name in stack):
            out.append(seg._replace(prose=any(name in PROSE_ELEMENTS for name in stack)))
    return out


def _words(text):
    return re.findall(r"[A-Za-z]+(?:[\u0027\u2019][A-Za-z]+)?", text)


# ---------------------------------------------------------------------------
# L2 — the masking layer. This is what makes "cannot change a fact" structural
# rather than aspirational: the factual spans are not in the string the rules
# see.
#
# Ordered, first match wins. Money/percent/time/ratio come before the bare
# number pattern so a composite is captured whole. Dash characters are
# deliberately NOT swallowed into a numeric range (``1–8`` masks as two numbers
# with a visible dash between them), because the dash rule has to be able to
# normalize that dash.
# ---------------------------------------------------------------------------
_MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|"
    "November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
)

PROTECTED_PATTERNS = (
    # An HTML entity reference is markup, not prose — except the dash spellings,
    # which the dash rule owns and must be able to see.
    ("entity_ref", r"&(?!mdash;|ndash;|#8212;|#8211;|#x201[34];)(?:#\d+|#[xX][0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]*);"),
    ("url", r"(?:https?://|www\.)[^\s<>\"]+"),
    ("email", r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    ("money", r"[$€£][ \t]?\d[\d,]*(?:\.\d+)?(?:[ \t]*(?:[KkMmBb]\b|million\b|billion\b|thousand\b))?"),
    # The sign is only a sign when nothing numeric precedes it: in "80-84%" the
    # hyphen is the range, not a minus, and reading it as one would make the
    # index disagree with itself across a dash rewrite.
    ("percent", r"(?<![\d.,])[+\-−]?\d[\d,]*(?:\.\d+)?[ \t]*(?:%|pp\b)"),
    ("multiple", r"\d+(?:\.\d+)?[ \t]*(?:[x×]\b|[x×](?=\s|$))"),
    ("date", (
        rf"\b(?:{_MONTHS})\.?[ \t]+\d{{1,2}}(?:st|nd|rd|th)?(?:,[ \t]*\d{{4}})?\b"
        rf"|\b\d{{1,2}}[ \t]+(?:{_MONTHS})\.?(?:,?[ \t]*\d{{4}})?\b"
        rf"|\b(?:{_MONTHS})\.?[ \t]+\d{{4}}\b"
        r"|\b\d{4}-\d{2}-\d{2}\b"
        r"|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b"
        r"|\bQ[1-4](?:[ \t]*(?:FY)?\d{2,4})?\b"
        r"|\bFY[ \t]?\d{2,4}\b"
    )),
    ("time", r"\b\d{1,2}:\d{2}(?::\d{2})?[ \t]*(?:[AaPp]\.?[Mm]\.?)?(?:[ \t]*[A-Z]{2,4}T\b)?"),
    ("ratio", r"\b\d+:\d+\b"),
    # A week/quarter reference reads as a label on a deck ("WK 1-2", "Week 6"),
    # and is masked as a unit so no rule can split one.
    ("week", r"\b(?:WKS?|WEEKS?|Q)\.?[ \t]*\d+(?:[ \t]*(?:of|OF)[ \t]*\d+)?\b"),
    ("number", r"\b\d[\d,]*(?:\.\d+)?(?:st|nd|rd|th)?\b"),
)

_PROTECTED_RES = tuple((kind, re.compile(pattern)) for kind, pattern in PROTECTED_PATTERNS)

# A capitalized run is a name until proven otherwise: "Ridgeline Site Services",
# "QofAI", "EBITDA", "Woodgrove Partners". Runs may be joined by the lowercase
# connectors a company name actually uses.
_NAME_WORD = r"[A-Z][A-Za-z0-9'’&./\-]*"
_NAME_CONNECTOR = r"(?:of|and|the|for|de|von|van|&)"
_ENTITY_RE = re.compile(
    rf"{_NAME_WORD}(?:[ \t]+(?:{_NAME_CONNECTOR}[ \t]+)?{_NAME_WORD})*"
)

# Private-use characters, so a placeholder can never collide with deck copy and
# is not a word character to any regex a rule uses.
_PLACEHOLDER_OPEN = ""
_PLACEHOLDER_CLOSE = ""
_PLACEHOLDER_RE = re.compile(f"{_PLACEHOLDER_OPEN}(\\d+){_PLACEHOLDER_CLOSE}")

NUMERIC_KINDS = ("money", "percent", "multiple", "date", "time", "ratio", "week", "number", "url", "email")

Span = namedtuple("Span", "kind text")


def _placeholder(index):
    return f"{_PLACEHOLDER_OPEN}{index}{_PLACEHOLDER_CLOSE}"


def _mask(text, *, lexicon=frozenset(), protect=()):
    """Replace every factual span in ``text`` with an opaque placeholder.

    Returns ``(masked_text, spans)``; ``spans[i]`` is the Span the placeholder
    with index ``i`` stands for. ``_unmask`` is the exact inverse, so any rule
    that leaves placeholders intact cannot alter a fact.

    ``protect`` is an iterable of literal strings the caller declares untouchable
    (the render leg passes every value ``render_guard`` checks). ``lexicon`` is
    the set of banned single words that must stay VISIBLE to the vocabulary
    rules: a one-word capitalized run whose word is in the lexicon is not masked,
    so "Pilot validated" is still reachable while "Pilot Point Manufacturing"
    (a multi-word run) is not.

    Masking is position-independent by design. Whether a name is masked never
    depends on where in a sentence it sits, so a punctuation rewrite that moves a
    sentence boundary cannot change what is protected.
    """
    spans = []
    claimed = []  # ordered, non-overlapping (start, end, kind)

    def claim(start, end, kind):
        for c_start, c_end, _ in claimed:
            if start < c_end and c_start < end:
                return False
        claimed.append((start, end, kind))
        return True

    # Caller-declared literals first: they outrank every built-in pattern.
    for literal in protect:
        literal = (literal or "").strip()
        if len(literal) < 2:
            continue
        for match in re.finditer(re.escape(literal), text):
            claim(match.start(), match.end(), "declared")

    for kind, pattern in _PROTECTED_RES:
        for match in pattern.finditer(text):
            claim(match.start(), match.end(), kind)

    for match in _ENTITY_RE.finditer(text):
        run = match.group(0).rstrip("'’&./-")
        if not run:
            continue
        words = run.split()
        if len(words) == 1 and words[0].casefold().strip(".,'’") in lexicon:
            continue  # a banned term standing alone: the vocabulary rules own it
        claim(match.start(), match.start() + len(run), "entity")

    claimed.sort()
    out = []
    cursor = 0
    for start, end, kind in claimed:
        out.append(text[cursor:start])
        out.append(_placeholder(len(spans)))
        spans.append(Span(kind, text[start:end]))
        cursor = end
    out.append(text[cursor:])
    return "".join(out), spans


def _unmask(masked, spans):
    """Restore every placeholder to its original text, verbatim."""
    return _PLACEHOLDER_RE.sub(lambda m: spans[int(m.group(1))].text, masked)

# ---------------------------------------------------------------------------
# Rule tables. Both families are data; the pass below never names a rule.
# ---------------------------------------------------------------------------
FormatRule = namedtuple("FormatRule", "id title handler source note")

FORMAT_RULES = (
    FormatRule(
        id="no-exclamation-points",
        title="Exclamation points become periods",
        handler="exclamation",
        source="Antonio, 2026-08-06 (house format spec)",
        note="An exclamation point is never house voice. Deterministic and total.",
    ),
    FormatRule(
        id="no-em-dashes",
        title="Em dashes become parentheses, a comma, or a spaced hyphen",
        handler="dashes",
        source="Casey, 2026-07-23 sync: \"I hate that, no M dashes throughout\"",
        note=(
            "The spec asks for a grammatical choice, so the rule makes one, and "
            "scopes it the way the bold rule is scoped. A pair bracketing a "
            "phrase in one sentence becomes parentheses anywhere. A single dash "
            "becomes a comma inside running prose (a spaced hyphen only where "
            "that comma would pile up beside one already there) and a spaced "
            "hyphen everywhere else, because a comma is wrong in a header, a "
            "phase band, or a label-value pair, which is where deck em dashes "
            "actually live. En dashes are not em dashes and are not in the spec, "
            "so they are left alone."
        ),
    ),
    FormatRule(
        id="minimize-colons",
        title="Prose colons are flagged for the reviewer; none is rewritten",
        handler="colons",
        source="Antonio, 2026-08-06 (house format spec)",
        note=(
            "A colon introducing an actual list is correct, and deciding whether "
            "one follows cost more machinery than the rule was worth, so the rule "
            "does not decide. Label colons are exempt because the rule is about "
            "prose. Times and ratios never reach it: the mask removed them first."
        ),
    ),
    FormatRule(
        id="no-in-paragraph-bold",
        title="Bold inside a running-prose paragraph is unbolded",
        handler="bold",
        source="Antonio, 2026-08-06 (house format spec)",
        note=(
            "Scoped to ``<p>`` and nothing else, because the spec is about "
            "paragraphs: headers, eyebrows, labels, table cells, and bullet "
            "lead-ins keep their bold. Inside a paragraph, bold that is the whole "
            "of it is a header set as a paragraph and stays; anything else is "
            "emphasis in running prose and loses the tag. No text moves either "
            "way. On a deck with no inline bold in a prose paragraph the rule "
            "correctly does nothing at all."
        ),
    ),
)

# ``group`` names the capture group that is replaced, so a rule can require
# context ("to leverage") without rewriting it. ``words`` declares every word the
# rule may consume or produce; the mask keeps them reachable and the factual
# index skips them. Declaration ORDER is load-bearing: the longer verb-phrase
# patterns come before the shorter noun patterns.
VOCABULARY_RULES_PATH = os.path.join(os.path.dirname(__file__), "vocabulary-rules.json")

VocabularyRule = namedtuple(
    "VocabularyRule",
    "id pattern replacement group mode audience words source note case_sensitive",
)

MODE_REPLACE = "replace"
MODE_REPLACE_AND_FLAG = "replace_and_flag"
MODE_FLAG = "flag"


_RULE_DEFAULTS = {"replacement": None, "group": 0, "audience": AUDIENCE_ANY, "case_sensitive": False}


def load_vocabulary_rules(path=VOCABULARY_RULES_PATH):
    """Read the banned-vocabulary table from its data file."""
    with open(path, encoding="utf-8") as f:
        records = json.load(f)
    return tuple(
        VocabularyRule(**{**_RULE_DEFAULTS, **r, "words": tuple(r["words"])})
        for r in records
    )


VOCABULARY_RULES = load_vocabulary_rules()

_VOCAB_RES = {
    rule.id: re.compile(rule.pattern, 0 if rule.case_sensitive else re.IGNORECASE)
    for rule in VOCABULARY_RULES
}

# Every word a rule can stand alone as. Kept out of the mask so the rules can
# reach it, and out of the factual index so a declared rewrite never reads as a
# changed entity.
VOCABULARY_LEXICON = frozenset(
    word.casefold() for rule in VOCABULARY_RULES for word in rule.words
)


# ---------------------------------------------------------------------------
# Results.
# ---------------------------------------------------------------------------
GateFlag = namedtuple("GateFlag", "rule_id kind text context note")
GateChange = namedtuple("GateChange", "rule_id before after")
GateResult = namedtuple("GateResult", "html flags changes")


class _Sink:
    """Collects the changes and flags a run produced."""

    def __init__(self):
        self.changes = []
        self.flags = []

    def change(self, rule_id, before, after):
        self.changes.append(GateChange(rule_id, before, after))

    def flag(self, rule_id, kind, text, context, note):
        # Deduped: a label repeated on eight slides is one decision, not eight.
        flag = GateFlag(rule_id, kind, text, context, note)
        if flag not in self.flags:
            self.flags.append(flag)


def _context(text, start, end, width=48):
    left, right = text[max(0, start - width):start], text[end:end + width]
    return " ".join(f"{left}[{text[start:end]}]{right}".split())


# ---------------------------------------------------------------------------
# The text rules. Every one of these runs on MASKED text.
# ---------------------------------------------------------------------------
_EXCLAMATION_RE = re.compile(r"!+")
EM_DASH_RE = re.compile(r"[ \t]*(?:—|&mdash;|&#8212;|&#x2014;)[ \t]*", re.IGNORECASE)


def _rule_exclamation(text, sink):
    """Exclamation points become periods. Total and unconditional."""
    def swap(match):
        sink.change("no-exclamation-points", match.group(0), ".")
        return "."
    return _EXCLAMATION_RE.sub(swap, text)


def _rule_dashes(text, sink, *, prose):
    """Em dashes out, by the cases the spec allows, scoped to where they sit.

    A pair bracketing a phrase inside one sentence becomes parentheses wherever
    it appears. A single dash becomes a comma in running prose, or a spaced
    hyphen outside it (a header, a phase band, a label-value pair) and in the one
    prose case where the comma would land beside one already there and pile up.
    "Inside one sentence" needs no sentence chunker: two dashes with terminal
    punctuation between them are not a pair. En dashes are left alone, being
    neither em dashes nor in the spec.
    """
    dashes = list(EM_DASH_RE.finditer(text))
    brackets, index = {}, 0
    while index + 1 < len(dashes):
        between = text[dashes[index].end():dashes[index + 1].start()]
        if between.strip() and not any(mark in between for mark in ".!?"):
            brackets[index], brackets[index + 1] = "(", ")"
            index += 2
        else:
            index += 1

    out, cursor = [], 0
    for index, match in enumerate(dashes):
        before, after = text[cursor:match.start()], text[match.end():]
        if brackets.get(index) == "(":
            replacement = "(" if not before else " ("
        elif brackets.get(index) == ")":
            replacement = ")" if not after or after[0] in ".,;:!?)" else ") "
        elif not prose or before.rstrip().endswith(",") or after.lstrip().startswith(","):
            # Outside prose a comma reads wrong, and inside it a comma here would
            # pile up on the one already there.
            replacement = " - "
        else:
            replacement = ", "
        sink.change("no-em-dashes", match.group(0), replacement)
        out.append(before)
        out.append(replacement)
        cursor = match.end()
    out.append(text[cursor:])
    return "".join(out)


# A colon after a short fragment with no verb is the deck's label device
# ("Status update:", "Week 4-5:"), not prose. Auxiliaries and copulas only: they
# are what tells a clause from a label.
_LABEL_MAX_WORDS = 4
_FINITE_VERBS = frozenset("""
is are was were be been being has have had do does did will would can could
should shall may might must
""".split())


def _rule_colons(text, sink):
    """Flag every prose colon for the reviewer. Rewrite none, exempt labels."""
    cursor = 0
    for match in re.finditer(":", text):
        fragment = _words(_PLACEHOLDER_RE.sub(" ", text[cursor:match.start()]))
        cursor = match.end()
        if len(fragment) <= _LABEL_MAX_WORDS and not any(
            word.casefold() in _FINITE_VERBS for word in fragment
        ):
            continue  # a label, and the rule is about prose
        sink.flag(
            "minimize-colons", "colon", ":",
            _context(text, match.start(), match.end()),
            "House style minimizes colons. Keep it only if it introduces an "
            "actual list; otherwise reword.",
        )
    return text


def _match_case(original, replacement):
    """Carry the original's casing onto the replacement.

    Case is never factual: a name's letters are untouched by any rule, because a
    name is masked before a rule runs.
    """
    letters = [ch for ch in original if ch.isalpha()]
    if not replacement or not letters:
        return replacement
    if all(ch.isupper() for ch in letters) and len(letters) > 1:
        return replacement.upper()
    if letters[0].isupper():
        return replacement[0].upper() + replacement[1:]
    return replacement


def _rule_vocabulary(text, sink, *, audience):
    """Apply the banned-vocabulary table, in declaration order."""
    for rule in VOCABULARY_RULES:
        if rule.audience != AUDIENCE_ANY and rule.audience != audience:
            continue
        pattern = _VOCAB_RES[rule.id]
        if rule.mode == MODE_FLAG:
            for match in pattern.finditer(text):
                sink.flag(rule.id, "vocabulary", match.group(rule.group),
                          _context(text, match.start(rule.group), match.end(rule.group)),
                          rule.note)
            continue

        def swap(match, rule=rule):
            original = match.group(rule.group)
            replacement = _match_case(original, rule.replacement)
            sink.change(rule.id, original, replacement)
            if rule.mode == MODE_REPLACE_AND_FLAG:
                sink.flag(rule.id, "vocabulary", original, match.group(0), rule.note)
            whole = match.group(0)
            head = whole[:match.start(rule.group) - match.start()]
            return head + replacement + whole[match.end(rule.group) - match.start():]

        text = pattern.sub(swap, text)
    return text


# ---------------------------------------------------------------------------
# The bold rule. Structural, and it moves no text: the only edit is deleting the
# open and close tags of a bold run that is not the whole of its block.
# ---------------------------------------------------------------------------
def _bold_is_whole_block(segments, open_index, close_index):
    """True when nothing but the bold run carries text inside its block."""
    for seg in reversed(segments[:open_index]):
        if isinstance(seg, Tag) and seg.name in BLOCK_ELEMENTS:
            break
        if isinstance(seg, TextNode) and seg.text.strip():
            return False
    for seg in segments[close_index + 1:]:
        if isinstance(seg, Tag) and seg.name in BLOCK_ELEMENTS:
            break
        if isinstance(seg, TextNode) and seg.text.strip():
            return False
    return True


def _apply_bold_rule(document, sink):
    """Unbold every bold run that is emphasis inside a running-prose paragraph."""
    segments = _segments(document)
    cuts, stack = [], []
    for index, seg in enumerate(segments):
        if not isinstance(seg, Tag) or seg.self_closing:
            continue
        if seg.closing:
            for i in range(len(stack) - 1, -1, -1):
                if stack[i] == seg.name:
                    del stack[i:]
                    break
            continue
        if (
            seg.name in BOLD_ELEMENTS
            and any(name in PROSE_ELEMENTS for name in stack)
            and not any(name in SKIP_ELEMENTS for name in stack)
        ):
            close = next((i for i in range(index + 1, len(segments)) if isinstance(segments[i], Tag)
                          and segments[i].closing and segments[i].name == seg.name), None)
            if close is not None and not _bold_is_whole_block(segments, index, close):
                inner = "".join(s.text for s in segments[index + 1:close] if isinstance(s, TextNode))
                sink.change("no-in-paragraph-bold", f"<{seg.name}>{inner}</{seg.name}>", inner)
                cuts += [(seg.start, seg.end), (segments[close].start, segments[close].end)]
        stack.append(seg.name)

    for start, end in sorted(cuts, reverse=True):
        document = document[:start] + document[end:]
    return document


# ---------------------------------------------------------------------------
# The pass.
# ---------------------------------------------------------------------------
def _apply_text_rules(document, sink, *, audience, protect):
    """Run the text-node rules over the whole document (L1 + L2 + L3)."""
    pieces = []
    cursor = 0
    for node in _text_nodes(document):
        masked, spans = _mask(node.text, lexicon=VOCABULARY_LEXICON, protect=protect)
        masked = _rule_exclamation(masked, sink)
        masked = _rule_dashes(masked, sink, prose=node.prose)
        masked = _rule_colons(masked, sink)
        masked = _rule_vocabulary(masked, sink, audience=audience)
        rewritten = _unmask(masked, spans)
        if rewritten != node.text:
            pieces.append(document[cursor:node.start])
            pieces.append(rewritten)
            cursor = node.end
    pieces.append(document[cursor:])
    return "".join(pieces)


def apply_text_gate(html, *, audience=AUDIENCE_EXTERNAL, protect=(), voice_pass=None, verify=True):
    """Run the deterministic text gate over a rendered deck.

    ``audience`` selects the audience-scoped vocabulary rules. ``protect`` is an
    iterable of literal strings the caller declares untouchable on top of the
    built-in masking; the render leg passes every value ``render_guard`` checks.

    Returns ``GateResult(html, flags, changes)``: what it handed to the human and
    what it rewrote, rule by rule. ``verify=True`` (the default) raises
    ``FactualChangeError`` rather than returning a deck whose facts moved.
    Idempotent: a second pass yields the same document and no changes.
    """
    protect = tuple(protect or ())
    sink = _Sink()
    out = _apply_text_rules(_apply_bold_rule(html, sink), sink, audience=audience, protect=protect)
    if verify:
        assert_no_factual_change(html, out, protect=protect)
    if voice_pass is not None:
        out = _run_voice_pass_seam(out, voice_pass, protect=protect)
    return GateResult(html=out, flags=sink.flags, changes=sink.changes)


def prose_at(document, offset):
    """Whether ``offset`` in ``document`` sits inside running prose.

    This module's own definition and no other: a ``<p>`` on the open-tag stack,
    exactly what ``_text_nodes`` marks each node with. Exposed because the edit
    path (part one item 6) gates one fragment at a time and has to gate it the
    way the same text would have been gated inside the rendered document, and
    the dash rule genuinely differs: a single em dash becomes a comma in prose
    and a spaced hyphen outside it.
    """
    stack = []
    for seg in _segments(document[:offset]):
        if not isinstance(seg, Tag) or seg.self_closing:
            continue
        if not seg.closing:
            stack.append(seg.name)
            continue
        for i in range(len(stack) - 1, -1, -1):
            if stack[i] == seg.name:
                del stack[i:]
                break
    return any(name in PROSE_ELEMENTS for name in stack)


def gate_fragment(text, *, prose=False, audience=AUDIENCE_EXTERNAL, protect=()):
    """Apply the format and vocabulary rules to ONE plain-text fragment.

    The unit here is a fragment of copy, not a document: text a reviewer typed
    into an edit, on its way onto a slide (part one item 6). ``apply_text_gate``
    is the wrong tool for that. It walks a document for text nodes, and running
    it over a revision file it did not render means re-deriving a factual index
    for content it has no prompt for, to prove nothing moved in a document this
    caller is not rewriting.

    Same L2 and L3 guarantees as the document path, because it is the same four
    rules over the same mask: every factual span is masked before a rule runs and
    restored after, the vocabulary rules match a closed lexicon, and
    ``assert_no_factual_change`` refuses a result whose facts moved. L1 does not
    arise, since a fragment is text and not markup.

    The bold rule is not run and cannot be: it deletes a tag pair, and a
    fragment on this path carries no markup at all. The caller escapes what it
    inserts, so nothing here can print as a tag either way.

    ``prose`` decides the dash rule the way the element would have decided it in
    the document; see ``prose_at``. Returns the same ``GateResult`` the document
    path returns, whose ``html`` field is the rewritten fragment, so a caller
    reads ``flags`` and ``changes`` identically on both paths. Idempotent.
    """
    sink = _Sink()
    masked, spans = _mask(text, lexicon=VOCABULARY_LEXICON, protect=protect)
    masked = _rule_exclamation(masked, sink)
    masked = _rule_dashes(masked, sink, prose=prose)
    masked = _rule_colons(masked, sink)
    masked = _rule_vocabulary(masked, sink, audience=audience)
    out = _unmask(masked, spans)
    assert_no_factual_change(text, out, protect=protect)
    return GateResult(html=out, flags=sink.flags, changes=sink.changes)


def review_flags(html, *, audience=AUDIENCE_EXTERNAL, protect=()):
    """The reviewer-facing flags for a finished deck.

    The gate is idempotent, so a second run rewrites nothing and reports exactly
    what the first left for a human. Safe on any deck, edited by hand or not.
    """
    return apply_text_gate(html, audience=audience, protect=protect).flags


# ---------------------------------------------------------------------------
# ===================== SEAM: LLM VOICE PASS (PROMPT-QUEUE F1) ===============
#
# The rules above cover what rules can reach. What they cannot reach is generic
# phrasing that says nothing specific about the client, and that needs a model.
#
# F1 attaches HERE and nowhere else: ``voice_pass`` is a callable ``(html) ->
# html`` that runs after the deterministic rules, and its output goes through
# ``assert_no_factual_change``. A violation raises and the render is refused;
# there is no silent fallback. That refusal is the load-bearing part of F1, not
# the prose pass.
#
# BUILT 2026-08-16: ``voice_pass.make_voice_pass()`` returns that callable. It
# rewrites running-prose text nodes only and never sees markup; the house voice
# and its rule table are data, in ``voice-rules.json``. The seam itself did not
# move, and this module still owns the guard.
# ---------------------------------------------------------------------------
def _run_voice_pass_seam(html, voice_pass, *, protect=()):
    """Run a caller-supplied voice pass behind the factual diff guard."""
    revised = voice_pass(html)
    assert_no_factual_change(html, revised, protect=protect)
    return revised


# ===================== END SEAM ============================================


# ---------------------------------------------------------------------------
# The factual index and the diff guard.
# ---------------------------------------------------------------------------
def factual_index(html, *, protect=()):
    """The facts a rendered deck asserts, as a comparable index.

    ``numeric`` is the ORDERED list of every figure, date, time, week reference,
    URL, and email; order matters, since swapping two figures is a change the
    multiset would miss. ``entities`` is a multiset of the proper-noun tokens,
    excluding what a rule is ALLOWED to move: the words a vocabulary rule declares,
    and a lone plain Title-case word, which cannot be told from an ordinary
    capitalized sentence opening. Name those in ``protect`` if they matter.
    """
    numeric, entities = [], Counter()
    # The whole deck's copy as one string, because deleting a bold tag joins two
    # text nodes: indexed per node, that join would recompose a name run and read
    # as a changed entity. Joined, the input and the output are the same string
    # wherever only markup moved.
    _, spans = _mask(" ".join(node.text for node in _text_nodes(html)),
                     lexicon=VOCABULARY_LEXICON, protect=protect)
    for span in spans:
        if span.kind in NUMERIC_KINDS:
            numeric.append(unicodedata.normalize("NFKC", " ".join(span.text.split())).casefold())
        elif span.kind in ("entity", "declared"):
            for word in _indexable_entity_words(span):
                entities[word.casefold()] += 1
    return {"numeric": numeric, "entities": entities}


def _indexable_entity_words(span):
    """The words of a protected span that count as entity evidence."""
    words = [w for w in _words(span.text) if w.casefold() not in VOCABULARY_LEXICON]
    if not words or span.kind == "declared" or len(_words(span.text)) > 1:
        return words
    only = words[0]
    distinctive = (
        (only.isupper() and len(only) > 1)
        or any(ch.isupper() for ch in only[1:])
        or any(ch.isdigit() for ch in span.text)
    )
    return words if distinctive else []


def assert_no_factual_change(before, after, *, protect=()):
    """Refuse a rewrite that moved a fact. The hard constraint, checked.

    The ordered numeric index and the entity multiset must both be identical: no
    number, dollar figure, percentage, date, time, week reference, proper noun,
    or entity name may appear, disappear, change value, or change position.
    Raises ``FactualChangeError`` naming the difference.

    Then the same question is asked a second time by ``crude_fact_delta``, which
    shares no code with ``_mask``. The index above cannot catch a bug in masking,
    because it is built by masking; the crude check exists to go red when the
    index has gone blind. Both run before a render is returned, so either one
    refuses the deck rather than warning about it.
    """
    index_before = factual_index(before, protect=protect)
    index_after = factual_index(after, protect=protect)

    if index_before["numeric"] != index_after["numeric"]:
        left, right = index_before["numeric"], index_after["numeric"]
        detail = next(
            (f"position {i}: {a!r} became {b!r}" for i, (a, b) in enumerate(zip(left, right)) if a != b),
            f"dropped {left[len(right):]!r}" if len(left) > len(right) else f"added {right[len(left):]!r}",
        )
        raise FactualChangeError(
            f"the text gate changed a number, date, or figure, which it must never do: {detail}"
        )

    if index_before["entities"] != index_after["entities"]:
        b, a = index_before["entities"], index_after["entities"]
        delta = {k: (b.get(k, 0), a.get(k, 0)) for k in sorted(set(b) | set(a)) if b.get(k, 0) != a.get(k, 0)}
        raise FactualChangeError(f"the text gate changed a proper noun or entity name: {delta}")

    crude = crude_fact_delta(before, after)
    if crude is not None:
        raise FactualChangeError(
            f"the independent check saw a fact move that the factual index did not: {crude}"
        )
    return True
