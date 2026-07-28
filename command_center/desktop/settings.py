"""Minimal ``QSettings``-backed preference store for the D1 shell.

This is the deliberate, documented stand-in for the full
``command_center.platform`` settings contract, which lands at D3
(`IMPLEMENTATION_ROADMAP.md` D1B/D3B). It persists exactly the three things the
shell owns per `ARCHITECTURE.md` §14–§15: window geometry, theme mode, and the
window-scoped selected-project preference — and introduces no new data file
beyond the platform-native settings surface (`ARCHITECTURE.md` §15).

A ``QSettings`` instance may be injected (tests pass an isolated ``IniFormat``
file) so no test ever writes to the real user/registry settings.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QSettings

from .theme import ThemeMode

ORGANIZATION = "AI Command Center"
APPLICATION = "AI Command Center Desktop"

_KEY_GEOMETRY = "window/geometry"
_KEY_WINDOW_STATE = "window/state"
_KEY_THEME = "appearance/theme"
_KEY_SELECTED_PROJECT = "workspace/selected_project"


class SettingsStore:
    """Typed accessor over a ``QSettings`` handle. Owns no Qt widgets."""

    def __init__(self, settings: QSettings | None = None) -> None:
        # Scope by organization/application once (mirrors the identifier the real
        # platform contract will set at QApplication construction).
        self._settings = settings or QSettings(ORGANIZATION, APPLICATION)

    @property
    def qsettings(self) -> QSettings:
        return self._settings

    # --- Theme -------------------------------------------------------------
    def theme_mode(self, default: ThemeMode = ThemeMode.SYSTEM) -> ThemeMode:
        return ThemeMode.from_value(self._settings.value(_KEY_THEME), default)

    def set_theme_mode(self, mode: ThemeMode) -> None:
        self._settings.setValue(_KEY_THEME, mode.value)

    # --- Window geometry ---------------------------------------------------
    def geometry(self) -> QByteArray | None:
        value = self._settings.value(_KEY_GEOMETRY)
        return value if isinstance(value, QByteArray) and not value.isEmpty() else None

    def set_geometry(self, geometry: QByteArray) -> None:
        self._settings.setValue(_KEY_GEOMETRY, geometry)

    def window_state(self) -> QByteArray | None:
        value = self._settings.value(_KEY_WINDOW_STATE)
        return value if isinstance(value, QByteArray) and not value.isEmpty() else None

    def set_window_state(self, state: QByteArray) -> None:
        self._settings.setValue(_KEY_WINDOW_STATE, state)

    # --- Selected project (window-scoped UI preference) --------------------
    def selected_project(self) -> str | None:
        value = self._settings.value(_KEY_SELECTED_PROJECT)
        return str(value) if value not in (None, "") else None

    def set_selected_project(self, project_id: str | None) -> None:
        if project_id:
            self._settings.setValue(_KEY_SELECTED_PROJECT, project_id)
        else:
            self._settings.remove(_KEY_SELECTED_PROJECT)

    def sync(self) -> None:
        """Flush pending writes to the backing store (called on clean shutdown)."""
        self._settings.sync()
