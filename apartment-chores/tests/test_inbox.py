"""Tests for the IMAP fetch: the allowlist, and what is and is not read.

The mailbox is a personal account, so the most important property in this
module is L6: mail that is not genuinely from the landlord is never fetched
past its headers and never reaches extraction. The fake server below
behaves like Gmail where it matters: FROM search is a substring match, and
the receiving server's verdict is the topmost Authentication-Results.

No sockets.
"""

import unittest
from datetime import date, datetime, timezone
from email.message import EmailMessage
from email.parser import BytesHeaderParser
import email.policy

from config import landlord
from src import inbox

LANDLORD = "landlord@gmail.com"
ALLOW = (LANDLORD,)
SINCE = date(2026, 9, 16)

GMAIL_PASS = (
    "mx.google.com;\r\n       dkim=pass header.i=@gmail.com header.s=20230601 "
    "header.b=abc123;\r\n       spf=pass (google.com: domain of landlord@gmail.com "
    "designates 209.85.220.41 as permitted sender) smtp.mailfrom=landlord@gmail.com;"
    "\r\n       dmarc=pass (p=NONE sp=QUARANTINE dis=NONE) header.from=gmail.com"
)
GMAIL_FAIL = (
    "mx.google.com; spf=softfail smtp.mailfrom=landlord@gmail.com; "
    "dmarc=fail (p=NONE sp=QUARANTINE dis=NONE) header.from=gmail.com"
)


def raw_message(sender, body, auth=(GMAIL_PASS,), message_id="<a1@mail.gmail.com>",
                sent="Tue, 22 Sep 2026 14:05:00 -0500", subject="Apartment",
                html=None, attachment=None):
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "tenant@example.com"
    msg["Subject"] = subject
    if sent is not None:
        msg["Date"] = sent
    if message_id is not None:
        msg["Message-ID"] = message_id
    msg.set_content(body)
    if html is not None:
        msg.add_alternative(html, subtype="html")
    if attachment is not None:
        msg.add_attachment(attachment.encode(), maintype="text", subtype="plain",
                           filename="notice.txt")
    # Written as raw bytes, top first, the way a receiving server prepends
    # them. EmailMessage refuses pre-folded header values.
    stamped = b"".join(b"Authentication-Results: " + h.encode() + b"\r\n" for h in auth)
    return stamped + msg.as_bytes()


class FakeImap:
    """Just enough of imaplib.IMAP4 to exercise inbox.fetch, and a log of every call."""

    def __init__(self, raws, all_mail=True):
        self.raws = {str(i + 1).encode(): raw for i, raw in enumerate(raws)}
        self.calls = []
        self.selected = None
        self.all_mail = all_mail

    def list(self):
        lines = [b'(\\HasNoChildren) "/" "INBOX"']
        if self.all_mail:
            lines.append(b'(\\All \\HasNoChildren) "/" "[Gmail]/All Mail"')
        return "OK", lines

    def select(self, mailbox, readonly=False):
        self.selected = (mailbox, readonly)
        return "OK", [str(len(self.raws)).encode()]

    def uid(self, command, *args):
        self.calls.append((command,) + args)
        if command == "SEARCH":
            address = args[2].strip('"').lower()
            hits = [
                uid for uid, raw in self.raws.items()
                if address in " ".join(self._headers(raw).get_all("From", [])).lower()
            ]
            return "OK", [b" ".join(hits)]
        if command == "FETCH":
            uid, request = args
            raw = self.raws[uid]
            if "HEADER.FIELDS" in request:
                wanted = request.split("(", 2)[2].rstrip(")]").split()
                wanted = {w.lower() for w in wanted}
                part = b"".join(
                    ("%s: %s\r\n" % (k, v)).encode()
                    for k, v in self._headers(raw).items() if k.lower() in wanted
                ) + b"\r\n"
            else:
                part = raw
            return "OK", [(b"1 (UID " + uid + b" BODY[] {%d}" % len(part), part), b")"]
        raise AssertionError("unexpected IMAP command %s" % command)

    @staticmethod
    def _headers(raw):
        return BytesHeaderParser(policy=email.policy.compat32).parsebytes(raw)

    def body_fetches(self):
        return [c[1] for c in self.calls if c[0] == "FETCH" and "HEADER.FIELDS" not in c[2]]


