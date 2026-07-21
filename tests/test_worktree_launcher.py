"""Regression coverage for `command_center.worktree_launcher` — the
worktree-aware roadmap launcher validation + launch/permission profiles
(task AICC-LAUNCH-001).

Covers every validation scenario the task enumerates: normal repository,
linked worktree, detached HEAD, invalid repository, wrong branch, dirty
worktree, git-metadata accessibility, registered-vs-unregistered worktree, and
the launch/permission profile description.
"""

from __future__ import annotations

import ast
import inspect
import shutil
import subprocess
from pathlib import Path

from command_center import agent_runner, worktree_launcher


# --------------------------------------------------------------------------
# Local git helpers (throwaway repos only — never a real project)
# --------------------------------------------------------------------------


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, check=True)
    (path / "f.txt").write_text("hello\n")
    subprocess.run(["git", "add", "f.txt"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


def _current_branch(repo: Path) -> str:
    return subprocess.run(
        ["git", "branch", "--show-current"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def _add_worktree(repo: Path, *, path: Path, branch: str, base: str = "HEAD") -> None:
    subprocess.run(["git", "worktree", "add", "-b", branch, str(path), base], cwd=repo, check=True)


def _errors_of(validation) -> str:
    return " | ".join(validation.errors)


def _check(validation, name: str):
    return next((c for c in validation.checks if c.name == name), None)


# --------------------------------------------------------------------------
# Read-only invariant
# --------------------------------------------------------------------------


def test_worktree_launcher_never_constructs_a_git_subprocess_call():
    """Same hard invariant as `command_center.launch`: this module only reads
    git state through `command_center.git_info` — it must never itself build a
    `git` argv, so it can never merge/push/switch/reset/clean. AST-checked so
    the word "git" in docstrings/comments can't produce a false positive."""
    source = inspect.getsource(worktree_launcher)
    tree = ast.parse(source)
    string_literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "git" not in string_literals, "worktree_launcher must never construct a `git` subprocess argv itself"


# --------------------------------------------------------------------------
# validate_worktree — the enumerated scenarios
# --------------------------------------------------------------------------


def test_normal_repository_main_worktree_validates(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    branch = _current_branch(repo)

    validation = worktree_launcher.validate_worktree(
        repository_root=repo, worktree_path=repo, expected_branch=branch
    )
    assert validation.can_launch, _errors_of(validation)
    assert not validation.needs_confirmation
    assert validation.actual_branch == branch
    assert _check(validation, "registered_worktree").status == worktree_launcher.CHECK_OK
    assert _check(validation, "git_metadata").status == worktree_launcher.CHECK_OK


def test_linked_worktree_validates(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    linked = tmp_path / "linked"
    _add_worktree(repo, path=linked, branch="feature/x")

    validation = worktree_launcher.validate_worktree(
        repository_root=repo, worktree_path=linked, expected_branch="feature/x"
    )
    assert validation.can_launch, _errors_of(validation)
    assert validation.actual_branch == "feature/x"
    assert _check(validation, "registered_worktree").status == worktree_launcher.CHECK_OK


def test_detached_head_warns_without_expected_branch(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    subprocess.run(["git", "checkout", "-q", head], cwd=repo, check=True)

    validation = worktree_launcher.validate_worktree(
        repository_root=repo, worktree_path=repo, expected_branch=None
    )
    # Detached HEAD is a confirmable warning, not a hard block, when no
    # specific branch was expected.
    assert validation.can_launch, _errors_of(validation)
    assert validation.needs_confirmation
    assert any("detached HEAD" in w for w in validation.warnings)
    assert _check(validation, "branch").status == worktree_launcher.CHECK_WARNING


def test_detached_head_is_a_hard_error_when_a_branch_was_expected(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    subprocess.run(["git", "checkout", "-q", head], cwd=repo, check=True)

    validation = worktree_launcher.validate_worktree(
        repository_root=repo, worktree_path=repo, expected_branch="main"
    )
    assert not validation.can_launch
    assert any("detached" in e for e in validation.errors)


def test_invalid_repository_is_not_a_git_repo(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    validation = worktree_launcher.validate_worktree(
        repository_root=None, worktree_path=plain, expected_branch=None
    )
    assert not validation.can_launch
    assert any("не является git-репозиторием" in e for e in validation.errors)
    assert _check(validation, "git_repository").status == worktree_launcher.CHECK_FAILED


def test_missing_workspace_path_is_an_error(tmp_path):
    validation = worktree_launcher.validate_worktree(
        repository_root=tmp_path, worktree_path=None, expected_branch=None
    )
    assert not validation.can_launch
    assert any("не выбран" in e.lower() or "не выбран" in e for e in validation.errors)


def test_nonexistent_workspace_path_is_an_error(tmp_path):
    validation = worktree_launcher.validate_worktree(
        repository_root=tmp_path, worktree_path=tmp_path / "nope", expected_branch=None
    )
    assert not validation.can_launch
    assert any("не найден" in e for e in validation.errors)


def test_wrong_branch_is_a_hard_error(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    linked = tmp_path / "linked"
    _add_worktree(repo, path=linked, branch="actual-branch")

    validation = worktree_launcher.validate_worktree(
        repository_root=repo, worktree_path=linked, expected_branch="expected-branch"
    )
    assert not validation.can_launch
    assert any("не совпадает" in e for e in validation.errors)
    assert _check(validation, "branch").status == worktree_launcher.CHECK_FAILED


def test_dirty_worktree_blocks_by_default_and_warns_when_not_required(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    linked = tmp_path / "linked"
    _add_worktree(repo, path=linked, branch="feature/x")
    (linked / "uncommitted.txt").write_text("dirty")

    blocked = worktree_launcher.validate_worktree(
        repository_root=repo, worktree_path=linked, expected_branch="feature/x"
    )
    assert not blocked.can_launch
    assert any("не чист" in e for e in blocked.errors)
    assert _check(blocked, "clean").status == worktree_launcher.CHECK_FAILED

    relaxed = worktree_launcher.validate_worktree(
        repository_root=repo, worktree_path=linked, expected_branch="feature/x", require_clean=False
    )
    assert relaxed.can_launch, _errors_of(relaxed)
    assert relaxed.needs_confirmation
    assert _check(relaxed, "clean").status == worktree_launcher.CHECK_WARNING


def test_unregistered_worktree_is_blocked_even_when_it_is_a_git_repo(tmp_path):
    """A directory that is a perfectly valid git repository but was never
    `git worktree add`-ed against the mapped repository must be rejected — the
    core "registered worktree" guarantee."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    unrelated = tmp_path / "unrelated"
    _init_repo(unrelated)  # its own repo, not a worktree of `repo`
    branch = _current_branch(unrelated)

    validation = worktree_launcher.validate_worktree(
        repository_root=repo, worktree_path=unrelated, expected_branch=branch
    )
    assert not validation.can_launch
    assert any("зарегистрированным worktree" in e for e in validation.errors)
    assert _check(validation, "registered_worktree").status == worktree_launcher.CHECK_FAILED


def test_missing_repository_root_blocks_registration_check(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    validation = worktree_launcher.validate_worktree(
        repository_root=None, worktree_path=repo, expected_branch=None
    )
    assert not validation.can_launch
    assert any("не сопоставлен" in e for e in validation.errors)


# --------------------------------------------------------------------------
# git_metadata_accessible — corrupted / pruned worktree metadata
# --------------------------------------------------------------------------


def test_git_metadata_accessible_on_healthy_repo(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    ok, detail = worktree_launcher.git_metadata_accessible(repo)
    assert ok, detail


def test_git_metadata_inaccessible_when_linked_worktree_admin_dir_removed(tmp_path):
    """A linked worktree whose backing `.git/worktrees/<name>` administrative
    directory was deleted survives on disk but its git metadata is gone — this
    must be reported distinctly, not as "not a git repository"."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    linked = tmp_path / "linked"
    _add_worktree(repo, path=linked, branch="feature/x")

    admin_root = repo / ".git" / "worktrees"
    for child in admin_root.iterdir():
        shutil.rmtree(child)

    ok, detail = worktree_launcher.git_metadata_accessible(linked)
    assert not ok
    assert "метаданные" in detail

    validation = worktree_launcher.validate_worktree(
        repository_root=repo, worktree_path=linked, expected_branch="feature/x"
    )
    assert not validation.can_launch
    assert any("метаданные" in e for e in validation.errors)


# --------------------------------------------------------------------------
# is_registered_worktree
# --------------------------------------------------------------------------


def test_is_registered_worktree_true_for_main_and_linked(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    linked = tmp_path / "linked"
    _add_worktree(repo, path=linked, branch="feature/x")

    assert worktree_launcher.is_registered_worktree(repo, repo)
    assert worktree_launcher.is_registered_worktree(repo, linked)


def test_is_registered_worktree_false_for_unrelated_repo(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    unrelated = tmp_path / "unrelated"
    _init_repo(unrelated)
    assert not worktree_launcher.is_registered_worktree(repo, unrelated)


# --------------------------------------------------------------------------
# Launch profile / permission profile
# --------------------------------------------------------------------------


def test_permission_profile_read_only_task_type():
    profile = worktree_launcher.describe_permission_profile("review")
    assert profile.key == worktree_launcher.PROFILE_READ_ONLY
    assert profile.allowed_tools == tuple(agent_runner.READ_ONLY_ALLOWED_TOOLS)
    assert profile.disallowed_tools == ()
    assert "Bash" not in " ".join(profile.allowed_tools or ())


def test_permission_profile_implementation_task_type():
    profile = worktree_launcher.describe_permission_profile("implementation")
    assert profile.key == worktree_launcher.PROFILE_IMPLEMENTATION
    assert profile.allowed_tools is None  # Bash available
    assert profile.disallowed_tools == tuple(agent_runner.GIT_WRITE_DISALLOWED_TOOLS)


def test_launch_profile_bundles_executor_and_permission():
    lp = worktree_launcher.describe_launch_profile(executor_id="claude_code", task_type="review")
    assert lp.executor_id == "claude_code"
    assert lp.executor_label == "Claude Code"
    assert lp.executor_available is True
    assert lp.permission.key == worktree_launcher.PROFILE_READ_ONLY
    assert "Claude Code" in lp.label
    assert "Read-only" in lp.label


def test_launch_profile_unknown_executor_falls_back_to_claude_code():
    lp = worktree_launcher.describe_launch_profile(executor_id="does-not-exist", task_type="implementation")
    # `executors.get_executor` returns the claude_code default for an unknown id.
    assert lp.executor_id == "claude_code"
    assert lp.permission.key == worktree_launcher.PROFILE_IMPLEMENTATION
