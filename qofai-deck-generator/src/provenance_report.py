"""What the reviewer is told about sources (item 14, step 4): one packet's
provenance, as plain data a panel can render.

The split settled on 2026-09-07 (NEXT-BUILD-PHASE part two, answer 3): the
client-facing deck says nothing about which document filled which field, because
a source annotation does not belong in front of a client, and the studio tells
the reviewer everything. `base_document` decides which document wins and records
where the documents disagreed; this turns that record into the three things a
reviewer asks, in the order they ask them.

  1. WHICH DOCUMENT FILLED WHICH FIELD. Summarised first and enumerated second,
     because the answer is usually "the attachment, mostly" and a reviewer
     should not have to read a table to learn it. IN THREE KINDS, since
     2026-09-08: what the deterministic parsers lifted out of a document's
     tables, what the model extraction pass quoted out of its prose, and what
     the writing pass wrote from its sections. They are three columns rather
     than one number because they are three different things, and one number
     was wrong in both directions at once -- it read as the total while
     counting only the first, and the parsers are written for the published
     paper's shapes, so a PDF scores zero there while filling half the deck.
  2. WHERE THE DOCUMENTS DISAGREED, both answers side by side. This is the list
     with a decision behind every row.
  3. WHAT WAS SET ASIDE. A figure a document really stated that the packet did
     not take, because the baseline moves as a group. Nothing to decide, and
     everything to know: without it a reviewer sees a field reported missing and
     types in by hand the figure the other document was holding all along.

QUIET WHEN THERE IS NOTHING TO SAY. `build` returns an empty dict for a run with
no attachment, so the studio grows no panel about sources on the runs that have
one source. Inside a panel each of the three parts is present only when it has
rows, so a run where nothing disagreed shows no disagreement table.

PLAIN DATA, NOT OBJECTS. Every value here is a string, a number, a list or a
dict, so the same report renders in a template, serialises into a run record,
and survives a trip through the deck store without anything having to know about
`source_span`. It names no client, no project and no document of its own: every
name in it came off the reviewer's own upload.

NOT ON THE RENDER PATH, and there is a test that says so
(`tests/test_provenance_never_renders.py`). Nothing here reaches the packet
document, the placeholder map, the prompt or a rendered deck.
"""

import base_document
import source_span
from packet_assembly import EBITDA, MARGIN, REVENUE, TIMELINE

# The unit a field's figure is in, read off the field's own name rather than
# from a table that would have to be kept in step with `packet_assembly`'s
# roster. Every packet figure is a `(low, high)` pair in its field's declared
# unit, so formatting is a question about the suffix and nothing else.
_MONEY_SUFFIXES = ("_usd", "_usd_yr")
_PERCENT_SUFFIXES = ("_pct",)
_POINT_SUFFIXES = ("_pp",)

# How a field reads in a panel. The packet paths are precise and unreadable, and
# a reviewer comparing two documents' answers should not have to parse
# `baseline.adjusted_ebitda_pct` to know which line to look at. Absent from this
# map means the path itself is shown, which is honest and never wrong.
FIELD_LABELS = {
    REVENUE: "revenue (TTM)",
    EBITDA: "adjusted EBITDA",
    MARGIN: "adjusted EBITDA margin",
    TIMELINE: "the implementation timeline",
    base_document.SCENARIOS: "the scenario table",
}


