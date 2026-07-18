import ast
import inspect
import json
import subprocess

from command_center import agent_runner, launch, launch_service, models


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


def _mock_claude(monkeypatch, *, exit_code: int = 0, result_text: str = "Verdict: APPROVED FOR COMMIT"):
    real_run = subprocess.run

    def fake_run(command, **kwargs):
        if command and command[0] == "claude":
            payload = json.dumps([{"type": "result", "result": result_text}])
            return subprocess.CompletedProcess(command, exit_code, stdout=payload, stderr="")
        return real_run(command, **kwargs)

    monkeypatch.setattr(agent_runner.subprocess, "run", fake_run)


def test_execute_agent_launch_completed_updates_task_and_returns_outcome(monkeypatch, git_repo):
    _mock_claude(monkeypatch)

    task = {"id": "t1"}
    models.normalize_task_execution(task)
    validation = launch.validate_launch(workspace_path=str(git_repo))

    outcome = launch_service.execute_agent_launch(
        project="AIOS",
        task_type="implementation",
        prompt="do the thing",
        timeout_seconds=30,
        repository_path=git_repo,
        task=task,
        validation=validation,
    )

    assert outcome.result_status == "completed"
    assert outcome.parsed["verdict"] == "APPROVED_FOR_COMMIT"
    assert task["latest_verdict"] == "APPROVED_FOR_COMMIT"
    assert task["launch_status"] == "Completed"
    assert task["executor"] == "claude_code"
    assert task["agent"] == "claude_code"
    assert task["finished_at"]
    assert task["started_at"]
    assert any(event["type"] == "tests_passed" for event in task["timeline"])


def test_execute_agent_launch_failure_sets_task_failed(monkeypatch, git_repo):
    _mock_claude(monkeypatch, exit_code=1, result_text="")

    task = {"id": "t2"}
    models.normalize_task_execution(task)
    validation = launch.validate_launch(workspace_path=str(git_repo))

    outcome = launch_service.execute_agent_launch(
        project="AIOS",
        task_type="implementation",
        prompt="x",
        timeout_seconds=30,
        repository_path=git_repo,
        task=task,
        validation=validation,
    )

    assert outcome.result_status == "failed"
    assert task["launch_status"] == "Failed"
    assert task["workflow_stage"] == "Ready"


def test_execute_agent_launch_without_task_still_returns_outcome(monkeypatch, git_repo):
    _mock_claude(monkeypatch, result_text="done")

    outcome = launch_service.execute_agent_launch(
        project="AIOS", task_type="review", prompt="x", timeout_seconds=30, repository_path=git_repo,
    )
    assert outcome.result_status == "completed"
    assert outcome.run["task_id"] is None


def test_execute_agent_launch_records_pull_request_url_and_stage(monkeypatch, git_repo):
    _mock_claude(monkeypatch, result_text="PR: https://github.com/example/repo/pull/7")

    task = {"id": "t3"}
    models.normalize_task_execution(task)
    validation = launch.validate_launch(workspace_path=str(git_repo))

    outcome = launch_service.execute_agent_launch(
        project="AIOS", task_type="implementation", prompt="x", timeout_seconds=30,
        repository_path=git_repo, task=task, validation=validation,
    )
    if outcome.parsed.get("pull_request_url"):
        assert task["pull_request_url"] == outcome.parsed["pull_request_url"]
        assert task["current_stage"] == "PR Ready"


def test_on_task_state_changed_fires_before_the_blocking_executor_call(monkeypatch, git_repo):
    calls: list[str] = []
    _mock_claude(monkeypatch, result_text="done")

    original_launch = agent_runner.run_claude_code

    def tracking_run_claude_code(*args, **kwargs):
        calls.append("blocking_call")
        return original_launch(*args, **kwargs)

    monkeypatch.setattr(agent_runner, "run_claude_code", tracking_run_claude_code)

    task = {"id": "t4"}
    models.normalize_task_execution(task)
    validation = launch.validate_launch(workspace_path=str(git_repo))

    launch_service.execute_agent_launch(
        project="AIOS", task_type="implementation", prompt="x", timeout_seconds=30,
        repository_path=git_repo, task=task, validation=validation,
        on_task_state_changed=lambda: calls.append("state_changed"),
    )

    assert calls == ["state_changed", "state_changed", "blocking_call"]


def test_execute_agent_launch_without_on_task_state_changed_does_not_raise(monkeypatch, git_repo):
    _mock_claude(monkeypatch, result_text="done")
    task = {"id": "t5"}
    models.normalize_task_execution(task)
    outcome = launch_service.execute_agent_launch(
        project="AIOS", task_type="implementation", prompt="x", timeout_seconds=30,
        repository_path=git_repo, task=task,
    )
    assert outcome.result_status == "completed"
