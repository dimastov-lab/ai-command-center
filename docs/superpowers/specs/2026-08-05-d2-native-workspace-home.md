# D2 — Native Workspace Home Design Spec

**Date:** 2026-08-05  
**Status:** Approved  
**Scope:** Desktop Increment 1, stage D2 (D2A → D2B → D2C → D2D)  
**References:** `docs/desktop/IMPLEMENTATION_ROADMAP.md`, `docs/desktop/WORKSPACE_HOME_SPEC.md`,
`docs/desktop/ARCHITECTURE.md`, `docs/desktop/DESIGN_SYSTEM.md`

---

## 1. Goal

Wire the native Home page to real data via `build_workspace_home_snapshot`, using existing read
models exclusively. No new snapshot fields. No changes to `command_center.workspace_home` or any
other existing core module unless a gap is found mid-implementation.

**Delivery:** 4 pull requests, in order: D2A → D2B → D2C → D2D. Each is independently
reviewable. Tests are written first (TDD) before the implementation that makes them green.

---

## 2. D2A — Application Service Adapter

### New package

```
command_center/application/
├── __init__.py
└── workspace_home_adapter.py
```

No Qt import anywhere in this package. Plain Python only (`ARCHITECTURE.md §5`).

### `WorkspaceHomeAdapter`

```python
class WorkspaceHomeAdapter:
    def __init__(self, api: ExecutionCenterAPI) -> None: ...
    def fetch_snapshot(self) -> dict: ...
```

`fetch_snapshot` delegates directly to `build_workspace_home_snapshot(execution_center_api=self._api)`
with default limits. Returns the snapshot dict unchanged — no transformation, no field additions.

### API construction

`AppShell` constructs `ExecutionCenterAPI` once (using the existing default SQLite path from
`command_center/runtime/db.py`) and passes it into `WorkspaceHomeAdapter`. The adapter holds a
reference to the API; it does not know about file-system paths or the platform layer (that
boundary lands in D3B).

### Tests (`tests/application/test_workspace_home_adapter.py`)

D2A also creates `tests/application/__init__.py` (new test package). Plain `pytest` — no
`QApplication` required.

- Adapter returns the same snapshot shape as calling `build_workspace_home_snapshot` directly,
  verified by reusing existing fixtures from `tests/test_workspace_home.py`.
- Adapter adds no new fields and drops no existing fields from the returned dict.

### Acceptance gate

`IMPLEMENTATION_ROADMAP.md` D2A criteria: adapter callable from plain pytest with no
`QApplication`; returns the same snapshot shape `build_workspace_home_snapshot` already returns.

---

## 3. D2B — Async Worker Framework

### New file

```
command_center/desktop/workers.py
```

### Pattern (`ARCHITECTURE.md §10–11`)

`QRunnable` is not a `QObject`, so signals are carried by a separate signal-holder:

```python
class WorkspaceHomeSignals(QObject):
    snapshot_ready = Signal(dict)
    error = Signal(str)

class WorkspaceHomeRefreshRunnable(QRunnable):
    def __init__(self, adapter: WorkspaceHomeAdapter,
                 signals: WorkspaceHomeSignals,
                 cancel_event: threading.Event) -> None: ...

    def run(self) -> None:
        if self._cancel_event.is_set():
            return
        try:
            snapshot = self._adapter.fetch_snapshot()
            self._signals.snapshot_ready.emit(snapshot)
        except Exception as exc:
            self._signals.error.emit(str(exc))
```

### Cooperative cancellation

