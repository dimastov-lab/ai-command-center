"""Accessibility contracts for Workspace Home display regions (D2D)."""

from __future__ import annotations

from PySide6.QtCore import Qt

from command_center.desktop import i18n
from command_center.desktop.components.activity_item import ActivityItem
from command_center.desktop.components.artifact_row import ArtifactRow
from command_center.desktop.components.project_card import ProjectCard
from command_center.desktop.components.report_row import ReportRow
from command_center.desktop.components.run_summary import RunSummary
from command_center.desktop.components.worktree_row import WorktreeRow


def test_project_card_is_focusable_and_announces_all_visible_state(qtbot):
    card = ProjectCard(
        {
            "id": "BANK",
            "display_name": "Банк",
            "sensitive": True,
            "repository_state": "unconfigured",
            "task_count": 2,
            "active_run_count": 1,
        }
    )
    qtbot.addWidget(card)

    assert card.focusPolicy() == Qt.StrongFocus
    assert card.accessibleName() == (
        "Банк, Конфиденциально, Не настроен, Задачи: 2, Активные запуски: 1"
    )


def test_workspace_rows_are_focusable_and_have_combined_accessible_names(qtbot):
    rows = [
        RunSummary(
            {
                "source": "v2",
                "run_id": "r1",
                "project": "AIOS",
                "task_type": "review",
                "state": "RUNNING",
            }
        ),
        ActivityItem(
            {"event_type": "run_completed", "project": "AIOS", "ts": "сегодня"}
        ),
        ArtifactRow(
            {
                "project": "AIOS",
                "task_type": "review",
                "created_at": "сегодня",
                "path": "/tmp/result.md",
            }
        ),
        ReportRow(
            {
                "project": "AIOS",
                "created_at": "сегодня",
                "run_id": "r1",
                "verdict": "APPROVED_FOR_COMMIT",
            }
        ),
        WorktreeRow({"path": "/repo", "branch": "(detached HEAD)", "head": "abc123"}),
    ]
    for row in rows:
        qtbot.addWidget(row)
        assert row.focusPolicy() == Qt.StrongFocus
        assert row.accessibleName().strip()

    assert i18n.RUN_SOURCE_LABELS["v2"] in rows[0].accessibleName()
    assert "Выполняется" in rows[0].accessibleName()
    assert "Запуск завершён" in rows[1].accessibleName()
    assert "/tmp/result.md" in rows[2].accessibleName()
    assert "Одобрено для commit" in rows[3].accessibleName()
    assert "(detached HEAD)" in rows[4].accessibleName()


def test_sensitive_artifact_accessible_name_cannot_leak_absent_path(qtbot):
    row = ArtifactRow(
        {
            "project": "BANK",
            "task_type": "review",
            "created_at": "сегодня",
            "nav_target": "BANK/artifacts",
        }
    )
    qtbot.addWidget(row)

    assert row.path_text() is None
    assert "nav_target" not in row.accessibleName()
    assert "/BANK/" not in row.accessibleName()
