"""What the owner should do next, and how his search is actually going.

Built 2026-09-20, when he said the Airtable base was too much to sift through
and he wanted one page to open. The reason a base cannot be that page is
structural rather than aesthetic: the free plan caps it at 1,000 records, the
sync caps postings at 600, and there are more surfaced postings than that, so
Airtable can only ever show him a rotating fraction of his own search ordered by
nothing in particular.

This module computes; `tools/dashboard.py` renders and writes the page. The
split is the same one `agent/delivery.py` and `agent/notify.py` have: what goes
on the page and how it reads change for different reasons and at different
times.

THE LEVERAGE SCORE is the only opinionated thing here and it is deliberately
arithmetic rather than a model call. He asked what the highest-leverage thing he
can do right now is, and an answer he cannot audit is worth less than one he
can, so every item carries the reasons that scored it and the page prints them.
It is also free, which means it reruns on every poll rather than on a budget.

What it is NOT is a second ranker. `agent/ranker.py` decides how good a posting
is for him, reading `rubric.md`; this decides how urgent it is that he acts,
which is a different question with different inputs: a tier 2 role closing on
Friday outranks a tier 1 that is rolling. Tier is one input of six here and the
rubric stays the only place fit is defined, per CLAUDE.md rule 3.
"""

from __future__ import annotations

import datetime as dt
import sqlite3

from . import gcal

# ---------------------------------------------------------------- the weights

# Every number below is a judgement and all of them are wrong in the third
# decimal place. They are here, named, so the page can explain itself and so
# changing the emphasis is an edit to one dict rather than a rewrite.
#
# The scale is arbitrary and only the ORDER matters. Roughly: anything with a
# real date attached beats anything without one, because a deadline is the only
# input here that is not reversible.
WEIGHTS = {
    "tier1": 40.0,           # the ranker says this is worth his afternoon
    "tier2": 18.0,
    # A scored-and-poor role is not neutral, it is a known bad use of an
    # afternoon, and without these a tier 4 posting with a real deadline
    # outranks a tier 1 that is rolling. Figma's "Brand Design Intern" reached
    # second place on the first run of this module for exactly that reason.
    # These apply only where the ranker has actually spoken; an unscored
    # posting stays neutral, because the ranker is windowed and silence about a
    # posting means it was never read, not that it is bad.
    "tier3": -12.0,
    "tier4": -30.0,
    "interested": 30.0,      # he personally said yes, which beats a model saying yes
    "window_closing": 45.0,  # a company-wide application window about to shut
    "deadline_soon": 50.0,   # the posting states a date and it is close
    "referral": 12.0,        # a contact at the company, per agent/contacts.py
    "fresh": 6.0,            # posted in the last few days, so the pool is empty
    "stale_penalty": -8.0,   # open a long time with nothing logged against it
}

# A window or deadline inside this many days is "closing" and scores the full
# weight; further out it decays linearly to nothing at DECAY_DAYS.
URGENT_DAYS = 14
DECAY_DAYS = 60

# How many items each panel carries before the rest become a count. The page is
# meant to be glanceable; a list of eighty is the Airtable problem again.
DO_NEXT_LIMIT = 12
PIPELINE_LIMIT = 40

# An application with no movement after this many days is called out. Matches
# `silent_after_days` in sources/email.toml, which is the same judgement about
# the same fact and should not drift from it.
SILENT_AFTER_DAYS = 21

OPEN = "closed_detected_at IS NULL AND COALESCE(closed_by_me, 0) = 0"
LIVE = "not_applied"
FINISHED = ("rejected", "skipped", "missed")

# What counts as an application actually sent. "skipped" and "missed" record a
# role he decided against or let close, which is useful state and is not an
# application; counting them would inflate the denominator of the one number he
# is trying to read off this page.
SENT = ("applied", "interviewing", "rejected", "offer")
SENT_SQL = "(" + ", ".join(f"'{s}'" for s in SENT) + ")"


def _today() -> dt.date:
    return dt.date.today()


def _days_until(value) -> int | None:
    """Whole days from today to an ISO date, or None if it will not parse.

    Tolerant on purpose. `stated_deadline` is written by Stage 0 from whatever
    the posting's prose said, so it holds "rolling", "ASAP" and empty strings far
    more often than it holds a date. A value that is not a date is not an error
    here, it is the ordinary case, and it must never take the page down.
    """
    if not value:
        return None
    text = str(value).strip()[:10]
    try:
        return (dt.date.fromisoformat(text) - _today()).days
    except ValueError:
        return None


