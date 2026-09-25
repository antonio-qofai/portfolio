"""Stage A, the deterministic prefilter. PRD.md section 5, no LLM anywhere.

Every posting the watchers store passes through here and comes out with one of
three verdicts.

    killed    excluded by a hard rule. Never scored, never emailed.
    surface   goes on to the ranker, carrying any flags it picked up.
    pending   cannot be decided yet because the timing rules need the term, and
              Stage 0 has not tagged this posting. It waits for the next run.

The split into two passes is the whole design. `evaluate_hard` applies the
exclusions that need no term at all: is this even a student role, is it a quant
trading title, is it confidently outside the United States, does it demand a
clearance he does not have, is it graduate-only. `evaluate_timing` applies the
term and hours rules and runs only on what survived. Postings die in the first
pass without ever costing a model call, which is what keeps Stage 0 inside the
ten dollar budget in PRD section 2.

No company, city, job title, or term name appears in this file. All of it lives
in sources/prefilter.toml. If the filter is wrong, that file is where to fix it.

The asymmetry stated in PRD section 5 runs through every decision here. A false
kill is invisible, because the owner never sees what was dropped. A false surface
costs him three seconds. So anything this module cannot resolve confidently is
surfaced with a flag, never dropped.
"""

import re
import tomllib
from dataclasses import dataclass, field

from . import config

KILLED = "killed"
SURFACE = "surface"
PENDING = "pending"

# Every flag this module can write. Named here so re-applying edited rules can
# clear its own flags without touching one a later milestone owns, such as the
# ranker's `reach`.
OWNED_FLAGS = (
    "unclear_quant", "unclear_location", "unclear_commitment", "unclear_term",
    "leave_required", "outside_cycle", "local_full_time",
)


@dataclass
class Verdict:
    """What the prefilter decided, and why, in words the owner can read."""

    outcome: str
    reason: str = ""
    flags: list[str] = field(default_factory=list)

    @property
    def killed(self) -> bool:
        return self.outcome == KILLED


_RULES: dict | None = None


def load_rules(path=None) -> dict:
    """Read sources/prefilter.toml. Cached, since it is read once per posting."""
    global _RULES
    if _RULES is None or path is not None:
        with open(path or config.PREFILTER_PATH, "rb") as fh:
            rules = tomllib.load(fh)
        if path is not None:
            return rules
        _RULES = rules
    return _RULES


# ------------------------------------------------------------------- matching

def _word_start(text: str, needles) -> str | None:
    """First needle that starts a word in text, or None.

    A word-start prefix match, not a substring and not a whole word. "intern"
    must still catch "Internship", so the end is left open. The start is
    anchored because a plain substring match once let "resident" catch "Vice
    President" and put 19 sales executives in the backlog report.
    """
    lowered = (text or "").lower()
    for needle in needles:
        if re.search(r"\b" + re.escape(needle.lower()), lowered):
            return needle
    return None


def _contains(text: str, needles) -> str | None:
    """First needle appearing anywhere in text, or None. For phrase matching."""
    lowered = (text or "").lower()
    for needle in needles:
        if needle.lower() in lowered:
            return needle
    return None


# ------------------------------------------------------------------ locations

def _split_places(location: str, separators) -> list[str]:
    parts = [location or ""]
    for sep in separators:
        parts = [piece for part in parts for piece in part.split(sep)]
    return [p.strip() for p in parts if p.strip()]


