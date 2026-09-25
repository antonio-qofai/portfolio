"""Stage B and Stage C, the ranker. PRD.md section 4, Milestone 6.

Scores every posting the prefilter surfaced on two axes, fit and reach, and
derives a tier from the result. Two stages, because the two questions cost very
different amounts to answer well.

    Stage B  the cheap model, on every survivor. Most postings are decided here.
    Stage C  the expensive model, only on what clears the routing thresholds in
             rubric.md. Its answer replaces Stage B's on the same row.

Nothing in this file knows what a good posting looks like. Every criterion, both
anchors, the tier bands, the routing thresholds and the score range come out of
rubric.md at runtime, which is CLAUDE.md rule 3 and the reason the owner can change
how ranking behaves by editing prose. agent/rubric.py owns reading that file;
this module owns the model call and what is done with the answer.

Three things are worth knowing before changing anything here.

The prompt is the rubric plus the owner's own labelled examples, in that order, and
it is identical across every call in a run. That is what makes it worth caching.
Whether caching actually happens depends on length: Anthropic ignores a cache
request on a prefix below the model's minimum, which is 4,096 tokens on Haiku and
1,024 on Sonnet. The rubric alone is about 2,300, so Stage C caches from the
second call onward and Stage B does not cache at all until enough labelled
examples are attached to carry it over the line. `run` reports which is happening
rather than leaving it to be guessed at.

Output is structured JSON enforced by the API, and the valid score range is built
into the schema as an enum out of rubric.md. A model cannot return a 12 on a 1 to
10 scale, so no clamping code is needed and none exists.

The owner's score overrides beat the model. The model's number is still stored, so
a disagreement stays visible, but the tier is derived from the override where he
wrote one.
"""

import json
import random

from . import config, db, rubric

# Emitted on a posting whose scoring call failed outright, so a gap in the
# scores is visible in the digest instead of looking like a tier 4 posting.
UNSCORED_FLAG = "scoring_failed"

# Scores are committed in batches this size, so an interrupted run keeps the
# model calls it has already paid for. Same reasoning as triage.COMMIT_EVERY.
COMMIT_EVERY = 20


class RankerUnavailable(RuntimeError):
    """No API key, or the SDK is missing. Ranking is skipped, never fatal."""


# --------------------------------------------------------------- the prompt

def schema(cfg: dict) -> dict:
    """The JSON shape the API enforces, built from the rubric's own score range.

    fit and reach are enums rather than plain integers on purpose. An enum of the
    legal scores makes an out-of-range answer impossible at the API level, which
    is stronger than validating one after the fact and means this file never has
    to decide what a 12 out of 10 should become.
    """
    lo, hi = cfg["scores"]["min"], cfg["scores"]["max"]
    legal = list(range(lo, hi + 1))
    return {
        "type": "object",
        "properties": {
            "fit": {
                "type": "integer",
                "enum": legal,
                "description": f"How much he would want this role, {lo} to {hi}.",
            },
            "reach": {
                "type": "integer",
                "enum": legal,
                "description": f"How likely he is to clear the bar, {lo} to {hi}.",
            },
            "reason": {
                "type": "string",
                "description": "One sentence naming the specific thing in the "
                               "posting that decided the fit score.",
            },
        },
        "required": ["fit", "reach", "reason"],
        "additionalProperties": False,
    }


def _example_line(row: dict) -> str:
    """One labelled posting, as the model sees it.

    His free text is the payload. A label with no reason still teaches the model
    something about which companies and titles he says yes to, but the sentence
    is what generalises, which is why README.md asks him to write one.
    """
    parts = [f"- {row['company']}: {row['title']}"]
    if row.get("location"):
        parts.append(f" ({row['location']})")
    override = []
    if row.get("fit_override") is not None:
        override.append(f"fit {row['fit_override']}")
    if row.get("reach_override") is not None:
        override.append(f"reach {row['reach_override']}")
    if override:
        parts.append(f"  [he scored this himself: {', '.join(override)}]")
    reason = (row.get("label_reason") or "").strip()
    if reason:
        parts.append(f'\n    His words: "{reason}"')
    return "".join(parts)


