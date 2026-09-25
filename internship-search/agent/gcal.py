"""Google Calendar sync for application windows and posting deadlines.

Writes to a dedicated calendar, never to your primary one, so everything this
creates can be hidden or deleted in one action without touching your own events.

Every event carries a stable key in its private extended properties. The sync
looks an event up by that key and patches it, so running this repeatedly updates
events in place instead of piling up duplicates. Changing a key in the TOML after
its event exists is the one thing that breaks that guarantee.

Auth is a one-time OAuth consent in the browser. The resulting token is cached in
.google-token.json and refreshes itself; neither it nor credentials.json is
committed. See README for the Google Cloud setup.
"""

import datetime as dt
import tomllib
from dataclasses import dataclass, replace

from . import config

SCOPES = ["https://www.googleapis.com/auth/calendar"]
CALENDAR_NAME = "Internship Deadlines"
KEY_PROPERTY = "internship_key"

CONFIDENCE_PREFIX = {
    "confirmed": "",
    "expected": "[expected] ",
    "uncertain": "[unconfirmed] ",
}


@dataclass(frozen=True)
class Window:
    key: str
    company: str
    label: str
    start: dt.date
    end: dt.date
    confidence: str = "expected"
    action: str = ""
    url: str = ""
    # True for the events derived from the database. Their computed date can be
    # in the past, so it is only a floor: the event is created no earlier than
    # the day the sync first makes it, and after that its date never moves. A
    # cycle window from the TOML is not pinned, because there the TOML is the
    # truth and an edit to its dates must reach the calendar.
    pinned: bool = False

    @property
    def summary(self) -> str:
        return f"{CONFIDENCE_PREFIX.get(self.confidence, '')}{self.label}"

    @property
    def description(self) -> str:
        parts = [f"Company: {self.company}", f"Confidence: {self.confidence}"]
        if self.action:
            parts.append(self.action)
        if self.url:
            parts.append(self.url)
        parts.append("")
        parts.append(
            "Created by the internship watcher from sources/cycle_windows.toml. "
            "Edits here are overwritten on the next sync; change the TOML instead."
        )
        return "\n".join(parts)


def load_windows(path=None) -> list[Window]:
    path = path or config.CYCLE_WINDOWS_PATH
    with open(path, "rb") as fh:
        data = tomllib.load(fh)

    windows = [Window(**entry) for entry in data.get("window", [])]

    keys = [w.key for w in windows]
    dupes = {k for k in keys if keys.count(k) > 1}
    if dupes:
        raise ValueError(f"duplicate window keys, these would collide on sync: {dupes}")
    for w in windows:
        if w.end < w.start:
            raise ValueError(f"window '{w.key}' ends before it starts")
    return windows


