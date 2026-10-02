"""Tests for the implementation-timeline parser (E4).

Two halves, and the split is deliberate. The census tests run against the eight
committed excerpts under `data-provider/fixtures/`, which is where the numbers
in the module's docstring come from. The trap tests run against HAND-BUILT
configs, spelled out in this file, because both traps are real on the live
corpus and absent from the committed eight: no captured fixture expresses phase
duration in weeks, mixes weeks with months, or draws one dataset per phase. A
hand-built config is the honest way to cover them. It is not a capture and it is
not evidence about the corpus, only about the parser.

Nothing here names a client. The fixtures are read for structure and counts, and
the hand-built configs use neutral phase names.
"""

import json
import pathlib
import re

import pytest

from chart_timeline_parser import (
    FIELD,
    extract_chart_configs,
    is_timeline,
    parse_timeline,
)
from source_span import PAPER, Opportunity, MissingFields, SourcedFigure

# The opportunity these readings are FOR (item 15, 2026-09-13). A figure names
# one the way it names its document, so a parser test has to state one, and
# stating a stand-in here is the same trade `PAPER` makes: these tests are about
# reading a table, not about which engagement asked for it.
OPPORTUNITY = Opportunity(id="OPP-PARSER-TEST")


FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "data-provider" / "fixtures"
MODULE = pathlib.Path(__file__).resolve().parent.parent / "src" / "chart_timeline_parser.py"

APOSTROPHE_FIXTURE = "paper-excerpt-scenario-rows-multi-table.json"
NO_TIMELINE_FIXTURE = "paper-excerpt-no-timeline-chart.json"

# Measured 2026-08-11 across all eight committed excerpts.
EXPECTED_CHARTS = 36
EXPECTED_TIMELINES = 7


def _paper(fixture):
    body = json.loads((FIXTURES / fixture).read_text())
    return body["opportunity"]["research_paper_natural"]


def _papers():
    return {path.name: _paper(path.name) for path in sorted(FIXTURES.glob("*.json"))}


def _chart(config, name="Rollout schedule"):
    """A hand-built `<Chart>` tag inside a paper, wrapped the way the real ones are."""
    return (
        "## Rollout\n\nProse above the tag.\n\n"
        f'<Chart\n  name="{name}"\n  description="Hand-built for a test."\n'
        f"  config='{json.dumps(config, indent=2)}'\n/>\n\nProse below the tag.\n"
    )


def _horizontal_bar(labels, datasets):
    return {
        "type": "bar",
        "data": {"labels": labels, "datasets": datasets},
        "options": {"indexAxis": "y"},
    }


def test_every_chart_in_every_fixture_extracts_and_parses_as_json():
    for name, paper in _papers().items():
        tags = re.findall(r"<Chart\b", paper)
        configs = extract_chart_configs(paper)
        assert len(configs) == len(tags), name
        assert all(isinstance(config, dict) for _, config in configs), name


def test_the_structural_selector_finds_seven_timelines_and_rejects_the_other_29():
    configs = [pair for paper in _papers().values() for pair in extract_chart_configs(paper)]
    assert len(configs) == EXPECTED_CHARTS
    timelines = [config for _, config in configs if is_timeline(config)]
    assert len(timelines) == EXPECTED_TIMELINES
    assert len(configs) - len(timelines) == EXPECTED_CHARTS - EXPECTED_TIMELINES


def test_the_apostrophe_config_survives_extraction_where_a_naive_regex_loses_it():
    """The trap: `config` is single-quote delimited and one title has an apostrophe."""
    paper = _paper(APOSTROPHE_FIXTURE)
    naive = re.findall(r"config='([^']*)'", paper)
    parsed_naively = [text for text in naive if _parses(text)]
    assert len(naive) == 4 and len(parsed_naively) == 3

    configs = extract_chart_configs(paper)
    assert len(configs) == 4
    assert all(_parses(text) for text, _ in configs)
    assert any("'" in text for text, _ in configs)


def _parses(text):
    try:
        json.loads(text)
    except ValueError:
        return False
    return True


