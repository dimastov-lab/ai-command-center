"""End-to-end completion-pipeline tests against REAL git + a fake GitHub.

These cover the AICC-AUTONOMY-001 demonstration scenarios (A/B/C) and the
Recovery/Supervisor spec cases (9-20). Only the GitHub API is faked; the
repository, branches, pushes, merges, and target-branch verification are all
real git, so the pipeline's git logic is genuinely exercised.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from command_center.runtime import db as runtime_db
from command_center.runtime.completion import CompletionState
from command_center.runtime.completion_service import CompletionOrchestrator
from command_center.runtime.github import STATE_CLOSED, STATE_OPEN, FakeGitHubClient, GitHubError, PullRequestState
from tests.completion_helpers import (
    build_repo,
    make_task_branch,
    merge_into_main,
    seed_completed_run,
)

NOW = datetime(2026, 7, 22, 12, 0, 0)


@pytest.fixture
def env(tmp_path):
    db_path = runtime_db.resolve_db_path()
    runtime_db.migrate(db_path)
    remote, work = build_repo(tmp_path)
    return db_path, remote, work, tmp_path


def _events(db_path, run_id):
    return [e["event_type"] for e in runtime_db.list_completion_events(db_path, run_id)]


def _auto_cfg(**extra):
    cfg = {"default_branch": "main", "merge_mode": "auto_after_checks"}
    cfg.update(extra)
    return cfg


# ============================================================================
# Scenario A — normal completion
# ============================================================================
def test_scenario_a_normal_completion(env):
    db_path, remote, work, tmp = env
    branch = "task/aicc-a"
    make_task_branch(work, branch)
    run = seed_completed_run(db_path, repository_path=str(work), branch=branch)

    gh = FakeGitHubClient(on_merge=lambda pr: merge_into_main(remote, tmp, pr.head_ref, squash=False))
    orch = CompletionOrchestrator(db_path, github=gh)
    orch.begin_completion(run, project_cfg=_auto_cfg())

    orch.advance(run["id"], now=NOW)

    row = runtime_db.get_completion(db_path, run["id"])
    assert row["completion_state"] == CompletionState.COMPLETED
    assert row["pull_request_number"] is not None
    assert row["merge_commit"]
    assert row["requires_human"] == 0
    events = _events(db_path, run["id"])
    for expected in ("EXECUTION_COMPLETED", "VALIDATION_PASSED", "PR_CREATED", "PR_MERGED",
                     "TARGET_BRANCH_VERIFIED", "TASK_COMPLETED"):
        assert expected in events, f"missing {expected} in {events}"
    # Validation actually ran and was recorded.
    assert runtime_db.list_validation_results(db_path, run["id"])


def test_scenario_a_squash_merge_verified(env):
    # Squash merge: the original commit is NOT on main, but the squash (merge)
    # commit is — verification must still succeed.
    db_path, remote, work, tmp = env
    branch = "task/aicc-squash"
    make_task_branch(work, branch)
    run = seed_completed_run(db_path, repository_path=str(work), branch=branch)

    gh = FakeGitHubClient(on_merge=lambda pr: merge_into_main(remote, tmp, pr.head_ref, squash=True))
    orch = CompletionOrchestrator(db_path, github=gh)
    orch.begin_completion(run, project_cfg=_auto_cfg())
    orch.advance(run["id"], now=NOW)

    row = runtime_db.get_completion(db_path, run["id"])
    assert row["completion_state"] == CompletionState.COMPLETED


def test_scenario_a_manual_merge_awaits(env):
    # Default (manual) merge mode never auto-merges: it stops at AWAITING_MERGE.
    db_path, remote, work, tmp = env
    branch = "task/aicc-manual"
    make_task_branch(work, branch)
    run = seed_completed_run(db_path, repository_path=str(work), branch=branch)

    gh = FakeGitHubClient()
    orch = CompletionOrchestrator(db_path, github=gh)
    orch.begin_completion(run, project_cfg={"default_branch": "main"})  # merge_mode defaults to manual
    orch.advance(run["id"], now=NOW)

    row = runtime_db.get_completion(db_path, run["id"])
    assert row["completion_state"] == CompletionState.AWAITING_MERGE
    assert gh.merged == []  # never auto-merged


# ============================================================================
# Scenario B — closed PR without merge -> recovery
# ============================================================================
def test_scenario_b_closed_pr_recovery(env):
    db_path, remote, work, tmp = env
    branch = "task/aicc-b"
    make_task_branch(work, branch)
    run = seed_completed_run(db_path, repository_path=str(work), branch=branch)

    gh = FakeGitHubClient()
    # A pre-existing CLOSED, unmerged PR whose remote branch was deleted.
    gh.seed(PullRequestState(number=41, url="https://x/pull/41", state=STATE_CLOSED, head_ref=branch))
    orch = CompletionOrchestrator(db_path, github=gh)
    orch.begin_completion(run, project_cfg={"default_branch": "main", "allow_pr_recovery": True})

    orch.advance(run["id"], now=NOW)

    row = runtime_db.get_completion(db_path, run["id"])
    assert row["completion_state"] == CompletionState.AWAITING_MERGE  # manual: awaits after replacement
    assert row["replaced_pull_request_number"] == 41
    assert row["pull_request_number"] != 41  # a NEW replacement PR
    assert len(gh.created) == 1
    events = _events(db_path, run["id"])
    assert "PR_CLOSED_UNMERGED" in events
    assert "REMOTE_BRANCH_RECREATED" in events
    assert "REPLACEMENT_PR_CREATED" in events


def test_open_pull_request_adopts_a_pr_opened_in_the_race_window(env):
    """Audit M6: two advancers can both see no PR at discovery (step_pull_request)
    and both reach _open_pull_request. The re-discovery just before `create` must
    adopt the PR a peer opened in between rather than creating a duplicate (which
    `gh pr create` would reject anyway, erroring the loser)."""
    db_path, remote, work, tmp = env
    branch = "task/aicc-m6-race"
    make_task_branch(work, branch)
    run = seed_completed_run(db_path, repository_path=str(work), branch=branch)

    class _RaceGitHub(FakeGitHubClient):
        # First discovery (the evaluator's, at step_pull_request) sees no PR; the
        # second (inside _open_pull_request, just before create) sees the peer's.
        def __init__(self, race_pr):
            super().__init__()
            self._race_pr = race_pr
            self._discovers = 0

        def discover_pull_request(self, repo, *, branch, base=None):
            self._discovers += 1
            return None if self._discovers == 1 else self._race_pr

    race_pr = PullRequestState(
        number=99, url="https://x/pull/99", state=STATE_OPEN,
        mergeable="MERGEABLE", head_ref=branch, base_ref="main",
    )
    gh = _RaceGitHub(race_pr)
    orch = CompletionOrchestrator(db_path, github=gh)
    orch.begin_completion(run, project_cfg={"default_branch": "main"})
    for _ in range(6):
        orch.advance(run["id"], now=NOW)

    row = runtime_db.get_completion(db_path, run["id"])
    assert row["pull_request_number"] == 99  # adopted the peer's PR
    assert gh.created == []  # never created a duplicate


def test_scenario_b_recovery_disabled_requires_attention(env):
    db_path, remote, work, tmp = env
    branch = "task/aicc-b2"
    make_task_branch(work, branch)
    run = seed_completed_run(db_path, repository_path=str(work), branch=branch)

    gh = FakeGitHubClient()
    gh.seed(PullRequestState(number=41, state=STATE_CLOSED, head_ref=branch))
    orch = CompletionOrchestrator(db_path, github=gh)
    orch.begin_completion(run, project_cfg={"default_branch": "main"})  # recovery disabled by default

    orch.advance(run["id"], now=NOW)

    row = runtime_db.get_completion(db_path, run["id"])
    assert row["completion_state"] == CompletionState.REQUIRES_ATTENTION
    assert row["requires_human"] == 1
    assert row["is_recoverable"] == 1
    assert gh.created == []  # never auto-recovered


# ============================================================================
# Scenario C — validation failure
# ============================================================================
def test_scenario_c_validation_failure(env):
    db_path, remote, work, tmp = env
    branch = "task/aicc-c"
    # A syntactically broken file makes `compileall` (the default plan) fail.
    make_task_branch(work, branch, filename="broken.py", content="def (:\n    pass\n")
    run = seed_completed_run(db_path, repository_path=str(work), branch=branch)

    gh = FakeGitHubClient()
    orch = CompletionOrchestrator(db_path, github=gh)
    orch.begin_completion(run, project_cfg=_auto_cfg())

    orch.advance(run["id"], now=NOW)

    row = runtime_db.get_completion(db_path, run["id"])
    assert row["completion_state"] == CompletionState.VALIDATION_FAILED
    assert row["requires_human"] == 1
    assert gh.created == []  # never opened a PR
    assert "VALIDATION_FAILED" in _events(db_path, run["id"])
    results = runtime_db.list_validation_results(db_path, run["id"])
    assert results and any(r["exit_code"] not in (0, None) for r in results)


# ============================================================================
# Completion-evidence: dirty tree / no commit (service level)
# ============================================================================
def test_agent_uncommitted_work_is_committed_and_pipeline_proceeds(env):
    """The agent runs sandboxed (no git-write), so it leaves its work
    uncommitted in its own isolated worktree. The pipeline now COMMITS that work
    on the task branch and proceeds — it no longer stalls forever at
    UNCOMMITTED_CHANGES (the missing link that stopped every run reaching merge)."""
    db_path, remote, work, tmp = env
    branch = "task/aicc-dirty"
    make_task_branch(work, branch)
    (work / "uncommitted.py").write_text("y = 2\n")  # the agent's uncommitted work
    run = seed_completed_run(db_path, repository_path=str(work), branch=branch)

    orch = CompletionOrchestrator(db_path, github=FakeGitHubClient())
    orch.begin_completion(run, project_cfg=_auto_cfg())
    orch.advance(run["id"], now=NOW)

    row = runtime_db.get_completion(db_path, run["id"])
    # The pipeline committed the work and moved past the uncommitted gate.
    assert row["completion_state"] != CompletionState.REQUIRES_ATTENTION
    assert row["last_reason_code"] != "UNCOMMITTED_CHANGES"
    # The agent's file is now a real commit — it no longer shows as an
    # uncommitted change (build artifacts like __pycache__ may appear afterwards
    # from validation; those are not the agent's work and don't matter here).
    from command_center.runtime import git_ops
    status = git_ops.run_git_write(work, ["status", "--porcelain"])
    assert "uncommitted.py" not in (status.stdout or ""), "agent work should be committed"


def test_no_task_commit_requires_attention(env):
    db_path, remote, work, tmp = env
    # A task branch with NO commit -> HEAD equals the base tip, no work done.
    from tests.completion_helpers import git

    git(work, "checkout", "-b", "task/empty")
    run = seed_completed_run(db_path, repository_path=str(work), branch="task/empty")

    orch = CompletionOrchestrator(db_path, github=FakeGitHubClient())
    orch.begin_completion(run, project_cfg=_auto_cfg())
    orch.advance(run["id"], now=NOW)

    row = runtime_db.get_completion(db_path, run["id"])
    assert row["completion_state"] == CompletionState.REQUIRES_ATTENTION
    assert row["last_reason_code"] == "NO_TASK_COMMIT"


# ============================================================================
# Recovery cases 9-14 (service level)
# ============================================================================
def test_recovery_remote_branch_recreated_only_once(env):
    db_path, remote, work, tmp = env
    branch = "task/aicc-once"
    make_task_branch(work, branch)
    run = seed_completed_run(db_path, repository_path=str(work), branch=branch)

    gh = FakeGitHubClient()
    gh.seed(PullRequestState(number=50, state=STATE_CLOSED, head_ref=branch))
    orch = CompletionOrchestrator(db_path, github=gh)
    orch.begin_completion(run, project_cfg={"default_branch": "main", "allow_pr_recovery": True})

    orch.advance(run["id"], now=NOW)
    orch.advance(run["id"], now=NOW + timedelta(minutes=5))  # second pass: no duplicate work

    events = _events(db_path, run["id"])
    assert events.count("REMOTE_BRANCH_RECREATED") == 1
    assert events.count("REPLACEMENT_PR_CREATED") == 1
    assert len(gh.created) == 1


def test_restart_during_recovery_does_not_duplicate_pr(env):
    db_path, remote, work, tmp = env
    branch = "task/aicc-crash"
    make_task_branch(work, branch)
    run = seed_completed_run(db_path, repository_path=str(work), branch=branch)

    class CrashAfterCreate(FakeGitHubClient):
        armed = True

        def create_pull_request(self, repo, *, base, head, title, body):
            pr = super().create_pull_request(repo, base=base, head=head, title=title, body=body)
            if self.armed:
                self.armed = False
                raise KeyboardInterrupt("simulated process death after PR creation")
            return pr

    gh = CrashAfterCreate()
    gh.seed(PullRequestState(number=60, state=STATE_CLOSED, head_ref=branch))
    orch1 = CompletionOrchestrator(db_path, github=gh)
    orch1.begin_completion(run, project_cfg={"default_branch": "main", "allow_pr_recovery": True})

    # Crash (BaseException) after the replacement PR is created externally but
    # before the DB transition to PULL_REQUEST_OPEN persists.
    with pytest.raises(KeyboardInterrupt):
        orch1.advance(run["id"], now=NOW)
    row = runtime_db.get_completion(db_path, run["id"])
    assert row["completion_state"] != CompletionState.AWAITING_MERGE  # transition not yet persisted
    assert len(gh.created) == 1  # the replacement PR was created externally before the crash

    # Restart: a fresh orchestrator resumes from persisted state and must NOT
    # create a second replacement PR (it discovers the existing open one).
    orch2 = CompletionOrchestrator(db_path, github=gh)
    orch2.advance(run["id"], now=NOW + timedelta(minutes=1))

    row = runtime_db.get_completion(db_path, run["id"])
    assert row["completion_state"] == CompletionState.AWAITING_MERGE
    assert len(gh.created) == 1  # STILL exactly one replacement PR across the "restart"


def test_commit_already_in_main_despite_closed_pr_completes(env):
    db_path, remote, work, tmp = env
    branch = "task/aicc-already"
    make_task_branch(work, branch)
    # The branch's work is ALREADY merged into main on the remote.
    from tests.completion_helpers import git

    git(work, "push", "origin", branch)
    merge_into_main(remote, tmp, branch, squash=False)
    run = seed_completed_run(db_path, repository_path=str(work), branch=branch)

    gh = FakeGitHubClient()
    gh.seed(PullRequestState(number=70, state=STATE_CLOSED, head_ref=branch))  # a closed PR
    orch = CompletionOrchestrator(db_path, github=gh)
    orch.begin_completion(run, project_cfg={"default_branch": "main"})
    orch.advance(run["id"], now=NOW)

    row = runtime_db.get_completion(db_path, run["id"])
    assert row["completion_state"] == CompletionState.COMPLETED
    assert gh.created == []  # no recovery needed; already in target


# ============================================================================
# Supervisor / poller cases 15-20
# ============================================================================
def test_advance_pending_advances_states(env):
    db_path, remote, work, tmp = env
    branch = "task/aicc-pending"
    make_task_branch(work, branch)
    run = seed_completed_run(db_path, repository_path=str(work), branch=branch)

    gh = FakeGitHubClient(on_merge=lambda pr: merge_into_main(remote, tmp, pr.head_ref, squash=False))
    orch = CompletionOrchestrator(db_path, github=gh)
    orch.begin_completion(run, project_cfg=_auto_cfg())

    results = orch.advance_pending(now=NOW)
    assert any(r.run_id == run["id"] for r in results)
    row = runtime_db.get_completion(db_path, run["id"])
    assert row["completion_state"] == CompletionState.COMPLETED


def test_temporary_github_failure_schedules_retry(env):
    db_path, remote, work, tmp = env
    branch = "task/aicc-flaky"
    make_task_branch(work, branch)
    run = seed_completed_run(db_path, repository_path=str(work), branch=branch)

    class FlakyGitHub(FakeGitHubClient):
        def discover_pull_request(self, repo, *, branch, base=None):
            raise GitHubError(["pr", "list"], 1, "temporary network failure")

    gh = FlakyGitHub()
    orch = CompletionOrchestrator(db_path, github=gh)
    orch.begin_completion(run, project_cfg=_auto_cfg())

    orch.advance_pending(now=NOW)  # validation passes, then discover fails -> retry scheduled

    row = runtime_db.get_completion(db_path, run["id"])
    assert row["completion_state"] not in (
        CompletionState.REQUIRES_ATTENTION,
        CompletionState.COMPLETED,
    )
    assert row["next_retry_at"] is not None
    assert row["next_retry_at"] > NOW.isoformat(timespec="seconds")
    assert row["retry_count"] >= 1


def test_permanent_validation_failure_does_not_retry_forever(env):
    db_path, remote, work, tmp = env
    branch = "task/aicc-permfail"
    make_task_branch(work, branch, filename="broken.py", content="def (:\n")
    run = seed_completed_run(db_path, repository_path=str(work), branch=branch)

    orch = CompletionOrchestrator(db_path, github=FakeGitHubClient())
    orch.begin_completion(run, project_cfg=_auto_cfg())
    orch.advance(run["id"], now=NOW)
    validations_after_first = len(runtime_db.list_validation_results(db_path, run["id"]))

    # A terminal validation failure must never be re-selected by the poller.
    for i in range(5):
        orch.advance_pending(now=NOW + timedelta(hours=i + 1))
    row = runtime_db.get_completion(db_path, run["id"])
    assert row["completion_state"] == CompletionState.VALIDATION_FAILED
    # Validation was not re-run.
    assert len(runtime_db.list_validation_results(db_path, run["id"])) == validations_after_first


def test_advance_pending_is_bounded_and_synchronous(env):
    db_path, remote, work, tmp = env
    # No pending rows -> returns immediately with an empty list (never blocks).
    orch = CompletionOrchestrator(db_path, github=FakeGitHubClient())
    assert orch.advance_pending(now=NOW) == []


def test_restart_resumes_from_persisted_state(env):
    db_path, remote, work, tmp = env
    branch = "task/aicc-restart"
    make_task_branch(work, branch)
    run = seed_completed_run(db_path, repository_path=str(work), branch=branch)

    # First orchestrator: manual mode -> stops at AWAITING_MERGE.
    gh1 = FakeGitHubClient()
    orch1 = CompletionOrchestrator(db_path, github=gh1)
    orch1.begin_completion(run, project_cfg={"default_branch": "main"})
    orch1.advance(run["id"], now=NOW)
    assert runtime_db.get_completion(db_path, run["id"])["completion_state"] == CompletionState.AWAITING_MERGE

    # A human merges the PR out of band (real merge into main).
    from tests.completion_helpers import git

    git(work, "push", "origin", branch)
    merge_into_main(remote, tmp, branch, squash=False)

    # Restart: a brand-new orchestrator resumes from the persisted row and
    # verifies the target branch to completion.
    orch2 = CompletionOrchestrator(db_path, github=FakeGitHubClient())
    orch2.advance(run["id"], now=NOW + timedelta(minutes=10))
    assert runtime_db.get_completion(db_path, run["id"])["completion_state"] == CompletionState.COMPLETED


def test_terminal_completed_task_not_reprocessed(env):
    db_path, remote, work, tmp = env
    branch = "task/aicc-terminal"
    make_task_branch(work, branch)
    run = seed_completed_run(db_path, repository_path=str(work), branch=branch)

    gh = FakeGitHubClient(on_merge=lambda pr: merge_into_main(remote, tmp, pr.head_ref, squash=False))
    orch = CompletionOrchestrator(db_path, github=gh)
    orch.begin_completion(run, project_cfg=_auto_cfg())
    orch.advance(run["id"], now=NOW)
    assert runtime_db.get_completion(db_path, run["id"])["completion_state"] == CompletionState.COMPLETED
    events_before = len(runtime_db.list_completion_events(db_path, run["id"]))

    result = orch.advance(run["id"], now=NOW + timedelta(hours=1))
    assert result.changed is False
    assert len(runtime_db.list_completion_events(db_path, run["id"])) == events_before
    # And the poller does not select it.
    assert orch.advance_pending(now=NOW + timedelta(hours=1)) == []


def test_begin_completion_is_idempotent(env):
    db_path, remote, work, tmp = env
    branch = "task/aicc-idem"
    make_task_branch(work, branch)
    run = seed_completed_run(db_path, repository_path=str(work), branch=branch)

    orch = CompletionOrchestrator(db_path, github=FakeGitHubClient())
    first = orch.begin_completion(run, project_cfg=_auto_cfg())
    second = orch.begin_completion(run, project_cfg=_auto_cfg())
    assert first["run_id"] == second["run_id"]
    # Exactly one completion row for the run.
    assert runtime_db.get_completion_by_task(db_path, run["task_id"])["run_id"] == run["id"]


def test_begin_completion_skips_non_completed_run(env):
    db_path, remote, work, tmp = env
    branch = "task/aicc-running"
    make_task_branch(work, branch)
    run = seed_completed_run(db_path, repository_path=str(work), branch=branch, state="RUNNING")

    orch = CompletionOrchestrator(db_path, github=FakeGitHubClient())
    assert orch.begin_completion(run, project_cfg=_auto_cfg()) is None
    assert runtime_db.get_completion(db_path, run["id"]) is None


def test_step_verify_recovers_late_merge_commit_oid(env):
    # M2: a squash merge whose commit oid GitHub has not populated at merge time
    # persists merge_commit=None. HEAD is not an ancestor of the squashed base,
    # so without the oid a genuinely-merged task strands at REQUIRES_ATTENTION.
    # _step_verify must re-discover the PR, recover the oid, verify the real
    # merge, and persist the recovered oid.
    import dataclasses

    db_path, remote, work, tmp = env
    branch = "task/aicc-late-oid"
    make_task_branch(work, branch)
    run = seed_completed_run(db_path, repository_path=str(work), branch=branch)

    class LateOidGitHub(FakeGitHubClient):
        """merge() reports success but with no merge-commit oid; the stored PR
        keeps the real oid, so a later discover() reveals it."""

        def merge_pull_request(self, repo, *, number, method="squash"):
            pr = super().merge_pull_request(repo, number=number, method=method)
            return dataclasses.replace(pr, merge_commit=None)

    gh = LateOidGitHub(on_merge=lambda pr: merge_into_main(remote, tmp, pr.head_ref, squash=True))
    orch = CompletionOrchestrator(db_path, github=gh)
    orch.begin_completion(run, project_cfg=_auto_cfg())
    orch.advance(run["id"], now=NOW)

    row = runtime_db.get_completion(db_path, run["id"])
    assert row["completion_state"] == CompletionState.COMPLETED
    # the oid recovered by the re-discovery was persisted, not left null
    assert row["merge_commit"]
