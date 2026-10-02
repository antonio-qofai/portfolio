"""Extraction cache (E8): memoize extraction on a hash of the paper text.

Two legs now, one scheme. `assemble_cached` memoizes the deterministic pass
(`packet_assembly.assemble`); `extract_cached` memoizes the second pass
(`paper_extraction.extract`), added for E11 Stage 2. Both key on the paper text
rather than on the opportunity id, for the same reason, and there is deliberately
no third caching scheme anywhere in this repo.

Two reasons, both from the contract rather than from taste. The contract
promises that the same request plus the same `last_kg_refresh` produces the
same packet, and `assemble` is deterministic already, so the only thing a
cache can add here is not re-running the four parsers over 36KB to 48KB of
paper on every render. And keying on the paper text itself, not the
opportunity id, means a rewritten paper invalidates its own entry rather than
riding on a stale one.

This wraps extraction; it does not change what extraction returns. A cache hit
replays the cached packet's fields under the requested `opportunity_id`, since
two calls sharing paper text (the same opportunity, re-rendered) should not
also silently share whichever opportunity id happened to populate the cache
first.

THE REPLAY REACHES THE FIGURES NOW (item 15, 2026-09-13). Every figure names
the opportunity it was read for, so replaying a cached packet under a different
`opportunity_id` while leaving its figures naming the first one would hand the
second opportunity a packet whose every figure claims to belong to the first.
That is the hazard this replay already existed for, one field deeper: a
multi-opportunity run reads the SAME attached document once per opportunity, so
the deterministic cache is hit on every opportunity after the first, on every
run with an attachment. The replay is still the honest answer rather than a
second entry, because the two readings of a table genuinely are the same
reading; only whose they are differs.
"""

import dataclasses
import hashlib

import packet_assembly
import source_span

_cache = {}
_extractions = {}


def assemble_cached(opportunity_id, paper, document, opportunity=None):
    """`packet_assembly.assemble(...)`, cached on `paper` and its document.

    `document` is required here and defaulted in `assemble` itself, because this
    is the one path a live run takes: a caller reading something other than the
    published paper cannot reach assembly through here without saying so.

    It is in the KEY as well as in the call, and it has to be. The packet's
    figures now name their document, so two sources with identical text (a
    reviewer attaching a copy of the paper is the obvious way) would otherwise
    share an entry and the second one's figures would claim the first one's
    document. That is the same hazard the `opportunity_id` replay below fixes,
    one field along, and it is not a second caching scheme: it is this scheme
    keyed on everything the answer depends on.
    """
    key = (_key(paper), document)
    cached = _cache.get(key)
    if cached is None:
        cached = packet_assembly.assemble(opportunity_id, paper, document,
                                          opportunity)
        _cache[key] = cached
    if cached.opportunity_id == opportunity_id:
        return cached
    return _replayed(cached, opportunity_id, opportunity)


def _replayed(cached, opportunity_id, opportunity):
    """A cached packet as the requested opportunity's own, figures included.

    The packet's id and every figure's `opportunity` move together, because a
    packet whose id says one thing and whose figures say another is worse than
    either alone. `scenarios` hold figures inside their cases, so they are
    rebuilt too rather than carried across naming the first caller's
    opportunity.
    """
    opportunity = opportunity or source_span.Opportunity(id=opportunity_id)
    return dataclasses.replace(
        cached,
        opportunity_id=opportunity_id,
        fields=tuple(dataclasses.replace(figure, opportunity=opportunity)
                     for figure in cached.fields),
        scenarios=tuple(
            dataclasses.replace(
                case,
                direct_uplift_usd_yr=dataclasses.replace(
                    case.direct_uplift_usd_yr, opportunity=opportunity),
                margin_gain_pp=(
                    None if case.margin_gain_pp is None
                    else dataclasses.replace(case.margin_gain_pp,
                                             opportunity=opportunity)
                ),
            )
            for case in cached.scenarios
        ),
    )


def extract_cached(paper, requested, phase_labels=(), *, document,
                   opportunity, description="", **options):
    """`paper_extraction.extract(...)`, cached on `paper` and what was asked for.

    The same reason as above and one more that matters more here, because this
    leg costs an API call rather than four parsers: re-rendering the same
    opportunity should not re-read its paper. The key is the paper text plus the
    request, not the opportunity id, so a rewritten paper invalidates its own
    entry rather than riding a stale one, and a run asking for a DIFFERENT set of
    absent fields gets its own answer instead of a subset of somebody else's.

    `document` is in the key for the reason `assemble_cached` gives above: the
    records carry figures that name it, so two sources with the same text must
    not share an answer.

    `opportunity` IS IN THE KEY TOO, AND IT IS LOAD-BEARING (item 15,
    2026-09-13). Unlike assembly, this leg cannot be replayed: it reads PROSE
    for a SUBJECT, so the same document read for two opportunities gives two
    different answers rather than one answer belonging to two people. Without it
    a two-opportunity run with one attached PRD collides on every axis at once:
    the deterministic parse of that PRD is identical for both opportunities, so
    both packets leave the same paths absent, so `requested` matches, so the key
    matches, and both slide 2s come back from one call reading for one subject.
    Provenance would name the PRD on both and be telling the truth.

    `description` is NOT in the key and does not need to be. It is the
    platform's own statement of what the opportunity is, so it is a function of
    the opportunity, and the opportunity is in the key. It is also not an
    `option`: options configure how the same question is asked and are excluded
    deliberately, while this is part of the question.

    `options` (the client, the model, the effort) are deliberately not in the key.
    They configure how the same question is asked, and a caller that changes them
    mid-process and needs a fresh answer calls `clear()`.

    Imported inside the call so the pure pipeline stays importable with no SDK
    installed, which is the same reason `paper_extraction` imports `anthropic`
    lazily.
    """
    import paper_extraction

    key = (_key(paper), tuple(requested), tuple(phase_labels), document,
           opportunity)
    if key not in _extractions:
        _extractions[key] = paper_extraction.extract(
            paper, requested, phase_labels, document=document,
            opportunity=opportunity, description=description, **options
        )
    return _extractions[key]


def _key(paper):
    """A cache key on the paper text itself, so a rewrite invalidates its entry."""
    return hashlib.sha256((paper or "").encode("utf-8")).hexdigest()


def clear():
    """Reset the cache. For tests: each test should start from a cold cache."""
    _cache.clear()
    _extractions.clear()
