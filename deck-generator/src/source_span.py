"""The source-span primitive (E5a): the golden rule, made mechanical.

A figure with no source behind it is a missing field, never a value. Stage E's
parsers return figures as `SourcedFigure`, unbuildable without the span of paper
text it came from, and record absences through `MissingFields`, unrecordable
without a reason. The names they look for live here too. No parsing in here.
"""

import dataclasses


class MissingSpanError(ValueError):
    """A figure was offered without a span that occurs in its own source."""


class MissingDocumentError(ValueError):
    """A figure was offered without the document its span belongs to."""


class MissingOpportunityError(ValueError):
    """A figure was offered without the opportunity it was read for."""


# What the published opportunity paper is, as a document kind. It arrives from
# the platform rather than from a file, so it is the one kind that never has a
# filename, and it is the kind every span in this repo had until 2026-09-07.
PAPER_KIND = "opportunity_paper"


@dataclasses.dataclass(frozen=True)
class Document:
    """Which document a span belongs to: what it is called and what it is.

    `kind` is a `document_text` kind for an attachment ("pdf", "docx",
    "markdown", "text") and `PAPER_KIND` for the published paper. `name` is the
    reviewer's own filename, and it is empty for the paper alone, which has
    none and never had one. An attachment with no name is a `ValueError`,
    because a reviewer told which of two documents filled a field cannot be told
    "the attachment" when there were two.

    Deliberately not the text. `SourcedFigure.source` already holds the text for
    verification, and a document that carried it again would be a second copy of
    a 40KB string on every figure in a packet.
    """

    name: str
    kind: str

    def __post_init__(self):
        if not isinstance(self.kind, str) or not self.kind.strip():
            raise ValueError("a document needs a kind.")
        if self.kind != PAPER_KIND and not (self.name or "").strip():
            raise ValueError(f"a {self.kind} document needs a name.")

    @property
    def uploaded(self):
        return self.kind != PAPER_KIND

    @property
    def label(self):
        """How to name this document to a reviewer.

        The filename for an attachment, because that is what the reviewer
        called it and what they will recognise, and a fixed phrase for the
        published paper, which has no name of its own.

        Reviewer-facing, and only ever used where a reviewer is reading. It is
        deliberately NOT what a parser's absence reason says: those reasons
        travel into the packet document, which is held free of provenance
        (`tests/test_provenance_never_renders.py`), so they name no document at
        all and the studio's panel does the naming instead.
        """
        return self.name if self.uploaded else "the published opportunity paper"


# The published paper, which is what every figure in this repo came from until a
# reviewer could attach something. Named here so a caller reading a paper does
# not build one of these per call and so the identity comparison is cheap.
PAPER = Document(name="", kind=PAPER_KIND)


@dataclasses.dataclass(frozen=True)
class Opportunity:
    """Which opportunity a figure was read FOR, as opposed to read FROM.

    `id` is the platform's own opportunity id and it is the identity: two
    readings of one document for two opportunities are two different readings,
    and the id is what says so. Required and non-empty, because an opportunity
    that cannot name itself would compare equal to every other one that could
    not, which is the collision this record exists to prevent.

    `title` is reviewer-facing naming and may be empty, the same way
    `Document.name` is empty for the published paper. A caller that knows only
    the id (`packet_assembly.assemble`'s own default) builds one with no title,
    and `label` falls back to the id so a reviewer still sees something.

    Deliberately not the description. The one real opportunity record measured
    2026-08-15 carries 1,772 characters of it, and `Document` already declined
    to hold a document's text for the same reason: a figure is not a place to
    keep a second copy of a long string. The description reaches the extraction
    pass as a prompt argument, where it is content rather than provenance.

    WHY A FIGURE CARRIES THIS AT ALL, which is the whole of item 15's subtlety.
    Until 2026-09-13 a run held one opportunity, so every figure in it belonged
    to that one by construction and the question could not be asked. A deck
    carrying more than one opportunity asks it, and the answer stops being
    obvious the moment a document is SHARED between them: the attached PRD
    describes both of Fabrikam' opportunities, so the same document read twice
    yields figures about different things, and a figure that names only its
    document is telling the truth while saying nothing about which slide it
    belongs on. "Which opportunity" is the same class of question as "which
    document", so it gets the same answer.
    """

    id: str
    title: str = ""

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("an opportunity needs an id.")

    @property
    def label(self):
        """How to name this opportunity to a reviewer.

        The title where there is one, because that is what the reviewer picked
        it by, and the id otherwise, which is never empty. Reviewer-facing, and
        on the same terms as `Document.label`: an absence reason never uses it,
        because those reasons travel into the packet document, which is held
        free of provenance.
        """
        return self.title if (self.title or "").strip() else self.id


