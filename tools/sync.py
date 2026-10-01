# /// script
# requires-python = ">=3.11"
# dependencies = ["anthropic"]
# ///
"""Publish a sanitized snapshot of a local project into this portfolio repo.

For each project:
  1. Export the project's publish branch (main unless rules.toml says
     otherwise) from the source repo, or only its `subdir` folder for a shared
     monorepo. Never the working tree, never a feature branch, whatever happens
     to be checked out.
  2. Drop excluded files, apply block replacements and overlays, apply
     redactions.
  3. Leak scan: secret-shaped strings, unapproved email addresses, denied text,
     and anything a redaction should have removed.
  4. Review: Claude reads exactly what changed since the last publish and flags
     personal or confidential information that no rule anticipated.
  5. Only if both gates pass, copy into the portfolio folder, rebuild the
     README project list from each project's PORTFOLIO.md, commit, and push.

Any finding at any gate stops that project and nothing is published. If the
review cannot run (no key, API down), that also stops it.

Two config files:
  tools/rules.toml                      public: excludes, overlays, generic rules
  ~/.config/portfolio-sync/private.toml private: source paths on this machine,
                                         redactions that name real values, and
                                         where to find the API key

Each run records its result in ~/.config/portfolio-sync/status.json.

Usage:
  uv run tools/sync.py internship-search     sync one project and push
  uv run tools/sync.py --all                 every project with a source here
  uv run tools/sync.py --all --check         build, scan and review; write nothing
  uv run tools/sync.py --all --no-push       commit locally, do not push
  uv run tools/sync.py NAME --approve-review publish a change the review blocked,
                                             after reading its findings. Only the
                                             exact change that was blocked is
                                             approved; anything newer is reviewed.
  uv run tools/sync.py --status              show the last result per project
  uv run tools/sync.py --install-hooks       add a post-commit hook to each source
"""

from __future__ import annotations

import argparse
import difflib
import fcntl
import fnmatch
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RULES = REPO / "tools" / "rules.toml"
CONFIG_DIR = Path.home() / ".config" / "portfolio-sync"
PRIVATE = CONFIG_DIR / "private.toml"
LOCK = CONFIG_DIR / ".lock"
STATUS = CONFIG_DIR / "status.json"
LOG = Path.home() / "Library" / "Logs" / "portfolio-sync.log"
HOOK_MARK = "# portfolio-sync"
README_START = "<!-- projects:start -->"
README_END = "<!-- projects:end -->"

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")

REVIEW_MODEL = "claude-opus-5-5"
REVIEW_CHUNK_CHARS = 250_000
REVIEW_SYSTEM = """\
You are the last check before code and documents are published to a public GitHub portfolio
that employers will read. The owner is a university student who builds AI agents for personal
use and during an internship. Everything has already been through automatic redaction. Your
job is to catch what the redaction rules did not anticipate.

You receive unified diffs between the version already published and the version about to be
published. Judge only added lines (those starting with "+"). Removed and context lines are
there so you can understand what changed.

Flag an added line if publishing it would expose any of these:
- A real private individual: a name, initials that clearly identify someone, a username, or a
  relationship detail (roommate, landlord, cleaner, family member, friend, referral contact,
  recruiter, coworker, classmate, professor).
- Contact or location details: email, phone, street address, apartment or unit, precise
  coordinates, or a neighbourhood tied to where the owner lives.
- Account identifiers: calendar IDs, base or table IDs, share links, OAuth client IDs, tokens,
  keys, webhook URLs, internal hostnames, file paths that reveal another person's name.
- The owner's private record: grades or GPA, applications and their outcomes (where they
  applied, interviews, rejections, offers), salary, health, finances, immigration status,
  family matters, or anything they would not put on a resume.
- Confidential business information: names of clients, portfolio companies, deals, or partners
  of any company the owner worked for, and internal metrics, pricing or strategy.
- Real data: rows, emails, messages, documents or calendar events copied from real accounts,
  including inside test fixtures and sample files.

Do not flag:
- Placeholders and redaction output: "the owner", "the user", Alex, Blake, Casey, Pat, Maria,
  Jordan Example, Example Capital, example.com, example.edu, .test domains, IDs made of X.
- Obviously fake test fixtures (alice@..., bob@..., a@b.com, "Roommate A").
- Public organisations named as targets or data sources, such as companies whose public job
  boards are polled, news sites, APIs, universities and course names.
- The owner's university and the internship company itself, which appear on their resume.
- Code, configuration keys, environment variable names, model names, and placeholders like
  YOUR_API_KEY.
- Opinions, design reasoning and engineering notes, even blunt ones.

Be precise. A false alarm stops publishing until the owner reads it, so flag only what a
careful person would agree is private. When you are unsure whether a name belongs to a real
private person, flag it.

Return every finding. For each, give the file path, the exact excerpt (under 120 characters),
a category, and one sentence on why it is private. Return an empty list when nothing should
be flagged."""

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "excerpt": {"type": "string"},
                    "category": {
                        "type": "string",
                        "enum": ["person", "contact_or_location", "identifier", "private_record",
                                 "confidential_business", "real_data", "other"],
                    },
                    "reason": {"type": "string"},
                },
                "required": ["file", "excerpt", "category", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["findings"],
    "additionalProperties": False,
}


