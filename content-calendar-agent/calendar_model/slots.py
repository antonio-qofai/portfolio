"""The calendar data model: a slot, a window, and what makes either valid.

Build order item 8 in PRD.md Section 5, the half that describes shape. The
other half, the store that accumulates daily runs, is state_store.py
alongside this file.

A slot is one dated position in the rolling 30-day window: a date, a
reference to a piece of content, the threads and form it carries, the lens
it is written for, its approval state, what it responds to, and a written
reason it sits on that date rather than another. A window is a lens, a date
range, a stated narrative arc, and the slots inside it.

What this module does not do
----------------------------
It does not decide what goes where. Synthesis and sequencing are build order
item 9 and scoring is item 10; both consume this schema and neither lives
here. This module holds the shape, the validation, and the serialization,
so that the hard parts have something to be wrong about in a checkable way.

References, never copies
-----------------------
A slot points at content by id and never carries its text. Alex and Robin
keep writing, so a copy forks their work and goes stale the same day, and
the repo rules forbid duplicating another team's content into this folder.
narrative/corpus_map.py already resolves these ids by reading the sibling
folders live through the ingestion readers, and that is the resolution path.

The id convention is the one already in narrative/corpus_tags.yaml:
`<source-agent>/<item-id>`, for example
`content-atomizer/anchor-01-the-consultant-with-leverage`. Published posts,
which cannot be scheduled but can be responded to, use the form threads.yaml
already uses in its evidence lists, for example
`sources/jordan-published-linkedin-posts.md#post-1`. There is no second
scheme here on purpose.

Why `responds_to` exists
------------------------
`content-atomizer/anchor-01-the-consultant-with-leverage` opens by answering
an already-published post, which makes it a sequel: scheduling it before the
thing it answers publishes a reply to nothing. Nothing else in the system
records ordering dependencies, and threads.yaml records the gap as a
tension. The field is a list because an item may answer more than one thing
and most answer none. Validation checks the ordering it implies, since a
dependency nobody checks is a comment.

Why the lens field is here in v1
--------------------------------
v1 builds the Jordan calendar only (PRD Section 2). Carrying the field now
means the three-calendar version is a config change rather than a schema
migration. The vocabulary comes from the `lenses:` block in threads.yaml,
not from this file.

Nothing editorial is hardcoded
------------------------------
Thread ids, form ids, lens ids, and the approval horizons are all read from
narrative/threads.yaml at runtime. The taxonomy is at 32 threads and is
about to be consolidated to roughly five to seven once Jordan weighs in, so
anything embedding today's ids breaks the week it happens. The two horizons
are read from the `sequencing:` block: this module never states how many
days are approved or how long the window runs.

The one vocabulary that is in Python is the set of approval states, because
those are a state machine this code reasons about rather than an editorial
choice. Four states, each earning its place:

    draft      staged by the agent, no human has looked at it
    approved   a human approved it; the only state that may publish
    rejected   a human said no. An append-only store has no update call, so
               reversing an approval means appending a newer state, and
               without a negative state an approval can never be withdrawn.
    published  it went out. C2 never sets this itself, since publishing is a
               manual human step, but the trailing-30-days half of the arc
               needs somewhere to record that it happened.

Degradation follows this folder's convention, set by conference_reader.py: a
missing or unreadable taxonomy, a malformed window file, or an unparseable
slot produces a warning on stderr and a degraded result, never an exception.
A daily run must not die because a config file moved. Validation returns its
findings as data rather than raising, for the same reason.

Round trip is lossless: keys this schema does not know about survive a read
and are written back out, so a hand-edited window keeps whatever a human
added to it. Verified by test_calendar_model.py.

Not one post per day: slots are dated entries, not a grid. Several may share
a date and most dates in a real window will have none, because cadence is an
open question for Jordan (PRD Section 7) and a schema that assumed an answer
would have to be rewritten when he gives one.

Display reads through this module too. `window_days` and the
human-readable labels on Taxonomy were added for build order item 14 so the
renderer asks the schema where the range and the gaps are, rather than
recomputing them from a horizon it read itself. They are queries about a
window, so they belong next to the window. `approval_boundary` was there too
until 2026-09-19, when the deadline it drew was removed.

Verified 2026-08-18 against narrative/threads.yaml as it then stood: 32
threads, 7 forms, 4 lenses of which 3 are calendars, horizons 14 and 30.
Label lookup, the range walk, and the boundary date re-verified the same
day against that file and against a temporary taxonomy with different
horizons.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import MISSING as _MISSING
from dataclasses import dataclass, field, fields
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    import yaml
except ImportError:  # pragma: no cover - reported, not raised
    yaml = None

_HERE = Path(__file__).resolve().parent
_AGENT_ROOT = _HERE.parent

DEFAULT_TAXONOMY_PATH = _AGENT_ROOT / "narrative" / "threads.yaml"
DEFAULT_TAGS_PATH = _AGENT_ROOT / "narrative" / "corpus_tags.yaml"
DEFAULT_STATE_DIR = _AGENT_ROOT / "state"

# The approval state machine. See the module docstring for why this is the
# one vocabulary that lives in Python rather than in configuration.
DRAFT = "draft"
APPROVED = "approved"
REJECTED = "rejected"
PUBLISHED = "published"
APPROVAL_STATES = (DRAFT, APPROVED, REJECTED, PUBLISHED)

# States that satisfy a requirement to be at least staged.
_AT_LEAST_DRAFT = (DRAFT, APPROVED, PUBLISHED)
_AT_LEAST_APPROVED = (APPROVED, PUBLISHED)

_REF_SEPARATOR = "/"


def _warn(message: str) -> None:
    print(f"slots: {message}", file=sys.stderr)


# ---------------------------------------------------------------------
# Taxonomy: the runtime read of narrative/threads.yaml
# ---------------------------------------------------------------------

@dataclass
class Taxonomy:
    """What threads.yaml says, read at runtime rather than embedded here.

    `ok` is False when the file could not be read. A caller may still
    validate against a not-ok taxonomy: thread, form, and lens checks are
    skipped rather than failing everything, because a missing config file
    is our problem and should not be reported as 32 invalid slots.
    """

    thread_ids: frozenset = frozenset()
    form_ids: frozenset = frozenset()
    lens_ids: frozenset = frozenset()
    calendar_lens_ids: frozenset = frozenset()
    v1_lens_ids: frozenset = frozenset()
    # The same v1 lenses, in the order threads.yaml declares them. A set
    # cannot answer "which calendar opens first", and sorting the ids answers
    # it alphabetically, which puts Blake's three-post calendar in front of
    # Jordan's full one. The order in the config is an editorial decision
    # someone made; this is what carries it.
    v1_lens_order: tuple = ()
    approved_horizon_days: int | None = None
    draft_horizon_days: int | None = None
    source_path: str = ""
    ok: bool = False
    # Human-readable labels for the ids above, for anything that shows a
    # window to a person rather than checking it. Empty when the taxonomy
    # states none, and every reader must fall back to the id, since a
    # missing label is a display question and never a validation failure.
    thread_names: dict = field(default_factory=dict)
    form_descriptions: dict = field(default_factory=dict)
    form_names: dict = field(default_factory=dict)
    lens_names: dict = field(default_factory=dict)
    # Whose posts a lens holds, as distinct from the audience it argues to.
    # Falls back to the lens label and then to the id, because a missing
    # person is a display question and never a validation failure.
    lens_people: dict = field(default_factory=dict)

    def thread_label(self, thread_id: str) -> str:
        return self.thread_names.get(thread_id) or thread_id

    def form_label(self, form_id: str) -> str:
        return self.form_names.get(form_id) or form_id

    def lens_label(self, lens_id: str) -> str:
        return self.lens_names.get(lens_id) or lens_id

    def lens_person(self, lens_id: str) -> str:
        return self.lens_people.get(lens_id) or self.lens_label(lens_id)


def load_taxonomy(path: str | Path = DEFAULT_TAXONOMY_PATH) -> Taxonomy:
    """Reads the thread, form, and lens vocabularies plus the horizons.

    Never raises. A missing file, unparseable YAML, or a document that is
    not a mapping each warn and return an empty, not-ok Taxonomy.
    """
    path = Path(path)
    if yaml is None:
        _warn("pyyaml is not installed; cannot read the taxonomy.")
        return Taxonomy(source_path=str(path))
    if not path.is_file():
        _warn(f"taxonomy not found at {path}; thread and form ids cannot be checked.")
        return Taxonomy(source_path=str(path))
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        _warn(f"could not parse taxonomy at {path} ({exc}); ids cannot be checked.")
        return Taxonomy(source_path=str(path))
    if not isinstance(doc, dict):
        _warn(f"taxonomy at {path} is not a mapping; ids cannot be checked.")
        return Taxonomy(source_path=str(path))

    threads = _ids(doc.get("threads"))
    forms = _ids(doc.get("forms"))

    lens_entries = doc.get("lenses")
    if isinstance(lens_entries, list) and lens_entries:
        lens_ids = _ids(lens_entries)
        calendar_lenses = frozenset(
            str(e.get("id"))
            for e in lens_entries
            if isinstance(e, dict) and e.get("id") and e.get("calendar", True)
        )
        v1_order = tuple(
            str(e.get("id"))
            for e in lens_entries
            if isinstance(e, dict) and e.get("id") and e.get("v1")
        )
        v1_lenses = frozenset(v1_order)
    else:
        # Older taxonomies carried the lens vocabulary only as a per-thread
        # field. Deriving it keeps this readable against those, but every
        # value including the non-calendar aggregate reads as a calendar,
        # so say so rather than silently accepting a slot lensed "all".
        derived = frozenset(
            str(t.get("lens"))
            for t in (doc.get("threads") or [])
            if isinstance(t, dict) and t.get("lens")
        )
        if derived:
            _warn(
                f"{path} has no 'lenses:' block; deriving the vocabulary from "
                "the threads, which cannot tell a calendar lens from an "
                "aggregate tag."
            )
        lens_ids = derived
        calendar_lenses = derived
        v1_lenses = frozenset()
        v1_order = ()

    sequencing = doc.get("sequencing")
    if not isinstance(sequencing, dict):
        _warn(f"{path} has no 'sequencing:' block; approval horizons are unknown.")
        sequencing = {}

    return Taxonomy(
        thread_ids=threads,
        form_ids=forms,
        lens_ids=lens_ids,
        calendar_lens_ids=calendar_lenses,
        v1_lens_ids=v1_lenses,
        v1_lens_order=v1_order,
        approved_horizon_days=_horizon(sequencing, "approved_horizon_days", path),
        draft_horizon_days=_horizon(sequencing, "draft_horizon_days", path),
        source_path=str(path),
        ok=True,
        thread_names=_labels(doc.get("threads"), "name"),
        form_descriptions=_labels(doc.get("forms"), "description"),
        form_names=_labels(doc.get("forms"), "name"),
        lens_names=_labels(lens_entries, "name"),
        lens_people=_labels(lens_entries, "person"),
    )


def _ids(entries: Any) -> frozenset:
    if not isinstance(entries, list):
        return frozenset()
    return frozenset(
        str(e["id"]) for e in entries if isinstance(e, dict) and e.get("id")
    )


def _labels(entries: Any, key: str) -> dict:
    """id -> a human-readable label, for entries that carry one.

    Whitespace is collapsed because these come out of folded YAML blocks and
    arrive with the line breaks still in them. An entry with no label is
    left out rather than given a placeholder, so a caller falls back to the
    id and nothing invents a name that is not in the taxonomy.
    """
    if not isinstance(entries, list):
        return {}
    labels = {}
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            labels[str(entry["id"])] = " ".join(value.split())
    return labels


def _horizon(sequencing: dict, key: str, path: Path) -> int | None:
    """Reads one horizon. Returns None rather than a default on purpose.

    There is no sensible fallback here: guessing a horizon would put a
    number in this file, which is exactly what reading it from config is
    meant to avoid, and a wrong guess silently mislabels which slots a human
    still has to approve. Checks that need a horizon they do not have report
    that they could not run.
    """
    if key not in sequencing:
        _warn(f"{path} 'sequencing:' has no {key}; that horizon cannot be checked.")
        return None
    try:
        value = int(sequencing[key])
    except (TypeError, ValueError):
        _warn(f"{path} 'sequencing:' {key} is not a number; that horizon cannot be checked.")
        return None
    if value <= 0:
        _warn(f"{path} 'sequencing:' {key} is not positive; that horizon cannot be checked.")
        return None
    return value


def known_item_refs(tags_path: str | Path = DEFAULT_TAGS_PATH) -> frozenset:
    """Item ids the narrative layer knows about, for optional reference checks.

    Read from narrative/corpus_tags.yaml, which is hand-maintained and lists
    every corpus item by id. This says an id is known to the taxonomy, not
    that the file is on disk today: that stronger check belongs to
    corpus_map.py, which reads the sibling folders live. Returns an empty
    set (with a warning) if the file cannot be read, and validation treats
    an empty set as "do not check".
    """
    path = Path(tags_path)
    if yaml is None or not path.is_file():
        _warn(f"corpus tags not found at {path}; item references cannot be checked.")
        return frozenset()
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        _warn(f"could not parse corpus tags at {path} ({exc}); references unchecked.")
        return frozenset()
    if not isinstance(doc, dict):
        return frozenset()
    return frozenset(str(k) for k in (doc.get("items") or {}))


# ---------------------------------------------------------------------
# The record types
# ---------------------------------------------------------------------

@dataclass
class Slot:
    """One dated position in the window, pointing at a piece of content.

    `summary` and `rationale` answer different questions and are kept
    apart deliberately. The summary says what the post argues; the
    rationale says why it sits on this date. Collapsing them gives a card
    that either describes the content and never justifies the placement,
    or justifies the placement to a reader who cannot tell what the post
    is. Both are the agent's own analysis, which is the line drawn in
    `narrative/corpus_tags.yaml`: this agent describes and sequences other
    agents' work, and never rewrites it. Neither field ever holds a title.
    """

    slot_id: str
    date: str  # ISO 8601 calendar date, YYYY-MM-DD
    item_ref: str  # "<source-agent>/<item-id>", never the content itself
    lens: str  # a calendar lens id from threads.yaml
    threads: list = field(default_factory=list)
    forms: list = field(default_factory=list)
    approval: str = DRAFT
    responds_to: list = field(default_factory=list)
    summary: str = ""  # what the post says, in the agent's own words
    rationale: str = ""  # why the post is on this date
    extra: dict = field(default_factory=dict)  # unknown keys, preserved verbatim

    def to_dict(self) -> dict:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Slot":
        return _from_dict(cls, data)


@dataclass
class CalendarWindow:
    """One lens's rolling forward window: a range, an arc, and its slots.

    `start_date` and `end_date` bound a range, not a grid. Dates inside it
    may hold several slots or none, because posts per week is unanswered
    (PRD Section 7) and a schema that assumed a cadence would have to change
    when Jordan gives one.

    `arc` is the stated narrative arc. PRD Section 2 requires the calendar
    to state one rather than being a list of dated topics, so the field is
    part of the artifact rather than commentary about it.
    """

    window_id: str
    lens: str
    start_date: str
    end_date: str
    generated_at: str = ""  # ISO 8601 UTC
    arc: str = ""
    slots: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = _to_dict(self)
        data["slots"] = [s.to_dict() if isinstance(s, Slot) else s for s in self.slots]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "CalendarWindow":
        window = _from_dict(cls, data)
        window.slots = [
            Slot.from_dict(s) if isinstance(s, dict) else s for s in window.slots
        ]
        return window

    def slots_on(self, day: str) -> list:
        return [s for s in self.slots if s.date == day]

    def dates(self) -> list:
        return sorted({s.date for s in self.slots})


@dataclass
class Issue:
    """One validation finding. Data, not an exception.

    severity is "error" for something that makes the window wrong, and
    "warning" for something a human should look at but that does not
    invalidate the artifact.
    """

    code: str
    where: str
    detail: str
    severity: str = "error"

    def __str__(self) -> str:
        return f"[{self.severity}] {self.code} at {self.where}: {self.detail}"


def _to_dict(record: Any) -> dict:
    data = {}
    for f in fields(record):
        if f.name == "extra":
            continue
        data[f.name] = getattr(record, f.name)
    for key, value in (getattr(record, "extra", None) or {}).items():
        if key not in data:
            data[key] = value
    return data


def _from_dict(cls, data: dict):
    if not isinstance(data, dict):
        raise ValueError(f"expected a mapping, got {type(data).__name__}")
    names = {f.name for f in fields(cls)} - {"extra"}
    known = {k: v for k, v in data.items() if k in names}
    extra = {k: v for k, v in data.items() if k not in names}
    missing = [
        f.name
        for f in fields(cls)
        if f.name not in known
        and f.name != "extra"
        and f.default is _MISSING
        and f.default_factory is _MISSING  # type: ignore[misc]
    ]
    if missing:
        raise ValueError(f"missing required field(s): {', '.join(missing)}")
    record = cls(**known)
    record.extra = extra
    return record


# ---------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------

def dumps(window: CalendarWindow) -> str:
    """Serializes a window to the exact bytes that get written to disk.

    Field order is dataclass order, with unknown keys after it, so two
    writes of the same window are byte-identical. `ensure_ascii=False`
    keeps the em dashes and curly quotes editors put in a rationale
    readable in the file rather than escaped.
    """
    return json.dumps(window.to_dict(), indent=2, ensure_ascii=False) + "\n"


def loads(text: str) -> CalendarWindow:
    return CalendarWindow.from_dict(json.loads(text))


def write_window(path: str | Path, window: CalendarWindow) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(window), encoding="utf-8")
    return path


def read_window(path: str | Path) -> CalendarWindow | None:
    """Reads a window file. Returns None with a warning rather than raising."""
    path = Path(path)
    if not path.is_file():
        _warn(f"no window file at {path}.")
        return None
    try:
        return loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError) as exc:
        _warn(f"could not read the window at {path} ({exc}).")
        return None


def window_filename(lens: str, month: str, qualifier: str = "") -> str:
    """The one place the standing-window naming convention is written down.

    `window-<lens>-<YYYY-MM>.json`, lens first. Three reasons, in order:

    - One dot, so the dot-count rule in `newest_window_path` still excludes
      `.proposed.json` and `.rejected.json` without being modified. That
      rule is the only thing stopping a proposal being picked up as a
      standing calendar.
    - A lens lookup is then a prefix glob, and dates still sort correctly
      inside one lens, which is the only ordering this module has ever
      relied on. Date-first naming would sort across lenses, which nothing
      wants.
    - The lens is already in `window_id` (`jordan-2026-10-01`), so the
      filename agrees with the id rather than inventing a second scheme.
    """
    stem = f"window-{lens}-{month}"
    return f"{stem}.{qualifier}.json" if qualifier else f"{stem}.json"


def newest_window_path(
    directory: str | Path = DEFAULT_STATE_DIR, *, lens: str
) -> Path | None:
    """The most recent window `synthesis/assemble.py --out` wrote for `lens`.

    Newest by filename rather than mtime, because the names carry the window
    they describe (`window-jordan-2026-10.json`) while mtime records when
    someone last happened to touch the file, and a re-run of an older month
    should not become the current calendar.

    `lens` is required, and keyword-only so that no existing positional call
    can acquire it by accident. Before three calendars existed this function
    took the last `window-*.json` by filename, which with three lenses on
    disk picks between them alphabetically. Today that returns
    `window-jordan-*` and is the right answer for the wrong reason; the first
    lens named after "jordan" would silently become everyone's calendar. A
    caller that cannot name a lens is not yet asking a well-formed question.

    Only a plain `window-<lens>-<id>.json` counts. Anything carrying a
    further qualifier (`window-jordan-2026-10.rejected.json`,
    `window-jordan-2026-10.proposed.json`) is deliberately not a calendar: a
    rejected window exists to be diagnosed and a proposed one exists to be
    reviewed, and neither is the thing anyone should be approving or
    publishing from. The rule is the dot count rather than a list of known
    suffixes, so the next qualifier somebody invents is excluded by default
    instead of silently becoming the standing calendar.

    Windows written before the lens-first convention (`window-2026-09.json`)
    do not match the prefix and are therefore invisible here. That is the
    intended outcome rather than a migration left undone: they stay on disk
    as history and the calendar points at October.

    Since 2026-09-18 the page, the approval queue and the weekly refresh ask
    `live_window_paths` instead, because once next month is assembled a lens
    has two standing windows and the newest one hides the rest of this month.
    This function answers only "what was assembled last" now, and its
    callers are `assemble.py --against` and anything else that really means
    that.
    """
    found = _standing_window_paths(directory, lens, "newest_window_path")
    return found[-1] if found else None


def live_window_paths(
    directory: str | Path = DEFAULT_STATE_DIR, *, lens: str, today: str | date
) -> list:
    """Every standing window for `lens` that has not fully passed, oldest first.

    Added 2026-09-18 when assembly went on a schedule. From the day next
    month is assembled until the current month ends, a lens has two standing
    windows, and "the current window" stops being one file. The page and the
    queue need both, because the second half of this month is still awaiting
    sign-off while next month's first half is being read. The weekly refresh
    needs both, because an empty or thin future window is exactly where
    newly arrived content matters. `newest_window_path` stays for the one
    question that really does want a single newest file.

    A window is live while its `end_date` is today or later. One whose end
    date cannot be read is kept rather than dropped: a standing calendar
    silently vanishing from the page is the failure this function exists to
    prevent, and a broken file surfaces downstream when it is read.

    The dot-count rule is the same one `newest_window_path` applies, from the
    same helper, so a `.proposed.json` or `.rejected.json` never becomes live.
    """
    reference = _as_date(today)
    live = []
    for path in _standing_window_paths(directory, lens, "live_window_paths"):
        window = read_window(path)
        end = _as_date(window.end_date) if window is not None else None
        if reference is None or end is None or end >= reference:
            live.append(path)
    return live


def _standing_window_paths(directory: str | Path, lens: str, caller: str) -> list:
    """Plain `window-<lens>-<id>.json` files, sorted by name, which sorts by month.

    Only one dot counts. Anything carrying a further qualifier is not a
    calendar, and the rule is the dot count rather than a list of suffixes so
    the next qualifier somebody invents is excluded by default.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return []
    if not str(lens).strip():
        _warn(f"{caller} was asked for a window with no lens.")
        return []
    return sorted(
        p for p in directory.glob(f"window-{lens}-*.json")
        if p.is_file() and p.name.count(".") == 1
    )


