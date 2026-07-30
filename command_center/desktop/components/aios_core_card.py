"""Read-only AIOS Core status card for Workspace Home."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .. import i18n, tokens


class AIOSCoreCard(QWidget):
    def __init__(self, status: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AIOSCoreCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            tokens.SPACE_MD, tokens.SPACE_MD, tokens.SPACE_MD, tokens.SPACE_MD
        )
        layout.setSpacing(tokens.SPACE_XS)

        title = QLabel(i18n.AIOS_CORE_TITLE)
        title.setObjectName("SectionTitle")
        layout.addWidget(title)
        state = QLabel(i18n.aios_readiness_label(status.get("readiness")))
        state.setObjectName("StatusBadge")
        layout.addWidget(state)
        source = QLabel(
            i18n.AIOS_CORE_SOURCE.format(
                source=i18n.aios_source_label(status.get("source"))
            )
        )
        source.setObjectName("RowMeta")
        layout.addWidget(source)

        if status.get("version"):
            version = QLabel(i18n.AIOS_CORE_VERSION.format(version=status["version"]))
            version.setObjectName("RowMeta")
            layout.addWidget(version)
        for text in status.get("evidence") or ():
            evidence = QLabel(str(text))
            evidence.setObjectName("RowMeta")
            evidence.setWordWrap(True)
            layout.addWidget(evidence)
        if status.get("detail"):
            detail = QLabel(str(status["detail"]))
            detail.setObjectName("RowMeta")
            detail.setWordWrap(True)
            layout.addWidget(detail)
