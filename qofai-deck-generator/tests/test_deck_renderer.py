"""Tests for the Deck Renderer.

The renderer's one network dependency (the Anthropic API) is injected as a
stub client, so these tests exercise the request shape, the text extraction,
and the fence stripping without a key or a network call.

Run with: python3 tests/test_deck_renderer.py
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from deck_renderer import (
    DEFAULT_MODEL,
    HOUSE_STYLE_BRIEF,
    HOUSE_STYLE_CSS,
    LOGO_MARK_CSS,
    LOGO_MARK_DATA_URI,
    STATUS_HOUSE_STYLE_BRIEF,
    STATUS_HOUSE_STYLE_CSS,
    STATUS_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    THEME_CSS,
    _strip_code_fences,
    prepend_house_style_brief,
    render_deck_html,
    strip_gap_flags,
    strip_house_style_brief,
)

# Every pinned stylesheet the renderer can emit, by deck type. The design rules
# below hold for ALL of them: there is one theme, so a rule that is true of one
# deck type and false of another is the defect these tests exist to catch.
_STYLESHEETS = {"proposal": HOUSE_STYLE_CSS, "status": STATUS_HOUSE_STYLE_CSS}
_BRIEFS = {"proposal": HOUSE_STYLE_BRIEF, "status": STATUS_HOUSE_STYLE_BRIEF}
_SYSTEM_PROMPTS = {"proposal": SYSTEM_PROMPT, "status": STATUS_SYSTEM_PROMPT}


def _declarations(css, selector):
    """Every declaration block for an exact `selector{...}` rule in `css`."""
    out = []
    needle = selector + "{"
    start = css.find(needle)
    while start != -1:
        open_brace = start + len(needle)
        out.append(css[open_brace:css.index("}", open_brace)])
        start = css.find(needle, open_brace)
    return out


def _strip_comments(css):
    """Drop `/* ... */` comments, so a rule assertion never matches prose."""
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


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


def test_strip_fences_removes_html_fence():
    fenced = "```html\n<!doctype html><p>x</p>\n```"
    assert _strip_code_fences(fenced) == "<!doctype html><p>x</p>", _strip_code_fences(fenced)


def test_strip_fences_removes_bare_fence():
    fenced = "```\n<!doctype html>\n```"
    assert _strip_code_fences(fenced) == "<!doctype html>", _strip_code_fences(fenced)


def test_strip_fences_leaves_unfenced_html_unchanged():
    html = "<!doctype html>\n<html></html>"
    assert _strip_code_fences(html) == html, _strip_code_fences(html)


def test_render_strips_em_dashes_from_output():
    # A build that emits an em dash is normalized before the deck is returned.
    # Dash normalization belongs to the house text gate (src/text_gate.py), which
    # makes the grammatical choice the spec asks for; a single dash takes a comma.
    client = _StubClient([_Block("text", "<!doctype html><p>Scope — Build</p>")])
    html = render_deck_html("P", client=client)
    assert "—" not in html, html
    assert "Scope, Build" in html, html


def test_render_applies_the_house_text_gate():
    # The whole gate is wired into the render leg, not just the dash rule: no
    # exclamation points, no bold inside a paragraph, no banned vocabulary.
    client = _StubClient([_Block("text", (
        "<!doctype html><p>We leverage the crew data! "
        "<strong>Nothing is billed.</strong></p>"
    ))])
    html = render_deck_html("P", client=client)
    assert "data!" not in html, html
    assert "leverage" not in html and "We use the crew data." in html, html
    assert "<strong>" not in html, html


def test_render_gate_cannot_touch_a_value_the_fidelity_guard_checks():
    # The gate is handed render_guard.protected_strings for this prompt, so a
    # packet-supplied milestone label is masked out of the vocabulary rules'
    # reach even when it contains a banned term.
    prompt = "milestones:\n  - id: M1\n    label: DEEP DIVE COMPLETE\n"
    client = _StubClient([_Block("text", (
        "<!doctype html><p>DEEP DIVE COMPLETE lands in week eight, "
        "and we deep dive into the crew data.</p>"
    ))])
    html = render_deck_html(prompt, client=client)
    assert "DEEP DIVE COMPLETE" in html, html
    assert "we research the crew data" in html, html


def test_render_returns_only_text_blocks_joined():
    # Non-text blocks (e.g. thinking) are ignored; text blocks are concatenated.
    client = _StubClient([
        _Block("thinking", "planning the layout"),
        _Block("text", "<!doctype html>\n"),
        _Block("text", "<html></html>"),
    ])
    html = render_deck_html("PROMPT BODY", client=client)
    assert html == "<!doctype html>\n<html></html>", html


def test_render_passes_content_prompt_verbatim_as_user_message():
    # A bare content prompt (no house-style header) reaches the model unchanged.
    client = _StubClient([_Block("text", "<!doctype html>")])
    render_deck_html("EXACT PROMPT TEXT", client=client)
    call = client.calls[0]
    assert call["messages"] == [{"role": "user", "content": "EXACT PROMPT TEXT"}], call["messages"]
    assert call["system"] == SYSTEM_PROMPT, "system prompt should be the format instruction"


def test_render_strips_house_style_header_before_sending():
    # The saved Claude Design prompt carries a house-style header; this leg gets
    # the house style via the system prompt, so the header is stripped and the
    # user message is the bare content — the deck Claude Code builds is unchanged.
    client = _StubClient([_Block("text", "<!doctype html>")])
    design_prompt = prepend_house_style_brief("EXACT PROMPT TEXT")
    assert design_prompt != "EXACT PROMPT TEXT"
    render_deck_html(design_prompt, client=client)
    call = client.calls[0]
    assert call["messages"] == [{"role": "user", "content": "EXACT PROMPT TEXT"}], call["messages"]


def test_house_style_brief_round_trips():
    # strip is the exact inverse of prepend, and strip is a no-op on bare content.
    content = "# Deck-wide fields\nclient_short: FBK\n\n## Slide 1 — Cover\n"
    assert strip_house_style_brief(prepend_house_style_brief(content)) == content
    assert strip_house_style_brief(content) == content


def test_render_defaults_to_opus_and_streams_with_adaptive_thinking():
    client = _StubClient([_Block("text", "<!doctype html>")])
    render_deck_html("P", client=client)
    call = client.calls[0]
    assert call["model"] == DEFAULT_MODEL, call["model"]
    assert call["thinking"] == {"type": "adaptive"}, call["thinking"]
    assert call["output_config"] == {"effort": "high"}, call["output_config"]
    assert call["max_tokens"] >= 16000, call["max_tokens"]


def test_render_model_override():
    client = _StubClient([_Block("text", "<!doctype html>")])
    render_deck_html("P", client=client, model="claude-sonnet-5")
    assert client.calls[0]["model"] == "claude-sonnet-5", client.calls[0]["model"]


# ------------------ slide 5 is a column of optional blocks ------------------
#
# 2026-09-23 (`build-plan-commercial-slide.md`). The retired layout was a GRID
# with one declared row per region, and the defect found on 2026-08-20 was a
# row count that disagreed with the children the spec emitted, which put the
# stretch on the wrong child. The adaptive deal sheet draws only the blocks its
# data states, so a fixed row count cannot be right for every deck. It is a
# flex column instead, which has no count to fall out of step.

_BLOCKS = ("deal-top", "invest", "returns", "deal-terms", "valuemap", "footnote")


def test_the_commercial_slide_is_a_flex_column_with_no_row_count():
    rule = re.search(r"\.comm\{[^}]*\}", HOUSE_STYLE_CSS).group(0)
    assert "flex-direction:column" in rule, rule
    assert "grid-template-rows" not in rule, rule


def test_a_lone_top_block_takes_the_full_width():
    assert ".deal-top > :only-child{grid-column:1 / -1}" in HOUSE_STYLE_CSS


def test_the_render_spec_names_every_block_and_draws_only_what_is_stated():
    for block in _BLOCKS:
        assert f"`.{block}`" in SYSTEM_PROMPT, block
    assert "ONLY the ones whose data the prompt carries" in SYSTEM_PROMPT
    # The contract `commercial_blocks.normalise` relies on.
    assert "keep them exactly" in SYSTEM_PROMPT


# ------------------------------- gap flags ---------------------------------
# The finished deck is saved clean of the "(unconfirmed, see gaps)" flag, but the
# `[MISSING: ...]` markers stay (a genuinely absent value the reviewer supplies).

_GAP_SPAN = '<span class="flag">(unconfirmed, see gaps)</span>'


def test_strip_gap_flags_removes_the_unconfirmed_flag_span():
    html = f"<p>Revenue was $40M {_GAP_SPAN} last year.</p>"
    out = strip_gap_flags(html)
    assert "(unconfirmed, see gaps)" not in out, out
    assert 'class="flag"' not in out, out
    # the value the flag annotated stays in place
    assert "Revenue was $40M" in out and "last year." in out, out


def test_strip_gap_flags_removes_a_bare_unwrapped_flag():
    html = "<p>EBITDA margin 18% (unconfirmed, see gaps)</p>"
    out = strip_gap_flags(html)
    assert "(unconfirmed, see gaps)" not in out, out
    assert "EBITDA margin 18%" in out, out


def test_strip_gap_flags_keeps_missing_markers():
    # A `[MISSING: ...]` marker is an absent value, not an unconfirmed one — it must
    # survive so the reviewer can still supply it on the deck.
    html = (f'<p>Owner: <span class="flag">[MISSING: engagement_lead]</span></p>'
            f'<p>Margin {_GAP_SPAN}</p>')
    out = strip_gap_flags(html)
    assert "[MISSING: engagement_lead]" in out, out
    assert "(unconfirmed, see gaps)" not in out, out


def test_strip_gap_flags_is_idempotent():
    html = f"<p>x {_GAP_SPAN} y</p>"
    once = strip_gap_flags(html)
    assert strip_gap_flags(once) == once, "second pass must change nothing"


def test_strip_gap_flags_noop_when_no_flags():
    html = '<p>Owner: <span class="flag">[MISSING: lead]</span></p>'
    assert strip_gap_flags(html) == html, "a deck with no gap flags is untouched"


# --------------------------- solid dark slides -----------------------------
# The cover/closing dark slides are a flat solid `background:var(--ink)` (Antonio,
# 2026-07-21 — the earlier radial-gradient read as a "weird blue gradient").

def _dark_slide_rule(css):
    """The body of the `.slide--dark{...}` declaration, so the assertions look at
    the actual rule and not an explanatory comment that names the old gradient."""
    marker = ".slide--dark{"
    start = css.index(marker) + len(marker)
    return css[start:css.index("}", start)]


def test_house_style_dark_slide_is_solid_no_gradient():
    rule = _dark_slide_rule(HOUSE_STYLE_CSS)
    assert "background:var(--ink)" in rule, rule
    assert "gradient" not in rule, "dark slide must be a solid fill, no gradient"


def test_status_house_style_dark_slide_is_solid_no_gradient():
    rule = _dark_slide_rule(STATUS_HOUSE_STYLE_CSS)
    assert "background:var(--ink)" in rule, rule
    assert "gradient" not in rule, "dark slide must be a solid fill, no gradient"


# ----------------- Casey's signed-off design refinements ------------------
# The six refinements approved at the 2026-07-21 check-in and implemented
# 2026-08-06 (NEXT-STEPS item 8). Each is easy to regress by editing one
# stylesheet and forgetting the other, which is exactly what these pin.


def test_every_deck_type_is_built_from_the_one_shared_theme():
    # ONE standardized theme, no options: a deck type contributes components, not
    # a palette. If a scaffold stops composing from THEME_CSS, the two deck types
    # can drift apart again.
    for deck_type, css in _STYLESHEETS.items():
        assert THEME_CSS in css, f"{deck_type} does not carry the shared theme"


def test_the_theme_defines_the_palette_exactly_once_per_deck():
    # One `:root` per stylesheet, so there is a single source for every token and
    # no second block quietly overriding it. (LOGO_MARK_CSS adds its own `:root`
    # for the logo asset only, and is appended separately by the system prompt.)
    for deck_type, css in _STYLESHEETS.items():
        assert css.count(":root{") == 1, f"{deck_type} declares {css.count(':root{')} palettes"


def test_both_deck_types_share_one_palette_and_one_type_scale():
    proposal, status = HOUSE_STYLE_CSS, STATUS_HOUSE_STYLE_CSS
    for token in ("--ink", "--paper", "--accent", "--pos", "--neg", "--on-dark"):
        pattern = re.compile(re.escape(token) + r":([^;]+);")
        assert pattern.search(proposal).group(1) == pattern.search(status).group(1), token
    for selector in (".slide", ".headline", ".summary", ".kicker", ".footer", ".cover-title"):
        assert _declarations(proposal, selector) == _declarations(status, selector), selector


def test_dark_cover_cream_body_dark_final_slide():
    # The standardized theme's structure: a cream body slide by default, and a
    # dark variant the cover and the closing slide opt into.
    for deck_type, css in _STYLESHEETS.items():
        assert "background:var(--paper)" in _declarations(css, ".slide")[0], deck_type
        assert "background:var(--ink)" in _dark_slide_rule(css), deck_type
    for deck_type, prompt in _SYSTEM_PROMPTS.items():
        assert 'class=\\"slide slide--dark\\"' in prompt.replace('"', '\\"'), deck_type


def test_arial_is_the_only_font_family_on_every_deck():
    # Arial everywhere (Casey, 2026-07-21), one family for body copy AND the
    # small-caps labels, and nothing loaded: no @font-face, no webfont import.
    for deck_type, css in _STYLESHEETS.items():
        body = _strip_comments(css)
        families = re.findall(r"--(?:sans|label|mono):([^;\n]+)", body)
        assert families, deck_type
        for family in families:
            assert "Arial" in family or family.strip() == "var(--sans)", (deck_type, family)
        assert "monospace" not in body, f"{deck_type} still asks for a monospace face"
        assert "@font-face" not in body and "@import" not in body, deck_type
        # Every font-family in the stylesheet resolves through those tokens.
        for value in re.findall(r"font-family:([^;}\n]+)", body):
            assert value.strip() in ("var(--sans)", "var(--label)"), (deck_type, value)


def test_the_briefs_ask_the_design_leg_for_arial_too():
    # The Claude Design leg gets prose, not CSS, so the font rule has to be stated
    # there or the two build legs diverge on typography.
    for deck_type, brief in _BRIEFS.items():
        assert "Arial everywhere" in brief, deck_type
        assert "monospace" not in brief, deck_type


def test_confidentiality_lives_only_in_the_footer():
    # Casey, 2026-07-21: the note appeared upper-right AND lower-left; keep the
    # footer one. The upper-right styling is gone, and both legs are told the note
    # is footer-only.
    for deck_type, css in _STYLESHEETS.items():
        assert ".conf{" not in css, f"{deck_type} still styles a header confidentiality note"
    for deck_type, prompt in _SYSTEM_PROMPTS.items():
        assert "confidentiality appears ONLY in " in prompt, deck_type
        assert "no confidentiality note" in prompt, deck_type
    for deck_type, brief in _BRIEFS.items():
        flat = " ".join(brief.split())   # the briefs are hard-wrapped prose
        assert "NO top-right confidentiality" in flat, deck_type
        assert "Confidentiality appears only" in flat or "confidentiality appears only" in flat, deck_type


def test_no_slide_carries_a_top_line_or_a_top_rule():
    # "You can get rid of that line at the top and the line and then the header.
    # Just start it with the eyebrow on the left." So: no rule under the cover's
    # top bar, no top bar at all on a body slide, and the eyebrow first.
    for deck_type, css in _STYLESHEETS.items():
        for rule in _declarations(css, ".topbar") + _declarations(css, ".slide--dark .topbar"):
            assert "border-bottom" not in rule, f"{deck_type} top bar still has a rule"
            assert "border-color" not in rule, f"{deck_type} top bar still has a rule"
        # The text wordmark that used to sit in that header line is gone with it.
        assert ".brandmark{" not in css, deck_type
    for deck_type, prompt in _SYSTEM_PROMPTS.items():
        assert "NON-COVER slides have NO top bar at all" in prompt, deck_type
        assert "a top rule" in prompt, deck_type


def test_gantt_phase_fills_are_one_blue_family_on_both_deck_types():
    # One color family per phase, shades of blue, and the shades live in the theme
    # so both Gantts draw from the same ramp. A phase's POSITION picks its shade,
    # so no content color name can reintroduce the multi-hue Gantt.
    ramp = re.findall(r"--phase-(\d):(#[0-9a-f]{6})", THEME_CSS)
    assert len(ramp) >= 3, ramp
    for _, hex_color in ramp:
        r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
        assert b > r and b >= g, f"phase color {hex_color} is not a blue"
    for deck_type, css in _STYLESHEETS.items():
        for step, _ in ramp:
            assert f".bar.phase-{step}{{" in css, (deck_type, step)
        # No hue-named bar fill survives on either deck type.
        assert not re.search(r"\.bar\.(?:cat-|)(?:green|maroon|pink|navy|red|gold)", css), deck_type
    for deck_type, prompt in _SYSTEM_PROMPTS.items():
        assert "phase-" in prompt, deck_type
        assert "bar--p1" not in prompt and "cat-{color}" not in prompt, deck_type


# ---------------------------------------------------------------------------
# Slide 4, the timeline. Both defects Casey raised on the 2026-08-20 sync are
# pinned here: the verbose description and the header row that wrapped onto
# itself. Measured on `decks/WTG/claude code/output-2.html` before the fix —
# header cells three and four on a `top` 13px below cells one and two, cell
# three starting at x=628 while cell two ended at x=690.
# ---------------------------------------------------------------------------


def test_every_gantt_header_cell_is_pinned_to_one_grid_row_on_both_deck_types():
    # A header cell placed by COLUMN alone goes wherever auto-placement puts it,
    # and a column two cells share sends the second onto an implicit second row:
    # the header then wraps under itself and paints over the cells before it.
    # The status Gantt has always pinned `grid-row:1` on its corner and every
    # `.g-col`; the proposal one was the deck type left without it.
    proposal = _strip_comments(HOUSE_STYLE_CSS)
    for selector in (".gantt-head .corner", ".gantt-head .wk"):
        blocks = _declarations(proposal, selector)
        assert blocks, selector
        for block in blocks:
            assert "grid-row:1" in block, (selector, block)
    # And the spec asks the builder for the same pin, so a cell is never left to
    # auto-placement in the first place.
    assert "grid-row:1" in SYSTEM_PROMPT
    assert "THE HEADER IS EXACTLY ONE ROW" in SYSTEM_PROMPT
    assert "grid-row:1" in STATUS_SYSTEM_PROMPT


def test_the_gantt_header_declares_the_same_tracks_as_its_rows():
    # The head and the rows must declare the SAME grid, or a header cell stops
    # sitting over the bar it labels. This is what makes "line up with the bars"
    # a property of the stylesheet rather than of one render.
    css = _strip_comments(HOUSE_STYLE_CSS)
    tracks = []
    for selector in (".gantt-head", ".trow"):
        blocks = _declarations(css, selector)
        assert len(blocks) == 1, selector
        found = re.search(r"grid-template-columns:([^;}]+)", blocks[0])
        assert found, selector
        tracks.append(found.group(1).strip())
    assert tracks[0] == tracks[1], tracks
    # The two halves of the bars' no-truncation rule, which must survive any
    # change here: minmax(0,1fr) week tracks and a trailing gutter track.
    assert "minmax(0,1fr)" in tracks[0], tracks[0]
    assert tracks[0].rstrip().endswith("minmax(64px,112px)"), tracks[0]
    assert "min-width:max-content" in _declarations(css, ".bar")[0]


def test_the_spec_says_what_the_timeline_description_is_for():
    # Casey, 2026-08-20: "the timeline description text was too verbose". The
    # role used to be filled by joining the per-phase caveat notes, so slide 4's
    # description restated slide 2's build strip and the Gantt right under it.
    # The data fix is in `data_source_adapter._axis_columns`' sibling
    # `plan_summary`; this pins the spec half — the builder is told what the
    # element is for, so it neither pads it nor rebuilds the old concatenation.
    assert "WHAT `.summary` IS FOR" in SYSTEM_PROMPT
    assert "never let it become a concatenation of the per-phase lines" in SYSTEM_PROMPT
    for phrase in ("phase labels", "phase notes", "workstream detail",
                   "milestone text"):
        assert phrase in SYSTEM_PROMPT, phrase


def test_a_phase_only_row_names_itself_once_not_twice():
    # Same slide, same sync: "minor alignment issues on the timeline slide". A
    # row that IS its phase used to print the phase text in its `.rlabel` AND
    # inside its bar, which put the same words on the slide twice and, because a
    # bar grows rightward to contain its own text, pushed the bar past the axis
    # column it was supposed to sit under.
    assert "carries NO TEXT AT ALL" in SYSTEM_PROMPT
    assert "do not repeat its label" in SYSTEM_PROMPT
    assert "carries that same phase text" not in SYSTEM_PROMPT
    # The no-truncation rule for a real workstream's detail is untouched.
    assert "never shortened to fit" in SYSTEM_PROMPT
    assert "grows rightward to contain" in SYSTEM_PROMPT


def test_both_logos_are_unchanged():
    # The footer mark and the cover's header mark both stay exactly as they were
    # (Antonio, 2026-08-06 — the qofai-text PNG swap was dropped). One asset,
    # tinted per slide, painted from an empty span in the cover top bar and in
    # every body footer.
    assert LOGO_MARK_DATA_URI.startswith("data:image/svg+xml,"), LOGO_MARK_DATA_URI
    assert "logo-mark--lg" in LOGO_MARK_CSS and "logo-mark--sm" in LOGO_MARK_CSS
    assert ".footer .brand-foot" in LOGO_MARK_CSS
    for deck_type, prompt in _SYSTEM_PROMPTS.items():
        assert 'logo-mark logo-mark--lg' in prompt, deck_type
        assert 'logo-mark logo-mark--sm' in prompt, deck_type
        assert "never use a text wordmark" in prompt, deck_type


def test_commercial_terms_instructions_are_generic_rows_not_fixed_fields():
    # The deal is named rows (`terms_rows`), never the retired fixed boxes, and
    # the empty state names its marker so the builder never invents a term or
    # silently drops the block.
    for retired in ("qofai_investment", "client_upfront", "comp_schedule",
                    "commercial_rows", "downside_protection", "payment_mechanics",
                    "client_retention", "tbox"):
        assert retired not in SYSTEM_PROMPT, retired
    assert "terms_rows" in SYSTEM_PROMPT
    assert "term--empty" in SYSTEM_PROMPT
    assert "[MISSING: terms_rows]" in SYSTEM_PROMPT
    assert "Never invent a term" in SYSTEM_PROMPT
    # Status has no commercial section at all; untouched by this change.
    assert "terms_rows" not in STATUS_SYSTEM_PROMPT


def test_terms_css_has_an_empty_state_rule_and_no_retired_classes():
    assert ".term--empty{" in HOUSE_STYLE_CSS
    for retired in (".tbox", ".downside{", ".mechanics{", ".caption{"):
        assert retired not in HOUSE_STYLE_CSS, retired


# ---- the empty-state / marker-preservation collision (2026-08-12) ----
#
# The bug this section exists to prevent, in full, because a one-string
# assertion would only record the fix:
#
# A system prompt carries a global CONTENT RULE ("preserve every
# `[MISSING: ...]` marker verbatim and visibly"). A per-slide instruction then
# describes an EMPTY STATE for one role — what to draw when that role's value
# IS the marker — and enumerates the box's contents WITHOUT the marker
# ("containing only `<span class="flag">AWAITING ...</span>`"). The two
# instructions contradict; the narrower per-slide one wins; the rendered HTML
# has no marker in it; `render_guard.check_render_fidelity` raises before
# either file is written, so generating that deck always fails and writes
# nothing. That is a prompt defect, not a guard defect: the guard's invariant
# (a marker in the prompt reaches the HTML, no exceptions) is what keeps it
# from having to know template semantics, and one per-role exemption would
# mean a new one for every future empty state.
#
# Nothing downstream can catch this. Every renderer in the suite is a stub
# returning hand-written HTML that already contains the marker (see the
# docstring on `_stub_renderer_factory` in tests/test_deck_generator.py), so no
# existing test ever asks what a renderer OBEYING the real system prompt would
# emit. Only a live API call could answer that directly. What IS checkable
# without one is the property that made the two instructions incompatible in
# the first place, and it is a property of the prompt STRING: an instruction
# conditioned on a role's marker must also name that marker in the output it
# prescribes. Any new empty state that forgets to fails this test, whatever
# role, slide, or deck type it is added to.

_LINE_MARKER_RE = re.compile(r"\[MISSING: [^\]]+\]")
# An exclusivity word is only a problem when it governs the box's CONTENTS
# ("containing only X"), not when it constrains something else ("use only a
# dashed border"), so it must follow a containment verb closely.
_EXCLUSIVE_CONTENT_RE = re.compile(
    r"\b(?:contain(?:s|ing)?|carr(?:y|ies|ying)|hold(?:s|ing)?|with)\b[^.]{0,24}?"
    r"\b(only|nothing but|solely|exclusively)\b",
    re.IGNORECASE,
)


def _marker_conditioned_instructions(prompt):
    """Every prompt line that is triggered BY a `[MISSING: ...]` marker.

    That is: a conditional ("if ...", "Empty state: ...") whose trigger is a
    role's value being the marker. The global CONTENT RULE line is not one of
    these — it states the requirement rather than branching on it — and is
    excluded by having no conditional trigger.

    Yields ``(line, condition_marker, output_region)``. The output region is
    everything after the condition clause, which ends at the first `,` or `;`
    following the trigger marker. A conditional with no such break has no
    output region at all, which the caller reports as a failure rather than
    passing vacuously.
    """
    for line in prompt.split("\n"):
        match = _LINE_MARKER_RE.search(line)
        if not match:
            continue
        if not re.search(r"\bif\b|empty state", line, re.IGNORECASE):
            continue
        break_at = min(
            (i for i in (line.find(",", match.end()), line.find(";", match.end()))
             if i != -1),
            default=-1,
        )
        output_region = "" if break_at == -1 else line[break_at + 1:]
        yield line, match.group(0), output_region


def _names_the_marker(output_region, condition_marker):
    """Does this prescribed output require the condition's marker in the HTML?

    Two accepted forms, because the prompt uses both: the concrete literal
    (`[MISSING: commercial_rows]`) for a named role, and a back-reference
    ("render the marker") where the condition matched a generic
    `[MISSING: ...]` and the role is whatever the data carried.
    """
    if condition_marker in output_region:
        return True
    generic = condition_marker == "[MISSING: ...]"
    return generic and "the marker" in output_region.casefold()


def test_every_marker_conditioned_instruction_requires_the_marker_in_its_output():
    # Iterates the live SYSTEM_PROMPTS registry rather than a list written
    # here, so a third deck type is covered the day it is added.
    from deck_renderer import SYSTEM_PROMPTS

    checked = 0
    for deck_type, prompt in SYSTEM_PROMPTS.items():
        for line, marker, output in _marker_conditioned_instructions(prompt):
            checked += 1
            where = f"{deck_type} prompt, instruction: {line.strip()[:140]}"

            assert output.strip(), (
                f"{where}\n  -> this instruction branches on {marker} but "
                "prescribes no output after the condition clause; the check "
                "below cannot see what it emits"
            )

            # The defect itself: the marker triggers the branch but never
            # reaches the HTML, so check_render_fidelity raises on the render
            # and the deck is never written.
            assert _names_the_marker(output, marker), (
                f"{where}\n  -> triggered by {marker} but its prescribed "
                f"output never names that marker:\n     {output.strip()}\n"
                "     A renderer obeying this emits HTML with no marker in "
                "it, and render_guard.check_render_fidelity raises before "
                "anything is written. Carry the marker in the output too."
            )

            # A stricter form of the same defect: even when the marker is
            # named somewhere, an enumeration of the element's contents that
            # says "only ..." overrides it unless the marker is inside the
            # enumeration.
            exclusive = _EXCLUSIVE_CONTENT_RE.search(output)
            if exclusive:
                enumerated = output[exclusive.start(1):]
                assert _names_the_marker(enumerated, marker), (
                    f"{where}\n  -> its contents are enumerated exhaustively "
                    f"('{exclusive.group(1)}') and the enumeration excludes "
                    f"{marker}:\n     {enumerated.strip()}\n"
                    "     An exhaustive list wins over the global CONTENT "
                    "RULE, so the marker is dropped from the HTML."
                )

    # Guards the guard: if the extractor stops matching (a prompt reflow, a
    # changed bullet character), the loop above would pass by checking nothing.
    assert checked >= 2, (
        f"expected at least the two known marker-conditioned instructions "
        f"(a workstream's missing weeks, the commercial empty state); "
        f"matched {checked} — the extractor has drifted from the prompt"
    )


def test_the_empty_state_collision_reaches_the_render_fidelity_guard():
    # The other half of the same story, at the boundary that actually failed:
    # HTML built the way the OLD instruction described (the empty box holding
    # the AWAITING flag and nothing else) does not survive the guard, while
    # HTML built the way the fixed instruction describes does. This pins WHY
    # the prompt test above matters, without asserting on prompt wording.
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
    from render_guard import RenderFidelityError, check_render_fidelity

    prompt = "commercial_rows: [MISSING: commercial_rows]\n"
    shell = '<!doctype html><html><body><div class="tbox tbox--empty">{}</div></body></html>'
    flag_only = '<span class="flag">AWAITING COMMERCIAL TERMS INPUT</span>'
    with_marker = '<span class="flag">[MISSING: commercial_rows]</span>' + flag_only

    try:
        check_render_fidelity(prompt, shell.format(flag_only))
    except RenderFidelityError as exc:
        assert "[MISSING: commercial_rows]" in str(exc), exc
    else:
        raise AssertionError(
            "the flag-only empty state must trip the fidelity guard; if it no "
            "longer does, the guard was weakened rather than the prompt fixed"
        )

    report = check_render_fidelity(prompt, shell.format(with_marker))
    assert report["ok"], report


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
