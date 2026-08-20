"""The consolidated finalization wait is stronger than what it replaced.

VOYN-W0-AICC-FLAKE-03. Five test modules each carried their own answer to "is
this run finished?" — two waited for a terminal state plus a report row, three
for the report row alone. `tests/finalization_helpers.wait_for_finalized_run`
replaced all five, and these are the two properties that make the replacement
worth making rather than a rename: each fails if the helper's predicate is
swapped back for either of the ones it retired.
"""

from __future__ import annotations

import threading
import time

import pytest

from command_center.runtime import db as runtime_db
from tests.finalization_helpers import wait_for_finalized_run


@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "runtime.db"
    runtime_db.migrate(path)
    return path


def _terminal_run(db_path, *, state: str = "COMPLETED") -> dict:
    task = runtime_db.create_task(db_path, project="AIOS", title="t", task_type="review")
    session = runtime_db.create_session(
        db_path, task_id=task["id"], project="AIOS", repository_path="/tmp/x"
    )
    run = runtime_db.create_run(
        db_path,
        session_id=session["id"],
        task_id=task["id"],
        project="AIOS",
        repository_path="/tmp/x",
        task_type="review",
        prompt="p",
        is_resume=False,
        command=["claude", "--print"],
    )
    for step in ("QUEUED", "RUNNING", state):
        current = runtime_db.get_run(db_path, run["id"])
        run = runtime_db.update_run_state(
            db_path, run["id"], expected_version=current["version"], new_state=step
        )
    return run


def test_a_report_row_is_not_the_finish_line(db_path):
    """The predicate three of the retired helpers used, and why it was wrong.

    The report is written *inside* the finalization window, not at its end:
    `_mark_finalized` is the write after it, and until that lands the daemon
    thread is still touching the database that the test's teardown is about to
    swap out from under it. A helper that returns here returns early.

    Mutation: change the helper's condition to `get_report(...) is not None` and
    this test fails.
    """
    run = _terminal_run(db_path)
    runtime_db.create_report(db_path, run["id"], "reports/whatever.md")
    assert runtime_db.get_report(db_path, run["id"]) is not None

    with pytest.raises(AssertionError) as excinfo:
        wait_for_finalized_run(db_path, run["id"], timeout=0.3)

    message = str(excinfo.value)
    assert "never finalized" in message
    assert "report=present" in message, (
        "the diagnosis must distinguish 'finalized nothing' from 'finalized all "
        f"but the marker'; got: {message}"
    )


def test_a_terminal_run_that_never_produces_a_report_is_still_finished(db_path):
    """The other half: the report poll waits out a run that already finished.

    `INTERRUPTED` and `UNKNOWN` write no report, and neither does the
    reconciliation path that produces them — `RunFinalizer.persist_run_failure`
    marks such a run finalized precisely because there is nothing else to wait
    for. A report poll reports "did not finish" about a run that is finished,
    which is a false failure aimed at the wrong place.

    Mutation: require a report row and this test fails.
    """
    run = _terminal_run(db_path, state="INTERRUPTED")
    runtime_db.mark_run_finalized(db_path, run["id"])
    assert runtime_db.get_report(db_path, run["id"]) is None

    settled = wait_for_finalized_run(db_path, run["id"], timeout=1.0)
    assert settled["state"] == "INTERRUPTED"
    assert settled["finalized_at"] is not None


def test_it_returns_as_soon_as_the_marker_lands(db_path):
    """A wait, not a sleep: the run finalizes late and the helper follows it."""
    run = _terminal_run(db_path)
    runtime_db.create_report(db_path, run["id"], "reports/whatever.md")

    def finalize_late() -> None:
        time.sleep(0.2)
        runtime_db.mark_run_finalized(db_path, run["id"])

    finisher = threading.Thread(target=finalize_late)
    finisher.start()
    try:
        settled = wait_for_finalized_run(db_path, run["id"], timeout=10.0)
    finally:
        finisher.join()
    assert settled["finalized_at"] is not None


def test_a_run_that_never_went_terminal_is_diagnosed_as_that_instead(db_path):
    """Slow and broken read identically from a bare timeout and are not the same.

    A run still RUNNING means the test's own setup did not finish; a run
    terminal-but-unfinalized means the defect this helper exists for. Saying
    which one happened is the difference between a five-minute diagnosis and an
    hour of reading the wrong module.
    """
    task = runtime_db.create_task(db_path, project="AIOS", title="t", task_type="review")
    session = runtime_db.create_session(
        db_path, task_id=task["id"], project="AIOS", repository_path="/tmp/x"
    )
    run = runtime_db.create_run(
        db_path,
        session_id=session["id"],
        task_id=task["id"],
        project="AIOS",
        repository_path="/tmp/x",
        task_type="review",
        prompt="p",
        is_resume=False,
        command=["claude", "--print"],
    )

    with pytest.raises(AssertionError, match="did not reach a terminal state"):
        wait_for_finalized_run(db_path, run["id"], timeout=0.3)


def test_a_missing_run_says_so_rather_than_blaming_finalization(db_path):
    with pytest.raises(AssertionError, match="does not exist"):
        wait_for_finalized_run(db_path, "no-such-run", timeout=0.3)
