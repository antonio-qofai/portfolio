"""Give the emails a readable HTML half, without rewriting how they are built.

The owner asked on 2026-09-20 for emails that are easier to read than "size 12
Arial". They were plain text and nothing else, because `notify.send` called
`msg.set_content(body)` and stopped there.

The obvious fix is to rebuild `agent/notify.py` to emit structure and render
that structure twice. That is the right long-term shape and it was not done
today, deliberately: `build_digest`, `build_roundup` and `build_urgent` are
hundreds of lines of carefully ordered judgement with 46 tests against them, and
rewriting all three to change a font is how a working digest acquires a bug.

So this reads the plain text the existing builders already produce and renders
it. That is only safe because the text is not prose, it is a strict indentation
grammar the builders have followed since Milestone 7, and `parse` is written
against that grammar with a fallback for every line it does not recognise. The
worst case for an unrecognised line is that it renders as a plain paragraph,
which is what it looks like in the text email anyway.

Two consequences worth knowing before editing a builder:

  - A builder that changes its indentation changes this file's output silently.
    `tools/test_digest.py` has cases pinning the shapes this parser depends on.
  - The text half is still the source of truth and still sent. Every client
    shows one or the other, never both, and a reader that prefers text loses
    nothing. Never drop the plain part to save effort here.

Everything is inlined. Email clients strip <style> blocks unpredictably, refuse
web fonts, and have no theme tokens, so none of the dashboard's CSS approach
survives the trip; this file repeats its palette by hand as literals on purpose.
"""

from __future__ import annotations

import html
import re

# The dashboard's palette, as literals. A colour here cannot be a token, and it
# cannot be imported from the page either, because that page's tokens only mean
# anything inside a browser that honours them.
INK = "#16201F"
INK_2 = "#4A5A57"
INK_3 = "#7C8C88"
LINE = "#DDE5E3"
ACCENT = "#15655C"
ACCENT_SOFT = "#EAF3F1"
URGENT = "#8C5309"
GROUND = "#F4F6F5"
PANEL = "#FFFFFF"

FONT = (
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
)
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"

URL_RE = re.compile(r"https?://\S+")


def _e(text: str) -> str:
    return html.escape(text, quote=True)


def parse(body: str) -> list[tuple[str, str]]:
    """Turn a built email body into (kind, text) pairs.

    The grammar, as the Milestone 7 builders emit it:

        SECTION HEADING          column 0, upper case
        prose                    column 0, mixed case
          Group heading (12)     two spaces
            - Company: Title     four spaces then a dash
              detail             six spaces
              you said: ...      six spaces, a quote from his own label
              https://...        six spaces, bare URL
            and 8 more...        four spaces, no dash

    Anything else becomes a paragraph. That fallback is the whole reason this
    is safe to run over text a builder may change without warning.
    """
    out: list[tuple[str, str]] = []
    for raw in body.splitlines():
        line = raw.rstrip()
        if not line.strip():
            out.append(("blank", ""))
            continue
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())

        if indent == 0:
            letters = [c for c in stripped if c.isalpha()]
            if letters and all(c.isupper() for c in letters) and len(stripped) > 2:
                out.append(("section", stripped))
            else:
                out.append(("para", stripped))
        elif indent >= 6:
            if stripped.startswith("http"):
                out.append(("url", stripped))
            elif stripped.lower().startswith("you said:"):
                out.append(("quote", stripped[len("you said:"):].strip()))
            else:
                out.append(("meta", stripped))
        elif stripped.startswith("- "):
            out.append(("item", stripped[2:].strip()))
        elif indent == 2:
            out.append(("group", stripped))
        else:
            out.append(("note", stripped))
    return out


def _linkify(text: str) -> str:
    """Escape, then turn bare URLs into anchors. Escaping first is the point."""
    escaped = _e(text)
    return URL_RE.sub(
        lambda m: f'<a href="{m.group(0)}" style="color:{ACCENT};">{m.group(0)}</a>',
        escaped,
    )


def render(body: str, subject: str = "") -> str:
    """A complete HTML document for one email body."""
    blocks = parse(body)
    parts: list[str] = []
    open_item = False

    def close_item():
        nonlocal open_item
        if open_item:
            parts.append("</div>")
            open_item = False

    for kind, text in blocks:
        if kind == "blank":
            continue
        if kind == "section":
            close_item()
            parts.append(
                f'<h2 style="font:600 12px/1.3 {MONO};letter-spacing:.12em;'
                f"text-transform:uppercase;color:{INK_3};margin:30px 0 10px;"
                f'padding-bottom:7px;border-bottom:2px solid {INK};">{_e(text)}</h2>'
            )
        elif kind == "group":
            close_item()
            parts.append(
                f'<h3 style="font:600 15px/1.35 {FONT};color:{INK};'
                f'margin:18px 0 8px;">{_e(text)}</h3>'
            )
        elif kind == "item":
            close_item()
            company, _, title = text.partition(": ")
            parts.append(
                f'<div style="margin:0 0 4px;padding:11px 13px;background:{PANEL};'
                f'border:1px solid {LINE};border-left:3px solid {ACCENT};border-radius:3px;">'
                f'<div style="font:600 15px/1.35 {FONT};color:{INK};">{_e(company)}</div>'
                f'<div style="font:400 14px/1.4 {FONT};color:{INK};">{_e(title)}</div>'
            )
            open_item = True
        elif kind == "meta":
            parts.append(
                f'<div style="font:400 12.5px/1.45 {FONT};color:{INK_3};'
                f'margin-top:4px;">{_linkify(text)}</div>'
            )
        elif kind == "quote":
            parts.append(
                f'<div style="font:400 13px/1.45 {FONT};color:{ACCENT};'
                f"background:{ACCENT_SOFT};padding:6px 9px;border-radius:2px;"
                f'margin-top:6px;">You said: {_e(text)}</div>'
            )
        elif kind == "url":
            parts.append(
                f'<div style="margin-top:6px;font:500 12.5px/1.4 {MONO};'
                f'word-break:break-all;"><a href="{_e(text)}" '
                f'style="color:{ACCENT};">{_e(text)}</a></div>'
            )
        elif kind == "note":
            close_item()
            parts.append(
                f'<div style="font:400 13px/1.5 {FONT};color:{INK_3};'
                f'margin:8px 0 0;font-style:italic;">{_linkify(text)}</div>'
            )
        else:
            close_item()
            parts.append(
                f'<p style="font:400 14.5px/1.6 {FONT};color:{INK_2};'
                f'margin:10px 0;max-width:62ch;">{_linkify(text)}</p>'
            )
    close_item()

    heading = (
        f'<div style="font:600 19px/1.3 {FONT};color:{INK};margin:0 0 4px;">'
        f"{_e(subject)}</div>"
        if subject
        else ""
    )

    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(subject or 'Internship watcher')}</title></head>
<body style="margin:0;padding:0;background:{GROUND};">
<div style="max-width:660px;margin:0 auto;padding:26px 18px 40px;">
{heading}
{''.join(parts)}
</div>
</body></html>"""
