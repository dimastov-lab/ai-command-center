"""Deterministic, read-only git inspection for the autonomous completion pipeline.

This module answers the git-state questions the completion evaluator needs but
`command_center.git_info` (a display-oriented status reader) does not expose:
commits ahead of a base, configured upstream, remote-branch existence, and —
critically for merge verification — whether a given commit is *reachable from*
a target ref (which is how a squash-merged change is confirmed present in
`main` even though the original commit hash no longer survives).

Every call routes through `git_info.run_git_command` — the one shared read-only
git subprocess primitive (fixed argv, never `shell=True`, explicit timeout) —
so there is no second copy of subprocess-invocation logic. Nothing here mutates
the repository; all git *writes* the pipeline performs (push, branch recreate)
live in `runtime.git_ops`, kept deliberately separate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from command_center import git_info

DEFAULT_REMOTE = "origin"

# Network-touching calls (ls-remote, fetch) get a longer budget than local ones.
_LOCAL_TIMEOUT = 5
_REMOTE_TIMEOUT = 30


@dataclass
class RepositoryState:
    """A snapshot of everything the completion evaluator needs to know about a
    run's working tree and how its branch relates to the base/target."""

    repository_path: str
    worktree_exists: bool = False
    is_repo: bool = False
    current_branch: str | None = None
    detached_head: bool = False
    dirty: bool = False
    uncommitted_paths: list[str] = field(default_factory=list)
    head_commit: str | None = None
    branch: str | None = None
    base_branch: str | None = None
    base_ref: str | None = None
    base_ref_exists: bool = False
    base_tip: str | None = None
    head_is_base_tip: bool = False
    commits_ahead_of_base: int = 0
    upstream: str | None = None
    remote: str = DEFAULT_REMOTE
    remote_branch_exists: bool = False
    target_ref: str | None = None
    target_ref_exists: bool = False
    commit_in_target: bool = False
    error: str | None = None

    @property
    def has_task_commit(self) -> bool:
        """True iff the branch carries at least one commit not already on the
        base — the "at least one task-related commit" completion evidence."""
        return self.commits_ahead_of_base > 0


def _run(repo: Path, args: list[str], timeout: int = _LOCAL_TIMEOUT):
    return git_info.run_git_command(repo, args, timeout=timeout)


def _ok(proc) -> bool:
    return proc is not None and proc.returncode == 0


def _out(proc) -> str:
    return proc.stdout.strip() if proc is not None and proc.stdout else ""


def is_inside_work_tree(repo: Path) -> bool:
    proc = _run(repo, ["rev-parse", "--is-inside-work-tree"])
    return _ok(proc) and _out(proc) == "true"


def current_branch(repo: Path) -> str | None:
    """The checked-out branch name, or None when detached."""
    proc = _run(repo, ["branch", "--show-current"])
    if not _ok(proc):
        return None
    name = _out(proc)
    return name or None


def head_commit(repo: Path) -> str | None:
    proc = _run(repo, ["rev-parse", "HEAD"])
    return _out(proc) if _ok(proc) else None


def is_dirty(repo: Path) -> tuple[bool, list[str]]:
    """(dirty, changed_paths) from `git status --porcelain`. Untracked files
    count as dirty — an agent that left a new file uncommitted has not
    finished the task."""
    proc = _run(repo, ["status", "--porcelain"])
    if not _ok(proc):
        return False, []
    lines = [line for line in (proc.stdout or "").splitlines() if line.strip()]
    paths = [line[3:] if len(line) > 3 else line for line in lines]
    return bool(lines), paths


