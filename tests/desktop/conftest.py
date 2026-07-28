"""Shared fixtures for the desktop (pytest-qt) test suite.

Runs against an offscreen ``QApplication`` (no real display) and isolates every
test's ``QSettings`` to a temp ``IniFormat`` file, so no test ever reads or writes
the real user/registry preferences.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

# The desktop suite needs PySide6 (an optional dependency declared in
# requirements-desktop.txt, not in the base install). Skip the whole suite
# gracefully when it is absent so `pytest -q` from the repo root still collects
# and runs the rest of the suite — the documented local quality gate — without
# the optional Qt stack. CI installs requirements-dev.txt (which pulls in
# PySide6) and therefore still runs these tests.
pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings

from command_center.desktop.app import build_shell
from command_center.desktop.settings import SettingsStore


@pytest.fixture
def settings_file(tmp_path):
    return tmp_path / "settings.ini"


@pytest.fixture
def settings_store(settings_file) -> SettingsStore:
    return SettingsStore(QSettings(str(settings_file), QSettings.IniFormat))


@pytest.fixture
def shell(qtbot, qapp, settings_store):
    """A fully-wired :class:`AppShell` under the offscreen QApplication."""
    widget, _theme = build_shell(qapp, settings_store)
    qtbot.addWidget(widget)
    return widget
