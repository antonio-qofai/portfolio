"""The adaptive commercial slide, from a PRD's §11.2 to the blocks on the deck.

`build-plan-commercial-slide.md` steps A2 to A4. The readers are pinned in
`test_commercial_prd_readers.py`; this file drives the chain through the real
assembly, fill, packet document, adapter and assembler, and then the blocks
`commercial_blocks.normalise` writes into a rendered deck. Every document here
is hand-built and neutral: the suite names no client.
"""

import re

import commercial_blocks
import packet_assembly
import packet_document
import packet_fill
import paper_extraction
import source_span
from commercial_blocks import normalise, render_investment, render_returns, render_terms
from data_source_adapter import INDICATIVE_FOOTNOTE, map_packet
from html_edit_layer import list_missing_markers
from live_proposal_provider import LiveProposalProvider
from prompt_assembler import assemble_prompt
from template_loader import load_template
from test_commercial_prd_readers import SECTION_11

import os

TEMPLATE = os.path.join(os.path.dirname(__file__), "..", "templates",
                        "proposal-template.md")

APPENDIX = """
# Appendix A: Financial Model Reference
Impact model. The levers combine to an annual EBITDA impact, in points of EBITDA margin.
| Scenario | Annual EBITDA Impact | Margin Uplift | Interpretation |
| --- | --- | --- | --- |
| Conservative | $100,000 | +2.5 pts | the floor |
| Ambitious | $166,000 | +4.5 pts | the ceiling |
"""

PRD = SECTION_11 + "\n" + APPENDIX
DOCUMENT = source_span.Document(name="a-prd.docx", kind="docx")


def _mapped(text=PRD):
    packet = packet_assembly.assemble("OPP-TEST", text, DOCUMENT)
    fill, sources = packet_fill.fill_map(packet=packet)
    markdown = packet_document.build(request={"company": "A", "project": "B"},
                                     packet=packet, fill=fill, sources=sources)
    return map_packet(markdown, {"project": "B"})


# ------------------------------- the chain ----------------------------------

def test_the_prd_half_reaches_the_slide_roles_verbatim():
    mapped = _mapped()
    assert mapped["investment_rows"] == [
        {"label": "Year 1", "value": "$40K–$60K"},
        {"label": "Ongoing", "value": "$10K–$14K/yr est."},
    ]
    assert mapped["return_rows"] == [
        {"scenario": "Conservative", "annual_ebitda": "$100,000/yr",
         "margin": "+2.5pp", "payback": "4.1–6.0 months"},
        {"scenario": "Ambitious", "annual_ebitda": "$166,000/yr",
         "margin": "+4.5pp", "payback": "2.3–3.3 months"},
    ]


def test_a_prd_states_no_deal_so_terms_wait_for_a_reviewer():
    mapped = _mapped()
    assert mapped["terms_rows"] == []
    prompt = assemble_prompt(load_template(TEMPLATE), mapped)
    assert "terms_rows: [MISSING: terms_rows]" in prompt


def test_no_chart_is_drawn_without_its_three_figures():
    mapped = _mapped()
    assert mapped["value_mapping"] == []
    prompt = assemble_prompt(load_template(TEMPLATE), mapped)
    assert "value_mapping" not in prompt
    assert "[MISSING: qofai_comp]" not in prompt


def test_the_indicative_footnote_is_chosen_and_never_marked_unconfirmed():
    mapped = _mapped()
    assert mapped["terms_footnote"] == INDICATIVE_FOOTNOTE
    assert "terms_footnote" not in mapped["_gap_roles"]


def test_a_firm_quote_gets_no_indicative_footnote():
    firm = PRD.replace("QofAI indicative estimate;", "QofAI quote;") \
              .replace("indicative Year-1 payback", "Year-1 payback")
    assert _mapped(firm)["terms_footnote"] == ""


def test_no_cost_line_item_reaches_the_prompt():
    prompt = assemble_prompt(load_template(TEMPLATE), _mapped())
    for line_item in ("Data pipeline", "retainer", "midpoint"):
        assert line_item not in prompt, line_item


def test_a_paper_with_no_cost_table_draws_no_investment_block():
    mapped = _mapped(APPENDIX)
    assert mapped["investment_rows"] == []
    prompt = assemble_prompt(load_template(TEMPLATE), mapped)
    assert "investment_rows" not in prompt


def test_payback_joins_cases_the_second_pass_fills():
    # A PRD whose dollar column the deterministic scenario reader does not
    # recognise has its cases filled later by the second pass. The payback is
    # keyed on the PRD's own scenario table (`scenario_margins`), so it is
    # there to join when they arrive.
    unrecognised = PRD.replace("Annual EBITDA Impact", "Annual Value")
    packet = packet_assembly.assemble("OPP-TEST", unrecognised, DOCUMENT)
    payback = next(f for f in packet.fields if f.field == packet_assembly.PAYBACK)
    assert set(payback.value) == {"conservative", "ambitious"}