def build(packet, sources, *, extraction=None, writing=None, opportunity=""):
    """One packet's provenance as plain data, or `{}` when there is nothing to say.

    `packet` is the final `Packet` the run assembled, after both the merge and
    the second extraction pass, so every figure in it carries the document it
    was actually read from. `sources` is the `base_document.precedence` chain the
    run used, in the order the documents won.

    `extraction` is the run's `second_pass.SecondPass` and `writing` its
    `second_pass.SecondWriting`, and THEY ARE WHY THIS FUNCTION TAKES MORE THAN
    A PACKET. A packet holds figures, so a report built from one alone counts
    only what the DETERMINISTIC PARSERS lifted -- and those parsers read the
    published paper's markdown table shapes, so a PDF or a Word file scores zero
    there by construction while supplying most of the deck. The first live run
    of item 14 (2026-09-08, a 392KB client PRD as the base) reported the base
    document at "0 fields" for a deck whose substance it had reshaped. The
    number was right; the label was the total. Both model legs read the BASE
    DOCUMENT's text, so what they took is attributable to the base by
    construction, and it is counted here beside what the parsers read rather
    than folded into it.

    Both are optional and both default to nothing rather than to zero-with-a-
    claim: a caller that has no pass to report gets a report that counts none,
    which is what a run with no model legs actually did.

    `opportunity` is the title of the opportunity this section is for, recorded
    rather than rendered. It is what `combine` below needs to tell two published
    papers apart, and it does nothing on a run with one section.

    Empty for a run with no attachment. Every field came from the published
    paper, every reviewer already knows that, and a panel saying so is a panel
    in the way.
    """
    sources = tuple(sources or ())
    if not any(source.uploaded for source in sources):
        return {}

    base = base_document.base(sources).document.label
    filled = _filled(packet)
    quoted = _quoted(extraction, base)
    written = _written(writing, base)
    return {
        "sources": [
            {"name": source.name, "kind": source.kind,
             "label": source.document.label, "uploaded": source.uploaded,
             "characters": len(source.text)}
            for source in sources
        ],
        "base": base,
        "opportunity": opportunity or "",
        # The summary that makes the enumeration optional: what each document
        # gave the deck, in precedence order, in the three kinds it can give it
        # in. Three numbers rather than one, because a figure a parser lifted, a
        # value a model quoted and a sentence a model wrote are different things
        # and this build does not blur them.
        "counts": _counts(filled, quoted, written, sources),
        "filled": filled,
        # What the extraction pass quoted out of the base document's prose, and
        # what the writing pass wrote from its sections. Enumerated as well as
        # counted, because a count nobody can check is the same unverifiable
        # claim the count above replaced.
        "quoted": quoted,
        "written": written,
        "conflicts": [_conflict(conflict) for conflict in packet.conflicts],
        "set_aside": [_set_aside(record) for record in packet.set_aside],
    }


def combine(reports):
    """Several sections' reports as ONE account of the run.

    THE GAP THIS CLOSES, found by a live two-opportunity run on 2026-09-13. The
    panel listed two sources, the attachment and one paper, on a run that read
    three documents. `build` above is per SECTION and was telling the truth
    about its own: it is handed one opportunity's chain, which is the shared
    uploads plus that opportunity's own paper and no other, because that
    construction is what keeps one opportunity's slide from being written from
    another's paper. What it cannot do is speak for the run, and the studio was
    rendering it as though it did.

    So the union is taken HERE rather than by widening `build`, which would have
    meant handing it a chain no single section was assembled from.

    EVERY LIST IS UNIONED, NOT JUST `sources`, and that is the whole difficulty.
    `_counts` walks the sources and reports each one's three numbers, so a
    source list widened on its own would give the second paper a row of zeroes
    on the first section's data -- which is precisely the "0 fields" defect the
    2026-09-08 entry exists to prevent, rebuilt by accident. The counts are
    therefore recomputed over the unioned rows.

    Rows are deduplicated on everything that identifies them, because the
    baseline is SHARED: the same figure from the same document appears in every
    section that carries it, and it is one fact about one company rather than
    one per slide. Two genuinely different readings differ in at least their
    value or their span and both survive.

    `base` is the run's base document, which every section shares by
    construction: the uploads lead every chain.

    One report passes through unchanged, by identity, which is every
    single-opportunity run.
    """
    reports = [report for report in reports if report]
    if not reports:
        return {}
    if len(reports) == 1:
        return reports[0]

    reports = [_qualified(report) for report in reports]
    sources, seen = [], set()
    for report in reports:
        for source in report.get("sources") or ():
            if source["label"] not in seen:
                seen.add(source["label"])
                sources.append(source)
    filled = _union(reports, "filled", ("field", "document", "value", "span"))
    quoted = _union(reports, "quoted", ("field", "document", "span"))
    written = _union(reports, "written", ("field", "document", "text"))
    return {
        "sources": sources,
        "base": reports[0]["base"],
        "counts": _counts(filled, quoted, written,
                          [_SourceRow(source) for source in sources]),
        "filled": filled,
        "quoted": quoted,
        "written": written,
        "conflicts": _union(reports, "conflicts", ("field",)),
        "set_aside": _union(reports, "set_aside", ("field", "value")),
    }


