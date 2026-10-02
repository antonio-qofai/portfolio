"""Which of slide 2's bullets matter most — a third pass, and the narrowest one.

Why it exists. `panel_fit` decides how many bullets a panel holds and trims the
list to that number. It trims from the TAIL, so which bullets survive is decided
entirely by the order they arrive in, and that order is an accident: the
deterministic parsers emit in document order and the second pass emits in whatever
order the model read the prose. On the deck of 2026-08-19 the TODAY panel held two
of seven, and the two it kept were simply the first two. Antonio asked for the pass
this module is: "it picks the most important after and before bullet points and
only keeps those."

WHAT MAKES THIS SAFE, AND IT IS STRUCTURAL RATHER THAN A PROMPT INSTRUCTION. This
pass returns INTEGERS. It is handed a numbered list and answers with an order; the
schema admits nothing but integers, and `read_response` reads nothing but integers.
There is no code path by which a character the model wrote can reach a deck. So the
whole apparatus the other two passes need — verbatim spans for the extractor, named
sections and proper-noun checks for the writer — is not weakened here, it is
unnecessary. A bullet that survives is byte-identical to the sourced string that
went in, because it IS that object, selected by index.

That is also why this is a separate module rather than a slot in `paper_writing`.
That pass moves SENTENCES THE MODEL WROTE and is governed accordingly; this one
moves no text at all and must never be filed beside it, or the distinction a
reviewer relies on stops meaning anything.

IT CANNOT LOSE OR DUPLICATE A BULLET EITHER. `read_response` does not trust the
answer to be a permutation. It drops indices that are out of range or repeated,
then appends every index the model did not name, in the order they arrived. So the
output is always exactly the input set, and the worst case of a bad answer is the
order the list already had. A refusal, a truncated response, an empty answer and an
outage all degrade to the same place: the paper's own order, which is what shipped
before this pass existed.

It does not decide how many bullets are shown. That is geometry and it belongs to
`panel_fit`. This pass is told nothing about how many will survive, deliberately:
asking a model to both rank and cut invites it to justify a cut, and the cut is not
a judgment, it is a measurement.

Not hardcoded to any client. Every bullet arrives from the packet and the only
other input is the slide's own headline and summary, for context about what this
engagement is arguing.
"""

import dataclasses
import json

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_MAX_TOKENS = 2000
DEFAULT_EFFORT = "medium"

# One attempt's read bound, and how many attempts. The narrowest leg in the
# pipeline and by far the cheapest: 2k tokens, medium effort, and 3 seconds on
# the measured live run of 2026-09-02. 60 seconds is 20x that, so the bound only
# ever fires on something genuinely wrong, and a call this cheap can afford
# three attempts where the render can afford two. Without a bound this leg
# inherited the same 600-second read as the render, which is a ten-minute wait
# for a three-second question nobody would have missed.
DEFAULT_TIMEOUT_S = 60.0
DEFAULT_ATTEMPTS = 3

# The two panels of slide 2, by the packet path whose list feeds each. The names
# are what the model answers with and what the ledger records.
TODAY = "today"
AFTER = "after"
PANEL_PATHS = {
    TODAY: "today_pain_points[]",
    AFTER: "target_capabilities[]",
}
PANEL_TITLES = {
    TODAY: "TODAY — the current-state problems this engagement addresses",
    AFTER: "AFTER — the capabilities the company would have once it ships",
}

RANKING_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["panels"],
    "properties": {
        "panels": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["panel", "order"],
                "properties": {
                    "panel": {"type": "string", "enum": [TODAY, AFTER]},
                    # Integers only. This is the schema-level half of the
                    # guarantee that no text the model wrote can reach a deck.
                    "order": {"type": "array", "items": {"type": "integer"}},
                },
            },
        },
    },
}

SYSTEM_PROMPT = """\
You are ordering the bullet points on one slide of a client proposal deck, for an
operating partner at a middle-market private equity firm.

You are NOT writing anything. You are not editing, shortening, merging, correcting
or rephrasing a single bullet. Every bullet you are given is a quotation that has
already been verified against a source document, and it will appear on the deck
exactly as written or not at all. Your entire job is to say what order they should
be considered in.

Why the order matters: the panel holds only as many bullets as physically fit, and
the ones at the end of your order are the ones that will not appear. So put first
the bullets a partner most needs to see.

What makes a bullet important here, strongest first:

1. It is specific and quantified. A bullet carrying a figure, a rate, a duration or
   a named system beats a bullet making the same point in general terms.
2. It bears on the case this slide is making. The headline and summary tell you
   what that case is; a bullet that supports it beats a true but tangential one.
3. It says something the rest of the slide does not. Two bullets making nearly the
   same point are worth less than one, so rank the weaker of the pair down.
4. It is about THIS company. A benchmark, a competitor's figure or an industry
   generality is context, and ranks below a fact about the company itself.

Rank DOWN, toward the end: bullets that restate the headline, bullets that describe
what the vendor will do rather than what the company gets, bullets whose meaning
depends on a sentence that is not on the slide, and bullets that are caveats about
data availability rather than findings.

Answer with one entry per panel. `order` is the bullet numbers, most important
first. Include every number you were given exactly once. Use the numbers as shown.
"""


