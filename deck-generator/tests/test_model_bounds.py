"""Every one of the five model legs declares its own bound, and none of them
retries a client its caller injected.

`test_model_call` holds the wrapper's contract. This file holds the CALLERS', and
the difference is the lesson E11 Stage 2g already taught this repo once: a
mechanism that is only tested at its own seam can be switched off in every place
that uses it and every test stays green. So these reach the five call sites by
name.

Two properties, and they are the two that could regress silently:

  * each leg names a `DEFAULT_TIMEOUT_S` and a `DEFAULT_ATTEMPTS` of its own,
    sized to that leg. One number for all five would either let the 3-second
    ranking pass hang for minutes or kill every 229-second render.
  * an injected client gets exactly ONE attempt. The transport belongs to
    whoever built the client, retrying somebody else's fake is not this code's
    call, and it is the property that kept 1451 existing tests' call counts
    unchanged when the retry arrived.
"""

import os
import sys

from source_span import PAPER, Opportunity

# The opportunity a reading is FOR (item 15). These tests are about a
# leg's bound rather than about provenance, so they name a stand-in once.
OPPORTUNITY = Opportunity(id="OPP-BOUNDS")
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import bullet_ranking  # noqa: E402
import deck_renderer  # noqa: E402
import html_edit_interpreter  # noqa: E402
import paper_extraction  # noqa: E402
import model_call  # noqa: E402
import paper_writing  # noqa: E402

# Named exactly as `httpx.ReadTimeout` is: `model_call.RETRYABLE` matches on the
# class NAME, so this is a retryable failure as far as every leg is concerned.
# Anything that DID retry an injected client would call these fakes twice.
class ReadTimeout(Exception):
    pass


LEGS = (
    ("extraction", paper_extraction),
    ("writing", paper_writing),
    ("ranking", bullet_ranking),
    ("render", deck_renderer),
    ("edit interpreter", html_edit_interpreter),
)


@pytest.mark.parametrize("name,module", LEGS, ids=[leg for leg, _m in LEGS])
def test_each_leg_declares_its_own_bound(name, module):
    assert isinstance(module.DEFAULT_TIMEOUT_S, float), name
    assert module.DEFAULT_TIMEOUT_S > 0, name
    assert isinstance(module.DEFAULT_ATTEMPTS, int), name
    assert module.DEFAULT_ATTEMPTS >= 1, name


def test_the_bounds_are_ordered_the_way_the_legs_are():
    """Sized against the measured live run of 2026-09-02: ranking 3s, writing
    59s, extraction 102s, render 229s. A bound that did not follow that ordering
    would be a bound copied from another leg."""
    assert (bullet_ranking.DEFAULT_TIMEOUT_S
            < paper_writing.DEFAULT_TIMEOUT_S
            <= paper_extraction.DEFAULT_TIMEOUT_S
            < deck_renderer.DEFAULT_TIMEOUT_S)


def test_no_leg_is_bounded_at_or_above_the_sdk_default():
    """600 seconds is what a bare client inherits, so a bound at 600 is not a
    bound. Every leg has to come in under it or the whole exercise is a no-op."""
    for name, module in LEGS:
        assert module.DEFAULT_TIMEOUT_S < 600, name


def test_the_render_bound_clears_the_slowest_measured_render():
    """230.6s, 243.9s and 212.8s were the three clean renders timed on
    2026-09-02. A bound anywhere near those would cut off healthy runs, which is
    a far worse failure than the stall it was meant to catch."""
    assert deck_renderer.DEFAULT_TIMEOUT_S > 244 * 1.5


class _RaisingCreate:
    """A non-streaming client whose one call stalls. Counts its calls."""

    def __init__(self):
        self.calls = 0
        self.messages = self

    def create(self, **kwargs):
        self.calls += 1
        raise ReadTimeout("The read operation timed out")


class _RaisingStream:
    """A streaming client whose stream stalls. Counts its calls."""

    def __init__(self):
        self.calls = 0
        self.messages = self

    def stream(self, **kwargs):
        self.calls += 1
        raise ReadTimeout("The read operation timed out")


def test_an_injected_extraction_client_is_called_once():
    client = _RaisingCreate()
    with pytest.raises(ReadTimeout):
        paper_extraction.extract("A paper that states things.",
                                 ["today_pain_points"], client=client,
                                 document=PAPER, opportunity=OPPORTUNITY)
    assert client.calls == 1


def test_an_injected_writing_client_is_called_once():
    client = _RaisingCreate()
    with pytest.raises(ReadTimeout):
        paper_writing.write("A paper.", "A description.",
                            [paper_writing.PATHS[0]], client=client)
    assert client.calls == 1


def test_an_injected_ranking_client_is_called_once():
    client = _RaisingCreate()
    with pytest.raises(ReadTimeout):
        bullet_ranking.rank({"today": ["one bullet", "another bullet"]},
                            client=client)
    assert client.calls == 1


def test_an_injected_render_client_is_called_once():
    client = _RaisingStream()
    with pytest.raises(ReadTimeout):
        deck_renderer.render_deck_html("A prompt.", client=client)
    assert client.calls == 1


def test_an_injected_edit_client_is_called_once():
    client = _RaisingStream()
    with pytest.raises(ReadTimeout):
        html_edit_interpreter.interpret_edit("<p>a deck</p>", "make it shorter",
                                             client=client)
    assert client.calls == 1


