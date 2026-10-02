"""Tests for the deterministic text gate (src/text_gate.py).

Three things are being pinned here:

  1. The format rules do what the house spec says: em dashes out (with the
     substitution chosen by position), no bold inside a paragraph, no exclamation
     points, and every colon flagged for the reviewer rather than rewritten.
  2. The banned vocabulary is replaced deterministically, and the forms whose
     replacement would invent meaning are flagged for the reviewer instead.
  3. The hard constraint holds: no number, date, dollar figure, percentage,
     proper noun, or entity name differs between input and output. That one is
     proved on real rendered decks the tests own, under
     `tests/fixtures/text_gate/`, not only on hand-written snippets. Those
     fixtures come in two kinds, pre-gate and current, and the comment above
     `_real_decks()` says which kind proves what. They are not interchangeable.

Run with: python3 tests/test_text_gate.py
"""

import glob
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from text_gate import (
    AUDIENCE_EXTERNAL,
    AUDIENCE_INTERNAL,
    FORMAT_RULES,
    VOCABULARY_RULES,
    FactualChangeError,
    GateResult,
    apply_text_gate,
    assert_no_factual_change,
    factual_index,
    review_flags,
)
from text_gate import _text_nodes
from crude_fact_check import crude_fact_delta


def _gate(html, **kwargs):
    return apply_text_gate(html, **kwargs)


def _text(html, **kwargs):
    """Gate a bare fragment and hand back the copy, for readable assertions."""
    return _gate(f"<div><p>{html}</p></div>", **kwargs).html


# ---------------------------------------------------------------------------
# Rule tables are data.
# ---------------------------------------------------------------------------
def test_rules_are_a_parameterized_data_structure():
    # Adding a rule must be a data edit, never a code edit: both families are
    # tuples of records carrying their own pattern, replacement, and provenance.
    assert len(FORMAT_RULES) >= 4, FORMAT_RULES
    assert len(VOCABULARY_RULES) >= 10, len(VOCABULARY_RULES)
    for rule in VOCABULARY_RULES:
        assert rule.id and rule.pattern and rule.source and rule.note, rule
    for rule in FORMAT_RULES:
        assert rule.id and rule.handler and rule.source and rule.note, rule


def test_no_replacement_can_introduce_a_digit():
    # The import-time validator enforces this; assert it here too, because it is
    # the reason a vocabulary rewrite can never put a number on a deck.
    for rule in VOCABULARY_RULES:
        if rule.replacement is None:
            continue
        assert not any(ch.isdigit() for ch in rule.replacement), rule.id


# ---------------------------------------------------------------------------
# Format rule: em dashes.
# ---------------------------------------------------------------------------
def test_a_bracketing_pair_becomes_parentheses():
    out = _text("The rollout, which is fenced—two crews, not the fleet—starts in week four.")
    assert "—" not in out, out
    assert "(two crews, not the fleet) starts" in out, out


def test_a_single_em_dash_becomes_a_comma():
    assert "Scope, Build" in _text("Scope—Build"), _text("Scope—Build")
    out = _text("The margin gap is measured monthly—it is compared to the baseline.")
    assert "—" not in out and "monthly, it is compared" in out, out


def test_the_spaced_hyphen_appears_only_where_a_comma_would_pile_up():
    out = _text("Cloud warehouse, accounting connector,—and the field app.")
    assert "connector, - and the field app" in out, out


def test_en_dashes_are_left_alone():
    # An en dash is not an em dash and is not in the spec.
    out = _text("Weeks 1–8 cover the foundation.")
    assert "1–8" in out, out


def test_em_dash_entity_spellings_are_covered():
    out = _text("Scope &mdash; Build and weeks 1 &ndash; 8")
    assert "&mdash;" not in out and "&ndash;" in out, out


