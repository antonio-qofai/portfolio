"""Build the brief and render the page. `--serve` also serves it on localhost.

    uv run run.py                     build data/brief.json and web/index.html
    uv run run.py --serve             build, then serve web/ at http://127.0.0.1:8000
    uv run run.py --serve --no-build  serve only (the always-on launchd job)
    uv run run.py --catch-up          build only if the last scheduled run was missed
                                      (the scheduled launchd job)
    uv run run.py --digest            email today's digest if it's due and not yet sent
                                      (the digest launchd job)
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dashboard import api, digest
from dashboard.config import ROOT, load_config
from dashboard.pipeline import DATA_DIR, build_brief, last_generated_at, save_brief
from dashboard.render import render, write_page


def last_scheduled_time(config: dict, now: datetime) -> datetime:
    """The most recent daily schedule time at or before `now`."""
    sched = config["schedule"]
    today = now.replace(hour=sched["hour"], minute=sched["minute"], second=0, microsecond=0)
    return today if now >= today else today - timedelta(days=1)


def needs_run(config: dict, now: datetime, last: datetime | None) -> bool:
    return last is None or last < last_scheduled_time(config, now)


def build(config: dict, trigger: str) -> None:
    brief = build_brief(config)
    save_brief(brief)
    page = write_page(render(brief, config))
    failed = [r.name for r in brief["results"].values() if r.error]
    status = f"failed={','.join(failed)}" if failed else "ok"
    DATA_DIR.mkdir(exist_ok=True)
    with open(DATA_DIR / "runs.log", "a") as log:
        log.write(f"{brief['generated_at']}\t{trigger}\t{status}\n")
    print(f"{brief['generated_at']} wrote {page.relative_to(ROOT)} ({status})", flush=True)


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    """Always serve the latest page; the file changes every morning."""

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


class DashboardHandler(NoCacheHandler):
    """Static files from web/, plus the check-off and feedback endpoints (dashboard/api.py)."""

    allowed_hosts: set[str] = set()
    timezone = "America/Chicago"

    def _same_origin(self) -> bool:
        # Host check blocks DNS rebinding; Origin check blocks other sites posting here.
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        return host in self.allowed_hosts and (origin is None or origin == f"http://{host}")

    def _api(self, method: str) -> None:
        if not self._same_origin():
            status, payload = 403, {"error": "forbidden"}
        elif method == "POST" and self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            status, payload = 415, {"error": "expected application/json"}
        else:
            length = int(self.headers.get("Content-Length") or 0)
            if length > api.MAX_BODY:
                status, payload = 413, {"error": "too large"}
            else:
                body = self.rfile.read(length) if length else b""
                now = datetime.now(ZoneInfo(self.timezone))
                status, payload = api.handle(method, self.path, body, DATA_DIR, now)
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path.startswith("/api/"):
            self._api("GET")
        else:
            super().do_GET()

    def do_POST(self) -> None:
        self._api("POST")


def serve(config: dict) -> None:
    host, port = config["server"]["host"], config["server"]["port"]
    DashboardHandler.allowed_hosts = {f"{host}:{port}", f"localhost:{port}"}
    DashboardHandler.timezone = config.get("timezone", "America/Chicago")
    # Serve only web/, never the repo root (which holds .env and data/).
    handler = functools.partial(DashboardHandler, directory=str(ROOT / "web"))
    with http.server.ThreadingHTTPServer((host, port), handler) as httpd:
        print(f"Serving at http://{host}:{port}  (Ctrl+C to stop)", flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Life Dashboard")
    parser.add_argument("--serve", action="store_true", help="serve the page on localhost")
    parser.add_argument("--no-build", action="store_true", help="skip building (use with --serve)")
    parser.add_argument("--catch-up", action="store_true", help="build only if the last scheduled run was missed")
    parser.add_argument("--digest", action="store_true", help="email today's digest if it's due")
    args = parser.parse_args()
    config = load_config()

    if args.digest:
        # The build job makes the brief; this job only sends, so the two never build twice.
        now = datetime.now(ZoneInfo(config.get("timezone", "America/Chicago")))
        current = not needs_run(config, now, last_generated_at())
        print(f"{now.isoformat(timespec='seconds')} digest: {digest.run(config, now, DATA_DIR, current)}", flush=True)
        return

    if args.catch_up:
        now = datetime.now(ZoneInfo(config.get("timezone", "America/Chicago")))
        if needs_run(config, now, last_generated_at()):
            build(config, trigger="scheduled")
        else:
            print(f"{now.isoformat(timespec='seconds')} brief is current, skipping", flush=True)
    elif not args.no_build:
        build(config, trigger="manual")

    if args.serve:
        serve(config)


if __name__ == "__main__":
    main()
