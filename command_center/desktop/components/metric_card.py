"""``MetricCard`` — one tile of the Workspace Home header strip (D2C, §1).

A large value over a caption label. Generic: the Home page supplies an aggregate
count and an already-Russian caption. Purely presentational — no data access.
"""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QVBoxLayout, QWidget

from .. import tokens


class MetricCard(QFrame):
    def __init__(self, value: object, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("MetricCard")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            tokens.SPACE_LG, tokens.SPACE_MD, tokens.SPACE_LG, tokens.SPACE_MD
        )
        layout.setSpacing(tokens.SPACE_XS)

        self._value = QLabel(str(value))
        self._value.setObjectName("MetricValue")
        self._label = QLabel(label)
        self._label.setObjectName("MetricCaption")
        self._label.setWordWrap(True)
        self._label.setMinimumWidth(0)
        self._label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self._value)
        layout.addWidget(self._label)

        # One combined accessible name so a screen reader announces the metric as
        # a unit ("Активные запуски: 2") rather than two disjoint labels.
        self.setAccessibleName(f"{label}: {value}")

    def value_text(self) -> str:
        return self._value.text()

    def label_text(self) -> str:
        return self._label.text()
