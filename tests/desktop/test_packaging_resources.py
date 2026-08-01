from __future__ import annotations

from importlib.resources.abc import Traversable
from pathlib import Path

from command_center.platform import resource, resource_path


def test_resource_root_resolves_command_center_package() -> None:
    root = resource()

    assert isinstance(root, Traversable)
    assert root.joinpath("desktop", "__init__.py").is_file()


def test_resource_path_matches_source_and_is_absolute() -> None:
    path = resource_path("desktop/__init__.py")

    assert path.is_absolute()
    assert path.is_file()


def test_macos_spec_keeps_server_frameworks_out() -> None:
    spec = Path(__file__).parents[2] / "packaging/macos/ai-command-center.spec"
    text = spec.read_text(encoding="utf-8")

    assert '"streamlit"' in text
    assert '"fastapi"' in text
    assert '"uvicorn"' in text
    assert 'target_arch="arm64"' in text


def test_windows_spec_keeps_server_frameworks_out() -> None:
    spec = Path(__file__).parents[2] / "packaging/windows/ai-command-center.spec"
    text = spec.read_text(encoding="utf-8")

    assert '"streamlit"' in text
    assert '"fastapi"' in text
    assert '"uvicorn"' in text
    assert '"flask"' in text


def test_windows_spec_builds_a_windowed_app_without_a_console() -> None:
    """A console=True build pops a cmd window behind the GUI on every launch."""
    spec = Path(__file__).parents[2] / "packaging/windows/ai-command-center.spec"
    text = spec.read_text(encoding="utf-8")

    assert "console=False" in text
    assert "target_arch" not in text, "target_arch is macOS-only; Windows must not pin it"
