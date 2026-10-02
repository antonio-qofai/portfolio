"""The GENERATED half of the second pass (E11 Stage 2c): sentences, not facts.

`paper_extraction` reads FACTS. Everything it returns is a quotation: a figure,
a metric, a phase, a capability, each carrying the verbatim span it came from,
each verified by `SourcedFigure` against the paper, each refused if the leaf's
own text does not occur inside its own span. That machinery is right and is not
touched here.

It does not fit the other half of what a deck needs. A slide headline, a cover
subtitle, a section subhead: nobody wrote those sentences in the research paper,
so there is no span to demand. Demanding one would either block the work
entirely or -- far worse -- push the model into copying some arbitrary sentence
out of the paper and calling it the span, which is a false provenance rather
than an absent one.

So a generated sentence gets its own rule, and it is not weaker than the
extraction rule, it is different. Four parts, and each of the first three is
checked mechanically after the model answers.

1. IT NAMES THE SECTIONS IT WAS WRITTEN FROM, and those sections must exist in
   that source. A section is resolved to the actual stretch of text under its
   own heading, so "written from Technical Requirements" is a claim about a
   region this module can point at, not a label.

2. IT MAY ONLY RESTATE THOSE SECTIONS. Enforced three ways rather than asked
   for. Every EVIDENCE span it supplies must occur inside one of the regions it
   named. Every NUMBER in the sentence must occur inside that evidence, so a
   figure in a generated sentence is span-verified exactly as an extracted
   figure is or the sentence is rejected whole. Every PROPER NOUN in the
   sentence -- a capitalised word that is not starting a sentence -- must occur
   in that evidence too, which is what stops an invented product, an invented
   party or an invented place.

   What this cannot catch, stated plainly rather than implied: a false CLAIM
   built entirely out of the source's own ordinary vocabulary. No mechanical
   check reaches that. It is why part 3 exists.

3. IT IS LABELLED AS GENERATED, distinct from extracted, in the packet's own
   section 8. A reviewer reads which sentences the model wrote and which the
   paper stated, at a glance, and can go to the named sections.

4. ABSENCE STAYS AVAILABLE. No source sections, no sentence: the deck keeps its
   existing deck-standard framing rather than acquiring engagement-specific
   framing nobody sourced. A refused sentence is a correct outcome.

Two sources, and a slot declares which one it may read. `PAPER` is the research
paper. `DESCRIPTION` is the opportunity's own description, which the packet
already parks verbatim in section 3 and which has no headings, so its whole text
is one region under a reserved name.

What is NOT here, deliberately. Slide 5's `terms_headline` and `terms_summary`
are not generated. QofAI's commercial terms are founder-set, no paper carries
them, the five commercial fields are designed to read AWAITING COMMERCIAL TERMS
INPUT, and writing engagement-specific framing around a slide whose data is
deliberately blank is the one thing that section of the deck must not do. They
keep their deck standard. See the 2026-08-19 CHANGELOG entry.

Not hardcoded to any client. Every slot is a deck copy role and every sentence
arrives from the source text the caller hands over.
"""

import dataclasses
import json
import re

# Same model and the same reasoning as `paper_extraction`: one place, and the
# model `voice_pass` is already on.
DEFAULT_MODEL = "claude-opus-5"
# 8000 stopped being enough the moment `copy.plan_summary` joined
# `WRITTEN_SCOPE` (2026-09-03). Instrumented on the real request that same day,
# twice, live WTG paper: `stop_reason: max_tokens`, output tokens exactly 8000,
# 3,447 characters of text, cut off mid-string inside the phase content it was
# still writing ("...Inventory-Reduction Program (Months 9-12)","Phase 1 process
# hardening must precede AI deploy'). Most of the budget goes on thinking, so the
# visible answer never got near finishing.
#
# The failure past the line is the same shape as the extraction pass's: `_answer`
# refuses an incomplete JSON body, `second_pass.write` catches it, and every
# framing line falls back to the deck's standard wording -- except
# `copy.opportunity_summary`, which has no standard fallback and renders as
# `[MISSING: opportunity_summary]` under slide 2's headline. Two live decks on
# 2026-09-03 shipped that placeholder and three slides of boilerplate where the
# 2026-08-26 deck had engagement-specific prose, and nothing in the studio said
# why: the reviewer sees a generic deck, the reason is in the server log.
DEFAULT_MAX_TOKENS = 24000
DEFAULT_EFFORT = "high"

