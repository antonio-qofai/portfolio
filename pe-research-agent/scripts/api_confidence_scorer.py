#!/usr/bin/env python3
"""
api_confidence_scorer.py

Scores each claim in evals/eval_set.json using the Claude API with structured output.
One API call per claim; uses tool_choice to force a machine-readable label.

Usage:
    python3 scripts/api_confidence_scorer.py
    python3 scripts/api_confidence_scorer.py --eval evals/eval_set.json --output evals/predictions.json
    python3 scripts/api_confidence_scorer.py --delay 1.0

Environment:
    ANTHROPIC_API_KEY  (required)

Dependencies:
    pip install anthropic
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

try:
    import anthropic
except ImportError:
    print("Error: anthropic package not installed. Run: pip install anthropic", file=sys.stderr)
    sys.exit(1)

MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """\
You are a confidence-band classifier for private equity research claims, applying the QofAI rubric.

Apply these checks in order and stop at the first match:

1. Claim has no source at all → low (flag as unsourced).
2. Claim based only on a user-edited or aggregator source (Crunchbase, Wikipedia, PitchBook \
user-submitted fields) → low.
3. Claim self-reported by the subject firm or portfolio company (ADV financials, firm website, \
firm press release, company website metrics) → medium at most, never high.
4. Claim is an editorial comparative ("notably high", "unusually long", "exceptionally fast") \
without a cited published benchmark → low.
5. Claim is a public inference drawn from multiple public documents rather than directly stated \
in any single source → medium (low if the inference requires large extrapolation).
6. Claim is independently reported, filed under regulatory consequence, or corroborated by two \
or more non-subject primary sources and not stale → high.
7. Claim independently reported by a single non-subject source (no corroboration) → medium.

Additional floors and ceilings:
- Revenue or EBITDA estimates for private companies: low floor.
- Inferences about a firm's motivation or intent: low floor.
- Aggregate portfolio metrics from a firm press release: medium floor and ceiling.
- Any claim corroborated only by the firm's own materials: medium ceiling.
- Crunchbase-only or LinkedIn-only role claims: low.
- Aggregating weak sources does not upgrade confidence; the band is set by the weakest source \
the claim depends on.
- Self-reported capital commitment or fund size figures are medium regardless of how specific \
they appear.

You will be given a single claim. Classify it and return the result using the classify_confidence tool.
"""

TOOL_SCHEMA = {
    "name": "classify_confidence",
    "description": "Return the confidence band for a PE research claim per the QofAI rubric.",
    "input_schema": {
        "type": "object",
        "properties": {
            "label": {
                "type": "string",
                "enum": ["high", "medium", "low"],
                "description": "The confidence band."
            },
            "reasoning": {
                "type": "string",
                "description": "One sentence identifying which rubric rule applied and why."
            }
        },
        "required": ["label", "reasoning"]
    }
}


def classify_claim(client: anthropic.Anthropic, claim_id: str, claim_text: str,
                   source_summary: str = "") -> dict:
    """Return {"label": str, "reasoning": str} for a single claim."""
    user_content = f"Claim to classify:\n\n{claim_text}"
    if source_summary:
        user_content += f"\n\nSource: {source_summary}"
    response = client.messages.create(
        model=MODEL,
        max_tokens=256,
        system=SYSTEM_PROMPT,
        tools=[TOOL_SCHEMA],
        tool_choice={"type": "tool", "name": "classify_confidence"},
        messages=[
            {
                "role": "user",
                "content": user_content
            }
        ]
    )
    for block in response.content:
        if block.type == "tool_use" and block.name == "classify_confidence":
            return block.input
    raise ValueError(f"No classify_confidence tool_use block returned for claim {claim_id}")


def main():
    parser = argparse.ArgumentParser(
        description="Score confidence bands for claims in an eval set via the Claude API."
    )
    parser.add_argument(
        "--eval",
        default="evals/eval_set.json",
        help="Path to eval set JSON (default: evals/eval_set.json)"
    )
    parser.add_argument(
        "--output",
        default="evals/predictions.json",
        help="Path to write predictions JSON (default: evals/predictions.json)"
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Seconds to wait between API calls (default: 0.5)"
    )
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    eval_path = Path(args.eval)
    if not eval_path.exists():
        print(f"Error: eval file not found: {eval_path}", file=sys.stderr)
        sys.exit(1)

    with open(eval_path) as f:
        eval_data = json.load(f)

    claims = eval_data.get("claims", [])
    if not claims:
        print("Error: no claims found in eval set.", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    total = len(claims)
    predictions = {}
    errors = []

    print(f"Model : {MODEL}")
    print(f"Eval  : {eval_path}")
    print(f"Claims: {total}")
    print(f"Output: {args.output}")
    print()

    for i, claim in enumerate(claims, 1):
        claim_id = claim["id"]
        print(f"[{i:02d}/{total}] {claim_id} ... ", end="", flush=True)

        try:
            result = classify_claim(client, claim_id, claim["claim_text"],
                                    claim.get("source_summary", ""))
            label = result["label"]
            reasoning = result["reasoning"]
            match = "OK " if label == claim["confidence_label"] else "ERR"
            print(f"{label:6s}  (true: {claim['confidence_label']:6s})  {match}")
            predictions[claim_id] = {
                "predicted_label": label,
                "reasoning": reasoning,
                "true_label": claim["confidence_label"],
                "source_dossier": claim["source_dossier"],
                "claim_text": claim["claim_text"]
            }
        except Exception as exc:
            print(f"FAILED — {exc}")
            errors.append({"id": claim_id, "error": str(exc)})
            predictions[claim_id] = {
                "predicted_label": None,
                "reasoning": f"Error: {exc}",
                "true_label": claim["confidence_label"],
                "source_dossier": claim["source_dossier"],
                "claim_text": claim["claim_text"]
            }

        if i < total:
            time.sleep(args.delay)

    scored = total - len(errors)
    correct = sum(
        1 for p in predictions.values()
        if p["predicted_label"] == p["true_label"]
    )
    accuracy = correct / scored if scored else 0.0

    output = {
        "model": MODEL,
        "eval_file": str(eval_path),
        "total_claims": total,
        "scored": scored,
        "errors": len(errors),
        "correct": correct,
        "accuracy": round(accuracy, 4),
        "predictions": predictions
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    print()
    print(f"Scored  : {scored}/{total}")
    print(f"Correct : {correct}/{scored}")
    print(f"Accuracy: {accuracy:.1%}")
    if errors:
        print(f"Errors  : {[e['id'] for e in errors]}")
    print(f"Written : {output_path}")


if __name__ == "__main__":
    main()
