"""The implementation-timeline parser (E4): a `<Chart>` config read as JSON.

One structure in a research paper is already machine-readable: the
implementation timeline, drawn as a Chart.js `<Chart>` tag whose `config`
attribute is JSON. So this parser does no prose extraction and no regex over a
config body. It finds the tags, parses each config as JSON, and picks the
timelines out structurally.

The selector is structural on purpose. Every timeline is `type: bar` with
`options.indexAxis` of `"y"` and every dataset value a two-element
`[start, end]` pair, which is Chart.js for a horizontal floating bar. Measured
2026-08-11 across the eight committed excerpts under `data-provider/fixtures/`:
that predicate holds on exactly 7 of 36 charts, with no false positive and no
false negative. A case-sensitive substring of the chart `name` separates the
same 7 and is still the wrong rule, because one of the seven carries a project
name in its own name and on the live corpus the name also carries a client
token, so keying on it would hardcode a client. The tests use the name as a
confirmation; nothing here reads it.

Units are reported, never normalized. A phase in weeks and a phase in months
are not the same number, and on the live corpus two papers express duration in
weeks while one of those mixes them: phases 1 to 3 labelled in weeks and phase
4 in months, with the data array in weeks throughout. So the unit governing a
phase's numbers comes from the dataset carrying them, and a phase label that
disagrees is reported as `unit_conflict` rather than resolved or normalized.

Provenance is E5a's. The config text is itself the span, so a `SourcedFigure`
is buildable here with no prose extraction, and a paper with no timeline is a
`MissingFields` entry rather than a failure or an invented value.

A config that is not JSON is loud on the ARTIFACT, not in the process (E11
Stage 2f, 2026-08-19). The instinct this module was written with is unchanged:
since Blake confirmed the platform's graphs are being reworked, a config that
stops being JSON is a platform change worth reporting rather than skipping
quietly. What changed is where the noise goes. Such a config used to raise
`JSONDecodeError` out of `extract_chart_configs`, and nothing between here and
the studio seam caught it, so ONE malformed drawing took down the whole packet:
the three baseline figures and both scenario fields, none of which reads a
chart, were lost with it. Now the CHART is rejected and the PACKET is not. The
remaining configs in the same paper are still returned, because a paper's
timeline may live in a chart after the broken one, and the failure is carried
into `MissingFields` with a reason that names it as a parse failure and locates
it. Nothing is dropped without a reason a reviewer can read.

Measured 2026-08-19 across the 49 published demo papers, 216 charts: exactly one
config does not parse, writing `\'` inside a JSON string, which is not a JSON
escape. Its paper's own timeline chart parses and was lost anyway. That count
supersedes the 94 charts this docstring used to cite. A reason string never
quotes a config body, per `data-provider/SCRUBBING.md`: a body carries chart
titles, third-party firm names and figures.

An unclosed `config='` still raises, and that is a different failure rather than
an inconsistency. One config that will not parse is one bad chart; a tag that
never closes is a structure the scanner cannot walk past to find the next one,
so skipping it would guess at where the paper resumes. Zero of the 216 charts
carry one.
"""

import json
import re

from source_span import MissingFields, SourcedFigure

FIELD = "timeline"

_CONFIG_OPEN = re.compile(r"config='")
_CONFIG_END = re.compile(r"'\s*/>")
_PARENTHETICAL = re.compile(r"\(([^)]*)\)")


def extract_chart_configs(paper):
    """Every `<Chart>` config in a paper that parses, as `(config_text, config)`
    pairs. A config that does not parse costs its own chart and no other."""
    return _scan_chart_configs(paper)[0]


def _scan_chart_configs(paper):
    """The configs that parsed, and one reason per config that did not.

    Split from `extract_chart_configs` so the failures have somewhere to go
    other than the call stack. The pairs are what every caller already reads;
    the reasons are what `parse_timeline` routes into `MissingFields`, which is
    the difference between rejecting a chart and skipping one quietly.
    """
    configs, malformed = [], []
    for opening in _CONFIG_OPEN.finditer(paper or ""):
        text = _config_text(paper, opening.end())
        try:
            configs.append((text, json.loads(text)))
        except ValueError as error:
            ordinal = len(configs) + len(malformed) + 1
            malformed.append(_malformed_reason(error, opening.end(), ordinal))
    return configs, tuple(malformed)


def _malformed_reason(error, config_start, ordinal):
    """Why one config did not parse, diagnosable without quoting the config.

    Two things a reviewer needs and neither of them is the body: which config in
    the paper it was, and where the parse stopped, given both inside the config
    and as an offset into the paper so it can be found by seeking rather than by
    counting tags. `json`'s own message names the syntax problem and never
    echoes document text, which is what makes it safe to carry into a tracked
    file under `data-provider/SCRUBBING.md`.
    """
    said = getattr(error, "msg", None) or error.__class__.__name__
    position = getattr(error, "pos", None)
    if position is None:
        return (f"<Chart> config {ordinal} in this document did not parse as "
                f"JSON ({said}).")
    return (
        f"<Chart> config {ordinal} in this document did not parse as JSON "
        f"({said}) at line {error.lineno} column {error.colno} of that config, "
        f"character {config_start + position} of the document."
    )


def _config_text(paper, start):
    """The config body, from the opening quote to the one that ends the tag.

    The attribute is single-quote delimited and a chart title may contain a
    literal apostrophe, so the terminator is the quote immediately before the
    tag's `/>` rather than the next quote. Matching lazily to the next quote
    truncates one config on the committed fixtures, which then fails to parse,
    and that is the whole of the "malformed config" reported here before.
    """
    end = _CONFIG_END.search(paper, start)
    if end is None:
        raise ValueError("a <Chart> config is never closed before its tag ends.")
    return paper[start:end.start()]