# ---------------------------------------------------------------------------
# Format rule: exclamation points.
# ---------------------------------------------------------------------------
def test_exclamation_points_become_periods():
    out = _text("The fleet is live!")
    assert "!" not in out, out
    assert "The fleet is live." in out, out


# ---------------------------------------------------------------------------
# Format rule: bold.
# ---------------------------------------------------------------------------
def test_bold_inside_a_paragraph_is_unbolded():
    html = "<p>An AI pricing engine with segment floors. <strong>Stop the margin bleed.</strong></p>"
    out = _gate(html).html
    assert "<strong>" not in out, out
    assert "Stop the margin bleed." in out, out


def test_bold_mid_sentence_is_unbolded():
    html = "<p>If margins do not improve, <b>QofAI earns nothing.</b></p>"
    out = _gate(html).html
    assert "<b>" not in out, out
    assert "QofAI earns nothing." in out, out


def test_bold_used_as_a_heading_is_left_alone():
    # Sole content of its block: a Gantt row label, not emphasis in prose.
    html = "<div class=\"row\"><b>Scoping and setup</b></div>"
    assert _gate(html).html == html, _gate(html).html


def test_bold_outside_a_prose_paragraph_is_left_alone():
    # The spec is about paragraphs. A header with a dated span beside it, a
    # bullet lead-in, a slide header, and a table cell are not paragraphs.
    for html in (
        "<div class=\"mtxt\"><strong>SCOPE LOCKED</strong><span>WEEK 2</span></div>",
        "<div class=\"st\"><b>Measured monthly.</b> Margin is compared to the baseline.</div>",
        "<h2>The <strong>only</strong> question that matters</h2>",
        "<td><b>Phase 1</b> foundation</td>",
    ):
        assert _gate(html).html == html, _gate(html).html


def test_unbolding_moves_no_text():
    html = "<p>Cost to the client today is <strong>$275,000</strong> a year.</p>"
    out = _gate(html).html
    assert "$275,000" in out, out
    assert "Cost to the client today is $275,000 a year." in out, out


# ---------------------------------------------------------------------------
# Format rule: colons.
# ---------------------------------------------------------------------------
def test_a_prose_colon_is_flagged_and_never_rewritten():
    html = "<p>The pipeline is slow: we measured it at three minutes.</p>"
    result = _gate(html)
    assert result.html == html, result.html
    assert [flag.kind for flag in result.flags] == ["colon"], result.flags


def test_label_colons_are_exempt():
    # A colon after a short fragment with no verb is the deck's label device, and
    # the rule is about prose. These are the four the status deck produced.
    for label in ("Status update: pricing and scheduling",
                  "+40% Coil steel: $0.46 to $0.64/lb",
                  "Week 4-5: pricing inputs",
                  "Week 6+: we mirror the line"):
        assert _gate(f"<p>{label}</p>").flags == [], label


def test_a_time_is_never_read_as_a_colon():
    # Masked before any rule runs, so it is not a colon and not a flag either.
    result = _gate("<p>The standup moved to 9:30 on Tuesday and it is working.</p>")
    assert "9:30" in result.html, result.html
    assert result.flags == [], result.flags


# ---------------------------------------------------------------------------
# Banned vocabulary.
# ---------------------------------------------------------------------------
def test_leverage_as_a_verb_is_replaced():
    assert "we use the" in _text("we leverage the existing warehouse").lower()
    assert "to use" in _text("a chance to leverage what is already there")
    assert "using" in _text("leveraging the existing connectors")


def test_leveraged_buyout_is_left_alone():
    # The noun is PE vocabulary. The rule fires only on evidence of the verb.
    out = _text("The leveraged buyout closed and operating leverage improved.")
    assert "leveraged buyout" in out, out
    assert "operating leverage" in out, out


def test_deep_dive_verb_and_noun_take_different_replacements():
    assert "research the" in _text("we deep dive into the pricing data")
    assert "close look" in _text("a deep dive on the numbers"), _text("a deep dive on the numbers")