def get_service():
    """Authorise and return a Calendar API client. Opens a browser on first run."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if config.GOOGLE_TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(config.GOOGLE_TOKEN_PATH), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not config.GOOGLE_CREDENTIALS_PATH.exists():
                raise FileNotFoundError(
                    f"Missing {config.GOOGLE_CREDENTIALS_PATH.name}. Download the OAuth "
                    "client secret from Google Cloud Console and save it there. "
                    "See README, section on calendar setup."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(config.GOOGLE_CREDENTIALS_PATH), SCOPES
            )
            creds = flow.run_local_server(port=0)
        config.GOOGLE_TOKEN_PATH.write_text(creds.to_json())

    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def ensure_calendar(service) -> str:
    """Find the dedicated calendar by name, creating it if this is the first run."""
    page_token = None
    while True:
        result = service.calendarList().list(pageToken=page_token).execute()
        for entry in result.get("items", []):
            if entry.get("summary") == CALENDAR_NAME:
                return entry["id"]
        page_token = result.get("nextPageToken")
        if not page_token:
            break

    created = service.calendars().insert(
        body={
            "summary": CALENDAR_NAME,
            "description": "Application windows and deadlines from the internship watcher.",
            "timeZone": config.CALENDAR_TIMEZONE,
        }
    ).execute()
    return created["id"]


# ------------------------------------------------- phase three, derived events

# Days after an application before the follow-up event lands. Two weeks, per PRD
# section 12. The digest's action block already names anything silent for three
# weeks; this is the earlier, gentler nudge and the two are deliberately
# different horizons rather than duplicates.
FOLLOWUP_DAYS = 14

# A tier 1 posting open this long with nothing logged against it gets a
# reminder. Mirrors `urgent.stale_after_days` in sources/email.toml, which is
# the same judgement about the same fact.
STALE_TIER1_DAYS = 10

# At most this many stale reminders, oldest first. Deliberately small. The first
# build of this emitted 40 all-day events on one date, which is the failure mode
# every alert in this project is designed against: a reminder he learns to
# scroll past is worse than none. 133 tier 1 roles are open and untouched, and
# the honest place to see all of them is the dashboard, which ranks them. This
# is a nudge about the oldest few, not an inventory.
STALE_TIER1_MAX = 5

# Statuses that are still waiting on somebody else. An offer needs a decision
# and a rejection needs nothing, so neither earns a follow-up.
FOLLOWUP_STATUSES = ("applied", "interviewing")


def derived_windows(conn, today: dt.date | None = None) -> list[Window]:
    """Calendar events computed from the database rather than read from TOML.

    PRD section 12 phase three. Built 2026-09-22, having been blocked since
    2026-08-19 on there being an application date to count from; `applied_at`
    exists now and seven applications carry one.

    Returned as `Window` objects so they go through exactly the same
    `_event_body`, key lookup and patch path as the cycle windows. That is the
    whole reason this is cheap: idempotency, reminders and the "edits here are
    overwritten" note already work, and a second event path would have had to
    reimplement all three and would drift from them.

    The connection is passed in rather than opened here, so this module still
    imports nothing but `config`. A calendar sync that dragged in `agent.db`
    would be a required dependency for an optional step.

    Two kinds, both keyed on the posting hash, which `CLAUDE.md` rule 11 fixes
    for the life of the row. A key that moved would orphan the event it made and
    create a duplicate beside it on the next run.
    """
    today = today or dt.date.today()
    out: list[Window] = []
    marks = ", ".join("?" for _ in FOLLOWUP_STATUSES)

    for row in conn.execute(
        f"SELECT hash, company, title, url, applied_at, applied_status "
        f"FROM postings WHERE applied_status IN ({marks}) "
        "AND applied_at IS NOT NULL AND TRIM(applied_at) <> ''",
        FOLLOWUP_STATUSES,
    ):
        try:
            applied = dt.date.fromisoformat(str(row["applied_at"]).strip()[:10])
        except ValueError:
            # A date that will not parse is not an error here. It is one row
            # without an event, not a calendar sync that fails.
            continue
        when = applied + dt.timedelta(days=FOLLOWUP_DAYS)
        out.append(Window(
            key=f"followup:{row['hash']}",
            company=row["company"] or "",
            label=f"Follow up: {row['company']} — {row['title']}",
            start=when,
            end=when,
            confidence="confirmed",
            pinned=True,
            action=(f"Applied {applied.isoformat()}, {FOLLOWUP_DAYS} days ago. "
                    "Chase it or mark the outcome in Airtable."),
            url=row["url"] or "",
        ))

    cutoff = (today - dt.timedelta(days=STALE_TIER1_DAYS)).isoformat()
    seen_companies: set[str] = set()
    for row in conn.execute(
        "SELECT hash, company, title, url, first_seen FROM postings "
        "WHERE tier = 1 AND closed_detected_at IS NULL "
        "AND COALESCE(closed_by_me, 0) = 0 AND prefilter_verdict = 'surface' "
        "AND COALESCE(applied_status, 'not_applied') = 'not_applied' "
        "AND COALESCE(label, '') = '' AND first_seen < ? "
        "ORDER BY first_seen",
        (cutoff,),
    ):
        # One per company. Without this the five slots went to five Palantir
        # requisitions of the same role in different cities, which reads as one
        # reminder repeated rather than five things to look at. The collapse in
        # `agent/grouping.py` solves the same problem for the digest and the
        # base; this is small enough to do inline.
        company_key = str(row["company"] or "").strip().lower()
        if company_key in seen_companies:
            continue
        seen_companies.add(company_key)
        if len(seen_companies) > STALE_TIER1_MAX:
            break
        # Dated when it BECAME stale, not today. A date computed from `today`
        # moves on every run, so the same event would be patched to a new day
        # four times a day and drift down the calendar forever. first_seen is
        # fixed, so this date is too. That date is usually already past, which
        # is what `pinned` is for: `push` lifts it to the day the event is
        # first created and then leaves it there.
        try:
            became = dt.date.fromisoformat(str(row["first_seen"]).strip()[:10])
            became = became + dt.timedelta(days=STALE_TIER1_DAYS)
        except ValueError:
            became = today
        out.append(Window(
            key=f"stale:{row['hash']}",
            company=row["company"] or "",
            label=f"Tier 1 untouched: {row['company']} — {row['title']}",
            start=became,
            end=became,
            confidence="confirmed",
            pinned=True,
            action=("Top band, open since "
                    f"{str(row['first_seen'])[:10]}, and you have neither "
                    "labelled nor applied to it."),
            url=row["url"] or "",
        ))
    return out


def _event_body(window: Window) -> dict:
    return {
        "summary": window.summary,
        "description": window.description,
        "start": {"date": window.start.isoformat()},
        # Google treats all-day end dates as exclusive, so add a day to make the
        # last day of the window actually appear on the calendar.
        "end": {"date": (window.end + dt.timedelta(days=1)).isoformat()},
        "transparency": "transparent",
        "extendedProperties": {"private": {KEY_PROPERTY: window.key}},
        "reminders": {
            "useDefault": False,
            "overrides": [{"method": "popup", "minutes": 24 * 60}],
        },
    }


def find_existing(service, calendar_id: str, key: str) -> dict | None:
    result = service.events().list(
        calendarId=calendar_id,
        privateExtendedProperty=f"{KEY_PROPERTY}={key}",
        showDeleted=False,
        maxResults=2,
    ).execute()
    items = result.get("items", [])
    return items[0] if items else None


def sync(dry_run: bool = False, conn=None) -> dict:
    """Push the cycle windows, plus the derived events when a connection is given.

    `conn` is optional so a caller with no database still syncs the windows.
    `tools/sync_calendar.py` always passes one.
    """
    windows = load_windows()
    if conn is not None:
        windows = windows + derived_windows(conn)

    if dry_run:
        for w in windows:
            print(f"  {w.start} to {w.end}  {w.summary}")
        return {"created": 0, "updated": 0, "total": len(windows), "dry_run": True}

    service = get_service()
    calendar_id = ensure_calendar(service)
    result = push(service, calendar_id, windows)
    result["calendar_id"] = calendar_id
    return result


def push(service, calendar_id: str, windows: list[Window],
         today: dt.date | None = None) -> dict:
    """Create or patch one event per window. Split from `sync` so it can be
    tested against a fake service without a Google account.

    A pinned window is dated no earlier than the day its event is first created,
    and never moves after that. Until 2026-09-25 every derived event was dated
    by its computed day, and all 12 live ones sat between 2026-08-17 and
    2026-09-02, in the past on the day they were made, so he never saw one. A
    date of `today` is not the fix on its own, because recomputed on every run
    it walks down the calendar; the fix is today once, at creation, and then
    whatever date the event already carries. The title, description and
    reminders are still patched, so a retitled posting stays readable.
    """
    today = today or dt.date.today()
    created = updated = 0
    for w in windows:
        body = _event_body(w)
        existing = find_existing(service, calendar_id, w.key)
        if existing:
            if w.pinned:
                for side in ("start", "end"):
                    if side in existing:
                        body[side] = existing[side]
            service.events().patch(
                calendarId=calendar_id, eventId=existing["id"], body=body
            ).execute()
            updated += 1
        else:
            if w.pinned and w.start < today:
                span = w.end - w.start
                body = _event_body(replace(w, start=today, end=today + span))
            service.events().insert(calendarId=calendar_id, body=body).execute()
            created += 1

    return {
        "created": created,
        "updated": updated,
        "total": len(windows),
        "dry_run": False,
    }
