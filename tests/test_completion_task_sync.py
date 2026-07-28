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


# --- P0 remediation: a resolved task masking an explicit pipeline rejection (D3)
# A `Done` task stays Done (its dependents remain released), but it must not keep
# displaying a green terminal `launch_status` while its completion pipeline is in
# an explicit rejection state (REVIEW_REJECTED / MERGE_BLOCKED / VALIDATION_FAILED
# / REQUIRES_ATTENTION / …). A benign failed *re-run* that seeds no rejection
# completion is NOT a regression and must be left untouched (preserves the
# existing "does not reopen a Done task from a failed run" behavior).


def test_done_task_masking_review_rejected_surfaces_attention():
    task = _task(status="Done", launch_status="Completed", progress=100)
    mutated = task_sync.flag_done_regression(task, {"completion_state": "REVIEW_REJECTED"})
    assert mutated is True
    assert task["launch_status"] == "Requires Attention"
    assert task["regressed_after_done"] is True
    assert task["status"] == "Done"  # dependents stay released


@pytest.mark.parametrize(
    "state",
    ["MERGE_BLOCKED", "VALIDATION_FAILED", "REQUIRES_ATTENTION", "PR_CLOSED_UNMERGED", "RECOVERY_FAILED"],
)
def test_done_task_masking_any_rejection_state_surfaces_attention(state):
    task = _task(status="Done", launch_status="Needs Review", progress=100)
    assert task_sync.flag_done_regression(task, {"completion_state": state}) is True
    assert task["launch_status"] == "Requires Attention"


def test_done_regression_is_idempotent():
    task = _task(status="Done", launch_status="Completed", progress=100)
    completion = {"completion_state": "REVIEW_REJECTED"}
    assert task_sync.flag_done_regression(task, completion) is True
    # Already surfaced — a later refresh must not re-flip or re-log.
    assert task_sync.flag_done_regression(task, completion) is False


def test_done_task_with_completed_completion_is_unchanged():
    task = _task(status="Done", launch_status="Completed", progress=100)
    assert task_sync.flag_done_regression(task, {"completion_state": "COMPLETED"}) is False
    assert task["launch_status"] == "Completed"


def test_done_task_with_no_completion_is_unchanged():
    # A benign failed re-run (e.g. working_tree_unchanged) seeds no rejection
    # completion — a genuinely-resolved task must stay "Completed".
    task = _task(status="Done", launch_status="Completed", progress=100)
    assert task_sync.flag_done_regression(task, None) is False
    assert task["launch_status"] == "Completed"


def test_non_done_task_is_never_flagged():
    task = _task(status="Backlog", launch_status="Failed")
    assert task_sync.flag_done_regression(task, {"completion_state": "REVIEW_REJECTED"}) is False


def test_regressed_done_task_recovers_when_completion_returns_to_completed():
    task = _task(
        status="Done", launch_status="Requires Attention",
        progress=100, regressed_after_done=True,
    )
    mutated = task_sync.flag_done_regression(task, {"completion_state": "COMPLETED"})
    assert mutated is True
    assert task["launch_status"] == "Completed"
    assert task.get("regressed_after_done") in (False, None)


# --- P0 remediation: "merged" without merge evidence (audit D4) --------------
# `pull_request_status="merged"` and a "Merged into target branch" timeline entry
# are factual claims (recommendation scoring reads the field; the UI shows the
# badge). A local-only completion (COMPLETED with allow_local_only, so no PR and
# no merge commit) never merged anything and must assert neither.


def test_completed_without_merge_evidence_is_not_labelled_merged():
    task = _task(progress=95)
    completion = {"completion_state": "COMPLETED", "pull_request_url": None, "merge_commit": None}
    task_sync.sync_task_from_completion(task, completion)
    assert task.get("pull_request_status") != "merged"


def test_local_only_completion_timeline_does_not_claim_merge():
    task = _task(progress=95)
    completion = {"completion_state": "COMPLETED", "pull_request_url": None, "merge_commit": None}
    task_sync.sync_task_from_completion(task, completion)
    assert "Merged into target branch" not in str(task.get("timeline", []))


def test_completed_with_merge_commit_is_labelled_merged():
    task = _task(progress=95)
    completion = {"completion_state": "COMPLETED", "pull_request_url": None, "merge_commit": "abc1234"}
    assert task_sync.sync_task_from_completion(task, completion) is True
    assert task["pull_request_status"] == "merged"


def test_completed_with_pull_request_url_is_labelled_merged():
    task = _task(progress=95)
    completion = {"completion_state": "COMPLETED", "pull_request_url": "https://github.com/x/y/pull/7"}
    task_sync.sync_task_from_completion(task, completion)
    assert task["pull_request_status"] == "merged"
