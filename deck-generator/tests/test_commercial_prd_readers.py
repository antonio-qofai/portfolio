"""The commercial slide's PRD half: §11.2's cost total and payback per case.

`build-plan-commercial-slide.md` step A1. Every document here is hand-built and
neutral, because the suite names no client. Each one carries a trap measured on
the three real PRDs on 2026-09-23, named in the test that uses it, so a future
change that reopens one fails here rather than on a client's deck. The real
files are measured separately, by a script outside the suite.
"""

import prd_section_parsers as parsers
from prd_section_parsers import cost_total, payback, unreadable_sections


def _doc(body):
    return body.strip("\n")


# A §11 in the shape all three current PRDs share, with every decoy "Total" row
# the real ones carry: one in the criteria table BEFORE the cost table, one in
# the scoring table AFTER it, and a Pros & cons cell that begins with the word.
SECTION_11 = _doc("""
# 11. Make-or-Buy Analysis
## 11.1 Evaluation Criteria
| Criterion | Why It Matters |
| --- | --- |
| Total cost (Yr 1 + ongoing) | Must clear the conservative $100,000 annual case |
## 11.2 Make: QofAI FDE Build
Indicative cost (QofAI build)

| Item | Year 1 | Ongoing |
| --- | --- | --- |
| Data pipeline | $10K–$14K | n/a |
| Support / maintenance retainer | n/a | $5K–$7K/yr est. |
| Indicative total (midpoint $50K / $12K per yr) | $40K–$60K | $10K–$14K/yr est. |

QofAI indicative estimate; each line is a range around QofAI's internal midpoint and must be confirmed in scoping. The earlier assessment assumed a $20K–$30K annual tooling budget. Against the conservative $100K annual case, indicative Year-1 payback is roughly 4.1–6.0 months (5 at the midpoint; ongoing cost is covered roughly 2× thereafter); against the ambitious $200K case, roughly 2.3–3.3 months — consistent with the 2.3–6.0 month range in the opportunity assessment.

### Pros & cons
| Pros | Cons |
| --- | --- |
| Lowest total cost at this scale | Build burden sits with the team |
## 11.4 Decision Matrix
| Criterion (Weight) | Make | Buy |
| --- | --- | --- |
| Total cost fit (10%) | 4 | 2 |
# 12. Build Milestones & Rollout
Not commercial.
""")

CASES = ("Conservative", "Ambitious")


# ------------------------------- cost_total ---------------------------------

def test_reads_only_the_total_row_of_the_item_table():
    found = cost_total(SECTION_11)
    assert found["rows"] == [
        {"label": "Year 1", "value": "$40K–$60K"},
        {"label": "Ongoing", "value": "$10K–$14K/yr est."},
    ]


def test_decoy_total_rows_outside_the_item_table_are_never_read():
    # The criteria row sits BEFORE the table and the matrix row AFTER it, and
    # both begin "Total". A section-wide scan would take the first.
    values = [row["value"] for row in cost_total(SECTION_11)["rows"]]
    assert not any("clear" in value or value == "4" for value in values)


def test_the_total_rows_own_label_never_travels():
    # "Indicative total (midpoint $50K / ...)" names QofAI's internal method.
    # Rows are labelled by their COLUMN, so the midpoint cannot reach a slide.
    assert "midpoint" not in repr(cost_total(SECTION_11)["rows"])


def test_line_items_are_never_read():
    assert "retainer" not in repr(cost_total(SECTION_11)).lower()
    assert "$10K–$14K" not in [row["value"] for row in cost_total(SECTION_11)["rows"]
                               if row["label"] == "Year 1"]


def test_a_plain_total_label_reads_too():
    doc = SECTION_11.replace("Indicative total (midpoint $50K / $12K per yr)",
                             "Indicative total")
    assert cost_total(doc)["rows"][0]["value"] == "$40K–$60K"


def test_a_column_stating_no_cost_is_left_out():
    doc = SECTION_11.replace("| $40K–$60K | $10K–$14K/yr est. |",
                             "| $40K–$60K | n/a |")
    assert cost_total(doc)["rows"] == [{"label": "Year 1", "value": "$40K–$60K"}]


def test_any_column_headers_are_read_as_given():
    doc = SECTION_11.replace("| Item | Year 1 | Ongoing |",
                             "| Item | Build | Run (annual) |")
    assert [row["label"] for row in cost_total(doc)["rows"]] == [
        "Build", "Run (annual)"]


