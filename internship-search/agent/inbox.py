"""Reads a narrow slice of the mailbox over IMAP, read-only.

SHELVED 2026-08-16, the day it was written. Nothing imports this module, nothing on
the schedule runs it, and it has never connected to a real mailbox. Its parsing is
verified against synthetic messages only. Do not describe it as working code without
that caveat, and do not wire it into a run.

It is kept because the wall is administrative rather than technical. UChicago's
Workspace disables app passwords, disables automatic forwarding, and leaves OAuth
needing a restricted Gmail scope no unverified personal app can hold, so there is no
mailbox this is allowed into. Any account that permits an app password revives it by
setting IMAP_USER and IMAP_PASSWORD and running tools.probe_inbox. See CHANGELOG.md
for 2026-08-16, and do not re-investigate the uchicago mailbox without a changed
policy or a different account.

PRD section 13 designed it as the shared half of Milestones 6.5 and 7.5: 6.5 turns the
newsletter into postings, 7.5 turns application mail into status, and both of them
would get their messages from here. Both are off the build path.

Three rules hold this module together and none of them is a preference.

Read-only is enforced by the protocol, not by remembering. Every mailbox is opened
with EXAMINE rather than SELECT, and every fetch uses BODY.PEEK rather than BODY,
so nothing here can mark a message read, move it, delete it or send anything.
The owner's unread count is not the agent's to change.

The slice is narrow. Nothing is read that an IMAP search did not match on sender
and date. There is no code path that walks the whole mailbox.

No address, sender name, subject line or phrase appears below this docstring.
All of it is data in sources/inbox.toml, per CLAUDE.md rule 2. Inbound mail is the
likeliest place to break that rule, because a list of phrases looks like code when
you are in the middle of writing code.
"""

import email
import imaplib
import re
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, timedelta
from email.header import decode_header, make_header
from email.message import Message

from . import config

# IMAP's date format is fixed by RFC 3501 and is not a locale question, so the
# month names are built here rather than through strftime, which would follow
# whatever locale the machine happens to be in.
_IMAP_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


class InboxError(RuntimeError):
    """Raised for anything that stops the mailbox being read."""


@dataclass(frozen=True)
class Mailbox:
    host: str
    port: int
    folder: str
    lookback_days: int
    readonly: bool = True


@dataclass(frozen=True)
class Newsletter:
    key: str
    name: str
    sender: str
    subject: str = ""
    enabled: bool = True
    notes: str = ""

    @property
    def source_key(self) -> str:
        return f"inbox:{self.key}"


@dataclass(frozen=True)
class Summary:
    """What a message says about itself, without its body."""

    uid: str
    message_id: str
    sender: str
    subject: str
    date: str
    size: int


@dataclass(frozen=True)
class InboxConfig:
    mailbox: Mailbox
    newsletters: tuple[Newsletter, ...] = field(default=())


def load(path=None) -> InboxConfig:
    """Read sources/inbox.toml. Every sender and pattern in the system is here."""
    path = path or config.INBOX_PATH
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)

    box = raw.get("mailbox", {})
    if not box.get("host"):
        raise InboxError(f"{path} has no [mailbox] host")

    mailbox = Mailbox(
        host=box["host"],
        port=int(box.get("port", 993)),
        folder=box.get("folder", "INBOX"),
        lookback_days=int(box.get("lookback_days", 21)),
        readonly=bool(box.get("readonly", True)),
    )

    newsletters = tuple(
        Newsletter(
            key=entry["key"],
            name=entry.get("name", entry["key"]),
            sender=entry["sender"],
            subject=entry.get("subject", ""),
            enabled=bool(entry.get("enabled", True)),
            notes=entry.get("notes", ""),
        )
        for entry in raw.get("newsletter", [])
    )

    return InboxConfig(mailbox=mailbox, newsletters=newsletters)


def _imap_date(days_back: int) -> str:
    when = date.today() - timedelta(days=days_back)
    return f"{when.day:02d}-{_IMAP_MONTHS[when.month - 1]}-{when.year}"


