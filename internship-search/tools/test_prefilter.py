"""Cases the prefilter must get right. Run with:

    .venv/bin/python -m tools.test_prefilter

No test framework, no dependencies, no database. Each case is a posting and the
verdict it must produce, and the file prints what failed.

This exists because a prefilter bug is silent. A ranking mistake shows up in the
digest as a bad suggestion; a prefilter mistake shows up as nothing at all, and
The owner never learns what he did not see. Every case below is either a rule from
PRD section 5 or a real collision found in the live data on 2026-08-07.

Add a case here whenever you change sources/prefilter.toml, especially when
loosening a rule. The cases are cheap and the failure mode they guard is not.
"""

import sys

from agent import prefilter

KILL, SURFACE, PENDING = prefilter.KILLED, prefilter.SURFACE, prefilter.PENDING


def posting(**kw) -> dict:
    base = {
        # Neutral on purpose: a student role that no rule kills, so a case
        # about locations is testing locations. It was "Software Engineer
        # Intern" until 2026-09-20, when the generalist software rule made that
        # title a kill and short-circuited twelve cases that were testing
        # something else entirely. The suite caught it; that is what it is for.
        "title": "Machine Learning Engineer Intern",
        "location": "San Francisco, CA",
        "description": "",
        "source": "greenhouse:example",
        "feed_terms": "",
        "feed_degrees": "",
        "term_stated": "Summer 2027",
        "weekly_hours": "",
        "hours_evidence": "",
        "tagged_at": "2026-08-07T00:00:00+00:00",
    }
    base.update(kw)
    return base


