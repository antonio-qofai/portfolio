#!/usr/bin/env python3
"""
run_research.py — Module 8 orchestration

Usage:
  python3 scripts/run_research.py "FIRM NAME"
  python3 scripts/run_research.py "FIRM NAME" --dry-run
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


DEFAULT_MAX_PORTCOS = 8
DEFAULT_MAX_PORTCO_WORKERS = 4  # run portcos 4 at a time; raise to 8-10 for speed if credits allow
DEFAULT_MAX_FINANCIAL_WORKERS = 2
EXAMPLE_PORTCOS = [
    "Example Company A",
    "Example Company B",
    "Example Company C",
]
MODEL = "claude-sonnet-4-6"
PORTCO_PROFILER_AGENT_PATH = ".claude/agents/portco-profiler.md"
AUDIT_TIMEOUT = 1200
FIRM_SECTION_NAMES = [
    "Firm Overview",
    "Investment Strategy",
    "Leadership",
]


@dataclass(frozen=True)
class RunContext:
    repo_root: Path
    runs_dir: Path
    run_dir: Path
    dossiers_dir: Path
    firm_name: str
    max_portcos: int
    max_portco_workers: int
    max_financial_workers: int
    skip_financials: bool
    dry_run: bool
    resume_run: Path | None
    from_phase: int | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build and optionally execute the Module 8 research workflow."
    )
    parser.add_argument("firm_name", help="Firm name to research.")
    parser.add_argument(
        "--max-portcos",
        type=int,
        default=DEFAULT_MAX_PORTCOS,
        help=f"Maximum number of portfolio companies to process (default: {DEFAULT_MAX_PORTCOS}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print and write the execution plan without calling Claude.",
    )
    parser.add_argument(
        "--portco-workers",
        type=int,
        default=DEFAULT_MAX_PORTCO_WORKERS,
        help=(
            "Maximum concurrent portco-profiler subagents "
            f"(default: {DEFAULT_MAX_PORTCO_WORKERS})."
        ),
    )
    parser.add_argument(
        "--financial-workers",
        type=int,
        default=DEFAULT_MAX_FINANCIAL_WORKERS,
        help=(
            "Maximum concurrent private-data-approximator calls "
            f"(default: {DEFAULT_MAX_FINANCIAL_WORKERS})."
        ),
    )
    parser.add_argument(
        "--skip-financials",
        action="store_true",
        help="Skip private-data-approximator to reduce token usage.",
    )
    parser.add_argument(
        "--resume-run",
        help="Resume from an existing run directory under runs/ or an absolute path.",
    )
    parser.add_argument(
        "--from-phase",
        type=int,
        choices=[9, 10],
        help="Resume starting from Phase 9 or Phase 10.",
    )
    return parser.parse_args()


def ensure_directories(repo_root: Path) -> tuple[Path, Path, Path]:
    scripts_dir = repo_root / "scripts"
    runs_dir = repo_root / "runs"
    dossiers_dir = repo_root / "dossiers"

    for directory in (scripts_dir, runs_dir, dossiers_dir):
        directory.mkdir(parents=True, exist_ok=True)

    return scripts_dir, runs_dir, dossiers_dir


def build_run_context(args: argparse.Namespace) -> RunContext:
    repo_root = Path(__file__).resolve().parent.parent
    _, runs_dir, dossiers_dir = ensure_directories(repo_root)
    resume_run: Path | None = None

    if args.resume_run:
        candidate = Path(args.resume_run)
        if not candidate.is_absolute():
            candidate = repo_root / candidate
        resume_run = candidate.resolve()
        run_dir = resume_run
    else:
        run_dir: Path | None = None
        for _ in range(5):
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            candidate = runs_dir / timestamp
            try:
                candidate.mkdir(parents=True, exist_ok=False)
                run_dir = candidate
                break
            except FileExistsError:
                continue
        if run_dir is None:
            raise RuntimeError("Unable to create a unique run directory.")

    return RunContext(
        repo_root=repo_root,
        runs_dir=runs_dir,
        run_dir=run_dir,
        dossiers_dir=dossiers_dir,
        firm_name=args.firm_name,
        max_portcos=args.max_portcos,
        max_portco_workers=args.portco_workers,
        max_financial_workers=args.financial_workers,
        skip_financials=args.skip_financials,
        dry_run=args.dry_run,
        resume_run=resume_run,
        from_phase=args.from_phase,
    )


def get_dry_run_portcos(max_portcos: int) -> list[str]:
    return EXAMPLE_PORTCOS[:max_portcos]


def render_run_plan(context: RunContext, portfolio_companies: list[str]) -> str:
    portco_lines = "\n".join(
        f"  - portco-profiler: {company}" for company in portfolio_companies
    )
    if not portco_lines:
        portco_lines = "  - No portfolio companies scheduled"

    return f"""# Run Plan

