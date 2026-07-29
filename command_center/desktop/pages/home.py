"""Home page — the native Workspace Home (Desktop D2C).

A `command_center.desktop` renderer over the snapshot returned by
`command_center.application.WorkspaceHomeAdapter` (itself a thin wrapper over
`build_workspace_home_snapshot`). The page owns no business logic and performs no
redaction — it presents already-sanitized data (`WORKSPACE_HOME_SPEC.md` §10).

Until a snapshot is loaded the page shows an :class:`EmptyState`; :meth:`load`
fetches asynchronously via the D2B worker framework (the GUI thread is never
blocked) and :meth:`render_snapshot` rebuilds the populated view: a header
MetricCard strip, one ProjectCard per project (with worktrees for ``ok``
projects), and the Active/Recent runs, Artifacts, Reports, and Activity sections.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import i18n, tokens
from ..components.activity_item import ActivityItem
from ..components.artifact_row import ArtifactRow
from ..components.empty_state import EmptyState
from ..components.metric_card import MetricCard
from ..components.project_card import ProjectCard
from ..components.report_row import ReportRow
from ..components.run_summary import RunSummary
from ..components.worktree_row import WorktreeRow
from ..tokens import Palette
from ..workers import AdapterCallRunnable
from .base_page import BasePage


class HomePage(BasePage):
    navigate_requested = Signal(str)  # target section key

    def __init__(self, adapter: object | None = None, parent: QWidget | None = None) -> None:
        super().__init__("home", i18n.HOME_TITLE, i18n.HOME_SUBTITLE, parent)
        self._adapter = adapter
        self._palette: Palette | None = None
        self._loading = False
        self._reset_registries()

        self._scroll = QScrollArea()
        self._scroll.setObjectName("HomeScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(tokens.SPACE_LG)
        self._scroll.setWidget(self._content)
        self.add_content(self._scroll, stretch=1)

        self._show_empty_state()

    # --- registries / accessors -------------------------------------------
    def _reset_registries(self) -> None:
        self._badged: list = []  # components with apply_palette
        self._project_cards: list[ProjectCard] = []
        self._metric_cards: list[MetricCard] = []
        self._run_summaries: list[RunSummary] = []
        self._activity_items: list[ActivityItem] = []
        self._artifact_rows: list[ArtifactRow] = []
        self._report_rows: list[ReportRow] = []

    def project_cards(self) -> list[ProjectCard]:
        return list(self._project_cards)

    def metric_cards(self) -> list[MetricCard]:
        return list(self._metric_cards)

    def run_summaries(self) -> list[RunSummary]:
        return list(self._run_summaries)

    def activity_items(self) -> list[ActivityItem]:
        return list(self._activity_items)

    def artifact_rows(self) -> list[ArtifactRow]:
        return list(self._artifact_rows)

    def report_rows(self) -> list[ReportRow]:
        return list(self._report_rows)

    # --- content management -----------------------------------------------
    def _clear_content(self) -> None:
        while True:
            item = self._content_layout.takeAt(0)
            if item is None:
                break
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._reset_registries()

    def _show_empty_state(self) -> None:
        self._clear_content()
        empty = EmptyState(
            i18n.HOME_EMPTY_TITLE,
            i18n.HOME_EMPTY_BODY,
            action_label=i18n.HOME_EMPTY_ACTION,
            on_action=lambda: self.navigate_requested.emit("projects"),
        )
        self._content_layout.addWidget(empty, stretch=1)
        self._loading = False

    def _show_loading_state(self) -> None:
        self._clear_content()
        loading = QLabel(i18n.HOME_LOADING)
        loading.setObjectName("SectionTitle")
        self._content_layout.addWidget(loading)
        self._content_layout.addStretch(1)
        self._loading = True

    def is_loading(self) -> bool:
        """True while a load is in flight and no snapshot has rendered yet."""
        return self._loading

    def apply_palette(self, palette: Palette) -> None:
        """Colour every badge-bearing component; re-applied after each render."""
        self._palette = palette
        for component in self._badged:
            component.apply_palette(palette)

    # --- rendering ---------------------------------------------------------
    def render_snapshot(self, snapshot: dict) -> None:
        """Rebuild the populated Workspace Home from ``snapshot`` (idempotent)."""
        self._clear_content()
        self._loading = False
        self._content_layout.addWidget(self._build_metric_strip(snapshot))
        self._content_layout.addWidget(self._build_projects_section(snapshot))
        self._content_layout.addWidget(
            self._build_runs_section(i18n.HOME_SECTION_ACTIVE_RUNS, snapshot.get("active_runs", []))
        )
        self._content_layout.addWidget(
            self._build_runs_section(i18n.HOME_SECTION_RECENT_RUNS, snapshot.get("recent_runs", []))
        )
        self._content_layout.addWidget(self._build_artifacts_section(snapshot))
        self._content_layout.addWidget(self._build_reports_section(snapshot))
        self._content_layout.addWidget(self._build_activity_section(snapshot))
        self._content_layout.addStretch(1)
        if self._palette is not None:
            self.apply_palette(self._palette)

    def _section(self, title: str, rows: list[QWidget]) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(tokens.SPACE_SM)
        header = QLabel(title)
        header.setObjectName("SectionTitle")
        layout.addWidget(header)
        if rows:
            for row in rows:
                layout.addWidget(row)
        else:
            empty = QLabel(i18n.HOME_SECTION_EMPTY)
            empty.setObjectName("RowMeta")
            layout.addWidget(empty)
        return box

    def _build_metric_strip(self, snapshot: dict) -> QWidget:
        strip = QWidget()
        layout = QHBoxLayout(strip)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(tokens.SPACE_MD)
        for value, label in (
            (len(snapshot.get("projects", [])), i18n.METRIC_PROJECTS),
            (len(snapshot.get("active_runs", [])), i18n.METRIC_ACTIVE_RUNS),
            (len(snapshot.get("recent_runs", [])), i18n.METRIC_RECENT_RUNS),
            (len(snapshot.get("artifacts", [])), i18n.METRIC_ARTIFACTS),
            (len(snapshot.get("reports", [])), i18n.METRIC_REPORTS),
        ):
            card = MetricCard(value, label)
            self._metric_cards.append(card)
            layout.addWidget(card)
        layout.addStretch(1)
        return strip

    def _build_projects_section(self, snapshot: dict) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(tokens.SPACE_SM)
        header = QLabel(i18n.HOME_SECTION_PROJECTS)
        header.setObjectName("SectionTitle")
        layout.addWidget(header)

        # Single-column stack of cards for now; a responsive multi-column grid
        # (§1.1/§1.2) is a follow-up within D2C. Each ok project's worktrees are
        # stacked directly under its card (§4).
        grid = QGridLayout()
        grid.setSpacing(tokens.SPACE_MD)
        worktrees_by = snapshot.get("worktrees_by_project", {})
        for row_index, project in enumerate(snapshot.get("projects", [])):
            card = ProjectCard(project)
            card.configure_requested.connect(lambda _pid: self.navigate_requested.emit("projects"))
            self._project_cards.append(card)
            self._badged.append(card)

            cell = QWidget()
            cell_layout = QVBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setSpacing(tokens.SPACE_XS)
            cell_layout.addWidget(card)
            if project.get("repository_state") == "ok":
                for worktree in worktrees_by.get(project["id"], {}).get("worktrees", []):
                    cell_layout.addWidget(WorktreeRow(worktree))
            grid.addWidget(cell, row_index, 0)
        layout.addLayout(grid)
        return box

    def _build_runs_section(self, title: str, runs: list[dict]) -> QWidget:
        rows: list[QWidget] = []
        for run in runs:
            summary = RunSummary(run)
            self._run_summaries.append(summary)
            self._badged.append(summary)
            rows.append(summary)
        return self._section(title, rows)

    def _build_artifacts_section(self, snapshot: dict) -> QWidget:
        rows: list[QWidget] = []
        for artifact in snapshot.get("artifacts", []):
            row = ArtifactRow(artifact)
            self._artifact_rows.append(row)
            rows.append(row)
        return self._section(i18n.HOME_SECTION_ARTIFACTS, rows)

    def _build_reports_section(self, snapshot: dict) -> QWidget:
        rows: list[QWidget] = []
        for report in snapshot.get("reports", []):
            row = ReportRow(report)
            self._report_rows.append(row)
            self._badged.append(row)
            rows.append(row)
        return self._section(i18n.HOME_SECTION_REPORTS, rows)

    def _build_activity_section(self, snapshot: dict) -> QWidget:
        rows: list[QWidget] = []
        for event in snapshot.get("recent_activity", []):
            item = ActivityItem(event)
            self._activity_items.append(item)
            rows.append(item)
        return self._section(i18n.HOME_SECTION_ACTIVITY, rows)

    # --- async loading -----------------------------------------------------
    def load(
        self,
        adapter: object | None = None,
        *,
        cancel_event: threading.Event | None = None,
        pool=None,
    ) -> AdapterCallRunnable | None:
        """Fetch the snapshot off the GUI thread and render it on completion.

        Returns the running :class:`AdapterCallRunnable` (or ``None`` if no adapter
        is available). Signals are connected before the worker starts so a fast
        adapter cannot complete before the render slot is attached.
        """
        adapter = adapter if adapter is not None else self._adapter
        if adapter is None:
            return None
        from PySide6.QtCore import QThreadPool

        self._show_loading_state()
        runnable = AdapterCallRunnable(adapter.snapshot, cancel_event=cancel_event)
        runnable.signals.result.connect(self.render_snapshot)
        runnable.signals.error.connect(self._on_load_error)
        (pool if pool is not None else QThreadPool.globalInstance()).start(runnable)
        return runnable

    def _on_load_error(self, exc: Exception) -> None:
        self._clear_content()
        self._loading = False
        message = QLabel(i18n.HOME_LOAD_ERROR)
        message.setObjectName("SectionTitle")
        detail = QLabel(str(exc))  # raw technical detail, shown under the RU message
        detail.setObjectName("RowMeta")
        detail.setWordWrap(True)
        self._content_layout.addWidget(message)
        self._content_layout.addWidget(detail)
        self._content_layout.addStretch(1)