# (label, posting, expected outcome of the hard pass)
HARD_CASES = [
    # --- the student gate -------------------------------------------------
    ("plain internship passes", posting(title="Product Management Intern"), PENDING),
    ("the neutral fixture passes every rule", posting(), PENDING),
    ("senior role dies", posting(title="Staff Software Engineer"), KILL),
    ("account executive dies", posting(title="Account Executive, Public Sector"), KILL),
    # "intern" prefix-matches "International". 98 senior roles got in this way.
    ("International is not an internship",
     posting(title="Head of Deal Desk - International"), KILL),
    ("Internal is not an internship",
     posting(title="Internal Audit Manager"), KILL),
    # A feed listing is judged on its title like everything else, since
    # trust_feed_provenance was turned off on 2026-08-19. This one still passes,
    # on "undergraduate", and it passes for that reason rather than for being
    # from a feed.
    ("a feed title with a student word still passes",
     posting(title="Undergraduate Cartographer", source="feed:simplify",
             feed_terms="Summer 2027"), PENDING),
    # The cost of that change, kept as a case rather than deleted, because it is
    # a real internship being killed and the number should stay visible. This
    # posting was PENDING until 2026-08-19 on provenance alone. The owner chose the
    # trade after reading all 22 postings it removes: a base holding roles that
    # are plainly not internships was worse than losing a handful whose titles
    # say nothing. Flip trust_feed_provenance back and this returns to PENDING.
    ("a feed title with no student word now dies, typo and all",
     posting(title="ntern, Engineer Software", source="feed:vanshb03",
             feed_terms="Summer 2027"), KILL),

    # --- quant, matched on role and never on employer ---------------------
    ("quant research intern dies", posting(title="Quantitative Research Intern"), KILL),
    ("quant trader intern dies", posting(title="Quantitative Trader Intern"), KILL),
    ("AI engineer at a quant firm survives",
     posting(title="Machine Learning Engineer Intern"), PENDING),
    ("quant developer is flagged, not killed",
     posting(title="Quantitative Developer Intern"), PENDING),

    # --- location, deliberately loose -------------------------------------
    ("London dies", posting(location="London, UK"), KILL),
    ("Toronto dies", posting(location="Toronto, ON, Canada"), KILL),
    ("Vancouver WASHINGTON survives", posting(location="Vancouver, WA"), PENDING),
    ("Waterloo IOWA survives", posting(location="Waterloo, IA"), PENDING),
    ("Melbourne FLORIDA survives", posting(location="Melbourne, FL"), PENDING),
    ("a US option among foreign ones survives",
     posting(location="London, UK; San Francisco, CA"), PENDING),
    ("remote in Canada survives, per PRD's remote exemption",
     posting(location="Remote in Canada"), PENDING),
    ("no location survives", posting(location=""), PENDING),
    ("umbrella location survives", posting(location="Multiple Locations"), PENDING),

    # --- clearance --------------------------------------------------------
    ("existing clearance dies",
     posting(description="Applicants must have an active security clearance."), KILL),
    ("sponsored clearance survives",
     posting(description="Must have the ability to obtain an active security "
                         "clearance after hire."), PENDING),

    # --- graduate only ----------------------------------------------------
    ("PhD-only feed degrees dies",
     posting(source="feed:simplify", feed_terms="Summer 2027", feed_degrees="PhD"), KILL),
    ("master's and PhD only dies",
     posting(source="feed:simplify", feed_terms="Summer 2027",
             feed_degrees="Master's; PhD"), KILL),
    ("bachelor's included survives",
     posting(source="feed:simplify", feed_terms="Summer 2027",
             feed_degrees="Bachelor's; Master's; PhD"), PENDING),
    ("PhD as a title prefix dies", posting(title="PhD Intern, Robotics"), KILL),
    ("PhD as a title suffix dies", posting(title="Data Science Intern, PhD"), KILL),
    ("a PhD mentioned nowhere near the role survives",
     posting(title="Research Intern, Physics"), PENDING),
    ("a preference for a graduate degree survives",
     posting(description="A master's degree is preferred but not required."), PENDING),

    # --- master's, added 2026-08-12 ---------------------------------------
    # He is an undergraduate, so a master's-restricted posting is as much a
    # mismatch as a PhD one.
    ("master's as a title suffix dies",
     posting(title="Data Engineer- Data Science Intern, Master's"), KILL),
    ("masters as a title word dies",
     posting(title="Software Engineering Masters Intern"), KILL),
    ("a graduate student intern dies",
     posting(title="Machine Learning Physics Graduate Student Intern"), KILL),
    # The trap this guards. Matching is word-start anchored, so \bgraduate finds
    # no boundary inside "undergraduate". If anyone switches the degree list to
    # a plain substring match, this case is what fails.
    ("an UNDERgraduate student intern survives",
     posting(title="Computing Undergraduate Student Intern"), PENDING),

    # --- full-time roles are not internships, added 2026-08-12 ------------
    # New-grad and early-career postings are jobs you start after graduating.
    # He graduates June 2028, so none are takeable this cycle.
    ("an early career full-time role dies",
     posting(title="2026 Early Career Electrical Engineer"), KILL),
    ("a new grad role dies",
     posting(title="Software Engineer, New Grad - Defense"), KILL),
    ("a rotational program dies",
     posting(title="People Analytics & Operations (Rotational Program)"), KILL),
    ("a head of early career recruiting role dies",
     posting(title="Head of Early Career Recruiting"), KILL),
    ("a campus recruiter dies", posting(title="Campus Recruiter"), KILL),
    ("even a genuine recruiting internship dies",
     posting(title="Campus Recruiting Intern"), KILL),
    ("a talent acquisition internship dies",
     posting(title="Talent Acquisition Intern"), KILL),
    ("a recruiting coordinator internship dies",
     posting(title="Recruiting Coordinator Intern"), KILL),
    # Added 2026-09-25. The same words name campus PROGRAMS, and the bare word
    # killed a forward deployed internship, his first choice of role.
    ("an internship run by a campus recruiting program survives",
     posting(title="Forward Deployed Engineer Intern - Campus Recruiting 2027"),
     PENDING),
    ("an internship inside a recruitment program survives",
     posting(title="Research Intern - Global Frontier Tech Recruitment Program"),
     PENDING),

    # --- excluded role categories, added 2026-08-12 -----------------------
    # These pass the student gate honestly; they are real student roles. They
    # are just not roles he would take, which is a different question.
    ("a law school student ambassador dies",
     posting(title="Law School Student Ambassador"), KILL),
    ("a campus ambassador dies", posting(title="Campus Ambassador Program"), KILL),
    ("a nursing student role dies", posting(title="Nursing Student Intern"), KILL),
    # What must NOT break. A bare "law" is deliberately not excluded, because it
    # would kill this employer and every lawful-basis compliance role.
    ("Lawrence Livermore survives the law exclusion",
     posting(company="Lawrence Livermore National Laboratory",
             title="Computing Undergraduate Student Intern"), PENDING),
    ("a policy internship survives", posting(title="AI Policy Intern"), PENDING),
    # What must NOT break: an early-career posting that is a real internship
    # still matches on "intern", and fellowships stay in scope entirely.
    ("an early career INTERNSHIP still survives",
     posting(title="Early Career Internship Program"), PENDING),
    ("a fellowship survives", posting(title="AI Policy Fellowship"), PENDING),
    ("a fellow title survives", posting(title="Research Fellow, Alignment"), PENDING),

    # --- words removed from the student gate, added 2026-08-14 -------------
    # "campus", "apprentice", "trainee" and "resident" admitted 25 postings on
    # their own and not one was an internship. Every case here is a real title
    # that was sitting in Airtable waiting to be labelled.
    ("a campus full-time hire dies",
     posting(title="Campus Software Engineer (Full-Time)"), KILL),
    ("a campus facilities job dies",
     posting(title="Bastrop Campus Planning Manager"), KILL),
    ("an apprentice trade role dies",
     posting(title="Apprentice Fabrication Technician - 2nd Shift"), KILL),
    ("a trades trainee dies",
     posting(title="NDE Inspector Trainee (Starship) - Temporary"), KILL),
    ("a resident staff job dies",
     posting(title="Resident Solutions Architect"), KILL),
    # What must NOT break. Every genuine posting carrying these words carries
    # "intern" or "undergraduate" too, which is why removing them costs nothing.
    ("the same campus role as an INTERNSHIP survives",
     posting(title="Campus AI Research Engineer (Intern)"), PENDING),
    ("a campus undergraduate internship survives",
     posting(title="Campus Undergraduate Summer Internship - Strategy"), PENDING),
    # "residency" stays in the gate while "resident" leaves it. An AI residency
    # is a programme he wants; a resident engineer is a staff job.
    ("an AI residency survives", posting(title="AI Residency"), PENDING),

    # --- role categories added 2026-08-14 ---------------------------------
    ("a skilled trades role dies",
     posting(title="Robot Service Technician"), KILL),
    ("a campus food service job dies", posting(title="Student Barista"), KILL),
    ("a legal internship dies", posting(title="Legal Intern - Summer 2027"), KILL),
    ("a compliance co-op dies", posting(title="Compliance Analyst Co-Op"), KILL),
    ("a business development internship dies",
     posting(title="2027 Business Development Summer Analyst"), KILL),
    ("a public policy internship dies",
     posting(title="Public Policy & Community Affairs Intern"), KILL),
    ("a talent community listing dies",
     posting(title="2027 EU Campus Programme Talent Community"), KILL),
    ("a senior fellow dies", posting(title="Senior Fellow, Energetics Safety"), KILL),
    ("a technical fellow dies",
     posting(title="Engineering Technical Fellow, Solid Rocket Motors"), KILL),
    ("a student contract worker dies",
     posting(title="Student Contract Worker: Analyst (Full-time)"), KILL),
    ("the other word order dies too",
     posting(title="Contract Student Worker - Machine Learning Engineer"), KILL),
    # What must NOT break. Every entry in that group is a phrase, never a bare
    # field name, so a software role on a compliance or marketing team survives.
    # A bare "compliance" would have killed this exact posting.
    # The point of this case is that a bare "compliance" must not kill a
    # technical role. Its title was ByteDance's real "Software Engineer Intern -
    # Global Payment - Compliance" until 2026-09-20; that posting now dies on
    # the generalist software rule instead, which is correct and intended, so
    # the case carries a rescued title to keep testing the thing it was written
    # for.
    ("a technical role on a compliance team survives",
     posting(title="Machine Learning Engineer Intern - Global Payment - Compliance"),
     PENDING),
    ("the Anthropic Fellows Program survives",
     posting(title="Anthropic Fellows Program, Reinforcement Learning"), PENDING),
    ("an ML fellowship survives",
     posting(title="Machine Learning Fellow - Human Frontier Collective"), PENDING),

    # --- the bare degree name, added 2026-08-14 ---------------------------
    # The older patterns all needed the degree next to the word "intern". These
    # three put it elsewhere in the title and were reaching Airtable.
    ("a PhD scientist internship dies",
     posting(title="PhD GenAI Research Scientist Intern"), KILL),
    ("a PhD research internship dies",
     posting(title="Hardware Machine Learning PhD Research Internship"), KILL),
    ("an MBA internship dies",
     posting(title="Product Management Intern - MBA - Power Solutions"), KILL),
    # The trap this guards, and the reason [degree].allow_title exists. A title
    # naming several degree levels is open to the lowest one it names. Without
    # the allow list the bare "phd" rule kills this posting silently, which is
    # the failure mode this whole file is written against.
    ("an internship open to BS/MS/PhD survives",
     posting(title="Research Intern (BS/MS/PhD)"), PENDING),

    # --- the employer rule, added 2026-08-14 ------------------------------
    # A university's campus jobs are open only to its own enrolled students, and
    # no title rule can see that: the title is identical to one he could take.
    ("another university's research assistantship dies",
     posting(company="Pennsylvania State University",
             title="Undergraduate Research Assistant"), KILL),
    ("another university's student job dies",
     posting(company="Arizona State University",
             title="Student Data Science Assistant"), KILL),
    # What must NOT break, twice over. His own university is where an
    # undergraduate assistantship is both takeable and local.
    ("his own university survives",
     posting(company="University of Chicago",
             title="Undergraduate Research Assistant"), PENDING),
    # And a separately incorporated employer carrying a university's name is not
    # a university. UVIMCO runs UVA's endowment and hires anyone.
    ("a university endowment fund survives",
     posting(company="University of Virginia Investment Management Company (UVIMCO)",
             title="Investment Analyst Intern"), PENDING),
    # Added 2026-09-25: a hospital and a trading firm's campus brand both carry
    # "University" in the name, and neither restricts hiring to its students.
    ("a university hospital system survives",
     posting(company="Cooper University Health Care",
             title="Enterprise Analytics Intern"), PENDING),
    ("a trading firm's campus program brand survives",
     posting(company="Akuna Capital University", title="Hardware Engineer Intern"),
     PENDING),
    ("a real university is still killed",
     posting(company="Pennsylvania State University", title="Research Intern"),
     KILL),

    # A slash inside a token is part of a name, not a list separator. Micron's
    # "Fab 10N/X, Singapore" split on a bare "/" into an unresolvable "Fab 10N"
    # and "X, Singapore", so the location never resolved ENTIRELY outside the
    # US and 40 Singapore fab roles surfaced. Both cases, because the fix is a
    # separator change and the risk of it is over-joining a real two-city
    # string.
    ("a slash inside a token does not split the location",
     posting(location="Fab 10N/X, Singapore"), KILL),
    ("a spaced slash still separates two places",
     posting(location="Singapore / Austin, TX"), PENDING),

    # --- generalist software, added 2026-09-20 ----------------------------
    # The craft is killed and the domain rescues it. Both halves need cases,
    # because a keep list that stopped working would look exactly like a kill
    # list doing its job, and 24 percent of the queue rides on this rule.
    ("a plain software engineering internship dies",
     posting(title="Software Engineer Intern"), KILL),
    ("a backend internship dies",
     posting(title="Software Engineer Intern - Backend"), KILL),
    ("software developer is the same role by another name",
     posting(title="Software Developer Intern"), KILL),
    # Each of these is a real posting the first draft of the rule killed.
    ("forward deployed survives, it is his first choice of role",
     posting(title="Forward Deployed Software Engineer, Internship"), PENDING),
    ("a vehicle domain survives",
     posting(title="Software Engineer Intern - Torque Path & Chassis Control"),
     PENDING),
    ("self-driving survives",
     posting(title="Software Engineer Intern, Maps & Self-Driving Navigation"),
     PENDING),
    ("avionics survives",
     posting(title="Software Developer Intern - Avionics Software"), PENDING),
    ("embedded survives, physics is an advantage there",
     posting(title="Embedded Software Engineer Intern"), PENDING),
    ("machine learning survives",
     posting(title="Software Engineer Intern, Machine Learning"), PENDING),
    # A role that never says "software engineer" is not this rule's business.
    ("an ML engineer title is untouched by the rule",
     posting(title="Machine Learning Engineer Intern"), PENDING),

    # --- an explicit yes overrides the hard rules -------------------------
    # The label override exists because this rule would otherwise have killed
    # two postings the owner had personally marked interested, pruned them from
    # Airtable, and left nothing to notice it by.
    ("a posting he labelled interested survives a kill rule",
     posting(title="Software Engineer Intern", label="interested"), PENDING),
    ("interested also overrides an unrelated kill rule",
     posting(title="Recruiting Coordinator Intern", label="interested"), PENDING),
    # His no is not overridden, and neither is silence. He labels very little,
    # so an unlabelled posting means he never looked, not that he declined.
    ("not_interested does not protect anything",
     posting(title="Software Engineer Intern", label="not_interested"), KILL),
    ("an unlabelled posting is not protected",
     posting(title="Software Engineer Intern", label=""), KILL),
]

