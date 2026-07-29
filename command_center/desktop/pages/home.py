"""Home page — cross-project rollup (native Workspace Home).

Placeholder in Desktop Increment 1: `DESKTOP_INCREMENT_1.md` §2 forbids real data
wiring here, which lands in D2 via ``command_center.application``'s Workspace Home
adapter. The page states this honestly through an :class:`EmptyState` and offers a
navigation-only next action to Projects (`INFORMATION_ARCHITECTURE.md` §11).
"""

from __future__ import annotations

from PySide6.QtCore import Signal

from .. import i18n
from ..components.empty_state import EmptyState
from .base_page import BasePage


class HomePage(BasePage):
    navigate_requested = Signal(str)  # target section key

    def __init__(self, parent=None) -> None:
        super().__init__(
            "home",
            i18n.HOME_TITLE,
            i18n.HOME_SUBTITLE,
            parent,
        )
        empty = EmptyState(
            i18n.HOME_EMPTY_TITLE,
            i18n.HOME_EMPTY_BODY,
            action_label=i18n.HOME_EMPTY_ACTION,
            on_action=lambda: self.navigate_requested.emit("projects"),
        )
        self.add_content(empty, stretch=1)
