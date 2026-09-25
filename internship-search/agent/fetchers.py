"""Layer 1 watchers. Deterministic HTTP polling, no LLM, no web search.

Each ATS platform gets a fetcher that turns its JSON into the same Posting record.
A fetcher either returns postings or raises; the caller distinguishes "this source
returned nothing" from "this source failed", because only the first one means a
posting closed.
"""

import collections
import hashlib
import re
import time
import typing
from dataclasses import asdict, dataclass, field

import httpx

from . import config, sources
from .sources import Company


class SourceError(RuntimeError):
    """A source could not be polled. Never treated as evidence a posting closed."""


@dataclass
class Posting:
    company: str
    title: str
    location: str
    url: str
    source: str
    ats: str
    external_id: str = ""
    description: str = ""

    # Written only by aggregator feeds, which state the term and the degree levels
    # outright. Stage 0 in Milestone 4 reads these and skips the model call when they
    # are unambiguous, which is the cheapest possible tagging. Company job boards
    # leave them empty and Stage 0 has to read the description instead.
    feed_terms: str = ""
    feed_degrees: str = ""

    # What the posting says right now. Changes when the company edits the listing,
    # which is exactly why it cannot be the thing that decides identity.
    content_hash: str = field(default="", init=False)

    # What makes two sightings the same posting. See _identity below.
    identity: str = field(default="", init=False)

    # The permanent row key, assigned once at discovery and never recomputed.
    # Airtable joins on this, so it has to outlive any edit to the posting.
    hash: str = field(default="", init=False)

    def __post_init__(self):
        self.title = _clean(self.title)
        self.location = _clean(self.location)
        self.description = (self.description or "")[: config.DESCRIPTION_CHARS]
        self.content_hash = _hash(self.company, self.title, self.location)
        self.hash = self.content_hash
        self.identity = _identity(self.source, self.external_id, self.content_hash)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["hash"] = self.hash
        d["content_hash"] = self.content_hash
        d["identity"] = self.identity
        return d


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _hash(company: str, title: str, location: str) -> str:
    key = "|".join(_clean(p).lower() for p in (company, title, location))
    return hashlib.sha256(key.encode()).hexdigest()[:32]


# Identity is stored as readable text rather than a digest for two reasons. A
# stale row can be diagnosed by looking at it, and agent/db.py can backfill the
# whole column in one SQL statement, which a hash would make impossible.
CONTENT_PREFIX = "content"


def _identity(source: str, external_id: str, content_hash: str) -> str:
    """What makes two sightings of a posting the same posting.

    Every ATS this agent polls issues a stable id per requisition and keeps it
    across edits, so that id is the identity wherever one exists. Before
    2026-08-12 identity was the content hash instead, which meant a company
    editing the location or the title on an open posting closed the old row as a
    false closure and stored the edited one as a false discovery. See CHANGELOG.

    The fallback is the content hash, for a source that supplies no id. It has
    the old behaviour and the old weakness, and that is the best available
    answer when the board tells us nothing stable.
    """
    external_id = (external_id or "").strip()
    if external_id:
        return f"{source}|{external_id}"
    return f"{CONTENT_PREFIX}|{content_hash}"


def resolve_identities(postings: list[Posting]) -> list[Posting]:
    """Demote an external_id that a single poll used for more than one posting.

    Almost every board issues one id per requisition, but a few reuse one id
    across two live listings, which would otherwise collapse two real postings
    into one row whose title flips on every run. Where that happens the id has
    told us nothing, so those postings fall back to the content hash and behave
    exactly as they did before this fix.

    This is deliberately a structural rule and not a list of boards, per
    CLAUDE.md rule 2. A board that starts or stops colliding needs no edit here.
    """
    counts = collections.Counter(p.identity for p in postings)
    for p in postings:
        if counts[p.identity] > 1 and not p.identity.startswith(f"{CONTENT_PREFIX}|"):
            p.identity = f"{CONTENT_PREFIX}|{p.content_hash}"
    return postings


def _strip_html(html: str | None) -> str:
    if not html:
        return ""
    text = re.sub(r"<[^>]+>", " ", html)
    text = (
        text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        .replace("&nbsp;", " ").replace("&#39;", "'").replace("&quot;", '"')
    )
    return _clean(text)


