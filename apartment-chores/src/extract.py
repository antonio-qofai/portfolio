"""Turn one landlord email into proposed items. The only LLM call in the repo.

Text in, items out. No Airtable, no clock: the send date comes with the
message. The model call itself goes through a `send` function so the rest
is testable without a network, and so the SDK is only imported where it is
actually used.

The model's answer is not trusted. Structured output guarantees its shape;
everything else is checked here in plain code:

  - Each item must quote a sentence that really is in the email. An item
    whose excerpt cannot be found is dropped as invented.
  - A date must fall between the send date and FURTHEST_DATE_DAYS after it.
  - If the quoted sentence names a weekday or a day of the month, the date
    must agree with it. "Thursday the 16th" resolved to a Friday is a
    misreading, and a confirmed misreading converts the wrong week.

A cleaner visit whose date fails a check is not dropped. It becomes a
request with no date, so a person still sees that a visit was mentioned
and enters the day by hand. A request whose date fails keeps its text and
loses the date.

Any failure of the call itself raises ExtractionError. The caller treats that
as "change nothing and exit 0" (PRD-v1.1 §5, criterion L7).
"""

import calendar
import json
import re
from dataclasses import dataclass, replace
from datetime import date, timedelta

VISIT = "cleaner_visit"
REQUEST = "request"

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": [VISIT, REQUEST]},
                    "summary": {"type": "string"},
                    "detail": {"type": "string"},
                    "date": {"type": "string"},
                    "excerpt": {"type": "string"},
                },
                "required": ["kind", "summary", "detail", "date", "excerpt"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

_WEEKDAYS = {name.lower(): i for i, name in enumerate(calendar.day_name)}
_WEEKDAYS.update({"tues": 1, "wed": 2, "thur": 3, "thurs": 3})
_WEEKDAY_WORD = re.compile(r"\b(%s)\b" % "|".join(sorted(_WEEKDAYS, key=len, reverse=True)))
_MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
_MONTHS.update({name.lower(): i for i, name in enumerate(calendar.month_abbr) if name})
_MONTHS["sept"] = 9
_ORDINAL = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\b")
_MONTH_DAY = re.compile(
    r"\b(%s)\.? (\d{1,2})\b" % "|".join(sorted(_MONTHS, key=len, reverse=True))
)


class ExtractionError(RuntimeError):
    """The model could not be asked, or its answer could not be used."""


@dataclass(frozen=True)
class Item:
    kind: str
    summary: str
    detail: str
    date: object  # datetime.date or None
    excerpt: str


def build_request(message, zone, copy, settings):
    """The keyword arguments for one messages.create call.

    settings is the config.landlord module (or anything with MODEL, EFFORT,
    MAX_TOKENS). The last message is always the user's: prefill is rejected
    by current models, and output_config.format replaces it.
    """
    local = message.sent_at.astimezone(zone)
    user = copy.user_message.format(
        weekday=copy.weekday_names[local.weekday()],
        sent_date=local.date().isoformat(),
        sent_time=local.strftime("%H:%M"),
        timezone=zone.key,
        subject=message.subject,
        body=message.body,
    )
    return {
        "model": settings.MODEL,
        "max_tokens": settings.MAX_TOKENS,
        "thinking": {"type": "adaptive"},
        "output_config": {
            "effort": settings.EFFORT,
            "format": {"type": "json_schema", "schema": SCHEMA},
        },
        "system": copy.system_prompt,
        "messages": [{"role": "user", "content": user}],
    }


def extract(message, send, zone, copy, settings):
    """Return (items, notes) for one message. Raises ExtractionError.

    send takes the request kwargs and returns (stop_reason, text).
    notes are one-line explanations of anything dropped or downgraded.
    """
    stop_reason, text = send(build_request(message, zone, copy, settings))
    sent_on = message.sent_at.astimezone(zone).date()
    return parse(stop_reason, text, message.body, sent_on, settings.FURTHEST_DATE_DAYS)


def parse(stop_reason, text, body, sent_on, furthest_days):
    """Validate the model's answer against the email. Pure."""
    if stop_reason != "end_turn":
        raise ExtractionError("model stopped with %r, not end_turn" % stop_reason)
    try:
        raw_items = json.loads(text)["items"]
    except (TypeError, ValueError, KeyError) as exc:
        raise ExtractionError("model answer is not the expected JSON: %s" % exc)

    haystack = _normalise(body)
    items, notes = [], []
    for raw in raw_items:
        excerpt = " ".join(str(raw.get("excerpt", "")).split())
        summary = " ".join(str(raw.get("summary", "")).split())
        if not excerpt or _normalise(excerpt) not in haystack:
            notes.append("Dropped %r: its quoted sentence is not in the email." % summary)
            continue
        if raw.get("kind") not in (VISIT, REQUEST) or not summary:
            notes.append("Dropped an item with kind %r and summary %r." % (raw.get("kind"), summary))
            continue

        item = Item(
            kind=raw["kind"],
            summary=summary,
            detail=" ".join(str(raw.get("detail", "")).split()),
            date=None,
            excerpt=excerpt,
        )
        proposed = str(raw.get("date", "")).strip()
        problem = _date_problem(proposed, excerpt, sent_on, furthest_days) if proposed else None
        if proposed and problem is None:
            item = replace(item, date=date.fromisoformat(proposed))
        elif proposed:
            notes.append("Date %s for %r discarded: %s." % (proposed, summary, problem))

        if item.kind == VISIT and item.date is None:
            notes.append("Visit %r has no usable date; proposed as a request instead." % summary)
            item = replace(item, kind=REQUEST)
        items.append(item)
    return items, notes


def sdk_sender(settings):
    """Build a `send` function on the official SDK. Raises ExtractionError.

    Imported here, not at module level, so the tests and the Monday run
    never need the package. The SDK retries 408/409/429/5xx and connection
    errors itself; whatever is left is a reason to change nothing today.
    """
    try:
        import anthropic
    except ImportError:
        raise ExtractionError("the anthropic package is not installed")
    try:
        client = anthropic.Anthropic(timeout=settings.TIMEOUT_SECONDS, max_retries=2)
    except anthropic.AnthropicError as exc:
        raise ExtractionError("cannot build the API client: %s" % exc)

    def send(request):
        try:
            response = client.messages.create(**request)
        except anthropic.AuthenticationError:
            raise ExtractionError("the API key was rejected")
        except anthropic.RateLimitError:
            raise ExtractionError("rate limited after retries")
        except anthropic.APIStatusError as exc:
            raise ExtractionError("API returned %s: %s" % (exc.status_code, exc.message))
        except anthropic.APIConnectionError as exc:
            raise ExtractionError("could not reach the API: %s" % exc)
        text = next((b.text for b in response.content if b.type == "text"), "")
        return response.stop_reason, text

    return send


def _normalise(text):
    """Whitespace-collapsed, casefolded, with curly quotes made straight."""
    table = {0x2018: "'", 0x2019: "'", 0x201C: '"', 0x201D: '"', 0x00A0: " "}
    return " ".join(text.translate(table).split()).casefold()


def _date_problem(proposed, excerpt, sent_on, furthest_days):
    """Why this date cannot be trusted, or None."""
    try:
        day = date.fromisoformat(proposed)
    except ValueError:
        return "not a YYYY-MM-DD date"
    if day < sent_on:
        return "before the email was sent (%s)" % sent_on.isoformat()
    if day > sent_on + timedelta(days=furthest_days):
        return "more than %d days after the email was sent" % furthest_days

    lowered = excerpt.lower()
    weekdays = {_WEEKDAYS[w] for w in _WEEKDAY_WORD.findall(lowered)}
    if weekdays and day.weekday() not in weekdays:
        return "the quoted sentence names %s but this is a %s" % (
            " or ".join(calendar.day_name[w] for w in sorted(weekdays)),
            calendar.day_name[day.weekday()],
        )

    days = {int(n) for n in _ORDINAL.findall(lowered)}
    days |= {int(n) for _, n in _MONTH_DAY.findall(lowered)}
    if days and day.day not in days:
        return "the quoted sentence names day %s of the month, not %d" % (
            " or ".join(str(d) for d in sorted(days)), day.day,
        )
    return None
