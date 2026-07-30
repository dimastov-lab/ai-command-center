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
_MAX_VISIBLE_ITEMS = 5
_WRAP_CHUNK = 24


def _wrap_identifier(value: str) -> str:
    return "\u200b".join(
        value[index : index + _WRAP_CHUNK]
        for index in range(0, len(value), _WRAP_CHUNK)
    )


def _display_items(values: list[str] | tuple[str, ...]) -> str:
    visible = [_wrap_identifier(value) for value in values[:_MAX_VISIBLE_ITEMS]]
    if len(values) > _MAX_VISIBLE_ITEMS:
        visible.append(f"ещё {len(values) - _MAX_VISIBLE_ITEMS}")
    return ", ".join(visible) or "не подтверждены"


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
                items=_display_items(status.get("capabilities") or ())
            )
        )
        capabilities.setObjectName("RowMeta")
        capabilities.setWordWrap(True)
        layout.addWidget(capabilities)
        gates = QLabel(
            i18n.AIOS_CORE_GATES.format(
                items=_display_items(status.get("gates") or ())
            )
        )
        gates.setObjectName("RowMeta")
        gates.setWordWrap(True)
        layout.addWidget(gates)
        evidence = QLabel(
            i18n.AIOS_CORE_EVIDENCE.format(
                items=_display_items(status.get("evidence") or ())
            )
        )
        evidence.setObjectName("RowMeta")
        evidence.setWordWrap(True)
        layout.addWidget(evidence)
        if status.get("detail"):
            detail = QLabel(str(status["detail"]))
            detail.setObjectName("RowMeta")
            detail.setWordWrap(True)
            layout.addWidget(detail)
        self.setAccessibleName(f"{i18n.AIOS_CORE_TITLE}: {state_text}")
        description = f"{source.text()}. {health.text()}. {capabilities.text()}. {gates.text()}."
        self.setAccessibleDescription(description[:1_024])

    def apply_palette(self, palette: Palette) -> None:
        self.status_badge.apply_palette(palette)
