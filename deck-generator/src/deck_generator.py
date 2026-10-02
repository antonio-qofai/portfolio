"""Wire Together — the end-to-end deck-generation flow.

The one top-level entry point (build-plan-v2.md "Wire Together"; PRD §4). It
takes a `deck_type`, a client/company reference, a required project reference,
and a provider, and returns either a finished per-slide design prompt or a
non-render result — never a partial or invented prompt.

- Template Loader (Module 1) loads the matching template's slide structure.
  This runs independently of the data path and cannot fail on client data.
- Data Source Adapter (Module 2) shapes the request, dispatches, polls, and
  branches internally on the envelope `status` and then the confidence/
  completeness gates. Only a clean ok-packet clearing both gates reaches the
  mapping half.
- Prompt Assembler (Module 3) merges the loaded structure with the mapped
  placeholder map into the final prompt, only on that clean path.

Both `deck_type = proposal` and `deck_type = status` run the same flow; the
deck_type selects the template, the adapter's request profile and mapping half,
the coverage profile, and the render scaffold. The status path produces a
variable-count deck (cover + tracking + N workstreams + next_steps). An error
envelope or a gate failure returns the adapter's own surfaced status and payload
unchanged, never a prompt — the non-negotiable bar from PRD criteria 4 and 5
(S5, S6 on the status path).

`generate_deck_prompt` returns the bare content prompt only and touches no
files. `generate_and_save_deck` wraps it to produce both deliverables for one
run: it prepends the pinned house-style header (`deck_renderer.
prepend_house_style_brief`) to that content, saves the result as the Claude
Design deliverable, and, from that same string, renders a finished HTML deck via
the Anthropic API (the Claude Code deliverable) — the renderer strips the header
before sending, so it renders the same content. Both files share one
auto-incremented number, so `generated-prompt-N.txt` and `output-N.html` always
correspond. Nothing is written on a non-render result, and the agent still sends
nothing to a client — the HTML is a draft for human review.

On a render, the render-fidelity guard (`render_guard.check_render_fidelity`,
PRD criterion 13) runs on the produced HTML before either file is written: it
checks that every reviewer marker and load-bearing value in the prompt
survived into the HTML, the prompt-to-HTML boundary the coverage guard does
not reach.

Five guards run on the clean path, at five different boundaries, because a deck
can be wrong in five unrelated ways:

- `packet_consistency.check_consistency` — the packet against ITSELF, before any
  prompt exists. The fidelity guards below can pass perfectly on a packet that
  says the plan is 16 weeks in one field and 10 in another; faithfully rendering
  an incoherent packet produces an incoherent deck.
- `coverage_guard.check_coverage` — the packet-to-prompt boundary (no field
  dropped on the floor).
- `render_guard.check_render_fidelity` — the prompt-to-HTML boundary (no value
  lost in the render).
- `layout_guard.check_layout` — the HTML-to-PIXELS boundary. The three above all
  read the deck as a string, and a clipped label defeats all of them:
  `overflow:hidden` hides text at paint time without removing it from the DOM, so
  a substring search finds it while the slide on screen reads "Switch pro". This
  one renders the deck in headless Chrome and measures it.
- `display_text_guard.check_display_text` — the HTML-to-WORDS boundary. The four
  above all pass on a slide that reads `<span class="base">Project Planning.</span>`
  in full view: the value is there, the packet is coherent, and the box measures
  clean. This one fails the render when the deck's own markup is readable as copy.
"""

import os
import re

from coverage_guard import check_coverage
from data_source_adapter import (
    bullet_selection,
    bullet_selection_per_opportunity,
    run_adapter,
)
from bullet_type import apply_panel_type
from deck_renderer import prepend_house_style_brief, render_deck_html, strip_gap_flags
from display_text_guard import check_display_text
from layout_guard import check_layout
from packet_consistency import check_consistency
from preference_store import (
    DEFAULT_STORE_PATH,
    applicable_preferences,
    render_preferences_block,
)
from prompt_assembler import assemble_prompt
from render_guard import check_render_fidelity
from commercial_blocks import normalise as normalise_commercial

# The roles `commercial_blocks.normalise` writes slide 5 from.
COMMERCIAL_BLOCK_ROLES = ("investment_rows", "return_rows", "terms_rows",
                          "value_mapping")
