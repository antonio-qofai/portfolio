"""Tests for the packet-to-document guard.

THE TEST THIS FILE EXISTS FOR is `test_the_2026_09_20_defect_is_refused`. On that
day a deck built from a ten-week PRD told a reviewer the build ran 24 weeks in
four phases, and coverage, render-fidelity and layout all passed, because all
three compare the deck to the PACKET and the packet said 24 weeks too. Every
other test here protects that one from becoming vacuous: a guard that fires on
everything, or that reads the same text as the parser it checks, would pass that
regression and be worthless.

Nothing here names a real client. The documents are hand-built in the shape of
the QofAI PRD template, with neutral phase and section names.
"""

import prd_loyalty_guard as guard
from prd_loyalty_guard import (
    check_prd_loyalty,
    stated_duration,
    stated_impact,
    stated_milestones,
)


class _Figure:
    def __init__(self, field, value):
        self.field = field
        self.value = value


class _Case:
    def __init__(self, margin):
        self.margin_gain_pp = _Figure("commercial.scenarios[].margin_gain_pp",
                                      margin)


class _Packet:
    """The three things the guard reads off a packet, and nothing else."""

    def __init__(self, phases=(), margins=()):
        self.fields = ([_Figure("timeline.phases", list(phases))]
                       if phases else [])
        self.scenarios = [_Case(margin) for margin in margins]


def _phases(*spans):
    return [{"label": f"Phase {index + 1}", "start": float(low),
             "end": float(high), "unit": "weeks"}
            for index, (low, high) in enumerate(spans)]


TEN_WEEK_PRD = """
# 3. Goals & Success Metrics

## 3.2 Success Metrics (KPIs)

| KPI | Baseline | Target |
| --- | --- | --- |
| Pipeline stage coverage | No pipeline | 100% of candidates tracked |
| EBITDA margin uplift | None | +0.50 to +2.50 pts |

# 6. Phased Scope

The build is a fixed ten-week engagement: two weeks of discovery, six weeks of
functionality, and two weeks of hardening.

| Phase | Focus | Key Deliverables |
| --- | --- | --- |
| Discovery Scope Wks 1–2 | Lock scope | Definitions |
| Phase 1 Alpha Wks 3–4 | Stand it up | Pipeline |
| Phase 2 Beta Wks 5–6 | Price it | Model |
| Phase 3 Gamma Wks 7–8 | Learn | Attribution |
| Hardening Handover Wks 9–10 | Prove it | Acceptance |

# 12. Build Milestones & Rollout

| Milestone | Target | Deliverable | Exit Criteria |
| --- | --- | --- | --- |
| M0 | Wks 1–2 | Discovery | Signed off |
| M1 | Wks 3–4 | Pipeline | Sourcing live |
| M2 | Wks 5–6 | Modeling | Model live |
| M3 | Wks 7–8 | Attribution | Criteria agreed |
| M4 | Wks 9–10 | Hardening | Release accepted |

# 13. Open Questions
""".strip()

TEN_WEEKS = _phases((1, 2), (3, 4), (5, 6), (7, 8), (9, 10))


# --- what the document states ---------------------------------------------

def test_the_duration_is_read_from_the_prose_not_the_table():
    """The independence the guard rests on. A parser reads §6's TABLE; this
    reads §6's SENTENCE, so a misparsed table cannot hide behind itself."""
    assert stated_duration(TEN_WEEK_PRD) == 10.0
    # Break the table and leave the sentence: the guard still knows ten.
    broken = TEN_WEEK_PRD.replace("Wks 9–10", "Wks 9–24")
    assert stated_duration(broken) == 10.0


def test_a_digit_duration_reads_as_well_as_a_word_one():
    """The corpus writes both: "a fixed ten-week engagement" and "a focused
    9-week program"."""
    assert stated_duration(
        TEN_WEEK_PRD.replace("a fixed ten-week engagement",
                             "a focused 9-week program")) == 9.0


def test_milestones_are_counted_from_section_12():
    assert stated_milestones(TEN_WEEK_PRD) == 5


def test_the_impact_range_comes_from_the_kpi_table():
    assert stated_impact(TEN_WEEK_PRD) == (0.50, 2.50)


def test_a_kpi_row_that_is_not_an_ebitda_margin_is_ignored():
    """A KPI table holds many rows. Only one is this fact."""
    assert stated_impact(TEN_WEEK_PRD.replace("EBITDA margin uplift",
                                              "Gross margin spread")) is None


