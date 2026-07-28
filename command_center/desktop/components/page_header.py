"""``PageHeader`` — page title plus optional short description.

Implements `DESIGN_SYSTEM.md` §7.6. The title carries heading accessibility
semantics; action hosting is added when the first page that needs actions lands
(Projects, D3).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .. import tokens


class PageHeader(QWidget):
    def __init__(self, title: str, description: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(tokens.SPACE_XS)

        self._title = QLabel(title)
        self._title.setObjectName("PageTitle")
        # A QLabel exposes its text as its accessible name; promoting its role to
        # a true "Heading" needs a custom QAccessibleInterface, deferred to the
        # accessibility pass in a later increment (`DESIGN_SYSTEM.md` §7.6).
        self._title.setAccessibleName(title)
        layout.addWidget(self._title)

        self._description = QLabel(description)
        self._description.setObjectName("PageDescription")
        self._description.setWordWrap(True)
        self._description.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._description.setVisible(bool(description))
        layout.addWidget(self._description)

    @property
    def title_label(self) -> QLabel:
        return self._title
