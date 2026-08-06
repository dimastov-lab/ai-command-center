from __future__ import annotations
from unittest.mock import MagicMock
import pytest
from command_center.application.aios_tasks import (
    aicc_dict_to_create_request,
    aios_task_to_aicc_dict,
)
from aios_sdk import CreateTaskRequest, Task

def _make_aicc_task(**overrides) -> dict:
    base = {
        "id": "abc123",
        "project": "AICC",
        "title": "Implement auth",
        "task_type": "implementation",
        "status": "Backlog",
        "priority": "Medium",
        "owner": "alice",
        "estimate_hours": 4.0,
        "depends_on": [],
        "goal": "Add auth",
        "notes": "notes here",
        "workflow_stage": "Draft",
        "created_at": "2026-08-06T10:00:00Z",
        "updated_at": "2026-08-06T10:00:00Z",
    }
    base.update(overrides)
    return base

def _make_aios_task(**overrides) -> Task:
    from datetime import datetime, timezone
    from unittest.mock import MagicMock
    t = MagicMock(spec=Task)
    t.id = "aios-task-1"
    t.title = "Implement auth"
    t.type = "implementation"
    t.subject_ref = "AICC/abc123"
    t.state = "open"
    t.priority = 2
    t.assignee = None
    t.escalated = False
    t.escalation_target = None
    t.due_at = None
    t.created_by = "alice"
    t.created_at = datetime(2026, 8, 6, 10, 0, tzinfo=timezone.utc)
    t.updated_at = datetime(2026, 8, 6, 10, 0, tzinfo=timezone.utc)
    t.payload = {
        "kanban_status": "Backlog",
        "project": "AICC",
        "aicc_id": "abc123",
        "task_type": "implementation",
        "workflow_stage": "Draft",
        "owner": "alice",
        "estimate_hours": 4.0,
        "depends_on": [],
        "goal": "Add auth",
        "notes": "notes here",
    }
    for k, v in overrides.items():
        setattr(t, k, v)
    return t

# --- aicc_dict_to_create_request ---

def test_backlog_maps_to_open_state():
    req, target = aicc_dict_to_create_request(_make_aicc_task(status="Backlog"))
    assert target == "open"

def test_in_progress_maps_to_in_progress_state():
    req, target = aicc_dict_to_create_request(_make_aicc_task(status="In Progress"))
    assert target == "in_progress"

def test_done_maps_to_completed_state():
    req, target = aicc_dict_to_create_request(_make_aicc_task(status="Done"))
    assert target == "completed"

def test_review_maps_to_in_progress_state():
    req, target = aicc_dict_to_create_request(_make_aicc_task(status="Review"))
    assert target == "in_progress"

def test_next_maps_to_open_state():
    req, target = aicc_dict_to_create_request(_make_aicc_task(status="Next"))
    assert target == "open"

def test_subject_ref_uses_project_and_id():
    req, _ = aicc_dict_to_create_request(_make_aicc_task(project="AML", id="xyz789"))
    assert req.subject_ref == "AML/xyz789"

def test_type_uses_task_type():
    req, _ = aicc_dict_to_create_request(_make_aicc_task(task_type="review"))
    assert req.type == "review"

def test_type_defaults_to_task_when_missing():
    task = _make_aicc_task()
    del task["task_type"]
    req, _ = aicc_dict_to_create_request(task)
    assert req.type == "task"

def test_medium_priority_maps_to_2():
    req, _ = aicc_dict_to_create_request(_make_aicc_task(priority="Medium"))
    assert req.priority == 2

def test_critical_priority_maps_to_4():
    req, _ = aicc_dict_to_create_request(_make_aicc_task(priority="Critical"))
    assert req.priority == 4

def test_low_priority_maps_to_1():
    req, _ = aicc_dict_to_create_request(_make_aicc_task(priority="Low"))
    assert req.priority == 1

def test_high_priority_maps_to_3():
    req, _ = aicc_dict_to_create_request(_make_aicc_task(priority="High"))
    assert req.priority == 3

def test_unknown_priority_defaults_to_2():
    req, _ = aicc_dict_to_create_request(_make_aicc_task(priority="P0"))
    assert req.priority == 2

def test_aicc_id_stored_in_payload():
    req, _ = aicc_dict_to_create_request(_make_aicc_task(id="abc123"))
    assert req.payload["aicc_id"] == "abc123"

def test_kanban_status_stored_in_payload():
    req, _ = aicc_dict_to_create_request(_make_aicc_task(status="Review"))
    assert req.payload["kanban_status"] == "Review"

def test_created_by_uses_owner():
    req, _ = aicc_dict_to_create_request(_make_aicc_task(owner="bob"))
    assert req.payload["owner"] == "bob"

# --- aios_task_to_aicc_dict ---

def test_round_trip_preserves_kanban_status():
    aicc = _make_aicc_task(status="Review")
    req, _ = aicc_dict_to_create_request(aicc)
    # Build a fake AIOS task as the server would return it
    aios = _make_aios_task(payload=req.payload | {"kanban_status": "Review"})
    result = aios_task_to_aicc_dict(aios)
    assert result["status"] == "Review"

def test_round_trip_preserves_project():
    aios = _make_aios_task(payload={"kanban_status": "Backlog", "project": "AICC", "aicc_id": "abc123"})
    result = aios_task_to_aicc_dict(aios)
    assert result["project"] == "AICC"

def test_round_trip_preserves_aicc_id():
    aios = _make_aios_task(payload={"kanban_status": "Backlog", "aicc_id": "abc123"})
    result = aios_task_to_aicc_dict(aios)
    assert result["id"] == "abc123"

def test_aios_id_stored_in_aios_id_field():
    aios = _make_aios_task(id="aios-task-99", payload={"kanban_status": "Backlog", "aicc_id": "abc123"})
    result = aios_task_to_aicc_dict(aios)
    assert result["aios_id"] == "aios-task-99"

def test_priority_2_maps_to_medium():
    aios = _make_aios_task(priority=2, payload={"kanban_status": "Backlog", "aicc_id": "abc123"})
    result = aios_task_to_aicc_dict(aios)
    assert result["priority"] == "Medium"

def test_priority_4_maps_to_critical():
    aios = _make_aios_task(priority=4, payload={"kanban_status": "Backlog", "aicc_id": "abc123"})
    result = aios_task_to_aicc_dict(aios)
    assert result["priority"] == "Critical"

def test_title_preserved():
    aios = _make_aios_task(title="My task", payload={"kanban_status": "Backlog", "aicc_id": "abc123"})
    result = aios_task_to_aicc_dict(aios)
    assert result["title"] == "My task"
