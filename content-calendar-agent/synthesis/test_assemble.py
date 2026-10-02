"""Tests for calendar assembly (PRD.md build order item 9).

What is worth asserting about a module whose core is a model call.

The model's judgment is not testable here and should not be. What is testable
is everything wrapped around it, and that wrapper is where this module can be
wrong in ways nobody would notice:

  - The policy actually reaches the model. Every criterion, the cadence, and
    the alternatives count come from `sequencing_criteria.yaml`, so a prompt
    built from a temporary config with deliberately different rules and
    numbers must differ accordingly. A test against the real config cannot
    tell a correct read from a hardcoded string.
  - The answer is bounded by what exists. The output schema constrains
    `item_ref` to posts actually read off disk, and `window_from_payload`
    drops anything outside that set, so a model naming a post that does not
    exist cannot put it on Jordan's calendar.
  - Unschedulable content stays out. `anchor-08` announces the discipline's
    name as an unfilled placeholder, and the one thing worse than a thin
    calendar is a confident one with that post on it.
  - A failure is loud. The readers degrade to empty on purpose; assembly must
    not, because an empty calendar returned quietly looks exactly like a month
    with nothing to say.
  - A thin calendar is a success. The corpus is roughly eight items against a
    window that wants four to eight slots, so running short is the expected
    case and must not be reported as an error.

No test here makes a network call or needs a key. The model call is injected.
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import date
from pathlib import Path

import yaml

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "calendar_model"))

import assemble  # noqa: E402
import slots as slots_mod  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_TAXONOMY = {
    "threads": [
        {"id": "alpha-thread", "name": "Alpha"},
        {"id": "beta-thread", "name": "Beta"},
    ],
    "forms": [{"id": "historical-analogy", "description": "An analogy"}],
    "lenses": [
        {"id": "jordan", "name": "Private equity", "calendar": True, "v1": True},
        {"id": "casey", "name": "Operator", "calendar": True, "v1": False},
    ],
    "sequencing": {
        "monthly_anchor": {"source": "value-creation-briefing", "expected": "first week of month"},
        "approved_horizon_days": 14,
        "draft_horizon_days": 28,
    },
}

_CRITERIA = {
    "version": 1,
    "model": {"id": "test-model-id", "effort": "low", "max_tokens": 4096, "thinking": "adaptive"},
    "lens": None,
    "cadence": {"posts_per_week_min": 3, "posts_per_week_max": 5},
    "alternatives_per_slot": 4,
    "candidate_sources": [
        {"id": "content-atomizer", "filter_by_persona": True},
        {"id": "value-creation-briefing", "filter_by_persona": True, "include_briefings": False},
    ],
    "context": {"market_scan": {"enabled": False}, "narrative_files": [], "published_history": []},
    "criteria": [
        {"id": "unmistakable-rule", "rule": "Never schedule on a Tuesday.", "why": "Because pineapples."},
        {"id": "second-rule", "rule": "Always open with a question.", "why": "Because kumquats."},
    ],
}


def _write_yaml(directory: Path, name: str, payload: dict) -> Path:
    path = directory / name
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def _taxonomy(directory: Path) -> slots_mod.Taxonomy:
    return slots_mod.load_taxonomy(_write_yaml(directory, "threads.yaml", _TAXONOMY))


def _corpus(directory: Path) -> tuple[Path, Path]:
    """A minimal stand-in for the two dependency agents' real folders."""
    atomizer = directory / "content-atomizer" / "output"
    posts = atomizer / "posts"
    posts.mkdir(parents=True)
    for post_id, persona, body in (
        ("anchor-aa-the-first", "Jordan", "First body about alpha."),
        ("anchor-bb-the-second", "Jordan", "Second body about beta."),
        ("anchor-cc-the-operator", "Casey", "An operator-lens body."),
        ("anchor-zz-the-broken", "Jordan", "Announces the name as Placeholder."),
    ):
        (posts / f"{post_id}.post.md").write_text(
            f"# Post: {post_id}\n\n**Persona:** {persona}\n\n---\n\n{body}\n\n---\n", encoding="utf-8"
        )

    vcb = directory / "value-creation-briefing" / "drafts"
    vcb.mkdir(parents=True)
    (vcb / "blog_jordan_2026-06.md").write_text("# A Jordan Blog\n\nJordan blog body.\n", encoding="utf-8")
    (vcb / "blog_blake_2026-06.md").write_text("# A Blake Blog\n\nBlake blog body.\n", encoding="utf-8")
    (vcb / "briefing_draft_2026-06.md").write_text("# Briefing\n\nBriefing body.\n", encoding="utf-8")
    return atomizer, vcb


def _tags(directory: Path) -> Path:
    return _write_yaml(
        directory,
        "corpus_tags.yaml",
        {
            "version": 1,
            "items": {
                "content-atomizer/anchor-zz-the-broken": {
                    "threads": [],
                    "schedulable": False,
                    "note": "Unfilled placeholder in the body. Do not schedule.",
                }
            },
        },
    )


def _candidates(directory: Path, criteria: dict | None = None, lens: str = "jordan", excluded=None):
    atomizer, vcb = _corpus(directory)
    return assemble.gather_candidates(
        criteria or _CRITERIA, lens, atomizer, vcb, _tags(directory), excluded=excluded
    )


# ---------------------------------------------------------------------------
# Configuration reaches the model
# ---------------------------------------------------------------------------


def test_every_criterion_reaches_the_prompt():
    prompt = assemble.build_system_prompt(_CRITERIA)
    assert "Never schedule on a Tuesday." in prompt, "a configured rule did not reach the prompt"
    assert "Always open with a question." in prompt, "the second configured rule was dropped"
    assert "Because pineapples." in prompt, "a rule's reasoning did not reach the prompt"
    print("ok  every criterion in the config reaches the prompt, with its reasoning")


def test_cadence_and_alternatives_come_from_config():
    prompt = assemble.build_system_prompt(_CRITERIA)
    assert "3 to 5 posts per week" in prompt, f"cadence not read from config: {prompt!r}"
    assert "up to 4 swap candidates" in prompt, "swap-candidate count not read from config"

    lean = dict(_CRITERIA, cadence={"posts_per_week_min": 1, "posts_per_week_max": 2},
                alternatives_per_slot=0)
    other = assemble.build_system_prompt(lean)
    assert "1 to 2 posts per week" in other, "cadence did not follow a different config"
    assert "swap candidate" not in other, "zero alternatives still asked for swap candidates"
    print("ok  cadence and swap candidates follow the config rather than a constant")


