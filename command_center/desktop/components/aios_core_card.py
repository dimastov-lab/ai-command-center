"""Read-only AIOS Core status card for Workspace Home."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .. import i18n, tokens
from ..tokens import Palette
from .status_badge import StatusBadge, StatusVariant

_VARIANTS = {
    "ready": StatusVariant.SUCCESS,
    "not_ready": StatusVariant.WARNING,
    "offline": StatusVariant.DANGER,
    "error": StatusVariant.DANGER,
    "contract_pending": StatusVariant.INFO,
}


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
        readiness = str(status.get("readiness") or "")
        state_text = i18n.aios_readiness_label(readiness)
        self.status_badge = StatusBadge(
            state_text, _VARIANTS.get(readiness, StatusVariant.NEUTRAL)
        )
        layout.addWidget(self.status_badge)
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
        health = QLabel(
            i18n.AIOS_CORE_HEALTH.format(
                health=i18n.aios_health_label(status.get("health"))
            )
        )
        health.setObjectName("RowMeta")
        layout.addWidget(health)
        capabilities = QLabel(
            i18n.AIOS_CORE_CAPABILITIES.format(
                items=", ".join(status.get("capabilities") or ()) or "не подтверждены"
            )
        )
        capabilities.setObjectName("RowMeta")
        capabilities.setWordWrap(True)
        layout.addWidget(capabilities)
        gates = QLabel(
            i18n.AIOS_CORE_GATES.format(
                items=", ".join(status.get("gates") or ()) or "не подтверждены"
            )
        )
        gates.setObjectName("RowMeta")
        gates.setWordWrap(True)
        layout.addWidget(gates)
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
        self.setAccessibleName(f"{i18n.AIOS_CORE_TITLE}: {state_text}")
        self.setAccessibleDescription(
            f"{source.text()}. {health.text()}. {capabilities.text()}. {gates.text()}."
        )

    def apply_palette(self, palette: Palette) -> None:
        self.status_badge.apply_palette(palette)
