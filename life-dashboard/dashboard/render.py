"""Render the brief to a static HTML page, in the PRD layout order:

header, Pressing actions, Today (calendar + chores), Inbox, QofAI, Job search, Reading.
All source text is HTML-escaped; it is data, never markup.
"""

from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path
from string import Template
from typing import Any

from dashboard.actions import split_events
from dashboard.config import ROOT
from dashboard.schema import ConnectorResult, Item
from dashboard.store import FEEDBACK_KINDS

TEMPLATE = ROOT / "web" / "template.html"

# Card ids in page order. Tests assert this order.
CARD_ORDER = ["header", "actions", "today", "inbox", "qofai", "job-search", "reading"]


def _all_day(iso: str | None) -> bool:
    return bool(iso) and len(iso) == 10  # "YYYY-MM-DD"


def _time(iso: str | None) -> str:
    if not iso:
        return ""
    if _all_day(iso):
        return "All day"
    return datetime.fromisoformat(iso).strftime("%-I:%M %p")


def _due(iso: str | None) -> str:
    """'due Sep 27' for dates, 'due Sep 27, 11:59 PM' for datetimes."""
    if not iso:
        return ""
    t = datetime.fromisoformat(iso)
    return "due " + (t.strftime("%b %-d") if _all_day(iso) else t.strftime("%b %-d, %-I:%M %p"))


def _safe_link(url: str) -> str:
    return escape(url) if url.startswith(("https://", "http://")) else ""


def _item(item: Item, meta: str = "", show_summary: bool = True, lead: str = "", pills: str = "") -> str:
    """A row: optional lead column (a time), the title, muted meta on the right, summary below."""
    lead_html = f'<span class="lead">{escape(lead)}</span>' if lead else ""
    meta_html = f'<span class="meta">{escape(meta)}</span>' if meta else ""
    summary = f'<p class="summary">{escape(item.summary)}</p>' if item.summary and show_summary else ""
    cls = ' class="timed"' if lead else ""
    return (
        f'<li{cls}>{lead_html}<div class="main"><div class="line">'
        f'<span class="title">{_title(item)}</span>{pills}{meta_html}</div>{summary}</div></li>'
    )


def _pills(item: Item) -> str:
    """Urgency hints as small tags: 'overdue', 'due today', ..."""
    return "".join(
        f'<span class="pill {escape(h)}">{escape(h.replace("_", " "))}</span>' for h in item.urgency_hints
    )


def _short_due(iso: str | None) -> str:
    """'Sun Oct 4' for chores and deadlines; the time rarely matters at a glance."""
    return datetime.fromisoformat(iso).strftime("%a %b %-d") if iso else ""


def _list(items: list[str], empty: str = "Nothing here.") -> str:
    if not items:
        return f'<p class="empty">{escape(empty)}</p>'
    return "<ul>" + "".join(items) + "</ul>"


def _status(results: list[ConnectorResult]) -> str:
    """An error line per failed connector. Fresh data is as old as the brief, stamped once in the header."""
    parts = []
    for r in results:
        if r.error:
            note = f" Showing last good result from {_stamp(r.last_updated)}." if r.stale else ""
            parts.append(f'<p class="error">{escape(r.name)} failed to load: {escape(r.error)}.{escape(note)}</p>')
    return "".join(parts)


def _episode_day(items: list[Item]) -> str:
    """' · Sun Sep 27': which episode the AI Daily Brief items come from."""
    day = items[0].timestamp if items else None
    return f" · {escape(datetime.fromisoformat(day).strftime('%a %b %-d'))}" if day else ""


def _stamp_day(iso: str | None) -> str:
    """'Tue Sep 29' or 'Tue Sep 29, 2:00 PM' for upcoming events."""
    if not iso:
        return ""
    t = datetime.fromisoformat(iso)
    return t.strftime("%a %b %-d") if _all_day(iso) else t.strftime("%a %b %-d, %-I:%M %p")


def _stamp(iso: str | None) -> str:
    """'Wed Sep 24, 6:00 AM' for stale data, which may be from another day."""
    if not iso:
        return "unknown time"
    return datetime.fromisoformat(iso).strftime("%a %b %-d, %-I:%M %p")


