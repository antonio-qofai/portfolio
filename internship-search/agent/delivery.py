"""What goes in which email, and when. Milestone 7, PRD section 3.

This module decides. `agent/notify.py` only writes the words. The split matters
because the two change for different reasons: the wording changes when an email
reads badly, and the policy changes when the owner wants less mail or a different
trigger, and neither should require reading the other.

Two files are read and neither is Python.

  rubric.md         which tier goes to the daily digest, the Sunday roundup, or
                    no email at all. That is `delivery` on each tier band, and
                    it lives there because a tier and its delivery are one
                    statement about worth. CLAUDE.md rule 3.
  sources/email.toml  everything else: item caps, the two urgent triggers, when
                    a roundup is due, and how a collapsed role prints.

Nothing here names a company, a posting, a city or a score.
"""

import tomllib
from datetime import datetime, timedelta, timezone

from . import config, db, grouping, rubric

_RULES: dict | None = None

# Key in the agent_state table. A roundup that carried only a closure summary
# stamps no posting, so no posting column can answer "when did the last one go".
LAST_ROUNDUP_KEY = "last_roundup_sent_at"


class DeliveryError(RuntimeError):
    """sources/email.toml is missing or malformed."""


def rules(path=None) -> dict:
    """The parsed sources/email.toml. Cached, like the rubric and the prefilter."""
    global _RULES
    if _RULES is None or path is not None:
        target = path or config.EMAIL_RULES_PATH
        try:
            with open(target, "rb") as fh:
                parsed = tomllib.load(fh)
        except FileNotFoundError as exc:
            raise DeliveryError(f"email rules not found at {target}") from exc
        except tomllib.TOMLDecodeError as exc:
            raise DeliveryError(f"{target} is not valid TOML: {exc}") from exc
        if path is not None:
            return parsed
        _RULES = parsed
    return _RULES


# ------------------------------------------------------------- the daily split

def route(row, cfg=None) -> str:
    """Which email one surfaced posting belongs in.

    Returns 'daily', 'sunday', 'never' or 'unscored'. The last is not a tier
    band: it is a posting the prefilter surfaced that the ranker has not reached.
    That state is normal and can last a long time, because ranking is capped per
    run and the owner pauses it outright while he labels, so treating unscored as
    "wait until it has a tier" can mean "never surface it", which is the miss
    PRD success criterion 1 exists to prevent.
    """
    tier = row.get("tier") if hasattr(row, "get") else row["tier"]
    if not isinstance(tier, int) or tier <= 0:
        return "unscored"
    return rubric.delivery_for(tier, cfg)


def while_ranking_paused(email_rules: dict) -> dict:
    """The email rules with unscored postings carried, for a month the budget
    has paused ranking.

    CLAUDE.md rule 10 allows `daily.include_unscored = false` only while the
    ranker is running. A paused ranker scores nothing, so with the switch off a
    posting found during the pause would reach no email at all. This returns a
    copy and never edits the parsed file.
    """
    out = dict(email_rules)
    out["daily"] = {**email_rules.get("daily", {}), "include_unscored": True}
    return out


def split_daily(rows, cfg=None, email_rules=None) -> dict:
    """Partition this run's reportable postings by where they go.

    Returns the groups bound for the daily digest, already collapsed, ordered
    and capped, plus counts of what was held back and why. Nothing outside
    `shown` is stamped, so everything held is offered again next time.
    """
    email_rules = email_rules or rules()
    daily_cfg = email_rules.get("daily", {})
    collapse_cfg = email_rules.get("collapse", {})

    buckets: dict[str, list[dict]] = {"daily": [], "sunday": [], "never": [],
                                      "unscored": []}
    for row in rows:
        buckets[route(row, cfg)].append(row)

    carried = list(buckets["daily"])
    if daily_cfg.get("include_unscored", True):
        carried += buckets["unscored"]

    groups = grouping.collapse(
        carried,
        lead_key=grouping.by_best,
        enabled=collapse_cfg.get("enabled", True),
    )
    groups.sort(key=lambda g: grouping.by_best(g.lead) + (g.lead.get("company", ""),))

    cap = int(daily_cfg.get("max_items", 8))
    shown = groups[:cap] if cap > 0 else groups
    overflow = groups[cap:] if cap > 0 else []

    return {
        "shown": shown,
        "overflow": len(overflow),
        "overflow_rows": sum(g.size for g in overflow),
        "held_for_roundup": len(buckets["sunday"]),
        "not_emailed": len(buckets["never"]),
        "unscored": len(buckets["unscored"]),
        "collapsed_rows": sum(g.collapsed for g in shown),
    }


