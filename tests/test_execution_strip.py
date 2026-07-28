"""Unit tests for the Execution Strip's pure ``strip_counts`` bucketing.

The strip filters **persisted** ``run.state`` values (``runtime/db.RUN_STATES``)
into the three Live Board buckets. The original implementation filtered by
*display* statuses (Launching/Waiting/Blocked/…) that are never written to
``run.state``, so "в очереди" was always 0 and "требуют внимания" only counted
FAILED. These tests guard the persisted-vocabulary mapping and the
waiting-not-always-zero regression.
"""

from __future__ import annotations

from command_center import execution_queue
from command_center.ui.execution_strip import current_counts, strip_counts


def _run(state: str) -> dict:
    return {"id": "x", "state": state}


def test_running_is_live():
    assert strip_counts([_run("RUNNING")]) == (1, 0, 0)


def test_prepared_and_queued_are_waiting():
    # The core regression guard: PREPARED/QUEUED are the persisted states that
    # map to the board's "Ожидают запуска" bucket. The old code looked for the
    # display string "WAITING", which is never persisted, so this was always 0.
    assert strip_counts([_run("PREPARED"), _run("QUEUED")]) == (0, 2, 0)


def test_failed_is_attention():
    assert strip_counts([_run("FAILED")]) == (0, 0, 1)


def test_interrupted_and_unknown_are_attention():
    assert strip_counts([_run("INTERRUPTED"), _run("UNKNOWN")]) == (0, 0, 2)


def test_completed_and_cancelled_not_surfaced():
    # Terminal/clean states are intentionally absent from the "now" strip.
    assert strip_counts([_run("COMPLETED"), _run("CANCELLED")]) == (0, 0, 0)


def test_empty():
    assert strip_counts([]) == (0, 0, 0)


def test_missing_or_unknown_state_ignored():
    assert strip_counts([{}, _run("NONSENSE")]) == (0, 0, 0)


def test_mixed_totals():
    runs = [
        _run("RUNNING"), _run("RUNNING"),
        _run("QUEUED"),
        _run("FAILED"), _run("INTERRUPTED"),
        _run("COMPLETED"), _run("CANCELLED"),
    ]
    assert strip_counts(runs) == (2, 1, 2)


# Superseded-attention consistency with the Live Board — see
# `execution_strip._nonsuperseded_runs`.
def _trun(run_id: str, task_id: str, state: str, started_at: str) -> dict:
    return {"id": run_id, "task_id": task_id, "state": state, "started_at": started_at}


def test_superseded_failed_dropped_from_attention():
    # A task failed once and was retried (now running): the old FAILED attempt
    # is superseded and must not inflate "требуют внимания".
    runs = [
        _trun("r1", "t1", "FAILED", "2025-01-01T10:00:00"),
        _trun("r2", "t1", "RUNNING", "2025-01-01T11:00:00"),
    ]
    from command_center.ui.execution_strip import _nonsuperseded_runs
    assert strip_counts(_nonsuperseded_runs(runs)) == (1, 0, 0)


def test_adhoc_runs_never_superseded():
    # Runs without a task_id stand alone — both FAILED ad-hoc runs count.
    runs = [
        _trun("r1", "", "FAILED", "2025-01-01T10:00:00"),
        _trun("r2", "", "FAILED", "2025-01-01T11:00:00"),
    ]
    from command_center.ui.execution_strip import _nonsuperseded_runs
    assert strip_counts(_nonsuperseded_runs(runs)) == (0, 0, 2)


def test_headline_waiting_count_includes_durable_execution_queue():
    entries = [
        {"id": "q1", "task_id": "t1", "state": execution_queue.STATE_READY},
        {"id": "q2", "task_id": "t2", "state": execution_queue.STATE_WAITING},
    ]
    counts = current_counts([], [], entries)
    assert counts.waiting == 2


def test_headline_and_board_rules_drop_superseded_and_done_attention():
    tasks = [
        {"id": "t1", "status": "Backlog", "progress": 40, "task_type": "implementation"},
        {"id": "t2", "status": "Done", "progress": 100, "task_type": "implementation"},
    ]
    runs = [
        _trun("old", "t1", "FAILED", "2025-01-01T10:00:00"),
        _trun("new", "t1", "RUNNING", "2025-01-01T11:00:00"),
        _trun("done-failure", "t2", "FAILED", "2025-01-01T12:00:00"),
    ]
    counts = current_counts(runs, tasks, [])
    assert counts.live == 1
    assert counts.attention == 0


def test_completed_write_task_not_merged_counts_as_attention():
    tasks = [
        {"id": "t1", "status": "In Progress", "progress": 55, "task_type": "implementation"}
    ]
    counts = current_counts(
        [_trun("r1", "t1", "COMPLETED", "2025-01-01T10:00:00")],
        tasks,
        [],
    )
    assert counts.attention == 1
    assert counts.done == 0