def test_the_chart_name_confirms_the_selector_and_is_never_read_by_it():
    """`Implementation Timeline` separates the same 7, and keying on it would
    hardcode a client, so the module must not contain the string at all."""
    named, selected = 0, 0
    for paper in _papers().values():
        charts = list(zip(re.findall(r'<Chart\s+name="([^"]*)"', paper),
                          [config for _, config in extract_chart_configs(paper)]))
        named += sum(1 for name, _ in charts if "Implementation Timeline" in name)
        selected += sum(1 for name, config in charts
                        if is_timeline(config) and "Implementation Timeline" in name)
    assert named == EXPECTED_TIMELINES and selected == EXPECTED_TIMELINES
    assert "Implementation Timeline" not in MODULE.read_text()


def test_the_two_nearest_false_positives_are_rejected():
    """Both are horizontal bars carrying `[start, end]` pairs and neither is a
    timeline: one pads with bare `null` rather than `[null, null]`, the other
    puts scalars in its second and third datasets."""
    for fixture in ("paper-excerpt-scenario-columns.json", NO_TIMELINE_FIXTURE):
        horizontal = [config for _, config in extract_chart_configs(_paper(fixture))
                      if config.get("type") == "bar"
                      and config.get("options", {}).get("indexAxis") == "y"]
        rejected = [config for config in horizontal if not is_timeline(config)]
        assert len(rejected) == 1, fixture
        values = [v for series in rejected[0]["data"]["datasets"]
                  for v in series["data"]]
        assert any(not isinstance(v, list) for v in values), fixture


def test_a_timeline_figure_carries_the_config_text_it_was_parsed_from():
    paper = _paper(APOSTROPHE_FIXTURE)
    missing = MissingFields()
    figure = parse_timeline(paper, missing, document=PAPER, opportunity=OPPORTUNITY)
    assert isinstance(figure, SourcedFigure) and figure.field == FIELD
    assert figure.span in paper and json.loads(figure.span)["type"] == "bar"
    assert missing.names == []
    assert [phase["unit"] for phase in figure.value] == ["months"] * 3


def test_a_paper_with_no_timeline_chart_records_a_missing_field():
    """The only absence case in the corpus, and no live paper lacks a timeline,
    so this test is one-sided on real data. An accepted limit."""
    missing = MissingFields()
    assert parse_timeline(_paper(NO_TIMELINE_FIXTURE), missing, document=PAPER, opportunity=OPPORTUNITY) is None
    assert missing.names == [FIELD]
    assert "no <Chart>" in missing.entries[0][1]


def test_a_hand_built_mixed_unit_config_reports_weeks_and_flags_the_month_label():
    """HAND-BUILT, not a capture. Two live papers give duration in weeks and one
    of those mixes them: phases 1 to 3 labelled in weeks, phase 4 in months,
    data in weeks throughout. No committed fixture carries this shape."""
    config = _horizontal_bar(
        ["Phase 1: Discovery (Weeks 0-6)",
         "Phase 2: Build (Weeks 6-14)",
         "Phase 3: Rollout (Weeks 14-24)",
         "Phase 4: Optimization (Months 6-9)"],
        [{"label": "Duration (weeks)",
          "data": [[0, 6], [6, 14], [14, 24], [24, 36]]}],
    )
    figure = parse_timeline(_chart(config), document=PAPER, opportunity=OPPORTUNITY)
    assert [phase["unit"] for phase in figure.value] == ["weeks"] * 4
    assert [phase["end"] for phase in figure.value] == [6, 14, 24, 36]
    assert figure.value[3]["unit_conflict"] == "months"
    assert not any("unit_conflict" in phase for phase in figure.value[:3])


def test_a_hand_built_config_with_one_dataset_per_phase_reads_every_phase():
    """HAND-BUILT, not a capture. One live paper draws one dataset per phase with
    `[null, null]` padding, so a parser reading `datasets[0]` gets one phase and
    three nulls. No committed fixture carries this shape."""
    pad = [None, None]
    config = _horizontal_bar(
        [f"Phase {n}: Stage {n} (Months {n - 1}-{n})" for n in range(1, 5)],
        [{"label": f"Phase {n} (months)",
          "data": [[n - 1, n] if i == n - 1 else pad for i in range(4)]}
         for n in range(1, 5)],
    )
    figure = parse_timeline(_chart(config), document=PAPER, opportunity=OPPORTUNITY)
    assert [(phase["start"], phase["end"]) for phase in figure.value] == [
        (0, 1), (1, 2), (2, 3), (3, 4)
    ]
    assert [phase["unit"] for phase in figure.value] == ["months"] * 4


