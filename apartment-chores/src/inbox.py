"""IMAP fetch for the landlord email reader.

The mailbox is a personal Gmail account. Everything in this file exists to
make sure that only the landlord's mail is ever read past its headers, and
that reading it leaves the mailbox exactly as it was.

The order of operations is the safety argument, so it is spelled out:

  1. The allowlist is validated before any connection. Empty, wildcard, or
     anything that is not one whole address raises. There is no "all mail"
     setting to fall back to.
  2. The mailbox is opened read-only (IMAP EXAMINE), so the server refuses
     to change flags. Every fetch is also BODY.PEEK, which never sets \\Seen.
     Unread mail stays unread.
  3. The server is asked for mail FROM each allowlisted address since the
     lookback date. Gmail matches FROM as a substring, and anyone can put
     any From line on a message, so this only narrows the field.
  4. For each hit, only a handful of headers are fetched. The message is
     kept only if its From is exactly one allowlisted address and Gmail's
     own authentication verdict says DMARC passed for that address's
     domain.
  5. Only then is the full message fetched. The body is taken from its
     text part, attachments are ignored, and quoted replies are cut off so
     the model never sees the digest or anyone else's words.

Nothing here knows which apartment, landlord, or model is involved.
"""

import email
import email.policy
import imaplib
import re
from dataclasses import dataclass
from datetime import timezone
from email.parser import BytesHeaderParser
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser

_ADDRESS = re.compile(r"^[^@\s*?%]+@[^@\s*?%]+\.[^@\s*?%]+$")
_HEADER_FIELDS = "FROM MESSAGE-ID DATE SUBJECT AUTHENTICATION-RESULTS"
_LIST_LINE = re.compile(r'^\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<name>.+)$')
_QUOTE_HEADER = re.compile(r"^On [^\n]*(?:\n[^\n]*)?wrote:[ \t]*$", re.MULTILINE)
_COMMENT = re.compile(r"\([^()]*\)")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class InboxError(RuntimeError):
    """The mailbox could not be read. A setup problem, not a quiet day."""


@dataclass(frozen=True)
class Message:
    """One landlord email that passed every check, reduced to what extraction needs."""

    message_id: str
    sender: str
    sent_at: object  # aware datetime
    subject: str
    body: str


def validate_allowlist(addresses):
    """Return the allowlist lowercased, or raise if it could match too much.

    A string is rejected outright rather than iterated character by
    character, and so is anything with a wildcard or without a whole
    domain. An empty list means nothing, never everything.
    """
    if isinstance(addresses, (str, bytes)) or addresses is None:
        raise ValueError(
            "landlord allowlist must be a list of addresses, got %r" % (addresses,)
        )
    cleaned = [str(a).strip().lower() for a in addresses]
    if not cleaned:
        raise ValueError("landlord allowlist is empty; refusing to read any mail")
    bad = [a for a in cleaned if not _ADDRESS.match(a)]
    if bad:
        raise ValueError(
            "landlord allowlist entries %r are not single whole addresses" % bad
        )
    return tuple(sorted(set(cleaned)))


def connect(host, address, password):
    """Log in over IMAPS. Raises InboxError if the server refuses."""
    try:
        conn = imaplib.IMAP4_SSL(host, timeout=30)
        conn.login(address, password)
    except (imaplib.IMAP4.error, OSError) as exc:
        raise InboxError("cannot log in to %s as %s: %s" % (host, address, exc))
    return conn


def fetch(conn, allowlist, since, trusted_authserv_id, max_body_chars):
    """Return (messages, notes) for allowlisted mail on or after `since`.

    conn is a logged-in imaplib connection, or anything with the same
    select / list / uid methods. since is a date. notes are one-line log
    messages about skipped mail; they never contain body text.
    """
    allowed = validate_allowlist(allowlist)
    mailbox = _all_mail(conn)
    status, _ = conn.select(mailbox, readonly=True)
    if status != "OK":
        raise InboxError("cannot open mailbox %s read-only" % mailbox)

    uids = []
    for address in allowed:
        for uid in _search_from(conn, address, since):
            if uid not in uids:
                uids.append(uid)

    messages, notes = [], []
    for uid in uids:
        headers = _parse_headers(_fetch_part(conn, uid, _header_request()))
        reason = _rejection(headers, allowed, trusted_authserv_id)
        if reason:
            notes.append("UID %s: skipped, %s." % (uid.decode(), reason))
            continue
        raw = _fetch_part(conn, uid, "(BODY.PEEK[])")
        message, reason = _parse_message(raw, headers, max_body_chars)
        if reason:
            notes.append("UID %s: skipped, %s." % (uid.decode(), reason))
            continue
        messages.append(message)
    return messages, notes


def _all_mail(conn):
    """Gmail's All Mail folder, so mail that was archived is still seen.

    Found by its \\All flag rather than its name, which Gmail localises.
    Falls back to INBOX on a server without one.
    """
    status, lines = conn.list()
    if status == "OK":
        for line in lines or []:
            if not isinstance(line, bytes):
                continue
            match = _LIST_LINE.match(line.decode("utf-8", "replace"))
            if match and "\\all" in match.group("flags").lower().split():
                name = match.group("name").strip()
                return name if name.startswith('"') else '"%s"' % name
    return "INBOX"


