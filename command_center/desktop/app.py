"""Application assembly and entry point wiring for the desktop shell.

Constructs the one ``QApplication`` (`ARCHITECTURE.md` §3), the settings store,
the theme controller (applying the persisted mode), and the :class:`AppShell`
main window — in the documented startup order. Kept import-light: importing this
module constructs nothing (no ``QApplication`` at import time), satisfying the D1A
smoke-test contract.
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .main_window import AppShell
from .settings import APPLICATION, ORGANIZATION, SettingsStore
from .theme import ThemeController


def build_shell(
    app: QApplication, settings: SettingsStore | None = None
) -> tuple[AppShell, ThemeController]:
    """Wire settings → theme → main window against an existing ``QApplication``.

    Split out from :func:`run` so tests can build the shell under pytest-qt's
    offscreen ``QApplication`` and inject an isolated :class:`SettingsStore`.
    """
    store = settings or SettingsStore()
    theme = ThemeController(app, mode=store.theme_mode())
    theme.apply()
    shell = AppShell(store, theme)
    return shell, theme


def _get_or_create_app(argv: list[str]) -> QApplication:
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    app = QApplication(argv)
    app.setOrganizationName(ORGANIZATION)
    app.setApplicationName(APPLICATION)
    return app


def run(argv: list[str] | None = None) -> int:
    """Launch the desktop application and run the Qt event loop to completion."""
    argv = list(sys.argv if argv is None else argv)
    app = _get_or_create_app(argv)
    shell, _theme = build_shell(app)
    # Wire the Workspace Home data adapter and kick off the first async load
    # (D2). Import here so importing this module constructs no ExecutionCenterAPI
    # or runtime database at import time (keeps the D1A smoke-test contract).
    from command_center.application.workspace_home_adapter import WorkspaceHomeAdapter

    shell.load_workspace_home(WorkspaceHomeAdapter())
    shell.show()
    return app.exec()