- Firm: {context.firm_name}
- Mode: {"dry-run" if context.dry_run else "live"}
- Max portfolio companies: {context.max_portcos}
- Portco workers: {context.max_portco_workers}
- Financial workers: {context.max_financial_workers}
- Skip financial estimates: {"yes" if context.skip_financials else "no"}
- Run directory: {context.run_dir}

## Execution Graph

1. In parallel:
   - firm-profiler
   - portfolio-discoverer
2. Then, after portfolio discovery:
{portco_lines}
   - These tasks use `{PORTCO_PROFILER_AGENT_PATH}` via --agent portco-profiler.
3. Then sequentially:
   - {"private-data-approximator" if not context.skip_financials else "private-data-approximator (skipped)"}
   - claim-labeler (confidence scoring + source typing in one combined pass)
   - dossier-assembler (Python — no Claude call)
   - audit-pass
"""


def write_run_plan(context: RunContext, plan_text: str) -> Path:
    plan_path = context.run_dir / "run-plan.md"
    plan_path.write_text(plan_text, encoding="utf-8")
    return plan_path


def print_run_plan(plan_text: str) -> None:
    print(plan_text)


# ---------------------------------------------------------------------------
# Orchestration helpers
# ---------------------------------------------------------------------------

def load_skill(repo_root: Path, skill_name: str) -> str:
    return (repo_root / "skills" / skill_name / "SKILL.md").read_text()


def load_sources(repo_root: Path) -> tuple[int, list[str]]:
    sources_dir = repo_root / "sources"
    source_files = sorted([f for f in sources_dir.glob("*.md") if f.name != ".gitkeep"])
    if not source_files:
        return 0, []
    return len(source_files), [sf.name for sf in source_files]


def list_source_files(repo_root: Path) -> list[Path]:
    sources_dir = repo_root / "sources"
    return sorted([f for f in sources_dir.glob("*.md") if f.name != ".gitkeep"])


def load_prompts(repo_root: Path) -> dict[str, str]:
    prompts_dir = repo_root / "prompts"
    # confidence_rubric and source_rubric are no longer injected into any system prompt;
    # scoring rules live in the skill contracts. Only load what is actually used.
    return {
        "research_agent": (prompts_dir / "research-agent.md").read_text(),
        "confidence_rubric": "",
        "source_rubric": "",
    }


def make_system_prompt(
    prompts: dict[str, str],
    extra_contract: str | None = None,
    *,
    include_research_agent: bool = True,
    include_confidence_rubric: bool = True,
    include_source_rubric: bool = True,
) -> str:
    parts = [
        "You are the QofAI Research Agent.",
    ]
    if include_research_agent:
        parts.extend(["", "## Governing Contract", "", prompts["research_agent"]])
    if include_confidence_rubric:
        parts.extend(["", "## Confidence Rubric", "", prompts["confidence_rubric"]])
    if include_source_rubric:
        parts.extend(["", "## Source Type Rubric", "", prompts["source_rubric"]])
    if extra_contract:
        parts.extend(["", "## Skill Contract", "", extra_contract])
    return "\n".join(parts)


def run_claude(system_prompt: str, user_message: str, repo_root: Path, timeout: int = 600) -> str:
    result = subprocess.run(
        [
            "claude", "--print",
            "--output-format", "text",
            "--model", MODEL,
            "--effort", "low",
            "--dangerously-skip-permissions",
            "--system-prompt", system_prompt,
        ],
        input=user_message,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(repo_root),
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        stdout = result.stdout.strip()
        detail = stderr or stdout or "claude exited non-zero"
        raise RuntimeError(detail)
    return result.stdout


def run_portco_subagent(
    company_name: str,
    firm_name: str,
    repo_root: Path,
    user_message: str,
    timeout: int = 300,
) -> tuple[str, str]:
    result = subprocess.run(
        [
            "claude", "--print",
            "--output-format", "text",
            "--model", MODEL,
            "--effort", "low",
            "--dangerously-skip-permissions",
            "--agent", "portco-profiler",
        ],
        input=user_message,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(repo_root),
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        stdout = result.stdout.strip()
        detail = stderr or stdout or "claude exited non-zero"
        raise RuntimeError(
            f"portco-profiler failed for {company_name}: {detail}"
        )
    return company_name, result.stdout


def extract_portfolio_companies(
    inventory_text: str, repo_root: Path, max_portcos: int
) -> list[str]:
    del repo_root
    companies: list[str] = []
    seen: set[str] = set()
    in_active_section = False
    has_sectioned_inventory = "section 1" in inventory_text.lower()

    for line in inventory_text.splitlines():
        stripped = line.strip()
        lower = stripped.lower()

        if lower.startswith("### section 1") or lower.startswith("## section 1"):
            in_active_section = True
            continue
        if in_active_section and (
            lower.startswith("### section 2")
            or lower.startswith("## section 2")
            or lower.startswith("### section 3")
            or lower.startswith("## section 3")
        ):
            break

        company: str | None = None

        heading_match = re.match(r"^###\s+\d+(?:\.\d+)*\s+(.+)$", stripped)
        if heading_match:
            if in_active_section or "section 1" not in inventory_text.lower():
                company = heading_match.group(1).strip()
        elif re.match(r"^\*\*[A-Za-z].*\(\d+\)\*\*$", stripped):
            if in_active_section or "section 1" not in inventory_text.lower():
                company = stripped.strip("*")
                company = re.sub(r"\s+\(\d+\)\s*$", "", company).strip()
        elif stripped.startswith(("- ", "* ")) and not has_sectioned_inventory:
            body = stripped[2:].strip()
            if not body.lower().startswith(("firm press release", "firm website", "pitchbook", "source", "notes")):
                company = body.split(",")[0].split("(")[0].split("|")[0].strip()
        elif stripped.startswith("|") and not stripped.startswith("|---"):
            parts = [part.strip() for part in stripped.strip("|").split("|")]
            if len(parts) >= 2:
                first = parts[0].lower()
                second = parts[1]
                if re.match(r"^\d+(?:\.\d+)*$", parts[0]) and second and second.lower() != "company":
                    company = second

        if company:
            company = re.sub(r"^[\d.#\-\s]+", "", company).strip()
            company = re.sub(r"\s+\|.*$", "", company).strip()
            if company.lower().startswith("section "):
                company = None
            if company in {"Healthcare", "Essential Services", "Specialty Manufacturing"}:
                company = None

        if company and company.lower() not in seen:
            seen.add(company.lower())
            companies.append(company)
            if len(companies) >= max_portcos:
                break

    return companies


def slugify(text: str, max_length: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_length] or "item"


def parse_h2_sections(markdown: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current_heading: str | None = None
    current_lines: list[str] = []

    for line in markdown.splitlines():
        match = re.match(r"^##\s+(.+)$", line.strip())
        if match:
            if current_heading:
                sections[current_heading] = "\n".join(current_lines).strip() + "\n"
            current_heading = match.group(1).strip()
            current_lines = [f"## {current_heading}"]
        elif current_heading:
            current_lines.append(line)

    if current_heading:
        sections[current_heading] = "\n".join(current_lines).strip() + "\n"

    return sections


def require_section(sections: dict[str, str], section_name: str) -> str:
    if section_name not in sections:
        raise RuntimeError(f"Required section missing from firm-profiler output: {section_name}")
    return sections[section_name]


def find_section(sections: dict[str, str], section_name: str) -> str | None:
    return sections.get(section_name)


def ensure_section_has_sources(section_text: str) -> str:
    if "Sources:" in section_text:
        return section_text
    return section_text.rstrip() + "\n\n**Sources:**\n- Sources listed in inline labels.\n"


def normalize_section_output(section_name: str, text: str) -> str:
    heading = f"## {section_name}"
    if heading in text:
        normalized = text[text.index(heading):].strip()
        return normalized + "\n"
    stripped = text.strip()
    if stripped.startswith("## "):
        return stripped + "\n"
    return f"{heading}\n\n{stripped}\n"


def section_filename(section_name: str) -> str:
    return f"{slugify(section_name)}.md"


def extract_inventory_entry(inventory_text: str, company_name: str) -> str:
    lines = inventory_text.splitlines()
    exact_table_rows = [
        line
        for line in lines
        if line.strip().startswith("|")
        and f"| {company_name.lower()} |" in line.lower()
    ]
    if exact_table_rows:
        return "\n".join(exact_table_rows).strip()

    matching = [
        line
        for line in lines
        if company_name.lower() in line.lower()
    ]
    return "\n".join(matching).strip() or f"- {company_name}"


def find_inventory_conflicts(inventory_text: str, company_name: str) -> str:
    lowered_name = company_name.lower()
    matches = [
        line.strip()
        for line in inventory_text.splitlines()
        if lowered_name in line.lower() and "conflict" in line.lower()
    ]
    return "\n".join(matches).strip() or "None noted in inventory."


def infer_company_aliases(company_name: str) -> list[str]:
    aliases = {company_name}
    words = re.findall(r"[A-Za-z0-9]+", company_name)
    if words:
        aliases.add(" ".join(words))
    uppercase_words = [word for word in words if word.isupper() and len(word) >= 2]
    if uppercase_words:
        aliases.add(" ".join(uppercase_words))
    if len(words) > 1:
        aliases.add(words[0])
    return sorted(alias for alias in aliases if alias)


def load_source_contents(repo_root: Path) -> dict[str, str]:
    """Pre-load all source file contents so find_relevant_sources can reuse them across companies."""
    return {
        sf.name: sf.read_text(encoding="utf-8", errors="ignore").lower()
        for sf in list_source_files(repo_root)
    }


def find_relevant_sources(
    repo_root: Path,
    company_name: str,
    source_contents: dict[str, str] | None = None,
) -> list[str]:
    relevant_files: list[str] = []
    aliases = [alias.lower() for alias in infer_company_aliases(company_name)]
    aliases = sorted(aliases, key=len, reverse=True)

    if source_contents is None:
        source_contents = load_source_contents(repo_root)

    for filename, content in source_contents.items():
        if any(alias in content for alias in aliases):
            relevant_files.append(filename)

    return sorted(relevant_files)[:4]


def build_portco_user_message(
    context: RunContext,
    research_date: str,
    inventory_text: str,
    company_name: str,
    source_contents: dict[str, str] | None = None,
) -> str:
    inventory_entry = extract_inventory_entry(inventory_text, company_name)
    conflicts = find_inventory_conflicts(inventory_text, company_name)
    relevant_sources = find_relevant_sources(context.repo_root, company_name, source_contents)
    if relevant_sources:
        relevant_sources_block = "\n".join(
            f"- {context.repo_root / 'sources' / name}" for name in relevant_sources
        )
        sources_instruction = (
            f"Read ONLY these source files (absolute paths listed below). "
            f"Do not open any other file in sources/. "
            f"If the company cannot be adequately sourced from these files, say so explicitly.\n"
            f"{relevant_sources_block}"
        )
    else:
        sources_instruction = (
            "No company-specific source files were matched. "
            "Do not scan sources/. Write the profile from the inventory entry alone and "
            "label all claims low confidence with source: public document — portfolio inventory."
        )

    return f"""Profile {company_name} as a portfolio company of {context.firm_name}.

