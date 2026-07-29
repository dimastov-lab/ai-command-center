"""Projects page — per-project repository/worktree state and path configuration.

Placeholder in Desktop Increment 1: repository-path configuration and per-project
detail land in D3 via ``command_center.application``'s Projects adapter (wrapping
the existing ``project_config`` functions verbatim). D1 states this honestly.
"""

from __future__ import annotations

from .. import i18n
from ..components.empty_state import EmptyState
from .base_page import BasePage


class ProjectsPage(BasePage):
    def __init__(self, parent=None) -> None:
        super().__init__(
            "projects",
            i18n.PROJECTS_TITLE,
            i18n.PROJECTS_SUBTITLE,
            parent,
        )
        empty = EmptyState(
            i18n.PROJECTS_EMPTY_TITLE,
            i18n.PROJECTS_EMPTY_BODY,
        )
        self.add_content(empty, stretch=1)
