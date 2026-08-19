"""The planner tick (BO-S2): eligible tasks -> atomic dispatches -> a report.

One tick, no loop: the schedule is a systemd oneshot timer
(deploy/systemd/aicc-backlog-planner.timer), the reaper's pattern — a missed
tick delays planning and never corrupts it, because every mutating step is
one call to ``backlog_dispatch`` (0006), which is atomic or refused.

Single planner, machine-held: the tick first takes the ``planner:global``
lease. A second control host running the same timer gets ``planner_busy``
and an empty report — not a second writer.

The report is the owner's answer to "why is my task waiting": every
non-dispatched candidate lands in exactly one bucket with the dispatch
function's own refusal reason, including ``skipped_by_wave_gate``
(approved decision 1) so the UI can say "wave N is still working".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from command_center.orchestrator.routing import cascade_for
from command_center.worker.payloads import AGENT_RUN_SCHEMA_VERSION

__all__ = ["PlanLimits", "PlanReport", "plan_once"]

_PLANNER_AUTHORITY = "planner:global"


@dataclass(frozen=True, slots=True)
class PlanLimits:
    planner: str = "aicc-planner"
    wip_limit: int = 4
    lease_ttl_seconds: int = 7200
    #: Per-tick dispatch cap, distinct from WIP: one tick must stay short.
    max_dispatches_per_tick: int = 4
    timeout_seconds: int = 900


@dataclass(slots=True)
class PlanReport:
    dispatched: list[tuple[str, str]] = field(default_factory=list)  # (task, work_item)
    skipped_by_wave_gate: list[tuple[str, str]] = field(default_factory=list)
    refused: list[tuple[str, str]] = field(default_factory=list)
    undispatchable: list[tuple[str, str]] = field(default_factory=list)
    released: list[tuple[str, str]] = field(default_factory=list)  # (task, queue_state)
    planner_busy: bool = False


def _payload_for(
    task: dict[str, Any], limits: PlanLimits
) -> tuple[dict[str, Any], int]:
    """The agent_run payload plus the attempt budget (= cascade length).

    Prompt discipline: the task record IS the assignment — id, title, body
    travel verbatim; the worker's provenance gate still applies, and
    ``untrusted=False`` is on the authority of the planner being the control
    plane acting on the canonical store.
    """
    cascade = cascade_for("implementation")
    prompt = (
        f"Central task: {task['task_id']} ({task['title']}).\n"
        f"Wave {task['wave']}, priority {task['priority'] or 'unset'}.\n\n"
        f"{task['body']}".strip()
    )
    payload = {
        "kind": "agent_run",
        "v": AGENT_RUN_SCHEMA_VERSION,
        "project_id": task["task_id"],
        "repository_path": task["repo"],
        "prompt": prompt,
        "task_type": cascade[0]["task_type"],
        "timeout_seconds": limits.timeout_seconds,
        "untrusted": False,
        "cascade": cascade,
    }
    return payload, len(cascade)


class Planner:
    """Owns nothing but the composition; every decision is the database's."""

    def __init__(self, connection_factory: Any) -> None:
        self._factory = connection_factory

    def _rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[tuple]:
        with self._factory() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall()

    def _row(self, sql: str, params: tuple[Any, ...]) -> tuple:
        return self._rows(sql, params)[0]

    def plan_once(self, limits: PlanLimits = PlanLimits()) -> PlanReport:
        report = PlanReport()
        ok, reason, *_ = self._row(
            "SELECT * FROM backlog_lease_acquire(%s, %s, %s)",
            (_PLANNER_AUTHORITY, limits.planner, max(limits.lease_ttl_seconds, 60)),
        )
        if not ok:
            report.planner_busy = True
            return report
        try:
            # Free finished lanes first, so this very tick can refill them.
            for task_id, queue_state, _action in self._rows(
                "SELECT * FROM backlog_release_terminal(%s)", (limits.planner,)
            ):
                report.released.append((task_id, queue_state))

            candidates = self._rows(
                "SELECT task_id, wave, priority, title, body, repo, dispatchable "
                "FROM backlog_eligible"
            )
            for task_id, wave, priority, title, body, repo, dispatchable in candidates:
                if len(report.dispatched) >= limits.max_dispatches_per_tick:
                    break
                task = {
                    "task_id": task_id,
                    "wave": wave,
                    "priority": priority,
                    "title": title,
                    "body": body,
                    "repo": repo,
                }
                if not dispatchable:
                    report.undispatchable.append((task_id, "no_repo"))
                    continue
                payload, budget = _payload_for(task, limits)
                ok, reason, work_item_id, _revision = self._row(
                    "SELECT * FROM backlog_dispatch(%s, %s, %s, %s, %s::jsonb, %s)",
                    (
                        task_id,
                        limits.planner,
                        limits.lease_ttl_seconds,
                        limits.wip_limit,
                        json.dumps(payload),
                        budget,
                    ),
                )
                if ok:
                    report.dispatched.append((task_id, work_item_id))
                elif reason == "earlier_wave_has_eligible_work":
                    report.skipped_by_wave_gate.append((task_id, reason))
                elif reason == "wip_exhausted":
                    report.refused.append((task_id, reason))
                    break  # the cap is global; later candidates cannot pass
                else:
                    report.refused.append((task_id, reason))
        finally:
            # The global lease is per TICK; repo leases outlive it by design.
            self._row(
                "SELECT * FROM backlog_lease_release(%s, %s)",
                (_PLANNER_AUTHORITY, limits.planner),
            )
        return report


def plan_once(connection_factory: Any, limits: PlanLimits = PlanLimits()) -> PlanReport:
    return Planner(connection_factory).plan_once(limits)
