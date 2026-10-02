"""Module 2 — Data Source Adapter.

The only module that touches client data. It sits behind one interface with two
internal halves (PRD §4 "The adapter's internal split"):

- Transport half (this file, for now): shape the contract request, dispatch the
  ``proposal-data-provider`` subagent in background mode, poll for completion,
  receive the response envelope, branch on ``status``, and check the two gates
  (``min_confidence`` / ``min_data_completeness``). It never renders and never
  maps; on a clean, gate-clearing ok-packet it hands the raw packet forward to
  the mapping half.
- Mapping half (added next): translate a clean ok-packet's structured blocks and
  labeled copy lines into the flat placeholder map, plus the ``_provenance`` /
  ``_gaps`` side channels.

The seam is the ``provider``: a background-dispatch object with ``submit`` /
``poll``. Against the frozen example packet the provider is a ``FixtureProvider``;
when the live ``proposal-data-provider`` ships it plugs in here with no change to
the mapping half or to Modules 1 and 3 (PRD criterion 9). Background dispatch is
mandatory even against the fixture: the live contract says foreground appears to
time out because KG plus walker assembly takes 30 to 90 seconds (PRD §2.C), so the
transport half models submit-then-poll regardless of what backs it.

Both non-render outcomes are normal, handled results, not thrown exceptions:
- an error envelope returns ``{"status": "error", ...}`` with the surfaced
  ``code`` / ``message`` / ``remediation`` (PRD criterion 5), and
- a gate failure returns ``{"status": "review", ...}`` with the packet's
  ``confidence`` / ``data_completeness`` / ``missing_fields`` (PRD criterion 4).
Neither ever yields a map, which is the contract's golden rule (§5).
"""

import re
import time

import bullet_toggles
import panel_fit

# Request constants fixed by the contract (proposal-data-request-CONTRACT.md §2).
INTENT = "generate_project_planning_proposal"
SCHEMA_VERSION = "0.1"
DEFAULT_TEMPLATE = "project_planning_v2"

# The six proposal sections, 1:1 with the proposal template's slides
# (templates/proposal-template.md "Section keys"). Default request asks for all.
ALL_SECTIONS = (
    "cover",
    "opportunity",
    "platform",
    "rollout",
    "commercial_terms",
    "next_steps",
)

# --- status path request constants (status-data-request-CONTRACT.md §2) -------
# The status path reuses the transport half unchanged; only these request
# constants and the mapping half differ, both selected by ``deck_type``. Keeping
# the status field names on this side of the module is deliberate: a status
# schema bump is a mapping-half edit, isolated from Modules 1 and 3 (PRD §5.2.C).
STATUS_INTENT = "generate_project_status_deck"
STATUS_DEFAULT_TEMPLATE = "status_check_in_v1"

# The four status sections, in template-slide order. Unlike the proposal's, the
# `workstreams` section expands to a variable number of slides (one per active
# workstream), so section keys — not slide numbers — drive skip handling here.
STATUS_ALL_SECTIONS = (
    "cover",
    "tracking",
    "workstreams",
    "next_steps",
)

# The section-key → slide-number correspondence, mirroring the proposal
# template's own "Section keys" table (templates/proposal-template.md). It is 1:1
# and ordered, so the slide number is the section's position in ALL_SECTIONS.
# Module 2 uses it to report skipped slides (the _skipped side channel) in the
# slide-number space Module 3 iterates in, so Module 3 need not know section keys.
SECTION_KEY_TO_SLIDE = {key: index + 1 for index, key in enumerate(ALL_SECTIONS)}

# Request options: the contract's server-side defaults (§2). These are configured
# request parameters the adapter sends, not per-run user choices (PRD §3). A
# caller may override any of them; the gate parameters (min_confidence,
# min_data_completeness) feed the render decision in PRD criterion 4.
DEFAULT_OPTIONS = {
    "currency": "USD",
    "voice": "qofai_standard",
    "min_confidence": "medium",
    "min_data_completeness": 0.70,
    "include_prose": True,
    "scenario_set": ["conservative", "base", "optimistic"],
}

# Status request options (status-data-request-CONTRACT.md §2): the shared gates
# plus the status-specific workstream-selection options that set the slide count.
STATUS_DEFAULT_OPTIONS = {
    "currency": "USD",
    "voice": "qofai_standard",
    "min_confidence": "medium",
    "min_data_completeness": 0.70,
    "include_prose": True,
    "workstream_filter": "active",
    "include_completed_workstreams": False,
}

# Per-deck-type request profile, selected by ``deck_type`` in ``shape_request``.
# The transport half is otherwise identical; this is the one place the request
# differs between the two paths.
_REQUEST_PROFILES = {
    "proposal": {
        "intent": INTENT,
        "template": DEFAULT_TEMPLATE,
        "sections": ALL_SECTIONS,
        "options": DEFAULT_OPTIONS,
    },
    "status": {
        "intent": STATUS_INTENT,
        "template": STATUS_DEFAULT_TEMPLATE,
        "sections": STATUS_ALL_SECTIONS,
        "options": STATUS_DEFAULT_OPTIONS,
    },
}

# Confidence is an ordered enum; a packet clears the gate only if its confidence
# is at least the requested minimum (contract §2 "abort if packet confidence
# below this").
CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}


class FixtureProvider:
    """In-process stand-in for the live ``proposal-data-provider`` subagent.

    Models the background dispatch protocol the transport half depends on:
    ``submit`` returns a handle, ``poll`` reports completion. ``ready_after``
    lets a test require N polls before the envelope is ready, standing in for the
    live provider's 30-to-90-second KG-plus-walker assembly, so the transport
    half's poll loop is exercised rather than assumed.

    Fixture-only compatibility exception (PRD §1, build plan Module 2 transport
    half): the frozen example packet predates the explicit ``project`` field and
    is implicitly scoped to one project. This provider returns that single frozen
    packet for whatever ``project`` the request names, so tests run without a
    second project fixture. This does not weaken the live contract: on the live
    path the provider resolves the project inside the company and returns
    E_PROJECT_REQUIRED / E_AMBIGUOUS_PROJECT / E_PROJECT_NOT_FOUND when
    appropriate. Those codes are tested here by feeding them as error envelopes
    (see ``from_envelope``), not by real resolution.
    """

    def __init__(self, envelope, ready_after=1):
        if ready_after < 1:
            raise ValueError("ready_after must be at least 1")
        self._envelope = envelope
        self._ready_after = ready_after
        self._polls = {}
        self._next_handle = 0

    @classmethod
    def from_envelope(cls, envelope, ready_after=1):
        """Return a fixed envelope verbatim. Use for error and gate-failure
        fixtures (feed an ``{"status": "error", ...}`` or a low-confidence
        ok-packet envelope)."""
        return cls(envelope, ready_after)

    @classmethod
    def from_packet_markdown(cls, packet_md, ready_after=1, request_echo=None):
        """Wrap raw packet markdown in an ok envelope."""
        envelope = {"status": "ok", "packet": packet_md}
        if request_echo is not None:
            envelope["request_echo"] = request_echo
        return cls(envelope, ready_after)

    @classmethod
    def from_packet_file(cls, path, ready_after=1, request_echo=None):
        """Wrap a packet markdown file (the frozen example packet) in an ok
        envelope."""
        with open(path, "r", encoding="utf-8") as f:
            packet_md = f.read()
        return cls.from_packet_markdown(packet_md, ready_after, request_echo)

    def submit(self, request, uploads=()):
        # The argument is named so a caller passing attachments (item 14) gets a
        # sentence rather than a `TypeError`, and it refuses rather than ignores
        # for the reason the whole item turns on: this provider replays a frozen
        # packet, so a dropped attachment would be a deck written from the
        # fixture while the reviewer believed it came from their document.
        if uploads:
            raise ValueError(
                "the fixture provider replays a frozen packet and cannot read "
                "an attachment; run the live source to build a deck from an "
                "uploaded document."
            )
        handle = self._next_handle
        self._next_handle += 1
        self._polls[handle] = 0
        return handle

    def poll(self, handle):
        self._polls[handle] += 1
        if self._polls[handle] >= self._ready_after:
            return {"done": True, "envelope": self._envelope}
        return {"done": False, "envelope": None}


def shape_request(
    company,
    project,
    *,
    company_id=None,
    deck_type="proposal",
    deck_title=None,
    pe_firm=None,
    proposal_date=None,
    check_in_date=None,
    opportunity_id=None,
    opportunity_ids=None,
    client_short=None,
    template=None,
    sections_requested=None,
    options=None,
):
    """Shape the contract request YAML-equivalent dict (contract §2).

    ``company`` and ``project`` are references the live provider resolves (a
    name, a name plus ``pe_firm``, or a UUID); this half sends them, it does not
    resolve them. ``company_id`` is the candidate a reviewer picked out of an
    ``E_AMBIGUOUS_COMPANY`` list (item 17, 2026-09-15) and it travels BESIDE
    ``company`` rather than replacing it: the live provider has no lookup-by-id
    tool, so the id narrows the name's own search to one of its results and is
    useless without the name. Sent only when there is one, so a request for a
    name that resolves to a single company is byte-identical to the request it
    was before this field existed. ``deck_type`` selects the request profile — intent, default
    template, section set, and default options — for the proposal or status
    contract; the transport half is otherwise identical. ``options`` overrides
    merge over the selected profile's defaults. ``check_in_date`` is the
    status-only as-of date (status-data-request-CONTRACT.md §2); it is ignored on
    the proposal path. ``opportunity_id`` is the reviewer's pick (E9e) of which
    published opportunity to build the proposal from; left unset, the live
    provider falls back to its own first-published-with-a-paper default.
    ``client_short`` is the deck's short brand form for the page footers, and it
    is here for the reason ``deck_title`` is: no data source supplies it.
    ``company.client_short`` is UNSOURCEABLE in the packet's own slot table and
    is filled on no path, so without this every live deck's footers carry the
    company's full registry name. Studio input, winning over the derived value
    and degrading to it when blank.

    ``opportunity_ids`` is the same field for a deck carrying MORE THAN ONE
    (item 15, 2026-09-13): the opportunities in the order the reviewer picked
    them, which is the order their slides read in. The singular field still
    works and means a list of one, so every caller that sent a request before
    today sends exactly the request it did, and there is no ceiling on the
    count. Nothing
    client-specific is baked in: every client/project value comes from the
    caller's arguments.
    """
    if not company:
        raise ValueError("company reference is required")
    if not (project or deck_title or opportunity_id or opportunity_ids):
        # A request has to say what the deck is about. A project reference is the
        # usual way (PRD §3) and was the only way until 2026-08-20; a `deck_title`
        # names the deck directly, and an `opportunity_id` names it by derivation,
        # because the live provider titles a project-less deck from the
        # opportunity it was told to build from. None of the three is a
        # caller-side contract violation, distinct from the provider's
        # E_PROJECT_REQUIRED (which is returned on the live path and tested here
        # as an error envelope).
        raise ValueError(
            "the request must name the deck: a project reference, a deck_title, "
            "or an opportunity_id to derive it from"
        )

    profile = _REQUEST_PROFILES.get(deck_type)
    if profile is None:
        raise ValueError(f"unknown deck_type: {deck_type!r}")

    merged_options = dict(profile["options"])
    if options:
        merged_options.update(options)

    request = {
        "intent": profile["intent"],
        "company": company,
        "project": project,
        "schema_version": SCHEMA_VERSION,
        "template": template or profile["template"],
        "sections_requested": list(sections_requested or profile["sections"]),
        "options": merged_options,
    }
    if company_id:
        request["company_id"] = company_id
    if deck_title:
        request["deck_title"] = deck_title
    if pe_firm:
        request["pe_firm"] = pe_firm
    if proposal_date:
        request["proposal_date"] = proposal_date
    if check_in_date:
        request["check_in_date"] = check_in_date
    if opportunity_id:
        request["opportunity_id"] = opportunity_id
    if opportunity_ids:
        request["opportunity_ids"] = list(opportunity_ids)
    if client_short:
        request["client_short"] = client_short
    return request


def _await_envelope(provider, request, poll_interval, max_polls, sleep, uploads=()):
    """Background dispatch: submit, then poll until the provider reports done.

    Mirrors the mandatory background mode from the contract. The fixture
    completes on its configured poll; the live provider completes when KG plus
    walker assembly finishes.

    `uploads` are the reviewer's attachments (item 14) and they are passed only
    when there are any, so a provider whose `submit` takes a request alone stays
    a provider this function can drive. The seam is one argument wide for every
    run that attaches nothing, which is every run that worked before.
    """
    handle = (provider.submit(request, uploads=uploads) if uploads
              else provider.submit(request))
    for _ in range(max_polls):
        status = provider.poll(handle)
        if status.get("done"):
            return status["envelope"]
        sleep(poll_interval)
    raise TimeoutError(
        f"proposal-data-provider did not complete within {max_polls} polls"
    )


def _read_packet_frontmatter(packet_md):
    """Read the packet's leading YAML frontmatter for the gate check.

    Returns ``{"confidence": str|None, "data_completeness": float|None,
    "packet_type": str|None}``. Only envelope-level metadata is read here; the
    structured-block parsing the gates do not need belongs to the mapping half
    (the transport/mapping seam).

    ``packet_type`` is what the data source says this packet IS
    (``project_planning_proposal`` / ``project_status_check_in``). It is read here
    rather than in the mapping half because it decides whether the mapping half
    should run at all: a packet whose type does not match the requested
    ``deck_type`` is the wrong data for the deck, and every downstream field is
    then unmappable.
    """
    match = re.match(r"^---\n(.*?)\n---", packet_md, re.DOTALL)
    if not match:
        raise ValueError("packet has no frontmatter block")
    frontmatter = match.group(1)

    confidence = None
    conf_match = re.search(
        r'^confidence:\s*"?([A-Za-z]+)"?', frontmatter, re.MULTILINE
    )
    if conf_match:
        confidence = conf_match.group(1)

    completeness = None
    comp_match = re.search(
        r"^data_completeness:\s*([0-9]*\.?[0-9]+)", frontmatter, re.MULTILINE
    )
    if comp_match:
        completeness = float(comp_match.group(1))

    packet_type = None
    type_match = re.search(
        r'^packet_type:\s*"?([A-Za-z0-9_.-]+)"?', frontmatter, re.MULTILINE
    )
    if type_match:
        packet_type = type_match.group(1)

    return {
        "confidence": confidence,
        "data_completeness": completeness,
        "packet_type": packet_type,
    }


def _passes_gates(frontmatter, min_confidence, min_data_completeness):
    """True only if the packet clears both hard gates (contract §2)."""
    confidence = frontmatter.get("confidence")
    completeness = frontmatter.get("data_completeness")
    if confidence is None or completeness is None:
        return False
    if CONFIDENCE_ORDER.get(confidence, -1) < CONFIDENCE_ORDER.get(min_confidence, 1):
        return False
    if completeness < min_data_completeness:
        return False
    return True


