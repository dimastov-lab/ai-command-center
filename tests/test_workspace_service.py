"""Tests for `command_center/workspace_service.py` -- the composition layer
over `workspace_home`, `tasks_repository`, and `execution_queue` (see
`docs/workspace/UNIVERSAL_WORKSPACE_ARCHITECTURE.md`'s `workspace_service.py`
section and `docs/workspace/IMPLEMENTATION_ROADMAP.md`'s UW-1 note that
snapshot functions must "degrade gracefully when a referenced id no longer
resolves").

Plain pytest, no Streamlit anywhere -- `workspace_service.py` has no
Streamlit import.
"""

from __future__ import annotations

import pytest

from command_center import execution_queue, models, tasks_repository, workspace_service
from command_center.runtime.api import ExecutionCenterAPI


def _api(tmp_path) -> ExecutionCenterAPI:
    return ExecutionCenterAPI(db_path=tmp_path / "runtime.db")


def _make_kanban_task(project: str = "AIOS", **overrides) -> dict:
    task = tasks_repository.new_task_record(
        project=project,
        title="Test task",
        task_type="implementation",
        status="To Do",
    )
    task.update(overrides)
    return task


# --------------------------------------------------------------------------
# project_snapshot
# --------------------------------------------------------------------------


def test_project_snapshot_rejects_unknown_project(tmp_path):
    with pytest.raises(ValueError):
        workspace_service.project_snapshot("NOPE", execution_center_api=_api(tmp_path), root=tmp_path)


def test_project_snapshot_includes_every_configured_project(tmp_path):
    api = _api(tmp_path)
    for project_id in models.PROJECT_IDS:
        snapshot = workspace_service.project_snapshot(project_id, execution_center_api=api, root=tmp_path)
        assert snapshot["project"]["id"] == project_id
        assert snapshot["config"]["id"] == project_id
        assert snapshot["active_runs"] == []
        assert snapshot["queue_entries"] == []


def test_project_snapshot_filters_queue_entries_to_project(tmp_path):
    task_a = _make_kanban_task(project="AIOS")
    task_b = _make_kanban_task(project="BUSINESS")
    tasks_by_id = {task_a["id"]: task_a, task_b["id"]: task_b}
    entries = execution_queue.enqueue([], task_a, tasks_by_id)
    entries = execution_queue.enqueue(entries, task_b, tasks_by_id)
    execution_queue.save_queue(tmp_path, entries)

    snapshot = workspace_service.project_snapshot("AIOS", execution_center_api=_api(tmp_path), root=tmp_path)
    assert len(snapshot["queue_entries"]) == 1
    assert snapshot["queue_entries"][0]["task_id"] == task_a["id"]


# --------------------------------------------------------------------------
# task_snapshot
# --------------------------------------------------------------------------


def test_task_snapshot_returns_none_for_unknown_task(tmp_path):
    result = workspace_service.task_snapshot("ghost", execution_center_api=_api(tmp_path), root=tmp_path)
    assert result is None


def test_task_snapshot_joins_task_and_queue_entry(tmp_path):
    task = _make_kanban_task(project="AIOS")
    tasks_repository.save_tasks(tmp_path, [task])
    tasks_by_id = {task["id"]: task}
    entries = execution_queue.enqueue([], task, tasks_by_id)
    execution_queue.save_queue(tmp_path, entries)

    snapshot = workspace_service.task_snapshot(task["id"], execution_center_api=_api(tmp_path), root=tmp_path)
    assert snapshot["task"]["id"] == task["id"]
    assert snapshot["queue_entry"]["task_id"] == task["id"]
    assert snapshot["current_run"] is None


def test_task_snapshot_resolves_current_run(tmp_path):
    db_path = tmp_path / "runtime.db"
    api = ExecutionCenterAPI(db_path=db_path)
    from command_center.runtime import db as runtime_db

    runtime_db.migrate(db_path)
    v2_task = runtime_db.create_task(db_path, project="AIOS", title="t", task_type="implementation")
    session = runtime_db.create_session(db_path, task_id=v2_task["id"], project="AIOS", repository_path="/tmp/x")
    run = runtime_db.create_run(
        db_path,
        session_id=session["id"],
        task_id=v2_task["id"],
        project="AIOS",
        task_type="implementation",
        repository_path="/tmp/x",
        prompt="p",
        is_resume=False,
    )

    task = _make_kanban_task(project="AIOS", current_run_id=run["id"])
    tasks_repository.save_tasks(tmp_path, [task])

    snapshot = workspace_service.task_snapshot(task["id"], execution_center_api=api, root=tmp_path)
    assert snapshot["current_run"]["id"] == run["id"]


# --------------------------------------------------------------------------
# panel_data
# --------------------------------------------------------------------------


def test_panel_data_rejects_unknown_panel(tmp_path):
    with pytest.raises(ValueError):
        workspace_service.panel_data("no_such_panel", execution_center_api=_api(tmp_path), root=tmp_path)


def test_panel_data_empty_when_required_id_missing(tmp_path):
    assert workspace_service.panel_data("kanban", execution_center_api=_api(tmp_path), root=tmp_path) == {}
    assert workspace_service.panel_data("task_detail", execution_center_api=_api(tmp_path), root=tmp_path) == {}


def test_panel_data_routes_project_scoped_panel(tmp_path):
    result = workspace_service.panel_data(
        "kanban", execution_center_api=_api(tmp_path), root=tmp_path, project_id="AIOS"
    )
    assert result["project"]["id"] == "AIOS"


def test_panel_data_routes_task_scoped_panel(tmp_path):
    task = _make_kanban_task(project="AIOS")
    tasks_repository.save_tasks(tmp_path, [task])

    result = workspace_service.panel_data(
        "task_detail", execution_center_api=_api(tmp_path), root=tmp_path, task_id=task["id"]
    )
    assert result["task"]["id"] == task["id"]


def test_panel_data_returns_empty_dict_for_none_data_kind_panel(tmp_path):
    assert workspace_service.panel_data("agents", execution_center_api=_api(tmp_path), root=tmp_path) == {}
