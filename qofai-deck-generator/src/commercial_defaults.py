"""QofAI's standing commercial language, and what may be defaulted at all.

A proposal deck's Commercial Terms slide renders five reviewer-supplied regions,
and two of them say the same thing on every engagement: the payment MECHANISM
(measured monthly, converted to EBITDA, share paid that month) and the
risk-reversal clause that backs it. A reviewer typing those two by hand on every
deck is typing QofAI's own boilerplate from memory, which is slower than a button
and worse than one, because memory drifts and a deck is a client-facing document.

So the studio offers one click that writes both. Antonio, 2026-08-20: "I want a
default payment, 'How Payment Works,' to be an option on the deck ... after the
deck is generated, someone can press 'Default, How Payment Works,' and then it
just appears on the deck."

WHAT IS DEFAULTABLE, AND WHY THE LINE SITS WHERE IT DOES

Mechanism only. The FBK deck's own third step read "QofAI receives the year's
share (20% / 10% / 5%) of that figure — paid monthly until the 2.5x cap or the
3-year term", and every number in that sentence is a term of one deal. A default
carrying them would put last deal's economics onto this deal's slide, arriving
with the settled look of a considered figure rather than the visible
`[MISSING: ...]` marker that is the only thing standing between a reviewer and
exactly that mistake. Asked directly, Antonio's answer was mechanism only.

The same rule excludes client detail. The default goes onto a deck for any client,
so it says "the client" and names no company or asset. FBK's wording said "project
& fleet EBITDA margin"; the fleet is FBK's.

The figures the mechanism refers to stay reviewer input, unchanged: QofAI
investment, client up-front, the comp schedule, the scenario table, the footnote.
This module makes two regions one click instead of two typing jobs. It does not
reduce what a human has to decide.

WHY THE TEXT IS A FILE

`templates/commercial-defaults.json`. It is content, and content edited in a
string literal is content nobody reviews as text; the file can be read and changed
by whoever owns the wording without touching the code that writes it. It sits in
`templates/` because it is keyed by the proposal template's own role names
(`downside_protection`, `payment_mechanics`), which is what makes it deck content
rather than a rule about deck content -- `src/voice-rules.json` and
`src/vocabulary-rules.json` are the latter, and they live with the code that
enforces them.

Loading is strict. A malformed or missing file raises rather than degrading to a
partial default, because half of this text is not a lesser version of it: a blue
box promising the client that QofAI earns nothing without improvement, above a
payment block that never says how payment is measured, is a worse slide than the
markers it replaced.
"""

import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULTS_PATH = os.path.join(_HERE, "..", "templates", "commercial-defaults.json")

# The roles this module can supply, which is deliberately not every sensitive role
# on the slide. Named here so a caller can ask what is defaultable without
# reading the file, and so a role quietly added to the JSON does not become
# defaultable by accident.
DEFAULTABLE = ("downside_protection", "payment_mechanics")


class DefaultsUnavailable(Exception):
    """The standing commercial language could not be read, so nothing is offered.

    Raised rather than returning a partial default: see the module docstring on
    why half of this text is worse than none of it.
    """


def load_defaults(path=None):
    """Read the standing commercial language.

    Returns ``{"downside_protection": str, "payment_mechanics": [{"lead", "rest"},
    ...]}``. Keys beginning with ``_`` are commentary in the file and are dropped.

    Every step is a ``lead`` and a ``rest`` rather than one sentence, because the
    slide renders the lead phrase bold and splitting a sentence to find that
    boundary is guesswork the file can simply state. It also means the emphasis is
    reviewed with the wording, in the same place.
    """
    path = path or DEFAULTS_PATH
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError) as exc:
        raise DefaultsUnavailable(
            f"the standing commercial language could not be read from {path}: {exc}"
        ) from exc

    if not isinstance(raw, dict):
        raise DefaultsUnavailable(f"{path} does not hold an object")

    downside = raw.get("downside_protection")
    if not isinstance(downside, str) or not downside.strip():
        raise DefaultsUnavailable(
            f"{path} carries no `downside_protection` line")

    steps = raw.get("payment_mechanics")
    if not isinstance(steps, list) or not steps:
        raise DefaultsUnavailable(f"{path} carries no `payment_mechanics` steps")
    clean_steps = []
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            raise DefaultsUnavailable(
                f"{path}: payment step {index} is not an object")
        lead = (step.get("lead") or "").strip()
        rest = (step.get("rest") or "").strip()
        if not lead or not rest:
            raise DefaultsUnavailable(
                f"{path}: payment step {index} needs both a `lead` and a `rest`")
        clean_steps.append({"lead": lead, "rest": rest})

    return {"downside_protection": downside.strip(),
            "payment_mechanics": clean_steps}
