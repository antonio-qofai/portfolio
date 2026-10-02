"""What the served calendar must not do.

The server is the first thing in this build that writes a human decision
without a human typing a command, so these tests are about the rules it could
quietly drop rather than about routing. Every one of them exists because the
route could plausibly have been written the other way.

Run it:  cd ui && ../.venv/bin/python test_app.py
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_AGENT_ROOT = _HERE.parent
for folder in ("calendar_model", "display", "approval", "narrative"):
    sys.path.insert(0, str(_AGENT_ROOT / folder))
sys.path.insert(0, str(_HERE))

import app as app_mod  # noqa: E402
import slots  # noqa: E402

PASSED = []
FAILED = []


def check(name):
    def wrap(fn):
        try:
            fn()
        except AssertionError as exc:
            FAILED.append(f"FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            FAILED.append(f"ERROR {name}: {type(exc).__name__}: {exc}")
        else:
            PASSED.append(f"ok  {name}")
        return fn

    return wrap


# The page shows windows that have not fully passed, so a test reading the
# real October windows on the real clock would start failing on 2026-10-31.
# Pinned to a day those windows are live.
TEST_TODAY = date(2026, 9, 18)


def _client(state_root):
    # Both roots, because since 2026-09-20 they are separate: windows are read
    # from the repo and decisions are written wherever the store resolved to.
    # A sandbox that set only the first would write real decisions into the
    # real store while claiming to be isolated.
    app_mod.DEFAULT_STATE_ROOT = Path(state_root)
    app_mod.DECISION_STORE_ROOT = Path(state_root)
    app_mod._today = lambda: TEST_TODAY
    app_mod.app.config["TESTING"] = True
    return app_mod.app.test_client()


def _sandbox():
    """A state root holding real windows, so slot ids are the real ones."""
    tmp = Path(tempfile.mkdtemp())
    real = _AGENT_ROOT / "state"
    for path in real.glob("window-*-*.json"):
        if path.name.count(".") == 1:
            shutil.copy(path, tmp / path.name)
    return tmp


def _any_slot_id(state_root):
    taxonomy = slots.load_taxonomy()
    for lens in taxonomy.v1_lens_order:
        path = slots.newest_window_path(state_root, lens=lens)
        if path is None:
            continue
        window = slots.read_window(path)
        if window and window.slots:
            return window.slots[0].slot_id
    raise AssertionError("no window on disk to test against")


def _post(client, body):
    response = client.post("/decide", json=body)
    return response.status_code, json.loads(response.data)


def _card_markup(page, slot_id):
    """The one card for `slot_id`, found without pinning its attribute order.

    The grid entry for a slot carries the same `data-slot`, and it comes
    first, so this anchors on the article tag. Matched with a regex rather
    than a literal because the attributes on that tag are presentation and
    move around; a test that breaks when `draggable` is added is a test about
    the wrong thing.
    """
    match = re.search(
        r"<article\b[^>]*\bdata-slot=\"" + re.escape(slot_id) + r"\"[^>]*>(.*?)</article>",
        page,
        re.S,
    )
    assert match, f"no card rendered for {slot_id}"
    return match.group(1)


@check("a decision with nobody behind it is refused")
def _():
    root = _sandbox()
    client = _client(root)
    slot = _any_slot_id(root)
    status, body = _post(client, {"by": "", "decisions": [{"slot_id": slot, "state": "approved"}]})
    assert status == 400 and body["ok"] is False, (status, body)
    assert not (root / "approvals.jsonl").exists(), "a refused decision still wrote to the store"


@check("a decline with no reason is refused, the same as on the CLI")
def _():
    root = _sandbox()
    client = _client(root)
    slot = _any_slot_id(root)
    status, body = _post(
        client, {"by": "A Reviewer", "decisions": [{"slot_id": slot, "state": "rejected", "note": ""}]}
    )
    assert status == 400 and body["ok"] is False, (status, body)
    assert "note" in body["detail"] or "reason" in body["detail"], body["detail"]
    assert not (root / "approvals.jsonl").exists()


@check("the page records published only with a link, and only on an approved post")
def _():
    # PRD Section 2's hard constraint is that the agent never posts, and it
    # still does not. Since 2026-09-23 a person can record that a post went
    # out, through `queue.decide`, which refuses it without a link or on a
    # post nobody approved. The server must not become the way around that.
    root = _sandbox()
    client = _client(root)
    slot = _any_slot_id(root)
    link = "https://www.linkedin.com/posts/jordan_ai-activity-7123456789012345678-AbCd"

    status, body = _post(
        client, {"by": "A Reviewer", "decisions": [{"slot_id": slot, "state": "published", "url": link}]}
    )
    assert status == 400 and body["ok"] is False, ("a draft was marked published", status, body)
    assert not (root / "approvals.jsonl").exists()

    _post(client, {"by": "A Reviewer", "decisions": [{"slot_id": slot, "state": "approved"}]})
    status, body = _post(
        client, {"by": "A Reviewer", "decisions": [{"slot_id": slot, "state": "published"}]}
    )
    assert status == 400 and body["ok"] is False, ("published with no link", status, body)

    status, body = _post(
        client, {"by": "A Reviewer", "decisions": [{"slot_id": slot, "state": "published", "url": link}]}
    )
    assert status == 200 and body["ok"] is True, (status, body)
    page = client.get("/").data.decode("utf-8")
    card = _card_markup(page, slot)
    assert 'class="badge state-published"' in card, "the store says published and the card does not"
    assert f'href="{link}"' in card, "a published post does not link to where it went out"
    assert re.search(r'<div class="mini"[^>]*data-slot="' + re.escape(slot) + r'"[^>]*data-state="published"', page)


@check("a slot id from no calendar is refused before anything is written")
def _():
    root = _sandbox()
    client = _client(root)
    status, body = _post(
        client, {"by": "A Reviewer", "decisions": [{"slot_id": "not-a-slot-01", "state": "approved"}]}
    )
    assert status == 400 and body["ok"] is False, (status, body)
    assert not (root / "approvals.jsonl").exists()


@check("one bad decision in a batch writes none of them")
def _():
    # The reason this matters: the store has no update call, so a partially
    # written batch cannot be taken back, and a reviewer who pressed one
    # button would have no way to find out which half of it counted.
    root = _sandbox()
    client = _client(root)
    good = _any_slot_id(root)
    status, body = _post(
        client,
        {
            "by": "A Reviewer",
            "decisions": [
                {"slot_id": good, "state": "approved"},
                {"slot_id": "not-a-slot-01", "state": "approved"},
            ],
        },
    )
    assert status == 400 and body["ok"] is False, (status, body)
    assert not (root / "approvals.jsonl").exists(), (
        "the good half of a refused batch was written, and the store cannot undo it"
    )


@check("a good decision is recorded, and the page then renders it from the store")
def _():
    root = _sandbox()
    client = _client(root)
    slot = _any_slot_id(root)
    status, body = _post(
        client, {"by": "A Reviewer", "decisions": [{"slot_id": slot, "state": "approved"}]}
    )
    assert status == 200 and body["ok"] is True, (status, body)

    rows = (root / "approvals.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 1, rows
    row = json.loads(rows[0])
    assert row["value"]["state"] == "approved" and row["value"]["by"] == "A Reviewer", row
    assert row["entry_key"] == slot, row

    page = client.get("/").data.decode("utf-8")
    assert 'class="badge state-approved"' in _card_markup(page, slot), (
        "the store records the approval and the page still shows a draft"
    )


@check("a correction is a newer row, never an edited one")
def _():
    root = _sandbox()
    client = _client(root)
    slot = _any_slot_id(root)
    _post(client, {"by": "A Reviewer", "decisions": [{"slot_id": slot, "state": "approved"}]})
    _post(
        client,
        {"by": "A Reviewer", "decisions": [{"slot_id": slot, "state": "rejected", "note": "wrong date"}]},
    )
    rows = (root / "approvals.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 2, f"a correction rewrote history instead of appending: {rows}"
    assert json.loads(rows[0])["value"]["state"] == "approved"
    assert json.loads(rows[1])["value"]["state"] == "rejected"


@check("the page served is live, and says where each calendar came from")
def _():
    root = _sandbox()
    client = _client(root)
    page = client.get("/").data.decode("utf-8")
    # Built by `url_for` since 2026-09-20, so it is rooted rather than
    # relative. On a direct request that is "/decide"; under the portal's proxy
    # it carries the mount prefix, which `ui/test_portal_mount.py` pins.
    assert 'data-record-url="/decide"' in page, (
        "the served page did not advertise the route, so its Record button stays hidden"
    )
    assert "window-" in page, "the provenance line does not name a window file"


@check("everyone's posts are on one calendar, not one calendar each")
def _():
    """The 2026-09-15 correction, and the thing most likely to regress.

    The page held three calendars stacked, one visible at a time, and that was
    read back as not what was asked for: "All three calendars should have all
    the postings for everyone." So there is exactly one month grid, every live
    lens has posts in it, and each post still says whose it is.
    """
    root = _sandbox()
    client = _client(root)
    page = client.get("/").data.decode("utf-8")

    assert page.count('<div class="grid">') == 1, (
        f"expected one month grid, found {page.count(chr(60) + 'div class=' + chr(34) + 'grid' + chr(34) + chr(62))}"
    )
    lenses = set(re.findall(r'<div class="mini"[^>]*data-lens="([a-z]+)"', page))
    taxonomy = slots.load_taxonomy()
    expected = {
        lens for lens in taxonomy.v1_lens_order
        if slots.newest_window_path(root, lens=lens) is not None
    }
    assert lenses == expected, f"the one calendar shows {lenses}, expected {expected}"

    # Every post in the month is draggable and every one names its person,
    # which are the two things that make a shared calendar usable.
    minis = re.findall(r'<div class="mini"([^>]*)>', page)
    assert minis, "no posts in the month"
    assert all('draggable="true"' in m for m in minis), (
        "a post in the month is not draggable, so it cannot be rescheduled there"
    )
    assert all('data-lens="' in m for m in minis), (
        "a post in the month does not say whose calendar it is on"
    )


@check("a post opens in a panel beside the month, not by scrolling down to it")
def _():
    """Added 2026-09-22: read and approve a post with the month still on screen.

    The panel ships empty and hidden, and lives inside the calendar view so
    the Post history tab hides it too. Its contents are copied in by the
    browser, so what is pinned here is the shell the script depends on.
    """
    root = _sandbox()
    client = _client(root)
    page = client.get("/").data.decode("utf-8")

    calendar = re.search(
        r'<div class="view" id="view-calendar">(.*?)<div class="view" id="view-history"', page, re.S
    )
    assert calendar, "no calendar view"
    assert page.count('id="detail"') == 1, "expected exactly one post panel"
    assert '<aside class="detail" id="detail" hidden' in calendar.group(1), (
        "the post panel is missing, visible before anything is opened, or outside the calendar view"
    )
    for hook in ('class="detailbody"', 'class="detailclose"', 'id="calwork"',
                 'data-step="-1"', 'data-step="1"', 'class="detailpos"'):
        assert hook in calendar.group(1), f"the panel script needs {hook}"

    minis = re.findall(r'<div class="mini"([^>]*)>', page)
    assert minis and all('tabindex="0"' in m for m in minis), (
        "a post in the month cannot be opened from the keyboard"
    )
    assert not any("read it below" in m for m in minis), (
        "a post in the month still promises to jump down the page"
    )


@check("the list is off the page, and the actions sit above the month")
def _():
    """2026-09-22: the panel replaced the list, and the toolbar moved to the month.

    The cards are still rendered, because the scripts read each post's date
    from its card, so what is pinned is that they are there and hidden.
    """
    root = _sandbox()
    page = _client(root).get("/").data.decode("utf-8")
    posts = re.search(r'<section class="posts" id="posts"( hidden)?>', page)
    assert posts and posts.group(1), "the list of cards is missing, or visible again"
    assert page.count("<article class=\"card\"") > 0, "the hidden list lost its cards"
    toolbar = page.find('<div class="toolbar">')
    grid = page.find('<div class="grid">')
    assert 0 <= toolbar < grid, "the toolbar is not above the month"
    for piece in ('class="lensbar"', 'class="reorderbar"', 'class="decidebar"'):
        assert page.find(piece, toolbar) < grid, f"{piece} is not in the toolbar above the month"
    assert "The posts</h2>" not in page, "the list's heading came back"


@check("a post the month cannot show gets a button that opens it")
def _():
    """A post dated outside its window has no cell, and the list that used to
    hold it is hidden, so without this it could not be reached at all."""
    root = _sandbox()
    october = slots.read_window(root / "window-jordan-2026-10.json")
    stray = slots.CalendarWindow(
        window_id=october.window_id, lens=october.lens,
        start_date=october.start_date, end_date=october.end_date,
        arc=october.arc, slots=list(october.slots) + [
            slots.Slot(slot_id="jordan-2026-10-01-99", date="2026-12-24",
                       item_ref=october.slots[0].item_ref, lens="jordan")
        ],
    )
    slots.write_window(root / "window-jordan-2026-10.json", stray)
    page = _client(root).get("/").data.decode("utf-8")
    assert '<section class="offgrid">' in page, "a post outside the month is not mentioned"
    assert 'data-open-slot="jordan-2026-10-01-99"' in page, "the notice cannot open the post"
    assert "below" not in re.search(r'<section class="offgrid">(.*?)</section>', page, re.S).group(1)

    clean = _client(_sandbox()).get("/").data.decode("utf-8")
    assert '<section class="offgrid">' not in clean, "the notice shows with nothing to report"


@check("a calendar of one post still wires Approve")
def _():
    """The script that wires every button stops when there is no reorder bar,
    and the bar used to be left out below two posts."""
    import render_calendar

    root = _sandbox()
    october = slots.read_window(root / "window-jordan-2026-10.json")
    single = slots.CalendarWindow(
        window_id=october.window_id, lens=october.lens,
        start_date=october.start_date, end_date=october.end_date,
        slots=[october.slots[0]],
    )
    page = render_calendar.render_html(
        [single], slots.load_taxonomy(), today=date(2026, 10, 1), agent_decided=True,
    )
    assert re.search(r'<div class="reorderbar" hidden>', page), (
        "a one-post calendar has no reorder bar, so no button on it is wired"
    )


@check("a decided post offers Undo, not the button that decided it")
def _():
    """2026-09-23: pressing Approve on an approved post used to do nothing."""
    root = _sandbox()
    client = _client(root)
    slot = _any_slot_id(root)
    _post(client, {"by": "A Reviewer", "decisions": [{"slot_id": slot, "state": "approved"}]})
    card = _card_markup(client.get("/").data.decode("utf-8"), slot)
    assert 'data-decide="draft"' in card and "Undo approval" in card, "an approved post has no Undo"
    assert 'data-decide="approved"' not in card, "an approved post still offers Approve"

    status, body = _post(client, {"by": "A Reviewer", "decisions": [{"slot_id": slot, "state": "draft"}]})
    assert status == 200 and body["ok"] is True, (status, body)
    card = _card_markup(client.get("/").data.decode("utf-8"), slot)
    assert 'class="badge state-draft"' in card and 'data-decide="approved"' in card, (
        "Undo did not put the post back to draft"
    )


@check("every alternative carries a Swap button naming the post it trades with")
def _():
    root = _sandbox()
    page = _client(root).get("/").data.decode("utf-8")
    offered = re.findall(r'data-swap="([^"]+)"', page)
    assert offered, "no alternative on the page can be swapped"
    refs = set(re.findall(r'<article class="card"[^>]*data-ref="([^"]+)"', page, re.S))
    assert all(ref in refs for ref in offered), (
        "a Swap button names a post that is not on this calendar"
    )


@check("the reviewer is asked once, by name, from the taxonomy")
def _():
    root = _sandbox()
    page = _client(root).get("/").data.decode("utf-8")
    taxonomy = slots.load_taxonomy()
    for lens in taxonomy.v1_lens_order:
        name = taxonomy.lens_person(lens)
        assert f'class="whopick" data-name="{name}"' in page, f"{name} is not offered"
    assert 'id="whomodal" hidden' in page, "the picker is open before the script decides it should be"


@check("a formatted post escapes whatever the file holds")
def _():
    """The formatter writes markup now, so a post containing markup must not."""
    import render_calendar

    out = render_calendar._render_post(
        "# Title\n\n<script>alert(1)</script> and **bold**\n\n- one\n- two\n\n"
        "[a link](https://example.com) [not a link](javascript:alert(1))"
    )
    assert "<script>" not in out and "&lt;script&gt;" in out, out
    assert '<h3 class="md1">Title</h3>' in out, out
    assert "<strong>bold</strong>" in out and "<ul><li>one</li><li>two</li></ul>" in out, out
    assert 'href="https://example.com"' in out, out
    assert 'href="javascript' not in out, out


@check("a published post finds its engagement by LinkedIn activity id")
def _():
    """The link a founder pastes and the link the portal returns are different
    shapes for the same post; the activity id is what they share."""
    import render_calendar

    sys.path.insert(0, str(_AGENT_ROOT / "ingestion"))
    from performance import History, PublishedRecord

    history = History(posts=[PublishedRecord(
        url="https://www.linkedin.com/feed/update/urn:li:activity:7123456789012345678",
        title="t", channel="linkedin", date_published="2026-10-01", engagements=4,
    )])
    pasted = "https://www.linkedin.com/posts/jordan_ai-activity-7123456789012345678-AbCd"
    found = render_calendar._engagement_by_slot({"jordan-2026-10-01-01": pasted}, history)
    assert found.get("jordan-2026-10-01-01") is not None, "the pasted link did not find its post"
    assert found["jordan-2026-10-01-01"].engagements == 4
    assert render_calendar._engagement_by_slot({"x": pasted}, None) == {}


@check("both months of a lens are on the page while both are live")
def _():
    """Once next month is assembled, this month's second half must stay visible."""
    root = _sandbox()
    october = slots.read_window(root / "window-jordan-2026-10.json")
    november = slots.CalendarWindow(
        window_id="jordan-2026-11-01", lens="jordan",
        start_date="2026-11-01", end_date="2026-11-30",
        slots=[slots.Slot(slot_id="jordan-2026-11-01-01", date="2026-11-02",
                          item_ref=october.slots[0].item_ref, lens="jordan")],
    )
    slots.write_window(root / "window-jordan-2026-11.json", november)
    page = _client(root).get("/").data.decode("utf-8")
    assert october.slots[-1].slot_id in page, "October's last post vanished from the page"
    assert "jordan-2026-11-01-01" in page, "November's post is not on the page"
    buttons = re.findall(r'data-select-lens="jordan"', page)
    assert len(buttons) == 1, f"the filter offers Jordan {len(buttons)} times, once per month"
    assert "October 2026" in page and "November 2026" in page, "arc cards do not say which month"


