"""Plain text email delivery.

Three emails, all built here and all sent by `send`. What goes in each one is
`agent/delivery.py`; this module only decides how it reads. PRD section 3.

  build_digest   daily, the tiers whose rubric band says delivery = "daily",
                 plus anything the ranker has not reached yet, capped and
                 suppressed when empty
  build_roundup  weekly, the tiers whose band says "sunday", plus what closed
  build_urgent   immediate, on the two triggers PRD section 3 allows

Missing SMTP credentials print instead of raising, so a watcher run is never
lost to an email problem. Since 2026-09-07 an unreachable mail server does the
same thing, for the same reason; see `send`.
"""

import smtplib
import ssl
import sys
from email.message import EmailMessage

from . import config, delivery, emailhtml

# How a stored flag reads in the email. PRD section 5 asks for these in capitals
# so the caveat is visible before the owner clicks through.
FLAG_LABELS = {
    "reach": "REACH",
    "leave_required": "LEAVE REQUIRED",
    "unclear_commitment": "UNCLEAR COMMITMENT",
    "unclear_quant": "UNCLEAR QUANT",
    "unclear_location": "UNCLEAR LOCATION",
    "unclear_term": "UNCLEAR TERM",
    "outside_cycle": "OUTSIDE CYCLE",
    "local_full_time": "FULL TIME, IN CHICAGO",
}


def format_flags(flags: str) -> str:
    labels = [FLAG_LABELS.get(f, f.upper()) for f in (flags or "").split(",") if f]
    return f"  [{', '.join(labels)}]" if labels else ""


def _score_line(group) -> str:
    """Fit, reach and the one line reason, when the posting has been scored.

    The owner's overrides are shown where he wrote one, with the model's number
    beside it, on the same principle the ranker stores both: a disagreement
    between him and the model should be visible rather than resolved silently.
    """
    fit, reach = group.get("fit_score"), group.get("reach_score")
    if fit is None and reach is None:
        return "    not yet scored"
    parts = []
    for name, model, override in (
        ("fit", fit, group.get("fit_override")),
        ("reach", reach, group.get("reach_override")),
    ):
        if override is not None:
            parts.append(f"{name} {override} (yours, model said {model})")
        else:
            parts.append(f"{name} {model}")
    line = f"    tier {group.get('tier')}, " + ", ".join(parts)
    reason = (group.get("reason") or "").strip()
    return f"{line}\n    {reason}" if reason else line


def _group_lines(group, referrals, collapse_cfg, with_company: bool = False) -> list[str]:
    """One role, however many cities it was posted in.

    with_company is for the urgent email, which is a flat list with no company
    headings above it. Without it the role has no employer on the line at all.
    """
    joiner = collapse_cfg.get("join", "; ")
    max_locations = int(collapse_cfg.get("max_locations", 4))
    where = group.location_text(joiner, max_locations) or "location not stated"
    name = f"{group.get('company')}: " if with_company else ""

    lines = [
        f"  - {name}{group.get('title')} ({where})"
        f"{format_flags(group.get('flags'))}"
    ]
    if group.collapsed:
        lines.append(
            f"    one role, {group.size} separate listings. Each city keeps its "
            "own row and closes on its own board's evidence."
        )
    lines.append(_score_line(group))

    for contact in referrals.get(group.get("company"), []):
        relationship = contact.get("relationship") or "contact"
        lines.append(f"    REFERRAL: {contact['name']}, {relationship}")

    if group.get("stated_deadline"):
        lines.append(f"    deadline: {str(group['stated_deadline'])[:10]}")
    lines.append(f"    {group.get('url') or ''}")
    return lines


def _by_tier(groups) -> list[tuple]:
    """Groups bucketed into their tier, best first, unscored last."""
    buckets: dict = {}
    for g in groups:
        tier = g.get("tier")
        key = tier if isinstance(tier, int) and tier > 0 else None
        buckets.setdefault(key, []).append(g)
    ordered = sorted(
        (k for k in buckets if k is not None)
    ) + ([None] if None in buckets else [])
    return [(k, buckets[k]) for k in ordered]


