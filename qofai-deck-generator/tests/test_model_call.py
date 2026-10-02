"""Every model leg is bounded, and a retry says so.

Why this file exists. Until 2026-09-02 all five call sites in this pipeline built
a bare `anthropic.Anthropic(api_key=...)`, which inherits the SDK's 600-second
read timeout and two silent retries. A stalled render stream was therefore
invisible for ten minutes and a repeatedly stalling one could burn half an hour
before raising anything; one observed UI run took 910 seconds, a clean run plus
roughly 8.5 minutes of stall. `model_call` is the bound and the retry, and these
tests hold both halves of its contract.

No network and no SDK. `bounded_client` is the only function here that imports
`anthropic`, and it is exercised against a stand-in module rather than the real
one, so this file runs on the system interpreter like the rest of the suite.
"""

import logging
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import model_call  # noqa: E402


class ReadTimeout(Exception):
    """Named exactly as `httpx.ReadTimeout` is, because that is what `RETRYABLE`
    matches on. The real one raised out of a stalled render stream on
    2026-09-02, which is the failure this whole module exists for."""


class Refusal(Exception):
    """A failure a second attempt answers the same way."""


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    """The pause between attempts is real in a run and pointless in a test."""
    monkeypatch.setattr(model_call, "RETRY_PAUSE_S", 0)


# --- what counts as worth retrying ------------------------------------------

def test_transport_failures_are_retryable_and_answers_are_not():
    assert model_call.is_retryable(ReadTimeout("stalled"))
    assert not model_call.is_retryable(Refusal("cannot"))
    assert not model_call.is_retryable(ValueError("a bug in this repo"))


def test_both_exception_hierarchies_are_covered():
    """The SDK wraps most transport faults in `APITimeoutError`, but a stream
    that stalls inside `get_final_message()` has been observed raising
    `httpx.ReadTimeout` raw. Missing either one means the retry never fires on
    the failure it was written for."""
    for name in ("ReadTimeout", "APITimeoutError", "APIConnectionError",
                 "InternalServerError", "OverloadedError"):
        assert name in model_call.RETRYABLE, name


# --- the retry --------------------------------------------------------------

def test_a_clean_call_costs_one_attempt():
    calls = []

    def send():
        calls.append(1)
        return "the answer"

    assert model_call.attempt("render", send) == "the answer"
    assert len(calls) == 1, "a healthy call must not pay for the retry"


def test_a_stalled_call_is_retried_and_the_second_answer_stands():
    calls = []

    def send():
        calls.append(1)
        if len(calls) == 1:
            raise ReadTimeout("The read operation timed out")
        return "the answer"

    assert model_call.attempt("render", send, attempts=2) == "the answer"
    assert len(calls) == 2


def test_a_refusal_is_not_retried():
    """A schema error, a bad key and a bug in this repo are answered the same way
    twice, so they cost one attempt and no wait."""
    calls = []

    def send():
        calls.append(1)
        raise Refusal("cannot")

    with pytest.raises(Refusal):
        model_call.attempt("extraction", send, attempts=3)
    assert len(calls) == 1


def test_exhausting_the_attempts_raises_a_message_naming_the_bound():
    """The whole point of the wrapper. The SDK's own message is "The read
    operation timed out", which says nothing about how long was allowed or how
    many times it was tried -- and that is exactly what tells a stall apart from
    a slow answer."""
    def send():
        raise ReadTimeout("The read operation timed out")

    with pytest.raises(model_call.ModelCallError) as caught:
        model_call.attempt("render", send, attempts=2, timeout_s=420.0)
    error = caught.value
    assert error.leg == "render"
    assert error.attempts == 2
    assert error.timeout_s == 420.0
    assert isinstance(error.__cause__, ReadTimeout), "the original must survive"
    for expected in ("render", "2 attempts", "420s", "ReadTimeout"):
        assert expected in str(error), (expected, str(error))


def test_the_message_says_what_the_leg_actually_spent():
    """A bound of 400s reads as a timeout. On 2026-09-03 a leg that failed in
    0.05s was reported against its 400s bound and read as a slow API; it had
    never reached a socket. Elapsed beside the bound is what tells them apart."""
    def send():
        raise ReadTimeout("connection refused")

    with pytest.raises(model_call.ModelCallError) as caught:
        model_call.attempt("extraction", send, attempts=1, timeout_s=400.0)
    error = caught.value
    assert error.elapsed_s is not None
    assert error.elapsed_s < 1.0
    assert "bounded at 400s" in str(error)
    assert "spending 0.0s in total" in str(error)


def test_a_wrapped_cause_is_named_by_type_and_never_by_message():
    """`anthropic` turns every non-SDK exception raised while sending into
    `APIConnectionError("Connection error.")`, so a malformed header, a DNS
    failure and a TLS failure all read identically -- which is why 2026-09-03's
    root cause (an `httpx.Timeout` handed to an SDK that had moved to `httpx2`)
    was invisible. The cause's CLASS is the distinguishing fact.

    Its message is deliberately not carried: h11 puts the offending header value
    in the text verbatim, which for an auth header is the API key, and this
    string reaches the studio's error panel and the deck store."""
    class APIConnectionError(Exception):
        pass

    def send():
        try:
            raise TypeError("'Timeout' object cannot be interpreted as an integer")
        except TypeError as inner:
            raise APIConnectionError("Connection error.") from inner

    with pytest.raises(model_call.ModelCallError) as caught:
        model_call.attempt("extraction", send, attempts=1, timeout_s=400.0)
    message = str(caught.value)
    assert "APIConnectionError: Connection error." in message
    assert "root cause TypeError" in message
    assert "Timeout' object cannot be interpreted" not in message, (
        "the cause's message may carry a credential; only its type is safe")


