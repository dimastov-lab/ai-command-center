"""Unit tests for the pure completion evaluator (`runtime.completion`).

These cover the "Completion evaluation" cases 1-8 (plus the recovery-decision
cases 9/13/14) from the AICC-AUTONOMY-001 spec, exercising only the pure
decision logic — no git, no gh, no database.
"""

from __future__ import annotations

from command_center.runtime import completion as C
from command_center.runtime.completion import CompletionEvaluator, CompletionPolicy, CompletionState, ReasonCode
from command_center.runtime.github import PullRequestState
from command_center.runtime.repo_state import RepositoryState
from command_center.runtime.validation import CommandResult, ValidationOutcome

EVAL = CompletionEvaluator()

COMPLETED_RUN = {"state": "COMPLETED"}


def _repo(**overrides) -> RepositoryState:
    base = dict(
        repository_path="/tmp/repo",
        worktree_exists=True,
        is_repo=True,
        dirty=False,
        head_commit="a" * 40,
        base_branch="main",
        commits_ahead_of_base=1,
        commit_in_target=False,
        remote_branch_exists=True,
    )
    base.update(overrides)
    return RepositoryState(**base)


def _validation(passed: bool) -> ValidationOutcome:
    exit_code = 0 if passed else 1
    return ValidationOutcome(
        ran=True,
        results=[
            CommandResult(
                command="pytest",
                exit_code=exit_code,
                started_at="a",
                finished_at="b",
                stdout_summary="",
                stderr_summary="",
            )
        ],
    )


def _pr(**overrides) -> PullRequestState:
    return PullRequestState(**overrides)


# 1 --------------------------------------------------------------------------
def test_exit0_but_dirty_worktree_is_not_complete():
    a = EVAL.evaluate(None, COMPLETED_RUN, _repo(dirty=True), policy=CompletionPolicy())
    assert not a.is_complete
    assert a.status == CompletionState.REQUIRES_ATTENTION
    assert a.reason_code == ReasonCode.UNCOMMITTED_CHANGES
    assert a.requires_human


# 2 --------------------------------------------------------------------------
def test_exit0_but_no_task_commit_is_not_complete():
    a = EVAL.evaluate(
        None, COMPLETED_RUN, _repo(commits_ahead_of_base=0, head_is_base_tip=True), policy=CompletionPolicy()
    )
    assert not a.is_complete
    assert a.status == CompletionState.REQUIRES_ATTENTION
    assert a.reason_code == ReasonCode.NO_TASK_COMMIT


# 3 --------------------------------------------------------------------------
def test_validation_fails_transitions_to_validation_failed():
    a = EVAL.evaluate(None, COMPLETED_RUN, _repo(), validation=_validation(False), policy=CompletionPolicy())
    assert a.status == CompletionState.VALIDATION_FAILED
    assert a.reason_code == ReasonCode.VALIDATION_FAILED
    assert a.requires_human
    assert not a.is_complete


def test_validation_not_yet_run_requests_validation():
    a = EVAL.evaluate(None, COMPLETED_RUN, _repo(), validation=None, policy=CompletionPolicy())
    assert a.status == CompletionState.VALIDATING_RESULT
    assert a.action == C.CompletionAction.RUN_VALIDATION


# 4 --------------------------------------------------------------------------
def test_validation_passes_and_pr_open_awaits_merge():
    pr = _pr(number=7, url="u", state="OPEN", checks_state="PASSING")
    a = EVAL.evaluate(None, COMPLETED_RUN, _repo(), pr, validation=_validation(True), policy=CompletionPolicy())
    assert a.status == CompletionState.AWAITING_MERGE
    assert a.action == C.CompletionAction.WAIT  # manual mode: never auto-merge
    assert a.pull_request_number == 7


# 5 --------------------------------------------------------------------------
def test_pr_merged_and_target_verified_is_completed():
    # commit_in_target True => definitive done.
    a = EVAL.evaluate(
        None,
        COMPLETED_RUN,
        _repo(commit_in_target=True),
        _pr(number=7, state="MERGED", merged_at="t", merge_commit="m"),
        validation=_validation(True),
        policy=CompletionPolicy(),
    )
    assert a.status == CompletionState.COMPLETED
    assert a.is_complete
    assert a.reason_code == ReasonCode.TARGET_VERIFIED


def test_pr_merged_but_target_not_yet_verified_goes_to_merged():
    a = EVAL.evaluate(
        None,
        COMPLETED_RUN,
        _repo(commit_in_target=False),
        _pr(number=7, state="MERGED", merged_at="t", merge_commit="m"),
        validation=_validation(True),
        policy=CompletionPolicy(),
    )
    assert a.status == CompletionState.MERGED
    assert a.action == C.CompletionAction.VERIFY_TARGET
    assert a.merge_commit == "m"


# 6 --------------------------------------------------------------------------
def test_pr_closed_unmerged_with_recovery_enabled_is_recoverable():
    policy = CompletionPolicy(allow_pr_recovery=True)
    a = EVAL.evaluate(
        None,
        COMPLETED_RUN,
        _repo(),
        _pr(number=7, state="CLOSED", merged_at=None, merge_commit=None),
        validation=_validation(True),
        policy=policy,
    )
    assert a.status == CompletionState.PR_CLOSED_UNMERGED
    assert a.action == C.CompletionAction.RECOVER_PULL_REQUEST
    assert a.is_recoverable


def test_pr_closed_unmerged_with_recovery_disabled_requires_attention():
    a = EVAL.evaluate(
        None,
        COMPLETED_RUN,
        _repo(),
        _pr(number=7, state="CLOSED", merged_at=None, merge_commit=None),
        validation=_validation(True),
        policy=CompletionPolicy(allow_pr_recovery=False),
    )
    assert a.status == CompletionState.REQUIRES_ATTENTION
    assert a.reason_code == ReasonCode.RECOVERY_DISABLED
    assert a.is_recoverable  # recoverable in principle, just not automatically
    assert a.requires_human