def _listing(groups, referrals, collapse_cfg) -> list[str]:
    """Tier headings, then company headings, then the roles."""
    lines: list[str] = []
    for tier, items in _by_tier(groups):
        heading = f"TIER {tier}" if tier else "NOT YET SCORED"
        rows = sum(g.size for g in items)
        suffix = f" ({len(items)})" if rows == len(items) else \
            f" ({len(items)} roles, {rows} listings)"
        lines.append(heading + suffix)
        if tier is None:
            lines.append(
                "  Surfaced by the filter but not yet scored, so no tier decides "
                "where they belong. Listed rather than held back, because "
                "ranking is capped per run and can be paused."
            )
        lines.append("")
        by_company: dict[str, list] = {}
        for g in items:
            by_company.setdefault(g.get("company"), []).append(g)
        for company in sorted(by_company):
            lines.append(company)
            for g in sorted(by_company[company], key=lambda x: x.get("title") or ""):
                lines.extend(_group_lines(g, referrals, collapse_cfg))
            lines.append("")
    return lines


def _footer(email_rules) -> list[str]:
    lines: list[str] = []
    url = (email_rules.get("links", {}).get("airtable_unlabeled_url") or "").strip()
    if url:
        lines.append("")
        lines.append(f"Label what you have not labelled: {url}")
    reminders = email_rules.get("reminders", {}).get("lines", [])
    if reminders:
        lines.append("")
        for line in reminders:
            lines.append(f"Reminder: {line}")
    return lines


# --------------------------------------------------------------- daily digest

def _action_lines(actions: dict, collapse_cfg: dict, url: str = "") -> list[str]:
    """The YOUR MOVE block. The owner's open loops, not the agent's findings.

    Deliberately worded as work rather than as news. Everything below this block
    in the digest is something the agent found; this is something he started, and
    it will say the same thing tomorrow unless he moves it. The two read
    differently on purpose so the standing list is not mistaken for new arrivals.
    """
    lines: list[str] = []
    if not actions or not delivery.has_actions(actions):
        return lines

    lines.append("YOUR MOVE")
    lines.append("")

    for row in actions["decide"]:
        lines.append(f"  OFFER  {row['company']}: {row['title']}")
        lines.append(f"    waiting on you. {row.get('url', '')}")
    if actions["decide"]:
        lines.append("")

    if actions["apply"]:
        total = len(actions["apply"]) + actions["over_cap"]
        lines.append(f"  Marked interested, not applied ({total})")
        joiner = collapse_cfg.get("join", "; ")
        max_locations = int(collapse_cfg.get("max_locations", 4))
        for group in actions["apply"]:
            row = group.lead
            where = group.location_text(joiner, max_locations)
            lines.append(f"    - {row['company']}: {row['title']}")
            detail = [f"      {where}" if where else "      "]
            if row.get("stated_deadline"):
                detail.append(f"closes {str(row['stated_deadline'])[:10]}")
            since = str(row.get("first_seen") or "")[:10]
            if since:
                detail.append(f"open since {since}")
            lines.append("  ".join(x for x in detail if x.strip()))
            if row.get("label_reason"):
                lines.append(f"      you said: {str(row['label_reason']).strip()}")
            lines.append(f"      {row.get('url', '')}")
        if actions["over_cap"]:
            lines.append(
                f"    and {actions['over_cap']} more, in the Interested view of "
                "the base."
            )
        if url:
            lines.append(f"    Work the list: {url}")
        lines.append("")

    for row in actions["silent"]:
        applied = str(row.get("applied_at") or "")[:10]
        lines.append(
            f"  No reply since {applied}: {row['company']}, {row['title']}"
        )
    if actions["silent"]:
        lines.append("")

    if actions["waiting"]:
        lines.append(f"  {actions['waiting']} application(s) waiting on a reply.")
        lines.append("")

    lines.append(
        "  This block repeats until you move it. Nothing here is marked as sent, "
        "because it is your list rather than the agent's."
    )
    lines.append("")
    return lines