@dataclasses.dataclass(frozen=True)
class SourcedFigure:
    """An extracted figure paired with the text it came from and the document
    that text belongs to.

    Every argument is required, so a figure without a span is a TypeError at the
    call site, and a blank span, or one absent from `source`, is a
    `MissingSpanError`: no default, no optional parameter, no validate-later call
    to forget. `source` is the document's text, held for verification and kept
    out of `repr` and equality; serialize `field`, `value`, `span`.

    `document` joined them on 2026-09-07 and is required for the same reason and
    in the same way. The span knew its text and not its document, which was true
    enough while every span in a packet came from the same place, and stopped
    being true the moment a reviewer could attach a document and a packet could
    hold figures from two. "Which document" is the same class of question as
    "which text", so it gets the same answer: no default, and a figure that
    cannot say is a `MissingDocumentError` rather than a figure that quietly
    claims the paper.

    It is compared and shown, unlike `source`. Two figures with the same value
    from different documents are not the same figure, and the difference between
    them is the whole of what the studio has to show a reviewer.

    `opportunity` joined them on 2026-09-13 (item 15) on exactly those terms and
    for exactly that reason one axis over. The document knew which text a figure
    came from, which was enough while a run held one opportunity and every
    figure in it belonged to that one by construction. A deck carrying more than
    one opportunity breaks that, and it breaks hardest where item 14 succeeded:
    an attached PRD is the base for EVERY opportunity in the run, so the same
    document read twice yields figures about different things, and a figure
    naming only its document is telling the truth while saying nothing about
    which slide it belongs on. No default, and a figure that cannot say is a
    `MissingOpportunityError` rather than a figure that quietly claims whichever
    opportunity was read first.

    Compared and shown for the same reason `document` is. Two figures with the
    same value read from one document for two opportunities are not the same
    figure, and a cache that thought they were would serve one opportunity's
    answer as the other's.
    """

    field: str
    value: object
    span: str
    source: str = dataclasses.field(repr=False, compare=False)
    document: Document
    opportunity: Opportunity

    def __post_init__(self):
        if not isinstance(self.span, str) or not self.span.strip():
            raise MissingSpanError(f"{self.field!r} was given no source span.")
        if self.span not in self.source:
            raise MissingSpanError(
                f"{self.field!r} was given a span that does not occur in its source."
            )
        if not isinstance(self.document, Document):
            raise MissingDocumentError(
                f"{self.field!r} was given no document its span belongs to."
            )
        if not isinstance(self.opportunity, Opportunity):
            raise MissingOpportunityError(
                f"{self.field!r} was given no opportunity it was read for."
            )


@dataclasses.dataclass(frozen=True)
class Answer:
    """What one document said about one field, for the reviewer's eyes.

    `span` is the text the answer came from. A unit assembled from several rows,
    which is the scenario table and only the scenario table, has no single row
    that is the answer, so its span is every row it stated joined by newlines:
    still the document's own words, and still nothing composed here.
    """

    document: Document
    value: object
    span: str


@dataclasses.dataclass(frozen=True)
class Conflict:
    """One field more than one document answered, where the answers differ.

    Not a field the base simply won, which is every field the base carries. A
    conflict is a real disagreement between two documents about the same thing,
    and it is the short list a reviewer has to look at.

    `answers` is in precedence order, so `answers[0]` is the one the packet
    carries and the rest are the alternatives. A reviewer reading one of these
    has a DECISION to make, which is what separates it from `SetAside` below.
    Reviewer-facing only: nothing here is rendered on a deck, because a source
    annotation does not belong in front of a client.
    """

    field: str
    answers: tuple

    @property
    def carried(self):
        """The answer the packet holds."""
        return self.answers[0]

    @property
    def alternatives(self):
        """The answers a lower-precedence document gave and the packet does not.

        Named for the decision rather than for the mechanism, because that is
        what a reviewer is looking at: the same figure as somebody else states
        it. What the packet declined to take under a rule about a unit larger
        than the field is `SetAside`, which is a different thing.
        """
        return tuple(self.answers[1:])