def _decode(value: str | None) -> str:
    """Turn an encoded header into readable text, or give back what it was."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


@contextmanager
def connect(mailbox: Mailbox):
    """Open the mailbox read-only and hand back the connection.

    The mailbox is opened with EXAMINE, which is what readonly=True makes imaplib
    send. That is the protocol-level guarantee PRD section 13 asks for: a server
    that has been told EXAMINE will not let this connection change flags at all,
    so read-only does not depend on every call site remembering to peek.
    """
    if not config.inbox_configured():
        raise InboxError(
            "IMAP_USER and IMAP_PASSWORD are not set in .env, so there is "
            "nothing to connect with"
        )
    if not mailbox.readonly:
        raise InboxError(
            "sources/inbox.toml set readonly = false. PRD section 13 forbids "
            "opening this mailbox any other way; fix the file rather than this line"
        )

    try:
        conn = imaplib.IMAP4_SSL(mailbox.host, mailbox.port)
    except OSError as exc:
        raise InboxError(f"could not reach {mailbox.host}:{mailbox.port}: {exc}") from exc

    try:
        conn.login(config.IMAP_USER, config.IMAP_PASSWORD)
    except imaplib.IMAP4.error as exc:
        conn.logout()
        raise InboxError(
            f"the server refused the login for {config.IMAP_USER}: {exc}. "
            "That is the credential being wrong or IMAP being closed on the "
            "account, not a bug here"
        ) from exc

    try:
        status, detail = conn.select(mailbox.folder, readonly=True)
        if status != "OK":
            raise InboxError(f"could not open {mailbox.folder}: {detail}")
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass
        conn.logout()


def search(conn, sender: str = "", days_back: int = 21) -> list[str]:
    """Return message uids matching a sender within the lookback window.

    Matching is on the sending address and never on the display name. A display
    name is set by whoever sends the message and is rewritten by mail clients, so
    treating it as identity is how a source of postings becomes a way in for
    anything that copies a name.
    """
    criteria = [f"SINCE {_imap_date(days_back)}"]
    if sender:
        criteria.append(f'FROM "{sender}"')

    status, data = conn.uid("SEARCH", None, f"({' '.join(criteria)})")
    if status != "OK":
        raise InboxError(f"search failed: {data}")
    if not data or not data[0]:
        return []
    return [uid.decode() for uid in data[0].split()]


def summarize(conn, uids: list[str]) -> list[Summary]:
    """Read headers only. No body is fetched and nothing is marked read."""
    out: list[Summary] = []
    for uid in uids:
        status, data = conn.uid(
            "FETCH",
            uid,
            "(RFC822.SIZE BODY.PEEK[HEADER.FIELDS (MESSAGE-ID FROM SUBJECT DATE)])",
        )
        if status != "OK" or not data or not isinstance(data[0], tuple):
            continue

        header_bytes = data[0][1]
        parsed = email.message_from_bytes(header_bytes)

        size = 0
        prefix = data[0][0]
        if isinstance(prefix, bytes) and b"RFC822.SIZE" in prefix:
            try:
                after = prefix.split(b"RFC822.SIZE")[1].split()[0]
                size = int(after.strip(b"() "))
            except (IndexError, ValueError):
                size = 0

        out.append(
            Summary(
                uid=uid,
                message_id=(parsed.get("Message-ID") or "").strip(),
                sender=_decode(parsed.get("From")),
                subject=_decode(parsed.get("Subject")),
                date=(parsed.get("Date") or "").strip(),
                size=size,
            )
        )
    return out


def fetch(conn, uid: str) -> Message:
    """Fetch one whole message with BODY.PEEK, so the \\Seen flag is untouched."""
    status, data = conn.uid("FETCH", uid, "(BODY.PEEK[])")
    if status != "OK" or not data or not isinstance(data[0], tuple):
        raise InboxError(f"could not fetch message {uid}")
    return email.message_from_bytes(data[0][1])


def body_text(msg: Message) -> str:
    """The readable text of a message, preferring plain text over HTML.

    A newsletter is usually sent as both. The plain part is what the extraction
    prompt should read, because the HTML part carries tracking wrappers and layout
    that cost tokens and teach the model nothing.
    """
    parts: list[str] = []
    fallback: list[str] = []

    for part in msg.walk() if msg.is_multipart() else [msg]:
        content_type = part.get_content_type()
        if content_type not in ("text/plain", "text/html"):
            continue
        if part.get_content_disposition() == "attachment":
            continue
        try:
            payload = part.get_payload(decode=True)
        except Exception:
            continue
        if not payload:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except LookupError:
            text = payload.decode("utf-8", errors="replace")

        if content_type == "text/plain":
            parts.append(text)
        else:
            fallback.append(text)

    return "\n".join(parts).strip() or "\n".join(fallback).strip()


def links(msg: Message) -> list[str]:
    """Every http link in the message, in order, deduplicated.

    Whether the newsletter links to postings directly is one of the two unknowns
    PRD section 13 says to read rather than guess at, and this is what answers it.
    """
    text = body_text(msg)
    found = re.findall(r"https?://[^\s\"'<>\)\]]+", text)
    seen: set[str] = set()
    out: list[str] = []
    for url in found:
        url = url.rstrip(".,;")
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out
