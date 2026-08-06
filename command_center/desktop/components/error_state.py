"""``ErrorState`` — shown when a page/section fails to load.

Implements `DESIGN_SYSTEM.md §7.16`. Raises ``QAccessible::Alert`` on first
show so screen readers announce the error without the user navigating to it.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QAccessibleEvent, QAccessible
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from .. import tokens


class ErrorState(QWidget):
    def __init__(
        self,
        message: str,
        retry_callback: Callable[[], None] | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ErrorState")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(tokens.SPACE_XL, tokens.SPACE_XL,
                                  tokens.SPACE_XL, tokens.SPACE_XL)
        layout.setSpacing(tokens.SPACE_MD)
        layout.setAlignment(Qt.AlignHCenter | Qt.AlignTop)

        msg_label = QLabel(message)
        msg_label.setWordWrap(True)
        msg_label.setAlignment(Qt.AlignHCenter)
        layout.addWidget(msg_label)

        self.retry_button: QPushButton | None = None
        if retry_callback is not None:
            btn = QPushButton("Retry")
            btn.setObjectName("ErrorStateRetry")
            btn.setAccessibleName("Retry")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(retry_callback)
            layout.addWidget(btn, alignment=Qt.AlignHCenter)
            self.retry_button = btn

        self.setAccessibleName(message)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QAccessible.updateAccessibility(
            QAccessibleEvent(self, QAccessible.Event.Alert)
        )
