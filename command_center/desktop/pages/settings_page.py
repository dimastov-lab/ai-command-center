"""Settings page — theme selection (D1) plus a placeholder for the rest.

Theme is the one preference in scope for Desktop Increment 1
(`DESKTOP_INCREMENT_1.md` §2 non-goals: "no settings beyond theme/window
geometry"), and Settings is theme's eventual home (`DESIGN_SYSTEM.md` §7.20).
The page owns no persistence: it emits :attr:`theme_mode_changed`, and the shell
applies + persists the choice. Density, window-geometry reset, and workspace
preferences arrive with the full ``SettingsForm`` in D3.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QButtonGroup, QGroupBox, QRadioButton, QVBoxLayout

from .. import tokens
from ..components.empty_state import EmptyState
from ..theme import ThemeMode
from .base_page import BasePage

_MODE_LABELS: tuple[tuple[ThemeMode, str], ...] = (
    (ThemeMode.LIGHT, "Light"),
    (ThemeMode.DARK, "Dark"),
    (ThemeMode.SYSTEM, "System (follow OS appearance)"),
)


class SettingsPage(BasePage):
    theme_mode_changed = Signal(object)  # emits ThemeMode

    def __init__(self, current_mode: ThemeMode, parent=None) -> None:
        super().__init__(
            "settings",
            "Settings",
            "Appearance, window, and workspace preferences.",
            parent,
        )

        appearance = QGroupBox("Appearance")
        appearance.setAccessibleName("Appearance settings")
        box = QVBoxLayout(appearance)
        box.setContentsMargins(
            tokens.SPACE_LG, tokens.SPACE_LG, tokens.SPACE_LG, tokens.SPACE_LG
        )
        box.setSpacing(tokens.SPACE_SM)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[ThemeMode, QRadioButton] = {}
        for mode, label in _MODE_LABELS:
            radio = QRadioButton(label)
            radio.setAccessibleName(f"{label} theme")
            radio.setChecked(mode is current_mode)
            radio.toggled.connect(
                lambda checked, m=mode: checked and self.theme_mode_changed.emit(m)
            )
            self._group.addButton(radio)
            self._buttons[mode] = radio
            box.addWidget(radio)

        self.add_content(appearance)

        placeholder = EmptyState(
            "More preferences are coming",
            "Density, window-geometry reset, and workspace preferences will appear "
            "here in a later increment.",
        )
        self.add_content(placeholder, stretch=1)

    def set_mode(self, mode: ThemeMode) -> None:
        """Reflect an externally-applied mode without re-emitting the change."""
        button = self._buttons.get(mode)
        if button is not None:
            button.blockSignals(True)
            button.setChecked(True)
            button.blockSignals(False)

    def buttons(self) -> dict[ThemeMode, QRadioButton]:
        return dict(self._buttons)
