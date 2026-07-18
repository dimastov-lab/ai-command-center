"""One-way, conservative sync from a v2 `run` (the execution-state source of
truth) onto the Kanban task it was launched for.

Never re-derives execution state from a task field — always reads the
`run` row fresh via `ExecutionCenterAPI`/`db`. Never advances
`workflow_stage`/`progress`/`current_stage`: those remain governed entirely
by the existing v1.2 rules (`models.set_current_stage`,
`workflow.suggest_next_task`) and by manual user action — this module only
ever touches `launch_status` and the small set of display/bookkeeping
fields (`current_run_id`, `report_path`, `repository_path`, `branch`,
`last_run_at`, `latest_verdict`, `pull_request_url`) that already exist on
every task record.
"""

from __future__ import annotations

from command_center import models, report_parser
from command_center.runtime import db, reports, session_view
from command_center.runtime.api import ExecutionCenterAPI

# Session-view display status -> Kanban `models.LAUNCH_STATUSES` value. Every
# value on the right is already a member of `models.LAUNCH_STATUSES` — no new
# status is introduced here, in the `run` table, or on the task record.
_LAUNCH_STATUS_BY_DISPLAY_STATUS: dict[str, str] = {
    session_view.STATUS_LAUNCHING: "Launching",
    session_view.STATUS_RUNNING: "Running",
    session_view.STATUS_WAITING: "Requires Attention",
    session_view.STATUS_REQUIRES_ATTENTION: "Requires Attention",
    session_view.STATUS_COMPLETED: "Completed",
    # `models.LAUNCH_STATUSES` has no dedicated "Cancelled" value — the
    # closest existing one is "Failed" (the task did not finish its work),
    # documented as a known simplification (see the plan's Limitations).
    session_view.STATUS_FAILED: "Failed",
    session_view.STATUS_CANCELLED: "Failed",
}

_TERMINAL_LAUNCH_STATUSES = frozenset({"Completed", "Failed"})


def _apply_terminal_fields(task: dict, run: dict, *, db_path) -> None:
    """The one-time work done exactly once per terminal run, guarded by the
    caller (`sync_task_from_run`). Reads the run's final result text via the
    same deterministic `report_parser.parse_report` v1.2 already uses — no
    new extraction logic."""
    report_row = db.get_report(db_path, run["id"])
    if report_row:
        task["report_path"] = report_row["path"]

    task["repository_path"] = run.get("repository_path")
    live_status = session_view.live_git_status(run.get("repository_path"))
    task["branch"] = (live_status or {}).get("branch") or run.get("expected_branch") or task.get("branch")
    task["last_run_at"] = run.get("completed_at")

    events = db.list_run_events(db_path, run["id"], after_seq=0, limit=1_000_000)
    parsed = report_parser.parse_report(reports.result_text(events)) if events else report_parser.empty_parsed_result()
    if parsed.get("verdict"):
        task["latest_verdict"] = parsed["verdict"]
    if parsed.get("pull_request_url"):
        task["pull_request_url"] = parsed["pull_request_url"]

    commit_hash = parsed.get("commit_hash")
    pull_request_url = parsed.get("pull_request_url")
    if commit_hash or pull_request_url:
        try:
            db.set_run_result_fields(
                db_path,
                run["id"],
                expected_version=run["version"],
                commit_hash=commit_hash,
                pull_request_url=pull_request_url,
            )
        except db.LostUpdateError:
            pass  # cosmetic run-row enrichment only; the task fields above are already set

    event_type = "completed" if task.get("launch_status") == "Completed" else "launch_requires_attention"
    models.append_timeline_event(
        task, event_type, f"Синхронизировано из прогона `{run['id'][:8]}` (Live Execution Center v2)."
    )


def sync_task_from_run(task: dict, run: dict, *, db_path) -> bool:
    """Returns whether `task` was mutated (so the caller knows to persist
    via `save_tasks`)."""
    status = session_view.derive_status(run)
    target_launch_status = _LAUNCH_STATUS_BY_DISPLAY_STATUS[status]
    mutated = False

    is_new_run_for_task = task.get("current_run_id") != run["id"]
    already_finalized_for_this_run = (
        not is_new_run_for_task and task.get("launch_status") in _TERMINAL_LAUNCH_STATUSES
    )

    if is_new_run_for_task:
        task["current_run_id"] = run["id"]
        mutated = True

    if task.get("launch_status") != target_launch_status:
        task["launch_status"] = target_launch_status
        mutated = True

    if status in session_view.TERMINAL_DISPLAY_STATUSES and not already_finalized_for_this_run:
        _apply_terminal_fields(task, run, db_path=db_path)
        mutated = True

    if mutated:
        task["updated_at"] = models.iso_now()
    return mutated


def reconcile_and_sync(api: ExecutionCenterAPI, tasks: list[dict]) -> list[dict]:
    """Reconciles every persisted `RUNNING` row against real OS processes
    (`Supervisor.reconcile()`, already implemented and tested — never
    duplicated here), then syncs every task that has a `current_run_id`
    (i.e. was launched, at some point, through the v2 bridge) against that
    run's current state. Tasks never touched by v2 launch have no
    `current_run_id` and are left completely alone. Returns the mutated
    tasks for the caller to persist."""
    api.reconcile()
    mutated: list[dict] = []
    for task in tasks:
        run_id = task.get("current_run_id")
        if not run_id:
            continue
        run = api.get_run(run_id)
        if run is None:
            continue
        if sync_task_from_run(task, run, db_path=api.db_path):
            mutated.append(task)
    return mutated
