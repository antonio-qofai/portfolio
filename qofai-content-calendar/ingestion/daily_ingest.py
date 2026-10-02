"""One ingest pass: read every available source, record what was seen.

This is the entry point the daily run calls (PRD.md Section 5, build order
item 12). It owns no reading logic of its own. Each source has a reader in
this folder already, and this module's whole job is to call them, normalize
what they return into one entry shape, and append that to the state store so
the month-end assembly has a month to assemble.

Why a runner exists at all
--------------------------
The readers return data and forget it. The store remembers but has no
producer. Until something joined them, C2 could observe the world and could
persist facts, but never actually accumulated anything: every run started
blank, which is exactly the failure the store was built to prevent. This is
that join, and nothing more.

What it records
---------------
One `ingestion_log` entry per item seen, keyed by the stable reference the
rest of the agent already uses, `<source-agent>/<item-id>`, the same
convention `narrative/corpus_tags.yaml` keys on and the same one the slot
schema's `item_ref` expects. Re-running is safe and is meant to be routine:
the store is append-only with no update call, `read_state(latest_per_key=...)`
collapses repeats, and seeing the same item on thirty consecutive days is a
true statement about thirty days rather than a duplicate to suppress.

Content is referenced, never copied. An entry carries the reference, a title,
and small facts worth trending (word count, persona, dates). The text stays
in the dependency agent's folder and is re-read live when it is needed, so
this store cannot go stale against what Alex and Robin currently hold, and no
other team's content is duplicated into ours.

Backfill
--------
`--backfill` marks entries as historical rather than as today's activity. The
distinction is not cosmetic: without it, twenty items appearing in one append
reads as a burst of publishing on the day the runner was first pointed at a
folder that had been filling up for two months. The flag records
`backfill: true` on every entry and stamps `event_at` from the item's own
date where the source gives one, so trend questions asked later get an
honest answer. Items whose source gives no date keep the run timestamp,
which is the truth: we know when we saw it and not when it appeared.

Sources that are absent, unreachable, or unconfigured are reported and
skipped. A missing Airtable token or a sibling folder that is not in the
sparse checkout must not take the ingest down, so every source is independent
and the run's exit code reflects whether the *store* was written, never
whether every source answered.

What it records, second store
-----------------------------
One `ingest_runs` entry per source per pass, keyed by the source name, saying
whether that source answered and how many items it gave. This exists because
the `ingestion_log` records items and only items, so a source that answered
with nothing and a source that was never asked leave the same trace in it:
none. Gap detection (item 11) has to tell those apart, and one more level of
that conflation is the failure `state_store.py` was written to prevent. With
the outcomes stored, a silent source resolves to one of three different
findings rather than one ambiguous one: the run did not happen, the run
happened and the source was genuinely empty, or the source was asked and
failed. The three want three different responses, and only the third is
anyone's fault.

Not in scope: deciding whether an expected input is late (build order item
11, gap detection, which reads what this writes), scheduling (item 12's
workflow), and anything about the calendar itself. This module reports
outcomes; it does not judge them.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

_HERE = Path(__file__).resolve().parent
_AGENT_ROOT = _HERE.parent
_REPOS_ROOT = _AGENT_ROOT.parent

if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

from calendar_model.state_store import DEFAULT_STATE_ROOT, record_state  # noqa: E402

import atomizer_reader  # noqa: E402
import read_status
import conference_reader  # noqa: E402
import market_scan_reader  # noqa: E402
import portal_reader  # noqa: E402
import vcb_reader  # noqa: E402

DEFAULT_STORE = "ingestion_log"

# Where per-source outcomes go. A separate store rather than a second kind of
# row in `ingestion_log`, so that store keeps meaning exactly one thing: one
# entry per item seen. Gap detection reads this one to tell a quiet source
# from an unasked one.
DEFAULT_RUN_STORE = "ingest_runs"

# Where each file-based dependency agent writes, relative to repos/. Paths are
# arguments with these as defaults, never constants embedded in the calls, so
# a moved folder or a test fixture is a flag rather than an edit.
DEFAULT_ATOMIZER_PATH = _REPOS_ROOT / "content-atomizer" / "output"
DEFAULT_VCB_PATH = _REPOS_ROOT / "value-creation-briefing" / "drafts"

# The source-agent half of every item reference. These match the folder names
# the corpus map and corpus_tags.yaml already use; changing one orphans every
# entry previously written under the old name.
SOURCE_ATOMIZER = "content-atomizer"
SOURCE_VCB = "value-creation-briefing"
SOURCE_CONFERENCE = "conference-event-intelligence"
SOURCE_MARKET_SCAN = "market-scan"

# The two portal-only sources, added 2026-09-21. Named for what the data is
# rather than for the agent that was once expected to supply it: engagement
# reaches the portal from one central PhantomBuster pull and not from Maria's
# agent, and published history is entered by a person rather than produced by
# anything. Nothing was ever recorded under the old `linkedin-content-outreach`
# id, so this renames a plan rather than orphaning a history.
#
# Both are skipped with a reason until `PORTAL_TOKEN` is set, exactly as the
# conference source was skipped before its Airtable token existed.
SOURCE_ENGAGEMENT = "linkedin-post-engagement"
SOURCE_PUBLISHED = "published-content"


def _warn(message: str) -> None:
    print(f"daily_ingest: {message}", file=sys.stderr)


# What a source did when asked. Three values rather than a boolean, because
# gap detection has three different things to say: nothing is wrong, someone
# needs to set a token, or the source broke. `ok` is kept alongside as the
# blunt answer the run's own reporting uses.
STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_UNCONFIGURED = "unconfigured"


@dataclass
class SourceOutcome:
    """What one source contributed to this pass, so a silent source is visible."""

    source: str
    ok: bool
    items: int = 0
    detail: str = ""
    status: str = STATUS_OK


@dataclass
class IngestResult:
    outcomes: list[SourceOutcome] = field(default_factory=list)
    entries: list[dict[str, Any]] = field(default_factory=list)
    stored: int = 0
    store_ok: bool = False
    runs_stored: int = 0
    run_store_ok: bool = False

    @property
    def total_items(self) -> int:
        return len(self.entries)


def _entry(
    source: str,
    item_id: str,
    *,
    title: str | None = None,
    event_at: str | None = None,
    backfill: bool = False,
    **facts: Any,
) -> dict[str, Any]:
    """One ingestion_log entry, in the shape the store stores verbatim."""
    value: dict[str, Any] = {"source": source}
    if title:
        value["title"] = title
    value.update({k: v for k, v in facts.items() if v is not None})
    if backfill:
        value["backfill"] = True

    entry: dict[str, Any] = {
        "action": "seen",
        "entry_key": f"{source}/{item_id}",
        "value": value,
    }
    if event_at:
        entry["event_at"] = event_at
    return entry


def _run_entry(outcome: SourceOutcome, *, mode: str) -> dict[str, Any]:
    """One `ingest_runs` entry: this source was asked, and this is what it said.

    Keyed by the source name alone, with no item id in it, so that
    `read_state(latest_per_key=True)` answers "what did each source do the
    last time it was asked" in one read. `event_at` is deliberately left to
    the store's default of now even on a backfill: the question this store
    answers is when we last *asked*, which is a fact about the run and not
    about the age of the content that came back.
    """
    return {
        "action": "asked",
        "entry_key": outcome.source,
        "quantity": outcome.items,
        "value": {
            "source": outcome.source,
            "ok": outcome.ok,
            "status": outcome.status,
            "items": outcome.items,
            "mode": mode,
            **({"detail": outcome.detail} if outcome.detail else {}),
        },
    }


def _month_to_event_at(month: str | None) -> str | None:
    """A `YYYY-MM` filename month as an ISO timestamp at the first of it.

    Robin's drafts are named by month and carry no day, so the first is the
    honest reading: the item belongs to that month and we do not know more.
    """
    if not month or len(month) != 7 or month[4] != "-":
        return None
    try:
        year, mon = int(month[:4]), int(month[5:])
        return datetime(year, mon, 1, tzinfo=timezone.utc).isoformat()
    except ValueError:
        return None


def _collect(
    result: IngestResult, source: str, reader: Callable[..., list[dict[str, Any]]]
) -> None:
    """Runs one source's collection, recording the outcome either way.

    Every source is independent: a sibling folder missing from the sparse
    checkout or an unset Airtable token is reported and skipped, never raised,
    because one absent source must not cost the run the sources that answered.

    Since 2026-09-17 the reader is also handed a `ReadStatus` and says which
    kind of nothing it is returning. A reader that comes back empty used to be
    indistinguishable from one whose credentials were refused or whose host
    was unreachable, so the store recorded all three as a healthy quiet
    source. The finer status is what lets the daily workflow redden on the
    second two and stay green on the first.
    """
    status = read_status.ReadStatus()
    try:
        entries = reader(status)
    except SourceUnconfigured as exc:
        _warn(f"{source} skipped: {exc}")
        result.outcomes.append(
            SourceOutcome(source, ok=False, detail=str(exc), status=STATUS_UNCONFIGURED)
        )
        return
    except Exception as exc:  # noqa: BLE001 - one bad source must not end the pass
        _warn(f"{source} failed: {type(exc).__name__}: {exc}")
        result.outcomes.append(
            SourceOutcome(source, ok=False, detail=str(exc), status=STATUS_FAILED)
        )
        return
    result.entries.extend(entries)
    if status.broken:
        # The reader returned without raising and told us it could not find
        # out what the source holds. That is a failure with data attached, not
        # a success, and recording it as ok is how a broken token stays
        # invisible for a fortnight.
        _warn(f"{source} came back {status.status}: {status.detail}")
        result.outcomes.append(
            SourceOutcome(
                source,
                ok=False,
                items=len(entries),
                detail=status.detail,
                status=status.status,
            )
        )
        return
    if status.status != read_status.OK:
        _warn(f"{source}: {status}")
    result.outcomes.append(
        SourceOutcome(
            source,
            ok=True,
            items=len(entries),
            detail=status.detail,
            status=status.status,
        )
    )


def collect_atomizer(status, path: Path, backfill: bool) -> list[dict[str, Any]]:
    posts = atomizer_reader.read_atomizer_posts(str(path), status=status)
    return [
        _entry(
            SOURCE_ATOMIZER,
            post.post_id,
            title=post.post_id,
            backfill=backfill,
            persona=post.persona,
            word_count=len(post.body.split()),
        )
        for post in posts
    ]


def collect_vcb(status, path: Path, backfill: bool) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for draft in vcb_reader.read_persona_blog_drafts(str(path), status=status):
        item_id = Path(draft.source_path).stem
        entries.append(
            _entry(
                SOURCE_VCB,
                item_id,
                title=draft.title,
                event_at=_month_to_event_at(draft.month) if backfill else None,
                backfill=backfill,
                persona=draft.persona,
                month=draft.month,
                word_count=len(draft.text.split()),
                kind="persona_blog",
            )
        )
    # The briefing read must not overwrite a problem the blog read found, so
    # it reports into its own box and only the worse of the two is kept. Both
    # come from the same folder, so "missing" from either means the same
    # thing, but an empty blog set beside three briefings is a healthy run.
    briefing_status = read_status.ReadStatus()
    for briefing in vcb_reader.read_briefing_drafts(str(path), status=briefing_status):
        item_id = Path(briefing.source_path).stem
        entries.append(
            _entry(
                SOURCE_VCB,
                item_id,
                title=item_id,
                event_at=_month_to_event_at(briefing.month) if backfill else None,
                backfill=backfill,
                month=briefing.month,
                word_count=len(briefing.text.split()),
                kind="monthly_briefing",
            )
        )
    # Worse of the two wins, and finding anything at all beats both.
    if entries:
        status.set(read_status.OK)
    elif briefing_status.broken and not status.broken:
        status.set(briefing_status.status, briefing_status.detail)
    elif status.status == read_status.EMPTY and briefing_status.status == read_status.MISSING:
        status.set(briefing_status.status, briefing_status.detail)
    return entries


class SourceUnconfigured(Exception):
    """A source cannot be read because it was never configured, not because it failed.

    Raised instead of returning nothing so `_collect` records an outcome that
    names the cause. Both cases skip the source and neither is fatal; the
    difference is entirely in what gap detection can then say about it, and
    "nobody has set the token" is a different sentence from "the source is
    quiet" to whoever reads the flag.
    """


def collect_conferences(status, backfill: bool) -> list[dict[str, Any]]:
    if not conference_reader.token_configured():
        raise SourceUnconfigured(
            "no Airtable token in the environment; set CALENDAR_AGENT_AIRTABLE_TOKEN "
            "(see README.md) or this source records zero rows every day"
        )
    records = conference_reader.read_conferences(status=status)
    return [
        _entry(
            SOURCE_CONFERENCE,
            record.record_id,
            title=record.conference_name,
            backfill=backfill,
            start_date=record.start_date,
            end_date=record.end_date,
            cfp_deadline=record.cfp_deadline,
            status=record.status,
        )
        for record in records
    ]


def collect_market_scan(status, backfill: bool, lookback_days: int | None) -> list[dict[str, Any]]:
    items = market_scan_reader.read_market_scan(lookback_days=lookback_days, status=status)
    return [
        _entry(
            SOURCE_MARKET_SCAN,
            item.item_id,
            title=item.title,
            event_at=item.published_at,
            backfill=backfill,
            feed=item.source_id,
            url=item.url,
            matched_terms=item.matched_terms or None,
        )
        for item in items
    ]


def collect_engagement(status, backfill: bool) -> list[dict[str, Any]]:
    if not portal_reader.token_configured():
        raise SourceUnconfigured(
            "no portal token in the environment; set PORTAL_TOKEN (Maria issues it from "
            "the portal's /admin page, see README.md) or engagement records nothing every day"
        )
    rows = portal_reader.read_post_engagement(status=status)
    return [
        _entry(
            SOURCE_ENGAGEMENT,
            row.item_id,
            title=row.engager_name or row.profile_url,
            event_at=row.engaged_at or row.scraped_at,
            backfill=backfill,
            post_url=row.post_url or None,
            action=row.action or None,
            profile_url=row.profile_url or None,
            # Carried per row rather than summarised once, because the config's
            # column guesses may be right for some rows and wrong for others
            # the day PhantomBuster changes a header mid-month.
            unresolved=row.unresolved or None,
        )
        for row in rows
    ]


def collect_published(status, backfill: bool) -> list[dict[str, Any]]:
    if not portal_reader.token_configured():
        raise SourceUnconfigured(
            "no portal token in the environment; set PORTAL_TOKEN (Maria issues it from "
            "the portal's /admin page, see README.md) or published history records nothing "
            "every day"
        )
    posts = portal_reader.read_published_content(status=status)
    return [
        _entry(
            SOURCE_PUBLISHED,
            post.item_id,
            title=post.title,
            event_at=post.date_published,
            backfill=backfill,
            url=post.url or None,
            channel=post.channel or None,
            themes=post.themes or None,
            status=post.status or None,
        )
        for post in posts
    ]


def run_ingest(
    *,
    atomizer_path: Path = DEFAULT_ATOMIZER_PATH,
    vcb_path: Path = DEFAULT_VCB_PATH,
    store: str = DEFAULT_STORE,
    run_store: str = DEFAULT_RUN_STORE,
    state_root: Path = DEFAULT_STATE_ROOT,
    backfill: bool = False,
    lookback_days: int | None = None,
    sources: list[str] | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
) -> IngestResult:
    """Reads every selected source and appends what it saw to the store.

    `sources` selects a subset by name; None means all of them. `dry_run`
    collects and reports without writing, which is how you check what a first
    real backfill would record before it is on disk for good.

    Two stores are written, never one. `store` takes an entry per item seen
    and `run_store` takes an entry per source asked, because a source that
    answered with nothing writes no items and would otherwise be
    indistinguishable from a source nobody asked.
    """
    result = IngestResult()
    wanted = set(sources) if sources else None

    def selected(name: str) -> bool:
        return wanted is None or name in wanted

    if selected(SOURCE_ATOMIZER):
        _collect(result, SOURCE_ATOMIZER, lambda st: collect_atomizer(st, atomizer_path, backfill))
    if selected(SOURCE_VCB):
        _collect(result, SOURCE_VCB, lambda st: collect_vcb(st, vcb_path, backfill))
    if selected(SOURCE_CONFERENCE):
        _collect(result, SOURCE_CONFERENCE, lambda st: collect_conferences(st, backfill))
    if selected(SOURCE_MARKET_SCAN):
        _collect(
            result,
            SOURCE_MARKET_SCAN,
            lambda st: collect_market_scan(st, backfill, lookback_days),
        )
    if selected(SOURCE_ENGAGEMENT):
        _collect(result, SOURCE_ENGAGEMENT, lambda st: collect_engagement(st, backfill))
    if selected(SOURCE_PUBLISHED):
        _collect(result, SOURCE_PUBLISHED, lambda st: collect_published(st, backfill))

    if dry_run:
        result.store_ok = True
        return result

    written = record_state(store, result.entries, root=state_root, now=now)
    result.stored = int(written.get("stored") or 0)
    result.store_ok = bool(written.get("ok"))

    mode = "backfill" if backfill else "ingest"
    run_rows = [_run_entry(outcome, mode=mode) for outcome in result.outcomes]
    run_written = record_state(run_store, run_rows, root=state_root, now=now)
    result.runs_stored = int(run_written.get("stored") or 0)
    result.run_store_ok = bool(run_written.get("ok"))
    if not result.run_store_ok:
        # Warned about loudly and deliberately not fatal. The exit code answers
        # "was the item store written", which is what the workflow's
        # commit-back decision needs and what item 12 documented; changing that
        # here would break a contract the workflow depends on. The cost of this
        # write failing is narrower and worth stating: gap detection loses its
        # only evidence of what was asked, so it degrades to reporting silence
        # without being able to name a cause.
        _warn(
            f"could not write the run store {run_store!r}; gap detection will not be "
            "able to tell a quiet source from an unasked one for this pass."
        )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--atomizer-path", type=Path, default=DEFAULT_ATOMIZER_PATH)
    parser.add_argument("--vcb-path", type=Path, default=DEFAULT_VCB_PATH)
    parser.add_argument("--store", default=DEFAULT_STORE)
    parser.add_argument(
        "--run-store",
        default=DEFAULT_RUN_STORE,
        help=f"where per-source outcomes go (default: {DEFAULT_RUN_STORE})",
    )
    parser.add_argument(
        "--state-root",
        type=Path,
        default=DEFAULT_STATE_ROOT,
        help=f"where the stores live (default: {DEFAULT_STATE_ROOT})",
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="mark entries as historical and date them from the source where it says",
    )
    parser.add_argument("--lookback-days", type=int, default=None)
    parser.add_argument(
        "--source",
        action="append",
        dest="sources",
        help="ingest only this source; repeatable",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="collect and report without writing to the store",
    )
    args = parser.parse_args(argv)

    result = run_ingest(
        atomizer_path=args.atomizer_path,
        vcb_path=args.vcb_path,
        store=args.store,
        run_store=args.run_store,
        state_root=args.state_root,
        backfill=args.backfill,
        lookback_days=args.lookback_days,
        sources=args.sources,
        dry_run=args.dry_run,
    )

    mode = "dry run" if args.dry_run else ("backfill" if args.backfill else "ingest")
    print(f"{mode}: {len(result.outcomes)} source(s)")
    # A source that answered and had nothing is not a failure and must not
    # read as one. Only the statuses where we could not find out do.
    _flags = {
        STATUS_OK: "ok     ",
        read_status.EMPTY: "empty  ",
        read_status.MISSING: "MISSING",
        read_status.UNREACHABLE: "NO REPLY",
        read_status.REJECTED: "REFUSED",
        STATUS_UNCONFIGURED: "SKIPPED",
        STATUS_FAILED: "FAILED ",
    }
    for outcome in result.outcomes:
        flag = _flags.get(outcome.status, "FAILED ")
        detail = f"  ({outcome.detail})" if outcome.detail else ""
        print(f"  [{flag}] {outcome.source}: {outcome.items} item(s){detail}")

    print(f"\n{result.total_items} item(s) collected")
    if args.dry_run:
        print("nothing written (dry run)")
    else:
        print(f"{result.stored} entry/entries appended to '{args.store}' under {args.state_root}")
        print(f"{result.runs_stored} outcome(s) appended to '{args.run_store}'")

    # A source that could not be reached is reported, not fatal. The exit code
    # answers "was the store written", because that is what the daily workflow
    # needs to decide whether the run is worth committing.
    return 0 if result.store_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
