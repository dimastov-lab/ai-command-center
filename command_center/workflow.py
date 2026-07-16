"""Verdict → next-task suggestion mapping ("Создать следующую задачу").

Nothing here executes a task or performs a git write. `suggest_next_task` only
produces a *draft* the user reviews and edits before the existing task-creation
workflow (`app.py`'s `create` page / `new_task_record`) is used to actually create the
task — see FEATURE 5 in the spec: "never automatically execute the new task" and, for
commit/push/PR stages specifically, "never execute Git writes automatically from the
suggestion button."
"""

from __future__ import annotations

from command_center import models, report_parser

# task_type is one of the five values scripts/start-task.sh recognizes
# (implementation/review/remediation/final_gate/architecture_review) — a task's
# *workflow_stage* (below) carries the finer-grained commit/push/PR semantics that
# don't map onto a start-task.sh template.
_VERDICT_SUGGESTIONS: dict[str, dict] = {
    models.VERDICT_NOT_APPROVED_FOR_COMMIT: {
        "task_type": "remediation",
        "workflow_stage": "Remediation",
        "requires_user_choice": False,
    },
    models.VERDICT_NOT_READY_FOR_FINAL_REVIEW: {
        "task_type": "remediation",
        "workflow_stage": "Remediation",
        "requires_user_choice": False,
    },
    models.VERDICT_READY_FOR_FINAL_REVIEW: {
        "task_type": "final_gate",
        "workflow_stage": "Final Review",
        "requires_user_choice": False,
    },
    models.VERDICT_READY_FOR_COMMIT: {
        # Ambiguous by design per spec: "READY FOR COMMIT → final_gate or commit
        # preparation, with user selection" — leave task_type unset so the UI must
        # ask.
        "task_type": None,
        "workflow_stage": "Commit Pending",
        "requires_user_choice": True,
    },
    models.VERDICT_APPROVED_FOR_COMMIT: {
        "task_type": "implementation",
        "workflow_stage": "Commit Pending",
        "requires_user_choice": False,
    },
}

TASK_TYPE_CHOICES_FOR_READY_FOR_COMMIT: list[str] = ["final_gate", "implementation"]

STAGE_AFTER_SUCCESSFUL_COMMIT = "Push Pending"
STAGE_AFTER_SUCCESSFUL_PUSH = "PR Pending"


def _format_findings_summary(parsed: dict) -> str:
    findings = parsed.get("findings") or {}
    lines = []
    for severity in models.SEVERITIES:
        items = findings.get(severity) or []
        if not items:
            continue
        lines.append(f"{severity} ({len(items)}):")
        lines.extend(f"  - {item}" for item in items)
    return "\n".join(lines) if lines else "Открытых находок не найдено в отчёте."


def build_objective_draft(run: dict, parsed: dict, *, contradictory: bool = False) -> str:
    verdict = parsed.get("verdict") or "неизвестен"
    verdict_label = models.VERDICT_LABELS.get(parsed.get("verdict", ""), verdict)
    next_action = parsed.get("recommended_next_action") or "не указана в отчёте"
    branch = parsed.get("branch") or run.get("post_run", {}).get("branch") or run.get("pre_run", {}).get("branch") or "неизвестна"
    repo = run.get("repository_path") or "неизвестен"

    parts = [
        f"Следующая задача по итогам запуска {run.get('id', '')[:8]} (проект {run.get('project')}).",
        f"Предыдущий вердикт: {verdict_label} ({verdict}).",
    ]
    if contradictory:
        parts.append(
            "⚠ Отчёт содержит противоречивые вердикты (найдено более одного). "
            "Показан наиболее консервативный вариант — проверьте отчёт вручную "
            "перед созданием задачи."
        )
    parts += [
        f"Репозиторий: {repo}",
        f"Ветка: {branch}",
        "",
        "Оставшиеся находки:",
        _format_findings_summary(parsed),
        "",
        f"Рекомендованное следующее действие из отчёта: {next_action}",
    ]
    return "\n".join(parts)


def suggest_next_task(run: dict) -> dict:
    """Return a next-task suggestion the user must review before creating anything.

    Shape: {task_type, task_type_choices, workflow_stage, objective_draft,
    requires_user_choice, source_verdict, contradictory}.
    """
    raw_parsed = run.get("parsed") or report_parser.empty_parsed_result()
    parsed = report_parser.apply_manual_corrections(raw_parsed)
    verdict = parsed.get("verdict")

    # A manual correction of the verdict field resolves a contradictory parse — the
    # user has already made the judgment call this would otherwise force.
    verdict_manually_corrected = "verdict" in (raw_parsed.get("manual_corrections") or {})
    contradictory = bool(raw_parsed.get("verdict_contradictory")) and not verdict_manually_corrected

    suggestion = _VERDICT_SUGGESTIONS.get(verdict, {
        "task_type": None,
        "workflow_stage": "Ready",
        "requires_user_choice": True,
    })

    return {
        "task_type": suggestion["task_type"],
        "task_type_choices": (
            TASK_TYPE_CHOICES_FOR_READY_FOR_COMMIT if verdict == models.VERDICT_READY_FOR_COMMIT else None
        ),
        "workflow_stage": suggestion["workflow_stage"],
        "objective_draft": build_objective_draft(run, parsed, contradictory=contradictory),
        # A contradictory report always forces manual review, regardless of which
        # verdict the (conservative, but still automatic) resolution picked.
        "requires_user_choice": suggestion["requires_user_choice"] or contradictory,
        "source_verdict": verdict,
        "contradictory": contradictory,
    }


def stage_after_commit_success() -> str:
    return STAGE_AFTER_SUCCESSFUL_COMMIT


def stage_after_push_success() -> str:
    return STAGE_AFTER_SUCCESSFUL_PUSH
