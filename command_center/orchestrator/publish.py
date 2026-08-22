"""Publish a completed mutating run as a pull request (BO-S3b, part 1).

The worker executes ``agent_run`` locally; its commits sit in the host clone
and go nowhere until this module publishes them. Publishing is gated by the
same single-writer invariant the pre-push hook enforces: a branch is pushed
only while this process holds the repository's writer lease, acquired through
the very tool the hook verifies (``voyn-lease``) — never bypassed with
``--no-verify``. The deploy key (added to GitHub with write access) is the
push credential; ``gh`` opens the PR carrying the ``HEAD_SHA:`` trailer that
result-ingest already parses.

Every outcome is data (a ``PublishResult``); this never raises into the
worker loop. A run that produced no commit is reported as ``nothing_to_publish``,
not an error — a review/analysis task legitimately changes no files.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

__all__ = ["PublishConfig", "PublishResult", "publish_run"]


@dataclass(frozen=True, slots=True)
class PublishConfig:
    lease_tool: str  # path to voyn-lease
    repository: str  # e.g. "ai-command-center"
    owner: str  # writer identity, e.g. "server-worker"
    session: str
    task: str  # the backlog task id
    deploy_key: str  # path to the per-repo deploy private key
    base: str = "main"
    ttl: int = 600


@dataclass(frozen=True, slots=True)
class PublishResult:
    ok: bool
    branch: str | None = None
    head_sha: str | None = None
    pr_url: str | None = None
    reason: str = ""


def _run(argv: list[str], cwd: Path, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    import os

    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, check=False, env=env, timeout=120
    )


def _lease_argv(cfg: PublishConfig, verb: str, repo_path: Path) -> list[str]:
    import os

    return [
        cfg.lease_tool, "--repo", str(repo_path), verb,
        "--repository", cfg.repository, "--owner", cfg.owner,
        "--session", cfg.session, "--task", cfg.task,
        "--pid", str(os.getpid()), "--process-start", _process_start(),
    ]


def _https_push_target(repo_path: Path) -> str | None:
    """The repo's own `origin`, rewritten to HTTPS when it is an
    `git@github.com:` SSH remote — pushed through the host's global `gh`
    credential helper (`gh auth setup-git`, already configured on every
    worker for `gh pr create`/`gh pr view` below) instead of a per-repo
    deploy key. Deploy keys proved unreliable in practice on 2026-08-21:
    GitHub silently blocks them for private repos on this org's Free plan,
    and one denied write even on a *public* repo despite a verified,
    correctly-registered, non-read-only key — with no actionable diagnostic
    on either side. `gh`'s own OAuth credential is what every manual
    recovery this session ultimately fell back to, so it becomes the
    primary path here rather than the last resort."""
    remote = _run(["git", "remote", "get-url", "origin"], repo_path)
    if remote.returncode != 0:
        return None
    url = remote.stdout.strip()
    prefix = "git@github.com:"
    if url.startswith(prefix):
        return "https://github.com/" + url[len(prefix):]
    if url.startswith("https://github.com/"):
        return url
    return None


def _process_start() -> str:
    # The lease binds ownership to (pid, process-start) so a recycled pid
    # cannot inherit a dead process's lease. Read this process's start from
    # /proc; falls back to "0" where unavailable (the lease tool still keys
    # on pid+owner+session, which is enough for the single-writer guarantee).
    try:
        with open(f"/proc/{__import__('os').getpid()}/stat", encoding="ascii") as fh:
            return fh.read().split(") ", 1)[1].split()[19]
    except (OSError, IndexError):
        return "0"


def publish_run(repo_path: Path, cfg: PublishConfig) -> PublishResult:
    """Acquire the lease, push a branch, open a PR. Idempotent on the branch
    name (``backlog/<task>``): a re-run force-updates the same branch and
    reuses the open PR, so a redelivered attempt does not fan out PRs."""
    head = _run(["git", "rev-parse", "HEAD"], repo_path)
    if head.returncode != 0:
        return PublishResult(ok=False, reason="cannot read HEAD")
    head_sha = head.stdout.strip()

    base_sha = _run(["git", "rev-parse", f"origin/{cfg.base}"], repo_path)
    if base_sha.returncode == 0 and base_sha.stdout.strip() == head_sha:
        return PublishResult(ok=False, reason="nothing_to_publish", head_sha=head_sha)

    branch = f"backlog/{cfg.task}"
    lease = _run(_lease_argv(cfg, "acquire", repo_path), repo_path)
    if lease.returncode != 0:
        # The lease is held by another writer: a data refusal, the attempt
        # returns to the pool and a later tick retries — never a forced push.
        return PublishResult(ok=False, reason=f"lease_unavailable: {lease.stderr.strip()[:120]}")
    # Live-reproduced 2026-08-21: `install-hooks` is what writes the
    # pre-push hook's `voyn-lease.env` (repository/owner/session/task/pid/
    # process-start) -- and it had only ever been run once, at whatever
    # moment the hooks were first provisioned on this host. The pre-push
    # hook reads that FROZEN file on every push and compares it against the
    # lease row THIS `acquire` just wrote fresh, so `verify` refused every
    # push with `VOYN_LEASE_REFUSED verify mismatch` once the identity that
    # provisioned the hooks (an old, long-dead process) no longer matched
    # anything this or any later worker process would ever present. Calling
    # `install-hooks` with the exact same identity args as the `acquire`
    # that just succeeded keeps the hook's on-disk copy of "who currently
    # holds this lease" in lockstep with the database row `verify` actually
    # checks against -- every acquire re-provisions it, not just the first
    # one ever run on a host. Failing this fails closed (release, refuse to
    # push) rather than attempting a push `verify` is already known to
    # reject with this stale a file.
    hooks = _run(_lease_argv(cfg, "install-hooks", repo_path), repo_path)
    if hooks.returncode != 0:
        _run(_lease_argv(cfg, "release", repo_path), repo_path)
        return PublishResult(
            ok=False, reason=f"install_hooks_failed: {hooks.stderr.strip()[:120]}"
        )
    try:
        https_target = _https_push_target(repo_path)
        if https_target is not None:
            push = _run(
                ["git", "push", "--force-with-lease", https_target,
                 f"HEAD:refs/heads/{branch}"],
                repo_path,
            )
        else:
            # origin isn't a github.com remote this host knows how to
            # rewrite to HTTPS -- fall back to the configured deploy key.
            git_ssh = f"ssh -i {cfg.deploy_key} -o IdentitiesOnly=yes"
            push = _run(
                ["git", "push", "--force-with-lease", "origin", f"HEAD:refs/heads/{branch}"],
                repo_path, {"GIT_SSH_COMMAND": git_ssh},
            )
        if push.returncode != 0:
            return PublishResult(ok=False, reason=f"push_failed: {push.stderr.strip()[:160]}")
    finally:
        _run(_lease_argv(cfg, "release", repo_path), repo_path)

    body = (
        f"Autonomous delivery of {cfg.task}.\n\n"
        f"HEAD_SHA: {head_sha}\n"
    )
    existing = _run(
        ["gh", "pr", "view", branch, "--json", "url", "-q", ".url"], repo_path
    )
    if existing.returncode == 0 and existing.stdout.strip():
        return PublishResult(ok=True, branch=branch, head_sha=head_sha, pr_url=existing.stdout.strip())
    created = _run(
        ["gh", "pr", "create", "--base", cfg.base, "--head", branch,
         "--title", f"{cfg.task}: autonomous delivery", "--body", body],
        repo_path,
    )
    if created.returncode != 0:
        return PublishResult(
            ok=False, branch=branch, head_sha=head_sha,
            reason=f"pr_create_failed: {created.stderr.strip()[:160]}",
        )
    return PublishResult(ok=True, branch=branch, head_sha=head_sha, pr_url=created.stdout.strip())
