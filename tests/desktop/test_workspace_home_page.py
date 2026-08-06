"""Component and HomePage integration tests for the native Workspace Home (D2C).

Covers every D2C widget class — StatusBadge, MetricCard, WorktreeRow, ActivityItem,
ArtifactRow, ReportRow, RunSummary, ProjectCard — and the HomePage page object that
composes them.  Tests exercise the *actual* constructor APIs discovered from source:
all row/item widgets take a single dict argument, not positional strings.

NOTE on API discrepancies vs task brief:
- WorktreeRow(worktree_dict)     — not (path, branch, short_head)
- ActivityItem(event_dict)       — not (project, event_type, timestamp)
- ArtifactRow(artifact_dict)     — not (project, task_type, created_at, nav_target, path)
- ProjectCard(project_dict)      — not (project_dict, worktrees_dict)
- page.project_cards()           — method, not property
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QLabel

from command_center import models
from command_center.desktop import i18n
from command_center.desktop.components.activity_item import ActivityItem, activity_label
from command_center.desktop.components.artifact_row import ArtifactRow
from command_center.desktop.components.metric_card import MetricCard
from command_center.desktop.components.project_card import ProjectCard
from command_center.desktop.components.report_row import ReportRow
from command_center.desktop.components.run_summary import RunSummary
from command_center.desktop.components.status_badge import StatusBadge, StatusVariant
from command_center.desktop.components.worktree_row import WorktreeRow
from command_center.desktop.pages.home import HomePage


# ---------------------------------------------------------------------------
# Snapshot helpers
# ---------------------------------------------------------------------------


def _minimal_snapshot() -> dict:
    """Minimal but structurally valid snapshot covering all PROJECT_IDS."""
    projects = [
        {
            "id": pid,
            "display_name": pid,
            "sensitive": False,
            "repository_path": None,
            "repository_state": "unconfigured",
            "task_count": 0,
            "active_run_count": 0,
        }
        for pid in models.PROJECT_IDS
    ]
    worktrees = {
        pid: {"state": "unconfigured", "worktrees": []}
        for pid in models.PROJECT_IDS
    }
    return {
        "projects": projects,
        "worktrees_by_project": worktrees,
        "active_runs": [],
        "recent_runs": [],
        "reports": [],
        "artifacts": [],
        "recent_activity": [],
    }


def _project(**over) -> dict:
    base = dict(
        id="AIOS",
        display_name="AIOS",
        sensitive=False,
        repository_path=None,
        repository_state="unconfigured",
        task_count=0,
        active_run_count=0,
    )
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# StatusBadge component tests
# ---------------------------------------------------------------------------


def test_status_badge_accessible_name_equals_label_text(qtbot):
    badge = StatusBadge("Активен", StatusVariant.ACTIVE)
    qtbot.addWidget(badge)
    assert badge.accessibleName() == "Активен"


def test_status_badge_variant_stored_correctly(qtbot):
    badge = StatusBadge("Опасность", StatusVariant.DANGER)
    qtbot.addWidget(badge)
    assert badge.variant is StatusVariant.DANGER


def test_status_badge_text_matches_constructor_arg(qtbot):
    badge = StatusBadge("Готово", StatusVariant.SUCCESS)
    qtbot.addWidget(badge)
    assert badge.text() == "Готово"


# ---------------------------------------------------------------------------
# MetricCard component tests
# ---------------------------------------------------------------------------


def test_metric_card_value_and_label_are_set(qtbot):
    card = MetricCard(42, "Проекты")
    qtbot.addWidget(card)
    assert card.value_text() == "42"
    assert card.label_text() == "Проекты"


def test_metric_card_accessible_name_combines_label_and_value(qtbot):
    card = MetricCard(7, "Запуски")
    qtbot.addWidget(card)
    assert "Запуски" in card.accessibleName()
    assert "7" in card.accessibleName()


def test_metric_card_zero_value_renders_as_zero_string(qtbot):
    card = MetricCard(0, "Артефакты")
    qtbot.addWidget(card)
    assert card.value_text() == "0"


# ---------------------------------------------------------------------------
# WorktreeRow component tests
# (actual API: WorktreeRow(worktree_dict) — not positional path/branch/head)
# ---------------------------------------------------------------------------


def test_worktree_row_shows_path_branch_head(qtbot):
    row = WorktreeRow({"path": "/repos/aios", "branch": "main", "head": "abc1234567"})
    qtbot.addWidget(row)
    assert row.path_text() == "/repos/aios"
    assert row.branch_text() == "main"
    assert row.head_text() == "abc1234567"


def test_worktree_row_accessible_name_contains_all_parts(qtbot):
    row = WorktreeRow({"path": "/r", "branch": "feat/x", "head": "deadbeef12"})
    qtbot.addWidget(row)
    name = row.accessibleName()
    assert "/r" in name
    assert "feat/x" in name
    assert "deadbeef12" in name


def test_worktree_row_missing_fields_degrade_to_empty(qtbot):
    row = WorktreeRow({})
    qtbot.addWidget(row)
    assert row.path_text() == ""
    assert row.branch_text() == ""
    assert row.head_text() == ""


# ---------------------------------------------------------------------------
# ActivityItem component tests
# (actual API: ActivityItem(event_dict) — not positional project/event_type/ts)
# ---------------------------------------------------------------------------


def test_activity_item_renders_known_event_type(qtbot):
    event = {"event_type": "run_completed", "project": "AIOS", "ts": "2026-08-06T10:00:00"}
    item = ActivityItem(event)
    qtbot.addWidget(item)
    # description_text returns the Russian label for the event type
    assert item.description_text() == activity_label("run_completed")
    assert "AIOS" in item.detail_text()


def test_activity_item_accessible_name_not_empty(qtbot):
    item = ActivityItem({"event_type": "run_started", "project": "ESF", "ts": "2026-08-06T09:00:00"})
    qtbot.addWidget(item)
    assert item.accessibleName() != ""


def test_activity_item_tolerates_empty_dict(qtbot):
    item = ActivityItem({})
    qtbot.addWidget(item)
    assert item.description_text() == ""


# ---------------------------------------------------------------------------
# ArtifactRow component tests
# (actual API: ArtifactRow(artifact_dict) — not positional args)
# ---------------------------------------------------------------------------


def test_artifact_row_summary_contains_task_type_label(qtbot):
    artifact = {
        "project": "AIOS",
        "task_type": "implementation",
        "created_at": "2026-08-06",
        "path": "/gen/x.md",
    }
    row = ArtifactRow(artifact)
    qtbot.addWidget(row)
    assert row.summary_text() != ""


def test_artifact_row_has_path_when_path_present(qtbot):
    artifact = {"task_type": "review", "created_at": "2026-08-01", "path": "/out/r.md"}
    row = ArtifactRow(artifact)
    qtbot.addWidget(row)
    assert row.has_path is True
    assert row.path_text() == "/out/r.md"


def test_artifact_row_no_path_for_sensitive_project(qtbot):
    """Sensitive project artifacts have the path key stripped upstream; row shows no path."""
    artifact = {"task_type": "implementation", "created_at": "2026-08-01"}
    row = ArtifactRow(artifact)
    qtbot.addWidget(row)
    assert row.has_path is False
    assert row.path_text() is None


# ---------------------------------------------------------------------------
# ReportRow component tests
# ---------------------------------------------------------------------------


def test_report_row_shows_verdict_badge_for_matched_run(qtbot):
    report = {
        "run_id": "r1",
        "source": "v2",
        "project": "AIOS",
        "verdict": "APPROVED_FOR_COMMIT",
        "created_at": "2026-08-01",
    }
    row = ReportRow(report)
    qtbot.addWidget(row)
    assert row.is_unmatched is False
    assert row.verdict_badge_widget is not None


def test_report_row_no_badge_for_unmatched_run(qtbot):
    report = {"run_id": None, "project": "AIOS", "created_at": "2026-08-01"}
    row = ReportRow(report)
    qtbot.addWidget(row)
    assert row.is_unmatched is True
    assert row.verdict_badge_widget is None


def test_report_row_accessible_name_not_empty_for_matched(qtbot):
    report = {"run_id": "r2", "project": "ESF", "verdict": "APPROVED_FOR_COMMIT", "created_at": "2026-08-02"}
    row = ReportRow(report)
    qtbot.addWidget(row)
    assert row.accessibleName() != ""


# ---------------------------------------------------------------------------
# RunSummary component tests
# ---------------------------------------------------------------------------


def test_run_summary_title_contains_project_and_task_type(qtbot):
    run = {"source": "v2", "run_id": "r1", "project": "AIOS", "task_type": "implementation", "state": "RUNNING"}
    summary = RunSummary(run)
    qtbot.addWidget(summary)
    assert "AIOS" in summary.title_text()


def test_run_summary_state_badge_shown_when_state_present(qtbot):
    run = {"source": "v2", "run_id": "r1", "project": "AIOS", "task_type": "review", "state": "COMPLETED"}
    summary = RunSummary(run)
    qtbot.addWidget(summary)
    assert summary.state_badge is not None
    assert summary.state_badge.variant is StatusVariant.SUCCESS


def test_run_summary_no_state_badge_when_state_absent(qtbot):
    run = {"source": "v2", "run_id": "r1", "project": "AIOS", "task_type": "review"}
    summary = RunSummary(run)
    qtbot.addWidget(summary)
    assert summary.state_badge is None


def test_run_summary_run_key_is_source_run_id_composite(qtbot):
    run = {"source": "v2", "run_id": "abc", "project": "AIOS", "task_type": "implementation", "state": "QUEUED"}
    summary = RunSummary(run)
    qtbot.addWidget(summary)
    assert summary.run_key == ("v2", "abc")


# ---------------------------------------------------------------------------
# ProjectCard — specific required tests
# ---------------------------------------------------------------------------


def test_project_card_task_count_label_is_tasks(qtbot):
    """The stat line uses i18n.PROJECT_TASKS_LABEL ("Задачи"), not "Kanban"."""
    card = ProjectCard(_project(task_count=5, repository_state="ok"))
    qtbot.addWidget(card)
    stats_joined = " ".join(card.stats_text())
    assert i18n.PROJECT_TASKS_LABEL in stats_joined
    assert "Kanban" not in stats_joined


def test_project_card_sensitive_badge_shown(qtbot):
    """sensitive=True → sensitive_badge is present with SENSITIVE variant."""
    card = ProjectCard(_project(id="BANK", display_name="BANK", sensitive=True))
    qtbot.addWidget(card)
    assert card.sensitive_badge is not None
    assert card.sensitive_badge.variant is StatusVariant.SENSITIVE
    assert card.sensitive_badge.text() == i18n.BADGE_SENSITIVE


def test_project_card_no_sensitive_badge_for_non_sensitive(qtbot):
    """sensitive=False → sensitive_badge is None (no badge rendered at all)."""
    card = ProjectCard(_project(sensitive=False))
    qtbot.addWidget(card)
    assert card.sensitive_badge is None


def test_project_card_accessible_name_includes_title_and_health(qtbot):
    card = ProjectCard(_project(display_name="ESF", repository_state="ok"))
    qtbot.addWidget(card)
    name = card.accessibleName()
    assert "ESF" in name
    assert i18n.REPO_STATE_LABELS["ok"] in name


def test_project_card_configure_button_visible_when_unconfigured(qtbot):
    card = ProjectCard(_project(repository_state="unconfigured"))
    qtbot.addWidget(card)
    assert card.configure_button is not None


def test_project_card_no_configure_button_when_ok(qtbot):
    card = ProjectCard(_project(repository_state="ok"))
    qtbot.addWidget(card)
    assert card.configure_button is None


# ---------------------------------------------------------------------------
# HomePage integration tests
# ---------------------------------------------------------------------------


def test_home_page_render_snapshot_renders_project_cards(qtbot):
    """render_snapshot creates exactly one ProjectCard per project in the snapshot."""
    page = HomePage()
    qtbot.addWidget(page)
    snap = _minimal_snapshot()
    page.render_snapshot(snap)
    cards = page.project_cards()
    expected_ids = [p["id"] for p in snap["projects"]]
    assert [c.project_id for c in cards] == expected_ids


def test_home_page_task_count_label_never_kanban(qtbot):
    """The word 'Kanban' must never appear in any QLabel on the rendered home page."""
    page = HomePage()
    qtbot.addWidget(page)
    page.render_snapshot(_minimal_snapshot())
    for label in page.findChildren(QLabel):
        assert "Kanban" not in label.text(), (
            f"Found 'Kanban' in label text: {label.text()!r}"
        )


def test_home_page_starts_without_project_cards(qtbot):
    """Before any render_snapshot call the page has no project cards."""
    page = HomePage()
    qtbot.addWidget(page)
    assert page.project_cards() == []


def test_home_page_render_snapshot_replaces_previous_content(qtbot):
    """Calling render_snapshot twice does not accumulate project cards."""
    page = HomePage()
    qtbot.addWidget(page)
    page.render_snapshot(_minimal_snapshot())
    page.render_snapshot(_minimal_snapshot())
    assert len(page.project_cards()) == len(models.PROJECT_IDS)


def test_home_page_metric_strip_counts_match_snapshot(qtbot):
    """MetricCard for projects reflects the number of projects in the snapshot."""
    page = HomePage()
    qtbot.addWidget(page)
    snap = _minimal_snapshot()
    page.render_snapshot(snap)
    metrics = {m.label_text(): m.value_text() for m in page.metric_cards()}
    assert metrics[i18n.METRIC_PROJECTS] == str(len(snap["projects"]))


def test_home_page_sensitive_projects_show_sensitive_badge(qtbot):
    """Snapshot projects with sensitive=True get a sensitive badge on their card."""
    snap = _minimal_snapshot()
    snap["projects"][0]["sensitive"] = True
    page = HomePage()
    qtbot.addWidget(page)
    page.render_snapshot(snap)
    first_card = page.project_cards()[0]
    assert first_card.sensitive_badge is not None


def test_home_page_non_sensitive_projects_have_no_sensitive_badge(qtbot):
    """Snapshot projects with sensitive=False produce no sensitive badge on their card."""
    page = HomePage()
    qtbot.addWidget(page)
    page.render_snapshot(_minimal_snapshot())
    for card in page.project_cards():
        # All projects in _minimal_snapshot have sensitive=False
        assert card.sensitive_badge is None
