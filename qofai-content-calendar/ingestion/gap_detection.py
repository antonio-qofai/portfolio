"""Whether each expected input actually arrived, and a flag when it did not.

Build order item 11 in PRD.md Section 5. Reads what item 12's daily run
writes and judges it. It fetches nothing, calls no dependency agent, and
touches no network: everything it knows comes out of the two append-only
stores under state/, which is what makes it cheap enough to run on every pass
and honest enough to be worth reading.

What counts as a gap
--------------------
Not "the source returned nothing today". A source can answer every single day
with exactly the items it gave yesterday, and that is not health, it is a
content pipeline that has stopped producing while still answering the phone.
So freshness is measured from the last time an item was seen for the *first*
time. The store is append-only, so the earliest entry under an `entry_key` is
when that item first appeared, and the newest of those firsts is the last time
anything new arrived. That number is what the intervals in
`gap_expectations.yaml` are compared against.

The three silences, which are not the same silence
--------------------------------------------------
A source that contributed nothing has done one of three things, and they want
three different responses from a human:

  never asked   the runner has no record of asking it at all. Usually a
                source added to expectations and not to the runner, or a
                store that was lost. Nobody upstream is at fault.
  unconfigured  it was asked and could not be read for want of a credential.
                A five-minute fix, and the only one of the three that is
                ours. Reported at flag severity whatever the config says,
                because a source that cannot be read at all is worse than
                one that is merely quiet.
  quiet         it was asked, it answered, and it had nothing new. Only this
                one is a statement about the dependency agent.

`ingestion_log` cannot tell these apart, because it records items and only
items: all three leave the same trace in it, which is none. That is why
`daily_ingest.py` also writes `ingest_runs`, one entry per source per pass
saying whether it answered. Conflating an empty read with a failed one is the
precise failure `calendar_model/state_store.py` was built to prevent, and
this module is where that distinction either survives or is quietly lost.

One dead schedule is one finding, not four
------------------------------------------
When the daily workflow stops firing, every source goes silent on the same
day. A detector that reports four late dependency agents in that situation
has pointed the reader at four innocent teams and away from the actual
problem. So the run record's own age is checked first, against
`run_staleness_days`, and when the run itself is stale the per-source silence
findings are suppressed and labelled as untrustworthy rather than printed.
Findings that do not depend on the run being recent (unconfigured sources, a
missing monthly anchor) still stand.

Sources whose count moves both ways
-----------------------------------
`volatile: true` marks a source whose upstream row count legitimately falls:
the conference table is a directory, where rows are added in batches and
removed as events pass, and it went from 80 rows to 61 in five weeks with no
filter applied. For those, a drop in count is reported as an observation and
never as a gap on its own. Without this, gap detection would eventually
report the conference reader as broken on the strength of it working
correctly.

The monthly anchor
------------------
One source is the month's tentpole rather than a stream, and its expected
timing is editorial, so it is not restated here or in
`gap_expectations.yaml`. It is read from the `sequencing.monthly_anchor`
block of `narrative/threads.yaml`, which already names the source and says
"first week of month" and already says in its own note that a late one is why
gap detection raises a flag. The one number this module keeps in its own
config is the day of the month that "first week" means operationally, since
parsing that phrase out of English would be a fragile way to learn something
a config key can state.

What it deliberately does not do
--------------------------------
It does not fix anything, fetch a missing input, or decide what the calendar
should do about a gap. It reports. Synthesis (item 9) and the weekly refresh
(item 17) are what react, and neither exists yet. It also does not change any
exit code the daily workflow depends on: this is a separate entry point, and
it exits 0 having reported unless `--fail-on` is passed, so wiring it into CI
is an explicit decision about when the pipeline should go red rather than a
side effect of installing it.

Run it:

    ../.venv/bin/python3 gap_detection.py
    ../.venv/bin/python3 gap_detection.py --json
    ../.venv/bin/python3 gap_detection.py --record        # persist the flags
    ../.venv/bin/python3 gap_detection.py --fail-on flag  # non-zero if any
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - reported, never fatal
    yaml = None

_HERE = Path(__file__).resolve().parent
_AGENT_ROOT = _HERE.parent

if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

from calendar_model.state_store import (  # noqa: E402
    DEFAULT_STATE_ROOT,
    read_state,
    record_state,
)

DEFAULT_EXPECTATIONS_PATH = _HERE / "gap_expectations.yaml"
DEFAULT_TAXONOMY_PATH = _AGENT_ROOT / "narrative" / "threads.yaml"

DEFAULT_ITEM_STORE = "ingestion_log"
DEFAULT_RUN_STORE = "ingest_runs"
DEFAULT_GAP_STORE = "gaps"

# Worst first. Used for ordering a report and for resolving --fail-on, so the
# order is load-bearing rather than decorative.
SEVERITIES = ("tentpole", "flag", "note")

# Cadences a source can be given in the expectations file.
CADENCE_CONTINUOUS = "continuous"
CADENCE_MONTHLY_ANCHOR = "monthly_anchor"
CADENCE_DEFERRED = "deferred"

# Finding kinds. Named constants because the report, the JSON, the stored
# flags and the tests all key on them, and a typo in a string literal in one
# of those four places is a silent hole in the detector.
KIND_RUN_STALE = "run_stale"
KIND_NEVER_ASKED = "never_asked"
KIND_UNCONFIGURED = "unconfigured"
KIND_SOURCE_FAILED = "source_failed"
KIND_SILENT = "silent"
KIND_ANCHOR_MISSING = "anchor_missing"
KIND_COUNT_DROP = "count_drop"

# The kinds that mean this agent's own machinery is broken, as opposed to the
# kinds that mean the humans and sibling agents upstream have not written
# anything lately. The split exists because it is the only one a CI run can
# act on, and severity does not express it: a missing monthly anchor is
# tentpole-serious for the calendar and is not something a red build can fix.
#
# Wiring `--fail-on tentpole` into the daily run was considered and rejected
# on 2026-09-17. It would have gone red that morning, and every morning after,
# because Robin had not shipped the September briefing. A build that is red
# for a reason nobody in CI can act on is a build everyone learns to ignore,
# which costs more than never having wired it in.
PIPELINE_KINDS = ("run_stale", "never_asked", "unconfigured", "source_failed")

# What daily_ingest records in an ingest_runs entry's `status`.
STATUS_UNCONFIGURED = "unconfigured"
STATUS_FAILED = "failed"


def _warn(message: str) -> None:
    print(f"gap_detection: {message}", file=sys.stderr)


# ---------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------

@dataclass
class Finding:
    """One thing worth a human's attention, with the evidence that produced it.

    `detail` carries the numbers rather than baking them into `summary`, so a
    caller can render its own sentence and a stored flag stays machine-readable
    after the wording changes.
    """

    kind: str
    severity: str
    summary: str
    source: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "kind": self.kind,
            "severity": self.severity,
            "summary": self.summary,
        }
        if self.source:
            data["source"] = self.source
        if self.detail:
            data["detail"] = self.detail
        return data


@dataclass
class GapReport:
    """Everything one pass concluded, including what it could not conclude."""

    checked_at: str
    findings: list[Finding] = field(default_factory=list)
    observations: list[Finding] = field(default_factory=list)
    sources_checked: list[str] = field(default_factory=list)
    sources_deferred: list[str] = field(default_factory=list)
    suppressed: list[str] = field(default_factory=list)
    ok: bool = True

    @property
    def worst(self) -> str | None:
        for severity in SEVERITIES:
            if any(f.severity == severity for f in self.findings):
                return severity
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "checked_at": self.checked_at,
            "worst_severity": self.worst,
            "findings": [f.to_dict() for f in self.findings],
            "observations": [f.to_dict() for f in self.observations],
            "sources_checked": self.sources_checked,
            "sources_deferred": self.sources_deferred,
            "suppressed": self.suppressed,
            "ok": self.ok,
        }


# ---------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------

@dataclass
class Expectation:
    source: str
    cadence: str
    max_silence_days: int
    severity: str
    volatile: bool = False


@dataclass
class Expectations:
    sources: list[Expectation] = field(default_factory=list)
    run_staleness_days: int = 2
    anchor_source: str | None = None
    anchor_deadline_day: int = 7
    loaded: bool = False


def _config_int(config: dict, key: str, fallback: int, where: str) -> int:
    raw = config.get(key, fallback)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        _warn(f"{where}: {key!r} is not a number; using {fallback}.")
        return fallback
    if value <= 0:
        _warn(f"{where}: {key!r} must be positive; using {fallback}.")
        return fallback
    return value


def _severity(raw: Any, fallback: str, where: str) -> str:
    if raw is None:
        return fallback
    value = str(raw).strip().lower()
    if value not in SEVERITIES:
        _warn(f"{where}: severity {raw!r} is not one of {SEVERITIES}; using {fallback!r}.")
        return fallback
    return value


def _load_yaml(path: Path, what: str) -> dict:
    """A mapping from a YAML file, or an empty one with a warning.

    Never raises: this module runs behind a daily job, and a hand-edited
    config with a stray tab must degrade to a reported no-op rather than take
    the run down.
    """
    if yaml is None:
        _warn(f"pyyaml is not installed; cannot read {what}.")
        return {}
    if not path.is_file():
        _warn(f"no {what} at {path}.")
        return {}
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        _warn(f"could not parse {what} at {path} ({exc}).")
        return {}
    except OSError as exc:
        _warn(f"could not read {what} at {path} ({exc}).")
        return {}
    if not isinstance(loaded, dict):
        _warn(f"{what} at {path} is not a mapping.")
        return {}
    return loaded


def load_expectations(
    path: str | Path = DEFAULT_EXPECTATIONS_PATH,
    taxonomy_path: str | Path = DEFAULT_TAXONOMY_PATH,
) -> Expectations:
    """The expectations file, with the monthly anchor read from the taxonomy.

    Two files on purpose. Which source is the month's tentpole is editorial
    and already stated in `narrative/threads.yaml`'s `sequencing` block, so it
    is read from there rather than restated here where it could drift out of
    agreement with the calendar that is built off it. Only the operational
    reading of "first week of month", a day number, lives in this module's own
    config.
    """
    config = _load_yaml(Path(path), "gap expectations")
    result = Expectations()
    if not config:
        return result
    result.loaded = True

    defaults = config.get("defaults") or {}
    if not isinstance(defaults, dict):
        _warn("gap expectations: `defaults` is not a mapping; ignoring it.")
        defaults = {}
    default_silence = _config_int(defaults, "max_silence_days", 7, "gap expectations defaults")
    default_severity = _severity(
        defaults.get("severity"), "flag", "gap expectations defaults"
    )

    result.run_staleness_days = _config_int(
        config, "run_staleness_days", 2, "gap expectations"
    )
    result.anchor_deadline_day = _config_int(
        config, "anchor_deadline_day", 7, "gap expectations"
    )

    entries = config.get("sources")
    if not isinstance(entries, list):
        _warn("gap expectations: `sources` is not a list; nothing to check.")
        return result

    for raw in entries:
        if not isinstance(raw, dict):
            _warn(f"gap expectations: skipping a source entry that is not a mapping: {raw!r}")
            continue
        source = raw.get("id")
        if not source or not isinstance(source, str):
            _warn(f"gap expectations: skipping a source entry with no usable id: {raw!r}")
            continue
        cadence = str(raw.get("cadence") or CADENCE_CONTINUOUS).strip().lower()
        if cadence not in (CADENCE_CONTINUOUS, CADENCE_MONTHLY_ANCHOR, CADENCE_DEFERRED):
            _warn(
                f"gap expectations: {source} has cadence {cadence!r}, which is not "
                f"recognised; treating it as {CADENCE_CONTINUOUS!r}."
            )
            cadence = CADENCE_CONTINUOUS
        result.sources.append(
            Expectation(
                source=source,
                cadence=cadence,
                max_silence_days=_config_int(
                    raw, "max_silence_days", default_silence, f"gap expectations for {source}"
                ),
                severity=_severity(
                    raw.get("severity"), default_severity, f"gap expectations for {source}"
                ),
                volatile=bool(raw.get("volatile")),
            )
        )

    result.anchor_source = _anchor_source(Path(taxonomy_path))
    if result.anchor_source is None:
        anchored = [e.source for e in result.sources if e.cadence == CADENCE_MONTHLY_ANCHOR]
        if anchored:
            _warn(
                f"{anchored} expect a monthly anchor, but "
                f"`sequencing.monthly_anchor.source` was not readable from {taxonomy_path}; "
                "the anchor check is skipped rather than guessed at."
            )
    return result


def _anchor_source(taxonomy_path: Path) -> str | None:
    """Which source the taxonomy names as the month's anchor, or None."""
    doc = _load_yaml(taxonomy_path, "thread taxonomy")
    sequencing = doc.get("sequencing") if isinstance(doc.get("sequencing"), dict) else {}
    anchor = sequencing.get("monthly_anchor")
    if not isinstance(anchor, dict):
        return None
    source = anchor.get("source")
    return source if isinstance(source, str) and source else None