@check("a post's shape shows its plain name, with the taxonomy's description as the tooltip")
def _():
    """Antonio, 2026-09-15: "I'm not sure what the form is". A raw id explained nothing."""
    import html as html_mod
    root = _sandbox()
    page = _client(root).get("/").data.decode("utf-8")
    taxonomy = slots.load_taxonomy()
    checked = 0
    for lens in taxonomy.v1_lens_order:
        for path in slots.live_window_paths(root, lens=lens, today=TEST_TODAY):
            for slot in slots.read_window(path).slots:
                card = _card_markup(page, slot.slot_id)
                for form in slot.forms:
                    chips = re.findall(r'<span class="chip form[^"]*"([^>]*)>([^<]*)</span>', card)
                    labels = [html_mod.unescape(text) for _, text in chips]
                    assert taxonomy.form_label(form) in labels, (form, labels)
                    assert taxonomy.form_label(form) != form, f"{form} has no plain name"
                    assert form not in labels, f"the raw id {form} is still the label"
                    titles = [html_mod.unescape(attrs) for attrs, _ in chips]
                    wanted = taxonomy.form_descriptions[form].split()[0]
                    assert any(wanted in t for t in titles), f"{form} has no description tooltip"
                    checked += 1
    assert checked, "no card carried a form, so nothing was checked"


