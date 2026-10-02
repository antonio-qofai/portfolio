"""Serving the calendar behind the QofAI portal, and writing where it survives.

Everything here fails quietly rather than loudly, which is why it is pinned
rather than reviewed. A missing prefix renders a perfect page whose Approve
button posts into the portal's root and 404s. A missing password on the
platform serves three founders' calendars to whoever has the URL. A store that
resolved to the container serves a working calendar that forgets every decision
on the next deploy. None of the three shows up as an error on screen.

Nothing in this file makes a network call. Flask's test client sends the
forwarding headers the portal would send, which is the whole of the contract,
and the store resolution is exercised against temporary directories.

Run it:  cd ui && ../.venv/bin/python test_portal_mount.py
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_AGENT_ROOT = _HERE.parent
for folder in ("calendar_model", "display", "approval", "narrative"):
    sys.path.insert(0, str(_AGENT_ROOT / folder))
sys.path.insert(0, str(_HERE))

import app as app_mod  # noqa: E402
import state_dir  # noqa: E402

PASSED = []
FAILED = []


def check(name):
    def wrap(fn):
        try:
            fn()
        except AssertionError as exc:
            FAILED.append(f"FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            FAILED.append(f"ERROR {name}: {type(exc).__name__}: {exc}")
        else:
            PASSED.append(f"ok  {name}")
        return fn

    return wrap


# The portal mounts each agent under its own folder name.
PREFIX = "/apps/thought-leadership-calendar-manager"
_USER = "qofai"
_PASS = "portal-test-pass"

# Same reason as in test_app.py: the page shows windows that have not fully
# passed, so a test reading the real October windows on the real clock would
# start failing on 2026-10-31.
TEST_TODAY = date(2026, 9, 18)


def _sandbox():
    """A state root holding the real windows, so slot ids are the real ones."""
    tmp = Path(tempfile.mkdtemp())
    for path in (_AGENT_ROOT / "state").glob("window-*-*.json"):
        if path.name.count(".") == 1:
            shutil.copy(path, tmp / path.name)
    return tmp


def _client(gate=True, on_platform=False, state_root=None):
    root = Path(state_root) if state_root else _sandbox()
    app_mod.DEFAULT_STATE_ROOT = root
    app_mod.DECISION_STORE_ROOT = root
    app_mod._today = lambda: TEST_TODAY
    app_mod._UI_USER = _USER
    app_mod._UI_PASS = _PASS if gate else None
    app_mod._ON_PLATFORM = on_platform
    app_mod.app.config["TESTING"] = True
    return app_mod.app.test_client()


def _auth():
    token = base64.b64encode(f"{_USER}:{_PASS}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _portal_headers(prefix=PREFIX):
    """What the portal puts on a request it forwards."""
    headers = dict(_auth())
    headers.update(
        {
            "X-Forwarded-Prefix": prefix,
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "portal.example.com",
        }
    )
    return headers


def _app_urls(html):
    """Every path this page will send a browser or a fetch to."""
    found = re.findall(r'(?:action|href|data-record-url)="(/[^"]*)"', html)
    found += re.findall(r"fetch\(\s*'([^']*)'", html)
    return found


# --- the platform's healthcheck ----------------------------------------------


@check("the healthcheck answers without credentials")
def _():
    """Railway's probe carries no auth. Gating it reads a healthy service as
    down, fails the deploy, and looks like broken code."""
    client = _client()
    response = client.get("/health")
    assert response.status_code == 200, response.status_code
    assert json.loads(response.data) == {"status": "ok"}, response.data


@check("the healthcheck exemption is one path, not a hole in the gate")
def _():
    client = _client()
    assert client.get("/").status_code == 401
    assert client.get("/healthz").status_code == 401
    assert client.get("/decisions").status_code == 401
    assert client.post("/decide", json={}).status_code == 401


@check("the healthcheck is the route the platform is actually told to call")
def _():
    """A health route Railway never calls is not a healthcheck. This is the
    B3 defect from the build plan: the app answered on /healthz while the
    config pointed at /health, so the deploy would have 404'd its own probe."""
    cfg = json.loads((_AGENT_ROOT / "railway.json").read_text(encoding="utf-8"))
    declared = cfg["deploy"]["healthcheckPath"]
    assert declared in app_mod._UNGATED, f"{declared} is declared but gated"
    client = _client()
    assert client.get(declared).status_code == 200, declared


@check("the healthcheck says nothing about state, so an empty calendar still deploys")
def _():
    """/healthz answers false with no window, which is the right answer to
    "is this agent working" and the wrong one to "did this deploy come up".
    A first deploy against an empty volume must not roll itself back."""
    empty = Path(tempfile.mkdtemp())
    client = _client(state_root=empty)
    assert client.get("/health").status_code == 200
    body = json.loads(client.get("/healthz", headers=_auth()).data)
    assert body["ok"] is False, "a state probe that cannot fail reports nothing"
    assert json.loads(client.get("/health").data) == {"status": "ok"}


