"""The second extraction pass (E11 Stage 2): a model reading the whole paper.

The first pass is deterministic and stays exactly as it is. Every extractor in
this repo before today was a parser reading one of two machine-readable shapes,
a Chart.js `<Chart>` config and a markdown table, so everything a paper states in
PROSE went unread and was then classified `UNSOURCEABLE`, a class whose own
reason says "no tool in the grant returns this field" -- a claim about structured
tool output, not about the research paper, which is in the grant and which the
parsers already read for charts and tables.

This module reads the rest. It is a general second pass over the packet, not a
patch for one slide: `SCOPE` below is a table of packet paths, and a path is
added to it by declaring one, not by writing code.

What makes this safe is not the model's care. It is three mechanical properties,
each enforced here and none of them a convention to remember.

1. QUOTATION, NOT COMPOSITION. Every extracted leaf carries the verbatim span it
   came from, `SourcedFigure` refuses to be constructed when the span is not
   literally in the paper (`MissingSpanError`), and this module additionally
   requires each leaf's own text to occur inside its own span. So the pass can
   only quote the paper. A summary, a rounding, a combination of two sections'
   figures, or an invented number fails at construction and never reaches a
   packet, let alone a deck. There is no bypass, no default and no
   optional-span path, for any field or for tests.

2. ONLY WHAT WAS ASKED FOR. `extract` takes the set of absent paths and returns
   only those; a path that was not requested is dropped with a reason. The
   precedence rule (`second_pass`) is what computes that set and what merges the
   answer, and it drops again on the way in, so a value can reach a packet only
   if the deterministic pass left its field empty at both moments.

3. NO CONVERSION HERE. A leaf's stated text is read into the packet's shape by
   the SAME readers the deterministic pass uses -- `scenario_table_parser._money`
   (as `packet_assembly.read_usd`), `packet_assembly.read_percent`,
   `scenario_table_parser.read_pp`. The model hands over the paper's own
   notation; the arithmetic that turns `$57.7M` into a `(low, high)` pair is the
   same code path E7a already ran on parser output. Months are never turned into
   weeks and no unit is normalised: `unit` is a closed enum whose stem must occur
   in its own span.

What this does NOT close, stated plainly because nothing here can. Span
verification is a check against FABRICATION. MISATTRIBUTION -- a value that
really is in the paper, placed in the wrong field -- verifies fine and no
automated check in this pipeline catches it. That is why every item also records
the SECTION HEADING it was read under, and why `second_pass` flags a roster value
taken from a scenario, projection, benchmark, competitor or vendor section rather
than a current-state one. The reviewer decides; this module does not
second-guess the reading with heuristics.

Not hardcoded to any client. `SCOPE` is keyed on schema paths, the paper arrives
as an argument, and the system prompt names no company, no sector and no figure.
"""

import dataclasses
import json

import packet_assembly
import scenario_table_parser
from source_span import MissingSpanError, SourcedFigure

# One place, per the no-hardcoding rule, and the same model `voice_pass` is on.
# `deck_renderer` and `html_edit_interpreter` still pin `claude-opus-4-8` and are
# deliberately left alone.
DEFAULT_MODEL = "claude-opus-5"

# `max_tokens` bounds thinking plus response together. The response is spans
# quoted out of a 15KB-48KB paper across up to ten paths, so this is sized for
# headroom rather than to an expected answer. Adaptive thinking is the only
# on-mode on this model and `budget_tokens` is removed (a 400); effort is the
# lever instead.
#
# 16000 was too close to the line and was sized before anyone measured the pass
# under its real request shape. Two faithful calls on 2026-09-03, same 29,202-
# character WTG paper, same schema and adaptive thinking, returned `end_turn` at
# 15,078 and 11,545 output tokens: the slower one spent 94% of the ceiling. The
# failure past it is not a short answer, it is a truncated one, which `_answer`
# refuses as unreadable JSON and `second_pass.run` turns into a packet with
# nothing added -- the 0.33 refusal on a paper that states every figure. A paper
# at the top of the 48KB range has no room at all at 16000. Raising it costs
# nothing on a run that does not need it, since a completion is billed for what
# it emits, and the headroom is only ever spent on the case that used to fail
# outright. 24000 rather than more because the ceiling and the read bound below
# are coupled: at the observed rate a completion that spends the whole ceiling
# takes about 345 seconds, and `test_model_bounds` requires this leg to stay
# under the render's 420. A pass that genuinely needs more than that wants the
# ordering premise revisited, not a bigger number smuggled in under it.
DEFAULT_MAX_TOKENS = 24000
DEFAULT_EFFORT = "high"