def test_a_flat_cadence_is_stated_as_exact_rather_than_as_a_range():
    """Two-to-two is an instruction; "2 to 2 posts per week" is a puzzle.

    The owner set a flat two per week on 2026-09-07, so min and max are now
    equal in the real config. Phrasing that as a range invites the model to
    read it as approximate, which is the opposite of what a floor equal to a
    ceiling means.
    """
    flat = dict(_CRITERIA, cadence={"posts_per_week_min": 2, "posts_per_week_max": 2})
    prompt = assemble.build_system_prompt(flat)
    assert "exactly 2 posts per week" in prompt, f"flat cadence not stated exactly: {prompt!r}"
    assert "2 to 2" not in prompt, "a flat cadence was rendered as a range"
    print("ok  a floor equal to a ceiling is stated as an exact number")


def test_posting_days_reach_the_prompt_and_claim_precedence():
    """The canonical LinkedIn document is in context and says something else.

    Section 9 of `narrative/linkedin-post-voice-and-rubric.md` sets a
    three-day minimum and a Tuesday-or-Wednesday slot. The owner replaced
    both on 2026-09-07 with Monday and Thursday. Since that document is now
    read as context, the prompt has to say which one wins, or the model is
    left to reconcile two rule sets and will explain the conflict instead of
    scheduling against it. Asserted against a config carrying different days
    from the real one, so a hardcoded weekday cannot pass this.
    """
    configured = dict(_CRITERIA, posting_days=["Wednesday", "Saturday"])
    prompt = assemble.build_system_prompt(configured)
    assert "Wednesday or Saturday" in prompt, f"posting days not read from config: {prompt!r}"
    assert "Monday" not in prompt, "a real posting day leaked into a prompt built from a fake config"
    assert "overrides" in prompt, "the prompt does not say it overrides the canonical document"

    without = assemble.build_system_prompt(dict(_CRITERIA, posting_days=[]))
    assert "Posting days" not in without, "posting-day text appeared with no days configured"
    print("ok  posting days come from config and are stated as overriding the canonical rules")


def test_no_criterion_text_is_hardcoded():
    """The real policy and a fake one must produce entirely different rules."""
    real = assemble.build_system_prompt(assemble.load_criteria())
    fake = assemble.build_system_prompt(_CRITERIA)
    for phrase in ("no-repeat-ideas", "Deloitte", "pyramid post"):
        assert phrase not in fake, f"{phrase!r} leaked into a prompt built from a different config"
    assert "Never schedule on a Tuesday." not in real, "a test rule leaked into the real prompt"
    print("ok  prompt text carries no criterion the config did not supply")


def test_an_empty_policy_is_refused():
    assert assemble.validate_criteria({}), "an empty policy was accepted"
    assert assemble.validate_criteria(dict(_CRITERIA, criteria=[])), "a policy with no rules was accepted"
    assert assemble.validate_criteria(dict(_CRITERIA, cadence=None)), "a policy with no cadence was accepted"
    assert assemble.validate_criteria(
        dict(_CRITERIA, cadence={"posts_per_week_min": 5, "posts_per_week_max": 2})
    ), "an inverted cadence range was accepted"
    assert not assemble.validate_criteria(_CRITERIA), "a valid policy was rejected"
    print("ok  a policy that would let the model choose its own rules is refused")


def test_a_lens_override_changes_only_that_lens():
    """Per-lens cadence and days reach that lens's prompt and no other.

    Added 2026-09-23 so the three founders stop posting on the same morning.
    Built from a fake config, so a hardcoded weekday cannot pass.
    """
    configured = dict(
        _CRITERIA,
        posting_days=["Friday"],
        lenses={"ops": {"posting_days": ["Saturday"],
                        "cadence": {"posts_per_week_min": 1, "posts_per_week_max": 1}}},
    )
    assert not assemble.validate_criteria(configured), "a valid lens override was refused"
    ops = assemble.build_system_prompt(assemble.criteria_for_lens(configured, "ops"))
    other = assemble.build_system_prompt(assemble.criteria_for_lens(configured, "deals"))
    assert "on a Saturday" in ops and "Friday" not in ops, f"override days not applied: {ops!r}"
    assert "exactly 1 post per week" in ops, "override cadence not applied"
    assert "on a Friday" in other and "Saturday" not in other, "an override leaked into another lens"
    assert configured["posting_days"] == ["Friday"], "applying an override mutated the shared policy"
    print("ok  a lens override sets that lens's days and cadence and leaves the others alone")


def test_a_lens_override_cannot_fork_the_policy():
    """Only cadence and days may differ by lens, and a bad cadence is still refused."""
    forked = dict(_CRITERIA, lenses={"ops": {"criteria": [{"rule": "Anything goes."}]}})
    assert assemble.validate_criteria(forked), "a lens was allowed to override the rules"
    inverted = dict(_CRITERIA, lenses={"ops": {"cadence": {"posts_per_week_min": 3,
                                                           "posts_per_week_max": 1}}})
    assert assemble.validate_criteria(inverted), "an inverted per-lens cadence was accepted"
    print("ok  a lens override is limited to cadence and posting days")


def test_the_real_policy_is_usable():
    """Guards against shipping a config the module cannot actually run."""
    criteria = assemble.load_criteria()
    problems = assemble.validate_criteria(criteria)
    assert not problems, f"the checked-in sequencing policy is unusable: {problems}"
    print("ok  the checked-in sequencing_criteria.yaml validates")


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------


def test_unschedulable_content_is_excluded():
    with tempfile.TemporaryDirectory() as tmp:
        excluded: list[str] = []
        refs = {c.item_ref for c in _candidates(Path(tmp), excluded=excluded)}
        assert "content-atomizer/anchor-zz-the-broken" not in refs, (
            "an item marked schedulable: false was offered to the model"
        )
        assert any("anchor-zz-the-broken" in reason for reason in excluded), (
            "the exclusion happened silently instead of being reported"
        )
        assert any("placeholder" in reason.lower() for reason in excluded), (
            "the exclusion did not carry the note explaining why"
        )
    print("ok  content marked unschedulable is excluded, and the reason is reported")


def test_off_lens_personas_are_excluded():
    with tempfile.TemporaryDirectory() as tmp:
        excluded: list[str] = []
        refs = {c.item_ref for c in _candidates(Path(tmp), excluded=excluded)}
        assert "content-atomizer/anchor-cc-the-operator" not in refs, "a Casey post entered the Jordan lens"
        assert "value-creation-briefing/blog_blake_2026-06" not in refs, "a Blake blog entered the Jordan lens"
        assert "content-atomizer/anchor-aa-the-first" in refs, "a Jordan post was wrongly excluded"
        assert "value-creation-briefing/blog_jordan_2026-06" in refs, "a Jordan blog was wrongly excluded"
    print("ok  only the calendar lens's own persona is schedulable")