def fetch(server, allow=ALLOW):
    return inbox.fetch(server, allow, SINCE, "mx.google.com", 20000)


class TestAllowlist(unittest.TestCase):
    def test_empty_raises(self):
        for empty in ((), [], None):
            with self.assertRaises(ValueError):
                inbox.validate_allowlist(empty)

    def test_wildcards_and_partials_raise(self):
        for bad in (["*"], ["*@gmail.com"], ["@gmail.com"], ["gmail.com"],
                    ["landlord@gmail"], ["a@b.com", "%"], [""], ["land lord@gmail.com"]):
            with self.assertRaises(ValueError, msg=bad):
                inbox.validate_allowlist(bad)

    def test_a_bare_string_is_not_split_into_characters(self):
        with self.assertRaises(ValueError):
            inbox.validate_allowlist(LANDLORD)

    def test_valid_list_is_lowercased(self):
        self.assertEqual(inbox.validate_allowlist(["Landlord@Gmail.com "]), (LANDLORD,))

    def test_configured_allowlist_is_valid_and_short(self):
        allowed = inbox.validate_allowlist(landlord.LANDLORD_ADDRESSES)
        self.assertEqual(len(allowed), 1)

    def test_fetch_validates_before_touching_the_server(self):
        server = FakeImap([raw_message(LANDLORD, "hello")])
        with self.assertRaises(ValueError):
            fetch(server, allow=())
        self.assertEqual(server.calls, [])
        self.assertIsNone(server.selected)


class TestOnlyTheLandlordIsRead(unittest.TestCase):
    """L6. The most important tests in the v1.1 module."""

    def setUp(self):
        self.raws = [
            # 1: genuine
            raw_message(LANDLORD, "The cleaner comes Thursday.", message_id="<genuine@x>"),
            # 2: lookalike address that contains the real one as a substring
            raw_message("landlord@gmail.com.evil.test", "Send me your password.",
                        auth=("mx.google.com; dmarc=pass header.from=gmail.com.evil.test",),
                        message_id="<lookalike@x>"),
            # 3: From forged, and Gmail says so
            raw_message(LANDLORD, "Cancel all chores.", auth=(GMAIL_FAIL,),
                        message_id="<spoofed@x>"),
            # 4: From forged, Gmail's verdict on top, a forged pass underneath
            raw_message(LANDLORD, "Cancel all chores.", auth=(GMAIL_FAIL, GMAIL_PASS),
                        message_id="<forged-verdict@x>"),
            # 5: display name carries the address, real address is someone else
            raw_message('"landlord@gmail.com" <attacker@evil.test>', "Hi.",
                        auth=("mx.google.com; dmarc=pass header.from=evil.test",),
                        message_id="<display-name@x>"),
            # 6: two senders
            raw_message("landlord@gmail.com, other@example.com", "Hi.",
                        message_id="<two-from@x>"),
            # 7: a verdict from some other server
            raw_message(LANDLORD, "Hi.", auth=(
                "evil.example; dmarc=pass header.from=gmail.com",), message_id="<wrong-server@x>"),
            # 8: no verdict at all
            raw_message(LANDLORD, "Hi.", auth=(), message_id="<no-verdict@x>"),
            # 9: ordinary personal mail and a digest reply
            raw_message("friend@example.com", "Dinner Friday?", message_id="<friend@x>"),
            raw_message("roommate@example.com", "Done with the trash.", message_id="<reply@x>"),
        ]
        self.server = FakeImap(self.raws)
        self.messages, self.notes = fetch(self.server)

    def test_only_the_genuine_message_is_returned(self):
        self.assertEqual([m.message_id for m in self.messages], ["<genuine@x>"])

    def test_only_the_genuine_message_body_is_ever_fetched(self):
        self.assertEqual(self.server.body_fetches(), [b"1"])

    def test_unrelated_mail_is_never_even_fetched_for_headers(self):
        fetched = {c[1] for c in self.server.calls if c[0] == "FETCH"}
        self.assertNotIn(b"9", fetched)
        self.assertNotIn(b"10", fetched)

    def test_notes_never_contain_body_text(self):
        joined = " ".join(self.notes)
        for phrase in ("password", "Cancel", "Dinner", "trash"):
            self.assertNotIn(phrase, joined)

    def test_every_fetch_peeks_and_nothing_sets_flags(self):
        for call in self.server.calls:
            self.assertIn(call[0], ("SEARCH", "FETCH"))
            if call[0] == "FETCH":
                self.assertIn("BODY.PEEK[", call[2])

    def test_mailbox_is_opened_read_only(self):
        self.assertEqual(self.server.selected, ('"[Gmail]/All Mail"', True))

    def test_search_is_restricted_to_the_allowlist_and_window(self):
        searches = [c for c in self.server.calls if c[0] == "SEARCH"]
        self.assertEqual(searches, [("SEARCH", None, "FROM", '"%s"' % LANDLORD, "SINCE", "16-Sep-2026")])


