"""Brute-force candidate tokens for a company across all three ATS platforms.

Usage:
    python -m tools.probe_tokens cohere cohereai cohere-inc

Prints every candidate that returns a real job board. Use it to turn a failing
entry in sources/companies.toml into a verified one. This only reads public job
board APIs, the same ones the watcher polls.
"""

import sys

import httpx

from agent import config

URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{t}/jobs",
    "lever": "https://api.lever.co/v0/postings/{t}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{t}",
}


def probe(client: httpx.Client, platform: str, token: str) -> int | None:
    try:
        resp = client.get(URLS[platform].format(t=token))
        if resp.status_code != 200:
            return None
        data = resp.json()
        if platform == "lever":
            return len(data) if isinstance(data, list) else None
        jobs = data.get("jobs")
        return len(jobs) if isinstance(jobs, list) else None
    except Exception:
        return None


def main() -> int:
    candidates = sys.argv[1:]
    if not candidates:
        print(__doc__)
        return 1

    client = httpx.Client(
        timeout=15, headers={"User-Agent": config.USER_AGENT}, follow_redirects=True
    )
    hits = 0
    with client:
        for token in candidates:
            for platform in URLS:
                n = probe(client, platform, token)
                if n is not None:
                    hits += 1
                    print(f"HIT  {platform:11s} {token:32s} {n} postings")
    if not hits:
        print("no hits")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
