"""Shared constants and record factories for the v1.2 agent workflow.

Follows the same convention as the existing `app.py` task model: plain dicts
(JSON-serializable as-is) plus small `new_*`/`normalize_*` factory functions,
not dataclasses/ORM models. This keeps every record trivially compatible with
`command_center.storage` and with Streamlit's session state.
"""

from __future__ import annotations

import uuid
from datetime import datetime

# --------------------------------------------------------------------------
# Projects
# --------------------------------------------------------------------------

PROJECT_IDS: list[str] = ["AIOS", "AICOS", "BANK", "LEGAL", "BUSINESS", "PERSONAL"]
SENSITIVE_PROJECT_IDS: set[str] = {"BANK", "LEGAL"}

# --------------------------------------------------------------------------
# Workflow stages (parallel to, not a replacement for, the Kanban status)
# --------------------------------------------------------------------------

WORKFLOW_STAGES: list[str] = [
    "Draft",
    "Ready",
    "Running",
    "Remediation",
    "Final Review",
    "Approved",
    "Commit Pending",
    "Push Pending",
    "PR Pending",
    "Done",
]

WORKFLOW_STAGE_LABELS: dict[str, str] = {
    "Draft": "Черновик",
    "Ready": "Готово к запуску",
    "Running": "Выполняется",
    "Remediation": "Исправление",
    "Final Review": "Финальная проверка",
    "Approved": "Одобрено",
    "Commit Pending": "Ожидает commit",
    "Push Pending": "Ожидает push",
    "PR Pending": "Ожидает Pull Request",
    "Done": "Готово",
}

# --------------------------------------------------------------------------
# Runs
# --------------------------------------------------------------------------

RUN_STATUSES: list[str] = ["queued", "running", "completed", "failed", "timed_out", "cancelled"]

RUN_STATUS_LABELS: dict[str, str] = {
    "queued": "В очереди",
    "running": "Выполняется",
    "completed": "Завершено",
    "failed": "Ошибка",
    "timed_out": "Истекло время ожидания",
    "cancelled": "Отменено",
}

RUN_STATUS_COLORS: dict[str, str] = {
    "queued": "gray",
    "running": "blue",
    "completed": "green",
    "failed": "red",
    "timed_out": "orange",
    "cancelled": "gray",
}

# --------------------------------------------------------------------------
# Parsed report verdicts
# --------------------------------------------------------------------------

VERDICT_APPROVED_FOR_COMMIT = "APPROVED_FOR_COMMIT"
VERDICT_NOT_APPROVED_FOR_COMMIT = "NOT_APPROVED_FOR_COMMIT"
VERDICT_READY_FOR_FINAL_REVIEW = "READY_FOR_FINAL_REVIEW"
VERDICT_NOT_READY_FOR_FINAL_REVIEW = "NOT_READY_FOR_FINAL_REVIEW"
VERDICT_READY_FOR_COMMIT = "READY_FOR_COMMIT"
VERDICT_FAILED = "FAILED"

VERDICT_LABELS: dict[str, str] = {
    VERDICT_APPROVED_FOR_COMMIT: "Одобрено для commit",
    VERDICT_NOT_APPROVED_FOR_COMMIT: "Не одобрено для commit",
    VERDICT_READY_FOR_FINAL_REVIEW: "Готово к финальной проверке",
    VERDICT_NOT_READY_FOR_FINAL_REVIEW: "Не готово к финальной проверке",
    VERDICT_READY_FOR_COMMIT: "Готово к commit",
    VERDICT_FAILED: "Ошибка",
}

SEVERITIES: list[str] = ["Blocker", "High", "Medium", "Low"]


def iso_now() -> str:
    """Naive local time, second precision, no timezone offset — deliberately matches
    `app.py`'s pre-existing v1.1 `new_task_record` convention (`datetime.now().isoformat
    (timespec="seconds")`), so every timestamp in this app (`created_at`/`updated_at`
    on tasks, and every v1.2 run/chat/activity timestamp) is directly comparable
    without conversion. Not migrated to a timezone-aware format for v1.2, to avoid a
    mixed-format backward-compatibility hazard against existing `data/tasks.json`
    records — all timestamps in this app should be read as "local time on the machine
    that wrote them," never assumed to be UTC."""
    return datetime.now().isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex


# --------------------------------------------------------------------------
# Task workflow-field extension (backward compatible with v1.1 tasks.json)
# --------------------------------------------------------------------------


def default_task_workflow_fields() -> dict:
    return {
        "parent_task_id": None,
        "prior_run_id": None,
        "current_run_id": None,
        "workflow_stage": "Draft",
        "latest_verdict": None,
        "report_path": None,
        "repository_path": None,
        "branch": None,
        "agent": None,
        "last_run_at": None,
    }


def normalize_task_workflow(task: dict) -> dict:
    for key, default in default_task_workflow_fields().items():
        task.setdefault(key, default)
    return task


# --------------------------------------------------------------------------
# Project chat
# --------------------------------------------------------------------------


def new_conversation(project: str, title: str, task_id: str | None = None) -> dict:
    now = iso_now()
    return {
        "id": new_id(),
        "project": project,
        "title": title or "Новый разговор",
        "task_id": task_id,
        "created_at": now,
        "updated_at": now,
        "messages": [],
    }


def new_message(role: str, content: str, provider: str | None = None) -> dict:
    return {
        "id": new_id(),
        "role": role,
        "content": content,
        "provider": provider,
        "created_at": iso_now(),
    }


# --------------------------------------------------------------------------
# Runs
# --------------------------------------------------------------------------


def new_run_record(
    *,
    project: str,
    task_id: str | None,
    agent: str,
    task_type: str,
    repository_path: str,
    prompt: str,
    timeout_seconds: int,
) -> dict:
    now = iso_now()
    return {
        "id": new_id(),
        "project": project,
        "task_id": task_id,
        "agent": agent,
        "task_type": task_type,
        "repository_path": repository_path,
        "prompt": prompt,
        "timeout_seconds": timeout_seconds,
        "status": "queued",
        "pre_run": {"branch": None, "head": None, "status_summary": None, "is_git_repo": None},
        "post_run": {"branch": None, "head": None, "status_summary": None},
        "started_at": None,
        "completed_at": None,
        "duration_seconds": None,
        "exit_code": None,
        "stdout": "",
        "stderr": "",
        "report_path": None,
        "parsed": None,
        "next_task_id": None,
        "created_at": now,
        "updated_at": now,
    }


# --------------------------------------------------------------------------
# Activity log
# --------------------------------------------------------------------------

ACTIVITY_EVENT_TYPES: list[str] = [
    "conversation_created",
    "message_added",
    "task_created_from_message",
    "run_queued",
    "run_started",
    "run_completed",
    "run_failed",
    "report_saved",
    "verdict_extracted",
    "task_moved_to_remediation",
    "next_task_created",
    "manual_field_correction",
]


def new_activity_event(
    event_type: str,
    *,
    project: str | None = None,
    task_id: str | None = None,
    run_id: str | None = None,
    conversation_id: str | None = None,
    message: str = "",
) -> dict:
    return {
        "id": new_id(),
        "ts": iso_now(),
        "type": event_type,
        "project": project,
        "task_id": task_id,
        "run_id": run_id,
        "conversation_id": conversation_id,
        # Truncated defensively: activity events are a log, not a place to mirror
        # full report/document content (which may be sensitive for BANK/LEGAL).
        "message": (message or "")[:500],
    }
