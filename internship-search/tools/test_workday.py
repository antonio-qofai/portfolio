"""The Workday fetcher, against a fake board. 2026-08-19.

    .venv/bin/python -m tools.test_workday

Makes no network request, spends nothing, and never touches state.db. Run it
after editing agent/fetchers.fetch_workday or the [workday] block in
sources/companies.toml.

Workday is the fourth ATS and the only one where the fetcher decides what it
asks for rather than reading a board whole. That makes it the only fetcher that
can lose a posting without anything failing, so the cases below are mostly about
what it refuses to do:

    it never guesses a facet id, it reads the tenant's own
    it never ANDs two facets, because that returns less than either
    it never returns an empty list when it cannot find the interns, because the
      watcher would read that as every posting from the source having closed

The last one is the dangerous case and is why Leidos raises instead of being
polled by text search.
"""

import sys

from agent import fetchers, sources
from agent.sources import Company

PASS, FAIL = "ok  ", "FAIL"
results: list[tuple[bool, str, str]] = []


def check(name: str, got, want, note: str = "") -> None:
    results.append((got == want, name, note or f"got {got!r}, wanted {want!r}"))


SETTINGS = {
    "page_size": 2,
    "max_pages": 5,
    "search_terms": ["intern"],
    "worker_type_facets": ["workerSubType", "jobFamilyGroup"],
    "worker_type_matches": ["intern", "co-op", "student"],
    "search_fallback": False,
    "max_details_per_run": 10,
}

COMPANY = Company(name="Acme", ats="workday", token="acme.wd1/External")


class FakeClient:
    """A Workday tenant that answers exactly like the real ones do.

    `jobs` is keyed by the facet ids a query applies, so a test can prove that
    two facets were asked for separately rather than together.
    """

    def __init__(self, facets, jobs, page_size=2):
        self.facets, self.jobs, self.page_size = facets, jobs, page_size
        self.calls: list[dict] = []

    def post(self, url, json):
        self.calls.append(json)
        applied = json.get("appliedFacets") or {}
        key = tuple(sorted((k, tuple(v)) for k, v in applied.items()))
        if json.get("searchText"):
            key = ("search", json["searchText"])
        matches = self.jobs.get(key, [])
        offset, limit = json.get("offset", 0), json.get("limit", self.page_size)
        return FakeResponse(
            {
                "total": len(matches),
                "jobPostings": matches[offset:offset + limit],
                "facets": self.facets,
            }
        )


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def job(n: int) -> dict:
    return {
        "title": f"Intern {n}",
        "externalPath": f"/job/Chicago/Intern-{n}_R{n}",
        "locationsText": "Chicago, IL",
        "bulletFields": [f"R{n}"],
    }


FACETS = [
    {
        "facetParameter": "workerSubType",
        "values": [
            {"id": "wst-regular", "descriptor": "Regular Employee", "count": 900},
            {"id": "wst-intern", "descriptor": "Intern (Fixed Term)", "count": 3},
        ],
    },
    {
        "facetParameter": "jobFamilyGroup",
        "values": [
            {"id": "jfg-eng", "descriptor": "Engineering", "count": 800},
            {"id": "jfg-interns", "descriptor": "Interns", "count": 2},
        ],
    },
    {
        "facetParameter": "timeType",
        "values": [{"id": "tt-full", "descriptor": "Full time", "count": 900}],
    },
]


def test_the_token_is_the_address_bar():
    """Three ways of writing the same board, because that is what gets pasted."""
    want = "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs"
    for token in [
        "nvidia.wd5/NVIDIAExternalCareerSite",
        "nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite",
        "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite",
        "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/",
    ]:
        check(f"token {token[:34]}", fetchers.workday_endpoint(token).jobs_url, want)

    try:
        fetchers.workday_endpoint("nvidia.wd5")
        check("a token with no site is refused", False, True)
    except fetchers.SourceError:
        check("a token with no site is refused", True, True)


def test_facet_ids_are_read_never_guessed():
    client = FakeClient(FACETS, {})
    found = fetchers.workday_intern_facets(
        client, fetchers.workday_endpoint(COMPANY.token), SETTINGS
    )
    check(
        "the tenant's own ids for its student job types",
        found,
        {"workerSubType": ["wst-intern"], "jobFamilyGroup": ["jfg-interns"]},
    )
    check("and nothing from a facet nobody asked about", "timeType" in found, False)


def test_two_facets_are_asked_separately():
    """Workday ANDs across facet parameters, so one query would return neither.

    Job type Intern AND category Interns is a narrower question than either, and
    at Micron it is the difference between 71 postings and 67.
    """
    jobs = {
        (("workerSubType", ("wst-intern",)),): [job(1), job(2), job(3)],
        (("jobFamilyGroup", ("jfg-interns",)),): [job(3), job(4)],
    }
    client = FakeClient(FACETS, jobs)
    postings = fetchers.fetch_workday_with(client, COMPANY, SETTINGS)

    check("every posting from both facets", sorted(p.external_id for p in postings),
          ["R1", "R2", "R3", "R4"])
    check("the one in both is stored once", len(postings), 4)
    applied = [c["appliedFacets"] for c in client.calls if c.get("appliedFacets")]
    check("no query ever carried both facets", max(len(a) for a in applied), 1)