def test_a_different_lens_selects_different_content():
    with tempfile.TemporaryDirectory() as tmp:
        refs = {c.item_ref for c in _candidates(Path(tmp), lens="casey")}
        assert "content-atomizer/anchor-cc-the-operator" in refs, "the Casey lens found no Casey post"
        assert "content-atomizer/anchor-aa-the-first" not in refs, "the Casey lens picked up a Jordan post"
    print("ok  the lens selects the corpus, so a second calendar is a config change")


def test_briefings_are_not_slots_unless_configured():
    with tempfile.TemporaryDirectory() as tmp:
        refs = {c.item_ref for c in _candidates(Path(tmp))}
        assert "value-creation-briefing/briefing_draft_2026-06" not in refs, (
            "a monthly briefing was offered as a post"
        )
    with tempfile.TemporaryDirectory() as tmp:
        opted_in = dict(_CRITERIA, candidate_sources=[
            {"id": "value-creation-briefing", "filter_by_persona": False, "include_briefings": True}
        ])
        refs = {c.item_ref for c in _candidates(Path(tmp), criteria=opted_in)}
        assert "value-creation-briefing/briefing_draft_2026-06" in refs, (
            "opting briefings in via config had no effect"
        )
    print("ok  briefings are anchors rather than slots, and that is configurable")


def test_a_missing_sibling_folder_does_not_crash():
    with tempfile.TemporaryDirectory() as tmp:
        got = assemble.gather_candidates(
            _CRITERIA, "jordan", Path(tmp) / "nope", Path(tmp) / "also-nope", _tags(Path(tmp))
        )
        assert got == [], "a missing dependency folder produced candidates out of nowhere"
    print("ok  a missing sibling folder reports and yields nothing, rather than raising")


# ---------------------------------------------------------------------------
# The answer is bounded by what exists
# ---------------------------------------------------------------------------


def test_schema_bounds_item_ref_to_real_content():
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        taxonomy = _taxonomy(directory)
        candidates = _candidates(directory)
        schema = assemble.build_output_schema(candidates, taxonomy, 4)

        slot = schema["properties"]["slots"]["items"]
        assert slot["properties"]["item_ref"]["enum"] == [c.item_ref for c in candidates], (
            "the schema did not bound item_ref to the candidates actually read"
        )
        assert set(slot["properties"]["threads"]["items"]["enum"]) == set(taxonomy.thread_ids), (
            "threads were not bounded by the taxonomy"
        )
        alternatives = slot["properties"]["alternatives"]
        assert "maxItems" not in alternatives, (
            "maxItems is back in the schema; structured outputs reject it with a 400"
        )
        assert "4" in alternatives["description"], (
            "the alternatives cap did not reach the schema description from config"
        )
        assert schema["additionalProperties"] is False, "the schema allows unknown top-level keys"
    print("ok  the output schema bounds the answer to content and vocabulary that exist")


def test_a_malformed_responds_to_ref_is_dropped_not_fatal():
    """The first live run failed a good window over one prose reference.

    `responds_to` cannot be an enum in the schema, because a legitimate
    reference may name an already-published post that is not a candidate. So
    it was the only model-supplied field arriving unfiltered, and
    `bad-responds-to-ref` is an error rather than a warning: one loose string
    rejected an otherwise sound five-slot month. Dropping the reference costs
    one edge in the narrative graph. Failing the window costs the month.
    """
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        taxonomy = _taxonomy(directory)
        candidates = _candidates(directory)
        chosen = candidates[0].item_ref
        other = candidates[1].item_ref
        payload = {
            "arc": "An arc.",
            "supply_note": "",
            "slots": [
                {
                    "date": "2026-09-14",
                    "item_ref": chosen,
                    "threads": ["alpha-thread"],
                    "forms": [],
                    "responds_to": [
                        "the published adoption-gap post",  # prose, not a ref
                        other,                              # a real reference
                        chosen,                             # answers itself
                        other,                              # duplicate
                        "",                                 # empty
                    ],
                    "rationale": "Real.",
                    "alternatives": [],
                }
            ],
        }
        window = assemble.window_from_payload(
            payload, taxonomy, "jordan", date(2026, 9, 1), candidates
        )
        assert len(window.slots) == 1, "the slot was dropped rather than its bad reference"
        assert window.slots[0].responds_to == [other], (
            f"expected only the well-formed reference to survive, got "
            f"{window.slots[0].responds_to}"
        )
        issues = slots_mod.validate_window(
            window, taxonomy, item_refs={c.item_ref for c in candidates}
        )
        bad = [i for i in slots_mod.errors(issues)
               if i.code in {"bad-responds-to-ref", "responds-to-self"}]
        assert not bad, f"the window still fails validation on its references: {bad}"
    print("ok  a malformed responds_to reference is dropped rather than failing the window")


def test_the_schema_carries_no_keyword_structured_outputs_rejects():
    """The 400 this guards against was found by running the thing, not by reading it.

    Structured outputs support a subset of JSON Schema, and a rejected keyword
    fails the whole request at the API rather than degrading. `maxItems` on the
    alternatives array is what actually happened on 2026-09-07 ("For 'array'
    type, property 'maxItems' is not supported"). The SDK strips unsupported
    keywords only for schemas generated from a Pydantic model, and this one is
    a dict, so nothing between here and the API will save us. Walking the whole
    schema rather than the one field that broke, because the next constraint
    someone adds will be somewhere else.
    """
    unsupported = {
        "maxItems", "minItems", "uniqueItems", "contains", "maxContains", "minContains",
        "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
        "minLength", "maxLength", "pattern",
        "minProperties", "maxProperties", "patternProperties", "propertyNames",
        "if", "then", "else", "not", "oneOf", "dependentSchemas",
    }

    def walk(node, path="$"):
        found = []
        if isinstance(node, dict):
            for key, value in node.items():
                if key in unsupported:
                    found.append(f"{path}.{key}")
                # `properties` keys are field names, not schema keywords, so a
                # calendar field legitimately called "pattern" is not a finding.
                child = f"{path}.{key}"
                if key == "properties" and isinstance(value, dict):
                    for name, sub in value.items():
                        found += walk(sub, f"{child}[{name}]")
                else:
                    found += walk(value, child)
        elif isinstance(node, list):
            for index, item in enumerate(node):
                found += walk(item, f"{path}[{index}]")
        return found

    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        schema = assemble.build_output_schema(_candidates(directory), _taxonomy(directory), 4)
        offenders = walk(schema)
        assert not offenders, (
            "the output schema carries keywords structured outputs rejects, so every "
            f"assembly would 400 before the model reads anything: {offenders}"
        )
    print("ok  the output schema uses only keywords structured outputs accepts")