# ---------------------------------------------------------------------
# Reading the stores
# ---------------------------------------------------------------------

def _entry_time(entry: Any) -> str:
    """When the store wrote this entry down.

    `created_at`, not `event_at`, and the difference matters here. A backfill
    stamps `event_at` from the item's own month, so an item written yesterday
    can carry a date from June. The question this module asks is when *we*
    first saw a thing, which is write time.
    """
    return str(getattr(entry, "created_at", "") or getattr(entry, "event_at", "") or "")


def first_seen_by_key(entries: list) -> dict[str, str]:
    """Earliest write time per `entry_key`, which is when each item appeared.

    Free because the store is append-only: nothing is ever rewritten, so the
    oldest row under a key is the first sighting by construction.
    """
    firsts: dict[str, str] = {}
    for entry in entries:
        key = getattr(entry, "entry_key", None)
        if not key:
            continue
        stamp = _entry_time(entry)
        if not stamp:
            continue
        current = firsts.get(key)
        if current is None or stamp < current:
            firsts[key] = stamp
    return firsts


def _source_of(entry: Any) -> str | None:
    """Which source an ingestion_log entry belongs to.

    Prefers the explicit `source` in the value over splitting the key, since
    the value is what the runner wrote deliberately and the key's shape is a
    convention that could be widened later.
    """
    value = getattr(entry, "value", None)
    if isinstance(value, dict):
        source = value.get("source")
        if isinstance(source, str) and source:
            return source
    key = getattr(entry, "entry_key", None)
    if isinstance(key, str) and "/" in key:
        return key.split("/", 1)[0]
    return None