def test_paging_stops_at_a_short_page():
    jobs = {(("workerSubType", ("wst-intern",)),): [job(i) for i in range(1, 6)]}
    client = FakeClient(FACETS, {**jobs, (("jobFamilyGroup", ("jfg-interns",)),): []})
    postings = fetchers.fetch_workday_with(client, COMPANY, SETTINGS)
    check("all five across three pages of two", len(postings), 5)
    offsets = [c.get("offset") for c in client.calls if c.get("limit") == 2]
    check("and it asked for exactly the pages it needed", offsets, [0, 2, 4, 0])


def test_identity_is_the_requisition_id():
    """CLAUDE.md rule 6. A Workday title edit must not close the posting."""
    jobs = {(("workerSubType", ("wst-intern",)),): [job(1)]}
    client = FakeClient(FACETS, {**jobs, (("jobFamilyGroup", ("jfg-interns",)),): []})
    first = fetchers.fetch_workday_with(client, COMPANY, SETTINGS)[0]

    edited = dict(job(1), title="Intern 1, Summer 2027", locationsText="Austin, TX")
    client2 = FakeClient(
        FACETS,
        {
            (("workerSubType", ("wst-intern",)),): [edited],
            (("jobFamilyGroup", ("jfg-interns",)),): [],
        },
    )
    second = fetchers.fetch_workday_with(client2, COMPANY, SETTINGS)[0]

    check("an edit keeps the identity", first.identity, second.identity)
    check("and changes what the posting says", first.content_hash != second.content_hash, True)
    check("the id is the requisition, not the path", first.identity.endswith("|R1"), True)


def test_a_board_with_no_interns_raises():
    """The Leidos case, and the reason it is a refusal rather than a result.

    Returning [] would be read by db.age_missing as every posting from this
    source having disappeared, so one facet rename at one employer would close
    its entire board. Raising is the documented way to say a source could not be
    polled, and SourceError is never evidence a posting closed.
    """
    plain = [
        {
            "facetParameter": "workerSubType",
            "values": [{"id": "wst-regular", "descriptor": "Regular", "count": 2000}],
        }
    ]
    client = FakeClient(plain, {})
    try:
        fetchers.fetch_workday_with(client, COMPANY, SETTINGS)
        check("a board with no student job type raises", False, True)
    except fetchers.SourceError as exc:
        check("a board with no student job type raises", True, True)
        check(
            "and says how to override it",
            "search_fallback" in str(exc),
            True,
        )


def test_the_search_fallback_is_off_and_still_works():
    plain = [
        {
            "facetParameter": "workerSubType",
            "values": [{"id": "wst-regular", "descriptor": "Regular", "count": 2000}],
        }
    ]
    client = FakeClient(plain, {("search", "intern"): [job(9)]})
    postings = fetchers.fetch_workday_with(
        client, COMPANY, dict(SETTINGS, search_fallback=True)
    )
    check("with the fallback on, the board is searched", len(postings), 1)


def test_the_shipped_settings_would_find_a_real_intern_facet():
    """The words in companies.toml against the descriptors the real boards use."""
    settings = sources.load_workday_settings()
    wanted = [w.lower() for w in settings["worker_type_matches"]]
    for descriptor in [
        "Intern (Fixed Term)",            # NVIDIA
        "Intern (Global) (Fixed Term)",   # Northrop Grumman
        "Intern - Regular (Fixed Term)",  # Micron
        "Interns",                        # Micron, job category
    ]:
        check(
            f"matches {descriptor!r}",
            any(w in descriptor.lower() for w in wanted),
            True,
        )
    check(
        "and does not match a plain employee",
        any(w in "regular employee" for w in wanted),
        False,
    )


def main() -> int:
    for fn in [
        test_the_token_is_the_address_bar,
        test_facet_ids_are_read_never_guessed,
        test_two_facets_are_asked_separately,
        test_paging_stops_at_a_short_page,
        test_identity_is_the_requisition_id,
        test_a_board_with_no_interns_raises,
        test_the_search_fallback_is_off_and_still_works,
        test_the_shipped_settings_would_find_a_real_intern_facet,
    ]:
        try:
            fn()
        except Exception as exc:
            # A case that raises is a failure, not the end of the run. Without
            # this one broken assumption hides every case after it, which is
            # exactly what a regression looks like from the outside.
            results.append((False, fn.__name__, f"raised {type(exc).__name__}: {exc}"))

    failed = 0
    for ok, name, note in results:
        if ok:
            print(f"{PASS} {name}")
        else:
            failed += 1
            print(f"{FAIL} {name}: {note}")

    print(f"\n{len(results) - failed} of {len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
