"""Compatibility import for the D3 platform-native preference store."""

from command_center.platform.preferences import (  # noqa: F401
    APPLICATION,
    ORGANIZATION,
    DensityMode,
    SettingsStore,
    ThemeMode,
)

__all__ = [
    "APPLICATION",
    "ORGANIZATION",
    "DensityMode",
    "SettingsStore",
    "ThemeMode",
]
