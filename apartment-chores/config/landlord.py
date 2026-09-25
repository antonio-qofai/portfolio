"""Settings for the landlord email reader (PRD-v1.1).

The mailbox this reads is a personal Gmail account, not a throwaway. Its
ordinary mail, and every reply to the weekly digest, sits next to the
landlord's. The allowlist below is therefore the only thing between a
language model and that inbox. src/inbox.py refuses to start if it is empty,
contains a wildcard, or contains anything that is not one whole address.

Adding an address here widens what the model can read. Raise it with the
account owner first; do not do it to catch mail sent from somewhere else.
"""

# Exact, whole addresses. Compared case-insensitively against the From header
# after Gmail has vouched that the message really came from that domain.
LANDLORD_ADDRESSES = ("landlord@example.com",)

IMAP_HOST = "imap.gmail.com"

# Gmail's authentication verdict is trusted only from this server. A sender
# can put any header they like in a message; Gmail's own verdict is the
# topmost Authentication-Results header, stamped on arrival.
TRUSTED_AUTHSERV_ID = "mx.google.com"

# How far back each run looks. Daily runs only need two days; seven covers a
# week of missed or failed runs. Anything older than this is never read.
LOOKBACK_DAYS = 7

# A body longer than this is skipped rather than cut, since cutting could
# drop the sentence that mattered. Landlord mail is a few hundred characters.
MAX_BODY_CHARS = 20000

# PRD-v1.1 §6.4. Chosen for date reasoning, not volume: a handful of calls a
# month. Parameters checked against the claude-api skill, Sept 23 2026.
MODEL = "claude-sonnet-5"
EFFORT = "medium"
MAX_TOKENS = 16000
TIMEOUT_SECONDS = 60.0

# A proposed date outside [send date, send date + this] is treated as a
# misread. Notice of a visit arrives about a week ahead; two months is
# generous for anything else.
FURTHEST_DATE_DAYS = 60
