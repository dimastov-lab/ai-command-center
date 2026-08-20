import ast
import inspect
import json
import subprocess

import pytest

from command_center import launch, launch_service, models, workspace_provisioning
from command_center.runtime import api as runtime_api
from command_center.runtime import db as runtime_db
from tests.finalization_helpers import wait_for_finalized_run


def test_launch_service_module_never_constructs_a_git_subprocess_call():
    """Same Automation Safety invariant as `launch.py`/`executors.py`: the
    orchestration layer must never itself invoke `git` as a subprocess."""
    source = inspect.getsource(launch_service)
    tree = ast.parse(source)
    string_literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "git" not in string_literals


def test_execute_agent_launch_v2_returns_immediately(git_repo, fake_claude):
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "5"
    task = {"id": "t-v2-1"}
    models.normalize_task_execution(task)
    validation = launch.validate_launch(workspace_path=str(git_repo))
    api = runtime_api.ExecutionCenterAPI(db_path=git_repo.parent / "runtime.db")

    import time

    started = time.monotonic()
    run = launch_service.execute_agent_launch_v2(
        project="AIOS", task_type="implementation", prompt="do the thing", timeout_seconds=30,
        repository_path=git_repo, task=task, validation=validation, confirmed=True,
        execution_center_api=api, source_repository_path=str(git_repo),
    )
    elapsed = time.monotonic() - started

    assert elapsed < 3.0, f"execute_agent_launch_v2 appears to have blocked for {elapsed:.2f}s"
    assert run["state"] in ("RUNNING", "QUEUED")
    assert task["current_run_id"] == run["id"]
    assert task["launch_status"] in ("Running", "Launching")

    if run["state"] == "RUNNING":
        api.request_cancel(run["id"], confirmed=True)
    wait_for_finalized_run(api.db_path, run["id"])


def test_execute_agent_launch_v2_persists_task_metadata_and_expected_branch(git_repo, fake_claude):
    fake_claude["FAKE_CLAUDE_LINES"] = json.dumps(
        [json.dumps({"type": "result", "result": "Verdict: APPROVED FOR COMMIT"})]
    )
    # The task runs in its own isolated worktree of `git_repo` on `feature/x`
    # (the workspace-isolation gate now refuses to launch a `feature/x` task in
    # a repo checked out on a different branch — that was the production bug).
    worktree = git_repo.parent / "wt-feature-x"
    subprocess.run(
        ["git", "worktree", "add", "-b", "feature/x", str(worktree), "HEAD"],
        cwd=git_repo, check=True, capture_output=True, text=True,
    )
    task = {"id": "t-v2-2", "branch": "feature/x", "prompt_version": 4}
    models.normalize_task_execution(task)
    task["branch"] = "feature/x"
    task["prompt_version"] = 4
    validation = launch.validate_launch(workspace_path=str(worktree), expected_branch="feature/x")
    api = runtime_api.ExecutionCenterAPI(db_path=git_repo.parent / "runtime.db")

    run = launch_service.execute_agent_launch_v2(
        project="AIOS", task_type="implementation", prompt="do the thing", timeout_seconds=30,
        repository_path=worktree, task=task, validation=validation, confirmed=True,
        execution_center_api=api, expected_branch="feature/x", source_repository_path=str(git_repo),
    )
    final = wait_for_finalized_run(api.db_path, run["id"])

    assert final["state"] == "COMPLETED"
    assert final["task_id"] == "t-v2-2"
    assert final["expected_branch"] == "feature/x"
    assert final["launch_source"] == "kanban_task"
    assert final["prompt_version"] == 4
    assert final["repository_path"] == str(worktree.resolve())


def test_execute_agent_launch_v2_without_task_is_execution_center_adhoc(git_repo, fake_claude, configure_project_repo):
    """`validation=None` (no task, no prior `launch.validate_launch` call) is
    the one case where `repository_already_validated` is *not* set — this
    exercises the untouched, stricter `agent_runner.validate_repository`
    path, so `repository_path` must match the project's configured path."""
    fake_claude["FAKE_CLAUDE_LINES"] = json.dumps(
        [json.dumps({"type": "result", "result": "done"})]
    )
    configure_project_repo("AIOS", git_repo)
    api = runtime_api.ExecutionCenterAPI(db_path=git_repo.parent / "runtime.db")

    run = launch_service.execute_agent_launch_v2(
        project="AIOS", task_type="implementation", prompt="do the thing", timeout_seconds=30,
        repository_path=git_repo, task=None, validation=None, confirmed=True,
        execution_center_api=api,
    )
    final = wait_for_finalized_run(api.db_path, run["id"])

    assert final["launch_source"] == "execution_center_adhoc"
    assert final["prompt_version"] is None


def test_execute_agent_launch_v2_rejects_task_workspace_without_source_repository(
    tmp_path,
):
    """A task workspace cannot authorize itself when repository ownership is
    unknown, even if it is otherwise a valid Git repository."""
    task_workspace = tmp_path / "task-workspace"
    task_workspace.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=task_workspace, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=task_workspace, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=task_workspace, check=True)
    (task_workspace / "f.txt").write_text("hi")
    subprocess.run(["git", "add", "f.txt"], cwd=task_workspace, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=task_workspace, check=True)

    task = {"id": "t-v2-3", "workspace_path": str(task_workspace)}
    models.normalize_task_execution(task)
    task["workspace_path"] = str(task_workspace)
    validation = launch.validate_launch(workspace_path=str(task_workspace))
    api = runtime_api.ExecutionCenterAPI(db_path=tmp_path / "runtime.db")

    with pytest.raises(workspace_provisioning.WorkspaceVerificationError) as exc_info:
        launch_service.execute_agent_launch_v2(
            project="AIOS", task_type="implementation", prompt="p", timeout_seconds=30,
            repository_path=task_workspace, task=task, validation=validation, confirmed=True,
            execution_center_api=api,
        )
    assert exc_info.value.failed_step == "source_repository_required"
    assert runtime_db.list_runs(api.db_path) == []