@check("the page never gives a founder a deadline")
def _():
    """Removed by owner decision on 2026-09-15, and worth a test to keep out.

    The approval horizon was a deadline: a post needed its decision fourteen
    days before it ran, and the page tinted those days, drew a line where the
    band ended, and told a founder that a post was wanted approved by now. The
    call was that a founder short on time will not approve on a fourteen-day
    lead and should not be nagged for it: the calendar exists to make the work
    easier, not to add a due date.

    So a slot is approved or it is not, and the page shows which without ever
    saying when. This is checked at a date deep inside the old horizon, where
    every one of those elements used to appear.
    """
    import render_calendar

    root = _sandbox()
    taxonomy = slots.load_taxonomy()
    windows = [
        slots.read_window(slots.newest_window_path(root, lens=lens))
        for lens in taxonomy.v1_lens_order
        if slots.newest_window_path(root, lens=lens) is not None
    ]
    assert windows, "no window to test against"

    # Inside the old approved horizon, which is where all of this used to draw.
    page = render_calendar.render_html(
        windows, taxonomy, today=date(2026, 10, 10), agent_decided=True
    )
    lowered = page.lower()
    for word in ("horizon", "deadline", "overdue", "approved by now", "due by", "not due yet"):
        assert word not in lowered, f"the page still tells a founder about a {word!r}"
    assert "req-approved" not in page, "a day is still shaded by when its post is due"
    assert "is-boundary" not in page, "the horizon boundary is still marked on a day"

    # What must survive: the states themselves, which is how approval is read.
    for state in ("draft", "approved", "rejected"):
        assert f"pip-{state}" in page, f"the {state} state lost its colour"
    assert "Approve" in page and "Decline" in page, "the decision buttons are gone"

    # The horizon stays in config and in the schema; only the display stopped
    # asking. That is what keeps this cheap to undo.
    assert taxonomy.approved_horizon_days == 14, (
        "the display change reached into policy; threads.yaml should be untouched"
    )


