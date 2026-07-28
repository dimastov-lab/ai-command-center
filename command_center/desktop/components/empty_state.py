"""``EmptyState`` — shown when a page/section has no data to display.

Implements `DESIGN_SYSTEM.md` §7.15. In Desktop Increment 1 the three active
pages are placeholders, so each uses an ``EmptyState`` to say — honestly, with no
fabricated data — what it will show and when its data wiring lands. The optional
action button navigates elsewhere (e.g. Home → Projects); its accessible name is
exposed, and the message text is not focusable.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from .. import tokens


class EmptyState(QWidget):
    action_triggered = Signal()

    def __init__(
        self,
        title: str,
        body: str,
        *,
        action_label: str | None = None,
        on_action: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            tokens.SPACE_XL, tokens.SPACE_XL, tokens.SPACE_XL, tokens.SPACE_XL
        )
        layout.setSpacing(tokens.SPACE_MD)
        layout.setAlignment(Qt.AlignHCenter | Qt.AlignTop)

        title_label = QLabel(title)
        title_label.setObjectName("EmptyStateTitle")
        title_label.setAccessibleName(title)
        title_label.setAlignment(Qt.AlignHCenter)
        layout.addWidget(title_label)

        body_label = QLabel(body)
        body_label.setObjectName("EmptyStateBody")
        body_label.setWordWrap(True)
        body_label.setAlignment(Qt.AlignHCenter)
        body_label.setMaximumWidth(520)
        layout.addWidget(body_label, alignment=Qt.AlignHCenter)

        self.action_button: QPushButton | None = None
        if action_label:
            button = QPushButton(action_label)
            button.setObjectName("EmptyStateAction")
            button.setAccessibleName(action_label)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(self.action_triggered.emit)
            if on_action is not None:
                button.clicked.connect(on_action)
            layout.addWidget(button, alignment=Qt.AlignHCenter)
            self.action_button = button