def test_the_alternatives_cap_is_enforced_on_the_way_in():
    """The schema states the cap; this is the half that cannot be ignored.

    Since `maxItems` cannot ride in the schema, a model that returns more
    alternatives than the config allows produces a perfectly valid response.
    Trimming has to happen here or the number in `sequencing_criteria.yaml`
    becomes a suggestion.
    """
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        taxonomy = _taxonomy(directory)
        candidates = _candidates(directory)
        chosen = candidates[0].item_ref
        others = [c.item_ref for c in candidates if c.item_ref != chosen]
        # The cap under test is 1, not the real config's 2, because the shared
        # fixture holds three Jordan-lens items and other tests depend on that
        # shape. Over-filling by one exercises the same trim.
        assert len(others) >= 2, "fixture corpus too small to over-fill the cap"

        payload = {
            "arc": "An arc.",
            "supply_note": "",
            "slots": [
                {
                    "date": "2026-09-07",
                    "item_ref": chosen,
                    "threads": [],
                    "forms": [],
                    "responds_to": [],
                    "rationale": "Real.",
                    "alternatives": [{"item_ref": ref, "why": "Also fits."} for ref in others[:2]],
                }
            ],
        }

        capped = assemble.window_from_payload(
            payload, taxonomy, "jordan", date(2026, 9, 1), candidates, max_alternatives=1
        )
        kept = capped.slots[0].extra.get("alternatives") or []
        assert len(kept) == 1, f"the cap was not enforced, kept {len(kept)}"
        assert [a["item_ref"] for a in kept] == others[:1], (
            "trimming did not keep the model's own ordering, so the best options "
            "are not the ones that survive"
        )

        uncapped = assemble.window_from_payload(
            payload, taxonomy, "jordan", date(2026, 9, 1), candidates
        )
        assert len(uncapped.slots[0].extra.get("alternatives") or []) == 2, (
            "a caller passing no cap had one applied anyway"
        )
    print("ok  the per-slot alternatives cap is enforced when the schema cannot carry it")


def test_summary_and_rationale_both_survive_the_payload():
    """The card needs what a post says as well as why it is on that date."""
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        taxonomy = _taxonomy(directory)
        candidates = _candidates(directory)
        payload = {
            "arc": "One sentence.",
            "slots": [
                {
                    "date": "2026-09-07",
                    "item_ref": candidates[0].item_ref,
                    "threads": [],
                    "forms": [],
                    "responds_to": [],
                    "summary": "What the post argues.",
                    "rationale": "Why it is on this date.",
                    "alternatives": [],
                }
            ],
        }
        window = assemble.window_from_payload(
            payload, taxonomy, "jordan", date(2026, 9, 1), candidates
        )
        slot = window.slots[0]
        assert slot.summary == "What the post argues.", slot.summary
        assert slot.rationale == "Why it is on this date.", slot.rationale
        assert slot.summary != slot.rationale, (
            "the two fields answer different questions and must not be filled from one"
        )
        assert "summary" in assemble.build_output_schema(
            candidates, taxonomy, 0
        )["properties"]["slots"]["items"]["required"], (
            "the model can omit the summary, so a card can come back with nothing on it"
        )
    print("ok  a slot carries what the post says and why it is on that date, separately")


def test_length_limits_come_from_config_and_are_reported_not_enforced():
    """Antonio's whole 2026-09-15 complaint was length, so the bound is policy.

    Reported rather than truncated: a rationale cut mid-sentence is a worse
    card than a long one, and a model that has stopped honouring the bound
    is something to see.
    """
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        taxonomy = _taxonomy(directory)
        candidates = _candidates(directory)

        assert assemble.resolve_limits({}) == {
            "summary_words": 0, "rationale_words": 0, "arc_sentences": 0
        }, "a policy naming no limits had numbers invented for it in Python"
        bounded = dict(_CRITERIA, limits={"summary_words": 5, "arc_sentences": 1})
        resolved = assemble.resolve_limits(bounded)
        assert resolved["summary_words"] == 5 and resolved["rationale_words"] == 0, resolved

        prompt = assemble.build_system_prompt(bounded)
        assert "at most 5 words" in prompt, "the bound never reached the model"
        assert "at most 1 sentences" in prompt
        assert "at most" not in assemble.build_system_prompt(_CRITERIA), (
            "a policy with no limits block still told the model about a bound"
        )

        long_summary = "one two three four five six seven eight"
        payload = {
            "arc": "First sentence. Second sentence. Third.",
            "slots": [
                {
                    "date": "2026-09-07",
                    "item_ref": candidates[0].item_ref,
                    "threads": [],
                    "forms": [],
                    "responds_to": [],
                    "summary": long_summary,
                    "rationale": "Short.",
                    "alternatives": [],
                }
            ],
        }
        window = assemble.window_from_payload(
            payload, taxonomy, "jordan", date(2026, 9, 1), candidates, limits=resolved
        )
        assert window.slots[0].summary == long_summary, (
            "an over-long summary was truncated; it is meant to be reported whole"
        )
        assert window.arc.startswith("First sentence."), "an over-long arc was truncated"
    print("ok  length limits are read from config, stated to the model, and reported not cut")


def test_an_invented_post_is_dropped():
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        taxonomy = _taxonomy(directory)
        candidates = _candidates(directory)
        payload = {
            "arc": "An arc.",
            "supply_note": "",
            "slots": [
                {
                    "date": "2026-09-07",
                    "item_ref": "content-atomizer/anchor-aa-the-first",
                    "threads": ["alpha-thread", "not-a-thread"],
                    "forms": [],
                    "responds_to": [],
                    "rationale": "Real.",
                    "alternatives": [],
                },
                {
                    "date": "2026-09-14",
                    "item_ref": "content-atomizer/anchor-hallucinated",
                    "threads": [],
                    "forms": [],
                    "responds_to": [],
                    "rationale": "Invented.",
                    "alternatives": [],
                },
            ],
        }
        window = assemble.window_from_payload(payload, taxonomy, "jordan", date(2026, 9, 1), candidates)
        refs = [s.item_ref for s in window.slots]
        assert "content-atomizer/anchor-hallucinated" not in refs, (
            "a post that does not exist reached the calendar"
        )
        assert len(window.slots) == 1, f"expected one surviving slot, got {refs}"
        assert window.slots[0].threads == ["alpha-thread"], (
            "an unknown thread id survived into the window"
        )
    print("ok  a post or thread the model invented is dropped rather than scheduled")