# --- the gate -----------------------------------------------------------------


@check("a wrong password is refused and a right one is served")
def _():
    client = _client()
    wrong = base64.b64encode(f"{_USER}:not-it".encode()).decode()
    assert client.get("/", headers={"Authorization": f"Basic {wrong}"}).status_code == 401
    assert client.get("/", headers=_auth()).status_code == 200


@check("no password set locally means no gate, which is how a laptop runs")
def _():
    client = _client(gate=False)
    assert client.get("/").status_code == 200


@check("no password set on the platform fails closed")
def _():
    """The failure this prevents is not an outage. It is the calendar, the
    posts and the record of who approved what, served to anyone with the URL
    because one variable was missed on a new service."""
    client = _client(gate=False, on_platform=True)
    response = client.get("/")
    assert response.status_code == 503, response.status_code
    assert b"UI_PASS" in response.data, "the 503 does not say what is missing"
    assert client.get("/health").status_code == 200, "the probe must still answer"


# --- links through the portal's prefix ----------------------------------------


@check("every path the page uses carries the portal prefix")
def _():
    client = _client()
    html = client.get("/", headers=_portal_headers()).get_data(as_text=True)
    urls = _app_urls(html)
    assert urls, "the page sent nowhere at all, which cannot be right"
    stray = [u for u in urls if not u.startswith(PREFIX)]
    assert not stray, f"these resolve against the portal's root, not this app: {stray}"


@check("the record route is built through the prefix, not written relative")
def _():
    """The one POST on the page. Unprefixed it hits the portal's own root, the
    page still renders, and Approve stops recording with no error anywhere."""
    html = _client().get("/", headers=_portal_headers()).get_data(as_text=True)
    match = re.search(r'data-record-url="([^"]*)"', html)
    assert match, "the page does not advertise a record route"
    assert match.group(1) == PREFIX + "/decide", match.group(1)


@check("the prefix is read from the header rather than assumed")
def _():
    """The mount path belongs to the portal, not to this repo."""
    other = "/apps/somewhere-else"
    html = _client().get("/", headers=_portal_headers(other)).get_data(as_text=True)
    stray = [u for u in _app_urls(html) if not u.startswith(other)]
    assert not stray, stray


@check("a direct request is unchanged by any of this")
def _():
    """Local runs and a direct hit on the Railway URL send no forwarding
    headers and must behave exactly as they did before."""
    html = _client().get("/", headers=_auth()).get_data(as_text=True)
    assert not any(u.startswith("/apps/") for u in _app_urls(html))
    assert 'data-record-url="/decide"' in html


# --- where a decision is written ----------------------------------------------


@check("an explicit override wins, and the repo is the last resort")
def _():
    volume = Path(tempfile.mkdtemp())
    os.environ["CALENDAR_STATE_DIR"] = str(volume)
    try:
        resolved = state_dir.resolve_store_dir()
    finally:
        del os.environ["CALENDAR_STATE_DIR"]
    assert resolved.directory == volume, resolved.directory
    assert resolved.durable is True
    assert resolved.source == "CALENDAR_STATE_DIR"

    plain = state_dir.resolve_store_dir()
    assert plain.directory == state_dir.REPO_STATE_DIR, plain.directory


@check("a variable naming a path that was never mounted is skipped, not trusted")
def _():
    """The exact silent failure the candidate list exists to catch: the
    variable is set, the volume is not there, and writing would go to a
    container path that is wiped on the next deploy."""
    missing = Path(tempfile.mkdtemp()) / "never-mounted"
    os.environ["CALENDAR_STATE_DIR"] = str(missing)
    try:
        resolved = state_dir.resolve_store_dir()
    finally:
        del os.environ["CALENDAR_STATE_DIR"]
    assert resolved.directory != missing, "an unmounted path was trusted"
    assert "CALENDAR_STATE_DIR" in resolved.checked, "it did not even try"


@check("falling through to the container on the platform is reported as not durable")
def _():
    """There is always a location, so the question is never whether one was
    found. It is whether what is written there survives the next deploy."""
    os.environ[state_dir.PLATFORM_VAR] = "production"
    try:
        hosted = state_dir.resolve_store_dir()
    finally:
        del os.environ[state_dir.PLATFORM_VAR]
    assert hosted.directory == state_dir.REPO_STATE_DIR
    assert hosted.durable is False, "a container path was reported as durable"
    assert "NOT durable" in hosted.describe()

    local = state_dir.resolve_store_dir()
    assert local.durable is True, "a local run must not warn; nobody reads a daily notice"


