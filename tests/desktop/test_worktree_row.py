"""Tests for `command_center/desktop/components/worktree_row.py` (Desktop D2C).

`WorktreeRow` renders one worktree of an ``ok`` project (`WORKSPACE_HOME_SPEC.md`
§4): path, branch, short HEAD — branch shown exactly as `git_info` returns it,
including a literal detached-HEAD marker (no invented label, no second source of
truth). It tolerates missing keys defensively.
"""

from __future__ import annotations

from command_center.desktop.components.worktree_row import WorktreeRow


def test_worktree_row_shows_path_branch_and_short_head(qtbot):
    row = WorktreeRow({"path": "/repo/wt", "branch": "main", "head": "abc1234567"})
    qtbot.addWidget(row)
    assert row.path_text() == "/repo/wt"
    assert row.branch_text() == "main"
    assert row.head_text() == "abc1234567"


def test_worktree_row_passes_detached_head_marker_through_unchanged(qtbot):
    row = WorktreeRow({"path": "/r", "branch": "(detached HEAD)", "head": "deadbeef12"})
    qtbot.addWidget(row)
    assert row.branch_text() == "(detached HEAD)"


def test_worktree_row_tolerates_missing_fields(qtbot):
    row = WorktreeRow({})
    qtbot.addWidget(row)
    assert row.path_text() == ""
    assert row.branch_text() == ""
    assert row.head_text() == ""
