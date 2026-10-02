"""Headless client for the QofAI Platform MCP server.

This is the transport that lets the deck generator's data path reach QofAI /
Agent OS company data without a browser login. Per the connection doc
(``connect-qofai-mcp.md``) and the 2026-07-23 Blake sync, a server-side Python
app authenticates to ``https://mcp.example.test/mcp`` with a machine bearer key
(``Authorization: Bearer <key>``) rather than the interactive OAuth connector
flow, because there is no browser on a server to complete the login.

Scope of this module: the wire protocol only. It speaks the MCP streamable-HTTP
JSON-RPC dialect (``initialize`` → ``notifications/initialized`` → ``tools/list``
/ ``tools/call``) over one HTTPS endpoint, and surfaces auth / protocol failures
as typed exceptions. It does not know about decks, packets, companies, or
projects — those live one layer up. The intended consumer is a future
``McpProvider`` that adapts this client to the ``submit`` / ``poll`` provider
seam in ``data_source_adapter.py``; keeping the wire layer separate means that
adapter work does not re-implement transport.

Design choices, all driven by the environment:
- Built on ``httpx`` (already present) rather than the official ``mcp`` SDK,
  which requires Python 3.10+ while this project runs on 3.9.
- Synchronous, to match the adapter's ``time.sleep`` poll loop.
- Nothing client-specific or secret is baked in. The endpoint and the key's env
  var name are parameters with sensible defaults; the key itself is read from
  the environment at call time and never logged.
- Both response encodings the transport allows are handled: a plain
  ``application/json`` body (what the live server returns today) and a
  ``text/event-stream`` (SSE) body (what the spec also permits).
"""

import json
import os

import httpx

# The one public endpoint (connect-qofai-mcp.md). The Railway URL is only a
# fallback for before the tunnel lands; callers can override via ``endpoint``.
DEFAULT_ENDPOINT = "https://mcp.example.test/mcp"

# The env var the key lives in. Named on the 2026-07-23 Blake sync; overridable
# so the client is not wedded to one deployment's naming.
DEFAULT_KEY_ENV = "QOFAI_MCP_SERVER_KEY"

# MCP protocol revision the server negotiated in the initialize probe.
DEFAULT_PROTOCOL_VERSION = "2025-06-18"

_CLIENT_NAME = "qofai-deck-generator"
_CLIENT_VERSION = "0.1"


class McpError(Exception):
    """Base for every failure this client raises."""


class McpMissingKeyError(McpError):
    """No bearer key was supplied and the env var was unset or empty."""


class McpAuthError(McpError):
    """The server rejected the key (HTTP 401/403). Re-issue or re-add the key."""


class McpEdgeChallengeError(McpError):
    """Something in front of the server answered, so the call never arrived.

    The endpoint sits behind a CDN whose bot protection can challenge any
    non-browser client. A challenge comes back as an HTML interstitial carrying
    401 or 403, which by status code alone is indistinguishable from the
    server's own auth rejection — so until 2026-08-24 this client reported a
    zone-wide challenge as an expired key and sent a reviewer off to have one
    re-issued.

    A sibling of ``McpAuthError`` rather than a subclass, deliberately: the
    request never reached the MCP server, the key was therefore never read, and
    treating this as an auth failure is the exact wrong turn. The remedy is an
    allow rule for machine clients on the zone, which no header this client can
    send will substitute for.
    """


class McpProtocolError(McpError):
    """A JSON-RPC error, a malformed envelope, or an unusable response body."""

    def __init__(self, message, *, code=None, data=None):
        super().__init__(message)
        self.code = code
        self.data = data


class McpToolError(McpError):
    """A ``tools/call`` returned ``isError: true`` — the tool ran and refused.

    Distinct from ``McpProtocolError`` (the call itself failed): here the call
    succeeded but the tool reported a business error (bad args, out-of-scope id).
    """

    def __init__(self, message, *, tool, content=None):
        super().__init__(message)
        self.tool = tool
        self.content = content


# Interstitial text the common CDN challenge pages carry. Matched only inside an
# HTML body under a 401/403, so an MCP server that happens to use one of these
# words in a JSON error message cannot trip it.
_EDGE_CHALLENGE_MARKERS = (
    "just a moment",
    "checking your browser",
    "attention required",
    "cf-browser-verification",
    "enable javascript and cookies",
)