def examples_block(grouped: dict[str, list[dict]]) -> str:
    """The few-shot block appended below the rubric, or '' when nothing is labelled.

    Returning an empty string on an empty base is the normal case for the first
    weeks, not a degraded one. The rubric's own text already tells the model to
    fall back on its anchors when no examples appear.
    """
    if not grouped:
        return ""

    sections = []
    for label, heading in (
        ("interested", "Postings he marked INTERESTED"),
        ("not_interested", "Postings he marked NOT INTERESTED"),
    ):
        rows = grouped.get(label) or []
        if rows:
            sections.append(
                f"### {heading}\n\n" + "\n".join(_example_line(r) for r in rows)
            )

    # A label the base carries that this code does not know about is still his
    # opinion, so it is passed through under its own heading rather than dropped.
    for label, rows in sorted(grouped.items()):
        if label not in ("interested", "not_interested") and rows:
            sections.append(
                f"### Postings he marked {label.upper()}\n\n"
                + "\n".join(_example_line(r) for r in rows)
            )

    if not sections:
        return ""
    return "## His actual labels\n\n" + "\n\n".join(sections)


def approx_tokens(text: str) -> int:
    """Four characters to a token, the same rough estimate tools.rubric_check prints.

    Only used to decide whether asking for a cache is worth anything. Being a
    little wrong costs nothing: below the model's minimum the API ignores the
    request, so the failure mode either way is that caching quietly does not
    happen, which is exactly what this estimate is here to report.
    """
    return len(text) // 4


def build_prompt(conn, cfg: dict | None = None) -> tuple[str, dict]:
    """The system prompt for every call in this run, plus what it is made of.

    Assembled once per run rather than once per posting. It has to be identical
    byte for byte across calls or the cache never reads, and rebuilding it per
    posting is the easiest way to accidentally make it differ.
    """
    cfg = cfg or rubric.settings()
    per_label = cfg["few_shot"]["per_label"]
    grouped = db.labeled_examples(conn, per_label)
    block = examples_block(grouped)
    text = rubric.prompt_text()
    if block:
        text = f"{text}\n\n{block}"
    return text, {
        "examples": sum(len(v) for v in grouped.values()),
        "tokens": approx_tokens(text),
    }


def system_param(text: str, model: str) -> list[dict]:
    """The system prompt as content blocks, cached when the model will cache it.

    cache_control is attached only when the prefix is long enough for the model
    to honour it. Attaching it below the minimum is harmless but misleading, and
    this system has enough quiet failure modes already.
    """
    block: dict = {"type": "text", "text": text}
    if approx_tokens(text) >= config.CACHE_MIN_TOKENS.get(model, 0):
        block["cache_control"] = {"type": "ephemeral"}
    return [block]


def posting_text(posting: dict) -> str:
    """What the model reads about one posting. Billed per token, per stage."""
    parts = [
        f"Company: {posting.get('company') or ''}",
        f"Title: {posting.get('title') or ''}",
        f"Location: {posting.get('location') or ''}",
    ]
    if posting.get("term"):
        parts.append(f"Term: {posting['term']}")
    if posting.get("weekly_hours"):
        parts.append(f"Stated weekly commitment: {posting['weekly_hours']}")
    # The flags Stage A wrote are shown because the rubric tells the model not to
    # price them in a second time. It cannot obey that without seeing them.
    if posting.get("flags"):
        parts.append(f"Flags already carried to him: {posting['flags']}")
    description = (posting.get("description") or "")[: config.RANK_DESCRIPTION_CHARS]
    parts.append(f"\nPosting text:\n{description or '(no description available)'}")
    return "\n".join(parts)


# ---------------------------------------------------------------- the calls

def _client():
    if not config.ranker_configured():
        raise RankerUnavailable("ANTHROPIC_API_KEY is not set")
    try:
        import anthropic
    except ImportError as exc:
        raise RankerUnavailable(f"anthropic SDK not installed: {exc}") from exc
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


def _request(client, posting: dict, system: list[dict], model: str,
             max_tokens: int, cfg: dict, effort: str | None) -> dict:
    """One scoring call. Returns the model's raw answer plus its token usage."""
    output_config: dict = {"format": {"type": "json_schema", "schema": schema(cfg)}}
    if effort:
        output_config["effort"] = effort

    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": posting_text(posting)}],
        output_config=output_config,
    )

    text = next((b.text for b in response.content if b.type == "text"), "{}")
    data = json.loads(text)
    usage = response.usage
    return {
        "fit": int(data["fit"]),
        "reach": int(data["reach"]),
        "reason": (data.get("reason") or "").strip(),
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
        "cache_write_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
    }