# One attempt's read bound, and how many attempts. This is the longest of the
# three non-render legs. The 300 seconds here was set against a single 102-second
# sample and the claim that "a healthy pass never comes near it" did not survive
# more of them: two faithful calls on 2026-09-03 took 107.8s and 216.3s, the
# second one 72% of that bound, and both returned `end_turn` with nine of twelve
# fields verified. So the bound was not generous, it was one slow-but-healthy run
# away from firing. Raised with the ceiling above it: at the observed ~70 output
# tokens per second a completion that actually uses the raised 24000-token
# ceiling runs about 345 seconds, so 400 covers one while staying inside both
# rules `test_model_bounds` holds this to: under the render's bound, and under
# the 600 a bare client would have inherited anyway. Bounding it at all is
# what matters here, because this pass is the one whose silent failure is worst:
# `second_pass.run` catches everything it raises and hands back the
# deterministic packet, so an unbounded stall used to cost ten minutes and then
# present itself to the reviewer as a thin research paper.
DEFAULT_TIMEOUT_S = 400.0
DEFAULT_ATTEMPTS = 2

# Leaf readers. `text` is the paper's own string carried across unchanged; the
# other four hand the string to the deterministic pass's own reader, so no
# arithmetic in this module is new.
TEXT = "text"
USD = "usd"
PERCENT = "percent"
PP = "pp"
NUMBER = "number"
UNIT = "unit"
# A leaf that is a MATCHING KEY rather than a claim about the paper. Its job is
# to say which phase a summary belongs to, it is never rendered (the merge keeps
# the phase's own label), and it is checked against the labels the FIRST pass
# already read rather than against the span. Requiring it inside its own span is
# the wrong check and a measurably costly one: on a live paper 2026-08-18 all
# three phase summaries were refused because the model quoted the scope sentence
# as the span and named the phase from its heading, which is the correct reading
# and the correct span. Membership in the first pass's own labels is both
# stricter (it cannot name a phase that does not exist) and right.
KEY = "key"

# The two units the corpus states. E11's 12-paper survey (2026-08-18) found
# months in 12 of 12 and weeks in 5 of 12. Nothing is converted between them.
UNITS = ("weeks", "months")


class ExtractionError(ValueError):
    """The model's answer could not be read at all (not: a field came back empty)."""


@dataclasses.dataclass(frozen=True)
class Leaf:
    """One leaf of a slot: its name, its reader, and whether it must be present.

    `required` is about the RECORD's shape, not about the paper: a record whose
    required leaf did not verify is dropped whole, because a scenario case with
    no dollar figure or a phase with no label is not a partial record, it is a
    different thing. `applies` is the other half of Stage 1's marker rule: a leaf
    that applies to a record's shape and simply was not obtained carries its key
    with an empty value, so `prompt_assembler._render_records` marks it, while a
    leaf that does not apply is omitted and says nothing.
    """

    name: str
    reader: str
    required: bool = False
    applies: bool = True


@dataclasses.dataclass(frozen=True)
class Slot:
    """One extractable packet path.

    `leaves` declares the record's shape; a slot whose only leaf is `value` is a
    scalar or a list of scalars depending on `many`. `sections` is the paper
    structure the corpus survey found this material under, passed to the model as
    orientation and never used to pre-select text: the whole paper goes over,
    because section-picking is what loses information.
    """

    path: str
    leaves: tuple
    many: bool
    sections: str
    guidance: str

    @property
    def names(self):
        return tuple(leaf.name for leaf in self.leaves)

    def leaf(self, name):
        for leaf in self.leaves:
            if leaf.name == name:
                return leaf
        return None


