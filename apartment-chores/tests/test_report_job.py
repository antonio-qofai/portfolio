"""Tests for scripts/report_job.sh and the launchd plist that runs it.

No network and no Airtable: uv is a fake script that records its arguments,
and the DNS check is replaced with /usr/bin/true.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import report_launchd  # noqa: E402

JOB = ROOT / "scripts" / "report_job.sh"
TZ = "America/Chicago"


class ReportJobTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.out = self.dir / "chores.json"
        self.calls = self.dir / "calls"
        self.uv = self.dir / "uv"
        self.uv.write_text('#!/bin/sh\necho "$@" >> "%s"\n' % self.calls)
        self.uv.chmod(0o755)

    def run_job(self):
        env = {**os.environ, "REPORT_JOB_DNS_CHECK": "/usr/bin/true"}
        return subprocess.run(["/bin/sh", str(JOB), str(self.uv), str(self.out), TZ],
                              env=env, capture_output=True, text=True)

    def write_report(self, days_ago, status):
        when = datetime.now(ZoneInfo(TZ)) - timedelta(days=days_ago)
        self.out.write_text(json.dumps({"generated_at": when.isoformat(), "status": status, "items": []}, indent=2))

    def exported(self):
        return self.calls.exists()

    def test_skips_when_todays_report_is_ok(self):
        self.write_report(0, "ok")
        self.assertEqual(self.run_job().returncode, 0)
        self.assertFalse(self.exported())

    def test_exports_when_report_is_old(self):
        self.write_report(1, "ok")
        self.assertEqual(self.run_job().returncode, 0)
        self.assertIn("-m src.report --out %s" % self.out, self.calls.read_text())

    def test_exports_when_todays_report_is_an_error(self):
        self.write_report(0, "error")
        self.run_job()
        self.assertTrue(self.exported())

    def test_exports_when_there_is_no_report(self):
        self.run_job()
        self.assertTrue(self.exported())


class PlistTest(unittest.TestCase):
    def test_plist_runs_the_job_script_with_retries(self):
        p = report_launchd.plist("/opt/uv", "/tmp/chores.json")
        self.assertEqual(p["ProgramArguments"], ["/bin/sh", str(JOB), "/opt/uv", "/tmp/chores.json", TZ])
        self.assertEqual(p["StartCalendarInterval"], [{"Hour": 5, "Minute": 45}])
        self.assertEqual(p["StartInterval"], 1800)
        self.assertTrue(p["RunAtLoad"])


if __name__ == "__main__":
    unittest.main()
