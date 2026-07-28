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
    # INTENTIONAL POLICY: `queue` (like `activity`/`status`/`overview.recent_
    # activity_count`/`reviews` below) does no per-serializer field filtering
    # of its own for a sensitive project's run. It inherits the upstream
    # field-allowlist in `command_center/workspace_home.py`
    # (`_RUN_ALLOWED_FIELDS`/`_ACTIVITY_ALLOWED_FIELDS`, applied by
    # `sanitize_workspace_project_entry`) as the *authoritative* redaction
    # boundary — that allowlist already permits project/task_type/state/
    # run_id/timestamps to reach here for BANK/LEGAL runs, matching what the
    # existing Streamlit Workspace Home page shows today. See
    # `tests/webapi/test_serializers.py::
    # test_serialize_home_sensitive_run_queue_and_activity_expose_only_allowlisted_fields`
    # for the regression test pinning this.
    #
    # This is DELIBERATELY asymmetric with `kpis.agents`/`kpis.tasks` and the
    # per-project raw counts in `_serialize_projects`/`_kpis` below, which
    # ADDITIONALLY exclude sensitive projects entirely (not merely allowlist
    # their fields) — because those are aggregates where a sensitive project's
    # raw number could otherwise be recovered by subtraction (see `_kpis`).
    # No such subtraction risk exists for `queue`/`activity`/`status`/
    # `reviews`, since they only ever carry already-allowlisted per-entry
    # fields, never a raw count to subtract from.
    #
    # Fully excluding sensitive-project rows from `queue`/`activity` (rather
    # than allowlisting their fields) would be a *stricter* policy than what
    # workspace_home.py's own Streamlit page does — that's a future policy
    # decision, not something this module changes unilaterally.
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

    # `active_run_count`/`task_count` are attached to every project (sensitive
    # or not, see module docstring) unfiltered by workspace_home's allowlist.
    # Fix round 1 (code review finding): summing them across ALL projects —
    # even though no single field ever says "BANK: N" — still leaks a
    # sensitive-derived raw number, because every *non*-sensitive project's
    # own `health` block is fully exposed in `projects[]`; a client can
    # recover the combined BANK+LEGAL raw total by subtracting the visible
    # non-sensitive projects' values from the workspace-wide sum. These KPI
    # aggregates are therefore computed over non-sensitive projects only —
    # unlike the existing internal Streamlit Workspace Home page (`app.py`'s
    # `render_workspace_home_page`), which sums the same fields including
    # sensitive projects for its top metric row; that precedent doesn't hold
    # here because this API additionally exposes each non-sensitive project's
    # value individually, which the Streamlit page's aggregate-only metric
    # does not, making the subtraction attack possible only on this surface.
    non_sensitive_projects = [p for p in raw_projects if not _is_sensitive(p)]
    non_sensitive_ids = {p.get("id") for p in non_sensitive_projects}

    total_active = sum(p.get("active_run_count", 0) for p in non_sensitive_projects)
    running_count = sum(
        1
        for r in active_runs
        if r.get("project") in non_sensitive_ids and (r.get("state") or r.get("status")) == _RUNNING_STATE
    )

    total_tasks = sum(p.get("task_count", 0) for p in non_sensitive_projects)

    # `reviews` (like `queue`/`activity`/`status` — see the policy comment
    # above `_serialize_queue`) is a count over already-allowlisted `reports`
    # entries (`_REPORT_ALLOWED_FIELDS` in workspace_home.py for sensitive
    # projects); it is intentionally NOT excluded per-project the way
    # `agents`/`tasks` above are, since a count alone carries no subtractable
    # raw per-project number.
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
    # `recent_activity_count` is a `len()` over the same upstream-allowlisted
    # `recent_activity` list used for `activity` below — see the policy
    # comment above `_serialize_queue`. Same reasoning: a bare count, so
    # nothing sensitive is added or excluded here beyond what the allowlist
    # already did.
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
