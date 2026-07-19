"""Composition layer over more than one owning store, on a panel's behalf.

See `docs/workspace/UNIVERSAL_WORKSPACE_ARCHITECTURE.md`'s `workspace_service.py`
section and `docs/workspace/WORKSPACE_PRINCIPLES.md` rule 6 ("service
boundaries"): a join used by more than one panel belongs here, not
copy-pasted into each panel. This module composes; it never owns, caches
beyond a single call, or writes.

Per `docs/workspace/MODULE_DEPENDENCY_MAP.md`'s forbidden-edges list, this
module never imports `workspace_context` and never accepts a
`WorkspaceContext` instance -- every function here takes plain ids as keyword
arguments. The caller (a panel, or later `ui/workspace_shell.py`) reads
`WorkspaceContext` itself and passes the relevant id(s) in; this keeps the L1
workspace layer acyclic (`Ctx` and `Svc` never depend on each other).
"""

from __future__ import annotations

from pathlib import Path

from command_center import execution_queue, models, panel_registry, project_config, tasks_repository, workspace_home
from command_center.runtime.api import ExecutionCenterAPI


def project_snapshot(project_id: str, *, execution_center_api: ExecutionCenterAPI, root: Path) -> dict:
    """One project's slice of `workspace_home.build_workspace_home_snapshot`
    (already sensitivity-redacted for BANK/LEGAL there), plus its execution
    queue entries. Matches the Project Overview panel's documented data
    source in `NAVIGATION_AND_PANEL_MODEL.md`: "`workspace_home.
    build_workspace_home_snapshot()` filtered to one project"."""
    if project_id not in models.PROJECT_IDS:
        raise ValueError(f"Unknown project: {project_id!r}")

    home = workspace_home.build_workspace_home_snapshot(execution_center_api=execution_center_api)
    project_entry = next((p for p in home["projects"] if p["id"] == project_id), None)
    queue_entries = [entry for entry in execution_queue.load_queue(root) if entry.get("project") == project_id]

    return {
        "project": project_entry,
        "config": project_config.get_project_config(project_id),
        "worktrees": home["worktrees_by_project"].get(project_id),
        "active_runs": [r for r in home["active_runs"] if r.get("project") == project_id],
        "recent_runs": [r for r in home["recent_runs"] if r.get("project") == project_id],
        "reports": [r for r in home["reports"] if r.get("project") == project_id],
        "artifacts": [a for a in home["artifacts"] if a.get("project") == project_id],
        "recent_activity": [a for a in home["recent_activity"] if a.get("project") == project_id],
        "queue_entries": queue_entries,
    }


def task_snapshot(task_id: str, *, execution_center_api: ExecutionCenterAPI, root: Path) -> dict | None:
    """A task joined with its execution queue entry and its current run, per
    the Task Detail panel's documented data source in
    `NAVIGATION_AND_PANEL_MODEL.md`. Returns `None` if the task no longer
    exists -- degrading gracefully, same discipline as
    `workspace_context.reconcile`, rather than raising."""
    tasks = tasks_repository.load_tasks(root)
    task = next((t for t in tasks if t.get("id") == task_id), None)
    if task is None:
        return None

    queue_entry = next(
        (entry for entry in execution_queue.load_queue(root) if entry.get("task_id") == task_id),
        None,
    )
    run_id = task.get("current_run_id")
    current_run = execution_center_api.get_run(run_id) if run_id else None

    return {"task": task, "queue_entry": queue_entry, "current_run": current_run}


def panel_data(
    panel_key: str,
    *,
    execution_center_api: ExecutionCenterAPI,
    root: Path,
    project_id: str | None = None,
    task_id: str | None = None,
) -> dict:
    """Routes to the right snapshot function for `panel_key`, per that
    panel's `data_kind` in `panel_registry.PANELS`. Returns `{}` when the id
    a panel's `data_kind` requires wasn't supplied -- an unscoped/empty-state
    render, not an error, matching every panel contract's documented empty
    state in `NAVIGATION_AND_PANEL_MODEL.md`."""
    spec = panel_registry.get(panel_key)
    if spec is None:
        raise ValueError(f"Unknown panel: {panel_key!r}")

    if spec.data_kind == "project":
        if project_id is None:
            return {}
        return project_snapshot(project_id, execution_center_api=execution_center_api, root=root)

    if spec.data_kind == "task":
        if task_id is None:
            return {}
        return task_snapshot(task_id, execution_center_api=execution_center_api, root=root) or {}

    return {}