def test_alternatives_are_bounded_and_never_duplicate_the_pick():
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        taxonomy = _taxonomy(directory)
        candidates = _candidates(directory)
        chosen = candidates[0].item_ref
        payload = {
            "arc": "An arc.",
            "supply_note": "",
            "slots": [{
                "date": "2026-09-07",
                "item_ref": chosen,
                "threads": [],
                "forms": [],
                "responds_to": [],
                "rationale": "Because.",
                "alternatives": [
                    {"item_ref": chosen, "why": "the same post again"},
                    {"item_ref": candidates[1].item_ref, "why": "a real alternative"},
                    {"item_ref": "content-atomizer/anchor-invented", "why": "not real"},
                ],
            }],
        }
        window = assemble.window_from_payload(payload, taxonomy, "jordan", date(2026, 9, 1), candidates)
        alternatives = window.slots[0].extra["alternatives"]
        refs = [a["item_ref"] for a in alternatives]
        assert chosen not in refs, "a slot offered its own post as an alternative to itself"
        assert "content-atomizer/anchor-invented" not in refs, "an invented alternative survived"
        assert refs == [candidates[1].item_ref], f"unexpected alternatives: {refs}"
    print("ok  alternatives stay real and never repeat the scheduled post")


def test_a_produced_window_validates_against_the_real_schema():
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        taxonomy = _taxonomy(directory)
        candidates = _candidates(directory)
        payload = {
            "arc": "The month argues one thing and then its consequence.",
            "supply_note": "",
            "slots": [
                {
                    "date": "2026-09-07",
                    "item_ref": candidates[0].item_ref,
                    "threads": ["alpha-thread"],
                    "forms": ["historical-analogy"],
                    "responds_to": [],
                    "rationale": "Opens the month.",
                    "alternatives": [],
                },
                {
                    "date": "2026-09-14",
                    "item_ref": candidates[1].item_ref,
                    "threads": ["beta-thread"],
                    "forms": [],
                    "responds_to": [candidates[0].item_ref],
                    "rationale": "Builds on the opener.",
                    "alternatives": [],
                },
            ],
        }
        window = assemble.window_from_payload(payload, taxonomy, "jordan", date(2026, 9, 1), candidates)
        issues = slots_mod.validate_window(
            window, taxonomy, item_refs={c.item_ref for c in candidates}, today="2026-09-01"
        )
        errors = slots_mod.errors(issues)
        assert not errors, f"an assembled window failed the shared schema: {[str(e) for e in errors]}"

        round_tripped = slots_mod.loads(slots_mod.dumps(window))
        assert round_tripped.to_dict() == window.to_dict(), "the window did not survive a round trip"
        assert round_tripped.slots[0].extra == window.slots[0].extra, "slot extras were lost"
    print("ok  an assembled window passes the shared schema and survives a round trip")


def test_assembly_never_marks_a_slot_approved():
    """The one thing this build exists to prevent is a forged sign-off.

    A fresh window is all drafts, so the shared validator flags every
    near-term slot as unapproved. That is the approval queue's worklist, not
    a defect, and the tempting fix (mark them approved so validation passes)
    would publish on Jordan's account without his consent.
    """
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        taxonomy = _taxonomy(directory)
        candidates = _candidates(directory)
        payload = {
            "arc": "An arc.",
            "supply_note": "",
            "slots": [{
                "date": "2026-09-03", "item_ref": candidates[0].item_ref, "threads": [], "forms": [],
                "responds_to": [], "rationale": "Soon.", "alternatives": [],
            }],
        }
        window = assemble.window_from_payload(payload, taxonomy, "jordan", date(2026, 9, 1), candidates)
        assert all(s.approval == slots_mod.DRAFT for s in window.slots), (
            "assembly marked a slot approved; nothing may claim a human's sign-off"
        )

        issues = slots_mod.validate_window(
            window, taxonomy, item_refs={c.item_ref for c in candidates}, today="2026-09-01"
        )
        assert not slots_mod.errors(issues), (
            f"a window of drafts reported errors: {[str(e) for e in slots_mod.errors(issues)]}"
        )
        for issue in issues:
            assert "approv" not in issue.code, (
                f"{issue.code}: a slot nobody has decided on is not a finding (2026-09-19)"
            )
    print("ok  assembly leaves every slot a draft, and a window of drafts is valid")


def test_slot_ids_are_unique_and_ordered_by_date():
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        taxonomy = _taxonomy(directory)
        candidates = _candidates(directory)
        payload = {
            "arc": "An arc.",
            "supply_note": "",
            "slots": [
                {"date": "2026-09-21", "item_ref": candidates[1].item_ref, "threads": [], "forms": [],
                 "responds_to": [], "rationale": "Later.", "alternatives": []},
                {"date": "2026-09-07", "item_ref": candidates[0].item_ref, "threads": [], "forms": [],
                 "responds_to": [], "rationale": "Earlier.", "alternatives": []},
            ],
        }
        window = assemble.window_from_payload(payload, taxonomy, "jordan", date(2026, 9, 1), candidates)
        ids = [s.slot_id for s in window.slots]
        assert len(set(ids)) == len(ids), f"slot ids collided: {ids}"
        assert [s.date for s in window.slots] == ["2026-09-07", "2026-09-21"], (
            "slots were not ordered by date regardless of the order the model returned them"
        )
    print("ok  slot ids are unique and slots come back in date order")


# ---------------------------------------------------------------------------
# Failure is loud; thinness is not a failure
# ---------------------------------------------------------------------------


def test_no_candidates_raises_rather_than_returning_an_empty_calendar():
    with tempfile.TemporaryDirectory() as tmp:
        taxonomy = _taxonomy(Path(tmp))
        try:
            assemble.assemble(
                _CRITERIA, taxonomy, "jordan", date(2026, 9, 1), [], assemble.Context(),
                caller=lambda *a, **k: {"arc": "", "slots": [], "supply_note": ""},
            )
        except assemble.AssemblyError as exc:
            assert "nothing to sequence" in str(exc), f"unhelpful message: {exc}"
        else:
            raise AssertionError("an empty corpus produced a calendar instead of an error")
    print("ok  an empty corpus is an error, not a quietly empty calendar")


