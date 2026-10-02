#!/usr/bin/env python3 -u
"""
run_research_v3.py — QofAI Research Agent v3 (skill-decomposed)
Usage: python3 scripts/run_research_v3.py "PE Firm Name"

Pipeline:
  firm-profiler + portfolio-discoverer →
  portco-profiler (one per company) →
  private-data-approximator →
  confidence-scorer → source-typer →
  dossier-assembler → audit-pass
"""

import sys
import subprocess
from pathlib import Path

RESEARCH_DATE = "2026-06-18"
OUTPUT_FILENAME = "v3-skillified.md"
MODEL = "claude-sonnet-4-6"


def run_skill(skill_md: str, system_prefix: str, user_message: str, label: str, retries: int = 2) -> str:
    system_prompt = f"{system_prefix}\n\n## SKILL CONTRACT\n\n{skill_md}"
    cmd = [
        "claude",
        "--print",
        "--output-format", "text",
        "--model", MODEL,
        "--effort", "low",
        "--system-prompt", system_prompt,
    ]
    for attempt in range(1, retries + 2):
        result = subprocess.run(
            cmd,
            input=user_message,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode == 0:
            print(f"[{label}] done")
            return result.stdout.strip()
        print(f"[{label}] attempt {attempt} failed: {result.stderr.strip()}", file=sys.stderr)
        if attempt == retries + 1:
            print(f"[{label}] all retries exhausted", file=sys.stderr)
            sys.exit(1)
    return ""  # unreachable


def build_sources_block(sources_dir: Path) -> tuple[str, int]:
    files = sorted(f for f in sources_dir.glob("*.md") if f.name != ".gitkeep")
    if not files:
        return "", 0
    parts = [f"### {f.name}\n\n{f.read_text()}" for f in files]
    return "\n\n---\n\n".join(parts), len(files)


def main() -> None:
    if len(sys.argv) < 2:
        print('Usage: python3 scripts/run_research_v3.py "PE Firm Name"', file=sys.stderr)
        sys.exit(1)

    firm_name = sys.argv[1]
    repo = Path(__file__).parent.parent

    # Load governing documents
    prompts_dir = repo / "prompts"
    research_agent_md = (prompts_dir / "research-agent.md").read_text()
    confidence_rubric_md = (prompts_dir / "confidence-rubric.md").read_text()
    source_rubric_md = (prompts_dir / "source-rubric.md").read_text()

    # Load skill definitions
    skills_dir = repo / "skills"
    def skill(name: str) -> str:
        return (skills_dir / name / "SKILL.md").read_text()

    # Load sources
    sources_block, source_count = build_sources_block(repo / "sources")
    if source_count == 0:
        print(f"No source files found in {repo / 'sources'}", file=sys.stderr)
        sys.exit(1)
    print(f"Loaded {source_count} source files.")

    # Shared system prefix used across all skill calls
    base_system = f"""You are the QofAI Research Agent. Your governing contract:

{research_agent_md}

Confidence rubric:
{confidence_rubric_md}

Source type rubric:
{source_rubric_md}

Research date: {RESEARCH_DATE}
Firm: {firm_name}"""

    sources_section = f"## SOURCE DOCUMENTS\n\n{sources_block}"

    # -------------------------------------------------------------------------
    # Step 1a: firm-profiler — Sections 1 (Firm Overview), 2 (Investment
    #          Strategy), 3 (Leadership)
    # -------------------------------------------------------------------------
    firm_sections = run_skill(
        skill("firm-profiler"),
        base_system,
        f"""Produce Sections 1, 2, and 3 for {firm_name}.

{sources_section}

Output the three sections only, in order, with a markdown H2 heading for each.""",
        "firm-profiler",
    )

    # -------------------------------------------------------------------------
    # Step 1b: portfolio-discoverer — structured inventory of portfolio companies
    # -------------------------------------------------------------------------
    portfolio_inventory = run_skill(
        skill("portfolio-discoverer"),
        base_system,
        f"""Enumerate the complete portfolio inventory for {firm_name}.

{sources_section}

Return a markdown list where each entry has: company name, sector, status
(active/exited), acquisition date if known, and the source(s) supporting it.
One line per company. This list will be used to drive individual portco-profiler calls.""",
        "portfolio-discoverer",
    )

    # Parse company names from inventory — handles bullet lists and pipe-delimited tables
    companies = []
    for line in portfolio_inventory.splitlines():
        stripped = line.strip()
        # Bullet list: "- Company Name, sector ..."
        if stripped.startswith(("- ", "* ")):
            company = stripped[2:].split(",")[0].split("(")[0].split("|")[0].strip()
            if company:
                companies.append(company)
        # Pipe-delimited table row: "| Company Name | sector | ..."
        elif stripped.startswith("|") and not stripped.startswith("|---") and not stripped.lower().startswith("| company"):
            parts = [p.strip() for p in stripped.strip("|").split("|")]
            if parts and parts[0] and not parts[0].startswith("-"):
                companies.append(parts[0])

    if not companies:
        print("WARNING: portfolio-discoverer returned no parseable companies. "
              "Proceeding with empty portfolio.", file=sys.stderr)

    print(f"Portfolio inventory: {len(companies)} companies found.")

    # -------------------------------------------------------------------------
    # Step 2: portco-profiler — one paragraph per portfolio company
    # -------------------------------------------------------------------------
    portco_profiles = []
    for company in companies:
        profile = run_skill(
            skill("portco-profiler"),
            base_system,
            f"""Produce a one-paragraph profile for {company}, a portfolio company of {firm_name}.

{sources_section}

Company being profiled: {company}

If financial scale context is needed and no public financials exist, note that
private-data-approximator should be called separately; do not fabricate figures.""",
            f"portco-profiler:{company}",
        )
        portco_profiles.append((company, profile))

    portco_block = "\n\n".join(
        f"### {name}\n\n{profile}" for name, profile in portco_profiles
    )

    # -------------------------------------------------------------------------
    # Step 3: private-data-approximator — revenue/EBITDA bands for private cos
    # -------------------------------------------------------------------------
    financial_estimates = run_skill(
        skill("private-data-approximator"),
        base_system,
        f"""For each private portfolio company of {firm_name} listed below, produce revenue
and EBITDA band estimates from public observable signals where at least two signals exist.
Label every estimate low confidence / private inference.

Portfolio inventory:
{portfolio_inventory}

Portco profiles (for observable signals):
{portco_block}

{sources_section}

Return a markdown section titled "## Financial Estimates" with one subsection per
company that has sufficient signals. Skip companies with insufficient signals.""",
        "private-data-approximator",
    )

    # -------------------------------------------------------------------------
    # Step 4: confidence-scorer — annotate all draft content
    # -------------------------------------------------------------------------
    all_draft = f"""## Firm Sections

{firm_sections}

## Portfolio Inventory

{portfolio_inventory}

## Portco Profiles

{portco_block}

{financial_estimates}"""

    confidence_annotated = run_skill(
        skill("confidence-scorer"),
        base_system,
        f"""Apply the confidence rubric to every load-bearing claim in the draft content below.
Annotate each claim with its confidence band and a brief rationale.
Do not rewrite content; annotate only.

{all_draft}""",
        "confidence-scorer",
    )

    # -------------------------------------------------------------------------
    # Step 5: source-typer — assign source types after confidence scoring
    # -------------------------------------------------------------------------
    fully_annotated = run_skill(
        skill("source-typer"),
        base_system,
        f"""Assign source types (public document, public inference, or private inference) to
every load-bearing claim in the confidence-annotated draft below.
Do not rewrite content; add source-type annotations only.

{confidence_annotated}""",
        "source-typer",
    )

    # -------------------------------------------------------------------------
    # Step 6: dossier-assembler — merge into final structured dossier
    # -------------------------------------------------------------------------
    draft_dossier = run_skill(
        skill("dossier-assembler"),
        base_system,
        f"""Assemble the final dossier for {firm_name} from the fully annotated content below.

Required sections in order:
1. Firm Overview (200-350 words)
2. Investment Strategy (150-250 words)
3. Leadership (200-300 words)
4. Portfolio (250-400 words)
5. Recent Activity (150-250 words)

Optionally add an Audit Notes section if advisory flags apply.

Every claim must carry an inline label:
*(confidence: [high/medium/low] | source: [type] - [specific document or basis], [date])*

Format rules: no em dashes, no bold inside paragraphs, no exclamation points, active voice.
Output only the dossier markdown.

Annotated content to assemble:
{fully_annotated}""",
        "dossier-assembler",
    )

    # -------------------------------------------------------------------------
    # Step 7: audit-pass — blocking gate before writing final output
    # -------------------------------------------------------------------------
    audit_result = run_skill(
        skill("audit-pass"),
        base_system,
        f"""Self-critique the assembled dossier for {firm_name} against the governing contract
and known failure modes. Flag any critical issues that prevent finalization.

Return a structured audit report followed by a PASS or FAIL verdict. If PASS, append the
word DOSSIER_FINAL on its own line, then reproduce the complete final dossier below it.
If FAIL, list the required corrections without reproducing the dossier.

Assembled dossier to audit:
{draft_dossier}""",
        "audit-pass",
    )

    # Extract the final dossier (after DOSSIER_FINAL marker)
    if "DOSSIER_FINAL" in audit_result:
        final_dossier = audit_result.split("DOSSIER_FINAL", 1)[1].strip()
    else:
        print("audit-pass returned FAIL — corrections required. Writing audit report instead.",
              file=sys.stderr)
        final_dossier = f"# AUDIT FAILED — corrections required\n\n{audit_result}"

    output_path = repo / "dossiers" / OUTPUT_FILENAME
    output_path.write_text(final_dossier)
    print(f"Dossier written to {output_path}")


if __name__ == "__main__":
    main()
