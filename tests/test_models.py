import pytest

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


# --------------------------------------------------------------------------
# Task execution model v2 (backward compatibility)
# --------------------------------------------------------------------------


def test_normalize_task_execution_backfills_v1_1_shaped_task():
    """A pre-v2 record has no `goal`/`prompt`/`progress`/etc. — it must load,
    keep every existing field untouched, and get sane v2 defaults, with its
    old `title` text (the objective) copied into the new `goal` field."""
    legacy_title = (
        "Investigate why the nightly sync job silently drops rows on retry when the "
        "upstream API returns a partial page and the cursor is not persisted correctly"
    )
    v1_1_task = {
        "id": "legacy-1",
        "project": "AIOS",
        "title": legacy_title,
        "task_type": "implementation",
        "status": "Backlog",
        "priority": "Medium",
        "depends_on": [],
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }
    models.normalize_task_execution(v1_1_task)

    assert v1_1_task["goal"] == legacy_title
    assert v1_1_task["title"] != legacy_title  # derived short title, not the raw objective
    assert v1_1_task["title"].startswith("Investigate why the nightly sync job")
    assert v1_1_task["id"] == "legacy-1"  # untouched
    assert v1_1_task["status"] == "Backlog"  # untouched
    assert v1_1_task["progress"] == 0
    assert v1_1_task["progress_mode"] == "auto"
    assert v1_1_task["current_stage"] == models.EXECUTION_STAGES[0]
    assert v1_1_task["launch_status"] == "Ready"
    assert v1_1_task["timeline"] == []
    assert v1_1_task["launch_history"] == []
    assert v1_1_task["prompt_history"] == []


def test_normalize_task_execution_does_not_split_title_when_goal_already_present():
    """A v2-created task already has an independent title/goal — normalizing
    it again (e.g. on every `load_tasks()` call) must not mutate either."""
    task = {"id": "t2", "title": "Fix sort bug", "goal": "Kanban cards sort by the wrong field"}
    models.normalize_task_execution(task)
    assert task["title"] == "Fix sort bug"
    assert task["goal"] == "Kanban cards sort by the wrong field"


def test_normalize_task_execution_derives_workspace_path_and_executor():
    task = {"id": "t3", "title": "X", "goal": "Y", "repository_path": "/repos/aios", "agent": "claude_code"}
    models.normalize_task_execution(task)
    assert task["workspace_path"] == "/repos/aios"
    assert task["executor"] == "claude_code"


def test_derive_short_title_truncates_on_word_boundary():
    long_text = "word " * 40
    short = models.derive_short_title(long_text.strip())
    assert len(short) <= 81
    assert short.endswith("…")


def test_derive_short_title_keeps_short_text_unchanged():
    assert models.derive_short_title("Fix sort bug") == "Fix sort bug"


def test_set_current_stage_updates_progress():
    task = {"progress_mode": "auto"}
    models.set_current_stage(task, "Implementation")
    assert task["current_stage"] == "Implementation"
    assert task["progress"] == 40
    assert task["progress_mode"] == "auto"


def test_set_current_stage_manual_override_blocks_auto_advance():
    task = {}
    models.set_current_stage(task, "Coding Complete", mode="manual")
    models.set_current_stage(task, "Implementation", mode="auto")  # should be a no-op
    assert task["current_stage"] == "Coding Complete"
    assert task["progress"] == 60


def test_reset_progress_to_auto_recomputes_from_stage():
    task = {}
    models.set_current_stage(task, "Tests Passed", mode="manual")
    task["progress"] = 12  # simulate a stale/manual value
    models.reset_progress_to_auto(task)
    assert task["progress_mode"] == "auto"
    assert task["progress"] == 90


def test_set_current_stage_rejects_unknown_stage():
    with pytest.raises(ValueError):
        models.set_current_stage({}, "Not A Real Stage")


def test_append_timeline_event_appends_structured_entry():
    task = {}
    models.append_timeline_event(task, "task_created", "Задача создана")
    assert len(task["timeline"]) == 1
    assert task["timeline"][0]["type"] == "task_created"
    assert task["timeline"][0]["message"] == "Задача создана"
    assert task["timeline"][0]["id"]
    assert task["timeline"][0]["ts"]


