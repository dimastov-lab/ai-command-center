"""The blocking independent review gate: no pull request without approval.

Covers the three layers that make the gate real rather than advisory:

  * the **state machine** — `AWAITING_REVIEW` has no edge back to
    `RESULT_VALID`, so a second pass cannot re-enter the pre-gate state and skip
    it, and `REVIEW_REJECTED` is terminal;
  * the **orchestrator** — with the policy on, `RESULT_VALID` is parked at the
    gate and no PR is opened until a verdict is written; a missing or
    unreadable verdict keeps the gate closed;
  * the **pipeline** — it launches the reviewer read-only and writes the verdict
    back, and a rejection feeds the existing rework loop.
"""

from __future__ import annotations

import pytest

from command_center import models, task_pipeline
from command_center.pipeline_settings import PipelineSettings
from command_center.runtime import api as runtime_api
from command_center.runtime import completion as completion_domain
from command_center.runtime import db as runtime_db
from command_center.runtime.completion import CompletionPolicy, CompletionState
from command_center.runtime.completion_service import (
    REVIEW_VERDICT_APPROVED,
    REVIEW_VERDICT_REJECTED,
    CompletionOrchestrator,
)
from command_center.runtime.github import FakeGitHubClient
from tests.completion_helpers import build_repo, make_task_branch, merge_into_main, seed_completed_run

NOW = __import__("datetime").datetime(2026, 7, 24, 12, 0, 0)


@pytest.fixture
def env(tmp_path):
    db_path = runtime_db.resolve_db_path()
    runtime_db.migrate(db_path)
    remote, work = build_repo(tmp_path)
    return db_path, remote, work, tmp_path


def _cfg(**extra):
    cfg = {"default_branch": "main", "merge_mode": "auto_after_checks", "requires_independent_review": True}
    cfg.update(extra)
    return cfg


def _states(db_path, run_id):
    return [e["event_type"] for e in runtime_db.list_completion_events(db_path, run_id)]


# --------------------------------------------------------------------------
# The state machine cannot be talked out of the gate
# --------------------------------------------------------------------------


def test_the_gate_has_no_edge_back_to_the_pre_gate_state():
    """Re-entering RESULT_VALID would let a second pass skip the gate."""
    assert not completion_domain.is_valid_completion_transition(
        CompletionState.AWAITING_REVIEW, CompletionState.RESULT_VALID
    )


def test_a_rejected_review_is_terminal():
    assert CompletionState.REVIEW_REJECTED in completion_domain.TERMINAL_STATES
    for target in (CompletionState.PULL_REQUEST_OPEN, CompletionState.MERGED, CompletionState.COMPLETED):
        assert not completion_domain.is_valid_completion_transition(
            CompletionState.REVIEW_REJECTED, target
        )


def test_review_is_off_by_default():
    assert CompletionPolicy().requires_independent_review is False
    assert PipelineSettings().require_independent_review is False
    assert PipelineSettings(enabled=True).independent_review_active is False


# --------------------------------------------------------------------------
# The orchestrator holds the row and opens no pull request
# --------------------------------------------------------------------------


def test_no_pull_request_is_opened_while_the_review_is_pending(env):
    db_path, remote, work, tmp = env
    branch = "task/review-a"
    make_task_branch(work, branch)
    run = seed_completed_run(db_path, repository_path=str(work), branch=branch)

    gh = FakeGitHubClient()
    orch = CompletionOrchestrator(db_path, github=gh)
    orch.begin_completion(run, project_cfg=_cfg())
    orch.advance(run["id"], now=NOW)

    row = runtime_db.get_completion(db_path, run["id"])
    assert row["completion_state"] == CompletionState.AWAITING_REVIEW
    assert gh.created == [], "a pull request was opened before the review approved it"
    assert row["pull_request_number"] is None
    assert "REVIEW_REQUESTED" in _states(db_path, run["id"])


def test_an_approved_review_lets_the_pull_request_proceed(env):
    db_path, remote, work, tmp = env
    branch = "task/review-b"
    make_task_branch(work, branch)
    run = seed_completed_run(db_path, repository_path=str(work), branch=branch)

    gh = FakeGitHubClient(on_merge=lambda pr: merge_into_main(remote, tmp, pr.head_ref))
    orch = CompletionOrchestrator(db_path, github=gh)
    orch.begin_completion(run, project_cfg=_cfg())
    orch.advance(run["id"], now=NOW)

    row = runtime_db.get_completion(db_path, run["id"])
    runtime_db.update_completion(
        db_path, run["id"], expected_version=row["version"],
        fields={"review_verdict": REVIEW_VERDICT_APPROVED, "review_summary": "ок"},
    )
    orch.advance(run["id"], now=NOW)

    row = runtime_db.get_completion(db_path, run["id"])
    assert row["completion_state"] == CompletionState.COMPLETED
    assert row["pull_request_number"] is not None
    assert "REVIEW_APPROVED" in _states(db_path, run["id"])