def dispatch_and_gate(
    company,
    project,
    provider,
    *,
    company_id=None,
    deck_type="proposal",
    deck_title=None,
    pe_firm=None,
    proposal_date=None,
    check_in_date=None,
    opportunity_id=None,
    opportunity_ids=None,
    client_short=None,
    template=None,
    sections_requested=None,
    options=None,
    uploads=(),
    poll_interval=2.0,
    max_polls=60,
    sleep=time.sleep,
):
    """Transport half: shape, dispatch (background), poll, branch, gate-check.

    Returns one of three discriminated results, none of them exceptions:

    - error envelope (branch point one) →
      ``{"status": "error", "code", "message", "remediation", "details"}``.
      Every contract error code, including the project-level ones
      (E_PROJECT_REQUIRED / E_AMBIGUOUS_PROJECT / E_PROJECT_NOT_FOUND), is
      surfaced this way and produces no packet; the adapter never falls back to
      company-level data (PRD criterion 5).
    - gate failure (branch point two) →
      ``{"status": "review", "confidence", "data_completeness", "missing_fields"}``.
      The packet is withheld from the mapping half (PRD criterion 4).
    - clean ok-packet clearing both gates →
      ``{"status": "ok", "packet", "request_echo", "confidence",
      "data_completeness"}``. This is the hand-off to the mapping half; ``ok``
      here means "gates cleared, packet ready to map", not "map produced".

    ``uploads`` are the reviewer's attachments (item 14), a sequence of
    ``base_document.Upload``. They are dispatched beside the request rather than
    inside it, because ``shape_request`` builds the contract request and that
    request is echoed into the packet document and the deck store, which is not
    a place for a file's bytes. A provider decides what an attachment means: the
    live one makes the first attachment the base document the deck is written
    from, and the fixture one refuses. Passing none is the path every caller
    took before this argument existed.

    Retry/backoff for the retry-and-escalate code (E_KG_UNREACHABLE) and
    re-request handling for E_AMBIGUOUS_COMPANY / E_BAD_REQUEST are live-path
    concerns layered on the surfaced error later; against fixtures every error
    code arrives as a terminal envelope. The non-negotiable bar the contract
    sets is that none of them ever yields a rendered prompt, which holds here
    because only the clean-ok branch carries a packet forward.
    """
    request = shape_request(
        company,
        project,
        company_id=company_id,
        deck_type=deck_type,
        deck_title=deck_title,
        pe_firm=pe_firm,
        proposal_date=proposal_date,
        check_in_date=check_in_date,
        opportunity_id=opportunity_id,
        opportunity_ids=opportunity_ids,
        client_short=client_short,
        template=template,
        sections_requested=sections_requested,
        options=options,
    )

    envelope = _await_envelope(provider, request, poll_interval, max_polls, sleep,
                               uploads=uploads)
    status = envelope.get("status")

    # Branch point one — the envelope status.
    if status == "error":
        error = envelope.get("error", {})
        return {
            "status": "error",
            "code": error.get("code"),
            "message": error.get("message"),
            "remediation": error.get("remediation"),
            "details": error.get("details", {}),
        }
    if status != "ok":
        # Not a contract-defined status: a malformed envelope from the provider,
        # a provider bug rather than a handled business outcome.
        raise ValueError(f"unrecognized envelope status: {status!r}")

    packet_md = envelope.get("packet")
    if not packet_md:
        raise ValueError("ok envelope carried no packet")

    # Branch point two — the gates. Belt-and-suspenders re-check of the packet's
    # own frontmatter against the request thresholds (contract §4).
    frontmatter = _read_packet_frontmatter(packet_md)
    min_confidence = request["options"]["min_confidence"]
    min_data_completeness = request["options"]["min_data_completeness"]

    if not _passes_gates(frontmatter, min_confidence, min_data_completeness):
        review = {
            "status": "review",
            "confidence": frontmatter["confidence"],
            "data_completeness": frontmatter["data_completeness"],
            # The packet frontmatter carries no missing_fields list; the richer
            # per-field gap reporting is read from packet §8 by the mapping half.
            # Agent OS's own E_LOW_CONFIDENCE error envelope carries missing_fields
            # in its details and is surfaced through the error branch above.
            "missing_fields": [],
        }
        if uploads:
            # AN INSUFFICIENT PRD IS ITS OWN ANSWER, not a thin packet.
            # Antonio, 2026-09-20: a run with an uploaded PRD is answered from
            # that PRD or is refused, and the refusal says the PRD is short
            # rather than pointing at another document. Since
            # `base_document.precedence` stopped appending the paper to an
            # attached chain, a gate failure here means exactly one thing: the
            # uploaded document does not carry what a deck needs. Naming the
            # paper as a remedy would invite the merge the rule exists to stop.
            review["insufficient_document"] = True
            review["document_names"] = [upload.filename for upload in uploads]
        return review

    # Clean ok-packet, both gates cleared: hand off to the mapping half.
    return {
        "status": "ok",
        "packet": packet_md,
        "request_echo": envelope.get("request_echo", request),
        "confidence": frontmatter["confidence"],
        "data_completeness": frontmatter["data_completeness"],
        # What the data source says this packet is, so the caller can check it
        # against the deck it was asked to build before mapping a single field.
        "packet_type": frontmatter["packet_type"],
        # Which document filled which field, where the documents disagreed, and
        # what the merge set aside (item 14, `provenance_report`). Reviewer-facing
        # only: it is carried BESIDE the packet and never inside it, so nothing
        # here can reach the placeholder map, the prompt or a deck. Empty on a
        # run with no attachment, which is every run that names one source.
        "provenance": envelope.get("provenance") or {},
        # What the deck was built from, for the deck store (item 14, step 5):
        # each attachment's extracted text, filename, kind, size and SHA-256.
        # Carried the same way and for the same reason as `provenance` above —
        # beside the packet, never inside it — so nothing here can reach the
        # placeholder map, the prompt or a deck. Empty on a run with no
        # attachment.
        "attachments": envelope.get("attachments") or [],
        # WHICH FRAMING LINES WERE WRITTEN AND WHICH REFUSED (item 24). Carried
        # the same way and for the same reason as the two above: the studio
        # cannot see inside the provider, and this is the only record that
        # distinguishes a slot the model declined from one whose answer restated
        # the deck standard it was asked to replace. Falsy when no writing pass
        # ran, which is every fixture run.
        "writing_ledger": envelope.get("writing_ledger") or None,
        # Part A4: the Next Steps gaps the document seems to state. Carried the
        # same way and for the same reason as the two above.
        "flag_audit": envelope.get("flag_audit") or [],
        # What each opportunity's section scored and whether it cleared the
        # floor (item 15). Carried the same way and for the same reason as the
        # two above.
        "opportunities": envelope.get("opportunities") or [],
    }


# ===========================================================================
# Mapping half
# ---------------------------------------------------------------------------
# Runs only on a clean ok-packet that cleared both gates. It resolves the
# packet's structured blocks by key path and carries its labeled copy lines
# through verbatim, following the build plan's per-slide role tables (those
# tables are authoritative; nothing is improvised). It invents nothing and does
# no free-text extraction. Residual gaps with no packet field are derived where
# the plan says to, otherwise left empty so Module 3 emits the missing marker.
# The §8 side channels (_provenance, _gaps) are extracted for Module 3.
# ===========================================================================

# --- fixture-tolerant packet parser -----------------------------------------
# The frozen packet is YAML-shaped but not strictly valid YAML: some inline
# records are space-separated rather than comma-separated (timeline workstreams,
# milestones), so a stock loader rejects it. This scoped, indentation-based
# reader handles the block shapes the contract defines plus the fixture's
# quirks: space- or comma-separated inline maps, brace-wrapped inline maps,
# underscore integers, flow lists, quoted and bare scalars, and inline comments.

_SECTION_RE = re.compile(r"^##\s+(\d+)\s*·")


def _strip_inline_comment(text):
    in_quote = False
    for i, char in enumerate(text):
        if char == '"':
            in_quote = not in_quote
        elif char == "#" and not in_quote and (i == 0 or text[i - 1] == " "):
            return text[:i].rstrip()
    return text


def _parse_scalar(raw):
    value = raw.strip().rstrip(",").strip()
    if value.startswith("[") and value.endswith("]"):
        return _parse_flow_list(value)
    if value.startswith("{") and value.endswith("}"):
        return _parse_inline_map(value)
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    if value in ("null", "~", ""):
        return None
    if value == "true":
        return True
    if value == "false":
        return False
    if re.fullmatch(r"-?[0-9][0-9_]*", value):
        return int(value.replace("_", ""))
    if re.fullmatch(r"-?[0-9_]*\.[0-9]+", value):
        return float(value.replace("_", ""))
    return value


def _split_top_level(body, seps):
    """Split ``body`` on any char in ``seps`` that sits outside double quotes."""
    parts, buf, in_quote = [], [], False
    for char in body:
        if char == '"':
            in_quote = not in_quote
            buf.append(char)
        elif char in seps and not in_quote:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(char)
    parts.append("".join(buf))
    return [p for p in (s.strip() for s in parts) if p]


def _parse_flow_list(raw):
    inner = raw.strip()[1:-1]
    return [_parse_scalar(item) for item in _split_top_level(inner, ",")]


def _parse_inline_map(raw):
    """Parse ``key: value`` pairs on one line, space- or comma-separated, with
    or without wrapping braces. Values may be quoted (a quoted value may contain
    commas or spaces)."""
    text = raw.strip()
    if text.startswith("{"):
        text = text[1:]
    if text.endswith("}"):
        text = text[:-1]
    result = {}
    i, n = 0, len(text)
    while i < n:
        while i < n and text[i] in " ,":
            i += 1
        key_match = re.match(r"([A-Za-z_][A-Za-z0-9_]*):", text[i:])
        if not key_match:
            break
        key = key_match.group(1)
        i += key_match.end()
        while i < n and text[i] == " ":
            i += 1
        if i < n and text[i] == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 1
            raw_value = text[i:j + 1]
            i = j + 1
        else:
            j = i
            while j < n:
                if text[j] == ",":
                    break
                if text[j] == " " and re.match(
                    r"\s+[A-Za-z_][A-Za-z0-9_]*:", text[j:]
                ):
                    break
                j += 1
            raw_value = text[i:j]
            i = j
        result[key] = _parse_scalar(raw_value)
    return result


def _is_inline_mapping(text):
    return text.startswith("{") or bool(re.match(r"[A-Za-z_][A-Za-z0-9_]*:\s", text + " "))


def _parse_block(lines, pos):
    if lines[pos][1].startswith("- "):
        return _parse_sequence(lines, pos)
    return _parse_mapping(lines, pos)


def _parse_mapping(lines, pos):
    indent = lines[pos][0]
    mapping = {}
    while pos < len(lines) and lines[pos][0] == indent and not lines[pos][1].startswith("- "):
        match = re.match(r"([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", lines[pos][1])
        if not match:
            pos += 1
            continue
        key, rest = match.group(1), match.group(2).strip()
        pos += 1
        if rest == "":
            if pos < len(lines) and lines[pos][0] > indent:
                mapping[key], pos = _parse_block(lines, pos)
            else:
                mapping[key] = None
        else:
            mapping[key] = _parse_scalar(rest)
    return mapping, pos


def _parse_sequence(lines, pos):
    indent = lines[pos][0]
    items = []
    while pos < len(lines) and lines[pos][0] == indent and lines[pos][1].startswith("- "):
        rest = lines[pos][1][2:].strip()
        pos += 1
        child_start = pos
        while pos < len(lines) and lines[pos][0] > indent:
            pos += 1
        child_lines = lines[child_start:pos]
        if _is_inline_mapping(rest):
            if rest.endswith(":") and child_lines:
                # A KEY WITH NO VALUE ON THE DASH LINE. Its value is whatever
                # follows at a deeper indent, and the item's remaining keys
                # resume at the column that key starts in, which is the dash
                # indent plus the two characters of "- ". Reassembling the item
                # as a mapping anchored there is the only way to read both.
                #
                # Found 2026-09-13 when section 2 became a list of
                # per-opportunity entries whose first key is a list of strings.
                # The old path parsed the child lines with `_parse_block`, which
                # saw a sequence, returned a list rather than a dict, discarded
                # it, and stopped: the first key came back null and every
                # sibling after it vanished. It could not have fired before,
                # because no emitted item had ever begun with a block key.
                item, _ = _parse_mapping([(indent + 2, rest)] + child_lines, 0)
            else:
                item = _parse_inline_map(rest)
                if child_lines:
                    child, _ = _parse_block(child_lines, 0)
                    if isinstance(child, dict):
                        item.update(child)
        else:
            item = _parse_scalar(rest)
        items.append(item)
    return items, pos


def _parse_packet_yaml(text):
    lines = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        content = _strip_inline_comment(raw.strip())
        if content:
            lines.append((indent, content))
    if not lines:
        return {}
    value, _ = _parse_block(lines, 0)
    return value


# --- section splitting, structured and copy extraction ----------------------

def _split_sections(packet_md):
    """Map each ``## N ·`` section number to its markdown text (frontmatter and
    the intro before §0 dropped)."""
    all_lines = packet_md.splitlines()
    indices = [i for i, line in enumerate(all_lines) if _SECTION_RE.match(line)]
    sections = {}
    for k, start in enumerate(indices):
        end = indices[k + 1] if k + 1 < len(indices) else len(all_lines)
        number = int(_SECTION_RE.match(all_lines[start]).group(1))
        sections[number] = "\n".join(all_lines[start:end])
    return sections


def _section_yaml(section_text):
    """Merge every fenced ```yaml block in a section into one dict."""
    merged = {}
    for match in re.finditer(r"```yaml\n(.*?)\n```", section_text, re.DOTALL):
        block = _parse_packet_yaml(match.group(1))
        if isinstance(block, dict):
            merged.update(block)
    return merged


def _prose(section_text):
    """The section text with its fenced yaml blocks removed, so copy-label
    lookups never match a yaml key that happens to share the label's name."""
    return re.sub(r"```yaml\n.*?\n```", "", section_text, flags=re.DOTALL)


def _bold(text):
    match = re.search(r"\*\*(.+?)\*\*", text)
    return match.group(1).strip() if match else None


def _strip_emphasis(text):
    return text.replace("**", "").replace("*", "").strip()


def _label_remainder(section_text, label):
    match = re.search(
        rf"^{re.escape(label)}:\s*(.*)$", _prose(section_text), re.MULTILINE
    )
    return match.group(1).strip() if match else None


def _headline(section_text):
    """The bold copy on a section's ``Headline:`` line."""
    remainder = _label_remainder(section_text, "Headline")
    if remainder is None:
        return ""
    return _bold(remainder) or _strip_emphasis(remainder)


def _headline_italic(section_text):
    """The italic sub-line sharing the ``Headline:`` line (used for §7's
    next-steps summary, which the packet writes after the bold headline)."""
    remainder = _label_remainder(section_text, "Headline")
    if remainder is None:
        return ""
    without_bold = re.sub(r"\*\*.+?\*\*", "", remainder)
    return _strip_emphasis(without_bold)


def _subhead_line(section_text, label):
    """The full copy on a One-liner / Subhead line, emphasis markers stripped
    (they are packet presentation; the text is carried verbatim)."""
    remainder = _label_remainder(section_text, label)
    return _strip_emphasis(remainder) if remainder is not None else ""


