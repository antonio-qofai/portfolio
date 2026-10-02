"""Renders one calendar window as a single self-contained HTML page.

Build order item 14 in PRD.md Section 5, the display half of the agent. It
reads a window through the schema in calendar_model/slots.py and writes one
file that opens from disk with no server, no build step, and no network
request. Everything it shows about threads, forms, lenses, and the approval
horizons is resolved from narrative/threads.yaml at render time.

What this page is for
---------------------
It is a conversation object, not a product surface. Jordan owns every
remaining narrative decision (which five to seven threads are the real
pillars, how many posts a week, what a good arc is, the month-end assembly
date), and he answers those better looking at a calendar than discussing one
in the abstract. So the page optimizes for a founder reading it once on a
call and being able to say "not that thread" or "that is too many posts".

It also settles PRD Section 3's open display question by demonstration. Pat
recommended a custom HTML page over the subscription product Jordan mentioned;
Jordan and Blake have not signed off. This is the page they are deciding about.

The ordering is not ours to claim
---------------------------------
The page states on its face, near the top and in prose a founder will read,
where its ordering came from, and that claim is generated rather than pasted
in. Until 2026-09-07 there was only one answer: the arrangement was
hand-constructed, because the module that decides ordering was build order
item 9 and it did not exist. Synthesis now writes real windows, so this module
defaults to the newest one in `state/` and renders the hand-built example only
when asked with `--example`. The disclaimer branches on which it got, because a
page that understates the build misleads exactly as much as one that overstates
it, and the caller passes that fact rather than this module guessing it from
the data.

What this module does not do
----------------------------
It decides nothing, scores nothing, detects no gaps, schedules nothing, and
writes nothing back. It renders a window it is handed. If this file ever
starts choosing dates, the wrong module is being edited.

Nothing editorial is in here
----------------------------
No thread id, no form id, no lens name and no date appears
in this file or in the markup it writes. Thread and lens labels come from the
taxonomy, and the range and its gaps come from `slots.window_days`. The
approval horizon is deliberately not consulted at all: see the note above
`_honesty_note` for why this page shows whether a post is approved and never
when it was due. The taxonomy is at 32
threads and consolidates to roughly five to seven right after the founder
call, so anything baking in today's ids breaks that week.

Titles are resolved, never copied. `narrative/corpus_map.py` reads the
sibling dependency folders live and is the documented resolution path for a
`<source-agent>/<item-id>` reference; this module asks it for titles and
falls back to the reference itself when the folders are not in the checkout.
No content text is read into the page.

Degradation follows this folder's convention, set by conference_reader.py: a
missing window file, an unreadable taxonomy, an unknown thread id, or a
window that fails validation each produce a warning on stderr and either a
page or an empty result, at exit 0, never a traceback.

Verified 2026-08-18: rendered the in-memory example window against the real
taxonomy (32 threads, 7 forms, horizons 14 and 30) and against a temporary
taxonomy carrying different horizons, and the two pages differ in the
division, the day tinting, and the findings. Also rendered a serialized
window read from disk, a window carrying an unknown thread id, a run with a
missing window file, and a run with a missing taxonomy. All exited 0.
"""
from __future__ import annotations

import argparse
import html
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_AGENT_ROOT = _HERE.parent

sys.path.insert(0, str(_AGENT_ROOT / "calendar_model"))

import slots  # noqa: E402
from slots import (  # noqa: E402
    APPROVAL_STATES,
    APPROVED,
    DRAFT,
    PUBLISHED,
    REJECTED,
    CalendarWindow,
    Taxonomy,
)

DEFAULT_OUT = _HERE / "calendar.html"
# Where the two content agents' folders are.
#
# Sibling folders in the monorepo, which is true on a laptop and is a deploy
# question anywhere else: a container built from this folder alone does not
# contain them, and then every card on the page falls back to a filename
# because the corpus resolves empty. The environment wins so a deployment can
# say where the checkout actually put them, rather than this being a code
# change. See README.md, "Hosting it on the portal".
DEFAULT_ATOMIZER = Path(
    os.environ.get("CALENDAR_ATOMIZER_PATH") or _AGENT_ROOT.parent / "content-atomizer" / "output"
)
DEFAULT_VCB = Path(
    os.environ.get("CALENDAR_VCB_PATH") or _AGENT_ROOT.parent / "value-creation-briefing" / "drafts"
)

# Weekday order for the grid. The Gregorian week, not an editorial choice.
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# One approval state, one semantic token. The literal colours live in the
# style sheet's `:root` and are redefined for the light theme, which is the
# only way a state colour can be legible in both. Before this the hexes were
# written here, which meant the page had exactly one theme and the portal's
# theme switch would have left approved posts unreadable.
#
# Keyed off the schema's own constants rather than off string literals, so a
# state renamed there does not leave a stale key here. Anything the schema
# does not know about falls through to the default and is still shown, since
# hiding an unknown state is worse than showing it plainly.
_STATE_TOKENS = {
    APPROVED: "ok",
    DRAFT: "warn",
    REJECTED: "err",
    # Muted on purpose: v1 does not publish, so a published post is history
    # rather than something anyone is being asked to look at.
    PUBLISHED: "done",
}
_DEFAULT_TOKEN = "unknown"

# The word shown for a state, where it differs from the stored name. The
# header has always said "declined", so the legend and the badge now do too;
# the store and every command still say `rejected`.
_STATE_LABELS = {REJECTED: "declined"}


def _state_label(state: str) -> str:
    return _STATE_LABELS.get(state, state)


def _warn(message: str) -> None:
    print(f"render_calendar: {message}", file=sys.stderr)


def _esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _rel(path) -> str:
    """A path as it reads in this repo, so the page is not full of one laptop."""
    try:
        return str(Path(path).resolve().relative_to(_AGENT_ROOT))
    except (ValueError, OSError):
        return str(path)


def _live_assembled_windows(lens: str, today):
    """Every window assembled for `lens` that has not fully passed by `today`.

    Two from the day next month is assembled until this month ends, and the
    page shows both. The rule itself lives in `slots.live_window_paths`, because the approval
    queue has to open the same calendar this page renders, and two modules
    answering "which window is current" separately is how a founder ends up
    approving one file while reading another. That is also why the lens is
    passed in rather than worked out here: with three calendars on disk,
    "which window" and "whose calendar" are one question, and the page is not
    the module that gets to answer it.
    """
    return slots.live_window_paths(lens=lens, today=today)


def _display_lens(taxonomy, requested: str | None) -> str | None:
    """Which calendar the page opens on.

    `--lens` wins. Otherwise the taxonomy decides, which keeps the list of
    live calendars in threads.yaml where every other module reads it from.
    """
    if requested:
        return str(requested)
    v1 = list(taxonomy.v1_lens_order)
    if not v1:
        _warn(f"{taxonomy.source_path} declares no v1 lens; pass --lens.")
        return None
    if len(v1) > 1:
        _warn(
            f"{taxonomy.source_path} declares several calendars ({', '.join(v1)}); "
            f"opening on {v1[0]}. Pass --lens to choose another."
        )
    return v1[0]


# ---------------------------------------------------------------------
# Title resolution, through the documented path rather than by reading files
# ---------------------------------------------------------------------

def resolve_corpus(
    atomizer_path: str | Path | None = None,
    vcb_path: str | Path | None = None,
) -> dict:
    """item_ref -> the live corpus item, read from the dependency agents.

    Goes through `narrative/corpus_map.py`, which is the resolution path the
    schema documents, so there is one place that knows how a reference maps
    to a piece of content. Returns an empty dict with a warning when the
    sibling folders are not in this checkout, in which case the page falls
    back to showing the reference, which is still true and still useful.

    Read live on every render rather than cached into the window, so the post
    on the page is the post its author last saved. The window holds a
    reference and never the content, which is what stops this agent becoming
    a second copy of somebody else's work.
    """
    # Read from the module at call time rather than bound as a default
    # argument. A default is evaluated once, when this file is imported, so
    # `DEFAULT_ATOMIZER` would stop being the answer the moment anything set
    # it afterwards: the environment override would work only because it is
    # read at import too, and would fail for any caller that set the path
    # late. One source of truth, asked each time.
    atomizer_path = atomizer_path or DEFAULT_ATOMIZER
    vcb_path = vcb_path or DEFAULT_VCB

    try:
        sys.path.insert(0, str(_AGENT_ROOT / "narrative"))
        import corpus_map  # noqa: F401
    except Exception as exc:  # pragma: no cover - reported, not raised
        _warn(f"could not load the corpus map ({exc}); showing references without titles.")
        return {}
    try:
        items = corpus_map.collect_items(Path(atomizer_path), Path(vcb_path))
    except Exception as exc:  # pragma: no cover - reported, not raised
        _warn(f"could not read the dependency folders ({exc}); showing references without titles.")
        return {}
    if not items:
        _warn(
            "the corpus map found no content; the sibling dependency folders are "
            "probably not in this checkout. Showing references without titles."
        )
    return {item.item_id: item for item in items}


def resolve_titles(
    atomizer_path: str | Path = DEFAULT_ATOMIZER,
    vcb_path: str | Path = DEFAULT_VCB,
) -> dict:
    """item_ref -> title, for a caller that wants only the names."""
    return {
        ref: item.title
        for ref, item in resolve_corpus(atomizer_path, vcb_path).items()
        if item.title
    }


def apply_stored_approvals(window: CalendarWindow, state_root: str | Path) -> CalendarWindow:
    """Overlays approval state recorded in the store onto the window.

    The window file says what is planned and the append-only store says what
    a human has since decided, and the store wins because it is the newer
    statement. `approval/queue.py` is what writes those decisions. Optional,
    and off unless asked for, so a checkout where nobody has decided anything
    yet does not print an empty-store warning on every render.
    """
    try:
        sys.path.insert(0, str(_AGENT_ROOT / "calendar_model"))
        import state_store
    except Exception as exc:  # pragma: no cover - reported, not raised
        _warn(f"could not load the state store ({exc}); rendering the window's own approvals.")
        return window
    recorded = state_store.latest_approvals(root=state_root)
    if not recorded:
        _warn(f"no approvals recorded under {state_root}; rendering the window's own state.")
        return window
    return slots.apply_approvals(window, recorded)


def published_links(state_root: str | Path) -> dict:
    """slot id -> the link recorded when it was marked published.

    Read from the same append-only store the approvals come from, newest
    decision per slot, so a post marked published and then taken back to
    draft carries no link. Empty when nothing has been marked published.
    """
    try:
        sys.path.insert(0, str(_AGENT_ROOT / "calendar_model"))
        import state_store
    except Exception as exc:  # pragma: no cover - reported, not raised
        _warn(f"could not load the state store ({exc}); showing no published links.")
        return {}
    return {
        slot_id: str(value.get("url") or "")
        for slot_id, value in state_store.latest_decisions(root=state_root).items()
        if isinstance(value, dict) and value.get("state") == PUBLISHED and value.get("url")
    }


# ---------------------------------------------------------------------
# Page assembly
# ---------------------------------------------------------------------

def _accent(state: str) -> str:
    """The semantic token name for an approval state."""
    return _STATE_TOKENS.get(state, _DEFAULT_TOKEN)


def _state_css() -> str:
    """CSS for the approval states the schema declares, generated from it."""
    rules = []
    for state in list(APPROVAL_STATES) + ["unknown"]:
        token = _accent(state)
        rules.append(
            f".state-{state}{{color:var(--{token});"
            f"background:var(--{token}-bg);border-color:var(--{token}-line)}}"
            f".pip-{state}{{background:var(--{token})}}"
            # The month tile's left edge and the legend's matching bar. Keyed
            # on `data-state` rather than a class, because the tests find a
            # month entry by its exact `class="mini"`.
            f'.mini[data-state="{state}"]{{border-left-color:var(--{token})}}'
            f'.swatch[data-state="{state}"],.seg[data-state="{state}"]{{background:var(--{token})}}'
        )
    return "".join(rules)


def _state_class(state: str) -> str:
    return f"state-{state}" if state in APPROVAL_STATES else "state-unknown"


def _pip_class(state: str) -> str:
    return f"pip-{state}" if state in APPROVAL_STATES else "pip-unknown"


def _long_date(day: str) -> str:
    value = slots._as_date(day)
    if value is None:
        return day
    return f"{_WEEKDAYS[value.weekday()]} {value.strftime('%d %B %Y').lstrip('0')}"


def _short_date(day: str) -> str:
    value = slots._as_date(day)
    if value is None:
        return day
    return value.strftime("%-d %b") if sys.platform != "win32" else value.strftime("%d %b")


def _short_ref(item_ref: str) -> str:
    return item_ref.split("/", 1)[1] if "/" in item_ref else item_ref


def _chip(label: str, title: str = "", extra: str = "") -> str:
    attrs = f' title="{_esc(title)}"' if title else ""
    return f'<span class="chip {extra}"{attrs}>{_esc(label)}</span>'


# The approval horizon came off this page on 2026-09-15, by owner decision.
#
# It was a deadline: a post needed its decision fourteen days before it ran,
# and the page tinted the days inside that window, drew a line where it ended,
# and flagged a slot that had passed it as overdue. Antonio's call was that a
# founder will not approve on a fourteen-day lead and should not be told off
# for it: "the calendar is meant to make life easier for them, not give them
# another deadline." A calendar that scolds a founder who is short on time is
# a calendar he stops opening.
#
# So a slot is approved or it is not, and the page shows which without ever
# saying when. On 2026-09-19 the rest of the agent followed: `required_approval`
# and the finding it raised are gone, and no module asks when a decision is due.
# `approved_horizon_days` stays in threads.yaml, read by nothing, so a deadline
# is cheap to bring back if one is ever wanted.

def _grid_months(days) -> str:
    """"October 2026", from the days the grid is actually showing.

    Added 2026-09-21, because the page never said which month it was. Antonio:
    "the calendar doesn't even say the month name and year." Read off the grid
    rather than off a window, so it cannot disagree with what is on screen, and
    it handles two months because a lens has two windows from the day next
    month is assembled until this one ends.
    """
    # `days` carries whatever the grid was built from, which is ISO strings
    # rather than dates, so they go through the same parser every other date
    # in this module does instead of being trusted.
    parsed = [d for d in (slots._as_date(d) for d in (days or [])) if d is not None]
    if not parsed:
        return ""
    first, last = parsed[0], parsed[-1]
    if (first.year, first.month) == (last.year, last.month):
        return first.strftime("%B %Y")
    if first.year == last.year:
        return f"{first.strftime('%B')} and {last.strftime('%B %Y')}"
    return f"{first.strftime('%B %Y')} and {last.strftime('%B %Y')}"


def _decided_summary(all_slots) -> str:
    """"2 approved", or nothing at all.

    What survives of the "Read this first" callout, which was deleted on
    2026-09-21 on Antonio's instruction ("totally remove it", and the page has
    "too much text, which makes the UI overwhelming"). It had been open as Q11
    since 2026-09-15.

    The counts are the one part of it a founder was ever going to act on, so
    they move into the header as three words. Everything else it said was the
    page explaining itself: that an agent chose the order, that placements
    carry reasons, that nothing publishes without approval. All three are
    visible on the cards themselves, which is where somebody reads them.

    Silent when nobody has decided anything, rather than saying "0 approved".
    An absence is not a number, and approval has not been something this
    calendar asks for since 2026-09-19.
    """
    # Published counted on its own since 2026-09-23, when a person could
    # first record one; folding it into "approved" undercounted what went out.
    approved = sum(1 for s in all_slots if s.approval == APPROVED)
    published = sum(1 for s in all_slots if s.approval == PUBLISHED)
    declined = sum(1 for s in all_slots if s.approval == REJECTED)
    parts = []
    if approved:
        parts.append(f"{approved} approved")
    if published:
        parts.append(f"{published} published")
    if declined:
        parts.append(f"{declined} declined")
    if len(parts) > 2:
        return ", ".join(parts[:-1]) + " and " + parts[-1]
    return " and ".join(parts)


def _glance(all_slots, taxonomy) -> str:
    """The month at a glance: where its posts stand, and whose they are.

    Added 2026-09-23. A thin bar split by state, and a count per person. It
    reports what has been decided and says nothing about what should be:
    no "to review", no percentage, no deadline, because the calendar exists
    to take work off a founder rather than to score one.
    """
    if not all_slots:
        return '<div class="glance" id="glance"></div>'
    counts = {}
    for slot in all_slots:
        counts[slot.approval] = counts.get(slot.approval, 0) + 1
    order = [state for state in (APPROVED, PUBLISHED, DRAFT, REJECTED) if counts.get(state)]
    order += [state for state in counts if state not in order]
    total = len(all_slots)
    bar = "".join(
        f'<span class="seg" data-state="{_esc(state)}" style="flex-grow:{counts[state]}" '
        f'title="{counts[state]} {_esc(_state_label(state))}"></span>'
        for state in order
    )
    people = {}
    for slot in all_slots:
        person = taxonomy.lens_person(slot.lens)
        people[person] = people.get(person, 0) + 1
    who = "".join(
        f'<span class="glanceperson"><span class="initial" aria-hidden="true">'
        f"{_esc(_initial(name))}</span>{_esc(name)} {count}</span>"
        for name, count in people.items()
    )
    return (
        f'<div class="glance" id="glance"><div class="glancebar" role="img" '
        f'aria-label="{total} posts by state">{bar}</div>'
        f'<div class="glancepeople">{who}</div></div>'
    )


def _example_notice(agent_decided: bool) -> str:
    """The one sentence kept from the callout, and only on the example page.

    Everything else in that section described a real calendar and is gone. This
    is not that: it fires only for `--example`, where no agent chose anything,
    and a page that looked identical to a real one would be the page passing a
    hand-built arrangement off as the agent's work. That is the failure this
    build has been stopped from committing three times.
    """
    if agent_decided:
        return ""
    return (
        '<section class="callout"><p>This is the hand-built example window. '
        "No agent chose these dates. Render without <code>--example</code> for a "
        "calendar the agent assembled.</p></section>"
    )

