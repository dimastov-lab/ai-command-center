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
CANONICAL_LANES: tuple[str, ...] = ("Backlog", "Next", "In Progress", "Review", "Blocked", "Done")

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
        if task.get("launch_status") == "Requires Attention" or task.get("regressed_after_done"):
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
