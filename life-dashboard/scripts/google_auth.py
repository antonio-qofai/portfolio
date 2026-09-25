"""Authorize Google access (read-only) and map calendars.

    uv run scripts/google_auth.py authorize [account]   browser consent; saves data/tokens/google-<account>.json
    uv run scripts/google_auth.py calendars             list calendars on the account, to fill google_id in config.yaml

The account defaults to `google_account` in config.yaml, which gets Calendar and
Gmail. Any other account (e.g. uchicago, named in an inbox's google_account)
gets Gmail only.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard.config import load_config  # noqa: E402
from dashboard.google_auth import CALENDAR_READONLY, GMAIL_READONLY, authorize, session  # noqa: E402

SCOPES = [CALENDAR_READONLY, GMAIL_READONLY]


def cmd_authorize(config: dict, account: str | None = None) -> None:
    account = account or config["google_account"]
    scopes = SCOPES if account == config["google_account"] else [GMAIL_READONLY]
    path = authorize(account, scopes)
    print(f"Saved token to {path}")


def cmd_calendars(config: dict, _account: str | None = None) -> None:
    http = session(config["google_account"], CALENDAR_READONLY)
    resp = http.get(
        "https://www.googleapis.com/calendar/v3/users/me/calendarList",
        params={"fields": "items(id,summary,accessRole,primary)"},
        timeout=20,
    )
    resp.raise_for_status()
    mapped = {c.get("google_id") for c in config["calendars"]}
    print(f"{'in config':<10} {'name':<40} google_id")
    for cal in sorted(resp.json().get("items", []), key=lambda c: not c.get("primary")):
        cid = "primary" if cal.get("primary") else cal["id"]
        flag = "yes" if cid in mapped or cal["id"] in mapped else ""
        print(f"{flag:<10} {cal.get('summary', '')[:39]:<40} {cid}")


if __name__ == "__main__":
    commands = {"authorize": cmd_authorize, "calendars": cmd_calendars}
    if len(sys.argv) not in (2, 3) or sys.argv[1] not in commands:
        sys.exit(f"usage: {sys.argv[0]} {{{'|'.join(commands)}}} [account]")
    commands[sys.argv[1]](load_config(), *sys.argv[2:])