def resolve_place(place: str, rules: dict) -> str:
    """Classify one place name as 'us', 'non_us', or 'unresolved'.

    The order is the whole point. Country names and unambiguous American cities
    are strong and settle it immediately. Two-letter state codes and foreign
    city names are weak, and between those two the state code wins:

        "Toronto, ON, Canada"  strong country name, killed at once
        "Vancouver, WA"        state code beats the foreign city, surfaced,
                               because Vancouver, Washington is a real place
        "Bengaluru, IN"        IN reads as Indiana, so this surfaces too

    That last case is a wrong answer in the safe direction, and it stays that
    way on purpose. Ranking Bengaluru low costs the owner three seconds. Killing
    Vancouver, Washington would cost him a job he never learned existed.

    Anything left over is unresolved, which also means surfaced, with a flag.
    """
    loc = rules["location"]
    lowered = place.lower()

    if (
        _contains(lowered, loc["us_strong"])
        or _word_start(lowered, loc["us_states"])
        or _word_start(lowered, loc["us_cities"])
    ):
        return "us"
    if _word_start(lowered, loc["non_us_countries"]):
        return "non_us"
    codes = set(loc["us_state_codes"])
    if any(token in codes for token in re.findall(r"\b[A-Z]{2}\b", place)):
        return "us"
    if _word_start(lowered, loc["non_us_cities"]):
        return "non_us"
    return "unresolved"


def check_location(location: str, rules: dict) -> Verdict:
    """PRD section 5. Kill only what resolves confidently AND exclusively abroad."""
    loc = rules["location"]
    places = _split_places(location, loc["separators"])
    if not places:
        return Verdict(SURFACE, flags=["unclear_location"])

    # PRD section 5 exempts remote and umbrella postings from the location
    # filter outright, "of any description". A remote role is not somewhere.
    if _word_start(location, loc["umbrella"]):
        resolved = [resolve_place(p, rules) for p in places]
        if "us" in resolved:
            return Verdict(SURFACE)
        return Verdict(SURFACE, flags=["unclear_location"])

    resolved = [resolve_place(p, rules) for p in places]

    if "us" in resolved:
        return Verdict(SURFACE)
    if all(r == "non_us" for r in resolved):
        return Verdict(KILLED, f"location resolves entirely outside the US: {location}")
    return Verdict(SURFACE, flags=["unclear_location"])


# ----------------------------------------------------------------- hard checks

def is_student_role(title: str, from_feed: bool, rules: dict) -> bool:
    """Whether this posting is a student or early-career role at all.

    Company boards carry every open role a company has, mostly senior full-time
    positions. Without this gate the agent would pay a model to read 17,919
    postings instead of roughly 2,100.

    Feed provenance counts on its own. The aggregator feeds track internships
    and nothing else, so a feed listing is an internship whatever its title says.
    """
    gate = rules["student_role"]
    if from_feed and gate.get("trust_feed_provenance", True):
        return True

    # A word-start match is not enough on its own. "intern" also starts
    # "International" and "Internal". So take the whole word the keyword landed
    # in and reject it if title_block lists it.
    blocked = {w.lower() for w in gate.get("title_block", [])}
    lowered = (title or "").lower()
    for needle in gate["title_include"]:
        pattern = r"\b" + re.escape(needle.lower()) + r"\w*"
        if any(m.group(0) not in blocked for m in re.finditer(pattern, lowered)):
            return True
    return False


def check_excluded_roles(title: str, rules: dict) -> Verdict:
    """Whole categories of work the owner does not want, killed on the title.

    Separate from the student gate above on purpose. That gate asks whether a
    posting is a student role at all, and a campus ambassador or a recruiting
    internship passes it honestly. This asks whether it is a student role he
    would ever take, which is a different question with a different answer.

    Every category lives in sources/prefilter.toml. There is no role name in
    this function and there must never be one.
    """
    excluded = rules.get("excluded_roles") or {}
    hit = _word_start(title, excluded.get("kill_title", []))
    if hit:
        return Verdict(KILLED, f"excluded role category: matched {hit!r}")
    return Verdict(SURFACE, "")


