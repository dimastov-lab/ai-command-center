"""Tests for the native Workspace Home page rendering (Desktop D2C).

`HomePage` starts in an EmptyState (no data wired yet) and, once given a snapshot
via :meth:`render_snapshot`, builds the populated Workspace Home from the D2C
components: a header MetricCard strip, one ProjectCard per project (with worktrees
for ``ok`` projects), and Active/Recent runs, Artifacts, Reports, Activity
sections. Loading is async through a `WorkspaceHomeAdapter`; the GUI thread is
never blocked.
"""

from __future__ import annotations

from command_center.desktop import i18n, tokens
from command_center.desktop.components.empty_state import EmptyState
from command_center.desktop.components.worktree_row import WorktreeRow
from command_center.desktop.pages.home import HomePage


def _snapshot(**over) -> dict:
    base = {
        "projects": [
            {
                "id": "AIOS", "display_name": "AIOS", "sensitive": False,
                "repository_path": "/r", "repository_state": "ok",
                "task_count": 3, "active_run_count": 1,
            },
            {
                "id": "BANK", "display_name": "BANK", "sensitive": True,
                "repository_path": None, "repository_state": "unconfigured",
                "task_count": 0, "active_run_count": 0,
            },
        ],
        "worktrees_by_project": {
            "AIOS": {"state": "ok", "worktrees": [{"path": "/r", "branch": "main", "head": "abc1234567"}]},
            "BANK": {"state": "unconfigured", "worktrees": []},
        },
        "active_runs": [
            {"source": "v2", "run_id": "r1", "project": "AIOS", "task_type": "implementation", "state": "RUNNING"}
        ],
        "recent_runs": [
            {"source": "v2", "run_id": "r0", "project": "AIOS", "task_type": "review", "state": "COMPLETED"}
        ],
        "recent_activity": [
            {"event_type": "run_completed", "project": "AIOS", "ts": "2026-07-29T10:00:00"}
        ],
        "artifacts": [
            {"project": "AIOS", "task_type": "implementation", "created_at": 1730000000,
             "nav_target": "AIOS/artifacts", "path": "/gen/x.md"}
        ],
        "reports": [
            {"run_id": "r0", "source": "v2", "project": "AIOS", "verdict": "APPROVED_FOR_COMMIT",
             "severity_counts": {}, "created_at": 1730000000}
        ],
    }
    base.update(over)
    return base


def test_home_starts_in_empty_state(qtbot):
    page = HomePage()
    qtbot.addWidget(page)
    assert page.findChild(EmptyState) is not None


def test_render_snapshot_makes_one_project_card_per_project_in_order(qtbot):
    page = HomePage()
    qtbot.addWidget(page)
    page.render_snapshot(_snapshot())
    assert [c.project_id for c in page.project_cards()] == ["AIOS", "BANK"]
    assert page.findChild(EmptyState) is None  # populated content replaced the empty state


def test_render_snapshot_metric_strip_reflects_counts(qtbot):
    page = HomePage()
    qtbot.addWidget(page)
    page.render_snapshot(_snapshot())
    metrics = {m.label_text(): m.value_text() for m in page.metric_cards()}
    assert metrics[i18n.METRIC_PROJECTS] == "2"
    assert metrics[i18n.METRIC_ACTIVE_RUNS] == "1"
    assert metrics[i18n.METRIC_REPORTS] == "1"


def test_render_snapshot_populates_all_sections(qtbot):
    page = HomePage()
    qtbot.addWidget(page)
    page.render_snapshot(_snapshot())
    assert len(page.run_summaries()) == 2  # 1 active + 1 recent
    assert len(page.report_rows()) == 1
    assert len(page.artifact_rows()) == 1
    assert len(page.activity_items()) == 1


def test_render_snapshot_shows_worktrees_only_for_ok_projects(qtbot):
    page = HomePage()
    qtbot.addWidget(page)
    page.render_snapshot(_snapshot())
    worktrees = page.findChildren(WorktreeRow)
    assert len(worktrees) == 1  # AIOS is ok; BANK is unconfigured


def test_render_snapshot_is_idempotent(qtbot):
    page = HomePage()
    qtbot.addWidget(page)
    page.render_snapshot(_snapshot())
    page.render_snapshot(_snapshot())  # re-render must not accumulate
    assert len(page.project_cards()) == 2


def test_configure_action_emits_navigate_to_projects(qtbot):
    page = HomePage()
    qtbot.addWidget(page)
    page.render_snapshot(_snapshot())
    bank = next(c for c in page.project_cards() if c.project_id == "BANK")
    assert bank.configure_button is not None
    with qtbot.waitSignal(page.navigate_requested, timeout=1000) as blocker:
        bank.configure_button.click()
    assert blocker.args == ["projects"]


def test_apply_palette_colours_rendered_badges(qtbot):
    page = HomePage()
    qtbot.addWidget(page)
    page.apply_palette(tokens.DARK)
    page.render_snapshot(_snapshot())
    aios = next(c for c in page.project_cards() if c.project_id == "AIOS")
    assert tokens.DARK.status_success in aios.health_badge.styleSheet()


def test_load_fetches_via_adapter_without_blocking_gui(qtbot):
    class _StubAdapter:
        def snapshot(self, **_kwargs):
            return _snapshot()

    page = HomePage()
    qtbot.addWidget(page)
    runnable = page.load(_StubAdapter())
    assert runnable is not None
    qtbot.waitUntil(lambda: len(page.project_cards()) == 2, timeout=2000)


def test_load_without_adapter_is_a_noop(qtbot):
    page = HomePage()
    qtbot.addWidget(page)
    assert page.load() is None
    assert page.findChild(EmptyState) is not None