def _edge_challenge_reason(resp):
    """Why this 401/403 looks like an edge challenge, or None if it does not.

    A JSON-RPC endpoint answers in JSON or SSE. An HTML body under a 401/403 did
    not come from the server's auth layer, it came from whatever is in front of
    it. ``cf-mitigated`` is the explicit signal (Cloudflare sets it when it
    challenges or blocks); the HTML checks are the generic shape, so another
    CDN's challenge still classifies correctly instead of reading as a bad key.
    """
    mitigated = resp.headers.get("cf-mitigated", "").strip()
    if mitigated:
        return f"the edge returned cf-mitigated: {mitigated}"
    if "html" not in resp.headers.get("content-type", "").lower():
        return None
    body = resp.text[:4000].lower()
    for marker in _EDGE_CHALLENGE_MARKERS:
        if marker in body:
            return f"an HTML challenge page containing {marker!r}"
    server = resp.headers.get("server", "").strip()
    origin = f" from {server!r}" if server else ""
    return f"an HTML page{origin} where a JSON-RPC body was required"


def _parse_sse(text):
    """Yield the JSON payloads carried by an SSE body's ``data:`` lines.

    The streamable-HTTP transport may answer a POST with an event stream instead
    of a JSON object; each event's ``data:`` line carries one JSON-RPC message.
    """
    for block in text.split("\n\n"):
        data_lines = [
            line[len("data:"):].lstrip()
            for line in block.splitlines()
            if line.startswith("data:")
        ]
        if not data_lines:
            continue
        payload = "\n".join(data_lines).strip()
        if not payload:
            continue
        try:
            yield json.loads(payload)
        except json.JSONDecodeError:
            continue