# --- the guard -------------------------------------------------------------

def test_a_loyal_packet_passes_and_says_what_it_checked():
    result = check_prd_loyalty(TEN_WEEK_PRD,
                               _Packet(TEN_WEEKS, [(0.5, 0.5), (2.5, 2.5)]))
    assert result["ok"]
    assert set(result["checked"]) == {"duration", "milestone count",
                                      "impact range"}


def test_the_2026_09_20_defect_is_refused():
    """THE REGRESSION. A ten-week PRD and the packet that actually shipped: the
    opportunity paper's four phases running to week 24. Every existing guard
    passed this packet because every existing guard compares the deck to it."""
    shipped = _Packet(_phases((0, 6), (6, 11), (11, 17), (17, 24)),
                      [(0.62, 1.24)])
    result = check_prd_loyalty(TEN_WEEK_PRD, shipped)
    assert not result["ok"]
    facts = {finding["fact"] for finding in result["findings"]}
    assert facts == {"build duration", "milestone count",
                     "EBITDA margin impact"}


def test_every_finding_names_both_values():
    """A finding that reported only the disagreement would send a reviewer back
    to the document to find out what it actually said."""
    shipped = _Packet(_phases((0, 24)), [(0.62, 1.24)])
    for finding in check_prd_loyalty(TEN_WEEK_PRD, shipped)["findings"]:
        assert finding["packet"] and finding["document"] and finding["where"]
        assert finding["packet"] != finding["document"]


def test_a_one_decimal_difference_is_formatting_and_not_a_contradiction():
    """A PRD may write +0.50 in one section and +0.5 in another."""
    result = check_prd_loyalty(
        TEN_WEEK_PRD.replace("+0.50 to +2.50 pts", "+0.5 to +2.5 pts"),
        _Packet(TEN_WEEKS, [(0.50, 0.50), (2.50, 2.50)]))
    assert result["ok"]


def test_a_document_stating_nothing_is_not_a_contradiction():
    """A paper, a spreadsheet, or any run with no PRD. A guard that refused
    everything it could not measure would refuse every run."""
    result = check_prd_loyalty("Total planned duration is 24 weeks.",
                               _Packet(_phases((0, 24))))
    assert result["ok"]
    assert result["checked"] == []


def test_an_empty_packet_is_not_a_contradiction():
    """Checked against a document, a packet with no plan yet states nothing to
    contradict. Absence is `coverage_guard`'s job, not this one's."""
    result = check_prd_loyalty(TEN_WEEK_PRD, _Packet())
    assert result["ok"]
    assert result["checked"] == []


def test_the_guard_does_not_import_the_parser_it_checks():
    """THE PROPERTY THAT MAKES IT WORTH ANYTHING. A guard sharing the parser's
    reading agrees with it by construction and would have passed the defect."""
    import tokenize

    with open(guard.__file__, encoding="utf-8") as handle:
        tokens = list(tokenize.generate_tokens(handle.readline))
    names = {token.string for token in tokens
             if token.type not in (tokenize.COMMENT, tokenize.STRING)}
    assert "prd_section_parsers" not in names
    assert "packet_fill" not in names


# --- the wiring ------------------------------------------------------------
# A guard nobody calls is a guard that passes everything. These two drive the
# real provider, because the module tests above cannot see whether
# `_assemble_one` reaches the guard at all. That failure mode cost this repo a
# day earlier on 2026-09-20, when the PRD autofill was inert while 2139 tests
# passed.

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))


def _provider_run(prd_text):
    """One live-seam run with a PRD attached, through the real provider."""
    import test_live_seam as seam
    from base_document import Upload

    spec = seam.CLIENTS["full"] if "full" in seam.CLIENTS else next(
        iter(seam.CLIENTS.values()))
    provider = seam.LiveProposalProvider(seam.StubClient(spec),
                                         generated_at=seam.STAMP)
    request = {
        "company": spec["company"]["name"],
        "project": spec["project"]["name"],
        "options": {"min_data_completeness": 0.70},
    }
    handle = provider.submit(
        request, uploads=(Upload(filename="a-prd.md",
                                 data=prd_text.encode("utf-8")),))
    return provider.poll(handle)["envelope"]


