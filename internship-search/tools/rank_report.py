"""Look at the ranker before and after it spends anything. Run with:

    .venv/bin/python -m tools.rank_report              # what it would do, spends nothing
    .venv/bin/python -m tools.rank_report --prompt     # print the exact prompt
    .venv/bin/python -m tools.rank_report --run 5      # actually score 5 postings
    .venv/bin/python -m tools.rank_report --scores 20  # read back what it decided

With no flags this makes no model call and writes nothing. That is the default on
purpose: the same asymmetry as tools/prefilter_report.py, where the cheap mistake
is looking twice and the expensive one is a bad rule applied to twelve hundred
postings.
"""

import argparse
import sys

from agent import config, db, ranker, rubric


def _stage_cost(count, prompt_tokens, posting_tokens, out_tokens,
                in_price, out_price, cached: bool) -> float:
    """What `count` calls to one stage actually cost, cache included.

    The prompt prefix is identical on every call, which is the whole point of
    building it once, so when it caches only the FIRST call pays for it. After
    that a read costs a tenth of base input. The per-posting suffix is different
    every time and never caches.

    This function is the 2026-09-22 fix. The old estimate charged full input
    price for `prompt_tokens + posting_tokens` on every single call and then
    printed a line admitting cache reads were not deducted. With a 5,300 token
    prompt against roughly 685 tokens of posting, the prefix is 89 percent of
    the input, so ignoring the cache overstated the bill by about eight times.
    It put clearing the queue at $16.58 to $23.42; the real figure was $0.52,
    and the wrong number is why ranking sat paused from 2026-08-12 to
    2026-09-20.
    """
    if cached and count > 0:
        # One write, then reads. The write is a real cost and is not rounded
        # away: on a small batch it is most of the bill.
        prefix = (
            prompt_tokens * config.CACHE_WRITE_MULTIPLIER
            + prompt_tokens * config.CACHE_READ_MULTIPLIER * (count - 1)
        )
    else:
        prefix = prompt_tokens * count
    suffix = posting_tokens * count
    return ((prefix + suffix) / 1_000_000 * in_price
            + count * out_tokens / 1_000_000 * out_price)


def _posting_tokens(pending: list) -> int:
    """Mean tokens the per-posting half of a prompt actually costs.

    Measured from the postings about to be scored, not assumed from
    `RANK_DESCRIPTION_CHARS`. That constant is a truncation CAP, and using it as
    a typical length was the second error in this estimate: 79 percent of
    surfaced postings carry no description at all, so the real mean is about 497
    characters against a 2,500 cap, and charging the cap overstated the suffix
    by nearly four times.
    """
    cap = config.RANK_DESCRIPTION_CHARS
    if not pending:
        return cap // 4 + 60
    total = sum(min(len(p.get("description") or ""), cap) for p in pending)
    return int(total / len(pending) / 4) + 60


