"""The payload -> execution bridge (VOYN-W0-AICC-SRV-05, slice 2).

``agent_run`` is executed through the *existing* runner
(``command_center.agent_runner.run_claude_code``) rather than a new one:
that module already owns the sandbox-profile decision (audit D7 --
provenance-aware downgrade to read-only), VCS-credential scrubbing, timeout
handling and the never-raises result shape. This bridge adds only what the
queue context requires -- payload validation, repository validation, and
folding the run into a ``HandlerOutcome`` -- and deliberately owns nothing
the runner already decides.

Outcome discipline:

- a payload defect (bad version, missing fields, unknown repository) is
  **non-retryable**: redelivery cannot repair data;
- a run that *executed* is ``ok`` regardless of the agent's own exit --
  "the agent failed the task" is a result for the control plane to read,
  not a queue-level failure to redeliver, and retrying a completed mutating
  run would re-apply its side effects;
- only the case where execution could not start stays retryable: another
  host or a later moment can genuinely cure it. That covers both the runner
  never launching the process (OS error surfaced as status ``failed`` with
  no exit code and empty stdout) *and* the CLI process launching but the
  Claude-CLI account itself failing before any task work happened (rate
  limit / auth / overload -- the CLI's own structured ``is_error`` +
  ``api_error_status``/``terminal_reason`` fields, see
  ``agent_runner.RunResult.is_executor_api_error``). Both are executor
  infrastructure failures, not task outcomes, however different their raw
  ``exit_code``/``stdout`` shape looks.

Result rows are bounded: stdout/stderr travel as tails, because a jsonb
column is a coordination record, not a log store -- the full transcript
stays on the worker host's journal.
"""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Any

from command_center import agent_runner, project_config, workspace_provisioning
from command_center.worker.daemon import Handler, HandlerOutcome
from command_center.worker.payloads import PayloadError, parse_agent_run
from command_center.worker.worktree_lease import blocking_lease
from command_center.orchestrator.publish import PublishConfig, publish_run

__all__ = ["build_handlers"]

_TAIL_CHARS = 4000

# Sibling worktree directory, one per branch — the SAME convention
# `task_pipeline.derive_worktree_path` uses for the desktop launch path
# (`<repo>-worktrees/<branch-slug>`, outside the repository so a worktree
# never appears as untracked noise inside it). Kept as a private four-line
# duplicate here rather than an import: task_pipeline.py is the ~2400-line
# desktop pipeline module, and pulling it into the worker's dispatch path for
# one pure path-formatting function would wire an unrelated UI-facing module
# into a security-sensitive hot path. Matching the string convention (not
# importing the code) is what matters — it means a task's worker-provisioned
# worktree and its desktop-pipeline worktree for the same branch resolve to
# the identical path, so `blocking_lease` still detects a collision between
# the two launch paths. If a third caller needs this convention, extract it
# into `workspace_provisioning` then — not before.
_WORKTREE_PARENT_SUFFIX = "-worktrees"

# VOYN-W0-AICC-ISOLATED-WORKTREE-PER-ATTEMPT naming decision: keyed by
# BRANCH (`backlog/<task>`), not by attempt. The payload contract
# (`worker.payloads.AgentRunRequest`) carries no `attempt_id`/`head_sha` --
# the only per-delivery identifier available at this call site is the
# queue's own `attempt_no` (a Handler parameter, not payload data). Keying by
# attempt_no instead of branch was rejected: git allows exactly one worktree
# per branch (`provision_workspace`'s `no_conflicting_worktree` gate), and
# `publish_run` always pushes to `backlog/<task>` regardless of which attempt
# produced the commit -- a worktree per attempt_no would either collide with
# that gate the moment two attempts of the same task existed, or require a
# second, attempt-scoped branch that `publish_run` does not know about. A
# worktree per TASK is the isolation the audit actually needs: the defect was
# different tasks/branches sharing ONE checkout, not one task's own retries
# sharing it with each other -- those retries sharing a worktree is
# correct, since they are building toward the same branch and the same PR.
_provision_locks: dict[str, threading.Lock] = {}
_provision_locks_guard = threading.Lock()


def _isolated_workspace_path(repository: Path, branch: str) -> Path:
    slug = branch.strip().strip("/").replace("/", "-") or "work"
    return repository.parent / f"{repository.name}{_WORKTREE_PARENT_SUFFIX}" / slug


