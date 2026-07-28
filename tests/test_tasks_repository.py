import ast
import inspect
import json

import pytest

from command_center import tasks_repository as tr


def test_tasks_repository_module_never_constructs_a_git_subprocess_call():
    source = inspect.getsource(tr)
    tree = ast.parse(source)
    string_literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "git" not in string_literals


def test_load_tasks_creates_empty_file_when_missing(tmp_path):
    tasks = tr.load_tasks(tmp_path)
    assert tasks == []
    assert tr.tasks_file_path(tmp_path).exists()


def test_load_tasks_seeds_from_example_file(tmp_path):
    example = tmp_path / "tasks.example.json"
    example.write_text(json.dumps([{"id": "x", "title": "Seed", "project": "AIOS", "status": "Backlog"}]))
    tasks = tr.load_tasks(tmp_path, example_file=example)
    assert len(tasks) == 1
    assert tasks[0]["title"] == "Seed"
    assert tasks[0]["progress"] == 0  # backfilled by normalize_task on load


def test_load_tasks_returns_empty_list_for_malformed_json(tmp_path):
    tasks_file = tr.tasks_file_path(tmp_path)
    tasks_file.parent.mkdir(parents=True, exist_ok=True)
    tasks_file.write_text("not json")
    assert tr.load_tasks(tmp_path) == []


def test_load_tasks_returns_empty_list_when_top_level_is_not_a_list(tmp_path):
    tasks_file = tr.tasks_file_path(tmp_path)
    tasks_file.parent.mkdir(parents=True, exist_ok=True)
    tasks_file.write_text(json.dumps({"not": "a list"}))
    assert tr.load_tasks(tmp_path) == []


def test_save_and_reload_roundtrip(tmp_path):
    task = tr.new_task_record("AIOS", "Title", "implementation", "Backlog", goal="Goal text")
    tr.save_tasks(tmp_path, [task])
    reloaded = tr.load_tasks(tmp_path)
    assert reloaded[0]["id"] == task["id"]
    assert reloaded[0]["goal"] == "Goal text"


def test_new_task_record_defaults_goal_to_title_when_omitted():
    task = tr.new_task_record("AIOS", "Just a title", "implementation", "Backlog")
    assert task["goal"] == "Just a title"


def test_new_task_record_seeds_task_created_timeline_event():
    task = tr.new_task_record("AIOS", "T", "implementation", "Backlog")
    assert task["timeline"][0]["type"] == "task_created"


def test_update_task_status_to_done_sets_merged_stage_and_persists(tmp_path):
    task = tr.create_task(tmp_path, "AIOS", "T", "implementation", "Backlog")
    updated = tr.update_task_status(tmp_path, task["id"], "Done")
    assert updated["status"] == "Done"
    assert updated["current_stage"] == "Merged"
    reloaded = tr.load_tasks(tmp_path)
    assert reloaded[0]["status"] == "Done"


def test_update_task_status_unknown_id_returns_none_and_is_a_no_op(tmp_path):
    tr.create_task(tmp_path, "AIOS", "T", "implementation", "Backlog")
    result = tr.update_task_status(tmp_path, "nonexistent", "Done")
    assert result is None
    reloaded = tr.load_tasks(tmp_path)
    assert reloaded[0]["status"] == "Backlog"


def test_delete_task_removes_and_persists(tmp_path):
    task = tr.create_task(tmp_path, "AIOS", "T", "implementation", "Backlog")
    tr.delete_task(tmp_path, task["id"])
    assert tr.load_tasks(tmp_path) == []


def test_task_label_formats_project_title_status():
    task = {"project": "AIOS", "title": "Fix bug", "status": "In Progress"}
    assert tr.task_label(task) == "[AIOS] Fix bug · In Progress"


def test_set_manual_launch_status_updates_and_persists(tmp_path):
    task = tr.create_task(tmp_path, "AIOS", "T", "implementation", "Backlog")
    updated = tr.set_manual_launch_status(tmp_path, task["id"], "Requires Attention", "paused manually")
    assert updated["launch_status"] == "Requires Attention"
    reloaded = tr.load_tasks(tmp_path)
    assert reloaded[0]["launch_status"] == "Requires Attention"
    assert reloaded[0]["timeline"][-1]["message"] == "paused manually"


