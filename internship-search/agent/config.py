"""Paths, environment, and tunable settings.

Everything configurable lives here so no other module reads os.environ directly.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

load_dotenv(ROOT / ".env")

# Local state. Not committed; see .gitignore.
DB_PATH = Path(os.getenv("DB_PATH") or ROOT / "state.db")

# The company token map. Hand-edited data, never code.
SOURCES_PATH = ROOT / "sources" / "companies.toml"

# The aggregator feed map. Also hand-edited data.
FEEDS_PATH = ROOT / "sources" / "feeds.toml"

# Known application windows for the cycle. Also hand-edited data.
CYCLE_WINDOWS_PATH = ROOT / "sources" / "cycle_windows.toml"

# Vetted preparation resources and the tasks they ask for.
RESOURCES_PATH = ROOT / "sources" / "resources.toml"

# Referral contacts, PRD section 7. The owner's file; nothing writes to it.
CONTACTS_PATH = ROOT / "sources" / "contacts.toml"

# Everything the Stage A prefilter matches on. Hand-edited data, never code.
PREFILTER_PATH = ROOT / "sources" / "prefilter.toml"

# The Stage 0 tagger's instruction text. Kept out of Python for the same reason
# the rubric is: editing how the tagger reads should never touch the code.
INTAKE_PROMPT_PATH = ROOT / "sources" / "intake_prompt.md"

# The scoring rubric, authoritative at runtime. Prose for the ranker plus the
# tier bands and routing thresholds it is scored against. CLAUDE.md rule 3: no
# scoring criterion, tier definition, or fit rule ever appears in Python.
RUBRIC_PATH = ROOT / "rubric.md"

# The Airtable base described as data: tables, fields, and which SQLite column
# each field carries. Renaming a field is an edit here, never a code change.
AIRTABLE_SCHEMA_PATH = ROOT / "sources" / "airtable.toml"

# When the agent runs: the hours, the launchd labels, the log paths and what a
# run consists of. Changing the schedule is an edit here plus a re-install.
SCHEDULE_PATH = ROOT / "sources" / "schedule.toml"

# What the agent emails, Milestone 7. Item caps, the urgent triggers, when the
# Sunday roundup is due, and how many locations a collapsed role shows. Which
# tier goes to which email is deliberately NOT here; that is `delivery` on the
# tier bands in rubric.md, per CLAUDE.md rule 3.
EMAIL_RULES_PATH = ROOT / "sources" / "email.toml"

# The mailbox, Milestone 6.5. The host, the folder, the lookback window, the
# newsletter senders and the subject patterns are all data in that file. Only the
# credentials are here, for the same reason SMTP's are: the TOML is committed.
INBOX_PATH = ROOT / "sources" / "inbox.toml"

# Google Calendar. Neither file is committed; see .gitignore.
GOOGLE_CREDENTIALS_PATH = Path(
    os.getenv("GOOGLE_CREDENTIALS_PATH") or ROOT / "credentials.json"
)
GOOGLE_TOKEN_PATH = Path(os.getenv("GOOGLE_TOKEN_PATH") or ROOT / ".google-token.json")
CALENDAR_TIMEZONE = os.getenv("CALENDAR_TIMEZONE", "America/Chicago")

# Polling behaviour. One student, not a scraper farm.
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "20"))
# A feed is one JSON file of several megabytes, not a small board response.
FEED_TIMEOUT = float(os.getenv("FEED_TIMEOUT", "60"))
HTTP_RETRIES = int(os.getenv("HTTP_RETRIES", "1"))
POLITE_DELAY = float(os.getenv("POLITE_DELAY", "0.4"))
USER_AGENT = os.getenv(
    "USER_AGENT",
    "internship-search/0.1 (personal job tracker; contact owner@example.edu)",
)

# A posting is closed after this many consecutive polls without seeing it.
# Two polls is roughly 12 hours at a 6 hour cadence. See PRD section 4.
CLOSURE_MISS_THRESHOLD = int(os.getenv("CLOSURE_MISS_THRESHOLD", "2"))

# How much posting text to keep for the Stage 0 intake tagger.
DESCRIPTION_CHARS = int(os.getenv("DESCRIPTION_CHARS", "4000"))

# Stage 0 intake tagging. One small call per genuinely new posting that needs
# one, and never a second call on a posting already tagged.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# PRD section 4 specifies Haiku for Stage 0. It is an extraction task, not a
# judgement one, and the budget in section 2 is ten dollars a month.
STAGE0_MODEL = os.getenv("STAGE0_MODEL", "claude-haiku-4-5")
STAGE0_MAX_TOKENS = int(os.getenv("STAGE0_MAX_TOKENS", "500"))

# Hard ceiling on model calls per run. The first run after Milestone 4 lands has
# a large backlog to tag; every run after it should be a handful of calls. This
# is the guard against a source glitch turning into a bill. Untagged postings
# are not lost when the cap is hit, they are tagged on the next run.
STAGE0_MAX_CALLS = int(os.getenv("STAGE0_MAX_CALLS", "400"))

# Published Haiku 4.5 list price, US dollars per million tokens. Used only to
# populate runs.estimated_cost so cost is observable without opening the
# Anthropic console. Update if the price changes.
STAGE0_INPUT_PRICE = float(os.getenv("STAGE0_INPUT_PRICE", "1.00"))
STAGE0_OUTPUT_PRICE = float(os.getenv("STAGE0_OUTPUT_PRICE", "5.00"))


def stage0_configured() -> bool:
    return bool(ANTHROPIC_API_KEY)


# Milestone 6, the ranker. Two stages, both reading rubric.md at runtime.
#
# PRD section 4 specifies Haiku for Stage B and Sonnet for Stage C. Stage B runs
# on every posting the prefilter surfaced; Stage C re-scores only what clears the
# routing thresholds in rubric.md, which is where most of the money goes and why
# those thresholds are a setting the owner can raise without a code change.
STAGE_B_MODEL = os.getenv("STAGE_B_MODEL", "claude-haiku-4-5")
STAGE_C_MODEL = os.getenv("STAGE_C_MODEL", "claude-sonnet-5")

# Two integers and one sentence come back, so the ceiling only has to cover a
# little JSON. Stage C's is larger because Sonnet thinks before it answers by
# default and max_tokens caps the thinking and the answer together; too small a
# number there truncates the response rather than shortening it.
STAGE_B_MAX_TOKENS = int(os.getenv("STAGE_B_MAX_TOKENS", "300"))
STAGE_C_MAX_TOKENS = int(os.getenv("STAGE_C_MAX_TOKENS", "2000"))

# Haiku 4.5 does not accept the effort parameter at all, so Stage B never sends
# one. Sonnet does; low is enough for a scored judgement against a rubric that
# already states its anchors, and it is the cheapest setting that still thinks.
STAGE_C_EFFORT = os.getenv("STAGE_C_EFFORT", "low")

# Hard ceiling on ranking calls per run, the same guard as STAGE0_MAX_CALLS and
# for the same reason. The first runs after Milestone 6 lands have a backlog of
# roughly 1,200 surfaced postings to score; draining it a batch at a time keeps
# any single run from becoming a bill. Nothing is lost by hitting the cap, since
# an unscored posting is picked up on the next run.
RANK_MAX_CALLS = int(os.getenv("RANK_MAX_CALLS", "150"))

# How much posting text the ranker reads. Deliberately shorter than Stage 0's
# 4,000: Stage 0 hunts for one phrase that could sit anywhere in the body, while
# fit and reach are decided by the opening description and the requirements,
# and this text is billed once per posting per stage rather than once ever.
RANK_DESCRIPTION_CHARS = int(os.getenv("RANK_DESCRIPTION_CHARS", "2500"))

# Published list prices, US dollars per million tokens, used only to populate
# runs.estimated_cost. Update if the price changes. Sonnet 5 carries an
# introductory rate through 2026-08-31; the standard rate is 3.00 and 15.00.
STAGE_B_INPUT_PRICE = float(os.getenv("STAGE_B_INPUT_PRICE", "1.00"))
STAGE_B_OUTPUT_PRICE = float(os.getenv("STAGE_B_OUTPUT_PRICE", "5.00"))
STAGE_C_INPUT_PRICE = float(os.getenv("STAGE_C_INPUT_PRICE", "2.00"))
STAGE_C_OUTPUT_PRICE = float(os.getenv("STAGE_C_OUTPUT_PRICE", "10.00"))

# Anthropic ignores a cache request on a prefix shorter than the model's
# minimum, so asking for one below these numbers is not an error, it is simply
# nothing. The ranker checks against them so the run log can say plainly whether
# the rubric is being cached or not, rather than leaving it a mystery. These are
# model properties, not scoring parameters, which is why they live here and not
# in rubric.md.
# What a cached prompt actually costs, as multipliers on the stage's input price.
# Anthropic bills a cache READ at a tenth of base input and a cache WRITE at one
# and a quarter, so a prompt that caches is an order of magnitude cheaper per
# call after the first. Added 2026-09-22, when `tools.rank_report` was found to
# be charging full input price for a prefix it knew was cached, overstating the
# queue by more than ten times and keeping ranking paused for five weeks.
CACHE_READ_MULTIPLIER = float(os.getenv("CACHE_READ_MULTIPLIER", "0.1"))
CACHE_WRITE_MULTIPLIER = float(os.getenv("CACHE_WRITE_MULTIPLIER", "1.25"))

CACHE_MIN_TOKENS = {
    "claude-haiku-4-5": 4096,
    "claude-sonnet-5": 1024,
}


def ranker_configured() -> bool:
    return bool(ANTHROPIC_API_KEY)

# Email. Missing credentials degrade to printing the digest, never to crashing.
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
EMAIL_FROM = os.getenv("EMAIL_FROM") or SMTP_USER
EMAIL_TO = os.getenv("EMAIL_TO", "owner@example.edu")


def email_configured() -> bool:
    return bool(SMTP_USER and SMTP_PASSWORD and EMAIL_FROM)


# Reading mail, Milestone 6.5. A separate credential from the sending one above,
# even when both are app passwords on the same account, so revoking one cannot
# silently break the other. Missing credentials skip the inbox and never crash a
# run, the same rule email and Airtable already follow.
#
# This is not owner@example.edu, and the reason is recorded in CHANGELOG.md
# under 2026-08-16: UChicago's Workspace has app passwords turned off, so the
# newsletter is forwarded to a mailbox that allows them and read from there.
IMAP_USER = os.getenv("IMAP_USER")
IMAP_PASSWORD = os.getenv("IMAP_PASSWORD")


def inbox_configured() -> bool:
    return bool(IMAP_USER and IMAP_PASSWORD)


# Airtable, Milestone 5. An input surface for labels, never the state store.
# A missing token degrades to skipping the sync, never to crashing a run, on the
# same principle as email above: the watcher is the spine and nothing optional
# should be able to stop it.
AIRTABLE_TOKEN = os.getenv("AIRTABLE_TOKEN")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_API_ROOT = os.getenv("AIRTABLE_API_ROOT", "https://api.airtable.com/v0")


def airtable_configured() -> bool:
    return bool(AIRTABLE_TOKEN and AIRTABLE_BASE_ID)
