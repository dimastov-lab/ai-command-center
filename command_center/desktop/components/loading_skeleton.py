"""Accessible, non-interactive loading placeholder for desktop sections."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QVBoxLayout, QWidget

from .. import i18n, tokens


class LoadingSkeleton(QWidget):
    """A stable-height placeholder removed when data or an error arrives.

    Qt Widgets does not expose a public setter for ``QAccessible::State::busy``.
    The region therefore exposes the equivalent state through its accessible
    name/description and a ``busy`` dynamic property that platform adapters and
    tests can inspect.
    """

    def __init__(
        self,
        *,
        row_count: int = 5,
        row_height: int = tokens.CONTROL_HEIGHT_LG,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("LoadingSkeleton")
        self.setFocusPolicy(Qt.NoFocus)
        self.setAccessibleName(i18n.HOME_LOADING)
        self.setAccessibleDescription(i18n.HOME_LOADING_ACCESSIBLE_DESCRIPTION)
        self.setProperty("busy", True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(tokens.SPACE_SM)
        self._rows: list[QFrame] = []
        for _ in range(max(1, row_count)):
            row = QFrame()
            row.setObjectName("LoadingSkeletonRow")
            row.setFixedHeight(row_height)
            row.setFocusPolicy(Qt.NoFocus)
            layout.addWidget(row)
            self._rows.append(row)

    def row_heights(self) -> list[int]:
        return [row.height() for row in self._rows]
