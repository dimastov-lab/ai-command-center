"""System appearance resolution and live change notification."""

from __future__ import annotations

from typing import Literal

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QGuiApplication

SystemTheme = Literal["light", "dark"]


def system_theme(app: QGuiApplication | None = None) -> SystemTheme:
    application = app or QGuiApplication.instance()
    if not isinstance(application, QGuiApplication):
        return "light"
    return (
        "dark"
        if application.styleHints().colorScheme() is Qt.ColorScheme.Dark
        else "light"
    )


class SystemThemeMonitor(QObject):
    """Qt-backed cross-platform observer for OS appearance changes."""

    theme_changed = Signal(str)

    def __init__(self, app: QGuiApplication, parent: QObject | None = None) -> None:
        super().__init__(parent or app)
        self._app = app
        app.styleHints().colorSchemeChanged.connect(self._on_scheme_changed)

    def current_theme(self) -> SystemTheme:
        return system_theme(self._app)

    def _on_scheme_changed(self, _scheme: Qt.ColorScheme) -> None:
        self.theme_changed.emit(self.current_theme())