def build_digest(
    split: dict,
    closed: list[dict],
    stats: dict,
    closed_interested: list[dict] | None = None,
    referrals: dict | None = None,
    email_rules: dict | None = None,
    actions: dict | None = None,
) -> tuple[str, str]:
    """The daily email. `split` comes from delivery.split_daily."""
    email_rules = email_rules or delivery.rules()
    referrals = referrals or {}
    collapse_cfg = email_rules.get("collapse", {})
    shown = split.get("shown", [])
    lines: list[str] = []

    # Above everything, including the seeding notice. A door closing on
    # something he wanted is the only thing in this email he cannot act on
    # later, and the whole reason it exists is that these were previously
    # buried in a flat CLOSED list that quiet runs never emailed at all.
    if closed_interested:
        lines.append(f"CLOSED, AND YOU MARKED THESE INTERESTED ({len(closed_interested)})")
        for p in closed_interested:
            when = str(p.get("closed_detected_at") or "")[:10]
            lines.append(f"  - {p['company']}: {p['title']}")
            lines.append(f"    last seen open {str(p.get('last_seen_open') or '')[:10]}, "
                         f"detected closed {when}")
            if p.get("label_reason"):
                lines.append(f"    you said: {str(p['label_reason']).strip()}")
            lines.append(f"    {p.get('url', '')}")
        lines.append("")
        lines.append(
            "If you already applied to one of these, the application still stands; "
            "boards routinely delist a role while the pipeline is live."
        )
        lines.append("")

    # Below the closures and above everything else. A closure is the one thing in
    # this email he can no longer act on, so it keeps the top; this is the one
    # thing he can act on right now, so it takes second.
    lines.extend(
        _action_lines(
            actions or {},
            collapse_cfg,
            (email_rules.get("links", {}).get("airtable_interested_url") or "").strip(),
        )
    )

    if stats.get("seeding"):
        lines.append(
            "First run. The database was empty, so every open posting was recorded as a "
            "baseline rather than emailed. From the next run on, this digest lists only "
            "postings that are genuinely new."
        )
        lines.append("")

    if stats.get("seeded_sources"):
        lines.append(
            f"{len(stats['seeded_sources'])} source(s) were polled for the first time "
            f"and recorded {stats.get('seeded_count', 0)} postings as a baseline rather "
            "than listing them here: "
            f"{', '.join(stats['seeded_sources'])}. Everything a brand new source "
            "returns is new by definition, which is noise, not news. To see what they "
            "brought in, run tools.backlog_report."
        )
        lines.append("")

    if stats.get("unstable"):
        lines.append(stats["unstable"])
        lines.append("")

    if shown:
        if stats.get("carried_over"):
            lines.append(
                f"{stats['carried_over']} of these were found by an earlier polling "
                "run today that does not send email. This is the first time they "
                "have been listed."
            )
            lines.append("")
        lines.extend(_listing(shown, referrals, collapse_cfg))

    # What was deliberately left out, so the cap and the tier routing are never
    # silent. A number he can see is a number he can change; sources/email.toml
    # and rubric.md are where both live.
    withheld = []
    if split.get("overflow"):
        withheld.append(
            f"{split['overflow']} more role(s) did not fit the "
            f"{email_rules.get('daily', {}).get('max_items', 8)} item cap. They are "
            "not stamped, so they lead the next digest."
        )
    if split.get("held_for_roundup"):
        withheld.append(
            f"{split['held_for_roundup']} posting(s) scored into a tier that goes to "
            "the Sunday roundup rather than here."
        )
    if split.get("not_emailed"):
        withheld.append(
            f"{split['not_emailed']} posting(s) scored into a tier that is never "
            "emailed. They are in SQLite and in the Airtable base."
        )
    if withheld:
        lines.append("HELD BACK")
        for line in withheld:
            lines.append(f"  - {line}")
        lines.append("")

    if closed:
        lines.append(f"CLOSED SINCE LAST RUN ({len(closed)})")
        for p in sorted(closed, key=lambda x: (x["company"], x["title"])):
            lines.append(f"  - {p['company']}: {p['title']}")
        lines.append("")

    lines.append("---")
    lines.append(
        f"Sources polled: {stats.get('sources_ok', 0)} ok, "
        f"{stats.get('sources_failed', 0)} failed. "
        f"Postings seen: {stats.get('postings_seen', 0)}. "
        f"Open postings tracked: {stats.get('open_total', 0)}."
    )
    if stats.get("failures"):
        lines.append("")
        lines.append("Sources that failed this run:")
        for name, err in stats["failures"]:
            lines.append(f"  - {name}: {err}")
    if stats.get("empty_sources"):
        lines.append("")
        lines.append(
            "Sources that answered but returned 0 postings. These are not errors and "
            "will never fail loudly, so they are listed here. A board that stays empty "
            "usually means the company moved off the ATS its token assumes:"
        )
        for name in stats["empty_sources"]:
            lines.append(f"  - {name}")
    if stats.get("hidden_by_prefilter"):
        lines.append(
            f"{stats['hidden_by_prefilter']} other new posting(s) were excluded by the "
            "Stage A prefilter or are still awaiting intake tagging. They are stored in "
            "SQLite, not lost; sources/prefilter.toml is where the rules live."
        )
    if stats.get("triage"):
        lines.append("")
        # Imported here rather than at module scope: notify is also imported by
        # the calendar and backlog tools, which have no triage stats to report.
        from .triage import summary_lines

        lines.extend(summary_lines(stats["triage"]))
    lines.extend(_footer(email_rules))

    if stats.get("seeding"):
        subject = f"Internship watcher seeded: {stats.get('postings_seen', 0)} postings recorded"
    elif stats.get("seeded_sources") and not shown:
        subject = (
            f"Internship watcher: {len(stats['seeded_sources'])} new source(s) seeded, "
            f"{stats.get('seeded_count', 0)} postings recorded"
        )
    else:
        top = sum(1 for g in shown if g.get("tier") == 1)
        subject = f"Internship watcher: {len(shown)} posting{'s' if len(shown) != 1 else ''}"
        if top:
            subject += f", {top} tier 1"

    return subject, "\n".join(lines)