@check("the page's javascript actually parses")
def _():
    """The failure this catches is invisible in every other way.

    The scripts are Python strings assembled into the page, so a stray brace
    is a syntax error the browser reports to a console nobody is reading. The
    page still renders: every control is drawn, correctly styled, in the right
    place, and none of them do anything. That happened on 2026-09-15 while
    removing a superseded branch, and it happened silently past a passing test
    suite, because asserting that markup contains a string says nothing about
    whether the code around it runs.

    Skipped rather than failed when node is absent, so this suite still runs
    on a machine without it.
    """
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    if not node:
        print("   (node not installed; skipping the syntax check)")
        return

    root = _sandbox()
    client = _client(root)
    page = client.get("/").data.decode("utf-8")
    blocks = re.findall(r"<script>(.*?)</script>", page, re.S)
    assert blocks, "the page carries no script at all"

    with tempfile.TemporaryDirectory() as tmp:
        for index, block in enumerate(blocks):
            path = Path(tmp) / f"block{index}.js"
            path.write_text(block, encoding="utf-8")
            result = subprocess.run(
                [node, "--check", str(path)], capture_output=True, text=True
            )
            assert result.returncode == 0, (
                f"script block {index} does not parse, so every control on the page is "
                f"drawn and dead:\n{result.stderr.strip()}"
            )