def check_generalist_software(title: str, rules: dict) -> Verdict:
    """Kill pure software engineering roles, keep the ones in a domain he wants.

    The only rule here with a keep list, and the asymmetry is the point. A title
    names the craft ("Software Engineer Intern") and says nothing about the
    competition, which is what the domain decides. The owner is an economics and
    physics major: against CS majors for a generalist backend internship he
    loses on paper, and against the same title attached to avionics, chassis
    control or forward deployed work he does not.

    A keep word anywhere in the title wins. That direction is deliberate and
    matches the rest of the prefilter: a false surface costs three seconds of
    reading, a false kill costs the posting with nothing to notice it by.

    Both lists are sources/prefilter.toml. No role name belongs in this
    function and no employer name belongs in either list.
    """
    section = rules.get("generalist_software") or {}
    kill = section.get("kill_title", [])
    if not kill:
        return Verdict(SURFACE, "")
    hit = _contains(title, kill)
    if not hit:
        return Verdict(SURFACE, "")
    rescued = _contains(title, section.get("keep_title", []))
    if rescued:
        return Verdict(SURFACE, "")
    return Verdict(KILLED, f"generalist software role: matched {hit!r}")


def check_employer(company: str, rules: dict) -> Verdict:
    """Kinds of employer that can never hire him, killed on the company name.

    The one rule in the file that looks at the employer rather than the role,
    and it earns the exception. A university's own campus jobs are open only to
    students enrolled there, so an undergraduate research assistantship at Penn
    State is unavailable to the owner for a reason no title rule can see: the
    title is identical to one he could take.

    The allow list is checked first and vetoes the kill, so his own university
    survives the generic test rather than the test having to be weakened.

    Every name lives in sources/prefilter.toml. This function names no employer
    and must never name one; the lists are kinds of employer, not companies.
    """
    emp = rules.get("employer") or {}
    if _word_start(company, emp.get("allow_company", [])):
        return Verdict(SURFACE)
    hit = _word_start(company, emp.get("kill_company", []))
    if hit:
        return Verdict(KILLED, f"employer hires only its own students: matched {hit!r}")
    return Verdict(SURFACE)


def check_quant(title: str, rules: dict) -> Verdict:
    """Matched on the role, never the employer. See PRD section 5.

    An AI or software role at a quant firm is in scope and scored normally. Only
    the quant trading and quant research titles die, at any firm. Ambiguous
    titles such as quantitative developer are flagged, not killed.
    """
    quant = rules["quant"]
    hit = _word_start(title, quant["kill_title"])
    if hit:
        return Verdict(KILLED, f"quant role title: matched '{hit}'")
    hit = _word_start(title, quant["flag_title"])
    if hit:
        return Verdict(SURFACE, flags=["unclear_quant"])
    return Verdict(SURFACE)


def check_clearance(description: str, rules: dict) -> Verdict:
    """Existing clearance required is out. Sponsored clearance is in.

    The allow phrases are checked first and veto the kill, because "ability to
    obtain an active security clearance" contains a kill phrase inside what is
    actually a sponsorship offer.
    """
    clr = rules["clearance"]
    if _contains(description, clr["allow_phrases"]):
        return Verdict(SURFACE)
    hit = _contains(description, clr["kill_phrases"])
    if hit:
        return Verdict(KILLED, f"requires an existing clearance: '{hit}'")
    return Verdict(SURFACE)


def check_degree(title: str, description: str, feed_degrees: str, rules: dict) -> Verdict:
    """Graduate-only and PhD-only postings are out. PRD section 5.

    Aggregator feeds publish the degree levels outright, which is the cleanest
    signal available: a listing admitting no undergraduate degree is graduate
    only. Company boards publish no such field, so title and description
    phrases carry it, kept narrow so a posting that merely prefers a graduate
    degree survives.
    """
    deg = rules["degree"]

    if feed_degrees:
        levels = [d.strip().lower() for d in feed_degrees.split(";") if d.strip()]
        if levels and not any(
            _contains(level, deg["undergraduate_feed_degrees"]) for level in levels
        ):
            return Verdict(KILLED, f"graduate-only degree requirement: {feed_degrees}")

    # Checked before the kill list and vetoes it, on the same reasoning as
    # check_clearance above. A title naming several degree levels is open to the
    # lowest one it names, so "Research Intern (BS/MS/PhD)" is an undergraduate
    # posting despite containing a kill pattern.
    if not _contains(title, deg.get("allow_title", [])):
        hit = _word_start(title, deg["kill_title"])
        if hit:
            return Verdict(KILLED, f"graduate-only title: matched '{hit}'")

    hit = _contains(description, deg["kill_phrases"])
    if hit:
        return Verdict(KILLED, f"graduate-only posting: '{hit}'")

    return Verdict(SURFACE)