def _estimate(pending: list, prompt_tokens: int) -> None:
    """Cost of scoring the pending postings, cache and real lengths included.

    Rewritten 2026-09-22 after this function kept ranking paused for five weeks.
    It put clearing the queue at $16.58 to $23.42 against a ten dollar budget;
    the run that actually cleared it cost about a dollar. Two errors compounded.

    It charged full input price for the prompt prefix on every call, when the
    prefix is identical by design and caches after the first. And it charged the
    truncation cap for every posting's description when most postings have none.

    It still errs high, which is deliberate and is the safe direction for a
    budget. The stage split is the remaining unknown: how many postings clear
    the routing thresholds cannot be known without scoring them, so this
    brackets it the way the old version did.
    """
    count = len(pending)
    posting_tokens = _posting_tokens(pending)
    b_cached = prompt_tokens >= config.CACHE_MIN_TOKENS.get(config.STAGE_B_MODEL, 0)
    c_cached = prompt_tokens >= config.CACHE_MIN_TOKENS.get(config.STAGE_C_MODEL, 0)

    b_cost = _stage_cost(count, prompt_tokens, posting_tokens, 60,
                         config.STAGE_B_INPUT_PRICE, config.STAGE_B_OUTPUT_PRICE,
                         b_cached)

    def c_cost(share: float) -> float:
        return _stage_cost(int(count * share), prompt_tokens, posting_tokens, 400,
                           config.STAGE_C_INPUT_PRICE, config.STAGE_C_OUTPUT_PRICE,
                           c_cached)

    print(f"\nCost to score all {count:,} from scratch")
    print(f"  Prompt {prompt_tokens:,} tokens"
          f"{', cached' if b_cached or c_cached else ', NOT cached'}"
          f"; about {posting_tokens} tokens per posting, measured")
    print(f"  Stage B on all of them            ${b_cost:.2f}")
    print(f"  Stage C on 10% of them            ${c_cost(0.10):.2f}")
    print(f"  Stage C on 30% of them            ${c_cost(0.30):.2f}")
    print(f"  So somewhere around               "
          f"${b_cost + c_cost(0.10):.2f} to ${b_cost + c_cost(0.30):.2f}")
    print(f"  At {config.RANK_MAX_CALLS} calls per run that is roughly "
          f"{max(1, count // max(1, config.RANK_MAX_CALLS))} run(s) to drain.")
    print("  Cache reads and writes ARE counted. Measured against the real run of"
          "\n  578 postings on 2026-09-22 this errs high by about a third.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect the ranker.")
    parser.add_argument("--prompt", action="store_true",
                        help="print the exact system prompt the model receives")
    parser.add_argument("--run", type=int, metavar="N",
                        help="score for real, spending at most N model calls. "
                             "Costs money and writes scores")
    parser.add_argument("--scores", type=int, metavar="N",
                        help="print the N highest scored open postings")
    args = parser.parse_args()

    conn = db.connect()
    try:
        cfg = rubric.settings()
    except rubric.RubricError as exc:
        print(f"rubric.md is unusable:\n\n  {exc}\n")
        return 1

    pending = db.unscored(conn)
    counts = db.score_counts(conn)
    system_text, info = ranker.build_prompt(conn, cfg)

    print(f"Surfaced and open: {counts.get('total', 0):,}")
    print(f"  already scored:  {counts.get('total', 0) - len(pending):,}")
    print(f"  awaiting a score:{len(pending):,}")
    for key in sorted(k for k in counts if k.startswith("tier_")):
        print(f"  {key.replace('_', ' ')}: {counts[key]:,}")

    print(f"\nPrompt: {len(system_text):,} characters, roughly {info['tokens']:,} tokens,"
          f" built from {info['examples']} labelled example(s).")
    for stage, model in (("Stage B", config.STAGE_B_MODEL),
                         ("Stage C", config.STAGE_C_MODEL)):
        minimum = config.CACHE_MIN_TOKENS.get(model, 0)
        ok = "cached" if info["tokens"] >= minimum else "NOT cached"
        print(f"  {stage} on {model}: {ok} (needs {minimum:,} tokens)")
    if info["examples"] == 0:
        print("\n  No labels yet, so the model is working from the rubric's anchors"
              "\n  alone. Label postings in Airtable and this block fills itself.")

    if pending:
        _estimate(pending, info["tokens"])
        print("\nNext in the queue")
        for posting in pending[:5]:
            print(f"  {posting['company']}: {posting['title']}")

    if args.prompt:
        print("\n" + "=" * 72)
        print(system_text)
        print("=" * 72)

    if args.scores:
        rows = conn.execute(
            "SELECT company, title, fit_score, reach_score, tier, scored_by, reason "
            "FROM postings WHERE closed_detected_at IS NULL AND fit_score IS NOT NULL "
            "ORDER BY fit_score DESC, reach_score DESC LIMIT ?", (args.scores,)
        ).fetchall()
        print(f"\nTop {len(rows)} scored posting(s)")
        for r in rows:
            print(f"\n  tier {r['tier']}  fit {r['fit_score']}  reach {r['reach_score']}"
                  f"  [{r['scored_by']}]  {r['company']}: {r['title']}")
            print(f"    {r['reason']}")

    if args.run:
        print(f"\nScoring {args.run} posting(s) for real.\n")
        stats = ranker.run(conn, max_calls=args.run, verbose=True)
        print()
        for line in ranker.summary_lines(stats):
            print(f"  {line}")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
