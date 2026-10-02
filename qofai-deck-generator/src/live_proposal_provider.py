"""Live proposal data provider: company/project resolution and the KG gate.

The first leg of Stage E (`data-provider/PRD.md` §4.6 steps 1-3), sitting beside
`qofai_mcp_client.py`. Takes a live `QofaiMcpClient` (or any object with the same
`call_tool_json` method) and resolves the contract's `company` / `project`
references against the real registry, per
`proposal-data-request-CONTRACT.md` §3.

The seam itself lands at the bottom of this file (E9d, 2026-08-15):
`LiveProposalProvider` implements the same `submit(request)` / `poll(handle)`
pair `FixtureProvider` does, and runs the legs above in order before handing the
assembled document back as a contract envelope. Nothing upstream of the seam
moves and `FixtureProvider` is untouched.

Field-name assumption, kept in one place per the Stage E rules: `list_companies`
returns `id`, `name`, `has_kg` and `list_projects` returns `id`, `name` on each
record; both are read only through the small helpers below.

Corrected 2026-08-15 against the first live call this module ever made. Both
tools return MORE than that. `list_companies` also returns `status`, and
`list_projects` also returns `company_id`, `created_at`, `description`, `status`
and `type`. Neither is a mismatch: every read here goes through a named key, so
an extra key is read past harmlessly and a tool that grows one does not break
this module. The assumption above is a floor on what must be present, not a
claim about what the whole record holds.

One granted-tool limitation, not a bug: the contract's step 1 ("UUID given ->
direct lookup, skip resolution") has no tool to implement it against. Neither
`list_companies` nor `list_projects` accepts a lookup by id, only a name search
(and `list_projects` only a `company_id` filter, no name search at all, so
project matching happens client-side). So every reference here resolves by name
through search; a UUID reference is out of scope for this window and would
resolve as not-found rather than being looked up directly. Worth raising as a
finding if a caller ever needs to pass a UUID.

That caller arrived on 2026-09-15 (item 17, the ambiguous-company picker), and
the limitation above is still exactly true: nothing here looks an id up. What a
picked `company_id` does instead is NARROW the name's own search to one of its
results, which needs the name as well as the id and so never stands alone. See
`resolve_company`. A request carrying a UUID and no name still resolves as
not-found, and the finding stands.
"""

import collections
import datetime
import math
import re

import base_document
import completeness_score
import document_routing
import document_text
import extraction_cache
import flag_audit
import bullet_ranking
import packet_document
import packet_fill
import attachment_record
import prd_loyalty_guard
import prd_section_parsers
import provenance_report
import second_pass as second_pass_module
import source_span
from data_source_adapter import map_packet


class ProviderError(Exception):
    """One of the contract's stop-and-escalate codes (contract §4, §5).

    Carries the same four fields as a contract error envelope, so a caller
    builds the envelope's `error` block directly from `code`, `message`,
    `remediation`, `details` rather than re-deriving them.
    """

    def __init__(self, code, message, remediation, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.remediation = remediation
        self.details = details or {}


def candidate_of(record):
    """One row of an `E_AMBIGUOUS_COMPANY` candidate list, from a registry record.

    Stated once because two things build it: the error's own `details`, and the
    studio's picker, which offers the reviewer exactly these rows. `pe_firm` is
    carried when the record has one and omitted when it does not, rather than
    being written in as an empty string: the contract's example (§6.2) shows a
    firm on every candidate, and the module header records that `list_companies`
    returns more than this module reads, so whether a row can name a firm is a
    live observation and not a code reading. Where it is present it is the one
    thing that tells two same-named companies apart.

    `has_kg` and `status` ride along for item 22, and they are two different
    facts about a row rather than one (live, 2026-09-15: a search for "Northwind"
    returns four records and three are `has_kg: false, status: DRAFT`, so three
    of the four candidates item 17 put on screen lead to `E_NO_KG` on the next
    click). They are carried rather than collapsed into one "unavailable" flag
    because only one of them is a gate: `check_kg_gate` refuses a company with no
    knowledge graph, and nothing anywhere refuses a DRAFT one. A row that is
    DRAFT and has a KG is a row we have no evidence against, so the picker says
    "draft" about it and still lets it be picked.

    `has_kg` is always present and always a bool, including when the record does
    not carry the key. That is deliberate and it matches `check_kg_gate`, which
    reads the same absence as a refusal: a picker that let a row through on a
    missing field would offer a choice the gate then rejects, which is the whole
    defect. `status` is carried only when the record states one, on the same
    footing as `pe_firm`: absent means not answered, not ACTIVE.

    Costs no call. Both fields are already on the record `list_companies`
    returned to resolve the name.
    """
    row = {"name": record["name"], "company_id": record["id"],
           "has_kg": bool(record.get("has_kg"))}
    firm = record.get("pe_firm") or record.get("firm") or ""
    if firm:
        row["pe_firm"] = firm
    status = record.get("status") or ""
    if status:
        row["status"] = status
    return row


def resolve_company(client, company, company_id=""):
    """Resolve a company name to its registry record (contract §3 step 2).

    Substring search only, via `list_companies(search=company)`. The registry
    has duplicate and overlapping display names (confirmed live: a search for
    "Northwind" also matches "NorthwindAI", "Northwindfact", "Northwindtracs"), so more than one
    match is a real, explicit outcome rather than something to pick around.

    `company_id` is the candidate a reviewer PICKED out of a previous call's
    `E_AMBIGUOUS_COMPANY` list (item 17), and it narrows this search rather than
    replacing it. That is forced by the grant and is the one thing to understand
    before editing this function: there is no lookup-by-id tool (see the module
    header), so the id cannot be dereferenced on its own. It is resolved by
    running the SAME search the name ran and taking the candidate whose id
    matches, which is sound because the candidate came out of that search in the
    first place. Searching the candidate's own exact name instead would not be
    equivalent and would be worse: the exact name of one of four "Northwind" rows
    still matches all four.

    So this is NOT the contract's §3 step 1 ("UUID given, direct lookup"), which
    still has no tool to implement it against. It is the narrowing branch of
    step 2, on the same footing as the `pe_firm` filter in step 3: a second
    value that picks one row out of a candidate list the name produced. The
    request accordingly carries the name AND the id, never the id alone.

    An id that matches nothing in the search is an error, never a quiet fall
    back to the name match. A stale id, an id from a different company's list
    and a typed one are the same shape from here, and resolving one of them to
    whatever the name happens to match would build a deck for a company the
    reviewer did not pick and say nothing about it.
    """
    if not company:
        raise ProviderError(
            "E_BAD_REQUEST",
            "company reference is required",
            "Supply a company name.",
            {"field": "company"},
        )

    matches = client.call_tool_json("list_companies", {"search": company})["companies"]

    if not matches:
        raise ProviderError(
            "E_COMPANY_NOT_FOUND",
            f"{company!r} matched no company in the registry.",
            "Surface to a human; suggest the closest names or ask for a company_id.",
            {"query": company, "closest": []},
        )
    if company_id:
        for match in matches:
            if match["id"] == company_id:
                return match
        raise ProviderError(
            "E_COMPANY_NOT_FOUND",
            f"No company matching {company!r} carries the id {company_id!r}.",
            "Re-enter the company name to list its candidates again and pick "
            "one; the id picked earlier is not among them.",
            {"query": company, "company_id": company_id, "closest": [],
             "candidates": [candidate_of(m) for m in matches]},
        )
    if len(matches) > 1:
        raise ProviderError(
            "E_AMBIGUOUS_COMPANY",
            f"{company!r} matched {len(matches)} companies.",
            "Re-request with a company_id.",
            {"candidates": [candidate_of(m) for m in matches]},
        )
    return matches[0]


def resolve_project(client, company_id, project, deck_title=""):
    """Resolve a project name within a resolved company (contract §3 step 4).

    `list_projects` takes only `company_id`, no name filter, so matching is
    done here, client-side, as a case-insensitive substring test against each
    project's name.

    The project contributes exactly one string to a deck, the cover `Title` and
    the same string in the page marks, so `deck_title` (studio input, 2026-08-20)
    substitutes for it. Two cases, and the difference between them is whether the
    platform has any engagement project for this company at all:

    - The company lists projects. Nothing about resolution moves: a project is
      still required and a name matching none of them is still refused, because
      that guard is what keeps a deck from being titled off one engagement and
      written from another. A `deck_title` renames the resolved record's title
      only, and the record's id still says which engagement the deck belongs to.
    - The company lists none. There is nothing to resolve and nothing to refuse,
      so the title falls to the studio: `deck_title` if the reviewer supplied
      one, else `None`, which tells the caller to name the deck from the
      opportunity it selects next. A company can carry published opportunities
      (the research paper nearly every other role comes from) and no project, and
      before this an `E_PROJECT_NOT_FOUND` stopped that run before the paper was
      ever read.

    A returned record carrying `title_source` is one whose title did NOT come
    from a platform project name, which is how the caller knows to echo the title
    in the request so the page marks read it too.
    """
    projects = client.call_tool_json("list_projects", {"company_id": company_id})[
        "projects"
    ]

    if not projects:
        if deck_title:
            return {"name": deck_title, "id": None, "title_source": "studio"}
        return None

    if not project:
        raise ProviderError(
            "E_PROJECT_REQUIRED",
            "No project was supplied.",
            "Ask the user which project; do not fall back to company-level data.",
            {"company_id": company_id},
        )

    needle = project.lower()
    matches = [p for p in projects if needle in p["name"].lower()]

    if not matches:
        raise ProviderError(
            "E_PROJECT_NOT_FOUND",
            f"{project!r} matched no project for this company.",
            "Surface to a human; suggest the closest names or ask for a project_id.",
            {"company_id": company_id, "query": project, "closest": []},
        )
    if len(matches) > 1:
        raise ProviderError(
            "E_AMBIGUOUS_PROJECT",
            f"{project!r} matched {len(matches)} projects.",
            "Re-request with a project_id; do not guess.",
            {
                "company_id": company_id,
                "candidates": [
                    {"name": p["name"], "project_id": p["id"]} for p in matches
                ],
            },
        )
    if deck_title:
        return dict(matches[0], name=deck_title, title_source="studio")
    return matches[0]


def check_kg_gate(company):
    """The knowledge-graph gate (E2, folded into E1 on 2026-08-10).

    Maps the `has_kg` flag `list_companies` already returned on the resolved
    company to `E_NO_KG`. No separate credential call exists in the grant, so
    this reads the field resolution already fetched rather than making a
    second call.
    """
    if not company.get("has_kg"):
        raise ProviderError(
            "E_NO_KG",
            f"{company['name']} exists in the registry but has no knowledge "
            "graph provisioned.",
            "Provision a KG before requesting a proposal. No proposal can be "
            "generated without opportunity data.",
            {"company_id": company["id"]},
        )
    return company


def list_published_opportunities(client, company_id):
    """Published opportunities for a company, in a fixed, deterministic order.

    Uses `list_opportunities(stage="published")` (E3), not
    `get_preliminary_assessment`: the latter returns null `paper_natural` /
    `paper_abstract` on 12 of the 15 KG companies that have a published
    report, with an empty `opportunities` list, an unresolved server-side
    defect (`data-provider/PRD.md` §2.1b). `list_opportunities` has no such
    defect. Sorted by `published_at` then `id`, since nothing in the contract
    or the tool's own docs promises the server returns a stable order.
    """
    opportunities = client.call_tool_json(
        "list_opportunities", {"company_id": company_id, "stage": "published"}
    )["opportunities"]
    return sorted(opportunities, key=lambda o: (o["published_at"], o["id"]))


def fetch_opportunity_paper(client, company_id, opportunity_id):
    """Fetch one opportunity's detail and its research paper, if any.

    Confirmed live: `get_opportunity_details` nests the record under an
    `opportunity` key, and `research_paper_natural` is present with content
    (46KB on a real published Northwind opportunity) only for a published
    opportunity whose paper the server has not withheld; otherwise the key is
    simply absent. Returns `(opportunity_detail, paper_text_or_None)`. A
    `None` paper is a normal outcome here, not an error: the caller records it
    as a missing field per the Stage E rules, never as a failure.
    """
    detail = client.call_tool_json(
        "get_opportunity_details",
        {"opportunity_id": opportunity_id, "company_id": company_id},
    )
    opportunity = detail["opportunity"]
    return opportunity, opportunity.get("research_paper_natural")


def _title_words(text):
    """The words a title is scored on: alphanumeric runs, lowercased, single
    characters dropped as noise."""
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 1]


