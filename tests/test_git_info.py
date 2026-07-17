"""Unit tests for `command_center.git_info` — read-only, per-repository git discovery."""

from __future__ import annotations

import subprocess

from command_center import git_info


def test_get_status_valid_repo(git_repo):
    status = git_info.get_status(git_repo)
    assert status["is_repo"] is True
    assert status["branch"]
    assert status["dirty"] is False
    assert status["modified_count"] == 0
    assert status["untracked_count"] == 0
    assert status["last_commit_hash"] != "—"
    assert status["last_commit_subject"] == "init"


def test_get_status_missing_repository(tmp_path):
    missing = tmp_path / "does-not-exist"
    status = git_info.get_status(missing)
    assert status == {"is_repo": False}


def test_get_status_non_git_directory(tmp_path):
    not_a_repo = tmp_path / "plain_dir"
    not_a_repo.mkdir()
    status = git_info.get_status(not_a_repo)
    assert status == {"is_repo": False}


def test_get_status_detached_head(git_repo):
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=git_repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    subprocess.run(["git", "checkout", "-q", head], cwd=git_repo, check=True)

    status = git_info.get_status(git_repo)
    assert status["is_repo"] is True
    assert status["branch"] == "(detached HEAD)"


def test_get_status_dirty_worktree(git_repo):
    (git_repo / "new_file.txt").write_text("untracked\n")
    (git_repo / "f.txt").write_text("modified\n")

    status = git_info.get_status(git_repo)
    assert status["dirty"] is True
    assert status["untracked_count"] == 1
    assert status["modified_count"] == 1


def test_get_worktrees_lists_main_worktree(git_repo):
    worktrees = git_info.get_worktrees(git_repo)
    assert len(worktrees) == 1
    assert worktrees[0]["path"] == str(git_repo)


def test_get_worktrees_after_worktree_add(git_repo, tmp_path):
    extra = tmp_path / "extra_worktree"
    subprocess.run(
        ["git", "worktree", "add", "-q", str(extra), "-b", "feature/extra"],
        cwd=git_repo,
        check=True,
    )

    worktrees = git_info.get_worktrees(git_repo)
    paths = {w["path"] for w in worktrees}
    assert str(git_repo) in paths
    assert str(extra) in paths
    extra_entry = next(w for w in worktrees if w["path"] == str(extra))
    assert extra_entry["branch"] == "feature/extra"


def test_get_worktrees_missing_repository(tmp_path):
    assert git_info.get_worktrees(tmp_path / "nope") == []


def test_get_worktrees_non_git_directory(tmp_path):
    not_a_repo = tmp_path / "plain_dir"
    not_a_repo.mkdir()
    assert git_info.get_worktrees(not_a_repo) == []


def test_get_log_returns_commits(git_repo):
    commits = git_info.get_log(git_repo, limit=5)
    assert len(commits) == 1
    assert commits[0]["subject"] == "init"
    assert set(commits[0]) == {"hash", "author", "date", "subject"}


def test_get_log_missing_repository(tmp_path):
    assert git_info.get_log(tmp_path / "nope") == []


def test_get_diff_stat_no_changes(git_repo):
    assert git_info.get_diff_stat(git_repo) == ""
    assert git_info.get_diff_stat(git_repo, staged=True) == ""


def test_get_diff_stat_with_unstaged_changes(git_repo):
    (git_repo / "f.txt").write_text("changed\n")
    assert "f.txt" in git_info.get_diff_stat(git_repo)


def test_get_diff_stat_missing_repository(tmp_path):
    assert git_info.get_diff_stat(tmp_path / "nope") == ""


def test_get_branches_lists_current_branch(git_repo):
    branches = git_info.get_branches(git_repo)
    status = git_info.get_status(git_repo)
    assert status["branch"] in branches


def test_get_branches_missing_repository(tmp_path):
    assert git_info.get_branches(tmp_path / "nope") == []


def test_get_remotes_empty_for_local_only_repo(git_repo):
    assert git_info.get_remotes(git_repo) == []


def test_get_remotes_missing_repository(tmp_path):
    assert git_info.get_remotes(tmp_path / "nope") == []


def test_run_git_command_timeout(git_repo, monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="git", timeout=kwargs.get("timeout", 5))

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert git_info.run_git_command(git_repo, ["status", "--porcelain"]) is None


def test_run_git_command_oserror(git_repo, monkeypatch):
    def fake_run(*args, **kwargs):
        raise OSError("git binary not found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert git_info.run_git_command(git_repo, ["status", "--porcelain"]) is None


def test_run_git_command_never_uses_shell(git_repo):
    result = git_info.run_git_command(git_repo, ["rev-parse", "--show-toplevel"])
    assert result is not None
    assert result.returncode == 0
