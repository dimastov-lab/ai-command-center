"""Tests for projecting completion state onto the Kanban task (`task_sync`)."""

from __future__ import annotations

import pytest

from command_center import models
from command_center.runtime import task_sync


def _task(**overrides):
    task = models.new_task(project="AICC", title="t") if hasattr(models, "new_task") else {
        "id": "t1", "project": "AICC", "title": "t", "progress": 5, "launch_status": "Needs Review",
    }
    task.setdefault("progress", 5)
    task.setdefault("launch_status", "Needs Review")
    task.update(overrides)
    return task


@pytest.mark.parametrize(
    "state,expected_status",
    [
        # `EXECUTION_FINISHED` is the seed state (process finished, pipeline not
        # yet advanced) — it must project to the honest, actionable "Needs
        # Review", never "Running" (Founder Gate blocking finding). Only the
        # genuinely-active `VALIDATING_RESULT` (reachable only when the autopilot
        # is advancing) is "Running".
        ("EXECUTION_FINISHED", "Needs Review"),
        ("VALIDATING_RESULT", "Running"),
        ("PULL_REQUEST_OPEN", "Needs Review"),
        ("AWAITING_MERGE", "Needs Review"),
        ("MERGED", "Needs Review"),
        ("VERIFYING_TARGET_BRANCH", "Needs Review"),
        ("COMPLETED", "Completed"),
        ("VALIDATION_FAILED", "Requires Attention"),
        ("PR_CLOSED_UNMERGED", "Requires Attention"),
        ("REQUIRES_ATTENTION", "Requires Attention"),
        ("RECOVERY_FAILED", "Requires Attention"),
    ],
)
def test_launch_status_mapping(state, expected_status):
    task = _task()
    completion = {"completion_state": state, "pull_request_url": None}
    task_sync.sync_task_from_completion(task, completion)
    assert task["launch_status"] == expected_status


def test_completed_sets_progress_and_pr_status():
    task = _task(progress=95)
    completion = {
        "completion_state": "COMPLETED",
        "pull_request_url": "https://github.com/x/y/pull/7",
    }
    mutated = task_sync.sync_task_from_completion(task, completion)
    assert mutated
    assert task["launch_status"] == "Completed"
    assert task["progress"] == 100
    assert task["pull_request_status"] == "merged"
    assert task["pull_request_url"] == "https://github.com/x/y/pull/7"


def test_non_completed_does_not_force_progress_100():
    task = _task(progress=50)
    completion = {"completion_state": "AWAITING_MERGE", "pull_request_url": None}
    task_sync.sync_task_from_completion(task, completion)
    assert task["progress"] == 50  # unchanged
    assert task.get("pull_request_status") != "merged"


@pytest.mark.parametrize("task_type", ["review", "architecture_review", "final_gate"])
def test_read_only_completed_run_resolves_to_completed_not_needs_review(task_type):
    """A read-only task (review/audit/gate) has no PR to review and nothing to
    merge — a clean COMPLETED run is terminally "Completed", never "Needs Review"
    (and never "Requires Attention" from a spurious merge completion)."""
    from command_center.runtime import session_view

    task = _task(task_type=task_type, progress=5)  # progress never reaches 100 for these
    resolved = task_sync._resolve_target_launch_status(session_view.STATUS_COMPLETED, task)
    assert resolved == "Completed"


def test_non_read_only_completed_below_100_is_needs_review():
    """An implementation task still gets the merge lifecycle: COMPLETED at
    progress < 100 is "Needs Review" (PR ready, awaiting human), unchanged."""
    from command_center.runtime import session_view

    task = _task(task_type="implementation", progress=95)
    resolved = task_sync._resolve_target_launch_status(session_view.STATUS_COMPLETED, task)
    assert resolved == "Needs Review"


def test_projection_is_idempotent():
    task = _task()
    completion = {"completion_state": "COMPLETED", "pull_request_url": None}
    assert task_sync.sync_task_from_completion(task, completion) is True
    # Second application: no further mutation.
    assert task_sync.sync_task_from_completion(task, completion) is False
