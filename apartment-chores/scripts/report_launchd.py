"""Run the report exporter on this Mac every morning, through launchd.

    python3 scripts/report_launchd.py install --out PATH   write the plist and load it
    python3 scripts/report_launchd.py status               is it loaded, and the last lines of its log
    python3 scripts/report_launchd.py uninstall            unload and delete it

The job runs scripts/report_job.sh at 05:45 local time (launchd runs a missed
time once when the Mac wakes), at load, and every 30 minutes. That script
waits for the network, then runs `python -m src.report --out PATH` through uv,
which supplies Python 3.12 and requests, so the Mac's own python3 does not
matter. Credentials come from .env in the repo root; see README.

Once a day is deliberate: Airtable's free tier meters API calls per month
across the workspace, and the scheduler on GitHub Actions shares that budget.
Each export makes two reads. The 30-minute triggers are retries: the script
exits at once when today's report is already written.
"""

import getpass
import re
import os
import plistlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LABEL = "com.%s.chores.report" % getpass.getuser()
PLIST = Path.home() / "Library" / "LaunchAgents" / (LABEL + ".plist")
LOG = Path.home() / "Library" / "Logs" / "chores-report.log"
HOUR, MINUTE = 5, 45
RETRY_SECONDS = 1800


def policy_timezone():
    """The due policy's timezone, read as text: this script runs on the Mac's python3 (3.9)."""
    text = (ROOT / "config" / "due_policy.py").read_text()
    return re.search(r'^TIMEZONE\s*=\s*"([^"]+)"', text, re.M).group(1)


def plist(uv, out):
    return {
        "Label": LABEL,
        "WorkingDirectory": str(ROOT),
        "ProgramArguments": ["/bin/sh", str(ROOT / "scripts" / "report_job.sh"), uv, str(out), policy_timezone()],
        "StartCalendarInterval": [{"Hour": HOUR, "Minute": MINUTE}],
        "StartInterval": RETRY_SECONDS,
        "RunAtLoad": True,
        "EnvironmentVariables": {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
        "StandardOutPath": str(LOG),
        "StandardErrorPath": str(LOG),
    }


def _launchctl(*args, check=True):
    return subprocess.run(["launchctl"] + list(args), capture_output=True, text=True, check=check)


def install(out):
    uv = shutil.which("uv")
    if not uv:
        sys.exit("uv not found on PATH. Install it first: https://docs.astral.sh/uv/")
    if not (ROOT / ".env").exists():
        sys.exit("no .env in %s. Create it first (see README, Report exporter)." % ROOT)
    out = Path(out).expanduser().resolve()
    domain = "gui/%d" % os.getuid()
    _launchctl("bootout", "%s/%s" % (domain, LABEL), check=False)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(PLIST, "wb") as f:
        plistlib.dump(plist(uv, out), f)
    # bootout finishes asynchronously; bootstrap fails (error 5) until it has.
    for _ in range(10):
        if _launchctl("bootstrap", domain, str(PLIST), check=False).returncode == 0:
            break
        time.sleep(1)
    else:
        _launchctl("bootstrap", domain, str(PLIST))
    print("installed %s, writing %s daily at %02d:%02d" % (PLIST, out, HOUR, MINUTE))


def uninstall():
    _launchctl("bootout", "gui/%d/%s" % (os.getuid(), LABEL), check=False)
    if PLIST.exists():
        PLIST.unlink()
    print("removed %s" % LABEL)


def status():
    loaded = _launchctl("print", "gui/%d/%s" % (os.getuid(), LABEL), check=False).returncode == 0
    print("%s: %s" % (LABEL, "loaded" if loaded else "not loaded"))
    if LOG.exists():
        print("last log lines (%s):" % LOG)
        for line in LOG.read_text().splitlines()[-5:]:
            print("  " + line)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["install"] and len(args) == 3 and args[1] == "--out":
        install(args[2])
    elif args == ["uninstall"]:
        uninstall()
    elif args == ["status"]:
        status()
    else:
        sys.exit("usage: %s install --out PATH | status | uninstall" % sys.argv[0])
