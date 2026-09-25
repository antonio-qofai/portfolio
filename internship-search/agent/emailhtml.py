"""Give the emails a readable HTML half, without rewriting how they are built.

The owner asked on 2026-09-20 for emails that are easier to read than "size 12
Arial". They were plain text and nothing else, because `notify.send` called
`msg.set_content(body)` and stopped there.

The obvious fix is to rebuild `agent/notify.py` to emit structure and render
that structure twice. That is the right long-term shape and it was not done,
deliberately: `build_digest`, `build_roundup` and `build_urgent` are hundreds of
lines of carefully ordered judgement with many tests against them, and
rewriting all three to change a font is how a working digest acquires a bug.

So this reads the plain text the existing builders already produce and renders
it. That is only safe because the text is not prose, it is a strict indentation
grammar the builders have followed since Milestone 7. Redesigned 2026-09-25 to
read that grammar as a tree rather than line by line, because the tier listing
nests one level deeper than the YOUR MOVE block and the flat reader rendered
every tier 1 role as a run of grey italic notes with a raw URL under it.

How it reads the text:

  - Every non-blank line becomes a node, and the lines indented under it become
    its children. Blank lines only separate.
  - A column-0 upper case line opens a section, and the section's name decides
    how its contents look: cards for roles, a compact list for YOUR MOVE,
    small and muted for HELD BACK and the closed list.
  - "---" at column 0 opens the footer. Everything after it is small and muted.
  - A line that matches none of this becomes a paragraph, which is what it looks
    like in the text email anyway.

Two consequences worth knowing before editing a builder:

  - A builder that changes its indentation changes this file's output silently.
    `tools/test_digest.py` has cases that fail if a line of the text is missing
    from the HTML, and cases pinning the shapes this reader depends on.
  - The text half is still the source of truth and still sent. Every client
    shows one or the other, never both. Never drop the plain part to save
    effort here.

`render` never raises. If the structured renderer fails on text it did not
expect, it falls back to `render_plain`, which escapes the body line by line
and cannot fail on any string. `notify.send` also catches, as a second layer,
and sends plain text alone.

Everything is inlined and laid out in tables. Email clients strip <style>
blocks unpredictably, refuse web fonts and ignore most modern layout, so the
palette is repeated by hand as literals on purpose. The page declares itself
light only: Apple Mail then leaves it alone, and the Gmail app's own darkening
still has explicit backgrounds on every surface to work from.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

# One accent and a ladder of neutrals. Nothing else carries colour.
INK = "#1B2422"
INK_2 = "#48524F"
INK_3 = "#7A8480"
LINE = "#E3E7E5"
ACCENT = "#1D6B5F"
ACCENT_SOFT = "#EAF3F0"
GROUND = "#F3F4F2"
PANEL = "#FFFFFF"

FONT = (
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, "
    "sans-serif"
)
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"

URL_RE = re.compile(r"https?://\S+")
FLAGS_RE = re.compile(r"\s+\[([A-Z0-9 ,]+)\]$")
SCORE_RE = re.compile(r"^tier (\d+), (fit .*)$")
TAIL_URL_RE = re.compile(r"^(.*?):?\s+(https?://\S+)$")


def _e(text: str) -> str:
    return html.escape(text, quote=True)


# ------------------------------------------------------------------ parsing

@dataclass
class Node:
    text: str
    indent: int
    children: list["Node"] = field(default_factory=list)


def tree(body: str) -> list[Node]:
    """Every non-blank line as a node, nested under the nearest shallower line."""
    roots: list[Node] = []
    stack: list[Node] = []
    for raw in body.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        node = Node(line.strip(), len(line) - len(line.lstrip()))
        while stack and stack[-1].indent >= node.indent:
            stack.pop()
        (stack[-1].children if stack else roots).append(node)
        stack.append(node)
    return roots


def is_heading(text: str) -> bool:
    # A trailing count is part of a heading, and it can carry lowercase words:
    # "TIER 1 (1 roles, 3 listings)". Judge the words before it.
    text = re.sub(r"\s*\([^()]*\)$", "", text)
    letters = [c for c in text if c.isalpha()]
    return len(text) > 2 and bool(letters) and all(c.isupper() for c in letters)


def split_item(text: str) -> tuple[str, list[str], list[str]]:
    """'Title (A; B)  [FLAG, FLAG]' into the title, its locations and its flags.

    Locations are the LAST balanced parenthesis group, because titles carry
    parentheses of their own ("Mechanical Engineer Intern (Summer 2027)"). A
    line with no trailing group comes back whole, with no locations.
    """
    flags: list[str] = []
    m = FLAGS_RE.search(text)
    if m:
        flags = [f.strip() for f in m.group(1).split(",") if f.strip()]
        text = text[: m.start()]
    text = text.rstrip()
    if not text.endswith(")"):
        return text, [], flags
    depth = 0
    for i in range(len(text) - 1, -1, -1):
        if text[i] == ")":
            depth += 1
        elif text[i] == "(":
            depth -= 1
            if depth == 0:
                if i == 0 or text[i - 1] != " ":
                    return text, [], flags
                where = text[i + 1 : -1]
                return text[:i].rstrip(), [w.strip() for w in where.split(";") if w.strip()], flags
    return text, [], flags


def split_company(text: str) -> tuple[str, str]:
    company, sep, title = text.partition(": ")
    return (company, title) if sep else ("", text)


# ---------------------------------------------------------------- fragments

def _linkify(text: str) -> str:
    """Escape, then turn bare URLs into anchors. Escaping first is the point."""
    return URL_RE.sub(
        lambda m: (f'<a href="{m.group(0)}" style="color:{ACCENT};'
                   f'word-break:break-all;">{m.group(0)}</a>'),
        _e(text),
    )


def _text_or_link(text: str, size: str = "14px", colour: str = INK_2) -> str:
    """'Work the list: https://...' reads as a link called 'Work the list'."""
    m = TAIL_URL_RE.match(text)
    if m and m.group(1).strip():
        return (f'<a href="{_e(m.group(2))}" style="color:{ACCENT};'
                f'font-weight:600;text-decoration:none;">{_e(m.group(1).strip())} '
                f"&rarr;</a>")
    return f'<span style="color:{colour};">{_linkify(text)}</span>'


def _p(inner: str, size: str = "14px", colour: str = INK_2, margin: str = "0 0 10px",
       extra: str = "") -> str:
    return (f'<p style="margin:{margin};font:400 {size}/1.55 {FONT};color:{colour};'
            f'{extra}">{inner}</p>')


def _pill(text: str, strong: bool = False) -> str:
    if strong:
        style = f"background:{ACCENT};color:#FFFFFF;border:1px solid {ACCENT};"
    else:
        style = f"background:{PANEL};color:{INK_2};border:1px solid {LINE};"
    return (f'<span style="display:inline-block;{style}border-radius:999px;'
            f"padding:2px 9px;margin:0 4px 4px 0;font:600 11px/1.5 {FONT};"
            f'letter-spacing:.02em;white-space:nowrap;">{_e(text)}</span>')


def _button(url: str, label: str = "View posting") -> str:
    return (f'<a href="{_e(url)}" style="display:inline-block;background:{ACCENT};'
            f"color:#FFFFFF;text-decoration:none;border-radius:6px;"
            f'padding:8px 14px;font:600 13px/1.2 {FONT};">{_e(label)} &rarr;</a>')


def _card(inner: str, accent: bool = True) -> str:
    edge = ACCENT if accent else LINE
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="border-collapse:separate;margin:0 0 12px;background:{PANEL};'
            f"border:1px solid {LINE};border-left:3px solid {edge};"
            f'border-radius:8px;"><tr><td style="padding:14px 16px;">{inner}'
            f"</td></tr></table>")


def _section_heading(text: str, muted: bool = False) -> str:
    colour = INK_3 if muted else INK
    size = "11px" if muted else "12px"
    return (f'<h2 style="margin:28px 0 12px;font:700 {size}/1.3 {FONT};'
            f"letter-spacing:.1em;text-transform:uppercase;color:{colour};"
            f'padding-bottom:6px;border-bottom:1px solid {LINE};">{_e(text)}</h2>')


def _meta_join(parts: list[str]) -> str:
    # Each fact stays on one line, so a date never breaks as "2026-08-" / "07".
    return f' <span style="color:{INK_3};">&middot;</span> '.join(
        f'<span style="white-space:nowrap;">{p}</span>' if len(p) <= 40 else p
        for p in parts if p
    )


def _split_meta(text: str) -> list[str]:
    """The builders separate facts on one line with two spaces. HTML collapses
    that to one, which ran the location straight into 'open since'."""
    return [_e(p.strip()) for p in re.split(r"\s{2,}", text) if p.strip()]


# ------------------------------------------------------------------ renders

def _role_card(node: Node, company: str = "") -> str:
    """One role in a tier or urgent listing, with its details as children."""
    text = node.text[2:].strip() if node.text.startswith("- ") else node.text
    title, locations, flags = split_item(text)
    if not company:
        company, title = split_company(title)

    url = ""
    pills: list[str] = []
    reason: list[str] = []
    notes: list[str] = []
    for child in _flatten(node.children):
        t = child.text
        score = SCORE_RE.match(t)
        if URL_RE.fullmatch(t):
            url = t
        elif score:
            pills.append(_pill(f"Tier {score.group(1)}", strong=True))
            for part in score.group(2).split(", "):
                pills.append(_pill(part))
        elif t == "not yet scored":
            pills.append(_pill("Not yet scored"))
        elif t.startswith("REFERRAL:"):
            notes.append(f'<span style="color:{ACCENT};font-weight:600;">'
                         f"{_e(t)}</span>")
        elif t.startswith("deadline:"):
            notes.append(f'<span style="color:{INK};font-weight:600;">'
                         f"{_e(t.capitalize())}</span>")
        elif t.startswith("one role,"):
            notes.append(f'<span style="color:{INK_3};">{_e(t)}</span>')
        else:
            reason.append(t)

    parts = []
    if company:
        parts.append(f'<div style="font:600 12px/1.4 {FONT};color:{INK_3};'
                     f'letter-spacing:.04em;text-transform:uppercase;">{_e(company)}</div>')
    parts.append(f'<div style="font:600 16px/1.35 {FONT};color:{INK};margin:2px 0 4px;">'
                 f"{_e(title)}</div>")
    if locations:
        parts.append(f'<div style="font:400 13px/1.45 {FONT};color:{INK_3};'
                     f'margin:0 0 8px;">{_e("; ".join(locations))}</div>')
    if pills or flags:
        parts.append('<div style="margin:0 0 6px;">' + "".join(pills)
                     + "".join(_pill(f) for f in flags) + "</div>")
    for r in reason:
        parts.append(_p(_linkify(r), colour=INK_2, margin="0 0 8px"))
    for n in notes:
        parts.append(f'<div style="font:400 13px/1.5 {FONT};margin:0 0 6px;">{n}</div>')
    if url:
        parts.append(f'<div style="margin-top:10px;">{_button(url)}</div>')
    return _card("".join(parts))


def _flatten(nodes: list[Node]) -> list[Node]:
    out: list[Node] = []
    for n in nodes:
        out.append(n)
        out.extend(_flatten(n.children))
    return out


def _compact_item(node: Node) -> str:
    """A role in the YOUR MOVE block or the closed-interested list: one linked
    title, a line of facts, and his own words when he wrote any."""
    text = node.text[2:].strip() if node.text.startswith("- ") else node.text
    company, title = split_company(text)
    url, facts, quote = "", [], ""
    for child in _flatten(node.children):
        t = child.text
        if URL_RE.fullmatch(t):
            url = t
        elif t.lower().startswith("you said:"):
            quote = t[len("you said:"):].strip()
        else:
            facts.extend(_split_meta(t))
    label = _e(title)
    if url:
        label = (f'<a href="{_e(url)}" style="color:{INK};text-decoration:none;'
                 f'border-bottom:1px solid {LINE};">{label}</a>')
    out = [f'<div style="font:400 14px/1.45 {FONT};color:{INK};">'
           + (f'<span style="font-weight:600;">{_e(company)}</span> '
              f'<span style="color:{INK_3};">&middot;</span> ' if company else "")
           + label + "</div>"]
    if facts:
        out.append(f'<div style="font:400 12.5px/1.5 {FONT};color:{INK_3};'
                   f'margin-top:2px;">{_meta_join(facts)}</div>')
    if quote:
        out.append(f'<div style="font:italic 400 13px/1.5 {FONT};color:{ACCENT};'
                   f'margin-top:3px;">&ldquo;{_e(quote)}&rdquo;</div>')
    return (f'<tr><td style="padding:9px 0;border-top:1px solid {LINE};">'
            + "".join(out) + "</td></tr>")


def _rows_table(rows: list[str]) -> str:
    return ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            'style="border-collapse:collapse;">' + "".join(rows) + "</table>")


def _your_move(children: list[Node]) -> str:
    """The standing to-do list, as one compact panel."""
    parts: list[str] = []
    silent: list[str] = []

    def flush_silent():
        if silent:
            parts.append(
                f'<div style="font:600 13px/1.4 {FONT};color:{INK};margin:16px 0 6px;">'
                "No reply yet</div>" + _rows_table(silent[:])
            )
            silent.clear()

    for node in children:
        t = node.text
        if t.startswith("No reply since "):
            when, _, rest = t[len("No reply since "):].partition(": ")
            company, _, title = rest.partition(", ")
            silent.append(
                f'<tr><td style="padding:6px 12px 6px 0;border-top:1px solid {LINE};'
                f'font:500 12px/1.4 {MONO};color:{INK_3};white-space:nowrap;'
                f'vertical-align:top;">{_e(when)}</td>'
                f'<td style="padding:6px 0;border-top:1px solid {LINE};'
                f'font:400 13.5px/1.4 {FONT};color:{INK};">'
                f'<span style="font-weight:600;">{_e(company)}</span>'
                + (f' <span style="color:{INK_3};">&middot;</span> {_e(title)}' if title else "")
                + "</td></tr>"
            )
            continue
        flush_silent()
        if t.startswith("OFFER"):
            company, title = split_company(t[len("OFFER"):].strip())
            url = next((c.text for c in _flatten(node.children)
                        if URL_RE.search(c.text)), "")
            url_m = URL_RE.search(url)
            parts.append(_card(
                f'<div style="font:700 11px/1.4 {FONT};color:{ACCENT};letter-spacing:.1em;">'
                f"OFFER, WAITING ON YOU</div>"
                f'<div style="font:600 15px/1.4 {FONT};color:{INK};margin:3px 0 8px;">'
                f"{_e(company)} &middot; {_e(title)}</div>"
                + (_button(url_m.group(0), "Open") if url_m else "")
            ))
        elif node.children and any(c.text.startswith("- ") for c in node.children):
            parts.append(f'<div style="font:600 13px/1.4 {FONT};color:{INK};'
                         f'margin:4px 0 4px;">{_e(t)}</div>')
            rows: list[str] = []
            tail: list[str] = []
            for c in node.children:
                if c.text.startswith("- "):
                    rows.append(_compact_item(c))
                else:
                    tail.append(c.text)
            parts.append(_rows_table(rows))
            for x in tail:
                parts.append(_p(_text_or_link(x, colour=INK_3), size="13px",
                                colour=INK_3, margin="8px 0 0"))
        elif t.startswith("This block repeats"):
            parts.append(_p(_e(t), size="11.5px", colour=INK_3, margin="14px 0 0"))
        else:
            parts.append(_p(_text_or_link(t), size="13px", colour=INK_2,
                            margin="12px 0 0"))
    flush_silent()
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="border-collapse:separate;background:{PANEL};border:1px solid {LINE};'
            f'border-radius:8px;"><tr><td style="padding:16px 18px;">'
            f'<div style="font:700 11px/1.3 {FONT};letter-spacing:.12em;color:{ACCENT};'
            f'margin:0 0 10px;">YOUR MOVE</div>'
            + "".join(parts) + "</td></tr></table>")


def _muted_list(children: list[Node]) -> str:
    out = []
    for node in _flatten(children):
        t = node.text[2:].strip() if node.text.startswith("- ") else node.text
        out.append(f'<div style="font:400 12px/1.55 {FONT};color:{INK_3};">'
                   f"{_linkify(t)}</div>")
    return "".join(out)


def _section_kind(heading: str) -> str:
    if heading == "YOUR MOVE":
        return "move"
    if heading.startswith("CLOSED, AND YOU MARKED"):
        return "owed"
    if heading.startswith(("TIER ", "NOT YET SCORED", "DEADLINE INSIDE", "OPEN ")):
        return "roles"
    if heading.startswith(("CLOSED", "HELD BACK")):
        return "muted"
    return "plain"


def _render_body(body: str) -> str:
    nodes = tree(body)
    out: list[str] = []
    kind = "plain"
    footer = False
    i = 0
    while i < len(nodes):
        node = nodes[i]
        t = node.text
        i += 1

        if node.indent == 0 and t == "---":
            footer = True
            out.append(f'<div style="border-top:1px solid {LINE};margin:30px 0 14px;"></div>')
            continue

        if footer:
            if node.indent == 0 and node.children:
                out.append(f'<div style="font:600 12px/1.5 {FONT};color:{INK_3};'
                           f'margin:10px 0 2px;">{_linkify(t)}</div>')
                out.append(_muted_list(node.children))
            elif TAIL_URL_RE.match(t):
                out.append(_p(_text_or_link(t, colour=INK_3), size="12.5px",
                              colour=INK_3, margin="10px 0 0"))
            else:
                out.append(_p(_linkify(t), size="12px", colour=INK_3, margin="0 0 6px"))
                out.append(_muted_list(node.children))
            continue

        if node.indent == 0 and is_heading(t):
            kind = _section_kind(t)
            if kind == "move":
                out.append('<div style="margin:18px 0 0;">'
                           + _your_move(node.children) + "</div>")
                continue
            out.append(_section_heading(t, muted=(kind == "muted")))
            if kind == "muted":
                out.append(_muted_list(node.children))
                continue
            _render_children(node.children, kind, out)
            continue

        # Column 0 but not a heading. In a role listing, a line with roles
        # indented under it is a company heading; anything else is prose.
        if node.indent == 0 and kind == "roles" and node.children and \
                any(c.text.startswith("- ") for c in node.children):
            for c in node.children:
                if c.text.startswith("- "):
                    out.append(_role_card(c, company=t))
                else:
                    out.append(_p(_linkify(c.text), size="13px", colour=INK_3))
            continue

        if kind == "muted":
            out.append(_muted_list([node]))
            continue
        out.append(_p(_text_or_link(t)))
        _render_children(node.children, kind, out)
    return "".join(out)


def _render_children(children: list[Node], kind: str, out: list[str]) -> None:
    rows: list[str] = []

    def flush():
        if rows:
            out.append(_rows_table(rows[:]))
            rows.clear()

    for c in children:
        if c.text.startswith("- ") and kind == "owed":
            rows.append(_compact_item(c))
        elif c.text.startswith("- ") and (kind == "roles" or c.children):
            flush()
            out.append(_role_card(c))
        elif c.text.startswith("- "):
            rows.append(f'<tr><td style="padding:3px 0;font:400 13.5px/1.5 {FONT};'
                        f'color:{INK_2};">&bull; {_linkify(c.text[2:])}</td></tr>')
        else:
            flush()
            out.append(_p(_text_or_link(c.text), size="13px", colour=INK_3))
            _render_children(c.children, kind, out)
    flush()


def _document(subject: str, inner: str) -> str:
    heading = (
        f'<div style="font:700 11px/1.3 {FONT};letter-spacing:.14em;color:{ACCENT};'
        f'margin:0 0 6px;">INTERNSHIP WATCHER</div>'
        f'<div style="font:700 21px/1.3 {FONT};color:{INK};margin:0 0 6px;">'
        f"{_e(subject)}</div>"
        if subject else ""
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<meta name="supported-color-schemes" content="light">
<title>{_e(subject or 'Internship watcher')}</title></head>
<body style="margin:0;padding:0;background:{GROUND};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{GROUND};">
<tr><td align="center" style="padding:24px 12px 40px;">
<table role="presentation" width="640" cellpadding="0" cellspacing="0" style="width:100%;max-width:640px;">
<tr><td style="font-family:{FONT};color:{INK};">
{heading}
{inner}
</td></tr></table>
</td></tr></table>
</body></html>"""


def render_plain(body: str, subject: str = "") -> str:
    """The fallback. Every line escaped and kept, spacing preserved, no parsing
    at all, so there is no string this can fail on."""
    lines = "".join(
        f'<div style="font:400 13px/1.5 {MONO};color:{INK};white-space:pre-wrap;">'
        f"{_linkify(line) or '&nbsp;'}</div>"
        for line in body.splitlines()
    )
    return _document(subject, lines)


def render(body: str, subject: str = "") -> str:
    """A complete HTML document for one email body. Never raises."""
    try:
        return _document(subject, _render_body(body))
    except Exception:  # noqa: BLE001 - a styling bug must never cost the email
        return render_plain(body, subject)
