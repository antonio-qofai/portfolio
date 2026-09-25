"""Prove a Workday board before adding it to the token map.

    python -m tools.probe_workday https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite
    python -m tools.probe_workday nvidia.wd5/NVIDIAExternalCareerSite ngc.wd1/Northrop_Grumman_External_Site

Paste the address bar of the company's Workday careers page. Anything the
fetcher accepts as a token is accepted here, and the locale segment Workday puts
in a browser URL is ignored.

This is the Workday equivalent of tools.probe_tokens and exists for a reason
that is specific to Workday: a board can resolve, answer 200, hold thousands of
postings, and still be useless to this agent, because it files its interns under
a job type the facet query cannot find. Leidos is exactly that and cost an
afternoon before this command existed. What is printed below is the thing to
look at:

    worker types    what the tenant calls its own job categories
    matched         which of them the [workday] block in companies.toml catches

A board with no match is not addable as it stands. Say so in
sources/needs-other-approach.md rather than turning on the search fallback,
which is off for the whole agent and not for one company.

Nothing is written. Adding a board is an edit to sources/companies.toml.
"""

import argparse

from agent import fetchers, sources


def probe(client, token: str, settings) -> int:
    try:
        endpoint = fetchers.workday_endpoint(token)
    except fetchers.SourceError as exc:
        print(f"{token}\n  unusable: {exc}\n")
        return 1

    print(f"{token}")
    print(f"  tenant {endpoint.tenant}, site {endpoint.site}")
    print(f"  {endpoint.jobs_url}")

    try:
        data = fetchers._workday_post(
            client,
            endpoint,
            {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""},
        )
    except fetchers.SourceError as exc:
        print(f"  FAILED: {exc}\n")
        return 1

    print(f"  {data.get('total')} postings on the board in total")

    wanted = [w.lower() for w in settings["worker_type_matches"]]
    matched_any = False
    for facet in data.get("facets") or []:
        if facet.get("facetParameter") not in settings["worker_type_facets"]:
            continue
        print(f"  {facet.get('facetParameter')} ({facet.get('descriptor')})")
        for value in facet.get("values") or []:
            name = value.get("descriptor") or ""
            hit = any(w in name.lower() for w in wanted)
            matched_any = matched_any or hit
            mark = "  <- matched" if hit else ""
            print(f"      {name[:52]:54} {value.get('count')}{mark}")

    if not matched_any:
        print(
            "\n  NOT ADDABLE. No job type here says intern, so the facet query has\n"
            "  nothing to ask for and the board would have to be read by text\n"
            "  search, which is off deliberately. Record it in\n"
            "  sources/needs-other-approach.md instead."
        )
        print()
        return 1

    print("\n  Addable. In sources/companies.toml:\n")
    print("      [[company]]")
    print('      name = "..."')
    print('      ats = "workday"')
    print(f'      token = "{endpoint.tenant}.{endpoint.host.split(".")[1]}/{endpoint.site}"')
    print()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check whether a Workday board can be polled for internships."
    )
    parser.add_argument(
        "tokens",
        nargs="+",
        help="a Workday careers URL, or a host/site token",
    )
    args = parser.parse_args()

    settings = sources.load_workday_settings()
    failures = 0
    with fetchers.make_client() as client:
        for token in args.tokens:
            failures += probe(client, token, settings)

    if failures:
        print(f"{failures} of {len(args.tokens)} board(s) cannot be polled as they are.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
