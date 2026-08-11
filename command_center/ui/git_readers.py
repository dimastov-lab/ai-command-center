"""Read-only git helpers extracted from ``app.py`` (NIGHT-W9-AICC-ARCH slice 2).

These are the thin, repo-root-pinned wrappers over
``command_center.git_info`` that used to live inline in ``app.py`` (its
"Git (read-only)" section). Everything here is **read-side only** — status,
log, diff-stat, branches, remotes, worktrees. The write side of git
(checkout, branch creation, commit, push) lives in
``command_center/runtime/git_ops.py`` and must never be mixed into this
module: the Git Center page reads through here and mutates only through the
runtime's supervised operations.

``app.py`` re-exports every name below unchanged, so existing call sites and
``app.<name>`` test references keep working.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from command_center import git_info

# Same repository root `app.py` binds its own ROOT to (this file lives at
# command_center/ui/git_readers.py, two levels below it).
ROOT = Path(__file__).resolve().parents[2]


def run_git_command(args: list[str], timeout: int = 5) -> subprocess.CompletedProcess | None:
    return git_info.run_git_command(ROOT, args, timeout=timeout)


def get_git_status() -> dict[str, object]:
    return git_info.get_status(ROOT)


def get_git_log(limit: int = 20) -> list[dict[str, str]]:
    return git_info.get_log(ROOT, limit=limit)


def get_git_diff_stat(staged: bool = False) -> str:
    return git_info.get_diff_stat(ROOT, staged=staged)


def get_git_branches() -> list[str]:
    return git_info.get_branches(ROOT)


def get_git_remotes() -> list[tuple[str, str]]:
    return git_info.get_remotes(ROOT)


def get_git_worktrees() -> list[dict[str, str]]:
    return git_info.get_worktrees(ROOT)