# --------------------------------------------------------------------------
# SCOPE — the packet paths this pass may fill, and nothing else.
#
# The five commercial reviewer fields are absent from this table on purpose and
# are not an oversight: QofAI's per-deal pricing is founder-set, 0 of 21
# published papers carry it, and Antonio stated on 2026-08-18 that he cannot
# supply it. It is a question for Casey and Jordan, never an extraction target.
#
# `subtitle` and `opportunity_summary` are absent too. They are a compression
# job rather than an extraction job, the source description runs 730 to 1,772
# characters, it is already parked verbatim in packet section 3, and it belongs
# under F1's diff guard.
#
# `target_metrics[].label` is absent by Antonio's ruling of 2026-08-15: it is a
# unit-naming string for a figure `get_opportunity_details` returns without one,
# and it stays a judgment for him rather than one this pass takes.
# --------------------------------------------------------------------------
SCOPE = (
    Slot(
        path=packet_assembly.REVENUE,
        leaves=(Leaf("value", USD, required=True),),
        many=False,
        sections="Financial Analysis > Current State",
        guidance="The company's own trailing revenue for the stated baseline "
                 "period. Not a projection, not a budget, not a competitor's, "
                 "not a market size, and not a segment of the total.",
    ),
    Slot(
        path=packet_assembly.EBITDA,
        leaves=(Leaf("value", USD, required=True),),
        many=False,
        sections="Financial Analysis > Current State",
        guidance="The company's own adjusted EBITDA for the same baseline "
                 "period as the revenue above. Not a projection or a budget.",
    ),
    Slot(
        path=packet_assembly.MARGIN,
        leaves=(Leaf("value", PERCENT, required=True),),
        many=False,
        sections="Financial Analysis > Current State",
        guidance="The company's own EBITDA margin for that baseline period, as "
                 "one percentage the paper states. A cell stating two "
                 "percentages is two periods, not a range: return nothing.",
    ),
    Slot(
        path="commercial.scenarios",
        leaves=(
            Leaf("name", TEXT, required=True),
            Leaf("direct_uplift_usd_yr", USD, required=True),
            Leaf("margin_gain_pp", PP),
        ),
        many=True,
        sections="Financial Analysis > Projected Impact",
        guidance="One record per NAMED impact case the paper presents as a "
                 "case (conservative / base / optimistic and their spellings). "
                 "A total, a subtotal, a per-value-stream line and a payback "
                 "table are not cases: returning one as a case restructures "
                 "the paper. If the paper states no named cases, return none.",
    ),
    Slot(
        path=packet_assembly.TIMELINE,
        leaves=(
            Leaf("label", TEXT, required=True),
            Leaf("start", NUMBER, required=True),
            Leaf("end", NUMBER, required=True),
            Leaf("unit", UNIT, required=True),
        ),
        many=True,
        sections="Implementation Approach > Timeline",
        guidance="One record per implementation phase, in the paper's own "
                 "order. `start` and `end` are the numbers the paper itself "
                 "states for that phase and `unit` is the unit it states them "
                 "in. Never convert months to weeks or weeks to months, and "
                 "never re-base one phase's numbers onto another's.",
    ),
    Slot(
        path="today_metrics",
        leaves=(Leaf("value", TEXT, required=True), Leaf("label", TEXT)),
        many=True,
        sections="Financial Analysis > Current State, The Operational Problem",
        guidance="A metric describing the company as it is TODAY, `value` the "
                 "figure exactly as written and `label` the paper's own name "
                 "for it. Never a target, a projection or a benchmark.",
    ),
    Slot(
        path="today_pain_points",
        leaves=(Leaf("value", TEXT, required=True),),
        many=True,
        sections="Financial Analysis > Current State, The Operational Problem",
        guidance="One entry per operational problem the paper states the "
                 "company has today, quoted from the paper's own sentence.",
    ),
    Slot(
        path="target_metrics",
        leaves=(Leaf("value", TEXT, required=True),),
        many=True,
        sections="Financial Analysis > Projected Impact, The Proposed Solution",
        guidance="A metric describing the company AFTER the work, as the paper "
                 "states it. Never a current-state figure.",
    ),
    Slot(
        path="target_capabilities",
        leaves=(Leaf("value", TEXT, required=True),),
        many=True,
        sections="Projected Impact, The Proposed Solution, Technical Requirements",
        guidance="One entry per capability the company would have after the "
                 "work, quoted from the paper's own sentence.",
    ),
    Slot(
        path="build_summary.duration",
        leaves=(
            Leaf("value", NUMBER, required=True),
            Leaf("unit", UNIT, required=True),
        ),
        many=False,
        sections="Implementation Approach > Timeline, The Proposed Solution",
        guidance="How long the WHOLE implementation runs, as one number and "
                 "the unit the paper states it in. Not one phase's length, not "
                 "a payback period, and not a contract term. Never converted: "
                 "a paper stating months returns months. A paper stating a "
                 "range rather than one number states no single duration; "
                 "return nothing.",
    ),
    Slot(
        path="platform_layers",
        leaves=(
            Leaf("kicker", TEXT),
            Leaf("title", TEXT, required=True),
            Leaf("body", TEXT),
        ),
        many=True,
        sections="The Proposed Solution, Technical Requirements",
        guidance="One record per COMPONENT of the platform this engagement "
                 "would build: a capture layer, a data store, an integration, "
                 "a rules engine, a reporting surface. `title` is the paper's "
                 "own naming of that component and `body` the paper's own "
                 "sentence saying what it does. `kicker` is the paper's own "
                 "category word for it where it states one, and is left out "
                 "where it does not. A benefit, a figure, a phase of the plan "
                 "and a piece of the client's existing stack are not "
                 "components. If the paper describes no platform, return none: "
                 "the deck then keeps its standard layers.",
    ),
    Slot(
        path="next_steps",
        leaves=(
            Leaf("title", TEXT, required=True),
            Leaf("body", TEXT),
            Leaf("owner", TEXT),
            Leaf("week", TEXT),
        ),
        many=True,
        sections="The Proposed Solution, Implementation Approach, Next Steps",
        guidance="One record per action the paper states has to happen to "
                 "START this work: an approval, a signature, a decision, a "
                 "prerequisite, a data hand-over. `title` is the action in the "
                 "paper's own words and `body` the paper's own sentence about "
                 "it. `owner` is a party the paper itself names as responsible "
                 "and `week` a timing the paper itself states; leave either "
                 "out rather than supplying one the paper does not state, "
                 "because an owner and a date are commitments about people. A "
                 "phase of the build plan is not a next step. If the paper "
                 "states no next steps, return none: the deck then keeps its "
                 "standard actions.",
    ),
    Slot(
        path="build_summary.phases[].summary",
        leaves=(
            Leaf("label", KEY, required=True),
            Leaf("summary", TEXT, required=True),
        ),
        many=True,
        sections="The Proposed Solution, Technical Requirements, "
                 "Implementation Approach > Timeline",
        guidance="What each phase DOES, quoted from the paper. `label` must "
                 "repeat one of the phase labels listed in the request "
                 "verbatim, so the summary lands on the right phase; a summary "
                 "under any other label is dropped.",
    ),
)