def test_a_thin_calendar_is_a_success_that_says_so():
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        taxonomy = _taxonomy(directory)
        candidates = _candidates(directory)
        payload = {
            "arc": "A short month.",
            "supply_note": "Only two posts clear the bar; the corpus cannot fill four weeks.",
            "slots": [{
                "date": "2026-09-07", "item_ref": candidates[0].item_ref, "threads": [], "forms": [],
                "responds_to": [], "rationale": "The only strong opener.", "alternatives": [],
            }],
        }
        window = assemble.assemble(
            _CRITERIA, taxonomy, "jordan", date(2026, 9, 1), candidates, assemble.Context(),
            caller=lambda *a, **k: payload,
        )
        assert len(window.slots) == 1, "a deliberately thin calendar was altered"
        assert "cannot fill" in window.extra.get("supply_note", ""), (
            "the supply note did not survive onto the window"
        )
    print("ok  a thin calendar succeeds and carries its own explanation")


def test_an_unexpected_sdk_error_becomes_a_sentence_not_a_traceback():
    """Regression. Found by running the module with no credentials set.

    The SDK raises a bare `TypeError` from header validation at request time
    when it cannot resolve a credential. That is neither an `anthropic.*`
    exception nor something the constructor raises, so a chain of specific
    handlers let the commonest setup mistake in this module escape as a
    traceback with exit 0. Any exception out of the call must arrive as an
    AssemblyError.
    """
    def explode(*_args, **_kwargs):
        raise TypeError("Could not resolve authentication method.")

    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        taxonomy = _taxonomy(directory)
        candidates = _candidates(directory)
        try:
            assemble.assemble(
                _CRITERIA, taxonomy, "jordan", date(2026, 9, 1), candidates,
                assemble.Context(), caller=explode,
            )
        except assemble.AssemblyError:
            pass
        except TypeError:
            raise AssertionError("a raw TypeError escaped instead of an AssemblyError")
        else:
            raise AssertionError("a failing model call produced a calendar")
    print("ok  an unexpected SDK error arrives as a reportable failure, not a traceback")


def test_a_bad_payload_does_not_produce_a_confident_window():
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        taxonomy = _taxonomy(directory)
        candidates = _candidates(directory)
        window = assemble.window_from_payload(
            {"arc": "", "slots": "not-a-list"}, taxonomy, "jordan", date(2026, 9, 1), candidates
        )
        assert window.slots == [], "a malformed slots field produced slots"
        issues = slots_mod.validate_window(window, taxonomy, item_refs={c.item_ref for c in candidates})
        assert issues, "a window with no arc and no slots reported no issues at all"
    print("ok  a malformed answer yields an empty window the validator objects to")


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------


def test_a_standing_window_is_shown_to_the_model_with_its_approvals():
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        taxonomy = _taxonomy(directory)
        candidates = _candidates(directory)
        standing = slots_mod.new_window("jordan-standing", "jordan", date(2026, 9, 1), taxonomy, arc="Standing.")
        standing.slots.append(
            slots_mod.Slot(
                slot_id="jordan-standing-01",
                date="2026-09-07",
                item_ref=candidates[0].item_ref,
                lens="jordan",
                approval=slots_mod.APPROVED,
                rationale="Already signed off.",
            )
        )
        content = assemble.build_user_content(
            candidates, assemble.Context(), "2026-09-01", "2026-09-28", "Private equity", standing
        )
        assert "The standing calendar" in content, "the standing window was not shown to the model"
        assert "jordan-standing-01" in content, "the standing slot did not reach the model"
        assert '"approval": "approved"' in content, (
            "the standing window went over without its approval state, so the model "
            "cannot tell which slots are fixed"
        )
    print("ok  a refresh shows the standing calendar and which slots are already approved")


def test_context_failures_are_reported_to_the_model_not_hidden():
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        criteria = dict(_CRITERIA, context={
            "market_scan": {"enabled": False},
            "narrative_files": [{"path": "narrative/does-not-exist.md", "label": "A missing document"}],
            "published_history": [],
        })
        context = assemble.gather_context(criteria, agent_root=directory)
        assert context.notes, "an unreadable context file produced no note"
        assert any("A missing document" in note for note in context.notes), (
            "the note did not name the document that went missing"
        )
        content = assemble.build_user_content(
            _candidates(directory), context, "2026-09-01", "2026-09-28", "Private equity"
        )
        assert "could not be read" in content, (
            "the model was not told a context input was missing, so it would sequence "
            "as though it had read everything"
        )
    print("ok  context that could not be read is declared to the model rather than hidden")


def test_briefing_insights_lead_and_the_rss_scan_catches_a_failed_read():
    """Added 2026-09-22, when Robin's scored insights replaced the RSS scan.

    Two things have to hold. When the insights come back, the RSS feeds are
    never read, or the arc gets two outside worlds instead of one better one.
    When they do not, the RSS scan fills in and the model is told why, so a
    lost read never leaves the arc thinner than it was before the swap.
    """
    import briefing_insights_reader
    import market_scan_reader
    from market_scan_reader import MarketScanItem

    insight = MarketScanItem(item_id="i/1", id_basis="airtable", source_id="vcb",
                             source_name="AI Daily Brief (via Value Creation Briefing)",
                             title="An insight")
    feed = MarketScanItem(item_id="f/1", id_basis="url", source_id="rss",
                          source_name="PE Hub", title="A feed item")
    rss_calls = []
    originals = (briefing_insights_reader.read_briefing_insights, market_scan_reader.read_market_scan)
    market_scan_reader.read_market_scan = lambda **k: rss_calls.append(k) or [feed]
    criteria = dict(_CRITERIA, context={
        "briefing_insights": {"enabled": True, "base_id": "appX", "table": "T"},
        "market_scan": {"enabled": True},
        "narrative_files": [],
        "published_history": [],
    })
    try:
        with tempfile.TemporaryDirectory() as tmp:
            briefing_insights_reader.read_briefing_insights = lambda *a, **k: ([insight], "")
            context = assemble.gather_context(criteria, agent_root=Path(tmp))
            assert [i.title for i in context.market_items] == ["An insight"], context.market_items
            assert not rss_calls, "the RSS scan was read even though the insights came back"
            assert not context.notes, context.notes

            briefing_insights_reader.read_briefing_insights = (
                lambda *a, **k: ([], "Airtable refused the token (HTTP 403) for appX/T")
            )
            context = assemble.gather_context(criteria, agent_root=Path(tmp))
            assert [i.title for i in context.market_items] == ["A feed item"], context.market_items
            assert any("refused the token" in n and "RSS" in n for n in context.notes), context.notes
    finally:
        briefing_insights_reader.read_briefing_insights, market_scan_reader.read_market_scan = originals
    print("ok  briefing insights lead, and the RSS scan fills in when they cannot be read")


