"""Tests for the free-text edit interpreter.

The interpreter's one network dependency (the Anthropic API) is injected as a
stub client returning canned JSON, so these tests exercise the request shape and
the response parsing (plain JSON, fenced JSON, an unresolved reply) without a key
or a network call. The stub mirrors the one in test_deck_renderer.py.

Run with: python3 tests/test_html_edit_interpreter.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from html_edit_interpreter import (
    DEFAULT_MODEL,
    PREFERENCES_HEADING,
    SYSTEM_PROMPT,
    interpret_edit,
)

DECK = (
    "<!doctype html>\n<html><head><style>.slide{}</style></head><body>\n"
    '<section class="slide slide--dark" data-slide="1">\n'
    "  <h1>Ridgeline Overview</h1>\n"
    "</section>\n"
    '<section class="slide" data-slide="2">\n'
    "  <p>Revenue was <b>$40M</b> last year. We recieve the report weekly.</p>\n"
    "</section>\n"
    "</body></html>\n"
)


class _Block:
    def __init__(self, type_, text=""):
        self.type = type_
        self.text = text


class _Message:
    def __init__(self, blocks):
        self.content = blocks


class _Stream:
    def __init__(self, message):
        self._message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._message


class _StubClient:
    """Records the kwargs it was called with and returns canned content."""

    def __init__(self, blocks):
        self._blocks = blocks
        self.calls = []
        self.messages = self

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        return _Stream(_Message(self._blocks))


def _client(text):
    return _StubClient([_Block("text", text)])


def test_interpret_returns_normalized_edits():
    reply = (
        '{"edits": ['
        '{"slide": 2, "source": "$40M", "replacement": "$42M", "note": "revenue"}'
        '], "unresolved": ""}'
    )
    client = _client(reply)
    plan = interpret_edit(DECK, "change the revenue to $42M", client=client)
    assert plan["unresolved"] == ""
    assert len(plan["edits"]) == 1
    e = plan["edits"][0]
    assert e == {"slide": 2, "source": "$40M", "replacement": "$42M", "note": "revenue"}


def test_interpret_handles_multiple_edits():
    reply = (
        '{"edits": ['
        '{"slide": 2, "source": "$40M", "replacement": "$42M", "note": "figure"},'
        '{"slide": 2, "source": "recieve", "replacement": "receive", "note": "typo"}'
        ']}'
    )
    plan = interpret_edit(DECK, "fix the figure and the typo", client=_client(reply))
    assert [e["source"] for e in plan["edits"]] == ["$40M", "recieve"]
    assert plan["unresolved"] == ""


def test_interpret_strips_markdown_fences():
    # A model that wraps its JSON in a ```json fence still parses cleanly.
    reply = (
        "```json\n"
        '{"edits": [{"slide": 2, "source": "recieve", "replacement": "receive"}]}\n'
        "```"
    )
    plan = interpret_edit(DECK, "fix the typo", client=_client(reply))
    assert len(plan["edits"]) == 1
    assert plan["edits"][0]["replacement"] == "receive"
    # note defaults to "" when the model omits it
    assert plan["edits"][0]["note"] == ""


def test_interpret_surfaces_unresolved_with_no_edits():
    # A styling request the interpreter cannot express as content swaps: no edits,
    # an explanation the UI shows the reviewer.
    reply = (
        '{"edits": [], "unresolved": "That is a styling change (font size); use the '
        'Claude Design handoff instead."}'
    )
    plan = interpret_edit(DECK, "make the headline bigger", client=_client(reply))
    assert plan["edits"] == []
    assert "styling change" in plan["unresolved"]


def test_interpret_extracts_json_around_stray_prose():
    # A stray sentence around the JSON object is tolerated (balanced-brace fallback).
    reply = (
        "Here is the plan you asked for:\n"
        '{"edits": [{"slide": 2, "source": "$40M", "replacement": "$41M"}]}\n'
        "Let me know if that works."
    )
    plan = interpret_edit(DECK, "nudge revenue", client=_client(reply))
    assert plan["edits"][0]["replacement"] == "$41M"


def test_interpret_sends_deck_and_instruction_and_defaults():
    client = _client('{"edits": []}')
    interpret_edit(DECK, "do nothing meaningful", client=client)
    call = client.calls[0]
    assert call["model"] == DEFAULT_MODEL, call["model"]
    assert call["system"] == SYSTEM_PROMPT
    assert call["thinking"] == {"type": "adaptive"}
    user = call["messages"][0]["content"]
    assert "do nothing meaningful" in user, "the instruction rides in the user message"
    assert "Ridgeline Overview" in user, "the current deck HTML rides along too"


def test_standing_preferences_ride_in_the_system_prompt():
    # A run carrying active preferences must send them to the model so replacements
    # comply. With preferences, the system prompt grows the notes; without, it is
    # byte-identical to the base prompt.
    client = _client('{"edits": []}')
    notes = ["spell out numbers under ten", "keep client names in title case"]
    interpret_edit(DECK, "tidy slide 2", client=client, preferences=notes)
    system = client.calls[0]["system"]
    assert system.startswith(SYSTEM_PROMPT)
    assert PREFERENCES_HEADING in system
    for note in notes:
        assert f"- {note}" in system


def test_empty_or_blank_preferences_leave_prompt_byte_identical():
    for prefs in (None, [], ["", "   "]):
        client = _client('{"edits": []}')
        interpret_edit(DECK, "tidy slide 2", client=client, preferences=prefs)
        assert client.calls[0]["system"] == SYSTEM_PROMPT, prefs


def test_interpret_empty_instruction_raises():
    try:
        interpret_edit(DECK, "   ", client=_client('{"edits": []}'))
    except ValueError as e:
        assert "instruction" in str(e)
    else:
        raise AssertionError("expected ValueError on an empty instruction")


def test_interpret_unparseable_reply_raises():
    try:
        interpret_edit(DECK, "change something", client=_client("not json at all"))
    except ValueError as e:
        assert "parse" in str(e)
    else:
        raise AssertionError("expected ValueError on an unparseable reply")


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"PASS  {test.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL  {test.__name__}: {e}")

    if failures:
        print(f"\n{failures} test(s) failed")
        sys.exit(1)
    print(f"\nAll {len(tests)} tests passed")
