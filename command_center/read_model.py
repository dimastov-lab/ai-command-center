"""Canonical read-model: the single source of truth for the counts every
surface displays.

The audit (D5) found the dashboard, Kanban board and Execution Center each
aggregate the task list inline, so the same fact renders as different numbers on
adjacent widgets (tasks 174 vs 88 vs 116; Backlog 31 vs 61; Blocked 71 vs 86 vs
absent). This module computes those counts once, so every caller reads the same
numbers.

Pure and Streamlit-free (like `recommend.py`/`workflow.py`): it takes the task
list and returns an immutable snapshot. The load-bearing invariant is that every
task lands in exactly one bucket, so `sum(by_lane.values()) + other == total` and
no task can silently vanish from a board again (D2).
"""

from __future__ import annotations

from dataclasses import dataclass

# The canonical *read-side* lanes, in board order, plus `Blocked` as a
# first-class compatibility lens. The stored planning vocabulary deliberately
# remains the five `models.KANBAN_STATUSES`; `Blocked` is included here because
# legacy/live data already contains it and those tasks must be visible rather
# than silently dropped (audit D2). Any status outside this set is counted in
# `other`.
# `Closed` is likewise read-side only: operator-cancelled tasks retire there
# (AICC-IMP-006, `runtime.task_sync`) instead of masquerading as failures in
# the planning lanes.
CANONICAL_LANES: tuple[str, ...] = ("Backlog", "Next", "In Progress", "Review", "Blocked", "Done", "Closed")

# Lanes that represent in-flight or ready-to-start work — neither resolved
# (`Done`) nor stuck (`Blocked`).
ACTIVE_LANES: tuple[str, ...] = ("Backlog", "Next", "In Progress", "Review")


@dataclass(frozen=True)
class TaskSnapshot:
    """One reconciled view of the task list. `by_lane` covers every canonical
    lane (zero-filled); `other` holds any task whose status is outside the
    canonical vocabulary so the totals still add up."""

    total: int
    by_lane: dict[str, int]
    other: int
    done: int
    blocked: int
    active: int
    attention: int


def task_snapshot(tasks: list[dict]) -> TaskSnapshot:
    """Reduce the task list to the canonical counts. Every task is bucketed
    exactly once, so `sum(by_lane.values()) + other == total` always holds.

    `attention` counts tasks that need a human decision: an explicit
    `launch_status == "Requires Attention"` or a task flagged
    `regressed_after_done` (a resolved task whose completion later regressed —
    see `task_sync.flag_done_regression`). It is deliberately a task-level
    count, kept distinct from run-level failure counts so the two are never
    conflated under one ambiguous "attention" number (audit D5)."""
    by_lane = {lane: 0 for lane in CANONICAL_LANES}
    other = 0
    attention = 0
    for task in tasks:
        status = task.get("status")
        if status in by_lane:
            by_lane[status] += 1
        else:
            other += 1
        # A resolved (Done) task must not also be counted as attention on the
        # strength of a stale `launch_status` — that renders one task as both
        # Done and needing-attention on adjacent widgets (audit DATA-D4). A Done
        # task needs attention only via an explicit post-Done regression flag.
        # `Closed` is retired work (operator-cancelled, AICC-IMP-006) — like
        # `Done`, a stale launch_status on it must not resurrect attention.
        if task.get("regressed_after_done") or (
            status not in ("Done", "Closed")
            and task.get("launch_status") == "Requires Attention"
        ):
            attention += 1
    return TaskSnapshot(
        total=len(tasks),
        by_lane=by_lane,
        other=other,
        done=by_lane["Done"],
        blocked=by_lane["Blocked"],
        active=sum(by_lane[lane] for lane in ACTIVE_LANES),
        attention=attention,
    )


# --- Canonical RUN-level snapshot (audit D5) ---------------------------------
# The persisted `run.state` vocabulary (runtime/db.RUN_STATES), bucketed exactly
# as the Execution Strip established. Kept here so the strip banner, the AI-
# Supervisor caption and the top-bar glyph share ONE definition instead of each
# counting "runs needing attention" over a different set (24 / 141 / 109 under
# the same word "внимания"). Callers pass the *non-superseded* runs (a task's
# latest attempt only) so `attention` means "needs attention now", not a
# cumulative historical tally. COMPLETED/CANCELLED are terminal and surface in
# no bucket — the run snapshot is a "what is happening now" view.
RUN_LIVE_STATES: frozenset[str] = frozenset({"RUNNING"})
RUN_WAITING_STATES: frozenset[str] = frozenset({"PREPARED", "QUEUED"})
RUN_ATTENTION_STATES: frozenset[str] = frozenset({"FAILED", "INTERRUPTED", "UNKNOWN"})


@dataclass(frozen=True)
class RunSnapshot:
    """One reconciled view of the run list: how many are live, waiting, or need
    attention *right now*."""

    total: int
    running: int
    queued: int
    attention: int


def run_snapshot(runs: list[dict]) -> RunSnapshot:
    """Bucket raw run rows by their persisted `state`. Pass the non-superseded
    runs (see `ui.execution_strip`/`live_board.superseded_run_ids`) so a task
    that failed once and was retried is not double-counted as still needing
    attention."""
    return RunSnapshot(
        total=len(runs),
        running=sum(1 for r in runs if r.get("state") in RUN_LIVE_STATES),
        queued=sum(1 for r in runs if r.get("state") in RUN_WAITING_STATES),
        attention=sum(1 for r in runs if r.get("state") in RUN_ATTENTION_STATES),
    )


def superseded_run_ids(runs: list[dict]) -> frozenset[str]:
    """Ids of runs that are NOT their task's latest attempt. Pass these to filter
    a run list so a failed run a task has since retried is not double-counted as
    still needing attention (audit D5 — the same rule the Execution Strip and the
    AI-Supervisor caption use). Ad-hoc runs (no `task_id`) stand alone and are
    never superseded; ties on `started_at` break by `id` for determinism."""
    latest_by_task: dict[str, dict] = {}
    for run in runs:
        task_id = run.get("task_id")
        if not task_id:
            continue
        current = latest_by_task.get(task_id)
        key = (run.get("started_at") or "", run.get("id") or "")
        if current is None or key > (current.get("started_at") or "", current.get("id") or ""):
            latest_by_task[task_id] = run
    latest_ids = {r.get("id") for r in latest_by_task.values()}
    return frozenset(r.get("id") for r in runs if r.get("task_id") and r.get("id") not in latest_ids)
