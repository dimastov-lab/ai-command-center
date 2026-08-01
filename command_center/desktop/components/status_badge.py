"""``StatusBadge`` — the shared status pill (`DESIGN_SYSTEM.md` §7.9).

A small, rounded label whose colour is the current palette's semantic
``status_*`` token for its :class:`StatusVariant`. Following the design system's
border-forward elevation language (§1.7), the badge is a coloured outline + text
on a transparent background rather than a filled chip, so it stays legible in both
themes. Colour comes from the applied :class:`~command_center.desktop.tokens.Palette`
via :func:`status_color`, so the badge re-themes with everything else.
"""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QWidget

from .. import tokens
from ..tokens import Palette


class StatusVariant(str, Enum):
    """Semantic status of a badge — one per ``status_*`` token in the palette."""

    NEUTRAL = "neutral"
    INFO = "info"
    ACTIVE = "active"
    SUCCESS = "success"
    WARNING = "warning"
    DANGER = "danger"
    CANCELLED = "cancelled"
    SENSITIVE = "sensitive"


def status_color(variant: StatusVariant, palette: Palette) -> str:
    """The palette colour for ``variant`` (pure; no widget required)."""
    return getattr(palette, f"status_{variant.value}")


class StatusBadge(QLabel):
    """A themed status pill carrying a short label and a semantic variant."""

    def __init__(
        self, text: str, variant: StatusVariant, parent: QWidget | None = None
    ) -> None:
        super().__init__(text, parent)
        self.setObjectName("StatusBadge")
        self._variant = variant
        self._palette: Palette | None = None
        self.setAlignment(Qt.AlignCenter)
        self.setFixedHeight(tokens.CONTROL_HEIGHT_SM)
        # The visible label is also the accessible name (§4) — screen-reader users
        # get the same status word sighted users read.
        self.setAccessibleName(text)

    @property
    def variant(self) -> StatusVariant:
        return self._variant

    def set_variant(self, variant: StatusVariant) -> None:
        self._variant = variant
        self._restyle()

    def apply_palette(self, palette: Palette) -> None:
        """Colour the badge from ``palette``; call again on theme change."""
        self._palette = palette
        self._restyle()

    def _restyle(self) -> None:
        if self._palette is None:
            return
        color = status_color(self._variant, self._palette)
        self.setStyleSheet(
            f"#StatusBadge {{"
            f" color: {color};"
            f" border: {tokens.BORDER_HAIRLINE_PX}px solid {color};"
            f" border-radius: {tokens.RADIUS_SM}px;"
            f" padding: 0px {tokens.SPACE_SM}px;"
            f" font-size: {tokens.TYPE_CAPTION_PT}pt;"
            f" background-color: transparent;"
            f" }}"
        )
