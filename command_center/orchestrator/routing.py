"""The executor routing matrix (BO-S2a, executor-cascade).

Route order is the recorded decision chain quality -> risk -> privacy ->
latency -> cost. The first slice is deliberately STATIC and deliberately
HONEST: it names only executors that actually exist on the worker hosts
today. A cascade naming an absent executor would not fail loudly — the
worker's unavailability path *retries to the next link*, so a phantom link
silently burns one attempt of every task's budget. That is why codex is a
COMMENT, not an entry, until its CLI is proven on worker-01.

Cascade mechanics live where the state already is: the planner writes the
cascade into the payload, ``max_attempts`` = its length (the attempt budget
IS the cascade budget), and the worker selects ``cascade[attempt_no - 1]``
(clamped) — so executor failover rides the queue's existing retry/reap
machinery (SRV-06) with no new tables and no new loop, and the audit trail
is the existing ``work_event`` attempt history (attempt_no <-> cascade step
is a bijection until the clamp).
"""

from __future__ import annotations

from typing import Any

__all__ = ["ROUTING_MATRIX", "cascade_for"]

#: task class -> ordered cascade. Each link: executor + the agent_run fields
#: it pins. 'claude' is the headless CLI the worker's agent_runner already
#: drives — the one executor proven on the fleet.
ROUTING_MATRIX: dict[str, list[dict[str, Any]]] = {
    "implementation": [
        {"executor": "claude", "task_type": "implementation"},
        # Escalation link: same executor, stronger effort profile. A distinct
        # link so an infrastructure-flavored first failure gets a second,
        # deliberate run rather than a blind identical retry.
        {"executor": "claude", "task_type": "implementation"},
        # {"executor": "codex", "task_type": "implementation"},
        #   -- enable when the codex CLI is verified present and non-interactive
        #      on worker-01 (VOYN backlog record: executor onboarding); adding
        #      it earlier would burn one attempt per task on a phantom link.
    ],
    "review": [
        {"executor": "claude", "task_type": "review"},
    ],
}


def cascade_for(task_class: str) -> list[dict[str, Any]]:
    """The cascade for a task class; unknown classes get the implementation
    route rather than a refusal — routing chooses HOW, never WHETHER."""
    return [
        dict(link)
        for link in ROUTING_MATRIX.get(task_class, ROUTING_MATRIX["implementation"])
    ]