from text_gate import review_flags
from template_loader import load_template

_HERE = os.path.dirname(__file__)

# Output roots, resolved relative to this file so a stranger can run the agent
# from anywhere. Each client gets its own subfolder under these, keyed by the
# packet's short brand form (company.client_short, e.g. "FBK" / "Ridgeline"), so
# one client's decks never read as another's iterations:
#     generated-prompts/<client>/generated-prompt-<N>.txt   (Claude Design input)
#     decks/<client>/claude code/output-<N>.html            (Claude Code deliverable)
#     decks/<client>/claude design/output-<N>.pdf           (human-made, not written here)
# The shared number N restarts per client and auto-increments within it, so a
# prompt and its rendered deck always correspond. Overridable per call.
DEFAULT_PROMPTS_ROOT = os.path.join(_HERE, "..", "generated-prompts")
DEFAULT_DECKS_ROOT = os.path.join(_HERE, "..", "decks")
DECK_CODE_SUBDIR = "claude code"      # rendered-HTML leaf within a client's deck folder
DECK_DESIGN_SUBDIR = "claude design"  # human-made design PDFs; created but not written here

# Both outputs share one number: generated-prompt-<N>.txt and output-<N>.html.
_PROMPT_FILE_RE = re.compile(r"^generated-prompt-(\d+)\.txt$")
_DECK_FILE_RE = re.compile(r"^output-(\d+)\.html$")

# Template path per deck_type. Both the proposal and the status paths are wired.
# A caller may override the path via `template_path` for testing or reuse without
# touching this default.
DECK_TYPE_TEMPLATE_PATHS = {
    "proposal": os.path.join(_HERE, "..", "templates", "proposal-template.md"),
    "status": os.path.join(_HERE, "..", "templates", "status-template.md"),
}

SUPPORTED_DECK_TYPES = ("proposal", "status")

# The `packet_type` each deck type's contract declares in its frontmatter
# (proposal-data-request-CONTRACT.md / status-data-request-CONTRACT.md). Config,
# not logic: a new deck type adds one entry. Used to catch the wrong DATA for the
# requested deck before any field is mapped — see `_check_packet_type`.
DECK_TYPE_PACKET_TYPES = {
    "proposal": "project_planning_proposal",
    "status": "project_status_check_in",
}

# Human-readable name per deck type, for the mismatch message only.
_DECK_TYPE_LABELS = {"proposal": "proposal", "status": "status check-in"}


def _check_packet_type(deck_type, packet_type):
    """Return an error envelope when the packet is the wrong TYPE for this deck.

    Every packet declares in its frontmatter what it is
    (``packet_type: project_planning_proposal`` / ``project_status_check_in``).
    Asking for one deck type against the other type's packet is a realistic
    reviewer mistake — pick a fixture, then change the deck-type dropdown — and
    without this check it surfaces as a coverage failure listing forty-odd
    unmappable field paths, which reads as a broken renderer rather than as
    "wrong packet". Neither the confidence/completeness gates nor the consistency
    guard catch it: the packet is internally fine, it is just not this deck's
    data.

    Returns ``None`` when the types agree, or when the packet declares no
    ``packet_type`` at all — an undeclared type is not evidence of a mismatch,
    and a hand-written packet without frontmatter metadata must stay usable.
    """
    expected = DECK_TYPE_PACKET_TYPES.get(deck_type)
    if not packet_type or not expected or packet_type == expected:
        return None
    # Name the deck type the packet IS for, when it is one we know.
    actual_deck_type = next(
        (dt for dt, pt in DECK_TYPE_PACKET_TYPES.items() if pt == packet_type), None
    )
    if actual_deck_type:
        remediation = (
            f"Either set the deck type to "
            f"'{actual_deck_type}', or point at a "
            f"{_DECK_TYPE_LABELS.get(deck_type, deck_type)} packet for this client."
        )
    else:
        remediation = (
            f"Point at a {_DECK_TYPE_LABELS.get(deck_type, deck_type)} packet "
            f"(one whose frontmatter declares packet_type: {expected})."
        )
    return {
        "status": "error",
        "code": "packet_type_mismatch",
        "message": (
            f"This packet is not {_DECK_TYPE_LABELS.get(deck_type, deck_type)} "
            f"data. You asked for a "
            f"{_DECK_TYPE_LABELS.get(deck_type, deck_type)} deck "
            f"(expects packet_type: {expected}), but the packet declares "
            f"packet_type: {packet_type}."
        ),
        "remediation": remediation,
        "details": {"expected_packet_type": expected, "packet_type": packet_type},
    }