def test_a_rejected_review_stops_the_pipeline_and_opens_nothing(env):
    db_path, remote, work, tmp = env
    branch = "task/review-c"
    make_task_branch(work, branch)
    run = seed_completed_run(db_path, repository_path=str(work), branch=branch)

    gh = FakeGitHubClient()
    orch = CompletionOrchestrator(db_path, github=gh)
    orch.begin_completion(run, project_cfg=_cfg())
    orch.advance(run["id"], now=NOW)

    row = runtime_db.get_completion(db_path, run["id"])
    runtime_db.update_completion(
        db_path, run["id"], expected_version=row["version"],
        fields={"review_verdict": REVIEW_VERDICT_REJECTED, "review_summary": "теряются данные"},
    )
    orch.advance(run["id"], now=NOW)

    row = runtime_db.get_completion(db_path, run["id"])
    assert row["completion_state"] == CompletionState.REVIEW_REJECTED
    assert row["requires_human"] == 1
    assert gh.created == []
    assert "теряются данные" in row["recommended_action"]


def test_without_the_policy_the_gate_is_not_entered_at_all(env):
    """Existing behaviour must be untouched when the gate is off."""
    db_path, remote, work, tmp = env
    branch = "task/review-d"
    make_task_branch(work, branch)
    run = seed_completed_run(db_path, repository_path=str(work), branch=branch)

    gh = FakeGitHubClient(on_merge=lambda pr: merge_into_main(remote, tmp, pr.head_ref))
    orch = CompletionOrchestrator(db_path, github=gh)
    orch.begin_completion(run, project_cfg=_cfg(requires_independent_review=False))
    orch.advance(run["id"], now=NOW)

    row = runtime_db.get_completion(db_path, run["id"])
    assert row["completion_state"] == CompletionState.COMPLETED
    assert "REVIEW_REQUESTED" not in _states(db_path, run["id"])


# --------------------------------------------------------------------------
# The pipeline half: launching the reviewer and recording its verdict
# --------------------------------------------------------------------------


def _await_review(db_path, work, branch="task/review-x"):
    make_task_branch(work, branch)
    run = seed_completed_run(db_path, repository_path=str(work), branch=branch)
    orch = CompletionOrchestrator(db_path, github=FakeGitHubClient())
    orch.begin_completion(run, project_cfg=_cfg())
    orch.advance(run["id"], now=NOW)
    return run


def test_an_unreadable_review_is_treated_as_rejection_never_approval(env):
    """The only safe default for a blocking gate."""
    db_path, remote, work, tmp = env
    run = _await_review(db_path, work)
    api = runtime_api.ExecutionCenterAPI(db_path=db_path)
    row = runtime_db.get_completion(db_path, run["id"])

    # A reviewer run that failed and produced no parseable verdict.
    failed = seed_completed_run(
        db_path, repository_path=str(work), branch="task/review-x", state="FAILED"
    )
    record = task_pipeline._record_review_verdict(api, row, runtime_db.get_run(db_path, failed["id"]))

    assert record["approved"] is False
    fresh = runtime_db.get_completion(db_path, run["id"])
    assert fresh["review_verdict"] == REVIEW_VERDICT_REJECTED


def test_the_reviewer_runs_read_only():
    """Independence is enforced by the process contract: the read-only profile's
    tool set contains no shell and no editor, so the reviewer physically cannot
    modify the change it is judging."""
    from command_center import agent_runner

    assert (
        agent_runner.profile_for_task_type(task_pipeline.REVIEW_TASK_TYPE)
        == agent_runner.PROFILE_READ_ONLY
    )


def test_a_rejected_review_feeds_the_rework_loop():
    assert CompletionState.REVIEW_REJECTED in task_pipeline.REWORK_TRIGGER_STATES


def test_the_review_instruction_demands_an_explicit_verdict():
    row = {"branch": "task/x", "base_branch": "main", "task_id": "t1"}
    text = task_pipeline._review_instruction({"title": "Задача", "goal": "цель"}, row)
    assert models.VERDICT_APPROVED_FOR_COMMIT in text
    assert models.VERDICT_NOT_APPROVED_FOR_COMMIT in text
    assert "только для чтения" in text


def test_the_pipeline_opt_in_turns_the_policy_on():
    overrides = task_pipeline.merge_policy_overrides(
        PipelineSettings(enabled=True, require_independent_review=True)
    )
    assert overrides["requires_independent_review"] is True
    assert CompletionPolicy.resolve(overrides=overrides).requires_independent_review is True


def test_no_reviewer_is_started_without_auto_launch(env):
    db_path, remote, work, tmp = env
    run = _await_review(db_path, work)
    api = runtime_api.ExecutionCenterAPI(db_path=db_path)
    records = task_pipeline.advance_reviews(
        api, {}, settings=PipelineSettings(enabled=True, require_independent_review=True)
    )
    assert records == []
    assert runtime_db.get_completion(db_path, run["id"])["review_run_id"] is None
