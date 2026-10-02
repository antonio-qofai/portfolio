"""Tests for the render-fidelity guard (`src/render_guard.py`).

Covers the prompt-to-HTML boundary (PRD criterion 13): every reviewer marker
and load-bearing value in the assembled prompt must survive into the rendered
HTML. Markers are a hard failure regardless of `strict`; values are report-only
unless `strict=True` is passed, per the false-positive-risk tradeoff (Claude
may legitimately reformat a value, e.g. `$1,500,000` -> `$1.5M`, without
actually dropping it).

Run with: python3 tests/test_render_guard.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from render_guard import RenderFidelityError, check_render_fidelity

PROMPT_4_PATH = os.path.join(
    os.path.dirname(__file__), "fixtures", "render_guard", "matched-prompt.txt"
)
OUTPUT_4_PATH = os.path.join(
    os.path.dirname(__file__), "fixtures", "render_guard", "matched-output.html"
)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


# ---- the real fixture: a matched, internally-consistent pass case ----

def test_matched_fixture_passes_clean():
    prompt = _read(PROMPT_4_PATH)
    html = _read(OUTPUT_4_PATH)
    report = check_render_fidelity(prompt, html)
    assert report["ok"], report
    assert report["missing_values"] == {}, report["missing_values"]
    # Sanity: every category actually found something to check, so a passing
    # report isn't just an artifact of empty extraction.
    for category, count in report["checked"].items():
        assert count > 0, (category, report["checked"])


def test_matched_fixture_passes_strict_too():
    # No missing values at all, so strict mode changes nothing here.
    prompt = _read(PROMPT_4_PATH)
    html = _read(OUTPUT_4_PATH)
    report = check_render_fidelity(prompt, html, strict=True)
    assert report["ok"], report


# ---- reviewer markers: always a hard failure ----

def test_dropped_unconfirmed_marker_raises():
    prompt = _read(PROMPT_4_PATH)
    html = _read(OUTPUT_4_PATH).replace("(unconfirmed, see gaps)", "")
    try:
        check_render_fidelity(prompt, html)
    except RenderFidelityError as e:
        assert "unconfirmed, see gaps" in str(e), str(e)
    else:
        raise AssertionError("expected RenderFidelityError for a dropped marker")


def test_dropped_missing_field_marker_raises():
    prompt = "role_name: [MISSING: role_name]\n"
    html = "<html><body>nothing relevant here</body></html>"
    try:
        check_render_fidelity(prompt, html)
    except RenderFidelityError as e:
        assert "[MISSING: role_name]" in str(e), str(e)
    else:
        raise AssertionError("expected RenderFidelityError for a dropped MISSING marker")


def test_marker_present_once_in_html_is_sufficient():
    # Presence, not count: the marker need only appear once in the HTML even
    # if it does not repeat every time it appeared in the prompt.
    prompt = "a: (unconfirmed, see gaps)\nb: (unconfirmed, see gaps)\n"
    html = "<p>(unconfirmed, see gaps)</p>"
    report = check_render_fidelity(prompt, html)
    assert report["ok"], report


# ---- load-bearing values: report-only by default, hard fail when strict ----

def test_missing_dollar_value_is_reported_not_raised_by_default():
    prompt = _read(PROMPT_4_PATH)
    html = _read(OUTPUT_4_PATH).replace("$275,000", "an unspecified investment")
    report = check_render_fidelity(prompt, html)
    assert not report["ok"], report
    assert "$275,000" in report["missing_values"]["dollar_amounts"], report["missing_values"]


def test_missing_dollar_value_raises_when_strict():
    prompt = _read(PROMPT_4_PATH)
    html = _read(OUTPUT_4_PATH).replace("$275,000", "an unspecified investment")
    try:
        check_render_fidelity(prompt, html, strict=True)
    except RenderFidelityError as e:
        assert "$275,000" in str(e), str(e)
    else:
        raise AssertionError("expected RenderFidelityError in strict mode")


def test_reformatted_dollar_value_still_matches():
    # A legitimate reformat (comma-separated -> compact) is not a drop.
    prompt = "qofai_investment: $1,500,000 · Conservative case\n"
    html = "<p>Investment: $1500000 total.</p>"
    report = check_render_fidelity(prompt, html)
    assert report["ok"], report


# ---- a stubbed HTML that drops one field, per the deliverable spec ----

def test_stubbed_html_drops_a_milestone_label_and_is_caught():
    prompt = (
        "milestones:\n"
        "1.\n"
        "  id: M0\n"
        "  week: 2\n"
        "  label: SCOPE LOCKED\n"
        "2.\n"
        "  id: M1\n"
        "  week: 8\n"
        "  label: PILOT VALIDATED\n"
    )
    # M1/PILOT VALIDATED renders; M0/SCOPE LOCKED is silently dropped.
    html = (
        "<html><body>"
        "<div class='ms'><div class='dot'>M1</div>"
        "<div class='txt'><strong>PILOT VALIDATED</strong></div></div>"
        "</body></html>"
    )
    report = check_render_fidelity(prompt, html)
    assert not report["ok"], report
    assert "M0" in report["missing_values"]["milestone_ids"], report["missing_values"]
    assert "SCOPE LOCKED" in report["missing_values"]["milestone_labels"], report["missing_values"]
    # The surviving milestone must not be flagged.
    assert "M1" not in report["missing_values"].get("milestone_ids", []), report
    assert "PILOT VALIDATED" not in report["missing_values"].get("milestone_labels", []), report


def test_stubbed_html_drops_a_scenario_and_is_caught():
    prompt = (
        "value_mapping:\n"
        "1.\n"
        "  scenario: CONSERVATIVE\n"
        "  ebitda_gain: +1.6pp\n"
        "2.\n"
        "  scenario: OPTIMISTIC\n"
        "  ebitda_gain: +2.7pp\n"
    )
    html = "<html><body><td>CONSERVATIVE</td><td>+1.6pp</td></body></html>"
    report = check_render_fidelity(prompt, html)
    assert not report["ok"], report
    assert "OPTIMISTIC" in report["missing_values"]["scenario_names"], report["missing_values"]
    assert "+2.7pp" in report["missing_values"]["percentages"], report["missing_values"]


# ---- category extraction correctness ----

def test_percentage_range_matches_as_a_single_span():
    prompt = "after_metric_1: +1.6–2.7pp · uplift\n"
    html = "<span class='big'>+1.6-2.7pp</span>"  # Claude swapped en-dash for hyphen
    report = check_render_fidelity(prompt, html)
    assert report["ok"], report


def test_multiplier_footnote_figures_checked():
    prompt = (
        "terms_footnote: Enterprise value at exit assumes a 6× EBITDA multiple, "
        "plus a 0.25× reporting-readiness multiple improvement on ~$6.9M adjusted "
        "EBITDA.\n"
    )
    html = "<p>Assumes a 6× multiple and $6.9M adjusted EBITDA.</p>"
    report = check_render_fidelity(prompt, html)
    # 0.25× is genuinely missing from this stub HTML.
    assert "0.25×" in report["missing_values"]["multipliers"], report["missing_values"]
    assert "6×" not in report["missing_values"].get("multipliers", []), report


def test_week_range_matches_both_orders():
    prompt = "build_summary: THE 16-WEEK BUILD\nPHASE 1 · WKS 1–8 · FOUNDATION\n"
    html = "<p>16-week build. Phase 1: WKS 1-8.</p>"
    report = check_render_fidelity(prompt, html)
    assert report["ok"], report


# ---- normalization: entities, tags, dashes, case ----

def test_html_entity_unescaped_before_matching():
    prompt = "milestones:\n1.\n  id: M0\n  label: Q&A LOCKED\n"
    html = "<p>Q&amp;A LOCKED</p>"
    report = check_render_fidelity(prompt, html)
    assert "Q&A LOCKED" not in report["missing_values"].get("milestone_labels", []), report


def test_angle_bracket_entity_value_not_a_false_positive():
    # Regression: a value containing `<` (e.g. `<15%`) renders as the entity
    # `&lt;15%`. If entities are unescaped BEFORE tags are stripped, the restored
    # `<` reads as an opening tag and the tag stripper eats `15%`, flagging a
    # spurious drop (the Vantgo `<15%` false positive). Tags must be stripped
    # first, then entities unescaped.
    prompt = "tracking_summary: churn held below <15% through the quarter\n"
    html = "<p>Churn held below &lt;15% through the quarter.</p>"
    report = check_render_fidelity(prompt, html, deck_type="status")
    assert "15%" not in report["missing_values"].get("percentages", []), report
    assert report["ok"], report


def test_greater_than_entity_value_not_a_false_positive():
    # Same failure mode with `&gt;`: `>20%` rendered as `&gt;20%` must match.
    prompt = "tracking_summary: margin above >20% at exit\n"
    html = "<p>Margin above &gt;20% at exit.</p>"
    report = check_render_fidelity(prompt, html, deck_type="status")
    assert report["ok"], report


def test_case_change_not_flagged_as_missing():
    # CSS text-transform: uppercase can make source-text case differ from what
    # renders; comparison is casefolded so this is not treated as a drop.
    prompt = "value_mapping:\n1.\n  scenario: CONSERVATIVE\n  ebitda_gain: +1.6pp\n"
    html = "<td class='sc' style='text-transform:uppercase'>Conservative</td><td>+1.6pp</td>"
    report = check_render_fidelity(prompt, html)
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


# ---------------------------------------------------------------------------
# SECTION LABELS (2026-09-15), the last prose seam on the proposal path.
#
# The template's `Section label:` line is prose the loader does not read and the
# assembler does not emit, so every proposal section label was the model's own
# invention from the slide title. That works while a label is a fixed string and
# stops working the moment it has to say WHICH opportunity a slide is, because
# an invented label cannot know. Slide 2's label is a bound role now, and this
# is what can see whether it survived.
# ---------------------------------------------------------------------------

TWO_LABELS = (
    "## Slide 2 — The Opportunity\n"
    "section_label: OPPORTUNITY 1 · MOBILE FIELD DATA CAPTURE\n"
    "opportunity_headline: A headline\n\n"
    "## Slide 3 — The Opportunity\n"
    "section_label: OPPORTUNITY 2 · REAL-TIME OPERATIONAL DASHBOARD\n"
)


def test_a_label_that_survived_is_counted_and_not_reported():
    html = ('<div class="kicker">OPPORTUNITY 1 · MOBILE FIELD DATA CAPTURE</div>'
            '<div class="kicker">OPPORTUNITY 2 · REAL-TIME OPERATIONAL DASHBOARD</div>')
    report = check_render_fidelity(TWO_LABELS, html)

    assert report["checked"]["section_labels"] == 2
    assert report["missing_values"] == {}
    assert report["ok"]


def test_a_render_that_invented_its_own_label_is_reported():
    """The exact failure this closes: two opportunity slides both reading THE
    OPPORTUNITY, which is what the deck did before the label was bound and what
    it would do again if the render ignored the role."""
    report = check_render_fidelity(
        TWO_LABELS, '<div class="kicker">THE OPPORTUNITY</div>')

    assert report["missing_values"]["section_labels"] == [
        "OPPORTUNITY 1 · MOBILE FIELD DATA CAPTURE",
        "OPPORTUNITY 2 · REAL-TIME OPERATIONAL DASHBOARD",
    ]
    assert not report["ok"]


def test_one_label_surviving_and_one_not_reports_only_the_one():
    """Which is the case that matters most: a deck where one slide says which
    opportunity it is and the other does not reads as correct at a glance."""
    report = check_render_fidelity(
        TWO_LABELS,
        '<div class="kicker">OPPORTUNITY 1 · MOBILE FIELD DATA CAPTURE</div>'
        '<div class="kicker">THE OPPORTUNITY</div>')

    assert report["missing_values"]["section_labels"] == [
        "OPPORTUNITY 2 · REAL-TIME OPERATIONAL DASHBOARD"
    ]


def test_a_missing_label_does_not_raise_by_default_but_does_under_strict():
    """It reports rather than raises because the label is a composed two-part
    string Claude may legitimately split across elements or separate
    differently, and this leg costs about four minutes. `strict` is where a
    caller that would rather lose the render than the label says so."""
    check_render_fidelity(TWO_LABELS, "<p>nothing</p>")
    with pytest.raises(RenderFidelityError):
        check_render_fidelity(TWO_LABELS, "<p>nothing</p>", strict=True)


def test_a_label_reached_through_markup_or_a_case_change_still_matches():
    """`_normalize` strips tags and casefolds, so a label set in small caps by
    CSS or split by an inline span is not a false positive."""
    report = check_render_fidelity(
        TWO_LABELS,
        '<div class="kicker">opportunity 1 <span>·</span> mobile field data capture</div>'
        '<div class="kicker">Opportunity 2 · Real-Time Operational Dashboard</div>')
    assert report["missing_values"] == {}


def test_an_empty_label_is_left_to_the_marker_check():
    """A role that came through empty carries a `[MISSING: ...]` marker, and
    that check already raises on it. Reporting it here as well would name one
    absence twice."""
    prompt = "section_label: [MISSING: section_label]\n"
    report = check_render_fidelity(prompt, "<p>[MISSING: section_label]</p>")
    assert "section_labels" not in report["checked"]


def test_a_prompt_with_no_supplied_label_reports_no_category():
    """Every proposal deck before 2026-09-15, and every slide whose label the
    system prompt still does not bind."""
    report = check_render_fidelity("opportunity_headline: A headline\n", "<p>x</p>")
    assert "section_labels" not in report["checked"]


def test_the_status_decks_three_labels_are_covered_by_the_same_rule():
    """Read off the key rather than from a list of role names, so the status
    deck's own labels are covered without naming them and a further one is
    covered the day a template declares it."""
    prompt = ("tracking_section_label: PHASED ROLLOUT · WEEK 3 OF 12\n"
              "section_label: NEW PROJECT · STATUS\n"
              "next_steps_section_label: NEXT STEPS\n")
    report = check_render_fidelity(prompt, "<p>nothing</p>", deck_type="status")
    assert report["missing_values"]["section_labels"] == [
        "PHASED ROLLOUT · WEEK 3 OF 12",
        "NEW PROJECT · STATUS",
        "NEXT STEPS",
    ]
