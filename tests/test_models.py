from command_center import models


def test_normalize_task_workflow_backfills_missing_fields():
    """Backward-compatible migration: a v1.1 task record (no v1.2 workflow fields)
    must load without error and gain the new fields with their documented defaults."""
    v1_1_task = {
        "id": "abc",
        "project": "AIOS",
        "title": "Legacy task from v1.1",
        "task_type": "implementation",
        "status": "Backlog",
        "priority": "Medium",
        "owner": "",
        "estimate_hours": 0.0,
        "depends_on": [],
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }
    models.normalize_task_workflow(v1_1_task)

    for key, default in models.default_task_workflow_fields().items():
        assert v1_1_task[key] == default
    assert v1_1_task["title"] == "Legacy task from v1.1"
    assert v1_1_task["id"] == "abc"


def test_normalize_task_workflow_does_not_overwrite_existing_values():
    task = {"workflow_stage": "Approved", "latest_verdict": "APPROVED_FOR_COMMIT"}
    models.normalize_task_workflow(task)
    assert task["workflow_stage"] == "Approved"
    assert task["latest_verdict"] == "APPROVED_FOR_COMMIT"
    assert task["parent_task_id"] is None


def test_new_run_record_shape_and_defaults():
    run = models.new_run_record(
        project="AIOS",
        task_id="t1",
        agent="claude_code",
        task_type="review",
        repository_path="/tmp/x",
        prompt="do it",
        timeout_seconds=60,
    )
    assert run["status"] == "queued"
    assert run["id"]
    assert run["parsed"] is None
    assert run["pre_run"]["is_git_repo"] is None
    assert run["next_task_id"] is None


def test_new_conversation_and_message_shape():
    conversation = models.new_conversation("AIOS", "Test chat", task_id="t1")
    assert conversation["project"] == "AIOS"
    assert conversation["messages"] == []
    message = models.new_message("user", "hello")
    assert message["role"] == "user"
    assert message["content"] == "hello"


def test_new_activity_event_truncates_long_message():
    event = models.new_activity_event("message_added", message="x" * 5000)
    assert len(event["message"]) == 500


def test_sensitive_project_ids():
    assert models.SENSITIVE_PROJECT_IDS == {"BANK", "LEGAL"}
    assert "AIOS" not in models.SENSITIVE_PROJECT_IDS
