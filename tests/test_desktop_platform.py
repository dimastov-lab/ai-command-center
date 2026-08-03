"""D3 platform abstraction contract without touching the host OS."""

from __future__ import annotations

from pathlib import Path

import pytest

from command_center.platform import file_manager, paths


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("darwin", "macos"), ("win32", "windows")],
)
def test_platform_name_is_stable(monkeypatch, raw, expected):
    monkeypatch.setattr(paths.sys, "platform", raw)
    assert paths.platform_name() == expected


def test_platform_name_rejects_unsupported_platform(monkeypatch):
    monkeypatch.setattr(paths.sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="Unsupported desktop platform"):
        paths.platform_name()


def test_macos_standard_directories(monkeypatch, tmp_path):
    monkeypatch.setattr(paths.sys, "platform", "darwin")
    monkeypatch.setattr(paths.Path, "home", classmethod(lambda _cls: tmp_path))
    assert paths.log_dir() == tmp_path / "Library/Logs/AI Command Center"
    assert paths.cache_dir() == tmp_path / "Library/Caches/AI Command Center"
    assert paths.crash_dir() == (
        tmp_path
        / "Library/Application Support/AI Command Center/CrashReports"
    )


def test_windows_standard_directories(monkeypatch, tmp_path):
    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert paths.log_dir() == tmp_path / "AI Command Center/Logs"
    assert paths.cache_dir() == tmp_path / "AI Command Center/Cache"
    assert paths.crash_dir() == tmp_path / "AI Command Center/CrashReports"


def test_runtime_environment_discovers_conventional_workspace(monkeypatch, tmp_path):
    workspace = tmp_path / "Projects" / "ai-command-center"
    (workspace / "data").mkdir(parents=True)
    (workspace / "generated").mkdir()
    (workspace / "reports").mkdir()
    monkeypatch.setattr(paths.Path, "home", classmethod(lambda _cls: tmp_path))
    for name in (
        "AICC_WORKSPACE_ROOT",
        "AICC_DATA_DIR",
        "AICC_GENERATED_ROOT",
        "AICC_REPORTS_ROOT",
    ):
        monkeypatch.delenv(name, raising=False)

    assert paths.configure_runtime_environment() == workspace
    assert paths.os.environ["AICC_DATA_DIR"] == str(workspace / "data")
    assert paths.os.environ["AICC_GENERATED_ROOT"] == str(workspace / "generated")
    assert paths.os.environ["AICC_REPORTS_ROOT"] == str(workspace / "reports")


def test_runtime_environment_preserves_explicit_overrides(monkeypatch, tmp_path):
    explicit = {
        "AICC_DATA_DIR": str(tmp_path / "custom-data"),
        "AICC_GENERATED_ROOT": str(tmp_path / "custom-generated"),
        "AICC_REPORTS_ROOT": str(tmp_path / "custom-reports"),
    }
    for name, value in explicit.items():
        monkeypatch.setenv(name, value)

    workspace = tmp_path / "workspace"
    (workspace / "data").mkdir(parents=True)

    assert paths.configure_runtime_environment(workspace) == workspace
    assert {name: paths.os.environ[name] for name in explicit} == explicit


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        ("macos", ["open", "-R", "/tmp/project"]),
        ("windows", ["explorer.exe", "/select,/tmp/project"]),
    ],
)
def test_reveal_uses_argument_list_without_shell(monkeypatch, platform, expected):
    calls = []
    monkeypatch.setattr(file_manager, "platform_name", lambda: platform)
    monkeypatch.setattr(
        file_manager.subprocess,
        "run",
        lambda command, check: calls.append((command, check)),
    )
    monkeypatch.setattr(Path, "resolve", lambda self, strict=False: self)

    file_manager.reveal_in_file_manager(Path("/tmp/project"))

    assert calls == [(expected, True)]
