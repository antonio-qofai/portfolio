"""One bounded Anthropic client, and one deliberate retry, for every model leg.

Why this module exists. Five call sites in this pipeline build a model client
(`deck_renderer`, `paper_extraction`, `paper_writing`, `bullet_ranking`,
`html_edit_interpreter`) and until 2026-09-02 every one of them built a bare
`anthropic.Anthropic(api_key=...)`. A bare client inherits the SDK's defaults:
a 600-second read timeout and two silent retries. So a stalled stream was
invisible for ten minutes and a run that kept stalling could burn half an hour
before raising anything. A measured live proposal run is about 397 seconds end
to end, of which the render is 229; the four isolated render calls timed on
2026-09-02 came in at 230.6s, 243.9s, 212.8s and one that stalled mid-stream
past the SDK's own 600-second read and raised `httpx.ReadTimeout`. One observed
UI run took 910 seconds, a clean run plus roughly 8.5 minutes of stall.

Two things follow, and this module is both of them.

ONE BOUND PER LEG, NOT ONE FOR ALL FIVE. The legs are not alike: the ranking
pass asks for 2k tokens and answered in 3 seconds, the render asks for 64k and
takes nearly four minutes. A single number generous enough for the render would
let the ranking pass hang for minutes over a call that should cost seconds, and
a single number tight enough for the ranking pass would kill every render. So
each call site declares its own `DEFAULT_TIMEOUT_S` beside its own model and
`max_tokens`, sized against that leg's own measurement, and passes it here.

RETRY OUT LOUD. The SDK's two retries are the wrong two: they are silent, they
are applied to the same unbounded read, and nothing downstream can tell a run
that answered first time from one that answered on the third. `bounded_client`
therefore sets `max_retries=0` and takes the retry back, and `attempt` does it
where it can be seen -- each retry is logged with the leg, the attempt number
and the bound it tripped, and a leg that exhausts its attempts raises
`ModelCallError`, whose message names the attempts and the bound rather than
leaving the reviewer with the SDK's bare "The read operation timed out". The
two passes that swallow a failure by design (`second_pass.run`,
`bullet_ranking.run`) put that message in their ledger, so the sentence a
reviewer eventually reads says a model call was bounded and gave up rather than
implying the paper was thin.

Retry only what a retry can fix. `RETRYABLE` is transport and transient server
state: a stalled read, a dropped connection, an overloaded or 5xx reply. A
refusal, a schema error, a bad key and a bug in this repo are all answered the
same way twice, so they raise on the first attempt and cost nothing extra.
Matched by exception CLASS NAME rather than by class, deliberately: this module
is imported on the pure pipeline path and must not drag in `anthropic` or
`httpx` to name an exception it may never see. The names cover both hierarchies
because both reach these call sites -- the SDK wraps most transport faults in
`APITimeoutError`, but a stream that stalls inside `get_final_message()` has
been observed raising `httpx.ReadTimeout` raw.

Nothing here is client-specific or project-specific. It holds no model name, no
prompt and no default duration of its own: every duration arrives from the leg
that owns it.
"""

import logging
import time

LOGGER = logging.getLogger("deck.model_call")

# How long a connection may take to establish, as opposed to how long an answer
# may take to arrive. Separate from the per-leg read bound because it is a
# property of the network rather than of the leg: no leg wants to wait minutes
# for a TCP handshake, and the slowest leg here does not want a different
# handshake bound from the fastest.
CONNECT_TIMEOUT_S = 15.0

# The transport and transient-server failures a second attempt can actually fix,
# by exception class name. See the module docstring for why it is by name.
RETRYABLE = frozenset({
    # httpx, raw: a stream that stalls or a connection that drops mid-read.
    "ReadTimeout", "ConnectTimeout", "WriteTimeout", "PoolTimeout",
    "ReadError", "WriteError", "ConnectError", "RemoteProtocolError",
    # the SDK's own wrappers, and the server states that are true for a moment.
    "APITimeoutError", "APIConnectionError", "APIConnectionTimeoutError",
    "InternalServerError", "OverloadedError", "RateLimitError",
    "ServiceUnavailableError",
})

# Between a failed attempt and the next one. Short, and it is not a backoff
# schedule: the failure this exists for is a stalled stream, where the wait
# already happened -- the leg has by then spent its whole read bound -- so a
# long sleep on top of it only adds to what the reviewer is watching.
RETRY_PAUSE_S = 2.0


def _spent(elapsed_s):
    """", spending 0.1s in total" -- the half of the sentence that separates a
    stall from a call that never left the process.

    A bound of 400s reads as a timeout, so a failure that took 0.1s against it
    was reported on 2026-09-03 as though the API had been slow. It had not: the
    key carried a trailing newline, httpx refused to build the header, and the
    request never reached a socket. Elapsed against the bound is what says so.
    """
    if elapsed_s is None:
        return ""
    return f", spending {elapsed_s:.1f}s in total"


def _root_cause(error):
    """"; root cause LocalProtocolError" where the SDK wrapped something else.

    TYPE NAME ONLY, AND DELIBERATELY. `anthropic` turns every non-SDK exception
    raised while sending into `APIConnectionError("Connection error.")`, so DNS
    failure, a TLS failure and a malformed auth header are indistinguishable in
    the message a reviewer reads. The class of the thing underneath tells them
    apart. Its MESSAGE is not safe to carry: h11 puts the offending header value
    in it verbatim, which for an auth header is the API key, and this string is
    rendered in the studio's error panel and stored with the run.
    """
    cause = getattr(error, "__cause__", None)
    if cause is None or type(cause) is type(error):
        return ""
    return f"; root cause {type(cause).__name__}"


