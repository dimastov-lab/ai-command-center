"""Shared primitives for invoking the external ``voyn-lease`` CLI tool.

``orchestrator.publish`` (the lease held around a `git push`) and
``worker.writer_lease`` (the lease held across the whole provision -> agent
-> tests -> publish lifecycle, VOYN-W0-AICC-LEASE-FULL-LIFECYCLE-FENCE) both
shell out to the same external tool with the same identity argv shape. This
module is the one place that shape is defined, so the two callers cannot
drift on how a lease row's owner is constructed -- and so a third caller
never has a reason to write a third copy.

``voyn-lease`` itself is not vendored in this repository; it is an external
binary named by ``VOYN_LEASE_TOOL`` (default ``"voyn-lease"``) and reached
through a DSN named by ``VOYN_LEASE_DSN``. This module only knows its argv
contract, inferred from every existing call site
(``acquire``/``install-hooks``/``release``/``list``), not its implementation.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

__all__ = ["LeaseIdentity", "lease_argv", "process_start", "run_lease"]


@dataclass(frozen=True, slots=True)
class LeaseIdentity:
    lease_tool: str
    repository: str
    owner: str
    session: str
    task: str


def process_start(pid: int | None = None) -> str:
    """Field 22 of ``/proc/<pid>/stat`` -- the same identity token the lease
    authority stores, immune to pid reuse (a recycled pid cannot inherit a
    dead process's lease). Falls back to ``"0"`` where unavailable; the
    lease tool still keys on pid+owner+session, which is enough for the
    single-writer guarantee even without this extra check."""
    target = os.getpid() if pid is None else pid
    try:
        with open(f"/proc/{target}/stat", encoding="ascii") as fh:
            return fh.read().split(") ", 1)[1].split()[19]
    except (OSError, IndexError):
        return "0"


def lease_argv(identity: LeaseIdentity, verb: str, repo_path: Path) -> list[str]:
    """The argv every ``voyn-lease`` invocation shares: ``--repo`` names the
    working directory the tool resolves to a repository (and, for
    ``install-hooks``, to that repository's common git dir -- shared by
    every worktree of the clone); the identity flags are what ``verify``
    checks a push's on-disk hook state against."""
    return [
        identity.lease_tool, "--repo", str(repo_path), verb,
        "--repository", identity.repository, "--owner", identity.owner,
        "--session", identity.session, "--task", identity.task,
        "--pid", str(os.getpid()), "--process-start", process_start(),
    ]


def run_lease(
    argv: list[str], cwd: Path, timeout: int = 120
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, check=False, timeout=timeout
    )
