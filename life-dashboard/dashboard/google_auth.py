"""Google OAuth for read-only Calendar (M2) and Gmail (M3).

Tokens live in data/tokens/google-<account>.json (gitignored, mode 0600).
v1 is read-only: any token carrying a scope outside READONLY_SCOPES is
refused, both when authorizing and when loading.
"""

from __future__ import annotations

import os
from pathlib import Path

from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials

from dashboard.config import ROOT, load_env

CALENDAR_READONLY = "https://www.googleapis.com/auth/calendar.readonly"
GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
READONLY_SCOPES = frozenset({CALENDAR_READONLY, GMAIL_READONLY})

TOKEN_DIR = ROOT / "data" / "tokens"


class NotAuthorized(RuntimeError):
    pass


def token_path(account: str) -> Path:
    return TOKEN_DIR / f"google-{account}.json"


def check_readonly(scopes: list[str] | set[str] | None) -> None:
    extra = set(scopes or ()) - READONLY_SCOPES
    if extra:
        raise NotAuthorized(f"refusing non-read-only scopes: {sorted(extra)}")


def _save(creds: Credentials, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json())
    path.chmod(0o600)


def authorize(account: str, scopes: list[str]) -> Path:
    """Run the browser consent flow and save the token. Interactive; not for launchd."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    check_readonly(scopes)
    load_env()
    client_id, secret = os.environ.get("GOOGLE_CLIENT_ID"), os.environ.get("GOOGLE_CLIENT_SECRET")
    if not client_id or not secret:
        raise NotAuthorized("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set in .env")
    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }
    flow = InstalledAppFlow.from_client_config(client_config, scopes=scopes)
    # select_account: always show the account chooser, so the right Google account is picked explicitly.
    creds = flow.run_local_server(port=0, open_browser=True, prompt="select_account consent")
    check_readonly(creds.granted_scopes or creds.scopes)
    path = token_path(account)
    _save(creds, path)
    return path


def session(account: str, scope: str) -> AuthorizedSession:
    """Authorized HTTP session for a saved token. Refreshes and re-saves if expired."""
    path = token_path(account)
    if not path.exists():
        raise NotAuthorized(f"no Google token for '{account}'. Run: uv run scripts/google_auth.py authorize")
    creds = Credentials.from_authorized_user_file(str(path))
    check_readonly(creds.scopes)
    if scope not in (creds.scopes or []):
        raise NotAuthorized(f"token for '{account}' lacks {scope}. Re-run scripts/google_auth.py authorize")
    if not creds.valid:
        if not creds.refresh_token:
            raise NotAuthorized(f"token for '{account}' expired with no refresh token. Re-authorize")
        creds.refresh(Request())
        _save(creds, path)
    return AuthorizedSession(creds)
