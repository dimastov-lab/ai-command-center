"""Project Intelligence: computed, read-only rollups over existing task and
project data — health, sprint/roadmap progress, remaining/blocked work, and
a critical-path hint.

Nothing here is persisted. Every value is recomputed on each call from
`tasks.json` (via the caller's already-loaded task list) and, where relevant,
`project_config` — matching the standing convention already established by
`command_center.workspace_home` and `command_center.recommend`: a plain read
model, never a second store, never something the caller must remember to
"update." A project's health this second is exactly what its task graph says
it is this second.

Two real data-model gaps are worked around explicitly rather than silently
faked:

- There is no per-task sprint identifier — `project_config.current_sprint`
  names *a* sprint for the whole project, but nothing on a task says which
  sprint it belongs to. `sprint_progress_pct` is therefore a proxy — the
  mean `progress` (0-100, from `models.STAGE_PROGRESS`) across the project's
  *active* tasks, i.e. "how far along is in-flight work," not "percent of
  sprint N done." Labelled as such by the caller (see `command_center.ui`).
- There is no milestone-to-task link either, so `roadmap_progress_pct` is
  the same metric as plain completion (done / total). Kept as a distinct
  field/name so a future task-level milestone field can replace its
  computation without changing every call site that reads it.
"""

from __future__ import annotations

from command_center import models, project_config

HEALTH_GOOD = "Good"
HEALTH_ATTENTION = "Attention"
HEALTH_AT_RISK = "At Risk"

# A project is "At Risk" once more than this share of its active tasks are
# blocked — chosen as a simple, documented threshold rather than tuned
# against real data (none exists yet); revisit once real usage data does.
AT_RISK_BLOCKED_RATIO = 0.3

_MAX_CRITICAL_PATH_DEPTH = 25


def rank_projects_by_activity(project_ids: list[str], tasks: list[dict]) -> list[str]:
    """`project_ids` (expected: `models.PROJECT_IDS`, the canonical registry)
    re-ordered by active (non-`Done`) task count, descending — ties broken by
    the input's own order, so the ranking is deterministic and never depends
    on dict/set iteration order. Used only to *prioritize/group* projects in
    a selector (e.g. "the 3 most active"); callers must still offer every id
    in `project_ids` — this function never drops one."""
    # Count on canonical ids (via the shared `project_config.canonical_project_id`
    # helper) so a task storing a display name / alias ("AI Command Center")
    # ranks under its canonical project ("AICC") instead of being ignored — the
    # same normalization the Kanban filter, pill count, and intelligence strip use.
    active_counts: dict[str, int] = dict.fromkeys(project_ids, 0)
    for task in tasks:
        project = project_config.canonical_project_id(task.get("project"))
        if project in active_counts and task.get("status") != "Done":
            active_counts[project] += 1
    return sorted(project_ids, key=lambda pid: (-active_counts[pid], project_ids.index(pid)))


def _longest_blocked_chain(tasks_by_id: dict[str, dict], blocked_task_ids: list[str]) -> list[str]:
    """Longest dependency chain gating any currently-blocked task, walked
    depth-first over unmet dependencies. Depth-bounded (not cycle-safe by
    construction — `depends_on` is user-editable data) and revisit-guarded
    per branch so a cycle degrades to a truncated path instead of an
    infinite walk."""

    def walk(task_id: str, visited: list[str]) -> list[str]:
        if len(visited) > _MAX_CRITICAL_PATH_DEPTH:
            return visited
        task = tasks_by_id.get(task_id)
        if task is None:
            return visited
        longest = visited
        for dep_id in models.unmet_dependencies(task, tasks_by_id):
            if dep_id in visited:
                continue
            candidate = walk(dep_id, [*visited, dep_id])
            if len(candidate) > len(longest):
                longest = candidate
        return longest

    best: list[str] = []
    for task_id in blocked_task_ids:
        chain = walk(task_id, [task_id])
        if len(chain) > len(best):
            best = chain
    return best


def compute_project_intelligence(
    project_id: str | None,
    tasks: list[dict],
    tasks_by_id: dict[str, dict] | None = None,
) -> dict:
    """`project_id=None` aggregates across every task (an "All Projects"
    rollup); otherwise scoped to that project only. Dependency resolution
    (`unmet_dependencies`, the critical path) always considers the *full*
    task graph in `tasks_by_id`, even when scoped to one project — a task
    can depend on a task from another project, and understating that would
    misreport why something is blocked."""
    all_tasks_by_id = tasks_by_id or {task["id"]: task for task in tasks if task.get("id")}
    # Scope on canonical ids so a display-name/alias task ("AI Command Center")
    # is included in its canonical project's ("AICC") rollup — otherwise the
    # intelligence strip's total/health/completion silently undercounts and
    # disagrees with the Kanban lane and pill for the very same project.
    project_tasks = [t for t in tasks if project_config.project_matches(t.get("project"), project_id)]

    total = len(project_tasks)
    done_tasks = [t for t in project_tasks if t.get("status") == "Done"]
    active_tasks = [t for t in project_tasks if t.get("status") != "Done"]
    blocked_tasks = [t for t in active_tasks if models.is_blocked(t, all_tasks_by_id)]

    completion_pct = round(len(done_tasks) / total * 100) if total else None
    roadmap_progress_pct = completion_pct  # see module docstring: same metric, distinct name
    sprint_progress_pct = (
        round(sum(int(t.get("progress") or 0) for t in active_tasks) / len(active_tasks))
        if active_tasks
        else None
    )

    blocked_ratio = (len(blocked_tasks) / len(active_tasks)) if active_tasks else 0.0
    failing_count = sum(
        1
        for t in active_tasks
        if t.get("latest_verdict") is not None and not models.is_passing_verdict(t.get("latest_verdict"))
    )

    if not blocked_tasks and failing_count == 0:
        health, health_reason = HEALTH_GOOD, "нет заблокированных задач и неудачных вердиктов"
    elif blocked_ratio > AT_RISK_BLOCKED_RATIO:
        health, health_reason = (
            HEALTH_AT_RISK,
            f"{len(blocked_tasks)} из {len(active_tasks)} активных задач заблокированы",
        )
    else:
        reasons = []
        if blocked_tasks:
            reasons.append(f"{len(blocked_tasks)} заблокированных задач")
        if failing_count:
            reasons.append(f"{failing_count} с неудачным вердиктом")
        health, health_reason = HEALTH_ATTENTION, ", ".join(reasons)

    critical_path_ids = _longest_blocked_chain(all_tasks_by_id, [t["id"] for t in blocked_tasks if t.get("id")])
    critical_path_titles = [
        (all_tasks_by_id.get(task_id, {}).get("title") or task_id) for task_id in critical_path_ids
    ]

    return {
        "project_id": project_id,
        "health": health,
        "health_reason": health_reason,
        "total": total,
        "active": len(active_tasks),
        "done": len(done_tasks),
        "remaining": len(active_tasks),
        "blocked": len(blocked_tasks),
        "completion_pct": completion_pct,
        "roadmap_progress_pct": roadmap_progress_pct,
        "sprint_progress_pct": sprint_progress_pct,
        "critical_path": critical_path_titles,
    }
