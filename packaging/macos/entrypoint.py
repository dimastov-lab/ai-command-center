"""PyInstaller entry point for the native macOS application."""

from __future__ import annotations

import sys

from command_center.platform.paths import configure_runtime_environment

configure_runtime_environment()

from command_center.desktop.app import run  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(run(sys.argv))
