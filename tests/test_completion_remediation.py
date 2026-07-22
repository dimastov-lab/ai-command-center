"""Founder Gate remediation regression tests (AICC-AUTONOMY-001).

Covers the blocking Major finding (honest default status projection) and the two
related Minor findings (benign compare-and-set loss must not become human
attention; structural completion-state transition guard). Uses REAL git (via
`completion_helpers`) plus a fake GitHub client, exactly like the scenario suite.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from command_center.runtime import api as runtime_api
from command_center.runtime import db as runtime_db
from command_center.runtime import task_sync
from command_center.runtime.completion import (
    COMPLETION_TRANSITIONS,
    TERMINAL_STATES,
    CompletionState,
    is_valid_completion_transition,
)
from command_center.runtime.completion_service import CompletionOrchestrator
from command_center.runtime.github import FakeGitHubClient
from tests.completion_helpers import (
    build_repo,
    make_task_branch,
    merge_into_main,
    seed_completed_run,
)

NOW = datetime(2026, 7, 22, 12, 0, 0)


@pytest.fixture
def db_path():
    p = runtime_db.resolve_db_path()
    runtime_db.migrate(p)
    return p


@pytest.fixture
def repo_env(tmp_path):
    remote, work = build_repo(tmp_path)
    return remote, work, tmp_path


def _make_run(db_path):
    """Minimal task/session/run (no real repo needed) for pure-DB tests."""
    task = runtime_db.create_task(db_path, project="AICC", title="t", task_type="implementation")
    session = runtime_db.create_session(
        db_path, task_id=task["id"], project="AICC", repository_path="/tmp/x"
    )
    return runtime_db.create_run(
        db_path, session_id=session["id"], task_id=task["id"], project="AICC",
        task_type="implementation", repository_path="/tmp/x", prompt="p", is_resume=False,
    )


def _seed_state(db_path, run, state):
    return runtime_db.create_completion(
        db_path, run_id=run["id"], task_id=run["task_id"], project="AICC",
        repository_path="/tmp/x", completion_state=state,
    )


def _task_for(run, **overrides):
    task = {
        "id": run["task_id"], "project": run["project"], "title": "t",
        "progress": 5, "launch_status": "Launching", "current_run_id": run["id"],
    }
    task.update(overrides)
    return task


# ============================================================================
# BLOCKING MAJOR — honest default status projection (autopilot OFF)
# ============================================================================
def test_founder_gate_default_autopilot_off_completed_is_needs_review(repo_env):
    """The exact Founder Gate scenario: a genuinely-completed run, autopilot
    disabled (the default), reconciled/synced. The seeded completion row must
    surface as "Needs Review" — NEVER a stuck "Running"."""
    remote, work, tmp = repo_env
    api = runtime_api.ExecutionCenterAPI()
    db_path = api.db_path
    branch = "task/gate"
    make_task_branch(work, branch)
    run = seed_completed_run(db_path, repository_path=str(work), branch=branch)
    task = _task_for(run)

    # 1-4: one reconcile/sync tick; nothing advances the row (autopilot off).
    mutated = task_sync.reconcile_and_sync(api, [task])
    assert mutated, "sync should have projected the completed run onto the task"
    completion = runtime_db.get_completion(db_path, run["id"])
    assert completion is not None  # 4: completion row IS seeded
    assert completion["completion_state"] == CompletionState.EXECUTION_FINISHED

    # 5: launch_status is Needs Review, NOT Running.
    assert task["launch_status"] == "Needs Review"

    # 6-7: repeated idle ticks remain stable; never regress to Running.
    for _ in range(4):
        task_sync.reconcile_and_sync(api, [task])
        row = runtime_db.get_completion(db_path, run["id"])
        assert row["completion_state"] == CompletionState.EXECUTION_FINISHED
        assert task["launch_status"] == "Needs Review"


def test_execution_finished_projection_is_needs_review():
    """Unit guard on the mapping itself: EXECUTION_FINISHED -> Needs Review."""
    task = {"progress": 5, "launch_status": "Launching"}
    task_sync.sync_task_from_completion(
        task, {"completion_state": CompletionState.EXECUTION_FINISHED, "pull_request_url": None}
    )
    assert task["launch_status"] == "Needs Review"


# ============================================================================
# 8 — autopilot-enabled flows still progress through the completion lifecycle
# ============================================================================
def test_autopilot_enabled_advances_off_seed_manual(repo_env):
    """With the autopilot advancing (manual merge default), the row leaves the
    EXECUTION_FINISHED seed and reaches AWAITING_MERGE -> "Needs Review"."""
    remote, work, tmp = repo_env
    db_path = runtime_db.resolve_db_path()
    runtime_db.migrate(db_path)
    branch = "task/gate-manual"
    make_task_branch(work, branch)
    run = seed_completed_run(db_path, repository_path=str(work), branch=branch)

    orch = CompletionOrchestrator(db_path, github=FakeGitHubClient())
    orch.begin_completion(run, project_cfg={"default_branch": "main"})
    orch.advance(run["id"], now=NOW)  # the autopilot's step

    completion = runtime_db.get_completion(db_path, run["id"])
    assert completion["completion_state"] == CompletionState.AWAITING_MERGE
    task = _task_for(run)
    task_sync.sync_task_from_completion(task, completion)
    assert task["launch_status"] == "Needs Review"


def test_autopilot_enabled_reaches_completed_auto(repo_env):
    """Auto-merge policy advances all the way to COMPLETED and projects
    "Completed"/progress 100 — the full lifecycle is intact."""
    remote, work, tmp = repo_env
    db_path = runtime_db.resolve_db_path()
    runtime_db.migrate(db_path)
    branch = "task/gate-auto"
    make_task_branch(work, branch)
    run = seed_completed_run(db_path, repository_path=str(work), branch=branch)

    gh = FakeGitHubClient(on_merge=lambda pr: merge_into_main(remote, tmp, pr.head_ref, squash=False))
    orch = CompletionOrchestrator(db_path, github=gh)
    orch.begin_completion(run, project_cfg={"default_branch": "main", "merge_mode": "auto_after_checks"})
    orch.advance(run["id"], now=NOW)

    completion = runtime_db.get_completion(db_path, run["id"])
    assert completion["completion_state"] == CompletionState.COMPLETED
    task = _task_for(run, progress=95)
    task_sync.sync_task_from_completion(task, completion)
    assert task["launch_status"] == "Completed"
    assert task["progress"] == 100


# ============================================================================
# MINOR — benign lost CAS must not become human attention
# ============================================================================
def test_advance_pending_benign_cas_loss_is_skipped(repo_env, monkeypatch):
    """A LostUpdateError raised while advancing is benign concurrent progress:
    the row is skipped, never terminalized, and no counter is bumped."""
    remote, work, tmp = repo_env
    db_path = runtime_db.resolve_db_path()
    runtime_db.migrate(db_path)
    branch = "task/cas"
    make_task_branch(work, branch)
    run = seed_completed_run(db_path, repository_path=str(work), branch=branch)
    orch = CompletionOrchestrator(db_path, github=FakeGitHubClient())
    orch.begin_completion(run, project_cfg={"default_branch": "main"})
    before = runtime_db.get_completion(db_path, run["id"])

    def _lose(run_id, *, now=None):
        raise runtime_db.LostUpdateError("simulated concurrent advance")

    monkeypatch.setattr(orch, "advance", _lose)
    results = orch.advance_pending(now=NOW)

    after = runtime_db.get_completion(db_path, run["id"])
    assert results == []  # the losing row produced no AdvanceResult
    assert after["completion_state"] == before["completion_state"]  # not terminalized
    assert after["completion_state"] != CompletionState.REQUIRES_ATTENTION
    assert after["requires_human"] == 0
    assert after["retry_count"] == before["retry_count"]  # no benign-loss counter bump
    assert after["version"] == before["version"]  # the loser wrote nothing at all


def test_advance_pending_missing_row_is_skipped(repo_env, monkeypatch):
    """If the completion row genuinely disappeared (concurrent cleanup), the
    generic handler skips instead of raising while trying to mark attention."""
    remote, work, tmp = repo_env
    db_path = runtime_db.resolve_db_path()
    runtime_db.migrate(db_path)
    branch = "task/gone"
    make_task_branch(work, branch)
    run = seed_completed_run(db_path, repository_path=str(work), branch=branch)
    orch = CompletionOrchestrator(db_path, github=FakeGitHubClient())
    orch.begin_completion(run, project_cfg={"default_branch": "main"})

    def _boom_then_delete(run_id, *, now=None):
        # Simulate the run/task being deleted (cascading the completion away)
        # concurrently with the advance.
        with runtime_db.connect(db_path) as conn:
            with runtime_db.transaction(conn):
                conn.execute("DELETE FROM completion WHERE run_id = ?", (run_id,))
        raise RuntimeError("advance blew up after concurrent deletion")

    monkeypatch.setattr(orch, "advance", _boom_then_delete)
    # Must NOT raise (the missing-row branch skips cleanly).
    results = orch.advance_pending(now=NOW)
    assert results == []
    assert runtime_db.get_completion(db_path, run["id"]) is None


def test_two_advancers_winner_preserved_loser_skips(repo_env):
    """Realistic race: a winner lands a legitimate forward transition; the
    losing advancer CAS-loses and must NOT clobber that progress to attention."""
    remote, work, tmp = repo_env
    db_path = runtime_db.resolve_db_path()
    runtime_db.migrate(db_path)
    branch = "task/race"
    make_task_branch(work, branch)
    run = seed_completed_run(db_path, repository_path=str(work), branch=branch)
    orch = CompletionOrchestrator(db_path, github=FakeGitHubClient())
    orch.begin_completion(
        run,
        project_cfg={
            "default_branch": "main", "validation_required": False,
            "requires_pull_request": False, "allow_local_only": True,
        },
    )

    original_step = orch._step
    calls = {"n": 0}

    def racing_step(row, *, now):
        if calls["n"] == 0:
            calls["n"] += 1
            winner = runtime_db.get_completion(db_path, row["run_id"])
            runtime_db.update_completion(
                db_path, row["run_id"], expected_version=winner["version"],
                fields={
                    "completion_state": CompletionState.RESULT_VALID,
                    "last_reason_code": "VALIDATION_PASSED",
                },
            )
        return original_step(row, now=now)

    orch._step = racing_step
    results = orch.advance_pending(now=NOW)

    after = runtime_db.get_completion(db_path, run["id"])
    assert results == []  # loser skipped
    assert after["completion_state"] == CompletionState.RESULT_VALID  # winner preserved
    assert after["completion_state"] != CompletionState.REQUIRES_ATTENTION
    assert after["requires_human"] == 0


# ============================================================================
# MINOR — structural completion-state transition guard
# ============================================================================
@pytest.mark.parametrize("current,targets", sorted(COMPLETION_TRANSITIONS.items(), key=lambda kv: kv[0]))
def test_declared_transitions_are_valid_and_self_loops_allowed(current, targets):
    for target in targets:
        assert is_valid_completion_transition(current, target), f"{current} -> {target}"
    assert is_valid_completion_transition(current, current)  # same-state always allowed


def test_every_orchestrator_transition_is_declared():
    """The transitions the orchestrator actually performs are all legal. This
    is a static allow-list check mirroring the real advance paths."""
    real = [
        (CompletionState.EXECUTION_FINISHED, CompletionState.RESULT_VALID),
        (CompletionState.EXECUTION_FINISHED, CompletionState.VALIDATION_FAILED),
        (CompletionState.EXECUTION_FINISHED, CompletionState.REQUIRES_ATTENTION),
        (CompletionState.EXECUTION_FINISHED, CompletionState.COMPLETED),
        (CompletionState.RESULT_VALID, CompletionState.PULL_REQUEST_OPEN),
        (CompletionState.RESULT_VALID, CompletionState.MERGED),
        (CompletionState.RESULT_VALID, CompletionState.AWAITING_MERGE),
        (CompletionState.PULL_REQUEST_OPEN, CompletionState.AWAITING_MERGE),
        (CompletionState.PULL_REQUEST_OPEN, CompletionState.MERGED),
        (CompletionState.AWAITING_MERGE, CompletionState.MERGED),
        (CompletionState.AWAITING_MERGE, CompletionState.MERGE_BLOCKED),
        (CompletionState.MERGE_BLOCKED, CompletionState.AWAITING_MERGE),
        (CompletionState.MERGED, CompletionState.VERIFYING_TARGET_BRANCH),
        (CompletionState.MERGED, CompletionState.COMPLETED),
        (CompletionState.VERIFYING_TARGET_BRANCH, CompletionState.COMPLETED),
        (CompletionState.PR_CLOSED_UNMERGED, CompletionState.PULL_REQUEST_OPEN),
        (CompletionState.PR_CLOSED_UNMERGED, CompletionState.RECOVERY_FAILED),
        (CompletionState.PULL_REQUEST_OPEN, CompletionState.RECOVERY_FAILED),
    ]
    for current, new in real:
        assert is_valid_completion_transition(current, new), f"{current} -> {new} should be legal"


@pytest.mark.parametrize(
    "current,new",
    [
        (CompletionState.MERGED, CompletionState.RESULT_VALID),
        (CompletionState.PULL_REQUEST_OPEN, CompletionState.EXECUTION_FINISHED),
        (CompletionState.VERIFYING_TARGET_BRANCH, CompletionState.VALIDATING_RESULT),
        (CompletionState.AWAITING_MERGE, CompletionState.EXECUTION_FINISHED),
    ],
)
def test_illegal_backward_transitions_rejected(current, new):
    assert not is_valid_completion_transition(current, new)


@pytest.mark.parametrize("terminal", sorted(TERMINAL_STATES))
def test_terminal_states_have_no_outgoing_transitions(terminal):
    assert COMPLETION_TRANSITIONS[terminal] == frozenset()
    # Any move to a different state out of a terminal state is illegal.
    assert not is_valid_completion_transition(terminal, CompletionState.MERGED)
    assert not is_valid_completion_transition(terminal, CompletionState.PULL_REQUEST_OPEN)
    # A same-state (idempotent) write is still permitted (never advanced anyway).
    assert is_valid_completion_transition(terminal, terminal)


def test_update_completion_rejects_transition_out_of_completed(db_path):
    run = _make_run(db_path)
    _seed_state(db_path, run, CompletionState.COMPLETED)
    with pytest.raises(runtime_db.InvalidCompletionTransitionError):
        runtime_db.update_completion(
            db_path, run["id"], expected_version=0,
            fields={"completion_state": CompletionState.MERGED},
        )


def test_update_completion_rejects_transition_out_of_requires_attention(db_path):
    run = _make_run(db_path)
    _seed_state(db_path, run, CompletionState.REQUIRES_ATTENTION)
    with pytest.raises(runtime_db.InvalidCompletionTransitionError):
        runtime_db.update_completion(
            db_path, run["id"], expected_version=0,
            fields={"completion_state": CompletionState.PULL_REQUEST_OPEN},
        )


def test_update_completion_rejects_backward_transition(db_path):
    run = _make_run(db_path)
    _seed_state(db_path, run, CompletionState.MERGED)
    with pytest.raises(runtime_db.InvalidCompletionTransitionError):
        runtime_db.update_completion(
            db_path, run["id"], expected_version=0,
            fields={"completion_state": CompletionState.RESULT_VALID},
        )


def test_update_completion_allows_same_state_retry_metadata(db_path):
    run = _make_run(db_path)
    row = _seed_state(db_path, run, CompletionState.AWAITING_MERGE)
    updated = runtime_db.update_completion(
        db_path, run["id"], expected_version=row["version"],
        fields={
            "completion_state": CompletionState.AWAITING_MERGE,  # same-state
            "next_retry_at": "2026-07-22T13:00:00",
            "retry_count": 3,
        },
    )
    assert updated["completion_state"] == CompletionState.AWAITING_MERGE
    assert updated["retry_count"] == 3


def test_update_completion_allows_metadata_only_update(db_path):
    run = _make_run(db_path)
    row = _seed_state(db_path, run, CompletionState.AWAITING_MERGE)
    updated = runtime_db.update_completion(
        db_path, run["id"], expected_version=row["version"],
        fields={"next_retry_at": "2026-07-22T13:00:00"},  # no completion_state at all
    )
    assert updated["completion_state"] == CompletionState.AWAITING_MERGE


def test_transition_checked_before_cas(db_path):
    """An illegal transition is rejected even with a stale version (transition
    guard runs first); a legal transition with a stale version still enforces
    CAS."""
    run = _make_run(db_path)
    _seed_state(db_path, run, CompletionState.COMPLETED)
    # Illegal transition + wrong version -> transition error (checked first).
    with pytest.raises(runtime_db.InvalidCompletionTransitionError):
        runtime_db.update_completion(
            db_path, run["id"], expected_version=999,
            fields={"completion_state": CompletionState.MERGED},
        )

    run2 = _make_run(db_path)
    _seed_state(db_path, run2, CompletionState.EXECUTION_FINISHED)
    # Legal transition + wrong version -> CAS still enforced.
    with pytest.raises(runtime_db.LostUpdateError):
        runtime_db.update_completion(
            db_path, run2["id"], expected_version=999,
            fields={"completion_state": CompletionState.RESULT_VALID},
        )