def rank_choices(project_name, choices):
    """Opportunity choices ordered by how well each label matches a project name.

    A deck resolves two things: the project supplies the cover title, and the
    chosen opportunity supplies the research paper nearly every slide is written
    from. Nothing on the platform links the two (no field on `list_projects`,
    `list_opportunities` or `get_opportunity_details`, and no edge from the KG's
    Engagement node to any Opportunity), so the correct pairing cannot be derived
    from the data. It can only be made easy to find: at a company with two dozen
    published opportunities, the one belonging to the project a reviewer typed
    can sit anywhere in a `published_at`-ordered list.

    Scoring is overlap weighted by inverse document frequency across the
    candidate labels, because a company's titles share their most prominent words
    ("ai", "deploy", "platform" recur in nearly every one) and a plain overlap
    count therefore ranks on noise. A word present in every candidate scores
    zero; a word present in one carries the most weight.

    Ranking, not choosing, and the two invariants that keep it that way: every
    candidate handed in comes back, exactly once, so nothing becomes
    unselectable however badly it scores; and with no project name the order is
    returned untouched, which is the behaviour that predates this ordering.

    The leader is flagged `best` so a caller can mark it, but only when the claim
    holds up: it must share at least one word with the project AND beat the
    runner-up outright. A project matching nothing, and a project matching two
    candidates equally well, are both ordered and left unmarked rather than
    given an arbitrary winner. The flag is a hint for a human reading the list;
    no code path may select on it.
    """
    choices = [dict(c) for c in choices]
    if not (project_name or "").strip() or not choices:
        return choices
    document_frequency = collections.Counter()
    for choice in choices:
        document_frequency.update(set(_title_words(choice["label"])))
    total = len(choices)
    project_words = set(_title_words(project_name))

    def score(choice):
        shared = project_words & set(_title_words(choice["label"]))
        return sum(math.log(total / document_frequency[w])
                   for w in shared if document_frequency[w])

    scored = sorted(((score(c), c) for c in choices), key=lambda pair: -pair[0])
    ranked = [choice for _s, choice in scored]
    leader = scored[0][0]
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    if leader > 0 and leader > runner_up:
        ranked[0]["best"] = True
    return ranked


def opportunity_choices(client, company, project="", company_id=""):
    """Published opportunities for a company name, to offer a reviewer a pick
    from (E9e). Resolves and KG-gates first, so an unresolved or ungated
    company surfaces the same `ProviderError` a run would, before anything is
    listed. No new tool: `list_opportunities` is already on this path.

    `project` is the project the reviewer named, used only to order the list and
    mark its leader (see `rank_choices`). It is not resolved against the registry
    here: an unrecognised or half-typed project name must still produce the full,
    selectable list rather than an error, because the reviewer is picking an
    opportunity at this point and not yet committing to a run. `resolve_project`
    does the strict resolution when the run itself happens.

    `company_id` is the reviewer's pick among an ambiguous name's candidates
    (item 17), and it is threaded here as well as into the run for the reason
    the picker exists: this route is where a reviewer meets the ambiguity, on
    company blur, several minutes before any run. Resolving with it is what turns
    the pick into a list of opportunities; without it a company matching four
    records is a wall rather than a choice.
    """
    resolved = check_kg_gate(resolve_company(client, company, company_id))["id"]
    return rank_choices(project, [
        {"id": o["id"], "label": o.get("title") or o.get("name") or o["id"]}
        for o in list_published_opportunities(client, resolved)
    ])


