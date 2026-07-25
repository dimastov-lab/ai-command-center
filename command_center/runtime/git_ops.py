"""The completion pipeline's git *write* adapter — the narrow, privileged set
of mutating git commands the autonomous pipeline is allowed to run.

Kept deliberately separate from `git_info`/`repo_state` (both strictly
read-only) and from the agent sandbox (where `git push`/`git merge` are
disallowed tools). Every command here is a fixed argv list run with
`shell=False` and an explicit timeout, mirroring
`portfolio_launch._run_git_write`.

Safety invariants enforced structurally:
  * **Never force-push** — `--force`/`--force-with-lease`/`+refspec` are
    rejected before any subprocess runs.
  * Pushing an ordinary branch is idempotent: re-pushing an up-to-date or
    already-recreated branch is a successful no-op, which is what makes remote-
    branch recreation safe to retry.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_DEFAULT_TIMEOUT = 60

_FORCE_TOKENS = ("--force", "-f", "--force-with-lease")


class GitOpsError(Exception):
    """A git write command failed (non-zero exit or could not be spawned).
    Carries the command, return code, and captured stderr for the audit
    trail — never any environment or credentials."""

    def __init__(self, args: list[str], returncode: int | None, stderr: str) -> None:
        self.args = args
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"git {' '.join(args)} failed (rc={returncode}): {stderr.strip()[:400]}")


def _assert_not_force(args: list[str]) -> None:
    for token in args:
        if token in _FORCE_TOKENS or token.startswith("+"):
            raise GitOpsError(args, None, f"force-push is forbidden (offending token: {token!r})")


def run_git_write(repo: Path, args: list[str], *, timeout: int = _DEFAULT_TIMEOUT) -> subprocess.CompletedProcess:
    """Run a mutating `git` command, returning the CompletedProcess. Raises
    `GitOpsError` on spawn failure; the caller inspects `returncode`."""
    _assert_not_force(args)
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - spawn failure
        raise GitOpsError(args, None, str(exc)) from exc


def commit_all(repo: Path, *, message: str) -> subprocess.CompletedProcess | None:
    """Stage every change in `repo` and commit it on the current branch.

    This is how the *pipeline* commits the work an agent produced — the agent
    runs sandboxed with no git-write tools, so it leaves its changes uncommitted
    in its own isolated worktree, and the completion pipeline turns those into a
    real commit on the task branch before validating and opening the pull
    request. Only ever called on a task's own linked worktree (isolation is
    enforced upstream), never on a primary tree.

    Returns the commit `CompletedProcess`, or `None` when there was nothing to
    commit (a clean tree — an idempotent no-op, not an error)."""
    status = run_git_write(repo, ["status", "--porcelain"])
    if status.returncode == 0 and not (status.stdout or "").strip():
        return None  # nothing to commit
    add = run_git_write(repo, ["add", "-A"])
    if add.returncode != 0:
        raise GitOpsError(["add", "-A"], add.returncode, add.stderr or "")
    # `--no-verify` so a repo's own pre-commit hooks (lint/format) cannot block
    # the pipeline from capturing the agent's work; validation runs separately.
    return run_git_write(repo, ["commit", "--no-verify", "-m", message])


def push_branch(
    repo: Path,
    *,
    remote: str,
    branch: str,
    set_upstream: bool = True,
) -> subprocess.CompletedProcess:
    """Push `branch` to `remote` (recreating the remote branch if it was
    deleted). Never force-pushes, so this fails loudly rather than clobbering
    if the remote branch has diverged — the safe behavior for recovery.

    Idempotent: an up-to-date branch pushes as a successful no-op."""
    args = ["push"]
    if set_upstream:
        args.append("--set-upstream")
    # Explicit `branch:branch` refspec so we never rely on push.default config.
    args.extend([remote, f"{branch}:{branch}"])
    proc = run_git_write(repo, args)
    if proc.returncode != 0:
        raise GitOpsError(args, proc.returncode, proc.stderr or "")
    return proc
