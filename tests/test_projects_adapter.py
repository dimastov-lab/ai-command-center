"""Plain-pytest contract tests for the Desktop D3A Projects adapter."""

from __future__ import annotations

from pathlib import Path

from command_center import project_config
from command_center.application.projects_adapter import ProjectsAdapter


def test_list_projects_returns_project_config_result_unchanged(monkeypatch):
    expected = {"AIOS": {"id": "AIOS", "repository_path": "/repo/aios"}}
    monkeypatch.setattr(project_config, "load_project_configs", lambda: expected)

    assert ProjectsAdapter().list_projects() is expected


def test_validate_repository_path_delegates_verbatim(monkeypatch):
    calls: list[str] = []

    def validate(path: str) -> tuple[bool, str]:
        calls.append(path)
        return False, "Проверочная ошибка"

    monkeypatch.setattr(project_config, "validate_repository_path", validate)

    assert ProjectsAdapter().validate_repository_path("candidate") == (
        False,
        "Проверочная ошибка",
    )
    assert calls == ["candidate"]


def test_save_repository_path_delegates_verbatim(monkeypatch):
    calls: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        project_config,
        "save_repository_path",
        lambda project_id, path: calls.append((project_id, path)),
    )

    adapter = ProjectsAdapter()
    adapter.save_repository_path("AIOS", "/repo/aios")
    adapter.save_repository_path("AIOS", None)

    assert calls == [("AIOS", "/repo/aios"), ("AIOS", None)]


def test_adapter_reuses_real_validation_success_and_failure(tmp_path):
    adapter = ProjectsAdapter()

    assert adapter.validate_repository_path(str(tmp_path)) == (True, "OK")
    valid, message = adapter.validate_repository_path(str(tmp_path / "missing"))
    assert valid is False
    assert "не существует" in message


def test_adapter_module_has_no_qt_import():
    source = (
        Path(project_config.__file__).resolve().parent
        / "application"
        / "projects_adapter.py"
    ).read_text(encoding="utf-8")
    assert "PySide6" not in source
    assert "PyQt" not in source
