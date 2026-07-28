"""Design tokens for the desktop shell.

Single source of truth for spacing, typography, radii, control heights, and the
light/dark colour palettes, implementing `docs/desktop/DESIGN_SYSTEM.md` §1–§2.
Every value is defined once here and referenced by :mod:`command_center.desktop.theme`
and the widgets, never hardcoded per-widget.

This module is pure data (dataclasses + constants). It imports nothing from Qt or
from ``command_center`` core, so it is trivially unit-testable and carries no
side effects at import time.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- §1.1 Spacing (px @ 1.0 scale) -----------------------------------------
SPACE_XS = 4
SPACE_SM = 8
SPACE_MD = 12
SPACE_LG = 16
SPACE_XL = 24
SPACE_XXL = 32

# --- §1.2 Typography (pt) --------------------------------------------------
TYPE_DISPLAY_PT = 20
TYPE_TITLE_PT = 15
TYPE_BODY_PT = 13
TYPE_CAPTION_PT = 11
TYPE_MONO_PT = 12

# --- §1.3 Control heights (px) ---------------------------------------------
CONTROL_HEIGHT_SM = 24
CONTROL_HEIGHT_MD = 32
CONTROL_HEIGHT_LG = 40
CONTROL_HEIGHT_TOPBAR = 48

# --- §1.4 Corner radii (px) ------------------------------------------------
RADIUS_SM = 4
RADIUS_MD = 8
RADIUS_LG = 12

# --- §1.5 Icon sizes (px) --------------------------------------------------
ICON_SM = 16
ICON_MD = 20
ICON_LG = 32

# --- §1.6 Borders ----------------------------------------------------------
BORDER_HAIRLINE_PX = 1
BORDER_FOCUS_PX = 2


@dataclass(frozen=True)
class Palette:
    """A complete theme palette. Every token in §1.10/§2 has a value here so no
    component can bypass theme switching with a hardcoded colour."""

    name: str
    # Surfaces
    bg_base: str
    surface: str
    surface_raised: str
    sidebar_bg: str
    topbar_bg: str
    # Lines
    border: str
    # Text
    text_primary: str
    text_secondary: str
    text_disabled: str
    # Accent / selection
    accent: str
    accent_emphasis: str
    selected_bg: str
    hover_bg: str
    # §1.10 Semantic status colours
    status_neutral: str
    status_info: str
    status_active: str
    status_success: str
    status_warning: str
    status_danger: str
    status_cancelled: str
    status_sensitive: str


LIGHT = Palette(
    name="light",
    bg_base="#f4f5f8",
    surface="#ffffff",
    surface_raised="#ffffff",
    sidebar_bg="#fbfbfd",
    topbar_bg="#ffffff",
    border="#e1e4ea",
    text_primary="#1a2340",
    text_secondary="#5b6478",
    text_disabled="#9aa1b0",
    accent="#3b5bdb",
    accent_emphasis="#2f4bc0",
    selected_bg="#eaeffd",
    hover_bg="#eef1f6",
    status_neutral="#6b7280",
    status_info="#2563eb",
    status_active="#7c3aed",
    status_success="#15803d",
    status_warning="#b45309",
    status_danger="#b91c1c",
    status_cancelled="#57534e",
    status_sensitive="#9d174d",
)

DARK = Palette(
    name="dark",
    bg_base="#14161c",
    surface="#1b1e26",
    surface_raised="#22262f",
    sidebar_bg="#17191f",
    topbar_bg="#1b1e26",
    border="#2a2e39",
    text_primary="#e8eaf0",
    text_secondary="#a2a9ba",
    text_disabled="#626878",
    accent="#5b7cff",
    accent_emphasis="#7089ff",
    selected_bg="#232a44",
    hover_bg="#21242e",
    status_neutral="#9aa1b0",
    status_info="#60a5fa",
    status_active="#a78bfa",
    status_success="#4ade80",
    status_warning="#fbbf24",
    status_danger="#f87171",
    status_cancelled="#a8a29e",
    status_sensitive="#f472b6",
)
