"""Drive a deck run from a test the way a browser drives one.

A run stopped owning its request on 2026-08-26. `POST /run` starts the pipeline
on a thread and answers immediately with a run id, the page polls
`/run-status`, and the deck is collected from `GET /?tab=result&run=<id>`. That
is what fixed the portal cutting off every render's response at ~300 seconds,
and it means a test can no longer read the outcome off the POST.

`run_deck` does the three steps in one call, so a test asserts on the finished
Result panel exactly as it did before. Keeping the wait here rather than in each
test is deliberate: the polling loop is the part that would rot into a `sleep`
scattered across eleven files.
"""

import re
import time

# What `render_studio` stamps on the body when a run is in flight.
_RUN_ID = re.compile(r'data-run-id="([^"]+)"')

# Generous, because it is a failure ceiling and not a delay: the loop returns the
# moment the run lands. Real pipeline runs in the suite are sub-second (no render
# leg), so this only ever fires on a genuinely stuck run, where a hang is far
# worse to debug than a named assertion.
DEFAULT_TIMEOUT_S = 30.0


def start_run(client, data=None):
    """POST /run and return ``(response, run_id)``.

    ``run_id`` is empty when no run started, which is the correct outcome for the
    refusals the route still answers synchronously (a packet that is not there, a
    live run with no opportunity picked, an unconfigured data source). Those spend
    nothing and have nothing to poll.
    """
    response = client.post("/run", data=data or {})
    found = _RUN_ID.search(response.get_data(as_text=True))
    return response, (found.group(1) if found else "")


def run_deck(client, data=None, timeout=DEFAULT_TIMEOUT_S):
    """POST /run, wait for the run to land, and return the Result-tab response.

    A refusal caught before the run starts is returned as it came back, since
    there is no run behind it and its own page is the outcome under test.
    """
    response, run_id = start_run(client, data)
    if not run_id:
        return response
    deadline = time.monotonic() + timeout
    while True:
        state = client.get(f"/run-status?id={run_id}").get_json() or {}
        if state.get("state") == "done":
            return client.get(f"/?tab=result&run={run_id}")
        if state.get("state") == "unknown":
            raise AssertionError(
                f"run {run_id} vanished from the registry before it finished"
            )
        if time.monotonic() > deadline:
            raise AssertionError(f"run {run_id} did not finish within {timeout}s")
        # Short enough that a sub-second test run is not padded by the wait,
        # long enough not to spin a core while the worker thread holds the GIL.
        time.sleep(0.005)


def wait_for_run(client, run_id, timeout=DEFAULT_TIMEOUT_S):
    """Block until ``run_id`` reports done, for a test that started it itself."""
    deadline = time.monotonic() + timeout
    while time.monotonic() <= deadline:
        state = client.get(f"/run-status?id={run_id}").get_json() or {}
        if state.get("state") == "done":
            return True
        if state.get("state") == "unknown":
            return False
        time.sleep(0.005)
    raise AssertionError(f"run {run_id} did not finish within {timeout}s")