def generate_deck_prompt(
    deck_type, company, project, provider, *, template_path=None,
    commercial_rows=None, **adapter_kwargs
):
    """Run the full flow for one deck request.

    ``company`` and ``project`` are the client/project references the adapter
    sends untouched (a name, a name plus ``pe_firm``, or a UUID); ``provider``
    is the background-dispatch object Module 2 polls (a ``FixtureProvider``
    against the frozen packet, or the live ``proposal-data-provider`` when it
    ships). ``adapter_kwargs`` passes straight through to ``run_adapter``
    (``pe_firm``, ``proposal_date``, ``sections_requested``, ``options``,
    ``poll_interval``, ``max_polls``, ``sleep``).

    ``commercial_rows`` (``[{"label", "value"}, ...]``) are deal terms a caller
    states up front. Since 2026-09-23 they are the commercial slide's TERMS rows
    and replace any terms the packet states. With none given, the packet's own
    terms render, and a packet with none (every PRD) leaves the
    ``[MISSING: terms_rows]`` marker for a reviewer rather than an invented term.

    Returns one of:

    - ``{"status": "error", "code", "message", "remediation", "details"}`` on
      any error envelope (Module 2, unchanged).
    - ``{"status": "review", "confidence", "data_completeness", "missing_fields"}``
      on a gate failure (Module 2, unchanged).
    - ``{"status": "ok", "prompt", "confidence", "data_completeness",
      "bullet_selection"}`` on a clean, gate-clearing packet: the full per-slide
      design prompt, plus the record of which of slide 2's bullets its panels
      held and who chose the order (``None`` on a deck with no such panel).

    ``deck_type`` selects the template, the adapter's request profile and mapping
    half, the coverage profile, and (in ``generate_and_save_deck``) the render
    scaffold. The status path produces a variable-count deck (cover + tracking +
    N workstreams + next_steps); everything else in the flow is shared.

    Raises ``ValueError`` for a ``deck_type`` outside the two the contract
    recognizes (``proposal`` / ``status``) — a caller-side violation, not a
    handled business outcome.
    """
    if deck_type not in SUPPORTED_DECK_TYPES:
        raise ValueError(f"unknown deck_type: {deck_type!r}")

    # Template Loader: structure only, independent of the data path below.
    path = template_path or DECK_TYPE_TEMPLATE_PATHS[deck_type]
    template = load_template(path)

    # Data Source Adapter: transport half (dispatch, poll, branch on status,
    # check gates), then, only on a clean ok-packet, the mapping half — both
    # selected by deck_type (request profile + mapper).
    result = run_adapter(company, project, provider, deck_type=deck_type, **adapter_kwargs)

    if result["status"] != "ok":
        # Error envelope or gate failure: surfaced unchanged, no prompt
        # (PRD criteria 4 and 5 / S5, S6).
        return result

    # Wrong-data check, before anything is mapped. A packet for the OTHER deck
    # type clears every gate and is internally consistent — it is simply not this
    # deck's data — so without this it surfaces downstream as a coverage failure
    # naming every unmappable field, which reads as a broken renderer.
    mismatch = _check_packet_type(deck_type, result.get("packet_type"))
    if mismatch:
        return mismatch

    # Packet-consistency guard: the packet may not contradict itself. The gates
    # upstream check confidence and completeness — whether the values are trusted
    # and present — not whether they agree with each other. A packet whose Gantt
    # columns stop at week 10 while its footer claims a 16-week plan clears every
    # gate and renders a deck that tells a client two different things. Raises
    # ConsistencyError here, before a prompt is assembled and before an API call
    # is spent, since no downstream step can repair the data.
    check_consistency(result["packet"], deck_type=deck_type)

    # A caller's own deal terms, when it has them up front. They are the
    # commercial slide's TERMS rows (2026-09-23) and they WIN over any terms
    # the packet states, because a human stating the deal is the confirmation
    # the packet cannot give. Left unset, the packet's own terms stand, and a
    # packet with none (every PRD) leaves the role's marker for a reviewer.
    # The parameter keeps its old name so existing callers are unchanged.
    if commercial_rows:
        result["placeholder_map"]["terms_rows"] = list(commercial_rows)

    # Prompt Assembler: only reached on the clean ok path.
    prompt = assemble_prompt(template, result["placeholder_map"])

    # Field-coverage guard: no populated rendered-section packet field may be
    # dropped without a slot or an explicit allowlist entry. Raises
    # CoverageError (naming the exact field paths) rather than shipping a prompt
    # that silently lost a field — faithful reproduction is the whole point.
    check_coverage(
        result["packet"], template, result["placeholder_map"], prompt, deck_type=deck_type
    )

    return {
        "status": "ok",
        "prompt": prompt,
        # Short brand form (company.client_short, falling back to the full name),
        # so the save wrapper can route this client's outputs into their own
        # folder without the caller having to name it.
        "client_short": result["placeholder_map"].get("client_short", ""),
        "confidence": result["confidence"],
        "data_completeness": result["data_completeness"],
        # Slide 2's bullet selection: which of the packet's bullets the panels
        # held, and whether a model chose the order. Carried out of the pipeline
        # because the placeholder map does not leave this function, and without it
        # a reviewer cannot see that a panel showed four of nine — which is half
        # of what makes trimming a list acceptable at all. ``None`` on a deck with
        # no such panel.
        "bullet_selection": bullet_selection(result["placeholder_map"]),
        # The same record for every opportunity the deck carries, which is what
        # the studio's bullet switches are grouped by (item 16's toggle rider).
        # The singular key above stays exactly what it was, because everything
        # that reads it is describing the first slide 2 and still should.
        "bullet_selection_per_opportunity":
            bullet_selection_per_opportunity(result["placeholder_map"]),
        # What the reviewer is told about sources (item 14, step 4). Carried out
        # of the pipeline for the same reason `bullet_selection` is: the studio
        # cannot see inside this function. Empty on a run with no attachment,
        # and it reaches no template role, no prompt and no deck.
        "provenance": result.get("provenance") or {},
        # The commercial slide's block data (2026-09-23), carried out for the
        # same reason: `generate_and_save_deck` rewrites slide 5's blocks from
        # it after the render (`commercial_blocks.normalise`), and the
        # placeholder map does not leave this function. Read as
        # `result.get("placeholder_map")` there, it was always empty, so the
        # first live render wrote nothing and every test still passed.
        "commercial_blocks_data": {
            role: result["placeholder_map"].get(role)
            for role in COMMERCIAL_BLOCK_ROLES
        },
        # What the deck was built from (item 14, step 5), carried out for the
        # same reason: the studio saves it into the deck store's `details` and
        # cannot see inside this function. Each attachment's extracted text,
        # filename, kind, size and SHA-256, in precedence order. Empty on a run
        # with no attachment, and it reaches no role, no prompt and no deck.
        "attachments": result.get("attachments") or [],
        # WHICH FRAMING LINES WERE WRITTEN AND WHICH WERE REFUSED (item 24),
        # carried out for the reason the three above are: the studio cannot see
        # inside this function, and the packet that holds the same record as
        # section 8 never leaves it and is never written to disk. Every run
        # before this one computed the ledger and discarded it, which is why two
        # live renders in a row could not be diagnosed. `None` on a run with no
        # writing pass, which is every fixture run. It reaches no role, no
        # prompt and no deck.
        "writing_ledger": result.get("writing_ledger") or None,
        # Part A4: the Next Steps gaps the document seems to state, for the
        # missing-values card. Reaches no role, no prompt and no deck.
        "flag_audit": result.get("flag_audit") or [],
        # WHICH SECTIONS ARE MARKED (item 15). The gate runs per opportunity, so
        # a deck can render with one section clear and another below the floor.
        # Carried out of the pipeline for the reason `provenance` is: the studio
        # cannot see inside this function, and a deck quietly carrying one good
        # section and one thin one is the shape most likely to reach a client
        # with nobody having noticed. It reaches no role, no prompt and no deck.
        "opportunities": result.get("opportunities") or [],
    }