def score_stage_b(client, posting: dict, system: list[dict], cfg: dict) -> dict:
    """The cheap pass. Haiku 4.5 rejects the effort parameter, so none is sent."""
    return _request(
        client, posting, system, config.STAGE_B_MODEL,
        config.STAGE_B_MAX_TOKENS, cfg, effort=None,
    )


def score_stage_c(client, posting: dict, system: list[dict], cfg: dict) -> dict:
    """The expensive pass, on postings the routing thresholds sent here."""
    return _request(
        client, posting, system, config.STAGE_C_MODEL,
        config.STAGE_C_MAX_TOKENS, cfg, effort=config.STAGE_C_EFFORT,
    )


def estimate_cost(stats: dict) -> float:
    """Spend for a run, from published list prices in config.py.

    Corrected 2026-09-22. The docstring here used to say cache reads and writes
    were "counted at the plain input rate, which overstates a cached run and
    never understates one". They were not counted at all. Anthropic reports
    cached tokens in `cache_read_input_tokens` and `cache_creation_input_tokens`
    and EXCLUDES them from `usage.input_tokens`, so summing input_tokens alone
    silently drops every cached token from the bill.

    That is the dangerous direction for a ten dollar budget, and it was not
    theoretical: the run that scored 578 postings on 2026-09-22 recorded $0.52
    while reading 5,021,208 cached tokens that cost real money and appeared
    nowhere. Every cost figure this project has quoted since caching began on
    2026-09-07 is low for the same reason.

    Now: reads at `CACHE_READ_MULTIPLIER` and writes at `CACHE_WRITE_MULTIPLIER`
    of each stage's own input price, because Haiku and Sonnet are priced
    differently and a cached token is billed against its own stage.
    """
    read = config.CACHE_READ_MULTIPLIER
    write = config.CACHE_WRITE_MULTIPLIER
    return (
        (
            stats["b_input_tokens"]
            + stats.get("b_cache_read_tokens", 0) * read
            + stats.get("b_cache_write_tokens", 0) * write
        ) / 1_000_000 * config.STAGE_B_INPUT_PRICE
        + stats["b_output_tokens"] / 1_000_000 * config.STAGE_B_OUTPUT_PRICE
        + (
            stats["c_input_tokens"]
            + stats.get("c_cache_read_tokens", 0) * read
            + stats.get("c_cache_write_tokens", 0) * write
        ) / 1_000_000 * config.STAGE_C_INPUT_PRICE
        + stats["c_output_tokens"] / 1_000_000 * config.STAGE_C_OUTPUT_PRICE
    )


# ------------------------------------------------- applying the rubric's rules

def settle(posting: dict, answer: dict, stage: str, cfg: dict) -> dict:
    """Turn the model's two numbers into what gets stored.

    Everything decided here comes from rubric.md: the tier band, the REACH flag,
    and nothing else. The one thing that overrides it is the owner, whose own score
    is used for the tier where he wrote one while the model's number is still
    recorded, so the two never silently merge into a single unattributable score.
    """
    fit = posting.get("fit_override")
    reach = posting.get("reach_override")
    fit = answer["fit"] if fit is None else int(fit)
    reach = answer["reach"] if reach is None else int(reach)

    band = rubric.tier_for(fit, cfg)
    flags = ["reach"] if rubric.is_reach(reach, cfg) else []
    return {
        "fit": answer["fit"],
        "reach": answer["reach"],
        "tier": band["number"],
        "reason": answer["reason"],
        "flags": db._merge_flags(posting.get("flags"), flags),
        "stage": stage,
        "effective_fit": fit,
        "effective_reach": reach,
        "delivery": band["delivery"],
    }


def needs_stage_c(answer: dict, posting: dict, cfg: dict) -> bool:
    """Whether the cheap model's answer is worth paying the expensive one to check.

    Routed on the model's own numbers, not on the owner's overrides. An override is
    already his final answer for the tier, so spending Sonnet on a posting he has
    personally scored buys nothing.
    """
    return rubric.needs_stage_c(answer["fit"], answer["reach"], cfg)


# ------------------------------------------------------------ the run itself

