"""Unit tests for the completion pipeline's adapters:
`runtime.repo_state`, `runtime.github`, `runtime.validation`, `runtime.git_ops`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from command_center.runtime import github, git_ops, repo_state, validation
from tests.completion_helpers import build_repo, git, make_task_branch, merge_into_main


# ============================================================================
# repo_state
# ============================================================================
def test_inspect_repository_detects_task_commit(tmp_path):
    remote, work = build_repo(tmp_path)
    head = make_task_branch(work, "task/x")
    state = repo_state.inspect_repository(str(work), base_branch="main", branch="task/x", check_remote=False)
    assert state.is_repo
    assert state.current_branch == "task/x"
    assert not state.dirty
    assert state.head_commit == head
    assert state.commits_ahead_of_base == 1
    assert state.has_task_commit
    assert not state.head_is_base_tip
    assert not state.commit_in_target


def test_inspect_repository_head_is_base_tip_when_no_commit(tmp_path):
    remote, work = build_repo(tmp_path)
    git(work, "checkout", "-b", "task/empty")
    state = repo_state.inspect_repository(str(work), base_branch="main", branch="task/empty", check_remote=False)
    assert state.head_is_base_tip
    assert not state.has_task_commit


def test_inspect_repository_missing_worktree(tmp_path):
    state = repo_state.inspect_repository(str(tmp_path / "nope"), base_branch="main")
    assert not state.worktree_exists
    assert state.error == "worktree_missing"


def test_is_ancestor_and_commit_in_target_after_merge(tmp_path):
    remote, work = build_repo(tmp_path)
    make_task_branch(work, "task/x")
    git(work, "push", "origin", "task/x")
    merge_into_main(remote, tmp_path, "task/x", squash=False)
    repo_state.fetch(work)
    state = repo_state.inspect_repository(str(work), base_branch="main", branch="task/x", check_remote=False)
    assert state.commit_in_target  # head is now an ancestor of origin/main


def test_remote_branch_exists(tmp_path):
    remote, work = build_repo(tmp_path)
    make_task_branch(work, "task/x")
    assert not repo_state.remote_branch_exists(work, "origin", "task/x")
    git(work, "push", "origin", "task/x")
    assert repo_state.remote_branch_exists(work, "origin", "task/x")


# ============================================================================
# github
# ============================================================================
def test_pull_request_state_merged_vs_closed():
    merged = github.pull_request_from_json({"number": 1, "state": "MERGED", "mergedAt": "t", "mergeCommit": {"oid": "abc"}})
    assert merged.is_merged and not merged.is_closed_unmerged
    assert merged.merge_commit == "abc"

    closed = github.pull_request_from_json({"number": 2, "state": "CLOSED", "mergedAt": None, "mergeCommit": None})
    assert closed.is_closed_unmerged and not closed.is_merged

    # A closed PR is NEVER treated as merged just because state != OPEN.
    assert not closed.is_merged


def test_classify_checks():
    assert github._classify_checks([{"conclusion": "SUCCESS"}]) == github.CHECKS_PASSING
    assert github._classify_checks([{"conclusion": "SUCCESS"}, {"conclusion": "FAILURE"}]) == github.CHECKS_FAILING
    assert github._classify_checks([{"status": "IN_PROGRESS"}]) == github.CHECKS_PENDING
    assert github._classify_checks([]) == github.CHECKS_NONE


def test_review_satisfied():
    assert github.PullRequestState(review_decision=None).review_satisfied
    assert github.PullRequestState(review_decision="APPROVED").review_satisfied
    assert not github.PullRequestState(review_decision="REVIEW_REQUIRED").review_satisfied
    assert not github.PullRequestState(review_decision="CHANGES_REQUESTED").review_satisfied


def test_fake_github_client_lifecycle():
    gh = github.FakeGitHubClient()
    assert gh.discover_pull_request(Path("."), branch="task/x") is None
    pr = gh.create_pull_request(Path("."), base="main", head="task/x", title="t", body="b")
    assert pr.is_open and pr.number == 1
    assert gh.discover_pull_request(Path("."), branch="task/x").number == 1
    merged = gh.merge_pull_request(Path("."), number=pr.number, method="squash")
    assert merged.is_merged
    assert gh.merged == [1]


def test_fake_github_merge_hook_runs():
    calls = []
    gh = github.FakeGitHubClient(on_merge=lambda pr: calls.append(pr.head_ref) or "mergesha")
    gh.create_pull_request(Path("."), base="main", head="task/y", title="t", body="b")
    merged = gh.merge_pull_request(Path("."), number=1)
    assert calls == ["task/y"]
    assert merged.merge_commit == "mergesha"


# ============================================================================
# validation
# ============================================================================
def test_parse_command_allowlist():
    cmd = validation.parse_command("pytest -q")
    assert cmd.argv == ["pytest", "-q"]
    with pytest.raises(validation.InvalidValidationCommand):
        validation.parse_command("rm -rf /")  # rm not allowlisted
    with pytest.raises(validation.InvalidValidationCommand):
        validation.parse_command("")


def test_resolve_validation_plan_layers():
    default = validation.resolve_validation_plan()
    assert default and default[0].argv[0] == "python3"
    task_plan = validation.resolve_validation_plan(task={"validation_commands": ["ruff check ."]})
    assert task_plan[0].argv == ["ruff", "check", "."]
    proj_plan = validation.resolve_validation_plan(project_cfg={"validation_commands": ["pytest"]})
    assert proj_plan[0].argv == ["pytest"]


def test_run_validation_pass_and_fail(tmp_path):
    (tmp_path / "ok.py").write_text("x = 1\n")
    plan = validation.resolve_validation_plan(task={"validation_commands": ["python3 -m compileall -q ."]})
    outcome = validation.run_validation(str(tmp_path), plan)
    assert outcome.ran and outcome.passed
    assert outcome.results[0].exit_code == 0

    (tmp_path / "broken.py").write_text("def (:\n")
    outcome2 = validation.run_validation(str(tmp_path), plan)
    assert outcome2.ran and not outcome2.passed
    assert outcome2.failed_commands


def test_run_validation_empty_plan_does_not_pass(tmp_path):
    outcome = validation.run_validation(str(tmp_path), [])
    assert outcome.ran
    assert not outcome.passed  # nothing ran => cannot claim validated


def test_validation_summary_truncation():
    long = "x" * 10000
    truncated = validation._truncate(long)
    assert len(truncated) < len(long)
    assert "truncated" in truncated


# ============================================================================
# git_ops
# ============================================================================
def test_git_ops_rejects_force_push(tmp_path):
    remote, work = build_repo(tmp_path)
    with pytest.raises(git_ops.GitOpsError):
        git_ops.run_git_write(work, ["push", "--force", "origin", "main"])
    with pytest.raises(git_ops.GitOpsError):
        git_ops.run_git_write(work, ["push", "origin", "+main"])


def test_push_branch_creates_remote_branch(tmp_path):
    remote, work = build_repo(tmp_path)
    make_task_branch(work, "task/x")
    assert not repo_state.remote_branch_exists(work, "origin", "task/x")
    git_ops.push_branch(work, remote="origin", branch="task/x")
    assert repo_state.remote_branch_exists(work, "origin", "task/x")
    # Idempotent: pushing again is a successful no-op.
    git_ops.push_branch(work, remote="origin", branch="task/x")