def _max_number(directory, pattern):
    """Highest N among files in ``directory`` matching ``pattern`` (0 if none/absent)."""
    if not os.path.isdir(directory):
        return 0
    highest = 0
    for name in os.listdir(directory):
        match = pattern.match(name)
        if match:
            highest = max(highest, int(match.group(1)))
    return highest


def _slugify_client(client_short):
    """Turn a client's short brand form into a safe, readable folder name.

    Preserves case and spaces (so ``"Ridgeline"`` stays ``"Ridgeline"`` and
    ``"FBK"`` stays ``"FBK"``), only neutralizing path separators and trimming
    surrounding whitespace. Falls back to ``"_unknown_client"`` for an empty
    slug so a nameless packet still lands somewhere instead of writing into a
    root folder.
    """
    slug = (client_short or "").strip().replace(os.sep, "-")
    if os.altsep:
        slug = slug.replace(os.altsep, "-")
    return slug or "_unknown_client"


def next_output_number(prompts_dir, decks_dir):
    """The next unused output number, shared by the prompt and the deck.

    Scans both folders for ``generated-prompt-<N>.txt`` and ``output-<N>.html``
    and returns one past the highest N seen across either — so a prompt and its
    deck always get the same number, and re-running never overwrites a prior
    pair. Starts at 1 when both folders are empty. When the folders are a single
    client's per-client dirs, the number naturally restarts per client.
    """
    highest = max(
        _max_number(prompts_dir, _PROMPT_FILE_RE),
        _max_number(decks_dir, _DECK_FILE_RE),
    )
    return highest + 1