def test_append_launch_attempt_updates_history_and_status():
    task = {}
    models.append_launch_attempt(task, executor="claude_code", branch="main", status="Launching", warnings=["dirty"])
    assert task["launch_status"] == "Launching"
    assert len(task["launch_history"]) == 1
    assert task["launch_history"][0]["executor"] == "claude_code"
    assert task["launch_history"][0]["warnings"] == ["dirty"]


def test_push_prompt_history_records_previous_value_and_bumps_version():
    task = {"prompt": "first draft"}
    models.push_prompt_history(task, "second draft")
    assert task["prompt"] == "second draft"
    assert task["prompt_version"] == 1
    assert task["prompt_history"][0]["text"] == "first draft"


def test_push_prompt_history_skips_history_entry_when_no_previous_prompt():
    task = {}
    models.push_prompt_history(task, "first draft")
    assert task["prompt"] == "first draft"
    assert task["prompt_history"] == []
    assert task.get("prompt_version", 0) == 0


def test_push_prompt_history_skips_duplicate_entry_when_prompt_unchanged():
    """Relaunching a task with an unedited prompt (the default case — the
    launcher pre-fills the previous prompt) must not create a duplicate
    history entry or bump the version each time."""
    task = {"prompt": "same text"}
    models.push_prompt_history(task, "same text")
    models.push_prompt_history(task, "same text")
    models.push_prompt_history(task, "same text")
    assert task["prompt"] == "same text"
    assert task["prompt_history"] == []
    assert task.get("prompt_version", 0) == 0


def test_push_prompt_history_records_only_genuine_changes():
    task = {"prompt": "v1"}
    models.push_prompt_history(task, "v1")  # no-op, not recorded
    models.push_prompt_history(task, "v2")  # genuine change, recorded
    models.push_prompt_history(task, "v2")  # no-op again
    models.push_prompt_history(task, "v3")  # genuine change, recorded
    assert task["prompt"] == "v3"
    assert task["prompt_version"] == 2
    assert [entry["text"] for entry in task["prompt_history"]] == ["v1", "v2"]


def test_is_passing_verdict():
    assert models.is_passing_verdict(models.VERDICT_APPROVED_FOR_COMMIT) is True
    assert models.is_passing_verdict(models.VERDICT_READY_FOR_COMMIT) is True
    assert models.is_passing_verdict(models.VERDICT_READY_FOR_FINAL_REVIEW) is True
    assert models.is_passing_verdict(models.VERDICT_NOT_APPROVED_FOR_COMMIT) is False
    assert models.is_passing_verdict(models.VERDICT_FAILED) is False
    assert models.is_passing_verdict(None) is False


def test_derive_dependency_edges_computes_reverse_edges_without_persisting():
    parent = {"id": "p"}
    child = {"id": "c", "parent_task_id": "p"}
    dependent = {"id": "d", "depends_on": ["p"]}
    all_tasks = [parent, child, dependent]

    edges = models.derive_dependency_edges(parent, all_tasks)
    assert edges["blocks"] == ["d"]
    assert edges["children"] == ["c"]
    assert "blocks" not in parent  # never persisted on the task dict
    assert "children" not in parent


def test_unmet_dependencies_and_is_blocked():
    tasks_by_id = {
        "done": {"id": "done", "status": "Done"},
        "open": {"id": "open", "status": "Backlog"},
    }
    blocked_task = {"depends_on": ["open"]}
    unblocked_task = {"depends_on": ["done"]}

    assert models.unmet_dependencies(blocked_task, tasks_by_id) == ["open"]
    assert models.is_blocked(blocked_task, tasks_by_id) is True
    assert models.unmet_dependencies(unblocked_task, tasks_by_id) == []
    assert models.is_blocked(unblocked_task, tasks_by_id) is False


def test_stage_progress_table_matches_mission_spec():
    assert models.STAGE_PROGRESS == {
        "Created": 0,
        "Workspace Verified": 5,
        "Architecture Review": 20,
        "Implementation": 40,
        "Coding Complete": 60,
        "Validation": 75,
        "Tests Passed": 90,
        "PR Ready": 95,
        "Completed Locally": 100,
        "Merged": 100,
    }
    assert models.EXECUTION_STAGES == list(models.STAGE_PROGRESS.keys())
