"""Runs Stage A and Stage 0 over the open postings. PRD.md sections 4 and 5.

This is the orchestration only; the rules are in agent/prefilter.py, the model
call is in agent/tagger.py, and the writes are in agent/db.py. Three passes,
in this order, and the order is the cost control.

    1. Hard exclusions. Every posting the prefilter has not seen. No term
       needed, no model call, most of the volume dies here.
    1.5 Descriptions, for boards that publish none in their listing. One HTTP
       request per surviving posting, then the hard rules run again now that
       there is text for them to read. Workday only.
    2. Stage 0 tagging. Only survivors of pass 1, and only the ones whose
       aggregator feed did not already state the term.
    3. Timing rules. Only survivors of pass 1 that pass 2 has answered for.

Every pass is resumable. A posting the model-call budget could not reach stays
pending and is picked up on the next run, so a capped run loses nothing.
"""

import time

from . import config, db, fetchers, prefilter, sources, tagger

# Tags are committed in batches this size, so an interrupted run keeps the model
# calls it already paid for.
COMMIT_EVERY = 25


def hard_pass(conn, verbose=False) -> dict:
    """Pass one. Kill everything that needs no term to rule out."""
    rules = prefilter.load_rules()
    postings = db.untriaged(conn)
    killed = 0

    for posting in postings:
        verdict = prefilter.evaluate_hard(posting, rules)
        db.record_verdict(conn, posting, verdict)
        if verdict.killed:
            killed += 1
            if verbose:
                print(f"  kill  {posting['company']}: {posting['title']}"
                      f"  ({verdict.reason})")
    conn.commit()

    return {"examined": len(postings), "killed": killed,
            "pending": len(postings) - killed}


def detail_pass(conn, max_fetches=None, verbose=False) -> dict:
    """Pass one and a half. Go back for the descriptions a board did not publish.

    Workday is the only source with this shape: its board response carries no
    text, so a posting arrives with a title and nothing else, and every rule
    that reads a description is blind on it. For a defense prime that is not a
    detail, it is the clearance rule, which is the single most common reason one
    of their postings is not for the owner.

    Placed between the hard rules and Stage 0 for two reasons, and the order is
    the cost control both times. It runs after the hard pass so a request is
    only ever spent on a posting that survived its title, which is most of the
    volume gone for free. It runs before Stage 0 so the model call that reads
    the description is not made against an empty one.

    Every enriched posting is then put back through the hard rules, because they
    have only ever seen it with the description missing. That is what makes the
    clearance and degree kills work here at all, and it can only take a posting
    from pending to killed, never the other way, since the rules that already
    ran are the same rules.

    Bounded and resumable, like every other pass. A posting the budget did not
    reach keeps its empty description and is first in the queue next run.
    """
    settings = sources.load_workday_settings()
    budget = settings["max_details_per_run"] if max_fetches is None else max_fetches
    platforms = sorted(fetchers.DESCRIPTION_FETCHERS)
    stats = {"fetched": 0, "killed": 0, "remaining": 0, "errors": []}

    postings = db.needs_description(conn, platforms, budget)
    if not postings:
        return stats

    by_source = {c.source_key: c for c in sources.load_companies()}
    rules = prefilter.load_rules()
    client = fetchers.make_client()
    try:
        for posting in postings:
            company = by_source.get(posting["source"])
            fetch = fetchers.DESCRIPTION_FETCHERS.get(posting["ats_platform"])
            if company is None or fetch is None:
                # The company left the token map since this posting was stored.
                # Nothing to fetch against, and nothing broken either.
                continue
            try:
                text = fetch(client, company, posting["url"])
            except Exception as exc:
                stats["errors"].append(
                    (posting["company"], posting["title"], f"{type(exc).__name__}: {exc}")
                )
                continue

            db.set_description(conn, posting["id"], text)
            stats["fetched"] += 1
            posting["description"] = text

            verdict = prefilter.evaluate_hard(posting, rules)
            db.record_verdict(conn, posting, verdict)
            if verdict.killed:
                stats["killed"] += 1
                if verbose:
                    print(f"  kill  {posting['company']}: {posting['title']}"
                          f"  ({verdict.reason})")
            if stats["fetched"] % COMMIT_EVERY == 0:
                conn.commit()
            time.sleep(config.POLITE_DELAY)
    finally:
        client.close()
        conn.commit()

    stats["remaining"] = len(db.needs_description(conn, platforms, budget + 1))
    return stats