# (label, posting, expected outcome, flags that must be present)
TIMING_CASES = [
    ("summer 2027 goes straight through",
     posting(term_stated="Summer 2027"), SURFACE, []),
    # Fall 2026 was part_time_only until 2026-08-12, when the owner said he cannot
    # do a fall internship at all. These four cases used to assert that part
    # time, remote and unstated-hours fall roles survived; they now assert the
    # opposite, because the hours no longer matter once the term is out.
    ("fall 2026 full time in person dies",
     posting(term_stated="Fall 2026", weekly_hours="40"), KILL, []),
    ("fall 2026 part time dies too, hours are irrelevant now",
     posting(term_stated="Fall 2026", weekly_hours="20"), KILL, []),
    ("fall 2026 remote dies whatever the hours",
     posting(term_stated="Fall 2026", weekly_hours="40",
             location="Remote in USA"), KILL, []),
    ("fall 2026 with unstated hours dies rather than surfacing flagged",
     posting(term_stated="Fall 2026"), KILL, []),
    ("spring 2027 full time elsewhere still dies",
     posting(term_stated="Spring 2027", weekly_hours="40"), KILL, []),
    # Added 2026-08-09 at the owner's request: a full-time spring 2027 role in
    # Chicago is acceptable, everywhere else the part-time rule stands.
    ("spring 2027 full time in Chicago survives, flagged",
     posting(term_stated="Spring 2027", weekly_hours="40", location="Chicago, IL"),
     SURFACE, ["local_full_time"]),
    ("the Chicago exemption reads a multi-city location",
     posting(term_stated="Spring 2027", weekly_hours="40",
             location="Chicago; New York"), SURFACE, ["local_full_time"]),
    ("the Chicago exemption does not leak into fall 2026",
     posting(term_stated="Fall 2026", weekly_hours="40", location="Chicago, IL"),
     KILL, []),
    ("the Chicago exemption does not leak into other cities",
     posting(term_stated="Spring 2027", weekly_hours="40",
             location="New York, NY"), KILL, []),
    ("a part-time Chicago spring role is not flagged full time",
     posting(term_stated="Spring 2027", weekly_hours="20", location="Chicago, IL"),
     SURFACE, []),
    ("winter 2027 full time survives, flagged",
     posting(term_stated="Winter 2027", weekly_hours="40"), SURFACE, ["leave_required"]),
    ("a past term dies", posting(term_stated="Summer 2026"), KILL, []),
    ("an unknown term surfaces with a flag",
     posting(term_stated=""), SURFACE, ["unclear_term"]),
    ("a bare season with no year surfaces with a flag",
     posting(term_stated="Summer"), SURFACE, ["unclear_term"]),
    ("the most permissive term wins when several are listed",
     posting(term_stated="Summer 2027; Fall 2026", weekly_hours="40"), SURFACE, []),
    # Spring, not fall, since fall now dies on the term before the hours are
    # ever read. The rule under test is the hour parsing, not the term.
    ("a range of hours reads as its lower bound",
     posting(term_stated="Spring 2027", weekly_hours="15-20"), SURFACE, []),
    ("beyond the cycle surfaces with a flag",
     posting(term_stated="Summer 2028"), SURFACE, ["outside_cycle"]),
]

