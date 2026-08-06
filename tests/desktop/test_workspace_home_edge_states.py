"""Edge state, accessibility, and redaction regression tests for Workspace Home.

Desktop D2D coverage (§15 / WORKSPACE_HOME_SPEC §10):
- LoadingSkeleton instantiation
- ErrorState message display and retry callback wiring
- Repository-state badge labels (unconfigured / invalid_path / not_git_repo)
- All-PROJECT_IDS-unconfigured scenario
- Detached HEAD worktree rendering
- BANK/LEGAL dual-layer path redaction
- StatusBadge accessible-name quality (text, not colour)
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QWidget

from command_center import models
from command_center.desktop import i18n
from command_center.desktop.components.error_state import ErrorState
from command_center.desktop.components.loading_skeleton import LoadingSkeleton
from command_center.desktop.components.status_badge import StatusBadge, StatusVariant
from command_center.desktop.components.worktree_row import WorktreeRow
from command_center.desktop.pages.home import HomePage


# ---------------------------------------------------------------------------
# Snapshot helper
# ---------------------------------------------------------------------------


def _snapshot_with_repo_states(**state_by_project):
    """Build minimal snapshot with specified repository_state per project."""
    projects = []
    worktrees = {}
    for pid in models.PROJECT_IDS:
        state = state_by_project.get(pid, "unconfigured")
        projects.append({
            "id": pid,
            "display_name": pid,
            "sensitive": pid in ("BANK", "LEGAL"),
            "repository_path": None if state == "unconfigured" else f"/fake/{pid}",
            "repository_state": state,
            "task_count": 0,
            "active_run_count": 0,
        })
        worktrees[pid] = {"state": state, "worktrees": []}
    return {
        "projects": projects,
        "worktrees_by_project": worktrees,
        "active_runs": [],
        "recent_runs": [],
        "reports": [],
        "artifacts": [],
        "recent_activity": [],
    }


# ---------------------------------------------------------------------------
# LoadingSkeleton
# ---------------------------------------------------------------------------


def test_loading_skeleton_renders(qtbot):
    """LoadingSkeleton(row_count=3, row_height=32) instantiates without crash."""
    widget = LoadingSkeleton(row_count=3, row_height=32)
    qtbot.addWidget(widget)
    assert widget is not None
    assert widget.property("busy") is True
    assert len(widget.row_heights()) == 3


# ---------------------------------------------------------------------------
# ErrorState
# ---------------------------------------------------------------------------


def test_error_state_shows_message(qtbot):
    """ErrorState exposes its message via the widget's accessible name."""
    widget = ErrorState("Connection failed", retry_callback=None)
    qtbot.addWidget(widget)
    assert "Connection failed" in widget.accessibleName()


def test_error_state_retry_callback_wired(qtbot):
    """retry_button is created and wired when a callback is supplied."""
    called: list[int] = []
    widget = ErrorState("Load error", retry_callback=lambda: called.append(1))
    qtbot.addWidget(widget)
    assert widget.retry_button is not None
    widget.retry_button.click()
    assert called == [1]


# ---------------------------------------------------------------------------
# Repository-state badge labels (via ProjectCard inside HomePage)
# ---------------------------------------------------------------------------


def _badge_names(page: HomePage) -> list[str]:
    """Collect the accessible name of every StatusBadge in the rendered page."""
    return [w.accessibleName() for w in page.findChildren(StatusBadge)]


def test_unconfigured_badge_label(qtbot):
    """All-unconfigured snapshot shows the Russian 'Не настроен' badge label."""
    page = HomePage()
    qtbot.addWidget(page)
    page.render_snapshot(_snapshot_with_repo_states())
    names = _badge_names(page)
    expected = i18n.REPO_STATE_LABELS["unconfigured"]  # "Не настроен"
    assert any(expected in n for n in names), (
        f"Expected badge label {expected!r} not found in {names}"
    )


def test_invalid_path_badge_label(qtbot):
    """invalid_path state renders the 'Путь недействителен' label."""
    page = HomePage()
    qtbot.addWidget(page)
    page.render_snapshot(_snapshot_with_repo_states(AIOS="invalid_path"))
    names = _badge_names(page)
    expected = i18n.REPO_STATE_LABELS["invalid_path"]  # "Путь недействителен"
    assert any(expected in n for n in names), (
        f"Expected badge label {expected!r} not found in {names}"
    )


def test_not_git_repo_badge_label(qtbot):
    """not_git_repo state renders the 'Не git-репозиторий' label."""
    page = HomePage()
    qtbot.addWidget(page)
    page.render_snapshot(_snapshot_with_repo_states(AIOS="not_git_repo"))
    names = _badge_names(page)
    expected = i18n.REPO_STATE_LABELS["not_git_repo"]  # "Не git-репозиторий"
    assert any(expected in n for n in names), (
        f"Expected badge label {expected!r} not found in {names}"
    )


# ---------------------------------------------------------------------------
# All-projects-unconfigured scenario
# ---------------------------------------------------------------------------