# 7 --------------------------------------------------------------------------
def test_pr_closed_but_with_merge_commit_takes_merged_path():
    # A closed PR that DID merge (merge commit present) must not be treated as
    # closed-unmerged.
    a = EVAL.evaluate(
        None,
        COMPLETED_RUN,
        _repo(commit_in_target=False),
        _pr(number=7, state="CLOSED", merged_at="t", merge_commit="deadbeef"),
        validation=_validation(True),
        policy=CompletionPolicy(),
    )
    assert a.status == CompletionState.MERGED
    assert a.action == C.CompletionAction.VERIFY_TARGET


# 8 --------------------------------------------------------------------------
def test_squash_merge_accepted_even_though_original_commit_not_in_main():
    # The original head commit is NOT an ancestor of main (squash rewrote it),
    # but repo_state resolved commit_in_target=True via the merge commit.
    a = EVAL.evaluate(
        None,
        COMPLETED_RUN,
        _repo(commit_in_target=True),
        _pr(number=7, state="MERGED", merged_at="t", merge_commit="squashsha"),
        validation=_validation(True),
        policy=CompletionPolicy(),
    )
    assert a.status == CompletionState.COMPLETED
    assert a.is_complete


# 13 -------------------------------------------------------------------------
def test_local_and_remote_gone_requires_human():
    a = EVAL.evaluate(
        None,
        COMPLETED_RUN,
        _repo(commits_ahead_of_base=0, head_is_base_tip=True, remote_branch_exists=False, commit_in_target=False),
        _pr(number=7, state="CLOSED", merged_at=None, merge_commit=None),
        validation=_validation(True),
        policy=CompletionPolicy(allow_pr_recovery=True),
    )
    # No task commit at all -> the NO_TASK_COMMIT guard fires first.
    assert a.status == CompletionState.REQUIRES_ATTENTION
    assert a.requires_human


# 14 -------------------------------------------------------------------------
def test_commit_already_in_main_despite_closed_pr_is_completed():
    a = EVAL.evaluate(
        None,
        COMPLETED_RUN,
        _repo(commit_in_target=True),
        _pr(number=7, state="CLOSED", merged_at=None, merge_commit=None),
        validation=_validation(True),
        policy=CompletionPolicy(),
    )
    assert a.status == CompletionState.COMPLETED
    assert a.is_complete
    assert a.reason_code == ReasonCode.TARGET_VERIFIED


# Auto-merge behavior -------------------------------------------------------
def test_auto_merge_ready_when_checks_pass():
    a = EVAL.evaluate(
        None,
        COMPLETED_RUN,
        _repo(),
        _pr(number=7, state="OPEN", checks_state="PASSING", mergeable="MERGEABLE"),
        validation=_validation(True),
        policy=CompletionPolicy(merge_mode=C.MERGE_AUTO_AFTER_CHECKS),
    )
    assert a.status == CompletionState.AWAITING_MERGE
    assert a.action == C.CompletionAction.MERGE


def test_auto_merge_blocked_when_checks_failing():
    a = EVAL.evaluate(
        None,
        COMPLETED_RUN,
        _repo(),
        _pr(number=7, state="OPEN", checks_state="FAILING"),
        validation=_validation(True),
        policy=CompletionPolicy(merge_mode=C.MERGE_AUTO_AFTER_CHECKS),
    )
    assert a.status == CompletionState.MERGE_BLOCKED
    assert a.reason_code == ReasonCode.CHECKS_FAILING


def test_auto_merge_blocked_when_review_required():
    a = EVAL.evaluate(
        None,
        COMPLETED_RUN,
        _repo(),
        _pr(number=7, state="OPEN", checks_state="PASSING", mergeable="MERGEABLE", review_decision="REVIEW_REQUIRED"),
        validation=_validation(True),
        policy=CompletionPolicy(merge_mode=C.MERGE_AUTO_AFTER_CHECKS_AND_REVIEW),
    )
    assert a.status == CompletionState.MERGE_BLOCKED
    assert a.reason_code == ReasonCode.REVIEW_REQUIRED


def test_no_pr_yet_prepares_pull_request():
    a = EVAL.evaluate(None, COMPLETED_RUN, _repo(), None, validation=_validation(True), policy=CompletionPolicy())
    assert a.status == CompletionState.PREPARING_PULL_REQUEST
    assert a.action == C.CompletionAction.OPEN_PULL_REQUEST


def test_local_only_completion_without_pr():
    policy = CompletionPolicy(requires_pull_request=False, allow_local_only=True)
    a = EVAL.evaluate(None, COMPLETED_RUN, _repo(), None, validation=_validation(True), policy=policy)
    assert a.status == CompletionState.COMPLETED
    assert a.reason_code == ReasonCode.LOCAL_ONLY_COMPLETE


def test_policy_resolve_layers_task_over_project():
    policy = CompletionPolicy.resolve(
        task={"merge_mode": "auto_after_checks"},
        project_cfg={"merge_mode": "manual", "allow_pr_recovery": True},
    )
    assert policy.merge_mode == "auto_after_checks"
    assert policy.allow_pr_recovery is True


def test_policy_json_roundtrip():
    policy = CompletionPolicy(allow_pr_recovery=True, merge_mode="auto_after_checks", merge_method="rebase")
    restored = CompletionPolicy.from_json(policy.to_json())
    assert restored.allow_pr_recovery is True
    assert restored.merge_mode == "auto_after_checks"
    assert restored.merge_method == "rebase"