def _card(
    card_id: str, title: str, body: str, results: list[ConnectorResult], extra_class: str = "", count: int = 0,
) -> str:
    cls = f"card {extra_class}".strip()
    count_html = f'<span class="count">{count}</span>' if count else ""
    return (
        f'<section class="{cls}" id="{card_id}">'
        f"<h2>{escape(title)}{count_html}</h2>{body}{_status(results)}</section>"
    )


def _title(item: Item) -> str:
    title = escape(item.title)
    link = _safe_link(item.link)
    return f'<a href="{link}" target="_blank" rel="noopener">{title}</a>' if link else title


def _feedback_buttons() -> str:
    buttons = "".join(
        f'<button type="button" data-feedback="{k}">{escape(k.replace("_", " ").capitalize())}</button>'
        for k in FEEDBACK_KINDS
    )
    return f'<span class="feedback">{buttons}</span>'


def _surfaced(entry: Any, meta: str, checkbox: bool, lead: str = "") -> str:
    """A Pressing action or Coming up event, with feedback buttons behind a toggle (and a check-off box for actions)."""
    first = (
        '<input type="checkbox" class="check" aria-label="Done">' if checkbox
        else f'<span class="lead">{escape(lead)}</span>'
    )
    why = " · ".join(filter(None, [entry.why, meta]))
    why_html = f'<span class="why">{escape(why)}</span>' if why else ""
    return (
        f'<li class="surfaced" data-key="{escape(entry.key)}">{first}'
        f'<div class="main"><span class="title">{_title(entry.item)}</span>{why_html}{_feedback_buttons()}</div>'
        f'<button type="button" class="more" aria-label="Rate this suggestion" title="Rate this suggestion">'
        f'<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="3" cy="8" r="1.4"/><circle cx="8" cy="8" r="1.4"/>'
        f'<circle cx="13" cy="8" r="1.4"/></svg></button></li>'
    )


def _label(item: Item) -> str:
    """'email.uchicago' -> 'UChicago' style label from the sub-source."""
    sub = item.source.split(".", 1)[1] if "." in item.source else item.source
    return {"uchicago": "UChicago", "qofai": "QofAI"}.get(sub, sub.replace("_", " ").title())


