"""The calendar, served, so that pressing Approve actually approves.

Why this exists
---------------
Until 2026-09-15 the page was a file. `display/render_calendar.py` wrote one
self-contained HTML document and a reviewer opened it from disk, which meant
every interaction on it ended at the clipboard: the page copied out the
`approval/queue.py` commands and a human ran them in a terminal. That was an
honest design for a file, and it was the wrong design for three founders
looking at three calendars.

Antonio's two complaints on the 2026-09-15 recording were the same complaint
from two directions. "When I press approve on a post, the calendar itself
doesn't change", and "if I press approve and then close the UI and open it,
the approve will not be there". Neither is a rendering bug. A file has nowhere
to write, so the page could not record anything, and a badge that changed
colour without a record behind it would have been the page lying about its own
state, which this build has had to be stopped from doing three times already.

A server is the thing that makes the honest version possible. The click can
write, so the badge can change, because by the time it changes it is reporting
something the store actually holds.

What it does not change
-----------------------
The rules did not move here. They stayed where they were:

- `approval/queue.py` is still the only thing that writes a human decision.
  This module calls `decide()` and reports what it returns. It does not
  construct a store row, does not relax a check, and cannot record a decision
  the CLI would have refused. A decline without a reason fails here exactly as
  it fails there.
- The store is still append-only. Correcting a decision is a newer decision.
- Nothing publishes. `published` is not a state a human may set through the
  queue, so it is not one anybody can set through this.
- The page still stages before it records. Approve marks a card, a decline
  still needs its reason, and one button at the bottom sends the batch. The
  badge is never written by the browser: a successful write reloads the page
  and the badge is rendered from the store, which is where it was always read
  from.

Running it
----------
    cd ui && ../.venv/bin/python app.py

No environment variables, no Railway, nothing installed beyond
`requirements.txt`. That is deliberate: this is the version that has to work
on a founder's laptop.

Hosting it (2026-09-20)
-----------------------
The three things hosting adds are all below, and every one of them is off when
its environment variable is unset, which is how a laptop runs. Nothing in this
section changes a local run.

- **A durable place to write.** Railway rebuilds the container from the repo on
  every deploy, so a decision written under the repo is gone at the next push.
  `calendar_model/state_dir.py` finds the mounted volume and decisions go there.
  Windows keep coming from the repo, because the jobs that write them run in
  Actions and commit. Locally both are `state/`, as before.
- **A gate.** `UI_PASS` turns on HTTP Basic over every route but `/health`. The
  portal signs a reviewer in with Google and forwards these credentials, so a
  reviewer arriving that way never sees a prompt, and the service's own address
  stays closed to anyone who finds it. Unset, there is no gate. Set on the
  platform is not optional: a hosted service with no password answers 503
  rather than serving three founders' calendars to whoever has the URL.
- **Prefix-aware links.** The portal mounts this app under a path it chooses,
  and the app is not told what it is except in a header. `ProxyFix` folds
  `X-Forwarded-Prefix` into the app's idea of its own root so `url_for` builds
  links that point back through the portal. This fails quietly rather than
  loudly, which is why `ui/test_portal_mount.py` pins it: the page renders and
  the one POST it makes 404s, so Approve just stops working.
"""

from __future__ import annotations

import hmac
import importlib.util
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_AGENT_ROOT = _HERE.parent

# The agent's packages are folders of modules rather than installed packages,
# which is how every other entry point in this build reaches them.
#
# `approval` is deliberately not in this list, and putting it back breaks the
# page. It holds `queue.py`, and any folder on `sys.path` containing a file
# named after a standard library module shadows that module for everything
# imported afterwards, including third-party code: urllib3 does `import queue`
# and would get ours, so the first request dies on
# `module 'queue' has no attribute 'Queue'` before the page renders. The
# approval queue is loaded from its path instead, below, under a name nothing
# else can claim. Same collision that answered 500 under gunicorn on
# 2026-09-20, in the opposite direction.
for folder in ("calendar_model", "display", "narrative", "ingestion"):
    sys.path.insert(0, str(_AGENT_ROOT / folder))