@dataclasses.dataclass(frozen=True)
class SetAside:
    """A figure a document stated that the packet did not take, and what took
    its place.

    NOT A CONFLICT, and the difference is the reviewer's. Only one document
    answered this field, so there is no disagreement and nothing to choose. It
    is here because the merge declined to take it under a rule about a unit
    LARGER THAN THE FIELD, which today means the baseline group: the three
    baseline figures and their period are one statement about one span of time,
    so a source that does not lead the baseline does not get to contribute one
    of them.

    Why it has to be written down at all. Without it a reviewer attaches a PRD
    that prices the work and restates no margin, sees EBITDA reported missing,
    and types in by hand the figure the published paper was holding the whole
    time, with nothing anywhere saying that happened or why. Setting the figure
    aside is right; leaving the reviewer to discover it is not.

    `lead` and `lead_period` are what the group came from instead, and `period`
    is the span of time this figure belongs to, which is the fact that makes the
    two incompatible. Both periods are None for a rule where a period is not
    what distinguishes them. A reviewer reading one of these has only something
    to KNOW.

    Reviewer-facing only, the same as `Conflict`, and for the same reason.
    """

    field: str
    answer: Answer
    rule: str
    lead: Document
    lead_period: object = None
    period: object = None


class MissingFields:
    """The absences, each with its reason. Both arguments are required, so an
    absence with no reason is a TypeError. `names` is the contract's
    `missing_fields` list; `entries` keeps the reasons a reviewer needs.
    """

    def __init__(self):
        self._entries = []

    def record(self, field, reason):
        if not str(field).strip() or not str(reason).strip():
            raise ValueError("a missing field needs both a name and a reason.")
        self._entries.append((field, reason))

    @property
    def entries(self):
        return tuple(self._entries)

    @property
    def names(self):
        return [field for field, _ in self._entries]


# What the parsers look for on a paper, in one place. Corrected 2026-08-12
# against the eight excerpts under `data-provider/fixtures/`, superseding the
# labels this map carried from `data-provider/PRD.md` section 2.1c, where only
# `Current LTM Revenue` was quoted verbatim and the rest were paraphrases. A
# parser meeting a label absent from this map reports it rather than adding
# one here.
#
# The evidence base behind the seven assumption labels is one paper, and
# saying so is the point. Exactly one committed fixture,
# `paper-excerpt-assumption-table-and-scenario-rows`, carries an assumption
# table in E5c's sense; six carry none, and `validated-assumption-table`
# carries a validated-claims table whose rows are not a second spelling of
# these fields. So there is no second-company spelling to hold as a tuple, and
# the short alias kept as a second element on `ltm_ebitda` and
# `capacity_utilization` is what makes thin evidence safe rather than a guess:
# it is what matched before this correction, and it keeps matching a paper
# that drops the `Current` prefix.
PAPER_FIELD_NAMES = {
    "ltm_revenue": ("Current LTM Revenue", "LTM Revenue"),
    "ltm_ebitda": ("Current LTM EBITDA", "LTM EBITDA"),
    # No label to correct, measured 2026-08-12 and left as it stands. No
    # assumption table in the eight fixtures carries an EBITDA margin row at
    # all: the margin sits inside the EBITDA row's own value cell
    # (`~$6.78M (~25%)`) and `parse_assumptions` already lifts it from there.
    # The `EBITDA Margin` rows that do exist are E5d's `| Metric |` period
    # tables rather than E5c's assumption tables. Reading a second value out
    # of one cell would be restructuring, and removing the entry would be a
    # structural change, so neither was done here.
    "ltm_ebitda_margin": ("LTM EBITDA Margin",),
    "capacity_utilization": ("Current Capacity Utilization", "Capacity Utilization"),
    "fixed_cost_base": ("Annual Fixed Cost Base",),
    "backlog": ("Existing Backlog",),
    "target_margin": ("Target EBITDA Margin at Scale",),
    # The three entries below are read by no module. `src/scenario_table_parser.py`
    # spells its own column and row labels and never consults this map. They are
    # corrected 2026-08-12 as a record, because a known-false entry that three
    # parser docstrings treat as authoritative is worse than no entry.
    #
    # Bare spellings only. A spelling that occurs only decorated with a figure
    # is named in the comment above its entry rather than stored: stripping a
    # decoration is out of scope, and a stored figure would bake one company's
    # detail into this file.
    #
    # `Upside` is absent below because it occurs only as
    # `Upside: $2.3M commodity volume`.
    "scenario_cases": (
        "Conservative",
        "Base Case",
        "Moderate",
        "Mid-range",
        "Mid-Range",
        "Optimistic",
        "Aggressive",
    ),
    # `EBITDA Impact (25% margin)` and `EBITDA Impact (at 25% margin)` are
    # absent below: both occur only carrying that paper's margin assumption.
    "incremental_ebitda": (
        "Incremental EBITDA",
        "Incremental Annual EBITDA",
        "Net EBITDA Impact",
        "EBITDA Contribution",
        "Total Annual Impact",
        "Annual Savings",
    ),
    # `EBITDA Margin Lift (on $26.2M)` is absent below for the same reason.
    # `(pp)` is kept, being a unit rather than a figure.
    "margin_impact_pp": ("EBITDA Margin Impact", "EBITDA Margin Impact (pp)"),
}
