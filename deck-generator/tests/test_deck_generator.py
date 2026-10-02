"""Tests for Wire Together — the end-to-end deck-generation flow
(`src/deck_generator.py`).

Covers the three paths out of the adapter reaching their right terminal
output (build-plan-v2.md "Wire Together"; PRD §4's fork): a clean ok-packet
reaches the assembler and returns a full prompt, a gate failure returns
confidence/completeness/missing-fields for review, an error envelope surfaces
code/message/remediation — plus `deck_type = status` returning
"not yet implemented" without touching the template or the adapter.

Run with: python3 tests/test_deck_generator.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from deck_generator import (
    generate_deck_prompt,
    generate_and_save_deck,
    next_output_number,
)
from data_source_adapter import FixtureProvider
from template_loader import load_template
from data_source_adapter import run_adapter
from prompt_assembler import assemble_prompt

PACKET_PATH = os.path.join(
    os.path.dirname(__file__), "..", "proposal-data-packet-EXAMPLE.md"
)
TEMPLATE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "templates", "proposal-template.md"
)
STATUS_PACKET_PATH = os.path.join(
    os.path.dirname(__file__), "..", "status-data-packet-EXAMPLE.md"
)

FIXTURE_PROJECT = "Operational Intelligence Platform"
STATUS_PROJECT = "Implementation Project"


def _no_sleep(_):
    pass


def _packet_with(confidence, data_completeness):
    return (
        f'---\nconfidence: "{confidence}"\n'
        f"data_completeness: {data_completeness}\n---\n\n# stub body\n"
    )


def _error_envelope(code):
    return {
        "status": "error",
        "error": {
            "code": code,
            "message": f"{code}: human-readable explanation",
            "remediation": f"what to do about {code}",
            "details": {},
        },
    }


# ---- deck_type = status: now wired end to end (was not_implemented) ----

def test_status_deck_type_runs_the_full_flow():
    # status is wired now: a clean status packet flows through the real path and
    # returns a variable-count status prompt, not a not_implemented placeholder.
    provider = FixtureProvider.from_packet_file(STATUS_PACKET_PATH)
    result = generate_deck_prompt(
        "status", "Northwind", STATUS_PROJECT, provider,
        poll_interval=0.0, sleep=_no_sleep,
    )
    assert result["status"] == "ok", result
    assert result["confidence"] == "high", result
    assert result["client_short"] == "Northwind", result
    # cover + tracking + 2 workstreams + next_steps = 5 slides, numbered 1..5.
    for n in range(1, 6):
        assert f"## Slide {n} —" in result["prompt"], (n, result["prompt"])
    assert "## Slide 6 —" not in result["prompt"], result["prompt"]
    assert result["prompt"].rstrip().endswith("Not a finished or sendable deck.")


def test_unknown_deck_type_raises():
    try:
        generate_deck_prompt("roadmap", "Any Co", "Any Project", provider=None)
    except ValueError as e:
        assert "roadmap" in str(e), str(e)
    else:
        raise AssertionError("expected ValueError for unrecognized deck_type")


# ---- clean ok-packet reaches the assembler, returns a full prompt ----

def test_clean_proposal_returns_full_prompt():
    provider = FixtureProvider.from_packet_file(PACKET_PATH)
    result = generate_deck_prompt(
        "proposal", "Ridgeline Site Services", FIXTURE_PROJECT, provider,
        poll_interval=0.0, sleep=_no_sleep,
    )
    assert result["status"] == "ok", result
    assert result["confidence"] == "high", result
    assert result["data_completeness"] == 0.92, result
    assert "## Slide 1 —" in result["prompt"], result["prompt"]
    assert "## Slide 6 —" in result["prompt"], result["prompt"]
    assert result["prompt"].rstrip().endswith(
        "Not a finished or sendable deck."
    ), result["prompt"][-200:]


def test_clean_run_carries_the_decided_commercial_clauses_as_terms_rows():
    # Step-3 decision regression, restated for the adaptive slide (2026-09-23).
    # The retention note and the no-improvement clause were slotted as fixed
    # boxes; they now reach the deck as TERMS rows, verbatim. The clean run
    # still produces all six slides and the review framing.
    provider = FixtureProvider.from_packet_file(PACKET_PATH)
    result = generate_deck_prompt(
        "proposal", "Ridgeline Site Services", FIXTURE_PROJECT, provider,
        poll_interval=0.0, sleep=_no_sleep,
    )
    assert result["status"] == "ok", result
    prompt = result["prompt"]
    assert "  value: Client retains 80–84%, 100% thereafter." in prompt, prompt
    assert (
        "  value: If margins don't improve above your locked "
        "baseline, QofAI earns nothing." in prompt
    ), prompt
    # Structure intact: six slides, review framing last.
    for n in range(1, 7):
        assert f"## Slide {n} —" in prompt, (n, prompt)
    assert prompt.rstrip().endswith("Not a finished or sendable deck."), prompt[-200:]


def test_wired_prompt_matches_calling_the_three_modules_directly():
    # The wired flow must produce exactly what calling Modules 1-3 by hand
    # produces; no extra transformation sneaks in at the wiring layer.
    provider_a = FixtureProvider.from_packet_file(PACKET_PATH)
    wired = generate_deck_prompt(
        "proposal", "Ridgeline Site Services", FIXTURE_PROJECT, provider_a,
        poll_interval=0.0, sleep=_no_sleep,
    )

    template = load_template(TEMPLATE_PATH)
    provider_b = FixtureProvider.from_packet_file(PACKET_PATH)
    manual_result = run_adapter(
        "Ridgeline Site Services", FIXTURE_PROJECT, provider_b,
        poll_interval=0.0, sleep=_no_sleep,
    )
    manual_prompt = assemble_prompt(template, manual_result["placeholder_map"])

    assert wired["prompt"] == manual_prompt, (wired["prompt"], manual_prompt)


# ---- the commercial slide's TERMS rows (2026-09-23, adaptive deal sheet) ----

def test_a_packet_stating_its_own_deal_carries_it_as_terms_rows():
    # THE ADAPTIVITY TEST (build-plan-commercial-slide.md, criterion 3). The
    # fixture packet states the retired FBK-style performance deal in the old
    # fixed fields; the adaptive slide must hold it as rows, with no special
    # case, beside the return table and the value chart.
    provider = FixtureProvider.from_packet_file(PACKET_PATH)
    result = generate_deck_prompt(
        "proposal", "Ridgeline Site Services", FIXTURE_PROJECT, provider,
        poll_interval=0.0, sleep=_no_sleep,
    )
    prompt = result["prompt"]
    assert "[MISSING: terms_rows]" not in prompt, prompt
    for label in ("QofAI investment", "Client up-front", "Comp schedule",
                  "Client retains", "Downside protection", "How payment works"):
        assert f"  label: {label}" in prompt, prompt
    assert "return_rows:" in prompt and "value_mapping:" in prompt, prompt
    assert "commercial_rows" not in prompt, "the retired role is gone"


def test_no_terms_anywhere_leaves_the_marker_for_a_reviewer():
    # A PRD states no deal terms, so the role is empty and its marker asks a
    # human, never an invented term.
    template = load_template(TEMPLATE_PATH)
    result = run_adapter("Ridgeline Site Services", FIXTURE_PROJECT,
                         FixtureProvider.from_packet_file(PACKET_PATH),
                         poll_interval=0.0, sleep=_no_sleep)
    placeholders = dict(result["placeholder_map"], terms_rows=[])
    prompt = assemble_prompt(template, placeholders)
    assert "terms_rows: [MISSING: terms_rows]" in prompt, prompt


def test_three_fee_structures_render_as_rows_with_no_code_change():
    # A flat fee, a retainer plus a success fee, and a performance schedule are
    # all just named rows, proving the row labels are data, not code. Rows a
    # caller states REPLACE the packet's own.
    structures = {
        "flat fee": [{"label": "FLAT FEE", "value": "$150K, due on signature"}],
        "retainer plus success fee": [
            {"label": "MONTHLY RETAINER", "value": "$12K/mo for 6 months"},
            {"label": "SUCCESS FEE", "value": "10% of realized EBITDA gain"},
        ],
        "performance-based": [
            {"label": "QOFAI INVESTMENT", "value": "~$224K, absorbed upfront"},
            {"label": "CLIENT UP-FRONT", "value": "$0"},
            {"label": "COMP SCHEDULE", "value": "Year 1 20% . Year 2 10%"},
        ],
    }
    for rows in structures.values():
        provider = FixtureProvider.from_packet_file(PACKET_PATH)
        result = generate_deck_prompt(
            "proposal", "Ridgeline Site Services", FIXTURE_PROJECT, provider,
            poll_interval=0.0, sleep=_no_sleep, commercial_rows=rows,
        )
        prompt = result["prompt"]
        assert "[MISSING: terms_rows]" not in prompt, prompt
        assert "  label: Downside protection" not in prompt, "caller rows replace the packet's"
        for row in rows:
            assert f"  label: {row['label']}" in prompt, prompt
            assert f"  value: {row['value']}" in prompt, prompt


def test_save_threads_commercial_rows_into_the_saved_prompt():
    # The kwarg reaches generate_and_save_deck's saved Design prompt too, not
    # only the bare generate_deck_prompt path.
    renderer, _ = _stub_renderer_factory()
    rows = [{"label": "FLAT FEE", "value": "$150K"}]
    provider = FixtureProvider.from_packet_file(PACKET_PATH)
    with tempfile.TemporaryDirectory() as pdir, tempfile.TemporaryDirectory() as ddir:
        result = generate_and_save_deck(
            "proposal", "Ridgeline Site Services", FIXTURE_PROJECT, provider,
            prompts_dir=pdir, decks_dir=ddir, renderer=renderer,
            commercial_rows=rows, poll_interval=0.0, sleep=_no_sleep,
        )
        assert result["status"] == "ok", result
        assert "label: FLAT FEE" in result["prompt"], result["prompt"]
        assert "value: $150K" in result["prompt"], result["prompt"]


# ---- gate failure returns review payload, no prompt (PRD criterion 4) ----

def test_gate_failure_returns_review_no_prompt():
    provider = FixtureProvider.from_packet_markdown(_packet_with("low", 0.92))
    result = generate_deck_prompt(
        "proposal", "Some Co", "Some Project", provider,
        poll_interval=0.0, sleep=_no_sleep,
    )
    assert result["status"] == "review", result
    assert result["confidence"] == "low", result
    assert result["data_completeness"] == 0.92, result
    assert "prompt" not in result, result


def test_low_completeness_returns_review_no_prompt():
    provider = FixtureProvider.from_packet_markdown(_packet_with("high", 0.55))
    result = generate_deck_prompt(
        "proposal", "Some Co", "Some Project", provider,
        poll_interval=0.0, sleep=_no_sleep,
    )
    assert result["status"] == "review", result
    assert "prompt" not in result, result


# ---- error envelope surfaces code/message/remediation, no prompt (criterion 5) ----

def test_error_envelope_surfaces_code_and_produces_no_prompt():
    for code in ("E_NO_KG", "E_COMPANY_NOT_FOUND", "E_KG_UNREACHABLE"):
        provider = FixtureProvider.from_envelope(_error_envelope(code))
        result = generate_deck_prompt(
            "proposal", "Some Co", "Some Project", provider,
            poll_interval=0.0, sleep=_no_sleep,
        )
        assert result["status"] == "error", (code, result)
        assert result["code"] == code, (code, result)
        assert result["message"], (code, result)
        assert result["remediation"], (code, result)
        assert "prompt" not in result, (code, result)


def test_project_level_error_surfaces_and_produces_no_prompt():
    provider = FixtureProvider.from_envelope(_error_envelope("E_PROJECT_NOT_FOUND"))
    result = generate_deck_prompt(
        "proposal", "Some Co", "Some Project", provider,
        poll_interval=0.0, sleep=_no_sleep,
    )
    assert result["status"] == "error", result
    assert result["code"] == "E_PROJECT_NOT_FOUND", result
    assert "prompt" not in result, result


# ---- request-shaping kwargs pass through to the adapter ----

def test_adapter_kwargs_pass_through():
    # sections_requested reaching the adapter should still surface as omitted
    # slides in the wired prompt, proving the kwarg actually threads through.
    provider = FixtureProvider.from_packet_file(PACKET_PATH)
    result = generate_deck_prompt(
        "proposal", "Ridgeline", FIXTURE_PROJECT, provider,
        sections_requested=["cover", "opportunity", "platform"],
        poll_interval=0.0, sleep=_no_sleep,
    )
    assert result["status"] == "ok", result
    assert "## Slide 4 —" not in result["prompt"], result["prompt"]
    assert "## Slide 5 —" not in result["prompt"], result["prompt"]
    assert "## Slide 6 —" not in result["prompt"], result["prompt"]


# ---- template_path override ----

def test_template_path_override_is_used():
    # Feeding a nonexistent path proves generate_deck_prompt actually attempts
    # to load the given override rather than always falling back to default.
    provider = FixtureProvider.from_packet_file(PACKET_PATH)
    try:
        generate_deck_prompt(
            "proposal", "Ridgeline", FIXTURE_PROJECT, provider,
            template_path="/nonexistent/path/template.md",
            poll_interval=0.0, sleep=_no_sleep,
        )
    except (FileNotFoundError, OSError):
        pass
    else:
        raise AssertionError("expected a file error from the overridden path")


def test_status_save_path_files_per_client_with_status_scaffold():
    # The status path files both deliverables per client under a shared number,
    # the same way the proposal path does, using the status render scaffold.
    def stub(prompt, **kwargs):
        assert kwargs.get("deck_type") == "status", kwargs
        return "<!doctype html><body>STATUS<p>(unconfirmed, see gaps)</p></body>"

    provider = FixtureProvider.from_packet_file(STATUS_PACKET_PATH)
    with tempfile.TemporaryDirectory() as pdir, tempfile.TemporaryDirectory() as ddir:
        result = generate_and_save_deck(
            "status", "Northwind", STATUS_PROJECT, provider,
            prompts_dir=pdir, decks_dir=ddir, renderer=stub,
            poll_interval=0.0, sleep=_no_sleep,
        )
        assert result["status"] == "ok", result
        assert result["number"] == 1, result
        assert os.path.isfile(os.path.join(pdir, "generated-prompt-1.txt"))
        assert os.path.isfile(os.path.join(ddir, "output-1.html"))
        # The saved Design prompt leads with the status house-style header.
        assert result["prompt"].startswith("===== QOFAI DECK HOUSE STYLE"), result["prompt"][:80]


# ---- next_output_number: shared, auto-incrementing, gap-tolerant ----

def _touch(directory, name):
    open(os.path.join(directory, name), "w").close()


def test_next_output_number_starts_at_one_when_empty():
    with tempfile.TemporaryDirectory() as p, tempfile.TemporaryDirectory() as d:
        assert next_output_number(p, d) == 1


def test_next_output_number_is_one_past_the_highest_across_both_folders():
    # Prompt folder has 1-3, deck folder has 2 and 5 → next is 6 (max across both).
    with tempfile.TemporaryDirectory() as p, tempfile.TemporaryDirectory() as d:
        for n in (1, 2, 3):
            _touch(p, f"generated-prompt-{n}.txt")
        _touch(d, "output-2.html")
        _touch(d, "output-5.html")
        assert next_output_number(p, d) == 6, next_output_number(p, d)


def test_next_output_number_ignores_unrelated_files():
    with tempfile.TemporaryDirectory() as p, tempfile.TemporaryDirectory() as d:
        _touch(p, "generated-prompt-4.txt")
        _touch(p, "notes.txt")
        _touch(p, "generated-prompt-v0-baseline.txt")  # no bare number → ignored
        _touch(d, ".DS_Store")
        assert next_output_number(p, d) == 5, next_output_number(p, d)


def test_missing_deck_dir_is_treated_as_empty():
    with tempfile.TemporaryDirectory() as p:
        _touch(p, "generated-prompt-2.txt")
        assert next_output_number(p, "/nonexistent/decks/dir") == 3


# ---- generate_and_save_deck: both deliverables, shared number, same prompt ----

def _stub_renderer_factory():
    """Returns (renderer, calls) — the renderer records the prompt it received
    and returns a fixed HTML string, so no API/key is needed. Echoes the
    "(unconfirmed, see gaps)" marker verbatim: the Ridgeline fixture's §8 flags
    a deal figure its TERMS rows carry, so its prompt always holds one, and the
    render-fidelity guard runs on every render, so a stub missing a marker the
    prompt actually contains would trip it (these tests are about file/number
    plumbing, not fidelity content)."""
    calls = []

    def renderer(prompt, **kwargs):
        calls.append(prompt)
        return (
            "<!doctype html><html><body>STUB DECK"
            "<p>(unconfirmed, see gaps)</p>"
            "</body></html>"
        )

    return renderer, calls


def test_save_writes_both_files_with_matched_number_and_identical_prompt():
    renderer, calls = _stub_renderer_factory()
    provider = FixtureProvider.from_packet_file(PACKET_PATH)
    with tempfile.TemporaryDirectory() as pdir, tempfile.TemporaryDirectory() as ddir:
        result = generate_and_save_deck(
            "proposal", "Ridgeline Site Services", FIXTURE_PROJECT, provider,
            prompts_dir=pdir, decks_dir=ddir, renderer=renderer,
            poll_interval=0.0, sleep=_no_sleep,
        )
        assert result["status"] == "ok", result
        assert result["number"] == 1, result
        prompt_path = os.path.join(pdir, "generated-prompt-1.txt")
        deck_path = os.path.join(ddir, "output-1.html")
        assert result["prompt_path"] == prompt_path, result
        assert result["deck_path"] == deck_path, result
        assert os.path.isfile(prompt_path) and os.path.isfile(deck_path)

        # The prompt fed to the renderer is byte-for-byte the saved prompt.
        with open(prompt_path, encoding="utf-8") as f:
            saved_prompt = f.read()
        assert calls == [saved_prompt], "renderer must receive the exact saved prompt"
        assert saved_prompt == result["prompt"], "saved file must equal the returned prompt"

        with open(deck_path, encoding="utf-8") as f:
            assert "STUB DECK" in f.read()


def test_save_increments_number_on_a_second_run():
    renderer, _ = _stub_renderer_factory()
    with tempfile.TemporaryDirectory() as pdir, tempfile.TemporaryDirectory() as ddir:
        for expected in (1, 2):
            provider = FixtureProvider.from_packet_file(PACKET_PATH)
            result = generate_and_save_deck(
                "proposal", "Ridgeline Site Services", FIXTURE_PROJECT, provider,
                prompts_dir=pdir, decks_dir=ddir, renderer=renderer,
                poll_interval=0.0, sleep=_no_sleep,
            )
            assert result["number"] == expected, result
        assert sorted(os.listdir(pdir)) == ["generated-prompt-1.txt", "generated-prompt-2.txt"]
        assert sorted(os.listdir(ddir)) == ["output-1.html", "output-2.html"]


def test_save_render_false_writes_prompt_only():
    renderer, calls = _stub_renderer_factory()
    provider = FixtureProvider.from_packet_file(PACKET_PATH)
    with tempfile.TemporaryDirectory() as pdir, tempfile.TemporaryDirectory() as ddir:
        result = generate_and_save_deck(
            "proposal", "Ridgeline Site Services", FIXTURE_PROJECT, provider,
            prompts_dir=pdir, decks_dir=ddir, renderer=renderer, render=False,
            poll_interval=0.0, sleep=_no_sleep,
        )
        assert os.path.isfile(os.path.join(pdir, "generated-prompt-1.txt"))
        assert os.listdir(ddir) == [], "no deck should be rendered when render=False"
        assert "deck_path" not in result, result
        assert calls == [], "renderer must not be called when render=False"


def test_save_render_failure_writes_nothing_and_does_not_consume_number():
    # If the render (API call) raises, the run must leave no files behind — no
    # orphaned prompt, no consumed number — so the next run reuses N and every
    # prompt keeps its matching deck.
    def failing_renderer(prompt, **kwargs):
        raise RuntimeError("simulated API failure")

    provider = FixtureProvider.from_packet_file(PACKET_PATH)
    with tempfile.TemporaryDirectory() as pdir, tempfile.TemporaryDirectory() as ddir:
        try:
            generate_and_save_deck(
                "proposal", "Ridgeline Site Services", FIXTURE_PROJECT, provider,
                prompts_dir=pdir, decks_dir=ddir, renderer=failing_renderer,
                poll_interval=0.0, sleep=_no_sleep,
            )
        except RuntimeError:
            pass
        else:
            raise AssertionError("expected the render failure to propagate")
        assert os.listdir(pdir) == [], f"prompt should not be written on render failure: {os.listdir(pdir)}"
        assert os.listdir(ddir) == [], os.listdir(ddir)
        # Number not consumed: a subsequent good run gets N=1.
        good_renderer, _ = _stub_renderer_factory()
        provider2 = FixtureProvider.from_packet_file(PACKET_PATH)
        result = generate_and_save_deck(
            "proposal", "Ridgeline Site Services", FIXTURE_PROJECT, provider2,
            prompts_dir=pdir, decks_dir=ddir, renderer=good_renderer,
            poll_interval=0.0, sleep=_no_sleep,
        )
        assert result["number"] == 1, result


# ---- render-fidelity guard wired in: report surfaced, marker drop blocks ----

def test_save_surfaces_the_render_fidelity_report():
    renderer, _ = _stub_renderer_factory()
    provider = FixtureProvider.from_packet_file(PACKET_PATH)
    with tempfile.TemporaryDirectory() as pdir, tempfile.TemporaryDirectory() as ddir:
        result = generate_and_save_deck(
            "proposal", "Ridgeline Site Services", FIXTURE_PROJECT, provider,
            prompts_dir=pdir, decks_dir=ddir, renderer=renderer,
            poll_interval=0.0, sleep=_no_sleep,
        )
        assert result["status"] == "ok", result
        assert "render_fidelity" in result, result
        assert "missing_values" in result["render_fidelity"], result["render_fidelity"]


def test_save_dropped_marker_raises_and_writes_nothing():
    # A renderer that drops the fixture's "(unconfirmed, see gaps)" marker must block
    # the run the same way a render (API) failure does: propagate, write no
    # files, consume no number.
    def dropping_renderer(prompt, **kwargs):
        return "<!doctype html><html><body>STUB DECK, no markers here</body></html>"

    provider = FixtureProvider.from_packet_file(PACKET_PATH)
    with tempfile.TemporaryDirectory() as pdir, tempfile.TemporaryDirectory() as ddir:
        from render_guard import RenderFidelityError

        try:
            generate_and_save_deck(
                "proposal", "Ridgeline Site Services", FIXTURE_PROJECT, provider,
                prompts_dir=pdir, decks_dir=ddir, renderer=dropping_renderer,
                poll_interval=0.0, sleep=_no_sleep,
            )
        except RenderFidelityError:
            pass
        else:
            raise AssertionError("expected RenderFidelityError for a dropped marker")
        assert os.listdir(pdir) == [], os.listdir(pdir)
        assert os.listdir(ddir) == [], os.listdir(ddir)

        good_renderer, _ = _stub_renderer_factory()
        provider2 = FixtureProvider.from_packet_file(PACKET_PATH)
        result = generate_and_save_deck(
            "proposal", "Ridgeline Site Services", FIXTURE_PROJECT, provider2,
            prompts_dir=pdir, decks_dir=ddir, renderer=good_renderer,
            poll_interval=0.0, sleep=_no_sleep,
        )
        assert result["number"] == 1, result


def test_a_saved_deck_carries_no_unconfirmed_marker():
    # The finished deck is clean of the gap flag, on both deck types (Antonio,
    # 2026-07-21: a gap flag is an internal review note, not client-facing deck
    # copy). This pins the `strip_gap_flags` call in `generate_and_save_deck`,
    # which runs after the fidelity guard has confirmed the markers survived the
    # render, so the guard's no-dropped-marker rule still bites and the artifact
    # still gets written clean. It is also what a reviewer's Confirm decision
    # rests on: there is no marker on a deck for a decision to strip. Deleting
    # that one call left the whole suite green before this test existed
    # (measured 2026-08-09).
    marker = "(unconfirmed, see gaps)"

    def stub(prompt, **kwargs):
        # Wrapped and bare: real renders in this repo carry both shapes.
        return (
            "<!doctype html><html><body>STUB DECK"
            f'<p>A modeled figure <span class="flag">{marker}</span></p>'
            f"<p>A judgment call {marker}</p></body></html>"
        )

    for deck_type, packet, company, project in (
        ("proposal", PACKET_PATH, "Ridgeline Site Services", FIXTURE_PROJECT),
        ("status", STATUS_PACKET_PATH, "Northwind", STATUS_PROJECT),
    ):
        provider = FixtureProvider.from_packet_file(packet)
        with tempfile.TemporaryDirectory() as pdir, tempfile.TemporaryDirectory() as ddir:
            result = generate_and_save_deck(
                deck_type, company, project, provider,
                prompts_dir=pdir, decks_dir=ddir, renderer=stub,
                poll_interval=0.0, sleep=_no_sleep,
            )
            assert result["status"] == "ok", result
            written = open(result["deck_path"], encoding="utf-8").read()
            assert marker not in written, f"{deck_type} deck kept the flag"
            assert 'class="flag"' not in written, "the badge goes with the marker"
            assert "A modeled figure" in written, "the value it annotated stays"


def test_save_printed_tag_raises_and_writes_nothing():
    # The display-text guard, at the boundary the four older guards cannot see: a
    # renderer that escapes its own markup produces a deck whose values are all
    # present and whose slides read `<span class="base">` in full view. That must
    # fail the render the same way a dropped marker does — no files, no number.
    def leaking_renderer(prompt, **kwargs):
        return (
            "<!doctype html><html><body>STUB DECK"
            "<p>(unconfirmed, see gaps)</p>"
            "<p>&lt;span class=&quot;base&quot;&gt;Project Planning.&lt;/span&gt;</p>"
            "</body></html>"
        )

    provider = FixtureProvider.from_packet_file(PACKET_PATH)
    with tempfile.TemporaryDirectory() as pdir, tempfile.TemporaryDirectory() as ddir:
        from display_text_guard import DisplayTextError

        try:
            generate_and_save_deck(
                "proposal", "Ridgeline Site Services", FIXTURE_PROJECT, provider,
                prompts_dir=pdir, decks_dir=ddir, renderer=leaking_renderer,
                poll_interval=0.0, sleep=_no_sleep,
            )
        except DisplayTextError as e:
            assert '<span class="base">' in str(e), str(e)
        else:
            raise AssertionError("expected DisplayTextError for a printed tag")
        assert os.listdir(pdir) == [], os.listdir(pdir)
        assert os.listdir(ddir) == [], os.listdir(ddir)


def test_save_strict_render_fidelity_raises_on_missing_value():
    # A renderer that preserves the marker but drops a load-bearing value
    # (the $275,000 investment figure) only blocks the run when the caller
    # opts into strict_render_fidelity; otherwise it is report-only.
    def value_dropping_renderer(prompt, **kwargs):
        return (
            "<!doctype html><html><body>STUB DECK"
            "<p>(unconfirmed, see gaps)</p>"
            "</body></html>"
        )

    provider = FixtureProvider.from_packet_file(PACKET_PATH)
    with tempfile.TemporaryDirectory() as pdir, tempfile.TemporaryDirectory() as ddir:
        result = generate_and_save_deck(
            "proposal", "Ridgeline Site Services", FIXTURE_PROJECT, provider,
            prompts_dir=pdir, decks_dir=ddir, renderer=value_dropping_renderer,
            poll_interval=0.0, sleep=_no_sleep,
        )
        assert result["status"] == "ok", result
        assert not result["render_fidelity"]["ok"], result["render_fidelity"]
        assert os.path.isfile(os.path.join(ddir, "output-1.html"))

    from render_guard import RenderFidelityError

    provider2 = FixtureProvider.from_packet_file(PACKET_PATH)
    with tempfile.TemporaryDirectory() as pdir, tempfile.TemporaryDirectory() as ddir:
        try:
            generate_and_save_deck(
                "proposal", "Ridgeline Site Services", FIXTURE_PROJECT, provider2,
                prompts_dir=pdir, decks_dir=ddir, renderer=value_dropping_renderer,
                strict_render_fidelity=True,
                poll_interval=0.0, sleep=_no_sleep,
            )
        except RenderFidelityError:
            pass
        else:
            raise AssertionError("expected RenderFidelityError in strict mode")
        assert os.listdir(pdir) == [], os.listdir(pdir)
        assert os.listdir(ddir) == [], os.listdir(ddir)


def test_save_writes_nothing_on_a_non_ok_result():
    renderer, calls = _stub_renderer_factory()
    provider = FixtureProvider.from_packet_markdown(_packet_with("low", 0.92))
    with tempfile.TemporaryDirectory() as pdir, tempfile.TemporaryDirectory() as ddir:
        result = generate_and_save_deck(
            "proposal", "Some Co", "Some Project", provider,
            prompts_dir=pdir, decks_dir=ddir, renderer=renderer,
            poll_interval=0.0, sleep=_no_sleep,
        )
        assert result["status"] == "review", result
        assert os.listdir(pdir) == [] and os.listdir(ddir) == [], (pdir, ddir)
        assert calls == [], "renderer must not run on a gate failure"
        assert "number" not in result and "prompt_path" not in result, result


# ---- standing reviewer preferences threaded into both render legs ----

def _renderer_capturing_kwargs():
    """Renderer stub that records (prompt, kwargs) per call, echoing a marker so
    the render-fidelity guard is satisfied."""
    calls = []

    def renderer(prompt, **kwargs):
        calls.append((prompt, kwargs))
        return "<!doctype html><body>STUB<p>(unconfirmed, see gaps)</p></body>"

    return renderer, calls


def test_no_store_leaves_both_legs_unchanged_and_reports_empty_preferences():
    renderer, calls = _renderer_capturing_kwargs()
    provider = FixtureProvider.from_packet_file(PACKET_PATH)
    with tempfile.TemporaryDirectory() as pdir, tempfile.TemporaryDirectory() as ddir:
        # A store path that does not exist — the no-preferences path.
        result = generate_and_save_deck(
            "proposal", "Ridgeline Site Services", FIXTURE_PROJECT, provider,
            prompts_dir=pdir, decks_dir=ddir, renderer=renderer,
            preferences_path=os.path.join(pdir, "no-such-store.json"),
            poll_interval=0.0, sleep=_no_sleep,
        )
        assert result["applied_preferences"] == [], result
        # The system prompt the render leg receives carries no preferences block.
        _, kwargs = calls[0]
        assert kwargs["preferences"] == "", kwargs
        # And the saved Design prompt carries no preferences heading.
        assert "STANDING REVIEWER PREFERENCES" not in result["prompt"]


def test_applicable_preference_reaches_both_legs():
    from preference_store import add_preference

    renderer, calls = _renderer_capturing_kwargs()
    provider = FixtureProvider.from_packet_file(PACKET_PATH)
    with tempfile.TemporaryDirectory() as pdir, tempfile.TemporaryDirectory() as ddir:
        store = os.path.join(pdir, "prefs.json")
        add_preference("tighten the footer whitespace", scope="proposal", path=store)
        add_preference("status only note", scope="status", path=store)

        result = generate_and_save_deck(
            "proposal", "Ridgeline Site Services", FIXTURE_PROJECT, provider,
            prompts_dir=pdir, decks_dir=ddir, renderer=renderer,
            preferences_path=store,
            poll_interval=0.0, sleep=_no_sleep,
        )
        # Only the proposal-scoped note applies to a proposal run.
        assert result["applied_preferences"] == ["tighten the footer whitespace"], result
        # Claude Code leg: preferences arrive via the system-prompt kwarg.
        _, kwargs = calls[0]
        assert "tighten the footer whitespace" in kwargs["preferences"]
        assert "status only note" not in kwargs["preferences"]
        # Claude Design leg: the same note is inside the saved header, and gets
        # stripped back out of the content the renderer is asked to reproduce.
        assert "tighten the footer whitespace" in result["prompt"]
        from deck_renderer import strip_house_style_brief
        assert "tighten the footer whitespace" not in strip_house_style_brief(result["prompt"])


def test_apply_preferences_false_ignores_the_store():
    from preference_store import add_preference

    renderer, calls = _renderer_capturing_kwargs()
    provider = FixtureProvider.from_packet_file(PACKET_PATH)
    with tempfile.TemporaryDirectory() as pdir, tempfile.TemporaryDirectory() as ddir:
        store = os.path.join(pdir, "prefs.json")
        add_preference("should be ignored", scope="global", path=store)
        result = generate_and_save_deck(
            "proposal", "Ridgeline Site Services", FIXTURE_PROJECT, provider,
            prompts_dir=pdir, decks_dir=ddir, renderer=renderer,
            preferences_path=store, apply_preferences=False,
            poll_interval=0.0, sleep=_no_sleep,
        )
        assert result["applied_preferences"] == [], result
        _, kwargs = calls[0]
        assert kwargs["preferences"] == ""


# ---- wrong packet for the requested deck type ----------------------------
# Picking a fixture and then changing the deck-type dropdown is a realistic
# reviewer mistake, and it used to surface as a CoverageError listing forty-odd
# unmappable field paths — which reads as a broken renderer, not as wrong data.
# Every packet declares what it is, so the mismatch is caught from that.

def test_status_deck_against_a_proposal_packet_names_the_mismatch():
    provider = FixtureProvider.from_packet_file(PACKET_PATH)
    result = generate_deck_prompt(
        "status", "Ridgeline Site Services", STATUS_PROJECT, provider,
        poll_interval=0.0, sleep=_no_sleep,
    )
    assert result["status"] == "error", result
    assert result["code"] == "packet_type_mismatch", result
    # The message names both types, so the reviewer can see which way round it is.
    assert "project_status_check_in" in result["message"], result["message"]
    assert "project_planning_proposal" in result["message"], result["message"]
    # And the remediation names the deck type this packet IS for.
    assert "'proposal'" in result["remediation"], result["remediation"]
    assert "prompt" not in result, "a mismatch must not produce a prompt"


def test_proposal_deck_against_a_status_packet_names_the_mismatch():
    """Both directions, so the check is not keyed to one deck type."""
    provider = FixtureProvider.from_packet_file(STATUS_PACKET_PATH)
    result = generate_deck_prompt(
        "proposal", "Northwind", FIXTURE_PROJECT, provider,
        poll_interval=0.0, sleep=_no_sleep,
    )
    assert result["status"] == "error", result
    assert result["code"] == "packet_type_mismatch", result
    assert "'status'" in result["remediation"], result["remediation"]


def test_a_packet_type_mismatch_writes_no_files():
    """Same bar as every other escalation: nothing on disk, no number consumed."""
    provider = FixtureProvider.from_packet_file(PACKET_PATH)
    with tempfile.TemporaryDirectory() as pdir, tempfile.TemporaryDirectory() as ddir:
        result = generate_and_save_deck(
            "status", "Ridgeline Site Services", STATUS_PROJECT, provider,
            prompts_dir=pdir, decks_dir=ddir, render=False,
            poll_interval=0.0, sleep=_no_sleep,
        )
        assert result["code"] == "packet_type_mismatch", result
        assert os.listdir(pdir) == [], os.listdir(pdir)
        assert os.listdir(ddir) == [], os.listdir(ddir)


def test_a_packet_declaring_no_type_still_runs():
    """An undeclared packet_type is not evidence of a mismatch.

    The check reads what the packet says it is; a packet that says nothing (a
    hand-written one, or a future schema that drops the field) must stay usable
    rather than being refused on missing metadata.
    """
    with open(PACKET_PATH, encoding="utf-8") as f:
        text = f.read()
    stripped = "\n".join(
        line for line in text.splitlines() if not line.startswith("packet_type:")
    )
    assert "packet_type:" not in stripped.split("---")[1]
    provider = FixtureProvider.from_packet_markdown(stripped)
    result = generate_deck_prompt(
        "proposal", "Ridgeline Site Services", FIXTURE_PROJECT, provider,
        poll_interval=0.0, sleep=_no_sleep,
    )
    assert result["status"] == "ok", result


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


def test_a_saved_deck_has_its_commercial_blocks_written_from_the_data():
    # The defect this pins, found on the first live render (2026-09-23): the
    # save path read the placeholder map, which never leaves
    # `generate_deck_prompt`, so `normalise` always saw nothing and wrote
    # nothing, and every stub-rendered test still passed. Driven through the
    # real save path with a render that follows the slide 5 spec.
    def spec_following(prompt, **kwargs):
        return (
            "<!doctype html><html><body>"
            '<section class="slide"><div class="comm">'
            '<div class="deal-top"><div class="returns">model drew this</div></div>'
            '<div class="deal-terms" style="grid-template-columns:repeat(1,1fr)">'
            "model drew this</div></div></section>"
            "<p>(unconfirmed, see gaps)</p></body></html>"
        )

    provider = FixtureProvider.from_packet_file(PACKET_PATH)
    with tempfile.TemporaryDirectory() as pdir, tempfile.TemporaryDirectory() as ddir:
        result = generate_and_save_deck(
            "proposal", "Ridgeline Site Services", FIXTURE_PROJECT, provider,
            prompts_dir=pdir, decks_dir=ddir, renderer=spec_following,
            poll_interval=0.0, sleep=_no_sleep,
        )
        written = open(result["deck_path"], encoding="utf-8").read()
    assert result["commercial_blocks"]["written"] == ["return", "terms"], result
    assert "model drew this" not in written
    assert 'data-label="QofAI investment"' in written
    assert 'data-scenario="CONSERVATIVE"' in written