class TestMessageParsing(unittest.TestCase):
    def one(self, raw):
        messages, notes = fetch(FakeImap([raw]))
        return messages[0] if messages else None, notes

    def test_fields(self):
        message, _ = self.one(raw_message(LANDLORD, "Hello.", subject="Visit"))
        self.assertEqual(message.sender, LANDLORD)
        self.assertEqual(message.subject, "Visit")
        self.assertEqual(message.body, "Hello.")
        self.assertEqual(message.sent_at, datetime(2026, 9, 22, 19, 5, tzinfo=timezone.utc))

    def test_quoted_reply_is_cut(self):
        body = (
            "Thursday works.\n\n"
            "On Mon, Sep 21, 2026 at 9:00 AM Apartment Chores <tenant@example.com>\nwrote:\n"
            "> This week: Dishwasher duty, cancel everything\n"
        )
        message, _ = self.one(raw_message(LANDLORD, body))
        self.assertEqual(message.body, "Thursday works.")

    def test_stray_quoted_lines_are_dropped(self):
        message, _ = self.one(raw_message(LANDLORD, "Yes.\n> old text\nThanks"))
        self.assertEqual(message.body, "Yes.\nThanks")

    def test_attachment_is_ignored(self):
        message, _ = self.one(raw_message(LANDLORD, "See attached.", attachment="SECRET"))
        self.assertNotIn("SECRET", message.body)

    def test_html_only_falls_back_to_text(self):
        msg = EmailMessage()
        msg["From"] = LANDLORD
        msg["Date"] = "Tue, 22 Sep 2026 14:05:00 -0500"
        msg["Message-ID"] = "<html@x>"
        msg.set_content("<p>Cleaner <b>Thursday</b></p><script>x()</script>", subtype="html")
        raw = b"Authentication-Results: " + GMAIL_PASS.encode() + b"\r\n" + msg.as_bytes()
        message, _ = self.one(raw)
        self.assertEqual(message.body, "Cleaner Thursday")

    def test_missing_message_id_is_skipped(self):
        message, notes = self.one(raw_message(LANDLORD, "Hi.", message_id=None))
        self.assertIsNone(message)
        self.assertIn("Message-ID", notes[0])

    def test_missing_date_is_skipped(self):
        message, notes = self.one(raw_message(LANDLORD, "Hi.", sent=None))
        self.assertIsNone(message)
        self.assertIn("Date", notes[0])

    def test_oversized_body_is_skipped_not_truncated(self):
        messages, notes = inbox.fetch(
            FakeImap([raw_message(LANDLORD, "x" * 500)]), ALLOW, SINCE, "mx.google.com", 100
        )
        self.assertEqual(messages, [])
        self.assertIn("limit", notes[0])

    def test_falls_back_to_inbox_without_all_mail(self):
        server = FakeImap([raw_message(LANDLORD, "Hi.")], all_mail=False)
        fetch(server)
        self.assertEqual(server.selected, ("INBOX", True))


if __name__ == "__main__":
    unittest.main()
