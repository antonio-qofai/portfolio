#!/usr/bin/env python3
"""The weekly calendar refresh: is the standing calendar still the right one?

Build order item 17 in PRD.md Section 5.

WHAT SITS BETWEEN THE DAILY RUN AND THE MONTHLY ONE

Before this, the agent accumulated daily and assembled monthly with nothing
in between, which meant a positioning shift arriving mid-month sat unapplied
for up to four weeks while the approved horizon kept publishing against a
stale arc. The 2026-08-13 repositioning is the case the founder call named.
This closes that gap: once a week it re-asks the question, and says what
changed.

IT PROPOSES, IT NEVER REWRITES

The standing window is never overwritten. A proposal is written beside it as
`<window>.proposed.json`, and `slots.newest_window_path` treats any window
carrying a further qualifier as not-a-calendar, so neither the page nor the
approval queue can mistake a proposal for the standing plan. This is not
tidiness. PRD Section 2 requires the near horizon to stay human-approved, and
a weekly job that rewrote the calendar in place would remove the thing that
was approved without anyone deciding to.

WHAT IS FIXED, AND WHY IT IS MORE THAN THE APPROVALS

Approved and published slots are fixed because a human agreed to them. Slots
dated before today are equally fixed, whatever their approval state, because
a date that has passed cannot be rescheduled and moving one rewrites history
rather than planning. The refresh cannot express that second case by marking
those slots approved, since writing an approval nobody gave is the single
failure this build is arranged to prevent, so it is stated in the prompt and
then verified on the way back. A proposal that moves a fixed slot is refused
outright rather than repaired, because a proposal that broke the approval
guarantee is not something anyone can act on, and silently restoring the slot
would hide that the model was told and did it anyway.

IT DOES NOT CALL THE MODEL UNLESS SOMETHING CHANGED

Re-proposing a calendar nothing has changed is worse than not running. It
spends a request to produce a different-but-equivalent ordering and moves
posts under someone who has already read the reasons for where they were.
So the triggers in `refresh_policy.yaml` decide, the report names which
fired, and a week where nothing changed costs nothing and still reports.

THE SUPPLY HALF IS FREE AND PRINTS EVERY WEEK

The founder call asked for the state of content behind the calendar: which
drafts exist, whether they are ordered, and whether anything should be
slotted in. That is arithmetic over the corpus and the window, so it needs no
model and runs whether or not a proposal is made. It is the half most likely
to matter at the moment, since the September window consumed the entire
schedulable corpus.

Usage:

    ../.venv/bin/python3 refresh.py                  # report, propose if warranted
    ../.venv/bin/python3 refresh.py --report-only    # never call the model
    ../.venv/bin/python3 refresh.py --force          # propose regardless of triggers
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - reported, not raised
    yaml = None

_HERE = Path(__file__).resolve().parent
_AGENT_ROOT = _HERE.parent

sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_AGENT_ROOT))
sys.path.insert(0, str(_AGENT_ROOT / "calendar_model"))
sys.path.insert(0, str(_AGENT_ROOT / "ingestion"))
sys.path.insert(0, str(_AGENT_ROOT / "approval"))

import assemble as assemble_mod  # noqa: E402
import slots as slots_mod  # noqa: E402
import state_store  # noqa: E402
from slots import APPROVED, PUBLISHED, CalendarWindow, Issue, Taxonomy  # noqa: E402

DEFAULT_POLICY_PATH = _HERE / "refresh_policy.yaml"
DEFAULT_INGESTION_STORE = "ingestion_log"

# The states a human has committed to. Anything here is fixed for the same
# reason the approval queue refuses to write one: it represents a decision
# somebody made, and a weekly job does not get to undo it.
_COMMITTED = (APPROVED, PUBLISHED)


def _warn(message: str) -> None:
    print(f"refresh: {message}", file=sys.stderr)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

def load_policy(path: str | Path = DEFAULT_POLICY_PATH) -> dict:
    """Reads the refresh policy. Returns {} with a warning rather than raising."""
    path = Path(path)
    if yaml is None:
        _warn("pyyaml is not installed, so the refresh policy could not be read.")
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle) or {}
    except FileNotFoundError:
        _warn(f"no refresh policy at {path}.")
        return {}
    except (OSError, yaml.YAMLError) as exc:
        _warn(f"could not read the refresh policy at {path}: {exc}")
        return {}
    return loaded if isinstance(loaded, dict) else {}


def validate_policy(policy: dict) -> list:
    """Problems that make a policy unusable, as a list of sentences.

    A refresh with an unreadable policy must not fall back to defaults in
    code. The whole point of the file is that the thresholds are visible, and
    a silent default would make a run that proposed nothing indistinguishable
    from a run that decided not to.
    """
    problems = []
    if not policy:
        return ["the refresh policy is empty or could not be read"]
    triggers = policy.get("triggers")
    if not isinstance(triggers, dict) or not triggers:
        problems.append("the policy states no triggers, so nothing could ever fire")
    else:
        for name, config in triggers.items():
            if not isinstance(config, dict):
                problems.append(f"trigger {name!r} is not a mapping")
    output = policy.get("output")
    if not isinstance(output, dict) or not output.get("proposal_suffix"):
        problems.append("the policy states no output.proposal_suffix, so a proposal has nowhere to go")
    return problems


def _trigger(policy: dict, name: str) -> dict:
    triggers = policy.get("triggers")
    if not isinstance(triggers, dict):
        return {}
    config = triggers.get(name)
    return config if isinstance(config, dict) else {}


def _enabled(policy: dict, name: str) -> bool:
    return bool(_trigger(policy, name).get("enabled"))


# ---------------------------------------------------------------------------
# What has changed since the window was written
# ---------------------------------------------------------------------------

@dataclass
class Signals:
    """What has happened since the standing window was assembled."""

    written_at: str = ""
    arrived: list = field(default_factory=list)          # item_refs first seen since
    arrived_schedulable: list = field(default_factory=list)
    declined: list = field(default_factory=list)         # slot_ids declined since
    approved: list = field(default_factory=list)         # slot_ids approved since
    narrative_changed: list = field(default_factory=list)  # document names whose content moved
    digests: dict = field(default_factory=dict)            # this run's content digests
    days_left: int | None = None
    notes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "written_at": self.written_at,
            "arrived": list(self.arrived),
            "arrived_schedulable": list(self.arrived_schedulable),
            "declined": list(self.declined),
            "approved": list(self.approved),
            "narrative_changed": list(self.narrative_changed),
            "digests": dict(self.digests),
            "days_left": self.days_left,
            "notes": list(self.notes),
        }


def _window_written_at(standing: CalendarWindow) -> str:
    """When the standing window was assembled, as an ISO timestamp.

    Falls back to the window's start date when `generated_at` is absent,
    which is true of a hand-built window. A missing timestamp must not make
    everything look new, because "everything arrived since" would fire every
    trigger on every run.
    """
    stamp = (standing.generated_at or "").strip()
    if stamp:
        return stamp
    if standing.start_date:
        return f"{standing.start_date}T00:00:00Z"
    return ""


def changes_since(
    standing: CalendarWindow,
    *,
    schedulable: set,
    entries: list,
    decisions: dict,
    narrative_paths: list = (),
    baseline: dict | None = None,
    today: str | date | None = None,
) -> Signals:
    """Everything the triggers might fire on, gathered once.

    `entries` are ingestion_log rows and `decisions` is what
    `state_store.latest_decisions` returns, so this reads the two stores the
    agent already keeps rather than any record of its own. The refresh holds
    no state: what changed is always derived from when the window was written
    against what the stores say, which means a re-run answers the same way
    and a missed week is not a gap in anything.
    """
    signals = Signals(written_at=_window_written_at(standing))
    if not signals.written_at:
        signals.notes.append(
            "the standing window carries no generated_at and no start date, so nothing "
            "can be dated against it and no arrival or decision counts as new"
        )
        return signals

    try:
        import gap_detection
    except Exception as exc:  # pragma: no cover - reported, not raised
        _warn(f"could not load gap detection ({exc}); arrivals will not be counted.")
        signals.notes.append("arrivals could not be read, so new content did not count")
    else:
        firsts = gap_detection.first_seen_by_key(entries)
        for item_ref, first in sorted(firsts.items()):
            if _after(first, signals.written_at):
                signals.arrived.append(item_ref)
                if item_ref in schedulable:
                    signals.arrived_schedulable.append(item_ref)

    for slot_id, value in sorted((decisions or {}).items()):
        if not isinstance(value, dict):
            continue
        when = str(value.get("at") or "")
        if when and not _after(when, signals.written_at):
            continue
        state = value.get("state")
        if state == slots_mod.REJECTED:
            signals.declined.append(slot_id)
        elif state in _COMMITTED:
            signals.approved.append(slot_id)

    signals.digests = document_digests(narrative_paths)
    if not baseline:
        # Nothing to compare against yet. Recording the digests is this run's
        # useful work, and claiming every document changed would be the exact
        # false positive the digests exist to remove.
        if signals.digests:
            signals.notes.append(
                f"recorded a first digest for {len(signals.digests)} narrative "
                "document(s); a change can only be seen from the next run on"
            )
    else:
        for name, digest in sorted(signals.digests.items()):
            was = baseline.get(name)
            if was is not None and was != digest:
                signals.narrative_changed.append(name)
        for name in sorted(set(baseline) - set(signals.digests)):
            signals.narrative_changed.append(name)

    reference = slots_mod.as_date(today) or datetime.now(timezone.utc).date()
    end = slots_mod.as_date(standing.end_date)
    if end is not None:
        signals.days_left = (end - reference).days
    return signals


def last_run(window_id: str, *, store: str, root: str | Path) -> dict:
    """The `value` of the newest recorded refresh for this window, or {}.

    Runs are keyed by window_id, so a new window starts with no history and
    therefore no baseline, which is correct: a freshly assembled calendar has
    nothing to have drifted from yet.
    """
    for row in state_store.read_state(store, root=root, entry_key=window_id, limit=1):
        value = row.value if isinstance(row.value, dict) else {}
        return value
    return {}


def baseline_digests(previous: dict, written_at: str) -> dict:
    """The digests to compare this run against, from the last recorded run.

    Empty when the standing window has been reassembled since that run, which
    resets the comparison rather than measuring today's narrative against a
    baseline taken for a calendar that no longer exists.
    """
    if not isinstance(previous, dict):
        return {}
    signals = previous.get("signals")
    if not isinstance(signals, dict):
        return {}
    if written_at and signals.get("written_at") and signals["written_at"] != written_at:
        return {}
    digests = signals.get("digests")
    return dict(digests) if isinstance(digests, dict) else {}


def document_digests(paths) -> dict:
    """name -> sha256 of each readable document, for comparing content.

    Not modification time, which is the obvious answer and is wrong in the
    place this job actually runs. Git leaves a file's mtime alone on a
    working copy unless the content changes, so mtime looks correct on a
    laptop. A CI checkout writes every file fresh, so every document's mtime
    is the checkout time and every file reads as "just changed". Wired to a
    cron that way, this trigger would have fired on every single run and the
    refresh would have called the model every week regardless, which is the
    churn and the unattended spend the triggers exist to prevent, wearing
    the costume of a working design.

    Keyed by file name rather than by full path, because the absolute path
    differs between a laptop and a runner and a baseline recorded on one has
    to be readable by the other.

    An unreadable file is skipped with a warning rather than recorded as a
    digest of nothing, since a digest of nothing would compare unequal to
    its real predecessor and report a change that did not happen.
    """
    digests = {}
    for path in paths or ():
        path = Path(path)
        try:
            digests[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            _warn(f"could not read {path} ({exc}); it is not in this run's digests.")
    return digests


def _after(stamp: str, reference: str) -> bool:
    """Is `stamp` later than `reference`, comparing instants rather than text.

    The two do not agree on spelling. `state_store` writes an offset
    (`2026-09-07T18:55:39+00:00`) and several callers write the Z form for the
    same instant, and "Z" sorts after "+" in ASCII, so a string comparison of
    two timestamps in the same second answers by format rather than by time.
    The dates usually differ by days here and would compare correctly by
    accident, which is exactly why it is worth not relying on.

    An unreadable stamp is treated as later than the reference, on the same
    reasoning as `_as_timestamp`: over-reporting a change costs one model
    call, and under-reporting it is the blind spot this module closes.
    """
    left, right = _as_timestamp(stamp), _as_timestamp(reference)
    if left == 0.0:
        return True
    return left > right


def _as_timestamp(stamp: str) -> float:
    """An ISO timestamp as epoch seconds, for comparing against st_mtime.

    Returns 0.0 on anything unreadable, which makes every file look changed
    rather than none. A refresh that over-reports a narrative change costs one
    model call; one that under-reports it is the four-week blind spot this
    module was built to close.
    """
    try:
        text = stamp.strip().replace("Z", "+00:00")
        moment = datetime.fromisoformat(text)
    except (AttributeError, ValueError):
        return 0.0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


# ---------------------------------------------------------------------------
# The state of content behind the calendar
# ---------------------------------------------------------------------------

@dataclass
class Supply:
    """What is behind the window, which is the founder's second ask.

    Scheduled, unscheduled, and what that means in weeks. No model call: this
    is arithmetic over the corpus and the window, so it reports every run.
    """

    corpus: int = 0
    scheduled: list = field(default_factory=list)
    unscheduled: list = field(default_factory=list)
    arrived_unscheduled: list = field(default_factory=list)
    posts_per_week: float = 0.0
    runway_weeks: float | None = None
    warn_below_weeks: float | None = None

    @property
    def thin(self) -> bool:
        return (
            self.runway_weeks is not None
            and self.warn_below_weeks is not None
            and self.runway_weeks < self.warn_below_weeks
        )

    def to_dict(self) -> dict:
        return {
            "corpus": self.corpus,
            "scheduled": len(self.scheduled),
            "unscheduled": list(self.unscheduled),
            "arrived_unscheduled": list(self.arrived_unscheduled),
            "posts_per_week": self.posts_per_week,
            "runway_weeks": self.runway_weeks,
            "thin": self.thin,
        }


def supply_report(
    standing: CalendarWindow,
    schedulable: set,
    signals: Signals,
    *,
    cadence: dict,
    policy: dict,
) -> Supply:
    """How much content stands behind the calendar, and for how long.

    Runway is unscheduled items divided by the cadence, which answers "how
    many more weeks could we fill if nothing else arrived". The cadence comes
    from the sequencing criteria rather than from here, because two modules
    holding a posts-per-week number is how they come to disagree.
    """
    scheduled = {slot.item_ref for slot in standing.slots if slot.item_ref}
    unscheduled = sorted(schedulable - scheduled)
    per_week = float(
        cadence.get("posts_per_week_max")
        or cadence.get("posts_per_week_min")
        or 0
    )
    supply = Supply(
        corpus=len(schedulable),
        scheduled=sorted(scheduled & schedulable),
        unscheduled=unscheduled,
        arrived_unscheduled=[r for r in signals.arrived_schedulable if r not in scheduled],
        posts_per_week=per_week,
        runway_weeks=round(len(unscheduled) / per_week, 1) if per_week else None,
    )
    warn = (policy.get("supply") or {}).get("runway_warn_weeks")
    supply.warn_below_weeks = float(warn) if isinstance(warn, (int, float)) else None
    return supply


# ---------------------------------------------------------------------------
# Whether to ask the model again
# ---------------------------------------------------------------------------

@dataclass
class Verdict:
    """Whether a re-proposal is warranted, and the reasons either way."""

    propose: bool = False
    fired: list = field(default_factory=list)     # (trigger, sentence)
    quiet: list = field(default_factory=list)     # sentences about what did not change

    def to_dict(self) -> dict:
        return {
            "propose": self.propose,
            "fired": [name for name, _ in self.fired],
            "reasons": [sentence for _, sentence in self.fired],
        }


def should_propose(signals: Signals, supply: Supply, policy: dict, *, force: bool = False) -> Verdict:
    """Reads the policy against the signals. Holds no threshold of its own."""
    verdict = Verdict()

    if force:
        verdict.propose = True
        verdict.fired.append(("forced", "asked for on the command line, overriding the triggers"))
        return verdict

    if policy.get("always"):
        verdict.propose = True
        verdict.fired.append(("always", "the policy re-proposes on every run"))
        return verdict

    name = "new_schedulable_content"
    if _enabled(policy, name):
        minimum = int(_trigger(policy, name).get("minimum") or 1)
        count = len(signals.arrived_schedulable)
        if count >= minimum:
            verdict.fired.append((
                name,
                f"{count} schedulable item(s) arrived since the window was written, "
                f"at or over the {minimum} the policy asks for: "
                + ", ".join(signals.arrived_schedulable),
            ))
        else:
            verdict.quiet.append(
                f"new content: {count} arrived, the policy wants {minimum}"
            )

    name = "content_declined"
    if _enabled(policy, name):
        if signals.declined:
            verdict.fired.append((
                name,
                f"{len(signals.declined)} slot(s) declined since the window was written, "
                f"leaving their dates empty: " + ", ".join(signals.declined),
            ))
        else:
            verdict.quiet.append("declines: none since the window was written")

    name = "narrative_changed"
    if _enabled(policy, name):
        if signals.narrative_changed:
            verdict.fired.append((
                name,
                f"{len(signals.narrative_changed)} narrative document(s) changed since the "
                "window was written: " + ", ".join(
                    Path(p).name for p in signals.narrative_changed
                ),
            ))
        else:
            verdict.quiet.append("narrative: unchanged since the window was written")

    # Reported, never proposed on. Assembling the next window is the monthly
    # assembly's job, and a refresh that quietly started producing next month's
    # calendar would make two different jobs indistinguishable in the log.
    name = "window_running_out"
    if _enabled(policy, name):
        threshold = _trigger(policy, name).get("days_left_at_or_below")
        if (
            isinstance(threshold, (int, float))
            and signals.days_left is not None
            and signals.days_left <= threshold
        ):
            verdict.quiet.append(
                f"the standing window ends in {signals.days_left} day(s), at or under the "
                f"{int(threshold)} the policy flags: the next one should already be assembled "
                "(monthly_assembly.py's job, not this one's); if it is not, that job has "
                "not run"
            )

    verdict.propose = bool(verdict.fired)
    return verdict


# ---------------------------------------------------------------------------
# The guarantee, verified rather than trusted
# ---------------------------------------------------------------------------

def fixed_slots(standing: CalendarWindow, today: str | date) -> dict:
    """slot_id -> (date, item_ref) for every slot a refresh may not move.

    Two reasons a slot is fixed, and they are different in kind. A human
    committed to it, or its date has already passed. The second is not
    expressible as an approval state and must not be written as one, so it
    lives here as a rule about time.
    """
    reference = slots_mod.as_date(today)
    fixed = {}
    for slot in standing.slots:
        target = slots_mod.as_date(slot.date)
        past = reference is not None and target is not None and target < reference
        if slot.approval in _COMMITTED or past:
            fixed[slot.slot_id] = (slot.date, slot.item_ref)
    return fixed


def reconcile_fixed(
    standing: CalendarWindow, proposal: CalendarWindow, today: str | date
) -> tuple:
    """Carries each fixed slot's identity onto the proposal, or says why it cannot.

    Returns `(reconciled, issues)`. Issues are `slots.Issue` so a caller
    reports them the way it reports any other validation finding, and they are
    errors rather than warnings: the proposal is asking to change something a
    person already agreed to, or something that has already happened, and a
    weekly job may do neither on its own.

    Why this matches on (date, item_ref) rather than on slot_id, which is the
    obvious key and the wrong one. `assemble.window_from_payload` builds ids
    positionally, as `<window_id>-<n>`, so the same post gets a different id
    the moment the ordering changes. Keying on the id would report every fixed
    slot as dropped on every refresh. The deeper problem is the same fact seen
    from the other side: approvals are recorded against slot_id, so an
    unreconciled re-assembly would slide an approval of the third post onto
    whatever post came third next time. That is the approval transferring to
    content nobody approved, arriving through a different door than the one
    the approval queue guards.

    So a fixed slot is identified by what it actually is, the post and the
    date, and the reconciled proposal carries the standing slot's id and
    approval state forward onto it. Without that carry-forward a proposal
    would come back with every slot marked draft, since a fresh assembly has
    no way to know otherwise, and adopting it would silently un-approve
    everything Jordan had signed off.
    """
    issues: list = []
    reconciled = CalendarWindow.from_dict(proposal.to_dict())
    by_ref: dict = {}
    by_date: dict = {}
    for index, slot in enumerate(reconciled.slots):
        by_ref.setdefault(slot.item_ref, []).append(index)
        by_date.setdefault(slot.date, []).append(index)

    taken: set = set()
    carried: dict = {}
    for slot in standing.slots:
        if slot.slot_id not in fixed_slots(standing, today):
            continue
        match = next(
            (
                i for i in by_date.get(slot.date, [])
                if i not in taken and reconciled.slots[i].item_ref == slot.item_ref
            ),
            None,
        )
        if match is not None:
            taken.add(match)
            carried[match] = slot
            continue

        elsewhere = [i for i in by_ref.get(slot.item_ref, []) if i not in taken]
        other_ref = [i for i in by_date.get(slot.date, []) if i not in taken]
        if elsewhere:
            issues.append(Issue(
                "fixed-slot-moved",
                slot.slot_id,
                f"{slot.item_ref} is fixed on {slot.date} and the proposal dates it "
                f"{reconciled.slots[elsewhere[0]].date}",
            ))
        elif other_ref:
            issues.append(Issue(
                "fixed-slot-repointed",
                slot.slot_id,
                f"{slot.date} is fixed on {slot.item_ref} and the proposal points it at "
                f"{reconciled.slots[other_ref[0]].item_ref}",
            ))
        else:
            issues.append(Issue(
                "fixed-slot-dropped",
                slot.slot_id,
                f"{slot.item_ref} is fixed on {slot.date} and the proposal does not hold it",
            ))

    # Carry the standing identity across, then make sure no slot the proposal
    # numbered for itself now collides with one of those. Ids have to stay
    # unique inside a window: the approval store is keyed by them, and two
    # slots sharing one would make a decision ambiguous rather than merely wrong.
    for index, slot in carried.items():
        reconciled.slots[index].slot_id = slot.slot_id
        reconciled.slots[index].approval = slot.approval
    used = {reconciled.slots[i].slot_id for i in carried}
    counter = 0
    for index, slot in enumerate(reconciled.slots):
        if index in carried:
            continue
        if slot.slot_id not in used:
            used.add(slot.slot_id)
            continue
        while True:
            counter += 1
            candidate = f"{reconciled.window_id}-r{counter:02d}"
            if candidate not in used:
                break
        slot.slot_id = candidate
        used.add(candidate)

    return reconciled, issues


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

@dataclass
class Outcome:
    """One refresh run, in the shape the report and the store both read."""

    standing_path: str = ""
    window_id: str = ""
    signals: Signals = field(default_factory=Signals)
    supply: Supply = field(default_factory=Supply)
    verdict: Verdict = field(default_factory=Verdict)
    proposal_path: str = ""
    violations: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    failed: str = ""

    @property
    def ok(self) -> bool:
        return not (self.violations or self.errors or self.failed)

    def to_entry(self, *, as_of: str) -> dict:
        """The state-store row for this run."""
        return {
            "action": "refresh",
            "entry_key": self.window_id or "unknown-window",
            "value": {
                "as_of": as_of,
                "standing": self.standing_path,
                "signals": self.signals.to_dict(),
                "supply": self.supply.to_dict(),
                "verdict": self.verdict.to_dict(),
                "proposal": self.proposal_path,
                "violations": [str(i) for i in self.violations],
                "errors": [str(i) for i in self.errors],
                "failed": self.failed,
            },
        }


def format_report(outcome: Outcome, *, as_of: str) -> str:
    """The whole run as text, in the order a person reads it."""
    supply, signals, verdict = outcome.supply, outcome.signals, outcome.verdict
    lines = [
        f"Refresh of {outcome.window_id} as of {as_of}",
        f"  standing calendar: {outcome.standing_path}",
        f"  written: {signals.written_at or 'unknown'}",
    ]
    if signals.days_left is not None:
        lines.append(f"  ends in {signals.days_left} day(s)")
    for note in signals.notes:
        lines.append(f"  note: {note}")

    lines += ["", "Content behind the calendar"]
    lines.append(f"  {supply.corpus} schedulable, {len(supply.scheduled)} scheduled, "
                 f"{len(supply.unscheduled)} unscheduled")
    if supply.runway_weeks is not None:
        at = f" at {supply.posts_per_week:g} posts a week" if supply.posts_per_week else ""
        lines.append(f"  runway {supply.runway_weeks:g} week(s){at}")
        if supply.thin:
            lines.append(
                f"  THIN: under the {supply.warn_below_weeks:g} week(s) the policy warns at. "
                "The calendar is consuming the corpus faster than it is refilled."
            )
    for ref in supply.unscheduled:
        lines.append(f"    unscheduled: {ref}")
    for ref in supply.arrived_unscheduled:
        lines.append(f"    arrived since, not scheduled: {ref}")

    lines += ["", "What changed"]
    if verdict.fired:
        for name, sentence in verdict.fired:
            lines.append(f"  [{name}] {sentence}")
    for sentence in verdict.quiet:
        lines.append(f"  quiet: {sentence}")
    if not verdict.fired and not verdict.quiet:
        lines.append("  nothing the policy watches")

    lines += [""]
    if outcome.failed:
        lines.append(f"Proposal failed: {outcome.failed}")
    elif outcome.violations:
        lines.append(
            f"Proposal REFUSED: it moved {len(outcome.violations)} slot(s) that are fixed."
        )
        for issue in outcome.violations:
            lines.append(f"  {issue}")
        if outcome.proposal_path:
            lines.append(f"  kept for inspection at {outcome.proposal_path}")
    elif outcome.errors:
        lines.append(f"Proposal REFUSED: {len(outcome.errors)} validation error(s).")
        for issue in outcome.errors:
            lines.append(f"  {issue}")
        if outcome.proposal_path:
            lines.append(f"  kept for inspection at {outcome.proposal_path}")
    elif outcome.proposal_path:
        lines.append(f"Proposed: {outcome.proposal_path}")
        lines.append("  Nothing is approved by this and the standing calendar is unchanged.")
    elif verdict.propose:
        lines.append("A proposal was warranted but none was made (report only).")
    else:
        lines.append("No proposal. Nothing the policy watches has changed.")
    return "\n".join(lines)


def refresh(
    standing: CalendarWindow,
    standing_path: str | Path,
    *,
    criteria: dict,
    taxonomy: Taxonomy,
    policy: dict,
    lens: str,
    candidates: list,
    context,
    entries: list,
    decisions: dict,
    narrative_paths: list = (),
    previous: dict | None = None,
    today: str | date | None = None,
    force: bool = False,
    report_only: bool = False,
    caller=None,
) -> Outcome:
    """Reports, and proposes when the policy says a re-proposal is warranted.

    Everything it reads is passed in, so a test drives a whole run without a
    checkout, a store, or a key. `caller` is handed to `assemble` untouched
    and is the only thing here that can spend money.
    """
    today = slots_mod.as_date(today) or datetime.now(timezone.utc).date()
    schedulable = {c.item_ref for c in candidates}
    outcome = Outcome(
        standing_path=str(standing_path),
        window_id=standing.window_id,
    )
    outcome.signals = changes_since(
        standing,
        schedulable=schedulable,
        entries=entries,
        decisions=decisions,
        narrative_paths=narrative_paths,
        baseline=baseline_digests(previous or {}, _window_written_at(standing)),
        today=today,
    )
    outcome.supply = supply_report(
        standing,
        schedulable,
        outcome.signals,
        cadence=criteria.get("cadence") or {},
        policy=policy,
    )
    outcome.verdict = should_propose(outcome.signals, outcome.supply, policy, force=force)

    if not outcome.verdict.propose or report_only:
        return outcome

    try:
        proposal = assemble_mod.assemble(
            criteria,
            taxonomy,
            lens,
            standing.start_date,
            candidates,
            context,
            standing=standing,
            as_of=today,
            **({"caller": caller} if caller is not None else {}),
        )
    except assemble_mod.AssemblyError as exc:
        outcome.failed = str(exc)
        return outcome

    proposal, outcome.violations = reconcile_fixed(standing, proposal, today)
    issues = slots_mod.validate_window(proposal, taxonomy, item_refs=schedulable)
    outcome.errors = slots_mod.errors(issues)

    suffix = (policy.get("output") or {}).get("proposal_suffix") or ".proposed"
    target = Path(standing_path).with_suffix(f"{suffix}.json")
    outcome.proposal_path = str(slots_mod.write_window(target, proposal))
    return outcome


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Weekly refresh: is the standing calendar still the right one?"
    )
    parser.add_argument("--window", default=None,
                        help="standing window (default: every live window for --lens in state/)")
    parser.add_argument("--lens", default=None,
                        help="which calendar to refresh (default: the taxonomy's v1 lens)")
    parser.add_argument("--policy", default=str(DEFAULT_POLICY_PATH))
    parser.add_argument("--criteria", default=str(assemble_mod.DEFAULT_CRITERIA_PATH))
    parser.add_argument("--threads", default=str(slots_mod.DEFAULT_TAXONOMY_PATH))
    parser.add_argument("--tags", default=str(assemble_mod.DEFAULT_TAGS_PATH))
    parser.add_argument("--atomizer", default=str(assemble_mod.DEFAULT_ATOMIZER_PATH))
    parser.add_argument("--vcb", default=str(assemble_mod.DEFAULT_VCB_PATH))
    parser.add_argument("--state-root", default=str(state_store.DEFAULT_STATE_ROOT))
    parser.add_argument("--as-of", default=None, help="reference date (default: today UTC)")
    parser.add_argument("--force", action="store_true",
                        help="propose regardless of the triggers")
    parser.add_argument("--report-only", action="store_true",
                        help="report what changed and never call the model")
    parser.add_argument("--all-lenses", action="store_true",
                        help="refresh every calendar the taxonomy declares live, in turn")
    args = parser.parse_args(argv)

    # Every live calendar, one after another. This lives here rather than as a
    # shell loop in the weekly workflow so that the list of calendars stays in
    # threads.yaml, where every other module reads it from. A workflow naming
    # the three lenses would be a fourth place to remember when a lens is
    # added, and the one place nobody would think to look.
    #
    # A lens that fails does not stop the ones after it. Refusing a proposal
    # for the Jordan calendar is a finding worth reading, and it is not a
    # reason for the Blake calendar to go unrefreshed for a week. The run
    # still reports failure, so a red job still means look at this.
    if args.all_lenses:
        taxonomy = slots_mod.load_taxonomy(args.threads)
        lenses = list(taxonomy.v1_lens_order)
        if not lenses:
            print(f"Cannot refresh: {args.threads} declares no live calendar.", file=sys.stderr)
            return 1
        passed = [a for a in (argv if argv is not None else sys.argv[1:]) if a != "--all-lenses"]
        worst = 0
        for lens in lenses:
            print(f"\n{'=' * 64}\n{lens} calendar\n{'=' * 64}")
            worst = max(worst, main(passed + ["--lens", lens]))
        print(f"\n{len(lenses)} calendar(s) refreshed; "
              f"{'all reported cleanly' if worst == 0 else 'at least one needs reading'}.")
        return worst

    policy = load_policy(args.policy)
    problems = validate_policy(policy)
    if problems:
        for problem in problems:
            _warn(problem)
        print("Cannot refresh: the refresh policy is not usable.", file=sys.stderr)
        return 1

    criteria = assemble_mod.load_criteria(args.criteria)
    if assemble_mod.validate_criteria(criteria):
        for problem in assemble_mod.validate_criteria(criteria):
            _warn(problem)
        print("Cannot refresh: the sequencing policy is not usable.", file=sys.stderr)
        return 1

    taxonomy = slots_mod.load_taxonomy(args.threads)
    lens = assemble_mod.resolve_lens(criteria, taxonomy, args.lens)
    if not lens:
        print("Cannot refresh: no calendar lens.", file=sys.stderr)
        return 1
    criteria = assemble_mod.criteria_for_lens(criteria, lens)

    # The lens resolved above is what picks the standing windows. With three
    # calendars on disk a lens-blind lookup refreshes one of them and leaves
    # the other two silently stale, which reads as a working schedule right
    # up to the month nobody re-checked.
    #
    # Every live window for the lens, not the newest and not only the one
    # covering today (2026-09-18). From the day next month is assembled a
    # lens has two, and next month is the one most likely to be empty or
    # thin, which is exactly where newly arrived content matters most: an
    # empty November refreshed only in mid-November would already be the
    # current month. Each window is its own run, with its own triggers and
    # its own run record keyed by window_id, so a trigger firing for one
    # never pays for a re-sequence of the other, and a quiet week stays free.
    # reconcile_fixed and respect-approved-slots apply inside each run,
    # unchanged, because each run only ever sees its own window.
    if args.window:
        standing_path = Path(args.window)
    else:
        as_of_day = args.as_of or datetime.now(timezone.utc).date().isoformat()
        live = slots_mod.live_window_paths(Path(args.state_root), lens=lens, today=as_of_day)
        if not live:
            _warn(
                f"no standing window for the {lens} lens under {args.state_root} covers "
                f"{as_of_day} or later; run monthly_assembly.py first."
            )
            return 1
        if len(live) > 1:
            passed = list(argv if argv is not None else sys.argv[1:])
            worst = 0
            for path in live:
                print(f"\n--- {lens}: {path.name} ---")
                worst = max(worst, main(passed + ["--lens", lens, "--window", str(path)]))
            return worst
        standing_path = live[0]
    standing = slots_mod.read_window(standing_path)
    if standing is None:
        return 1

    # The standing calendar is the window file plus every decision taken since,
    # exactly as the page and the queue see it. Refreshing against the file
    # alone would propose changes to slots a human has already approved.
    decisions = state_store.latest_decisions(root=args.state_root)
    standing = slots_mod.apply_approvals(standing, state_store.states_of(decisions))

    excluded: list = []
    candidates = assemble_mod.gather_candidates(
        criteria, lens, args.atomizer, args.vcb, args.tags, excluded=excluded
    )
    # Posts another calendar of this lens already holds are not candidates
    # here either, the same rule assembly applies, so refreshing November
    # cannot pull October's posts in and the supply report does not count
    # them as runway this window has.
    placed = assemble_mod.placed_elsewhere(
        criteria, lens, args.state_root,
        args.as_of or datetime.now(timezone.utc).date().isoformat(),
        skip=[standing_path],
    )
    candidates = [c for c in candidates if c.item_ref not in placed]
    context = assemble_mod.gather_context(criteria)
    context.feedback = assemble_mod.gather_feedback(criteria, lens, args.state_root)
    entries = state_store.read_state(DEFAULT_INGESTION_STORE, root=args.state_root)
    narrative_paths = [
        _AGENT_ROOT / "narrative" / name
        for name in sorted(p.name for p in (_AGENT_ROOT / "narrative").glob("*.md"))
    ] if (_AGENT_ROOT / "narrative").is_dir() else []
    if context.season is not None:
        # A new season brief is a new arc, so it counts as a narrative change.
        narrative_paths.append(Path(context.season[1]))

    as_of = args.as_of or datetime.now(timezone.utc).date().isoformat()
    run_store = (policy.get("output") or {}).get("store") or "refresh_runs"
    previous = last_run(standing.window_id, store=run_store, root=args.state_root)
    outcome = refresh(
        standing,
        standing_path,
        criteria=criteria,
        taxonomy=taxonomy,
        policy=policy,
        lens=lens,
        candidates=candidates,
        context=context,
        entries=entries,
        decisions=decisions,
        narrative_paths=narrative_paths,
        previous=previous,
        today=as_of,
        force=args.force,
        report_only=args.report_only,
    )

    print(format_report(outcome, as_of=as_of))

    # Recording is not bookkeeping here: this run's digests are the next run's
    # baseline, so a run that fails to record leaves the following one unable
    # to tell a changed document from a first sighting.
    if (policy.get("output") or {}).get("record_runs"):
        written = state_store.record_state(
            run_store, [outcome.to_entry(as_of=as_of)], root=args.state_root
        )
        if not written.get("ok"):
            _warn(
                f"the refresh ran but its run was not recorded to {run_store}, so the "
                "next run has no baseline to compare the narrative against"
            )

    return 0 if outcome.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
