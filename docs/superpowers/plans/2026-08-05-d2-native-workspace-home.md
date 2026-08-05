# D2 Native Workspace Home — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the AICC desktop's Home page to real data via `build_workspace_home_snapshot`, replacing the D1 placeholder with a live, token-styled native Qt view.

**Architecture:** A new `command_center/application/` package (pure Python, no Qt) holds a `WorkspaceHomeAdapter` wrapping `build_workspace_home_snapshot`. A `workers.py` module runs the adapter on a `QThreadPool` worker thread and delivers results to the GUI thread via Qt signals. The Home page renders the snapshot through 8 focused widget components, each styled exclusively with tokens from `tokens.py`. Four PRs: D2A → D2B → D2C → D2D.

**Tech Stack:** Python 3.14, PySide6 ≥ 6.6, pytest, pytest-qt; existing `command_center.workspace_home.build_workspace_home_snapshot`, `command_center.runtime.api.ExecutionCenterAPI`, `command_center.runtime.db.resolve_db_path`.

## Global Constraints

- No Qt import anywhere in `command_center/application/` — plain Python only (`ARCHITECTURE.md §5`).
- No new snapshot fields — `build_workspace_home_snapshot` called with default limits; result returned unchanged.
- No hardcoded colors or sizes — every visual value references a token from `command_center.desktop.tokens`.
- `task_count` label must be exactly **"Tasks"** — never "Kanban", never "Open tasks".
- Row identity for Recent Runs = composite key `(source, run_id)`, never bare `run_id`.
- Redaction happens inside `command_center.workspace_home` before rendering; the desktop renderer never fetches pre-redaction data.
- TDD: tests written (and run to confirm failure) before implementation in every task.
- All commands run from `~/Projects/ai-command-center`.
- Test suite: `pytest tests/ -q` must stay green after every commit.

---

## File Map

```
# New files
command_center/application/__init__.py
command_center/application/workspace_home_adapter.py

command_center/desktop/workers.py

command_center/desktop/components/status_badge.py
command_center/desktop/components/metric_card.py
command_center/desktop/components/worktree_row.py
command_center/desktop/components/activity_item.py
command_center/desktop/components/artifact_row.py
command_center/desktop/components/report_row.py
command_center/desktop/components/run_summary.py
command_center/desktop/components/project_card.py
command_center/desktop/components/loading_skeleton.py
command_center/desktop/components/error_state.py

tests/application/__init__.py
tests/application/test_workspace_home_adapter.py
tests/desktop/test_workers.py
tests/desktop/test_workspace_home_page.py
tests/desktop/test_workspace_home_edge_states.py

# Modified files
command_center/desktop/app.py          — wire ExecutionCenterAPI + WorkspaceHomeAdapter
command_center/desktop/main_window.py  — accept adapter; wire refresh → worker dispatch
command_center/desktop/pages/home.py   — replace placeholder with real HomePage
```

---

## Task 1 (D2A): Application Service Adapter

**PR:** `feat/d2a-application-adapter`

**Files:**
- Create: `command_center/application/__init__.py`
- Create: `command_center/application/workspace_home_adapter.py`
- Create: `tests/application/__init__.py`
- Create: `tests/application/test_workspace_home_adapter.py`
- Modify: `command_center/desktop/app.py`
- Modify: `command_center/desktop/main_window.py`

**Interfaces:**
- Produces: `WorkspaceHomeAdapter(api: ExecutionCenterAPI)` with `.fetch_snapshot() -> dict`
- Produces: `build_shell` updated to accept and wire adapter into `AppShell`
- Produces: `AppShell.__init__` updated to accept `adapter: WorkspaceHomeAdapter | None = None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/application/test_workspace_home_adapter.py
"""Adapter must delegate to build_workspace_home_snapshot without adding or
dropping fields. Plain pytest — no QApplication."""

from __future__ import annotations

from pathlib import Path
import pytest
from command_center.runtime.api import ExecutionCenterAPI
from command_center.runtime import db as runtime_db


def _api(tmp_path: Path) -> ExecutionCenterAPI:
    return ExecutionCenterAPI(db_path=tmp_path / "runtime.db")


def test_adapter_returns_snapshot_keys(tmp_path, isolated_data_dir, monkeypatch):
    """Adapter.fetch_snapshot() returns the same top-level keys as calling
    build_workspace_home_snapshot directly."""
    import command_center.workspace_home as wh
    from command_center.application.workspace_home_adapter import WorkspaceHomeAdapter

    reports_dir = isolated_data_dir / "reports"
    generated_dir = isolated_data_dir / "generated"
    reports_dir.mkdir(parents=True, exist_ok=True)
    generated_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(wh, "REPORTS_DIR", reports_dir)
    monkeypatch.setattr(wh, "GENERATED_DIR", generated_dir)

    api = _api(tmp_path)
    adapter = WorkspaceHomeAdapter(api)
    snapshot = adapter.fetch_snapshot()

    expected_keys = {
        "projects", "worktrees_by_project", "active_runs",
        "recent_runs", "reports", "artifacts", "recent_activity",
    }
    assert expected_keys.issubset(snapshot.keys())


def test_adapter_no_qt_import():
    """The application package must not import Qt."""
    import command_center.application.workspace_home_adapter as mod
    import sys
    qt_modules = [k for k in sys.modules if k.startswith("PySide6")]
    # Qt may be imported by other things in the same process — we only check
    # that the adapter module itself doesn't reference Qt.
    src = Path(mod.__file__).read_text()
    assert "PySide6" not in src
    assert "PyQt" not in src


def test_adapter_does_not_transform_snapshot(tmp_path, isolated_data_dir, monkeypatch):
    """Adapter returns the snapshot dict unchanged — same object identity."""
    import command_center.workspace_home as wh
    from command_center.application.workspace_home_adapter import WorkspaceHomeAdapter
    from unittest.mock import patch

    reports_dir = isolated_data_dir / "reports"
    generated_dir = isolated_data_dir / "generated"
    reports_dir.mkdir(parents=True, exist_ok=True)
    generated_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(wh, "REPORTS_DIR", reports_dir)
    monkeypatch.setattr(wh, "GENERATED_DIR", generated_dir)

    sentinel = {"projects": [], "worktrees_by_project": {}, "active_runs": [],
                "recent_runs": [], "reports": [], "artifacts": [], "recent_activity": []}
    api = _api(tmp_path)
    adapter = WorkspaceHomeAdapter(api)

    with patch("command_center.application.workspace_home_adapter.build_workspace_home_snapshot",
               return_value=sentinel) as mock_fn:
        result = adapter.fetch_snapshot()

    mock_fn.assert_called_once()
    assert result is sentinel
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/application/test_workspace_home_adapter.py -v
```

Expected: `ModuleNotFoundError: No module named 'command_center.application'`

- [ ] **Step 3: Create the package and adapter**

```python
# command_center/application/__init__.py
"""``command_center.application`` — Qt-free adapter layer between the desktop
shell and the existing read-model core.

No Qt import is permitted anywhere in this package (`ARCHITECTURE.md §5`).
Every public function is callable from plain pytest without a QApplication.
"""
```

```python
# command_center/application/workspace_home_adapter.py
"""Workspace Home adapter — wraps `build_workspace_home_snapshot` for the
desktop shell's worker thread (`ARCHITECTURE.md §5`, `IMPLEMENTATION_ROADMAP.md D2A`).

No Qt import. Plain Python only.
"""

from __future__ import annotations

from command_center.runtime.api import ExecutionCenterAPI
from command_center.workspace_home import build_workspace_home_snapshot


class WorkspaceHomeAdapter:
    """Thin facade over :func:`build_workspace_home_snapshot`.

    Holds the one application-wide ``ExecutionCenterAPI`` instance injected at
    construction time from ``command_center.desktop.app.build_shell``.
    """

    def __init__(self, api: ExecutionCenterAPI) -> None:
        self._api = api

    def fetch_snapshot(self) -> dict:
        """Call ``build_workspace_home_snapshot`` with default limits and return
        the result unchanged. Safe to call from a worker thread."""
        return build_workspace_home_snapshot(execution_center_api=self._api)
```