# ---------------------------------------------------------------------
# Horizons, resolved from configuration
# ---------------------------------------------------------------------

def window_bounds(taxonomy: Taxonomy, start: str | date) -> tuple:
    """The (start, end) dates of a window opening on `start`: to the end of its month.

    A window is a calendar month since 2026-09-18. It used to be
    `draft_horizon_days` long, which made a 30-day window starting on the
    first miss the 31st of every long month: 2026-12-31 is a Thursday posting
    day and would have belonged to no window. Assembly is monthly and the
    file is named by month (`window-<lens>-<YYYY-MM>.json`), so the window is
    the month. A start after the first runs to the end of that same month,
    so a window never spills into the next month's file.

    `draft_horizon_days` is untouched. Since 2026-09-19 its one remaining
    reader is the `beyond-window` warning in `check_horizons`. The two shared
    a number, not a concept, and window length no longer reads it.
    `taxonomy` is still accepted so no caller had to change.

    Windows assembled before this change (October runs 10-01 to 10-30) keep
    the range they state, which is still a true statement of what they cover.
    Returns (start, None) for an unreadable start.
    """
    first = _as_date(start)
    if first is None:
        return (start if isinstance(start, str) else str(start), None)
    following = (first.replace(day=1) + timedelta(days=32)).replace(day=1)
    return (first.isoformat(), (following - timedelta(days=1)).isoformat())


