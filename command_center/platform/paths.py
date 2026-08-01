"""Standard per-platform application directories."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_APP_DIR = "AI Command Center"


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