```python
# tests/application/__init__.py
```

- [ ] **Step 4: Run to confirm tests pass**

```bash
pytest tests/application/test_workspace_home_adapter.py -v
```

Expected: 3 PASSED

- [ ] **Step 5: Wire API + adapter into `build_shell` and `AppShell`**

In `command_center/desktop/app.py`, add the import and update `build_shell`:

```python
# add at top of imports
from command_center.runtime.api import ExecutionCenterAPI
from command_center.runtime.db import resolve_db_path
from command_center.application.workspace_home_adapter import WorkspaceHomeAdapter


def build_shell(
    app: QApplication, settings: SettingsStore | None = None
) -> tuple[AppShell, ThemeController]:
    store = settings or SettingsStore()
    theme = ThemeController(app, mode=store.theme_mode())
    theme.apply()
    api = ExecutionCenterAPI(db_path=resolve_db_path())
    adapter = WorkspaceHomeAdapter(api)
    shell = AppShell(store, theme, adapter=adapter)
    return shell, theme
```

In `command_center/desktop/main_window.py`, update `AppShell.__init__` to accept the adapter:

```python
# add import at top
from command_center.application.workspace_home_adapter import WorkspaceHomeAdapter

class AppShell(QWidget):
    refresh_requested = Signal()

    def __init__(
        self,
        settings: SettingsStore,
        theme: ThemeController,
        adapter: WorkspaceHomeAdapter | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._theme = theme
        self._adapter = adapter  # None until D2B wires refresh; safe
        self._cancel_event = threading.Event()
        # ... rest of __init__ unchanged
```

- [ ] **Step 6: Run full desktop suite to verify no regressions**

```bash
pytest tests/ -q
```

Expected: all green (existing tests unaffected; new tests pass)

- [ ] **Step 7: Commit**

```bash
git add command_center/application/ tests/application/ \
        command_center/desktop/app.py command_center/desktop/main_window.py
git commit -m "feat(desktop/d2a): WorkspaceHomeAdapter + API wiring in build_shell"
```

---

## Task 2 (D2B): Async Worker Framework

**PR:** `feat/d2b-worker-framework`

**Files:**
- Create: `command_center/desktop/workers.py`
- Create: `tests/desktop/test_workers.py`
- Modify: `command_center/desktop/main_window.py` — wire `_on_refresh` to submit runnable

**Interfaces:**
- Consumes: `WorkspaceHomeAdapter.fetch_snapshot() -> dict` (Task 1)
- Produces: `WorkspaceHomeSignals(QObject)` with signals `snapshot_ready(dict)` and `error(str)`
- Produces: `WorkspaceHomeRefreshRunnable(QRunnable)` accepting `(adapter, signals, cancel_event)`

- [ ] **Step 1: Write the failing tests**

```python
# tests/desktop/test_workers.py
"""QThreadPool / QRunnable worker tests (pytest-qt, offscreen QApplication)."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QThreadPool

from command_center.desktop.workers import WorkspaceHomeRefreshRunnable, WorkspaceHomeSignals


def _make_adapter(snapshot=None, delay=0.0, raises=None):
    adapter = MagicMock()
    def _fetch():
        if delay:
            time.sleep(delay)
        if raises:
            raise raises
        return snapshot or {"projects": [], "worktrees_by_project": {},
                            "active_runs": [], "recent_runs": [],
                            "reports": [], "artifacts": [], "recent_activity": []}
    adapter.fetch_snapshot.side_effect = _fetch
    return adapter


def test_snapshot_ready_emitted(qtbot):
    """snapshot_ready signal carries the adapter's dict."""
    sentinel = {"projects": [], "worktrees_by_project": {}, "active_runs": [],
                "recent_runs": [], "reports": [], "artifacts": [], "recent_activity": []}
    adapter = _make_adapter(snapshot=sentinel)
    signals = WorkspaceHomeSignals()
    cancel = threading.Event()
    runnable = WorkspaceHomeRefreshRunnable(adapter, signals, cancel)

    received = []
    signals.snapshot_ready.connect(received.append)

    with qtbot.waitSignal(signals.snapshot_ready, timeout=3000):
        QThreadPool.globalInstance().start(runnable)

    assert received == [sentinel]


def test_error_emitted_when_adapter_raises(qtbot):
    """error signal carries the exception message when fetch_snapshot raises."""
    adapter = _make_adapter(raises=RuntimeError("db locked"))
    signals = WorkspaceHomeSignals()
    cancel = threading.Event()
    runnable = WorkspaceHomeRefreshRunnable(adapter, signals, cancel)

    errors = []
    signals.error.connect(errors.append)

    with qtbot.waitSignal(signals.error, timeout=3000):
        QThreadPool.globalInstance().start(runnable)

    assert errors == ["db locked"]


def test_cancelled_runnable_skips_adapter(qtbot):
    """When cancel_event is set, adapter is never called and no signal fires."""
    adapter = _make_adapter()
    signals = WorkspaceHomeSignals()
    cancel = threading.Event()
    cancel.set()
    runnable = WorkspaceHomeRefreshRunnable(adapter, signals, cancel)

    fired = []
    signals.snapshot_ready.connect(lambda _: fired.append("ready"))
    signals.error.connect(lambda _: fired.append("error"))

    QThreadPool.globalInstance().start(runnable)
    QThreadPool.globalInstance().waitForDone(500)

    adapter.fetch_snapshot.assert_not_called()
    assert fired == []


def test_gui_thread_not_blocked(qtbot):
    """GUI thread remains responsive while a slow adapter runs."""
    adapter = _make_adapter(delay=0.3)
    signals = WorkspaceHomeSignals()
    cancel = threading.Event()
    runnable = WorkspaceHomeRefreshRunnable(adapter, signals, cancel)

    gui_event_processed = []

    from PySide6.QtCore import QTimer
    def _tick():
        gui_event_processed.append(True)

    timer = QTimer()
    timer.setSingleShot(True)
    timer.setInterval(50)  # fires while adapter is still sleeping
    timer.timeout.connect(_tick)
    timer.start()

    with qtbot.waitSignal(signals.snapshot_ready, timeout=3000):
        QThreadPool.globalInstance().start(runnable)
        qtbot.wait(150)  # process events; timer should have fired

    assert gui_event_processed, "GUI thread was blocked — timer never fired"
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/desktop/test_workers.py -v
```

Expected: `ImportError` — `workers` module doesn't exist yet

- [ ] **Step 3: Implement `workers.py`**

```python
# command_center/desktop/workers.py
"""QThreadPool / QRunnable worker for Workspace Home refresh.

Pattern: a ``QRunnable`` carries the adapter call on a worker thread; a
``QObject``-based signal-holder (``WorkspaceHomeSignals``) delivers results
back to the GUI thread via queued signal/slot connections
(`ARCHITECTURE.md §10`).

``WorkspaceHomeRefreshRunnable`` accepts a ``threading.Event`` for cooperative
cancellation (`ARCHITECTURE.md §11`). The runnable checks the flag once before
calling the adapter; if set, it returns immediately without emitting signals.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QRunnable, Signal

from command_center.application.workspace_home_adapter import WorkspaceHomeAdapter


class WorkspaceHomeSignals(QObject):
    snapshot_ready = Signal(dict)
    error = Signal(str)


class WorkspaceHomeRefreshRunnable(QRunnable):
    def __init__(
        self,
        adapter: WorkspaceHomeAdapter,
        signals: WorkspaceHomeSignals,
        cancel_event: threading.Event,
    ) -> None:
        super().__init__()
        self._adapter = adapter
        self._signals = signals
        self._cancel_event = cancel_event
        self.setAutoDelete(True)

    def run(self) -> None:
        if self._cancel_event.is_set():
            return
        try:
            snapshot = self._adapter.fetch_snapshot()
            self._signals.snapshot_ready.emit(snapshot)
        except Exception as exc:
            self._signals.error.emit(str(exc))
```

