"""Milestone 4 measurement. What the prefilter killed, and whether it was right.

    .venv/bin/python -m tools.prefilter_report              # what is stored now
    .venv/bin/python -m tools.prefilter_report --dry-run    # re-run the rules, write nothing
    .venv/bin/python -m tools.prefilter_report --samples 5  # show examples of each kill
    .venv/bin/python -m tools.prefilter_report --surfaced   # list what survived
    .venv/bin/python -m tools.prefilter_report --reapply    # keep the edited rules

PRD section 10 sets an 80 percent kill target for Milestone 4 and asks for the
actual figure to be recorded. This prints it.

The loop after editing sources/prefilter.toml is --dry-run to see what the
change would do, then --reapply to keep it. --reapply forgets every stored
verdict and decides again from scratch. It costs nothing, because Stage 0 tags
are kept and never re-read.

The --samples flag matters more than the headline number. A false kill is
invisible in normal use, because the owner only ever sees what survived, so the
only way to catch one is to read a sample of what died. Every kill here carries
the reason and the rule that produced it. If a sample looks wrong, the fix is in
sources/prefilter.toml, never in Python.

--dry-run re-applies the current rules to every open posting without touching
the database. Use it after editing prefilter.toml to see what the change would
do before committing to it. It uses no model calls, so it costs nothing; the
timing rules fall back to whatever Stage 0 already stored.
"""

import argparse
import collections
import random

from agent import db, prefilter, triage

# Samples are drawn at random, but from a fixed seed, so the same database
# produces the same examples twice. Taking the first N instead grouped every
# sample under whichever company sorts first: the initial audit showed eight
# Anthropic roles and nothing else, which hides exactly the kind of systematic
# error the samples exist to catch.
SAMPLE_SEED = 0


def evaluate_all(conn) -> list[tuple[dict, object]]:
    """Apply the current rules to every open posting. Writes nothing."""
    rules = prefilter.load_rules()
    results = []
    for row in conn.execute("SELECT * FROM postings WHERE closed_detected_at IS NULL"):
        posting = dict(row)
        verdict = prefilter.evaluate_hard(posting, rules)
        if not verdict.killed:
            timing = prefilter.evaluate_timing(posting, rules)
            timing.flags = verdict.flags + timing.flags
            verdict = timing
        results.append((posting, verdict))
    return results


def stored(conn) -> list[tuple[dict, object]]:
    """What the last real run decided, read back out of the database."""
    results = []
    for row in conn.execute(
        "SELECT * FROM postings WHERE closed_detected_at IS NULL "
        "AND prefilter_verdict IS NOT NULL"
    ):
        posting = dict(row)
        results.append(
            (
                posting,
                prefilter.Verdict(
                    posting["prefilter_verdict"],
                    posting["prefilter_reason"] or "",
                    [f for f in (posting["flags"] or "").split(",") if f],
                ),
            )
        )
    return results


def rule_of(reason: str) -> str:
    """Collapse a per-posting reason to the rule that produced it."""
    return reason.split(":")[0].strip() or "unexplained"


def report(results, samples: int, show_surfaced: bool) -> str:
    killed = [(p, v) for p, v in results if v.outcome == prefilter.KILLED]
    surfaced = [(p, v) for p, v in results if v.outcome == prefilter.SURFACE]
    pending = [(p, v) for p, v in results if v.outcome == prefilter.PENDING]
    decided = len(killed) + len(surfaced)

    by_rule = collections.Counter(rule_of(v.reason) for _, v in killed)
    gate = by_rule.get("not a student or early-career role", 0)
    # Two rates, because they answer different questions. The headline is what
    # PRD Milestone 4 asks for, measured against raw postings. The second is the
    # one that says whether the exclusion rules themselves are doing anything,
    # since the student gate would post a huge number on its own.
    candidates = decided - gate

    lines = [
        "STAGE A PREFILTER",
        "",
        f"  {len(results):6d}  open postings examined",
        f"  {len(killed):6d}  killed",
        f"  {len(surfaced):6d}  surfaced",
        f"  {len(pending):6d}  pending, waiting on Stage 0 intake tagging",
        "",
    ]
    if decided:
        lines += [
            f"  Kill rate: {len(killed) / decided:.1%} of the {decided} decided. "
            f"PRD Milestone 4 target is 80%.",
            f"  Of that, {gate} died on the student-role gate alone. Among the "
            f"{candidates} postings that are plausibly student roles, the hard "
            f"exclusions and timing rules killed "
            f"{(len(killed) - gate) / candidates:.1%}." if candidates else "",
        ]
    else:
        lines.append("  Nothing decided yet.")
    lines += ["", "WHY THINGS DIED"]
    for rule, n in by_rule.most_common():
        lines.append(f"  {n:6d}  {rule}")

    lines += ["", "FLAGS ON SURFACED POSTINGS"]
    by_flag = collections.Counter(f for _, v in surfaced for f in v.flags)
    if by_flag:
        for flag, n in by_flag.most_common():
            lines.append(f"  {n:6d}  {flag}")
    else:
        lines.append("  none")

    if samples:
        lines += ["", f"SAMPLE KILLS ({samples} per rule, drawn at random)", "",
                  "Read these. A false kill is invisible anywhere else in the system."]
        grouped: dict[str, list] = collections.defaultdict(list)
        for posting, verdict in killed:
            grouped[rule_of(verdict.reason)].append((posting, verdict))
        rng = random.Random(SAMPLE_SEED)
        for rule in by_rule:
            group = grouped[rule]
            lines += ["", f"  {rule}  ({len(group)} total)"]
            for posting, verdict in rng.sample(group, min(samples, len(group))):
                lines.append(f"    - {posting['company']}: {posting['title']}")
                lines.append(f"      {verdict.reason}")

    if show_surfaced:
        lines += ["", f"SURFACED ({len(surfaced)})", ""]
        for posting, verdict in sorted(surfaced, key=lambda x: (x[0]["company"], x[0]["title"])):
            flags = f"  [{', '.join(verdict.flags)}]" if verdict.flags else ""
            lines.append(f"  - {posting['company']}: {posting['title']}{flags}")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="re-apply the current rules in memory instead of reading stored verdicts",
    )
    parser.add_argument(
        "--samples", type=int, default=3, help="example kills to print per rule (0 for none)"
    )
    parser.add_argument("--surfaced", action="store_true", help="list everything that survived")
    parser.add_argument(
        "--reapply",
        action="store_true",
        help="forget every stored verdict and decide again with the current rules",
    )
    args = parser.parse_args()

    conn = db.connect()

    if args.reapply:
        cleared = db.clear_verdicts(conn, prefilter.OWNED_FLAGS)
        hard = triage.hard_pass(conn)
        timing = triage.timing_pass(conn)
        print(
            f"Re-applied the current rules to {cleared} posting(s). "
            f"No model calls were made; Stage 0 tags were kept.\n"
            f"Hard exclusions killed {hard['killed']}, timing rules killed "
            f"{timing['killed']}.\n"
        )

    results = evaluate_all(conn) if args.dry_run else stored(conn)
    if args.dry_run:
        print("Dry run: rules re-applied in memory, database untouched.\n")
    print(report(results, args.samples, args.surfaced))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
