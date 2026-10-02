"""Repeatable eval harness for the status deck path (PRD §5, S1–S13).

Replaces one-off eyeballing with a re-runnable check. For every status packet in
``CASES`` it runs two legs:

1. Structural leg (no API key): the full deterministic pipeline
   (loader -> adapter -> assembler -> coverage guard) via ``generate_deck_prompt``.
   Asserts coverage passes, the assembled slide count equals the packet's own
   ``total_slides`` (S7, both read from pipeline output, never hardcoded), and
   that no other client's signature tokens leak into this prompt (S8).

2. Live leg (only when ``ANTHROPIC_API_KEY`` is present): one real render via
   ``generate_and_save_deck`` and a render-fidelity report. Reviewer markers are
   hard-enforced by the pipeline (a miss raises and fails the eval); value misses
   are report-only, since Claude may reformat a value without dropping it (S13).
   With no key the live leg is skipped and said so, not failed.

The script reuses the pipeline entry points and re-implements no mapping. Client
details live only in the ``CASES`` config, never in the checking logic: a third
packet drops in as one more ``StatusCase`` entry, and the leakage check derives
its banned set from the other cases automatically.

Run from the repo root:

    python3 scripts/eval_status_decks.py

Exits non-zero if any hard check fails (coverage, slide count, leakage, or a live
render error / dropped reviewer marker). A report-only value miss does not fail.
"""

import argparse
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))


def _load_dotenv(path):
    """Populate os.environ from a KEY=VALUE `.env` file, without a dependency.

    Only sets a key that is not already in the environment, so a real exported
    variable always wins. Skips blank lines and `#` comments; strips one layer
    of surrounding single or double quotes. Absent file is a no-op. Mirrors the
    helper in scripts/render_sample_deck.py so the eval script stays standalone.
    """
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_dotenv(os.path.join(_ROOT, ".env"))

from data_source_adapter import FixtureProvider
from deck_generator import generate_and_save_deck, generate_deck_prompt
from coverage_guard import CoverageError
from render_guard import RenderFidelityError


class StatusCase:
    """One status packet to evaluate, plus the tokens unique to that client.

    ``signatures`` are distinctive strings that must never appear in any OTHER
    client's rendered deck (proper nouns, workstream names, client-specific
    figures). They are config, not logic: the leakage check bans, for each case,
    the union of every other case's signatures. Adding a client is one entry.
    """

    def __init__(self, packet, company, project, signatures):
        self.packet = packet
        self.company = company
        self.project = project
        self.signatures = tuple(signatures)


# --- Config: the only place client details live. Add a packet with one entry. ---
CASES = [
    StatusCase(
        packet="status-data-packet-EXAMPLE.md",
        company="Northwind",
        project="Implementation Project",
        signatures=(
            "Northwind", "Woodgrove Partners", "Dynamic Pricing", "Production Scheduling",
            "Planwright", "Charlotte", "14.0pp", "Coil",
        ),
    ),
    StatusCase(
        packet="templates/packets/status-data-packet-second.md",
        company="Vantgo Logistics",
        project="Fulfillment Automation",
        signatures=(
            "Vantgo", "Northgate", "Dispatch Optimization", "Warehouse Robotics",
            "Freight Audit",
        ),
    ),
]

_SLIDE_HEADER_RE = re.compile(r"(?m)^## Slide \d+ —")
_TOTAL_SLIDES_RE = re.compile(r"(?m)^total_slides:\s*(\d+)\s*$")


def _no_sleep(_):
    pass


def _foreign_signatures(case):
    """Every other case's signature tokens — what must NOT appear in this deck."""
    banned = []
    for other in CASES:
        if other is case:
            continue
        banned.extend(other.signatures)
    return banned


class _Report:
    """Accumulates one case's outcome for compact printing + exit-code rollup."""

    def __init__(self, company):
        self.company = company
        self.client_short = company
        self.lines = []
        self.hard_failed = False

    def check(self, label, ok, detail=""):
        mark = "PASS" if ok else "FAIL"
        if not ok:
            self.hard_failed = True
        self.lines.append(f"  {label:<14} {mark}{('  ' + detail) if detail else ''}")

    def note(self, label, detail):
        self.lines.append(f"  {label:<14} {detail}")

    def render(self):
        header = f"{self.company} (client_short={self.client_short})"
        return "\n".join([header] + self.lines)


def _stub_renderer(prompt, **kwargs):
    """Canned HTML for the structural leg — no API, carries a reviewer marker so
    a stub-path render-fidelity check would still find one if ever run."""
    return "<!doctype html><body>stub<p>(unconfirmed, see gaps)</p></body>"


