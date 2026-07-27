"""Recommendation presentation model: wraps `command_center.recommend`
(the existing, unmodified scoring core) with the extra fields a real "next
task" surface needs — dependencies, impact, readiness, and whether a task is
already sitting in the execution queue.

Deliberately not a second recommendation engine: every ranking decision
still comes from `recommend.list_recommendations`'s scoring loop. This
module only *decorates* each result with information recommend.py has no
reason to know about (the dependency graph's reverse edges, the queue's
current contents) — reusing `models.derive_dependency_edges` and
`models.unmet_dependencies`, the same helpers the Task Card and Kanban
"blocked" chip already use, so a recommendation's dependency list can never
disagree with what the card itself shows for the same task.
"""

from __future__ import annotations

from command_center import execution_queue, models, recommend


def _queued_state(task_id: str, queue_entries: list[dict] | None) -> str | None:
    if not queue_entries:
        return None
    for entry in queue_entries:
        if entry.get("task_id") == task_id and entry.get("state") in execution_queue.OPEN_STATES:
            return entry.get("state")
    return None


def build_recommendation_views(
    tasks: list[dict],
    tasks_by_id: dict[str, dict] | None = None,
    *,
    project: str | None = None,
    queue_entries: list[dict] | None = None,
    limit: int = 3,
    active_runs: list[dict] | None = None,
) -> list[dict]:
    """One view dict per recommendation, best-first:

    - `reasons` / `score` — verbatim from `recommend.Recommendation`.
    - `dependencies` — `[{id, title, status, done}]` for every
      `depends_on` entry (resolved against `tasks_by_id`; a dangling id
      renders with `title=None`).
    - `impact` — how many other tasks list this one in *their*
      `depends_on` (`models.derive_dependency_edges`'s `blocks` count) —
      "how much work unblocks if this lands."
    - `ready` — always `True` today (`recommend.py` already excludes
      blocked tasks from candidacy), kept explicit rather than assumed so a
      future relaxation of that filter doesn't silently mislabel a blocked
      task as recommended-and-ready.
    - `queued_state` — `"waiting"` / `"ready"` if this task already has an
      open execution-queue entry, else `None` — so the UI can show "Queued"
      instead of offering to queue it again.
    """
    all_tasks_by_id = tasks_by_id or {task["id"]: task for task in tasks if task.get("id")}
    recommendations = recommend.list_recommendations(tasks, project=project, limit=limit, active_runs=active_runs)

    views: list[dict] = []
    for rec in recommendations:
        task = rec.task
        task_id = task.get("id")

        dependencies = []
        for dep_id in task.get("depends_on") or []:
            dep = all_tasks_by_id.get(dep_id)
            dependencies.append(
                {
                    "id": dep_id,
                    "title": dep.get("title") if dep else None,
                    "status": dep.get("status") if dep else None,
                    "done": bool(dep and dep.get("status") == "Done"),
                }
            )

        impact = len(models.derive_dependency_edges(task, tasks)["blocks"])

        views.append(
            {
                "task_id": task_id,
                "title": task.get("title") or "Без названия",
                "project": task.get("project"),
                "priority": task.get("priority", "Medium"),
                "score": rec.score,
                "reasons": rec.reasons,
                "dependencies": dependencies,
                "impact": impact,
                "ready": not models.is_blocked(task, all_tasks_by_id),
                "queued_state": _queued_state(task_id, queue_entries),
                "workspace_path": task.get("workspace_path") or task.get("repository_path"),
            }
        )
    return views