def window_days(window: CalendarWindow) -> list:
    """Every date in the window's range, including the ones holding nothing.

    A window is a range with gaps rather than a grid, and the gaps are
    information: cadence is unanswered (PRD Section 7), so an empty day is
    an open question a human should see rather than an error. Anything
    displaying a window needs the whole range and not just the dates in use,
    which is why this lives here rather than being recomputed by each caller.
    Returns an empty list when either bound is unreadable, and never
    materializes a range longer than a year.
    """
    first = _as_date(window.start_date)
    last = _as_date(window.end_date)
    if first is None or last is None or last < first:
        return []
    span = (last - first).days
    if span > 366:
        _warn(f"{window.window_id} spans {span + 1} days; listing the first 366 only.")
        span = 365
    return [(first + timedelta(days=n)).isoformat() for n in range(span + 1)]


# Approval is an affordance, not a requirement (owner decision 2026-09-19).
#
# `approval_boundary` and `required_approval` used to live here. They answered
# "what state does this date have to be in by now", and everything downstream
# turned that into an obligation: an error on every unapproved slot, a due and
# overdue queue, an example window that pre-approved itself. Antonio's call is
# that the founders approving posts is a feature for them, so they can see on
# the dashboard what they have signed off, and never something the agent
# requires in order to work.
#
# So the presence of an approval is respected everywhere it was before, and
# the absence of one is no longer a finding anywhere. Nothing here asks when.
# `apply_approvals` below still carries what the store holds, the refresh
# still refuses to move an approved slot, and the page still reports the
# store rather than the button.
#
def check_horizons(
    window: CalendarWindow, taxonomy: Taxonomy, today: str | date
) -> list:
    """Issues about the window's own shape, never about who has not decided yet.

    Two findings, and neither asks anything of a founder. A slot that carries
    a decision to decline still holds a date, which is a date spoken for by a
    post nobody will publish. And a slot beyond the forward window is a
    planning question for whoever assembles it.

    Until 2026-09-19 this also reported every unapproved slot inside the
    approved horizon as an error. That was the obligation the owner removed:
    see the note above `partition_issues`, which existed only to carve that
    finding back out again.
    """
    issues: list = []
    reference = _as_date(today)

    for slot in window.slots:
        if _as_date(slot.date) is None:
            continue
        if slot.approval not in _AT_LEAST_DRAFT:
            issues.append(
                Issue(
                    "declined-slot-holds-a-date",
                    slot.slot_id,
                    f"{slot.date} is spoken for by a slot that is {slot.approval!r}, "
                    "so the date carries a post nobody will publish",
                )
            )

    if reference is not None and taxonomy.draft_horizon_days is not None:
        edge = reference + timedelta(days=taxonomy.draft_horizon_days)
        for slot in window.slots:
            target = _as_date(slot.date)
            if target is not None and target >= edge:
                issues.append(
                    Issue(
                        "beyond-window",
                        slot.slot_id,
                        f"{slot.date} is past the {taxonomy.draft_horizon_days}-day "
                        f"forward window that opens on {reference.isoformat()}",
                        severity="warning",
                    )
                )
    return issues