# ---------------------------------------------------------------------------
# Posts already on another calendar (added 2026-09-18)
# ---------------------------------------------------------------------------

_PLACED_POLICY = {
    "already_placed": {
        "enabled": True,
        "running_window_states": ["draft", "approved", "published"],
        "past_window_states": ["approved", "published"],
        "empty_window_arc": "Nothing new to place yet. All {count} posts sit on {windows}.",
        "empty_window_supply_note": "No model call. {count} posts already on {windows}.",
    }
}


def _placed_window(root: Path, month: str, start: str, end: str, refs_states) -> Path:
    window = slots_mod.CalendarWindow(
        window_id=f"jordan-{start}", lens="jordan", start_date=start, end_date=end,
        slots=[slots_mod.Slot(slot_id=f"jordan-{start}-{n:02d}", date=start, item_ref=ref,
                              lens="jordan", approval=state)
               for n, (ref, state) in enumerate(refs_states, 1)],
    )
    return slots_mod.write_window(root / slots_mod.window_filename("jordan", month), window)


def test_a_post_on_another_running_calendar_is_not_a_candidate():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _placed_window(root, "2026-10", "2026-10-01", "2026-10-31", [
            ("content-atomizer/a", "draft"),
            ("content-atomizer/b", "approved"),
            ("content-atomizer/c", "rejected"),
        ])
        _placed_window(root, "2026-08", "2026-08-01", "2026-08-31", [
            ("content-atomizer/d", "draft"),
            ("content-atomizer/e", "approved"),
        ])
        placed = assemble.placed_elsewhere(_PLACED_POLICY, "jordan", root, "2026-10-16")
        assert set(placed) == {"content-atomizer/a", "content-atomizer/b",
                               "content-atomizer/e"}, placed
        assert placed["content-atomizer/a"] == "jordan-2026-10-01"
        # The window being built is never "elsewhere".
        skip = root / "window-jordan-2026-10.json"
        assert set(assemble.placed_elsewhere(_PLACED_POLICY, "jordan", root, "2026-10-16",
                                             skip=[skip])) == {"content-atomizer/e"}
        # Off means off.
        assert assemble.placed_elsewhere({}, "jordan", root, "2026-10-16") == {}
    print("ok  a running calendar holds its posts in any state but declined; a past one "
          "only the ones that went out")


def test_an_approval_in_the_store_counts_on_a_past_window():
    """Approvals live in the store, not the file, so the file alone would say draft."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _placed_window(root, "2026-08", "2026-08-01", "2026-08-31",
                       [("content-atomizer/d", "draft")])
        assert assemble.placed_elsewhere(_PLACED_POLICY, "jordan", root, "2026-10-16") == {}
        import state_store
        state_store.record_state("approvals", [{
            "action": "approved", "entry_key": "jordan-2026-08-01-01",
            "value": {"state": "approved", "by": "test"},
        }], root=root)
        decisions = state_store.states_of(state_store.latest_decisions(root=root))
        assert decisions.get("jordan-2026-08-01-01") == "approved", decisions
        placed = assemble.placed_elsewhere(_PLACED_POLICY, "jordan", root, "2026-10-16")
        assert "content-atomizer/d" in placed, "a store approval was ignored"
    print("ok  a past window's approvals are read from the store, as the page reads them")


def _run_main_with(candidates, root: Path, out: Path, assembled: list) -> int:
    originals = (assemble.gather_candidates, assemble.gather_context, assemble.assemble,
                 assemble.load_criteria)
    criteria = dict(_CRITERIA, **_PLACED_POLICY)

    def fake_assemble(criteria, taxonomy, lens, start, cands, context, standing=None, **_):
        assembled.append([c.item_ref for c in cands])
        return slots_mod.new_window(f"{lens}-{start}", lens, start, taxonomy, arc="an arc")

    assemble.gather_candidates = lambda *a, **k: list(candidates)
    assemble.gather_context = lambda *a, **k: assemble.Context()
    assemble.assemble = fake_assemble
    assemble.load_criteria = lambda *a, **k: criteria
    try:
        threads = root / "threads.yaml"
        threads.write_text(yaml.safe_dump(_TAXONOMY))
        return assemble.main(["--threads", str(threads), "--lens", "jordan",
                              "--start", "2026-11-01", "--out", str(out),
                              "--state-root", str(root), "--today", "2026-10-16"])
    finally:
        (assemble.gather_candidates, assemble.gather_context, assemble.assemble,
         assemble.load_criteria) = originals


def _cand(ref):
    return assemble.Candidate(item_ref=ref, source="content-atomizer", persona="jordan",
                              title=ref, text="text", word_count=1, source_path=ref)


def test_a_fully_placed_corpus_writes_an_honest_empty_month_without_a_model_call():
    """The expected November: every post is already in October, and that is correct."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _placed_window(root, "2026-10", "2026-10-01", "2026-10-30",
                       [("content-atomizer/a", "draft"), ("content-atomizer/b", "draft")])
        out = root / "window-jordan-2026-11.json"
        assembled: list = []
        code = _run_main_with([_cand("content-atomizer/a"), _cand("content-atomizer/b")],
                              root, out, assembled)
        assert code == 0, "an empty month because everything is placed is a success, not red"
        assert not assembled, "the model path was reached with nothing to sequence"
        window = slots_mod.read_window(out)
        assert window is not None and window.slots == [], "November double-booked October"
        assert window.arc.startswith("Nothing new to place yet"), window.arc
        assert "2" in window.arc and "jordan-2026-10-01" in window.arc, window.arc
        for word in ("error", "failed", "failure", "could not"):
            assert word not in window.arc.lower(), f"the arc reads as a failure: {window.arc}"
        assert window.extra.get("assembled_without_model") is True
        assert set(window.extra.get("already_placed") or {}) == {"content-atomizer/a",
                                                                 "content-atomizer/b"}
    print("ok  a corpus already on the calendar makes an empty month that says why, "
          "with no model call")


def test_only_unplaced_posts_reach_the_model():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _placed_window(root, "2026-10", "2026-10-01", "2026-10-30",
                       [("content-atomizer/a", "draft")])
        assembled: list = []
        code = _run_main_with([_cand("content-atomizer/a"), _cand("content-atomizer/new")],
                              root, root / "window-jordan-2026-11.json", assembled)
        assert code == 0
        assert assembled == [["content-atomizer/new"]], assembled
    print("ok  a partially placed corpus sends only the unplaced posts to the model")