def generate_and_save_deck(
    deck_type,
    company,
    project,
    provider,
    *,
    client_slug=None,
    deck_path_override=None,
    prompts_dir=None,
    decks_dir=None,
    prompts_root=DEFAULT_PROMPTS_ROOT,
    decks_root=DEFAULT_DECKS_ROOT,
    render=True,
    model=None,
    renderer=render_deck_html,
    client=None,
    template_path=None,
    commercial_rows=None,
    strict_render_fidelity=False,
    strict_layout=False,
    check_deck_layout=True,
    apply_preferences=True,
    preferences_path=DEFAULT_STORE_PATH,
    **adapter_kwargs,
):
    """Run the full flow and persist both deliverables with a shared number.

    Builds the prompt via ``generate_deck_prompt``. On any non-``ok`` result
    (``not_implemented`` / ``error`` / ``review``) it returns that result
    unchanged and writes nothing — a deck is only ever produced from a clean,
    gate-clearing packet.

    Outputs are organized per client. Unless the caller passes ``prompts_dir`` /
    ``decks_dir`` explicitly (tests do), each is derived from the packet's short
    brand form — ``client_slug`` if given, otherwise ``company.client_short``
    from the resolved packet — as ``prompts_root/<client>/`` and
    ``decks_root/<client>/claude code/``. So one client's decks never look like
    another's iterations, and the number restarts per client.

    On ``ok`` it picks the next shared number N (``next_output_number``, scoped
    to that client's folders), prepends the pinned house-style header to the
    content prompt (``deck_renderer.prepend_house_style_brief``), writes that to
    ``prompts_dir/generated-prompt-N.txt`` (the Claude Design deliverable), and,
    when ``render`` is true, feeds that same string to the renderer and writes
    the HTML to ``decks_dir/output-N.html`` (the Claude Code deliverable). The
    renderer strips the header before sending, so both legs build from the same
    content — that is the whole point of one number. ``result["prompt"]`` is the
    saved Design string (content plus header); render fidelity is checked against
    the content prompt.

    When ``render`` is true, the render-fidelity guard (``render_guard.
    check_render_fidelity``, PRD criterion 13) runs on the rendered HTML before
    either file is written. A dropped reviewer marker always raises
    ``RenderFidelityError``; a dropped load-bearing value only raises when
    ``strict_render_fidelity=True``, otherwise it is returned in the report.
    Either way, a raise here leaves no files written and does not consume the
    number, the same as a render failure.

    Standing reviewer preferences (the feedback loop's learning half) are read
    from ``preferences_path`` and applied to both legs when ``apply_preferences``
    is true: the applicable format-only notes for this ``deck_type`` are appended
    to the Claude Code system prompt and placed inside the Claude Design header,
    so a preference a reviewer captured on a past run (e.g. "tighten the tracker
    whitespace") is carried into every future deck, on both legs identically.
    They affect formatting only and never the content prompt, so the coverage and
    render-fidelity guards are unaffected and the deterministic content guarantee
    holds. With no store file, or ``apply_preferences=False``, both legs are
    byte-identical to the no-preferences path. ``preferences_path`` is overridable
    so tests stay hermetic.

    The layout guard (``layout_guard.check_layout``) then measures the deck as it
    RENDERS, in headless Chrome — the boundary no string-level guard can reach,
    since ``overflow:hidden`` hides text without removing it from the HTML. Its
    report lands on ``result["layout"]``. Report-only by default (a layout defect
    loses nothing from the record, and the reviewer needs the rendered slide in
    front of them to fix it); ``strict_layout=True`` raises ``LayoutError``
    instead, and ``check_deck_layout=False`` skips the measurement entirely. With
    no browser installed the guard reports a skip rather than failing the run.

    The returned dict is the ``generate_deck_prompt`` ``ok`` payload plus
    ``number``, ``prompt_path``, and (when rendered) ``deck_path``,
    ``render_fidelity`` and ``layout`` (the guards' reports). It also carries
    ``applied_preferences`` — the list of preference notes applied to this run
    (empty when none) — so a caller (the review UI) can show what shaped the deck.
    On a rendered run it also carries ``text_gate`` — the house text gate's
    reviewer flags: the colons it judged ambiguous and the banned vocabulary it
    declined to rewrite because the term came from a packet value rather than
    from render-side voice.
    ``renderer`` and ``client`` are injection points for testing; ``model``
    overrides the renderer's default.
    """
    result = generate_deck_prompt(
        deck_type, company, project, provider, template_path=template_path,
        commercial_rows=commercial_rows, **adapter_kwargs
    )
    if result["status"] != "ok":
        return result

    # Per-client output folders, derived from the packet's short brand form
    # unless the caller pinned the leaf dirs directly. Deriving both together
    # keeps the prompt and its deck under the same client and number.
    if prompts_dir is None or decks_dir is None:
        slug = _slugify_client(client_slug or result.get("client_short", ""))
        client_decks_dir = os.path.join(decks_root, slug)
        if prompts_dir is None:
            prompts_dir = os.path.join(prompts_root, slug)
        if decks_dir is None:
            decks_dir = os.path.join(client_decks_dir, DECK_CODE_SUBDIR)
        # Create the sibling "claude design" folder too, so a fresh client's
        # structure shows both leaves even before any design PDF is dropped in.
        os.makedirs(os.path.join(client_decks_dir, DECK_DESIGN_SUBDIR), exist_ok=True)

    number = next_output_number(prompts_dir, decks_dir)

    # Standing reviewer preferences for this deck_type — the feedback loop's
    # learning half. The applicable format-only notes are rendered once into a
    # block that goes to both legs identically (header + system prompt), so a
    # past reviewer note shapes this deck. Empty (no store / no match / disabled)
    # means both legs stay byte-identical to the no-preferences path.
    pref_notes = []
    pref_block = ""
    if apply_preferences:
        applicable = applicable_preferences(deck_type, path=preferences_path)
        pref_notes = [p["note"] for p in applicable]
        pref_block = render_preferences_block(applicable)
    result["applied_preferences"] = pref_notes

    # The Claude Design deliverable is the content prompt with the pinned
    # house-style header prepended, so the human pasting it into Claude Design
    # gets the same formatting decisions the Claude Code leg carries in its
    # system prompt (deck_renderer.prepend_house_style_brief), plus the standing
    # preferences inside that header. The renderer strips that header before
    # calling the API, so the content Claude Code renders is byte-for-byte the
    # bare content prompt; render fidelity is therefore checked against the
    # content prompt, not the header. Both files are still written from one
    # string (design_prompt) under one number.
    content_prompt = result["prompt"]
    design_prompt = prepend_house_style_brief(content_prompt, deck_type, preferences=pref_block)
    result["prompt"] = design_prompt

    # Render first, before writing anything. If the API call fails, the run
    # writes no files and does not consume the number — so a failed render can't
    # leave an orphaned prompt with no matching deck, and the next run reuses N.
    html = None
    if render:
        render_kwargs = {"client": client, "deck_type": deck_type, "preferences": pref_block}
        if model is not None:
            render_kwargs["model"] = model
        html = renderer(design_prompt, **render_kwargs)
        result["render_fidelity"] = check_render_fidelity(
            content_prompt, html, strict=strict_render_fidelity, deck_type=deck_type
        )
        # The guard has confirmed every reviewer marker survived the render; now
        # take the "(unconfirmed, see gaps)" flags back OFF the deck so the saved
        # artifact is clean. Gap flags are an internal review-checklist concern
        # (the UI lists them separately), never client-facing deck copy. Done
        # here, post-guard and pre-save, so the guarantee holds and the file is
        # written clean. `[MISSING: ...]` markers are left in place.
        html = strip_gap_flags(html)

        # The commercial slide's blocks, rewritten from the placeholder map
        # (`commercial_blocks.normalise`). After the fidelity guard, which has
        # checked the model's render against the prompt, and after the flag
        # strip, so the blocks are written clean. Report-only: a block the
        # render drew no container for is left as drawn and named here.
        if deck_type == "proposal":
            html, result["commercial_blocks"] = normalise_commercial(
                html, result.get("commercial_blocks_data") or {})

        # Slide 2's bullet type, where a panel is set below the house size.
        # Applied here, after the flag strip and before the display-text and
        # layout guards, so both guards measure the deck as it will be opened.
        # Only panels the fitter stepped down produce a rule; a deck where
        # nothing was resized comes out byte-identical to before.
        # `.get` on both, because a status run and an early-return result carry
        # no placeholder map at all, and a proposal that fitted nothing carries
        # no per-opportunity report. Either way there is nothing to resize.
        html = apply_panel_type(
            html,
            (result.get("placeholder_map") or {}).get("_panel_fit_per_opportunity"),
        )

        # Display-text guard: the deck's own markup must never be readable as
        # words on a slide. Checked on the artifact that gets written, after the
        # flag strip, so what is guarded is exactly what a reviewer opens. Always
        # hard: a printed `<span ...>` is unambiguous, so there is nothing for a
        # human to weigh and nothing worth writing the file for.
        check_display_text(html, where=f"the {deck_type} deck")

        # House text gate reporting. The gate itself already ran inside the
        # render leg (`deck_renderer._apply_house_text_gate`), and it is
        # idempotent, so re-inspecting the finished deck rewrites nothing and
        # returns exactly what the gate handed to the human: the ambiguous colons
        # and the banned vocabulary it declined to rewrite because the value came
        # from the packet. The reviewer sees these; the gate never guesses.
        result["text_gate"] = {
            "flags": [flag._asdict() for flag in review_flags(html)],
        }

        # Layout guard: measure the deck as RENDERED, which is the one thing no
        # string-level guard can do. A clipped label is still in the HTML —
        # overflow:hidden hides text at paint time, it does not remove it — so the
        # coverage and fidelity guards both report clean on a slide that visibly
        # reads "Switch pro". Measured on the post-strip HTML because that is the
        # artifact that gets written and reviewed.
        #
        # Report-only by default, and deliberately so: a layout defect loses
        # nothing from the record, the fix is CSS or shorter copy, and the human
        # has to SEE the slide to judge it. Raising here would burn the render and
        # leave nothing to look at. The report rides on the result and the review
        # UI shows it loudly; `strict_layout=True` turns it into a hard gate for
        # the eval scripts.
        if check_deck_layout:
            result["layout"] = check_layout(html, strict=strict_layout)

    os.makedirs(prompts_dir, exist_ok=True)
    prompt_path = os.path.join(prompts_dir, f"generated-prompt-{number}.txt")
    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write(design_prompt)
    result["number"] = number
    result["prompt_path"] = prompt_path

    if render:
        # `deck_path_override` is how a RE-RENDER lands inside an existing
        # deck's chain instead of starting a new one (item 16's toggle rider):
        # the caller has already worked out which revision file this render
        # belongs to, and a second `output-N.html` beside it would be the fork
        # the rider exists to avoid. The prompt still takes its own number, so
        # every render keeps its own prompt on disk.
        deck_path = deck_path_override or os.path.join(
            decks_dir, f"output-{number}.html")
        os.makedirs(os.path.dirname(os.path.abspath(deck_path)) or decks_dir,
                    exist_ok=True)
        with open(deck_path, "w", encoding="utf-8") as f:
            f.write(html)
        result["deck_path"] = deck_path

    return result