# ---------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------

def validate_slot(slot: Slot, taxonomy: Taxonomy, item_refs: Iterable = ()) -> list:
    issues: list = []
    where = slot.slot_id or "<slot with no id>"

    if not slot.slot_id:
        issues.append(Issue("missing-slot-id", "<unknown>", "a slot has no slot_id"))
    if _as_date(slot.date) is None:
        issues.append(Issue("bad-date", where, f"{slot.date!r} is not an ISO 8601 date"))
    if not _is_item_ref(slot.item_ref):
        issues.append(
            Issue(
                "bad-item-ref",
                where,
                f"{slot.item_ref!r} is not a '<source-agent>/<item-id>' reference",
            )
        )
    if not slot.rationale.strip():
        issues.append(
            Issue(
                "missing-rationale",
                where,
                "no reason recorded for putting this item on this date",
                severity="warning",
            )
        )
    if not slot.summary.strip():
        issues.append(
            Issue(
                "missing-summary",
                where,
                "nothing recorded about what this post says",
                severity="warning",
            )
        )

    if slot.approval not in APPROVAL_STATES:
        issues.append(
            Issue(
                "unknown-approval-state",
                where,
                f"{slot.approval!r} is not one of {', '.join(APPROVAL_STATES)}",
            )
        )

    if taxonomy.ok and taxonomy.calendar_lens_ids:
        if slot.lens not in taxonomy.calendar_lens_ids:
            known = ", ".join(sorted(taxonomy.calendar_lens_ids))
            issues.append(
                Issue(
                    "unknown-lens",
                    where,
                    f"{slot.lens!r} is not a calendar lens in {taxonomy.source_path} ({known})",
                )
            )
    if taxonomy.ok and taxonomy.thread_ids:
        if not slot.threads:
            issues.append(
                Issue("no-threads", where, "the slot carries no thread", severity="warning")
            )
        for thread_id in slot.threads:
            if thread_id not in taxonomy.thread_ids:
                issues.append(
                    Issue(
                        "unknown-thread",
                        where,
                        f"{thread_id!r} is not a thread in {taxonomy.source_path}",
                    )
                )
    if taxonomy.ok and taxonomy.form_ids:
        for form_id in slot.forms:
            if form_id not in taxonomy.form_ids:
                issues.append(
                    Issue(
                        "unknown-form",
                        where,
                        f"{form_id!r} is not a form in {taxonomy.source_path}",
                    )
                )

    refs = frozenset(item_refs)
    if refs and slot.item_ref not in refs:
        issues.append(
            Issue(
                "unknown-item",
                where,
                f"{slot.item_ref!r} is not an item the narrative layer knows about",
                severity="warning",
            )
        )

    for ref in slot.responds_to:
        if not _is_item_ref(ref):
            issues.append(
                Issue("bad-responds-to-ref", where, f"{ref!r} is not a content reference")
            )
        if ref == slot.item_ref:
            issues.append(Issue("responds-to-self", where, "the slot responds to its own item"))
    return issues


