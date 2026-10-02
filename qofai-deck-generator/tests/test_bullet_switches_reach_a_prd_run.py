"""The bullet switches have to reach a PRD run, not just exist.

WHY THIS FILE EXISTS. `bullet_toggles` was built on 2026-09-20, unit-tested in
`test_bullet_toggles.py`, and never once rendered. Antonio, reviewing the studio
that afternoon: "I don't see a toggle button on these bullets, which is not good
because I thought we had dealt with them."

The whole feature hung on one flag, `switches_live`, which was `bool(packet)`.
`packet` is a path to a frozen packet FILE, which only the retired fixture
source ever posted, so on every live run it was "" and the switch column was
silently dropped. 2257 tests passed and not one of them asserted that flag.

So this file asserts the GATE, from the outside, on the path a reviewer
actually uses: a live run with an attached PRD and no packet file anywhere.

Run with: python3 -m pytest tests/test_bullet_switches_reach_a_prd_run.py
"""

import io
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ui"))

from test_ui_attachments import (OK_RESULT, attachment, live_run,  # noqa: E402,F401
                                 studio)

SELECTION = {
    "panels": [
        {"panel": "today", "label": "TODAY", "role": "today_pain_bullets",
         "shown": 1, "of": 2,
         "kept": [{"text": "Captains handwrite the daily report on paper",
                   "source_position": 1}],
         "dropped": [{"text": "Fuel entry is optional across live projects",
                      "source_position": 2}],
         "ranked": True, "reordered": False, "room_px": 96.4,
         "overflowed": False, "note": "1 of 2 shown"},
    ],
    "notes": [],
    "trimmed": True,
    "ranked": True,
}

# A real sha256 shape. The value is what a base document's bytes hash to, and
# it is the only thing that identifies the run's source once the paper has left
# the chain.
SHA = "b" * 64

ATTACHMENTS = [{"filename": "Client_PRD.docx", "kind": "docx",
                "size_bytes": 4096, "sha256": SHA, "text": "the PRD text"}]


def _prd_run(studio, **result_extra):
    client, captured = studio
    captured["result"] = dict(OK_RESULT, bullet_selection=SELECTION,
                              attachments=ATTACHMENTS, **result_extra)
    return live_run(client, uploads=[attachment("PRD body", "Client_PRD.docx")]
                    ).get_data(as_text=True)


def test_a_prd_run_offers_the_switches(studio):
    """THE REGRESSION. A live run posts no packet path, and the switches have to
    be offered anyway, because the PRD is what the deck was written from."""
    html = _prd_run(studio)
    assert "Slide 2 bullet selection" in html, "the card itself must render"
    assert "<th>Switch</th>" in html, (
        "the switch column must reach a PRD run; this is the flag that was "
        "false on every live run for the life of the feature"
    )


def test_the_switches_are_offered_per_bullet_not_just_as_a_header(studio):
    """A header with no buttons under it would pass the test above and still
    give a reviewer nothing to press."""
    html = _prd_run(studio)
    assert html.count('class="inline bullet-switch-form"') == 2, (
        "one switch form per bullet the packet carried, kept or dropped"
    )


def test_a_switch_is_recorded_against_the_prd_and_not_a_packet_path(studio):
    """Antonio, 2026-09-20, asked which document a switch belongs to: the
    uploaded PRD, by content hash. Re-upload an edited PRD and the switches
    reset, because those are different bullets now."""
    import bullet_toggles

    html = _prd_run(studio)
    assert bullet_toggles.source_key(SHA) in html, (
        "the form must post the PRD's own key, so the route records the switch "
        "against the document rather than against a packet file that does not "
        "exist on this path"
    )


def test_a_run_with_no_document_at_all_still_renders_the_card_read_only(studio):
    """No PRD and no packet means nothing to key a switch against. The card is
    still the record of what the slide dropped, so it renders without the
    column rather than disappearing."""
    client, captured = studio
    captured["result"] = dict(OK_RESULT, bullet_selection=SELECTION,
                              attachments=[])
    html = live_run(client).get_data(as_text=True)
    assert "Slide 2 bullet selection" in html
    assert "<th>Switch</th>" not in html


# --- and the switch has to change what the next render is asked for ---------

def test_a_switch_recorded_against_the_prd_reaches_the_next_render(studio, tmp_path,
                                                                   monkeypatch):
    """The other half of the gate. Offering a switch that changes nothing on the
    next render would be the same defect one layer down, so this asserts the
    override actually reaches the adapter, keyed on the same PRD."""
    import app as ui_app
    import bullet_toggles

    store = str(tmp_path / "toggles.json")
    monkeypatch.setattr(ui_app, "TOGGLES_PATH", store)

    # The reviewer switches the dropped bullet ON, filed under the PRD's hash.
    body = b"PRD body"
    import hashlib
    scope = bullet_toggles.source_key(hashlib.sha256(body).hexdigest())
    bullet_toggles.set_toggle(scope, 0, "today_pain_bullets",
                              "Fuel entry is optional across live projects",
                              bullet_toggles.ON, path=store)

    client, captured = studio
    captured["result"] = dict(OK_RESULT, bullet_selection=SELECTION,
                              attachments=ATTACHMENTS)
    live_run(client, uploads=[(io.BytesIO(body), "Client_PRD.docx")])

    overrides = captured["kwargs"].get("bullet_overrides")
    assert overrides, (
        "a switch recorded against this PRD must reach the render; without this "
        "the column is offered and pressing it changes nothing"
    )
    assert overrides[0]["today_pain_bullets"] == {
        bullet_toggles.bullet_key("Fuel entry is optional across live projects"):
            bullet_toggles.ON
    }


def test_a_switch_filed_under_a_different_prd_does_not_leak_into_this_run(studio,
                                                                          tmp_path,
                                                                          monkeypatch):
    """The point of keying on the document. Another PRD's switches are not this
    deck's, and an edited PRD is another PRD."""
    import app as ui_app
    import bullet_toggles

    store = str(tmp_path / "toggles.json")
    monkeypatch.setattr(ui_app, "TOGGLES_PATH", store)
    other = bullet_toggles.source_key("a" * 64)
    bullet_toggles.set_toggle(other, 0, "today_pain_bullets",
                              "Fuel entry is optional across live projects",
                              bullet_toggles.ON, path=store)

    client, captured = studio
    captured["result"] = dict(OK_RESULT, bullet_selection=SELECTION,
                              attachments=ATTACHMENTS)
    live_run(client, uploads=[(io.BytesIO(b"PRD body"), "Client_PRD.docx")])

    assert not captured["kwargs"].get("bullet_overrides"), (
        "switches belong to the document they were made against"
    )