def test_a_timeline_whose_every_value_is_padding_is_a_missing_field():
    config = _horizontal_bar(
        ["Phase 1 (Months 0-3)", "Phase 2 (Months 3-6)"],
        [{"label": "Duration (months)", "data": [[None, None], [None, None]]}],
    )
    assert is_timeline(config)
    missing = MissingFields()
    assert parse_timeline(_chart(config), missing, document=PAPER, opportunity=OPPORTUNITY) is None
    assert missing.names == [FIELD]
    assert "no phase spans" in missing.entries[0][1]


def test_an_unclosed_config_attribute_raises_rather_than_being_guessed_at():
    with pytest.raises(ValueError):
        extract_chart_configs("<Chart name=\"x\" config='{\"type\": \"bar\"}\n")


# --- E11 Stage 2f: a malformed config is an absence, not a dead run ----------
#
# All five configs below are HAND-BUILT and neutral. The defect they carry is
# the one measured on the live corpus 2026-08-19: 216 charts across the 49
# published demo papers, of which exactly one config writes `\'` inside a JSON
# string, which is not a JSON escape. That paper's own timeline chart parses
# fine and was lost anyway, because one `json.loads` with no `try` took the
# whole paper down. Nothing here quotes that config: `data-provider/SCRUBBING.md`
# covers its body, which names a real third-party firm.

MALFORMED_BODY = r"""{"type": "line", "data": {"labels": ["Acme\'s option"], "datasets": []}}"""
NO_TIMELINE_REASON = "no <Chart> in this document is a timeline."


def _tag(name, body):
    """One hand-built `<Chart>` tag, its config body written out verbatim."""
    return (
        f'<Chart\n  name="{name}"\n  description="Hand-built for a test."\n'
        f"  config='{body}'\n/>\n"
    )


def _paper_of(*tags):
    return "## Section\n\nProse above the tags.\n\n" + "\n".join(tags) + "\nProse below.\n"


def _timeline_body():
    return json.dumps(_horizontal_bar(
        ["Phase 1: Discovery (Months 0-3)", "Phase 2: Rollout (Months 3-6)"],
        [{"label": "Duration (months)", "data": [[0, 3], [3, 6]]}],
    ), indent=2)


def _decoy_body():
    """A chart that parses and is not a timeline, so the absence path is reached."""
    return json.dumps({"type": "pie", "data": {"labels": ["a", "b"], "datasets": []}})


def _malformed_message():
    """`json`'s own words for this body, read from `json` rather than spelled here."""
    try:
        json.loads(MALFORMED_BODY)
    except ValueError as error:
        return error.msg
    raise AssertionError("MALFORMED_BODY parses, so it is no longer a fixture.")


def test_a_malformed_config_beside_a_valid_timeline_leaves_the_timeline_found():
    """The live shape. The broken chart is not the timeline, and never was."""
    paper = _paper_of(_tag("Vendor mix", MALFORMED_BODY),
                      _tag("Rollout schedule", _timeline_body()))
    missing = MissingFields()
    figure = parse_timeline(paper, missing, document=PAPER, opportunity=OPPORTUNITY)
    assert isinstance(figure, SourcedFigure) and figure.field == FIELD
    assert [(phase["start"], phase["end"]) for phase in figure.value] == [(0, 3), (3, 6)]
    assert missing.entries == ()
    assert len(extract_chart_configs(paper)) == 1


def test_the_other_configs_survive_a_malformed_one_in_any_position():
    """A paper's timeline may live in a chart after the broken one, so position
    cannot decide what is returned."""
    body = _timeline_body()
    for position in range(3):
        tags = [_tag(f"Chart {n}", body) for n in range(3)]
        tags[position] = _tag("Vendor mix", MALFORMED_BODY)
        paper = _paper_of(*tags)
        assert len(extract_chart_configs(paper)) == 2
        assert parse_timeline(paper, document=PAPER, opportunity=OPPORTUNITY) is not None


def test_a_malformed_config_and_no_timeline_records_the_parse_failure():
    paper = _paper_of(_tag("Vendor mix", MALFORMED_BODY))
    missing = MissingFields()
    assert parse_timeline(paper, missing, document=PAPER, opportunity=OPPORTUNITY) is None
    assert missing.names == [FIELD]
    reason = missing.entries[0][1]
    assert "JSON" in reason
    assert _malformed_message() in reason