def validate_window(
    window: CalendarWindow,
    taxonomy: Taxonomy,
    item_refs: Iterable = (),
    today: str | date | None = None,
) -> list:
    """Every issue in a window. Returns findings; never raises.

    `today` defaults to the current UTC date and is a parameter so a test or
    a replay can ask what the window looked like on some other day.
    """
    issues: list = []
    where = window.window_id or "<window with no id>"

    if not window.window_id:
        issues.append(Issue("missing-window-id", "<unknown>", "the window has no window_id"))
    start = _as_date(window.start_date)
    end = _as_date(window.end_date)
    if start is None:
        issues.append(Issue("bad-date", where, f"start_date {window.start_date!r} is not a date"))
    if end is None:
        issues.append(Issue("bad-date", where, f"end_date {window.end_date!r} is not a date"))
    if start is not None and end is not None and end < start:
        issues.append(Issue("inverted-range", where, f"{window.end_date} precedes {window.start_date}"))
    if not window.arc.strip():
        issues.append(
            Issue(
                "missing-arc",
                where,
                "the window states no narrative arc, which makes it a list of dated topics",
                severity="warning",
            )
        )
    if taxonomy.ok and taxonomy.calendar_lens_ids and window.lens not in taxonomy.calendar_lens_ids:
        issues.append(
            Issue("unknown-lens", where, f"{window.lens!r} is not a calendar lens")
        )

    seen_ids: dict = {}
    scheduled: dict = {}
    for slot in window.slots:
        issues.extend(validate_slot(slot, taxonomy, item_refs))
        if slot.slot_id in seen_ids:
            issues.append(
                Issue("duplicate-slot-id", slot.slot_id, "two slots share this slot_id")
            )
        seen_ids[slot.slot_id] = slot
        if slot.lens != window.lens:
            issues.append(
                Issue(
                    "lens-mismatch",
                    slot.slot_id,
                    f"slot lens {slot.lens!r} is not the window's lens {window.lens!r}",
                )
            )
        slot_date = _as_date(slot.date)
        if slot_date is not None:
            if start is not None and slot_date < start:
                issues.append(
                    Issue("date-outside-window", slot.slot_id, f"{slot.date} precedes {window.start_date}")
                )
            if end is not None and slot_date > end:
                issues.append(
                    Issue("date-outside-window", slot.slot_id, f"{slot.date} follows {window.end_date}")
                )
            scheduled.setdefault(slot.item_ref, slot_date)

    # An item scheduled twice inside one 30-day window is almost certainly a
    # mistake rather than a decision, so it is reported. Several slots on one
    # date is not: cadence is unanswered and the window is a range, not a grid.
    counts: dict = {}
    for slot in window.slots:
        counts[slot.item_ref] = counts.get(slot.item_ref, 0) + 1
    for item_ref, count in counts.items():
        if count > 1:
            issues.append(
                Issue(
                    "item-scheduled-twice",
                    item_ref,
                    f"appears in {count} slots inside one window",
                    severity="warning",
                )
            )

    issues.extend(_check_ordering(window, scheduled))
    issues.extend(check_horizons(window, taxonomy, today or datetime.now(timezone.utc).date()))
    return issues


