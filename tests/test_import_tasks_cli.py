"""`scripts/import_tasks.py`, exercised as a genuinely separate OS process —
same rationale as `tests/test_execution_center_debug_cli.py`: proves the CLI
entry point itself (argument parsing, exit codes, stdout) works, not just the
`command_center.task_import` functions it calls.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from command_center import tasks_repository

ROOT = Path(__file__).resolve().parent.parent
CLI_SCRIPT = ROOT / "scripts" / "import_tasks.py"


def _task(**overrides) -> dict:
    base = {
        "id": "PKG-001",
        "title": "CLI-imported task",
        "goal": "CLI-imported task goal",
        "repository_path": "/repo",
        "workspace_path": "/repo",
        "branch": "main",
        "status": "Backlog",
        "project": "AIOS",
        "task_type": "implementation",
        "priority": "Medium",
        "depends_on": [],
    }
    base.update(overrides)
    return base


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CLI_SCRIPT), *args],
        cwd=ROOT,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=30,
    )


def _write_package(tmp_path: Path, tasks: list[dict], name: str = "package.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(tasks), encoding="utf-8")
    return path


def test_dry_run_does_not_write_tasks(tmp_path):
    package_path = _write_package(tmp_path, [_task(id="PKG-001"), _task(id="PKG-002")])
    result = _run_cli(str(package_path), "--dry-run")
    assert result.returncode == 0
    assert "New:             2" in result.stdout
    assert tasks_repository.load_tasks(ROOT) == []


def test_apply_writes_tasks(tmp_path):
    package_path = _write_package(tmp_path, [_task(id="PKG-001"), _task(id="PKG-002")])
    result = _run_cli(str(package_path), "--apply")
    assert result.returncode == 0
    assert "Импортировано: 2" in result.stdout

    stored = tasks_repository.load_tasks(ROOT)
    assert sorted(t["id"] for t in stored) == ["PKG-001", "PKG-002"]


def test_apply_twice_is_idempotent(tmp_path):
    package_path = _write_package(tmp_path, [_task(id="PKG-001"), _task(id="PKG-002")])
    first = _run_cli(str(package_path), "--apply")
    assert first.returncode == 0

    second = _run_cli(str(package_path), "--apply")
    assert second.returncode == 0
    assert "Импортировано: 0" in second.stdout
    assert "Пропущено (дубликаты): 2" in second.stdout

    stored = tasks_repository.load_tasks(ROOT)
    assert len(stored) == 2


def test_apply_refuses_package_with_validation_errors(tmp_path):
    package_path = _write_package(tmp_path, [_task(id="PKG-001", status="Someday")])
    result = _run_cli(str(package_path), "--apply")
    assert result.returncode == 2
    assert "ошибки валидации" in result.stderr
    assert tasks_repository.load_tasks(ROOT) == []


def test_malformed_json_exits_nonzero(tmp_path):
    package_path = tmp_path / "bad.json"
    package_path.write_text("not json", encoding="utf-8")
    result = _run_cli(str(package_path), "--dry-run")
    assert result.returncode == 2
    assert "Ошибка разбора пакета" in result.stderr


def test_missing_file_exits_nonzero(tmp_path):
    result = _run_cli(str(tmp_path / "does-not-exist.json"), "--dry-run")
    assert result.returncode == 2
    assert "Файл не найден" in result.stderr


def test_requires_exactly_one_of_dry_run_or_apply(tmp_path):
    package_path = _write_package(tmp_path, [_task(id="PKG-001")])
    result = _run_cli(str(package_path))
    assert result.returncode == 2

    result_both = _run_cli(str(package_path), "--dry-run", "--apply")
    assert result_both.returncode == 2


def test_apply_refuses_package_with_unresolved_dependency_by_default(tmp_path):
    package_path = _write_package(tmp_path, [_task(id="PKG-001", depends_on=["PKG-TYPO"])])
    result = _run_cli(str(package_path), "--apply")
    assert result.returncode == 2
    assert "PKG-TYPO" in result.stdout  # listed in the preview's error rows
    assert tasks_repository.load_tasks(ROOT) == []


def test_dry_run_refuses_package_with_unresolved_dependency_by_default(tmp_path):
    package_path = _write_package(tmp_path, [_task(id="PKG-001", depends_on=["PKG-TYPO"])])
    result = _run_cli(str(package_path), "--dry-run")
    assert result.returncode == 2
    assert "ошибки валидации" in result.stderr


def test_allow_unresolved_dependencies_flag_downgrades_to_a_warning_and_imports(tmp_path):
    package_path = _write_package(tmp_path, [_task(id="PKG-001", depends_on=["PKG-TYPO"])])
    result = _run_cli(str(package_path), "--apply", "--allow-unresolved-dependencies")
    assert result.returncode == 0
    assert "Импортировано: 1" in result.stdout
    assert "WARNING" in result.stdout
    assert "PKG-TYPO" in result.stdout

    stored = tasks_repository.load_tasks(ROOT)
    assert [t["id"] for t in stored] == ["PKG-001"]


def test_envelope_format_package_is_accepted(tmp_path):
    payload = {"schema_version": "1", "package_id": "wave-1", "tasks": [_task(id="PKG-001")]}
    package_path = tmp_path / "envelope.json"
    package_path.write_text(json.dumps(payload), encoding="utf-8")

    result = _run_cli(str(package_path), "--apply")
    assert result.returncode == 0
    assert "wave-1" in result.stdout
