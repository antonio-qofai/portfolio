"""Tests for the ranking pass — which of slide 2's bullets matter most.

Everything here runs against an INJECTED FAKE client, like every other model test
in this suite. Nothing reaches the network.

What this file is mostly about is the REPAIR half, and it is asserted the way
`test_paper_writing` asserts its refusals: by handing the reader answers that are
wrong in each of the specific ways a ranking model can be wrong — an index that is
not a bullet, the same index twice, indices left out, a panel nobody asked about,
no answer at all, unreadable JSON, an outright exception — and showing that every
one of them lands on a valid order rather than on a broken deck.

The difference from the other two passes, and it is the reason this module is
small: this one answers in INTEGERS. There is no span to verify and no sentence to
check, because no character the model writes is ever copied into the result. The
test that pins that property is
`test_no_text_from_the_answer_can_reach_the_bullets`.
"""

import json

import pytest

import bullet_ranking
from bullet_ranking import (AFTER, TODAY, Ranking, RankingError, apply_order,
                            build_request, ledger, make_ranker, rank,
                            read_response, run)


class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Message:
    def __init__(self, text):
        self.content = [_Block(text)]


class FakeClient:
    """A stand-in for `anthropic.Anthropic`, recording what it was asked."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    @property
    def messages(self):
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _Message(json.dumps(self.answer))


class ExplodingClient:
    calls = ()

    @property
    def messages(self):
        return self

    def create(self, **kwargs):
        raise RuntimeError("the model is having a day")


TODAY_BULLETS = [
    "Maintenance and repairs are charged to cost of services as incurred, so the "
    "line is not separately disclosed",
    "Postpaid churn of 1.12% in Q4 2024 against a 0.86% full-year benchmark",
    "An older asset base typically correlates with higher failure rates",
]
AFTER_BULLETS = [
    "Reduce mean time to repair by 30-40% and unplanned outages by up to 40%",
    "Computer vision analysis of drone-captured inspection imagery",
    "Converting emergency, unplanned repairs to scheduled maintenance",
    "Model refinement; autonomous remediation; cross-domain correlation",
]
PANELS = {TODAY: TODAY_BULLETS, AFTER: AFTER_BULLETS}


def answer(**orders):
    return {"panels": [{"panel": panel, "order": list(order)}
                       for panel, order in orders.items()]}


def read(payload, panels=None):
    return read_response(panels or PANELS, _Message(json.dumps(payload)))


# --- the happy path --------------------------------------------------------

def test_a_clean_answer_orders_each_panel():
    ranking = read(answer(today=[2, 0, 1], after=[3, 1, 0, 2]))
    assert ranking.orders[TODAY] == (2, 0, 1)
    assert ranking.orders[AFTER] == (3, 1, 0, 2)
    assert ranking.notes == ()


def test_the_order_reaches_the_bullets_in_the_order_it_names():
    ranking = read(answer(today=[2, 0, 1]))
    ordered = apply_order(TODAY_BULLETS, ranking.orders[TODAY])
    assert ordered == [TODAY_BULLETS[2], TODAY_BULLETS[0], TODAY_BULLETS[1]]


def test_the_request_numbers_every_bullet_and_carries_the_slide_context():
    _system, message = build_request(PANELS, "A headline", "A summary line.")
    assert 'number="0"' in message and 'number="2"' in message
    assert TODAY_BULLETS[0] in message
    assert "A headline" in message and "A summary line." in message
    assert 'name="today"' in message and 'name="after"' in message


def test_the_system_prompt_forbids_writing_rather_than_only_asking_for_an_order():
    system, _message = build_request(PANELS)
    assert "not writing" in system.lower()
    assert "rephrasing" in system.lower()


# --- the repairs, which are the module -------------------------------------

def test_an_index_that_is_not_a_bullet_is_dropped_and_named():
    ranking = read(answer(today=[0, 9, 1, 2]))
    assert ranking.orders[TODAY] == (0, 1, 2)
    assert any("9" in note for note in ranking.notes), ranking.notes


def test_a_negative_index_is_not_treated_as_counting_from_the_end():
    ranking = read(answer(today=[-1, 0, 1, 2]))
    assert ranking.orders[TODAY] == (0, 1, 2)


def test_something_that_is_not_a_number_at_all_is_dropped():
    ranking = read({"panels": [{"panel": TODAY, "order": ["first", None, 1]}]})
    assert sorted(ranking.orders[TODAY]) == [0, 1, 2]
    assert ranking.orders[TODAY][0] == 1, ranking.orders[TODAY]


def test_a_repeated_index_is_kept_once_and_named():
    ranking = read(answer(today=[1, 1, 0, 2]))
    assert ranking.orders[TODAY] == (1, 0, 2)
    assert any("more than once" in note for note in ranking.notes), ranking.notes


def test_bullets_the_model_left_out_keep_their_own_order_at_the_end():
    ranking = read(answer(after=[3, 1]))
    assert ranking.orders[AFTER] == (3, 1, 0, 2)
    assert any("unranked" in note for note in ranking.notes), ranking.notes


def test_a_panel_nobody_asked_about_is_ignored_and_named():
    ranking = read({"panels": [{"panel": "commercial", "order": [0]}]})
    assert "commercial" not in ranking.orders
    assert any("commercial" in note for note in ranking.notes), ranking.notes


def test_a_panel_with_no_answer_keeps_the_order_it_arrived_in():
    ranking = read(answer(today=[2, 1, 0]))
    assert ranking.orders[AFTER] == (0, 1, 2, 3)
    assert any("no order returned" in note for note in ranking.notes)


def test_an_empty_order_leaves_the_panel_untouched():
    ranking = read(answer(today=[]))
    assert ranking.orders[TODAY] == (0, 1, 2)


def test_every_repair_still_produces_a_permutation():
    """The invariant that matters: whatever comes back, no bullet is lost and
    none is duplicated, so the panel shows the same set it was given."""
    for bad in ([0, 0, 0], [9, 9], [], [2], ["x", 1, 1, 7, -3], [1, 2, 0]):
        ranking = read(answer(today=bad))
        assert sorted(ranking.orders[TODAY]) == [0, 1, 2], (bad, ranking.orders)


def test_an_unreadable_answer_raises_rather_than_guessing():
    with pytest.raises(RankingError):
        read_response(PANELS, _Message("not json at all"))
    with pytest.raises(RankingError):
        read_response(PANELS, _Message(json.dumps([1, 2, 3])))


# --- the property that makes this pass safe --------------------------------

def test_no_text_from_the_answer_can_reach_the_bullets():
    """The structural guarantee. The reader takes integers and nothing else, so
    text smuggled into the response has nowhere to go."""
    payload = {"panels": [{
        "panel": TODAY,
        "order": [1, 0, 2],
        "text": "An invented figure of $9.9M in annual savings.",
        "bullets": ["A replacement bullet nobody sourced."],
    }]}
    ranking = read(payload)
    ordered = apply_order(TODAY_BULLETS, ranking.orders[TODAY])
    assert ordered == [TODAY_BULLETS[1], TODAY_BULLETS[0], TODAY_BULLETS[2]]
    assert set(ordered) == set(TODAY_BULLETS)
    for bullet in ordered:
        assert bullet in TODAY_BULLETS


def test_apply_order_refuses_an_order_that_is_not_a_permutation():
    assert apply_order(TODAY_BULLETS, [0, 1]) == TODAY_BULLETS
    assert apply_order(TODAY_BULLETS, [0, 1, 5]) == TODAY_BULLETS


def test_order_for_falls_back_to_the_identity_on_a_stale_order():
    ranking = Ranking(orders={TODAY: (0, 1, 2)})
    assert ranking.order_for(TODAY, 3) == (0, 1, 2)
    assert ranking.order_for(TODAY, 5) == (0, 1, 2, 3, 4)
    assert ranking.order_for("nobody", 2) == (0, 1)


# --- the call, and every way it degrades -----------------------------------

def test_the_call_is_made_with_the_schema_that_admits_integers_only():
    client = FakeClient(answer(today=[0, 1, 2], after=[0, 1, 2, 3]))
    rank(PANELS, "Headline", "Summary", client=client)
    schema = client.calls[0]["output_config"]["format"]["schema"]
    order = schema["properties"]["panels"]["items"]["properties"]["order"]
    assert order["items"] == {"type": "integer"}


def test_a_panel_with_one_bullet_is_not_worth_asking_about():
    client = FakeClient(answer())
    ranking = rank({TODAY: ["only one"], AFTER: AFTER_BULLETS}, client=client)
    asked = client.calls[0]["messages"][0]["content"]
    assert 'name="today"' not in asked
    assert 'name="after"' in asked
    assert ranking.orders[TODAY] == (0,)


def test_nothing_to_rank_makes_no_call_at_all():
    client = FakeClient(answer())
    ranking = rank({TODAY: ["one"], AFTER: []}, client=client)
    assert client.calls == []
    assert ranking.orders[TODAY] == (0,)


def test_with_no_ranker_the_bullets_keep_the_order_they_arrived_in():
    ranking = run(PANELS, ranker=None)
    assert ranking.orders[TODAY] == (0, 1, 2)
    assert ranking.orders[AFTER] == (0, 1, 2, 3)
    assert ranking.notes == ()


def test_a_ranker_that_raises_leaves_the_deck_exactly_as_it_was():
    """The pass sits on top of a deck that already renders, so no failure of it
    may sink a render."""
    def boom(panels, headline="", summary=""):
        raise RuntimeError("no api key")

    ranking = run(PANELS, ranker=boom)
    assert ranking.orders[TODAY] == (0, 1, 2)
    assert any("failed" in note for note in ranking.notes), ranking.notes


def test_a_client_that_raises_degrades_the_same_way_through_the_factory():
    ranker = make_ranker(client=ExplodingClient())
    ranking = run(PANELS, ranker=ranker)
    assert ranking.orders[AFTER] == (0, 1, 2, 3)
    assert any("RuntimeError" in note for note in ranking.notes), ranking.notes


def test_an_unreadable_answer_degrades_rather_than_propagating():
    class Garbage(FakeClient):
        def create(self, **kwargs):
            return _Message("<html>rate limited</html>")

    ranking = run(PANELS, ranker=make_ranker(client=Garbage(None)))
    assert ranking.orders[TODAY] == (0, 1, 2)
    assert any("RankingError" in note for note in ranking.notes), ranking.notes


def test_empty_panels_need_no_ranker_and_no_call():
    assert run({}, ranker=None).orders == {}
    assert run({TODAY: []}, ranker=None).orders == {TODAY: ()}


# --- the ledger ------------------------------------------------------------

def test_the_ledger_records_the_order_and_whether_it_moved():
    entries = ledger(read(answer(today=[2, 0, 1], after=[0, 1, 2, 3])), PANELS)
    by_panel = {entry["panel"]: entry for entry in entries["panels"]}
    assert by_panel[TODAY]["order"] == [2, 0, 1]
    assert by_panel[TODAY]["reordered"] is True
    assert by_panel[AFTER]["reordered"] is False
    assert by_panel[TODAY]["path"] == "today_pain_points[]"


def test_the_ledger_carries_every_repair_so_a_salvaged_answer_is_visible():
    entries = ledger(read(answer(today=[9, 9, 0])), PANELS)
    assert entries["notes"], entries


def test_a_panel_with_nothing_to_order_is_not_in_the_ledger():
    assert ledger(read(answer(), {TODAY: ["one"]}), {TODAY: ["one"]}) is None
    assert ledger(None, PANELS) is None


def test_the_two_panels_share_no_bullet_between_clients():
    """The anti-hardcoding check on the new channel: nothing in this module
    carries content, so a second call with different bullets returns only an
    order over those bullets."""
    other = {TODAY: ["A wholly different problem", "And another"],
             AFTER: ["A wholly different capability", "And another one"]}
    client = FakeClient(answer(today=[1, 0], after=[1, 0]))
    ranking = rank(other, client=client)
    ordered = apply_order(other[TODAY], ranking.orders[TODAY])
    assert set(ordered) == set(other[TODAY])
    for bullet in ordered:
        assert bullet not in TODAY_BULLETS


def test_the_module_names_no_client_and_no_figure():
    """Same bar as every other module here: nothing about one company."""
    import inspect

    source = inspect.getsource(bullet_ranking)
    for forbidden in ("Lamna", "Ridgeline", "FBK", "Northwind", "$"):
        assert forbidden not in source, forbidden