def test_unpack_and_drill_down_and_synergies():
    assert "examine" in _text("we unpack the thesis")
    assert "focus on" in _text("we drill down into the fleet data")
    assert "benefits" in _text("the synergies are real")


def test_synergies_is_replaced_and_still_flagged_for_the_reviewer():
    result = _gate("<p>The synergies are real.</p>")
    assert "benefits" in result.html, result.html
    assert any(flag.rule_id == "synergies" for flag in result.flags), result.flags


def test_drill_down_noun_is_flagged_not_rewritten():
    # "dashboards with drill-down" names a capability. Every deterministic
    # substitute changes what it claims, so the reviewer decides.
    html = "<p>Dashboards with drill-down from fleet to crew.</p>"
    result = _gate(html)
    assert result.html == html, result.html
    assert any(flag.rule_id == "drill-down-noun" for flag in result.flags), result.flags


def test_portco_is_external_only():
    external = _text("every portco we touch", audience=AUDIENCE_EXTERNAL)
    internal = _text("every portco we touch", audience=AUDIENCE_INTERNAL)
    assert "portfolio company" in external, external
    assert "portco" in internal, internal


def test_replacement_carries_the_original_casing():
    assert "EXAMINE" in _text("The team will UNPACK the thesis in week eight.")
    assert "Examine" in _text("Unpack the thesis in week eight.")


def test_a_capitalized_multiword_label_is_protected_from_the_vocabulary_rules():
    # A milestone label the packet supplied is extraction-side content, not
    # render-side voice. A multi-word capitalized run is masked, so the
    # vocabulary table cannot reach it.
    html = "<div class=\"mtxt\"><strong>DEEP DIVE COMPLETE</strong><span>WEEK 8</span></div>"
    assert "DEEP DIVE COMPLETE" in _gate(html).html, _gate(html).html


def test_please_dont_hesitate_is_cut():
    out = _text("Please don't hesitate to reach out with questions.")
    assert "hesitate" not in out, out
    assert "reach out with questions." in out, out


def test_looking_forward_fragment_gets_a_subject():
    out = _text("Looking forward to the Thursday session.")
    assert "We are looking forward to" in out, out


def test_a_name_that_contains_a_banned_word_is_protected():
    # A multi-word capitalized run is a name, and names are masked before any
    # rule runs, so the vocabulary table cannot reach inside one.
    out = _text("Deep Dive Partners signed in week two.")
    assert "Deep Dive Partners" in out, out


def test_caller_declared_values_are_untouchable():
    # What the render leg does: hand the gate every value the fidelity guard
    # checks, so a packet-supplied label is invisible to the vocabulary rules.
    html = "<p>The deep dive review is booked, and we deep dive into the crew data.</p>"
    out = _gate(html, protect=["deep dive review"]).html
    assert "deep dive review" in out, out
    assert "we research the crew data" in out, out


# ---------------------------------------------------------------------------
# The hard constraint: no factual change, ever.
# ---------------------------------------------------------------------------
_FACT_HEAVY = """<!doctype html><html><head><style>.a{color:#111}</style></head><body>
<section class="slide"><h1>Ridgeline Site Services</h1>
<p class="summary">Steel moved +40% — $0.46 to $0.64/lb — and cost Ridgeline roughly
$1.5M in COGS across FY26. <strong>Stop the margin bleed!</strong></p>
<p>The pilot runs in weeks 1–8 for a single crew: we leverage the existing
the accounting system connector, then drill down into the fleet data. Q3 2026 is the
decision point: 3.5x on a $275,000 build, measured at 9:30 each Monday.</p>
<p>Woodgrove Partners holds 80–84%, and QofAI takes 20% / 10% / 5% until the
2.5× cap. Deep dive into the deck: it is a synergies story!</p>
</section></body></html>"""