def last_new_item_per_source(entries: list) -> dict[str, str]:
    """Per source, the most recent *first* sighting of any of its items.

    This is the freshness number the intervals are compared against, and the
    reason it is not simply the newest entry: a source re-reporting yesterday's
    items every day would otherwise look permanently healthy.
    """
    firsts = first_seen_by_key(entries)
    by_key_source = {}
    for entry in entries:
        key = getattr(entry, "entry_key", None)
        if key and key not in by_key_source:
            source = _source_of(entry)
            if source:
                by_key_source[key] = source

    latest: dict[str, str] = {}
    for key, stamp in firsts.items():
        source = by_key_source.get(key)
        if not source:
            continue
        if source not in latest or stamp > latest[source]:
            latest[source] = stamp
    return latest


def _parse_ts(raw: str) -> datetime | None:
    if not raw:
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_days(stamp: str, now: datetime) -> float | None:
    moment = _parse_ts(stamp)
    if moment is None:
        return None
    return (now - moment).total_seconds() / 86400.0


# ---------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------

def detect_gaps(
    *,
    expectations: Expectations | None = None,
    expectations_path: str | Path = DEFAULT_EXPECTATIONS_PATH,
    taxonomy_path: str | Path = DEFAULT_TAXONOMY_PATH,
    item_store: str = DEFAULT_ITEM_STORE,
    run_store: str = DEFAULT_RUN_STORE,
    state_root: str | Path = DEFAULT_STATE_ROOT,
    now: datetime | None = None,
) -> GapReport:
    """Reads both stores, judges every configured source, returns the report.

    Never raises. An unreadable store, an unparseable config, or a source
    nobody has ever asked about all produce findings or warnings, because a
    detector that dies is indistinguishable from a detector that found
    nothing, and the second is what a green run is supposed to mean.
    """
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    report = GapReport(checked_at=moment.isoformat())

    if expectations is None:
        expectations = load_expectations(expectations_path, taxonomy_path)
    if not expectations.loaded or not expectations.sources:
        report.ok = False
        report.findings.append(
            Finding(
                kind=KIND_RUN_STALE,
                severity="tentpole",
                summary=(
                    "No usable expectations config, so nothing could be checked. "
                    "A silent detector reads exactly like a healthy one, which is "
                    "why this is reported rather than returned as no gaps."
                ),
                detail={"expectations_path": str(expectations_path)},
            )
        )
        return report

    items = read_state(item_store, root=state_root)
    runs = read_state(run_store, root=state_root, latest_per_key=True)

    last_new = last_new_item_per_source(items)
    run_by_source = {}
    for entry in runs:
        key = getattr(entry, "entry_key", None)
        if isinstance(key, str) and key:
            run_by_source[key] = entry

    # The run's own age, checked before any source is blamed for silence.
    run_stamps = [s for s in (_entry_time(e) for e in runs) if s]
    last_run = max(run_stamps) if run_stamps else None
    run_age = _age_days(last_run, moment) if last_run else None
    run_is_stale = run_age is None or run_age > expectations.run_staleness_days

    if not runs:
        report.findings.append(
            Finding(
                kind=KIND_RUN_STALE,
                severity="tentpole",
                summary=(
                    "No ingest run has ever been recorded, so no source can be "
                    "judged late. Either the daily run has never written the run "
                    "store or the store was lost."
                ),
                detail={"run_store": run_store, "state_root": str(state_root)},
            )
        )
    elif run_is_stale:
        report.findings.append(
            Finding(
                kind=KIND_RUN_STALE,
                severity="tentpole",
                summary=(
                    f"The last ingest run was {run_age:.1f} days ago, over the "
                    f"{expectations.run_staleness_days}-day limit. The run is the "
                    "finding: every source goes quiet when the schedule stops, so "
                    "per-source silence is not reported this pass."
                ),
                detail={
                    "last_run_at": last_run,
                    "age_days": round(run_age, 2),
                    "limit_days": expectations.run_staleness_days,
                },
            )
        )

    for expectation in expectations.sources:
        if expectation.cadence == CADENCE_DEFERRED:
            report.sources_deferred.append(expectation.source)
            continue
        report.sources_checked.append(expectation.source)
        _check_source(
            report,
            expectation,
            expectations=expectations,
            run_entry=run_by_source.get(expectation.source),
            last_new=last_new.get(expectation.source),
            items=items,
            now=moment,
            run_is_stale=run_is_stale,
            have_run_evidence=bool(runs),
        )

    report.findings.sort(key=lambda f: (SEVERITIES.index(f.severity), f.source or ""))
    return report