def _qualified(report):
    """One section's report with its published paper named by its opportunity.

    THE ONE THING THAT CANNOT BE LEFT TO THE RECORD. Every published paper is
    `Document(name="", kind=PAPER_KIND)`, because the paper is the one source
    that arrives from the platform rather than from a file and has never had a
    filename. Two papers in one run are therefore the same `Document` and carry
    the same label, "the published opportunity paper". Nothing keys on that and
    nothing collides -- the caches carry the text and the opportunity, and every
    figure names its own opportunity -- but a REVIEWER told which of two
    documents filled a field cannot be told "the paper" when there were two.
    That is the same sentence `Document` already refuses to allow for
    attachments, one source kind along.

    Named here rather than in the record because naming it there would move the
    label on every single-opportunity run too, and a run with one paper has
    nothing to disambiguate. `combine` only reaches this with more than one
    section, so the qualification appears exactly when it is needed.

    Applied to every list at once, not only to `sources`: the counts are keyed
    on a row's document label, so relabelling the source list alone would leave
    two papers' rows merged under one count while the panel listed them apart.
    """
    title = report.get("opportunity") or ""
    if not title:
        return report
    plain = source_span.PAPER.label
    qualified = f"{plain} for {title}"

    def relabel(rows, key):
        return [dict(row, **{key: qualified}) if row.get(key) == plain else row
                for row in rows or ()]

    return dict(
        report,
        sources=[dict(source, label=qualified)
                 if source["label"] == plain else source
                 for source in report.get("sources") or ()],
        filled=relabel(report.get("filled"), "document"),
        quoted=relabel(report.get("quoted"), "document"),
        written=relabel(report.get("written"), "document"),
    )


class _SourceRow:
    """One unioned source row, in the shape `_counts` reads a chain source in.

    `_counts` takes `base_document.Source` objects and reads `document.label`
    off each. A unioned row is plain data by then, so this is the adapter, and
    it exists rather than `_counts` learning a second shape because that
    function is the one place the three columns are defined and it should keep
    one input.
    """

    def __init__(self, row):
        self.document = type("_D", (), {"label": row["label"]})()


def _union(reports, key, identity):
    """Every report's rows under `key`, in order, once each.

    Deduplicated on `identity` because a shared figure appears in every section
    that carries it and is one fact rather than one per slide.
    """
    rows, seen = [], set()
    for report in reports:
        for row in report.get(key) or ():
            mark = tuple(str(row.get(name)) for name in identity)
            if mark in seen:
                continue
            seen.add(mark)
            rows.append(row)
    return rows


def _filled(packet):
    """Which document filled which field, in the deck's own reading order."""
    rows = [
        {"field": figure.field,
         "label": FIELD_LABELS.get(figure.field, figure.field),
         "document": figure.document.label,
         "uploaded": figure.document.uploaded,
         "value": value_text(figure.field, figure.value),
         "span": figure.span}
        for figure in packet.fields
    ]
    if packet.scenarios:
        first = packet.scenarios[0].direct_uplift_usd_yr
        rows.append({
            "field": base_document.SCENARIOS,
            "label": FIELD_LABELS[base_document.SCENARIOS],
            "document": first.document.label,
            "uploaded": first.document.uploaded,
            "value": f"{len(packet.scenarios)} case(s)",
            "span": first.span,
        })
    return sorted(rows, key=lambda row: _order(row["field"]))