def _get(client: httpx.Client, url: str) -> dict | list:
    last: Exception | None = None
    for attempt in range(config.HTTP_RETRIES + 1):
        try:
            resp = client.get(url)
            if resp.status_code == 404:
                raise SourceError(f"404, token is probably wrong: {url}")
            resp.raise_for_status()
            return resp.json()
        except SourceError:
            raise
        except Exception as exc:  # network, timeout, bad JSON
            last = exc
            if attempt < config.HTTP_RETRIES:
                time.sleep(1.5 * (attempt + 1))
    raise SourceError(f"{type(last).__name__}: {last}")


def fetch_greenhouse(client: httpx.Client, company: Company) -> list[Posting]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{company.token}/jobs?content=true"
    data = _get(client, url)
    postings = []
    for job in data.get("jobs", []):
        postings.append(
            Posting(
                company=company.name,
                title=job.get("title", ""),
                location=(job.get("location") or {}).get("name", ""),
                url=job.get("absolute_url", ""),
                source=company.source_key,
                ats="greenhouse",
                external_id=str(job.get("id", "")),
                description=_strip_html(job.get("content")),
            )
        )
    return postings


def fetch_lever(client: httpx.Client, company: Company) -> list[Posting]:
    url = f"https://api.lever.co/v0/postings/{company.token}?mode=json"
    data = _get(client, url)
    postings = []
    for job in data:
        cats = job.get("categories") or {}
        postings.append(
            Posting(
                company=company.name,
                title=job.get("text", ""),
                location=cats.get("location", ""),
                url=job.get("hostedUrl", ""),
                source=company.source_key,
                ats="lever",
                external_id=str(job.get("id", "")),
                description=_strip_html(job.get("descriptionPlain") or job.get("description")),
            )
        )
    return postings


def fetch_ashby(client: httpx.Client, company: Company) -> list[Posting]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{company.token}?includeCompensation=false"
    data = _get(client, url)
    postings = []
    for job in data.get("jobs", []):
        postings.append(
            Posting(
                company=company.name,
                title=job.get("title", ""),
                location=job.get("location", ""),
                url=job.get("jobUrl", ""),
                source=company.source_key,
                ats="ashby",
                external_id=str(job.get("id", "")),
                description=_strip_html(job.get("descriptionPlain") or job.get("descriptionHtml")),
            )
        )
    return postings


# ------------------------------------------------------------------ Workday
#
# Workday is the fourth ATS and the only one that does not hand over a board in
# one GET. Three things make it different and all three are visible below.
#
# It is a POST with a JSON body, it pages 20 at a time, and it publishes no
# description in the list response. The first two are mechanical. The third is
# not: every rule the prefilter applies to a description, clearance above all,
# is blind on a Workday posting until something fetches the detail page, which
# is what agent/triage.detail_pass does for the survivors of the title gate.
#
# Nothing here names a company. The tenant and the site are the token in
# sources/companies.toml, the search terms and the page cap are the [workday]
# block in that same file, and neither belongs in this module. CLAUDE.md rule 2.


class WorkdayEndpoint(typing.NamedTuple):
    host: str
    tenant: str
    site: str

    @property
    def jobs_url(self) -> str:
        return f"https://{self.host}/wday/cxs/{self.tenant}/{self.site}/jobs"

    def detail_url(self, external_path: str) -> str:
        return f"https://{self.host}/wday/cxs/{self.tenant}/{self.site}{external_path}"

    def public_url(self, external_path: str) -> str:
        return f"https://{self.host}/{self.site}{external_path}"


