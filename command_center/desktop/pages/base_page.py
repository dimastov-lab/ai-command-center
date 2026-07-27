"""``BasePage`` — common page scaffold: a :class:`PageHeader` over a content area.

Every section page subclasses this so the header/margins are consistent and the
shell can treat pages uniformly. Pages hold no business logic and reach no core
module directly (`ARCHITECTURE.md` §4) — in D1 they render placeholders only.
"""

from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget

from .. import tokens
from ..components.page_header import PageHeader


class BasePage(QWidget):
    def __init__(
        self, section_key: str, title: str, description: str = "", parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.section_key = section_key
        self.setAccessibleName(f"{title} page")

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(
            tokens.SPACE_XXL, tokens.SPACE_XL, tokens.SPACE_XXL, tokens.SPACE_XL
        )
        self._layout.setSpacing(tokens.SPACE_LG)

        self.header = PageHeader(title, description)
        self._layout.addWidget(self.header)

    def add_content(self, widget: QWidget, *, stretch: int = 0) -> None:
        self._layout.addWidget(widget, stretch)

    def add_stretch(self) -> None:
        self._layout.addStretch(1)