def _cover_bullet(section_text, label):
    """A §1 cover-copy bullet: ``- **Label:** `value` `` — return the backtick
    value carried verbatim."""
    match = re.search(
        rf"-\s*\*\*{re.escape(label)}:\*\*\s*`([^`]*)`", _prose(section_text)
    )
    return match.group(1).strip() if match else ""


# --- formatting helpers for derived roles (never invented) ------------------

# A dollar RANGE exactly as `packet_fill._stated` writes one: two bare numbers on
# the deck's own en dash, with no unit on either, because naming the unit is this
# layer's job. Matched whole, so a string carrying anything else still passes
# through untouched rather than being half-formatted.
_USD_RANGE_RE = re.compile(r"(\d+(?:\.\d+)?)–(\d+(?:\.\d+)?)")


def _fmt_usd(amount):
    """A dollar figure, or a dollar range, in the deck's own house style.

    A single figure reads ``$575,000``, unchanged. A range reads
    ``$575,000–$862,000``, with the sign on BOTH endpoints: that is the form the
    template's own example states (``EBITDA margin uplift — $1.3M–$1.8M / yr
    direct``) and the form both reference packets carry, so it is settled house
    style rather than a choice made here. Each endpoint is formatted exactly as a
    lone figure would be, so no arithmetic and no re-rounding touches either half
    (E11 Stage 2e). Anything else non-numeric still passes through as it arrived.
    """
    if amount is None:
        return ""
    if isinstance(amount, (int, float)):
        return f"${amount:,}"
    match = _USD_RANGE_RE.fullmatch(str(amount))
    if match:
        return "–".join(_fmt_usd(_whole(half)) for half in match.groups())
    return str(amount)


def _whole(text):
    """A numeric string as an int where it is integral, so it groups as one."""
    value = float(text)
    return int(value) if value.is_integer() else value