def test_no_number_date_or_figure_changes_on_fact_heavy_copy():
    result = _gate(_FACT_HEAVY)
    before = factual_index(_FACT_HEAVY)
    after = factual_index(result.html)
    assert before["numeric"] == after["numeric"], (
        [x for x in before["numeric"] if x not in after["numeric"]],
        [x for x in after["numeric"] if x not in before["numeric"]],
    )
    assert before["entities"] == after["entities"], (before["entities"], after["entities"])
    # And the figures are still literally on the page.
    for figure in ("$0.46", "$0.64", "$1.5M", "$275,000", "+40%", "80", "84", "3.5x", "9:30", "2.5×"):
        assert figure in result.html, figure


def test_the_gate_verifies_itself_on_every_run():
    # verify=True is the default, so a rule that moved a fact raises instead of
    # returning a deck. Proved by forcing a change past the rules.
    result = _gate(_FACT_HEAVY)
    assert isinstance(result, GateResult)
    broken = result.html.replace("$275,000", "$285,000")
    try:
        assert_no_factual_change(_FACT_HEAVY, broken)
    except FactualChangeError as exc:
        assert "number" in str(exc) or "figure" in str(exc), exc
    else:
        raise AssertionError("a changed dollar figure was not caught")


def test_the_diff_guard_catches_a_changed_entity_name():
    before = "<p>Ridgeline Site Services signed in week two.</p>"
    after = "<p>Ridgeline Field Services signed in week two.</p>"
    try:
        assert_no_factual_change(before, after)
    except FactualChangeError as exc:
        assert "entity" in str(exc) or "proper noun" in str(exc), exc
    else:
        raise AssertionError("a changed entity name was not caught")


def test_the_independent_check_sees_a_name_go_missing():
    # The second opinion, which shares no code with the masking layer. It is
    # cruder than the factual index on purpose: it must stay quiet on a
    # legitimate rewrite, and go red when a name is gone. It is what fails if a
    # bug in masking ever blinds the index and the protection together.
    clean = ("<p>The synergies are real.</p>", "<p>The benefits are real.</p>")
    assert crude_fact_delta(*clean) is None, crude_fact_delta(*clean)
    lost = ("<p>Deep Dive Partners signed.</p>", "<p>Close look Partners signed.</p>")
    assert "entity name" in (crude_fact_delta(*lost) or ""), crude_fact_delta(*lost)


def test_markup_outside_text_nodes_is_byte_identical():
    result = _gate(_FACT_HEAVY)
    # The stylesheet is untouched, and so is every attribute.
    assert ".a{color:#111}" in result.html, result.html
    assert 'class="summary"' in result.html and 'class="slide"' in result.html


def test_style_and_script_bodies_are_never_rewritten():
    html = (
        "<html><head><style>.x{content:'deep dive!'}</style>"
        "<script>var s = 'we leverage this — always!';</script></head>"
        "<body><p>ok</p></body></html>"
    )
    out = _gate(html).html
    assert "deep dive!" in out, out
    assert "we leverage this — always!" in out, out


def test_the_gate_is_idempotent():
    once = _gate(_FACT_HEAVY).html
    twice = _gate(once)
    assert twice.html == once, "second pass changed the deck"
    assert twice.changes == [], twice.changes


def test_a_clean_document_is_returned_unchanged():
    html = "<html><body><p>The crew logs each ticket on paper today.</p></body></html>"
    assert _gate(html).html == html


