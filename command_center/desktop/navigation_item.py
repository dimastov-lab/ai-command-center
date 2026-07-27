"""``NavigationItem`` — one selectable (or disabled) sidebar entry.

Implements `DESIGN_SYSTEM.md` §7.3. States: default / hovered / selected /
disabled (styled via the ``NavigationItem`` selector in
:func:`command_center.desktop.theme.build_stylesheet`). Disabled items are inert
(ignore clicks) and skipped in Tab order (`INFORMATION_ARCHITECTURE.md` §2.1).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton

from . import tokens

DISABLED_TOOLTIP = "Available in a future release"


class NavigationItem(QPushButton):
    """A checkable, left-aligned sidebar button carrying a section key.

    Enabled items are Tab-focusable and activate on click/Enter/Space (standard
    ``QAbstractButton`` behaviour — not reimplemented). Disabled items set
    ``Qt.NoFocus`` so keyboard Tab traversal skips them, and expose their
    disabled meaning to assistive tech via the accessible description.
    """

    def __init__(self, section_key: str, label: str, *, enabled: bool = True) -> None:
        super().__init__(label)
        self.section_key = section_key
        self.setObjectName("NavigationItem")
        self.setCheckable(True)
        self.setMinimumHeight(tokens.CONTROL_HEIGHT_LG)
        self.setCursor(Qt.PointingHandCursor if enabled else Qt.ArrowCursor)

        # Accessibility (§4): the label is the accessible name; disabled state is
        # mirrored from the visible tooltip so screen-reader users get the same
        # information sighted users get.
        self.setAccessibleName(label)
        self.setEnabled(enabled)
        if not enabled:
            self.setFocusPolicy(Qt.NoFocus)
            self.setToolTip(DISABLED_TOOLTIP)
            self.setAccessibleDescription(DISABLED_TOOLTIP)
        else:
            self.setFocusPolicy(Qt.StrongFocus)
