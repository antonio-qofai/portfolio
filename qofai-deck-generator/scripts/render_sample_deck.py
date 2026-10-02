"""Render one sample proposal deck with the pinned house style.

Drives the full pipeline against the frozen example packet, so the saved
`generated-prompts/<client>/generated-prompt-N.txt` and
`decks/<client>/claude code/output-N.html` are a real paired pipeline output
(shared N, scoped to that client), not a hand-fed render. The `<client>` folder
is derived automatically from the packet's short brand form (client_short).

Needs the `anthropic` package (`pip install -r requirements.txt`) and an
`ANTHROPIC_API_KEY`. The key can come from the environment or, more
conveniently, from a `.env` file in the project root holding

    ANTHROPIC_API_KEY=sk-ant-...

That `.env` is already covered by the repo's `**/.env` gitignore rule, so the
secret never gets committed. Run from the project root:

    python3 scripts/render_sample_deck.py

`--deck-type` selects which path to render (`proposal` or `status`). Each deck
type has its own default packet + company + project, so with no other flags the
script renders that type's frozen example:

    python3 scripts/render_sample_deck.py                    # Ridgeline proposal
    python3 scripts/render_sample_deck.py --deck-type status # Northwind status

To render any other client's packet, override the three inputs (all optional,
they travel together):

    python3 scripts/render_sample_deck.py \
        --packet templates/packets/proposal-data-packet-fbk.md \
        --company "Fabrikam Marine" \
        --project "Field Capture & Project Dashboards"

    python3 scripts/render_sample_deck.py --deck-type status \
        --packet templates/packets/status-data-packet-second.md \
        --company "Vantgo Logistics" \
        --project "Fulfillment Automation"
"""

import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))


def _load_dotenv(path):
    """Populate os.environ from a KEY=VALUE `.env` file, without a dependency.

    Only sets a key that is not already in the environment, so a real exported
    variable always wins. Skips blank lines and `#` comments; strips one layer
    of surrounding single or double quotes. Absent file is a no-op.
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
from deck_generator import generate_and_save_deck

# Per-deck-type defaults: each type points at its own frozen example packet and
# that packet's company + project, so `--deck-type` alone renders the right
# sample without the caller having to also pass --packet/--company/--project.
DECK_TYPE_DEFAULTS = {
    "proposal": {
        "packet": os.path.join(_ROOT, "proposal-data-packet-EXAMPLE.md"),
        "company": "Ridgeline Site Services",
        "project": "Operational Intelligence Platform",
    },
    "status": {
        "packet": os.path.join(_ROOT, "status-data-packet-EXAMPLE.md"),
        "company": "Northwind",
        "project": "Implementation Project",
    },
}


def _no_sleep(_):
    pass


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--deck-type",
        choices=sorted(DECK_TYPE_DEFAULTS),
        default="proposal",
        help="which deck path to render (default: proposal)",
    )
    # These default to None so the per-deck-type defaults can fill any the caller
    # did not pass, after we know the deck type.
    parser.add_argument(
        "--packet",
        default=None,
        help="path to the response data packet (default: the deck type's example)",
    )
    parser.add_argument(
        "--company",
        default=None,
        help="company name to resolve in the packet",
    )
    parser.add_argument(
        "--project",
        default=None,
        help="project name within the company",
    )
    args = parser.parse_args(argv)
    defaults = DECK_TYPE_DEFAULTS[args.deck_type]
    if args.packet is None:
        args.packet = defaults["packet"]
    if args.company is None:
        args.company = defaults["company"]
    if args.project is None:
        args.project = defaults["project"]
    return args


def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set; export it and re-run.")

    args = _parse_args()

    provider = FixtureProvider.from_packet_file(args.packet)
    result = generate_and_save_deck(
        args.deck_type,
        args.company,
        args.project,
        provider,
        poll_interval=0.0,
        sleep=_no_sleep,
    )

    if result["status"] != "ok":
        sys.exit(f"pipeline returned non-ok result, wrote nothing: {result}")

    print("prompt:", result["prompt_path"])
    print("deck:  ", result["deck_path"])
    print("render_fidelity:", result["render_fidelity"])

    # The layout guard's verdict, spelled out rather than dumped: a clipped label
    # is the one defect that does not show up anywhere in the HTML, so a render
    # that "succeeded" can still have shipped a slide that shows less than it
    # carries. A skip is printed too — "not measured" must not read as "clean".
    layout = result.get("layout") or {}
    if not layout:
        pass
    elif not layout.get("checked"):
        print(f"layout: NOT MEASURED — {layout.get('skipped', 'no reason given')}")
    elif layout.get("ok"):
        print(f"layout: clean ({layout.get('slides', 0)} slides measured)")
    else:
        findings = layout.get("findings") or []
        print(f"layout: {len(findings)} PROBLEM(S) — content on the deck is not visible:")
        for line in layout.get("summary") or []:
            print(f"  - {line}")


if __name__ == "__main__":
    main()