def _provision_lock(key: str) -> threading.Lock:
    """One lock per resolved workspace path, so two in-process deliveries
    that both compute the same new path cannot both pass
    `provision_workspace`'s `workspace.exists()` check before either has
    created it (`provision_workspace` is check-then-act, not itself atomic
    against a concurrent identical call).

    This closes the race for THIS process only. The daemon's own claim loop
    (`worker.daemon.WorkerDaemon.run_forever`) dispatches one item at a time,
    so within a single worker process two deliveries never overlap in the
    first place; this lock is defense-in-depth for a future multi-threaded
    dispatcher, and for tests that call the handler directly from several
    threads. A concurrent SECOND PROCESS (this host or another) redelivering
    the SAME task while a first attempt is still running is a different,
    known gap: nothing here acquires a held lease for the duration of the
    run (only `blocking_lease` preflights, and `publish_run` holds one only
    around its own push) -- closing that is VOYN-W0-AICC-LEASE-FULL-
    LIFECYCLE, explicitly out of scope for this change."""
    with _provision_locks_guard:
        lock = _provision_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _provision_locks[key] = lock
        return lock

#: BO-S3 result enrichment: the delivery contract for machine-readable
#: outcomes. A PR reference is extracted by its exact URL shape — nothing
#: else on GitHub looks like it — and the head SHA ONLY from an explicit
#: labelled trailer line (`HEAD_SHA: <hex>`), because a bare 40-hex string in
#: a transcript is any object id at all and guessing is what the
#: no-substring rule forbids. The planner's prompt asks the executor for the
#: trailer; an executor that omits it simply yields a result without a sha,
#: and the backlog's DONE gate holds until the fact arrives another way.
_PR_URL = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")
_HEAD_SHA_TRAILER = re.compile(r"^HEAD_SHA:\s*([0-9a-f]{7,40})\s*$", re.MULTILINE)


def _machine_outcome(result_text: str) -> dict[str, str | None]:
    pr_match = _PR_URL.search(result_text)
    sha_match = _HEAD_SHA_TRAILER.search(result_text)
    return {
        "pr_url": pr_match.group(0) if pr_match else None,
        "head_sha": sha_match.group(1) if sha_match else None,
    }


def _tail(text: str) -> str:
    return text[-_TAIL_CHARS:] if len(text) > _TAIL_CHARS else text


def _cascade_link(request, attempt_no: int) -> dict[str, Any] | None:
    """BO-S2a: the cascade link for this delivery, selected by the queue's own
    attempt number — no new state, so failover rides the existing retry/reap
    machinery. Clamped at the tail: once the cascade is exhausted the last
    link keeps serving until the attempt budget (its length) dead-letters."""
    if not request.cascade:
        return None
    return request.cascade[min(attempt_no, len(request.cascade)) - 1]


