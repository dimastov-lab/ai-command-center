"""AIOS Tasks API adapter — field/state mapping between AICC task dicts and AIOS SDK models.

Application-layer only: no engine imports, no sqlite3, no subprocess.
Imports only from aios_sdk (public SDK), not from aios.* (boundary gate: ADR-0008).
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from aios_sdk import AIOSClient, CreateTaskRequest, Task

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


def aicc_dict_to_create_request(task: dict[str, Any]) -> tuple[Any, str]:
    """Map an AICC task dict to a ``CreateTaskRequest`` and the target AIOS state.

    Returns (request, target_state) where target_state ∈ {"open", "in_progress", "completed"}.
    The caller is responsible for driving the task to that state via the SDK
    (task already starts as "open" after create; call .start()/.complete() as needed).
    """
    from aios_sdk import CreateTaskRequest

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


def aios_task_to_aicc_dict(task: Any) -> dict[str, Any]:
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


# ---------------------------------------------------------------------------
# ID mapping (AICC uuid hex ↔ AIOS task id)
# ---------------------------------------------------------------------------


class AIOSIdMap:
    """Thread-safe persistent mapping between AICC task ids (uuid hex) and AIOS task ids.

    Backed by a single JSON file (`data/aios_task_map.json`). All writes are
    atomic (write-to-temp + os.replace) to prevent corruption on crash.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._data: dict[str, str] = self._load()

    def _load(self) -> dict[str, str]:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save(self) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)

    def get(self, aicc_id: str) -> str | None:
        with self._lock:
            return self._data.get(aicc_id)

    def put(self, aicc_id: str, aios_id: str) -> None:
        with self._lock:
            self._data[aicc_id] = aios_id
            self._save()

    def remove(self, aicc_id: str) -> None:
        with self._lock:
            if aicc_id in self._data:
                del self._data[aicc_id]
                self._save()


# ---------------------------------------------------------------------------
# AIOS-backed repository
# ---------------------------------------------------------------------------

class AIOSTasksRepository:
    """Implements AICC task CRUD operations via the AIOS Tasks API SDK.

    All methods accept/return plain AICC task dicts (same shape as the JSON backend).
    State transitions drive AIOS lifecycle calls; the local ``AIOSIdMap`` bridges
    AICC uuid-hex ids to AIOS system ids.
    """

    def __init__(self, client: Any, id_map: AIOSIdMap) -> None:
        self._client = client
        self._id_map = id_map

    def load_all(self) -> list[dict[str, Any]]:
        return [aios_task_to_aicc_dict(t) for t in self._client.tasks.iterate()]

    def create(self, task_dict: dict[str, Any]) -> dict[str, Any]:
        req, target_state = aicc_dict_to_create_request(task_dict)
        result = self._client.tasks.create(req)
        aios_id = result.data.id
        aicc_id = task_dict.get("id", "")
        if aicc_id:
            self._id_map.put(aicc_id, aios_id)
        if target_state == "in_progress":
            result = self._client.tasks.start(aios_id)
        elif target_state == "completed":
            result = self._client.tasks.complete(aios_id)
        return aios_task_to_aicc_dict(result.data)

    def upsert(self, task_dict: dict[str, Any]) -> None:
        aicc_id = task_dict.get("id", "")
        aios_id = self._id_map.get(aicc_id) if aicc_id else None
        if aios_id:
            # Task exists in AIOS — sync status only (full update not in API v1)
            new_status = task_dict.get("status", "Backlog")
            self.update_status(aicc_id, new_status)
        else:
            self.create(task_dict)

    def update_status(self, task_id: str, new_status: str) -> dict[str, Any] | None:
        aios_id = self._id_map.get(task_id)
        if not aios_id:
            return None
        target_state = _STATUS_TO_AIOS_STATE.get(new_status, "open")
        if target_state == "in_progress":
            result = self._client.tasks.start(aios_id)
        elif target_state == "completed":
            result = self._client.tasks.complete(aios_id)
        elif target_state == "open":
            # No direct "reopen" in API v1 — treat as noop (state already open or assigned)
            result = self._client.tasks.get(aios_id)
        else:
            result = self._client.tasks.get(aios_id)
        return aios_task_to_aicc_dict(result.data)

    def delete(self, task_id: str) -> bool:
        aios_id = self._id_map.get(task_id)
        if not aios_id:
            return False
        self._client.tasks.cancel(aios_id)
        self._id_map.remove(task_id)
        return True