# (label, posting, flag that must be present after the hard pass)
FLAG_CASES = [
    ("ambiguous quant title carries a flag",
     posting(title="Quantitative Developer Intern"), "unclear_quant"),
    ("unresolved location carries a flag",
     posting(location="Belgrade"), "unclear_location"),
    ("blank location carries a flag", posting(location=""), "unclear_location"),
]


def main() -> int:
    rules = prefilter.load_rules()
    failures = []

    for label, p, expected in HARD_CASES:
        got = prefilter.evaluate_hard(p, rules)
        if got.outcome != expected:
            failures.append(f"hard   {label}: expected {expected}, got "
                            f"{got.outcome} ({got.reason})")

    for label, p, expected, want_flags in TIMING_CASES:
        got = prefilter.evaluate_timing(p, rules)
        if got.outcome != expected:
            failures.append(f"timing {label}: expected {expected}, got "
                            f"{got.outcome} ({got.reason})")
        missing = [f for f in want_flags if f not in got.flags]
        if missing:
            failures.append(f"timing {label}: missing flag(s) {missing}, "
                            f"got {got.flags}")

    for label, p, want in FLAG_CASES:
        got = prefilter.evaluate_hard(p, rules)
        if want not in got.flags:
            failures.append(f"flag   {label}: expected {want}, got {got.flags}")

    total = len(HARD_CASES) + len(TIMING_CASES) + len(FLAG_CASES)
    if failures:
        print(f"{len(failures)} of {total} checks FAILED\n")
        for line in failures:
            print(f"  {line}")
        return 1

    print(f"All {total} prefilter checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
