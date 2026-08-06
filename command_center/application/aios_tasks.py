"""AIOS Tasks API adapter — field/state mapping between AICC task dicts and AIOS SDK models.

Application-layer only: no engine imports, no sqlite3, no subprocess.
Imports only from aios_sdk (public SDK), not from aios.* (boundary gate: ADR-0008).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from aios_sdk import CreateTaskRequest, Task

# ---------------------------------------------------------------------------
# Priority mapping
# ---------------------------------------------------------------------------

_AICC_PRIORITY_TO_INT: dict[str, int] = {
    "Low": 1,
    "Medium": 2,
    "High": 3,
    "Critical": 4,
}

_INT_TO_AICC_PRIORITY: dict[int, str] = {v: k for k, v in _AICC_PRIORITY_TO_INT.items()}

# AICC Kanban status → AIOS lifecycle target state after creation
_STATUS_TO_AIOS_STATE: dict[str, str] = {
    "Backlog": "open",
    "Next": "open",
    "In Progress": "in_progress",
    "Review": "in_progress",
    "Done": "completed",
}

_AICC_PAYLOAD_FIELDS: tuple[str, ...] = (
    "project",
    "task_type",
    "workflow_stage",
    "owner",
    "estimate_hours",
    "depends_on",
    "goal",
    "notes",
    "timeline",
    "parent_task_id",
    "prior_run_id",
    "launch_status",
    "current_run_id",
    "report_path",
    "repository_path",
    "branch",
    "last_run_at",
    "latest_verdict",
    "pull_request_url",
    "workspace_path",
    "executor",
    "agent",
    "prompt",
    "untrusted_import",
    "current_stage",
    "progress",
)


def aicc_dict_to_create_request(task: dict[str, Any]) -> tuple[CreateTaskRequest, str]:
    """Map an AICC task dict to a ``CreateTaskRequest`` and the target AIOS state.

    Returns (request, target_state) where target_state ∈ {"open", "in_progress", "completed"}.
    The caller is responsible for driving the task to that state via the SDK
    (task already starts as "open" after create; call .start()/.complete() as needed).
    """
    status = task.get("status", "Backlog")
    target_state = _STATUS_TO_AIOS_STATE.get(status, "open")

    project = task.get("project", "")
    task_id = task.get("id", "")
    subject_ref = f"{project}/{task_id}"[:255] if project and task_id else task_id[:255] or "unknown"

    task_type = task.get("task_type") or "task"
    priority = _AICC_PRIORITY_TO_INT.get(task.get("priority", "Medium"), 2)

    payload: dict[str, Any] = {
        "aicc_id": task_id,
        "kanban_status": status,
    }
    for field in _AICC_PAYLOAD_FIELDS:
        value = task.get(field)
        if value is not None:
            payload[field] = value

    return (
        CreateTaskRequest(
            subject_ref=subject_ref,
            type=task_type,
            title=task.get("title", "Untitled")[:512],
            priority=priority,
            payload=payload,
        ),
        target_state,
    )


def aios_task_to_aicc_dict(task: Task) -> dict[str, Any]:
    """Reconstruct a full AICC task dict from an AIOS Task object.

    The AIOS ``payload`` carries the original AICC fields.
    The AICC ``id`` is restored from ``payload["aicc_id"]`` (the original uuid hex).
    ``aios_id`` is set to the AIOS-system task id for internal mapping use.
    """
    payload = task.payload or {}
    aicc_id = payload.get("aicc_id") or task.id

    now_iso = datetime.now(timezone.utc).isoformat()
    created_at = task.created_at.isoformat() if isinstance(task.created_at, datetime) else now_iso
    updated_at = task.updated_at.isoformat() if isinstance(task.updated_at, datetime) else now_iso

    result: dict[str, Any] = {
        "id": aicc_id,
        "aios_id": task.id,
        "title": task.title,
        "status": payload.get("kanban_status", "Backlog"),
        "priority": _INT_TO_AICC_PRIORITY.get(task.priority, "Medium"),
        "created_at": created_at,
        "updated_at": updated_at,
    }
    # Restore all AICC-specific fields from payload
    for field in _AICC_PAYLOAD_FIELDS:
        if field in payload:
            result[field] = payload[field]
    # Ensure required AICC fields have defaults
    result.setdefault("project", "")
    result.setdefault("task_type", task.type if task.type != "task" else "")
    result.setdefault("depends_on", [])
    result.setdefault("owner", "")
    result.setdefault("estimate_hours", 0.0)
    result.setdefault("goal", result["title"])
    result.setdefault("notes", "")
    result.setdefault("workflow_stage", "Draft")
    result.setdefault("timeline", [])
    return result
