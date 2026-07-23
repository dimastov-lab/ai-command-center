"""Coverage for the shared, non-mutating launch classifier
(`launch_service.prepare_task_launch`) and for the execution-queue path being
able to provision a missing-but-provisionable isolated worktree — the Founder
Gate Blocker that automatic provisioning was unreachable from the normal
product paths.

Uses real temporary git repositories and the local `fake_claude` fixture — no
paid agent is ever launched.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from command_center import execution_queue, git_info, launch, launch_service, models
from command_center import workspace_provisioning as wp
from command_center.runtime import api as runtime_api
from command_center.runtime import db as runtime_db


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


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


def _cfg(repo: Path) -> dict:
    return {"repository_path": str(repo)}


def _task(**overrides) -> dict:
    task = {"id": "t", "project": "AIOS", "title": "T", "status": "Backlog",
            "priority": "Medium", "depends_on": []}
    task.update(models.default_task_execution_fields())
    task.update(models.default_task_workflow_fields())
    task.update(overrides)
    return task


# --------------------------------------------------------------------------
# Shared classification — driven by stable codes, never message text
# --------------------------------------------------------------------------


def test_missing_workspace_with_valid_inputs_is_provisionable(tmp_path):
    repo = _make_repo(tmp_path / "repo")
    worktree = tmp_path / "wt" / "audit"  # absent
    task = _task(workspace_path=str(worktree), branch="audit/execution-queue", base_branch="main")

    prep = launch_service.prepare_task_launch(task=task, project_config=_cfg(repo))

    assert prep.decision == launch_service.LAUNCH_DECISION_PROVISIONABLE
    assert prep.provisionable is True
    assert prep.launchable is True
    # Classified via the stable code, not the localized "not found" text.
    assert prep.validation.blocked_only_by(launch.ISSUE_WORKSPACE_MISSING)
    assert launch.ISSUE_WORKSPACE_MISSING in prep.validation.error_codes
    assert prep.provision_notice  # operator-facing "will be created" notice


def test_missing_workspace_without_base_branch_is_blocked(tmp_path):
    repo = _make_repo(tmp_path / "repo")
    worktree = tmp_path / "wt" / "audit"
    # No base_branch on the task and no default_branch in the project config.
    task = _task(workspace_path=str(worktree), branch="audit/execution-queue")

    prep = launch_service.prepare_task_launch(task=task, project_config=_cfg(repo))

    assert prep.decision == launch_service.LAUNCH_DECISION_BLOCKED
    assert prep.launchable is False
    assert prep.fatal_messages  # the fatal errors are surfaced


def test_missing_workspace_with_nonexistent_base_branch_is_blocked(tmp_path):
    repo = _make_repo(tmp_path / "repo")
    worktree = tmp_path / "wt" / "audit"
    task = _task(workspace_path=str(worktree), branch="audit/execution-queue", base_branch="no-such-base")

    prep = launch_service.prepare_task_launch(task=task, project_config=_cfg(repo))

    assert prep.decision == launch_service.LAUNCH_DECISION_BLOCKED
    assert prep.launchable is False


def test_workspace_path_that_is_a_file_is_fatal_not_provisionable(tmp_path):
    """Same human-facing 'not found' message, *different* stable code — proving
    classification is code-driven, not text-driven: a path that exists but is a
    file is fatal, never treated as a provisionable absent worktree."""
    repo = _make_repo(tmp_path / "repo")
    a_file = tmp_path / "not-a-dir"
    a_file.write_text("i am a file\n")
    task = _task(workspace_path=str(a_file), branch="audit/execution-queue", base_branch="main")

    prep = launch_service.prepare_task_launch(task=task, project_config=_cfg(repo))

    assert prep.validation.error_codes == [launch.ISSUE_WORKSPACE_NOT_DIRECTORY]
    assert prep.decision == launch_service.LAUNCH_DECISION_BLOCKED


def test_existing_isolated_worktree_is_ready(tmp_path):
    repo = _make_repo(tmp_path / "repo")
    worktree = tmp_path / "wt" / "audit"
    _git(repo, "worktree", "add", "-b", "audit/execution-queue", str(worktree), "main")
    task = _task(workspace_path=str(worktree), branch="audit/execution-queue", base_branch="main")

    prep = launch_service.prepare_task_launch(task=task, project_config=_cfg(repo))

    assert prep.decision == launch_service.LAUNCH_DECISION_READY
    assert prep.launchable is True


def test_main_line_missing_workspace_is_not_auto_provisioned(tmp_path):
    """A main-line branch is not auto-provisioned into a second worktree."""
    repo = _make_repo(tmp_path / "repo")
    worktree = tmp_path / "wt" / "main"
    task = _task(workspace_path=str(worktree), branch="main", base_branch="main")

    prep = launch_service.prepare_task_launch(task=task, project_config=_cfg(repo))

    assert prep.decision == launch_service.LAUNCH_DECISION_BLOCKED


# --------------------------------------------------------------------------
# Execution queue — Founder Gate regression, path 2
# --------------------------------------------------------------------------


def _enqueue(task: dict) -> list[dict]:
    tasks_by_id = {task["id"]: task}
    return execution_queue.enqueue([], task, tasks_by_id)


def test_launch_ready_provisions_missing_workspace_and_launches(tmp_path, fake_claude):
    """EXACT Founder Gate regression via the execution queue: a Ready task
    whose isolated worktree does not exist yet is provisioned and launched in
    that worktree — never skipped as invalid, never run in the main repo."""
    repo = _make_repo(tmp_path / "repo")
    worktree = tmp_path / "wt" / "audit"  # absent
    fake_claude["FAKE_CLAUDE_TOUCH_FILE"] = "agent_ran_here.txt"

    task = _task(id="a", workspace_path=str(worktree), branch="audit/execution-queue", base_branch="main")
    tasks_by_id = {"a": task}
    entries = _enqueue(task)
    api = runtime_api.ExecutionCenterAPI(db_path=tmp_path / "runtime.db")

    updated, results = execution_queue.launch_ready(
        tmp_path, entries, [task], tasks_by_id, {"AIOS": _cfg(repo)}, api
    )

    assert results[0].launched is True, results[0].message
    assert updated[0]["state"] == execution_queue.STATE_LAUNCHED

    # The worktree was created, on the expected branch, as an isolated worktree.
    assert worktree.is_dir()
    assert git_info.get_status(worktree)["branch"] == "audit/execution-queue"

    run = api.get_run(results[0].run_id)
    assert Path(run["repository_path"]).resolve() == worktree.resolve()
    done = api.supervisor.wait_for_run(results[0].run_id, timeout=10)
    assert done["state"] == "COMPLETED"

    # The agent ran in the worktree, never in the main repository.
    assert (worktree / "agent_ran_here.txt").exists()
    assert not (repo / "agent_ran_here.txt").exists()
    assert git_info.get_status(repo)["status_lines"] == []


def test_launch_ready_provision_failure_is_requires_attention_not_launched(tmp_path):
    """When provisioning/verification fails, the queue records an explicit
    Requires-Attention reason, starts no process, and creates no run row."""
    repo = _make_repo(tmp_path / "repo")
    # `feature/x` already exists and is checked out in another worktree — a
    # conflicting-worktree provisioning failure.
    _git(repo, "branch", "feature/x")
    other = tmp_path / "other-wt"
    _git(repo, "worktree", "add", str(other), "feature/x")

    worktree = tmp_path / "wt" / "x"  # absent
    task = _task(id="a", workspace_path=str(worktree), branch="feature/x", base_branch="main")
    tasks_by_id = {"a": task}
    entries = _enqueue(task)
    api = runtime_api.ExecutionCenterAPI(db_path=tmp_path / "runtime.db")

    # Preconditions: classified as provisionable (so it is *attempted*, not skipped).
    prep = launch_service.prepare_task_launch(task=task, project_config=_cfg(repo))
    assert prep.decision == launch_service.LAUNCH_DECISION_PROVISIONABLE

    updated, results = execution_queue.launch_ready(
        tmp_path, entries, [task], tasks_by_id, {"AIOS": _cfg(repo)}, api
    )

    assert results[0].launched is False
    assert "изоляции" in results[0].message  # explicit, structured reason
    assert results[0].validation_report is not None
    assert results[0].validation_report["failed_step"] == "no_conflicting_worktree"
    assert updated[0]["state"] == execution_queue.STATE_READY  # left in place, not launched
    assert not worktree.exists()  # nothing provisioned
    assert runtime_db.list_runs(api.db_path) == []  # no run row


def test_launch_ready_negative_regression_primary_checkout_is_blocked(tmp_path):
    """Negative regression: a task pointed at the primary checkout on `main`
    but expecting `audit/execution-queue` is never launched (and never
    provisions or mutates anything)."""
    repo = _make_repo(tmp_path / "repo")  # primary checkout, on main
    task = _task(id="a", workspace_path=str(repo), branch="audit/execution-queue", base_branch="main")
    tasks_by_id = {"a": task}
    entries = _enqueue(task)
    api = runtime_api.ExecutionCenterAPI(db_path=tmp_path / "runtime.db")

    updated, results = execution_queue.launch_ready(
        tmp_path, entries, [task], tasks_by_id, {"AIOS": _cfg(repo)}, api
    )

    assert results[0].launched is False
    assert updated[0]["state"] == execution_queue.STATE_READY
    assert runtime_db.list_runs(api.db_path) == []  # zero side effects
    assert git_info.get_status(repo)["branch"] == "main"
    assert git_info.get_status(repo)["status_lines"] == []


def test_v2_direct_service_call_still_fails_closed_on_primary_checkout(tmp_path):
    """Defense in depth: even bypassing the queue classification, the service
    layer refuses the observed case (primary checkout / main / audit branch)."""
    repo = _make_repo(tmp_path / "repo")
    api = runtime_api.ExecutionCenterAPI(db_path=tmp_path / "runtime.db")

    import pytest

    with pytest.raises(wp.WorkspaceVerificationError) as exc:
        launch_service.execute_agent_launch_v2(
            project="AIOS", task_type="implementation", prompt="do it", timeout_seconds=30,
            repository_path=repo, execution_center_api=api, confirmed=True,
            expected_branch="audit/execution-queue", base_branch="main",
            source_repository_path=str(repo),
        )
    assert exc.value.failed_step == "branch_matches"
    assert runtime_db.list_runs(api.db_path) == []
