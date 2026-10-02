"""What the portal readers must do before anyone can trust their first live call.

These readers have never reached a live portal, so every test here is against
a stub that answers the way the portal's own source says it answers. That is
worth being plain about: these prove the reader handles each documented shape
correctly, and they cannot prove the shapes are right. The first real call is
what does that, and `portal_sources.yaml` carries `verified_against_live_portal`
so nobody mistakes one for the other.

What they are really for is the failure modes, which are all silent. A token
that is refused, a scrape column that was renamed, an endpoint that dedupes by
person when we wanted events, an empty answer that means the scrape never ran:
every one of those produces a page that looks fine and a number that is wrong.

Run it:  cd ingestion && ../.venv/bin/python test_portal_reader.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import portal_reader  # noqa: E402
import read_status  # noqa: E402

PASSED: list = []
FAILED: list = []


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


class _Response:
    def __init__(self, status_code=200, body=None, text=""):
        self.status_code = status_code
        self._body = body
        self._text = text

    def json(self):
        if self._body is None:
            raise ValueError("no JSON object could be decoded")
        return self._body


class _Requests:
    """Stands in for `requests`, and records what was asked for."""

    RequestException = Exception

    def __init__(self, responses, error=None):
        # path -> response, or a single response for every path.
        self.responses = responses
        self.error = error
        self.calls: list = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params or {}), "headers": dict(headers or {})})
        if self.error:
            raise self.error
        if isinstance(self.responses, dict):
            for fragment, response in self.responses.items():
                if fragment in url:
                    return response
            return _Response(404, {})
        return self.responses


class _Env:
    """Sets PORTAL_URL and PORTAL_TOKEN for the body of a test, and a fake
    `requests` with them, restoring all three afterwards."""

    def __init__(self, requests_stub, token="qap_test", url="https://portal.example"):
        self.stub = requests_stub
        self.token = token
        self.url = url

    def __enter__(self):
        import os

        self._saved = (
            os.environ.get(portal_reader.TOKEN_ENV_VAR),
            os.environ.get(portal_reader.URL_ENV_VAR),
            portal_reader.requests,
        )
        if self.token is None:
            os.environ.pop(portal_reader.TOKEN_ENV_VAR, None)
        else:
            os.environ[portal_reader.TOKEN_ENV_VAR] = self.token
        os.environ[portal_reader.URL_ENV_VAR] = self.url
        portal_reader.requests = self.stub
        return self.stub

    def __exit__(self, *exc):
        import os

        token, url, requests_mod = self._saved
        for name, value in ((portal_reader.TOKEN_ENV_VAR, token), (portal_reader.URL_ENV_VAR, url)):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        portal_reader.requests = requests_mod
        return False


CONFIG = portal_reader.load_config()


# The shape a real row has, copied from the fixtures in
# `repos/linkedin-content-outreach/tests/test_engagement_source.py`, which is
# an agent that has consumed these exact rows since 2026-08-12. The portal's
# own three keys are on top. Written out rather than paraphrased, because the
# two details that matter are both easy to lose in a paraphrase: the post
# column is `postsUrl` with an s, and the flags are the strings "true" and ""
# rather than booleans.
def _engagement_row(**overrides):
    row = {
        "profile_url": "https://www.linkedin.com/in/jane-doe",
        "pull_source": "post-engagement",
        "pull_network": "jordan",
        "pull_started_at": "2026-09-20T12:00:00+00:00",
        "scraped_at": "2026-09-20T12:03:11+00:00",
        "postsUrl": "https://www.linkedin.com/feed/update/urn:li:activity:7123",
        "hasLiked": "true",
        "hasCommented": "",
        "lastCommentedAt": "",
    }
    row.update(overrides)
    return row


# --- the config itself --------------------------------------------------------


@check("the config still says nothing here has been checked against a live portal")
def _():
    """This flips to true the day somebody makes the first real call and
    confirms the shapes. Until then the README, the page and the run log all
    read it, so it flipping early would make three honest statements false."""
    assert CONFIG.verified is False, (
        "portal_sources.yaml says the portal has been verified. If that is true, the "
        "README's 'never called a live portal' paragraph is now wrong and must go."
    )


@check("engagement asks for every row, not the latest per person")
def _():
    """The trap in this endpoint. It dedupes by profile_url unless asked not
    to, so a founder who reacted to five posts arrives as one row and four
    engagements vanish with no error anywhere. Engagement is an event per
    (person, post), not a state per person."""
    params = CONFIG.source("post_engagement").get("params") or {}
    assert params.get("all") is True, f"engagement would be deduped by person: {params}"


@check("engagement asks for the post scraper's rows, not all of LinkedIn's")
def _():
    """Three phantoms feed one store. 01 exports a search and 02 scrapes
    profiles; only 03 is about a post. Without the filter this reader would
    count profile rows as engagements."""
    params = CONFIG.source("post_engagement").get("params") or {}
    assert params.get("source") == "post-engagement", params
    assert params.get("network"), "a pull with no network cannot be attributed"


@check("published content sends no status filter")
def _():
    """We do not know what values the library's status column takes. Asking
    for "Published" against a library that says "Live" returns nothing and
    looks exactly like an empty library."""
    params = CONFIG.source("published_content").get("params") or {}
    assert "status" not in params, f"a guessed status vocabulary would filter the library away: {params}"


# --- how it fails -------------------------------------------------------------


@check("no token is unconfigured, and nothing is called")
def _():
    stub = _Requests(_Response(200, {"rows": []}))
    with _Env(stub, token=None):
        status = read_status.ReadStatus()
        rows = portal_reader.read_post_engagement(config=CONFIG, status=status)
    assert rows == []
    assert status.status == read_status.UNCONFIGURED, status.status
    assert stub.calls == [], "a reader with no token still called the portal"


@check("a refused token is rejected, which is not the same as unset")
def _():
    """Both redden the daily build and they are somebody different's problem:
    unset is an onboarding step nobody did, refused is a credential somebody
    revoked."""
    for code in (401, 403):
        stub = _Requests(_Response(code, {}))
        with _Env(stub):
            status = read_status.ReadStatus()
            rows = portal_reader.read_published_content(config=CONFIG, status=status)
        assert rows == []
        assert status.status == read_status.REJECTED, (code, status.status)


@check("an unreachable portal is unreachable, not empty")
def _():
    stub = _Requests(None, error=Exception("connection refused"))
    stub.RequestException = Exception
    with _Env(stub):
        status = read_status.ReadStatus()
        rows = portal_reader.read_post_engagement(config=CONFIG, status=status)
    assert rows == []
    assert status.status == read_status.UNREACHABLE, status.status


@check("a non-JSON answer fails rather than pretending to be empty")
def _():
    stub = _Requests(_Response(200, None, text="<html>a proxy error page</html>"))
    with _Env(stub):
        status = read_status.ReadStatus()
        rows = portal_reader.read_published_content(config=CONFIG, status=status)
    assert rows == []
    assert status.status == read_status.FAILED, status.status


@check("the request carries the bearer token and nothing else identifying")
def _():
    stub = _Requests(_Response(200, {"rows": []}))
    with _Env(stub):
        portal_reader.read_published_content(config=CONFIG, status=read_status.ReadStatus())
    assert stub.calls, "nothing was called"
    headers = stub.calls[0]["headers"]
    assert headers.get("Authorization") == "Bearer qap_test", headers
    assert set(headers) == {"Authorization"}, headers


# --- empty is not one answer --------------------------------------------------


@check("no engagement rows and no pull means the scrape never ran")
def _():
    """The endpoint's own note says so. No rows and no pull are different
    answers, and treating them as one is how a missing week-over-week baseline
    goes unnoticed."""
    stub = _Requests({"/linkedin/rows": _Response(200, {"rows": []}),
                      "/linkedin/pulls": _Response(200, {"pulls": [], "count": 0})})
    with _Env(stub):
        status = read_status.ReadStatus()
        rows = portal_reader.read_post_engagement(config=CONFIG, status=status)
    assert rows == []
    assert status.status == read_status.MISSING, status.status
    assert "pull" in status.detail


@check("no engagement rows but a pull on record means nobody engaged")
def _():
    stub = _Requests({"/linkedin/rows": _Response(200, {"rows": []}),
                      "/linkedin/pulls": _Response(200, {"pulls": [{"id": "p-1"}], "count": 1})})
    with _Env(stub):
        status = read_status.ReadStatus()
        rows = portal_reader.read_post_engagement(config=CONFIG, status=status)
    assert rows == []
    assert status.status == read_status.EMPTY, status.status


@check("an unreadable pull log is not recorded as a pull that did not happen")
def _():
    """Three values rather than two: we could not find out must never be
    written down as it did not happen."""
    stub = _Requests({"/linkedin/rows": _Response(200, {"rows": []}),
                      "/linkedin/pulls": _Response(500, {})})
    with _Env(stub):
        status = read_status.ReadStatus()
        portal_reader.read_post_engagement(config=CONFIG, status=status)
    assert status.status == read_status.EMPTY, status.status
    assert "could not be read" in status.detail, status.detail


# --- engagement rows ----------------------------------------------------------


@check("an engagement row is read, and says which column each field came from")
def _():
    stub = _Requests({"/linkedin/rows": _Response(200, {"rows": [_engagement_row()]})})
    with _Env(stub):
        status = read_status.ReadStatus()
        rows = portal_reader.read_post_engagement(config=CONFIG, status=status)
    assert len(rows) == 1, rows
    row = rows[0]
    assert status.status == read_status.OK
    assert row.post_url.endswith("activity:7123"), row.post_url
    assert row.action == "like"
    assert row.profile_url.endswith("jane-doe")
    assert row.resolved_from["post_url"] == "postsUrl", row.resolved_from
    assert row.resolved_from["action"] == "hasLiked", row.resolved_from
    # No name column has ever been seen on these rows, so this one stays
    # unresolved and the display falls back to the profile URL. An unresolved
    # name is not a failure; an unresolved post is.
    assert row.unresolved == ["engager_name"], row.unresolved
    # The whole original row is kept whatever we managed to resolve, because
    # the columns are PhantomBuster's and may hold something nobody asked for.
    assert row.raw["pull_source"] == "post-engagement"


@check("a renamed column is found through the candidates rather than lost")
def _():
    """The scrape's header changes without warning. A reader that hardcoded
    `postUrl` would return zero engagements the morning it became
    `post_url`, while reporting a perfectly healthy read."""
    row = _engagement_row()
    row["postUrl"] = row.pop("postsUrl")
    row["profileUrl"] = row.pop("profile_url")
    stub = _Requests({"/linkedin/rows": _Response(200, {"rows": [row]})})
    with _Env(stub):
        rows = portal_reader.read_post_engagement(config=CONFIG, status=read_status.ReadStatus())
    assert len(rows) == 1
    assert rows[0].resolved_from["post_url"] == "postUrl", rows[0].resolved_from
    # The LinkedIn agent's mapper falls back to profileUrl, so a row carrying
    # only that is a shape somebody has already met rather than a hypothetical.
    assert rows[0].profile_url.endswith("jane-doe"), rows[0].profile_url


@check("a row whose post column matches nothing is kept and reported, not dropped")
def _():
    """Silently dropping it would report a healthy read of fewer rows, which
    is the failure that looks like success."""
    row = _engagement_row()
    row["theNewNameNobodyToldUs"] = row.pop("postsUrl")
    stub = _Requests({"/linkedin/rows": _Response(200, {"rows": [row, _engagement_row()]})})
    with _Env(stub):
        status = read_status.ReadStatus()
        rows = portal_reader.read_post_engagement(config=CONFIG, status=status)
    assert len(rows) == 2, "a row was dropped rather than reported"
    blind = [r for r in rows if "post_url" in r.unresolved]
    assert len(blind) == 1
    assert blind[0].raw["theNewNameNobodyToldUs"], "the value was not even kept"
    assert status.status == read_status.OK, "some rows were readable, so the read is ok"
    assert "no resolvable post column" in status.detail


@check("every row unreadable is a failure, not a successful read of nothing useful")
def _():
    rows_in = []
    for _ in range(3):
        row = _engagement_row()
        row["somethingElse"] = row.pop("postsUrl")
        rows_in.append(row)
    stub = _Requests({"/linkedin/rows": _Response(200, {"rows": rows_in})})
    with _Env(stub):
        status = read_status.ReadStatus()
        rows = portal_reader.read_post_engagement(config=CONFIG, status=status)
    assert len(rows) == 3
    assert status.status == read_status.FAILED, status.status


@check("a like and a comment are told apart, and neither is a bare truthiness check")
def _():
    """The flags reach us as the strings "true" and "", never as booleans, so
    `if row["hasLiked"]` would read the string "false" as a like. Both can be
    true on one row, and a row with neither set is still an engagement,
    because the scrape returning it at all means it saw the person engage."""
    cases = [
        ({"hasLiked": "true", "hasCommented": ""}, "like"),
        ({"hasLiked": "", "hasCommented": "true"}, "comment"),
        ({"hasLiked": "true", "hasCommented": "true"}, "comment"),
        ({"hasLiked": "", "hasCommented": ""}, "engagement"),
        ({"hasLiked": "false", "hasCommented": "false"}, "engagement"),
    ]
    for flags, expected in cases:
        stub = _Requests({"/linkedin/rows": _Response(200, {"rows": [_engagement_row(**flags)]})})
        with _Env(stub):
            rows = portal_reader.read_post_engagement(config=CONFIG, status=read_status.ReadStatus())
        assert rows[0].action == expected, (flags, rows[0].action)


@check("an engagement is keyed by the pair, so the same one read twice is one")
def _():
    """The store is append-only and collapses on `entry_key`. If the key were
    the row's arrival, thirty mornings of reading the same like would be
    thirty engagements."""
    stub = _Requests({"/linkedin/rows": _Response(200, {"rows": [_engagement_row()]})})
    with _Env(stub):
        first = portal_reader.read_post_engagement(config=CONFIG, status=read_status.ReadStatus())
        second = portal_reader.read_post_engagement(config=CONFIG, status=read_status.ReadStatus())
    assert first[0].item_id == second[0].item_id
    assert "activity:7123" in first[0].item_id and "jane-doe" in first[0].item_id


# --- published content --------------------------------------------------------


@check("published rows are read, newest first")
def _():
    body = {"rows": [
        {"title": "Older", "url": "https://x/1", "date_published": "2026-08-01", "channel": "LinkedIn"},
        {"title": "Newer", "url": "https://x/2", "date_published": "2026-09-01", "channel": "LinkedIn"},
    ]}
    stub = _Requests({"/content": _Response(200, body)})
    with _Env(stub):
        status = read_status.ReadStatus()
        posts = portal_reader.read_published_content(config=CONFIG, status=status)
    assert [p.title for p in posts] == ["Newer", "Older"], [p.title for p in posts]
    assert status.status == read_status.OK


@check("a content row with no publish date is a plan, not a history")
def _():
    body = {"rows": [
        {"title": "Drafted", "url": "https://x/1", "date_published": ""},
        {"title": "Published", "url": "https://x/2", "date_published": "2026-09-01"},
    ]}
    stub = _Requests({"/content": _Response(200, body)})
    with _Env(stub):
        posts = portal_reader.read_published_content(config=CONFIG, status=read_status.ReadStatus())
    assert [p.title for p in posts] == ["Published"], [p.title for p in posts]


@check("a library holding only undated rows is empty rather than ok")
def _():
    body = {"rows": [{"title": "Drafted", "url": "https://x/1", "date_published": ""}]}
    stub = _Requests({"/content": _Response(200, body)})
    with _Env(stub):
        status = read_status.ReadStatus()
        posts = portal_reader.read_published_content(config=CONFIG, status=status)
    assert posts == []
    assert status.status == read_status.EMPTY
    assert "none of them published" in status.detail


@check("themes arrive as a list whether the portal sends a list or a string")
def _():
    """Airtable multi-selects come back either way depending on the column,
    and a string iterated as a list becomes a theme per character."""
    body = {"rows": [
        {"title": "A", "url": "https://x/1", "date_published": "2026-09-01",
         "themes": "ai-in-pe, value-creation"},
        {"title": "B", "url": "https://x/2", "date_published": "2026-09-02",
         "themes": ["ai-in-pe"]},
    ]}
    stub = _Requests({"/content": _Response(200, body)})
    with _Env(stub):
        posts = portal_reader.read_published_content(config=CONFIG, status=read_status.ReadStatus())
    by_title = {p.title: p.themes for p in posts}
    assert by_title["A"] == ["ai-in-pe", "value-creation"], by_title["A"]
    assert by_title["B"] == ["ai-in-pe"], by_title["B"]


if __name__ == "__main__":
    for line in PASSED:
        print(line)
    for line in FAILED:
        print(line, file=sys.stderr)
    print(f"\n{len(PASSED)}/{len(PASSED) + len(FAILED)} passed")
    sys.exit(1 if FAILED else 0)