def evaluate_hard(posting: dict, rules: dict | None = None) -> Verdict:
    """Pass one. Every exclusion that does not need to know the term.

    A posting reaching SURFACE here is not finished; it still has to clear the
    timing rules once Stage 0 has read its term. It returns PENDING to say so.
    """
    rules = rules or load_rules()
    title = posting.get("title") or ""
    description = posting.get("description") or ""

    # An explicit yes from the owner beats every automated rule here, added
    # 2026-09-20 with the generalist software rule that made it necessary.
    #
    # Until then the prefilter could not see his labels at all, so a rule added
    # later could silently kill a posting he had personally marked interested,
    # prune it from Airtable on the next sync, and leave him no way to notice.
    # The generalist software rule would have done exactly that to two roles, a
    # Palantir infrastructure internship and an Anduril one.
    #
    # Only "interested" protects. "not_interested" is his no and the rules are
    # free to agree with it, and an unlabelled posting carries no opinion at all,
    # which matters because he labels very little: absence of a label means he
    # never looked, never that he declined. It deliberately does not exempt the
    # timing rules, because a role he wants that is for the wrong season is
    # still for the wrong season.
    if (posting.get("label") or "") == "interested":
        return Verdict(PENDING, "kept: labelled interested, which overrides the hard rules")
    from_feed = bool((posting.get("source") or "").startswith("feed:")) or bool(
        posting.get("feed_terms")
    )

    if not is_student_role(title, from_feed, rules):
        return Verdict(KILLED, "not a student or early-career role")

    flags: list[str] = []
    for verdict in (
        check_employer(posting.get("company") or "", rules),
        check_excluded_roles(title, rules),
        check_generalist_software(title, rules),
        check_quant(title, rules),
        check_location(posting.get("location") or "", rules),
        check_clearance(description, rules),
        check_degree(title, description, posting.get("feed_degrees") or "", rules),
    ):
        if verdict.killed:
            return verdict
        flags.extend(verdict.flags)

    return Verdict(PENDING, "clears the hard exclusions, waiting on term", flags)


# ----------------------------------------------------------------- timing pass

def match_term(stated: str, rules: dict) -> tuple[str | None, dict | None]:
    """Map a stated term onto a term key from prefilter.toml.

    One matcher, two callers. The aggregator feeds state the term in a field and
    Stage 0 reads it out of the description, and both arrive here, so there is a
    single term vocabulary and a single place to change it.

    A posting naming several terms resolves to the most permissive one it names,
    which is the entry appearing earliest in the file. Ordering the summer 2027
    entry first is therefore load bearing: a posting open to both summer 2027
    and fall 2026 should not be filtered by the fall part-time rule.
    """
    if not stated:
        return None, None
    lowered = stated.lower()
    for entry in rules.get("term", []):
        if any(p.lower() in lowered for p in entry["patterns"]):
            return entry["key"], entry
    return None, None


def _parse_hours(weekly_hours: str) -> float | None:
    """First number in the stated hours. '15-20' reads as 15, the lower bound.

    The lower bound is the right read: a posting offering 15 to 20 hours is
    compatible with a class schedule, and taking the upper bound would filter it.
    """
    numbers = re.findall(r"\d+(?:\.\d+)?", weekly_hours or "")
    return float(numbers[0]) if numbers else None