def is_timeline(config):
    """Structural: a horizontal floating bar, whatever the chart is named."""
    if config.get("type") != "bar":
        return False
    if (config.get("options") or {}).get("indexAxis") != "y":
        return False
    values = [v for series in _datasets(config) for v in series.get("data") or []]
    return bool(values) and all(_is_pair(v) for v in values)


def _datasets(config):
    return (config.get("data") or {}).get("datasets") or []


def _is_pair(value):
    return isinstance(value, list) and len(value) == 2


def _is_span(value):
    """A pair carrying two numbers. `[null, null]` is padding, not a phase."""
    return _is_pair(value) and all(isinstance(n, (int, float)) for n in value)


def _phases(config):
    """One record per label that some dataset gives a real `[start, end]`.

    Reading `datasets[0]` is what breaks on the live corpus: one paper draws
    one dataset PER phase, padded with `[null, null]` at every other index, so
    a parser reading the first dataset gets one phase and three nulls. Walking
    the labels and taking whichever dataset fills each index reads that shape
    and the ordinary single-dataset one alike.
    """
    phases = []
    axis_unit = _axis_unit(config)
    for index, label in enumerate((config.get("data") or {}).get("labels") or []):
        for series in _datasets(config):
            data = series.get("data") or []
            if index < len(data) and _is_span(data[index]):
                phases.append(_phase(label, data[index], series.get("label"),
                                     axis_unit))
                break
    return phases


def _phase(label, span, series_label, axis_unit=None):
    """One phase, in the unit its own numbers are in rather than a common one."""
    data_unit = _unit(series_label)
    label_unit = _unit(label)
    phase = {
        "label": label,
        "start": span[0],
        "end": span[1],
        # The axis title is the LAST source and never outranks the two that
        # belong to the numbers themselves. It is read at all because a chart
        # that states its unit once, on the axis, was leaving the deck's
        # timeline header as bare numbers with nothing saying what they count
        # (reported by Antonio on the Contoso deck, 2026-09-20). Still
        # stated rather than inferred: a chart whose axis says nothing produces
        # no unit here, and the header stays bare.
        "unit": data_unit or label_unit or axis_unit,
    }
    if data_unit and label_unit and data_unit != label_unit:
        phase["unit_conflict"] = label_unit
    return phase


def _axis_unit(config):
    """`weeks` or `months` named by the chart's own x axis or title, or None.

    A floating-bar timeline runs its numbers along x, so the x scale's title is
    where a chart names what its numbers count ("Month", "Weeks from kickoff").
    The chart's overall title is read second, because a title like
    "Rollout schedule (Weeks)" states the same thing about the same numbers.
    Neither is inferred from anything: both are text the paper wrote. The chart's
    NAME attribute is still never read, by this or anything else here.
    """
    options = config.get("options") or {}
    scales = options.get("scales") or {}
    x_axis = scales.get("x") or {}
    candidates = [
        ((x_axis.get("title") or {}).get("text")),
        x_axis.get("label"),
        (((options.get("plugins") or {}).get("title") or {}).get("text")),
    ]
    for text in candidates:
        unit = _unit_word(text)
        if unit:
            return unit
    return None


def _unit_word(text):
    """`weeks` or `months` from anywhere in ``text``, or None.

    Unlike `_unit`, which reads a parenthetical because that is how a dataset
    label carries a unit beside a name, an axis title is usually the unit and
    nothing else ("Month"). So the whole string is read, and only these two
    words are recognised.
    """
    lowered = (text or "").lower()
    if "week" in lowered:
        return "weeks"
    if "month" in lowered:
        return "months"
    return None


def _unit(text):
    """`weeks` or `months` from a parenthetical, or None. Nothing is inferred."""
    for chunk in _PARENTHETICAL.findall(text or ""):
        lowered = chunk.lower()
        if "week" in lowered:
            return "weeks"
        if "month" in lowered:
            return "months"
    return None


def parse_timeline(paper, missing=None, *, document, opportunity):
    """The paper's implementation timeline as a `SourcedFigure`, or None.

    `document` is which document `paper` is and `opportunity` is which
    opportunity it is being read FOR, both keyword-only and both required, so
    the phase list out of here can say where it came from and which slide it
    belongs on (`source_span.Document`, `source_span.Opportunity`).

    `missing` is the caller's `MissingFields`, so an absence lands in the
    packet's `missing_fields` with its reason and drags `data_completeness` the
    way the golden rule requires. Absence is a normal outcome: one published
    paper on the live corpus carries no timeline chart at all. A paper whose
    timeline chart parses is unaffected by a malformed config elsewhere in it,
    which is the case the live corpus actually carries.
    """
    missing = missing if missing is not None else MissingFields()
    configs, malformed = _scan_chart_configs(paper)
    for text, config in configs:
        if not is_timeline(config):
            continue
        phases = _phases(config)
        if not phases:
            missing.record(FIELD,
                           "this document's timeline chart carries no phase "
                           "spans.")
            return None
        return SourcedFigure(field=FIELD, value=phases, span=text, source=paper,
                             document=document, opportunity=opportunity)
    missing.record(FIELD, _absence_reason(malformed))
    return None


def _absence_reason(malformed):
    """No timeline, and whether a config that would not parse is why.

    Two different things to whoever reads section 8, so they read differently. A
    paper carrying no timeline chart is a normal outcome and one published paper
    on the live corpus is one. A config that stopped being JSON is a platform
    signal, and every one of them is named here rather than counted, so a second
    malformed config cannot hide behind the first.
    """
    if not malformed:
        return "no <Chart> in this document is a timeline."
    return (
        f"no <Chart> in this document is a timeline, and {len(malformed)} of its "
        "configs could not be read at all: " + " ".join(malformed)
    )