def render(brief: dict[str, Any], config: dict) -> str:
    r: dict[str, ConnectorResult] = brief["results"]
    def get(name: str) -> ConnectorResult:
        return r.get(name) or ConnectorResult(name=name, error="connector not registered")

    weather, cal, email, chores = get("weather"), get("calendar"), get("email"), get("chores")
    jobs, nyt, aib = get("job_search"), get("nyt"), get("ai_daily_brief")

    now = datetime.fromisoformat(brief["generated_at"])
    events = split_events(sorted(cal.items, key=lambda i: i.timestamp or ""), now)[0]
    personal_events = [e for e in events if e.section == "personal"]

    # 1. Header
    w = weather.items[0] if weather.items else None
    weather_html = (
        f'<p class="weather"><span class="temp">{escape(w.title)}</span>'
        + (f'<span class="detail">{escape(w.summary)}</span>' if w.summary else "") + "</p>"
        if w else '<p class="weather"><span class="detail">Weather unavailable.</span></p>'
    )
    timed = [e for e in events if not _all_day(e.timestamp)]
    first = (timed or events or [None])[0]
    first_html = (
        f'<p class="first"><span class="label">First up</span>'
        f"{escape(_time(first.timestamp))} · {escape(first.title)}</p>"
        if first else '<p class="first"><span class="label">First up</span>Nothing on the calendar</p>'
    )
    header = (
        f'<header class="hero" id="header">'
        f'<div class="hero-top"><span class="date">{escape(now.strftime("%A, %B %-d"))}</span>'
        f'<span class="stamp">Updated {escape(_time(brief["generated_at"]))}</span></div>'
        f'<h1>Good morning, {escape(config.get("owner_name", ""))}.</h1>'
        f'<div class="facts">{weather_html}{first_html}</div>'
        f"{_status([weather, cal])}</header>"
    )

    # 2. Pressing actions. Connector errors show on their own cards, so only the ranking note shows here.
    actions = [_surfaced(a, _due(a.item.due), checkbox=True) for a in brief["actions"]]
    note = brief.get("actions_note")
    actions_body = (
        (f'<p class="note warn">{escape(note)}</p>' if note else "")
        + (f'<ol class="actions">{"".join(actions)}</ol>' if actions else '<p class="empty">Nothing pressing.</p>')
    )
    actions_card = _card("actions", "Pressing actions", actions_body, [], "primary", count=len(actions))

    # 3. Today: calendar + chores side by side
    upcoming = [
        _surfaced(u, "", checkbox=False, lead=_stamp_day(u.item.timestamp))
        for u in brief.get("upcoming", [])
    ]
    cal_body = _list([_item(e, lead=_time(e.timestamp)) for e in personal_events], "No events.")
    if upcoming:
        cal_body += f'<h3>Coming up</h3><ul>{"".join(upcoming)}</ul>'
    cal_card = _card("today-calendar", "Calendar", cal_body, [cal], "sub")
    chore_items = [c for c in chores.items if c.section == "personal"]
    chore_card = _card(
        "today-chores", "Chores",
        # Titles only: the chore agent's summaries are full definitions of done.
        _list([_item(c, _short_due(c.due), show_summary=False, pills=_pills(c)) for c in chore_items],
              "No chores due."),
        [chores], "sub",
    )
    today = (
        f'<section class="card today" id="today"><div class="split">{cal_card}{chore_card}</div></section>'
    )

    # 4. Inbox (personal inboxes only): pressing school, work and internship email
    personal_mail = [m for m in email.items if m.section == "personal"]
    pressing = [m for m in personal_mail if {"reply_needed", "deadline"} & set(m.urgency_hints)]
    rest = [m for m in personal_mail if m not in pressing]
    inbox_body = _list(
        [_item(m, _short_due(m.due) or _label(m), pills=_pills(m)) for m in pressing],
        "Nothing pressing.",
    )
    if rest:
        inbox_body += (
            f"<details><summary>Everything else ({len(rest)})</summary>"
            f"{_list([_item(m, _label(m), show_summary=False) for m in rest])}</details>"
        )
    inbox = _card("inbox", "Inbox", inbox_body, [email], count=len(pressing))

    # 5. QofAI (kept separate from personal items). Its email lives in Slack, so no inbox here.
    q_events = [e for e in events if e.section == "qofai" and not e.due]
    q_due = [i for res in r.values() for i in res.items if i.section == "qofai" and i.due]
    if q_events or q_due:
        qofai_body = (
            (_list([_item(e, lead=_time(e.timestamp)) for e in q_events]) if q_events else "")
            + ("<h3>Deadlines</h3>" + _list([_item(i, _short_due(i.due)) for i in q_due]) if q_due else "")
        )
    else:
        qofai_body = '<p class="empty">No meetings or deadlines today.</p>'
    qofai = _card("qofai", "QofAI", qofai_body, [cal], "work")

    # 6. Job search
    job_card = _card(
        "job-search", "Job search",
        _list([_item(j, _short_due(j.due), pills=_pills(j)) for j in jobs.items], "Nothing new."),
        [jobs],
    )

    # 7. Reading: NYT and the AI Daily Brief side by side on wide screens
    nyt_items = nyt.items[: config["news"]["cap"]]
    aib_items = aib.items[: config["ai_daily_brief"]["cap"]]
    reading_body = (
        '<div class="split">'
        "<div><h3>NYT</h3>" + _list([_item(n) for n in nyt_items], "No stories.") + "</div>"
        + f"<div><h3>AI Daily Brief{_episode_day(aib_items)}</h3>"
        + _list([_item(a) for a in aib_items], "No items.") + "</div></div>"
    )
    reading = _card("reading", "Reading", reading_body, [nyt, aib])

    body = header + actions_card + today + inbox + f'<div class="pair">{qofai}{job_card}</div>' + reading
    return Template(TEMPLATE.read_text()).substitute(title="Life Dashboard", body=body)


def write_page(html: str, path: Path | None = None) -> Path:
    path = path or ROOT / "web" / "index.html"
    path.write_text(html)
    return path
