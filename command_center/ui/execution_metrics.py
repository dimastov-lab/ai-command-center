"""Canonical, pure counters for execution UI surfaces.

Every visible execution count must describe the same snapshot with the same
rules: only the latest attempt of a task is actionable, completed Kanban tasks
do not remain in attention, dismissed attention rows do not remain in the
headline, and the waiting total includes both queued tasks and the brief
run-level PREPARED/QUEUED states.
"""

from __future__ import annotations

from dataclasses import dataclass

from command_center import execution_queue
from command_center.runtime import session_view
from command_center.ui import live_board


@dataclass(frozen=True)
class ExecutionCounts:
    live: int = 0
    waiting: int = 0
    attention: int = 0
    done: int = 0

    def for_bucket(self, bucket: str) -> int:
        return {
            live_board.BUCKET_LIVE: self.live,
            live_board.BUCKET_WAITING: self.waiting,
            live_board.BUCKET_ATTENTION: self.attention,
            live_board.BUCKET_DONE: self.done,
        }[bucket]


def actionable_board(
    sessions: list[dict],
    tasks_by_id: dict[str, dict],
    *,
    dismissed_attention_run_ids: set[str] | frozenset[str] = frozenset(),
) -> dict[str, list[dict]]:
    """Bucket sessions and remove rows that no longer require user action."""
    board = live_board.split_board(sessions, display_status="display_status")
    hidden_attention = (
        live_board.superseded_run_ids(sessions)
        | live_board.completed_task_run_ids(sessions, tasks_by_id)
        | frozenset(dismissed_attention_run_ids)
    )
    if hidden_attention:
        board[live_board.BUCKET_ATTENTION] = [
            row
            for row in board[live_board.BUCKET_ATTENTION]
            if row.get("run_id") not in hidden_attention
        ]
    return board


def counts_for_snapshot(
    board: dict[str, list[dict]], queue_entries: list[dict]
) -> ExecutionCounts:
    """Count one already-bucketed snapshot, including the durable task queue."""
    open_queue = sum(
        1 for entry in queue_entries if entry.get("state") in execution_queue.OPEN_STATES
    )
    return ExecutionCounts(
        live=len(board[live_board.BUCKET_LIVE]),
        waiting=len(board[live_board.BUCKET_WAITING]) + open_queue,
        attention=len(board[live_board.BUCKET_ATTENTION]),
        done=len(board[live_board.BUCKET_DONE]),
    )


def lightweight_sessions_from_runs(
    runs: list[dict], tasks_by_id: dict[str, dict]
) -> list[dict]:
    """Project run rows far enough to produce the same headline buckets.

    This intentionally skips logs, reports and git inspection. It is used by
    the small cross-page status strip, which needs current counts but must not
    rebuild the expensive interactive Live Center every few seconds.
    """
    rows: list[dict] = []
    for run in runs:
        task = tasks_by_id.get(run.get("task_id")) or {}
        status = session_view.derive_status(
            run,
            awaiting_handshake=session_view.is_awaiting_handshake(run),
        )
        display_status = session_view.operator_display_status(
            status,
            progress=task.get("progress"),
            task_type=run.get("task_type") or task.get("task_type"),
        )
        rows.append(
            {
                "run_id": run.get("id"),
                "task_id": run.get("task_id"),
                "started_at": run.get("started_at") or run.get("created_at"),
                "display_status": display_status,
            }
        )
    return rows
