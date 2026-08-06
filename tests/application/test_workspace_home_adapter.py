"""WorkspaceHomeAdapter tests — plain pytest, no QApplication (ARCHITECTURE.md §5)."""
from __future__ import annotations
from pathlib import Path
from command_center.runtime.api import ExecutionCenterAPI
from command_center.application.workspace_home_adapter import WorkspaceHomeAdapter


def _api(tmp_path: Path) -> ExecutionCenterAPI:
    return ExecutionCenterAPI(db_path=tmp_path / "runtime.db")


def test_adapter_snapshot_returns_expected_keys(tmp_path, isolated_data_dir, monkeypatch):
    """adapter.snapshot() returns the standard Workspace Home top-level keys."""
    import command_center.workspace_home as wh
    reports_dir = isolated_data_dir / "reports"
    generated_dir = isolated_data_dir / "generated"
    reports_dir.mkdir(parents=True, exist_ok=True)
    generated_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(wh, "REPORTS_DIR", reports_dir)
    monkeypatch.setattr(wh, "GENERATED_DIR", generated_dir)

    adapter = WorkspaceHomeAdapter(execution_center_api=_api(tmp_path))
    result = adapter.snapshot()

    expected_keys = {"projects", "worktrees_by_project", "active_runs",
                     "recent_runs", "reports", "artifacts", "recent_activity"}
    assert expected_keys.issubset(result.keys())


def test_adapter_no_qt_import():
    """The application package must not import Qt (ARCHITECTURE.md §5)."""
    import command_center.application.workspace_home_adapter as mod
    src = Path(mod.__file__).read_text()
    assert "PySide6" not in src
    assert "PyQt" not in src


def test_adapter_snapshot_does_not_transform_result(tmp_path, isolated_data_dir, monkeypatch):
    """adapter.snapshot() returns build_workspace_home_snapshot's dict unchanged."""
    import command_center.workspace_home as wh
    from unittest.mock import patch
    reports_dir = isolated_data_dir / "reports"
    generated_dir = isolated_data_dir / "generated"
    reports_dir.mkdir(parents=True, exist_ok=True)
    generated_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(wh, "REPORTS_DIR", reports_dir)
    monkeypatch.setattr(wh, "GENERATED_DIR", generated_dir)

    sentinel = {
        "projects": [], "worktrees_by_project": {}, "active_runs": [],
        "recent_runs": [], "reports": [], "artifacts": [], "recent_activity": [],
    }
    adapter = WorkspaceHomeAdapter(execution_center_api=_api(tmp_path))
    with patch("command_center.application.workspace_home_adapter.build_workspace_home_snapshot",
               return_value=sentinel) as mock_fn:
        result = adapter.snapshot()

    mock_fn.assert_called_once()
    assert result is sentinel
