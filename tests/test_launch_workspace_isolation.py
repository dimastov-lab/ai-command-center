"""End-to-end regression coverage that the workspace-isolation gate is
enforced at the real launch chokepoints — `Supervisor.start_raw` and
`launch_service.execute_agent_launch_v2` — not just in the pure
`workspace_provisioning` unit layer.

Reproduces the exact production failure: a task whose expected branch was
`audit/execution-queue` must never launch Claude in the main repository on
`main`, must never reach "Workspace Verified", and must leave no files behind.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from command_center import git_info, launch, launch_service, models
from command_center import workspace_provisioning as wp
from command_center.runtime import api as runtime_api
from command_center.runtime import context_service, db as runtime_db
from command_center.runtime import supervisor as supervisor_module


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def _make_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@test.com")
    _git(path, "config", "user.name", "test")
    (path / "f.txt").write_text("hello\n")
    _git(path, "add", "f.txt")
    _git(path, "commit", "-q", "-m", "init")
    _git(path, "branch", "-M", "main")
    return path


def _add_worktree(repo: Path, path: Path, branch: str, base: str = "main") -> Path:
    _git(repo, "worktree", "add", "-b", branch, str(path), base)
    return path


# --------------------------------------------------------------------------
# Supervisor.start_raw — the fail-closed chokepoint
# --------------------------------------------------------------------------


def test_start_raw_fails_closed_on_branch_mismatch_and_starts_no_process(tmp_path):
    """The observed case at the lowest chokepoint: expected
    `audit/execution-queue`, actual `main`, in the main repo."""
    repo = _make_repo(tmp_path / "repo")
    sup = supervisor_module.Supervisor(db_path=tmp_path / "runs.db")

    spec = wp.WorkspaceSpec(
        workspace_path=str(repo),
        expected_branch="audit/execution-queue",
        base_branch="main",
        repository_path=str(repo),
        allow_provision=False,
    )
    with pytest.raises(supervisor_module.WorkspaceVerificationFailed) as exc_info:
        sup.start_raw(
            project="AIOS",
            repository_path=str(repo),
            task_type="implementation",
            prompt="do the audit",
            confirmed=True,
            repository_already_validated=True,
            expected_branch="audit/execution-queue",
            workspace_verification=spec,
        )

    err = exc_info.value
    assert isinstance(err, supervisor_module.SupervisorError)  # existing handlers catch it
    assert err.failed_step == "branch_matches"
    assert err.structured["expected_branch"] == "audit/execution-queue"
    assert err.structured["actual_branch"] == "main"

    # No run row was ever created (raised before create_run), so nothing is
    # left dangling in an active state.
    assert runtime_db.list_runs(sup.db_path) == []
    # The main repository was not touched — no stray files.
    assert git_info.get_status(repo)["status_lines"] == []


def test_start_raw_rejects_verification_for_a_different_launch_directory(tmp_path):
    verified_repo = _make_repo(tmp_path / "verified")
    actual_repo = _make_repo(tmp_path / "actual")
    sup = supervisor_module.Supervisor(db_path=tmp_path / "runs.db")
    spec = wp.WorkspaceSpec(
        workspace_path=str(verified_repo),
        expected_branch="main",
        base_branch="main",
        repository_path=str(verified_repo),
    )

    with pytest.raises(supervisor_module.WorkspaceVerificationFailed) as exc_info:
        sup.start_raw(
            project="AIOS",
            repository_path=str(actual_repo),
            task_type="implementation",
            prompt="must not run",
            confirmed=True,
            repository_already_validated=True,
            workspace_verification=spec,
        )

    assert exc_info.value.failed_step == "workspace_matches_launch_path"
    assert runtime_db.list_runs(sup.db_path) == []


def test_start_raw_verified_worktree_runs_claude_in_that_worktree(
    tmp_path, configure_project_repo, fake_claude
):
    repo = _make_repo(tmp_path / "repo")
    worktree = _add_worktree(repo, tmp_path / "wt" / "audit", "audit/execution-queue")
    configure_project_repo("AIOS", repo)
    # Prove the cwd handed to Claude by having the fake CLI write into it.
    fake_claude["FAKE_CLAUDE_TOUCH_FILE"] = "agent_ran_here.txt"

    sup = supervisor_module.Supervisor(db_path=tmp_path / "runs.db")
    spec = wp.WorkspaceSpec(
        workspace_path=str(worktree),
        expected_branch="audit/execution-queue",
        base_branch="main",
        repository_path=str(repo),
    )
    run = sup.start_raw(
        project="AIOS",
        repository_path=str(worktree),
        task_type="implementation",
        prompt="do the audit",
        confirmed=True,
        repository_already_validated=True,
        expected_branch="audit/execution-queue",
        workspace_verification=spec,
    )
    assert run["state"] == "RUNNING"
    # Claude's cwd is the verified worktree (its resolved path).
    assert Path(run["repository_path"]).resolve() == worktree.resolve()

    final = sup.wait_for_run(run["id"], timeout=10)
    assert final["state"] == "COMPLETED"

    # "Workspace Verified" is emitted only as a real, evidence-backed event.
    events = runtime_db.list_run_events(sup.db_path, run["id"])
    verified = [e for e in events if e["payload"].get("lifecycle") == "workspace_verified"]
    assert len(verified) == 1
    assert verified[0]["payload"]["expected_branch"] == "audit/execution-queue"
    assert verified[0]["payload"]["actual_branch"] == "audit/execution-queue"
    assert verified[0]["payload"]["is_isolated_worktree"] is True

    # fake_claude writes into its cwd — the file landed in the worktree,
    # never in the main repository.
    assert (worktree / "agent_ran_here.txt").exists()
    assert not (repo / "agent_ran_here.txt").exists()
    assert git_info.get_status(repo)["status_lines"] == []


def test_verified_run_is_guarded_before_workspace_event_is_persisted(
    tmp_path, configure_project_repo, fake_claude, monkeypatch
):
    """A concurrent reconcile must not interrupt the PREPARED run while its
    verification evidence is being persisted."""
    repo = _make_repo(tmp_path / "repo")
    worktree = _add_worktree(repo, tmp_path / "wt" / "audit", "audit/execution-queue")
    configure_project_repo("AIOS", repo)
    sup = supervisor_module.Supervisor(db_path=tmp_path / "runs.db")
    spec = wp.WorkspaceSpec(
        workspace_path=str(worktree),
        expected_branch="audit/execution-queue",
        base_branch="main",
        repository_path=str(repo),
    )

    original_append = supervisor_module.db.append_run_event
    observed: dict[str, object] = {}

    def append_with_reconcile(db_path, run_id, event_type, payload):
        if payload.get("lifecycle") == "workspace_verified":
            observed["guarded"] = run_id in sup._launching
            observed["reconciled"] = sup.reconcile()
            observed["state"] = runtime_db.get_run(sup.db_path, run_id)["state"]
        return original_append(db_path, run_id, event_type, payload)

    monkeypatch.setattr(supervisor_module.db, "append_run_event", append_with_reconcile)

    run = sup.start_raw(
        project="AIOS",
        repository_path=str(worktree),
        task_type="implementation",
        prompt="do the audit",
        confirmed=True,
        repository_already_validated=True,
        expected_branch="audit/execution-queue",
        workspace_verification=spec,
    )

    assert observed == {"guarded": True, "reconciled": [], "state": "PREPARED"}
    assert run["state"] == "RUNNING"
    sup.wait_for_run(run["id"], timeout=10)


# --------------------------------------------------------------------------
# launch_service.execute_agent_launch_v2 — orchestration layer
# --------------------------------------------------------------------------


def test_v2_observed_case_blocks_and_never_reaches_workspace_verified(
    tmp_path, configure_project_repo
):
    repo = _make_repo(tmp_path / "repo")
    configure_project_repo("AIOS", repo)
    api = runtime_api.ExecutionCenterAPI(db_path=tmp_path / "runs.db")

    task = {"id": "T-AUDIT", "project": "AIOS", "branch": "audit/execution-queue"}
    models.normalize_task_execution(task)
    assert task["current_stage"] == "Created"
    validation = launch.validate_launch(
        workspace_path=str(repo), expected_branch="audit/execution-queue"
    )

    with pytest.raises(wp.WorkspaceVerificationError) as exc_info:
        launch_service.execute_agent_launch_v2(
            project="AIOS",
            task_type="implementation",
            prompt="do the audit",
            timeout_seconds=30,
            repository_path=repo,  # the main-repo fallback — the defect
            execution_center_api=api,
            confirmed=True,
            task=task,
            validation=validation,
            expected_branch="audit/execution-queue",
            base_branch="main",
            source_repository_path=str(repo),
        )

    assert exc_info.value.failed_step == "branch_matches"
    # The task was never advanced to "Workspace Verified".
    assert task["current_stage"] == "Created"
    assert task.get("current_run_id") is None
    # No run was started; the main repo is untouched.
    assert runtime_db.list_runs(api.db_path) == []
    assert git_info.get_status(repo)["status_lines"] == []


def test_v2_auto_provisions_missing_worktree_and_launches_there(
    tmp_path, configure_project_repo, fake_claude
):
    repo = _make_repo(tmp_path / "repo")
    configure_project_repo("AIOS", repo)
    api = runtime_api.ExecutionCenterAPI(db_path=tmp_path / "runs.db")

    worktree = tmp_path / "wt" / "audit"  # does not exist yet
    task = {
        "id": "T-AUDIT",
        "project": "AIOS",
        "branch": "audit/execution-queue",
        "workspace_path": str(worktree),
    }
    models.normalize_task_execution(task)

    run = launch_service.execute_agent_launch_v2(
        project="AIOS",
        task_type="implementation",
        prompt="do the audit",
        timeout_seconds=30,
        repository_path=worktree,
        execution_center_api=api,
        confirmed=True,
        task=task,
        validation=None,  # provisioning creates the worktree
        expected_branch="audit/execution-queue",
        base_branch="main",
        source_repository_path=str(repo),
    )

    assert worktree.is_dir()
    assert git_info.get_status(worktree)["branch"] == "audit/execution-queue"
    final = api.get_run(run["id"])
    assert Path(final["repository_path"]).resolve() == worktree.resolve()

    done = api.supervisor.wait_for_run(run["id"], timeout=10)
    assert done["state"] == "COMPLETED"
    events = runtime_db.list_run_events(api.db_path, run["id"])
    verified = [e for e in events if e["payload"].get("lifecycle") == "workspace_verified"]
    assert verified[0]["payload"]["provision_outcome"] == "created"


def test_unconfirmed_launch_does_not_provision_branch_or_worktree(tmp_path):
    repo = _make_repo(tmp_path / "repo")
    worktree = tmp_path / "wt" / "audit"
    api = runtime_api.ExecutionCenterAPI(db_path=tmp_path / "runs.db")
    task = {"id": "T-AUDIT", "project": "AIOS", "branch": "audit/execution-queue"}
    models.normalize_task_execution(task)

    with pytest.raises(context_service.ConfirmationRequiredError):
        launch_service.execute_agent_launch_v2(
            project="AIOS",
            task_type="implementation",
            prompt="do not mutate",
            timeout_seconds=30,
            repository_path=worktree,
            execution_center_api=api,
            confirmed=False,
            task=task,
            expected_branch="audit/execution-queue",
            base_branch="main",
            source_repository_path=str(repo),
        )

    assert not worktree.exists()
    assert "audit/execution-queue" not in git_info.get_branches(repo)
    assert runtime_db.list_runs(api.db_path) == []