class SyncError(Exception):
    pass


class ReviewBlocked(SyncError):
    def __init__(self, message: str, findings: list[dict], digest: str):
        super().__init__(message)
        self.findings = findings
        self.digest = digest


@dataclass
class Redaction:
    pattern: re.Pattern
    replace: str
    scan: bool  # re-check after redaction; a match means a leak


# ------------------------------------------------------------------ config


def load_config() -> tuple[dict, dict]:
    if not PRIVATE.exists():
        raise SyncError(f"missing {PRIVATE}; nothing is synced without it")
    with RULES.open("rb") as f:
        rules = tomllib.load(f)
    with PRIVATE.open("rb") as f:
        private = tomllib.load(f)
    return rules, private


def compile_redactions(entries: list[dict]) -> list[Redaction]:
    out = []
    for e in entries:
        flags = re.MULTILINE | (re.IGNORECASE if "i" in e.get("flags", "") else 0)
        out.append(Redaction(re.compile(e["pattern"], flags), e["replace"], e.get("scan", True)))
    return out


def run(cmd: list[str], cwd: Path | None = None) -> str:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SyncError(f"{' '.join(cmd)} failed: {r.stderr.strip()}")
    return r.stdout


# ------------------------------------------------------------------ build


def export_branch(src: Path, branch: str, dest: Path, subdir: str = "") -> str:
    """Export a branch's tip, or one folder of it. The checked-out branch and working tree are ignored."""
    ref = f"refs/heads/{branch}"
    if subprocess.run(["git", "show-ref", "--verify", "--quiet", ref], cwd=src).returncode != 0:
        raise SyncError(f"{src}: no local branch {branch!r} to publish from")
    sha = run(["git", "rev-parse", "--short", ref], cwd=src).strip()
    tree = f"{ref}:{subdir.strip('/')}" if subdir else ref
    r = subprocess.run(["git", "archive", "--format=tar", tree], cwd=src, capture_output=True)
    if r.returncode != 0:
        raise SyncError(f"{src}: cannot export {tree}: {r.stderr.decode().strip()}")
    data = r.stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(dest, filter="data")
    return sha


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def files_under(root: Path):
    for p in sorted(root.rglob("*")):
        if p.is_file() and not p.is_symlink():
            yield p


def apply_excludes(root: Path, patterns: list[str]) -> None:
    for p in list(files_under(root)):
        rel = p.relative_to(root).as_posix()
        if any(fnmatch.fnmatch(rel, pat) for pat in patterns):
            p.unlink()


