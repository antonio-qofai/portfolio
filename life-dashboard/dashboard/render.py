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

from dashboard.config import ROOT
from dashboard.schema import ConnectorResult, Item

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


def _item(item: Item, meta: str = "") -> str:
    title = escape(item.title)
    link = _safe_link(item.link)
    if link:
        title = f'<a href="{link}" target="_blank" rel="noopener">{title}</a>'
    meta_html = f'<span class="meta">{escape(meta)}</span>' if meta else ""
    summary = f'<p class="summary">{escape(item.summary)}</p>' if item.summary else ""
    return f"<li>{meta_html}<span class=\"title\">{title}</span>{summary}</li>"


def _list(items: list[str], empty: str = "Nothing here.") -> str:
    if not items:
        return f'<p class="empty">{escape(empty)}</p>'
    return "<ul>" + "".join(items) + "</ul>"


def _status(results: list[ConnectorResult]) -> str:
    """'Updated 6:02 AM' footer plus an error line per failed connector."""
    parts = []
    stamps = [r.last_updated for r in results if r.last_updated and not r.error]
    if stamps:
        parts.append(f'<p class="updated">Updated {escape(_time(max(stamps)))}</p>')
    for r in results:
        if r.error:
            note = f" Showing last good result from {_stamp(r.last_updated)}." if r.stale else ""
            parts.append(f'<p class="error">{escape(r.name)} failed to load: {escape(r.error)}.{escape(note)}</p>')
    return "".join(parts)


def _stamp(iso: str | None) -> str:
    """'Wed Sep 24, 6:00 AM' for stale data, which may be from another day."""
    if not iso:
        return "unknown time"
    return datetime.fromisoformat(iso).strftime("%a %b %-d, %-I:%M %p")


def _card(card_id: str, title: str, body: str, results: list[ConnectorResult], extra_class: str = "") -> str:
    cls = f"card {extra_class}".strip()
    return (
        f'<section class="{cls}" id="{card_id}">'
        f"<h2>{escape(title)}</h2>{body}{_status(results)}</section>"
    )


def _hints(item: Item) -> str:
    return ", ".join(h.replace("_", " ") for h in item.urgency_hints)


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

    events = sorted(cal.items, key=lambda i: i.timestamp or "")
    personal_events = [e for e in events if e.section == "personal"]
    now = datetime.fromisoformat(brief["generated_at"])

    # 1. Header
    w = weather.items[0] if weather.items else None
    weather_line = f"{w.title}. {w.summary}" if w else "Weather unavailable."
    timed = [e for e in events if not _all_day(e.timestamp)]
    first = (timed or events or [None])[0]
    first_line = f"First up: {_time(first.timestamp)} {first.title}" if first else "No events today."
    header = (
        f'<header class="card header" id="header">'
        f'<p class="date">{escape(now.strftime("%A, %B %-d"))}</p>'
        f'<h1>Good morning, {escape(config.get("owner_name", ""))}.</h1>'
        f'<p>{escape(weather_line)}</p><p>{escape(first_line)}</p>'
        f"{_status([weather, cal])}</header>"
    )

    # 2. Pressing actions
    actions = [
        f'<li><label><input type="checkbox" disabled> <span class="title">{escape(a.title)}</span></label>'
        f'<span class="why">{escape(_label(a))} · {escape(_hints(a))}</span></li>'
        for a in brief["actions"]
    ]
    actions_body = (
        '<p class="note">Placeholder ranking. LLM ranking arrives in M5.</p>'
        + (f'<ol class="actions">{"".join(actions)}</ol>' if actions else '<p class="empty">Nothing pressing.</p>')
    )
    actions_card = _card("actions", "Pressing actions", actions_body, list(r.values()))

    # 3. Today: calendar + chores side by side
    cal_card = _card(
        "today-calendar", "Calendar",
        _list([_item(e, f"{_time(e.timestamp)} · {_label(e)}") for e in personal_events], "No events."),
        [cal], "sub",
    )
    chore_items = [c for c in chores.items if c.section == "personal"]
    chore_card = _card(
        "today-chores", "Chores",
        _list([_item(c, _hints(c) or _due(c.due)) for c in chore_items],
              "No chores due."),
        [chores], "sub",
    )
    today = f'<section class="card today" id="today"><h2>Today</h2><div class="split">{cal_card}{chore_card}</div></section>'

    # 4. Inbox (personal inboxes only): pressing school, work and internship email
    personal_mail = [m for m in email.items if m.section == "personal"]
    pressing = [m for m in personal_mail if {"reply_needed", "deadline"} & set(m.urgency_hints)]
    rest = [m for m in personal_mail if m not in pressing]
    inbox_body = _list(
        [_item(m, " · ".join(filter(None, [_label(m), _hints(m), _due(m.due)]))) for m in pressing],
        "Nothing pressing.",
    )
    if rest:
        inbox_body += (
            f"<details><summary>Everything else ({len(rest)})</summary>"
            f"{_list([_item(m, _label(m)) for m in rest])}</details>"
        )
    inbox = _card("inbox", "Inbox", inbox_body, [email])

    # 5. QofAI (kept separate from personal items). Its email lives in Slack, so no inbox here.
    q_events = [e for e in events if e.section == "qofai" and not e.due]
    q_due = [i for res in r.values() for i in res.items if i.section == "qofai" and i.due]
    qofai_body = (
        "<h3>Meetings</h3>" + _list([_item(e, _time(e.timestamp)) for e in q_events], "No work meetings.")
        + "<h3>Deadlines</h3>" + _list([_item(i, _due(i.due)) for i in q_due], "No work deadlines.")
    )
    qofai = _card("qofai", "QofAI", qofai_body, [cal], "work")

    # 6. Job search
    job_card = _card(
        "job-search", "Job search",
        _list([_item(j, _due(j.due)) for j in jobs.items], "No changes since yesterday."),
        [jobs],
    )

    # 7. Reading
    nyt_items = nyt.items[: config["news"]["cap"]]
    aib_items = aib.items[: config["ai_daily_brief"]["cap"]]
    reading_body = (
        "<h3>NYT</h3>" + _list([_item(n) for n in nyt_items], "No stories.")
        + "<h3>AI Daily Brief</h3>" + _list([_item(a) for a in aib_items], "No items.")
    )
    reading = _card("reading", "Reading", reading_body, [nyt, aib])

    body = header + actions_card + today + inbox + qofai + job_card + reading
    return Template(TEMPLATE.read_text()).substitute(
        title="Life Dashboard",
        body=body,
        generated_at=escape(_time(brief["generated_at"])),
    )


def write_page(html: str, path: Path | None = None) -> Path:
    path = path or ROOT / "web" / "index.html"
    path.write_text(html)
    return path