def fetch_published_papers(client, company_id):
    """Select published opportunities and fetch each one's paper (E3).

    One deterministic pass: `list_published_opportunities` then
    `fetch_opportunity_paper` per opportunity, in that order. Returns a list of
    `{"opportunity": <detail dict>, "paper": <text or None>}`, preserving the
    selection order so a caller building a packet can rely on it.
    """
    fetched = []
    for opp in list_published_opportunities(client, company_id):
        detail, paper = fetch_opportunity_paper(client, company_id, opp["id"])
        fetched.append({"opportunity": detail, "paper": paper})
    return fetched


# ===========================================================================
# The confidence band, ruled by Antonio on 2026-08-15
# ---------------------------------------------------------------------------
# `completeness_score.py` lines 21 through 23 state that a packet with no band
# fails `_passes_gates` closed and that deriving one from the ratio is a decision
# for whoever wires the provider in behind the seam. This is that wiring, so the
# band is derived here by a written rule rather than asserted: a pure, uncapped
# function of `data_completeness` and nothing else.
# ===========================================================================

HIGH_AT = 0.90
MEDIUM_AT = 0.70
LOW_BAND = "low"
BANDS = ((HIGH_AT, "high"), (MEDIUM_AT, "medium"))

# What the band measures and what it does not, recorded in section 8 beside
# `role_coverage` so the divergence sits on the artifact rather than in a
# changelog. `data_completeness` counts what the paper supplied against E7a's
# six-field roster; `role_coverage` counts what the deck got. On a live packet
# they read 1.0 against 0.22, so a high band and eleven missing roles are the
# same run. Antonio took that tradeoff deliberately on 2026-08-15, declining the
# capped alternative. Neither number here is a gate: `data_completeness` in the
# frontmatter is the only one `_passes_gates` reads.
CONFIDENCE_BASIS = (
    "confidence is derived from data_completeness by a written rule (high at "
    f"{HIGH_AT}, medium at {MEDIUM_AT}, low below). data_completeness measures "
    "how many of the six paper-derived roster fields the research paper "
    "supplied. It does not measure how much of the deck has a source; "
    "role_coverage beside it is that second number, and the two diverge. No "
    "gate reads role_coverage."
)


def confidence_band(data_completeness):
    """The band a completeness ratio earns. Deterministic, uncapped, pure."""
    for threshold, band in BANDS:
        if data_completeness >= threshold:
            return band
    return LOW_BAND


def error_envelope(error):
    """A `ProviderError` as the contract's `{"status": "error"}` envelope.

    Mechanical, because `ProviderError` already carries the envelope's own four
    fields. Every contract code comes back through here rather than escaping the
    seam as an exception, which is what lets the studio render an outcome for
    each one.
    """
    return {"status": "error", "error": {
        "code": error.code, "message": error.message,
        "remediation": error.remediation, "details": error.details,
    }}


