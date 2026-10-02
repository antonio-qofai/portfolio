"""Review UI — a thin internal tool over the deck pipeline, with the reviewer
feedback loop built in.

This is the human-in-the-loop surface the PRD and NEXT-STEPS item 8 call for, and
the delivery vehicle for the feedback loop (item 3). It does not reimplement any
deck logic; it drives ``deck_generator.generate_and_save_deck`` and reads/writes
the standing-preferences store. It is internal only — the agent never ships to a
client, a human reviews here first.

The surface is a single page with three tabs, matching the Claude Design mockup
(Antonio, 2026-07-21):

- Generate — pick a sample fixture (or override company / project / packet /
  check_in_date) and a deck_type, then run the pipeline.
- Preferences — the standing format-only refinements applied automatically to
  every future run.
- Result — the outcome of the last run. On a clean run: any layout problems the
  layout guard measured on the rendered deck (shown first, since a clipped label
  is the one defect a reviewer cannot find by reading the HTML), the rendered deck
  (scaled to fit, no sideways scroll), a free-text edit box (a Claude call turns
  a plain-language request into exact edits), the flagged-claims review checklist
  (flags never print on the deck; resolving one does NOT re-render it), the
  standing-preference capture form, and the saved Claude Design prompt with its
  download. On a non-render outcome: the gate-failure payload or the error
  envelope, never a deck.

Rendering and the free-text edit both hit the Anthropic API and need
ANTHROPIC_API_KEY (loaded from a repo ``.env`` if present). With no key, the UI
still runs the full deterministic pipeline and shows the Design prompt and any
non-render outcome; only the API legs are disabled, and the page says so.

Run it:

    pip install flask            # UI-only dependency; the pipeline needs none
    python3 ui/app.py            # then open http://127.0.0.1:5000

Nothing here is hardcoded to one client: fixtures live in ui/fixtures.json, and
every client-specific value flows from the packet or the reviewer's input.
"""

import hashlib
import json
import os
import re
import secrets
import sys
import threading
import time
from datetime import date, datetime

from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    make_response,
    redirect,
    render_template_string,
    request,
    send_file,
    url_for,
)
from markupsafe import escape
from werkzeug.middleware.proxy_fix import ProxyFix

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "src"))


def _load_dotenv(path):
    """Populate os.environ from a KEY=VALUE .env file, without a dependency.

    Only sets a key not already in the environment; skips blanks and comments;
    strips one layer of surrounding quotes. Mirrors the scripts' helper so the UI
    picks up ANTHROPIC_API_KEY the same way a sample render does.
    """
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_dotenv(os.path.join(_ROOT, ".env"))

from data_source_adapter import FixtureProvider
from design_export_prompt import assemble_design_export_prompt
from store_dir_resolver import resolve_store_dir
from template_loader import fill_expectations, load_template
from commercial_defaults import DefaultsUnavailable, load_defaults
from commercial_terms import (
    TermsRejected,
    clean_cases,
    clean_rows,
    form_state as commercial_form_state,
)
from deck_generator import (
    DECK_CODE_SUBDIR,
    DECK_TYPE_TEMPLATE_PATHS,
    DEFAULT_DECKS_ROOT,
    DEFAULT_PROMPTS_ROOT,
    generate_and_save_deck,
)
from deck_store import (
    delete_decks as delete_decks_from_store,
    get_deck,
    list_decks as list_decks_from_store,
    save_deck as save_deck_to_store,
)
from bullet_toggles import (
    DEFAULT_TOGGLES_PATH,
    OFF as BULLET_OFF,
    ON as BULLET_ON,
    bullet_key,
    clear_toggle as clear_bullet_toggle,
    overrides_for as bullet_overrides_for,
    set_toggle as set_bullet_toggle,
    source_key as bullet_source_key,
)
from gap_decisions import (
    CONFIRMED,
    DEFAULT_DECISIONS_PATH,
    decisions_by_field,
    reopen as reopen_gap,
    resolve as resolve_gap_decision,
    send_back as send_back_gap,
)
import panel_fit

from gap_resolver import deck_shape, describe_gap, list_gaps
from html_edit_interpreter import interpret_edit
from html_edit_layer import (
    AMBIGUOUS_SOURCE,
    BulletDoesNotFit,
    EditNotApplicable,
    apply_edit_and_save,
    apply_edits_and_save,
    base_stem,
    clear_marker_edits,
    current_revision_path,
    edit_log_path,
    list_missing_markers,
    list_progress_items,
    list_slide_bullets,
    locate_bullet_list,
    load_edit_log,
    next_revision_path,
    set_bullet_presence_and_save,
    read_commercial_regions,
    read_commercial_terms,
    set_commercial_defaults_and_save,
    set_commercial_terms_and_save,
    slide_count,
    toggle_progress_item_and_save,
    undo_last_edit,
)
# Measuring a candidate deck before it is written. The studio only ever read
# the layout report the pipeline produced; a bullet switched ON is the first
# thing the STUDIO creates that can clip a panel, so it has to measure its own
# work. About 0.8s on a six-slide deck. See `_overflow_verifier`.
from bullet_type import read_panel_type, set_panel_type
from layout_guard import check_layout
from missing_values import (
    GROUP_ORDER,
    # Aliased: the module-level SOURCE_FIXTURE / SOURCE_LIVE below name a data
    # source, which is a different thing entirely from where a field's value was
    # meant to come from.
    REVIEWER as FILL_REVIEWER,
    SENSITIVE as FILL_SENSITIVE,
    SOURCE as FILL_SOURCE,
    classify_marker,
    group_missing_markers,
    marker_counts,
    merge_expectations,
)
from preference_store import (
    DEFAULT_STORE_PATH,
    VALID_SCOPES,
    add_preference,
    applicable_preferences,
    delete_preference,
    load_store,
    seed_store_if_absent,
    set_active,
)
from coverage_guard import CoverageError
from packet_consistency import ConsistencyError
from render_guard import RenderFidelityError
# Module-level rather than lazy, unlike the SDK and the MCP client: `model_call`
# imports nothing but `logging` and `time` at module scope (its own `anthropic`
# and `httpx` imports sit inside `bounded_client`), and `_pipeline_result`
# catches `ModelCallError` by name in an `except` clause, which is evaluated
# when the exception is raised rather than when the module loads.
import model_call
# Same reasoning, one module along: `deck_renderer` imports the SDK lazily inside
# `render_deck_html`, and `_pipeline_result` catches `RenderTruncated` by name in
# an `except` clause, which is evaluated when the exception is raised.
from deck_renderer import RenderTruncated

app = Flask(__name__)

# Behind the GTM portal this app is not served from its own root: the portal
# proxies it under a path like /apps/project-status-deck-generator/ and says so
# in X-Forwarded-Prefix. Without this, every url_for link and redirect would come
# out relative to this service's own root and walk the reviewer straight out of
# the portal's Google login. ProxyFix folds the forwarded prefix, host and scheme
# into the WSGI environment so url_for builds portal-correct URLs. Direct (local
# or Railway) requests send no such headers, so nothing changes off the portal.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# HTTP Basic gate for hosted runs. The deck HTML carries client financials, so a
# hosted URL cannot be open. Credentials come from the environment (UI_USER /
# UI_PASS) — never a literal here, and never per-client. With UI_PASS unset the
# gate is off, so a local `python3 ui/app.py` needs no setup; set UI_PASS on the
# hosted service to switch it on. One shared credential is deliberate: this
# authorizes access, it does not identify the reviewer (the preference store
# takes its author from the form, not from here).
_UI_USER = os.environ.get("UI_USER", "qofai")
_UI_PASS = os.environ.get("UI_PASS")

# Fail closed in the cloud. Off-by-default is right for local dev and wrong for a
# hosted service: a dropped or forgotten UI_PASS would otherwise serve client
# financials to anyone with the URL. The platform always sets
# RAILWAY_ENVIRONMENT, so a missing password takes the hosted studio offline
# instead of opening it up. Local runs never see this variable.
_ON_RAILWAY = bool(os.environ.get("RAILWAY_ENVIRONMENT"))


def _unauthorized():
    """A fresh 401 per request — a shared Response object would carry any
    mutation (an after_request header, a computed Content-Length) into the next
    caller's response."""
    return Response(
        "Login required",
        401,
        {"WWW-Authenticate": 'Basic realm="QofAI deck studio"'},
    )


@app.before_request
def _require_login():
    # Railway probes /health to decide whether the service is up, and the probe
    # carries no credentials. It has to clear the gate before any other check or
    # a perfectly healthy deploy reads as down. Exempting it leaks nothing: the
    # route answers liveness only, never client data.
    if request.path == "/health":
        return None
    if _ON_RAILWAY and not _UI_PASS:
        return Response("Not configured: UI_PASS is unset.", 503)
    if not _UI_PASS:
        return None  # no password configured: local dev, gate off
    auth = request.authorization
    if auth is None or auth.type != "basic" or not auth.username or not auth.password:
        return _unauthorized()
    # compare_digest needs bytes: a non-ASCII username or password raises
    # TypeError on str input, which would surface as a 500 instead of a 401.
    user_ok = secrets.compare_digest(
        auth.username.encode("utf-8"), _UI_USER.encode("utf-8")
    )
    pass_ok = secrets.compare_digest(
        auth.password.encode("utf-8"), _UI_PASS.encode("utf-8")
    )
    if not (user_ok and pass_ok):
        return _unauthorized()
    return None


FIXTURES_PATH = os.path.join(_HERE, "fixtures.json")

# Where the app reads and writes its own output. A hosted service runs from a
# fresh copy of the code on every deploy, so anything written under the repo is
# discarded on the next push — these three point at a persistent volume there
# (mounted at /data). Module globals, not the imported constants directly, so a
# test can point them at a temp root and drive a round trip hermetically. With
# no environment variables set, every one falls back to today's repo path and
# local behaviour is unchanged.
#
# DECKS_ROOT is also the root every served/edited deck must resolve inside, the
# guard that keeps the preview / download / edit routes off arbitrary files.
DECKS_ROOT = os.environ.get("DECKS_ROOT", DEFAULT_DECKS_ROOT)
PROMPTS_ROOT = os.environ.get("PROMPTS_ROOT", DEFAULT_PROMPTS_ROOT)
STORE_PATH = os.environ.get("PREF_STORE_PATH", DEFAULT_STORE_PATH)
# Which flagged claims a reviewer has confirmed. Reviewer state, kept out of the
# data packet — the packet is the data source's record of what it sent and nothing
# here may edit one. Same volume treatment as the preference store on a hosted
# service, since a store written under the repo is discarded on the next deploy.
DECISIONS_PATH = os.environ.get("GAP_DECISIONS_PATH", DEFAULT_DECISIONS_PATH)
# Which slide 2 bullets a reviewer switched on or off. Reviewer state on exactly
# the same terms as the decisions above: never written into the packet, and
# redirected onto the mounted volume on a hosted service, where a store under
# the repo is discarded by the next deploy.
TOGGLES_PATH = os.environ.get("BULLET_TOGGLES_PATH", DEFAULT_TOGGLES_PATH)

# Seed a fresh store on boot so the preferences already captured apply to the
# first hosted render, instead of the team having to recapture them. A no-op
# locally (source and destination are the same file) and a no-op on every boot
# after the first, so a store the team has since edited is never overwritten.
# A failure here must not take the studio down: the studio still runs with an
# empty store, and the reason goes to the log where a deploy shows it.
try:
    if seed_store_if_absent(STORE_PATH):
        print(f"[boot] seeded preference store at {STORE_PATH}", file=sys.stderr)
except Exception as exc:  # pragma: no cover - depends on the mounted volume
    print(f"[boot] could not seed preference store: {exc!r}", file=sys.stderr)


def _no_sleep(_):
    pass


# Which provider a run goes through (E9d). The fixture path reads a frozen packet
# off disk; the live path resolves the company and project against the platform
# and assembles the packet itself. Fixture is the default, so nothing about an
# existing run changes.
SOURCE_FIXTURE = "fixture"
SOURCE_LIVE = "live"

# The opportunity dropdown's empty first option. Deliberately not an invitation:
# the previous wording ("default: first published opportunity with a paper") read
# as a recommendation, and a live run that takes it is now refused. Both the form
# and the JS that rewrites the list on every refresh render this one string, so
# the reviewer never sees two different descriptions of the same option.
OPPORTUNITY_PLACEHOLDER = "Select the opportunity this deck is for"


# ---- Attachments (item 14) ------------------------------------------------
#
# The documents a deck is written from. The reviewer attaches one or more, the
# first becomes the base document and the published opportunity paper drops to a
# supporting source (`src/base_document.py`).
#
# THE TWO CEILINGS LIVE HERE AND NOT IN `document_text`, on purpose. That module
# extracts whatever it is given and says so in its own docstring: the answer to a
# file that is too large is a form rejection with a message, and the form is
# here. Both are overridable by environment variable the way every other path and
# limit in this module is, so the hosted service can be tightened without a
# deploy of new code.
#
# 8 MB PER FILE, and what that is based on. The published papers this pipeline
# was measured against carry 36KB to 48KB of TEXT (`src/extraction_cache.py`),
# and a PRD is a document of that order: execution detail, pricing, timelines,
# deliverable dates. 8 MB of PDF or docx comfortably holds a text-heavy hundred
# pages, which is already several times any paper on the corpus. What it stops is
# the class of file that is large because it is not text: a scan of a printed
# contract, an image-heavy design export. Those are exactly the files that come
# back `E_NO_TEXT`, so the ceiling refuses them faster and with a better sentence
# than the extractor would.
#
# 4 FILES PER RUN. Casey's ask on 2026-09-04 was a PRD, with contracts in reach
# after it, so four is a base document plus three supporting sources, which is
# more precedence than anyone has asked for. It also bounds the run: four
# deterministic parse passes, and four files' bytes held in memory on the worker
# thread while it works.
#
# Antonio's call on both numbers. They are stated here so changing them is one
# line and an argument rather than a search through a branch.
ATTACHMENT_FIELD = "documents"
MAX_ATTACHMENT_BYTES = int(os.environ.get("MAX_ATTACHMENT_BYTES", 8 * 1024 * 1024))
MAX_ATTACHMENTS = int(os.environ.get("MAX_ATTACHMENTS", 4))

# The backstop under the two ceilings above, and a different kind of limit. They
# refuse a file the studio has already read; this refuses a REQUEST before the
# studio reads it, which is the only protection against a body nobody should have
# sent. Sized off the two so it can never be the binding limit on a legitimate
# run, and generously above anything else this app posts (the largest is a deck's
# own HTML, which is measured in hundreds of kilobytes).
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get(
    "MAX_CONTENT_LENGTH",
    MAX_ATTACHMENT_BYTES * MAX_ATTACHMENTS + 8 * 1024 * 1024,
))

# Why a file is too large to read, and what to do about it. Written once because
# two different ceilings refuse the same file for the same reason: the per-file
# one below, which refuses a file the studio has already read, and the request
# ceiling above, which refuses a body the studio never got to look at. Two
# accounts of one refusal would be two things for a reviewer to reconcile.
ATTACHMENT_TOO_LARGE_REMEDIATION = (
    "A document this size is usually large because it is not text: a scan, or "
    "an export carrying its images. Only text can be read out of it, so attach "
    "a version whose text can be selected, or split it and attach the part the "
    "deck is about."
)


@app.errorhandler(413)
def _request_too_large(_error):
    """The studio's own refusal for a body Werkzeug rejected before any route ran.

    THE DEFECT THIS CLOSES. `MAX_CONTENT_LENGTH` is a limit on the REQUEST, so a
    single attachment over it is refused before `_read_attachments` runs and
    before `run()` is entered at all. What the reviewer got was Werkzeug's own
    bare "413 Request Entity Too Large" page: no studio around it, no remediation
    on it, and no way on from it. A scanned contract over the request ceiling is
    the file that produces it, and a first screen that refuses without explaining
    has already cost this project two of the founder's sessions.

    IT MAY NOT TOUCH THE BODY, AND THAT IS THE TRAP IN WRITING ONE. `request.form`
    and `request.files` parse the body, which is the thing that raised this, so
    either one here would raise 413 inside the 413 handler and turn a refusal
    into a 500. Which means nothing the reviewer typed can be recovered, so the
    page says so plainly rather than rendering a Generate tab that looks like it
    kept their inputs.

    The sentence is the per-file ceiling's own sentence
    (`ATTACHMENT_TOO_LARGE_REMEDIATION`), because it is the same file with the
    same fix; only which ceiling caught it differs. The status stays 413: the
    studio answers with its own page, not with a different outcome.
    """
    return render_studio("result", result_ctx=_build_result_ctx(
        result=_attachment_refusal(
            "request_too_large",
            "The whole request was larger than "
            f"{_megabytes(app.config['MAX_CONTENT_LENGTH'])} and was refused "
            "before any of it was read, so the studio cannot say which file it "
            "was. One attached document over "
            f"{_megabytes(MAX_ATTACHMENT_BYTES)} is what usually does it.",
            ATTACHMENT_TOO_LARGE_REMEDIATION + " Nothing ran, nothing was saved "
            "and no API call was spent. The form itself did not survive the "
            "refusal — the request carrying it is what was turned away — so the "
            "Generate tab needs filling in again before the re-run.",
        ),
        status="error",
    )), 413


def _read_attachments(files, *, live):
    """The reviewer's attached documents, read HERE, in the request thread.

    THIS IS THE TRAP THIS FUNCTION EXISTS FOR. A run has executed on a worker
    thread since 2026-08-26: `POST /run` answers immediately with a run id and
    the page polls. `request.files` is a handle on the live request, so the
    worker thread cannot read one, and the failure mode of getting this wrong is
    the worst shape this build has seen: a run that works locally whenever the
    request happens to outlive the read, and fails on the service. So the bytes
    are read into memory before the thread is started, and what crosses the
    boundary is `base_document.Upload` objects with nothing behind them.

    Returns `(uploads, refusal)`. `refusal` is a result envelope in the studio's
    own shape or None, and every one of them costs no pipeline call, no thread
    and no API spend, the same as a missing packet or an unpicked opportunity.

    THE ORDER IS THE PRECEDENCE ORDER. `getlist` hands them back in the order the
    form posted them and that order is preserved all the way to
    `base_document.precedence`, where the first is the base document. What the
    studio cannot control is the order a browser's own file picker reports a
    multiple selection in, so the panel states which document was treated as the
    base after the run rather than asking the reviewer to trust the form.
    """
    from base_document import Upload
    from document_text import ACCEPTED_EXTENSIONS

    attached = [
        storage for storage in files.getlist(ATTACHMENT_FIELD)
        if storage is not None and (storage.filename or "").strip()
    ]
    if not attached:
        return (), None
    if not live:
        # The fixture provider replays a frozen packet and refuses an attachment
        # rather than dropping one (`data_source_adapter.FixtureProvider`). Said
        # here so the reviewer reads a sentence about the data source rather than
        # a ValueError out of a worker thread.
        return (), _attachment_refusal(
            "attachment_needs_live_source",
            f"{len(attached)} document(s) were attached to a fixture run.",
            "A fixture run replays a frozen packet, so there is nothing for a "
            "document to be the base of. Switch the data source to live, or "
            "remove the attachment and run the fixture as it is.",
        )
    if len(attached) > MAX_ATTACHMENTS:
        return (), _attachment_refusal(
            "too_many_attachments",
            f"{len(attached)} documents were attached and this accepts "
            f"{MAX_ATTACHMENTS}.",
            f"Attach at most {MAX_ATTACHMENTS}: the first is the base document "
            "the deck is written from and the rest answer what it does not. If "
            "more than that are genuinely needed, the ceiling is one constant "
            "in the studio and a conversation, not a limit of the pipeline.",
        )

    uploads = []
    for storage in attached:
        data = storage.read()
        if len(data) > MAX_ATTACHMENT_BYTES:
            return (), _attachment_refusal(
                "attachment_too_large",
                f"{storage.filename!r} is {_megabytes(len(data))} and this "
                f"accepts {_megabytes(MAX_ATTACHMENT_BYTES)} per file.",
                ATTACHMENT_TOO_LARGE_REMEDIATION,
            )
        uploads.append(Upload(filename=storage.filename, data=data))
    return tuple(uploads), None


def _attachment_refusal(code, message, remediation):
    """One rejected attachment, in the shape the Result panel already renders."""
    return {"status": "error", "code": code, "message": message,
            "remediation": remediation, "details": ""}


def _megabytes(count):
    return f"{count / (1024 * 1024):.1f} MB"


def _accepted_attachment_types():
    """The accept list for the file input, read from the module that decides it.

    `document_text.ACCEPTED_EXTENSIONS` is exported for exactly this, so the
    markup and the validation cannot drift apart: an extension added there
    appears on the form with no edit here.
    """
    from document_text import ACCEPTED_EXTENSIONS

    return ",".join(ACCEPTED_EXTENSIONS)


def _live_provider():
    """The live seam object, with its MCP client built here rather than imported.

    A function-level import on purpose: `qofai_mcp_client` pulls `httpx`, and the
    hosted service has not been confirmed to have it. Building the client inside
    the one branch that needs it keeps `import httpx` off the hosted path's
    module-level imports until E10 lands the dependency and the code together.
    The endpoint and the key's environment variable name are that client's own
    parameters, defaulted in one place, so nothing is named here.

    All three LLM passes are attached here (E11 Stage 2g; the ranker 2026-08-19),
    and this is the only place they are turned on for a reviewer. Stages 2 through
    2c built the second extraction pass and the writing pass behind
    `LiveProposalProvider`'s own `extractor` / `writer` seam, where both default
    to off; until this function handed them over, every one of those stages was
    switched off in the one place a reviewer clicks, and the studio scored a deck
    the parsers alone could reach. The ranking pass is attached the same way and
    is listed here so it cannot repeat that: a pass nothing calls is a pass that
    does not exist.

    With no Anthropic key configured the provider is built with neither, and that
    is a configuration state rather than a failure: the first pass alone is a
    complete deck, so the studio keeps working exactly as it did, no key is
    needed for a fixture run, and nothing here returns the error envelope
    `_live_provider_or_error` returns for a missing MCP key. Without the MCP key
    there is no data at all; without this one there is still a deck.

    `_has_api_key` is the studio's existing key predicate, the same one four
    render call sites already read, so there is one way to ask rather than two.
    The factory imports sit inside the branch that needs them for the same reason
    the client's does, keeping the Anthropic SDK off the hosted path's
    module-level imports; no factory constructs a model client, so building the
    provider still reaches nothing.
    """
    from live_proposal_provider import LiveProposalProvider

    if not _has_api_key():
        return LiveProposalProvider(_live_client())

    import second_pass

    import bullet_ranking

    return LiveProposalProvider(
        _live_client(),
        extractor=second_pass.make_extractor(),
        writer=second_pass.make_writer(),
        ranker=bullet_ranking.make_ranker(),
    )


def _live_client():
    """The live MCP client alone, for a route that only needs to list
    opportunities (E9e) rather than run the whole seam. Same function-level
    import, for the same reason as `_live_provider`."""
    from qofai_mcp_client import QofaiMcpClient

    return QofaiMcpClient()


def _live_provider_or_error():
    """The live seam object, or a studio error envelope in its place.

    Building the client is what raises, and it happens before the pipeline's own
    try block: with no key configured, `QofaiMcpClient.__init__` raises out of
    `_live_provider()` and the reviewer gets a 500 they cannot read. That is a
    configuration failure caught before any render, so it is surfaced the way
    `CoverageError` is, as an error envelope naming what is wrong and what to do.
    The exception's own message names the environment variable it looked in, so
    no variable name is written here.

    Returns `(provider, None)` or `(None, error_result)`.
    """
    from qofai_mcp_client import McpMissingKeyError

    try:
        return _live_provider(), None
    except McpMissingKeyError as exc:
        return None, {
            "status": "error",
            "code": "data_source_not_configured",
            "message": str(exc),
            "remediation": (
                "Nothing was rendered and no API call was spent. The live data "
                "source needs its server key set in this environment before a "
                "live run can reach the platform; a fixture run needs no key. "
                "Set the variable named above and re-run."
            ),
            "details": "",
        }


def _load_fixtures():
    with open(FIXTURES_PATH, encoding="utf-8") as f:
        return json.load(f).get("fixtures", [])


def _fixture_by_id(fixture_id):
    for fx in _load_fixtures():
        if fx["id"] == fixture_id:
            return fx
    return None


def _has_api_key():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _save_deck_to_history(deck_path, *, deck_type, company, project, packet="",
                          check_in_date=""):
    """Write the deck as it currently stands into the durable store.

    The only path into the store (prompt B3a). A render does not persist on its
    own: entry into deck history is the reviewer's decision, so a deck nobody
    settled on stays a working file in the session and is never a row.

    "As it currently stands" is the load-bearing half. The HTML is read from the
    revision on screen rather than from the original render, and the edit chain,
    the flags and their resolutions ride along in ``details`` — because the row
    is all that is left on a machine where the deck files are gone, which is what
    every redeploy of the hosted service produces. A row carrying the deck as it
    was born would come back disagreeing with the deck the reviewer chose to keep.

    Everything else the Decks tab needs to reconstruct the full Result view later
    (the packet, the run's own company/project spelling and check-in date, the
    saved Design prompt's path, the applied preferences, the fidelity report, and
    the deck's own filesystem path, used if it is still there when the row is
    opened) rides along the same way.

    Returns ``(notice, notice_bad)``. The durability warning lands here rather
    than on the render, where it tells the reviewer that the thing they just
    chose to keep will not survive a redeploy, and it is a visible notice rather
    than a log line because a silent miss reads exactly like a working history.
    """
    resolution = resolve_store_dir()
    outcome = _carry_run_outcome(deck_path)
    gaps, resolved = _gaps_for(packet, deck_type)
    try:
        with open(deck_path, encoding="utf-8") as f:
            html = f.read()
        details = {
            "packet": packet,
            "raw_company": company,
            "raw_project": project,
            "check_in_date": check_in_date,
            "prompt_rel": outcome.get("prompt_rel", ""),
            "applied_preferences": outcome.get("applied") or [],
            "render_fidelity": (outcome.get("result") or {}).get("render_fidelity"),
            # Which of slide 2's bullets the panels held, and who chose the order.
            # A stored row is all that is left once the deck files are gone, and a
            # reviewer opening it then has no other way to learn that the slide
            # shows four of nine.
            "bullet_selection": (outcome.get("result") or {}).get("bullet_selection"),
            # The same record per opportunity, which is what the switches are
            # grouped by. A deck with one opportunity has one entry here and the
            # card reads exactly as it did.
            "bullet_selection_per_opportunity":
                (outcome.get("result") or {}).get("bullet_selection_per_opportunity"),
            # What the deck was built from (item 14, step 5). One record per
            # attachment: the extracted TEXT, plus the filename, the kind, the
            # size and a SHA-256 of the original bytes, in precedence order. Not
            # the bytes — the model read the text, so the text is the honest
            # record of what produced a slide, and the hash is what says two
            # decks came from the same document or that a changed version
            # arrived under an unchanged name (Antonio, 2026-09-07).
            #
            # An empty list is the truthful record of a run with no attachment,
            # and a row saved before today simply has no key, which reads the
            # same way as every other field added to `details` after the fact.
            "attachments": (outcome.get("result") or {}).get("attachments") or [],
            # WHICH FRAMING LINES WERE WRITTEN AND WHICH REFUSED (item 24). Kept
            # with the deck for the reason the attachments are: the run that
            # made it is gone the moment the process ends, and "the answer
            # survives the process" is the whole of this item. A row saved
            # before today has no key, which reads as no ledger and renders
            # nothing, exactly as `attachments` did when it was added.
            "writing_ledger": (outcome.get("result") or {}).get("writing_ledger"),
            # Part A4, kept with the deck so a reopened deck still says which of
            # its flags the document states.
            "flag_audit": (outcome.get("result") or {}).get("flag_audit") or [],
            "deck_path": deck_path,
            "edits": load_edit_log(deck_path),
            "gaps": gaps,
            "resolved": resolved,
        }
        save_deck_to_store(deck_type, company, project, html, is_final=True,
                           details=details, store_path=resolution.store_path)
    except Exception as exc:
        return (
            f"Not saved to deck history: the store at {resolution.directory} "
            f"could not be written ({exc}). The deck itself is untouched on the "
            "filesystem.", True,
        )
    if not resolution.durable:
        checked = ", ".join(resolution.checked)
        return (
            f"Saved, but deck history is not persisting: the store is not on "
            f"durable storage (checked {checked}), so it is wiped on every "
            "deploy.", True,
        )
    return (
        f"Saved to deck history as the current {deck_type or 'deck'} for "
        f"{company or '(unnamed)'}. Saving again updates this row rather than "
        "adding another.", False,
    )


def _safe_within(root, path):
    """True when ``path`` resolves to a file inside ``root`` — the guard that
    keeps the preview/download routes from serving arbitrary files."""
    root_real = os.path.realpath(root)
    path_real = os.path.realpath(path)
    return path_real == root_real or path_real.startswith(root_real + os.sep)


# A row opened from the Decks tab needs a `deck_path`-shaped identity even when
# its own file is gone (a redeploy wipes the filesystem the database survives),
# because every route and template that serves, edits, or captions a deck keys
# off that one string. This sentinel is that identity for the database's copy.
_DB_DECK_PREFIX = "db-deck:"


def _db_deck_id(path):
    """The database id a `db-deck:` sentinel names, or ``None`` for anything
    else (including a real filesystem path, which never carries the prefix)."""
    if path and path.startswith(_DB_DECK_PREFIX):
        try:
            return int(path[len(_DB_DECK_PREFIX):])
        except ValueError:
            return None
    return None


def _deck_path_exists(path):
    """True for a real file inside ``DECKS_ROOT``, or a `db-deck:` sentinel
    naming a row still in the store — the two shapes ``deck_path`` can take now
    that a deck can be opened from the database with no file behind it."""
    db_id = _db_deck_id(path)
    if db_id is not None:
        try:
            return get_deck(db_id) is not None
        except Exception:
            return False
    return bool(path) and _safe_within(DECKS_ROOT, path) and os.path.isfile(path)


# ---- Slide 2 bullet switches (item 16's toggle rider) ---------------------

# What a bullet's switch can be in. `auto` is not a third setting a reviewer
# chooses between: it is the absence of a decision, and it reads differently
# from `off` on purpose. Off is a reviewer saying the slide is better without
# this line; auto is nobody having said anything, so the fitter decides.
SWITCH_AUTO = "auto"


def _toggle_scope(packet="", attachments=None, uploads=()):
    """What this run's bullet switches are remembered against.

    THE UPLOADED PRD WINS, by content hash (Antonio, 2026-09-20). A live run
    posts no packet path at all, so keying on one is what left the switches
    unreachable for the life of the feature; and the PRD is the only source a
    deck is written from once `base_document.precedence` has dropped the paper,
    so it is the honest thing to hang a reviewer's decision on.

    Re-upload an edited PRD and the hash moves, so the switches reset to auto.
    That is the intended reading: the reviewer approved the bullets they saw,
    and a document that has changed states different ones.

    Three sources, in the order they become available across a run. `uploads`
    are the raw files, which is all the render path holds before the pipeline
    has built anything; `attachments` are the records the pipeline produced,
    which is what the Result panel and the saved row hold; and `packet` is the
    fixture path, which still keys a frozen-packet run exactly as it always did.
    """
    for upload in uploads or ():
        data = getattr(upload, "data", None)
        if data:
            return bullet_source_key(hashlib.sha256(data).hexdigest())
    for record in attachments or ():
        digest = (record or {}).get("sha256") or ""
        if digest:
            return bullet_source_key(digest)
    return packet


def _deck_bullets_now(deck_path, opportunity, role):
    """The bullets the DECK currently shows for one panel, or None.

    THE DECK IS THE AUTHORITY, not the fitter's record (2026-09-22). A switch
    edits the deck now, so `bullet_selection.kept` describes what the ORIGINAL
    render put on the slide and stops being true the moment anyone flips
    anything. Reading the switch's position out of that record instead of out
    of the file is how the switch and the slide drift apart, and the switch is
    the one that ends up lying.

    None means "could not read it", which is the signal to fall back to the
    fitter's record: no deck on screen, or a deck whose slide 2 came back in a
    shape `locate_bullet_list` does not recognise.
    """
    if not deck_path:
        return None
    try:
        current = current_revision_path(deck_path)
        with open(current, encoding="utf-8") as fh:
            html = fh.read()
        return {b["text"] for b in list_slide_bullets(
            html, opportunity=opportunity, role=role)}
    except (EditNotApplicable, OSError):
        return None


def _switch_blocks(selection, per_opportunity, packet, deck_path=""):
    """The bullet switches for one deck, grouped by opportunity.

    Each block carries the panels of one slide 2, each panel every bullet the
    packet gave it, and each bullet its switch state plus whether switching it
    ON is possible at all. That last question is geometric and is answered here
    rather than in the template, because answering it means re-running the
    fitter: a bullet fits if the panel still holds every switched-on bullet
    INCLUDING this one at the smallest readable size, and that depends on how
    long the other switched-on bullets are, so it moves with every switch and
    cannot be cached per panel (Antonio, 2026-09-20: the limit is the room, not
    a count).
    """
    blocks = per_opportunity or ([dict(selection, index=0, label="")]
                                 if selection else [])
    if not blocks:
        return []
    # The scope the switch forms filed under, forwarded by the form beside
    # them. Falls back to the packet path for a frozen-packet run. Named for
    # the re-render it used to serve until 2026-09-22; it is just the switch's
    # scope now.
    switch_scope = (request.form.get("scope") or "").strip() or packet
    overrides = bullet_overrides_for(switch_scope, path=TOGGLES_PATH) if packet else {}
    out = []
    for block in blocks:
        index = block.get("index", 0)
        panel_views = []
        for panel in block.get("panels") or []:
            role = panel.get("role") or ""
            recorded = (overrides.get(index) or {}).get(role) or {}
            rows = (list(panel.get("kept") or [])
                    + list(panel.get("dropped") or [])
                    + list(panel.get("switched_off") or []))
            texts = [row.get("text") or "" for row in rows]
            # What the deck SHOWS, read off the current revision, with the
            # fitter's record as the fallback when the deck cannot be read.
            # See `_deck_bullets_now` for why that order and not the other.
            live = _deck_bullets_now(deck_path, index, role)
            if live is None:
                on_slide = {row.get("text") or ""
                            for row in (panel.get("kept") or [])}
            else:
                normalised = {" ".join(t.split()) for t in live}
                on_slide = {t for t in texts
                            if " ".join(t.split()) in normalised}
            forced_on = [text for text in texts
                         if recorded.get(bullet_key(text)) == BULLET_ON]
            forced_off = [text for text in texts
                          if recorded.get(bullet_key(text)) == BULLET_OFF]
            room = float(panel.get("room_px") or 0.0)
            bullets = []
            for row in rows:
                text = row.get("text") or ""
                key = bullet_key(text)
                state = recorded.get(key, SWITCH_AUTO)
                # When the deck could be read, it has already been edited to
                # match the switch, so its own contents are the answer and the
                # recorded state must not override them. The `state` clause
                # stays for the fallback case, where `on_slide` is the
                # original render's `kept` list and the switch is the only
                # thing that knows about a later decision.
                shown = (text in on_slide if live is not None
                         else (text in on_slide and state != BULLET_OFF))
                # Can this one be switched on? Ask the fitter with it added to
                # the set the reviewer has already forced. A bullet already on
                # the slide is not asked about: it is there.
                blocked = False
                if state != BULLET_ON and not shown:
                    trial = panel_fit.fit_bullets_with_overrides(
                        texts, room_px=room,
                        forced_on=forced_on + [text], forced_off=forced_off)
                    blocked = text in trial.blocked
                bullets.append({
                    "text": text,
                    "key": key,
                    "state": state,
                    "shown": shown,
                    "source_position": row.get("source_position"),
                    "blocked": blocked,
                })
            current = panel_fit.fit_bullets_with_overrides(
                texts, room_px=room, forced_on=forced_on, forced_off=forced_off)
            panel_views.append({
                "panel": panel,
                "role": role,
                "label": panel.get("label") or "",
                "bullets": bullets,
                "font_px": current.font_px,
                "room_px": round(room, 1),
                "used_px": round(current.used_px, 1),
                # A reviewer may pass the editorial five; the studio says so and
                # does not refuse it (Antonio, 2026-09-20).
                "over_cap": current.over_cap,
                "cap": panel_fit.MAX_BULLETS_PER_PANEL,
                "floor_px": panel_fit.BULLET_FONT_STEPS[-1],
                "house_px": panel_fit.BULLET_FONT,
                "switched": bool(recorded),
            })
        out.append({
            "index": index,
            "label": block.get("label") or "",
            "panels": panel_views,
        })
    return out


def _db_run_outcome(details, deck_path):
    """The fidelity report, the bullet selection, the applied preferences and the
    saved prompt for a stored deck, in the same shape `_carry_run_outcome` returns
    for a live one — read from `details` rather than the in-memory `_LAST_RESULT`,
    since a stored deck may belong to a session that is long gone. A row saved
    before a given field was recorded simply has none, which reads as absent."""
    return {
        "result": {"status": "ok",
                   "render_fidelity": details.get("render_fidelity"),
                   "bullet_selection": details.get("bullet_selection"),
                   "bullet_selection_per_opportunity":
                       details.get("bullet_selection_per_opportunity"),
                   # The documents this deck was written from, back out of the
                   # row that kept them. This is what "reopening a saved deck
                   # reaches the record" is, and it is the only account of the
                   # sources a reopened deck has: the provenance report belongs
                   # to a run and the run is long gone.
                   "attachments": details.get("attachments") or [],
                   # And the record of how the framing copy came out, back out
                   # of the same row (item 24). A deck reopened months later is
                   # exactly when "why does this headline read like a template"
                   # gets asked, and the run that could have answered it ended
                   # long ago.
                   "writing_ledger": details.get("writing_ledger"),
                   "flag_audit": details.get("flag_audit") or []},
        "status": "ok",
        "applied": details.get("applied_preferences") or [],
        "prompt_rel": details.get("prompt_rel", ""),
        "render_deck_path": deck_path,
        "guards_stale": False,
    }


# A rendered deck on disk: the original `output-N.html` or a revision
# `output-N-rK.html`. Mirrors html_edit_layer's naming, read-only here.
_DECK_FILE_ANY_RE = re.compile(
    r"^(?P<base>output-\d+)(?:-r(?P<rev>\d+))?\.html$", re.IGNORECASE
)


def list_recent_decks(decks_root=None, limit=60):
    """Every rendered deck on disk, newest first, as display rows.

    A run only ever put the deck it just made on screen, so a second run made the
    first one unreachable — the only way back was ``/deck-view`` with a
    hand-typed absolute path. On a shared studio serving several clients that is
    the difference between a tool and a demo, so the decks a reviewer already
    produced are listed and reopenable.

    Walks ``<decks_root>/<client>/<DECK_CODE_SUBDIR>/output-N[-rK].html``. The
    client comes from the folder name (which the pipeline derives from the
    packet's own short brand form), so nothing here is keyed to a client. Each row
    carries ``client``, ``name``, ``path``, ``mtime`` (epoch, for sorting),
    ``when`` (display), ``revision`` (``K`` or 0) and ``edits`` (logged edit
    count), so a reviewer can tell an edited copy from the original render.
    Returns ``[]`` when the root does not exist yet.
    """
    root = decks_root or DECKS_ROOT
    rows = []
    if not os.path.isdir(root):
        return rows
    try:
        clients = sorted(os.listdir(root))
    except OSError:
        return rows
    for client in clients:
        code_dir = os.path.join(root, client, DECK_CODE_SUBDIR)
        if not os.path.isdir(code_dir):
            continue
        try:
            names = os.listdir(code_dir)
        except OSError:
            continue
        for name in names:
            match = _DECK_FILE_ANY_RE.match(name)
            if not match:
                continue
            path = os.path.join(code_dir, name)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            rows.append({
                "client": client,
                "name": name,
                "path": path,
                "mtime": mtime,
                "when": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"),
                "number": int(match.group("base").split("-")[-1]),
                "revision": int(match.group("rev")) if match.group("rev") else 0,
                "edits": len(load_edit_log(path)),
            })
    # An original also carries how many revisions sit beside it (`rev_count`),
    # because deleting an original takes its revisions with it and the row has to
    # be able to say so before the reviewer confirms. Counted over every row found,
    # before `limit` truncates, so the number is the truth on disk rather than the
    # truth about this page.
    rev_counts = {}
    for row in rows:
        if row["revision"]:
            key = (row["client"], row["number"])
            rev_counts[key] = rev_counts.get(key, 0) + 1
    for row in rows:
        row["rev_count"] = (
            0 if row["revision"] else rev_counts.get((row["client"], row["number"]), 0)
        )
    # Newest first, but a checkout gives whole batches of decks the same mtime, and
    # ordering inside a batch by directory order interleaves clients and puts
    # revisions above the render they came from. Fall back to client, then deck
    # number, then revision, so a tie reads as a list rather than as noise.
    rows.sort(key=lambda r: (r["mtime"], r["client"], r["number"], r["revision"]),
              reverse=True)
    return rows[:limit]


def delete_deck(path):
    """Delete one rendered deck from disk and return what actually went.

    A REVISION (``output-N-rK.html``) goes on its own: the file, plus the entries
    it contributed to the shared ``output-N.edits.json`` log — the same bookkeeping
    ``undo_last_edit`` does, so the surviving history never cites a revision that
    is no longer on disk. The log file is removed when it empties, so a deck whose
    every revision has been deleted reads as having no edit history.

    An ORIGINAL (``output-N.html``) takes its revisions and the whole edit log with
    it. A revision is a derived copy of the original and the edit ladder walks back
    to it (``undo_last_edit`` on ``-r1`` lands on the original), so leaving the
    revisions behind would orphan them against a deck that no longer exists.

    The run's saved Design prompt under ``PROMPTS_ROOT`` is deliberately NOT
    touched. It is a separate artifact of the run, and deleting a deck is not a
    request to delete the record of how the deck was asked for.

    Callers are responsible for confirming the path is inside ``DECKS_ROOT``; this
    only ever removes files whose names match the deck naming pattern, so a path
    that slipped through cannot make it delete something arbitrary. Returns
    ``{"removed": [names...]}``, empty when nothing could be removed.
    """
    directory = os.path.dirname(os.path.abspath(path))
    name = os.path.basename(path)
    match = _DECK_FILE_ANY_RE.match(name)
    if not match:
        return {"removed": []}

    targets = [name]
    if not match.group("rev"):
        pattern = re.compile(
            rf"^{re.escape(match.group('base'))}-r(\d+)\.html$", re.IGNORECASE)
        try:
            found = [n for n in os.listdir(directory) if pattern.match(n)]
        except OSError:
            found = []
        targets += sorted(found, key=lambda n: int(pattern.match(n).group(1)))

    removed = []
    for target in targets:
        try:
            os.remove(os.path.join(directory, target))
            removed.append(target)
        except OSError:
            pass
    if not removed:
        return {"removed": []}

    log_file = edit_log_path(path)
    if match.group("rev"):
        remaining = [e for e in load_edit_log(path) if e.get("revision") not in removed]
        if remaining:
            with open(log_file, "w", encoding="utf-8") as f:
                json.dump(remaining, f, indent=2, ensure_ascii=False)
                f.write("\n")
        elif os.path.isfile(log_file):
            os.remove(log_file)
    elif os.path.isfile(log_file):
        os.remove(log_file)
    return {"removed": removed}


def _display_root(path):
    """``path`` as a repo-relative path when it is inside the repo, else absolute.

    The default roots are built from ``__file__`` and arrive unnormalized
    (``.../src/../decks``), which reads as a bug in the one place the UI tells a
    reviewer where their decks live.
    """
    real = os.path.realpath(path)
    root = os.path.realpath(_ROOT)
    if real == root or real.startswith(root + os.sep):
        return os.path.relpath(real, root) + os.sep
    return real


def _gaps_for(packet, deck_type=""):
    """The packet's declared gap flags, split into open and resolved.

    Returns ``(open_gaps, resolved_gaps)``. The packet is READ ONLY — it declares
    every gap it ever declared, always, and nothing here writes to it. Which of
    those a reviewer has confirmed comes from the separate decision store
    (``gap_decisions``), so the packet stays the data source's unaltered record of
    what it sent while the checklist still remembers the reviewer's judgment across
    runs and across deploys.

    Each gap is enriched with a human-readable ``slide_label`` (the slide's number
    and name, e.g. ``"Slide 5: Workstream 3"``) and ``where`` (via
    ``gap_resolver.describe_gap``) so the checklist points at a slide the reviewer
    can find in the deck, not a raw packet field path.

    An open gap also carries ``status``: ``"verify"`` when the reviewer sent it
    back to be verified, empty when nobody has decided anything about it yet. A
    sent-back claim stays OPEN — it was routed, not settled — so the split above is
    confirmed versus not, and the status is what stops a routed claim from looking
    untouched (C4, 2026-08-09).
    """
    if not packet:
        return [], []
    packet_abs = packet if os.path.isabs(packet) else os.path.join(_ROOT, packet)
    if not os.path.isfile(packet_abs):
        return [], []
    try:
        with open(packet_abs, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return [], []
    raw = list_gaps(text)
    # The workstream count places the closing slide, whose number depends on how
    # many workstream slides precede it. Read once per call, from the same packet.
    n_workstreams = deck_shape(text)["n_workstreams"]
    decided = decisions_by_field(packet, path=DECISIONS_PATH)
    open_gaps, resolved = [], []
    for gap in raw:
        field = gap.get("field", "")
        loc = describe_gap(field, deck_type, n_workstreams)
        status = decided.get(field, "")
        row = {
            **gap,
            "slide": loc["slide"],
            "slide_number": loc["slide_number"],
            "slide_label": loc["slide_label"],
            "where": loc["where"],
            "status": status,
        }
        (resolved if status == CONFIRMED else open_gaps).append(row)
    return open_gaps, resolved


# ------------------------------- templates ---------------------------------

BASE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Q of AI Deck Studio</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Hanken+Grotesk:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap">
<style>
  /* Q of AI — deck studio theme, ported from the Claude Design mockup.
     Dark, committed (internal tool). Class-based so it restyles every
     server-rendered screen without touching the Jinja bodies. Fonts load from
     Google Fonts as progressive enhancement; offline it falls back to system-ui. */
  :root {
    color-scheme: dark;
    /* BLACK AND WHITE, WITH BLUE AS AN ACCENT AND NOT A BACKGROUND (Antonio,
       2026-09-20). Every surface used to be a shade of navy, which read as
       decoration rather than as a tool; the blue is now what a reader is meant
       to act on — links, the primary button, the focused field — and everything
       else is neutral. The house style's blue/black/white is unchanged, it is
       the proportions that are. */
    --bg: #0b0b0d;
    --panel: #141417;
    --field: #0e0e11;
    --border: #26262b;
    --border-field: #2e2e34;
    --border-soft: #1e1e22;
    --border-mid: #3a3a42;
    --divider: #1c1c20;
    --ink: #f2f2f4;
    --muted: #9a9aa4;
    --kicker: #a8a8b2;
    --faint: #6d6d78;
    --accent: #3d7dff;
    --link: #6f9fff;
    --link-hover: #a9c4ff;
    --btn: #2f6bdd;
    --btn-border: #3d7dff;
    --btn-ink: #ffffff;
    --ok: #35d07f;
    --ok-ink: #7ee6ac;
    --warn: #f4c14b;
    --bad: #ff6b7a;
    --radius: 14px;
    --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
    --title-font: "Space Grotesk", "Hanken Grotesk", system-ui, sans-serif;
    --body-font: "Hanken Grotesk", -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0;
    /* THE SCROLL THAT LOOKED LIKE A CRASH (2026-09-20). Antonio, reviewing a
       deck: "I was scrolling on the Result tab, and it just sent me back to
       Generate, and it says there's no deck yet." Nothing had crashed and
       nothing was lost. On a Mac trackpad a vertical scroll carrying any
       horizontal component is Chrome's swipe-to-go-back gesture, and "sent me
       BACK" is literally what happened: the browser walked history back to the
       page before the run and restored it from the back/forward cache, which is
       a snapshot of the Generate tab with an empty Result panel. The server was
       never asked, and it still held the deck the whole time.

       Ruled out first, because the obvious suspects were all wrong: the studio
       had not restarted (the process had been up since before the render and
       still held `_LAST_RESULT`), the run record had not been evicted
       (retention is 8 and three runs existed), and no route redirects to the
       Generate tab (every `redirect` names its tab).

       `overscroll-behavior-x: none` on the ROOT is what stops it, and it has to
       be here rather than on the panes. `.studio-deck` and `.studio-side`
       already set `overscroll-behavior: contain`, which contains their own
       scroll chain, but the deck a reviewer actually scrolls is an IFRAME whose
       document is the generated deck, and that document carries no overscroll
       rule of its own. The gesture escapes the frame and the navigation happens
       at the TOP level, so the top level is where it is refused. Nothing is
       added to the deck's own CSS: the deck is a deliverable that gets
       downloaded and opened on its own, and studio behaviour does not belong
       in it. */
    overscroll-behavior-x: none;
  }
  body {
    font: 15px/1.45 var(--body-font);
    color: var(--ink);
    background: var(--bg);
    min-height: 100vh;
    -webkit-font-smoothing: antialiased;
  }
  ::selection { background: rgba(61,125,255,.22); }
  ::-webkit-scrollbar { width: 10px; height: 10px; }
  /* Neutral, not navy (Antonio, 2026-09-20: "the thing that you can scroll
     with your mouse ... it's blue. I don't want it to be blue"). */
  ::-webkit-scrollbar-thumb { background: var(--border-mid); border-radius: 6px;
    border: 2px solid transparent; background-clip: padding-box; }
  ::-webkit-scrollbar-track { background: transparent; }

  /* ---- header / brand ---- */
  header {
    position: sticky; top: 0; z-index: 40;
    display: flex; align-items: center; gap: 16px;
    padding: 14px 40px;
    /* Neutral chrome. The header and the tab bar were a dark navy
       (rgba(7,12,26)) over a blue divider, which is the "top is blue" Antonio
       asked to lose on 2026-09-20. Blue stays where it means something a
       reviewer acts on: links, the primary button, the focus ring, picked rows
       and the progress bar. */
    background: rgba(11,11,13,.82);
    backdrop-filter: blur(14px);
    border-bottom: 1px solid var(--border);
  }
  .brand-logo { flex: none; width: 30px; height: 30px; display: block; color: var(--ink); }
  .brand-text h1 { font-family: var(--title-font); font-size: 16px; margin: 0;
    font-weight: 650; letter-spacing: -.01em; line-height: 1.15; }
  .brand-text .sub { color: var(--muted); font-size: 12px; line-height: 1.3; margin-top: 1px; }
  header .spacer { flex: 1; }
  header .badge {
    flex: none; display: inline-flex; align-items: center; gap: 8px;
    font-size: 12px; color: var(--kicker); border: 1px solid var(--border-mid);
    border-radius: 8px; padding: 6px 13px; white-space: nowrap; font-weight: 500;
    background: rgba(255,255,255,.035);
  }
  header .badge .dot { width: 7px; height: 7px; border-radius: 50%;
    background: var(--muted); animation: pulsefade 2.6s infinite; }
  @keyframes pulsefade { 0%,100% { opacity:.5 } 50% { opacity:1 } }

  /* ---- tab bar ---- */
  .tabbar {
    position: sticky; top: 59px; z-index: 30;
    display: flex; gap: 4px; align-items: center;
    padding: 0 40px; background: rgba(11,11,13,.82);
    backdrop-filter: blur(14px); border-bottom: 1px solid var(--border);
  }
  .tabbar button {
    background: none; border: 0; margin: 0; padding: 14px 18px;
    font: inherit; font-weight: 600; font-size: 14px; color: var(--muted);
    cursor: pointer; border-bottom: 2px solid transparent; border-radius: 0;
    transition: color .15s, border-color .15s;
  }
  .tabbar button:hover { color: var(--ink); filter: none; }
  .tabbar button.active { color: var(--ink); border-bottom-color: var(--ink); }
  .tabbar button .count {
    display: inline-block; margin-left: 7px; font-size: 11px; font-weight: 700;
    color: var(--ink); background: rgba(255,255,255,.07); border: 1px solid var(--border-mid);
    border-radius: 999px; padding: 0 7px; line-height: 17px; vertical-align: middle;
  }

  /* Wider canvas — the studio fills the page instead of a thin column. */
  main { width: 100%; max-width: min(2200px, 97vw); margin: 0 auto;
         padding: 20px 28px 40px; }
  /* The studio split is the one panel that wants the full width: it has two
     columns of its own and each has its own padding, so `main`'s side padding
     is a third margin outside them. Half of it goes back (2026-09-21), which
     is most of the space Antonio saw to the right of the controls. */
  .studio-split { margin: 0 -14px; }
  /* The Result tab is the one that is mostly deck, so it keeps almost none of
     the page's side padding (Antonio, 2026-09-20: "there's some dead space ...
     on the right side and on the left side"). The Generate form still reads
     better with air, so this is scoped rather than global. */
  body.on-result main { padding: 12px 12px 16px; }

  .tab-panel { display: none; }
  .tab-panel.active { display: block; }

  /* ---- cards ---- */
  .card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 24px 26px;
    margin-bottom: 22px;
  }
  .card h2 { font-family: var(--title-font); font-size: 18px; margin: 0 0 16px;
    font-weight: 650; letter-spacing: -.01em; color: var(--ink); }
  .card h2 .muted { font-weight: 400; }

  /* ---- forms ---- */
  label { display: block; font-size: 12px; font-weight: 500; margin: 14px 0 6px;
    color: var(--muted); }
  input, select, textarea {
    width: 100%; padding: 10px 11px; border: 1px solid var(--border-field);
    border-radius: 9px; font: inherit; font-size: 14px; color: var(--ink);
    background: var(--field); transition: border-color .15s, box-shadow .15s;
  }
  input:focus, select:focus, textarea:focus {
    outline: none; border-color: var(--accent);
    box-shadow: 0 0 0 3px rgba(61,125,255,.16);
  }
  input[type=checkbox] { accent-color: var(--accent); }
  select option { background: var(--field); }
  textarea { min-height: 72px; resize: vertical; line-height: 1.4; }
  .row { display: flex; gap: 16px; flex-wrap: wrap; }
  .row > * { flex: 1; min-width: 180px; }
  button {
    background: var(--btn); color: var(--btn-ink); border: 1px solid var(--btn-border);
    border-radius: 10px; padding: 12px 24px; font: inherit; font-weight: 600;
    cursor: pointer; margin-top: 18px; transition: filter .15s, transform .1s;
  }
  button:hover { filter: brightness(1.08); }
  button:active { transform: translateY(1px); }
  /* Secondary actions read in ink, not in a pale blue. Blue is reserved for the
     ONE primary action, links out of the page, the focus ring and picked rows
     (Antonio, 2026-09-20: "I want more black and white themes"). */
  button.ghost {
    background: transparent; color: var(--ink); border: 1px solid var(--border-mid);
    border-radius: 9px; padding: 6px 13px; margin: 0; font-size: 12.5px; font-weight: 550;
  }
  button.ghost:hover { border-color: var(--accent); filter: none; }
  .inline { display: inline; }
  /* A checkbox + its text as one block row. `.inline` on a label lets a following
     button share the line and overlap the label text, so a checkbox row gets its
     own block and the checkbox keeps its intrinsic width. */
  .checkrow { margin: 16px 0 0; }
  .checkline { display: flex; align-items: center; gap: 9px; margin: 0;
    font-size: 13.5px; color: var(--ink); cursor: pointer; }
  .checkline input[type=checkbox] { width: auto; flex: none; margin: 0; }
  .muted { color: var(--muted); font-size: 13px; }
  a { color: var(--link); text-decoration: none; }
  a:hover { color: var(--link-hover); text-decoration: underline; }

  .pill {
    display: inline-block; background: rgba(255,255,255,.06); color: var(--ink);
    border: 1px solid var(--border-mid); border-radius: 6px;
    padding: 3px 10px; font-size: 11.5px; margin-right: 6px; font-weight: 550;
  }
  .ok { color: var(--ok-ink); } .warn { color: var(--warn); } .bad { color: var(--bad); }

  /* Neutral, not navy (2026-09-23). The last two surfaces still carrying the
     old navy theme after the 2026-09-20 black-and-white pass. */
  pre {
    background: var(--field); border: 1px solid var(--border-soft); border-radius: 10px;
    padding: 16px 18px; overflow-x: auto; font-size: 12.5px; white-space: pre-wrap;
    color: #cfcfd6; line-height: 1.6; font-family: var(--mono);
  }
  ul { margin: 8px 0; padding-left: 20px; }
  code {
    font-family: var(--mono); font-size: 12px;
    background: rgba(255,255,255,.05); color: var(--kicker); padding: 2px 6px;
    border-radius: 5px; border: 1px solid var(--border);
  }

  table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
  td, th { text-align: left; padding: 11px 14px; vertical-align: top; }
  th { color: var(--faint); font-weight: 600; font-size: 11px;
    text-transform: uppercase; letter-spacing: .09em; white-space: nowrap;
    border-bottom: 1px solid var(--border); }
  td { border-bottom: 1px solid var(--divider); }

  .empty-state {
    text-align: center; color: var(--muted); padding: 60px 20px;
  }
  .empty-state .big { font-family: var(--title-font); font-size: 19px;
    color: var(--ink); margin-bottom: 6px; }

  /* ---- deck preview: scaled to fit width — no sideways scroll, ever ---- */
  /* THE THREE SIT TOGETHER (Antonio, 2026-09-21: "I want the three buttons to
     be next to each other. They're far apart, which is not good"). They were
     `space-between` across the deck's full width, which on a wide display put
     "open full screen" and "download PDF" a deck apart. Grouped left, with
     Save deck pushed to the far end: it is the one action here that writes
     something, and it now shares this row instead of costing the deck another
     one. */
  .deck-toolbar {
    display: flex; align-items: center; justify-content: flex-start; gap: 8px;
    flex-wrap: wrap; margin: 10px 0 0;
  }
  /* `gap` already spaces them; the margin was a second, unequal gap. */
  .deck-toolbar .linkbtn { margin-right: 0; }
  .deck-toolbar .save-deck-form { margin: 0 0 0 auto; }
  /* Neutral, not link-blue. These three are things a reviewer does WITH a deck
     they are already looking at, not references out of the page, and Antonio
     asked on 2026-09-20 for them not to be blue. Underlined so they still read
     as clickable without colour doing the work. */
  .deck-toolbar a {
    color: var(--kicker); text-decoration: underline;
    text-underline-offset: 3px; text-decoration-color: var(--border-mid);
  }
  .deck-toolbar a:hover { color: var(--ink); text-decoration-color: var(--muted); }
  .deck-frame-wrap {
    border: 1px solid var(--border-field); border-radius: 12px; overflow: hidden;
    background: var(--bg); padding: 12px;
  }
  /* The viewport is a fixed-height window; the iframe inside is laid out at the
     deck's true 1280px width and then transform-scaled by JS so its scaled width
     exactly equals the viewport width. Result: the whole slide width is always
     visible (no horizontal scroll) and the deck scrolls vertically inside. */
  .deck-viewport {
    position: relative; width: 100%; height: 78vh; min-height: 540px;
    overflow: hidden; border-radius: 8px; background: #fff;
  }
  .deck-frame {
    position: absolute; top: 0; left: 0; width: 1280px; border: 0;
    background: #fff; transform-origin: top left;
  }

  /* ---- the opportunity picker reads as a LIST, not a text box ---- */
  /* Empty, a `select[multiple]` with one disabled row looks exactly like an
     input, which is what it was mistaken for (Antonio, 2026-09-20). */
  select[multiple] {
    padding: 6px; background: var(--field); min-height: 132px;
  }
  select[multiple] option {
    padding: 7px 9px; border-radius: 6px; margin-bottom: 2px;
  }
  select[multiple] option:checked {
    background: var(--accent) linear-gradient(0deg, var(--accent), var(--accent));
    color: var(--btn-ink);
  }
  select[multiple] option:disabled { color: var(--faint); }

  /* THE FILE CONTROLS CARRY THE ACCENT (Antonio, 2026-09-20: "let's make the
     choose file button a little colorful ... it's just the outline, can be in
     blue"). He offered either the native picker or the Add file button; doing
     both is what makes them read as one control rather than two. */
  input[type=file]::file-selector-button {
    font: inherit; font-size: 13px; font-weight: 500;
    border: 1px solid var(--accent); color: var(--accent);
    background: none; border-radius: 7px; padding: 6px 12px;
    margin-right: 10px; cursor: pointer;
  }
  input[type=file]::file-selector-button:hover { background: rgba(61,125,255,.07); }
  .addfile { border-color: var(--accent); color: var(--accent); }

  /* A link that acts like a button, for the deliverables under the deck. */
  .linkbtn {
    display: inline-block; text-decoration: none;
    font-size: 12px; font-weight: 500;
    border: 1px solid var(--accent); color: var(--accent);
    border-radius: 7px; padding: 5px 11px; margin-right: 8px;
  }
  .linkbtn:hover { background: rgba(61,125,255,.07); text-decoration: none; }

  /* A field whose answer is a couple of words does not need the column
     (Antonio, 2026-09-20: "the author optional is okay, but let's make the box
     smaller so it takes up less space"). */
  input.narrow { max-width: 220px; }

  /* The undo beside a switch, quieter than the two states it undoes. */
  /* ---- the bullet switch: a track and a knob that slides (2026-09-21) ---- */
  /* A `<button>` and not a checkbox, because each of these is its own form post
     and has to work with the page script absent. The track IS the button; the
     knob is the one child element, moved by `left` so the transition is a
     slide and not a redraw. */
  button.switch {
    position: relative; display: inline-block; vertical-align: middle;
    width: 38px; height: 21px; padding: 0; margin: 0; border-radius: 999px;
    border: 1px solid var(--border-mid); background: var(--field);
    cursor: pointer; transition: background .16s, border-color .16s;
  }
  button.switch .knob {
    position: absolute; top: 2px; left: 2px; width: 15px; height: 15px;
    border-radius: 50%; background: var(--muted);
    transition: left .16s, background .16s;
  }
  /* ON IS THE ONLY STATE WITH COLOUR (Antonio, 2026-09-21: "they have the
     color. If you toggle them off, they don't have the color"). */
  button.switch.is-on {
    background: var(--accent); border-color: var(--accent);
  }
  button.switch.is-on .knob { left: 19px; background: #fff; }
  button.switch:hover:not(:disabled) { filter: none; border-color: var(--accent); }
  button.switch:active { transform: none; }
  button.switch:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  /* Blocked: the switch stays, dead and visibly so, because a missing control
     reads as a bug while a dead one reads as a reason. */
  button.switch:disabled { opacity: .45; cursor: not-allowed; }
  /* The reset sits beside the switch and must not out-weigh it. */
  button.switch-reset { font-size: 11px; padding: 3px 7px; color: var(--muted);
    margin-left: 8px; vertical-align: middle; }

  /* "+ Add file", beside the native picker. */
  .addfile { margin-top: 8px; }

  /* ---- the upload that leads the Generate form ---- */
  .upload-lead {
    border: 1px solid var(--border-field); border-radius: 12px;
    padding: 16px 18px; margin: 4px 0 14px; background: var(--panel);
  }
  /* The Generate tab's optional inputs, folded (2026-09-21). Styled as a line
     of text with a marker, not as a card: it is a way past three fields, and a
     panel around it would cost the height the fold is here to save. */
  .more-options { margin: 16px 0 4px; }
  .more-options > summary {
    cursor: pointer; font-size: 12.5px; font-weight: 550; color: var(--kicker);
    padding: 6px 0; list-style: none; display: flex; align-items: center; gap: 7px;
  }
  .more-options > summary::-webkit-details-marker { display: none; }
  .more-options > summary::before {
    content: "›"; display: inline-block; font-size: 15px; line-height: 1;
    color: var(--muted); transition: transform .15s;
  }
  .more-options[open] > summary::before { transform: rotate(90deg); }
  .more-options > summary:hover { color: var(--ink); }
  .more-options > summary .muted { font-weight: 400; }
  .more-options[open] { border-bottom: 1px solid var(--border); padding-bottom: 10px; }

  .upload-lead .upload-title {
    font-size: 15px; font-weight: 600; color: var(--ink);
    text-transform: none; letter-spacing: -.01em; margin: 0 0 6px;
  }

  /* ---- the two ways to generate (Generate tab) ---- */
  .branchrow { display: grid; gap: 8px; margin: 14px 0 10px; }
  .branchline {
    display: flex; align-items: flex-start; gap: 10px; margin: 0;
    background: var(--panel); border: 1px solid var(--border-field);
    border-radius: 10px; padding: 11px 13px; cursor: pointer;
    font-size: 13px; font-weight: 400; color: var(--ink); text-transform: none;
    letter-spacing: normal;
  }
  .branchline input { margin-top: 2px; }
  .branchline:has(input:checked) { border-color: var(--accent, #3d7dff); }
  .prd-found {
    border: 1px solid var(--border-field); border-radius: 10px;
    padding: 12px 14px; margin-bottom: 12px; font-size: 13px;
  }
  .prd-found .line { margin: 0 0 6px; }
  .prd-found .line:last-child { margin-bottom: 0; }
  .prd-found .field { color: var(--muted); display: inline-block; min-width: 96px; }

  /* ---- the attachment list (Generate tab) ---- */
  .filelist { list-style: none; margin: 8px 0 0; padding: 0; }
  .filelist li {
    display: flex; align-items: center; gap: 10px;
    background: var(--panel); border: 1px solid var(--border-field);
    border-radius: 8px; padding: 7px 10px; margin-bottom: 6px; font-size: 13px;
  }
  .filelist .name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis;
    white-space: nowrap; }
  .filelist .size { color: var(--muted); font-size: 12px; white-space: nowrap; }
  .filelist .base { color: var(--muted); font-size: 11px; white-space: nowrap;
    text-transform: uppercase; letter-spacing: .08em; }
  /* Which opportunity a file writes. Unplaced files wrap rather than clip,
     because the reason is the part a reviewer needs to read. */
  .filelist .route { font-size: 12px; white-space: nowrap; }
  .filelist .route.unrouted { color: var(--muted); white-space: normal;
    flex: 0 1 45%; }

  /* ---- the studio split (item 16) ----
     The defect it fixes: every reviewer control sat BELOW the deck in one
     column, so reaching one meant scrolling a tall deck off the screen, and
     judging what it did meant scrolling back.

     The first cut pinned the deck and let the PAGE scroll, which was worse in
     a way that only shows on a real deck (Antonio, 2026-09-20): the page's
     scrollbar belonged to the controls column, so reading down the controls
     was the only way to move, and the deck was not even the first thing on
     screen because the run-information cards sat above it. Both are fixed
     here. The split is a fixed-height region and EACH COLUMN SCROLLS ITSELF,
     so the deck moves under its own scrollbar and the controls under theirs,
     and the deck is the first thing in its column. */
  .studio-split {
    /* 400px, not 500 (Antonio, 2026-09-20). The column lost most of its cards
       in the same review, and every pixel it gives back is one the deck takes. */
    /* 26px of gap, not 12 (Antonio, 2026-09-21: "the controls and the slides
       now are too close together, but there's extra space on the right, so
       let's move the controls to the right a little bit"). The extra 14px is
       taken from the column's own right padding and from `main`'s, not from
       the deck: widening the gap at the deck's expense would undo the size he
       asked for on 2026-09-20. */
    display: grid; grid-template-columns: minmax(0, 1fr) 400px;
    gap: 26px; align-items: stretch;
    /* The studio header and tab bar above, and a little air below. Anything
       taller than this scrolls inside a column rather than moving the page. */
    height: calc(100vh - 128px);
    min-height: 480px;
  }
  /* `overflow-x: hidden` is explicit because it would otherwise compute to
     `auto` the moment `overflow-y` is not `visible`, which is what gave the
     controls column a sideways scrollbar nobody asked for (Antonio,
     2026-09-20: "I don't need the ability to scroll"). */
  .studio-deck, .studio-side {
    min-width: 0; height: 100%; overflow-y: auto; overflow-x: hidden;
    overscroll-behavior: contain;
  }
  /* Nothing in the column may force it wide again. */
  .studio-side pre { white-space: pre-wrap; word-break: break-word; }
  /* Room for each pane's own scrollbar without the cards' right edge landing
     underneath it. */
  .studio-deck { padding-right: 4px; }
  /* 2px, not 6. This was room for the pane's own scrollbar; most of it is what
     Antonio saw as space to the RIGHT of the controls, and the wider gap on
     the other side is where it went. */
  .studio-side { padding-right: 2px; }
  /* The other half of "scrub the boxes all together" (Antonio, 2026-09-20).
     The deck's box went entirely; these cannot, because six of them stacked in
     one narrow column with no edge between them is one wall of text. They lose
     the background and most of the padding instead, and keep a hairline rule,
     which reads as separation rather than as six framed panels. */
  .studio-side > .card {
    background: none; border: 0; border-top: 1px solid var(--border);
    border-radius: 0; padding: 14px 2px 4px; margin-bottom: 8px;
  }
  .studio-side > .card:first-child { border-top: 0; padding-top: 2px; }

  /* ---- the controls column, review 3 (2026-09-21) ---- */
  /* EVERY PRIMARY BUTTON IN THIS COLUMN IS COMPACT. Antonio named three by
     hand ("Apply edit", "Render again with these switches", "Export Claude
     Design prompt") and each is oversized for the same reason: a button with
     no class gets the PRIMARY style, which is sized for "Generate deck" at the
     top of an empty form. Five buttons in this column carry no class, so all
     five are sized here rather than three of them by hand — two full-size
     buttons left among three compact ones would read as a mistake.

     `:not(.ghost)` rather than source order: `button.ghost` is the same
     specificity as `.studio-side button`, so a plain descendant rule would be
     decided by which line came last, which is the kind of thing that breaks
     the next time this block moves. */
  .studio-side button:not(.ghost) {
    padding: 8px 15px; margin-top: 12px; font-size: 13px; border-radius: 9px;
  }
  /* THE EDIT CARD IS NOT FLOATING (Antonio, 2026-09-21: "there should be some
     separation between the 'Edit this deck' box and the rest of everything ...
     I don't want to do a box, but something, because it's just kind of
     floating"). It is the first card in the column, so it is the one card with
     no rule above it and nothing marking where it starts.

     An accent rule down its left edge, not a box: it says "this is the primary
     control" in the one colour this UI already uses to say that, and it costs
     no border on three sides. The gap below it is doubled so the bullet card
     underneath reads as the next thing rather than as more of this one. */
  .studio-side > .card.edit-card {
    border-left: 2px solid var(--accent); border-radius: 0;
    padding-left: 14px; margin-bottom: 18px; padding-bottom: 8px;
  }
  /* The author field and the submit share a row, bottom-aligned so the button
     sits on the input's baseline rather than on the label's. */
  .edit-submit-row {
    display: flex; align-items: flex-end; gap: 12px; margin-top: 12px;
  }
  .edit-submit-row .edit-author { flex: 1; min-width: 0; }
  .edit-submit-row label { margin: 0 0 6px; }
  .edit-submit-row input { margin: 0; }
  .edit-submit-row button:not(.ghost) { margin-top: 0; flex: none; }
  /* The deck fills whatever the card's own chrome leaves, measured by the
     layout rather than guessed at with a subtraction: the heading, the fidelity
     line, the toolbar and the save form are each present or absent depending on
     the deck, and a fixed `calc()` was sized for the tallest case and left the
     frame at its floor on every other one. */
  .studio-deck { display: flex; flex-direction: column; }
  /* NO BOX AROUND THE DECK (Antonio, 2026-09-20: "I say we scrub the boxes all
     together and put these things a little closer to each other"). The card's
     border, background and 26px of padding were framing a thing that is already
     a frame, and each side of it was a side the deck did not get. */
  .studio-deck > .card { display: flex; flex-direction: column; flex: 1;
    min-height: 0; background: none; border: 0; border-radius: 0; padding: 0; }
  /* Same again one layer in. `overflow: hidden` stays: it is what clips the
     scaled iframe. */
  .studio-deck .deck-frame-wrap { border: 0; border-radius: 0; padding: 0; }
  /* D5: the heading sat 16px off its own status line. */
  .studio-deck > .card > h2 { margin-bottom: 6px; }
  .studio-deck > .card > p { margin-top: 4px; }
  /* The wrapper is the space; the window inside it is the slide (2026-09-21).
     Centred horizontally and pinned to the top, so a deck that does not use
     the column's full height leaves its margin at the bottom rather than
     floating the deck down the page. */
  .studio-deck .deck-frame-wrap { flex: 1; min-height: 0; display: flex;
    justify-content: center; align-items: flex-start; }
  .studio-deck .deck-viewport { flex: 1; height: auto; min-height: 300px; }
  .studio-deck .card:last-child, .studio-side .card:last-child { margin-bottom: 0; }

  /* Narrow screens keep the single column, deck first, and give the page its
     scroll back: two panes on a small screen leaves each one too short to read.
     The JS that scales the deck already refits on resize, so crossing this
     breakpoint needs no help. */
  /* The controls fold away entirely when a reviewer wants the deck at full
     width (Antonio, 2026-09-20, asked for narrowed AND collapsible). The state
     is remembered, because every edit re-renders this page and a panel that
     reopened itself on each one would be worse than not folding at all. */
  .studio-split.side-collapsed { grid-template-columns: minmax(0, 1fr) 0; gap: 0; }
  .studio-split.side-collapsed .studio-side { display: none; }
  .deck-heading-row { display: flex; align-items: baseline; gap: 12px; }
  .deck-heading-row h2 { margin: 0 0 6px; flex: 1; }
  button.side-toggle {
    font-size: 11px; padding: 4px 10px; white-space: nowrap;
    background: none; border: 1px solid var(--border-field); color: var(--muted);
  }
  button.side-toggle:hover { border-color: var(--accent); color: var(--accent);
    filter: none; }

  @media (max-width: 1180px) {
    .studio-split { grid-template-columns: 1fr; height: auto; }
    /* Folding is meaningless in one column: there is nothing beside the deck. */
    .studio-split.side-collapsed { grid-template-columns: 1fr; }
    .studio-split.side-collapsed .studio-side { display: block; }
    .side-toggle { display: none; }
    .studio-deck, .studio-side { height: auto; overflow: visible; }
    .studio-deck .deck-viewport { height: 78vh; }
  }

  /* ---- progress overlay (driven by the pipeline script below) ---- */
  #progress-overlay {
    position: fixed; inset: 0; z-index: 80; display: none;
    align-items: center; justify-content: center; padding: 24px;
    background: rgba(8,8,10,.8); backdrop-filter: blur(8px);
  }
  #progress-overlay.on { display: flex; }
  /* A run in flight must not lock the whole studio. On a tab that is not the one
     about to show the deck (Decks, Preferences) the same overlay becomes a corner
     card with no backdrop and no pointer capture, so a reviewer can read deck
     history or set a preference while a render finishes. Same element and same
     poller either way, so the run is still collected the moment it lands. */
  #progress-overlay.mini {
    inset: auto 20px 20px auto; padding: 0;
    background: none; backdrop-filter: none; pointer-events: none;
  }
  #progress-overlay.mini .progress-card {
    width: 320px; text-align: left; padding: 18px 20px 16px;
    background: var(--panel); border: 1px solid var(--border); border-radius: 14px;
    box-shadow: 0 12px 34px rgba(0,0,0,.5);
  }
  #progress-overlay.mini .brand-logo { display: none; }
  #progress-overlay.mini h3 { font-size: 15px; }
  #progress-overlay.mini .progress-stage {
    min-height: 0; font-size: 12.5px; margin-bottom: 12px;
  }
  /* The backdrop covers the page, and until 2026-09-02 it covered the tab bar
     with it, so the one control a waiting reviewer actually wants (go and read
     deck history) could not be clicked and the corner form above was
     unreachable except by reloading the URL. The header is lifted over the
     overlay while a run blocks: the tabs stay live, the form underneath stays
     covered, and a second submit is still impossible. */
  body.run-blocking header,
  body.run-blocking .tabbar { z-index: 90; }
  /* NO BOX on the blocking overlay. It was a navy card on a blue border in
     the middle of a dimmed page, which is chrome around a progress bar rather
     than anything a reviewer reads. The heading, the stage line, the bar and
     the percentage sit straight on the backdrop now.

     The MINI form below keeps its box, and that is not an inconsistency: a
     corner element floating over live content with no edge is unreadable,
     whereas a centred one over a dimmed page needs none. Same element, same
     poller; only the blocking form loses its frame. */
  .progress-card {
    width: min(480px, 100%); text-align: center;
    background: none; border: 0; border-radius: 0;
    padding: 0; animation: ovin .3s ease;
  }
  @keyframes ovin { from { opacity:0; transform: translateY(8px) scale(.98) } to { opacity:1; transform:none } }
  .progress-card .brand-logo { width: 44px; height: 44px; margin: 0 auto 14px; }
  .progress-card h3 { font-family: var(--title-font); margin: 0 0 4px;
    font-size: 20px; font-weight: 650; letter-spacing: -.01em; }
  /* Two lines of room: the stage labels wrap, and a box that grew and shrank
     between ticks would make the whole card jump. */
  .progress-stage { color: var(--muted); font-size: 14px; font-weight: 500;
    min-height: 40px; margin-bottom: 18px; line-height: 1.4; }
  .progress-track {
    height: 9px; border-radius: 999px; background: var(--field);
    border: 1px solid var(--border); overflow: hidden;
  }
  .progress-fill {
    height: 100%; width: 4%; border-radius: 999px; background: var(--accent);
    transition: width .35s ease;
  }
  .progress-fill.indeterminate { width: 40%; animation: indet 1.2s ease-in-out infinite; }
  @keyframes indet { 0% { margin-left: -40% } 100% { margin-left: 100% } }
  .progress-pct { margin-top: 12px; font-size: 12.5px; color: var(--muted);
    font-family: var(--mono); font-variant-numeric: tabular-nums; }

  /* ================= DEMO POLISH (2026-09-23) =================
     Cosmetic only: nothing here moves a control, changes what a form posts or
     touches the deck's own CSS. Kept as one block so it reads, and reverts, as
     one change. Blue, black and white, no glows and no gradients, per the house
     style; colour still only marks what a reviewer acts on or a state. */

  /* Numbers line up in every table: dates, counts, slide numbers. */
  td { font-variant-numeric: tabular-nums; }
  /* One focus ring for every control a keyboard can reach. */
  a:focus-visible, button:focus-visible, summary:focus-visible,
  input[type=checkbox]:focus-visible, input[type=file]:focus-visible {
    outline: 2px solid var(--accent); outline-offset: 2px;
  }
  /* A tab arrives rather than appearing. */
  .tab-panel.active { animation: panelin .22s ease both; }
  @keyframes panelin { from { opacity: 0; transform: translateY(4px) } to { opacity: 1; transform: none } }
  .card h3 {
    font-size: 11.5px; font-weight: 650; text-transform: uppercase;
    letter-spacing: .1em; color: var(--kicker); margin: 22px 0 8px;
  }

  /* ---- Decks: rows that read as rows, actions that read as one set ---- */
  .decks-table .pill { white-space: nowrap; }
  /* A source filename has no spaces, so it could not wrap, and with the action
     buttons held on one line the table's narrowest width passed the card's
     edge at a 1466px window (Antonio, 2026-09-23 03:12, measured by the Phase 4
     window). `anywhere`, not `break-word`: only `anywhere` lowers the table's
     minimum width. Scoped off the action cell so a button label never breaks. */
  .decks-table td:not(.row-actions) a { overflow-wrap: anywhere; }
  /* Below 1300px four buttons on one line still outrun the card (33px at a
     1100px window), so the set may wrap between buttons, never inside one. */
  @media (max-width: 1300px) {
    /* `.decks-table` in front: this block sits above the base
       `td.row-actions` rule, which would otherwise win on source order. */
    .decks-table td.row-actions { white-space: normal; }
    .decks-table td.row-actions > * { white-space: nowrap; margin-bottom: 4px; }
    .decks-table td.row-actions a.rowbtn { display: inline-block; }
  }
  .decks-table tr:not(:first-child):hover td { background: rgba(255,255,255,.022); }
  .decks-table td { vertical-align: middle; }
  td.row-actions { white-space: nowrap; text-align: right; }
  td.row-actions form.inline { display: inline; }
  a.rowbtn, td.row-actions button.ghost {
    display: inline-flex; align-items: center; font-size: 12px; font-weight: 550;
    padding: 5px 11px; margin: 0 0 0 4px; border: 1px solid var(--border-mid);
    border-radius: 8px; color: var(--ink); background: transparent; line-height: 1.3;
    text-decoration: none; transition: border-color .15s, color .15s, background .15s;
  }
  a.rowbtn:hover { border-color: var(--accent); color: var(--ink); text-decoration: none; }
  a.rowbtn-primary { background: var(--btn); border-color: var(--btn-border); color: var(--btn-ink); }
  a.rowbtn-primary:hover { color: var(--btn-ink); filter: brightness(1.08); }
  /* Delete is quiet until it is aimed at. */
  button.rowbtn-danger { color: var(--muted); }
  button.rowbtn-danger:hover { border-color: var(--bad); color: var(--bad); }

  /* ---- Result: the deck named, its state as chips, the slide on a stage ---- */
  .deck-title-block { flex: 1; min-width: 0; }
  .deck-kicker {
    display: flex; flex-wrap: wrap; font-size: 11px; font-weight: 600;
    text-transform: uppercase; letter-spacing: .12em; color: var(--faint); margin: 2px 0 5px;
  }
  .deck-kicker span + span::before { content: "·"; margin: 0 8px; }
  .deck-heading-row h2 { font-size: 21px; font-weight: 600; letter-spacing: -.015em;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .status-chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 2px 0 12px; }
  .chip {
    display: inline-flex; align-items: center; gap: 7px; font-size: 12px; font-weight: 550;
    padding: 3px 10px 3px 9px; border: 1px solid var(--border-mid); border-radius: 999px;
    color: var(--kicker); background: rgba(255,255,255,.03); white-space: nowrap;
  }
  .chip::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: var(--muted); }
  .chip-ok::before { background: var(--ok); }
  .chip-warn::before { background: var(--warn); }
  .chip-bad { border-color: rgba(255,107,122,.4); }
  .chip-bad::before { background: var(--bad); }
  /* The slide gets an edge and a shadow, so a navy slide on a near-black page
     reads as a slide rather than as more page. The wrapper stops clipping so
     the shadow can draw; the window inside it still clips the scaled frame. */
  .studio-deck .deck-frame-wrap { overflow: visible; }
  .deck-viewport {
    background: var(--field); border-radius: 10px;
    box-shadow: 0 0 0 1px rgba(255,255,255,.08), 0 22px 48px -12px rgba(0,0,0,.75);
    animation: deckin .5s cubic-bezier(.2,.7,.2,1) both;
  }
  @keyframes deckin { from { opacity: 0; transform: translateY(10px) } to { opacity: 1; transform: none } }
  .deck-toolbar { margin-top: 14px; }

  /* The bullet card sits inside `#bullets-card`, the wrapper its switches swap
     in place, so the 2026-09-20 rule that took the box off every card in this
     column (`.studio-side > .card`) never reached it and it kept a full frame. */
  .studio-side > #bullets-card > .card {
    background: none; border: 0; border-top: 1px solid var(--border);
    border-radius: 0; padding: 14px 2px 4px; margin-bottom: 8px;
  }

  /* ---- The controls column: calmer headings, smaller print ---- */
  .studio-side > .card h2 { font-size: 15.5px; font-weight: 600; letter-spacing: -.01em;
    margin-bottom: 8px; }
  .studio-side > .card p.muted { font-size: 12.5px; line-height: 1.5; }
  .studio-side > .card h3:first-of-type { margin-top: 16px; }
  /* Missing fields stack: where it is on one line, what to write under it. */
  .studio-side table.fill-table tr:first-child { display: none; }
  .studio-side table.fill-table tr {
    display: grid; grid-template-columns: auto 1fr; column-gap: 10px; row-gap: 8px;
    padding: 11px 0; border-bottom: 1px solid var(--divider);
  }
  .studio-side table.fill-table td { padding: 0; border: 0; min-width: 0; }
  .studio-side table.fill-table td:first-child {
    font-size: 11px; font-weight: 650; text-transform: uppercase; letter-spacing: .08em;
    color: var(--faint); padding-top: 3px; white-space: nowrap;
  }
  .studio-side table.fill-table td:first-child::before { content: "Slide "; }
  .studio-side table.fill-table td:nth-child(2) { font-size: 12.5px; color: var(--muted); }
  .studio-side table.fill-table td:nth-child(3) {
    grid-column: 1 / -1; display: flex; gap: 6px; align-items: center;
  }
  .studio-side table.fill-table td:nth-child(3) form.inline:first-child {
    display: flex; gap: 6px; flex: 1; min-width: 0;
  }
  input.fill-input { flex: 1; min-width: 0; width: auto; padding: 7px 10px; font-size: 13px; }
  .studio-side table.fill-table button.ghost { white-space: nowrap; padding: 6px 11px; }
  .clear-flags-form { display: inline-flex; align-items: center; gap: 8px; margin: 2px 0 6px; }

  /* ---- The wait: the moment a founder watches longest ---- */
  .progress-card .brand-logo { width: 52px; height: 52px; margin-bottom: 20px;
    animation: breathe 2.6s ease-in-out infinite; }
  @keyframes breathe { 0%,100% { opacity: .7; transform: scale(1) } 50% { opacity: 1; transform: scale(1.06) } }
  .progress-card h3 { font-size: 24px; font-weight: 600; letter-spacing: -.02em; margin-bottom: 8px; }
  .progress-track { height: 6px; }
  .progress-pct { font-size: 13px; margin-top: 14px; }
  .stage-in { animation: stagein .35s ease both; }
  @keyframes stagein { from { opacity: 0; transform: translateY(4px) } to { opacity: 1; transform: none } }
  #progress-overlay.mini .brand-logo { animation: none; }

  /* ---- Empty Result tab ---- */
  .empty-state { padding: 72px 20px; }
  .empty-icon { width: 40px; height: 40px; color: var(--faint); margin: 0 auto 14px; display: block; }
  .empty-state p.muted { max-width: 52ch; margin: 6px auto 0; line-height: 1.5; }
  .empty-state button.ghost { margin-top: 18px; padding: 8px 16px; font-size: 13px; }

  @media (prefers-reduced-motion: reduce) {
    * { animation: none !important; transition: none !important; }
  }
</style>
</head>
<body>
{% set qlogo %}
<svg class="brand-logo" viewBox="0 0 27.9143 27.9143" xmlns="http://www.w3.org/2000/svg" aria-label="Q of AI">
  <path fill="currentColor" d="M0 0V27.9143L6.97527 20.9324V6.97527H20.9324V20.9324H13.9571V13.9571L6.97527 20.9324V27.9143H27.9143V0H0Z"/>
</svg>
{% endset %}
<header>
  {{ qlogo }}
  <div class="brand-text">
    <h1>Deck Generator</h1>
    <div class="sub">Q of AI · proposal and status decks, reviewed before they ship</div>
  </div>
  <span class="spacer"></span>
</header>

<nav class="tabbar" role="tablist">
  <button type="button" data-tab="generate" role="tab">Generate</button>
  <button type="button" data-tab="decks" role="tab">Decks<span class="count" id="decks_count"
    {% if not deck_count %}style="display:none"{% endif %}>{{ deck_count }}</span></button>
  <button type="button" data-tab="preferences" role="tab">Preferences</button>
  <button type="button" data-tab="result" role="tab">Result</button>
</nav>

<main>
  <section class="tab-panel" id="panel-generate" role="tabpanel">{{ generate_panel | safe }}</section>
  <section class="tab-panel" id="panel-decks" role="tabpanel">{{ decks_panel | safe }}</section>
  <section class="tab-panel" id="panel-preferences" role="tabpanel">{{ preferences_panel | safe }}</section>
  <section class="tab-panel" id="panel-result" role="tabpanel">{{ result_panel | safe }}</section>
</main>

{# Submit-time progress overlay. A form tagged data-progress="deck" walks the
   reviewer through the real deck-pipeline stages; data-progress="edit" shows a
   single indeterminate "applying your edit" state. The page navigation on
   response clears it. #}
<div id="progress-overlay" role="dialog" aria-live="polite" aria-label="Working">
  <div class="progress-card">
    {{ qlogo }}
    <h3 id="progress-title">Generating your deck</h3>
    <div class="progress-stage" id="progress-stage">Starting…</div>
    <div class="progress-track"><div class="progress-fill" id="progress-fill"></div></div>
    <div class="progress-pct" id="progress-pct">0%</div>
  </div>
</div>

<script>
(function () {
  // ---- Tabs ----
  var buttons = Array.prototype.slice.call(document.querySelectorAll('.tabbar [data-tab]'));
  var panels = {
    generate: document.getElementById('panel-generate'),
    decks: document.getElementById('panel-decks'),
    preferences: document.getElementById('panel-preferences'),
    result: document.getElementById('panel-result')
  };
  function activate(name) {
    buttons.forEach(function (b) { b.classList.toggle('active', b.dataset.tab === name); });
    Object.keys(panels).forEach(function (k) {
      if (panels[k]) panels[k].classList.toggle('active', k === name);
    });
    // The Result tab is mostly deck, and gets the page's side padding back.
    document.body.classList.toggle('on-result', name === 'result');
    if (name === 'result') fitDecks();
    // A tab switch mid-run moves the overlay between its blocking and corner
    // forms. Told the destination rather than left to read the hash, which
    // `replaceState` below has not written yet.
    if (window.placeOverlay) window.placeOverlay(name);
    try { history.replaceState(null, '', '#' + name); } catch (e) {}
  }
  buttons.forEach(function (b) {
    b.addEventListener('click', function () { activate(b.dataset.tab); });
  });

  // ---- Folding the controls column away (Antonio, 2026-09-20) ----
  // Remembered across page loads, because applying an edit re-renders this page
  // and a column that sprang back open on every edit would be worse than one
  // that never folded. The deck is transform-scaled to its viewport by JS, so
  // the grid change alone does nothing until `fitDecks` runs again.
  var SIDE_KEY = 'psdg.side-collapsed';
  function applySide(collapsed) {
    var split = document.querySelector('.studio-split');
    var button = document.getElementById('side_toggle');
    if (split) split.classList.toggle('side-collapsed', collapsed);
    if (button) button.textContent = collapsed ? 'show controls' : 'hide controls';
    if (window.fitDecks) window.fitDecks();
  }
  window.toggleSide = function () {
    var split = document.querySelector('.studio-split');
    if (!split) return;
    var next = !split.classList.contains('side-collapsed');
    try { localStorage.setItem(SIDE_KEY, next ? '1' : ''); } catch (e) {}
    applySide(next);
  };

  // ---- Deck preview scaling: scale the 1280px-wide deck to the viewport width
  //      so the full slide is visible with no horizontal scroll. ----
  // The deck's slide is a FIXED 1280px frame. Laying the iframe out at exactly
  // 1280px looks right but is not: the iframe's own vertical scrollbar eats into
  // its content box (documentElement.clientWidth comes back ~1265), so the slide
  // overflows by the scrollbar's width, the iframe grows a horizontal scrollbar,
  // and the right edge of every slide — the footer date and confidentiality
  // strip — is hidden until the reviewer scrolls sideways. So measure the
  // scrollbar and lay the iframe out that much wider than the deck, then scale
  // off the widened width. Measured per frame rather than assumed, because the
  // gutter is 0 on overlay-scrollbar platforms and ~15px on desktop Chrome.
  var DECK_BASE_W = 1280;
  // A slide is a fixed 1280x720 box (`.slide` in every rendered deck), so the
  // deck's aspect ratio is a constant and not something to measure.
  var DECK_BASE_H = 720;
  function frameScrollbarWidth(frame) {
    try {
      var doc = frame.contentDocument;
      if (!doc || !doc.documentElement) return 0;
      var gutter = frame.contentWindow.innerWidth - doc.documentElement.clientWidth;
      return (gutter > 0 && gutter < 40) ? gutter : 0;
    } catch (e) { return 0; }  // cross-origin should not happen; degrade quietly
  }
  // ONE WHOLE SLIDE, NEVER PART OF TWO (Antonio, 2026-09-21: "the window to
  // view the slide should be the size of the slide, so you can see the whole
  // slide without scrolling ... so you don't have to scroll within the slide").
  //
  // What it used to do: scale to WIDTH, then make the iframe as tall as the
  // viewport happened to be. The deck then scrolled inside at an arbitrary
  // offset, so a reviewer landed on the bottom of one slide and the top of the
  // next and had to scroll to read either.
  //
  // What it does now: fit to CONTAIN. The frame is laid out at exactly one
  // slide (1280x720 plus the iframe's own scrollbar gutter) and scaled by
  // whichever of width and height binds first, so the scaled frame IS a slide
  // and the whole of it is on screen. Leftover viewport on the other axis
  // becomes an even margin rather than a second, partial slide.
  //
  // The 1.8 ceiling stays. Its original reason (an unbounded width scale makes
  // a slide taller than the window) is gone, but a 1280px render pushed past
  // 1.8 is being magnified past what it was rastered for.
  // THE WINDOW IS THE SLIDE, NOT THE COLUMN (Antonio, 2026-09-21: "there's some
  // empty space in the viewing window because of the height of the deck ... cut
  // the sides in. Make the window a little smaller from the left and the
  // right").
  //
  // Contain-fitting inside a fixed-size window leaves a margin on whichever
  // axis does not bind, and that margin was INSIDE the white window, which read
  // as the deck failing to fill its own box. So the available space is measured
  // on the WRAPPER and the window itself is then sized to the fitted slide:
  // whatever is left over falls outside it, as page, where it reads as layout
  // rather than as a gap in the deck.
  //
  // Measuring the wrapper and not the window is the whole trick. The window's
  // own width is about to become an output of this function, so reading it as
  // an input would feed the last fit back into the next one.
  function fitDeck(vp) {
    var frame = vp.querySelector('.deck-frame');
    if (!frame) return;
    var wrap = vp.parentElement;
    if (!wrap) return;
    var availW = wrap.clientWidth;
    var availH = wrap.clientHeight;
    if (!availW || !availH) return;
    var layoutW = DECK_BASE_W + frameScrollbarWidth(frame);
    frame.style.width = layoutW + 'px';
    frame.style.height = DECK_BASE_H + 'px';
    var scale = Math.min(1.8, availW / layoutW, availH / DECK_BASE_H);
    frame.style.transform = 'scale(' + scale + ')';
    frame.style.left = '0';
    frame.style.top = '0';
    // `flex: none` because the window is a flex child of the wrapper and would
    // otherwise be stretched back to the full column width by `flex: 1`.
    vp.style.flex = 'none';
    vp.style.minWidth = '0';
    vp.style.minHeight = '0';
    vp.style.width = Math.round(layoutW * scale) + 'px';
    vp.style.height = Math.round(DECK_BASE_H * scale) + 'px';
    snapDeck(frame);
  }

  // MOVING BETWEEN SLIDES, once the frame is exactly one slide tall. The deck
  // stacks its slides in a flex column with a 22px gap, so scrolling by the
  // frame's height would drift by a gap per slide and land mid-slide again.
  // Scroll snapping is what makes an ordinary scroll gesture come to rest on a
  // slide instead.
  //
  // INJECTED INTO THE LIVE PREVIEW DOCUMENT, NEVER INTO THE DECK. The deck is a
  // deliverable that gets downloaded and opened on its own, and studio
  // behaviour does not belong in it — the same rule the overscroll fix follows.
  // This writes a <style> into the iframe's DOM at runtime; the file on disk is
  // untouched, and the downloaded copy has never seen it.
  function snapDeck(frame) {
    try {
      var doc = frame.contentDocument;
      if (!doc || !doc.head || doc.getElementById('studio-snap')) return;
      var st = doc.createElement('style');
      st.id = 'studio-snap';
      // `center`, not `start`: the frame and the slide are the same height, so
      // centring one in the other seats it exactly, and it stays right if the
      // deck's own padding ever changes.
      st.textContent = 'html{scroll-snap-type:y mandatory}' +
                       '.slide{scroll-snap-align:center}';
      doc.head.appendChild(st);
    } catch (e) {}  // same-origin should not fail; degrade to plain scrolling
  }
  function fitDecks() {
    document.querySelectorAll('.deck-viewport').forEach(fitDeck);
  }
  window.fitDecks = fitDecks;
  window.addEventListener('resize', fitDecks);
  window.addEventListener('load', fitDecks);
  // Restore the fold before the first fit, so the deck is scaled once, to the
  // width it is actually going to have.
  (function () {
    var saved = '';
    try { saved = localStorage.getItem(SIDE_KEY) || ''; } catch (e) {}
    if (saved) applySide(true);
  })();
  // The scrollbar only exists once the deck document has laid out, and the first
  // fit can run before that. Re-fit each frame on its own load event, and keep a
  // late pass for a frame that was hidden (display:none has no scrollbar) when
  // its own load fired.
  document.querySelectorAll('.deck-frame').forEach(function (f) {
    f.addEventListener('load', function () { fitDeck(f.closest('.deck-viewport')); });
  });

  // ---- Stay on the slide (2026-09-23) ----
  // Antonio: "when I toggle a control on or off ... it takes me to the title
  // slide. Is there a way I can stay on the same slide so I can instantly see
  // the change?" Every change to a deck writes a NEW REVISION, and showing it
  // means loading a new document into the preview. The preview is one slide
  // tall and scrolls inside itself, so a fresh document starts at slide 1.
  //
  // So the slide on screen is noted before the new revision loads and scrolled
  // back into view once it has. BY INDEX, not by scroll offset: `snapDeck`
  // snaps slide centres and the deck puts a gap between slides, so an offset
  // lands between two. Found by which slide's centre is nearest the frame's
  // centre, which is exactly what the snap settles on. Restored AFTER `fitDeck`,
  // whose load listener above was registered first and injects the snap style.
  function slideOnScreen(frame) {
    try {
      var slides = frame.contentDocument.querySelectorAll('section.slide');
      var middle = frame.contentWindow.innerHeight / 2;
      var best = 0, nearest = Infinity;
      for (var i = 0; i < slides.length; i++) {
        var box = slides[i].getBoundingClientRect();
        var off = Math.abs(box.top + box.height / 2 - middle);
        if (off < nearest) { nearest = off; best = i; }
      }
      return best;
    } catch (e) { return 0; }  // same-origin should not fail; fall back to slide 1
  }
  function showSlide(frame, index) {
    try {
      var slides = frame.contentDocument.querySelectorAll('section.slide');
      if (!slides.length) return;
      // Clamped, because a revision can carry fewer slides than the last one.
      slides[Math.min(index, slides.length - 1)]
        .scrollIntoView({ block: 'center', behavior: 'instant' });
    } catch (e) {}
  }
  function reloadPreviewKeepingSlide(frame, url) {
    var index = slideOnScreen(frame);
    frame.addEventListener('load', function restore() {
      frame.removeEventListener('load', restore);
      showSlide(frame, index);
    });
    frame.src = url;
  }
  window.reloadPreviewKeepingSlide = reloadPreviewKeepingSlide;

  // THE FORMS THAT RE-RENDER THE WHOLE STUDIO keep the slide too. An edit,
  // an undo, a supplied value or a progress box posts natively and comes back
  // as a new page, so the index rides across in sessionStorage: written when
  // one of those forms submits, read and cleared by the preview's first load.
  // Only forms marked `data-keep-slide`, so a new run still opens on its cover.
  var KEEP_SLIDE_KEY = 'psdg.keep-slide';
  document.querySelectorAll('form[data-keep-slide]').forEach(function (form) {
    form.addEventListener('submit', function () {
      var frame = document.querySelector('.deck-frame');
      if (!frame) return;
      try { sessionStorage.setItem(KEEP_SLIDE_KEY, String(slideOnScreen(frame))); }
      catch (e) {}
    });
  });
  (function () {
    var kept = null;
    try {
      kept = sessionStorage.getItem(KEEP_SLIDE_KEY);
      sessionStorage.removeItem(KEEP_SLIDE_KEY);
    } catch (e) {}
    var frame = document.querySelector('.deck-frame');
    if (kept === null || !frame) return;
    var index = parseInt(kept, 10) || 0;
    // The frame may already have loaded by the time this runs.
    if (frame.contentDocument && frame.contentDocument.readyState === 'complete'
        && frame.contentDocument.querySelector('section.slide')) {
      showSlide(frame, index);
      return;
    }
    frame.addEventListener('load', function restore() {
      frame.removeEventListener('load', restore);
      showSlide(frame, index);
    });
  })();

  // ---- Fixture autofill (Generate tab) ----
  // ---- What the attached PRD fills in (Generate tab) ----
  // Antonio, 2026-09-20: the page assumes you have a PRD, because that is how
  // Casey uses it. Attach one and the client, the opportunity and the project
  // fill themselves from what the document says; attach nothing and the same
  // fields are there to be typed. No mode to choose: the page reads what is in
  // front of it.
  //
  // THE FAILURE THIS ALSO FIXES. The scan takes a few seconds (it resolves the
  // company and lists its opportunities), and pressing Generate during it
  // posted a run with no opportunity picked, which the route refuses. The
  // submit now waits for the scan and says that is what it is doing.
  (function () {
    var found = document.getElementById('prd_found');
    var input = document.getElementById('{{ attachment_field }}');
    if (!found || !input) return;
    var form = input.form;
    var scanning = false;
    var pending = false;

    function esc(text) {
      var d = document.createElement('div');
      d.textContent = text == null ? '' : String(text);
      return d.innerHTML;
    }
    function line(label, value) {
      return '<p class="line"><span class="field">' + label + '</span>' + value + '</p>';
    }
    function report(data) {
      var html = '';
      if (data.error) {
        html += '<p class="line bad">' + esc(data.error) + '</p>';
      } else {
        html += line('Client', '<b>' + esc(data.company) + '</b>'
                     + (data.client_stated && data.client_stated !== data.company
                        ? ' <span class="muted">(the document says \u201c'
                          + esc(data.client_stated) + '\u201d)</span>' : ''));
        // Every opportunity an attachment names, not only the first file's,
        // because each attached PRD writes the opportunity it names.
        var matched = matchedIds(data);
        var opps = [];
        (data.opportunities || []).forEach(function (o) {
          if (matched.indexOf(String(o.id)) !== -1) opps.push('<b>' + esc(o.label) + '</b>');
        });
        html += line(opps.length > 1 ? 'Opportunities' : 'Opportunity', opps.length
          ? opps.join(', ')
          : '<span class="warn">not matched \u2014 pick one below</span>');
        var projects = data.projects || [];
        html += line('Project', projects.length === 1
          ? '<b>' + esc(projects[0].name) + '</b>'
          : (projects.length
             ? '<span class="warn">' + projects.length
               + ' on this company \u2014 pick one below</span>'
             : '<span class="muted">none on this company</span>'));
      }
      (data.notes || []).forEach(function (n) {
        html += '<p class="line muted">' + esc(n) + '</p>';
      });
      var read = (data.documents || []).length > 1
        ? data.documents.length + ' documents' : esc(data.filename);
      html += '<p class="line muted">Read from ' + read
            + '. Change anything below before you run.</p>';
      found.innerHTML = html;
      found.style.display = 'block';
    }

    function matchedIds(data) {
      var ids = data.matched_opportunity_ids
        || (data.matched_opportunity_id ? [data.matched_opportunity_id] : []);
      return ids.map(String);
    }

    function fill(data) {
      var company = document.getElementById('company');
      var pick = document.getElementById('company_id');
      var project = document.getElementById('project');
      var picker = document.getElementById('opportunity_ids');
      if (company && data.company) company.value = data.company;
      if (pick) pick.value = data.company_id || '';
      // One project means there is nothing to choose and so nothing to guess.
      var projects = data.projects || [];
      projectFromPlatform = projects.length >= 1;
      if (project && projects.length === 1) {
        project.value = projects[0].name;
      }
      // Several means the platform is asking a question the PRD did not
      // answer, so it is the one case the project comes back on screen.
      var row = document.getElementById('project_choice_row');
      var choice = document.getElementById('project_choice');
      if (row && choice) {
        choice.innerHTML = '';
        if (projects.length > 1) {
          projects.forEach(function (pr) {
            var opt = document.createElement('option');
            opt.value = pr.name; opt.textContent = pr.name;
            choice.appendChild(opt);
          });
          row.style.display = '';
          if (project) project.value = projects[0].name;
        } else {
          row.style.display = 'none';
        }
      }
      if (picker) {
        picker.innerHTML = '';
        var placeholder = document.createElement('option');
        placeholder.value = ''; placeholder.disabled = true;
        placeholder.textContent = '{{ opportunity_placeholder }}';
        picker.appendChild(placeholder);
        (data.opportunities || []).forEach(function (o) {
          var opt = document.createElement('option');
          opt.value = o.id;
          opt.textContent = o.best ? o.label + '  (best title match for this project)'
                                   : o.label;
          // The clean name, without the best-match hint, because it is what
          // fills the project below.
          opt.dataset.label = o.label;
          if (matchedIds(data).indexOf(String(o.id)) !== -1) opt.selected = true;
          picker.appendChild(opt);
        });
      }
      // THE SCAN SELECTS PROGRAMMATICALLY, WHICH FIRES NO `change`, so the
      // listener that derives the project never hears about it and the field
      // stayed empty on the PRD path, which is the main path (found on the
      // Decks tab, 2026-09-20: every saved row had a blank Project). Called
      // here by hand. It no-ops when the platform supplied a project.
      if (window.syncProjectToOpportunity) window.syncProjectToOpportunity();
    }

    function scan() {
      if (!input.files || !input.files.length) return;
      scanning = true;
      var body = new FormData();
      // EVERY document (2026-09-22). Each one's front matter names the
      // opportunity it writes, and the run routes it there, so the list has to
      // show every file's match rather than the first one's. The client is
      // still resolved from the first.
      [].slice.call(input.files).forEach(function (f) {
        body.append('{{ attachment_field }}', f);
      });
      // The reviewer's company pick, when there is one. A re-scan without it
      // re-asks the ambiguous question and the chooser reappears forever.
      var picked = document.getElementById('company_id');
      if (picked && picked.value) body.append('company_id', picked.value);
      found.style.display = 'block';
      found.innerHTML = '<p class="line muted">Reading '
                      + (input.files.length > 1
                         ? input.files.length + ' documents'
                         : esc(input.files[0].name))
                      + ' and looking it up on the platform\u2026</p>';
      fetch('{{ url_for('prd_scan') }}', { method: 'POST', body: body })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          report(data);
          // AN AMBIGUOUS CLIENT NAME IS A QUESTION, NOT A FAILURE, and the
          // studio already had the surface for it (item 17). `/prd-scan` has
          // always returned the candidates in that shape; the PRD branch just
          // printed the refusal sentence and stopped, so a document naming a
          // company that matches four registry rows was a dead end on a screen
          // that could have asked. Nothing new is drawn here: the same chooser
          // the typed-company flow uses, offered from the other entry point.
          if (data.candidates && data.candidates.length) {
            window.offerCompanies(data.candidates, data.error || '', scan);
            return;
          }
          fill(data);
          window.attachmentRoutes = data.documents || [];
          if (window.renderAttachments) window.renderAttachments();
        })
        .catch(function (err) {
          found.innerHTML = '<p class="line bad">Could not read the document: '
                          + esc(err) + '. Name the company and pick the '
                          + 'opportunity below.</p>';
        })
        .then(function () {
          scanning = false;
          if (pending) { pending = false; if (form) form.requestSubmit(); }
        });
    }

    // DEFERRED ONE TICK, so the attachment list below has merged the new pick
    // into the input first. Read synchronously, the scan saw only the files
    // chosen this time, which after "+ Add file" is not the set that will post.
    input.addEventListener('change', function () { setTimeout(scan, 0); });
    window.rescanAttachments = function () {
      window.attachmentRoutes = [];
      if (input.files && input.files.length) scan();
    };

    // "+ Add file" reopens the native picker. The list keeps what is already
    // there and the picker's own replacement is merged into it by the
    // attachment-list script below, so adding one at a time and choosing
    // several at once end in the same place.
    var addFile = document.getElementById('add_file');
    if (addFile) addFile.addEventListener('click', function () { input.click(); });

    if (form) form.addEventListener('submit', function (ev) {
      if (!scanning) return;
      // Hold the run until the lookup lands, rather than spending one on a
      // form the scan has not filled yet.
      ev.preventDefault();
      ev.stopImmediatePropagation();
      pending = true;
      found.innerHTML += '<p class="line muted">Still reading the document '
                       + '\u2014 the run will start as soon as it lands.</p>';
    }, true);
  })();

  // ---- The attachment list (Generate tab) ----
  // The file input reports "2 files" and nothing else, and a file added by
  // mistake could only be taken back by choosing the whole set again. This
  // keeps its own list, renders it with a remove on each row, and writes the
  // list back into the input through a DataTransfer, which is the only way to
  // change what a file input will post. Picking files again ADDS to the list
  // rather than replacing it, because the native control replaces, and that is
  // the other half of the same complaint.
  (function () {
    var input = document.getElementById('{{ attachment_field }}');
    var list = document.getElementById('attachments_list');
    if (!input || !list || typeof DataTransfer === 'undefined') return;
    var files = [];

    function sizeOf(bytes) {
      if (bytes < 1024) return bytes + ' B';
      if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + ' KB';
      return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    }
    function writeBack() {
      var dt = new DataTransfer();
      files.forEach(function (f) { dt.items.add(f); });
      input.files = dt.files;
    }
    // What the scan matched each file to, by filename, or nothing yet.
    function routeOf(f) {
      var routes = window.attachmentRoutes || [];
      for (var i = 0; i < routes.length; i++) {
        if (routes[i].filename === f.name) return routes[i];
      }
      return null;
    }
    function render() {
      list.innerHTML = '';
      var routed = files.some(function (f) {
        var where = routeOf(f);
        return where && where.opportunity_id;
      });
      files.forEach(function (f, index) {
        var row = document.createElement('li');
        var name = document.createElement('span');
        name.className = 'name';
        name.textContent = f.name;
        var size = document.createElement('span');
        size.className = 'size';
        size.textContent = sizeOf(f.size);
        row.appendChild(name);
        // Which one is the base is a fact about the run, not decoration: the
        // first document attached is what the deck is written from, and the
        // paper answers only what it does not.
        // Once any file is matched to an opportunity, "base" stops being one
        // fact about the run: each matched file is the base for its own
        // opportunity, and the match says so instead.
        if (index === 0 && !routed) {
          var base = document.createElement('span');
          base.className = 'base';
          base.textContent = 'base';
          row.appendChild(base);
        }
        var where = routeOf(f);
        if (where) {
          var tag = document.createElement('span');
          tag.className = where.opportunity_id ? 'route' : 'route unrouted';
          tag.textContent = where.opportunity_id
            ? '\u2192 ' + where.opportunity_title
            : 'every opportunity \u00b7 ' + where.reason;
          // A FAILED MATCH IS VISIBLE: a PRD whose title matched nothing
          // writes every slide, and only the reviewer can tell that from a
          // supporting note that was meant to.
          if (!where.opportunity_id) tag.title = where.reason;
          row.appendChild(tag);
        }
        row.appendChild(size);
        var remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'ghost';
        remove.textContent = 'remove';
        remove.addEventListener('click', function () {
          files.splice(index, 1);
          writeBack();
          render();
          if (window.rescanAttachments) window.rescanAttachments();
        });
        row.appendChild(remove);
        list.appendChild(row);
      });
    }
    input.addEventListener('change', function () {
      [].slice.call(input.files).forEach(function (f) {
        var already = files.some(function (existing) {
          return existing.name === f.name && existing.size === f.size
                 && existing.lastModified === f.lastModified;
        });
        if (!already) files.push(f);
      });
      writeBack();
      render();
    });
    window.renderAttachments = render;
  })();

  // ---- Background save (item 16, part one 2) ----
  // Saving used to post the form and re-render the page, which is a full
  // repaint for an action that changes nothing a reviewer is looking at: the
  // scroll position went back to the top and their place in the controls
  // column was lost. The save now runs underneath and reports beside the
  // button when it lands. Without JavaScript the form posts as it always did.
  document.querySelectorAll('.save-deck-form').forEach(function (form) {
    var button = form.querySelector('button[type="submit"]');
    var status = form.querySelector('.save-deck-status');
    if (!button || !status) return;
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var data = new FormData(form);
      data.set('background', '1');
      button.disabled = true;
      status.className = 'save-deck-status muted';
      status.textContent = 'Saving\u2026';
      fetch(form.action, { method: 'POST', body: data })
        .then(function (r) {
          if (!r.ok) throw new Error('save failed: ' + r.status);
          return r.json();
        })
        .then(function (out) {
          status.className = 'save-deck-status ' + (out.bad ? 'bad' : 'ok');
          status.textContent = out.notice || 'Saved.';
          // The Decks tab was rendered at page load and does not know about
          // this save. Swap in the panel the route just rendered, so switching
          // to the tab shows the deck that was saved rather than the list as it
          // stood before. The panel binds no listeners (its delete form uses an
          // inline onsubmit), so replacing its markup costs nothing.
          if (out.decks_html) {
            var panel = document.getElementById('panel-decks');
            if (panel) panel.innerHTML = out.decks_html;
            var badge = document.getElementById('decks_count');
            if (badge) {
              badge.textContent = out.deck_count || '';
              badge.style.display = out.deck_count ? '' : 'none';
            }
          }
        })
        .catch(function (err) {
          // A failed save has to say so where the reviewer is looking. It
          // reports the same way a refused one does, because from here the
          // difference is not one they can act on differently.
          status.className = 'save-deck-status bad';
          status.textContent = 'Could not save: ' + err.message;
        })
        .then(function () { button.disabled = false; });
    });
  });

  // ---- Background bullet switch (2026-09-21) ----
  // Antonio: "when I switch a toggle, it jumps me back to the top of the page
  // ... then I have to scroll back down to where I was toggling and toggle
  // more." A switch used to post the form natively, which re-rendered the whole
  // studio: the deck iframe reloaded, both columns went back to the top, and
  // the reviewer lost their place for an action that changes nothing on screen.
  //
  // It posts underneath now and swaps the card the route re-rendered. Two
  // details make the swap worth doing rather than just restoring the scroll:
  // the switch that was pressed has to end up pointing the other way, and the
  // fitter re-runs on every switch, so the OTHER bullets' blocked states move
  // with it. Only the returned card knows both.
  //
  // Without JavaScript every one of these still posts natively and the page
  // re-renders exactly as it did before.
  function bindSwitchForms(root) {
    (root || document).querySelectorAll('.bullet-switch-form').forEach(function (form) {
      if (form.dataset.bgbound) return;
      form.dataset.bgbound = '1';
      form.addEventListener('submit', function (ev) {
        // A deck opened out of history posts no path, and the route has no
        // card to re-render without one. Let those post natively.
        var path = form.querySelector('input[name="deck_path"]');
        if (!path || !path.value) return;
        ev.preventDefault();
        var data = new FormData(form);
        data.set('background', '1');
        // The form carries several submit buttons (the switch, and a reset
        // when there is one to offer), and `FormData` alone includes none of
        // them. The pressed one is the whole instruction.
        var pressed = ev.submitter;
        if (pressed && pressed.name) data.set(pressed.name, pressed.value);
        var card = document.getElementById('bullets-card');
        var side = document.querySelector('.studio-side');
        var keepScroll = side ? side.scrollTop : 0;
        if (pressed) pressed.disabled = true;
        fetch(form.action, { method: 'POST', body: data })
          .then(function (r) {
            if (!r.ok) throw new Error('switch failed: ' + r.status);
            return r.json();
          })
          .then(function (out) {
            // THE DECK MOVED UNDER THE PAGE (2026-09-22). A switch now edits
            // the deck, which writes a NEW REVISION, so the file the page was
            // showing is no longer the current one. Two things have to follow
            // it or the reviewer is looking at, and acting on, the revision
            // before last.
            if (out.deck_path && out.old_deck_path) {
              // Every form on this page forwards the deck path in a hidden
              // field, under two different names. Repointed by VALUE rather
              // than by a list of ids, so a form added later is carried too.
              ['path', 'deck_path'].forEach(function (name) {
                document.querySelectorAll('input[name="' + name + '"]')
                  .forEach(function (input) {
                    if (input.value === out.old_deck_path) {
                      input.value = out.deck_path;
                    }
                  });
              });
            }
            if (out.preview_url) {
              // The iframe is showing the old revision's HTML. Reloading it is
              // the whole point of the feature: this is where the reviewer
              // SEES the bullet appear or go.
              // Through the helper, so the slide the switch was on is the
              // slide on screen once the new revision loads.
              var frame = document.querySelector('.deck-frame');
              if (frame) reloadPreviewKeepingSlide(frame, out.preview_url);
            }
            if (out.bullets_html && card) {
              // `outerHTML` because the fragment carries its own wrapper div,
              // so the id survives the swap and the next switch can find it.
              card.outerHTML = out.bullets_html;
              var fresh = document.getElementById('bullets-card');
              bindSwitchForms(fresh);
              var status = fresh && fresh.querySelector('.switch-status');
              if (status) {
                status.textContent = out.notice || '';
                status.className = 'switch-status ' + (out.bad ? 'bad' : 'muted');
              }
            }
            // The card can change height as switches block and free each
            // other, so the column is put back where the reviewer left it.
            if (side) side.scrollTop = keepScroll;
          })
          .catch(function (err) {
            if (pressed) pressed.disabled = false;
            var status = document.querySelector('#bullets-card .switch-status');
            if (status) {
              status.className = 'switch-status bad';
              status.textContent = 'Could not record that switch: ' + err.message;
            }
          });
      });
    });
  }
  bindSwitchForms(document);

  window.fillFixture = function (sel) {
    var o = sel.options[sel.selectedIndex];
    if (!o) return;
    var set = function (id, v) { var el = document.getElementById(id); if (el) el.value = v; };
    set('deck_type', o.dataset.deck);
    set('company', o.dataset.company);
    set('project', o.dataset.project);
    set('packet', o.dataset.packet);
    // Writing `.value` fires no `change`, so the deck type can move from under
    // the check-in row without the listener below ever hearing about it: a
    // fixture whose deck is a status deck would land with the row still hidden.
    if (window.syncDeckType) window.syncDeckType();
  };

  // ---- Which half of the form the chosen data source actually uses ----
  // The fixture controls and the live controls were both on screen at all times,
  // so switching to `live` left a frozen fixture's company and packet path
  // standing, and that company resolves against nothing on the platform. It is
  // the dead end a reviewer hits first (Casey, 2026-08-24: the studio answered
  // "'Ridgeline Site Services' matched no company in the registry" for a source
  // he had only just switched). The fixture-only controls are hidden on `live`
  // now, and the prefill they wrote is cleared -- but ONLY while it is still the
  // fixture's own value, because a company the reviewer typed is theirs and is
  // never discarded.
  function fixtureSelect() {
    return document.querySelector('select[name="fixture_id"]');
  }
  window.syncDataSource = function () {
    var source = document.querySelector('[name="data_source"]');
    if (!source) return;
    var live = source.value === 'live';
    ['fixture_row', 'packet_row'].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.style.display = live ? 'none' : '';
    });
    // The attachments row is the one control that goes the OTHER way: a fixture
    // run replays a frozen packet, so there is nothing for an attached document
    // to be the base of. Hiding is a hint and not the guarantee; a file posted
    // with the fixture source anyway is refused by the route with a sentence
    // about the data source.
    var attachments = document.getElementById('attachments_row');
    if (attachments) attachments.style.display = live ? '' : 'none';
    var sel = fixtureSelect();
    var fx = sel ? sel.options[sel.selectedIndex] : null;
    if (!fx) return;
    if (live) {
      ['company', 'project'].forEach(function (id) {
        var el = document.getElementById(id);
        if (el && el.value === (fx.dataset[id] || '')) el.value = '';
      });
    } else if (window.fillFixture) {
      // Coming back the other way, restore what the switch to live cleared, so
      // the fixture path is never left with an empty company to retype.
      window.fillFixture(sel);
    }
  };

  // ---- Which fields the chosen deck type actually uses ----
  // The check-in date belongs to a status deck and to nothing else: it dates the
  // check-in the deck reports against, and only the status path reads it
  // (`status_scope.py`, and the status mapper's engagement block).
  // It was on screen for every run regardless, so a reviewer generating a
  // proposal was asked for a date their deck has no place to put, and two of
  // Casey's sessions went on the first screen before he ever reached a deck.
  // The same rule as `syncDataSource` above: hiding is a hint and not the
  // guarantee, and the route drops the posted value anyway.
  window.syncDeckType = function () {
    var deckType = document.getElementById('deck_type');
    if (!deckType) return;
    var row = document.getElementById('checkin_row');
    if (row) row.style.display = deckType.value === 'status' ? '' : 'none';
  };

  // ---- Opportunity picker (E9e, Generate tab, live source only) ----
  var OPPORTUNITY_PLACEHOLDER = '{{ opportunity_placeholder }}';
  // Listing costs a round trip to the platform, about two seconds on a company
  // with fifteen published opportunities. Until 2026-09-02 the picker was emptied
  // at the START of that fetch and stayed empty until it landed, so a reviewer
  // who typed a company and went straight for the dropdown opened an empty box
  // with nothing on screen to say a request was in flight. That is what read as
  // broken on the 2026-08-24 walkthrough ("it seems like it's not working"), and
  // the list was in fact arriving a second or two later. It says so now.
  var OPPORTUNITY_LOADING = 'Loading opportunities\u2026';
  // A slower EARLIER request must never overwrite a newer list. Company-blur and
  // project-blur fire in quick succession, so two fetches overlap routinely, and
  // without this the loser can land last and leave one company's opportunities
  // under another company's name.
  var opportunitySeq = 0;
  var opportunityLoading = false;
  // WHAT THE LIST ON SCREEN WAS FETCHED FOR, and what was picked out of it.
  // Reported 2026-09-20: pick an opportunity, click into another field, and the
  // pick is gone. Every refetch rebuilds the <select> from scratch, and company
  // and project both refetch on blur, so leaving either field after picking
  // threw the pick away — and a blur that lands while an earlier fetch is still
  // in flight threw it away a second or two AFTER it was made, which is what
  // made it read as the box not working rather than as a reload.
  // Two rules fix it. A refetch for a company and project that have not changed
  // does not happen at all, and a refetch that does happen puts back every pick
  // whose opportunity is still in the new list.
  var opportunityKey = null;
  function opportunityKeyFor(company, project, pick) {
    return [company, project, pick].join('\u0000');
  }
  function selectedOpportunities(sel) {
    return [].slice.call(sel.options)
      .filter(function (o) { return o.selected && o.value; })
      .map(function (o) { return o.value; });
  }
  function opportunityOnly(sel, text) {
    sel.innerHTML = '';
    var opt = document.createElement('option');
    opt.value = '';
    opt.textContent = text;
    // Disabled rather than selectable: in a multiple picker an enabled
    // placeholder is a row a reviewer can pick, and picking it would submit an
    // empty id beside the real ones.
    opt.disabled = true;
    sel.appendChild(opt);
  }
  // ---- The ambiguous-company chooser (item 17) ----
  // A name that matches more than one registry record used to end here: the
  // route answered "matched 4 companies" and the remediation said to re-request
  // with a company_id, which is a value no reviewer has and no screen offered.
  // The route hands the candidates over now, because the provider is already
  // holding them when it raises, and they are rendered as a choice. Nothing
  // about the search changes: four matches on a short name is the registry
  // telling the truth about itself rather than a fault to design around.
  window.clearCompanyPick = function () {
    var hidden = document.getElementById('company_id');
    if (hidden) hidden.value = '';
    var box = document.getElementById('company_choices');
    if (box) { box.innerHTML = ''; box.style.display = 'none'; }
  };
  function offerCompanies(candidates, message, after) {
    // `after` is what happens once a company is picked, and the two entry
    // points differ. The typed-company flow wants the opportunity list
    // (`loadOpportunities`, the default). The PRD branch wants a RE-SCAN: only
    // that re-reads the document's own opportunity title against the picked
    // company's list, which is the whole point of having uploaded a PRD.
    // Listing alone would leave the reviewer to spot the match by eye.
    var box = document.getElementById('company_choices');
    if (!box) return;
    // Rendering this list AT ALL means nothing is resolved right now: the route
    // returns candidates only when it could not settle on one company. So the
    // pick that led here, if there was one, is void, and it is dropped before
    // the chooser is drawn. Without this a reviewer whose pick went stale could
    // press Run with the chooser still on screen and spend a run posting the id
    // the route had just refused.
    var hidden = document.getElementById('company_id');
    if (hidden) hidden.value = '';
    box.innerHTML = '';
    var head = document.createElement('div');
    head.className = 'muted';
    head.textContent = message + ' Pick the one you mean.';
    box.appendChild(head);
    candidates.forEach(function (c) {
      // The firm is the only thing that tells two same-named companies apart,
      // and the registry does not always carry one. Named when it is there.
      var label = c.pe_firm ? c.name + ' · ' + c.pe_firm : c.name;
      // ITEM 22. A candidate with no knowledge graph cannot produce a deck:
      // `check_kg_gate` refuses it, so picking it spends a click to be told
      // what we were already holding when we drew the row. It is still SHOWN,
      // with the reason in words, because a reviewer who typed a name that is
      // in the registry and sees nothing has learned less than one who sees it
      // named and unavailable.
      if (!c.has_kg) {
        var dead = document.createElement('div');
        dead.className = 'muted';
        dead.style.opacity = '.65';
        dead.style.margin = '6px 0';
        dead.textContent = label + ': no knowledge graph, so no deck can be '
          + 'built for it yet.';
        box.appendChild(dead);
        return;  // not a button at all: there is nothing here to press
      }
      var b = document.createElement('button');
      b.type = 'button';  // never a submit: this form's default button runs the pipeline
      b.className = 'ghost';
      // DRAFT is a different fact from no-KG and is not a refusal anywhere in
      // the pipeline, so it is said and not enforced. On the live rows the two
      // coincide, which is exactly why they are kept apart here: the first row
      // where they disagree should read correctly without another change.
      b.textContent = (c.status && c.status !== 'ACTIVE')
        ? label + ' (' + c.status.toLowerCase() + ')' : label;
      b.addEventListener('click', function () {
        var hidden = document.getElementById('company_id');
        if (hidden) hidden.value = c.company_id;
        box.innerHTML = '';
        box.style.display = 'none';
        // Straight back down the same route with the pick, which is what turns
        // the choice into a list of opportunities rather than leaving the
        // reviewer to work out that something further is needed.
        if (typeof after === 'function') { after(); return; }
        window.loadOpportunities();
      });
      box.appendChild(b);
    });
    box.style.display = '';
  }
  // The PRD branch needs the same chooser, and it is a separate IIFE. Exported
  // rather than duplicated: two company pickers that could drift apart is how
  // one of them ends up refusing a pick the other accepts.
  window.offerCompanies = offerCompanies;

  // The project field is hidden and derived (Antonio, 2026-09-20). Whatever is
  // picked first names the deck, which is what a reviewer would have typed.
  window.syncProjectChoice = function () {
    var choice = document.getElementById('project_choice');
    var project = document.getElementById('project');
    if (choice && project) project.value = choice.value || '';
  };

  // Set when the client lists a real engagement project, which then owns the
  // field. Only a client with NONE takes the opportunity's name.
  var projectFromPlatform = false;

  window.syncProjectToOpportunity = function () {
    var picker = document.getElementById('opportunity_ids');
    var project = document.getElementById('project');
    if (!picker || !project) return;
    // A client whose projects had to be chosen between has already answered.
    var row = document.getElementById('project_choice_row');
    if (row && row.style.display !== 'none') return;
    // And a client with exactly one engagement project already has its real
    // name in the field; `resolve_project` matches against that name, and an
    // opportunity title usually matches none of the client's projects.
    if (projectFromPlatform) return;
    for (var i = 0; i < picker.options.length; i++) {
      var opt = picker.options[i];
      if (opt.selected && opt.value) {
        project.value = opt.dataset.label || opt.textContent || '';
        return;
      }
    }
    project.value = '';
  };

  window.loadOpportunities = function () {
    var sel = document.getElementById('opportunity_ids');
    var source = document.querySelector('[name="data_source"]');
    var company = document.getElementById('company');
    var project = document.getElementById('project');
    var pick = document.getElementById('company_id');
    if (!sel || !source || !company) return;
    var key = opportunityKeyFor(company.value.trim(),
                                project ? project.value.trim() : '',
                                pick ? pick.value.trim() : '');
    // Nothing has changed about which list this should be, and a list is
    // already on screen. Leave it, and leave what is picked in it.
    if (key === opportunityKey && sel.options.length > 1) return;
    var wasPicked = selectedOpportunities(sel);
    var seq = ++opportunitySeq;
    opportunityLoading = false;
    opportunityOnly(sel, OPPORTUNITY_PLACEHOLDER);
    // A dropdown with nothing in it looks identical whether the company has no
    // published opportunities, the route returned an error envelope, or the
    // request was rejected outright. Every one of those now writes here.
    var note = document.getElementById('opportunity_error');
    var show = function (msg) {
      if (!note) return;
      note.textContent = msg || '';
      note.style.display = msg ? 'block' : 'none';
    };
    show('');
    // The chooser goes too, but the PICK does not: this function is what runs
    // immediately after a candidate is chosen, and clearing the id here would
    // drop the choice on the way to spending it.
    var box = document.getElementById('company_choices');
    if (box) { box.innerHTML = ''; box.style.display = 'none'; }
    if (source.value !== 'live' || !company.value.trim()) { opportunityKey = null; return; }
    opportunityLoading = true;
    opportunityOnly(sel, OPPORTUNITY_LOADING);
    // The project rides along so the route can order the list by it. Absent or
    // blank, the order is whatever the platform gave, which is what a reviewer
    // who has not typed a project yet should see.
    fetch('{{ url_for('live_opportunities') }}?company=' + encodeURIComponent(company.value.trim())
          + '&project=' + encodeURIComponent(project ? project.value.trim() : '')
          + '&company_id=' + encodeURIComponent(pick ? pick.value.trim() : ''))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (seq !== opportunitySeq) return;  // a later request owns the list now
        opportunityLoading = false;
        opportunityOnly(sel, OPPORTUNITY_PLACEHOLDER);
        if (data.candidates && data.candidates.length) {
          // The one error that is a question rather than a failure, so it is
          // asked where the company was typed instead of being reported under
          // the opportunity list as something that went wrong.
          offerCompanies(data.candidates, data.error);
          return;
        }
        if (data.error) { show('Could not list opportunities. ' + data.error); return; }
        (data.opportunities || []).forEach(function (o) {
          var opt = document.createElement('option');
          opt.value = o.id;
          // The route marks at most one leader, and only when it outscores the
          // rest outright. Saying so in the option text is the whole point of the
          // flag: a hint the reviewer can read and overrule, never a selection.
          opt.textContent = o.best ? o.label + '  (best title match for this project)'
                                   : o.label;
          // A pick survives the list being fetched again. Restored by id, so a
          // reordered list (the project decides the order) keeps the same
          // opportunities picked rather than the same positions.
          if (wasPicked.indexOf(String(o.id)) !== -1) opt.selected = true;
          sel.appendChild(opt);
        });
        opportunityKey = key;
        if (window.syncProjectToOpportunity) window.syncProjectToOpportunity();
        var kept = selectedOpportunities(sel);
        if (wasPicked.length && kept.length < wasPicked.length) {
          // The one case where a pick is dropped on purpose: this company's new
          // list does not carry it. Said out loud, because a pick that vanishes
          // silently is exactly the bug this block exists to fix.
          show(wasPicked.length - kept.length + ' of your picked opportunit'
               + (wasPicked.length - kept.length === 1 ? 'y is' : 'ies are')
               + ' not in this list, so ' + (kept.length ? 'they were dropped'
               : 'nothing is picked') + '. Pick again from the list above.');
        }
      })
      .catch(function (err) {
        if (seq !== opportunitySeq) return;
        opportunityLoading = false;
        opportunityOnly(sel, OPPORTUNITY_PLACEHOLDER);
        show('Could not list opportunities. The request to the studio failed: ' + err);
      });
  };
  // ---- Picking more than one opportunity without holding a modifier ----
  // Antonio, 2026-09-22: "when selecting multiple opportunities, they have to
  // be next to each other in the dropdown."
  //
  // Nothing in this build requires that. `request.form.getlist` takes every
  // selected id and the provider is happy with any set. It is what a native
  // `<select multiple>` DOES: a plain click REPLACES the selection, and the
  // only ways to add a second row are shift-click, which extends a contiguous
  // range, and cmd-click, which is the one a reviewer has to already know
  // about. So picking the first and the fourth by clicking each in turn
  // silently leaves one picked, and the run goes ahead with a deck the
  // reviewer did not ask for -- which is the worst shape of all, because
  // nothing refuses.
  //
  // Each click now TOGGLES its own row and leaves the others alone, which is
  // what the list looks like it does. The modifiers still work, because the
  // native behaviour is only suppressed for a plain click on an option.
  //
  // Keyboard is untouched: only `mousedown` is intercepted, so arrow keys and
  // space still select the way they always did.
  (function () {
    var picker = document.getElementById('opportunity_ids');
    if (!picker) return;
    picker.addEventListener('mousedown', function (ev) {
      // Let the modifiers do their own thing; a reviewer who knows cmd-click
      // should not find it taken away.
      if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button !== 0) return;
      var opt = ev.target;
      // Chrome and Firefox report the OPTION; a browser that reports the
      // SELECT gives us nothing to toggle, so the native behaviour stands.
      if (!opt || opt.tagName !== 'OPTION' || opt.disabled) return;
      ev.preventDefault();
      opt.selected = !opt.selected;
      // `preventDefault` on mousedown also suppresses the focus the click
      // would have given, and a picker that cannot be tabbed out of is worse
      // than the problem being fixed.
      picker.focus();
      // The pick drives the project field (`syncProjectToOpportunity`), and a
      // selection changed by script fires no event of its own.
      picker.dispatchEvent(new Event('change', { bubbles: true }));
    });
  })();

  (function () {
    var source = document.querySelector('[name="data_source"]');
    var company = document.getElementById('company');
    var project = document.getElementById('project');
    var picker = document.getElementById('opportunity_ids');
    if (source) source.addEventListener('change', function () {
      // Which half of the form is in play first, then the list for it.
      if (window.syncDataSource) window.syncDataSource();
      // The pick goes with the switch. `syncDataSource` may have just cleared
      // or restored the company under it, and an id chosen for one company name
      // is not an answer about another.
      if (window.clearCompanyPick) window.clearCompanyPick();
      window.loadOpportunities();
    });
    var deckType = document.getElementById('deck_type');
    if (deckType) deckType.addEventListener('change', function () {
      if (window.syncDeckType) window.syncDeckType();
    });
    // A picked company id belongs to the name it was picked under and to no
    // other. Editing the name drops it, which is the difference between the
    // next fetch listing the right company's opportunities and it being refused
    // for carrying an id that name's own search does not return.
    if (company) company.addEventListener('input', window.clearCompanyPick);
    if (company) company.addEventListener('blur', window.loadOpportunities);
    // Opening the picker is the moment a reviewer expects to see the list, and a
    // blur is not guaranteed to have fired before it: the company may have been
    // written by the fixture autofill, or the source switched with it already
    // filled. Fires only when there is nothing to show and nothing in flight, so
    // reopening a list that is already populated never re-fetches.
    if (picker) picker.addEventListener('focus', function () {
      if (opportunityLoading || picker.options.length > 1) return;
      window.loadOpportunities();
    });
    // The project IS the opportunity now, so the pick writes it. The first
    // pick, matching the way the deck is already titled after the first.
    if (picker) picker.addEventListener('change', window.syncProjectToOpportunity);
  })();

  // ---- Commercial terms rows (Result tab: the terms form) ----
  // Renameable and extendable, because a flat fee, a retainer plus a success fee
  // and a performance schedule are all just rows with a label and a value.
  window.addTermsRow = function () {
    var wrap = document.getElementById('terms_rows');
    if (!wrap) return;
    var row = wrap.firstElementChild.cloneNode(true);
    row.querySelectorAll('input').forEach(function (i) { i.value = ''; });
    wrap.appendChild(row);
  };

  // ---- Promote-block toggle (the "attach a note" card's format/content select) ----

  // ---- Progress overlay ----
  // Built server-side so the polling paths carry the portal's forwarded prefix and
  // the cadence is not a second copy of the constant in app.py.
  var RUN_STATUS_URL = '{{ url_for('run_status') }}';
  var RUN_RESULT_URL = '{{ url_for('home') }}?tab=result';
  var RUN_POLL_MS = {{ run_poll_ms }};
  // Paced against the real pipeline, not a fixed tick. A bar that advanced every
  // 900ms hit its last stage in five seconds and then sat at 92% for the rest of
  // the run with nothing moving — indistinguishable from a hung request. Each
  // stage therefore carries the elapsed SECOND it starts at, the percentage eases
  // toward the next stage between ticks, and the elapsed time is always on screen
  // so a slow render still reads as alive. All of that still holds.
  //
  // WHAT DID NOT HOLD was the assumption underneath the numbers. Every version of
  // this table until 2026-09-02 was built on "the Claude render leg is almost all
  // of the run", so the render stage began at second 12 and ran to second 270 —
  // 258 seconds of a 300-second expectation. A live proposal run measured that day
  // (company WTG, a 29,202-character paper) says otherwise:
  //
  //     MCP company + paper fetch      1.3s   no model
  //     deterministic parsers          0.0s   no model
  //     extraction pass              102s     model
  //     writing pass                  59s     model
  //     ranking pass                   3s     model
  //     render                       229s     model, streaming
  //     layout guard                   2.1s   headless Chrome, no model
  //                                  -----
  //     clean live run               ~397s    (6.6 min)
  //
  // The render is 58% of a clean run, not almost all of it, and it does not BEGIN
  // until about second 166. So the old bar spent the first two and a half minutes
  // claiming to render while the extraction and writing passes were what was
  // actually running, and it was baselined on 5 minutes against a 6.6-minute run,
  // which made a healthy run announce that it had outlasted expectations at the
  // exact moment the render was hitting its stride. Both halves of that lied to
  // the reviewer in the same direction: they made a normal run look wrong, which
  // is how a genuinely stalled one stopped standing out.
  //
  // The durations below are that measurement's own, ONE PER LEG, and the table a
  // given run is watched against is the legs that run actually has, laid end to
  // end. Written that way rather than as one hard-coded table because there are
  // four run shapes and only one of them is the live render: a fixture run makes
  // no model pass at all, and a run with the render switched off ends when the
  // prompt is assembled. A single table would have to be wrong for three of the
  // four, which is the mistake being corrected here rather than a new place to
  // repeat it. The four deterministic studio steps between the ranking pass and
  // the render (the consistency check, gap resolution, prompt assembly, the
  // coverage guard) cost under a second between them and share one label rather
  // than flashing four in three seconds.
  //
  // These seconds are an expectation rather than a measurement of THIS run: the
  // render is a single synchronous call with no progress channel back. So the bar
  // is capped below 100% and, once a run outlasts its expectation, says so plainly
  // rather than pretending to be nearly done. Only the page navigation completes
  // it.
  var LEGS = {
    packet:   [2,   "Reading the project packet"],
    extract:  [102, "Reading the source documents' prose for what the parsers missed"],
    write:    [59,  "Writing the framing lines the deck has no source for"],
    rank:     [3,   "Ranking slide 2's bullets by what matters most"],
    assemble: [3,   "Checking the packet, resolving gaps, assembling the prompt"],
    render:   [226, "Rendering the slides via Claude"],
    fidelity: [1,   "Checking render fidelity"],
    layout:   [2,   "Measuring the rendered layout"]
  };
  // The legs each run shape has, in the order they run. `live-render` sums to 398
  // seconds against the 397 measured.
  var SHAPES = {
    'live-render':    ['packet', 'extract', 'write', 'rank', 'assemble',
                       'render', 'fidelity', 'layout'],
    'live':           ['packet', 'extract', 'write', 'rank', 'assemble'],
    'fixture-render': ['packet', 'assemble', 'render', 'fidelity', 'layout'],
    'fixture':        ['packet', 'assemble']
  };
  // [elapsed second the leg begins, percentage AT THAT MOMENT, label]. The
  // percentage is the floor of the leg, not its finish: the bar eases from each
  // entry toward the next, so the render climbs across its whole duration instead
  // of jumping to its end value the second it starts. Derived from each leg's
  // share of the run rather than hand-tuned, so the bar moves at the rate the run
  // actually progresses, and it starts at 2% and ends short of 95% so the cap
  // holds without a second rule.
  function stagesFor(shape) {
    var legs = SHAPES[shape] || SHAPES['live-render'];
    var total = 0, k;
    for (k = 0; k < legs.length; k++) total += LEGS[legs[k]][0];
    var stages = [], at = 0;
    for (k = 0; k < legs.length; k++) {
      stages.push([at, 2 + Math.round(93 * at / total), LEGS[legs[k]][1]]);
      at += LEGS[legs[k]][0];
    }
    return {stages: stages, expected: total + 15};
  }
  // What the current run is being watched against. Replaced by `startDeck` the
  // moment a run starts; the live render is the default because it is the only
  // shape a page can be showing before it knows.
  var STAGES = stagesFor('live-render').stages;
  var RENDER_EXPECTED_S = stagesFor('live-render').expected;
  // This machine's own finished runs, per shape, from the server. Empty for a
  // studio that has not run three of a shape yet.
  var RUN_HISTORY = {{ run_history_json|safe }};
  function usualRange() {
    // The clause the "still working" line adds when there IS history, and
    // nothing at all when there is not. A median with quartiles either side
    // says "this is roughly how long, and it varies", which a single number
    // cannot; `LEGS` stays the bar's PACING table, which is what it was
    // measured for, and stops being quoted as a promise.
    var h = RUN_HISTORY[watchedShape] || RUN_HISTORY['live-render'];
    if (!h) return '';
    if (h.low === h.high) return ' — these usually take about ' + fmtElapsed(h.median);
    return ' — these usually take ' + fmtElapsed(h.low) + ' to ' + fmtElapsed(h.high)
         + ' (' + h.runs + ' runs)';
  }
  // Which shape THIS page's form would submit, read at submit time. The poller
  // re-anchors from the server's own answer, so a reload mid-run picks the right
  // table even though the form has been reset by then.
  function formShape(form) {
    var source = form && form.querySelector('[name="data_source"]');
    var render = form && form.querySelector('[name="render"]');
    var live = source && source.value === 'live';
    var rendering = render && render.checked && !render.disabled;
    return (live ? 'live' : 'fixture') + (rendering ? '-render' : '');
  }
  var overlay = document.getElementById('progress-overlay');
  var fill = document.getElementById('progress-fill');
  var pct = document.getElementById('progress-pct');
  var stageEl = document.getElementById('progress-stage');
  var titleEl = document.getElementById('progress-title');
  var timer = null;
  // Blocking on the tab that is about to show the deck, a corner card anywhere
  // else. A render runs for minutes, a reviewer waiting on one goes and looks at
  // something, and until 2026-09-02 the modal covered every tab, so deck history
  // and preferences were both unreachable for the length of the run. See the
  // `.mini` rules in the stylesheet.
  function effectiveTab() {
    var name = document.body.getAttribute('data-active-tab') || 'generate';
    var hash = (location.hash || '').replace('#', '');
    if (panels[hash]) name = hash;
    return name;
  }
  window.placeOverlay = function (tabName) {
    if (!overlay || !overlay.classList.contains('on')) return;
    var tab = tabName || effectiveTab();
    var blocking = (tab === 'generate' || tab === 'result');
    overlay.classList.toggle('mini', !blocking);
    document.body.classList.toggle('run-blocking', blocking);
  };
  function showOverlay() {
    if (!overlay) return;
    overlay.classList.add('on');
    window.placeOverlay();
  }
  // Outside startDeck deliberately: the poller re-anchors this from the server's
  // own elapsed seconds, so the count on screen tracks the run rather than the tab.
  var t0 = Date.now();
  // ROUNDED FIRST (Antonio, 2026-09-22, screenshot of the loading screen:
  // "these usually take 5m 45.69999999999999s to 6m 47.39999999999998s").
  // The elapsed counter is whole seconds and always looked right; the RANGE is
  // quantiles over recorded durations, which are floats, and `s % 60` on a
  // float returns a float. Rounded here rather than at the source, because
  // the stored precision is real and only the reading of it was wrong.
  function fmtElapsed(s) {
    var t = Math.max(0, Math.round(Number(s) || 0));
    var m = Math.floor(t / 60), r = t % 60;
    return m ? (m + 'm ' + (r < 10 ? '0' : '') + r + 's') : (r + 's');
  }
  function set(p, label, elapsed) {
    fill.style.width = p + '%';
    pct.textContent = Math.round(p) + '%'
      + (elapsed == null ? '' : '  ·  ' + fmtElapsed(elapsed) + ' elapsed');
    // A new stage fades in rather than snapping (2026-09-23, demo polish).
    // Only when the label actually changes: the poller calls this every tick
    // with the same stage, and replaying the fade each time would flicker.
    if (label && stageEl.textContent !== label) {
      stageEl.textContent = label;
      stageEl.classList.remove('stage-in');
      void stageEl.offsetWidth;  // restart the animation
      stageEl.classList.add('stage-in');
    }
  }
  function startDeck(baseElapsed, shape) {
    if (shape) {
      var table = stagesFor(shape);
      STAGES = table.stages;
      RENDER_EXPECTED_S = table.expected;
    }
    titleEl.textContent = 'Generating your deck';
    fill.classList.remove('indeterminate');
    pct.style.display = '';
    showOverlay();
    // The server's elapsed seconds, not the page's: a reload five minutes into a
    // render resumes the real count instead of starting over at zero.
    t0 = Date.now() - (baseElapsed || 0) * 1000;
    function tick() {
      var elapsed = Math.floor((Date.now() - t0) / 1000);
      // The stage we are in, and the one after it, so the bar can ease between
      // them instead of standing still for the whole render leg.
      var idx = 0;
      for (var k = 0; k < STAGES.length; k++) {
        if (elapsed >= STAGES[k][0]) idx = k;
      }
      var cur = STAGES[idx], nxt = STAGES[idx + 1];
      var p = cur[1];
      if (nxt) {
        var span = nxt[0] - cur[0];
        var into = Math.min(1, span > 0 ? (elapsed - cur[0]) / span : 1);
        p = cur[1] + (nxt[1] - cur[1]) * into;
      }
      // Past the expectation: creep, never arrive, and say what is happening.
      var label = cur[2];
      if (!nxt) {
        p = Math.min(98, cur[1] + (elapsed - cur[0]) * 0.05);
      }
      if (elapsed > RENDER_EXPECTED_S) {
        // MEASURED, not asserted. A single number was wrong almost every time
        // and read as a promise; a range off this machine's own finished runs
        // is both true and visibly an estimate. `usualRange()` is empty until
        // three runs of this shape exist, and the sentence then simply drops
        // the clause rather than falling back to a figure nobody re-measured.
        label = 'Still working' + usualRange() + '. Leave this open.';
      }
      set(p, label, elapsed);
    }
    tick();
    timer = setInterval(tick, 1000);
  }
  function startEdit() {
    titleEl.textContent = 'Applying your edit';
    stageEl.textContent = 'Reading the deck and interpreting your request…';
    fill.style.width = '';
    fill.classList.add('indeterminate');
    pct.style.display = 'none';
    showOverlay();
  }
  document.querySelectorAll('form[data-progress]').forEach(function (f) {
    f.addEventListener('submit', function () {
      if (f.getAttribute('data-progress') === 'edit') startEdit();
      else startDeck(0, formShape(f));
    });
  });

  // ---- A run that outlives its request ----
  // The deck POST used to hold the connection for the whole render, and the
  // response was what a proxy in front of the studio cut off (the portal returned
  // "upstream error" at ~300s on 2026-08-26 while the run itself was fine). Now
  // the POST returns at once with a run id, and this polls until it lands. Every
  // request is short, so no hop between here and the studio has a slow response to
  // give up on.
  var runId = document.body.getAttribute('data-run-id') || '';
  var poll = null;
  function stopWatching() {
    if (poll) { clearInterval(poll); poll = null; }
    if (timer) { clearInterval(timer); timer = null; }
  }
  function collect(id) {
    stopWatching();
    // Leaves the overlay up: the navigation is what takes it down, so there is no
    // blank moment between "done" and the deck appearing.
    var sep = RUN_RESULT_URL.indexOf('?') === -1 ? '?' : '&';
    location.replace(RUN_RESULT_URL + sep + 'run=' + encodeURIComponent(id));
  }
  var watchedShape = '';
  function watch(id) {
    // No shape yet: the first poll below carries the server's own answer, and
    // until it lands the default table is the one a live render is watched
    // against, which is the longest of the four and so never overstates progress.
    startDeck(0);
    poll = setInterval(function () {
      fetch(RUN_STATUS_URL + '?id=' + encodeURIComponent(id), {cache: 'no-store'})
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d.state === 'done') { collect(id); return; }
          if (d.state === 'unknown') {
            // The process restarted, or retention dropped it. Say so rather than
            // leaving a bar creeping toward a deck that will never arrive.
            stopWatching();
            stageEl.textContent = 'This run is no longer available. '
              + 'The service restarted while it was working. Re-run it.';
            return;
          }
          // Re-anchor on the server's clock each tick, so the elapsed time stays
          // true across a reload and never drifts from the run itself.
          if (typeof d.elapsed === 'number') t0 = Date.now() - d.elapsed * 1000;
          // And on the server's account of what KIND of run this is, which the
          // form cannot answer after a reload has reset it. A fixture render and
          // a live render differ by nearly three minutes of model passes, so a
          // page watching the wrong one narrates legs that are not running.
          if (d.shape && SHAPES[d.shape] && d.shape !== watchedShape) {
            watchedShape = d.shape;
            var table = stagesFor(d.shape);
            STAGES = table.stages;
            RENDER_EXPECTED_S = table.expected;
          }
        })
        .catch(function () { /* a dropped poll is not a failed run; try again */ });
    }, RUN_POLL_MS);
  }
  window.addEventListener('pageshow', function () {
    // A run in flight keeps its overlay; anything else clears it, which is what
    // stops a back-button page from showing a frozen bar.
    if (runId) return;
    stopWatching();
    overlay.classList.remove('on');
    overlay.classList.remove('mini');
    document.body.classList.remove('run-blocking');
  });
  if (runId) watch(runId);

  // ---- Autofill the default-selected fixture on load ----
  // onchange never fires for the option already selected at load, so the default
  // fixture's company/project/packet fields would stay empty and the run would
  // submit a blank packet. Fill them once on load.
  var fxSel = document.querySelector('select[name="fixture_id"]');
  if (fxSel && window.fillFixture) window.fillFixture(fxSel);
  // Then hide whichever half of the form the current source does not use. After
  // the autofill, so a reload on the live source starts with the fixture prefill
  // already cleared rather than showing it for a moment first.
  if (window.syncDataSource) window.syncDataSource();
  // And whichever fields the deck type on the form does not use. `fillFixture`
  // above may have moved the deck type a moment ago, so this runs after it for
  // the same reason.
  if (window.syncDeckType) window.syncDeckType();

  // ---- Initial tab: honor the server hint, then the URL hash ----
  var initial = document.body.getAttribute('data-active-tab') || 'generate';
  var hash = (location.hash || '').replace('#', '');
  if (panels[hash]) initial = hash;
  activate(initial);
})();
</script>
</body>
</html>
"""


GENERATE_PANEL = """
<div class="card">
  <h2>Generate a deck</h2>
  {# Multipart because of the file input below, and nothing more: the form still
     posts natively and the page script only drives the progress overlay, so
     attachments needed no JavaScript. #}
  <form method="post" action="{{ url_for('run') }}" data-progress="deck"
        enctype="multipart/form-data">
    {# THE FROZEN-PACKET SOURCE IS GONE FROM THIS FORM (Antonio, 2026-09-20):
       every deck from here on is built from live platform data, so the choice
       between a frozen packet and the platform was a question with one answer
       and a way to spend a run on the wrong one. The data source is live, and
       this hidden field is what says so to the route.

       The fixture PATH is not deleted, only taken off the screen. It is what
       the suite's 2126 tests and every sample render run on, and deleting it
       would delete the only offline way to exercise the pipeline. A request
       that names it explicitly is still honoured; nothing on this page can
       produce one. #}
    <input type="hidden" name="data_source" value="live">

    {# THE PRD LEADS THE FORM (Antonio, 2026-09-20). The radio chooser that was
       here for an hour asked a question the page can answer by looking: attach
       a document and the branch is the PRD one, attach nothing and it is the
       manual one. Casey opens this page with a PRD in hand, so the upload is
       the first thing on it and the fields below fill themselves from what the
       document says. With no PRD the same fields are right there, empty, to be
       filled by hand. #}
    <div id="attachments_row" class="upload-lead">
    <label class="upload-title">Upload the PRD and any other files here</label>
    {# One line (Antonio, 2026-09-20). What the scan fills in is visible in the
       fields the moment it lands, and the no-PRD path is the rest of the form
       being there, so neither needed saying. #}
    <div class="muted" style="margin:-4px 0 10px">Attach the PRD, then press Generate.</div>
    <input type="file" name="{{ attachment_field }}" id="{{ attachment_field }}"
           accept="{{ attachment_accept }}" multiple>
    {# Files arrive one at a time as often as in a batch, and the native control
       REPLACES the set every time it is used, so a second visit to it used to
       throw the first file away. The list below keeps them and this button is
       the way to add to it (Antonio, 2026-09-20). #}
    <button type="button" class="ghost addfile" id="add_file" title="Add another file">+ Add file</button>
    {# The browser's own control says "2 files" and offers no way to take one
       back, so an attachment added by mistake could only be undone by picking
       the whole set again (Antonio, 2026-09-20). This lists what is attached,
       by name and size, with a remove on each. The input's own FileList is
       rebuilt as the list changes, so what is posted is always what is shown. #}
    <ul id="attachments_list" class="filelist"></ul>
    <div class="muted">{{ attachment_accept }}, up to {{ attachment_max_mb }} each.</div>
    </div>
    {# Where the scan reports. Empty until a document is read. #}
    <div id="prd_found" class="prd-found" style="display:none"></div>

    <div class="row">
      <div>
        <label>Deck type</label>
        <select name="deck_type" id="deck_type">
          <option value="proposal">proposal</option>
          <option value="status">status</option>
        </select>
      </div>
    </div>
    {# These are filled by the scan when a PRD is attached and typed when one is
       not. They are always on screen either way (Antonio, 2026-09-20): hiding
       them until a scan had run meant a reviewer with no PRD found an empty
       page, and a reviewer whose scan failed found nothing to fall back to.
       Whatever is in them at submit is what runs. #}
    <div class="row">
      <div id="company_row"><label>Company</label><input type="text" name="company" id="company"></div>
    </div>
    {# THE PROJECT IS NOT ASKED FOR ANY MORE (Antonio, 2026-09-20: "since the
       opportunity is just the name of the slide deck every time, we don't need
       a project field ... let's omit the project field altogether").

       It is filled from the opportunity instead of being dropped, because it is
       not only a label. Every live run reaches `resolve_project`, and a client
       that lists engagement projects with no project supplied is refused with
       `E_PROJECT_REQUIRED` — the guard that stops a deck being titled off one
       engagement and written from another. Sending the opportunity's own name
       keeps that guard satisfied and honest, and `run()` fills it server-side
       as well for a post where this script never ran. #}
    <input type="hidden" name="project" id="project" value="">
    {# AND THE ONE CASE THE HIDDEN FIELD CANNOT ANSWER. `resolve_project`
       substring-matches the project against the client's own engagement
       projects, so a client that lists several and no way to say which one
       would be refused with `E_PROJECT_NOT_FOUND` and nothing on screen to fix
       it with. Hidden for a client with none (which is the PRD case Antonio was
       looking at) and for a client with exactly one, since neither asks a
       question; shown only when the platform genuinely needs an answer. #}
    <div id="project_choice_row" style="display:none">
      <label>Which engagement is this deck for?
        <span class="muted">(this client lists more than one)</span></label>
      <select id="project_choice" onchange="syncProjectChoice()"></select>
    </div>
    {# The ambiguous-company picker (item 17). Empty and hidden until a company
       name matches more than one registry record, which is the registry telling
       the truth about itself rather than a fault: a search for a short name can
       match four companies whose names all contain it. The chooser is written
       here, under the name it is about, and the id it posts rides beside that
       name because the provider resolves it by narrowing the name's own search.
       Cleared the moment the name is edited, since an id from one name's
       candidates means nothing under another. #}
    <div id="company_choices" style="display:none"></div>
    <input type="hidden" name="company_id" id="company_id">
    {# The short client name and the deck title are no longer asked for
       (Antonio, 2026-09-20): the deck takes its title from the opportunity,
       which is the name the reviewer has already picked and the one the client
       knows the work by. Both remain route parameters, so a programmatic caller
       can still supply either and `resolve_project` still honours a
       `deck_title` when one is given. #}
    <div id="opportunity_row">
    <label>Opportunities <span class="muted">(pick one or more)</span></label>
    <select name="opportunity_ids" id="opportunity_ids" multiple size="6">
      <option value="" disabled>{{ opportunity_placeholder }}</option>
    </select>
    {# "In the order you pick them" was not true (2026-09-22). A form submits a
       multiple select's options in DOCUMENT order, so the slides follow the
       list, not the order a reviewer clicked, and "titled after the first"
       means the highest one picked rather than the one picked first. Said
       accurately here rather than fixed in the route, because which order is
       WANTED is Antonio's call and not a bug to be guessed at. #}
    <div class="muted">Click each one you want. Each gets its own slide, in the
      order they appear in this list, and the deck is titled after the
      highest.</div>
    <p class="bad" id="opportunity_error" style="display:none;margin:6px 0 0"></p>
    </div>
    {# THE OPTIONAL INPUTS FOLD AWAY (Antonio, 2026-09-21: "Can we simplify the
       Generate tab so I don't need to scroll all the way down to generate?").

       On the PRD path — the main path — a reviewer reads none of these three.
       Check-in date applies to status decks only and `syncDeckType` already
       hides it on a proposal; PE firm and proposal date are optional on every
       live run and the proposal date is pre-filled with today. Three fields
       nobody edits were two full rows between the upload and the Generate
       button.

       A native `<details>`, not a scripted panel: it posts its fields whether
       it is open or shut, it needs no JavaScript, and `syncDeckType` can go on
       hiding `#checkin_row` inside it without knowing it is in here. #}
    <details class="more-options">
      <summary>Optional details<span class="muted"> — check-in date, PE firm, proposal date</span></summary>
      <div class="row">
        {# Hidden on a proposal deck by `syncDeckType`: a proposal has no
           check-in date and never has, so the field was a question every run
           asked and only half the runs had an answer to. Hiding is a hint and
           not the guarantee; a date posted with a proposal anyway is dropped by
           the route rather than refused, because it is inert downstream rather
           than wrong. #}
        <div id="checkin_row">
          <label>Check-in date <span class="muted">(status decks)</span></label>
          <input type="date" name="check_in_date" id="check_in_date" value="{{ today }}">
        </div>
        <div>
          <label>PE firm <span class="muted">(live source)</span></label>
          <input type="text" name="pe_firm" id="pe_firm">
        </div>
        <div>
          <label>Proposal date <span class="muted">(live source)</span></label>
          <input type="text" name="proposal_date" id="proposal_date" value="{{ today }}" placeholder="e.g. 2026-08-15">
        </div>
      </div>
    </details>
    {# Hidden on the live source for the same reason as the fixture picker: a
       live run ignores this path entirely. #}
    {# The packet path went with the frozen source: a live run ignores it, and
       it was only ever the fixture picker's third field. The input stays as a
       hidden one because the studio keys a run's reviewer state to its packet
       (flagged claims, bullet switches), and a live run fills it in with the
       packet it assembled. #}
    <input type="hidden" name="packet" id="packet" value="">
    {# Commercial terms are NOT asked for here any more (2026-08-20). They were
       entered on this tab, before the deck existed, while everything else on that
       slide arrived as `[MISSING: ...]` rows in the supply-missing card afterwards
       — two places, two shapes, for one slide, which is most of why Antonio called
       the commercial editing confusing. They now live in one form on the Result
       tab, which is also when a reviewer actually settles them: after seeing the
       deck. The render's own "awaiting commercial terms input" state is what the
       slide carries until then, by design. #}
    {# The documents the deck is written from (item 14). Hidden on the fixture
       source by `syncDataSource`, for the same reason as the fixture picker: a
       fixture run replays a frozen packet and has nothing for a document to be
       the base of. The accept list is read off
       `document_text.ACCEPTED_EXTENSIONS` rather than restated here, so an
       extension added there appears on this form with no edit to the markup. #}
    {# A block-level row, not an inline label: an inline label lets the following
       button share its line and sit on top of the label text. #}
    <div class="checkrow">
      <label class="checkline">
        <input type="checkbox" name="render" value="1" {{ 'checked' if has_key else 'disabled' }}>
        <span>Render HTML deck via Anthropic API</span>
      </label>
    </div>
    {% if not has_key %}
      <div class="muted">ANTHROPIC_API_KEY not set — render leg disabled. The pipeline
      still runs and the Design prompt + any non-render outcome are shown.</div>
    {% endif %}
    {# The two prose passes used to be explained in a paragraph here. They are a
       fact about the environment rather than a decision on this form, and with
       a key set they always run, so the paragraph was telling a reviewer
       something they could not act on. It is stated where it can be acted on:
       the Result tab records what each pass read or wrote. Without a key the
       warning above already says the leg is off. #}
    <button type="submit">Generate deck</button>
  </form>
</div>
"""


DECKS_PANEL = """
<div class="card">
  <h2>Decks already generated <span class="muted">({{ decks|length }})</span></h2>
  {% if notice %}<p class="{{ 'bad' if notice_bad else 'ok' }}"
     style="margin:0 0 14px">{{ notice }}</p>{% endif %}
  {% if db_mode %}
  <p class="muted">Every deck a reviewer <b>saved</b>, newest first and
  read from the database, so it survives a redeploy. A render does not land here on its own:
  <b>Save deck</b> on the Result tab is the only thing that writes a row.</p>
  {% else %}
  <p class="muted">Every deck rendered into <code>{{ decks_root }}</code>, newest
  first. A row marked <code>r{{ '{' }}K{{ '}' }}</code> is a revision carrying
  reviewer edits; the original render sits beside it, untouched.</p>
  {% endif %}
  {# The one line here that is a warning rather than a description. #}
  <p class="muted">Delete cannot be undone{{ '' if db_mode else ' and removes the deck from disk' }}.
  {%- if not db_mode %} Deleting an original takes its revisions with it.
  {%- endif %} The run's saved Design prompt is kept either way.</p>
  {% if decks %}
  <form method="post" action="{{ url_for('delete_deck_bulk_route') }}"
        onsubmit="return confirm('Delete every selected deck? This cannot be undone.');">
  <table class="decks-table">
    <tr>
      <th></th>
      {% if db_mode %}
      <th>Client</th><th>Project</th><th>Source</th><th>Deck type</th><th>Generated</th><th>Final</th><th></th>
      {% else %}
      <th>Client</th><th>File</th><th>Kind</th><th>Rendered</th><th></th>
      {% endif %}
    </tr>
    {% for d in decks %}
    <tr>
      <td><input type="checkbox" name="selected" value="{{ d.id if db_mode else d.path }}"></td>
      {% if db_mode %}
      <td><span class="pill">{{ d.company }}</span></td>
      <td>{{ d.project }}</td>
      {# WHICH DOCUMENT THIS DECK WAS WRITTEN FROM (Antonio, 2026-09-20). It
         replaces the card of that name on the Result tab: "the user knows
         because they uploaded the PRD ... when we press save deck, it has the
         PRD like in the decks tab."

         The link opens the TEXT the model actually read, not the file. The
         original bytes are never stored (`attachment_record` keeps the
         filename, the kind, the size, a sha256 and the extracted text), and the
         text is the honest record of what produced these slides anyway.

         A row saved before attachments existed has no key and renders a dash,
         the same way every other late-added `details` key behaves. #}
      <td class="muted">
        {%- set sources = (d.details or {}).get('attachments') or [] %}
        {%- if sources %}
        <a href="{{ url_for('attachment_text', id=d.id, index=0) }}"
           target="_blank" rel="noopener">{{ sources[0].filename }}</a>
        {%- if sources|length > 1 %}
        <div style="font-size:11px">+{{ sources|length - 1 }} more</div>
        {%- endif %}
        {%- else %}—{% endif %}
      </td>
      <td class="muted">{{ d.deck_type }}</td>
      <td class="muted" style="white-space:nowrap">{{ d.created_at.strftime('%Y-%m-%d %H:%M') }}</td>
      <td class="muted">{{ 'final' if d.is_final else 'not final' }}</td>
      <td class="row-actions">
        <a class="rowbtn rowbtn-primary" href="{{ url_for('deck_view', id=d.id) }}">open to edit</a>
        <a class="rowbtn" href="{{ url_for('preview', path=_db_deck_prefix ~ d.id) }}" target="_blank" rel="noopener">full screen ↗</a>
        <a class="rowbtn" href="{{ url_for('download', path=_db_deck_prefix ~ d.id) }}">download</a>
        <button class="ghost rowbtn-danger" type="submit" form="deck-delete-{{ loop.index }}">delete</button>
      </td>
      {% else %}
      <td><span class="pill">{{ d.client }}</span></td>
      <td><code>{{ d.name }}</code></td>
      <td class="muted">
        {%- if d.revision %}revision r{{ d.revision }}{% if d.edits %} · {{ d.edits }} logged edit(s){% endif %}
        {%- else %}original render{% endif %}
      </td>
      <td class="muted" style="white-space:nowrap">{{ d.when }}</td>
      <td class="row-actions">
        <a class="rowbtn rowbtn-primary" href="{{ url_for('deck_view', path=d.path) }}">open to edit</a>
        <a class="rowbtn" href="{{ url_for('preview', path=d.path) }}" target="_blank" rel="noopener">full screen ↗</a>
        <a class="rowbtn" href="{{ url_for('download', path=d.path) }}">download</a>
        <button class="ghost rowbtn-danger" type="submit" form="deck-delete-{{ loop.index }}">delete</button>
      </td>
      {% endif %}
    </tr>
    {% endfor %}
  </table>
  <button class="ghost" type="submit" style="margin-top:10px">Delete selected</button>
  </form>
  {# THE ROW DELETES LIVE OUT HERE (2026-09-23). Each used to be a <form> inside
     the table, and the table is inside the "Delete selected" form. A form
     cannot contain a form, so the browser dropped every inner one: a row's
     delete button submitted the BULK form, with its confirm, and deleted the
     TICKED decks rather than its own row (or nothing, with none ticked). Each
     row's button now names its own form here through the `form` attribute,
     which a button may do from anywhere in the document. #}
  {% for d in decks %}
  {% if db_mode %}
  <form id="deck-delete-{{ loop.index }}" method="post" action="{{ url_for('delete_deck_route') }}"
        onsubmit='return confirm("Delete this deck? This cannot be undone.");'>
    <input type="hidden" name="id" value="{{ d.id }}">
  </form>
  {% else %}
  {%- set also = ', and the %d revision(s) beside it'|format(d.rev_count) if d.rev_count else '' %}
  {#- Single-quoted attribute deliberately: `tojson` escapes `'` but not `"`,
      so a double-quoted attribute would be terminated by the message's own
      quotes and the confirm would never fire. #}
  <form id="deck-delete-{{ loop.index }}" method="post" action="{{ url_for('delete_deck_route') }}"
        onsubmit='return confirm({{ ("Delete " ~ d.name ~ also ~ "? This removes the file from disk and cannot be undone.") | tojson }});'>
    <input type="hidden" name="path" value="{{ d.path }}">
  </form>
  {% endif %}
  {% endfor %}
  {% else %}
  {% if db_mode %}
  <p class="muted">Nothing saved yet. Renders sit under
  <code>{{ decks_root }}</code> until you press <b>Save deck</b>.</p>
  {% else %}
  <p class="muted">Nothing rendered yet. Generate a deck and it shows up here.</p>
  {% endif %}
  {% endif %}
</div>
"""


PREFERENCES_PANEL = """
<div class="card">
  <h2>Standing reviewer preferences</h2>
  <p class="muted">Format-only refinements applied automatically to every future
  run on the matching path. They never change deck content.</p>
  {% if prefs %}
  <table>
    <tr><th>#</th><th>Scope</th><th>Note</th><th>Author</th><th>State</th><th></th></tr>
    {% for p in prefs %}
    <tr>
      <td>{{ p.id }}</td>
      <td><span class="pill">{{ p.scope }}</span></td>
      <td>{{ p.note }}</td>
      <td class="muted">{{ p.author or '—' }}</td>
      <td>{{ 'active' if p.active else 'inactive' }}</td>
      <td style="white-space:nowrap">
        <form method="post" action="{{ url_for('toggle_preference') }}" class="inline">
          <input type="hidden" name="id" value="{{ p.id }}">
          <input type="hidden" name="active" value="{{ 0 if p.active else 1 }}">
          <button class="ghost" type="submit">{{ 'deactivate' if p.active else 'activate' }}</button>
        </form>
        <form method="post" action="{{ url_for('delete_preference_route') }}" class="inline"
              onsubmit="return confirm('Delete this preference permanently?');">
          <input type="hidden" name="id" value="{{ p.id }}">
          <button class="ghost" type="submit">delete</button>
        </form>
      </td>
    </tr>
    {% endfor %}
  </table>
  {% else %}
    <p class="muted">None captured yet.</p>
  {% endif %}

  <form method="post" action="{{ url_for('add_preference_route') }}">
    <div class="row">
      <div>
        <label>Scope</label>
        <select name="scope">
          {% for s in scopes %}<option value="{{ s }}">{{ s }}</option>{% endfor %}
        </select>
      </div>
      <div><label>Author (optional)</label><input type="text" name="author"></div>
    </div>
    <label>New preference note</label>
    <textarea name="note" placeholder="e.g. tighten vertical whitespace between tracker rows"></textarea>
    <button type="submit">Save preference</button>
  </form>
</div>
"""


# Inserted as-is, NOT rendered through Jinja, so a template comment here would
# print on the page. "Pick a fixture" named a picker that left the Generate tab
# on 2026-09-20; the copy was rewritten 2026-09-23 for what the tab asks now,
# with a way back to it.
RESULT_EMPTY = """
<div class="card">
  <div class="empty-state">
    <svg class="empty-icon" viewBox="0 0 24 24" aria-hidden="true" fill="none"
         stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">
      <rect x="3" y="5" width="18" height="12" rx="1.5"/><path d="M8 21h8"/><path d="M12 17v4"/>
    </svg>
    <div class="big">No deck yet</div>
    <p class="muted">Upload a PRD on the Generate tab and press Generate. The deck,
    the edit box and the review checklist show up here.</p>
    <button type="button" class="ghost" onclick="var b=document.querySelector('.tabbar [data-tab=generate]'); if (b) b.click();">Go to Generate</button>
  </div>
</div>
"""


# Slide 2's bullet selection, as its own fragment.
#
# WHY IT IS NOT PART OF `RESULT_PANEL`. A switch used to post the form and
# re-render the whole page, which threw the reviewer back to the top of the
# controls column every time (Antonio, 2026-09-21: "when I switch a toggle,
# it jumps me back to the top of the page ... then I have to scroll back down
# to where I was toggling"). The switch posts in the background now and swaps
# this card in place, which is only possible if the card can be rendered on
# its own. Same shape as `EDIT_CARDS`, and it reads the same context.
#
# The wrapper div carries the id the script swaps, and is outside the
# `{% if %}` so a run with no selection still has somewhere to put one.
BULLETS_CARD = """
<div id="bullets-card">
{% if result and result.bullet_selection %}
{% set sel = result.bullet_selection %}
<div class="card">
  {# STRIPPED TO THE BULLET AND ITS SWITCH (Antonio, 2026-09-20: "let's remove
     all this jibber jabber ... just the bullet and then it's toggled on. So
     very simple. Just bullet column on the left with the text. And then on the
     right, it's just on or off.").

     What went: the paragraph explaining how the panel's room is measured, the
     paragraph explaining the three switch states, the per-panel "Order:" line
     naming the ranking pass ("they don't need to know that"), the shown-of-total
     counts in the panel headings, the "On the slide" column and the "In the
     packet" numbering ("we don't need to number them").

     What stayed: everything that reports a PROBLEM. A panel that fitted nothing,
     a panel over the editorial cap, a switch that cannot fit and why, and a
     ranking answer that needed repair. Those are the lines a reviewer acts on. #}
  <h2>Slide 2 bullet selection</h2>
  {# Where a background switch reports. Empty on every page load: it describes
     the last switch pressed, and on a fresh render there has not been one. #}
  <p class="switch-status muted" role="status" aria-live="polite" style="margin:-8px 0 10px"></p>
  {% if switches_live %}
  {# NO BUTTON HERE ANY MORE, AND NOTHING IN ITS PLACE (2026-09-22). A switch
     changes the deck the moment it is pressed, so there is nothing left to
     ask for afterwards. "Render again with these switches" went with the
     /rerender route it posted to, and the line that replaced it earlier today
     ("the next render will build the deck with it") went too, because it is
     no longer what happens.

     What a switch still does BESIDES editing the deck is record itself
     against the PRD, which a later run from the Generate tab reads back. That
     is worth knowing and is not worth a line here: a reviewer watching the
     slide change has already been told the thing this card is for. #}
  {% endif %}
  {# One block per opportunity, because a deck carries a slide 2 each and a
     switch belongs to exactly one of them (Antonio, 2026-09-20). A deck with one
     opportunity renders one unlabelled block, which is what the card always was. #}
  {% for blk in bullet_switches %}
  {% if bullet_switches|length > 1 %}
  <h2 style="margin-top:20px;font-size:15px;color:var(--ink)">Opportunity {{ blk.index + 1 }}{% if blk.label %} · {{ blk.label }}{% endif %}</h2>
  {% endif %}
  {% for p in blk.panels %}
  {% set panel = p.panel %}
  {# 14px, not 22 (Antonio, 2026-09-21: "so much space between the bullet
     selection and the button, and then from the button to the bullet section").
     The button above it lost its own 18px top margin in the same review, so
     the two gaps that met here were nearly 40px of nothing. #}
  <h2 style="margin-top:14px;font-size:15px">{{ p.label }}</h2>
  {# A ranking pass that ran and was THROWN AWAY is the one ordering fact a
     reviewer can act on: these are the bullets that came first in the packet,
     not the ones a model judged most important. The other three branches said
     what the order was when nothing had gone wrong, which is the sentence he
     cut. #}
  {% if panel.rank_refused %}
  <p class="warn" style="margin-top:0">A ranking pass DID run on this panel and
  <b>its order was not applied</b>. {{ panel.rank_refused }}
  The bullets below are therefore whichever came first in the packet's list,
  not the ones a model judged most important.</p>
  {% endif %}
  {% if panel.overflowed %}
  <p class="bad">Not even one bullet fitted this panel's room. The top one is on the
  slide anyway, because an empty panel would hide that there was anything to show —
  expect the layout report above to name it as clipped.</p>
  {% endif %}
  {% if p.over_cap %}
  {# The five-bullet cap is an editorial judgment and a reviewer may pass it. It
     is stated rather than enforced: a reviewer looking at the slide can see what
     the panel is becoming, and the fitter cannot. #}
  <p class="warn">This panel is carrying more than {{ p.cap }} bullets, which is as
  many as it is meant to hold. It fits, and a panel this long reads as a list
  rather than a summary.</p>
  {% endif %}
  <table>
    {# The bullet and its switch. The switch's own position says whether the
       line is on the slide, which is what the "On the slide" column used to
       say in words (Antonio, 2026-09-20: "the only one that's on is going to be
       obvious that it's the one that was on"). The packet-position column went
       with the numbering. #}
    <tr><th>Bullet</th>{% if switches_live %}<th>Switch</th>{% endif %}</tr>
    {% for b in p.bullets %}
    <tr>
      <td{% if not b.shown %} class="muted"{% endif %}>{{ b.text }}</td>
      {% if switches_live %}
      <td style="white-space:nowrap">
        {# ONE SWITCH THAT SLIDES (Antonio, 2026-09-21: "the toggle bullets
           should be like a toggle, literally like a switch ... I don't want an
           on/off and off/on button ... it also makes it easier to see the ones
           that are already on because they're just toggled on, and they have
           the color").

           THE STATE MODEL IS STILL THREE-VALUED AND THAT IS NOT A CONTRADICTION.
           `auto` is the absence of a decision, not a third setting: a bullet
           nobody has switched is placed by the fitter. What the switch SHOWS is
           `b.shown`, which is whether the bullet is on the slide right now, and
           that is true under `auto` as well as under an explicit `on`. So the
           colour answers the question he asked it to answer ("which of these
           are on the slide") for every bullet, decided or not.

           What the switch POSTS is the opposite of what it shows, which is what
           a switch means. Flipping an `auto` bullet therefore writes an explicit
           decision, and `reset` is still the only way back to the fitter — kept,
           and only once there is something to undo.

           A SUBMIT BUTTON, NOT A CHECKBOX. Every switch is its own form post and
           has to keep working with no JavaScript; a checkbox that looks like a
           switch needs a script to submit, and the one that does not is a button
           with a track drawn around it. #}
        <form method="post" action="{{ url_for('bullet_toggle_route') }}" class="inline bullet-switch-form">
          {# The DOCUMENT this deck was written from, not the packet path. On a
             live run there is no packet file and this is the only stable name
             the switch can be filed under. #}
          <input type="hidden" name="scope" value="{{ toggle_scope }}">
          <input type="hidden" name="opportunity" value="{{ blk.index }}">
          <input type="hidden" name="role" value="{{ p.role }}">
          <input type="hidden" name="text" value="{{ b.text }}">
          <input type="hidden" name="deck_path" value="{{ deck_path }}">
          {% if b.blocked %}
          {# Cannot be switched on at any length the panel can hold, so the
             switch is off and disabled rather than absent: a missing control
             reads as a bug, a dead one reads as a reason, and the reason is in
             the title and spelled out under the row. #}
          <button class="switch is-off" type="submit" name="state"
            value="{{ switch_on }}" role="switch" aria-checked="false" disabled
            title="These bullets, at their lengths, do not fit this panel's {{ p.room_px }}px at {{ p.floor_px }}px, the smallest readable size. Switch another one off, or shorten the copy in the packet."><span class="knob"></span></button>
          {% else %}
          <button class="switch {{ 'is-on' if b.shown else 'is-off' }}" type="submit"
            name="state" value="{{ switch_off if b.shown else switch_on }}"
            role="switch" aria-checked="{{ 'true' if b.shown else 'false' }}"
            title="{% if b.shown %}On the slide. Switch off to drop this bullet.{% else %}Not on the slide. Switch on to add it.{% endif %}"><span class="knob"></span></button>
          {% endif %}
          {% if b.state != switch_auto %}
          <button class="ghost switch-reset" type="submit" name="state"
            value="{{ switch_auto }}"
            title="Forget this switch and hand the bullet back to the fitter.">reset</button>
          {% endif %}
        </form>
        {% if b.blocked %}
        <div class="muted" style="font-size:11px;max-width:230px;white-space:normal">
          Will not fit: this panel has {{ p.room_px }}px and what is switched on,
          plus this line, needs more than that even at {{ p.floor_px }}px.
        </div>
        {% endif %}
      </td>
      {% endif %}
    </tr>
    {% endfor %}
  </table>
  {% endfor %}
  {% endfor %}
  {% if sel.notes %}
  {# A repair means the ranking answer was salvaged rather than clean, which is the
     one thing that makes a recorded order less trustworthy. It is never allowed to
     change a bullet's text, only its place. #}
  <p class="warn" style="margin-top:18px">The ranking answer needed repair before it
  could be used ({{ sel.notes|length }}):</p>
  <ul>{% for n in sel.notes %}<li class="muted">{{ n }}</li>{% endfor %}</ul>
  {% endif %}
  {% if sel.not_applied %}
  {# A different fact from a repair, and kept visibly apart from one. A repair is
     the ranking pass salvaging its own answer; this is the answer being thrown
     away afterwards by the layer that maps the packet onto the slide. On a live
     WTG run that discard was silent and this card said "no ranking pass ran",
     which sent a reviewer looking at the wrong half of the pipeline. #}
  <p class="bad" style="margin-top:18px">A recorded order could not be applied to
  {{ sel.not_applied|length }} panel(s), so those panels show the packet's own
  order rather than the ranked one:</p>
  <ul>{% for n in sel.not_applied %}<li class="muted"><b>{{ n.panel }}</b> —
  {{ n.reason }}</li>{% endfor %}</ul>
  {% endif %}
</div>
{% endif %}
</div>
"""


RESULT_PANEL = """
{# A completed action (edit applied, flag resolved, edit undone) is good news and
   reads as one; only a refused action is a warning. Yellow on every notice made a
   successful resolve look like something had gone wrong. #}
{% if notice %}<div class="card"><p class="{{ 'bad' if notice_bad else 'ok' }}"
   style="white-space:pre-line;margin:0">{{ notice }}</p></div>{% endif %}

{# The cards that describe the RUN rather than the deck: the outcome, which
   document it was written from, how the framing lines came out, and what was
   attached. They used to sit above the deck, which is why the deck was not the
   first thing on screen (Antonio, 2026-09-20). Captured here and emitted at the
   bottom of the controls column: they are read once, after the deck has been
   judged, not acted with while it is. #}
{# The flagged-claims checklist, captured so it can be emitted right under the
   deck rather than at the bottom of the panel. It is the human-in-the-loop review
   gate, and it used to sit BELOW the "last resort" Claude Design handoff — so the
   one card a reviewer must work was under the one they should reach for least.
   Captured rather than duplicated because it also has to render when there is no
   deck on screen (a flag decision on a run whose deck has since been undone). #}
{# Slide 2's bullet selection. Captured the same way the flagged-claims checklist
   is, because it has to render whether or not a deck reached the screen: the
   selection happens while the prompt is assembled, so a run with the render leg
   off still made it.

   Why this card exists. Two steps stand between the packet's bullet lists and the
   slide: a model ranks them, and the fitter keeps as many as the panel holds.
   Both were being recorded and neither was reaching a human, so a reviewer read
   four bullets with no way to know the packet carried nine or that a model chose
   which four. A trim is only acceptable if it is visible. #}
{# WHAT WENT WRONG, and nothing about what went right.

   Antonio, 2026-09-20: "We can remove the run outcome box." He said it looking
   at a SUCCESSFUL run, where this card read `[proposal] [Contoso] status:
   ok` and told him nothing he could not see from the deck beside it. That much
   is gone.

   THE FAILURE BRANCHES STAY, because this card is the only place an error, a
   refusal or an escalation reaches the screen at all. Deleting it outright left
   a refused run rendering a blank Result tab, which is the opposite of the rule
   the refusals were written for: an insufficient PRD is its own answer and has
   to say so. Caught by 71 tests, not by reading.

   So: nothing on a clean run, the whole envelope on every other kind. #}
{% set run_info_cards %}
{% if result and status != 'ok' %}
<div class="card">
  <h2>{% if status == 'review' %}Escalated to human review{% else %}This run did not produce a deck{% endif %}</h2>
  <p>
    <span class="pill">{{ deck_type }}</span>
    <span class="pill">{{ company }}</span>
    {% if status == 'review' %}<b class="warn">no deck was written</b>
    {% else %}<b class="bad">{{ status }}</b>{% endif %}
  </p>

  {% if status == 'review' %}
    <p>The pipeline withheld a deck and routed to human review — the "never
    fabricate" guarantee. Missing or low-confidence data:</p>
    <table>
      <tr><th>confidence</th><td>{{ result.confidence }}</td></tr>
      <tr><th>data_completeness</th><td>{{ result.data_completeness }}</td></tr>
      <tr><th>missing_fields</th><td>{{ result.missing_fields }}</td></tr>
    </table>
  {% elif status == 'error' %}
    {# `details` is a dict on a provider envelope and an empty string on the two
       studio-side envelopes, so it is normalised before anything reads a key. #}
    {% set d = result.details if result.details is mapping else {} %}
    {% set failed_pass = d.get('second_pass_failed') %}
    {% set never_read = d.get('absent_after_failed_pass') or [] %}
    {% if failed_pass %}
    {# The lie this card exists to stop. A gate failure used to read as a verdict
       on the research paper, and on 2026-09-02 a live WTG run reported four
       baseline figures missing that the paper states in full, because the
       extraction pass had timed out. The reviewer's next move was to go and read
       the paper, which was the wrong place. So a score computed after a failed
       pass says so first, above the code and the figures. #}
    <p class="bad" style="margin-top:0"><b>A model call failed on this run, so the
    score below is not a verdict on the source data.</b> The second extraction
    pass did not complete ({{ failed_pass }}). Everything that pass was asked to
    read out of the source's prose stayed absent, which is why fields the source
    may well state are being reported as missing here. Re-run before going to
    look at the source: the pass is bounded and retried, and a re-run that fails
    the same way means the pass itself is broken rather than the data.</p>
    {% endif %}
    <table>
      <tr><th>code</th><td>{{ result.code }}</td></tr>
      {# pre-line: a guard message is a multi-line list of what broke, one item per
         line, and collapsing it into a paragraph makes it unreadable. #}
      <tr><th>message</th><td style="white-space:pre-line">{{ result.message }}</td></tr>
      <tr><th>remediation</th><td>{{ result.remediation }}</td></tr>
      {% if never_read %}
      {# The field names alone. Every entry carries the same sentence, so printing
         the reason once per field turned this row into ten identical paragraphs. #}
      <tr><th>never read</th><td>{% for entry in never_read %}<code>{{ entry.field }}</code>{% if not loop.last %}, {% endif %}{% endfor %}
        <div class="muted">Each of these was requested from the pass that failed,
        and each kept whatever reason the deterministic parsers had already given
        it.</div></td></tr>
      {% endif %}
      {# `provenance` is excluded for the same reason `absent_after_failed_pass`
         is: it has a card of its own below, and printed here it is a page of
         raw dict. #}
      <tr><th>details</th><td>{% if d %}{% for key, value in d|dictsort %}{% if key not in ('absent_after_failed_pass', 'provenance') %}{{ key }}: {{ value }}<br>{% endif %}{% endfor %}{% else %}{{ result.details }}{% endif %}</td></tr>
    </table>
  {% elif status == 'render_error' %}
    {# pre-line here too: a guard failure that reaches this branch is a multi-line
       list, and collapsing it into one paragraph made it a wall of field paths. #}
    <p class="bad" style="white-space:pre-line">Nothing was written. {{ result.message }}</p>
    {# A bounded model leg that gave up carries remediation of its own
       (`model_call.ModelCallError`, mapped in `_pipeline_result`). Older
       render-error envelopes carry none and this renders nothing. #}
    {% if result.remediation %}<p class="muted"
       style="white-space:pre-line">{{ result.remediation }}</p>{% endif %}
  {% endif %}
</div>
{% endif %}
{% endset %}

{# The bullet card is rendered SEPARATELY and handed in as `bullets_card`
   (2026-09-21), the same way `edit_cards` is. It was a `{% set %}` block here
   until a switch had to be able to repaint itself without reloading the page:
   a background post needs the card as a fragment it can return, and a fragment
   is something a route can render on its own. See `BULLETS_CARD`. #}

{# The cards that close the review: what the run applied, the box for a
   standing preference, and the two Claude Design prompts. Captured so the
   split layout can put them in the right-hand column beside the deck, and
   so the branches with no deck still emit them in their old place. #}
{% set tail_cards %}
{% if result and status == 'ok' %}
{# THE APPLIED-PREFERENCES CARD IS GONE (Antonio, 2026-09-21: "Remove the
   section on applied styling preferences"). It listed the standing notes a run
   had been given, which is a fact about the run rather than something a
   reviewer acts on, and it was one more card between the reviewer and the deck.

   The `applied` context value STAYS and is still filtered against the store on
   every render (`_build_result_ctx`). It is written to the run outcome and read
   back by `_recall_result_ctx`, so deleting the plumbing would lose a record of
   what shaped a deck; only the card is gone. #}

{# ONLY WHEN NO DECK WAS RENDERED (Antonio, 2026-09-20: "it says cloud design
   prompt and cloud design export prompt, so we don't need both of them ... just
   keep the one that you think is more important").

   The export prompt below is the one that is kept, because it carries the deck
   as it currently stands, edits included, while this one is the prompt the
   ORIGINAL render was built from and is wrong the moment anyone edits.

   It is not deleted outright, because with the render leg off this prompt IS
   the deliverable and the branch below says so. `deck_path` is exactly the
   condition that branch splits on, so the guard hands the card to the no-render
   path and to nothing else. #}
{% if not deck_path %}
<div class="card">
  <h2>Claude Design prompt{% if prompt_rel %} <span class="muted">(saved: {{ prompt_rel }})</span>{% endif %}</h2>
  {% if guards_stale %}
  <p class="warn">This is the prompt the original render was built from. Reviewer
  edits are applied to the HTML only, so the edits on screen are not in this
  prompt — pasting it into Claude Design reproduces the pre-edit deck.</p>
  {% endif %}
  <pre>{{ result.prompt }}</pre>
</div>
{% endif %}
{% endif %}

{% if design_export_prompt %}
<div class="card">
  <h2>Claude Design export prompt</h2>
  <p class="muted">The deck exactly as it currently stands, edits included.
  Copy this whole block and paste it into Claude Design to recreate it there
  for further editing.</p>
  <pre>{{ design_export_prompt }}</pre>
</div>
{% endif %}
{% endset %}

{% if deck_path %}
<div class="studio-split">
<div class="studio-deck">

<div class="card">
  {# THE DECK IS NAMED BY WHAT IT IS ABOUT (2026-09-23, demo polish). The
     heading was the run's own description ("Rendered deck", or "Rendered deck
     (from deck history, saved 2026-09-23 07:57:11.267000+00:00)"), which is
     the first line on the screen a founder reads. The project is the name the
     client knows the work by, so it leads, and the client, the deck type and
     how this copy was reached sit above it as a kicker. `deck_heading` is still
     what speaks when there is no project to name. #}
  <div class="deck-heading-row">
    <div class="deck-title-block">
      {%- set heading_note = deck_heading if raw_project and deck_heading != 'Rendered deck' else '' %}
      {%- if raw_company or deck_type or heading_note %}
      <div class="deck-kicker">
        {%- if raw_company %}<span>{{ raw_company }}</span>{% endif %}
        {%- if deck_type %}<span>{{ deck_type }} deck</span>{% endif %}
        {%- if heading_note %}<span>{{ heading_note }}</span>{% endif %}
      </div>
      {%- endif %}
      <h2>{{ raw_project or deck_heading }}</h2>
    </div>
    {# Folds the controls away so the deck runs nearly full width, and brings
       them back. Lives in the DECK column on purpose: a control inside the
       panel it hides would go with it. #}
    <button type="button" class="ghost side-toggle" id="side_toggle"
            onclick="toggleSide()">hide controls</button>
  </div>
  {% if result and result.render_fidelity %}
  {# ONE LINE, AND EVERY PART OF IT SOMETHING A REVIEWER CAN ACT ON
     (2026-09-20). It used to open with "every value and marker survived the
     render", which Antonio cut: "we don't need that ... I feel like that's
     unnecessary." He is right that a guard reporting success at length trains
     a reader to skip the line the one time it reports a failure, so silence is
     now what passing looks like and only a problem speaks.

     The layout half said "27 problem(s)". That was one slide overflowing,
     counted once per DOM element caught in it, and it read as 27 defects. It
     now names the DEFECT and its size, which is what tells a reviewer whether
     the deck can ship. See `_layout_defects`.

     And the count he actually wanted is here: he read the 27 AS a field count
     ("27 missing fields"), because nothing on the page reported one. #}
  {# CHIPS, NOT A SENTENCE (2026-09-23, demo polish). Same facts, same rule
     that only a problem speaks, but each one is its own chip with a dot in the
     colour of its state, so the line reads at a glance instead of being parsed.
     "Clean" is the one success that is shown, because it is a measurement a
     reviewer asked for, not a guard reporting on itself. #}
  <div class="status-chips">
    {%- if not result.render_fidelity.ok %}
      <span class="chip chip-warn">check the values below</span>
    {%- endif %}
    {%- if result.render_fidelity.missing_values %}
      <span class="chip chip-warn">value misses (report-only): {{ result.render_fidelity.missing_values }}</span>
    {%- endif %}
    {%- if missing_markers %}
      <span class="chip chip-warn">{{ missing_markers }} missing field{{ '' if missing_markers == 1 else 's' }} on the deck</span>
    {%- endif %}
    {%- if result.layout %}
      {%- if not result.layout.checked %}
        <span class="chip chip-warn">layout not measured ({{ result.layout.skipped }})</span>
      {%- elif result.layout.ok %}
        <span class="chip chip-ok">layout clean · {{ result.layout.slides }} slides measured</span>
      {%- else %}
        {%- for defect in layout_defects %}
          <span class="chip chip-bad">slide {{ defect.slide }} overflows by
          {{ defect.overflow_px|round|int }}px{% if defect.elements > 1 %}
          ({{ defect.elements }} elements){% endif %}</span>
        {%- endfor %}
      {%- endif %}
    {%- endif %}
  </div>
  {% if guards_stale %}
  {# Both guards ran on the model's render. A reviewer edit writes a new revision
     and neither guard re-runs, so reporting them without saying which file they
     describe would vouch for text nobody measured. #}
  <p class="warn">Both checks above ran on <code>{{ render_deck_name }}</code>, the
  original render. The copy on screen has reviewer edits applied since, which have
  not been re-checked for fidelity or for clipped text.</p>
  {% endif %}
  {% endif %}
  <div class="deck-frame-wrap">
    <div class="deck-viewport">
      <iframe class="deck-frame" src="{{ url_for('preview', path=deck_path) }}" title="Rendered deck preview"></iframe>
    </div>
  </div>
  {# THE DECK IS FIRST (2026-09-20). Antonio: "I also want those to be below
     the slide. The slide should be the first thing, so those buttons should be
     below the slide." The toolbar and the Save deck row used to sit above the
     frame, which cost the deck their height and was the "lot of space from
     there to where the deck actually starts" in the same review. One reorder
     answers both: nothing is above the deck now except the heading and the one
     status line. #}
  {# The toolbar's explanatory line is gone (2026-09-20): every pixel above the
     frame is a pixel the deck does not get, and "scaled to fit, scroll inside
     the frame" describes what the reader can already see happening. #}
  {# Outlined rather than three text links in a row (Antonio, 2026-09-20:
     "should also be outlined in some sort of blue ... they're kind of hard to
     see right now"). They stay <a> elements: they open a tab and they
     download, and a <button> does neither. #}
  <div class="deck-toolbar">
    <a class="linkbtn" href="{{ url_for('preview', path=deck_path) }}"
       target="_blank" rel="noopener">open full screen ↗</a>
    <a class="linkbtn" href="{{ url_for('download', path=deck_path) }}">download HTML</a>
    <a class="linkbtn" href="{{ url_for('download_pdf', path=deck_path) }}">download PDF</a>
  {# Deck history is a reviewer decision, not a side effect of rendering: this
     button is the only thing that writes a row. Not offered for a deck opened
     out of the store, which is by definition already saved. On this row rather
     than below it (2026-09-21): a second row under the deck is height the deck
     does not get, and `margin-left:auto` keeps it visibly a different kind of
     action from the three deliverable links. #}
  {% if not is_db_deck %}
  <form method="post" action="{{ url_for('save_deck_route') }}" class="inline save-deck-form">
    <input type="hidden" name="path" value="{{ deck_path }}">
    <input type="hidden" name="deck_type" value="{{ deck_type }}">
    <input type="hidden" name="company" value="{{ raw_company }}">
    <input type="hidden" name="project" value="{{ raw_project }}">
    <input type="hidden" name="packet" value="{{ packet }}">
    <input type="hidden" name="check_in_date" value="{{ check_in_date }}">
    <button type="submit" class="ghost" title="Deck history keeps the decks a reviewer saved, and nothing else. This writes the deck as it currently stands, edits and all; saving again updates the same row rather than adding another.">Save deck</button>
    {# Where a background save reports. The form still posts normally without
       JavaScript, which re-renders the page the way it always did. #}
    <span class="save-deck-status muted" role="status" aria-live="polite"></span>
    {# No blurb. It repeated the button's own `title` word for word, and this
       row sits under the deck where every line costs the deck height. #}
  </form>
  {% endif %}
  </div>
</div>

{# The deck column ends here and the controls column opens beside it. #}
</div>

<div class="studio-side">
{# The layout findings lead the controls column. They used to sit above the
   deck, which pushed the deck itself off the top of the screen; they are a
   list of places to go and look, so they belong beside the deck they point
   at rather than in front of it (Antonio, 2026-09-20). #}
{# Layout findings go ABOVE the deck: they are the one defect class a reviewer
   cannot spot by reading the HTML, and each one points at a slide to go look at. #}
{% if result and result.layout and result.layout.findings %}
<div class="card">
  <h2 class="bad">Layout problems ({{ result.layout.findings|length }})</h2>
  <p class="muted">Measured on the rendered deck in a headless browser. Each of
  these is content the deck CARRIES but a slide does not SHOW — text cut off inside
  its box, or pushed past the edge of a panel or the slide frame. No other guard can
  see this: the full text is still in the HTML, so the coverage and render-fidelity
  checks both pass. Fix the copy length or the scaffold's geometry, then re-run.</p>
  {% if guards_stale %}
  <p class="warn">Measured on <code>{{ render_deck_name }}</code>, the original
  render — not the edited copy on screen. Your edits have not been re-measured.</p>
  {% endif %}
  <table>
    <tr><th>Where</th><th>What is cut</th><th>Overflow</th></tr>
    {% for f in result.layout.findings %}
    <tr>
      <td>
        <b>{% if f.slide %}Slide {{ f.slide }}{% else %}Deck{% endif %}</b>
        <div class="muted" style="font-size:11px;margin-top:3px"><code>{{ f.element }}</code></div>
      </td>
      <td>
        {{ f.text }}
        <div class="muted" style="font-size:11px;margin-top:3px">
          {% if f.kind == 'clipped' %}clipped inside its own box ({{ f.axis }})
          {% else %}runs past the {{ f.side }} edge of <code>{{ f.container }}</code>{% endif %}
        </div>
      </td>
      <td style="white-space:nowrap">{{ f.overflow_px }}px</td>
    </tr>
    {% endfor %}
  </table>
</div>
{% endif %}

{# THE EDIT BOX LEADS (Antonio, 2026-09-20: "I think edit this deck box should
   go first. This is the most important box. And I think it should actually go
   before the toggle bullets.").

   Layout problems stay above it, which he did not ask for either way: they only
   appear when a slide is actually broken, and a defect report a reviewer has to
   scroll past the edit box to find is a defect report that gets missed. Nothing
   else is above the edit box on a clean run.

   Then the editing ladder (free text, supply-missing, history), then the bullet
   switches, then what closes the review. #}
{{ edit_cards | safe }}
{{ bullets_card | safe }}
{{ tail_cards }}
{{ run_info_cards }}
</div>
</div>
{% elif result and status == 'ok' %}
<div class="card">
  <h2>Deck not rendered</h2>
  <p class="muted">Render leg was off (no API key or unchecked). The Design
  prompt below is the deliverable; enable rendering to produce the HTML deck.</p>
</div>
{# The selection happened while the prompt was assembled, so it describes the
   Design prompt below just as it would have described a deck. #}
{{ bullets_card | safe }}
{{ tail_cards }}
{{ run_info_cards }}
{% else %}
{{ bullets_card | safe }}
{{ tail_cards }}
{{ run_info_cards }}
{% endif %}

"""


# The editability surface. Four cards:
#   1. Free-text edit — the primary edit control for a plain-language request
#      (Antonio, 2026-07-21). A Claude call (html_edit_interpreter) turns it into
#      exact content swaps, applied deterministically by html_edit_layer with no
#      re-render and no drift. The run context (packet, deck_type, ...) rides
#      along on hidden fields so the Result view stays coherent after the edit.
#   2. Attach a note to a slide — a reviewer-named exact swap (no Claude call),
#      posted to the same deterministic /edit route the supply-missing fill
#      below uses. Classified content or format; only a format note offers the
#      promote-to-preference checkbox, because only a format note may be learned
#      (`preference_store` is format-only by design — C5, 2026-08-09).
#   3. Supply missing values — the `[MISSING: ...]` markers the render left,
#      offered as fill fields. Filling one writes the value onto the slide (a
#      content edit); it is never written back to the packet, so the
#      never-fabricate guarantee holds. Posts to the deterministic
#      /supply-missing route.
#
#      Three cards, not one (2026-08-20). The single list conflated a value the
#      SOURCE failed to provide with one we deliberately require a human to
#      supply, and on the live proposal deck the second kind outnumbered the
#      first five to one — a list where 83% of the rows are working as intended
#      teaches a reviewer to skip it, which defeats its only job. `missing_values`
#      splits them by what the template declares about each field, and each card
#      counts its own MARKERS so the three headings still add up to what is on
#      the deck.
#   4. Claude Design handoff — the last-resort rung for a change in-UI editing
#      cannot make (layout, styling). Hands the raw HTML to Claude Design.


# The heading and the standing explanation for each group of the supply-missing
# split. Copy, not logic: the classes come from `missing_values`, which reads the
# templates' own declarations, and a class with no entry here would render an
# unlabeled card rather than change what is in it.
MISSING_GROUP_CARDS = {
    FILL_SOURCE: {
        "heading": "Supply missing values",
        "blurb": (
            "The real gaps. Each is a <code>[MISSING: ...]</code> marker the render "
            "left because the data source was asked for this value and had none. "
            "This is the list to work through."
        ),
    },
    FILL_REVIEWER: {
        "heading": "Values we expect you to supply",
        "blurb": (
            "Nothing went wrong here. Each of these is a field the deck asks a "
            "human for and never asks a source for — a date, an owner, a target "
            "week — so a marker means the input has not been given yet, not that "
            "anything is missing from the data."
        ),
    },
    FILL_SENSITIVE: {
        "heading": "Commercial terms, entered by hand",
        "blurb": (
            "Deliberately not sourced. These are QofAI's own per-deal figures, and "
            "the platform is not meant to supply them, so the deck says "
            "<b>AWAITING COMMERCIAL TERMS INPUT</b> where each belongs rather than "
            "leaving a blank that hides the fact a figure goes there. Filling them "
            "here works exactly like any other value; a dedicated section for them "
            "is not built yet."
        ),
    },
}


EDIT_CARDS = """
{% if db_mode %}
<div class="card">
  <h2>Editing is off for a deck opened from history</h2>
  <p class="muted">This deck is being served out of the store because the file it
  was rendered to is gone (a redeploy wipes the filesystem the database survives).
  Its HTML and its edit history below are what the reviewer saved. Re-run the
  pipeline to get an editable copy.</p>
</div>
{% else %}
<div class="card edit-card">
  <h2>Edit this deck</h2>
  {# One sentence (Antonio, 2026-09-20: "We can literally just keep 'say what
     you want changed in plain language.' That's it."). The paragraph that was
     here described the mechanism, which a reviewer does not act on. #}
  <p class="muted">Say what you want changed in plain language.</p>
  {% if not has_key %}
  <p class="warn">ANTHROPIC_API_KEY not set — the free-text edit is disabled. Use
  the supply-missing fields below, or set a key.</p>
  {% endif %}
  <form method="post" action="{{ url_for('edit_ai') }}" data-keep-slide data-progress="edit">
    <input type="hidden" name="path" value="{{ deck_path }}">
    <input type="hidden" name="deck_type" value="{{ ctx_deck_type }}">
    <input type="hidden" name="company" value="{{ ctx_company }}">
    <input type="hidden" name="project" value="{{ ctx_project }}">
    <input type="hidden" name="packet" value="{{ ctx_packet }}">
    <input type="hidden" name="check_in_date" value="{{ ctx_check_in_date }}">
    {# NO LABEL ON THE TEXTAREA (Antonio, 2026-09-21: "let's just remove the
       'What should change?' part completely"). The card's own heading and the
       one line under it already say what this box is for, and the placeholder
       shows the shape of an answer. #}
    <textarea name="instruction" style="min-height:88px"
      placeholder="e.g. On slide 2 change the revenue figure to $42M, and fix the typo 'recieve' in the closing slide."
      {{ 'disabled' if not has_key else '' }}></textarea>
    {# AUTHOR AND THE BUTTON SHARE A LINE (Antonio, 2026-09-21: "so much empty
       space below 'Author optional' ... so much space above 'Author optional'
       as well"). Stacked, the label's 14px top margin, the input, and the
       button's own 18px top margin were three gaps for two controls. Side by
       side they are one row, the author field is as narrow as a name needs,
       and the button sits where the eye already is. #}
    <div class="edit-submit-row">
      <div class="edit-author">
        <label for="edit_author">Author <span class="muted">(optional)</span></label>
        <input type="text" name="author" id="edit_author" placeholder="reviewer name">
      </div>
      <button type="submit" {{ 'disabled' if not has_key else '' }}>Apply edit</button>
    </div>
  </form>
</div>

{% endif %}

{# THE OLD COMMERCIAL EDITORS ARE GONE (Antonio, 2026-09-23: "the old
   commercial terms edits ... were not relevant anymore"). The "Commercial
   terms" form and the "How payment works" card wrote the three-case chart and
   the payment-mechanism regions of the slide the adaptive deal sheet
   (`build-plan-commercial-slide.md`) replaced. A deck rendered since then has
   neither region, so both cards only ever appeared on an older saved deck,
   where they offered to edit a slide design that no longer ships. The routes
   behind them (`/commercial-terms`, `/commercial-defaults`) are left for that
   plan's Phase B to delete with its new editor; nothing on this page reaches
   them. The "Commercial terms, entered by hand" missing-values card below stays. #}

{% for group in group_order %}
{% if marker_groups[group] and not db_mode %}
<div class="card">
  <h2>{{ group_cards[group].heading }} ({{ marker_counts[group] }})</h2>
  <p class="muted">{{ group_cards[group].blurb|safe }}</p>
  <p class="muted">Supplying one writes the value onto the slide as a
  reviewer-provided edit for <b>this deck only</b> — it is never written back to the
  packet and never learned as a preference, so the deck stays a faithful record with
  a visible, logged human edit.</p>
  {% if group == reviewer_group and flag_audit %}
  {# STATED BUT NOT READ (Part A4, 2026-09-23). The flags the document seems to
     answer are our reader's gap, not the document's, so they are named before
     the reviewer clears them. #}
  <div class="flag-audit">
    <p class="warn"><b>The document seems to state {{ flag_audit|length }} of
    these, and our reader missed {{ 'it' if flag_audit|length == 1 else 'them' }}.</b>
    Clearing {{ 'it' if flag_audit|length == 1 else 'them' }} hides our gap, not the
    document's; worth reporting so the reader is fixed.</p>
    <ul class="flag-audit-list">
      {% for f in flag_audit %}
      <li>{% if f.opportunity %}{{ f.opportunity }} · {% endif %}step {{ f.number }}
        {{ f.title }}: <code>{{ f.field }}</code></li>
      {% endfor %}
    </ul>
  </div>
  {% endif %}
  {% if group != sensitive_group %}
  {# CLEAR (Antonio, 2026-09-23): "maybe there is no owner, or there is no
     designated week ... you press ... and then it just goes away." Takes every
     marker in this card off the slide as ONE revision, with the separator each
     leaves tidied. No value is written and nothing is remembered: the next
     render of the same document shows the flag again. Never offered on the
     commercial-terms card, and `/clear-flags` refuses those markers anyway. #}
  <form method="post" action="{{ url_for('clear_flags') }}" data-keep-slide class="inline clear-flags-form">
    <input type="hidden" name="path" value="{{ deck_path }}">
    {% for row in marker_groups[group] %}{% for t in row.targets %}
    <input type="hidden" name="slide" value="{{ t.slide }}">
    <input type="hidden" name="source" value="{{ t.source }}">
    <input type="hidden" name="occurrence" value="{{ t.occurrence }}">
    <input type="hidden" name="occurrences" value="{{ t.occurrences }}">
    {% endfor %}{% endfor %}
    <input type="hidden" name="deck_type" value="{{ ctx_deck_type }}">
    <input type="hidden" name="company" value="{{ ctx_company }}">
    <input type="hidden" name="project" value="{{ ctx_project }}">
    <input type="hidden" name="packet" value="{{ ctx_packet }}">
    <input type="hidden" name="check_in_date" value="{{ ctx_check_in_date }}">
    <button class="ghost" type="submit">Clear all {{ marker_counts[group] }}</button>
    <span class="muted">for values the document does not state</span>
  </form>
  {% endif %}
  {# `fill-table`: each row stacks in the narrow column (2026-09-23). Three
     table cells side by side left the field cell about 60px wide, so "1 of 5 on
     slide 9" wrapped onto four lines and pushed "Write onto slide" and "Clear"
     past the column's right edge, where the column's own `overflow-x: hidden`
     cut them off. #}
  <table class="fill-table">
    <tr><th>Slide</th><th>Field</th><th>Value to write</th></tr>
    {% for row in marker_groups[group] %}
    <tr>
      <td>{{ row.slides|join(', ') }}</td>
      <td><code>{{ row.field }}</code>{% if row.deck_wide and row.count > 1 %}
        <span class="muted"> — one value, {{ row.count }} places</span>
        {%- elif row.targets[0].occurrences > 1 %}
        {# Which of several identical markers this row is. The deck is on the same
           page and these rows are in document order, so the position is how a
           reviewer maps a row to the slot they can see. #}
        <span class="muted"> — {{ row.targets[0].occurrence }} of {{ row.targets[0].occurrences }} on slide {{ row.targets[0].slide }}</span>
        {%- endif %}</td>
      <td>
        <form method="post" action="{{ url_for('supply_missing') }}" data-keep-slide class="inline">
          <input type="hidden" name="path" value="{{ deck_path }}">
          <input type="hidden" name="field" value="{{ row.field }}">
          {# One (slide, source, occurrence) triple per place this value has to
             reach. A deck-wide field posts several and they are applied together
             or not at all; every other row posts exactly one. The occurrence is
             what lets a repeated marker be aimed at, so it rides along with the
             pair rather than being inferred at the far end. #}
          {% for t in row.targets %}
          <input type="hidden" name="slide" value="{{ t.slide }}">
          <input type="hidden" name="source" value="{{ t.source }}">
          <input type="hidden" name="occurrence" value="{{ t.occurrence }}">
          <input type="hidden" name="occurrences" value="{{ t.occurrences }}">
          {% endfor %}
          {# The same run context the free-text form forwards. Without it the
             route sees no packet, so filling one missing value dropped the whole
             flagged-claims checklist off the page. #}
          <input type="hidden" name="deck_type" value="{{ ctx_deck_type }}">
          <input type="hidden" name="company" value="{{ ctx_company }}">
          <input type="hidden" name="project" value="{{ ctx_project }}">
          <input type="hidden" name="packet" value="{{ ctx_packet }}">
          <input type="hidden" name="check_in_date" value="{{ ctx_check_in_date }}">
          <input type="text" name="replacement" placeholder="supplied value"
                 class="fill-input">
          <button class="ghost" type="submit">Write onto slide</button>
        </form>
        {% if group != sensitive_group %}
        <form method="post" action="{{ url_for('clear_flags') }}" data-keep-slide class="inline clear-flags-form">
          <input type="hidden" name="path" value="{{ deck_path }}">
          {% for t in row.targets %}
          <input type="hidden" name="slide" value="{{ t.slide }}">
          <input type="hidden" name="source" value="{{ t.source }}">
          <input type="hidden" name="occurrence" value="{{ t.occurrence }}">
          <input type="hidden" name="occurrences" value="{{ t.occurrences }}">
          {% endfor %}
          <input type="hidden" name="deck_type" value="{{ ctx_deck_type }}">
          <input type="hidden" name="company" value="{{ ctx_company }}">
          <input type="hidden" name="project" value="{{ ctx_project }}">
          <input type="hidden" name="packet" value="{{ ctx_packet }}">
          <input type="hidden" name="check_in_date" value="{{ ctx_check_in_date }}">
          <button class="ghost" type="submit">Clear</button>
        </form>
        {% endif %}
      </td>
    </tr>
    {% endfor %}
  </table>
</div>
{% endif %}
{% endfor %}

{% if progress_items and not db_mode %}
<div class="card">
  <h2>Progress items ({{ progress_items|length }})</h2>
  <p class="muted">Correct a checkbox the render got wrong (the reviewer's most
  common correction: "that one is actually done"). This changes the deck's
  display only for <b>this deck</b> — it is never written back to the packet,
  which stays immutable, so the deck may now disagree with the data it was
  built from. That disagreement is recorded below like any other edit.</p>
  <table>
    <tr><th>Slide</th><th>Item</th><th>State</th><th>Set to</th></tr>
    {% for p in progress_items %}
    <tr>
      <td>{{ p.slide }}</td>
      <td>{{ p.label }}</td>
      <td>{{ p.state }}</td>
      <td>
        <form method="post" action="{{ url_for('toggle_progress') }}" data-keep-slide class="inline">
          <input type="hidden" name="path" value="{{ deck_path }}">
          <input type="hidden" name="slide" value="{{ p.slide }}">
          <input type="hidden" name="label" value="{{ p.label }}">
          <input type="hidden" name="deck_type" value="{{ ctx_deck_type }}">
          <input type="hidden" name="company" value="{{ ctx_company }}">
          <input type="hidden" name="project" value="{{ ctx_project }}">
          <input type="hidden" name="packet" value="{{ ctx_packet }}">
          <input type="hidden" name="check_in_date" value="{{ ctx_check_in_date }}">
          <select name="new_state">
            <option value="pending" {{ 'selected' if p.state=='pending' else '' }}>pending</option>
            <option value="in_process" {{ 'selected' if p.state=='in_process' else '' }}>in process</option>
            <option value="done" {{ 'selected' if p.state=='done' else '' }}>done</option>
          </select>
          <button class="ghost" type="submit">Update</button>
        </form>
      </td>
    </tr>
    {% endfor %}
  </table>
</div>
{% endif %}

{% if edit_log %}
<div class="card">
  <h2>Edit history ({{ edit_log|length }})</h2>
  <p class="muted">Every human edit on this deck, oldest first. The render-fidelity
  audit trail: what changed, on which slide, by whom.</p>
  {% if not db_mode %}
  <form method="post" action="{{ url_for('undo') }}" data-keep-slide class="inline" style="margin-bottom:12px">
    <input type="hidden" name="path" value="{{ deck_path }}">
    <input type="hidden" name="deck_type" value="{{ ctx_deck_type }}">
    <input type="hidden" name="company" value="{{ ctx_company }}">
    <input type="hidden" name="project" value="{{ ctx_project }}">
    <input type="hidden" name="packet" value="{{ ctx_packet }}">
    <input type="hidden" name="check_in_date" value="{{ ctx_check_in_date }}">
    <button class="ghost" type="submit">↶ Undo last edit</button>
    <span class="muted">Removes the most recent revision and reverts to the one before it.</span>
  </form>
  {% endif %}
  <table>
    <tr><th>Revision</th><th>Slide</th><th>Kind</th><th>Before → After</th><th>Author</th><th>When</th></tr>
    {% for e in edit_log %}
    <tr>
      <td><code>{{ e.revision }}</code></td>
      <td>{{ e.slide }}</td>
      <td>{{ e.kind }}</td>
      <td><code>{{ e.before }}</code> → <code>{{ e.after or '(deleted)' }}</code></td>
      <td class="muted">{{ e.author or '—' }}</td>
      <td class="muted">{{ e.created }}</td>
    </tr>
    {% endfor %}
  </table>
</div>
{% endif %}

<div class="card">
  <h2>Export Claude Design prompt</h2>
  <p class="muted">Turns the deck exactly as it currently stands, edits
  included, into one self-contained prompt below — nothing to attach
  separately, just copy it into Claude Design to pick up editing there and
  bring it back.</p>
  <form method="post" action="{{ url_for('export_design_prompt') }}" class="inline">
    <input type="hidden" name="path" value="{{ deck_path }}">
    <input type="hidden" name="deck_type" value="{{ ctx_deck_type }}">
    <input type="hidden" name="company" value="{{ ctx_company }}">
    <input type="hidden" name="project" value="{{ ctx_project }}">
    <input type="hidden" name="packet" value="{{ ctx_packet }}">
    <input type="hidden" name="check_in_date" value="{{ ctx_check_in_date }}">
    <button type="submit">Export Claude Design prompt</button>
  </form>
</div>
"""


# ------------------------------- rendering ---------------------------------

def _generate_panel_html():
    return render_template_string(
        GENERATE_PANEL, fixtures=_load_fixtures(), has_key=_has_api_key(),
        opportunity_placeholder=OPPORTUNITY_PLACEHOLDER,
        attachment_field=ATTACHMENT_FIELD,
        attachment_accept=_accepted_attachment_types(),
        attachment_max_mb=_megabytes(MAX_ATTACHMENT_BYTES),
        attachment_max_count=MAX_ATTACHMENTS,
        # Both date fields default to today (Antonio, 2026-09-20). Filled
        # server-side rather than by script so the value is there when the page
        # arrives and the field does not flicker, and so it still works with
        # JavaScript off. A default, not a decision: both fields are editable
        # and the run uses whatever is in them.
        today=date.today().isoformat(),
    )


def _decks_panel_html(notice="", notice_bad=False):
    """The Decks tab: the store's history of every session's decks when it is
    readable, or today's filesystem listing (with a visible reason) when it is
    not. A delete outcome passed in as ``notice`` always wins over that reason,
    so a "here's why there's no history" line never buries "deleted."
    """
    resolution = resolve_store_dir()
    db_mode = False
    decks = []
    try:
        decks = list_decks_from_store(store_path=resolution.store_path)
        db_mode = True
    except Exception as exc:
        decks = list_recent_decks()
        if not notice:
            notice, notice_bad = (
                f"Deck history is not persisting (the store at "
                f"{resolution.directory} could not be read: {exc}). Showing "
                "decks saved to the filesystem on this machine instead.", True)
    return render_template_string(
        DECKS_PANEL, decks=decks, db_mode=db_mode, decks_root=_display_root(DECKS_ROOT),
        notice=notice, notice_bad=notice_bad, _db_deck_prefix=_DB_DECK_PREFIX,
    ), len(decks)


def _preferences_panel_html():
    store = load_store(STORE_PATH)
    return render_template_string(
        PREFERENCES_PANEL,
        prefs=store.get("preferences", []),
        scopes=VALID_SCOPES,
    )


# Fill expectations per deck type, read once from the templates. A template is a
# committed file that does not change under a running studio, so it is read at
# first use and kept; a read failure degrades to "nothing declared", which puts
# every marker in the source-gap group — the conservative direction, the one that
# asks a reviewer to look rather than telling them not to.
_FILL_EXPECTATIONS = {}


def _fill_expectations_for(deck_type):
    """What each field name on a deck of this type expects, keyed by field name.

    ``deck_type`` is whatever the run context carried. For a type with a template,
    that template's declarations answer — and only that template's, because a
    field name means what the deck it belongs to says it means (`week` is a
    reviewer-supplied commitment under a proposal's next steps and a sourced
    schedule fact under a status deck's slip markers). With no usable deck type,
    every template's declarations are merged and any name they disagree on
    collapses to a source gap, which is `fill_expectations`' own rule applied one
    level up.
    """
    if deck_type in _FILL_EXPECTATIONS:
        return _FILL_EXPECTATIONS[deck_type]
    paths = ([DECK_TYPE_TEMPLATE_PATHS[deck_type]]
             if deck_type in DECK_TYPE_TEMPLATE_PATHS
             else list(DECK_TYPE_TEMPLATE_PATHS.values()))
    declared, failed = [], False
    for path in paths:
        try:
            declared.append(fill_expectations(load_template(path)))
        except Exception:
            failed = True
    merged = merge_expectations(declared)
    # A partial read is not cached: a template that could not be opened this once
    # would otherwise leave the studio degraded until it restarts.
    if not failed:
        _FILL_EXPECTATIONS[deck_type] = merged
    return merged


def _edit_cards_html(deck_path, *, run_ctx=None, flag_audit=None):
    """Render the editability cards for one deck.

    ``run_ctx`` (optional) carries the originating run parameters (packet,
    deck_type, company, project, check_in_date) so the free-text edit form can
    forward them and keep the Result view coherent after an edit. Returns ``""``
    when the deck cannot be read, so a caller can embed it unconditionally.

    A `db-deck:` sentinel has no file behind it, so its HTML and its edit chain
    come out of the stored row instead. That chain is the point of saving a deck
    as it currently stands, and it would be invisible here otherwise. The forms
    that act on a file — the free-text edit, the supply-missing fills, and undo —
    are dropped in that mode rather than offered against a deck that is gone.
    """
    db_id = _db_deck_id(deck_path)
    if db_id is not None:
        try:
            row = get_deck(db_id)
        except Exception:
            row = None
        if not row:
            return ""
        html = row.get("html") or ""
        edit_log = (row.get("details") or {}).get("edits") or []
    else:
        try:
            with open(deck_path, encoding="utf-8") as f:
                html = f.read()
        except OSError:
            return ""
        edit_log = load_edit_log(deck_path)
    run_ctx = run_ctx or {}
    # The three groups the supply-missing card splits into, and the marker (not
    # row) count per group, so the headings still add up to what is on the deck.
    marker_groups = group_missing_markers(
        list_missing_markers(html),
        _fill_expectations_for(run_ctx.get("deck_type", "")),
    )
    counts = marker_counts(marker_groups)
    commercial_regions = read_commercial_regions(html)
    # Whether the button would overwrite rather than fill. A region holding its
    # `[MISSING: ...]` marker is an empty slot; anything else is content someone
    # or something already put there, and replacing that silently is the kind of
    # surprise a reviewer should be warned about before they click.
    would_replace = bool(commercial_regions) and any(
        text and not text.startswith("[MISSING:")
        for text in commercial_regions.values())
    # The terms form's state, read off the slide so it reloads with what is there.
    # The standing clause pre-fills the blue box when the deck carries none; a
    # defaults file that cannot be read just means one fewer pre-filled field, so
    # it degrades rather than taking the form down with it.
    try:
        standing_downside = load_defaults()["downside_protection"]
    except DefaultsUnavailable:
        standing_downside = ""
    # None when the slide's five regions are not all on the deck, which is how a
    # status deck answers. The form is then not built at all rather than built
    # blank: `form_state` happily returns an empty form for a caller that wants
    # one, and offering that here would put twelve inputs in front of a reviewer
    # with nowhere for the values to go.
    on_deck = read_commercial_terms(html)
    terms_form = (commercial_form_state(on_deck, downside=standing_downside)
                  if on_deck is not None else None)
    return render_template_string(
        EDIT_CARDS,
        deck_path=deck_path,
        db_mode=db_id is not None,
        slides=slide_count(html),
        marker_groups=marker_groups,
        marker_counts=counts,
        group_order=GROUP_ORDER,
        group_cards=MISSING_GROUP_CARDS,
        sensitive_group=FILL_SENSITIVE,
        reviewer_group=FILL_REVIEWER,
        # Part A4: the Next Steps gaps the document seems to state, from the
        # run this deck came from (or its saved record). Empty for a deck no
        # run or record speaks for, which then shows the plain list.
        flag_audit=(flag_audit if flag_audit is not None else
                    (_run_outcome_for(deck_path).get("result") or {})
                    .get("flag_audit") or []),
        progress_items=list_progress_items(html),
        # The two commercial regions and what they read as now, or None on a deck
        # that has no Commercial Terms slide. Read off the DECK rather than off a
        # deck-type name, so the offer appears exactly where there is somewhere to
        # write it and the studio needs no second opinion about which types have
        # the slide.
        commercial_regions=commercial_regions,
        commercial_defaults_would_replace=would_replace,
        # Always three cases, filled from whatever the slide carries, so a base
        # case the render never emitted is a box a reviewer can simply type into.
        terms_form=terms_form,
        edit_log=edit_log,
        has_key=_has_api_key(),
        ctx_deck_type=run_ctx.get("deck_type", ""),
        ctx_company=run_ctx.get("company", ""),
        ctx_project=run_ctx.get("project", ""),
        ctx_packet=run_ctx.get("packet", ""),
        ctx_check_in_date=run_ctx.get("check_in_date", ""),
    )


def _live_preference_notes():
    """The notes currently in the standing-preferences store (active or not), or
    ``None`` when the store cannot be read.

    ``None`` means "unknown", so a caller filtering against this leaves its list
    alone rather than blanking it on a transient read failure.
    """
    try:
        store = load_store(STORE_PATH)
    except Exception:
        return None
    return {p.get("note") for p in store.get("preferences", [])}


def _bullets_card_html(ctx):
    """Slide 2's bullet card on its own, from a Result-tab context.

    Rendered separately so `/bullet-toggle` can post in the background and swap
    the card in place rather than reloading the page under the reviewer.
    """
    return render_template_string(BULLETS_CARD, **ctx)


def _result_panel_html(ctx):
    """Render the Result panel from a context dict, or the empty state when None."""
    if not ctx:
        return RESULT_EMPTY
    # The bullet card is a fragment (see `BULLETS_CARD`), so it is rendered
    # first and handed in, exactly as `edit_cards` is.
    return render_template_string(RESULT_PANEL,
                                  bullets_card=_bullets_card_html(ctx), **ctx)


def render_studio(active_tab="generate", *, result_ctx=None, decks_notice="",
                  decks_notice_bad=False, run_id=""):
    """Render the whole three-tab studio with the given tab active.

    ``decks_notice`` is the Decks tab's own one-line banner (a deck deleted, or a
    delete that could not be carried out); it has nothing to do with the Result
    panel's notice and is not cached, so it shows once and is gone on the next load.
    """
    if result_ctx is not None:
        _remember_result(result_ctx)
    result_html = _result_panel_html(result_ctx)
    decks_html, deck_count = _decks_panel_html(decks_notice, decks_notice_bad)
    rendered = render_template_string(
        BASE,
        generate_panel=_generate_panel_html(),
        decks_panel=decks_html,
        preferences_panel=_preferences_panel_html(),
        result_panel=result_html,
        active_tab=active_tab,
        deck_count=deck_count,
        opportunity_placeholder=OPPORTUNITY_PLACEHOLDER,
        # The shared script looks the file input up by this id. Rendered without
        # it, `getElementById('')` returned null and BOTH the attachment list
        # and the PRD scan returned at their first guard — silently, because a
        # guard is how they degrade when the Generate tab is not the one on
        # screen. That is why an attached PRD filled nothing (2026-09-20).
        attachment_field=ATTACHMENT_FIELD,
        run_poll_ms=RUN_POLL_MS,
        # What runs on THIS machine have actually taken, per shape, so the
        # overlay quotes measured history instead of a constant nobody has
        # re-measured. `{}` until three runs of a shape exist, and the client
        # falls back to its own leg table there rather than inventing a number.
        run_history_json=json.dumps(
            {shape: est for shape, est in
             ((s, _duration_estimate(s)) for s in
              ("live-render", "live", "fixture-render", "fixture"))
             if est}),
    )
    # The initial-tab hint the client script reads (kept out of the Jinja body so
    # the same BASE serves every screen). `data-run-id`, when present, is a run
    # still working: the script opens the progress overlay and polls for it, which
    # is what survives a reload mid-render.
    attrs = f'data-active-tab="{active_tab}"'
    if run_id:
        attrs += f' data-run-id="{escape(run_id)}"'
    return rendered.replace("<body>", f"<body {attrs}>", 1)


def _layout_defects(layout):
    """Layout findings grouped by ROOT CAUSE, worst first, or [].

    Antonio, 2026-09-20, reading "27 problem(s)": "the 27 problems, I assume,
    are the missing fields ... problems is too strong of a word."

    Both halves of that were worth answering and neither was answered by
    renaming it. The 27 were not missing fields at all, they were the layout
    guard measuring geometry; and they were not 27 defects, they were ONE --
    slide 6 running 318.9px past the bottom of its frame -- reported once per
    DOM element caught in it: the `div.step`, the `h3`, the `p`, the
    `span.tag`, each separately. A count of elements reads as a count of
    problems, which is why a single overflow looked like a catastrophe and why
    a genuinely broken deck would not have stood out from it.

    So this groups by (slide, container, side): one entry per thing actually
    wrong, carrying the worst overflow in it and how many elements it caught.
    `_element_count` is kept rather than dropped because it is the honest
    measure of how much of the slide is affected.
    """
    groups = {}
    for finding in layout.get("findings") or ():
        key = (finding.get("slide"), finding.get("container"),
               finding.get("side"))
        try:
            overflow = float(finding.get("overflow_px") or 0)
        except (TypeError, ValueError):
            overflow = 0.0
        held = groups.setdefault(key, {"slide": finding.get("slide"),
                                       "kind": finding.get("kind"),
                                       "side": finding.get("side"),
                                       "overflow_px": 0.0, "elements": 0})
        held["overflow_px"] = max(held["overflow_px"], overflow)
        held["elements"] += 1
    return sorted(groups.values(), key=lambda g: -g["overflow_px"])


def _missing_marker_count(deck_path):
    """How many `[MISSING: ...]` markers the deck on screen actually shows.

    The number Antonio was looking for when he read the layout count as a field
    count, and nothing was reporting it. Counted off the RENDERED deck rather
    than off the packet's `missing_fields`, because a packet gap that no slide
    renders is not something a reviewer can see, and one that renders twice is
    two marks on the page.
    """
    try:
        with open(deck_path, encoding="utf-8") as fh:
            return fh.read().count("[MISSING:")
    except OSError:
        return 0


def _build_result_ctx(*, result=None, status=None, deck_type="", company="",
                      applied=None, deck_path=None, prompt_rel="", gaps=None,
                      resolved=None,
                      show_gaps=False, notice="", notice_bad=False,
                      raw_company="", raw_project="", packet="", check_in_date="",
                      do_render=False, deck_heading="Rendered deck",
                      render_deck_path="", guards_stale=False,
                      design_export_prompt=""):
    """Assemble the Result-panel context dict from named parts.

    Keeps every route rendering the same panel from one shape, so the panel can be
    produced after a full pipeline run, after a no-render gap decision, or after a
    deterministic edit, with each section guarding on the keys it needs.
    """
    run_ctx = {
        "deck_type": deck_type,
        "company": raw_company,
        "project": raw_project,
        "packet": packet,
        "check_in_date": check_in_date,
    }
    # The run's own audit, handed over rather than looked up: on the run's first
    # page this is built before `_LAST_RESULT` holds the new result.
    audit = (result or {}).get("flag_audit") if isinstance(result, dict) else None
    edit_cards = (_edit_cards_html(deck_path, run_ctx=run_ctx, flag_audit=audit)
                  if deck_path else "")
    # Which document filled which field, normalised out of the two places it can
    # arrive from. A clean run carries it beside the packet; a run the
    # completeness gate refused carries it in the error's own details, which is
    # where it matters most (an attachment stating a partial baseline is exactly
    # what drops a run below the floor). Empty for a run with no attachment, and
    # the panel renders nothing at all for an empty one.
    provenance = {}
    if isinstance(result, dict):
        provenance = result.get("provenance") or {}
        if not provenance and isinstance(result.get("details"), dict):
            provenance = result["details"].get("provenance") or {}
    # What the deck was built from (item 14, step 5): one record per attachment,
    # carrying the extracted text plus the filename, kind, size and SHA-256 of
    # the file. A live run carries it beside the packet; a deck reopened out of
    # the store carries it back out of its own `details`, which is the whole
    # point of saving it. Empty for a run with no attachment, and the card
    # renders nothing at all for an empty one.
    attachments = []
    if isinstance(result, dict):
        attachments = result.get("attachments") or []
    # WHICH FRAMING LINES WERE WRITTEN AND WHICH REFUSED (item 24). Read the way
    # `provenance` is, from both places it can arrive: a clean run carries it
    # beside the packet, and a run the completeness gate refused carries it in
    # the error's own details, which is where it matters most -- a refused run
    # is exactly when a reviewer is asking what the pass managed to write.
    writing_ledger = None
    if isinstance(result, dict):
        writing_ledger = result.get("writing_ledger")
        if not writing_ledger and isinstance(result.get("details"), dict):
            writing_ledger = result["details"].get("writing_ledger")

    # The applied-preferences list is a snapshot taken at render time, but it is
    # displayed as a live view of what shapes this deck. A preference the reviewer
    # has since deleted must not keep showing up here (Antonio, 2026-07-24), so the
    # snapshot is filtered against the store on every render. On a fresh run this is
    # a no-op (nothing has been deleted yet); it only bites on a recalled result.
    applied_notes = list(applied or [])
    live_notes = _live_preference_notes()
    if live_notes is not None:
        applied_notes = [note for note in applied_notes if note in live_notes]
    # Slide 2's switches, built here so the template lays out an answer rather
    # than working one out. Offered as controls only when the packet is known,
    # because a switch is recorded against a packet; a deck reopened without one
    # still renders the card, read-only, exactly as it did before.
    selection = (result or {}).get("bullet_selection")
    # The switches are keyed on the document this deck was written from, not on
    # a packet path: a live run has no packet file and used to lose them.
    toggle_scope = _toggle_scope(packet=packet, attachments=attachments)
    switch_blocks = _switch_blocks(
        selection, (result or {}).get("bullet_selection_per_opportunity"),
        toggle_scope, deck_path=deck_path)

    return {
        "result": result,
        "status": status,
        "bullet_switches": switch_blocks,
        # Offered as controls whenever the run has something to key a switch
        # against. A deck reopened with neither a document nor a packet still
        # renders the card, read-only, exactly as it did before.
        "switches_live": bool(toggle_scope),
        "toggle_scope": toggle_scope,
        "switch_auto": SWITCH_AUTO,
        "switch_on": BULLET_ON,
        "switch_off": BULLET_OFF,
        "provenance": provenance,
        "attachments": attachments,
        "writing_ledger": writing_ledger,
        "deck_type": deck_type,
        "company": company or "(unnamed)",
        "applied": applied_notes,
        "deck_path": deck_path,
        # A deck opened out of the store has no file behind it, so the controls
        # that act on one (Save deck, the edit ladder) are not offered for it.
        "is_db_deck": _db_deck_id(deck_path) is not None,
        "deck_heading": deck_heading,
        "edit_cards": edit_cards,
        "prompt_rel": prompt_rel,
        "gaps": gaps or [],
        # Claims the reviewer has already confirmed. Listed separately and
        # reopenable, rather than disappearing: the packet still declares them, so
        # the record of what was reviewed (and the way back) has to live on screen.
        "resolved": resolved or [],
        "show_gaps": show_gaps,
        "notice": notice,
        "notice_bad": notice_bad,
        "raw_company": raw_company,
        "raw_project": raw_project,
        "packet": packet,
        "check_in_date": check_in_date,
        "do_render": do_render,
        # The deck the render-fidelity and layout guards actually measured, and
        # whether what is on screen has moved on from it (a reviewer edit writes a
        # new revision, and neither guard re-runs). Kept explicit so the reports
        # can stay visible — a reviewer still needs them — while saying plainly
        # that they describe the original render, not this revision.
        "render_deck_path": render_deck_path,
        "render_deck_name": os.path.basename(render_deck_path) if render_deck_path else "",
        "guards_stale": guards_stale,
        # Set only by `/export-design-prompt`, and only for the response to that
        # click — every other route leaves this at its default, so the exported
        # prompt does not linger on screen past the action that produced it.
        "design_export_prompt": design_export_prompt,
        # ONE ENTRY PER THING ACTUALLY WRONG, not one per element caught in it.
        # See `_layout_defects`: the "27 problem(s)" a reviewer read on
        # 2026-09-20 was a single slide overflowing, counted 27 times.
        "layout_defects": _layout_defects(
            (result or {}).get("layout") or {}
        ) if isinstance(result, dict) else [],
        # And the count he was actually looking for, which nothing reported.
        "missing_markers": _missing_marker_count(deck_path) if deck_path else 0,
    }


# The most recent Result render's rebuildable inputs. This is an internal,
# single-user local studio (see the module docstring), so a module-level cache is
# enough to keep the last result alive across a tab switch or a preferences POST —
# both of which reload the page via GET `/` and would otherwise show "No deck yet"
# and force a regenerate. Not persisted to disk and not per-session by design.
_LAST_RESULT = {}


# ---- Background runs ------------------------------------------------------
#
# A render takes about five minutes. Held inside its own POST, the RESPONSE is
# what a proxy in front of the studio cuts off: on 2026-08-26 the portal returned
# a bare "upstream error" at roughly 300 seconds while the run itself was healthy
# and went on to finish, so every render behind the portal lost its deck. Our own
# gunicorn timeout (600s) never fired and never would have; the limit was a hop
# we do not control and cannot see.
#
# So a run no longer owns a request. `POST /run` starts it on a thread, answers
# immediately with a run id, and the page polls `/run-status` until it lands. No
# request lives longer than a moment, which is what makes this independent of any
# proxy's patience rather than tuned to one proxy's number.
#
# Keyed by run id, not a single slot, so two reviewers on the portal each collect
# their own run instead of the second finisher wiping the first. The record is the
# ONLY state the worker thread touches: `_LAST_RESULT` and every edit route stay
# request-thread-only, which is what keeps this from needing locks across the app.
_RUNS = {}
_RUNS_LOCK = threading.Lock()

# Finished runs kept so a reviewer can reload, switch tabs, or come back later and
# still collect the deck. Bounded because this is memory, and in-process by
# design, so a redeploy drops them and `/run-status` answers "unknown" rather than
# spinning forever on a run that no longer exists.
_RUN_RETENTION = 8

# How long the browser waits between polls. Three seconds is under a proxy's
# patience by two orders of magnitude and costs one trivial request per tick.
RUN_POLL_MS = 3000

# The reviewer's own run, remembered in their browser so a reload or a closed tab
# does not lose a deck that is still being built. Only ever holds a run id, which
# is opaque and useless to anyone who does not already have the studio open.
RUN_COOKIE = "deck_run"

# Long enough to outlive a five-minute render and a reviewer wandering off, short
# enough that a stale id does not follow them around for days.
_RUN_COOKIE_MAX_AGE = 60 * 60 * 4


def _prune_runs_locked():
    """Drop the oldest finished runs past ``_RUN_RETENTION``. Caller holds the lock.

    Only finished runs are eligible: a run still working is never evicted, however
    many have piled up behind it, because evicting it would strand a deck that is
    about to exist with nobody able to ask for it.
    """
    done = sorted((rec["started"], rid) for rid, rec in _RUNS.items()
                  if rec["state"] == "done")
    for _, rid in done[:max(0, len(done) - _RUN_RETENTION)]:
        del _RUNS[rid]


def _new_run(inputs):
    """Register a run and return its id.

    ``inputs`` is everything the Result panel needs that the pipeline result does
    not carry (the packet path, the reviewer's raw company/project, the check-in
    date, whether a render was asked for). Held here rather than re-read from the
    form later, because the request that submitted the form is gone by the time
    the run finishes.
    """
    run_id = secrets.token_urlsafe(9)
    with _RUNS_LOCK:
        _RUNS[run_id] = {
            "state": "running",
            "started": time.monotonic(),
            "elapsed": None,
            "result": None,
            "inputs": inputs,
        }
        _prune_runs_locked()
    return run_id


def _run_record(run_id):
    """A copy of the run's record, or None if there is no such run.

    A copy, so a caller reading a record cannot observe it changing under them
    halfway through, and cannot mutate the registry by accident.
    """
    if not run_id:
        return None
    with _RUNS_LOCK:
        rec = _RUNS.get(run_id)
        return dict(rec) if rec is not None else None


def _finish_run(run_id, result):
    """Attach the outcome and mark the run done. Called on the worker thread."""
    with _RUNS_LOCK:
        rec = _RUNS.get(run_id)
        if rec is None:
            # Evicted mid-run (only possible if retention was reduced) or the
            # process restarted. Nothing to attach it to, and the poll will say so.
            return
        rec["result"] = result
        rec["state"] = "done"
        rec["elapsed"] = time.monotonic() - rec["started"]
        shape, seconds = _run_shape(rec), rec["elapsed"]
    # Outside the lock: this touches the disk, and the registry must not be held
    # while it does. A failure here costs an estimate, never a run.
    _log_run_duration(shape, seconds)


# How long runs of each shape ACTUALLY take, newest last, so the overlay can
# stop quoting a number nobody has re-measured.
#
# Antonio, 2026-09-20, on the loading screen: "it says a render usually takes 6
# minutes and 53 seconds, which is a lie. So that's really old data." It was not
# invented -- it is `LEGS` summed, and `LEGS` came from a real measurement of a
# 397-second run -- but a constant measured once is a constant that goes stale,
# and the folder rule against hardcoding values applies to it as much as to a
# client name. So the expectation is now computed from this machine's own
# finished runs and the table is only the fallback for a studio that has not run
# anything yet.
#
# Per SHAPE, because the four differ by minutes: a fixture run makes no model
# pass and a run with the render off ends when the prompt is assembled. Pooling
# them would quote a live render's wait to someone running a fixture.
# Inside the RESOLVED store directory rather than at the repo root, and that is
# not tidiness. The log is studio state, it belongs beside the deck store, and
# putting it there means the autouse fixture in `tests/conftest.py` that already
# points `DECK_STORE_DIR` at a tmp_path isolates it for free. The first cut wrote
# to the repo root and the suite promptly filled it with 25 zero-second runs,
# because every test that finishes a run finishes it instantly.
def _durations_path():
    override = os.environ.get("RUN_DURATIONS_PATH")
    if override:
        return override
    return os.path.join(resolve_store_dir().directory, "run-durations.json")


# Enough to have a median that means something, few enough that a change in how
# long a render takes shows up within a day's work rather than being averaged
# away by months of history.
_DURATIONS_KEPT = 25

# Below this, a "run" is not a run whose length is worth remembering: a fixture
# assembly with no render and no model call returns in well under a second, and
# so does every test. The threshold is what keeps a median honest — 25 instant
# runs would otherwise tell a reviewer a render takes no time at all, which is
# the same class of lie as the stale constant this replaces, pointing the other
# way.
_DURATION_FLOOR_S = 10


def _log_run_duration(shape, seconds):
    """Append one finished run's wall time, newest last. Never raises.

    Best-effort by design: this is an estimate for a progress bar, so a
    read-only directory, a partial write from a previous crash or a concurrent
    writer all degrade to "no history yet" and the overlay falls back to the
    measured table. A studio that cannot write here still renders decks.
    """
    if not shape or not seconds or seconds < _DURATION_FLOOR_S:
        return
    try:
        log = _read_durations()
        kept = (log.get(shape) or [])[-(_DURATIONS_KEPT - 1):]
        log[shape] = kept + [round(float(seconds), 1)]
        tmp = _durations_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(log, fh)
        os.replace(tmp, _durations_path())
    except (OSError, TypeError, ValueError):
        # Expected: a read-only directory, a full disk, a half-written file from
        # a previous crash. NOT a bare `except Exception`, which is how the
        # first cut hid an AttributeError (`resolve_store_dir()` has no `.path`)
        # behind a silent "no history yet" for its whole life.
        return


def _read_durations():
    """The duration log, or {} when there is none or it cannot be read."""
    try:
        with open(_durations_path(), encoding="utf-8") as fh:
            log = json.load(fh)
        return log if isinstance(log, dict) else {}
    except (OSError, ValueError):
        # A missing log is the normal first-run state; a malformed one is a
        # crash mid-write. Both mean "no history", and neither is a defect in
        # this module worth hiding.
        return {}


def _duration_estimate(shape):
    """`{"median": s, "low": s, "high": s, "runs": n}` for a shape, or None.

    The median rather than the mean, because one run that stalled behind a slow
    model call would drag a mean upward for the next twenty-four runs and make
    every normal run look late -- which is the exact failure the `LEGS` comment
    records the old bar having.

    `low`/`high` are the quartiles, so the overlay can say "usually 5 to 7
    minutes" instead of a single number it will miss most of the time. Withheld
    entirely under three runs: a median of two is a coin toss, and quoting one
    would repeat the mistake of stating an expectation more firmly than the
    evidence supports.
    """
    runs = sorted((_read_durations().get(shape) or []))
    if len(runs) < 3:
        return None
    return {"median": _quantile(runs, 0.5), "low": _quantile(runs, 0.25),
            "high": _quantile(runs, 0.75), "runs": len(runs)}


def _quantile(ordered, q):
    """Nearest-rank quantile of an already-sorted list."""
    index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[index]


def _execute_run(run_id, **kwargs):
    """The worker thread's whole body: run the pipeline, record the outcome.

    Nothing may escape here. An exception that got out would leave the record on
    "running" for the life of the process and the reviewer watching a progress bar
    for a run that stopped, so the ``finally`` marks it done whatever happened.
    ``_pipeline_result`` already turns every expected failure into an error
    envelope; the ``except`` is for the unexpected.
    """
    result = {"status": "render_error",
              "message": "The run stopped before it produced a result."}
    try:
        result = _pipeline_result(**kwargs)
    except Exception as exc:
        result = {"status": "render_error",
                  "message": f"{type(exc).__name__}: {exc}"}
    finally:
        _finish_run(run_id, result)


def _is_generated_run(entry):
    """Whether ``entry`` is a pipeline run that produced a deck still on disk.

    Such a run owns the memory slot: it is the thing the reviewer generated, and
    it is the only entry that can carry a fidelity report, applied preferences and
    a saved Design prompt, none of which can be rebuilt from a deck file alone.
    A deck opened from the database (a `db-deck:` sentinel path) carries all of
    that from its own stored record instead of a file, so it qualifies the same
    way a fresh render does.
    """
    path = entry.get("deck_path")
    if not path:
        return False
    if _db_deck_id(path) is not None:
        return bool(entry.get("result"))
    return bool(entry.get("result")) and os.path.isfile(path)


def _remember_result(ctx):
    """Cache the inputs needed to rebuild the current Result panel later.

    The transient ``notice``/``notice_bad`` are intentionally dropped so a stale
    banner never reappears on a later tab switch; everything else is enough for
    :func:`_recall_result_ctx` to rebuild the panel (gaps and edit cards are
    recomputed fresh at recall so they track the current packet and deck).

    A generated run holds the slot for the rest of the session, so a panel with no
    run outcome of its own cannot evict one that has it. Opening some other deck
    from the Decks tab used to overwrite the slot, which is how the Result tab lost
    the deck: the run record was then gone for good, so coming back could not
    restore the fidelity report, the applied preferences, the flagged-claims
    checklist or the prompt, and a later delete of that unrelated deck cleared the
    slot outright and left "No deck yet" behind. The detour still renders its own
    deck in the response that asked for it — it just does not become the session's
    remembered result.
    """
    if _is_generated_run(_LAST_RESULT) and not _is_generated_run(ctx):
        return
    _LAST_RESULT.clear()
    _LAST_RESULT.update({
        "result": ctx.get("result"),
        "status": ctx.get("status"),
        "deck_type": ctx.get("deck_type", ""),
        "applied": ctx.get("applied") or [],
        "deck_path": ctx.get("deck_path"),
        "prompt_rel": ctx.get("prompt_rel", ""),
        "show_gaps": ctx.get("show_gaps", False),
        "raw_company": ctx.get("raw_company", ""),
        "raw_project": ctx.get("raw_project", ""),
        "packet": ctx.get("packet", ""),
        "check_in_date": ctx.get("check_in_date", ""),
        "do_render": ctx.get("do_render", False),
        "deck_heading": ctx.get("deck_heading", "Rendered deck"),
        "render_deck_path": ctx.get("render_deck_path", ""),
        "guards_stale": ctx.get("guards_stale", False),
    })


def _carry_run_outcome(deck_path):
    """The last run's outcome, when it is the run that produced ``deck_path``.

    A reviewer resolving a flag or applying an edit is still looking at the SAME
    run: the deck, its fidelity and layout reports, the preferences that shaped
    it, and the saved Design prompt are all still the relevant record. Those
    routes have no pipeline result of their own, and building the panel without
    one silently dropped every one of those cards — so a reviewer who resolved one
    of two flags lost the run outcome and the Design prompt path with it, and the
    cache then remembered the stripped-down panel so a tab switch could not get
    them back.

    Returns the keys ``_build_result_ctx`` needs, or ``{}`` when there is nothing
    to carry. The match is on the deck's BASE stem (``output-20`` covers
    ``output-20-r3.html``), so an unrelated deck opened from the Decks tab never
    inherits another run's outcome. ``guards_stale`` is set when the deck on
    screen is not byte-for-byte the one the guards measured.
    """
    st = _LAST_RESULT
    if not st or not deck_path:
        return {}
    measured = st.get("render_deck_path") or st.get("deck_path") or ""
    if not measured or base_stem(measured) != base_stem(deck_path):
        return {}
    return {
        "result": st.get("result"),
        "status": st.get("status"),
        "applied": st.get("applied") or [],
        "prompt_rel": st.get("prompt_rel", ""),
        "render_deck_path": measured,
        "guards_stale": os.path.abspath(measured) != os.path.abspath(deck_path),
    }


def _run_outcome_for(deck_path):
    """`_carry_run_outcome`, extended to understand a `db-deck:` sentinel.

    A gap decision posted from a deck opened out of the database carries that
    sentinel back as its `deck_path`, and the outcome to restore lives in the
    store's own record for that id, not in `_LAST_RESULT` (which may belong to
    an unrelated run, or to no run at all).
    """
    db_id = _db_deck_id(deck_path)
    if db_id is not None:
        try:
            row = get_deck(db_id)
        except Exception:
            row = None
        return _db_run_outcome(row.get("details") or {}, deck_path) if row else {}
    return _carry_run_outcome(deck_path)


def _recall_result_ctx():
    """Rebuild the last Result-panel context, or ``None`` if none rendered yet.

    A deck file that has since vanished (e.g. undone away) drops to no-deck rather
    than pointing at a missing file.
    """
    if not _LAST_RESULT:
        return None
    st = _LAST_RESULT
    deck_path = st.get("deck_path")
    if deck_path and not os.path.isfile(deck_path):
        deck_path = None
    gaps, resolved = (_gaps_for(st.get("packet", ""), st.get("deck_type", ""))
                      if st.get("show_gaps") else ([], []))
    return _build_result_ctx(
        result=st.get("result"), status=st.get("status"),
        deck_type=st.get("deck_type", ""), company=st.get("raw_company", ""),
        applied=st.get("applied") or [], deck_path=deck_path,
        prompt_rel=st.get("prompt_rel", ""), gaps=gaps, resolved=resolved,
        show_gaps=st.get("show_gaps", False),
        raw_company=st.get("raw_company", ""), raw_project=st.get("raw_project", ""),
        packet=st.get("packet", ""), check_in_date=st.get("check_in_date", ""),
        do_render=st.get("do_render", False),
        deck_heading=st.get("deck_heading", "Rendered deck"),
        render_deck_path=st.get("render_deck_path", ""),
        guards_stale=st.get("guards_stale", False),
    )


# -------------------------------- routes -----------------------------------

def _decks_notice():
    """The Decks tab's banner for a delete that just happened, from the query string.

    ``/deck-delete`` redirects (post/redirect/get) so a refresh cannot re-fire a
    delete, which leaves the redirect's own parameters as the only place to carry
    the outcome: ``deleted`` names the file that went and ``also`` counts the
    revisions that went with it, or ``failed`` names a file that could not be
    removed. Returns ``(notice, notice_bad)``, empty when the load is an ordinary one.
    """
    failed = (request.args.get("failed") or "").strip()
    if failed:
        return (f"Could not delete {failed}. It may already be gone, or the file "
                f"may not be writable — check the decks folder."), True
    name = (request.args.get("deleted") or "").strip()
    if not name:
        return "", False
    try:
        also = int(request.args.get("also", "0"))
    except ValueError:
        also = 0
    if also:
        return f"Deleted {name} and the {also} revision(s) beside it.", False
    return f"Deleted {name}.", False


@app.route("/health")
def health():
    """Platform liveness probe. Deliberately says nothing about the deck store,
    the data provider or the credentials — Railway asks whether the process is
    serving, and a probe that fails on a degraded dependency would pull a
    working service out of rotation."""
    return jsonify({"status": "ok"})


@app.route("/")
def home():
    tab = request.args.get("tab", "generate")
    if tab not in ("generate", "decks", "preferences", "result"):
        tab = "generate"
    notice, notice_bad = _decks_notice()

    # A run named in the URL is a reviewer collecting a specific run, which is how
    # the client script delivers one the moment the poll says it landed. Resolved
    # by id rather than from the shared slot, so two reviewers finishing at once
    # each get their own deck.
    asked = (request.args.get("run") or "").strip()
    if asked:
        rec = _run_record(asked)
        if rec is None:
            # Retention passed it, or the process restarted mid-run. Say so: a
            # reviewer who waited five minutes deserves better than a Generate tab
            # with no explanation.
            return render_studio("result", result_ctx=_build_result_ctx(
                result={"status": "error", "code": "run_not_found",
                        "message": ("That run is no longer available. Runs are "
                                    "held in memory, so a restart or a redeploy "
                                    "ends them."),
                        "remediation": ("Re-run it from the Generate tab. Nothing "
                                        "was saved to deck history either way, "
                                        "since only Save deck writes a row."),
                        "details": ""},
                status="error",
            ), decks_notice=notice, decks_notice_bad=notice_bad)
        if rec["state"] == "running":
            return render_studio(tab, run_id=asked,
                                 decks_notice=notice, decks_notice_bad=notice_bad)
        return render_studio("result", result_ctx=_run_result_ctx(rec),
                             decks_notice=notice, decks_notice_bad=notice_bad)

    # No run named, but this browser has one still working: keep the overlay and
    # the polling alive across a reload so a reviewer who refreshes mid-render does
    # not lose sight of it. A FINISHED cookie run is deliberately not resolved
    # here — tab switches keep coming from `_LAST_RESULT`, which is what the edit,
    # gap and commercial routes update, so a reviewer who has edited the deck sees
    # their edits rather than the original render.
    mine = _run_record(request.cookies.get(RUN_COOKIE, ""))
    running = mine["state"] == "running" if mine else False

    # Restore the last result so switching tabs (or returning from a preferences
    # change) keeps the deck, edit surface, and checklist instead of wiping them.
    return render_studio(tab, result_ctx=_recall_result_ctx(),
                         run_id=request.cookies.get(RUN_COOKIE, "") if running else "",
                         decks_notice=notice, decks_notice_bad=notice_bad)


@app.route("/run-status")
def run_status():
    """Has this run landed? Answered in microseconds, which is the whole point.

    The client polls this instead of holding a five-minute request open, so no hop
    between the reviewer and the studio ever sees a slow response to cut off.
    `elapsed` comes from the server's own clock rather than the page's, so a reload
    mid-run shows the true elapsed time instead of restarting the count.
    """
    rec = _run_record((request.args.get("id") or "").strip())
    if rec is None:
        return jsonify({"state": "unknown"})
    if rec["state"] == "running":
        return jsonify({"state": "running",
                        "elapsed": int(time.monotonic() - rec["started"]),
                        "shape": _run_shape(rec)})
    return jsonify({"state": "done", "elapsed": int(rec.get("elapsed") or 0)})


def _run_shape(rec):
    """Which of the four run shapes the overlay should be watching this run
    against: `live-render`, `live`, `fixture-render` or `fixture`.

    The legs a run has decide how long it takes and what its progress bar should
    be narrating, and they differ by nearly three minutes: a live render makes
    three model passes before the render begins, a fixture render makes none.
    The page can read its own form at submit time, but a reload resets the form
    while the run keeps going, so the server has to be able to answer too. The
    names are the keys of the client's own `SHAPES` table; an unknown one there
    falls back to the live render, which is the longest of the four and so never
    overstates progress.
    """
    inputs = rec.get("inputs") or {}
    source = SOURCE_LIVE if inputs.get("data_source") == SOURCE_LIVE else SOURCE_FIXTURE
    return f"{source}-render" if inputs.get("do_render") else source


@app.route("/run", methods=["POST"])
def run():
    fixture_id = (request.form.get("fixture_id") or "").strip()
    # Stripped because the check-in drop below compares it: a hand-built post
    # sending " status " would otherwise lose its date silently on the way to
    # failing anyway at the mapper, which is two confusing outcomes for one typo.
    deck_type = (request.form.get("deck_type") or "proposal").strip()
    company = (request.form.get("company") or "").strip()
    # The candidate the reviewer chose when the company name matched more than
    # one registry record (item 17), posted by the chooser on the Generate tab.
    # Blank for every name that resolves to one company, which is nearly every
    # run, and those requests are unchanged.
    company_id = (request.form.get("company_id") or "").strip()
    project = (request.form.get("project") or "").strip()
    packet = (request.form.get("packet") or "").strip()
    # Dropped rather than refused on a proposal, and this is the only place it
    # is dropped. A check-in date belongs to a status deck: `status_scope.py`
    # reads it when the status-scope provider builds the packet, and nothing on
    # the proposal path reads it at all, so one posted with a proposal is inert
    # downstream rather than wrong, and refusing a run over a value that would
    # change nothing would be a worse answer than ignoring it. (Checked
    # 2026-09-15 by posting a date the fixture packet does not carry: it reaches
    # the request and the run context, while the deck's DISPLAYED date comes
    # from the packet's own `check_in_date_display`. So on the fixture path the
    # posted value drives the request and not the page.) Normalised here, at the edge, so
    # it reaches neither the request, the request echo the Result tab shows, nor
    # the run context the edit forms carry forward. The form hides the field on
    # a proposal (`syncDeckType`); this is what makes that true rather than
    # cosmetic, for a posted form that never ran the script.
    check_in_date = (request.form.get("check_in_date") or "").strip()
    if deck_type != "status":
        check_in_date = ""
    # The FORM always posts `live` now (2026-09-20): the frozen-packet source is
    # off the screen and there is nothing on the page that can ask for it. This
    # default governs only a request that names no source at all, which no
    # browser produces and the suite's offline runs do, so it stays where it is
    # rather than turning every test into a live call.
    data_source = (request.form.get("data_source") or SOURCE_FIXTURE).strip()
    # The live path's two request fields (contract section 2). Both optional, and
    # both absent means the cover carries a client with no firm beside it and no
    # date, which is an honest gap rather than an invented one.
    pe_firm = (request.form.get("pe_firm") or "").strip()
    proposal_date = (request.form.get("proposal_date") or "").strip()
    # Studio input, like the commercial rows and the two fields above: no data
    # source supplies it and nothing is invented in its place when it is blank.
    deck_title = (request.form.get("deck_title") or "").strip()
    # The short brand form for the footers. Studio input for the reason
    # `deck_title` is: no data source supplies it, on either deck type.
    client_short = (request.form.get("client_short") or "").strip()
    # Fall back to the selected fixture's frozen values for any field left blank,
    # so a run still resolves even if the client-side autofill did not populate the
    # fields (e.g. the default-selected fixture on first load, or JS disabled).
    if fixture_id and data_source == SOURCE_FIXTURE:
        fx = _fixture_by_id(fixture_id)
        if fx:
            packet = packet or fx.get("packet", "")
            company = company or fx.get("company", "")
            project = project or fx.get("project", "")
    do_render = bool(request.form.get("render")) and _has_api_key()
    # Every opportunity the reviewer picked, in the order the picker reports
    # them, which is the order their slides read in. `getlist` because the
    # picker is `multiple` since item 15; the singular field is still read so a
    # form posted from anywhere else, or a saved one, still works.
    opportunity_ids = [
        value.strip() for value in request.form.getlist("opportunity_ids")
        if value.strip()
    ]
    single = (request.form.get("opportunity_id") or "").strip()
    if single and single not in opportunity_ids:
        opportunity_ids.append(single)
    # In order, once each. Picking one opportunity twice is a deck with two
    # identical slides, which nobody wants; the provider refuses it again for
    # its own callers, and this stops it travelling that far from a reviewer's
    # own picker.
    opportunity_ids = list(dict.fromkeys(opportunity_ids))
    # Read INSIDE the request. `request.files` does not survive the worker
    # thread; see `_read_attachments`, which is where the reasoning lives.
    uploads, attachment_refusal = _read_attachments(
        request.files, live=data_source == SOURCE_LIVE
    )
    if attachment_refusal is not None:
        return render_studio("result", result_ctx=_build_result_ctx(
            result=attachment_refusal, status="error", deck_type=deck_type,
            company=company, packet=packet, raw_company=company,
            raw_project=project, check_in_date=check_in_date,
            do_render=do_render,
        ))
    # THE PRD ANSWERS THIS IF THE BROWSER DID NOT (Antonio, 2026-09-20: "if I
    # upload a PRD I don't want to also have to select a company and an
    # opportunity"). The Generate tab scans an attachment as it is chosen and
    # fills the fields, but that is a convenience of the page and it fails for
    # every reason a page fails: the run posted before the scan landed, a
    # browser with the script blocked, a form restored from history. So the RUN
    # does the same read, from the same document, when it needs a value nobody
    # supplied. It resolves nothing the document does not state, and it selects
    # only where the document's stated title matches exactly one published
    # opportunity; anything less lands on the refusal below with the reason.
    #
    # EVERY UPLOAD, not the first (2026-09-22). Two PRDs naming two
    # opportunities are a deck for both, and the provider routes each PRD to
    # the opportunity it names, so reading only the first would post a deck
    # for one opportunity with the other's PRD attached to nothing. The company
    # comes from the first document that resolves one, and every later read is
    # handed it, so a second PRD cannot move the run to a different company.
    if data_source == SOURCE_LIVE and uploads and (not opportunity_ids or not company):
        wanted = not opportunity_ids
        for upload in uploads:
            derived, why = _derive_from_document(upload, company, company_id)
            company = company or derived.get("company") or ""
            company_id = company_id or derived.get("company_id") or ""
            project = project or derived.get("project") or ""
            found = derived.get("opportunity_id")
            if wanted and found and found not in opportunity_ids:
                opportunity_ids.append(found)
            if why:
                app.logger.info("PRD read on run: %s", why)

    # No `commercial_rows`: the studio no longer collects them before the render.
    # `_run_and_render` still takes them, and so does the adapter, for a
    # programmatic caller that genuinely has the terms up front — dropping the
    # parameter would remove a capability rather than move a form.
    return _run_and_render(deck_type, company, project, packet, check_in_date, do_render,
                           data_source=data_source, company_id=company_id,
                           pe_firm=pe_firm, proposal_date=proposal_date,
                           opportunity_ids=opportunity_ids,
                           deck_title=deck_title, client_short=client_short,
                           uploads=uploads)


@app.route("/live-opportunities")
def live_opportunities():
    """The reviewer's opportunity picker (E9e): the resolved company's
    published opportunities, or the same resolution/gate error a run would hit,
    surfaced before the reviewer commits to one. `list_opportunities` only;
    no new tool.

    Ordered by title match against the project named on the form, because the
    platform records no project-to-opportunity link and a company can carry two
    dozen published opportunities in an order that has nothing to do with the
    engagement in front of the reviewer. Ordering costs no extra call: it is
    arithmetic over titles this route already holds."""
    from live_proposal_provider import ProviderError, opportunity_choices
    from qofai_mcp_client import McpError

    company = (request.args.get("company") or "").strip()
    # The project only orders the list and marks its leader; it is never resolved
    # here and never filters, so a half-typed or unrecognised one still yields
    # every published opportunity. See `rank_choices`.
    project = (request.args.get("project") or "").strip()
    # The candidate the reviewer picked on a previous pass of this same route
    # (item 17). It rides beside the name rather than replacing it, because the
    # provider resolves it by narrowing the name's own search; see
    # `resolve_company`.
    company_id = (request.args.get("company_id") or "").strip()
    if not company:
        return jsonify({"opportunities": []})
    try:
        return jsonify({"opportunities": opportunity_choices(
            _live_client(), company, project=project, company_id=company_id)})
    except ProviderError as exc:
        # The candidates travel with the error, and this is the whole of item 17
        # on this side. An ambiguous company used to put "matched 4 companies"
        # in the picker and stop, with a remediation naming a
        # `company_id` no reviewer has and no screen offered. The provider is
        # already holding the four rows when it raises; handing them on costs
        # nothing and turns the wall into a choice. Empty for every other code,
        # so nothing else on this route moves.
        return jsonify({"opportunities": [], "error": exc.message,
                        "candidates": exc.details.get("candidates") or []})
    except McpError as exc:
        # This route builds its own client and never goes through the seam, so
        # `submit`'s envelope discipline never applies to it and nothing else
        # catches these: unguarded they return 500 HTML to a `fetch` asking for
        # JSON. Covers a missing key and a dead transport alike, in the same
        # envelope shape as above, so the picker has one field to read whatever
        # went wrong. The type is named because "no opportunities" and "the
        # platform never answered" look identical in a dropdown otherwise.
        return jsonify({"opportunities": [],
                        "error": f"{type(exc).__name__}: {exc}"})


def _derive_from_document(upload, company, company_id):
    """What an attached PRD supplies that the form did not, for a live run.

    The Generate tab already scans an attachment through `/prd-scan` and fills
    the fields with what it found. This is the same read on the server, for the
    runs where that did not happen: the reviewer pressed Generate before the
    scan landed, or the page's script never ran at all. The rule is the same in
    both places and lives in one module, so the two cannot drift.

    Returns ``(derived, why)``. ``derived`` carries only values the document
    states and the platform confirms, and is empty when either is missing;
    ``why`` is a line for the log, never for a slide. Any failure here is
    silent to the run: the refusal that follows already says what was missing,
    and a document that cannot be read is not a reason to stop a reviewer who
    filled the form by hand.
    """
    from live_proposal_provider import ProviderError, opportunity_choices, resolve_company
    from qofai_mcp_client import McpError
    from document_text import extract_text as extract_document_text
    import prd_front_matter

    derived = {}
    try:
        extracted = extract_document_text(upload.data, upload.filename)
        stated = prd_front_matter.read(getattr(extracted, "text", "") or "")
    except Exception as exc:  # a document we cannot read is not a run we stop
        return derived, f"{upload.filename}: {type(exc).__name__}"
    if not stated["client_candidates"]:
        return derived, f"{upload.filename}: states no client"

    client = _live_client()
    names = [company] if company else stated["client_candidates"]
    resolved, used = None, ""
    for name in names:
        try:
            resolved = resolve_company(client, name, company_id)
            used = name
            break
        except (ProviderError, McpError):
            continue
    if resolved is None:
        return derived, f"{upload.filename}: no candidate resolved"
    derived["company"] = used
    derived["company_id"] = str(
        resolved.get("id") if isinstance(resolved, dict) else resolved) or ""

    if not stated["opportunity"] or not stated["opportunity_selectable"]:
        return derived, f"{upload.filename}: no selectable opportunity stated"
    try:
        choices = opportunity_choices(client, used, project="",
                                      company_id=derived["company_id"])
    except (ProviderError, McpError) as exc:
        return derived, f"{upload.filename}: {type(exc).__name__} listing"
    match = prd_front_matter.match_opportunity(stated["opportunity"], choices)
    if match is None:
        return derived, (f"{upload.filename}: stated opportunity matched "
                         "none or several")
    derived["opportunity_id"] = str(match.get("id") or "")
    return derived, ""


@app.route("/prd-scan", methods=["POST"])
def prd_scan():
    """What the attached PRD says it is about, resolved against the platform.

    The PRD branch of the Generate tab (Antonio, 2026-09-20): "if I upload a PRD
    I don't want to also have to select a company and an opportunity, that
    information is already in the PRD." It is, in a labelled front-matter block
    every document in the corpus carries, and `prd_front_matter` reads it
    without a model call for the reason that module's docstring gives.

    THIS ROUTE STORES NOTHING. The bytes are read, parsed and dropped; the file
    input still holds the reviewer's selection, so the run posts the same file
    again and the upload path stays exactly as item 14 built it.

    It costs the same platform calls the reviewer's own typing costs today, and
    it answers with what it FOUND rather than acting on it: the form shows the
    client, the project and the opportunity it resolved, and the reviewer can
    change any of them before spending a run. A branch that resolved a company
    the reviewer never saw named would be faster and would eventually build a
    deck for the wrong one.
    """
    from live_proposal_provider import (
        ProviderError, opportunity_choices, resolve_company,
    )
    from qofai_mcp_client import McpError
    import prd_front_matter
    from document_text import extract_text as extract_document_text

    files = [f for f in request.files.getlist(ATTACHMENT_FIELD) if f and f.filename]
    if not files:
        return jsonify({"error": "No document was attached, so there was "
                                 "nothing to read."})
    document = files[0]
    extracted = extract_document_text(document.read(), document.filename)
    text = getattr(extracted, "text", "") or ""
    if not text.strip():
        return jsonify({"error": (
            f"{document.filename} carries no readable text, so nothing could be "
            "read out of it. Fill the fields below by hand, or attach a "
            "different file.")})

    stated = prd_front_matter.read(text)
    # The reviewer's pick among an ambiguous name's candidates (item 17),
    # threaded so a re-scan AFTER choosing resolves. Without it the second scan
    # asks the same ambiguous question, the chooser reappears, and the PRD's own
    # opportunity title never gets matched against the picked company's list --
    # which is the difference between offering a choice and acting on one.
    picked_company_id = (request.form.get("company_id") or "").strip()
    out = {
        "filename": document.filename,
        "client_stated": stated["client_stated"],
        "opportunity_stated": stated["opportunity"],
        "opportunity_note": stated["opportunity_note"],
        "opportunity_selectable": stated["opportunity_selectable"],
        "company": "", "company_id": "", "projects": [],
        "opportunities": [], "matched_opportunity_id": "",
        "candidates": [], "notes": [],
    }
    if not stated["client_candidates"]:
        out["error"] = (
            f"{document.filename} states no 'Prepared for' line, so there is no "
            "client to look up. Type the company below.")
        return jsonify(out)

    # Each candidate in turn, best first. The first that resolves wins and the
    # rest are never tried, so an ampersand row costs one extra call at most.
    client = _live_client()
    # The refusal and the candidates it came with travel together: a later
    # candidate failing outright must not leave an earlier candidate's list
    # under a different name's message.
    refusal, candidates, refused_name = "", [], ""
    resolved = None
    for name in stated["client_candidates"]:
        try:
            resolved = resolve_company(client, name, picked_company_id)
            out["company"] = name
            break
        except ProviderError as exc:
            # An ambiguous name is the interesting outcome, so it is kept over a
            # plain not-found from a later candidate.
            found = exc.details.get("candidates") or []
            if found or not refusal:
                refusal, candidates, refused_name = exc.message, found, name
        except McpError as exc:
            return jsonify(dict(out, error=f"{type(exc).__name__}: {exc}"))
    if resolved is None:
        # An ambiguous name is a question rather than a failure, and the studio
        # already has a surface for it: the item 17 chooser, in the shape
        # `/live-opportunities` returns. The name that produced this message is
        # named, so a reader can tell which of the document's candidates it
        # describes.
        out["error"] = refusal or (
            "The client this document names did not resolve on the platform.")
        if refused_name:
            out["error"] = f"Searched for \u201c{refused_name}\u201d. " + out["error"]
        out["candidates"] = candidates
        return jsonify(out)

    out["company_id"] = str(
        resolved.get("id") if isinstance(resolved, dict) else resolved) or ""

    # The projects, so a company with exactly one needs no click and a company
    # with several can be picked from without typing.
    try:
        projects = client.call_tool_json(
            "list_projects", {"company_id": out["company_id"]}).get("projects") or []
        out["projects"] = [{"id": str(p.get("id") or ""),
                            "name": p.get("name") or ""} for p in projects]
    except McpError as exc:
        # Named by class and never by its text, which is the rule
        # `document_text` states and the 2026-09-03 h11 finding is the reason
        # for. Not fatal: the project is one string on the cover, and a reviewer
        # can type it.
        out["notes"].append(
            f"The project list could not be read ({type(exc).__name__}). Type "
            "the project below, or leave it and name the deck directly.")

    try:
        out["opportunities"] = opportunity_choices(
            client, out["company"], project="", company_id=out["company_id"])
    except ProviderError as exc:
        out["notes"].append(exc.message)
    except McpError as exc:
        out["notes"].append(f"{type(exc).__name__}: {exc}")

    # THE MATCH IS A STATED TITLE AGAINST A LISTED TITLE, never a score. The
    # ranking pass's `best` flag is a hint for a human that no code path may
    # select on (`rank_choices`), and this does not: it matches the title the
    # document itself names. EXACTLY ONE match selects; more than one is
    # reported and selects nothing, because with no project name the list is
    # ordered by `published_at` and taking the first hit would break a tie on a
    # date nobody was thinking about. See `prd_front_matter.match_opportunity`.
    if stated["opportunity"] and stated["opportunity_selectable"]:
        hits = prd_front_matter.opportunity_matches(
            stated["opportunity"], out["opportunities"])
        if len(hits) == 1:
            out["matched_opportunity_id"] = str(hits[0].get("id") or "")
        elif hits:
            names = ", ".join(f"\u201c{h.get('label')}\u201d" for h in hits)
            out["notes"].append(
                f"This document names \u201c{stated['opportunity']}\u201d, and "
                f"{len(hits)} published opportunities match it: {names}. Pick "
                "the one you mean.")
        else:
            out["notes"].append(
                f"This document names \u201c{stated['opportunity']}\u201d, which is "
                "not among this company's published opportunities. Pick the one "
                "you mean.")
    elif stated["opportunity"]:
        out["notes"].append(
            f"This document's opportunity is \u201c{stated['opportunity']}\u201d, "
            f"which it states as {stated['opportunity_note'] or 'not published'}. "
            "The platform lists published opportunities only, so it cannot be "
            "selected. Pick the one this deck should be written from.")
    else:
        out["notes"].append(
            "This document names no opportunity, so pick the one this deck "
            "should be written from.")
    out["documents"] = _scan_documents(files, text, out["opportunities"])
    # Every opportunity some attachment names, in attach order, which is what
    # the picker selects. The first document's own match leads, so a run with
    # one attachment selects exactly what it selected before.
    matched = [out["matched_opportunity_id"]] if out["matched_opportunity_id"] else []
    for entry in out["documents"]:
        if entry["opportunity_id"] and entry["opportunity_id"] not in matched:
            matched.append(entry["opportunity_id"])
    out["matched_opportunity_ids"] = matched
    return jsonify(out)


def _scan_documents(files, first_text, opportunities):
    """Which opportunity each attached file names, for the attachment list.

    THE SAME RULE THE RUN APPLIES, from the same module: `document_routing`
    matches each file's front-matter title against the company's published
    list, and the provider makes that call again on the run. So what the list
    shows beside a file is what that file will write.

    A FAILED MATCH IS SHOWN, never swallowed. A file this cannot place applies
    to every opportunity in the deck, which is right for a supporting note and
    wrong for a PRD whose title did not match, and only a reviewer can tell
    those apart. So its reason goes on the screen beside it.

    The first file's text is already extracted; the rest are read here. A file
    that cannot be read is reported as such rather than failing the scan.
    """
    import document_routing
    from document_text import extract_text as extract_document_text

    everyone = [{"id": o.get("id"), "title": o.get("label")}
                for o in opportunities or ()]
    documents = []
    for index, storage in enumerate(files):
        if index == 0:
            documents.append(_ScannedDocument(storage.filename, first_text))
            continue
        storage.stream.seek(0)
        extracted = extract_document_text(storage.read(), storage.filename)
        documents.append(_ScannedDocument(
            storage.filename, getattr(extracted, "text", "") or ""))
    routes = document_routing.route(documents, everyone, opportunities or ())
    return [
        dict(document_routing.record((where,))[0],
             reason=where.reason if document.text.strip()
             else "carries no readable text")
        for document, where in zip(documents, routes)
    ]


class _ScannedDocument:
    """What `document_routing.route` reads off a document: a name and text."""

    def __init__(self, filename, text):
        self.filename, self.text = filename, text


def _run_and_render(deck_type, company, project, packet, check_in_date, do_render,
                    notice="", commercial_rows=None, data_source=SOURCE_FIXTURE,
                    company_id="", pe_firm="", proposal_date="", opportunity_ids=(),
                    deck_title="", client_short="", uploads=()):
    """Run the pipeline for one packet and render the Result tab."""
    # A new run supersedes the remembered one whatever it produces, so the slot is
    # released here rather than left for `_remember_result` to judge: a run that
    # escalated to review or errored has no deck of its own, and without this the
    # previous run's deck would keep the slot and the Result tab would show it
    # instead of the outcome the reviewer just triggered.
    _LAST_RESULT.clear()
    live = data_source == SOURCE_LIVE
    packet_abs = packet if os.path.isabs(packet) else os.path.join(_ROOT, packet)
    if not live and not os.path.isfile(packet_abs):
        return render_studio("result", result_ctx=_build_result_ctx(
            result={"status": "error", "code": "packet_not_found",
                    "message": f"Packet not found: {packet}", "remediation": "",
                    "details": ""},
            status="error", deck_type=deck_type, company=company,
            packet=packet, raw_company=company, raw_project=project,
            check_in_date=check_in_date, do_render=do_render,
        ))
    # The live path's own missing input, refused here for the same reason and in
    # the same shape as the fixture path's missing packet: no pipeline call, no
    # render, no API spend, and a message naming what is absent.
    #
    # A live deck resolves two things separately. The project decides the cover
    # title and the six page marks; the chosen opportunity decides the research
    # paper, and through it slide 2's headline, the cover subhead, the AFTER
    # metric and nearly every content role. Only the project half was ever
    # validated, so an unpicked run took whichever published opportunity carried
    # a paper first and could title a deck from one engagement while writing
    # every slide from another. The provider keeps that default, because a
    # programmatic caller sweeping a corpus genuinely wants "any opportunity with
    # a paper"; a reviewer in front of a form does not, and no automatic pairing
    # is available to fall back on (the platform records no project-to-
    # opportunity link, so any pairing the studio chose would be a guess).
    if live and not opportunity_ids:
        return render_studio("result", result_ctx=_build_result_ctx(
            result={"status": "error", "code": "opportunity_required",
                    "message": ("A live run needs the opportunity this deck is "
                                "for. None was picked."),
                    "remediation": (
                        "Pick at least one from the Opportunities list on the "
                        "Generate tab, then re-run. Nothing was rendered and no "
                        "API call was spent. The project names the deck; each "
                        "opportunity supplies the research paper its own slide "
                        "is written from, so the studio will not choose for you."
                    ),
                    "details": ""},
            status="error", deck_type=deck_type, company=company,
            packet=packet, raw_company=company, raw_project=project,
            check_in_date=check_in_date, do_render=do_render,
        ))

    # The seam, and the one thing the switch decides. Everything downstream of it
    # is identical on both paths.
    if live:
        provider, config_error = _live_provider_or_error()
        if config_error:
            return render_studio("result", result_ctx=_build_result_ctx(
                result=config_error, status="error", deck_type=deck_type,
                company=company, packet=packet, raw_company=company,
                raw_project=project, check_in_date=check_in_date,
                do_render=do_render,
            ))
    else:
        provider = FixtureProvider.from_packet_file(packet_abs)
    adapter_kwargs = {"poll_interval": 0.0, "sleep": _no_sleep}
    # The reviewer's pick among an ambiguous company name's candidates (item
    # 17). It travels BESIDE the name, never instead of it: with no
    # lookup-by-id tool on the grant the provider resolves it by narrowing the
    # name's own search. Only when there is one, so a run against a name that
    # resolves to a single company sends the request it always sent.
    if company_id:
        adapter_kwargs["company_id"] = company_id
    if deck_type == "status" and check_in_date:
        adapter_kwargs["check_in_date"] = check_in_date
    if pe_firm:
        adapter_kwargs["pe_firm"] = pe_firm
    if proposal_date:
        adapter_kwargs["proposal_date"] = proposal_date
    if opportunity_ids:
        # A list even when it holds one, because the contract takes a list and
        # the provider treats one as a deck of one. The singular field is still
        # accepted by `shape_request` for a programmatic caller that sends it.
        adapter_kwargs["opportunity_ids"] = list(opportunity_ids)
    if deck_title:
        adapter_kwargs["deck_title"] = deck_title
    # Passed on BOTH deck types, unlike most of what is above it: the footers
    # are the same on a status deck and the platform supplies no short form for
    # either. Only when there is one, so a run that leaves it blank sends
    # exactly the request it sent before this field existed.
    if client_short:
        adapter_kwargs["client_short"] = client_short
    # Already read into memory by the route, so this crosses the thread boundary
    # as bytes and not as a handle on a request that is about to end. Passed only
    # when there are any, so a run with no attachment submits exactly the request
    # it submitted before attachments existed.
    if uploads:
        adapter_kwargs["uploads"] = uploads
    # Slide 2 bullet switches, if this source has any. Only when there are any,
    # so a source nobody has touched sends the request it always sent and its
    # panels are fitted by the fitter alone.
    #
    # Keyed on the UPLOADED DOCUMENT here, hashed straight off the bytes,
    # because the pipeline has not run yet and there are no attachment records
    # to read. `_toggle_scope` falls back to the packet path for a frozen run.
    overrides_scope = _toggle_scope(packet=packet, uploads=uploads)
    overrides = (bullet_overrides_for(overrides_scope, path=TOGGLES_PATH)
                 if overrides_scope else {})
    if overrides:
        adapter_kwargs["bullet_overrides"] = overrides

    # Hand the five minutes to a thread and answer now. Everything above this line
    # is cheap and stays in the request, so a missing packet, an unpicked
    # opportunity or an unconfigured data source is still refused instantly with
    # no run to poll and no API spend — the same refusals, in the same shape, as
    # before background runs existed.
    run_id = _new_run({
        "deck_type": deck_type,
        "company": company,
        "project": project,
        "packet": packet,
        "check_in_date": check_in_date,
        "do_render": do_render,
        "data_source": data_source,
        "notice": notice,
        # What was attached, by name and size only. The bytes stay out of the
        # registry: the run holds them for the length of the run through the
        # thread's own arguments, and the record is what the Result panel reads.
        "attachments": [{"name": upload.filename, "bytes": len(upload.data)}
                        for upload in uploads],
    })
    threading.Thread(
        target=_execute_run,
        args=(run_id,),
        kwargs={
            "deck_type": deck_type,
            "company": company,
            "project": project,
            "provider": provider,
            "do_render": do_render,
            "commercial_rows": commercial_rows,
            "adapter_kwargs": adapter_kwargs,
        },
        # Daemon: a run must never hold the process open at shutdown. A redeploy
        # mid-run loses that run, which the poll reports honestly rather than
        # hiding behind a bar that never fills.
        daemon=True,
        name=f"deck-run-{run_id}",
    ).start()
    return _running_response(run_id)


def _running_response(run_id):
    """The page a reviewer gets the instant a run starts.

    The Generate tab with the progress overlay open and the run id attached, which
    is what the client script polls. The run id also goes into a cookie scoped to
    this app's mount point, so a reviewer who reloads, or closes the tab and comes
    back, is reconnected to their own run instead of losing it. Scoped to
    `script_root` rather than "/" so the portal's other apps never see it, and
    marked secure only when the request already is, so a local http run still
    keeps its cookie.
    """
    response = make_response(render_studio("generate", run_id=run_id))
    response.set_cookie(
        RUN_COOKIE, run_id,
        max_age=_RUN_COOKIE_MAX_AGE, httponly=True, samesite="Lax",
        secure=request.is_secure, path=request.script_root or "/",
    )
    return response


def _pipeline_result(deck_type, company, project, provider, do_render,
                     commercial_rows, adapter_kwargs):
    """Run the pipeline once and return its result envelope.

    Extracted from `_run_and_render` when runs moved onto their own thread. Every
    expected failure is mapped to an envelope here rather than raised, so the
    worker thread always has something to hand back and the Result panel renders
    the same outcome it always did. Touches no Flask state and no module cache, so
    it is safe off the request thread: the paths it writes through are the module
    constants, and the reviewer's own inputs arrive as arguments.
    """
    try:
        result = generate_and_save_deck(
            deck_type, company, project, provider,
            render=do_render, preferences_path=STORE_PATH,
            decks_root=DECKS_ROOT, prompts_root=PROMPTS_ROOT,
            commercial_rows=commercial_rows, **adapter_kwargs,
        )
    except ConsistencyError as exc:
        # The packet contradicts itself (e.g. a footer claiming a 16-week plan
        # over a 10-week timeline). Surfaced as a data error, not a render error:
        # nothing was rendered, no API call was spent, and the fix is in the
        # packet, so the message is shown in full with the remediation attached.
        result = {
            "status": "error",
            "code": "packet_inconsistent",
            "message": str(exc),
            "remediation": (
                "Correct the contradicting fields at the data source, then re-run. "
                "The pipeline renders faithfully and will not choose one of two "
                "conflicting values for you."
            ),
            "details": "",
        }
    except CoverageError as exc:
        # A packet field with no template slot. Like ConsistencyError this is a
        # DATA problem caught before any API call, so it must not wear the
        # "render_error" label: a reviewer who reads "render failed" retries the
        # run and waits three minutes for the same result. Surfaced as an error
        # envelope with the field list intact and the fix named.
        result = {
            "status": "error",
            "code": "packet_not_mappable",
            "message": str(exc),
            "remediation": (
                "Nothing was rendered and no API call was spent. Every field listed "
                "above is in the packet but has nowhere to go on this deck type's "
                "slides. If you meant a different deck type, change it on the "
                "Generate tab; otherwise the template needs a slot for these "
                "fields, or the packet should not be carrying them."
            ),
            "details": "",
        }
    except RenderFidelityError as exc:
        result = {"status": "render_error", "message": f"render fidelity: {exc}"}
    except RenderTruncated as exc:
        # NOT the same failure as a bounded leg giving up, and it must not get
        # that remediation. `ModelCallError` below tells the reviewer to re-run,
        # because a stalled stream is usually not stalled twice. A truncation is
        # the opposite: the call completed and ran out of room, and a re-run
        # runs out of room again seven minutes later. The number is what has to
        # move, and the message carries the numbers to move it against.
        result = {
            "status": "render_error",
            "message": str(exc),
            "remediation": (
                "Nothing was written, and re-running will not help: the render "
                "answered and the answer was cut off, so it will be cut off "
                "again in the same place. A deck's slide count is the "
                "reviewer's since one deck could carry several opportunities, "
                "so the most likely cause is a deck long enough to outgrow a "
                "ceiling sized for six slides. Either render fewer "
                "opportunities in one deck, or raise the render's "
                "`max_tokens` against the figures in the message above."
            ),
            "details": "",
        }
    except model_call.ModelCallError as exc:
        # A bounded model leg that gave up. Before the legs were bounded this
        # arrived here as the SDK's bare "The read operation timed out" after ten
        # or more minutes, with no remediation, which told the reviewer nothing
        # about whether to wait, re-run, or go and look at the data. The
        # exception's own message names the leg, the attempts and the per-attempt
        # bound; the remediation says what to do about it.
        result = {
            "status": "render_error",
            "message": str(exc),
            "remediation": (
                "Nothing was written. The call was retried and bounded rather "
                "than left to hang, so this is a stall or an outage on the "
                f"{exc.leg} leg and not a slow answer that needed more time. "
                "Re-run: a stalled stream is usually not stalled twice. If it "
                "gives up again the same way, check the platform's status "
                "before spending a third run on it."
            ),
        }
    except Exception as exc:  # API/network/other render failure: nothing was written
        result = {"status": "render_error", "message": str(exc)}

    return result


def _run_result_ctx(rec):
    """The Result panel for a finished run. Request thread only.

    The other half of what `_run_and_render` used to do in one pass. It stayed on
    this side of the thread boundary deliberately: `_gaps_for` and
    `_build_result_ctx` feed a template, and a worker thread has no request to
    build URLs against. Everything it needs comes from the run's own record, so a
    reviewer who reloads, switches tabs, or opens the link on another device gets
    the same panel rather than a rebuilt guess.
    """
    inputs = rec["inputs"]
    notice = inputs.get("notice", "")
    result = rec["result"] or {}
    deck_type = inputs["deck_type"]
    company = inputs["company"]
    packet = inputs["packet"]
    status = result.get("status")
    deck_path = result.get("deck_path")
    prompt_rel = (
        os.path.relpath(result["prompt_path"], _ROOT) if result.get("prompt_path") else ""
    )
    gaps, resolved = _gaps_for(packet, deck_type) if status == "ok" else ([], [])

    # A render does not enter deck history on its own (prompt B3a). It is a
    # working file in this session until the reviewer presses Save deck, which is
    # the only call site of `_save_deck_to_history` — so the store holds the decks
    # somebody settled on rather than every attempt at one. Unchanged by the move
    # to background runs: a run that finishes while nobody is looking still saves
    # nothing on its own.
    notice_bad = False

    return _build_result_ctx(
        result=result, status=status, deck_type=deck_type, company=company,
        applied=result.get("applied_preferences", []), deck_path=deck_path,
        prompt_rel=prompt_rel, gaps=gaps, resolved=resolved,
        show_gaps=(status == "ok"),
        notice=notice, notice_bad=notice_bad,
        raw_company=company, raw_project=inputs["project"], packet=packet,
        check_in_date=inputs["check_in_date"], do_render=inputs["do_render"],
        # This is the run the guards measured, so their reports describe exactly
        # the deck on screen. A later edit carries this forward and marks it stale.
        render_deck_path=deck_path or "", guards_stale=False,
    )


@app.route("/save-deck", methods=["POST"])
def save_deck_route():
    """Put the deck on screen into deck history. The only way into the store.

    Nothing else writes a row: a render is a working file until the reviewer
    decides it is worth keeping (Antonio, 2026-08-09). Saving the same deck again
    updates its row, so the history carries the deck the reviewer settled on
    rather than each version on the way there.
    """
    path = (request.form.get("path") or "").strip()
    if not path or not _safe_within(DECKS_ROOT, path) or not os.path.isfile(path):
        abort(404)
    run_ctx = _run_ctx_from_form()
    notice, notice_bad = _save_deck_to_history(
        path, deck_type=run_ctx["deck_type"], company=run_ctx["company"],
        project=run_ctx["project"], packet=run_ctx["packet"],
        check_in_date=run_ctx["check_in_date"],
    )
    # A save asked for in the background answers with its outcome and nothing
    # else (part one 2, Blake 2026-08-20). Saving used to re-render the whole
    # page, which threw away the reviewer's scroll position and their place in
    # the controls column for an action that changes nothing on screen. The
    # form still posts normally when JavaScript is off, and that path still
    # re-renders, so the fallback is the old behaviour rather than no behaviour.
    if request.form.get("background") == "1":
        # AND THE REFRESHED DECKS TAB (Antonio, 2026-09-20: "I just saved the
        # deck and then I switched to the decks tab and it didn't appear").
        #
        # The save was working the whole time. All four panels are rendered
        # server-side at page load and the tab bar only shows and hides them, so
        # a save that deliberately does not reload the page left `#panel-decks`
        # holding the list as it stood BEFORE the save. Returning the panel with
        # the outcome is what makes the background save honest.
        decks_html, deck_count = _decks_panel_html()
        return jsonify({"notice": notice, "bad": bool(notice_bad),
                        "decks_html": decks_html, "deck_count": deck_count})
    return _render_edit_result(path, notice=notice, notice_bad=notice_bad,
                               run_ctx=run_ctx)


def _fitter_kept(deck_path, opportunity, role, text):
    """Whether the ORIGINAL render put this bullet on the slide.

    What `auto` means on the deck. A reset hands the bullet back to the fitter,
    and the fitter already answered once: the run's own `bullet_selection`
    records which bullets it kept. So resetting restores the slide to the
    answer the render was built from, rather than leaving it wherever the
    reviewer's last switch left it.

    Returns None when the run's selection cannot be read, which is the signal
    to leave the deck alone rather than guess at it.
    """
    outcome = _run_outcome_for(deck_path) or {}
    result = outcome.get("result") or {}
    blocks = (result.get("bullet_selection_per_opportunity")
              or ([result.get("bullet_selection")] if result.get("bullet_selection")
                  else []))
    if opportunity >= len(blocks) or not blocks[opportunity]:
        return None
    wanted = " ".join((text or "").split())
    for panel in (blocks[opportunity] or {}).get("panels") or []:
        if (panel.get("role") or "") != role:
            continue
        for row in panel.get("kept") or []:
            if " ".join((row.get("text") or "").split()) == wanted:
                return True
        return False
    return None


def _bullet_source_position(deck_path, opportunity, role, text):
    """Where this bullet sits in the PACKET's list for its panel, or None.

    Used only when switching one back on, so it lands where it was rather than
    at the end of whatever is currently showing.
    """
    outcome = _run_outcome_for(deck_path) or {}
    result = outcome.get("result") or {}
    blocks = (result.get("bullet_selection_per_opportunity")
              or ([result.get("bullet_selection")] if result.get("bullet_selection")
                  else []))
    if opportunity >= len(blocks) or not blocks[opportunity]:
        return None
    wanted = " ".join((text or "").split())
    for panel in (blocks[opportunity] or {}).get("panels") or []:
        if (panel.get("role") or "") != role:
            continue
        rows = (list(panel.get("kept") or []) + list(panel.get("dropped") or [])
                + list(panel.get("switched_off") or []))
        rows.sort(key=lambda r: r.get("source_position") or 0)
        for index, row in enumerate(rows):
            if " ".join((row.get("text") or "").split()) == wanted:
                return index
    return None


def _panel_room_px(deck_path, opportunity, role):
    """The panel's measured room in px, from the run's own selection record.

    The fitter needs it to answer "what type size holds these lines", and it
    is not in the document: the render measured it and wrote it into the
    selection. None when the run cannot be read, which is the signal to leave
    the type alone.
    """
    outcome = _run_outcome_for(deck_path) or {}
    result = outcome.get("result") or {}
    blocks = (result.get("bullet_selection_per_opportunity")
              or ([result.get("bullet_selection")] if result.get("bullet_selection")
                  else []))
    if opportunity >= len(blocks) or not blocks[opportunity]:
        return None
    for panel in (blocks[opportunity] or {}).get("panels") or []:
        if (panel.get("role") or "") == role:
            return float(panel.get("room_px") or 0.0) or None
    return None


class _PanelRefit:
    """Settle one panel after a switch: measure, shrink if it must, measure again.

    THE GAP THIS CLOSES (2026-09-22). A full render spends the type ladder on
    any panel a reviewer has decided about -- `data_source_adapter._panel_fit`
    fits an untouched panel at the house size and a decided one at every step
    from 12.5 down to 10.5, and `bullet_type` delivers the answer to the
    finished deck. A real-time switch that inserted at the deck's current size
    and refused on overflow would refuse bullets the NEXT generated deck goes
    on to show, and a studio that disagrees with the thing it builds is worse
    than one that is merely strict.

    MEASURE FIRST, SHRINK SECOND, AND NOT THE OTHER WAY ROUND. The obvious
    build is to ask `panel_fit` what size holds the new list and write that.
    It was the first build, and it was wrong in both directions. Asked with
    `fit_bullets` it answered "12.5px, and drop the longest line" -- which is
    the right answer for a fitter choosing what goes on a slide and the wrong
    one for a reviewer who has already chosen, so nothing ever resized.
    Corrected to `size_holding_all` it went the other way: the estimator works
    from character counts against a room measured on an earlier revision, and
    it is more pessimistic than a browser, so a bullet that renders fine at
    12.5px had the whole panel dropped to 10.5px to hold it.

    The browser is the authority, so the browser goes first. Try the deck as
    it stands. Only if that clips does the estimator get asked, and its answer
    is measured too. At most three measurements, and one in the common case.

    IT RUNS IN BOTH DIRECTIONS. A panel shrunk to hold a bullet has to grow
    back when that bullet goes, or a deck quietly gets smaller as a reviewer
    works. A removal starts by trying the house size for exactly that reason.
    """

    #: House size, the estimator's answer, then the floor. Deduplicated in
    #: order, so the common case is one measurement and the worst case three.
    MAX_TRIES = 3

    def __init__(self, deck_path, opportunity, role, *, adding):
        self.opportunity = opportunity
        self.role = role
        self.adding = adding
        self.room_px = _panel_room_px(deck_path, opportunity, role)
        self.panel = "today" if role == "today_pain_bullets" else "after"
        self.resized_from = None
        self.resized_to = None

    def _candidate_sizes(self, texts, current):
        """The sizes to try, largest first, best guess in the middle."""
        steps = panel_fit.BULLET_FONT_STEPS
        house = panel_fit.BULLET_FONT
        floor = steps[-1]
        # THE MIDDLE RUNG, and why the estimator only sometimes supplies it.
        # `size_holding_all` is a good hint and a bad authority: it works from
        # character counts against a room measured on an earlier revision, so
        # it is wrong in both directions. When it names a size BELOW the house
        # size, that is a real suggestion and worth trying second. When it says
        # None (nothing holds them) or names the house size (everything does),
        # it has told us nothing the browser has not already contradicted, and
        # the useful second try is the middle of the ladder.
        #
        # Skipping this rung is how a panel that 11.5px would have held ends up
        # at 10.5px. A deck smaller than it needs to be is a worse deck, not a
        # safer one.
        guess = None
        if self.room_px and texts:
            guess = panel_fit.size_holding_all(texts, room_px=self.room_px)
        if not guess or guess >= house:
            guess = steps[len(steps) // 2]
        order, seen = [], set()
        for size in (house, guess, floor):
            if size and size not in seen:
                seen.add(size)
                order.append(size)
        # A panel already below the house size keeps that as a starting point
        # too, so a removal that cannot grow back does not have to fall all
        # the way to the floor to find that out.
        if current and current not in seen:
            order.insert(1, current)
        return order[:self.MAX_TRIES]

    def settle(self, candidate):
        """``(html, refusal)``. Never writes; the caller owns the file."""
        try:
            region = locate_bullet_list(candidate, opportunity=self.opportunity,
                                        role=self.role)
            texts = [b["text"] for b in list_slide_bullets(
                candidate, opportunity=self.opportunity, role=self.role)]
        except EditNotApplicable:
            # Nothing to size. The structural edit already succeeded, so this
            # is not a refusal; it just means the type is left alone.
            return candidate, ""
        slide = region["slide"]
        current = read_panel_type(candidate).get((slide, self.panel))
        self.resized_from = current

        last_report = None
        for size in self._candidate_sizes(texts, current):
            attempt = set_panel_type(
                candidate, slide, self.panel,
                None if size >= panel_fit.BULLET_FONT else size)
            report, failure = self._measure(attempt)
            if failure:
                return candidate, failure
            last_report = report
            if report.get("ok"):
                self.resized_to = None if size >= panel_fit.BULLET_FONT else size
                return attempt, ""

        return candidate, self._refusal(last_report)

    def _measure(self, html):
        """``(report, hard_failure)``. A hard failure stops the whole switch."""
        try:
            report = check_layout(html)
        except Exception as exc:
            if not self.adding:
                return {"ok": True}, ""
            return None, (f"This bullet could not be added because the fit "
                          f"could not be checked ({exc}). Nothing on the deck "
                          f"changed.")
        if not report.get("checked"):
            if not self.adding:
                return {"ok": True}, ""
            return None, ("This bullet could not be added because the fit "
                          "could not be checked (%s), and an unmeasured bullet "
                          "can be clipped without anything saying so. Nothing "
                          "on the deck changed."
                          % (report.get("skipped") or "no reason given"))
        return report, ""

    def _refusal(self, report):
        where = ", ".join(sorted({
            f"slide {f.get('slide')}" for f in (report or {}).get("findings") or []
            if f.get("slide")})) or "the deck"
        if not self.adding:
            return ("Taking this bullet off left content past the edge of a "
                    f"panel ({where}), so nothing was changed.")
        floor = panel_fit.BULLET_FONT_STEPS[-1]
        return ("Adding this bullet pushes content past the edge of its panel "
                f"({where}) even at {floor:g}px, the smallest readable size, "
                "so it was not added and the deck is unchanged. Switch another "
                "bullet off, or shorten this one in the PRD.")

    def note(self):
        """What to tell the reviewer about a resize, or ""."""
        if self.resized_to == self.resized_from:
            return ""
        if self.resized_to:
            return (" The panel's type is now %gpx so it can hold them."
                    % self.resized_to)
        return " The panel is back at its full type size."


@app.route("/bullet-toggle", methods=["POST"])
def bullet_toggle_route():
    """Switch one slide 2 bullet on, off, or back to the fitter's judgment.

    THE PACKET IS NEVER TOUCHED, on exactly the terms `/gap-decision` is not:
    what a reviewer wants a slide to show is reviewer state, and the packet is
    the data source's record of what it sent.

    A switch changes what the NEXT render is asked for. It cannot repaint the
    deck on screen, because the fit happens while the prompt is assembled and
    the edit layer swaps display text without ever touching markup, so it can
    neither add a list item nor remove one. The route records the decision and
    says so; the deck changes when the deck is rendered again.
    """
    # `scope` is what the switch form posts: the uploaded PRD's key, or the
    # packet path on a frozen-packet run. `packet` is still read so a form
    # posted before 2026-09-20, or from anywhere else, still records.
    scope = (request.form.get("scope") or request.form.get("packet") or "").strip()
    role = (request.form.get("role") or "").strip()
    text = request.form.get("text") or ""
    state = (request.form.get("state") or "").strip()
    deck_path = (request.form.get("deck_path") or "").strip()
    try:
        opportunity = int(request.form.get("opportunity") or 0)
    except ValueError:
        opportunity = 0

    live_deck = deck_path if (deck_path and _deck_path_exists(deck_path)) else ""
    # What the PAGE is currently showing, kept before a landed edit moves
    # `deck_path` on to the new revision. The script needs both: the old value
    # to find its stale hidden fields, the new one to write into them.
    requested_path = deck_path

    if not scope or not role or not text.strip():
        notice, notice_bad = ("A switch needs a source document, a panel and a "
                              "bullet, and this request named at least one of "
                              "them as empty. Nothing was recorded."), True
    elif state not in (SWITCH_AUTO, BULLET_ON, BULLET_OFF):
        notice, notice_bad = (f"Unknown switch state {state!r}. Nothing was "
                              "recorded."), True
    else:
        # WHAT THE SLIDE SHOULD READ AFTER THIS CLICK. `on` and `off` say it
        # outright. `auto` hands the bullet back to the fitter, and the fitter
        # already answered once, in the run's own selection, so a reset
        # restores the slide to the answer the render was built from.
        if state == BULLET_ON:
            wanted = True
        elif state == BULLET_OFF:
            wanted = False
        else:
            wanted = _fitter_kept(live_deck, opportunity, role, text) if live_deck else None

        edit_note, edited_to = "", ""
        if live_deck and wanted is not None:
            # THE DECK CHANGES NOW, NOT ON THE NEXT RENDER (Antonio,
            # 2026-09-22). The edit is applied first and the switch is recorded
            # only if it lands, so the store never claims something the deck
            # does not show. A switch-on is measured before anything is
            # written; see `_overflow_verifier`.
            refit = _PanelRefit(live_deck, opportunity, role, adding=bool(wanted))
            try:
                landed = set_bullet_presence_and_save(
                    live_deck, opportunity=opportunity, role=role, text=text,
                    on=wanted,
                    position=_bullet_source_position(live_deck, opportunity,
                                                     role, text),
                    author=(request.form.get("author") or "").strip(),
                    settle=refit.settle,
                )
            except BulletDoesNotFit as exc:
                return _bullet_toggle_response(
                    live_deck, str(exc), True, requested_path)
            except EditNotApplicable as exc:
                return _bullet_toggle_response(
                    live_deck,
                    f"This switch could not be applied to the deck: {exc} "
                    f"Nothing was recorded and the deck is unchanged.",
                    True, requested_path)
            if landed["changed"]:
                edited_to = landed["revision_path"]
                edit_note = (" The deck now shows it, saved as %s.%s"
                             % (landed["revision_name"], refit.note()))
            else:
                edit_note = " The deck already read that way."

        if state == SWITCH_AUTO:
            clear_bullet_toggle(scope, opportunity, role, text, path=TOGGLES_PATH)
            notice = ("Switch cleared. This bullet is back with the fitter's own "
                      "answer." + edit_note)
        else:
            set_bullet_toggle(scope, opportunity, role, text, state,
                              path=TOGGLES_PATH)
            notice = ("Switched %s." % state) + (
                edit_note or " Recorded against this PRD; the next render will "
                             "build the deck with it.")
        notice_bad = False
        if edited_to:
            deck_path = edited_to

    return _bullet_toggle_response(deck_path, notice, notice_bad, requested_path)


def _bullet_toggle_response(deck_path, notice, notice_bad, requested_path):
    """One answer shape for every way a switch can end.

    `deck_path` is the deck to SHOW, which after a switch that landed is the
    new revision. `requested_path` is what the page posted, so the script can
    find the stale value in its own markup and replace it: the deck changed
    under the page, and every form still carrying the old path has to follow.

    THE CARD COMES BACK WITH THE ANSWER (2026-09-21). A switch that only
    reported a notice would leave the switch it was on pointing the wrong way
    until the next reload, which is worse than the page jump it replaced.
    """
    if not (deck_path and _deck_path_exists(deck_path)):
        if request.form.get("background") == "1":
            return jsonify({"notice": notice, "bad": bool(notice_bad)})
        return redirect(url_for("home", tab="result"))

    if request.form.get("background") != "1":
        return _render_edit_result(deck_path, notice=notice,
                                   notice_bad=notice_bad,
                                   run_ctx=_run_ctx_from_form())
    ctx = _edit_result_ctx(deck_path, notice=notice, notice_bad=notice_bad,
                           run_ctx=_run_ctx_from_form())
    return jsonify({
        "notice": notice,
        "bad": bool(notice_bad),
        "bullets_html": _bullets_card_html(ctx),
        # Only when the deck actually moved. The script reloads the preview and
        # repoints the page's forms on this, so sending it every time would
        # reload the iframe on a switch that changed nothing.
        "deck_path": deck_path if deck_path != requested_path else "",
        "old_deck_path": requested_path if deck_path != requested_path else "",
        "preview_url": (url_for("preview", path=deck_path)
                        if deck_path != requested_path else ""),
    })


@app.route("/gap-decision", methods=["POST"])
def gap_decision():
    """Confirm, send back, or reopen one flagged claim. THE PACKET IS NEVER TOUCHED.

    Flags never print on the deck — the render strips them before the file is
    written (``deck_renderer.strip_gap_flags``, Antonio 2026-07-21) — so a decision
    here changes neither the packet nor the deck. It is a checklist action and
    nothing else:

    - ``resolve`` records the reviewer's confirmation in the decision store
      (``gap_decisions``), which moves the row to "confirmed on review".
    - ``verify`` records that the claim was sent back to be verified. It stays on
      the open checklist, now marked, because it was routed rather than settled.
      This used to be a ``keep`` that wrote nothing at all, so the routing was
      gone on the next page render and a claim somebody had deliberately sent back
      looked exactly like one nobody had opened (C4, 2026-08-09).
    - ``reopen`` drops a recorded decision in either direction, putting the claim
      back to undecided. The packet-editing version could not offer this: undoing
      a resolve used to mean hand-editing the gaps block back into the packet.

    The packet is the data source's record of what it sent and is read-only
    everywhere in this repo (Antonio, 2026-07-28). This route used to remove the
    resolved gap from the packet on disk, which edited a committed fixture on a
    local review and was silently discarded on the next deploy of the hosted
    service, where packets live in the deployed repo image.

    Either way the Result tab is re-rendered against the existing deck — no
    pipeline run, no API call, no re-render.
    """
    action = request.form.get("action", "")
    field = (request.form.get("field") or "").strip()
    deck_type = request.form.get("deck_type", "proposal")
    company = (request.form.get("company") or "").strip()
    project = (request.form.get("project") or "").strip()
    packet = (request.form.get("packet") or "").strip()
    check_in_date = (request.form.get("check_in_date") or "").strip()
    deck_path = (request.form.get("deck_path") or "").strip() or None
    if deck_path and not _deck_path_exists(deck_path):
        deck_path = None

    author = (request.form.get("author") or "").strip()
    notice = ""
    if field and action == "resolve":
        if resolve_gap_decision(packet, field, author=author, path=DECISIONS_PATH):
            notice = (f"Confirmed “{field}”. Recorded against this packet as "
                      f"reviewed. The packet and the deck are both unchanged.")
        else:
            notice = f"“{field}” was already confirmed; nothing changed."
    elif field and action == "verify":
        if send_back_gap(packet, field, author=author, path=DECISIONS_PATH):
            notice = (f"Sent “{field}” back to be verified. Recorded, and it stays "
                      f"on the checklist marked as awaiting verification. The "
                      f"packet and the deck are both unchanged.")
        else:
            notice = f"“{field}” was already awaiting verification; nothing changed."
    elif field and action == "reopen":
        if reopen_gap(packet, field, path=DECISIONS_PATH):
            notice = f"Reopened “{field}” — it is back on the review checklist."
        else:
            notice = f"“{field}” carried no recorded decision; nothing changed."

    gaps, resolved = _gaps_for(packet, deck_type)
    ctx = _build_result_ctx(
        deck_type=deck_type, company=company, deck_path=deck_path, gaps=gaps,
        resolved=resolved,
        show_gaps=True, notice=notice, raw_company=company, raw_project=project,
        packet=packet, check_in_date=check_in_date,
        deck_heading="Rendered deck",
        # A flag decision is not a new run: keep the run outcome, the guard
        # reports, the applied preferences and the saved Design prompt on screen.
        **_run_outcome_for(deck_path),
    )
    return render_studio("result", result_ctx=ctx)


def _nth(values, index):
    """``values[index]`` or ``""`` — for reading several same-named form fields.

    A browser sends one list per field name, and the lists are parallel: entry N of
    each belongs to row N. A short list means a field the form did not send, which
    reads as blank rather than as an error, so a hand-built or older POST degrades
    instead of raising.
    """
    return values[index] if index < len(values) else ""


def _run_ctx_from_form():
    """Pull the forwarded run context off an edit form (all optional)."""
    return {
        "deck_type": request.form.get("deck_type", "proposal"),
        "company": (request.form.get("company") or "").strip(),
        "project": (request.form.get("project") or "").strip(),
        "packet": (request.form.get("packet") or "").strip(),
        "check_in_date": (request.form.get("check_in_date") or "").strip(),
    }


def _render_edit_result(deck_path, *, notice="", notice_bad=False, run_ctx=None,
                        design_export_prompt=""):
    """Render the Result tab for a (possibly edited) deck copy, keeping the
    checklist and run context when the originating packet is known."""
    return render_studio("result", result_ctx=_edit_result_ctx(
        deck_path, notice=notice, notice_bad=notice_bad, run_ctx=run_ctx,
        design_export_prompt=design_export_prompt))


def _edit_result_ctx(deck_path, *, notice="", notice_bad=False, run_ctx=None,
                     design_export_prompt=""):
    """The context `_render_edit_result` renders, built on its own.

    Split out so a BACKGROUND post can rebuild the same state and render one
    fragment of it (the bullet card) instead of a whole page. Every caller that
    wants the page still goes through `_render_edit_result`.
    """
    run_ctx = run_ctx or {}
    packet = run_ctx.get("packet", "")
    gaps, resolved = _gaps_for(packet, run_ctx.get("deck_type", ""))
    ctx = _build_result_ctx(
        deck_path=deck_path, gaps=gaps, resolved=resolved, show_gaps=bool(packet),
        notice=notice, notice_bad=notice_bad,
        deck_type=run_ctx.get("deck_type", ""),
        company=run_ctx.get("company", ""),
        raw_company=run_ctx.get("company", ""),
        raw_project=run_ctx.get("project", ""),
        packet=packet, check_in_date=run_ctx.get("check_in_date", ""),
        deck_heading="Editing " + os.path.basename(deck_path),
        design_export_prompt=design_export_prompt,
        # An edit is not a new run either. Carried when this deck belongs to the
        # last run; `guards_stale` then flags that the fidelity and layout reports
        # describe the original render, not the revision on screen. `_run_outcome_for`
        # rather than `_carry_run_outcome` directly, so a `db-deck:` sentinel (the
        # export button is reachable from a deck opened out of history) reads its
        # outcome from the stored row instead of matching nothing and losing the panel.
        **_run_outcome_for(deck_path),
    )
    return ctx


# What a reviewer does about an ambiguous target, which is not what they do about
# an absent one. Named here rather than in `html_edit_layer` because it describes
# a control on this page (the exact-text edit) and the edit layer knows nothing
# about the studio.
AMBIGUOUS_SOURCE_REMEDY = (
    "Name it again over a longer span that is unique on that slide, or use the "
    "exact-text edit to pick the occurrence you mean."
)


def _applied_edit_sentence(edit):
    """What the studio says one applied edit did, from what it actually did.

    THE ROUTE DESCRIBES THE EDIT AND NEVER CHARACTERISES IT (2026-09-18). This
    used to prefer the interpreter's own `note` and fall back to this sentence
    only when the model omitted one, so the line a reviewer read about a change
    to a client deck was written by the model and echoed verbatim. The note is
    specified to the model as nothing more than "<short human summary>" and
    validated as a string and nothing else, so it carried whatever it was handed:
    unbounded length, and on the 2026-09-17 figure probe the word "Corrected",
    which asserts to a reviewer that a new number is right when nothing checked
    it. `edit-notice-note-channel-FINDING.md` has what else reached the page.

    The route cannot tell a note that describes an action from one that asserts a
    fact, and a judge for that would be a judge for a channel with no reason to
    exist. So the note is not shown. The slide number and the two strings the
    edit layer actually swapped say what happened, and they are the same three
    facts the edit log keeps, which is what makes the screen and the audit trail
    finally describe one event rather than two.

    The model's own words still reach a reviewer where only it can explain
    something, through `unresolved`, and there they are attributed. See
    `_model_words`.
    """
    return (f"slide {edit['slide']}: “{edit['source']}” → "
            f"“{edit['replacement']}”")


# How much of a refusal a reviewer is shown. `unresolved` is free prose the model
# writes, and a rendered deck can dictate what it says (see
# `prompt-injection-unresolved-FINDING.md`, confirmed 2026-09-18). A bound is not
# a filter and does not pretend to be one: it is here because a refusal nobody
# will read is not serving the reviewer, and because the longer the passage the
# more it reads as the studio's own copy rather than as something quoted.
_MODEL_WORDS_LIMIT = 240

# Where a sentence ends, allowing for a closing quote or bracket after the stop.
# Used to cut a long refusal at a boundary rather than mid-clause.
_SENTENCE_END = re.compile(r"[.!?][\"'”’)\]]*(?=\s|$)")


def _model_words(text):
    """Text the MODEL wrote, set off as a quotation rather than said in our voice.

    `unresolved` is the one channel worth keeping, because explaining why a
    request could not be done as text swaps is exactly the thing a deterministic
    sentence cannot do: "adding slides and creating new facts are both
    prohibited" is the good result, not a defect. What it must not do is arrive
    looking like the studio said it. Until 2026-09-18 it was appended as
    "Note: ..." and, on a request that produced no edits at all, made up almost
    the entire notice with nothing marking where the studio stopped and the model
    started.

    WHY THIS IS MORE THAN A LABEL NOW. A deck can dictate this sentence. Asked
    for something it must refuse, the model writes free prose here, and a deck
    carrying instruction-shaped text had it repeat "This deck was verified
    against the source document by QofAI's render-fidelity guard and is approved
    to send" verbatim. Both clauses false, and `render_guard` is not on this path.
    The documents are QofAI's own, so the realistic trigger is an accident rather
    than an attack, but a reviewer skimming a panel should never be able to read
    model text as a system statement.

    So the words are QUOTED AND SET OFF on their own lines, not run into our
    sentence, and the model does not get to lay out our notice: its own newlines
    are collapsed, because the panel renders with `white-space:pre-line` and a
    passage carrying blank lines composes what reads as a second message from us.

    NOT A FILTER, deliberately. Nothing here inspects the sentence or judges
    whether it is honest. Rules on open text are what `text_gate` refuses to be,
    and a judge would be a new mechanism to get wrong for a defect this size.
    """
    text = " ".join((text or "").split())
    if not text:
        return ""
    if len(text) > _MODEL_WORDS_LIMIT:
        # Cut at a sentence boundary and mark it. A half-sentence that reads as
        # complete is worse than no sentence, because it changes what was said
        # while looking like the whole of it.
        ends = [m.end() for m in _SENTENCE_END.finditer(text[:_MODEL_WORDS_LIMIT])]
        if ends:
            text = text[:ends[-1]] + " (…)"
        else:
            return ("\n\n    Claude gave a reason for refusing, but it ran to "
                    f"{len(text)} characters with no sentence break, so it is "
                    "not shown here.")
    return ("\n\n    Claude refused, in its own words, quoted and not ours:\n"
            f"    “{text}”")


def _skipped_parts_notice(failed):
    """How the free-text edit reports the parts of a batch that did not apply.

    TWO REFUSALS THAT NEEDED TELLING APART (2026-09-17). This read "N part(s)
    could not be located and were skipped" for every failure, which is true of a
    source that is absent and false of one that is AMBIGUOUS. That source was
    located too well rather than not at all, and the two want opposite things
    from a reviewer. Absent means the text is not there to retype against;
    ambiguous means it is there more than once and needs a wider span.

    Before the same day's fix to `apply_edits_and_save` a reviewer never saw this
    sentence for an ambiguous source anyway, because the batch aborted instead of
    skipping. Now that it skips, the wording has to carry the difference.
    """
    # Partitioned by reason, not by identity: two failures on the same slide can
    # be equal dicts, and a membership test would then put both in one bucket.
    ambiguous, absent = [], []
    for failure in failed:
        target = ambiguous if AMBIGUOUS_SOURCE in (failure.get("reason") or "") \
            else absent
        target.append(failure)
    parts = []
    if absent:
        slides = ", ".join(str(f.get("slide")) for f in absent)
        parts.append(
            f"{len(absent)} part(s) could not be located (slide {slides}) and "
            f"were skipped; the rest applied."
        )
    if ambiguous:
        slides = ", ".join(str(f.get("slide")) for f in ambiguous)
        parts.append(
            f"{len(ambiguous)} part(s) were skipped because the text appears "
            f"more than once on that slide (slide {slides}), so it was left "
            f"unchanged rather than changed in the wrong place. "
            f"{AMBIGUOUS_SOURCE_REMEDY}"
        )
    return " ".join(parts)


@app.route("/edit-ai", methods=["POST"])
def edit_ai():
    """Apply a free-text edit: interpret the instruction, then swap deterministically.

    The reviewer's plain-language request goes to ``html_edit_interpreter``, which
    reads the current deck and returns exact ``{slide, source, replacement}``
    swaps; those are applied by ``html_edit_layer.apply_edits_and_save`` (no
    re-render, no drift, full audit log). Nothing is written when the instruction
    resolves to no applicable swap; the reviewer sees why.
    """
    path = (request.form.get("path") or "").strip()
    if not path or not _safe_within(DECKS_ROOT, path) or not os.path.isfile(path):
        abort(404)
    # Interpret and apply the SAME document. The edit layer applies to the deck's
    # current revision either way; reading the model's copy from an older path
    # would have it name text that is no longer there.
    path = current_revision_path(path)
    run_ctx = _run_ctx_from_form()
    instruction = (request.form.get("instruction") or "").strip()
    author = request.form.get("author", "")

    if not _has_api_key():
        return _render_edit_result(
            path, notice="No ANTHROPIC_API_KEY set — the free-text edit needs the "
            "API. Use the supply-missing fields, or set a key.", notice_bad=True,
            run_ctx=run_ctx)
    if not instruction:
        return _render_edit_result(
            path, notice="Type what you want changed first.", notice_bad=True,
            run_ctx=run_ctx)

    # Standing reviewer preferences apply to edits too, not just fresh renders:
    # word every replacement to comply with the reviewer's captured formatting
    # rules for this deck type (global + deck-type scope), same store as generate.
    pref_notes = [
        p["note"] for p in applicable_preferences(
            run_ctx.get("deck_type", ""), path=STORE_PATH
        )
    ]

    try:
        with open(path, encoding="utf-8") as f:
            html = f.read()
        plan = interpret_edit(html, instruction, preferences=pref_notes)
    except Exception as exc:  # API/network/parse failure: nothing written
        return _render_edit_result(
            path, notice=f"Could not interpret that edit: {exc}", notice_bad=True,
            run_ctx=run_ctx)

    edits = plan.get("edits", [])
    unresolved = plan.get("unresolved", "")
    if not edits:
        msg = ("No change made." + _model_words(unresolved) if unresolved
               else "No change made. Claude did not find text to change for "
                    "that request.")
        return _render_edit_result(path, notice=msg, notice_bad=True, run_ctx=run_ctx)

    try:
        result = apply_edits_and_save(
            path, edits, kind="content", author=author, instruction=instruction
        )
    except EditNotApplicable as exc:
        return _render_edit_result(
            path, notice=f"Edit not applied. {exc}", notice_bad=True, run_ctx=run_ctx)

    applied = result["applied"]
    changed = "; ".join(_applied_edit_sentence(e) for e in applied)
    notice = (f"Applied {len(applied)} edit"
              f"{'s' if len(applied) != 1 else ''}; saved as "
              f"{result['revision_name']}. {changed}")
    if result["failed"]:
        notice += " " + _skipped_parts_notice(result["failed"])
    if unresolved:
        notice += _model_words(unresolved)
    return _render_edit_result(result["revision_path"], notice=notice, run_ctx=run_ctx)


@app.route("/edit", methods=["POST"])
def edit():
    """Apply one deterministic edit (exact swap) to a deck — the supply-missing
    path and the promote-to-preference guardrail.

    An exact-string swap on one slide (``html_edit_layer``): no re-render, no
    re-flow, and the model-rendered original is preserved. On an edit that cannot
    be applied deterministically (source not found or ambiguous) nothing is
    written and the same deck is shown again with the reason.

    Promote-to-preference guardrail: a format edit may also be saved as a standing
    preference (the learning half, ``preference_store``); a content edit never can.
    Enforced here regardless of the client-side toggle.
    """
    path = (request.form.get("path") or "").strip()
    if not path or not _safe_within(DECKS_ROOT, path) or not os.path.isfile(path):
        abort(404)
    # Same rule as the free-text route: the deck's current revision is what gets
    # edited, so a supply-missing fill posted from an older page lands on the deck
    # as it now stands instead of forking it.
    path = current_revision_path(path)
    run_ctx = _run_ctx_from_form()

    try:
        slide = int(request.form.get("slide", ""))
    except ValueError:
        slide = 0
    source = (request.form.get("source") or "").strip()
    replacement = (request.form.get("replacement") or "").strip()
    kind = request.form.get("kind", "content")
    if kind not in ("format", "content"):
        kind = "content"
    author = request.form.get("author", "")

    try:
        result = apply_edit_and_save(
            path, slide, source, replacement, kind=kind, author=author
        )
    except EditNotApplicable as exc:
        return _render_edit_result(
            path, notice=f"Edit not applied. {exc}", notice_bad=True, run_ctx=run_ctx
        )

    notice = (f"Edit applied to slide {slide}; saved as "
              f"{result['revision_name']}. The original is preserved.")

    if request.form.get("remember") == "1":
        note = (request.form.get("pref_note") or "").strip()
        scope = request.form.get("pref_scope", "global")
        if kind == "format" and note:
            try:
                add_preference(note, scope=scope, author=author, path=STORE_PATH)
                notice += (f" Saved as a standing {scope} preference; it will apply "
                           f"to every future deck.")
            except ValueError:
                notice += " (Preference not saved: the note or scope was invalid.)"
        elif kind == "content":
            notice += (" This is a content edit, so it stays on this deck only and was "
                       "not learned as a preference.")
        elif not note:
            notice += " (Preference not saved: no note was given.)"

    return _render_edit_result(result["revision_path"], notice=notice, run_ctx=run_ctx)


@app.route("/supply-missing", methods=["POST"])
def supply_missing():
    """Write one reviewer-supplied value into every place on the deck it belongs.

    The supply-missing cards' route. It takes ONE value and a list of
    ``(slide, source, occurrence)`` targets — one per `[MISSING: ...]` marker the
    row covers — and applies them as a single revision. Nearly every row posts
    exactly one target; a field the template declares ``deck_wide`` posts one per
    occurrence, because a date repeated in six slide footers is one value and
    offering it as six inputs asks the reviewer to type it six times and lets them
    disagree.

    ``occurrence`` is which of that marker text's appearances on its slide the
    target is, and it is what makes a repeated marker fillable at all. Four
    ``[MISSING: week]`` markers on one next-steps slide are four different weeks
    wearing one string; before this route carried the position, every one of them
    was refused as ambiguous and the card sent the reviewer to the exact-text edit
    to name a wider span by hand. That was 17 of the 30 markers on the live
    proposal deck of 2026-08-20, which is most of what this card exists to do.

    All or nothing (``atomic=True``). Five footers updated and a sixth still
    reading `[MISSING: ...]` would put the deck in the state the deck-wide
    declaration says cannot happen, and reporting that as success is the failure
    this guards against; so a refusal writes nothing, names the slide that refused,
    and leaves the deck exactly as it was. One reviewer action stays one revision,
    which is what keeps undo honest.

    A supplied value is never written back to the packet and never learned as a
    preference — there is no promote-to-preference path here at all, which is why
    this is its own route rather than a shape bolted onto ``/edit``. It is a
    visible, logged reviewer edit on the artifact and nothing more, which is what
    keeps the never-fabricate rule intact.
    """
    path = (request.form.get("path") or "").strip()
    if not path or not _safe_within(DECKS_ROOT, path) or not os.path.isfile(path):
        abort(404)
    # Same rule as every other edit route: the deck's current revision is what
    # gets edited, so a fill posted from an older page lands on the deck as it now
    # stands instead of forking it.
    path = current_revision_path(path)
    run_ctx = _run_ctx_from_form()

    field = (request.form.get("field") or "").strip()
    replacement = (request.form.get("replacement") or "").strip()
    author = request.form.get("author", "")
    slides = request.form.getlist("slide")
    sources = request.form.getlist("source")
    # Which of several identical markers each target is, posted alongside its
    # (slide, source) so a repeated marker can be aimed at. An older page that
    # posts no occurrences still works: the list comes back short and every target
    # falls through to the unique-match rule, which is what those pages meant.
    occurrences = request.form.getlist("occurrence")
    # How many of that marker text the page counted when it was rendered. A page
    # left open while the deck moved on describes a document that no longer
    # exists, and a position read off it would land on whatever now sits at that
    # index. Posting the count lets the edit layer refuse instead.
    totals = request.form.getlist("occurrences")
    if not sources or len(slides) != len(sources):
        return _render_edit_result(
            path, notice="Nothing supplied: the form named no place to write this "
                         "value. Reload the deck and try again.",
            notice_bad=True, run_ctx=run_ctx)

    edits = [{"slide": slide, "source": source, "replacement": replacement,
              "occurrence": occurrences[i] if i < len(occurrences) else None,
              "occurrences": totals[i] if i < len(totals) else None}
             for i, (slide, source) in enumerate(zip(slides, sources))]
    label = f"`{field}`" if field else "the value"
    try:
        result = apply_edits_and_save(
            path, edits, kind="content", author=author, atomic=True)
    except EditNotApplicable as exc:
        return _render_edit_result(
            path, notice=f"{label} was not written. {exc}",
            notice_bad=True, run_ctx=run_ctx)

    places = len(result["applied"])
    where = (f"{places} places on the deck" if places > 1
             else f"slide {result['applied'][0]['slide']}")
    notice = (f"Supplied {label} to {where}; saved as {result['revision_name']}. "
              f"The original is preserved.")
    return _render_edit_result(result["revision_path"], notice=notice,
                               run_ctx=run_ctx)


@app.route("/clear-flags", methods=["POST"])
def clear_flags():
    """Take `[MISSING: ...]` markers off the deck, for facts the document lacks.

    Part A3 of `build-plan-phase4-flags-and-fit.md`. Antonio, 2026-09-23: "maybe
    there is no owner, or there is no designated week", and "you press ... and
    then it just goes away." The supply-missing card's other answer: where
    "Write onto slide" supplies a value, this says there is none to supply.

    NO MODEL CALL AND NO NEW VALUE. The marker and its flag badge come off, and
    the separator it leaves in its tag is tidied ("[MISSING: week] · CCO" reads
    "CCO"), as one revision per press through `apply_edits_and_save`, so Undo
    takes a whole press back byte for byte. Never written to the packet and never
    remembered for the next render of the same document (Antonio, 2026-09-23):
    a later render shows the flag again, which is what stops a clear hiding a
    value the document goes on to state.

    NEVER A COMMERCIAL TERMS MARKER. The template declares those SENSITIVE and
    they are meant to read as awaiting input; this refuses them on the server
    even if a page posts one, rather than trusting the page to leave them out.

    The targets are checked against the deck AS IT NOW STANDS: a posted
    `(slide, source, occurrence)` that the current revision does not carry, or
    carries at a different total, is refused rather than cleared at a position
    that has since moved.
    """
    path = (request.form.get("path") or "").strip()
    if not path or not _safe_within(DECKS_ROOT, path) or not os.path.isfile(path):
        abort(404)
    path = current_revision_path(path)
    run_ctx = _run_ctx_from_form()
    posted = list(zip(request.form.getlist("slide"),
                      request.form.getlist("source"),
                      request.form.getlist("occurrence"),
                      request.form.getlist("occurrences")))
    if not posted:
        return _render_edit_result(
            path, notice="Nothing cleared: the form named no marker.",
            notice_bad=True, run_ctx=run_ctx)
    with open(path, encoding="utf-8") as fh:
        html = fh.read()
    expectations = _fill_expectations_for(run_ctx.get("deck_type", ""))
    listed = {(str(m["slide"]), m["source"], str(m["occurrence"]),
               str(m["occurrences"])): m for m in list_missing_markers(html)}
    markers, stale, refused = [], 0, 0
    for target in posted:
        marker = listed.get(tuple(str(value) for value in target))
        if marker is None:
            stale += 1
        elif classify_marker(marker["field"], expectations) == FILL_SENSITIVE:
            refused += 1
        else:
            markers.append(marker)
    if stale or not markers:
        why = ("the deck has changed since this page was drawn; reload it and "
               "try again" if stale else
               "commercial terms are entered by hand, never cleared")
        return _render_edit_result(path, notice=f"Nothing cleared: {why}.",
                                   notice_bad=True, run_ctx=run_ctx)
    try:
        result = apply_edits_and_save(
            path, clear_marker_edits(html, markers), kind="content",
            author=request.form.get("author", ""),
            instruction="clear missing-field flags", atomic=True)
    except EditNotApplicable as exc:
        return _render_edit_result(path, notice=f"Nothing cleared. {exc}",
                                   notice_bad=True, run_ctx=run_ctx)
    count = len(markers)
    notice = (f"Cleared {count} missing-field flag{'' if count == 1 else 's'}; "
              f"saved as {result['revision_name']}. Undo takes it back.")
    if refused:
        notice += (f" {refused} commercial-terms marker"
                   f"{' was' if refused == 1 else 's were'} left for you to fill.")
    return _render_edit_result(result["revision_path"], notice=notice,
                               run_ctx=run_ctx)


@app.route("/commercial-terms", methods=["POST"])
def commercial_terms():
    """Write the reviewer's commercial terms into all five regions of slide 5.

    The terms form's route. One submission, one revision, five regions, so Undo
    reverses the whole slide in one step rather than leaving a reviewer to walk
    back five edits.

    WHY THIS REPLACED TWO OTHER SURFACES. The terms used to be collected as
    label/value rows on the Generate tab, before the deck existed, while every
    other figure on the same slide arrived afterwards as `[MISSING: ...]` rows in
    the supply-missing card. Two places, two shapes, one slide. Antonio,
    2026-08-20: "the commercial terms editing part is a bit confusing ... I think I
    want to structure the commercial terms part of the editing portion of the UI to
    just have three cases: a conservative case, a base case, an optimistic case ...
    Honestly, I think open text is the best."

    ALWAYS THREE CASES, whatever the render emitted. The proposal deck of
    2026-08-20 carried two, because the paper's table had two, and the base case is
    the one an operating partner reads first. Since the gain figure is a write-in
    too, no value in a scenario row comes from a source, so a third box is a box to
    type in rather than an error to raise.

    WHAT IS REFUSED. A case with some of its figures and not others, and a figure
    with no number the chart can size a bar from. Both are facts about the
    submission's own coherence rather than about what a source supplied, and both
    are reported with every problem named at once so the form takes one pass.
    """
    path = (request.form.get("path") or "").strip()
    if not path or not _safe_within(DECKS_ROOT, path) or not os.path.isfile(path):
        abort(404)
    path = current_revision_path(path)
    run_ctx = _run_ctx_from_form()
    author = request.form.get("author", "")

    scenarios = request.form.getlist("scenario")
    posted_cases = [
        {"scenario": scenario,
         "ebitda_gain": _nth(request.form.getlist("ebitda_gain"), index),
         "qofai_comp": _nth(request.form.getlist("qofai_comp"), index),
         "client_retained_ebitda": _nth(
             request.form.getlist("client_retained_ebitda"), index),
         "enterprise_value": _nth(request.form.getlist("enterprise_value"), index)}
        for index, scenario in enumerate(scenarios)
    ]
    labels = request.form.getlist("terms_row_label")
    values = request.form.getlist("terms_row_value")
    posted_rows = [{"label": label, "value": _nth(values, index)}
                   for index, label in enumerate(labels)]

    try:
        terms = {
            "cases": clean_cases(posted_cases),
            "rows": clean_rows(posted_rows),
            "client_retention": request.form.get("client_retention", ""),
            "downside_protection": request.form.get("downside_protection", ""),
            "terms_footnote": request.form.get("terms_footnote", ""),
        }
    except TermsRejected as exc:
        return _render_edit_result(
            path, notice=f"The commercial terms were not written. {exc}",
            notice_bad=True, run_ctx=run_ctx)

    try:
        result = set_commercial_terms_and_save(path, terms, author=author)
    except EditNotApplicable as exc:
        return _render_edit_result(
            path, notice=f"The commercial terms were not written. {exc}",
            notice_bad=True, run_ctx=run_ctx)

    cases = len(terms["cases"])
    rows = len(terms["rows"])
    notice = (
        f"Wrote the commercial terms: {cases} "
        f"case{'s' if cases != 1 else ''} on the chart and {rows} "
        f"row{'s' if rows != 1 else ''} across the top"
        f"{', which puts the strip back to awaiting input' if not rows else ''}. "
        f"Saved as {result['revision_name']}; the original is preserved and Undo "
        f"reverses the whole slide in one step.")
    return _render_edit_result(result["revision_path"], notice=notice,
                               run_ctx=run_ctx)


@app.route("/commercial-defaults", methods=["POST"])
def commercial_defaults():
    """Write QofAI's standing commercial language into both regions of a deck.

    The one-click default (Antonio, 2026-08-20: "I want a default payment, 'How
    Payment Works,' to be an option on the deck ... after the deck is generated,
    someone can press 'Default, How Payment Works,' and then it just appears").

    Two regions, one revision: the payment mechanism under HOW PAYMENT WORKS, and
    the risk-reversal clause in the blue box above the value map. They are one
    statement — the blue box promises the client that QofAI earns nothing without
    improvement, and the block below it is what says how improvement is measured —
    so `set_commercial_defaults` writes both or refuses, and one press of the
    button is one press of undo.

    MECHANISM ONLY. No share, cap, term, or dollar figure is written from here.
    Those are terms of one deal, the deck asks a reviewer for them by name, and a
    default carrying the last deal's numbers into this deck would arrive with the
    settled look of a considered figure instead of the visible marker that is the
    only thing standing between a reviewer and exactly that mistake. The wording
    lives in `templates/commercial-defaults.json`; see `src/commercial_defaults.py`
    for where the line is drawn and why.

    Like every other edit route: the deck's current revision in, the next revision
    out, logged and undoable, and the packet is never touched.
    """
    path = (request.form.get("path") or "").strip()
    if not path or not _safe_within(DECKS_ROOT, path) or not os.path.isfile(path):
        abort(404)
    path = current_revision_path(path)
    run_ctx = _run_ctx_from_form()
    author = request.form.get("author", "")

    try:
        defaults = load_defaults()
    except DefaultsUnavailable as exc:
        # Nothing partial is offered: half this text is worse than none of it, so a
        # file that cannot be read is reported rather than worked around.
        return _render_edit_result(
            path, notice=f"The standing commercial language was not written. {exc}",
            notice_bad=True, run_ctx=run_ctx)

    try:
        result = set_commercial_defaults_and_save(
            path, downside=defaults["downside_protection"],
            payment_steps=defaults["payment_mechanics"], author=author)
    except EditNotApplicable as exc:
        return _render_edit_result(
            path, notice=f"The standing commercial language was not written. {exc}",
            notice_bad=True, run_ctx=run_ctx)

    slides = sorted({record["slide"] for record in result["applied"]})
    where = ("slide " + ", ".join(str(n) for n in slides)) if slides else "the deck"
    notice = (f"Wrote the default \"How Payment Works\" and the no-improvement "
              f"clause to {where}; saved as {result['revision_name']}. The original "
              f"is preserved, and Undo reverses both together.")
    return _render_edit_result(result["revision_path"], notice=notice,
                               run_ctx=run_ctx)


@app.route("/toggle-progress", methods=["POST"])
def toggle_progress():
    """Correct one progress item's checkbox state as a display-only edit.

    A checkbox's state lives in a class attribute, so this is a markup change
    and does not go through ``/edit`` (``html_edit_layer.apply_text_edit``
    changes display text and never markup); it goes through
    ``html_edit_layer.toggle_progress_item_and_save`` instead. Same chain as
    every other edit underneath — current revision in, next revision out,
    logged, undoable — so it shows up in edit history for free. The packet is
    never touched, so the deck may now disagree with the packet that produced
    it; the logged revision is what makes that traceable rather than invisible.
    """
    path = (request.form.get("path") or "").strip()
    if not path or not _safe_within(DECKS_ROOT, path) or not os.path.isfile(path):
        abort(404)
    path = current_revision_path(path)
    run_ctx = _run_ctx_from_form()

    try:
        slide = int(request.form.get("slide", ""))
    except ValueError:
        slide = 0
    label = (request.form.get("label") or "").strip()
    new_state = request.form.get("new_state", "")
    author = request.form.get("author", "")

    try:
        result = toggle_progress_item_and_save(
            path, slide, label, new_state, author=author
        )
    except EditNotApplicable as exc:
        return _render_edit_result(
            path, notice=f"Edit not applied. {exc}", notice_bad=True, run_ctx=run_ctx
        )

    notice = (f"Progress item updated on slide {slide}; saved as "
              f"{result['revision_name']}. The original is preserved.")
    return _render_edit_result(result["revision_path"], notice=notice, run_ctx=run_ctx)


@app.route("/undo", methods=["POST"])
def undo():
    """Undo the most recent edit on a deck and show the reverted deck.

    Removes the latest revision (and its edit-log entries) via
    ``html_edit_layer.undo_last_edit`` and re-renders the Result tab against the
    now-current deck — the prior revision, or the untouched original. Nothing to
    undo (an unedited deck) is reported, not an error.
    """
    path = (request.form.get("path") or "").strip()
    if not path or not _safe_within(DECKS_ROOT, path) or not os.path.isfile(path):
        abort(404)
    run_ctx = _run_ctx_from_form()
    outcome = undo_last_edit(path)
    if not outcome["undone"]:
        return _render_edit_result(
            path, notice=outcome["reason"], notice_bad=True, run_ctx=run_ctx)
    notice = (f"Undid the last edit (removed {outcome['removed_revision']}). "
              f"Now showing {os.path.basename(outcome['current_path'])}.")
    return _render_edit_result(outcome["current_path"], notice=notice, run_ctx=run_ctx)


@app.route("/export-design-prompt", methods=["POST"])
def export_design_prompt():
    """Item 3(e). Turn the deck as it currently stands, edits included, into a
    self-contained prompt a reviewer pastes into Claude Design to pick up
    editing there and bring it back.

    Reads the current revision rather than trusting the posted path, for the
    same reason `/edit` and `/toggle-progress` do (C1): an export computed from
    a stale path would hand off a deck missing whatever edits happened since. A
    `db-deck:` sentinel has no revision file to resolve, so
    `current_revision_path` returns it unchanged, and `_resolve_deck_html`
    reads the row's own stored HTML instead — a saved deck's stored HTML is
    already its current state (B3a), which is what makes offering this button
    on a deck opened from history correct rather than a guess.
    """
    path = (request.form.get("path") or "").strip()
    if not _deck_path_exists(path):
        abort(404)
    path = current_revision_path(path)
    run_ctx = _run_ctx_from_form()
    html, _ = _resolve_deck_html(path)
    prompt = assemble_design_export_prompt(html)
    notice = "Claude Design export prompt ready below. Copy it and paste into Claude Design."
    return _render_edit_result(path, notice=notice, run_ctx=run_ctx,
                               design_export_prompt=prompt)


def _saved_heading(created_at):
    """"Saved Sep 23, 2026 · 07:57 UTC" for a deck opened out of the store.

    The row's timestamp is a `datetime` from Postgres and an ISO-8601 string
    from SQLite, and printing either raw put microseconds and a `+00:00` offset
    in the Result tab's heading. Both are UTC (see `deck_store`), so the zone is
    named rather than converted. Anything unparseable falls back to the plain
    value, which is ugly but never wrong.
    """
    value = created_at
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return f"Saved {created_at}" if created_at else "Saved deck"
    if not isinstance(value, datetime):
        return "Saved deck"
    return f"Saved {value.strftime('%b')} {value.day}, {value.year} · {value.strftime('%H:%M')} UTC"


def _render_db_deck_result(deck_id):
    """The full Result view for one row out of the database, by id.

    Unlike `_render_edit_result` (keyed to a live file and whatever happens to
    be in `_LAST_RESULT`), this reads its fidelity report, applied preferences,
    saved prompt, and originating packet straight out of the row's own stored
    ``details`` — the only way a deck from another session, possibly after a
    redeploy, can come back with more than its raw HTML. When the file it was
    rendered to is still on disk, its real path is used (so the deck and any
    editing affordances that need a file still work); otherwise a `db-deck:`
    sentinel stands in, and the HTML is served straight from the row.
    """
    try:
        row = get_deck(int(deck_id))
    except Exception:
        row = None
    if not row:
        abort(404)
    details = row.get("details") or {}
    local_path = details.get("deck_path", "")
    if not (local_path and _safe_within(DECKS_ROOT, local_path)
            and os.path.isfile(local_path)):
        local_path = f"{_DB_DECK_PREFIX}{row['id']}"
    packet = details.get("packet", "")
    deck_type = row.get("deck_type", "")
    gaps, resolved = _gaps_for(packet, deck_type) if packet else ([], [])
    # The packet is read live when it is still readable, so a flag resolved since
    # the save shows as resolved. When it is not (the row outlived the machine it
    # was rendered on), the flags and resolutions saved with the deck stand in.
    if not (gaps or resolved):
        gaps = details.get("gaps") or []
        resolved = details.get("resolved") or []
    ctx = _build_result_ctx(
        deck_type=deck_type, company=row.get("company", ""), deck_path=local_path,
        gaps=gaps, resolved=resolved, show_gaps=bool(packet or gaps or resolved),
        raw_company=details.get("raw_company", row.get("company", "")),
        raw_project=details.get("raw_project", row.get("project", "")),
        packet=packet, check_in_date=details.get("check_in_date", ""),
        deck_heading=_saved_heading(row.get("created_at")),
        **_db_run_outcome(details, local_path),
    )
    return render_studio("result", result_ctx=ctx)


@app.route("/deck-view")
def deck_view():
    """Review page for one saved deck copy (an original or a revision), or,
    with ``id`` instead of ``path``, one row out of the database.

    This is where the Decks tab's "open" lands, so it has to give back a usable
    review surface and not just a preview: when the deck belongs to the run still
    in the cache, the originating packet is recovered too, which is what restores
    the flagged-claims checklist and lets an edit forward its context. For a deck
    from an earlier session there is no packet to recover, so the deck, the edit
    box and the history are shown without a checklist rather than with an empty one.
    """
    deck_id = (request.args.get("id") or "").strip()
    if deck_id:
        return _render_db_deck_result(deck_id)
    path = (request.args.get("path") or "").strip()
    if not path or not _safe_within(DECKS_ROOT, path) or not os.path.isfile(path):
        abort(404)
    run_ctx = None
    measured = _LAST_RESULT.get("render_deck_path") or _LAST_RESULT.get("deck_path") or ""
    if measured and base_stem(measured) == base_stem(path):
        run_ctx = {
            "deck_type": _LAST_RESULT.get("deck_type", ""),
            "company": _LAST_RESULT.get("raw_company", ""),
            "project": _LAST_RESULT.get("raw_project", ""),
            "packet": _LAST_RESULT.get("packet", ""),
            "check_in_date": _LAST_RESULT.get("check_in_date", ""),
        }
    return _render_edit_result(path, run_ctx=run_ctx)


def _clear_last_result_if_db_deck(deck_id):
    """Drop the cached Result-tab run if it was this database row's own
    sentinel — the same reasoning `delete_deck_route`'s filesystem branch
    applies, so a deleted deck is never left on screen offering to edit or
    undo a row that is now gone."""
    if _LAST_RESULT.get("deck_path") == f"{_DB_DECK_PREFIX}{deck_id}":
        _LAST_RESULT.clear()


@app.route("/deck-delete", methods=["POST"])
def delete_deck_route():
    """Delete one deck listed on the Decks tab, and say what went.

    A database row (``id``) and a filesystem deck (``path``) delete
    differently — one row removed from ``decks``, versus a file plus the
    revisions and edit log beside it — so the form sends whichever identity
    matches the mode the tab is listing in. POST + redirect either way, so a
    refresh of the Decks tab cannot re-fire a delete; the outcome rides back on
    the redirect's query string (see :func:`_decks_notice`).
    """
    deck_id = (request.form.get("id") or "").strip()
    if deck_id:
        try:
            removed = delete_decks_from_store([int(deck_id)]) if deck_id.isdigit() else 0
        except Exception:
            removed = 0
        if not removed:
            return redirect(url_for("home", tab="decks", failed=f"deck #{deck_id}"))
        _clear_last_result_if_db_deck(int(deck_id))
        return redirect(url_for("home", tab="decks", deleted=f"deck #{deck_id}"))

    # The same ``_safe_within`` guard the serving routes use applies first, so
    # this can only ever remove a file under ``DECKS_ROOT``.
    #
    # When the deck that was on the Result tab is one of the files removed, the
    # cached run is dropped: the tab's edit ladder, guard reports and Design
    # prompt all describe a deck that no longer exists, and offering to edit or
    # undo a deleted file is worse than showing nothing. A delete that touches
    # some OTHER deck leaves the Result tab alone.
    path = (request.form.get("path") or "").strip()
    if not path or not _safe_within(DECKS_ROOT, path) or not os.path.isfile(path):
        abort(404)
    name = os.path.basename(path)
    removed = delete_deck(path)["removed"]
    if not removed:
        return redirect(url_for("home", tab="decks", failed=name))

    directory = os.path.dirname(os.path.abspath(path))
    gone = {os.path.join(directory, n) for n in removed}
    on_screen = _LAST_RESULT.get("deck_path") or ""
    if on_screen and os.path.abspath(on_screen) in gone:
        _LAST_RESULT.clear()
    return redirect(url_for("home", tab="decks", deleted=name,
                            also=len(removed) - 1))


@app.route("/deck-delete-bulk", methods=["POST"])
def delete_deck_bulk_route():
    """Delete every deck the reviewer checked on the Decks tab.

    39 rows one at a time (commit ``c41edc3``, the per-row delete) is why the
    tab was unusable at that count. ``selected`` carries database ids in DB
    mode and filesystem paths in filesystem mode — never a mix, since the tab
    is only ever listing one mode at a time — so this checks which one it got
    rather than being told.
    """
    selected = [s.strip() for s in request.form.getlist("selected") if s.strip()]
    if not selected:
        return redirect(url_for("home", tab="decks"))

    if all(s.isdigit() for s in selected):
        ids = [int(s) for s in selected]
        try:
            removed = delete_decks_from_store(ids)
        except Exception:
            removed = 0
        for deck_id in ids:
            _clear_last_result_if_db_deck(deck_id)
        if not removed:
            return redirect(url_for("home", tab="decks", failed="the selected deck(s)"))
        return redirect(url_for("home", tab="decks", deleted=f"{removed} deck(s)"))

    removed_count = 0
    on_screen = _LAST_RESULT.get("deck_path") or ""
    on_screen_gone = False
    for path in selected:
        if not _safe_within(DECKS_ROOT, path) or not os.path.isfile(path):
            continue
        removed = delete_deck(path)["removed"]
        removed_count += len(removed)
        directory = os.path.dirname(os.path.abspath(path))
        if on_screen and os.path.abspath(on_screen) in {
                os.path.join(directory, n) for n in removed}:
            on_screen_gone = True
    if on_screen_gone:
        _LAST_RESULT.clear()
    if not removed_count:
        return redirect(url_for("home", tab="decks", failed="the selected deck(s)"))
    return redirect(url_for("home", tab="decks", deleted=f"{removed_count} file(s)"))


@app.route("/preview")
def preview():
    return _serve_deck(request.args.get("path", ""), as_download=False)


@app.route("/attachment-text")
def attachment_text():
    """The text one attachment gave, as the model read it.

    What the Decks tab's Source column links to (Antonio, 2026-09-20). The
    original file is never stored, so there is nothing to hand back but the
    extraction, and the extraction is the honest answer to "what produced this
    deck" anyway: the model read the text, not the bytes.

    Served as plain text on purpose. It is a record to look at, not a document
    to re-upload, and rendering it as HTML would invite a stored document's own
    markup into the studio's page.
    """
    try:
        row = get_deck(int(request.args.get("id") or 0))
    except Exception:
        row = None
    if not row:
        abort(404)
    records = (row.get("details") or {}).get("attachments") or []
    try:
        index = int(request.args.get("index") or 0)
    except ValueError:
        index = 0
    if index < 0 or index >= len(records):
        abort(404)
    record = records[index] or {}
    name = record.get("filename") or "attachment"
    body = (
        "%s\n"
        "%s\n"
        "kind: %s · %s bytes · sha256 %s\n"
        "\nThis is the TEXT the deck was written from, extracted from the file "
        "named above. The file itself is not kept.\n"
        "%s\n\n"
    ) % (name, "=" * len(name), record.get("kind") or "?",
         record.get("size_bytes") or "?", record.get("sha256") or "?", "-" * 60)
    return Response(body + (record.get("text") or ""),
                    mimetype="text/plain; charset=utf-8")


@app.route("/download")
def download():
    return _serve_deck(request.args.get("path", ""), as_download=True)


def _serve_deck(path, *, as_download):
    """Serve a rendered deck: a file under the decks root, or, for a `db-deck:`
    sentinel, the HTML stored against that id — the deck history view's own
    deck when the file it was rendered to is no longer on disk."""
    db_id = _db_deck_id(path)
    if db_id is not None:
        try:
            row = get_deck(db_id)
        except Exception:
            row = None
        if not row:
            abort(404)
        resp = Response(row["html"], mimetype="text/html")
        if as_download:
            resp.headers["Content-Disposition"] = f'attachment; filename="deck-{db_id}.html"'
        return resp
    if not path or not _safe_within(DECKS_ROOT, path) or not os.path.isfile(path):
        abort(404)
    return send_file(path, mimetype="text/html", as_attachment=as_download,
                     download_name=os.path.basename(path))


def _resolve_deck_html(path):
    """The raw HTML string behind ``path`` (a `db-deck:` sentinel or a file
    under DECKS_ROOT) plus a filename stem — what the PDF export needs that
    `_serve_deck` doesn't expose, since that route serves a Response, not the
    string itself."""
    db_id = _db_deck_id(path)
    if db_id is not None:
        try:
            row = get_deck(db_id)
        except Exception:
            row = None
        if not row:
            abort(404)
        return row["html"], f"deck-{db_id}"
    if not path or not _safe_within(DECKS_ROOT, path) or not os.path.isfile(path):
        abort(404)
    with open(path, "r", encoding="utf-8") as f:
        return f.read(), os.path.splitext(os.path.basename(path))[0]


def _render_pdf(html):
    """Print ``html`` to PDF bytes in headless Chromium at the deck's own
    fixed 1280x720 slide size, so each printed page matches its slide exactly
    instead of reflowing into a page format the deck was never laid out for.
    Playwright is imported here, not at module load, so nothing else in the UI
    needs it installed unless a reviewer actually presses the PDF button."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            page.set_content(html, wait_until="load")
            return page.pdf(width="1280px", height="720px", print_background=True,
                             margin={"top": "0px", "right": "0px", "bottom": "0px", "left": "0px"})
        finally:
            browser.close()


@app.route("/download-pdf")
def download_pdf():
    html, base_name = _resolve_deck_html(request.args.get("path", ""))
    resp = Response(_render_pdf(html), mimetype="application/pdf")
    resp.headers["Content-Disposition"] = f'attachment; filename="{base_name}.pdf"'
    return resp


@app.route("/preferences/add", methods=["POST"])
def add_preference_route():
    note = request.form.get("note", "")
    scope = request.form.get("scope", "global")
    author = request.form.get("author", "")
    if note.strip():
        try:
            add_preference(note, scope=scope, author=author, path=STORE_PATH)
        except ValueError:
            pass  # bad scope/empty note: fall through, nothing saved
    return redirect(url_for("home", tab="preferences"))


@app.route("/preferences/toggle", methods=["POST"])
def toggle_preference():
    try:
        pref_id = int(request.form.get("id", ""))
    except ValueError:
        return redirect(url_for("home", tab="preferences"))
    active = request.form.get("active") == "1"
    set_active(pref_id, active, path=STORE_PATH)
    return redirect(url_for("home", tab="preferences"))


@app.route("/preferences/delete", methods=["POST"])
def delete_preference_route():
    """Delete a standing preference outright (not just deactivate it)."""
    try:
        pref_id = int(request.form.get("id", ""))
    except ValueError:
        return redirect(url_for("home", tab="preferences"))
    delete_preference(pref_id, path=STORE_PATH)
    return redirect(url_for("home", tab="preferences"))


if __name__ == "__main__":
    # Prompts/decks land in the repo's real output roots (deck_generator's
    # defaults), so the download link and the numbered files match a normal run.
    # Debug off by default; override the port with PORT.
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")))