class QofaiMcpClient:
    """A minimal, headless MCP client for the QofAI Platform server.

    Typical use::

        with QofaiMcpClient() as mcp:
            tools = mcp.list_tools()               # what this key grants
            result = mcp.call_tool("list_companies", {"limit": 5})

    The key is read from ``QOFAI_MCP_SERVER_KEY`` unless ``api_key`` is passed.
    ``initialize`` runs lazily on the first ``list_tools`` / ``call_tool`` and is
    not repeated.
    """

    def __init__(
        self,
        *,
        endpoint=DEFAULT_ENDPOINT,
        api_key=None,
        api_key_env=DEFAULT_KEY_ENV,
        timeout=60.0,
        protocol_version=DEFAULT_PROTOCOL_VERSION,
        client_name=_CLIENT_NAME,
        client_version=_CLIENT_VERSION,
    ):
        key = api_key if api_key is not None else os.environ.get(api_key_env, "")
        key = key.strip()
        if not key:
            raise McpMissingKeyError(
                f"No MCP key. Set {api_key_env} in the environment (a secret, "
                f"never committed) or pass api_key=."
            )
        self._endpoint = endpoint
        self._key = key
        self._key_env = api_key_env
        self._protocol_version = protocol_version
        self._client_name = client_name
        self._client_version = client_version
        self._session_id = None
        self._server_info = None
        self._initialized = False
        self._next_id = 0
        self._http = httpx.Client(timeout=timeout)

    # -- context management -------------------------------------------------

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self._http.close()

    # -- low-level JSON-RPC -------------------------------------------------

    def _headers(self):
        headers = {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": self._protocol_version,
        }
        # Only sent once the server hands one back on initialize; this server
        # runs stateless (no session id), so it usually stays absent.
        if self._session_id:
            headers["MCP-Session-Id"] = self._session_id
        return headers

    def _post(self, payload, *, expect_response=True):
        """POST one JSON-RPC message; return its result dict (or None).

        Captures a session id if the server issues one, splits 401/403 into
        ``McpEdgeChallengeError`` (an edge answered, the key was never read) and
        ``McpAuthError`` (the server itself refused the key), and unwraps a
        JSON-RPC ``error`` into ``McpProtocolError``. Handles both JSON and SSE
        response bodies.
        """
        try:
            resp = self._http.post(
                self._endpoint, headers=self._headers(), json=payload
            )
        except httpx.HTTPError as exc:
            raise McpProtocolError(f"MCP request failed: {exc}") from exc

        if resp.status_code in (401, 403):
            reason = _edge_challenge_reason(resp)
            if reason:
                raise McpEdgeChallengeError(
                    f"Blocked before the MCP server (HTTP {resp.status_code}): "
                    f"{reason}. The request never arrived, so {self._key_env} "
                    f"was never read and re-issuing it changes nothing. A CDN "
                    f"or WAF in front of {self._endpoint} is challenging this "
                    f"client; machine clients need an allow rule on that zone. "
                    f"To confirm from a shell, with no key involved: "
                    f"curl -sS -o /dev/null -D - {self._endpoint}"
                )
            raise McpAuthError(
                f"MCP server rejected the key (HTTP {resp.status_code}). The "
                f"{self._key_env} value may be wrong or expired; have an admin "
                f"re-issue it on the MCP tab."
            )
        if resp.status_code >= 400:
            raise McpProtocolError(
                f"MCP server returned HTTP {resp.status_code}: {resp.text[:300]}"
            )

        sid = resp.headers.get("mcp-session-id")
        if sid and not self._session_id:
            self._session_id = sid

        if not expect_response:
            return None

        message = self._decode(resp, payload.get("id"))
        if "error" in message:
            err = message["error"] or {}
            raise McpProtocolError(
                f"MCP JSON-RPC error: {err.get('message', 'unknown')}",
                code=err.get("code"),
                data=err.get("data"),
            )
        return message.get("result", {})

    def _decode(self, resp, want_id):
        """Return the JSON-RPC message matching ``want_id`` from a JSON or SSE body."""
        content_type = resp.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            fallback = None
            for msg in _parse_sse(resp.text):
                if want_id is not None and msg.get("id") == want_id:
                    return msg
                if "result" in msg or "error" in msg:
                    fallback = fallback or msg
            if fallback is not None:
                return fallback
            raise McpProtocolError("No JSON-RPC message found in SSE response")
        try:
            return resp.json()
        except json.JSONDecodeError as exc:
            raise McpProtocolError(
                f"MCP response was not JSON: {resp.text[:300]}"
            ) from exc

    def _rpc_id(self):
        self._next_id += 1
        return self._next_id

    # -- handshake ----------------------------------------------------------

    def initialize(self):
        """Run the MCP handshake once; return the server's ``serverInfo`` dict."""
        if self._initialized:
            return self._server_info
        result = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._rpc_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": self._protocol_version,
                    "capabilities": {},
                    "clientInfo": {
                        "name": self._client_name,
                        "version": self._client_version,
                    },
                },
            }
        )
        self._server_info = result.get("serverInfo", {})
        # A well-behaved client tells the server the handshake is complete. The
        # server does not answer this notification (no id, no result).
        self._post(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            expect_response=False,
        )
        self._initialized = True
        return self._server_info

    def _ensure_initialized(self):
        if not self._initialized:
            self.initialize()

    @property
    def server_info(self):
        return self._server_info

    # -- tools --------------------------------------------------------------

    def list_tools(self):
        """Return the tool descriptors this key grants (``tools/list``).

        The server filters ``tools/list`` by the key's capabilities, so this is
        the authoritative inventory for this identity, not the full catalog.
        """
        self._ensure_initialized()
        result = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._rpc_id(),
                "method": "tools/list",
                "params": {},
            }
        )
        return result.get("tools", [])

    def call_tool(self, name, arguments=None):
        """Call one tool; return the raw MCP ``result`` (content + isError).

        Raises ``McpToolError`` if the tool reports ``isError: true``. Use
        ``call_tool_json`` when the tool returns a JSON text block you want
        parsed.
        """
        self._ensure_initialized()
        result = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._rpc_id(),
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            }
        )
        if result.get("isError"):
            raise McpToolError(
                f"Tool {name!r} reported an error: {_text_of(result)[:300]}",
                tool=name,
                content=result.get("content"),
            )
        return result

    def call_tool_text(self, name, arguments=None):
        """Call a tool and return the concatenated text of its content blocks."""
        return _text_of(self.call_tool(name, arguments))

    def call_tool_json(self, name, arguments=None):
        """Call a tool whose content is a JSON text block; return it parsed.

        The data tools return their payload as a JSON string inside a ``text``
        content block, so this unwraps the one extra layer for callers.
        """
        text = self.call_tool_text(name, arguments)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise McpProtocolError(
                f"Tool {name!r} did not return JSON text: {text[:300]}"
            ) from exc


def _text_of(result):
    """Join the ``text`` fields of an MCP result's content blocks."""
    parts = [
        block.get("text", "")
        for block in (result.get("content") or [])
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "\n".join(parts)