# -------------------------------------------------------------- Sunday roundup

def build_roundup(
    content: dict,
    referrals: dict | None = None,
    email_rules: dict | None = None,
) -> tuple[str, str]:
    """The weekly email. Lower tiers, plus what closed. PRD section 3."""
    email_rules = email_rules or delivery.rules()
    referrals = referrals or {}
    collapse_cfg = email_rules.get("collapse", {})
    shown = content.get("shown", [])
    closed = content.get("closed", [])

    tiers = ", ".join(str(t) for t in content.get("tiers", [])) or "none"
    lines = [
        "WEEKLY ROUNDUP",
        "",
        f"Tier {tiers}: worth knowing about, not worth interrupting you for. "
        f"Everything surfaced in the last {int(content.get('lookback_days', 8))} days "
        "that no email has carried.",
        "",
    ]

    if shown:
        lines.extend(_listing(shown, referrals, collapse_cfg))
    else:
        lines.append("Nothing new in these tiers this week.")
        lines.append("")

    if content.get("overflow"):
        lines.append(
            f"{content['overflow']} more did not fit the cap. They are not stamped, "
            "so they lead next week's roundup."
        )
        lines.append("")

    days = int(content.get("closed_lookback_days", 7))
    closed_cap = int(email_rules.get("roundup", {}).get("closed_max_items", 40))
    lines.append(f"CLOSED IN THE LAST {days} DAYS ({len(closed)})")
    if closed:
        # Interested first, whatever the date. A door shutting on something he
        # wanted is the only line in this email he cannot act on later, and the
        # cap below must never be what hides it.
        ordered = sorted(closed, key=lambda p: p.get("label") != "interested")
        for p in ordered[:closed_cap]:
            when = str(p.get("closed_detected_at") or "")[:10]
            mark = " (you were interested)" if p.get("label") == "interested" else ""
            lines.append(f"  - {when}  {p['company']}: {p['title']}{mark}")
        if len(ordered) > closed_cap:
            lines.append(
                f"  ...and {len(ordered) - closed_cap} more. Raise "
                "roundup.closed_max_items in sources/email.toml to see them all."
            )
    else:
        lines.append("  Nothing closed.")
    lines.append("")
    lines.append("---")
    lines.append(
        "This is the roundup for the tiers rubric.md routes to Sunday. Moving a "
        "tier between this email and the daily one is an edit to its band in "
        "rubric.md; the caps and the window are sources/email.toml."
    )
    lines.extend(_footer(email_rules))

    subject = (
        f"Internship roundup: {len(shown)} posting{'s' if len(shown) != 1 else ''}, "
        f"{len(closed)} closed"
    )
    return subject, "\n".join(lines)


# ----------------------------------------------------------------- urgent mail

