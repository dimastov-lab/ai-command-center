"""Tests for `command_center/application/workspace_home_adapter.py` (Desktop D2A).

The adapter is a thin *application-layer* wrapper over the existing
`build_workspace_home_snapshot` read model. Its contract: own the single
`ExecutionCenterAPI` and return the read model's snapshot UNCHANGED — it must not
drop, add, or alter any field (`docs/desktop/IMPLEMENTATION_ROADMAP.md` D2A).

Plain pytest: no `QApplication`, and the adapter module must not import Qt.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from command_center import activity_log, project_config, workspace_home
from command_center.application.workspace_home_adapter import WorkspaceHomeAdapter
from command_center.runtime.api import ExecutionCenterAPI

SNAPSHOT_KEYS = {
    "projects",
    "worktrees_by_project",
    "active_runs",
    "recent_runs",
    "recent_activity",
    "artifacts",
    "reports",
}


@pytest.fixture
def _isolated_artifact_dirs(isolated_data_dir, monkeypatch):
    reports_dir = isolated_data_dir / "reports"
    generated_dir = isolated_data_dir / "generated"
    reports_dir.mkdir(parents=True, exist_ok=True)
    generated_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(workspace_home, "REPORTS_DIR", reports_dir)
    monkeypatch.setattr(workspace_home, "GENERATED_DIR", generated_dir)
    return reports_dir, generated_dir


def _api(tmp_path) -> ExecutionCenterAPI:
    return ExecutionCenterAPI(db_path=tmp_path / "runtime.db")


def test_adapter_returns_read_model_snapshot_unchanged_empty_state(tmp_path):
    api = _api(tmp_path)
    expected = workspace_home.build_workspace_home_snapshot(execution_center_api=api)

    actual = WorkspaceHomeAdapter(execution_center_api=api).snapshot()

    assert actual == expected
    assert set(actual) == SNAPSHOT_KEYS


def test_adapter_preserves_every_field_on_populated_state(
    tmp_path, git_repo, configure_project_repo, fake_claude, _isolated_artifact_dirs
):
    configure_project_repo("AIOS", git_repo)
    project_config.save_repository_path("AIOS", str(git_repo))
    api = _api(tmp_path)
    run = api.start_run(
        project="AIOS",
        repository_path=str(git_repo),
        task_type="implementation",
        instruction="do work",
        confirmed=True,
    )
    api.supervisor.wait_for_run(run["id"], timeout=10)
    activity_log.log_event("run_completed", project="AIOS", run_id=run["id"], message="ok")
    _reports_dir, generated_dir = _isolated_artifact_dirs
    (generated_dir / "AIOS").mkdir(parents=True, exist_ok=True)
    (generated_dir / "AIOS" / "abc123_implementation.md").write_text("# Task\n")

    expected = workspace_home.build_workspace_home_snapshot(execution_center_api=api)
    actual = WorkspaceHomeAdapter(execution_center_api=api).snapshot()

    assert actual == expected
    assert set(actual) == SNAPSHOT_KEYS


def test_adapter_passes_limits_through(tmp_path):
    api = _api(tmp_path)
    expected = workspace_home.build_workspace_home_snapshot(
        execution_center_api=api, active_runs_limit=1, reports_limit=3
    )

    actual = WorkspaceHomeAdapter(execution_center_api=api).snapshot(
        active_runs_limit=1, reports_limit=3
    )

    assert actual == expected


def test_adapter_owns_the_injected_execution_center_api(tmp_path):
    api = _api(tmp_path)
    adapter = WorkspaceHomeAdapter(execution_center_api=api)
    assert adapter.execution_center_api is api


def test_adapter_constructs_a_single_default_api_when_none_injected(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(
        "command_center.application.workspace_home_adapter.ExecutionCenterAPI",
        lambda: sentinel,
    )
    adapter = WorkspaceHomeAdapter()
    assert adapter.execution_center_api is sentinel


def test_adapter_module_does_not_import_qt():
    module_file = (
        Path(workspace_home.__file__).resolve().parent
        / "application"
        / "workspace_home_adapter.py"
    )
    source = module_file.read_text(encoding="utf-8")
    assert "PySide6" not in source
    assert "PyQt" not in source
