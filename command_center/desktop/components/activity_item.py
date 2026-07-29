"""``ActivityItem`` — one recent-activity event row (D2C, §6).

Shows a Russian event description + project + timestamp, all read defensively.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from .. import i18n, tokens


def activity_label(event_type: str | None) -> str:
    """Russian label for an activity ``event_type`` (falls back to the raw type)."""
    return i18n.ACTIVITY_EVENT_LABELS.get(event_type, event_type or "")


class ActivityItem(QFrame):
    def __init__(self, event: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ActivityItem")
        self.setFocusPolicy(Qt.StrongFocus)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(
            tokens.SPACE_MD, tokens.SPACE_XS, tokens.SPACE_MD, tokens.SPACE_XS
        )
        layout.setSpacing(tokens.SPACE_MD)

        self._desc = QLabel(activity_label(event.get("event_type")))
        self._desc.setObjectName("RowTitle")
        detail_parts = [str(p) for p in (event.get("project"), event.get("ts")) if p]
        self._detail = QLabel(" · ".join(detail_parts))
        self._detail.setObjectName("RowMeta")

        layout.addWidget(self._desc)
        layout.addStretch(1)
        layout.addWidget(self._detail)
        self.setAccessibleName(
            ", ".join(part for part in (self._detail.text(), self._desc.text()) if part)
        )

    def description_text(self) -> str:
        return self._desc.text()

    def detail_text(self) -> str:
        return self._detail.text()