def _check_ordering(window: CalendarWindow, scheduled: dict) -> list:
    """Enforces what `responds_to` claims: a reply cannot precede its post.

    Only checks references to items scheduled in this same window. A
    reference to an already-published post is satisfied by definition, and a
    reference to something in no window at all is a synthesis question
    (build order item 9), not a schema violation.
    """
    issues: list = []
    for slot in window.slots:
        slot_date = _as_date(slot.date)
        if slot_date is None:
            continue
        for ref in slot.responds_to:
            other = scheduled.get(ref)
            if other is None:
                continue
            if other >= slot_date:
                issues.append(
                    Issue(
                        "responds-to-later-item",
                        slot.slot_id,
                        f"responds to {ref}, which is scheduled on {other.isoformat()}, "
                        f"on or after this slot's {slot.date}",
                    )
                )
    return issues


# `PENDING_APPROVAL_CODES` and `partition_issues` used to live here. They
# existed for one code, `unapproved-inside-approved-horizon`, which fired on
# every draft slot in a freshly assembled window: the validator called a
# normal calendar broken, and every caller had to partition the finding back
# out before reading the errors. The finding is gone (2026-09-19), so the
# workaround is too, and callers read `errors` and `warnings` directly.
#
def errors(issues: Iterable) -> list:
    return [i for i in issues if i.severity == "error"]