def workday_endpoint(token: str) -> WorkdayEndpoint:
    """Turn a companies.toml token into the three parts of a Workday address.

    Workday needs a host, a tenant and a site where the other three boards need
    one word, and the tenant appears twice in the URL. Rather than three TOML
    keys for one board, the token is written the way the careers page reads:

        nvidia.wd5/NVIDIAExternalCareerSite
        nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite
        https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite

    All three mean the same endpoint, so the address bar can be pasted in
    unedited. The tenant is the first label of the host, which is Workday's own
    convention and holds for every tenant probed on 2026-08-19.

    A locale segment is dropped. Workday puts one in the browser URL and refuses
    it in the API path, which is the single most likely way for a pasted token
    to look right and 404.
    """
    raw = (token or "").strip()
    raw = re.sub(r"^https?://", "", raw)
    parts = [p for p in raw.split("/") if p]
    if len(parts) < 2:
        raise SourceError(
            f"workday token {token!r} needs a host and a site, "
            "for example nvidia.wd5/NVIDIAExternalCareerSite"
        )

    host = parts[0]
    if not host.endswith(".myworkdayjobs.com"):
        host = f"{host}.myworkdayjobs.com"

    # Anything that looks like en-US or en_GB is a locale Workday adds for
    # people, not for the API.
    site = next(
        (p for p in reversed(parts[1:]) if not re.fullmatch(r"[a-z]{2}[-_][A-Za-z]{2}", p)),
        "",
    )
    if not site:
        raise SourceError(f"workday token {token!r} has no site segment")

    tenant = host.split(".")[0]
    return WorkdayEndpoint(host=host, tenant=tenant, site=site)


def _workday_post(client: httpx.Client, endpoint, body: dict) -> dict:
    last: Exception | None = None
    for attempt in range(config.HTTP_RETRIES + 1):
        try:
            resp = client.post(endpoint.jobs_url, json=body)
            if resp.status_code in (404, 422):
                raise SourceError(
                    f"{resp.status_code}, tenant or site is probably wrong: "
                    f"{endpoint.jobs_url}"
                )
            resp.raise_for_status()
            return resp.json()
        except SourceError:
            raise
        except Exception as exc:
            last = exc
            if attempt < config.HTTP_RETRIES:
                time.sleep(1.5 * (attempt + 1))
    raise SourceError(f"{type(last).__name__}: {last}")


def workday_intern_facets(client: httpx.Client, endpoint, settings) -> dict[str, list[str]]:
    """The tenant's own ids for its student job types, read at runtime.

    This is what makes a Workday poll affordable. A Workday tenant publishes its
    facets in the same response as its postings, and most file interns under a
    value whose name says so: "Intern (Fixed Term)" at NVIDIA, "Intern (Global)
    (Fixed Term)" at Northrop Grumman, "Intern - Regular (Fixed Term)" and a
    whole "Interns" job category at Micron. Asking for that facet returns the
    internships and nothing else, where a text search for "intern" returns 936
    postings at NVIDIA because it matches the body text.

    The ids are opaque and differ per tenant, so they are never written down in
    companies.toml; they are discovered on every poll, which costs one request.
    What is written down is which facets to look in and which words mark a value,
    both generic, neither naming a company. CLAUDE.md rule 2.

    Returned per facet rather than merged, because Workday ANDs across facet
    parameters and ORs within one. Two parameters in a single query means job
    type Intern AND category Interns, which is narrower than either and not what
    was asked for.
    """
    data = _workday_post(
        client, endpoint, {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""}
    )
    wanted = [w.lower() for w in settings["worker_type_matches"]]
    names = settings["worker_type_facets"]
    out: dict[str, list[str]] = {}
    for facet in data.get("facets") or []:
        param = facet.get("facetParameter")
        if param not in names:
            continue
        ids = [
            v["id"]
            for v in (facet.get("values") or [])
            if v.get("id") and any(w in (v.get("descriptor") or "").lower() for w in wanted)
        ]
        if ids:
            out[param] = ids
    return out


def _workday_collect(client, endpoint, company, body, settings, found) -> bool:
    """Page one query into `found`. True if the page cap stopped it first."""
    size = settings["page_size"]
    truncated = True
    for page in range(settings["max_pages"]):
        data = _workday_post(
            client, endpoint, dict(body, limit=size, offset=page * size)
        )
        batch = data.get("jobPostings") or []
        for job in batch:
            path = job.get("externalPath") or ""
            bullets = job.get("bulletFields") or []
            posting = Posting(
                company=company.name,
                title=job.get("title", ""),
                location=job.get("locationsText", ""),
                url=endpoint.public_url(path) if path else "",
                source=company.source_key,
                ats="workday",
                # bulletFields carries the requisition id every Workday tenant
                # probed puts there. externalPath is the fallback because it
                # embeds the same id and is stable per posting; neither is the
                # title, which CLAUDE.md rule 6 forbids.
                external_id=str(bullets[0] if bullets else path),
            )
            found.setdefault(posting.identity, posting)
        if len(batch) < size:
            truncated = False
            break
        time.sleep(config.POLITE_DELAY)
    return truncated