def test_a_cause_of_the_same_class_is_not_restated():
    """The common case: the SDK wrapped nothing and `__cause__` is the exception
    the leg already names. Repeating it adds a clause and no fact."""
    def send():
        raise ReadTimeout("stalled")

    with pytest.raises(model_call.ModelCallError) as caught:
        model_call.attempt("render", send, attempts=1, timeout_s=420.0)
    assert "root cause" not in str(caught.value)


def test_a_passed_key_is_stripped_because_a_key_is_a_header_value(monkeypatch):
    """Whitespace does not make a credential wrong, it makes it UNSENDABLE:
    httpx refuses to build the header and the SDK reports the result as a
    connection error. `_load_dotenv` strips what it sets, so this covers the
    caller that read a key from somewhere that does not."""
    built = _fake_sdk(monkeypatch)
    model_call.bounded_client("a-key\n", timeout_s=60.0)
    assert built["api_key"] == "a-key"


def test_one_attempt_is_singular_in_the_message():
    def send():
        raise ReadTimeout("stalled")

    with pytest.raises(model_call.ModelCallError) as caught:
        model_call.attempt("ranking", send, attempts=1, timeout_s=60)
    assert "1 attempt," in str(caught.value)


def test_a_call_with_no_bound_of_ours_raises_what_it_caught():
    """`timeout_s=None` means this code did not set the bound, which is what
    every call site says when its client was injected. Wrapping there would put a
    sentence naming a bound and an attempt count that were somebody else's into a
    degrading pass's ledger, and a fabricated timeout is worse than a bare one."""
    def send():
        raise ReadTimeout("The read operation timed out")

    with pytest.raises(ReadTimeout):
        model_call.attempt("render", send, attempts=1)


def test_every_retry_is_logged_so_a_run_that_retried_can_be_told_apart(caplog):
    """The SDK's two retries are silent, which is the other half of why they are
    the wrong two: nothing downstream could tell a run that answered first time
    from one that answered on the third."""
    def send():
        raise ReadTimeout("The read operation timed out")

    with caplog.at_level(logging.WARNING, logger="deck.model_call"):
        with pytest.raises(model_call.ModelCallError):
            model_call.attempt("writing", send, attempts=3, timeout_s=240)
    text = caplog.text
    assert "attempt 1 of 3" in text
    assert "attempt 2 of 3" in text
    assert "gave up after 3 attempt" in text


def test_a_caller_can_record_the_retry_itself():
    seen = []

    def send():
        if not seen:
            raise ReadTimeout("stalled")
        return "ok"

    result = model_call.attempt(
        "render", send, attempts=2,
        on_retry=lambda leg, number, error: seen.append((leg, number,
                                                         type(error).__name__)),
    )
    assert result == "ok"
    assert seen == [("render", 1, "ReadTimeout")]


def test_attempts_below_one_still_run_once():
    """A caller that computed zero attempts wants no call made twice, not no call
    made at all -- returning None here would be a silent skip of a model leg."""
    calls = []
    assert model_call.attempt("ranking", lambda: calls.append(1) or "ok",
                              attempts=0) == "ok"
    assert len(calls) == 1


# --- the bound --------------------------------------------------------------

def _fake_sdk(monkeypatch):
    """Stand-ins for `anthropic` and `httpx`, recording what the client was built
    with. The real SDK is not installed on the interpreter this suite runs on,
    and installing it to assert two keyword arguments would be the wrong trade."""
    built = {}

    class Timeout:
        def __init__(self, read, connect=None):
            self.read, self.connect = read, connect

    class Anthropic:
        def __init__(self, api_key=None, timeout=None, max_retries=None):
            built.update(api_key=api_key, timeout=timeout,
                         max_retries=max_retries)

    anthropic = types.ModuleType("anthropic")
    anthropic.Anthropic = Anthropic
    httpx = types.ModuleType("httpx")
    httpx.Timeout = Timeout
    monkeypatch.setitem(sys.modules, "anthropic", anthropic)
    monkeypatch.setitem(sys.modules, "httpx", httpx)
    return built


def test_the_client_is_bounded_at_the_leg_s_own_read_timeout(monkeypatch):
    built = _fake_sdk(monkeypatch)
    model_call.bounded_client("a-key", timeout_s=420.0)
    assert built["api_key"] == "a-key"
    assert built["timeout"].read == 420.0
    assert built["timeout"].connect == model_call.CONNECT_TIMEOUT_S


def test_the_client_keeps_none_of_the_sdk_s_own_retries(monkeypatch):
    """`attempt` owns retrying, out loud. Leaving the SDK's two in place would
    multiply the bound by three and do it silently."""
    built = _fake_sdk(monkeypatch)
    model_call.bounded_client(None, timeout_s=60.0)
    assert built["max_retries"] == 0


def test_the_connect_bound_is_separate_from_the_read_bound(monkeypatch):
    """A handshake is a property of the network, not of the leg: the slowest leg
    here does not want a different handshake bound from the fastest."""
    built = _fake_sdk(monkeypatch)
    model_call.bounded_client(None, timeout_s=420.0, connect_timeout_s=5.0)
    assert built["timeout"].connect == 5.0
    assert built["timeout"].read == 420.0


def test_this_module_drags_no_sdk_onto_the_pure_path():
    """Importing it must cost nothing. `paper_extraction`, `bullet_ranking` and
    the rest import it at call time on a path the whole test suite reaches, and
    the hosted service has not been confirmed to carry `httpx` at all."""
    source = open(os.path.join(os.path.dirname(__file__), "..", "src",
                               "model_call.py"), encoding="utf-8").read()
    head = source.split("def bounded_client", 1)[0]
    for banned in ("import anthropic", "import httpx"):
        assert banned not in head, banned