def warnings(issues: Iterable) -> list:
    return [i for i in issues if i.severity != "error"]


# ---------------------------------------------------------------------
# Approval state, joined from the store
# ---------------------------------------------------------------------

def apply_approvals(window: CalendarWindow, approvals: dict) -> CalendarWindow:
    """Returns a copy of `window` with approval state taken from `approvals`.

    `approvals` maps slot_id to state, which is what
    state_store.latest_approvals() returns. This is the join that makes
    approval survive a run: the window file says what is planned, the
    append-only store says what a human has since decided about it, and the
    store wins because it is the newer statement.

    Unknown states are left alone with a warning rather than written in, so
    a typo in a hand-appended entry cannot quietly unapprove a slot.
    """
    updated = []
    for slot in window.slots:
        state = approvals.get(slot.slot_id)
        if state is None or state == slot.approval:
            updated.append(slot)
            continue
        if state not in APPROVAL_STATES:
            _warn(f"ignoring approval state {state!r} for {slot.slot_id}; not a known state.")
            updated.append(slot)
            continue
        replacement = Slot.from_dict(slot.to_dict())
        replacement.approval = state
        updated.append(replacement)
    result = CalendarWindow.from_dict(window.to_dict())
    result.slots = updated
    return result


def apply_reschedules(window: CalendarWindow, reschedules: dict) -> CalendarWindow:
    """Returns a copy of `window` with slot dates taken from `reschedules`.

    `reschedules` maps slot_id to new_date (ISO 8601 string). Slots not in
    the map are unchanged. The same overlay pattern as `apply_approvals`: the
    window file says what the agent proposed, the reschedule store says what
    a human has since moved, and the store wins.

    An invalid or missing date is ignored with a warning rather than written
    in, for the same reason unknown approval states are ignored: a bad entry
    in the store should not silently corrupt the calendar.
    """
    updated = []
    for slot in window.slots:
        new_date = reschedules.get(slot.slot_id)
        if new_date is None or new_date == slot.date:
            updated.append(slot)
            continue
        if _as_date(new_date) is None:
            _warn(f"ignoring reschedule for {slot.slot_id}: {new_date!r} is not a valid date.")
            updated.append(slot)
            continue
        replacement = Slot.from_dict(slot.to_dict())
        replacement.date = new_date
        updated.append(replacement)
    result = CalendarWindow.from_dict(window.to_dict())
    result.slots = updated
    return result