@check("a served page decides on the click; a file still stages and copies out")
def _():
    """Two pages, two interactions, and the difference is having somewhere to write.

    Antonio's complaint on the 2026-09-15 recording was that pressing Approve
    changed nothing. It was true twice. The first time the page was a file and
    genuinely could not record. The second time the route existed but Approve
    still only staged, because step 6 added the route underneath the old
    clipboard interaction instead of replacing it, so recording needed a name
    and a second button most of a page away.

    The file keeps the staging path, because for a file it is the honest one.
    """
    root = _sandbox()
    client = _client(root)
    served = client.get("/").data.decode("utf-8")

    served_body = re.search(r"<body[^>]*>", served).group(0)
    assert 'data-record-url="/decide"' in served_body, (
        f"the served page does not carry the route on its body: {served_body}"
    )
    assert "function decideNow(" in served, (
        "the served page has no immediate-write path, so Approve only stages again"
    )
    assert "Approve and Decline record straight away" in served, (
        "the served page does not tell the reviewer that its buttons record"
    )

    # The standalone file: same renderer, no route, staging intact.
    import render_calendar

    taxonomy = slots.load_taxonomy()
    windows = [
        slots.read_window(slots.newest_window_path(root, lens=lens))
        for lens in taxonomy.v1_lens_order
        if slots.newest_window_path(root, lens=lens) is not None
    ]
    offline = render_calendar.render_html(
        windows, taxonomy, today=date(2026, 10, 1), agent_decided=True
    )
    # The script always mentions the attribute; what matters is whether the
    # body actually carries one, since that is what the script keys on.
    body_tag = re.search(r"<body[^>]*>", offline).group(0)
    assert "data-record-url" not in body_tag, (
        f"a page with nowhere to write advertised a route to write to: {body_tag}"
    )
    assert "Copy the decisions" in offline, (
        "the file lost the clipboard path, which is its only way to record anything"
    )
    assert "Clicking Approve or Decline records nothing" in offline, (
        "the file stopped saying that its buttons record nothing, which is still true"
    )


