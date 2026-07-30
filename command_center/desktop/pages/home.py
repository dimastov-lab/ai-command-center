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
import weakref

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import i18n, tokens
from ..components.activity_item import ActivityItem
from ..components.aios_core_card import AIOSCoreCard
from ..components.artifact_row import ArtifactRow
from ..components.empty_state import EmptyState
from ..components.loading_skeleton import LoadingSkeleton
from ..components.metric_card import MetricCard
from ..components.project_card import ProjectCard
from ..components.provider_capabilities_card import ProviderCapabilitiesCard
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
        self._loading_skeleton: LoadingSkeleton | None = None
        self._request_id = 0
        self._active_runnables: dict[int, AdapterCallRunnable] = {}
        self._accept_worker_signals = True
        self._has_snapshot = False
        self._last_aios_status: dict | None = None
        self._last_provider_capabilities: list[dict] | None = None
        self._reset_registries()

        self._scroll = QScrollArea()
        self._scroll.setObjectName("HomeScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        # Workspace data can contain arbitrarily long repository paths and
        # branch names.  The page follows the window width; individual rows
        # elide/clamp their metadata rather than growing the canvas sideways.
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._content = QWidget()
        self._content.setMinimumWidth(0)
        self._content.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
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
        self._aios_core_card: AIOSCoreCard | None = None
        self._provider_capabilities_card: ProviderCapabilitiesCard | None = None

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

    def aios_core_card(self) -> AIOSCoreCard | None:
        return self._aios_core_card

    def provider_capabilities_card(self) -> ProviderCapabilitiesCard | None:
        return self._provider_capabilities_card

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
        self._loading_skeleton = None

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
        self._loading_skeleton = LoadingSkeleton(
            row_count=5,
            row_height=tokens.CONTROL_HEIGHT_LG,
        )
        self._content_layout.addWidget(self._loading_skeleton)
        self._content_layout.addStretch(1)
        self._loading = True

    def loading_skeleton(self) -> LoadingSkeleton | None:
        return self._loading_skeleton

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
        if self._last_aios_status is not None and "aios_core" not in snapshot:
            snapshot = {**snapshot, "aios_core": self._last_aios_status}
        self._clear_content()
        self._loading = False
        self._has_snapshot = True
        if snapshot.get("aios_core") is not None:
            self._aios_core_card = AIOSCoreCard(snapshot["aios_core"])
            self._badged.append(self._aios_core_card)
            self._content_layout.addWidget(self._aios_core_card)
        if self._last_provider_capabilities is not None:
            self._provider_capabilities_card = ProviderCapabilitiesCard(
                self._last_provider_capabilities
            )
            self._content_layout.addWidget(self._provider_capabilities_card)
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

    def render_aios_core(self, status: dict) -> None:
        """Update only the remote AIOS projection, preserving local Home data."""
        self._last_aios_status = dict(status)
        if not self._has_snapshot:
            return
        if self._aios_core_card is not None:
            self._badged = [item for item in self._badged if item is not self._aios_core_card]
            self._content_layout.removeWidget(self._aios_core_card)
            self._aios_core_card.setParent(None)
            self._aios_core_card.deleteLater()
        self._aios_core_card = AIOSCoreCard(status)
        self._badged.append(self._aios_core_card)
        self._content_layout.insertWidget(0, self._aios_core_card)
        if self._palette is not None:
            self._aios_core_card.apply_palette(self._palette)

    def render_provider_capabilities(self, providers: list[dict]) -> None:
        self._last_provider_capabilities = [dict(item) for item in providers]
        if not self._has_snapshot:
            return
        if self._provider_capabilities_card is not None:
            self._content_layout.removeWidget(self._provider_capabilities_card)
            self._provider_capabilities_card.setParent(None)
            self._provider_capabilities_card.deleteLater()
        self._provider_capabilities_card = ProviderCapabilitiesCard(providers)
        insert_at = 1 if self._aios_core_card is not None else 0
        self._content_layout.insertWidget(insert_at, self._provider_capabilities_card)

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
        if adapter is None or not self._accept_worker_signals:
            return None
        from PySide6.QtCore import QThreadPool

        if not self._has_snapshot:
            self._show_loading_state()
        self._request_id += 1
        request_id = self._request_id
        for old_request_id, old_runnable in tuple(self._active_runnables.items()):
            if old_request_id != request_id:
                old_runnable.cancel()
        runnable = AdapterCallRunnable(adapter.snapshot, cancel_event=cancel_event)
        self._active_runnables[request_id] = runnable
        page_ref = weakref.ref(self)

        def deliver_result(snapshot: dict) -> None:
            page = page_ref()
            if (
                page is not None
                and page._accept_worker_signals
                and request_id == page._request_id
            ):
                page.render_snapshot(snapshot)

        def deliver_error(_exc: Exception) -> None:
            page = page_ref()
            if (
                page is not None
                and page._accept_worker_signals
                and request_id == page._request_id
            ):
                page._on_load_error()

        def release_runnable() -> None:
            page = page_ref()
            if page is not None:
                page._active_runnables.pop(request_id, None)

        runnable.signals.result.connect(deliver_result)
        runnable.signals.error.connect(deliver_error)
        runnable.signals.finished.connect(release_runnable)
        (pool if pool is not None else QThreadPool.globalInstance()).start(runnable)

        status_method = getattr(adapter, "aios_core_status", None)
        if callable(status_method):
            status_key = -request_id
            status_runnable = AdapterCallRunnable(status_method, cancel_event=cancel_event)
            self._active_runnables[status_key] = status_runnable

            def deliver_status(status: dict) -> None:
                page = page_ref()
                if (
                    page is not None
                    and page._accept_worker_signals
                    and request_id == page._request_id
                ):
                    page.render_aios_core(status)

            def deliver_status_error(_exc: Exception) -> None:
                deliver_status(
                    {
                        "readiness": "error",
                        "source": "AIOS API",
                        "version": None,
                        "health": None,
                        "capabilities": [],
                        "gates": [],
                        "evidence": [],
                        "detail": "Не удалось получить статус AIOS Core",
                    }
                )

            def release_status_runnable() -> None:
                page = page_ref()
                if page is not None:
                    page._active_runnables.pop(status_key, None)

            status_runnable.signals.result.connect(deliver_status)
            status_runnable.signals.error.connect(deliver_status_error)
            status_runnable.signals.finished.connect(release_status_runnable)
            (pool if pool is not None else QThreadPool.globalInstance()).start(status_runnable)

        provider_method = getattr(adapter, "provider_capabilities", None)
        if callable(provider_method):
            provider_key = -(1_000_000 + request_id)
            provider_runnable = AdapterCallRunnable(
                provider_method, cancel_event=cancel_event
            )
            self._active_runnables[provider_key] = provider_runnable

            def deliver_providers(providers: list[dict]) -> None:
                page = page_ref()
                if (
                    page is not None
                    and page._accept_worker_signals
                    and request_id == page._request_id
                ):
                    page.render_provider_capabilities(providers)

            def release_provider_runnable() -> None:
                page = page_ref()
                if page is not None:
                    page._active_runnables.pop(provider_key, None)

            provider_runnable.signals.result.connect(deliver_providers)
            provider_runnable.signals.finished.connect(release_provider_runnable)
            (pool if pool is not None else QThreadPool.globalInstance()).start(
                provider_runnable
            )
        return runnable

    def shutdown_workers(self) -> None:
        """Stop accepting callbacks and cooperatively cancel every page request."""
        self._accept_worker_signals = False
        self._request_id += 1  # invalidates already-queued result/error callbacks
        for runnable in tuple(self._active_runnables.values()):
            runnable.cancel()

    def active_worker_count(self) -> int:
        return len(self._active_runnables)

    def _on_load_error(self) -> None:
        if self._has_snapshot:
            self._loading = False
            return
        self._clear_content()
        self._loading = False
        message = QLabel(i18n.HOME_LOAD_ERROR)
        message.setObjectName("SectionTitle")
        detail = QLabel(i18n.HOME_LOAD_ERROR_DETAIL)
        detail.setObjectName("RowMeta")
        detail.setWordWrap(True)
        self._content_layout.addWidget(message)
        self._content_layout.addWidget(detail)
        self._content_layout.addStretch(1)
