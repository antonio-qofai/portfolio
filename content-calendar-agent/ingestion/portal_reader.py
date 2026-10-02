"""The two inputs C2 can only get from the QofAI Agent Portal.

What this is for
----------------
Post engagement and published post history are the two C2 inputs that have no
direct path. PRD Section 3 has recorded them as deferred since 2026-08-18 and
Section 4 records the consequence: v1 places a post by internal coherence
alone, with no engagement weighting and no continuity against what actually
went out and how it did.

This module is that path, built ahead of having a token so that onboarding is
a verification rather than a build.

What it reads, and why only these two
-------------------------------------
The portal exposes four reads C2 could use. This module deliberately takes
two.

- ``GET /api/v1/linkedin/rows`` for post engagement. Portal-only for a real
  reason: one paid PhantomBuster account runs the scrapes for the whole
  program, so reading engagement anywhere else means a second scrape against
  the same LinkedIn accounts.
- ``GET /api/v1/content`` for published history. Portal-only because nothing
  in the program publishes to LinkedIn, so the record of what went out exists
  only where a person recorded it.

The other two are left alone on purpose. ``GET /api/v1/conferences`` would
duplicate `conference_reader.py`, which reads the same rows live today, and
swapping it is the 2026-08-18 sourcing decision's business, not this module's.
``GET /api/v1/content/drafts`` is other agents' outbound drafts, which is not
content for Jordan's calendar.

That distinction is the whole reason this does not violate the 2026-08-18
sourcing decision. That decision deferred *repointing* the existing readers,
because onboarding then would have meant maintaining two paths to the same
data while Content Atomizer and Value Creation Briefing still publish
nowhere. These two inputs have no first path to be a second of.

What has not happened
---------------------
**No call in this module has ever reached a live portal.** C2 has no token.
Every endpoint, parameter and field name comes from reading the portal's own
source in this monorepo on 2026-09-21, which is good evidence and is not a
response. `ingestion/portal_sources.yaml` names the file each value came from.

That is a status rather than a caveat, so it is reported rather than written
in a comment: every result carries ``verified`` from the config, the daily
run records it, and `README.md` says what the first real call has to check.
Getting a token is about twenty minutes and is Maria's to issue, from the
portal's `/admin` page, not a deploy and not Railway. He has owned the portal
since 2026-08-24 (`gtm-portal/portal/HANDOFF.md`). The portal's own
`contrib/ONBOARDING.md` still names Pat, who has left QofAI, so read the
handoff rather than the onboarding doc on that one point.

The contract it keeps
---------------------
Identical to every other reader here, because the runner depends on it: an
empty list and a warning rather than an exception, a `ReadStatus` saying which
kind of nothing came back, and no reader-specific failure that could end a
four-source pass. `requests` rather than the portal's own `httpx` client,
because this package already has `requests`, reads only, and vendoring 1,100
lines of write API to make four GETs would be the larger dependency.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import read_status
import requests
import yaml

_HERE = Path(__file__).resolve().parent

DEFAULT_CONFIG_PATH = _HERE / "portal_sources.yaml"

# Both named by the portal's onboarding document, and both read at call time
# rather than at import so a test and a run can differ.
URL_ENV_VAR = "PORTAL_URL"
TOKEN_ENV_VAR = "PORTAL_TOKEN"


def _warn(message: str) -> None:
    print(f"portal_reader: {message}", file=sys.stderr)


@dataclass
class PortalConfig:
    """`portal_sources.yaml`, loaded."""

    portal_url: str = ""
    timeout_seconds: int = 30
    verified: bool = False
    sources: dict = field(default_factory=dict)

    def source(self, name: str) -> dict:
        return dict(self.sources.get(name) or {})


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> PortalConfig:
    """The config, or an empty one with a warning. Never raises."""
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        _warn(f"no config at {path}; the portal sources cannot be read.")
        return PortalConfig()
    except Exception as exc:  # noqa: BLE001 - a bad config must not end the run
        _warn(f"could not read {path} ({exc}); the portal sources cannot be read.")
        return PortalConfig()
    return PortalConfig(
        portal_url=str(raw.get("portal_url") or ""),
        timeout_seconds=int(raw.get("timeout_seconds") or 30),
        verified=bool(raw.get("verified_against_live_portal")),
        sources=dict(raw.get("sources") or {}),
    )


def token_configured() -> bool:
    """Whether there is a token to call with.

    Checked by the runner before the reader is invoked, the same way the
    Airtable token is, so an unset token is reported as a decision nobody has
    taken yet rather than as a source that broke.
    """
    return bool(os.environ.get(TOKEN_ENV_VAR, "").strip())


def portal_url(config: PortalConfig | None = None) -> str:
    """The environment wins over the config, because the onboarding document
    tells every agent to set `PORTAL_URL` and a stale default here should
    never quietly override what somebody set."""
    from_env = os.environ.get(URL_ENV_VAR, "").strip()
    if from_env:
        return from_env.rstrip("/")
    return (config or load_config()).portal_url.rstrip("/")


# --------------------------------------------------------------------------
# The HTTP layer. One place, so both reads fail the same way.
# --------------------------------------------------------------------------


def _get(path: str, params: dict, config: PortalConfig, status) -> dict | None:
    """One GET. Returns the decoded body, or None having said why not."""
    token = os.environ.get(TOKEN_ENV_VAR, "").strip()
    if not token:
        read_status.fill(
            status,
            read_status.UNCONFIGURED,
            f"{TOKEN_ENV_VAR} is not set; Maria issues the agent's token from /admin",
        )
        return None

    base = portal_url(config)
    if not base:
        read_status.fill(
            status, read_status.UNCONFIGURED, f"no portal URL, in {URL_ENV_VAR} or the config"
        )
        return None

    try:
        response = requests.get(
            f"{base}{path}",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=config.timeout_seconds,
        )
    except requests.RequestException as exc:
        read_status.fill(status, read_status.UNREACHABLE, f"{base}{path}: {exc}")
        _warn(f"could not reach {path} ({exc}); returning nothing.")
        return None

    if response.status_code in (401, 403):
        # Distinct from unset on purpose. A token that exists and is refused
        # is somebody's revoked credential; an unset one is an onboarding step
        # nobody has done. Gap detection reddens the build on both, and the
        # person reading it needs to know which.
        read_status.fill(
            status,
            read_status.REJECTED,
            f"the portal refused the token on {path} ({response.status_code})",
        )
        _warn(f"the portal refused our token on {path}; returning nothing.")
        return None

    if response.status_code >= 400:
        read_status.fill(
            status, read_status.FAILED, f"{path} answered {response.status_code}"
        )
        _warn(f"{path} answered {response.status_code}; returning nothing.")
        return None

    try:
        body = response.json()
    except ValueError as exc:
        read_status.fill(status, read_status.FAILED, f"{path} did not answer JSON: {exc}")
        _warn(f"{path} did not answer JSON ({exc}); returning nothing.")
        return None

    if not isinstance(body, dict):
        read_status.fill(status, read_status.FAILED, f"{path} answered {type(body).__name__}")
        return None

    # The portal says so itself when it served a cached copy because Airtable
    # was unreachable. Passed on rather than swallowed: the rows are real but
    # they are not current, and a freshness question asked later deserves the
    # truth.
    if body.get("stale"):
        _warn(f"{path} served a cached copy; the portal could not reach Airtable.")
    for note in body.get("notes") or []:
        _warn(f"{path}: {note}")

    return body


def _action(row: dict, spec: dict) -> tuple[str, str]:
    """What kind of engagement a row records, and which flag said so.

    Not a column lookup. PhantomBuster gives boolean-ish flags that reach us
    as the strings "true" and "", never as real booleans, so a truthiness
    check would read the string "false" as true. A row with no flag set is
    still an engagement: the scrape returning the row at all means it saw that
    person engage with that post, and a missing or renamed flag is not the
    same fact as nobody engaging. That is `engagement_source.py`'s reading in
    the LinkedIn agent, followed deliberately rather than re-decided, because
    two agents disagreeing about what a row means is worse than either
    answer.
    """
    flags = dict(spec.get("flags") or {})
    wanted = str(spec.get("flag_true_value") or "true").strip().lower()
    for name, candidates in flags.items():
        for column in candidates or []:
            if str(row.get(column, "")).strip().lower() == wanted:
                return str(name), str(column)
    return str(spec.get("flag_default") or "engagement"), ""


def _first_present(row: dict, candidates: list) -> tuple[str, str]:
    """The first candidate column present on this row, and its value.

    Returns an empty pair when none of them is there. The caller reports that
    rather than treating the row as absent, because a row that arrived and
    could not be read is a different fact from a row that never came.
    """
    for name in candidates or []:
        if name in row and row[name] not in (None, ""):
            return str(name), str(row[name])
    return "", ""


# --------------------------------------------------------------------------
# Post engagement
# --------------------------------------------------------------------------


@dataclass
class EngagementRow:
    """One person's engagement with one post.

    `scraped_at`, `pull_source` and `pull_network` are the portal's own and
    are read directly. Everything else is PhantomBuster's CSV header, which
    its own store says changes without warning, so it is resolved through the
    candidate lists in the config and the whole original row is kept in `raw`
    either way. `profile_url` is in the second group despite the portal
    normalising one onto every row, because the LinkedIn agent's mapper falls
    back to `profileUrl` and a reader that could not is a reader that drops
    rows on a shape somebody has already met.
    """

    profile_url: str
    post_url: str
    engager_name: str
    action: str
    engaged_at: str
    scraped_at: str
    pull_source: str
    pull_network: str
    # Which config candidate actually matched, per field. This is what tells
    # whoever runs the first live call whether the guesses in
    # `portal_sources.yaml` were right, without them having to diff a payload.
    resolved_from: dict
    # Fields no candidate matched. Non-empty means the config needs an edit.
    unresolved: list
    raw: dict

    @property
    def item_id(self) -> str:
        """Stable within the store, so the append-only log collapses repeats.

        A person engaging with a post is one event however many times it is
        read, so the reference is the pair rather than the row's arrival.
        """
        return f"{self.post_url or 'unknown-post'}::{self.profile_url or 'unknown-person'}"


def read_post_engagement(
    *,
    config: PortalConfig | None = None,
    since: str | None = None,
    status=None,
) -> list:
    """Who reacted to or commented on a post, newest pull first.

    Empty is not one answer. The endpoint's own note says no rows and no pull
    are different things, so when nothing comes back this asks the pull log
    before deciding which kind of nothing to report. That distinction is the
    whole reason `read_status` exists, and getting it wrong here is how a
    missing baseline goes unnoticed for a week.
    """
    config = config or load_config()
    spec = config.source("post_engagement")
    if not spec:
        read_status.fill(status, read_status.FAILED, "no post_engagement block in the config")
        return []

    params = dict(spec.get("params") or {})
    if since:
        params["since"] = since

    body = _get(str(spec.get("path") or ""), params, config, status)
    if body is None:
        return []

    rows = list(body.get("rows") or [])
    if not rows:
        pulled = _pull_happened(spec, config)
        if pulled is False:
            read_status.fill(
                status,
                read_status.MISSING,
                "no engagement rows and no pull on record; the central scrape has not run",
            )
        elif pulled is True:
            read_status.fill(
                status, read_status.EMPTY, "a pull ran and nobody engaged with anything"
            )
        else:
            read_status.fill(
                status,
                read_status.EMPTY,
                "no engagement rows, and the pull log could not be read to say why",
            )
        return []

    columns = dict(spec.get("columns") or {})
    engagements = []
    for row in rows:
        resolved: dict = {}
        unresolved: list = []
        values: dict = {}
        for wanted in ("post_url", "profile_url", "engager_name", "engaged_at"):
            key, value = _first_present(row, list(columns.get(wanted) or []))
            values[wanted] = value
            if key:
                resolved[wanted] = key
            else:
                unresolved.append(wanted)
        action, action_from = _action(row, spec)
        if action_from:
            resolved["action"] = action_from
        engagements.append(
            EngagementRow(
                profile_url=values["profile_url"],
                post_url=values["post_url"],
                engager_name=values["engager_name"],
                action=action,
                engaged_at=values["engaged_at"],
                scraped_at=str(row.get("scraped_at") or ""),
                pull_source=str(row.get("pull_source") or ""),
                pull_network=str(row.get("pull_network") or ""),
                resolved_from=resolved,
                unresolved=unresolved,
                raw=dict(row),
            )
        )

    blind = [e for e in engagements if "post_url" in e.unresolved]
    if blind:
        # Loud, because this is the failure that looks like success: rows
        # arrived, the read is `ok`, and not one of them can be attached to a
        # post. The fix is four lines of YAML, but only if somebody is told.
        _warn(
            f"{len(blind)} of {len(engagements)} engagement rows carry no column matching "
            f"{list(columns.get('post_url') or [])}. Add the real column name to "
            "portal_sources.yaml; until then these cannot be joined to a post."
        )
        read_status.fill(
            status,
            read_status.OK if len(blind) < len(engagements) else read_status.FAILED,
            f"{len(blind)} rows have no resolvable post column",
        )
    else:
        read_status.fill(status, read_status.OK, f"{len(engagements)} engagements")
    return engagements


def _pull_happened(spec: dict, config: PortalConfig) -> bool | None:
    """Whether the central scrape has run for this network at all.

    True, False, or None when the pull log itself could not be read. Three
    values rather than two because "we could not find out" must not be
    recorded as "it did not happen".
    """
    path = str(spec.get("pulls_path") or "")
    if not path:
        return None
    params = {}
    network = (spec.get("params") or {}).get("network")
    if network:
        params["network"] = network
    body = _get(path, params, config, None)
    if body is None:
        return None
    return bool(body.get("pulls"))


# --------------------------------------------------------------------------
# Published post history
# --------------------------------------------------------------------------


@dataclass
class PublishedPost:
    """One thing that actually went out."""

    title: str
    url: str
    channel: str
    themes: list
    target_audience: str
    date_published: str
    status: str
    notes: str

    @property
    def item_id(self) -> str:
        """The URL, which is the only field that identifies a post rather than
        describing it. Falls back to the title so a library row entered
        without a link is still recorded rather than dropped."""
        return self.url or f"untitled::{self.title}" if (self.url or self.title) else ""


def read_published_content(
    *,
    config: PortalConfig | None = None,
    published_since: str | None = None,
    status=None,
) -> list:
    """What has been published, newest first.

    No status filter is sent. The endpoint would happily filter on the
    library's own status column, and we do not know what values that column
    takes: asking for "Published" against a library that says "Live" returns
    nothing and looks exactly like an empty library. Rows are filtered here on
    having a publish date instead, which is a fact rather than a vocabulary.
    """
    config = config or load_config()
    spec = config.source("published_content")
    if not spec:
        read_status.fill(status, read_status.FAILED, "no published_content block in the config")
        return []

    params = dict(spec.get("params") or {})
    if published_since:
        params["published_since"] = published_since

    body = _get(str(spec.get("path") or ""), params, config, status)
    if body is None:
        return []

    rows = list(body.get("rows") or [])
    if not rows:
        read_status.fill(status, read_status.EMPTY, "the content library returned no rows")
        return []

    require_date = bool(spec.get("require_publish_date", True))
    posts = []
    undated = 0
    for row in rows:
        date_published = str(row.get("date_published") or "").strip()
        if require_date and not date_published:
            undated += 1
            continue
        themes = row.get("themes")
        if isinstance(themes, str):
            themes = [t.strip() for t in themes.split(",") if t.strip()]
        posts.append(
            PublishedPost(
                title=str(row.get("title") or ""),
                url=str(row.get("url") or ""),
                channel=str(row.get("channel") or ""),
                themes=list(themes or []),
                target_audience=str(row.get("target_audience") or ""),
                date_published=date_published,
                status=str(row.get("status") or ""),
                notes=str(row.get("notes") or ""),
            )
        )

    if undated:
        _warn(
            f"{undated} content rows have no publish date and were skipped; "
            "a row with no date is a plan rather than a history."
        )

    if not posts:
        read_status.fill(
            status,
            read_status.EMPTY,
            f"{len(rows)} content rows, none of them published",
        )
        return []

    posts.sort(key=lambda p: p.date_published, reverse=True)
    read_status.fill(status, read_status.OK, f"{len(posts)} published posts")
    return posts


if __name__ == "__main__":
    config = load_config()
    print(f"portal      {portal_url(config) or '(none)'}")
    print(f"token       {'set' if token_configured() else 'not set'}")
    print(f"verified    {config.verified}")
    for name, reader in (
        ("post engagement", read_post_engagement),
        ("published content", read_published_content),
    ):
        st = read_status.ReadStatus()
        items = reader(config=config, status=st)
        print(f"{name:18} {len(items):4} items   {st.status}  {st.detail}")
