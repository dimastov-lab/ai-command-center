"""Standard per-platform application directories."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_APP_DIR = "AI Command Center"
_WORKSPACE_NAME = "ai-command-center"
_RUNTIME_PATHS = {
    "AICC_DATA_DIR": "data",
    "AICC_GENERATED_ROOT": "generated",
    "AICC_REPORTS_ROOT": "reports",
}


def _discover_workspace_root() -> Path | None:
    configured = os.environ.get("AICC_WORKSPACE_ROOT")
    if configured:
        return Path(configured).expanduser()

    for candidate in (
        Path.home() / "Projects" / _WORKSPACE_NAME,
        Path.home() / _WORKSPACE_NAME,
    ):
        if (candidate / "data").is_dir():
            return candidate
    return None


def configure_runtime_environment(workspace_root: Path | None = None) -> Path | None:
    """Point a packaged desktop app at a conventional live workspace."""
    root = (
        Path(workspace_root).expanduser()
        if workspace_root is not None
        else _discover_workspace_root()
    )
    if root is None:
        return None

    for variable, child in _RUNTIME_PATHS.items():
        os.environ.setdefault(variable, str(root / child))
    return root


def platform_name() -> str:
    if sys.platform == "darwin":
        return "macos"
    if sys.platform == "win32":
        return "windows"
    raise RuntimeError(f"Unsupported desktop platform: {sys.platform}")


def _windows_local_app_data() -> Path:
    value = os.environ.get("LOCALAPPDATA")
    if not value:
        raise RuntimeError("LOCALAPPDATA is not configured")
    return Path(value)


def log_dir() -> Path:
    if platform_name() == "macos":
        return Path.home() / "Library" / "Logs" / _APP_DIR
    return _windows_local_app_data() / _APP_DIR / "Logs"


def cache_dir() -> Path:
    if platform_name() == "macos":
        return Path.home() / "Library" / "Caches" / _APP_DIR
    return _windows_local_app_data() / _APP_DIR / "Cache"


def crash_dir() -> Path:
    if platform_name() == "macos":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / _APP_DIR
            / "CrashReports"
        )
    return _windows_local_app_data() / _APP_DIR / "CrashReports"
