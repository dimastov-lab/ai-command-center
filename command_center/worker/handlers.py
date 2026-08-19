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

import threading
from typing import Any

from command_center import agent_runner
from command_center.worker.daemon import Handler, HandlerOutcome
from command_center.worker.payloads import PayloadError, parse_agent_run

__all__ = ["build_handlers"]

_TAIL_CHARS = 4000


def _tail(text: str) -> str:
    return text[-_TAIL_CHARS:] if len(text) > _TAIL_CHARS else text


def _run_agent(payload: dict[str, Any], lease_lost: threading.Event) -> HandlerOutcome:
    request = parse_agent_run(payload)
    if isinstance(request, PayloadError):
        return HandlerOutcome(ok=False, reason=request.reason, retryable=request.retryable)

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
        return HandlerOutcome(ok=False, reason=f"claude cli unavailable: {detail}", retryable=True)

    if lease_lost.is_set():
        # The lease died while we validated; starting a mutating run now
        # would produce effects no attempt row accounts for.
        return HandlerOutcome(ok=False, reason="lease lost before execution started", retryable=True)

    # Provenance gate (audit D7, applied at the queue boundary): an untrusted
    # payload asking for a *mutating* task type is refused outright rather
    # than silently downgraded. profile_for_task would downgrade it to the
    # read-only profile -- but running a mutating prompt read-only produces a
    # half-executed task that looks completed, and masquerading the task type
    # to force the downgrade would lie to the audit trail. Non-retryable:
    # redelivery cannot add the operator elevation; the control plane
    # re-enqueues with untrusted=false after review.
    if request.untrusted and request.task_type in agent_runner.MUTATING_TASK_TYPES:
        return HandlerOutcome(
            ok=False,
            reason=(
                f"untrusted payload requests mutating task_type "
                f"{request.task_type!r}; operator elevation required"
            ),
            retryable=False,
        )

    run = agent_runner.run_claude_code(
        repository_path=repository,
        prompt=request.prompt,
        task_type=request.task_type,
        timeout_seconds=request.timeout_seconds,
        model=request.model,
    )

    result = {
        "status": run.status,
        "exit_code": run.exit_code,
        "duration_seconds": round(run.duration_seconds, 3),
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "stdout_tail": _tail(run.stdout),
        "stderr_tail": _tail(run.stderr),
        "result_text": _tail(agent_runner.extract_result_text(run.stdout)),
    }
    if run.status == "failed" and run.exit_code is None and not run.stdout:
        # The process never started (OSError path in the runner): nothing
        # executed, so redelivery is safe and may land on a healthier host.
        return HandlerOutcome(ok=False, reason=_tail(run.stderr) or "runner failed to start", retryable=True)
    return HandlerOutcome(ok=True, result=result)


def build_handlers() -> dict[str, Handler]:
    return {"agent_run": _run_agent}
