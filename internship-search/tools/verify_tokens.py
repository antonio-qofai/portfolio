"""Prove every token in the map actually returns postings.

Run this after editing sources/companies.toml:
    python -m tools.verify_tokens

Anything reported FAIL is a token that does not work. Fix it or delete the entry.
Anything reported EMPTY is worse: the token resolves, the board answers 200, and it
covers nothing. That reports healthy forever, so it is called out separately and
still exits non-zero. Never ship a guessed token; this script is how a guess
becomes a fact.
"""

import sys

from agent import fetchers
from agent.sources import load_companies


def main() -> int:
    companies = load_companies()
    client = fetchers.make_client()

    ok, failed, empty = [], [], []

    with client:
        for company in companies:
            try:
                postings = fetchers.fetch_company(client, company)
            except fetchers.SourceError as exc:
                failed.append((company, str(exc)))
                print(f"FAIL  {company.name:34s} {company.ats}:{company.token}  {exc}")
                continue

            titles = [p.title.lower() for p in postings]
            interns = sum(
                1 for t in titles if "intern" in t or "student" in t or "new grad" in t
            )

            # 200 with an empty list is not success. It is a board that covers nothing
            # while reporting healthy, which is how a company goes silently missing.
            label = "ok   " if postings else "EMPTY"
            (ok if postings else empty).append((company, len(postings), interns))
            print(
                f"{label} {company.name:34s} {company.ats}:{company.token:28s} "
                f"{len(postings):4d} postings, {interns} intern-ish"
            )

    print()
    print(
        f"{len(ok)} verified, {len(empty)} empty, {len(failed)} failed, "
        f"{len(companies)} total"
    )

    if failed:
        print("\nRemove or correct these entries in sources/companies.toml:")
        for company, _ in failed:
            print(f"  {company.name}  ({company.ats}:{company.token})")

    if empty:
        print("\nThese tokens resolve but cover nothing. Hunt the real board with")
        print("  .venv/bin/python -m tools.probe_tokens <guess> <guess> ...")
        for company, _, _ in empty:
            print(f"  {company.name}  ({company.ats}:{company.token})")

    return 1 if (failed or empty) else 0


if __name__ == "__main__":
    raise SystemExit(main())