def test_the_basis_is_read_from_the_paragraph_under_the_table():
    assert cost_total(SECTION_11)["basis"] == "indicative"
    firm = SECTION_11.replace("QofAI indicative estimate;", "QofAI quote;") \
                     .replace("indicative Year-1 payback", "Year-1 payback")
    assert cost_total(firm)["basis"] == ""


def test_no_total_row_yields_nothing_and_is_named_unreadable():
    # Summing the lines would print a figure the PRD does not state.
    doc = SECTION_11.replace(
        "| Indicative total (midpoint $50K / $12K per yr) | $40K–$60K | $10K–$14K/yr est. |\n",
        "")
    assert cost_total(doc) is None
    labels = [label for label, _reason in unreadable_sections(doc)]
    assert "section 11.2" in labels


def test_two_total_rows_are_refused_not_chosen_between():
    doc = SECTION_11.replace(
        "| Indicative total (midpoint",
        "| Total | $1K | $1K |\n| Indicative total (midpoint")
    assert cost_total(doc) is None


def test_a_prd_with_no_cost_table_is_silence_not_a_misread():
    doc = SECTION_11.replace("| Item | Year 1 | Ongoing |", "| Line | Year 1 | Ongoing |")
    assert cost_total(doc) is None
    assert "section 11.2" not in [label for label, _r in unreadable_sections(doc)]


def test_a_document_with_no_section_11_2_yields_nothing():
    assert cost_total("# 6. Phased Scope\nNothing about cost.") is None


# --------------------------------- payback ----------------------------------

def test_reads_each_cases_payback_as_the_prd_wrote_it():
    found, _reasons = payback(SECTION_11, CASES)
    assert found["conservative"]["text"] == "4.1–6.0 months"
    assert found["ambitious"]["text"] == "2.3–3.3 months"
    assert (found["conservative"]["low"], found["conservative"]["high"]) == (4.1, 6.0)


def test_a_semicolon_inside_a_parenthetical_does_not_split_the_clause():
    # "(5 at the midpoint; ongoing cost ...)" split naively hands the ambitious
    # clause a fragment and the conservative clause loses its end.
    found, _reasons = payback(SECTION_11, CASES)
    assert set(found) == {"conservative", "ambitious"}


def test_a_trailing_range_for_no_case_is_ignored_with_a_reason():
    found, reasons = payback(SECTION_11, CASES)
    assert found["ambitious"]["text"] == "2.3–3.3 months"
    assert any("names no case" in reason for reason in reasons)


def test_a_dollar_range_elsewhere_in_the_paragraph_is_never_read():
    found, _reasons = payback(SECTION_11, CASES)
    assert "20" not in found["conservative"]["text"]


def test_only_the_cases_the_scenario_table_names_are_keyed():
    found, _reasons = payback(SECTION_11, ("Conservative",))
    assert set(found) == {"conservative"}


def test_a_case_stated_twice_is_refused():
    doc = SECTION_11.replace(
        "roughly 2.3–3.3 months",
        "roughly 2.3–3.3 months; against the conservative case again, roughly 9 months")
    found, reasons = payback(doc, CASES)
    assert "conservative" not in found
    assert any("stated twice" in reason for reason in reasons)


def test_a_single_month_figure_reads():
    doc = SECTION_11.replace("roughly 4.1–6.0 months", "roughly 15 months")
    found, _reasons = payback(doc, CASES)
    assert found["conservative"] == {"text": "15 months", "low": 15.0, "high": 15.0}


def test_no_payback_sentence_yields_nothing():
    doc = SECTION_11.replace("payback", "return")
    assert payback(doc, CASES) == ({}, [])


def test_case_names_match_as_whole_words():
    # "Base" must not match inside "database".
    doc = SECTION_11.replace("Against the conservative $100K annual case",
                             "Against the database-driven conservative case")
    found, _reasons = payback(doc, ("Base", "Conservative", "Ambitious"))
    assert "base" not in found


def test_the_cost_readers_name_no_client():
    # Scoped to the §11.2 block: the module's older docstrings cite the real
    # PRD a measurement was taken on, which is history, not a hardcoded value.
    source = open(parsers.__file__, encoding="utf-8").read()
    block = source[source.index("§11.2: what the build costs"):
                   source.index("def unreadable_sections")]
    for client in ("Contoso", "Tailspin Industrial Group", "TIG", "Fabrikam"):
        assert client not in block