Required inputs:
- company_name: {company_name}
- firm_name: {context.firm_name}
- research_date: {research_date}

Inventory entry:
{inventory_entry}

Inventory conflicts:
{conflicts}

Source files to read:
{sources_instruction}

Constraints:
- Do not browse the web.
- Do not read any repo files other than your skill contract and the source files listed above.
- Return only the one-paragraph profile required by the skill contract (60-120 words).
"""


def build_portfolio_section(
    portco_outputs: dict[str, str],
    financial_outputs: dict[str, str],
    ordered_companies: list[str],
) -> str:
    parts = ["## Portfolio", ""]
    for company in ordered_companies:
        profile = portco_outputs.get(company)
        if not profile:
            continue
        parts.extend([f"### {company}", "", profile.strip()])
        estimate = financial_outputs.get(company, "").strip()
        if estimate:
            parts.extend(["", estimate])
        parts.append("")

    parts.extend([
        "**Sources:**",
        "- Sources listed in inline labels within each company profile and estimate.",
        "",
    ])
    return "\n".join(parts).strip() + "\n"



def extract_audit_result(audit_text: str) -> str | None:
    match = re.search(r"^## Result\s*\n\s*(PASS|FAIL)\b", audit_text, flags=re.MULTILINE)
    if match:
        return match.group(1)
    fallback = re.search(r"\b(PASS|FAIL)\b", audit_text)
    if fallback:
        return fallback.group(1)
    return None


def append_log(log_path: Path, message: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{timestamp}] {message}"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line)


def infer_research_date(run_dir: Path, fallback: str) -> str:
    log_path = run_dir / "run-log.md"
    if log_path.exists():
        match = re.search(
            r"^Started:\s*(\d{4}-\d{2}-\d{2})T",
            log_path.read_text(encoding="utf-8", errors="ignore"),
            flags=re.MULTILINE,
        )
        if match:
            return match.group(1)

    return fallback


def build_shared_user_context(
    repo_root: Path, firm_name: str, research_date: str, source_file_names: list[str]
) -> str:
    """Full context for source-reading phases (firm-profiler, portfolio-discoverer)."""
    source_manifest = "\n".join(f"- {name}" for name in source_file_names)
    return f"""Research date: {research_date}