def _grid(taxonomy, today, days, slots_by_date, titles) -> str:
    if not days:
        return '<p class="muted">The window states no readable date range, so no grid is drawn.</p>'

    first = slots._as_date(days[0])
    last = slots._as_date(days[-1])
    in_range = set(days)
    today_iso = today.isoformat()

    lead = first.weekday()
    start_cell = first - timedelta(days=lead)
    total = (last - start_cell).days + 1
    total += (-total) % 7

    heads = "".join(f'<div class="gridhead">{d}</div>' for d in _WEEKDAYS)
    cells = []
    for index in range(total):
        day = start_cell + timedelta(days=index)
        iso = day.isoformat()
        if iso not in in_range:
            cells.append('<div class="cell out"></div>')
            continue
        classes = ["cell"]
        if day.weekday() >= 5:
            classes.append("weekend")
        if iso == today_iso:
            classes.append("is-today")
        here = slots_by_date.get(iso, [])
        if here:
            classes.append("has-slots")
        label = f"{day.day}"
        if day.day == 1 or iso == days[0]:
            label = f"{day.day} {day.strftime('%b')}"
        # data-slot carries the slot id so the reorder script can keep this
        # grid in step with the timeline below. A page that lets you move a
        # post and then shows the old order in the month view is worse than
        # one that does not let you move it at all.
        # The month entry is the thing you drag and the thing you click, and
        # it is now the only drag source on the page. Antonio, 2026-09-15:
        # "I wanted to drag on the calendar itself ... I don't want to start
        # the drag below." So the card in the list below is no longer
        # draggable at all, and this carries `draggable`, the lens it belongs
        # to, and the id its card answers to.
        #
        # Clicking it opens the post in the panel on the right (2026-09-22),
        # and `tabindex` lets the keyboard do the same with Enter.
        lines = "".join(
            f'<div class="mini" draggable="true" tabindex="0" data-slot="{_esc(s.slot_id)}" '
            f'data-lens="{_esc(s.lens)}" data-state="{_esc(s.approval)}" '
            f'title="{_esc(taxonomy.lens_person(s.lens))}, the '
            f'{_esc(taxonomy.lens_label(s.lens))} lens. Drag to another date, or click to '
            f'open it on the right.">'
            f'<span class="pip {_pip_class(s.approval)}"></span>'
            f'<span class="minibody">'
            f'<span class="lenstag"><span class="initial" aria-hidden="true">'
            f'{_esc(_initial(taxonomy.lens_person(s.lens)))}</span>'
            f'{_esc(taxonomy.lens_person(s.lens))}</span>'
            f'<span class="minitext">{_esc(titles.get(s.item_ref) or _short_ref(s.item_ref))}</span>'
            f"</span></div>"
            for s in sorted(here, key=lambda s: (s.lens, s.slot_id))
        )
        cells.append(
            f'<div class="{" ".join(classes)}" data-date="{_esc(iso)}">'
            f'<div class="daynum"><span>{_esc(label)}</span></div>{lines}</div>'
        )

    # No heading and no lede above the month. The calendar is the first thing
    # on the page and the largest thing on it, and a line of prose explaining
    # that it is a calendar is the kind of furniture this page was asked to
    # lose. The legend stays, because a coloured edge does need saying once
    # what it means, and it sits above the month now rather than under it.
    legend = "".join(
        f'<span class="legenditem"><span class="swatch" data-state="{_esc(state)}"></span>'
        f"{_esc(_state_label(state))}</span>"
        for state in APPROVAL_STATES
    )
    return f"""
<section>
  <p class="legend">{legend}</p>
  <div class="grid">{heads}{"".join(cells)}</div>
</section>
"""


def _offgrid_notice(days, slots_by_date, titles, taxonomy) -> str:
    """The posts the month cannot show, each one a button that opens it.

    Replaced a sentence on 2026-09-22 that sent the reader to "the end of the
    date order below", which is the list, and the list is no longer on screen.
    A post dated outside the stated range, or with no readable date, has no
    cell to sit in, so this is the only way to reach it. Says nothing when
    every post is on the grid, which is the normal case.
    """
    in_range = set(days or [])
    off = sorted(
        (s for d, here in slots_by_date.items() if d not in in_range for s in here),
        key=lambda s: (s.date, s.slot_id),
    )
    if not off:
        return '<div id="offgridslot"></div>'
    buttons = "".join(
        f'<button type="button" class="offgridopen" data-open-slot="{_esc(s.slot_id)}" '
        f'data-lens="{_esc(s.lens)}" title="{_esc(taxonomy.lens_person(s.lens))}, '
        f'{_esc(_long_date(s.date) if slots._as_date(s.date) else "no readable date")}">'
        f"{_esc(titles.get(s.item_ref) or _short_ref(s.item_ref))}</button>"
        for s in off
    )
    plural = "post sits" if len(off) == 1 else "posts sit"
    return (
        f'<div id="offgridslot"><section class="offgrid"><span>{len(off)} {plural} outside '
        f"this month's dates.</span> {buttons}</section></div>"
    )


# ---------------------------------------------------------------------
# The post, formatted
# ---------------------------------------------------------------------
#
# Added 2026-09-23. Until then the post was shown as the raw file, `#` and
# `**` and all, on the rule that it is "never edited, re-titled or reflowed".
# Antonio chose to render it instead: a heading marked `##` is a heading, and
# showing the marks was the page reading the file aloud rather than showing
# the post. The rule that survives is the one that mattered: not a word is
# added, removed or changed. Everything is escaped before any formatting is
# applied, so the only HTML on the page is the HTML written here, and a link
# is kept only when it is a web address.
#
# Written here rather than taken from a library because the page has to open
# from disk with nothing installed and no network request.

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_MD_RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_MD_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
_MD_NUMBER = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_MD_QUOTE = re.compile(r"^\s*>\s?(.*)$")
_MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_MD_BOLD = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
_MD_ITALIC = re.compile(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])")
_MD_CODE = re.compile(r"`([^`]+)`")


def _md_inline(text: str) -> str:
    out = _esc(text)
    out = _MD_CODE.sub(r"<code>\1</code>", out)
    out = _MD_LINK.sub(r'<a href="\2" rel="noreferrer noopener" target="_blank">\1</a>', out)
    out = _MD_BOLD.sub(r"<strong>\2</strong>", out)
    out = _MD_ITALIC.sub(r"<em>\1</em>", out)
    return out


