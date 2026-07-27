"""Projects page — per-project repository/worktree state and path configuration.

Placeholder in Desktop Increment 1: repository-path configuration and per-project
detail land in D3 via ``command_center.application``'s Projects adapter (wrapping
the existing ``project_config`` functions verbatim). D1 states this honestly.
"""

from __future__ import annotations

from ..components.empty_state import EmptyState
from .base_page import BasePage


class ProjectsPage(BasePage):
    def __init__(self, parent=None) -> None:
        super().__init__(
            "projects",
            "Projects",
            "Repository status, worktrees, and per-project configuration.",
            parent,
        )
        empty = EmptyState(
            "Project configuration is not wired yet",
            "Viewing and editing each project's repository path, and its worktree "
            "and repository state, will appear here. This activates in a later "
            "increment, backed by the existing project configuration service.",
        )
        self.add_content(empty, stretch=1)