Firm: {firm_name}
Sources directory: {repo_root / "sources"}
Available source files:
{source_manifest}
"""


def build_minimal_context(firm_name: str, research_date: str) -> str:
    """Minimal context for phases that must not read source files (labeler, audit)."""
    return f"""Research date: {research_date}
Firm: {firm_name}
"""


def assemble_dossier_python(
    firm_name: str,
    research_date: str,
    typed_sections: dict[str, str],
    ordered_sections: list[str],
    prior_dossier_path: Path,
) -> str:
    """Assemble the final dossier in Python — no Claude call needed."""
    parts = [
        f"# {firm_name} — Firm Dossier",
        f"Research date: {research_date}",
        f"Version: v4-orchestrated",
        "",
    ]
    for section_name in ordered_sections:
        content = typed_sections.get(section_name, "").strip()
        if content:
            parts.extend([content, "", "---", ""])

    if prior_dossier_path.exists():
        parts.extend([
            "## Changes from Prior Run",
            "",
            f"Prior dossier: {prior_dossier_path.name}. "
            "Review key figures (AUM, headcount, portfolio count, leadership) against prior run manually.",
            "",
        ])

    return "\n".join(parts).strip() + "\n"


def require_file(path: Path, description: str) -> Path:
    if not path.exists():
        raise RuntimeError(f"Required {description} missing: {path}")
    return path


def run_phase_9_and_10(
    context: RunContext,
    prompts: dict[str, str],
    research_date: str,
    typed_dir: Path,
    assembled_dir: Path,
    log_path: Path,
    *,
    start_phase: int = 9,
) -> int:
    repo_root = context.repo_root
    prior_dossier_path = context.dossiers_dir / "v3-skillified.md"
    assembled_draft_path = assembled_dir / "v4-orchestrated-draft.md"

    typed_paths = {
        "Firm Overview": require_file(
            typed_dir / section_filename("Firm Overview"), "typed Firm Overview section"
        ),
        "Investment Strategy": require_file(
            typed_dir / section_filename("Investment Strategy"),
            "typed Investment Strategy section",
        ),
        "Leadership": require_file(
            typed_dir / section_filename("Leadership"), "typed Leadership section"
        ),
        "Portfolio": require_file(
            typed_dir / section_filename("Portfolio"), "typed Portfolio section"
        ),
        "Recent Activity": require_file(
            typed_dir / section_filename("Recent Activity"), "typed Recent Activity section"
        ),
    }

    if start_phase <= 9:
        append_log(log_path, "Phase 9 start — assembling dossier in Python.")
        # Assembly is pure concatenation of sections we already have in typed_dir.
        # No Claude call needed — eliminates re-reading 5 files we just wrote.
        typed_section_texts = {
            name: path.read_text(encoding="utf-8")
            for name, path in typed_paths.items()
        }
        ordered_sections = FIRM_SECTION_NAMES + ["Portfolio", "Recent Activity"]
        assembled_output = assemble_dossier_python(
            context.firm_name, research_date, typed_section_texts,
            ordered_sections, prior_dossier_path,
        )
        assembled_draft_path.write_text(assembled_output, encoding="utf-8")
        append_log(log_path, f"Assembly complete: {assembled_draft_path}")
    else:
        assembled_output = require_file(
            assembled_draft_path, "assembled draft dossier for Phase 10 resume"
        ).read_text(encoding="utf-8")

    append_log(log_path, "Phase 10 start — audit-pass.")
    audit_skill = load_skill(repo_root, "audit-pass")
    # The audit skill contract defines all 13 blocking checks explicitly. The confidence
    # and source rubric worked examples are calibration aids for labelers, not auditors.
    # research-agent.md covers the authoritative definitions the audit needs.
    audit_system_prompt = make_system_prompt(
        prompts,
        audit_skill,
        include_research_agent=True,
        include_confidence_rubric=False,
        include_source_rubric=False,
    )
    audit_output = run_claude(
        audit_system_prompt,
        f"""{build_minimal_context(context.firm_name, research_date)}