BY_PATH = {slot.path: slot for slot in SCOPE}
PATHS = tuple(slot.path for slot in SCOPE)

# The response shape, so every field comes back as an object carrying its value,
# its verbatim span and its section heading rather than as prose to re-parse. One
# uniform shape across every slot: a scalar slot returns at most one item, a
# record slot one item per record, a scalar list one item per entry.
EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "fields": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "leaves": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string"},
                                            "text": {"type": "string"},
                                        },
                                        "required": ["name", "text"],
                                        "additionalProperties": False,
                                    },
                                },
                                "span": {"type": "string"},
                                "section": {"type": "string"},
                                # Which opportunity this item is about (item 15).
                                # DECLARED HERE AS WELL AS ASKED FOR IN THE
                                # PROMPT, and the two going out of step is what
                                # broke every live extraction for a day: this
                                # object sets `additionalProperties: False`, so
                                # a key the prompt requires and the schema does
                                # not is a key the model CANNOT emit under
                                # structured output. Every item then arrived
                                # with no attribution, `_record` refused every
                                # one of them, and the pass returned nothing on
                                # every document of every run. See the guard in
                                # `tests/test_paper_extraction.py`, which reads
                                # the keys `_record` requires off the parsed
                                # source and asserts each one is emittable.
                                "about": {"type": "string"},
                            },
                            "required": ["leaves", "span", "section", "about"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["path", "items"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["fields"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """\
You read one research paper and report what it states, for a data packet whose \
every value must be traceable to the paper's own words.

You are the SECOND of two passes. The first is a set of deterministic parsers \
that already read every chart configuration and every markdown table. You are \
asked only for fields that pass left empty, and only those fields are listed in \
the request. Report a field that is not listed and it is discarded.

The rules, and every one of them is checked mechanically after you answer.

1. QUOTE, DO NOT WRITE. Each item carries `span`, a stretch of the paper copied \
character for character. A span that does not occur in the paper is discarded, \
and so is the value with it. Copy it; do not retype it from memory.

2. EACH LEAF'S TEXT MUST OCCUR INSIDE ITS OWN SPAN, character for character. So \
a leaf is a fragment of the paper, never a paraphrase, never a summary, never a \
rounding, never two sections' figures combined, and never a unit converted.

3. RETURN NOTHING FOR A FIELD THE PAPER DOES NOT STATE. An empty result is a \
correct and common outcome. A field left out is recorded as absent and shown to \
a reviewer as a gap, which is the intended behaviour. Filling one with something \
close is the single worst thing you can do here.

4. SAY WHERE IT CAME FROM. `section` is the paper's own heading path for the \
place you read the item, for example `Financial Analysis > Current State`. A \
reviewer uses it to check the value landed in the right field, which is the one \
error class no check here can catch.

5. THE FIELD'S OWN MEANING GOVERNS. A figure about a competitor, a vendor, a \
third-party benchmark, a market, a scenario or a projection is not a figure \
about this company today. When the paper states several candidates, prefer the \
one the field asks for and leave the field empty if none of them is it.

6. READ FOR ONE OPPORTUNITY. The request names the one opportunity this reading \
is for. A document may describe several: a PRD written for an engagement often \
covers every opportunity in it, and a value that belongs to a different one \
belongs on a different slide. Each item carries `about`, which is the named \
opportunity copied exactly as the request writes it. RETURN NOTHING YOU CANNOT \
ATTRIBUTE TO THE NAMED OPPORTUNITY. An item whose `about` is missing is \
discarded, and so is one naming anything else; leaving a field empty because \
the document states it only for another opportunity is a correct outcome and \
rule 3 already covers what to do about it.

Numbers keep the paper's own notation: `$57.7M` stays `$57.7M`, `46.7%` stays \
`46.7%`, `1.9pp` stays `1.9pp`. Downstream code does the arithmetic.\
"""


@dataclasses.dataclass(frozen=True)
class Extracted:
    """One verified record for one packet path.

    `leaves` maps leaf name to the value its reader produced, already in the
    packet's own shape. `stated` keeps the paper's own strings beside it, and
    `span` and `section` are what a reviewer reads. `figure` is the
    `SourcedFigure` whose construction verified the span; it is kept so a
    consumer merging into a `Packet` does not build a second one.
    """

    path: str
    leaves: dict
    stated: dict
    span: str
    section: str
    figure: SourcedFigure


@dataclasses.dataclass(frozen=True)
class Extraction:
    """What one second pass produced, and what it refused.

    `records` maps a packet path to its verified records in the model's own
    order. `dropped` is `(path, reason)` pairs, in the same shape
    `source_span.MissingFields` uses, because a refusal a reviewer cannot see is
    the same failure as a value with no source.
    """

    requested: tuple
    records: dict
    dropped: tuple

    @property
    def paths(self):
        return tuple(path for path in self.requested if self.records.get(path))


def build_request(paper, requested, phase_labels=(), *, opportunity=None,
                  description=""):
    """The system blocks and the user message for one extraction call.

    The paper goes over WHOLE, in its own block, marked for prompt caching: the
    same paper is read for every field in one call and re-read whenever a second
    call is made against it, and section-picking is exactly what loses the
    information this pass exists to recover. The volatile half -- which paths are
    absent this run -- sits in the user message, after the cache breakpoint.

    THE SUBJECT IS VOLATILE TOO, so it goes in the user message beside the
    absent paths rather than in the cached block (item 15). That is the point of
    putting it there: on a multi-opportunity run the same document is read once
    per opportunity, so the cached half is identical across those calls and the
    subject is the whole of what differs. Putting it in the cached block would
    either break the cache for every call or, worse, share one opportunity's
    subject with the next.

    `description` is the platform's own statement of what the opportunity is,
    and it is here rather than on `source_span.Opportunity` because it is prompt
    CONTENT: the one real record measured 2026-08-15 runs 1,772 characters, and
    what the figure carries is provenance. It is also the disambiguator that
    does the actual work, since two opportunities on one engagement can have
    titles a reader cannot tell apart out of context.
    """
    # `SCOPE` order rather than the caller's, so the same absent set composes
    # the same request however it was assembled.
    asked_for = set(requested)
    slots = [slot for slot in SCOPE if slot.path in asked_for]
    asked = [
        {
            "path": slot.path,
            "one_item_only": not slot.many,
            "leaves": [
                {"name": leaf.name,
                 "required": leaf.required,
                 "notation": _notation(leaf.reader)}
                for leaf in slot.leaves
            ],
            "usually_found_under": slot.sections,
            "what_this_field_is": slot.guidance,
        }
        for slot in slots
    ]
    body = {"fields_absent_from_the_packet": asked}
    if opportunity is not None:
        body["read_this_paper_for_this_opportunity_only"] = {
            "name": opportunity.label,
            "copy_this_name_into_every_item_about": opportunity.label,
            "what_this_opportunity_is": description or "",
        }
    if phase_labels:
        # The only cross-field key in the request, and it is a key rather than
        # content: a phase summary has to land on the phase the first pass
        # already named, or it lands on the wrong bar of the plan.
        body["phase_labels_the_first_pass_already_read"] = list(phase_labels)
    return (
        [
            {"type": "text", "text": SYSTEM_PROMPT},
            {"type": "text", "text": "<research_paper>\n" + (paper or "")
                                     + "\n</research_paper>",
             "cache_control": {"type": "ephemeral"}},
        ],
        json.dumps(body, indent=2, sort_keys=True),
    )


def _notation(reader):
    """What a leaf's text has to look like, in the paper's own terms."""
    return {
        TEXT: "the paper's own words, copied",
        KEY: "one of the phase labels listed in this request, repeated exactly",
        USD: "a dollar figure exactly as the paper writes it, e.g. $57.7M",
        PERCENT: "one percentage exactly as the paper writes it, e.g. 46.7%",
        PP: "a percentage-point figure as the paper writes it, e.g. 1.9pp",
        NUMBER: "a number the paper states, digits only",
        UNIT: "weeks or months, whichever the paper states",
    }[reader]


def extract(paper, requested, phase_labels=(), *, document, opportunity,
            description="", client=None,
            model=DEFAULT_MODEL, max_tokens=DEFAULT_MAX_TOKENS,
            effort=DEFAULT_EFFORT, api_key=None,
            timeout_s=DEFAULT_TIMEOUT_S, attempts=DEFAULT_ATTEMPTS):
    """Read `requested` packet paths off `paper`. Returns an `Extraction`.

    `document` is which document `paper` is (`source_span.Document`) and
    `opportunity` is which opportunity it is being read FOR
    (`source_span.Opportunity`), both keyword-only and both required, because
    every record out of here carries a `SourcedFigure` and a figure that cannot
    say where it came from, or which slide it belongs on, is not one.

    The second matters more on this leg than on any parser. A deterministic
    parser reads a table, and a table in a shared document is the same table
    whichever opportunity is being served. This leg reads PROSE, and prose in a
    document describing two opportunities says different things about each, so
    the reading genuinely differs and the figure has to say which reading it
    was.

    `client` may be injected (anything exposing `messages.create(...)`); when
    omitted a real `anthropic.Anthropic` is constructed and reads
    `ANTHROPIC_API_KEY` from the environment, or the explicit `api_key`. Every
    test in this repo runs against an injected fake and none reaches the network.

    An empty `requested`, or a paper with no text, returns an empty `Extraction`
    without calling anything: a run where the deterministic pass already filled
    everything should cost nothing.

    `timeout_s` bounds one attempt's read and `attempts` says how many it gets,
    on a client this function builds; an injected `client` keeps its own
    transport and gets one attempt. Exhausting the attempts raises
    `model_call.ModelCallError`, which names the bound and the count -- and which
    `second_pass.run` records rather than swallows silently. See `model_call`.
    """
    requested = tuple(dict.fromkeys(requested))
    known = tuple(path for path in requested if path in BY_PATH)
    dropped = [
        (path, "not an extractable packet path; `paper_extraction.SCOPE` "
               "declares what this pass may fill.")
        for path in requested if path not in BY_PATH
    ]
    if not known or not (paper or "").strip():
        return Extraction(requested=requested, records={}, dropped=tuple(dropped))

    system, message = build_request(paper, known, phase_labels,
                                    opportunity=opportunity,
                                    description=description)
    import model_call  # lazy: keeps the pure pipeline dependency-free

    resolved = client
    if resolved is None:
        resolved = model_call.bounded_client(api_key, timeout_s=timeout_s)
    else:
        timeout_s, attempts = None, 1
    response = model_call.attempt(
        "extraction",
        lambda: resolved.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            thinking={"type": "adaptive"},
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": EXTRACTION_SCHEMA},
            },
            messages=[{"role": "user", "content": message}],
        ),
        attempts=attempts, timeout_s=timeout_s,
    )
    return read_response(paper, known, response, dropped, phase_labels,
                         document=document, opportunity=opportunity)


def attributed_to(about, opportunity):
    """Whether an item's `about` names `opportunity`.

    Normalised on whitespace and case and compared for equality, which is all
    this can be: the model is handed the exact string to copy and rule 1 already
    makes this pass a copying pass, so echoing one given string is the easiest
    thing asked of it anywhere in this module.

    `Opportunity.label` is never empty, since the id is required and is what the
    label falls back to, so there is always something to match against and this
    needs no case for a subject that cannot name itself.
    """
    return _normalised(about) == _normalised(opportunity.label)


def _normalised(text):
    return " ".join(str(text or "").split()).casefold()


def read_response(paper, requested, response, dropped=(), phase_labels=(),
                  *, document, opportunity):
    """Verify one model response against the paper. Returns an `Extraction`.

    Separated from the call so the whole verification half is testable without a
    client at all, and so a fake in a test exercises the same code a live run
    does rather than a shortcut around it.
    """
    dropped = list(dropped)
    answer = _answer(response)
    records = {}
    for field in answer.get("fields") or ():
        path = field.get("path")
        slot = BY_PATH.get(path)
        if slot is None or path not in requested:
            dropped.append((str(path), _unasked(path, requested)))
            continue
        kept = []
        for item in field.get("items") or ():
            record, why = _record(paper, slot, item, phase_labels,
                                  document=document,
                                  opportunity=opportunity)
            if record is None:
                dropped.append((path, why))
                continue
            if not slot.many and kept:
                dropped.append((path, "the paper states one value for this "
                                      "field and the pass returned more than "
                                      "one; the extras are not merged."))
                break
            kept.append(record)
        if kept:
            records[path] = tuple(kept)
    return Extraction(
        requested=tuple(requested), records=records, dropped=tuple(dropped)
    )


def _unasked(path, requested):
    if path in BY_PATH and path not in requested:
        return ("the deterministic pass already sourced this field, or it was "
                "never absent; the second pass fills absences only.")
    return ("not an extractable packet path; `paper_extraction.SCOPE` declares "
            "what this pass may fill.")


def _answer(response):
    """The model's JSON, or `ExtractionError`.

    Structured outputs constrain the shape, so a failure here is a transport or
    a truncation rather than a formatting slip, and guessing at half an answer
    would be exactly the invention this pass forbids.

    AND THE REFUSAL CARRIES WHAT SPLITS THE TWO (2026-09-13). A truncated answer
    and a malformed one raise the same `json.JSONDecodeError` and are different
    bugs: one wants a bigger ceiling and the other wants a look at the request.
    `stop_reason` says which, the usage says how much room was actually spent,
    and the length of the body says where it stopped. All three go into the
    message, so the record `second_pass` keeps of a failed pass is already the
    measurement rather than a prompt to go and take one on a later run.

    This was not academic. A live two-opportunity run on 2026-09-13 cut at
    character 5,098, which is roughly 1,300 tokens of response against a 24,000
    ceiling. The obvious reading, that the answer outgrew the ceiling, does not
    fit that number on its own: `max_tokens` bounds THINKING PLUS RESPONSE
    together, so the same figure is equally consistent with the budget going to
    thinking and with the stream stopping for a reason that is not the ceiling
    at all. Raising a number against the first reading would be sizing against a
    guess.
    """
    text = "".join(
        block.text for block in getattr(response, "content", ())
        if getattr(block, "type", None) == "text"
    )
    try:
        answer = json.loads(text)
    except (TypeError, ValueError) as error:
        raise ExtractionError(
            f"the extraction pass returned no readable JSON: {error}"
            f" [{_measured(response, text)}]"
        ) from error
    if not isinstance(answer, dict):
        raise ExtractionError("the extraction pass returned no field object.")
    return answer


def _measured(response, text):
    """What the answer actually cost, for a refusal that has to say which bug.

    Read through `getattr` throughout, because an injected fake in a test is a
    two-line stand-in with a `content` list and nothing else, and a diagnostic
    that crashed on the object it is diagnosing would be worse than none.
    """
    usage = getattr(response, "usage", None)
    parts = [f"stop_reason={getattr(response, 'stop_reason', None)!r}",
             f"body_chars={len(text)}"]
    for name in ("input_tokens", "output_tokens"):
        value = getattr(usage, name, None)
        if value is not None:
            parts.append(f"{name}={value}")
    return ", ".join(parts)


def _record(paper, slot, item, phase_labels=(), *, document, opportunity):
    """One verified record, or `(None, reason)`.

    The span is verified by constructing a `SourcedFigure`, which is the repo's
    own mechanism rather than a second copy of it, and every leaf's own text is
    then required to occur inside that span. Both checks are literal.

    THE SUBJECT CHECK (item 15) IS NEITHER OF THOSE, AND SAYING SO IS THE POINT.
    No mechanical check can decide whether a stretch of prose is ABOUT one
    opportunity or another; that judgment is the model's, which is exactly why
    it is now told which opportunity it is reading for. What this adds is that
    the judgment has to be STATED, per item, and that two of its outcomes are
    refusals rather than merges: an item that attributes itself to nothing is
    discarded, and so is one that attributes itself elsewhere. That is the
    golden rule arriving at a seam where it was previously unstated, and the
    second case is the more useful of the two, because it gives the pass a way
    to say "the document states this, and it belongs to your other opportunity"
    instead of choosing between dropping it silently and putting it on the
    wrong slide.

    The order of the checks is deliberate and unchanged where it matters. The
    span is verified FIRST, so a fabricated span is still refused as a
    fabrication rather than as a mis-attribution, and the subject decides only
    what is asked for and never what verification means.
    """
    span = item.get("span")
    section = (item.get("section") or "").strip()
    about = item.get("about")
    if not isinstance(span, str) or not span.strip():
        return None, "an extracted value arrived with no source span."
    if span not in paper:
        return None, ("an extracted value arrived with a span that does not "
                      "occur in the paper, so the value is discarded: "
                      f"{_shortened(span)!r}")
    if not section:
        return None, ("an extracted value arrived with no section heading, and "
                      "a reviewer cannot check placement without one: "
                      f"{_shortened(span)!r}")
    if not str(about or "").strip():
        return None, ("an extracted value arrived attributed to no opportunity, "
                      f"so it is discarded: {_shortened(span)!r}")
    if not attributed_to(about, opportunity):
        return None, ("an extracted value was attributed to "
                      f"{_shortened(str(about))!r} and this reading is for "
                      f"{opportunity.label!r}, so it is discarded rather than "
                      f"placed on another opportunity's slide: "
                      f"{_shortened(span)!r}")
    leaves, stated, refused = {}, {}, {}
    for entry in item.get("leaves") or ():
        name, text = entry.get("name"), entry.get("text")
        leaf = slot.leaf(name) if isinstance(name, str) else None
        if leaf is None:
            continue
        value, why = _read(leaf, text, span, phase_labels)
        if value is None:
            refused[name] = why
            continue
        leaves[name] = value
        stated[name] = text.strip()
    for leaf in slot.leaves:
        if leaf.required and leaf.name not in leaves:
            return None, (f"an extracted {slot.path} record was dropped whole "
                          f"rather than filled in: its {leaf.name} "
                          + refused.get(leaf.name, "was not stated at all."))
    try:
        figure = SourcedFigure(slot.path, leaves, span, paper, document,
                               opportunity)
    except MissingSpanError as error:
        return None, str(error)
    return Extracted(
        path=slot.path, leaves=leaves, stated=stated, span=span,
        section=section, figure=figure,
    ), None


def _read(leaf, text, span, phase_labels=()):
    """`(value, None)` for a leaf that verifies, else `(None, why)`.

    Two DIFFERENT refusals live here and a reviewer needs to tell them apart,
    which is why the reason comes back rather than a bare None. Measured on a
    live paper 2026-08-18: the model quoted `$3.571 million` correctly, the span
    verified, the text really was inside it, and the figure still did not enter,
    because `packet_assembly.read_usd` read the `$3.571M` suffix notation and
    not the spelled-out word. Reporting that as "no value verifies against its
    own span" blames the model for a limit of the reader, and sent one
    investigation down the wrong path before this reason was split.

    SUPERSEDED 2026-08-19. That specific finding is fixed, not just reported:
    E11 Stage 2b widened all three readers to the spelled magnitudes and spelled
    units, so `$3.571 million`, `12.4 percent` and `1.9 percentage points` now
    read. The split reason below is what stays, because a reader can never
    accept every notation and the next narrow case must still name the reader
    rather than the span. What the readers still refuse is a figure stated inside
    supporting prose, which is the residue check Stage 2b deliberately left
    alone. See the 2026-08-19 entry in `CHANGELOG.md`.

    The literal-occurrence check is what makes this pass a quoting pass. `UNIT`
    is the one leaf whose text is a closed word rather than a fragment, so it is
    checked by stem, case-insensitively, against its own span -- a phase labelled
    `Months 1-3` states its unit in a capital and a singular.
    """
    if not isinstance(text, str) or not text.strip():
        return None, "was returned empty."
    text = text.strip()
    if leaf.reader == UNIT:
        unit = text.lower()
        if unit not in UNITS:
            return None, f"is {text!r}, which is not one of {UNITS}."
        if unit[:-1] not in span.lower():
            return None, (f"is {text!r}, which its own span does not state; "
                          "units are carried, never converted.")
        return unit, None
    if leaf.reader == KEY:
        # No labels to key against means the first pass read no phases at all,
        # so there is nothing to match and the span check is what is left.
        if phase_labels and text not in phase_labels:
            return None, (f"is {_shortened(text)!r}, which is not one of the "
                          "phase labels the first pass read, so there is no "
                          "phase for it to belong to.")
        if not phase_labels and text not in span:
            return None, (f"is {_shortened(text)!r}, which neither matches a "
                          "phase the first pass read nor occurs in its own "
                          "span.")
        return text, None
    if text not in span:
        return None, (f"is {_shortened(text)!r}, which does not occur inside "
                      "its own span, so it is a paraphrase rather than a "
                      "quotation.")
    if leaf.reader == TEXT:
        return text, None
    reader = {USD: packet_assembly.read_usd,
              PERCENT: packet_assembly.read_percent,
              PP: scenario_table_parser.read_pp,
              NUMBER: _number}.get(leaf.reader)
    value = reader(text) if reader else None
    if value is None:
        return None, (f"is {text!r}, which the deterministic pass's own "
                      f"{leaf.reader} reader does not read as one figure. The "
                      "quotation is sound; the notation is one this repo's "
                      "reader does not accept, and widening it would change "
                      "what the FIRST pass reads on every paper.")
    return value, None


def _number(text):
    """A plain number the paper states, or None. No arithmetic and no rounding."""
    try:
        value = float(text.replace(",", ""))
    except ValueError:
        return None
    return int(value) if value.is_integer() else value


def _shortened(text, limit=80):
    """One line of a span, for a reason a reviewer reads in a gaps list."""
    collapsed = " ".join(str(text).split())
    return collapsed if len(collapsed) <= limit else collapsed[:limit] + "..."