@check("a fresh volume is seeded with the decisions once, and never written over")
def _():
    """A volume mounted on a service that has been running is empty, and every
    decision taken before it lives in the committed copy. Seeding once is what
    stops the page reporting that nobody has approved anything."""
    # Against a stand-in repo rather than the real one, because nothing has
    # been approved yet and a test that passes by finding no file to copy is
    # not testing the copy.
    committed_root = Path(tempfile.mkdtemp())
    (committed_root / state_dir.SEEDED_STORE).write_text("committed\n", encoding="utf-8")
    real_repo = state_dir.REPO_STATE_DIR
    state_dir.REPO_STATE_DIR = committed_root
    try:
        volume = Path(tempfile.mkdtemp())
        assert state_dir.seed_store(volume) == [state_dir.SEEDED_STORE]
        assert (volume / state_dir.SEEDED_STORE).read_text(encoding="utf-8") == "committed\n"

        # The volume is the authority from here on. The repo copy is at best an
        # hour behind it, so a second pass must not touch what is there.
        (volume / state_dir.SEEDED_STORE).write_text("newer\n", encoding="utf-8")
        assert state_dir.seed_store(volume) == [], "a second boot overwrote live decisions"
        assert (volume / state_dir.SEEDED_STORE).read_text(encoding="utf-8") == "newer\n"

        # A volume with no decision log yet, and a repo that has none either,
        # is the state this agent is actually in today: nothing to copy and
        # nothing to report.
        (committed_root / state_dir.SEEDED_STORE).unlink()
        assert state_dir.seed_store(Path(tempfile.mkdtemp())) == []
    finally:
        state_dir.REPO_STATE_DIR = real_repo


@check("seeding never touches the repo's own state directory")
def _():
    assert state_dir.seed_store(state_dir.REPO_STATE_DIR) == []


@check("windows are read from the repo while decisions are written to the store")
def _():
    """The split that makes the hosted version work without a merge: no file
    has two writers. Windows come from git, decisions from the page."""
    windows = _sandbox()
    store = Path(tempfile.mkdtemp())
    app_mod.DEFAULT_STATE_ROOT = windows
    app_mod.DECISION_STORE_ROOT = store
    app_mod._today = lambda: TEST_TODAY
    app_mod._UI_USER, app_mod._UI_PASS, app_mod._ON_PLATFORM = _USER, _PASS, False
    client = app_mod.app.test_client()

    page = client.get("/", headers=_auth()).get_data(as_text=True)
    slot_id = re.search(r'data-slot="([^"]+)"', page).group(1)
    status = client.post(
        "/decide",
        json={"by": "A Reviewer", "decisions": [{"slot_id": slot_id, "state": "approved"}]},
        headers=_auth(),
    )
    assert status.status_code == 200, status.data

    assert (store / "approvals.jsonl").is_file(), "the decision did not reach the store"
    assert not (windows / "approvals.jsonl").exists(), (
        "a decision was written into the window directory, which a deploy discards"
    )
    for path in windows.glob("window-*.json"):
        assert path.stat().st_size, path


@check("the decision log is served for the commit-back job, and only behind the gate")
def _():
    """The scheduled job that puts decisions under version control runs in
    Actions, where the Railway volume cannot be reached, so it reads them from
    here. Raw lines, because the file is the record."""
    store = Path(tempfile.mkdtemp())
    client = _client(state_root=store)
    assert client.get("/decisions").status_code == 401

    empty = client.get("/decisions", headers=_auth())
    assert empty.status_code == 200 and empty.data == b"", empty.data

    line = json.dumps({"slot_id": "jordan-2026-10-01", "state": "approved"})
    (store / "approvals.jsonl").write_text(line + "\n", encoding="utf-8")
    served = client.get("/decisions", headers=_auth())
    assert served.get_data(as_text=True) == line + "\n", served.data
    assert "ndjson" in served.headers["Content-Type"], served.headers["Content-Type"]


@check("the approval queue is the real one, not the standard library's")
def _():
    """Found by running this app under gunicorn on 2026-09-20, and findable no
    other way. Python ships a `queue` module. `import queue` returns whatever
    is in `sys.modules`, gunicorn's worker imports the standard one first, and
    the folder on `sys.path` then loses. The page rendered perfectly and the
    first decision anyone recorded answered 500.
    """
    import queue as stdlib_queue

    assert app_mod.queue_mod is not stdlib_queue, (
        "the app is holding the standard library's FIFO, so no decision can be recorded"
    )
    assert app_mod.queue_mod.__file__ == str(_AGENT_ROOT / "approval" / "queue.py")
    for rule in ("decide", "DECIDABLE_STATES"):
        assert hasattr(app_mod.queue_mod, rule), rule


if __name__ == "__main__":
    for line in PASSED:
        print(line)
    for line in FAILED:
        print(line, file=sys.stderr)
    print(f"\n{len(PASSED)}/{len(PASSED) + len(FAILED)} passed")
    sys.exit(1 if FAILED else 0)
