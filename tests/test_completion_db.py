"""Unit tests for the completion persistence layer (migration 5 + CRUD)."""

from __future__ import annotations

import sqlite3

import pytest

from command_center.runtime import db as runtime_db


@pytest.fixture
def db_path():
    path = runtime_db.resolve_db_path()
    runtime_db.migrate(path)
    return path


def _run(db_path):
    task = runtime_db.create_task(db_path, project="AICC", title="t", task_type="implementation")
    session = runtime_db.create_session(db_path, task_id=task["id"], project="AICC", repository_path="/tmp/r")
    run = runtime_db.create_run(
        db_path, session_id=session["id"], task_id=task["id"], project="AICC",
        task_type="implementation", repository_path="/tmp/r", prompt="p", is_resume=False,
    )
    return task, session, run


def test_schema_version_matches_the_migration_sequence(db_path):
    assert runtime_db.current_schema_version(db_path) == runtime_db.SCHEMA_VERSION
    # Compared against the migration list rather than a literal, so adding a
    # migration does not fail a test about version *consistency*.
    assert runtime_db.SCHEMA_VERSION == max(v for v, _ in runtime_db.MIGRATIONS)


def test_migrate_is_idempotent(db_path):
    runtime_db.migrate(db_path)  # second run must not error
    assert runtime_db.current_schema_version(db_path) == runtime_db.SCHEMA_VERSION


def test_create_and_get_completion(db_path):
    _, session, run = _run(db_path)
    row = runtime_db.create_completion(
        db_path, run_id=run["id"], task_id=run["task_id"], project="AICC",
        repository_path="/tmp/r", completion_state="EXECUTION_FINISHED", session_id=session["id"],
        branch="task/x", base_branch="main", merge_mode="manual", merge_method="squash",
    )
    assert row["version"] == 0
    fetched = runtime_db.get_completion(db_path, run["id"])
    assert fetched["completion_state"] == "EXECUTION_FINISHED"
    assert fetched["branch"] == "task/x"
    assert fetched["retry_count"] == 0


def test_create_completion_duplicate_rejected(db_path):
    _, _, run = _run(db_path)
    runtime_db.create_completion(
        db_path, run_id=run["id"], task_id=run["task_id"], project="AICC",
        repository_path="/tmp/r", completion_state="EXECUTION_FINISHED",
    )
    with pytest.raises(sqlite3.IntegrityError):
        runtime_db.create_completion(
            db_path, run_id=run["id"], task_id=run["task_id"], project="AICC",
            repository_path="/tmp/r", completion_state="EXECUTION_FINISHED",
        )


def test_update_completion_cas(db_path):
    _, _, run = _run(db_path)
    runtime_db.create_completion(
        db_path, run_id=run["id"], task_id=run["task_id"], project="AICC",
        repository_path="/tmp/r", completion_state="EXECUTION_FINISHED",
    )
    updated = runtime_db.update_completion(
        db_path, run["id"], expected_version=0,
        fields={"completion_state": "VALIDATING_RESULT", "retry_count": 2},
    )
    assert updated["completion_state"] == "VALIDATING_RESULT"
    assert updated["version"] == 1
    with pytest.raises(runtime_db.LostUpdateError):
        runtime_db.update_completion(db_path, run["id"], expected_version=0, fields={"retry_count": 3})


def test_update_completion_rejects_unknown_field(db_path):
    _, _, run = _run(db_path)
    runtime_db.create_completion(
        db_path, run_id=run["id"], task_id=run["task_id"], project="AICC",
        repository_path="/tmp/r", completion_state="EXECUTION_FINISHED",
    )
    with pytest.raises(runtime_db.UnknownRunFieldError):
        runtime_db.update_completion(db_path, run["id"], expected_version=0, fields={"task_id": "evil"})


def test_list_completions_filters_by_state_and_due(db_path):
    _, _, run = _run(db_path)
    runtime_db.create_completion(
        db_path, run_id=run["id"], task_id=run["task_id"], project="AICC",
        repository_path="/tmp/r", completion_state="AWAITING_MERGE",
    )
    runtime_db.update_completion(
        db_path, run["id"], expected_version=0, fields={"next_retry_at": "2026-07-22T10:00:00"}
    )
    assert runtime_db.list_completions(db_path, states=["AWAITING_MERGE"], due_before="2026-07-22T11:00:00")
    assert runtime_db.list_completions(db_path, states=["AWAITING_MERGE"], due_before="2026-07-22T09:00:00") == []
    assert runtime_db.list_completions(db_path, states=["COMPLETED"]) == []
    assert runtime_db.list_completions(db_path, states=[]) == []


def test_completion_events_are_ordered_and_carry_metadata(db_path):
    _, _, run = _run(db_path)
    runtime_db.create_completion(
        db_path, run_id=run["id"], task_id=run["task_id"], project="AICC",
        repository_path="/tmp/r", completion_state="EXECUTION_FINISHED",
    )
    runtime_db.append_completion_event(db_path, run["id"], "PR_CREATED", reason_code="PR_OPEN", metadata={"n": 1})
    runtime_db.append_completion_event(db_path, run["id"], "PR_MERGED", reason_code="PR_MERGED")
    events = runtime_db.list_completion_events(db_path, run["id"])
    assert [e["event_type"] for e in events] == ["PR_CREATED", "PR_MERGED"]
    assert events[0]["seq"] == 1 and events[1]["seq"] == 2
    assert events[0]["metadata"] == {"n": 1}
    assert events[1]["metadata"] is None


def test_validation_results_recorded(db_path):
    _, _, run = _run(db_path)
    runtime_db.create_completion(
        db_path, run_id=run["id"], task_id=run["task_id"], project="AICC",
        repository_path="/tmp/r", completion_state="VALIDATING_RESULT",
    )
    runtime_db.record_validation_result(
        db_path, run["id"], attempt=1, command="pytest", exit_code=0,
        started_at="a", finished_at="b", stdout_summary="ok", stderr_summary="",
    )
    runtime_db.record_validation_result(
        db_path, run["id"], attempt=1, command="ruff check .", exit_code=1,
        started_at="a", finished_at="b", stdout_summary="E501", stderr_summary="",
    )
    results = runtime_db.list_validation_results(db_path, run["id"])
    assert len(results) == 2
    assert {r["command"] for r in results} == {"pytest", "ruff check ."}
    assert runtime_db.list_validation_results(db_path, run["id"], attempt=2) == []


def test_get_completion_by_task_returns_latest(db_path):
    _, _, run = _run(db_path)
    runtime_db.create_completion(
        db_path, run_id=run["id"], task_id=run["task_id"], project="AICC",
        repository_path="/tmp/r", completion_state="COMPLETED",
    )
    assert runtime_db.get_completion_by_task(db_path, run["task_id"])["run_id"] == run["id"]
    assert runtime_db.get_completion_by_task(db_path, "nonexistent") is None
