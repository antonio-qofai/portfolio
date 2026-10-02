"""Which opportunity each attached document is about, read from the document.

Why this exists. Antonio, 2026-09-22: "the plan needs to also make the agent
ready for PRD uploads with multiple opportunities, not just opportunity papers."
Until today every attachment joined every opportunity's chain, so a deck for two
opportunities with one PRD each was written twice from whichever PRD was
attached first, and the second PRD answered only what the first did not.

THE MAPPING IS A FACT ABOUT THE DOCUMENT'S OWN CONTENT. Every PRD in the corpus
opens with a labelled front-matter row naming its opportunity, and
`prd_front_matter` reads it without a model for the reason that module gives.
That title is matched against the platform's published list by the same
`match_opportunity` the Generate tab's scan uses, so the page and the run cannot
disagree about which opportunity a document names. Nothing here reads a
filename: the Contoso PRDs happen to be named after their opportunities,
and the corpus does not promise that.

`base_document.precedence` is untouched and still reads nothing about a
document's content. The caller asks this module which documents belong to an
opportunity and hands `precedence` only those.

A DOCUMENT IS ROUTED ONLY WHEN THE ANSWER IS UNAMBIGUOUS AND IN THIS DECK. It
names one published opportunity, exactly one matches, and that one is among the
opportunities this deck is for. Every other document (a supporting note, a PRD
naming nothing, one "not yet published", one matching none or several, one
naming an opportunity nobody picked) applies to the whole run, which is what
every attachment did before this module existed. So a document this module
cannot place is never dropped, and a run nothing can be placed in is exactly
the run that shipped.

The match runs against the platform's FULL published list rather than the
picked subset, deliberately. A loose containment match that is ambiguous across
the company's opportunities can look unique among the two a reviewer picked,
and routing on that would be deciding a tie by what happened to be selected.
"""

import dataclasses

import prd_front_matter


@dataclasses.dataclass(frozen=True)
class Route:
    """Where one document goes, and why, for the reviewer.

    `opportunity_id` is empty when the document applies to the whole run, and
    `reason` then says why it could not be placed. `stated` is the title the
    document itself wrote, so the screen can show what was read beside what it
    matched.
    """

    filename: str
    stated: str
    opportunity_id: str = ""
    opportunity_title: str = ""
    reason: str = ""

    @property
    def routed(self):
        return bool(self.opportunity_id)


def stated_opportunity(document):
    """`(title, selectable)` as the document's front matter states it."""
    stated = prd_front_matter.read(getattr(document, "text", "") or "")
    return stated["opportunity"], stated["opportunity_selectable"], stated


def needs_listing(documents):
    """Whether any document states a selectable opportunity worth matching.

    A caller holding no published list asks this first, so a run whose
    attachments name nothing costs no platform call to learn that.
    """
    return any(stated_opportunity(document)[1] for document in documents or ())


def route(documents, picked, listed):
    """One `Route` per document, in attach order.

    `documents` are `document_text.ExtractedDocument`s. `picked` are the
    opportunity details this deck is for, each with an `id` and a `title`.
    `listed` is the platform's published opportunities as `{id, label}`
    choices, the shape `opportunity_choices` returns, or None when nobody
    fetched them because nothing needed matching.
    """
    titles = {str(o.get("id") or ""): o.get("title") or "" for o in picked or ()}
    routes = []
    for document in documents or ():
        filename = getattr(document, "filename", "") or ""
        title, selectable, stated = stated_opportunity(document)
        if not title:
            routes.append(Route(filename, "", reason="names no opportunity"))
            continue
        if not selectable:
            routes.append(Route(filename, title, reason=(
                f"states it as {stated['opportunity_note'] or 'not published'}, "
                "so no published opportunity can match it")))
            continue
        match = prd_front_matter.match_opportunity(title, listed or ())
        if match is None:
            routes.append(Route(filename, title, reason=(
                "matches none or several of this company's published "
                "opportunities")))
            continue
        matched = str(match.get("id") or "")
        if matched not in titles:
            routes.append(Route(filename, title, reason=(
                f"matches “{match.get('label') or matched}”, which "
                "this deck is not for")))
            continue
        routes.append(Route(filename, title, opportunity_id=matched,
                            opportunity_title=titles[matched]))
    return tuple(routes)


def documents_for(documents, routes, opportunity_id):
    """The documents one opportunity's chain is built from, in attach order.

    Its own routed documents and every unrouted one. Attach order is kept,
    because `precedence` reads position as precedence and the reviewer set it.
    """
    return tuple(
        document for document, where in zip(documents or (), routes)
        if not where.routed or where.opportunity_id == opportunity_id
    )


def record(routes):
    """The routes as plain dicts, for an envelope and a screen."""
    return [dataclasses.asdict(where) for where in routes]