def test_the_second_pass_cannot_write_a_cost_or_a_payback():
    # A paper's prose carries third-party vendor costs a model could mistake for
    # QofAI's, so the new paths are deterministic only.
    for slot in paper_extraction.SCOPE:
        assert not slot.path.startswith("commercial.investment"), slot.path
        assert not slot.path.startswith("commercial.cost"), slot.path
        leaves = {leaf.name for leaf in slot.leaves}
        assert "payback" not in leaves, slot.path


# --------------------- several opportunities, one slide ---------------------

def test_two_opportunities_rows_are_labelled_and_never_summed():
    first = [{"label": "Year 1", "value": "$40K–$60K"}]
    second = [{"label": "Year 1", "value": "$90K–$110K"}]
    sections = [
        {"fill": {"commercial.investment": first}, "opportunity": {"title": "First"}},
        {"fill": {"commercial.investment": second}, "opportunity": {"title": "Second"}},
    ]
    gathered = LiveProposalProvider._gather_scenarios(None, {}, sections)
    assert gathered["commercial.investment"] == [
        {"label": "Year 1", "value": "$40K–$60K", "opportunity": "First"},
        {"label": "Year 1", "value": "$90K–$110K", "opportunity": "Second"},
    ]


def test_one_opportunity_carries_no_label():
    sections = [{"fill": {"commercial.investment": [{"label": "Year 1", "value": "$1"}]},
                 "opportunity": {"title": "Only"}}]
    assert LiveProposalProvider._gather_scenarios(None, {}, sections) == {}


# ------------------------------ the blocks ----------------------------------

def _rendered_slide(extra=""):
    """A slide 5 the way a render following the spec draws it."""
    return (
        '<section class="slide" data-slide="8"><div class="body"><div class="comm">'
        '<div class="deal-top"><div class="invest">model text</div>'
        '<div class="returns">model text</div></div>'
        '<div class="deal-terms" style="grid-template-columns:repeat(1,1fr)">'
        '<div class="term term--empty"><div class="term-l">Terms</div>'
        '<div class="term-v"><span class="flag">[MISSING: terms_rows]</span></div>'
        f"</div></div>{extra}"
        '<div class="footnote">note</div></div></div></section>'
    )


def test_normalise_writes_every_block_from_the_data():
    mapped = _mapped()
    html, report = normalise(_rendered_slide(), mapped)
    assert report == {"written": ["investment", "return", "terms"], "missing": []}
    assert 'data-value="$10K–$14K/yr est."' in html
    assert 'data-payback="4.1–6.0 months"' in html
    assert "model text" not in html


def test_the_empty_terms_box_keeps_a_marker_the_studio_can_fill():
    html, _report = normalise(_rendered_slide(), _mapped())
    markers = [m["field"] for m in list_missing_markers(html)]
    assert markers == ["terms_rows"]


def test_a_missing_container_is_reported_never_raised():
    mapped = _mapped()
    slide = _rendered_slide().replace('<div class="returns">model text</div>', "")
    html, report = normalise(slide, mapped)
    assert "return" in report["missing"]
    assert "investment" in report["written"]


def test_a_deck_with_no_commercial_slide_is_untouched():
    html = "<section class='slide'>status</section>"
    assert normalise(html, {}) == (html, {"written": [], "missing": []})


def test_terms_rows_lay_out_three_across_at_most():
    rows = [{"label": f"T{n}", "value": str(n)} for n in range(5)]
    assert "repeat(3,1fr)" in render_terms(rows)
    assert "repeat(2,1fr)" in render_terms(rows[:2])


def test_blocks_escape_everything_they_are_given():
    evil = "<script>x</script>"
    for fragment in (render_investment([{"label": evil, "value": evil}]),
                     render_returns([{"scenario": evil, "payback": evil}]),
                     render_terms([{"label": evil, "value": evil}])):
        assert "<script>" not in fragment


def test_a_column_no_case_carries_is_not_drawn():
    table = render_returns([{"scenario": "A", "annual_ebitda": "$1/yr"}])
    assert "<th>Annual EBITDA</th>" in table
    assert "<th>Payback</th>" not in table and "<th>Margin</th>" not in table


def test_opportunities_head_their_own_rows_and_no_total_is_drawn():
    table = render_returns([
        {"opportunity": "First", "scenario": "A", "annual_ebitda": "$1/yr"},
        {"opportunity": "Second", "scenario": "A", "annual_ebitda": "$2/yr"},
    ])
    assert table.count('class="ret-opp"') == 2
    assert "total" not in table.lower()


def test_the_chart_rows_are_sized_from_the_figures():
    cases = [{"scenario": "Base Case", "ebitda_gain": "+2pp",
              "qofai_comp": "$100K", "client_retained_ebitda": "$400K",
              "enterprise_value": "$1M"}]
    slide = _rendered_slide('<div class="valuemap"><div class="vm-rows">x</div></div>')
    html, report = normalise(slide, dict(_mapped(), value_mapping=cases))
    assert "chart" in report["written"]
    assert re.search(r'vm-seg comp" style="width:10\.0%', html), html


def test_the_module_names_no_client():
    source = open(commercial_blocks.__file__, encoding="utf-8").read()
    for client in ("Contoso", "Tailspin Industrial Group", "TIG", "Fabrikam", "Ridgeline"):
        assert client not in source