# ---------------------------------------------------------------------------
# Decline reasons reach the model (2026-09-23)
# ---------------------------------------------------------------------------


def _feedback_store(tmp: Path):
    """Two lenses, each with a window, and decisions of every kind about them."""
    import state_store

    root = tmp / "state"
    root.mkdir()
    for lens, month in (("jordan", "2026-09"), ("jordan", "2026-10"), ("blake", "2026-10")):
        window_id = f"{lens}-{month}-01"
        slots_mod.write_window(root / f"window-{lens}-{month}.json", slots_mod.CalendarWindow(
            window_id=window_id, lens=lens, start_date=f"{month}-01", end_date=f"{month}-28",
            slots=[slots_mod.Slot(slot_id=f"{window_id}-01", date=f"{month}-07",
                                  item_ref=f"atomizer/{lens}-{month}", lens=lens)],
        ))

    def row(slot_id, window_id, state, note="", at="2026-09-20T10:00:00Z"):
        value = {"state": state, "by": "Jordan", "at": at, "window_id": window_id,
                 "item_ref": f"atomizer/{slot_id}", "date": "2026-10-07"}
        if note:
            value["note"] = note
        return {"action": "approval", "entry_key": slot_id, "event_at": at, "value": value}

    state_store.record_state("approvals", [
        row("jordan-2026-10-01-01", "jordan-2026-10-01", "rejected", "Too salesy for week one"),
        row("jordan-2026-09-01-01", "jordan-2026-09-01", "rejected", "Old reason"),
        row("blake-2026-10-01-01", "blake-2026-10-01", "rejected", "Not Jordan's feedback"),
    ], root=root)
    return root


def test_decline_reasons_are_gathered_for_this_lens_only():
    with tempfile.TemporaryDirectory() as tmp:
        root = _feedback_store(Path(tmp))
        found = assemble.gather_feedback({}, "jordan", root)
        notes = [f["note"] for f in found]
        assert "Too salesy for week one" in notes and "Old reason" in notes, notes
        assert "Not Jordan's feedback" not in notes, "another founder's reason steered this calendar"
        assert all(f["by"] == "Jordan" for f in found)

        recent = assemble.gather_feedback({"feedback_lookback_windows": 1}, "jordan", root)
        assert [f["note"] for f in recent] == ["Too salesy for week one"], recent
        print("ok  decline reasons are gathered per lens, within the lookback")


def test_a_withdrawn_decline_is_not_feedback():
    import state_store

    with tempfile.TemporaryDirectory() as tmp:
        root = _feedback_store(Path(tmp))
        state_store.record_state("approvals", [{
            "action": "approval", "entry_key": "jordan-2026-10-01-01",
            "event_at": "2026-09-21T10:00:00Z",
            "value": {"state": "approved", "by": "Jordan", "at": "2026-09-21T10:00:00Z",
                      "window_id": "jordan-2026-10-01"},
        }], root=root)
        notes = [f["note"] for f in assemble.gather_feedback({}, "jordan", root)]
        assert "Too salesy for week one" not in notes, "a decline taken back still steers the model"
        print("ok  a decline that was taken back is not fed to the model")


def test_decline_reasons_appear_in_the_prompt_and_only_when_there_are_some():
    empty = assemble.build_user_content([], assemble.Context(), "2026-11-01", "2026-11-30", "PE")
    assert "declined, and why" not in empty, "an empty feedback section was written"
    context = assemble.Context(feedback=[{
        "slot_id": "jordan-2026-10-01-01", "item_ref": "atomizer/anchor-03",
        "date": "2026-10-07", "by": "Jordan", "note": "Too salesy for week one",
    }])
    prompt = assemble.build_user_content([], context, "2026-11-01", "2026-11-30", "PE")
    assert "## What the founders declined, and why (1)" in prompt, prompt[-600:]
    assert 'atomizer/anchor-03 on 2026-10-07, declined by Jordan: "Too salesy for week one"' in prompt
    print("ok  the prompt carries each reason, and no section when there are none")


def test_an_unreadable_store_means_no_feedback_rather_than_no_calendar():
    with tempfile.TemporaryDirectory() as tmp:
        assert assemble.gather_feedback({}, "jordan", Path(tmp) / "missing") == []
        print("ok  a missing store gives an assembly without feedback")


def test_the_newest_season_brief_is_the_arc_and_the_model_reads_it():
    """Added 2026-09-23, when the atomizer's season became the arc.

    The newest season wins by its number, not by the order the files sort in,
    so Season 10 beats Season 9. It reaches the model under its label, and a
    missing brief is a note rather than a silent arc from nothing.
    """
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        atomizer = directory / "atomizer"
        atomizer.mkdir()
        for number in (1, 9, 10):
            (atomizer / f"season-{number}-anchor-post-briefs.md").write_text(
                f"# Season {number} briefs\n", encoding="utf-8"
            )
        season = {"enabled": True, "root": "atomizer", "pattern": "season-*-anchor-post-briefs.md",
                  "label": "The season arc"}
        criteria = dict(_CRITERIA, context={
            "season_arc": season,
            "market_scan": {"enabled": False},
            "narrative_files": [],
            "published_history": [],
        })
        context = assemble.gather_context(criteria, agent_root=directory)
        assert context.season is not None, context.notes
        assert context.season[1].endswith("season-10-anchor-post-briefs.md"), context.season[1]
        content = assemble.build_user_content(
            _candidates(directory), context, "2026-10-01", "2026-10-30", "Private equity"
        )
        assert "## The season arc" in content and "# Season 10 briefs" in content, (
            "the season brief did not reach the model"
        )

        criteria["context"]["season_arc"] = dict(season, root="nowhere")
        context = assemble.gather_context(criteria, agent_root=directory)
        assert context.season is None
        assert any("season brief" in note for note in context.notes), context.notes
    print("ok  the newest season brief reaches the model, and a missing one is a note")


def test_the_shipped_policy_reads_the_atomizers_season():
    criteria = assemble.load_criteria()
    season = (criteria.get("context") or {}).get("season_arc") or {}
    assert season.get("enabled"), "the season arc is off in the shipped policy"
    assert any(rule.get("id") == "follow-the-season" for rule in criteria.get("criteria") or []), (
        "no rule tells the model the season drives the order"
    )
    print("ok  the shipped policy reads the season and says it drives the order")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _tests():
    return [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]


def main() -> int:
    failures = 0
    for test in _tests():
        try:
            test()
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {test.__name__}: {exc}")
    total = len(_tests())
    print(f"\n{total - failures}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
