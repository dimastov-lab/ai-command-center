"""Read-only Git discovery, parameterized by repository path.

Extracted from `app.py`'s original git helpers (which hardcoded `cwd=ROOT`, the AI
Command Center's own repository). Every function here takes an explicit `cwd: Path`
so callers — including Workspace Home (see `WORKSPACE_HOME_ARCHITECTURE.md` §7) — can
query any of the managed projects' repositories, not just this one.

Same subprocess-safety properties as the original: fixed argv, never `shell=True`,
`capture_output=True`, `text=True`, an explicit timeout, and only the read-only git
subcommands already documented in `ARCHITECTURE.md` §6 (`rev-parse`,
`branch --show-current`, `branch --list`, `status --porcelain`, `log`, `diff --stat`,
`remote -v`, `worktree list --porcelain`). No git-write subcommand is ever invoked.
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
    # Combine the "is_repo" probe and branch discovery into a single call.
    # `--abbrev-ref HEAD` returns the branch name or "HEAD" for detached HEAD.
    probe = run_git_command(cwd, ["rev-parse", "--show-toplevel", "--abbrev-ref", "HEAD"])
    if probe is None or probe.returncode != 0:
        return {"is_repo": False}

    probe_lines = probe.stdout.splitlines()
    root = probe_lines[0].strip() if probe_lines else ""
    branch_raw = probe_lines[1].strip() if len(probe_lines) > 1 else ""
    branch = branch_raw if branch_raw and branch_raw != "HEAD" else "(detached HEAD)"

    status = run_git_command(cwd, ["status", "--porcelain"])
    # Get short hash AND commit subject in a single call (tab-separated).
    head = run_git_command(cwd, ["log", "-1", "--pretty=%h%x1f%s"])

    status_lines = [
        line
        for line in (status.stdout.splitlines() if status and status.returncode == 0 else [])
        if line
    ]
    untracked_count = sum(1 for line in status_lines if line.startswith("??"))
    modified_count = len(status_lines) - untracked_count

    head_hash = "—"
    head_subject = "—"
    if head and head.returncode == 0 and head.stdout.strip():
        parts = head.stdout.strip().split("\x1f", 1)
        head_hash = parts[0] if parts[0] else "—"
        head_subject = parts[1].strip() if len(parts) > 1 else "—"

    return {
        "is_repo": True,
        "root": root,
        "branch": branch,
        "dirty": bool(status_lines),
        "modified_count": modified_count,
        "untracked_count": untracked_count,
        "last_commit_hash": head_hash,
        "last_commit_subject": head_subject,
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
