"""Why a reader came back with nothing.

The problem this solves
-----------------------
Every reader in this package has the same contract, and it is the right one:
degrade to an empty result with a warning rather than raise, because one dead
source must not end a four-source run. What it lacked was a way to say which
kind of nothing it was returning.

Before this, a token that exists but is rejected, an API that cannot be
reached, a folder that is not there, and a table that is genuinely empty all
arrived at the runner as an ok result with zero items. Only an unset token was
distinguishable, and only because the runner checked the environment before
calling. So a morning where Airtable started refusing our credentials looked
exactly like a morning where Taylor had cleared the conference table, and both
looked like a healthy run of a quiet source.

That matters more now than it did. Gap detection runs in the daily workflow
from 2026-09-17 and reddens the build on `unconfigured` and `source_failed`,
so the difference between "empty" and "broken" is the difference between a
build nobody needs to look at and one somebody does.

How it is passed
----------------
An optional out-parameter, following `assemble.gather_candidates(excluded=[])`
which already works this way in this codebase. A caller that wants the reason
constructs a `ReadStatus` and passes it; a caller that does not passes
nothing and sees the identical list it always saw. No reader's return type
changes, so `narrative/corpus_map.py` and `synthesis/assemble.py` are
untouched.

    status = ReadStatus()
    posts = read_atomizer_posts(path, status=status)
    if status.status != OK:
        ...

What the values mean
--------------------
The split that matters is whether the thing we read from is working. EMPTY and
MISSING mean it is working and has nothing for us. UNREACHABLE, REJECTED and
FAILED mean we could not find out. UNCONFIGURED means we never asked.
"""

from __future__ import annotations

from dataclasses import dataclass

# We read something.
OK = "ok"

# The source answered and has nothing. A folder that exists and is empty, a
# table with no rows, a feed with nothing inside the lookback. Not a problem.
EMPTY = "empty"

# The place we read from is not there. A dependency agent's output folder that
# does not exist yet is the common case, and on a CI runner it usually means a
# sparse checkout dropped the sibling repo rather than that the agent is idle.
MISSING = "missing"

# We could not reach the source at all: DNS, connection, timeout.
UNREACHABLE = "unreachable"

# The source answered and refused us. A token that is set and wrong, expired,
# or lacking scope. Distinct from UNCONFIGURED because the fix is different:
# somebody has to reissue a credential rather than set one.
REJECTED = "rejected"

# We never asked, because we have no credential to ask with.
UNCONFIGURED = "unconfigured"

# Something else went wrong. Kept as a catch-all so a reader never has to
# choose between a wrong specific status and raising.
FAILED = "failed"

STATUSES = (OK, EMPTY, MISSING, UNREACHABLE, REJECTED, UNCONFIGURED, FAILED)

# The statuses that mean we could not find out what the source holds, as
# opposed to finding out that it holds nothing. Gap detection and the daily
# workflow key on this distinction rather than on item counts.
BROKEN = (UNREACHABLE, REJECTED, UNCONFIGURED, FAILED)


@dataclass
class ReadStatus:
    """A mutable box a reader fills in with why it returned what it did.

    Starts as OK so a reader that simply succeeds does not have to say so, and
    a caller that forgets to check reads the same thing it read before.
    """

    status: str = OK
    detail: str = ""

    def set(self, status: str, detail: str = "") -> None:
        self.status = status
        self.detail = " ".join(str(detail).split())

    @property
    def ok(self) -> bool:
        return self.status == OK

    @property
    def broken(self) -> bool:
        """True when we could not find out, rather than found out it is empty."""
        return self.status in BROKEN

    def __str__(self) -> str:
        return f"{self.status}: {self.detail}" if self.detail else self.status


def fill(status: ReadStatus | None, value: str, detail: str = "") -> None:
    """Sets `status` when a caller supplied one, and does nothing when not.

    A helper so no reader has to repeat `if status is not None:` at every exit,
    which is the shape of the bug where one branch forgets to report.
    """
    if status is not None:
        status.set(value, detail)