dossier_path: {assembled_draft_path}
run_log_path: {log_path}

Read the dossier at `dossier_path` and return the audit report only.
Do not open any files in sources/ or prompts/.
""",
        repo_root,
        AUDIT_TIMEOUT,
    )
    audit_report_path = context.run_dir / "audit-report.md"
    audit_report_path.write_text(audit_output, encoding="utf-8")
    append_log(log_path, f"audit-pass complete: {audit_report_path}")

    audit_result = extract_audit_result(audit_output)
    dossier_path = context.dossiers_dir / "v4-orchestrated.md"
    if audit_result != "PASS":
        append_log(log_path, "Audit did not return PASS. Final dossier not written to dossiers/.")
        return 1

    dossier_path.write_text(assembled_output, encoding="utf-8")
    append_log(log_path, f"v4 dossier written to {dossier_path}")
    append_log(log_path, "Run complete.")
    return 0


# ---------------------------------------------------------------------------
# Live orchestration
# ---------------------------------------------------------------------------

def run_live(context: RunContext) -> int:
    repo_root = context.repo_root
    run_dir = context.run_dir
    log_path = run_dir / "run-log.md"
    research_date = infer_research_date(run_dir, datetime.now().strftime("%Y-%m-%d"))
    typed_dir = run_dir / "typed"
    portco_profiles_dir = run_dir / "portco-profiles"
    assembled_dir = run_dir / "assembled"

    for directory in (typed_dir, portco_profiles_dir, assembled_dir):
        directory.mkdir(parents=True, exist_ok=True)

    if context.resume_run:
        if not run_dir.exists():
            raise RuntimeError(f"Resume run directory does not exist: {run_dir}")
        if not log_path.exists():
            raise RuntimeError(f"Resume run log missing: {log_path}")
        append_log(
            log_path,
            f"Resume requested — starting from Phase {context.from_phase} using existing artifacts.",
        )
    else:
        log_path.write_text(
            f"# Run Log\n\nFirm: {context.firm_name}\nStarted: {datetime.now().isoformat()}\n\n",
            encoding="utf-8",
        )

    # Load shared inputs
    source_count, source_file_names = load_sources(repo_root)
    if source_count == 0:
        print("No source files found in sources/.", file=sys.stderr)
        return 1
    append_log(log_path, f"Loaded {source_count} source files.")
    append_log(
        log_path,
        "Concurrency limits — "
        f"portco-workers={context.max_portco_workers}, "
        f"financial-workers={context.max_financial_workers}.",
    )

    prompts = load_prompts(repo_root)
    shared_user_context = build_shared_user_context(
        repo_root, context.firm_name, research_date, source_file_names
    )

    if context.resume_run:
        if context.from_phase is None:
            raise RuntimeError("--from-phase is required when using --resume-run.")
        return run_phase_9_and_10(
            context,
            prompts,
            research_date,
            typed_dir,
            assembled_dir,
            log_path,
            start_phase=context.from_phase,
        )

    # ------------------------------------------------------------------
    # Phase 1: firm-profiler + portfolio-discoverer in parallel
    # ------------------------------------------------------------------
    append_log(log_path, "Phase 1 start — firm-profiler and portfolio-discoverer in parallel.")

    firm_profiler_skill = load_skill(repo_root, "firm-profiler")
    portfolio_discoverer_skill = load_skill(repo_root, "portfolio-discoverer")
    firm_system_prompt = make_system_prompt(
        prompts,
        firm_profiler_skill,
        include_confidence_rubric=False,
        include_source_rubric=False,
    )
    # portfolio-discoverer only needs to produce an inventory table — no confidence labels,
    # no dossier schema, no refusal-behavior rules. research-agent.md is unnecessary here.
    portfolio_system_prompt = make_system_prompt(
        prompts,
        portfolio_discoverer_skill,
        include_research_agent=False,
        include_confidence_rubric=False,
        include_source_rubric=False,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        firm_future = executor.submit(
            run_claude,
            firm_system_prompt,
            shared_user_context
            + "\n\nUse local files in `sources/` only; do not browse the web.\n"
            + "Produce exactly these four sections, in order: Firm Overview, Investment Strategy, Leadership, Recent Activity.\n"
            + "Output markdown H2 headings for each section and include a Sources subsection per section.\n"
            + "For Recent Activity: cover platform investments, exits, and firm-level events in the past 24 months. "
            + "If you state deal pace or frequency, show the underlying date math. "
            + "Do not use unsupported comparative descriptors.\n",
            repo_root,
        )
        portfolio_future = executor.submit(
            run_claude,
            portfolio_system_prompt,
            shared_user_context
            + "\n\nUse local files in `sources/` only; do not browse the web.\n"
            + "Produce the structured portfolio inventory only. This output will drive one"
            + " portco-profiler subagent per portfolio company.\n",
            repo_root,
        )
        firm_profile_output = firm_future.result()
        portfolio_inventory_output = portfolio_future.result()

    (run_dir / "firm-profiler-output.md").write_text(firm_profile_output, encoding="utf-8")
    (run_dir / "portfolio-inventory.md").write_text(portfolio_inventory_output, encoding="utf-8")
    append_log(log_path, "Phase 1 complete.")

    firm_sections = parse_h2_sections(firm_profile_output)
    draft_sections: dict[str, str] = {
        name: ensure_section_has_sources(require_section(firm_sections, name))
        for name in FIRM_SECTION_NAMES
    }

    # ------------------------------------------------------------------
    # Phase 2: extract portfolio company list
    # ------------------------------------------------------------------
    append_log(log_path, "Extracting portfolio company list from inventory.")
    companies = extract_portfolio_companies(
        portfolio_inventory_output, repo_root, context.max_portcos
    )
    if not companies:
        append_log(log_path, "No portfolio companies found. Halting.")
        return 1
    append_log(log_path, f"Found {len(companies)} companies: {', '.join(companies)}")

    # ------------------------------------------------------------------
    # Phase 3: portco-profiler fan-out in parallel
    # ------------------------------------------------------------------
    append_log(
        log_path,
        f"Phase 3 start — portco-profiler subagent for {len(companies)} companies in parallel.",
    )

    # Pre-load source file contents once so build_portco_user_message doesn't re-read
    # all source files for every company (would be 20 × 22 = 440 disk reads otherwise).
    cached_source_contents = load_source_contents(repo_root)
    portco_outputs: dict[str, str] = {}

    with ThreadPoolExecutor(max_workers=min(len(companies), context.max_portco_workers)) as executor:
        futures = {
            executor.submit(
                run_portco_subagent,
                company,
                context.firm_name,
                repo_root,
                build_portco_user_message(
                    context,
                    research_date,
                    portfolio_inventory_output,
                    company,
                    cached_source_contents,
                ),
            ): company
            for company in companies
        }
        for future in as_completed(futures):
            company = futures[future]
            try:
                _, output = future.result()
                slug = slugify(company, max_length=40)
                (portco_profiles_dir / f"{slug}.md").write_text(output, encoding="utf-8")
                portco_outputs[company] = output
                append_log(log_path, f"portco-profiler complete: {company}")
            except Exception as exc:
                append_log(log_path, f"portco-profiler FAILED for {company}: {exc}")

    append_log(log_path, "Phase 3 complete.")
    if not portco_outputs:
        append_log(log_path, "No portco-profiler outputs succeeded. Halting.")
        return 1

    # ------------------------------------------------------------------
    # Phase 4: private-data-approximator per portfolio company
    # ------------------------------------------------------------------
    financial_outputs: dict[str, str] = {}
    if context.skip_financials:
        append_log(log_path, "Phase 4 skipped — private-data-approximator disabled by flag.")
    else:
        append_log(log_path, "Phase 4 start — private-data-approximator per portfolio company.")
        private_data_skill = load_skill(repo_root, "private-data-approximator")
        private_data_system_prompt = make_system_prompt(
            prompts,
            private_data_skill,
            include_confidence_rubric=False,
            include_source_rubric=False,
        )

        with ThreadPoolExecutor(
            max_workers=min(len(portco_outputs), context.max_financial_workers)
        ) as executor:
            futures = {}
            for company in companies:
                profile = portco_outputs.get(company)
                if not profile:
                    continue
                inventory_entry = extract_inventory_entry(portfolio_inventory_output, company)
                user_message = f"""{build_minimal_context(context.firm_name, research_date)}