# One attempt's read bound, and how many attempts. The 240 here was 4x a single
# 59-second sample, taken before this leg carried `plan_summary`; the
# instrumented calls above spent 84.5 seconds just reaching the old ceiling. At
# that rate a completion that actually uses the raised 24000 runs about 250
# seconds, so 300 covers one and still holds the ordering `test_model_bounds`
# requires: above ranking, at or below extraction, under the render.
DEFAULT_TIMEOUT_S = 300.0
DEFAULT_ATTEMPTS = 2

PAPER = "paper"
DESCRIPTION = "description"

# The reserved section name a DESCRIPTION-sourced sentence names, since an
# opportunity description carries no headings to resolve.
DESCRIPTION_SECTION = "opportunity.description"


class WritingError(ValueError):
    """The model's answer could not be read at all (not: a sentence was refused)."""


@dataclasses.dataclass(frozen=True)
class Sentence:
    """One deck copy role a sentence may be written for.

    `path` is the copy path section 8 records it under and `role` the template
    role it reaches. `limit` bounds the rendered length, because a headline that
    runs three lines is a layout failure the deck cannot absorb and the guard
    downstream would only find after rendering.
    """

    path: str
    role: str
    source: str
    limit: int
    guidance: str


# ITEM 27. The cap every written headline shares, 90 until 2026-09-19.
#
# PICKED AGAINST THE LINES THE BUILD ACTUALLY PRODUCED, not chosen as a round
# number. The five headlines written across the two live decks measured 61, 62,
# 68, 75 and 79 against the old 90, so none was ever refused and the cap did no
# work. The last two are three-clause lists ("Price the options, test the data,
# then put visibility in front of the plant"), which is the shape Antonio asked
# us to stop producing on 2026-09-18.
#
# 68 TO 74 is the whole window that refuses both lists while keeping all three
# single-idea lines, and the arithmetic is worth stating because the number has
# to be re-derivable. A cap keeps the 68-character keeper only at 68 or above,
# and refuses the 75-character list only at 74 or below.
#
# 70 sits near the middle of that window: two characters above the longest
# keeper, five below the shortest list. Not at 68, which would make the longest
# good headline exactly maximal and refuse an equally good one a word longer, and
# not at 74, which leaves only one character of margin under the shortest list.
#
# CORRECTED 2026-09-19, hours after being written, by the review window. This
# first read "66 to 70", which is wrong at both ends: at 66 or 67 the
# 68-character keeper is refused, and 71 to 74 are inside the window and were
# excluded. It came from sampling caps at 90, 75, 70, 68, 65 and 60 and reading
# the boundary off the gaps between samples instead of deriving it from the
# lengths. The cap itself was never wrong; the reasoning recorded next to it was,
# which is the more dangerous half to get wrong because it is what the next
# person reads before moving the number. `test_the_cap_window_is_what_the_
# comment_says` now pins these bounds so prose and arithmetic cannot drift apart
# again.
#
# The cap cannot make a line good, only make the list shape impossible to fit.
# The guidance below is what asks for one idea; this stops three from fitting.
HEADLINE_LIMIT = 70

