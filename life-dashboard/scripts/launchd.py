"""Install, remove, or inspect the launchd jobs for this project.

    uv run scripts/launchd.py install     write plists to ~/Library/LaunchAgents and load them
    uv run scripts/launchd.py uninstall   unload and delete them
    uv run scripts/launchd.py status      show whether each job is loaded

Two jobs:
  build   `run.py --catch-up` at the scheduled time (launchd fires a missed
          calendar run on wake), at login, and every 30 minutes as a safety net.
          --catch-up skips when today's brief already exists, so extra
          triggers are cheap.
  serve   `run.py --serve --no-build`, kept alive, localhost only.

Waking the Mac itself needs a one-time `sudo pmset repeat ...` (see README).
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard.config import ROOT, load_config  # noqa: E402

PREFIX = "com.user.life-dashboard"
AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
PYTHON = ROOT / ".venv" / "bin" / "python"
LOG_DIR = ROOT / "data" / "logs"


def plists(config: dict) -> dict[str, dict]:
    base = {
        "WorkingDirectory": str(ROOT),
        "EnvironmentVariables": {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
    }
    sched = config["schedule"]
    return {
        f"{PREFIX}.build": {
            **base,
            "Label": f"{PREFIX}.build",
            "ProgramArguments": [str(PYTHON), "run.py", "--catch-up"],
            "StartCalendarInterval": [{"Hour": sched["hour"], "Minute": sched["minute"]}],
            "StartInterval": 1800,
            "RunAtLoad": True,
            "StandardOutPath": str(LOG_DIR / "build.log"),
            "StandardErrorPath": str(LOG_DIR / "build.log"),
        },
        f"{PREFIX}.serve": {
            **base,
            "Label": f"{PREFIX}.serve",
            "ProgramArguments": [str(PYTHON), "run.py", "--serve", "--no-build"],
            "RunAtLoad": True,
            "KeepAlive": True,
            "StandardOutPath": str(LOG_DIR / "serve.log"),
            "StandardErrorPath": str(LOG_DIR / "serve.log"),
        },
    }


def _launchctl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, check=check)


def install() -> None:
    if not PYTHON.exists():
        sys.exit(f"{PYTHON} not found. Run `uv sync` first.")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    for label, plist in plists(load_config()).items():
        path = AGENTS_DIR / f"{label}.plist"
        _launchctl("bootout", f"{domain}/{label}", check=False)  # reload if already installed
        with open(path, "wb") as f:
            plistlib.dump(plist, f)
        # bootout finishes asynchronously; bootstrap fails (error 5) until it has.
        for _ in range(10):
            if _launchctl("bootstrap", domain, str(path), check=False).returncode == 0:
                break
            time.sleep(1)
        else:
            _launchctl("bootstrap", domain, str(path))  # raise with launchctl's error
        print(f"installed {path}")


def uninstall() -> None:
    domain = f"gui/{os.getuid()}"
    for label in plists(load_config()):
        _launchctl("bootout", f"{domain}/{label}", check=False)
        path = AGENTS_DIR / f"{label}.plist"
        if path.exists():
            path.unlink()
            print(f"removed {path}")


def status() -> None:
    domain = f"gui/{os.getuid()}"
    for label in plists(load_config()):
        r = _launchctl("print", f"{domain}/{label}", check=False)
        if r.returncode != 0:
            print(f"{label}: not loaded")
            continue
        fields = dict(
            line.strip().split(" = ", 1) for line in r.stdout.splitlines() if " = " in line and line.startswith("\t") and not line.startswith("\t\t")
        )
        print(f"{label}: {fields.get('state', '?')}, last exit {fields.get('last exit code', '?')}")


if __name__ == "__main__":
    commands = {"install": install, "uninstall": uninstall, "status": status}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        sys.exit(f"usage: {sys.argv[0]} {{{'|'.join(commands)}}}")
    commands[sys.argv[1]]()
