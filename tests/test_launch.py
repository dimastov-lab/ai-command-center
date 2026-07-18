import ast
import inspect
import subprocess

from command_center import launch, models


def test_launch_module_never_constructs_a_git_subprocess_call():
    """Hard invariant from the Automation Safety requirements: the Launch
    System only reads git state via `command_center.git_info` (a separate,
    independently-reviewed read-only module) and opens a terminal/Finder/
    clipboard — it must never itself invoke `git` as a subprocess, so it
    can never merge, push, switch/delete a branch, rebase, stash, reset, or
    clean. Checked via AST so prose in docstrings/comments (which legitimately
    discusses these git operations) can't produce a false positive."""
    source = inspect.getsource(launch)
    tree = ast.parse(source)
    string_literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "git" not in string_literals, "launch.py must never construct a `git` subprocess argv itself"


def test_validate_launch_errors_when_workspace_path_missing():
    result = launch.validate_launch(workspace_path=None)
    assert result.errors
    assert not result.can_launch


def test_validate_launch_errors_when_workspace_does_not_exist(tmp_path):
    missing = tmp_path / "does-not-exist"
    result = launch.validate_launch(workspace_path=str(missing))
    assert result.errors
    assert not result.can_launch


def test_validate_launch_errors_when_not_a_git_repo(tmp_path):
    result = launch.validate_launch(workspace_path=str(tmp_path))
    assert result.errors
    assert "не является git-репозиторием" in result.errors[0]


def test_validate_launch_warns_on_dirty_tree(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "untracked.txt").write_text("x")
    result = launch.validate_launch(workspace_path=str(tmp_path))
    assert result.can_launch  # dirty tree is a warning, not a blocking error
    assert result.needs_confirmation
    assert any("не чистое" in warning for warning in result.warnings)


def test_validate_launch_warns_on_branch_mismatch(tmp_path):
    _init_repo(tmp_path)
    result = launch.validate_launch(workspace_path=str(tmp_path), expected_branch="some-other-branch")
    assert result.can_launch
    assert any("отличается от ожидаемой" in warning for warning in result.warnings)


def test_validate_launch_clean_repo_no_warnings(tmp_path):
    _init_repo(tmp_path)
    result = launch.validate_launch(workspace_path=str(tmp_path))
    assert result.can_launch
    assert not result.needs_confirmation


def test_open_terminal_at_missing_path_fails_cleanly(tmp_path):
    ok, message = launch.open_terminal_at(tmp_path / "nope")
    assert ok is False
    assert message


def test_open_folder_at_missing_path_fails_cleanly(tmp_path):
    ok, message = launch.open_folder_at(tmp_path / "nope")
    assert ok is False
    assert message


def test_begin_launch_records_history_and_sets_status_requires_attention_on_warning():
    task = {}
    models.normalize_task_execution(task)
    validation = launch.LaunchValidation(warnings=["dirty tree"], git_status={"branch": "main"})
    status = launch.begin_launch(task, executor_id="claude_code", validation=validation)
    assert status == "Requires Attention"
    assert task["launch_status"] == "Requires Attention"
    assert task["launch_history"][-1]["warnings"] == ["dirty tree"]
    assert task["started_at"]
    assert task["current_stage"] == "Workspace Verified"


def test_begin_launch_sets_launching_without_warnings():
    task = {}
    models.normalize_task_execution(task)
    validation = launch.LaunchValidation()
    status = launch.begin_launch(task, executor_id="claude_code", validation=validation)
    assert status == "Launching"


def test_complete_launch_sets_finished_at_and_status():
    task = {}
    models.normalize_task_execution(task)
    status = launch.complete_launch(task, executor_id="claude_code", succeeded=True)
    assert status == "Completed"
    assert task["finished_at"]
    assert task["launch_history"][-1]["status"] == "Completed"


def test_complete_launch_failure_sets_failed_status():
    task = {}
    models.normalize_task_execution(task)
    status = launch.complete_launch(task, executor_id="claude_code", succeeded=False)
    assert status == "Failed"


def _init_repo(path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "README.md").write_text("hello")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)
