"""Negative-path eval harness — the escalation guarantee (PRD criteria 4 & 5).

The companion to ``eval_status_decks.py``. That harness runs only happy-path
packets that clear the gates; this one runs packets that must NOT render. The
agent's core guarantee, endorsed by Blake and Casey, is "never fabricate,
escalate to human review when data is missing." This harness is the re-runnable
proof of it, on both the proposal and status paths.

For every case in ``CASES`` it asserts that a below-bar or errored data packet
routes to escalation, not a deck:

1. Gate failure (a packet the provider assembled but flagged as below the
   ``min_confidence`` / ``min_data_completeness`` bar) must come back
   ``status = review`` with the packet's own confidence / data_completeness
   surfaced, and NO prompt.

2. An Agent OS error envelope (``E_LOW_CONFIDENCE``, whose details carry the
   ``missing_fields`` list — the "missing required field" signal both contracts
   define) must come back ``status = error`` with the code, remediation, and
   ``missing_fields`` surfaced, and NO prompt.

For each case both entry points are exercised: ``generate_deck_prompt`` (returns
the escalation payload, never a ``prompt`` key) and ``generate_and_save_deck``
pointed at a throwaway output root (returns the same payload and writes ZERO
files — no ``generated-prompt-N.txt``, no ``output-N.html``). The second check is
the real teeth: it proves the "do not render" bar holds at the file-writing
layer, not just in the returned dict.

The gate-failure packets are the real frozen example packets with only their
frontmatter confidence / data_completeness rewritten below the bar, so each case
is a realistic full packet the provider marked incomplete, not a bare stub —
mapping never runs on a gate failure, but using the real body keeps the case
honest against future reordering.

Run from the repo root (no API key needed — escalation happens before any
render):

    python3 scripts/eval_negative_path.py

Exits non-zero if any case renders when it must escalate, escalates on the wrong
branch, drops the expected surfaced fields, or writes any file.
"""

import os
import re
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from data_source_adapter import FixtureProvider
from deck_generator import generate_and_save_deck, generate_deck_prompt


def _no_sleep(_):
    pass


# --- gate-failing packet fixtures: real example packet, frontmatter below bar --
_CONFIDENCE_RE = re.compile(r'^confidence:\s*"?[A-Za-z]+"?', re.MULTILINE)
_COMPLETENESS_RE = re.compile(r"^data_completeness:\s*[0-9]*\.?[0-9]+", re.MULTILINE)


def _packet_below_bar(packet_relpath, *, confidence, data_completeness):
    """Read a real example packet and rewrite only its frontmatter gates.

    Everything else (the full structured body) is carried verbatim, so the case
    is a realistic packet the provider assembled and then flagged as below the
    bar, not a synthetic stub. The gate is read from frontmatter, so this is all
    it takes to force the escalation branch.
    """
    with open(os.path.join(_ROOT, packet_relpath), encoding="utf-8") as f:
        packet_md = f.read()
    packet_md, n_conf = _CONFIDENCE_RE.subn(f'confidence: "{confidence}"', packet_md, count=1)
    packet_md, n_comp = _COMPLETENESS_RE.subn(
        f"data_completeness: {data_completeness}", packet_md, count=1
    )
    if n_conf != 1 or n_comp != 1:
        raise RuntimeError(
            f"could not rewrite frontmatter gates in {packet_relpath} "
            f"(confidence={n_conf}, completeness={n_comp})"
        )
    return packet_md


def _error_envelope(code, *, missing_fields, confidence, data_completeness):
    """An Agent OS E_LOW_CONFIDENCE-shaped envelope. Both contracts define its
    details as {confidence, data_completeness, missing_fields} and its
    remediation as 'do not render, return for human review'."""
    return {
        "status": "error",
        "error": {
            "code": code,
            "message": f"{code}: packet assembled but below the confidence/completeness bar",
            "remediation": "Do NOT render; return for human review.",
            "details": {
                "confidence": confidence,
                "data_completeness": data_completeness,
                "missing_fields": list(missing_fields),
            },
        },
    }


