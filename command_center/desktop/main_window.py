"""``AppShell`` — the single top-level window composing the shell.

Implements `DESIGN_SYSTEM.md` §7.1 and `ARCHITECTURE.md` §3/§13/§14. Owns the
sidebar, top bar, and the page stack; restores/persists window geometry through
:class:`SettingsStore`; and performs a bounded clean shutdown (§13) — signalling
any in-flight ``QThreadPool`` workers to stop cooperatively and waiting up to a
fixed timeout before the window closes. D1 has no data workers yet, so this is the
seam D2 extends rather than a redesign point.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QThreadPool, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .pages.home import HomePage
from .pages.projects import ProjectsPage
from .pages.settings_page import SettingsPage
from .settings import SettingsStore
from .sections import ACTIVE_SECTION_KEYS, DEFAULT_SECTION_KEY
from .sidebar import Sidebar
from .theme import ThemeController, ThemeMode
from .top_bar import TopBar

WINDOW_TITLE = "AI Command Center"
DEFAULT_WIDTH = 1180
DEFAULT_HEIGHT = 760
MIN_WIDTH = 900
MIN_HEIGHT = 600
SHUTDOWN_TIMEOUT_MS = 5000


class AppShell(QWidget):
    """Top-level shell window. A ``QWidget`` (not ``QMainWindow``) is sufficient
    here: the shell composes its own sidebar/top bar rather than using Qt's
    dock/toolbar/menu chrome, keeping the layout fully token-driven."""

    refresh_requested = Signal()

    def __init__(
        self,
        settings: SettingsStore,
        theme: ThemeController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._theme = theme
        # Cooperative-cancellation flag shared with future background workers
        # (`ARCHITECTURE.md` §11). Set on shutdown; workers poll it at checkpoints.
        self._cancel_event = threading.Event()

        self.setObjectName("AppShell")
        self.setWindowTitle(WINDOW_TITLE)
        self.setAccessibleName(WINDOW_TITLE)
        self.setMinimumSize(MIN_WIDTH, MIN_HEIGHT)

        self._build_ui()
        self._restore_geometry()
        # Select the default section without emitting through the sidebar signal.
        self._activate_section(DEFAULT_SECTION_KEY)
        self.sidebar.set_current(DEFAULT_SECTION_KEY)

    # --- construction ------------------------------------------------------
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sidebar = Sidebar()
        self.sidebar.section_selected.connect(self._on_section_selected)
        root.addWidget(self.sidebar)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        self.top_bar = TopBar()
        self.top_bar.refresh_requested.connect(self._on_refresh)
        right_layout.addWidget(self.top_bar)

        self.stack = QStackedWidget()
        self._pages: dict[str, QWidget] = {}

        home = HomePage()
        home.navigate_requested.connect(self.navigate_to)
        self._add_page(home)

        self._add_page(ProjectsPage())

        settings_page = SettingsPage(self._theme.mode)
        settings_page.theme_mode_changed.connect(self._on_theme_mode_changed)
        self._settings_page = settings_page
        self._add_page(settings_page)

        right_layout.addWidget(self.stack, 1)
        root.addWidget(right, 1)

    def _add_page(self, page: QWidget) -> None:
        key = page.section_key  # type: ignore[attr-defined]
        self._pages[key] = page
        self.stack.addWidget(page)

    # --- navigation --------------------------------------------------------
    def _on_section_selected(self, key: str) -> None:
        self._activate_section(key)

    def navigate_to(self, key: str) -> None:
        """Programmatic navigation (e.g. an EmptyState next-action). Reflects the
        selection in the sidebar too, keeping the two in sync."""
        if key not in ACTIVE_SECTION_KEYS:
            return
        self.sidebar.set_current(key)
        self._activate_section(key)

    def _activate_section(self, key: str) -> None:
        page = self._pages.get(key)
        if page is not None:
            self.stack.setCurrentWidget(page)

    @property
    def current_section_key(self) -> str | None:
        current = self.stack.currentWidget()
        for key, page in self._pages.items():
            if page is current:
                return key
        return None

    # --- theme -------------------------------------------------------------
    def _on_theme_mode_changed(self, mode: ThemeMode) -> None:
        self._theme.set_mode(mode)
        self._settings.set_theme_mode(mode)

    # --- refresh -----------------------------------------------------------
    def _on_refresh(self) -> None:
        # D1 has no page-level data adapter to re-run; this is the seam D2 wires
        # the active page's Workspace Home refresh onto. Re-emit so callers/tests
        # can observe the user's intent.
        self.refresh_requested.emit()

    # --- geometry / lifecycle ---------------------------------------------
    def _restore_geometry(self) -> None:
        geometry = self._settings.geometry()
        if geometry is not None:
            self.restoreGeometry(geometry)
        else:
            self.resize(DEFAULT_WIDTH, DEFAULT_HEIGHT)

    def _persist_state(self) -> None:
        self._settings.set_geometry(self.saveGeometry())
        self._settings.set_theme_mode(self._theme.mode)
        self._settings.sync()

    @property
    def cancel_event(self) -> threading.Event:
        return self._cancel_event

    def shutdown(self, timeout_ms: int = SHUTDOWN_TIMEOUT_MS) -> bool:
        """Bounded clean shutdown (`ARCHITECTURE.md` §13). Signals cooperative
        cancellation, waits up to ``timeout_ms`` for the global thread pool to
        drain, then persists state. Returns whether the pool drained in time."""
        self._cancel_event.set()
        drained = QThreadPool.globalInstance().waitForDone(timeout_ms)
        self._persist_state()
        return drained

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt override)
        self.shutdown()
        event.accept()
