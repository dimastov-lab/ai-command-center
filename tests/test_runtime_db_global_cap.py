"""The global concurrency cap as an atomic invariant in db.create_run (audit P0/H3).

The scheduler's cap was advisory; direct launch paths (the queue's launch_ready,
portfolio launches, the review gate) bypassed it, so a batch "launch all READY"
could spawn one agent per entry regardless of max_global_concurrency. The cap is
now enforced inside create_run's own BEGIN IMMEDIATE transaction, so it holds for
every path and cannot be raced past.
"""
from __future__ import annotations

import pytest

from command_center.runtime import db


def _active_run(db_path, workspace):
    task = db.create_task(db_path, project="AIOS", title="t", task_type="implementation")
    session = db.create_session(db_path, task_id=task["id"], project="AIOS", repository_path=workspace)
    return db.create_run(
        db_path, session_id=session["id"], task_id=task["id"], project="AIOS",
        task_type="implementation", repository_path=workspace, prompt="p", is_resume=False,
    )


def _create_with_cap(db_path, workspace, cap):
    task = db.create_task(db_path, project="AIOS", title="t", task_type="implementation")
    session = db.create_session(db_path, task_id=task["id"], project="AIOS", repository_path=workspace)
    return db.create_run(
        db_path, session_id=session["id"], task_id=task["id"], project="AIOS",
        task_type="implementation", repository_path=workspace, prompt="p", is_resume=False,
        enforce_workspace_lock=True, max_global_concurrency=cap,
    )


def test_create_run_rejects_a_launch_at_the_global_cap(tmp_path):
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)
    _active_run(db_path, "/tmp/ws1")
    _active_run(db_path, "/tmp/ws2")  # 2 active across all workspaces

    with pytest.raises(db.GlobalConcurrencyLimitError) as exc:
        _create_with_cap(db_path, "/tmp/ws3", cap=2)
    assert exc.value.active_count == 2
    assert exc.value.limit == 2


def test_create_run_allows_a_launch_below_the_global_cap(tmp_path):
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)
    _active_run(db_path, "/tmp/ws1")  # 1 active

    run = _create_with_cap(db_path, "/tmp/ws2", cap=2)  # 2nd is allowed
    assert run["state"] == "PREPARED"


def test_global_cap_counts_only_active_runs_not_terminal_ones(tmp_path):
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)
    run = _active_run(db_path, "/tmp/ws1")
    # Drive it to a terminal state — it must stop counting against the cap.
    run = db.update_run_state(db_path, run["id"], expected_version=run["version"], new_state="QUEUED")
    run = db.update_run_state(db_path, run["id"], expected_version=run["version"], new_state="RUNNING",
                              fields={"pid": None, "started_at": "2020-01-01T00:00:00"})
    db.update_run_state(db_path, run["id"], expected_version=run["version"], new_state="COMPLETED",
                        fields={"completed_at": "2020-01-01T00:00:01"})

    run2 = _create_with_cap(db_path, "/tmp/ws2", cap=1)  # cap=1, but 0 active now
    assert run2["state"] == "PREPARED"


def test_create_run_without_a_cap_is_unbounded(tmp_path):
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)
    for i in range(5):
        _active_run(db_path, f"/tmp/ws{i}")
    # No max_global_concurrency passed -> the historical unbounded behaviour.
    run = _active_run(db_path, "/tmp/ws-extra")
    assert run["state"] == "PREPARED"