def _render_post(body: str) -> str:
    """A post's markdown as HTML, with every word as the file has it."""
    blocks: list[str] = []
    para: list[str] = []
    items: list[str] = []
    kind = ""
    quote: list[str] = []

    def flush():
        nonlocal kind
        if para:
            blocks.append("<p>" + "<br>".join(_md_inline(line) for line in para) + "</p>")
            para.clear()
        if items:
            blocks.append(f"<{kind}>" + "".join(f"<li>{i}</li>" for i in items) + f"</{kind}>")
            items.clear()
            kind = ""
        if quote:
            blocks.append("<blockquote>" + "<br>".join(_md_inline(q) for q in quote) + "</blockquote>")
            quote.clear()

    for raw in (body or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            flush()
            continue
        heading = _MD_HEADING.match(line)
        if heading:
            flush()
            level = min(len(heading.group(1)), 3)
            blocks.append(f'<h{level + 2} class="md{level}">{_md_inline(heading.group(2))}</h{level + 2}>')
            continue
        if _MD_RULE.match(line):
            flush()
            blocks.append("<hr>")
            continue
        bullet, number = _MD_BULLET.match(line), _MD_NUMBER.match(line)
        if bullet or number:
            want = "ul" if bullet else "ol"
            if para or quote or (items and kind != want):
                flush()
            kind = want
            items.append(_md_inline((bullet or number).group(1)))
            continue
        quoted = _MD_QUOTE.match(line)
        if quoted:
            if para or items:
                flush()
            quote.append(quoted.group(1))
            continue
        if items or quote:
            flush()
        para.append(line.strip())
    flush()
    return "".join(blocks)


def _initial(name: str) -> str:
    """"J" for Jordan. A letter, never a colour: colour already means state."""
    name = (name or "").strip()
    return name[:1].upper() if name else "?"


def _engagement_by_slot(links, history) -> dict:
    """slot id -> the published record whose link matches the one recorded.

    The first join between the calendar and what went out (2026-09-23).
    `ingestion/performance.py` could never say which calendar post a LinkedIn
    post was, because nothing recorded a slot's link when it went out. Marking
    a post published records one, and this matches it on the activity id the
    same way performance joins engagement to posts. Empty until the portal
    token exists and the daily run has read something to match against.
    """
    if not links or history is None or not getattr(history, "posts", None):
        return {}
    try:
        sys.path.insert(0, str(_AGENT_ROOT / "ingestion"))
        from performance import normalise_url
    except Exception as exc:  # pragma: no cover - reported, not raised
        _warn(f"could not load the performance reader ({exc}); showing no engagement.")
        return {}
    by_url = {normalise_url(record.url): record for record in history.posts if record.url}
    found = {}
    for slot_id, url in links.items():
        record = by_url.get(normalise_url(url))
        if record is not None:
            found[slot_id] = record
    return found


def _slot_card(slot, taxonomy, today, titles, scheduled, corpus, links=None, drew=None) -> str:
    # "The standing rule wants this approved by now" came off on 2026-09-15
    # with the rest of the deadline. It fired on every draft inside the
    # fourteen-day horizon, which is to say on the posts nearest to running,
    # which is to say on the founder who had not got to them yet.
    #
    # A declined post still holding a date stays flagged, because that is a
    # state the calendar is in rather than a deadline anyone has missed: the
    # date is spoken for by a post nobody is going to publish, and no amount
    # of waiting fixes it.
    mismatch = ""
    if slot.approval == REJECTED:
        mismatch = '<p class="mismatch">This slot was declined and still holds a date.</p>'

    title = titles.get(slot.item_ref)
    heading = _esc(title) if title else _esc(_short_ref(slot.item_ref))

    thread_chips = "".join(
        _chip(
            taxonomy.thread_label(t),
            title=f"{t}" + ("" if not taxonomy.ok or not taxonomy.thread_ids or t in taxonomy.thread_ids
                            else " (not a thread in the taxonomy)"),
            extra="thread" + ("" if not taxonomy.ok or not taxonomy.thread_ids or t in taxonomy.thread_ids
                              else " unknown"),
        )
        for t in slot.threads
    ) or '<span class="muted">no thread</span>'

    # The form's plain name from the taxonomy, with its description as the
    # tooltip. The raw id meant nothing to a reader ("I'm not sure what the
    # form is", 2026-09-15), and falls back only when the taxonomy names none.
    form_chips = "".join(
        _chip(
            taxonomy.form_label(f),
            title=taxonomy.form_descriptions.get(f, ""),
            extra="form" + ("" if not taxonomy.ok or not taxonomy.form_ids or f in taxonomy.form_ids
                            else " unknown"),
        )
        for f in slot.forms
    ) or '<span class="muted">no form</span>'

    answers = ""
    if slot.responds_to:
        parts = []
        for ref in slot.responds_to:
            when = scheduled.get(ref)
            where = (
                f"scheduled here on {_esc(_short_date(when))}"
                if when
                else "not in this window, so it is published history or a later one"
            )
            parts.append(f"<li><code>{_esc(ref)}</code>, {where}</li>")
        answers = (
            '<div class="answers"><span class="fieldlabel">Answers</span>'
            f'<ul>{"".join(parts)}</ul></div>'
        )

    # What the post says, then why it is on this date, in that order and with
    # the second one folded away. Before 2026-09-15 the card carried only the
    # placement rationale, which told a reader why a post was on a Thursday
    # without telling them what the post was. The summary answers the question
    # someone actually has in front of a calendar; the rationale answers the
    # one they have when they disagree with it.
    summary = (
        f'<p class="summary">{_esc(slot.summary)}</p>'
        if slot.summary.strip()
        else '<p class="summary muted">No summary recorded for this post.</p>'
    )
    rationale = (
        f'<details class="rationalewrap"><summary>Why this date</summary>'
        f'<p class="rationale">{_esc(slot.rationale)}</p></details>'
        if slot.rationale.strip()
        else '<p class="rationale muted">No reason recorded for this date.</p>'
    )

    # The post itself, read live from the author's folder through the corpus
    # map. Never edited or re-titled here: what appears is what the file
    # holds, formatted since 2026-09-23 (see `_render_post`), which is the
    # same rule the window follows by carrying a reference rather than a copy.
    item = corpus.get(slot.item_ref)
    body = (item.body or "").strip() if item is not None else ""
    if body:
        words = getattr(item, "word_count", 0) or len(body.split())
        read = (
            f'<details class="post"><summary>Read the post ({words} words)</summary>'
            f'<div class="postbody">{_render_post(body)}</div>'
            f'<p class="postsource">Read from <code>{_esc(_rel(item.path))}</code> '
            "just now, so this is whatever its author last saved.</p></details>"
        )
    else:
        read = (
            '<p class="muted postmissing">The text of this post could not be read from the '
            "dependency agent's folder, so only the reference is shown.</p>"
        )

    # Every posting day carries one definite post, and the optionality lives in
    # being able to reorder rather than in a menu under each date. Jordan's
    # "here are the six options that you have to choose from" (founder call
    # 2026-08-19, 10:54) was resolved that way by the owner on 2026-09-07: a
    # calendar that asks a question on every date is not a calendar. So these
    # render as swap candidates, offered for when a date is wrong, rather than
    # as a shortlist awaiting a decision.
    alternatives = ""
    options = [a for a in (slot.extra.get("alternatives") or []) if isinstance(a, dict)]
    if options:
        rows = []
        for option in options:
            ref = str(option.get("item_ref") or "")
            if not ref:
                continue
            label = titles.get(ref) or _short_ref(ref)
            why = str(option.get("why") or "").strip()
            # Swap trades this post's date with the other post's (2026-09-23).
            # Every alternative the assembly offers is already scheduled on
            # another date of the same calendar, so a swap moves two posts and
            # adds or drops none. The script finds the other card by
            # `data-ref` and disables the button if it is not on the page.
            rows.append(
                f'<li><div class="alttext"><span class="altname">{_esc(label)}</span>'
                + (f'<span class="altwhy">{_esc(why)}</span>' if why else "")
                + f'</div><button type="button" class="swapbtn" data-swap="{_esc(ref)}" '
                f'title="Trade dates with this post">Swap</button></li>'
            )
        if rows:
            alternatives = (
                f'<details class="alts"><summary>Swap with another post ({len(rows)})</summary>'
                f"<ul>{''.join(rows)}</ul></details>"
            )

    # What can be done next depends on what has been decided. A draft offers
    # Approve and Decline. A decided post offers Undo, which records the
    # post back to draft (a newer row; nothing is overwritten), and an
    # approved one can be marked published with its link. Pressing Approve
    # on a post already approved used to do nothing visible at all.
    link = (links or {}).get(slot.slot_id, "")
    person = taxonomy.lens_person(slot.lens)
    if slot.approval == APPROVED:
        actions = (
            f'<button class="decide undo" data-decide="{DRAFT}" '
            'title="Take the approval back. The post returns to draft.">Undo approval</button>'
            f'<button class="decide publish" data-decide="{PUBLISHED}" '
            'title="Record that this post went out, with its link.">Mark published</button>'
        )
    elif slot.approval == REJECTED:
        actions = (
            f'<button class="decide undo" data-decide="{DRAFT}" '
            'title="Take the decline back. The post returns to draft.">Undo decline</button>'
        )
    elif slot.approval == PUBLISHED:
        record = (drew or {}).get(slot.slot_id)
        reach = ""
        if record is not None:
            count = getattr(record, "engagements", 0) or 0
            reach = (
                f'<span class="reach">{count} engagement{"" if count == 1 else "s"}</span>'
                if count else '<span class="reach">no engagement recorded yet</span>'
            )
        actions = (
            (f'<a class="postlink" href="{_esc(link)}" rel="noreferrer noopener" '
             'target="_blank">Open the published post</a>' if link else "")
            + reach
            + f'<button class="decide undo" data-decide="{DRAFT}" '
            'title="Recorded by mistake? This returns the post to draft.">Undo</button>'
        )
    else:
        actions = (
            f'<button class="decide" data-decide="{APPROVED}" '
            'title="Approve this post.">Approve</button>'
            f'<button class="decide" data-decide="{REJECTED}" '
            'title="Decline this post. A decline needs a reason.">Decline</button>'
        )

    # `data-title` is what the month grid shows for this post, so a swap can
    # update the grid without re-deriving the title in JavaScript.
    return f"""
<article class="card" id="slot-{_esc(slot.slot_id)}" data-lens="{_esc(slot.lens)}"
         data-slot="{_esc(slot.slot_id)}" data-ref="{_esc(slot.item_ref)}"
         data-title="{_esc(title or _short_ref(slot.item_ref))}" data-home="{_esc(slot.date)}"
         data-home-label="{_esc(_long_date(slot.date))}" data-state="{_esc(slot.approval)}">
  <div class="cardhead">
    <h3>{heading}</h3>
    <span class="badge {_state_class(slot.approval)}"
          title="What the approval store currently records for this slot">{_esc(_state_label(slot.approval))}</span>
  </div>
  <p class="ref"><span class="initial" aria-hidden="true">{_esc(_initial(person))}</span><span class="lenstag">{_esc(person)}</span></p>
  <div class="cardtools">
    <button class="move" data-dir="up"
            title="Move this post to the previous scheduled date. Dragging can reach any day; this is here for the keyboard.">&uarr;</button>
    <button class="move" data-dir="down"
            title="Move this post to the next scheduled date. Dragging can reach any day; this is here for the keyboard.">&darr;</button>
    <span class="toolgap"></span>
    <span class="pending" hidden></span>
    {actions}
  </div>
  <p class="moved" hidden>Moved from <span class="movedfrom"></span>. The reason below was
     written for that date.</p>
  <p class="declinerow" hidden>
    <label>Why <input class="notetext" type="text" maxlength="300"
           placeholder="what is wrong with this post on this date"></label>
    <span class="noteneeded">A decline needs a reason.</span>
  </p>
  <p class="publishrow" hidden>
    <label>Link <input class="linktext" type="url" maxlength="500"
           placeholder="https://www.linkedin.com/posts/..."></label>
    <span class="noteneeded">Paste the link to the post as it went out.</span>
  </p>
  <!-- Whose post it is, and nothing else. The item reference and the slot id
       were printed here until 2026-09-21 and came off with the rest of the
       overexplaining: a file path and an internal id are developer detail on a
       page a founder reads, and the served page decides with a button rather
       than with an id typed into a terminal. Both are still on the article as
       `data-ref` and `data-slot`, so nothing is lost for anyone inspecting the
       page, for the reorder script, or for a test. The person's name now sits
       under the title, above the controls. -->
  {mismatch}
  {summary}
  {read}
  {rationale}
  <div class="tags">
    <div><span class="fieldlabel" title="What this post argues. The recurring
         arguments the calendar sequences against, from narrative/threads.yaml."
         >Argues</span> {thread_chips}</div>
    <div><span class="fieldlabel" title="How this post argues it: the shape it uses to
         make its case, such as a historical analogy or a named case with the numbers
         attached. Not what it is about, which is the line above."
         >Shape</span> {form_chips}</div>
  </div>
  {answers}
  {alternatives}
</article>
"""


def _timeline(all_slots, taxonomy, today, days, slots_by_date, titles, corpus, links=None, drew=None) -> str:
    scheduled = {}
    for slot in all_slots:
        scheduled.setdefault(slot.item_ref, slot.date)

    dated = {d for d in slots_by_date if slots._as_date(d) is not None}
    walk = sorted(set(days) | dated)
    undated = [s for d in slots_by_date if d not in dated for s in slots_by_date[d]]
    in_range = set(days)
    rows = []

    # Every day in the window gets a row, including the ones with nothing on
    # them. It used to collapse runs of empty days into a single "3 days with
    # nothing scheduled" line, which read well and made those days impossible
    # to drop a post onto, because there was no element standing for any one
    # of them. Drag and drop needs every day to exist, so every day exists,
    # and an empty one is styled down to a single muted line instead of being
    # summarised away. The alternative was building the missing rows in
    # JavaScript on each drop, which would have put this page's markup in two
    # places and let them drift.
    for iso in walk:
        here = slots_by_date.get(iso, [])
        cards = "".join(
            _slot_card(s, taxonomy, today, titles, scheduled, corpus, links, drew)
            for s in sorted(here, key=lambda s: s.slot_id)
        )
        offset = ""
        value = slots._as_date(iso)
        if value is not None:
            delta = (value - today).days
            offset = f"{delta:+d} days" if delta else "today"
        many = f'<span class="daycount">{len(here)} posts</span>' if len(here) > 1 else ""
        outside = ""
        if days and iso not in in_range:
            outside = '<span class="offrange">outside the stated range</span>'
        rows.append(
            f"""
<div class="dayrow" data-date="{_esc(iso)}">
  <div class="daylabel"><div class="dayline">{_esc(_long_date(iso))}</div>
    <div class="muted">{_esc(offset)}</div>{many}{outside}</div>
  <div class="daycards">{cards}</div>
</div>
"""
        )
    if undated:
        cards = "".join(
            _slot_card(s, taxonomy, today, titles, scheduled, corpus, links, drew)
            for s in sorted(undated, key=lambda s: s.slot_id)
        )
        rows.append(
            f"""
<div class="dayrow">
  <div class="daylabel"><div class="dayline">No readable date</div>
    <span class="offrange">{len(undated)} slot(s)</span></div>
  <div class="daycards">{cards}</div>
</div>
"""
        )

    # Hidden since 2026-09-22. The panel on the right replaced this list as
    # the place a post is read and decided on, so the list came off the page.
    # It is still rendered, whole, because it is where the page keeps each
    # post's current date: drag, the arrows, Save order, Copy the order and
    # the panel all read a card's day row, and moving that job somewhere else
    # would have meant rewriting every one of them for no visible gain.
    return f'<section class="posts" id="posts" hidden>{"".join(rows)}</section>'


def _toolbar(windows, taxonomy, selected, all_slots, reviewer="") -> str:
    """The filter and every page-wide action, on one row above the month.

    Built 2026-09-22 from three pieces that used to be spread down the page:
    the lens filter on top, and the reorder and decision bars under the posts
    with a paragraph of instructions each. The instructions became tooltips.
    What stays in words is only what a page opened from disk has to admit:
    that it cannot save a move and cannot record a decision.

    Always drawn, including for a calendar of one post. The reorder bar was
    left out below two posts, and the script wiring every button on the page
    stops when it finds no reorder bar, so on a one-post calendar Approve did
    nothing at all. Now the bar is there and its buttons are hidden instead.
    """
    few = len([s for s in all_slots if slots._as_date(s.date) is not None]) < 2
    reorder = (
        f'<div class="reorderbar"{" hidden" if few else ""}>'
        '<span class="status"></span>'
        '<button data-act="save-order" class="save-order" disabled '
        'title="Drag a post to a new date in the month, then save.">Save order</button>'
        '<button data-act="reset" disabled title="Put every moved post back.">Reset</button>'
        '<span class="muted nosave-hint" hidden>Moving a post changes this page only.</span>'
        "</div>"
    )

    # The decision bar. A file opened from disk has nowhere to write and the
    # approval store is append-only, so there the buttons record nothing and
    # the bar says so in its own words rather than in a footnote. What it
    # produces is the exact `approval/queue.py` commands, which is the module
    # that owns every rule about what a decision has to carry. The page does
    # not get to decide what a valid approval looks like; it only collects one.
    decide = (
        '<div class="decidebar">'
        '<span class="status"></span>'
        # The field is the record of who is deciding and what every script
        # reads; it is hidden, and the chip beside it is what a reviewer sees.
        # Asked once, by the picker below, and remembered in this browser.
        f'<label class="whofield" hidden>Deciding as <input class="who" type="text" maxlength="60" '
        f'value="{_esc(reviewer)}" placeholder="your name"></label>'
        '<span class="whoami" hidden>Deciding as <strong class="whoname"></strong>'
        '<button type="button" class="whochange" title="Someone else is reviewing">change</button></span>'
        # These two belong to the page that cannot write. A served page
        # records on the click itself and hides them; see `_APP_SCRIPT`.
        '<button data-act="copy-decisions" class="staged" disabled>Copy the decisions</button>'
        '<button data-act="clear-decisions" class="staged" disabled>Clear</button>'
        '<span class="muted recordhint">Clicking Approve or Decline records nothing. '
        "Copy the commands out and run them to record.</span>"
        "</div>"
    )
    return (
        '<div class="toolbar">'
        f"{_lens_bar(windows, taxonomy, selected)}"
        f'<div class="actions">{decide}{reorder}</div>'
        "</div>"
        + _who_picker(taxonomy)
    )


def _who_picker(taxonomy) -> str:
    """"Who's reviewing?", asked once and then remembered in this browser.

    Added 2026-09-23. The name used to be a text field a reviewer had to fill
    before the first decision, and nothing on the page showed it had been
    remembered afterwards, so it read as a form to fill in on every visit.
    The founders are offered by name, from the taxonomy's lens people, so a
    new lens adds its person here without this module changing. It is not a
    sign-in: Q4 decided identity is recorded rather than verified, and a
    portal sign-in that passes `reviewer` skips this altogether.
    """
    people = []
    for lens in taxonomy.v1_lens_order:
        person = taxonomy.lens_person(lens)
        if person and person not in people:
            people.append(person)
    buttons = "".join(
        f'<button type="button" class="whopick" data-name="{_esc(name)}">'
        f'<span class="initial" aria-hidden="true">{_esc(_initial(name))}</span>{_esc(name)}</button>'
        for name in people
    )
    return (
        '<div class="whomodal" id="whomodal" hidden role="dialog" aria-modal="true" '
        'aria-labelledby="whotitle"><div class="whocard">'
        '<p class="kicker">Content calendar</p>'
        '<h2 id="whotitle">Who is reviewing?</h2>'
        f'<div class="whochoices">{buttons}</div>'
        '<form class="whoother"><input type="text" maxlength="60" placeholder="Someone else" '
        'aria-label="Your name"><button type="submit">Continue</button></form>'
        "</div></div>"
    )


def _findings(issues) -> str:
    """Validation errors only, and nothing at all when there are none.

    The Validation section came off the page on 2026-09-15. It listed every
    finding the schema returned, and the finding it listed most was
    `unapproved-inside-approved-horizon`, which fired on every draft slot
    inside the fourteen-day horizon. That is the normal state of a freshly
    assembled window, and presenting it as a validation finding taught a
    reader that a healthy calendar is full of problems. Antonio's words were
    "these aren't really errors", and he was right. That finding no longer
    exists at all: approval stopped being something the schema requires on
    2026-09-19, so there is nothing here to filter out any more.

    Warnings are not shown. Errors still are, because an error means the
    window is wrong, and a page that renders a broken calendar silently is
    the same failure as a page that overstates its own state, just pointing
    the other way. Both are still printed in full by `main`, which is where
    someone fixing one is looking.
    """
    errs = slots.errors(issues)
    if not errs:
        return ""
    rows = [
        f'<li class="finding err"><code>{_esc(issue.code)}</code> at '
        f'<code>{_esc(issue.where)}</code>'
        f'<div class="detail">{_esc(issue.detail)}</div></li>'
        for issue in errs
    ]
    return f"""
<section>
  <h2>{len(errs)} thing{"" if len(errs) == 1 else "s"} wrong with the calendars below</h2>
  <ul class="findings">{"".join(rows)}</ul>
</section>
"""


def _corpus_notice(all_slots, corpus, offered: bool) -> str:
    """Says so when the page is showing filenames instead of posts.

    The failure this exists for is a deployment one and it is silent. The
    corpus is read live on every render from the two content agents' folders,
    which are siblings in the monorepo. A container built from this folder
    alone does not contain them, `resolve_corpus` returns nothing, and every
    card falls back to its `item_ref`: a founder opens the calendar and sees
    sixteen filenames like `blog_blake_2026-06`, with the only explanation on
    stderr, in a log nobody reads. The calendar looks broken and the reason is
    a build setting.

    Four conditions, all of them necessary. A corpus must have been offered at
    all: a caller that passed none is rendering without titles deliberately,
    which is what `--no-titles` and the plain file render do, and telling them
    their deployment is broken would be a false alarm on the documented path.
    There must be slots, or there is nothing to have lost. The corpus must be
    empty rather than merely thin, because a partial read is a different
    problem and saying "the content is missing" over a page that has most of
    it would be worse than silence. And the slots must actually reference
    corpus items, since a window of posts that reference nothing is not
    missing anything.

    Same rule as the approved-horizon band and the retrospective: say nothing
    rather than explain an absence that is not there.
    """
    if not offered or corpus:
        return ""
    refs = [s.item_ref for s in (all_slots or []) if getattr(s, "item_ref", "")]
    if not refs:
        return ""
    return f"""
<section class="corpusgap">
  <h2>These are filenames, not posts</h2>
  <p>The {len(refs)} posts below are showing their file references because the
  content folders they come from are not in this deployment. The calendar
  itself is intact: every date, every decision and every rationale is real.
  What is missing is the title and body of each post, which this page reads
  live from the Content Atomizer and Value Creation Briefing folders rather
  than copying.</p>
  <p class="muted">Whoever deployed this can fix it by giving the service the
  whole repository, or by setting <code>CALENDAR_ATOMIZER_PATH</code> and
  <code>CALENDAR_VCB_PATH</code> to wherever the checkout put those two
  folders.</p>
</section>
"""


# What the Post history tab says before the portal inputs exist. Kept to what
# will appear, since the page must not hand a founder a task.
_HISTORY_PENDING = (
    "Nothing here yet. Published posts will appear here from the QofAI portal's "
    "Content Library and from Mark published on the calendar. The likes and "
    "comments each post drew will come from the portal's LinkedIn store, which "
    "the program's PhantomBuster post-engagement scrape fills every day."
)


def _detail_panel(reviewer: str = "") -> str:
    """The panel a post opens in, beside the month. Empty until one is clicked.

    Added 2026-09-22. Antonio: "This scrolling back up and down is not good."
    Reading a post and approving it meant leaving the calendar for the list
    below and coming back. Now a click in the month opens the post here, and
    it can be read and decided on with the month still on screen.

    Not the sidebar removed on 2026-09-15, which pinned the month itself beside
    the list and took its width all the time. This one exists only while a post
    is open, and closing it gives the month its width back.

    The panel holds a copy of the post's card, filled in by `_APP_SCRIPT`.
    The card in the list below stays the one the page believes: every button in
    here acts on that card through its slot id, so moving, staging and
    recording a decision each still have exactly one place they happen.
    """
    return (
        '<aside class="detail" id="detail" hidden aria-label="The post you opened">'
        '<div class="detailhead"><span class="detaildate"></span>'
        '<span class="detailpos"></span>'
        '<button class="detailstep" type="button" data-step="-1" '
        'title="Previous post (left arrow)" aria-label="Previous post">&lsaquo;</button>'
        '<button class="detailstep" type="button" data-step="1" '
        'title="Next post (right arrow)" aria-label="Next post">&rsaquo;</button>'
        '<button class="detailclose" type="button" title="Close (Esc)" '
        'aria-label="Close">&times;</button></div>'
        '<p class="detailstatus" aria-live="polite"></p>'
        '<div class="detailbody"></div>'
        "</aside>"
    )


def _view_tabs() -> str:
    """Calendar or Post history. Two views of one page, switched in place.

    Added 2026-09-22 so the retrospective has a place of its own before its
    data exists, rather than appearing under the posts only once it does.
    """
    return (
        '<div class="viewtabs" id="viewtabs" role="tablist">'
        '<button class="viewbtn" type="button" role="tab" data-view="calendar" '
        'aria-pressed="true">Calendar</button>'
        '<button class="viewbtn" type="button" role="tab" data-view="history" '
        'aria-pressed="false">Post history</button>'
        "</div>"
    )


def _performance(history) -> str:
    """What went out, and what it drew. A retrospective beside a plan.

    The calendar is forward-looking and this is the only thing on the page
    that looks back, which is why it sits under the posts rather than beside
    them. Its whole job is to stop the page implying that the agent knows how
    its own suggestions performed, which it does not and will not until
    something records the URL of a slot when it publishes.

    Two rules, both learned the hard way on this page.

    It says nothing rather than explaining an absence, unless the absence is
    the interesting part. An empty panel that reads "no data" teaches a reader
    to ignore the panel. So when there is no history this prints the one
    sentence saying which kind of nothing it is, and when there is no reason
    to print even that, it prints nothing at all.

    It never presents a partial count as a total. Engagement on a post nobody
    logged in the library cannot be attributed, and saying "42 reactions"
    while holding six it could not place would be the page overstating what it
    knows.
    """
    # Changed 2026-09-22. The section used to vanish when empty; it has its
    # own tab now, and a tab that opens on nothing reads as broken. So an
    # empty history gets one line saying what will land here, and nothing
    # about who has to do what to get it there. `None` is a page opened from
    # disk, which cannot read the store at all, and says the same thing.
    reason = history.why_empty() if history is not None else ""
    if history is None or not history.has_history:
        return f"""
<section class="performance">
  <h2>Post history</h2>
  <p class="muted">{_esc(reason or _HISTORY_PENDING)}</p>
</section>
"""

    rows = []
    for record in history.posts:
        drew = (
            f"{record.engagements} engagement{'' if record.engagements == 1 else 's'}"
            if record.engagements
            else "nothing recorded"
        )
        who = ""
        if record.engagers:
            shown = ", ".join(record.engagers[:3])
            more = len(record.engagers) - 3
            who = f'<div class="detail">{_esc(shown)}{f" and {more} more" if more > 0 else ""}</div>'
        title = _esc(record.title or record.url or "untitled")
        linked = (
            f'<a href="{_esc(record.url)}" rel="noreferrer noopener" target="_blank">{title}</a>'
            if record.url
            else title
        )
        rows.append(
            f'<li class="wentout"><span class="when">{_esc(record.date_published or "undated")}</span> '
            f"{linked}<span class=\"drew\">{_esc(drew)}</span>{who}</li>"
        )

    caveats = []
    if history.unattached_engagements:
        caveats.append(
            f"{history.unattached_engagements} engagement"
            f"{'' if history.unattached_engagements == 1 else 's'} landed on posts the "
            "content library does not list, so they are counted nowhere above. The "
            "library is maintained by hand, so this is expected rather than wrong."
        )
    if history.unreadable_engagements:
        caveats.append(
            f"{history.unreadable_engagements} engagement row"
            f"{'' if history.unreadable_engagements == 1 else 's'} carry no readable post "
            "column, which means the column names in ingestion/portal_sources.yaml need "
            "updating against what the scrape actually returns. This one is ours."
        )
    note = (
        f'<p class="muted">{_esc(" ".join(caveats))}</p>' if caveats else ""
    )

    return f"""
<section class="performance">
  <h2>Post history</h2>
  <ul class="wentoutlist">{"".join(rows)}</ul>
  {note}
</section>
"""


def _provenance(windows, taxonomy, today, window_source, tags_path, refs, titles) -> str:
    # Cut to one line on 2026-09-15. It was an eight-row table plus two
    # paragraphs, and Antonio's read was "seems a bit useless ... if you
    # really think we should keep it, then make it smaller". Kept rather than
    # deleted, because which file a page was rendered from is the question
    # anyone asks first when two people are looking at different calendars,
    # and a page that cannot answer it wastes a meeting. Everything that was
    # in the table and is not here is still printed by `main` on every render.
    return f"""
<footer>
  <p class="prov-line">
    {_esc(window_source)}. Rendered
    {_esc(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))}.
  </p>
</footer>
"""


# The whole style sheet, as a plain string rather than inside the page
# f-string. Every brace in CSS had to be doubled while it lived there,
# which is a silent-corruption risk on a file this size and made the
# sheet unreadable next to the markup it styles.
_STYLE = """/* House style, settled 2026-09-15 and recorded in BUILD-PLAN section 4a.
   Three QofAI surfaces were read and they do not agree with each other, so
   each one wins on a different thing rather than being averaged:

     colour     from qofai.com, which is the brand: near-black ground,
                near-white ink, two greys, hairline borders, and exactly one
                saturated accent, blue. No second hue, no gradient, no glow.
     structure  from the deck generator: panel over field over ground, small
                muted labels above values, mono for a ref or an id, and a
                token set here rather than colours written inline.
     scale      from the portal, so the page feels native to the container it
                is mounted in: a 4/8/12/16/24/32/48 spacing ladder, its type
                sizes, and its [data-theme] switch.

   The portal's own accent is orange and it carries a --glow-accent token.
   Neither is taken. The instruction was blue, black and white with no glow,
   and the brand site agrees, so the page follows the brand rather than the
   shell it is mounted in. The consequence to know rather than to fix: the
   bar above this page is orange and this page is blue.

   Approval states are the one place a non-blue hue is load-bearing rather
   than decorative, so they are the only other colours here, and each is
   defined in both themes, because a state colour that is only legible in one
   of them is a badge nobody can read. */
:root {
  color-scheme: dark;

  --bg: #0a0a0a;
  --panel: #141414;
  --field: #0f0f0f;
  --ink: #fafafa;
  --muted: #a3a3a3;
  --muted-2: #737373;
  --line: #262626;
  --line-soft: rgba(255, 255, 255, .12);
  --accent: #2565cc;
  --accent-ink: #ffffff;
  --accent-soft: rgba(37, 101, 204, .16);
  --accent-hover: #1f56ad;
  --tile: rgba(255, 255, 255, .045);
  --line-faint: #1c1c1c;

  --ok: #54d68a;      --ok-bg: rgba(84, 214, 138, .12);      --ok-line: rgba(84, 214, 138, .34);
  --warn: #e3b341;    --warn-bg: rgba(227, 179, 65, .12);    --warn-line: rgba(227, 179, 65, .34);
  --err: #f0716a;     --err-bg: rgba(240, 113, 106, .12);    --err-line: rgba(240, 113, 106, .34);
  --done: #8b9ab5;    --done-bg: rgba(139, 154, 181, .12);   --done-line: rgba(139, 154, 181, .30);
  --unknown: #8a8a8a; --unknown-bg: rgba(138, 138, 138, .12); --unknown-line: rgba(138, 138, 138, .30);

  --space-1: 4px; --space-2: 8px; --space-3: 12px; --space-4: 16px;
  --space-5: 24px; --space-6: 32px; --space-7: 48px;
  --fs-body: 15px; --fs-ui: 13px; --fs-meta: 12px;
  --radius: 10px; --radius-sm: 6px;
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}

/* The light half of the same set, applied when the portal says light and
   when nothing says anything and the reviewer's own machine prefers it. The
   :not([data-theme="dark"]) guard is what stops an explicit dark choice
   being overridden by the operating system. */
:root {
  --light-bg: #ffffff;
  --light-panel: #fbfbfa;
  --light-field: #f6f6f4;
  --light-ink: #0a0a0a;
  --light-muted: #5c5c5c;
  --light-muted-2: #7a7a7a;
  --light-line: #e4e4e1;
  --light-line-soft: rgba(0, 0, 0, .10);
  --light-tile: rgba(0, 0, 0, .035);
  --light-line-faint: #efefec;
}
@media (prefers-color-scheme: light) {
  :root:not([data-theme="dark"]) { color-scheme: light; }
}
:root[data-theme="light"] {
  color-scheme: light;
  --bg: var(--light-bg); --panel: var(--light-panel); --field: var(--light-field);
  --ink: var(--light-ink); --muted: var(--light-muted); --muted-2: var(--light-muted-2);
  --line: var(--light-line); --line-soft: var(--light-line-soft);
  --tile: var(--light-tile); --line-faint: var(--light-line-faint);
  --accent-soft: rgba(37, 101, 204, .10);
  --ok: #128a4b;      --ok-bg: rgba(18, 138, 75, .08);       --ok-line: rgba(18, 138, 75, .32);
  --warn: #8a6d1f;    --warn-bg: rgba(138, 109, 31, .08);    --warn-line: rgba(138, 109, 31, .32);
  --err: #b3392b;     --err-bg: rgba(179, 57, 43, .08);      --err-line: rgba(179, 57, 43, .32);
  --done: #4a5570;    --done-bg: rgba(74, 85, 112, .08);     --done-line: rgba(74, 85, 112, .28);
  --unknown: #6f6f6f; --unknown-bg: rgba(111, 111, 111, .08); --unknown-line: rgba(111, 111, 111, .26);
}
@media (prefers-color-scheme: light) {
  :root:not([data-theme="dark"]) {
    --bg: var(--light-bg); --panel: var(--light-panel); --field: var(--light-field);
    --ink: var(--light-ink); --muted: var(--light-muted); --muted-2: var(--light-muted-2);
    --line: var(--light-line); --line-soft: var(--light-line-soft);
    --tile: var(--light-tile); --line-faint: var(--light-line-faint);
    --accent-soft: rgba(37, 101, 204, .10);
    --ok: #128a4b;      --ok-bg: rgba(18, 138, 75, .08);    --ok-line: rgba(18, 138, 75, .32);
    --warn: #8a6d1f;    --warn-bg: rgba(138, 109, 31, .08); --warn-line: rgba(138, 109, 31, .32);
    --err: #b3392b;     --err-bg: rgba(179, 57, 43, .08);   --err-line: rgba(179, 57, 43, .32);
    --done: #4a5570;    --done-bg: rgba(74, 85, 112, .08);  --done-line: rgba(74, 85, 112, .28);
    --unknown: #6f6f6f; --unknown-bg: rgba(111, 111, 111, .08); --unknown-line: rgba(111, 111, 111, .26);
  }
}

* { box-sizing: border-box; }
body {
  margin: 0; padding: var(--space-6) var(--space-5) var(--space-7);
  font: var(--fs-body)/1.55 var(--sans);
  color: var(--ink); background: var(--bg);
  -webkit-font-smoothing: antialiased;
}
main { max-width: 1320px; margin: 0 auto; }

/* The month is the page.
   The sidebar lasted one build. It put the month in a pinned column beside a
   scrolling list of posts, and the verdict was "forget the sidebar ... I
   didn't like it". What replaced it was the simpler thing that was asked for:
   the calendar is the first thing you see and the biggest thing on the page.

   Since 2026-09-22 the post you open sits in a panel on the right, so it can
   be read and approved without scrolling away from the month, and the list of
   posts that used to run underneath is off the page. The panel exists only
   while a post is open, and the page widens to make room rather than
   squeezing the month. */
.month { margin-bottom: var(--space-6); }
.month .grid { gap: var(--space-2); }
h1 { font-size: 28px; font-weight: 500; line-height: 1.15; margin: 0; letter-spacing: -0.02em; }
h2 { font-size: 18px; font-weight: 500; margin: var(--space-6) 0 var(--space-3); letter-spacing: -0.01em; }
h3 { font-size: var(--fs-body); font-weight: 500; margin: 0; line-height: 1.35; }
p { margin: 0 0 var(--space-2); }
code { font: var(--fs-ui)/1.4 var(--mono); color: var(--muted); }
a { color: var(--accent); }
.muted { color: var(--muted); }
button:focus-visible, input:focus-visible, summary:focus-visible {
  outline: 2px solid var(--accent); outline-offset: 2px;
}

/* Small uppercase kicker above a value, which is the deck generator's one
   structural idea worth copying wholesale. */
.kicker, .sectionlabel {
  font-size: var(--fs-meta); text-transform: uppercase; letter-spacing: .08em;
  color: var(--muted-2); font-weight: 500;
}
.sectionlabel { margin: var(--space-7) 0 var(--space-3); }

/* ---- header ------------------------------------------------------------
   One heading, the month, with what the page is above it as a label. Two
   headings stacked, "Content calendar" over "October 2026", spent a line on
   the thing a reader already knew. */
.masthead { display: flex; align-items: flex-end; justify-content: space-between; gap: var(--space-4); flex-wrap: wrap; }
.masthead .kicker { margin: 0 0 var(--space-1); }
header .meta { color: var(--muted); font-size: var(--fs-ui); margin: 0 0 3px; }
header .meta span + span::before { content: " · "; }
.viewtabs { display: flex; gap: var(--space-2); margin-top: var(--space-4); }
.view[hidden] { display: none; }

.callout {
  margin: var(--space-5) 0 var(--space-1); padding: var(--space-4) var(--space-4);
  background: var(--panel); border: 1px solid var(--line);
  border-left: 3px solid var(--warn); border-radius: var(--radius);
}
.callout p { max-width: 78ch; color: var(--muted); margin: 0; }

/* ---- the one toolbar ---------------------------------------------------
   The filter on the left and every page-wide action on the right, on the row
   above the month. The reorder and decision bars are still two elements,
   because the script finds each by its class, and `display: contents` lets
   their controls sit in one row as if they were one. Status lines and the
   disk page's two notes take a line of their own under the buttons. */
.toolbar {
  display: flex; align-items: center; justify-content: space-between;
  gap: var(--space-2) var(--space-4); flex-wrap: wrap;
  margin: var(--space-4) 0 var(--space-4); padding: var(--space-2);
  background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius);
  font-size: var(--fs-ui);
}
.actions {
  display: flex; align-items: center; justify-content: flex-end;
  gap: var(--space-2); flex-wrap: wrap; flex: 1 1 auto;
}
.reorderbar, .decidebar { display: contents; }
.reorderbar[hidden] { display: none; }
.decidebar label { order: 1; color: var(--muted-2); display: inline-flex; align-items: center; gap: var(--space-2); }
.reorderbar button { order: 2; }
.decidebar button { order: 3; }
.actions .status, .actions .recordhint, .actions .nosave-hint {
  order: 9; flex-basis: 100%; text-align: right; color: var(--muted-2); font-size: var(--fs-meta);
}
.actions .status:empty { display: none; }
.actions [hidden] { display: none; }
.toolbar button, .detailhead button {
  font: var(--fs-ui)/1.2 var(--sans); padding: 7px var(--space-3); cursor: pointer;
  border: 1px solid var(--line); background: var(--field); color: var(--ink);
  border-radius: var(--radius-sm);
}
.toolbar button:hover:not(:disabled) { border-color: var(--accent); }
.toolbar button:disabled { opacity: .4; cursor: default; }
.toolbar .save-order:not(:disabled) {
  background: var(--accent); border-color: var(--accent); color: var(--accent-ink); font-weight: 500;
}
.toolbar input, .declinerow input, .publishrow input, .whoother input {
  font: var(--fs-ui)/1.2 var(--sans); padding: 6px var(--space-2);
  border: 1px solid var(--line); border-radius: var(--radius-sm);
  background: var(--field); color: var(--ink);
}
.toolbar input { width: 14ch; }
.whofield[hidden], .whoami[hidden] { display: none; }
.whoami { order: 1; color: var(--muted-2); display: inline-flex; align-items: center; gap: 6px; }
.whoami strong { color: var(--ink); font-weight: 500; }
.toolbar .whochange {
  padding: 2px 4px; border: none; background: none; color: var(--accent); cursor: pointer;
}
.toolbar .whochange:hover { text-decoration: underline; }

/* ---- the lens filter --------------------------------------------------
   Three calendars, one page. The filter is a control over which of the three
   is on screen, not a control over what any of them contains: each lens's
   window was assembled separately and is rendered whole, so switching lens
   changes what you are looking at and never what it says. */
.lensbar { display: flex; align-items: center; gap: var(--space-2); flex-wrap: wrap; }
.lensbar .kicker { margin: 0 var(--space-1) 0 var(--space-2); }
.lensbtn, .viewbtn {
  font: var(--fs-ui)/1.2 var(--sans); padding: 7px var(--space-4); cursor: pointer;
  border: 1px solid var(--line); background: var(--field); color: var(--muted);
  border-radius: var(--radius-sm);
}
.lensbtn:hover, .viewbtn:hover { color: var(--ink); border-color: var(--line-soft); }
.lensbtn[aria-pressed="true"], .viewbtn[aria-pressed="true"] {
  background: var(--accent); border-color: var(--accent); color: var(--accent-ink);
}
.lensbtn .count { opacity: .72; margin-left: var(--space-1); }

/* The posts the month cannot hold. Red, because a post outside the dates it
   belongs to is a state the calendar is in, and each is a button that opens
   it in the panel, since there is nowhere else on the page to find it. */
.offgrid {
  display: flex; align-items: center; gap: var(--space-2); flex-wrap: wrap;
  margin: 0 0 var(--space-4); padding: var(--space-2) var(--space-3);
  border: 1px solid var(--err-line); border-left: 3px solid var(--err);
  background: var(--err-bg); border-radius: var(--radius-sm);
  font-size: var(--fs-ui); color: var(--err);
}
.offgridopen {
  font: var(--fs-ui)/1.2 var(--sans); padding: 4px var(--space-2); cursor: pointer;
  border: 1px solid var(--err-line); background: var(--panel); color: var(--ink);
  border-radius: var(--radius-sm);
}
.offgridopen:hover { border-color: var(--accent); }
.offgridopen[hidden] { display: none; }

/* ---- the month grid ---------------------------------------------------
   Bigger type and no truncation, both from the 2026-09-15 recording. The
   cells grow to fit the name rather than the name being cut to fit the
   cell: a post shown as "the 74% problem, why..." is a post nobody can
   identify, and the ellipsis was hiding the very thing the grid is for.

   From 2026-09-22 each post is its own tile, and its left edge carries its
   approval state. Three posts on one day used to read as one block of text
   with three 7px dots in it. The person stays a word rather than a colour,
   as it has been since 2026-09-15, because colour already means state. */
.grid { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: var(--space-1); }
.gridhead {
  font-size: var(--fs-meta); color: var(--muted-2); text-transform: uppercase;
  letter-spacing: .08em; padding: 0 var(--space-1) var(--space-1);
}
.cell {
  min-height: 96px; padding: var(--space-2); border: 1px solid var(--line);
  border-radius: var(--radius-sm); background: transparent;
}
/* Busy days carry the panel colour and empty ones do not, so the days with
   something on them are the ones the eye lands on. Weekends recede further,
   since that is the Gregorian week and not an editorial call. */
.cell.has-slots { background: var(--panel); border-color: var(--line-soft); }
.cell.weekend:not(.has-slots) { border-color: var(--line-faint); }
.cell.weekend:not(.has-slots) .daynum { opacity: .6; }
.cell.out { border: none; background: transparent; }
/* Every cell is a drop target, including the empty ones. Decided 2026-09-15:
   any calendar day, not only a Monday or a Thursday. The posting days are a
   policy about where posts should go, and moving one somewhere else is
   exactly the judgement a human is here to apply. */
.cell.is-drop { border-color: var(--accent); background: var(--accent-soft); }
.cell.out.is-drop { border: 1px solid var(--accent); }
.daynum { font-size: var(--fs-meta); color: var(--muted-2); margin-bottom: var(--space-2); line-height: 22px; }
.daynum span { display: inline-block; min-width: 22px; }
/* Today is the filled blue mark every calendar uses, rather than an outline
   that reads the same as a drop target. */
.cell.is-today .daynum span {
  padding: 0 7px; border-radius: 11px; text-align: center;
  background: var(--accent); color: var(--accent-ink); font-weight: 500;
}
.mini {
  display: block; min-width: 0; margin-bottom: 6px; padding: 6px var(--space-2) 7px;
  background: var(--tile); border-left: 3px solid var(--unknown);
  border-radius: var(--radius-sm); font-size: var(--fs-ui); line-height: 1.35;
  cursor: grab;
}
.mini:last-child { margin-bottom: 0; }
.mini:hover { background: var(--accent-soft); }
.mini:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.mini.is-dragging { opacity: .4; cursor: grabbing; }
.mini[hidden] { display: none; }
/* The dot the tile's edge replaced. Kept in the markup, and in the legend's
   history, so nothing that reads a `pip-` class breaks. */
.mini .pip { display: none; }
/* `break-word` rather than `anywhere`, so a title breaks mid-word only when
   one word is wider than the column, and not whenever the column is narrow. */
.minitext { display: block; min-width: 0; overflow-wrap: break-word; color: var(--ink); }
.minibody { display: block; min-width: 0; }
/* Whose post this is. One calendar carrying three people's posts has to say
   which is which, and colour is already spoken for: it carries the state,
   which is the thing that was asked to change colour when somebody
   approves. So the person is a word, not a second colour. */
.lenstag {
  display: block; font-size: 11px; text-transform: uppercase; font-weight: 500;
  letter-spacing: .07em; color: var(--muted-2); margin-bottom: 2px;
}
.legend {
  display: flex; justify-content: flex-end; gap: var(--space-4); flex-wrap: wrap;
  margin: 0 0 var(--space-2); font-size: var(--fs-meta); color: var(--muted-2);
}
.legenditem { display: inline-flex; align-items: center; gap: 6px; }
.swatch { width: 3px; height: 12px; border-radius: 2px; display: inline-block; background: var(--unknown); }

/* The post open in the panel, marked in the month. */
.mini.is-selected { background: var(--accent-soft); box-shadow: inset 0 0 0 1px var(--accent); }

/* ---- what each calendar argues ------------------------------------------
   Each calendar's own argument, side by side under the month. One calendar
   does not mean one argument: the three windows were assembled separately and
   each states the arc its own month makes, so all three are shown and
   attributed rather than merged into something nobody wrote. */
.arcs { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: var(--space-3); }
.arccard {
  background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius);
  border-top: 2px solid var(--accent); padding: var(--space-4);
}
.arccard[hidden] { display: none; }
.arccard p { font-size: var(--fs-ui); line-height: 1.6; color: var(--muted); }
.arccard .kicker { margin-bottom: var(--space-2); color: var(--ink); }
.arcmeta { color: var(--muted-2); font-size: var(--fs-meta); margin: var(--space-3) 0 0; }
.arccard .supply { margin: var(--space-2) 0 0; }
.arccard .supply summary { cursor: pointer; color: var(--muted-2); font-size: var(--fs-meta); }
.arccard .supply summary:hover { color: var(--ink); }
.arccard .supply p { margin-top: var(--space-2); font-size: var(--fs-meta); }

/* ---- the card, as it appears in the panel ------------------------------
   The list these cards used to form is hidden (see `_timeline`), so the
   panel is the only place a card is seen. Title and state together, whose
   post it is, then one row of controls: the arrows quiet on the left,
   Approve as the one filled button on the right. */
.posts[hidden], .card[hidden] { display: none; }
.card {
  background: var(--panel); border: 1px solid var(--line);
  border-radius: var(--radius); padding: var(--space-4);
}
.card.is-moved { border-color: var(--accent); }
.cardhead { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--space-3); }
.cardhead h3 { flex: 1 1 auto; min-width: 0; }
.badge {
  flex: 0 0 auto; margin-top: 2px;
  font-size: var(--fs-meta); padding: 2px var(--space-2); border-radius: var(--radius-sm);
  border: 1px solid; white-space: nowrap;
}
.ref { margin: var(--space-1) 0 0; }
.ref .lenstag { display: inline; }
.cardtools {
  display: flex; align-items: center; gap: var(--space-2); flex-wrap: wrap;
  margin: var(--space-3) 0; padding-bottom: var(--space-3); border-bottom: 1px solid var(--line);
}
.toolgap { flex: 1 1 auto; }
.move {
  font: var(--fs-ui)/1 var(--sans); width: 28px; height: 28px; cursor: pointer;
  border: 1px solid var(--line); background: transparent; border-radius: var(--radius-sm);
  color: var(--muted-2);
}
.move:hover:not(:disabled) { color: var(--ink); border-color: var(--accent); }
.move:disabled { opacity: .3; cursor: default; }
.decide {
  font: 500 var(--fs-ui)/1.2 var(--sans); padding: 7px var(--space-4); cursor: pointer;
  border: 1px solid var(--line); background: transparent; color: var(--muted);
  border-radius: var(--radius-sm);
}
.decide[data-decide="approved"] { background: var(--accent); border-color: var(--accent); color: var(--accent-ink); }
.decide[data-decide="approved"]:hover:not(:disabled) { background: var(--accent-hover); border-color: var(--accent-hover); }
.decide[data-decide="rejected"]:hover:not(:disabled) { color: var(--err); border-color: var(--err-line); background: var(--err-bg); }
.decide:disabled { opacity: .4; cursor: default; }
.decide.is-on[data-decide="approved"] { background: var(--ok-bg); border-color: var(--ok-line); color: var(--ok); }
.decide.is-on[data-decide="rejected"] { background: var(--err-bg); border-color: var(--err-line); color: var(--err); }
.pending {
  font-size: var(--fs-meta); padding: 2px var(--space-2); border-radius: 10px; font-style: italic;
  background: var(--warn-bg); border: 1px solid var(--warn-line); color: var(--warn);
}
.pending[hidden] { display: none; }
/* Under the buttons rather than between them, so staging a decision on the
   disk page does not push Decline onto a line of its own. */
.cardtools .pending { order: 9; margin-left: auto; }
.moved {
  margin: 0 0 var(--space-3); padding: var(--space-2) var(--space-3); font-size: var(--fs-ui);
  color: var(--warn); background: var(--warn-bg); border: 1px solid var(--warn-line);
  border-radius: var(--radius-sm);
}
.moved[hidden] { display: none; }
/* A class that sets display beats the browser's [hidden] rule, so every
   element this page hides with the attribute has to say so itself. */
.declinerow { margin: 0 0 var(--space-3); font-size: var(--fs-ui); display: flex; gap: var(--space-2); flex-wrap: wrap; align-items: center; }
.declinerow[hidden] { display: none; }
.declinerow label { display: flex; align-items: center; gap: var(--space-2); flex: 1 1 100%; }
.declinerow input { flex: 1 1 auto; min-width: 0; }
.declinerow .noteneeded { color: var(--muted-2); }
.declinerow.is-missing input { border-color: var(--err); }
.card.is-deciding { border-color: var(--ok-line); }
.card.is-declining { border-color: var(--err-line); }
.summary { color: var(--ink); line-height: 1.6; }
.rationale { color: var(--muted); font-size: var(--fs-ui); line-height: 1.6; }
.mismatch { color: var(--err); font-size: var(--fs-ui); }

/* The folded parts of a card, all the same quiet control. */
.post, .rationalewrap, .alts { margin-top: var(--space-3); font-size: var(--fs-ui); }
.post > summary, .rationalewrap > summary, .alts > summary {
  cursor: pointer; color: var(--muted-2); font-weight: 500;
}
.post > summary:hover, .rationalewrap > summary:hover, .alts > summary:hover { color: var(--ink); }
.rationale { margin-top: var(--space-2); }
/* The post itself. Pre-wrapped rather than reflowed into paragraphs: the
   file's own line breaks are the author's, and a LinkedIn post's short lines
   are part of how it reads. */
.postbody {
  margin-top: var(--space-3); padding: var(--space-4) var(--space-5); background: var(--field);
  border: 1px solid var(--line); border-radius: var(--radius-sm);
  overflow-wrap: break-word; color: var(--ink);
  font-size: var(--fs-body); line-height: 1.7;
}
/* The post, formatted (see `_render_post`). Headings at reading size rather
   than page size, since they are inside a card inside a panel. */
.postbody > :first-child { margin-top: 0; }
.postbody p, .postbody ul, .postbody ol, .postbody blockquote { margin: 0 0 var(--space-4); }
.postbody .md1, .postbody .md2, .postbody .md3 { font-weight: 600; line-height: 1.3; margin: var(--space-5) 0 var(--space-2); }
.postbody .md1 { font-size: 19px; letter-spacing: -0.01em; }
.postbody .md2 { font-size: 16px; }
.postbody .md3 { font-size: var(--fs-body); color: var(--muted); }
.postbody ul, .postbody ol { padding-left: var(--space-5); }
.postbody li { margin-bottom: var(--space-1); }
.postbody blockquote { border-left: 2px solid var(--line-soft); padding-left: var(--space-3); color: var(--muted); }
.postbody hr { border: none; border-top: 1px solid var(--line); margin: var(--space-5) 0; }
.postbody strong { font-weight: 600; }
.postbody code { font-size: .9em; }
.postsource { margin: var(--space-2) 0 0; color: var(--muted-2); font-size: var(--fs-meta); }
.postmissing { margin-top: var(--space-2); font-size: var(--fs-ui); }

/* What a post argues and how. Words separated by dots rather than a row of
   outlined pills, which made nine tags look like nine buttons. */
.tags {
  display: flex; flex-direction: column; gap: var(--space-2);
  margin-top: var(--space-4); padding-top: var(--space-3); border-top: 1px solid var(--line);
  font-size: var(--fs-ui);
}
.fieldlabel {
  display: block; color: var(--muted-2); margin-bottom: 2px;
  font-size: 11px; text-transform: uppercase; letter-spacing: .07em; font-weight: 500;
}
.chip { display: inline; color: var(--muted); }
.chip + .chip::before { content: "\\00b7"; margin: 0 6px; color: var(--muted-2); }
.chip.unknown { color: var(--err); }
.answers { margin-top: var(--space-3); font-size: var(--fs-ui); }
.answers ul { margin: var(--space-1) 0 0; padding-left: var(--space-5); }
.alts { border-top: 1px solid var(--line); padding-top: var(--space-3); }
.alts ul { margin: var(--space-2) 0 0; padding-left: var(--space-5); }
.alts li { margin-bottom: var(--space-2); }
.altname { display: block; font-weight: 500; color: var(--ink); }
.altref { display: block; color: var(--muted-2); font-size: var(--fs-meta); margin: 1px 0 2px; font-family: var(--mono); }
.altwhy { display: block; color: var(--muted); }

/* ---- the panel a post opens in ----------------------------------------
   Beside the month, pinned while the page scrolls under it, and scrolling on
   its own when the post is long. The page gets wider while it is open, up to
   the width of a large screen, so the month keeps room for its titles.

   Below 1400px there is not room for both: beside a panel the month's
   columns fell to about 70px on a 1100px screen, and at 1300px a word like
   "Deployments" still did not fit one.
   So there the panel slides over the right edge of the page instead, and
   the month keeps its full width underneath. */
main:has(.calwork.has-detail) { max-width: 1760px; }
.calwork.has-detail {
  display: grid; grid-template-columns: minmax(0, 1fr) minmax(320px, 400px);
  gap: var(--space-5); align-items: start;
}
aside.detail {
  position: sticky; top: var(--space-4);
  max-height: calc(100vh - 2 * var(--space-4)); overflow-y: auto;
  background: var(--panel); border: 1px solid var(--line); border-top: 2px solid var(--accent);
  border-radius: var(--radius); padding: var(--space-4);
}
aside.detail[hidden] { display: none; }
.detailhead { display: flex; align-items: center; gap: var(--space-2); }
.detaildate { font-weight: 500; color: var(--ink); }
.detailpos { flex: 1 1 auto; color: var(--muted-2); font-size: var(--fs-meta); font-variant-numeric: tabular-nums; }
.detailhead button { width: 30px; height: 30px; padding: 0; font-size: 18px; line-height: 1; color: var(--muted); }
.detailhead button:hover:not(:disabled) { color: var(--ink); border-color: var(--accent); }
.detailhead button:disabled { opacity: .3; cursor: default; }
.detailstatus { margin: var(--space-2) 0 0; font-size: var(--fs-ui); color: var(--muted); }
.detailstatus:empty { display: none; }
.detailbody { margin-top: var(--space-3); border-top: 1px solid var(--line); padding-top: var(--space-4); }
aside.detail .card { background: transparent; border: none; padding: 0; }
aside.detail .card h3 { font-size: 18px; letter-spacing: -0.01em; }
@media (max-width: 1399px) {
  main:has(.calwork.has-detail) { max-width: 1320px; }
  .calwork.has-detail { display: block; }
  aside.detail {
    position: fixed; top: 0; right: 0; bottom: 0; z-index: 10;
    width: min(420px, 100vw); max-height: none; border-radius: 0;
    border-top: none; border-right: none; border-bottom: none;
    border-left: 2px solid var(--accent);
  }
}

/* ---- 2026-09-23: the parts that make it feel finished ------------------ */

/* Initials. A letter in a neutral circle, never a colour, because colour
   already means approval state. */
.initial {
  display: inline-flex; align-items: center; justify-content: center;
  width: 18px; height: 18px; border-radius: 50%; margin-right: 6px; flex: 0 0 18px;
  background: var(--line-soft); color: var(--ink);
  font: 600 10px/1 var(--sans); letter-spacing: 0; text-transform: uppercase;
}
.mini .lenstag { display: flex; align-items: center; }
.mini .initial { width: 16px; height: 16px; flex-basis: 16px; font-size: 9px; }
.ref { display: flex; align-items: center; }
.ref .initial { width: 22px; height: 22px; flex-basis: 22px; font-size: 11px; }

/* Header: the counts, and the theme switch beside them. */
.mastside { display: flex; align-items: center; gap: var(--space-3); }
.themetoggle {
  font: var(--fs-meta)/1 var(--sans); padding: 6px var(--space-3); cursor: pointer;
  border: 1px solid var(--line); background: transparent; color: var(--muted-2);
  border-radius: 999px;
}
.themetoggle:hover { color: var(--ink); border-color: var(--accent); }

/* The month at a glance. State as a bar, people as counts, no targets. */
.glance { display: flex; align-items: center; gap: var(--space-5); flex-wrap: wrap; margin-top: var(--space-4); }
.glance:empty { display: none; }
.glancebar {
  display: flex; gap: 2px; flex: 1 1 320px; max-width: 520px; height: 6px;
  border-radius: 3px; overflow: hidden;
}
.glancebar .seg { flex-basis: 0; min-width: 6px; background: var(--unknown); transition: flex-grow .5s ease; }
.glancepeople { display: flex; gap: var(--space-4); font-size: var(--fs-ui); color: var(--muted); }
.glanceperson { display: inline-flex; align-items: center; }

/* Tiles: a little more presence, and a little life. */
.minitext { font-weight: 500; font-size: 13.5px; letter-spacing: -0.003em; }
.mini { transition: background-color .15s ease, transform .15s ease, box-shadow .15s ease; }
.mini:hover { transform: translateY(-1px); }
.mini.is-changed { animation: changed 1.4s ease; }
@keyframes changed { 0% { box-shadow: inset 0 0 0 2px var(--accent); background: var(--accent-soft); } 100% { box-shadow: none; } }

/* The panel slides in, and the post inside it fades as you step. */
aside.detail { animation: slidein .22s ease-out; }
@keyframes slidein { from { opacity: 0; transform: translateX(12px); } to { opacity: 1; transform: none; } }
.card.is-entering { animation: fadein .2s ease-out; }
@keyframes fadein { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }

/* What a decided post offers instead of Approve and Decline. */
.decide.undo { background: transparent; color: var(--muted); }
.decide.undo:hover:not(:disabled) { color: var(--ink); border-color: var(--accent); }
.decide.publish { background: transparent; color: var(--ink); border-color: var(--line-soft); }
.decide.publish:hover:not(:disabled) { border-color: var(--accent); }
.postlink { font-size: var(--fs-ui); color: var(--accent); margin-right: var(--space-1); }
.reach { font-size: var(--fs-meta); color: var(--muted-2); margin-right: var(--space-1); }
.publishrow { margin: 0 0 var(--space-3); font-size: var(--fs-ui); display: flex; gap: var(--space-2); flex-wrap: wrap; align-items: center; }
.publishrow[hidden] { display: none; }
.publishrow label { display: flex; align-items: center; gap: var(--space-2); flex: 1 1 100%; }
.publishrow input { flex: 1 1 auto; min-width: 0; }
.publishrow .noteneeded { color: var(--muted-2); }
.publishrow.is-missing input { border-color: var(--err); }

/* Swap: each alternative, with its button on the right. */
.alts ul { list-style: none; padding-left: 0; }
.alts li { display: flex; align-items: flex-start; gap: var(--space-3); padding: var(--space-2) 0; border-top: 1px solid var(--line); margin: 0; }
.alts li:first-child { border-top: none; }
.alttext { flex: 1 1 auto; min-width: 0; }
.swapbtn {
  flex: 0 0 auto; font: 500 var(--fs-ui)/1.2 var(--sans); padding: 6px var(--space-3); cursor: pointer;
  border: 1px solid var(--accent); background: var(--accent-soft); color: var(--ink);
  border-radius: var(--radius-sm);
}
.swapbtn:hover:not(:disabled) { background: var(--accent); color: var(--accent-ink); }
.swapbtn:disabled { opacity: .35; cursor: default; border-color: var(--line); background: transparent; }

/* Who is reviewing: asked once, over the page. */
.whomodal {
  position: fixed; inset: 0; z-index: 30; display: flex; align-items: center; justify-content: center;
  background: rgba(0, 0, 0, .55); padding: var(--space-4);
}
.whomodal[hidden] { display: none; }
.whocard {
  width: min(420px, 100%); background: var(--panel); border: 1px solid var(--line);
  border-top: 2px solid var(--accent); border-radius: var(--radius); padding: var(--space-5);
  animation: fadein .2s ease-out;
}
.whocard h2 { margin: var(--space-1) 0 var(--space-4); font-size: 22px; }
.whochoices { display: grid; gap: var(--space-2); }
.whopick {
  display: flex; align-items: center; gap: var(--space-2); text-align: left;
  font: 500 var(--fs-body)/1.2 var(--sans); padding: var(--space-3); cursor: pointer;
  border: 1px solid var(--line); background: var(--field); color: var(--ink); border-radius: var(--radius-sm);
}
.whopick:hover, .whopick:focus-visible { border-color: var(--accent); background: var(--accent-soft); }
.whopick .initial { width: 26px; height: 26px; flex-basis: 26px; font-size: 12px; margin-right: var(--space-1); }
.whoother { display: flex; gap: var(--space-2); margin-top: var(--space-3); }
.whoother input { flex: 1 1 auto; min-width: 0; padding: 8px var(--space-2); }
.whoother button {
  font: var(--fs-ui)/1.2 var(--sans); padding: 8px var(--space-3); cursor: pointer;
  border: 1px solid var(--line); background: var(--field); color: var(--ink); border-radius: var(--radius-sm);
}

/* The hover preview. */
.peek {
  position: fixed; z-index: 20; width: 300px; pointer-events: none;
  background: var(--panel); border: 1px solid var(--line-soft); border-radius: var(--radius);
  padding: var(--space-3) var(--space-4); animation: fadein .15s ease-out;
}
.peek[hidden] { display: none; }
.peekmeta { margin: 0 0 var(--space-1); font-size: var(--fs-meta); color: var(--muted-2); text-transform: uppercase; letter-spacing: .06em; }
.peektitle { margin: 0 0 var(--space-2); font-weight: 500; line-height: 1.35; }
.peektext { margin: 0; font-size: var(--fs-ui); color: var(--muted); line-height: 1.55; }

/* Argument cards: two lines, then More. */
.arctext { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden; margin-bottom: 0; }
.arccard.is-open .arctext { display: block; -webkit-line-clamp: unset; }
.arcmore {
  margin-top: var(--space-1); padding: 0; border: none; background: none; cursor: pointer;
  font: var(--fs-meta)/1.4 var(--sans); color: var(--accent);
}
.arcmore[hidden] { display: none; }

h1 { font-size: 32px; }

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important; }
  .mini:hover { transform: none; }
}

/* ---- validation, post history, the deployment notice, the footer ------ */
.findings { list-style: none; padding: 0; margin: 0; }
.finding {
  border: 1px solid var(--line); border-left: 3px solid var(--err); background: var(--panel);
  padding: var(--space-2) var(--space-3); margin-bottom: var(--space-2);
  border-radius: var(--radius-sm); font-size: var(--fs-ui);
}
/* `.detail` here is a line of small print under a finding or a post that went
   out. The panel shares the word, so its rules all say `aside.detail`. */
.finding .detail, .wentout .detail { color: var(--muted); margin-top: 3px; }
/* Loud, because it is the one notice on this page that means somebody has to
   go and change a setting, and quiet was how it failed for a whole deploy. */
.corpusgap {
  border: 1px solid var(--err-line); border-left: 3px solid var(--err);
  background: var(--err-bg); border-radius: var(--radius-sm);
  padding: var(--space-2) var(--space-3); margin-bottom: var(--space-3); max-width: 78ch;
}
.corpusgap h2 { margin-top: 0; font-size: var(--fs-ui); }
/* What went out. Quieter than anything on the calendar itself, deliberately:
   it is context for the plan and not part of it. */
.wentoutlist { list-style: none; padding: 0; margin: 0; max-width: 78ch; }
.wentout {
  border: 1px solid var(--line); border-left: 3px solid var(--accent); background: var(--panel);
  padding: var(--space-2) var(--space-3); margin-bottom: var(--space-2);
  border-radius: var(--radius-sm); font-size: var(--fs-ui);
}
.wentout .when { color: var(--muted-2); font-variant-numeric: tabular-nums; margin-right: var(--space-2); }
.wentout .drew { color: var(--muted); margin-left: var(--space-2); }
footer { margin-top: var(--space-7); border-top: 1px solid var(--line); padding-top: var(--space-2); color: var(--muted-2); font-size: var(--fs-ui); }
.prov-line { margin: 0; color: var(--muted-2); font-size: var(--fs-meta); max-width: 90ch; }"""


def _month_label(window) -> str:
    """", October 2026" from the window's own start date, or nothing if unreadable.

    Not the same question as `_grid_months`, which answers for the whole grid.
    This one labels a single window's arc card, and a lens can have two.
    """
    first = slots._as_date(window.start_date)
    return f", {first.strftime('%B %Y')}" if first else ""


def _arcs(windows, taxonomy) -> str:
    """Each calendar's stated arc, side by side and labelled.

    One calendar does not mean one argument. The three windows were assembled
    separately and each states the arc its own month makes, so all three are
    shown and attributed rather than merged. Merging them would need this
    module to decide what October argues across three audiences, and the
    display decides nothing.
    """
    blocks = []
    for window in windows:
        supply = str(window.extra.get("supply_note") or "").strip()
        arc = (
            f'<p class="arctext">{_esc(window.arc)}</p>'
            '<button type="button" class="arcmore" hidden>More</button>'
            if window.arc.strip()
            else '<p class="muted">This window states no arc, which makes it a list of dated '
                 "topics.</p>"
        )
        posts = len(window.slots)
        dates = len({s.date for s in window.slots})
        blocks.append(
            f'<article class="arccard" data-lens="{_esc(window.lens)}" '
            f'data-window="{_esc(window.window_id)}">'
            f'<p class="kicker">{_esc(taxonomy.lens_person(window.lens))}, the '
            f'{_esc(taxonomy.lens_label(window.lens))} lens{_month_label(window)}</p>'
            f"{arc}"
            f'<p class="arcmeta">{posts} post{"" if posts == 1 else "s"} on {dates} '
            f'date{"" if dates == 1 else "s"}</p>'
            + (f'<details class="supply"><summary>Why this calendar holds {posts} '
               f'post{"" if posts == 1 else "s"}</summary><p>{_esc(supply)}</p></details>'
               if supply else "")
            + "</article>"
        )
    return (
        '<section class="arcsection"><h2 class="sectionlabel">What each calendar argues</h2>'
        f'<div class="arcs">{"".join(blocks)}</div></section>'
    )


def _lens_bar(windows, taxonomy: Taxonomy, selected: str) -> str:
    """The filter over the one calendar.

    Changed 2026-09-15. It used to switch between three calendars, one
    visible at a time, which is what Antonio meant when he said a window
    holding three calendars was not what he asked for. There is one calendar
    now and everyone is on it; this narrows what it shows. "All three" is the
    default rather than an option you have to find, because the shared month
    is the point.
    """
    # One button per lens, not per window. A lens holds two windows while
    # next month is assembled and this one is still running, and a filter
    # showing "Jordan" twice would be asking which month rather than whose.
    counts = {}
    for window in windows:
        counts[window.lens] = counts.get(window.lens, 0) + len(window.slots)
    if len(counts) < 2:
        return ""
    buttons = []
    for lens, count in counts.items():
        pressed = "true" if lens == selected else "false"
        buttons.append(
            f'<button class="lensbtn" type="button" data-select-lens="{_esc(lens)}" '
            f'aria-pressed="{pressed}">{_esc(taxonomy.lens_person(lens))}'
            f'<span class="count">{count}</span></button>'
        )
    total = sum(len(w.slots) for w in windows)
    everything = "true" if selected == "all" else "false"
    buttons.insert(
        0,
        f'<button class="lensbtn" type="button" data-select-lens="all" '
        f'aria-pressed="{everything}">Everyone<span class="count">{total}</span></button>',
    )
    return (
        '<div class="lensbar" id="lensbar">'
        '<span class="kicker">Show</span>' + "".join(buttons) + "</div>"
    )


def render_html(
    windows,
    taxonomy: Taxonomy,
    *,
    today: date,
    issues=None,
    titles=None,
    window_source: str = "",
    tags_path: str = "",
    refs=(),
    corpus=None,
    agent_decided: bool = False,
    selected: str = "",
    record_url: str = "",
    reschedule_url: str = "",
    reviewer: str = "",
    history=None,
    links=None,
) -> str:
    """The whole page, as one string. Reads the windows, decides nothing.

    One calendar carrying everyone's posts, rebuilt 2026-09-15 from three
    calendars stacked in one page. Antonio: "All three calendars should have
    all the postings for everyone. Everyone's postings should be on one
    calendar." A slot already knows which lens it belongs to, so merging them
    for display invents nothing and loses nothing: every post keeps its lens,
    its id and its own window's arc.

    The month is the page. It comes first, it is the widest thing on it, and
    the posts run underneath rather than beside, which is the layout that
    replaced the sidebar on the same day it was built. Clicking a post in the
    month opens it in a panel on the right, beside the month, where it can be
    read and decided on without scrolling (2026-09-22).

    `issues` is keyed by window id, since validation is per window and a lens
    has two windows while next month is assembled and this one is still running.
    `record_url` is the route that records a decision, empty for a page opened
    from disk, which has nowhere to write and still ends at the clipboard.

    `history` is what went out and how it did, from
    `ingestion/performance.py`. None, which is what a page rendered from disk
    passes, gets the same one-line placeholder as an empty history, on the
    Post history tab.

    `links` is slot id -> the URL recorded when it was marked published, from
    the approval store. It is what a published post's panel links to.

    `reviewer` prefills who is deciding. Empty today: the local app does not
    know who is at the keyboard, so the page asks once and remembers. It is
    the seam for the portal, which signs a reviewer in with Google and can
    pass that name straight through. Q4 decided identity is recorded rather
    than verified, so this is a default and never a restriction: the store
    records the name it is given, exactly as `queue.py --by` always has.
    """
    if isinstance(windows, CalendarWindow):
        windows = [windows]
    windows = [w for w in windows if w is not None]
    if not windows:
        raise ValueError("render_html was given no window to render")
    issues = issues or {}
    titles = titles or {}
    # Whether a corpus was *offered*, kept before the line below collapses
    # None into an empty dict. The two are different facts and the notice
    # depends on telling them apart: a caller that passed nothing is rendering
    # without titles on purpose, and a caller that passed a corpus which came
    # back empty has a deployment that cannot see the content folders.
    corpus_offered = corpus is not None
    corpus = corpus or {}
    selected = selected or "all"

    # The merged view. Every slot carries its own lens, so nothing here has to
    # decide whose a post is; it only has to put them in one order.
    all_slots = [s for window in windows for s in window.slots]
    slots_by_date = {}
    for slot in all_slots:
        slots_by_date.setdefault(slot.date, []).append(slot)

    days = []
    seen = set()
    for window in windows:
        for day in slots.window_days(window):
            if day not in seen:
                seen.add(day)
                days.append(day)
    days.sort()

    all_issues = [issue for lens_issues in issues.values() for issue in lens_issues]

    # No date range under the title. "The dates right under that are
    # [un]necessary, so delete those" on the 2026-09-15 recording, and the
    # month grid below states the range in the only form anyone reads it in
    # anyway. What the header carries instead is the one fact the grid does
    # not: how much is on the page and across how many calendars.
    record_attr = f' data-record-url="{_esc(record_url)}"' if record_url else ""
    reschedule_attr = f' data-reschedule-url="{_esc(reschedule_url)}"' if reschedule_url else ""

    return f"""<!doctype html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<script>
// Dark unless this browser chose light, set before the page paints so it
// never flashes the wrong one. Dark is the brand's own ground (qofai.com);
// the toggle in the header remembers a reviewer who prefers light.
try {{
  var chosen = localStorage.getItem("tlcm.theme");
  if (chosen === "light" || chosen === "dark") document.documentElement.setAttribute("data-theme", chosen);
}} catch (error) {{ /* private window */ }}
</script>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Content calendar</title>
<style>
{_STYLE}
{_state_css()}
</style>
</head>
<body{record_attr}{reschedule_attr}>
<main>
<header>
  <div class="masthead">
    <div>
      <p class="kicker">Content calendar</p>
      <h1>{_esc(_grid_months(days) or "Content calendar")}</h1>
    </div>
    <div class="mastside">
      <p class="meta" id="meta"><span>{len(all_slots)} posts{_decided_summary(all_slots) and ", " + _decided_summary(all_slots)}</span></p>
      <button type="button" class="themetoggle" id="themetoggle" title="Switch between dark and light">Light</button>
    </div>
  </div>
  {_glance(all_slots, taxonomy)}
  {_view_tabs()}
</header>
<div class="view" id="view-calendar">
<div class="calwork" id="calwork">
<div class="calmain">
{_corpus_notice(all_slots, corpus, corpus_offered)}
{_toolbar(windows, taxonomy, selected, all_slots, reviewer)}
{_offgrid_notice(days, slots_by_date, titles, taxonomy)}
<section class="month" id="monthsection">
  {_grid(taxonomy, today, days, slots_by_date, titles)}
</section>
{_example_notice(agent_decided)}
{_arcs(windows, taxonomy)}
{_timeline(all_slots, taxonomy, today, days, slots_by_date, titles, corpus, links, _engagement_by_slot(links, history))}
{_findings(all_issues)}
{_provenance(windows, taxonomy, today, window_source, tags_path, refs, titles)}
</div>
{_detail_panel(reviewer)}
</div>
</div>
<div class="view" id="view-history" hidden>
{_performance(history)}
</div>
</main>
{_APP_SCRIPT}
</body>
</html>
"""


# Everything the page does, in one script. Rebuilt 2026-09-23 from four.
#
# Why one. Recording a decision used to reload the whole page, so every
# script could find its elements once at load and keep them. Now a decision,
# a swap or a saved order refreshes the page in place: it fetches the page
# again, from the server, and replaces only the parts that show what the
# store holds (the month, the hidden cards, the counts, the glance strip, the
# off-grid notice). Four scripts each holding on to elements from the first
# load would have been left talking to elements that no longer exist, so the
# parts that are replaced are always looked up fresh, and the parts that are
# not (the toolbar, the panel, the tabs) are the only ones held.
#
# What does not change with it. Nothing on the page is ever painted from a
# button: a badge, a tile's edge and a count are what the server rendered from
# the store, whether they arrive by reload or by this refresh. If the refresh
# fails for any reason the page falls back to a plain reload.
#
# What the page does, in order below: theme, tabs, the lens filter, who is
# reviewing, the panel, moving posts, deciding, the refresh, swapping, clicks
# and keys, drag and drop, the hover preview, and the argument cards.
#
# Deliberately no dependency, no build step, and no network call beyond the
# page's own routes, because the page has to keep opening from disk on a
# laptop with nothing installed. A file opened from disk has nowhere to write,
# so there every decision ends at the clipboard as `approval/queue.py`
# commands, and the page says so.
#
# Raw string on purpose. The join separator in `commands` is a newline the
# *browser* must see as an escape, and in a normal Python string it becomes a
# real newline here, which ends the JavaScript string literal and takes the
# whole script down with a SyntaxError. The page still renders, so this
# fails silently: every control is drawn and none of them work.
_APP_SCRIPT = r"""<script>
(function () {
  var doc = document;
  function $(selector, root) { return (root || doc).querySelector(selector); }
  function $$(selector, root) {
    return Array.prototype.slice.call((root || doc).querySelectorAll(selector));
  }
  function stored(kind, key) {
    try { return window[kind].getItem(key); } catch (error) { return null; }
  }
  function store(kind, key, value) {
    try {
      if (value === null) window[kind].removeItem(key); else window[kind].setItem(key, value);
    } catch (error) { /* private window */ }
  }
  var reduced = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  var recordUrl = doc.body.getAttribute('data-record-url');
  var rescheduleUrl = doc.body.getAttribute('data-reschedule-url');
  var live = !!recordUrl;
  var liveReschedule = !!rescheduleUrl;

  // ---- theme ------------------------------------------------------------
  var themeButton = $('#themetoggle');
  function paintThemeButton() {
    if (!themeButton) return;
    var dark = doc.documentElement.getAttribute('data-theme') !== 'light';
    themeButton.textContent = dark ? 'Light' : 'Dark';
  }
  if (themeButton) {
    themeButton.addEventListener('click', function () {
      var next = doc.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
      doc.documentElement.setAttribute('data-theme', next);
      store('localStorage', 'tlcm.theme', next);
      paintThemeButton();
    });
  }
  paintThemeButton();

  // ---- the two views ----------------------------------------------------
  var tabs = $('#viewtabs');
  function showView(view) {
    $$('[data-view]', tabs).forEach(function (b) {
      b.setAttribute('aria-pressed', b.getAttribute('data-view') === view ? 'true' : 'false');
    });
    $('#view-calendar').hidden = view !== 'calendar';
    $('#view-history').hidden = view !== 'history';
    hidePeek();
  }
  if (tabs) {
    tabs.addEventListener('click', function (event) {
      var button = event.target.closest('[data-view]');
      if (!button) return;
      var view = button.getAttribute('data-view');
      showView(view);
      history.replaceState(null, '', view === 'history' ? '#history' : location.pathname + location.search);
    });
    if (location.hash === '#history') showView('history');
  }

  // ---- the lens filter ----------------------------------------------------
  // `data-select-lens` on the control, `data-lens` on the thing it selects.
  // They were both `data-lens` until 2026-09-15, which meant the sweep matched
  // the filter buttons as well as the posts: choosing Jordan hid the other
  // buttons and the filter became one-way. One attribute doing two jobs is
  // what made that possible, so they are two attributes.
  function currentLens() {
    var pressed = $('.lensbtn[aria-pressed="true"]');
    return pressed ? pressed.getAttribute('data-select-lens') : 'all';
  }
  function applyLens() {
    var wanted = currentLens();
    $$('[data-lens]').forEach(function (element) {
      element.hidden = !(wanted === 'all' || wanted === element.getAttribute('data-lens'));
    });
    doc.body.setAttribute('data-showing', wanted);
  }
  var lensbar = $('#lensbar');
  if (lensbar) {
    lensbar.addEventListener('click', function (event) {
      var button = event.target.closest('.lensbtn');
      if (!button) return;
      $$('.lensbtn', lensbar).forEach(function (other) {
        other.setAttribute('aria-pressed', other === button ? 'true' : 'false');
      });
      applyLens();
      steps();
    });
  }

  // ---- toolbar ------------------------------------------------------------
  var status = $('.reorderbar .status');
  var reset = $('.reorderbar [data-act="reset"]');
  var saveOrder = $('.reorderbar [data-act="save-order"]');
  var nosaveHint = $('.reorderbar .nosave-hint');
  var decideBar = $('.decidebar');
  var decideStatus = $('.decidebar .status');
  var copyDecisions = $('.decidebar [data-act="copy-decisions"]');
  var clearDecisions = $('.decidebar [data-act="clear-decisions"]');
  var whoField = $('.decidebar .who');

  if (!liveReschedule) {
    if (saveOrder) saveOrder.hidden = true;
    if (nosaveHint) nosaveHint.hidden = false;
  }
  // True of every served page, so it is a tooltip rather than a sentence on
  // screen. The disk page keeps its sentence, because there it is the one
  // thing a reviewer must not miss.
  var said = 'Approve and Decline record straight away. A decline needs a reason '
    + 'first. Correcting a decision records a newer one; nothing is overwritten.';
  if (live && decideBar) {
    $$('.staged', decideBar).forEach(function (button) { button.hidden = true; });
    var hint = $('.recordhint', decideBar);
    if (hint) hint.hidden = true;
    var chip = $('.whoami');
    if (chip) chip.title = said;
  }

  // ---- who is reviewing -------------------------------------------------
  // Asked once, by name, and remembered in this browser (2026-09-23). The
  // hidden field is still the record every other part reads, and it is the
  // seam for the portal: a signed-in name arrives in it already filled and
  // the picker never opens. Q4 decided identity is recorded, not verified.
  var WHO_KEY = 'tlcm.reviewer';
  var modal = $('#whomodal');
  var afterWho = null;

  function reviewerName() {
    var typed = whoField ? (whoField.value || '').trim() : '';
    return typed || (stored('localStorage', WHO_KEY) || '').trim();
  }
  function paintWho() {
    var name = reviewerName();
    var chip = $('.whoami');
    if (!chip) return;
    chip.hidden = !name;
    var shown = $('.whoname', chip);
    if (shown) shown.textContent = name;
  }
  function setReviewer(name) {
    name = (name || '').trim();
    if (!name) return;
    if (whoField) whoField.value = name;
    store('localStorage', WHO_KEY, name);
    paintWho();
  }
  function askWho(then) {
    if (!modal) return;
    afterWho = then || null;
    modal.hidden = false;
    var first = $('.whopick', modal);
    if (first) first.focus();
  }
  function closeWho() {
    if (modal) modal.hidden = true;
    var then = afterWho;
    afterWho = null;
    if (then && reviewerName()) then();
  }
  if (modal) {
    modal.addEventListener('click', function (event) {
      var pick = event.target.closest('.whopick');
      if (pick) { setReviewer(pick.getAttribute('data-name')); closeWho(); return; }
      if (event.target === modal) { afterWho = null; closeWho(); }
    });
    var other = $('.whoother', modal);
    if (other) {
      other.addEventListener('submit', function (event) {
        event.preventDefault();
        var field = $('input', other);
        if (field && field.value.trim()) { setReviewer(field.value); closeWho(); }
      });
    }
  }
  if (whoField && !whoField.value) whoField.value = (stored('localStorage', WHO_KEY) || '').trim();
  paintWho();

  // ---- the panel ----------------------------------------------------------
  // A copy of the post's card, beside the month. The copy is a view and never
  // a second card: it loses its id and its `data-lens`, so nothing that looks
  // cards up, and not the filter, can mistake it for the real one, and every
  // button in it acts on the real card through `data-slot`. The open post
  // survives a reload through sessionStorage.
  var panel = $('#detail');
  var work = $('#calwork');
  var panelBody = panel && $('.detailbody', panel);
  var panelWhen = panel && $('.detaildate', panel);
  var panelStatus = panel && $('.detailstatus', panel);
  var panelPos = panel && $('.detailpos', panel);
  var prevButton = panel && $('.detailstep[data-step="-1"]', panel);
  var nextButton = panel && $('.detailstep[data-step="1"]', panel);
  var OPEN_KEY = 'tlcm.selected';
  var current = null;

  function cards() { return $$('.daycards > .card'); }
  function realCard(slot) { return slot ? doc.getElementById('slot-' + slot) : null; }
  function tileFor(slot) { return slot ? $('.mini[data-slot="' + slot + '"]') : null; }
  function panelCard() { return panelBody ? $('.card', panelBody) : null; }
  function ownerOf(element) {
    var view = element && element.closest('.card');
    if (!view || !view.classList.contains('is-panel')) return view;
    return realCard(view.getAttribute('data-slot'));
  }
  // Every card that is on screen, the real one or the panel's copy, paired
  // with the real card it stands for.
  function views() {
    var list = cards().map(function (card) { return { view: card, card: card }; });
    var copy = panelCard();
    var owner = copy && ownerOf(copy);
    if (copy && owner) list.push({ view: copy, card: owner });
    return list;
  }
  function dayOf(card) {
    var row = card && card.closest('.dayrow');
    return row ? row.getAttribute('data-date') : '';
  }
  function dayLabelOf(card) {
    var row = card && card.closest('.dayrow');
    var line = row && $('.dayline', row);
    return line ? line.textContent.trim() : '';
  }
  // Every post in date order, skipping the ones the filter is hiding. The
  // hidden list is in date order and follows every move.
  function order() { return cards().filter(function (card) { return !card.hidden; }); }

  function steps() {
    if (!panel || !current) return;
    var list = order();
    var at = -1;
    list.forEach(function (card, index) { if (card.getAttribute('data-slot') === current) at = index; });
    panelPos.textContent = at < 0 ? '' : (at + 1) + ' of ' + list.length;
    if (prevButton) prevButton.disabled = at <= 0;
    if (nextButton) nextButton.disabled = at < 0 || at >= list.length - 1;
  }
  function step(by) {
    var list = order();
    for (var i = 0; i < list.length; i++) {
      if (list[i].getAttribute('data-slot') === current) {
        var to = list[i + by];
        if (to) open(to.getAttribute('data-slot'), { scroll: true });
        return;
      }
    }
  }
  function mark(slot) {
    $$('.mini.is-selected, .card.is-selected').forEach(function (el) { el.classList.remove('is-selected'); });
    if (!slot) return;
    var tile = tileFor(slot);
    if (tile) tile.classList.add('is-selected');
    var card = realCard(slot);
    if (card) card.classList.add('is-selected');
  }

  // Rebuilds the copy from the real card, keeping what the reviewer had open
  // and typed. Not called while somebody is typing, since a rebuild would
  // take their cursor away.
  function refreshPanel(entering) {
    if (!panel || !current) return;
    var card = realCard(current);
    if (!card) { close(); return; }
    var old = panelCard();
    var same = old && old.getAttribute('data-slot') === current;
    var opened = same ? $$('details', old).map(function (d) { return d.open; }) : [];
    var copy = card.cloneNode(true);
    copy.removeAttribute('id');
    copy.removeAttribute('data-lens');
    copy.hidden = false;
    copy.classList.add('is-panel');
    copy.classList.remove('is-selected');
    $$('details', copy).forEach(function (d, index) { d.open = !!opened[index]; });
    ['.notetext', '.linktext'].forEach(function (selector) {
      var typed = $(selector, card);
      var field = $(selector, copy);
      if (typed && field) field.value = typed.value;
    });
    // A swap needs the other post to be on this page. If it is not, the
    // button says so rather than doing nothing.
    $$('.swapbtn', copy).forEach(function (button) {
      var other = cardByRef(button.getAttribute('data-swap'), card);
      if (!other || !dayOf(card) || !dayOf(other)) {
        button.disabled = true;
        button.title = 'That post is not on this calendar, so there is nothing to swap with';
      }
    });
    if (entering && !reduced) copy.classList.add('is-entering');
    var keep = same ? panel.scrollTop : 0;
    panelBody.replaceChildren(copy);
    panel.scrollTop = keep;
    panelWhen.textContent = dayLabelOf(card);
    steps();
  }

  function open(slot, options) {
    if (!panel || !realCard(slot)) return;
    var changed = current !== slot;
    current = slot;
    store('sessionStorage', OPEN_KEY, slot);
    if (changed) panelStatus.textContent = '';
    refreshPanel(changed);
    panel.hidden = false;
    work.classList.add('has-detail');
    mark(slot);
    hidePeek();
    if (changed) panel.scrollTop = 0;
    // Stepping with Previous and Next keeps the month on the post as well,
    // so the tile it is reading is always on screen.
    // Centred when it is near an edge or off screen, rather than scrolled
    // just far enough, which left it flush against the bottom of the window.
    if (options && options.scroll) {
      var tile = tileFor(slot);
      var box = tile && tile.getBoundingClientRect();
      var margin = 96;
      if (box && (box.top < margin || box.bottom > window.innerHeight - margin)) {
        tile.scrollIntoView({ block: 'center', behavior: reduced ? 'auto' : 'smooth' });
      }
    }
  }
  function close() {
    if (!panel) return;
    current = null;
    store('sessionStorage', OPEN_KEY, null);
    panel.hidden = true;
    work.classList.remove('has-detail');
    panelBody.replaceChildren();
    panelStatus.textContent = '';
    mark(null);
  }

  // ---- moving posts ------------------------------------------------------
  // The card's day row is the truth about where a post sits. A move changes
  // the row, and this brings the month tile, the moved note and the toolbar
  // along. Every rationale was written for one post on one date, so a moved
  // card says so rather than carrying a reason that now argues for a date
  // it is not on.
  function sync() {
    var list = cards();
    list.forEach(function (card, index) {
      var home = card.getAttribute('data-home');
      var here = dayOf(card) || home;
      var moved = here !== home;
      card.classList.toggle('is-moved', moved);
      var note = $('.moved', card);
      if (note) {
        note.hidden = !moved;
        var from = $('.movedfrom', note);
        if (from) from.textContent = card.getAttribute('data-home-label') || home;
      }
      var tile = tileFor(card.getAttribute('data-slot'));
      if (tile) {
        var cell = $('.cell[data-date="' + here + '"]');
        if (cell && tile.parentNode !== cell) cell.appendChild(tile);
      }
      var up = $('.move[data-dir="up"]', card);
      var down = $('.move[data-dir="down"]', card);
      if (up) up.disabled = index === 0;
      if (down) down.disabled = index === list.length - 1;
    });
    var changed = list.filter(function (c) { return c.classList.contains('is-moved'); }).length;
    if (status) {
      status.textContent = changed
        ? changed + ' post' + (changed === 1 ? '' : 's') + ' moved from their saved dates.'
        : '';
    }
    if (reset) reset.disabled = changed === 0;
    if (saveOrder) saveOrder.disabled = changed === 0;
    syncDecisions();
    refreshPanel(false);
    mark(current);
  }

  function swapNodes(a, b) {
    var pa = a.parentNode;
    var marker = doc.createComment('');
    pa.insertBefore(marker, a);
    b.parentNode.insertBefore(a, b);
    pa.insertBefore(b, marker);
    pa.removeChild(marker);
  }

  // ---- deciding ----------------------------------------------------------
  var WANTS = {
    approved: 'approve', rejected: 'decline', draft: 'undo', published: 'mark published'
  };
  function linkOk(text) { return /^https?:\/\/\S+$/i.test(text || ''); }

  // Every staged decision, on the page that cannot write. A decline with no
  // reason, and a publish with no link, are deliberately not decisions:
  // `approval/queue.py` refuses both, so offering to copy a command it will
  // refuse would hand somebody a decision they think they made.
  function pending() {
    return cards().map(function (card) {
      var state = card.getAttribute('data-pending');
      if (!state) return null;
      var note = ($('.notetext', card) || {}).value || '';
      var link = ($('.linktext', card) || {}).value || '';
      note = note.trim();
      link = link.trim();
      return {
        card: card, slot: card.getAttribute('data-slot'), state: state, note: note, link: link,
        ready: state === 'rejected' ? note.length > 0 : state === 'published' ? linkOk(link) : true
      };
    }).filter(Boolean);
  }

  function syncDecisions() {
    var list = pending();
    views().forEach(function (pair) {
      var card = pair.view;
      var state = pair.card.getAttribute('data-pending') || '';
      card.classList.toggle('is-deciding', state === 'approved');
      card.classList.toggle('is-declining', state === 'rejected');
      $$('.decide', card).forEach(function (button) {
        button.classList.toggle('is-on', button.getAttribute('data-decide') === state);
      });
      // A served page stages nothing, so its rows are opened by `decideNow`
      // and must not be shut again by this.
      var rows = { '.declinerow': 'rejected', '.publishrow': 'published' };
      Object.keys(rows).forEach(function (selector) {
        var row = $(selector, card);
        if (!row) return;
        if (!live) row.hidden = state !== rows[selector];
        var field = $(selector === '.declinerow' ? '.notetext' : '.linktext', pair.card);
        var value = field ? field.value.trim() : '';
        var missing = state === rows[selector] && (selector === '.declinerow' ? !value : !linkOk(value));
        row.classList.toggle('is-missing', missing);
      });
      var chip = $('.pending', card);
      if (chip) {
        chip.hidden = !state;
        chip.textContent = (WANTS[state] || state) + ', not recorded';
      }
    });
    if (!decideStatus) return;
    var ready = list.filter(function (d) { return d.ready; }).length;
    var waiting = list.length - ready;
    if (live) {
      decideStatus.textContent = '';
    } else if (list.length) {
      var parts = [ready + ' decision' + (ready === 1 ? '' : 's') + ' ready to record'];
      if (waiting) parts.push(waiting + ' still need' + (waiting === 1 ? 's' : '') + ' a reason or a link');
      parts.push('none of it recorded');
      decideStatus.textContent = parts.join(', ') + '.';
    } else {
      decideStatus.textContent = '';
    }
    if (copyDecisions) copyDecisions.disabled = ready === 0;
    if (clearDecisions) clearDecisions.disabled = list.length === 0;
  }

  function setDecideStatus(message) {
    if (decideStatus) decideStatus.textContent = message;
    if (panelStatus) panelStatus.textContent = message;
  }

  // Opens a row that asks for something (a reason, a link) in the real card
  // and in the copy, and puts the cursor in the one the reviewer is using.
  function ask(card, view, selector, field) {
    [card, view].forEach(function (each) {
      var row = $(selector, each);
      if (row) { row.hidden = false; row.classList.add('is-missing'); }
    });
    var here = $(field, view);
    if (here) here.focus();
  }

  // One click, one decision, one write. Served pages only.
  //
  // What it does not do is decide anything the CLI would refuse. The rules
  // live in `approval/queue.py` and the route calls it, so a decline with no
  // reason, or a publish with no link, fails there whatever this does. Asking
  // first is a courtesy that saves a round trip, never a second copy of the
  // rule.
  function decideNow(card, want, button) {
    if (!card || button.disabled) return;
    var view = button.closest('.card') || card;
    var who = reviewerName();
    if (!who) {
      setDecideStatus('Say who is reviewing first.');
      askWho(function () { decideNow(card, want, button); });
      return;
    }
    store('localStorage', WHO_KEY, who);

    var note = '';
    var link = '';
    if (want === 'rejected') {
      note = (($('.notetext', card) || {}).value || '').trim();
      if (!note) {
        ask(card, view, '.declinerow', '.notetext');
        setDecideStatus('A decline needs a reason. Type it, then press Decline again.');
        return;
      }
    }
    if (want === 'published') {
      link = (($('.linktext', card) || {}).value || '').trim();
      if (!linkOk(link)) {
        ask(card, view, '.publishrow', '.linktext');
        setDecideStatus('Paste the link to the post as it went out, then press Mark published again.');
        return;
      }
    }

    var buttons = $$('.decide', card).concat(view !== card ? $$('.decide', view) : []);
    buttons.forEach(function (each) { each.disabled = true; });
    setDecideStatus('Recording...');

    fetch(recordUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        by: who,
        decisions: [{ slot_id: card.getAttribute('data-slot'), state: want, note: note, url: link }]
      })
    }).then(function (response) {
      return response.json().then(function (body) { return { ok: response.ok, body: body }; });
    }).then(function (result) {
      if (!result.ok || !result.body || result.body.ok !== true) {
        buttons.forEach(function (each) { each.disabled = false; });
        setDecideStatus('Not recorded: ' + ((result.body && result.body.detail) || 'refused'));
        return;
      }
      return refreshFromServer().then(function () { setDecideStatus(''); });
    }).catch(function (error) {
      buttons.forEach(function (each) { each.disabled = false; });
      setDecideStatus('Not recorded: ' + error);
    });
  }

  // Shell-quote for the copied command. The note is free text a person typed,
  // and an apostrophe in it would otherwise end the quoted argument and hand
  // somebody a command that fails, or one that runs differently from what the
  // page showed them.
  function quoted(text) {
    return "'" + String(text).replace(/'/g, "'\\''") + "'";
  }
  var FLAGS = { approved: '--approve', rejected: '--reject', draft: '--unapprove', published: '--publish' };
  function commands() {
    var who = reviewerName() || 'YOUR-NAME';
    var lines = ['cd approval'];
    pending().filter(function (d) { return d.ready; }).forEach(function (d) {
      var line = '../.venv/bin/python3 queue.py ' + FLAGS[d.state] + ' ' + d.slot + ' --by ' + quoted(who);
      if (d.state === 'rejected') line += ' --note ' + quoted(d.note);
      if (d.state === 'published') line += ' --url ' + quoted(d.link);
      lines.push(line);
    });
    return lines.join('\n');
  }
  function toClipboard(button, text, label) {
    var done = function () {
      var was = button.textContent;
      button.textContent = 'Copied';
      setTimeout(function () { button.textContent = was; }, 1500);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () { window.prompt(label, text); });
    } else {
      window.prompt(label, text);
    }
  }

  // ---- the in-place refresh ---------------------------------------------
  var REFRESHED = ['#meta', '#glance', '#monthsection', '#posts', '#offgridslot'];
  function refreshFromServer() {
    var before = {};
    $$('.mini').forEach(function (tile) { before[tile.getAttribute('data-slot')] = tile.getAttribute('data-state'); });
    return fetch(location.pathname + location.search, { cache: 'no-store', credentials: 'same-origin' })
      .then(function (response) {
        if (!response.ok) throw new Error('the page answered ' + response.status);
        return response.text();
      })
      .then(function (text) {
        var fresh = new DOMParser().parseFromString(text, 'text/html');
        REFRESHED.forEach(function (selector) {
          var mine = $(selector);
          var theirs = fresh.querySelector(selector);
          if (mine && theirs) mine.replaceWith(doc.importNode(theirs, true));
        });
        applyLens();
        sync();
        // A tile whose state just changed says so for a moment.
        $$('.mini').forEach(function (tile) {
          var was = before[tile.getAttribute('data-slot')];
          if (was && was !== tile.getAttribute('data-state') && !reduced) {
            tile.classList.add('is-changed');
            setTimeout(function () { tile.classList.remove('is-changed'); }, 1400);
          }
        });
      })
      .catch(function () { location.reload(); });
  }

  // ---- swapping ------------------------------------------------------------
  // Swap trades two posts' dates (2026-09-23). Every alternative is already
  // scheduled elsewhere on the same calendar, so a swap is two moves and the
  // reschedule store already records moves. Each post keeps its own approval,
  // because an approval belongs to the slot and the slot moves with its post.
  function cardByRef(ref, not) {
    return cards().filter(function (c) { return c !== not && c.getAttribute('data-ref') === ref; })[0] || null;
  }
  function swapWith(button) {
    var card = ownerOf(button);
    var other = card && cardByRef(button.getAttribute('data-swap'), card);
    if (!card || !other || button.disabled) return;
    var mine = dayOf(card);
    var theirs = dayOf(other);
    if (!mine || !theirs) return;
    if (!liveReschedule) { swapNodes(card, other); sync(); return; }
    var who = reviewerName();
    if (!who) { askWho(function () { swapWith(button); }); return; }
    button.disabled = true;
    setDecideStatus('Swapping...');
    fetch(rescheduleUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ by: who, moves: [
        { slot_id: card.getAttribute('data-slot'), new_date: theirs },
        { slot_id: other.getAttribute('data-slot'), new_date: mine }
      ] })
    }).then(function (response) {
      return response.json().then(function (body) { return { ok: response.ok, body: body }; });
    }).then(function (result) {
      if (!result.ok || !result.body || result.body.ok !== true) {
        button.disabled = false;
        setDecideStatus('Not swapped: ' + ((result.body && result.body.detail) || 'refused'));
        return;
      }
      return refreshFromServer().then(function () { setDecideStatus(''); });
    }).catch(function (error) {
      button.disabled = false;
      setDecideStatus('Not swapped: ' + error);
    });
  }

  function saveTheOrder(button) {
    if (!liveReschedule || button.disabled) return;
    var moved = cards().filter(function (c) { return c.classList.contains('is-moved'); });
    if (!moved.length) return;
    var who = reviewerName();
    if (!who) { askWho(function () { saveTheOrder(button); }); return; }
    button.disabled = true;
    button.textContent = 'Saving...';
    fetch(rescheduleUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ by: who, moves: moved.map(function (c) {
        return { slot_id: c.getAttribute('data-slot'), new_date: dayOf(c) || c.getAttribute('data-home') };
      }) })
    }).then(function (response) {
      return response.json().then(function (body) { return { ok: response.ok, body: body }; });
    }).then(function (result) {
      button.textContent = 'Save order';
      if (!result.ok || !result.body || result.body.ok !== true) {
        button.disabled = false;
        if (status) status.textContent = 'Not saved: ' + ((result.body && result.body.detail) || 'refused');
        return;
      }
      return refreshFromServer();
    }).catch(function (error) {
      button.disabled = false;
      button.textContent = 'Save order';
      if (status) status.textContent = 'Not saved: ' + error;
    });
  }

  // ---- clicks, typing and keys -------------------------------------------
  doc.addEventListener('click', function (event) {
    var target = event.target;
    if (target.closest('.detailclose')) { close(); return; }
    var stepper = target.closest('.detailstep');
    if (stepper) { step(Number(stepper.getAttribute('data-step'))); return; }
    if (target.closest('.whochange')) { askWho(null); return; }
    var more = target.closest('.arcmore');
    if (more) {
      var arc = more.closest('.arccard');
      var isOpen = arc.classList.toggle('is-open');
      more.textContent = isOpen ? 'Less' : 'More';
      return;
    }
    var opener = target.closest('.mini, [data-open-slot]');
    if (opener) {
      open(opener.getAttribute('data-slot') || opener.getAttribute('data-open-slot'));
      return;
    }
    var swapButton = target.closest('.swapbtn');
    if (swapButton) { swapWith(swapButton); return; }
    var mover = target.closest('.move');
    if (mover) {
      var card = ownerOf(mover);
      var list = cards();
      var neighbour = list[list.indexOf(card) + (mover.getAttribute('data-dir') === 'up' ? -1 : 1)];
      if (card && neighbour) { swapNodes(card, neighbour); sync(); }
      return;
    }
    var decide = target.closest('.decide');
    if (decide) {
      var owner = ownerOf(decide);
      if (!owner) return;
      var want = decide.getAttribute('data-decide');
      if (live) { decideNow(owner, want, decide); return; }
      owner.setAttribute('data-pending', owner.getAttribute('data-pending') === want ? '' : want);
      syncDecisions();
      var fieldFor = { rejected: '.notetext', published: '.linktext' }[owner.getAttribute('data-pending')];
      var field = fieldFor && $(fieldFor, decide.closest('.card'));
      if (field) field.focus();
      return;
    }
    var act = target.closest('[data-act]');
    if (!act) return;
    var action = act.getAttribute('data-act');
    if (action === 'reset') {
      if (live) refreshFromServer(); else location.reload();
      return;
    }
    if (action === 'save-order') { saveTheOrder(act); return; }
    if (action === 'clear-decisions') {
      cards().forEach(function (c) {
        c.removeAttribute('data-pending');
        ['.notetext', '.linktext'].forEach(function (selector) {
          var f = $(selector, c);
          if (f) f.value = '';
        });
      });
      syncDecisions();
      refreshPanel(false);
      return;
    }
    if (action === 'copy-decisions') toClipboard(act, commands(), 'Run these to record the decisions:');
  });

  // A reason or a link typed in the panel lands in the real card, which is
  // the one every decision is read from.
  doc.addEventListener('input', function (event) {
    var target = event.target;
    var field = target.closest('.is-panel .notetext, .is-panel .linktext');
    if (field) {
      var owner = ownerOf(field);
      var real = owner && $(field.classList.contains('notetext') ? '.notetext' : '.linktext', owner);
      if (real) real.value = field.value;
    } else if (!target.closest('.notetext, .linktext')) {
      return;
    }
    syncDecisions();
  });

  doc.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') {
      if (modal && !modal.hidden) { afterWho = null; closeWho(); return; }
      if (current) { close(); return; }
    }
    var typing = event.target.closest && event.target.closest('input, textarea, select');
    if (current && !typing && (event.key === 'ArrowLeft' || event.key === 'ArrowRight')) {
      event.preventDefault();
      step(event.key === 'ArrowLeft' ? -1 : 1);
      return;
    }
    var tile = event.target.closest && event.target.closest('.mini');
    if (tile && (event.key === 'Enter' || event.key === ' ')) {
      event.preventDefault();
      open(tile.getAttribute('data-slot'));
    }
  });

  // ---- drag and drop, inside the month ------------------------------------
  // Antonio, 2026-09-15: "I wanted to drag on the calendar itself ... I don't
  // want to start the drag below." The tile is the drag source and a cell is
  // the target. What moves underneath is still the card, because its day row
  // is what `sync` treats as the truth, and `sync` brings the tile along.
  // A drop onto an occupied date moves rather than swaps: with everyone on
  // one calendar a date holding two or three posts is the normal case.
  var dragging = null;
  function clearDropMarks() {
    $$('.is-drop').forEach(function (element) { element.classList.remove('is-drop'); });
  }
  doc.addEventListener('dragstart', function (event) {
    var tile = event.target.closest && event.target.closest('.mini');
    if (!tile) return;
    hidePeek();
    dragging = realCard(tile.getAttribute('data-slot'));
    if (!dragging) return;
    tile.classList.add('is-dragging');
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = 'move';
      // Some browsers refuse to start a drag with no payload set.
      event.dataTransfer.setData('text/plain', tile.getAttribute('data-slot') || '');
    }
  });
  doc.addEventListener('dragend', function () {
    $$('.mini.is-dragging').forEach(function (tile) { tile.classList.remove('is-dragging'); });
    dragging = null;
    clearDropMarks();
  });
  doc.addEventListener('dragover', function (event) {
    if (!dragging) return;
    var cell = event.target.closest && event.target.closest('.cell[data-date]');
    if (!cell) return;
    // Without this the browser treats the drop as unwanted and animates the
    // tile back, which reads as the calendar refusing the move.
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
    if (!cell.classList.contains('is-drop')) { clearDropMarks(); cell.classList.add('is-drop'); }
  });
  doc.addEventListener('dragleave', function (event) {
    var cell = event.target.closest && event.target.closest('.cell[data-date]');
    if (cell && !cell.contains(event.relatedTarget)) cell.classList.remove('is-drop');
  });
  doc.addEventListener('drop', function (event) {
    if (!dragging) return;
    var cell = event.target.closest && event.target.closest('.cell[data-date]');
    if (!cell) return;
    event.preventDefault();
    clearDropMarks();
    var row = $('.dayrow[data-date="' + cell.getAttribute('data-date') + '"]');
    var home = row && $('.daycards', row);
    var card = dragging;
    dragging = null;
    $$('.mini.is-dragging').forEach(function (tile) { tile.classList.remove('is-dragging'); });
    if (!home || card.parentNode === home) return;
    home.appendChild(card);
    sync();
  });

  // ---- the hover preview ---------------------------------------------------
  // A moment's hover over a tile shows whose post it is, its date and what it
  // argues, without opening anything (2026-09-23). A click still opens the
  // panel, which is where anything is decided. Not shown for the post the
  // panel already has open.
  var peek = doc.createElement('div');
  peek.className = 'peek';
  peek.hidden = true;
  peek.setAttribute('role', 'tooltip');
  doc.body.appendChild(peek);
  var peekTimer = null;
  var peekFor = null;
  function hidePeek() {
    clearTimeout(peekTimer);
    peek.hidden = true;
    peekFor = null;
  }
  function showPeek(tile) {
    var slot = tile.getAttribute('data-slot');
    var card = realCard(slot);
    if (!card || slot === current || dragging) return;
    var summary = $('.summary', card);
    var person = $('.ref .lenstag', card);
    peek.replaceChildren();
    var top = doc.createElement('p');
    top.className = 'peekmeta';
    top.textContent = (person ? person.textContent + ', ' : '') + dayLabelOf(card);
    var title = doc.createElement('p');
    title.className = 'peektitle';
    title.textContent = card.getAttribute('data-title') || '';
    peek.appendChild(top);
    peek.appendChild(title);
    if (summary && !summary.classList.contains('muted')) {
      var text = doc.createElement('p');
      text.className = 'peektext';
      text.textContent = summary.textContent;
      peek.appendChild(text);
    }
    peek.hidden = false;
    var rect = tile.getBoundingClientRect();
    var width = peek.offsetWidth;
    var left = rect.right + 10;
    if (left + width > window.innerWidth - 12) left = Math.max(12, rect.left - width - 10);
    var topAt = Math.min(rect.top, window.innerHeight - peek.offsetHeight - 12);
    peek.style.left = left + 'px';
    peek.style.top = Math.max(12, topAt) + 'px';
    peekFor = tile;
  }
  doc.addEventListener('mouseover', function (event) {
    var tile = event.target.closest && event.target.closest('.mini');
    if (!tile || tile === peekFor) return;
    clearTimeout(peekTimer);
    peekTimer = setTimeout(function () { showPeek(tile); }, 350);
  });
  doc.addEventListener('mouseout', function (event) {
    var tile = event.target.closest && event.target.closest('.mini');
    if (!tile || (event.relatedTarget && tile.contains(event.relatedTarget))) return;
    hidePeek();
  });
  window.addEventListener('scroll', hidePeek, { passive: true });
  doc.addEventListener('mousedown', hidePeek);

  // ---- the argument cards ---------------------------------------------------
  // Two lines each, with More where there is more (2026-09-23): three
  // paragraphs side by side were the densest text on the page.
  $$('.arccard').forEach(function (arc) {
    var text = $('.arctext', arc);
    var more = $('.arcmore', arc);
    if (text && more && text.scrollHeight > text.clientHeight + 1) more.hidden = false;
  });

  // ---- start ---------------------------------------------------------------
  sync();
  var saved = stored('sessionStorage', OPEN_KEY);
  if (saved) open(saved);
  // Asked on the first visit, before anything is decided. Not asked again in
  // this browser, and never when a signed-in name arrived with the page.
  if (!reviewerName()) askWho(null);
})();
</script>"""


# ---------------------------------------------------------------------
# Runnable entry point
# ---------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Renders a calendar window as one self-contained HTML page."
    )
    parser.add_argument("--window", default=None,
                        help="a serialized window file; defaults to the newest assembled "
                             "window in state/, and to the in-memory example only if there "
                             "is none")
    parser.add_argument("--example", action="store_true",
                        help="render the hand-built example window even if a real one exists")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="where to write the page")
    parser.add_argument("--threads", default=str(slots.DEFAULT_TAXONOMY_PATH), help="path to threads.yaml")
    parser.add_argument("--tags", default=str(slots.DEFAULT_TAGS_PATH), help="path to corpus_tags.yaml")
    parser.add_argument("--today", default=None, help="reference date (YYYY-MM-DD), default today UTC")
    parser.add_argument("--lens", default=None,
                        help="which calendar to render; default is the v1 lens in threads.yaml")
    parser.add_argument("--atomizer", default=str(DEFAULT_ATOMIZER), help="Content Atomizer output folder")
    parser.add_argument("--vcb", default=str(DEFAULT_VCB), help="Value Creation Briefing drafts folder")
    parser.add_argument("--no-titles", action="store_true",
                        help="skip title resolution and show references only")
    parser.add_argument("--state-root", default=None,
                        help="overlay approval state recorded in this state store")
    args = parser.parse_args(argv)

    taxonomy = slots.load_taxonomy(args.threads)
    today = slots._as_date(args.today) or datetime.now(timezone.utc).date()
    if args.today and slots._as_date(args.today) is None:
        _warn(f"{args.today!r} is not an ISO date; using {today.isoformat()}.")

    # Until 2026-09-07 there was no real window to render, so the example was
    # the only thing this could show and it was the default. Now that synthesis
    # writes one, defaulting to the example would quietly render a
    # hand-constructed calendar next to a real one and label it only in the
    # provenance footer. Prefer the real window; keep --example to ask for the
    # other on purpose.
    # Which calendars this page carries. `--window` names one file and is the
    # single-window path; otherwise every lens the taxonomy declares live gets
    # looked up, and the ones with a window on disk are the ones rendered. A
    # lens with no window is skipped with a warning rather than rendered
    # empty, because an empty calendar and a calendar nobody has assembled
    # yet are different things and the page cannot tell them apart.
    windows = []
    sources = []
    if args.window:
        window = slots.read_window(args.window)
        if window is None:
            _warn("no window to render, so no page was written.")
            return 0
        windows = [window]
        sources = [f"read from {_rel(args.window)}"]
    elif not args.example:
        wanted = [args.lens] if args.lens else list(taxonomy.v1_lens_order)
        if not wanted:
            _warn(f"{args.threads} declares no v1 lens; pass --lens or --window.")
        for lens in wanted:
            paths = _live_assembled_windows(lens, today)
            if not paths:
                _warn(f"no assembled window for the {lens} lens covers "
                      f"{today.isoformat()} or later; skipping it.")
                continue
            for path in paths:
                window = slots.read_window(path)
                if window is not None:
                    windows.append(window)
                    sources.append(f"read from {_rel(path)}")

    if not windows:
        sys.path.insert(0, str(_AGENT_ROOT / "calendar_model"))
        import example_window

        windows = [example_window.build(taxonomy, today, args.lens)]
        sources = [
            "built in memory by calendar_model/example_window.py, a hand-constructed "
            "example rather than an agent's output"
        ]
        agent_decided = False
    else:
        agent_decided = True

    links = {}
    if args.state_root:
        windows = [apply_stored_approvals(w, args.state_root) for w in windows]
        links = published_links(args.state_root)

    corpus = {} if args.no_titles else resolve_corpus(args.atomizer, args.vcb)
    titles = {ref: item.title for ref, item in corpus.items() if item.title}
    refs = slots.known_item_refs(args.tags)
    issues = {
        w.window_id: slots.validate_window(w, taxonomy, refs, today=today) for w in windows
    }

    page = render_html(
        windows,
        taxonomy,
        today=today,
        issues=issues,
        titles=titles,
        window_source="; ".join(sources),
        tags_path=str(args.tags),
        refs=refs,
        corpus=corpus,
        agent_decided=agent_decided,
        selected=args.lens or "",
        links=links,
    )

    out = Path(args.out)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(page, encoding="utf-8")
    except OSError as exc:
        _warn(f"could not write {out} ({exc}); nothing was rendered.")
        return 0

    print(f"wrote {out}  ({len(page):,} bytes, self-contained)")
    for window in windows:
        found = issues.get(window.window_id, [])
        print(f"  window: {window.window_id}, lens {window.lens}, "
              f"{window.start_date} to {window.end_date}, {len(window.slots)} slots")
        print(f"    dates carrying a slot: {len({s.date for s in window.slots})}, "
              f"days in the stated range: {len(slots.window_days(window))}")
        print(f"    validation: {len(slots.errors(found))} error(s), "
              f"{len(slots.warnings(found))} warning(s)")
        # Printed in full here because the page no longer lists them. A
        # warning off the page has to still be somewhere a person will see it.
        for issue in found:
            print(f"      {issue}")
    print(f"  reference date {today.isoformat()}, horizons approved "
          f"{taxonomy.approved_horizon_days}, draft {taxonomy.draft_horizon_days}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