@dataclasses.dataclass(frozen=True)
class Ranking:
    """An order per panel, plus what had to be repaired to get there.

    `orders` maps a panel name to a tuple of indices that is always a permutation
    of the input's own indices. `notes` records any repair, so a reviewer reading
    section 8 can tell a clean answer from a salvaged one.
    """

    orders: dict
    notes: tuple = ()

    def order_for(self, panel, count):
        """The order for `panel`, or the identity order when there is none."""
        order = self.orders.get(panel)
        if not order or sorted(order) != list(range(count)):
            return tuple(range(count))
        return tuple(order)


class RankingError(RuntimeError):
    """The ranking pass could not be read.

    Raised only for an answer that cannot be parsed at all. A merely BAD order is
    not an error: it is repaired into a valid one, because the fallback for this
    pass is the order the list already had and that is never worse than refusing.
    """


def apply_order(bullets, order):
    """`bullets` reordered by `order`, which must be a permutation of its indices."""
    bullets = list(bullets)
    if sorted(order) != list(range(len(bullets))):
        return bullets
    return [bullets[index] for index in order]


def build_request(panels, headline="", summary=""):
    """`(system, message)` for the ranking call.

    `panels` maps a panel name to its list of bullets. Only panels carrying more
    than one bullet are worth asking about, and the caller has already filtered
    for that.
    """
    blocks = [
        "<slide>",
        f"  <headline>{headline or ''}</headline>",
        f"  <summary>{summary or ''}</summary>",
        "</slide>",
    ]
    for panel, bullets in panels.items():
        blocks.append(f'<panel name="{panel}">')
        blocks.append(f"  <what_it_shows>{PANEL_TITLES.get(panel, panel)}</what_it_shows>")
        for index, bullet in enumerate(bullets):
            blocks.append(f"  <bullet number=\"{index}\">{bullet}</bullet>")
        blocks.append("</panel>")
    return SYSTEM_PROMPT, "\n".join(blocks)


def _answer(response):
    """The model's JSON object, or `RankingError`."""
    text = "".join(
        block.text for block in getattr(response, "content", ())
        if getattr(block, "type", None) == "text"
    )
    try:
        answer = json.loads(text)
    except (TypeError, ValueError) as error:
        raise RankingError(
            f"the ranking pass returned no readable JSON: {error}"
        ) from error
    if not isinstance(answer, dict):
        raise RankingError("the ranking pass returned no ranking object.")
    return answer


def _repair(panel, returned, count):
    """`(order, notes)` — a valid permutation of `range(count)`, whatever came back.

    Three things go wrong with a returned order and all three are repaired rather
    than refused, because the fallback is the order the list already had:
    an index outside the list, the same index twice, and an index simply left out.
    """
    order, seen, notes = [], set(), []
    dropped_range, dropped_repeat = [], []
    for value in returned or ():
        try:
            index = int(value)
        except (TypeError, ValueError):
            dropped_range.append(value)
            continue
        if not 0 <= index < count:
            dropped_range.append(index)
            continue
        if index in seen:
            dropped_repeat.append(index)
            continue
        seen.add(index)
        order.append(index)
    missing = [index for index in range(count) if index not in seen]
    order.extend(missing)
    if dropped_range:
        notes.append(f"{panel}: dropped {dropped_range!r}, not a bullet number "
                     f"on this panel (it has {count}).")
    if dropped_repeat:
        notes.append(f"{panel}: dropped {dropped_repeat!r}, returned more than once.")
    if missing:
        notes.append(f"{panel}: {missing!r} went unranked and kept the order they "
                     "arrived in, at the end.")
    return tuple(order), tuple(notes)


def read_response(panels, response):
    """Verify one model response against the panels it was asked about.

    Separated from the call so the whole verification half is testable with no
    client at all, and so a fake in a test exercises the same code a live run
    does rather than a shortcut around it.

    Reads INTEGERS and nothing else. No string in the response is ever copied into
    the result, which is what makes it impossible for this pass to put a word on a
    deck that was not already sourced.
    """
    answer = _answer(response)
    orders, notes = {}, []
    answered = set()
    for entry in answer.get("panels") or ():
        if not isinstance(entry, dict):
            continue
        panel = entry.get("panel")
        if panel not in panels:
            notes.append(f"ignored an order for {panel!r}, which is not a panel "
                         "this pass asked about.")
            continue
        answered.add(panel)
        order, repaired = _repair(panel, entry.get("order"), len(panels[panel]))
        orders[panel] = order
        notes.extend(repaired)
    for panel in panels:
        if panel not in answered:
            orders[panel] = tuple(range(len(panels[panel])))
            notes.append(f"{panel}: no order returned, so the bullets keep the "
                         "order they arrived in.")
    return Ranking(orders=orders, notes=tuple(notes))


