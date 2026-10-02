"""A CDN challenge must not be reported as an expired key.

Written after 2026-08-24, when the opportunity picker showed "the
QOFAI_MCP_SERVER_KEY value may be wrong or expired" while the endpoint was in
fact returning a Cloudflare managed challenge. The key was fine and had never
been read: the request died at the edge. The observable that proved it was that
the same call failed identically with the key, without the key, and with a
browser User-Agent, and a rejection that ignores credentials is not a
credential rejection.

Every case drives ``QofaiMcpClient`` through an ``httpx.MockTransport``, so nothing
here makes a network call and no key is needed beyond a literal placeholder.
"""

import httpx
import pytest

from qofai_mcp_client import (
    McpAuthError,
    McpEdgeChallengeError,
    McpError,
    QofaiMcpClient,
)

ENDPOINT = "https://mcp.example.test/mcp"
KEY = "placeholder-not-a-real-key"

# The shape Cloudflare actually returned on 2026-08-24: 403, HTML interstitial,
# and the cf-mitigated header naming the decision.
CHALLENGE_BODY = (
    '<!DOCTYPE html><html lang="en-US"><head><title>Just a moment...</title>'
    "</head><body>Enable JavaScript and cookies to continue</body></html>"
)


def _client(handler):
    client = QofaiMcpClient(endpoint=ENDPOINT, api_key=KEY)
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def _responder(status, *, body, headers):
    def handler(request):
        return httpx.Response(status, text=body, headers=headers)

    return handler


@pytest.mark.parametrize("status", [401, 403])
def test_cf_mitigated_header_is_an_edge_challenge_not_an_auth_failure(status):
    """The explicit signal. Present on either status, it settles the question."""
    client = _client(_responder(
        status,
        body=CHALLENGE_BODY,
        headers={"content-type": "text/html", "server": "cloudflare",
                 "cf-mitigated": "challenge"},
    ))
    with pytest.raises(McpEdgeChallengeError) as caught:
        client.list_tools()
    message = str(caught.value)
    assert "cf-mitigated: challenge" in message
    # The whole point: it must not send anyone after the key.
    assert "expired" not in message
    assert "re-issuing it changes nothing" in message


def test_an_html_interstitial_without_the_header_still_classifies():
    """A CDN that sets no cf-mitigated header must not read as a bad key.

    The generic shape carries it: an HTML body under a 403 did not come from a
    JSON-RPC server's auth layer.
    """
    client = _client(_responder(
        403,
        body=CHALLENGE_BODY,
        headers={"content-type": "text/html; charset=UTF-8"},
    ))
    with pytest.raises(McpEdgeChallengeError) as caught:
        client.list_tools()
    assert "just a moment" in str(caught.value).lower()


def test_html_with_no_known_marker_names_the_server_that_sent_it():
    """An unrecognised interstitial is still an edge, and says whose."""
    client = _client(_responder(
        403,
        body="<html><body>Forbidden</body></html>",
        headers={"content-type": "text/html", "server": "some-other-cdn"},
    ))
    with pytest.raises(McpEdgeChallengeError) as caught:
        client.list_tools()
    assert "some-other-cdn" in str(caught.value)


def test_a_real_key_rejection_is_still_an_auth_error():
    """The regression guard in the other direction.

    A genuine refusal from the server arrives as JSON, and must keep pointing at
    the key, otherwise this fix trades one misleading message for another.
    """
    client = _client(_responder(
        403,
        body='{"error":"forbidden"}',
        headers={"content-type": "application/json"},
    ))
    with pytest.raises(McpAuthError) as caught:
        client.list_tools()
    message = str(caught.value)
    assert "QOFAI_MCP_SERVER_KEY" in message
    assert "expired" in message


def test_the_new_error_stays_inside_the_mcp_hierarchy():
    """`live_proposal_provider` catches `McpError` and reports the class name.

    Descending from it is what makes this surface as E_SOURCE_UNREACHABLE with a
    named error_type instead of escaping the seam as a bare exception.
    """
    assert issubclass(McpEdgeChallengeError, McpError)
    # A sibling of McpAuthError, not a subclass: an `except McpAuthError` branch
    # must never swallow a challenge and conclude the key is bad.
    assert not issubclass(McpEdgeChallengeError, McpAuthError)