def tag_pass(conn, max_calls=None, verbose=False) -> dict:
    """Pass two. Stage 0 intake tagging on what survived pass one."""
    untagged = [p for p in db.pending(conn) if not p["tagged_at"]]
    stats = {"tagged": 0, "from_feed": 0, "from_model": 0,
             "input_tokens": 0, "output_tokens": 0, "cost": 0.0,
             "remaining": 0, "skipped": False, "errors": []}

    if not untagged:
        return stats

    needs_call = [p for p in untagged if tagger.from_feed(p) is None]
    budget = config.STAGE0_MAX_CALLS if max_calls is None else max_calls

    if needs_call and not config.stage0_configured():
        # No key is not an error. The feed-tagged postings still go through and
        # the rest wait, so the watcher keeps working without the model.
        if verbose:
            print(f"  Stage 0: ANTHROPIC_API_KEY not set, "
                  f"{len(needs_call)} posting(s) need a model call and will wait")
        stats["skipped"] = True
        budget = 0

    errors: list = []
    for posting, tag in tagger.tag_batch(untagged, max_calls=budget,
                                         verbose=verbose, errors=errors):
        db.record_tag(conn, posting["id"], tag)
        stats["tagged"] += 1
        stats["from_feed" if tag["stage0_source"] == "feed" else "from_model"] += 1
        stats["input_tokens"] += tag["input_tokens"]
        stats["output_tokens"] += tag["output_tokens"]
        # Commit as we go. The calls are sequential and roughly a second each,
        # so clearing a large backlog runs for minutes. Without this, a Ctrl-C
        # or a dropped connection throws away every call already paid for.
        if stats["tagged"] % COMMIT_EVERY == 0:
            conn.commit()
    conn.commit()

    stats["cost"] = tagger.estimate_cost(stats["input_tokens"], stats["output_tokens"])
    stats["errors"] = errors
    # A failed posting stays untagged, so it is still owed a call next run.
    stats["remaining"] = max(0, len(needs_call) - stats["from_model"])
    return stats


def timing_pass(conn, verbose=False) -> dict:
    """Pass three. The timing rules, on everything Stage 0 has answered for."""
    rules = prefilter.load_rules()
    postings = db.pending(conn, tagged_only=True)
    killed = 0

    for posting in postings:
        verdict = prefilter.evaluate_timing(posting, rules)
        db.record_verdict(conn, posting, verdict)
        if verdict.killed:
            killed += 1
            if verbose:
                print(f"  kill  {posting['company']}: {posting['title']}"
                      f"  ({verdict.reason})")
    conn.commit()

    return {"examined": len(postings), "killed": killed,
            "surfaced": len(postings) - killed}


def run(conn, max_calls=None, verbose=False) -> dict:
    """All three passes. Returns one stats block for the digest and the run log."""
    hard = hard_pass(conn, verbose)
    details = detail_pass(conn, verbose=verbose)
    tagged = tag_pass(conn, max_calls, verbose)
    timing = timing_pass(conn, verbose)

    counts = db.triage_counts(conn)
    decided = counts.get("killed", 0) + counts.get("surface", 0)

    return {
        "hard": hard,
        "details": details,
        "tag": tagged,
        "timing": timing,
        "counts": counts,
        # The Milestone 4 target in PRD section 10 is 80 percent. Measured over
        # postings actually decided, so the pending backlog does not flatter it.
        "kill_rate": counts.get("killed", 0) / decided if decided else 0.0,
    }


def summary_lines(stats: dict) -> list[str]:
    """Human-readable block, shared by the digest, the run output, and the report."""
    counts, tag = stats["counts"], stats["tag"]
    lines = [
        f"Prefilter: {counts.get('killed', 0)} killed, "
        f"{counts.get('surface', 0)} surfaced, "
        f"{counts.get('pending', 0)} awaiting intake tagging, "
        f"out of {counts.get('total', 0)} open.",
        f"Kill rate {stats['kill_rate']:.0%} of decided postings (target 80%).",
    ]
    details = stats.get("details") or {}
    if details.get("fetched"):
        lines.append(
            f"Fetched {details['fetched']} missing description(s), "
            f"{details['killed']} killed once there was text to read."
        )
    if details.get("remaining"):
        lines.append(
            f"{details['remaining']} posting(s) still have no description and "
            "will be fetched on the next run."
        )
    if details.get("errors"):
        lines.append(f"{len(details['errors'])} description fetch(es) failed:")
        for company, title, err in details["errors"][:5]:
            lines.append(f"  - {company}: {title}: {err}")
    if tag["tagged"]:
        lines.append(
            f"Stage 0 tagged {tag['tagged']}: {tag['from_feed']} free from feed "
            f"metadata, {tag['from_model']} by model call, "
            f"estimated ${tag['cost']:.4f}."
        )
    if tag["remaining"]:
        lines.append(
            f"{tag['remaining']} posting(s) still need a model call and will be "
            f"tagged on the next run."
        )
    if tag.get("errors"):
        # Named rather than counted. A tagging call that keeps failing on the
        # same posting would otherwise look identical to a budget cap.
        lines.append(f"{len(tag['errors'])} tagging call(s) failed:")
        for company, title, err in tag["errors"][:5]:
            lines.append(f"  - {company}: {title}: {err}")
    return lines
