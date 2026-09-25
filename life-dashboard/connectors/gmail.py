"""Gmail inboxes from config.yaml, read-only (gmail.readonly).

An inbox is fetched only when it names a `google_account` (token in
data/tokens/); the others are skipped until connected. Threads are fetched
with format=metadata, so only headers and Gmail's snippet arrive, never
bodies.

Every Primary thread whose last message came from someone else goes to the
LLM triage (dashboard/triage.py), which keeps only pressing school, work and
internship email. If triage fails, the rule-based flags stand: last message
from someone else, a real person, not bulk or automated mail.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime
from email.utils import parseaddr
from html import unescape
from zoneinfo import ZoneInfo

from dashboard import triage
from dashboard.google_auth import GMAIL_READONLY, session
from dashboard.schema import Item

API = "https://gmail.googleapis.com/gmail/v1/users/me"
HEADERS = ["From", "Subject", "List-Unsubscribe", "List-Id", "Precedence", "Auto-Submitted"]
THREAD_FIELDS = "id,messages(labelIds,snippet,internalDate,payload/headers)"
# Gmail tabs other than Primary hold newsletters, promos and notifications.
BULK_CATEGORIES = {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_UPDATES", "CATEGORY_FORUMS"}
# Bulk tabs are excluded in the query so they can't crowd real mail out of max_threads;
# they are only counted.
_TABS = [c.removeprefix("CATEGORY_").lower() for c in sorted(BULK_CATEGORIES)]
NOT_BULK = " ".join(f"-category:{t}" for t in _TABS)
ONLY_BULK = "(" + " OR ".join(f"category:{t}" for t in _TABS) + ")"
BULK_COUNT_LIMIT = 500
FALLBACK_NOTE = "(AI filter unavailable) "
AUTOMATED_SENDER = re.compile(
    r"^(no-?reply|do-?not-?reply|notifications?|mailer-daemon|bounces?|alerts?|updates?|news(letter)?)\b",
    re.I,
)


def _headers(message: dict) -> dict[str, str]:
    return {h["name"].lower(): h["value"] for h in message.get("payload", {}).get("headers", [])}


def is_automated(message: dict) -> bool:
    h = _headers(message)
    if "list-unsubscribe" in h or "list-id" in h:
        return True
    if h.get("precedence", "").lower() in ("bulk", "list", "junk"):
        return True
    if h.get("auto-submitted", "no").lower() != "no":
        return True
    if BULK_CATEGORIES & set(message.get("labelIds", [])):
        return True
    local = parseaddr(h.get("from", ""))[1].split("@")[0]
    return bool(AUTOMATED_SENDER.match(local))


def _sender(from_header: str) -> str:
    name, addr = parseaddr(from_header)
    return name or addr or "(unknown sender)"


def parse_thread(thread: dict, inbox: dict, me: str, tz: ZoneInfo) -> Item | None:
    """One Item per thread, from its last non-draft message. None if the thread has no messages."""
    parsed = _parse(thread, inbox, me, tz)
    return parsed[0] if parsed else None


def _parse(thread: dict, inbox: dict, me: str, tz: ZoneInfo) -> tuple[Item, dict | None] | None:
    """(Item, triage candidate or None if the user sent the last message)."""
    messages = [m for m in thread.get("messages", []) if "DRAFT" not in m.get("labelIds", [])]
    if not messages:
        return None
    last = messages[-1]
    from_me = "SENT" in last.get("labelIds", []) or parseaddr(_headers(last).get("from", ""))[1].lower() == me
    reply_needed = not from_me and not is_automated(last)
    sent = datetime.fromtimestamp(int(last.get("internalDate", 0)) / 1000, tz)
    item = Item(
        source=f"email.{inbox['id']}",
        # Gmail HTML-encodes snippets; the page escapes on output, so decode here.
        title=unescape(_headers(messages[0]).get("subject") or "(no subject)"),
        summary=unescape(f"{_sender(_headers(last).get('from', ''))}: {last.get('snippet', '')}").strip(),
        link=f"https://mail.google.com/mail/?authuser={me}#all/{thread['id']}",
        timestamp=sent.isoformat(),
        urgency_hints=["reply_needed"] if reply_needed else [],
        section=inbox["section"],
    )
    candidate = None if from_me else {
        "id": thread["id"],
        "inbox": inbox.get("label", inbox["id"]),
        "from": unescape(_headers(last).get("from", "")),
        "subject": item.title,
        "snippet": unescape(last.get("snippet", "")),
        "date": item.timestamp,
    }
    return item, candidate


def apply_triage(item: Item, sender: str, verdict: dict) -> None:
    """Pressing email gets reply_needed and/or deadline; the rest loses its flags."""
    hints = []
    if verdict["pressing"]:
        if verdict["reply_needed"]:
            hints.append("reply_needed")
        if verdict["due"] or not verdict["reply_needed"]:
            hints.append("deadline")
        item.summary = f"{sender}: {verdict['reason']}"
        item.due = verdict["due"]
    item.urgency_hints = hints


def _count_threads(http, q: str) -> int:
    """Thread ids only (no metadata), up to BULK_COUNT_LIMIT."""
    count, params = 0, {"q": q, "maxResults": BULK_COUNT_LIMIT, "fields": "threads(id),nextPageToken"}
    while count < BULK_COUNT_LIMIT:
        resp = http.get(f"{API}/threads", params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        count += len(data.get("threads", []))
        if not data.get("nextPageToken"):
            break
        params["pageToken"] = data["nextPageToken"]
    return min(count, BULK_COUNT_LIMIT)


def bulk_item(count: int, inbox: dict, me: str, lookback_days: int, capped: bool) -> Item:
    return Item(
        source=f"email.{inbox['id']}",
        title=f"{count}{'+' if capped else ''} in Promotions, Social, Updates and Forums",
        summary=f"Last {lookback_days} days, not checked for replies.",
        link=f"https://mail.google.com/mail/?authuser={me}#inbox",
        section=inbox["section"],
    )


def _fetch_inbox(inbox: dict, email_cfg: dict, tz: ZoneInfo) -> list[tuple[Item, dict | None]]:
    http = session(inbox["google_account"], GMAIL_READONLY)
    profile = http.get(f"{API}/profile", params={"fields": "emailAddress"}, timeout=20)
    profile.raise_for_status()
    me = profile.json()["emailAddress"].lower()

    window = f"in:inbox newer_than:{email_cfg['lookback_days']}d"
    listing = http.get(
        f"{API}/threads",
        params={
            "q": f"{window} {NOT_BULK}",
            "maxResults": email_cfg["max_threads"],
            "fields": "threads(id)",
        },
        timeout=20,
    )
    listing.raise_for_status()

    items = []
    for t in listing.json().get("threads", []):
        resp = http.get(
            f"{API}/threads/{t['id']}",
            params={"format": "metadata", "metadataHeaders": HEADERS, "fields": THREAD_FIELDS},
            timeout=20,
        )
        resp.raise_for_status()
        parsed = _parse(resp.json(), inbox, me, tz)
        if parsed:
            items.append(parsed)

    bulk = _count_threads(http, f"{window} {ONLY_BULK}")
    if bulk:
        items.append((bulk_item(bulk, inbox, me, email_cfg["lookback_days"], bulk >= BULK_COUNT_LIMIT), None))
    return items


def fetch(config: dict) -> list[Item]:
    tz = ZoneInfo(config.get("timezone", "America/Chicago"))
    parsed = []
    for inbox in config["inboxes"]:
        if inbox.get("provider") != "gmail" or not inbox.get("google_account"):
            continue  # not connected yet (UChicago waits on the policy check)
        parsed.extend(_fetch_inbox(inbox, config["email"], tz))
    triage_all(parsed, config, datetime.now(tz).date())
    return sorted((item for item, _ in parsed), key=lambda i: i.timestamp or "", reverse=True)


def triage_all(parsed: list[tuple[Item, dict | None]], config: dict, today, client=None) -> None:
    """Apply LLM verdicts in place; on failure keep the rule flags and mark them."""
    candidates = {c["id"]: (item, c) for item, c in parsed if c}
    try:
        verdicts = triage.classify([c for _, c in candidates.values()], config["email"]["model"], today, client)
    except triage.TriageError as e:
        print(f"email triage failed, using rules: {e}", file=sys.stderr)
        for item, _ in parsed:
            if item.urgency_hints:
                item.summary = FALLBACK_NOTE + item.summary
        return
    for tid, (item, c) in candidates.items():
        apply_triage(item, _sender(c["from"]), verdicts[tid])