# --------------------------------------------------------------------------
# WRITTEN_SCOPE — the copy roles a sentence may be written for, and no others.
# --------------------------------------------------------------------------
WRITTEN_SCOPE = (
    Sentence(
        path="copy.subtitle",
        role="subtitle",
        source=DESCRIPTION,
        limit=160,
        guidance="The cover's one-line subtitle, under the project title and "
                 "above nothing. Say what this engagement IS, in the "
                 "description's own terms. The cover already carries the "
                 "client's name and the project's title, so it must not "
                 "restate either: a subtitle that repeats the title back has "
                 "spent the only line the cover gives it. No figure, no date, "
                 "no promise about an outcome.",
    ),
    Sentence(
        path="copy.opportunity_summary",
        role="opportunity_summary",
        source=DESCRIPTION,
        limit=240,
        guidance="One line under the opportunity headline saying what the "
                 "opportunity IS. It sits above two columns that already carry "
                 "the today figures, the pain bullets, the after figures and "
                 "the capability bullets, so it must NOT restate them and must "
                 "never be a run-on of the bullet text: the reader meets every "
                 "one of those a centimetre below this line. Name the shape of "
                 "the opportunity, not its contents. One sentence. A "
                 "compression of the description, never an addition to it.",
    ),
    Sentence(
        path="copy.platform_headline",
        role="platform_headline",
        source=PAPER,
        limit=HEADLINE_LIMIT,
        # The standard this replaces is deliberately NOT quoted here. It used to
        # be, verbatim, and this was the only slot in the table that quoted its
        # own fallback: the prompt handed the model the exact sentence it was
        # told to beat, and a model that hands it straight back writes a line
        # that passes every check and renders identically to writing nothing.
        # Slide 3 of the 2026-09-15 live Northwind deck is what that looks like.
        # `second_pass.restates_deck_standard` now refuses that answer; this says
        # what the line is FOR so it does not get offered in the first place.
        guidance="A headline naming the ONE thing that makes this platform "
                 "particular, written from the sections that describe it: the "
                 "systems it joins, or the layer it adds, or the capability it "
                 "creates. Not all three. It sits above the numbered components, "
                 "so a line that only says a platform is being built restates "
                 "the slide's own title and is not worth having.",
    ),
    Sentence(
        path="copy.platform_summary",
        role="platform_summary",
        source=PAPER,
        limit=200,
        guidance="One sentence under that headline saying what the "
                 "platform does, written from the same sections. It sits above "
                 "the numbered components, each of which already carries its "
                 "own title and description, so it must NOT list them or walk "
                 "through them in order. Say what the components add up to: "
                 "what the platform does once they are all built.",
    ),
    Sentence(
        path="copy.plan_headline",
        role="plan_headline",
        source=PAPER,
        limit=HEADLINE_LIMIT,
        guidance="A headline naming the ONE thing this SCHEDULE is for, "
                 "written from the sections that describe the implementation. "
                 "The slide is the timeline and its kicker says so, so this "
                 "line answers the question the Gantt underneath does not: why "
                 "the work is sequenced this way. Not the arc of the work as a "
                 "story, not the first "
                 "phase's own title, which the build strip below already "
                 "carries, and not a list of what each phase does. Do not "
                 "state a duration or a date here; the plan's own horizon is a "
                 "separate field with its own source.",
    ),
    Sentence(
        path="copy.plan_summary",
        role="plan_summary",
        source=PAPER,
        limit=200,
        guidance="One sentence under that headline saying what the programme "
                 "does end to end, and nothing else. The phase count and the "
                 "milestone placement are both drawn on the Gantt this line "
                 "sits above, and the build strip below already lists each "
                 "phase's own detail, so it must NOT restate per-phase detail "
                 "and must never be a run-on of the phase notes. Written from "
                 "the sections that describe the implementation.",
    ),
    Sentence(
        path="copy.next_steps_headline",
        role="next_steps_headline",
        source=PAPER,
        limit=HEADLINE_LIMIT,
        guidance="A headline naming the ONE kind of work the next period is, "
                 "written from the sections that describe the actions. It sits "
                 "above the numbered actions, each carrying its own title, week "
                 "and owner, so it must NOT be the first action restated as a "
                 "heading, and it must not list them. Never name a person, a "
                 "party, a date or a commitment.",
    ),
    Sentence(
        path="copy.next_steps_summary",
        role="next_steps_summary",
        source=PAPER,
        limit=160,
        guidance="One line under that headline, framing the next period AS A "
                 "WHOLE: what it establishes and what it leaves ready. It sits "
                 "directly above the numbered actions, which already state "
                 "every one of them in full, so it must NOT enumerate them and "
                 "must never be a run-on of the action titles joined by commas, "
                 "which is this line's characteristic failure and reads as a "
                 "list the reader is about to be given anyway. One sentence. "
                 "Never name a person, a party, a date or a commitment.",
    ),
)

BY_PATH = {slot.path: slot for slot in WRITTEN_SCOPE}
PATHS = tuple(slot.path for slot in WRITTEN_SCOPE)

