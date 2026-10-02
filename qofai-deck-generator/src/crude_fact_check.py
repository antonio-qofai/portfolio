"""A second factual check that shares no code with the masking layer.

WHY THIS EXISTS. The text gate protects facts by hiding them behind placeholders
before any rule runs (`_mask` in text_gate.py), and the audit that confirms no
fact changed builds its comparison from `factual_index`, which calls that same
`_mask`. So a bug in masking blinds the protection and the audit together, and
every test still passes. That happened: making the entity loop inside `_mask` a
no-op let rules reach names AND stopped the index counting names, and the suite
stayed green.

This module is the second opinion. It imports nothing from text_gate, knows
nothing about spans, placeholders, the vocabulary lexicon, or what a caller
declared protected, and it is deliberately cruder than the real index. Its only
job is to go red when the real index has gone blind, so it is written to be
impossible to blind in the same way: plain regexes over the raw document.
"""

import re
from collections import Counter

_ENTITY_REF = re.compile(r"&#?\w+;")          # &mdash; and &#8212; carry digits
_TAGS = re.compile(r"<[^>]*>")
_FIGURE = re.compile(r"\$?\d+(?:,\d{3})*(?:\.\d+)?%?")
_NAME_RUN = re.compile(r"[A-Z][A-Za-z]*(?:\s+[A-Z][A-Za-z]*)+")


def _visible(html):
    """The document's copy, crudely.

    A tag becomes a space, because markup can sit inside a name and removing it
    joins the words either side. A character reference becomes a non-space mark,
    because it is punctuation standing between words: spelling `&mdash;` as a
    space would read "Scope &mdash; Build" as the name "Scope Build" and then
    call the em dash rewrite a lost name. The mark also takes the digits in
    `&#8212;` out of the figure count, where they were never a figure.
    """
    return _ENTITY_REF.sub("~", _TAGS.sub(" ", html))


def crude_fact_delta(before, after):
    """Describe a factual difference between two documents, or return None.

    Figures are compared in order, because swapping two is a change a multiset
    would miss. Names are compared as a multiset of the words inside runs of two
    or more capitalized words, which is the crudest thing that still sees a name:
    a lone capitalized word cannot be told from a sentence opening, and counting
    those would flag every legitimate vocabulary rewrite.
    """
    b, a = _visible(before), _visible(after)

    figures_before, figures_after = _FIGURE.findall(b), _FIGURE.findall(a)
    if figures_before != figures_after:
        pair = next((f"{x!r} became {y!r}" for x, y in zip(figures_before, figures_after) if x != y), None)
        return f"figure {pair}" if pair else f"figure count went {len(figures_before)} to {len(figures_after)}"

    names_before, names_after = _run_words(b), _run_words(a)
    if names_before != names_after:
        delta = {word: (names_before.get(word, 0), names_after.get(word, 0))
                 for word in sorted(set(names_before) | set(names_after))
                 if names_before.get(word, 0) != names_after.get(word, 0)}
        return f"entity name {delta}"
    return None


def _run_words(text):
    return Counter(word for run in _NAME_RUN.findall(text) for word in run.split())