from flask import Flask, Response, jsonify, request, url_for  # noqa: E402
from werkzeug.middleware.proxy_fix import ProxyFix  # noqa: E402

import render_calendar  # noqa: E402
import slots  # noqa: E402
import state_dir  # noqa: E402  (calendar_model/state_dir.py)
import state_store  # noqa: E402  (calendar_model/state_store.py)
import performance  # noqa: E402  (ingestion/performance.py)

# `approval/queue.py` is loaded from its path rather than by name, and this is
# not style. Python ships a `queue` module, so `import queue` returns whichever
# one is in `sys.modules` already. Under `python app.py` nothing has imported
# the standard one yet and the path insert above wins. Under gunicorn the
# worker has, so the name resolves to the standard library's thread-safe FIFO,
# and the first decision anybody records answers 500 with
# `module 'queue' has no attribute 'decide'`. Found by running this app under
# gunicorn on 2026-09-20, which is the only way it shows up: every test and
# every local run passes.
def _load_queue():
    """The real `approval/queue.py`, under a name nothing else can claim."""
    if "approval_queue" in sys.modules:
        return sys.modules["approval_queue"]
    spec = importlib.util.spec_from_file_location(
        "approval_queue", _AGENT_ROOT / "approval" / "queue.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["approval_queue"] = module
    spec.loader.exec_module(module)
    return module


queue_mod = _load_queue()

app = Flask(__name__)

# Read the forwarding headers the portal sends, so `url_for` builds links that
# come back through it. One proxy hop, which is the portal. With no headers on
# the request this does nothing at all, so a local run is untouched.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# Where windows are read from. Named as it was before the store moved, because
# that is what it has always been: the directory the repo keeps its state in.
DEFAULT_STATE_ROOT = state_dir.window_root()

# Where decisions are written. The same directory on a laptop, the mounted
# volume on the platform. Resolved once at startup rather than per request:
# the answer cannot change while the process runs, and the seed must happen
# once rather than on every page load.
_STORE = state_dir.resolve_store_dir()
DECISION_STORE_ROOT = _STORE.directory

# The store name for saved reschedules. Same append-only pattern as approvals:
# slot_id -> new_date, latest per key wins.
RESCHEDULE_STORE = "reschedules"

# The gate. Unset means no gate, which is how a laptop runs and how every test
# that is not about the gate runs. Module-level so a test can set them.
_UI_USER = os.environ.get("UI_USER", "qofai")
_UI_PASS = os.environ.get("UI_PASS")

# Set by Railway and by nothing else, so it is what makes an unset password an
# error rather than a local convenience.
_ON_PLATFORM = bool(os.environ.get(state_dir.PLATFORM_VAR))

# The one route outside the gate. Railway's healthcheck carries no credentials
# and would otherwise read a working service as down and fail the deploy.
_UNGATED = ("/health",)


def _authorized(auth) -> bool:
    """Constant-time on both fields, so neither can be guessed a character at
    a time off the response timing."""
    if auth is None or auth.type != "basic":
        return False
    user_ok = hmac.compare_digest((auth.username or ""), _UI_USER)
    pass_ok = hmac.compare_digest((auth.password or ""), _UI_PASS or "")
    return user_ok and pass_ok


@app.before_request
def _gate():
    """Nothing but `/health` is served without credentials, once a password is
    set. Before the route, so a route added later is gated by default rather
    than by its author remembering."""
    if request.path in _UNGATED:
        return None
    if not _UI_PASS:
        if _ON_PLATFORM:
            # Fail closed. Three founders' calendars and the record of who
            # approved what are not served to whoever finds the URL because a
            # variable was missed. A 503 on a fresh service means the password
            # is not set yet, not a broken deploy.
            return (
                jsonify(
                    ok=False,
                    detail="UI_PASS is not set on this service, so it will not serve.",
                ),
                503,
            )
        return None
    if _authorized(request.authorization):
        return None
    return Response(
        "Authentication required.",
        401,
        {"WWW-Authenticate": 'Basic realm="QofAI content calendar"'},
    )


def _today():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).date()


