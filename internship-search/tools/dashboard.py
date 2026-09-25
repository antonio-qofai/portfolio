"""Render the search dashboard to a standalone HTML file.

    .venv/bin/python -m tools.dashboard              # writes build/dashboard.html
    .venv/bin/python -m tools.dashboard --open       # and opens it
    .venv/bin/python -m tools.dashboard --json       # the data, for debugging

`agent/dashboard.py` decides what goes on the page; this decides how it reads.

The output is body content only, with no doctype, <html>, <head> or <body>
wrapper, because the Artifact publisher supplies those. That also makes the file
openable straight off disk in any browser, which is the local fallback when the
page has not been republished lately.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
import webbrowser
from pathlib import Path

from agent import config, dashboard, db

OUT = Path(config.ROOT) / "build" / "dashboard.html"

STATUS_LABELS = {
    "applied": "Applied",
    "interviewing": "Interviewing",
    "rejected": "Rejected",
    "offer": "Offer",
    "skipped": "Skipped",
    "missed": "Missed",
    "not_applied": "Not applied",
}


def e(value) -> str:
    return html.escape(str(value if value is not None else ""))


def _chip(text: str, kind: str = "") -> str:
    return f'<span class="chip {kind}">{e(text)}</span>'


def _do_next(items: list[dict]) -> str:
    if not items:
        return (
            '<p class="empty">Nothing is scoring above zero right now. That '
            "usually means every open window is far off and nothing carries a "
            "stated deadline, not that there is nothing to do.</p>"
        )
    top = max(i["score"] for i in items) or 1
    rows = []
    for n, item in enumerate(items, 1):
        width = max(4, round(100 * item["score"] / top))
        chips = "".join(
            _chip(r, "urgent" if ("shuts" in r or "closes" in r) else
                  ("yes" if "interested" in r else ""))
            for r in item["reasons"]
        )
        url = item.get("url") or ""
        link = (
            f'<a class="go" href="{e(url)}" target="_blank" rel="noopener">Open posting</a>'
            if url else '<span class="go muted">No link</span>'
        )
        rows.append(f"""
      <li class="lead" data-hash="{e(item.get('hash'))}"
          data-company="{e(item.get('company'))}" data-title="{e(item.get('title'))}">
        <div class="rank">{n}</div>
        <div class="lead-main">
          <div class="lead-head">
            <span class="co">{e(item.get('company'))}</span>
            <span class="ti">{e(item.get('title'))}</span>
          </div>
          <div class="loc">{e(item.get('location') or 'Location not stated')}</div>
          <div class="chips">{chips}</div>
        </div>
        <div class="lead-side">
          <div class="meter" title="Leverage {item['score']}">
            <span style="width:{width}%"></span>
          </div>
          <div class="score">{item['score']:.0f}</div>
          <div class="acts">{link}<button class="log" type="button">Log it</button></div>
        </div>
      </li>""")
    return f'<ol class="leads">{"".join(rows)}</ol>'


def _windows(items: list[dict]) -> str:
    if not items:
        return '<p class="empty">No application window is open today.</p>'
    cells = []
    for w in items:
        days = w["days_left"]
        kind = "critical" if days <= 14 else ("warn" if days <= 30 else "")
        note = e(w.get("action") or w.get("label") or "")
        cells.append(f"""
      <div class="win {kind}">
        <div class="win-co">{e(w['company'])}</div>
        <div class="win-days"><b>{days}</b><span>days left</span></div>
        <div class="win-note">{note}</div>
      </div>""")
    return f'<div class="wins">{"".join(cells)}</div>'


def _pipeline(items: list[dict]) -> str:
    if not items:
        return """
    <div class="empty-state">
      <p><b>No applications recorded.</b> The agent believes you have applied
      nowhere, so it cannot tell you how your search is going or learn what you
      say yes to.</p>
      <p>Log one against any role above, or add one by hand for somewhere the
      agent never saw. Everything you log here is read back into the database.</p>
      <button class="add" type="button" id="add-manual">Add an application</button>
    </div>"""
    rows = []
    for item in items:
        status = item.get("applied_status") or "applied"
        days = item.get("days_since")
        silent = " silent" if item.get("silent") else ""
        when = f"{days}d ago" if days is not None else "—"
        rows.append(f"""
      <tr>
        <td class="c">{e(item.get('company'))}</td>
        <td>{e(item.get('title'))}</td>
        <td><span class="pill {e(status)}">{e(STATUS_LABELS.get(status, status))}</span></td>
        <td class="num{silent}">{e(when)}</td>
      </tr>""")
    return f"""
    <table class="grid">
      <thead><tr><th>Company</th><th>Role</th><th>Status</th><th>Since</th></tr></thead>
      <tbody>{"".join(rows)}</tbody>
    </table>
    <button class="add" type="button" id="add-manual">Add an application</button>"""


def _upcoming(items: list[dict]) -> str:
    if not items:
        return """
    <p class="empty">Nothing scheduled. When you have an interview or an
    assessment, log it against the role and the date shows up here.</p>"""
    rows = []
    for item in items:
        days = item.get("days_until")
        when = "Today" if days == 0 else ("Tomorrow" if days == 1 else f"in {days} days")
        kind = "critical" if (days is not None and days <= 2) else ""
        rows.append(f"""
      <li class="ev {kind}">
        <div class="ev-when"><b>{e(item.get('next_event_at'))}</b><span>{e(when)}</span></div>
        <div class="ev-what">
          <b>{e(item.get('company'))}</b> {e(item.get('title'))}
          <div class="note">{e(item.get('next_event_note') or 'No note')}</div>
        </div>
      </li>""")
    return f'<ul class="events">{"".join(rows)}</ul>'


def _outlook(o: dict) -> str:
    rate = o["response_rate"]
    if o["applied"] == 0:
        rate_text, rate_note = "—", "no applications recorded yet"
    else:
        rate_text = f"{round(100 * rate)}%"
        rate_note = f"{o['responded']} of {o['applied']} got any reply"

    def tile(value, label, note="", kind=""):
        return f"""
      <div class="tile {kind}">
        <div class="tv">{e(value)}</div>
        <div class="tl">{e(label)}</div>
        <div class="tn">{e(note)}</div>
      </div>"""

    return f"""
    <div class="tiles">
      {tile(f"{o['surfaced']:,}", "worth a look", f"from {o['tracked']:,} tracked")}
      {tile(f"{o['scored']:,}", "ranked so far", "the rest are unread")}
      {tile(o['tier1_waiting'], "tier 1 waiting", "top band, not applied", "good")}
      {tile(o['interested_waiting'], "you said yes to", "and have not applied", "warn")}
      {tile(o['applied'], "applications", "everywhere you have applied")}
      {tile(rate_text, "response rate", rate_note)}
    </div>"""


def render(data: dict) -> str:
    o = data["outlook"]
    if o["applied"] == 0:
        headline = (
            "Nothing is logged as applied yet, so the pipeline below is empty and "
            "the agent cannot tell you how the search is going."
        )
    elif o["interviewing"]:
        headline = f"{o['interviewing']} live in interviews, {o['waiting']} waiting on a reply."
    else:
        headline = (
            f"{o['applied']} applications in, {o['waiting']} still waiting on a reply."
        )

    payload = json.dumps(
        {
            "leads": [
                {"hash": i.get("hash"), "company": i.get("company"),
                 "title": i.get("title"), "url": i.get("url")}
                for i in data["do_next"]
            ]
        }
    )

    return f"""<title>Search Command</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>
  /* Light is the base set. Every token is declared here before any media or
     [data-theme] block redefines it, so the un-stamped "system" state always
     resolves a full palette. Neutrals carry a slight teal bias rather than
     being pure grey, matching the instrument accent. */
  :root {{
    --ground:#F4F6F5; --panel:#FFFFFF; --sunk:#EDF1F0;
    --line:#D8E0DE; --line-soft:#E6ECEA;
    --ink:#16201F; --ink-2:#4A5A57; --ink-3:#778783;
    --accent:#1C6F66; --accent-soft:#D9EAE7; --accent-ink:#0F4A44;
    --urgent:#9A5B0C; --urgent-soft:#FBEBD5;
    --critical:#95352A; --critical-soft:#FADFDA;
    --good:#2F6B39; --good-soft:#DDEEDF;
    --shadow:0 1px 2px rgba(20,40,38,.07), 0 6px 18px rgba(20,40,38,.05);
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      --ground:#0F1817; --panel:#16211F; --sunk:#111A19;
      --line:#2A3A37; --line-soft:#1F2D2B;
      --ink:#E8EFED; --ink-2:#A3B4B0; --ink-3:#748682;
      --accent:#4FB5A6; --accent-soft:#16332F; --accent-ink:#8FD8CC;
      --urgent:#D9A25A; --urgent-soft:#332612;
      --critical:#E08A7C; --critical-soft:#3A1D18;
      --good:#79BE85; --good-soft:#18301C;
      --shadow:0 1px 2px rgba(0,0,0,.4), 0 8px 24px rgba(0,0,0,.3);
    }}
  }}
  :root[data-theme="dark"] {{
    --ground:#0F1817; --panel:#16211F; --sunk:#111A19;
    --line:#2A3A37; --line-soft:#1F2D2B;
    --ink:#E8EFED; --ink-2:#A3B4B0; --ink-3:#748682;
    --accent:#4FB5A6; --accent-soft:#16332F; --accent-ink:#8FD8CC;
    --urgent:#D9A25A; --urgent-soft:#332612;
    --critical:#E08A7C; --critical-soft:#3A1D18;
    --good:#79BE85; --good-soft:#18301C;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 8px 24px rgba(0,0,0,.3);
  }}

  * {{ box-sizing:border-box; }}
  body {{
    background:var(--ground); color:var(--ink);
    font:400 15px/1.55 "IBM Plex Sans", system-ui, -apple-system, sans-serif;
    margin:0; padding:0 20px 72px;
    -webkit-font-smoothing:antialiased;
  }}
  .wrap {{ max-width:1080px; margin:0 auto; }}

  /* ---- masthead ---- */
  header {{ padding:34px 0 22px; border-bottom:2px solid var(--ink); margin-bottom:28px; }}
  h1 {{
    font:600 34px/1.05 Archivo, system-ui, sans-serif;
    letter-spacing:-.022em; margin:0 0 8px; text-wrap:balance;
  }}
  .lede {{ color:var(--ink-2); max-width:62ch; margin:0 0 14px; }}
  .stamp {{
    font:500 11px/1 "IBM Plex Mono", ui-monospace, monospace;
    letter-spacing:.09em; text-transform:uppercase; color:var(--ink-3);
  }}

  /* ---- sections ---- */
  section {{ margin:0 0 38px; }}
  h2 {{
    font:600 12px/1 "IBM Plex Mono", ui-monospace, monospace;
    letter-spacing:.14em; text-transform:uppercase; color:var(--ink-3);
    margin:0 0 4px; display:flex; align-items:baseline; gap:10px;
  }}
  h2 em {{ font-style:normal; color:var(--accent); }}
  .sub {{ color:var(--ink-2); font-size:13.5px; margin:0 0 16px; max-width:64ch; }}

  /* ---- closing windows: a countdown strip, not cards ---- */
  .wins {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:1px;
           background:var(--line); border:1px solid var(--line); border-radius:3px;
           overflow:hidden; }}
  .win {{ background:var(--panel); padding:14px 16px; display:flex; flex-direction:column; gap:3px;
          border-left:3px solid transparent; }}
  .win.warn {{ border-left-color:var(--urgent); }}
  .win.critical {{ border-left-color:var(--critical); background:var(--critical-soft); }}
  .win-co {{ font:600 15px/1.2 Archivo, sans-serif; }}
  .win-days {{ display:flex; align-items:baseline; gap:6px;
               font-family:"IBM Plex Mono", monospace; font-variant-numeric:tabular-nums; }}
  .win-days b {{ font-size:26px; font-weight:600; line-height:1; }}
  .win.critical .win-days b {{ color:var(--critical); }}
  .win.warn .win-days b {{ color:var(--urgent); }}
  .win-days span {{ font-size:11px; color:var(--ink-3); text-transform:uppercase; letter-spacing:.07em; }}
  .win-note {{ font-size:12.5px; color:var(--ink-2); }}

  /* ---- the leverage list, the point of the page ---- */
  .leads {{ list-style:none; margin:0; padding:0; border-top:1px solid var(--line); }}
  .lead {{
    display:grid; grid-template-columns:38px 1fr 176px; gap:16px; align-items:start;
    padding:15px 4px; border-bottom:1px solid var(--line-soft);
  }}
  .lead:hover {{ background:var(--panel); }}
  .rank {{
    font:500 13px/1 "IBM Plex Mono", monospace; color:var(--ink-3);
    padding-top:4px; font-variant-numeric:tabular-nums;
  }}
  .lead-head {{ display:flex; flex-wrap:wrap; align-items:baseline; gap:8px; }}
  .co {{ font:600 15px/1.25 Archivo, sans-serif; }}
  .ti {{ font-size:14.5px; color:var(--ink); }}
  .loc {{ font-size:12.5px; color:var(--ink-3); margin-top:2px; }}
  .chips {{ display:flex; flex-wrap:wrap; gap:5px; margin-top:8px; }}
  .chip {{
    font:500 11px/1 "IBM Plex Mono", monospace; letter-spacing:.02em;
    padding:4px 7px; border-radius:2px; background:var(--sunk); color:var(--ink-2);
    border:1px solid var(--line-soft);
  }}
  .chip.urgent {{ background:var(--urgent-soft); color:var(--urgent); border-color:transparent; }}
  .chip.yes {{ background:var(--accent-soft); color:var(--accent-ink); border-color:transparent; }}
  .lead-side {{ display:flex; flex-direction:column; gap:6px; align-items:stretch; }}
  .meter {{ height:4px; background:var(--sunk); border-radius:2px; overflow:hidden; }}
  .meter span {{ display:block; height:100%; background:var(--accent); }}
  .score {{
    font:600 12px/1 "IBM Plex Mono", monospace; color:var(--ink-3);
    font-variant-numeric:tabular-nums; text-align:right;
  }}
  .acts {{ display:flex; gap:6px; margin-top:2px; }}
  .go, .log, .add {{
    font:500 12px/1 "IBM Plex Sans", sans-serif; padding:7px 10px; border-radius:3px;
    border:1px solid var(--line); background:var(--panel); color:var(--ink);
    cursor:pointer; text-decoration:none; text-align:center; flex:1;
  }}
  .go:hover, .log:hover, .add:hover {{ border-color:var(--accent); color:var(--accent-ink); }}
  .go.muted {{ color:var(--ink-3); cursor:default; }}
  .log[data-done="1"] {{ background:var(--accent-soft); color:var(--accent-ink); border-color:transparent; }}
  .add {{ flex:0 0 auto; margin-top:14px; padding:9px 14px; }}
  :focus-visible {{ outline:2px solid var(--accent); outline-offset:2px; }}

  /* ---- pipeline ---- */
  .table-scroll {{ overflow-x:auto; }}
  .grid {{ width:100%; border-collapse:collapse; font-size:14px; }}
  .grid th {{
    text-align:left; font:600 11px/1 "IBM Plex Mono", monospace; letter-spacing:.08em;
    text-transform:uppercase; color:var(--ink-3); padding:0 12px 9px 0;
    border-bottom:1px solid var(--line);
  }}
  .grid td {{ padding:11px 12px 11px 0; border-bottom:1px solid var(--line-soft); vertical-align:top; }}
  .grid td.c {{ font-weight:600; }}
  .grid td.num {{ font-family:"IBM Plex Mono", monospace; font-variant-numeric:tabular-nums;
                  color:var(--ink-3); white-space:nowrap; }}
  .grid td.num.silent {{ color:var(--critical); font-weight:600; }}
  .pill {{
    display:inline-block; font:500 11px/1 "IBM Plex Mono", monospace;
    padding:4px 8px; border-radius:2px; background:var(--sunk); color:var(--ink-2);
  }}
  .pill.applied {{ background:var(--accent-soft); color:var(--accent-ink); }}
  .pill.interviewing {{ background:var(--good-soft); color:var(--good); }}
  .pill.offer {{ background:var(--good); color:var(--panel); }}
  .pill.rejected {{ background:var(--critical-soft); color:var(--critical); }}

  /* ---- upcoming ---- */
  .events {{ list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:1px;
             background:var(--line); border:1px solid var(--line); border-radius:3px; overflow:hidden; }}
  .ev {{ background:var(--panel); padding:13px 16px; display:grid;
         grid-template-columns:120px 1fr; gap:16px; border-left:3px solid transparent; }}
  .ev.critical {{ border-left-color:var(--critical); }}
  .ev-when {{ display:flex; flex-direction:column; font-family:"IBM Plex Mono", monospace; }}
  .ev-when b {{ font-size:14px; font-variant-numeric:tabular-nums; }}
  .ev-when span {{ font-size:11px; color:var(--ink-3); text-transform:uppercase; letter-spacing:.07em; }}
  .note {{ font-size:12.5px; color:var(--ink-2); margin-top:2px; }}

  /* ---- outlook tiles ---- */
  .tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:1px;
            background:var(--line); border:1px solid var(--line); border-radius:3px; overflow:hidden; }}
  .tile {{ background:var(--panel); padding:16px; }}
  .tv {{ font:600 27px/1 "IBM Plex Mono", monospace; font-variant-numeric:tabular-nums;
         letter-spacing:-.02em; }}
  .tile.good .tv {{ color:var(--good); }}
  .tile.warn .tv {{ color:var(--urgent); }}
  .tl {{ font-size:13px; font-weight:500; margin-top:5px; }}
  .tn {{ font-size:12px; color:var(--ink-3); margin-top:1px; }}

  .empty {{ color:var(--ink-2); font-size:14px; max-width:62ch; }}
  .empty-state {{
    border:1px dashed var(--line); border-radius:4px; padding:20px; background:var(--panel);
  }}
  .empty-state p {{ margin:0 0 10px; max-width:64ch; color:var(--ink-2); font-size:14px; }}
  .empty-state b {{ color:var(--ink); }}

  /* ---- logging dialog ---- */
  dialog {{
    border:1px solid var(--line); border-radius:5px; background:var(--panel); color:var(--ink);
    padding:0; width:min(440px, calc(100vw - 32px)); box-shadow:var(--shadow);
  }}
  dialog::backdrop {{ background:rgba(10,20,19,.5); }}
  .dlg {{ padding:20px; display:flex; flex-direction:column; gap:13px; }}
  .dlg h3 {{ font:600 17px/1.25 Archivo, sans-serif; margin:0; }}
  .dlg label {{ font:500 11px/1 "IBM Plex Mono", monospace; letter-spacing:.08em;
                text-transform:uppercase; color:var(--ink-3); display:block; margin-bottom:5px; }}
  .dlg input, .dlg select {{
    width:100%; padding:8px 10px; font:400 14px "IBM Plex Sans", sans-serif;
    border:1px solid var(--line); border-radius:3px; background:var(--ground); color:var(--ink);
  }}
  .dlg-acts {{ display:flex; gap:8px; justify-content:flex-end; margin-top:4px; }}
  .dlg-acts button {{ padding:9px 15px; flex:0 0 auto; }}
  .primary {{ background:var(--accent); color:#fff; border-color:transparent; }}
  .primary:hover {{ opacity:.9; color:#fff; }}
  #saved {{ font-size:12.5px; color:var(--good); min-height:16px; }}

  footer {{ border-top:1px solid var(--line); padding-top:16px; color:var(--ink-3); font-size:12.5px; }}
  footer code {{ font-family:"IBM Plex Mono", monospace; color:var(--ink-2); }}

  @media (max-width:720px) {{
    .lead {{ grid-template-columns:28px 1fr; }}
    .lead-side {{ grid-column:2; flex-direction:row; align-items:center; flex-wrap:wrap; }}
    .meter {{ flex:1 1 90px; }}
    .ev {{ grid-template-columns:1fr; gap:6px; }}
  }}
  @media (prefers-reduced-motion: reduce) {{ * {{ transition:none !important; }} }}
</style>

<div class="wrap">
  <header>
    <h1>Search Command</h1>
    <p class="lede">{e(headline)}</p>
    <div class="stamp">Built {e(data['generated_at'][:16].replace('T', ' '))} &middot; Summer 2027 cycle</div>
  </header>

  <section>
    <h2>Do next <em>highest leverage first</em></h2>
    <p class="sub">Ranked by urgency, not by fit. A closing window or a stated
    deadline outweighs a good score with no date on it, and every row shows the
    reasons that put it there.</p>
    {_do_next(data['do_next'])}
  </section>

  <section>
    <h2>Closing windows</h2>
    <p class="sub">Company-wide application windows, from
    <code>sources/cycle_windows.toml</code>. These shut whether or not a posting
    is still listed.</p>
    {_windows(data['windows'])}
  </section>

  <section>
    <h2>Upcoming</h2>
    <p class="sub">Interviews, assessments and anything else with a date.</p>
    {_upcoming(data['upcoming'])}
  </section>

  <section>
    <h2>Pipeline <em>everywhere you have applied</em></h2>
    <p class="sub">Anything silent for three weeks or more is marked in red.</p>
    <div class="table-scroll">{_pipeline(data['pipeline'])}</div>
  </section>

  <section>
    <h2>How it is going</h2>
    <p class="sub">A rejection counts as a response. Applications still waiting
    are not counted as failures, because they have not failed yet.</p>
    {_outlook(data['outlook'])}
  </section>

  <footer>
    <p>Built from <code>state.db</code> by <code>tools.dashboard</code>. Anything
    you log here is stored with the page and pulled into the database on the next
    session; it does not reach SQLite on its own.</p>
    <div id="saved"></div>
  </footer>
</div>

<dialog id="dlg">
  <form method="dialog" class="dlg">
    <h3 id="dlg-title">Log an application</h3>
    <div>
      <label for="f-company">Company</label>
      <input id="f-company" name="company" required>
    </div>
    <div>
      <label for="f-role">Role</label>
      <input id="f-role" name="title">
    </div>
    <div>
      <label for="f-status">Status</label>
      <select id="f-status" name="status">
        <option value="applied">Applied</option>
        <option value="interviewing">Interviewing</option>
        <option value="rejected">Rejected</option>
        <option value="offer">Offer</option>
      </select>
    </div>
    <div>
      <label for="f-date">Next interview or assessment, if any</label>
      <input id="f-date" name="event_at" type="date">
    </div>
    <div>
      <label for="f-note">Note</label>
      <input id="f-note" name="note" placeholder="Phone screen, take-home, who you spoke to">
    </div>
    <div class="dlg-acts">
      <button value="cancel" type="submit">Cancel</button>
      <button value="save" type="submit" class="primary" id="f-save">Save</button>
    </div>
  </form>
</dialog>

<script>
(function () {{
  const DATA = {payload};
  const dlg = document.getElementById("dlg");
  const saved = document.getElementById("saved");
  const form = dlg.querySelector("form");
  let store = null;
  let pending = null;

  // The page works with no database behind it: every control below either
  // writes or says plainly that it cannot. `claude.use` resolving null is the
  // ordinary offline case, not an error.
  if (window.claude && typeof window.claude.use === "function") {{
    window.claude.use("db").then(function (db) {{ store = db; }}).catch(function () {{}});
  }}

  function openFor(entry) {{
    pending = entry || {{}};
    document.getElementById("dlg-title").textContent =
      entry && entry.company ? "Log " + entry.company : "Add an application";
    form.company.value = (entry && entry.company) || "";
    form.title.value = (entry && entry.title) || "";
    form.note.value = "";
    form.event_at.value = "";
    dlg.showModal();
  }}

  document.querySelectorAll(".lead .log").forEach(function (btn) {{
    btn.addEventListener("click", function () {{
      const li = btn.closest(".lead");
      openFor({{
        hash: li.dataset.hash,
        company: li.dataset.company,
        title: li.dataset.title,
      }});
    }});
  }});

  const manual = document.getElementById("add-manual");
  if (manual) manual.addEventListener("click", function () {{ openFor(null); }});

  form.addEventListener("submit", function (ev) {{
    if (form.returnValue === "cancel") return;
    const btn = ev.submitter;
    if (btn && btn.value === "cancel") return;
    const record = {{
      hash: (pending && pending.hash) || "",
      company: form.company.value.trim(),
      title: form.title.value.trim(),
      status: form.status.value,
      event_at: form.event_at.value || "",
      note: form.note.value.trim(),
      logged_at: new Date().toISOString(),
    }};
    if (!record.company) return;
    const id = (record.hash || record.company.toLowerCase().replace(/[^a-z0-9]+/g, "-"))
      .slice(0, 180) + "-" + Date.now().toString(36);
    if (!store) {{
      saved.textContent =
        "Saved nowhere: this copy of the page has no store behind it. Open the "
        + "published version to log applications.";
      saved.style.color = "var(--critical)";
      return;
    }}
    store.doc("applications/" + id).set(record).then(function () {{
      saved.textContent = "Logged " + record.company + ". It reaches the database on the next session.";
      saved.style.color = "var(--good)";
      document.querySelectorAll(".lead").forEach(function (li) {{
        if (li.dataset.hash && li.dataset.hash === record.hash) {{
          const b = li.querySelector(".log");
          b.textContent = "Logged";
          b.dataset.done = "1";
        }}
      }});
    }}).catch(function (err) {{
      saved.textContent = "Could not save: " + (err && err.code ? err.code : "unknown error");
      saved.style.color = "var(--critical)";
    }});
  }});
}})();
</script>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(OUT), help="where to write the page")
    ap.add_argument("--open", action="store_true", help="open it in a browser after writing")
    ap.add_argument("--json", action="store_true", help="print the data instead of the page")
    args = ap.parse_args()

    conn = db.connect()
    data = dashboard.collect(conn)

    if args.json:
        print(json.dumps(data, indent=2, default=str))
        return 0

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(data), encoding="utf-8")

    o = data["outlook"]
    print(f"Wrote {out}")
    print(f"  {len(data['do_next'])} lead(s), {len(data['pipeline'])} application(s), "
          f"{len(data['upcoming'])} upcoming, {len(data['windows'])} open window(s)")
    if o["applied"] == 0:
        print("  No applications recorded, so the pipeline panel is empty.")
    if args.open:
        webbrowser.open(out.resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
