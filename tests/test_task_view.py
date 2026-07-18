from command_center import models, task_view


def test_cached_git_status_returns_not_repo_for_missing_path():
    assert task_view.cached_git_status(None, {}) == {"is_repo": False}


def test_cached_git_status_memoizes_per_path(tmp_path, monkeypatch):
    calls = []

    def fake_get_status(path):
        calls.append(path)
        return {"is_repo": True, "dirty": False}

    monkeypatch.setattr(task_view.git_info, "get_status", fake_get_status)
    cache: dict[str, dict] = {}
    task_view.cached_git_status(str(tmp_path), cache)
    task_view.cached_git_status(str(tmp_path), cache)
    assert len(calls) == 1


def test_cached_git_status_separate_paths_not_shared(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(task_view.git_info, "get_status", lambda path: calls.append(path) or {"is_repo": True})
    cache: dict[str, dict] = {}
    task_view.cached_git_status(str(tmp_path / "a"), cache)
    task_view.cached_git_status(str(tmp_path / "b"), cache)
    assert len(calls) == 2


def test_set_manual_launch_status_updates_task_and_timeline():
    task = {}
    models.normalize_task_execution(task)
    task_view.set_manual_launch_status(task, "Requires Attention", "paused manually")
    assert task["launch_status"] == "Requires Attention"
    assert task["timeline"][-1]["message"] == "paused manually"
    assert task["updated_at"]


def test_sorted_timeline_orders_newest_first():
    task = {
        "timeline": [
            {"ts": "2026-01-01T00:00:00", "type": "a"},
            {"ts": "2026-01-03T00:00:00", "type": "c"},
            {"ts": "2026-01-02T00:00:00", "type": "b"},
        ]
    }
    ordered = task_view.sorted_timeline(task)
    assert [event["type"] for event in ordered] == ["c", "b", "a"]


def test_sorted_timeline_handles_empty_task():
    assert task_view.sorted_timeline({}) == []


def test_dependency_graph_dot_returns_none_when_no_relations():
    task = {"id": "a"}
    assert task_view.dependency_graph_dot(task, {"a": task}) is None


def test_dependency_graph_dot_includes_dependency_edge():
    parent = {"id": "a", "title": "Parent task"}
    child = {"id": "b", "title": "Child task", "depends_on": ["a"]}
    dot = task_view.dependency_graph_dot(child, {"a": parent, "b": child})
    assert dot is not None
    assert '"a" -> "b"' in dot


def test_dependency_graph_dot_includes_parent_child_edge():
    parent = {"id": "p", "title": "Parent"}
    child = {"id": "c", "title": "Child", "parent_task_id": "p"}
    dot = task_view.dependency_graph_dot(parent, {"p": parent, "c": child})
    assert dot is not None
    assert '"p" -> "c"' in dot
