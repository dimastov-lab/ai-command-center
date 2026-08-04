"""Thin application adapter for Desktop D3A project-path configuration.

The adapter deliberately delegates to :mod:`command_center.project_config`
without reimplementing validation or persistence.  It contains no Qt imports,
so it remains independently testable and keeps UI concerns out of the
application layer.
"""

from __future__ import annotations

from command_center import project_config


class ProjectsAdapter:
    """Expose the existing project configuration service to the desktop UI."""

    def list_projects(self) -> dict[str, dict]:
        """Return the canonical project configuration mapping unchanged."""
        return project_config.load_project_configs()

    def validate_repository_path(self, path: str) -> tuple[bool, str]:
        """Delegate repository-path validation to the canonical domain service."""
        return project_config.validate_repository_path(path)

    def save_repository_path(self, project_id: str, path: str | None) -> None:
        """Persist or clear a repository path through the canonical service."""
        project_config.save_repository_path(project_id, path)