def _counts(filled, quoted, written, sources):
    """What each source gave the deck, in the three kinds it can give it in.

    `parsed` is what the deterministic parsers lifted out of the document's own
    tables and charts. `quoted` is the VALUES the model extraction pass read out
    of its prose, counted the same way the panel's own list enumerates them: one
    per quotation, because the column and the list beneath it were reporting the
    same thing as two numbers that disagreed (6 fields against 39 values), and a
    reviewer reading two numbers for one thing has to work out which is a lie.
    `written` is the framing lines the writing pass wrote from its sections.
    Every source is listed even at zero, and `total` is there so a reviewer
    scanning for a document that gave nothing has one column to scan.

    Why three and not one. The parsers are written for the published paper's
    markdown shapes, so a PDF or a Word file commonly reads ZERO under `parsed`
    while its prose fills half the deck through the other two. One number would
    make that document look like a document nothing came from, which is the
    conclusion the first live run of item 14 invited. Adding them together would
    make it look like the parsers had read it, which is a different false
    statement. So they are three columns and each says what it counts.

    A figure the extraction pass merged into the PACKET is counted under
    `quoted` and not under `parsed`, even though it is a packet figure carrying
    the base document: `filled` cannot tell the two apart on its own, and a
    model's reading counted as a parser's would be exactly the blur this splits.
    """
    quoted_fields, quoted_values, written_by = {}, {}, {}
    for row in quoted:
        quoted_fields.setdefault(row["document"], set()).add(row["field"])
        quoted_values[row["document"]] = quoted_values.get(row["document"], 0) + 1
    for row in written:
        written_by[row["document"]] = written_by.get(row["document"], 0) + 1

    parsed_paths = {}
    for row in filled:
        parsed_paths.setdefault(row["document"], set()).add(row["field"])

    counts = []
    for source in sources:
        label = source.document.label
        # A packet figure the pass filled is the pass's, not a parser's.
        parsed = len(parsed_paths.get(label, set()) - quoted_fields.get(label, set()))
        row = {"document": label, "parsed": parsed,
               "quoted": quoted_values.get(label, 0),
               "written": written_by.get(label, 0)}
        row["total"] = row["parsed"] + row["quoted"] + row["written"]
        counts.append(row)
    return counts


def _quoted(extraction, base):
    """What the model extraction pass read out of the documents' prose.

    One row per record the pass merged, because a path like `today_metrics`
    carries several and each is a separate quotation a reviewer may want to
    check. The count beside it counts these same rows, so the panel never
    describes one thing with two numbers.

    ATTRIBUTED PER RECORD, NOT PER CALL. The pass is handed one document's text
    and that document's `Document` (`live_proposal_provider._second_pass`), and
    since 2026-09-12 it is handed the next source's when the base left a roster
    field absent (`second_pass.rescue`). Every record carries the
    `SourcedFigure` whose construction verified its span, so a figure rescued
    from the published paper names the PAPER here even though the deck was
    written from an attachment. That is the whole reason the rescue is one call
    per document rather than one prompt holding both.

    `merged` is the paths the pass was allowed to fill and did. A path it asked
    about and was refused is not here, because the deck does not carry it.
    """
    if extraction is None:
        return []
    records = getattr(extraction, "records", None) or {}
    rows = []
    for path in tuple(getattr(extraction, "merged", ()) or ()):
        for record in records.get(path, ()) or ():
            rows.append({
                "field": path,
                "label": FIELD_LABELS.get(path, path),
                "document": _record_document(record, base),
                "section": _line(getattr(record, "section", "")),
                "span": _line(getattr(record, "span", "")),
            })
    return rows


def _record_document(record, base):
    """Which document one extracted record was read out of.

    Off the record's own verified figure rather than off the call it arrived in,
    so a chain of calls cannot misattribute one. `base` is the fallback for a
    record carrying no figure, which is not a shape `paper_extraction` produces
    today: every `Extracted` is built around a `SourcedFigure`.
    """
    figure = getattr(record, "figure", None)
    document = getattr(figure, "document", None)
    return getattr(document, "label", None) or base


def _written(writing, base):
    """The framing lines the writing pass wrote, and what it wrote them from.

    Attributed to the base document for the same reason as `_quoted`: the pass
    reads `base.text`, and the sections each sentence names are that document's
    sections. The opportunity's own one-line description is context it also
    sees, which is worth knowing and is not a second source of figures.

    GENERATED, never quoted, and the row says so in its own field rather than
    leaving a reader to infer it from where the row sits. Section 8 keeps the
    two apart for this reason and so does this.
    """
    if writing is None:
        return []
    sentences = getattr(writing, "sentences", None) or {}
    return [
        {
            "field": path,
            "label": FIELD_LABELS.get(path, path),
            "document": base,
            "kind": "written, not quoted",
            "text": _line(getattr(sentence, "text", ""), 400),
            "written_from": list(getattr(sentence, "sections", ()) or ()),
        }
        for path, sentence in sorted(sentences.items())
    ]