def _eval_case(case, live, skip_reason=""):
    report = _Report(case.company)

    # --- Structural leg: deterministic pipeline, no key. --------------------
    provider = FixtureProvider.from_packet_file(os.path.join(_ROOT, case.packet))
    try:
        result = generate_deck_prompt(
            "status", case.company, case.project, provider,
            poll_interval=0.0, sleep=_no_sleep,
        )
    except CoverageError as exc:
        report.check("coverage", False, str(exc).splitlines()[0])
        return report

    if result["status"] != "ok":
        report.check("pipeline", False, f"non-ok status: {result['status']}")
        return report

    report.check("coverage", True)

    prompt = result["prompt"]
    report.client_short = result.get("client_short", case.company)

    # Slide count vs the packet's own total_slides — both from pipeline output.
    actual_slides = len(_SLIDE_HEADER_RE.findall(prompt))
    total_match = _TOTAL_SLIDES_RE.search(prompt)
    total_slides = int(total_match.group(1)) if total_match else None
    slides_ok = total_slides is not None and actual_slides == total_slides
    report.check(
        "slides", slides_ok,
        f"{actual_slides} assembled vs total_slides {total_slides}",
    )

    # Own client_short must be present (catches an empty / wrong render).
    report.check(
        "self-present", report.client_short in prompt,
        f"client_short '{report.client_short}'",
    )

    # Zero cross-client leakage: no other case's signatures appear here.
    leaked = [t for t in _foreign_signatures(case) if t in prompt]
    report.check(
        "leakage", not leaked,
        "clean" if not leaked else f"leaked: {', '.join(leaked)}",
    )

    # --- Live leg: one real render + fidelity, only with a key. -------------
    if not live:
        report.note("live render", f"skipped ({skip_reason})")
        return report

    live_provider = FixtureProvider.from_packet_file(os.path.join(_ROOT, case.packet))
    try:
        rendered = generate_and_save_deck(
            "status", case.company, case.project, live_provider,
            poll_interval=0.0, sleep=_no_sleep,
        )
    except RenderFidelityError as exc:
        # A dropped reviewer marker is hard-enforced upstream; treat as a failure.
        report.check("live render", False, f"marker fidelity: {exc}")
        return report

    if rendered["status"] != "ok":
        report.check("live render", False, f"non-ok status: {rendered['status']}")
        return report

    fidelity = rendered.get("render_fidelity") or {}
    missing = fidelity.get("missing_values") or {}
    # Markers are hard-enforced (a miss would have raised). Value misses are
    # report-only, so they inform but do not fail the eval.
    report.check("live render", True, f"N={rendered.get('number')}")
    if missing:
        flat = "; ".join(f"{k}: {', '.join(v)}" for k, v in missing.items())
        report.note("fidelity", f"ok={fidelity.get('ok')}  value misses (report-only): {flat}")
    else:
        report.note("fidelity", f"ok={fidelity.get('ok')}  no value misses")

    # Layout is a HARD check in the eval, unlike in an interactive run. In the UI a
    # reviewer sees the findings next to the rendered deck and judges them; an eval
    # has no human in the loop, so a deck that hides content it carries is a
    # failure, not a note. A skip (no browser) is a note — an unavailable browser
    # says nothing about the deck.
    layout = rendered.get("layout") or {}
    if not layout.get("checked"):
        report.note("layout", f"not measured: {layout.get('skipped', 'no report')}")
    elif layout.get("ok"):
        report.check("layout", True, f"{layout.get('slides', 0)} slides, no clipped content")
    else:
        findings = layout.get("summary") or []
        report.check("layout", False, f"{len(findings)} clipped/overflowing: " + "; ".join(findings[:3]))

    report.note("prompt", os.path.relpath(rendered["prompt_path"], _ROOT))
    report.note("deck", os.path.relpath(rendered["deck_path"], _ROOT))
    return report


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-live", action="store_true",
        help="skip the live render leg even when a key is present (structural only)",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    live = has_key and not args.no_live
    if args.no_live:
        skip_reason = "--no-live"
    elif not has_key:
        skip_reason = "no ANTHROPIC_API_KEY"
    else:
        skip_reason = ""

    print("=" * 68)
    print(f"Status deck eval — {len(CASES)} packet(s), "
          f"live render leg: {'ON' if live else 'OFF'}")
    print("=" * 68)

    hard_failures = 0
    for case in CASES:
        report = _eval_case(case, live, skip_reason)
        print()
        print(report.render())
        if report.hard_failed:
            hard_failures += 1

    print()
    print("=" * 68)
    if hard_failures:
        print(f"RESULT: FAIL — {hard_failures} of {len(CASES)} packet(s) failed a hard check")
    else:
        print(f"RESULT: PASS — all {len(CASES)} packet(s) cleared every hard check")
    print("=" * 68)
    return 1 if hard_failures else 0


if __name__ == "__main__":
    sys.exit(main())
