"""Print the URL of every view in the base, for pasting into config.

    .venv/bin/python -m tools.airtable_view_urls

Built 2026-09-20. `sources/email.toml` and `README.md` both said Airtable's API
cannot read a view URL, and that is literally true: no endpoint returns one. It
is also misleading, and it cost a month. A view URL is exactly

    https://airtable.com/<base id>/<table id>/<view id>

and the metadata API returns all three. So the URL cannot be fetched but it can
always be constructed, and there was never a reason for anyone to copy one out
of an address bar.

Read-only, one API call.
"""

from __future__ import annotations

import sys

from agent import airtable, config


def main() -> int:
    if not config.airtable_configured():
        print("AIRTABLE_TOKEN and AIRTABLE_BASE_ID are not set in .env.")
        return 1
    with airtable.Client() as client:
        tables = client.base_tables()
    for table in tables:
        views = table.get("views") or []
        if not views:
            continue
        print(f"\n{table['name']}")
        for view in views:
            print(f"  {view['name']:<16} "
                  f"https://airtable.com/{config.AIRTABLE_BASE_ID}"
                  f"/{table['id']}/{view['id']}")
    print("\nPaste into sources/email.toml: airtable_unlabeled_url, "
          "airtable_interested_url.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