@check("the reviewer's name reaches the page it is passed to")
def _():
    """The seam for the portal's Google identity, per Q4.

    Recorded rather than enforced: the store has always written the name it
    was given. This only decides what the field starts as.
    """
    import render_calendar

    root = _sandbox()
    taxonomy = slots.load_taxonomy()
    windows = [
        slots.read_window(slots.newest_window_path(root, lens=lens))
        for lens in taxonomy.v1_lens_order
        if slots.newest_window_path(root, lens=lens) is not None
    ]
    page = render_calendar.render_html(
        windows, taxonomy, today=date(2026, 10, 1), agent_decided=True,
        record_url="decide", reviewer="A Signed-In Reviewer",
    )
    assert 'value="A Signed-In Reviewer"' in page, "the reviewer name never reached the field"

    blank = render_calendar.render_html(
        windows, taxonomy, today=date(2026, 10, 1), agent_decided=True, record_url="decide"
    )
    assert 'class="who" type="text" maxlength="60" value=""' in blank, (
        "a page given no reviewer invented one"
    )


@check("the lens filter cannot hide its own controls")
def _():
    """The filter was one-way, and the cause was one attribute doing two jobs.

    `data-lens` said both "this post belongs to Jordan" and "this button
    selects Jordan", so the handler's `[data-lens]` sweep hid the other
    buttons and left no visible way back to anyone else's posts. The fix is
    that a control never carries `data-lens`, and this is what holds that:
    anything the filter hides must be content, so no element matched by the
    filter may be a control.
    """
    root = _sandbox()
    client = _client(root)
    page = client.get("/").data.decode("utf-8")

    filtered = re.findall(r"<(\w+)([^>]*\bdata-lens=\"[^\"]*\"[^>]*)>", page)
    assert filtered, "nothing on the page carries data-lens"
    controls = [
        f"<{tag}{attrs}>" for tag, attrs in filtered
        if tag.lower() == "button" or "lensbtn" in attrs
    ]
    assert not controls, (
        f"a filter control carries data-lens, so choosing a lens hides it: {controls[:1]}"
    )

    buttons = re.findall(r"<button[^>]*class=\"lensbtn\"[^>]*>", page)
    assert buttons, "no filter buttons rendered"
    assert all("data-select-lens=" in b for b in buttons), (
        "a filter button does not say which lens it selects"
    )
    # Every button must select something the page can actually show.
    selectable = set(re.findall(r'data-select-lens="([a-z]+)"', page))
    present = set(re.findall(r'<div class="mini"[^>]*data-lens="([a-z]+)"', page))
    assert present <= selectable, f"{present - selectable} has posts but no way to filter to it"
    assert "all" in selectable, "there is no way back to everyone's calendar"


