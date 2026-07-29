"""Tests for `command_center/desktop/components/artifact_row.py` (Desktop D2C).

`ArtifactRow` renders one generated-artifact entry (`WORKSPACE_HOME_SPEC.md` §7):
task_type + created_at, and a file path only when the snapshot carries one. For a
sensitive project the ``path`` key is absent (stripped upstream, §10), so the row
has no raw path to display — it must not fabricate one.
"""

from __future__ import annotations

from command_center.desktop.components.artifact_row import ArtifactRow


def test_artifact_row_shows_task_type_and_created_at(qtbot):
    row = ArtifactRow(
        {"project": "AIOS", "task_type": "implementation", "created_at": 1730000000, "path": "/gen/x.md"}
    )
    qtbot.addWidget(row)
    assert "implementation" in row.summary_text()


def test_artifact_row_shows_path_when_present(qtbot):
    row = ArtifactRow({"project": "AIOS", "task_type": "review", "path": "/gen/x.md"})
    qtbot.addWidget(row)
    assert row.has_path is True
    assert row.path_text() == "/gen/x.md"


def test_artifact_row_has_no_path_for_sensitive_entry(qtbot):
    # Sensitive project entry: `path` was stripped upstream, so the key is absent.
    row = ArtifactRow({"project": "BANK", "task_type": "review", "created_at": 1730000000})
    qtbot.addWidget(row)
    assert row.has_path is False
    assert row.path_text() is None