Company name: {company}
Inventory entry:
{inventory_entry}

Portco profile:
{profile}

If there are fewer than two observable signals, say that revenue and EBITDA are not estimable
from public sources and do not fabricate numbers. Otherwise produce the structured estimate only.
Do not read any files in sources/.
"""
                futures[executor.submit(
                    run_claude, private_data_system_prompt, user_message, repo_root, 300
                )] = company

            for future in as_completed(futures):
                company = futures[future]
                try:
                    output = future.result()
                    financial_outputs[company] = output
                    (portco_profiles_dir / f"{slugify(company, 40)}-financials.md").write_text(
                        output,
                        encoding="utf-8",
                    )
                    append_log(log_path, f"private-data-approximator complete: {company}")
                except Exception as exc:
                    append_log(log_path, f"private-data-approximator FAILED for {company}: {exc}")

        append_log(log_path, "Phase 4 complete.")

    # ------------------------------------------------------------------
    # Phase 5: Recent Activity — produced by firm-profiler in Phase 1
    # ------------------------------------------------------------------
    # firm-profiler is now asked to produce Recent Activity as its fourth section, so no
    # separate Claude call is needed here. The fallback is kept only as a safety net.
    append_log(log_path, "Phase 5 start — extracting Recent Activity from firm-profiler output.")
    recent_activity_section = find_section(firm_sections, "Recent Activity")
    if recent_activity_section is None:
        append_log(log_path, "WARNING: firm-profiler did not produce Recent Activity. Running fallback.")
        fallback_system_prompt = make_system_prompt(
            prompts,
            load_skill(repo_root, "firm-profiler"),
            include_confidence_rubric=False,
            include_source_rubric=False,
        )
        recent_activity_section = run_claude(
            fallback_system_prompt,
            shared_user_context
            + f"\n\nProduce only a markdown section `## Recent Activity` for {context.firm_name}. "
            + "Use local files in `sources/` only. Show date math for any deal pace claims. "
            + "No comparative descriptors without a benchmark. End with `**Sources:**`.\n",
            repo_root,
        )
    draft_sections["Recent Activity"] = ensure_section_has_sources(recent_activity_section)
    (run_dir / "recent-activity-output.md").write_text(
        draft_sections["Recent Activity"],
        encoding="utf-8",
    )
    append_log(log_path, "Phase 5 complete.")

    # ------------------------------------------------------------------
    # Phase 6: Build portfolio section draft
    # ------------------------------------------------------------------
    draft_sections["Portfolio"] = build_portfolio_section(portco_outputs, financial_outputs, companies)
    (run_dir / "portfolio-section-draft.md").write_text(
        draft_sections["Portfolio"],
        encoding="utf-8",
    )
    append_log(log_path, "Phase 6 complete — portfolio section assembled from company outputs.")

    # ------------------------------------------------------------------
    # Phase 7: combined claim-labeler (confidence scoring + source typing in one pass)
    # Replaces the former separate Phase 7 (confidence-scorer) and Phase 8 (source-typer).
    # One model call per section instead of two, cutting labeling cost by ~50%.
    # ------------------------------------------------------------------
    append_log(log_path, "Phase 7 start — claim-labeler (confidence + source type) on each section.")
    confidence_skill = load_skill(repo_root, "confidence-scorer")
    source_typer_skill = load_skill(repo_root, "source-typer")
    combined_skill_contract = (
        "## Confidence-Scorer Contract\n\n"
        + confidence_skill
        + "\n\n## Source-Typer Contract\n\n"
        + source_typer_skill
    )
    # Rubric files are excluded from the labeling system prompt — the skill contracts already
    # contain the scoring rules. The rubric worked examples are calibration aids, not needed
    # per-call. This saves ~15-20k tokens per labeling call.
    labeler_system_prompt = make_system_prompt(
        prompts,
        combined_skill_contract,
        include_research_agent=False,
        include_confidence_rubric=False,
        include_source_rubric=False,
    )
    ordered_sections = FIRM_SECTION_NAMES + ["Portfolio", "Recent Activity"]
    # Portfolio is excluded from the labeling loop — portco-profiler already applies inline
    # labels to each company profile. Re-labeling 20 profiles in one call is redundant and
    # produces the most expensive single context in the pipeline.
    sections_to_label = FIRM_SECTION_NAMES + ["Recent Activity"]

    typed_sections: dict[str, str] = {}
    for section_name in sections_to_label:
        labeled_output = run_claude(
            labeler_system_prompt,
            f"""{build_minimal_context(context.firm_name, research_date)}
