"""Tests for the Claude Design export prompt (item 3(e), C6).

Run with: python3 tests/test_design_export_prompt.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from design_export_prompt import assemble_design_export_prompt

DECK_HTML = (
    '<!doctype html><html><body>'
    '<section class="slide" data-slide="1"><h1>Cover</h1>'
    '<p>Investment of $275,000 this year.</p></section>'
    '</body></html>'
)


def test_the_current_html_is_carried_verbatim():
    prompt = assemble_design_export_prompt(DECK_HTML)
    assert DECK_HTML in prompt, "the export must carry the exact HTML it was given"


def test_the_prompt_names_claude_design_and_marks_itself_a_handoff():
    prompt = assemble_design_export_prompt(DECK_HTML)
    assert "Claude Design" in prompt
    assert "END OF DESIGN EXPORT PROMPT" in prompt


def test_an_edited_deck_produces_an_export_with_the_edit_not_the_original():
    """The whole point of this export is that it reflects the deck as it
    currently stands, not the original generation, so an edited HTML string
    must produce a different export than the pre-edit one."""
    edited = DECK_HTML.replace("$275,000", "$310,000")
    assert "$310,000" in assemble_design_export_prompt(edited)
    assert "$310,000" not in assemble_design_export_prompt(DECK_HTML)


if __name__ == "__main__":
    test_the_current_html_is_carried_verbatim()
    test_the_prompt_names_claude_design_and_marks_itself_a_handoff()
    test_an_edited_deck_produces_an_export_with_the_edit_not_the_original()
    print("All tests passed.")