def _run_agent(
    payload: dict[str, Any], lease_lost: threading.Event, attempt_no: int = 1
) -> HandlerOutcome:
    request = parse_agent_run(payload)
    if isinstance(request, PayloadError):
        return HandlerOutcome(
            ok=False, reason=request.reason, retryable=request.retryable
        )

    link = _cascade_link(request, attempt_no)
    task_type = request.task_type
    model = request.model
    if link is not None:
        executor = str(link.get("executor"))
        if executor != "claude":
            # Executor unavailability is a ROUTING signal, not a task error
            # (approved BO-S2a decision): a retryable refusal returns the
            # attempt to the pool and the next delivery selects the next
            # link. An unknown name on the LAST link exhausts the budget
            # into the dead letter, where the reason names the route.
            return HandlerOutcome(
                ok=False,
                reason=f"executor_unavailable: {executor!r} (cascade step {attempt_no})",
                retryable=True,
            )
        task_type = str(link.get("task_type", task_type))
        model_override = link.get("model")
        if isinstance(model_override, str) and model_override.strip():
            model = model_override

    try:
        repository = agent_runner.validate_repository(
            request.project_id, request.repository_path
        )
    except agent_runner.RunnerError as exc:
        # A repository this host cannot see may exist on another: the row is
        # host-local state, so let redelivery try elsewhere -- bounded by the
        # item's own max_attempts.
        return HandlerOutcome(ok=False, reason=str(exc), retryable=True)

    available, detail = agent_runner.claude_cli_preflight()
    if not available:
        return HandlerOutcome(
            ok=False, reason=f"claude cli unavailable: {detail}", retryable=True
        )

    if lease_lost.is_set():
        # The lease died while we validated; starting a mutating run now
        # would produce effects no attempt row accounts for.
        return HandlerOutcome(
            ok=False, reason="lease lost before execution started", retryable=True
        )

    # Provenance gate (audit D7, applied at the queue boundary): an untrusted
    # payload asking for a *mutating* task type is refused outright rather
    # than silently downgraded. profile_for_task would downgrade it to the
    # read-only profile -- but running a mutating prompt read-only produces a
    # half-executed task that looks completed, and masquerading the task type
    # to force the downgrade would lie to the audit trail. Non-retryable:
    # redelivery cannot add the operator elevation; the control plane
    # re-enqueues with untrusted=false after review.
    if request.untrusted and task_type in agent_runner.MUTATING_TASK_TYPES:
        return HandlerOutcome(
            ok=False,
            reason=(
                f"untrusted payload requests mutating task_type "
                f"{task_type!r}; operator elevation required"
            ),
            retryable=False,
        )

    # The publish branch is `backlog/<task>` (publish.py) -- per-task, or
    # every dispatch for this project collides on one shared branch and a
    # later force-push silently erases an earlier task's still-unmerged work
    # (VOYN-W0-AICC-PUBLISH-BRANCH-COLLISION). Falls back to project_id only
    # for a payload enqueued before this field existed. Computed once and
    # reused for both the isolation branch below and PublishConfig.task, so
    # the worktree a run executes in and the branch it publishes to can never
    # drift apart.
    backlog_task = request.backlog_task_id or request.project_id

    # Isolated workspace per mutating task (VOYN-W0-AICC-ISOLATED-WORKTREE-
    # PER-ATTEMPT, a P0 audit finding: every mutating run for a project
    # shared ONE checkout -- `repository` -- with no isolation at all, so two
    # concurrently-dispatched tasks for the same project could corrupt each
    # other's working tree. `run_repository` is what actually gets handed to
    # the CLI and to `publish_run` below; for a read-only task it stays
    # `repository` unchanged (no isolation requirement, and no worktree churn
    # for the common read-only case). See `_isolated_workspace_path`'s
    # comment for the branch-not-attempt naming decision.
    run_repository = repository
    isolated_workspace: Path | None = None

    if task_type in agent_runner.MUTATING_TASK_TYPES:
        expected_branch = f"backlog/{backlog_task}"
        isolated_workspace = _isolated_workspace_path(repository, expected_branch)

        # Single-writer gate at the dispatch boundary (part B of
        # VOYN-OPS-WORKER-DISPATCH-INTO-LEASED-WORKTREE), now checked against
        # the ISOLATED workspace -- the directory this dispatch is actually
        # about to write into -- rather than the shared `repository`. Checked
        # before provisioning: the path is computed deterministically above
        # with no filesystem access, so this can run whether or not the
        # worktree exists yet. Retryable: a lease is a temporary claim, so
        # redelivery lands once it is released, bounded by the item's own
        # max_attempts.
        held = blocking_lease(isolated_workspace)
        if held is not None:
            return HandlerOutcome(ok=False, reason=held, retryable=True)

        base_branch = (
            project_config.get_project_config(request.project_id).get("default_branch")
            or "main"
        )
        spec = workspace_provisioning.WorkspaceSpec(
            workspace_path=str(isolated_workspace),
            expected_branch=expected_branch,
            base_branch=base_branch,
            repository_path=str(repository),
            task_type=task_type,
        )
        try:
            # Locked per-path: `provision_workspace` is check-then-act
            # (`workspace.exists()` then `git worktree add`), not itself safe
            # against two in-process callers racing to create the same new
            # path. See `_provision_lock`'s docstring for what this does and
            # does not cover.
            with _provision_lock(str(isolated_workspace)):
                evidence = workspace_provisioning.provision_and_verify(spec)
        except workspace_provisioning.WorkspaceVerificationError as exc:
            # A verification failure here (branch already checked out
            # elsewhere, base branch missing, dirty leftover worktree under a
            # stricter policy, ...) is a fact about repository state that a
            # later moment can genuinely cure -- redelivery retries once
            # whatever blocked it clears, bounded by max_attempts.
            return HandlerOutcome(
                ok=False,
                reason=f"workspace isolation failed at {exc.failed_step}: {exc.detail}",
                retryable=True,
            )
        run_repository = Path(evidence.workspace_path)

    run = agent_runner.run_claude_code(
        repository_path=run_repository,
        prompt=request.prompt,
        task_type=task_type,
        timeout_seconds=request.timeout_seconds,
        model=model,
    )

    result_text = agent_runner.extract_result_text(run.stdout)
    result = {
        "cascade_step": attempt_no if link is not None else None,
        "executor": (link or {}).get("executor", "claude"),
        **_machine_outcome(result_text),
        "status": run.status,
        "exit_code": run.exit_code,
        "duration_seconds": round(run.duration_seconds, 3),
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "stdout_tail": _tail(run.stdout),
        "stderr_tail": _tail(run.stderr),
        "result_text": _tail(result_text),
    }
    if run.status == "failed" and run.exit_code is None and not run.stdout:
        # The process never started (OSError path in the runner): nothing
        # executed, so redelivery is safe and may land on a healthier host.
        # The worktree (if any) holds no run output either -- safe to remove
        # immediately rather than leave an empty one behind for every failed
        # launch attempt.
        if isolated_workspace is not None:
            workspace_provisioning.remove_workspace(isolated_workspace, repository)
        return HandlerOutcome(
            ok=False,
            reason=_tail(run.stderr) or "runner failed to start",
            retryable=True,
        )
    if run.is_executor_api_error:
        # Incident 2026-08-21 16:09 UTC: the CLI process itself started (exit
        # code 1, non-empty stdout) but the *account*, not the task, failed --
        # a rate limit / auth / overload response the CLI reports through its
        # own structured `is_error`+`api_error_status`/`terminal_reason`
        # fields (see `RunResult.is_executor_api_error`), never by executing
        # any task work. That is indistinguishable from the "process never
        # started" case above in every way that matters here: nothing ran,
        # so redelivery is safe and may land on a healthier host/account, and
        # the worktree (if any) holds no run output worth preserving.
        if isolated_workspace is not None:
            workspace_provisioning.remove_workspace(isolated_workspace, repository)
        return HandlerOutcome(
            ok=False,
            reason=f"executor infrastructure failure (api_error_status present in CLI output): {_tail(result_text)}",
            retryable=True,
        )
    # BO-S3b: a successful mutating run publishes its commits as a PR so the
    # autonomous loop closes without a human. Opt-in by env
    # (AICC_PUBLISH_DEPLOY_KEY); unset = local commit only, review fleets
    # unaffected. The lease inside publish_run enforces single-writer.
    deploy_key = os.environ.get("AICC_PUBLISH_DEPLOY_KEY", "")
    if task_type in agent_runner.MUTATING_TASK_TYPES and deploy_key:
        pub = publish_run(
            run_repository,
            PublishConfig(
                lease_tool=os.environ.get("VOYN_LEASE_TOOL", "voyn-lease"),
                repository=os.environ.get(
                    "VOYN_LEASE_REPOSITORY", request.project_id
                ),
                owner=os.environ.get("AICC_PUBLISH_OWNER", "server-worker"),
                session=os.environ.get("VOYN_LEASE_SESSION", "server-worker"),
                task=backlog_task,
                deploy_key=deploy_key,
            ),
        )
        result["publish"] = {
            "ok": pub.ok,
            "branch": pub.branch,
            "pr_url": pub.pr_url,
            "reason": pub.reason,
        }
        if pub.pr_url:
            result["pr_url"] = pub.pr_url
        # Cleanup is conditioned on the branch actually being durable
        # somewhere else first. `pub.ok` (a real push -- the commit now lives
        # on `origin/backlog/<task>`) and `reason == "nothing_to_publish"`
        # (publish.py's own `ok=False` -- HEAD already equals the base
        # branch, so the run made no commit at all) both mean there is
        # nothing local left to lose; in either case the worktree is
        # disposable. Any OTHER failure (lease_unavailable / push_failed /
        # pr_create_failed / cannot read HEAD) deliberately leaves the
        # worktree in place: it may be the only remaining copy of whatever
        # the agent committed, and deleting it on a transient publish
        # failure would be real, unrecoverable data loss for the sake of
        # tidiness. "Recovery-safe" here means recoverable, not merely
        # crash-safe -- the next attempt's `provision_workspace` simply
        # reuses ("reused") the still-present worktree.
        publish_left_nothing_local = pub.ok or pub.reason == "nothing_to_publish"
        if isolated_workspace is not None and publish_left_nothing_local:
            workspace_provisioning.remove_workspace(isolated_workspace, repository)
    elif isolated_workspace is not None:
        # Publishing is disabled for this deployment (no
        # AICC_PUBLISH_DEPLOY_KEY): the worktree's local commits are the ONLY
        # record of this run's work, exactly as the shared checkout's commits
        # were before this change. Removing it would silently discard a
        # completed mutating run's entire output, so local-only mode never
        # cleans up -- the worktree accumulates the same way the shared
        # checkout used to, and an operator enabling publishing later can
        # still recover it.
        pass
    return HandlerOutcome(ok=True, result=result)


def build_handlers() -> dict[str, Handler]:
    return {"agent_run": _run_agent}
