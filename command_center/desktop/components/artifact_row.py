"""``ArtifactRow`` — one generated-artifact entry (D2C, §7).

Shows task_type + created_at, and a path only when the snapshot carries one. For a
sensitive project the ``path`` key is absent (stripped upstream, §10), so the row
simply has no path to show — it never fabricates or reconstructs one.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QWidget

from .. import i18n, tokens


class ArtifactRow(QFrame):
    def __init__(self, artifact: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ArtifactRow")
        self.setFocusPolicy(Qt.StrongFocus)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(
            tokens.SPACE_MD, tokens.SPACE_XS, tokens.SPACE_MD, tokens.SPACE_XS
        )
        layout.setSpacing(tokens.SPACE_MD)

        summary_parts = [
            str(p)
            for p in (
                i18n.task_type_label(artifact.get("task_type")),
                artifact.get("created_at"),
            )
            if p not in (None, "")
        ]
        self._summary = QLabel(" · ".join(summary_parts))
        self._summary.setObjectName("RowTitle")
        layout.addWidget(self._summary)

        path = artifact.get("path")
        self.has_path: bool = "path" in artifact and path is not None
        self._path_label: QLabel | None = None
        if self.has_path:
            self._path_label = QLabel(str(path))
            self._path_label.setObjectName("RowMeta")
            self._path_label.setMinimumWidth(0)
            self._path_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            self._path_label.setToolTip(self._path_label.text())
            layout.addStretch(1)
            layout.addWidget(self._path_label, 1)
        visible_parts = [str(artifact.get("project") or ""), self._summary.text()]
        if self._path_label is not None:
            visible_parts.append(self._path_label.text())
        self.setAccessibleName(", ".join(part for part in visible_parts if part))

    def summary_text(self) -> str:
        return self._summary.text()

    def path_text(self) -> str | None:
        return self._path_label.text() if self._path_label is not None else None