def _fmt_multiple(value):
    """Render an EV multiple: drop a bare trailing ``.0`` (``6.0`` → ``"6"``) but
    keep a real fraction (``0.25`` → ``"0.25"``). Non-numeric passes through."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _fmt_usd_approx_millions(amount):
    """Approximate USD in millions, one decimal, leading ``~`` — the deck
    footnote form (``6_900_000`` → ``"~$6.9M"``, ``1_725_000`` → ``"~$1.7M"``).
    Non-numeric passes through unchanged."""
    if not isinstance(amount, (int, float)):
        return str(amount)
    return f"~${amount / 1_000_000:.1f}M"


def _fmt_usd_display(amount):
    """A dollar figure for a 24px METRIC slot, abbreviated to its magnitude.

    `_fmt_usd` is built for a table cell and spells every digit, which is right
    there and wrong here: the baseline EBITDA of a large client rendered as
    `$40,500,000,000` on a live deck 2026-08-19, eleven digits across a display
    slot whose own house-style example is `$1.3M-$1.8M`.

    The tilde follows `_fmt_usd_approx_millions`, and for the same reason: a
    figure shown to one decimal of its magnitude has been rounded, and the deck
    says so rather than presenting an approximation as exact. It is dropped when
    nothing was actually rounded away, so `$40.5B` does not apologise for a
    precision it did not lose.

    Below a million there is nothing to gain: `$575,000` is already short enough
    for the slot and exact, so it passes to `_fmt_usd` unchanged. Anything
    non-numeric passes through untouched, as everywhere else here.
    """
    if not isinstance(amount, (int, float)):
        return _fmt_usd(amount)
    for cutoff, suffix in ((1_000_000_000, "B"), (1_000_000, "M")):
        if abs(amount) >= cutoff:
            scaled = round(abs(amount) / cutoff, 1)
            exact = scaled * cutoff == abs(amount)
            # The sign belongs outside the dollar mark: `-$2.4M`, never `$-2.4M`.
            sign = "-" if amount < 0 else ""
            return f"{'' if exact else '~'}{sign}${scaled:g}{suffix}"
    return _fmt_usd(amount)


def _ev_footnote_role(commercial):
    """Compose the EV footnote from the packet's structured ``ev_assumptions`` so
    the reporting-readiness dollar contribution renders explicitly, matching the
    reference deck (the FBK footnote states "~$5.4M from a 0.25× reporting-
    readiness multiple improvement on ~$21.8M adjusted EBITDA").

    Composing from the assumptions, rather than copying the prose ``ev_footnote``
    blind, is what makes the four ``ev_assumptions`` fields genuinely carried
    (they are slotted to ``terms_footnote`` in the coverage guard). The prose
    line often omits the resulting dollar figure — the packet field exists but no
    reader ever sees it — which is exactly the silent gap this closes. Falls back
    to the packet's ``ev_footnote`` prose when any assumption is absent, since
    there is then nothing to compose from. The connective text is a template
    constant, in the same spirit as ``after_horizon`` / ``build_summary``; every
    number comes from the packet.
    """
    prose = commercial.get("ev_footnote", "")
    assumptions = commercial.get("ev_assumptions") or {}
    exit_multiple = assumptions.get("exit_ebitda_multiple")
    rr_multiple = assumptions.get("reporting_readiness_multiple_uplift")
    ebitda_base = assumptions.get("adjusted_ebitda_base_usd")
    rr_ev = assumptions.get("reporting_readiness_ev_usd")
    if None in (exit_multiple, rr_multiple, ebitda_base, rr_ev):
        return prose
    return (
        f"Enterprise value at exit assumes a {_fmt_multiple(exit_multiple)}× "
        f"EBITDA multiple on the annual uplift, plus "
        f"{_fmt_usd_approx_millions(rr_ev)} from a {_fmt_multiple(rr_multiple)}× "
        f"reporting-readiness multiple improvement on "
        f"{_fmt_usd_approx_millions(ebitda_base)} adjusted EBITDA. "
        "Directional, for decision support."
    )


def _metric(metric):
    """Fold a {value, label} metric into its single string role (the template
    declares no separate label role); the deck's own middot is the separator.
    Either half absent still renders the other, matching ``_status_metric``;
    both absent leaves the role empty for Module 3's missing marker."""
    value = metric.get("value")
    label = metric.get("label")
    if value and label:
        return f"{value} · {label}"
    return value or label or ""


def _origin_of(section8, path):
    """What the packet's section 8 records as the source of ``path``.

    Section 8's `sources` list is `{field, origin}` pairs written by
    `packet_document`, one per filled path. Returns an empty string for a path it
    does not name, which every caller must treat as "cannot vouch for it".
    """
    for entry in (section8 or {}).get("provenance", {}).get("sources", []) or []:
        if entry.get("field") == path:
            return entry.get("origin") or ""
    return ""


def _after_metric(metric, origin):
    """One AFTER metric, captioned when the packet says where the figure came from.

    ``origin`` is what the packet's section 8 records as the source of
    `target_metrics`. The caption is only correct when that source is the
    opportunity-details tool, because that is the path
    `packet_fill._target_metrics` fills from `ebitda_impact` and nothing else. A
    `target_metrics` the second pass read out of the paper's PROSE can be any
    quantity the paper stated, so it is left exactly as it arrives rather than
    captioned as an EBITDA figure it may not be.

    A label the record already carries always wins: this only fills one that is
    absent.
    """
    if metric.get("label"):
        return _metric(metric)
    value = metric.get("value")
    if not value or origin != OPPORTUNITY_DETAILS_ORIGIN:
        return _metric(metric)
    # The unit rides on the value the tool stated. A percentage-point figure
    # moves the MARGIN; any other unit is an impact on EBITDA itself, and the
    # caption says only what it can stand behind.
    caption = (EBITDA_IMPACT_CAPTION_PP if str(value).strip().endswith("pp")
               else EBITDA_IMPACT_CAPTION)
    return _metric({"value": value, "label": caption})


def _baseline_metrics(baseline):
    """The TODAY figures, from the baseline the AFTER figure acts on.

    Slide 2 is a before-and-after, and it only reads as one when both boxes state
    the same quantity. The AFTER box carries the opportunity's EBITDA impact, so
    TODAY carries the EBITDA that impact moves: the margin first, because a
    percentage-point impact acts on it directly, then the dollar figure it sits
    on. Antonio, 2026-08-19, on the pair that shipped instead (connection counts
    against a margin delta): "One has a piece of words, and one has a percentage
    point difference, so we either have to have consistency over both boxes."

    Both are `ROSTER` fields, so a packet that clears the 0.70 completeness gate
    carries them; neither is computed, converted or re-rounded. Section 1 of the
    contract has always titled itself "Company Profile & Baseline -> Cover slide +
    Opportunity TODAY", so this is the slot they were declared for.

    An empty baseline yields nothing and the caller falls back to the paper's own
    metrics.
    """
    baseline = baseline or {}
    margin = baseline.get("adjusted_ebitda_pct")
    ebitda = baseline.get("adjusted_ebitda_usd")
    metrics = []
    if margin is not None:
        metrics.append({"value": f"{margin}%", "label": BASELINE_MARGIN_CAPTION})
    if ebitda is not None:
        metrics.append({"value": _fmt_usd_display(ebitda),
                        "label": BASELINE_EBITDA_CAPTION})
    return metrics


def _today_metrics_and_leftovers(baseline, today_metrics):
    """``(metrics, leftovers)`` for the TODAY panel.

    The baseline financials take the metric slots where the packet has them. The
    paper-derived metrics they displace are NOT dropped: a figure like a company's
    connection count is real, sourced and useful context, so it comes back as a
    bullet, where the ranker and the fitter weigh it against the other bullets.
    Antonio, 2026-08-19: "the figures of the number of connections is probably
    useful elsewhere in the slide deck."

    With no baseline in the packet the paper's metrics keep the slots and nothing
    is displaced, which is the behaviour that predates this.
    """
    financials = _baseline_metrics(baseline)
    if not financials:
        return list(today_metrics or ()), []
    return financials, list(today_metrics or ())


def _split_metric(text):
    """A folded ``VALUE · LABEL`` role back into its two halves, for measuring."""
    if not text:
        return ("", "")
    value, _, label = str(text).partition(" · ")
    return (value, label)


# Slide 2's two panels, in the order the slide reads them: the panel name the
# ranking pass answers with, the bullet role, the metric roles stacked above the
# bullets (which is what decides how much room is left for them), and the label
# the template's own slide-2 section uses. One table, because the rank step and
# the fit step have to agree about which list belongs to which panel.
SLIDE_TWO_PANELS = (
    ("today", "TODAY", "today_pain_bullets",
     ("today_metric_1", "today_metric_2")),
    ("after", "AFTER", "after_capability_bullets",
     ("after_metric_1", "after_metric_2")),
)

# Bullet role by the panel name the ranking pass answers with.
PRIORITY_ROLES = {panel: role for panel, _label, role, _metrics in SLIDE_TWO_PANELS}


def _apply_display_priority(placeholder_map, section8, appended=None):
    """Reorder slide 2's bullet lists by section 8's `display_priority`.

    The ranking pass records an order and changes no list, so the packet's own
    lists stay in the order their source gave them and a reviewer comparing the
    packet against the paper sees the paper's order. The reordering happens here,
    at the display layer, because that is where it matters: the panel shows only
    as many bullets as fit and `_fit_slide_two_bullets` trims from the tail.

    `appended` is how many items at the TAIL of a role's list this mapping layer
    added AFTER the packet's own list, keyed by role, and it is what makes the
    rank reach the deck at all on a packet with baseline financials. The TODAY
    panel's list is not the packet's list: `map_packet` gives the two metric
    slots to the baseline figures and appends the paper-derived metrics they
    displace to the bullets, so the displayed list is the ranked list plus that
    tail. Until 2026-09-02 this method compared the recorded order against the
    displayed length, found 5 against 7, and dropped the whole order without a
    word -- the panel then showed the first few bullets in packet order, the
    review surface said "no ranking pass ran on this panel", and every displaced
    metric (which is to say every quantified figure in the panel) sat at the tail
    where the fitter trims first. A live WTG run lost the $0.85M shrinkage
    figure, the $193K, the $170,000 net adjustments and the 41.07% margin exactly
    that way, and reported that no pass had run.

    So an order is now checked against the length it actually ranked, and applied
    to that prefix with the appended tail left where the mapping layer put it.
    That cannot lose or duplicate a bullet: the head is a permutation of the head
    and the tail is untouched. The tail keeps its place rather than being
    interleaved because it was never ranked -- nothing here knows where those
    items belong among the ranked ones, and inventing a position for them would
    be a judgment this layer has no basis for.

    A block that still does not line up -- a stale order, a malformed one, an
    order against a list that changed for some other reason -- leaves the list
    exactly as it arrived, which is the behaviour that predates the pass. What
    changed is that the refusal is now RECORDED and reaches the review surface,
    so "a pass ran and its order could not be applied" stops presenting itself as
    "no pass ran".

    Returns `{"roles", "notes", "not_applied"}`: what it applied keyed by role,
    the ranking pass's own repair notes, and one entry per panel whose order was
    refused, with the reason.
    """
    priority = (section8 or {}).get("provenance", {}).get("display_priority")
    appended = appended or {}
    applied, not_applied = {}, []
    for entry in (priority or {}).get("panels", []) or []:
        panel = entry.get("panel")
        role = PRIORITY_ROLES.get(panel)
        bullets = placeholder_map.get(role) if role else None
        if not bullets or not isinstance(bullets, (list, tuple)):
            continue
        order = entry.get("order") or []
        # How many of the displayed items the rank could have seen. Clamped, so a
        # miscounted tail cannot produce a negative length or reach past the list.
        extra = min(max(int(appended.get(role) or 0), 0), len(bullets))
        ranked = len(bullets) - extra
        if sorted(order) != list(range(ranked)):
            not_applied.append({
                "panel": panel,
                "role": role,
                "reason": (
                    f"the recorded order names {len(order)} bullet(s) and this "
                    f"panel's ranked list holds {ranked}"
                    + (f" ({len(bullets)} on the slide, {extra} appended by the "
                       "mapping layer after the pass ran)" if extra else "")
                    + ", so it is not a permutation of that list and the panel "
                      "keeps the order the packet stated."
                ),
            })
            continue
        # The identity for the appended tail, so the whole displayed list has an
        # entry and `_bullet_lineage` can map every shown bullet back to its own
        # place in the list the mapping layer built.
        full = list(order) + list(range(ranked, len(bullets)))
        placeholder_map[role] = [bullets[index] for index in full]
        applied[role] = {
            "order": full,
            # Recomputed here rather than trusted from the block, because what a
            # reviewer needs to know is whether the list on the DECK differs from
            # the one the packet declared, and only this step can say that.
            "reordered": full != list(range(len(bullets))),
        }
    return {"roles": applied, "notes": list((priority or {}).get("notes") or ()),
            "not_applied": not_applied}


def _lineage_rows(pairs, texts):
    """The fitter's own strings, each tagged with its place in the packet's list.

    The fitter is the authority on the text: a row's ``text`` is the string that
    will render, byte for byte, never the one this function looked up. The
    position is dropped rather than guessed when the two sequences disagree —
    they cannot today, since `_bullet_lineage` filters with the fitter's own
    predicate, but a future drift must show up as a missing position rather than
    as a bullet labelled with another bullet's place in the source.
    """
    aligned = len(pairs) == len(texts) and all(
        pair[1] == text for pair, text in zip(pairs, texts)
    )
    return [{"text": text,
             "source_position": pairs[index][0] if aligned else None}
            for index, text in enumerate(texts)]


def _bullet_lineage(bullets, order):
    """Each usable bullet paired with where it sat in the list the packet stated.

    ``bullets`` is the list as the deck will read it, which is post-rank, and
    ``order`` is the permutation `_apply_display_priority` applied (empty when it
    applied none). Returns ``[(source_position, text), ...]`` with the blanks
    dropped by `panel_fit.is_usable`, so the positions line up item for item with
    `Fit.kept` followed by `Fit.dropped`.

    ``source_position`` is 1-based and counts into the packet's own list, blanks
    included, because that is the list a reviewer holding the packet is looking
    at. With no order applied it is simply the bullet's own place in that list.
    """
    order = list(order or ())
    lineage = []
    for position, text in enumerate(bullets):
        if not panel_fit.is_usable(text):
            continue
        source = order[position] if position < len(order) else position
        lineage.append((source + 1, text))
    return lineage


def _fit_slide_two_bullets(placeholder_map, phases, order_report=None,
                           overrides=None):
    """Trim slide 2's two bullet lists to what their panels actually hold.

    Why this is here rather than in CSS. The panels have `overflow:visible` and
    the spill stops short of the slide edge, so an over-long list is neither
    clipped nor off-slide: it is painted on top of the build band, which is how a
    deck shipped on 2026-08-19 with both panels' bullets lying unreadable across
    it. No stylesheet can drop an item, and no fixed cap is right either, because
    the room left for bullets moves with the headline's line count, the number of
    build phases, and how tall the metrics above them are.

    What it does NOT do. It never rewrites, shortens or summarises a bullet, so
    nothing unsourced can enter here; it only decides how many of them fit. The
    order it trims from is the order the list arrives in, which today is the
    paper's own. Choosing which bullets matter most is a judgment and is a
    separate pass.

    Dropping is reported, never silent: the returned note rides on the map as
    `_panel_fit` so a reviewer can see that a panel showed four of nine. The
    report names every panel it fitted, including the ones that lost nothing,
    because a reviewer has to be able to tell "everything fitted" from "nothing
    measured this panel".

    Fitting happens at the house type size only. `panel_fit` can also step the
    type down to hold more, but delivering that to the renderer needs a per-panel
    role the template does not declare, so it is deliberately not wired.
    """
    headline = placeholder_map.get("opportunity_headline") or ""
    summary = placeholder_map.get("opportunity_summary") or ""
    band = [(phase.get("label") or "", phase.get("summary") or "")
            for phase in (phases or ())]
    house = (panel_fit.BULLET_FONT,)
    # A panel nobody has switched anything on is fitted at the house size only,
    # exactly as before. A panel a reviewer HAS taken a decision about spends the
    # type ladder, because the ladder exists to honour a request a human made and
    # not to let the fitter quietly shrink a deck nobody asked it to shrink.
    overrides = overrides or {}

    report = {}
    applied = (order_report or {}).get("roles", {})
    refused = {entry.get("role"): entry.get("reason")
               for entry in (order_report or {}).get("not_applied", ()) or ()}
    for panel, label, role, metric_roles in SLIDE_TWO_PANELS:
        bullets = placeholder_map.get(role)
        # A list, or nothing. A packet whose yaml gave this role a bare string
        # would otherwise be iterated character by character, and a crash in the
        # mapping layer sinks a render that would otherwise have degraded.
        if not bullets or not isinstance(bullets, (list, tuple)):
            continue
        rank = applied.get(role) or {}
        lineage = _bullet_lineage(bullets, rank.get("order"))
        metrics = [_split_metric(placeholder_map.get(name))
                   for name in metric_roles]
        room = panel_fit.bullet_room(headline=headline, summary=summary,
                                     phases=band,
                                     metrics=[m for m in metrics if m[0] or m[1]])
        panel_overrides = overrides.get(role) or {}
        forced_off = []
        if panel_overrides:
            keyed = {bullet_toggles.bullet_key(text): text for text in bullets}
            forced_on = [keyed[key] for key, state in panel_overrides.items()
                         if state == bullet_toggles.ON and key in keyed]
            forced_off = [keyed[key] for key, state in panel_overrides.items()
                          if state == bullet_toggles.OFF and key in keyed]
            fit = panel_fit.fit_bullets_with_overrides(
                bullets, room_px=room, forced_on=forced_on,
                forced_off=forced_off)
        else:
            fit = panel_fit.fit_bullets(bullets, room_px=room, font_steps=house)
        placeholder_map[role] = fit.kept
        # Recorded for every panel that was fitted, not only for the ones that
        # lost a bullet. A reviewer asking "is this all of it?" needs the answer
        # either way, and "all 2 shown" is an answer; an entry only on the drop
        # path leaves silence standing for both outcomes.
        report[role] = {
            "panel": panel,
            "label": label,
            "role": role,
            "shown": len(fit.kept),
            "of": len(fit.kept) + len(fit.dropped),
            # Text plus the place it held in the packet's own list, so a reviewer
            # can check a bullet against the source without counting. `lineage`
            # is the same filtered sequence the fitter split, so the slice is the
            # split: kept first, dropped after it.
            "kept": _lineage_rows(lineage[:len(fit.kept)], fit.kept),
            "dropped": _lineage_rows(lineage[len(fit.kept):], fit.dropped),
            # Bullets a reviewer switched OFF. Recorded separately from the ones
            # the panel could not hold, and recorded at all because a bullet that
            # vanished from this card could never be switched back on: the record
            # is what the switches are drawn from, so a bullet missing here is a
            # decision a reviewer cannot undo.
            "switched_off": [{"text": text, "source_position": None}
                             for text in forced_off],
            "ranked": bool(rank),
            # A pass that ran and whose order this layer refused, with the reason.
            # Empty on both of the other two outcomes, so a surface can tell three
            # states apart where it could previously only see two: no pass ran, a
            # pass ran and its order stands, a pass ran and its order was dropped.
            "rank_refused": refused.get(role) or "",
            "reordered": bool(rank.get("reordered")),
            "room_px": round(fit.room_px, 1),
            # The size this panel's bullets are set at. Recorded for every panel
            # so a reader can tell "12.5, the house size" from "11.0, because a
            # reviewer asked for a bullet that did not fit at 12.5"; the delivery
            # of a below-house size to the deck is `bullet_type`.
            "font_px": fit.font_px,
            "overflowed": fit.overflowed,
            "note": fit.note,
        }
    return report


def bullet_selection(placeholder_map):
    """Slide 2's bullet selection, as one record a review surface can render.

    Merges the two side channels the mapping half leaves behind — `_panel_fit`
    (how many of each panel's bullets the panel holds, and which ones) and
    `_bullet_order` (the order the ranking pass chose) — into the panels in the
    order slide 2 reads them.

    This exists because both halves of that story were being recorded and neither
    was reaching a human. A panel showing four bullets of nine is a selection made
    by a model and a measurement made by a fitter, and a reviewer signing off on
    the deck has to be able to see both. Composed here, beside the code that
    records it, rather than in the studio's template, so the packet-to-deck facts
    stay in one place and the UI only lays them out.

    Returns ``None`` when no panel was fitted — a status deck, or a proposal whose
    packet carried neither list — so a caller has nothing to show rather than an
    empty card.
    """
    fit = (placeholder_map or {}).get("_panel_fit") or {}
    order = (placeholder_map or {}).get("_bullet_order") or {}
    panels = [fit[role] for _panel, _label, role, _metrics in SLIDE_TWO_PANELS
              if role in fit]
    if not panels:
        return None
    return {
        "panels": panels,
        # The ranking pass's repairs (an out-of-range index, a duplicate, an
        # omission). Present means the answer was salvaged rather than clean,
        # which is the one thing that makes a recorded order less trustworthy.
        "notes": list(order.get("notes") or ()),
        # An order the pass recorded and the mapping layer could not apply. Kept
        # apart from `notes` on purpose: a repair is the PASS salvaging its own
        # answer, this is the answer being thrown away afterwards, and a reviewer
        # who cannot tell those apart cannot tell which half to go and look at.
        "not_applied": list(order.get("not_applied") or ()),
        "trimmed": any(panel["dropped"] for panel in panels),
        "ranked": any(panel["ranked"] for panel in panels),
    }


def bullet_selection_per_opportunity(placeholder_map):
    """One bullet-selection record per opportunity, in the order the deck reads.

    ``bullet_selection`` describes the FIRST opportunity's slide 2, because the
    flat map carries that one and the card was written when a deck had exactly
    one. A deck with several carries a slide 2 each, with their own panels and
    their own trims, and a reviewer switching bullets on and off needs to be
    looking at the one they mean (Antonio, 2026-09-20: one set of switches per
    opportunity). Each entry carries its own index and title so the studio can
    say which slide 2 a switch belongs to.

    Returns ``[]`` when no panel was fitted, on the same terms as the singular
    version: nothing to show rather than an empty card.
    """
    fits = (placeholder_map or {}).get("_panel_fit_per_opportunity") or []
    orders = (placeholder_map or {}).get("_bullet_order_per_opportunity") or []
    items = (placeholder_map or {}).get("opportunities") or []
    out = []
    for index, fit in enumerate(fits):
        panels = [fit[role] for _panel, _label, role, _metrics in SLIDE_TWO_PANELS
                  if role in fit]
        if not panels:
            continue
        order = orders[index] if index < len(orders) else {}
        item = items[index] if index < len(items) else {}
        out.append({
            "index": index,
            # The slide's own label if it has one, so the block is named the way
            # the deck names it rather than "opportunity 2".
            "label": item.get("section_label") or item.get("opportunity_headline") or "",
            "panels": panels,
            "notes": list((order or {}).get("notes") or ()),
            "not_applied": list((order or {}).get("not_applied") or ()),
            "trimmed": any(panel["dropped"] for panel in panels),
            "ranked": any(panel["ranked"] for panel in panels),
        })
    return out


def _scenario_ebitda_gain(scenario):
    """Compose the scenario's EBITDA-gain cell from whichever of the two
    source figures (percentage-point gain, dollar uplift) the paper stated.
    Either alone still renders; both absent leaves the cell empty for Module
    3's missing marker, instead of a bare ``None`` standing in for either.

    Either figure may be a range the paper stated (E11 Stage 2e), and the cell
    needed no change to carry one: ``pp`` is already named once after the figure,
    so a two-endpoint figure names it once after the second endpoint, and
    ``_fmt_usd`` handles the dollar half. Both halves come through as the paper
    stated them, with no endpoint chosen for either."""
    pp = scenario.get("margin_gain_pp")
    usd = scenario.get("direct_uplift_usd_yr")
    parts = []
    if pp is not None:
        parts.append(f"+{pp}pp")
    if usd is not None:
        parts.append(f"{_fmt_usd(usd)}/yr")
    return " · ".join(parts)


def _workstream_weeks(workstream):
    """Per-workstream week span from the packet's ``start_week`` / ``end_week``.

    Returns ``"start–end"`` (en-dash, matching the ``timeline_columns`` week-range
    form). Returns ``None`` when either endpoint is absent: on a timeline template
    those fields are load-bearing (they set each Gantt bar's geometry), so a
    missing endpoint must surface as Module 3's missing marker rather than have the
    code silently stamp the whole phase span on every row — which would leave the
    render's bar geometry unconstrained by our data. Templates without a timeline
    slide never carry a workstream, so nothing is required of them (build plan,
    Module 2 timeline mapping)."""
    start = workstream.get("start_week")
    end = workstream.get("end_week")
    if start is None or end is None:
        return None
    return f"{start}–{end}"


def _unit_range(unit, span):
    """A span prefixed with the plural unit the packet stated ("MONTHS 0–3").

    The axis names its unit once, the way the template's own example does
    (`Wk 1–2 / 3–4 / …`), because the deck has no separate role for a unit and
    inventing one would move the 18-role denominator `role_coverage` is read
    against. A packet that stated no unit renders the bare range rather than a
    guessed one.
    """
    return f"{unit.upper()} {span}" if unit else str(span)


def _unit_point(unit, position):
    """A point on the axis in the packet's own unit ("MONTH 3", "WEEK 6").

    Singular, because a boundary is a point rather than a duration. The packet's
    unit vocabulary comes from `chart_timeline_parser`, which reports the unit a
    chart states and never invents one, so the only transform here is dropping a
    trailing plural.
    """
    if not unit:
        return str(position)
    word = unit[:-1] if unit.endswith("s") else unit
    return f"{word.upper()} {position}"


# One axis entry stated as a bare numeric range, exactly as `packet_fill._span`
# writes one: two numbers on the deck's own en dash (a hyphen and an em dash are
# accepted because a stated `week_buckets` axis is hand-written). Matched whole,
# so any other label shape passes through untouched rather than being reshaped
# into a range it never was.
_AXIS_SPAN_RE = re.compile(r"\A\s*(\d+(?:\.\d+)?)\s*[–—-]\s*(\d+(?:\.\d+)?)\s*\Z")


def _axis_span(label):
    """``(start, end)`` for a bare numeric range label, else ``None``."""
    match = _AXIS_SPAN_RE.match(str(label))
    if not match:
        return None
    start, end = float(match.group(1)), float(match.group(2))
    return (start, end) if end >= start else None


def _axis_number(value):
    """An integral float as an int, so an axis boundary reads as one."""
    return int(value) if float(value).is_integer() else value


def _axis_columns(timeline):
    """The timeline's column axis: the packet's own buckets, else its columns.

    ``week_buckets`` is what the frozen fixture packets state directly, and a
    stated value always wins. Where the packet instead carries ``columns`` — the
    chart's own phase spans, each with the unit that chart stated — the axis is
    composed from those, with the first entry naming the unit and the rest bare
    ranges under it. An entry with no range is skipped rather than rendered as an
    empty column.

    THE AXIS IS MONOTONIC, which the phase spans it is composed from are not.
    A plan whose phases run concurrently states overlapping spans — the WTG
    paper of 2026-08-26 draws Phase 2 over months 2–4 and Phase 3 over months
    3–6 — and this used to hand both to the renderer as axis entries. Their
    `grid-column` placements then overlapped on month 3, so the header row could
    not fit on one grid row: the third and fourth cells were auto-placed onto a
    second row, wrapped under the first two and painted over them (measured on
    `decks/WTG/claude code/output-2.html`, slide 4: cell 3 starting at x=628
    while cell 2 ended at x=690, on a `top` 13px below the others). That is the
    "minor alignment issues on the timeline slide" Casey raised on 2026-08-20.

    An axis is a ruler, not a phase list. So each entry's START is clamped to the
    previous entry's END, and an entry that reaches no further than the previous
    one adds no ground and is dropped. The ENDS are never moved, so every
    boundary on the ruler is one the chart actually stated, and the BARS still
    carry each phase's true, possibly overlapping, span — a concurrent phase is
    still drawn concurrently, it just no longer bends the ruler underneath it.
    A label that is not a bare numeric range is carried through as stated and
    ends the clamping run rather than being reshaped.
    """
    buckets = timeline.get("week_buckets") or []
    if buckets:
        return buckets
    columns, cursor = [], None
    for column in timeline.get("columns") or []:
        label = column.get("label")
        if label is None or label == "":
            continue
        span = _axis_span(label)
        if span is None:
            columns.append(
                _unit_range(column.get("unit"), label) if not columns
                else str(label)
            )
            cursor = None
            continue
        start, end = span
        if cursor is not None:
            if end <= cursor:
                continue
            if start < cursor:
                start = cursor
                label = f"{_axis_number(start)}–{_axis_number(end)}"
        cursor = end
        columns.append(
            _unit_range(column.get("unit"), label) if not columns else str(label)
        )
    return columns


def _phase_span(phase):
    """A phase's own span from the packet's unit-neutral ``start`` / ``end``.

    ``None`` when either endpoint is absent, for the same reason
    ``_workstream_weeks`` returns ``None``: the span is the bar's geometry, and
    half a span back-filled from somewhere else is a bar our data never placed.
    """
    start, end = phase.get("start"), phase.get("end")
    if start is None or end is None:
        return None
    return f"{start}–{end}"


def _phase_rows(phase):
    """One timeline row per workstream, or the phase itself as its own row.

    This used to be a bare nested loop over ``phase["workstreams"]``, which
    yielded nothing at all for a phase that carries none — and no research paper
    in the corpus decomposes a phase into workstreams, so a paper-derived packet
    produced zero rows however well its chart parsed. That is one of the four
    independent reasons slide 4 rendered blank (verified 2026-08-18).

    A phase with workstreams is unchanged: one row each, each carrying its own
    span. A phase with none is its own row, spanning its own span. The workstream
    keys are left OUT of that record rather than nulled, so the assembler emits
    no line for them: the paper never claimed a workstream existed. Their
    absence is still recorded — ``timeline.phases[].workstreams[].name`` and its
    three siblings sit in the packet's own §8 gaps, which is where a reviewer
    reads what no source supplied. ``weeks`` stays present either way, since it
    is load-bearing on this slide and its absence has to show on the deck.
    """
    workstreams = phase.get("workstreams") or []
    if not workstreams:
        return [{"phase": phase.get("label"), "weeks": _phase_span(phase)}]
    return [
        {
            # Name and detail are separate roles: the name is the row label
            # (left of the bars) and the detail rides inside the bar. They
            # used to be concatenated into one `workstream` label; splitting
            # them lets the template place each where the house style wants.
            "phase": phase.get("label"),
            "workstream": ws.get("name"),
            "workstream_detail": ws.get("detail"),
            # Per-workstream span from the packet, not the phase span. None
            # when the packet omits it, so Module 3 flags the row (the span is
            # load-bearing on a timeline template).
            "weeks": _workstream_weeks(ws),
        }
        for ws in workstreams
    ]


MILESTONE_ID = "M{ordinal}"
MILESTONE_LABEL = "MILESTONE {ordinal}"


def _milestone_role(milestone, ordinal):
    """One milestone record for the deck, in the packet's own unit.

    A packet that states its own ``id`` / ``week`` / ``label`` is carried
    verbatim; the frozen fixture packets do. A paper-derived packet states a
    ``position`` and its unit instead, off a phase boundary the chart drew, and
    the id and the ordinal label around it are deck framing in the same class as
    ``after_horizon``'s "AFTER — TARGET IN ~N WEEKS": they name the milestone's
    place in the sequence and assert nothing about this engagement. Antonio drew
    that line on 2026-08-18 — ``Milestone 1`` is framing, ``M1 · Pilot
    Validated`` names an outcome the paper never stated and is not composed here.
    Every number and its unit come from the packet.
    """
    if milestone.get("position") is None:
        return {key: milestone.get(key) for key in ("id", "week", "label")}
    return {
        "id": milestone.get("id") or MILESTONE_ID.format(ordinal=ordinal),
        "week": milestone.get("week")
        or _unit_point(milestone.get("unit"), milestone["position"]),
        "label": milestone.get("label")
        or MILESTONE_LABEL.format(ordinal=ordinal),
    }


_MONTHS = (
    "JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE",
    "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER",
)


def _fmt_deck_date(proposal_date):
    """Format an ISO ``proposal_date`` into the template's deck-date form
    ("JULY 6 2026", matching the {deck_date} role example). A derived view of the
    structured field, deterministic (fixed English month table). If the input is
    not a plain ISO date, it is passed through unchanged rather than guessed at.
    """
    if not proposal_date:
        return ""
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", proposal_date.strip())
    if not match:
        return proposal_date
    year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
    if not 1 <= month <= 12:
        return proposal_date
    return f"{_MONTHS[month - 1]} {day} {year}"


CONFIDENTIALITY_DEFAULT = "QOFAI CONFIDENTIAL"

# Deck copy for slide 2's before-and-after pair, composed here rather than stored
# in the packet — the same place and the same kind of thing as `after_horizon`'s
# "AFTER — TARGET IN ~36 MONTHS" and the build band's "THE 36-MONTH BUILD".
#
# Why the AFTER figure needs one. `packet_fill._target_metrics` writes
# `ebitda_impact` as a value with no label, because `target_metrics[].label` is
# absent by Antonio's ruling of 2026-08-15: the packet does not get to name a
# figure the tool returns unnamed. That ruling stands and the packet is unchanged.
# What it left on the deck was a 24px `0.6-1.3pp` with no caption at all, beside a
# TODAY panel whose figures did have captions. Antonio, 2026-08-19: "0.6-1.3pp
# means nothing if we dont know what the metric is."
#
# A caption is not a claim about the paper, so it needs no span: it names the
# FIELD the value came from. It is therefore applied only where that field is
# known to be the source — see `_after_metric`, which reads the packet's own
# section 8 origin and captions nothing it cannot vouch for.
EBITDA_IMPACT_CAPTION_PP = "EBITDA margin impact"
EBITDA_IMPACT_CAPTION = "EBITDA impact"
OPPORTUNITY_DETAILS_ORIGIN = "get_opportunity_details"

# The TODAY captions, naming the baseline fields the AFTER figure acts on.
BASELINE_MARGIN_CAPTION = "Adjusted EBITDA margin"
BASELINE_EBITDA_CAPTION = "Adjusted EBITDA"
DECK_TYPE_LABEL_DEFAULT = "PROJECT PLANNING"

# Which packet field path(s) feed each rendered role. This is the reverse of the
# build plan's per-slide mapping tables, declared as data so Module 2 can answer
# "which template role carries this packet field". It backs the _gap_roles side
# channel (translating packet-path gap flags into the role space Module 3
# renders in) and is the same path↔role index the deferred two-client leakage
# test (PRD criterion 6) checks _provenance against. Copy roles and templated
# defaults have no structured KG source path and cannot be §8-gap-flagged, so
# they are intentionally absent.
ROLE_SOURCE_PATHS = {
    "client_full": ["company.name"],
    "client_short": ["company.client_short"],
    "pe_firm": ["company.pe_firm"],
    "today_metric_1": ["today_metrics"],
    "today_metric_2": ["today_metrics"],
    "today_pain_bullets": ["today_pain_points"],
    "after_horizon": ["build_summary.duration_weeks", "build_summary.duration"],
    "after_metric_1": ["target_metrics"],
    "after_metric_2": ["target_metrics"],
    "after_capability_bullets": ["target_capabilities"],
    "build_summary": ["build_summary"],
    # `plan_summary` is deliberately absent: it became a copy role on 2026-09-03
    # and copy roles carry no structured source path, the same as
    # `platform_summary` and `terms_summary`.
    "components": ["platform_layers"],
    "timeline_columns": ["timeline.week_buckets", "timeline.columns"],
    "timeline_rows": ["timeline.phases"],
    "milestones": ["timeline.milestones"],
    # Slide 5, the adaptive deal sheet (2026-09-23).
    "investment_rows": ["commercial.investment"],
    "return_rows": ["commercial.scenarios"],
    "terms_rows": ["commercial.qofai_investment_usd", "commercial.client_upfront_usd",
                   "commercial.comp_schedule", "commercial.client_retention_note",
                   "commercial.no_improvement_clause", "commercial.how_payment_works"],
    "value_mapping": ["commercial.scenarios"],
    # The footnote is composed from the EV assumptions (falling back to the prose
    # line); a §8 gap on either source flags terms_footnote as unconfirmed.
    "terms_footnote": ["commercial.ev_footnote", "commercial.ev_assumptions"],
    "deck_date": ["request.proposal_date"],
    "project_name": ["request.project"],
    "footer_right": ["request.project"],
}


def client_short(request, company):
    """The deck's short brand form, and the ONE place its precedence is decided.

    THE REVIEWER'S VALUE FIRST, then the platform's, then the full name. The
    middle term is there for the day a source supplies one and is not reached
    today: `company.client_short` is classified UNSOURCEABLE in
    `packet_document`'s slot table -- "no tool in the grant returns this field
    and no label in the graph carries it" -- and `packet_fill` never fills it on
    any path. So the fallback to the full name fires on EVERY live run, always,
    by construction.

    That is the defect this closes, found on the first live Fabrikam deck
    (2026-09-15) and initially read as a field that came back empty. It did not
    come back empty; there has never been anything to come back. The fixture
    packets carry "FBK" and "Ridgeline" because a human wrote them into a frozen
    fixture, which is why a fixture run looked right and every live deck's seven
    footers carried the company's full registry name.

    So the value comes from outside the platform or not at all, and
    `deck_title` is the precedent it follows exactly: a cosmetic string no data
    source supplies, entered in the studio, winning over the derived value and
    degrading to it when blank.

    ONE FUNCTION BECAUSE THERE ARE TWO MAPPERS. The proposal map and the status
    map each decided this independently, and a precedence stated twice is a
    precedence with two chances to be edited apart -- which is how the baseline
    label and the baseline figures came apart for a day on 2026-09-07. Both call
    this, and a test asserts both calls off the parsed source.

    IT DOES NOT ENTER THE PACKET. A reviewer's own string is not something the
    data source said, so `company.client_short` stays absent in section 1 and
    stays in section 8's gaps as unsourceable, which is the honest record. What
    changes is what the DECK renders.
    """
    request, company = request or {}, company or {}
    return (str(request.get("client_short") or "").strip()
            or company.get("client_short")
            or company.get("name", ""))


# The template roles a REVIEWER fills in the studio, and the request key each
# arrives under. One entry today, and the table is the point: a second
# studio-supplied field is one line here rather than an edit to two mappers.
REVIEWER_SUPPLIED_ROLES = {"client_short": "client_short"}


def supplied_roles(request):
    """The roles the reviewer filled, which are never unconfirmed.

    ONE STATEMENT, BECAUSE TWO MAPPERS SUPPRESS. `ROLE_SOURCE_PATHS` maps
    `client_short` to `company.client_short`, which is absent on every run and
    therefore gap-flagged on every run, so the role has always rendered
    "(unconfirmed, see gaps)" -- it is there in the first live Fabrikam prompt.
    That is right for a value derived from a source that did not supply it and
    wrong for one a human typed: a reviewer is not waiting on confirmation of
    their own input, they ARE the confirmation.

    The first version of this fix stated the suppression twice, once
    generalised on the proposal path and once as an inline `role ==
    "client_short"` filter on the status path, which is the hazard
    `client_short`'s own docstring names reintroduced one layer down. Found by
    the review window by MUTATION: replacing the status guard with `if True`
    restored the pre-fix bug on every status deck and not one test failed.

    Table-driven for the second-order version of the same problem. The
    generalised site would have absorbed a second studio field as one more
    entry; the hardcoded one would have kept working on proposal decks and
    silently not on status decks.
    """
    request = request or {}
    return {role for role, key in REVIEWER_SUPPLIED_ROLES.items()
            if str(request.get(key) or "").strip()}


def _gap_flagged_roles(gap_paths, supplied=()):
    """Translate packet-path gap flags (§8 gaps) into the template roles they
    affect, so Module 3 can render an "unconfirmed" marker on the right role
    (PRD criterion 10). A role matches a gap when its source path and the gap
    path are equal or one nests under the other (a gap on `commercial` flags
    every commercial-sourced role; a gap on a leaf flags the role carrying it).
    A gap path that maps to no rendered role (e.g. `baseline.baseline_locked_date`)
    yields nothing, which is correct.

    `supplied` are roles the REVIEWER filled, which are never unconfirmed
    however their packet path scored (2026-09-15). The marker means a
    packet-sourced value the packet itself flagged as needing confirmation
    before anything ships. A value a human typed into the studio is not waiting
    on confirmation from anybody: they are the confirmation. Without this,
    `client_short` carried "(unconfirmed, see gaps)" on every run, because its
    path is unsourceable and therefore gap-flagged always, and a reviewer who
    had just typed the short form would have been told their own answer was
    unconfirmed.
    """
    supplied = set(supplied or ())
    flagged = set()
    for gap in gap_paths:
        for role, paths in ROLE_SOURCE_PATHS.items():
            for path in paths:
                if (
                    gap == path
                    or gap.startswith(path + ".")
                    or path.startswith(gap + ".")
                ):
                    flagged.add(role)
                    break
    return sorted(flagged - supplied)


def map_packet(packet_md, request, *, bullet_overrides=None):
    """Map a clean ok-packet into the flat placeholder map (PRD §4 step 6).

    ``request`` supplies the fields the frozen packet does not carry itself: the
    resolved project reference (the fixture packet predates the explicit
    ``project`` field) and, as a fallback, the proposal date. Returns the flat
    map keyed by template role name, plus four side channels for Module 3:

    - ``_provenance`` — the §8 from_kg / derived / templated_defaults sets.
    - ``_gaps`` — the §8 gap-flagged packet field paths (verbatim).
    - ``_gap_roles`` — those gaps translated into the template roles they affect,
      so Module 3 can flag the right role (PRD criterion 10).
    - ``_skipped`` — the slide numbers whose section the request did not ask for,
      so Module 3 omits them cleanly rather than flagging empty roles (PRD
      criterion 7).
    - ``_panel_fit`` — what slide 2's panels could not hold. A bullet list longer
      than its panel is trimmed to what fits rather than painted over the band
      below it, and this records what was dropped so the trim is visible to a
      reviewer instead of silent (2026-08-19).
    - ``_bullet_order`` — the order the ranking pass chose for slide 2's bullet
      lists, as applied, with that pass's own repair notes. Recorded for the same
      reason: a panel showing four of nine was a selection, and the reviewer has
      to be able to see that a model made it (2026-08-19).
    """
    sections = _split_sections(packet_md)
    request = request or {}

    y0 = _section_yaml(sections.get(0, ""))
    y1 = _section_yaml(sections.get(1, ""))
    y2 = _section_yaml(sections.get(2, ""))
    y4 = _section_yaml(sections.get(4, ""))
    y5 = _section_yaml(sections.get(5, ""))
    y6 = _section_yaml(sections.get(6, ""))
    y7 = _section_yaml(sections.get(7, ""))
    y8 = _section_yaml(sections.get(8, ""))

    company = y1.get("company", {})
    # SECTION 2 REPEATS (item 15, 2026-09-13). Its yaml is a list, one entry per
    # opportunity, and `_opportunity_roles` below turns one entry into the roles
    # of one slide 2. This layer produces BOTH: the flat map the deck has always
    # had, carrying the first entry's roles, and the list the assembler repeats
    # over. Deliberately both, because they are read by different things --
    # `check_coverage` and every non-repeating consumer read the flat map, and
    # only `prompt_assembler` reads the list -- and because they are built from
    # one function, so they cannot say different things about one opportunity.
    entries = y2.get("opportunities") or [{}]
    y2 = entries[0]
    build = y2.get("build_summary", {})
    phases = build.get("phases", [])
    duration_weeks = build.get("duration_weeks")
    # The horizon in whatever unit the paper stated it (E11 Stage 2c). The
    # contract's own field is week-denominated and stays absent on a plan stated
    # in months, which left slide 2's AFTER band with no horizon at all on most
    # of the corpus. `duration_weeks` still wins where a packet carries it, so
    # nothing that rendered before moves; `_horizon_parts` falls through to the
    # unit-neutral pair and NOTHING is converted between the two.
    horizon, horizon_unit = _horizon_parts(build)
    timeline = y5.get("timeline", {})
    commercial = y6.get("commercial", {})

    baseline = y1.get("baseline", {})
    # Every opportunity's slide 2, in order. The first one's roles go into the
    # flat map below; the whole list goes onto the map as `opportunities`, which
    # is what the template's `Repeat:` directive names.
    slides = [_opportunity_roles(entry, baseline, y8, position, len(entries))
              for position, entry in enumerate(entries, start=1)]
    slide_two, displaced_count = slides[0]

    # Recurring fields the request scopes (packet has no project short-form).
    # `deck_title` wins where the request carries one, so the page marks read the
    # same string the cover does: on a project-less company the cover's title came
    # from the studio or from the opportunity, and the project reference the
    # reviewer typed is empty or something else entirely (98e5d15, extended
    # 2026-08-20). With no `deck_title` nothing moves.
    project_name = request.get("deck_title") or request.get("project") or ""
    deck_kicker = _cover_bullet(sections.get(1, ""), "Eyebrow")

    placeholder_map = {
        # Slide 1 — Title / Cover
        "deck_kicker": deck_kicker,
        "prepared_for": _cover_bullet(sections.get(1, ""), "Prepared for"),
        "project_title": _cover_bullet(sections.get(1, ""), "Title"),
        "subtitle": _cover_bullet(sections.get(1, ""), "Subhead"),
        "footer_left": CONFIDENTIALITY_DEFAULT,
        # Residual gap: no packet field; derive the engagement label from the
        # project name, else leave empty for Module 3's missing marker.
        "footer_right": project_name.upper() if project_name else "",

        # Slide 2 — The Opportunity, which REPEATS once per opportunity since
        # 2026-09-13. The flat map carries the first one's roles, built by the
        # same `_opportunity_roles` that builds every entry in the list below,
        # so the two cannot disagree about one opportunity.
        **slide_two,

        # Slide 3 — The Platform. The flat map carries the lead opportunity's,
        # built by the same function that builds every opportunity's own copy
        # of the slide below, so the two cannot disagree about one opportunity.
        **_platform_roles(_headline(sections.get(4, "")),
                          _subhead_line(sections.get(4, ""), "Subhead"), y4),

        # Slide 4 — Phased Rollout
        **_plan_roles(
            _headline(sections.get(5, "")),
        # §5's own `Subhead:` line, read the same way §4's and §6's are. It
        # used to be assembled here by joining `build_summary.phases[].summary`
        # on a space, which is the defect Casey raised on 2026-08-20 ("the
        # timeline description text was too verbose"): the join dropped every
        # phase label and every separator, so the slide carried a run-on of the
        # phase CAVEAT notes, attributed to nothing, restating what slide 2's
        # build strip and the Gantt directly underneath had both already shown.
        # The phase notes still render, once, on the build strip via
        # `_phase_line`. `packet_fill.PLAN_SUMMARY` is the deck standard and
        # `paper_writing`'s `copy.plan_summary` slot writes the
        # engagement-specific line where a run has a writing pass.
            _subhead_line(sections.get(5, ""), "Subhead"),
            timeline),

        # Slide 5 — Commercial Terms, an adaptive deal sheet
        # (`build-plan-commercial-slide.md`). Every block is composed by rule
        # from what the packet states, and an empty block is an empty list, so
        # an optional role emits no prompt line and no marker.
        "terms_headline": _headline(sections.get(6, "")),
        "terms_summary": _subhead_line(sections.get(6, ""), "Subhead"),
        "investment_rows": _investment_rows(commercial),
        "return_rows": _return_rows(commercial),
        "terms_rows": _terms_rows(commercial),
        "value_mapping": _value_mapping(commercial),
        "terms_footnote": _terms_footnote(commercial),

        # Slide 6 — Next Steps
        **_next_steps_roles(_headline(sections.get(7, "")),
                            _headline_italic(sections.get(7, "")), y7),

        # Recurring fields (fill once, propagate)
        "client_full": company.get("name", ""),
        # The short brand form for the footers, whose precedence lives in one
        # place because two mappers read it. Reviewer's value, then the
        # platform's, then the full client name; it degrades rather than
        # emitting a MISSING marker, because a footer short-form is cosmetic and
        # defaultable and markers are for load-bearing, un-defaultable fields.
        # Only a genuinely nameless company leaves this empty for the marker.
        "client_short": client_short(request, company),
        "pe_firm": company.get("pe_firm", ""),
        "project_name": project_name,
        "deck_date": _fmt_deck_date(
            y0.get("request", {}).get("proposal_date")
            or request.get("proposal_date")
            or ""
        ),
        "confidentiality": CONFIDENTIALITY_DEFAULT,
        # Footer uses the deck-type text, not the literal "PROPOSAL": derive it
        # from the cover eyebrow's leading segment ("PROJECT PLANNING · <date>").
        "deck_type_label": (
            deck_kicker.split(" · ")[0] if deck_kicker else DECK_TYPE_LABEL_DEFAULT
        ),
    }

    # Slide 2, in order: rank, then fit. The ranking pass decides which bullets
    # matter most and the fitter decides how many fit, and they have to run in
    # that order because the fitter trims from the tail.
    # Per opportunity, because each slide 2 has its own two panels and its own
    # room. The first entry is the flat map itself, so ranking and fitting it in
    # place is what keeps the flat map and the list agreeing after the trim.
    items, reports, fits = [], [], []
    for index, (roles, appended) in enumerate(slides):
        item = dict(roles)
        order_report = _apply_display_priority(
            item, y8,
            # The TODAY panel is the only list this layer lengthens. `after` is
            # the packet's `target_capabilities` verbatim, so it appends nothing
            # and its order matches on its own length.
            appended={"today_pain_bullets": appended},
        )
        # Applied to the finished roles because the room depends on the
        # headline, the band and the metrics that are only all known here. The
        # rank report goes in so the fit report can say where a surviving bullet
        # sat in the packet's list.
        fits.append(_fit_slide_two_bullets(
            item, (entries[index].get("build_summary") or {}).get("phases") or [],
            order_report, overrides=(bullet_overrides or {}).get(index)))
        reports.append(order_report)
        items.append(item)
    order_report = reports[0]
    # The flat map carries the FIRST opportunity's slide 2, after its rank and
    # its trim, so the two never disagree about it. Copied back rather than
    # aliased: a map holding a list that holds the map is a cycle, and every
    # consumer that copies or renders one would meet it.
    placeholder_map.update(items[0])
    placeholder_map["_panel_fit"] = fits[0]
    placeholder_map["_bullet_order"] = order_report
    # What the repeating slide renders against, one entry per opportunity, in
    # the order the deck reads.
    placeholder_map["opportunities"] = items
    placeholder_map["_panel_fit_per_opportunity"] = fits
    placeholder_map["_bullet_order_per_opportunity"] = reports

    provenance = y8.get("provenance", {})
    placeholder_map["_provenance"] = {
        key: provenance.get(key, [])
        for key in ("from_kg", "derived", "templated_defaults")
    }
    gap_paths = [
        gap["field"] for gap in provenance.get("gaps", []) if gap.get("field")
    ]
    placeholder_map["_gaps"] = gap_paths
    placeholder_map["_gap_roles"] = _gap_flagged_roles(
        gap_paths, supplied=supplied_roles(request) | _constant_roles(placeholder_map)
    )
    _add_per_opportunity_slides(items, placeholder_map, y4, y5, y7)

    # Sections the request did not ask for come back nulled (contract §2); mark
    # their slides skipped so Module 3 omits them cleanly instead of flagging
    # empty roles (PRD criterion 7). Derived from the request the adapter sent,
    # which is deterministic and does not depend on the packet.
    requested = request.get("sections_requested") or list(ALL_SECTIONS)
    placeholder_map["_skipped"] = sorted(
        SECTION_KEY_TO_SLIDE[key]
        for key in ALL_SECTIONS
        if key not in requested
    )

    return placeholder_map


def _platform_roles(headline, summary, y4):
    """Slide 3's roles from one section-4 block: the flat one or an entry."""
    return {
        "platform_headline": headline,
        "platform_summary": summary,
        "components": [
            {
                "number": layer.get("number"),
                "kicker": layer.get("kicker"),
                "title": layer.get("title"),
                "description": layer.get("body"),
            }
            for layer in y4.get("platform_layers", []) or []
        ],
    }


def _plan_roles(headline, summary, timeline):
    """Slide 4's roles from one timeline: the flat one or an entry's."""
    timeline = timeline or {}
    return {
        "plan_headline": headline,
        "plan_summary": summary,
        "timeline_columns": _axis_columns(timeline),
        "timeline_rows": [
            row
            for phase in timeline.get("phases", []) or []
            for row in _phase_rows(phase)
        ],
        "milestones": [
            _milestone_role(m, ordinal)
            for ordinal, m in enumerate(timeline.get("milestones", []) or [],
                                        start=1)
        ],
    }


def _next_steps_roles(headline, summary, y7):
    """Slide 6's roles from one section-7 block: the flat one or an entry."""
    return {
        "next_steps_headline": headline,
        "next_steps_summary": summary,
        "action_items": [
            {
                "number": a.get("number"),
                "week": a.get("week"),
                "owner": a.get("owner"),
                "title": a.get("title"),
                "description": a.get("body"),
            }
            for a in y7.get("next_steps", []) or []
        ],
    }


# The roles of the three slides that repeat beside slide 2, in the order the
# builders above produce them.
PER_OPPORTUNITY_ROLES = tuple(
    list(_platform_roles("", "", {}))
    + list(_plan_roles("", "", {}))
    + list(_next_steps_roles("", "", {}))
)


# The label each repeated slide carries on a deck with several opportunities,
# ahead of slide 2's own "OPPORTUNITY N · NAME".
REPEATED_SLIDE_LABELS = {
    "platform_section_label": "THE PLATFORM",
    "plan_section_label": "TIMELINE",
    "next_steps_section_label": "NEXT STEPS",
}


def _add_per_opportunity_slides(items, placeholder_map, y4, y5, y7):
    """Give every opportunity's item its own Platform, Timeline and Next Steps.

    Antonio, 2026-09-22, on the first two-opportunity deck: "we need two
    platform slides and 2 next steps slides for two opportunities", and the
    timeline repeats too (§4 of the build plan). Sections 4, 5 and 7 carry an
    `opportunities` list on a deck with several, one entry per opportunity in
    deck order, and each item's roles are built from its own entry by the same
    functions that build the flat map.

    COMPLETE OR NOT AT ALL, because a repeated slide resolves its roles against
    the item alone and has no fallback to the flat map: a role missing from an
    item renders `[MISSING: ...]` rather than the deck's value. So where a
    section carries no list, or one whose length does not match the deck's
    opportunities, every item takes the flat map's roles BY COPY. That is every
    one-opportunity packet and every frozen fixture, and it is what keeps them
    rendering exactly what they rendered before.

    Each item's `_gap_roles` is the flat map's, restricted to these slides'
    roles. Slide 2's items have never carried any, and restricting keeps them
    that way; on these slides the flat map's gaps are the schema's unsourceable
    fields, which are the same for every opportunity.
    """
    blocks = {4: y4, 5: y5, 7: y7}
    entries = {
        section: block.get("opportunities")
        if isinstance(block.get("opportunities"), list)
        and len(block.get("opportunities")) == len(items) else None
        for section, block in blocks.items()
    }
    flat = {role: placeholder_map.get(role) for role in PER_OPPORTUNITY_ROLES}
    gaps = [role for role in placeholder_map.get("_gap_roles", [])
            if role in PER_OPPORTUNITY_ROLES]
    for index, item in enumerate(items):
        roles = dict(flat)
        if entries[4]:
            entry = entries[4][index] or {}
            roles.update(_platform_roles(entry.get("headline", ""),
                                         entry.get("summary", ""), entry))
        if entries[5]:
            entry = entries[5][index] or {}
            roles.update(_plan_roles(entry.get("headline", ""),
                                     entry.get("summary", ""),
                                     entry.get("timeline")))
        if entries[7]:
            entry = entries[7][index] or {}
            roles.update(_next_steps_roles(entry.get("headline", ""),
                                           entry.get("summary", ""), entry))
        item.update(roles)
        # WHICH OPPORTUNITY each repeated slide is (Antonio, 2026-09-22): two
        # Platform slides told apart only by their headlines are the problem
        # slide 2's label solved, so they take the same position-and-name
        # label. The timeline too, on his read of output-17 ("the timelines
        # dont name the opportunity"), which is the one case his 2026-09-20
        # fixed TIMELINE kicker now extends: it still leads with TIMELINE,
        # and a one-opportunity deck still reads TIMELINE alone. Empty on a
        # one-opportunity deck, and the roles are optional, so that prompt
        # emits no line for them and does not move.
        title = item.get("opportunity_headline", "")
        for role, slide_label in REPEATED_SLIDE_LABELS.items():
            item[role] = (
                f"{slide_label} · {_section_label(title, index + 1, len(items))}"
                if len(items) > 1 else "")
        item["_gap_roles"] = list(gaps)
    # The flat map carries the first opportunity's, as it does slide 2's.
    for role in REPEATED_SLIDE_LABELS:
        placeholder_map[role] = items[0].get(role, "") if items else ""


# Slide 2's section label when the deck carries ONE opportunity: what it has
# always read, because a deck with one has nothing to disambiguate.
OPPORTUNITY_LABEL = "THE OPPORTUNITY"


def _section_label(title, position, total):
    """Slide 2's section label, which says WHICH opportunity on a deck with several.

    ANTONIO'S ASK, 2026-09-15, from looking at the first rendered
    two-opportunity deck: both opportunity slides read "THE OPPORTUNITY" and
    both show the identical TODAY strip, because the company baseline is one
    fact about one company resolved once for the engagement and correctly
    repeated. So two slides carried the same label and the same large figures
    and were told apart only by reading the headline. The deck was right and a
    reader flicking through it could still attribute one slide's numbers to the
    other opportunity.

    BOTH THE POSITION AND THE NAME, and each half answers a different question.
    An ordinal alone tells a reader there are two and not which is which. The
    name alone tells them which and not where they are in the deck, which is
    what someone flicking back for "the dashboards one" is actually using. His
    own suggestion was "Opportunity 1 and 2 or something"; this takes the intent
    and keeps the name, because the name is the half a reader recognises.

    Joined with the middot the deck already uses for a two-part label, which is
    its own idiom rather than a new one: slide 4 reads "PHASED ROLLOUT · 16
    WEEKS" and slide 5 "COMMERCIAL TERMS · A PERFORMANCE PARTNERSHIP".

    LENGTH IS NOT THE CONSTRAINT, and that is measured rather than assumed.
    `panel_fit`'s own estimator puts the longest real label here, "OPPORTUNITY 2
    · REAL-TIME OPERATIONAL DASHBOARD", at about 243px of the 1176px the label
    row has, against 227px for the 44-character label slide 5 already renders.
    There is room for roughly four times this before wrapping is in question.

    Uppercased the way `footer_right` is, because every section label on this
    deck is set in small caps and the template's own examples are written that
    way.

    NOT NEW PACKET MATERIAL. The name is the opportunity's own title, already in
    the packet as this section's headline; this composes a label out of what is
    there. And it does not fire on a one-opportunity deck, for the same reason
    the opportunity column is absent from a one-opportunity value-mapping table:
    a label that exists to tell two things apart is noise when there is one.
    """
    if total <= 1:
        return OPPORTUNITY_LABEL
    ordinal = f"OPPORTUNITY {position}"
    title = (title or "").strip()
    return f"{ordinal} · {title.upper()}" if title else ordinal


def _opportunity_roles(entry, baseline, section8, position=1, total=1):
    """One opportunity's slide 2 roles, and how many bullets this layer appended.

    Section 2 repeats once per opportunity (item 15, 2026-09-13), so everything
    slide 2 renders is a function of ONE entry plus two things that belong to
    the deck rather than to the opportunity: the company's baseline, which is
    the same figures whichever opportunity is being described and which take the
    TODAY slots, and section 8's origin ledger, which says whether the AFTER
    figure's field is known.

    Returns `(roles, appended)`. `appended` is how many bullets the TODAY panel
    gained here, which the rank step downstream has to know: the ranking pass
    ranked the PACKET's list, and an order checked against the longer displayed
    list matches nothing (2026-09-02).
    """
    build = entry.get("build_summary", {}) or {}
    phases = build.get("phases", []) or []
    horizon, horizon_unit = _horizon_parts(build)
    # The baseline financials take slide 2's TODAY slots so both boxes state the
    # same quantity; whatever they displace becomes a bullet rather than a loss.
    today_metrics, displaced = _today_metrics_and_leftovers(
        baseline, entry.get("today_metrics", []) or [])
    displaced_bullets = [_metric(m) for m in displaced if _metric(m)]
    target_metrics = entry.get("target_metrics", []) or []
    # Where section 8 says `target_metrics` came from, so the AFTER caption is
    # only applied to a figure whose field is known.
    target_origin = _origin_of(section8, "target_metrics")
    headline = entry.get("headline", "")
    roles = {
        # WHICH opportunity this slide is, on a deck carrying several. Composed
        # from the headline rather than sourced, and "THE OPPORTUNITY" on a deck
        # with one.
        "section_label": _section_label(headline, position, total),
        # The headline and the one-liner moved out of §2's prose and into each
        # opportunity's own entry on 2026-09-13, for the reason the section
        # repeats at all: prose sits between a heading and its yaml, and N
        # prose blocks cannot be paired with N entries.
        "opportunity_headline": headline,
        "opportunity_summary": entry.get("one_liner", ""),
        "today_metric_1": _metric(today_metrics[0]) if len(today_metrics) > 0 else "",
        "today_metric_2": _metric(today_metrics[1]) if len(today_metrics) > 1 else "",
        # `or []` rather than a default: a packet may carry the key as an
        # explicit null, which `packet_document` treats as an unaccounted leaf,
        # and this map still has to build so the guard can report on it.
        "today_pain_bullets": (list(entry.get("today_pain_points") or [])
                               + displaced_bullets),
        "after_horizon": (
            f"AFTER — TARGET IN ~{horizon} {horizon_unit.upper()}"
            if horizon is not None else ""
        ),
        "after_metric_1": (_after_metric(target_metrics[0], target_origin)
                           if len(target_metrics) > 0 else ""),
        "after_metric_2": (_after_metric(target_metrics[1], target_origin)
                           if len(target_metrics) > 1 else ""),
        "after_capability_bullets": entry.get("target_capabilities", []) or [],
        "build_summary": (
            f"THE {horizon}-{_singular(horizon_unit).upper()} BUILD\n"
            + "\n".join(_phase_line(phase) for phase in phases)
        ) if horizon is not None else "",
    }
    return roles, len(displaced_bullets)


def _horizon_parts(build):
    """`(number, plural unit)` for the build horizon, or `(None, "")`.

    Two sources and no conversion between them. The contract's own
    `duration_weeks` is week-denominated and wins where a packet carries one, so
    every packet that rendered a horizon before renders the same one. Where it is
    absent, the unit-neutral pair E11 Stage 2c added carries the number the paper
    stated and the unit it stated it in. A number with no unit yields nothing:
    the deck would have to guess which unit it was, and guessing is what the
    whole no-conversion rule exists to refuse.
    """
    if build.get("duration_weeks"):
        return build["duration_weeks"], "weeks"
    duration, unit = build.get("duration"), build.get("duration_unit")
    if duration is None or not unit:
        return None, ""
    return duration, str(unit)


def _singular(unit):
    """`weeks` as `week`, for the deck's own compound-adjective phrasing.

    `THE 14-MONTH BUILD`, the form the template's own example states. English,
    not arithmetic: the number and the unit are the paper's and neither is
    touched. `paper_extraction` already matches a stated unit by the same stem.
    """
    return unit[:-1] if unit.endswith("s") else unit


def _phase_line(phase):
    """One phase on the build strip: its label, and its summary where there is one.

    A phase whose summary the packet does not carry renders its label alone
    rather than raising. Before E11 Stage 2c this composed `label — summary`
    unconditionally and was only ever reached on packets that had both; the
    unit-neutral horizon reaches it on packets that carry the label alone.
    """
    label, summary = phase.get("label", ""), phase.get("summary")
    return f"{label} — {summary}" if summary else str(label)


def _commercial_amount_role(commercial, amount_key, note_key):
    amount = commercial.get(amount_key)
    note = commercial.get(note_key, "")
    formatted = _fmt_usd(amount) if amount is not None else ""
    if formatted and note:
        return f"{formatted} · {note}"
    return formatted or note or ""


def _comp_schedule_role(commercial):
    rows = commercial.get("comp_schedule", [])
    schedule = " · ".join(f"{r.get('year')} {r.get('pct')}%" for r in rows)
    cap_note = commercial.get("cap_note", "")
    if schedule and cap_note:
        return f"{schedule} · {cap_note}"
    return schedule or cap_note or ""


def _labelled(row, opportunity):
    """``row`` with its opportunity label first, when the packet names one."""
    return {**({"opportunity": opportunity} if opportunity else {}), **row}


def _investment_rows(commercial):
    """INVESTMENT: the PRD's cost total, one row per cost column.

    Labelled by the column and valued with the PRD's own cell text, both
    carried verbatim, so nothing is reformatted and the total row's own label
    (which can name QofAI's internal midpoint) never reaches the slide.
    """
    return [
        _labelled({"label": row.get("label"), "value": row.get("value")},
                  row.get("opportunity"))
        for row in commercial.get("investment") or []
        if row.get("label") and row.get("value")
    ]


def _return_rows(commercial):
    """RETURN: one row per case the source names, cells only where stated.

    A cell the case does not carry is left out of the record entirely rather
    than set blank, which is how an optional record field emits no line and no
    marker (`prompt_assembler._render_records`). The figures are formatted the
    way slide 5 has always formatted them (`_scenario_ebitda_gain`), split into
    their own columns; payback is the PRD's own range text.
    """
    rows = []
    for case in commercial.get("scenarios") or []:
        record = {"scenario": case.get("name")}
        usd = case.get("direct_uplift_usd_yr")
        if usd is not None:
            record["annual_ebitda"] = f"{_fmt_usd(usd)}/yr"
        pp = case.get("margin_gain_pp")
        if pp is not None:
            record["margin"] = f"+{pp}pp"
        if case.get("payback"):
            record["payback"] = case["payback"]
        rows.append(_labelled(record, case.get("opportunity")))
    return rows


def _value_mapping(commercial):
    """The value chart's cases, ONLY those carrying all three chart figures.

    Antonio, 2026-09-22: the chart is hidden when there are no comp, retained
    EBITDA and EV figures. A PRD states none of them, so on a PRD deck this is
    empty and the optional role draws nothing. A case with some but not all
    three is left out too, because a stacked bar cannot be drawn to scale from
    part of its stack.
    """
    cases = []
    for case in commercial.get("scenarios") or []:
        figures = (case.get("qofai_comp_usd"),
                   case.get("client_retained_ebitda_usd"),
                   case.get("enterprise_value_at_exit_usd"))
        if any(figure is None for figure in figures):
            continue
        cases.append(_labelled({
            "scenario": case.get("name"),
            "ebitda_gain": _scenario_ebitda_gain(case),
            "qofai_comp": (_fmt_usd(case.get("qofai_comp_usd"))
                           + (" (cap)" if case.get("qofai_comp_note") else "")),
            "client_retained_ebitda": _fmt_usd(case.get("client_retained_ebitda_usd")),
            "enterprise_value": _fmt_usd(case.get("enterprise_value_at_exit_usd")),
        }, case.get("opportunity")))
    return cases


# The packet's own deal terms, where a packet states them, as TERMS rows. These
# are the contract's pre-2026-09-22 field names (the FBK performance deal), and
# carrying them as rows rather than as fixed boxes is what shows the adaptive
# slide can hold a deal it was not designed around. A PRD states none of them,
# so on a PRD deck this is empty and the role's marker asks a reviewer.
_TERM_FIELDS = (
    ("QofAI investment",
     lambda c: _commercial_amount_role(c, "qofai_investment_usd", "qofai_investment_note")),
    ("Client up-front",
     lambda c: _commercial_amount_role(c, "client_upfront_usd", "client_upfront_note")),
    ("Comp schedule", _comp_schedule_role),
    ("Client retains", lambda c: c.get("client_retention_note") or ""),
    ("Downside protection", lambda c: c.get("no_improvement_clause") or ""),
    ("How payment works", lambda c: " · ".join(c.get("how_payment_works") or [])),
)


def _terms_rows(commercial):
    """TERMS: the deal's own rows, in the order above, only where stated."""
    rows = []
    for label, read in _TERM_FIELDS:
        value = read(commercial)
        if value:
            rows.append({"label": label, "value": value})
    return rows


# The basis line for a slide whose figures a PRD states as an indicative
# estimate, which all three current PRDs do. A deck constant, like
# `after_horizon`: it carries no number, and it is only used when the packet's
# own investment rows say the figures are indicative.
INDICATIVE_FOOTNOTE = ("Indicative QofAI estimate, to be confirmed in scoping. "
                       "Directional, for decision support.")


def _constant_roles(placeholder_map):
    """Roles holding a deck constant this run, which are never unconfirmed.

    The indicative footnote is chosen because §11.2 says its figures are
    indicative, and it carries no sourced number of its own. Without this, the
    absent reviewer-class EV paths behind the same role would mark a line that
    states nothing to confirm as "(unconfirmed, see gaps)" on every PRD deck.
    """
    if placeholder_map.get("terms_footnote") == INDICATIVE_FOOTNOTE:
        return {"terms_footnote"}
    return set()


def _terms_footnote(commercial):
    """The packet's own footnote, else the indicative basis line, else none."""
    stated = _ev_footnote_role(commercial)
    if stated:
        return stated
    if any(row.get("basis") == "indicative"
           for row in commercial.get("investment") or []):
        return INDICATIVE_FOOTNOTE
    return ""


# ===========================================================================
# Status mapping half
# ---------------------------------------------------------------------------
# Runs only on a clean status ok-packet that cleared both gates. Parallel to the
# proposal mapping half above, and structured the same way: resolve the packet's
# structured blocks by key path into the status template's roles, carry the
# labeled copy lines verbatim, and extract the §5 provenance/gaps side channels.
# It invents nothing. Everything time-aware (dated columns, the TODAY marker,
# "Week N of M") is read straight from the packet's check_in_date-derived fields
# (§1 engagement, §2 tracking), never from the render date (PRD S4).
#
# The one structural difference from the proposal map: the workstreams block is
# an ARRAY, so this returns a ``workstreams`` list of per-workstream role dicts
# (one status slide each) rather than only flat roles. The assembler emits one
# slide per entry (PRD S7). All status field names live here, so a schema or
# slide-shape change is a contained mapping-half edit (PRD §5.2.C).
# ===========================================================================

STATUS_CONFIDENTIALITY_DEFAULT = "QOFAI CONFIDENTIAL"
STATUS_DECK_TYPE_LABEL_DEFAULT = "PROJECT CHECK-IN"

# Which framing block a workstream `stage` selects (PRD S12). `new` frames the
# current state as TODAY and the target as AFTER; `existing` frames them as
# WHERE WE ARE and TARGET. Frame A is always the current state, Frame B the
# target, so the assembler and renderer stay stage-agnostic — the selection
# happens here, from `stage`, never guessed from content.
_STAGE_FRAMES = {
    "new": {"a": "today", "b": "after", "a_bullets": "pain_bullets", "b_bullets": "capability_bullets"},
    "existing": {"a": "where_we_are", "b": "target", "a_bullets": "notes", "b_bullets": "notes"},
}

# Reverse index for the non-workstream status roles that could be §5-gap-flagged,
# mirroring the proposal's ROLE_SOURCE_PATHS. Per-workstream gaps are resolved
# separately (they carry an array index the flat roles do not). Copy roles and
# templated defaults have no structured source path and are intentionally absent.
STATUS_ROLE_SOURCE_PATHS = {
    "client_full": ["company.name"],
    "client_short": ["company.client_short"],
    "pe_firm": ["company.pe_firm"],
    "project_week": ["engagement.project_week"],
    "total_slides": ["deck.total_slides"],
    "tracking_summary": ["tracking.summary"],
    "timeline_columns": ["tracking.columns"],
    "today_marker_week": ["tracking.today_marker"],
    "today_marker_label": ["tracking.today_marker"],
    "bar_categories": ["tracking.bar_categories"],
    "gantt_bars": ["tracking.lanes"],
    "slip_or_buffer_markers": ["tracking.slip_or_buffer_markers"],
}

# A gap path pointing inside a specific workstream, e.g.
# "workstreams[0].after.metrics[0].value".
_WS_GAP_RE = re.compile(r"^workstreams\[(\d+)\]\.([A-Za-z_]+)")


def _status_metric(metric):
    """Fold a {value, label} metric into one "VALUE · LABEL" string, matching the
    proposal ``_metric`` — the renderer splits on the middot to size the value.
    Carries whatever the packet holds; invents nothing."""
    value = metric.get("value")
    label = metric.get("label")
    if value and label:
        return f"{value} · {label}"
    return value or label or ""


def _status_frames(workstream):
    """Resolve a workstream's two framing blocks into the neutral Frame A / Frame
    B roles, selecting the source blocks from ``stage`` (PRD S12). Blocks the
    stage does not use are ``null`` in the packet and simply are not referenced,
    so they never surface as missing-field markers (PRD S9)."""
    frames = _STAGE_FRAMES.get(workstream.get("stage"))
    if frames is None:
        # Unknown stage: leave the frames empty so Module 3 flags them, rather
        # than guessing which blocks to render.
        return {
            "frame_a_label": "", "frame_a_metrics": [], "frame_a_bullets": [],
            "frame_b_label": "", "frame_b_metrics": [], "frame_b_bullets": [],
        }
    block_a = workstream.get(frames["a"]) or {}
    block_b = workstream.get(frames["b"]) or {}
    return {
        "frame_a_label": block_a.get("label", ""),
        "frame_a_metrics": [_status_metric(m) for m in block_a.get("metrics", [])],
        "frame_a_bullets": list(block_a.get(frames["a_bullets"], []) or []),
        # The current-state block labels itself (`TODAY` / `WHERE WE ARE`); the
        # target block names its horizon instead of a plain label.
        "frame_b_label": block_b.get("horizon", ""),
        "frame_b_metrics": [_status_metric(m) for m in block_b.get("metrics", [])],
        "frame_b_bullets": list(block_b.get(frames["b_bullets"], []) or []),
    }


def _status_progress_items(progress_tracker):
    """Flatten a progress tracker's groups into one record per item, carrying the
    group name, the group's status badge, the item label (with its detail folded
    in), and the load-bearing ``state`` (done / pending / in_process) verbatim —
    never synthesized (PRD S13)."""
    items = []
    for group in progress_tracker.get("groups", []):
        group_name = group.get("name")
        badge = group.get("status_label")
        for item in group.get("items", []):
            label = item.get("label")
            detail = item.get("detail")
            if detail:
                label = f"{label} · {detail}"
            items.append({
                "group": group_name,
                "status_label": badge,
                "label": label,
                "state": item.get("state"),
            })
    return items


def _status_workstream(workstream):
    """Map one packet workstream into its status-slide role dict (the per-slide
    map the repeating slide renders against)."""
    summary = workstream.get("summary", "") or ""
    hook = workstream.get("summary_hook")
    if hook:
        summary = f"{summary} {hook}".strip()
    progress_tracker = workstream.get("progress_tracker") or {}
    item = {
        "section_label": workstream.get("section_label", ""),
        "workstream_name_full": workstream.get("name_full", ""),
        "workstream_name_accent": workstream.get("name_accent", ""),
        "workstream_summary": summary,
        "progress_label": progress_tracker.get("label", ""),
        "progress_right_label": progress_tracker.get("right_label", ""),
        "progress_items": _status_progress_items(progress_tracker),
        "ws_next_steps": list(workstream.get("next_steps", []) or []),
    }
    item.update(_status_frames(workstream))
    return item


def _status_gap_roles(gap_paths, n_workstreams, supplied=()):
    """Translate §5 gap paths into the roles they flag (PRD S13/criterion 10).

    Returns ``(top_roles, per_workstream_roles)`` — the singleton-slide roles to
    flag on the flat map, and a per-workstream list of role sets so a gap on
    ``workstreams[0].after.metrics[0].value`` flags only that workstream's
    ``frame_b_metrics``. A gap path that maps to no rendered role yields nothing.

    ``supplied`` are the roles the reviewer filled, dropped for the reason
    `supplied_roles` gives. This path needs its own TRANSLATOR, because a status
    gap carries a workstream index and a proposal gap does not, and it must not
    have its own SUPPRESSION: that is one rule and it is stated once, in the set
    both callers pass in. Only the top-level roles are subtracted, because a
    reviewer-supplied field is a deck-wide recurring role and never one
    workstream's.
    """
    top = set()
    per_ws = [set() for _ in range(n_workstreams)]
    for gap in gap_paths:
        ws_match = _WS_GAP_RE.match(gap)
        if ws_match:
            idx = int(ws_match.group(1))
            block = ws_match.group(2)
            if idx >= n_workstreams:
                continue
            # The progress tracker is not a stage-selected framing block, so it
            # never reached the frame branch below and a gap inside it flagged
            # nothing. That is the one place a status packet can flag a
            # completion state, which is what the scope-assessment path (H1)
            # declares a gap on for every state it infers.
            if block == "progress_tracker":
                per_ws[idx].add("progress_items")
                continue
            frames = None
            for stage_frames in _STAGE_FRAMES.values():
                if block == stage_frames["a"]:
                    frames = "a"
                elif block == stage_frames["b"]:
                    frames = "b"
                if frames:
                    break
            if frames is None:
                continue
            rest = gap[ws_match.end():]
            if "metric" in rest:
                per_ws[idx].add(f"frame_{frames}_metrics")
            elif any(k in rest for k in ("bullet", "note", "capabilit", "pain")):
                per_ws[idx].add(f"frame_{frames}_bullets")
            elif "horizon" in rest or "label" in rest:
                per_ws[idx].add(f"frame_{frames}_label")
            else:
                per_ws[idx].add(f"frame_{frames}_metrics")
            continue
        for role, paths in STATUS_ROLE_SOURCE_PATHS.items():
            for path in paths:
                if (
                    gap == path
                    or gap.startswith(path + ".")
                    or gap.startswith(path + "[")
                    or path.startswith(gap + ".")
                ):
                    top.add(role)
                    break
    return sorted(top - set(supplied or ())), per_ws


def map_status_packet(packet_md, request):
    """Map a clean status ok-packet into the flat placeholder map plus a
    ``workstreams`` list of per-slide role dicts (PRD §5.4).

    ``request`` supplies the resolved project reference (used only to derive the
    footer engagement label, mirroring the proposal's footer_right derivation).
    Everything else is read from the packet. Side channels match the proposal
    map: ``_provenance``, ``_gaps``, ``_gap_roles``, and ``_skipped`` (by section
    key here, since the workstreams section expands to a variable slide count).
    """
    sections = _split_sections(packet_md)
    request = request or {}

    y1 = _section_yaml(sections.get(1, ""))
    y2 = _section_yaml(sections.get(2, ""))
    y3 = _section_yaml(sections.get(3, ""))
    y4 = _section_yaml(sections.get(4, ""))
    y5 = _section_yaml(sections.get(5, ""))

    deck = y1.get("deck", {})
    company = y1.get("company", {})
    engagement = y1.get("engagement", {})
    cover = y1.get("cover", {})
    tracking = y2.get("tracking", {})
    workstreams = y3.get("workstreams", []) or []
    next_steps_slide = y4.get("next_steps_slide", {})

    project_name = request.get("project") or ""
    deck_kicker = cover.get("deck_kicker", "")
    total_slides = deck.get("total_slides")

    placeholder_map = {
        # Slide 1 — Title / Cover
        "deck_kicker": deck_kicker,
        "prepared_for": cover.get("prepared_for", ""),
        "deck_title_accent": cover.get("deck_title_accent", ""),
        "deck_title_primary": cover.get("deck_title_primary", ""),
        "status_subtitle": cover.get("status_subtitle", ""),

        # Slide 2 — Project Tracking (dated Gantt with TODAY marker)
        "tracking_section_label": tracking.get("section_label", ""),
        "tracking_headline": tracking.get("headline", ""),
        "tracking_summary": tracking.get("summary", ""),
        "timeline_columns": [
            {"id": c.get("id"), "date": c.get("date"), "label": c.get("label")}
            for c in tracking.get("columns", [])
        ],
        "today_marker_week": (tracking.get("today_marker") or {}).get("on_week", ""),
        "today_marker_label": (tracking.get("today_marker") or {}).get("label", ""),
        "bar_categories": [
            {"id": b.get("id"), "color": b.get("color"), "covers": b.get("covers")}
            for b in tracking.get("bar_categories", [])
        ],
        # Bars flattened one record per bar, each carrying its lane. `category`
        # drives the fill color (PRD S11); `state` is annotation only.
        "gantt_bars": [
            {
                "lane": lane.get("name"),
                "label": bar.get("label"),
                "start_week": bar.get("start_week"),
                "end_week": bar.get("end_week"),
                "category": bar.get("category"),
                "state": bar.get("state"),
            }
            for lane in tracking.get("lanes", [])
            for bar in lane.get("bars", [])
        ],
        "slip_or_buffer_markers": [
            {"label": m.get("label"), "week": m.get("week"), "kind": m.get("kind")}
            for m in tracking.get("slip_or_buffer_markers", [])
        ],

        # Slide 4 — Next Steps (one column per active workstream)
        "next_steps_section_label": next_steps_slide.get("section_label", ""),
        "next_steps_headline": next_steps_slide.get("headline", ""),
        "next_steps_summary": next_steps_slide.get("summary", ""),
        "next_steps_items": [
            {
                "column_title": column.get("title"),
                "workstream_id": column.get("workstream_id"),
                "number": step.get("number"),
                "title": step.get("title"),
                "body": step.get("body"),
            }
            for column in next_steps_slide.get("columns", [])
            for step in column.get("steps", [])
        ],

        # Recurring fields (fill once, propagate) — all time-aware fields come
        # from the packet's check_in_date-derived engagement block (PRD S4).
        "client_full": company.get("name", ""),
        # The same precedence the proposal map uses, from the same function.
        "client_short": client_short(request, company),
        "pe_firm": company.get("pe_firm", ""),
        "check_in_date": engagement.get("check_in_date_display", ""),
        "project_week": engagement.get("project_week", ""),
        "confidentiality": engagement.get("confidentiality") or STATUS_CONFIDENTIALITY_DEFAULT,
        "month_year": engagement.get("check_in_month_year", ""),
        # Footer engagement label, derived from the project name the request
        # scoped (mirrors the proposal footer_right derivation); the packet
        # carries it only in prose. Empty for a nameless project → missing marker.
        "footer_engagement": f"{project_name.upper()} CHECK-IN" if project_name else "",
        # Footer denominator, read from data, never a hardcoded 05 (PRD S7).
        "total_slides": str(total_slides) if total_slides is not None else "",
        "deck_type_label": (
            deck_kicker.split(" · ")[0] if deck_kicker else STATUS_DECK_TYPE_LABEL_DEFAULT
        ),
    }

    # Per-workstream slide maps (one status slide each, in packet order).
    provenance = y5.get("provenance", {})
    gap_paths = [g["field"] for g in provenance.get("gaps", []) if g.get("field")]
    top_gap_roles, per_ws_gap_roles = _status_gap_roles(
        gap_paths, len(workstreams), supplied=supplied_roles(request)
    )

    workstream_maps = []
    for idx, workstream in enumerate(workstreams):
        item = _status_workstream(workstream)
        item["_gap_roles"] = sorted(per_ws_gap_roles[idx])
        workstream_maps.append(item)
    placeholder_map["workstreams"] = workstream_maps

    placeholder_map["_provenance"] = {
        key: provenance.get(key, [])
        for key in ("from_kg", "from_plan_schedule", "derived", "templated_defaults")
    }
    placeholder_map["_gaps"] = gap_paths
    placeholder_map["_gap_roles"] = top_gap_roles

    # Skip is by section KEY on the status path (the workstreams section expands
    # to N slides, so a slide-number skip set does not fit). Derived from the
    # request the adapter sent — deterministic, packet-independent (PRD S9).
    requested = request.get("sections_requested") or list(STATUS_ALL_SECTIONS)
    placeholder_map["_skipped"] = [
        key for key in STATUS_ALL_SECTIONS if key not in requested
    ]

    return placeholder_map


# Mapping-half dispatch by deck_type. Selected in ``run_adapter``; the transport
# half is shared.
MAPPERS = {
    "proposal": map_packet,
    "status": map_status_packet,
}


# ===========================================================================
# One interface over both halves
# ===========================================================================

def run_adapter(company, project, provider, *, deck_type="proposal", **kwargs):
    """The adapter's single interface (PRD §4). Runs the transport half, and on
    a clean ok-packet that cleared both gates runs the mapping half and returns
    the flat placeholder map. Error and gate-failure results pass through
    unchanged, carrying no map (the golden rule).

    ``deck_type`` selects both the request profile (transport half) and the
    mapping half (``MAPPERS``). The proposal path is the default, so existing
    callers are unchanged; the status path resolves the status packet's
    structured blocks into the status template's roles instead."""
    mapper = MAPPERS.get(deck_type)
    if mapper is None:
        raise ValueError(f"unknown deck_type: {deck_type!r}")
    # Reviewer bullet switches belong to the mapping half and mean nothing to
    # the transport half, so they come off here rather than travelling through
    # a dispatch that would have to ignore them. Only the proposal path has
    # slide 2 panels to switch, so only that mapper is offered them.
    bullet_overrides = kwargs.pop("bullet_overrides", None)
    mapper_kwargs = ({"bullet_overrides": bullet_overrides}
                     if deck_type == "proposal" and bullet_overrides else {})
    result = dispatch_and_gate(company, project, provider, deck_type=deck_type, **kwargs)
    if result["status"] != "ok":
        return result
    return {
        "status": "ok",
        "confidence": result["confidence"],
        "data_completeness": result["data_completeness"],
        # What the data source says this packet IS. Carried through so the
        # wire-together layer can check it against the deck it was asked to build:
        # the mapping half will happily map a packet for the OTHER deck type and
        # return a map of misses, which surfaces much later as a coverage failure
        # listing every unmappable field.
        "packet_type": result.get("packet_type"),
        # The raw ok-packet is carried alongside the map so the wire-together
        # layer can run the field-coverage guard (packet ↔ prompt) without a
        # second dispatch. It is not slide content and Module 3 ignores it.
        "packet": result["packet"],
        "placeholder_map": mapper(result["packet"], result["request_echo"],
                                  **mapper_kwargs),
        # Beside the map, never in it. See the transport half's own note. The
        # attachment record travels on the same terms: the studio saves it with
        # the deck and the mapping half never looks inside it.
        "provenance": result.get("provenance") or {},
        "attachments": result.get("attachments") or [],
        # Item 24, on the same terms as the two above.
        "writing_ledger": result.get("writing_ledger") or None,
        # Part A4, on the same terms.
        "flag_audit": result.get("flag_audit") or [],
        # WHICH SECTIONS CLEARED THE FLOOR AND WHICH ARE MARKED (item 15). The
        # gate runs per opportunity, so a deck can render with one good section
        # and one thin one, and that is the shape most likely to be sent to a
        # client without anyone noticing. It travels beside the map, never in
        # it: a score is an annotation about our own confidence and belongs in
        # front of a reviewer rather than on a slide.
        "opportunities": result.get("opportunities") or [],
    }
