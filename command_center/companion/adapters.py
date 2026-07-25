"""Read adapters for the Companion Sync Service.

Every function here is a **thin, additive-only wrapper**: it calls one existing
function and returns that function's existing shape. None of them compute
anything the core does not already compute, and none of them drop or rename a
field. That rule (`docs/mobile/API_REQUIREMENTS.md` §1) is what keeps the mobile
client and the desktop app from drifting into two different opinions about the
same state — a divergence that would be invisible until the two disagreed in
front of the operator.

Two consequences worth stating, because they are easy to erode later:

- **Redaction is not this layer's job, and must not become it.** Sensitive
  projects (BANK/LEGAL) are redacted inside `workspace_home` before its snapshot
  is returned, so these adapters never see the unredacted values. Adding a
  redaction step here would create a second, competing boundary — and the one
  that is easy to forget to update.
- **No new state.** Nothing here writes, and nothing here caches. A mobile
  client reading a stale cache while the desktop shows live state is exactly the
  class of bug this service exists to avoid.

Serialization to JSON is left to the HTTP layer: these return plain Python
structures, so they are equally usable by a test, a CLI, or a future adapter
that is not HTTP at all.
"""

from __future__ import annotations

from pathlib import Path

from command_center import (
    activity_log,
    execution_queue,
    recommendation_service,
    report_parser,
    tasks_repository,
    workspace_home,
)
from command_center.runtime import api as runtime_api


def dashboard(*, execution_center_api: runtime_api.ExecutionCenterAPI) -> dict:
    """The full Workspace Home snapshot, exactly as the desktop renders it:
    `{projects, worktrees_by_project, active_runs, recent_runs, recent_activity,
    artifacts, reports}`. Redaction for sensitive projects is already applied by
    the snapshot builder itself."""
    return workspace_home.build_workspace_home_snapshot(execution_center_api=execution_center_api)


def projects(*, execution_center_api: runtime_api.ExecutionCenterAPI) -> list[dict]:
    """The snapshot's `projects` list. Derived from the same call as
    `dashboard` rather than a second, independently-built project list, so the
    two can never disagree about a project's state."""
    return dashboard(execution_center_api=execution_center_api)["projects"]


def project_tasks(project_id: str, *, execution_center_api: runtime_api.ExecutionCenterAPI) -> list[dict]:
    return execution_center_api.list_tasks(project=project_id)


def runs(
    *,
    execution_center_api: runtime_api.ExecutionCenterAPI,
    state: str | None = None,
    states: list[str] | None = None,
    task_id: str | None = None,
    session_id: str | None = None,
    limit: int | None = 100,
) -> list[dict]:
    """Run rows. `limit` defaults to a bounded page rather than the whole table:
    a mobile client on a slow link must not be handed an unbounded history, and
    the caller can always ask for more explicitly."""
    return execution_center_api.list_runs(
        state=state, states=states, task_id=task_id, session_id=session_id, limit=limit
    )


def run(run_id: str, *, execution_center_api: runtime_api.ExecutionCenterAPI) -> dict | None:
    """One run row, or `None` when it does not exist — the HTTP layer turns that
    into a 404 rather than this layer inventing an error shape."""
    return execution_center_api.get_run(run_id)


def run_events(
    run_id: str,
    *,
    execution_center_api: runtime_api.ExecutionCenterAPI,
    after_seq: int = 0,
    limit: int = 1000,
) -> list[dict]:
    return execution_center_api.get_events(run_id, after_seq=after_seq, limit=limit)


def run_report(run_id: str, *, execution_center_api: runtime_api.ExecutionCenterAPI) -> dict | None:
    """The run's report: its stored row, the raw markdown, and the *existing*
    parse of it.

    `parsed` is `report_parser.parse_report`'s own return shape, never
    re-derived here — the mobile report screen and the desktop one must agree
    about a verdict, and two parsers would eventually not.

    Returns `None` when no report row exists. A row whose file has since been
    removed returns the row with empty content rather than raising: a missing
    file is a fact about the report, not a failure of the request."""
    row = execution_center_api.get_report(run_id)
    if row is None:
        return None
    raw = ""
    path = row.get("path")
    if path:
        try:
            raw = Path(path).read_text(encoding="utf-8")
        except OSError:
            raw = ""
    return {
        "run_id": run_id,
        "path": path,
        "created_at": row.get("created_at"),
        "raw_markdown": raw,
        "parsed": report_parser.parse_report(raw) if raw else report_parser.empty_parsed_result(),
    }


def queue(root: Path) -> list[dict]:
    """The persisted execution queue, read-only.

    Deliberately `load_queue`, not `reevaluate_and_persist`: a mobile client
    polling this endpoint must not silently rewrite queue state as a side effect
    of looking at it. Re-evaluation belongs to the desktop's own refresh
    checkpoints."""
    return execution_queue.load_queue(root)


def recommendations(root: Path, *, project: str | None = None, limit: int = 3) -> list[dict]:
    tasks = tasks_repository.load_tasks(root)
    tasks_by_id = {task["id"]: task for task in tasks if task.get("id")}
    return recommendation_service.build_recommendation_views(
        tasks,
        tasks_by_id,
        project=project,
        queue_entries=execution_queue.load_queue(root),
        limit=limit,
    )


def activity(*, limit: int = 100) -> list[dict]:
    return activity_log.load_activity(limit=limit)
