"""Theme resolution, application, and the Settings-page theme control.

Covers `DESKTOP_INCREMENT_1.md` §2's theme-switching criterion and
`DESIGN_SYSTEM.md` §2.
"""

from __future__ import annotations

from PySide6.QtCore import Qt

from command_center.desktop import tokens
from command_center.desktop.theme import ThemeMode, resolve_palette


def test_resolve_palette_light_and_dark_are_pinned():
    assert resolve_palette(ThemeMode.LIGHT, Qt.ColorScheme.Dark) is tokens.LIGHT
    assert resolve_palette(ThemeMode.DARK, Qt.ColorScheme.Light) is tokens.DARK


def test_resolve_palette_system_follows_os_scheme():
    assert resolve_palette(ThemeMode.SYSTEM, Qt.ColorScheme.Dark) is tokens.DARK
    assert resolve_palette(ThemeMode.SYSTEM, Qt.ColorScheme.Light) is tokens.LIGHT
    # Unknown (e.g. offscreen) resolves to Light — the fresh-install-safe default.
    assert resolve_palette(ThemeMode.SYSTEM, Qt.ColorScheme.Unknown) is tokens.LIGHT


def test_theme_mode_from_value_tolerates_garbage():
    assert ThemeMode.from_value("dark", ThemeMode.SYSTEM) is ThemeMode.DARK
    assert ThemeMode.from_value(None, ThemeMode.SYSTEM) is ThemeMode.SYSTEM
    assert ThemeMode.from_value("nonsense", ThemeMode.LIGHT) is ThemeMode.LIGHT


def test_settings_page_change_applies_palette_and_persists(shell, settings_store, qapp):
    shell.navigate_to("settings")
    settings_page = shell._settings_page

    settings_page.buttons()[ThemeMode.DARK].click()
    # Applied to the live application …
    assert tokens.DARK.bg_base in qapp.styleSheet()
    # … and persisted through the settings store.
    assert settings_store.theme_mode() is ThemeMode.DARK

    settings_page.buttons()[ThemeMode.LIGHT].click()
    assert tokens.LIGHT.bg_base in qapp.styleSheet()
    assert settings_store.theme_mode() is ThemeMode.LIGHT


def test_theme_switch_visibly_changes_palette_token(shell, qapp):
    shell.navigate_to("settings")
    buttons = shell._settings_page.buttons()

    buttons[ThemeMode.LIGHT].click()
    light_sheet = qapp.styleSheet()
    buttons[ThemeMode.DARK].click()
    dark_sheet = qapp.styleSheet()

    assert light_sheet != dark_sheet
    assert tokens.LIGHT.sidebar_bg in light_sheet
    assert tokens.DARK.sidebar_bg in dark_sheet


def test_dark_theme_radio_indicator_has_explicit_high_contrast_states():
    from command_center.desktop.theme import build_stylesheet

    sheet = build_stylesheet(tokens.DARK)
    assert "QRadioButton::indicator" in sheet
    assert f"border: 2px solid {tokens.DARK.text_secondary}" in sheet
    assert "QRadioButton::indicator:checked" in sheet
    assert f"border: 2px solid {tokens.DARK.accent_emphasis}" in sheet
    assert f"background-color: {tokens.DARK.accent}" in sheet
