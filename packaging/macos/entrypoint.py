"""PyInstaller entry point for the native macOS application."""

from __future__ import annotations

import sys

from command_center.desktop.app import run


if __name__ == "__main__":
    raise SystemExit(run(sys.argv))