def rank(panels, headline="", summary="", *, client=None, model=DEFAULT_MODEL,
         max_tokens=DEFAULT_MAX_TOKENS, effort=DEFAULT_EFFORT, api_key=None,
         timeout_s=DEFAULT_TIMEOUT_S, attempts=DEFAULT_ATTEMPTS):
    """Order each panel's bullets by importance. -> `Ranking`.

    `client` may be injected (anything exposing `messages.create(...)`); with none,
    a real `anthropic.Anthropic` is constructed, reading `ANTHROPIC_API_KEY` or the
    explicit `api_key`. Every test in this repo runs against an injected fake and
    none reaches the network.

    A panel with fewer than two bullets has nothing to order and is not sent. With
    no panel left to ask about, this returns an empty `Ranking` without calling
    anything, so a thin deck pays for no round trip.

    `timeout_s` bounds one attempt's read and `attempts` says how many it gets,
    on a client this function builds; an injected `client` keeps its own
    transport and gets one attempt. `run` below catches whatever this raises, so
    the bound decides how long a broken ranking call may delay a render that is
    going to happen either way. See `model_call`.
    """
    askable = {panel: list(bullets) for panel, bullets in (panels or {}).items()
               if bullets and len(bullets) > 1}
    if not askable:
        return Ranking(orders={panel: tuple(range(len(list(bullets or ()))))
                               for panel, bullets in (panels or {}).items()},
                       notes=())

    system, message = build_request(askable, headline, summary)
    import model_call  # lazy: keeps the pure pipeline dependency-free

    resolved = client
    if resolved is None:
        resolved = model_call.bounded_client(api_key, timeout_s=timeout_s)
    else:
        timeout_s, attempts = None, 1
    response = model_call.attempt(
        "ranking",
        lambda: resolved.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": RANKING_SCHEMA},
            },
            messages=[{"role": "user", "content": message}],
        ),
        attempts=attempts, timeout_s=timeout_s,
    )
    ranking = read_response(askable, response)
    # A panel too short to be worth asking about still needs an entry, so a
    # caller gets an order for every panel it handed over rather than having to
    # know which ones were sent.
    orders = dict(ranking.orders)
    for panel, bullets in (panels or {}).items():
        orders.setdefault(panel, tuple(range(len(list(bullets or ())))))
    return Ranking(orders=orders, notes=ranking.notes)


def make_ranker(**options):
    """Build the `(panels, headline, summary) -> Ranking` callable the provider takes.

    The one place the real ranking pass is turned on, mirroring
    `second_pass.make_extractor` and `make_writer`. `options` go through to
    `rank`: `client`, `api_key`, `model`, `max_tokens`, `effort`.

    Not cached. The other two passes read a paper that is the same across many
    fields; this one is asked once per render about the bullets that render, so a
    second call is a re-render rather than a second field.
    """

    def ranker(panels, headline="", summary=""):
        return rank(panels, headline, summary, **options)

    return ranker


def run(panels, headline="", summary="", ranker=None):
    """Rank if a ranker was supplied, else keep the order the bullets arrived in.

    The degradation seam, and the reason every caller can treat this pass as
    optional. No ranker, a ranker that raises, an unreadable answer: all three
    return the identity order with the reason recorded, and the deck renders
    exactly as it did before this pass existed.
    """
    counts = {panel: len(list(bullets or ()))
              for panel, bullets in (panels or {}).items()}
    identity = Ranking(orders={panel: tuple(range(count))
                               for panel, count in counts.items()})
    if ranker is None or not any(counts.values()):
        return identity
    try:
        return ranker(panels, headline, summary)
    except Exception as error:                      # noqa: BLE001 - see docstring
        # Deliberately broad, and this is the one place in the pipeline where that
        # is right. The pass is a nicety on top of a deck that already renders, so
        # no failure mode of it may sink a render. An SDK error, a timeout, a
        # schema refusal and a bug in this module all land here, are named in the
        # ledger, and leave the paper's own order standing.
        return Ranking(orders=identity.orders, notes=(
            f"the ranking pass failed ({type(error).__name__}: {error}), so the "
            "bullets keep the order they arrived in.",
        ))


def ledger(ranking, panels):
    """Section 8's record of what this pass did, or `None` when it did nothing.

    Reports the order per panel and any repair, so a reviewer can see that the
    bullets on the deck were chosen rather than taken off the top of the list, and
    can tell a clean answer from a salvaged one.
    """
    if ranking is None:
        return None
    entries = []
    for panel, order in sorted((ranking.orders or {}).items()):
        bullets = list((panels or {}).get(panel) or ())
        if len(bullets) < 2:
            continue
        entries.append({
            "panel": panel,
            "path": PANEL_PATHS.get(panel, panel),
            "order": list(order),
            "reordered": list(order) != list(range(len(bullets))),
        })
    if not entries:
        return None
    return {"panels": entries, "notes": list(ranking.notes or ())}
