"""Next Task recommendation: a pure scoring function over the existing task
list plus read-only git health, so callers (the `executive`/`dashboard`
pages) can show "work on this next" — and always *why*. Never creates,
launches, or modifies anything; a plain read model like `workflow.py` and
`workspace_home.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from command_center import git_info, models

PRIORITY_WEIGHT: dict[str, int] = {"Critical": 3, "High": 2, "Medium": 1, "Low": 0}

FAILING_VERDICTS = {
    models.VERDICT_NOT_APPROVED_FOR_COMMIT,
    models.VERDICT_NOT_READY_FOR_FINAL_REVIEW,
    models.VERDICT_FAILED,
}


@dataclass
class Recommendation:
    task: dict
    score: float
    reasons: list[str] = field(default_factory=list)


def _repo_health(repository_path: str | None) -> tuple[bool, str | None]:
    """Read-only workspace/git health check, reusing `git_info` exactly as
    the Launch System does — no new git wrapping here."""
    if not repository_path:
        return True, None
    path = Path(repository_path)
    if not path.exists():
        return False, f"workspace недоступен: {repository_path}"
    status = git_info.get_status(path)
    if not status.get("is_repo"):
        return False, "не git-репозиторий"
    if status.get("branch") == "(detached HEAD)":
        return False, "detached HEAD"
    if status.get("dirty"):
        return False, "рабочее дерево не чистое"
    return True, None


def recommend_next_task(tasks: list[dict], project: str | None = None) -> Recommendation | None:
    tasks_by_id = {task["id"]: task for task in tasks if task.get("id")}
    running_repos = {
        task.get("repository_path")
        for task in tasks
        if task.get("launch_status") == "Running" and task.get("repository_path")
    }

    candidates: list[Recommendation] = []
    for task in tasks:
        if project and task.get("project") != project:
            continue
        if task.get("status") == "Done":
            continue
        if models.is_blocked(task, tasks_by_id):
            continue

        reasons: list[str] = []
        score = 0.0

        priority = task.get("priority", "Medium")
        score += PRIORITY_WEIGHT.get(priority, 1) * 10
        reasons.append(f"приоритет «{priority}»")

        healthy, problem = _repo_health(task.get("repository_path"))
        if not healthy:
            score -= 15
            reasons.append(f"репозиторий требует внимания ({problem})")
        elif task.get("repository_path"):
            score += 5
            reasons.append("репозиторий чист и готов к работе")

        if task.get("repository_path") in running_repos:
            score -= 20
            reasons.append("исполнитель уже занят на этом репозитории другой задачей")
        else:
            reasons.append("исполнитель свободен")

        deps = task.get("depends_on") or []
        if deps:
            all_merged = all(tasks_by_id.get(dep_id, {}).get("pull_request_status") == "merged" for dep_id in deps)
            if all_merged:
                score += 6
                reasons.append("все зависимости выполнены и смержены")
            else:
                score += 3
                reasons.append("все зависимости выполнены")

        if task.get("latest_verdict") in FAILING_VERDICTS:
            score += 6
            reasons.append(f"предыдущий запуск требует исправлений (вердикт «{task['latest_verdict']}»)")

        if task.get("workflow_stage") in ("Remediation", "Final Review"):
            score += 8
            reasons.append(f"уже в процессе («{task['workflow_stage']}») — стоит довести до конца")

        candidates.append(Recommendation(task=task, score=score, reasons=reasons))

    if not candidates:
        return None

    candidates.sort(key=lambda rec: rec.score, reverse=True)
    return candidates[0]