def apply_blocks(root: Path, blocks: list[dict]) -> None:
    """Replace the text between two heading lines. Missing markers fail closed."""
    for b in blocks:
        target = root / b["file"]
        if not target.exists():
            continue
        text = target.read_text(encoding="utf-8")
        start = re.search(b["start"], text, re.MULTILINE)
        end = re.search(b["end"], text[start.end():], re.MULTILINE) if start else None
        if not (start and end):
            raise SyncError(f"{b['file']}: block markers {b['start']!r}..{b['end']!r} not found")
        body = (REPO / b["with"]).read_text(encoding="utf-8")
        cut = start.end() + end.start()
        target.write_text(text[:start.start()] + body.rstrip() + "\n\n" + text[cut:], encoding="utf-8")


def apply_overlays(root: Path, overlays: list[dict]) -> None:
    for o in overlays:
        dest = root / o["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / o["from"], dest)


def apply_redactions(root: Path, redactions: list[Redaction]) -> None:
    for p in files_under(root):
        text = read_text(p)
        if text is None:
            continue
        new = text
        for r in redactions:
            new = r.pattern.sub(r.replace, new)
        if new != text:
            p.write_text(new, encoding="utf-8")


def scan(root: Path, rules: dict, redactions: list[Redaction], extra_deny: list[str]) -> list[str]:
    s = rules["scan"]
    secret = [re.compile(x) for x in s["secret_patterns"]]
    deny = [re.compile(x, re.IGNORECASE | re.MULTILINE) for x in s.get("deny_patterns", []) + extra_deny]
    leftovers = [r.pattern for r in redactions if r.scan]
    allow_emails = {e.lower() for e in s.get("email_allow", [])}
    allow_domains = tuple(d.lower() for d in s.get("email_allow_domains", []))
    findings = []
    for p in files_under(root):
        rel = p.relative_to(root).as_posix()
        text = read_text(p)
        if text is None:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            where = f"{rel}:{n}"
            for pat in secret:
                if pat.search(line):
                    findings.append(f"{where}  secret-like value /{pat.pattern}/")
            for pat in deny + leftovers:
                m = pat.search(line)
                if m:
                    findings.append(f"{where}  denied text {m.group(0)!r}")
            for m in EMAIL.finditer(line):
                addr = m.group(0).lower()
                domain = addr.rsplit("@", 1)[1]
                if addr in allow_emails or any(domain == d or domain.endswith("." + d) for d in allow_domains):
                    continue
                findings.append(f"{where}  unapproved email {m.group(0)!r}")
    return findings


def build(name: str, src: Path, rules: dict, private: dict, out: Path) -> str:
    proj = rules["projects"].get(name)
    if proj is None:
        raise SyncError(f"{name}: no [projects.{name}] section in rules.toml")
    sha = export_branch(src, proj.get("branch", "main"), out, proj.get("subdir", ""))
    apply_excludes(out, rules.get("exclude_everywhere", []) + proj.get("exclude", []))
    apply_blocks(out, proj.get("block", []))
    apply_overlays(out, proj.get("overlay", []))
    redactions = compile_redactions(private.get("redact", []) + rules.get("redact", []))
    apply_redactions(out, redactions)
    findings = scan(out, rules, redactions, private.get("deny", []))
    if findings:
        shown = "\n  ".join(findings[:400])
        more = f"\n  ... and {len(findings) - 400} more" if len(findings) > 400 else ""
        raise SyncError(f"{name}: leak scan found {len(findings)} issue(s), nothing published\n  {shown}{more}")
    return sha


# ------------------------------------------------------------------ review


def diff_against_published(name: str, built: Path) -> list[str]:
    """One unified diff per text file that is new or changed since the last publish."""
    published = REPO / name
    diffs = []
    for p in files_under(built):
        rel = p.relative_to(built).as_posix()
        new = read_text(p)
        if new is None:
            continue
        old_path = published / rel
        old = (read_text(old_path) or "") if old_path.exists() else ""
        if old == new:
            continue
        lines = difflib.unified_diff(old.splitlines(), new.splitlines(),
                                     f"published/{rel}", f"new/{rel}", n=2, lineterm="")
        diffs.append("\n".join(lines))
    return diffs


def chunk(diffs: list[str], limit: int) -> list[str]:
    batches, current = [], ""
    for d in diffs:
        while len(d) > limit:  # one huge file: split on line boundaries
            cut = d.rfind("\n", 0, limit)
            cut = cut if cut > 0 else limit
            batches.append(d[:cut])
            d = d[cut:]
        if current and len(current) + len(d) > limit:
            batches.append(current)
            current = ""
        current += d + "\n\n"
    if current:
        batches.append(current)
    return batches


def api_key(private: dict) -> str:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"]
    env_file = private.get("review", {}).get("env_file")
    if env_file:
        for line in Path(env_file).expanduser().read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "ANTHROPIC_API_KEY" and value.strip():
                return value.strip().strip("'\"")
    raise SyncError("no ANTHROPIC_API_KEY found, so the review cannot run; nothing published "
                    "(set [review] env_file in private.toml)")


def review(name: str, diffs: list[str], private: dict) -> list[dict]:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key(private))
    findings = []
    for batch in chunk(diffs, REVIEW_CHUNK_CHARS):
        try:
            resp = client.beta.messages.create(
                model=REVIEW_MODEL,
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                output_config={"effort": "medium", "format": {"type": "json_schema", "schema": REVIEW_SCHEMA}},
                system=REVIEW_SYSTEM,
                messages=[{"role": "user", "content": f"Project folder: {name}\n\n{batch}"}],
            )
        except anthropic.APIError as e:
            raise SyncError(f"{name}: review request failed ({e.__class__.__name__}: {e}); nothing published")
        if resp.stop_reason != "end_turn":
            raise SyncError(f"{name}: review ended with {resp.stop_reason}; nothing published")
        text = next(b.text for b in resp.content if b.type == "text")
        findings.extend(json.loads(text)["findings"])
    return findings


