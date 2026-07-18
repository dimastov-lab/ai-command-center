import ast
import inspect
import json

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
    task = tr.new_task_record("AIOS", "T", "implementation", "Backlog")
    tasks = [task]
    tr.update_task_status(tmp_path, tasks, task["id"], "Done")
    assert tasks[0]["status"] == "Done"
    assert tasks[0]["current_stage"] == "Merged"
    reloaded = tr.load_tasks(tmp_path)
    assert reloaded[0]["status"] == "Done"


def test_delete_task_removes_and_persists(tmp_path):
    task = tr.new_task_record("AIOS", "T", "implementation", "Backlog")
    tasks = [task]
    tr.delete_task(tmp_path, tasks, task["id"])
    assert tr.load_tasks(tmp_path) == []


def test_task_label_formats_project_title_status():
    task = {"project": "AIOS", "title": "Fix bug", "status": "In Progress"}
    assert tr.task_label(task) == "[AIOS] Fix bug · In Progress"


def test_set_manual_launch_status_updates_and_persists(tmp_path):
    task = tr.new_task_record("AIOS", "T", "implementation", "Backlog")
    tasks = [task]
    tr.set_manual_launch_status(tmp_path, tasks, task["id"], "Requires Attention", "paused manually")
    assert tasks[0]["launch_status"] == "Requires Attention"
    reloaded = tr.load_tasks(tmp_path)
    assert reloaded[0]["launch_status"] == "Requires Attention"
    assert reloaded[0]["timeline"][-1]["message"] == "paused manually"


def test_set_manual_launch_status_unknown_id_is_a_no_op_but_still_saves(tmp_path):
    task = tr.new_task_record("AIOS", "T", "implementation", "Backlog")
    tasks = [task]
    tr.set_manual_launch_status(tmp_path, tasks, "nonexistent", "Ready", "note")
    assert tasks[0]["launch_status"] == "Ready"  # unchanged, default
