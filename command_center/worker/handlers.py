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
- only the case where execution could not start (runner missing, OS error
  surfaced as status ``failed`` with no exit code and empty stdout) stays
  retryable: another host or a later moment can genuinely cure it.

Result rows are bounded: stdout/stderr travel as tails, because a jsonb
column is a coordination record, not a log store -- the full transcript
stays on the worker host's journal.
"""

from __future__ import annotations

import os
import re
import threading
from typing import Any

from command_center import agent_runner
from command_center.worker.daemon import Handler, HandlerOutcome
from command_center.worker.payloads import PayloadError, parse_agent_run
from command_center.worker.worktree_lease import blocking_lease
from command_center.orchestrator.publish import PublishConfig, publish_run

__all__ = ["build_handlers"]

_TAIL_CHARS = 4000

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

    # Single-writer gate at the dispatch boundary (part B of
    # VOYN-OPS-WORKER-DISPATCH-INTO-LEASED-WORKTREE). The git hooks enforce
    # the writer lease for commits made through them, but this handler wrote
    # into the routed checkout without ever asking who holds it -- so an agent
    # could edit files in a tree another writer owns, and the hook would only
    # notice at commit time, with the collision already on disk. Retryable:
    # a lease is a temporary claim, so redelivery lands once it is released,
    # bounded by the item's own max_attempts. Mutating types only -- a
    # read-only run gets the read-only sandbox profile and writes nothing.
    if task_type in agent_runner.MUTATING_TASK_TYPES:
        held = blocking_lease(repository)
        if held is not None:
            return HandlerOutcome(ok=False, reason=held, retryable=True)

    run = agent_runner.run_claude_code(
        repository_path=repository,
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
        return HandlerOutcome(
            ok=False,
            reason=_tail(run.stderr) or "runner failed to start",
            retryable=True,
        )
    # BO-S3b: a successful mutating run publishes its commits as a PR so the
    # autonomous loop closes without a human. Opt-in by env
    # (AICC_PUBLISH_DEPLOY_KEY); unset = local commit only, review fleets
    # unaffected. The lease inside publish_run enforces single-writer.
    deploy_key = os.environ.get("AICC_PUBLISH_DEPLOY_KEY", "")
    if task_type in agent_runner.MUTATING_TASK_TYPES and deploy_key:
        pub = publish_run(
            repository,
            PublishConfig(
                lease_tool=os.environ.get("VOYN_LEASE_TOOL", "voyn-lease"),
                repository=os.environ.get(
                    "VOYN_LEASE_REPOSITORY", request.project_id
                ),
                owner=os.environ.get("AICC_PUBLISH_OWNER", "server-worker"),
                session=os.environ.get("VOYN_LEASE_SESSION", "server-worker"),
                # The publish branch is `backlog/<task>` (publish.py) --
                # per-task, or every dispatch for this project collides on
                # one shared branch and a later force-push silently erases
                # an earlier task's still-unmerged work (VOYN-W0-AICC-
                # PUBLISH-BRANCH-COLLISION). Falls back to project_id only
                # for a payload enqueued before this field existed.
                task=request.backlog_task_id or request.project_id,
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
    return HandlerOutcome(ok=True, result=result)


def build_handlers() -> dict[str, Handler]:
    return {"agent_run": _run_agent}