def _blank_stats() -> dict:
    return {
        "examined": 0, "scored": 0, "stage_b": 0, "stage_c": 0,
        "b_input_tokens": 0, "b_output_tokens": 0,
        "c_input_tokens": 0, "c_output_tokens": 0,
        "cache_read_tokens": 0, "cache_write_tokens": 0,
        "b_cache_read_tokens": 0, "b_cache_write_tokens": 0,
        "c_cache_read_tokens": 0, "c_cache_write_tokens": 0,
        "calls": 0, "remaining": 0, "skipped": False, "errors": [],
        "cost": 0.0, "examples": 0, "prompt_tokens": 0,
        "cached_stages": [], "variance": None, "variance_samples": 0,
        "unstable": False, "tiers": {},
    }


def _account(stats: dict, answer: dict, stage: str) -> None:
    prefix = "b" if stage == "stage_b" else "c"
    stats[f"{prefix}_input_tokens"] += answer["input_tokens"]
    stats[f"{prefix}_output_tokens"] += answer["output_tokens"]
    # Per stage as well as in total, added 2026-09-22. The totals are what the
    # report prints; the per-stage split is what `estimate_cost` needs, because
    # Haiku and Sonnet are priced differently and a cached token is billed as a
    # fraction of its own stage's input price.
    stats[f"{prefix}_cache_read_tokens"] += answer["cache_read_tokens"]
    stats[f"{prefix}_cache_write_tokens"] += answer["cache_write_tokens"]
    stats["cache_read_tokens"] += answer["cache_read_tokens"]
    stats["cache_write_tokens"] += answer["cache_write_tokens"]
    stats["calls"] += 1


def run(conn, max_calls: int | None = None, verbose: bool = False) -> dict:
    """Score every surfaced posting that has no score, up to the call budget.

    Resumable in the same way the Stage 0 pass is: a posting the budget could not
    reach keeps its empty score and is picked up on the next run, so hitting the
    cap costs latency and never coverage.
    """
    cfg = rubric.settings()
    stats = _blank_stats()

    queue = cfg.get("queue") or {}
    postings = db.unscored(
        conn,
        max_age_days=int(queue.get("max_age_days", 0) or 0),
        newest_first=bool(queue.get("newest_first", False)),
        always_labels=tuple(queue.get("always_score_labelled", []) or []),
    )
    stats["examined"] = len(postings)
    if not postings:
        stats["tiers"] = db.score_counts(conn)
        return stats

    budget = config.RANK_MAX_CALLS if max_calls is None else max_calls

    if not config.ranker_configured():
        # No key is not an error, on the same principle as Stage 0. The watcher
        # is the spine; scoring is decoration and must never stop a run.
        if verbose:
            print(f"  Ranker: ANTHROPIC_API_KEY not set, "
                  f"{len(postings)} posting(s) will wait")
        stats["skipped"] = True
        stats["remaining"] = len(postings)
        stats["tiers"] = db.score_counts(conn)
        return stats

    system_text, prompt_info = build_prompt(conn, cfg)
    stats["examples"] = prompt_info["examples"]
    stats["prompt_tokens"] = prompt_info["tokens"]
    systems = {
        "stage_b": system_param(system_text, config.STAGE_B_MODEL),
        "stage_c": system_param(system_text, config.STAGE_C_MODEL),
    }
    stats["cached_stages"] = [
        name for name, blocks in systems.items() if "cache_control" in blocks[0]
    ]

    client = _client()
    # Sampled up front so the choice does not depend on how far the budget got,
    # which would bias the self-check toward whatever is at the front of the queue.
    fraction = cfg["variance"]["sample_fraction"]
    sample = set(
        random.sample(range(len(postings)), int(len(postings) * fraction))
    ) if fraction else set()
    deltas: list[tuple[int, int]] = []

    for index, posting in enumerate(postings):
        # Never begin a posting the budget cannot finish. Stage B alone is a
        # valid score, but a posting whose Stage B answer clears the routing
        # thresholds is owed the expensive model, and once any score is stored
        # the posting stops being unscored and is never offered again. Starting
        # one with a single call left would therefore write a cheap score onto a
        # posting that was routed to Sonnet and silently lose the upgrade. Two
        # calls reserved, three when this posting is also a variance sample; the
        # cost is at most a couple of unused calls at the tail of a capped run.
        needed = 3 if index in sample else 2
        if budget < needed:
            continue

        try:
            answer = score_stage_b(client, posting, systems["stage_b"], cfg)
        except RankerUnavailable:
            raise
        except Exception as exc:  # one bad posting must not end the batch
            stats["errors"].append((posting.get("company"), posting.get("title"), str(exc)))
            if verbose:
                print(f"  score FAIL {posting.get('company')}: {exc}")
            continue
        budget -= 1
        _account(stats, answer, "stage_b")
        stage = "stage_b"

        if needs_stage_c(answer, posting, cfg) and budget > 0:
            try:
                answer = score_stage_c(client, posting, systems["stage_c"], cfg)
                budget -= 1
                _account(stats, answer, "stage_c")
                stage = "stage_c"
            except Exception as exc:
                # Stage B's answer already exists and is a real score. Losing the
                # second opinion is a downgrade, not a failure, so it is recorded
                # and the cheaper score stands.
                stats["errors"].append(
                    (posting.get("company"), posting.get("title"), f"stage C: {exc}")
                )

        # The self-check in PRD section 4. Scoring the same posting twice and
        # measuring the movement is the only evidence that the numbers mean
        # anything from one run to the next.
        if index in sample and budget > 0:
            try:
                second = score_stage_b(client, posting, systems["stage_b"], cfg)
                budget -= 1
                _account(stats, second, "stage_b")
                deltas.append((abs(second["fit"] - answer["fit"]),
                               abs(second["reach"] - answer["reach"])))
            except Exception:
                pass  # a failed self-check is not worth failing a run over

        score = settle(posting, answer, stage, cfg)
        db.record_score(conn, posting["id"], score)
        stats["scored"] += 1
        stats[stage] += 1

        if verbose:
            print(
                f"  score {stage[-1]}  {posting.get('company')}: "
                f"{posting.get('title')} -> fit {score['fit']} reach "
                f"{score['reach']}, tier {score['tier']} ({score['delivery']})"
            )
        if stats["scored"] % COMMIT_EVERY == 0:
            conn.commit()
    conn.commit()

    if deltas:
        stats["variance_samples"] = len(deltas)
        mean_fit = sum(d[0] for d in deltas) / len(deltas)
        mean_reach = sum(d[1] for d in deltas) / len(deltas)
        stats["variance"] = max(mean_fit, mean_reach)
        stats["unstable"] = stats["variance"] > cfg["variance"]["max_mean_delta"]

    stats["cost"] = estimate_cost(stats)
    stats["remaining"] = max(0, len(postings) - stats["scored"])
    stats["tiers"] = db.score_counts(conn)
    return stats