@check("the list below is not a drag source")
def _():
    """Explicit on 2026-09-15: "I don't want to start the drag below."

    Worth a test rather than a comment, because putting `draggable` back on
    the card is the obvious thing to reach for the next time someone wants to
    reorder from the list.
    """
    root = _sandbox()
    client = _client(root)
    page = client.get("/").data.decode("utf-8")
    articles = re.findall(r"<article\b[^>]*>", page)
    assert articles, "no cards rendered"
    offenders = [a for a in articles if "draggable" in a]
    assert not offenders, f"a card below the calendar is draggable: {offenders[:1]}"


@check("an empty post history waits on its own tab, in one line")
def _():
    """Nothing has been published and no engagement exists, which is true on
    the day this is first demoed and stays true until a portal token lands.

    Until 2026-09-21 the calendar carried three lines about this, which was
    the overexplaining Antonio asked to be removed. On 2026-09-22 he asked
    for a Post history tab that exists before its data does, so the section
    renders again, but only behind that tab, hidden by default, in one line,
    and without a word about tokens or onboarding."""
    root = _sandbox()
    page = _client(root).get("/").data.decode("utf-8")
    calendar = re.search(r'<div class="view" id="view-calendar">(.*?)<div class="view" id="view-history"', page, re.S)
    assert calendar, "the calendar view is missing"
    assert '<section class="performance">' not in calendar.group(1), (
        "an empty retrospective is back on the calendar itself"
    )
    tab = re.search(r'<div class="view" id="view-history" hidden>(.*?)</div>', page, re.S)
    assert tab, "the Post history tab is missing or not hidden by default"
    body = tab.group(1)
    assert "Nothing here yet" in body, body
    for word in ("token", "Maria", "onboard"):
        assert word not in body, f"the empty tab talks about {word!r}"
    assert 'data-view="history"' in page, "there is no control that opens the tab"


@check("a published post and its engagement reach the page")
def _():
    root = _sandbox()
    sys.path.insert(0, str(_AGENT_ROOT / "ingestion"))
    from calendar_model.state_store import record_state

    url = "https://www.linkedin.com/posts/jordan_activity-999"
    record_state(
        "ingestion_log",
        [{
            "value": {"source": "published-content", "title": "The pilot-to-production gap", "url": url},
            "entry_key": f"published-content/{url}",
            "event_at": "2026-09-04",
        }],
        root=root,
    )
    record_state(
        "ingestion_log",
        [{
            "value": {"source": "linkedin-post-engagement", "title": "Jane Doe", "post_url": url,
                      "profile_url": "https://li/in/jane"},
            "entry_key": f"linkedin-post-engagement/{url}::jane",
            "event_at": "2026-09-05",
        }],
        root=root,
    )

    page = _client(root).get("/").data.decode("utf-8")
    match = re.search(r'<section class="performance">(.*?)</section>', page, re.S)
    assert match, "no retrospective rendered"
    body = match.group(1)
    assert "The pilot-to-production gap" in body, body
    assert "1 engagement" in body, body
    assert "Jane Doe" in body, body
    assert url in body, "the post is not linked, so nobody can go and look at it"


