"""Read-only git discovery helpers, parameterized by repository path.

Extracted from `app.py`'s ROOT-only git helpers so the same logic can be
reused per-project (each project's own `repository_path`), not only for this
application's own repository. Every subcommand here is read-only; there is
no git-write capability in this module.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def run_git_command(cwd: Path, args: list[str], timeout: int = 5) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def get_status(cwd: Path) -> dict[str, object]:
    toplevel = run_git_command(cwd, ["rev-parse", "--show-toplevel"])
    if toplevel is None or toplevel.returncode != 0:
        return {"is_repo": False}

    branch = run_git_command(cwd, ["branch", "--show-current"])
    status = run_git_command(cwd, ["status", "--porcelain"])
    head_hash = run_git_command(cwd, ["rev-parse", "--short", "HEAD"])
    head_subject = run_git_command(cwd, ["log", "-1", "--pretty=%s"])

    status_lines = [
        line
        for line in (status.stdout.splitlines() if status and status.returncode == 0 else [])
        if line
    ]
    untracked_count = sum(1 for line in status_lines if line.startswith("??"))
    modified_count = len(status_lines) - untracked_count

    return {
        "is_repo": True,
        "root": toplevel.stdout.strip(),
        "branch": branch.stdout.strip() if branch and branch.stdout.strip() else "(detached HEAD)",
        "dirty": bool(status_lines),
        "modified_count": modified_count,
        "untracked_count": untracked_count,
        "last_commit_hash": head_hash.stdout.strip() if head_hash and head_hash.returncode == 0 else "—",
        "last_commit_subject": head_subject.stdout.strip() if head_subject and head_subject.returncode == 0 else "—",
        "status_lines": status_lines,
    }


def get_log(cwd: Path, limit: int = 20) -> list[dict[str, str]]:
    result = run_git_command(
        cwd,
        ["log", f"-{limit}", "--pretty=format:%h%x1f%an%x1f%ad%x1f%s", "--date=short"],
        timeout=10,
    )
    if result is None or result.returncode != 0 or not result.stdout.strip():
        return []

    commits: list[dict[str, str]] = []
    for line in result.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 4:
            commits.append({"hash": parts[0], "author": parts[1], "date": parts[2], "subject": parts[3]})
    return commits


def get_diff_stat(cwd: Path, staged: bool = False) -> str:
    args = ["diff", "--cached", "--stat"] if staged else ["diff", "--stat"]
    result = run_git_command(cwd, args, timeout=10)
    if result is None or result.returncode != 0:
        return ""
    return result.stdout.strip()


def get_branches(cwd: Path) -> list[str]:
    result = run_git_command(cwd, ["branch", "--list", "--format=%(refname:short)"])
    if result is None or result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def get_remotes(cwd: Path) -> list[tuple[str, str]]:
    result = run_git_command(cwd, ["remote", "-v"])
    if result is None or result.returncode != 0:
        return []
    seen: dict[str, str] = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            seen.setdefault(parts[0], parts[1])
    return list(seen.items())


def get_worktrees(cwd: Path) -> list[dict[str, str]]:
    result = run_git_command(cwd, ["worktree", "list", "--porcelain"], timeout=10)
    if result is None or result.returncode != 0:
        return []

    worktrees: list[dict[str, str]] = []
    current: dict[str, str] = {}

    for line in result.stdout.splitlines():
        if not line.strip():
            if current:
                worktrees.append(current)
                current = {}
            continue
        if line.startswith("worktree "):
            current["path"] = line[len("worktree "):].strip()
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD "):].strip()[:10]
        elif line.startswith("branch "):
            current["branch"] = line[len("branch "):].strip().removeprefix("refs/heads/")
        elif line == "bare":
            current["branch"] = "(bare)"
        elif line == "detached":
            current["branch"] = "(detached)"

    if current:
        worktrees.append(current)

    return worktrees