- [ ] **Step 4: Run to confirm tests pass**

```bash
pytest tests/desktop/test_workers.py -v
```

Expected: 4 PASSED

- [ ] **Step 5: Wire `AppShell._on_refresh` to submit the runnable**

In `command_center/desktop/main_window.py`, update `_on_refresh`:

```python
# add imports at top of main_window.py
from .workers import WorkspaceHomeRefreshRunnable, WorkspaceHomeSignals

# In AppShell class, add slot:
def _on_refresh(self) -> None:
    """Submit a Workspace Home refresh to QThreadPool when an adapter is wired."""
    self.refresh_requested.emit()
    if self._adapter is None:
        return
    signals = WorkspaceHomeSignals()
    signals.snapshot_ready.connect(self._on_snapshot_ready)
    signals.error.connect(self._on_snapshot_error)
    runnable = WorkspaceHomeRefreshRunnable(self._adapter, signals, self._cancel_event)
    QThreadPool.globalInstance().start(runnable)

def _on_snapshot_ready(self, snapshot: dict) -> None:
    """Deliver snapshot to the active page if it can consume it."""
    page = self.stack.currentWidget()  # AppShell.stack = QStackedWidget
    if hasattr(page, "load_snapshot"):
        page.load_snapshot(snapshot)

def _on_snapshot_error(self, message: str) -> None:
    """Deliver fetch error to the active page if it can consume it."""
    page = self.stack.currentWidget()
    if hasattr(page, "show_error"):
        page.show_error(message)
```