def test_set_manual_launch_status_unknown_id_is_a_no_op_and_returns_none(tmp_path):
    tr.create_task(tmp_path, "AIOS", "T", "implementation", "Backlog")
    result = tr.set_manual_launch_status(tmp_path, "nonexistent", "Ready", "note")
    assert result is None
    reloaded = tr.load_tasks(tmp_path)
    assert reloaded[0]["launch_status"] == "Ready"  # unchanged, default


# --------------------------------------------------------------------------
# Task inheritance: workspace_path / branch / executor / prompt, resolved by
# the caller (the Create Task UI, via `project_config.task_defaults_from_project`)
# and handed to `new_task_record` as the final, already-resolved values.
# --------------------------------------------------------------------------


def test_new_task_record_applies_given_workspace_branch_executor_prompt():
    task = tr.new_task_record(
        "AIOS", "T", "implementation", "Backlog",
        workspace_path="/ws/aios", branch="develop", executor="claude_code", prompt="Do the thing.",
    )
    assert task["workspace_path"] == "/ws/aios"
    assert task["branch"] == "develop"
    assert task["executor"] == "claude_code"
    assert task["prompt"] == "Do the thing."


def test_new_task_record_executor_also_sets_legacy_agent_field():
    """`agent` is the pre-v2 field name for the same concept; keeping both in
    sync at creation time avoids the same drift the retired v1 synchronous
    launch flow guarded against after a run completes."""
    task = tr.new_task_record("AIOS", "T", "implementation", "Backlog", executor="claude_code")
    assert task["agent"] == "claude_code"
    assert task["executor"] == "claude_code"


def test_new_task_record_without_inheritance_kwargs_leaves_pre_existing_defaults():
    """Backward compatibility: every pre-existing call site that doesn't pass
    the new kwargs at all must keep getting exactly the same record shape as
    before — `workspace_path`/`branch`/`executor` unset, `prompt` empty."""
    task = tr.new_task_record("AIOS", "T", "implementation", "Backlog")
    assert task["workspace_path"] is None
    assert task["branch"] is None
    assert task["executor"] is None
    assert task["prompt"] == ""


def test_new_task_record_inherited_workspace_survives_save_and_reload(tmp_path):
    task = tr.new_task_record(
        "AIOS", "T", "implementation", "Backlog", workspace_path="/ws/aios", branch="develop",
    )
    tr.save_tasks(tmp_path, [task])
    reloaded = tr.load_tasks(tmp_path)
    assert reloaded[0]["workspace_path"] == "/ws/aios"
    assert reloaded[0]["branch"] == "develop"


# --------------------------------------------------------------------------
# The shared transactional layer: `tasks_lock`/`mutate_tasks` and every
# verb-named helper built on it (`create_task`/`upsert_task`/`upsert_tasks`/
# `update_task_status`/`set_manual_launch_status`/`delete_task`). See this
# module's docstring for why a hand-rolled `load_tasks()` ... mutate ...
# `save_tasks()` sequence (the pre-existing shape of every one of these
# before the Founder Gate lost-update remediation) is unsafe.
# --------------------------------------------------------------------------


def test_create_task_appends_and_persists(tmp_path):
    task = tr.create_task(tmp_path, "AIOS", "New task", "implementation", "Backlog", goal="Do it")
    assert task["project"] == "AIOS"
    assert task["goal"] == "Do it"
    reloaded = tr.load_tasks(tmp_path)
    assert len(reloaded) == 1
    assert reloaded[0]["id"] == task["id"]


def test_create_task_twice_produces_two_distinct_tasks(tmp_path):
    first = tr.create_task(tmp_path, "AIOS", "First", "implementation", "Backlog")
    second = tr.create_task(tmp_path, "AIOS", "Second", "implementation", "Backlog")
    assert first["id"] != second["id"]
    reloaded = tr.load_tasks(tmp_path)
    assert {t["id"] for t in reloaded} == {first["id"], second["id"]}