def is_part_time_compatible(posting: dict, rules: dict) -> tuple[bool | None, str]:
    """Whether this role can run alongside a full course load.

    Returns True, False, or None for unknown, plus a short explanation. Unknown
    is a real answer and must never be collapsed into False; PRD section 5 says
    a posting that does not state hours is surfaced with a flag.
    """
    com = rules["commitment"]
    hours = posting.get("weekly_hours") or ""

    # Remote is read from where the job is, not from the body text. A long
    # description mentions remote work in passing constantly; the location field
    # and the title are where a genuinely remote role says so.
    where = f"{posting.get('title') or ''} {posting.get('location') or ''}"
    hit = _contains(where, com["remote_phrases"])
    if hit:
        return True, f"remote or remote-friendly: '{hit}'"

    # Likewise the hours come from what Stage 0 extracted, not from a second
    # scan of the raw description. Stage 0 already read it; re-reading it here
    # would silently second-guess the one component that owns this fact.
    hit = _contains(f"{hours} {posting.get('hours_evidence') or ''} {where}",
                    com["part_time_phrases"])
    if hit:
        return True, f"part-time wording: '{hit}'"

    number = _parse_hours(hours)
    if number is not None:
        limit = com["max_part_time_hours"]
        if number <= limit:
            return True, f"{number:g} hours per week is at or under {limit}"
        return False, f"{number:g} hours per week is over {limit}"

    if "full-time" in hours.lower() or "full time" in hours.lower():
        return False, "posting states full-time"

    return None, "hours not stated"


def evaluate_timing(posting: dict, rules: dict | None = None) -> Verdict:
    """Pass two. The timing rules in PRD section 5, keyed off the term.

    Call this only on a posting that reached PENDING in pass one and has since
    been tagged by Stage 0.
    """
    rules = rules or load_rules()
    terms = rules["terms"]

    stated = posting.get("term_stated") or posting.get("feed_terms") or ""
    key, entry = match_term(stated, rules)

    if entry is None:
        policy = terms["unmatched_policy"] if stated else terms["unknown_policy"]
        flag = terms["unmatched_flag"] if stated else terms["unknown_flag"]
        if policy == "kill":
            return Verdict(KILLED, f"term policy: {stated or 'unknown'}")
        return Verdict(SURFACE, f"term not resolved: {stated or 'not stated'}", [flag])

    policy = entry["policy"]
    flags = [entry["flag"]] if entry.get("flag") else []

    if policy == "kill":
        return Verdict(KILLED, f"term outside the recruiting cycle: {stated}")

    if policy == "part_time_only":
        compatible, why = is_part_time_compatible(posting, rules)
        if compatible is False:
            # A term may exempt certain places from its own part-time rule. A
            # full-time role in a city the owner is already living in during that
            # term is a different proposition from one that needs him elsewhere.
            here = _word_start(
                posting.get("location") or "", entry.get("full_time_ok_locations", [])
            )
            if here:
                flags.append(entry.get("full_time_ok_flag", "local_full_time"))
                return Verdict(SURFACE, f"{key} full time, but in {here}: {why}", flags)
            # Otherwise filtered silently, per PRD section 5. He is in class; an
            # in-person full-time role elsewhere in a term he is enrolled in is
            # not an opportunity.
            return Verdict(KILLED, f"{key} in-person full time: {why}")
        if compatible is None:
            flags.append(rules["commitment"]["unclear_flag"])
        return Verdict(SURFACE, f"{key}: {why}", flags)

    return Verdict(SURFACE, f"term {key}", flags)


def needs_term(posting: dict) -> bool:
    """Whether the timing pass would have to guess without Stage 0 tagging.

    A posting whose feed already stated an unambiguous term does not need a
    model call, which is what makes the 1,527 feed postings free to tag.
    """
    if posting.get("tagged_at"):
        return False
    return not match_term(posting.get("feed_terms") or "", load_rules())[0]