class NegativeCase:
    """One packet that must escalate instead of rendering.

    ``make_provider`` is a factory (not a provider) because the provider is
    stateful — poll counters advance — and each case runs two pipeline calls, so
    each needs its own fresh provider. ``expect`` is the required escalation
    ``status`` (``review`` or ``error``); ``surfaced`` names the escalation
    fields that must be present and non-empty on the returned payload, so a
    regression that drops them (leaving a reviewer blind to WHY it escalated)
    fails the eval. A surfaced name may be a dotted path (``details.missing_fields``)
    since the error branch nests the contract's ``details`` block unchanged.
    """

    def __init__(self, label, deck_type, company, project, make_provider, expect, surfaced):
        self.label = label
        self.deck_type = deck_type
        self.company = company
        self.project = project
        self.make_provider = make_provider
        self.expect = expect
        self.surfaced = surfaced


# --- Config: the only place client details live. Add a case with one entry. ---
# Real example packets, addressed relative to the repo root, each paired with the
# company and project that packet is written about. Every case below reads its
# client from here, so a packet swaps out in one place instead of six.
_PROPOSAL_PACKET = "proposal-data-packet-EXAMPLE.md"
_PROPOSAL_CLIENT = ("Ridgeline Site Services", "Operational Intelligence Platform")
_STATUS_PACKET = "status-data-packet-EXAMPLE.md"
_STATUS_CLIENT = ("Northwind", "Implementation Project")

CASES = [
    # ---- proposal path -----------------------------------------------------
    NegativeCase(
        "proposal · low confidence → review",
        "proposal", *_PROPOSAL_CLIENT,
        lambda: FixtureProvider.from_packet_markdown(
            _packet_below_bar(_PROPOSAL_PACKET, confidence="low", data_completeness=0.92)
        ),
        expect="review",
        surfaced=("confidence", "data_completeness"),
    ),
    NegativeCase(
        "proposal · low completeness → review",
        "proposal", *_PROPOSAL_CLIENT,
        lambda: FixtureProvider.from_packet_markdown(
            _packet_below_bar(_PROPOSAL_PACKET, confidence="high", data_completeness=0.55)
        ),
        expect="review",
        surfaced=("confidence", "data_completeness"),
    ),
    NegativeCase(
        "proposal · E_LOW_CONFIDENCE (missing fields) → error",
        "proposal", *_PROPOSAL_CLIENT,
        lambda: FixtureProvider.from_envelope(
            _error_envelope(
                "E_LOW_CONFIDENCE",
                missing_fields=["commercial.scenarios", "timeline.milestones"],
                confidence="low", data_completeness=0.41,
            )
        ),
        expect="error",
        surfaced=("code", "remediation", "details.missing_fields"),
    ),
    # ---- status path -------------------------------------------------------
    NegativeCase(
        "status · low confidence → review",
        "status", *_STATUS_CLIENT,
        lambda: FixtureProvider.from_packet_markdown(
            _packet_below_bar(_STATUS_PACKET, confidence="low", data_completeness=0.90)
        ),
        expect="review",
        surfaced=("confidence", "data_completeness"),
    ),
    NegativeCase(
        "status · low completeness → review",
        "status", *_STATUS_CLIENT,
        lambda: FixtureProvider.from_packet_markdown(
            _packet_below_bar(_STATUS_PACKET, confidence="high", data_completeness=0.60)
        ),
        expect="review",
        surfaced=("confidence", "data_completeness"),
    ),
    NegativeCase(
        "status · E_LOW_CONFIDENCE (missing fields) → error",
        "status", *_STATUS_CLIENT,
        lambda: FixtureProvider.from_envelope(
            _error_envelope(
                "E_LOW_CONFIDENCE",
                missing_fields=["tracking.lanes", "workstreams[1].after.metrics"],
                confidence="low", data_completeness=0.38,
            )
        ),
        expect="error",
        surfaced=("code", "remediation", "details.missing_fields"),
    ),
]


class _Report:
    """Accumulates one case's checks for compact printing + exit-code rollup."""

    def __init__(self, label):
        self.label = label
        self.lines = []
        self.hard_failed = False

    def check(self, name, ok, detail=""):
        if not ok:
            self.hard_failed = True
        mark = "PASS" if ok else "FAIL"
        self.lines.append(f"  {name:<18} {mark}{('  ' + detail) if detail else ''}")

    def render(self):
        return "\n".join([self.label] + self.lines)