class LiveProposalProvider:
    """The seam object (E9d): `submit(request)` plus `poll(handle)`, live.

    `client` is a `QofaiMcpClient` or anything with the same `call_tool_json`.
    It is passed in rather than constructed here, so this module names no
    endpoint and no environment variable, and so `httpx` stays off the hosted
    module-level import path until E10 lands the dependency (queue E10).

    `submit` does the work and `poll` reports it done. The contract's background
    mode exists because live assembly takes 30 to 90 seconds; a synchronous
    submit satisfies the protocol the transport half depends on without
    pretending to an async machinery nothing here has.

    `generated_at` is a parameter so a caller can pin the timestamp; left unset
    it is stamped at submit time.
    """

    def __init__(self, client, generated_at=None, extractor=None, writer=None,
                 ranker=None):
        self._client = client
        self._generated_at = generated_at
        self._envelopes = {}
        # The second extraction pass (E11 Stage 2), off unless a caller hands it
        # over -- the same seam shape `apply_text_gate(..., voice_pass=)` uses
        # for the other LLM leg. `second_pass.make_extractor()` builds the real
        # one; a test injects a fake and reaches no network. Absent, this
        # provider behaves exactly as it did before Stage 2 and the packet omits
        # the section 8 `second_pass` block entirely rather than claiming a pass
        # ran and found nothing.
        self._extractor = extractor
        # The WRITING pass (E11 Stage 2c), on the same seam and off by default
        # for the same reason. It is a second, separate LLM leg: the extractor
        # quotes facts under span discipline and this one writes the deck's
        # framing sentences under the generated rules in `paper_writing`. A
        # caller may turn on either, both, or neither; with this one off the deck
        # keeps its standard framing and the packet omits the section 8
        # `generated` block entirely rather than claiming a pass ran.
        self._writer = writer
        # The RANKING pass (2026-08-19), on the same seam and off by default for
        # the same reason. It is the narrowest of the three: it returns bullet
        # NUMBERS, never text, so it cannot put a word on a deck. With it off,
        # slide 2's panels keep whichever bullets happen to come first in the
        # packet's lists, which is what shipped before it existed.
        self._ranker = ranker

    def submit(self, request, uploads=()):
        # Imported here rather than at module scope so the fixture path never
        # pulls `httpx`. `LiveProposalProvider.submit` is the live path by
        # definition, so this costs the fixture path nothing.
        from qofai_mcp_client import McpError

        # `uploads` are the reviewer's attachments (item 14), a sequence of
        # `base_document.Upload`, and they are a second argument rather than a
        # member of `request` on purpose: `request` is the contract request and
        # it is echoed into the packet document and the deck store, which is not
        # a place to put a file's bytes. Optional, so every caller that submits a
        # request alone keeps working and a run with no attachment is the run
        # that shipped.
        handle = len(self._envelopes)
        try:
            self._envelopes[handle] = self._assemble(request, uploads)
        except ProviderError as error:
            self._envelopes[handle] = error_envelope(error)
        except McpError as error:
            # Agent OS never answered, so it could not have returned a code of
            # its own. The agent raises one on its behalf, which is what makes
            # `E_SOURCE_UNREACHABLE` the only code in the table the agent
            # originates. Without this the transport's exceptions leave the seam
            # as exceptions rather than envelopes, because the two hierarchies
            # are disjoint, and every caller of `submit` has to know that.
            self._envelopes[handle] = error_envelope(ProviderError(
                "E_SOURCE_UNREACHABLE",
                f"Agent OS could not be reached: {type(error).__name__}: {error}",
                "Retry once with backoff; escalate to whoever can see the "
                "platform's status. Do not re-run the deck.",
                {"endpoint_kind": "agent_os_mcp",
                 "error_type": type(error).__name__},
            ))
        return handle

    def poll(self, handle):
        return {"done": True, "envelope": self._envelopes[handle]}

    def _assemble(self, request, uploads=()):
        """Resolve, gate, select, fetch, assemble, build, in contract order.

        `uploads` are the reviewer's attachments and they are read FIRST, before
        a single question is put to the platform. A file we cannot read costs no
        round trip to discover, and a reviewer who mis-attached one finds out in
        the time it takes to read the file rather than after the resolution leg.
        """
        uploads = tuple(uploads or ())
        documents = self._documents(uploads)
        # `company_id` is the reviewer's pick among an ambiguous name's
        # candidates (item 17) and travels BESIDE the name, never instead of it:
        # with no lookup-by-id tool on the grant it narrows the name's own
        # search rather than dereferencing. See `resolve_company`.
        company = check_kg_gate(resolve_company(
            self._client, request.get("company"), request.get("company_id") or ""))
        project = resolve_project(self._client, company["id"], request.get("project"),
                                  deck_title=request.get("deck_title") or "")
        picked = self._select_all(company["id"], _opportunity_ids(request))
        opportunity, _paper = picked[0]
        # The deck's title, where the platform had no project to take it from.
        # Selection has to happen first, which is why this sits below the pick
        # rather than inside `resolve_project`: the opportunity IS the fallback
        # title, and resolution cannot see it. The FIRST opportunity titles the
        # deck, because the title belongs to the engagement and a deck carrying
        # several opportunities still has one cover.
        if project is None:
            project = {"name": opportunity.get("title") or "", "id": None,
                       "title_source": "opportunity"}
        # A title that did not come from a platform project name is echoed into
        # the request, because the cover reads the project record and the page
        # marks read the request. Both have to say the same thing (98e5d15), and
        # a run that named a project echoes exactly what the caller sent, so
        # nothing moves for the runs that already worked.
        if project.get("title_source"):
            request = dict(request, deck_title=project["name"])
        # THE BASE DOCUMENT RULE (item 14, step 2), and the only place it is
        # applied. `base_document.precedence` orders the sources and the paper
        # is always last; the base's TEXT is what the two model legs read, so
        # neither of them, nor anything below them, learns that a document was
        # attached. With no attachment the chain is the paper alone, the merge
        # of one packet is that packet, and both legs are handed exactly the
        # text they were handed before.
        # THE ENGAGEMENT CHAIN (item 15, step 3), which resolves the units that
        # belong to the COMPANY and the ENGAGEMENT rather than to any one
        # opportunity: the three baseline figures, which are the same facts
        # whichever opportunity is being described. Not the timeline, which
        # each opportunity keeps from its own chain (`base_document.SHARED_UNITS`
        # says why, 2026-09-22). Every source in the run,
        # uploads first. On a one-opportunity run it IS that opportunity's own
        # chain and `apply_shared` is a no-op by identity.
        # Built only when there is more than one opportunity, and that is an
        # identity argument rather than a branch on behaviour: with one,
        # `engagement_precedence` returns that opportunity's own chain, the
        # merge of it is the same packet, and `apply_shared` returns it by
        # identity. Building it anyway would walk the same sources a second time
        # for an answer already in hand, and a run with no attachment would
        # reach assembly twice with the paper where it has always reached it
        # once.
        engagement = None
        if len(picked) > 1:
            engagement = self._packet(
                opportunity["id"],
                base_document.engagement_precedence(
                    [paper for _detail, paper in picked], documents
                ),
                subject_of(opportunity),
            )
        # EACH OPPORTUNITY'S OWN DOCUMENTS (2026-09-22). A document whose front
        # matter names one of this deck's opportunities joins that
        # opportunity's chain alone; every other document joins every chain,
        # as all of them did before. `precedence` is then called per
        # opportunity with only what belongs to it, and reads nothing about
        # content itself. The ENGAGEMENT chain above still reads every
        # document, because the baseline it resolves is a fact about the
        # company whichever document states it.
        routes = self._route(company["id"], picked, documents)
        assembled = [
            self._assemble_one(
                detail, paper,
                document_routing.documents_for(documents, routes, detail["id"])
                if routes else documents,
                engagement, request, company)
            for detail, paper in picked
        ]
        sources = assembled[0]["sources"]
        base = assembled[0]["base"]
        second = assembled[0]["second"]
        written = assembled[0]["written"]
        # The gate reads the packet AFTER the second pass, and that is the point
        # of running the pass before it rather than after: a roster field the
        # paper states in prose and no parser could read is a field the packet
        # genuinely has, and refusing the deck over its absence would be
        # refusing over a limit of the parsers rather than of the paper. What
        # the gate cannot do is soften: `ROSTER` is the same six fields and the
        # floor is the same 0.70, so a packet still short of them still returns
        # `E_LOW_CONFIDENCE`.
        # What the reviewer is told about sources, and the one thing in this
        # envelope that exists for them rather than for the deck. Built from the
        # FINAL packet, so a field the extraction pass read out of the base
        # document names the base document. Empty on a run with no attachment,
        # which is what keeps the studio quiet on the runs that have one source.
        # Both passes go in beside the packet. A report built from the packet
        # alone counts only what the deterministic parsers lifted, and those
        # parsers read the published paper's table shapes, so an attached PDF
        # scores zero there whatever it contributed. Both legs read `base.text`,
        # so what they took belongs to the base document by construction.
        # ONE ACCOUNT OF THE RUN, built from every section's own report.
        # `provenance_report.build` is per SECTION and is handed one
        # opportunity's chain, which is the shared uploads plus that
        # opportunity's own paper and no other; that construction is what keeps
        # one opportunity's slide from being written from another's paper, and
        # it means no single section's report can speak for the run. A live
        # two-opportunity run on 2026-09-13 listed two sources where the run had
        # read three, because the studio was rendering the first section's
        # report as though it were the deck's.
        provenance = provenance_report.combine([
            provenance_report.build(
                entry["second"].packet, entry["sources"],
                extraction=self._landed(entry["second"], request, company,
                                        entry["opportunity"]),
                writing=entry["written"],
                opportunity=(entry["opportunity"] or {}).get("title") or "",
            )
            for entry in assembled
        ])
        # THE GATE RUNS PER OPPORTUNITY SECTION, not once over a merged packet
        # (item 15). Each opportunity's packet is scored against the same six
        # roster names and the same floor, and a thin paper fails ITS OWN
        # section and is marked rather than sinking a deck whose other
        # opportunity was fine. That is a live possibility rather than a
        # hypothetical: the papers are being rewritten on the platform, and one
        # replaced opportunity's new paper parses to one field where the old
        # gave five.
        #
        # The deck is refused only when NO opportunity clears, which for one
        # opportunity reduces exactly to the refusal that has always happened.
        #
        # Carried into the gate as well as out of it. A gate failure is where an
        # attachment matters MOST: an attachment that states a partial baseline
        # is exactly what drops a run below the floor, and the figure the group
        # rule declined is sitting in the other document unmentioned. Telling
        # the reviewer only on the runs that succeeded would leave the failure
        # they most need explaining as the one with no explanation.
        marks = self._gate_each(assembled, request, provenance=provenance)
        return {
            "status": "ok",
            "packet": self._document(request, company, project, opportunity,
                                     second, written, assembled=assembled),
            # WHAT EACH OPPORTUNITY SCORED, and whether its section cleared.
            # Reviewer-facing, beside the packet and never inside it: a deck
            # carrying a marked section must say which one and why, and a score
            # is an annotation about our own confidence rather than anything a
            # client should read on a slide.
            "opportunities": marks,
            "request_echo": request,
            "provenance": provenance,
            # WHAT THE DECK WAS BUILT FROM, for the store (item 14, step 5). The
            # extracted text plus each file's name, kind, size and SHA-256, in
            # precedence order, and never the bytes: the model read the text, so
            # the text is the honest record of what produced a slide.
            #
            # Beside the packet and never inside it, exactly as `provenance` is.
            # A record names a file, and a filename is a source annotation, which
            # does not belong in front of a client. Empty on a run with no
            # attachment, which is every run that names one source.
            #
            # Built here rather than in the studio because this is the only place
            # that holds both halves: the route has the bytes and no extraction,
            # and everything below `_documents` has the text and no bytes.
            "attachments": attachment_record.build_all(uploads, documents),
            # WHICH FRAMING LINES WERE WRITTEN AND WHICH WERE REFUSED (item 24).
            # The same structure section 8 carries, beside the packet rather
            # than only inside it, because the packet never leaves
            # `generate_and_save_deck` and is never written to disk (Antonio,
            # 2026-07-28), so until now every run computed this and discarded
            # it. Two live renders in a row could not be diagnosed for that
            # reason: on 2026-09-16 `copy.plan_summary` and
            # `copy.next_steps_summary` came back as their deck standards where
            # the day before they carried written lines, and whether the model
            # returned the standard or declined the slot is a distinction this
            # structure already makes and nothing could read.
            #
            # The STRUCTURE and not the rendered section: `written_ledger` runs
            # here anyway for the document, so carrying its return costs nothing,
            # while re-parsing section 8 out of the packet text would be reading
            # back something this same call just wrote. `None` when no writing
            # pass ran, exactly as `written_ledger` returns it, so a run with no
            # writer says nothing rather than claiming an empty pass.
            #
            # Reaches no template role, no prompt and no deck, for the reason
            # `provenance` and `attachments` reach none: a record about the run
            # is not content.
            "writing_ledger": second_pass_module.written_ledger(written),
            # WHICH OPPORTUNITY EACH ATTACHMENT WROTE, and why an unplaced one
            # applies to the whole run. Reviewer-facing for the reason
            # `attachments` is: a reviewer who attached two PRDs needs to see
            # which slide each one wrote. Empty on a one-opportunity run, where
            # every attachment belongs to the only opportunity there is.
            "document_routes": document_routing.record(routes),
            # WHICH EMPTY NEXT STEPS FIELDS THE DOCUMENT SEEMS TO STATE (Part A4,
            # 2026-09-23). A flag the document answers is our reader's gap, not
            # the document's, and a reviewer about to clear it should see that.
            # Reviewer-facing and beside the packet, like the two above.
            "flag_audit": self._flag_audit(assembled),
        }

    def _assemble_one(self, opportunity, paper, documents, engagement, request,
                      company):
        """One opportunity's own assembly: its chain, its packet, its passes.

        THE CHAIN IS ITS OWN PAPER AND NO OTHER, which is what makes
        "opportunity A's slide is never written from B's paper" a fact about how
        this is built rather than a check somewhere downstream. `documents` are
        this opportunity's own: the attachments whose front matter names it,
        and every attachment that names none of the deck's opportunities
        (`document_routing`). The supporting tail is this opportunity's paper
        alone.

        `apply_shared` then puts the ENGAGEMENT's baseline on top, because that
        belongs to the company. The timeline stays this opportunity's own. On a
        one-opportunity run the engagement packet IS this packet and that call
        returns it by identity.

        Both model legs read the BASE, exactly as they have since item 14, and
        both are told which opportunity they are reading for, exactly as they
        have since this morning.
        """
        sources = base_document.precedence(paper, documents)
        base = base_document.base(sources)
        subject = subject_of(opportunity)
        packet = base_document.apply_shared(
            self._packet(opportunity["id"], sources, subject), engagement
        )
        # THE PACKET-TO-DOCUMENT CHECK, and the only guard that looks this
        # direction (`prd_loyalty_guard`'s docstring says why the other three
        # could not have caught the 2026-09-20 defect). Run HERE, on the
        # deterministic packet and before either model leg, for two reasons: a
        # PRD the packet contradicts fails for no API spend, and the facts it
        # checks are ones the deterministic parsers own.
        #
        # THE LIMIT, stated rather than hidden: a roster field the second pass
        # fills afterwards is not covered, so a timeline that arrives from prose
        # rather than from §6's table reaches a deck unchecked. That is the
        # narrower risk of the two, since `prd_section_parsers` reads §6
        # deterministically on every PRD in the corpus, and moving the check
        # after the legs would mean paying for a run this can already refuse.
        # PRESENT AND UNREADABLE IS NOT SILENCE, and it is checked before the
        # loyalty guard because the guard cannot see it: a §6 that yields no
        # phases leaves the duration check with nothing to compare, so the
        # guard passes a document whose plan was never read. With the paper out
        # of the chain there is nothing behind a PRD any more, so this is the
        # 24-week failure with the plan coming from nowhere instead of from the
        # wrong place, and it should be as loud.
        #
        # Scoped to documents that ARE PRDs: a supporting note or a spreadsheet
        # states no §6 and must not be refused for it.
        if prd_section_parsers.is_prd(base.text):
            unreadable = prd_section_parsers.unreadable_sections(base.text)
            if unreadable:
                raise ProviderError(
                    "E_DOCUMENT_UNREADABLE",
                    "This document is a QofAI PRD and a section it needs was "
                    "found but could not be read. "
                    + " ".join(f"In {label}, {reason}"
                               for label, reason in unreadable),
                    "Check that section against the template, then re-run. "
                    "This is refused rather than rendered because nothing "
                    "stands behind an uploaded PRD: a section reading as empty "
                    "is the whole of what the deck would know about that fact.",
                    {"sections": [label for label, _r in unreadable]},
                )
        loyalty = prd_loyalty_guard.check_prd_loyalty(base.text, packet)
        if not loyalty["ok"]:
            raise ProviderError(
                "E_DOCUMENT_CONTRADICTED",
                "This deck would contradict the document it was built from. "
                + " ".join(finding["message"] for finding in loyalty["findings"]),
                "Correct the document, or the extraction that read it, then "
                "re-run. The pipeline will not choose between two "
                "statements of the same fact.",
                {"findings": loyalty["findings"], "checked": loyalty["checked"]},
            )
        second = self._second_pass(base, packet, request, company, opportunity)
        # AND THEN THE SOURCES BEHIND IT, for the roster fields the base did not
        # answer (2026-09-12). The pass above reads the BASE, which is the whole
        # of what it read for its life -- and with a document attached that is a
        # PRD, which states the engagement and not the company's trailing
        # revenue. A live run scored 0.83 with nothing attached and 0.17 with the
        # PRD attached, refused, for want of three baseline figures the published
        # paper states: attaching a document made a working run fail. The
        # deterministic merge already had this rule ("the paper answers only what
        # the base does not") and the model leg did not.
        #
        # Per field and never per document: each source is asked only for what
        # the sources ahead of it left absent, one document per call, so a
        # rescued figure names the document it was actually read from. Costs
        # nothing on a run whose base answered everything, and nothing at all on
        # a run with no attachment, whose chain has no supporting source.
        second = second_pass_module.rescue(
            second, base_document.supporting(sources),
            extractor=self._extractor, opportunity=subject,
            description=(opportunity or {}).get("description") or "",
        )
        written = second_pass_module.write(
            base.text, (opportunity or {}).get("description"), writer=self._writer
        )
        return {"opportunity": opportunity, "sources": sources, "base": base,
                "second": second, "written": written}

    def _flag_audit(self, assembled):
        """Each opportunity's Next Steps gaps the document seems to state.

        Only where the base document is a PRD, because §14 is where a PRD
        states its steps and a published paper has no §14 to audit. The steps
        audited are the ones `prd_section_parsers.next_steps` read from that
        PRD, which is what a PRD run's Next Steps slide carries.
        """
        findings = []
        for entry in assembled:
            text = entry["base"].text
            if not prd_section_parsers.is_prd(text):
                continue
            title = (entry["opportunity"] or {}).get("title") or ""
            for finding in flag_audit.stated_but_not_read(
                    text, prd_section_parsers.next_steps(text)):
                findings.append(dict(finding, opportunity=title))
        return findings

    def _route(self, company_id, picked, documents):
        """Where each attachment goes, or () when there is nothing to decide.

        Nothing to decide on a one-opportunity run, which is why that run makes
        no new platform call and cannot move. The published list is fetched only
        when some document states an opportunity it could match, and a listing
        failure is left to raise: `submit` turns it into
        `E_SOURCE_UNREACHABLE`, which is louder than a deck quietly written with
        every document on every slide.
        """
        if len(picked) < 2 or not documents:
            return ()
        listed = None
        if document_routing.needs_listing(documents):
            listed = [
                {"id": o["id"], "label": o.get("title") or o.get("name") or o["id"]}
                for o in list_published_opportunities(self._client, company_id)
            ]
        return document_routing.route(
            documents, [detail for detail, _paper in picked], listed)

    def _documents(self, uploads):
        """Every attachment as text, in the order the reviewer attached them.

        An extraction failure becomes the contract error envelope off its own
        four fields, unchanged. `document_text.ExtractionFailure` carries
        `code`, `message`, `remediation` and `details` because those are this
        module's own four arguments, so the reviewer reads the sentence the
        module that refused the file wrote. Re-deriving one here would put a
        second, different account of the same refusal on the screen.

        Never a fall back to the paper. A reviewer who attached a document and
        got a deck written from the published paper instead would have no way to
        tell, which is the one outcome part two's success criteria rule out by
        name.
        """
        documents = []
        for upload in uploads or ():
            result = document_text.extract_text(upload.data, upload.filename)
            if not result.ok:
                raise ProviderError(result.code, result.message,
                                    result.remediation, result.details)
            documents.append(result)
        return tuple(documents)

    def _packet(self, opportunity_id, sources, opportunity=None):
        """The deterministic packet, from every source in precedence order.

        One source is the path that shipped: one `assemble_cached` call on the
        paper, and `merge_packets` of a single packet returns that packet
        itself. The cache keys on the text rather than on the opportunity id,
        deliberately (`extraction_cache`'s own docstring), so a different base
        document invalidates its own entry and no second caching scheme is
        needed here or anywhere.
        """
        return base_document.merge_packets(
            extraction_cache.assemble_cached(opportunity_id, source.text,
                                             source.document, opportunity)
            for source in sources
        )

    def _second_pass(self, base, packet, request, company, opportunity):
        """E11 Stage 2, run against the deterministic packet and its own fill map.

        `base` is the base document, which is the published paper unless the
        reviewer attached something. Its text is what the pass reads and its
        `document` is what every figure the pass produces names. The pass reads prose for the fields the
        deterministic parsers left absent, and it reads them out of the document
        the deck is being written from; a span it returns is verified against
        that same text, so provenance stays consistent without the pass knowing
        which document it was handed.

        The fill map is built twice on this path and both builds are pure. This
        one exists only to answer "which document paths did the first pass leave
        empty", which is what composes the request; `_document` builds the one
        that is written, from the packet this returns.
        """
        fill, _sources = packet_fill.fill_map(
            company=company, request=request, opportunity=opportunity,
            packet=packet,
        )
        return second_pass_module.run(
            base.text, packet, fill, extractor=self._extractor,
            document=base.document, opportunity=subject_of(opportunity),
            # What the opportunity IS, in the platform's own words. It reaches
            # the prompt and never a figure: on a run whose base document
            # describes more than one opportunity, this is what lets the pass
            # tell which half it was asked to read.
            description=(opportunity or {}).get("description") or "",
        )

    def _landed(self, second, request, company, opportunity):
        """The second pass with its DOCUMENT HALF merged, for the report.

        `run` merges the roster half and records those paths in `merged`; the
        document half (the metrics, the pain points, the platform layers, the
        next steps) is merged by `merge_fill` against the fill map rebuilt from
        the pass's own packet, which happens inside `_document`. That leaves the
        `SecondPass` this function is handed knowing only half of what the pass
        landed, and the half it does not know is most of what an attached
        document contributes -- which is exactly how the panel came to report a
        PRD that reshaped a deck at "0 fields" (2026-09-08).

        So the merge is run here too, on its own copies. `merge_fill` returns new
        maps and a new result rather than editing either, and `fill_map` is pure
        (its own docstring says both of `_document`'s builds are), so this is a
        third pure build and nothing downstream can tell it happened. Same
        inputs as `_document`'s, so the report and section 8 agree by
        construction rather than by two people keeping them in step.

        Run BEFORE the gate as well, deliberately: a gate failure is where the
        panel matters most, and a refused run that quoted half a deck out of an
        attachment must not report that attachment at zero.
        """
        fill, fill_sources = packet_fill.fill_map(
            company=company, request=request, opportunity=opportunity,
            packet=second.packet,
        )
        _fill, _sources, landed = second_pass_module.merge_fill(
            fill, fill_sources, second
        )
        return landed

    def _section(self, entry, request, company):
        """One further opportunity's section 2: its own fill map and its own copy.

        The same four steps `_document` runs for the first one, in the same
        order, because a section built any other way would be a second account
        of what a slide 2 is. `apply_templated_defaults` runs here too: a deck
        standard that went back on one opportunity's platform has to go back on
        every one's, or the merged slide would read as sourced on a run where it
        was templated.
        """
        opportunity, second = entry["opportunity"], entry["second"]
        fill, sources = packet_fill.fill_map(
            company=company, request=request, opportunity=opportunity,
            packet=second.packet,
        )
        fill, sources, second = second_pass_module.merge_fill(fill, sources, second)
        fill, sources, _fell_back = packet_fill.apply_templated_defaults(
            fill, sources
        )
        framing, _sources = second_pass_module.merge_written(
            sources, entry["written"]
        )
        return {"fill": fill, "sources": sources, "opportunity": opportunity,
                "copy": packet_fill.opportunity_copy(opportunity, framing),
                "slide_copy": packet_fill.slide_copy(framing)}

    def _merge_shared_records(self, fill, sources, sections):
        """The platform components and the next steps, merged whole for the deck.

        THE SAME RULE AS THE PACKET-LEVEL MERGE, AND THE SAME STATEMENT OF IT.
        `base_document.take_whole` is called here exactly as `merge_packets`
        calls it: the highest-precedence source stating any of a unit supplies
        ALL of it, and every other source's is declined. Stating the rule twice
        in two vocabularies is how the baseline label and the baseline figures
        came apart for a day on 2026-09-07, so this passes the lists to that
        function rather than re-deriving what a lead is.

        WHY THESE TWO ARE HERE AND NOT IN `merge_packets`. `platform_layers` and
        `next_steps` are not packet fields at all. They are fill-map paths,
        filled by the second pass's document half or by the contract's deck
        standard, so the only place holding all of them is here, after every
        opportunity's fill map exists.

        WHY THEY STILL MERGE, now that every opportunity has its own Platform,
        Timeline and Next Steps slide (2026-09-22). The merge is what the FLAT
        map carries, and the flat map is what a one-opportunity deck, the frozen
        fixtures and every non-repeating consumer read; each opportunity's own
        lists ride in `sections` untouched and reach its own slides from there.
        What the merge must never do is splice: two documents' numbered
        component lists joined into one would state a platform neither document
        states, which is the argument `merge_packets` makes about scenario
        tables.

        Precedence is deck order, which is the order the reviewer asked for the
        opportunities. On a run with an attachment every section's list came
        from the same base document anyway, so this only decides anything on a
        run with several papers and nothing attached.
        """
        if len(sections) < 2:
            return fill, sources
        fill, sources = dict(fill), dict(sources)
        for path in SHARED_RECORD_PATHS:
            stated = [section["fill"].get(path) for section in sections]
            lead, _declined = base_document.take_whole(stated)
            if not stated[lead]:
                continue
            fill[path] = stated[lead]
            # The lead's OWN account of where its list came from, so section 8
            # does not report the first opportunity's origin for a list the
            # second one supplied.
            origin = sections[lead]["sources"].get(path)
            if origin:
                sources[path] = origin
        return fill, sources

    def _gather_scenarios(self, fill, sections):
        """Every opportunity's scenario cases, as rows on one table.

        CONCATENATED AND NEVER MERGED. The platform and the next steps each get
        a slide per opportunity; slide 5 is the one slide that stays shared, so
        its rows are gathered onto one table rather than chosen between. Each
        case is a real projection about a real opportunity, and a deck carrying
        two opportunities has two sets of them.

        AND NEVER SUMMED (Antonio, 2026-09-13). Adding two opportunities' uplift
        together prints a figure neither paper states, which is exactly what
        `base_document.merge_packets` already refuses for a scenario table.

        The opportunity becomes a LABEL on the row, carried only when the rows
        come from more than one, so a deck with nothing to disambiguate renders
        exactly the table it rendered before. That is the same way `client_short`
        degrades and the same way the published paper is the one document with
        no filename: a label that exists to tell two things apart is absent when
        there is one thing.

        Slide 5 stays ONE combined set either way, because QofAI contracts the
        engagement rather than the opportunity.
        """
        # The investment rows (§11.2's cost total, 2026-09-23) gather on exactly
        # the same terms: each opportunity's own PRD states its own cost, and
        # two PRDs' totals are two facts, never one summed figure.
        for path in (SCENARIO_PATH, INVESTMENT_PATH):
            rows = [section["fill"].get(path) or () for section in sections]
            if sum(1 for group in rows if group) < 2:
                continue
            gathered = []
            for section, group in zip(sections, rows):
                gathered.extend(packet_fill.label_scenarios(
                    group, (section["opportunity"] or {}).get("title") or ""
                ))
            fill = dict(fill, **{path: gathered})
        return fill

    def _rank(self, fill, framing, opportunity):
        """Slide 2's bullet order, as a section 8 ledger, or None if none ran.

        Reads the two bullet lists out of the merged fill map and asks the
        ranking pass which of them a partner most needs to see. The panel that
        holds them shows only as many as physically fit, and `panel_fit` trims
        from the tail, so without this the surviving bullets are whichever
        happened to come first.

        The context it is given is what the slide argues: the opportunity's own
        title, and the summary line if the writing pass produced one, else the
        opportunity's description. All three are already on this path; none is
        fetched for this.

        Degrades to nothing. `bullet_ranking.run` catches every failure of the
        pass itself and returns the order the lists already had, and a run with
        no ranker never calls anything, so this can only ever change WHICH
        sourced bullets appear, never whether the deck renders.
        """
        # No ranker attached means no pass ran, and the packet says so by
        # omitting the block rather than by carrying one that reports every list
        # unchanged. Those are different facts and section 8 must not blur them.
        if self._ranker is None:
            return None
        opportunity = opportunity or {}
        # Keyed with the `[]` suffix, which is how the fill map names a list of
        # SCALARS (`second_pass.FILL_KEY`); a record list is keyed by its
        # container without one. Stripping it here looked up nothing.
        panels = {panel: list(fill.get(path) or ())
                  for panel, path in bullet_ranking.PANEL_PATHS.items()}
        if not any(panels.values()):
            return None
        summary = ((framing or {}).get("copy.opportunity_summary")
                   or opportunity.get("description") or "")
        ranking = bullet_ranking.run(
            panels,
            headline=opportunity.get("title") or "",
            summary=summary,
            ranker=self._ranker,
        )
        return bullet_ranking.ledger(ranking, panels)

    def _select(self, company_id, opportunity_id):
        """The reviewer's pick (E9e) if one was made, else the first published
        opportunity carrying a research paper, the same default as before.

        A picked opportunity is fetched and handed straight to assembly, paper
        or no paper: whether it clears the bar is the completeness gate's job
        below, not this method's, so a picked opportunity short on baselines
        still returns `E_LOW_CONFIDENCE` rather than `E_NO_CORPUS`. Only the
        unpicked default keeps requiring a paper to choose one at all.
        """
        if opportunity_id:
            return fetch_opportunity_paper(self._client, company_id, opportunity_id)
        for opportunity in list_published_opportunities(self._client, company_id):
            detail, paper = fetch_opportunity_paper(
                self._client, company_id, opportunity["id"]
            )
            if paper:
                return detail, paper
        raise ProviderError(
            "E_NO_CORPUS",
            "No published opportunity for this company carries a research paper.",
            "Trigger a corpus build, or notify a human; no proposal can be "
            "derived without one.",
            {"company_id": company_id},
        )

    def _select_all(self, company_id, opportunity_ids):
        """Every opportunity this deck is for, as `(detail, paper)` pairs.

        NO CEILING ON THE COUNT, which is Antonio's call (2026-09-13) and not
        this module's to add. What scales with the count is the render, and the
        render's bound is sized off the slide count rather than assumed.

        With no ids this falls back to the first published opportunity carrying
        a paper, which is the default that shipped, and with one id it fetches
        exactly that one. Both return a tuple of one, so a deck carrying one
        opportunity is a deck carrying a list of one and nothing branches on the
        count anywhere below here.
        """
        if not opportunity_ids:
            return (self._select(company_id, None),)
        return tuple(
            fetch_opportunity_paper(self._client, company_id, opportunity_id)
            for opportunity_id in opportunity_ids
        )

    def _gate_each(self, assembled, request, provenance=None):
        """Score every opportunity's section, and refuse only if none clears.

        THE GATE RUNS PER OPPORTUNITY SECTION (item 15). Each section is scored
        against the same six roster names and the same 0.70 floor, and a thin
        paper fails its own section and is MARKED rather than sinking a deck
        whose other opportunity was fine.

        The deck is still refused when NOTHING clears, with the same
        `E_LOW_CONFIDENCE` and the same message the first failing section would
        have produced on its own. For one opportunity that reduces exactly to
        the refusal that has always happened, which is why the sparse paper
        still scores 4 of 6 and is still turned away.

        Returns the reviewer's account, one record per opportunity, in deck
        order: what it scored, whether it cleared, and what it is still missing.
        A marked section renders with its own `[MISSING: ...]` markers, which it
        already did; this is what tells the reviewer that the marks are a score
        below the floor rather than a slide that happened to be thin.
        """
        threshold = request["options"]["min_data_completeness"]
        marks = []
        for entry in assembled:
            packet = entry["second"].packet
            ratio = completeness_score.data_completeness(packet)
            second = entry["second"]
            marks.append({
                "opportunity_id": (entry["opportunity"] or {}).get("id") or "",
                "title": (entry["opportunity"] or {}).get("title") or "",
                "data_completeness": ratio,
                "confidence": confidence_band(ratio),
                "cleared": not completeness_score.gate(packet, threshold),
                "missing_fields": [name for name, _why in packet.missing_fields],
                # A MODEL LEG THAT BROKE, ON A RUN THAT SUCCEEDED. Until
                # 2026-09-13 a failed pass reached the reviewer only through the
                # gate, so it was visible exactly when the deck was refused and
                # invisible whenever the safety net held. A live
                # two-opportunity run that day lost eleven fields to one
                # truncated answer, had every one of them covered by the rescue
                # and by the deterministic parsers, scored 1.0 on both sections
                # and reported ok. Nothing anywhere said a leg had failed, which
                # is worse than the refusal case rather than better: a run that
                # is turned away gets looked at, and a run that succeeds does
                # not. Section 8 carried it and no reviewer reads section 8 of a
                # deck that worked.
                "pass_failed": bool(getattr(second, "failed", False)),
                "pass_failure": getattr(second, "failure", "") or "",
                # What that leg was asked for and did not read. The fields may
                # well be present anyway, filled by the parsers or rescued from
                # another source, which is exactly why the count matters: it
                # says how much of this section rested on the safety net.
                "absent_after_failed_pass": [
                    {"field": path, "reason": reason}
                    for path, reason in (getattr(second, "dropped", ()) or ())
                ] if getattr(second, "failed", False) else [],
            })
        if not any(mark["cleared"] for mark in marks):
            first = assembled[0]
            self._gate(first["second"].packet, request, first["second"],
                       provenance=provenance, marks=marks)
        return marks

    def _gate(self, packet, request, second=None, provenance=None, marks=None):
        """The completeness gate, as the contract's own `E_LOW_CONFIDENCE`.

        The adapter re-checks the frontmatter behind this, which is
        belt-and-suspenders rather than duplication: the contract says the
        provider returns the code instead of a partial packet.

        `second` is the `SecondPass`, and it is here for one reason: THIS METHOD
        RAISES BEFORE `_document` IS CALLED. So on a gate failure the packet
        document is never built, section 8 never exists, and
        `second_pass.ledger(second)` -- which carries the pass's own account of
        every field it could not fill and why -- is discarded unread. A live WTG
        run on 2026-09-02 returned "The assembled packet scores 0.33 against a
        0.7 floor" naming four missing baseline figures that the research paper
        states in full, because the extraction pass had timed out and the
        envelope said nothing about it. The reviewer went to inspect the paper,
        which was the wrong place, and the same opportunity rendered a complete
        deck twice the same day.

        So a gate failure that coincides with a FAILED pass says so, in the
        message the studio shows and in the details a machine reads. The floor
        itself does not move and the code does not change: a packet short of the
        roster is still refused, and this is about naming the reason correctly,
        not about softening the refusal. A pass that ran and simply found nothing
        adds nothing here -- `SecondPass.failed` is false in that case -- because
        "the paper does not state it" is what the unadorned message already
        means.
        """
        threshold = request["options"]["min_data_completeness"]
        code = completeness_score.gate(packet, threshold)
        if not code:
            return
        ratio = completeness_score.data_completeness(packet)
        message = (f"The assembled packet scores {ratio:.2f} against a "
                   f"{threshold} floor.")
        remediation = "Do not render; return for human review."
        details = {
            "confidence": confidence_band(ratio),
            "data_completeness": ratio,
            "missing_fields": [name for name, _why in packet.missing_fields],
        }
        if provenance:
            # The reviewer's own account of the run, in the details a machine
            # reads and the studio renders. It changes no code and no floor: a
            # packet short of the roster is still refused.
            details["provenance"] = provenance
        if marks and len(marks) > 1:
            # Every section's own score, on a deck where NONE of them cleared.
            # One number for a deck carrying several opportunities would say
            # which of them to go and look at only by accident.
            details["opportunities"] = marks
            message += (
                f" No section cleared: {len(marks)} opportunities scored "
                + ", ".join(f"{mark['title'] or mark['opportunity_id']} "
                            f"{mark['data_completeness']:.2f}"
                            for mark in marks)
                + "."
            )
        if second is not None and getattr(second, "failed", False):
            requested = list(getattr(second, "requested", ()) or ())
            details["second_pass_failed"] = second.failure
            # The pass's OWN reasons, path by path, in the same `(field, reason)`
            # shape section 8's `not_filled` block would have carried had the
            # document been built. This is the block that makes "absent because a
            # model call failed" distinguishable from "absent because the paper
            # does not state it", which is the whole defect.
            details["absent_after_failed_pass"] = [
                {"field": path, "reason": reason}
                for path, reason in (getattr(second, "dropped", ()) or ())
            ]
            message += (
                f" This score is the deterministic parsers' alone: the second "
                f"extraction pass did not complete ({second.failure}), so the "
                f"{len(requested)} field(s) it was asked to read out of the "
                f"paper's prose were never read."
            )
            remediation = (
                "A model call failed on this run, so the score above is not a "
                "verdict on the research paper. Re-run before treating the "
                "missing figures as missing from the source: the same paper has "
                "cleared this floor on a run where the pass completed. If a "
                "re-run fails the same way, the pass itself is broken and the "
                "paper is still the wrong place to look."
            )
        raise ProviderError(code, message, remediation, details)

    def _document(self, request, company, project, opportunity, second,
                  written=None, assembled=()):
        """The contract's packet markdown, built twice for one reported number.

        `assembled` is every opportunity's assembly, in deck order (item 15).
        The FIRST one is what `second`, `written` and `opportunity` already name,
        so a one-opportunity run walks exactly the path it walked before; the
        rest contribute their own section 2 and their own share of the two
        merged slides.

        `role_coverage` can only be measured on what `map_packet` produced, so
        the first build is measured and the second carries the figure. Section 8
        feeds no role, so the two documents differ in that number alone.

        `second` is the `SecondPass`. Its packet is what the fill map is derived
        from, so a roster field it filled reaches the document the same way a
        parsed one does; its document half is merged onto that map afterwards,
        which is where the precedence rule is checked the second time -- against
        the map as it actually stands rather than as it stood when the request
        was composed.
        """
        packet = second.packet
        fill, sources = packet_fill.fill_map(
            company=company, request=request, opportunity=opportunity, packet=packet
        )
        fill, sources, second = second_pass_module.merge_fill(
            fill, sources, second
        )
        # The deck standard goes back on whatever the paper did not carry, AFTER
        # the second pass has had its chance at it (E11 Stage 2c). `fell_back`
        # is what section 8 reports as templated rather than paper-sourced.
        fill, sources, fell_back = packet_fill.apply_templated_defaults(
            fill, sources
        )
        # The generated half, kept apart from the fill map on purpose: a written
        # sentence reaches the deck through the COPY channel and never becomes a
        # packet field, so it cannot move `Packet.present`, `data_completeness`
        # or any gate.
        framing, sources = second_pass_module.merge_written(sources, written)
        # Which of slide 2's bullets matter most, decided on the FINAL lists, so
        # a bullet the second pass added is ranked alongside the parsed ones.
        # This records an order and changes no list: `fill` is untouched, and a
        # consumer showing fewer bullets than the packet carries reads section 8
        # to decide which. Run here rather than in `_assemble` because the merged
        # fill is what has the lists, and run ONCE even though `build` is called
        # twice below, so the second build costs no second call.
        priority = self._rank(fill, framing, opportunity)
        # Every other opportunity's section 2, and the two merged slides' own
        # merge. The first section is the fill map just built, so a run with one
        # opportunity produces the identical list of one it produced before.
        sections = [{"fill": fill, "sources": sources,
                     "opportunity": opportunity,
                     "copy": packet_fill.opportunity_copy(opportunity, framing),
                     "slide_copy": packet_fill.slide_copy(framing)}]
        for entry in tuple(assembled)[1:]:
            sections.append(self._section(entry, request, company))
        fill, sources = self._merge_shared_records(fill, sources, sections)
        fill = self._gather_scenarios(fill, sections)
        ratio = completeness_score.data_completeness(packet)
        parts = {
            "request": request,
            "packet": packet_fill.record_unit_mismatch(packet),
            "fill": fill,
            "sources": sources,
            "meta": {
                "generated_at": self._generated_at or _stamp(),
                "company_id": company["id"],
                "confidence": confidence_band(ratio),
            },
            "copy": packet_fill.copy_lines(
                company=company, request=request, project=project,
                opportunity=opportunity, written=framing,
            ),
            # Section 2 repeats, once per opportunity (item 15), each entry
            # carrying that opportunity's own flat fill map and its own two copy
            # lines. The fill map stays FLAT and per-opportunity, which is what
            # keeps `packet_fill` and `second_pass` from having to know a deck
            # can carry more than one.
            "sections": sections,
            "opportunities": packet_fill.opportunity_record(opportunity),
            "confidence_basis": CONFIDENCE_BASIS,
            "second_pass": second_pass_module.ledger(second),
            "templated_fallbacks": fell_back,
            "derived": packet_fill.DERIVED_PATHS,
            "generated": second_pass_module.written_ledger(written),
            "display_priority": priority,
        }
        measured = packet_document.build(**parts)
        coverage = packet_fill.role_coverage(map_packet(measured, request))
        return packet_document.build(role_coverage=coverage, **parts)


