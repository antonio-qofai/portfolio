"""Claude Design export prompt (item 3(e)).

The reverse direction of ``prompt_assembler``: that module turns a packet into
a prompt that generates a first deck. This module turns an already-rendered
deck's current HTML, edits included, into one self-contained prompt a
reviewer pastes into Claude Design to recreate that exact deck there for
further editing. The HTML is carried verbatim, never re-derived or
re-summarized, so the export can never disagree with what is on screen.
"""

INSTRUCTION = (
    "Recreate the deck below exactly as an editable design, reproducing every "
    "slide's content, layout, and styling as written. This is a deck already "
    "reviewed in Claude Code, including every edit applied since it was "
    "generated, so change nothing when you open it. Once it is open here, "
    "further edits are made directly on the slides.\n\n"
    "--- BEGIN CURRENT DECK HTML ---"
)

CLOSING_MARKER = (
    "--- END CURRENT DECK HTML ---\n"
    "END OF DESIGN EXPORT PROMPT. This hands off the deck exactly as it "
    "currently stands, edits included, for further editing in Claude Design."
)


def assemble_design_export_prompt(html):
    """Wrap ``html``, a deck's current-revision source, into a paste-ready
    Claude Design prompt that hands the deck off exactly as it stands."""
    return "\n".join([INSTRUCTION, html, CLOSING_MARKER])