`AppShell._cancel_event` (already present in D1) is passed to each `QRunnable` at creation.
The worker checks it before calling the adapter. On shutdown, `AppShell` sets the flag and calls
`QThreadPool.waitForDone(5000)` (already wired in D1's bounded clean shutdown, `ARCHITECTURE.md
§13`).

### Tests (`tests/desktop/test_workers.py`, pytest-qt)

- GUI thread remains responsive while worker runs: a deliberately slow adapter mock + `qtbot.waitSignal`.
- `snapshot_ready` is delivered on the GUI thread (not the worker thread).
- When `cancel_event` is set before `run()`, the adapter is never called and no signal is emitted.
- When the adapter raises, `error` signal is emitted with the exception message; `snapshot_ready`
  is not emitted.

### Acceptance gate

`IMPLEMENTATION_ROADMAP.md` D2B criteria: worker framework delivers adapter result via Qt signal
on the GUI thread; GUI thread verifiably non-blocked during a slow adapter call.

---

## 4. D2C — Workspace Home Layout

### New components (`command_center/desktop/components/`)

| File | Widget | Purpose |
|------|--------|---------|
| `status_badge.py` | `StatusBadge` | Semantic-colored status indicator; sensitive variant for BANK/LEGAL |
| `metric_card.py` | `MetricCard` | Single KPI display (project count, active-run count, etc.) |
| `project_card.py` | `ProjectCard` | Per-project summary (name, repo health, task count, active runs) |
| `run_summary.py` | `RunSummary` | Active/recent run row (source tag, state, timestamps, duration) |
| `activity_item.py` | `ActivityItem` | Activity feed row (project, event type, timestamp) |
| `artifact_row.py` | `ArtifactRow` | Generated-task-file entry |
| `report_row.py` | `ReportRow` | Run report entry with verdict badge |
| `worktree_row.py` | `WorktreeRow` | Git worktree row (path, branch, short HEAD) |

`EmptyState` and `PageHeader` already exist in D1 and are reused unchanged.

### Replaced file

`command_center/desktop/pages/home.py` — placeholder replaced with the real `HomePage`.

### `HomePage` structure

- `QScrollArea` wrapping a `QWidget` with a `QVBoxLayout`.
- Three layout modes switched in `resizeEvent`: wide (≥1280px), medium (768–1279px), minimum
  (≥600px). Below 600px the main window enforces a minimum content width.
- **Header strip**: `MetricCard` row (total projects, active runs, total tasks).
- **Project grid**: one `ProjectCard` per entry in `snapshot["projects"]`, in `PROJECT_IDS` order
  (`AIOS`, `AICOS`, `BANK`, `LEGAL`, `BUSINESS`, `PERSONAL`) — never re-sorted.
- **Active Runs / Recent Runs**: `RunSummary` rows, most recent first.
- **Artifacts / Reports**: `ArtifactRow` / `ReportRow` rows.
- **Recent Activity**: `ActivityItem` rows, full width, most recent first.

### Refresh flow

1. `TopBar`'s Refresh action emits `AppShell.refresh_requested`.
2. `AppShell` slot creates `WorkspaceHomeSignals` + `WorkspaceHomeRefreshRunnable` and submits to
   `QThreadPool.globalInstance()`.
3. `HomePage` replaces content with `LoadingSkeleton` immediately.
4. On `snapshot_ready`: skeleton removed, real content rendered.
5. On `error`: skeleton removed, `ErrorState` shown for the failed section.
6. "Last updated" timestamp (held in the adapter, not a new snapshot field) shown next to Refresh.

### Styling

All tokens from `tokens.py` (D1). Light/dark via `ThemeController`. Compact/comfortable density
via `SettingsStore`. No hardcoded colors or sizes — every visual property references a token.

### Invariants that must not regress

- `task_count` label = **"Tasks"** exactly. Never "Kanban", never "Open tasks".
- Row identity for Recent Runs = composite key `(source, run_id)`, never bare `run_id`.
- Synthetic activity rows are v2-only (`_derive_activity_from_v2_runs`). No second derivation path.
- Redaction happens before rendering inside `command_center.workspace_home`. The renderer never
  fetches or accesses pre-redaction data for BANK/LEGAL entries.
- `failure_reason` rendered as a plain status label ("Timed out"), never as free text.

### Tests (`tests/desktop/test_workspace_home_page.py`, pytest-qt)

- Populated scenario: ≥1 project, ≥1 run, ≥1 artifact — all sections render with correct data.
- BANK/LEGAL dual-layer: snapshot dict has no forbidden fields → widget tree contains no
  forbidden text (mirrors `tests/test_workspace_home_ui.py`'s existing structure).
- Composite key `(source, run_id)` used for run row identity.
- `task_count` label is exactly "Tasks".
- Loading skeleton shown while fetch is in flight; removed on `snapshot_ready`.

### Acceptance gate

`IMPLEMENTATION_ROADMAP.md` D2C criteria: all sections render correctly for a populated scenario
(≥1 configured project, ≥1 run, ≥1 artifact, ≥1 report, ≥1 activity event).

---

## 5. D2D — Edge States & Accessibility

### New components

```
command_center/desktop/components/
├── loading_skeleton.py   # LoadingSkeleton
└── error_state.py        # ErrorState
```

`EmptyState` (D1) reused unchanged.

### Edge state rendering (`WORKSPACE_HOME_SPEC.md §15`)

| Existing signal | Native rendering |
|----------------|-----------------|
| `repository_state == "unconfigured"` | `StatusBadge` "Not configured" + action "Configure repository path" → navigates to Projects |
| `repository_state == "invalid_path"` | `StatusBadge` "Path no longer valid" |
| `repository_state == "not_git_repo"` | `StatusBadge` "Not a git repository" |
| branch == `"(detached HEAD)"` | Verbatim in `WorktreeRow` branch field |
| All 6 projects unconfigured | Every `ProjectCard` shows "Not configured" — primary fresh-install scenario |
| Per-project failure isolation | One project in `ErrorState`; all others render normally |

No new failure detection — every signal already exists in `workspace_home.py`/`project_config.py`/
`git_info.py`. The renderer's job is correct presentation of existing signals.

### Accessibility (`DESIGN_SYSTEM.md §4`)

- Every widget: `setAccessibleName` + `setAccessibleDescription`.
- `StatusBadge`: accessible name includes the text label (not color-only).
- `ErrorState`: raises `QAccessible::Alert` on first appearance.
- `ProjectCard` grids, list rows: Tab-order via native Qt focus chain.
- Global keyboard shortcut for Refresh registered application-wide (`INFORMATION_ARCHITECTURE.md §10`).

### Tests (`tests/desktop/test_workspace_home_edge_states.py`, pytest-qt)

- Each §15 edge case from the table above.
- BANK/LEGAL dual-layer regression: snapshot-level assertion (no forbidden fields in dict) +
  widget-level assertion (no forbidden text in rendered widget tree).
- Per-project failure isolation: injecting a single-project fetch failure → that project shows
  `ErrorState`, remaining projects render normally.

### Acceptance gate

`IMPLEMENTATION_ROADMAP.md` D2D criteria + `DESKTOP_INCREMENT_1.md §3`'s full D2 acceptance
criteria list passes.

---

## 6. Non-goals

Matching `WORKSPACE_HOME_SPEC.md §16` and `DESKTOP_INCREMENT_1.md`:

- No worktree mutation.
- No run start/cancel from Home.
- No auto-refresh or live polling.
- No new snapshot fields.
- No new redaction logic (all redaction stays in `command_center.workspace_home`).
- No merging or deprecating of existing Streamlit pages.
- No D3 content (Projects page, Settings, platform abstraction) in this scope.