# ---------------------------------------------------------------------------
# The same guarantee, proved on real rendered decks the tests own.
#
# These are real renders rather than hand-written snippets on purpose: the copy
# is model output, so a fixture only proves the guarantee if it carries the
# traps nobody thought to write. One per deck type per kind. They name a client
# because a real render does.
#
# THE FOUR FIXTURES ARE TWO DIFFERENT KINDS OF EVIDENCE. Do not read them as
# interchangeable.
#
#   *-pre-gate.html   Rendered 2026-07-20, before the gate landed, so the rules
#                     still have targets: the proposal fires 17 rules and the
#                     status deck fires 8. These are what make the
#                     no-factual-change guarantee testable at all, because a
#                     pass that rewrites nothing cannot be caught rewriting a
#                     fact. Break a rule and the factual assertions below go red
#                     on these two.
#   *-current.html    Rendered 2026-08-06, after the theme work and the gate
#                     landed, and clean under scripts/check_deck_layout.py.
#                     Since the gate runs inside the renderer these are gate
#                     output, so they fire 0 and 1 rules. They prove a different
#                     and real property: today's pipeline output is already
#                     clean and the gate is idempotent on it. They cannot prove
#                     the factual guarantee and are not kept for that.
# ---------------------------------------------------------------------------
def _real_decks():
    pattern = os.path.join(os.path.dirname(__file__), "fixtures", "text_gate", "*.html")
    return sorted(glob.glob(pattern))


def _pre_gate_decks():
    return [p for p in _real_decks() if os.path.basename(p).endswith("-pre-gate.html")]


def test_a_fixture_makes_the_gate_do_real_work():
    # The reason the pre-gate pair is committed. If both of these ever fall to
    # zero the factual assertions below stop biting and start passing vacuously,
    # which reports identically to passing for the right reason.
    fired = {os.path.basename(p): len(apply_text_gate(open(p, encoding="utf-8").read()).changes)
             for p in _pre_gate_decks()}
    assert len(fired) == 2, fired
    assert all(count > 0 for count in fired.values()), fired
    assert max(fired.values()) >= 8, fired


def test_real_decks_survive_the_gate_with_no_factual_change():
    decks = _real_decks()
    assert decks, "no deck fixtures found under tests/fixtures/text_gate/"
    for path in decks:
        with open(path, "r", encoding="utf-8") as f:
            html = f.read()
        result = apply_text_gate(html)  # verify=True raises on any factual drift
        before, after = factual_index(html), factual_index(result.html)
        assert before["numeric"] == after["numeric"], path
        assert before["entities"] == after["entities"], path


def test_real_decks_come_out_of_the_gate_clean():
    for path in _real_decks():
        with open(path, "r", encoding="utf-8") as f:
            html = f.read()
        gated = apply_text_gate(html)
        copy = " ".join(node.text for node in _text_nodes(gated.html))
        assert "—" not in copy and "&mdash;" not in copy, path
        assert "!" not in copy, path
        # Nothing left to rewrite is the definition of clean: a second pass is a
        # no-op, so no rule still has a target anywhere in the deck.
        assert apply_text_gate(gated.html).changes == [], path


def test_real_decks_are_idempotent_under_the_gate():
    for path in _real_decks()[:6]:
        with open(path, "r", encoding="utf-8") as f:
            html = f.read()
        once = apply_text_gate(html).html
        assert apply_text_gate(once).html == once, path


def test_review_flags_are_re_derivable_from_a_finished_deck():
    html = "<div><p>The warehouse access is blocked: IT has not answered.</p></div>"
    gated = apply_text_gate(html).html
    assert [f.rule_id for f in review_flags(gated)] == ["minimize-colons"], review_flags(gated)



# ---------------------------------------------------------------------------
# The F1 seam.
# ---------------------------------------------------------------------------
def test_the_voice_pass_seam_runs_behind_the_diff_guard():
    html = "<p>The crew logs every ticket on paper today.</p>"

    def harmless(document):
        return document.replace("every ticket", "each ticket")

    out = apply_text_gate(html, voice_pass=harmless).html
    assert "each ticket" in out, out


def test_the_voice_pass_seam_refuses_a_factual_change():
    html = "<p>Ridgeline Site Services signed a $275,000 build.</p>"

    def careless(document):
        return document.replace("$275,000", "$285,000")

    try:
        apply_text_gate(html, voice_pass=careless)
    except FactualChangeError:
        pass
    else:
        raise AssertionError("the seam let a changed dollar figure through")


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