def test_all_six_unconfigured_primary_scenario(qtbot):
    """render_snapshot with all PROJECT_IDS unconfigured produces one card each."""
    page = HomePage()
    qtbot.addWidget(page)
    page.render_snapshot(_snapshot_with_repo_states())
    cards = page.project_cards()
    assert len(cards) == len(models.PROJECT_IDS), (
        f"Expected {len(models.PROJECT_IDS)} cards, got {len(cards)}"
    )


# ---------------------------------------------------------------------------
# Detached HEAD
# ---------------------------------------------------------------------------


def test_detached_head_verbatim(qtbot):
    """'(detached HEAD)' branch string is rendered verbatim in WorktreeRow accessible name."""
    snapshot = _snapshot_with_repo_states(AIOS="ok")
    # Inject a worktree with a detached HEAD branch for the ok project.
    snapshot["worktrees_by_project"]["AIOS"] = {
        "state": "ok",
        "worktrees": [
            {
                "path": "/fake/AIOS/.git/worktrees/wt1",
                "branch": "(detached HEAD)",
                "head": "abc1234",
            }
        ],
    }
    page = HomePage()
    qtbot.addWidget(page)
    page.render_snapshot(snapshot)

    worktree_rows = page.findChildren(WorktreeRow)
    assert worktree_rows, "Expected at least one WorktreeRow for the ok AIOS project"
    accessible_names = [w.accessibleName() for w in worktree_rows]
    assert any("(detached HEAD)" in name for name in accessible_names), (
        f"'(detached HEAD)' not found in WorktreeRow accessible names: {accessible_names}"
    )


# ---------------------------------------------------------------------------
# BANK/LEGAL dual-layer path redaction
# ---------------------------------------------------------------------------


@pytest.fixture
def _isolated_artifact_dirs(isolated_data_dir, monkeypatch):
    """Isolate generated/ and reports/ directories for workspace_home scans."""
    from command_center import workspace_home

    reports_dir = isolated_data_dir / "reports"
    generated_dir = isolated_data_dir / "generated"
    reports_dir.mkdir(parents=True, exist_ok=True)
    generated_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(workspace_home, "REPORTS_DIR", reports_dir)
    monkeypatch.setattr(workspace_home, "GENERATED_DIR", generated_dir)
    return generated_dir, reports_dir


def test_bank_snapshot_has_no_forbidden_fields(qtbot, _isolated_artifact_dirs):
    """BANK artifacts in the snapshot carry no 'path' key (redaction §10)."""
    from command_center import workspace_home
    from command_center.runtime.api import ExecutionCenterAPI

    generated_dir, _reports_dir = _isolated_artifact_dirs
    (generated_dir / "BANK").mkdir(parents=True, exist_ok=True)
    (generated_dir / "BANK" / "20260806_implementation.md").write_text(
        "# BANK implementation output\n\nConfidential task details."
    )

    snapshot = workspace_home.build_workspace_home_snapshot(
        execution_center_api=ExecutionCenterAPI()
    )

    bank_artifacts = [
        a for a in snapshot.get("artifacts", []) if a.get("project") == "BANK"
    ]
    # A generated BANK artifact must be visible (not hidden entirely).
    assert bank_artifacts, "Expected BANK artifact in snapshot — none found"
    for artifact in bank_artifacts:
        assert "path" not in artifact, (
            f"BANK artifact exposed forbidden 'path' field: {artifact}"
        )


def test_bank_widget_has_no_raw_path_text(qtbot, _isolated_artifact_dirs):
    """Rendered widget tree for a BANK-inclusive snapshot exposes no raw path containing 'BANK'."""
    from command_center import workspace_home
    from command_center.runtime.api import ExecutionCenterAPI

    generated_dir, _reports_dir = _isolated_artifact_dirs
    (generated_dir / "BANK").mkdir(parents=True, exist_ok=True)
    (generated_dir / "BANK" / "20260806_implementation.md").write_text(
        "# BANK implementation\n"
    )

    snapshot = workspace_home.build_workspace_home_snapshot(
        execution_center_api=ExecutionCenterAPI()
    )

    page = HomePage()
    qtbot.addWidget(page)
    page.render_snapshot(snapshot)

    # Collect all user-visible text from the widget tree.
    all_text_values: list[str] = []
    for widget in [page, *page.findChildren(QWidget)]:
        for getter in ("text", "accessibleName", "accessibleDescription", "toolTip"):
            fn = getattr(widget, getter, None)
            if callable(fn):
                try:
                    value = fn()
                except TypeError:
                    continue
                if isinstance(value, str) and value:
                    all_text_values.append(value)

    for text_value in all_text_values:
        for token in text_value.split():
            if token.startswith("/") and "BANK" in token:
                raise AssertionError(
                    f"Raw path token {token!r} containing 'BANK' found in widget text: {text_value!r}"
                )


# ---------------------------------------------------------------------------
# Accessibility
# ---------------------------------------------------------------------------


def test_status_badge_accessible_name_is_text_not_color(qtbot):
    """StatusBadge accessible name is the label text, never a colour code."""
    badge = StatusBadge("Configured", StatusVariant.SUCCESS)
    qtbot.addWidget(badge)
    name = badge.accessibleName()
    assert name == "Configured"
    assert "#" not in name, f"Accessible name contains colour code: {name!r}"