# The fill-map paths the DECK owns rather than any one opportunity: the platform
# slide's numbered components and the next-steps list. Item 15 renders one of
# each for the whole deck, so they merge whole from their lead exactly as the
# baseline group does one layer up. Named here because they are fill-map paths
# rather than packet fields, so `base_document` never sees them.
SHARED_RECORD_PATHS = ("platform_layers", "next_steps")

# The one fill-map path that is per opportunity and still renders on a SHARED
# slide. Slide 5 is one combined set, because QofAI contracts the engagement
# rather than the opportunity, so every opportunity's cases become rows on one
# table rather than a table each. See `_gather_scenarios`.
SCENARIO_PATH = "commercial.scenarios"
# The commercial slide's INVESTMENT rows, per opportunity on the same shared
# slide and gathered the same way (`_gather_scenarios`).
INVESTMENT_PATH = "commercial.investment"


def _opportunity_ids(request):
    """The opportunities this request is for, in the order it named them.

    `opportunity_ids` is the contract's field since item 15, and the singular
    `opportunity_id` still works and means a list of one, so every caller that
    submitted a request before today submits exactly the request it did.
    Deduplicated in order: asking for one opportunity twice is a deck with two
    identical slide 2s, which nobody wants and nothing downstream would refuse.
    """
    request = request or {}
    named = list(request.get("opportunity_ids") or ())
    single = request.get("opportunity_id")
    if single and single not in named:
        named.append(single)
    return tuple(dict.fromkeys(name for name in named if name))


def subject_of(opportunity):
    """Which opportunity a reading is FOR, as `source_span.Opportunity`.

    The platform's own id and title, and never its description: the description
    is what the extraction pass is given to read for, which is content, and this
    is what every figure carries, which is provenance. One is 1,772 characters
    on the one real record measured and the other is two short strings.

    Built here rather than in the provider's callers because this is the only
    place holding the opportunity detail `get_opportunity_details` returned.
    """
    opportunity = opportunity or {}
    return source_span.Opportunity(
        id=str(opportunity.get("id") or ""),
        title=str(opportunity.get("title") or ""),
    )


def _stamp():
    """Now, in the frontmatter's own UTC notation."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