def test_an_injected_clients_failure_reaches_the_caller_unwrapped():
    """`ModelCallError` names a bound this code did not set, so a leg running on
    somebody else's client raises what that client raised. The two degrading
    passes record whatever arrives either way; wrapping it here would put a
    fabricated timeout in their ledgers."""
    client = _RaisingCreate()
    with pytest.raises(ReadTimeout):
        bullet_ranking.rank({"today": ["one", "two"]}, client=client)


def test_a_leg_asked_for_extra_attempts_on_an_injected_client_still_makes_one():
    """The rule is about WHO OWNS THE TRANSPORT, not about the argument: an
    explicit `attempts` cannot talk a leg into retrying a caller's own client."""
    client = _RaisingCreate()
    with pytest.raises(ReadTimeout):
        bullet_ranking.rank({"today": ["one", "two"]}, client=client, attempts=5)
    assert client.calls == 1


def test_the_extraction_ceiling_clears_the_slowest_measured_completion():
    """Truncation is the failure this ceiling exists to avoid, and it is not a
    short answer: `_answer` refuses an incomplete JSON body outright, so the pass
    contributes nothing and the packet drops to whatever the parsers alone found.

    Two faithful calls on 2026-09-03, same 29,202-character WTG paper, same schema
    and adaptive thinking, returned `end_turn` at 15,078 and 11,545 output tokens.
    At the old 16000 the slower of those spent 94% of the ceiling, which is not
    headroom, and a paper at the top of the documented 15KB-48KB range had none.
    """
    assert paper_extraction.DEFAULT_MAX_TOKENS >= 15078 * 1.5


def test_the_extraction_bound_clears_the_slowest_measured_extraction():
    """107.8s and 216.3s were those same two calls. The bound this replaced was
    300s, set against a single 102s sample, which the 216.3s run came within a
    third of."""
    assert paper_extraction.DEFAULT_TIMEOUT_S > 216.3 * 1.5


# ---------------------------------------------------------------------------
# A RENDER THAT WAS CUT OFF (2026-09-13).
#
# The extraction pass answers JSON, so a truncation there fails to parse and
# raises on its own. This leg answers HTML, and a truncated HTML document is
# still a document: it parses, it renders, it saves, and a deck missing its last
# slides looks exactly like a deck that was meant to have fewer. Since item 15 a
# deck's slide count is the reviewer's and has no ceiling, while the render's own
# ceiling stays where it was sized for six slides.
# ---------------------------------------------------------------------------

class _Answered:
    """A client whose one call answers, with whatever stop reason it is given."""

    def __init__(self, stop_reason, output_tokens=1000, text="<html>a deck</html>"):
        self._message = type("_M", (), {
            "stop_reason": stop_reason,
            "usage": type("_U", (), {"output_tokens": output_tokens})(),
            "content": [type("_B", (), {"type": "text", "text": text})()],
        })()
        self.messages = self

    def stream(self, **kwargs):
        message = self._message

        class _Stream:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get_final_message(self):
                return message

        return _Stream()


def test_a_render_cut_off_at_the_ceiling_is_refused_rather_than_written():
    with pytest.raises(deck_renderer.RenderTruncated) as raised:
        deck_renderer.render_deck_html(
            "a prompt", client=_Answered("max_tokens", output_tokens=64000))

    error = raised.value
    assert error.stop_reason == "max_tokens"
    assert error.output_tokens == 64000
    assert error.max_tokens == deck_renderer.DEFAULT_MAX_TOKENS
    assert error.characters > 0


def test_the_refusal_carries_the_numbers_the_ceiling_would_be_sized_against():
    """Against a guess is how the extraction ceiling nearly got moved this
    morning, and the discipline is the same one: the record of the failure IS
    the measurement."""
    with pytest.raises(deck_renderer.RenderTruncated) as raised:
        deck_renderer.render_deck_html(
            "a prompt", client=_Answered("max_tokens", output_tokens=64000))

    message = str(raised.value)
    assert "output_tokens=64000" in message
    assert f"max_tokens={deck_renderer.DEFAULT_MAX_TOKENS}" in message
    assert "html_chars=" in message


def test_any_stop_reason_that_is_not_completion_is_refused():
    """`refusal` with zero output tokens is a shape this leg has actually
    produced (2026-09-02), and it writes an empty deck rather than raising."""
    for stop_reason in ("max_tokens", "refusal", "something_new"):
        with pytest.raises(deck_renderer.RenderTruncated):
            deck_renderer.render_deck_html(
                "a prompt", client=_Answered(stop_reason))


def test_a_completed_render_is_returned_as_it_always_was():
    html = deck_renderer.render_deck_html(
        "a prompt", client=_Answered(deck_renderer.COMPLETE))
    assert "a deck" in html


def test_a_response_stating_no_stop_reason_is_not_judged():
    """Every injected client in this suite is a stand-in with a `content` list,
    and a fake that never set a stop reason is not a model that answered badly."""
    bare = type("_C", (), {})()
    bare.messages = bare
    bare.stream = lambda **kwargs: type("_S", (), {
        "__enter__": lambda self: self,
        "__exit__": lambda self, *exc: False,
        "get_final_message": lambda self: type("_M", (), {
            "content": [type("_B", (), {"type": "text", "text": "<p>ok</p>"})()],
        })(),
    })()
    assert "ok" in deck_renderer.render_deck_html("a prompt", client=bare)


def test_the_truncation_is_not_the_error_that_tells_a_reviewer_to_re_run():
    """`ModelCallError` means a leg gave up after its attempts and its bound, and
    its remediation says to re-run because a stalled stream is usually not
    stalled twice. A truncation is the opposite: the call completed and ran out
    of room, so a re-run reaches the same place seven minutes later."""
    assert not issubclass(deck_renderer.RenderTruncated, model_call.ModelCallError)