section_name: {section_name}

Perform a combined labeling pass applying both confidence scoring and source typing to every
materially load-bearing claim in this section. Materially load-bearing claims are: numeric
figures, dates, current named roles/status, sector/strategy statements, portfolio company
status or deal dates, and deal pace/frequency claims. Do not label every transitional or
explanatory sentence.

Return only the clean annotated markdown section for `{section_name}`.
Preserve the exact H2 heading `## {section_name}`.
Do not return a summary, review memo, or scoring report.
Do not emit [CORRECTED], [ADDED], [SOURCE-TYPER NOTE], [ACCURACY NOTE], or any similar
reviewer markers in the output. Output only readable prose with standard inline labels.
Do not open any files in sources/ or prompts/.

draft_section:
{draft_sections[section_name]}
""",
            repo_root,
        )
        section_text = ensure_section_has_sources(
            normalize_section_output(section_name, labeled_output)
        )
        typed_sections[section_name] = section_text
        (typed_dir / section_filename(section_name)).write_text(
            section_text, encoding="utf-8"
        )

    # Portfolio passes through directly — portco-profiler already labeled each company entry.
    portfolio_text = draft_sections["Portfolio"]
    typed_sections["Portfolio"] = portfolio_text
    (typed_dir / section_filename("Portfolio")).write_text(portfolio_text, encoding="utf-8")

    append_log(log_path, "Phase 7 complete.")

    return run_phase_9_and_10(
        context,
        prompts,
        research_date,
        typed_dir,
        assembled_dir,
        log_path,
        start_phase=9,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_args()
    context = build_run_context(args)

    if args.max_portcos < 1:
        print("--max-portcos must be at least 1.", file=sys.stderr)
        return 1
    if args.portco_workers < 1:
        print("--portco-workers must be at least 1.", file=sys.stderr)
        return 1
    if args.financial_workers < 1:
        print("--financial-workers must be at least 1.", file=sys.stderr)
        return 1
    if args.resume_run and args.dry_run:
        print("--dry-run cannot be combined with --resume-run.", file=sys.stderr)
        return 1
    if args.from_phase and not args.resume_run:
        print("--from-phase requires --resume-run.", file=sys.stderr)
        return 1

    if context.dry_run:
        portfolio_companies = get_dry_run_portcos(context.max_portcos)
        plan_text = render_run_plan(context, portfolio_companies)
        plan_path = write_run_plan(context, plan_text)
        print_run_plan(plan_text)
        print(f"Run plan written to: {plan_path}")
        print(f"Run directory: {context.run_dir}")
        return 0

    return run_live(context)


if __name__ == "__main__":
    raise SystemExit(main())
