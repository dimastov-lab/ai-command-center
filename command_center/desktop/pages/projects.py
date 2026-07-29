"""Projects page for repository-path configuration (Desktop D3A).

Presentation code owns only widgets and interaction flow.  Loading, validation,
and persistence are delegated to :class:`ProjectsAdapter`, which in turn wraps
the canonical :mod:`command_center.project_config` functions.
"""

from __future__ import annotations

import logging
from typing import Protocol

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from command_center.application.projects_adapter import ProjectsAdapter

from .. import i18n, tokens
from ..components.empty_state import EmptyState
from .base_page import BasePage

logger = logging.getLogger(__name__)


class ProjectsAdapterProtocol(Protocol):
    def list_projects(self) -> dict[str, dict]: ...

    def validate_repository_path(self, path: str) -> tuple[bool, str]: ...

    def save_repository_path(self, project_id: str, path: str | None) -> None: ...


class ProjectPathEditor(QFrame):
    """Editor for one canonical project's repository path."""

    def __init__(
        self,
        project: dict,
        adapter: ProjectsAdapterProtocol,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.project_id = str(project["id"])
        self._adapter = adapter
        display_name = i18n.project_display_name(
            self.project_id, str(project.get("display_name") or self.project_id)
        )
        self.setAccessibleName(i18n.project_editor_accessible_name(display_name))

        root = QVBoxLayout(self)
        root.setContentsMargins(
            tokens.SPACE_LG, tokens.SPACE_LG, tokens.SPACE_LG, tokens.SPACE_LG
        )
        root.setSpacing(tokens.SPACE_SM)

        title = QLabel(display_name)
        title.setObjectName("CardTitle")
        root.addWidget(title)

        path_label = QLabel(i18n.PROJECTS_PATH_LABEL)
        path_label.setObjectName("RowMeta")
        root.addWidget(path_label)

        self.path_edit = QLineEdit()
        self.path_edit.setObjectName(f"ProjectPath_{self.project_id}")
        self.path_edit.setPlaceholderText(i18n.PROJECTS_PATH_PLACEHOLDER)
        self.path_edit.setAccessibleName(i18n.project_path_accessible_name(display_name))
        self.path_edit.setClearButtonEnabled(True)
        self.path_edit.setText(str(project.get("repository_path") or ""))
        self.path_edit.returnPressed.connect(self._save)
        root.addWidget(self.path_edit)

        actions = QHBoxLayout()
        self.save_button = QPushButton(i18n.PROJECTS_SAVE_ACTION)
        self.save_button.setAccessibleName(i18n.project_save_accessible_name(display_name))
        self.save_button.setCursor(Qt.PointingHandCursor)
        self.save_button.clicked.connect(self._save)
        actions.addWidget(self.save_button)

        self.clear_button = QPushButton(i18n.PROJECTS_CLEAR_ACTION)
        self.clear_button.setAccessibleName(i18n.project_clear_accessible_name(display_name))
        self.clear_button.setCursor(Qt.PointingHandCursor)
        self.clear_button.setEnabled(bool(self.path_edit.text()))
        self.clear_button.clicked.connect(self._clear)
        actions.addWidget(self.clear_button)
        actions.addStretch(1)
        root.addLayout(actions)

        self.status_label = QLabel(
            "" if self.path_edit.text() else i18n.PROJECTS_PATH_NOT_CONFIGURED
        )
        self.status_label.setObjectName("RowMeta")
        self.status_label.setWordWrap(True)
        self.status_label.setAccessibleName(self.status_label.text())
        root.addWidget(self.status_label)

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)
        self.status_label.setAccessibleName(text)

    def _save(self) -> None:
        candidate = self.path_edit.text().strip()
        valid, message = self._adapter.validate_repository_path(candidate)
        if not valid:
            self._set_status(message)
            self.path_edit.setFocus(Qt.OtherFocusReason)
            return
        try:
            self._adapter.save_repository_path(self.project_id, candidate)
        except (OSError, ValueError):
            logger.exception("Could not save repository path for %s", self.project_id)
            self._set_status(i18n.PROJECTS_SAVE_ERROR)
            return
        self.path_edit.setText(candidate)
        self.clear_button.setEnabled(True)
        self._set_status(i18n.PROJECTS_PATH_SAVED)

    def _clear(self) -> None:
        try:
            self._adapter.save_repository_path(self.project_id, None)
        except (OSError, ValueError):
            logger.exception("Could not clear repository path for %s", self.project_id)
            self._set_status(i18n.PROJECTS_SAVE_ERROR)
            return
        self.path_edit.clear()
        self.clear_button.setEnabled(False)
        self._set_status(i18n.PROJECTS_PATH_CLEARED)


class ProjectsPage(BasePage):
    def __init__(
        self,
        adapter: ProjectsAdapterProtocol | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            "projects",
            i18n.PROJECTS_TITLE,
            i18n.PROJECTS_SUBTITLE,
            parent,
        )
        self._adapter = adapter or ProjectsAdapter()
        self._editors: dict[str, ProjectPathEditor] = {}
        self._build_content()

    def _build_content(self) -> None:
        try:
            projects = self._adapter.list_projects()
        except (OSError, ValueError):
            logger.exception("Could not load project configuration")
            self.add_content(
                EmptyState(
                    i18n.PROJECTS_LOAD_ERROR_TITLE,
                    i18n.PROJECTS_LOAD_ERROR_BODY,
                ),
                stretch=1,
            )
            return

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(tokens.SPACE_MD)
        for project_id, project in projects.items():
            editor = ProjectPathEditor(project, self._adapter)
            self._editors[project_id] = editor
            layout.addWidget(editor)
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setObjectName("ProjectsScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(content)
        self.add_content(scroll, stretch=1)

    def editors(self) -> dict[str, ProjectPathEditor]:
        """Return a copy of the editor registry for testing and diagnostics."""
        return dict(self._editors)
