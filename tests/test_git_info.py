"""Unit tests for `command_center/git_info.py` — parameterized-by-`cwd` git discovery."""

from __future__ import annotations

import subprocess

from command_center import git_info


def test_get_status_on_real_repo(git_repo):
    status = git_info.get_status(git_repo)
    assert status["is_repo"] is True
    assert status["dirty"] is False
    assert status["branch"]
    assert status["last_commit_hash"] != "—"
    assert status["last_commit_subject"] == "init"


def test_get_status_on_missing_path(tmp_path):
    missing = tmp_path / "does-not-exist"
    status = git_info.get_status(missing)
    assert status == {"is_repo": False}


def test_get_status_on_non_git_directory(tmp_path):
    not_a_repo = tmp_path / "plain-dir"
    not_a_repo.mkdir()
    status = git_info.get_status(not_a_repo)
    assert status == {"is_repo": False}


def test_get_status_dirty_with_untracked_and_modified(git_repo):
    (git_repo / "f.txt").write_text("changed\n")
    (git_repo / "new.txt").write_text("new\n")
    status = git_info.get_status(git_repo)
    assert status["dirty"] is True
    assert status["modified_count"] == 1
    assert status["untracked_count"] == 1


def test_get_log(git_repo):
    commits = git_info.get_log(git_repo, limit=5)
    assert len(commits) == 1
    assert commits[0]["subject"] == "init"
    assert set(commits[0].keys()) == {"hash", "author", "date", "subject"}


def test_get_diff_stat_empty_when_clean(git_repo):
    assert git_info.get_diff_stat(git_repo) == ""
    assert git_info.get_diff_stat(git_repo, staged=True) == ""


def test_get_diff_stat_unstaged_and_staged(git_repo):
    (git_repo / "f.txt").write_text("changed\n")
    assert "f.txt" in git_info.get_diff_stat(git_repo)
    assert git_info.get_diff_stat(git_repo, staged=True) == ""

    subprocess.run(["git", "add", "f.txt"], cwd=git_repo, check=True)
    assert git_info.get_diff_stat(git_repo) == ""
    assert "f.txt" in git_info.get_diff_stat(git_repo, staged=True)


def test_get_branches(git_repo):
    branches = git_info.get_branches(git_repo)
    assert len(branches) == 1


def test_get_remotes_empty_when_none_configured(git_repo):
    assert git_info.get_remotes(git_repo) == []


def test_get_remotes_with_origin(git_repo, tmp_path):
    other = tmp_path / "other.git"
    subprocess.run(["git", "init", "-q", "--bare", str(other)], check=True)
    subprocess.run(["git", "remote", "add", "origin", str(other)], cwd=git_repo, check=True)
    remotes = git_info.get_remotes(git_repo)
    assert ("origin", str(other)) in remotes


def test_fetch_and_ahead_behind_use_explicit_refresh(git_repo, tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=git_repo, check=True)
    subprocess.run(["git", "push", "-q", "-u", "origin", "main"], cwd=git_repo, check=True)

    # A local commit is ahead; a commit pushed from a second clone is behind.
    (git_repo / "local.txt").write_text("local\n")
    subprocess.run(["git", "add", "local.txt"], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "local"], cwd=git_repo, check=True)

    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", "-b", "main", str(remote), str(other)], check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=other, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=other, check=True)
    (other / "remote.txt").write_text("remote\n")
    subprocess.run(["git", "add", "remote.txt"], cwd=other, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "remote"], cwd=other, check=True)
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=other, check=True)

    # No implicit fetch: origin/main is still the locally cached ref.
    assert git_info.get_ahead_behind(git_repo) == {
        "available": True,
        "upstream": "origin/main",
        "ahead": 1,
        "behind": 0,
        "error": "",
    }

    assert git_info.fetch_remotes(git_repo) == (True, "")
    assert git_info.get_ahead_behind(git_repo) == {
        "available": True,
        "upstream": "origin/main",
        "ahead": 1,
        "behind": 1,
        "error": "",
    }


def test_ahead_behind_reports_missing_upstream_without_fetch(git_repo, monkeypatch):
    calls = []
    real_run = git_info.run_git_command

    def record(cwd, args, timeout=5):
        calls.append(args)
        return real_run(cwd, args, timeout)

    monkeypatch.setattr(git_info, "run_git_command", record)
    result = git_info.get_ahead_behind(git_repo)

    assert result["available"] is False
    assert "tracking" in str(result["error"])
    assert not any(args and args[0] == "fetch" for args in calls)


def test_fetch_remotes_surfaces_failure(git_repo):
    subprocess.run(
        ["git", "remote", "add", "origin", str(git_repo / "missing-remote.git")],
        cwd=git_repo,
        check=True,
    )
    ok, error = git_info.fetch_remotes(git_repo)
    assert ok is False
    assert error


def test_get_worktrees_single(git_repo):
    worktrees = git_info.get_worktrees(git_repo)
    assert len(worktrees) == 1
    assert worktrees[0]["path"] == str(git_repo)


def test_get_worktrees_after_add(git_repo, tmp_path):
    new_branch_path = tmp_path / "worktree-2"
    subprocess.run(
        ["git", "worktree", "add", "-b", "feature/x", str(new_branch_path)],
        cwd=git_repo,
        check=True,
    )
    worktrees = git_info.get_worktrees(git_repo)
    assert len(worktrees) == 2
    branches = {w.get("branch") for w in worktrees}
    assert "feature/x" in branches


def test_get_worktrees_on_missing_path(tmp_path):
    assert git_info.get_worktrees(tmp_path / "does-not-exist") == []


def test_detached_head(git_repo):
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=git_repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    subprocess.run(["git", "checkout", "-q", head], cwd=git_repo, check=True)
    status = git_info.get_status(git_repo)
    assert status["is_repo"] is True
    assert status["branch"] == "(detached HEAD)"


def test_run_git_command_returns_none_on_bad_binary(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    assert git_info.run_git_command(tmp_path, ["status"]) is None