# --------------------------------------------------------------------------
# Duplicate active-run prevention — two agents must never mutate the same
# task/workspace concurrently.
# --------------------------------------------------------------------------


def test_execute_agent_launch_v2_refuses_second_launch_against_same_task(git_repo, fake_claude):
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "5"
    task = {"id": "dup-task-1"}
    models.normalize_task_execution(task)
    validation = launch.validate_launch(workspace_path=str(git_repo))
    api = runtime_api.ExecutionCenterAPI(db_path=git_repo.parent / "runtime.db")

    first_run = launch_service.execute_agent_launch_v2(
        project="AIOS", task_type="implementation", prompt="first", timeout_seconds=30,
        repository_path=git_repo, task=task, validation=validation, confirmed=True,
        execution_center_api=api, source_repository_path=str(git_repo),
    )
    assert first_run["state"] == "RUNNING"
    prompt_history_before = list(task.get("prompt_history") or [])

    try:
        launch_service.execute_agent_launch_v2(
            project="AIOS", task_type="implementation", prompt="second", timeout_seconds=30,
            repository_path=git_repo, task=task, validation=validation, confirmed=True,
            execution_center_api=api, source_repository_path=str(git_repo),
        )
        raise AssertionError("expected DuplicateActiveLaunchError")
    except launch_service.DuplicateActiveLaunchError as exc:
        assert first_run["id"] in str(exc)

    # The refused second launch must not have mutated the task at all.
    assert task["prompt_history"] == prompt_history_before
    assert task["current_run_id"] == first_run["id"]
    assert len(runtime_db.list_runs(api.db_path, task_id="dup-task-1")) == 1

    api.request_cancel(first_run["id"], confirmed=True)
    wait_for_finalized_run(api.db_path, first_run["id"])


def test_execute_agent_launch_v2_refuses_second_launch_against_same_workspace_different_task(
    git_repo, fake_claude
):
    """Two *different* tasks pointed at the same resolved workspace must not
    be allowed to launch concurrently either — not just the same task id."""
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "5"
    task_a = {"id": "dup-task-a"}
    task_b = {"id": "dup-task-b"}
    models.normalize_task_execution(task_a)
    models.normalize_task_execution(task_b)
    validation = launch.validate_launch(workspace_path=str(git_repo))
    api = runtime_api.ExecutionCenterAPI(db_path=git_repo.parent / "runtime.db")

    first_run = launch_service.execute_agent_launch_v2(
        project="AIOS", task_type="implementation", prompt="first", timeout_seconds=30,
        repository_path=git_repo, task=task_a, validation=validation, confirmed=True,
        execution_center_api=api, source_repository_path=str(git_repo),
    )

    try:
        launch_service.execute_agent_launch_v2(
            project="AIOS", task_type="implementation", prompt="second", timeout_seconds=30,
            repository_path=git_repo, task=task_b, validation=validation, confirmed=True,
            execution_center_api=api, source_repository_path=str(git_repo),
        )
        raise AssertionError("expected DuplicateActiveLaunchError")
    except launch_service.DuplicateActiveLaunchError:
        pass

    assert task_b.get("current_run_id") is None

    api.request_cancel(first_run["id"], confirmed=True)
    wait_for_finalized_run(api.db_path, first_run["id"])


def test_execute_agent_launch_v2_allows_relaunch_once_prior_run_is_terminal(git_repo, fake_claude):
    fake_claude["FAKE_CLAUDE_LINES"] = json.dumps([json.dumps({"type": "result", "result": "done"})])
    task = {"id": "dup-task-2"}
    models.normalize_task_execution(task)
    validation = launch.validate_launch(workspace_path=str(git_repo))
    api = runtime_api.ExecutionCenterAPI(db_path=git_repo.parent / "runtime.db")

    first_run = launch_service.execute_agent_launch_v2(
        project="AIOS", task_type="implementation", prompt="first", timeout_seconds=30,
        repository_path=git_repo, task=task, validation=validation, confirmed=True,
        execution_center_api=api, source_repository_path=str(git_repo),
    )
    wait_for_finalized_run(api.db_path, first_run["id"])

    second_run = launch_service.execute_agent_launch_v2(
        project="AIOS", task_type="implementation", prompt="second", timeout_seconds=30,
        repository_path=git_repo, task=task, validation=validation, confirmed=True,
        execution_center_api=api, source_repository_path=str(git_repo),
    )
    assert second_run["id"] != first_run["id"]
    wait_for_finalized_run(api.db_path, second_run["id"])


def test_find_active_run_conflict_none_when_nothing_active(git_repo):
    api = runtime_api.ExecutionCenterAPI(db_path=git_repo.parent / "runtime.db")
    conflict = launch_service.find_active_run_conflict(
        api, task_id="whatever", resolved_workspace=str(git_repo.resolve())
    )
    assert conflict is None