# ---------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------

def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _is_item_ref(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parts = value.strip().split(_REF_SEPARATOR, 1)
    return len(parts) == 2 and bool(parts[0].strip()) and bool(parts[1].strip())


# Public, because the validator makes this shape a contract rather than an
# internal detail: anything writing slots has to be able to ask whether a
# reference will pass before it builds one. `synthesis/assemble.py` uses it to
# filter the model's `responds_to` refs on the way in, which is the difference
# between dropping one bad reference and failing the whole window.
is_item_ref = _is_item_ref

# Public for the same reason. Every slot date in this agent is parsed by this
# function, including the lenient cases (a datetime, a full timestamp, a
# stray space), and a caller that re-derives "how many days out is this slot"
# with a bare `date.fromisoformat` gets a different answer on exactly the
# inputs this one exists to absorb. `approval/queue.py` uses it to say how
# close a decision is to being late.
as_date = _as_date


def new_window(
    window_id: str,
    lens: str,
    start: str | date,
    taxonomy: Taxonomy,
    arc: str = "",
    generated_at: str | None = None,
) -> CalendarWindow:
    """Builds an empty window whose length comes from configuration.

    Convenience so callers do not have to compute the end date, and so the
    window's length comes from `window_bounds` rather than being stated.
    """
    first, last = window_bounds(taxonomy, start)
    return CalendarWindow(
        window_id=window_id,
        lens=lens,
        start_date=first,
        end_date=last or first,
        generated_at=generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        arc=arc,
        slots=[],
    )


if __name__ == "__main__":
    # Standalone run: reads the real taxonomy, builds the hand-constructed
    # example window from example_window.py, writes it, reads it back, writes
    # it again, and reports whether the two writes are byte-identical. Then
    # shows the approval horizons doing their work, and finally shows what
    # validation catches when a window is wrong.
    #
    # It decides nothing. Which item belongs on which date is build order
    # item 9; this only demonstrates that the schema can hold the answer.
    import tempfile

    sys.path.insert(0, str(_HERE))
    import example_window  # noqa: E402

    parser = argparse.ArgumentParser(
        description="Calendar slot and window schema for the Thought Leadership Calendar Manager."
    )
    parser.add_argument("--threads", default=str(DEFAULT_TAXONOMY_PATH), help="path to threads.yaml")
    parser.add_argument("--tags", default=str(DEFAULT_TAGS_PATH), help="path to corpus_tags.yaml")
    parser.add_argument("--today", default=None, help="reference date (YYYY-MM-DD), default today UTC")
    parser.add_argument("--out", default=None, help="where to write the example window (default: a temp file)")
    args = parser.parse_args()

    taxonomy = load_taxonomy(args.threads)
    reference = _as_date(args.today) or datetime.now(timezone.utc).date()

    print(f"Taxonomy: {taxonomy.source_path}")
    print(f"  threads: {len(taxonomy.thread_ids)}   forms: {len(taxonomy.form_ids)}")
    print(
        f"  calendar lenses: {', '.join(sorted(taxonomy.calendar_lens_ids)) or 'none'}"
        f"   (v1: {', '.join(sorted(taxonomy.v1_lens_ids)) or 'none'})"
    )
    print(
        f"  horizons, read from the sequencing block, not from this file: "
        f"approved {taxonomy.approved_horizon_days}, draft {taxonomy.draft_horizon_days}"
    )

    window = example_window.build(taxonomy, reference)
    print(f"\nExample window {window.window_id}")
    print(f"  lens {window.lens}, {window.start_date} to {window.end_date}, {len(window.slots)} slots")
    print(f"  dates used: {len(window.dates())} of "
          f"{(_as_date(window.end_date) - _as_date(window.start_date)).days + 1} in range, "
          "so the window is a range with gaps rather than a grid")

    out = Path(args.out) if args.out else Path(tempfile.mkdtemp()) / "example-window.json"
    write_window(out, window)
    first = out.read_bytes()
    reread = read_window(out)
    write_window(out, reread)
    second = out.read_bytes()
    print(f"\nRound trip: wrote {out}")
    print(f"  write, read, write again is byte-identical: {first == second}")
    print(f"  {len(first)} bytes, {len(reread.slots)} slots read back")

    print("\nApproval states, as the store would report them:")
    for slot in sorted(window.slots, key=lambda s: (s.date, s.slot_id)):
        offset = (_as_date(slot.date) - reference).days
        print(f"  {slot.date} (+{offset:2d}d)  {slot.approval:9s}  {slot.item_ref}")

    refs = known_item_refs(args.tags)
    found = validate_window(window, taxonomy, refs, today=reference)
    print(f"\nValidation of the example: {len(errors(found))} error(s), {len(warnings(found))} warning(s)")
    for issue in found:
        print(f"  {issue}")

    # The same window, broken on purpose, so the checks are visibly doing
    # something rather than passing vacuously.
    broken = CalendarWindow.from_dict(window.to_dict())
    broken.slots[0].approval = DRAFT
    broken.slots[1].threads = ["a-thread-nobody-declared"]
    broken.slots[0].responds_to = [broken.slots[-1].item_ref]
    caught = validate_window(broken, taxonomy, refs, today=reference)
    print(f"\nSame window, broken on purpose: {len(errors(caught))} error(s)")
    for issue in errors(caught):
        print(f"  {issue}")