def test_upsert_task_replaces_existing_entry_by_id_only(tmp_path):
    task_a = tr.create_task(tmp_path, "AIOS", "A", "implementation", "Backlog")
    task_b = tr.create_task(tmp_path, "AIOS", "B", "implementation", "Backlog")

    task_a["status"] = "Done"
    tr.upsert_task(tmp_path, task_a)

    reloaded = {t["id"]: t for t in tr.load_tasks(tmp_path)}
    assert reloaded[task_a["id"]]["status"] == "Done"
    assert reloaded[task_b["id"]]["status"] == "Backlog"  # untouched


def test_upsert_task_appends_when_id_absent(tmp_path):
    task = tr.new_task_record("AIOS", "Not yet saved", "implementation", "Backlog")
    tr.upsert_task(tmp_path, task)
    reloaded = tr.load_tasks(tmp_path)
    assert len(reloaded) == 1
    assert reloaded[0]["id"] == task["id"]


def test_upsert_tasks_bulk_commits_only_the_given_subset(tmp_path):
    task_a = tr.create_task(tmp_path, "AIOS", "A", "implementation", "Backlog")
    task_b = tr.create_task(tmp_path, "AIOS", "B", "implementation", "Backlog")
    task_c = tr.create_task(tmp_path, "AIOS", "C", "implementation", "Backlog")

    task_a["status"] = "Done"
    task_b["status"] = "In Progress"
    tr.upsert_tasks(tmp_path, [task_a, task_b])

    reloaded = {t["id"]: t for t in tr.load_tasks(tmp_path)}
    assert reloaded[task_a["id"]]["status"] == "Done"
    assert reloaded[task_b["id"]]["status"] == "In Progress"
    assert reloaded[task_c["id"]]["status"] == "Backlog"  # untouched


def test_upsert_tasks_is_safe_to_call_with_an_unchanged_full_list(tmp_path):
    """`upsert_tasks` is the callback handed to UI panels that pass their
    entire `tasks` list regardless of how many actually changed — must not
    duplicate anything."""
    task = tr.create_task(tmp_path, "AIOS", "A", "implementation", "Backlog")
    all_tasks = tr.load_tasks(tmp_path)
    tr.upsert_tasks(tmp_path, all_tasks)
    reloaded = tr.load_tasks(tmp_path)
    assert len(reloaded) == 1
    assert reloaded[0]["id"] == task["id"]


def test_mutate_tasks_persists_whatever_the_mutator_leaves_the_list_as(tmp_path):
    def _mutator(tasks: list[dict]) -> str:
        tasks.append(tr.new_task_record("AIOS", "Via mutate_tasks", "implementation", "Backlog"))
        return "done"

    result = tr.mutate_tasks(tmp_path, _mutator)
    assert result == "done"
    reloaded = tr.load_tasks(tmp_path)
    assert len(reloaded) == 1


def test_mutate_tasks_persist_if_false_skips_the_write(tmp_path):
    def _mutator(tasks: list[dict]) -> bool:
        tasks.append(tr.new_task_record("AIOS", "Should not persist", "implementation", "Backlog"))
        return False

    tr.mutate_tasks(tmp_path, _mutator, persist_if=lambda changed: changed)
    assert tr.load_tasks(tmp_path) == []


def test_mutate_tasks_does_not_save_when_mutator_raises(tmp_path):
    def _mutator(tasks: list[dict]) -> None:
        tasks.append(tr.new_task_record("AIOS", "Should not persist", "implementation", "Backlog"))
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        tr.mutate_tasks(tmp_path, _mutator)
    assert tr.load_tasks(tmp_path) == []


def test_tasks_lock_is_released_after_an_exception(tmp_path):
    with pytest.raises(RuntimeError):
        with tr.tasks_lock(tmp_path, timeout=2.0):
            raise RuntimeError("boom")

    # Must be immediately re-acquirable — a short timeout would fail if the
    # exception above had left the lock held.
    with tr.tasks_lock(tmp_path, timeout=2.0):
        pass