def _check_source(
    report: GapReport,
    expectation: Expectation,
    *,
    expectations: Expectations,
    run_entry: Any,
    last_new: str | None,
    items: list,
    now: datetime,
    run_is_stale: bool,
    have_run_evidence: bool,
) -> None:
    source = expectation.source

    if run_entry is None:
        if not have_run_evidence:
            # The run store is empty, so *nothing* was asked and every source
            # looks unasked for the same single reason, already reported once as
            # the run finding. Naming four sources here would be the same
            # misdirection the run-staleness rule exists to prevent, one level
            # down. The anchor check below still runs: it reads the item store
            # and does not depend on there being a run record.
            report.suppressed.append(source)
            if expectation.cadence == CADENCE_MONTHLY_ANCHOR:
                _check_anchor(
                    report, expectation, expectations=expectations, items=items, now=now
                )
            return
        report.findings.append(
            Finding(
                kind=KIND_NEVER_ASKED,
                severity=expectation.severity,
                summary=(
                    f"{source} is expected but the runner has no record of ever "
                    "asking it, though it has recorded asking others. Usually a "
                    "source added to expectations and not to the runner, and not a "
                    "statement about the source."
                ),
                source=source,
            )
        )
        return

    value = getattr(run_entry, "value", None) or {}
    status = str(value.get("status") or ("ok" if value.get("ok") else STATUS_FAILED))
    detail = value.get("detail")

    if status == STATUS_UNCONFIGURED:
        # Deliberately not expectation.severity. A source that cannot be read
        # at all is a worse state than one that is merely quiet, and the config
        # number describes patience with quietness.
        report.findings.append(
            Finding(
                kind=KIND_UNCONFIGURED,
                severity="flag",
                summary=(
                    f"{source} could not be read for want of configuration, so its "
                    "silence says nothing about the source. This is ours to fix."
                ),
                source=source,
                detail={"reported": detail} if detail else {},
            )
        )
        return

    # Anything that is neither ok nor empty means we could not find out what
    # the source holds. The readers gained finer statuses on 2026-09-17
    # (missing, unreachable, rejected) and each of them is a source failure
    # from this module's point of view: the difference between them is useful
    # to whoever fixes it and irrelevant to whether it is broken. Matching on
    # "not ok and not empty" rather than on a list is deliberate, so a status
    # added later fails loudly here instead of falling silently through.
    if status not in ("ok", "empty"):
        report.findings.append(
            Finding(
                kind=KIND_SOURCE_FAILED,
                severity=expectation.severity,
                summary=(
                    f"{source} was asked and could not be read ({status}), so nothing "
                    "new could arrive."
                ),
                source=source,
                detail={"reported": detail} if detail else {},
            )
        )
        return

    if expectation.volatile:
        _note_count_drop(report, expectation, run_entry, items)

    if expectation.cadence == CADENCE_MONTHLY_ANCHOR:
        _check_anchor(report, expectation, expectations=expectations, items=items, now=now)
        return

    # Silence, last, and only when the run itself is trustworthy.
    if run_is_stale:
        report.suppressed.append(source)
        return

    if last_new is None:
        report.findings.append(
            Finding(
                kind=KIND_SILENT,
                severity=expectation.severity,
                summary=(
                    f"{source} has been asked and has answered, but no item from it "
                    "has ever been recorded."
                ),
                source=source,
            )
        )
        return

    age = _age_days(last_new, now)
    if age is None:
        _warn(f"{source}: could not read {last_new!r} as a timestamp; skipping its silence check.")
        return
    if age > expectation.max_silence_days:
        report.findings.append(
            Finding(
                kind=KIND_SILENT,
                severity=expectation.severity,
                summary=(
                    f"Nothing new from {source} in {age:.1f} days, over its "
                    f"{expectation.max_silence_days}-day limit. It is answering; it "
                    "is not producing."
                ),
                source=source,
                detail={
                    "last_new_item_at": last_new,
                    "age_days": round(age, 2),
                    "limit_days": expectation.max_silence_days,
                },
            )
        )


