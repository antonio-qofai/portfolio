# /// script
# requires-python = ">=3.11"
# ///
"""Publish a sanitized snapshot of a local project into this portfolio repo.

For each project: export the committed HEAD of the source repo (never the
working tree), drop excluded files, apply overlays and block replacements,
apply redactions, then leak-scan the result. Only a clean scan is copied into
the portfolio folder, committed, and pushed. Any finding stops the sync for
that project and nothing is published.

Two config files:
  tools/rules.toml                      public: excludes, overlays, generic rules
  ~/.config/portfolio-sync/private.toml private: source paths on this machine,
                                         and redactions that name real values

Usage:
  uv run tools/sync.py internship-search     sync one project and push
  uv run tools/sync.py --all                 every project with a source here
  uv run tools/sync.py --all --check         build and scan only, write nothing
  uv run tools/sync.py --all --no-push       commit locally, do not push
  uv run tools/sync.py --install-hooks       add a post-commit hook to each source
"""

from __future__ import annotations

import argparse
import fcntl
import fnmatch
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RULES = REPO / "tools" / "rules.toml"
PRIVATE = Path.home() / ".config" / "portfolio-sync" / "private.toml"
LOCK = Path.home() / ".config" / "portfolio-sync" / ".lock"
LOG = Path.home() / "Library" / "Logs" / "portfolio-sync.log"
HOOK_MARK = "# portfolio-sync"

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")


class SyncError(Exception):
    pass


@dataclass
class Redaction:
    pattern: re.Pattern
    replace: str
    scan: bool  # re-check after redaction; a match means a leak


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


def export_head(src: Path, dest: Path) -> str:
    sha = run(["git", "rev-parse", "--short", "HEAD"], cwd=src).strip()
    data = subprocess.run(["git", "archive", "--format=tar", "HEAD"], cwd=src,
                          capture_output=True, check=True).stdout
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
    sha = export_head(src, out)
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


def publish(name: str, built: Path, sha: str, push: bool) -> bool:
    dest = REPO / name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(built, dest)
    run(["git", "add", "-A", "--", name], cwd=REPO)
    if not run(["git", "status", "--porcelain", "--", name], cwd=REPO).strip():
        print(f"{name}: already current at {sha}")
        return False
    run(["git", "commit", "-q", "-m", f"Sync {name} from {sha}", "--", name], cwd=REPO)
    print(f"{name}: committed snapshot of {sha}")
    if push and has_remote():
        run(["git", "pull", "-q", "--rebase", "--autostash"], cwd=REPO)
        run(["git", "push", "-q"], cwd=REPO)
        print(f"{name}: pushed")
    return True


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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("projects", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--check", action="store_true", help="build and scan only")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--install-hooks", action="store_true")
    args = ap.parse_args()

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

    LOCK.parent.mkdir(parents=True, exist_ok=True)
    failed = []
    with LOCK.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for name in names:
            if name not in sources:
                print(f"{name}: no source on this machine, skipped")
                continue
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    sha = build(name, sources[name], rules, private, Path(tmp))
                    if args.check:
                        print(f"{name}: clean at {sha} (check only, nothing written)")
                    else:
                        publish(name, Path(tmp), sha, push=not args.no_push)
            except SyncError as e:
                failed.append(name)
                print(e, file=sys.stderr)
                notify(f"{name}: sync blocked. See {LOG}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