def _urgency(days: int | None) -> float:
    """1.0 for something closing now, decaying to 0.0 at DECAY_DAYS, 0 if past.

    Returns 0 for a date already gone rather than a negative number. A window
    that shut last week is not negatively urgent, it is simply not an argument
    for doing anything, and letting it go negative would push genuinely good
    postings below bad ones for a reason nobody could read off the page.
    """
    if days is None or days < 0:
        return 0.0
    if days <= URGENT_DAYS:
        return 1.0
    if days >= DECAY_DAYS:
        return 0.0
    return (DECAY_DAYS - days) / (DECAY_DAYS - URGENT_DAYS)


def _windows_by_company() -> dict[str, dict]:
    """Open application windows from sources/cycle_windows.toml, keyed by company.

    Read through `gcal.load_windows` rather than parsed again here, so the page
    and the calendar can never disagree about when Microsoft shuts.
    """
    out: dict[str, dict] = {}
    today = _today()
    try:
        windows = gcal.load_windows()
    except Exception:
        # The page must render without this file. A window is an enrichment;
        # every other panel stands on the database alone.
        return out
    for w in windows:
        if w.start <= today <= w.end:
            days = (w.end - today).days
            key = w.company.strip().lower()
            if key not in out or days < out[key]["days_left"]:
                out[key] = {
                    "company": w.company,
                    "label": w.label,
                    "days_left": days,
                    "confidence": w.confidence,
                    "action": w.action,
                    "url": w.url,
                }
    return out


def _contacts_by_company(conn) -> set[str]:
    try:
        return {
            (r[0] or "").strip().lower()
            for r in conn.execute("SELECT company FROM contacts")
        }
    except sqlite3.Error:
        return set()


def score_posting(row: dict, window: dict | None, has_contact: bool) -> dict:
    """How urgent is it that the owner acts on this posting today?

    Returns the score and, just as importantly, the human-readable reasons that
    produced it. The reasons are not decoration: a ranking he cannot audit is a
    ranking he has to trust blindly, and he has already told me he does not want
    to sift through a list he cannot interrogate.
    """
    score = 0.0
    reasons: list[str] = []

    tier = row.get("tier")
    if tier == 1:
        score += WEIGHTS["tier1"]
        reasons.append("tier 1")
    elif tier == 2:
        score += WEIGHTS["tier2"]
        reasons.append("tier 2")
    elif tier == 3:
        score += WEIGHTS["tier3"]
    elif tier == 4:
        score += WEIGHTS["tier4"]
        reasons.append("tier 4, probably not worth it")

    if (row.get("label") or "") == "interested":
        score += WEIGHTS["interested"]
        reasons.append("you marked this interested")

    deadline_days = _days_until(row.get("stated_deadline"))
    if deadline_days is not None:
        urgency = _urgency(deadline_days)
        if urgency > 0:
            score += WEIGHTS["deadline_soon"] * urgency
            reasons.append(
                "closes in %d day%s" % (deadline_days, "" if deadline_days == 1 else "s")
            )

    if window:
        urgency = _urgency(window["days_left"])
        if urgency > 0:
            score += WEIGHTS["window_closing"] * urgency
            reasons.append(f"{window['company']} window shuts in {window['days_left']} days")

    if has_contact:
        score += WEIGHTS["referral"]
        reasons.append("you know someone here")

    age = _days_until(row.get("first_seen"))
    age_days = -age if age is not None else None
    if age_days is not None and age_days <= 5:
        score += WEIGHTS["fresh"]
        reasons.append("posted this week")
    elif age_days is not None and age_days > 45 and not row.get("label"):
        score += WEIGHTS["stale_penalty"]

    return {"score": round(score, 1), "reasons": reasons}


def _row(r: sqlite3.Row) -> dict:
    return {k: r[k] for k in r.keys()}


def do_next(conn, limit: int = DO_NEXT_LIMIT) -> list[dict]:
    """The highest-leverage things he could do right now, best first.

    Restricted to postings he has NOT applied to, because this panel answers
    "what should I do" and an application already sent is not a thing to do. It
    deliberately includes postings with no tier: the ranker is windowed to the
    last fortnight, so an unscored posting is usually just an older one, and
    dropping them would hide exactly the roles that are closing soonest.
    """
    windows = _windows_by_company()
    contacts = _contacts_by_company(conn)
    conn.row_factory = sqlite3.Row
    rows = [
        _row(r)
        for r in conn.execute(
            f"SELECT * FROM postings WHERE {OPEN} AND prefilter_verdict = 'surface' "
            f"AND applied_status = '{LIVE}'"
        )
    ]
    scored = []
    for row in rows:
        key = (row.get("company") or "").strip().lower()
        verdict = score_posting(row, windows.get(key), key in contacts)
        if verdict["score"] <= 0:
            continue
        row.update(verdict)
        scored.append(row)
    scored.sort(key=lambda r: (-r["score"], r.get("company") or ""))
    return scored[:limit]


