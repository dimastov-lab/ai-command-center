"""Tests for `command_center/application/workspace_home_adapter.py` (Desktop D2A).

The adapter is an *application-layer* composition boundary over the existing
`build_workspace_home_snapshot` read model and the separate read-only AIOS Core
status port. It preserves every existing read-model field and adds `aios_core`
without treating the local runtime as evidence about AIOS.

Plain pytest: no `QApplication`, and the adapter module must not import Qt.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from command_center import activity_log, project_config, workspace_home
from command_center.application.aios_status import AIOSCoreReadiness, AIOSCoreStatus
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


def test_adapter_exposes_aios_core_status_through_independent_public_port(tmp_path, monkeypatch):
    class _AIOSClient:
        def get_core_status(self):
            return AIOSCoreStatus(
                readiness=AIOSCoreReadiness.READY,
                source="AIOS API",
                version="0.3.0",
                evidence=("build:abc123",),
            )

    monkeypatch.setattr(
        "command_center.application.workspace_home_adapter.build_workspace_home_snapshot",
        lambda **_kwargs: {"projects": []},
    )

    adapter = WorkspaceHomeAdapter(
        execution_center_api=_api(tmp_path),
        aios_status_client=_AIOSClient(),
    )
    snapshot = adapter.snapshot()
    status = adapter.aios_core_status()

    assert "aios_core" not in snapshot
    assert status == {
        "readiness": "ready",
        "source": "AIOS API",
        "version": "0.3.0",
        "health": None,
        "capabilities": [],
        "gates": [],
        "evidence": ["build:abc123"],
        "detail": None,
    }


def test_local_snapshot_does_not_wait_for_or_call_aios_status(tmp_path, monkeypatch):
    class _RaisingAIOSClient:
        def get_core_status(self):
            raise RuntimeError("remote unavailable")

    monkeypatch.setattr(
        "command_center.application.workspace_home_adapter.build_workspace_home_snapshot",
        lambda **_kwargs: {"projects": [{"id": "AIOS"}]},
    )
    adapter = WorkspaceHomeAdapter(
        execution_center_api=_api(tmp_path),
        aios_status_client=_RaisingAIOSClient(),
    )

    assert adapter.snapshot() == {"projects": [{"id": "AIOS"}]}


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