# ------------------------------------------------------------------ status


def load_status() -> dict:
    try:
        return json.loads(STATUS.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_status(name: str, **fields) -> None:
    status = load_status()
    status[name] = {"at": datetime.now().astimezone().isoformat(timespec="seconds"), **fields}
    STATUS.write_text(json.dumps(status, indent=2) + "\n")


def show_status() -> None:
    for name, s in sorted(load_status().items()):
        print(f"{name}: {s['state']} at {s['at']}" + (f" ({s.get('sha')})" if s.get("sha") else ""))
        if s.get("message"):
            print(f"  {s['message'].splitlines()[0]}")
        for f in s.get("findings", []):
            print(f"  - {f['file']}: {f['excerpt']!r}  [{f['category']}] {f['reason']}")


# ------------------------------------------------------------------ publish


def rebuild_readme(rules: dict) -> bool:
    """Regenerate the README project list from each published folder's PORTFOLIO.md."""
    readme = REPO / "README.md"
    text = readme.read_text(encoding="utf-8")
    start, end = text.find(README_START), text.find(README_END)
    if start < 0 or end < start:
        return False
    order = rules.get("readme_order", [])
    folders = [d for d in REPO.iterdir() if d.is_dir() and (d / "PORTFOLIO.md").is_file()]
    folders.sort(key=lambda d: (order.index(d.name) if d.name in order else len(order), d.name))
    sections = [f"### [{d.name}]({d.name}/)\n\n{(d / 'PORTFOLIO.md').read_text(encoding='utf-8').strip()}"
                for d in folders]
    new = text[:start + len(README_START)] + "\n\n" + "\n\n".join(sections) + "\n\n" + text[end:]
    if new == text:
        return False
    readme.write_text(new, encoding="utf-8")
    return True


def publish(name: str, built: Path, sha: str, rules: dict, push: bool) -> bool:
    dest = REPO / name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(built, dest)
    rebuild_readme(rules)
    run(["git", "add", "-A", "--", name, "README.md"], cwd=REPO)
    changed = bool(run(["git", "status", "--porcelain", "--", name, "README.md"], cwd=REPO).strip())
    if changed:
        run(["git", "commit", "-q", "-m", f"Sync {name} from {sha}", "--", name, "README.md"], cwd=REPO)
        print(f"{name}: committed snapshot of {sha}")
    else:
        print(f"{name}: already current at {sha}")
    # Push whenever local is ahead, so a push that failed last time is retried.
    if push and has_remote():
        run(["git", "pull", "-q", "--rebase", "--autostash"], cwd=REPO)
        if run(["git", "rev-list", "--count", "@{u}..HEAD"], cwd=REPO).strip() != "0":
            run(["git", "push", "-q"], cwd=REPO)
            print(f"{name}: pushed")
    return changed


def has_remote() -> bool:
    return bool(run(["git", "remote"], cwd=REPO).strip())


def notify(message: str) -> None:
    if sys.platform == "darwin":
        safe = message.replace('"', "'")[:200]
        subprocess.run(["osascript", "-e", f'display notification "{safe}" with title "Portfolio sync"'],
                       capture_output=True)


def install_hooks(private: dict) -> None:
    uv = shutil.which("uv") or "/opt/homebrew/bin/uv"
    for name, src in private["sources"].items():
        hook = Path(src).expanduser() / ".git" / "hooks" / "post-commit"
        line = (f'( "{uv}" run --quiet --script "{REPO / "tools" / "sync.py"}" {name} '
                f'>> "{LOG}" 2>&1 & ) {HOOK_MARK}\n')
        existing = hook.read_text() if hook.exists() else "#!/bin/sh\n"
        if HOOK_MARK in existing:
            print(f"{name}: hook already installed")
            continue
        hook.write_text(existing.rstrip("\n") + "\n" + line)
        hook.chmod(0o755)
        print(f"{name}: hook installed at {hook}")


def sync_one(name: str, src: Path, rules: dict, private: dict, args) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        built = Path(tmp)
        sha = build(name, src, rules, private, built)
        diffs = diff_against_published(name, built)
        if not diffs:
            print(f"{name}: nothing changed since the last publish ({sha})")
            save_status(name, state="current", sha=sha)
            return
        digest = hashlib.sha256("\n".join(diffs).encode()).hexdigest()
        prior = load_status().get(name, {})
        if args.approve_review and prior.get("state") == "blocked" and prior.get("digest") == digest:
            print(f"{name}: publishing the blocked change you approved")
        else:
            if args.approve_review:
                print(f"{name}: the change differs from the one that was blocked, so it is reviewed again")
            findings = review(name, diffs, private)
            if findings:
                lines = "\n  ".join(f"{f['file']}: {f['excerpt']!r}  [{f['category']}] {f['reason']}"
                                    for f in findings)
                raise ReviewBlocked(f"{name}: review flagged {len(findings)} item(s), nothing published\n  {lines}",
                                    findings, digest)
        if args.check:
            print(f"{name}: clean at {sha} (check only, nothing written)")
            return
        publish(name, built, sha, rules, push=not args.no_push)
        save_status(name, state="published", sha=sha)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("projects", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--check", action="store_true", help="build, scan and review only")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--approve-review", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--install-hooks", action="store_true")
    args = ap.parse_args()

    if args.status:
        show_status()
        return 0
    try:
        rules, private = load_config()
    except SyncError as e:
        print(e, file=sys.stderr)
        return 2
    sources = {k: Path(v).expanduser() for k, v in private.get("sources", {}).items()}

    if args.install_hooks:
        install_hooks(private)
        return 0

    names = list(sources) if args.all else args.projects
    if not names:
        ap.error("name a project or pass --all")

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    failed = []
    with LOCK.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for name in names:
            if name not in sources:
                print(f"{name}: no source on this machine, skipped")
                continue
            try:
                sync_one(name, sources[name], rules, private, args)
            except ReviewBlocked as e:
                failed.append(name)
                print(e, file=sys.stderr)
                save_status(name, state="blocked", message=str(e), findings=e.findings, digest=e.digest)
                notify(f"{name}: review blocked publishing. Run sync.py --status")
            except SyncError as e:
                failed.append(name)
                print(e, file=sys.stderr)
                save_status(name, state="error", message=str(e))
                notify(f"{name}: sync blocked. Run sync.py --status")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