class ModelCallError(RuntimeError):
    """A model leg that gave up, naming what was tried rather than what broke.

    `str()` is written to be read by the reviewer who is looking at it, because
    it is: it reaches the studio's error panel through `_pipeline_result` and the
    two degrading passes' ledgers verbatim. The SDK's own message ("The read
    operation timed out") says nothing about how long was allowed or how many
    times it was tried, which is precisely what distinguishes a stall from a
    slow answer.

    The attributes are kept for a caller that wants to branch on them rather
    than parse the sentence: `leg` names the call site, `attempts` how many were
    spent, `timeout_s` the per-attempt bound, and `__cause__` the last real
    exception.
    """

    def __init__(self, leg, attempts, timeout_s, error, *, elapsed_s=None):
        self.leg = leg
        self.attempts = attempts
        self.timeout_s = timeout_s
        self.error = error
        self.elapsed_s = elapsed_s
        plural = "attempt" if attempts == 1 else "attempts"
        super().__init__(
            f"the {leg} model call did not complete in {attempts} {plural}, "
            f"each bounded at {timeout_s:g}s{_spent(elapsed_s)} "
            f"({type(error).__name__}: {error}{_root_cause(error)})"
        )


def is_retryable(error):
    """Whether a second attempt at this could plausibly answer differently."""
    return type(error).__name__ in RETRYABLE


def bounded_client(api_key=None, *, timeout_s, connect_timeout_s=CONNECT_TIMEOUT_S):
    """An `anthropic.Anthropic` bounded at `timeout_s` per read, with no retries
    of its own.

    `max_retries=0` is the point of the function as much as the timeout is: the
    SDK's retries are silent and `attempt` below owns retrying instead, so a run
    that retried says so. Both imports are function-level, matching every other
    place in this repo that reaches the SDK, so importing this module costs the
    pure pipeline nothing and the hosted path does not need `httpx` at import
    time.

    A passed key is stripped, because a key is a header VALUE and whitespace
    makes one unsendable rather than merely wrong: httpx hands an illegal header
    to h11, h11 raises, and the SDK reports that as
    `APIConnectionError("Connection error.")` in about a tenth of a second,
    which reads as a network fault. This covers a caller that passes a key it
    read itself; a key left to the SDK to read from the environment is
    normalised by the entry point that owns the environment (`ui.app`), since
    this function never sees it.
    """
    import anthropic
    import httpx

    return anthropic.Anthropic(
        api_key=api_key.strip() if isinstance(api_key, str) else api_key,
        timeout=httpx.Timeout(timeout_s, connect=connect_timeout_s),
        max_retries=0,
    )


def attempt(leg, call, *, attempts=2, timeout_s=None, on_retry=None):
    """Run `call()`, retrying a transport failure `attempts - 1` times. -> its result.

    `call` takes no arguments and must build its own request each time, because a
    streamed request cannot be replayed: the render and the edit interpreter both
    open a stream inside the callable for exactly this reason.

    A retryable failure is logged and retried; anything else is raised as it
    stands, so a refusal, a schema error or a bug in this repo costs one attempt
    and no wait. When the last attempt fails, the original exception is wrapped
    in `ModelCallError` and chained (`raise ... from`), so a caller that wants
    the SDK's own exception still has it on `__cause__`.

    `timeout_s` is recorded, not applied: the bound lives on the client that
    `call` uses. It is passed so the message a reviewer reads can name it, and it
    is also what decides whether the wrapping happens at all. A caller that
    passes None is saying THIS CODE DID NOT SET THE BOUND -- which is what every
    call site says when its client was injected -- and in that case the original
    exception is raised untouched rather than wrapped in a sentence claiming a
    bound and an attempt count that were somebody else's. A fabricated timeout in
    a degrading pass's ledger would be worse than the bare SDK message this
    module exists to replace.

    `on_retry` is called as `(leg, number, error)` before each retry, for a
    caller that wants to record the retry somewhere of its own; the log entry
    happens either way.
    """
    attempts = max(1, int(attempts))
    started = time.monotonic()
    for number in range(1, attempts + 1):
        try:
            return call()
        except Exception as error:  # noqa: BLE001 -- re-raised below, always
            last = error
            if number >= attempts or not is_retryable(error):
                break
            LOGGER.warning(
                "%s model call attempt %d of %d failed (%s: %s); retrying",
                leg, number, attempts, type(error).__name__, error,
            )
            if on_retry is not None:
                on_retry(leg, number, error)
            time.sleep(RETRY_PAUSE_S)
    elapsed = time.monotonic() - started
    if timeout_s is not None and is_retryable(last):
        LOGGER.error("%s model call gave up after %d attempt(s) at %ss, "
                     "spending %.1fs in total: %s: %s%s",
                     leg, attempts, timeout_s, elapsed, type(last).__name__,
                     last, _root_cause(last))
        raise ModelCallError(leg, attempts, timeout_s, last,
                             elapsed_s=elapsed) from last
    raise last