def _read_reschedule_map() -> dict:
    """Latest saved reschedule per slot_id, as {slot_id: new_date}."""
    entries = state_store.read_state(
        RESCHEDULE_STORE, root=DECISION_STORE_ROOT, latest_per_key=True
    )
    return {
        e.entry_key: e.value.get("new_date")
        for e in entries
        if e.entry_key and isinstance(e.value, dict) and e.value.get("new_date")
    }


def _load_windows(taxonomy, today=None):
    """Every live window for every live lens, in the declared lens order.

    Live means not yet fully passed as of `today`, via
    `slots.live_window_paths`. From the day next month is assembled to the
    end of this one, a lens has two, and both are shown: this month's second
    half is still there to decide on while next month is being read. The
    newest file alone would hide the first.

    Read on every request rather than cached at startup. The whole point of
    the server is that a decision taken a moment ago is visible now, and a
    process holding a window in memory would serve the calendar as it was
    when it booted. Three windows and their approval overlays are a few
    milliseconds of file reads.
    """
    today = today or _today()
    windows = []
    sources = []
    # Read once for all lenses; the reschedule store is shared across lenses.
    reschedule_map = _read_reschedule_map()
    for lens in taxonomy.v1_lens_order:
        for path in slots.live_window_paths(DEFAULT_STATE_ROOT, lens=lens, today=today):
            window = slots.read_window(path)
            if window is None:
                continue
            # Approvals overlay first, then reschedules. Both are stored in
            # DECISION_STORE_ROOT, durable on the platform. On a laptop both
            # live in state/, which is the same directory.
            window = render_calendar.apply_stored_approvals(window, DECISION_STORE_ROOT)
            if reschedule_map:
                window = slots.apply_reschedules(window, reschedule_map)
            windows.append(window)
            # `_rel` rather than `relative_to`, which raises for a path outside
            # the agent folder. That is not a hypothetical: the hosted version
            # keeps state on a Railway volume at /data, so every window path
            # will be outside this repo, and a provenance line is not worth a 500.
            sources.append(f"read from {render_calendar._rel(path)}")
    return windows, sources


@app.get("/")
def calendar():
    taxonomy = slots.load_taxonomy()
    today = _today()
    windows, sources = _load_windows(taxonomy, today)
    if not windows:
        return (
            "<h1>No calendar yet</h1><p>No assembled window covering today or later was "
            f"found under <code>{DEFAULT_STATE_ROOT}</code>. Run "
            "<code>synthesis/monthly_assembly.py</code> first.</p>",
            200,
            {"Content-Type": "text/html; charset=utf-8"},
        )

    corpus = render_calendar.resolve_corpus()
    page = render_calendar.render_html(
        windows,
        taxonomy,
        today=today,
        issues={
            w.window_id: slots.validate_window(
                w, taxonomy, slots.known_item_refs(), today=today
            )
            for w in windows
        },
        titles={ref: item.title for ref, item in corpus.items() if item.title},
        corpus=corpus,
        window_source="; ".join(sources),
        tags_path=str(slots.DEFAULT_TAGS_PATH),
        refs=slots.known_item_refs(),
        agent_decided=True,
        # What went out and how it did, read from the ingestion log rather
        # than fetched: the daily run is what talks to the portal. Read from
        # the window root, because the log is written by the scheduled jobs
        # and arrives with the deploy, the same as the windows.
        history=performance.read_history(root=DEFAULT_STATE_ROOT),
        selected=request.args.get("lens", ""),
        links=render_calendar.published_links(DECISION_STORE_ROOT),
        # Built rather than written. Under the portal this app is mounted at a
        # path it is only told about in a header, and a literal "decide" or
        # "/decide" resolves against the portal's root instead of this app's:
        # the page renders, the POST 404s, and Approve silently stops working.
        record_url=url_for("record"),
        reschedule_url=url_for("reschedule"),
    )
    return page, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.post("/decide")
