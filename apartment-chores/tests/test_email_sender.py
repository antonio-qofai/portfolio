"""Tests for src/email_sender.py.

Nothing here opens a socket. The envelope is built and inspected, and the
credential handling is driven through the environment, so the only untested
line is the SMTP conversation itself.
"""

import os
import unittest

from src import email_sender


class EnvCase(unittest.TestCase):
    """Base class that restores the mail environment after each test."""

    def setUp(self):
        self._saved = {
            name: os.environ.get(name)
            for name in (email_sender.ADDRESS_ENV, email_sender.PASSWORD_ENV)
        }

    def tearDown(self):
        for name, value in self._saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def set_env(self, address=None, password=None):
        for name, value in (
            (email_sender.ADDRESS_ENV, address),
            (email_sender.PASSWORD_ENV, password),
        ):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


class TestCredentials(EnvCase):

    def test_both_present_returns_pair(self):
        self.set_env("bot@gmail.com", "abcd efgh ijkl mnop")
        self.assertEqual(
            email_sender.credentials(), ("bot@gmail.com", "abcd efgh ijkl mnop")
        )

    def test_values_are_stripped(self):
        self.set_env("  bot@gmail.com  ", "  secret  ")
        self.assertEqual(email_sender.credentials(), ("bot@gmail.com", "secret"))

    def test_missing_address_raises(self):
        self.set_env(None, "secret")
        with self.assertRaises(RuntimeError) as ctx:
            email_sender.credentials()
        self.assertIn(email_sender.ADDRESS_ENV, str(ctx.exception))

    def test_missing_password_raises(self):
        self.set_env("bot@gmail.com", None)
        with self.assertRaises(RuntimeError) as ctx:
            email_sender.credentials()
        self.assertIn(email_sender.PASSWORD_ENV, str(ctx.exception))

    def test_both_missing_names_both(self):
        self.set_env(None, None)
        with self.assertRaises(RuntimeError) as ctx:
            email_sender.credentials()
        self.assertIn(email_sender.ADDRESS_ENV, str(ctx.exception))
        self.assertIn(email_sender.PASSWORD_ENV, str(ctx.exception))

    def test_empty_string_counts_as_missing(self):
        self.set_env("   ", "secret")
        with self.assertRaises(RuntimeError):
            email_sender.credentials()

    def test_sender_that_is_not_an_address_raises(self):
        self.set_env("not-an-address", "secret")
        with self.assertRaises(RuntimeError) as ctx:
            email_sender.credentials()
        self.assertIn("not an email address", str(ctx.exception))


class TestValidateRecipients(unittest.TestCase):

    def test_single_recipient(self):
        self.assertEqual(
            email_sender.validate_recipients(["a@uchicago.edu"]), ["a@uchicago.edu"]
        )

    def test_order_is_preserved(self):
        given = ["c@uchicago.edu", "a@uchicago.edu", "b@uchicago.edu"]
        self.assertEqual(email_sender.validate_recipients(given), given)

    def test_whitespace_stripped(self):
        self.assertEqual(
            email_sender.validate_recipients([" a@uchicago.edu "]), ["a@uchicago.edu"]
        )

    def test_blank_entries_dropped(self):
        self.assertEqual(
            email_sender.validate_recipients(["a@uchicago.edu", "", "  "]),
            ["a@uchicago.edu"],
        )

    def test_empty_list_raises(self):
        with self.assertRaises(ValueError) as ctx:
            email_sender.validate_recipients([])
        self.assertIn("no recipients", str(ctx.exception))

    def test_all_blank_raises(self):
        with self.assertRaises(ValueError):
            email_sender.validate_recipients(["", "   "])

    def test_malformed_address_raises(self):
        with self.assertRaises(ValueError) as ctx:
            email_sender.validate_recipients(["a@uchicago.edu", "nope"])
        self.assertIn("nope", str(ctx.exception))