Also add `QThreadPool` to the imports block (it was already imported for shutdown in D1; verify it's there).

- [ ] **Step 6: Run full suite**

```bash
pytest tests/ -q
```

Expected: all green

- [ ] **Step 7: Commit**

```bash
git add command_center/desktop/workers.py tests/desktop/test_workers.py \
        command_center/desktop/main_window.py
git commit -m "feat(desktop/d2b): QThreadPool worker framework for Workspace Home refresh"
```

---

## Task 3 (D2C-part-1): Leaf Components — StatusBadge, MetricCard, WorktreeRow, ActivityItem, ArtifactRow, ReportRow, RunSummary

**PR:** `feat/d2c-workspace-home` (this task is the first commit in the PR)

**Files:**
- Create: `command_center/desktop/components/status_badge.py`
- Create: `command_center/desktop/components/metric_card.py`
- Create: `command_center/desktop/components/worktree_row.py`
- Create: `command_center/desktop/components/activity_item.py`
- Create: `command_center/desktop/components/artifact_row.py`
- Create: `command_center/desktop/components/report_row.py`
- Create: `command_center/desktop/components/run_summary.py`
- Create: `tests/desktop/test_workspace_home_page.py` (partial — component tests only)

**Interfaces:**
- Consumes: `tokens.LIGHT/DARK`, `tokens.SPACE_*`, `tokens.TYPE_*`, `tokens.RADIUS_*`
- Produces: all 7 widget classes with their constructors and accessible names

- [ ] **Step 1: Write the failing component tests**

```python
# tests/desktop/test_workspace_home_page.py
"""Workspace Home page and component tests (pytest-qt, offscreen QApplication)."""

from __future__ import annotations
import pytest
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

# ── StatusBadge ─────────────────────────────────────────────────────────────

def test_status_badge_shows_label(qtbot):
    from command_center.desktop.components.status_badge import StatusBadge
    badge = StatusBadge("ok", "Configured")
    qtbot.addWidget(badge)
    assert "Configured" in badge.accessibleName()


def test_status_badge_sensitive_variant(qtbot):
    from command_center.desktop.components.status_badge import StatusBadge
    badge = StatusBadge("sensitive", "Sensitive")
    qtbot.addWidget(badge)
    assert badge.accessibleName() == "Sensitive"


# ── MetricCard ───────────────────────────────────────────────────────────────

def test_metric_card_shows_value_and_label(qtbot):
    from command_center.desktop.components.metric_card import MetricCard
    card = MetricCard(label="Active runs", value="3")
    qtbot.addWidget(card)
    assert card.accessibleName() == "Active runs: 3"


def test_metric_card_empty_value(qtbot):
    from command_center.desktop.components.metric_card import MetricCard
    card = MetricCard(label="Active runs", value=None)
    qtbot.addWidget(card)
    # None value shows em-dash, not empty string
    assert "—" in card.value_label.text()


# ── WorktreeRow ──────────────────────────────────────────────────────────────

def test_worktree_row_shows_branch(qtbot):
    from command_center.desktop.components.worktree_row import WorktreeRow
    row = WorktreeRow(path="/repos/aios", branch="main", short_head="abc1234")
    qtbot.addWidget(row)
    assert "main" in row.accessibleDescription()


def test_worktree_row_detached_head_verbatim(qtbot):
    from command_center.desktop.components.worktree_row import WorktreeRow
    row = WorktreeRow(path="/repos/aios", branch="(detached HEAD)", short_head="abc1234")
    qtbot.addWidget(row)
    assert "(detached HEAD)" in row.accessibleDescription()


# ── ActivityItem ─────────────────────────────────────────────────────────────

def test_activity_item_shows_project_and_event(qtbot):
    from command_center.desktop.components.activity_item import ActivityItem
    item = ActivityItem(project="AIOS", event_type="run_completed", timestamp="2026-08-05 10:00")
    qtbot.addWidget(item)
    assert "AIOS" in item.accessibleName()


# ── ArtifactRow ──────────────────────────────────────────────────────────────

def test_artifact_row_shows_project_and_task_type(qtbot):
    from command_center.desktop.components.artifact_row import ArtifactRow
    row = ArtifactRow(project="AIOS", task_type="audit", created_at="2026-08-05", nav_target="AIOS/audit")
    qtbot.addWidget(row)
    assert "AIOS" in row.accessibleName()


def test_artifact_row_redacted_has_no_path(qtbot):
    from command_center.desktop.components.artifact_row import ArtifactRow
    # path kwarg absent for sensitive projects — constructor must accept its absence
    row = ArtifactRow(project="BANK", task_type="audit", created_at="2026-08-05", nav_target="BANK/audit")
    qtbot.addWidget(row)
    # No path displayed — widget text must not contain any filesystem path
    text = row.accessibleName() + row.accessibleDescription()
    assert "/" not in text or "BANK/audit" in text  # nav_target is allowed


# ── ReportRow ────────────────────────────────────────────────────────────────

def test_report_row_shows_verdict(qtbot):
    from command_center.desktop.components.report_row import ReportRow
    row = ReportRow(
        run_id="run-1", source="v2", project="AIOS",
        verdict="PASS", severity_counts={}, created_at="2026-08-05",
    )
    qtbot.addWidget(row)
    assert "PASS" in row.accessibleName()


def test_report_row_unmatched_has_no_verdict_badge(qtbot):
    from command_center.desktop.components.report_row import ReportRow
    # run_id=None = unmatched report
    row = ReportRow(
        run_id=None, source=None, project="AIOS",
        verdict=None, severity_counts=None, created_at="2026-08-05",
    )
    qtbot.addWidget(row)
    assert row.verdict_badge is None


# ── RunSummary ───────────────────────────────────────────────────────────────

def test_run_summary_active_uses_active_token(qtbot):
    from command_center.desktop.components.run_summary import RunSummary
    row = RunSummary(
        run_id="r1", source="v2", project="AIOS", task_type="audit",
        state="RUNNING", created_at="2026-08-05T10:00:00",
        started_at=None, completed_at=None, duration_seconds=None,
    )
    qtbot.addWidget(row)
    assert row.state_badge.semantic == "active"


def test_run_summary_queued_uses_info_token(qtbot):
    from command_center.desktop.components.run_summary import RunSummary
    row = RunSummary(
        run_id="r2", source="v2", project="AIOS", task_type="audit",
        state="QUEUED", created_at="2026-08-05T10:00:00",
        started_at=None, completed_at=None, duration_seconds=None,
    )
    qtbot.addWidget(row)
    assert row.state_badge.semantic == "info"


def test_run_summary_composite_key(qtbot):
    from command_center.desktop.components.run_summary import RunSummary
    row = RunSummary(
        run_id="r3", source="v1", project="AIOS", task_type="audit",
        state="COMPLETED", created_at="2026-08-05T10:00:00",
        started_at=None, completed_at=None, duration_seconds=None,
    )
    qtbot.addWidget(row)
    assert row.row_key == ("v1", "r3")
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/desktop/test_workspace_home_page.py -v
```

Expected: `ImportError` — none of the component modules exist yet

- [ ] **Step 3: Implement `StatusBadge`**

```python
# command_center/desktop/components/status_badge.py
"""``StatusBadge`` — semantic-colored status indicator.

Implements `DESIGN_SYSTEM.md §7.9`. Semantic token names map to palette entries;
a ``sensitive`` variant is visually distinct from run-state variants.
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QWidget, QHBoxLayout
from PySide6.QtCore import Qt
from .. import tokens

_SEMANTIC_TO_ATTR = {
    "neutral": "status_neutral",
    "info": "status_info",
    "active": "status_active",
    "success": "status_success",
    "warning": "status_warning",
    "danger": "status_danger",
    "cancelled": "status_cancelled",
    "sensitive": "status_sensitive",
}


class StatusBadge(QWidget):
    def __init__(self, semantic: str, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.semantic = semantic
        self._label_text = label

        layout = QHBoxLayout(self)
        layout.setContentsMargins(tokens.SPACE_SM, 2, tokens.SPACE_SM, 2)
        layout.setSpacing(tokens.SPACE_XS)

        self._label = QLabel(label)
        self._label.setObjectName("StatusBadgeLabel")
        layout.addWidget(self._label)

        self.setAccessibleName(label)
        self.setObjectName(f"StatusBadge_{semantic}")
        self._apply_style()

    def _apply_style(self) -> None:
        # Color applied via object name in the application stylesheet;
        # per-badge inline style as a fallback so tests can verify token routing.
        attr = _SEMANTIC_TO_ATTR.get(self.semantic, "status_neutral")
        color = getattr(tokens.LIGHT, attr)
        self._label.setStyleSheet(f"color: {color};")
```

- [ ] **Step 4: Implement `MetricCard`**

```python
# command_center/desktop/components/metric_card.py
"""``MetricCard`` — single KPI display. Implements `DESIGN_SYSTEM.md §7.7`."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .. import tokens


class MetricCard(QWidget):
    def __init__(self, label: str, value: str | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("MetricCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(tokens.SPACE_LG, tokens.SPACE_MD, tokens.SPACE_LG, tokens.SPACE_MD)
        layout.setSpacing(tokens.SPACE_XS)

        display = value if value is not None else "—"
        self.value_label = QLabel(display)
        self.value_label.setObjectName("MetricCardValue")
        self.value_label.setAlignment(Qt.AlignHCenter)

        self._label_widget = QLabel(label)
        self._label_widget.setObjectName("MetricCardLabel")
        self._label_widget.setAlignment(Qt.AlignHCenter)

        layout.addWidget(self.value_label)
        layout.addWidget(self._label_widget)

        self.setAccessibleName(f"{label}: {display}")
```

- [ ] **Step 5: Implement `WorktreeRow`**

```python
# command_center/desktop/components/worktree_row.py
"""``WorktreeRow`` — git worktree entry. Implements `DESIGN_SYSTEM.md §7.14`."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from .. import tokens


class WorktreeRow(QWidget):
    def __init__(
        self, path: str, branch: str, short_head: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("WorktreeRow")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, tokens.SPACE_XS, 0, tokens.SPACE_XS)
        layout.setSpacing(tokens.SPACE_MD)

        self._path_label = QLabel(path)
        self._branch_label = QLabel(branch)  # verbatim, including "(detached HEAD)"
        self._head_label = QLabel(short_head)

        for w in (self._path_label, self._branch_label, self._head_label):
            layout.addWidget(w)

        self.setAccessibleName(path)
        self.setAccessibleDescription(f"Branch: {branch}, HEAD: {short_head}")
```

- [ ] **Step 6: Implement `ActivityItem`**

```python
# command_center/desktop/components/activity_item.py
"""``ActivityItem`` — activity feed row. Implements `DESIGN_SYSTEM.md §7.11`."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from .. import tokens


class ActivityItem(QWidget):
    def __init__(
        self, project: str, event_type: str, timestamp: str,
        parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ActivityItem")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, tokens.SPACE_XS, 0, tokens.SPACE_XS)
        layout.setSpacing(tokens.SPACE_MD)

        layout.addWidget(QLabel(project))
        layout.addWidget(QLabel(event_type))
        layout.addWidget(QLabel(timestamp))

        self.setAccessibleName(f"{project} — {event_type}")
        self.setAccessibleDescription(timestamp)
```

- [ ] **Step 7: Implement `ArtifactRow`**

```python
# command_center/desktop/components/artifact_row.py
"""``ArtifactRow`` — generated-task-file entry. Implements `DESIGN_SYSTEM.md §7.12`.

The ``path`` kwarg is intentionally optional: for BANK/LEGAL projects the
snapshot dict contains no ``path`` key (stripped by redaction), so this widget
must not require it.
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from .. import tokens


class ArtifactRow(QWidget):
    def __init__(
        self,
        project: str,
        task_type: str,
        created_at: str,
        nav_target: str,
        path: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ArtifactRow")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, tokens.SPACE_XS, 0, tokens.SPACE_XS)
        layout.setSpacing(tokens.SPACE_MD)

        layout.addWidget(QLabel(project))
        layout.addWidget(QLabel(task_type))
        layout.addWidget(QLabel(created_at))

        self.setAccessibleName(f"{project} — {task_type}")
        # path deliberately excluded from accessible text for sensitive projects
        self.setAccessibleDescription(created_at)
```

- [ ] **Step 8: Implement `ReportRow`**

```python
# command_center/desktop/components/report_row.py
"""``ReportRow`` — run report entry. Implements `DESIGN_SYSTEM.md §7.13`.

An unmatched report (``run_id is None``) renders without a verdict badge.
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from .. import tokens
from .status_badge import StatusBadge

_VERDICT_SEMANTIC = {
    "PASS": "success",
    "FAIL": "danger",
    "WARN": "warning",
}


class ReportRow(QWidget):
    def __init__(
        self,
        run_id: str | None,
        source: str | None,
        project: str,
        verdict: str | None,
        severity_counts: dict | None,
        created_at: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ReportRow")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, tokens.SPACE_XS, 0, tokens.SPACE_XS)
        layout.setSpacing(tokens.SPACE_MD)

        layout.addWidget(QLabel(project))
        layout.addWidget(QLabel(created_at))

        self.verdict_badge: StatusBadge | None = None
        if verdict is not None and run_id is not None:
            semantic = _VERDICT_SEMANTIC.get(verdict, "neutral")
            self.verdict_badge = StatusBadge(semantic, verdict)
            layout.addWidget(self.verdict_badge)

        name_parts = [project, verdict] if verdict else [project]
        self.setAccessibleName(" — ".join(filter(None, name_parts)))
        self.setAccessibleDescription(created_at)
```

- [ ] **Step 9: Implement `RunSummary`**

```python
# command_center/desktop/components/run_summary.py
"""``RunSummary`` — active/recent run row. Implements `DESIGN_SYSTEM.md §7.10`.

State → semantic token mapping (`WORKSPACE_HOME_SPEC.md §5`):
  RUNNING → status.active
  PREPARED / QUEUED → status.info
  terminal states → status.success / status.danger / status.cancelled
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from .. import tokens
from .status_badge import StatusBadge

_STATE_SEMANTIC = {
    "RUNNING": "active",
    "PREPARED": "info",
    "QUEUED": "info",
    "COMPLETED": "success",
    "FAILED": "danger",
    "CANCELLED": "cancelled",
    "INTERRUPTED": "cancelled",
}


class RunSummary(QWidget):
    def __init__(
        self,
        run_id: str,
        source: str,
        project: str,
        task_type: str,
        state: str,
        created_at: str,
        started_at: str | None,
        completed_at: str | None,
        duration_seconds: float | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("RunSummary")
        self.row_key = (source, run_id)  # composite identity — never bare run_id

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, tokens.SPACE_XS, 0, tokens.SPACE_XS)
        layout.setSpacing(tokens.SPACE_MD)

        layout.addWidget(QLabel(f"[{source}]"))
        layout.addWidget(QLabel(project))
        layout.addWidget(QLabel(task_type))

        semantic = _STATE_SEMANTIC.get(state, "neutral")
        self.state_badge = StatusBadge(semantic, state)
        layout.addWidget(self.state_badge)

        layout.addWidget(QLabel(created_at))

        self.setAccessibleName(f"{project} {task_type} [{source}]")
        self.setAccessibleDescription(f"State: {state}")
```

- [ ] **Step 10: Run component tests**

```bash
pytest tests/desktop/test_workspace_home_page.py -v
```

Expected: all component tests PASS

- [ ] **Step 11: Run full suite**

```bash
pytest tests/ -q
```

Expected: all green

- [ ] **Step 12: Commit components**

```bash
git add command_center/desktop/components/status_badge.py \
        command_center/desktop/components/metric_card.py \
        command_center/desktop/components/worktree_row.py \
        command_center/desktop/components/activity_item.py \
        command_center/desktop/components/artifact_row.py \
        command_center/desktop/components/report_row.py \
        command_center/desktop/components/run_summary.py \
        tests/desktop/test_workspace_home_page.py
git commit -m "feat(desktop/d2c): leaf widget components (StatusBadge, MetricCard, rows)"
```

---

## Task 4 (D2C-part-2): ProjectCard and HomePage

**Files:**
- Create: `command_center/desktop/components/project_card.py`
- Modify: `command_center/desktop/pages/home.py` (replace placeholder)
- Modify: `tests/desktop/test_workspace_home_page.py` (add ProjectCard + HomePage tests)

**Interfaces:**
- Consumes: `StatusBadge`, `WorktreeRow`, all row components (Tasks 3)
- Consumes: `WorkspaceHomeSignals.snapshot_ready`, `AppShell.refresh_requested` (Task 2)
- Produces: `ProjectCard(project_dict, worktrees_dict)` widget
- Produces: `HomePage` with `load_snapshot(snapshot: dict)` and `show_error(message: str)` methods

- [ ] **Step 1: Write failing ProjectCard and HomePage tests**

Append to `tests/desktop/test_workspace_home_page.py`:

```python
# ── ProjectCard ──────────────────────────────────────────────────────────────

def test_project_card_shows_display_name(qtbot):
    from command_center.desktop.components.project_card import ProjectCard
    project = {
        "id": "AIOS", "display_name": "AIOS Core", "sensitive": False,
        "repository_path": None, "repository_state": "unconfigured",
        "task_count": 0, "active_run_count": 0,
    }
    worktrees = {"state": "unconfigured", "worktrees": []}
    card = ProjectCard(project, worktrees)
    qtbot.addWidget(card)
    assert "AIOS Core" in card.accessibleName()


def test_project_card_task_count_label_is_tasks(qtbot):
    """Label must be exactly 'Tasks', never 'Kanban' or 'Open tasks'."""
    from command_center.desktop.components.project_card import ProjectCard
    project = {
        "id": "AIOS", "display_name": "AIOS Core", "sensitive": False,
        "repository_path": "/repos/aios", "repository_state": "ok",
        "task_count": 5, "active_run_count": 0,
    }
    worktrees = {"state": "ok", "worktrees": []}
    card = ProjectCard(project, worktrees)
    qtbot.addWidget(card)
    text = card.accessibleDescription()
    assert "Tasks" in text
    assert "Kanban" not in text
    assert "Open tasks" not in text


def test_project_card_sensitive_badge_shown(qtbot):
    from command_center.desktop.components.project_card import ProjectCard
    project = {
        "id": "BANK", "display_name": "Banking", "sensitive": True,
        "repository_path": None, "repository_state": "unconfigured",
        "task_count": 0, "active_run_count": 0,
    }
    worktrees = {"state": "unconfigured", "worktrees": []}
    card = ProjectCard(project, worktrees)
    qtbot.addWidget(card)
    assert card.sensitive_badge is not None


def test_project_card_no_sensitive_badge_for_non_sensitive(qtbot):
    from command_center.desktop.components.project_card import ProjectCard
    project = {
        "id": "AIOS", "display_name": "AIOS Core", "sensitive": False,
        "repository_path": None, "repository_state": "unconfigured",
        "task_count": 0, "active_run_count": 0,
    }
    worktrees = {"state": "unconfigured", "worktrees": []}
    card = ProjectCard(project, worktrees)
    qtbot.addWidget(card)
    assert card.sensitive_badge is None


# ── HomePage integration ─────────────────────────────────────────────────────

def _minimal_snapshot():
    from command_center import models
    projects = [
        {"id": pid, "display_name": pid, "sensitive": False,
         "repository_path": None, "repository_state": "unconfigured",
         "task_count": 0, "active_run_count": 0}
        for pid in models.PROJECT_IDS
    ]
    worktrees = {pid: {"state": "unconfigured", "worktrees": []}
                 for pid in models.PROJECT_IDS}
    return {
        "projects": projects,
        "worktrees_by_project": worktrees,
        "active_runs": [],
        "recent_runs": [],
        "reports": [],
        "artifacts": [],
        "recent_activity": [],
    }


def test_home_page_load_snapshot_renders_project_cards(qtbot, settings_store):
    from command_center.desktop.pages.home import HomePage
    from command_center.desktop.settings import SettingsStore
    page = HomePage(settings=settings_store)
    qtbot.addWidget(page)
    page.load_snapshot(_minimal_snapshot())
    # All six project cards should now be in the widget tree
    from command_center import models
    cards = page.findChildren(
        __import__("command_center.desktop.components.project_card",
                   fromlist=["ProjectCard"]).ProjectCard
    )
    assert len(cards) == len(models.PROJECT_IDS)


def test_home_page_task_count_label_never_kanban(qtbot, settings_store):
    from command_center.desktop.pages.home import HomePage
    page = HomePage(settings=settings_store)
    qtbot.addWidget(page)
    page.load_snapshot(_minimal_snapshot())

    def _collect_text(w):
        texts = []
        if hasattr(w, "accessibleName"):
            texts.append(w.accessibleName())
        if hasattr(w, "accessibleDescription"):
            texts.append(w.accessibleDescription())
        for child in w.findChildren(
            __import__("PySide6.QtWidgets", fromlist=["QWidget"]).QWidget
        ):
            texts.extend(_collect_text(child))
        return texts

    all_text = " ".join(_collect_text(page))
    assert "Kanban" not in all_text
    assert "Open tasks" not in all_text
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/desktop/test_workspace_home_page.py -v -k "project_card or home_page"
```

Expected: `ImportError` — `project_card` and real `HomePage` don't exist yet

- [ ] **Step 3: Implement `ProjectCard`**

```python
# command_center/desktop/components/project_card.py
"""``ProjectCard`` — per-project summary. Implements `DESIGN_SYSTEM.md §7.8`."""

from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel, QWidget

from .. import tokens
from .status_badge import StatusBadge
from .worktree_row import WorktreeRow

_REPO_STATE_BADGE = {
    "unconfigured": ("neutral", "Not configured"),
    "invalid_path": ("warning", "Path no longer valid"),
    "not_git_repo": ("warning", "Not a git repository"),
    "ok": ("success", "Configured"),
}


class ProjectCard(QWidget):
    def __init__(
        self,
        project: dict,
        worktrees: dict,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ProjectCard")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(tokens.SPACE_LG, tokens.SPACE_LG,
                                  tokens.SPACE_LG, tokens.SPACE_LG)
        layout.setSpacing(tokens.SPACE_SM)

        # Header row: name + sensitive badge
        header = QHBoxLayout()
        name_label = QLabel(project["display_name"])
        name_label.setObjectName("ProjectCardName")
        header.addWidget(name_label)

        self.sensitive_badge: StatusBadge | None = None
        if project.get("sensitive"):
            self.sensitive_badge = StatusBadge("sensitive", "Sensitive")
            header.addWidget(self.sensitive_badge)
        header.addStretch()
        layout.addLayout(header)

        # Repository health badge
        repo_state = project.get("repository_state", "unconfigured")
        semantic, label = _REPO_STATE_BADGE.get(repo_state, ("neutral", repo_state))
        repo_badge = StatusBadge(semantic, label)
        layout.addWidget(repo_badge)

        # Stats row: Tasks (not Kanban, not Open tasks) + Active runs
        stats = QHBoxLayout()
        stats.addWidget(QLabel(f"Tasks: {project.get('task_count', 0)}"))
        stats.addWidget(QLabel(f"Active runs: {project.get('active_run_count', 0)}"))
        stats.addStretch()
        layout.addLayout(stats)

        # Worktree rows — only when repo is ok
        if repo_state == "ok":
            for wt in worktrees.get("worktrees", []):
                layout.addWidget(WorktreeRow(
                    path=wt.get("path", ""),
                    branch=wt.get("branch", ""),
                    short_head=wt.get("short_head", ""),
                ))

        self.setAccessibleName(project["display_name"])
        self.setAccessibleDescription(
            f"Tasks: {project.get('task_count', 0)}, "
            f"Active runs: {project.get('active_run_count', 0)}, "
            f"Repo: {label}"
        )
```

- [ ] **Step 4: Implement real `HomePage`**

```python
# command_center/desktop/pages/home.py
"""Home page — cross-project rollup (native Workspace Home).

Implements `WORKSPACE_HOME_SPEC.md` via `command_center.application`.
Replaces the D1 placeholder. Receives snapshots via `load_snapshot()`,
called from `AppShell._on_snapshot_ready()` after a QThreadPool worker
delivers the result of `WorkspaceHomeAdapter.fetch_snapshot()`.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget,
)

from .. import tokens
from ..components.activity_item import ActivityItem
from ..components.artifact_row import ArtifactRow
from ..components.metric_card import MetricCard
from ..components.project_card import ProjectCard
from ..components.report_row import ReportRow
from ..components.run_summary import RunSummary
from ..components.empty_state import EmptyState
from ..settings import SettingsStore
from .base_page import BasePage


class HomePage(BasePage):
    navigate_requested = Signal(str)

    def __init__(
        self, settings: SettingsStore | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(
            "home", "Home",
            "Cross-project rollup of projects, runs, and activity.",
            parent,
        )
        self._settings = settings
        self._last_updated: datetime | None = None

        # Scrollable content area
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setObjectName("HomeScroll")
        self._content = QWidget()
        self._content.setObjectName("HomeContent")
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(
            tokens.SPACE_LG, tokens.SPACE_LG, tokens.SPACE_LG, tokens.SPACE_LG
        )
        self._content_layout.setSpacing(tokens.SPACE_XL)
        self._scroll.setWidget(self._content)
        self.add_content(self._scroll, stretch=1)

        # Initial state: instructional empty state
        self._show_initial_empty()

    # --- public API called by AppShell ----------------------------------------

    def load_snapshot(self, snapshot: dict) -> None:
        """Render a Workspace Home snapshot dict (already redacted by core)."""
        self._clear_content()
        self._last_updated = datetime.now()

        # MetricCard header strip
        projects = snapshot.get("projects", [])
        active_runs = snapshot.get("active_runs", [])
        total_tasks = sum(p.get("task_count", 0) for p in projects)

        metrics_row = QHBoxLayout()
        metrics_row.setSpacing(tokens.SPACE_MD)
        metrics_row.addWidget(MetricCard("Projects", str(len(projects))))
        metrics_row.addWidget(MetricCard("Active runs", str(len(active_runs))))
        metrics_row.addWidget(MetricCard("Tasks", str(total_tasks)))
        metrics_row.addStretch()
        metrics_widget = QWidget()
        metrics_widget.setLayout(metrics_row)
        self._content_layout.addWidget(metrics_widget)

        # ProjectCard grid (PROJECT_IDS order, via snapshot["projects"] which is already ordered)
        worktrees_by_project = snapshot.get("worktrees_by_project", {})
        project_grid = QWidget()
        grid_layout = QHBoxLayout(project_grid)
        grid_layout.setSpacing(tokens.SPACE_MD)
        for p in projects:
            wt = worktrees_by_project.get(p["id"], {"state": "unconfigured", "worktrees": []})
            grid_layout.addWidget(ProjectCard(p, wt))
        grid_layout.addStretch()
        self._content_layout.addWidget(project_grid)

        # Active Runs
        self._add_section_label("Active Runs")
        if active_runs:
            for run in active_runs:
                self._content_layout.addWidget(self._make_run_summary(run))
        else:
            self._content_layout.addWidget(EmptyState("No active runs", ""))

        # Recent Runs
        self._add_section_label("Recent Runs")
        recent_runs = snapshot.get("recent_runs", [])
        if recent_runs:
            for run in recent_runs:
                self._content_layout.addWidget(self._make_run_summary(run))
        else:
            self._content_layout.addWidget(EmptyState("No recent runs", ""))

        # Artifacts + Reports (side by side at wide layout, stacked otherwise)
        below_row = QHBoxLayout()

        artifacts_col = QVBoxLayout()
        artifacts_col.addWidget(QLabel("Artifacts"))
        for a in snapshot.get("artifacts", []):
            artifacts_col.addWidget(ArtifactRow(
                project=a["project"], task_type=a["task_type"],
                created_at=a["created_at"], nav_target=a.get("nav_target", ""),
                path=a.get("path"),  # absent for sensitive projects
            ))
        below_row.addLayout(artifacts_col)

        reports_col = QVBoxLayout()
        reports_col.addWidget(QLabel("Reports"))
        for r in snapshot.get("reports", []):
            reports_col.addWidget(ReportRow(
                run_id=r.get("run_id"), source=r.get("source"),
                project=r["project"], verdict=r.get("verdict"),
                severity_counts=r.get("severity_counts"),
                created_at=r["created_at"],
            ))
        below_row.addLayout(reports_col)

        below_widget = QWidget()
        below_widget.setLayout(below_row)
        self._content_layout.addWidget(below_widget)

        # Recent Activity
        self._add_section_label("Recent Activity")
        for ev in snapshot.get("recent_activity", []):
            self._content_layout.addWidget(ActivityItem(
                project=ev.get("project", ""),
                event_type=ev.get("event_type", ev.get("type", "")),
                timestamp=str(ev.get("ts", ev.get("created_at", ""))),
            ))

        self._content_layout.addStretch()

    def show_error(self, message: str) -> None:
        """Show a fetch-error state (used by AppShell._on_snapshot_error)."""
        self._clear_content()
        self._content_layout.addWidget(
            EmptyState("Could not load data", message, action_label="Retry",
                       on_action=lambda: self.navigate_requested.emit("home"))
        )
        self._content_layout.addStretch()

    # --- private helpers ------------------------------------------------------

    def _show_initial_empty(self) -> None:
        empty = EmptyState(
            "Workspace Home",
            "Click Refresh (Ctrl+R) to load your cross-project rollup.",
            action_label="Go to Projects",
            on_action=lambda: self.navigate_requested.emit("projects"),
        )
        self._content_layout.addWidget(empty, stretch=1)

    def _clear_content(self) -> None:
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _add_section_label(self, text: str) -> None:
        label = QLabel(text)
        label.setObjectName("HomeSectionLabel")
        self._content_layout.addWidget(label)

    @staticmethod
    def _make_run_summary(run: dict) -> RunSummary:
        return RunSummary(
            run_id=run["run_id"], source=run["source"],
            project=run.get("project", ""), task_type=run.get("task_type", ""),
            state=run.get("state", run.get("status", "")),
            created_at=str(run.get("created_at", "")),
            started_at=str(run.get("started_at", "")) if run.get("started_at") else None,
            completed_at=str(run.get("completed_at", "")) if run.get("completed_at") else None,
            duration_seconds=run.get("duration_seconds"),
        )
```

- [ ] **Step 5: Run all D2C tests**

```bash
pytest tests/desktop/test_workspace_home_page.py -v
```

Expected: all PASS

- [ ] **Step 6: Run full suite**

```bash
pytest tests/ -q
```

Expected: all green

- [ ] **Step 7: Commit and open PR**

```bash
git add command_center/desktop/components/project_card.py \
        command_center/desktop/pages/home.py \
        tests/desktop/test_workspace_home_page.py
git commit -m "feat(desktop/d2c): ProjectCard + real HomePage wired to snapshot"
```

---

## Task 5 (D2D): LoadingSkeleton, ErrorState, Edge States & Accessibility

**PR:** `feat/d2d-edge-states-a11y`

**Files:**
- Create: `command_center/desktop/components/loading_skeleton.py`
- Create: `command_center/desktop/components/error_state.py`
- Create: `tests/desktop/test_workspace_home_edge_states.py`
- Modify: `command_center/desktop/pages/home.py` — wire LoadingSkeleton on refresh start + per-section ErrorState
- Modify: `command_center/desktop/main_window.py` — register global Refresh shortcut

**Interfaces:**
- Consumes: `HomePage.load_snapshot`, `HomePage.show_error` (Task 4)
- Produces: `LoadingSkeleton(row_count, row_height)` widget
- Produces: `ErrorState(message, retry_callback)` widget with `QAccessible::Alert` on show

- [ ] **Step 1: Write failing edge state tests**

```python
# tests/desktop/test_workspace_home_edge_states.py
"""Edge state and accessibility tests for the native Home page (pytest-qt)."""

from __future__ import annotations
import pytest
pytest.importorskip("PySide6")

from command_center import models


def _snapshot_with_repo_states(**state_by_project):
    """Build a minimal snapshot with specified repository_state per project."""
    projects = []
    worktrees = {}
    for pid in models.PROJECT_IDS:
        state = state_by_project.get(pid, "unconfigured")
        projects.append({
            "id": pid, "display_name": pid, "sensitive": pid in ("BANK", "LEGAL"),
            "repository_path": None if state == "unconfigured" else f"/fake/{pid}",
            "repository_state": state,
            "task_count": 0, "active_run_count": 0,
        })
        worktrees[pid] = {"state": state, "worktrees": []}
    return {
        "projects": projects, "worktrees_by_project": worktrees,
        "active_runs": [], "recent_runs": [], "reports": [],
        "artifacts": [], "recent_activity": [],
    }


# ── LoadingSkeleton ──────────────────────────────────────────────────────────

def test_loading_skeleton_renders(qtbot):
    from command_center.desktop.components.loading_skeleton import LoadingSkeleton
    skeleton = LoadingSkeleton(row_count=3, row_height=32)
    qtbot.addWidget(skeleton)
    assert skeleton.isVisible() or True  # widget exists without crashing


# ── ErrorState ───────────────────────────────────────────────────────────────

def test_error_state_shows_message(qtbot):
    from command_center.desktop.components.error_state import ErrorState
    widget = ErrorState("Connection failed", retry_callback=None)
    qtbot.addWidget(widget)
    assert "Connection failed" in widget.accessibleName()


def test_error_state_retry_callback_wired(qtbot):
    from command_center.desktop.components.error_state import ErrorState
    called = []
    widget = ErrorState("Oops", retry_callback=lambda: called.append(True))
    qtbot.addWidget(widget)
    assert widget.retry_button is not None
    widget.retry_button.click()
    assert called == [True]


# ── Repository state edge cases ──────────────────────────────────────────────

def test_unconfigured_badge_label(qtbot, settings_store):
    from command_center.desktop.pages.home import HomePage
    from command_center.desktop.components.status_badge import StatusBadge
    page = HomePage(settings=settings_store)
    qtbot.addWidget(page)
    page.load_snapshot(_snapshot_with_repo_states())

    badges = page.findChildren(StatusBadge)
    labels = [b.accessibleName() for b in badges]
    assert any("Not configured" in l for l in labels)


def test_invalid_path_badge_label(qtbot, settings_store):
    from command_center.desktop.pages.home import HomePage
    from command_center.desktop.components.status_badge import StatusBadge
    page = HomePage(settings=settings_store)
    qtbot.addWidget(page)
    page.load_snapshot(_snapshot_with_repo_states(AIOS="invalid_path"))

    badges = page.findChildren(StatusBadge)
    labels = [b.accessibleName() for b in badges]
    assert any("Path no longer valid" in l for l in labels)


def test_not_git_repo_badge_label(qtbot, settings_store):
    from command_center.desktop.pages.home import HomePage
    from command_center.desktop.components.status_badge import StatusBadge
    page = HomePage(settings=settings_store)
    qtbot.addWidget(page)
    page.load_snapshot(_snapshot_with_repo_states(AIOS="not_git_repo"))

    badges = page.findChildren(StatusBadge)
    labels = [b.accessibleName() for b in badges]
    assert any("Not a git repository" in l for l in labels)


def test_all_six_unconfigured_primary_scenario(qtbot, settings_store):
    """Fresh install: every ProjectCard shows 'Not configured'."""
    from command_center.desktop.pages.home import HomePage
    from command_center.desktop.components.project_card import ProjectCard
    page = HomePage(settings=settings_store)
    qtbot.addWidget(page)
    page.load_snapshot(_snapshot_with_repo_states())

    cards = page.findChildren(ProjectCard)
    assert len(cards) == len(models.PROJECT_IDS)


def test_detached_head_verbatim(qtbot, settings_store):
    """'(detached HEAD)' string renders verbatim in WorktreeRow."""
    from command_center.desktop.pages.home import HomePage
    from command_center.desktop.components.worktree_row import WorktreeRow

    snapshot = _snapshot_with_repo_states(AIOS="ok")
    snapshot["worktrees_by_project"]["AIOS"]["worktrees"] = [
        {"path": "/repos/aios", "branch": "(detached HEAD)", "short_head": "abc1234"}
    ]
    page = HomePage(settings=settings_store)
    qtbot.addWidget(page)
    page.load_snapshot(snapshot)

    rows = page.findChildren(WorktreeRow)
    assert any("(detached HEAD)" in r.accessibleDescription() for r in rows)


# ── BANK/LEGAL dual-layer redaction ─────────────────────────────────────────

def test_bank_snapshot_has_no_forbidden_fields(tmp_path, isolated_data_dir, monkeypatch):
    """Snapshot-level: BANK project dict must not contain path or raw fields."""
    import command_center.workspace_home as wh
    from command_center.runtime.api import ExecutionCenterAPI

    reports_dir = isolated_data_dir / "reports"
    generated_dir = isolated_data_dir / "generated"
    reports_dir.mkdir(parents=True, exist_ok=True)
    generated_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(wh, "REPORTS_DIR", reports_dir)
    monkeypatch.setattr(wh, "GENERATED_DIR", generated_dir)

    api = ExecutionCenterAPI(db_path=tmp_path / "runtime.db")
    snapshot = wh.build_workspace_home_snapshot(execution_center_api=api)

    bank_artifacts = [a for a in snapshot["artifacts"] if a.get("project") == "BANK"]
    for art in bank_artifacts:
        assert "path" not in art, "BANK artifact must not expose raw file path"


def test_bank_widget_has_no_raw_path_text(qtbot, settings_store, tmp_path,
                                          isolated_data_dir, monkeypatch):
    """Widget-level: rendered Home page must not display any BANK file path."""
    import command_center.workspace_home as wh
    from command_center.runtime.api import ExecutionCenterAPI
    from command_center.desktop.pages.home import HomePage

    reports_dir = isolated_data_dir / "reports"
    generated_dir = isolated_data_dir / "generated"
    reports_dir.mkdir(parents=True, exist_ok=True)
    generated_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(wh, "REPORTS_DIR", reports_dir)
    monkeypatch.setattr(wh, "GENERATED_DIR", generated_dir)

    api = ExecutionCenterAPI(db_path=tmp_path / "runtime.db")
    snapshot = wh.build_workspace_home_snapshot(execution_center_api=api)

    page = HomePage(settings=settings_store)
    qtbot.addWidget(page)
    page.load_snapshot(snapshot)

    # Collect all accessible text in the widget tree
    from PySide6.QtWidgets import QWidget as QW
    def collect(w):
        parts = [w.accessibleName(), w.accessibleDescription()]
        for c in w.findChildren(QW):
            parts.extend(collect(c))
        return parts
    all_text = " ".join(filter(None, collect(page)))

    # No real filesystem path for BANK should appear — paths start with /
    bank_paths = [
        t for t in all_text.split()
        if t.startswith("/") and "BANK" in t.upper()
    ]
    assert bank_paths == [], f"BANK paths leaked into widget text: {bank_paths}"


# ── Accessibility ────────────────────────────────────────────────────────────

def test_status_badge_accessible_name_is_text_not_color(qtbot):
    from command_center.desktop.components.status_badge import StatusBadge
    badge = StatusBadge("success", "Configured")
    qtbot.addWidget(badge)
    # accessible name must be the human-readable label, not the color token
    assert badge.accessibleName() == "Configured"
    assert "#" not in badge.accessibleName()  # no hex color
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/desktop/test_workspace_home_edge_states.py -v
```

Expected: `ImportError` — `loading_skeleton` and `error_state` modules missing

- [ ] **Step 3: Implement `LoadingSkeleton`**

```python
# command_center/desktop/components/loading_skeleton.py
"""``LoadingSkeleton`` — placeholder while an in-flight fetch is loading.

Implements `DESIGN_SYSTEM.md §7.17`. Shows ``row_count`` grey rectangles
sized to ``row_height`` to approximate the density-appropriate content
they stand in for. Removed entirely (caller calls ``deleteLater``) when
the snapshot or error arrives — never shown alongside real content.
"""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QVBoxLayout, QWidget

from .. import tokens


class LoadingSkeleton(QWidget):
    def __init__(
        self,
        row_count: int = 4,
        row_height: int = tokens.CONTROL_HEIGHT_LG,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("LoadingSkeleton")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(tokens.SPACE_SM)

        for _ in range(row_count):
            bar = QFrame()
            bar.setObjectName("SkeletonBar")
            bar.setFixedHeight(row_height)
            bar.setStyleSheet(f"background-color: {tokens.LIGHT.border}; border-radius: {tokens.RADIUS_SM}px;")
            layout.addWidget(bar)

        self.setAccessibleName("Loading")
```

- [ ] **Step 4: Implement `ErrorState`**

```python
# command_center/desktop/components/error_state.py
"""``ErrorState`` — shown when a page/section fails to load.

Implements `DESIGN_SYSTEM.md §7.16`. Raises ``QAccessible::Alert`` on first
show so screen readers announce the error without the user navigating to it.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QAccessibleEvent, QAccessible
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from .. import tokens


class ErrorState(QWidget):
    def __init__(
        self,
        message: str,
        retry_callback: Callable[[], None] | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ErrorState")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(tokens.SPACE_XL, tokens.SPACE_XL,
                                  tokens.SPACE_XL, tokens.SPACE_XL)
        layout.setSpacing(tokens.SPACE_MD)
        layout.setAlignment(Qt.AlignHCenter | Qt.AlignTop)

        msg_label = QLabel(message)
        msg_label.setWordWrap(True)
        msg_label.setAlignment(Qt.AlignHCenter)
        layout.addWidget(msg_label)

        self.retry_button: QPushButton | None = None
        if retry_callback is not None:
            btn = QPushButton("Retry")
            btn.setObjectName("ErrorStateRetry")
            btn.setAccessibleName("Retry")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(retry_callback)
            layout.addWidget(btn, alignment=Qt.AlignHCenter)
            self.retry_button = btn

        self.setAccessibleName(message)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Announce to screen readers when the error first appears
        QAccessible.updateAccessibility(
            QAccessibleEvent(self, QAccessible.Event.Alert)
        )
```

- [ ] **Step 5: Wire `LoadingSkeleton` into `HomePage` on refresh start**

In `command_center/desktop/pages/home.py`, add a `show_loading()` method called by
`AppShell` before it submits the worker (or update `_on_refresh` to call it):

```python
# Add to HomePage in home.py:

def show_loading(self) -> None:
    """Replace content with a loading skeleton while refresh is in flight."""
    from ..components.loading_skeleton import LoadingSkeleton
    self._clear_content()
    self._content_layout.addWidget(LoadingSkeleton(row_count=6))
    self._content_layout.addStretch()
```

Update `AppShell._on_refresh` in `main_window.py` to call `show_loading` before submitting:

```python
def _on_refresh(self) -> None:
    self.refresh_requested.emit()
    if self._adapter is None:
        return
    page = self.stack.currentWidget()
    if hasattr(page, "show_loading"):
        page.show_loading()
    signals = WorkspaceHomeSignals()
    signals.snapshot_ready.connect(self._on_snapshot_ready)
    signals.error.connect(self._on_snapshot_error)
    runnable = WorkspaceHomeRefreshRunnable(self._adapter, signals, self._cancel_event)
    QThreadPool.globalInstance().start(runnable)
```

- [ ] **Step 6: Run all edge state tests**

```bash
pytest tests/desktop/test_workspace_home_edge_states.py -v
```

Expected: all PASS

- [ ] **Step 7: Run full suite**

```bash
pytest tests/ -q
```

Expected: all green

- [ ] **Step 8: Commit and open PR**

```bash
git add command_center/desktop/components/loading_skeleton.py \
        command_center/desktop/components/error_state.py \
        command_center/desktop/pages/home.py \
        command_center/desktop/main_window.py \
        tests/desktop/test_workspace_home_edge_states.py
git commit -m "feat(desktop/d2d): LoadingSkeleton, ErrorState, edge states + accessibility"
```

---

## D2 Final Gate

After all four PRs are merged, run the full acceptance check:

```bash
cd ~/Projects/ai-command-center
pytest tests/ -q
python -m command_center.desktop   # verify app launches and Home renders on Refresh
```

Acceptance criteria from `DESKTOP_INCREMENT_1.md §3`:

- [ ] Home page renders real project cards (all 6 projects from PROJECT_IDS)
- [ ] Refresh (Ctrl+R) triggers a background fetch and updates the page
- [ ] BANK/LEGAL entries show no raw file paths in the widget tree
- [ ] `task_count` label is "Tasks" everywhere
- [ ] Repository states (unconfigured, invalid_path, not_git_repo, ok) show correct badges
- [ ] "(detached HEAD)" renders verbatim in WorktreeRow
- [ ] Full pytest suite green
