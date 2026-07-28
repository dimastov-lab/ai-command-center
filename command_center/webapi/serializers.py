"""Pure snapshot -> API DTO mapping for the `GET /api/home` endpoint.

`serialize_home` maps the dict returned by
`command_center.workspace_home.build_workspace_home_snapshot` onto the stable
output DTO the web frontend (Task 4) consumes:
`{projects, kpis, queue, health, activity, overview, status}`.

Redaction for BANK/LEGAL is primarily upstream, in `workspace_home.py`
(`sanitize_workspace_project_entry`'s field allowlist), which already strips
sensitive fields from every *run*/*report*/*artifact*/*activity* entry before
it reaches this module. That allowlist does **not** cover the per-project
rollup entries in `snapshot["projects"]`, though — `build_workspace_home_snapshot`
attaches `task_count`/`active_run_count` to every project, sensitive or not,
unfiltered (see `workspace_home.py` around `build_workspace_home_snapshot`'s
`projects_out.append(...)`). This module is the one place that gap is closed
for the web surface: any project flagged `sensitive` (or, defensively,
`redacted`) never gets a `health` block or raw counts in the serialized
output — only `id`/`name`/`healthy`/`redacted`. A pure function, no I/O, no
mutation, safe to unit test without a database.
"""

from __future__ import annotations

from typing import Any

from command_center import models

# `workspace_home._discover_worktrees` returns one of "ok" / "unconfigured" /
# "invalid_path" / "not_git_repo" per project. "unconfigured" (no repository
# configured yet) is not itself a health problem; the other two non-"ok"
# states are actual misconfiguration.
_UNHEALTHY_REPOSITORY_STATES = frozenset({"invalid_path", "not_git_repo"})

# Real v2 run `state` values that mean "actually executing" (a strict subset
# of `runtime.db.EXECUTION_CENTER_ACTIVE_STATES`, which also includes
# "PREPARED"/"QUEUED"). Duplicated here rather than imported so this module
# stays a pure, DB-free mapper (see module docstring).
_RUNNING_STATE = "RUNNING"


def _project_healthy(project: dict[str, Any]) -> bool:
    if "healthy" in project:
        return bool(project["healthy"])
    return project.get("repository_state") not in _UNHEALTHY_REPOSITORY_STATES


def _is_sensitive(project: dict[str, Any]) -> bool:
    return bool(project.get("sensitive") or project.get("redacted"))


def _serialize_projects(raw_projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    projects: list[dict[str, Any]] = []
    for p in raw_projects:
        pid = p.get("id", "")
        item: dict[str, Any] = {
            "id": pid,
            "name": p.get("name") or p.get("display_name") or pid,
            "healthy": _project_healthy(p),
        }
        if _is_sensitive(p):
            # Never emit raw metrics for a sensitive/redacted project — see
            # module docstring. `healthy` above is a derived boolean, not a
            # raw metric, so it is fine to keep.
            item["redacted"] = True
        elif "health" in p:
            item["health"] = p["health"]
        else:
            item["health"] = {
                "task_count": p.get("task_count", 0),
                "active_run_count": p.get("active_run_count", 0),
                "repository_state": p.get("repository_state"),
            }
        projects.append(item)
    return projects


def _active_runs(snap: dict[str, Any]) -> list[dict[str, Any]]:
    # Prefer the real snapshot key; fall back to a "runs" key so a caller
    # feeding a simpler/legacy fixture still works.
    if "active_runs" in snap:
        return snap.get("active_runs") or []
    return snap.get("runs") or []


def _serialize_queue(active_runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    queue = []
    for r in active_runs:
        queue.append(
            {
                # Real run entries carry no display title; `task_type` is the
                # closest thing to one (e.g. "implementation", "review").
                "title": r.get("title") or r.get("task_type") or r.get("run_id") or "Run",
                "project": r.get("project", ""),
                # No progress signal exists anywhere upstream (v2 runs have no
                # progress column) — sensible zero default rather than a
                # fabricated number.
                "progress": int(r.get("progress", 0) or 0),
                "state": r.get("state") or r.get("status") or "",
            }
        )
    return queue


def _kpis(snap: dict[str, Any], raw_projects: list[dict[str, Any]], active_runs: list[dict[str, Any]]) -> dict[str, Any]:
    healthy_count = sum(1 for p in raw_projects if _project_healthy(p))

    # `active_run_count` is attached to every project (sensitive or not,
    # see module docstring) unfiltered by workspace_home's allowlist, so
    # summing it — a workspace-wide total, not a per-project breakdown — is
    # consistent with the existing internal Streamlit Workspace Home page
    # (`app.py`'s `render_workspace_home_page`), which sums the very same
    # field across all projects, sensitive included, for its top metric row.
    total_active = sum(p.get("active_run_count", 0) for p in raw_projects)
    running_count = sum(1 for r in active_runs if (r.get("state") or r.get("status")) == _RUNNING_STATE)

    total_tasks = sum(p.get("task_count", 0) for p in raw_projects)

    reports = snap.get("reports") or []
    total_reviews = len(reports)
    pending_reviews = sum(1 for r in reports if not models.is_passing_verdict(r.get("verdict")))

    return {
        "projects": {
            "value": len(raw_projects),
            "meta_key": "all_healthy",
            "meta_n": healthy_count,
        },
        "agents": {
            "value": total_active,
            "meta_key": "running",
            "meta_n": running_count,
        },
        "tasks": {
            "value": total_tasks,
            # No status column exists on the `task` table today (see
            # `command_center/runtime/db.py`'s `CREATE TABLE task` schema),
            # so an in-progress subset genuinely isn't derivable from the
            # snapshot — sensible zero default rather than a fabricated
            # number, per the corrections to this task's brief.
            "meta_key": "in_progress",
            "meta_n": 0,
        },
        "reviews": {
            "value": total_reviews,
            "meta_key": "pending",
            "meta_n": pending_reviews,
        },
    }


def _status(raw_projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # `repository_state` (ok/unconfigured/invalid_path/not_git_repo) is not a
    # raw metric, so it's safe to surface for every project, sensitive or not.
    return [
        {"project": p.get("id", ""), "repository_state": p.get("repository_state")}
        for p in raw_projects
        if "repository_state" in p
    ]


def _health(raw_projects: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(raw_projects)
    healthy = sum(1 for p in raw_projects if _project_healthy(p))
    return {"projects_healthy": healthy, "projects_total": total}


def _overview(snap: dict[str, Any]) -> dict[str, Any]:
    return {
        "reports_count": len(snap.get("reports") or []),
        "artifacts_count": len(snap.get("artifacts") or []),
        "recent_activity_count": len(snap.get("recent_activity") or []),
    }


def serialize_home(snap: dict[str, Any]) -> dict[str, Any]:
    raw_projects = snap.get("projects") or []
    active_runs = _active_runs(snap)

    return {
        "projects": _serialize_projects(raw_projects),
        "kpis": _kpis(snap, raw_projects, active_runs),
        "queue": _serialize_queue(active_runs),
        "health": _health(raw_projects),
        "activity": snap.get("recent_activity") or snap.get("activity") or [],
        "overview": _overview(snap),
        "status": _status(raw_projects),
    }
