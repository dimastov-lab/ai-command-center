"""Read-only provider availability projection; never launches providers."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .. import i18n, tokens


class ProviderCapabilitiesCard(QWidget):
    def __init__(self, providers: list[dict], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ProviderCapabilitiesCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            tokens.SPACE_MD, tokens.SPACE_MD, tokens.SPACE_MD, tokens.SPACE_MD
        )
        layout.setSpacing(tokens.SPACE_XS)
        title = QLabel(i18n.PROVIDERS_TITLE)
        title.setObjectName("SectionTitle")
        layout.addWidget(title)
        for provider in providers:
            text = (
                f"{provider['display_name']} — "
                f"{i18n.provider_readiness_label(provider.get('readiness'))}; "
                f"источник: {provider.get('provenance') or 'не указан'}"
            )
            if provider.get("detail"):
                text += f"; {provider['detail']}"
            row = QLabel(text)
            row.setObjectName("RowMeta")
            row.setWordWrap(True)
            layout.addWidget(row)
        self.setAccessibleName(i18n.PROVIDERS_TITLE)
        self.setAccessibleDescription(i18n.PROVIDERS_ACCESSIBLE_DESCRIPTION)
