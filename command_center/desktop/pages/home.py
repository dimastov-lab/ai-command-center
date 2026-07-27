"""Home page — cross-project rollup (native Workspace Home).

Placeholder in Desktop Increment 1: `DESKTOP_INCREMENT_1.md` §2 forbids real data
wiring here, which lands in D2 via ``command_center.application``'s Workspace Home
adapter. The page states this honestly through an :class:`EmptyState` and offers a
navigation-only next action to Projects (`INFORMATION_ARCHITECTURE.md` §11).
"""

from __future__ import annotations

from PySide6.QtCore import Signal

from ..components.empty_state import EmptyState
from .base_page import BasePage


class HomePage(BasePage):
    navigate_requested = Signal(str)  # target section key

    def __init__(self, parent=None) -> None:
        super().__init__(
            "home",
            "Home",
            "Cross-project rollup of projects, runs, and activity.",
            parent,
        )
        empty = EmptyState(
            "Workspace Home is not wired yet",
            "Your cross-project rollup — projects, active runs, recent activity, "
            "artifacts and reports — will appear here. Live data wiring lands in a "
            "later increment. For now, configure a project to get started.",
            action_label="Go to Projects",
            on_action=lambda: self.navigate_requested.emit("projects"),
        )
        self.add_content(empty, stretch=1)