def test_a_contradicting_prd_is_refused_by_the_real_provider():
    """THE WIRING. The PRD says ten weeks; the stub client's paper says
    otherwise. The run must come back as an error envelope naming the code,
    not as a deck."""
    envelope = _provider_run(TEN_WEEK_PRD.replace(
        "The build is a fixed ten-week engagement",
        "The build is a fixed three-week engagement"))
    assert envelope["status"] == "error", envelope.get("status")
    assert envelope["error"]["code"] == "E_DOCUMENT_CONTRADICTED"
    # Both values, so a reviewer never has to reopen the document: the guard
    # read "three-week" from the prose and the packet's plan from §6's table,
    # which is exactly the independence the module is built on.
    assert "3-week build" in envelope["error"]["message"]
    assert "10 weeks" in envelope["error"]["message"]


def test_a_loyal_prd_is_not_refused_by_the_real_provider():
    """The other half, so the test above cannot pass by refusing everything."""
    envelope = _provider_run(TEN_WEEK_PRD)
    assert envelope["status"] != "error" or \
        envelope["error"]["code"] != "E_DOCUMENT_CONTRADICTED"


# --- present and unreadable (plan criterion 6) -----------------------------
# A document with no §6 and a document whose §6 is present but states no phase
# row produced the IDENTICAL reason string until 2026-09-20, so template drift
# -- a renamed column, an empty draft table, a heading that stopped matching --
# degraded exactly like a PRD that never had a plan. With the paper out of the
# chain there is nothing behind a PRD any more, so that is the 24-week failure
# with the plan coming from nowhere rather than from the wrong place.

import prd_section_parsers as parsers

_DRIFTED = TEN_WEEK_PRD.replace(
    "| Discovery Scope Wks 1–2 | Lock scope | Definitions |\n"
    "| Phase 1 Alpha Wks 3–4 | Stand it up | Pipeline |\n"
    "| Phase 2 Beta Wks 5–6 | Price it | Model |\n"
    "| Phase 3 Gamma Wks 7–8 | Learn | Attribution |\n"
    "| Hardening Handover Wks 9–10 | Prove it | Acceptance |\n",
    "| Discovery | Lock scope | Definitions |\n")


def test_a_present_but_unreadable_section_is_named():
    found = dict(parsers.unreadable_sections(_DRIFTED))
    assert "section 6" in found
    assert "present but states no phase" in found["section 6"]


def test_genuine_silence_is_not_a_misread():
    """A document that simply has no §6 must not be refused for it: that is
    every opportunity paper, and a supporting attachment besides."""
    assert parsers.unreadable_sections("# 1. Executive Summary\n\nNothing.\n") == []


def test_a_readable_prd_reports_nothing():
    assert parsers.unreadable_sections(TEN_WEEK_PRD) == []


def test_only_sections_with_nothing_behind_them_are_listed():
    """§7, §14 and §3.1 have a second pass reading the same document and a deck
    standard behind that, so one reading empty degrades rather than blinds. A
    reader refusing over those would block a thin PRD that can still produce an
    honest deck."""
    thin = TEN_WEEK_PRD + "\n# 7. Functional Requirements\n\nProse, no table.\n"
    assert [label for label, _r in parsers.unreadable_sections(thin)] == []


def test_the_guard_cannot_see_this_which_is_why_it_is_checked_first():
    """A §6 that yields no phases leaves the duration check with nothing to
    compare, so `check_prd_loyalty` PASSES a document whose plan was never
    read. That is the hole this fills, stated as a test so the ordering in
    `_assemble_one` is not moved by accident."""
    result = check_prd_loyalty(_DRIFTED, _Packet())
    assert result["ok"]
    assert result["checked"] == []
    assert parsers.unreadable_sections(_DRIFTED)


def test_the_real_provider_refuses_an_unreadable_prd():
    """THE WIRING, on the same terms as the loyalty guard's: a check nobody
    calls passes everything."""
    envelope = _provider_run(_DRIFTED)
    assert envelope["status"] == "error", envelope.get("status")
    assert envelope["error"]["code"] == "E_DOCUMENT_UNREADABLE"
    assert "section 6" in envelope["error"]["message"]


def test_a_readable_prd_is_not_refused_by_the_real_provider():
    """The other half, so the test above cannot pass by refusing everything."""
    envelope = _provider_run(TEN_WEEK_PRD)
    assert (envelope["status"] != "error"
            or envelope["error"]["code"] != "E_DOCUMENT_UNREADABLE")