def _note_count_drop(
    report: GapReport, expectation: Expectation, run_entry: Any, items: list
) -> None:
    """Records a fallen row count as an observation, never as a gap.

    Only for sources marked volatile, where the count falling is the source
    working correctly. The conference table is the case this exists for: it
    went from 80 rows to 61 in five weeks with no filter applied, because
    events pass and their rows are removed.
    """
    current = getattr(run_entry, "quantity", None)
    if not isinstance(current, int):
        return
    known = len({k for k in first_seen_by_key(items) if k.startswith(f"{expectation.source}/")})
    if known and current < known:
        report.observations.append(
            Finding(
                kind=KIND_COUNT_DROP,
                severity="note",
                summary=(
                    f"{expectation.source} returned {current} rows against "
                    f"{known} ever recorded. Expected for this source: rows are "
                    "removed upstream as events pass, and our store keeps them as "
                    "last-seen. Not a gap and not a broken read."
                ),
                source=expectation.source,
                detail={"returned": current, "ever_recorded": known},
            )
        )


def _check_anchor(
    report: GapReport,
    expectation: Expectation,
    *,
    expectations: Expectations,
    items: list,
    now: datetime,
) -> None:
    """Whether this month's tentpole has arrived, once its window has closed.

    Silent before the deadline day rather than reporting a not-yet-late input
    as late, which would train the reader to ignore the flag in the first week
    of every month, exactly when the anchor actually matters.
    """
    source = expectation.source
    if expectations.anchor_source and expectations.anchor_source != source:
        _warn(
            f"{source} is configured as the monthly anchor but the taxonomy names "
            f"{expectations.anchor_source!r}; checking {source} as configured and "
            "leaving the disagreement visible."
        )

    today = now.date()
    if today.day <= expectations.anchor_deadline_day:
        return

    month = f"{today.year:04d}-{today.month:02d}"
    for entry in items:
        if _source_of(entry) != source:
            continue
        value = getattr(entry, "value", None)
        if isinstance(value, dict) and str(value.get("month") or "") == month:
            return

    report.findings.append(
        Finding(
            kind=KIND_ANCHOR_MISSING,
            severity=expectation.severity,
            summary=(
                f"No {source} item for {month} has arrived, and the first week of "
                "the month has closed. This is the month's anchor, so the rest of "
                "the calendar is arranged around something that is not there yet."
            ),
            source=source,
            detail={
                "month": month,
                "deadline_day": expectations.anchor_deadline_day,
                "day_of_month": today.day,
            },
        )
    )


