"""``ProjectCard`` — one project's rollup on the Workspace Home page (D2C).

Renders a single ``snapshot["projects"]`` entry per `WORKSPACE_HOME_SPEC.md`
§2–§3: title, repository-health badge, an optional sensitive badge, the exact
"Tasks" / "Active runs" counts, and a navigation-only "configure repository path"
action shown only while the repository is unconfigured. All redaction is already
applied upstream (§10) — this widget only presents what the snapshot gives it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import i18n, tokens
from ..tokens import Palette
from .status_badge import StatusBadge, StatusVariant

# WORKSPACE_HOME_SPEC.md §3 — the one signal a user has for why a project's
# worktree section is empty. This mapping must not drift from the four states
# `_discover_worktrees` can return.
_HEALTH_VARIANTS: dict[str, StatusVariant] = {
    "unconfigured": StatusVariant.NEUTRAL,
    "invalid_path": StatusVariant.WARNING,
    "not_git_repo": StatusVariant.WARNING,
    "ok": StatusVariant.SUCCESS,
}


def repository_health(state: str) -> tuple[StatusVariant, str]:
    """Map a ``repository_state`` to its badge variant and Russian label (§3)."""
    return _HEALTH_VARIANTS.get(state, StatusVariant.NEUTRAL), i18n.REPO_STATE_LABELS.get(
        state, i18n.UNKNOWN_REPOSITORY_STATE
    )


class ProjectCard(QFrame):
    """A bordered card summarizing one project."""

    configure_requested = Signal(str)  # emits the project id (navigation-only)

    def __init__(self, project: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.setFocusPolicy(Qt.StrongFocus)
        self.project_id: str = project["id"]
        self._badges: list[StatusBadge] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(
            tokens.SPACE_LG, tokens.SPACE_LG, tokens.SPACE_LG, tokens.SPACE_LG
        )
        root.setSpacing(tokens.SPACE_SM)

        header = QHBoxLayout()
        display_name = project.get("display_name") or i18n.project_display_name(
            project["id"], project["id"]
        )
        self.title_label = QLabel(display_name)
        self.title_label.setObjectName("CardTitle")
        header.addWidget(self.title_label)
        header.addStretch(1)

        self.sensitive_badge: StatusBadge | None = None
        if project.get("sensitive"):
            self.sensitive_badge = StatusBadge(i18n.BADGE_SENSITIVE, StatusVariant.SENSITIVE)
            self._badges.append(self.sensitive_badge)
            header.addWidget(self.sensitive_badge)

        variant, label = repository_health(project.get("repository_state", "unconfigured"))
        self.health_badge = StatusBadge(label, variant)
        self._badges.append(self.health_badge)
        header.addWidget(self.health_badge)
        root.addLayout(header)

        self._stats: list[QLabel] = []
        for text in (
            f"{i18n.PROJECT_TASKS_LABEL}: {project.get('task_count', 0)}",
            f"{i18n.PROJECT_ACTIVE_RUNS_LABEL}: {project.get('active_run_count', 0)}",
        ):
            stat = QLabel(text)
            stat.setObjectName("CardStat")
            self._stats.append(stat)
        stats_row = QHBoxLayout()
        for stat in self._stats:
            stats_row.addWidget(stat)
        stats_row.addStretch(1)
        root.addLayout(stats_row)

        accessible_parts = [self.title_label.text()]
        if project.get("sensitive"):
            accessible_parts.append(i18n.BADGE_SENSITIVE)
        accessible_parts.append(label)
        accessible_parts.extend(stat.text() for stat in self._stats)
        self.setAccessibleName(", ".join(accessible_parts))

        self.configure_button: QPushButton | None = None
        if project.get("repository_state") == "unconfigured":
            button = QPushButton(i18n.PROJECT_CONFIGURE_ACTION)
            button.setObjectName("CardAction")
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda: self.configure_requested.emit(self.project_id))
            self.configure_button = button
            root.addWidget(button)

    def stats_text(self) -> list[str]:
        """The rendered stat lines (for tests and diagnostics)."""
        return [stat.text() for stat in self._stats]

    def apply_palette(self, palette: Palette) -> None:
        """Propagate a palette to every child badge (call again on theme change)."""
        for badge in self._badges:
            badge.apply_palette(palette)