def record():
    """Records a batch of decisions, or records none of them.

    The batch is checked in full before anything is written. A page offering
    six decisions and recording four of them, with no way to say which four,
    would leave a reviewer unable to tell what they had agreed to, and the
    store has no update call to take the four back with. So every decision is
    validated first and the whole batch is refused on the first bad one.

    That check is not reimplemented here. It is `queue.decide` with
    `dry_run`-shaped intent: the first pass runs the same function against a
    throwaway store root, so the rules that apply are literally the rules the
    CLI applies, and the second pass writes for real.
    """
    payload = request.get_json(silent=True) or {}
    by = str(payload.get("by") or "").strip()
    decisions = payload.get("decisions")

    if not by:
        return jsonify(ok=False, detail="no one is named as deciding"), 400
    if not isinstance(decisions, list) or not decisions:
        return jsonify(ok=False, detail="no decisions were sent"), 400

    taxonomy = slots.load_taxonomy()
    windows, _sources = _load_windows(taxonomy)
    if not windows:
        return jsonify(ok=False, detail="there is no calendar to decide on"), 409

    # A slot id says which calendar it belongs to, so the window a decision
    # applies to is found rather than assumed. Deciding against the wrong
    # window is how `queue.decide`'s existence check gets bypassed by
    # accident, and an approval that joins onto nothing is invisible forever.
    by_slot = {}
    for window in windows:
        for slot in window.slots:
            by_slot[slot.slot_id] = window

    prepared = []
    for entry in decisions:
        if not isinstance(entry, dict):
            return jsonify(ok=False, detail="a decision was not an object"), 400
        slot_id = str(entry.get("slot_id") or "").strip()
        state = str(entry.get("state") or "").strip()
        note = str(entry.get("note") or "").strip()
        url = str(entry.get("url") or "").strip()
        window = by_slot.get(slot_id)
        if window is None:
            return jsonify(
                ok=False, detail=f"{slot_id or 'a decision'} is not a slot in any calendar"
            ), 400
        prepared.append((window, slot_id, state, note, url))

    # First pass, against a store nobody reads, purely to find out whether the
    # real one would accept all of them.
    import tempfile

    with tempfile.TemporaryDirectory() as rehearsal:
        for window, slot_id, state, note, url in prepared:
            # The rehearsal store starts empty, so it cannot see that a post
            # is approved, and "only an approved post can be published" would
            # refuse every publish here. So it is seeded with the real
            # decisions first, and still nothing it writes is ever read.
            real = Path(DECISION_STORE_ROOT) / state_dir.SEEDED_STORE
            if real.is_file() and not (Path(rehearsal) / state_dir.SEEDED_STORE).exists():
                import shutil

                shutil.copy(real, Path(rehearsal) / state_dir.SEEDED_STORE)
            result = queue_mod.decide(
                window, slot_id, state, by=by, note=note, url=url, root=rehearsal
            )
            if not result.get("ok"):
                return jsonify(ok=False, detail=result.get("detail", "refused")), 400

    recorded = []
    for window, slot_id, state, note, url in prepared:
        result = queue_mod.decide(
            window, slot_id, state, by=by, note=note, url=url, root=DECISION_STORE_ROOT
        )
        if not result.get("ok"):
            # Reaching here means the store refused something the rehearsal
            # accepted, so the batch is already part-written. Say so plainly
            # and name what did land, because the append-only store is the
            # record and a reviewer needs to know what it now holds.
            return jsonify(
                ok=False,
                detail=(
                    f"{result.get('detail', 'the store refused a decision')}. "
                    f"{len(recorded)} decision(s) before it were written and stand: "
                    f"{', '.join(recorded) or 'none'}"
                ),
                recorded=recorded,
            ), 500
        recorded.append(slot_id)

    return jsonify(ok=True, recorded=recorded, detail=f"{len(recorded)} recorded by {by}")