def build_urgent(
    content: dict,
    referrals: dict | None = None,
    email_rules: dict | None = None,
) -> tuple[str, str]:
    """The immediate email. Two triggers only, per PRD section 3."""
    email_rules = email_rules or delivery.rules()
    referrals = referrals or {}
    collapse_cfg = email_rules.get("collapse", {})
    deadline = content.get("deadline", [])
    stale = content.get("stale", [])

    lines = [
        "URGENT",
        "",
        "This email fires the moment a trigger does, on any polling run rather "
        "than only the evening one. Each posting can raise each trigger once.",
        "",
    ]

    if deadline:
        hours = int(content.get("within_hours", 72))
        lines.append(f"DEADLINE INSIDE {hours} HOURS ({len(deadline)})")
        lines.append("")
        for g in deadline:
            lines.extend(_group_lines(g, referrals, collapse_cfg, with_company=True))
        lines.append("")

    if stale:
        days = int(content.get("after_days", 10))
        lines.append(f"OPEN {days}+ DAYS WITH NOTHING LOGGED ({len(stale)})")
        lines.append("")
        lines.append(
            "  These are top-tier roles you have neither labelled nor applied to. "
            "Label one in Airtable and it stops appearing here."
        )
        lines.append("")
        for g in stale:
            lines.extend(_group_lines(g, referrals, collapse_cfg, with_company=True))
        lines.append("")

    lines.append("---")
    lines.append(
        "Both triggers and the tiers that can raise them are sources/email.toml. "
        "Set urgent.enabled = false there to switch this email off entirely."
    )
    lines.extend(_footer(email_rules))

    parts = []
    if deadline:
        parts.append(f"{len(deadline)} deadline{'s' if len(deadline) != 1 else ''} close")
    if stale:
        parts.append(f"{len(stale)} ageing unactioned")
    subject = "Internship URGENT: " + ", ".join(parts)
    return subject, "\n".join(lines)


def send(subject: str, body: str) -> bool:
    """Returns True if the email actually went out. Never raises.

    Every caller is written against that sentence. `agent/run.py` stamps a
    posting as alerted only `if sent`, which is CLAUDE.md rule 10, and
    `tools/scheduled_run.py` stamps a health alert on the same test. A caller
    that has to guard the call as well as the answer gets the rule wrong
    eventually.

    Until 2026-09-07 the sentence was only half true. An unconfigured mailbox
    returned False; every transport error raised, and the exception went all the
    way out of `agent/run.py:main`. So a laptop with no network turned into a
    failed run: six days in the fortnight to 2026-09-07 ended that way, each one
    recorded as a failure of the watcher, which had in fact polled 176 boards
    and stored everything it found. Because the watcher is the required step,
    the run also stopped before the Airtable and calendar syncs, and Airtable
    only syncs on the digest job, so the base went unmirrored on exactly the
    days the mail was down.

    Nothing was lost from the digest itself, because a send that did not happen
    stamps nothing and the postings are offered again on the next run. What was
    lost was the ability to tell a mail outage from a broken watcher by reading
    the log.

    Returning False does not make the failure quiet. The caller reports it in
    the run report, and `tools/scheduled_run.py` turns that into a health fault
    of its own, so an agent that cannot reach its mail server is still a fault
    The owner hears about. It is just no longer a fault attributed to the wrong
    part of the system.
    """
    if not config.email_configured():
        print("\n[email not configured, printing digest instead]\n")
        print(f"Subject: {subject}\n")
        print(body)
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.EMAIL_FROM
    msg["To"] = config.EMAIL_TO
    msg.set_content(body)

    # The readable half, added 2026-09-20 when the owner said the emails were hard
    # to read. Generated FROM the text rather than instead of it: the plain part
    # above stays the source of truth and the fallback, every client shows one
    # or the other, and a reader that prefers text loses nothing.
    #
    # Swallowing the exception is deliberate and is the same principle as the
    # transport catch below. `agent/emailhtml.py` parses an indentation grammar
    # that a change to any builder could break, and losing the styling is a bad
    # day while losing the email is rule 10 territory.
    try:
        msg.add_alternative(emailhtml.render(body, subject), subtype="html")
    except Exception as exc:  # noqa: BLE001 - styling must never cost the send
        print(f"\n[html half could not be built, sending plain text: {exc!r}]",
              file=sys.stderr)

    context = ssl.create_default_context()
    try:
        if config.SMTP_PORT == 465:
            with smtplib.SMTP_SSL(
                config.SMTP_HOST, config.SMTP_PORT, context=context
            ) as server:
                server.login(config.SMTP_USER, config.SMTP_PASSWORD)
                server.send_message(msg)
        else:
            with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as server:
                server.starttls(context=context)
                server.login(config.SMTP_USER, config.SMTP_PASSWORD)
                server.send_message(msg)
    # OSError covers the whole offline family, since socket.gaierror,
    # ConnectionRefusedError, TimeoutError and ssl.SSLError all derive from it.
    # SMTPException covers the server answering and refusing, which includes a
    # rejected app password. Deliberately not `except Exception`: a bug in the
    # message this module built is not a delivery problem and must still raise.
    except (OSError, smtplib.SMTPException) as exc:
        print(f"\n[email could not be sent: {exc!r}]", file=sys.stderr)
        return False
    return True