def _line(text, limit=240):
    """One line of a span or a sentence, for a panel that has to stay readable.

    Same shape as `second_pass._line`, deliberately not imported from it: this
    module is plain data for a reviewer and does not depend on the pass that
    produced the record.
    """
    line = " ".join(str(text or "").split())
    return line if len(line) <= limit else line[:limit - 1] + "\u2026"


def _conflict(conflict):
    return {
        "field": conflict.field,
        "label": FIELD_LABELS.get(conflict.field, conflict.field),
        "answers": [_answer(conflict.field, answer) for answer in conflict.answers],
        "carried": _answer(conflict.field, conflict.carried),
        "alternatives": [_answer(conflict.field, answer)
                         for answer in conflict.alternatives],
    }


def _set_aside(record):
    """One declined figure, with the sentence that makes it actionable.

    The note names the document that stated it and the period it belongs to,
    because those two facts are the whole reason it was not used and a reviewer
    told only that a figure exists has been handed trivia.
    """
    answer = _answer(record.field, record.answer)
    return {
        "field": record.field,
        "label": FIELD_LABELS.get(record.field, record.field),
        "rule": record.rule,
        "answer": answer,
        "lead": record.lead.label if record.lead is not None else "",
        "lead_period": record.lead_period or "",
        "period": record.period or "",
        "note": _note(record, answer),
    }


def _note(record, answer):
    lead = record.lead.label if record.lead is not None else "the base document"
    stated = record.answer.document.label
    field = FIELD_LABELS.get(record.field, record.field)
    period = f" for {record.period}" if record.period else ""
    lead_period = f" for {record.lead_period}" if record.lead_period else ""
    return (
        f"{stated} states {field}{period} as {answer['value']}, and the deck "
        f"does not use it. The three baseline figures and their period are one "
        f"statement about one span of time, so they come from one document, and "
        f"{lead} leads the baseline{lead_period}. Mixing the two would print a "
        f"set of figures no document states. If this is the figure the deck "
        f"should carry, put it in yourself through the missing-values card, "
        f"which records that a person did."
    )


def _answer(field, answer):
    return {
        "document": answer.document.label,
        "uploaded": answer.document.uploaded,
        "value": value_text(field, answer.value),
        "span": answer.span,
    }


def value_text(field, value):
    """One figure as a reviewer reads it, in the unit its field declares.

    Not the deck's formatting, deliberately. This is a comparison surface: two
    documents' answers to one field, side by side, and what matters is that the
    difference between them is visible rather than that either is pretty.
    """
    if isinstance(value, tuple) and len(value) == 2 and _numeric(value):
        low, high = value
        if low == high:
            return _scalar(field, low)
        return f"{_scalar(field, low)} to {_scalar(field, high)}"
    if isinstance(value, (list, tuple)):
        return "; ".join(_element(field, element) for element in value) or "(none)"
    if isinstance(value, dict):
        return "; ".join(f"{key}: {item}" for key, item in sorted(value.items()))
    return str(value)


def _numeric(pair):
    return all(isinstance(part, (int, float)) and not isinstance(part, bool)
               for part in pair)


def _element(field, element):
    if isinstance(element, dict):
        label = element.get("label")
        return str(label) if label else "; ".join(
            f"{key}: {item}" for key, item in sorted(element.items()))
    if isinstance(element, tuple):
        return " / ".join(value_text(field, part) for part in element
                          if part is not None)
    return str(element)


def _scalar(field, number):
    if field.endswith(_MONEY_SUFFIXES):
        return f"${number:,.0f}"
    if field.endswith(_PERCENT_SUFFIXES):
        return f"{number:g}%"
    if field.endswith(_POINT_SUFFIXES):
        return f"{number:g} pp"
    return f"{number:g}"


def _order(field):
    try:
        return (0, base_document._CONFLICT_ORDER.index(field))
    except ValueError:
        return (1, field)
