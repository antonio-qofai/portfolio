"""Measure rendered decks for clipped, overlapping or off-slide content.

The layout guard (`src/layout_guard.py`) runs automatically on every render, but
it only sees decks produced from that point on. This script points the same guard
at any HTML deck already on disk, so a whole back catalogue can be swept in one
pass — useful after a scaffold change, to confirm nothing regressed, and to find
what the older decks were shipping before the guard existed.

Needs a local Chrome/Chromium/Edge (set `CHROME_BIN` to pin one). Needs no API
key: this reads finished HTML and never calls a model.

Run from the project root:

    python3 scripts/check_deck_layout.py                       # every deck under decks/
    python3 scripts/check_deck_layout.py "decks/Vantgo/claude code/output-6.html"
    python3 scripts/check_deck_layout.py --strict               # exit 1 on any finding

Exit code is 0 unless `--strict` is passed and at least one deck has a finding, so
it can gate CI without failing an ordinary audit run.
"""

import argparse
import glob
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from layout_guard import check_layout, describe_finding, find_chrome  # noqa: E402

DEFAULT_GLOB = os.path.join(_ROOT, "decks", "*", "claude code", "output-*.html")


def _decks(paths):
    """Resolve the decks to check: the given paths, else every rendered deck."""
    if paths:
        return [p for p in paths if os.path.isfile(p)]
    return sorted(glob.glob(DEFAULT_GLOB))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", help="deck HTML files (default: all)")
    parser.add_argument("--strict", action="store_true",
                        help="exit 1 when any deck has a finding")
    parser.add_argument("--tolerance", type=float, default=None,
                        help="pixels of overflow to ignore (default: the guard's)")
    args = parser.parse_args(argv)

    if not find_chrome():
        print("No Chrome/Chromium/Edge found. Install one or set CHROME_BIN.")
        return 0 if not args.strict else 1

    decks = _decks(args.paths)
    if not decks:
        print("No decks to check.")
        return 0

    kwargs = {} if args.tolerance is None else {"tolerance": args.tolerance}
    total_findings = 0
    for path in decks:
        with open(path, encoding="utf-8") as f:
            report = check_layout(f.read(), **kwargs)
        rel = os.path.relpath(path, _ROOT)
        if not report["checked"]:
            print(f"SKIP  {rel} — {report['skipped']}")
            continue
        findings = report["findings"]
        total_findings += len(findings)
        if not findings:
            print(f"OK    {rel} ({report['slides']} slides)")
            continue
        print(f"FAIL  {rel} ({report['slides']} slides, {len(findings)} finding(s))")
        for finding in findings:
            print(f"        - {describe_finding(finding)}")

    print(f"\n{len(decks)} deck(s) checked, {total_findings} finding(s).")
    return 1 if (args.strict and total_findings) else 0


if __name__ == "__main__":
    sys.exit(main())
