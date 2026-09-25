from zoneinfo import ZoneInfo

from connectors import gmail
from connectors.gmail import is_automated, parse_thread
from dashboard.config import load_config

TZ = ZoneInfo("America/Chicago")
ME = "me@gmail.com"
INBOX = {"id": "personal", "section": "personal"}


def msg(sender, subject="Coffee Friday?", labels=("INBOX", "CATEGORY_PERSONAL"), snippet="Are you free?", **headers):
    hs = [{"name": "From", "value": sender}, {"name": "Subject", "value": subject}]
    hs += [{"name": k.replace("_", "-"), "value": v} for k, v in headers.items()]
    return {"labelIds": list(labels), "snippet": snippet, "internalDate": "1790000000000", "payload": {"headers": hs}}


def thread(*messages):
    return {"id": "18a2b3c4d5e6f7a8", "messages": list(messages)}


def test_person_waiting_on_me_needs_reply():
    item = parse_thread(thread(msg("Jane Doe <jane@example.com>")), INBOX, ME, TZ)
    assert item.urgency_hints == ["reply_needed"]
    assert item.source == "email.personal" and item.section == "personal"
    assert item.title == "Coffee Friday?" and item.summary == "Jane Doe: Are you free?"
    assert item.link == f"https://mail.google.com/mail/?authuser={ME}#all/18a2b3c4d5e6f7a8"
    assert item.timestamp.endswith("-05:00")


def test_my_reply_was_last():
    sent = msg(f"Me <{ME}>", labels=("SENT",))
    item = parse_thread(thread(msg("Jane <jane@example.com>"), sent), INBOX, ME, TZ)
    assert item.urgency_hints == []
    # A draft after their message doesn't count as a reply.
    draft = msg(f"Me <{ME}>", labels=("DRAFT",))
    assert parse_thread(thread(msg("Jane <jane@example.com>"), draft), INBOX, ME, TZ).urgency_hints == ["reply_needed"]


def test_automated_mail_is_not_reply_needed():
    assert is_automated(msg("Shop <deals@shop.com>", List_Unsubscribe="<mailto:u@shop.com>"))
    assert is_automated(msg("Bank <alerts@bank.com>"))
    assert is_automated(msg("GitHub <noreply@github.com>"))
    assert is_automated(msg("Server <root@host.com>", Auto_Submitted="auto-generated"))
    assert is_automated(msg("List <x@lists.org>", Precedence="bulk"))
    assert is_automated(msg("Store <hi@store.com>", labels=("INBOX", "CATEGORY_PROMOTIONS")))
    assert not is_automated(msg("Jane <jane@example.com>"))
    assert not is_automated(msg("Newsom <newsom@example.com>"))


def test_empty_or_draft_only_thread_skipped():
    assert parse_thread(thread(), INBOX, ME, TZ) is None
    assert parse_thread(thread(msg(ME, labels=("DRAFT",))), INBOX, ME, TZ) is None


def test_unconnected_inboxes_are_skipped(monkeypatch):
    fetched = []
    monkeypatch.setattr(gmail, "_fetch_inbox", lambda inbox, _cfg, _tz: fetched.append(inbox["id"]) or [])
    gmail.fetch(load_config())
    assert fetched == ["personal"]  # UChicago and QofAI wait on policy checks


def test_config_email_caps():
    config = load_config()
    assert config["email"]["lookback_days"] > 0 and config["email"]["max_threads"] > 0


def test_gmail_html_entities_are_decoded():
    m = msg("Shop <jane@example.com>", subject="Boots &amp; more", snippet="It&#39;s <b>")
    item = parse_thread(thread(m), INBOX, ME, TZ)
    assert item.title == "Boots & more" and item.summary == "Shop: It's <b>"


def test_bulk_tabs_become_one_count_line():
    item = gmail.bulk_item(42, INBOX, ME, 3, capped=False)
    assert item.title == "42 in Promotions, Social, Updates and Forums" and not item.urgency_hints
    assert gmail.bulk_item(500, INBOX, ME, 3, capped=True).title.startswith("500+")
    assert gmail.NOT_BULK == "-category:forums -category:promotions -category:social -category:updates"