def pipeline(conn, limit: int = PIPELINE_LIMIT) -> list[dict]:
    """Everywhere he has applied, and how long it has been silent.

    Not restricted to open postings. An application to a role that has since
    closed is still an application he is waiting on, and hiding it would make
    the pipeline lie about how many irons are in the fire.
    """
    conn.row_factory = sqlite3.Row
    rows = [
        _row(r)
        for r in conn.execute(
            f"SELECT * FROM postings WHERE applied_status IN {SENT_SQL} "
            "ORDER BY COALESCE(applied_at, first_seen) DESC"
        )
    ]
    for row in rows:
        since = _days_until(row.get("applied_at") or row.get("first_seen"))
        row["days_since"] = -since if since is not None else None
        row["silent"] = (
            row["days_since"] is not None
            and row["days_since"] >= SILENT_AFTER_DAYS
            and row.get("applied_status") not in FINISHED
        )
    return rows[:limit]


def upcoming(conn) -> list[dict]:
    """Interviews, assessments and anything else with a date, soonest first."""
    conn.row_factory = sqlite3.Row
    rows = [
        _row(r)
        for r in conn.execute(
            "SELECT * FROM postings WHERE next_event_at IS NOT NULL "
            "AND TRIM(next_event_at) <> '' ORDER BY next_event_at"
        )
    ]
    out = []
    for row in rows:
        days = _days_until(row.get("next_event_at"))
        if days is not None and days < -1:
            continue
        row["days_until"] = days
        out.append(row)
    return out


def outlook(conn) -> dict:
    """The numbers he asked for: how the search is actually going.

    The response rate is the one that matters to him and it is also the one that
    is easy to compute dishonestly. It counts applications that got ANY reply,
    which means interviewing, offer and rejected: a rejection is a response and
    pretending otherwise flatters the number. Applications still sitting at
    `applied` are not counted as failures either, because they have not failed
    yet; they are simply not yet evidence of anything.
    """
    q = lambda s, *a: conn.execute(s, a).fetchone()[0]
    surfaced = q(f"SELECT COUNT(*) FROM postings WHERE {OPEN} AND prefilter_verdict='surface'")
    applied = q(f"SELECT COUNT(*) FROM postings WHERE applied_status IN {SENT_SQL}")
    responded = q(
        "SELECT COUNT(*) FROM postings WHERE applied_status IN "
        "('interviewing', 'offer', 'rejected')"
    )
    interviewing = q("SELECT COUNT(*) FROM postings WHERE applied_status = 'interviewing'")
    offers = q("SELECT COUNT(*) FROM postings WHERE applied_status = 'offer'")
    waiting = q("SELECT COUNT(*) FROM postings WHERE applied_status = 'applied'")
    scored = q(
        f"SELECT COUNT(*) FROM postings WHERE {OPEN} AND prefilter_verdict='surface' "
        "AND tier IS NOT NULL"
    )
    tier1 = q(
        f"SELECT COUNT(*) FROM postings WHERE {OPEN} AND prefilter_verdict='surface' "
        "AND tier = 1 AND applied_status = ?", LIVE
    )
    interested_open = q(
        f"SELECT COUNT(*) FROM postings WHERE {OPEN} AND label='interested' "
        "AND applied_status = ?", LIVE
    )
    return {
        "tracked": q("SELECT COUNT(*) FROM postings"),
        "open": q(f"SELECT COUNT(*) FROM postings WHERE {OPEN}"),
        "surfaced": surfaced,
        "scored": scored,
        "tier1_waiting": tier1,
        "interested_waiting": interested_open,
        "applied": applied,
        "waiting": waiting,
        "interviewing": interviewing,
        "offers": offers,
        "responded": responded,
        # None rather than 0 when nothing has been applied to. A response rate
        # of "0%" on zero applications reads as a failure and is arithmetic on
        # an empty set; the page says "no applications recorded" instead.
        "response_rate": (responded / applied) if applied else None,
    }


def windows(conn=None) -> list[dict]:
    """Open application windows, soonest to close first."""
    return sorted(_windows_by_company().values(), key=lambda w: w["days_left"])


def collect(conn) -> dict:
    """Everything the page needs, in one pass."""
    return {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "outlook": outlook(conn),
        "do_next": do_next(conn),
        "pipeline": pipeline(conn),
        "upcoming": upcoming(conn),
        "windows": windows(conn),
    }
