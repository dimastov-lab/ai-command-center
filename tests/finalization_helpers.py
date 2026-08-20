"""One authority for "this run is finished", for tests that launch real runs.

VOYN-W0-AICC-FLAKE-03, the item its fix recorded rather than did.

`Supervisor._supervise` commits the terminal run row first and only afterwards
appends `process_exited`, auto-commits whatever the agent left uncommitted,
saves the report and stamps `run.finalized_at` — all on a **daemon** thread that
interpreter shutdown does not join. For the width of that window a run reads
COMPLETED while its work is uncommitted and its report does not exist. The
product side of that defect is closed: `scripts/execution_center_debug.py` and
`scripts/relaunch-task.py` both wait for finalization before returning, and
`run.finalized_at` (VOYN-W0-AICC-SRV-09-FINALIZED-AT) made the same question
answerable from another process.

The test side was not. Five modules had each grown their own answer to it —
two `_wait_for_run_terminal`s and three `_wait_for_report`s, none importing the
others, one of them documenting the hazard in twenty lines and the next
restating it in two. That is the duplicate authority the fix's own commit
recorded as outstanding, and it is weaker than the marker in two ways that
matter:

- **The report is not the last write.** `finalized_at` is, and the whole point
  of a wait in a test is that the daemon thread is done touching the database
  before teardown swaps `REPORTS_ROOT` or the db path out from under it.
- **Not every terminal run produces a report.** `INTERRUPTED` and `UNKNOWN`
  don't, nor does the reconcile path, nor does a run whose report write itself
  failed — all of which `finalized_at` marks and a report poll waits out to a
  timeout, reporting "did not finish" about a run that finished.

So the predicate is the durable marker, read the one way every process can read
it. `Supervisor.wait_for_run` answers a strictly stronger question — it also
waits for ownership release — but only for the process that owns the run, which
the Streamlit `AppTest` callers deliberately are not (constructing a second
`Supervisor` there gets a second, empty `_active` registry whose `reconcile()`
races the real one). Where a test does hold the owning API, calling
`api.supervisor.wait_for_run` first and then this is the belt-and-braces order;
this is the part that must always be there.
"""

from __future__ import annotations

import time

from command_center.runtime import db as runtime_db

__all__ = ["wait_for_finalized_run"]

#: Poll cadence. The window this waits out is milliseconds wide on a clean tree
#: and ~150 ms once the auto-commit has a real `git commit` to make, so 50 ms
#: costs nothing and keeps a failure's diagnosis close to the moment it happened.
_POLL_SECONDS = 0.05


def wait_for_finalized_run(db_path, run_id: str, *, timeout: float = 15.0) -> dict:
    """Block until `run_id` is finalized — not merely terminal — and return it.

    Raises `AssertionError` naming *which* guarantee is missing, because the two
    failures read identically from a bare timeout and mean opposite things: a
    run still RUNNING is slow, and a run terminal-but-unfinalized is the defect
    this helper exists for.
    """
    deadline = time.monotonic() + timeout
    run = None
    while True:
        run = runtime_db.get_run(db_path, run_id)
        if run is not None and run.get("finalized_at"):
            return run
        if time.monotonic() >= deadline:
            break
        time.sleep(_POLL_SECONDS)

    if run is None:
        raise AssertionError(f"run {run_id!r} does not exist in {db_path} after {timeout}s")
    if run["state"] not in runtime_db.TERMINAL_STATES:
        raise AssertionError(
            f"run {run_id!r} did not reach a terminal state within {timeout}s "
            f"(state={run['state']!r})"
        )
    has_report = runtime_db.get_report(db_path, run_id) is not None
    raise AssertionError(
        f"run {run_id!r} is {run['state']} but never finalized within {timeout}s "
        f"(finalized_at is NULL, report={'present' if has_report else 'missing'}) — "
        "its supervisor thread was killed or wedged inside the finalization "
        "window, so the process_exited event, the auto-commit of the agent's "
        "work and the run report may all be missing"
    )