_MISSING = object()


def _resolve(payload, path):
    """Walk a dotted ``path`` (e.g. ``details.missing_fields``) into a result
    dict, returning ``_MISSING`` if any hop is absent or not a dict."""
    value = payload
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            return _MISSING
        value = value[key]
    return value


def _is_nonempty(value):
    """True unless the surfaced field is missing or an empty string/list/dict.

    A surfaced escalation field that is present but empty is as useless to a
    reviewer as an absent one, so both fail the ``surfaced`` check.
    """
    if value is _MISSING or value is None:
        return False
    if isinstance(value, (str, list, dict, tuple)):
        return len(value) > 0
    return True


def _output_files(root):
    """Every generated prompt / deck file under ``root`` (recursively)."""
    found = []
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if _PROMPT_NAME_RE.match(name) or _DECK_NAME_RE.match(name):
                found.append(os.path.relpath(os.path.join(dirpath, name), root))
    return found


_PROMPT_NAME_RE = re.compile(r"^generated-prompt-\d+\.txt$")
_DECK_NAME_RE = re.compile(r"^output-\d+\.html$")


def _eval_case(case):
    report = _Report(case.label)

    # --- Leg 1: generate_deck_prompt escalates, never returns a prompt. -----
    result = generate_deck_prompt(
        case.deck_type, case.company, case.project, case.make_provider(),
        poll_interval=0.0, sleep=_no_sleep,
    )
    report.check(
        "prompt: status", result.get("status") == case.expect,
        f"got '{result.get('status')}', want '{case.expect}'",
    )
    report.check(
        "prompt: no deck", "prompt" not in result,
        "no prompt returned" if "prompt" not in result else "LEAKED a prompt",
    )
    missing_surfaced = [f for f in case.surfaced if not _is_nonempty(_resolve(result, f))]
    report.check(
        "prompt: surfaced", not missing_surfaced,
        "all present" if not missing_surfaced else f"empty/absent: {', '.join(missing_surfaced)}",
    )

    # --- Leg 2: generate_and_save_deck escalates AND writes zero files. -----
    # Point both output roots at a throwaway dir; on any non-ok result the
    # pipeline returns before it ever computes a number or touches a folder, so
    # the dir must stay empty. This is the "do not render" bar at the file layer.
    with tempfile.TemporaryDirectory(prefix="negpath-") as tmp:
        prompts_root = os.path.join(tmp, "generated-prompts")
        decks_root = os.path.join(tmp, "decks")
        saved = generate_and_save_deck(
            case.deck_type, case.company, case.project, case.make_provider(),
            prompts_root=prompts_root, decks_root=decks_root,
            render=False, poll_interval=0.0, sleep=_no_sleep,
        )
        report.check(
            "save: status", saved.get("status") == case.expect,
            f"got '{saved.get('status')}', want '{case.expect}'",
        )
        report.check(
            "save: no paths",
            "prompt_path" not in saved and "deck_path" not in saved and "number" not in saved,
            "no file refs" if "prompt_path" not in saved else "LEAKED a file ref",
        )
        leaked_files = _output_files(tmp)
        report.check(
            "save: no files", not leaked_files,
            "nothing written" if not leaked_files else f"WROTE: {', '.join(leaked_files)}",
        )

    return report


def main(argv=None):
    print("=" * 68)
    print(f"Negative-path eval — {len(CASES)} escalation case(s), no render")
    print("The guarantee: missing/low-confidence data escalates, never fabricates.")
    print("=" * 68)

    hard_failures = 0
    for case in CASES:
        report = _eval_case(case)
        print()
        print(report.render())
        if report.hard_failed:
            hard_failures += 1

    print()
    print("=" * 68)
    if hard_failures:
        print(f"RESULT: FAIL — {hard_failures} of {len(CASES)} case(s) failed a hard check")
    else:
        print(f"RESULT: PASS — all {len(CASES)} case(s) escalated cleanly, zero files written")
    print("=" * 68)
    return 1 if hard_failures else 0


if __name__ == "__main__":
    sys.exit(main())