# ------------------------------------------------------------- the weekly one

def _parse(ts: str | None):
    """An ISO string as an aware datetime, or None if it will not parse.

    Always aware. A naive value is read as UTC rather than returned as-is, and
    that is not tidiness, it is the fix for a 44 hour outage.

    On 2026-09-20 `tools.log_application` wrote `applied_at` as a bare
    "2026-08-15" while `_applied_stamp` had always written a full `db.now()`
    with an offset. `fromisoformat` parses both happily and returns a naive
    datetime for the first, so the comparison against an aware cutoff in
    `action_content` raised TypeError, the watcher exited 1, and because the
    watcher is a required step every run stopped before both syncs. Seven runs
    failed over two days. `agent/health.py` caught it and mailed it, which is
    the one good half of that story.

    The lesson is not "write the right format", though that was also fixed. Any
    column a person or a later tool can fill will eventually hold a date rather
    than a timestamp, and the spine of the system must not care. Anything that
    parses a stored date in this module goes through here.
    """
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(str(ts).strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def last_weekday_start(weekday: int, when: datetime) -> datetime:
    """Midnight UTC on the most recent occurrence of a weekday, at or before now.

    0 is Monday and 6 is Sunday, matching datetime.weekday().
    """
    midnight = when.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight - timedelta(days=(when.weekday() - weekday) % 7)


def roundup_owed(conn, email_rules=None, when=None) -> bool:
    """Whether a Sunday roundup is due.

    The test is "has one been sent since the most recent Sunday", not "is today
    Sunday". That difference is what survives a closed laptop. launchd runs a
    missed job once on wake, so a digest job scheduled for Sunday evening may
    really run on Monday morning, and a plain day-of-week test would drop that
    week's roundup entirely. PRD section 4 calls a lost run a coverage problem;
    this is the same reasoning applied to the weekly email.

    The first roundup after this ships has no previous send recorded, so it is
    owed immediately rather than waiting up to a week.
    """
    email_rules = email_rules or rules()
    cfg = email_rules.get("roundup", {})
    if not cfg.get("enabled", True):
        return False
    when = when or datetime.now(timezone.utc)
    due_since = last_weekday_start(int(cfg.get("weekday", 6)), when)
    last = _parse(db.get_state(conn, LAST_ROUNDUP_KEY))
    return last is None or last < due_since


def roundup_content(conn, cfg=None, email_rules=None) -> dict:
    """What this week's roundup would carry. Reads only."""
    email_rules = email_rules or rules()
    r = email_rules.get("roundup", {})
    collapse_cfg = email_rules.get("collapse", {})

    tiers = rubric.tiers_with_delivery("sunday", cfg)
    rows = db.roundup_candidates(conn, tiers, float(r.get("lookback_days", 8)))
    groups = grouping.collapse(
        rows, lead_key=grouping.by_best, enabled=collapse_cfg.get("enabled", True)
    )
    cap = int(r.get("max_items", 25))
    shown = groups[:cap] if cap > 0 else groups

    return {
        "tiers": tiers,
        "shown": shown,
        "overflow": max(0, len(groups) - len(shown)),
        "closed": db.closed_since(conn, float(r.get("closed_lookback_days", 7))),
        "lookback_days": float(r.get("lookback_days", 8)),
        "closed_lookback_days": float(r.get("closed_lookback_days", 7)),
    }


def mark_roundup_sent(conn) -> None:
    db.set_state(conn, LAST_ROUNDUP_KEY, db.now())


# ------------------------------------------------------------------- urgent

# Which stamp column each trigger writes. Paired with the query here rather than
# at the call site so a new trigger cannot be added without deciding how it is
# deduplicated, which is the only thing standing between "urgent" and "every run
# emails the same posting again".
DEADLINE_STAMP = "urgent_deadline_alerted_at"
STALE_STAMP = "urgent_stale_alerted_at"


def urgent_content(conn, email_rules=None) -> dict:
    """The two triggers PRD section 3 allows, and nothing else."""
    email_rules = email_rules or rules()
    u = email_rules.get("urgent", {})
    collapse_cfg = email_rules.get("collapse", {})
    if not u.get("enabled", True):
        return {"deadline": [], "stale": [], "rows": [], "enabled": False}

    tiers = [int(t) for t in u.get("tiers", [1])]
    collapse_on = collapse_cfg.get("enabled", True)

    deadline_rows = db.urgent_by_deadline(
        conn, tiers, float(u.get("deadline_within_hours", 72))
    )
    stale_rows = db.urgent_by_age(
        conn,
        tiers,
        float(u.get("stale_after_days", 10)),
        bool(u.get("stale_needs_action", True)),
    )

    return {
        "enabled": True,
        "tiers": tiers,
        "deadline": grouping.collapse(
            deadline_rows, lead_key=grouping.by_best, enabled=collapse_on
        ),
        "stale": grouping.collapse(
            stale_rows, lead_key=grouping.by_best, enabled=collapse_on
        ),
        "deadline_rows": deadline_rows,
        "stale_rows": stale_rows,
        "within_hours": float(u.get("deadline_within_hours", 72)),
        "after_days": float(u.get("stale_after_days", 10)),
    }


def has_urgent(content: dict) -> bool:
    return bool(content.get("deadline") or content.get("stale"))


# ------------------------------------------------------------------ referrals

def action_content(conn, cfg=None, email_rules=None) -> dict:
    """The owner's own open loops, for the block at the top of the daily digest.

    Every other function in this module answers "what has the agent found that
    it has not told him about yet", and everything they return is stamped once
    an email carries it, so it is never offered twice. This one answers "what
    has he started and not finished", which stays true until he finishes it, so
    nothing here is stamped and every list repeats until it empties. CLAUDE.md
    rule 10 is about not stamping what an email did not carry; this is the
    narrower case of there being no stamp to write at all.

    Four groups, and the order is the order they matter in:

      decide    an offer, which is waiting on him and on nobody else
      apply     marked interested, never applied. The highest-value list in the
                system: the judging is already done and the only thing left is
                the part that gets him the job
      silent    applied, and nothing has come back for weeks
      waiting   applied, still inside the normal wait. A count, not a list

    Locations are collapsed exactly as the digest collapses them, so a role
    posted in four cities is one line here too. The lead is the best-scored
    member, matching the digest and not Airtable, because this is an email.
    """
    email_rules = email_rules or rules()
    cfg = cfg or email_rules.get("actions", {})
    if not cfg.get("enabled", True):
        return {"apply": [], "decide": [], "silent": [], "waiting": 0, "over_cap": 0}

    waiting_statuses = list(cfg.get("waiting_statuses", []))
    decision_statuses = list(cfg.get("decision_statuses", []))
    items = db.action_items(conn, waiting_statuses + decision_statuses)

    collapse_cfg = email_rules.get("collapse", {})
    groups = grouping.collapse(
        items["to_apply"],
        lead_key=grouping.by_best,
        enabled=collapse_cfg.get("enabled", True),
    )
    cap = int(cfg.get("max_items", 12))
    shown, over_cap = groups[:cap], max(0, len(groups) - cap)

    decide, silent, waiting = [], [], 0
    cutoff = datetime.now(timezone.utc) - timedelta(
        days=float(cfg.get("silent_after_days", 21))
    )
    for row in items["waiting"]:
        if row.get("applied_status") in decision_statuses:
            decide.append(row)
            continue
        waiting += 1
        applied = _parse(row.get("applied_at"))
        # An application with no date is one he logged before the sync started
        # stamping them, so its age is unknown rather than zero. Reported as
        # waiting and never as silent, because calling it silent would be
        # inventing a date the database does not have.
        if applied is not None and applied < cutoff:
            silent.append(row)

    return {
        "apply": shown,
        "decide": decide,
        "silent": silent,
        "waiting": waiting,
        "over_cap": over_cap,
    }


def has_actions(content: dict) -> bool:
    return bool(
        content["apply"] or content["decide"] or content["silent"] or content["waiting"]
    )


def referrals_for(conn, groups) -> dict[str, list[dict]]:
    """Contacts keyed by the company name as the posting spells it.

    PRD section 7. `agent/contacts.for_company` has been built and called by
    nothing since 2026-08-09; this is what finally calls it. Looked up once per
    distinct company rather than once per posting.
    """
    from . import contacts

    out: dict[str, list[dict]] = {}
    for group in groups:
        name = group.get("company")
        if not name or name in out:
            continue
        found = contacts.for_company(conn, name)
        if found:
            out[name] = found
    return out