WRITING_SCHEMA = {
    "type": "object",
    "properties": {
        "sentences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "text": {"type": "string"},
                    "sections": {"type": "array", "items": {"type": "string"}},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["path", "text", "sections", "evidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["sentences"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """\
You write the framing sentences of one slide deck: a headline, a subhead, a \
cover subtitle. Everything else on the deck is quoted from the source and \
verified against it; these are the lines nobody wrote in the source, so you \
write them, under a rule of their own.

The rule is that you may RESTATE the source and may not ADD to it. Every one of \
the checks below runs mechanically after you answer, and a sentence that fails \
any of them is discarded and the deck keeps its standard framing instead, which \
is a correct outcome and not a failure.

1. NAME THE SECTIONS YOU WROTE FROM. `sections` is the source's own heading \
path or paths, for example `Technical Requirements` or `Financial Analysis > \
Current State`. A section that is not a real heading in the source discards the \
sentence.

2. QUOTE YOUR EVIDENCE. `evidence` is one or more stretches of the source \
copied character for character, and each one must sit INSIDE a section you \
named. This is what you restated. Copy it; do not retype it from memory.

3. ANY NUMBER YOU WRITE MUST APPEAR IN YOUR EVIDENCE, character for character. \
A figure in a framing line is held to the same standard as a figure anywhere \
else on the deck.

4. ANY CAPITALISED WORD THAT IS NOT STARTING A SENTENCE MUST APPEAR IN YOUR \
EVIDENCE. Do not name a product, a party, a place or a system the source does \
not name.

5. NEVER STATE A COMMITMENT. No owner, no date, no price, no fee, no schedule \
and no promise about a result. Those are claims about people and money, not \
restatements of a source.

6. RETURN NOTHING FOR A SLIDE THE SOURCE DOES NOT SUPPORT. Leaving a sentence \
out is the right answer whenever the sections you would need are missing or say \
nothing about that slide. A deck standard is better than an invention.

Write plainly and specifically. A sentence that could sit on any deck for any \
company is worth no more than the standard it replaces.\
"""


@dataclasses.dataclass(frozen=True)
class Written:
    """One verified generated sentence, and what it was written from."""

    path: str
    text: str
    sections: tuple
    evidence: tuple

    @property
    def role(self):
        return BY_PATH[self.path].role


@dataclasses.dataclass(frozen=True)
class Writing:
    """What one writing pass produced, and what it refused.

    `sentences` maps a copy path to its verified `Written`. `dropped` is
    `(path, reason)` pairs, the same shape `paper_extraction.Extraction` uses,
    because a refusal a reviewer cannot see is the same failure as a sentence
    with no source.
    """

    requested: tuple
    sentences: dict
    dropped: tuple

    @property
    def paths(self):
        return tuple(path for path in self.requested if path in self.sentences)


# --------------------------------------------------------------------------
# Section resolution: a named heading becomes a region of the source.
# --------------------------------------------------------------------------

_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$", re.MULTILINE)
_SPLIT_SECTION = re.compile(r"\s*(?:>|/|»)\s*")
_DIGITS = re.compile(r"\d[\d,.]*")
_WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*")
_OPENS_A_SENTENCE = ".!?:;\"'“”(—-–"


def headings(text):
    """Every markdown heading in `text` as `(level, title, start, end)`.

    `end` is where the heading's own region stops: the next heading at the same
    level or shallower, or the end of the text. That is what makes "written from
    Technical Requirements" checkable rather than decorative.
    """
    matches = list(_HEADING.finditer(text or ""))
    found = []
    for index, match in enumerate(matches):
        level = len(match.group(1))
        end = len(text or "")
        for later in matches[index + 1:]:
            if len(later.group(1)) <= level:
                end = later.start()
                break
        found.append((level, match.group(2).strip(), match.start(), end))
    return found


def _normalised(title):
    """A heading title as it compares: no emphasis marks, no case, one space."""
    return " ".join(re.sub(r"[*_`#]", "", str(title)).split()).lower()


def resolve_section(text, path, source=PAPER):
    """`(start, end)` for one named section path, or None.

    Every segment of the path must be a real heading in the source, and the
    LAST one supplies the region. Where the path names ancestors, a candidate
    nested inside one of them is preferred, so `Financial Analysis > Current
    State` resolves under its own parent on a paper carrying `Current State`
    twice.

    A DESCRIPTION-sourced slot has no headings to resolve, so it names the one
    reserved section and gets the whole text.
    """
    if source == DESCRIPTION:
        return (0, len(text or "")) if _normalised(path) == DESCRIPTION_SECTION else None
    segments = [segment for segment in _SPLIT_SECTION.split(str(path)) if segment.strip()]
    if not segments:
        return None
    found = headings(text)
    by_title = {}
    for level, title, start, end in found:
        by_title.setdefault(_normalised(title), []).append((level, start, end))
    for segment in segments:
        if _normalised(segment) not in by_title:
            return None
    candidates = by_title[_normalised(segments[-1])]
    for segment in reversed(segments[:-1]):
        nested = [
            (level, start, end) for level, start, end in candidates
            if any(parent_start <= start < parent_end
                   for _l, parent_start, parent_end in by_title[_normalised(segment)])
        ]
        if nested:
            candidates = nested
            break
    _level, start, end = candidates[0]
    return start, end


# --------------------------------------------------------------------------
# The request and the call.
# --------------------------------------------------------------------------

def build_request(paper, description, requested):
    """The system blocks and the user message for one writing call.

    Both sources cross WHOLE and both are marked for prompt caching, the same
    way `paper_extraction.build_request` sends the paper: a framing line is
    written from whatever the source actually says, and section-picking here
    would be picking the answer.
    """
    asked_for = set(requested)
    slots = [slot for slot in WRITTEN_SCOPE if slot.path in asked_for]
    body = {
        "framing_lines_the_deck_has_no_source_for": [
            {
                "path": slot.path,
                "read_from": ("the opportunity description"
                              if slot.source == DESCRIPTION
                              else "the research paper"),
                "sections_must_name": (
                    [DESCRIPTION_SECTION] if slot.source == DESCRIPTION
                    else "the paper's own heading paths"
                ),
                "at_most_characters": slot.limit,
                "what_this_line_is": slot.guidance,
            }
            for slot in slots
        ]
    }
    return (
        [
            {"type": "text", "text": SYSTEM_PROMPT},
            {"type": "text",
             "text": "<research_paper>\n" + (paper or "") + "\n</research_paper>\n"
                     "<opportunity_description>\n" + (description or "")
                     + "\n</opportunity_description>",
             "cache_control": {"type": "ephemeral"}},
        ],
        json.dumps(body, indent=2, sort_keys=True),
    )


def write(paper, description, requested, *, client=None, model=DEFAULT_MODEL,
          max_tokens=DEFAULT_MAX_TOKENS, effort=DEFAULT_EFFORT, api_key=None,
          timeout_s=DEFAULT_TIMEOUT_S, attempts=DEFAULT_ATTEMPTS):
    """Write `requested` framing lines off `paper` / `description`. -> `Writing`.

    `client` may be injected (anything exposing `messages.create(...)`); with
    none, a real `anthropic.Anthropic` is constructed, reading `ANTHROPIC_API_KEY`
    or the explicit `api_key`. Every test in this repo runs against an injected
    fake and none reaches the network.

    An empty `requested`, or two empty sources, returns an empty `Writing`
    without calling anything.

    `timeout_s` bounds one attempt's read and `attempts` says how many it gets,
    on a client this function builds; an injected `client` keeps its own
    transport and gets one attempt. See `model_call`.
    """
    requested = tuple(dict.fromkeys(requested))
    known = tuple(path for path in requested if path in BY_PATH)
    dropped = [
        (path, "not a generated copy path; `paper_writing.WRITTEN_SCOPE` "
               "declares which framing lines may be written.")
        for path in requested if path not in BY_PATH
    ]
    if not known or not ((paper or "").strip() or (description or "").strip()):
        return Writing(requested=requested, sentences={}, dropped=tuple(dropped))

    system, message = build_request(paper, description, known)
    import model_call  # lazy: keeps the pure pipeline dependency-free

    resolved = client
    if resolved is None:
        resolved = model_call.bounded_client(api_key, timeout_s=timeout_s)
    else:
        timeout_s, attempts = None, 1
    response = model_call.attempt(
        "writing",
        lambda: resolved.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            thinking={"type": "adaptive"},
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": WRITING_SCHEMA},
            },
            messages=[{"role": "user", "content": message}],
        ),
        attempts=attempts, timeout_s=timeout_s,
    )
    return read_response(paper, description, known, response, dropped)


def read_response(paper, description, requested, response, dropped=()):
    """Verify one model response against its sources. Returns a `Writing`.

    Separated from the call so the whole verification half is testable with no
    client at all, and so a fake in a test exercises the same code a live run
    does rather than a shortcut around it.
    """
    dropped = list(dropped)
    answer = _answer(response)
    sentences = {}
    for entry in answer.get("sentences") or ():
        path = entry.get("path")
        slot = BY_PATH.get(path)
        if slot is None or path not in requested:
            dropped.append((str(path), _unasked(path, requested)))
            continue
        if path in sentences:
            dropped.append((path, "the deck has one line here and the pass "
                                  "wrote more than one; the extras are not "
                                  "merged."))
            continue
        source = paper if slot.source == PAPER else description
        written, why = _sentence(source, slot, entry)
        if written is None:
            dropped.append((path, why))
            continue
        sentences[path] = written
    return Writing(requested=tuple(requested), sentences=sentences,
                   dropped=tuple(dropped))


def _unasked(path, requested):
    if path in BY_PATH and path not in requested:
        return ("this line already has a source, or was never absent; the "
                "writing pass fills absences only.")
    return ("not a generated copy path; `paper_writing.WRITTEN_SCOPE` declares "
            "which framing lines may be written.")


def _answer(response):
    """The model's JSON, or `WritingError`."""
    text = "".join(
        block.text for block in getattr(response, "content", ())
        if getattr(block, "type", None) == "text"
    )
    try:
        answer = json.loads(text)
    except (TypeError, ValueError) as error:
        raise WritingError(
            f"the writing pass returned no readable JSON: {error}"
        ) from error
    if not isinstance(answer, dict):
        raise WritingError("the writing pass returned no sentence object.")
    return answer


def _sentence(source, slot, entry):
    """One verified `Written`, or `(None, reason)`. Every check is literal."""
    text = entry.get("text")
    if not isinstance(text, str) or not text.strip():
        return None, "was returned empty."
    text = " ".join(text.split())
    if len(text) > slot.limit:
        return None, (f"runs {len(text)} characters against a {slot.limit} "
                      "limit for this line, and a headline that wraps is a "
                      "layout failure the deck cannot absorb.")

    named = [str(name) for name in (entry.get("sections") or []) if str(name).strip()]
    if not named:
        return None, ("named no source section, and a generated sentence with "
                      "no stated source is exactly what this pass refuses.")
    regions, unresolved = [], []
    for name in named:
        region = resolve_section(source, name, slot.source)
        if region is None:
            unresolved.append(name)
        else:
            regions.append(region)
    if unresolved:
        return None, (f"named {unresolved!r}, which is not a section of this "
                      "source, so there is nothing it can have been written "
                      "from.")

    evidence = [str(span) for span in (entry.get("evidence") or []) if str(span).strip()]
    if not evidence:
        return None, ("quoted no evidence from the sections it named, so what "
                      "it restated cannot be checked.")
    for span in evidence:
        placed = source.find(span) if isinstance(source, str) else -1
        if placed < 0:
            return None, ("quoted evidence that does not occur in the source: "
                          f"{_shortened(span)!r}")
        if not any(start <= placed and placed + len(span) <= end
                   for start, end in regions):
            return None, ("quoted evidence from outside the sections it named: "
                          f"{_shortened(span)!r}")
    quoted = " ␟ ".join(evidence)

    for number in _DIGITS.findall(text):
        if number not in quoted:
            return None, (f"states {number!r}, which does not occur in the "
                          "evidence it quoted. A number in a generated sentence "
                          "is held to the same span check as a number anywhere "
                          "else on the deck.")
    for name in _proper_nouns(text):
        if name not in quoted:
            return None, (f"names {name!r}, which the sections it quoted do not "
                          "name, so it is an addition to the source rather "
                          "than a restatement of it.")
    return Written(path=slot.path, text=text, sections=tuple(named),
                   evidence=tuple(evidence)), None


def _proper_nouns(text):
    """Capitalised words that are not opening a sentence, in order.

    The fabrication surface a restatement check can actually reach: an invented
    product, party, place or system arrives capitalised. Ordinary vocabulary is
    left alone deliberately, because requiring every word of a generated
    sentence to occur in the source would refuse every sentence worth having and
    would still not catch a false claim built out of the source's own words.
    That last case is what the reviewer and the `generated` label are for.
    """
    found = []
    for match in _WORD.finditer(text):
        word = match.group()
        if not word[:1].isupper():
            continue
        before = text[:match.start()].rstrip()
        if not before or before[-1] in _OPENS_A_SENTENCE:
            continue
        found.append(word)
    return found


def _shortened(text, limit=80):
    collapsed = " ".join(str(text).split())
    return collapsed if len(collapsed) <= limit else collapsed[:limit] + "..."
