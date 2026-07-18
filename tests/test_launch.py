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


# --------------------------------------------------------------------------
# resolve_workspace_path — task.workspace_path / project.default_workspace_path
# / project.repository_path precedence
# --------------------------------------------------------------------------


def test_resolve_workspace_path_prefers_task_workspace_over_project_repository():
    task = {"workspace_path": "/task/workspace"}
    project_config = {"repository_path": "/project/repo", "default_workspace_path": "/project/default"}
    selection = launch.resolve_workspace_path(task=task, project_config=project_config)
    assert selection.path == "/task/workspace"
    assert selection.source == launch.WORKSPACE_SOURCE_TASK


def test_resolve_workspace_path_missing_task_workspace_falls_back_to_project_default():
    task = {"workspace_path": None}
    project_config = {"repository_path": "/project/repo", "default_workspace_path": "/project/default"}
    selection = launch.resolve_workspace_path(task=task, project_config=project_config)
    assert selection.path == "/project/default"
    assert selection.source == launch.WORKSPACE_SOURCE_PROJECT_DEFAULT


def test_resolve_workspace_path_missing_task_and_project_default_falls_back_to_repository_path():
    """Backward compatibility: a legacy task with no `workspace_path` and a
    project with no `default_workspace_path` override must resolve to
    exactly the project's `repository_path` — the same value every
    pre-existing task already launched against — with no data migration."""
    task = {}
    selection = launch.resolve_workspace_path(task=task, project_config={"repository_path": "/legacy/repo"})
    assert selection.path == "/legacy/repo"
    assert selection.source == launch.WORKSPACE_SOURCE_REPOSITORY_FALLBACK

    # No task at all (e.g. a project-only launch with no associated task)
    # resolves the same way.
    selection = launch.resolve_workspace_path(task=None, project_config={"repository_path": "/legacy/repo"})
    assert selection.path == "/legacy/repo"
    assert selection.source == launch.WORKSPACE_SOURCE_REPOSITORY_FALLBACK


def test_resolve_workspace_path_keeps_invalid_task_workspace_and_does_not_fall_back(tmp_path):
    """An explicit-but-broken task.workspace_path must win the precedence
    order (and therefore block via `validate_launch`) rather than being
    silently swapped out for a lower-precedence path that happens to work."""
    invalid = tmp_path / "does-not-exist"
    task = {"workspace_path": str(invalid)}
    project_config = {"repository_path": str(tmp_path)}  # a valid, existing directory
    selection = launch.resolve_workspace_path(task=task, project_config=project_config)
    assert selection.path == str(invalid)
    assert selection.source == launch.WORKSPACE_SOURCE_TASK

    validation = launch.validate_launch(workspace_path=selection.path)
    assert not validation.can_launch
    assert any("не найден" in error for error in validation.errors)


def test_resolve_workspace_path_valid_worktree_on_expected_branch_passes_validation(tmp_path):
    repo = tmp_path / "aios-p1-deployment"
    repo.mkdir()
    _init_repo(repo)
    subprocess.run(["git", "checkout", "-q", "-b", "feature/p1-7-deployment"], cwd=repo, check=True)

    task = {"workspace_path": str(repo), "branch": "feature/p1-7-deployment"}
    # The project's own repository_path deliberately points elsewhere — the
    # task's own worktree must still be what gets selected and validated.
    selection = launch.resolve_workspace_path(
        task=task, project_config={"repository_path": str(tmp_path / "aios")}
    )
    assert selection.path == str(repo)
    assert selection.source == launch.WORKSPACE_SOURCE_TASK

    validation = launch.validate_launch(workspace_path=selection.path, expected_branch=task["branch"])
    assert validation.can_launch
    assert not validation.needs_confirmation
    assert validation.git_status["branch"] == "feature/p1-7-deployment"


# --------------------------------------------------------------------------
# Persisted launch metadata records the actual resolved workspace path
# --------------------------------------------------------------------------


def test_begin_launch_records_the_resolved_workspace_path_and_branch():
    task = {}
    models.normalize_task_execution(task)
    validation = launch.LaunchValidation(
        git_status={"branch": "feature/p1-7-deployment"}, workspace_path="/resolved/workspace"
    )
    launch.begin_launch(task, executor_id="claude_code", validation=validation)
    assert task["launch_history"][-1]["workspace_path"] == "/resolved/workspace"
    assert task["launch_history"][-1]["branch"] == "feature/p1-7-deployment"


def test_complete_launch_records_the_actual_workspace_path():
    task = {}
    models.normalize_task_execution(task)
    task["branch"] = "feature/p1-7-deployment"
    launch.complete_launch(
        task, executor_id="claude_code", succeeded=True, workspace_path="/resolved/workspace"
    )
    assert task["launch_history"][-1]["workspace_path"] == "/resolved/workspace"
    assert task["launch_history"][-1]["branch"] == "feature/p1-7-deployment"


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


# --------------------------------------------------------------------------
# End-to-end: a task inherits its workspace at creation time (via
# `project_config.task_defaults_from_project`) and Launch resolves that same
# path unchanged — proving the two mechanisms agree rather than drifting.
# --------------------------------------------------------------------------


def test_launch_resolves_the_same_workspace_a_task_inherited_at_creation(tmp_path):
    from command_center import project_config, tasks_repository

    project_repo = tmp_path / "aios"
    project_repo.mkdir()
    _init_repo(project_repo)

    project_default_workspace = tmp_path / "aios-worktree"
    project_default_workspace.mkdir()
    _init_repo(project_default_workspace)

    cfg = project_config.default_project_config("AIOS")
    cfg["repository_path"] = str(project_repo)
    cfg["default_workspace_path"] = str(project_default_workspace)
    cfg["default_branch"] = "main"

    inherited = project_config.task_defaults_from_project(cfg)
    task = tasks_repository.new_task_record(
        "AIOS", "T", "implementation", "Backlog",
        workspace_path=inherited["workspace_path"], branch=inherited["branch"],
    )

    # The task was never explicitly pointed at a workspace by hand — it
    # inherited it from the project at creation — yet Launch still resolves
    # to it (the "task" precedence tier), not the project repository.
    selection = launch.resolve_workspace_path(task=task, project_config=cfg)
    assert selection.path == str(project_default_workspace)
    assert selection.source == launch.WORKSPACE_SOURCE_TASK


def test_launch_resolves_repository_path_when_task_inherited_from_project_with_no_default_workspace(tmp_path):
    """Legacy-shaped project (only `repository_path` configured, matching
    every project before this feature existed): a task created against it
    still inherits a usable workspace_path, and Launch resolves to the exact
    same repository — proving no behavior changed for projects that never
    configure `default_workspace_path`."""
    from command_center import project_config, tasks_repository

    project_repo = tmp_path / "aios"
    project_repo.mkdir()
    _init_repo(project_repo)

    cfg = project_config.default_project_config("AIOS")
    cfg["repository_path"] = str(project_repo)

    inherited = project_config.task_defaults_from_project(cfg)
    task = tasks_repository.new_task_record(
        "AIOS", "T", "implementation", "Backlog", workspace_path=inherited["workspace_path"],
    )

    selection = launch.resolve_workspace_path(task=task, project_config=cfg)
    assert selection.path == str(project_repo)
    assert selection.source == launch.WORKSPACE_SOURCE_TASK