def _search_from(conn, address, since):
    since_text = "%02d-%s-%d" % (since.day, _MONTHS[since.month - 1], since.year)
    status, data = conn.uid("SEARCH", None, "FROM", '"%s"' % address, "SINCE", since_text)
    if status != "OK":
        raise InboxError("IMAP search for %s failed" % address)
    return [uid for chunk in data if chunk for uid in chunk.split()]


def _header_request():
    return "(BODY.PEEK[HEADER.FIELDS (%s)])" % _HEADER_FIELDS


def _fetch_part(conn, uid, request):
    status, data = conn.uid("FETCH", uid, request)
    if status != "OK":
        raise InboxError("IMAP fetch of UID %s failed" % uid.decode())
    for item in data or []:
        if isinstance(item, tuple) and len(item) == 2:
            return item[1]
    raise InboxError("IMAP fetch of UID %s returned no content" % uid.decode())


def _parse_headers(raw):
    return BytesHeaderParser(policy=email.policy.compat32).parsebytes(raw)


def _rejection(headers, allowed, trusted_authserv_id):
    """Why this message must not be read, or None if it may be."""
    senders = [addr.strip().lower() for _, addr in getaddresses(headers.get_all("From", []))]
    senders = [s for s in senders if s]
    if len(senders) != 1:
        return "From names %d addresses, expected exactly one" % len(senders)
    sender = senders[0]
    if sender not in allowed:
        return "sender is not on the allowlist"
    domain = sender.rsplit("@", 1)[1]
    results = headers.get_all("Authentication-Results", [])
    if not results:
        return "no authentication verdict from the receiving server"
    if not _dmarc_passed(results[0], trusted_authserv_id, domain):
        return "the receiving server did not confirm it came from %s" % domain
    return None


def _dmarc_passed(header, trusted_authserv_id, domain):
    """True if this Authentication-Results header is the trusted server's own
    and records dmarc=pass with header.from equal to the sender's domain.

    Only the topmost header is passed in. Gmail stamps its verdict there on
    arrival; anything below it came with the message and proves nothing.
    """
    text = _COMMENT.sub("", " ".join(str(header).split()))
    parts = [p.strip() for p in text.split(";")]
    if not parts or parts[0].split()[0:1] != [trusted_authserv_id]:
        return False
    for part in parts[1:]:
        tokens = dict(
            t.split("=", 1) for t in part.split() if "=" in t
        )
        if tokens.get("dmarc", "").lower() == "pass" and \
                tokens.get("header.from", "").lower() == domain:
            return True
    return False


def _parse_message(raw, headers, max_body_chars):
    """Return (Message, None) or (None, reason)."""
    message_id = (headers.get("Message-ID") or "").strip()
    if not message_id:
        return None, "no Message-ID, so it could not be de-duplicated"
    try:
        sent_at = parsedate_to_datetime(headers.get("Date") or "")
    except (TypeError, ValueError):
        sent_at = None
    if sent_at is None or sent_at.tzinfo is None:
        return None, "no usable Date header to resolve relative dates against"

    parsed = email.message_from_bytes(raw, policy=email.policy.default)
    body = strip_quoted(_text_body(parsed))
    if not body.strip():
        return None, "no text body"
    if len(body) > max_body_chars:
        return None, "body is %d characters, over the %d limit" % (len(body), max_body_chars)

    sender = getaddresses(headers.get_all("From", []))[0][1].strip().lower()
    return Message(
        message_id=message_id,
        sender=sender,
        sent_at=sent_at.astimezone(timezone.utc),
        subject=str(parsed.get("Subject", "") or "").strip(),
        body=body.strip(),
    ), None


def _text_body(parsed):
    """The first text/plain part, else the first text/html part as text.

    Attachments are skipped by disposition. Links are left as text and never
    followed.
    """
    html = None
    for part in parsed.walk():
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        kind = part.get_content_type()
        if kind == "text/plain":
            return part.get_content()
        if kind == "text/html" and html is None:
            html = part.get_content()
    return _html_to_text(html) if html is not None else ""


def strip_quoted(text):
    """Cut a reply down to the sender's own words.

    Drops everything from a Gmail-style "On <date>, <name> wrote:" line
    onwards, and any remaining line quoted with ">". Forwarded messages are
    left alone: a landlord forwarding something is sending it.
    """
    text = text.replace("\r\n", "\n")
    match = _QUOTE_HEADER.search(text)
    if match:
        text = text[: match.start()]
    kept = [line for line in text.split("\n") if not line.lstrip().startswith(">")]
    return "\n".join(kept).rstrip()


class _TextExtractor(HTMLParser):
    _BREAKS = {"br", "p", "div", "li", "tr", "blockquote"}

    def __init__(self):
        super().__init__()
        self.parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in self._BREAKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def _html_to_text(html):
    extractor = _TextExtractor()
    extractor.feed(html)
    lines = [" ".join(line.split()) for line in "".join(extractor.parts).split("\n")]
    return "\n".join(line for line in lines if line)