def test_the_malformed_reason_and_the_no_timeline_reason_say_different_things():
    """Section 8 has to tell them apart. A paper with no timeline chart is normal,
    one on the live corpus has none; a config that stopped being JSON is a
    platform signal and reads as one."""
    clean, broken = MissingFields(), MissingFields()
    assert parse_timeline(_paper_of(_tag("Spend mix", _decoy_body())), clean, document=PAPER, opportunity=OPPORTUNITY) is None
    assert parse_timeline(_paper_of(_tag("Spend mix", MALFORMED_BODY)), broken, document=PAPER, opportunity=OPPORTUNITY) is None
    assert clean.entries[0][1] == NO_TIMELINE_REASON
    assert broken.entries[0][1] != clean.entries[0][1]
    assert "JSON" not in clean.entries[0][1]


def test_the_malformed_reason_locates_the_config_without_quoting_it():
    """Diagnosable on its own: which config, and where the parse stopped. And
    `data-provider/SCRUBBING.md` on the other side, because a real config body
    carries chart titles, third-party firm names and figures."""
    paper = _paper_of(_tag("Spend mix", _decoy_body()),
                      _tag("Vendor mix", MALFORMED_BODY))
    missing = MissingFields()
    assert parse_timeline(paper, missing, document=PAPER, opportunity=OPPORTUNITY) is None
    reason = missing.entries[0][1]

    assert "config 2" in reason
    offset = int(re.search(r"character (\d+) of the document", reason).group(1))
    start = paper.index(MALFORMED_BODY)
    assert start <= offset < start + len(MALFORMED_BODY)

    for fragment in ("Acme", "labels", "datasets", '"type"', "Vendor mix"):
        assert fragment not in reason


def test_every_malformed_config_in_one_paper_is_named_rather_than_counted_away():
    """No silent skip: two broken configs produce two reasons, not one."""
    paper = _paper_of(_tag("Vendor mix", MALFORMED_BODY),
                      _tag("Spend mix", MALFORMED_BODY))
    missing = MissingFields()
    assert parse_timeline(paper, missing, document=PAPER, opportunity=OPPORTUNITY) is None
    reason = missing.entries[0][1]
    assert "config 1" in reason and "config 2" in reason


def test_an_unclosed_config_still_raises_through_parse_timeline():
    """Left as it was, deliberately (E11 Stage 2f). An unclosed `config='` is not
    one bad chart, it is a tag structure the scanner cannot walk past to find the
    next one, so skipping it would guess at where the paper resumes. Zero of the
    216 live-corpus charts carry one, measured 2026-08-19."""
    with pytest.raises(ValueError):
        parse_timeline("<Chart name=\"x\" config='{\"type\": \"bar\"}\n", MissingFields(), document=PAPER, opportunity=OPPORTUNITY)


# Recorded 2026-08-19 from the PRE-change parser, so this table is evidence about
# what the eight excerpts read as before Stage 2f rather than a restatement of
# what they read as after it. Every chart on all eight parses, so this stage must
# move none of it. shape -> (phase count, first phase span, absence reason or None).
PRE_STAGE_2F = {
    "assumption-table-and-scenario-rows": (3, (0, 3), None),
    "no-timeline-chart": (0, None, NO_TIMELINE_REASON),
    "scenario-both-orientations": (4, (0, 2), None),
    "scenario-columns": (3, (0, 3), None),
    "scenario-rows-canonical": (3, (0, 3), None),
    "scenario-rows-multi-table": (3, (0, 3), None),
    "sparse-no-scenario-table": (3, (0, 3), None),
    "validated-assumption-table": (3, (0, 3), None),
}


@pytest.mark.parametrize("shape", sorted(PRE_STAGE_2F))
def test_a_paper_whose_charts_all_parse_reads_exactly_as_it_did_before(shape):
    count, first, reason = PRE_STAGE_2F[shape]
    paper = _paper(f"paper-excerpt-{shape}.json")
    missing = MissingFields()
    figure = parse_timeline(paper, missing, document=PAPER, opportunity=OPPORTUNITY)
    if reason is None:
        assert len(figure.value) == count
        assert (figure.value[0]["start"], figure.value[0]["end"]) == first
        assert missing.entries == ()
    else:
        assert figure is None
        assert [entry[1] for entry in missing.entries] == [reason]
