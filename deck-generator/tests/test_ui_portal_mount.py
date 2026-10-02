"""Serving the studio under the GTM portal's proxy (Pat, 21 Aug).

The portal signs a reviewer in with Google, then proxies this app under a path
like `/apps/project-status-deck-generator/`, forwarding the studio's own
`UI_USER`/`UI_PASS` on every request so the reviewer never sees a second prompt.
Three things have to hold for that to work, and all three fail quietly rather
than loudly — the page still renders, the links just walk the reviewer out of
the portal — so they are pinned here.

Nothing in this file makes a network call: Flask's test client sends the
forwarding headers the portal would send, which is the whole of the contract.
"""

import base64
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ui"))

pytest.importorskip("flask")

PREFIX = "/apps/project-status-deck-generator"
_USER = "qofai"
_PASS = "portal-test-pass"


@pytest.fixture
def client(monkeypatch):
    """The studio with its Basic gate switched on, as the hosted service runs it."""
    import app as ui_app

    monkeypatch.setattr(ui_app, "_UI_USER", _USER)
    monkeypatch.setattr(ui_app, "_UI_PASS", _PASS)
    return ui_app.app.test_client()


def _portal_headers(prefix=PREFIX):
    """What the portal puts on a request it forwards."""
    token = base64.b64encode(f"{_USER}:{_PASS}".encode()).decode()
    return {
        "Authorization": f"Basic {token}",
        "X-Forwarded-Prefix": prefix,
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": "portal.example.com",
    }


def _auth_only():
    token = base64.b64encode(f"{_USER}:{_PASS}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


# --- 1. the platform healthcheck ---------------------------------------------


def test_health_needs_no_credentials(client):
    """Railway's probe carries no auth. Gating it would read a healthy
    service as down and fail the deploy."""
    r = client.get("/health")
    assert r.status_code == 200
    assert r.get_json() == {"status": "ok"}


def test_health_exemption_does_not_open_anything_else(client):
    """The exemption is one path, not a hole in the gate."""
    assert client.get("/").status_code == 401


def test_health_is_declared_to_the_platform():
    """A route Railway is never told to call is not a healthcheck."""
    import json
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    cfg = json.loads((root / "railway.json").read_text())
    assert cfg["deploy"]["healthcheckPath"] == "/health"


# --- 2. links built through the forwarded prefix ------------------------------


def _links(html):
    return re.findall(r'(?:action|href)="(/[^"]*)"', html)


def test_links_carry_the_portal_prefix(client):
    """Without ProxyFix these come out at this service's own root, and every
    click leaves the portal's login behind."""
    html = client.get("/", headers=_portal_headers()).get_data(as_text=True)
    links = _links(html)
    assert links, "expected the shell to render some links"
    assert all(u.startswith(PREFIX) for u in links), [
        u for u in links if not u.startswith(PREFIX)
    ]


def test_direct_requests_are_unchanged(client):
    """Local runs and a direct hit on the Railway URL send no forwarding
    headers, and must behave exactly as they did before."""
    html = client.get("/", headers=_auth_only()).get_data(as_text=True)
    links = _links(html)
    assert links
    assert not any(u.startswith("/apps/") for u in links)


def test_prefix_is_read_from_the_header_not_assumed(client):
    """The mount path belongs to the portal, not to this repo. A different
    prefix has to come out in the links."""
    other = "/apps/somewhere-else"
    html = client.get("/", headers=_portal_headers(other)).get_data(as_text=True)
    assert all(u.startswith(other) for u in _links(html))


# --- 3. the opportunity lookup ------------------------------------------------


def _fetch_targets(html):
    return re.findall(r"fetch\('([^']*)'", html)


def test_opportunity_lookup_is_prefixed(client):
    """This fetch was the one root-relative path in the app. Under the portal a
    literal `/live-opportunities` hits the portal's own root and the dropdown
    silently stays empty."""
    html = client.get("/", headers=_portal_headers()).get_data(as_text=True)
    targets = _fetch_targets(html)
    assert targets, "expected the live-opportunity lookup to render"
    # Every fetch the shell makes carries the prefix, not only this one. The
    # PRD branch added a second (`/prd-scan`, 2026-09-20) and it is the same
    # hazard: a literal root-relative path hits the portal's own root and the
    # branch silently does nothing.
    assert all(t.startswith(PREFIX + "/") for t in targets), targets
    assert any(t.startswith(PREFIX + "/live-opportunities") for t in targets), targets


def test_no_root_relative_paths_remain_in_the_shell(client):
    """A guard for the next hand-written path: anything the portal serves has to
    be built by url_for, so nothing may come out unprefixed."""
    html = client.get("/", headers=_portal_headers()).get_data(as_text=True)
    stray = [
        u
        for u in _links(html) + _fetch_targets(html)
        if u.startswith("/") and not u.startswith(PREFIX)
    ]
    assert not stray, f"root-relative paths bypass the portal mount: {stray}"