def fetch_workday(client: httpx.Client, company: Company) -> list[Posting]:
    """One Workday board, asked for its interns rather than read whole.

    The other three fetchers read an entire board in one request and let the
    prefilter throw away 93 percent of it. That does not work here: a Workday
    tenant answers 20 postings at a time and NVIDIA has 2,000, so reading the
    board whole is 100 requests, four times a day, per company.

    So the query is narrowed at the source, and the honest thing to say about
    that is that it is not filtering. A posting outside the query is never seen
    at all, so it cannot appear in a coverage audit as a kill, and no edit to
    sources/prefilter.toml can bring it back. That is the trade Workday forces
    and it is why the narrowing lives in data, in one block, with the warning
    written next to it.

    A tenant whose facets name no student job type raises rather than returning
    what it can. Returning an empty list would be read by the watcher as every
    posting from this source having closed, and returning a text search instead
    is the Leidos case: 816 postings in 74 seconds, most of them matching only
    because "international" contains "intern". Both are worse than saying so.
    """
    return fetch_workday_with(client, company, sources.load_workday_settings())


def fetch_workday_with(client, company: Company, settings: dict) -> list[Posting]:
    """fetch_workday with its settings handed in, which is what makes it testable.

    The split exists only so tools.test_workday can hold the [workday] block
    still while it varies one key. Nothing else should call this.
    """
    endpoint = workday_endpoint(company.token)
    found: dict[str, Posting] = {}

    facets = workday_intern_facets(client, endpoint, settings)
    if facets:
        for param, ids in facets.items():
            _workday_collect(
                client, endpoint, company,
                {"appliedFacets": {param: ids}, "searchText": ""},
                settings, found,
            )
        return list(found.values())

    if not settings["search_fallback"]:
        raise SourceError(
            f"{company.source_key} publishes no student job type in "
            f"{', '.join(settings['worker_type_facets'])}, so there is no cheap "
            "way to ask it for internships. Set search_fallback in the [workday] "
            "block of sources/companies.toml to poll it by text search instead, "
            "and read what that costs before you do."
        )

    for term in settings["search_terms"]:
        _workday_collect(
            client, endpoint, company,
            {"appliedFacets": {}, "searchText": term},
            settings, found,
        )
    return list(found.values())


def workday_description(client: httpx.Client, company: Company, url: str) -> str:
    """The description for one Workday posting, fetched one posting at a time.

    Called by agent/triage.detail_pass and never by the watcher, because this is
    one request per posting and the watcher polls hundreds. By the time a
    posting reaches here it has already survived every rule that can be applied
    to a title, so the request is spent on something plausible.
    """
    endpoint = workday_endpoint(company.token)
    prefix = endpoint.public_url("")
    if not url.startswith(prefix):
        raise SourceError(f"not a {company.source_key} posting url: {url}")
    data = _get(client, endpoint.detail_url(url[len(prefix):]))
    info = data.get("jobPostingInfo") or {}
    return _strip_html(info.get("jobDescription"))


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "workday": fetch_workday,
}

# Boards that publish no description in their listing response, so a posting
# arrives with an empty one and something has to go back for it. Keyed by ATS
# so triage never asks which company this is. CLAUDE.md rule 2.
DESCRIPTION_FETCHERS = {
    "workday": workday_description,
}


def make_client() -> httpx.Client:
    return httpx.Client(
        timeout=config.HTTP_TIMEOUT,
        headers={"User-Agent": config.USER_AGENT, "Accept": "application/json"},
        follow_redirects=True,
    )


def fetch_company(client: httpx.Client, company: Company) -> list[Posting]:
    fetcher = FETCHERS.get(company.ats)
    if fetcher is None:
        raise SourceError(f"no fetcher for ATS platform '{company.ats}'")
    postings = fetcher(client, company)
    time.sleep(config.POLITE_DELAY)
    return postings