@app.post("/reschedule")
def reschedule():
    """Records a batch of date moves. Each move is slot_id -> new_date.

    The reschedule store is the same append-only pattern as the approval
    store: latest entry per slot_id wins. On the next page load,
    `_read_reschedule_map` reads the latest per slot and `apply_reschedules`
    swaps the dates before rendering, so the calendar opens with posts on
    their saved dates.

    Moving a post back to its original date is a valid reschedule: it writes
    the original date to the store, and the overlay produces no visible change
    (the date it carries matches the window file). A "reset to agent order"
    would write each slot's original date back, which the browser already
    knows from `data-home`.
    """
    from datetime import date as _date

    payload = request.get_json(silent=True) or {}
    by = str(payload.get("by") or "").strip()
    moves = payload.get("moves")

    if not by:
        return jsonify(ok=False, detail="no one is named as rescheduling"), 400
    if not isinstance(moves, list) or not moves:
        return jsonify(ok=False, detail="no moves were sent"), 400

    entries = []
    for move in moves:
        if not isinstance(move, dict):
            return jsonify(ok=False, detail="a move was not an object"), 400
        slot_id = str(move.get("slot_id") or "").strip()
        new_date = str(move.get("new_date") or "").strip()
        if not slot_id:
            return jsonify(ok=False, detail="a move had no slot_id"), 400
        try:
            _date.fromisoformat(new_date)
        except ValueError:
            return jsonify(
                ok=False, detail=f"{new_date!r} is not a valid ISO date for {slot_id}"
            ), 400
        entries.append({
            "action": "reschedule",
            "entry_key": slot_id,
            "value": {"new_date": new_date, "by": by},
        })

    result = state_store.record_state(RESCHEDULE_STORE, entries, root=DECISION_STORE_ROOT)
    if not result.get("ok"):
        return jsonify(ok=False, detail="the store would not accept the reschedule"), 500

    return jsonify(
        ok=True,
        recorded=len(entries),
        detail=f"{len(entries)} reschedule(s) saved by {by}",
    )


@app.get("/health")
def health():
    """The platform's probe, and deliberately the dullest route here.

    It answers that the process is serving and nothing else. It reads no state,
    names no calendar and touches no credential, which is what lets it sit
    outside the gate.

    It is separate from `/healthz` below on purpose. `/healthz` answers false
    when there is no window, which is the right answer to "is this agent
    working" and the wrong one to "did this deploy come up": a first deploy
    against an empty volume would fail its healthcheck, roll back, and look
    like a broken build rather than an empty calendar.
    """
    return jsonify(status="ok")


@app.get("/healthz")
def healthz():
    """Enough to tell a running app from one that cannot read its own state."""
    taxonomy = slots.load_taxonomy()
    windows, _sources = _load_windows(taxonomy)
    # `corpus` is here because it is the one thing that can be wrong on the
    # platform while the page still returns 200 and looks populated. A
    # container built from this folder alone cannot see the two content
    # agents' folders, every card falls back to a filename, and nothing about
    # the response says so. A deploy check that reads this catches it before a
    # founder does.
    corpus = render_calendar.resolve_corpus()
    return jsonify(
        ok=bool(windows),
        taxonomy=taxonomy.ok,
        calendars=[w.window_id for w in windows],
        store=str(DECISION_STORE_ROOT),
        durable=_STORE.durable,
        corpus=len(corpus),
        corpus_from=[str(render_calendar.DEFAULT_ATOMIZER), str(render_calendar.DEFAULT_VCB)],
    )


@app.get("/decisions")
def decisions():
    """The decision log, exactly as it is on disk, for the job that commits it.

    Decisions written by the hosted page land on a Railway volume, which is
    durable across deploys and invisible to git. A scheduled job in this repo
    is what puts them back under version control, and it runs in Actions, where
    the volume cannot be reached. So it reads them from here.

    Served as the raw append-only lines rather than as a parsed summary,
    because the file is the record: whatever commits it should be committing
    what the store holds and not this module's reading of it. Behind the gate
    with everything else.
    """
    path = Path(DECISION_STORE_ROOT) / state_dir.SEEDED_STORE
    body = path.read_text(encoding="utf-8") if path.is_file() else ""
    return Response(body, 200, {"Content-Type": "application/x-ndjson; charset=utf-8"})


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    print(f"Calendar on http://{args.host}:{args.port}/")
    print(f"  windows   {DEFAULT_STATE_ROOT}")
    print(f"  {_STORE.describe()}")
    print(f"  gate      {'on' if _UI_PASS else 'off (UI_PASS is not set)'}")
    print("  Approve and Decline write to the append-only store through approval/queue.py.")
    app.run(host=args.host, port=args.port, debug=args.debug)