# ---------------------------------------------------------------------
# Persisting the flags
# ---------------------------------------------------------------------

def record_gaps(
    report: GapReport,
    *,
    store: str = DEFAULT_GAP_STORE,
    state_root: str | Path = DEFAULT_STATE_ROOT,
    now: datetime | None = None,
) -> dict:
    """Appends one entry per finding, so a flag outlives the run that raised it.

    Keyed `<kind>/<source>` so `latest_per_key=True` gives the current state of
    each distinct problem. A gap that persists for a week appends seven
    entries, which is the honest record: it was true on seven days, and git
    history then shows when it started and when it stopped.
    """
    entries = [
        {
            "action": finding.kind,
            "entry_key": f"{finding.kind}/{finding.source}" if finding.source else finding.kind,
            "value": finding.to_dict(),
        }
        for finding in report.findings
    ]
    if not entries:
        return {"stored": 0, "ok": True}
    return record_state(store, entries, root=state_root, now=now)


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------

def format_report(report: GapReport) -> str:
    lines: list[str] = []
    if report.findings:
        worst = report.worst
        lines.append(f"{len(report.findings)} finding(s), worst severity {worst}:")
        for finding in report.findings:
            lines.append(f"  [{finding.severity}] {finding.kind}: {finding.summary}")
    else:
        lines.append("No gaps found.")

    for observation in report.observations:
        lines.append(f"  [note] {observation.kind}: {observation.summary}")

    if report.sources_checked:
        lines.append(f"\nchecked:  {', '.join(report.sources_checked)}")
    if report.sources_deferred:
        lines.append(f"deferred: {', '.join(report.sources_deferred)}")
    if report.suppressed:
        lines.append(
            f"not judged this pass (the run is stale, not these sources): "
            f"{', '.join(report.suppressed)}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--expectations", type=Path, default=DEFAULT_EXPECTATIONS_PATH)
    parser.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY_PATH)
    parser.add_argument("--item-store", default=DEFAULT_ITEM_STORE)
    parser.add_argument("--run-store", default=DEFAULT_RUN_STORE)
    parser.add_argument("--gap-store", default=DEFAULT_GAP_STORE)
    parser.add_argument(
        "--state-root",
        type=Path,
        default=DEFAULT_STATE_ROOT,
        help=f"where the stores live (default: {DEFAULT_STATE_ROOT})",
    )
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument(
        "--record",
        action="store_true",
        help="append the findings to the gaps store so they outlive this run",
    )
    parser.add_argument(
        "--fail-on",
        choices=SEVERITIES,
        default=None,
        help=(
            "exit non-zero when a finding of this severity or worse is present. "
            "Off by default so installing this cannot turn the daily run red "
            "without someone deciding it should."
        ),
    )
    parser.add_argument(
        "--fail-on-kind",
        nargs="+",
        metavar="KIND",
        choices=(
            KIND_RUN_STALE, KIND_NEVER_ASKED, KIND_UNCONFIGURED, KIND_SOURCE_FAILED,
            KIND_SILENT, KIND_ANCHOR_MISSING, KIND_COUNT_DROP,
        ),
        default=None,
        help=(
            "exit non-zero when a finding of one of these kinds is present. Use this "
            "rather than --fail-on in CI: severity says how much a finding matters to "
            f"the calendar, and kind says whether anyone reading a build log can act "
            f"on it. {', '.join(PIPELINE_KINDS)} are the ones that mean this agent "
            "stopped working."
        ),
    )
    parser.add_argument(
        "--as-of",
        default=None,
        help="judge as though it were this ISO timestamp, for checking a past day",
    )
    args = parser.parse_args(argv)

    now = None
    if args.as_of:
        now = _parse_ts(args.as_of)
        if now is None:
            print(f"could not read --as-of {args.as_of!r} as a timestamp", file=sys.stderr)
            return 2

    report = detect_gaps(
        expectations_path=args.expectations,
        taxonomy_path=args.taxonomy,
        item_store=args.item_store,
        run_store=args.run_store,
        state_root=args.state_root,
        now=now,
    )

    if args.record:
        written = record_gaps(
            report, store=args.gap_store, state_root=args.state_root, now=now
        )
        if not written.get("ok"):
            _warn(f"could not append findings to {args.gap_store!r}; they were reported only.")

    print(json.dumps(report.to_dict(), indent=2) if args.json else format_report(report))

    if args.fail_on:
        worst = report.worst
        if worst and SEVERITIES.index(worst) <= SEVERITIES.index(args.fail_on):
            return 1
    if args.fail_on_kind:
        wanted = set(args.fail_on_kind)
        hit = [f for f in report.findings if f.kind in wanted]
        if hit:
            print(
                f"\nFailing: {len(hit)} finding(s) of "
                f"{', '.join(sorted(wanted))}.",
                file=sys.stderr,
            )
            for finding in hit:
                print(f"  [{finding.kind}] {finding.summary}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