def summary_lines(stats: dict) -> list[str]:
    """Human-readable block, shared by the digest, the run output, and the report."""
    if stats["skipped"]:
        return [f"Ranker skipped, no API key. {stats['remaining']} posting(s) waiting."]
    if not stats["examined"]:
        return ["Ranker: every surfaced posting already has a score."]

    lines = [
        f"Ranked {stats['scored']} of {stats['examined']} unscored posting(s): "
        f"{stats['stage_b']} settled by the cheap model, "
        f"{stats['stage_c']} re-scored by the expensive one.",
        f"{stats['calls']} model call(s), estimated ${stats['cost']:.4f}.",
    ]

    if stats["prompt_tokens"]:
        cached = ", ".join(stats["cached_stages"]) or "neither stage"
        lines.append(
            f"Prompt is roughly {stats['prompt_tokens']:,} tokens "
            f"({stats['examples']} labelled example(s)); cached on {cached}."
        )
    if stats["cache_read_tokens"]:
        lines.append(f"{stats['cache_read_tokens']:,} token(s) served from cache.")
    if stats["remaining"]:
        lines.append(
            f"{stats['remaining']} posting(s) still unscored and will be "
            f"picked up on the next run."
        )
    if stats["variance"] is not None:
        verdict = "above tolerance" if stats["unstable"] else "within tolerance"
        lines.append(
            f"Self-check re-scored {stats['variance_samples']} posting(s): "
            f"mean movement {stats['variance']:.2f}, {verdict}."
        )
    if stats["errors"]:
        lines.append(f"{len(stats['errors'])} scoring call(s) failed:")
        for company, title, err in stats["errors"][:5]:
            lines.append(f"  - {company}: {title}: {err}")
    return lines