def ref_exists(repo: Path, ref: str) -> bool:
    """Whether a ref (branch, remote-tracking ref, or commit) resolves to a
    commit object locally."""
    proc = _run(repo, ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"])
    return _ok(proc) and bool(_out(proc))


def commits_ahead(repo: Path, base_ref: str, head: str = "HEAD") -> int:
    """Number of commits reachable from `head` but not from `base_ref`
    (`git rev-list --count base..head`). Returns 0 if either ref is unknown."""
    proc = _run(repo, ["rev-list", "--count", f"{base_ref}..{head}"])
    if not _ok(proc):
        return 0
    try:
        return int(_out(proc) or "0")
    except ValueError:
        return 0


def is_ancestor(repo: Path, commit: str, ref: str) -> bool:
    """True iff `commit` is an ancestor of (i.e. already contained in) `ref`.

    This is the primitive behind target-branch verification. A normal merge or
    fast-forward leaves the branch's own commit an ancestor of `main`; a squash
    merge does not, which is why the service also checks the *merge commit*
    (the squash commit that lands on `main`) through this same function."""
    if not commit:
        return False
    proc = _run(repo, ["merge-base", "--is-ancestor", commit, ref])
    # Exit 0 => is ancestor; exit 1 => not; other/None => unknown (treat False).
    return proc is not None and proc.returncode == 0


def upstream_ref(repo: Path, branch: str | None = None) -> str | None:
    """The configured upstream (tracking) ref for `branch` (or the current
    branch), e.g. `origin/task/aicc-1`, or None if none is configured."""
    spec = f"{branch}@{{u}}" if branch else "@{u}"
    proc = _run(repo, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", spec])
    if not _ok(proc):
        return None
    return _out(proc) or None


def remote_branch_exists(repo: Path, remote: str, branch: str, *, timeout: int = _REMOTE_TIMEOUT) -> bool:
    """Whether `branch` currently exists on `remote` (`git ls-remote --heads`).

    Authoritative against the actual remote — this is what distinguishes "the
    remote branch was deleted" (PR closed, branch gone) from "still pushed".
    Returns False if the remote is unreachable, so callers must not treat a
    transient network failure as a confirmed deletion without corroboration."""
    proc = _run(repo, ["ls-remote", "--heads", remote, branch], timeout=timeout)
    if not _ok(proc):
        return False
    return bool(_out(proc))


def fetch(repo: Path, remote: str = DEFAULT_REMOTE, *, timeout: int = _REMOTE_TIMEOUT) -> bool:
    """`git fetch --prune <remote>` so remote-tracking refs (used for target
    verification) reflect the real remote. Read-only w.r.t. the working tree.
    Returns whether the fetch succeeded."""
    proc = _run(repo, ["fetch", "--prune", remote], timeout=timeout)
    return _ok(proc)


def inspect_repository(
    repository_path: str,
    *,
    base_branch: str | None,
    branch: str | None = None,
    remote: str = DEFAULT_REMOTE,
    target_ref: str | None = None,
    verify_commit: str | None = None,
    check_remote: bool = True,
) -> RepositoryState:
    """Build a full `RepositoryState` for `repository_path`.

    `target_ref` defaults to `<remote>/<base_branch>` — the branch a merge must
    land on for the task to be complete. `verify_commit`, when given (e.g. a PR
    merge commit), is additionally checked for reachability from the target,
    which is how squash merges are verified. `check_remote=False` skips the two
    network calls (used in tests / offline evaluation)."""
    repo = Path(repository_path)
    state = RepositoryState(
        repository_path=repository_path,
        branch=branch,
        base_branch=base_branch,
        remote=remote,
    )
    state.worktree_exists = repo.exists() and repo.is_dir()
    if not state.worktree_exists:
        state.error = "worktree_missing"
        return state
    if not is_inside_work_tree(repo):
        state.error = "not_a_repository"
        return state
    state.is_repo = True

    state.current_branch = current_branch(repo)
    state.detached_head = state.current_branch is None
    state.dirty, state.uncommitted_paths = is_dirty(repo)
    state.head_commit = head_commit(repo)
    state.upstream = upstream_ref(repo, branch)

    # Resolve the base ref: prefer the remote-tracking ref (what a merge target
    # really is), fall back to a local base branch.
    if base_branch:
        remote_base = f"{remote}/{base_branch}"
        if ref_exists(repo, remote_base):
            state.base_ref = remote_base
        elif ref_exists(repo, base_branch):
            state.base_ref = base_branch
        state.base_ref_exists = state.base_ref is not None
        if state.base_ref:
            state.commits_ahead_of_base = commits_ahead(repo, state.base_ref, state.head_commit or "HEAD")
            base_tip_proc = _run(repo, ["rev-parse", state.base_ref])
            state.base_tip = _out(base_tip_proc) if _ok(base_tip_proc) else None
            # "No task commit" signal: HEAD is exactly the base tip => the run
            # produced no commit at all (distinct from a change that was merged,
            # where HEAD differs from the base tip but is reachable from it).
            state.head_is_base_tip = bool(
                state.head_commit and state.base_tip and state.head_commit == state.base_tip
            )

    # Target ref for merge verification (defaults to remote base).
    state.target_ref = target_ref or (f"{remote}/{base_branch}" if base_branch else None)
    if state.target_ref:
        state.target_ref_exists = ref_exists(repo, state.target_ref)
        if state.target_ref_exists:
            reachable = False
            if state.head_commit and is_ancestor(repo, state.head_commit, state.target_ref):
                reachable = True
            if not reachable and verify_commit and is_ancestor(repo, verify_commit, state.target_ref):
                reachable = True
            state.commit_in_target = reachable

    if check_remote and branch:
        state.remote_branch_exists = remote_branch_exists(repo, remote, branch)

    return state