class TestBuildMessage(unittest.TestCase):

    def test_headers(self):
        message = email_sender.build_message(
            "Subject line", "Body line\n", ["a@uchicago.edu"], "bot@gmail.com"
        )
        self.assertEqual(message["Subject"], "Subject line")
        self.assertEqual(message["From"], "bot@gmail.com")
        self.assertEqual(message["To"], "a@uchicago.edu")

    def test_multiple_recipients_joined(self):
        message = email_sender.build_message(
            "S", "B", ["a@uchicago.edu", "b@uchicago.edu"], "bot@gmail.com"
        )
        self.assertEqual(message["To"], "a@uchicago.edu, b@uchicago.edu")

    def test_body_is_plain_text(self):
        message = email_sender.build_message(
            "S", "Body line\n", ["a@uchicago.edu"], "bot@gmail.com"
        )
        self.assertEqual(message.get_content_type(), "text/plain")
        self.assertEqual(message.get_content(), "Body line\n")

    def test_body_survives_unicode(self):
        message = email_sender.build_message(
            "S", "café — done\n", ["a@uchicago.edu"], "bot@gmail.com"
        )
        self.assertIn("café", message.get_content())

    def test_empty_subject_raises(self):
        with self.assertRaises(ValueError) as ctx:
            email_sender.build_message(
                "   ", "B", ["a@uchicago.edu"], "bot@gmail.com"
            )
        self.assertIn("subject is empty", str(ctx.exception))

    def test_no_recipients_raises(self):
        with self.assertRaises(ValueError):
            email_sender.build_message("S", "B", [], "bot@gmail.com")

    def test_display_name_appears_in_from(self):
        message = email_sender.build_message(
            "S", "B", ["a@uchicago.edu"], "bot@gmail.com", "Apartment Chores"
        )
        self.assertEqual(message["From"], "Apartment Chores <bot@gmail.com>")

    def test_no_display_name_leaves_a_bare_address(self):
        message = email_sender.build_message(
            "S", "B", ["a@uchicago.edu"], "bot@gmail.com"
        )
        self.assertEqual(message["From"], "bot@gmail.com")

    def test_blank_display_name_leaves_a_bare_address(self):
        message = email_sender.build_message(
            "S", "B", ["a@uchicago.edu"], "bot@gmail.com", "   "
        )
        self.assertEqual(message["From"], "bot@gmail.com")

    def test_display_name_is_stripped(self):
        message = email_sender.build_message(
            "S", "B", ["a@uchicago.edu"], "bot@gmail.com", "  Chores  "
        )
        self.assertEqual(message["From"], "Chores <bot@gmail.com>")

    def test_display_name_with_a_comma_is_quoted(self):
        """formataddr must quote it, or the comma reads as a second address."""
        message = email_sender.build_message(
            "S", "B", ["a@uchicago.edu"], "bot@gmail.com", "Chores, Apartment"
        )
        self.assertEqual(message["From"], '"Chores, Apartment" <bot@gmail.com>')

    def test_the_address_is_still_reachable(self):
        from email.utils import parseaddr
        message = email_sender.build_message(
            "S", "B", ["a@uchicago.edu"], "bot@gmail.com", "Apartment Chores"
        )
        self.assertEqual(parseaddr(message["From"])[1], "bot@gmail.com")

    def test_html_makes_it_multipart_alternative(self):
        message = email_sender.build_message(
            "S", "plain body", ["a@uchicago.edu"], "bot@gmail.com",
            "Chores", "<div>rich body</div>",
        )
        self.assertTrue(message.is_multipart())
        self.assertEqual(message.get_content_subtype(), "alternative")

    def test_both_parts_are_present_and_plain_text_comes_first(self):
        """Order matters: the last part wins, so HTML must come second."""
        message = email_sender.build_message(
            "S", "plain body", ["a@uchicago.edu"], "bot@gmail.com",
            "Chores", "<div>rich body</div>",
        )
        types = [p.get_content_type() for p in message.iter_parts()]
        self.assertEqual(types, ["text/plain", "text/html"])

    def test_blank_html_stays_plain(self):
        message = email_sender.build_message(
            "S", "plain body", ["a@uchicago.edu"], "bot@gmail.com", "Chores", "   ",
        )
        self.assertFalse(message.is_multipart())

    def test_message_is_not_multipart(self):
        message = email_sender.build_message(
            "S", "B", ["a@uchicago.edu"], "bot@gmail.com"
        )
        self.assertFalse(message.is_multipart())


if __name__ == "__main__":
    unittest.main()
