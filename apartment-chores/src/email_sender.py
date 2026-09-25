"""Gmail SMTP sender.

The one place in the repo that talks to a mail server. Plain text only: the
digest and the nudges are short, and a plain body renders the same everywhere
including a phone lock screen, which is where most of these will be read.

Credentials come from the environment, never from a file in the repo. The
sender must be a personal Gmail account with 2FA and an app password — app
passwords are unavailable on Workspace accounts, so a university address
cannot send here (PRD section 6).

This module does not know who the recipients are. It is handed addresses.
"""

import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
TIMEOUT_SECONDS = 30

ADDRESS_ENV = "GMAIL_ADDRESS"
PASSWORD_ENV = "GMAIL_APP_PASSWORD"

_ADDRESS_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def credentials():
    """Return (address, app_password) from the environment.

    Raises if either is missing. A run that cannot send is a failed run, not
    a run that quietly skips its email — a digest nobody notices is missing
    is the failure mode this whole system exists to avoid.
    """
    address = os.environ.get(ADDRESS_ENV, "").strip()
    password = os.environ.get(PASSWORD_ENV, "").strip()

    missing = [
        name
        for name, value in ((ADDRESS_ENV, address), (PASSWORD_ENV, password))
        if not value
    ]
    if missing:
        raise RuntimeError(
            "cannot send mail: environment variable(s) %s are unset or empty"
            % ", ".join(missing)
        )
    if not _ADDRESS_PATTERN.match(address):
        raise RuntimeError(
            "cannot send mail: %s is %r, which is not an email address"
            % (ADDRESS_ENV, address)
        )
    return address, password


def build_message(subject, body, to_addresses, from_address, display_name=None,
                  html=None):
    """Return the EmailMessage that send_email would send.

    Split out from the sending so the envelope can be inspected and tested
    without a network connection.

    display_name is what the recipient sees instead of the bare address. The
    sending account belongs to one of the roommates, so without it every
    reminder looks like that person chasing the others. The text itself is
    not this module's business and arrives from the caller.
    """
    recipients = validate_recipients(to_addresses)
    if not subject.strip():
        raise ValueError("cannot send mail: the subject is empty")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = _from_header(from_address, display_name)
    message["To"] = ", ".join(recipients)
    message.set_content(body)
    # multipart/alternative. A client that renders HTML shows the HTML; one
    # that does not, and a phone lock screen preview, gets the plain text.
    # Sending both is also the safer option for inbox placement.
    if html is not None and html.strip():
        message.add_alternative(html, subtype="html")
    return message


def _from_header(address, display_name):
    """Return the From value, with a display name when one was given."""
    if display_name is None or not display_name.strip():
        return address
    return formataddr((display_name.strip(), address))


def validate_recipients(to_addresses):
    """Return the recipient list, stripped, or raise if it is unusable."""
    recipients = [str(a).strip() for a in to_addresses if str(a).strip()]
    if not recipients:
        raise ValueError("cannot send mail: no recipients given")
    bad = [a for a in recipients if not _ADDRESS_PATTERN.match(a)]
    if bad:
        raise ValueError(
            "cannot send mail: %s are not email addresses" % bad
        )
    return recipients


def send_email(subject, body, to_addresses, display_name=None, html=None):
    """Send one plain text email. Returns the recipient list actually used."""
    address, password = credentials()
    message = build_message(
        subject, body, to_addresses, address, display_name, html
    )

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(
        SMTP_HOST, SMTP_PORT, context=context, timeout=TIMEOUT_SECONDS
    ) as smtp:
        smtp.login(address, password)
        smtp.send_message(message)
    return message["To"].split(", ")