@check("the page never claims to know how a scheduled post performed")
def _():
    """The thing deliberately not built. Nothing records the URL of a slot
    when it publishes, so a slot cannot be joined to a published post except
    by matching titles, which is a guess that breaks the moment an editor
    changes a headline. The retrospective stands on its own and the cards say
    nothing about performance."""
    root = _sandbox()
    page = _client(root).get("/").data.decode("utf-8")

    # Checked against the markup rather than against the words on the page.
    # A post's own body is the author's and may say anything, including
    # "a consultant engagement", and a test that greps prose would fail on
    # somebody else's sentence rather than on our claim.
    performance = re.search(r'<section class="performance">.*?</section>', page, re.S)
    outside = page.replace(performance.group(0), "") if performance else page
    for marker in ('class="drew"', 'class="wentout"', "data-engagements"):
        assert marker not in outside, (
            f"{marker} appears outside the retrospective, so something on the calendar "
            "is claiming to know how a post performed"
        )

    articles = re.findall(r"<article\b[^>]*>", page)
    assert articles, "no cards rendered"
    assert not [a for a in articles if "engagement" in a.lower()], articles[:1]


@check("a page that lost its content folders says so instead of showing filenames")
def _():
    """The deployment failure that renders a 200 and looks populated. The two
    content agents' folders are siblings in the monorepo and a container built
    from this folder alone does not contain them; the corpus resolves empty,
    every card falls back to its item_ref, and a founder opens a calendar of
    filenames like `blog_blake_2026-06` with the only explanation on stderr."""
    import render_calendar

    root = _sandbox()
    client = _client(root)

    real = render_calendar.resolve_corpus()
    assert real, "this test cannot tell the two states apart without a real corpus"
    page = client.get("/").data.decode("utf-8")
    assert '<section class="corpusgap">' not in page, (
        "the notice fires while the content folders are present"
    )

    gone = render_calendar.DEFAULT_ATOMIZER, render_calendar.DEFAULT_VCB
    render_calendar.DEFAULT_ATOMIZER = Path("/nonexistent/atomizer")
    render_calendar.DEFAULT_VCB = Path("/nonexistent/vcb")
    try:
        broken = client.get("/").data.decode("utf-8")
    finally:
        render_calendar.DEFAULT_ATOMIZER, render_calendar.DEFAULT_VCB = gone

    assert '<section class="corpusgap">' in broken, (
        "the page shows filenames and does not say why"
    )
    assert "CALENDAR_ATOMIZER_PATH" in broken, "the notice does not name the fix"
    # The calendar itself is not the thing that broke, and the notice must not
    # imply it is: the dates and decisions are all still real. Whitespace is
    # normalised because the notice is wrapped in the source and a test that
    # pins the wrapping breaks on a reflow rather than on a claim.
    flat = re.sub(r"\s+", " ", broken)
    assert "calendar itself is intact" in flat, "the notice reads as though the calendar broke"


@check("the state probe reports the corpus, so a deploy check catches this")
def _():
    """/healthz is what a deploy check reads. A page that renders 200 with
    every card showing a filename is the failure it exists to catch."""
    import render_calendar

    root = _sandbox()
    client = _client(root)
    body = json.loads(client.get("/healthz").data)
    assert body["corpus"] > 0, body
    assert len(body["corpus_from"]) == 2, body

    gone = render_calendar.DEFAULT_ATOMIZER, render_calendar.DEFAULT_VCB
    render_calendar.DEFAULT_ATOMIZER = Path("/nonexistent/atomizer")
    render_calendar.DEFAULT_VCB = Path("/nonexistent/vcb")
    try:
        body = json.loads(client.get("/healthz").data)
    finally:
        render_calendar.DEFAULT_ATOMIZER, render_calendar.DEFAULT_VCB = gone
    assert body["corpus"] == 0, body
    assert "/nonexistent/atomizer" in body["corpus_from"], body


@check("the content folders can be pointed somewhere else without a code change")
def _():
    """The lever a deployment needs. Railway may not put the sibling folders
    where a laptop does, and that must be a variable rather than an edit."""
    import importlib
    import os

    os.environ["CALENDAR_ATOMIZER_PATH"] = "/somewhere/else/atomizer"
    try:
        import render_calendar

        reloaded = importlib.reload(render_calendar)
        assert str(reloaded.DEFAULT_ATOMIZER) == "/somewhere/else/atomizer", (
            reloaded.DEFAULT_ATOMIZER
        )
    finally:
        del os.environ["CALENDAR_ATOMIZER_PATH"]
        importlib.reload(render_calendar)


if __name__ == "__main__":
    for line in PASSED:
        print(line)
    for line in FAILED:
        print(line, file=sys.stderr)
    print(f"\n{len(PASSED)}/{len(PASSED) + len(FAILED)} passed")
    sys.exit(1 if FAILED else 0)
